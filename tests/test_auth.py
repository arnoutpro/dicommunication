"""Accounts, sessions, lockout and the permission table (app/auth.py)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app import auth
from app.auth import ADMIN, OPERATOR, PUBLIC, VIEWER, AuthError

GOOD = "correct horse battery"


# --- passwords -----------------------------------------------------------------


def test_a_password_is_hashed_with_a_salt_and_verified() -> None:
    first, second = auth.hash_password(GOOD, n=2**10), auth.hash_password(GOOD, n=2**10)
    assert first != second and first.startswith("scrypt$1024$") and GOOD not in first
    assert auth.verify_password(GOOD, first) and not auth.verify_password(GOOD + "x", first)
    assert not auth.verify_password(GOOD, "not a hash") and not auth.verify_password(GOOD, "md5$1$2$3$4$5")


@pytest.mark.parametrize(
    ("password", "message"),
    [("short", "at least 12"), ("x" * 300, "at most"), ("aaaaaaaaaaaaaaaa", "repetitive"), ("alice-the-admin", "same as the username")],
)
def test_weak_passwords_are_refused(password, message) -> None:
    with pytest.raises(AuthError, match=message):
        auth.check_password_rules(password, "alice-the-admin")


def test_usernames_are_checked() -> None:
    assert auth.check_username(" alice.b ") == "alice.b"
    for bad in ("", "ab", "has space", "semi;colon", "x" * 41):
        with pytest.raises(AuthError):
            auth.check_username(bad)


# --- users ---------------------------------------------------------------------


@pytest.fixture
def users(tmp_path: Path) -> auth.UserStore:
    return auth.UserStore(tmp_path)


def test_users_are_stored_as_hashes_and_survive_a_restart(users: auth.UserStore, tmp_path: Path) -> None:
    assert not users.has_users()
    alice = users.add("Alice", GOOD, ADMIN)
    assert users.has_users() and "correct horse" not in (tmp_path / "users.json").read_text()
    again = auth.UserStore(tmp_path)
    assert again.by_username("alice").id == alice.id and again.get(alice.id).role == ADMIN
    assert auth.verify_password(GOOD, again.by_username("ALICE").password_hash)


def test_a_username_can_only_be_used_once_in_any_case(users: auth.UserStore) -> None:
    users.add("alice", GOOD, ADMIN)
    with pytest.raises(AuthError, match="already taken"):
        users.add("ALICE", GOOD + "2", OPERATOR)


def test_the_last_active_administrator_cannot_be_removed_demoted_or_disabled(users: auth.UserStore) -> None:
    alice = users.add("alice", GOOD, ADMIN)
    for action in (lambda: users.delete(alice.id), lambda: users.set_role(alice.id, OPERATOR), lambda: users.set_disabled(alice.id, True)):
        with pytest.raises(AuthError, match="at least one active administrator"):
            action()
    bob = users.add("bobby", GOOD + "b", ADMIN)
    users.set_role(bob.id, OPERATOR)  # fine: alice is still an administrator
    assert users.get(bob.id).role == OPERATOR
    users.set_disabled(bob.id, True)
    users.delete(bob.id)
    assert users.by_username("bobby") is None


def test_changing_a_password_replaces_the_hash_and_checks_the_rules(users: auth.UserStore) -> None:
    alice = users.add("alice", GOOD, ADMIN)
    with pytest.raises(AuthError):
        users.set_password(alice.id, "short")
    users.set_password(alice.id, "a brand new passphrase")
    assert auth.verify_password("a brand new passphrase", users.get(alice.id).password_hash)
    assert not auth.verify_password(GOOD, users.get(alice.id).password_hash)


def test_a_damaged_users_file_reads_as_no_users(tmp_path: Path) -> None:
    (tmp_path / "users.json").write_text("{not json")
    assert auth.UserStore(tmp_path).list() == []


# --- sessions ------------------------------------------------------------------


def test_a_session_expires_when_idle_and_when_old() -> None:
    sessions = auth.SessionStore(idle_seconds=0.15, absolute_seconds=0.5)
    token = sessions.create("u1")
    assert sessions.get(token).user_id == "u1" and sessions.get("wrong") is None and sessions.get("") is None
    time.sleep(0.1)
    assert sessions.get(token) is not None  # seen again: the idle clock restarts
    time.sleep(0.2)
    assert sessions.get(token) is None  # idle too long
    long = auth.SessionStore(idle_seconds=5, absolute_seconds=0.2)
    token = long.create("u1")
    time.sleep(0.3)
    assert long.get(token) is None  # too old even though it was in use


def test_signing_out_and_changing_a_password_end_sessions() -> None:
    sessions = auth.SessionStore()
    keep, other, someone_else = sessions.create("u1"), sessions.create("u1"), sessions.create("u2")
    sessions.delete_user("u1", keep=keep)
    assert sessions.get(keep) and sessions.get(other) is None and sessions.get(someone_else)
    sessions.delete(keep)
    assert sessions.get(keep) is None


def test_tokens_are_not_kept_in_the_clear() -> None:
    sessions = auth.SessionStore()
    token = sessions.create("u1")
    assert token not in sessions._sessions and len(token) >= 40


# --- lockout -------------------------------------------------------------------


def test_repeated_wrong_passwords_lock_the_name_and_the_address() -> None:
    limiter = auth.LoginLimiter(lock_after=3, lock_seconds=0.3)
    for _ in range(2):
        limiter.failed("alice", "10.0.0.9")
    assert limiter.wait("alice", "10.0.0.9") == 0
    limiter.failed("Alice", "10.0.0.9")
    assert limiter.wait("alice", "10.0.0.9") >= 1
    assert limiter.wait("alice", "10.0.0.77") >= 1  # the name is locked wherever it comes from
    assert limiter.wait("someone-else", "10.0.0.9") >= 1  # and the address, whatever the name
    assert limiter.wait("someone-else", "10.0.0.50") == 0
    time.sleep(0.35)
    assert limiter.wait("alice", "10.0.0.9") == 0  # the lock runs out


def test_a_good_login_clears_the_count() -> None:
    limiter = auth.LoginLimiter(lock_after=3, lock_seconds=60)
    limiter.failed("alice", "10.0.0.9")
    limiter.failed("alice", "10.0.0.9")
    limiter.succeeded("alice", "10.0.0.9")
    limiter.failed("alice", "10.0.0.9")
    assert limiter.wait("alice", "10.0.0.9") == 0


# --- the permission table ------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "role"),
    [
        ("GET", "/static/css/app.css", PUBLIC),
        ("GET", "/health", PUBLIC),
        ("POST", "/login", PUBLIC),
        ("GET", "/lite", VIEWER),
        ("GET", "/lite/api/status", VIEWER),
        ("GET", "/", OPERATOR),
        ("GET", "/router", OPERATOR),
        ("POST", "/router", ADMIN),
        ("POST", "/router/abc123/delete", ADMIN),
        ("POST", "/router/abc123/run-now", OPERATOR),
        ("GET", "/router/abc123/runs", OPERATOR),
        ("POST", "/tools/c-find-advanced/run", OPERATOR),
        ("GET", "/tools/dicom-import/abc", OPERATOR),
        ("POST", "/tools/hl7-send/run", OPERATOR),
        ("GET", "/config/remotes", ADMIN),
        ("POST", "/config/transfer/export", ADMIN),
        ("GET", "/config/users", ADMIN),
        ("GET", "/logs/download", ADMIN),
        ("POST", "/api/fs/pick-directory", ADMIN),
        ("GET", "/api/remotes", ADMIN),
        ("POST", "/api/tools/c-find/run", OPERATOR),
        ("GET", "/something/nobody/planned", ADMIN),  # deny by default
        ("DELETE", "/lite", ADMIN),  # a method the rule does not list falls through
    ],
)
def test_who_may_use_which_url(method, path, role) -> None:
    assert auth.policy_for(method, path).role == role


def test_roles_rank_and_allow() -> None:
    assert auth.allows(ADMIN, OPERATOR) and auth.allows(OPERATOR, OPERATOR) and auth.allows(OPERATOR, VIEWER)
    assert not auth.allows(VIEWER, OPERATOR) and not auth.allows(OPERATOR, ADMIN) and not auth.allows(None, VIEWER)
    assert auth.allows(None, PUBLIC)


@pytest.mark.parametrize(
    ("target", "expected"),
    [("/tools/dicom-import", "/tools/dicom-import"), ("/router?x=1", "/router?x=1"), ("", "/"), ("https://evil.example/", "/"), ("//evil.example", "/"), ("/\\evil", "/"), ("/a\r\nSet-Cookie: x", "/"), ("javascript:alert(1)", "/")],
)
def test_the_page_to_return_to_is_always_on_this_site(target, expected) -> None:
    assert auth.safe_next(target) == expected


def test_network_exposure_is_read_from_the_environment() -> None:
    assert not auth.network_exposed({})
    assert not auth.network_exposed({"DICOMM_LISTEN_HOST": "127.0.0.1"})
    assert not auth.network_exposed({"DICOMM_HTTP_BIND": "127.0.0.1"})
    assert auth.network_exposed({"DICOMM_LISTEN_HOST": "0.0.0.0"})
    assert auth.network_exposed({"DICOMM_HTTP_BIND": "0.0.0.0"})
    assert auth.network_exposed({"DICOMM_LISTEN_HOST": "10.1.2.3"})


def test_login_is_required_once_an_account_exists_or_when_exposed(tmp_path: Path) -> None:
    state = auth.AuthState(users=auth.UserStore(tmp_path))
    assert not state.required()
    state.users.add("alice", GOOD, ADMIN)
    assert state.required()
    assert auth.AuthState(users=auth.UserStore(tmp_path / "empty"), exposed=True).required()


def test_landing_page_depends_on_the_role() -> None:
    assert auth.landing_for(VIEWER) == "/lite" and auth.landing_for(OPERATOR) == "/" and auth.landing_for(ADMIN) == "/"
