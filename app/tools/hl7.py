"""Send a pasted or saved HL7 v2 message over TCP (MLLP by default)."""

from __future__ import annotations

import time
from typing import Any

from app.hl7 import (
    DEFAULT_PORT,
    MAX_MESSAGE_CHARS,
    display_hl7,
    latin1_replaced,
    msh_control_id,
    msa_ack_code,
    normalize_hl7,
    obr_reason,
    obr_status,
    orc_order_control,
    orc_status,
    orc_transaction_time,
    send_hl7,
    send_wire_hints,
    stamp_new_control_id,
    stamp_obr_reason_ce_text,
    stamp_obr_status,
    stamp_orc_status,
    stamp_orc_transaction_time,
    stamp_order_control,
    use_mllp,
)
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool
from app.tools.registry import register


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _flag(value: object, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _send_notes(message: str) -> str:
    bits: list[str] = []
    control_id = msh_control_id(message)
    if control_id:
        bits.append(f"MSH-10 {control_id}")
    order_control = orc_order_control(message)
    if order_control:
        bits.append(f"ORC-1 {order_control}")
    status_code = orc_status(message)
    if status_code:
        bits.append(f"ORC-5 {status_code}")
    txn = orc_transaction_time(message)
    if txn:
        bits.append(f"ORC-9 {txn}")
    status = obr_status(message)
    if status:
        bits.append(f"OBR-25 {status}")
    obr_fields, reason = obr_reason(message)
    if obr_fields:
        if reason is None:
            bits.append(f"OBR has {obr_fields} fields (no OBR-31)")
        else:
            bits.append(f"OBR-31 {reason or '(empty)'}")
    return f" · {' · '.join(bits)}" if bits else ""


class Hl7SendTool(BaseTool):
    id = "hl7-send"
    name = "HL7 send"
    description = (
        "Paste an HL7 v2 message and send it to a host:port over TCP. "
        "MLLP framing is on by default. This is a sender, not a message analyzer."
    )
    category = "hl7"
    requires_remote = False
    template = "hl7.html"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        options = options or {}
        started = time.perf_counter()
        host = str(options.get("host") or "").strip()
        if not host and remote is not None:
            host = remote.connect_host
        raw_port = options.get("port", DEFAULT_PORT)
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Port must be a number between 1 and 65535.",
            )
        if port < 1 or port > 65535:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Port must be a number between 1 and 65535.",
            )
        endpoint = f"{host}:{port}" if host else ""
        message = str(options.get("message") or "")
        mllp = use_mllp(options.get("mllp"), default=True)
        timeout = float(options.get("timeout") or local.timeout_seconds)
        new_control_id = _flag(options.get("new_control_id"), default=False)
        change_order = _flag(options.get("change_order"), default=False)
        obr_reason_ce = _flag(options.get("obr_reason_ce"), default=False)
        obr_in_progress = _flag(options.get("obr_in_progress"), default=False)
        orc_control = str(options.get("orc_control") or "XO").strip().upper()
        if orc_control not in {"XO", "SC", "XX", "CA"}:
            orc_control = "XO"

        if not host:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Enter a host (or pick a remote node for its host).",
            )
        normalized = normalize_hl7(message)
        if not normalized:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Paste an HL7 v2 message first.",
                remote_name=endpoint,
            )
        if len(normalized) > MAX_MESSAGE_CHARS:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=f"Message is larger than {MAX_MESSAGE_CHARS} characters.",
                remote_name=endpoint,
            )
        if not normalized.startswith("MSH"):
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="HL7 v2 messages start with an MSH segment.",
                remote_name=endpoint,
            )
        if new_control_id:
            normalized = stamp_new_control_id(normalized)
        if change_order:
            normalized = stamp_order_control(normalized, orc_control)
            normalized = stamp_orc_transaction_time(normalized)
        if obr_reason_ce:
            normalized = stamp_obr_reason_ce_text(normalized)
        if obr_in_progress:
            normalized = stamp_obr_status(normalized, "SC")
            normalized = stamp_orc_status(normalized, "IP")

        steps: list[ToolStep] = []
        connect_started = time.perf_counter()
        try:
            ack = send_hl7(host, port, normalized, timeout=timeout, mllp=mllp)
        except OSError as exc:
            steps.append(
                ToolStep(
                    name="TCP",
                    ok=False,
                    message=f"{host}:{port} — {exc}",
                    duration_ms=_elapsed_ms(connect_started),
                )
            )
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary=f"Could not send to {host}:{port}: {exc}",
                remote_id=remote.id if remote else None,
                remote_name=remote.name if remote else endpoint,
                duration_ms=_elapsed_ms(started),
                steps=steps,
            )

        framing = "MLLP" if mllp else "raw TCP"
        notes = _send_notes(normalized)
        if latin1_replaced(normalized):
            steps.append(
                ToolStep(
                    name="Encoding",
                    ok=True,
                    message="Non-Latin-1 characters were replaced with '?' before sending.",
                )
            )
        steps.append(
            ToolStep(
                name="Send",
                ok=True,
                message=f"Sent {len(normalized)} characters to {host}:{port} ({framing}){notes}",
                duration_ms=_elapsed_ms(connect_started),
                details={"output": display_hl7(normalized)},
            )
        )
        ack_code = msa_ack_code(ack)
        if ack.strip():
            ack_ok = ack_code in {None, "AA", "CA"}
            if ack_code in {"AA", "CA"}:
                ack_message = f"ACK {ack_code} from {host}:{port}"
            elif ack_code:
                ack_message = f"ACK {ack_code} from {host}:{port}"
            else:
                ack_message = f"Response from {host}:{port} (no MSA segment)"
            steps.append(
                ToolStep(
                    name="ACK",
                    ok=ack_ok,
                    message=ack_message,
                    details={"output": display_hl7(ack), "msa": ack_code or ""},
                )
            )
            if ack_ok:
                summary = ack_message if ack_code else f"Sent to {host}:{port}; peer responded."
            else:
                summary = ack_message
        else:
            if mllp:
                steps.append(
                    ToolStep(
                        name="ACK",
                        ok=False,
                        message=f"No MLLP ACK from {host}:{port} before timeout.",
                    )
                )
                summary = f"Sent to {host}:{port}, but no ACK came back."
            else:
                steps.append(
                    ToolStep(
                        name="ACK",
                        ok=True,
                        message="No response (raw TCP does not require an ACK).",
                    )
                )
                summary = f"Sent to {host}:{port} over raw TCP."

        hints = send_wire_hints(normalized)
        for hint in hints:
            steps.append(ToolStep(name="Hint", ok=True, message=hint))
        if hints and ack.strip() and msa_ack_code(ack) in {"AA", "CA"}:
            summary = f"{summary}. An ACK is not a PACS update — see Hint."

        ok = bool(steps) and all(step.ok for step in steps)
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id if remote else None,
            remote_name=remote.name if remote else endpoint,
            duration_ms=_elapsed_ms(started),
            steps=steps,
            log=display_hl7(ack) if ack.strip() else "",
        )


register(Hl7SendTool())
