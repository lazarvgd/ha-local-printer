"""Coordinator: device capabilities, periodic status and jobs (scan, print, copy)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from pathlib import Path
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import raster
from .const import (
    COLOR_MODES,
    CONF_ESCL_URL,
    CONF_HOST,
    CONF_IPP_URL,
    DEFAULT_RESOLUTION,
    DOMAIN,
    EVENT_PRINT_DONE,
    EVENT_SCAN_DONE,
    LETTER_COUNTRIES,
    SCAN_DIR,
    UPDATE_INTERVAL,
)
from .discovery import replace_host
from .escl import EsclClient, EsclError, ScannerCaps
from .ipp import PRINTER_STATES, IppClient, IppError, PrinterCaps, _as_list
from .planner import PlanError, PrintPlan, ScanPlan, plan_print, plan_scan, printer_papers

_LOGGER = logging.getLogger(__name__)
NETWORK_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)
COPY_DPI = 300


@dataclass
class Marker:
    name: str
    level: int | None
    color: str | None
    kind: str | None


@dataclass
class DeviceData:
    scanner_state: str | None = None
    adf_state: str | None = None
    printer_state: str | None = None
    state_reasons: list[str] = field(default_factory=list)
    state_message: str | None = None
    markers: list[Marker] = field(default_factory=list)


def parse_markers(attrs: dict[str, Any]) -> list[Marker]:
    names = _as_list(attrs.get("marker-names"))
    levels = _as_list(attrs.get("marker-levels"))
    colors = _as_list(attrs.get("marker-colors"))
    kinds = _as_list(attrs.get("marker-types"))
    return [
        Marker(
            name=name,
            # negative levels (-1/-2/-3) mean unknown / unavailable
            level=levels[i] if i < len(levels) and isinstance(levels[i], int) and levels[i] >= 0 else None,
            color=colors[i] if i < len(colors) else None,
            kind=kinds[i] if i < len(kinds) else None,
        )
        for i, name in enumerate(names)
    ]


def _prepare(document: bytes, plan: PrintPlan) -> tuple[bytes, int | None]:
    """Blocking: convert the document according to the plan. Return (payload, page count)."""
    if plan.convert == "none":
        return document, None
    if plan.convert == "image_to_pdf":
        return raster.images_to_pdf([document], plan.dpi), 1
    if plan.convert == "pdf_repeat":
        return raster.merge_pdfs([document] * plan.copies_in_document), None
    pages = [raster.fit_to_paper(p, plan.paper_mm, plan.dpi) for p in raster.load_pages(document, plan.dpi, plan.color)]
    if not pages:
        raise ValueError("Document has no pages")
    pages = pages * plan.copies_in_document
    duplex = plan.sides is not None
    if duplex:
        pages = [p if i % 2 == 0 else raster.back_side_transform(p, plan.sheet_back) for i, p in enumerate(pages)]
    if plan.convert == "pwg":
        return raster.encode_pwg(pages, plan.dpi, plan.paper_mm, plan.media or "", duplex), len(pages)
    return raster.encode_urf(pages, plan.dpi, duplex), len(pages)


class PrinterCoordinator(DataUpdateCoordinator[DeviceData]):
    """Owns the clients, device capabilities, user settings and last results."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        host = entry.data[CONF_HOST]
        escl_url, ipp_url = entry.data.get(CONF_ESCL_URL), entry.data.get(CONF_IPP_URL)
        # The host may have been updated by zeroconf after the URLs were stored
        escl_url = replace_host(escl_url, host) if escl_url else None
        ipp_url = replace_host(ipp_url, host) if ipp_url else None
        self.host = host
        self.escl = EsclClient(self._session(escl_url), escl_url) if escl_url else None
        self.ipp = IppClient(self._session(ipp_url), ipp_url) if ipp_url else None
        self.scanner_caps: ScannerCaps | None = None
        self.printer_caps: PrinterCaps | None = None

        # Settings changed by the select/number entities (option keys from const.py)
        self.scan_source = "platen"
        self.scan_color = "color"
        self.scan_resolution = DEFAULT_RESOLUTION
        self.scan_format = "pdf"
        self.scan_area = "a4"
        self.print_paper = "a4"
        self.print_sides = "one-sided"
        self.print_color = "monochrome"
        self.copies = 1

        self.last_scan: dict[str, Any] | None = None
        self.last_print: dict[str, Any] | None = None
        self._print_lock = asyncio.Lock()

    def _session(self, url: str) -> aiohttp.ClientSession:
        # Printers serving HTTPS almost always use a self-signed certificate
        return async_get_clientsession(self.hass, verify_ssl=not url.startswith("https"))

    @property
    def scan_dir(self) -> Path:
        media = self.hass.config.media_dirs.get("local") or self.hass.config.path("media")
        return Path(media) / SCAN_DIR

    @property
    def regional_paper(self) -> str:
        return "letter" if (self.hass.config.country or "").upper() in LETTER_COUNTRIES else "a4"

    @property
    def can_scan(self) -> bool:
        return self.scanner_caps is not None and bool(self.scanner_caps.sources)

    @property
    def can_print(self) -> bool:
        return self.printer_caps is not None

    async def async_load_capabilities(self) -> None:
        """Run once at setup: find out what the device can do."""
        if self.escl:
            try:
                self.scanner_caps = await self.escl.capabilities()
            except (EsclError, *NETWORK_ERRORS) as err:
                _LOGGER.warning("Could not read scanner capabilities: %s", err)
        if self.ipp:
            try:
                self.printer_caps = await self.ipp.capabilities()
            except (IppError, *NETWORK_ERRORS) as err:
                _LOGGER.warning("Could not read printer capabilities: %s", err)
        if not self.can_scan and not self.can_print:
            raise UpdateFailed("Device responds neither as a scanner nor as a printer")
        if self.printer_caps:
            self.print_paper = printer_papers(self.printer_caps, self.regional_paper)[0]
            self.scan_area = self.print_paper if self.print_paper in ("a4", "letter") else "a4"

    async def _async_update_data(self) -> DeviceData:
        data = DeviceData()
        ok = False
        if self.ipp:
            try:
                attrs = await self.ipp.status()
                data.printer_state = PRINTER_STATES.get(attrs.get("printer-state"), "unknown")
                data.state_reasons = [r for r in _as_list(attrs.get("printer-state-reasons")) if r != "none"]
                data.state_message = attrs.get("printer-state-message") or None
                data.markers = parse_markers(attrs)
                ok = True
            except (IppError, *NETWORK_ERRORS) as err:
                _LOGGER.debug("IPP status failed: %s", err)
        if self.escl:
            if self.escl.busy:
                data.scanner_state = "Processing"  # do not poll the scanner while it is scanning
                ok = True
            else:
                try:
                    data.scanner_state, data.adf_state = await self.escl.status()
                    ok = True
                except NETWORK_ERRORS as err:
                    _LOGGER.debug("eSCL status failed: %s", err)
        if not ok:
            raise UpdateFailed("Device is not reachable")
        return data

    # ---- Scanning ----

    async def _scan(self, source: str, color: str, dpi: int, file_format: str, area: str) -> tuple[list[bytes], ScanPlan]:
        if not self.escl or not self.scanner_caps:
            raise HomeAssistantError("This device has no scanner (or it is unavailable)")
        if color not in COLOR_MODES:
            raise HomeAssistantError(f"Unknown color mode: {color}")
        try:
            plan = plan_scan(
                self.scanner_caps, source=source, color=COLOR_MODES[color], dpi=dpi, file_format=file_format, area=area
            )
        except PlanError as err:
            raise HomeAssistantError(str(err)) from err
        try:
            documents = await self.escl.scan(
                self.scanner_caps, plan.source, plan.color, plan.dpi, plan.mime, plan.width, plan.height
            )
        except (EsclError, *NETWORK_ERRORS) as err:
            raise HomeAssistantError(f"Scan failed: {err}") from err
        return documents, plan

    async def async_scan(
        self,
        source: str | None = None,
        color: str | None = None,
        resolution: int | None = None,
        file_format: str | None = None,
        area: str | None = None,
    ) -> dict[str, Any]:
        """Scan and store the file(s) in media/scans."""
        documents, plan = await self._scan(
            source or self.scan_source,
            color or self.scan_color,
            int(resolution or self.scan_resolution),
            (file_format or self.scan_format).lower(),
            area or self.scan_area,
        )
        if plan.extension == "pdf":
            try:
                if plan.convert_to_pdf:
                    pdf = await self.hass.async_add_executor_job(raster.images_to_pdf, documents, plan.dpi)
                else:
                    pdf = await self.hass.async_add_executor_job(raster.merge_pdfs, documents)
            except Exception as err:  # noqa: BLE001 - PIL/pdfium raise many exception types
                raise HomeAssistantError(f"Could not build a PDF from the scan: {err}") from err
            documents = [pdf]

        now = dt_util.now()
        stem = f"scan_{now:%Y-%m-%d_%H-%M-%S}"
        if len(documents) == 1:
            names = [f"{stem}.{plan.extension}"]
        else:
            names = [f"{stem}_{i}.{plan.extension}" for i in range(1, len(documents) + 1)]
        paths = [self.scan_dir / name for name in names]

        def _write() -> None:
            self.scan_dir.mkdir(parents=True, exist_ok=True)
            for path, doc in zip(paths, documents):
                path.write_bytes(doc)

        await self.hass.async_add_executor_job(_write)

        self.last_scan = {
            "path": str(paths[0]),
            "paths": [str(p) for p in paths],
            "filename": names[0],
            "files": len(paths),
            "size_kb": round(sum(len(d) for d in documents) / 1024),
            "media_content_id": f"media-source://media_source/local/{SCAN_DIR}/{names[0]}",
            "time": now.isoformat(),
            "source": plan.source,
            "color_mode": plan.color,
            "resolution": plan.dpi,
            "format": plan.extension,
            "entry_id": self.config_entry.entry_id,
        }
        self.hass.bus.async_fire(EVENT_SCAN_DONE, self.last_scan)
        self.async_update_listeners()
        await self.async_request_refresh()
        return self.last_scan

    # ---- Printing ----

    async def async_print(
        self,
        document: bytes,
        filename: str,
        copies: int | None = None,
        paper: str | None = None,
        sides: str | None = None,
        color: bool | None = None,
    ) -> dict[str, Any]:
        """Print a PDF or image in a format the printer understands."""
        if not self.ipp or not self.printer_caps:
            raise HomeAssistantError("This device has no printer (or it is unavailable)")
        copies = int(copies or self.copies)
        try:
            plan = plan_print(
                self.printer_caps,
                is_pdf=raster.is_pdf(document),
                is_jpeg=document[:3] == b"\xff\xd8\xff",
                paper=paper or self.print_paper,
                sides=sides or self.print_sides,
                color=self.print_color == "color" if color is None else color,
                copies=copies,
            )
        except PlanError as err:
            raise HomeAssistantError(str(err)) from err

        if self._print_lock.locked():
            raise HomeAssistantError("A print job is already being sent")
        async with self._print_lock:
            try:
                payload, pages = await self.hass.async_add_executor_job(_prepare, document, plan)
            except Exception as err:  # noqa: BLE001 - PIL/pdfium raise many exception types
                raise HomeAssistantError(f"Could not prepare the document for printing: {err}") from err
            try:
                job_id = await self.ipp.print_document(
                    payload,
                    plan.mime,
                    filename,
                    plan.copies_ipp,
                    plan.media,
                    plan.sides,
                    plan.color_mode,
                    plan.dpi if plan.convert in ("pwg", "urf") else None,
                )
            except (IppError, *NETWORK_ERRORS) as err:
                raise HomeAssistantError(f"Printing failed: {err}") from err

        self.last_print = {
            "filename": filename,
            "pages": pages,
            "copies": copies,
            "format": plan.mime,
            "job_id": job_id,
            "time": dt_util.now().isoformat(),
            "entry_id": self.config_entry.entry_id,
        }
        self.hass.bus.async_fire(EVENT_PRINT_DONE, self.last_print)
        self.async_update_listeners()
        await self.async_request_refresh()
        return self.last_print

    async def async_print_file(self, file_path: str, **kwargs: Any) -> dict[str, Any]:
        path = Path(file_path)
        allowed = self.hass.config.is_allowed_path(str(path)) or path.resolve().is_relative_to(self.scan_dir.resolve())
        if not allowed:
            raise HomeAssistantError(f"Path {path} is not allowed (use /media or allowlist_external_dirs)")
        try:
            document = await self.hass.async_add_executor_job(path.read_bytes)
        except OSError as err:
            raise HomeAssistantError(f"Could not read {path}: {err}") from err
        return await self.async_print(document, path.name, **kwargs)

    async def async_copy(self, copies: int | None = None, color: bool | None = None) -> dict[str, Any]:
        """Copy: scan and print right away (all pages when using the ADF)."""
        if not self.can_print:
            raise HomeAssistantError("This device has no printer")
        color = self.print_color == "color" if color is None else color
        images, _ = await self._scan(
            self.scan_source, "color" if color else "grayscale", COPY_DPI, "jpg", self.print_paper
        )
        if len(images) == 1:
            document = images[0]
        else:
            document = await self.hass.async_add_executor_job(raster.images_to_pdf, images, COPY_DPI)
        return await self.async_print(document, "copy", copies=copies, color=color)
