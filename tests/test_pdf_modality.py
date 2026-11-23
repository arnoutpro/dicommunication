"""PDF to DICOM: choosing the Modality the Encapsulated PDF is stored with."""

from __future__ import annotations

import time

import pytest
from pydicom.dataset import Dataset
from pynetdicom import evt
from pynetdicom.sop_class import EncapsulatedPDFStorage

from app.models import LocalAE, RemoteNode
from app.pdf_dicom import (
    DEFAULT_MODALITY,
    MODALITY_SUGGESTIONS,
    CollectError,
    PdfSource,
    encapsulate_pdf,
    encapsulate_sources,
    normalize_modality,
)
from app.tools.pdf_store import PdfStoreTool
from tests.test_pdf_store import MINIMAL_PDF, _free_port, _start_scp

SOURCE = PdfSource(name="note.pdf", data=MINIMAL_PDF)
IDENTITY = {"patient_name": "DOE^JANE", "patient_id": "1001"}


# --- the value ------------------------------------------------------------------------


def test_blank_means_doc_and_the_suggestions_start_with_it() -> None:
    assert DEFAULT_MODALITY == "DOC" and MODALITY_SUGGESTIONS[0] == "DOC" and {"SR", "OT"} <= set(MODALITY_SUGGESTIONS)
    for blank in (None, "", "   "):
        assert normalize_modality(blank) == "DOC"


def test_a_code_is_trimmed_and_put_in_capitals() -> None:
    assert normalize_modality(" sr ") == "SR"
    assert normalize_modality("ot") == "OT"
    assert normalize_modality("My_Doc 2") == "MY_DOC 2"
    assert normalize_modality("A" * 16) == "A" * 16
    assert normalize_modality("DOC\n") == "DOC"  # whitespace around it is trimmed


@pytest.mark.parametrize("bad", ["A" * 17, "S\\R", "S/R", "SR;", "säd", "DO\nC", "<b>", "ÉCHO", "sr!"])
def test_an_invalid_code_is_refused_with_a_readable_message(bad: str) -> None:
    with pytest.raises(CollectError) as caught:
        normalize_modality(bad)
    assert "not a valid Modality" in str(caught.value) and "DOC, SR or OT" in str(caught.value)


def test_a_long_bad_value_is_cut_in_the_message() -> None:
    with pytest.raises(CollectError) as caught:
        normalize_modality("x" * 5 + "!" * 500)
    assert len(str(caught.value)) < 200


# --- the object -----------------------------------------------------------------------


def test_the_instance_keeps_doc_by_default_and_takes_the_chosen_modality() -> None:
    assert encapsulate_pdf(SOURCE, **IDENTITY).Modality == "DOC"
    ds = encapsulate_pdf(SOURCE, modality="sr", **IDENTITY)
    assert ds.Modality == "SR"
    # Only the tag changes: still an Encapsulated PDF with the PDF inside.
    assert ds.SOPClassUID == EncapsulatedPDFStorage and ds.MIMETypeOfEncapsulatedDocument == "application/pdf"
    assert ds.EncapsulatedDocument.startswith(b"%PDF")


def test_every_pdf_of_a_batch_gets_the_modality() -> None:
    batch = encapsulate_sources([SOURCE, PdfSource(name="b.pdf", data=MINIMAL_PDF)], modality="OT", **IDENTITY)
    assert [ds.Modality for ds in batch] == ["OT", "OT"]
    assert [ds.Modality for ds in encapsulate_sources([SOURCE], **IDENTITY)] == ["DOC"]


# --- the tool -------------------------------------------------------------------------


def test_the_tool_reports_the_modality_in_its_step_and_records() -> None:
    result = PdfStoreTool().run(LocalAE(timeout_seconds=5), None, {**IDENTITY, "send": False, "modality": "sr", "pdfs": [{"filename": "note.pdf", "content": MINIMAL_PDF}]})
    assert result.ok, result.summary
    assert result.records[0]["modality"] == "SR"
    assert "modality SR" in next(step.message for step in result.steps if step.name == "Encapsulate")


def test_the_tool_defaults_to_doc_when_the_option_is_missing_or_blank() -> None:
    for options in ({}, {"modality": ""}):
        result = PdfStoreTool().run(LocalAE(timeout_seconds=5), None, {**IDENTITY, **options, "send": False, "pdfs": [{"filename": "n.pdf", "content": MINIMAL_PDF}]})
        assert result.ok and result.records[0]["modality"] == "DOC"


def test_an_invalid_modality_fails_before_anything_is_built_or_sent() -> None:
    result = PdfStoreTool().run(LocalAE(timeout_seconds=5), None, {**IDENTITY, "send": False, "modality": "S/R", "pdfs": [{"filename": "n.pdf", "content": MINIMAL_PDF}]})
    assert not result.ok and "not a valid Modality" in result.summary and not result.records


def test_the_chosen_modality_reaches_the_pacs() -> None:
    port = _free_port()
    received: list[Dataset] = []

    def handle_store(event):
        received.append(event.dataset)
        return 0x0000

    server = _start_scp("PDF_SCP", port, [EncapsulatedPDFStorage], [(evt.EVT_C_STORE, handle_store)])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pdf store", ae_title="PDF_SCP", host="127.0.0.1", port=port)
        result = PdfStoreTool().run(LocalAE(timeout_seconds=5), remote, {**IDENTITY, "modality": "SR", "pdfs": [{"filename": "n.pdf", "content": MINIMAL_PDF}]})
        assert result.ok, result.summary
        assert [ds.Modality for ds in received] == ["SR"]
    finally:
        server.shutdown()


# --- the page -------------------------------------------------------------------------


def test_the_form_offers_the_modality_with_doc_filled_in(client) -> None:
    page = client.get("/tools/pdf-store").text
    assert 'name="modality"' in page and 'value="DOC"' in page and 'list="pdf-modality-options"' in page
    for choice in MODALITY_SUGGESTIONS:
        assert f'<option value="{choice}">' in page


def test_the_form_sends_the_modality_and_shows_it_in_the_result(client) -> None:
    response = client.post(
        "/tools/pdf-store/run",
        data={**IDENTITY, "same_study": "on", "modality": "ot"},
        files={"pdfs": ("letter.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 200 and "modality OT" in response.text
    assert 'value="OT"' in response.text  # what was chosen stays in the form for the next run


def test_a_bad_modality_on_the_form_gives_a_clear_error_and_keeps_what_was_typed(client) -> None:
    response = client.post(
        "/tools/pdf-store/run",
        data={**IDENTITY, "same_study": "on", "modality": "S/R"},
        files={"pdfs": ("letter.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert "not a valid Modality" in response.text and 'value="S/R"' in response.text.replace("&#34;", '"')
