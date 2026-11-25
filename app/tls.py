"""HTTPS for the app, with a certificate it makes itself.

A hospital without its own certificate authority cannot get a trusted certificate
for an internal computer name, so the app creates a self-signed one: a new key
pair and a certificate valid for this computer's names and addresses. Browsers
warn about it the first time. The Security page shows its fingerprint, to compare
with what the browser shows, and offers the certificate for download, so it can be
installed as trusted on the computers that use the app, which ends the warning.

The key stays in ``tls/`` in the data folder (owner-only on systems that support
it; on Windows the folder's own permissions apply, which the Windows installer
limits to administrators, SYSTEM and the service account). The certificate is made
again when it nears its end or when this computer's names or addresses change.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.netutil import own_addresses, own_names

VALID_DAYS = 730
RENEW_BEFORE_DAYS = 30


@dataclass
class TlsFiles:
    cert: Path
    key: Path


def local_identity(extra_names: list[str] | None = None) -> tuple[list[str], list[str]]:
    """This computer's DNS names and IP addresses, for the certificate."""
    names: list[str] = ["localhost"]
    addresses: list[str] = ["127.0.0.1", "::1"]
    for name in own_names():
        if name not in names:
            names.append(name)
    for name in extra_names or []:
        name = name.strip().lower()
        if name and name != "*" and name not in names:
            try:
                ipaddress.ip_address(name)
                if name not in addresses:
                    addresses.append(name)
            except ValueError:
                names.append(name)
    for address in own_addresses():
        if address not in addresses:
            addresses.append(address)
    return names, addresses


def _san(cert: x509.Certificate) -> tuple[set[str], set[str]]:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set(), set()
    return set(ext.get_values_for_type(x509.DNSName)), {str(ip) for ip in ext.get_values_for_type(x509.IPAddress)}


def _usable(cert_path: Path, key_path: Path, names: list[str], addresses: list[str]) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        return False
    public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if public != cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
        return False
    if cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) < dt.timedelta(days=RENEW_BEFORE_DAYS):
        return False
    have_names, have_addresses = _san(cert)
    return set(names) <= have_names and {str(ipaddress.ip_address(a)) for a in addresses} <= have_addresses


COMMON_NAME_MAX = 64  # X.509 allows at most 64 characters in a Common Name
DNS_NAME_MAX = 253


def _common_name(names: list[str]) -> str:
    """The first name that fits in a Common Name (other than localhost if there is one); browsers use the SAN, so this is only a label."""
    for name in [*names[1:], *names[:1]]:
        if 1 <= len(name) <= COMMON_NAME_MAX:
            return name
    return "Dicommunication"


def _generate(cert_path: Path, key_path: Path, names: list[str], addresses: list[str]) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    names = [name for name in names if 1 <= len(name) <= DNS_NAME_MAX]  # a longer one is not a DNS name
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _common_name(names)), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Dicommunication (self-signed)")])
    now = dt.datetime.now(dt.timezone.utc)
    alt = [x509.DNSName(name) for name in names] + [x509.IPAddress(ipaddress.ip_address(a)) for a in addresses]
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only from the first byte where the system supports it.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key_pem)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def ensure_certificate(data_dir: Path | str, extra_names: list[str] | None = None) -> TlsFiles:
    """The certificate and key to serve with, made now if missing, expiring, or for other names."""
    folder = Path(data_dir) / "tls"
    files = TlsFiles(cert=folder / "cert.pem", key=folder / "key.pem")
    names, addresses = local_identity(extra_names)
    names = [name for name in names if 1 <= len(name) <= DNS_NAME_MAX]  # the same names are checked and written, or it would be remade at every start
    if not _usable(files.cert, files.key, names, addresses):
        _generate(files.cert, files.key, names, addresses)
    return files


def describe(cert_path: Path | str) -> dict:
    """What the Security page shows about the certificate."""
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    names, addresses = _san(cert)
    return {
        "enabled": True,
        "fingerprint": ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)),
        "not_after": cert.not_valid_after_utc.strftime("%Y-%m-%d"),
        "names": sorted(names) + sorted(addresses),
        "cert_path": str(cert_path),
    }
