"""Tests for document conversion: page loading, fitting, PWG/URF encoding and PDF helpers."""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw
import pypdfium2 as pdfium
import pytest

from helpers import decode_pwg, decode_urf
from local_printer import raster
from local_printer.const import PAPER_SIZES

A4_MM = PAPER_SIZES["a4"]["mm"]
A4_MEDIA = PAPER_SIZES["a4"]["ipp"]


# ------------------------------------------------------------ test images


def gray_text_page(width: int = 400, height: int = 300, seed: int = 1) -> Image.Image:
    """White page with text, solid bars, a gradient and a band of random noise."""
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "Hello PWG raster 0123456789", fill=0)
    draw.rectangle((10, 40, width - 10, 60), fill=0)  # long run of black
    draw.rectangle((10, 70, 140, 90), fill=128)
    for x in range(width):
        draw.point((x, 100), fill=x % 256)  # no runs at all
    rnd = random.Random(seed)
    noise = Image.frombytes("L", (width, 40), bytes(rnd.randrange(256) for _ in range(width * 40)))
    img.paste(noise, (0, 120))
    return img


def rgb_page(width: int = 257, height: int = 50) -> Image.Image:
    """RGB page whose byte runs straddle pixel boundaries (catches misaligned packing)."""
    img = Image.new("RGB", (width, height), (255, 255, 255))
    px = img.load()
    for x in range(width):
        # (5,5,5) (5,5,7) (7,7,7) ... -> byte-level runs that are not pixel aligned
        px[x, 0] = [(5, 5, 5), (5, 5, 7), (7, 7, 7)][x % 3]
        px[x, 1] = (10, 20, 30) if x < 200 else (30, 20, 10)  # 200-pixel run then 57
        px[x, 2] = (x % 256, (x * 7) % 256, (x * 13) % 256)  # literal pixels
    ImageDraw.Draw(img).text((5, 10), "Color", fill=(200, 0, 0))
    return img


# ------------------------------------------------------------- PWG raster


class TestPwg:
    def test_gray_roundtrip_and_header(self):
        page = gray_text_page()
        data = raster.encode_pwg([page], 300, A4_MM, A4_MEDIA)
        (decoded,) = decode_pwg(data)
        assert decoded["pixels"] == page.tobytes()
        h = decoded["header"]
        assert h["PwgRaster"] == "PwgRaster"
        assert h["HWResolution"] == (300, 300)
        assert (h["Width"], h["Height"]) == page.size
        assert h["BitsPerColor"] == 8
        assert h["BitsPerPixel"] == 8
        assert h["BytesPerLine"] == page.width
        assert h["ColorSpace"] == 18  # sGray
        assert h["NumColors"] == 1
        assert h["TotalPageCount"] == 1
        assert h["NumCopies"] == 1
        assert h["PageSizeName"] == A4_MEDIA
        assert h["PageSize"] == (595, 842)
        assert h["Duplex"] == 0

    def test_rgb_roundtrip_is_pixel_aligned(self):
        page = rgb_page()
        data = raster.encode_pwg([page], 600, A4_MM, A4_MEDIA)
        (decoded,) = decode_pwg(data)
        assert decoded["pixels"] == page.tobytes()
        h = decoded["header"]
        assert h["BitsPerPixel"] == 24
        assert h["BytesPerLine"] == page.width * 3
        assert h["ColorSpace"] == 19  # sRGB
        assert h["NumColors"] == 3
        assert h["HWResolution"] == (600, 600)

    def test_multi_page_duplex(self):
        pages = [gray_text_page(seed=s) for s in range(3)]
        data = raster.encode_pwg(pages, 300, A4_MM, A4_MEDIA, duplex=True)
        decoded = decode_pwg(data)
        assert len(decoded) == 3
        for page, out in zip(pages, decoded):
            assert out["pixels"] == page.tobytes()
            assert out["header"]["TotalPageCount"] == 3
            assert out["header"]["Duplex"] == 1
            assert out["header"]["Tumble"] == 0  # long edge

    @pytest.mark.parametrize("mode,fill", [("L", 0), ("RGB", (1, 2, 3))])
    def test_long_runs_and_many_identical_lines(self, mode, fill):
        # 1000-pixel runs (> 128) and 600 identical lines (> 256 line repeat)
        page = Image.new(mode, (1000, 600), fill)
        data = raster.encode_pwg([page], 300, A4_MM, A4_MEDIA)
        (decoded,) = decode_pwg(data)
        assert decoded["pixels"] == page.tobytes()
        assert len(data) < 4 + 1796 + 100  # a solid page compresses to a few bytes

    def test_odd_run_lengths(self):
        # Run lengths around the 128 packet boundary, separated by single literals
        row = bytearray()
        for n in (1, 2, 127, 128, 129, 130, 255, 256, 257):
            row += bytes([n % 200 + 1]) * n
            row += bytes([0])
        page = Image.frombytes("L", (len(row), 1), bytes(row))
        (decoded,) = decode_pwg(raster.encode_pwg([page], 300, A4_MM, A4_MEDIA))
        assert decoded["pixels"] == page.tobytes()

    def test_literal_runs_longer_than_128(self):
        page = Image.frombytes("L", (300, 2), bytes(i % 251 for i in range(600)))
        (decoded,) = decode_pwg(raster.encode_pwg([page], 300, A4_MM, A4_MEDIA))
        assert decoded["pixels"] == page.tobytes()

    def test_letter_page_size(self):
        page = Image.new("L", (10, 10), 255)
        (decoded,) = decode_pwg(raster.encode_pwg([page], 300, PAPER_SIZES["letter"]["mm"], "na_letter_8.5x11in"))
        assert decoded["header"]["PageSize"] == (612, 792)

    def test_long_media_name_keeps_header_size(self):
        page = Image.new("L", (4, 4), 255)
        data = raster.encode_pwg([page], 300, A4_MM, "custom_" + "x" * 70)
        (decoded,) = decode_pwg(data)
        assert decoded["pixels"] == page.tobytes()


