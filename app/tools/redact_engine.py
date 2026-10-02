"""Pixel-level redaction: black out one rectangle in a DICOM image's pixel data,
and optionally stamp a text label on it.

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
from functools import lru_cache
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset

from app.paths import package_dir


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


# --- text stamp ---------------------------------------------------------------

# Fonts ship with the app (app/static/fonts, DejaVu, free licence), so the
# text looks the same on Docker, the Windows MSI and the Mac app, and the
# browser preview can use the very same files.
FONT_FILES = {
    ("sans", False): "DejaVuSans.ttf",
    ("sans", True): "DejaVuSans-Bold.ttf",
    ("mono", False): "DejaVuSansMono.ttf",
    ("mono", True): "DejaVuSansMono-Bold.ttf",
}
FONT_FAMILIES = ("sans", "mono")
# name -> RGB. Gray images get the luminance of the colour.
TEXT_COLORS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "yellow": (255, 230, 0),
    "red": (255, 40, 40),
    "green": (60, 220, 90),
    "cyan": (0, 220, 255),
}
TEXT_BACKGROUNDS = ("none", "black", "white")
MIN_TEXT_SIZE = 6
MAX_TEXT_SIZE = 400
MAX_TEXT_CHARS = 2000


@dataclass(frozen=True)
class TextStamp:
    """A block of text to draw into the pixels, top-left origin. Newlines make lines."""

    text: str = ""
    x: int = 0
    y: int = 0
    size: int = 24  # font size in pixels
    family: str = "sans"
    bold: bool = False
    color: str = "white"
    background: str = "none"  # a box behind the text, so it stays readable on any image

    @property
    def enabled(self) -> bool:
        return bool(self.text.strip())


def _clean_choice(value: object, allowed: object, default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _flag(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}


def parse_text_stamp(
    text: object, x: object, y: object, size: object, family: object, bold: object, color: object, background: object
) -> TextStamp:
    def _int(value: object, default: int) -> int:
        try:
            return int(float(str(value).strip() or default))
        except (TypeError, ValueError):
            return default

    cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n")[:MAX_TEXT_CHARS]
    return TextStamp(
        text=cleaned,
        x=max(0, _int(x, 0)),
        y=max(0, _int(y, 0)),
        size=max(MIN_TEXT_SIZE, min(MAX_TEXT_SIZE, _int(size, 24))),
        family=_clean_choice(family, FONT_FAMILIES, "sans"),
        bold=_flag(bold),
        color=_clean_choice(color, TEXT_COLORS, "white"),
        background=_clean_choice(background, TEXT_BACKGROUNDS, "none"),
    )


@lru_cache(maxsize=16)
def _font(family: str, bold: bool, size: int):
    from PIL import ImageFont

    path = Path(package_dir()) / "static" / "fonts" / FONT_FILES[(family, bold)]
    try:
        return ImageFont.truetype(str(path), size)
    except OSError as exc:
        raise RedactionError(f"Could not load the text font {path.name}: {exc}") from exc


def render_text_masks(stamp: TextStamp) -> tuple[np.ndarray, np.ndarray]:
    """The stamp as two uint8 coverage masks of the same shape: (box, glyphs).

    ``glyphs`` is anti-aliased 0..255; ``box`` is 255 where the background box
    goes (all of it, padding included) or all 0 when there is no box.
    """
    from PIL import Image, ImageDraw

    font = _font(stamp.family, stamp.bold, stamp.size)
    spacing = max(1, stamp.size // 5)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = probe.multiline_textbbox((0, 0), stamp.text, font=font, spacing=spacing)
    pad = max(2, stamp.size // 4) if stamp.background != "none" else 0
    width = int(right - left) + 2 * pad + 1
    height = int(bottom - top) + 2 * pad + 1
    glyphs = Image.new("L", (width, height), 0)
    ImageDraw.Draw(glyphs).multiline_text((pad - left, pad - top), stamp.text, font=font, fill=255, spacing=spacing)
    box = np.full((height, width), 255 if stamp.background != "none" else 0, dtype=np.uint8)
    return box, np.asarray(glyphs, dtype=np.uint8)


def _value_range(ds: Dataset, dtype: np.dtype) -> tuple[int, int]:
    """Lowest and highest pixel value the image can hold, from BitsStored."""
    stored = int(getattr(ds, "BitsStored", 0) or dtype.itemsize * 8)
    if int(getattr(ds, "PixelRepresentation", 0) or 0) == 1:
        return -(1 << (stored - 1)), (1 << (stored - 1)) - 1
    return 0, (1 << stored) - 1


def _paint_values(ds: Dataset, rgb: tuple[int, int, int], is_color: bool, lo: int, hi: int) -> list[float]:
    """The colour as one pixel value per sample, in the image's own encoding."""
    photometric = str(getattr(ds, "PhotometricInterpretation", "") or "")
    if is_color:
        r, g, b = (c / 255.0 for c in rgb)
        if photometric.startswith("YBR"):  # raw YCbCr (full range): not RGB, so convert
            y = 0.299 * r + 0.587 * g + 0.114 * b
            cb = 0.5 + (-0.168736 * r - 0.331264 * g + 0.5 * b)
            cr = 0.5 + (0.5 * r - 0.418688 * g - 0.081312 * b)
            return [v * (hi - lo) + lo for v in (y, cb, cr)]
        return [v * (hi - lo) + lo for v in (r, g, b)]
    luminance = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0
    if photometric == "MONOCHROME1":  # 0 is white here
        luminance = 1.0 - luminance
    return [luminance * (hi - lo) + lo]


