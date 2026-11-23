"""Build the macOS installer package: the program plus a launchd service, wrapped for the DMG.

Layout installed on the Mac (see app/macos_service.py for what the post-install step adds):

    /usr/local/lib/dicommunication/    the frozen program (PyInstaller onedir)
    /usr/local/bin/dicommunication     link to it, so `sudo dicommunication service ...` works

Everything that depends on the chosen port and network option (the launchd job, the
data folder, the shortcut) is written by the post-install script, not shipped here.
``pkgbuild`` and ``productbuild`` only exist on macOS; ``--stage-only`` lays out the
payload, scripts and installer resources without them, for tests and for Linux.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
IDENTIFIER = "pro.arnout.dicommunication"
PROGRAM_DIR = "usr/local/lib/dicommunication"
LINK = "usr/local/bin/dicommunication"
RESOURCES = ("welcome.html", "conclusion.html")
LICENSE = HERE.parent / "windows" / "License.rtf"
SCRIPTS = ("preinstall", "postinstall")


def normalize_arch(raw: str | None = None) -> str:
    machine = (raw or platform.machine()).lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    return machine or "unknown"


def pkg_filename(version: str, arch: str | None = None) -> str:
    return f"dicommunication-{version}-macos-{normalize_arch(arch)}.pkg"


def distribution_xml(version: str, arch: str) -> str:
    """The installer's script: one hidden choice, the system volume only, one architecture."""
    host = {"arm64": "arm64", "x86_64": "x86_64"}.get(arch, "")
    options = f'<options customize="never" require-scripts="false"{f" hostArchitectures={chr(34)}{host}{chr(34)}" if host else ""} />'
    return f"""<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>Dicommunication</title>
    <welcome file="welcome.html" />
    <license file="License.rtf" />
    <conclusion file="conclusion.html" />
    {options}
    <domains enable_anywhere="false" enable_currentUserHome="false" enable_localSystem="true" />
    <choices-outline>
        <line choice="{IDENTIFIER}" />
    </choices-outline>
    <choice id="{IDENTIFIER}" title="Dicommunication" visible="false">
        <pkg-ref id="{IDENTIFIER}" />
    </choice>
    <pkg-ref id="{IDENTIFIER}" version="{escape(version)}" onConclusion="none">component.pkg</pkg-ref>
</installer-gui-script>
"""


def stage(onedir: Path, staging: Path, version: str, arch: str) -> dict[str, Path]:
    """Lay out payload/, scripts/, resources/ and distribution.xml under ``staging``."""
    exe = onedir / "dicommunication"
    if not onedir.is_dir() or not exe.is_file():
        raise SystemExit(f"frozen program not found: {exe}")
    if staging.exists():
        shutil.rmtree(staging)
    payload, scripts, resources = staging / "payload", staging / "scripts", staging / "resources"
    shutil.copytree(onedir, payload / PROGRAM_DIR, symlinks=True)
    link = payload / LINK
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("../lib/dicommunication/dicommunication")
    scripts.mkdir(parents=True)
    for name in SCRIPTS:
        target = scripts / name
        shutil.copyfile(HERE / "scripts" / name, target)
        target.chmod(0o755)
    resources.mkdir(parents=True)
    for name in RESOURCES:
        shutil.copyfile(HERE / "resources" / name, resources / name)
    shutil.copyfile(LICENSE, resources / "License.rtf")
    distribution = staging / "distribution.xml"
    distribution.write_text(distribution_xml(version, arch), encoding="utf-8")
    return {"payload": payload, "scripts": scripts, "resources": resources, "distribution": distribution}


def build(parts: dict[str, Path], staging: Path, output: Path, version: str) -> Path:
    """Run pkgbuild and productbuild (macOS only)."""
    component = staging / "component.pkg"
    subprocess.run(
        [
            "pkgbuild",
            "--root", str(parts["payload"]),
            "--scripts", str(parts["scripts"]),
            "--identifier", IDENTIFIER,
            "--version", version,
            "--install-location", "/",
            "--ownership", "recommended",
            str(component),
        ],
        check=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    subprocess.run(
        [
            "productbuild",
            "--distribution", str(parts["distribution"]),
            "--resources", str(parts["resources"]),
            "--package-path", str(staging),
            str(output),
        ],
        check=True,
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Dicommunication installer package (macOS).")
    parser.add_argument("onedir", type=Path, help="The PyInstaller output folder (dist/dicommunication)")
    parser.add_argument("--version", required=True)
    parser.add_argument("--arch", default=None)
    parser.add_argument("--output", type=Path, default=Path("dist"), help="Directory for the .pkg")
    parser.add_argument("--stage-dir", type=Path, default=None)
    parser.add_argument("--stage-only", action="store_true", help="Lay out the files without pkgbuild/productbuild (tests, Linux).")
    args = parser.parse_args(argv)
    arch = normalize_arch(args.arch)
    staging = args.stage_dir or (args.output / "pkg-staging")
    parts = stage(args.onedir, staging, args.version, arch)
    if args.stage_only:
        print(f"Staged {staging}", flush=True)
        return 0
    pkg = build(parts, staging, args.output / pkg_filename(args.version, arch), args.version)
    print(f"Built {pkg}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
