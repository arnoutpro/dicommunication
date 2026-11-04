from __future__ import annotations

import numpy as np

from app.tools.stroke_font import FONT, draw_text


def test_draw_text_renders_within_bounds_and_clears_elsewhere() -> None:
    array = np.zeros((100, 300), dtype=np.uint8)
    draw_text(array, "HI", 10, 10, 290, 90, 255, is_color=False)
    assert (array[10:90, 10:290] == 255).any()
    assert not (array[0:10, :] == 255).any()
    assert not (array[90:, :] == 255).any()
    assert not (array[:, 0:10] == 255).any()
    assert not (array[:, 290:] == 255).any()


def test_draw_text_empty_string_is_a_no_op() -> None:
    array = np.zeros((50, 50), dtype=np.uint8)
    draw_text(array, "", 0, 0, 50, 50, 255, is_color=False)
    assert not array.any()
    draw_text(array, "   ", 0, 0, 50, 50, 255, is_color=False)
    assert not array.any()


def test_draw_text_zero_size_box_does_not_crash() -> None:
    array = np.zeros((50, 50), dtype=np.uint8)
    draw_text(array, "X", 10, 10, 10, 10, 255, is_color=False)
    draw_text(array, "X", 20, 10, 10, 10, 255, is_color=False)
    assert not array.any()


def test_draw_text_box_too_small_to_fit_a_character_is_a_no_op() -> None:
    array = np.zeros((50, 50), dtype=np.uint8)
    draw_text(array, "HELLO", 0, 0, 4, 4, 255, is_color=False)
    assert not array.any()


def test_draw_text_unmapped_characters_are_skipped_not_fatal() -> None:
    array = np.zeros((100, 300), dtype=np.uint8)
    draw_text(array, "HI é THERE", 0, 0, 300, 100, 255, is_color=False)
    assert (array == 255).any()


def test_font_renders_dicom_patient_name_separator() -> None:
    # PN-format patient names use "^" between components (e.g. "DOE^JOHN"),
    # and labels referencing a patient also commonly need "_" (filenames,
    # identifiers) — both should render, not silently vanish like "@" does.
    assert "^" in FONT
    assert "_" in FONT
    array = np.zeros((100, 300), dtype=np.uint8)
    draw_text(array, "DOE^JOHN", 0, 0, 300, 100, 255, is_color=False)
    assert (array == 255).any()


def test_draw_text_color_image_fills_the_given_rgb_triplet() -> None:
    array = np.zeros((100, 300, 3), dtype=np.uint8)
    draw_text(array, "OK", 0, 0, 300, 100, (10, 20, 30), is_color=True)
    touched = array[(array != 0).any(axis=-1)]
    assert touched.size > 0
    assert (touched == [10, 20, 30]).all()


def test_font_only_defines_uppercase_glyphs() -> None:
    assert "A" in FONT and "a" not in FONT
    assert all(ch == ch.upper() for ch in FONT if ch.isalpha())
