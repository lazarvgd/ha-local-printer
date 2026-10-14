"""Document preparation for printing and scan post-processing.

- PDF/images -> pages (PIL) -> PWG raster or Apple URF (formats accepted by printers without PDF support)
- merging several scans into one PDF

All functions are blocking and must run in the executor.
"""

from __future__ import annotations

import io
import re
import struct

from PIL import Image

POINTS_PER_MM = 72 / 25.4


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def load_pages(data: bytes, dpi: int, color: bool) -> list[Image.Image]:
    """PDF or image -> list of pages as PIL images ("L" or "RGB")."""
    mode = "RGB" if color else "L"
    if is_pdf(data):
        import pypdfium2 as pdfium  # imported lazily, only when needed

        pdf = pdfium.PdfDocument(data)
        try:
            return [page.render(scale=dpi / 72, grayscale=not color).to_pil().convert(mode) for page in pdf]
        finally:
            pdf.close()
    pages = []
    with Image.open(io.BytesIO(data)) as img:
        for frame in range(getattr(img, "n_frames", 1)):  # multi-frame TIFF/GIF
            img.seek(frame)
            pages.append(img.convert(mode))
    return pages


def fit_to_paper(img: Image.Image, paper_mm: tuple[float, float], dpi: int) -> Image.Image:
    """Fit the image onto the paper (keeping the aspect ratio), centered on a white page."""
    page_w = round(paper_mm[0] / 25.4 * dpi)
    page_h = round(paper_mm[1] / 25.4 * dpi)
    if img.width > img.height:
        img = img.rotate(90, expand=True)  # rotate landscape content to portrait
    scale = min(page_w / img.width, page_h / img.height)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    if size != img.size:
        img = img.resize(size, Image.LANCZOS)
    page = Image.new(img.mode, (page_w, page_h), 255 if img.mode == "L" else (255, 255, 255))
    page.paste(img, ((page_w - img.width) // 2, (page_h - img.height) // 2))
    return page


def back_side_transform(page: Image.Image, sheet_back: str) -> Image.Image:
    """Transform an even (back side) page for long-edge duplex printing, following CUPS.

    sheet_back is pwg-raster-document-sheet-back (normal/flipped/rotated/manual-tumble).
    """
    if sheet_back == "flipped":
        return page.transpose(Image.FLIP_TOP_BOTTOM)
    if sheet_back == "rotated":
        return page.transpose(Image.ROTATE_180)
    return page


def _compress_line(line: bytes, bpp: int, out: bytearray, runs: re.Pattern) -> None:
    """PackBits variant from PWG 5102.4 / Apple URF: the unit is a pixel of `bpp` bytes."""
    literal: list[bytes] = []

    def flush() -> None:
        for i in range(0, len(literal), 128):
            chunk = literal[i : i + 128]
            out.append(257 - len(chunk) if len(chunk) > 1 else 0)
            out.extend(b"".join(chunk))
        literal.clear()

    for match in runs.finditer(line):
        count = (match.end() - match.start()) // bpp
        pixel = match.group(1)
        if count == 1:
            literal.append(pixel)
            continue
        flush()
        while count > 0:
            n = min(count, 128)
            out.append(n - 1)
            out.extend(pixel)
            count -= n
    flush()


def _compress_page(page: Image.Image, out: bytearray) -> None:
    bpp = 3 if page.mode == "RGB" else 1
    runs = re.compile(rb"(.{%d})\1*" % bpp, re.S)
    width, height = page.size
    stride = width * bpp
    raw = page.tobytes()
    y = 0
    while y < height:
        line = raw[y * stride : (y + 1) * stride]
        repeat = 1
        while repeat < 256 and y + repeat < height and raw[(y + repeat) * stride : (y + repeat + 1) * stride] == line:
            repeat += 1
        out.append(repeat - 1)
        _compress_line(line, bpp, out, runs)
        y += repeat


def _pwg_header(page: Image.Image, dpi: int, paper_mm: tuple[float, float], media: str, total: int, duplex: bool) -> bytes:
    h = bytearray(1796)
    color = page.mode == "RGB"
    width, height = page.size

    def put_str(offset: int, value: str) -> None:
        raw = value.encode()[:63]  # 64-byte field, keep a terminating NUL
        h[offset : offset + len(raw)] = raw

    def put_u32(offset: int, value: int) -> None:
        struct.pack_into(">I", h, offset, value)

    put_str(0, "PwgRaster")
    put_u32(272, 1 if duplex else 0)  # Duplex
    put_u32(276, dpi)  # HWResolution
    put_u32(280, dpi)
    put_u32(340, 1)  # NumCopies (copies are requested via IPP)
    put_u32(352, round(paper_mm[0] * POINTS_PER_MM))  # PageSize in points
    put_u32(356, round(paper_mm[1] * POINTS_PER_MM))
    put_u32(372, width)
    put_u32(376, height)
    put_u32(384, 8)  # BitsPerColor
    put_u32(388, 24 if color else 8)  # BitsPerPixel
    put_u32(392, width * (3 if color else 1))  # BytesPerLine
    put_u32(400, 19 if color else 18)  # ColorSpace: sRGB / sGray
    put_u32(420, 3 if color else 1)  # NumColors
    put_u32(452, total)  # TotalPageCount
    put_u32(456, 1)  # CrossFeedTransform
    put_u32(460, 1)  # FeedTransform
    put_u32(472, width)  # ImageBoxRight
    put_u32(476, height)  # ImageBoxBottom
    put_u32(480, 0xFFFFFF)  # AlternatePrimary
    put_u32(484, 4)  # PrintQuality: normal
    put_str(1732, media)  # PageSizeName
    return bytes(h)


def encode_pwg(pages: list[Image.Image], dpi: int, paper_mm: tuple[float, float], media: str, duplex: bool = False) -> bytes:
    out = bytearray(b"RaS2")
    for page in pages:
        out += _pwg_header(page, dpi, paper_mm, media, len(pages), duplex)
        _compress_page(page, out)
    return bytes(out)


def encode_urf(pages: list[Image.Image], dpi: int, duplex: bool = False) -> bytes:
    """Apple raster (URF/UNIRAST), the format used by AirPrint printers."""
    out = bytearray(b"UNIRAST\x00" + struct.pack(">I", len(pages)))
    for page in pages:
        color = page.mode == "RGB"
        header = bytearray(32)
        header[0] = 24 if color else 8  # bits per pixel
        header[1] = 1 if color else 0  # 1 = sRGB, 0 = sGray
        header[2] = 3 if duplex else 1  # per CUPS: 1 = simplex, 2 = short edge, 3 = long edge
        header[3] = 4  # quality: normal
        struct.pack_into(">III", header, 12, page.width, page.height, dpi)
        out += header
        _compress_page(page, out)
    return bytes(out)


def images_to_pdf(images: list[bytes], dpi: int) -> bytes:
    """One or more images (e.g. JPEG scans) -> a single PDF."""
    frames = []
    for data in images:
        with Image.open(io.BytesIO(data)) as img:
            frames.append(img.convert("RGB") if img.mode not in ("L", "RGB") else img.copy())
    buf = io.BytesIO()
    frames[0].save(buf, "PDF", resolution=dpi, save_all=True, append_images=frames[1:])
    return buf.getvalue()


def merge_pdfs(documents: list[bytes]) -> bytes:
    """Several PDFs (e.g. one page per document from an ADF) -> a single PDF."""
    if len(documents) == 1:
        return documents[0]
    import pypdfium2 as pdfium

    merged = pdfium.PdfDocument.new()
    sources = [pdfium.PdfDocument(doc) for doc in documents]
    try:
        for src in sources:
            merged.import_pages(src)
        buf = io.BytesIO()
        merged.save(buf)
        return buf.getvalue()
    finally:
        for src in sources:
            src.close()
        merged.close()


def render_preview(data: bytes, max_size: int = 1000) -> bytes:
    """First page of a PDF or image -> PNG thumbnail no larger than `max_size` pixels."""
    if is_pdf(data):
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            page = pdf[0]
            width, height = page.get_size()  # points
            img = page.render(scale=max_size / max(width, height)).to_pil()
        finally:
            pdf.close()
    else:
        with Image.open(io.BytesIO(data)) as src:
            img = src.convert("RGB")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()
