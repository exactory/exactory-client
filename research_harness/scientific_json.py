"""One bounded JSON byte boundary for checked scientific review delivery."""

import json

from .errors import ResearchError
from .workspace import strict_json


def _bounded_nesting(decoded):
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


def scientific_json(data, media_type="", *, encoded_string=False):
    """Return (recognized, value), using the encodings accepted by strict_json."""
    if not isinstance(data, bytes) or len(data) > 64 * 1024 * 1024:
        raise ResearchError("invalid_scientific_delivery", "Scientific JSON bytes must fit the individual 64 MiB artifact bound")
    mime = media_type.split(";", 1)[0].strip().lower()
    declared = mime == "application/json" or mime.endswith("+json")
    try:
        decoded = data.decode(json.detect_encoding(data))
    except UnicodeError:
        if declared:
            return True, strict_json(data)
        return False, None
    starts = ("{", "[", '"') if encoded_string else ("{", "[")
    if declared or decoded.lstrip().startswith(starts):
        _bounded_nesting(decoded)
        try:
            return True, strict_json(data)
        except ResearchError as error:
            # A leading bracket or quote is only a hint: undeclared text that
            # no JSON decoder reads is text. Text that a decoder reads, such as
            # JSON with duplicate fields, stays strict, because as text its
            # decoded strings would miss the privacy checks.
            if declared or error.code != "invalid_json":
                raise
            try:
                json.loads(decoded)
            except ValueError:
                return False, None
            raise
    return False, None
