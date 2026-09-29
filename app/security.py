"""Browser-facing protections for a login-less tool that listens on loopback.

The UI has no login by design (SECURITY.md): it is a console for the one
operator at the keyboard. That makes the operator's own web browser the thing
to defend against. Any site open in another tab can:

- submit forms to http://127.0.0.1:8080 (cross-site request forgery): add a
  remote node, push tags, clean pixel data, clear the log; and
- re-point a hostname it controls at 127.0.0.1 (DNS rebinding) and then read
  the JSON API as if it were same-origin.

This module refuses both, and sets response headers that keep the pages from
being framed or loading script from anywhere but /static.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.applog import log

ALLOWED_HOSTS_ENV = "DICOMM_ALLOWED_HOSTS"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        # htmx injects a small <style> for its request indicators.
        "style-src 'self' 'unsafe-inline'",
        # Dicom Cleaner previews are data: PNGs drawn onto a canvas.
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ]
)

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = tuple(
    (name.encode("latin-1"), value.encode("latin-1"))
    for name, value in (
        ("content-security-policy", CONTENT_SECURITY_POLICY),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        # Same-origin requests keep their Referer (a fallback for the
        # cross-site check below); nothing leaks to other sites.
        ("referrer-policy", "same-origin"),
        ("cross-origin-opener-policy", "same-origin"),
        ("cross-origin-resource-policy", "same-origin"),
        ("permissions-policy", "camera=(), microphone=(), geolocation=(), usb=(), payment=()"),
    )
)


def configured_hosts(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Extra host names from DICOMM_ALLOWED_HOSTS (comma-separated), lower-case.

    Needed only when the UI is reached by a *name* other than localhost, such
    as through an authenticating reverse proxy. "*" turns the host check off.
    """
    environ = os.environ if environ is None else environ
    raw = environ.get(ALLOWED_HOSTS_ENV, "")
    return frozenset(part.strip().lower().rstrip(".") for part in raw.split(",") if part.strip())


def hostname_of(host: str) -> str:
    """The name or address part of a Host header or URL netloc, lower-case."""
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:8080
        return host[1 : host.find("]")] if "]" in host else host[1:]
    if host.count(":") == 1:  # name:port or 1.2.3.4:port
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def is_allowed_host(host: str, extra: Iterable[str] = ()) -> bool:
    """Whether a Host header is one this app answers to.

    IP literals are always fine: a DNS-rebinding page can only make the
    browser send a *name* it controls, never a bare address. localhost and
    *.localhost never leave the machine. Anything else must be listed in
    DICOMM_ALLOWED_HOSTS.
    """
    extra = frozenset(extra)
    if "*" in extra:
        return True
    name = hostname_of(host)
    if not name:
        return True
    if name == "localhost" or name.endswith(".localhost") or name in extra:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def cross_site_reason(headers: dict[str, str], host: str, extra: Iterable[str] = ()) -> str | None:
    """Why a state-changing request looks like it came from another site, or None.

    Browsers say where a request came from (Sec-Fetch-Site, Origin, Referer);
    a page on another site cannot forge those. Requests with none of them
    (curl, scripts, the test client) are not from a browser and cannot be CSRF.
    """
    site = headers.get("sec-fetch-site", "").lower()
    if site in {"same-origin", "none"}:
        return None
    if site in {"cross-site", "same-site"}:
        return f"Sec-Fetch-Site: {site}"
    extra = frozenset(extra)
    for header in ("origin", "referer"):
        value = headers.get(header)
        if value is None:
            continue
        if value == "null":
            return f"{header.title()}: null"
        netloc = urlsplit(value).netloc.lower()
        if netloc == host.strip().lower() or hostname_of(netloc) in extra:
            return None
        return f"{header.title()}: {value}"
    return None


class SecurityMiddleware:
    """Pure ASGI middleware: host check, cross-site check, security headers."""

    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str] | None = None) -> None:
        self.app = app
        self.allowed_hosts = frozenset(configured_hosts() if allowed_hosts is None else allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        path = scope.get("path", "")

        if not is_allowed_host(host, self.allowed_hosts):
            log.warning("Refused request for %s with Host %r (not an allowed host)", path, host)
            await _plain_response(
                send,
                400,
                f"Host {hostname_of(host)!r} is not allowed. Open the app at http://127.0.0.1 "
                f"or http://localhost, or add the name to {ALLOWED_HOSTS_ENV}.",
            )
            return

        if scope.get("method", "GET").upper() not in SAFE_METHODS:
            reason = cross_site_reason(headers, host, self.allowed_hosts)
            if reason:
                log.warning("Refused cross-site %s %s (%s)", scope.get("method"), path, reason)
                await _plain_response(
                    send,
                    403,
                    "Refused: this request came from another website. Use the app's own pages.",
                )
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(n, v) for n, v in SECURITY_HEADERS if n not in existing]
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def _plain_response(send: Send, status: int, text: str) -> None:
    body = text.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                *SECURITY_HEADERS,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
