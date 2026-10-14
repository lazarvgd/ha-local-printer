"""Preview image of the newest scan, shown on the device page and in dashboards."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import LocalPrinterConfigEntry
from .coordinator import PrinterCoordinator
from .entity import LocalPrinterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LocalPrinterConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.runtime_data.can_scan:
        async_add_entities([LastScanImage(entry.runtime_data)])


class LastScanImage(LocalPrinterEntity, ImageEntity):
    _attr_content_type = "image/png"

    def __init__(self, coordinator: PrinterCoordinator) -> None:
        LocalPrinterEntity.__init__(self, coordinator, "last_scan_preview")
        ImageEntity.__init__(self, coordinator.hass)
        self._newest: str | None = None
        self._update_from_data()

    def _update_from_data(self) -> None:
        scans = self.coordinator.data.scans if self.coordinator.data else []
        newest = scans[0]["name"] if scans else None
        if newest != self._newest:
            self._newest = newest
            self._attr_image_last_updated = (
                datetime.fromisoformat(scans[0]["time"]) if scans else dt_util.utcnow()
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from_data()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return super().available and self._newest is not None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if not self._newest:
            return None
        return {"file": self._newest, "url": self.coordinator.scan_url(self._newest)}

    async def async_image(self) -> bytes | None:
        return await self.coordinator.async_preview()