def _stamp_text(ds: Dataset, frames: np.ndarray, stamp: TextStamp, rows: int, cols: int, is_color: bool) -> None:
    """Draw ``stamp`` into every frame. ``frames`` is a (F, rows, cols, C) view of the pixel array."""
    box, glyphs = render_text_masks(stamp)
    x0, y0 = min(stamp.x, cols), min(stamp.y, rows)
    x1, y1 = min(cols, x0 + glyphs.shape[1]), min(rows, y0 + glyphs.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    box = box[: y1 - y0, : x1 - x0].astype(np.float32)[..., None] / 255.0
    glyphs = glyphs[: y1 - y0, : x1 - x0].astype(np.float32)[..., None] / 255.0

    lo, hi = _value_range(ds, frames.dtype)
    ink = np.array(_paint_values(ds, TEXT_COLORS[stamp.color], is_color, lo, hi), dtype=np.float32)
    ground = None
    if stamp.background != "none":
        ground = np.array(
            _paint_values(ds, TEXT_COLORS[stamp.background], is_color, lo, hi), dtype=np.float32
        )

    target = frames[:, y0:y1, x0:x1, :]  # a view: writing to it writes the image
    work = target.astype(np.float32)
    if ground is not None:
        work = work * (1.0 - box) + ground * box
    work = work * (1.0 - glyphs) + ink * glyphs
    target[...] = np.clip(np.rint(work), lo, hi).astype(frames.dtype)


def redact_pixels(ds: Dataset, region: RedactRegion | None, text: TextStamp | None = None) -> Dataset:
    """Black out ``region`` in ds's pixel data, then draw ``text`` on top, in
    place, and return ds. Either may be ``None`` / empty to do only the other.

    Also sets Burned-In Annotation (0028,0301): NO after a plain redaction,
    since that's the point of running this at all — leaving it unset or "YES"
    would tell a downstream consumer the image may still carry burned-in
    identifiers. Once text has been drawn in, the image does carry burned-in
    annotation, so it is set to YES instead (the label is yours; the flag
    says there is text to look at).
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

    if region is not None:
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

    stamped = text is not None and text.enabled
    if stamped:
        # One (frames, rows, cols, samples) view, whatever the source shape.
        if is_multiframe and is_color:
            frames = pixel_array
        elif is_multiframe:
            frames = pixel_array[..., np.newaxis]
        elif is_color:
            frames = pixel_array[np.newaxis]
        else:
            frames = pixel_array[np.newaxis, ..., np.newaxis]
        _stamp_text(ds, frames, text, rows, cols, is_color)

    ds.PixelData = pixel_array.tobytes()
    if "PlanarConfiguration" in ds:
        # pixel_array is always returned/consumed color-by-pixel, regardless
        # of how the source encoded it — tobytes() here is therefore planar 0.
        ds.PlanarConfiguration = 0
    ds.BurnedInAnnotation = "YES" if stamped else "NO"
    return ds
