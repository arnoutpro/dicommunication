"""Every tracked file must be checkable on Windows, where the MSI is built.

A stray file named like ``C:/status/x.json`` (made by running a Router rule whose
export folder is a Windows path while testing on Linux) once broke the build at
checkout, before a single step ran.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


def _tracked() -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [name for name in out.decode("utf-8").split("\0") if name]


def test_no_tracked_path_is_invalid_on_windows() -> None:
    bad = []
    for name in _tracked():
        for part in name.split("/"):
            stem = part.split(".")[0].upper()
            if INVALID.search(part) or part.endswith((" ", ".")) or stem in RESERVED:
                bad.append(name)
                break
    assert not bad, f"These tracked paths cannot be checked out on Windows: {bad}"


def test_the_weekly_dependency_check_is_in_place() -> None:
    text = (ROOT / ".github" / "workflows" / "dependency-check.yml").read_text(encoding="utf-8")
    assert "schedule:" in text and "cron:" in text and "workflow_dispatch:" in text
    assert "pip-audit --local" in text and "-r requirements-desktop.txt" in text  # audits what a build installs, desktop extras included
    assert "windows-latest" in text and "python -m pytest" in text
    assert re.search(r"permissions:\s+contents: read", text)  # it never needs to write
