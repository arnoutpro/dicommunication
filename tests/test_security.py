"""Browser-facing protections (app/security.py) and the leftovers they replaced.

The UI has no login by design; these tests pin down that a page on another
website still cannot drive it (CSRF) or read it (DNS rebinding).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.models import RemoteNode
from app.security import cross_site_reason, is_allowed_host
from app.store import ConfigStore

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
EVIL = {"Origin": "https://evil.example", "Referer": "https://evil.example/page", "Sec-Fetch-Site": "cross-site"}
ADD_REMOTE = {"name": "attacker", "ae_title": "EVIL", "host": "203.0.113.9", "port": "104"}


@pytest.fixture
def local(tmp_path) -> tuple[TestClient, ConfigStore]:
    """A client that talks to the app the way the desktop window does."""
    store = ConfigStore(tmp_path)
    return TestClient(create_app(store), base_url="http://127.0.0.1:8080"), store


# ── Cross-site request forgery ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "headers",
    [
        EVIL,
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},  # another port or subdomain is still another origin
        {"Origin": "https://evil.example"},
        {"Origin": "null"},  # sandboxed iframe / data: URL
        {"Referer": "http://evil.example:8080/x"},
        {"Origin": "http://localhost:3000"},  # a different local dev server
    ],
)
def test_cross_site_form_post_is_refused(local, headers) -> None:
    client, store = local
    response = client.post("/config/remotes", data=ADD_REMOTE, headers=headers, follow_redirects=False)
    assert response.status_code == 403
    assert "another website" in response.text
    assert store.load().remotes == []


def test_cross_site_post_cannot_clear_the_log_or_hit_the_json_api(local) -> None:
    client, _ = local
    assert client.post("/logs/clear", headers=EVIL, follow_redirects=False).status_code == 403
    assert client.post("/api/remotes", json={"name": "x", "ae_title": "X", "host": "1.2.3.4"}, headers=EVIL).status_code == 403
    assert client.delete("/api/remotes/anything", headers=EVIL).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1:8080"},
        {"Origin": "http://127.0.0.1:8080"},  # older browsers: no Sec-Fetch-Site
        {"Referer": "http://127.0.0.1:8080/config/remotes"},
        {"Sec-Fetch-Site": "none"},  # typed into the address bar
        {},  # curl, scripts: not a browser, so not CSRF
    ],
)
def test_same_origin_and_non_browser_posts_still_work(local, headers) -> None:
    client, store = local
    response = client.post("/config/remotes", data=ADD_REMOTE, headers=headers, follow_redirects=False)
    assert response.status_code in (200, 303)
    assert [r.name for r in store.load().remotes] == ["attacker"]


def test_cross_site_reads_are_not_blocked_by_the_csrf_check(local) -> None:
    # A plain GET changes nothing; the browser's same-origin policy keeps the
    # response from the other site. Only the Host check applies to reads.
    client, _ = local
    assert client.get("/health", headers=EVIL).status_code == 200


def test_reverse_proxy_origin_can_be_allowed_by_name() -> None:
    headers = {"origin": "https://dicomm.example.org"}
    assert cross_site_reason(headers, "127.0.0.1:8080") is not None
    assert cross_site_reason(headers, "127.0.0.1:8080", {"dicomm.example.org"}) is None


# ── DNS rebinding (Host header) ─────────────────────────────────────────────


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8080", "127.0.0.1.nip.io:8080", "attacker.test"])
def test_foreign_host_header_is_refused(local, host) -> None:
    client, _ = local
    response = client.get("/api/remotes", headers={"Host": host})
    assert response.status_code == 400
    assert "not allowed" in response.text


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:8080", "localhost:8080", "LOCALHOST", "app.localhost:8080", "[::1]:8080", "10.0.0.5:8080", "192.168.1.20"],
)
def test_loopback_names_and_ip_addresses_are_allowed(local, host) -> None:
    client, _ = local
    assert client.get("/health", headers={"Host": host}).status_code == 200


def test_allowed_hosts_setting() -> None:
    assert not is_allowed_host("dicomm.example.org")
    assert is_allowed_host("dicomm.example.org:443", {"dicomm.example.org"})
    assert is_allowed_host("anything.example", {"*"})


# ── Headers and endpoints ───────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/", "/static/js/app.js", "/health"])
def test_security_headers_on_pages_static_files_and_api(local, path) -> None:
    client, _ = local
    response = client.get(path)
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


def test_refusals_carry_the_security_headers_too(local) -> None:
    client, _ = local
    response = client.get("/", headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_served(local, path) -> None:
    client, _ = local
    assert client.get(path).status_code == 404


# ── What the strict CSP relies on ───────────────────────────────────────────


def test_templates_have_no_inline_script_handlers_or_eval() -> None:
    """script-src 'self' blocks all of these, and htmx js:{} values eval'd PACS data."""
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for pattern in (r"<script>", r"<script\s+(?![^>]*\bsrc=)[^>]*>", r"\son[a-z]+=\"", r"js:\{", r"hx-on[:-]"):
            for match in re.finditer(pattern, text):
                offenders.append(f"{path.relative_to(TEMPLATES)}: {match.group(0)}")
    assert offenders == []


def test_cleaner_preview_study_uid_is_data_not_code() -> None:
    """A PACS-supplied UID lands in hx-vals as JSON data, never as code."""
    import html as html_lib
    import json

    from app.routes._shared import templates

    hostile = """1.2"+alert(1)+"3'<x>"""
    rendered = templates.get_template("partials/cleaner_preview_images.html").render(
        href=lambda p: p, study_uid=hostile, images=[{"SeriesInstanceUID": "1.2.3.4.5.6", "SOPInstanceUID": "7", "InstanceNumber": 1}], error=None
    )
    assert "js:" not in rendered
    attr = re.search(r"hx-vals='([^']*)'", rendered)
    assert attr, rendered
    assert json.loads(html_lib.unescape(attr.group(1))) == {"study_uid": hostile}


# ── Network PING argument injection ─────────────────────────────────────────


@pytest.mark.parametrize("host", ["-f", "--help", "-t"])
def test_remote_host_cannot_look_like_a_ping_option(host) -> None:
    with pytest.raises(ValidationError):
        RemoteNode(name="x", ae_title="X", host=host, port=104)
    with pytest.raises(ValidationError):
        RemoteNode(name="x", ae_title="X", hostname=host, port=104)
