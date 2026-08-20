"""HL7 v2 sender: MLLP framing over TCP. This is not a message parser."""

from __future__ import annotations

import socket
import uuid
from datetime import datetime, timezone

MLLP_START = b"\x0b"
MLLP_END = b"\x1c\x0d"
DEFAULT_PORT = 2575
MAX_MESSAGE_CHARS = 512_000


def sample_adt_a01(*, sending_app: str = "DICOMM", timestamp: str | None = None) -> str:
    """A tiny test ADT^A01 the user can edit. Not a production mapping."""
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return (
        f"MSH|^~\\&|{sending_app}|ARNPRO|RECVAPP|RECVFAC|{stamp}||ADT^A01|MSG00001|P|2.5\r"
        f"EVN|A01|{stamp}\r"
        "PID|||ARNPRO-TEST||TEST^DICOMMUNICATE||19800101|U"
    )


def normalize_hl7(text: str) -> str:
    """HL7 v2 segments are CR-separated. Accept pasted LF / CRLF in the textarea."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\r".join(lines)


def display_hl7(text: str) -> str:
    """Show one segment per line in the UI."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def wrap_mllp(payload: bytes) -> bytes:
    return MLLP_START + payload + MLLP_END


def unwrap_mllp(data: bytes) -> bytes:
    if data.startswith(MLLP_START):
        data = data[1:]
    end = data.find(MLLP_END)
    if end >= 0:
        data = data[:end]
    return data


def msa_ack_code(ack: str) -> str | None:
    """MSA-1 only (AA / AE / AR). Not a general HL7 parser."""
    for segment in (ack or "").replace("\n", "\r").split("\r"):
        if segment.startswith("MSA|") or segment.startswith("MSA^"):
            fields = segment.split("|")
            if len(fields) > 1 and fields[1].strip():
                return fields[1].strip()
        elif segment.startswith("MSA") and "|" in segment:
            fields = segment.split("|")
            if len(fields) > 1 and fields[1].strip():
                return fields[1].strip()
    return None


def encode_hl7(text: str) -> bytes:
    return normalize_hl7(text).encode("latin-1", errors="replace")


def decode_hl7(data: bytes) -> str:
    return data.decode("latin-1", errors="replace")


def latin1_replaced(text: str) -> bool:
    raw = normalize_hl7(text)
    try:
        raw.encode("latin-1")
    except UnicodeEncodeError:
        return True
    return False


def _segments(message: str) -> list[str]:
    return [seg for seg in normalize_hl7(message).split("\r") if seg]


def msh_field(message: str, number: int) -> str:
    """HL7 MSH-n. MSH-3 is sending application. Split index 0 is ``MSH``, so MSH-n is fields[n-1]."""
    if number < 2:
        return ""
    parts = _segments(message)
    if not parts or not parts[0].startswith("MSH"):
        return ""
    msh = parts[0]
    sep = msh[3] if len(msh) > 3 else "|"
    fields = msh.split(sep)
    index = number - 1
    return fields[index].strip() if len(fields) > index else ""


def msh_control_id(message: str) -> str:
    return msh_field(message, 10)


def new_message_control_id(*, timestamp: str | None = None) -> str:
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"D{stamp}{uuid.uuid4().hex[:3]}"


