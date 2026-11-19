"""Settings export and import (app/settings_transfer.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import settings_transfer as st
from app.models import LocalAE, RemoteNode, RouteRule, SavedQuery, VirtualAE
from app.store import ConfigStore

ALL = st.SECTIONS


def _fill(store: ConfigStore) -> dict[str, str]:
    """A store with one of everything, rules wired to the remote node."""
    remote = store.add_remote(RemoteNode(name="PACS prod", ae_title="PACS", host="10.0.0.5", port=104, kind="pacs")).remotes[-1]
    dest = store.add_remote(RemoteNode(name="Archive", ae_title="ARCH", host="10.0.0.6", port=104)).remotes[-1]
    identity = store.add_identity(VirtualAE(name="CT room", ae_title="CT01", modality="CT")).identities[-1]
    store.save_local(LocalAE(ae_title="WORKSTN", port=11113))
    store.save_query(SavedQuery(name="Open exams", remote_id=remote.id, identity_id=identity.id, include=["PatientName"], values={"StudyDate": "20261101"}))
    rule = store.add_route_rule(
        RouteRule(name="Nightly", source_remote_id=remote.id, destination_remote_ids=[dest.id], modality="CT", export_dir="/data/out", seen_study_uids=["1.2.3"], status="active")
    )
    return {"remote": remote.id, "dest": dest.id, "identity": identity.id, "rule": rule.id}


def _import(target: ConfigStore, source: ConfigStore, sections=ALL):
    parsed = st.parse_settings(st.export_bytes(source, ALL))
    plan = st.plan_import(target, parsed, sections)
    return plan, st.apply_import(target, plan)


@pytest.fixture
def source(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "source")
    _fill(store)
    return store


@pytest.fixture
def target(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / "target")


# --- export --------------------------------------------------------------------


def test_export_holds_only_the_chosen_sections_and_no_run_state(source: ConfigStore) -> None:
    document = st.export_settings(source, (st.SECTION_REMOTES, st.SECTION_RULES))
    assert document["format"] == st.FORMAT and document["version"] == st.VERSION and document["exported_at"]
    assert set(document["sections"]) == {"remotes", "rules"}
    (rule,) = document["sections"]["rules"]
    for key in ("status", "seen_study_uids", "last_run_at", "next_run_at", "created_at"):
        assert key not in rule
    assert rule["name"] == "Nightly" and rule["export_dir"] == "/data/out"
    assert all("created_at" not in item for item in document["sections"]["remotes"])


def test_nothing_that_can_hold_patient_data_is_ever_exported(source: ConfigStore) -> None:
    document = st.export_settings(source, ALL)
    assert set(document["sections"]) <= set(st.SECTIONS)
    text = json.dumps(document)
    assert "results" not in document["sections"] and "worklist" not in text and "hl7" not in text.lower()


def test_defaults_leave_out_the_machine_specific_and_the_risky_sections() -> None:
    assert st.SECTION_LOCAL not in st.DEFAULT_EXPORT and st.SECTION_QUERIES not in st.DEFAULT_EXPORT
    assert st.SECTION_LOCAL not in st.DEFAULT_IMPORT


def test_section_counts_and_filename(source: ConfigStore) -> None:
    counts = st.section_counts(source)
    assert counts == {"remotes": 2, "identities": 1, "local": 1, "queries": 1, "rules": 1}
    from datetime import datetime

    assert st.export_filename(datetime(2026, 11, 19)) == "dicommunication-settings-2026-11-19.json"


# --- a round trip --------------------------------------------------------------


def test_everything_arrives_on_a_fresh_computer_with_working_links(source: ConfigStore, target: ConfigStore) -> None:
    ids = {r.name: r.id for r in source.load().remotes}
    plan, result = _import(target, source)
    assert (result.added, result.updated) == (5, 1)  # 2 nodes, 1 identity, 1 query, 1 rule are new; the local AE is replaced
    config = target.load()
    assert {r.name: r.id for r in config.remotes} == ids  # the same ids, so a re-import matches
    assert [i.name for i in config.identities] == ["CT room"]
    assert config.local.ae_title == "WORKSTN" and config.local.port == 11113
    (query,) = target.list_saved_queries()
    assert query.remote_id == ids["PACS prod"] and query.identity_id == config.identities[0].id
    (rule,) = target.list_route_rules()
    assert rule.source_remote_id == ids["PACS prod"] and rule.destination_remote_ids == [ids["Archive"]]
    # New rules start stopped, with no run history, so nothing queries a PACS on import.
    assert rule.status == "stopped" and rule.next_run_at is None and rule.seen_study_uids == []


def test_importing_the_same_file_twice_changes_nothing(source: ConfigStore, target: ConfigStore) -> None:
    _import(target, source)
    before = {p.name: p.read_bytes() for p in target.data_dir.glob("*.json")}
    plan, result = _import(target, source)
    assert plan.changes() == 0 and (result.added, result.updated) == (0, 0) and result.unchanged == len(plan.items)
    assert {p.name: p.read_bytes() for p in target.data_dir.glob("*.json")} == before


def test_the_local_ae_is_only_replaced_when_asked(source: ConfigStore, target: ConfigStore) -> None:
    _import(target, source, sections=st.DEFAULT_IMPORT)
    assert target.load().local.ae_title == LocalAE().ae_title
    parsed = st.parse_settings(st.export_bytes(source, (st.SECTION_LOCAL,)))
    plan = st.plan_import(target, parsed, (st.SECTION_LOCAL,))
    assert [(i.section, i.action) for i in plan.items] == [("local", st.UPDATE)]
    st.apply_import(target, plan)
    assert target.load().local.ae_title == "WORKSTN"
    assert st.plan_import(target, parsed, (st.SECTION_LOCAL,)).items[0].action == st.SAME


# --- matching and links --------------------------------------------------------


def test_a_node_with_the_same_name_is_updated_and_links_follow_its_local_id(source: ConfigStore, target: ConfigStore) -> None:
    mine = target.add_remote(RemoteNode(name="pacs PROD", ae_title="OLD", host="1.1.1.1", port=104)).remotes[-1]
    plan, result = _import(target, source)
    remotes = {r.name.casefold(): r for r in target.load().remotes}
    assert len(remotes) == 2 and remotes["pacs prod"].id == mine.id  # same record, my id kept
    assert remotes["pacs prod"].ae_title == "PACS" and remotes["pacs prod"].host == "10.0.0.5"
    assert plan.count("remotes", st.UPDATE) == 1 and plan.count("remotes", st.ADD) == 1
    (rule,) = target.list_route_rules()
    assert rule.source_remote_id == mine.id  # rewritten to the id used here
    assert target.list_saved_queries()[0].remote_id == mine.id


def test_an_existing_rule_keeps_its_running_state_when_updated(source: ConfigStore, target: ConfigStore) -> None:
    _import(target, source)
    (rule,) = target.list_route_rules()
    target.save_route_rule_run_state(rule.model_copy(update={"seen_study_uids": ["9.9.9"], "last_run_ok": True}))
    target_rule = target.get_route_rule(rule.id)
    assert target_rule is not None
    edited = source.list_route_rules()[0].model_copy(update={"interval_minutes": 3})
    source.update_route_rule(edited.id, edited)
    plan, _ = _import(target, source, sections=(st.SECTION_RULES,))
    assert plan.count("rules", st.UPDATE) == 1 and "keeps its running state" in plan.items[0].detail
    (after,) = target.list_route_rules()
    assert after.interval_minutes == 3 and after.seen_study_uids == ["9.9.9"] and after.status == target_rule.status


def test_a_link_to_a_node_that_is_not_here_is_a_warning_not_a_failure(source: ConfigStore, target: ConfigStore) -> None:
    plan, _ = _import(target, source, sections=(st.SECTION_RULES, st.SECTION_QUERIES))
    texts = " ".join(plan.warnings)
    assert "remote node that does not exist" in texts and "Nightly" in texts and "Open exams" in texts
    assert "writes files to /data/out" in texts
    assert len(target.list_route_rules()) == 1


def test_an_identity_that_is_not_here_falls_back_to_the_workstation_ae(source: ConfigStore, target: ConfigStore) -> None:
    plan, _ = _import(target, source, sections=(st.SECTION_QUERIES,))
    assert "virtual AE that does not exist" in " ".join(plan.warnings)
    assert target.list_saved_queries()[0].identity_id == ""


def test_only_the_chosen_sections_are_read(source: ConfigStore, target: ConfigStore) -> None:
    plan, _ = _import(target, source, sections=(st.SECTION_REMOTES,))
    assert plan.sections() == ["remotes"] and target.list_route_rules() == [] and target.list_saved_queries() == []


def test_a_name_listed_twice_in_the_file_is_used_once(source: ConfigStore, target: ConfigStore) -> None:
    document = st.export_settings(source, (st.SECTION_REMOTES,))
    document["sections"]["remotes"].append({**document["sections"]["remotes"][0], "id": "other"})
    plan = st.plan_import(target, st.parse_settings(json.dumps(document)), (st.SECTION_REMOTES,))
    assert plan.count("remotes", st.ADD) == 2 and any("twice" in p for p in plan.problems)


# --- reading a file ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not json at all", "not readable JSON"),
        (b"\xff\xfe\x00", "not readable JSON"),
        (b'["a list"]', "not a Dicommunication settings file"),
        (json.dumps({"format": "something-else", "version": 1}), "not a Dicommunication settings file"),
        (json.dumps({"format": st.FORMAT, "version": "1"}), "no valid version"),
        (json.dumps({"format": st.FORMAT, "version": 1}), "no sections"),
        (json.dumps({"format": st.FORMAT, "version": 99, "app_version": "9.9.9", "sections": {}}), "newer Dicommunication"),
        (b"x" * (st.MAX_BYTES + 1), "larger than"),
    ],
)
def test_files_that_are_not_ours_are_refused_with_a_reason(raw, message) -> None:
    with pytest.raises(st.SettingsError, match=message):
        st.parse_settings(raw)


def test_a_bad_record_is_reported_and_left_out_while_the_rest_is_read(source: ConfigStore, target: ConfigStore) -> None:
    document = st.export_settings(source, (st.SECTION_REMOTES,))
    document["sections"]["remotes"].append({"name": "Broken", "ae_title": "B", "port": 99999})
    document["sections"]["remotes"].append("not even an object")
    parsed = st.parse_settings(json.dumps(document))
    assert [r.name for r in parsed.remotes] == ["PACS prod", "Archive"]
    assert any("Broken" in p and "left out" in p for p in parsed.problems) and len(parsed.problems) == 2
    assert st.plan_import(target, parsed, (st.SECTION_REMOTES,)).problems == parsed.problems


def test_a_file_with_a_byte_order_mark_still_reads(source: ConfigStore) -> None:
    raw = b"\xef\xbb\xbf" + st.export_bytes(source, (st.SECTION_REMOTES,))
    assert len(st.parse_settings(raw).remotes) == 2


# --- failing halfway -----------------------------------------------------------


def test_a_failure_part_way_puts_everything_back(source: ConfigStore, target: ConfigStore, monkeypatch) -> None:
    target.add_remote(RemoteNode(name="Mine", ae_title="MINE", host="2.2.2.2", port=104))
    before = {p.name: p.read_bytes() for p in target.data_dir.glob("*.json")}
    parsed = st.parse_settings(st.export_bytes(source, ALL))
    plan = st.plan_import(target, parsed, ALL)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(target, "add_route_rule", boom)  # the last write fails, after the others went through
    with pytest.raises(st.SettingsError, match="nothing was changed.*disk full"):
        st.apply_import(target, plan)
    assert {p.name: p.read_bytes() for p in target.data_dir.glob("*.json")} == before
    assert [r.name for r in target.load().remotes] == ["Mine"]
