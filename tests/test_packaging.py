from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

from app.launcher import (
    apply_runtime_env,
    keep_alive_hint,
    main,
    redirect_frozen_stdio,
    windows_data_dir,
)
from app.main import http_publish_note
from app.paths import package_dir
from app.store import _default_data_dir
from app.tools.ping import icmp_argv
from app.tools.registry import BUILTIN_TOOL_MODULES

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "packaging" / "windows" / filename,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_harvest():
    return _load_module("windows_harvest", "harvest.py")


def _load_pack_nuget():
    return _load_module("windows_pack_nuget", "pack_nuget.py")


def _load_make_dmg():
    spec = importlib.util.spec_from_file_location(
        "macos_make_dmg",
        ROOT / "packaging" / "macos" / "make_dmg.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_icmp_argv_posix(monkeypatch) -> None:
    monkeypatch.setattr("app.tools.ping.runtime_os_name", lambda: "posix")
    assert icmp_argv("/bin/ping", "10.1.2.3", 3, 10) == [
        "/bin/ping",
        "-c",
        "3",
        "-W",
        "10",
        "10.1.2.3",
    ]


def test_icmp_argv_windows(monkeypatch) -> None:
    monkeypatch.setattr("app.tools.ping.runtime_os_name", lambda: "nt")
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
    monkeypatch.setattr("app.store.runtime_os_name", lambda: "nt")
    monkeypatch.delenv("DICOMM_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert _default_data_dir() == tmp_path / "dicommunication"


def test_default_data_dir_env_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DICOMM_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.setattr("app.store.runtime_os_name", lambda: "nt")
    assert _default_data_dir() == tmp_path / "custom"


def test_apply_runtime_env_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.launcher.runtime_os_name", lambda: "nt")
    monkeypatch.delenv("DICOMM_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    apply_runtime_env()
    assert Path(os.environ["DICOMM_DATA_DIR"]) == tmp_path / "dicommunication"
    assert windows_data_dir() == tmp_path / "dicommunication"


def test_apply_runtime_env_posix_leaves_unset(monkeypatch) -> None:
    monkeypatch.setattr("app.launcher.runtime_os_name", lambda: "posix")
    monkeypatch.delenv("DICOMM_DATA_DIR", raising=False)
    apply_runtime_env()
    assert "DICOMM_DATA_DIR" not in os.environ


def test_launcher_reuses_running_server(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("app.launcher.apply_runtime_env", lambda: None)
    monkeypatch.setattr("app.launcher.server_is_up", lambda url, timeout=0.4: True)
    monkeypatch.setattr("app.desktop.is_frozen", lambda: False)
    monkeypatch.delenv("DICOMM_UI", raising=False)
    monkeypatch.setattr("app.launcher.webbrowser.open", lambda url: opened.append(url))
    assert main(["--host", "127.0.0.1", "--port", "8080"]) == 0
    assert opened == ["http://127.0.0.1:8080/"]
    opened.clear()
    assert main(["--host", "127.0.0.1", "--port", "8080", "--profile", "dicomtag-analytics"]) == 0
    assert opened == ["http://127.0.0.1:8080/vue/"]
    opened.clear()
    assert main(["--host", "127.0.0.1", "--port", "8080", "--profile", "vue-analytics"]) == 0
    assert opened == ["http://127.0.0.1:8080/vue/"]


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


def test_pack_nuget_wraps_msi(tmp_path) -> None:
    import zipfile
    from xml.etree import ElementTree

    msi = tmp_path / "dicommunication-0.2.0-win64.msi"
    msi.write_bytes(b"msi-payload")
    pack_nuget = _load_pack_nuget()
    nupkg = pack_nuget.pack(msi, "0.2.0", tmp_path / "out")
    assert nupkg == tmp_path / "out" / "dicommunication.msi.0.2.0.nupkg"
    with zipfile.ZipFile(nupkg) as zf:
        names = set(zf.namelist())
        assert "dicommunication.msi.nuspec" in names
        assert "tools/dicommunication-0.2.0-win64.msi" in names
        assert zf.read("tools/dicommunication-0.2.0-win64.msi") == b"msi-payload"
        nuspec = ElementTree.fromstring(zf.read("dicommunication.msi.nuspec"))
        ns = {"n": "http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd"}
        assert nuspec.find("n:metadata/n:id", ns).text == "dicommunication.msi"
        assert nuspec.find("n:metadata/n:version", ns).text == "0.2.0"


def test_pack_nuget_cli(tmp_path) -> None:
    msi = tmp_path / "dicommunication-9.9.9-win64.msi"
    msi.write_bytes(b"msi")
    pack_nuget = _load_pack_nuget()
    assert (
        pack_nuget.main(
            [str(msi), "--version", "9.9.9", "--output", str(tmp_path)]
        )
        == 0
    )
    assert (tmp_path / "dicommunication.msi.9.9.9.nupkg").is_file()


def test_keep_alive_hint_macos(monkeypatch) -> None:
    monkeypatch.setattr("app.launcher.sys.platform", "darwin")
    assert "Dock" in keep_alive_hint()


def test_keep_alive_hint_windows(monkeypatch) -> None:
    monkeypatch.setattr("app.launcher.sys.platform", "win32")
    monkeypatch.setattr("app.launcher.runtime_os_name", lambda: "nt")
    assert "window" in keep_alive_hint()


def test_keep_alive_hint_native_window() -> None:
    from app.desktop import UI_WINDOW

    assert "Close the Dicommunication window" in keep_alive_hint(UI_WINDOW)
    assert "Close the Dicomtag Analytics window" in keep_alive_hint(
        UI_WINDOW, title="Dicomtag Analytics"
    )


def test_macos_spec_is_windowed_app_bundle() -> None:
    spec = (ROOT / "packaging" / "macos" / "dicommunication.spec").read_text(encoding="utf-8")
    assert "BUNDLE(" in spec
    assert 'name="Dicommunication.app"' in spec
    assert "argv_emulation=False" in spec
    assert "argv_emulation=True" not in spec
    assert "console=False" in spec
    assert "launcher.py" in spec
    assert "pro.arnout.dicommunication" in spec
    assert "webview" in spec
    assert "webview.platforms.cocoa" in spec
    assert "AppKit" in spec
    assert "NSPrincipalClass" in spec
    assert "rthook_cocoa.py" in spec
    assert "app.icns" in spec
    assert "icon=None" not in spec
    rthook = ROOT / "packaging" / "macos" / "rthook_cocoa.py"
    assert rthook.is_file()
    assert "NSApplicationActivationPolicyRegular" in rthook.read_text(encoding="utf-8")


def test_macos_build_script_exists() -> None:
    script = ROOT / "packaging" / "macos" / "build.sh"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "dicommunication.spec" in text
    assert "make_dmg.py" in text
    assert "Dicommunication.app" in text
    assert "requirements-desktop.txt" in text


def test_macos_dmg_workflow() -> None:
    workflow = (ROOT / ".github" / "workflows" / "macos-dmg.yml").read_text(encoding="utf-8")
    assert "runs-on: macos-latest" in workflow
    assert "actions/setup-python@v7" in workflow
    assert "actions/checkout@v7" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "softprops/action-gh-release@v3" in workflow
    assert "packaging/macos/build.sh" in workflow
    assert "requirements-desktop.txt" in workflow
    assert "release_tag" in workflow
    assert "dist/*.dmg" in workflow


def test_make_dmg_normalizes_arch_and_filename() -> None:
    make_dmg = _load_make_dmg()
    assert make_dmg.normalize_arch("aarch64") == "arm64"
    assert make_dmg.normalize_arch("AMD64") == "x86_64"
    assert make_dmg.dmg_filename("0.2.0", "arm64") == "dicommunication-0.2.0-macos-arm64.dmg"


def test_make_dmg_stages_app_and_applications_link(tmp_path) -> None:
    app = tmp_path / "Dicommunication.app"
    macos_dir = app / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    (macos_dir / "dicommunication").write_text("fake", encoding="utf-8")
    staging = tmp_path / "stage"
    make_dmg = _load_make_dmg()
    make_dmg.stage_app(app, staging)
    assert (staging / "Dicommunication.app" / "Contents" / "MacOS" / "dicommunication").is_file()
    assert (staging / "Applications").is_symlink()
    assert (staging / "Applications").readlink() == Path("/Applications")
    readme = (staging / "Read Me.txt").read_text(encoding="utf-8")
    assert "Gatekeeper" in readme
    assert "own window" in readme
    assert "Dicomtag Analytics" in readme
    assert "--profile dicomtag-analytics" in readme


def test_make_dmg_stage_only_cli(tmp_path) -> None:
    app = tmp_path / "Dicommunication.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("<plist/>", encoding="utf-8")
    make_dmg = _load_make_dmg()
    out = tmp_path / "dist"
    assert (
        make_dmg.main(
            [
                str(app),
                "--version",
                "0.2.0",
                "--arch",
                "arm64",
                "--output",
                str(out),
                "--stage-only",
            ]
        )
        == 0
    )
    staging = out / "dmg-staging"
    assert (staging / "Dicommunication.app").is_dir()
    assert not list(out.glob("*.dmg"))


def test_resolve_ui_mode_frozen_defaults_to_window(monkeypatch) -> None:
    from app.desktop import UI_WINDOW, resolve_ui_mode

    monkeypatch.setattr("app.desktop.is_frozen", lambda: True)
    monkeypatch.delenv("DICOMM_UI", raising=False)
    assert resolve_ui_mode(no_browser=False, browser=False, window=False) == UI_WINDOW


def test_resolve_ui_mode_source_defaults_to_browser(monkeypatch) -> None:
    from app.desktop import UI_BROWSER, resolve_ui_mode

    monkeypatch.setattr("app.desktop.is_frozen", lambda: False)
    monkeypatch.delenv("DICOMM_UI", raising=False)
    assert resolve_ui_mode(no_browser=False, browser=False, window=False) == UI_BROWSER


def test_resolve_ui_mode_env_and_flags(monkeypatch) -> None:
    from app.desktop import UI_BROWSER, UI_NONE, UI_WINDOW, resolve_ui_mode

    monkeypatch.setattr("app.desktop.is_frozen", lambda: True)
    monkeypatch.setenv("DICOMM_UI", "browser")
    assert resolve_ui_mode(no_browser=False, browser=False, window=False) == UI_BROWSER
    assert resolve_ui_mode(no_browser=True, browser=False, window=False) == UI_NONE
    assert resolve_ui_mode(no_browser=False, browser=False, window=True) == UI_WINDOW


def test_frozen_reuses_running_server_in_native_window(monkeypatch) -> None:
    from app.desktop import UI_WINDOW

    opened: list[str] = []
    monkeypatch.setattr("app.launcher.apply_runtime_env", lambda: None)
    monkeypatch.setattr("app.launcher.server_is_up", lambda url, timeout=0.4: True)
    monkeypatch.setattr("app.launcher.resolve_ui_mode", lambda **kwargs: UI_WINDOW)
    monkeypatch.setattr(
        "app.launcher.run_native_window",
        lambda url, title=None: opened.append(url) or True,
    )
    monkeypatch.setattr(
        "app.launcher.webbrowser.open",
        lambda url: opened.append(f"browser:{url}"),
    )
    assert main(["--host", "127.0.0.1", "--port", "8080"]) == 0
    assert opened == ["http://127.0.0.1:8080/"]
    opened.clear()
    assert main(["--host", "127.0.0.1", "--port", "8080", "--profile", "dicomtag-analytics"]) == 0
    assert opened == ["http://127.0.0.1:8080/vue/"]


def test_run_native_window_starts_webview(monkeypatch, tmp_path) -> None:
    import types

    from app.desktop import WINDOW_TITLE, _bring_app_to_front, run_native_window

    calls: dict = {}

    class Shown:
        def __iadd__(self, fn):
            calls["shown"] = fn
            return self

    class Window:
        events = types.SimpleNamespace(shown=Shown())

    mod = types.ModuleType("webview")

    def create_window(title, url, **kwargs):
        calls["create"] = (title, url, kwargs)
        return Window()

    def start(**kwargs):
        calls["start"] = kwargs

    mod.create_window = create_window
    mod.start = start
    monkeypatch.setitem(sys.modules, "webview", mod)
    monkeypatch.setenv("DICOMM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.desktop.native_gui", lambda: "cocoa")
    assert run_native_window("http://127.0.0.1:8080/") is True
    assert calls["create"][0] == WINDOW_TITLE
    assert calls["create"][1] == "http://127.0.0.1:8080/"
    calls.clear()
    assert run_native_window("http://127.0.0.1:8080/vue/", title="Dicomtag Analytics") is True
    assert calls["create"][0] == "Dicomtag Analytics"
    assert calls["create"][1] == "http://127.0.0.1:8080/vue/"
    assert calls["create"][2]["hidden"] is False
    assert calls["start"]["private_mode"] is False
    assert calls["start"]["gui"] == "cocoa"
    assert calls["shown"] is _bring_app_to_front
    assert (tmp_path / "webview").is_dir()


def test_macos_run_until_windows_close_pumps_when_app_already_running(monkeypatch) -> None:
    import types

    from app import desktop

    shown: list[str] = []

    class Win:
        def show(self) -> None:
            shown.append("show")

    class FakeWebview:
        windows = [Win()]

    class FakeNSApp:
        @staticmethod
        def isRunning() -> bool:
            return True

        @staticmethod
        def setActivationPolicy_(_policy) -> None:
            return None

        @staticmethod
        def activateIgnoringOtherApps_(_flag) -> None:
            return None

        @staticmethod
        def windows() -> list:
            return []

    class FakeRunLoop:
        @staticmethod
        def currentRunLoop():
            return FakeRunLoop()

        def runMode_beforeDate_(self, mode, date) -> None:
            FakeWebview.windows.clear()

    appkit = types.ModuleType("AppKit")
    appkit.NSApp = FakeNSApp
    appkit.NSApplicationActivationPolicyRegular = 0
    foundation = types.ModuleType("Foundation")
    foundation.NSDate = types.SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda _s: "later")
    foundation.NSDefaultRunLoopMode = "NSDefaultRunLoopMode"
    foundation.NSRunLoop = FakeRunLoop
    pyobjc = types.ModuleType("PyObjCTools")
    pyobjc.AppHelper = types.SimpleNamespace(runEventLoop=lambda: shown.append("loop"))

    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setitem(sys.modules, "PyObjCTools", pyobjc)

    desktop._macos_run_until_windows_close(FakeWebview)
    assert shown == ["show"]
    assert FakeWebview.windows == []


def test_macos_run_until_windows_close_starts_loop_when_idle(monkeypatch) -> None:
    import types

    from app import desktop

    loops: list[str] = []

    class FakeWebview:
        windows = [object()]

    class FakeNSApp:
        @staticmethod
        def isRunning() -> bool:
            return False

        @staticmethod
        def setActivationPolicy_(_policy) -> None:
            return None

        @staticmethod
        def activateIgnoringOtherApps_(_flag) -> None:
            return None

        @staticmethod
        def windows() -> list:
            return []

    appkit = types.ModuleType("AppKit")
    appkit.NSApp = FakeNSApp
    appkit.NSApplicationActivationPolicyRegular = 0
    foundation = types.ModuleType("Foundation")
    foundation.NSDate = types.SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda _s: "later")
    foundation.NSDefaultRunLoopMode = "NSDefaultRunLoopMode"
    foundation.NSRunLoop = types.SimpleNamespace(currentRunLoop=lambda: None)
    pyobjc = types.ModuleType("PyObjCTools")
    pyobjc.AppHelper = types.SimpleNamespace(runEventLoop=lambda: loops.append("loop"))

    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setitem(sys.modules, "PyObjCTools", pyobjc)

    desktop._macos_run_until_windows_close(FakeWebview)
    assert loops == ["loop"]


def test_redirect_frozen_stdio_writes_launch_log(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.launcher.is_frozen", lambda: True)
    monkeypatch.setenv("DICOMM_DATA_DIR", str(tmp_path))
    old_out, old_err = sys.stdout, sys.stderr
    handle = None
    try:
        redirect_frozen_stdio()
        handle = sys.stdout
        print("hello-from-frozen", flush=True)
    finally:
        sys.stdout = old_out
        sys.stderr = old_err
        if handle is not None and handle not in {old_out, old_err}:
            handle.close()
    text = (tmp_path / "launch.log").read_text(encoding="utf-8")
    assert "dicommunication launch" in text
    assert "hello-from-frozen" in text


def test_redirect_frozen_stdio_skips_unfrozen(monkeypatch) -> None:
    monkeypatch.setattr("app.launcher.is_frozen", lambda: False)
    old = sys.stdout
    redirect_frozen_stdio()
    assert sys.stdout is old


def test_windows_spec_hides_console_and_bundles_webview() -> None:
    spec = (ROOT / "packaging" / "windows" / "dicommunication.spec").read_text(
        encoding="utf-8"
    )
    assert "console=False" in spec
    assert "webview" in spec
    workflow = (ROOT / ".github" / "workflows" / "windows-msi.yml").read_text(
        encoding="utf-8"
    )
    assert "requirements-desktop.txt" in workflow


def test_packaging_icons_match_favicon() -> None:
    svg = (ROOT / "app" / "static" / "favicon.svg").read_text(encoding="utf-8")
    assert "arnout.pro" in svg
    assert "Sansation Bold" in svg
    assert "M32 10 12 54" not in svg
    ico = ROOT / "packaging" / "icons" / "app.ico"
    icns = ROOT / "packaging" / "icons" / "app.icns"
    preview = ROOT / "packaging" / "icons" / "app-1024.png"
    assert ico.read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert icns.read_bytes()[:4] == b"icns"
    assert preview.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert ico.stat().st_size > 1024
    assert icns.stat().st_size > 16_000


def test_windows_spec_and_wix_use_app_icon() -> None:
    spec = (ROOT / "packaging" / "windows" / "dicommunication.spec").read_text(encoding="utf-8")
    wxs = (ROOT / "packaging" / "windows" / "Product.wxs").read_text(encoding="utf-8")
    assert "app.ico" in spec
    assert 'icon=ICON' in spec
    assert 'SourceFile="packaging\\icons\\app.ico"' in wxs
    assert 'Icon="AppIcon"' in wxs
    assert "ARPPRODUCTICON" in wxs
    assert 'Name="Dicomtag Analytics"' in wxs
    assert 'Arguments="--profile dicomtag-analytics"' in wxs


def test_htmx_is_served_from_this_app_not_a_cdn() -> None:
    base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]*\ssrc="([^"]+)"', base)

    assert scripts, "base.html should still load htmx"
    for src in scripts:
        assert src.startswith("/static/"), f"{src} is loaded from a third party"

    vendored = ROOT / "app" / "static" / "vendor" / "htmx-2.0.4.min.js"
    assert vendored.is_file()
    assert "/static/vendor/htmx-2.0.4.min.js" in base


