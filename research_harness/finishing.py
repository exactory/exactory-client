"""Retry only a finishing write, after its costly work has already completed."""

import time

from . import storage
from .errors import ResearchError

# The finish of a review call and the records that a probe writes for itself commit against the records that they
# read. They wait this long for a busy store or a changed record, because a refusal can cost the work of a reviewer
# call or of a probe program.
SCOPED_FINISH_ATTEMPT_COUNT = 600
SCOPED_FINISH_WAIT_SECONDS = 600.0
SCOPED_FINISH_RETRY_CODES = ('store_busy', 'stale_revision', 'review_task_dependency_changed')


def retry_store_write(write, *, attempts=8, seconds=8.0, codes=('store_busy', 'stale_revision')):
    """Call write again after a refusal whose code is in codes (by default store_busy or stale_revision), at most
    attempts times within seconds of the first call; every attempt keeps Store's lock and revision checks, and any
    other refusal propagates at once."""
    deadline = time.monotonic() + seconds
    for attempt in range(attempts):
        try:
            return write()
        except ResearchError as error:
            remaining = deadline - time.monotonic()
            if error.code not in codes or attempt == attempts - 1 or remaining <= 0:
                raise
            time.sleep(min(storage._BUSY_TIMEOUT_SECONDS, remaining))
            if time.monotonic() >= deadline:
                raise
