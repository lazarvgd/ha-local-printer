"""Tests for the eSCL scanner client (capability parsing and scan job flow)."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import aiohttp
from aioresponses import CallbackResult, aioresponses
import pytest
from yarl import URL

from local_printer.escl import (
    STANDARD_RESOLUTIONS,
    EsclClient,
    EsclError,
    ScannerCaps,
    SourceCaps,
    parse_capabilities,
)

BASE = "http://printer.local:8080/eSCL"
JOB_ID = "8d4e3f2a-1111-2222-3333-444455556666"
# HP returns the job Location with port 80 even though eSCL lives on 8080
HP_LOCATION = f"http://printer.local:80/eSCL/ScanJobs/{JOB_ID}"
JOB_URL = f"{BASE}/ScanJobs/{JOB_ID}"


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def settings_of(mocked: aioresponses) -> dict[str, str]:
    """Parse the ScanSettings XML that was POSTed, keyed by local element name."""
    calls = mocked.requests[("POST", URL(f"{BASE}/ScanJobs"))]
    assert len(calls) == 1
    root = ET.fromstring(calls[0].kwargs["data"])
    assert local(root.tag) == "ScanSettings"
    return {local(e.tag): (e.text or "").strip() for e in root.iter()}


def calls(mocked: aioresponses, method: str, url: str) -> int:
    return len(mocked.requests.get((method, URL(url)), []))


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
def client(session) -> EsclClient:
    return EsclClient(session, BASE + "/")


@pytest.fixture
def mocked():
    with aioresponses() as m:
        yield m


@pytest.fixture
def m141w_caps(m141w_scanner_xml) -> ScannerCaps:
    return parse_capabilities(m141w_scanner_xml)


# ------------------------------------------------------------- capabilities


class TestParseRealM141w:
    def test_identity(self, m141w_caps):
        assert m141w_caps.version == "2.63"
        assert m141w_caps.make_and_model == "HP LaserJet MFP M141w"
        assert m141w_caps.uuid == "0a1b2c3d-0000-4000-8000-000000000141"

    def test_platen_only(self, m141w_caps):
        assert list(m141w_caps.sources) == ["platen"]

    def test_platen_caps(self, m141w_caps):
        platen = m141w_caps.sources["platen"]
        assert platen.resolutions == [200, 300, 600]
        assert platen.color_modes == ["RGB24", "Grayscale8"]
        assert set(platen.formats) == {"image/jpeg", "application/pdf", "application/octet-stream"}
        assert len(platen.formats) == 3  # Ext + plain DocumentFormat are de-duplicated
        assert (platen.max_width, platen.max_height) == (2550, 3508)


class TestParseSyntheticAdf:
    @pytest.fixture
    def caps(self, adf_scanner_xml) -> ScannerCaps:
        return parse_capabilities(adf_scanner_xml)

    def test_namespace_agnostic_identity(self, caps):
        assert caps.version == "2.6"
        assert caps.make_and_model == "Example ADF Scanner 9000"
        assert caps.uuid == "11111111-2222-3333-4444-555555555555"

    def test_three_sources(self, caps):
        assert set(caps.sources) == {"platen", "adf", "adf_duplex"}

    def test_range_maps_to_standard_resolutions(self, caps):
        assert caps.sources["platen"].resolutions == [r for r in STANDARD_RESOLUTIONS if 75 <= r <= 600]
        assert caps.sources["adf"].resolutions == [100, 150, 200, 300]

    def test_range_honors_step(self, caps):
        assert caps.sources["adf_duplex"].resolutions == [150, 300]

    def test_formats_and_color_modes(self, caps):
        for src in caps.sources.values():
            assert src.formats == ["image/jpeg"]
        assert caps.sources["platen"].color_modes == ["BlackAndWhite1", "Grayscale8", "RGB24"]
        assert caps.sources["adf"].color_modes == ["BlackAndWhite1", "Grayscale8"]
        assert caps.sources["adf_duplex"].color_modes == ["BlackAndWhite1"]

    def test_per_source_max_size(self, caps):
        assert (caps.sources["adf"].max_width, caps.sources["adf"].max_height) == (2550, 4200)
        assert (caps.sources["adf_duplex"].max_width, caps.sources["adf_duplex"].max_height) == (2480, 3508)

    def test_default_namespace(self):
        xml = (
            '<ScannerCapabilities xmlns="http://schemas.hp.com/imaging/escl/2011/05/03">'
            "<PlatenInputCaps><MaxWidth>100</MaxWidth></PlatenInputCaps></ScannerCapabilities>"
        )
        caps = parse_capabilities(xml)
        assert caps.version == "2.0"  # missing Version -> default
        assert caps.sources["platen"].max_width == 100
        assert caps.sources["platen"].max_height == 3508  # default
        assert caps.sources["platen"].resolutions == [300]  # nothing advertised -> fallback


async def test_capabilities_http_and_parse_errors(client, mocked, m141w_scanner_xml):
    mocked.get(f"{BASE}/ScannerCapabilities", status=200, body=m141w_scanner_xml)
    caps = await client.capabilities()
    assert caps.make_and_model == "HP LaserJet MFP M141w"

    mocked.get(f"{BASE}/ScannerCapabilities", status=500)
    with pytest.raises(EsclError):
        await client.capabilities()

    mocked.get(f"{BASE}/ScannerCapabilities", status=200, body="<not-xml")
    with pytest.raises(EsclError):
        await client.capabilities()


# ------------------------------------------------------------------ job URL


class TestJobUrl:
    def test_wrong_port_is_replaced_by_base_origin(self, client):
        assert client._job_url(HP_LOCATION) == JOB_URL

    def test_absolute_path(self, client):
        assert client._job_url(f"/eSCL/ScanJobs/{JOB_ID}") == JOB_URL

    def test_relative_id(self, client):
        assert client._job_url(JOB_ID) == JOB_URL

    def test_https_base(self, session):
        c = EsclClient(session, "https://10.0.0.5/eSCL")
        assert c._job_url("http://10.0.0.5:80/eSCL/ScanJobs/x") == "https://10.0.0.5/eSCL/ScanJobs/x"


# ------------------------------------------------------------------- status

STATUS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                    xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.63</pwg:Version>
  <pwg:State>{state}</pwg:State>
  {adf}
  <scan:Jobs><scan:JobInfo><pwg:JobState>Completed</pwg:JobState></scan:JobInfo></scan:Jobs>
</scan:ScannerStatus>"""