def test_vendored_htmx_is_served(client) -> None:
    response = client.get("/static/vendor/htmx-2.0.4.min.js")

    assert response.status_code == 200
    assert response.text.startswith("var htmx=")


# "${VAR:-default}:published:target" — an overridable host bind address.
COMPOSE_BINDING = re.compile(r"^\$\{(\w+):-([^}]*)\}:(\d+):(\d+)$")


def _compose_published_ports() -> list[str]:
    """The entries under the compose service's `ports:` key, unquoted."""
    lines = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    entries: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == "ports:":
            inside = True
            continue
        if not inside or stripped.startswith("#") or not stripped:
            continue
        if not stripped.startswith("- "):
            break
        entries.append(stripped[2:].strip().strip('"'))
    return entries


def test_compose_publishes_every_port_through_an_overridable_bind_address() -> None:
    published = _compose_published_ports()

    assert len(published) == 2, published
    for entry in published:
        assert COMPOSE_BINDING.match(entry), (
            f"{entry!r} pins a host interface. Publish it as "
            '"${VAR:-<default>}:<port>:<port>" so an operator can change it.'
        )


def test_compose_keeps_the_unauthenticated_ui_on_loopback() -> None:
    binds = {}
    for entry in _compose_published_ports():
        match = COMPOSE_BINDING.match(entry)
        assert match
        _var, default_bind, host_port, _container_port = match.groups()
        binds[host_port] = default_bind

    # The web UI and JSON API have no login, so the default must not be
    # reachable from off the host.
    assert binds["8080"] == "127.0.0.1"

    # The MWL SCP is the opposite case: a modality has to be able to C-FIND
    # this workstation, so locking it to loopback would silently break it.
    assert binds["11112"] == "0.0.0.0"


