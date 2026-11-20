"""What the exactory commands share about the exactory application they talk to.

bin/exactory and bin/exactory-draft reach the same application. The origin rule and the id
pattern live here once, because a drift between the two commands would write a wrong page
URL into a permanent Zenodo record. The text rules of the application live here once too,
because problem-value counts a text of the post before the post command sends it.
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
# The limits of the text of What a resolution would change, in UTF-16 code units after the trim, as the application checks them.
CHANGES_IF_RESOLVED_MIN_UNITS = 100
CHANGES_IF_RESOLVED_MAX_UNITS = 5000


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
