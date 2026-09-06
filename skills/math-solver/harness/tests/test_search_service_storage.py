"""Command transactions preserve idempotency before filesystem observation."""

from pathlib import Path
import tempfile
import unittest

from search_controller.errors import SearchError
from search_controller.storage import Store


class ServiceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / ".search")
        self.store.initialize({}, "objective-one")

    def test_replay_and_stale_checks_precede_builder(self):
        self.assertTrue(hasattr(self.store, "append_operation"), "Locked command construction is required")
        calls = []
        identity = {"command": "render", "target": None, "spec_digest": "a" * 64}

        def build(document, content):
            calls.append(document)
            return dict(identity, operations=[], effects=[])

        first = self.store.append_operation(identity, 0, "request-one", build)
        self.assertEqual(self.store.append_operation(identity, 99, "request-one", build), first)
        with self.assertRaises(SearchError):
            self.store.append_operation(identity, 0, "request-two", build)
        with self.assertRaises(SearchError):
            self.store.append_operation(dict(identity, command="audit"), 1, "request-one", build)
        self.assertEqual(len(calls), 1)

    def test_builder_document_isolation_and_failed_validation_commit_nothing(self):
        self.assertTrue(hasattr(self.store, "append_operation"), "Locked command construction is required")
        identity = {"command": "adopt", "target": None, "spec_digest": "b" * 64}

        def build(document, content):
            document["contract"]["forged"] = True
            content.put_artifact(b"immutable snapshot")
            return dict(identity, operations=[], effects=[])

        def reject(document, event):
            self.assertEqual(document["contract"], {})
            raise SearchError("invalid_record", "Later adoption operation failed")

        with self.assertRaises(SearchError):
            self.store.append_operation(identity, 0, "request-one", build, reject)
        self.assertEqual(self.store.read()["events"], [])
        self.assertEqual(list((self.store.root / "artifacts").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
