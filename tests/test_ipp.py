"""Tests for the minimal IPP encoder/decoder and client."""

from __future__ import annotations

import struct

import aiohttp
from aioresponses import aioresponses
import pytest
from yarl import URL

from helpers import (
    ipp_collection,
    ipp_int,
    ipp_message,
    ipp_multi,
    ipp_operation_group,
    ipp_range,
    ipp_resolution,
    ipp_str,
    ipp_value,
    parse_ipp_request,
)
from local_printer import ipp
from local_printer.ipp import (
    OP_GET_PRINTER_ATTRIBUTES,
    OP_IDENTIFY_PRINTER,
    OP_PRINT_JOB,
    IppClient,
    IppError,
    PrinterCaps,
    parse_response,
)

URL_HTTP = "http://printer.local:631/ipp/print"

PRINTER_GROUP = 0x04


def m141w_printer_group() -> bytes:
    """Printer attributes group as an HP M141w would encode it, with collections mixed in."""
    media_col = ipp_collection(
        "media-col-default",
        [
            (
                "media-size",
                ipp_collection("", [("x-dimension", ipp_int("", 21590)), ("y-dimension", ipp_int("", 27940))]),
            ),
            ("media-source", ipp_str("", "main")),
            ("media-type", ipp_str("", "stationery")),
        ],
    )
    # 1setOf collection: an additional collection value with name-length 0
    media_col_db = ipp_collection("media-col-database", [("media-source", ipp_str("", "main"))]) + ipp_collection(
        "", [("media-source", ipp_str("", "manual"))]
    )
    return (
        ipp_str("printer-make-and-model", "HP LaserJet MFP M141w", 0x41)
        + ipp_str("printer-uuid", "urn:uuid:0a1b2c3d-0000-4000-8000-000000000141", 0x45)
        + ipp_value(0x23, "operations-supported", struct.pack(">i", 0x02))
        + b"".join(ipp_value(0x23, "", struct.pack(">i", op)) for op in (0x04, 0x0B, 0x3C))
        + ipp_multi(
            "document-format-supported",
            ["application/PCLm", "application/octet-stream", "image/pwg-raster", "image/urf"],
            0x49,
        )
        + media_col  # collection in the middle must not disturb neighbours
        + ipp_str("pwg-raster-document-type-supported", "sgray_8")
        + ipp_resolution("pwg-raster-document-resolution-supported", 300, 300)
        + ipp_resolution("", 600, 600)
        + ipp_str("pwg-raster-document-sheet-back", "normal")
        + ipp_multi("urf-supported", ["V1.5", "CP99", "W8", "OB10", "PQ4", "IS1-4", "MT1-3-5", "RS300-600"])
        + media_col_db
        + ipp_multi("media-supported", ["iso_a4_210x297mm", "na_letter_8.5x11in"])
        + ipp_str("media-default", "na_letter_8.5x11in")
        + ipp_str("sides-supported", "one-sided")
        + ipp_multi("print-color-mode-supported", ["auto", "auto-monochrome", "monochrome"])
        + ipp_range("copies-supported", 1, 99)
        + ipp_value(0x22, "color-supported", b"\x00")
        + ipp_int("printer-state", 3, 0x23)
        + ipp_str("marker-names", "Black Cartridge HP W1500A", 0x42)
        + ipp_int("marker-levels", 59)
        + ipp_str("marker-types", "toner")
    )


@pytest.fixture
def m141w_response() -> bytes:
    return ipp_message(0x0000, [ipp_operation_group(), (PRINTER_GROUP, m141w_printer_group())])


# ------------------------------------------------------------ parse_response


