"""Sensors: printer/scanner/ADF state, one sensor per marker (toner/ink), last scan and print."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LocalPrinterConfigEntry
from .coordinator import PrinterCoordinator
from .entity import LocalPrinterEntity

PRINTER_STATES = ["idle", "processing", "stopped", "unknown"]
SCANNER_STATES = ["idle", "processing", "testing", "stopped", "down", "unknown"]


def _enum(value: str | None, options: list[str]) -> str | None:
    if value is None:
        return None
    value = value.lower()
    return value if value in options else "unknown"


def _adf_state(value: str | None) -> str | None:
    # eSCL AdfState values look like "ScannerAdfLoaded" / "ScannerAdfEmpty" / "ScannerAdfJam"
    if not value:
        return None
    return re.sub(r"^ScannerAdf", "", value).lower() or None


@dataclass(frozen=True, kw_only=True)
class LocalPrinterSensorDescription(SensorEntityDescription):
    value: Callable[[PrinterCoordinator], Any]
    attributes: Callable[[PrinterCoordinator], dict[str, Any] | None] = lambda c: None
    available: Callable[[PrinterCoordinator], bool] = lambda c: True


SENSORS = (
    LocalPrinterSensorDescription(
        key="printer_state",
        device_class=SensorDeviceClass.ENUM,
        options=PRINTER_STATES,
        value=lambda c: _enum(c.data.printer_state, PRINTER_STATES),
        attributes=lambda c: {"reasons": c.data.state_reasons, "message": c.data.state_message},
        available=lambda c: c.can_print,
    ),
    LocalPrinterSensorDescription(
        key="scanner_state",
        device_class=SensorDeviceClass.ENUM,
        options=SCANNER_STATES,
        value=lambda c: _enum(c.data.scanner_state, SCANNER_STATES),
        available=lambda c: c.can_scan,
    ),
    LocalPrinterSensorDescription(
        key="adf_state",
        value=lambda c: _adf_state(c.data.adf_state),
        available=lambda c: c.can_scan and any(s.startswith("adf") for s in c.scanner_caps.sources),
    ),
    LocalPrinterSensorDescription(
        key="last_scan",
        value=lambda c: c.last_scan["filename"] if c.last_scan else None,
        attributes=lambda c: c.last_scan,
        available=lambda c: c.can_scan,
    ),
    LocalPrinterSensorDescription(
        key="last_print",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: c.last_print["filename"] if c.last_print else None,
        attributes=lambda c: c.last_print,
        available=lambda c: c.can_print,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: LocalPrinterConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        LocalPrinterSensor(coordinator, d) for d in SENSORS if d.available(coordinator)
    ]
    entities += [MarkerSensor(coordinator, i) for i in range(len(coordinator.data.markers))]
    async_add_entities(entities)


class LocalPrinterSensor(LocalPrinterEntity, SensorEntity):
    entity_description: LocalPrinterSensorDescription

    def __init__(self, coordinator: PrinterCoordinator, description: LocalPrinterSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attributes(self.coordinator)


class MarkerSensor(LocalPrinterEntity, SensorEntity):
    """Toner/ink level for one marker reported by the printer."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PrinterCoordinator, index: int) -> None:
        super().__init__(coordinator, f"marker_{index}")
        self._index = index
        self._attr_translation_key = "marker"
        marker = coordinator.data.markers[index]
        self._attr_translation_placeholders = {"name": marker.name}
        self._attr_icon = "mdi:water" if marker.kind and "ink" in marker.kind else "mdi:printer-3d-nozzle"

    @property
    def _marker(self):
        markers = self.coordinator.data.markers
        return markers[self._index] if self._index < len(markers) else None

    @property
    def native_value(self) -> int | None:
        return self._marker.level if self._marker else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not (marker := self._marker):
            return None
        return {"marker_name": marker.name, "marker_color": marker.color, "marker_type": marker.kind}
