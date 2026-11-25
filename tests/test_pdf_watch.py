"""Dicom Router: a rule that watches a folder for PDFs and sends them to a PACS."""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest
from pynetdicom import AE, evt
from pynetdicom.sop_class import EncapsulatedPDFStorage

from app.models import LocalAE, RemoteNode, RouteRule
from app.mwl_scp import WorklistSCP
from app.pdf_watch import SENT_DIR, FAILED_DIR, SETTLE_SECONDS
from app.router_scheduler import RouterScheduler
from app.store import ConfigStore

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Pacs:
    """A Storage SCP for Encapsulated PDFs that can be told to refuse."""

    def __init__(self) -> None:
        self.received: list = []
        self.status = 0x0000
        self.port = _free_port()
        ae = AE(ae_title="PDF_SCP")
        ae.add_supported_context(EncapsulatedPDFStorage)
        self.server = ae.start_server(("127.0.0.1", self.port), block=False, evt_handlers=[(evt.EVT_C_STORE, self._store)])

    def _store(self, event):
        if self.status == 0x0000:
            self.received.append(event.dataset)
        return self.status

    def stop(self) -> None:
        self.server.shutdown()


@pytest.fixture
def pacs():
    server = Pacs()
    yield server
    server.stop()


@pytest.fixture
def setup(tmp_path, pacs):
    store = ConfigStore(tmp_path / "data")
    remote = store.add_remote(RemoteNode(name="pdf pacs", ae_title="PDF_SCP", host="127.0.0.1", port=pacs.port)).remotes[-1]
    folder = tmp_path / "watch"
    folder.mkdir()
    scheduler = RouterScheduler(store, WorklistSCP(store))
    return store, remote, folder, scheduler


def _old(path: Path, seconds: int = SETTLE_SECONDS + 30) -> Path:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))
    return path


def _put(folder: Path, name: str, data: bytes = PDF, old: bool = True) -> Path:
    path = folder / name
    path.write_bytes(data)
    return _old(path) if old else path


def _rule(store: ConfigStore, remote: RemoteNode, folder: Path, **extra) -> RouteRule:
    return store.add_route_rule(
        RouteRule(name="Reports", kind="pdf_folder", pdf_folder=str(folder), pdf_destination_id=remote.id, **extra)
    )


