"""Bounded JPEG structure checks for self-contained 8-bit Huffman DCT images.

The marker, frame, table and scan syntax follows ITU-T T.81 Annex B. Sequential
and progressive scans are supported. This checks container structure and scan
presence, without decoding entropy symbols, coefficients or pixels.
"""

from .errors import ResearchError


def _malformed():
    raise ResearchError("malformed_visual_asset", "The saved JPEG has incomplete or invalid frame, table or scan structure")


def _unsupported():
    raise ResearchError("unsupported_visual_asset", "This JPEG coding process requires another supported static representation")


def _scan_end(data, offset, restart_interval):
    """Find the next structural marker without treating stuffed bytes as one."""
    count, restart = 0, 0
    while offset < len(data):
        marker_start = data.find(b"\xff", offset)
        if marker_start < 0:
            _malformed()
        count += marker_start - offset
        offset = marker_start + 1
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        if offset >= len(data):
            _malformed()
        marker = data[offset]
        offset += 1
        if marker == 0:
            if offset != marker_start + 2:
                _malformed()
            count += 1
        elif 0xd0 <= marker <= 0xd7:
            if not restart_interval or not count or marker != 0xd0 + restart:
                _malformed()
            restart, count = (restart + 1) % 8, 0
        else:
            if not count:
                _malformed()
            return marker_start
    _malformed()


def _tables(body, marker, quantization, huffman):
    offset = 0
    if not body:
        _malformed()
    while offset < len(body):
        header = body[offset]
        offset += 1
        kind, identifier = header >> 4, header & 15
        if kind > 1 or identifier > 3:
            _malformed()
        if marker == 0xdb:
            size = 64 * (kind + 1)
            values = body[offset:offset + size]
            if len(values) != size or any(int.from_bytes(values[i:i + kind + 1], "big") == 0 for i in range(0, size, kind + 1)):
                _malformed()
            quantization[identifier] = kind
        else:
            counts = body[offset:offset + 16]
            if len(counts) != 16 or not 1 <= sum(counts) <= 256:
                _malformed()
            capacity = 1
            for count in counts:
                capacity = capacity * 2 - count
                if capacity <= 0:
                    _malformed()
            size = 16 + sum(counts)
            symbols = body[offset + 16:offset + size]
            if len(symbols) != sum(counts) or any(value > 11 if kind == 0 else value & 15 > 10 for value in symbols):
                _malformed()
            huffman.add((kind, identifier))
        offset += size


def validate_jpeg(data):
    if not data.startswith(b"\xff\xd8"):
        _malformed()
    offset, frame, mode, restart_interval = 2, None, None, 0
    quantization, huffman, scanned, progression = {}, set(), set(), {}
    while offset < len(data):
        if data[offset] != 0xff:
            _malformed()
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        if offset == len(data):
            _malformed()
        marker = data[offset]
        offset += 1
        if marker == 0xd9:
            if frame is None or scanned != set(frame) or offset != len(data):
                _malformed()
            return
        if marker in (0, 0xd8) or 0xd0 <= marker <= 0xd7:
            _malformed()
        if marker not in (0xc0, 0xc1, 0xc2, 0xc4, 0xda, 0xdb, 0xdd, 0xfe) and not 0xe0 <= marker <= 0xef:
            _unsupported()
        if offset + 2 > len(data):
            _malformed()
        size = int.from_bytes(data[offset:offset + 2], "big")
        if size < 2 or offset + size > len(data):
            _malformed()
        body = data[offset + 2:offset + size]
        offset += size
        if marker in (0xdb, 0xc4):
            _tables(body, marker, quantization, huffman)
        elif marker in (0xc0, 0xc1, 0xc2):
            if frame is not None or len(body) < 6 or len(body) != 6 + 3 * body[5] or not body[5]:
                _malformed()
            if body[0] != 8 or body[5] > 4:
                _unsupported()
            if not int.from_bytes(body[1:3], "big") or not int.from_bytes(body[3:5], "big"):
                _malformed()
            frame, mode = {}, marker
            for index in range(6, len(body), 3):
                component, sampling, table = body[index:index + 3]
                if component in frame or not 1 <= sampling >> 4 <= 4 or not 1 <= sampling & 15 <= 4 or table > 3:
                    _malformed()
                frame[component] = (table, (sampling >> 4) * (sampling & 15))
        elif marker == 0xdd:
            if len(body) != 2:
                _malformed()
            restart_interval = int.from_bytes(body, "big")
        elif marker == 0xda:
            if frame is None or not body or not 1 <= body[0] <= 4 or len(body) != 4 + 2 * body[0]:
                _malformed()
            components = list(body[1:-3:2])
            tables = list(body[2:-3:2])
            if len(set(components)) != len(components) or any(c not in frame for c in components):
                _malformed()
            if components != sorted(components, key=list(frame).index) or len(components) > 1 and sum(frame[c][1] for c in components) > 10:
                _malformed()
            first, last, approximation = body[-3:]
            high, low = approximation >> 4, approximation & 15
            if mode != 0xc2:
                if (first, last, approximation) != (0, 63, 0) or scanned.intersection(components):
                    _malformed()
            elif (not 0 <= first <= last <= 63 or first == 0 and last != 0 or first > 0 and len(components) != 1
                    or high > 13 or low > 13 or high and high != low + 1):
                _malformed()
            for component, table in zip(components, tables):
                dc, ac = table >> 4, table & 15
                if dc > 3 or ac > 3 or mode == 0xc0 and (dc > 1 or ac > 1) or quantization.get(frame[component][0]) != 0:
                    _malformed()
                if (first == 0 and high == 0 and (0, dc) not in huffman) or (last > 0 and (1, ac) not in huffman):
                    _malformed()
                if mode == 0xc2:
                    if first > 0 and (component, 0) not in progression:
                        _malformed()
                    for coefficient in range(first, last + 1):
                        previous = progression.get((component, coefficient))
                        if previous != (high if high else None):
                            _malformed()
                        progression[component, coefficient] = low
            if first == 0:
                scanned.update(components)
            offset = _scan_end(data, offset, restart_interval)
    _malformed()
