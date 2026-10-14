"""Config flow: manual setup by IP address and automatic zeroconf discovery."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from homeassistant.components import zeroconf

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # Home Assistant < 2025.1
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from .const import CONF_ESCL_URL, CONF_HOST, CONF_IPP_URL, CONF_NAME, CONF_NOTIFY, CONF_SCAN_FOLDER, DOMAIN
from .coordinator import check_scan_folder, default_scan_dir
from .discovery import ProbeResult, build_url, probe_host


SERVICE_TYPES = ["_uscan._tcp.local.", "_uscans._tcp.local.", "_ipp._tcp.local.", "_ipps._tcp.local."]
MANUAL = "manual"


async def async_find_devices(hass, timeout: float = 3.0) -> dict[str, str]:
    """Browse the network for scanners/printers. Return {ip: "Model (ip)"}."""
    aiozc = await zeroconf.async_get_async_instance(hass)
    found: set[tuple[str, str]] = set()

    def on_change(zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is ServiceStateChange.Added:
            found.add((service_type, name))

    browser = AsyncServiceBrowser(aiozc.zeroconf, SERVICE_TYPES, handlers=[on_change])
    try:
        await asyncio.sleep(timeout)
    finally:
        await browser.async_cancel()

    devices: dict[str, str] = {}
    for service_type, name in sorted(found):
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(aiozc.zeroconf, 1500):
            continue
        addresses = info.parsed_addresses(IPVersion.V4Only)
        if not addresses:
            continue
        props = {k.decode(errors="replace"): (v or b"").decode(errors="replace") for k, v in info.properties.items()}
        model = props.get("ty") or props.get("mdl") or name.split(".")[0]
        devices.setdefault(addresses[0], f"{model} ({addresses[0]})")
    return devices


class LocalPrinterConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LocalPrinterOptionsFlow()

    def __init__(self) -> None:
        self._host: str | None = None
        self._probe: ProbeResult | None = None

    async def _probe_host(self, host: str, escl_url: str | None = None, ipp_url: str | None = None) -> ProbeResult:
        session = async_get_clientsession(self.hass, verify_ssl=False)
        return await probe_host(session, host, escl_url, ipp_url)

    def _create(self) -> ConfigFlowResult:
        assert self._probe and self._host
        name = self._probe.model or self._host
        return self.async_create_entry(
            title=name,
            data={
                CONF_HOST: self._host,
                CONF_NAME: name,
                CONF_ESCL_URL: self._probe.escl_url,
                CONF_IPP_URL: self._probe.ipp_url,
            },
        )

    def _data_updates(self) -> dict[str, Any]:
        assert self._probe and self._host
        return {
            CONF_HOST: self._host,
            CONF_ESCL_URL: self._probe.escl_url,
            CONF_IPP_URL: self._probe.ipp_url,
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer the devices found on the network; fall back to entering an address."""
        if user_input is not None:
            if user_input[CONF_HOST] == MANUAL:
                return await self.async_step_manual()
            return await self.async_step_manual({CONF_HOST: user_input[CONF_HOST]})

        configured = {entry.data.get(CONF_HOST) for entry in self._async_current_entries()}
        devices = {ip: label for ip, label in (await async_find_devices(self.hass)).items() if ip not in configured}
        if not devices:
            return await self.async_step_manual()
        options = [{"value": ip, "label": label} for ip, label in sorted(devices.items(), key=lambda d: d[1])]
        options.append({"value": MANUAL, "label": "Enter an address manually"})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=options[0]["value"]): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._probe = await self._probe_host(self._host)
            if not self._probe.found:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(self._probe.uuid or self._host)
                self._abort_if_unique_id_configured(updates=self._data_updates())
                return self._create()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=self._host or ""): str}),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        props = discovery_info.properties
        uuid = (props.get("uuid") or "").lower() or None
        self._host = str(discovery_info.ip_address)
        service = discovery_info.type
        # The same device advertises _uscan and _ipp - deduplicate by uuid early
        if uuid:
            await self.async_set_unique_id(uuid)
            self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

        escl_url = ipp_url = None
        if service.startswith(("_uscan.", "_uscans.")):
            scheme = "https" if service.startswith("_uscans.") else "http"
            escl_url = build_url(scheme, self._host, discovery_info.port, props.get("rs") or "eSCL")
        elif service.startswith(("_ipp.", "_ipps.")):
            scheme = "https" if service.startswith("_ipps.") else "http"
            ipp_url = build_url(scheme, self._host, discovery_info.port, props.get("rp") or "ipp/print")

        self._probe = await self._probe_host(self._host, escl_url, ipp_url)
        if not self._probe.found:
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(self._probe.uuid or self._host)
        self._abort_if_unique_id_configured(updates=self._data_updates())

        self.context["title_placeholders"] = {"name": self._probe.model or self._host}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._probe
        if user_input is not None:
            return self._create()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": self._probe.model or self._host or "",
                "scanner": "yes" if self._probe.escl_url else "no",
                "printer": "yes" if self._probe.ipp_url else "no",
            },
        )


class LocalPrinterOptionsFlow(OptionsFlow):
    """Scan folder and where the "scan ready" notification goes."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        default_folder = str(default_scan_dir(self.hass))
        if user_input is not None:
            folder = (user_input.get(CONF_SCAN_FOLDER) or default_folder).strip().rstrip("/") or default_folder
            if error := await self.hass.async_add_executor_job(check_scan_folder, self.hass, folder):
                errors[CONF_SCAN_FOLDER] = error
            else:
                return self.async_create_entry(data={**user_input, CONF_SCAN_FOLDER: folder})
        services = sorted(s for s in self.hass.services.async_services_for_domain("notify") if s != "send_message")
        targets = ["persistent_notification", *services]
        current = [t for t in self.config_entry.options.get(CONF_NOTIFY, []) if t in targets]
        folder = self.config_entry.options.get(CONF_SCAN_FOLDER) or default_folder
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_FOLDER, default=folder): TextSelector(),
                    vol.Optional(CONF_NOTIFY, default=current): SelectSelector(
                        SelectSelectorConfig(options=targets, multiple=True, mode=SelectSelectorMode.LIST)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"default_folder": default_folder},
        )