class TestParseResponse:
    def test_status_and_scalar_types(self, m141w_response):
        status, attrs = parse_response(m141w_response)
        assert status == 0
        assert attrs["attributes-charset"] == "utf-8"
        assert attrs["printer-make-and-model"] == "HP LaserJet MFP M141w"
        assert attrs["printer-state"] == 3
        assert attrs["marker-levels"] == 59
        assert attrs["marker-names"] == "Black Cartridge HP W1500A"
        assert attrs["color-supported"] is False

    def test_multi_valued_become_lists(self, m141w_response):
        _, attrs = parse_response(m141w_response)
        assert attrs["operations-supported"] == [0x02, 0x04, 0x0B, 0x3C]
        assert attrs["document-format-supported"] == [
            "application/PCLm",
            "application/octet-stream",
            "image/pwg-raster",
            "image/urf",
        ]
        assert attrs["media-supported"] == ["iso_a4_210x297mm", "na_letter_8.5x11in"]

    def test_resolution_and_range(self, m141w_response):
        _, attrs = parse_response(m141w_response)
        assert attrs["pwg-raster-document-resolution-supported"] == [(300, 300, 3), (600, 600, 3)]
        assert attrs["copies-supported"] == (1, 99)

    def test_collections_are_skipped_cleanly(self, m141w_response):
        _, attrs = parse_response(m141w_response)
        assert "media-col-default" not in attrs
        assert "media-col-database" not in attrs
        # Member names/values must not leak into top-level attributes
        assert not {"media-size", "x-dimension", "media-source", "media-type", ""} & attrs.keys()
        # Attributes right before the collections must not get collection values appended
        assert attrs["document-format-supported"][-1] == "image/urf"
        assert attrs["urf-supported"][-1] == "RS300-600"
        # ...and attributes right after them are intact
        assert attrs["pwg-raster-document-type-supported"] == "sgray_8"
        assert attrs["media-supported"] == ["iso_a4_210x297mm", "na_letter_8.5x11in"]

    def test_end_tag_stops_parsing(self):
        data = ipp_message(0, [ipp_operation_group()], trailer=b"\x44garbage-after-end")
        _, attrs = parse_response(data)
        assert set(attrs) == {"attributes-charset", "attributes-natural-language"}

    def test_too_short(self):
        with pytest.raises(IppError):
            parse_response(b"\x02\x00\x00")

    @pytest.mark.parametrize("cut", [9, 10, 11, 60, 61])
    def test_truncated_never_raises_foreign_exception(self, m141w_response, cut):
        """Truncated input may parse partially or raise IppError, but nothing else."""
        try:
            parse_response(m141w_response[:cut])
        except IppError:
            pass

    def test_full_m141w_to_caps(self, m141w_response, m141w_attrs):
        _, attrs = parse_response(m141w_response)
        parsed = PrinterCaps.from_attributes(attrs)
        expected = PrinterCaps.from_attributes(m141w_attrs)
        expected.firmware = None  # not encoded in the binary fixture
        parsed.formats.sort()
        expected.formats.sort()
        assert parsed == expected


# ------------------------------------------------------------- PrinterCaps


class TestPrinterCaps:
    def test_m141w(self, m141w_printer):
        caps = m141w_printer
        assert caps.make_and_model == "HP LaserJet MFP M141w"
        assert caps.uuid == "0a1b2c3d-0000-4000-8000-000000000141"
        assert caps.supports_color is False
        assert caps.max_copies == 99
        assert caps.can_identify is True
        assert caps.pwg_resolutions == [300, 600]
        assert caps.pwg_types == ["sgray_8"]
        assert caps.pwg_sheet_back == "normal"
        assert caps.sides == ["one-sided"]
        assert caps.media_default == "na_letter_8.5x11in"
        assert "image/pwg-raster" in caps.formats

    def test_single_resolution_tuple(self):
        caps = PrinterCaps.from_attributes({"pwg-raster-document-resolution-supported": (600, 600, 3)})
        assert caps.pwg_resolutions == [600]

    def test_defaults_when_missing(self):
        caps = PrinterCaps.from_attributes({})
        assert caps.max_copies == 1
        assert caps.can_identify is False
        assert caps.sides == ["one-sided"]
        assert caps.pwg_sheet_back == "normal"
        assert caps.uuid is None

    @pytest.mark.parametrize(
        "attrs",
        [
            {"print-color-mode-supported": ["monochrome", "color"]},
            {"pwg-raster-document-type-supported": ["sgray_8", "srgb_8"]},
            {"urf-supported": ["W8", "SRGB24"]},
        ],
    )
    def test_supports_color(self, attrs):
        assert PrinterCaps.from_attributes(attrs).supports_color


# ------------------------------------------------------------ encoders


