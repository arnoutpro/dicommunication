"""The macOS installer package, its scripts and its CI (packaging/macos)."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import xml.dom.minidom
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAC = ROOT / "packaging" / "macos"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, MAC / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_pkg = _load("macos_make_pkg", "make_pkg.py")
make_dmg = _load("macos_make_dmg_2", "make_dmg.py")


def _fake_onedir(root: Path) -> Path:
    onedir = root / "dicommunication"
    (onedir / "_internal").mkdir(parents=True)
    (onedir / "dicommunication").write_text("#!/bin/sh\n", encoding="utf-8")
    (onedir / "_internal" / "lib.so").write_text("x", encoding="utf-8")
    return onedir


# --- the frozen program ----------------------------------------------------------


def test_the_spec_builds_a_console_program_without_the_window_libraries() -> None:
    spec = (MAC / "dicommunication.spec").read_text(encoding="utf-8")
    assert "console=True" in spec and "console=False" not in spec
    assert "BUNDLE(" not in spec  # no .app: it is a service
    assert "launcher.py" in spec and "app.icns" in spec
    assert "collect_data_files(\"webview\")" not in spec and "webview.platforms.cocoa" not in spec
    assert "rthook_cocoa" not in spec and not (MAC / "rthook_cocoa.py").exists()
    assert '"webview"' in spec.split("excludes=")[1]  # kept out of the bundle on purpose


# --- the package -----------------------------------------------------------------


def test_the_staged_package_has_the_program_the_link_the_scripts_and_the_resources(tmp_path) -> None:
    parts = make_pkg.stage(_fake_onedir(tmp_path), tmp_path / "stage", "0.13.0", "arm64")
    payload = parts["payload"]
    assert (payload / "usr/local/lib/dicommunication/dicommunication").is_file()
    assert (payload / "usr/local/lib/dicommunication/_internal/lib.so").is_file()
    link = payload / "usr/local/bin/dicommunication"
    assert link.is_symlink() and link.resolve() == (payload / "usr/local/lib/dicommunication/dicommunication").resolve()
    for name in ("preinstall", "postinstall"):
        script = parts["scripts"] / name
        assert script.is_file()
        if sys.platform != "win32":  # Windows has no execute bit; make_pkg runs on a Mac
            assert script.stat().st_mode & 0o111  # the installer only runs executable scripts
    for name in ("welcome.html", "conclusion.html", "License.rtf"):
        assert (parts["resources"] / name).is_file()
    assert "sign-in" in (ROOT / "packaging" / "windows" / "License.rtf").read_text(encoding="utf-8")


def test_staging_refuses_a_missing_program(tmp_path) -> None:
    with pytest.raises(SystemExit):
        make_pkg.stage(tmp_path / "nothing", tmp_path / "stage", "1", "arm64")


def test_the_distribution_script_is_valid_and_limits_the_install() -> None:
    text = make_pkg.distribution_xml("0.13.0", "arm64")
    document = xml.dom.minidom.parseString(text)  # raises if it is not well formed
    assert 'hostArchitectures="arm64"' in text and 'enable_localSystem="true"' in text and 'enable_anywhere="false"' in text
    assert document.getElementsByTagName("pkg-ref")[-1].getAttribute("version") == "0.13.0"
    assert 'file="welcome.html"' in text and 'file="conclusion.html"' in text and 'file="License.rtf"' in text
    assert "hostArchitectures" not in make_pkg.distribution_xml("0.13.0", "weird")


def test_package_and_disk_image_names() -> None:
    assert make_pkg.pkg_filename("0.13.0", "aarch64") == "dicommunication-0.13.0-macos-arm64.pkg"
    assert make_dmg.dmg_filename("0.13.0", "arm64") == "dicommunication-0.13.0-macos-arm64.dmg"


def test_the_stage_only_command_lays_out_the_package_and_the_disk_image(tmp_path) -> None:
    out = tmp_path / "dist"
    assert make_pkg.main([str(_fake_onedir(tmp_path)), "--version", "0.13.0", "--arch", "arm64", "--output", str(out), "--stage-only"]) == 0
    assert (out / "pkg-staging" / "distribution.xml").is_file()
    fake_pkg = tmp_path / "x.pkg"
    fake_pkg.write_bytes(b"pkg")
    assert make_dmg.main([str(fake_pkg), "--version", "0.13.0", "--arch", "arm64", "--output", str(out), "--stage-only"]) == 0
    staged = out / "dmg-staging"
    assert (staged / "Dicommunication.pkg").read_bytes() == b"pkg"
    readme = (staged / "Read Me.txt").read_text(encoding="utf-8")
    for word in ("background service", "Setup", "sudo dicommunication service", "/Library/Application Support/Dicommunication", "export them first"):
        assert word in readme, word
    with pytest.raises(SystemExit):
        make_dmg.stage_package(tmp_path / "missing.pkg", tmp_path / "s2")
    with pytest.raises(SystemExit):
        make_dmg.stage_package(tmp_path / "x.txt", tmp_path / "s3")


# --- the scripts -----------------------------------------------------------------


# On a Windows runner "bash" is the WSL stub, which cannot read these files; Linux and macOS check them.
@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32", reason="needs a real bash")
@pytest.mark.parametrize("path", ["scripts/preinstall", "scripts/postinstall", "build.sh", "smoke_test.sh"])
def test_the_shell_scripts_have_valid_syntax(path: str) -> None:
    assert subprocess.run(["bash", "-n", str(MAC / path)], capture_output=True).returncode == 0


def test_postinstall_creates_the_service_account_and_starts_the_service() -> None:
    text = (MAC / "scripts" / "postinstall").read_text(encoding="utf-8")
    assert 'SERVICE_USER="_dicommunication"' in text and "UserShell /usr/bin/false" in text and "IsHidden 1" in text
    assert "seq 300 399" in text  # the range macOS keeps for service accounts
    assert "--configure-service --from-saved --reload" in text  # keeps an upgrade's port and network option
    assert "exit $STATUS" in text and text.startswith("#!/bin/bash")


def test_preinstall_stops_the_service_and_replaces_only_our_own_old_app() -> None:
    text = (MAC / "scripts" / "preinstall").read_text(encoding="utf-8")
    assert "launchctl bootout" in text and text.rstrip().endswith("exit 0")  # never blocks the install
    assert "CFBundleIdentifier" in text and '"$ID" = "$LABEL"' in text  # another app of that name is left alone


def test_the_build_script_makes_the_package_then_the_disk_image_without_the_window_libraries() -> None:
    text = (MAC / "build.sh").read_text(encoding="utf-8")
    assert text.index("make_pkg.py") < text.index("make_dmg.py")
    assert "requirements-desktop.txt" not in text and "Dicommunication.app" not in text


def test_the_workflow_builds_uploads_then_smoke_tests_the_real_install() -> None:
    workflow = (ROOT / ".github" / "workflows" / "macos-dmg.yml").read_text(encoding="utf-8")
    assert "runs-on: macos-latest" in workflow and "requirements-desktop.txt" not in workflow
    assert workflow.index("Upload DMG") < workflow.index("smoke_test.sh")  # a failing test must not lose the artifact
    assert "dist/*.dmg" in workflow and "release_tag" in workflow
    smoke = (MAC / "smoke_test.sh").read_text(encoding="utf-8")
    for word in ("installer -pkg", "*/setup", "create-admin smoke", "service uninstall --delete-data", "0700", "id _dicommunication"):
        assert word in smoke, word
