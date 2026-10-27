"""Artifact preparation shares authentication without weakening commit validation."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import weakref

from research_harness import cli, integration, operations, probe_provenance, storage
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from research_harness.storage import Store


class _InterruptConnection:
    def __init__(self, connection, stage):
        self._connection, self._stage = connection, stage

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def execute(self, sql, *args):
        result = self._connection.execute(sql, *args)
        prefixes = {"record": "INSERT OR REPLACE INTO records", "event": "INSERT INTO events",
                    "receipt": "INSERT INTO receipts"}
        if self._stage in prefixes and sql.startswith(prefixes[self._stage]):
            raise KeyboardInterrupt(self._stage)
        return result

    def commit(self):
        if self._stage == "commit" and self._connection.total_changes:
            raise KeyboardInterrupt("commit")
        return self._connection.commit()


class ResearchStorePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "study"
        self.store = Store(self.root, create=True)
        self.database = self.root / ".exactory/research.sqlite3"
        (self.root / "authored.txt").write_bytes(b"A local authored artifact.\n")
        self.payload = {"id": "authored", "path": "authored.txt", "media_type": "text/plain"}

    def build_cli_args(self, payload=None, revision=0, request="pin"):
        path = self.directory / "payload.json"
        path.write_text(json.dumps(self.payload if payload is None else payload))
        return ["artifact", "--workspace", str(self.root), "--file", str(path),
                "--expected-revision", str(revision), "--request-id", request]

    def invoke_cli(self, payload=None, revision=0, request="pin"):
        return cli.run(cli.build_parser().parse_args(self.build_cli_args(payload, revision, request)))

    def assert_refusal(self, code, action):
        with self.assertRaises(ResearchError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def seed_store(self, store=None, value="original", revision=0, request="seed"):
        store = self.store if store is None else store
        return store.mutate("fixture.seed", {"value": value},
                            lambda tx: tx.put("fixture", "seed", {"value": value}),
                            expected_revision=revision, request_id=request)

    def read_counts(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return tuple(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                         for table in ("records", "events", "receipts"))

    def read_preparation(self, store=None):
        store = self.store if store is None else store
        self.assertTrue(callable(getattr(store, "_read_preparation", None)),
                        "Storage must issue an owned authenticated preparation")
        return store._read_preparation()

    def test_register_artifact_with_two_full_validations_and_one_structural_check(self):
        calls = []
        original = storage._validate

        def count_validation(connection, **kwargs):
            calls.append((kwargs.get("replay", True), connection.execute("PRAGMA query_only").fetchone()[0]))
            return original(connection, **kwargs)

        with mock.patch.object(storage, "_validate", side_effect=count_validation):
            receipt = self.invoke_cli()
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(sum(replay for replay, _ in calls), 2, calls)
        self.assertEqual(calls.count((False, 1)), 1, calls)
        self.assertEqual(calls.count((True, 0)), 1, calls)
        self.assertEqual(self.read_counts(), (2, 1, 1))

    def test_keep_structural_validation_from_issuing_content_proof(self):
        fingerprint = hashlib.sha256()
        before = fingerprint.hexdigest()
        with self.store._connection() as connection:
            self.assertEqual(storage._validate(connection, replay=False, fingerprint=fingerprint), 0)
        self.assertEqual(fingerprint.hexdigest(), before)

    def test_share_the_owned_record_view_between_classification_and_preparation(self):
        self.seed_store()
        observed = []
        original_classify = probe_provenance.should_stage_probe_artifact_pin
        original_immutable = integration.immutable_record

        def classify(records, payload, request_id):
            observed.append(records)
            with self.assertRaises(TypeError):
                records["fixture"]["injected"] = {"value": "untrusted"}
            records["fixture"]["seed"]["value"] = "caller changed a defensive read"
            return original_classify(records, payload, request_id)

        def prepare_record(records, *args):
            self.assertIs(records, observed[0])
            self.assertEqual(records["fixture"]["seed"], {"value": "original"})
            return original_immutable(records, *args)

        with mock.patch.object(probe_provenance, "should_stage_probe_artifact_pin", side_effect=classify), \
                mock.patch.object(integration, "immutable_record", side_effect=prepare_record):
            self.invoke_cli(revision=1)
        self.assertEqual(len(observed), 1)

    def test_release_preparation_records_and_pass_only_an_immutable_proof_to_mutate(self):
        views, operations_seen = [], []
        original_read = Store._read_preparation
        original_mutate = Store.mutate

        def read_preparation(store):
            snapshot = original_read(store)
            views.append(weakref.ref(snapshot.records))
            return snapshot

        def mutate(store, operation, *args, **kwargs):
            if operation != "fixture.probe":
                proof = kwargs.get("_preparation")
                self.assertFalse(hasattr(proof, "records"), "Store.mutate received a preparation record view")
                self.assertIsInstance(proof, storage._PreparationProof)
                self.assertEqual(proof._fields, ("identity", "revision", "fingerprint"))
                self.assertIsInstance(proof.identity, tuple)
                self.assertIsInstance(proof.revision, int)
                self.assertIsInstance(proof.fingerprint, bytes)
                for field in proof._fields:
                    with self.assertRaises(AttributeError):
                        setattr(proof, field, None)
                self.assertTrue(all(reference() is None for reference in views),
                                "Preparation records survived into independent write validation")
                operations_seen.append(operation)
            return original_mutate(store, operation, *args, **kwargs)

        with mock.patch.object(Store, "_read_preparation", new=read_preparation), \
                mock.patch.object(Store, "mutate", new=mutate):
            receipt = self.invoke_cli()
            self.assertEqual(self.invoke_cli(), receipt)
            self.root = self.directory / "probe-study"
            self.store = Store(self.root, create=True)
            payload = self.save_probe_launch()
            receipt = self.invoke_cli(payload, revision=1)
            self.assertEqual(probe_provenance.pin_probe_local_artifact(
                self.store, payload, expected_revision=1, request_id="pin"), receipt)
        self.assertEqual(operations_seen, ["workspace.artifact", "workspace.artifact",
            "workspace.probe_artifact_intent", "workspace.artifact", "workspace.artifact"])

    def test_refuse_dict_mutators_on_the_owned_record_view(self):
        self.seed_store()
        records = self.read_preparation().records
        for view in (records, records["fixture"]):
            for name in ("update", "pop", "popitem", "clear", "setdefault"):
                with self.subTest(view=type(view).__name__, name=name):
                    with self.assertRaises(AttributeError):
                        getattr(view, name)
        self.assertEqual(records["fixture"]["seed"], {"value": "original"})

    def test_keep_membership_and_overlay_copy_from_copying_record_values(self):
        self.seed_store()
        preparation = self.read_preparation()
        records = preparation.records
        with mock.patch.object(storage.copy, "deepcopy", side_effect=AssertionError("Copied record JSON")):
            self.assertIn("fixture", records)
            self.assertIn("seed", records["fixture"])
            self.assertNotIn("missing", records["fixture"])
            overlay = records.copy()
        overlay["fixture"] = {"seed": {"value": "prospective"}}
        self.assertEqual(records["fixture"]["seed"], {"value": "original"})
        self.assertEqual(overlay["fixture"]["seed"], {"value": "prospective"})

    def test_keep_the_preparation_record_view_and_revision_read_only(self):
        preparation = self.read_preparation()
        with self.assertRaises(AttributeError):
            preparation.records = {"fixture": {"unvalidated": {}}}
        with self.assertRaises(AttributeError):
            preparation.revision = 1

    def test_copy_managed_preparation_preserves_historical_reads_and_isolation(self):
        self.seed_store()
        def configure(transaction):
            transaction.put("configuration", "research", {"profile": "research"})
            transaction.put("workspace", "study", {"stage": "literature"})
            transaction.put("fixture", "seed", {"value": "current"})
        self.store.mutate("fixture.configure", {}, configure, expected_revision=1, request_id="configure")
        records = self.read_preparation().records
        overlay = records.copy()
        self.assertEqual(overlay.read_revision(), 2)
        self.assertEqual(overlay.read_snapshot(1)["fixture"]["seed"], {"value": "original"})
        identity = hashlib.sha256(b'{"value":"original"}').hexdigest()
        self.assertTrue(overlay.verify_record_version("fixture", "seed", 1, identity))
        self.assertEqual(overlay.read_record("fixture", "seed", identity), {"value": "original"})
        overlay["fixture"] = {"seed": {"value": "prospective"}}
        self.assertEqual(records["fixture"]["seed"], {"value": "current"})
        self.assertEqual(overlay.read_snapshot(2)["fixture"]["seed"], {"value": "current"})

    def test_refuse_an_intervening_writer_without_publishing_the_prepared_patch(self):
        original = ArtifactStore.put

        def interleave(artifacts, *args):
            result = original(artifacts, *args)
            self.seed_store()
            return result

        with mock.patch.object(ArtifactStore, "put", new=interleave):
            self.assert_refusal("stale_revision", self.invoke_cli)
        self.assertEqual(self.store.snapshot(), {"revision": 1, "records": {
            "fixture": {"seed": {"value": "original"}}}})
        self.assertIsNone(self.store.committed_request("pin"))
        self.assertEqual(self.read_counts(), (1, 1, 1))

    def test_replay_exact_original_response_before_and_after_later_revisions_once(self):
        original = storage.Transaction.put
        staged = []

        def count_put(transaction, kind, *args):
            if kind == "local_artifact":
                staged.append(kind)
            return original(transaction, kind, *args)

        with mock.patch.object(storage.Transaction, "put", new=count_put):
            receipt = self.invoke_cli()
            self.assertEqual(self.invoke_cli(), receipt)
            self.seed_store(revision=1)
            self.assertEqual(self.invoke_cli(), receipt)
        self.assertEqual(staged, ["local_artifact"])
        self.assertEqual(self.read_counts(), (3, 2, 2))
        with closing(sqlite3.connect(self.database)) as connection:
            original_response = connection.execute("SELECT response FROM receipts WHERE request_id='pin'").fetchone()[0]
        encoded = json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(original_response, encoded)

    def test_refuse_changed_canonical_payload_before_stale_revision_or_source_access(self):
        self.invoke_cli()
        self.seed_store(revision=1)
        (self.root / self.payload["path"]).unlink()
        changed = dict(self.payload, media_type="application/octet-stream")
        self.assert_refusal("request_id_conflict", lambda: self.invoke_cli(changed))
        self.assertEqual(self.read_counts(), (3, 2, 2))

    def test_refuse_changed_operation_before_stale_revision_without_preparation(self):
        self.invoke_cli()
        self.seed_store(revision=1)
        self.assert_refusal("request_id_conflict", lambda: operations.prepared_mutation(
            self.store, "different.operation", self.payload, lambda *_: self.fail("Ran conflicting preparation"),
            expected_revision=0, request_id="pin"))

    def test_replay_after_the_original_artifact_is_changed_or_removed(self):
        receipt = self.invoke_cli()
        (self.root / self.payload["path"]).write_bytes(b"Changed after commit.\n")
        self.assertEqual(self.invoke_cli(), receipt)
        (self.root / self.payload["path"]).unlink()
        self.assertEqual(self.invoke_cli(), receipt)
        self.assertEqual(self.store.revision, 1)

    def save_probe_launch(self):
        launch = {"id": "probe", "directory": "research/probes/probe/run-fixture", "output_dir": "results"}
        def apply(transaction):
            transaction.put("probe_intent", "probe", {"id": "probe"})
            transaction.put("probe_launch", "probe", launch)
        self.store.mutate("fixture.probe", {}, apply, expected_revision=0, request_id="probe")
        path = launch["directory"] + "/stdout"
        (self.root / path).parent.mkdir(parents=True)
        (self.root / path).write_bytes(b"Probe output.\n")
        return dict(self.payload, path=path)

    def test_stage_probe_bound_bytes_with_origins_before_publication(self):
        payload = self.save_probe_launch()
        original = ArtifactStore.put
        def check_origin(artifacts, data, media_type):
            records = self.store.snapshot()["records"]
            self.assertIn("pin", records["probe_artifact_pin"])
            self.assertEqual(len(records["probe_artifact"]), 1)
            self.assertNotIn("local_artifact", records)
            return original(artifacts, data, media_type)
        with mock.patch.object(ArtifactStore, "put", new=check_origin):
            receipt = self.invoke_cli(payload, revision=1)
        self.assertEqual(receipt["revision"], 3)
        self.assertEqual(receipt["result"]["scientific_validation"], False)

    def test_resume_reserved_probe_pin_without_reclassifying_it_as_ordinary(self):
        payload = self.save_probe_launch()
        with mock.patch.object(ArtifactStore, "put", side_effect=KeyboardInterrupt("after reservation")):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke_cli(payload, revision=1)
        self.assertEqual(self.store.revision, 2)
        self.assertIsNone(self.store.committed_request("pin"))
        self.assert_refusal("request_id_conflict", lambda: self.invoke_cli(self.payload, revision=1))
        receipt = self.invoke_cli(payload, revision=1)
        (self.root / payload["path"]).unlink()
        self.assertEqual(self.invoke_cli(payload, revision=1), receipt)
        self.assertEqual(self.store.revision, 3)

    def corrupt_store(self, target):
        with closing(sqlite3.connect(self.database)) as connection:
            with connection:
                if target == "history":
                    connection.execute("DROP TRIGGER events_no_update")
                    connection.execute("UPDATE events SET digest = ?", ("0" * 64,))
                    connection.execute(storage._SCHEMA[("trigger", "events_no_update")])
                elif target == "receipt":
                    connection.execute("DROP TRIGGER receipts_no_update")
                    connection.execute("UPDATE receipts SET digest = ?", ("0" * 64,))
                    connection.execute(storage._SCHEMA[("trigger", "receipts_no_update")])
                elif target == "projection":
                    connection.execute("UPDATE records SET value = '{}' ")
                else:
                    connection.execute("DROP TRIGGER receipts_no_delete")

    def test_reauthenticate_history_receipts_projection_and_guards_after_preparation(self):
        self.seed_store()
        backup = self.directory / "untampered.sqlite3"
        shutil.copyfile(self.database, backup)
        original = ArtifactStore.put
        for target in ("history", "receipt", "projection", "guard"):
            with self.subTest(target=target):
                shutil.copyfile(backup, self.database)
                def tamper(artifacts, *args):
                    result = original(artifacts, *args)
                    self.corrupt_store(target)
                    return result
                with mock.patch.object(ArtifactStore, "put", new=tamper):
                    self.assert_refusal("corrupt_state", lambda: self.invoke_cli(revision=1))
                self.assertEqual(self.read_counts(), (1, 1, 1))
        shutil.copyfile(backup, self.database)
        self.assertIsNone(self.store.committed_request("pin"))

    def test_refuse_valid_same_revision_replacement_by_inode_or_authenticated_content(self):
        self.seed_store()
        backup = self.directory / "original.sqlite3"
        shutil.copyfile(self.database, backup)
        original = ArtifactStore.put
        for mode in ("replace", "same-inode"):
            with self.subTest(mode=mode):
                shutil.copyfile(backup, self.database)
                other = Store(self.directory / mode, create=True)
                self.seed_store(other, value="different valid history")
                def replace_store(artifacts, *args):
                    result = original(artifacts, *args)
                    if mode == "replace":
                        os.replace(other._path, self.database)
                    else:
                        inode = self.database.stat().st_ino
                        with closing(sqlite3.connect(other._path)) as source:
                            with closing(sqlite3.connect(self.database)) as destination:
                                source.backup(destination)
                        self.assertEqual(self.database.stat().st_ino, inode)
                    return result
                with mock.patch.object(ArtifactStore, "put", new=replace_store):
                    error = self.assert_refusal("stale_revision", lambda: self.invoke_cli(revision=1))
                self.assertEqual(error.details["reason"], "snapshot_identity_mismatch")
                self.assertIn("snapshot", error.message.lower())
                self.assertEqual(self.store.snapshot()["records"], {
                    "fixture": {"seed": {"value": "different valid history"}}})
                self.assertIsNone(self.store.committed_request("pin"))
                self.assertEqual(self.read_counts(), (1, 1, 1))

    def test_refuse_changed_authenticated_history_even_when_current_records_match(self):
        self.seed_store(value="original history")
        self.seed_store(value="same final value", revision=1, request="second")
        other = Store(self.directory / "other-history", create=True)
        self.seed_store(other, value="changed history")
        self.seed_store(other, value="same final value", revision=1, request="second")
        self.assertEqual(self.store.snapshot(), other.snapshot())
        original = ArtifactStore.put
        def replace_history(artifacts, *args):
            result = original(artifacts, *args)
            with closing(sqlite3.connect(other._path)) as source:
                with closing(sqlite3.connect(self.database)) as destination:
                    source.backup(destination)
            return result
        with mock.patch.object(ArtifactStore, "put", new=replace_history):
            error = self.assert_refusal("stale_revision", lambda: self.invoke_cli(revision=2))
        self.assertEqual(error.details["reason"], "snapshot_identity_mismatch")
        self.assertEqual(self.read_counts(), (1, 2, 2))

    def test_reject_another_workspace_proof_without_running_the_callback(self):
        other = Store(self.directory / "other-workspace", create=True)
        preparation = self.read_preparation(other).release_proof()
        error = self.assert_refusal("stale_revision", lambda: self.store.mutate(
            "workspace.artifact", self.payload, lambda _: self.fail("Ran foreign preparation"),
            expected_revision=0, request_id="pin", _preparation=preparation))
        self.assertEqual(error.details["reason"], "snapshot_identity_mismatch")
        self.assertEqual(self.read_counts(), (0, 0, 0))

    def test_resolve_original_request_and_conflict_before_checking_a_foreign_proof(self):
        receipt = self.invoke_cli()
        other = Store(self.directory / "other-workspace", create=True)
        preparation = self.read_preparation(other).release_proof()
        self.seed_store(revision=1)
        def replay(operation, payload):
            return self.store.mutate(operation, payload, lambda _: self.fail("Ran replay callback"),
                                     expected_revision=0, request_id="pin", _preparation=preparation)
        self.assertEqual(replay("workspace.artifact", self.payload), receipt)
        self.assert_refusal("request_id_conflict", lambda: replay("changed", self.payload))
        self.assert_refusal("request_id_conflict", lambda: replay(
            "workspace.artifact", dict(self.payload, id="changed")))
        self.assertEqual(self.store.revision, 2)

    def test_authenticate_afresh_instead_of_trusting_a_caller_map(self):
        forged = {"revision": 0, "records": {"literature_operation": {"pin": {}}}}
        def prepare(records, payload):
            self.assertEqual(dict(records), {})
            return [("fixture", "checked", {"checked": True})], {"checked": True}
        receipt = operations.prepared_mutation(self.store, "fixture.prepare", {}, prepare,
            expected_revision=0, request_id="pin", _snapshot=forged)
        self.assertEqual(receipt["result"], {"checked": True})
        self.assertEqual(self.read_counts(), (2, 1, 1))

    def test_roll_back_interruptions_before_callback_after_staging_inserts_and_during_commit(self):
        original_open = Store._open
        for stage in ("callback", "record", "event", "receipt", "commit"):
            with self.subTest(stage=stage):
                def open_connection(store, path, *, writable):
                    connection = original_open(store, path, writable=writable)
                    return _InterruptConnection(connection, stage) if writable else connection
                with mock.patch.object(Store, "_open", new=open_connection):
                    if stage == "callback":
                        with mock.patch.object(storage.Transaction, "__init__", side_effect=KeyboardInterrupt(stage)):
                            with self.assertRaises(KeyboardInterrupt):
                                self.invoke_cli()
                    else:
                        with self.assertRaises(KeyboardInterrupt):
                            self.invoke_cli()
                recovered = Store(self.root, create=True)
                self.assertEqual(recovered.snapshot(), {"revision": 0, "records": {}})
                self.assertIsNone(recovered.committed_request("pin"))
                self.assertEqual(self.read_counts(), (0, 0, 0))
        self.assertEqual(self.invoke_cli()["revision"], 1)

    def kill_cli_at_boundary(self, boundary):
        marker = self.directory / "boundary.json"
        script = '''
import builtins
import json
from pathlib import Path
import signal
import sys
from research_harness import cli
from research_harness.storage import Store
boundary, marker, arguments = sys.argv[1:]
def hold(value):
    Path(marker).write_text(value)
    signal.pause()
if boundary == "stdout":
    builtins.print = lambda value, **kwargs: hold(value)
else:
    original_open = Store._open
    class HeldConnection:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def commit(self):
            if self.connection.total_changes:
                hold("before commit")
            return self.connection.commit()
    def open_connection(store, path, *, writable):
        connection = original_open(store, path, writable=writable)
        if writable:
            connection.execute("PRAGMA cache_size=1")
            return HeldConnection(connection)
        return connection
    Store._open = open_connection
cli.main(json.loads(arguments))
'''
        child = subprocess.Popen([sys.executable, "-c", script, boundary, str(marker),
                                  json.dumps(self.build_cli_args())], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 45
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "CLI did not reach " + boundary)
            child.kill()
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, -signal.SIGKILL)
            self.assertEqual((stdout, stderr), (b"", b""))
            return marker.read_text()
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)

    def test_retry_after_kill_after_commit_before_stdout_returns_the_original_receipt(self):
        original = json.loads(self.kill_cli_at_boundary("stdout"))
        (self.root / self.payload["path"]).unlink()
        self.assertEqual(self.invoke_cli(), original)
        self.assertEqual(self.read_counts(), (2, 1, 1))
        self.assertEqual(self.store.revision, 1)

    def test_recover_after_kill_before_commit_and_retry_as_one_successful_commit(self):
        self.kill_cli_at_boundary("commit")
        recovered = Store(self.root, create=True)
        self.assertEqual(recovered.snapshot(), {"revision": 0, "records": {}})
        self.assertIsNone(recovered.committed_request("pin"))
        receipt = self.invoke_cli()
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(self.invoke_cli(), receipt)
        self.assertEqual(self.read_counts(), (2, 1, 1))


if __name__ == "__main__":
    unittest.main()