def _compose_environment() -> dict[str, str]:
    """The compose service's `environment:` mapping, values left unsubstituted."""
    lines = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    env: dict[str, str] = {}
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == "environment:":
            inside = True
            continue
        if not inside or stripped.startswith("#") or not stripped:
            continue
        if ":" not in stripped or stripped.endswith(":"):
            break
        key, _, value = stripped.partition(":")
        env[key.strip()] = value.strip().strip('"')
    return env


def test_compose_tells_the_container_which_address_it_published_on() -> None:
    passthrough = _compose_environment().get("DICOMM_HTTP_BIND", "")
    match = re.fullmatch(r"\$\{DICOMM_HTTP_BIND:-([^}]*)\}", passthrough)
    assert match, f"DICOMM_HTTP_BIND passthrough is {passthrough!r}"

    ui_default = next(
        COMPOSE_BINDING.match(entry).group(2)
        for entry in _compose_published_ports()
        if COMPOSE_BINDING.match(entry).group(3) == "8080"
    )
    # If these drift apart the startup banner reports an address the port was
    # not actually published on.
    assert match.group(1) == ui_default


def test_publish_note_says_nothing_outside_compose() -> None:
    assert http_publish_note({}) is None
    assert http_publish_note({"DICOMM_HTTP_BIND": "   "}) is None


def test_publish_note_explains_a_loopback_publish() -> None:
    note = http_publish_note({"DICOMM_HTTP_BIND": "127.0.0.1"})

    assert note is not None
    assert "127.0.0.1:8080" in note
    assert "Docker host only" in note
    # The whole point is that it names the way out.
    assert "DICOMM_HTTP_BIND=0.0.0.0" in note


def test_publish_note_warns_when_the_ui_is_open_to_the_network() -> None:
    note = http_publish_note({"DICOMM_HTTP_BIND": "0.0.0.0"})

    assert note is not None
    assert "reachable from the network" in note
    assert "reverse proxy" in note


def test_publish_note_uses_the_configured_port() -> None:
    note = http_publish_note({"DICOMM_HTTP_BIND": "127.0.0.1", "PORT": "9000"})

    assert note is not None
    assert "127.0.0.1:9000" in note