def stamp_new_control_id(
    message: str,
    *,
    control_id: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Replace MSH-10 (and MSH-7) so a resend is not treated as a duplicate."""
    segments = _segments(message)
    if not segments or not segments[0].startswith("MSH"):
        return normalize_hl7(message)
    msh = segments[0]
    sep = msh[3] if len(msh) > 3 else "|"
    fields = msh.split(sep)
    while len(fields) < 10:
        fields.append("")
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    while len(fields) < 7:
        fields.append("")
    fields[6] = stamp
    fields[9] = control_id or new_message_control_id(timestamp=stamp)
    segments[0] = sep.join(fields)
    return "\r".join(segments)


def orc_order_control(message: str) -> str:
    for seg in _segments(message):
        if seg.startswith("ORC|"):
            fields = seg.split("|")
            return fields[1].strip() if len(fields) > 1 else ""
    return ""


def stamp_order_control(message: str, code: str = "XO") -> str:
    """Set ORC-1 (order control). XO is change; NW is new. Does not invent an ORC."""
    return _set_field(message, "ORC|", 1, (code or "XO").strip() or "XO")


def orc_status(message: str) -> str:
    """ORC-5 (order status), if present."""
    return _get_field(message, "ORC|", 5)


def stamp_orc_status(message: str, status: str = "IP") -> str:
    """Set ORC-5. IP is in progress. Does not invent an ORC."""
    return _set_field(message, "ORC|", 5, (status or "IP").strip() or "IP")


def orc_transaction_time(message: str) -> str:
    """ORC-9 (date/time of transaction), if present."""
    return _get_field(message, "ORC|", 9)


def stamp_orc_transaction_time(message: str, *, timestamp: str | None = None) -> str:
    """Set ORC-9. Some engines ignore XO without a transaction time."""
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return _set_field(message, "ORC|", 9, stamp)


def obr_reason(message: str) -> tuple[int, str | None]:
    """OBR field count and OBR-31 (Reason for Study), if that field exists."""
    for seg in _segments(message):
        if seg.startswith("OBR|"):
            fields = seg.split("|")
            count = max(0, len(fields) - 1)
            value = fields[31] if len(fields) > 31 else None
            return count, value
    return 0, None


def obr_status(message: str) -> str:
    """OBR-25 (Result Status), if present."""
    return _get_field(message, "OBR|", 25)


def stamp_obr_status(message: str, status: str = "SC") -> str:
    """Set OBR-25. SC is in progress. Does not invent an OBR."""
    return _set_field(message, "OBR|", 25, (status or "SC").strip() or "SC")


def _ce_with_text(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw
    if "^" not in raw:
        ident = raw.split()[0]
        return f"{ident}^{raw}"
    parts = raw.split("^")
    ident = parts[0].strip()
    text = parts[1].strip() if len(parts) > 1 else ""
    if not ident and text:
        parts[0] = text.split()[0]
        return "^".join(parts)
    return raw


def stamp_obr_reason_ce_text(message: str) -> str:
    """Make OBR-31 a CE: identifier^text. Fills an empty identifier from the text."""
    segments = _segments(message)
    for index, seg in enumerate(segments):
        if not seg.startswith("OBR|"):
            continue
        fields = seg.split("|")
        if len(fields) <= 31:
            continue
        encoded = _ce_with_text(fields[31])
        if encoded != fields[31]:
            fields[31] = encoded
            segments[index] = "|".join(fields)
    return "\r".join(segments)


def _get_field(message: str, prefix: str, index: int) -> str:
    for seg in _segments(message):
        if not seg.startswith(prefix):
            continue
        fields = seg.split("|")
        return fields[index].strip() if len(fields) > index else ""
    return ""


def _set_field(message: str, prefix: str, index: int, value: str) -> str:
    segments = _segments(message)
    for i, seg in enumerate(segments):
        if not seg.startswith(prefix):
            continue
        fields = seg.split("|")
        while len(fields) <= index:
            fields.append("")
        fields[index] = value
        segments[i] = "|".join(fields)
    return "\r".join(segments)


# Philips Vue HL7_VIP (load-balancer front door). Not IS Link's process bind port.
VUE_HL7_VIP_PORTS = frozenset({10010, 4001, 4003, 4005})


def _ack_peer_kind(ack: str) -> str:
    """Best-effort label for who sent the ACK. Empty if unknown."""
    app_id = msh_field(ack, 3).split("^", 1)[0].strip().upper().replace(" ", "")
    fac_id = msh_field(ack, 4).split("^", 1)[0].strip().upper().replace(" ", "")
    blob = f"{app_id} {fac_id}"
    if "MIRTH" in blob or "NEXTGENCONNECT" in blob.replace(" ", ""):
        return "mirth"
    if app_id in {"ISLINK", "CSISLINK"} or "ISLINK" in app_id or fac_id in {"CARESTREAM"}:
        return "islink"
    if app_id == "IBE":
        return "ibe"
    return ""


def _routing_hint(message: str, ack: str = "", port: int | None = None) -> str | None:
    """Empty IS Link after ACK is routing, not OBR-31. None if this is not an order."""
    if not any(seg.startswith("OBR|") for seg in _segments(message)):
        return None
    ack_app = msh_field(ack, 3)
    peer_kind = _ack_peer_kind(ack) if ack.strip() else ""
    vip = port in VUE_HL7_VIP_PORTS if port is not None else False
    vip_note = (
        f" Port {port} is Philips Vue's usual HL7 VIP (10010/4001/4003/4005), not proof you hit IS Link's process."
        if vip
        else ""
    )
    if peer_kind == "mirth":
        return (
            f"ACK MSH-3 is {ack_app}. That is Mirth, not Vue.{vip_note} "
            "IS Link stays empty because this never reached the IS Link listener. "
            "In IS Link Configuration, read the Listener bind port. If it is not this port, send there. You do not need Mirth for that test."
        )
    if peer_kind == "ibe":
        return (
            f"ACK MSH-3 is {ack_app}. That looks like Vue IBE, not IS Link.{vip_note} "
            "IS Link stays empty until the ORM reaches the IS Link Listener process. "
            "In IS Link Configuration, read the Listener bind port and send there."
        )
    if peer_kind == "islink":
        return (
            f"ACK MSH-3 is {ack_app}. That looks like IS Link. If Incoming and Error are still empty, "
            "this is the wrong IS Link instance, a date/filter miss, or MSH-5/MSH-6 do not match what that listener accepts."
        )
    who = f" ACK MSH-3 is {ack_app}." if ack_app else ""
    if vip:
        return (
            f"Port {port} is Philips Vue's usual HL7 VIP (10010/4001/4003/4005), not IS Link's own bind port.{who} "
            "The ACK is from the VIP or whatever sits behind it (often Mirth or IBE). "
            "In IS Link Configuration, read the Listener process port. If it differs, send to that host:port. "
            "On the IS Link server, check which executable owns this port."
        )
    return (
        f"The ACK is from the TCP peer (often Mirth Connect), not Vue.{who} "
        "If IS Link's incoming queue is empty, put IS Link's Listener bind host and port in Host/Port and send again. "
        "You do not need Mirth for that test."
    )


def send_wire_hints(message: str, ack: str = "", port: int | None = None) -> list[str]:
    """Likely reasons an ACK AA still leaves PACS unchanged. Not a validator."""
    hints: list[str] = []
    order = orc_order_control(message)
    order_id = order.split("^", 1)[0].strip().upper()
    routing = _routing_hint(message, ack=ack, port=port)
    if routing:
        hints.append(routing)
    has_obr = any(seg.startswith("OBR|") for seg in _segments(message))
    if has_obr and not order:
        hints.append(
            "This message has an OBR but no ORC. Many PACS ignore an order change without ORC-1 XO or SC."
        )
    elif order_id == "NW":
        hints.append(
            "ORC-1 is still NW (new). Philips Vue / IS Link uses SC to update an order. Repeating NW is usually ACKed and skipped."
        )
    elif order_id == "XO":
        hints.append(
            "ORC-1 is XO. Philips Vue / IS Link updates an order with ORC-1 SC (NW = new, SC = update, CA = cancel). XO is often ACKed by Mirth and ignored by Vue."
        )
    _, reason = obr_reason(message)
    if reason and " " in reason.split("^", 1)[0] and "^" not in reason:
        hints.append(
            "OBR-31 has a space and no ^. Reason for Study is a CE (id^text). Turn on OBR-31 as CE text."
        )
    elif reason and reason.startswith("^"):
        hints.append(
            "OBR-31 has an empty identifier (^text). Many PACS read only the id. Turn on OBR-31 as CE text so it becomes id^text."
        )
    status = obr_status(message)
    status_id = status.split("^", 1)[0].strip().upper()
    if status_id in {"COMPLETED", "COMPLETE", "CM"}:
        hints.append(
            f"OBR-25 is {status}. Vue may still skip a finished exam. Set OBR-25 to SC (in progress) as a test — that is not the same as ORC-1 SC."
        )
    return hints


def use_mllp(value: object, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"0", "false", "off", "no", "raw"}:
        return False
    if text in {"1", "true", "on", "yes", "mllp"}:
        return True
    return default


def send_hl7(
    host: str,
    port: int,
    message: str,
    *,
    timeout: float = 10.0,
    mllp: bool = True,
) -> str:
    """Send ``message`` to ``host:port``. Return the ACK (possibly empty)."""
    payload = encode_hl7(message)
    framed = wrap_mllp(payload) if mllp else payload
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(framed)
        chunks = bytearray()
        while True:
            try:
                chunk = sock.recv(4096)
            except (TimeoutError, socket.timeout):
                break
            if not chunk:
                break
            chunks.extend(chunk)
            if mllp and MLLP_END in chunks:
                break
        raw = bytes(chunks)
        return decode_hl7(unwrap_mllp(raw) if mllp else raw)
