"""Stop listening / Start listening: free the local DICOM port for another program without losing the settings."""

from __future__ import annotations

import socket

from app.models import LocalAE
from app.store import ConfigStore


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _answers(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _listening_app(app, store: ConfigStore, **extra) -> int:
    port = _free_port()
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=port, storage_scp_enabled=True, **extra))
    app.state.mwl_scp.restart()
    return port


def test_stop_frees_the_port_and_start_takes_it_back(app, client, store: ConfigStore) -> None:
    port = _listening_app(app, store)
    try:
        assert app.state.mwl_scp.running and _answers(port)
        assert "Stop listening" in client.get("/").text

        stopped = client.post("/config/local/listener", data={"action": "pause", "next": "/"}, follow_redirects=True)
        assert stopped.status_code == 200
        assert not app.state.mwl_scp.running and not _answers(port)  # the port is free for another program
        assert f"Paused: port {port} is free" in stopped.text and "Start listening" in stopped.text
        assert store.load().local.storage_scp_enabled  # the settings are untouched

        started = client.post("/config/local/listener", data={"action": "resume", "next": "/"}, follow_redirects=True)
        assert app.state.mwl_scp.running and _answers(port) and "Stop listening" in started.text
        assert not store.load().local.listener_paused
    finally:
        app.state.mwl_scp.stop()


def test_a_pause_survives_a_restart_and_saving_the_form(app, client, store: ConfigStore) -> None:
    port = _listening_app(app, store)
    try:
        client.post("/config/local/listener", data={"action": "pause"})
        app.state.mwl_scp.restart()  # what a restart of the service does with the saved settings
        assert not app.state.mwl_scp.running and not _answers(port)
        client.post("/config/local", data={"ae_title": "DICOMM", "host": "127.0.0.1", "port": port, "storage_scp_enabled": "on"})
        assert store.load().local.listener_paused and not app.state.mwl_scp.running  # saving does not silently resume
    finally:
        app.state.mwl_scp.stop()


def test_there_is_no_button_when_nothing_listens(client, store: ConfigStore) -> None:
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=_free_port()))  # no MWL SCP, no C-STORE
    assert "Stop listening" not in client.get("/").text and "Start listening" not in client.get("/config/local").text


def test_the_pages_say_paused_not_not_listening(app, client, store: ConfigStore) -> None:
    _listening_app(app, store, listener_paused=True)
    for page in ("/", "/config"):
        body = client.get(page).text
        assert "Paused" in body and "Not listening" not in body, page


def test_bad_actions_and_return_pages_are_handled(app, client, store: ConfigStore) -> None:
    _listening_app(app, store)
    try:
        assert client.post("/config/local/listener", data={"action": "explode"}).status_code == 400
        moved = client.post("/config/local/listener", data={"action": "resume", "next": "https://evil.example/"}, follow_redirects=False)
        assert moved.status_code == 303 and moved.headers["location"] == "/config/local"  # never an outside address
    finally:
        app.state.mwl_scp.stop()


def test_the_paused_state_is_not_exported_and_an_import_does_not_change_it(tmp_path) -> None:
    from app import settings_transfer as st

    source, target = ConfigStore(tmp_path / "a"), ConfigStore(tmp_path / "b")
    source.save_local(LocalAE(ae_title="SRC", host="127.0.0.1", port=11113, storage_scp_enabled=True, listener_paused=True))
    target.save_local(LocalAE(ae_title="TGT", host="127.0.0.1", port=11114, storage_scp_enabled=True, listener_paused=False))
    exported = st.export_bytes(source, [st.SECTION_LOCAL])
    assert b"listener_paused" not in exported  # it is runtime state of that computer, not a setting

    plan = st.plan_import(target, st.parse_settings(exported), [st.SECTION_LOCAL])
    st.apply_import(target, plan)
    assert target.load().local.ae_title == "SRC" and target.load().local.listener_paused is False  # kept as it was here

    target.save_local(target.load().local.model_copy(update={"listener_paused": True}))
    again = st.plan_import(target, st.parse_settings(exported), [st.SECTION_LOCAL])
    st.apply_import(target, again)
    assert target.load().local.listener_paused is True  # a paused listener stays paused after an import
