"""Selects for scan and print settings. Options come from the device's real capabilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LocalPrinterConfigEntry
from .const import COLOR_MODES, FORMATS, PAPER_SIZES, SCAN_AREA_MAX, SOURCES
from .coordinator import PrinterCoordinator
from .entity import LocalPrinterEntity
from .planner import printer_papers


def _scan_colors(c: PrinterCoordinator) -> list[str]:
    modes = {m for s in c.scanner_caps.sources.values() for m in s.color_modes}
    return [key for key, escl in COLOR_MODES.items() if escl in modes] or ["color"]


def _scan_resolutions(c: PrinterCoordinator) -> list[str]:
    values = sorted({r for s in c.scanner_caps.sources.values() for r in s.resolutions})
    return [str(r) for r in values] or ["300"]


def _scan_formats(c: PrinterCoordinator) -> list[str]:
    mimes = {f for s in c.scanner_caps.sources.values() for f in s.formats}
    options = [key for key, mime in FORMATS.items() if mime in mimes]
    if "pdf" not in options and "image/jpeg" in mimes:
        options.insert(0, "pdf")  # built from JPEG by the integration
    return options or ["jpg"]


def _print_colors(c: PrinterCoordinator) -> list[str]:
    return ["monochrome", "color"] if c.printer_caps.supports_color else ["monochrome"]


@dataclass(frozen=True)
class SelectSpec:
    key: str
    icon: str
    attr: str  # coordinator attribute holding the current value
    options: Callable[[PrinterCoordinator], list[str]]
    available: Callable[[PrinterCoordinator], bool]
    numeric: bool = False


SELECTS = (
    SelectSpec(
        "scan_source",
        "mdi:tray-full",
        "scan_source",
        lambda c: [s for s in SOURCES if s in c.scanner_caps.sources],
        lambda c: c.can_scan and len(c.scanner_caps.sources) > 1,
    ),
    SelectSpec("scan_color", "mdi:palette", "scan_color", _scan_colors, lambda c: c.can_scan),
    SelectSpec(
        "scan_resolution", "mdi:image-size-select-large", "scan_resolution", _scan_resolutions, lambda c: c.can_scan, True
    ),
    SelectSpec("scan_format", "mdi:file-pdf-box", "scan_format", _scan_formats, lambda c: c.can_scan),
    SelectSpec("scan_area", "mdi:crop", "scan_area", lambda c: [*PAPER_SIZES, SCAN_AREA_MAX], lambda c: c.can_scan),
    SelectSpec("print_paper", "mdi:file-outline", "print_paper", lambda c: printer_papers(c.printer_caps, c.regional_paper), lambda c: c.can_print),
    SelectSpec(
        "print_sides",
        "mdi:book-open-page-variant",
        "print_sides",
        lambda c: [s for s in ("one-sided", "two-sided-long-edge") if s in c.printer_caps.sides],
        lambda c: c.can_print and "two-sided-long-edge" in c.printer_caps.sides,
    ),
    SelectSpec(
        "print_color",
        "mdi:palette-outline",
        "print_color",
        _print_colors,
        lambda c: c.can_print and c.printer_caps.supports_color,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: LocalPrinterConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(LocalPrinterSelect(coordinator, spec) for spec in SELECTS if spec.available(coordinator))


class LocalPrinterSelect(LocalPrinterEntity, SelectEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PrinterCoordinator, spec: SelectSpec) -> None:
        super().__init__(coordinator, spec.key)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_options = spec.options(coordinator)
        if self.current_option not in self._attr_options:
            self._set(self._attr_options[0])

    def _set(self, option: str) -> None:
        setattr(self.coordinator, self._spec.attr, int(option) if self._spec.numeric else option)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in self.options:
            self._set(last.state)

    @property
    def current_option(self) -> str:
        return str(getattr(self.coordinator, self._spec.attr))

    async def async_select_option(self, option: str) -> None:
        self._set(option)
        self.async_write_ha_state()
