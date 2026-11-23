"""The Windows headless edition's setup step (app/service_setup.py)."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app import service_setup as ss
from app.launcher import main


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_validate_port() -> None:
    assert ss.validate_port("8080") == 8080
    assert ss.validate_port(" 1024 ") == 1024
    assert ss.validate_port(65535) == 65535
    for bad in ("", "abc", "80", "1023", "65536", "-5", "80.5"):
        with pytest.raises(ValueError):
            ss.validate_port(bad)


def test_listen_host() -> None:
    assert ss.listen_host("local") == "127.0.0.1"
    assert ss.listen_host("network") == "0.0.0.0"
    with pytest.raises(ValueError):
        ss.listen_host("everywhere")


def test_service_xml_for_this_computer_only() -> None:
    root = _parse(ss.render_service_xml(port=8090, listen="local", data_dir=r"C:\ProgramData\Dicommunication"))
    assert root.findtext("id") == "Dicommunication"
    assert root.findtext("executable") == "%BASE%\\dicommunication.exe"
    assert root.findtext("arguments") == "--no-browser --host 127.0.0.1 --port 8090"
    env = {e.get("name"): e.get("value") for e in root.findall("env")}
    assert env == {"DICOMM_DATA_DIR": r"C:\ProgramData\Dicommunication", "DICOMM_REQUIRE_LOGIN": "1"}  # sign-in is on even for "this computer only"
    assert root.findtext("logpath") == r"C:\ProgramData\Dicommunication\logs"


def test_service_xml_for_the_network_names_the_computer() -> None:
    root = _parse(
        ss.render_service_xml(port=8080, listen="network", data_dir=r"C:\ProgramData\Dicommunication", hostnames=["pacs-pc", "pacs-pc.lan"])
    )
    assert root.findtext("arguments") == "--no-browser --host 0.0.0.0 --port 8080"
    env = {e.get("name"): e.get("value") for e in root.findall("env")}
    assert env["DICOMM_ALLOWED_HOSTS"] == "pacs-pc,pacs-pc.lan"


def test_service_xml_escapes_and_rejects_a_bad_port() -> None:
    root = _parse(ss.render_service_xml(port=8080, listen="local", data_dir=r"C:\Data & <More>"))
    assert {e.get("name"): e.get("value") for e in root.findall("env")}["DICOMM_DATA_DIR"] == r"C:\Data & <More>"
    with pytest.raises(ValueError):
        ss.render_service_xml(port=80, listen="local", data_dir="C:\\x")


def test_configure_writes_the_settings_and_folders(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(ss, "runtime_os_name", lambda: "nt")
    monkeypatch.setattr(
        ss.subprocess, "run", lambda argv, **kw: (calls.append(argv), subprocess.CompletedProcess(argv, 0))[1]
    )
    install, data = tmp_path / "install", tmp_path / "data"
    install.mkdir()
    assert ss.configure_service(port="8123", listen="local", install_dir=install, data_dir=data) == 0
    xml = _parse((install / ss.SERVICE_XML_NAME).read_text(encoding="utf-8"))
    assert xml.findtext("arguments") == "--no-browser --host 127.0.0.1 --port 8123"
    assert (data / "logs").is_dir()
    assert "configured: port 8123" in (data / ss.SETUP_LOG_NAME).read_text()
    # Access is limited to SYSTEM, administrators and the service account, by SID.
    argv = calls[0]
    assert argv[0] == "icacls" and "/inheritance:r" in argv
    assert any(a.startswith("*S-1-5-18") for a in argv) and any(a.startswith("*S-1-5-32-544") for a in argv)
    assert any(a.startswith("*S-1-5-19") for a in argv)
    # Only the grants matter here: the folder path itself may contain "Users" (C:\Users\...).
    grants = argv[argv.index("/grant:r") + 1 :]
    assert len(grants) == 3
    assert not any("Users" in a or "Everyone" in a for a in grants)


def test_a_failed_permission_change_is_only_a_warning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ss, "runtime_os_name", lambda: "nt")
    monkeypatch.setattr(ss.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 5))
    install, data = tmp_path / "install", tmp_path / "data"
    install.mkdir()
    assert ss.configure_service(port=8080, listen="local", install_dir=install, data_dir=data) == 0
    assert "could not restrict" in (data / ss.SETUP_LOG_NAME).read_text()
    assert (install / ss.SERVICE_XML_NAME).exists()


def test_a_bad_port_fails_setup_and_says_why(tmp_path: Path) -> None:
    install, data = tmp_path / "install", tmp_path / "data"
    install.mkdir()
    assert ss.configure_service(port="80", listen="local", install_dir=install, data_dir=data) == 2
    assert not (install / ss.SERVICE_XML_NAME).exists()
    assert "between 1024 and 65535" in (data / ss.SETUP_LOG_NAME).read_text()


def test_missing_install_folder_fails_setup(tmp_path: Path) -> None:
    assert ss.configure_service(port=8080, listen="local", install_dir=tmp_path / "nope", data_dir=tmp_path / "data") == 4


def test_launcher_runs_the_setup_step(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(ss, "run_configure", lambda port, listen: seen.update(port=port, listen=listen) or 0)
    assert main(["--configure-service", "--port", "8090", "--listen", "network"]) == 0
    assert seen == {"port": 8090, "listen": "network"}


def test_uninstall_cleanup_for_the_service_targets_programdata(monkeypatch, tmp_path: Path) -> None:
    import app.uninstall as un

    purged: list[Path | None] = []
    monkeypatch.setattr(un, "confirm_purge_data", lambda: True)
    monkeypatch.setattr(un, "purge_data_dir", lambda path=None: purged.append(path))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    assert un.run_uninstall_cleanup(service=True) == 0
    assert purged == [tmp_path / "Dicommunication"]
    purged.clear()
    assert un.run_uninstall_cleanup() == 0
    assert purged == [None]


# --- packaging: the headless edition's installer pieces -----------------------

import hashlib
import importlib.util
import io
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def _fetch_winsw():
    spec = importlib.util.spec_from_file_location("fetch_winsw", ROOT / "packaging" / "windows" / "fetch_winsw.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_nupkg(exe: bytes = b"MZ-fake") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("lib/net461-full/WinSW.NET461.exe", exe)
        archive.writestr("lib/net40-full/WinSW.NET4.exe", b"other")
    return buffer.getvalue()


def test_winsw_is_staged_only_when_the_checksum_matches(tmp_path: Path) -> None:
    fetch = _fetch_winsw()
    package = _fake_nupkg(b"MZ-the-real-one")
    target = fetch.stage(tmp_path / "out", nupkg=package, expected_sha256=hashlib.sha256(package).hexdigest())
    assert target.name == "dicommunication-service.exe" and target.read_bytes() == b"MZ-the-real-one"
    with pytest.raises(ValueError, match="checksum mismatch"):
        fetch.stage(tmp_path / "bad", nupkg=package)  # not the pinned package
    assert not (tmp_path / "bad").exists()


def test_winsw_package_without_the_expected_exe_is_refused() -> None:
    fetch = _fetch_winsw()
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "nothing")
    with pytest.raises(ValueError, match="not found"):
        fetch.extract_exe(empty.getvalue())


def test_winsw_pin_is_a_sha256_and_a_version() -> None:
    fetch = _fetch_winsw()
    assert re.fullmatch(r"[0-9a-f]{64}", fetch.WINSW_NUPKG_SHA256)
    assert fetch.WINSW_VERSION in fetch.WINSW_URL


def test_service_installer_replaces_the_desktop_one_and_matches_its_version_rules() -> None:
    product = (ROOT / "packaging" / "windows" / "Product.wxs").read_text(encoding="utf-8")
    service = (ROOT / "packaging" / "windows" / "Headless.wxs").read_text(encoding="utf-8")
    code = re.search(r'UpgradeCode="([^"]+)"', product).group(1)
    assert f'UpgradeCode="{code}"' in service  # installing one replaces the other
    from app import __version__

    for text in (product, service):
        assert f'<?define ProductVersion = "{__version__}" ?>' in text
    assert 'AllowSameVersionUpgrades="yes"' in service
    assert "AppFiles" in service  # the same harvested program files


def test_service_installer_settings_agree_with_the_setup_step() -> None:
    service = (ROOT / "packaging" / "windows" / "Headless.wxs").read_text(encoding="utf-8")
    assert f'Name="{ss.SERVICE_NAME}"' in service
    assert ss.SERVICE_XML_NAME in service
    assert f"--port [PORT] --listen {ss.LISTEN_LOCAL}" in service and f"--port [PORT] --listen {ss.LISTEN_NETWORK}" in service
    assert 'Account="NT AUTHORITY\\LocalService"' in service
    assert str(ss.DEFAULT_PORT) in re.search(r'<Property Id="PORT" Value="([^"]*)"', service).group(1)
    # No double hyphen inside an XML comment: WiX refuses to read the file.
    for comment in re.findall(r"<!--(.*?)-->", service, flags=re.S):
        assert "--" not in comment


def test_workflow_builds_and_publishes_the_service_msi() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows-msi.yml").read_text(encoding="utf-8")
    assert "fetch_winsw.py" in workflow and "Headless.wxs" in workflow
    assert "dicommunication-headless-" in workflow and "dicommunication-service-" not in workflow
    # The desktop artifact must not pick the headless MSI up by accident.
    assert "dist/dicommunication-[0-9]*-win64.msi" in workflow
