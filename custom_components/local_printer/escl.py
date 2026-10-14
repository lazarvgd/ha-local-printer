"""eSCL (AirScan) client: capabilities, status and scanning from the platen or ADF."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import aiohttp

NS_SCAN = "http://schemas.hp.com/imaging/escl/2011/05/03"  # standard eSCL namespace (all vendors)
NS_PWG = "http://www.pwg.org/schemas/2010/12/sm"
STANDARD_RESOLUTIONS = (75, 100, 150, 200, 300, 400, 600, 1200)

SCAN_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ns_scan}" xmlns:pwg="{ns_pwg}">
  <pwg:Version>{version}</pwg:Version>
  <scan:Intent>Document</scan:Intent>
  <pwg:ScanRegions><pwg:ScanRegion>
    <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
    <pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>
    <pwg:Width>{width}</pwg:Width><pwg:Height>{height}</pwg:Height>
  </pwg:ScanRegion></pwg:ScanRegions>
  <pwg:InputSource>{source}</pwg:InputSource>{duplex}
  <scan:ColorMode>{color}</scan:ColorMode>
  <scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>
  <pwg:DocumentFormat>{mime}</pwg:DocumentFormat>
  <scan:DocumentFormatExt>{mime}</scan:DocumentFormatExt>
</scan:ScanSettings>"""


class EsclError(Exception):
    """Error while communicating with the scanner."""


@dataclass
class SourceCaps:
    """Capabilities of one input source (platen, ADF, duplex ADF)."""

    color_modes: list[str] = field(default_factory=list)
    resolutions: list[int] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    max_width: int = 2550  # 1/300 inch
    max_height: int = 3508


@dataclass
class ScannerCaps:
    version: str = "2.0"
    make_and_model: str | None = None
    uuid: str | None = None
    sources: dict[str, SourceCaps] = field(default_factory=dict)  # keys: platen, adf, adf_duplex


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el: ET.Element, name: str) -> ET.Element | None:
    """Find an element by local name, ignoring namespaces."""
    return next((e for e in el.iter() if _local(e.tag) == name), None)


def _texts(el: ET.Element, name: str) -> list[str]:
    return [e.text.strip() for e in el.iter() if _local(e.tag) == name and e.text]


def _int(el: ET.Element | None, name: str, default: int) -> int:
    found = _find(el, name) if el is not None else None
    try:
        return int(found.text) if found is not None and found.text else default
    except ValueError:
        return default


def _parse_input_caps(el: ET.Element) -> SourceCaps:
    caps = SourceCaps(
        color_modes=list(dict.fromkeys(_texts(el, "ColorMode"))),
        formats=list(dict.fromkeys(_texts(el, "DocumentFormatExt") + _texts(el, "DocumentFormat"))),
        max_width=_int(el, "MaxWidth", 2550),
        max_height=_int(el, "MaxHeight", 3508),
    )
    resolutions = set()
    for res in (e for e in el.iter() if _local(e.tag) == "DiscreteResolution"):
        x = _int(res, "XResolution", 0)
        if x:
            resolutions.add(x)
    for rng in (e for e in el.iter() if _local(e.tag) == "XResolutionRange"):
        lo, hi = _int(rng, "Min", 0), _int(rng, "Max", 0)
        step = max(_int(rng, "Step", 1), 1)
        in_range = [r for r in STANDARD_RESOLUTIONS if lo <= r <= hi and (r - lo) % step == 0]
        resolutions.update(in_range or [r for r in (lo, hi) if r])
    caps.resolutions = sorted(resolutions) or [300]
    return caps


def parse_capabilities(xml_text: str) -> ScannerCaps:
    root = ET.fromstring(xml_text)
    caps = ScannerCaps(
        version=(_find(root, "Version").text or "2.0") if _find(root, "Version") is not None else "2.0",
        make_and_model=getattr(_find(root, "MakeAndModel"), "text", None),
        uuid=getattr(_find(root, "UUID"), "text", None),
    )
    if (platen := _find(root, "PlatenInputCaps")) is not None:
        caps.sources["platen"] = _parse_input_caps(platen)
    if (adf := _find(root, "AdfSimplexInputCaps")) is not None:
        caps.sources["adf"] = _parse_input_caps(adf)
    if (adf_duplex := _find(root, "AdfDuplexInputCaps")) is not None:
        caps.sources["adf_duplex"] = _parse_input_caps(adf_duplex)
    return caps


