"""End-to-end tests of the integration inside Home Assistant with a fake printer."""

from __future__ import annotations

from http import HTTPStatus

import aiohttp

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.local_printer.const import CONF_ESCL_URL, CONF_HOST, CONF_IPP_URL, CONF_NAME, DOMAIN

from .conftest import PDF_PAGE

HOST = "192.0.2.10"


async def _setup(hass: HomeAssistant, tmp_path, options=None) -> MockConfigEntry:
    hass.config.media_dirs = {"local": str(tmp_path)}
    hass.config.country = "RS"
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="0a1b2c3d-0000-4000-8000-000000000141",
        title="HP LaserJet MFP M139-M142",
        data={
            CONF_HOST: HOST,
            CONF_NAME: "HP LaserJet MFP M139-M142",
            CONF_ESCL_URL: f"http://{HOST}:8080/eSCL",
            CONF_IPP_URL: f"http://{HOST}:631/ipp/print",
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant, entry, key: str) -> str:
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.unique_id.endswith(f"_{key}"):
            return ent.entity_id
    raise AssertionError(f"no entity for {key}")


async def test_setup_creates_entities(hass: HomeAssistant, tmp_path, device):
    entry = await _setup(hass, tmp_path)
    assert hass.states.get(_entity(hass, entry, "printer_state")).state == "idle"
    assert hass.states.get(_entity(hass, entry, "marker_0")).state == "59"
    assert hass.states.get(_entity(hass, entry, "scans")).state == "0"
    # A4 because the configured country uses A4, even though the printer defaults to Letter
    assert hass.states.get(_entity(hass, entry, "print_paper")).state == "a4"
    for key in ("scan", "copy", "print_last_scan", "identify", "print_history", "last_scan_preview"):
        _entity(hass, entry, key)


async def test_scan_is_stored_listed_and_served(
    hass: HomeAssistant, tmp_path, device, hass_client, hass_client_no_auth
):
    entry = await _setup(hass, tmp_path)
    events = async_capture_events(hass, f"{DOMAIN}_scan_done")

    await hass.services.async_call("button", "press", {"entity_id": _entity(hass, entry, "scan")}, blocking=True)
    await hass.async_block_till_done()

    files = list((tmp_path / "scans").iterdir())
    assert len(files) == 1 and files[0].read_bytes() == PDF_PAGE
    assert events and events[0].data["filename"] == files[0].name

    scans = hass.states.get(_entity(hass, entry, "scans"))
    assert scans.state == "1"
    link = scans.attributes["files"][0]["url"]

    # Signed link works without an auth header; the plain path requires auth
    client = await hass_client()
    resp = await client.get(link)
    assert resp.status == HTTPStatus.OK and await resp.read() == PDF_PAGE
    assert resp.headers["Content-Type"] == "application/pdf"
    base = f"/api/local_printer/scans/{entry.entry_id}"
    resp = await client.get(f"{base}/{files[0].name}")
    assert resp.status == HTTPStatus.OK  # hass_client sends a valid token
    resp = await client.get(f"{base}/..%2Fsecret.pdf")
    assert resp.status in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)  # HA's own filter may answer first
    resp = await client.get(f"{base}/.hidden.pdf")
    assert resp.status == HTTPStatus.NOT_FOUND
    resp = await client.get(f"/api/local_printer/scans/unknown_entry/{files[0].name}")
    assert resp.status == HTTPStatus.NOT_FOUND
    unauth = await hass_client_no_auth()  # unsigned link without a token
    assert (await unauth.get(link.split("?")[0])).status == HTTPStatus.UNAUTHORIZED

    # Preview image is rendered from the PDF
    image = hass.states.get(_entity(hass, entry, "last_scan_preview"))
    assert image.state != "unavailable"
    resp = await client.get(image.attributes["entity_picture"])
    assert resp.status == HTTPStatus.OK and (await resp.read())[:8] == b"\x89PNG\r\n\x1a\n"


