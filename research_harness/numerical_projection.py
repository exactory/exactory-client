"""Decode bounded primitive NPY arrays without NumPy, pickle or code execution."""

import ast
import math
import re
import struct

from .errors import ResearchError


_CODE = "invalid_numerical_projection"
MAX_ELEMENTS = 8_000_000


def _encode_float(value):
    if math.isnan(value):
        return {"nonfinite": "nan"}
    if math.isinf(value):
        return {"nonfinite": "positive_infinity" if value > 0 else "negative_infinity"}
    return value


def decode_npy(data, *, remaining_elements=MAX_ELEMENTS):
    if not isinstance(data, bytes) or len(data) < 10 or data[:6] != b"\x93NUMPY":
        raise ResearchError(_CODE, "The scientific member must contain a complete NPY header")
    version = tuple(data[6:8])
    if version not in ((1, 0), (2, 0), (3, 0)):
        raise ResearchError(_CODE, "Only NPY versions 1.0, 2.0 and 3.0 are supported")
    length_bytes = 2 if version == (1, 0) else 4
    prefix = 8 + length_bytes
    if len(data) < prefix:
        raise ResearchError(_CODE, "The NPY header length is truncated")
    length = int.from_bytes(data[8:prefix], "little")
    if not 1 <= length <= 65536 or prefix + length > len(data):
        raise ResearchError(_CODE, "The complete NPY header must fit its 64 KiB bound")
    raw_header = data[prefix:prefix + length]
    if not raw_header.endswith(b"\n"):
        raise ResearchError(_CODE, "The NPY header must end with its declared newline")
    try:
        parsed = ast.parse(raw_header.decode("utf-8" if version == (3, 0) else "latin-1").strip(), mode="eval")
        if not isinstance(parsed.body, ast.Dict) or len(parsed.body.keys) != 3:
            raise ValueError("The header has exactly three unique fields")
        header = ast.literal_eval(parsed)
    except (UnicodeError, ValueError, SyntaxError, TypeError, RecursionError) as error:
        raise ResearchError(_CODE, "The NPY header must contain a bounded literal dictionary") from error
    if not isinstance(header, dict) or set(header) != {"descr", "fortran_order", "shape"}:
        raise ResearchError(_CODE, "The NPY header must declare dtype, shape and storage order exactly once")
    shape, fortran, dtype = header["shape"], header["fortran_order"], header["descr"]
    if (not isinstance(shape, tuple) or len(shape) > 32 or type(fortran) is not bool
            or any(type(n) is not int or n < 0 for n in shape)):
        raise ResearchError(_CODE, "NPY shapes contain at most 32 nonnegative integer dimensions")
    count = math.prod(shape)
    if count > remaining_elements:
        raise ResearchError(_CODE, "The numerical population exceeds the aggregate element bound")
    match = re.fullmatch(r"([<>|])([biufcU])([0-9]+)", dtype) if isinstance(dtype, str) else None
    if match is None:
        raise ResearchError(_CODE, "Object, structured, native-endian and unsupported NPY dtypes cannot be delivered")
    endian, kind, width = match[1], match[2], int(match[3])
    size = 4 * width if kind == "U" else width
    sizes = {"b": (1,), "i": (1, 2, 4, 8), "u": (1, 2, 4, 8), "f": (4, 8), "c": (8, 16)}
    if (kind != "U" and width not in sizes[kind] or kind == "U" and width > 16384
            or endian == "|" and size != 1):
        raise ResearchError(_CODE, "Use a supported primitive width and explicit multi-byte endian order")
    payload = data[prefix + length:]
    if len(payload) != count * size:
        raise ResearchError(_CODE, "The NPY payload must match its exact dtype and shape without trailing bytes")
    order = "<" if endian == "|" else endian
    values, hexes = [], []
    for index in range(count):
        element = payload[index * size:(index + 1) * size]
        hexes.append(element.hex())
        if kind in ("i", "u"):
            value = {"integer": str(int.from_bytes(element, "little" if order == "<" else "big", signed=kind == "i"))}
        elif kind == "b":
            if element not in (b"\x00", b"\x01"):
                raise ResearchError(_CODE, "Boolean NPY storage must contain canonical zero or one values")
            value = element == b"\x01"
        elif kind == "f":
            value = _encode_float(struct.unpack(order + ("f" if size == 4 else "d"), element)[0])
        elif kind == "c":
            real, imag = struct.unpack(order + ("ff" if size == 8 else "dd"), element)
            value = {"complex": {"real": _encode_float(real), "imag": _encode_float(imag)}}
        else:
            codes = [int.from_bytes(element[i:i + 4], "little" if order == "<" else "big") for i in range(0, size, 4)]
            if any(code > 0x10FFFF or 0xD800 <= code <= 0xDFFF for code in codes):
                raise ResearchError(_CODE, "Unicode NPY elements must contain valid UCS-4 scalar values")
            value = "".join(chr(code) for code in codes).rstrip("\x00")
        values.append(value)
    strides = [math.prod(shape[:i] if fortran else shape[i + 1:]) for i in range(len(shape))]
    return {"dtype": dtype, "shape": list(shape), "fortran_order": fortran, "element_strides": strides,
            "index_mapping": "flat_index = sum(index[i] * element_strides[i])",
            "values": values, "element_hex": hexes, "element_count": count,
            "storage_order": "F" if fortran else "C", "npy_version": list(version)}
