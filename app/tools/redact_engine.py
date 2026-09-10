"""Pixel-level redaction: black out one rectangle in a DICOM image's pixel data.

Works from the DICOM tags that actually describe the pixel geometry
(NumberOfFrames, SamplesPerPixel) rather than guessing from array shape —
a single-frame color image and a multi-frame grayscale image can both come
back from pydicom as a 3-D array, so shape alone can't tell them apart.

Compressed Transfer Syntaxes (JPEG Baseline/Lossless, RLE, ...) are
decompressed first via ``Dataset.decompress()``. That needs pylibjpeg or
gdcm installed, same as pydicom's own ``pixel_array`` always has for those
syntaxes — this module doesn't do anything pydicom couldn't already do, it
just surfaces the failure as a readable RedactionError instead of letting
whatever pydicom/pillow exception bubble up.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydicom.dataset import Dataset


class RedactionError(Exception):
    """A dataset could not be redacted — no pixel data, or an undecodable one."""


@dataclass(frozen=True)
class RedactRegion:
    """A rectangle in pixel space, top-left origin. width/height <= 0 means "to the edge"."""

    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 100

    def bounds(self, cols: int, rows: int) -> tuple[int, int, int, int]:
        x0 = max(0, min(self.x, cols))
        y0 = max(0, min(self.y, rows))
        x1 = cols if self.width <= 0 else min(cols, x0 + self.width)
        y1 = rows if self.height <= 0 else min(rows, y0 + self.height)
        return x0, y0, x1, y1


def parse_region(x: object, y: object, width: object, height: object) -> RedactRegion:
    def _int(value: object, default: int = 0) -> int:
        try:
            return int(str(value).strip() or default)
        except (TypeError, ValueError):
            return default

    return RedactRegion(
        x=max(0, _int(x, 0)),
        y=max(0, _int(y, 0)),
        width=_int(width, 0),
        height=max(1, _int(height, 100)),
    )


def redact_pixels(ds: Dataset, region: RedactRegion) -> Dataset:
    """Black out ``region`` in ds's pixel data, in place, and return ds.

    Also sets Burned-In Annotation (0028,0301) to NO, since that's the
    point of running this at all — leaving it unset or "YES" would tell a
    downstream consumer the image may still carry burned-in identifiers.
    """
    if "PixelData" not in ds:
        raise RedactionError("No Pixel Data element — not an image instance.")

    file_meta = getattr(ds, "file_meta", None)
    transfer_syntax = getattr(file_meta, "TransferSyntaxUID", None)
    if transfer_syntax is not None and transfer_syntax.is_compressed:
        try:
            ds.decompress()
        except Exception as exc:  # noqa: BLE001
            raise RedactionError(
                f"Could not decode compressed pixel data ({exc}). "
                "Install pylibjpeg or gdcm so pydicom can decode this Transfer Syntax."
            ) from exc

    try:
        pixel_array = ds.pixel_array
    except Exception as exc:  # noqa: BLE001
        raise RedactionError(f"Could not read pixel data: {exc}") from exc

    rows = int(getattr(ds, "Rows", 0) or 0)
    cols = int(getattr(ds, "Columns", 0) or 0)
    frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
    samples = int(getattr(ds, "SamplesPerPixel", 1) or 1)
    is_multiframe = frames > 1
    is_color = samples > 1
    expected_ndim = 2 + (1 if is_multiframe else 0) + (1 if is_color else 0)
    if pixel_array.ndim != expected_ndim:
        raise RedactionError(
            f"Unexpected pixel array shape {pixel_array.shape} for "
            f"{frames} frame(s) x {samples} sample(s)/pixel."
        )

    x0, y0, x1, y1 = region.bounds(cols, rows)
    if x1 > x0 and y1 > y0:
        if is_multiframe and is_color:
            pixel_array[:, y0:y1, x0:x1, :] = 0
        elif is_multiframe:
            pixel_array[:, y0:y1, x0:x1] = 0
        elif is_color:
            pixel_array[y0:y1, x0:x1, :] = 0
        else:
            pixel_array[y0:y1, x0:x1] = 0

    ds.PixelData = pixel_array.tobytes()
    if "PlanarConfiguration" in ds:
        # pixel_array is always returned/consumed color-by-pixel, regardless
        # of how the source encoded it — tobytes() here is therefore planar 0.
        ds.PlanarConfiguration = 0
    ds.BurnedInAnnotation = "NO"
    return ds
