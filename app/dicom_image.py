"""Build a tiny solid-black placeholder image as a DICOM Secondary Capture
instance, carrying a Modality Worklist order's Patient/Study/Accession
identifiers — for simulating a modality completing a scheduled exam.

Secondary Capture Image Storage is used regardless of the chosen Modality
value because it is the generic "any pixel data" SOP Class; the Modality
(0008,0060) tag is set to whatever the operator picked, which is how real
modalities' derived/secondary objects are commonly tagged too.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import PYDICOM_IMPLEMENTATION_UID, ExplicitVRLittleEndian, generate_uid
from pynetdicom.sop_class import SecondaryCaptureImageStorage

IMAGE_SIZE = 256


def _dicom_lo(value: str, length: int = 64) -> str:
    return (value or "").strip()[:length]


def build_black_image_dataset(
    *,
    patient_name: str,
    patient_id: str,
    modality: str,
    accession_number: str = "",
    study_instance_uid: str = "",
    study_description: str = "",
    body_part_examined: str = "",
    series_description: str = "",
    instance_number: int = 1,
) -> Dataset:
    now = datetime.now(timezone.utc)
    sop_uid = generate_uid()
    pixels = bytes(IMAGE_SIZE * IMAGE_SIZE)  # all-zero bytes: a solid black frame.

    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.FileMetaInformationVersion = b"\x00\x01"
    ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
    ds.file_meta.ImplementationVersionName = "DICOMM"

    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_instance_uid or generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientName = _dicom_lo(patient_name)
    ds.PatientID = _dicom_lo(patient_id, 64)
    ds.PatientBirthDate = ""
    ds.PatientSex = ""
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.SeriesDate = ds.StudyDate
    ds.SeriesTime = ds.StudyTime
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.StudyID = "1"
    ds.AccessionNumber = _dicom_lo(accession_number, 16)
    ds.ReferringPhysicianName = ""
    ds.StudyDescription = _dicom_lo(study_description)
    ds.Modality = _dicom_lo(modality, 16) or "OT"
    ds.SeriesNumber = 1
    ds.SeriesDescription = _dicom_lo(series_description)
    ds.BodyPartExamined = _dicom_lo(body_part_examined, 16).upper()
    ds.InstanceNumber = int(instance_number)
    ds.Manufacturer = "Arnout.pro"
    ds.ManufacturerModelName = "Dicommunication"
    ds.InstitutionName = "Dicommunication Tool"
    # "SYN" (Synthetic Image) is a PS3.3 C.7.6.1 Defined Term for Conversion
    # Type — the honest answer for a generated placeholder, not a scan.
    ds.ConversionType = "SYN"
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = IMAGE_SIZE
    ds.Columns = IMAGE_SIZE
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = pixels
    ds.BurnedInAnnotation = "NO"

    text = f"{ds.PatientName}{ds.PatientID}{ds.StudyDescription}{ds.SeriesDescription}"
    if not text.isascii():
        ds.SpecificCharacterSet = "ISO_IR 192"
    return ds