async def test_status_with_adf(client, mocked):
    mocked.get(
        f"{BASE}/ScannerStatus",
        body=STATUS_XML.format(state="Processing", adf="<scan:AdfState>ScannerAdfLoaded</scan:AdfState>"),
    )
    assert await client.status() == ("Processing", "ScannerAdfLoaded")


async def test_status_platen_only(client, mocked):
    mocked.get(f"{BASE}/ScannerStatus", body=STATUS_XML.format(state="Idle", adf=""))
    assert await client.status() == ("Idle", None)


async def test_status_garbage(client, mocked):
    mocked.get(f"{BASE}/ScannerStatus", body="oops")
    assert await client.status() == ("Unknown", None)


# --------------------------------------------------------------------- scan


def mock_job(mocked: aioresponses, status: int = 201, location: str | None = HP_LOCATION) -> None:
    headers = {"Location": location} if location else {}
    mocked.post(f"{BASE}/ScanJobs", status=status, headers=headers)


async def test_scan_platen_single_document(client, mocked, m141w_caps):
    mock_job(mocked)
    mocked.get(f"{JOB_URL}/NextDocument", status=200, body=b"%PDF-page1")
    # A second page would be wrong for the platen - must never be requested
    mocked.get(f"{JOB_URL}/NextDocument", status=200, body=b"%PDF-page2")

    docs = await client.scan(m141w_caps, "platen", "RGB24", 300, "application/pdf", 2480, 3508)

    assert docs == [b"%PDF-page1"]
    assert calls(mocked, "GET", f"{JOB_URL}/NextDocument") == 1
    assert calls(mocked, "DELETE", JOB_URL) == 0
    s = settings_of(mocked)
    assert s["InputSource"] == "Platen"
    assert "Duplex" not in s
    assert s["XResolution"] == s["YResolution"] == "300"
    assert s["ColorMode"] == "RGB24"
    assert s["Width"] == "2480" and s["Height"] == "3508"
    assert s["XOffset"] == s["YOffset"] == "0"
    assert s["DocumentFormat"] == s["DocumentFormatExt"] == "application/pdf"
    assert s["Version"] == "2.63"
    assert not client.busy


@pytest.mark.parametrize("source,duplex", [("adf", False), ("adf_duplex", True)])
async def test_scan_adf_loops_until_404(client, mocked, adf_scanner_xml, source, duplex):
    caps = parse_capabilities(adf_scanner_xml)
    mock_job(mocked)
    for body in (b"jpg-1", b"jpg-2", b"jpg-3"):
        mocked.get(f"{JOB_URL}/NextDocument", status=200, body=body)
    mocked.get(f"{JOB_URL}/NextDocument", status=404)

    docs = await client.scan(caps, source, "Grayscale8", 200, "image/jpeg", 2480, 3508)

    assert docs == [b"jpg-1", b"jpg-2", b"jpg-3"]
    assert calls(mocked, "GET", f"{JOB_URL}/NextDocument") == 4
    s = settings_of(mocked)
    assert s["InputSource"] == "Feeder"
    assert ("Duplex" in s) is duplex
    if duplex:
        assert s["Duplex"] == "true"
    assert s["ColorMode"] == "Grayscale8"
    assert s["XResolution"] == "200"


