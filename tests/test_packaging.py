from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from app.launcher import apply_runtime_env, main, windows_data_dir
from app.paths import package_dir
from app.store import _default_data_dir
from app.tools.ping import icmp_argv
from app.tools.registry import BUILTIN_TOOL_MODULES

ROOT = Path(__file__).resolve().parents[1]


def _load_harvest():
    spec = importlib.util.spec_from_file_location(
        "windows_harvest",
        ROOT / "packaging" / "windows" / "harvest.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_icmp_argv_posix(monkeypatch) -> None:
    monkeypatch.setattr("app.tools.ping.os.name", "posix")
    assert icmp_argv("/bin/ping", "10.1.2.3", 3, 10) == [
        "/bin/ping",
        "-c",
        "3",
        "-W",
        "10",
        "10.1.2.3",
    ]


def test_icmp_argv_windows(monkeypatch) -> None:
    monkeypatch.setattr("app.tools.ping.os.name", "nt")
    assert icmp_argv("ping.exe", "10.1.2.3", 3, 2.5) == [
        "ping.exe",
        "-n",
        "3",
        "-w",
        "2500",
        "10.1.2.3",
    ]


def test_package_dir_source() -> None:
    assert package_dir() == ROOT / "app"
    assert (package_dir() / "templates" / "base.html").is_file()
    assert (package_dir() / "static" / "css" / "app.css").is_file()


def test_package_dir_frozen(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert package_dir() == tmp_path / "app"


def test_default_data_dir_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.store.os.name", "nt")
    monkeypatch.delenv("DICOMM_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert _default_data_dir() == tmp_path / "dicommunication"


def test_default_data_dir_env_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DICOMM_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.setattr("app.store.os.name", "nt")
    assert _default_data_dir() == tmp_path / "custom"


def test_apply_runtime_env_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.launcher.os.name", "nt")
    monkeypatch.delenv("DICOMM_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    apply_runtime_env()
    assert Path(os.environ["DICOMM_DATA_DIR"]) == tmp_path / "dicommunication"
    assert windows_data_dir() == tmp_path / "dicommunication"


def test_apply_runtime_env_posix_leaves_unset(monkeypatch) -> None:
    monkeypatch.setattr("app.launcher.os.name", "posix")
    monkeypatch.delenv("DICOMM_DATA_DIR", raising=False)
    apply_runtime_env()
    assert "DICOMM_DATA_DIR" not in os.environ


def test_launcher_reuses_running_server(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("app.launcher.apply_runtime_env", lambda: None)
    monkeypatch.setattr("app.launcher.server_is_up", lambda url, timeout=0.4: True)
    monkeypatch.setattr("app.launcher.webbrowser.open", lambda url: opened.append(url))
    assert main(["--host", "127.0.0.1", "--port", "8080"]) == 0
    assert opened == ["http://127.0.0.1:8080/"]


def test_launcher_no_browser_on_existing_server(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("app.launcher.apply_runtime_env", lambda: None)
    monkeypatch.setattr("app.launcher.server_is_up", lambda url, timeout=0.4: True)
    monkeypatch.setattr("app.launcher.webbrowser.open", lambda url: opened.append(url))
    assert main(["--no-browser"]) == 0
    assert opened == []


def test_launcher_help() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_builtin_tool_modules_match_source() -> None:
    found = {
        path.stem
        for path in (ROOT / "app" / "tools").glob("*.py")
        if path.name not in {"__init__.py", "base.py", "registry.py"}
    }
    assert set(BUILTIN_TOOL_MODULES) == found


def test_harvest_wix_fragment(tmp_path) -> None:
    dist = tmp_path / "dicommunication"
    nested = dist / "_internal" / "uv"
    nested.mkdir(parents=True)
    (dist / "dicommunication.exe").write_bytes(b"mz")
    (nested / "payload.dll").write_bytes(b"dll")
    harvest = _load_harvest()
    xml = harvest.harvest(dist)
    assert 'ComponentGroup Id="AppFiles"' in xml
    assert "dicommunication.exe" in xml
    assert "_internal\\uv\\payload.dll" in xml
    assert xml.count("<Component ") == 2
    out = tmp_path / "harvested.wxs"
    assert harvest.main([str(dist), str(out)]) == 0
    assert out.read_text(encoding="utf-8") == xml
