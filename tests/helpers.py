"""Test-only reference implementations: IPP message builder/parser and raster decoders.

These are written independently from the production code so the tests verify the
wire formats rather than re-running the same logic.
"""

from __future__ import annotations

import struct

# --------------------------------------------------------------------------- IPP


def ipp_value(tag: int, name: str, raw: bytes) -> bytes:
    """One attribute value: value-tag, name-length, name, value-length, value."""
    enc = name.encode()
    return bytes([tag]) + struct.pack(">H", len(enc)) + enc + struct.pack(">H", len(raw)) + raw


def ipp_int(name: str, value: int, tag: int = 0x21) -> bytes:
    return ipp_value(tag, name, struct.pack(">i", value))


def ipp_str(name: str, value: str, tag: int = 0x44) -> bytes:
    return ipp_value(tag, name, value.encode())


def ipp_multi(name: str, values: list[str], tag: int = 0x44) -> bytes:
    """Multi-valued attribute: first value named, the rest with name-length 0."""
    out = ipp_str(name, values[0], tag)
    for value in values[1:]:
        out += ipp_str("", value, tag)
    return out


def ipp_resolution(name: str, x: int, y: int, unit: int = 3) -> bytes:
    return ipp_value(0x32, name, struct.pack(">iib", x, y, unit))


def ipp_range(name: str, lo: int, hi: int) -> bytes:
    return ipp_value(0x33, name, struct.pack(">ii", lo, hi))


def ipp_collection(name: str, members: list[tuple[str, bytes]]) -> bytes:
    """begCollection, then (memberAttrName, value) pairs, then endCollection.

    Each member value is a pre-built value with an empty name (may itself be a collection).
    """
    out = ipp_value(0x34, name, b"")
    for member_name, value in members:
        out += ipp_value(0x4A, "", member_name.encode())
        out += value
    out += ipp_value(0x37, "", b"")
    return out


def ipp_message(status_or_op: int, groups: list[tuple[int, bytes]], request_id: int = 1, trailer: bytes = b"") -> bytes:
    out = struct.pack(">BBHI", 2, 0, status_or_op, request_id)
    for group_tag, body in groups:
        out += bytes([group_tag]) + body
    return out + b"\x03" + trailer


def ipp_operation_group(extra: bytes = b"") -> tuple[int, bytes]:
    return 0x01, ipp_str("attributes-charset", "utf-8", 0x47) + ipp_str("attributes-natural-language", "en", 0x48) + extra


def parse_ipp_request(data: bytes) -> dict:
    """Parse a flat IPP request (no collections).

    Returns {"version", "operation", "request_id", "attrs": [(group, tag, name, raw)], "document"}.
    """
    version = (data[0], data[1])
    operation, request_id = struct.unpack(">HI", data[2:8])
    pos, group, attrs = 8, None, []
    while True:
        tag = data[pos]
        pos += 1
        if tag == 0x03:
            break
        if tag < 0x10:
            group = tag
            continue
        (name_len,) = struct.unpack(">H", data[pos : pos + 2])
        pos += 2
        name = data[pos : pos + name_len].decode()
        pos += name_len
        (val_len,) = struct.unpack(">H", data[pos : pos + 2])
        pos += 2
        attrs.append((group, tag, name, data[pos : pos + val_len]))
        pos += val_len
    return {
        "version": version,
        "operation": operation,
        "request_id": request_id,
        "attrs": attrs,
        "document": data[pos:],
    }


# ------------------------------------------------------------------ PackBits-ish


def _decode_lines(data: bytes, pos: int, width: int, height: int, pixel_bytes: int) -> tuple[bytes, int]:
    """Decode PWG 5102.4 / URF compressed bitmap.

    Per line group: one line-repeat byte (repeat count - 1), then packets until the
    line is full: 0..127 -> next pixel repeated n + 1 times; 129..255 -> 257 - n literal
    pixels (128 is not used by encoders; treat it as invalid).
    """
    stride = width * pixel_bytes
    rows: list[bytes] = []
    while len(rows) < height:
        repeat = data[pos] + 1
        pos += 1
        line = bytearray()
        while len(line) < stride:
            n = data[pos]
            pos += 1
            if n <= 127:
                line += data[pos : pos + pixel_bytes] * (n + 1)
                pos += pixel_bytes
            elif n == 128:
                raise ValueError("packet byte 128 is reserved")
            else:
                count = 257 - n
                line += data[pos : pos + count * pixel_bytes]
                pos += count * pixel_bytes
        if len(line) != stride:
            raise ValueError(f"line overflow: {len(line)} != {stride}")
        rows.extend([bytes(line)] * repeat)
    if len(rows) != height:
        raise ValueError(f"line repeat overflow: {len(rows)} != {height}")
    return b"".join(rows), pos


def _u32(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">I", buf, offset)[0]


def _cstr(buf: bytes, offset: int, size: int = 64) -> str:
    return buf[offset : offset + size].split(b"\x00", 1)[0].decode()


def decode_pwg(data: bytes) -> list[dict]:
    """Reference PWG raster decoder. Returns a list of {"header": {...}, "pixels": bytes}."""
    assert data[:4] == b"RaS2", "missing PWG sync word"
    pos, pages = 4, []
    while pos < len(data):
        raw = data[pos : pos + 1796]
        assert len(raw) == 1796, "truncated page header"
        pos += 1796
        header = {
            "PwgRaster": _cstr(raw, 0),
            "Duplex": _u32(raw, 272),
            "HWResolution": (_u32(raw, 276), _u32(raw, 280)),
            "NumCopies": _u32(raw, 340),
            "PageSize": (_u32(raw, 352), _u32(raw, 356)),
            "Tumble": _u32(raw, 368),
            "Width": _u32(raw, 372),
            "Height": _u32(raw, 376),
            "BitsPerColor": _u32(raw, 384),
            "BitsPerPixel": _u32(raw, 388),
            "BytesPerLine": _u32(raw, 392),
            "ColorOrder": _u32(raw, 396),
            "ColorSpace": _u32(raw, 400),
            "NumColors": _u32(raw, 420),
            "TotalPageCount": _u32(raw, 452),
            "PageSizeName": _cstr(raw, 1732),
        }
        pixels, pos = _decode_lines(data, pos, header["Width"], header["Height"], header["BitsPerPixel"] // 8)
        pages.append({"header": header, "pixels": pixels})
    return pages


def decode_urf(data: bytes) -> tuple[int, list[dict]]:
    """Reference Apple URF (UNIRAST) decoder. Returns (declared page count, pages)."""
    assert data[:8] == b"UNIRAST\x00", "missing UNIRAST magic"
    declared = _u32(data, 8)
    pos, pages = 12, []
    while pos < len(data):
        raw = data[pos : pos + 32]
        assert len(raw) == 32, "truncated page header"
        pos += 32
        header = {
            "bpp": raw[0],
            "colorspace": raw[1],
            "duplex": raw[2],
            "quality": raw[3],
            "width": _u32(raw, 12),
            "height": _u32(raw, 16),
            "dpi": _u32(raw, 20),
        }
        pixels, pos = _decode_lines(data, pos, header["width"], header["height"], header["bpp"] // 8)
        pages.append({"header": header, "pixels": pixels})
    return declared, pages
