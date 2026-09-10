"""One application: Dicommunication.

Everything that used to be five separate windows sharing one process —
Dicommunication itself, Dicomtag Analytics, Dicom Anonymizer, Dicom Router,
and Dicom Cleaner — is now reachable from the single main window's own
navigation. There's exactly one Start-menu shortcut / .app bundle / uvicorn
process; every tool page lives at its plain path (``/tools/<id>``, or
``/router`` for Dicom Router, which isn't a `BaseTool`).

This module now only carries what's left once the per-shell prefix/window
machinery is gone: the product name, and `display_tool_name` for renaming
results stored under an old product name before this merge.
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
