from __future__ import annotations

from pathlib import Path

from app import __version__
from app.models import RemoteNode


def test_health_and_pages(client) -> None:
    assert client.get("/health").json() == {"status": "ok", "version": __version__}
    home = client.get("/")
    assert home.status_code == 200
    assert b"Arnout.pro Dicommunication Tool" in home.content
    assert f"v{__version__}".encode() in home.content
    assert b"nav-children" in home.content
    assert b'data-nav-id="test-tools"' in home.content
    assert b'data-nav-id="connectivity"' in home.content
    assert b'nav-branch is-open" data-nav-id="test-tools"' not in home.content
    assert b"sidebar" in home.content
    assert b"aurora-wrapper" in home.content
    assert b'id="theme-toggle"' in home.content
    assert b'data-theme-option="light"' in home.content
    assert b'data-theme-option="dark"' in home.content
    assert b'data-theme-option="system"' in home.content
    assert b'data-theme-option="professional"' in home.content
    assert b"brand-watermark" in home.content
    assert b"site-brand-mark-letter" in home.content
    assert b"site-brand-copy" in home.content
    assert b'href="/logs"' in home.content
    assert b"DIMSE" in home.content
    assert b"Connectivity" in home.content
    assert b"<details" not in home.content
    assert b"C-ECHO all nodes" not in home.content
    assert b"Open worklist" not in home.content
    assert b"Open configuration" not in home.content
    config = client.get("/config")
    assert config.status_code == 200
    assert b"Configured nodes" in config.content
    assert b"Local DICOM AE" in config.content
    assert b"Advanced settings" not in config.content
    assert b"config-tabs" not in config.content
    local_ae = client.get("/config/local")
    assert local_ae.status_code == 200
    assert b"Advanced settings" in local_ae.content
    identities = client.get("/config/identities")
    assert identities.status_code == 200
    assert b"Virtual local AE titles" in identities.content
    remotes = client.get("/config/remotes")
    assert remotes.status_code == 200
    assert b"Remote DICOM nodes" in remotes.content
    legacy = client.get("/config?edit=nope", follow_redirects=False)
    assert legacy.status_code == 303
    assert legacy.headers["location"].startswith("/config/remotes")
    echo_board = client.get("/echo-board")
    assert echo_board.status_code == 200
    worklist = client.get("/worklist")
    assert worklist.status_code == 200
    assert b"Worklist" in worklist.content
    ping = client.get("/tools/ping")
    assert ping.status_code == 200
    logs = client.get("/logs")
    assert logs.status_code == 200
    assert b"Log view" in logs.content
    echo = client.get("/tools/c-echo")
    assert echo.status_code == 200
    hl7 = client.get("/tools/hl7-send")
    assert hl7.status_code == 200
    assert b"HL7 send" in hl7.content
    assert b"Send HL7" in hl7.content
    testbench = client.get("/testbench")
    assert testbench.status_code == 200
    assert b"C-STORE" in testbench.content
    assert b"Study Root" in testbench.content
    assert b"1.2.840.10008.5.1.4.31" in testbench.content
    assert b'href="/about"' in home.content
    assert b'href="/help"' in home.content
    about = client.get("/about")
    assert about.status_code == 200
    assert f"v{__version__}".encode() in about.content
    assert b"trusted-network DICOM and HL7 connectivity workstation" in about.content
    help_page = client.get("/help")
    assert help_page.status_code == 200
    assert b"C-ECHO" in help_page.content
    assert b"Modality Worklist" in help_page.content
    assert b"Advanced troubleshooting" in help_page.content
    assert b"HL7 send" in help_page.content
    assert b"Encapsulated PDF" in help_page.content
    pdf_store = client.get("/tools/pdf-store")
    assert pdf_store.status_code == 200
    assert b"PDF to DICOM" in pdf_store.content
    assert b'name="pdfs"' in pdf_store.content
    assert b'name="zip_file"' in pdf_store.content
    assert b"webkitdirectory" in pdf_store.content
    assert b"data-browse-directory" in pdf_store.content
    assert b"data-scan-directory" in pdf_store.content
    assert b'class="req"' in pdf_store.content
    assert b"Generate Patient Name" in pdf_store.content
    assert b"Unique patient per PDF" in pdf_store.content
    assert b'nav-branch is-open" data-nav-id="test-tools"' in pdf_store.content
    assert b'data-nav-id="dimse"' in pdf_store.content


def test_shared_layout_is_dense() -> None:
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text(
        encoding="utf-8"
    )
    assert ".main {\n  padding: 1rem 1.25rem 1.15rem;" in css
    assert ".panel { padding: 0.75rem 0.9rem 0.85rem; margin-bottom: 0.7rem; }" in css
    assert "h1 { margin: 0 0 0.15rem; font-size: var(--text-xl);" in css
    assert "gap: 0.5rem 0.75rem;" in css


def test_save_local_ae_and_remote_via_forms(client) -> None:
    response = client.post(
        "/config/local",
        data={
            "ae_title": "WORKSTA",
            "host": "127.0.0.1",
            "hostname": "workstation1",
            "port": "11113",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"WORKSTA" in response.content

    added = client.post(
        "/config/remotes",
        data={
            "name": "Lab PACS",
            "ae_title": "LABPACS",
            "hostname": "pacs.lab.local",
            "host": "10.0.0.5",
            "port": "104",
            "notes": "test VLAN",
        },
        follow_redirects=True,
    )
    assert added.status_code == 200
    assert b"LABPACS" in added.content
    assert b"10.0.0.5:104" in added.content


def test_add_virtual_ae_via_form(client) -> None:
    added = client.post(
        "/config/identities",
        data={"name": "CT scanner 1", "ae_title": "CT1", "modality": "CT"},
        follow_redirects=True,
    )
    assert added.status_code == 200
    assert b"CT1" in added.content
    assert b"CT scanner 1" in added.content
    worklist = client.get("/worklist")
    assert b"Present as" in worklist.content
    assert b"CT scanner 1" in worklist.content
    assert b"CT1" in worklist.content


def test_reject_oversized_ae_title(client) -> None:
    response = client.post(
        "/config/local",
        data={
            "ae_title": "THIS_TITLE_IS_TOO_LONG",
            "host": "0.0.0.0",
            "port": "11112",
            "timeout_seconds": "10",
            "max_pdu": "16382",
            "implementation_version": "DICOMM_1",
        },
    )
    assert response.status_code == 400
    assert b"AE Title" in response.content or b"ae_title" in response.content


def test_json_api_config_tools_and_run(client, store) -> None:
    listed = client.get("/api/tools").json()
    ids = {item["id"] for item in listed}
    assert {"ping", "c-echo", "mwl-find", "c-store", "pdf-store", "c-find", "hl7-send"} <= ids

    remote = RemoteNode(name="api node", ae_title="APINODE", host="127.0.0.1", port=9)
    created = client.post("/api/remotes", json=remote.model_dump(mode="json"))
    assert created.status_code == 201
    remote_id = created.json()["id"]

    ping = client.post("/api/tools/ping/run", json={"remote_id": remote_id})
    assert ping.status_code == 200
    body = ping.json()
    assert body["tool_id"] == "ping"
    assert body["ok"] is False
    assert any(step["name"] == "TCP port" for step in body["steps"])

    missing = client.post("/api/tools/ping/run", json={"remote_id": "nope"})
    assert missing.status_code == 400
