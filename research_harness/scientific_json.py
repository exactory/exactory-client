"""One bounded JSON byte boundary for checked scientific review delivery."""

import json

from .errors import ResearchError
from .workspace import strict_json


def _check_nesting_bound(decoded):
    depth, quoted, escaped = 0, False, False
    for character in decoded:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > 40:
                raise ResearchError("invalid_scientific_delivery", "Decoded scientific JSON nesting exceeds its bound")
        elif character in "]}":
            depth -= 1


def _is_decodable(decoded):
    """Whether a JSON decoder reads these characters, before the strict rules."""
    try:
        # Integers of any length, which some interpreters refuse to convert.
        json.loads(decoded, parse_int=str)
    except RecursionError:
        # Deeper than this interpreter's stack; a decoder with a larger one reads it.
        return True
    except ValueError:
        return False
    return True


def parse_scientific_json(data, media_type=""):
    """Return (recognized, value), using the encodings accepted by strict_json.

    A declared JSON media type requires strict JSON. Other bytes that begin
    with `{`, `[` or `"` are JSON when a JSON decoder reads them, and then they
    must be strict JSON too, because as text their decoded strings would miss
    the privacy checks. Bytes that no JSON decoder reads are text."""
    if not isinstance(data, bytes) or len(data) > 64 * 1024 * 1024:
        raise ResearchError("invalid_scientific_delivery", "Scientific JSON bytes must fit the individual 64 MiB artifact bound")
    mime = media_type.split(";", 1)[0].strip().lower()
    declared = mime == "application/json" or mime.endswith("+json")
    try:
        # The JSON decoder reads bytes with the same encoding and error handler.
        decoded = data.decode(json.detect_encoding(data), "surrogatepass")
    except UnicodeError:
        if declared:
            return True, strict_json(data)
        return False, None
    if declared:
        _check_nesting_bound(decoded)
        return True, strict_json(data)
    if not decoded.lstrip().startswith(("{", "[", '"')):
        return False, None
    try:
        value = strict_json(data)
    except ResearchError as error:
        if error.code == "invalid_json" and not _is_decodable(decoded):
            return False, None
        _check_nesting_bound(decoded)
        raise
    _check_nesting_bound(decoded)
    return True, value
