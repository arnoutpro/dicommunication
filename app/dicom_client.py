"""Shared pynetdicom Application Entity helpers."""

from __future__ import annotations

import io
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, UID
from pynetdicom import AE

from app.models import LocalAE, RemoteNode

PREFERRED_TS = [ImplicitVRLittleEndian, ExplicitVRLittleEndian]


def make_ae(local: LocalAE) -> AE:
    ae = AE(ae_title=local.ae_title)
    ae.maximum_pdu_size = local.max_pdu
    ae.acse_timeout = local.timeout_seconds
    ae.dimse_timeout = local.timeout_seconds
    ae.network_timeout = local.timeout_seconds
    if local.implementation_version:
        ae.implementation_version_name = local.implementation_version
    return ae


def uid_name(value: Any) -> str:
    try:
        return UID(str(value)).name
    except Exception:  # noqa: BLE001
        return str(value)


def context_rows(assoc: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for accepted, contexts in (
        (True, getattr(assoc, "accepted_contexts", None) or []),
        (False, getattr(assoc, "rejected_contexts", None) or []),
    ):
        for context in contexts:
            syntaxes = getattr(context, "transfer_syntax", None) or []
            transfer = str(syntaxes[0]) if syntaxes else ""
            rows.append(
                {
                    "abstract_syntax": str(getattr(context, "abstract_syntax", "")),
                    "name": uid_name(getattr(context, "abstract_syntax", "")),
                    "transfer_syntax": transfer,
                    "transfer_syntax_name": uid_name(transfer) if transfer else "",
                    "accepted": accepted,
                }
            )
    return rows


def rejected_sop_message(
    contexts: list[dict[str, Any]],
    requested: str,
    extra: str = "",
) -> str | None:
    if not contexts or any(row.get("accepted") for row in contexts):
        return None
    names = ", ".join(
        str(row.get("name") or row.get("abstract_syntax") or "SOP Class") for row in contexts
    )
    message = f"The peer did not accept {requested} ({names})."
    if extra:
        return f"{message} {extra}"
    return message


def associate(
    local: LocalAE,
    remote: RemoteNode,
    abstract_syntaxes: list[Any],
    transfer_syntaxes: list[Any] | None = None,
):
    ae = make_ae(local)
    syntaxes = list(transfer_syntaxes) if transfer_syntaxes else PREFERRED_TS
    for syntax in abstract_syntaxes:
        ae.add_requested_context(syntax, syntaxes)
    assoc = ae.associate(remote.connect_host, remote.port, ae_title=remote.ae_title)
    return ae, assoc


@contextmanager
def capture_pynetdicom_log() -> Iterator[io.StringIO]:
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("pynetdicom")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield log_stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def reject_reason(assoc: Any) -> str:
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
    return "Association rejected, aborted, or the host did not accept a DICOM connection."
