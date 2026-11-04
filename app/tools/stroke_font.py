"""A tiny hand-drawn stroke font for burning a text label into pixel data.

Not Pillow: this project deliberately avoids it (see app/tools/dicom_preview.py)
to keep the desktop build's dependency/packaging footprint small. Each glyph
is a handful of polylines on a 4 (wide) x 6 (tall) logical grid rather than a
bitmap, so it scales cleanly to any rectangle size with plain line drawing —
good enough for a short label like "REDACTED", not for general typography.
Monospace, uppercase only (input text is upper-cased); an unmapped character
is simply skipped rather than raising, since a dropped character in a label
is cosmetic, not a correctness problem.
"""

from __future__ import annotations

import numpy as np

Point = tuple[float, float]
Stroke = list[Point]

FONT: dict[str, list[Stroke]] = {
    " ": [],
    "A": [[(2, 0), (0, 6)], [(2, 0), (4, 6)], [(0.8, 3.8), (3.2, 3.8)]],
    "B": [[(0, 0), (0, 6)], [(0, 0), (3, 0), (3.7, 1.5), (3, 3), (0, 3)], [(0, 3), (3.7, 3), (4, 4.5), (3.7, 6), (0, 6)]],
    "C": [[(4, 1.2), (2, 0), (0, 1.5), (0, 4.5), (2, 6), (4, 4.8)]],
    "D": [[(0, 0), (0, 6)], [(0, 0), (2.5, 0), (4, 2), (4, 4), (2.5, 6), (0, 6)]],
    "E": [[(4, 0), (0, 0), (0, 6), (4, 6)], [(0, 3), (3, 3)]],
    "F": [[(0, 6), (0, 0), (4, 0)], [(0, 3), (3, 3)]],
    "G": [[(4, 1.2), (2, 0), (0, 1.5), (0, 4.5), (2, 6), (4, 4.8), (4, 3.5)], [(2.3, 3.5), (4, 3.5)]],
    "H": [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]],
    "I": [[(1, 0), (3, 0)], [(2, 0), (2, 6)], [(1, 6), (3, 6)]],
    "J": [[(2, 0), (4, 0)], [(3, 0), (3, 4.8)], [(3, 4.8), (2, 6), (0.5, 5.2)]],
    "K": [[(0, 0), (0, 6)], [(4, 0), (0, 3), (4, 6)]],
    "L": [[(0, 0), (0, 6), (4, 6)]],
    "M": [[(0, 6), (0, 0), (2, 3.5), (4, 0), (4, 6)]],
    "N": [[(0, 6), (0, 0), (4, 6), (4, 0)]],
    "O": [[(2, 0), (4, 1.2), (4, 4.8), (2, 6), (0, 4.8), (0, 1.2), (2, 0)]],
    "P": [[(0, 6), (0, 0), (3.2, 0), (4, 1.5), (3.2, 3), (0, 3)]],
    "Q": [[(2, 0), (4, 1.2), (4, 4.8), (2, 6), (0, 4.8), (0, 1.2), (2, 0)], [(2.2, 4), (4.3, 6.3)]],
    "R": [[(0, 6), (0, 0), (3.2, 0), (4, 1.5), (3.2, 3), (0, 3)], [(1.5, 3), (4, 6)]],
    "S": [[(4, 1.2), (2, 0), (0.3, 1), (0.3, 2.3), (4, 3.7), (4, 5), (2, 6), (0, 4.8)]],
    "T": [[(0, 0), (4, 0)], [(2, 0), (2, 6)]],
    "U": [[(0, 0), (0, 4.5), (1, 6), (3, 6), (4, 4.5), (4, 0)]],
    "V": [[(0, 0), (2, 6), (4, 0)]],
    "W": [[(0, 0), (1, 6), (2, 2.5), (3, 6), (4, 0)]],
    "X": [[(0, 0), (4, 6)], [(4, 0), (0, 6)]],
    "Y": [[(0, 0), (2, 3.2)], [(4, 0), (2, 3.2)], [(2, 3.2), (2, 6)]],
    "Z": [[(0, 0), (4, 0), (0, 6), (4, 6)]],
    "0": [[(2, 0), (4, 1.2), (4, 4.8), (2, 6), (0, 4.8), (0, 1.2), (2, 0)], [(0.5, 5.3), (3.5, 0.7)]],
    "1": [[(1, 1.2), (2, 0), (2, 6)], [(1, 6), (3, 6)]],
    "2": [[(0.3, 1.5), (1.3, 0), (3, 0), (4, 1.3), (3.7, 2.7), (0, 6), (4, 6)]],
    "3": [[(0.3, 1), (2, 0), (3.7, 1), (3.7, 2.7), (2, 3.2), (3.7, 3.7), (3.7, 5), (2, 6), (0.3, 5)]],
    "4": [[(3, 6), (3, 0), (0, 4), (4, 4)]],
    "5": [[(4, 0), (0, 0), (0, 2.8), (2.5, 2.8), (4, 4), (4, 5), (2, 6), (0.3, 5)]],
    "6": [[(3.5, 0.5), (1.5, 0), (0.2, 1.8), (0.2, 4.5), (1.5, 6), (3.3, 6), (4, 4.5), (3.3, 3.2), (0.6, 3.2)]],
    "7": [[(0, 0), (4, 0), (1.5, 6)]],
    "8": [[(2, 0), (3.5, 0.8), (3.5, 2.5), (2, 3.2), (0.5, 2.5), (0.5, 0.8), (2, 0)], [(2, 3.2), (3.7, 4), (3.7, 5.3), (2, 6), (0.3, 5.3), (0.3, 4), (2, 3.2)]],
    "9": [[(0.5, 5.5), (2.5, 6), (3.8, 4.2), (3.8, 1.5), (2.5, 0), (0.7, 0), (0, 1.3), (0.7, 2.8), (3.4, 2.8)]],
    ".": [[(2, 5.7), (2, 6)]],
    ",": [[(2, 5.5), (2, 6), (1.3, 6.8)]],
    "-": [[(0.5, 3), (3.5, 3)]],
    ":": [[(2, 1.5), (2, 1.8)], [(2, 4.2), (2, 4.5)]],
    "'": [[(2, 0), (2, 1.2)]],
    "(": [[(3, 0), (1, 2), (1, 4), (3, 6)]],
    ")": [[(1, 0), (3, 2), (3, 4), (1, 6)]],
    "/": [[(0, 6), (4, 0)]],
}

