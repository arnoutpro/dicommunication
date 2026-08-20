from __future__ import annotations

import socket
import threading
from pathlib import Path

from app.hl7 import (
    DEFAULT_PORT,
    display_hl7,
    msh_control_id,
    msh_field,
    msa_ack_code,
    normalize_hl7,
    obr_reason,
    obr_status,
    orc_order_control,
    orc_status,
    orc_transaction_time,
    sample_adt_a01,
    send_hl7,
    send_wire_hints,
    stamp_new_control_id,
    stamp_obr_reason_ce_text,
    stamp_obr_status,
    stamp_orc_status,
    stamp_orc_transaction_time,
    stamp_order_control,
    unwrap_mllp,
    wrap_mllp,
    MLLP_END,
)
from app.models import Hl7Message, LocalAE
from app.store import ConfigStore
from app.tools.hl7 import Hl7SendTool


def test_normalize_and_mllp_roundtrip() -> None:
    pasted = "MSH|^~\\&|A|B\nEVN|A01\n"
    normalized = normalize_hl7(pasted)
    assert normalized == "MSH|^~\\&|A|B\rEVN|A01"
    framed = wrap_mllp(normalized.encode("latin-1"))
    assert framed.startswith(b"\x0b")
    assert framed.endswith(b"\x1c\x0d")
    assert unwrap_mllp(framed).decode("latin-1") == normalized
    assert display_hl7(normalized) == "MSH|^~\\&|A|B\nEVN|A01"


def test_msa_ack_code() -> None:
    ack = "MSH|^~\\&|R|F\rMSA|AA|MSG00001"
    assert msa_ack_code(ack) == "AA"
    assert msa_ack_code("MSH|^~\\&|R|F\rMSA|AE|MSG00001|bad") == "AE"
    assert msa_ack_code("MSH|^~\\&|R|F") is None


def test_sample_adt_starts_with_msh() -> None:
    message = sample_adt_a01(timestamp="20260101000000")
    assert message.startswith("MSH|")
    assert "ADT^A01" in message
    assert "ARNPRO-TEST" in message


def _serve_mllp_once(ack: str) -> tuple[int, dict, threading.Thread, socket.socket]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    received: dict[str, bytes] = {}

    def run() -> None:
        conn, _addr = server.accept()
        with conn:
            data = b""
            while MLLP_END not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            received["raw"] = data
            conn.sendall(wrap_mllp(ack.encode("latin-1")))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, received, thread, server


def test_send_hl7_receives_aa_ack() -> None:
    message = sample_adt_a01(timestamp="20260101000000")
    ack = "MSH|^~\\&|RECV|FAC|DICOMM|ARNPRO|20260101000001||ACK^A01|A1|P|2.5\rMSA|AA|MSG00001"
    port, received, thread, server = _serve_mllp_once(ack)
    try:
        reply = send_hl7("127.0.0.1", port, message, timeout=2)
        thread.join(timeout=2)
        assert msa_ack_code(reply) == "AA"
        assert unwrap_mllp(received["raw"]).decode("latin-1").startswith("MSH|")
    finally:
        server.close()


def test_hl7_tool_pass_and_ae(store: ConfigStore) -> None:
    tool = Hl7SendTool()
    local = LocalAE(timeout_seconds=2)
    message = sample_adt_a01(timestamp="20260101000000")
    ack = "MSH|^~\\&|R|F\rMSA|AA|MSG00001"
    port, _received, thread, server = _serve_mllp_once(ack)
    try:
        result = tool.run(
            local,
            None,
            {"host": "127.0.0.1", "port": port, "message": display_hl7(message)},
        )
        thread.join(timeout=2)
        assert result.ok
        assert "ACK AA" in result.summary
        assert result.remote_name == f"127.0.0.1:{port}"
    finally:
        server.close()

    closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    closed.bind(("127.0.0.1", 0))
    unused = closed.getsockname()[1]
    closed.close()
    failed = tool.run(local, None, {"host": "127.0.0.1", "port": unused, "message": message})
    assert failed.ok is False
    assert "Could not send" in failed.summary

    ae_ack = "MSH|^~\\&|R|F\rMSA|AE|MSG00001|unknown patient"
    port, _received, thread, server = _serve_mllp_once(ae_ack)
    try:
        rejected = tool.run(
            local,
            None,
            {"host": "127.0.0.1", "port": port, "message": message},
        )
        thread.join(timeout=2)
        assert rejected.ok is False
        assert "ACK AE" in rejected.summary
    finally:
        server.close()


