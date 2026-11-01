"""Task preparation retains authenticated dependencies across sibling commits."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from research_harness import operations, storage
from research_harness.errors import ResearchError
from research_harness.storage import Store


class ScopedStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.store = Store(self.root, create=True)
        self.put("task", "a", {"generation": 1})
        self.put("task", "b", {"generation": 1})
        self.put("selector", "a", {"version": 1})

    def put(self, kind, key, value):
        return self.store.mutate("fixture", {"kind": kind, "key": key, "value": value},
                                 lambda tx: tx.put(kind, key, value), expected_revision=self.store.revision,
                                 request_id="fixture-" + str(self.store.revision))

    def refuse(self, code, action):
        with self.assertRaises(ResearchError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def prepare(self, records, payload):
        own = records["task"]["a"]
        selector = records["selector"]["a"]
        self.assertNotIn("a", records.get("accepted", {}))
        return [("accepted", "a", {"task": own, "selector": selector})], {"ok": True}

    def mutate(self, prepare=None, **kwargs):
        return operations.prepared_task_mutation(self.store, "task.accept", {"task_id": "a"},
                                                 prepare or self.prepare, request_id="accept", **kwargs)

    def test_sibling_commit_preserves_preparation_and_uses_actual_commit_revision(self):
        def prepare(records, payload):
            result = self.prepare(records, payload)
            self.put("task", "b", {"generation": 2})
            return result
        def finalize(changes, result, revision):
            changes[0][2]["recorded_revision"] = revision
            return changes, dict(result, recorded_revision=revision)
        result = self.mutate(prepare, finalize=finalize, expected_revision=self.store.revision)
        self.assertEqual(result["revision"], 5)
        self.assertEqual(result["result"]["recorded_revision"], 5)
        self.assertEqual(self.store.snapshot()["records"]["accepted"]["a"]["recorded_revision"], 5)

    def test_own_task_selector_or_dependency_changes_invalidate(self):
        for kind, key, value in (("task", "a", {"generation": 2}),
                                 ("selector", "a", {"version": 2})):
            def prepare(records, payload):
                result = self.prepare(records, payload)
                self.put(kind, key, value)
                return result
            self.refuse("review_task_dependency_changed", lambda: self.mutate(prepare))
        self.assertNotIn("accepted", self.store.snapshot()["records"])

    def test_same_bytes_rewritten_still_invalidate_selector_version(self):
        def prepare(records, payload):
            result = self.prepare(records, payload)
            self.put("selector", "a", {"version": 1})
            return result
        self.refuse("review_task_dependency_changed", lambda: self.mutate(prepare))

    def test_absence_predicate_revalidated_including_an_absent_kind(self):
        def prepare(records, payload):
            result = self.prepare(records, payload)
            self.put("accepted", "a", {"other": True})
            return result
        self.refuse("review_task_dependency_changed", lambda: self.mutate(prepare))
        self.assertEqual(self.store.snapshot()["records"]["accepted"]["a"], {"other": True})

    def test_sibling_insert_does_not_break_key_absence(self):
        def prepare(records, payload):
            result = self.prepare(records, payload)
            self.put("accepted", "b", {"ok": True})
            return result
        self.assertTrue(self.mutate(prepare)["result"]["ok"])

    def test_iteration_binds_membership_and_blind_write_binds_target(self):
        def prepare(records, payload):
            list(records["task"])
            self.put("task", "c", {"generation": 1})
            return [], {}
        self.refuse("review_task_dependency_changed", lambda: self.mutate(prepare))
        original = self.store.mutate_scoped
        def intervene(*args, **kwargs):
            self.put("accepted", "a", {"other": True})
            return original(*args, **kwargs)
        with mock.patch.object(self.store, "mutate_scoped", side_effect=intervene):
            self.refuse("review_task_dependency_changed", lambda: self.mutate(lambda r, p: (
                [("accepted", "a", {"blind": True})], {})))

    def test_receipt_replay_and_conflict_precede_stale_dependencies(self):
        original = self.mutate()
        self.put("task", "a", {"generation": 7})
        with mock.patch.object(self, "prepare", side_effect=AssertionError("No preparation on replay")):
            self.assertEqual(self.mutate(expected_revision=0), original)
        self.refuse("request_id_conflict", lambda: operations.prepared_task_mutation(
            self.store, "task.accept", {"task_id": "changed"}, lambda r, p: None,
            request_id="accept", expected_revision=0))
        self.refuse("request_id_conflict", lambda: self.store.mutate_scoped(
            "changed", {"task_id": "a"}, lambda tx, revision: None,
            request_id="accept", _preparation={}))

    def test_reject_author_maps_and_foreign_store_proofs(self):
        self.refuse("invalid_preparation", lambda: self.store.mutate_scoped(
            "task.accept", {}, lambda tx, revision: {}, request_id="untrusted", _preparation={}))
        other = Store(self.root.parent / "other", create=True)
        proof = other._read_scoped_preparation().release_proof()
        self.refuse("invalid_preparation", lambda: self.store.mutate_scoped(
            "task.accept", {}, lambda tx, revision: {}, request_id="foreign", _preparation=proof))

    def test_full_history_validation_retained_after_preparation(self):
        def prepare(records, payload):
            result = self.prepare(records, payload)
            with sqlite3.connect(str(self.root / ".exactory/research.sqlite3")) as connection:
                connection.execute("UPDATE records SET value = '{}' WHERE kind = 'task' AND key = 'b'")
            return result
        self.refuse("corrupt_state", lambda: self.mutate(prepare))

    def test_scoped_commit_rolls_back_callback_failure(self):
        proof = self.store._read_scoped_preparation().release_proof()
        before = self.store.snapshot()
        def apply(tx, revision):
            tx.put("accepted", "a", {"ok": True})
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.store.mutate_scoped("interrupt", {}, apply, request_id="interrupt", _preparation=proof)
        self.assertEqual(self.store.snapshot(), before)

    def test_ordinary_prepared_mutation_still_uses_global_cas(self):
        def prepare(records, payload):
            self.put("task", "b", {"generation": 3})
            return [], {}
        self.refuse("stale_revision", lambda: operations.prepared_mutation(
            self.store, "ordinary", {}, prepare, request_id="ordinary", expected_revision=self.store.revision))
