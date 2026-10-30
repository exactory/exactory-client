"""Retry only a finishing write, after its costly work has already completed."""

import time

from . import storage
from .errors import ResearchError


def retry_store_write(write, *, attempts=8, seconds=8.0):
    """Call write again after store_busy or stale_revision only, at most attempts times within seconds of the first
    call; every attempt keeps Store's lock and revision checks, and any other refusal propagates at once."""
    deadline = time.monotonic() + seconds
    for attempt in range(attempts):
        try:
            return write()
        except ResearchError as error:
            remaining = deadline - time.monotonic()
            if error.code not in ('store_busy', 'stale_revision') or attempt == attempts - 1 or remaining <= 0:
                raise
            time.sleep(min(storage._BUSY_TIMEOUT_SECONDS, remaining))
            if time.monotonic() >= deadline:
                raise