LOGICAL_WIDTH = 4.0
LOGICAL_HEIGHT = 6.0
LOGICAL_GAP = 1.0
LOGICAL_PITCH = LOGICAL_WIDTH + LOGICAL_GAP


def _draw_polyline(array: np.ndarray, points: list[Point], thickness: float, color, is_color: bool) -> None:
    half = max(0.5, thickness / 2)
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = max(abs(x1 - x0), abs(y1 - y0))
        steps = max(1, int(length / max(0.4, half)))
        for i in range(steps + 1):
            t = i / steps
            cx = x0 + (x1 - x0) * t
            cy = y0 + (y1 - y0) * t
            ix0 = max(0, int(round(cx - half)))
            iy0 = max(0, int(round(cy - half)))
            ix1 = min(array.shape[1], int(round(cx + half)) + 1)
            iy1 = min(array.shape[0], int(round(cy + half)) + 1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            if is_color:
                array[iy0:iy1, ix0:ix1, :] = color
            else:
                array[iy0:iy1, ix0:ix1] = color


def draw_text(
    array: np.ndarray,
    text: str,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color,
    is_color: bool = False,
) -> None:
    """Burn ``text`` into ``array`` (a single 2-D, or 2-D+channel, frame),
    monospace and centered within the x0,y0,x1,y1 box. Scaled to fit; if the
    box is too small for even one readable character at scale 1px per
    logical unit, nothing is drawn rather than an illegible smear.
    """
    text = text.strip().upper()
    if not text:
        return
    width_px = x1 - x0
    height_px = y1 - y0
    if width_px <= 0 or height_px <= 0:
        return
    total_logical_width = len(text) * LOGICAL_PITCH - LOGICAL_GAP
    scale_h = (height_px * 0.8) / LOGICAL_HEIGHT
    scale_w = (width_px * 0.9) / max(0.001, total_logical_width)
    scale = min(scale_h, scale_w)
    if scale < 1.0:
        return
    thickness = max(1.0, scale * 0.85)
    text_w = total_logical_width * scale
    text_h = LOGICAL_HEIGHT * scale
    ox = x0 + (width_px - text_w) / 2
    oy = y0 + (height_px - text_h) / 2
    for i, ch in enumerate(text):
        glyph = FONT.get(ch)
        if not glyph:
            continue
        cell_x = ox + i * LOGICAL_PITCH * scale
        for stroke in glyph:
            points = [(cell_x + gx * scale, oy + gy * scale) for gx, gy in stroke]
            _draw_polyline(array, points, thickness, color, is_color)
