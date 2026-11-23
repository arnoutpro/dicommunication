"""Sign in, sign out, first-administrator setup, and changing your own password."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.applog import log
from app.audit import AuditLog
from app.auth import (
    LOOPBACK,
    SESSION_COOKIE,
    ADMIN,
    AuthError,
    AuthState,
    burn_time,
    check_password_rules,
    landing_for,
    safe_next,
    verify_password,
)
from app.routes._shared import page, templates

router = APIRouter()


def _state(request: Request) -> AuthState:
    return request.app.state.auth


def _audit(request: Request) -> AuditLog:
    return request.app.state.audit


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _set_cookie(request: Request, response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )


def _login_page(request: Request, *, error: str = "", username: str = "", next_url: str = "/", status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "username": username, "next_url": next_url, "expired": request.query_params.get("expired") == "1"},
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    auth = _state(request)
    if not auth.required():
        return RedirectResponse("/", status_code=303)  # sign-in is not turned on
    if not auth.users.has_users():
        return RedirectResponse("/setup", status_code=303)
    return _login_page(request, next_url=safe_next(next))


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    next_url = safe_next(str(form.get("next") or "/"))
    auth, audit, ip = _state(request), _audit(request), _ip(request)
    wait = auth.limiter.wait(username, ip)
    if wait:
        audit.record(ip=ip, action="Sign in blocked (too many attempts)", method="POST", path="/login", status=429)
        minutes = max(1, round(wait / 60))
        return _login_page(request, error=f"Too many wrong passwords. Try again in {minutes} minute{'s' if minutes != 1 else ''}.", username=username, next_url=next_url, status_code=429)
    user = auth.users.by_username(username)
    if user is not None and not user.disabled and verify_password(password, user.password_hash):
        auth.limiter.succeeded(username, ip)
        token = auth.sessions.create(user.id)
        auth.users.record_login(user.id)
        audit.record(user=user.username, role=user.role, ip=ip, action="Sign in", method="POST", path="/login", status=303)
        response = RedirectResponse(next_url if next_url != "/" else landing_for(user.role), status_code=303)
        _set_cookie(request, response, token)
        return response
    if user is None:
        burn_time(password)  # an unknown name takes as long to answer as a known one
    auth.limiter.failed(username, ip)
    audit.record(user=user.username if user else "", ip=ip, action="Sign in failed", method="POST", path="/login", status=401, detail="" if user else "unknown user")
    return _login_page(request, error="Wrong username or password.", username=username, next_url=next_url, status_code=401)


@router.post("/logout")
def logout(request: Request):
    auth = _state(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    session = auth.sessions.get(token)
    user = auth.users.get(session.user_id) if session else None
    if token:
        auth.sessions.delete(token)
    if user:
        _audit(request).record(user=user.username, role=user.role, ip=_ip(request), action="Sign out", method="POST", path="/logout", status=303)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# --- first administrator -------------------------------------------------------


def _setup_page(request: Request, *, error: str = "", username: str = "", status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, "setup.html", {"error": error, "username": username}, status_code=status_code)


def _on_this_computer(request: Request) -> bool:
    return _ip(request) in LOOPBACK or _ip(request) == "testclient"


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    if _state(request).users.has_users():
        return RedirectResponse("/login", status_code=303)
    if not _on_this_computer(request):
        return templates.TemplateResponse(request, "setup.html", {"error": "", "username": "", "remote": True}, status_code=403)
    return _setup_page(request)


@router.post("/setup")
async def setup(request: Request):
    auth = _state(request)
    if auth.users.has_users():
        return RedirectResponse("/login", status_code=303)
    if not _on_this_computer(request):
        return templates.TemplateResponse(request, "setup.html", {"error": "", "username": "", "remote": True}, status_code=403)
    form = await request.form()
    username, password, again = str(form.get("username") or "").strip(), str(form.get("password") or ""), str(form.get("confirm") or "")
    if password != again:
        return _setup_page(request, error="The two passwords are not the same.", username=username, status_code=400)
    try:
        user = auth.users.add(username, password, ADMIN)
    except AuthError as exc:
        return _setup_page(request, error=str(exc), username=username, status_code=400)
    _audit(request).record(user=user.username, role=user.role, ip=_ip(request), action="Created the first administrator", method="POST", path="/setup", status=303)
    log.info("The first administrator account was created")
    token = auth.sessions.create(user.id)
    auth.users.record_login(user.id)
    response = RedirectResponse("/", status_code=303)
    _set_cookie(request, response, token)
    return response


# --- your own account ----------------------------------------------------------


def _account_page(request: Request, *, error: str = "", done: bool = False, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, "account.html", page(request, nav="account", error=error, done=done), status_code=status_code)


@router.get("/account", response_class=HTMLResponse)
def account(request: Request):
    return _account_page(request)


@router.post("/account/password")
async def account_password(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        return _account_page(request, error="Sign-in is not turned on, so there is no password to change.", status_code=400)
    form = await request.form()
    current, new, again = str(form.get("current") or ""), str(form.get("new") or ""), str(form.get("confirm") or "")
    auth = _state(request)
    if not verify_password(current, user.password_hash):
        _audit(request).record(user=user.username, role=user.role, ip=_ip(request), action="Password change refused", method="POST", path="/account/password", status=400)
        return _account_page(request, error="The current password is not right.", status_code=400)
    if new != again:
        return _account_page(request, error="The two new passwords are not the same.", status_code=400)
    try:
        check_password_rules(new, user.username)
        auth.users.set_password(user.id, new)
    except AuthError as exc:
        return _account_page(request, error=str(exc), status_code=400)
    auth.sessions.delete_user(user.id, keep=request.cookies.get(SESSION_COOKIE, ""))  # other sign-ins end
    return _account_page(request, done=True)
