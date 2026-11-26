"""Dicom Router: a rule that watches a folder for DICOM files and sends them to a PACS."""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

from app.dicom_watch import ITEM_SETTLE_SECONDS
from app.models import RemoteNode, RouteRule
from app.mwl_scp import WorklistSCP
from app.pdf_watch import FAILED_DIR, SENT_DIR
from app.router_scheduler import RouterScheduler
from app.store import ConfigStore
from test_dicom_import import Pacs, make_file


@pytest.fixture
def pacs():
    server = Pacs()
    yield server
    server.stop()


@pytest.fixture
def setup(tmp_path, pacs):
    store = ConfigStore(tmp_path / "data")
    remote = store.add_remote(RemoteNode(name="pacs", ae_title="PACS", host="127.0.0.1", port=pacs.port)).remotes[-1]
    folder = tmp_path / "watch"
    folder.mkdir()
    scheduler = RouterScheduler(store, WorklistSCP(store))
    return store, remote, folder, scheduler


def _age(path: Path, seconds: int = ITEM_SETTLE_SECONDS + 30) -> None:
    stamp = time.time() - seconds
    targets = [path] + (list(path.rglob("*")) if path.is_dir() else [])
    for target in targets:
        os.utime(target, (stamp, stamp))


def _rule(store: ConfigStore, remote: RemoteNode, folder: Path, **extra) -> RouteRule:
    return store.add_route_rule(
        RouteRule(name="Exports", kind="dicom_folder", pdf_folder=str(folder), pdf_destination_id=remote.id, **extra)
    )


def _study(folder: Path, name: str, *, images: int = 2, patient_id: str = "P1") -> Path:
    """A study folder with its images (and a sub-folder), aged so that it has settled."""
    from pydicom.uid import generate_uid

    study_uid, series_uid = str(generate_uid()), str(generate_uid())
    for number in range(1, images + 1):
        make_file(folder / name / "series1", f"IM{number}", study_uid=study_uid, series_uid=series_uid, number=number, patient_id=patient_id)
    _age(folder / name)
    return folder / name


