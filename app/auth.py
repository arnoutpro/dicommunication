"""Accounts, sessions and the permission table.

The app began as a console for one operator on one machine, with no login. Once it
listens on the network and handles patient data it needs to know who is asking, so
this module adds: accounts with hashed passwords, server-side sessions, a lockout
for repeated wrong passwords, and ``POLICY``, the table that says which role may
use which URL. A URL that matches no rule needs an administrator (deny by default),
and a test fails if a route has no explicit rule.

Roles, lowest to highest:

* ``viewer``    the status dashboard and help only.
* ``operator``  everything that works with PACS nodes already configured: Analytics,
                Import, Anonymizer, Cleaner, Send Image, PDF, Tag Editor, Testbench,
                Worklist, HL7, and starting or stopping Router rules.
* ``admin``     everything, including configuration, users, logs, settings import
                and export, and any folder or host.

No web code here: the middleware (app/auth_middleware.py) and the pages use it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

VIEWER, OPERATOR, ADMIN = "viewer", "operator", "admin"
ROLES = (VIEWER, OPERATOR, ADMIN)
RANK = {VIEWER: 1, OPERATOR: 2, ADMIN: 3}
ROLE_LABELS = {VIEWER: "Viewer (status dashboard only)", OPERATOR: "Operator (use the tools)", ADMIN: "Administrator (everything)"}

SESSION_COOKIE = "dicomm_session"
IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 12 * 60 * 60
MIN_PASSWORD = 12
MAX_PASSWORD = 256
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
LOCK_AFTER = 5
LOCK_SECONDS = 5 * 60
USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class AuthError(Exception):
    """A problem the user can read and fix (weak password, name taken)."""


# --- passwords -----------------------------------------------------------------


def hash_password(password: str, *, n: int = SCRYPT_N) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=SCRYPT_R, p=SCRYPT_P, maxmem=128 * 1024 * 1024, dklen=32)
    return "scrypt${}${}${}${}${}".format(n, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=128 * 1024 * 1024, dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH = hash_password("not a real password, used to keep timing the same", n=2**12)


def burn_time(password: str) -> None:
    """Spend the time a real check takes, so an unknown username answers no faster."""
    verify_password(password, _DUMMY_HASH)


def check_password_rules(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters. A few random words make a good password.")
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"Use at most {MAX_PASSWORD} characters.")
    if username and password.casefold() == username.casefold():
        raise AuthError("The password must not be the same as the username.")
    if len(set(password)) < 5:
        raise AuthError("That password is too repetitive. Choose a different one.")


def check_username(username: str) -> str:
    name = (username or "").strip()
    if not USERNAME.match(name):
        raise AuthError("A username is 3 to 40 letters, digits, dots, dashes or underscores.")
    return name


# --- users ---------------------------------------------------------------------


@dataclass
class User:
    id: str
    username: str
    role: str
    password_hash: str
    disabled: bool = False
    created_at: str = ""
    last_login: str = ""

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "role": self.role, "disabled": self.disabled, "created_at": self.created_at, "last_login": self.last_login}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UserStore:
    """Accounts in ``users.json`` in the data folder (hashes only, never passwords)."""

    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / "users.json"
        self._lock = threading.Lock()
        self._cache: tuple[float, list[User]] | None = None

    def _read(self) -> list[User]:
        try:
            stamp = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self._cache = None
            return []
        if self._cache and self._cache[0] == stamp:
            return list(self._cache[1])
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            users = [User(**item) for item in raw]
        except (OSError, ValueError, TypeError):
            users = []
        self._cache = (stamp, users)
        return list(users)

    def _write(self, users: list[User]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump([u.__dict__ for u in users], handle, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._cache = None

    def list(self) -> list[User]:
        with self._lock:
            return self._read()

    def has_users(self) -> bool:
        return bool(self.list())

    def get(self, user_id: str) -> User | None:
        return next((u for u in self.list() if u.id == user_id), None)

    def by_username(self, username: str) -> User | None:
        wanted = (username or "").strip().casefold()
        return next((u for u in self.list() if u.username.casefold() == wanted), None)

    def add(self, username: str, password: str, role: str) -> User:
        name = check_username(username)
        if role not in ROLES:
            raise AuthError("Choose a role.")
        check_password_rules(password, name)
        with self._lock:
            users = self._read()
            if any(u.username.casefold() == name.casefold() for u in users):
                raise AuthError(f"The username {name} is already taken.")
            user = User(id=uuid.uuid4().hex[:12], username=name, role=role, password_hash=hash_password(password), created_at=_now())
            self._write([*users, user])
            return user

    def _change(self, user_id: str, change) -> User:
        with self._lock:
            users = self._read()
            target = next((u for u in users if u.id == user_id), None)
            if target is None:
                raise AuthError("That user no longer exists.")
            change(target, users)
            self._write(users)
            return target

    def set_password(self, user_id: str, password: str) -> User:
        user = self.get(user_id)
        if user is None:
            raise AuthError("That user no longer exists.")
        check_password_rules(password, user.username)
        hashed = hash_password(password)
        return self._change(user_id, lambda u, _all: setattr(u, "password_hash", hashed))

    def set_role(self, user_id: str, role: str) -> User:
        if role not in ROLES:
            raise AuthError("Choose a role.")

        def change(user: User, users: list[User]) -> None:
            if user.role == ADMIN and role != ADMIN and _last_admin(user, users):
                raise AuthError("There must always be at least one active administrator.")
            user.role = role

        return self._change(user_id, change)

    def set_disabled(self, user_id: str, disabled: bool) -> User:
        def change(user: User, users: list[User]) -> None:
            if disabled and user.role == ADMIN and _last_admin(user, users):
                raise AuthError("There must always be at least one active administrator.")
            user.disabled = disabled

        return self._change(user_id, change)

    def delete(self, user_id: str) -> None:
        with self._lock:
            users = self._read()
            target = next((u for u in users if u.id == user_id), None)
            if target is None:
                return
            if target.role == ADMIN and _last_admin(target, users):
                raise AuthError("There must always be at least one active administrator.")
            self._write([u for u in users if u.id != user_id])

    def record_login(self, user_id: str) -> None:
        try:
            self._change(user_id, lambda u, _all: setattr(u, "last_login", _now()))
        except AuthError:
            pass


def _last_admin(user: User, users: list[User]) -> bool:
    return not any(u.role == ADMIN and not u.disabled for u in users if u.id != user.id)


# --- sessions ------------------------------------------------------------------


@dataclass
class Session:
    user_id: str
    created: float
    seen: float


class SessionStore:
    """Sessions held in memory: a restart signs everyone out, which is the safe default."""

    def __init__(self, idle_seconds: float = IDLE_SECONDS, absolute_seconds: float = ABSOLUTE_SECONDS) -> None:
        self.idle_seconds = idle_seconds
        self.absolute_seconds = absolute_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._sessions[self._key(token)] = Session(user_id, now, now)
        return token

    def get(self, token: str) -> Session | None:
        if not token:
            return None
        key = self._key(token)
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            if now - session.seen > self.idle_seconds or now - session.created > self.absolute_seconds:
                del self._sessions[key]
                return None
            session.seen = now
            return session

    def delete(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(self._key(token), None)

    def delete_user(self, user_id: str, keep: str = "") -> None:
        keep_key = self._key(keep) if keep else ""
        with self._lock:
            for key in [k for k, s in self._sessions.items() if s.user_id == user_id and k != keep_key]:
                del self._sessions[key]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


# --- lockout -------------------------------------------------------------------


class LoginLimiter:
    """Five wrong passwords in a row lock a username, and the address they came from, for five minutes."""

    def __init__(self, lock_after: int = LOCK_AFTER, lock_seconds: float = LOCK_SECONDS) -> None:
        self.lock_after = lock_after
        self.lock_seconds = lock_seconds
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def keys(username: str, ip: str) -> list[str]:
        return [f"user:{(username or '').strip().casefold()}", f"ip:{ip}"]

    def wait(self, username: str, ip: str) -> int:
        """Seconds left on a lock, 0 if not locked."""
        now = time.monotonic()
        with self._lock:
            left = 0.0
            for key in self.keys(username, ip):
                count, until = self._fails.get(key, (0, 0.0))
                if count >= self.lock_after and until > now:
                    left = max(left, until - now)
            return int(left) + (1 if left else 0)

    def failed(self, username: str, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in self.keys(username, ip):
                count, until = self._fails.get(key, (0, 0.0))
                if until and until <= now:
                    count = 0  # the lock ran out: start counting again
                count += 1
                self._fails[key] = (count, now + self.lock_seconds if count >= self.lock_after else until)

    def succeeded(self, username: str, ip: str) -> None:
        with self._lock:
            for key in self.keys(username, ip):
                self._fails.pop(key, None)


# --- who may use which URL -----------------------------------------------------

PUBLIC = "public"


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern
    methods: frozenset[str] | None
    role: str
    label: str


def _rule(pattern: str, role: str, label: str, methods: str = "") -> Rule:
    return Rule(re.compile(pattern), frozenset(methods.split()) or None, role, label)


GET, POST = "GET", "POST"
# First match wins. Anything that matches nothing needs an administrator.
POLICY: tuple[Rule, ...] = (
    # Anyone, signed in or not.
    _rule(r"^/static(/.*)?$", PUBLIC, "Static file"),
    _rule(r"^/health$", PUBLIC, "Health check", GET),
    _rule(r"^/(login|setup)$", PUBLIC, "Sign in", "GET POST"),
    _rule(r"^/logout$", PUBLIC, "Sign out", POST),
    # Any signed-in user.
    _rule(r"^/(help|about)$", VIEWER, "Read help", GET),
    _rule(r"^/lite(/api/status)?$", VIEWER, "View the status dashboard", GET),
    _rule(r"^/account(/password)?$", VIEWER, "Own account", "GET POST"),
    # Administrators only. These come before the broader operator rules below.
    _rule(r"^/config/users(/.*)?$", ADMIN, "Manage users", "GET POST"),
    _rule(r"^/config/security$", ADMIN, "Change security settings", "GET POST"),
    _rule(r"^/config/audit(/download)?$", ADMIN, "View the audit log", GET),
    _rule(r"^/config(/.*)?$", ADMIN, "Change configuration", "GET POST"),
    _rule(r"^/logs(/.*)?$", ADMIN, "Application log", "GET POST"),
    _rule(r"^/api/(config|remotes|identities|logging|logs|fs)(/.*)?$", ADMIN, "Change configuration (API)", "GET POST PUT DELETE"),
    _rule(r"^/router$", ADMIN, "Add or change a router rule", POST),
    _rule(r"^/router/[^/]+/delete$", ADMIN, "Delete a router rule", POST),
    # Operators: the tools, against nodes already configured.
    _rule(r"^/$", OPERATOR, "Dashboard", GET),
    _rule(r"^/router$", OPERATOR, "View router rules", GET),
    _rule(r"^/router/[^/]+/runs$", OPERATOR, "View router run history", GET),
    _rule(r"^/router/[^/]+/(start|pause|stop|run-now)$", OPERATOR, "Start or stop a router rule", POST),
    _rule(r"^/(testbench|echo-board|worklist)(/.*)?$", OPERATOR, "Use a test tool", "GET POST"),
    _rule(r"^/tools/(dicom-import)(/.*)?$", OPERATOR, "Dicom Import", "GET POST"),
    _rule(r"^/tools/c-find-advanced/run$", OPERATOR, "Run an Analytics query", POST),
    _rule(r"^/tools/(anonymize|dicom-cleaner|send-image|pdf-store|tag-editor|hl7-send)(/.*)?$", OPERATOR, "Use a PACS tool", "GET POST"),
    _rule(r"^/tools(/.*)?$", OPERATOR, "Use a tool", "GET POST"),
    _rule(r"^/api/(tools|echo-board|worklist|hl7)(/.*)?$", OPERATOR, "Use a tool (API)", "GET POST PUT DELETE"),
)
DEFAULT_RULE = Rule(re.compile(".*"), None, ADMIN, "Administration")


def policy_for(method: str, path: str) -> Rule:
    for rule in POLICY:
        if rule.pattern.match(path) and (rule.methods is None or method.upper() in rule.methods):
            return rule
    return DEFAULT_RULE


def allows(role: str | None, needed: str) -> bool:
    if needed == PUBLIC:
        return True
    return role in RANK and RANK[role] >= RANK[needed]


def safe_next(target: str, default: str = "/") -> str:
    """A post-login destination: a path on this site, never another address."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target or any(ord(c) < 32 for c in target):
        return default
    return target


