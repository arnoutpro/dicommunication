"""The desktop window's native Save dialog bridge (DesktopApi.save_file)."""

import sys
import types

import pytest

from app.desktop import DesktopApi


def _fake_webview(monkeypatch, picked):
    calls = {}

    class Window:
        def create_file_dialog(self, dialog_type, **kwargs):
            calls["kwargs"] = kwargs
            return picked

    module = types.SimpleNamespace(windows=[Window()], SAVE_DIALOG=30, FileDialog=None)
    monkeypatch.setitem(sys.modules, "webview", module)
    return calls


def test_save_file_writes_to_the_chosen_path(monkeypatch, tmp_path) -> None:
    target = tmp_path / "chosen.csv"
    calls = _fake_webview(monkeypatch, (str(target),))
    result = DesktopApi().save_file("export.csv", "a,b\n1,2\n")
    assert result == {"ok": True, "path": str(target)}
    assert target.read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert calls["kwargs"]["save_filename"] == "export.csv"


def test_save_file_cancelled_writes_nothing(monkeypatch) -> None:
    _fake_webview(monkeypatch, None)
    assert DesktopApi().save_file("export.json", "{}") == {"ok": False, "cancelled": True}


def test_save_file_strips_directories_from_the_suggested_name(monkeypatch, tmp_path) -> None:
    calls = _fake_webview(monkeypatch, str(tmp_path / "x.csv"))
    DesktopApi().save_file("../../etc/passwd", "x")
    assert calls["kwargs"]["save_filename"] == "passwd"
