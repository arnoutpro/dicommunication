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


def msh_control_id(message: str) -> str:
    parts = _segments(message)
    if not parts or not parts[0].startswith("MSH"):
        return ""
    msh = parts[0]
    sep = msh[3] if len(msh) > 3 else "|"
    fields = msh.split(sep)
    return fields[9].strip() if len(fields) > 9 else ""


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


def obr_reason(message: str) -> tuple[int, str | None]:
    """OBR field count and OBR-31 (Reason for Study), if that field exists."""
    for seg in _segments(message):
        if seg.startswith("OBR|"):
            fields = seg.split("|")
            count = max(0, len(fields) - 1)
            value = fields[31] if len(fields) > 31 else None
            return count, value
    return 0, None


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
