"""Send a pasted or saved HL7 v2 message over TCP (MLLP by default)."""

from __future__ import annotations

import time
from typing import Any

from app.hl7 import (
    DEFAULT_PORT,
    MAX_MESSAGE_CHARS,
    display_hl7,
    msa_ack_code,
    normalize_hl7,
    send_hl7,
    use_mllp,
)
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool
from app.tools.registry import register


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


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
        steps.append(
            ToolStep(
                name="Send",
                ok=True,
                message=f"Sent {len(normalized)} characters to {host}:{port} ({framing})",
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