def test_attr_and_additional_encoding():
    assert ipp._attr(ipp.TAG_KEYWORD, "sides", "one-sided") == ipp_str("sides", "one-sided")
    assert ipp._attr(ipp.TAG_INTEGER, "copies", 7) == ipp_int("copies", 7)
    assert ipp._attr(ipp.TAG_RESOLUTION, "printer-resolution", 600) == ipp_resolution("printer-resolution", 600, 600)
    assert ipp._attr(ipp.TAG_BOOLEAN, "b", True) == ipp_value(0x22, "b", b"\x01")
    assert ipp._additional(ipp.TAG_KEYWORD, "media-default") == ipp_str("", "media-default")


@pytest.mark.parametrize(
    "tag,raw,expected",
    [
        (0x21, struct.pack(">i", -5), -5),
        (0x23, struct.pack(">i", 4), 4),
        (0x22, b"\x01", True),
        (0x32, struct.pack(">iib", 300, 600, 3), (300, 600, 3)),
        (0x33, struct.pack(">ii", 1, 99), (1, 99)),
        (0x44, "naïve".encode(), "naïve"),
        (0x30, b"\x01\x02", b"\x01\x02"),  # octetString stays raw
    ],
)
def test_decode(tag, raw, expected):
    assert ipp._decode(tag, raw) == expected


# ------------------------------------------------------------- client


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
def mocked():
    with aioresponses() as m:
        yield m


def sent(mocked: aioresponses, url: str = URL_HTTP) -> list[dict]:
    return [parse_ipp_request(c.kwargs["data"]) for c in mocked.requests[("POST", URL(url))]]


def ok_response(extra: bytes = b"") -> bytes:
    return ipp_message(0x0000, [ipp_operation_group(extra)])


def attr_map(request: dict) -> dict[str, tuple[int, int, bytes]]:
    return {name: (group, tag, raw) for group, tag, name, raw in request["attrs"] if name}


class TestHeader:
    def test_header_fields(self, session):
        client = IppClient(session, URL_HTTP)
        req = parse_ipp_request(client._header(OP_GET_PRINTER_ATTRIBUTES) + b"\x03")
        assert req["version"] == (2, 0)
        assert req["operation"] == OP_GET_PRINTER_ATTRIBUTES
        assert req["request_id"] == 1
        names = [name for _, _, name, _ in req["attrs"]]
        # charset and natural-language must be the first two operation attributes
        assert names[:2] == ["attributes-charset", "attributes-natural-language"]
        a = attr_map(req)
        assert a["printer-uri"] == (0x01, 0x45, b"ipp://printer.local:631/ipp/print")
        assert a["requesting-user-name"][1] == 0x42

    def test_request_ids_increment(self, session):
        client = IppClient(session, URL_HTTP)
        ids = [parse_ipp_request(client._header(OP_IDENTIFY_PRINTER) + b"\x03")["request_id"] for _ in range(3)]
        assert ids == [1, 2, 3]

    def test_https_uses_ipps(self, session):
        client = IppClient(session, "https://10.0.0.7:443/ipp/print")
        a = attr_map(parse_ipp_request(client._header(OP_IDENTIFY_PRINTER) + b"\x03"))
        assert a["printer-uri"][2] == b"ipps://10.0.0.7:443/ipp/print"


async def test_capabilities_request_and_parse(session, mocked, m141w_response):
    mocked.post(URL_HTTP, status=200, body=m141w_response)
    caps = await IppClient(session, URL_HTTP).capabilities()
    assert caps.max_copies == 99 and caps.can_identify

    (req,) = sent(mocked)
    assert req["operation"] == OP_GET_PRINTER_ATTRIBUTES
    requested = []
    for _, tag, name, raw in req["attrs"]:
        if name == "requested-attributes" or (not name and requested):
            assert tag == 0x44
            requested.append(raw.decode())
    assert requested == ipp.CAPABILITY_ATTRIBUTES
    assert mocked.requests[("POST", URL(URL_HTTP))][0].kwargs["headers"]["Content-Type"] == "application/ipp"


async def test_status_returns_attrs(session, mocked, m141w_response):
    mocked.post(URL_HTTP, status=200, body=m141w_response)
    attrs = await IppClient(session, URL_HTTP).status()
    assert attrs["marker-levels"] == 59
    assert attrs["marker-types"] == "toner"


