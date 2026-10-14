"""Tests for the printer job history (Get-Jobs) and scan previews."""

from __future__ import annotations

import io
import struct

import aiohttp
from aioresponses import aioresponses
from PIL import Image

from helpers import ipp_int, ipp_message, ipp_operation_group, ipp_str, ipp_value
from local_printer import raster
from local_printer.ipp import IppClient, parse_groups

URL = "http://printer.local:631/ipp/print"


def ipp_datetime(name: str, year: int, month: int, day: int, hour: int, minute: int) -> bytes:
    raw = struct.pack(">HBBBBBBcBB", year, month, day, hour, minute, 0, 0, b"+", 2, 0)
    return ipp_value(0x31, name, raw)


def job_group(job_id: int, name: str, state: int) -> tuple[int, bytes]:
    return (
        0x02,
        ipp_int("job-id", job_id)
        + ipp_str("job-name", name, 0x42)
        + ipp_int("job-state", state, 0x23)
        + ipp_str("job-originating-user-name", "phone", 0x42)
        + ipp_int("job-impressions-completed", 3)
        + ipp_datetime("date-time-at-completed", 2026, 10, 14, 9, 30),
    )


class TestParseGroups:
    def test_one_group_per_job(self):
        data = ipp_message(0, [ipp_operation_group(), job_group(7, "a.pdf", 9), job_group(8, "b.pdf", 8)])
        status, groups = parse_groups(data)
        assert status == 0
        assert [tag for tag, _ in groups] == [0x01, 0x02, 0x02]
        assert [g["job-id"] for tag, g in groups if tag == 0x02] == [7, 8]

    def test_datetime_is_iso_with_offset(self):
        data = ipp_message(0, [ipp_operation_group(), job_group(1, "x", 9)])
        _, groups = parse_groups(data)
        assert groups[1][1]["date-time-at-completed"] == "2026-10-14T09:30:00+02:00"


class TestJobs:
    async def test_jobs_newest_first_with_readable_state(self):
        body = ipp_message(0, [ipp_operation_group(), job_group(7, "old.pdf", 9), job_group(9, "new.pdf", 8)])
        with aioresponses() as mock:
            mock.post(URL, body=body, content_type="application/ipp")
            async with aiohttp.ClientSession() as session:
                jobs = await IppClient(session, URL).jobs()
        assert [j["job_id"] for j in jobs] == [9, 7]
        assert jobs[0]["state"] == "aborted" and jobs[1]["state"] == "completed"
        assert jobs[1] == {
            "job_id": 7,
            "name": "old.pdf",
            "state": "completed",
            "user": "phone",
            "pages": 3,
            "created": None,
            "completed": "2026-10-14T09:30:00+02:00",
        }

    async def test_empty_history(self):
        with aioresponses() as mock:
            mock.post(URL, body=ipp_message(0, [ipp_operation_group()]), content_type="application/ipp")
            async with aiohttp.ClientSession() as session:
                assert await IppClient(session, URL).jobs() == []


class TestRenderPreview:
    def _pdf(self, size=(595, 842)) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (size[0], size[1]), "white").save(buf, "PDF", resolution=72)
        return buf.getvalue()

    def test_pdf_preview_fits_max_size(self):
        png = raster.render_preview(self._pdf(), max_size=400)
        img = Image.open(io.BytesIO(png))
        assert img.format == "PNG" and max(img.size) <= 400 and img.height > img.width

    def test_image_preview_is_downscaled(self):
        buf = io.BytesIO()
        Image.new("L", (3000, 2000), 200).save(buf, "JPEG")
        img = Image.open(io.BytesIO(raster.render_preview(buf.getvalue(), max_size=500)))
        assert img.size == (500, 333)
