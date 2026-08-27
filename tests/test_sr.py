from __future__ import annotations

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from pynetdicom.sop_class import BasicTextSRStorage

from app.sr import flatten_sr, is_structured_report, parse_sr, sr_plain_text


def _code(value: str, meaning: str, scheme: str = "DCM") -> Dataset:
    item = Dataset()
    item.CodeValue = value
    item.CodingSchemeDesignator = scheme
    item.CodeMeaning = meaning
    return item


def make_radiology_sr(*, study_uid: str = "1.2.3", series_uid: str = "1.2.3.99") -> Dataset:
    ds = Dataset()
    ds.SOPClassUID = BasicTextSRStorage
    ds.SOPInstanceUID = generate_uid()
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = "SR"
    ds.PatientName = "DOE^JANE"
    ds.PatientID = "1001"
    ds.StudyDate = "20260826"
    ds.AccessionNumber = "ACC1"
    ds.StudyDescription = "CT CHEST"
    ds.SeriesDescription = "Report"
    ds.CompletionFlag = "COMPLETE"
    ds.VerificationFlag = "VERIFIED"
    ds.ValueType = "CONTAINER"
    ds.ContinuityOfContent = "SEPARATE"
    ds.ConceptNameCodeSequence = [_code("11528-7", "Radiology Report", "LN")]

    findings = Dataset()
    findings.RelationshipType = "CONTAINS"
    findings.ValueType = "CONTAINER"
    findings.ConceptNameCodeSequence = [_code("121070", "Findings")]

    finding = Dataset()
    finding.RelationshipType = "CONTAINS"
    finding.ValueType = "TEXT"
    finding.ConceptNameCodeSequence = [_code("121071", "Finding")]
    finding.TextValue = "No acute osseous abnormality."

    impression = Dataset()
    impression.RelationshipType = "CONTAINS"
    impression.ValueType = "TEXT"
    impression.ConceptNameCodeSequence = [_code("121073", "Impression")]
    impression.TextValue = "Normal CT chest."

    findings.ContentSequence = [finding]
    ds.ContentSequence = [findings, impression]

    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    return ds


def test_flatten_sr_walks_content_sequence() -> None:
    ds = make_radiology_sr()
    assert is_structured_report(ds)
    parsed = parse_sr(ds)
    assert parsed["document_title"] == "Radiology Report"
    assert parsed["completion_flag"] == "COMPLETE"
    names = [item["name"] for item in parsed["items"]]
    assert names == ["Findings", "Finding", "Impression"]
    finding = parsed["items"][1]
    assert finding["value_type"] == "TEXT"
    assert finding["value"] == "No acute osseous abnormality."
    assert finding["depth"] == 1
    text = sr_plain_text(parsed["items"])
    assert "Finding: No acute osseous abnormality." in text
    assert "Impression: Normal CT chest." in text
    assert flatten_sr(ds)[0]["value_type"] == "CONTAINER"
    assert parsed["findings"] == "No acute osseous abnormality."
    assert parsed["impression"] == "Normal CT chest."


def test_findings_column_joins_nested_text() -> None:
    ds = make_radiology_sr()
    extra = Dataset()
    extra.RelationshipType = "CONTAINS"
    extra.ValueType = "TEXT"
    extra.ConceptNameCodeSequence = [_code("121071", "Finding")]
    extra.TextValue = "No pleural effusion."
    ds.ContentSequence[0].ContentSequence.append(extra)
    parsed = parse_sr(ds)
    assert parsed["findings"] == "No acute osseous abnormality.\nNo pleural effusion."
    assert parsed["impression"] == "Normal CT chest."
