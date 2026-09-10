"""One application: Dicommunication.

Everything that used to be five separate windows sharing one process —
Dicommunication itself, Dicomtag Analytics, Dicom Anonymizer, Dicom Router,
and Dicom Cleaner — is now reachable from the single main window's own
navigation. There's exactly one Start-menu shortcut / .app bundle / uvicorn
process; every tool page lives at its plain path (``/tools/<id>``, or
``/router`` for Dicom Router, which isn't a `BaseTool`).

Phase 2 groups that navigation into a top tab row, one tab per product plus
a shared Configuration tab — see TAB_* below. `active_tab()` maps a page's
existing `nav` (and, for generic tool pages, `tool_id`) to the tab it
belongs under, so call sites that already pass `nav`/`tool_id` to `page()`
don't need to change.
"""

from __future__ import annotations

PRODUCT_DICOMM = "Dicommunication"
PRODUCT_ANALYTICS = "Dicomtag Analytics"

# Accepted, but ignored, by `--profile` for backward compatibility with
# shortcuts/scripts from before the five products merged into one window.
PROFILE_DICOMM = "dicommunication"
PROFILES = (
    PROFILE_DICOMM,
    "dicomtag-analytics",
    "vue-analytics",
    "dicom-anonymizer",
    "dicom-router",
    "dicom-cleaner",
)

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


def profile_start_path(profile: str) -> str:  # noqa: ARG001 — kept for launcher.py's call site
    return "/"


def profile_window_title(profile: str) -> str:  # noqa: ARG001 — kept for launcher.py's call site
    return PRODUCT_DICOMM


# ---------------------------------------------------------------------------
# Tabs (Phase 2)
# ---------------------------------------------------------------------------

TAB_DICOMM = "dicommunication"
TAB_ANALYTICS = "analytics"
TAB_ANONYMIZER = "anonymizer"
TAB_ROUTER = "router"
TAB_CLEANER = "cleaner"
TAB_CONFIG = "config"

TAB_ORDER = (TAB_DICOMM, TAB_ANALYTICS, TAB_ANONYMIZER, TAB_ROUTER, TAB_CLEANER, TAB_CONFIG)

TAB_LABELS = {
    TAB_DICOMM: PRODUCT_DICOMM,
    TAB_ANALYTICS: PRODUCT_ANALYTICS,
    TAB_ANONYMIZER: "Dicom Anonymizer",
    TAB_ROUTER: "Dicom Router",
    TAB_CLEANER: "Dicom Cleaner",
    TAB_CONFIG: "Configuration",
}

# Where each tab's own link in the tab row goes.
TAB_HREFS = {
    TAB_DICOMM: "/",
    TAB_ANALYTICS: "/tools/c-find-advanced",
    TAB_ANONYMIZER: "/tools/anonymize",
    TAB_ROUTER: "/router",
    TAB_CLEANER: "/tools/dicom-cleaner",
    TAB_CONFIG: "/config",
}

# These three used to be the "single-tool shell" products; they're full tabs
# now, so the Dicommunication tab's own Test tools list (and its "registered
# tools" summary on the dashboard) no longer lists them — each has its own
# tab-level home instead.
PROMOTED_TAB_TOOL_IDS = frozenset({"c-find-advanced", "anonymize", "dicom-cleaner"})

_CONFIG_NAV_VALUES = frozenset({"config", "config-local", "config-identities", "config-remotes", "logs"})

_TOOL_ID_TABS = {
    "c-find-advanced": TAB_ANALYTICS,
    "anonymize": TAB_ANONYMIZER,
    "dicom-cleaner": TAB_CLEANER,
}


def active_tab(nav: str, tool_id: str | None = None) -> str:
    """Which tab a page belongs under, from the `nav`/`tool_id` it already
    passes to `page()`. Pages that aren't part of any product in particular
    (About, Help) fall back to the Dicommunication tab rather than leaving
    the tab row with nothing selected.
    """
    if nav in _CONFIG_NAV_VALUES:
        return TAB_CONFIG
    if nav == "router":
        return TAB_ROUTER
    if nav == "tools" and tool_id in _TOOL_ID_TABS:
        return _TOOL_ID_TABS[tool_id]
    return TAB_DICOMM
