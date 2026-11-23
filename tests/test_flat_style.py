"""The flat, dense style (0.14): square corners, thin lines, small system text."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def _token(name: str) -> str:
    return re.search(rf"^\s*{re.escape(name)}:\s*([^;]+);", CSS, re.M).group(1).strip()


def test_every_corner_is_square() -> None:
    for name in ("--radius-xs", "--radius-sm", "--radius", "--radius-lg"):
        assert _token(name) == "0", name
    assert "999px" not in CSS  # no pills
    radii = set(re.findall(r"border-radius:\s*([^;]+);", CSS))
    assert radii <= {"0", "var(--radius)", "var(--radius-sm)", "var(--radius-xs)", "var(--radius-lg)", "inherit"}, radii


def test_nothing_glows_blurs_or_casts_a_shadow() -> None:
    assert "backdrop-filter" not in CSS and "-webkit-backdrop-filter" not in CSS
    assert not re.search(r"box-shadow:(?!\s*none)", CSS), "a box-shadow came back"
    assert "gradient(" not in CSS


def test_the_text_is_small_and_uses_the_system_font() -> None:
    assert _token("--text-base") == "0.8125rem"  # 13px
    assert _token("--text-md") == "0.8125rem"
    assert _token("--text-2xl") == "1.125rem"  # 18px page title, not 28
    assert "Plus Jakarta" not in CSS and "@font-face" not in CSS
    assert "Segoe UI" in _token("--font-ui") and "system-ui" in _token("--font-ui")
    assert not (ROOT / "app" / "static" / "fonts").exists()  # nothing bundled


def test_pages_have_no_icon_tile_and_the_accent_is_not_indigo() -> None:
    block = CSS[CSS.index(".page-icon-tile {") : CSS.index("}", CSS.index(".page-icon-tile {"))]
    assert "display: none" in block and "display: flex" not in block
    for indigo in ("#4338ca", "#818cf8", "#a5b4fc", "129 140 248", "67 56 202"):
        assert indigo not in CSS, indigo


def test_the_status_dashboard_for_viewers_is_flat_too() -> None:
    lite = (ROOT / "app" / "static" / "css" / "lite.css").read_text(encoding="utf-8")
    assert "999px" not in lite and not re.search(r"border-radius:\s*[1-9]", lite)
    assert "font:13px" in lite and "font-size:28px" not in lite
