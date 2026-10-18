"""What the exactory commands share about the exactory application they talk to.

bin/exactory and bin/exactory-draft reach the same application. The origin rule and the id
pattern live here once, because a drift between the two commands would write a wrong page
URL into a permanent Zenodo record.
"""

import os
import re

_DEFAULT_API_URL = "https://www.exactory.ai"
# Every id the application hands out (problems, verifications) is a UUID.
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def read_api_base_url() -> str:
    """The application origin every command talks to: EXACTORY_API_URL, or the default."""
    return os.environ.get("EXACTORY_API_URL", _DEFAULT_API_URL).rstrip("/")