# ------------------------------------------------------------------- URF


class TestUrf:
    def test_gray_roundtrip_and_header(self):
        page = gray_text_page()
        declared, (decoded,) = decode_urf(raster.encode_urf([page], 300))
        assert declared == 1
        assert decoded["pixels"] == page.tobytes()
        h = decoded["header"]
        assert h["bpp"] == 8
        assert h["colorspace"] == 0  # sGray
        assert h["duplex"] == 1  # simplex
        assert h["quality"] == 4
        assert (h["width"], h["height"], h["dpi"]) == (*page.size, 300)

    def test_rgb_multi_page_duplex(self):
        pages = [rgb_page(), rgb_page(100, 300), Image.new("RGB", (300, 300), (9, 9, 9))]
        declared, decoded = decode_urf(raster.encode_urf(pages, 600, duplex=True))
        assert declared == 3
        assert len(decoded) == 3
        for page, out in zip(pages, decoded):
            assert out["pixels"] == page.tobytes()
            assert out["header"]["bpp"] == 24
            assert out["header"]["colorspace"] == 1  # sRGB
            assert out["header"]["duplex"] == 3  # long edge (CUPS: 2 = short edge)
            assert out["header"]["dpi"] == 600


# ------------------------------------------------------------ page geometry


class TestFitToPaper:
    def test_portrait_fits_and_centers(self):
        img = Image.new("L", (100, 200), 0)
        page = raster.fit_to_paper(img, A4_MM, 100)
        assert page.size == (827, 1169)  # A4 at 100 dpi
        assert page.mode == "L"
        bbox = Image.eval(page, lambda v: 255 - v).getbbox()  # non-white area
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        assert abs(w / h - 0.5) < 0.01  # aspect preserved
        assert h == 1169 or w == 827  # touches at least one edge
        assert abs(bbox[0] - (827 - bbox[2])) <= 1  # horizontally centered

    def test_landscape_is_rotated(self):
        img = Image.new("RGB", (400, 200), (0, 0, 255))  # right half blue
        ImageDraw.Draw(img).rectangle((0, 0, 199, 199), fill=(255, 0, 0))  # left half red
        page = raster.fit_to_paper(img, A4_MM, 100)
        assert page.size == (827, 1169)
        assert page.mode == "RGB"
        bbox = Image.eval(page.convert("L"), lambda v: 255 - v).getbbox()
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        assert h > w  # now portrait
        assert abs(w / h - 0.5) < 0.01
        # rotate(90) is counter-clockwise: the left half ends up at the bottom
        cx = (bbox[0] + bbox[2]) // 2
        assert page.getpixel((cx, bbox[3] - 5)) == (255, 0, 0)
        assert page.getpixel((cx, bbox[1] + 5)) == (0, 0, 255)

    @pytest.mark.parametrize("dpi", [150, 300, 600])
    def test_output_size_matches_dpi(self, dpi):
        page = raster.fit_to_paper(Image.new("L", (50, 50), 0), A4_MM, dpi)
        assert page.size == (round(210 / 25.4 * dpi), round(297 / 25.4 * dpi))


