"""Number of copies for printing and copying."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LocalPrinterConfigEntry
from .coordinator import PrinterCoordinator
from .entity import LocalPrinterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LocalPrinterConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.runtime_data.can_print:
        async_add_entities([CopiesNumber(entry.runtime_data)])


class CopiesNumber(LocalPrinterEntity, NumberEntity, RestoreEntity):
    _attr_icon = "mdi:content-duplicate"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 1
    _attr_native_max_value = 99
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: PrinterCoordinator) -> None:
        super().__init__(coordinator, "copies")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state.split(".")[0].isdigit():
            self.coordinator.copies = int(float(last.state))

    @property
    def native_value(self) -> float:
        return self.coordinator.copies

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.copies = int(value)
        self.async_write_ha_state()
