"""The self-signed certificate (app/tls.py)."""

from __future__ import annotations

import datetime as dt
import ssl
import stat
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from app import tls


def _load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_a_certificate_is_made_for_this_computers_names_and_addresses(tmp_path: Path) -> None:
    files = tls.ensure_certificate(tmp_path, extra_names=["pacs-pc.hospital.lan", "10.20.30.40"])
    cert = _load(files.cert)
    names, addresses = tls._san(cert)
    assert {"localhost", "pacs-pc.hospital.lan"} <= names and {"127.0.0.1", "10.20.30.40"} <= addresses
    assert cert.issuer == cert.subject  # self-signed
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days >= 700
    constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert constraints.ca is False  # it cannot be used to sign other certificates
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value


def test_the_private_key_is_readable_by_the_owner_only(tmp_path: Path) -> None:
    files = tls.ensure_certificate(tmp_path)
    if sys.platform == "win32":
        pytest.skip("permissions on Windows come from the folder")
    assert stat.S_IMODE(files.key.stat().st_mode) == 0o600


def test_an_existing_certificate_is_reused_until_something_changes(tmp_path: Path) -> None:
    first = tls.ensure_certificate(tmp_path)
    before = first.cert.read_bytes()
    assert tls.ensure_certificate(tmp_path).cert.read_bytes() == before
    tls.ensure_certificate(tmp_path, extra_names=["a-new-name.lan"])  # a new name needs a new certificate
    assert first.cert.read_bytes() != before and "a-new-name.lan" in tls._san(_load(first.cert))[0]


def test_an_expiring_or_mismatched_certificate_is_replaced(tmp_path: Path, monkeypatch) -> None:
    files = tls.ensure_certificate(tmp_path)
    old = files.cert.read_bytes()
    monkeypatch.setattr(tls, "RENEW_BEFORE_DAYS", 10_000)  # as if it were nearly out of date
    assert tls.ensure_certificate(tmp_path).cert.read_bytes() != old
    monkeypatch.setattr(tls, "RENEW_BEFORE_DAYS", 30)
    files.key.write_bytes(tls.ensure_certificate(tmp_path).key.read_bytes())
    other = tmp_path / "other"
    tls.ensure_certificate(other)
    files.key.write_bytes((other / "tls" / "key.pem").read_bytes())  # a key that does not belong to the certificate
    kept = files.cert.read_bytes()
    assert tls.ensure_certificate(tmp_path).cert.read_bytes() != kept
    files.cert.write_text("garbage")
    assert tls.ensure_certificate(tmp_path).cert.read_bytes().startswith(b"-----BEGIN CERTIFICATE")


def test_describe_gives_the_fingerprint_a_person_can_compare(tmp_path: Path) -> None:
    files = tls.ensure_certificate(tmp_path)
    info = tls.describe(files.cert)
    assert info["enabled"] and info["not_after"] > dt.date.today().isoformat() and "localhost" in info["names"]
    assert info["fingerprint"].replace(":", "").lower() == _load(files.cert).fingerprint(hashes.SHA256()).hex()
    assert len(info["fingerprint"].split(":")) == 32


def test_the_key_and_certificate_load_into_a_tls_context(tmp_path: Path) -> None:
    files = tls.ensure_certificate(tmp_path)
    ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(str(files.cert), str(files.key))


def test_a_real_https_server_answers_and_a_client_that_trusts_the_certificate_connects(tmp_path: Path) -> None:
    """The proof the pieces fit: uvicorn serving with the generated files, and a verified client."""
    from fastapi import FastAPI

    app = FastAPI()
    app.get("/ping")(lambda: {"ok": True})
    files = tls.ensure_certificate(tmp_path)
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, ssl_certfile=str(files.cert), ssl_keyfile=str(files.key), log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        response = httpx.get(f"https://localhost:{port}/ping", verify=ssl.create_default_context(cafile=str(files.cert)))
        assert response.json() == {"ok": True}
        with pytest.raises(httpx.ConnectError):  # without trusting it, the connection is refused
            httpx.get(f"https://localhost:{port}/ping")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
