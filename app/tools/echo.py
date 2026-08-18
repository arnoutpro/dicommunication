"""DICOM Verification SCU: associate and send C-ECHO."""

from __future__ import annotations

import io
import logging
import time
from typing import Any

from pynetdicom import AE
from pynetdicom.sop_class import Verification

from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool
from app.tools.registry import register


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


class CEchoTool(BaseTool):
    id = "c-echo"
    name = "C-ECHO"
    description = (
        "Establish a DICOM association with the remote AE and send a Verification "
        "(C-ECHO) request. This is the standard application-level connectivity check."
    )
    category = "dimse"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        if remote is None:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Select a remote DICOM node first.",
            )

        started = time.perf_counter()
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.INFO)
        logger = logging.getLogger("pynetdicom")
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        steps: list[ToolStep] = []
        assoc = None
        try:
            assoc_started = time.perf_counter()
            ae = AE(ae_title=local.ae_title)
            ae.maximum_pdu_size = local.max_pdu
            ae.acse_timeout = local.timeout_seconds
            ae.dimse_timeout = local.timeout_seconds
            ae.network_timeout = local.timeout_seconds
            if local.implementation_version:
                ae.implementation_version_name = local.implementation_version
            ae.add_requested_context(Verification)

            assoc = ae.associate(remote.connect_host, remote.port, ae_title=remote.ae_title)
            details = self._assoc_details(assoc, local, remote)

            if not assoc.is_established:
                steps.append(
                    ToolStep(
                        name="Association",
                        ok=False,
                        message=self._reject_reason(assoc),
                        duration_ms=_elapsed_ms(assoc_started),
                        details=details,
                    )
                )
            else:
                steps.append(
                    ToolStep(
                        name="Association",
                        ok=True,
                        message=(
                            f"Associated {local.ae_title} → "
                            f"{remote.ae_title}@{remote.connect_host}:{remote.port}"
                        ),
                        duration_ms=_elapsed_ms(assoc_started),
                        details=details,
                    )
                )

                echo_started = time.perf_counter()
                status = assoc.send_c_echo()
                if status:
                    code = int(status.Status)
                    echo_ok = code == 0x0000
                    steps.append(
                        ToolStep(
                            name="C-ECHO",
                            ok=echo_ok,
                            message=(
                                f"DIMSE status 0x{code:04X} Success"
                                if echo_ok
                                else f"DIMSE status 0x{code:04X}"
                            ),
                            duration_ms=_elapsed_ms(echo_started),
                            details={"status": f"0x{code:04X}"},
                        )
                    )
                else:
                    steps.append(
                        ToolStep(
                            name="C-ECHO",
                            ok=False,
                            message="No C-ECHO response (timeout, abort, or invalid PDU).",
                            duration_ms=_elapsed_ms(echo_started),
                        )
                    )

                release_started = time.perf_counter()
                assoc.release()
                steps.append(
                    ToolStep(
                        name="Release",
                        ok=True,
                        message="Association released",
                        duration_ms=_elapsed_ms(release_started),
                    )
                )
        except Exception as exc:  # noqa: BLE001 — surface any association/network failure
            steps.append(
                ToolStep(
                    name="C-ECHO",
                    ok=False,
                    message=f"{type(exc).__name__}: {exc}",
                    duration_ms=_elapsed_ms(started),
                )
            )
        finally:
            if assoc is not None and getattr(assoc, "is_established", False):
                try:
                    assoc.abort()
                except Exception:  # noqa: BLE001
                    pass
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        ok = bool(steps) and all(step.ok for step in steps)
        if ok:
            echo_step = next((step for step in steps if step.name == "C-ECHO"), None)
            summary = (
                f"C-ECHO Success from {local.ae_title} to {remote.ae_title}"
                f"{f' in {echo_step.duration_ms} ms' if echo_step and echo_step.duration_ms is not None else ''}"
            )
        else:
            failed = next((step for step in steps if not step.ok), None)
            summary = failed.message if failed else "C-ECHO failed"

        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id,
            remote_name=remote.name,
            duration_ms=_elapsed_ms(started),
            steps=steps,
            log=log_stream.getvalue().strip(),
        )

    def _assoc_details(self, assoc: Any, local: LocalAE, remote: RemoteNode) -> dict[str, Any]:
        details: dict[str, Any] = {
            "calling_ae": local.ae_title,
            "called_ae": remote.ae_title,
            "host": remote.connect_host,
            "port": remote.port,
        }
        for attr in ("is_established", "is_rejected", "is_aborted"):
            if hasattr(assoc, attr):
                details[attr] = bool(getattr(assoc, attr))
        return details

    def _reject_reason(self, assoc: Any) -> str:
        try:
            if getattr(assoc, "is_rejected", False):
                primitive = getattr(getattr(assoc, "acceptor", None), "primitive", None)
                if primitive is not None:
                    result = getattr(primitive, "result_str", None) or getattr(
                        primitive, "result", "rejected"
                    )
                    diagnostic = getattr(primitive, "diagnostic_str", None) or getattr(
                        primitive, "diagnostic", ""
                    )
                    return f"Association rejected: {result} {diagnostic}".strip()
                return "Association rejected by the remote AE."
            if getattr(assoc, "is_aborted", False):
                return "Association aborted before it was established."
        except Exception:  # noqa: BLE001
            pass
        return (
            "Association rejected, aborted, or the host did not accept a DICOM connection."
        )


register(CEchoTool())