def landing_for(role: str) -> str:
    return "/lite" if role == VIEWER else "/"


def network_exposed(environ: dict | None = None) -> bool:
    """True when the app can be reached from other computers (not only this one)."""
    environ = os.environ if environ is None else environ
    for key in ("DICOMM_LISTEN_HOST", "DICOMM_HTTP_BIND"):
        value = (environ.get(key) or "").strip()
        if value and value not in LOOPBACK and value != "[::1]":
            return True
    return False


def login_always_required(environ: dict | None = None) -> bool:
    """True for the Windows headless edition, whose service sets ``DICOMM_REQUIRE_LOGIN=1``."""
    environ = os.environ if environ is None else environ
    return (environ.get("DICOMM_REQUIRE_LOGIN") or "").strip() == "1"


@dataclass
class AuthState:
    """What the middleware and pages share for one running app."""

    users: UserStore
    sessions: SessionStore = field(default_factory=SessionStore)
    limiter: LoginLimiter = field(default_factory=LoginLimiter)
    exposed: bool = False
    always: bool = False  # the headless edition: a service on a server, whether or not the network option is ticked

    def required(self) -> bool:
        """Login is required when other computers can reach the app, in the headless edition, or as soon as an account exists."""
        return self.exposed or self.always or self.users.has_users()
