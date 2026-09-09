from __future__ import annotations

import json

from app.models import Hl7Message, LocalAE, RemoteNode, RouteMatch, RouteRule, RouteRun, VirtualAE, WorklistEntry
from app.store import ConfigStore


def test_store_creates_default_config(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    config = store.load()
    assert config.local.ae_title == "DICOMM"
    assert (tmp_path / "config.json").exists()


def test_store_roundtrip_local_and_remote(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.save_local(LocalAE(ae_title="WORKSTATION1", host="127.0.0.1", hostname="ws1", port=11113))
    remote = RemoteNode(name="Orthanc", ae_title="ORTHANC", hostname="orthanc.lab", host="10.1.2.3", port=4242)
    store.add_remote(remote)

    reloaded = ConfigStore(tmp_path).load()
    assert reloaded.local.ae_title == "WORKSTATION1"
    assert reloaded.local.port == 11113
    assert reloaded.local.hostname == "ws1"
    assert len(reloaded.remotes) == 1
    assert reloaded.remotes[0].ae_title == "ORTHANC"
    assert reloaded.remotes[0].hostname == "orthanc.lab"

    store.update_remote(remote.id, RemoteNode(name="Orthanc lab", ae_title="ORTHANC", host="10.1.2.3", port=4242))
    store.delete_remote(remote.id)
    assert store.load().remotes == []


def test_store_roundtrip_virtual_ae(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    identity = VirtualAE(name="CT scanner 1", ae_title="CT1", modality="CT", notes="room 4")
    store.add_identity(identity)
    reloaded = ConfigStore(tmp_path).load()
    assert len(reloaded.identities) == 1
    assert reloaded.identities[0].ae_title == "CT1"
    assert reloaded.identities[0].scheduled_station_ae_title == "CT1"
    store.update_identity(
        identity.id,
        VirtualAE(name="CT scanner 1", ae_title="CT1", station_ae_title="CTROOM1"),
    )
    assert store.load().identities[0].scheduled_station_ae_title == "CTROOM1"
    store.delete_identity(identity.id)
    assert store.load().identities == []


def test_store_roundtrip_hl7_messages(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    stored = store.add_hl7_message(Hl7Message(name="ADT", body="MSH|^~\\&|A|B"))
    listed = ConfigStore(tmp_path).list_hl7_messages()
    assert len(listed) == 1
    assert listed[0].id == stored.id
    assert listed[0].name == "ADT"
    assert store.get_hl7_message(stored.id) is not None
    store.delete_hl7_message(stored.id)
    assert store.list_hl7_messages() == []
    assert store.get_hl7_message(stored.id) is None


def test_store_roundtrip_route_rules(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    rule = store.add_route_rule(RouteRule(name="Nightly CT", source_remote_id="pacs1", modality="CT"))

    reloaded = ConfigStore(tmp_path).list_route_rules()
    assert len(reloaded) == 1
    assert reloaded[0].id == rule.id
    assert reloaded[0].modality == "CT"
    assert store.get_route_rule(rule.id) is not None
    assert store.get_route_rule("missing") is None

    updated = store.update_route_rule(
        rule.id, RouteRule(name="Nightly CT/MR", source_remote_id="pacs1", modality="CT,MR")
    )
    assert updated.name == "Nightly CT/MR"
    assert updated.created_at == rule.created_at

    store.delete_route_rule(rule.id)
    assert store.list_route_rules() == []


def test_update_route_rule_missing_raises(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    try:
        store.update_route_rule("missing", RouteRule(name="X", source_remote_id="pacs1"))
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_save_route_rule_run_state_preserves_edit_fields(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    rule = store.add_route_rule(RouteRule(name="Nightly CT", source_remote_id="pacs1", modality="CT"))

    rule.mark_seen(["1.2.3"])
    from datetime import datetime, timezone

    rule.last_run_at = datetime.now(timezone.utc)
    rule.last_run_ok = True
    saved = store.save_route_rule_run_state(rule)

    reloaded = store.get_route_rule(rule.id)
    assert reloaded.seen_study_uids == ["1.2.3"]
    assert reloaded.last_run_ok is True
    assert reloaded.modality == "CT"
    assert saved.seen_study_uids == ["1.2.3"]


def test_store_roundtrip_route_runs(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    rule = store.add_route_rule(RouteRule(name="Nightly CT", source_remote_id="pacs1"))
    run = store.add_route_run(
        RouteRun(
            rule_id=rule.id,
            rule_name=rule.name,
            matched_count=2,
            new_count=1,
            matches=[RouteMatch(study_instance_uid="1.2.3", patient_id="42")],
        )
    )

    all_runs = ConfigStore(tmp_path).list_route_runs()
    assert len(all_runs) == 1
    assert all_runs[0].id == run.id
    assert all_runs[0].matches[0].study_instance_uid == "1.2.3"

    scoped = store.list_route_runs(rule_id=rule.id)
    assert len(scoped) == 1
    assert store.list_route_runs(rule_id="other-rule") == []


def test_unparsable_config_falls_back_to_defaults(tmp_path) -> None:
    (tmp_path / "config.json").write_text("{ not json at all", encoding="utf-8")
    store = ConfigStore(tmp_path)

    config = store.load()

    assert config.local.ae_title == "DICOMM"
    assert config.remotes == []
    backups = list(tmp_path.glob("config.corrupt-*.json"))
    assert len(backups) == 1, "the unreadable file should be kept for recovery"
    assert backups[0].read_text(encoding="utf-8") == "{ not json at all"


def test_schema_invalid_config_falls_back_to_defaults(tmp_path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"local": {"ae_title": "X", "port": 999999}}), encoding="utf-8"
    )
    store = ConfigStore(tmp_path)

    assert store.load().local.port == 11112
    assert list(tmp_path.glob("config.corrupt-*.json"))


def test_config_is_rewritten_after_a_fallback(tmp_path) -> None:
    (tmp_path / "config.json").write_text("]]]", encoding="utf-8")
    store = ConfigStore(tmp_path)
    store.load()

    store.add_remote(RemoteNode(name="PACS", ae_title="PACS", host="10.0.0.1", port=104))

    assert [r.ae_title for r in ConfigStore(tmp_path).load().remotes] == ["PACS"]


def test_one_bad_record_does_not_hide_the_rest(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.add_worklist_entry(WorklistEntry(patient_name="DOE^JANE", patient_id="1"))
    raw = json.loads((tmp_path / "worklist.json").read_text(encoding="utf-8"))
    raw.append({"patient_name": "NO^ID"})  # patient_id is required
    raw.append("not even an object")
    (tmp_path / "worklist.json").write_text(json.dumps(raw), encoding="utf-8")

    entries = ConfigStore(tmp_path).list_worklist()

    assert [entry.patient_id for entry in entries] == ["1"]


def test_unparsable_worklist_does_not_raise(tmp_path) -> None:
    (tmp_path / "worklist.json").write_text("<<<not json>>>", encoding="utf-8")
    store = ConfigStore(tmp_path)

    assert store.list_worklist() == []

    entry = store.add_worklist_entry(WorklistEntry(patient_name="A^B", patient_id="7"))
    assert [item.id for item in store.list_worklist()] == [entry.id]