async def test_scan_503_busy(client, mocked, m141w_caps):
    mock_job(mocked, status=503, location=None)
    with pytest.raises(EsclError, match="(?i)busy"):
        await client.scan(m141w_caps, "platen", "RGB24", 300, "image/jpeg", 2480, 3508)
    assert not client.busy


@pytest.mark.parametrize("status,location", [(409, None), (400, None), (201, None)])
async def test_scan_job_rejected(client, mocked, m141w_caps, status, location):
    mock_job(mocked, status=status, location=location)
    with pytest.raises(EsclError):
        await client.scan(m141w_caps, "platen", "RGB24", 300, "image/jpeg", 2480, 3508)


async def test_scan_download_http_error_cancels_job(client, mocked, m141w_caps):
    mock_job(mocked)
    mocked.get(f"{JOB_URL}/NextDocument", status=500)
    mocked.delete(JOB_URL, status=200)
    with pytest.raises(EsclError):
        await client.scan(m141w_caps, "platen", "RGB24", 300, "image/jpeg", 2480, 3508)
    assert calls(mocked, "DELETE", JOB_URL) == 1
    assert not client.busy


async def test_scan_download_connection_error_cancels_job(client, mocked, m141w_caps):
    mock_job(mocked)
    mocked.get(f"{JOB_URL}/NextDocument", exception=aiohttp.ClientConnectionError("reset"))
    mocked.delete(JOB_URL, exception=aiohttp.ClientConnectionError("still down"))  # cancel must not raise
    with pytest.raises(EsclError):
        await client.scan(m141w_caps, "platen", "RGB24", 300, "image/jpeg", 2480, 3508)
    assert calls(mocked, "DELETE", JOB_URL) == 1


async def test_scan_first_adf_page_404_is_error(client, mocked, adf_scanner_xml):
    caps = parse_capabilities(adf_scanner_xml)
    mock_job(mocked)
    mocked.get(f"{JOB_URL}/NextDocument", status=404)
    mocked.delete(JOB_URL)
    with pytest.raises(EsclError):
        await client.scan(caps, "adf", "Grayscale8", 200, "image/jpeg", 2480, 3508)
    assert calls(mocked, "DELETE", JOB_URL) == 1


async def test_scan_adf_failure_mid_job_returns_received_pages(client, mocked, adf_scanner_xml):
    caps = parse_capabilities(adf_scanner_xml)
    mock_job(mocked)
    mocked.get(f"{JOB_URL}/NextDocument", status=200, body=b"jpg-1")
    mocked.get(f"{JOB_URL}/NextDocument", status=500)
    mocked.delete(JOB_URL)
    docs = await client.scan(caps, "adf", "Grayscale8", 200, "image/jpeg", 2480, 3508)
    assert docs == [b"jpg-1"]
    assert calls(mocked, "DELETE", JOB_URL) == 1


async def test_scan_empty_document_is_error(client, mocked, m141w_caps):
    mock_job(mocked)
    mocked.get(f"{JOB_URL}/NextDocument", status=200, body=b"")
    mocked.delete(JOB_URL)
    with pytest.raises(EsclError):
        await client.scan(m141w_caps, "platen", "RGB24", 300, "image/jpeg", 2480, 3508)
    assert calls(mocked, "DELETE", JOB_URL) == 1


async def test_concurrent_scan_is_rejected(client, mocked, m141w_caps):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_download(url, **kwargs):
        started.set()
        await release.wait()
        return CallbackResult(status=200, body=b"page")

    mock_job(mocked)
    mocked.get(f"{JOB_URL}/NextDocument", callback=slow_download)

    first = asyncio.create_task(client.scan(m141w_caps, "platen", "RGB24", 300, "image/jpeg", 2480, 3508))
    await asyncio.wait_for(started.wait(), 5)
    assert client.busy
    with pytest.raises(EsclError, match="(?i)busy"):
        await client.scan(m141w_caps, "platen", "RGB24", 300, "image/jpeg", 2480, 3508)
    release.set()
    assert await first == [b"page"]
    assert not client.busy
    # The rejected call must not have created a second job on the scanner
    assert calls(mocked, "POST", f"{BASE}/ScanJobs") == 1


def test_source_caps_defaults():
    assert SourceCaps().max_width == 2550 and SourceCaps().max_height == 3508