def test_hl7_tool_rejects_empty_and_non_msh() -> None:
    tool = Hl7SendTool()
    local = LocalAE()
    empty = tool.run(local, None, {"host": "127.0.0.1", "port": DEFAULT_PORT, "message": "  "})
    assert empty.ok is False
    assert "Paste" in empty.summary
    junk = tool.run(local, None, {"host": "127.0.0.1", "port": 2575, "message": "hello"})
    assert junk.ok is False
    assert "MSH" in junk.summary
    no_host = tool.run(local, None, {"message": sample_adt_a01()})
    assert no_host.ok is False
    assert "host" in no_host.summary.lower()


def test_hl7_page_and_saved_messages(client, store) -> None:
    page = client.get("/tools/hl7-send")
    assert page.status_code == 200
    assert b"HL7 send" in page.content
    assert b"MSH|" in page.content
    assert b"not a message analyzer" in page.content
    assert b"Saved messages" in page.content

    saved = client.post(
        "/tools/hl7-send/messages",
        data={"name": "ADT test", "message": sample_adt_a01(), "port": "2575", "mllp": "mllp"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    messages = store.list_hl7_messages()
    assert len(messages) == 1
    assert messages[0].name == "ADT test"
    loaded = client.get(f"/tools/hl7-send?load={messages[0].id}")
    assert loaded.status_code == 200
    assert b"ADT test" in loaded.content
    assert b"ARNPRO-TEST" in loaded.content

    listed = client.get("/api/hl7/messages").json()
    assert listed[0]["name"] == "ADT test"
    deleted = client.delete(f"/api/hl7/messages/{messages[0].id}")
    assert deleted.status_code == 200
    assert store.list_hl7_messages() == []


def test_hl7_form_and_api_send(client) -> None:
    message = sample_adt_a01(timestamp="20260101000000")
    ack = "MSH|^~\\&|R|F\rMSA|AA|MSG00001"
    port, _received, thread, server = _serve_mllp_once(ack)
    try:
        posted = client.post(
            "/tools/hl7-send/run",
            data={
                "host": "127.0.0.1",
                "port": str(port),
                "message": display_hl7(message),
                "mllp": "mllp",
            },
        )
        thread.join(timeout=2)
        assert posted.status_code == 200
        assert b"ACK AA" in posted.content
        assert b"Pass" in posted.content
    finally:
        server.close()

    port, _received, thread, server = _serve_mllp_once(ack)
    try:
        api = client.post(
            "/api/tools/hl7-send/run",
            json={
                "options": {
                    "host": "127.0.0.1",
                    "port": port,
                    "message": message,
                    "mllp": True,
                }
            },
        )
        thread.join(timeout=2)
        assert api.status_code == 200
        body = api.json()
        assert body["ok"] is True
        assert body["tool_id"] == "hl7-send"
        assert "ACK AA" in body["summary"]
    finally:
        server.close()


def test_hl7_message_preview() -> None:
    item = Hl7Message(name="x", body="MSH|^~\\&|A\rEVN|A01")
    assert item.preview.startswith("MSH|")


def _orm(
    *,
    orc: str = "NW",
    reason: str = "arnout.pro SEH",
    status: str = "COMPLETED",
    control_id: str = "MSG00001",
) -> str:
    fields = ["OBR"] + [""] * 31
    fields[1] = "1"
    fields[2] = "7205625601^CS"
    fields[25] = status
    fields[31] = reason
    return (
        f"MSH|^~\\&|A|B|||20260101000000||ORM^O01|{control_id}|P|2.5\r"
        f"ORC|{orc}|7205625601^CS\r"
        + "|".join(fields)
    )


def test_stamp_new_control_id_and_obr_31() -> None:
    original = sample_adt_a01(timestamp="20260101000000")
    assert msh_control_id(original) == "MSG00001"
    stamped = stamp_new_control_id(original, control_id="D999", timestamp="20260102000000")
    assert msh_control_id(stamped) == "D999"
    assert stamped.split("\r")[0].split("|")[6] == "20260102000000"

    short = "MSH|^~\\&|A|B\rORC|NW|ORD1\rOBR|1|ORD1|||CTCHEST"
    count, reason = obr_reason(short)
    assert reason is None
    assert count < 31
    assert orc_order_control(short) == "NW"
    obr = "|".join(["OBR"] + [""] * 30 + ["follow-up CT"])
    long = f"MSH|^~\\&|A|B\rORC|XO|ORD1\r{obr}"
    count, reason = obr_reason(long)
    assert count == 31
    assert reason == "follow-up CT"
    assert orc_order_control(long) == "XO"


def test_stamp_order_control_and_obr_reason_ce() -> None:
    original = _orm()
    assert orc_order_control(original) == "NW"
    changed = stamp_order_control(original, "XO")
    assert orc_order_control(changed) == "XO"
    assert orc_order_control(original) == "NW"
    assert stamp_order_control("MSH|^~\\&|A|B\rPID|||X") == "MSH|^~\\&|A|B\rPID|||X"

    _, reason = obr_reason(original)
    assert reason == "arnout.pro SEH"
    encoded = stamp_obr_reason_ce_text(original)
    _, encoded_reason = obr_reason(encoded)
    assert encoded_reason == "arnout.pro^arnout.pro SEH"
    already = stamp_obr_reason_ce_text(encoded)
    _, again = obr_reason(already)
    assert again == "arnout.pro^arnout.pro SEH"
    caret_only = stamp_obr_reason_ce_text(_orm(reason="^arnout.pro SEH"))
    _, filled = obr_reason(caret_only)
    assert filled == "arnout.pro^arnout.pro SEH"
    assert obr_status(original) == "COMPLETED"
    timed = stamp_orc_transaction_time(original, timestamp="20260820120000")
    assert orc_transaction_time(timed) == "20260820120000"
    assert obr_status(stamp_obr_status(original, "SC")) == "SC"
    assert orc_status(stamp_orc_status(original, "IP")) == "IP"


def test_msh_field_sending_application() -> None:
    ack = "MSH|^~\\&|MIRTH|HOSP|DICOMM|ARNPRO|20260101000001||ACK^O01|A1|P|2.5\rMSA|AA|MSG00001"
    assert msh_field(ack, 3) == "MIRTH"
    assert msh_field(ack, 4) == "HOSP"
    assert msh_field(ack, 10) == "A1"
    assert msh_control_id(ack) == "A1"
    assert msh_field("PID|||1", 3) == ""


def test_send_wire_hints_for_repeat_new_order() -> None:
    hints = send_wire_hints(_orm())
    assert any("ORC-1 is still NW" in hint for hint in hints)
    assert any("OBR-31 has a space" in hint for hint in hints)
    assert any("OBR-25 is COMPLETED" in hint for hint in hints)
    quiet = send_wire_hints(_orm(orc="SC", reason="arnout.pro^arnout.pro SEH", status="SC"))
    assert not any("COMPLETED" in hint or "ORC-1 is still NW" in hint or "empty identifier" in hint for hint in quiet)
    assert any("IS Link" in hint and "Mirth" in hint for hint in quiet)
    assert not any("ORC-1 is XO" in hint for hint in quiet)
    xo = send_wire_hints(_orm(orc="XO", reason="arnout.pro^arnout.pro SEH", status="SC"))
    assert any("ORC-1 is XO" in hint for hint in xo)
    empty_id = send_wire_hints(_orm(orc="SC", reason="^arnout.pro SEH", status="SC"))
    assert any("empty identifier" in hint for hint in empty_id)
    mirth = send_wire_hints(
        _orm(orc="SC", reason="arnout.pro^arnout.pro SEH", status="SC"),
        ack="MSH|^~\\&|MIRTH|HOSP\rMSA|AA|MSG00001",
    )
    assert any("ACK MSH-3 is MIRTH" in hint and "IS Link stays empty" in hint for hint in mirth)
    islink = send_wire_hints(
        _orm(orc="SC", reason="arnout.pro^arnout.pro SEH", status="SC"),
        ack="MSH|^~\\&|ISLINK|VUE\rMSA|AA|MSG00001",
    )
    assert any("looks like IS Link" in hint for hint in islink)
    vip = send_wire_hints(
        _orm(orc="SC", reason="arnout.pro^arnout.pro SEH", status="SC"),
        ack="MSH|^~\\&|RECVAPP|FAC\rMSA|AA|MSG00001",
        port=10010,
    )
    assert any("10010" in hint and "Queues & Notifications" in hint for hint in vip)
    mirth_vip = send_wire_hints(
        _orm(orc="SC", reason="arnout.pro^arnout.pro SEH", status="SC"),
        ack="MSH|^~\\&|MIRTH|HOSP\rMSA|AA|MSG00001",
        port=10010,
    )
    assert any("ACK MSH-3 is MIRTH" in hint and "10010" in hint for hint in mirth_vip)
    ibe = send_wire_hints(
        _orm(orc="SC", reason="arnout.pro^arnout.pro SEH", status="SC"),
        ack="MSH|^~\\&|IBE|VUE\rMSA|AA|MSG00001",
        port=10010,
    )
    assert any("Vue IBE" in hint for hint in ibe)
    scribe = send_wire_hints(
        _orm(orc="SC", reason="arnout.pro^arnout.pro SEH", status="SC"),
        ack="MSH|^~\\&|SCRIBE|FAC\rMSA|AA|MSG00001",
    )
    assert not any("Vue IBE" in hint for hint in scribe)


def test_hl7_tool_stamps_control_id_when_requested() -> None:
    tool = Hl7SendTool()
    local = LocalAE(timeout_seconds=2)
    message = sample_adt_a01(timestamp="20260101000000")
    ack = "MSH|^~\\&|R|F\rMSA|AA|MSG00001"
    port, received, thread, server = _serve_mllp_once(ack)
    try:
        result = tool.run(
            local,
            None,
            {
                "host": "127.0.0.1",
                "port": port,
                "message": message,
                "new_control_id": True,
            },
        )
        thread.join(timeout=2)
        assert result.ok
        sent = unwrap_mllp(received["raw"]).decode("latin-1")
        assert msh_control_id(sent) != "MSG00001"
        send_step = next(step for step in result.steps if step.name == "Send")
        assert "MSH-10" in send_step.message
        assert "MSG00001" not in send_step.message
    finally:
        server.close()


def test_hl7_tool_stamps_order_change_when_requested() -> None:
    tool = Hl7SendTool()
    local = LocalAE(timeout_seconds=2)
    message = _orm()
    ack = "MSH|^~\\&|R|F\rMSA|AA|MSG00001"
    port, received, thread, server = _serve_mllp_once(ack)
    try:
        result = tool.run(
            local,
            None,
            {
                "host": "127.0.0.1",
                "port": port,
                "message": message,
                "new_control_id": True,
                "change_order": True,
                "obr_reason_ce": True,
            },
        )
        thread.join(timeout=2)
        assert result.ok
        sent = unwrap_mllp(received["raw"]).decode("latin-1")
        assert msh_control_id(sent) != "MSG00001"
        assert orc_order_control(sent) == "XO"
        assert orc_transaction_time(sent)
        _, reason = obr_reason(sent)
        assert reason == "arnout.pro^arnout.pro SEH"
        send_step = next(step for step in result.steps if step.name == "Send")
        assert "ORC-1 XO" in send_step.message
        assert "OBR-25 COMPLETED" in send_step.message
        assert "ORC-9" in send_step.message
        assert any(step.name == "Hint" and "COMPLETED" in step.message for step in result.steps)
        assert "not a PACS update" in result.summary
    finally:
        server.close()


def test_hl7_tool_leaves_orc_when_change_order_off() -> None:
    tool = Hl7SendTool()
    local = LocalAE(timeout_seconds=2)
    message = _orm()
    ack = "MSH|^~\\&|R|F\rMSA|AA|MSG00001"
    port, received, thread, server = _serve_mllp_once(ack)
    try:
        result = tool.run(
            local,
            None,
            {"host": "127.0.0.1", "port": port, "message": message},
        )
        thread.join(timeout=2)
        assert result.ok
        sent = unwrap_mllp(received["raw"]).decode("latin-1")
        assert orc_order_control(sent) == "NW"
        assert msh_control_id(sent) == "MSG00001"
        assert any(step.name == "Hint" and "ORC-1 is still NW" in step.message for step in result.steps)
    finally:
        server.close()


def test_hl7_tool_stamps_orc_sc_for_vue_when_requested() -> None:
    tool = Hl7SendTool()
    local = LocalAE(timeout_seconds=2)
    message = _orm()
    ack = "MSH|^~\\&|R|F\rMSA|AA|MSG00001"
    port, received, thread, server = _serve_mllp_once(ack)
    try:
        result = tool.run(
            local,
            None,
            {
                "host": "127.0.0.1",
                "port": port,
                "message": message,
                "change_order": True,
                "orc_control": "SC",
            },
        )
        thread.join(timeout=2)
        assert result.ok
        sent = unwrap_mllp(received["raw"]).decode("latin-1")
        assert orc_order_control(sent) == "SC"
        send_step = next(step for step in result.steps if step.name == "Send")
        assert "ORC-1 SC" in send_step.message
        assert not any(step.name == "Hint" and "ORC-1 is XO" in step.message for step in result.steps)
    finally:
        server.close()


def test_hl7_tool_stamps_obr_in_progress_when_requested() -> None:
    tool = Hl7SendTool()
    local = LocalAE(timeout_seconds=2)
    message = _orm()
    ack = "MSH|^~\\&|R|F\rMSA|AA|MSG00001"
    port, received, thread, server = _serve_mllp_once(ack)
    try:
        result = tool.run(
            local,
            None,
            {
                "host": "127.0.0.1",
                "port": port,
                "message": message,
                "change_order": True,
                "obr_reason_ce": True,
                "obr_in_progress": True,
            },
        )
        thread.join(timeout=2)
        assert result.ok
        sent = unwrap_mllp(received["raw"]).decode("latin-1")
        assert orc_order_control(sent) == "XO"
        assert obr_status(sent) == "SC"
        assert orc_status(sent) == "IP"
        send_step = next(step for step in result.steps if step.name == "Send")
        assert "OBR-25 SC" in send_step.message
        assert "ORC-5 IP" in send_step.message
        assert not any(step.name == "Hint" and "COMPLETED" in step.message for step in result.steps)
        assert any(step.name == "Hint" and "Mirth" in step.message for step in result.steps)
        ack_step = next(step for step in result.steps if step.name == "ACK")
        assert "MSH-3 R" in ack_step.message
    finally:
        server.close()


def test_hl7_tool_names_mirth_ack_when_is_link_would_stay_empty() -> None:
    tool = Hl7SendTool()
    local = LocalAE(timeout_seconds=2)
    message = _orm(orc="SC", reason="arnout.pro^arnout.pro SEH", status="SC")
    ack = "MSH|^~\\&|MIRTH|HOSP\rMSA|AA|MSG00001"
    port, _received, thread, server = _serve_mllp_once(ack)
    try:
        result = tool.run(
            local,
            None,
            {
                "host": "127.0.0.1",
                "port": port,
                "message": message,
            },
        )
        thread.join(timeout=2)
        assert result.ok
        ack_step = next(step for step in result.steps if step.name == "ACK")
        assert "MSH-3 MIRTH" in ack_step.message
        assert any(
            step.name == "Hint" and "IS Link stays empty" in step.message for step in result.steps
        )
    finally:
        server.close()


def test_hl7_page_has_resend_hint(client) -> None:
    page = client.get("/tools/hl7-send")
    assert b'enctype="multipart/form-data"' in page.content
    assert b"New Message Control ID" in page.content
    assert b'name="change_order"' in page.content
    assert b"Change existing order" in page.content
    assert b'name="orc_control"' in page.content
    assert b"Vue / IS Link update order" in page.content
    assert b'name="obr_reason_ce"' in page.content
    assert b"OBR-31 as CE text" in page.content
    assert b'name="obr_in_progress"' in page.content
    assert b"Set OBR-25 to SC" in page.content
    assert b"ORC-1" in page.content
    assert b"OBR-31" in page.content
    assert b"Queues" in page.content
    assert b"ACK MSH-3" in page.content
    assert b"10010" in page.content
    assert b"2112" in page.content


def test_hl7_page_has_wrapping_segment_editor(client) -> None:
    page = client.get("/tools/hl7-send")
    assert page.status_code == 200
    assert b'data-hl7-editor' in page.content
    assert b'data-hl7-view="segments"' in page.content
    assert b'data-hl7-view="raw"' in page.content
    assert b'data-hl7-segments' in page.content
    assert b'wrap="soft"' in page.content
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text(
        encoding="utf-8"
    )
    body_css = css[css.index("textarea.hl7-body") : css.index("[data-hl7-editor]")]
    assert "white-space: pre-wrap;" in body_css
    assert "overflow-wrap: anywhere;" in body_css
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "function initHl7Editor" in js
