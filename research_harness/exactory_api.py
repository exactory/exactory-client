"""What the exactory commands share about the exactory application they talk to.

bin/exactory and bin/exactory-draft reach the same application. The origin rule and the id
pattern live here once, because a drift between the two commands would write a wrong page
URL into a permanent Zenodo record. The text rules of the application live here once too,
because problem-statement and problem-value apply the rules of the post to a text before the
post command sends it.
"""

import ipaddress
import os
import re
from urllib.parse import unquote

from .errors import ResearchError

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


POST_CITATION_LIMIT = 50
_POST_CITATION_TEXT_BOUNDS = (10, 1000)
_POST_CITATION_LOCATOR_MAX = 500
# DOI_LOCATOR uses JavaScript's \S in the server schema; Python's \S treats U+0085 differently.
_DOI_LOCATOR_RE = re.compile(r"10\.[0-9]{4,9}/[^" + re.escape(JAVASCRIPT_TRIM_CHARACTERS) + r"]+")
# Mirrors the server's locator refinement: a new-format id (2301.00001), an
# old-style id (hep-th/9901001), and the arXiv: prefix are all accepted. The patterns
# use [0-9], not \d, as the link patterns below explain.
_ARXIV_LOCATOR_RE = re.compile(
    r"(?:arxiv:)?(?:[0-9]{4}\.[0-9]{4,5}|[a-z-]+(?:\.[a-z]{2})?/[0-9]{7})(?:v[0-9]+)?",
    re.IGNORECASE,
)


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


def _is_citation_locator(locator: str) -> bool:
    """A locator an agent can resolve: a DOI, an arXiv id, or an https URL."""
    return bool(_DOI_LOCATOR_RE.fullmatch(locator)
                or _ARXIV_LOCATOR_RE.fullmatch(locator)
                or (locator.startswith("https://") and _is_https_url(locator)))


def _is_https_url(value: str) -> bool:
    """Refuse the https URLs that the server's URL.canParse (the WHATWG URL parser) certainly
    refuses: an empty host, an unclosed or invalid IPv6 literal, a forbidden host code point, or an
    invalid port. International and numeric hosts are left to the server, which refuses an invalid
    one with a 422, so this check never refuses a URL that the server accepts. The locator is posted
    as given."""
    # WHATWG removes ASCII tabs and newlines, reads a backslash as a slash, and ends the authority
    # at the first slash, backslash, question mark, or number sign.
    parsed = re.sub(r"[\t\n\r]", "", value)
    authority = re.split(r"[/\\?#]", parsed[len("https:"):].lstrip("/\\"), maxsplit=1)[0]
    host_port = authority.rpartition("@")[2]
    if host_port.startswith("["):
        host, closing, tail = host_port[1:].partition("]")
        # ipaddress accepts a zone id after %, and WHATWG does not.
        if not closing or "%" in host or (tail and not tail.startswith(":")):
            return False
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return False
        port = tail[1:]
    else:
        host, _, port = host_port.partition(":")
        try:
            host = unquote(host, errors="strict")
        except UnicodeDecodeError:
            return False
        if not host or re.search(r"[\x00-\x20\x7f%#/:<>?@\[\]\\^|]", host):
            return False
    return not port or bool(re.fullmatch(r"[0-9]+", port) and int(port) <= 65535)


def validate_post_citation_field(field: str, value: str, **context) -> str:
    """Validate a citation or locator string at a write boundary and return its wire spelling."""
    if field == "locator":
        bounds = (0, _POST_CITATION_LOCATOR_MAX)
        character_code = "problem_citation_locator_character"
        length_code = "problem_citation_locator_length"
    else:
        bounds = _POST_CITATION_TEXT_BOUNDS
        character_code = "problem_citation_text_character"
        length_code = "problem_citation_text_length"
    value = normalize_plain_text_line_breaks(value)
    found = find_non_plain_text_character(value)
    if found is not None:
        character, line, column = found
        raise ResearchError(character_code, "Remove the unsupported character from the citation field.",
                            dict(context, field=field, character=f"U+{ord(character):04X}", line=line, column=column))
    value = value.strip(JAVASCRIPT_TRIM_CHARACTERS)
    units = count_utf16_units(value)
    minimum, maximum = bounds
    if not minimum <= units <= maximum:
        raise ResearchError(length_code, "Use a citation field within the post's length limits.",
                            dict(context, field=field, characters=units, minimum=minimum, maximum=maximum))
    if field == "locator" and not _is_citation_locator(value):
        raise ResearchError("problem_citation_locator_format", "Give a DOI, an arXiv ID or an HTTPS URL.",
                            dict(context, field=field))
    return value


def validate_post_citations(entries) -> list:
    """Validate the complete citation list of an outgoing post."""
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ResearchError("problem_citations_shape", "Give an array of citation objects.")
    if not 1 <= len(entries) <= POST_CITATION_LIMIT:
        raise ResearchError("problem_citations_count", "Give a citation list within the post's count limits.",
                            {"count": len(entries), "minimum": 1, "maximum": POST_CITATION_LIMIT})
    parsed = []
    for index, entry in enumerate(entries, start=1):
        unknown_keys = sorted(set(entry) - {"citation", "locator"})
        if unknown_keys:
            raise ResearchError("problem_citation_unknown_key", "Give exactly citation and locator.",
                                {"index": index, "unexpected_fields": unknown_keys})
        citation, locator = entry.get("citation"), entry.get("locator")
        if not isinstance(citation, str) or not isinstance(locator, str):
            raise ResearchError("problem_citation_field_type", "Give citation and locator as strings.", {"index": index})
        parsed.append({"citation": validate_post_citation_field("citation", citation, index=index),
                       "locator": validate_post_citation_field("locator", locator, index=index)})
    return parsed
