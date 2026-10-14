"""Tests for the pure decision logic: print format selection and scan settings."""

from __future__ import annotations

import dataclasses

import pytest

from local_printer import const
from local_printer.escl import ScannerCaps, SourceCaps, parse_capabilities
from local_printer.ipp import PrinterCaps
from local_printer.planner import PlanError, plan_print, plan_scan, printer_papers

A4 = const.PAPER_SIZES["a4"]


def printer(**kwargs) -> PrinterCaps:
    defaults = dict(sides=["one-sided"], max_copies=99)
    defaults.update(kwargs)
    return PrinterCaps(**defaults)


def pp(caps: PrinterCaps, **kwargs):
    params = dict(is_pdf=True, is_jpeg=False, paper="a4", sides="one-sided", color=False, copies=1)
    params.update(kwargs)
    return plan_print(caps, **params)


PDF_ONLY = dict(formats=["application/pdf"])
URF_ONLY = dict(
    formats=["image/urf"],
    urf=["V1.4", "CP1", "W8", "SRGB24", "RS300-600", "DM3", "IS1"],
    color_modes=["monochrome", "color"],
)


# ------------------------------------------------------------- plan_print


class TestPlanPrintM141w:
    def test_pdf_goes_to_gray_pwg(self, m141w_printer):
        plan = pp(m141w_printer)
        assert plan.mime == "image/pwg-raster"
        assert plan.convert == "pwg"
        assert plan.color is False
        assert plan.dpi == 300
        assert plan.media == A4["ipp"]
        assert plan.paper_mm == A4["mm"]
        assert plan.sheet_back == "normal"
        assert plan.sides is None  # one-sided is the default, not sent
        assert plan.color_mode == "monochrome"
        assert (plan.copies_ipp, plan.copies_in_document) == (1, 1)

    def test_jpeg_goes_to_pwg_too(self, m141w_printer):
        assert pp(m141w_printer, is_pdf=False, is_jpeg=True).convert == "pwg"

    def test_color_request_on_mono_printer(self, m141w_printer):
        plan = pp(m141w_printer, color=True)
        assert plan.color is False
        assert plan.color_mode == "monochrome"

    def test_duplex_not_supported_falls_back(self, m141w_printer):
        assert pp(m141w_printer, sides="two-sided-long-edge").sides is None

    def test_copies_within_limit_go_through_ipp(self, m141w_printer):
        plan = pp(m141w_printer, copies=5)
        assert (plan.copies_ipp, plan.copies_in_document) == (5, 1)

    def test_copies_over_limit_go_into_document(self, m141w_printer):
        plan = pp(m141w_printer, copies=150)
        assert (plan.copies_ipp, plan.copies_in_document) == (1, 150)

    def test_paper_not_reported_by_printer(self, m141w_printer):
        plan = pp(m141w_printer, paper="legal")
        assert plan.media is None  # let the printer choose its tray
        assert plan.paper_mm == const.PAPER_SIZES["legal"]["mm"]  # but render for Legal

    def test_unknown_paper(self, m141w_printer):
        with pytest.raises(PlanError):
            pp(m141w_printer, paper="tabloid")


