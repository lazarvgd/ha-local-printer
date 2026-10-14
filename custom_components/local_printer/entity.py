"""Shared entity base: every entity belongs to one device per config entry."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DOMAIN
from .coordinator import PrinterCoordinator


class LocalPrinterEntity(CoordinatorEntity[PrinterCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PrinterCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_translation_key = key
        printer, scanner = coordinator.printer_caps, coordinator.scanner_caps
        model = (printer and printer.make_and_model) or (scanner and scanner.make_and_model)
        manufacturer = model.split()[0] if model else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.data.get(CONF_NAME) or entry.title,
            manufacturer=manufacturer,
            model=model,
            sw_version=printer.firmware if printer else None,
            configuration_url=f"http://{coordinator.host}",
        )
