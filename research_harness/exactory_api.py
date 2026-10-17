"""What the exactory commands share about the exactory application they talk to.

bin/exactory and bin/exactory-draft reach the same application. The origin rule, the id
pattern, the account page, and the ORCID message live here once, because a drift between
the two commands would write a wrong page URL into a permanent Zenodo record.
"""

import os
import re

_DEFAULT_API_URL = "https://www.exactory.ai"
ACCOUNT_PAGE_PATH = "/account"
# Every id the application hands out (problems, verifications) is a UUID.
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def read_api_base_url() -> str:
    """The application origin every command talks to: EXACTORY_API_URL, or the default."""
    return os.environ.get("EXACTORY_API_URL", _DEFAULT_API_URL).rstrip("/")


def build_orcid_required_msg(operation: str, api_base_url: str) -> str:
    """The refusal of an operation that needs a linked ORCID iD (design 2026-10-12 §3). The
    sentence names the page that links one, which the server's generic 403 cannot."""
    return (f"{operation} needs an ORCID iD linked to your exactory account."
            f" Link one at {api_base_url}{ACCOUNT_PAGE_PATH}. Then run the command again.")