class EsclClient:
    """Talks to a scanner at the given eSCL base URL (e.g. http://1.2.3.4:8080/eSCL)."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        parts = urlsplit(self.base_url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def capabilities(self) -> ScannerCaps:
        async with self._session.get(
            f"{self.base_url}/ScannerCapabilities", timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                raise EsclError(f"ScannerCapabilities: HTTP {resp.status}")
            text = await resp.text()
        try:
            return parse_capabilities(text)
        except ET.ParseError as err:
            raise EsclError(f"Invalid scanner response: {err}") from err

    async def status(self) -> tuple[str, str | None]:
        """Return (scanner state, ADF state or None)."""
        async with self._session.get(
            f"{self.base_url}/ScannerStatus", timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            text = await resp.text()
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return "Unknown", None
        state = _find(root, "State")
        adf = _find(root, "AdfState")
        return (state.text if state is not None else "Unknown"), (adf.text if adf is not None else None)

    def _job_url(self, location: str) -> str:
        # Some printers (e.g. HP) return a Location with the wrong port - keep only the path
        path = urlsplit(location).path or location
        if not path.startswith("/"):
            path = f"{urlsplit(self.base_url).path}/ScanJobs/{path}"
        return f"{self._origin}{path}"

    async def scan(
        self,
        caps: ScannerCaps,
        source: str,
        color: str,
        dpi: int,
        mime: str,
        width: int,
        height: int,
    ) -> list[bytes]:
        """Scan and return a list of documents (one for the platen, one or more for the ADF)."""
        if self._lock.locked():
            raise EsclError("Scanner is busy with another scan")
        async with self._lock:
            body = SCAN_SETTINGS.format(
                ns_scan=NS_SCAN,
                ns_pwg=NS_PWG,
                version=caps.version,
                width=width,
                height=height,
                source="Platen" if source == "platen" else "Feeder",
                duplex="\n  <scan:Duplex>true</scan:Duplex>" if source == "adf_duplex" else "",
                color=color,
                dpi=dpi,
                mime=mime,
            )
            async with self._session.post(
                f"{self.base_url}/ScanJobs",
                data=body.encode(),
                headers={"Content-Type": "text/xml"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                location = resp.headers.get("Location")
                if resp.status == 503:
                    raise EsclError("Scanner is busy")
                if resp.status == 409:
                    raise EsclError("Scanner rejected the job - check that the ADF has paper")
                if resp.status not in (200, 201) or not location:
                    raise EsclError(f"Scanner rejected the job (HTTP {resp.status})")
            job_url = self._job_url(location)

            documents: list[bytes] = []
            try:
                while True:
                    async with self._session.get(
                        f"{job_url}/NextDocument", timeout=aiohttp.ClientTimeout(total=300)
                    ) as resp:
                        if resp.status in (404, 410) and documents:
                            break  # no more pages
                        if resp.status != 200:
                            raise EsclError(f"Downloading the scan failed (HTTP {resp.status})")
                        data = await resp.read()
                    if not data:
                        break
                    documents.append(data)
                    if source == "platen":
                        break  # the platen always yields exactly one document
            except (aiohttp.ClientError, asyncio.TimeoutError, EsclError) as err:
                await self._cancel(job_url)
                if documents:
                    return documents  # return what was already received
                raise EsclError(f"Downloading the scan failed: {err}") from err
            if not documents:
                await self._cancel(job_url)
                raise EsclError("Scanner returned an empty document")
            return documents

    async def _cancel(self, job_url: str) -> None:
        """Delete the job so the scanner does not get stuck."""
        try:
            async with self._session.delete(job_url, timeout=aiohttp.ClientTimeout(total=10)):
                pass
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
