"""An explicit operator abandonment must not manufacture native success."""

import copy
import fcntl
import hashlib
import json
from pathlib import Path
from unittest import mock

import attack
from search_controller.schema import digest
from tests.search_execution_support import admit_workspace
from tests.search_fixtures import provenance
from tests.support import WorkspaceTest


class RankOperatorRecoveryTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)
        self.order = self.read_json("ranking.json")["order"]
        self.write_json("ranking.json", self.order)
        self.activity_prefix = b"".join(
            (json.dumps({"at": "2026-09-06T18:56:20Z", "tool": "Write", "target": name}) + "\n").encode()
            for name in ["problem.json", "novelty.md", "study/problem.md",
                         "study/attack-the-negative-side.md", "preconditions.json", "ranking.json"])
        (self.workspace / "activity.jsonl").write_bytes(self.activity_prefix)
        prepare = getattr(self, "prepare_native_before_intent", None)
        if prepare is not None:
            prepare(self)

        def legacy_defects(workspace):
            # Reproduce the original uncaught type error with the actual input.
            attack.read_json(workspace / "ranking.json").get("order")
            return []

        with mock.patch.object(attack, "find_ranking_defects", side_effect=legacy_defects):
            with self.assertRaises(AttributeError):
                self.run_cli("rank", self.slug)
        self.write_json("ranking.json", {"order": self.order})
        self.write_json("tasks.json", {"tasks": [
            {"id": number, "text": "Preserve question %d" % number, "status": "open",
             "added_at": "2026-09-06T18:56:21Z", "added_after_move": 0,
             "done_at": None, "done_after_move": None}
            for number in range(1, 5)]})
        activity = {"at": "2026-09-06T18:56:34Z", "tool": "Edit", "target": "ranking.json"}
        (self.workspace / "activity.jsonl").write_bytes(
            self.activity_prefix + (json.dumps(activity) + "\n").encode())
        self.before = self.controller.status()
        self.intent = next(iter(self.before["service"]["native_intents"].values()))
        self.spec = self.make_spec()

    def evidence(self, name, raw):
        path = self.attack_root / name
        path.write_bytes(raw)
        return {"path": str(path), "digest": hashlib.sha256(raw).hexdigest()}

    def native_bytes(self):
        return {str(path.relative_to(self.workspace)): path.read_bytes()
                for path in self.workspace.rglob("*") if path.is_file()}

    def make_spec(self):
        snapshot = {"files": [
            {"path": name, "digest": hashlib.sha256(raw).hexdigest()}
            for name, raw in sorted(self.native_bytes().items()) if name != "LINEAGE.md"]}
        subject = {
            "schema_version": 1, "root": str(self.attack_root),
            "objective_id": self.before["objective_id"], "contract_digest": self.before["contract_digest"],
            "revision": self.before["revision"], "node_id": "node-000001",
            "intent": copy.deepcopy(self.intent), "current_snapshot": snapshot,
            "operator": provenance("recovery-operator"),
            "source_evidence": [self.evidence("original-rank.py", b"def run_rank(args):\n    raise AttributeError('original fixture failure')\n"),
                                self.evidence("original-integration.py", b"# Fixture: rank has no native output paths.\n")],
            "incident": self.evidence("incident.md", b"The fixture interrupted a read-only ranking inspection. Its terminal outcome was not recorded.\n"),
            "authorization": self.evidence("authorization.md", b"The test operator is authorized to abandon only this exact fixture intent, retaining all evidence and accounts.\n"),
            "quiescence": self.evidence("quiescence.md", b"The test owns the fixture. No other native-file writers are active during this transaction.\n"),
        }
        review = {"schema_version": 1, "subject_digest": digest(subject),
                  "reviewer": provenance("independent-recovery-reviewer"), "decision": "approve",
                  "findings": {key: "The exact synthetic fixture satisfies the declared recovery condition."
                               for key in ["read_only_origin", "delta", "authority", "preservation", "quiescence"]},
                  "unresolved_objections": []}
        return {"rank_recovery": {"subject": subject, "review": review}}

    def repair(self, spec=None, request="recover-interrupted-rank"):
        path = self.attack_root / "repair-spec.json"
        path.write_text(json.dumps(self.spec if spec is None else spec))
        return self.run_cli("search", "reconcile", "--spec", str(path),
                            "--expected-revision", str(self.before["revision"]),
                            "--request-id", request, "--json")

    def assert_rejected(self, spec=None):
        state = self.controller.status()
        native = self.native_bytes()
        result, out, err = self.repair(spec)
        self.assertNotEqual(result, 0)
        self.assertNotIn("invalid_command", err, "The command must reach the recovery checks")
        self.assertEqual(self.controller.status(), state)
        self.assertEqual(self.native_bytes(), native)

    def test_default_reconcile_remains_closed_to_ambiguous_effects(self):
        result, out, err = self.run_cli("search", "reconcile", "--expected-revision",
                                      str(self.before["revision"]), "--request-id", "ordinary-reconcile")
        self.assertNotEqual(result, 0)
        self.assertIn("recovery_conflict", err)
        self.assertEqual(self.controller.status(), self.before)

    def test_changed_input_after_review_is_preserved_and_rejected(self):
        for name in ["ranking.json", "tasks.json", "activity.jsonl", "problem.json"]:
            with self.subTest(name=name):
                path = self.workspace / name
                raw = path.read_bytes()
                path.write_bytes(raw + b"\n")
                self.assert_rejected()
                path.write_bytes(raw)

    def test_unexplained_addition_is_rejected_even_when_review_binds_it(self):
        (self.workspace / "unrelated.txt").write_text("Unexplained native data")
        self.assert_rejected(self.make_spec())

    def test_review_cannot_authorize_a_changed_ranking_order(self):
        self.write_json("ranking.json", {"order": list(reversed(self.order))})
        self.assert_rejected(self.make_spec())

    def test_review_cannot_authorize_activity_rewriting_or_extra_lines(self):
        path = self.workspace / "activity.jsonl"
        raw = path.read_bytes()
        for changed in [raw.replace(b"Write", b"Edit", 1), raw + b"{}\n"]:
            with self.subTest(changed=changed):
                path.write_bytes(changed)
                self.assert_rejected(self.make_spec())
        path.write_bytes(raw)

    def test_review_cannot_authorize_completed_or_charged_tasks(self):
        original = self.read_json("tasks.json")
        for key, value in [("status", "done"), ("added_after_move", 1), ("done_after_move", 0)]:
            with self.subTest(key=key):
                tasks = copy.deepcopy(original)
                tasks["tasks"][0][key] = value
                self.write_json("tasks.json", tasks)
                self.assert_rejected(self.make_spec())

    def test_missing_or_self_authored_review_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec["rank_recovery"]["review"]["reviewer"] = spec["rank_recovery"]["subject"]["operator"]
        self.assert_rejected(spec)
        spec = copy.deepcopy(self.spec)
        spec["rank_recovery"]["review"]["subject_digest"] = "0" * 64
        self.assert_rejected(spec)

    def test_source_and_authorization_bytes_are_bound(self):
        subject = self.spec["rank_recovery"]["subject"]
        for item in subject["source_evidence"] + [subject["authorization"], subject["quiescence"], subject["incident"]]:
            with self.subTest(path=item["path"]):
                path = Path(item["path"])
                raw = path.read_bytes()
                path.write_bytes(raw + b"Changed after review\n")
                self.assert_rejected()
                path.write_bytes(raw)

    def test_live_native_owner_and_a_receipt_both_block_abandonment(self):
        marker = self.controller.store.root / "native" / (self.intent["id"] + ".json")
        with marker.with_suffix(".lock").open("rb") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_rejected()
        marker.write_text("{}")
        self.assert_rejected()

    def test_retry_is_idempotent_and_keeps_the_original_history(self):
        document = self.controller.store.read()
        self.assertEqual(self.repair()[0], 0)
        repaired = self.controller.store.read()
        self.assertEqual(repaired["events"][:-1], document["events"])
        self.assertEqual(self.repair()[0], 0)
        self.assertEqual(self.controller.store.read(), repaired)
        spec = copy.deepcopy(self.spec)
        spec["rank_recovery"]["review"]["findings"]["delta"] = "Different command identity"
        self.assert_rejected(spec)

    def test_activity_drift_during_evidence_persistence_blocks_commit(self):
        # A single early snapshot check misses an activity writer during persistence.
        original = self.controller.store._write_immutable
        path = self.workspace / "activity.jsonl"
        raw = path.read_bytes()
        changed = raw + b'{"at":"2026-09-06T19:00:00Z","tool":"Edit","target":"problem.json"}\n'
        fired = []
        def interleaved(target, content):
            original(target, content)
            if not fired:
                path.write_bytes(changed)
                fired.append(True)
        with mock.patch.object(self.controller.store, "_write_immutable", side_effect=interleaved):
            from search_controller.errors import SearchError
            try:
                self.controller.command("reconcile", self.spec, self.before["revision"], "drift")
                rejected = False
            except SearchError:
                rejected = True
        self.assertTrue(rejected, "Snapshot drift during persistence must prevent acknowledgement")
        self.assertEqual(self.controller.status(), self.before)
        self.assertEqual(path.read_bytes(), changed, "Do not roll back the intervening writer")

    def test_original_native_lock_is_held_through_event_commit(self):
        original = self.controller.store._atomic_replace
        lock = self.controller.store.root / "native" / (self.intent["id"] + ".lock")
        observed = []
        def inspect(target, content):
            if target == self.controller.store.tree_path:
                with lock.open("rb") as contender:
                    try:
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except BlockingIOError:
                        acquired = False
                observed.append(acquired)
            original(target, content)
        with mock.patch.object(self.controller.store, "_atomic_replace", side_effect=inspect):
            self.controller.command("reconcile", self.spec, self.before["revision"], "locked-commit")
        self.assertEqual(observed, [False])

    def test_crash_after_commit_retries_without_a_second_acknowledgement(self):
        original = self.controller.store._atomic_replace
        def interrupted(target, content):
            original(target, content)
            if target == self.controller.store.tree_path:
                raise OSError("Interrupted after the durable event")
        with mock.patch.object(self.controller.store, "_atomic_replace", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.controller.command("reconcile", self.spec, self.before["revision"], "recover-interrupted-rank")
        committed = self.controller.store.read()
        self.assertEqual(self.repair()[0], 0)
        self.assertEqual(self.controller.store.read(), committed)

    def test_replaced_missing_or_symlinked_lock_cannot_authorize_recovery(self):
        lock = self.controller.store.root / "native" / (self.intent["id"] + ".lock")
        saved = lock.with_suffix(".preserved")
        lock.rename(saved)
        self.assert_rejected()
        lock.write_bytes(b"replacement")
        self.assert_rejected()
        lock.unlink()
        lock.symlink_to(saved)
        self.assert_rejected()
        lock.unlink()
        saved.rename(lock)

    def prepared_fixture(self, prepare):
        fixture = RankOperatorRecoveryTests("test_default_reconcile_remains_closed_to_ambiguous_effects")
        fixture.prepare_native_before_intent = prepare
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def test_unaccounted_original_journal_cannot_be_hidden_by_zero_counters(self):
        from tests.support import make_move
        def prepare(fixture):
            (fixture.workspace / "journal.jsonl").write_text(json.dumps(make_move(1)) + "\n")
        fixture = self.prepared_fixture(prepare)
        fixture.assert_rejected()

    def test_abandonment_does_not_refresh_lifecycle_facts(self):
        from tests.support import OPENING
        def prepare(fixture):
            conditions = fixture.read_json("preconditions.json")
            conditions[OPENING].update(note=attack.FAIL_NOTE, failed_after_move=0)
            fixture.write_json("preconditions.json", conditions)
        fixture = self.prepared_fixture(prepare)
        before = fixture.controller.status()["control"]["node_facts"]
        self.assertEqual(fixture.repair()[0], 0)
        self.assertEqual(fixture.controller.status()["control"]["node_facts"], before)

    def test_recovery_retry_does_not_initialize_an_unrelated_later_child(self):
        from search_controller import service
        from tests.support import admit_native_child
        self.assertEqual(self.repair()[0], 0)
        original = service.replace_text
        def interrupted(path, value):
            if path.name == "journal.jsonl":
                raise OSError("Interrupted later child initialization")
            return original(path, value)
        with mock.patch.object(service, "replace_text", side_effect=interrupted):
            with self.assertRaises(OSError):
                admit_native_child(self.controller)
        before = self.controller.status()
        self.assertTrue(before["service"]["pending_effect_ids"])
        child_journal = self.attack_root / "hypothesis" / "journal.jsonl"
        self.assertFalse(child_journal.exists())
        self.assertEqual(self.repair()[0], 0)
        self.assertFalse(child_journal.exists())
        self.assertEqual(self.controller.status(), before)

    def test_ranking_wrapper_preserves_json_types(self):
        def prepare(fixture):
            fixture.order[0]["reason"] = 1
            fixture.write_json("ranking.json", fixture.order)
        fixture = self.prepared_fixture(prepare)
        changed = fixture.read_json("ranking.json")
        changed["order"][0]["reason"] = True
        fixture.write_json("ranking.json", changed)
        fixture.assert_rejected(fixture.make_spec())

    def test_reviewed_read_only_inspection_pair_is_preserved(self):
        path = self.workspace / "activity.jsonl"
        raw = path.read_bytes()
        records = [{"at": "2026-09-06T21:41:28Z", "tool": "Bash", "target": target}
                   for target in ["ranking.json", "activity.jsonl"]]
        observed = raw + b"".join((json.dumps(row) + "\n").encode() for row in records)
        path.write_bytes(observed)
        result, out, err = self.repair(self.make_spec())
        self.assertEqual(result, 0, err)
        self.assertEqual(path.read_bytes(), observed)

    def test_inspection_pair_cannot_hide_an_unrelated_command(self):
        path = self.workspace / "activity.jsonl"
        raw = path.read_bytes()
        for target in ["problem.json", "journal.jsonl", "tasks.json"]:
            rows = [{"at": "2026-09-06T21:41:28Z", "tool": "Bash", "target": name}
                    for name in ["ranking.json", target]]
            path.write_bytes(raw + b"".join((json.dumps(row) + "\n").encode() for row in rows))
            self.assert_rejected(self.make_spec())

    def test_reviewed_followup_activity_inspection_is_preserved(self):
        # A valid reviewed followup must not require deleting a hook record.
        path = self.workspace / "activity.jsonl"
        records = [
            {"at": "2026-09-06T21:41:28Z", "tool": "Bash", "target": "ranking.json"},
            {"at": "2026-09-06T21:41:28Z", "tool": "Bash", "target": "activity.jsonl"},
            {"at": "2026-09-06T21:42:30Z", "tool": "Bash", "target": "activity.jsonl"},
        ]
        observed = path.read_bytes() + b"".join((json.dumps(row) + "\n").encode() for row in records)
        path.write_bytes(observed)
        result, out, err = self.repair(self.make_spec())
        self.assertEqual(result, 0, err)
        self.assertEqual(path.read_bytes(), observed)

    def test_followup_inspection_cannot_hide_a_different_target(self):
        path = self.workspace / "activity.jsonl"
        records = [
            {"at": "2026-09-06T21:41:28Z", "tool": "Bash", "target": "ranking.json"},
            {"at": "2026-09-06T21:41:28Z", "tool": "Bash", "target": "activity.jsonl"},
            {"at": "2026-09-06T21:42:30Z", "tool": "Bash", "target": "journal.jsonl"},
        ]
        path.write_bytes(path.read_bytes() + b"".join((json.dumps(row) + "\n").encode() for row in records))
        self.assert_rejected(self.make_spec())

    def test_reviewed_abandonment_preserves_native_data_and_requires_a_fresh_rank(self):
        # Removing the opt-in recovery path must leave this exact interruption blocked.
        native = self.native_bytes()
        marker = self.controller.store.root / "native" / (self.intent["id"] + ".json")
        lock = marker.with_suffix(".lock")
        identity = (lock.stat().st_dev, lock.stat().st_ino)
        result, out, err = self.repair()
        self.assertEqual(result, 0, err)
        state = self.controller.status()
        self.assertEqual(state["service"]["native_intents"], {})
        receipt = state["service"]["native_receipts"][self.intent["id"]]
        self.assertEqual(receipt["outcome"], "failed")
        self.assertIn("operator abandonment", " ".join(receipt["diagnostics"]).lower())
        self.assertFalse(marker.exists(), "An operator decision is not an original native receipt")
        self.assertEqual((lock.stat().st_dev, lock.stat().st_ino), identity)
        self.assertEqual(self.native_bytes(), native)
        for key in ["accounts", "totals", "contract", "acceptances", "proof_status", "nodes"]:
            self.assertEqual(state[key], self.before[key])
        self.assertEqual(self.run_cli("rank", self.slug)[0], 0)
        self.assertEqual(self.controller.status()["totals"], self.before["totals"])
