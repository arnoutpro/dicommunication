"""The gate in front of every request: who is this, and may they do that?

Pure ASGI, so it sees the request before routing. It is only active when login is
required (see ``AuthState.required``): when other computers can reach the app, or
as soon as an account exists. Otherwise the app behaves as it always has, for a
single local operator, and the request is simply treated as an administrator's.

It also writes the audit log for everything that changes something.
"""

from __future__ import annotations

import json
import re
from http.cookies import SimpleCookie
from urllib.parse import quote

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.audit import AuditLog
from app.auth import ADMIN, LOOPBACK, PUBLIC, SESSION_COOKIE, AuthState, User, allows, policy_for

# Reading these shows patient data or the audit trail, so they are logged although they only read.
AUDIT_GETS = re.compile(r"^/(logs/download|router/[^/]+/runs|config/audit(/download)?|tools/dicom-import/[^/]+)$")
SETUP_NEEDED = (
    "<!doctype html><meta charset=utf-8><title>Setup needed</title>"
    "<body style='font:16px system-ui;max-width:42rem;margin:4rem auto;padding:0 1rem'>"
    "<h1>Setup needed</h1><p>Dicommunication asks for sign-in here, so it needs an administrator "
    "account before anyone can use it.</p><p>On the computer it runs on, open "
    "<code>http://localhost:{port}/setup</code> in a browser (or <code>https://localhost:{port}/setup</code> if "
    "the address needs https). See the documentation for <code>--create-admin</code> if you cannot open a browser there.</p></body>"
)


def _cookie(scope: Scope, name: str) -> str:
    for key, value in scope.get("headers", []):
        if key == b"cookie":
            jar = SimpleCookie()
            try:
                jar.load(value.decode("latin-1"))
            except Exception:  # noqa: BLE001
                return ""
            if name in jar:
                return jar[name].value
    return ""


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


async def _respond(send: Send, status: int, body: str, content_type: str = "text/html; charset=utf-8", extra: list[tuple[bytes, bytes]] | None = None) -> None:
    data = body.encode("utf-8")
    headers = [(b"content-type", content_type.encode()), (b"content-length", str(len(data)).encode()), (b"cache-control", b"no-store"), *(extra or [])]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


class AuthMiddleware:
    def __init__(self, app: ASGIApp, state: AuthState, audit: AuditLog) -> None:
        self.app = app
        self.state = state
        self.audit = audit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        rule = policy_for(method, path)
        client = scope.get("client")
        ip = client[0] if client else ""
        user: User | None = None
        required = self.state.required()
        scope.setdefault("state", {})
        if required:
            session = self.state.sessions.get(_cookie(scope, SESSION_COOKIE))
            user = self.state.users.get(session.user_id) if session else None
            if user is not None and user.disabled:
                user = None

        if not required:
            scope["state"].update(user=None, role=ADMIN, auth_required=False)
        else:
            scope["state"].update(user=user, role=user.role if user else None, auth_required=True)
            if rule.role != PUBLIC:
                if not self.state.users.has_users():
                    if method in ("GET", "HEAD") and ip in LOOPBACK and _header(scope, b"hx-request") != "true":
                        await _respond(send, 303, "", "text/plain", [(b"location", b"/setup")])  # on the server itself: go and create the administrator
                        return
                    port = (scope.get("server") or ("", 8080))[1]
                    await _respond(send, 503, SETUP_NEEDED.format(port=port))
                    return
                if user is None:
                    await self._unauthenticated(scope, send, method, path)
                    return
                if not allows(user.role, rule.role):
                    self.audit.record(user=user.username, role=user.role, ip=ip, action="Denied: " + rule.label, method=method, path=path, status=403)
                    await self._forbidden(scope, send, method, path)
                    return

        status_holder = {"status": 0}

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                if required and rule.role != PUBLIC and not path.startswith("/static"):
                    headers = list(message.get("headers", []))
                    if not any(k.lower() == b"cache-control" for k, _ in headers):
                        headers.append((b"cache-control", b"no-store"))  # patient data must not sit in the browser cache
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, capture)

        if rule.role != PUBLIC and (method not in ("GET", "HEAD", "OPTIONS") or AUDIT_GETS.match(path)):
            self.audit.record(
                user=user.username if user else "(no login)",
                role=user.role if user else "",
                ip=ip,
                action=rule.label,
                method=method,
                path=path,
                status=status_holder["status"],
            )

    @staticmethod
    def _wants_json(scope: Scope, path: str) -> bool:
        return path.startswith("/api/") or "application/json" in _header(scope, b"accept")

    async def _unauthenticated(self, scope: Scope, send: Send, method: str, path: str) -> None:
        if _header(scope, b"hx-request") == "true":
            await _respond(send, 401, "Sign in required", "text/plain; charset=utf-8", [(b"hx-redirect", b"/login")])
        elif self._wants_json(scope, path):
            await _respond(send, 401, json.dumps({"detail": "Sign in required"}), "application/json")
        elif method in ("GET", "HEAD"):
            query = scope.get("query_string", b"").decode("latin-1")
            target = quote(path + ("?" + query if query else ""), safe="/?=&")
            await _respond(send, 303, "", "text/plain", [(b"location", f"/login?next={target}".encode())])
        else:
            await _respond(send, 303, "", "text/plain", [(b"location", b"/login")])

    async def _forbidden(self, scope: Scope, send: Send, method: str, path: str) -> None:
        if self._wants_json(scope, path):
            await _respond(send, 403, json.dumps({"detail": "Your role may not do that"}), "application/json")
        elif _header(scope, b"hx-request") == "true":
            await _respond(send, 403, "Your role may not do that.", "text/plain; charset=utf-8")
        else:
            await _respond(
                send,
                403,
                "<!doctype html><meta charset=utf-8><title>Not allowed</title>"
                "<body style='font:16px system-ui;max-width:42rem;margin:4rem auto;padding:0 1rem'>"
                "<h1>Not allowed</h1><p>Your account may not do that. Ask an administrator if you need it.</p>"
                "<p><a href='/'>Back</a></p></body>",
            )
