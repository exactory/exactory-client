"""Retry only a finishing write, after its costly work has already completed."""

import time

from . import storage
from .errors import ResearchError


def retry_store_write(write):
    """Retain Store's lock and CAS checks, with at most eight attempts and an eight-second retry window."""
    deadline = time.monotonic() + 8.0
    for attempt in range(8):
        try:
            return write()
        except ResearchError as error:
            remaining = deadline - time.monotonic()
            if error.code not in ('store_busy', 'stale_revision') or attempt == 7 or remaining <= 0:
                raise
            time.sleep(min(storage._BUSY_TIMEOUT_SECONDS, remaining))
            if time.monotonic() >= deadline:
                raise
