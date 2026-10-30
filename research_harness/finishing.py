"""Retry only a finishing write, after its costly work has already completed."""

import time

from . import storage
from .errors import ResearchError


def retry_store_write(write):
    """Call write again after store_busy or stale_revision only, at most eight times within eight seconds of the
    first call; every attempt keeps Store's lock and revision checks, and any other refusal propagates at once."""
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
