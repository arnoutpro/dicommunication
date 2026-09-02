from __future__ import annotations

import socket
import zipfile

import pytest
from fastapi.testclient import TestClient
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage
from pynetdicom import AE, evt
from pynetdicom.presentation import build_context
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.main import create_app
from app.models import LocalAE, RemoteNode
from app.shell import (
    ANONYMIZE_PREFIX,
    PROFILE_ANONYMIZER,
    PRODUCT_NAMES,
    SHELL_ANONYMIZE,
    anonymize_path_allowed,
    is_anonymize_public_path,
    is_anonymizer_profile,
    profile_start_path,
    profile_window_title,
    public_href,
    tools_exclude,
    tools_for_shell,
)
from app.store import ConfigStore
from app.tools.anon_engine import AnonBatch


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_instance(study_uid: str, series_uid: str, sop_uid: str) -> Dataset:
    ds = Dataset()
    ds.PatientName = "DOE^JANE"
    ds.PatientID = "12345"
    ds.PatientBirthDate = "19800101"
    ds.AccessionNumber = "ACC1"
    ds.StudyDescription = "CT CHEST"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.Modality = "CT"
    ds.Rows = 2
    ds.Columns = 2
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.SamplesPerPixel = 1
    ds.PixelData = b"\x01\x02\x03\x04"
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    return ds


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


def test_anonymize_shell_constants() -> None:
    assert PRODUCT_NAMES[SHELL_ANONYMIZE] == "Dicom Anonymizer"
    assert is_anonymizer_profile(PROFILE_ANONYMIZER)
    assert not is_anonymizer_profile("dicommunication")
    assert profile_start_path(PROFILE_ANONYMIZER) == "/anonymize/"
    assert profile_window_title(PROFILE_ANONYMIZER) == "Dicom Anonymizer"


def test_anonymize_path_helpers() -> None:
    assert is_anonymize_public_path("/anonymize")
    assert is_anonymize_public_path("/anonymize/config/remotes")
    assert not is_anonymize_public_path("/vue")
    assert public_href("/", shell=SHELL_ANONYMIZE) == "/anonymize/"
    assert public_href("/config/remotes", shell=SHELL_ANONYMIZE) == "/anonymize/config/remotes"
    assert public_href("/static/css/app.css", shell=SHELL_ANONYMIZE) == "/static/css/app.css"
    assert anonymize_path_allowed("/")
    assert anonymize_path_allowed("/tools/anonymize/run")
    assert anonymize_path_allowed("/help")
    assert not anonymize_path_allowed("/testbench")
    assert not anonymize_path_allowed("/tools/c-echo")


def test_anonymize_tool_hidden_from_other_shells() -> None:
    from app.shell import SHELL_DICOMM, SHELL_VUE

    assert "anonymize" not in tools_exclude(SHELL_ANONYMIZE)
    assert "anonymize" in tools_exclude(SHELL_DICOMM)
    assert "anonymize" in tools_exclude(SHELL_VUE)
    ids = {tool.id for tool in tools_for_shell(SHELL_ANONYMIZE)}
    assert ids == {"anonymize"}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def test_nuke_strips_everything_but_structural_tags_and_keeps_uids_consistent() -> None:
    study_uid, series_uid = "1.2.3", "1.2.3.4"
    instances = [_make_instance(study_uid, series_uid, f"1.2.3.4.{i}") for i in range(3)]
    batch = AnonBatch()
    nuked = [batch.anonymize(ds, "nuke") for ds in instances]
    assert all("PatientName" not in n for n in nuked)
    assert all("AccessionNumber" not in n for n in nuked)
    assert nuked[0].StudyInstanceUID == nuked[1].StudyInstanceUID == nuked[2].StudyInstanceUID
    assert str(nuked[0].StudyInstanceUID) != study_uid
    assert len({str(n.SOPInstanceUID) for n in nuked}) == 3
    assert nuked[0].PixelData == b"\x01\x02\x03\x04"
    assert nuked[0].Rows == 2 and nuked[0].Modality == "CT"
    assert str(nuked[0].file_meta.MediaStorageSOPInstanceUID) == str(nuked[0].SOPInstanceUID)


