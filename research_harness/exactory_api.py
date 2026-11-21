"""What the exactory commands share about the exactory application they talk to.

bin/exactory and bin/exactory-draft reach the same application. The origin rule and the id
pattern live here once, because a drift between the two commands would write a wrong page
URL into a permanent Zenodo record. The text rules of the application live here once too,
because problem-statement and problem-value apply the rules of the post to a text before the
post command sends it.
"""

import os
import re

_DEFAULT_API_URL = "https://www.exactory.ai"
# Every id the application hands out (problems, verifications) is a UUID.
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
# ECMAScript WhiteSpace and LineTerminator, used by String.prototype.trim:
# https://tc39.es/ecma262/#sec-string.prototype.trim (TrimString and the lexical whitespace rules).
JAVASCRIPT_TRIM_CHARACTERS = ("\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
                              "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
                              "\u2028\u2029\u202f\u205f\u3000\ufeff")
# The limits of each text field of a problem post, in UTF-16 code units after the trim, as the application checks them.
POST_TEXT_BOUNDS = {"title": (8, 200), "statement": (50, 20000), "statement_details": (1, 20000),
                    "motivation": (100, 10000), "current_state": (100, 10000), "resolution_criteria": (50, 5000),
                    "changes_if_resolved": (100, 5000)}
# Mirrors plainTextSchema (apps/web/src/server/schemas/plain-text.ts in the exactory repository, design
# 2026-10-12 §26.24), which the server applies to every free-text field: no control character except tab,
# line feed, and no unpaired surrogate, after CRLF and lone CR become LF. Python holds a character beyond the basic plane
# as one code point, and the json module joins an escaped surrogate pair into one, so every surrogate in a text is
# unpaired.
_NON_PLAIN_TEXT_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\ud800-\udfff]")
# The one line of a problem statement that divides the statement, shown in a box, from its details.
_STATEMENT_DETAILS_SEPARATOR_RE = re.compile(r"^[ \t]*---[ \t]*$", re.MULTILINE)


def read_api_base_url() -> str:
    """The application origin every command talks to: EXACTORY_API_URL, or the default."""
    return os.environ.get("EXACTORY_API_URL", _DEFAULT_API_URL).rstrip("/")


def normalize_plain_text_line_breaks(text: str) -> str:
    """Mirror plainTextSchema: CRLF and lone CR become LF before every other check."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def count_utf16_units(text: str) -> int:
    """Length in UTF-16 code units, the unit the server's bounds count.
    surrogatepass counts a lone surrogate as one unit, the way a JavaScript
    string does, instead of raising UnicodeEncodeError."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def find_non_plain_text_character(text: str):
    """The first character that plainTextSchema refuses in a text with normalized line breaks, with its line and its
    column counted in characters from 1, as (character, line, column); None when the text holds no such character."""
    match = _NON_PLAIN_TEXT_CHARACTER_RE.search(text)
    if match is None:
        return None
    line_start = text.rfind("\n", 0, match.start()) + 1
    return match.group(), text.count("\n", 0, match.start()) + 1, match.start() - line_start + 1


def split_statement_details(text: str) -> list:
    """The parts of a statement, with normalized line breaks, around each line that holds only ---: the statement
    alone, the statement and its details, or more parts for a statement with more such lines."""
    return _STATEMENT_DETAILS_SEPARATOR_RE.split(normalize_plain_text_line_breaks(text))


def find_post_text_violation(field: str, text: str):
    """The rule of the post that a text of the field breaks, checked in the order of plainTextSchema: the line breaks,
    the characters, the trim, then the bounds of POST_TEXT_BOUNDS. The rule comes with its numbers; None when the text
    breaks none."""
    text = normalize_plain_text_line_breaks(text)
    found = find_non_plain_text_character(text)
    if found is not None:
        character, line, column = found
        return {"rule": "character", "character": f"U+{ord(character):04X}", "line": line, "column": column}
    characters = count_utf16_units(text.strip(JAVASCRIPT_TRIM_CHARACTERS))
    minimum, maximum = POST_TEXT_BOUNDS[field]
    if minimum <= characters <= maximum:
        return None
    return {"rule": "length", "characters": characters, "minimum": minimum, "maximum": maximum}
