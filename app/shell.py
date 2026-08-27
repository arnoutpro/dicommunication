"""Two products from one process: Dicommunication and Dicomtag Analytics.

The MSI / DMG still freeze a single executable. Dicommunication is the
workstation (PING, DIMSE, HL7, worklist). Dicomtag Analytics is the
Study Root C-FIND UI that used to live under Test tools as C-FIND Advanced.

Both share config, logs, and the local server. The analytics UI is mounted at
``/vue/`` so both windows can stay open against one uvicorn. The Start-menu
shortcut passes ``--profile dicomtag-analytics``. ``vue-analytics`` is still
accepted as the previous profile name.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from app.tools import list_tools, list_tools_by_category
from app.tools.base import BaseTool

SHELL_DICOMM = "dicommunication"
SHELL_VUE = "vue"

PRODUCT_DICOMM = "Dicommunication"
PRODUCT_ANALYTICS = "Dicomtag Analytics"

PROFILE_DICOMM = "dicommunication"
PROFILE_VUE = "dicomtag-analytics"
PROFILE_VUE_LEGACY = "vue-analytics"
PROFILES = (PROFILE_DICOMM, PROFILE_VUE, PROFILE_VUE_LEGACY)

VUE_PREFIX = "/vue"
VUE_TOOL_ID = "c-find-advanced"

PRODUCT_NAMES = {
    SHELL_DICOMM: PRODUCT_DICOMM,
    SHELL_VUE: PRODUCT_ANALYTICS,
}

WINDOW_TITLES = {
    PROFILE_DICOMM: PRODUCT_DICOMM,
    PROFILE_VUE: PRODUCT_ANALYTICS,
    PROFILE_VUE_LEGACY: PRODUCT_ANALYTICS,
}


def is_analytics_profile(profile: str) -> bool:
    return profile in (PROFILE_VUE, PROFILE_VUE_LEGACY)


def profile_start_path(profile: str) -> str:
    if is_analytics_profile(profile):
        return VUE_PREFIX + "/"
    return "/"


def profile_window_title(profile: str) -> str:
    return WINDOW_TITLES.get(profile, WINDOW_TITLES[PROFILE_DICOMM])


def is_vue_public_path(path: str) -> bool:
    return path == VUE_PREFIX or path.startswith(VUE_PREFIX + "/")


def strip_vue_prefix(path: str) -> str:
    if path == VUE_PREFIX:
        return "/"
    if path.startswith(VUE_PREFIX + "/"):
        stripped = path[len(VUE_PREFIX) :]
        return stripped or "/"
    return path


def vue_path_allowed(path: str) -> bool:
    """Pages the analytics product may show after the /vue prefix is stripped."""
    if path == "/" or path in {"/health", "/help", "/about", "/docs", "/redoc", "/openapi.json"}:
        return True
    if path.startswith("/static") or path.startswith("/api"):
        return True
    if path.startswith("/tools/" + VUE_TOOL_ID):
        return True
    if path.startswith("/config") or path.startswith("/logs"):
        return True
    return False


def public_href(path: str, *, shell: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if shell != SHELL_VUE:
        return path
    if path.startswith("/static"):
        return path
    if path == "/":
        return VUE_PREFIX + "/"
    return VUE_PREFIX + path


def prefix_redirect_location(location: str) -> str:
    """Keep 303s inside /vue when the request was for the analytics product."""
    if not location:
        return location
    parts = urlsplit(location)
    path = parts.path or "/"
    if path.startswith("/static") or path.startswith("/docs"):
        return location
    if path == VUE_PREFIX or path.startswith(VUE_PREFIX + "/"):
        return location
    if not path.startswith("/"):
        return location
    new_path = VUE_PREFIX + ("/" if path == "/" else path)
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def tools_exclude(shell: str) -> frozenset[str]:
    if shell == SHELL_VUE:
        return frozenset(
            tool.id for tool in list_tools() if tool.id != VUE_TOOL_ID
        )
    return frozenset({VUE_TOOL_ID})


def tools_for_shell(shell: str) -> list[BaseTool]:
    return list_tools(exclude=tools_exclude(shell))


def tool_groups_for_shell(shell: str) -> list[tuple[str, list[BaseTool]]]:
    return list_tools_by_category(exclude=tools_exclude(shell))
