"""Retry only a finishing write, after its costly work has already completed."""

import time

from . import storage
from .errors import ResearchError

# A finishing write records work that is already paid for: a reviewer call, a probe, an admitted run, a request to a
# remote service, an acquisition, a unit of a full-text job. It commits against the records that it read and waits
# this long for a busy store or a changed record, because a refusal can cost that work. retry_finishing_write applies
# the three values. The finish of a review call (review_protocol._finish_call) uses them with retry_store_write, and
# the captures of a review call and of a staged review task use the attempt count and the wait.
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


def retry_finishing_write(write, busy_msg, *, kept=None):
    """Call write until the store takes it, for at most SCOPED_FINISH_WAIT_SECONDS. A finishing write commits against
    the records that it read, so a busy store or a commit that changes one of those records refuses an attempt. Within
    the wait, a COMMIT that readers keep busy is tried again and the attempt keeps its replay
    (storage.retry_busy_commits_until). When the refusals last beyond the wait, the write is refused with store_busy
    and busy_msg. Its details hold the last refusal as cause, and the members of kept: values that exist only in this
    process and that the caller of the command can still use."""
    deadline = time.monotonic() + SCOPED_FINISH_WAIT_SECONDS
    try:
        with storage.retry_busy_commits_until(deadline):
            return retry_store_write(write, attempts=SCOPED_FINISH_ATTEMPT_COUNT, seconds=SCOPED_FINISH_WAIT_SECONDS,
                                     codes=SCOPED_FINISH_RETRY_CODES)
    except ResearchError as error:
        if error.code not in SCOPED_FINISH_RETRY_CODES:
            raise
        raise ResearchError('store_busy', busy_msg, dict(kept or {}, cause=error.as_dict())) from error
