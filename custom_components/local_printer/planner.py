"""Pure decision logic (no HA, no network): print format selection and scan settings.

Kept separate from the coordinator so it can be unit tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import FORMATS, PAPER_SIZES, PREFERRED_PRINT_DPI, SCAN_AREA_MAX
from .escl import ScannerCaps, SourceCaps
from .ipp import PrinterCaps


class PlanError(Exception):
    """The requested operation is not supported by this device."""


@dataclass(frozen=True)
class PrintPlan:
    mime: str  # document-format sent to the printer
    convert: str  # "none" | "pwg" | "urf" | "image_to_pdf" | "pdf_repeat"
    color: bool  # raster in sRGB (True) or sGray (False)
    dpi: int | None
    media: str | None
    paper_mm: tuple[float, float]
    sides: str | None
    color_mode: str | None  # IPP print-color-mode
    copies_ipp: int  # copies requested through IPP
    copies_in_document: int  # copies repeated inside the document (printer cannot do copies)
    sheet_back: str = "normal"  # how to transform even raster pages for duplex printing


def printer_papers(caps: PrinterCaps, preferred: str | None = None) -> list[str]:
    """Known paper sizes the printer supports; `preferred` first, else the printer's default first.

    Many printers ship with a factory default of Letter regardless of region, so the
    caller can pass the size that matches the user's country.
    """
    papers = [name for name, p in PAPER_SIZES.items() if not caps.media or p["ipp"] in caps.media]
    default = preferred if preferred in papers else next(
        (n for n, p in PAPER_SIZES.items() if p["ipp"] == caps.media_default), None
    )
    if default in papers:
        papers.remove(default)
        papers.insert(0, default)
    return papers or ["a4"]


def _pick_resolution(supported: list[int]) -> int:
    if not supported or PREFERRED_PRINT_DPI in supported:
        return PREFERRED_PRINT_DPI
    higher = [r for r in supported if r > PREFERRED_PRINT_DPI]
    return min(higher) if higher else max(supported)


URF_SHEET_BACK = {"DM1": "normal", "DM2": "flipped", "DM3": "rotated", "DM4": "manual-tumble"}


def _urf_sheet_back(urf: list[str]) -> str:
    return next((URF_SHEET_BACK[k] for k in urf if k in URF_SHEET_BACK), "normal")


def _urf_resolutions(urf: list[str]) -> list[int]:
    for keyword in urf:
        if keyword.startswith("RS"):
            return sorted(int(v) for v in keyword[2:].split("-") if v.isdigit())
    return []


def plan_print(
    caps: PrinterCaps,
    *,
    is_pdf: bool,
    is_jpeg: bool,
    paper: str,
    sides: str,
    color: bool,
    copies: int,
) -> PrintPlan:
    formats = caps.formats
    if paper not in PAPER_SIZES:
        raise PlanError(f"Unknown paper size: {paper}")
    media = PAPER_SIZES[paper]["ipp"]
    if caps.media and media not in caps.media:
        media = None  # printer does not report this size - let it choose
    color = color and caps.supports_color
    if sides not in caps.sides:
        sides = "one-sided"
    color_mode = None
    if caps.color_modes:
        if color and "color" in caps.color_modes:
            color_mode = "color"
        elif "monochrome" in caps.color_modes:
            color_mode = "monochrome"
    copies_ipp, copies_doc = (copies, 1) if copies <= caps.max_copies else (1, copies)
    common = dict(
        media=media,
        paper_mm=PAPER_SIZES[paper]["mm"],
        sides=sides if sides != "one-sided" else None,
        color_mode=color_mode,
        copies_ipp=copies_ipp,
        copies_in_document=copies_doc,
    )

    # 1) Printer understands PDF natively - best quality, no conversion
    if is_pdf and "application/pdf" in formats and copies_doc == 1:
        return PrintPlan(mime="application/pdf", convert="none", color=color, dpi=None, **common)
    # 2) JPEG input and the printer accepts JPEG directly
    if is_jpeg and "image/jpeg" in formats and copies_doc == 1:
        return PrintPlan(mime="image/jpeg", convert="none", color=color, dpi=None, **common)
    # 3) PWG raster (IPP Everywhere)
    if "image/pwg-raster" in formats:
        types = caps.pwg_types or ["sgray_8"]
        use_color = color and "srgb_8" in types
        if not use_color and "sgray_8" not in types and "srgb_8" in types:
            use_color = True  # printer accepts RGB only - send RGB even for monochrome
        if not use_color and "sgray_8" not in types:
            raise PlanError(f"Unsupported PWG raster type: {', '.join(types)}")
        return PrintPlan(
            mime="image/pwg-raster",
            convert="pwg",
            color=use_color,
            dpi=_pick_resolution(caps.pwg_resolutions),
            sheet_back=caps.pwg_sheet_back,
            **common,
        )
    # 4) Apple raster (AirPrint)
    if "image/urf" in formats:
        use_color = color and "SRGB24" in caps.urf
        if not use_color and "W8" not in caps.urf and "SRGB24" in caps.urf:
            use_color = True
        return PrintPlan(
            mime="image/urf",
            convert="urf",
            color=use_color,
            dpi=_pick_resolution(_urf_resolutions(caps.urf)),
            sheet_back=_urf_sheet_back(caps.urf),
            **common,
        )
    # 5) PDF-only printer that cannot do copies - repeat the PDF pages ourselves
    if is_pdf and "application/pdf" in formats:
        return PrintPlan(mime="application/pdf", convert="pdf_repeat", color=color, dpi=None, **common)
    # 6) Printer accepts PDF and the input is an image - wrap it in a PDF
    if "application/pdf" in formats:
        return PrintPlan(mime="application/pdf", convert="image_to_pdf", color=color, dpi=PREFERRED_PRINT_DPI, **common)
    raise PlanError("Printer supports none of the formats this integration can produce (PDF, PWG raster, URF)")


@dataclass(frozen=True)
class ScanPlan:
    source: str
    color: str  # eSCL ColorMode
    dpi: int
    mime: str  # format requested from the scanner
    convert_to_pdf: bool  # scanner cannot produce PDF - build it from JPEG ourselves
    width: int
    height: int
    extension: str


def _nearest(value: int, options: list[int]) -> int:
    return min(options, key=lambda r: (abs(r - value), -r))


def plan_scan(caps: ScannerCaps, *, source: str, color: str, dpi: int, file_format: str, area: str) -> ScanPlan:
    """Build scan settings. `color` is an eSCL ColorMode, `file_format` a FORMATS key."""
    if not caps.sources:
        raise PlanError("Scanner reports no input sources")
    if source not in caps.sources:
        source = "platen" if "platen" in caps.sources else next(iter(caps.sources))
    src: SourceCaps = caps.sources[source]

    if src.color_modes and color not in src.color_modes:
        color = "Grayscale8" if "Grayscale8" in src.color_modes else src.color_modes[0]
    if src.resolutions:
        dpi = _nearest(dpi, src.resolutions)

    if file_format not in FORMATS:
        raise PlanError(f"Unknown file format: {file_format}")
    mime = FORMATS[file_format]
    convert = False
    if src.formats and mime not in src.formats:
        if file_format == "pdf" and "image/jpeg" in src.formats:
            mime, convert = "image/jpeg", True
        elif "image/jpeg" in src.formats:
            mime, file_format = "image/jpeg", "jpg"
        else:
            raise PlanError(f"Scanner does not support format {file_format}")

    if area == SCAN_AREA_MAX or area not in PAPER_SIZES:
        width, height = src.max_width, src.max_height
    else:
        width, height = PAPER_SIZES[area]["escl"]
    return ScanPlan(
        source=source,
        color=color,
        dpi=dpi,
        mime=mime,
        convert_to_pdf=convert,
        width=min(width, src.max_width),
        height=min(height, src.max_height),
        extension=file_format,
    )
