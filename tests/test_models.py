from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import LocalAE, RemoteNode, VirtualAE, normalize_ae_title


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
    assert local.storage_scp_enabled is False


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
    named = RemoteNode(name="PACS DNS", ae_title="PACS1", hostname="pacs.hospital.local")
    assert named.connect_host == "pacs.hospital.local"
    both = RemoteNode(
        name="PACS both",
        ae_title="PACS1",
        hostname="pacs.hospital.local",
        host="10.0.0.8",
    )
    assert both.connect_host == "10.0.0.8"
    with pytest.raises(ValidationError):
        RemoteNode(name="PACS", ae_title="PACS1")
    mwl = RemoteNode(name="RIS MWL", ae_title="RISMWL", host="10.0.0.9", kind="mwl")
    assert mwl.provides_mwl is True
    assert mwl.kind_label == "DMWL"


def test_virtual_ae_station_defaults_to_calling_ae() -> None:
    identity = VirtualAE(name="CT scanner 1", ae_title="CT1")
    assert identity.scheduled_station_ae_title == "CT1"
    named = VirtualAE(name="CT scanner 1", ae_title="CT1", station_ae_title="CTROOM1")
    assert named.scheduled_station_ae_title == "CTROOM1"


def test_calling_ae_overrides_workstation_title() -> None:
    from app.models import AppConfig

    config = AppConfig()
    identity = VirtualAE(name="MR1", ae_title="MR1", modality="MR")
    config.identities.append(identity)
    impersonated = config.calling_ae(identity.id)
    assert impersonated.ae_title == "MR1"
    assert impersonated.station_ae_title == "MR1"
    assert impersonated.port == config.local.port
    assert config.calling_ae(None).ae_title == "DICOMM"
    try:
        config.calling_ae("missing")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")
