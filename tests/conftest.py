"""Shared fixtures for the pure (Home Assistant independent) modules.

The package ``__init__.py`` imports ``homeassistant``, which is not installed in the
test environment. We register a bare stub package so submodules (``escl``, ``ipp``,
``raster``, ``planner``, ``const``) can be imported without executing ``__init__.py``.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "custom_components" / "local_printer"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if "local_printer" not in sys.modules:
    _pkg = types.ModuleType("local_printer")
    _pkg.__path__ = [str(PACKAGE_DIR)]
    _pkg.__package__ = "local_printer"
    sys.modules["local_printer"] = _pkg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from local_printer.ipp import PrinterCaps  # noqa: E402


def _patch_aioresponses_for_new_aiohttp() -> None:
    """aioresponses 0.7.9 builds ClientResponse without ``stream_writer``.

    aiohttp >= 3.13 made that keyword-only argument mandatory. Inject a dummy
    writer so mocked responses can be constructed. Harmless on older aiohttp.
    """
    import inspect
    import types as _types

    import aiohttp
    import aioresponses.core as core

    if "stream_writer" not in inspect.signature(aiohttp.ClientResponse.__init__).parameters:
        return

    class _CompatClientResponse(aiohttp.ClientResponse):
        def __init__(self, *args, stream_writer=None, **kwargs):
            if stream_writer is None:
                stream_writer = _types.SimpleNamespace(output_size=0)
            super().__init__(*args, stream_writer=stream_writer, **kwargs)

    core.ClientResponse = _CompatClientResponse


_patch_aioresponses_for_new_aiohttp()

# Real IPP attributes reported by an HP LaserJet MFP M141w, in the shape
# produced by ipp.parse_response (multi-valued -> list, resolution -> (x, y, unit),
# rangeOfInteger -> (lo, hi)).
M141W_IPP_ATTRS = {
    "printer-make-and-model": "HP LaserJet MFP M141w",
    "printer-uuid": "urn:uuid:0a1b2c3d-0000-4000-8000-000000000141",
    "printer-firmware-string-version": "20230421",
    "operations-supported": [0x02, 0x04, 0x05, 0x06, 0x08, 0x09, 0x0A, 0x0B, 0x3C],
    "document-format-supported": [
        "application/PCLm",
        "application/octet-stream",
        "image/pwg-raster",
        "image/urf",
    ],
    "pwg-raster-document-type-supported": "sgray_8",
    "pwg-raster-document-resolution-supported": [(300, 300, 3), (600, 600, 3)],
    "pwg-raster-document-sheet-back": "normal",
    "urf-supported": ["V1.5", "CP99", "W8", "OB10", "PQ4", "IS1-4", "MT1-3-5", "RS300-600"],
    "media-supported": ["iso_a4_210x297mm", "na_letter_8.5x11in"],
    "media-default": "na_letter_8.5x11in",
    "sides-supported": "one-sided",
    "print-color-mode-supported": ["auto", "auto-monochrome", "monochrome"],
    "copies-supported": (1, 99),
    "marker-names": "Black Cartridge HP W1500A",
    "marker-levels": 59,
    "marker-types": "toner",
}


@pytest.fixture
def m141w_attrs() -> dict:
    return dict(M141W_IPP_ATTRS)


@pytest.fixture
def m141w_printer() -> PrinterCaps:
    return PrinterCaps.from_attributes(dict(M141W_IPP_ATTRS))


@pytest.fixture
def m141w_scanner_xml() -> str:
    return (FIXTURES / "m141w_caps.xml").read_text()


@pytest.fixture
def adf_scanner_xml() -> str:
    return (FIXTURES / "adf_duplex_caps.xml").read_text()