def test_fuzz_scrambles_values_but_keeps_structure_and_uid_consistency() -> None:
    study_uid, series_uid = "1.2.3", "1.2.3.4"
    instances = [_make_instance(study_uid, series_uid, f"1.2.3.4.{i}") for i in range(2)]
    batch = AnonBatch()
    fuzzed = [batch.anonymize(ds, "fuzz") for ds in instances]
    assert fuzzed[0].PatientName != "DOE^JANE"
    assert fuzzed[0].AccessionNumber != "ACC1"
    assert fuzzed[0].StudyInstanceUID == fuzzed[1].StudyInstanceUID
    assert str(fuzzed[0].StudyInstanceUID) != study_uid
    assert fuzzed[0].PixelData == b"\x01\x02\x03\x04"
    assert fuzzed[0].Rows == 2 and fuzzed[0].Modality == "CT"


def test_remove_patient_erase_only_touches_identifying_tags() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.9")
    batch = AnonBatch()
    out = batch.anonymize(ds, "remove_patient", remove_patient_erase=True)
    assert out.PatientName == ""
    assert out.PatientID == ""
    assert out.AccessionNumber == ""
    assert out.StudyDescription == "CT CHEST"  # not identifying -> kept
    assert str(out.StudyInstanceUID) != "1.2.3"  # UID is always remapped


def test_remove_patient_fuzz_scrambles_instead_of_blanking() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.10")
    batch = AnonBatch()
    out = batch.anonymize(ds, "remove_patient", remove_patient_erase=False)
    assert out.PatientName not in ("DOE^JANE", "")


def test_custom_mode_keeps_untouched_tags_and_applies_only_named_actions() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.11")
    batch = AnonBatch()
    out = batch.anonymize(
        ds,
        "custom",
        custom_actions={
            "PatientName": {"action": "replace", "value": "ANON^PATIENT"},
            "PatientID": {"action": "erase"},
            "StudyInstanceUID": {"action": "fresh_uid"},
        },
    )
    assert out.PatientName == "ANON^PATIENT"
    assert out.PatientID == ""
    assert str(out.StudyInstanceUID) != "1.2.3"
    assert out.AccessionNumber == "ACC1"  # not named in custom_actions -> kept as-is


def test_unknown_mode_raises() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.12")
    with pytest.raises(ValueError):
        AnonBatch().anonymize(ds, "bogus")


# ---------------------------------------------------------------------------
# End-to-end: query + run against real (fake) SCPs, through the HTTP layer
# ---------------------------------------------------------------------------


def _start_find_move_scp(find_port: int, storage_port: int, study_uid: str, instance: Dataset):
    def handle_find(event):
        identifier = event.identifier
        if str(getattr(identifier, "QueryRetrieveLevel", "STUDY")) == "STUDY":
            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            ds.PatientName = "DOE^JANE"
            ds.PatientID = "12345"
            ds.StudyDate = "20260101"
            ds.AccessionNumber = "ACC1"
            ds.StudyDescription = "CT CHEST"
            ds.ModalitiesInStudy = "CT"
            ds.StudyInstanceUID = study_uid
            yield 0xFF00, ds
        yield 0x0000, None

    def handle_move(event):
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(instance.SOPClassUID))]}
        yield 1
        yield 0xFF00, instance

    ae = AE(ae_title="QR_SCP")
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    server = ae.start_server(
        ("127.0.0.1", find_port),
        block=False,
        evt_handlers=[(evt.EVT_C_FIND, handle_find), (evt.EVT_C_MOVE, handle_move)],
    )
    return server


