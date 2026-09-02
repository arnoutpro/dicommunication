"""SOP Classes the local Storage SCP accepts, beyond Structured Reports.

Kept separate from app.sr (which is specifically about identifying and parsing
Structured Reports) so that widening what the listener accepts never changes
what is_structured_report() considers an SR.
"""

from __future__ import annotations

from typing import Any

from pynetdicom.sop_class import (
    ComputedRadiographyImageStorage,
    CTImageStorage,
    DigitalMammographyXRayImageStorageForPresentation,
    DigitalMammographyXRayImageStorageForProcessing,
    DigitalXRayImageStorageForPresentation,
    DigitalXRayImageStorageForProcessing,
    EnhancedCTImageStorage,
    EnhancedMRImageStorage,
    MRImageStorage,
    NuclearMedicineImageStorage,
    PositronEmissionTomographyImageStorage,
    SecondaryCaptureImageStorage,
    UltrasoundImageStorage,
    UltrasoundMultiFrameImageStorage,
    XRayAngiographicImageStorage,
    XRayRadiofluoroscopicImageStorage,
)

from app.sr import SR_STORAGE_SOP_CLASSES

GENERAL_IMAGE_STORAGE_SOP_CLASSES: tuple[Any, ...] = (
    CTImageStorage,
    EnhancedCTImageStorage,
    MRImageStorage,
    EnhancedMRImageStorage,
    DigitalXRayImageStorageForPresentation,
    DigitalXRayImageStorageForProcessing,
    DigitalMammographyXRayImageStorageForPresentation,
    DigitalMammographyXRayImageStorageForProcessing,
    ComputedRadiographyImageStorage,
    UltrasoundImageStorage,
    UltrasoundMultiFrameImageStorage,
    SecondaryCaptureImageStorage,
    NuclearMedicineImageStorage,
    PositronEmissionTomographyImageStorage,
    XRayAngiographicImageStorage,
    XRayRadiofluoroscopicImageStorage,
)

# Everything the local Storage SCP negotiates when Accept C-STORE is on: SR
# (report retrieve) plus the general image classes above (tag editor retrieve).
ALL_STORAGE_SOP_CLASSES: tuple[Any, ...] = SR_STORAGE_SOP_CLASSES + GENERAL_IMAGE_STORAGE_SOP_CLASSES
