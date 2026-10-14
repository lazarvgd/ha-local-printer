"""Find eSCL and IPP endpoints on a host (no HA dependency, unit testable)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp

from .const import ESCL_CANDIDATES, IPP_CANDIDATES
from .escl import EsclClient, EsclError, ScannerCaps
from .ipp import IppClient, IppError, PrinterCaps

PROBE_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, EsclError, IppError, ValueError)


@dataclass
class ProbeResult:
    escl_url: str | None = None
    ipp_url: str | None = None
    scanner: ScannerCaps | None = None
    printer: PrinterCaps | None = None

    @property
    def found(self) -> bool:
        return bool(self.escl_url or self.ipp_url)

    @property
    def uuid(self) -> str | None:
        uuid = (self.scanner and self.scanner.uuid) or (self.printer and self.printer.uuid)
        return uuid.lower() if uuid else None

    @property
    def model(self) -> str | None:
        return (self.printer and self.printer.make_and_model) or (self.scanner and self.scanner.make_and_model)


def build_url(scheme: str, host: str, port: int, path: str) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 literal
    default_port = {"http": 80, "https": 443}[scheme]
    netloc = host if port == default_port else f"{host}:{port}"
    return f"{scheme}://{netloc}/{path.strip('/')}"


async def probe_escl(session: aiohttp.ClientSession, url: str) -> ScannerCaps | None:
    try:
        caps = await EsclClient(session, url).capabilities()
    except PROBE_ERRORS:
        return None
    return caps if caps.sources else None


async def probe_ipp(session: aiohttp.ClientSession, url: str) -> PrinterCaps | None:
    try:
        return await IppClient(session, url).capabilities()
    except PROBE_ERRORS:
        return None


async def probe_host(
    session: aiohttp.ClientSession,
    host: str,
    escl_url: str | None = None,
    ipp_url: str | None = None,
) -> ProbeResult:
    """Use the given URLs if they work, otherwise try the well-known locations."""
    result = ProbeResult()
    escl_urls = [escl_url] if escl_url else [build_url(s, host, p, path) for s, p, path in ESCL_CANDIDATES]
    ipp_urls = [ipp_url] if ipp_url else [build_url(s, host, p, path) for s, p, path in IPP_CANDIDATES]
    if escl_url:
        escl_urls += [build_url(s, host, p, path) for s, p, path in ESCL_CANDIDATES]
    if ipp_url:
        ipp_urls += [build_url(s, host, p, path) for s, p, path in IPP_CANDIDATES]

    for url in dict.fromkeys(escl_urls):
        if caps := await probe_escl(session, url):
            result.escl_url, result.scanner = url, caps
            break
    for url in dict.fromkeys(ipp_urls):
        if caps := await probe_ipp(session, url):
            result.ipp_url, result.printer = url, caps
            break
    return result


def replace_host(url: str, host: str) -> str:
    """Point a stored endpoint URL at the device's current address (it may change via DHCP)."""
    parts = urlsplit(url)
    port = parts.port or {"http": 80, "https": 443}[parts.scheme]
    return build_url(parts.scheme, host, port, parts.path)
