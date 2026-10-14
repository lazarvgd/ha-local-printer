"""Fixtures for running the integration inside a real Home Assistant test instance."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from custom_components.local_printer.escl import parse_capabilities  # noqa: E402
from custom_components.local_printer.ipp import PrinterCaps  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures"
PDF_PAGE = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def scanner_caps():
    return parse_capabilities((FIXTURES / "m141w_caps.xml").read_text())


@pytest.fixture
def printer_caps():
    return PrinterCaps(
        make_and_model="HP LaserJet MFP M139-M142",
        uuid="0a1b2c3d-0000-4000-8000-000000000141",
        firmware="20260530",
        formats=["application/PCLm", "application/octet-stream", "image/pwg-raster", "image/urf"],
        pwg_types=["sgray_8"],
        pwg_resolutions=[300, 600],
        urf=["V1.5", "CP99", "W8", "RS300-600"],
        media=["iso_a4_210x297mm", "na_letter_8.5x11in"],
        media_default="na_letter_8.5x11in",
        sides=["one-sided"],
        color_modes=["auto", "monochrome"],
        max_copies=99,
        can_identify=True,
    )


@pytest.fixture
def device(scanner_caps, printer_caps):
    """Patch the network clients with a fake HP M141w."""
    status = {
        "printer-state": 3,
        "printer-state-reasons": "none",
        "marker-names": "Black Cartridge HP W1500A",
        "marker-levels": 59,
        "marker-types": "toner",
    }
    with (
        patch("custom_components.local_printer.escl.EsclClient.capabilities", AsyncMock(return_value=scanner_caps)),
        patch("custom_components.local_printer.escl.EsclClient.status", AsyncMock(return_value=("Idle", None))),
        patch("custom_components.local_printer.escl.EsclClient.scan", AsyncMock(return_value=[PDF_PAGE])) as scan,
        patch("custom_components.local_printer.ipp.IppClient.capabilities", AsyncMock(return_value=printer_caps)),
        patch("custom_components.local_printer.ipp.IppClient.status", AsyncMock(return_value=status)),
        patch("custom_components.local_printer.ipp.IppClient.jobs", AsyncMock(return_value=[])),
        patch("custom_components.local_printer.ipp.IppClient.print_document", AsyncMock(return_value=42)) as print_doc,
    ):
        yield {"scan": scan, "print": print_doc}
