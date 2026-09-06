import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from search_controller import SearchError, Store, canonical_bytes, safe_path
import search_controller.storage as storage_module


def _append_worker(root, value, ready, start, results):
    store = Store(Path(root))
    ready.put(True)
    start.wait()
    try:
        event = store.append("record", {"value": value}, 0, f"request-{value}")
        results.put(("committed", event["payload"]["value"]))
    except SearchError as error:
        results.put((error.code, value))


def _hold_lock(lock_path, ready, release):
    with open(lock_path, "a+b") as stream:
        storage_module.fcntl.flock(stream.fileno(), storage_module.fcntl.LOCK_EX)
        ready.put(True)
        release.wait(timeout=10)


class SearchStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "controller"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def initialized_store(self):
        store = Store(self.root)
        store.initialize({"claim": "A"}, "objective-a")
        return store

    def assert_error_code(self, code, operation):
        with self.assertRaises(SearchError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_search_error_exposes_structured_fields(self):
        error = SearchError("bad_state", "state is invalid", {"field": "events"})
        self.assertEqual(error.code, "bad_state")
        self.assertEqual(error.message, "state is invalid")
        self.assertEqual(error.details, {"field": "events"})
        self.assertEqual(str(error), "state is invalid")

    def test_canonical_bytes_are_stable_and_reject_nonfinite_numbers(self):
        self.assertEqual(
            canonical_bytes({"z": 1, "a": "é"}),
            b'{"a":"\xc3\xa9","z":1}',
        )
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assert_error_code(
                    "invalid_json", lambda value=value: canonical_bytes({"n": value})
                )

    def test_canonical_bytes_reject_non_json_keys_and_invalid_unicode(self):
        for value in ({1: "number key"}, {"text": "\ud800"}):
            with self.subTest(value=value):
                self.assert_error_code(
                    "invalid_json", lambda value=value: canonical_bytes(value)
                )

    def test_initialize_writes_exact_envelope_and_reopens_idempotently(self):
        store = self.initialized_store()
        self.assertEqual(
            store.read(),
            {
                "schema_version": 1,
                "objective_id": "objective-a",
                "contract": {"claim": "A"},
                "events": [],
            },
        )
        self.assertEqual(
            store.initialize({"claim": "A"}, "objective-a"), store.read()
        )
        self.assertEqual(
            set(json.loads((self.root / "tree.json").read_text()).keys()),
            {"schema_version", "objective_id", "contract", "events"},
        )

    def test_initialize_rejects_different_existing_identity(self):
        store = self.initialized_store()
        old_bytes = (self.root / "tree.json").read_bytes()
        self.assert_error_code(
            "already_initialized",
            lambda: store.initialize({"claim": "B"}, "objective-a"),
        )
        self.assertEqual((self.root / "tree.json").read_bytes(), old_bytes)

    def test_append_commits_sequenced_event(self):
        store = self.initialized_store()
        event = store.append("record", {"value": 1}, 0, "request-one")
        self.assertEqual(
            event,
            {
                "sequence": 1,
                "request_id": "request-one",
                "kind": "record",
                "payload": {"value": 1},
            },
        )
        self.assertEqual(store.read()["events"], [event])

    def test_stale_writer_cannot_replace_committed_event(self):
        store = Store(self.root)
        store.initialize({"claim": "A"}, "objective-a")
        store.append("record", {"value": 1}, 0, "request-one")
        with self.assertRaises(SearchError) as caught:
            store.append("record", {"value": 2}, 0, "request-two")
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(len(store.read()["events"]), 1)

    def test_identical_request_replay_precedes_revision_check_and_validation(self):
        store = self.initialized_store()
        validation_count = 0

        def validate(document, event):
            nonlocal validation_count
            validation_count += 1
            self.assertEqual(len(document["events"]), 0)
            self.assertEqual(event["sequence"], 1)

        original = store.append(
            "record", {"value": 1}, 0, "request-one", validate=validate
        )
        replay = store.append(
            "record", {"value": 1}, 0, "request-one", validate=validate
        )
        self.assertEqual(replay, original)
        self.assertEqual(validation_count, 1)

    def test_reused_request_id_with_different_input_is_rejected(self):
        store = self.initialized_store()
        store.append("record", {"value": 1}, 0, "request-one")
        for kind, payload in (
            ("other", {"value": 1}),
            ("record", {"value": 2}),
        ):
            with self.subTest(kind=kind, payload=payload):
                self.assert_error_code(
                    "request_id_conflict",
                    lambda kind=kind, payload=payload: store.append(
                        kind, payload, 1, "request-one"
                    ),
                )
        self.assertEqual(len(store.read()["events"]), 1)

    def test_validation_failure_preserves_exact_old_document_bytes(self):
        store = self.initialized_store()
        tree_path = self.root / "tree.json"
        old_bytes = tree_path.read_bytes()

        def reject(document, event):
            raise SearchError("invalid_event", "candidate rejected")

        self.assert_error_code(
            "invalid_event",
            lambda: store.append(
                "record", {"value": 1}, 0, "request-one", validate=reject
            ),
        )
        self.assertEqual(tree_path.read_bytes(), old_bytes)

    def test_validator_mutations_cannot_change_committed_state_or_candidate(self):
        store = self.initialized_store()
        first = store.append("record", {"value": 1}, 0, "request-one")

        def mutate(document, candidate):
            document["objective_id"] = "rewritten-objective"
            document["contract"]["claim"] = "rewritten-claim"
            document["events"][0]["request_id"] = "rewritten-request"
            document["events"][0]["kind"] = "rewritten-kind"
            document["events"][0]["payload"]["value"] = 999
            candidate["request_id"] = "rewritten-candidate-request"
            candidate["kind"] = "rewritten-candidate-kind"
            candidate["payload"]["value"] = 999

        second = store.append(
            "record", {"value": 2}, 1, "request-two", validate=mutate
        )

        self.assertEqual(
            second,
            {
                "sequence": 2,
                "request_id": "request-two",
                "kind": "record",
                "payload": {"value": 2},
            },
        )
        self.assertEqual(
            store.read(),
            {
                "schema_version": 1,
                "objective_id": "objective-a",
                "contract": {"claim": "A"},
                "events": [first, second],
            },
        )

    def test_interrupted_replace_preserves_exact_old_document_bytes(self):
        store = self.initialized_store()
        tree_path = self.root / "tree.json"
        old_bytes = tree_path.read_bytes()
        with mock.patch("search_controller.storage.os.replace", side_effect=OSError("cut")):
            with self.assertRaises(OSError):
                store.append("record", {"value": 1}, 0, "request-one")
        self.assertEqual(tree_path.read_bytes(), old_bytes)
        self.assertEqual(list(self.root.glob(".tree.json.*.tmp")), [])

    def test_two_writers_serialize_and_only_one_zero_revision_append_commits(self):
        self.initialized_store()
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_append_worker,
                args=(str(self.root), value, ready, start, results),
            )
            for value in (1, 2)
        ]
        for process in processes:
            process.start()
        for _ in processes:
            self.assertTrue(ready.get(timeout=10))
        start.set()
        observed = [results.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(
            sorted(status for status, _ in observed),
            ["committed", "revision_conflict"],
        )
        events = Store(self.root).read()["events"]
        self.assertEqual(len(events), 1)
        self.assertIn(events[0]["payload"]["value"], (1, 2))

    def test_writer_lock_times_out_without_changing_committed_bytes(self):
        if storage_module.fcntl is None:
            self.skipTest("fcntl is unavailable")
        store = self.initialized_store()
        tree_path = self.root / "tree.json"
        old_bytes = tree_path.read_bytes()
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        release = context.Event()
        process = context.Process(
            target=_hold_lock,
            args=(str(self.root / "tree.lock"), ready, release),
        )
        process.start()
        try:
            self.assertTrue(ready.get(timeout=10))
            started = time.monotonic()
            with mock.patch.object(storage_module, "LOCK_TIMEOUT_SECONDS", 0.05):
                with mock.patch.object(storage_module, "LOCK_RETRY_SECONDS", 0.005):
                    self.assert_error_code(
                        "lock_timeout",
                        lambda: store.append(
                            "record", {"value": 1}, 0, "request-one"
                        ),
                    )
            elapsed = time.monotonic() - started
        finally:
            release.set()
            process.join(timeout=10)
        self.assertEqual(process.exitcode, 0)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(tree_path.read_bytes(), old_bytes)

    def test_read_rejects_duplicate_keys_and_nonfinite_numbers(self):
        store = self.initialized_store()
        corrupt_documents = (
            (
                b'{"schema_version":1,"schema_version":1,'
                b'"objective_id":"o","contract":{},"events":[]}'
            ),
            b'{"schema_version":1,"objective_id":"o","contract":{"n":NaN},"events":[]}',
            b'{"schema_version":1,"objective_id":"o","contract":{"n":Infinity},"events":[]}',
            b"[" * 2000 + b"]" * 2000,
        )
        for raw in corrupt_documents:
            with self.subTest(raw=raw):
                (self.root / "tree.json").write_bytes(raw)
                self.assert_error_code("corrupt_state", store.read)

    def test_read_rejects_malformed_envelopes_and_revisions(self):
        valid = {
            "schema_version": 1,
            "objective_id": "objective-a",
            "contract": {"claim": "A"},
            "events": [],
        }
        malformed = []
        for missing in valid:
            candidate = dict(valid)
            del candidate[missing]
            malformed.append(candidate)
        malformed.extend(
            [
                {**valid, "extra": True},
                {**valid, "schema_version": 2},
                {**valid, "objective_id": ""},
                {**valid, "contract": []},
                {**valid, "events": {}},
                {
                    **valid,
                    "events": [
                        {
                            "sequence": 0,
                            "request_id": "r",
                            "kind": "record",
                            "payload": {},
                        }
                    ],
                },
                {
                    **valid,
                    "events": [
                        {
                            "sequence": True,
                            "request_id": "r",
                            "kind": "record",
                            "payload": {},
                        }
                    ],
                },
                {
                    **valid,
                    "events": [
                        {
                            "sequence": 1,
                            "request_id": "r",
                            "kind": "record",
                            "payload": {},
                            "extra": True,
                        }
                    ],
                },
            ]
        )
        store = self.initialized_store()
        for document in malformed:
            with self.subTest(document=document):
                (self.root / "tree.json").write_bytes(canonical_bytes(document))
                self.assert_error_code("corrupt_state", store.read)

    def test_read_rejects_unencodable_metadata_strings(self):
        store = self.initialized_store()
        for field in ("objective_id", "request_id", "kind"):
            document = {
                "schema_version": 1,
                "objective_id": "objective-a",
                "contract": {"claim": "A"},
                "events": [
                    {
                        "sequence": 1,
                        "request_id": "request-one",
                        "kind": "record",
                        "payload": {"value": 1},
                    }
                ],
            }
            if field == "objective_id":
                document[field] = "\ud800"
            else:
                document["events"][0][field] = "\ud800"
            with self.subTest(field=field):
                raw = json.dumps(document, ensure_ascii=True).encode("utf-8")
                (self.root / "tree.json").write_bytes(raw)
                self.assert_error_code("corrupt_state", store.read)

    def test_append_rejects_malformed_inputs_without_modifying_document(self):
        store = self.initialized_store()
        tree_path = self.root / "tree.json"
        old_bytes = tree_path.read_bytes()
        calls = (
            lambda: store.append("record", {}, True, "r"),
            lambda: store.append("record", {}, -1, "r"),
            lambda: store.append("", {}, 0, "r"),
            lambda: store.append("record", {}, 0, ""),
            lambda: store.append("record", [], 0, "r"),
        )
        for call in calls:
            with self.subTest(call=call):
                self.assert_error_code("invalid_input", call)
                self.assertEqual(tree_path.read_bytes(), old_bytes)

    def test_blob_round_trip_and_corruption_detection(self):
        store = self.initialized_store()
        value = {"claim": "A", "cases": [1, 2]}
        digest = store.put_blob(value)
        self.assertEqual(digest, hashlib.sha256(canonical_bytes(value)).hexdigest())
        self.assertEqual(store.get_blob(digest), value)
        blob_path = self.root / "blobs" / f"{digest}.json"
        blob_path.write_bytes(b'{"claim":"changed"}')
        self.assert_error_code("corrupt_blob", lambda: store.get_blob(digest))

    def test_blob_rejects_a_finite_syntax_number_that_overflows_when_decoded(self):
        store = self.initialized_store()
        raw = b'{"number":1e400}'
        digest = hashlib.sha256(raw).hexdigest()
        (self.root / "blobs" / f"{digest}.json").write_bytes(raw)
        self.assert_error_code("corrupt_blob", lambda: store.get_blob(digest))

    def test_immutable_blob_collision_is_rejected(self):
        store = self.initialized_store()
        value = {"claim": "A"}
        digest = hashlib.sha256(canonical_bytes(value)).hexdigest()
        blob_path = self.root / "blobs" / f"{digest}.json"
        blob_path.parent.mkdir(exist_ok=True)
        blob_path.write_bytes(b"not the requested content")
        self.assert_error_code("immutable_collision", lambda: store.put_blob(value))
        self.assertEqual(blob_path.read_bytes(), b"not the requested content")

    def test_artifact_round_trip_and_digest_checks(self):
        store = self.initialized_store()
        content = b"proof output\x00\xff"
        digest = store.put_artifact(content)
        self.assertEqual(digest, hashlib.sha256(content).hexdigest())
        self.assertEqual(store.get_artifact(digest), content)
        artifact_path = self.root / "artifacts" / digest
        artifact_path.write_bytes(b"tampered")
        self.assert_error_code("corrupt_artifact", lambda: store.get_artifact(digest))

    def test_artifact_collision_is_rejected_without_overwrite(self):
        store = self.initialized_store()
        content = b"proof output"
        digest = hashlib.sha256(content).hexdigest()
        artifact_path = self.root / "artifacts" / digest
        artifact_path.parent.mkdir(exist_ok=True)
        artifact_path.write_bytes(b"existing wrong bytes")
        self.assert_error_code(
            "immutable_collision", lambda: store.put_artifact(content)
        )
        self.assertEqual(artifact_path.read_bytes(), b"existing wrong bytes")

    def test_digest_arguments_cannot_escape_content_directories(self):
        store = self.initialized_store()
        for digest in ("../tree", "/tmp/tree", "a" * 63, "g" * 64):
            with self.subTest(digest=digest):
                self.assert_error_code(
                    "invalid_digest", lambda digest=digest: store.get_blob(digest)
                )
                self.assert_error_code(
                    "invalid_digest", lambda digest=digest: store.get_artifact(digest)
                )

    def test_safe_path_rejects_absolute_traversal_and_escaped_symlink(self):
        self.root.mkdir()
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        (self.root / "escape").symlink_to(outside, target_is_directory=True)
        self.assertEqual(safe_path(self.root, "inside/file"), self.root / "inside/file")
        for relative in (
            "../outside",
            "inside/../file",
            "/tmp/outside",
            "escape/file",
        ):
            with self.subTest(relative=relative):
                self.assert_error_code(
                    "unsafe_path",
                    lambda relative=relative: safe_path(self.root, relative),
                )

    def test_safe_path_can_validate_a_path_below_a_not_yet_created_root(self):
        self.assertEqual(
            safe_path(self.root, "inside/file"), self.root / "inside/file"
        )

    def test_symlinked_controller_directory_is_rejected(self):
        real_root = Path(self.temporary_directory.name) / "real-controller"
        real_root.mkdir()
        self.root.symlink_to(real_root, target_is_directory=True)
        self.assert_error_code(
            "unsafe_path",
            lambda: Store(self.root).initialize({"claim": "A"}, "objective-a"),
        )


if __name__ == "__main__":
    unittest.main()