async def test_scan_sends_notification(hass: HomeAssistant, tmp_path, device):
    calls = []
    hass.services.async_register("notify", "mobile_app_phone", lambda call: calls.append(call))
    await _setup(hass, tmp_path, options={"notify_services": ["mobile_app_phone"]})

    await hass.services.async_call(DOMAIN, "scan", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["title"] == "Scan ready"
    assert "/api/local_printer/scans/" in calls[0].data["data"]["clickAction"]


async def test_print_upload_and_history(hass: HomeAssistant, tmp_path, device, hass_client):
    entry = await _setup(hass, tmp_path)
    assert await async_setup_component(hass, "file_upload", {})
    client = await hass_client()
    form = aiohttp.FormData()
    form.add_field("file", PDF_PAGE, filename="letter.pdf", content_type="application/pdf")
    resp = await client.post("/api/file_upload", data=form)
    assert resp.status == HTTPStatus.OK
    file_id = (await resp.json())["file_id"]

    result = await hass.services.async_call(
        DOMAIN, "print_upload", {"file": file_id, "copies": 2}, blocking=True, return_response=True
    )
    assert result["job_id"] == 42 and result["copies"] == 2
    args = device["print"].await_args.args
    assert args[1] == "image/pwg-raster" and args[0][:4] == b"RaS2"

    history = hass.states.get(_entity(hass, entry, "print_history"))
    assert history.state == "1" and history.attributes["jobs"][0]["filename"] == "letter.pdf"

    # History survives a reload
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, entry, "print_history")).state == "1"


async def test_delete_scan(hass: HomeAssistant, tmp_path, device):
    entry = await _setup(hass, tmp_path)
    response = await hass.services.async_call(DOMAIN, "scan", {}, blocking=True, return_response=True)
    await hass.services.async_call(DOMAIN, "delete_scan", {"filename": response["filename"]}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, entry, "scans")).state == "0"


async def test_options_flow_lists_notify_services(hass: HomeAssistant, tmp_path, device):
    hass.services.async_register("notify", "mobile_app_phone", lambda call: None)
    entry = await _setup(hass, tmp_path)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    # Default folder is <media>/scans
    schema_defaults = {str(k): k.default() for k in result["data_schema"].schema if hasattr(k, "default")}
    assert schema_defaults["scan_folder"] == str(tmp_path / "scans")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_folder": str(tmp_path / "nas" / "scans"), "notify_services": ["mobile_app_phone"]}
    )
    assert entry.options["notify_services"] == ["mobile_app_phone"]
    assert entry.options["scan_folder"] == str(tmp_path / "nas" / "scans")

    # New scans go to the chosen folder
    await hass.services.async_call(DOMAIN, "scan", {}, blocking=True, return_response=True)
    assert len(list((tmp_path / "nas" / "scans").iterdir())) == 1


async def test_options_flow_rejects_bad_folders(hass: HomeAssistant, tmp_path, device):
    entry = await _setup(hass, tmp_path)
    for folder, error in (("relative/path", "folder_not_absolute"), ("/etc/scans", "folder_not_allowed")):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"scan_folder": folder})
        assert result["type"] == "form" and result["errors"] == {"scan_folder": error}


async def test_user_flow_offers_discovered_devices(hass: HomeAssistant, device):
    from unittest.mock import AsyncMock, patch

    from custom_components.local_printer.discovery import ProbeResult

    probe = ProbeResult(
        escl_url=f"http://{HOST}:8080/eSCL",
        ipp_url=f"http://{HOST}:631/ipp/print",
        scanner=None,
        printer=None,
    )
    with (
        patch(
            "custom_components.local_printer.config_flow.async_find_devices",
            AsyncMock(return_value={HOST: f"HP LaserJet MFP M139-M142 ({HOST})"}),
        ),
        patch("custom_components.local_printer.config_flow.probe_host", AsyncMock(return_value=probe)),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["step_id"] == "user"
        options = result["data_schema"].schema["host"].config["options"]
        assert options[0] == {"value": HOST, "label": f"HP LaserJet MFP M139-M142 ({HOST})"}
        assert options[-1]["value"] == "manual"

        # Picking the manual option shows the address form
        manual = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "manual"})
        assert manual["step_id"] == "manual"

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
        assert result["type"] == "create_entry"
        assert result["data"]["escl_url"] == f"http://{HOST}:8080/eSCL"


async def test_user_flow_without_discovery_asks_for_address(hass: HomeAssistant):
    from unittest.mock import AsyncMock, patch

    with patch("custom_components.local_printer.config_flow.async_find_devices", AsyncMock(return_value={})):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "manual"
