from __future__ import annotations

import re

import io
import socket
import struct
import time
import zipfile
from datetime import date
from pathlib import Path

import pytest
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import EncapsulatedPDFStorage, SecondaryCaptureImageStorage, Verification

from app.models import LocalAE, RemoteNode
from app.pdf_dicom import (
    MAX_FILES,
    CollectError,
    PdfSource,
    collect_from_directory,
    collect_from_uploads,
    collect_from_zip,
    collect_pdfs,
    encapsulate_pdf,
    encapsulate_sources,
    is_pdf,
    iter_directory_pdfs,
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
    assert any("non-PDF ZIP" in note for note in warnings)


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
    (tmp_path / "fake.pdf").write_bytes(b"this is not a pdf")
    listing = list_directory_pdfs(tmp_path)
    assert listing["ok"] is True
    assert listing["pdf_count"] == 2
    assert listing["sendable"] == 2
    assert listing["skipped_other"] >= 1
    assert listing["skipped_not_pdf"] == 1
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
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    listed = client.get("/api/tools/pdf-store/scan", params={"directory": str(tmp_path)})
    assert listed.status_code == 200
    body = listed.json()
    assert body["pdf_count"] == 1
    assert body["skipped_other"] >= 1
    assert body["files"][0]["name"] == "one.pdf"
    missing = client.get("/api/tools/pdf-store/scan", params={"directory": str(tmp_path / "nope")})
    assert missing.status_code == 400
    htmx = client.post(
        "/tools/pdf-store/scan",
        data={"directory": str(tmp_path)},
        headers={"HX-Request": "true"},
    )
    assert htmx.status_code == 200
    assert b"<strong>1</strong> PDF" in htmx.content
    assert b"one.pdf" in htmx.content


def test_uploads_reject_non_pdf_filenames() -> None:
    sources, warnings = collect_from_uploads(
        [
            {"filename": "note.pdf", "content": MINIMAL_PDF},
            {"filename": "photo.jpg", "content": b"\xff\xd8\xff"},
        ]
    )
    assert [item.name for item in sources] == ["note.pdf"]
    assert any("photo.jpg" in note and "not a PDF" in note for note in warnings)


def test_form_upload_rejects_non_pdf(client) -> None:
    response = client.post(
        "/tools/pdf-store/run",
        data={"patient_name": "DOE^JANE", "patient_id": "1001", "same_study": "on"},
        files={"pdfs": ("photo.jpg", b"not-a-pdf", "image/jpeg")},
    )
    assert response.status_code == 200
    assert b"not a PDF" in response.content or b"No PDF" in response.content


def test_pick_directory_unavailable_without_desktop(client, monkeypatch) -> None:
    monkeypatch.setenv("DICOMM_NO_DIALOGS", "1")
    response = client.post("/api/fs/pick-directory")
    assert response.status_code == 503


def _zip_with_compression_method(method: int) -> bytes:
    """A structurally valid ZIP whose entry claims a codec zipfile cannot decode."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("report.pdf", MINIMAL_PDF)
    raw = bytearray(buffer.getvalue())
    for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        index = raw.find(signature)
        while index >= 0:
            struct.pack_into("<H", raw, index + offset, method)
            index = raw.find(signature, index + 4)
    return bytes(raw)


def test_undecodable_zip_entry_is_skipped_not_raised() -> None:
    sources, warnings = collect_from_zip(_zip_with_compression_method(99))

    assert sources == []
    assert any("unreadable ZIP entry" in note for note in warnings)


def test_undecodable_zip_entry_does_not_500(client) -> None:
    response = client.post(
        "/tools/pdf-store/run",
        data={"patient_name": "DOE^JANE", "patient_id": "1001", "send": ""},
        files={"zip_file": ("reports.zip", _zip_with_compression_method(99), "application/zip")},
    )

    assert response.status_code == 200


def test_truncated_zip_reports_a_readable_error() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("report.pdf", MINIMAL_PDF)
    with pytest.raises(CollectError, match="Not a valid ZIP archive"):
        collect_from_zip(buffer.getvalue()[:20])


def test_directory_scan_stops_at_the_entry_budget(tmp_path, monkeypatch) -> None:
    for index in range(200):
        (tmp_path / f"report{index:04d}.pdf").write_bytes(MINIMAL_PDF)

    _root, all_hits, _skipped = iter_directory_pdfs(tmp_path)
    assert len(all_hits) == 200

    monkeypatch.setattr("app.pdf_dicom.MAX_SCAN_ENTRIES", 25)
    _root, capped_hits, _skipped = iter_directory_pdfs(tmp_path)

    assert len(capped_hits) == 25


def test_directory_scan_does_not_descend_past_max_depth(tmp_path) -> None:
    (tmp_path / "top.pdf").write_bytes(MINIMAL_PDF)
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "allowed.pdf").write_bytes(MINIMAL_PDF)
    too_deep = deep / "d"
    too_deep.mkdir()
    (too_deep / "ignored.pdf").write_bytes(MINIMAL_PDF)

    _root, hits, _skipped = iter_directory_pdfs(tmp_path)

    assert sorted(relative for _path, relative in hits) == ["a/b/c/allowed.pdf", "top.pdf"]


def test_directory_scan_skips_symlinks(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "keep.pdf").write_bytes(MINIMAL_PDF)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(MINIMAL_PDF)
    (real / "link").symlink_to(outside, target_is_directory=True)
    (real / "loop").symlink_to(real, target_is_directory=True)

    _root, hits, _skipped = iter_directory_pdfs(real)

    assert [relative for _path, relative in hits] == ["keep.pdf"]


def test_upload_batch_over_the_cap_is_rejected_without_buffering_everything(client) -> None:
    big = MINIMAL_PDF + b"\0" * (256 * 1024)
    files = [("pdfs", (f"r{i:03d}.pdf", big, "application/pdf")) for i in range(MAX_FILES * 4)]

    response = client.post(
        "/tools/pdf-store/run",
        data={"patient_name": "DOE^JANE", "patient_id": "1001", "send": ""},
        files=files,
    )

    assert response.status_code == 200
    assert b"Too many PDFs" in response.content


def test_upload_batch_at_the_cap_still_works(client) -> None:
    files = [("pdfs", (f"r{i:03d}.pdf", MINIMAL_PDF, "application/pdf")) for i in range(MAX_FILES)]

    response = client.post(
        "/tools/pdf-store/run",
        data={"patient_name": "DOE^JANE", "patient_id": "1001", "send": ""},
        files=files,
    )

    assert response.status_code == 200
    assert b"Too many PDFs" not in response.content


def test_pdf_store_page_is_four_steps_with_one_source(client, remote) -> None:
    page = client.get("/tools/pdf-store").text
    for title in ("Choose PDFs", "Patient", "Study and document", "Send"):
        assert f'</span>{title}</h2>' in page
    # Files is the default source; the other sources' inputs don't submit.
    assert '<input type="radio" name="source" value="files" checked' in page
    assert re.search(r'name="pdfs" type="file"[^>]*multiple aria-label="PDF files">', page)
    assert re.search(r'name="zip_file"[^>]*disabled>', page)
    assert re.search(r'name="folder"[^>]*disabled>', page)
    assert re.search(r'name="directory"[^>]*data-directory-input disabled>', page)
    # The Generate toggle has its own label, so the field label focuses the input.
    assert '<label class="label-line" for="pdf-patient-name">' in page
    assert 'id="pdf-patient-name" name="patient_name"' in page
    assert "Encapsulate &amp; send" in page


def test_pdf_store_failed_directory_run_reopens_on_directory(client, tmp_path) -> None:
    missing = tmp_path / "nope"
    response = client.post(
        "/tools/pdf-store/run",
        data={"directory": str(missing), "patient_name": "DOE^JANE", "patient_id": "1"},
    )
    page = response.text
    assert "Path not found" in page
    # The path stays visible under the source it belongs to.
    assert '<input type="radio" name="source" value="directory" checked' in page
    assert re.search(r'name="directory" value="%s"[^>]*data-directory-input>' % re.escape(str(missing)), page)
    assert re.search(r'name="pdfs"[^>]*disabled>', page)


def _three_pdfs() -> list[PdfSource]:
    return [PdfSource(name=f"{stem}.pdf", data=MINIMAL_PDF) for stem in ("referral", "discharge", "lab")]


def test_typed_values_go_on_every_pdf_as_they_are() -> None:
    datasets = encapsulate_sources(
        _three_pdfs(),
        patient_name="DOE^JANE",
        patient_id="1001",
        accession_number="ACC42",
        study_description="External reports",
        document_title="Outside letter",
        same_study=True,
    )
    assert {ds.AccessionNumber for ds in datasets} == {"ACC42"}
    assert {ds.StudyDescription for ds in datasets} == {"External reports"}
    # No per-file suffix: the title is what was typed.
    assert {ds.DocumentTitle for ds in datasets} == {"Outside letter"}


def test_generated_values_one_study() -> None:
    datasets = encapsulate_sources(
        _three_pdfs(),
        patient_name="DOE^JANE",
        patient_id="1001",
        accession_number="ignored",
        document_title="ignored",
        same_study=True,
        generate_accession=True,
        generate_study_description=True,
        generate_document_title=True,
    )
    accessions = {ds.AccessionNumber for ds in datasets}
    assert len(accessions) == 1
    [accession] = accessions
    assert accession.startswith("ACC") and len(accession) <= 16 and accession != "ignored"
    assert {ds.StudyDescription for ds in datasets} == {f"PDF import {date.today():%Y-%m-%d}"}
    assert [ds.DocumentTitle for ds in datasets] == ["referral", "discharge", "lab"]


def test_generated_values_one_study_per_pdf() -> None:
    datasets = encapsulate_sources(
        _three_pdfs(),
        patient_name="DOE^JANE",
        patient_id="1001",
        same_study=False,
        generate_accession=True,
        generate_study_description=True,
    )
    assert len({ds.AccessionNumber for ds in datasets}) == 3
    assert [ds.StudyDescription for ds in datasets] == ["referral", "discharge", "lab"]
    assert len({ds.StudyInstanceUID for ds in datasets}) == 3


def test_form_generate_flags_reach_the_documents(client, tmp_path: Path) -> None:
    response = client.post(
        "/tools/pdf-store/run",
        data={
            "patient_name": "DOE^JANE",
            "patient_id": "1001",
            "same_study": "on",
            "generate_accession": "on",
            "document_title": "Scanned letter",
        },
        files=[
            ("pdfs", ("a.pdf", MINIMAL_PDF, "application/pdf")),
            ("pdfs", ("b.pdf", MINIMAL_PDF, "application/pdf")),
        ],
    )
    assert response.status_code == 200
    page = response.text
    # Page comes back with the choices kept.
    assert re.search(r'name="generate_accession" value="on" checked', page)
    assert re.search(r'id="pdf-accession_number"[^>]*disabled>', page)
    assert not re.search(r'name="generate_document_title" value="on" checked', page)
    assert 'value="Scanned letter"' in page


def test_document_title_generates_by_default(client) -> None:
    page = client.get("/tools/pdf-store").text
    assert re.search(r'name="generate_document_title" value="on" checked', page)
    assert re.search(r'id="pdf-document_title"[^>]*disabled>', page)
    assert not re.search(r'name="generate_accession" value="on" checked', page)
