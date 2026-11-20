"""Another writer of a test store, a held write lock and a held reader, for the tests of the finishing writes: the
writes that record paid work, and the records before and after a reviewer program."""

import sqlite3
import time
from unittest import mock

from research_harness import finishing, storage
from research_harness.errors import ResearchError
from research_harness.storage import Store

ORIGINAL_MUTATE = Store.mutate
# The commit entries of the store. A release before the one that adds mutate_serialized has the first two.
COMMIT_ENTRIES = [name for name in ("mutate", "mutate_scoped", "mutate_serialized") if hasattr(Store, name)]


class WaitlessTime:
    """What research_harness.finishing sees as the time module, and no other module: it never sleeps. With ticking,
    each reading of its clock passes ten seconds, so that the wait of a finishing write ends without real time."""

    def __init__(self, ticking=False):
        self.ticking, self.now = ticking, 0.0

    def sleep(self, seconds):
        pass

    def monotonic(self):
        if not self.ticking:
            return time.monotonic()
        self.now += 10.0
        return self.now


class OtherWriters:
    """Another writer of the store of a test case, a held write lock, and a reader. The case gives self.root."""

    def start_other_writers(self):
        self.unrelated_count = 0
        self.attempts = {}
        waits = mock.patch.object(finishing, "time", WaitlessTime())
        waits.start()
        self.addCleanup(waits.stop)

    def commit_unrelated(self):
        """One commit of another writer: a record of a kind that no finishing write reads."""
        self.unrelated_count += 1
        number = self.unrelated_count
        other = Store(self.root)
        ORIGINAL_MUTATE(other, "fixture.unrelated", {"n": number},
                        lambda tx: tx.put("fixture_unrelated", "row-" + str(number), {"n": number}),
                        expected_revision=other.revision, request_id="unrelated-" + str(number))

    def meet_commits(self, operations, act=None, limit=None):
        """Before each commit of these operations another writer acts (by default it commits one unrelated record), at
        most limit times. Every commit entry of the store is wrapped, so a test does not depend on which of them a
        write uses. Returns a function that ends the meeting."""
        act = act or self.commit_unrelated
        patches = []

        def wrap(original):
            def commit(store, operation, payload, apply, **kwargs):
                if operation in operations:
                    count = self.attempts[operation] = self.attempts.get(operation, 0) + 1
                    if limit is None or count <= limit:
                        act()
                return original(store, operation, payload, apply, **kwargs)
            return commit
        for name in COMMIT_ENTRIES:
            patch = mock.patch.object(Store, name, wrap(getattr(Store, name)))
            patch.start()
            patches.append(patch)
            self.addCleanup(patch.stop)
        return lambda: [patch.stop() for patch in patches]

    def hold_write_lock(self):
        connection = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        self.addCleanup(connection.close)
        return connection

    def hold_read_lock(self):
        """A reader in the middle of a read: it holds the SHARED lock of the store until its transaction ends."""
        connection = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("BEGIN")
        connection.execute("SELECT revision FROM metadata").fetchone()
        return connection

    def observe_commits(self, refused, on_refusal):
        """Open every connection of the store so that a COMMIT that SQLite refuses is counted, and on_refusal runs
        before the refusal is raised."""
        connect = sqlite3.connect

        class ObservedConnection(sqlite3.Connection):
            def commit(self):
                try:
                    return super().commit()
                except sqlite3.OperationalError:
                    refused.append(True)
                    on_refusal()
                    raise
        return mock.patch.object(storage.sqlite3, "connect",
                                 lambda *args, **kwargs: connect(*args, factory=ObservedConnection, **kwargs))

    def refuse_beyond_the_wait(self, operation, run):
        """Hold the write lock from the first commit of operation on, until run is refused; then release it and end
        the meeting."""
        held = []
        end = self.meet_commits((operation,), act=lambda: held or held.append(self.hold_write_lock()))
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01), \
                mock.patch.object(finishing, "time", WaitlessTime(ticking=True)):
            with self.assertRaises(ResearchError) as caught:
                run()
        held[0].execute("ROLLBACK")
        end()
        return caught.exception
