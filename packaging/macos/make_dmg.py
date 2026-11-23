"""Wrap the installer package in a UDZO DMG, with a short Read Me."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
from pathlib import Path

VOLUME_NAME = "Dicommunication"
README_NAME = "Read Me.txt"

README_TEXT = """Dicommunication - Arnout.pro

1. Open Dicommunication.pkg and follow the installer (it asks for an administrator
   password). The build is not signed, so the first time macOS may refuse it:
   right-click the package and choose Open, or allow it under System Settings >
   Privacy & Security.
2. Dicommunication installs as a background service: it starts with this Mac, needs
   nobody to be logged in, and has no window. Open http://localhost:8080/ in a
   browser. The first visit goes to Setup, where you create the first administrator.
   Sign-in is always required.
3. In Terminal, to manage it:
     sudo dicommunication service status
     sudo dicommunication service create-admin NAME
     sudo dicommunication service network on      (let other computers connect, over HTTPS)
     sudo dicommunication service port 8090
     sudo dicommunication service uninstall

Settings and history live in /Library/Application Support/Dicommunication, and can
contain patient data. An old Dicommunication.app (versions up to 0.12) is replaced by
this installation, and its settings are not carried over: export them first
(Configuration > Import / export), then import them after installing.

If a modality must C-FIND this Mac, allow incoming connections for the listen port
(11112) in the macOS firewall.
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


def stage_package(pkg: Path, staging: Path) -> Path:
    """Copy the installer package into *staging* next to a short readme."""
    pkg = pkg.resolve()
    if pkg.suffix != ".pkg" or not pkg.is_file():
        raise SystemExit(f"installer package not found: {pkg}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copyfile(pkg, staging / "Dicommunication.pkg")
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
    parser = argparse.ArgumentParser(description="Wrap the installer package in a UDZO DMG.")
    parser.add_argument("pkg", type=Path, help="Path to the .pkg made by make_pkg.py")
    parser.add_argument("--version", required=True, help="Version string in the DMG filename")
    parser.add_argument("--arch", default=None, help="Architecture label (arm64 or x86_64)")
    parser.add_argument("--output", type=Path, default=Path("dist"), help="Directory for the DMG")
    parser.add_argument("--stage-dir", type=Path, default=None, help="Staging folder (default: <output>/dmg-staging)")
    parser.add_argument("--stage-only", action="store_true", help="Copy the files without calling hdiutil (for tests / Linux).")
    args = parser.parse_args(argv)

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = args.stage_dir or (output_dir / "dmg-staging")
    stage_package(args.pkg, staging)

    if args.stage_only:
        print(f"Staged {staging}", flush=True)
        return 0

    dmg = output_dir / dmg_filename(args.version, args.arch)
    create_dmg(staging, dmg)
    print(f"Built {dmg}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
