"""Authenticated HTTP endpoint that serves stored scans (PDF/JPG/PNG).

Home Assistant's media browser only shows images, video and audio, so PDFs need their own
endpoint. Links handed out to dashboards and notifications are signed, so they open in any
browser without extra login while they are valid.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from aiohttp import web

from homeassistant.helpers.http import KEY_HASS, HomeAssistantView

from .const import DOMAIN, SCAN_EXTENSIONS, SCAN_URL_PREFIX


class ScanFileView(HomeAssistantView):
    url = SCAN_URL_PREFIX + "/{entry_id}/{filename}"
    name = f"api:{DOMAIN}:scans"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, filename: str) -> web.StreamResponse:
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        # Only plain file names of scan files - no directories, no hidden files
        if (
            Path(filename).name != filename
            or filename.startswith(".")
            or Path(filename).suffix.lower() not in SCAN_EXTENSIONS
        ):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        path = entry.runtime_data.scan_dir / filename
        if not await hass.async_add_executor_job(path.is_file):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.FileResponse(path, headers={"Content-Disposition": f'inline; filename="{filename}"'})
