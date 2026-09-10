"""Render one DICOM instance's pixel data as a small preview PNG.

Used by Dicom Cleaner's region picker so an operator can drag a rectangle
on the actual image instead of typing pixel coordinates blind. This is a
region-picking aid, not a diagnostic viewer: normalization is a plain
min-max stretch (with pydicom's VOI LUT applied first when present for a
more readable preview), and large images are downscaled by simple
striding. Encoding is a minimal stdlib PNG writer (zlib + struct) so this
doesn't need Pillow just to show one frame.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np
from pydicom.dataset import Dataset

MAX_PREVIEW_DIM = 768


class PreviewError(Exception):
    """A dataset could not be rendered as a preview image."""


def _normalize_to_uint8(array: np.ndarray) -> np.ndarray:
    array = array.astype(np.float32)
    lo = float(array.min())
    hi = float(array.max())
    if hi <= lo:
        return np.zeros(array.shape, dtype=np.uint8)
    scaled = (array - lo) / (hi - lo) * 255.0
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _grayscale_frame(ds: Dataset, frame: np.ndarray) -> np.ndarray:
    try:
        from pydicom.pixels import apply_voi_lut

        frame = apply_voi_lut(frame, ds)
    except Exception:  # noqa: BLE001 — preview only, fall back to a raw stretch
        pass
    return _normalize_to_uint8(frame)


def _downscale(array: np.ndarray, max_dim: int) -> np.ndarray:
    longest = max(array.shape[0], array.shape[1])
    if longest <= max_dim:
        return array
    step = -(-longest // max_dim)  # ceil division
    return array[::step, ::step]


def _encode_png(array: np.ndarray) -> bytes:
    if array.ndim == 2:
        color_type, channels = 0, 1
    elif array.ndim == 3 and array.shape[2] == 3:
        color_type, channels = 2, 3
    else:
        raise PreviewError(f"Unsupported preview array shape {array.shape}")

    height, width = array.shape[:2]
    row_bytes = width * channels
    raw = bytearray((row_bytes + 1) * height)
    stride = row_bytes + 1
    for y in range(height):
        offset = y * stride
        raw[offset] = 0  # filter type: none
        raw[offset + 1 : offset + 1 + row_bytes] = array[y].tobytes()
    compressed = zlib.compress(bytes(raw), level=6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def render_preview_png(ds: Dataset) -> tuple[bytes, int, int, int, int]:
    """Return (png_bytes, orig_rows, orig_cols, png_rows, png_cols).

    orig_rows/orig_cols are the dataset's own Rows/Columns — the space the
    redact region must be expressed in. png_rows/png_cols are the (possibly
    downscaled) preview image's own pixel dimensions, needed by the browser
    to translate a rectangle it drew back into the original pixel space.
    """
    if "PixelData" not in ds:
        raise PreviewError("No Pixel Data element — not an image instance.")

    file_meta = getattr(ds, "file_meta", None)
    transfer_syntax = getattr(file_meta, "TransferSyntaxUID", None)
    if transfer_syntax is not None and transfer_syntax.is_compressed:
        try:
            ds.decompress()
        except Exception as exc:  # noqa: BLE001
            raise PreviewError(
                f"Could not decode compressed pixel data ({exc}). "
                "Install pylibjpeg or gdcm so pydicom can decode this Transfer Syntax."
            ) from exc

    try:
        pixel_array = ds.pixel_array
    except Exception as exc:  # noqa: BLE001
        raise PreviewError(f"Could not read pixel data: {exc}") from exc

    frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
    frame = pixel_array[0] if frames > 1 else pixel_array
    orig_rows = int(getattr(ds, "Rows", frame.shape[0]) or frame.shape[0])
    orig_cols = int(getattr(ds, "Columns", frame.shape[1]) or frame.shape[1])

    samples = int(getattr(ds, "SamplesPerPixel", 1) or 1)
    if samples > 1:
        display = _normalize_to_uint8(frame[..., :3] if frame.shape[-1] >= 3 else frame)
    else:
        display = _grayscale_frame(ds, frame)

    display = _downscale(display, MAX_PREVIEW_DIM)
    png_rows, png_cols = display.shape[0], display.shape[1]
    return _encode_png(display), orig_rows, orig_cols, png_rows, png_cols
