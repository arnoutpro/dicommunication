from __future__ import annotations

import io
import socket
import time
import zipfile
from pathlib import Path

import pytest
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import EncapsulatedPDFStorage, SecondaryCaptureImageStorage, Verification

from app.models import LocalAE, RemoteNode
from app.pdf_dicom import (
    CollectError,
    PdfSource,
    collect_from_directory,
    collect_from_zip,
    collect_pdfs,
    encapsulate_pdf,
    is_pdf,
    list_directory_pdfs,
    resolve_patient_identities,
)
from app.tools.pdf_store import PdfStoreTool

MINIMAL_PDF = (
    b"%PDF-1.1\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]/Parent 2 0 R>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_scp(ae_title: str, port: int, contexts, handlers):
    scp = AE(ae_title=ae_title)
    for context in contexts:
        scp.add_supported_context(context)
    return scp.start_server(("127.0.0.1", port), block=False, evt_handlers=handlers)


def test_minimal_pdf_magic() -> None:
    assert is_pdf(MINIMAL_PDF)
    assert not is_pdf(b"PK\x03\x04not a pdf")
    assert not is_pdf(b"")


def test_encapsulate_pdf_dataset() -> None:
    source = PdfSource(name="report.pdf", data=MINIMAL_PDF + b"X")  # odd length
    ds = encapsulate_pdf(
        source,
        patient_name="DOE^JANE",
        patient_id="1001",
        accession_number="ACC9",
        study_description="External report",
        document_title="Discharge",
    )
    assert str(ds.SOPClassUID) == str(EncapsulatedPDFStorage)
    assert ds.Modality == "DOC"
    assert ds.MIMETypeOfEncapsulatedDocument == "application/pdf"
    assert ds.ConversionType == "WSD"
    assert ds.DocumentTitle == "Discharge"
    assert str(ds.PatientName) == "DOE^JANE"
    assert ds.PatientID == "1001"
    assert ds.AccessionNumber == "ACC9"
    assert len(ds.EncapsulatedDocument) % 2 == 0
    assert bytes(ds.EncapsulatedDocument).rstrip(b"\x00").startswith(b"%PDF")
    assert ds.file_meta.MediaStorageSOPClassUID == EncapsulatedPDFStorage


def test_zip_skips_non_pdf_and_path_traversal() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("keep/report.pdf", MINIMAL_PDF)
        archive.writestr("notes.txt", b"hello")
        archive.writestr("../escape.pdf", MINIMAL_PDF)
        archive.writestr("__MACOSX/._skip.pdf", MINIMAL_PDF)
        archive.writestr("notpdf.pdf", b"this is not a pdf")
    sources, warnings = collect_from_zip(buffer.getvalue())
    names = [item.name for item in sources]
    assert names == ["report.pdf"]
    assert any("unsafe path" in note or "escape" in note for note in warnings)
    assert any("not a PDF" in note for note in warnings)


def test_collect_from_directory(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(MINIMAL_PDF)
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.pdf").write_bytes(MINIMAL_PDF)
    (tmp_path / "ignore.txt").write_text("nope", encoding="utf-8")
    (tmp_path / ".hidden.pdf").write_bytes(MINIMAL_PDF)
    sources, warnings = collect_from_directory(tmp_path)
    names = sorted(item.name for item in sources)
    assert names == ["a.pdf", "b.pdf"]
    assert warnings == []


def test_directory_missing_raises() -> None:
    with pytest.raises(CollectError, match="Path not found"):
        collect_from_directory("/no/such/pdf-dir-e318")


def test_encapsulate_only_without_remote() -> None:
    result = PdfStoreTool().run(
        LocalAE(timeout_seconds=5),
        None,
        {
            "patient_name": "DOE^JANE",
            "patient_id": "1001",
            "send": False,
            "pdfs": [{"filename": "note.pdf", "content": MINIMAL_PDF}],
        },
    )
    assert result.ok, result.summary
    assert "not sent" in result.summary
    assert result.records[0]["status"] == "encapsulated"
    assert result.records[0]["patient_id"] == "1001"
    assert result.records[0]["sop_class"] == "Encapsulated PDF Storage"


def test_requires_patient_identifiers() -> None:
    result = PdfStoreTool().run(
        LocalAE(),
        None,
        {"send": False, "pdfs": [{"filename": "note.pdf", "content": MINIMAL_PDF}]},
    )
    assert result.ok is False
    assert "Patient Name" in result.summary


def test_generate_shared_patient_identity() -> None:
    result = PdfStoreTool().run(
        LocalAE(),
        None,
        {
            "send": False,
            "generate_name": True,
            "generate_id": True,
            "pdfs": [
                {"filename": "a.pdf", "content": MINIMAL_PDF},
                {"filename": "b.pdf", "content": MINIMAL_PDF},
            ],
        },
    )
    assert result.ok, result.summary
    assert result.records[0]["patient_name"].startswith("ARNPRO^PDF")
    assert result.records[0]["patient_id"].startswith("PDF")
    assert result.records[0]["patient_id"] == result.records[1]["patient_id"]


def test_unique_patient_per_pdf() -> None:
    identities = resolve_patient_identities(
        [
            PdfSource(name="discharge.pdf", data=MINIMAL_PDF),
            PdfSource(name="lab.pdf", data=MINIMAL_PDF),
        ],
        patient_name="",
        patient_id="",
        unique_patient=True,
    )
    assert identities[0][0].startswith("PDF^")
    assert identities[0][1] != identities[1][1]
    assert "DISCHARGE" in identities[0][1]
    result = PdfStoreTool().run(
        LocalAE(),
        None,
        {
            "send": False,
            "unique_patient": True,
            "pdfs": [
                {"filename": "discharge.pdf", "content": MINIMAL_PDF},
                {"filename": "lab.pdf", "content": MINIMAL_PDF},
            ],
        },
    )
    assert result.ok, result.summary
    assert result.records[0]["patient_id"] != result.records[1]["patient_id"]
    assert result.records[0]["study_instance_uid"] != result.records[1]["study_instance_uid"]


def test_list_directory_pdfs_counts_without_sending(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(MINIMAL_PDF)
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.pdf").write_bytes(MINIMAL_PDF)
    (tmp_path / "skip.txt").write_text("nope", encoding="utf-8")
    listing = list_directory_pdfs(tmp_path)
    assert listing["ok"] is True
    assert listing["pdf_count"] == 2
    assert listing["sendable"] == 2
    names = {item["name"] for item in listing["files"]}
    assert names == {"a.pdf", "b.pdf"}


def test_pdf_c_store_against_in_process_scp() -> None:
    port = _free_port()
    received: list[Dataset] = []

    def handle_store(event):
        received.append(event.dataset)
        return 0x0000

    server = _start_scp(
        "PDF_SCP",
        port,
        [EncapsulatedPDFStorage],
        [(evt.EVT_C_STORE, handle_store)],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pdf store", ae_title="PDF_SCP", host="127.0.0.1", port=port)
        result = PdfStoreTool().run(
            LocalAE(timeout_seconds=5),
            remote,
            {
                "patient_name": "DOE^JANE",
                "patient_id": "1001",
                "same_study": True,
                "pdfs": [
                    {"filename": "one.pdf", "content": MINIMAL_PDF},
                    {"filename": "two.pdf", "content": MINIMAL_PDF},
                ],
            },
        )
        assert result.ok, result.summary
        assert len(received) == 2
        assert str(received[0].PatientID) == "1001"
        assert received[0].Modality == "DOC"
        assert received[0].StudyInstanceUID == received[1].StudyInstanceUID
        assert {row["status"] for row in result.records} == {"stored"}
        assert any(ctx["accepted"] for ctx in result.contexts)
    finally:
        server.shutdown()


def test_pdf_store_rejected_when_peer_is_secondary_capture_only() -> None:
    port = _free_port()
    server = _start_scp(
        "SC_ONLY",
        port,
        [SecondaryCaptureImageStorage, Verification],
        [(evt.EVT_C_ECHO, lambda event: 0x0000)],
    )
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="sc only", ae_title="SC_ONLY", host="127.0.0.1", port=port)
        result = PdfStoreTool().run(
            LocalAE(timeout_seconds=5),
            remote,
            {
                "patient_name": "DOE^JANE",
                "patient_id": "1001",
                "pdfs": [{"filename": "note.pdf", "content": MINIMAL_PDF}],
            },
        )
        assert result.ok is False
        assert result.contexts
        assert "Encapsulated PDF" in result.summary
    finally:
        server.shutdown()


def test_form_upload_encapsulates_without_sending(client, tmp_path: Path) -> None:
    pdf_path = tmp_path / "letter.pdf"
    pdf_path.write_bytes(MINIMAL_PDF)
    response = client.post(
        "/tools/pdf-store/run",
        data={
            "patient_name": "DOE^JANE",
            "patient_id": "1001",
            "same_study": "on",
        },
        files={"pdfs": ("letter.pdf", pdf_path.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    assert b"Encapsulated" in response.content
    assert b"not sent" in response.content or b"encapsulated" in response.content


def test_json_api_directory_without_send(client, tmp_path: Path) -> None:
    (tmp_path / "scan.pdf").write_bytes(MINIMAL_PDF)
    response = client.post(
        "/api/tools/pdf-store/run",
        json={
            "options": {
                "patient_name": "DOE^JANE",
                "patient_id": "1001",
                "directory": str(tmp_path),
                "send": False,
            }
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["records"][0]["source"] == "scan.pdf"
    assert body["records"][0]["status"] == "encapsulated"


def test_collect_pdfs_combines_zip_and_directory(tmp_path: Path) -> None:
    (tmp_path / "disk.pdf").write_bytes(MINIMAL_PDF)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("zipped.pdf", MINIMAL_PDF)
    sources, warnings = collect_pdfs(
        {"zip_bytes": buffer.getvalue(), "directory": str(tmp_path)}
    )
    assert sorted(item.name for item in sources) == ["disk.pdf", "zipped.pdf"]
    assert warnings == []


def test_scan_api_lists_directory(client, tmp_path: Path) -> None:
    (tmp_path / "one.pdf").write_bytes(MINIMAL_PDF)
    listed = client.get("/api/tools/pdf-store/scan", params={"directory": str(tmp_path)})
    assert listed.status_code == 200
    body = listed.json()
    assert body["pdf_count"] == 1
    assert body["files"][0]["name"] == "one.pdf"
    missing = client.get("/api/tools/pdf-store/scan", params={"directory": str(tmp_path / "nope")})
    assert missing.status_code == 400
    htmx = client.post(
        "/tools/pdf-store/scan",
        data={"directory": str(tmp_path)},
        headers={"HX-Request": "true"},
    )
    assert htmx.status_code == 200
    assert b"1 PDF" in htmx.content
    assert b"one.pdf" in htmx.content


def test_pick_directory_unavailable_without_desktop(client, monkeypatch) -> None:
    monkeypatch.setenv("DICOMM_NO_DIALOGS", "1")
    response = client.post("/api/fs/pick-directory")
    assert response.status_code == 503
