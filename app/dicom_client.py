"""Shared pynetdicom Application Entity helpers."""

from __future__ import annotations

from typing import Any

from pynetdicom import AE

from app.models import LocalAE


def make_ae(local: LocalAE) -> AE:
    ae = AE(ae_title=local.ae_title)
    ae.maximum_pdu_size = local.max_pdu
    ae.acse_timeout = local.timeout_seconds
    ae.dimse_timeout = local.timeout_seconds
    ae.network_timeout = local.timeout_seconds
    if local.implementation_version:
        ae.implementation_version_name = local.implementation_version
    return ae


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
