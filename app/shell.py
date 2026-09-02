"""Three products from one process: Dicommunication, Dicomtag Analytics, and
Dicom Anonymizer.

The MSI / DMG still freeze a single executable. Dicommunication is the
workstation (PING, DIMSE, HL7, worklist). Dicomtag Analytics is the
Study Root C-FIND UI that used to live under Test tools as C-FIND Advanced.
Dicom Anonymizer queries, retrieves, and anonymizes studies/series/images.

All three share config, logs, and the local server. Dicomtag Analytics is
mounted at ``/vue/`` and Dicom Anonymizer at ``/anonymize/`` so every window
can stay open against one uvicorn. Each Start-menu shortcut passes its own
``--profile``. ``vue-analytics`` is still accepted as Dicomtag Analytics'
previous profile name.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from app.tools import list_tools, list_tools_by_category
from app.tools.base import BaseTool

SHELL_DICOMM = "dicommunication"
SHELL_VUE = "vue"
SHELL_ANONYMIZE = "anonymize"

PRODUCT_DICOMM = "Dicommunication"
PRODUCT_ANALYTICS = "Dicomtag Analytics"
PRODUCT_ANONYMIZER = "Dicom Anonymizer"

PROFILE_DICOMM = "dicommunication"
PROFILE_VUE = "dicomtag-analytics"
PROFILE_VUE_LEGACY = "vue-analytics"
PROFILE_ANONYMIZER = "dicom-anonymizer"
PROFILES = (PROFILE_DICOMM, PROFILE_VUE, PROFILE_VUE_LEGACY, PROFILE_ANONYMIZER)

VUE_PREFIX = "/vue"
VUE_TOOL_ID = "c-find-advanced"
ANONYMIZE_PREFIX = "/anonymize"
ANONYMIZE_TOOL_ID = "anonymize"

# Every tool that gets its own single-tool shell/prefix — hidden from the
# main Dicommunication shell's own tool list, same as it always hid c-find-advanced.
SINGLE_TOOL_IDS = frozenset({VUE_TOOL_ID, ANONYMIZE_TOOL_ID})

PRODUCT_NAMES = {
    SHELL_DICOMM: PRODUCT_DICOMM,
    SHELL_VUE: PRODUCT_ANALYTICS,
    SHELL_ANONYMIZE: PRODUCT_ANONYMIZER,
}

WINDOW_TITLES = {
    PROFILE_DICOMM: PRODUCT_DICOMM,
    PROFILE_VUE: PRODUCT_ANALYTICS,
    PROFILE_VUE_LEGACY: PRODUCT_ANALYTICS,
    PROFILE_ANONYMIZER: PRODUCT_ANONYMIZER,
}


def is_analytics_profile(profile: str) -> bool:
    return profile in (PROFILE_VUE, PROFILE_VUE_LEGACY)


def is_anonymizer_profile(profile: str) -> bool:
    return profile == PROFILE_ANONYMIZER


LEGACY_ANALYTICS_NAMES = frozenset(
    {
        "Vue PACS Database Analytics",
        "C-FIND Advanced",
    }
)


def display_tool_name(name: str) -> str:
    """Show the current product name for stored results from earlier titles."""
    if name in LEGACY_ANALYTICS_NAMES:
        return PRODUCT_ANALYTICS
    return name


def profile_start_path(profile: str) -> str:
    if is_analytics_profile(profile):
        return VUE_PREFIX + "/"
    if is_anonymizer_profile(profile):
        return ANONYMIZE_PREFIX + "/"
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
    return _single_tool_path_allowed(path, VUE_TOOL_ID)


def is_anonymize_public_path(path: str) -> bool:
    return path == ANONYMIZE_PREFIX or path.startswith(ANONYMIZE_PREFIX + "/")


def strip_anonymize_prefix(path: str) -> str:
    if path == ANONYMIZE_PREFIX:
        return "/"
    if path.startswith(ANONYMIZE_PREFIX + "/"):
        stripped = path[len(ANONYMIZE_PREFIX) :]
        return stripped or "/"
    return path


def anonymize_path_allowed(path: str) -> bool:
    """Pages the anonymizer product may show after the /anonymize prefix is stripped."""
    return _single_tool_path_allowed(path, ANONYMIZE_TOOL_ID)


def _single_tool_path_allowed(path: str, tool_id: str) -> bool:
    if path == "/" or path in {"/health", "/help", "/about", "/docs", "/redoc", "/openapi.json"}:
        return True
    if path.startswith("/static") or path.startswith("/api"):
        return True
    if path.startswith("/tools/" + tool_id):
        return True
    if path.startswith("/config") or path.startswith("/logs"):
        return True
    return False


_SHELL_PREFIXES = {SHELL_VUE: VUE_PREFIX, SHELL_ANONYMIZE: ANONYMIZE_PREFIX}


def public_href(path: str, *, shell: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    prefix = _SHELL_PREFIXES.get(shell)
    if prefix is None:
        return path
    if path.startswith("/static"):
        return path
    if path == "/":
        return prefix + "/"
    return prefix + path


def prefix_redirect_location(location: str, *, prefix: str = VUE_PREFIX) -> str:
    """Keep a 303 inside the given shell prefix when the original request was."""
    if not location:
        return location
    parts = urlsplit(location)
    path = parts.path or "/"
    if path.startswith("/static") or path.startswith("/docs"):
        return location
    if path == prefix or path.startswith(prefix + "/"):
        return location
    if not path.startswith("/"):
        return location
    new_path = prefix + ("/" if path == "/" else path)
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def tools_exclude(shell: str) -> frozenset[str]:
    if shell == SHELL_VUE:
        return frozenset(tool.id for tool in list_tools() if tool.id != VUE_TOOL_ID)
    if shell == SHELL_ANONYMIZE:
        return frozenset(tool.id for tool in list_tools() if tool.id != ANONYMIZE_TOOL_ID)
    return SINGLE_TOOL_IDS


def tools_for_shell(shell: str) -> list[BaseTool]:
    return list_tools(exclude=tools_exclude(shell))


def tool_groups_for_shell(shell: str) -> list[tuple[str, list[BaseTool]]]:
    return list_tools_by_category(exclude=tools_exclude(shell))
