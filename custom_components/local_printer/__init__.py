"""Local Printer & Scanner: scan, print and monitor network printers without any cloud."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import COLOR_MODES, DOMAIN, FORMATS, PAPER_SIZES, SCAN_AREA_MAX, SIDES, SOURCES
from .coordinator import PrinterCoordinator

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR]
SERVICES = ("scan", "print_file", "print_last_scan", "copy")

type LocalPrinterConfigEntry = ConfigEntry[PrinterCoordinator]

ENTRY_FIELD = {vol.Optional("config_entry_id"): cv.string}
COPIES_FIELD = {vol.Optional("copies"): vol.All(vol.Coerce(int), vol.Range(min=1, max=999))}
PRINT_FIELDS = {
    **COPIES_FIELD,
    vol.Optional("paper"): vol.In(list(PAPER_SIZES)),
    vol.Optional("sides"): vol.In(SIDES),
    vol.Optional("color"): cv.boolean,
}
SCAN_SCHEMA = vol.Schema(
    {
        **ENTRY_FIELD,
        vol.Optional("source"): vol.In(SOURCES),
        vol.Optional("color"): vol.In(list(COLOR_MODES)),
        vol.Optional("resolution"): vol.Coerce(int),
        vol.Optional("format"): vol.In(list(FORMATS)),
        vol.Optional("area"): vol.In([*PAPER_SIZES, SCAN_AREA_MAX]),
    }
)
PRINT_FILE_SCHEMA = vol.Schema({**ENTRY_FIELD, vol.Required("file_path"): cv.string, **PRINT_FIELDS})
PRINT_LAST_SCAN_SCHEMA = vol.Schema({**ENTRY_FIELD, **PRINT_FIELDS})
COPY_SCHEMA = vol.Schema({**ENTRY_FIELD, **COPIES_FIELD, vol.Optional("color"): cv.boolean})


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> PrinterCoordinator:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if entry_id := call.data.get("config_entry_id"):
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError("No matching printer is configured")
    if len(entries) > 1:
        raise ServiceValidationError("Several printers are configured - pass config_entry_id")
    return entries[0].runtime_data


def _print_kwargs(call: ServiceCall) -> dict:
    return {k: call.data[k] for k in ("copies", "paper", "sides", "color") if k in call.data}


async def async_setup_entry(hass: HomeAssistant, entry: LocalPrinterConfigEntry) -> bool:
    coordinator = PrinterCoordinator(hass, entry)
    try:
        await coordinator.async_load_capabilities()
    except UpdateFailed as err:
        raise ConfigEntryNotReady(str(err)) from err
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "scan"):
        return

    async def scan(call: ServiceCall) -> ServiceResponse:
        return await _coordinator(hass, call).async_scan(
            call.data.get("source"),
            call.data.get("color"),
            call.data.get("resolution"),
            call.data.get("format"),
            call.data.get("area"),
        )

    async def print_file(call: ServiceCall) -> ServiceResponse:
        return await _coordinator(hass, call).async_print_file(call.data["file_path"], **_print_kwargs(call))

    async def print_last_scan(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        if not coordinator.last_scan:
            raise HomeAssistantError("Nothing has been scanned yet")
        return await coordinator.async_print_file(coordinator.last_scan["path"], **_print_kwargs(call))

    async def copy(call: ServiceCall) -> ServiceResponse:
        return await _coordinator(hass, call).async_copy(call.data.get("copies"), call.data.get("color"))

    hass.services.async_register(DOMAIN, "scan", scan, SCAN_SCHEMA, SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "print_file", print_file, PRINT_FILE_SCHEMA, SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, "print_last_scan", print_last_scan, PRINT_LAST_SCAN_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, "copy", copy, COPY_SCHEMA, SupportsResponse.OPTIONAL)


async def async_unload_entry(hass: HomeAssistant, entry: LocalPrinterConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not [e for e in hass.config_entries.async_loaded_entries(DOMAIN) if e.entry_id != entry.entry_id]:
        for service in SERVICES:
            hass.services.async_remove(DOMAIN, service)
    return unloaded