def test_pdfs_in_the_folder_are_sent_and_moved_to_sent(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _put(folder, "REPORT_1001.pdf")
    _put(folder, "REPORT_1002.PDF")
    rule = _rule(store, remote, folder)
    run = scheduler.run_rule(rule)
    assert run.ok and run.new_count == 2 and run.outcome_counts == {"forwarded": 2}
    assert len(pacs.received) == 2
    assert sorted(path.name for path in (folder / SENT_DIR).iterdir()) == ["REPORT_1001.pdf", "REPORT_1002.PDF"]
    assert [path.name for path in folder.iterdir()] == [SENT_DIR]
    # The next run finds nothing to do.
    again = scheduler.run_rule(store.get_route_rule(rule.id))
    assert again.ok and again.new_count == 0 and len(pacs.received) == 2


def test_a_file_that_is_still_being_written_waits(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _put(folder, "fresh.pdf", old=False)
    run = scheduler.run_rule(_rule(store, remote, folder))
    assert run.ok and run.new_count == 0 and run.status_counts == {"Waiting": 1} and not pacs.received
    assert (folder / "fresh.pdf").exists()


def test_a_refusing_pacs_leaves_the_files_for_the_next_run(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _put(folder, "a.pdf")
    rule = _rule(store, remote, folder)
    pacs.status = 0xA700
    run = scheduler.run_rule(rule)
    assert not run.ok and run.outcome_counts == {"failed": 1} and "0xA700" in "".join(run.error_counts)
    assert (folder / "a.pdf").exists() and not (folder / SENT_DIR).exists()
    pacs.status = 0x0000
    retry = scheduler.run_rule(store.get_route_rule(rule.id))
    assert retry.ok and retry.outcome_counts == {"forwarded": 1} and (folder / SENT_DIR / "a.pdf").exists()


def test_something_that_is_not_a_pdf_goes_to_failed_once(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _put(folder, "notes.pdf", b"just text")
    rule = _rule(store, remote, folder)
    run = scheduler.run_rule(rule)
    assert not run.ok and run.outcome_counts == {"failed": 1} and (folder / FAILED_DIR / "notes.pdf").exists()
    again = scheduler.run_rule(store.get_route_rule(rule.id))
    assert again.ok and again.new_count == 0


def test_only_the_files_directly_in_the_folder_are_looked_at(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    (folder / "sub").mkdir()
    _put(folder / "sub", "deep.pdf")
    _put(folder, ".hidden.pdf")
    _put(folder, "readme.txt", b"x")
    run = scheduler.run_rule(_rule(store, remote, folder))
    assert run.ok and run.new_count == 0 and not pacs.received


def test_a_failed_move_is_flagged_and_the_file_is_not_sent_twice(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _put(folder, "a.pdf")
    (folder / SENT_DIR).write_bytes(b"in the way")  # a file called Sent: the folder cannot be made
    rule = _rule(store, remote, folder)
    run = scheduler.run_rule(rule)
    assert not run.ok and "could not be moved" in run.error and run.outcome_counts == {"forwarded": 1}
    again = scheduler.run_rule(store.get_route_rule(rule.id))
    assert len(pacs.received) == 1 and again.new_count == 0  # remembered, not sent again


def test_the_patient_comes_from_the_file_name_by_default_or_is_typed(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _put(folder, "Smith_J_1234.pdf")
    scheduler.run_rule(_rule(store, remote, folder))
    assert len(pacs.received) == 1 and "1234" in str(pacs.received[0].PatientID) + str(pacs.received[0].PatientName).upper()
    pacs.received.clear()
    typed_folder = folder.parent / "typed"
    typed_folder.mkdir()
    _put(typed_folder, "x.pdf")
    _put(typed_folder, "y.pdf")
    rule = store.add_route_rule(
        RouteRule(name="Typed", kind="pdf_folder", pdf_folder=str(typed_folder), pdf_destination_id=remote.id,
                  pdf_patient_mode="typed", pdf_patient_name="DOE^JANE", pdf_patient_id="42", pdf_modality="SR")
    )
    scheduler.run_rule(rule)
    assert {str(ds.PatientID) for ds in pacs.received} == {"42"} and {str(ds.Modality) for ds in pacs.received} == {"SR"}
    assert len({str(ds.StudyInstanceUID) for ds in pacs.received}) == 1  # one study for a typed patient


def test_a_missing_folder_is_reported(setup, pacs, tmp_path) -> None:
    store, remote, folder, scheduler = setup
    rule = _rule(store, remote, tmp_path / "nowhere")
    run = scheduler.run_rule(rule)
    assert not run.ok and "not found" in run.error and store.get_route_rule(rule.id).last_run_ok is False


def test_file_names_never_reach_the_history_or_the_daily_counts(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    _put(folder, "PATIENT_9988776.pdf")
    _put(folder, "BAD_5544332.pdf", b"nope")
    scheduler.run_rule(_rule(store, remote, folder))
    on_disk = "".join(path.read_text() for path in store.data_dir.glob("*.json"))
    assert "9988776" not in on_disk and "5544332" not in on_disk and "PATIENT_" not in on_disk


def test_the_rule_is_checked_when_it_is_made() -> None:
    with pytest.raises(ValueError, match="Watch folder is required"):
        RouteRule(name="x", kind="pdf_folder", pdf_destination_id="r")
    with pytest.raises(ValueError, match="Send to is required"):
        RouteRule(name="x", kind="pdf_folder", pdf_folder="/tmp")
    with pytest.raises(ValueError, match="Patient Name is required"):
        RouteRule(name="x", kind="pdf_folder", pdf_folder="/tmp", pdf_destination_id="r", pdf_patient_mode="typed")
    with pytest.raises(ValueError, match="Source PACS is required"):
        RouteRule(name="x")
    assert RouteRule(name="x", source_remote_id="r").kind == "query"  # old rules are unchanged


def test_the_counts_are_added_up_per_day_for_the_charts(setup, pacs) -> None:
    store, remote, folder, scheduler = setup
    rule = _rule(store, remote, folder)
    _put(folder, "a.pdf")
    scheduler.run_rule(rule)
    _put(folder, "b.pdf")
    _put(folder, "c.pdf")
    scheduler.run_rule(store.get_route_rule(rule.id))
    day = next(iter(store.route_daily(rule.id).values()))
    assert day == {"Sent": 3}


# --- the Router page, the status dashboard and the settings file ---------------------------------


def _form(remote: RemoteNode, folder: Path, **extra) -> dict:
    data = {
        "kind": "pdf_folder", "name": "Reports", "pdf_folder": str(folder), "pdf_destination_id": remote.id,
        "pdf_modality": "DOC", "pdf_patient_mode": "per_file", "pdf_generate_document_title": "on",
        "schedule_mode": "interval", "interval_minutes": "15",
    }
    data.update(extra)
    return data


def test_a_pdf_watch_rule_can_be_added_from_the_page(client, store, remote, tmp_path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    response = client.post("/router", data=_form(remote, folder), follow_redirects=False)
    assert response.status_code == 303
    rule = store.list_route_rules()[0]
    assert rule.kind == "pdf_folder" and rule.pdf_folder == str(folder) and rule.pdf_destination_id == remote.id
    assert rule.source_remote_id == "" and rule.pdf_generate_document_title is True and rule.pdf_generate_accession is False
    page = client.get("/router").text
    assert "Watches" in page and str(folder) in page


def test_the_page_says_what_is_wrong_with_a_pdf_watch_rule(client, store, remote, tmp_path) -> None:
    missing = client.post("/router", data=_form(remote, tmp_path / "nowhere"))
    assert missing.status_code == 400 and 'id="error-pdf_folder"' in missing.text and "not found" in missing.text
    assert 'value="Reports"' in missing.text and 'name="kind"' in missing.text  # what was typed is kept
    folder = tmp_path / "in"
    folder.mkdir()
    no_node = client.post("/router", data=_form(remote, folder, pdf_destination_id=""))
    assert no_node.status_code == 400 and 'id="error-pdf_destination_id"' in no_node.text
    typed = client.post("/router", data=_form(remote, folder, pdf_patient_mode="typed"))
    assert typed.status_code == 400 and 'id="error-pdf_patient_name"' in typed.text
    bad_mod = client.post("/router", data=_form(remote, folder, pdf_modality="lower case!"))
    assert bad_mod.status_code == 400 and 'id="error-pdf_modality"' in bad_mod.text
    assert not store.list_route_rules()
    # A query rule still needs its source, and the message is still under that box.
    query = client.post("/router", data={"name": "q", "kind": "query", "schedule_mode": "interval", "interval_minutes": "15"})
    assert query.status_code == 400 and 'id="error-source_remote_id"' in query.text


def test_the_form_hides_what_the_other_kind_needs(client, store, remote) -> None:
    page = client.get("/router?new=1").text
    assert 'name="kind"' in page and 'data-reveal-when="kind=pdf_folder" hidden' in page
    assert 'data-required-when="kind=query" required' in page  # the source box is required for a query rule only
    # Editing keeps the kind and does not offer to change it.
    rule = store.add_route_rule(RouteRule(name="w", kind="pdf_folder", pdf_folder="/tmp", pdf_destination_id=remote.id))
    edit = client.get(f"/router?edit={rule.id}").text
    assert '<input type="hidden" name="kind" value="pdf_folder">' in edit and 'id="rule-kind"' not in edit
    assert 'name="pdf_folder"' in edit and 'value="/tmp"' in edit


def test_the_runs_page_and_the_dashboard_speak_of_pdfs(client, store, remote, setup, pacs) -> None:
    _store, _remote, folder, scheduler = setup
    # The router page above uses the app's own store; build the rule in it and run the scheduler for the same store.
    rule = store.add_route_rule(RouteRule(name="Reports", kind="pdf_folder", pdf_folder=str(folder), pdf_destination_id=remote.id))
    assert client.get(f"/router/{rule.id}/runs").status_code == 200
    page = client.get(f"/router/{rule.id}/runs").text
    assert "What this rule does" in page and "Encapsulated PDF Storage" in page and "What this rule asks the PACS" not in page


def test_the_dashboard_shows_sent_and_failed_pdfs_and_charts_them(setup, pacs) -> None:
    from datetime import date

    from app.lite_status import history_for_rule, tile_for_rule

    store, remote, folder, scheduler = setup
    rule = _rule(store, remote, folder)
    _put(folder, "ok.pdf")
    _put(folder, "bad.pdf", b"not a pdf")
    run = scheduler.run_rule(rule)
    tile = tile_for_rule(store.get_route_rule(rule.id), run)
    assert tile["unit"] == "PDFs" and tile["by_status"] == {"Sent": 1, "Failed": 1} and tile["state"] == "error"
    history = history_for_rule(rule, store.route_daily(rule.id), 7, date.today())
    assert history["unit"] == "PDFs" and history["keyed"] is True
    assert history["series"]["Sent"][-1] == 1 and history["series"]["Failed"][-1] == 1


def test_the_settings_file_carries_a_pdf_watch_rule_without_its_memory(setup, pacs, tmp_path) -> None:
    import json

    from app import settings_transfer

    store, remote, folder, scheduler = setup
    rule = _rule(store, remote, folder)
    _put(folder, "a.pdf")
    scheduler.run_rule(rule)
    assert store.get_route_rule(rule.id).pdf_sent_keys
    exported = json.loads(settings_transfer.export_bytes(store, [settings_transfer.SECTION_RULES, settings_transfer.SECTION_REMOTES]))
    body = exported["sections"][settings_transfer.SECTION_RULES][0]
    assert body["kind"] == "pdf_folder" and body["pdf_folder"] == str(folder) and "pdf_sent_keys" not in body
