"""Bounded PNG, GIF and WebP container checks without pixel reconstruction.

PNG additionally checks the complete zlib stream's filtered byte count, capped
at 64 MiB. GIF LZW and WebP compressed pixels are not decoded. Their mandatory
image descriptors, data blocks and container termination must be present.
"""

import struct
import zlib

from .errors import ResearchError


_MAX_PNG_FILTERED_BYTES = 64 * 1024 * 1024


def _malformed():
    raise ResearchError("malformed_visual_asset", "The saved image has incomplete or invalid container structure")


def _unsupported():
    raise ResearchError("unsupported_visual_asset", "This image representation exceeds the supported structural checks")


def _png_stream(header, compressed):
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    allowed = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if not width or not height or depth not in allowed.get(color, ()) or compression or filtering or interlace not in (0, 1):
        _malformed()
    passes = ((0, 0, 1, 1),) if interlace == 0 else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
    expected = 0
    for x, y, dx, dy in passes:
        columns, rows = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if columns and rows:
            expected += rows * (1 + (columns * depth * channels[color] + 7) // 8)
    if expected > _MAX_PNG_FILTERED_BYTES:
        _unsupported()
    decoder = zlib.decompressobj()
    try:
        filtered = decoder.decompress(compressed, expected + 1)
    except zlib.error:
        _malformed()
    if len(filtered) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        _malformed()


def validate_png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        _malformed()
    offset, header, palette, finished_data, payload = 8, None, False, False, []
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + size + 12
        if end > len(data):
            _malformed()
        kind, body = data[offset + 4:offset + 8], data[offset + 8:end - 4]
        if any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in kind) or zlib.crc32(kind + body) & 0xffffffff != int.from_bytes(data[end - 4:end], "big"):
            _malformed()
        if header is None and kind != b"IHDR":
            _malformed()
        if kind == b"IHDR":
            if header is not None or size != 13:
                _malformed()
            header = body
        elif kind == b"PLTE":
            if palette or payload or not size or size > 768 or size % 3:
                _malformed()
            palette = True
        elif kind == b"IDAT":
            if finished_data or header[9] == 3 and not palette:
                _malformed()
            payload.append(body)
        elif kind == b"IEND":
            if body or not payload or end != len(data):
                _malformed()
            _png_stream(header, b"".join(payload))
            return
        elif kind in (b"acTL", b"fcTL", b"fdAT") or not kind[0] & 32:
            _unsupported()
        if kind != b"IDAT" and payload:
            finished_data = True
        offset = end
    _malformed()


def _gif_blocks(data, offset):
    total = 0
    while offset < len(data):
        size = data[offset]
        offset += 1
        if not size:
            return offset, total
        if offset + size > len(data):
            _malformed()
        offset, total = offset + size, total + size
    _malformed()


def validate_gif(data):
    if len(data) < 13 or data[:6] not in (b"GIF87a", b"GIF89a"):
        _malformed()
    width, height = struct.unpack("<HH", data[6:10])
    if not width or not height:
        _malformed()
    palette = bool(data[10] & 128)
    offset, images = 13 + (3 * (2 ** ((data[10] & 7) + 1)) if palette else 0), 0
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == 0x3b:
            if not images or offset != len(data):
                _malformed()
            return
        if marker == 0x21:
            if offset >= len(data):
                _malformed()
            label = data[offset]
            offset += 1
            if label not in (0xf9, 0xfe, 0xff):
                _unsupported()
            if label in (0xf9, 0xff) and (offset >= len(data) or data[offset] != (4 if label == 0xf9 else 11)):
                _malformed()
            offset, total = _gif_blocks(data, offset)
            if label == 0xf9 and total != 4:
                _malformed()
        elif marker == 0x2c:
            if offset + 9 > len(data):
                _malformed()
            left, top, columns, rows, flags = struct.unpack("<HHHHB", data[offset:offset + 9])
            if not columns or not rows or left + columns > width or top + rows > height or flags & 24:
                _malformed()
            offset += 9
            if flags & 128:
                offset += 3 * (2 ** ((flags & 7) + 1))
            elif not palette:
                _malformed()
            if offset >= len(data) or not 2 <= data[offset] <= 8:
                _malformed()
            offset, total = _gif_blocks(data, offset + 1)
            if not total:
                _malformed()
            images += 1
        else:
            _malformed()
    _malformed()


def _webp_frame(kind, body):
    if kind == b"VP8 ":
        if len(body) <= 10 or body[3:6] != b"\x9d\x01\x2a":
            _malformed()
        tag = int.from_bytes(body[:3], "little")
        width, height = (int.from_bytes(body[i:i + 2], "little") & 0x3fff for i in (6, 8))
        if tag & 1 or (tag >> 1) & 7 > 3 or not tag & 16 or not width or not height or not 0 < tag >> 5 < len(body) - 10:
            _malformed()
        return (width, height), False
    if len(body) <= 5 or body[0] != 0x2f:
        _malformed()
    header = int.from_bytes(body[1:5], "little")
    if header >> 29:
        _unsupported()
    return ((header & 0x3fff) + 1, ((header >> 14) & 0x3fff) + 1), bool(header & (1 << 28))


def validate_webp(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP" or int.from_bytes(data[4:8], "little") + 8 != len(data):
        _malformed()
    offset, seen, canvas, frame, alpha, flags = 12, set(), None, None, False, 0
    while offset + 8 <= len(data):
        kind, size = data[offset:offset + 4], int.from_bytes(data[offset + 4:offset + 8], "little")
        end = offset + 8 + size
        if end + (size & 1) > len(data) or size & 1 and data[end] != 0:
            _malformed()
        body = data[offset + 8:end]
        if not seen and kind not in (b"VP8 ", b"VP8L", b"VP8X"):
            _malformed()
        if kind in (b"VP8X", b"VP8 ", b"VP8L", b"ALPH", b"ICCP", b"EXIF", b"XMP ") and kind in seen:
            _malformed()
        if kind == b"VP8X":
            if seen or size != 10 or body[0] & 0xc1 or any(body[1:4]):
                _malformed()
            flags = body[0]
            canvas = tuple(int.from_bytes(body[i:i + 3], "little") + 1 for i in (4, 7))
            if canvas[0] * canvas[1] > 0xffffffff:
                _malformed()
            if flags & 2:
                _unsupported()
        elif kind in (b"ANIM", b"ANMF"):
            _unsupported()
        elif kind == b"ALPH":
            if canvas is None or frame is not None or len(body) <= 1 or body[0] & 0xc0 or body[0] & 3 > 1 or (body[0] >> 4) & 3 > 1:
                _malformed()
            if not body[0] & 3 and len(body) - 1 != canvas[0] * canvas[1]:
                _malformed()
            alpha = True
        elif kind in (b"VP8 ", b"VP8L"):
            if frame is not None or kind == b"VP8L" and alpha:
                _malformed()
            frame, embedded_alpha = _webp_frame(kind, body)
            alpha = alpha or embedded_alpha
            if canvas is not None and frame != canvas:
                _malformed()
        elif kind == b"ICCP" and (canvas is None or frame is not None or not body):
            _malformed()
        seen.add(kind)
        offset = end + (size & 1)
    if offset != len(data) or frame is None:
        _malformed()
    if canvas is not None and any(bool(flags & bit) != present for bit, present in
            ((0x20, b"ICCP" in seen), (0x10, alpha), (0x08, b"EXIF" in seen), (0x04, b"XMP " in seen))):
        _malformed()
