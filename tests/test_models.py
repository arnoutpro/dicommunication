from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import LocalAE, RemoteNode, normalize_ae_title


def test_ae_title_strips_and_accepts_sixteen_chars() -> None:
    assert normalize_ae_title("  ORTHANC  ") == "ORTHANC"
    assert len(normalize_ae_title("A" * 16)) == 16


def test_ae_title_rejects_too_long_or_backslash() -> None:
    with pytest.raises(ValueError, match="16 characters"):
        normalize_ae_title("THIS_TITLE_IS_WAY_TOO_LONG")
    with pytest.raises(ValueError, match="backslash"):
        normalize_ae_title("BAD\\AE")


def test_local_ae_defaults() -> None:
    local = LocalAE()
    assert local.ae_title == "DICOMM"
    assert local.port == 11112
    assert local.timeout_seconds == 10.0


def test_local_ae_port_bounds() -> None:
    with pytest.raises(ValidationError):
        LocalAE(port=0)
    with pytest.raises(ValidationError):
        LocalAE(port=70000)


def test_remote_node_requires_name_and_host() -> None:
    with pytest.raises(ValidationError):
        RemoteNode(name="  ", ae_title="PACS", host="10.0.0.1")
    node = RemoteNode(name="PACS", ae_title="PACS1", host="10.0.0.8", port=104)
    assert node.endpoint == "10.0.0.8:104"
    mwl = RemoteNode(name="RIS MWL", ae_title="RISMWL", host="10.0.0.9", kind="mwl")
    assert mwl.provides_mwl is True
    assert mwl.kind_label == "DMWL"
