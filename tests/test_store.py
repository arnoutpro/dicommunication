from __future__ import annotations

from app.models import LocalAE, RemoteNode
from app.store import ConfigStore


def test_store_creates_default_config(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    config = store.load()
    assert config.local.ae_title == "DICOMM"
    assert (tmp_path / "config.json").exists()


def test_store_roundtrip_local_and_remote(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.save_local(LocalAE(ae_title="WORKSTATION1", host="127.0.0.1", port=11113))
    remote = RemoteNode(name="Orthanc", ae_title="ORTHANC", host="10.1.2.3", port=4242)
    store.add_remote(remote)

    reloaded = ConfigStore(tmp_path).load()
    assert reloaded.local.ae_title == "WORKSTATION1"
    assert reloaded.local.port == 11113
    assert len(reloaded.remotes) == 1
    assert reloaded.remotes[0].ae_title == "ORTHANC"

    store.update_remote(remote.id, RemoteNode(name="Orthanc lab", ae_title="ORTHANC", host="10.1.2.3", port=4242))
    store.delete_remote(remote.id)
    assert store.load().remotes == []
