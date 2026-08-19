"""Stage a drag-to-Applications folder and wrap it in a UDZO DMG."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
from pathlib import Path

VOLUME_NAME = "Dicommunication"
APP_BUNDLE_NAME = "Dicommunication.app"
README_NAME = "Read Me.txt"

README_TEXT = """Dicommunication — Arnout.pro

1. Drag Dicommunication.app onto Applications.
2. The first time, right-click the app and choose Open (the build is unsigned;
   Gatekeeper will warn).
3. The default browser opens http://127.0.0.1:8080
4. Quit Dicommunication from the Dock to stop the server.

Config and logs stay in ~/.dicommunication across upgrades.

If a modality must C-FIND this workstation, allow incoming TCP for
Dicommunication (listen port 11112).
"""


def normalize_arch(raw: str | None = None) -> str:
    machine = (raw or platform.machine()).lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    return machine or "unknown"


def dmg_filename(version: str, arch: str | None = None) -> str:
    return f"dicommunication-{version}-macos-{normalize_arch(arch)}.dmg"


def stage_app(app_path: Path, staging: Path) -> Path:
    """Copy the .app into *staging* with an Applications symlink and a short readme."""
    app_path = app_path.resolve()
    if app_path.name != APP_BUNDLE_NAME:
        raise SystemExit(f"expected {APP_BUNDLE_NAME}, got {app_path.name}")
    if not app_path.is_dir():
        raise SystemExit(f"app bundle not found: {app_path}")

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    shutil.copytree(app_path, staging / APP_BUNDLE_NAME, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    (staging / README_NAME).write_text(README_TEXT, encoding="utf-8")
    return staging


def create_dmg(staging: Path, output: Path, volume_name: str = VOLUME_NAME) -> Path:
    """Wrap *staging* with hdiutil. Must run on macOS."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    cmd = [
        "hdiutil",
        "create",
        "-volname",
        volume_name,
        "-srcfolder",
        str(staging.resolve()),
        "-ov",
        "-format",
        "UDZO",
        str(output),
    ]
    subprocess.run(cmd, check=True)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage Dicommunication.app and create a UDZO DMG.",
    )
    parser.add_argument("app", type=Path, help="Path to Dicommunication.app")
    parser.add_argument("--version", required=True, help="Version string in the DMG filename")
    parser.add_argument("--arch", default=None, help="Architecture label (arm64 or x86_64)")
    parser.add_argument("--output", type=Path, default=Path("dist"), help="Directory for the DMG")
    parser.add_argument(
        "--stage-dir",
        type=Path,
        default=None,
        help="Staging folder (default: <output>/dmg-staging)",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="Copy the .app layout without calling hdiutil (for tests / Linux).",
    )
    args = parser.parse_args(argv)

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = args.stage_dir or (output_dir / "dmg-staging")
    stage_app(args.app, staging)

    if args.stage_only:
        print(f"Staged {staging}", flush=True)
        return 0

    dmg = output_dir / dmg_filename(args.version, args.arch)
    create_dmg(staging, dmg)
    print(f"Built {dmg}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