class TestBackSide:
    @pytest.fixture
    def img(self):
        img = Image.new("L", (2, 2))
        img.putdata([1, 2, 3, 4])  # row-major: top-left, top-right, bottom-left, bottom-right
        return img

    def test_normal(self, img):
        assert list(raster.back_side_transform(img, "normal").tobytes()) == [1, 2, 3, 4]

    def test_flipped(self, img):
        assert list(raster.back_side_transform(img, "flipped").tobytes()) == [3, 4, 1, 2]

    def test_rotated(self, img):
        assert list(raster.back_side_transform(img, "rotated").tobytes()) == [4, 3, 2, 1]

    def test_unknown_is_untouched(self, img):
        assert list(raster.back_side_transform(img, "manual-tumble").tobytes()) == [1, 2, 3, 4]


# --------------------------------------------------------------- loading


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def pdf_bytes(pages: list[Image.Image], resolution: float = 72) -> bytes:
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=resolution, save_all=True, append_images=pages[1:])
    return buf.getvalue()


def pdf_page_count(data: bytes) -> int:
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


class TestLoadPages:
    def test_png_gray_and_color(self):
        img = Image.new("RGBA", (30, 20), (255, 0, 0, 255))
        data = png_bytes(img)
        (gray,) = raster.load_pages(data, 300, color=False)
        (rgb,) = raster.load_pages(data, 300, color=True)
        assert gray.mode == "L" and rgb.mode == "RGB"
        assert gray.size == rgb.size == (30, 20)
        assert rgb.getpixel((0, 0)) == (255, 0, 0)

    def test_multi_frame_tiff(self):
        frames = [Image.new("L", (40, 60), v) for v in (0, 128, 255)]
        buf = io.BytesIO()
        frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
        pages = raster.load_pages(buf.getvalue(), 300, color=False)
        assert [p.getpixel((0, 0)) for p in pages] == [0, 128, 255]

    def test_pdf_two_pages_rendered_at_dpi(self):
        p1 = Image.new("RGB", (100, 150), (0, 0, 0))
        p2 = Image.new("RGB", (150, 100), (255, 255, 255))
        data = pdf_bytes([p1, p2], resolution=72)  # 1 px = 1 pt
        assert raster.is_pdf(data)
        pages = raster.load_pages(data, 144, color=False)
        assert len(pages) == 2
        assert pages[0].size == (200, 300)
        assert pages[1].size == (300, 200)
        assert all(p.mode == "L" for p in pages)
        assert pages[0].getpixel((100, 150)) < 20
        assert pages[1].getpixel((150, 100)) > 235
        color_pages = raster.load_pages(data, 72, color=True)
        assert [p.mode for p in color_pages] == ["RGB", "RGB"]

    def test_is_pdf(self):
        assert raster.is_pdf(b"%PDF-1.7\n...")
        assert not raster.is_pdf(b"\x89PNG\r\n")
        assert not raster.is_pdf(b"")


# ------------------------------------------------------------- PDF helpers


def jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


class TestPdfHelpers:
    def test_images_to_pdf(self):
        images = [
            jpeg_bytes(Image.new("RGB", (100, 140), (200, 10, 10))),
            jpeg_bytes(Image.new("L", (100, 140), 50)),
            png_bytes(Image.new("1", (100, 140), 1)),  # bilevel -> converted to RGB
        ]
        data = raster.images_to_pdf(images, 100)
        assert raster.is_pdf(data)
        assert pdf_page_count(data) == 3
        pdf = pdfium.PdfDocument(data)
        try:
            w, h = pdf[0].get_size()
            assert (round(w), round(h)) == (72, round(140 * 72 / 100))  # page size honours dpi
        finally:
            pdf.close()

    def test_merge_pdfs(self):
        a = pdf_bytes([Image.new("L", (50, 50), 0)])
        b = pdf_bytes([Image.new("L", (50, 50), 0), Image.new("L", (60, 60), 0)])
        merged = raster.merge_pdfs([a, b, a])
        assert raster.is_pdf(merged)
        assert pdf_page_count(merged) == 4

    def test_merge_single_is_passthrough(self):
        a = pdf_bytes([Image.new("L", (50, 50), 0)])
        assert raster.merge_pdfs([a]) is a
