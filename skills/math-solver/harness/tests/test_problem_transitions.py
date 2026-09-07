"""Reserved entries may refine a problem without changing their admitted claim."""

import copy
import json
import subprocess
import sys
from unittest import mock

import attack
from search_controller.errors import SearchError
from search_controller.problem_records import validate_journal_problem
from tests.support import WorkspaceTest, make_move
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke


class ProblemTransitionTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)

    def reserve_historical_move(self):
        # Old begin events have exactly this schema, but did not store problem bytes.
        with mock.patch("search_controller.integration.pin_problem",
                        side_effect=lambda content, problem: attack.compute_problem_digest(problem)):
            invoke(self.controller, "begin", begin_spec())

    def journal_process(self, *extra):
        return subprocess.run(
            [sys.executable, attack.__file__, "--strategies", str(self.controller.strategies_dir),
             "--attack-root", str(self.attack_root), "journal", "add", self.slug,
             "--json", json.dumps(make_move(1, problem_changed=True)), *extra],
            capture_output=True, text=True, check=False)

    def refine_problem(self):
        problem = self.read_json("problem.json")
        problem["shape"]["objects"] = "A refined description discovered during the reserved entry"
        self.write_json("problem.json", problem)
        return problem

    def test_refinement_after_reservation_records_the_post_problem_once(self):
        before = self.read_json("problem.json")
        invoke(self.controller, "begin", begin_spec())
        after = self.read_json("problem.json")
        after["shape"]["objects"] = "A refined description discovered during the reserved entry"
        self.write_json("problem.json", after)

        status, out, err = self.run_cli(
            "journal", "add", self.slug, "--json",
            json.dumps(make_move(1, problem_changed=True)))

        self.assertEqual((status, err), (0, ""))
        state = self.controller.status()
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(state["totals"]["reserved_moves"], 0)
        self.assertEqual(state["control"]["pending_moves"], [])
        reservation = state["service"]["moves"]["move-node-000001-1"]
        receipt = state["service"]["journal_receipts"]["node-000001:1"]
        self.assertEqual(reservation["problem_digest"], attack.compute_problem_digest(before))
        self.assertEqual(receipt["problem_digest"], attack.compute_problem_digest(after))
        self.assertNotEqual(reservation["problem_digest"], receipt["problem_digest"])
        self.assertEqual(json.loads(self.controller.store.get_artifact(reservation["problem_digest"])), before)
        self.assertEqual(json.loads(self.controller.store.get_artifact(receipt["problem_digest"])), after)
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)
        self.assertEqual(len(attack.read_journal(self.workspace)), 1)

    def test_changed_claim_is_rejected_without_an_intent_or_charge(self):
        invoke(self.controller, "begin", begin_spec())
        before = self.controller.status()
        problem = self.read_json("problem.json")
        problem["claim"] = "A different theorem that was never admitted."
        self.write_json("problem.json", problem)

        status, out, err = self.run_cli(
            "journal", "add", self.slug, "--json",
            json.dumps(make_move(1, problem_changed=True)))

        self.assertEqual(status, 1)
        self.assertEqual(json.loads(err)["error"]["code"], "claim_mismatch")
        self.assertEqual(self.controller.status(), before)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), b"")

    def test_historical_reservation_accepts_only_its_explicit_original_snapshot(self):
        original = self.read_json("problem.json")
        self.write_json("before.json", original)
        self.reserve_historical_move()
        self.refine_problem()

        result = self.journal_process("--problem-before", self.slug + "/before.json")

        self.assertEqual((result.returncode, result.stderr), (0, ""))
        state = self.controller.status()
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(state["totals"]["reserved_moves"], 0)
        self.assertEqual(json.loads(self.controller.store.get_artifact(
            state["service"]["moves"]["move-node-000001-1"]["problem_digest"])), original)

    def test_historical_reservation_without_snapshot_stays_pending(self):
        self.reserve_historical_move()
        self.refine_problem()
        before = self.controller.status()

        result = self.journal_process()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "problem_snapshot_required")
        self.assertEqual(self.controller.status(), before)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), b"")

    def test_recovery_refuses_a_problem_changed_after_the_durable_intent(self):
        invoke(self.controller, "begin", begin_spec())
        after = self.refine_problem()
        with mock.patch.object(attack, "run_journal_add", side_effect=OSError("interrupted before append")):
            with self.assertRaises(OSError):
                self.run_cli("journal", "add", self.slug, "--json",
                             json.dumps(make_move(1, problem_changed=True)))
        before_recovery = self.controller.status()
        changed = self.read_json("problem.json")
        changed["shape"]["objects"] = "An unjournalled later change"
        self.write_json("problem.json", changed)

        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "reconcile", {}, None)

        self.assertEqual(caught.exception.code, "recovery_conflict")
        self.assertEqual(self.controller.status(), before_recovery)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), b"")
        self.write_json("problem.json", after)
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)

    def test_unmodified_historical_problem_needs_no_new_snapshot_option(self):
        self.reserve_historical_move()
        status, out, err = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)

    def test_wrong_original_snapshot_cannot_rebind_a_historical_reservation(self):
        self.reserve_historical_move()
        changed = self.refine_problem()
        self.write_json("before.json", changed)
        before = self.controller.status()

        result = self.journal_process("--problem-before", self.slug + "/before.json")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "digest_mismatch")
        self.assertEqual(self.controller.status(), before)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), b"")

    def test_original_snapshot_must_stay_inside_the_attack_root(self):
        self.reserve_historical_move()
        self.refine_problem()
        before = self.controller.status()

        result = self.journal_process("--problem-before", "../outside.json")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "unsafe_path")
        self.assertEqual(self.controller.status(), before)

    def test_unicode_problem_snapshots_match_the_native_journal_digest(self):
        problem = self.read_json("problem.json")
        problem["shape"]["objects"] = "Integers in ℤ with a naïve encoding"
        self.write_json("problem.json", problem)
        invoke(self.controller, "begin", begin_spec())
        self.refine_problem()

        status, out, err = self.run_cli("journal", "add", self.slug, "--json",
                                     json.dumps(make_move(1, problem_changed=True)))

        self.assertEqual((status, err), (0, ""))
        state = self.controller.status()
        digest = state["service"]["moves"]["move-node-000001-1"]["problem_digest"]
        snapshot = json.loads(self.controller.store.get_artifact(digest))
        self.assertEqual(snapshot["shape"]["objects"], "Integers in ℤ with a naïve encoding")
        self.assertEqual(len(attack.read_journal(self.workspace)), 1)

    def test_transition_replay_rejects_forged_pre_post_and_claim(self):
        before = self.read_json("problem.json")
        invoke(self.controller, "begin", begin_spec())
        after = self.refine_problem()
        state = self.controller.status()
        reservation = state["service"]["moves"]["move-node-000001-1"]
        payload = {"problem_digest": attack.compute_problem_digest(after),
                   "problem_transition": {"before": before, "after": after,
                       "line": dict(make_move(1, problem_changed=True), problem_digest=attack.compute_problem_digest(after))}}
        validate_journal_problem(state, reservation, payload)
        for side, field, replacement in [
                ("before", "objects", "A different starting problem"),
                ("after", "objects", "A different ending problem"),
                ("after", "claim", "An unadmitted claim")]:
            with self.subTest(side=side, field=field):
                forged = copy.deepcopy(payload)
                problem = forged["problem_transition"][side]
                if field == "claim":
                    problem["claim"] = replacement
                    forged["problem_digest"] = attack.compute_problem_digest(problem)
                else:
                    problem["shape"][field] = replacement
                with self.assertRaises(SearchError):
                    validate_journal_problem(state, reservation, forged)
        self.assertEqual(self.controller.status(), state)

    def test_explicit_null_transition_is_not_a_legacy_intent(self):
        invoke(self.controller, "begin", begin_spec())
        state = self.controller.status()
        reservation = state["service"]["moves"]["move-node-000001-1"]
        payload = {"problem_digest": reservation["problem_digest"], "problem_transition": None}

        with self.assertRaises(SearchError):
            validate_journal_problem(state, reservation, payload)

        self.assertEqual(self.controller.status(), state)

    def test_problem_changed_flag_still_must_match_the_native_baseline(self):
        invoke(self.controller, "begin", begin_spec())
        self.refine_problem()
        before = self.controller.status()

        status, out, err = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))

        self.assertEqual(status, 1)
        self.assertIn("problem_changed is false", err)
        self.assertEqual(self.controller.status(), before)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), b"")

    def test_reconcile_cannot_take_a_journal_intent_from_its_live_writer(self):
        invoke(self.controller, "begin", begin_spec())
        self.refine_problem()
        native_append = attack.run_journal_add
        recovery_errors = []

        def concurrent_recovery(args):
            try:
                invoke(self.controller, "reconcile", {}, None)
            except SearchError as error:
                recovery_errors.append(error.code)
            return native_append(args)

        with mock.patch.object(attack, "run_journal_add", side_effect=concurrent_recovery):
            status, out, err = self.run_cli("journal", "add", self.slug, "--json",
                                         json.dumps(make_move(1, problem_changed=True)))

        self.assertEqual((status, err), (0, ""))
        self.assertEqual(recovery_errors, ["journal_owned"])
        self.assertEqual(len(attack.read_journal(self.workspace)), 1)
        state = self.controller.status()
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(state["totals"]["reserved_moves"], 0)
        receipt = state["service"]["journal_receipts"]["node-000001:1"]
        self.assertEqual(self.controller.store.get_artifact(receipt["journal_prefix_digest"]),
                         (self.workspace / "journal.jsonl").read_bytes())

    def test_transition_replay_requires_the_corresponding_journal_line(self):
        before = self.read_json("problem.json")
        invoke(self.controller, "begin", begin_spec())
        after = self.refine_problem()
        state = self.controller.status()
        reservation = state["service"]["moves"]["move-node-000001-1"]
        incomplete = {"problem_digest": attack.compute_problem_digest(after),
                      "problem_transition": {"before": before, "after": after}}

        with self.assertRaises(SearchError):
            validate_journal_problem(state, reservation, incomplete)

    def test_problem_transition_line_cannot_change_the_reserved_entry_or_post_digest(self):
        before = self.read_json("problem.json")
        invoke(self.controller, "begin", begin_spec())
        after = self.refine_problem()
        state = self.controller.status()
        reservation = state["service"]["moves"]["move-node-000001-1"]
        line = dict(make_move(1, problem_changed=True), problem_digest=attack.compute_problem_digest(after))
        payload = {"problem_digest": line["problem_digest"],
                   "problem_transition": {"before": before, "after": after, "line": line}}
        for field, value in [("entry", "strengthen-the-target"),
                             ("problem_digest", attack.compute_problem_digest(before))]:
            with self.subTest(field=field):
                forged = copy.deepcopy(payload)
                forged["problem_transition"]["line"][field] = value
                with self.assertRaises(SearchError):
                    validate_journal_problem(state, reservation, forged)

    def test_corrupt_problem_artifact_cannot_be_replaced_by_an_explicit_snapshot(self):
        original = self.read_json("problem.json")
        self.write_json("before.json", original)
        invoke(self.controller, "begin", begin_spec())
        self.refine_problem()
        before = self.controller.status()
        digest = before["service"]["moves"]["move-node-000001-1"]["problem_digest"]
        artifact = self.controller.store.root / "artifacts" / digest
        artifact.write_bytes(b"corrupted fixture bytes")

        result = self.journal_process("--problem-before", self.slug + "/before.json")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "corrupt_artifact")
        self.assertEqual(artifact.read_bytes(), b"corrupted fixture bytes")
        self.assertEqual(self.controller.status(), before)

    def test_crash_after_append_recovers_one_line_and_one_charge(self):
        from search_controller import integration
        invoke(self.controller, "begin", begin_spec())
        self.refine_problem()
        with mock.patch.object(integration, "after_legacy", side_effect=OSError("interrupted before acknowledgement")):
            with self.assertRaises(OSError):
                self.run_cli("journal", "add", self.slug, "--json",
                             json.dumps(make_move(1, problem_changed=True)))
        journal = (self.workspace / "journal.jsonl").read_bytes()
        self.assertEqual(len(journal.splitlines()), 1)
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 0)
        invoke(self.controller, "reconcile", {}, None)
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), journal)
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)

    def test_writer_keeps_ownership_until_after_acknowledgement(self):
        from search_controller import integration
        invoke(self.controller, "begin", begin_spec())
        self.refine_problem()
        acknowledge = integration.after_legacy
        recovery_errors = []

        def concurrent_recovery(context, outcome, diagnostics=None):
            self.assertEqual(len(attack.read_journal(self.workspace)), 1)
            try:
                invoke(self.controller, "reconcile", {}, None)
            except SearchError as error:
                recovery_errors.append(error.code)
            return acknowledge(context, outcome, diagnostics)

        with mock.patch.object(integration, "after_legacy", side_effect=concurrent_recovery):
            status, out, err = self.run_cli("journal", "add", self.slug, "--json",
                                         json.dumps(make_move(1, problem_changed=True)))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(recovery_errors, ["journal_owned"])
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)

    def test_recovery_checks_the_journal_artifact_against_its_transition(self):
        from search_controller.integration import internal_operation
        from search_controller.problem_records import pin_problem
        before_problem = self.read_json("problem.json")
        invoke(self.controller, "begin", begin_spec())
        after_problem = self.refine_problem()
        post_digest = attack.compute_problem_digest(after_problem)
        line = dict(make_move(1, problem_changed=True), problem_digest=post_digest)
        wrong_line = dict(line, problem_digest=attack.compute_problem_digest(before_problem))

        def forged_intent(state, content):
            reservation = state["service"]["moves"]["move-node-000001-1"]
            pin_problem(content, after_problem)
            return [{"kind": "journal_intended", "payload": {
                "reservation_id": reservation["id"], "before_digest": reservation["journal_prefix_digest"],
                "after_digest": content.put_artifact((json.dumps(wrong_line) + "\n").encode("utf-8")),
                "problem_digest": post_digest,
                "problem_transition": {"before": before_problem, "after": after_problem, "line": line}}}]

        # Exercise the I/O audit even for an internally assembled, otherwise valid event.
        internal_operation(self.controller, "legacy-journal", "mismatched-artifact-fixture", forged_intent)
        before_recovery = self.controller.status()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(caught.exception.code, "digest_mismatch")
        self.assertEqual(self.controller.status(), before_recovery)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), b"")
        self.assertEqual(before_recovery["totals"]["used_moves"], 0)

    def test_legacy_unchanged_intent_replays_without_transition_snapshots(self):
        from search_controller.integration import internal_operation
        self.reserve_historical_move()
        reservation = self.controller.status()["service"]["moves"]["move-node-000001-1"]
        line = dict(make_move(1), problem_digest=reservation["problem_digest"])

        def historical_intent(state, content):
            return [{"kind": "journal_intended", "payload": {
                "reservation_id": reservation["id"], "before_digest": reservation["journal_prefix_digest"],
                "after_digest": content.put_artifact((json.dumps(line) + "\n").encode("utf-8")),
                "problem_digest": reservation["problem_digest"]}}]

        internal_operation(self.controller, "legacy-journal", "historical-intent-fixture", historical_intent)
        invoke(self.controller, "reconcile", {}, None)
        state = self.controller.status()
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(state["totals"]["reserved_moves"], 0)
        self.assertEqual(attack.read_journal(self.workspace), [line])


class ProblemTransitionRunTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root, computational=True)
        step = self.workspace / "deterministic" / "job"
        step.mkdir(parents=True)
        (step / "job.py").write_text("print('fixture evidence')\n")
        self.spec = command_spec(self.workspace, [sys.executable, "job.py"])

    def test_refinement_does_not_authorize_a_workload_against_changed_inputs(self):
        invoke(self.controller, "begin", begin_spec())
        problem = self.read_json("problem.json")
        problem["shape"]["objects"] = "Changed after the workload reservation baseline"
        self.write_json("problem.json", problem)
        before = self.controller.status()

        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", self.spec)

        self.assertEqual(caught.exception.code, "digest_mismatch")
        self.assertEqual(self.controller.status(), before)
        self.assertEqual(before["runs"], {})

    def test_refinement_after_a_terminal_run_preserves_its_frozen_evidence(self):
        invoke(self.controller, "begin", begin_spec())
        invoke(self.controller, "run", self.spec)
        run = self.controller.status()["runs"]["run-000001"]
        frozen = self.controller.store.get_blob(run["input_digest"])
        problem = self.read_json("problem.json")
        problem["shape"]["objects"] = "An interpretation recorded after the bounded run"
        self.write_json("problem.json", problem)

        status, out, err = self.run_cli("journal", "add", self.slug, "--json",
                                     json.dumps(make_move(1, problem_changed=True)))

        self.assertEqual((status, err), (0, ""))
        state = self.controller.status()
        self.assertEqual(state["runs"]["run-000001"], run)
        self.assertEqual(self.controller.store.get_blob(run["input_digest"]), frozen)
        self.assertEqual(state["totals"]["used_runs"], 1)
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(state["proof_status"], "open")
        self.assertEqual(state["acceptances"], {})
