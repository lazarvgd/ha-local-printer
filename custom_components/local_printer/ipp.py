"""Minimal IPP client: capabilities, status, printing and identify."""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import struct
from typing import Any
from urllib.parse import urlsplit

import aiohttp

# Delimiter tags
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03

# Value tags
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_RANGE = 0x33
TAG_RESOLUTION = 0x32
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MIME = 0x49

OP_PRINT_JOB = 0x0002
OP_VALIDATE_JOB = 0x0004
OP_GET_PRINTER_ATTRIBUTES = 0x000B
OP_IDENTIFY_PRINTER = 0x003C

PRINTER_STATES = {3: "idle", 4: "processing", 5: "stopped"}

STATUS_ATTRIBUTES = [
    "printer-state",
    "printer-state-reasons",
    "printer-state-message",
    "marker-names",
    "marker-levels",
    "marker-colors",
    "marker-types",
]
CAPABILITY_ATTRIBUTES = [
    "printer-make-and-model",
    "printer-uuid",
    "printer-firmware-string-version",
    "operations-supported",
    "document-format-supported",
    "pwg-raster-document-type-supported",
    "pwg-raster-document-resolution-supported",
    "pwg-raster-document-sheet-back",
    "urf-supported",
    "media-supported",
    "media-default",
    "sides-supported",
    "print-color-mode-supported",
    "copies-supported",
]


class IppError(Exception):
    """Error while communicating with the printer."""


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@dataclass
class PrinterCaps:
    make_and_model: str | None = None
    uuid: str | None = None
    firmware: str | None = None
    formats: list[str] = field(default_factory=list)
    pwg_types: list[str] = field(default_factory=list)
    pwg_resolutions: list[int] = field(default_factory=list)
    pwg_sheet_back: str = "normal"
    urf: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    media_default: str | None = None
    sides: list[str] = field(default_factory=list)
    color_modes: list[str] = field(default_factory=list)
    max_copies: int = 1
    can_identify: bool = False

    @classmethod
    def from_attributes(cls, attrs: dict[str, Any]) -> PrinterCaps:
        copies = attrs.get("copies-supported")
        uuid = attrs.get("printer-uuid")
        return cls(
            make_and_model=attrs.get("printer-make-and-model"),
            uuid=uuid.removeprefix("urn:uuid:") if isinstance(uuid, str) else None,
            firmware=attrs.get("printer-firmware-string-version"),
            formats=_as_list(attrs.get("document-format-supported")),
            pwg_types=_as_list(attrs.get("pwg-raster-document-type-supported")),
            pwg_resolutions=sorted({r[0] for r in _as_list(attrs.get("pwg-raster-document-resolution-supported"))}),
            pwg_sheet_back=attrs.get("pwg-raster-document-sheet-back") or "normal",
            urf=_as_list(attrs.get("urf-supported")),
            media=_as_list(attrs.get("media-supported")),
            media_default=attrs.get("media-default"),
            sides=_as_list(attrs.get("sides-supported")) or ["one-sided"],
            color_modes=_as_list(attrs.get("print-color-mode-supported")),
            max_copies=copies[1] if isinstance(copies, tuple) else 1,
            can_identify=OP_IDENTIFY_PRINTER in _as_list(attrs.get("operations-supported")),
        )

    @property
    def supports_color(self) -> bool:
        return "color" in self.color_modes or "srgb_8" in self.pwg_types or "SRGB24" in self.urf


def _attr(tag: int, name: str, value: Any) -> bytes:
    if tag in (TAG_INTEGER, TAG_ENUM):
        raw = struct.pack(">i", value)
    elif tag == TAG_BOOLEAN:
        raw = b"\x01" if value else b"\x00"
    elif tag == TAG_RESOLUTION:
        raw = struct.pack(">iib", value, value, 3)  # 3 = dpi
    else:
        raw = str(value).encode()
    enc_name = name.encode()
    return struct.pack(">bh", tag, len(enc_name)) + enc_name + struct.pack(">h", len(raw)) + raw


def _additional(tag: int, value: str) -> bytes:
    raw = value.encode()
    return struct.pack(">bhh", tag, 0, len(raw)) + raw


def _decode(tag: int, raw: bytes) -> Any:
    if tag in (TAG_INTEGER, TAG_ENUM) and len(raw) == 4:
        return struct.unpack(">i", raw)[0]
    if tag == TAG_BOOLEAN:
        return raw != b"\x00"
    if tag == TAG_RESOLUTION and len(raw) == 9:
        return struct.unpack(">iib", raw)  # (x, y, units)
    if tag == TAG_RANGE and len(raw) == 8:
        return struct.unpack(">ii", raw)
    if 0x40 <= tag <= 0x4F:
        return raw.decode(errors="replace")
    return raw


