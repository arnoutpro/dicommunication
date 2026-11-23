"""Limits for operators on what they may point the server at.

An administrator may use any folder and any address. An operator may only use the
nodes already configured, and read folders an administrator listed under
Configuration > Security. Without these limits a signed-in operator could make the
server read any folder it can, or send to a PACS of their own choosing. When
sign-in is off (one person on one computer) everyone counts as an administrator.
"""

from __future__ import annotations

from fastapi import Request

from app.auth import ADMIN
from app.security_settings import path_is_allowed

ADHOC_DENIED = "Only an administrator can use an address that is not a saved node. Choose a saved node, or ask an administrator to add it."
FOLDER_DENIED = "This folder is not on the list of folders operators may use. Ask an administrator to add it under Configuration > Security."


def is_admin(request: Request) -> bool:
    return getattr(request.state, "role", ADMIN) == ADMIN


def folder_allowed(request: Request, path: str) -> bool:
    if is_admin(request):
        return True
    return path_is_allowed(path, request.app.state.security_settings.load().import_roots)


def node_known(request: Request, host: str, port: object) -> bool:
    """True if host and port are one of the configured nodes."""
    wanted_host = (host or "").strip().casefold()
    try:
        wanted_port = int(str(port).strip())
    except ValueError:
        return False
    return any(
        node.port == wanted_port and wanted_host in {node.connect_host.casefold(), (node.host or "").casefold(), (node.hostname or "").casefold()}
        for node in request.app.state.store.load().remotes
    )
