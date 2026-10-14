"""Buttons: scan, copy, print last scan, identify printer."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LocalPrinterConfigEntry
from .coordinator import PrinterCoordinator
from .entity import LocalPrinterEntity


async def _print_last_scan(c: PrinterCoordinator) -> Any:
    if not c.last_scan:
        raise HomeAssistantError("Nothing has been scanned yet")
    return await c.async_print_file(c.last_scan["path"])


async def _identify(c: PrinterCoordinator) -> None:
    await c.ipp.identify()


@dataclass(frozen=True, kw_only=True)
class LocalPrinterButtonDescription(ButtonEntityDescription):
    press: Callable[[PrinterCoordinator], Awaitable[Any]]
    available: Callable[[PrinterCoordinator], bool]


BUTTONS = (
    LocalPrinterButtonDescription(
        key="scan", icon="mdi:scanner", press=lambda c: c.async_scan(), available=lambda c: c.can_scan
    ),
    LocalPrinterButtonDescription(
        key="copy",
        icon="mdi:content-copy",
        press=lambda c: c.async_copy(),
        available=lambda c: c.can_scan and c.can_print,
    ),
    LocalPrinterButtonDescription(
        key="print_last_scan",
        icon="mdi:printer",
        press=_print_last_scan,
        available=lambda c: c.can_scan and c.can_print,
    ),
    LocalPrinterButtonDescription(
        key="identify",
        icon="mdi:bell-ring",
        entity_category=EntityCategory.CONFIG,
        press=_identify,
        available=lambda c: c.can_print and c.printer_caps.can_identify,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: LocalPrinterConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(LocalPrinterButton(coordinator, d) for d in BUTTONS if d.available(coordinator))


class LocalPrinterButton(LocalPrinterEntity, ButtonEntity):
    entity_description: LocalPrinterButtonDescription

    def __init__(self, coordinator: PrinterCoordinator, description: LocalPrinterButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press(self.coordinator)