def test_anonymize_query_then_run_end_to_end(tmp_path) -> None:
    find_port = _free_port()
    local_port = _free_port()
    study_uid = "1.2.826.0.1.3680043.8.498.11223344"
    series_uid = "1.2.826.0.1.3680043.8.498.11223345"
    sop_uid = "1.2.826.0.1.3680043.8.498.11223346"
    instance = _make_instance(study_uid, series_uid, sop_uid)
    server = _start_find_move_scp(find_port, local_port, study_uid, instance)

    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    store.add_remote(remote)
    app = create_app(store)

    try:
        with TestClient(app) as client:
            query = client.post(
                f"{ANONYMIZE_PREFIX}/tools/anonymize/run",
                data={"action": "query", "remote_id": remote.id, "study_date": "2026-01-01"},
            )
            assert query.status_code == 200
            assert "DOE^JANE" in query.text
            assert "Anonymize at" in query.text

            out_dir = tmp_path / "anon_out"
            run = client.post(
                f"{ANONYMIZE_PREFIX}/tools/anonymize/run",
                data={
                    "action": "run",
                    "remote_id": remote.id,
                    "level": "STUDY",
                    "study_uid": [study_uid],
                    "mode": "nuke",
                    "output_dir": str(out_dir),
                    "archive": "none",
                },
            )
            assert run.status_code == 200
            assert "Anonymized and exported" in run.text, run.text

            written = list(out_dir.rglob("*.dcm"))
            assert written
            import pydicom

            anonymized = pydicom.dcmread(written[0])
            assert "PatientName" not in anonymized
            assert str(anonymized.StudyInstanceUID) != study_uid
    finally:
        server.shutdown()


def test_anonymize_zip_archive_contains_the_output(tmp_path) -> None:
    find_port = _free_port()
    local_port = _free_port()
    study_uid = "1.2.826.0.1.3680043.8.498.22334455"
    series_uid = "1.2.826.0.1.3680043.8.498.22334456"
    sop_uid = "1.2.826.0.1.3680043.8.498.22334457"
    instance = _make_instance(study_uid, series_uid, sop_uid)
    server = _start_find_move_scp(find_port, local_port, study_uid, instance)

    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    store.add_remote(remote)
    app = create_app(store)

    try:
        with TestClient(app) as client:
            out_dir = tmp_path / "anon_out_zip"
            run = client.post(
                f"{ANONYMIZE_PREFIX}/tools/anonymize/run",
                data={
                    "action": "run",
                    "remote_id": remote.id,
                    "level": "STUDY",
                    "study_uid": [study_uid],
                    "mode": "remove_patient",
                    "remove_patient_action": "erase",
                    "output_dir": str(out_dir),
                    "archive": "zip",
                },
            )
            assert run.status_code == 200
            assert "Anonymized and exported" in run.text, run.text
            zips = list(out_dir.glob("*.zip"))
            assert zips
            with zipfile.ZipFile(zips[0]) as zf:
                assert any(name.endswith(".dcm") for name in zf.namelist())
    finally:
        server.shutdown()


def test_anonymize_run_requires_selection_and_output_dir() -> None:
    from app.tools.anonymize import AnonymizeTool

    tool = AnonymizeTool()
    local = LocalAE(ae_title="DICOMM")
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=104)

    no_selection = tool.run(local, remote, {"action": "run", "mode": "nuke", "output_dir": "/tmp/x"})
    assert not no_selection.ok
    assert "select" in no_selection.summary.lower()

    no_output = tool.run(
        local, remote,
        {"action": "run", "entities_json": [{"study_uid": "1.2.3"}], "mode": "nuke"},
    )
    assert not no_output.ok
    assert "output folder" in no_output.summary.lower()

    no_mode = tool.run(
        local, remote,
        {
            "action": "run",
            "entities_json": [{"study_uid": "1.2.3"}],
            "output_dir": "/tmp/x",
        },
    )
    assert not no_mode.ok
    assert "mode" in no_mode.summary.lower()