def test_a_study_folder_is_sent_and_moves_to_sent_as_a_whole(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _study(folder, "study-a", images=3)
    rule = _rule(store, remote, folder)
    run = scheduler.run_rule(rule)
    assert run.ok and run.status_counts == {"Sent": 1} and run.outcome_counts == {"forwarded": 1}
    assert len(pacs.received) == 3
    assert not (folder / "study-a").exists() and (folder / SENT_DIR / "study-a" / "series1" / "IM1").exists()
    again = scheduler.run_rule(store.get_route_rule(rule.id))
    assert again.ok and not again.status_counts and len(pacs.received) == 3  # nothing is sent twice


def test_loose_files_and_an_archive_are_items_too(setup, pacs, tmp_path) -> None:
    store, remote, folder, scheduler = setup
    make_file(folder, "LOOSE1", patient_id="P9")
    _age(folder / "LOOSE1")
    source = tmp_path / "src"
    make_file(source, "ZIPPED1", patient_id="P8")
    zipped = folder / "export.zip"
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.write(source / "ZIPPED1", "inside/ZIPPED1")
    _age(zipped)
    run = scheduler.run_rule(_rule(store, remote, folder))
    assert run.ok and run.status_counts == {"Sent": 2}
    assert {str(d.PatientID) for d in pacs.received} == {"P9", "P8"}
    assert (folder / SENT_DIR / "LOOSE1").exists() and (folder / SENT_DIR / "export.zip").exists()


def test_a_7z_is_unpacked_only_for_the_run(setup, pacs, tmp_path) -> None:
    py7zr = pytest.importorskip("py7zr")
    store, remote, folder, scheduler = setup
    source = tmp_path / "src"
    make_file(source, "SEVEN1", patient_id="P7")
    archive = folder / "export.7z"
    with py7zr.SevenZipFile(archive, "w") as handle:
        handle.write(source / "SEVEN1", "inside/SEVEN1")
    _age(archive)
    run = scheduler.run_rule(_rule(store, remote, folder))
    assert run.ok and run.status_counts == {"Sent": 1} and {str(d.PatientID) for d in pacs.received} == {"P7"}
    assert (folder / SENT_DIR / "export.7z").exists()
    assert not (store.data_dir / "router_tmp").exists() or not any((store.data_dir / "router_tmp").rglob("*"))


def test_an_item_still_being_copied_in_waits(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _study(folder, "old", images=1)
    make_file(folder / "fresh", "IM1")  # just written: not aged
    run = scheduler.run_rule(_rule(store, remote, folder))
    assert run.status_counts == {"Sent": 1, "Waiting": 1} and (folder / "fresh").exists() and len(pacs.received) == 1


def test_an_item_the_pacs_refuses_stays_and_is_tried_again(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _study(folder, "study-a", images=2)
    rule = _rule(store, remote, folder)
    pacs.status = 0xA700
    run = scheduler.run_rule(rule)
    assert not run.ok and run.status_counts == {"Failed": 1} and "could not be sent" in run.error
    assert (folder / "study-a").exists() and not (folder / SENT_DIR).exists()
    pacs.status = 0x0000
    retry = scheduler.run_rule(store.get_route_rule(rule.id))
    assert retry.ok and retry.status_counts == {"Sent": 1} and (folder / SENT_DIR / "study-a").exists()


def test_one_bad_study_does_not_hold_back_the_others(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    bad = _study(folder, "bad", images=1, patient_id="BAD")
    _study(folder, "good", images=1, patient_id="GOOD")
    from pydicom import dcmread

    pacs.fail_for.add(str(dcmread(str(next(bad.rglob("IM1")))).SOPInstanceUID))
    run = scheduler.run_rule(_rule(store, remote, folder))
    assert run.status_counts == {"Sent": 1, "Failed": 1} and not run.ok
    assert (folder / "bad").exists() and (folder / SENT_DIR / "good").exists()


def test_items_without_dicom_hidden_things_and_the_sent_folder_are_left_alone(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    (folder / "notes.txt").write_text("not dicom")
    (folder / "empty").mkdir()
    (folder / "empty" / "readme.md").write_text("hi")
    _study(folder, ".hidden-study", images=1)
    (folder / SENT_DIR).mkdir()
    _study(folder / SENT_DIR, "old-sent", images=1)
    for path in (folder / "notes.txt", folder / "empty"):
        _age(path)
    run = scheduler.run_rule(_rule(store, remote, folder))
    assert run.ok and not run.status_counts and not pacs.received
    assert (folder / "notes.txt").exists() and (folder / "empty").exists() and (folder / ".hidden-study").exists()


def test_a_failed_move_is_flagged_and_the_item_is_not_sent_again(setup, pacs, monkeypatch) -> None:
    store, remote, folder, scheduler = setup
    _study(folder, "study-a", images=1)
    rule = _rule(store, remote, folder)
    import app.dicom_watch as watch

    monkeypatch.setattr(watch, "move_into", lambda *args, **kwargs: False)
    run = scheduler.run_rule(rule)
    assert not run.ok and "could not be moved into the Sent folder" in run.error and len(pacs.received) == 1
    again = scheduler.run_rule(store.get_route_rule(rule.id))
    assert len(pacs.received) == 1 and "could not be moved" in again.error  # retried the move only


def test_names_are_never_written_to_disk(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _study(folder, "SECRET-NAME-1234", images=1, patient_id="SECRETID")
    scheduler.run_rule(_rule(store, remote, folder))
    for path in store.data_dir.rglob("*.json"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "SECRET-NAME-1234" not in text and "SECRETID" not in text and "DOE^JANE" not in text, path.name


def test_a_missing_folder_is_reported(setup) -> None:
    store, remote, folder, scheduler = setup
    rule = _rule(store, remote, folder)
    shutil.rmtree(folder)
    run = scheduler.run_rule(rule)
    assert not run.ok and "not found" in run.error


# --- the page, the dashboard and the settings file ---------------------------------------------


def _form(remote: RemoteNode, folder: Path, **extra) -> dict:
    data = {
        "kind": "dicom_folder", "name": "Exports", "pdf_folder": str(folder), "pdf_destination_id": remote.id,
        "pdf_modality": "DOC", "pdf_patient_mode": "typed", "schedule_mode": "interval", "interval_minutes": "15",
    }
    data.update(extra)
    return data


def test_a_dicom_watch_rule_can_be_added_from_the_page(client, store, remote, tmp_path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    # The PDF-only boxes are hidden for this kind: a typed patient with no name must not block it.
    assert client.post("/router", data=_form(remote, folder), follow_redirects=False).status_code == 303
    rule = store.list_route_rules()[0]
    assert rule.kind == "dicom_folder" and rule.pdf_folder == str(folder) and rule.watch_unit == "items"
    page = client.get("/router").text
    assert "Watches" in page and "DICOM" in page and str(folder) in page
    assert client.get(f"/router/{rule.id}/runs").status_code == 200
    assert "Watch a folder for DICOM files" in client.get("/router?new=1").text


def test_the_page_says_what_is_wrong_with_a_dicom_watch_rule(client, store, remote, tmp_path) -> None:
    missing = client.post("/router", data=_form(remote, tmp_path / "nowhere"))
    assert missing.status_code == 400 and 'id="error-pdf_folder"' in missing.text
    folder = tmp_path / "in"
    folder.mkdir()
    no_node = client.post("/router", data=_form(remote, folder, pdf_destination_id=""))
    assert no_node.status_code == 400 and 'id="error-pdf_destination_id"' in no_node.text
    assert not store.list_route_rules()


def test_the_dashboard_counts_items_and_the_settings_file_carries_the_rule(setup, pacs) -> None:
    import json
    from datetime import date

    from app import settings_transfer
    from app.lite_status import history_for_rule, tile_for_rule

    store, remote, folder, scheduler = setup
    _study(folder, "study-a", images=1)
    rule = _rule(store, remote, folder)
    run = scheduler.run_rule(rule)
    tile = tile_for_rule(store.get_route_rule(rule.id), run)
    assert tile["unit"] == "items" and tile["by_status"] == {"Sent": 1}
    history = history_for_rule(rule, store.route_daily(rule.id), 7, date.today())
    assert history["unit"] == "items" and history["keyed"] is True and history["series"]["Sent"][-1] == 1
    exported = json.loads(settings_transfer.export_bytes(store, [settings_transfer.SECTION_RULES, settings_transfer.SECTION_REMOTES]))
    body = exported["sections"][settings_transfer.SECTION_RULES][0]
    assert body["kind"] == "dicom_folder" and body["pdf_folder"] == str(folder) and "pdf_sent_keys" not in body