class TestPlanPrintFormats:
    def test_native_pdf_no_conversion(self):
        plan = pp(printer(formats=["application/pdf", "image/pwg-raster"]))
        assert (plan.mime, plan.convert, plan.dpi) == ("application/pdf", "none", None)

    def test_native_jpeg_no_conversion(self):
        plan = pp(printer(formats=["image/jpeg", "image/pwg-raster"]), is_pdf=False, is_jpeg=True)
        assert (plan.mime, plan.convert) == ("image/jpeg", "none")

    def test_pdf_input_not_sent_as_jpeg(self):
        plan = pp(printer(formats=["image/jpeg", "image/pwg-raster"]))
        assert plan.convert == "pwg"

    def test_native_pdf_skipped_when_copies_must_be_rendered(self):
        caps = printer(formats=["application/pdf", "image/pwg-raster"], max_copies=1)
        plan = pp(caps, copies=3)
        assert plan.convert == "pwg"
        assert plan.copies_in_document == 3

    def test_urf_only_printer(self):
        plan = pp(printer(**URF_ONLY), color=True)
        assert (plan.mime, plan.convert) == ("image/urf", "urf")
        assert plan.dpi == 300
        assert plan.sheet_back == "rotated"  # DM3
        assert plan.color is True
        assert plan.color_mode == "color"

    @pytest.mark.parametrize(
        "rs,dpi", [("RS300-600", 300), ("RS600-1200", 600), ("RS150", 150), ("RS200-400", 400)]
    )
    def test_urf_resolution_from_rs_keyword(self, rs, dpi):
        plan = pp(printer(formats=["image/urf"], urf=["W8", rs]))
        assert plan.dpi == dpi

    @pytest.mark.parametrize("dm,back", [("DM1", "normal"), ("DM2", "flipped"), ("DM4", "manual-tumble")])
    def test_urf_sheet_back(self, dm, back):
        assert pp(printer(formats=["image/urf"], urf=["W8", "RS300", dm])).sheet_back == back

    def test_urf_rgb_only_forces_color(self):
        plan = pp(printer(formats=["image/urf"], urf=["SRGB24", "RS300"]), color=False)
        assert plan.color is True

    def test_pwg_srgb_only_forces_color(self):
        plan = pp(printer(formats=["image/pwg-raster"], pwg_types=["srgb_8"]), color=False)
        assert plan.convert == "pwg"
        assert plan.color is True

    def test_pwg_color_when_requested(self):
        caps = printer(formats=["image/pwg-raster"], pwg_types=["sgray_8", "srgb_8"], color_modes=["monochrome", "color"])
        plan = pp(caps, color=True)
        assert plan.color is True and plan.color_mode == "color"
        plan = pp(caps, color=False)
        assert plan.color is False and plan.color_mode == "monochrome"

    def test_pwg_unsupported_type(self):
        with pytest.raises(PlanError):
            pp(printer(formats=["image/pwg-raster"], pwg_types=["adobe-rgb_16"]))

    @pytest.mark.parametrize("supported,dpi", [([600, 1200], 600), ([150], 150), ([], 300), ([300, 600], 300)])
    def test_pwg_resolution_choice(self, supported, dpi):
        assert pp(printer(formats=["image/pwg-raster"], pwg_resolutions=supported)).dpi == dpi

    def test_pwg_preferred_over_urf(self):
        caps = printer(formats=["image/urf", "image/pwg-raster"], urf=["W8", "RS600"], pwg_resolutions=[300])
        assert pp(caps).convert == "pwg"

    def test_pwg_sheet_back_passthrough(self):
        caps = printer(formats=["image/pwg-raster"], pwg_sheet_back="flipped", sides=["one-sided", "two-sided-long-edge"])
        plan = pp(caps, sides="two-sided-long-edge")
        assert plan.sheet_back == "flipped"
        assert plan.sides == "two-sided-long-edge"

    def test_jpeg_on_pdf_only_printer_wrapped_in_pdf(self):
        plan = pp(printer(**PDF_ONLY), is_pdf=False, is_jpeg=True)
        assert (plan.mime, plan.convert, plan.dpi) == ("application/pdf", "image_to_pdf", const.PREFERRED_PRINT_DPI)

    def test_png_on_pdf_only_printer_wrapped_in_pdf(self):
        plan = pp(printer(**PDF_ONLY), is_pdf=False, is_jpeg=False)
        assert plan.convert == "image_to_pdf"

    def test_pdf_input_with_copies_on_pdf_only_printer(self):
        caps = printer(**PDF_ONLY, max_copies=1)  # no copies-supported reported
        try:
            plan = pp(caps, is_pdf=True, copies=2)
        except PlanError:
            return  # refusing is acceptable
        assert plan.convert != "image_to_pdf"

    @pytest.mark.parametrize(
        "formats", [["application/PCLm", "application/octet-stream"], ["image/jpeg"], []]
    )
    def test_no_usable_format(self, formats):
        with pytest.raises(PlanError):
            pp(printer(formats=formats))

    def test_color_mode_none_when_printer_reports_nothing(self):
        assert pp(printer(formats=["image/pwg-raster"])).color_mode is None


class TestPrinterPapers:
    def test_m141w_default_first(self, m141w_printer):
        assert printer_papers(m141w_printer) == ["letter", "a4"]

    def test_no_media_list_means_all(self):
        papers = printer_papers(printer(media_default="iso_a5_148x210mm"))
        assert papers[0] == "a5"
        assert sorted(papers) == sorted(const.PAPER_SIZES)

    def test_unknown_media_only(self):
        assert printer_papers(printer(media=["jis_b5_182x257mm"])) == ["a4"]

    def test_keys_are_paper_size_keys(self, m141w_printer):
        assert set(printer_papers(m141w_printer)) <= set(const.PAPER_SIZES)


# -------------------------------------------------------------- plan_scan


@pytest.fixture
def m141w_scanner(m141w_scanner_xml) -> ScannerCaps:
    return parse_capabilities(m141w_scanner_xml)


@pytest.fixture
def adf_scanner(adf_scanner_xml) -> ScannerCaps:
    return parse_capabilities(adf_scanner_xml)


def ps(caps: ScannerCaps, **kwargs):
    params = dict(source="platen", color="RGB24", dpi=300, file_format="pdf", area="a4")
    params.update(kwargs)
    return plan_scan(caps, **params)


