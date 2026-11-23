"""The macOS background service (app/macos_service.py), run against a temporary folder and a fake launchctl."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from app import macos_service as ms
from app.auth import UserStore, verify_password


class FakeRunner:
    """Stands in for subprocess.run: records every command, answers from a table."""

    def __init__(self, answers: dict[str, subprocess.CompletedProcess] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def __call__(self, command, **_kwargs):
        self.calls.append(list(command))
        key = " ".join(command[:2])
        return self.answers.get(key, subprocess.CompletedProcess(command, 0, "", ""))

    def ran(self, *prefix: str) -> bool:
        return any(call[: len(prefix)] == list(prefix) for call in self.calls)


POSIX = sys.platform != "win32"  # file modes and ownership only mean something on a Unix; the service only ever runs on a Mac


@pytest.fixture(autouse=True)
def chowns(monkeypatch) -> list[tuple[str, int, int]]:
    """Record ownership changes instead of making them, so these tests give the same result for any user.

    (The CI runner is not root and a developer's container may be; the real chown is only
    allowed for root, so it must never run in a test.)
    """
    calls: list[tuple[str, int, int]] = []
    # raising=False: Windows has no os.chown (this suite also runs there, in the MSI build).
    monkeypatch.setattr(ms.os, "chown", lambda path, uid, gid, **_kw: calls.append((str(path), uid, gid)), raising=False)
    monkeypatch.setattr(ms, "_service_ids", lambda: None)  # the account does not exist unless a test says so
    monkeypatch.setattr(ms, "_is_root", lambda: False)
    return calls


@pytest.fixture
def layout(tmp_path: Path) -> ms.Layout:
    return ms.Layout(root=tmp_path)


def plist_of(layout: ms.Layout) -> dict:
    return plistlib.loads(layout.plist.read_bytes())


# --- what is written -------------------------------------------------------------


def test_the_launchd_job_runs_at_boot_as_the_service_account_and_always_asks_for_sign_in(layout) -> None:
    job = plistlib.loads(ms.render_plist(ms.Settings(port=8090), executable="/usr/local/lib/dicommunication/dicommunication", data_dir="/Library/Application Support/Dicommunication", log_file="/var/log/dicommunication/service.log"))
    assert job["Label"] == "pro.arnout.dicommunication"
    assert job["ProgramArguments"] == ["/usr/local/lib/dicommunication/dicommunication", "--no-browser", "--host", "127.0.0.1", "--port", "8090"]
    assert job["RunAtLoad"] is True and job["KeepAlive"] is True
    assert job["UserName"] == job["GroupName"] == "_dicommunication"
    assert job["EnvironmentVariables"] == {"DICOMM_DATA_DIR": "/Library/Application Support/Dicommunication", "DICOMM_REQUIRE_LOGIN": "1"}
    assert job["StandardOutPath"] == job["StandardErrorPath"] == "/var/log/dicommunication/service.log"


def test_the_network_option_listens_everywhere_and_names_the_computer() -> None:
    job = plistlib.loads(ms.render_plist(ms.Settings(port=8080, listen="network"), executable="/x", data_dir="/d", log_file="/l", hostnames=["pacs-mac.local", "pacs-mac"]))
    assert job["ProgramArguments"][2:4] == ["--host", "0.0.0.0"]
    assert job["EnvironmentVariables"]["DICOMM_ALLOWED_HOSTS"] == "pacs-mac.local,pacs-mac"
    assert "DICOMM_ALLOWED_HOSTS" not in plistlib.loads(ms.render_plist(ms.Settings(), executable="/x", data_dir="/d", log_file="/l", hostnames=["a"]))["EnvironmentVariables"]


def test_a_bad_port_or_listen_value_is_refused() -> None:
    with pytest.raises(ValueError):
        ms.render_plist(ms.Settings(port=80), executable="/x", data_dir="/d", log_file="/l")
    with pytest.raises(ValueError):
        ms.render_plist(ms.Settings(listen="everywhere"), executable="/x", data_dir="/d", log_file="/l")


def test_the_shortcut_opens_https_only_for_the_network_option() -> None:
    assert plistlib.loads(ms.render_shortcut(ms.Settings(port=8090)))["URL"] == "http://localhost:8090/"
    assert plistlib.loads(ms.render_shortcut(ms.Settings(port=8080, listen="network")))["URL"] == "https://localhost:8080/"


def test_the_log_rotation_line_has_the_fields_newsyslog_expects() -> None:
    line = ms.render_newsyslog("/var/log/dicommunication/service.log").splitlines()[1].split()
    assert line == ["/var/log/dicommunication/service.log", "_dicommunication:_dicommunication", "640", "5", "5120", "*", "NJ"]  # no spaces in the path: newsyslog cannot quote them


# --- configure -------------------------------------------------------------------


def test_a_first_install_uses_the_defaults_and_writes_everything(layout) -> None:
    runner = FakeRunner()
    assert ms.configure(layout, runner=runner, from_saved=True, hostnames=[]) == 0
    assert plist_of(layout)["ProgramArguments"][-4:] == ["--host", "127.0.0.1", "--port", "8080"]
    assert json.loads(layout.settings_file.read_text()) == {"port": 8080, "listen": "local"}
    assert (layout.data_dir / "logs").is_dir() and layout.log_dir.is_dir()
    assert layout.newsyslog.is_file() and plistlib.loads(layout.shortcut.read_bytes())["URL"] == "http://localhost:8080/"
    if POSIX:
        assert oct(layout.data_dir.stat().st_mode & 0o777) == "0o700"  # patient data: owner only
        assert oct(layout.plist.stat().st_mode & 0o777) == "0o644"
    assert not runner.calls  # no reload asked for, and no firewall exception for this computer only


def test_an_upgrade_keeps_the_port_and_network_option_an_administrator_chose(layout) -> None:
    layout.data_dir.mkdir(parents=True)
    layout.settings_file.write_text(json.dumps({"port": 8443, "listen": "network"}))
    assert ms.configure(layout, from_saved=True, runner=FakeRunner(), hostnames=["h"]) == 0
    assert plist_of(layout)["ProgramArguments"][-4:] == ["--host", "0.0.0.0", "--port", "8443"]


def test_unreadable_saved_settings_fall_back_to_the_defaults(layout) -> None:
    layout.data_dir.mkdir(parents=True)
    for junk in ("not json", '{"port": "abc"}', '{"listen": "everywhere"}', "[]"):
        layout.settings_file.write_text(junk)
        assert ms.load_settings(layout) == ms.Settings()


def test_reload_stops_then_loads_the_job_and_the_network_option_opens_the_firewall(layout) -> None:
    runner = FakeRunner()
    assert ms.configure(layout, listen="network", reload=True, runner=runner, hostnames=["h"]) == 0
    assert runner.ran("launchctl", "bootout", "system/pro.arnout.dicommunication")
    assert runner.ran("launchctl", "bootstrap", "system", str(layout.plist))
    bootout = next(i for i, c in enumerate(runner.calls) if c[1:2] == ["bootout"])
    bootstrap = next(i for i, c in enumerate(runner.calls) if c[1:2] == ["bootstrap"])
    assert bootout < bootstrap
    assert runner.ran(ms.FIREWALL_TOOL, "--add", str(layout.executable)) and runner.ran(ms.FIREWALL_TOOL, "--unblockapp", str(layout.executable))


def test_as_root_with_the_service_account_the_files_get_the_right_owners(layout, monkeypatch, chowns) -> None:
    monkeypatch.setattr(ms, "_is_root", lambda: True)
    monkeypatch.setattr(ms, "_service_ids", lambda: (301, 302))
    assert ms.configure(layout, runner=FakeRunner(), hostnames=[]) == 0
    owners = {Path(path): (uid, gid) for path, uid, gid in chowns}
    assert owners[layout.plist] == (0, 0)  # launchd refuses a job that root does not own
    for private in (layout.data_dir, layout.data_dir / "logs", layout.log_dir, layout.settings_file):
        assert owners[private] == (301, 302), private  # the service account, and nobody else, may read patient data


def test_without_the_service_account_the_folders_are_left_alone_and_it_is_logged(layout, monkeypatch, chowns) -> None:
    monkeypatch.setattr(ms, "_is_root", lambda: True)
    assert ms.configure(layout, runner=FakeRunner(), hostnames=[]) == 0
    assert [Path(path) for path, _u, _g in chowns] == [layout.plist]
    assert "does not exist" in (layout.log_dir / ms.SETUP_LOG_NAME).read_text()


def test_a_failing_launchctl_is_reported_but_the_files_stay_in_place(layout, capsys) -> None:
    runner = FakeRunner({"launchctl bootstrap": subprocess.CompletedProcess([], 5, "", "Bootstrap failed: 5")})
    assert ms.configure(layout, reload=True, runner=runner, hostnames=[]) == 5
    assert layout.plist.is_file() and "did not load" in capsys.readouterr().out
    assert "Bootstrap failed" in (layout.log_dir / ms.SETUP_LOG_NAME).read_text()


def test_invalid_port_or_listen_changes_nothing(layout, capsys) -> None:
    for kwargs in ({"port": "80"}, {"port": "abc"}, {"listen": "everywhere"}):
        assert ms.configure(layout, runner=FakeRunner(), **kwargs) == 2
    assert not layout.plist.exists() and not layout.settings_file.exists()
    assert "Not done" in capsys.readouterr().out


def test_changing_only_the_port_keeps_the_network_option(layout) -> None:
    ms.configure(layout, listen="network", runner=FakeRunner(), hostnames=[])
    ms.configure(layout, port="8099", runner=FakeRunner(), hostnames=[])
    assert ms.load_settings(layout) == ms.Settings(port=8099, listen="network")


def test_the_shortcut_is_optional(layout, monkeypatch) -> None:
    monkeypatch.setattr(ms, "render_shortcut", lambda settings: (_ for _ in ()).throw(OSError("no /Applications")))
    assert ms.configure(layout, runner=FakeRunner(), hostnames=[]) == 0  # a missing shortcut never fails the install


# --- status and uninstall ----------------------------------------------------------


def test_status_reports_the_state_and_the_address(layout) -> None:
    ms.configure(layout, port="8090", runner=FakeRunner(), hostnames=[])
    running = FakeRunner({"launchctl print": subprocess.CompletedProcess([], 0, "pro.arnout = {\n\tstate = running\n}", "")})
    text = ms.status(layout, running)
    assert "Service:  running" in text and "http://localhost:8090/" in text and "this computer only" in text
    assert "Service:  loaded" in ms.status(layout, FakeRunner({"launchctl print": subprocess.CompletedProcess([], 0, "state = waiting", "")}))
    assert "Service:  not loaded" in ms.status(layout, FakeRunner({"launchctl print": subprocess.CompletedProcess([], 113, "", "")}))


def test_uninstall_keeps_the_data_unless_told_otherwise(layout) -> None:
    ms.configure(layout, runner=FakeRunner(), hostnames=[])
    layout.program_dir.mkdir(parents=True)
    layout.executable.write_text("x")
    layout.command_link.parent.mkdir(parents=True)
    try:
        layout.command_link.symlink_to(layout.executable)
    except OSError:  # Windows without the symlink privilege
        layout.command_link.write_text("link")
    runner, said = FakeRunner(), []
    assert ms.uninstall(layout, delete_data=False, runner=runner, say=said.append) == 0
    assert runner.ran("launchctl", "bootout") and runner.ran("pkgutil", "--forget", "pro.arnout.dicommunication")
    for gone in (layout.plist, layout.newsyslog, layout.shortcut, layout.program_dir, layout.command_link):
        assert not gone.exists() and not gone.is_symlink(), gone
    assert layout.data_dir.is_dir() and "patient data" in said[0]
    assert not runner.ran("dscl")  # the account stays while the data it owns stays


def test_uninstall_can_delete_the_data_logs_and_account(layout) -> None:
    ms.configure(layout, runner=FakeRunner(), hostnames=[])
    runner = FakeRunner()
    ms.uninstall(layout, delete_data=True, runner=runner, say=lambda _m: None)
    assert not layout.data_dir.exists() and not layout.log_dir.exists()
    assert runner.ran("dscl", ".", "-delete", "/Users/_dicommunication") and runner.ran("dscl", ".", "-delete", "/Groups/_dicommunication")


# --- the command: dicommunication service ... -----------------------------------------


def test_the_command_refuses_to_run_off_a_mac(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(ms, "is_macos", lambda: False)
    assert ms.service_main(["status"]) == 2 and "only available on macOS" in capsys.readouterr().out


def test_changing_things_needs_administrator_rights(layout, monkeypatch, capsys) -> None:
    monkeypatch.setattr(ms, "_is_root", lambda: False)
    for argv in (["network", "on"], ["port", "8090"], ["create-admin", "x"], ["uninstall"]):
        assert ms.service_main(argv, layout=layout, runner=FakeRunner()) == 1
    assert "sudo" in capsys.readouterr().out
    assert ms.service_main(["status"], layout=layout, runner=FakeRunner()) == 0  # reading is fine for anyone


def test_network_on_and_off_and_port_reconfigure_and_reload(layout, monkeypatch, capsys) -> None:
    monkeypatch.setattr(ms, "_is_root", lambda: True)
    monkeypatch.setattr(ms, "computer_names", lambda: ["mac.local"])
    runner = FakeRunner()
    assert ms.service_main(["network", "on"], layout=layout, runner=runner) == 0
    assert ms.load_settings(layout).listen == "network" and runner.ran("launchctl", "bootstrap")
    assert "HTTPS" in capsys.readouterr().out
    assert ms.service_main(["port", "8099"], layout=layout, runner=FakeRunner()) == 0
    assert ms.load_settings(layout) == ms.Settings(port=8099, listen="network")
    assert ms.service_main(["network", "off"], layout=layout, runner=FakeRunner()) == 0
    assert ms.load_settings(layout) == ms.Settings(port=8099, listen="local")
    assert ms.service_main(["port", "80"], layout=layout, runner=FakeRunner()) == 2


def test_create_admin_asks_twice_and_makes_an_account_the_app_can_use(layout, monkeypatch) -> None:
    monkeypatch.setattr(ms, "_is_root", lambda: True)
    monkeypatch.setattr(ms, "fix_ownership", lambda path: None)
    answers = iter(["a long enough passphrase", "a long enough passphrase"])
    assert ms.service_main(["create-admin", "boss"], layout=layout, runner=FakeRunner(), password=lambda _p: next(answers)) == 0
    user = UserStore(layout.data_dir).by_username("boss")
    assert user.role == "admin" and verify_password("a long enough passphrase", user.password_hash)
    mismatch = iter(["one long passphrase", "another passphrase"])
    assert ms.service_main(["create-admin", "other"], layout=layout, runner=FakeRunner(), password=lambda _p: next(mismatch)) == 2
    assert UserStore(layout.data_dir).by_username("other") is None


def test_create_admin_can_read_the_password_from_a_file(layout, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ms, "_is_root", lambda: True)
    monkeypatch.setattr(ms, "fix_ownership", lambda path: None)
    secret = tmp_path / "pw.txt"
    secret.write_text("a long enough passphrase\nignored\n")
    assert ms.service_main(["create-admin", "boss", "--password-file", str(secret)], layout=layout, runner=FakeRunner()) == 0
    assert UserStore(layout.data_dir).by_username("boss") is not None
    assert ms.service_main(["create-admin", "x", "--password-file", str(tmp_path / "none.txt")], layout=layout, runner=FakeRunner()) == 2


def test_uninstall_asks_about_the_data_only_at_a_terminal_and_defaults_to_keeping_it(layout, monkeypatch) -> None:
    monkeypatch.setattr(ms, "_is_root", lambda: True)
    ms.configure(layout, runner=FakeRunner(), hostnames=[])
    monkeypatch.setattr(ms.sys.stdin, "isatty", lambda: False, raising=False)
    ms.service_main(["uninstall"], layout=layout, runner=FakeRunner(), ask=lambda _q: pytest.fail("must not ask without a terminal"))
    assert layout.data_dir.exists()
    ms.configure(layout, runner=FakeRunner(), hostnames=[])
    monkeypatch.setattr(ms.sys.stdin, "isatty", lambda: True, raising=False)
    ms.service_main(["uninstall"], layout=layout, runner=FakeRunner(), ask=lambda _q: "")
    assert layout.data_dir.exists()  # just pressing Return keeps it
    ms.service_main(["uninstall"], layout=layout, runner=FakeRunner(), ask=lambda _q: "y")
    assert not layout.data_dir.exists()


# --- how the launcher reaches it -------------------------------------------------------


def test_the_launcher_hands_the_service_command_over(monkeypatch) -> None:
    from app import launcher

    seen = []
    monkeypatch.setattr(ms, "service_main", lambda argv: seen.append(argv) or 0)
    assert launcher.main(["service", "status"]) == 0 and seen == [["status"]]


def test_configure_service_on_a_mac_runs_the_mac_setup_and_elsewhere_the_windows_one(monkeypatch) -> None:
    from app import launcher, service_setup

    calls = []
    monkeypatch.setattr(ms, "run_configure", lambda **kw: calls.append(("mac", kw)) or 0)
    monkeypatch.setattr(service_setup, "run_configure", lambda port, listen: calls.append(("win", port, listen)) or 0)
    monkeypatch.setattr(launcher, "is_macos", lambda: True)
    assert launcher.main(["--configure-service", "--from-saved", "--reload"]) == 0
    assert calls[-1] == ("mac", {"port": 8080, "listen": "local", "from_saved": True, "reload": True})
    monkeypatch.setattr(launcher, "is_macos", lambda: False)
    assert launcher.main(["--configure-service", "--port", "8090", "--listen", "network"]) == 0
    assert calls[-1] == ("win", 8090, "network")


def test_the_service_data_folder_on_a_mac(monkeypatch) -> None:
    from app import service_setup

    monkeypatch.setattr(service_setup, "is_macos", lambda: True)
    assert service_setup.service_data_dir() == Path("/Library/Application Support/Dicommunication")
