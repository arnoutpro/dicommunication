"""Configuration > Users, Security and Audit log (administrators only; see auth.POLICY)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.auth import ADMIN, ROLE_LABELS, ROLES, AuthError, AuthState, network_exposed
from app.applog import log
from app.routes._shared import page, templates
from app.security_settings import SecuritySettings

router = APIRouter()


def _auth(request: Request) -> AuthState:
    return request.app.state.auth


def _who(request: Request) -> tuple[str, str]:
    user = getattr(request.state, "user", None)
    return (user.username, user.role) if user else ("(no login)", "")


def _note(request: Request, action: str, detail: str) -> None:
    """A line for what an administrator did to accounts or settings: the target, never a password."""
    name, role = _who(request)
    request.app.state.audit.record(
        user=name, role=role, ip=request.client.host if request.client else "", action=action, method="POST", path=request.url.path, status=200, detail=detail
    )


# --- users ---------------------------------------------------------------------


def _users_page(request: Request, *, flash: str = "", ok: bool = False, status_code: int = 200) -> HTMLResponse:
    auth = _auth(request)
    me = getattr(request.state, "user", None)
    return templates.TemplateResponse(
        request,
        "config_users.html",
        page(request, nav="config-users", users=sorted(auth.users.list(), key=lambda u: u.username.casefold()), roles=ROLES, role_labels=ROLE_LABELS, me=me.id if me else "", flash=flash, flash_ok=ok, login_on=auth.required()),
        status_code=status_code,
    )


@router.get("/config/users", response_class=HTMLResponse)
def users_page(request: Request):
    return _users_page(request)


@router.post("/config/users")
async def users_add(request: Request):
    form = await request.form()
    username, password, role = str(form.get("username") or ""), str(form.get("password") or ""), str(form.get("role") or "")
    try:
        user = _auth(request).users.add(username, password, role)
    except AuthError as exc:
        return _users_page(request, flash=str(exc), status_code=400)
    _note(request, "User created", f"{user.username} ({user.role})")
    log.info("A user account was created")
    return _users_page(request, flash=f"The account {user.username} was created. They sign in with the password you gave; ask them to change it under Account.", ok=True)


@router.post("/config/users/{user_id}/update")
async def users_update(request: Request, user_id: str):
    form = await request.form()
    auth = _auth(request)
    user = auth.users.get(user_id)
    if user is None:
        return _users_page(request, flash="That user no longer exists.", status_code=404)
    me = getattr(request.state, "user", None)
    role, disabled = str(form.get("role") or user.role), bool(form.get("disabled"))
    try:
        if role != user.role:
            auth.users.set_role(user_id, role)
        if disabled != user.disabled:
            if disabled and me is not None and me.id == user_id:
                raise AuthError("You cannot disable your own account.")
            auth.users.set_disabled(user_id, disabled)
    except AuthError as exc:
        return _users_page(request, flash=str(exc), status_code=400)
    auth.sessions.delete_user(user_id, keep=request.cookies.get("dicomm_session", "") if me and me.id == user_id else "")
    _note(request, "User changed", f"{user.username}: role {role}{', disabled' if disabled else ''}")
    return _users_page(request, flash=f"{user.username} was updated.", ok=True)


@router.post("/config/users/{user_id}/password")
async def users_password(request: Request, user_id: str):
    form = await request.form()
    auth = _auth(request)
    user = auth.users.get(user_id)
    if user is None:
        return _users_page(request, flash="That user no longer exists.", status_code=404)
    try:
        auth.users.set_password(user_id, str(form.get("password") or ""))
    except AuthError as exc:
        return _users_page(request, flash=str(exc), status_code=400)
    auth.sessions.delete_user(user_id)
    _note(request, "Password reset", user.username)
    return _users_page(request, flash=f"The password of {user.username} was changed and they were signed out.", ok=True)


@router.post("/config/users/{user_id}/delete")
async def users_delete(request: Request, user_id: str):
    auth = _auth(request)
    user = auth.users.get(user_id)
    me = getattr(request.state, "user", None)
    if user is None:
        return _users_page(request, flash="That user no longer exists.", status_code=404)
    if me is not None and me.id == user_id:
        return _users_page(request, flash="You cannot delete your own account. Ask another administrator.", status_code=400)
    try:
        auth.users.delete(user_id)
    except AuthError as exc:
        return _users_page(request, flash=str(exc), status_code=400)
    auth.sessions.delete_user(user_id)
    _note(request, "User deleted", user.username)
    return _users_page(request, flash=f"{user.username} was deleted.", ok=True)


# --- security settings ---------------------------------------------------------


def _security_page(request: Request, *, flash: str = "", ok: bool = False, settings: SecuritySettings | None = None, error_roots: str = "", status_code: int = 200) -> HTMLResponse:
    auth = _auth(request)
    current = settings or request.app.state.security_settings.load()
    reason = "other computers can reach this app" if auth.exposed else "this is the headless edition, which always asks for sign-in" if auth.always else "an account exists" if auth.users.has_users() else ""
    return templates.TemplateResponse(
        request,
        "config_security.html",
        page(
            request,
            nav="config-security",
            settings=current,
            roots_text=error_roots or "\n".join(current.import_roots),
            login_on=auth.required(),
            login_reason=reason,
            exposed=auth.exposed,
            has_users=auth.users.has_users(),
            users_count=len(auth.users.list()),
            tls=getattr(request.app.state, "tls_info", None),
            flash=flash,
            flash_ok=ok,
        ),
        status_code=status_code,
    )


@router.get("/config/security", response_class=HTMLResponse)
def security_page(request: Request):
    return _security_page(request)


@router.post("/config/security")
async def security_save(request: Request):
    form = await request.form()
    roots_text = str(form.get("import_roots") or "")
    try:
        minutes = int(str(form.get("retention_minutes") or "").strip())
        days = int(str(form.get("history_days") or "").strip())
        settings = SecuritySettings(retention_minutes=minutes, history_days=days, import_roots=[line for line in roots_text.splitlines()])
    except (ValueError, ValidationError) as exc:
        message = "Use whole numbers: 0 to 1440 minutes for patient rows, 0 to 3650 days for history." if isinstance(exc, ValueError) and not isinstance(exc, ValidationError) else _first(exc)
        return _security_page(request, flash=message, error_roots=roots_text, status_code=400)
    request.app.state.security_settings.save(settings)
    request.app.state.retention.sweep_once()  # a shorter time takes effect now
    _note(request, "Security settings changed", f"rows {settings.retention_minutes} min, history {settings.history_days} days, {len(settings.import_roots)} allowed folder(s)")
    return _security_page(request, flash="Saved.", ok=True)


def _first(exc: ValidationError) -> str:
    error = exc.errors()[0]
    return str(error.get("msg", "Invalid")).removeprefix("Value error, ")


@router.get("/config/security/certificate")
def certificate(request: Request):
    info = getattr(request.app.state, "tls_info", None)
    if not info or not info.get("enabled"):
        return RedirectResponse("/config/security", status_code=303)
    return FileResponse(info["cert_path"], filename="dicommunication-certificate.pem", media_type="application/x-pem-file")


# --- audit log -----------------------------------------------------------------


@router.get("/config/audit", response_class=HTMLResponse)
def audit_page(request: Request, user: str = "", action: str = ""):
    audit = request.app.state.audit
    intact, why = audit.verify()
    return templates.TemplateResponse(
        request,
        "config_audit.html",
        page(request, nav="config-audit", rows=audit.tail(500, user=user.strip(), action=action.strip()), user=user, action=action, intact=intact, why=why),
    )


@router.get("/config/audit/download")
def audit_download(request: Request):
    path = request.app.state.audit.path
    if not path.exists():
        return RedirectResponse("/config/audit", status_code=303)
    return FileResponse(path, filename="dicommunication-audit.log", media_type="text/plain", headers={"Cache-Control": "no-store"})
