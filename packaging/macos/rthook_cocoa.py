"""Mark the frozen Mac app as a regular GUI process before pywebview starts."""

from __future__ import annotations

import sys

if sys.platform == "darwin":
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyRegular

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyRegular
        )
    except Exception:
        pass
