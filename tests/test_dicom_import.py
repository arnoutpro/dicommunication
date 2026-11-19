"""DICOM import: scan a folder, edit, send (app/dicom_import.py)."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from pydicom import dcmread
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.fileset import FileSet
from pydicom.sequence import Sequence
from pydicom.uid import (
    CTImageStorage,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    RLELossless,
    generate_uid,
)
from pynetdicom import AE, evt

from app import dicom_import as di
from app.models import LocalAE, RemoteNode


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_file(
    folder: Path,
    name: str,
    *,
    patient_id: str = "P1",
    patient_name: str = "DOE^JANE",
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
    number: int = 1,
    modality: str = "CT",
    **extra: str,
) -> tuple[Path, Dataset]:
    folder.mkdir(parents=True, exist_ok=True)
    sop_uid = sop_uid or str(generate_uid())
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    path = folder / name
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid or str(generate_uid())
    ds.SeriesInstanceUID = series_uid or str(generate_uid())
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientBirthDate = "19800101"
    ds.PatientSex = "F"
    ds.StudyDate = "20261101"
    ds.StudyTime = "101500"
    ds.StudyDescription = "CT CHEST"
    ds.AccessionNumber = "ACC1"
    ds.ReferringPhysicianName = "REF^DOC"
    ds.StudyID = "S1"
    ds.Modality = modality
    ds.SeriesNumber = 1
    ds.SeriesDescription = "Axial"
    ds.InstanceNumber = number
    ds.Rows = ds.Columns = 8
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    for keyword, value in extra.items():
        setattr(ds, keyword, value)
    ds.PixelData = (np.arange(64, dtype=np.uint16)).tobytes()
    ds.save_as(path, enforce_file_format=True)
    return path, ds


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    study_a, series_a = str(generate_uid()), str(generate_uid())
    study_b = str(generate_uid())
    make_file(tmp_path / "a", "IM1", study_uid=study_a, series_uid=series_a, number=1)
    make_file(tmp_path / "a", "IM2", study_uid=study_a, series_uid=series_a, number=2)
    make_file(tmp_path / "b", "IM3", study_uid=study_b, patient_id="P2", patient_name="ROE^RICHARD", modality="MR")
    (tmp_path / "notes.txt").write_text("not dicom")
    return tmp_path


# --- scan ----------------------------------------------------------------------


def test_scan_groups_patients_studies_series_and_instances(folder: Path) -> None:
    scan = di.scan_folder(folder)
    assert sorted(scan.patients) == ["P1", "P2"]
    assert scan.instance_count == 3 and scan.series_count == 2 and len(scan.studies()) == 2
    jane = scan.patients["P1"]
    assert jane.name == "DOE^JANE" and jane.birth_date == "19800101" and jane.sex == "F"
    (study,) = jane.studies.values()
    assert (study.description, study.accession, study.date, study.modalities) == ("CT CHEST", "ACC1", "20261101", ["CT"])
    (series,) = study.series.values()
    assert [i.number for i in series.instances] == [1, 2] and series.modality == "CT"
    assert scan.size > 0


def test_scan_ignores_files_that_are_not_dicom_and_says_so(folder: Path) -> None:
    scan = di.scan_folder(folder)
    assert scan.skipped_files == 1 and scan.skipped_examples == ["notes.txt"]
    assert any(w.level == "info" and "not DICOM" in w.text for w in scan.warnings)


def test_scan_leaves_out_files_without_uids_and_duplicates(tmp_path: Path) -> None:
    study = str(generate_uid())
    make_file(tmp_path, "ok", study_uid=study, sop_uid="1.2.3.4")
    make_file(tmp_path, "dup", study_uid=study, sop_uid="1.2.3.4")  # the same SOP Instance UID again
    path, ds = make_file(tmp_path, "nouid")
    del ds.StudyInstanceUID
    ds.save_as(path, enforce_file_format=True)
    scan = di.scan_folder(tmp_path)
    assert scan.instance_count == 1
    levels = {(w.level, w.text.split()[0]) for w in scan.warnings}
    assert ("error", "1") in levels and ("warn", "1") in levels


def test_scan_warns_when_one_patient_id_has_two_names(tmp_path: Path) -> None:
    make_file(tmp_path, "a", patient_name="DOE^JANE")
    make_file(tmp_path, "b", patient_name="DOE^JANET")
    scan = di.scan_folder(tmp_path)
    assert scan.patients["P1"].names == ["DOE^JANE", "DOE^JANET"]
    assert any("different names" in w.text for w in scan.warnings)


def test_empty_folder_and_missing_folder(tmp_path: Path) -> None:
    scan = di.scan_folder(tmp_path)
    assert scan.instance_count == 0 and any(w.level == "error" for w in scan.warnings)
    with pytest.raises(di.ImportProblem):
        di.scan_folder(tmp_path / "nope")


def test_scan_reports_progress_and_can_be_cancelled(folder: Path) -> None:
    seen: list[int] = []
    di.scan_folder(folder, progress=lambda p: seen.append(p.files_seen))
    stop = threading.Event()
    stop.set()
    scan = di.scan_folder(folder, cancel=stop)
    assert scan.cancelled and scan.instance_count == 0
    assert any("stopped" in w.text for w in scan.warnings)


def test_scan_reads_a_dicomdir_and_checks_it_against_the_folder(tmp_path: Path) -> None:
    study = str(generate_uid())
    paths = [make_file(tmp_path / "src", f"IM{n}", study_uid=study, number=n)[0] for n in (1, 2)]
    media = tmp_path / "media"
    file_set = FileSet()
    for path in paths:
        file_set.add(str(path))
    file_set.write(str(media))
    scan = di.scan_folder(media)
    assert scan.dicomdirs == ["DICOMDIR"]
    assert scan.instance_count == 2
    assert all(i.in_dicomdir for s in scan.studies() for se in s.series.values() for i in se.instances)
    # A file the DICOMDIR lists is missing, and a stray one is not listed.
    victim = next((media).rglob("IM1")) if list(media.rglob("IM1")) else sorted(p for p in media.rglob("*") if p.is_file() and p.name != "DICOMDIR")[0]
    victim.unlink()
    make_file(media, "STRAY", study_uid=study, number=9)
    scan = di.scan_folder(media)
    texts = " ".join(w.text for w in scan.warnings)
    assert "not on disk" in texts and "not in the DICOMDIR" in texts


def test_a_broken_dicomdir_falls_back_to_the_files(tmp_path: Path) -> None:
    make_file(tmp_path, "IM1")
    # Looks like a DICOMDIR (right name and marker) but is not a usable one.
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = di.DICOMDIR_SOP_CLASS
    meta.MediaStorageSOPInstanceUID = str(generate_uid())
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    bad = FileDataset(str(tmp_path / "DICOMDIR"), {}, file_meta=meta, preamble=b"\0" * 128)
    bad.save_as(tmp_path / "DICOMDIR", enforce_file_format=True)
    scan = di.scan_folder(tmp_path)
    assert scan.instance_count == 1 and scan.dicomdirs == ["DICOMDIR"]
    assert any("could not be read" in w.text for w in scan.warnings)


# --- edits ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "name", "typed", "expected"),
    [
        ("patient", "birth_date", "1980-02-03", "19800203"),
        ("patient", "birth_date", "19800203", "19800203"),
        ("patient", "birth_date", "", ""),
        ("patient", "sex", "f", "F"),
        ("study", "date", "2026-11-19", "20261119"),
        ("study", "time", "14:30", "143000"),
        ("study", "time", "143005", "143005"),
        ("study", "accession", " ACC9 ", "ACC9"),
    ],
)
def test_normalise_value_accepts(kind, name, typed, expected) -> None:
    assert di.normalise_value(kind, name, typed) == expected


@pytest.mark.parametrize(
    ("kind", "name", "typed"),
    [
        ("patient", "birth_date", "19801301"),
        ("patient", "birth_date", "yesterday"),
        ("patient", "sex", "X"),
        ("study", "time", "25:00"),
        ("study", "accession", "A" * 17),
        ("patient", "name", "DOE\\JANE"),
        ("patient", "patient_id", "P\x001"),
    ],
)
def test_normalise_value_rejects(kind, name, typed) -> None:
    with pytest.raises(di.ImportProblem):
        di.normalise_value(kind, name, typed)


def test_edits_from_form_keeps_only_what_changed_and_reports_bad_values(folder: Path) -> None:
    scan = di.scan_folder(folder)
    study0 = scan.studies()[0]
    form = {
        "p0_name": "DOE^JANE",  # unchanged
        "p0_patient_id": "NEW1",
        "p0_birth_date": "1981-05-06",
        "p0_sex": "X",  # invalid
        "s0_description": study0.description,  # unchanged
        "s0_accession": "ACC9",
        "s0_time": "9:99",  # invalid
    }
    edits, errors = di.edits_from_form(scan, form, new_uids=True)
    assert edits.patients == {"P1": {"patient_id": "NEW1", "birth_date": "19810506"}}
    assert edits.studies == {study0.uid: {"accession": "ACC9"}}
    assert set(errors) == {"p0_sex", "s0_time"}
    assert edits.new_uids and edits.changed_count() == 3 and not edits.is_empty()
    assert di.ImportEdits().is_empty()


def test_apply_edits_changes_fields_and_remaps_uids_everywhere() -> None:
    sop, series, study, other = "1.1.1", "1.1.2", "1.1.3", "9.9.9"
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPInstanceUID = sop
    ds.SOPInstanceUID, ds.SeriesInstanceUID, ds.StudyInstanceUID = sop, series, study
    ds.PatientName, ds.PatientID, ds.AccessionNumber = "A^B", "P1", "OLD"
    item = Dataset()
    item.ReferencedSOPInstanceUID = sop  # points at one of the import's own images
    keep = Dataset()
    keep.ReferencedSOPInstanceUID = other  # points elsewhere: must stay
    ds.ReferencedImageSequence = Sequence([item, keep])
    edits = di.ImportEdits(patients={"P1": {"name": "X^Y"}}, studies={study: {"accession": "NEW"}}, new_uids=True)
    study_obj = di.ImportedStudy(uid=study, patient_key="P1")
    series_obj = di.ImportedSeries(uid=series)
    series_obj.instances.append(di.ImportedInstance("p", 1, str(CTImageStorage), sop, str(ExplicitVRLittleEndian)))
    study_obj.series[series] = series_obj
    uids = di.build_uid_map([study_obj])
    di.apply_edits(ds, "P1", study, edits, uids)
    assert (ds.PatientName, ds.PatientID, ds.AccessionNumber) == ("X^Y", "P1", "NEW")
    new_sop = uids.old_to_new[sop]
    assert ds.SOPInstanceUID == new_sop != sop and ds.file_meta.MediaStorageSOPInstanceUID == new_sop
    assert ds.StudyInstanceUID == uids.old_to_new[study] and ds.SeriesInstanceUID == uids.old_to_new[series]
    assert ds.ReferencedImageSequence[0].ReferencedSOPInstanceUID == new_sop
    assert ds.ReferencedImageSequence[1].ReferencedSOPInstanceUID == other


# --- sending -------------------------------------------------------------------


class Pacs:
    """A storage SCP that records what it receives."""

    def __init__(self, syntaxes=None, status=0x0000, fail_for: set[str] | None = None, drop_after: int | None = None) -> None:
        self.received: list[Dataset] = []
        self.port = _free_port()
        self.status = status
        self.fail_for = fail_for or set()
        self.syntaxes = syntaxes or [ImplicitVRLittleEndian, ExplicitVRLittleEndian]
        self.contexts: list[int] = []
        ae = AE(ae_title="PACS")
        ae.add_supported_context(CTImageStorage, self.syntaxes)
        ae.add_supported_context("1.2.840.10008.5.1.4.1.1.4", self.syntaxes)  # MR
        self.server = ae.start_server(("127.0.0.1", self.port), block=False, evt_handlers=[(evt.EVT_C_STORE, self._store)])
        time.sleep(0.05)

    def _store(self, event):
        ds = event.dataset
        ds.file_meta = event.file_meta
        if str(ds.SOPInstanceUID) in self.fail_for:
            return 0xA700
        self.received.append(ds)
        return self.status

    def node(self) -> RemoteNode:
        return RemoteNode(name="pacs", ae_title="PACS", host="127.0.0.1", port=self.port)

    def stop(self) -> None:
        self.server.shutdown()


@pytest.fixture
def pacs():
    server = Pacs()
    yield server
    server.stop()


LOCAL = LocalAE(ae_title="IMPORTER")


def _send(scan, edits, remote, **options):
    return di.send_import(scan, edits, LOCAL, remote, di.SendOptions(**options), di.SendProgress())


def test_send_stores_every_file_unchanged(folder: Path, pacs: Pacs) -> None:
    scan = di.scan_folder(folder)
    progress = _send(scan, di.ImportEdits(), pacs.node())
    assert progress.ok and (progress.total, progress.stored, progress.failed) == (3, 3, 0)
    assert progress.bytes_sent == scan.size and progress.done
    assert {str(d.PatientID) for d in pacs.received} == {"P1", "P2"}
    assert {str(d.SOPInstanceUID) for d in pacs.received} == {
        i.sop_instance_uid for s in scan.studies() for se in s.series.values() for i in se.instances
    }


def test_send_applies_the_edits_without_touching_the_files(folder: Path, pacs: Pacs) -> None:
    scan = di.scan_folder(folder)
    before = {p: p.read_bytes() for p in folder.rglob("IM*")}
    study = scan.patients["P1"].studies
    (study_uid,) = study
    edits = di.ImportEdits(
        patients={"P1": {"name": "NEW^NAME", "patient_id": "NEW1"}},
        studies={study_uid: {"accession": "ACC77", "description": "RENAMED"}},
    )
    progress = _send(scan, edits, pacs.node())
    assert progress.ok
    jane = [d for d in pacs.received if str(d.PatientID) == "NEW1"]
    assert len(jane) == 2 and all(str(d.PatientName) == "NEW^NAME" and d.AccessionNumber == "ACC77" and d.StudyDescription == "RENAMED" for d in jane)
    roe = [d for d in pacs.received if str(d.PatientID) == "P2"]
    assert len(roe) == 1 and str(roe[0].PatientName) == "ROE^RICHARD" and roe[0].AccessionNumber == "ACC1"
    assert {p: p.read_bytes() for p in folder.rglob("IM*")} == before  # the source files are never modified


def test_send_can_give_everything_new_uids_consistently(folder: Path, pacs: Pacs) -> None:
    scan = di.scan_folder(folder)
    old = {i.sop_instance_uid for s in scan.studies() for se in s.series.values() for i in se.instances}
    progress = _send(scan, di.ImportEdits(new_uids=True), pacs.node())
    assert progress.ok and len(pacs.received) == 3
    assert not {str(d.SOPInstanceUID) for d in pacs.received} & old
    jane = [d for d in pacs.received if str(d.PatientID) == "P1"]
    assert len({str(d.StudyInstanceUID) for d in jane}) == 1 and len({str(d.SeriesInstanceUID) for d in jane}) == 1
    assert str(jane[0].StudyInstanceUID) not in {s.uid for s in scan.studies()}


def test_send_only_the_chosen_studies(folder: Path, pacs: Pacs) -> None:
    scan = di.scan_folder(folder)
    (jane_study,) = scan.patients["P1"].studies
    progress = _send(scan, di.ImportEdits(), pacs.node(), study_uids={jane_study})
    assert progress.total == 2 and {str(d.PatientID) for d in pacs.received} == {"P1"}


def test_a_compressed_image_is_decompressed_when_the_pacs_will_not_take_it(tmp_path: Path, pacs: Pacs) -> None:
    path, ds = make_file(tmp_path, "RLE")
    ds.compress(RLELossless)
    ds.save_as(path, enforce_file_format=True)
    scan = di.scan_folder(tmp_path)
    assert any("compressed" in w.text for w in scan.warnings)
    progress = _send(scan, di.ImportEdits(), pacs.node())
    assert progress.ok and progress.decompressed == 1
    assert str(pacs.received[0].file_meta.TransferSyntaxUID) in (str(ExplicitVRLittleEndian), str(ImplicitVRLittleEndian))
    assert pacs.received[0].pixel_array.shape == (8, 8)


def test_a_compressed_image_fails_clearly_when_decompressing_is_off(tmp_path: Path, pacs: Pacs) -> None:
    path, ds = make_file(tmp_path, "RLE")
    ds.compress(RLELossless)
    ds.save_as(path, enforce_file_format=True)
    progress = _send(di.scan_folder(tmp_path), di.ImportEdits(), pacs.node(), decompress=False)
    assert progress.failed == 1 and not progress.ok and "presentation context" in progress.failures[0].reason.lower()


def test_a_compressed_image_goes_as_it_is_when_the_pacs_takes_it(tmp_path: Path) -> None:
    server = Pacs(syntaxes=[RLELossless, ExplicitVRLittleEndian])
    try:
        path, ds = make_file(tmp_path, "RLE")
        ds.compress(RLELossless)
        ds.save_as(path, enforce_file_format=True)
        progress = _send(di.scan_folder(tmp_path), di.ImportEdits(), server.node())
        assert progress.ok and progress.decompressed == 0
        assert str(server.received[0].file_meta.TransferSyntaxUID) == str(RLELossless)
    finally:
        server.stop()


def test_failures_are_counted_and_listed_while_the_rest_goes_on(folder: Path) -> None:
    scan = di.scan_folder(folder)
    bad = next(iter(scan.patients["P1"].studies.values())).series
    bad_uid = next(iter(bad.values())).instances[0].sop_instance_uid
    server = Pacs(fail_for={bad_uid})
    try:
        progress = _send(scan, di.ImportEdits(), server.node())
        assert (progress.stored, progress.failed) == (2, 1) and not progress.ok
        assert progress.failures[0].reason == "C-STORE status 0xA700" and progress.failures[0].path.endswith("IM1")
    finally:
        server.stop()


def test_a_warning_status_counts_as_stored(folder: Path) -> None:
    server = Pacs(status=0xB000)
    try:
        progress = _send(di.scan_folder(folder), di.ImportEdits(), server.node())
        assert (progress.stored, progress.warnings, progress.failed) == (3, 3, 0)
    finally:
        server.stop()


def test_an_unreachable_pacs_is_reported_not_raised(folder: Path) -> None:
    node = RemoteNode(name="nobody", ae_title="NOBODY", host="127.0.0.1", port=_free_port())
    progress = _send(di.scan_folder(folder), di.ImportEdits(), node)
    assert progress.done and progress.not_attempted == 3 and "did not accept" in progress.message and not progress.ok


def test_send_can_be_cancelled(folder: Path, pacs: Pacs) -> None:
    stop = threading.Event()
    stop.set()
    progress = di.send_import(di.scan_folder(folder), di.ImportEdits(), LOCAL, pacs.node(), di.SendOptions(), di.SendProgress(), stop)
    assert progress.cancelled and progress.stored == 0 and progress.not_attempted == 3 and not progress.ok


def test_nothing_selected_is_a_clear_message(folder: Path, pacs: Pacs) -> None:
    progress = _send(di.scan_folder(folder), di.ImportEdits(), pacs.node(), study_uids=set())
    assert progress.message == "Nothing is selected to send." and progress.done


def test_a_file_that_vanished_after_the_scan_is_one_failure(folder: Path, pacs: Pacs) -> None:
    scan = di.scan_folder(folder)
    next(folder.rglob("IM3")).unlink()
    progress = _send(scan, di.ImportEdits(), pacs.node())
    assert (progress.stored, progress.failed) == (2, 1) and "Could not read" in progress.failures[0].reason
