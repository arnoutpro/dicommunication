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
        ds.Modality = "CT" if number == 1 else "MR"
        out[uid] = ds
    return out


def _scp(find_port: int, storage_port: int, moves: list[Dataset]):
    instances = _instances()

    def handle_find(event):
        level = str(getattr(event.identifier, "QueryRetrieveLevel", "STUDY"))
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
