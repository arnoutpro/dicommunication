"""Dicom Anonymizer: see and pick the series of a study, and choose the folder layout."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.presentation import build_context
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.main import create_app
from app.models import LocalAE, RemoteNode
from app.store import ConfigStore
from app.tools.anonymize import _series_folder_names, _write_anonymized
from test_anonymize import _free_port, _make_instance

STUDY = "1.2.826.0.1.3680043.8.498.5000"
SERIES = ["1.2.826.0.1.3680043.8.498.5001", "1.2.826.0.1.3680043.8.498.5002"]


def _instances() -> dict[str, Dataset]:
    out = {}
    for number, uid in enumerate(SERIES, start=1):
        ds = _make_instance(STUDY, uid, uid + ".1")
        ds.SeriesNumber = number
        ds.SeriesDescription = f"Series {number} desc"
        ds.Modality = "CT" if number == 1 else "MR"
        out[uid] = ds
    return out


def _scp(find_port: int, storage_port: int, moves: list[Dataset]):
    instances = _instances()

    def handle_find(event):
        level = str(getattr(event.identifier, "QueryRetrieveLevel", "STUDY"))
        if level == "STUDY":
            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            ds.PatientName = "DOE^JANE"
            ds.PatientID = "12345"
            ds.StudyDate = "20260101"
            ds.StudyDescription = "CT"
            ds.ModalitiesInStudy = "CT\\MR"
            ds.StudyInstanceUID = STUDY
            yield 0xFF00, ds
        if level == "SERIES":
            for number, uid in enumerate(SERIES, start=1):
                ds = Dataset()
                ds.QueryRetrieveLevel = "SERIES"
                ds.StudyInstanceUID = STUDY
                ds.SeriesInstanceUID = uid
                ds.Modality = instances[uid].Modality
                ds.SeriesNumber = number
                ds.SeriesDescription = f"Series {number} desc"
                ds.NumberOfSeriesRelatedInstances = 1
                yield 0xFF00, ds
        yield 0x0000, None

    def handle_move(event):
        identifier = event.identifier
        moves.append(identifier)
        wanted = [str(identifier.SeriesInstanceUID)] if "SeriesInstanceUID" in identifier else SERIES
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(instances[SERIES[0]].SOPClassUID))]}
        yield len(wanted)
        for uid in wanted:
            yield 0xFF00, instances[uid]

    ae = AE(ae_title="QR_SCP")
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    return ae.start_server(
        ("127.0.0.1", find_port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find), (evt.EVT_C_MOVE, handle_move)]
    )


def _client(tmp_path):
    find_port, local_port = _free_port(), _free_port()
    moves: list[Dataset] = []
    server = _scp(find_port, local_port, moves)
    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    store.add_remote(remote)
    return TestClient(create_app(store)), remote, server, moves


def test_the_series_of_a_study_are_listed_and_ticked(tmp_path) -> None:
    client, remote, server, _moves = _client(tmp_path)
    try:
        with client:
            page = client.post("/tools/anonymize/series", data={"remote_id": remote.id, "series_study": STUDY}).text
        assert "Series 1 desc" in page and "Series 2 desc" in page
        assert page.count('name="series_sel"') == 2 and page.count(" checked") == 2
        assert f'name="series_opened" value="{STUDY}"' in page
    finally:
        server.shutdown()


def test_the_series_list_says_why_it_is_empty(client: TestClient) -> None:
    assert "No study given" in client.post("/tools/anonymize/series", data={}).text
    assert "Select a remote" in client.post("/tools/anonymize/series", data={"series_study": STUDY}).text


def test_only_the_ticked_series_are_retrieved_and_written_per_series(tmp_path) -> None:
    client, remote, server, moves = _client(tmp_path)
    out = tmp_path / "out"
    try:
        with client:
            run = client.post(
                "/tools/anonymize/run",
                data={
                    "action": "run", "remote_id": remote.id, "level": "SERIES", "study_uid": [STUDY],
                    "series_opened": [STUDY], "series_sel": [f"{STUDY}|{SERIES[1]}"],
                    "mode": "remove_patient", "remove_patient_action": "erase", "output_dir": str(out), "archive": "none",
                    "layout": "series",
                },
            )
        assert "Anonymized and exported 1 instance" in run.text, run.text
        assert len(moves) == 1 and str(moves[0].SeriesInstanceUID) == SERIES[1] and str(moves[0].QueryRetrieveLevel) == "SERIES"
        folders = sorted(path.relative_to(out).parts[1:] for path in out.rglob("*.dcm"))
        assert len(folders) == 1 and folders[0][1].startswith("series-2-MR")
    finally:
        server.shutdown()


def test_a_study_whose_series_were_never_opened_is_retrieved_whole(tmp_path) -> None:
    client, remote, server, moves = _client(tmp_path)
    try:
        with client:
            run = client.post(
                "/tools/anonymize/run",
                data={
                    "action": "run", "remote_id": remote.id, "level": "STUDY", "study_uid": [STUDY],
                    "mode": "remove_patient", "remove_patient_action": "erase", "output_dir": str(tmp_path / "o"), "archive": "none",
                },
            )
        assert "Anonymized and exported 2 instance" in run.text, run.text
        assert len(moves) == 1 and str(moves[0].QueryRetrieveLevel) == "STUDY"
    finally:
        server.shutdown()


def test_unticking_every_series_of_an_opened_study_leaves_nothing_to_do(tmp_path) -> None:
    client, remote, server, moves = _client(tmp_path)
    try:
        with client:
            run = client.post(
                "/tools/anonymize/run",
                data={
                    "action": "run", "remote_id": remote.id, "level": "SERIES", "study_uid": [STUDY], "series_opened": [STUDY],
                    "mode": "nuke", "output_dir": str(tmp_path / "o"), "archive": "none",
                },
            )
        assert "Select at least one study" in run.text and not moves
    finally:
        server.shutdown()


def test_flat_layout_puts_every_file_in_the_export_folder_and_series_layout_does_not(tmp_path) -> None:
    datasets = list(_instances().values())
    flat = tmp_path / "flat"
    flat.mkdir()
    paths = _write_anonymized(datasets, flat, "flat")
    assert {path.parent for path in paths} == {flat}
    nested = tmp_path / "nested"
    nested.mkdir()
    paths = _write_anonymized(datasets, nested, "series")
    assert len({path.parent for path in paths}) == 2 and all(path.parent.parent.parent == nested for path in paths)
    assert sorted(path.parent.name for path in paths) == ["series-1-CT", "series-2-MR"]


def test_series_folders_never_use_the_description_and_stay_unique() -> None:
    first, second = (_make_instance(STUDY, uid, uid + ".1") for uid in SERIES)
    for ds in (first, second):
        ds.SeriesNumber = 3
        ds.SeriesDescription = "JANE DOE CHEST"
    names = _series_folder_names([first, second])
    assert len(set(names.values())) == 2 and all("JANE" not in name for name in names.values())
    third = _make_instance(STUDY, SERIES[0], SERIES[0] + ".2")
    del third.Modality
    assert _series_folder_names([third])[(STUDY, SERIES[0])].endswith(SERIES[0][-6:])  # no number: the UID tail tells it apart


def test_an_unknown_layout_falls_back_to_series_folders(tmp_path) -> None:
    out = tmp_path / "x"
    out.mkdir()
    paths = _write_anonymized(list(_instances().values()), out, "series")
    assert all(path.parent != out for path in paths)


def test_the_page_offers_series_and_the_folder_layout(client: TestClient, store: ConfigStore, remote: RemoteNode) -> None:
    # The layout choice sits in step 4, which shows once studies were found; the template carries both.
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "app" / "templates" / "anonymize.html").read_text(encoding="utf-8")
    assert 'name="layout" value="series" checked' in text and 'name="layout" value="flat"' in text
    assert "series_url" in text


def test_no_folder_dialog_in_the_service_editions(monkeypatch) -> None:
    from app import fs_dialog

    monkeypatch.delenv("DICOMM_NO_DIALOGS", raising=False)
    monkeypatch.setenv("DICOMM_REQUIRE_LOGIN", "1")  # what both service installers set
    assert fs_dialog.dialogs_available() is False
    monkeypatch.delenv("DICOMM_REQUIRE_LOGIN")
    monkeypatch.setenv("DICOMM_NO_DIALOGS", "1")
    assert fs_dialog.dialogs_available() is False


def test_the_anonymizer_says_when_there_is_no_folder_dialog(tmp_path, monkeypatch) -> None:
    from app.routes import anonymize as route

    client, remote, server, _moves = _client(tmp_path)
    try:
        for available, expect in ((False, True), (True, False)):
            monkeypatch.setattr(route, "dialogs_available", lambda available=available: available)
            with client:
                page = client.post(
                    "/tools/anonymize/run", data={"action": "query", "remote_id": remote.id, "study_date": "2026-01-01"}
                ).text
            assert "Output folder" in page
            assert ("There is no folder dialog here" in page) is expect
            assert ("disabled title=\"No desktop folder dialog" in page) is expect
    finally:
        server.shutdown()


def test_series_folders_can_be_named_by_description() -> None:
    from app.tools.anonymize import _folder_text

    first, second = _instances().values()
    names = _series_folder_names([first, second], {(STUDY, SERIES[0]): "TLC Inspiratory 0,6", (STUDY, SERIES[1]): "RV Expiratory 0,6"})
    assert names == {(STUDY, SERIES[0]): "TLC Inspiratory 0,6", (STUDY, SERIES[1]): "RV Expiratory 0,6"}
    assert _folder_text('A/B: C? "x" <y>  ') == "A_B_ C_ _x_ _y_"
    assert _folder_text("Dose Report.") == "Dose Report" and len(_folder_text("x" * 300)) == 80


def test_a_missing_description_falls_back_and_equal_descriptions_stay_apart() -> None:
    first, second = _instances().values()
    names = _series_folder_names([first, second], {(STUDY, SERIES[0]): "", (STUDY, SERIES[1]): "  "})
    assert sorted(names.values()) == ["series-1-CT", "series-2-MR"]
    same = _series_folder_names([first, second], {(STUDY, SERIES[0]): "Lung", (STUDY, SERIES[1]): "lung"})
    assert sorted(same.values()) == ["Lung - 1", "lung - 2"]
    for ds in (first, second):
        ds.SeriesNumber = 7
    twice = _series_folder_names([first, second], {(STUDY, SERIES[0]): "Lung", (STUDY, SERIES[1]): "Lung"})
    assert len(set(name.lower() for name in twice.values())) == 2


def test_the_description_names_the_folder_even_when_the_mode_removes_it(tmp_path) -> None:
    client, remote, server, _moves = _client(tmp_path)
    out = tmp_path / "out"
    try:
        with client:
            run = client.post(
                "/tools/anonymize/run",
                data={
                    "action": "run", "remote_id": remote.id, "level": "STUDY", "study_uid": [STUDY],
                    "mode": "nuke", "output_dir": str(out), "archive": "none", "layout": "series", "series_names": "description",
                },
            )
        assert "Anonymized and exported 2 instance" in run.text, run.text
        assert sorted(path.parent.name for path in out.rglob("*.dcm")) == ["Series 1 desc", "Series 2 desc"]
    finally:
        server.shutdown()


def test_by_default_the_description_is_not_used(tmp_path) -> None:
    client, remote, server, _moves = _client(tmp_path)
    out = tmp_path / "out"
    try:
        with client:
            client.post(
                "/tools/anonymize/run",
                data={"action": "run", "remote_id": remote.id, "level": "STUDY", "study_uid": [STUDY], "mode": "remove_patient",
                      "remove_patient_action": "erase", "output_dir": str(out), "archive": "none"},
            )
        assert sorted(path.parent.name for path in out.rglob("*.dcm")) == ["series-1-CT", "series-2-MR"]
    finally:
        server.shutdown()


def test_study_folders_can_be_named_by_description() -> None:
    from app.tools.anonymize import _study_folder_names

    first, second = _instances().values()
    other = _make_instance("1.2.826.0.1.3680043.8.498.6000", "1.2.826.0.1.3680043.8.498.6001", "1.2.826.0.1.3680043.8.498.6002")
    uid_only = _study_folder_names([first, other])
    assert uid_only == {STUDY: STUDY, other.StudyInstanceUID: str(other.StudyInstanceUID)}
    named = _study_folder_names([first, second, other], {STUDY: "CT Thorax 2026", str(other.StudyInstanceUID): "  "})
    assert named[STUDY] == "CT Thorax 2026" and named[str(other.StudyInstanceUID)] == str(other.StudyInstanceUID)  # no description: the UID
    twin = _study_folder_names([first, other], {STUDY: "CT", str(other.StudyInstanceUID): "ct"})
    assert len(set(name.lower() for name in twin.values())) == 2 and all(name[:2].lower() == "ct" for name in twin.values())


def test_the_study_description_names_the_study_folder(tmp_path) -> None:
    client, remote, server, _moves = _client(tmp_path)
    out = tmp_path / "out"
    try:
        with client:
            run = client.post(
                "/tools/anonymize/run",
                data={
                    "action": "run", "remote_id": remote.id, "level": "STUDY", "study_uid": [STUDY],
                    "mode": "nuke", "output_dir": str(out), "archive": "none", "layout": "series",
                    "study_names": "description", "series_names": "description",
                },
            )
        assert "Anonymized and exported 2 instance" in run.text, run.text
        parts = sorted(path.relative_to(out).parts[1:] for path in out.rglob("*.dcm"))
        assert [part[:2] for part in parts] == [("CT CHEST", "Series 1 desc"), ("CT CHEST", "Series 2 desc")]
    finally:
        server.shutdown()


def test_by_default_the_study_folder_is_the_uid(tmp_path) -> None:
    client, remote, server, _moves = _client(tmp_path)
    out = tmp_path / "out"
    try:
        with client:
            client.post(
                "/tools/anonymize/run",
                data={"action": "run", "remote_id": remote.id, "level": "STUDY", "study_uid": [STUDY], "mode": "remove_patient",
                      "remove_patient_action": "erase", "output_dir": str(out), "archive": "none"},
            )
        folders = {path.relative_to(out).parts[1] for path in out.rglob("*.dcm")}
        assert len(folders) == 1 and next(iter(folders)).startswith("1.2.826.")
    finally:
        server.shutdown()


# --- a PACS that closes the association, or refuses one series: nothing may be lost silently ---

MANY = [f"1.2.826.0.1.3680043.8.498.7{n:03d}" for n in range(4)]


def _many_instances() -> dict[str, Dataset]:
    out = {}
    for index, uid in enumerate(MANY):
        ds = _make_instance(STUDY, uid, uid + ".1")
        ds.SeriesNumber = [2, 3, 601, 602][index]
        ds.SeriesDescription = f"Desc {ds.SeriesNumber}"
        out[uid] = ds
    return out


def _picky_scp(find_port: int, storage_port: int, per_association: int, refuse: set[str], log: list[tuple[int, str]]):
    instances = _many_instances()
    used: dict[int, int] = {}

    def handle_move(event):
        key = id(event.assoc)
        used[key] = used.get(key, 0) + 1
        series = str(event.identifier.SeriesInstanceUID)
        log.append((key, series))
        if used[key] > per_association:
            event.assoc.abort()  # what some PACS do: hang up after a few moves
            yield None, None
            return
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(instances[series].SOPClassUID))]}
        yield 1
        if series in refuse:
            yield 0xC000, None  # the move itself fails
            return
        yield 0xFF00, instances[series]

    ae = AE(ae_title="QR_SCP")
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    return ae.start_server(("127.0.0.1", find_port), block=False, evt_handlers=[(evt.EVT_C_MOVE, handle_move)])


def _picky_run(tmp_path, per_association: int, refuse: set[str]):
    find_port, local_port = _free_port(), _free_port()
    log: list[tuple[int, str]] = []
    server = _picky_scp(find_port, local_port, per_association, refuse, log)
    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True, timeout_seconds=5))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    store.add_remote(remote)
    out = tmp_path / "out"
    labels = [f"{uid}|{number} · Desc {number}" for uid, number in zip(MANY, [2, 3, 601, 602])]
    try:
        with TestClient(create_app(store)) as client:
            run = client.post(
                "/tools/anonymize/run",
                data={
                    "action": "run", "remote_id": remote.id, "level": "SERIES", "study_uid": [STUDY],
                    "series_opened": [STUDY], "series_sel": [f"{STUDY}|{uid}" for uid in MANY], "series_label": labels,
                    "mode": "remove_patient", "remove_patient_action": "erase", "output_dir": str(out), "archive": "none",
                },
            )
    finally:
        server.shutdown()
    return run.text, sorted(path.parent.name for path in out.rglob("*.dcm")), log


def test_a_pacs_that_hangs_up_after_two_moves_still_delivers_every_series(tmp_path) -> None:
    text, folders, log = _picky_run(tmp_path, per_association=2, refuse=set())
    assert "Anonymized and exported 4 instance" in text, text
    assert [name.split("-")[1] for name in folders] == ["2", "3", "601", "602"]
    assert len({key for key, _series in log}) >= 2  # it had to come back on a new association


def test_a_series_the_pacs_refuses_is_named_and_the_run_is_not_called_a_success(tmp_path) -> None:
    text, folders, _log = _picky_run(tmp_path, per_association=99, refuse={MANY[2], MANY[3]})
    assert len(folders) == 2
    assert "but not everything" in text and "601 · Desc 601" in text and "602 · Desc 602" in text
    assert "Anonymized and exported" not in text  # the plain success line is not shown
    assert "0xC000" in text
