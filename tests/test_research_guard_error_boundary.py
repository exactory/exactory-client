"""A shared snapshot lock preserves failures raised by its native caller."""

import tempfile
import unittest
from pathlib import Path

from research_harness.storage import Store


class SharedGuardErrorBoundaryTests(unittest.TestCase):
    def test_caller_oserror_keeps_identity_and_releases_snapshot_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            failure = OSError("Native commit completed before publication failed")
            with self.assertRaises(OSError) as caught:
                with store.guarded_snapshot():
                    raise failure
            self.assertIs(caught.exception, failure)
            revision = store.revision
            with store.guarded_snapshot() as guard:
                self.assertEqual(guard.snapshot()["revision"], revision)

    def test_missing_native_file_is_not_reported_as_a_missing_common_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            failure = FileNotFoundError("Native deliverable was removed during publication")
            with self.assertRaises(FileNotFoundError) as caught:
                with store.guarded_snapshot():
                    raise failure
            self.assertIs(caught.exception, failure)
            self.assertEqual(store.revision, 0)


if __name__ == "__main__":
    unittest.main()