class TestPlanScan:
    def test_m141w_defaults(self, m141w_scanner):
        plan = ps(m141w_scanner)
        assert plan.source == "platen"
        assert plan.color == "RGB24"
        assert plan.dpi == 300
        assert plan.mime == "application/pdf"
        assert plan.convert_to_pdf is False
        assert (plan.width, plan.height) == A4["escl"]
        assert plan.extension == "pdf"

    @pytest.mark.parametrize("source", ["adf", "adf_duplex", "bogus"])
    def test_unsupported_source_falls_back_to_platen(self, m141w_scanner, source):
        assert ps(m141w_scanner, source=source).source == "platen"

    def test_adf_source_kept_when_supported(self, adf_scanner):
        assert ps(adf_scanner, source="adf_duplex").source == "adf_duplex"

    def test_no_platen_falls_back_to_first_source(self):
        caps = ScannerCaps(sources={"adf": SourceCaps(resolutions=[300])})
        assert ps(caps, source="platen").source == "adf"

    def test_no_sources(self):
        with pytest.raises(PlanError):
            ps(ScannerCaps())

    @pytest.mark.parametrize(
        "requested,expected",
        [(100, 200), (200, 200), (250, 300), (299, 300), (450, 600), (449, 300), (1200, 600)],
    )
    def test_dpi_snaps_to_nearest_supported(self, m141w_scanner, requested, expected):
        # ties (250, 450) go to the higher resolution
        assert ps(m141w_scanner, dpi=requested).dpi == expected

    def test_color_fallback(self, adf_scanner):
        assert ps(adf_scanner, source="adf", color=const.COLOR_MODES["color"]).color == "Grayscale8"
        assert ps(adf_scanner, source="adf_duplex", color="RGB24").color == "BlackAndWhite1"
        assert ps(adf_scanner, source="platen", color="BlackAndWhite1").color == "BlackAndWhite1"

    def test_pdf_on_jpeg_only_scanner(self, adf_scanner):
        plan = ps(adf_scanner, file_format="pdf")
        assert plan.mime == "image/jpeg"
        assert plan.convert_to_pdf is True
        assert plan.extension == "pdf"

    def test_png_on_jpeg_only_scanner(self, adf_scanner):
        plan = ps(adf_scanner, file_format="png")
        assert plan.mime == "image/jpeg"
        assert plan.convert_to_pdf is False
        assert plan.extension == "jpg"

    def test_native_jpeg(self, m141w_scanner):
        plan = ps(m141w_scanner, file_format="jpg")
        assert (plan.mime, plan.extension, plan.convert_to_pdf) == ("image/jpeg", "jpg", False)

    def test_unsupported_format_without_jpeg(self):
        caps = ScannerCaps(sources={"platen": SourceCaps(formats=["application/pdf"], resolutions=[300])})
        with pytest.raises(PlanError):
            ps(caps, file_format="png")

    def test_unknown_format(self, m141w_scanner):
        with pytest.raises(PlanError):
            ps(m141w_scanner, file_format="bmp")

    def test_area_max_uses_scanner_max(self, m141w_scanner):
        plan = ps(m141w_scanner, area=const.SCAN_AREA_MAX)
        assert (plan.width, plan.height) == (2550, 3508)

    def test_unknown_area_uses_scanner_max(self, m141w_scanner):
        plan = ps(m141w_scanner, area="whatever")
        assert (plan.width, plan.height) == (2550, 3508)

    def test_region_clamped_to_max(self, m141w_scanner):
        plan = ps(m141w_scanner, area="legal")  # 2550 x 4200 on a 3508-high platen
        assert (plan.width, plan.height) == (2550, 3508)

    def test_region_per_source_max(self, adf_scanner):
        plan = ps(adf_scanner, source="adf_duplex", area="letter")  # 2550 x 3300, duplex max width 2480
        assert (plan.width, plan.height) == (2480, 3300)
        plan = ps(adf_scanner, source="adf", area="legal")
        assert (plan.width, plan.height) == (2550, 4200)


class TestRegionalPaper:
    """A printer with a factory default of Letter should still offer A4 first in A4 regions."""

    def test_preferred_overrides_printer_default(self, m141w_printer):
        assert m141w_printer.media_default == "na_letter_8.5x11in"
        assert printer_papers(m141w_printer, "a4")[0] == "a4"

    def test_printer_default_used_without_preference(self, m141w_printer):
        assert printer_papers(m141w_printer)[0] == "letter"

    def test_unsupported_preference_falls_back_to_printer_default(self, m141w_printer):
        caps = dataclasses.replace(m141w_printer, media=["na_letter_8.5x11in"])
        assert printer_papers(caps, "a4") == ["letter"]