async def test_identify(session, mocked):
    mocked.post(URL_HTTP, status=200, body=ok_response())
    await IppClient(session, URL_HTTP).identify()
    (req,) = sent(mocked)
    assert req["operation"] == OP_IDENTIFY_PRINTER
    assert req["document"] == b""


@pytest.mark.parametrize("status", [0x0400, 0x040A, 0x0500])
async def test_ipp_error_status_raises(session, mocked, status):
    body = ipp_message(status, [ipp_operation_group(ipp_str("status-message", "client-error-bad-request", 0x41))])
    mocked.post(URL_HTTP, status=200, body=body)
    with pytest.raises(IppError) as err:
        await IppClient(session, URL_HTTP).identify()
    assert f"{status:04x}" in str(err.value)


async def test_successful_with_ignored_attributes_status_ok(session, mocked):
    mocked.post(URL_HTTP, status=200, body=ipp_message(0x0001, [ipp_operation_group()]))
    await IppClient(session, URL_HTTP).identify()  # 0x0001 is still successful-*


async def test_http_error_raises(session, mocked):
    mocked.post(URL_HTTP, status=500)
    with pytest.raises(IppError):
        await IppClient(session, URL_HTTP).identify()


class TestPrintDocument:
    DOC = b"RaS2" + b"\x00\xff" * 50

    async def print_(self, session, mocked, **kwargs) -> tuple[int | None, dict]:
        mocked.post(URL_HTTP, status=200, body=ok_response(ipp_int("job-id", 42)))
        params = dict(
            document=self.DOC,
            mime="image/pwg-raster",
            job_name="test.pdf",
            copies=1,
            media=None,
            sides=None,
            color_mode=None,
            resolution=None,
        )
        params.update(kwargs)
        job_id = await IppClient(session, URL_HTTP).print_document(**params)
        (req,) = sent(mocked)
        return job_id, req

    async def test_minimal_job(self, session, mocked):
        job_id, req = await self.print_(session, mocked)
        assert job_id == 42
        assert req["operation"] == OP_PRINT_JOB
        a = attr_map(req)
        assert a["document-format"] == (0x01, 0x49, b"image/pwg-raster")
        assert a["job-name"] == (0x01, 0x42, b"test.pdf")
        for absent in ("copies", "media", "sides", "print-color-mode", "printer-resolution"):
            assert absent not in a
        assert all(group == 0x01 for group, _, _, _ in req["attrs"])  # no empty job group
        assert req["document"] == self.DOC

    async def test_all_job_attributes(self, session, mocked):
        _, req = await self.print_(
            session,
            mocked,
            copies=3,
            media="iso_a4_210x297mm",
            sides="two-sided-long-edge",
            color_mode="monochrome",
            resolution=600,
        )
        a = attr_map(req)
        assert a["copies"] == (0x02, 0x21, struct.pack(">i", 3))
        assert a["media"] == (0x02, 0x44, b"iso_a4_210x297mm")
        assert a["sides"] == (0x02, 0x44, b"two-sided-long-edge")
        assert a["print-color-mode"] == (0x02, 0x44, b"monochrome")
        assert a["printer-resolution"] == (0x02, 0x32, struct.pack(">iib", 600, 600, 3))
        assert req["document"] == self.DOC

    async def test_sides_only_when_given(self, session, mocked):
        _, req = await self.print_(session, mocked, sides="two-sided-long-edge")
        a = attr_map(req)
        assert set(a) & {"copies", "media", "print-color-mode"} == set()
        assert a["sides"][0] == 0x02

    async def test_document_after_end_tag(self, session, mocked):
        _, req = await self.print_(session, mocked, document=b"\x03\x03payload")
        data = mocked.requests[("POST", URL(URL_HTTP))][0].kwargs["data"]
        assert data.endswith(b"\x03\x03\x03payload")  # end-of-attributes tag then raw bytes
        assert req["document"] == b"\x03\x03payload"

    async def test_print_error(self, session, mocked):
        mocked.post(URL_HTTP, status=200, body=ipp_message(0x040B, [ipp_operation_group()]))
        with pytest.raises(IppError):
            await IppClient(session, URL_HTTP).print_document(self.DOC, "image/urf", "x", 1, None, None, None, None)
