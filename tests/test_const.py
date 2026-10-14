"""Sanity checks for constants that other modules rely on."""

from __future__ import annotations

import pytest

from local_printer import const


@pytest.mark.parametrize("name", list(const.PAPER_SIZES))
def test_paper_sizes_are_consistent(name):
    paper = const.PAPER_SIZES[name]
    width_mm, height_mm = paper["mm"]
    # eSCL sizes are in 1/300 inch and must match the millimeter size
    assert paper["escl"] == pytest.approx((width_mm / 25.4 * 300, height_mm / 25.4 * 300), abs=1)
    assert paper["ipp"].endswith(("mm", "in"))


def test_scan_area_max_is_not_a_paper_key():
    assert const.SCAN_AREA_MAX not in const.PAPER_SIZES


def test_escl_color_modes():
    assert set(const.COLOR_MODES.values()) == {"RGB24", "Grayscale8", "BlackAndWhite1"}


def test_formats_are_mime_types():
    assert const.FORMATS["pdf"] == "application/pdf"
    assert const.FORMATS["jpg"] == "image/jpeg"
    assert all("/" in mime for mime in const.FORMATS.values())