def parse_response(data: bytes) -> tuple[int, dict[str, Any]]:
    """Return (status-code, attributes). Multi-valued attributes become lists."""
    if len(data) < 8:
        raise IppError("IPP response too short")
    status = struct.unpack(">h", data[2:4])[0]
    attrs: dict[str, Any] = {}
    pos, last, depth = 8, None, 0
    try:
        while pos < len(data):
            tag = data[pos]
            pos += 1
            if tag == TAG_END and depth == 0:
                break
            if tag < 0x10:  # new attribute group delimiter
                continue
            name_len = struct.unpack(">h", data[pos : pos + 2])[0]
            pos += 2
            name = data[pos : pos + name_len].decode(errors="replace")
            pos += name_len
            val_len = struct.unpack(">h", data[pos : pos + 2])[0]
            pos += 2
            value = _decode(tag, data[pos : pos + val_len])
            pos += val_len
            # Skip collections (begin 0x34 / end 0x37) - not needed here
            if tag == 0x34:
                depth += 1
                continue
            if tag == 0x37:
                depth -= 1
                continue
            if depth:
                continue
            if name_len == 0 and last:  # additional value of the previous attribute
                if not isinstance(attrs[last], list):
                    attrs[last] = [attrs[last]]
                attrs[last].append(value)
            elif name_len:
                last = name
                attrs[name] = value
    except struct.error as err:
        raise IppError(f"Invalid IPP response: {err}") from err
    return status, attrs


class IppClient:
    """Talks to a printer at the given IPP URL (e.g. http://1.2.3.4:631/ipp/print)."""

    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self.url = url
        parts = urlsplit(url)
        scheme = "ipps" if parts.scheme == "https" else "ipp"
        self._uri = f"{scheme}://{parts.netloc}{parts.path}"
        self._request_ids = itertools.count(1)

    def _header(self, operation: int) -> bytes:
        return (
            struct.pack(">bbhi", 2, 0, operation, next(self._request_ids))
            + bytes([TAG_OPERATION])
            + _attr(TAG_CHARSET, "attributes-charset", "utf-8")
            + _attr(TAG_LANGUAGE, "attributes-natural-language", "en")
            + _attr(TAG_URI, "printer-uri", self._uri)
            + _attr(TAG_NAME, "requesting-user-name", "HomeAssistant")
        )

    async def _request(self, payload: bytes, timeout: int = 15) -> dict[str, Any]:
        async with self._session.post(
            self.url,
            data=payload,
            headers={"Content-Type": "application/ipp"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                raise IppError(f"HTTP {resp.status}")
            body = await resp.read()
        status, attrs = parse_response(body)
        if status >= 0x0100:  # everything below 0x0100 is successful-*
            message = attrs.get("status-message") or ""
            raise IppError(f"Printer returned error 0x{status:04x} {message}".strip())
        return attrs

    async def _get_attributes(self, names: list[str]) -> dict[str, Any]:
        payload = self._header(OP_GET_PRINTER_ATTRIBUTES)
        payload += _attr(TAG_KEYWORD, "requested-attributes", names[0])
        for name in names[1:]:
            payload += _additional(TAG_KEYWORD, name)
        return await self._request(payload + bytes([TAG_END]))

    async def capabilities(self) -> PrinterCaps:
        return PrinterCaps.from_attributes(await self._get_attributes(CAPABILITY_ATTRIBUTES))

    async def status(self) -> dict[str, Any]:
        return await self._get_attributes(STATUS_ATTRIBUTES)

    async def identify(self) -> None:
        await self._request(self._header(OP_IDENTIFY_PRINTER) + bytes([TAG_END]))

    async def print_document(
        self,
        document: bytes,
        mime: str,
        job_name: str,
        copies: int,
        media: str | None,
        sides: str | None,
        color_mode: str | None,
        resolution: int | None,
    ) -> int | None:
        """Send a document. Return the job-id."""
        job = b""
        if copies > 1:
            job += _attr(TAG_INTEGER, "copies", copies)
        if media:
            job += _attr(TAG_KEYWORD, "media", media)
        if sides:
            job += _attr(TAG_KEYWORD, "sides", sides)
        if color_mode:
            job += _attr(TAG_KEYWORD, "print-color-mode", color_mode)
        if resolution:
            job += _attr(TAG_RESOLUTION, "printer-resolution", resolution)
        payload = (
            self._header(OP_PRINT_JOB)
            + _attr(TAG_NAME, "job-name", job_name)
            + _attr(TAG_MIME, "document-format", mime)
            + (bytes([TAG_JOB]) + job if job else b"")
            + bytes([TAG_END])
            + document
        )
        attrs = await self._request(payload, timeout=300)
        return attrs.get("job-id")
