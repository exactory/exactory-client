"""Metered entry reservations and real bounded process execution."""

import json
import sys
import os
from pathlib import Path
import subprocess
import time

from search_controller.errors import SearchError
from tests.support import WorkspaceTest, make_move
from tests.search_execution_support import admit_workspace, begin_spec, invoke, command_spec, review_native_inputs


class SearchExecutionTests(WorkspaceTest):
    def test_problem_progress_requires_reassessment_before_the_next_pass(self):
        invoke(self.controller, "begin", begin_spec())
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))[0], 0)
        invoke(self.controller, "begin", begin_spec())
        problem = self.read_json("problem.json")
        problem["shape"]["objects"] = "The same exact claim with a refined object description"
        self.write_json("problem.json", problem)
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(2, problem_changed=True)))[0], 0)
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", dict(begin_spec(), **{"pass": 2}))
        self.assertEqual(caught.exception.code, "strategy_reassessment_required")
        self.assertEqual(self.controller.status()["control"]["pending_moves"], [])

    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root, computational=True)

    def test_begin_reserves_move_and_blocks_an_unjournalled_next_entry(self):
        invoke(self.controller, "begin", begin_spec())
        state = self.controller.status()
        self.assertEqual(state["totals"]["reserved_moves"], 1)
        self.assertEqual(state["totals"]["used_moves"], 0)
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", begin_spec())
        self.assertEqual(caught.exception.code, "recovery_required")

    def test_journal_acknowledges_exact_reserved_legacy_bytes_once(self):
        invoke(self.controller, "begin", begin_spec())
        status, out, err = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual((status, err), (0, ""))
        state = self.controller.status()
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(state["totals"]["reserved_moves"], 0)
        receipt = state["service"]["journal_receipts"]["node-000001:1"]
        self.assertEqual(self.controller.store.get_artifact(receipt["journal_prefix_digest"]),
                         (self.workspace / "journal.jsonl").read_bytes())
        self.assertEqual(json.loads((self.workspace / "journal.jsonl").read_text())["move"], 1)
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)

    def test_begin_rejects_an_unstudied_strategy_before_reserving(self):
        spec = begin_spec()
        spec["strategy"] = "ladder-the-parameter"
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", spec)
        self.assertEqual(caught.exception.code, "admission_required")
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)

    def test_real_command_runs_frozen_bytes_and_replay_never_reruns(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("import os\nprint(os.getcwd())\nprint('frozen input')\n")
        invoke(self.controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        revision = self.controller.status()["revision"]
        try:
            receipt = self.controller.command("run", spec, revision, "one-job", "node-000001")
        except SearchError as error:
            self.fail("A reserved command must execute: " + error.code)
        state = self.controller.status()
        run = state["runs"]["run-000001"]
        self.assertEqual(run["status"], "terminal")
        self.assertEqual(state["totals"]["used_runs"], 1)
        self.assertEqual(state["totals"]["reserved_runs"], 0)
        self.assertNotEqual(run["cwd"], str(step.resolve()))
        result = self.controller.store.get_blob(run["result_digest"])
        output = self.controller.store.get_artifact(result["commands"][0]["stdout_digest"]).decode()
        self.assertEqual(output.splitlines(), [run["cwd"], "frozen input"])
        self.assertEqual(result["kind"], "command")
        (step / "job.py").write_text("raise RuntimeError('must never rerun')\n")
        self.assertEqual(self.controller.command("run", spec, revision, "one-job", "node-000001"), receipt)
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 1)

    def test_timeout_is_charged_and_bounded(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("import time\ntime.sleep(20)\n")
        invoke(self.controller, "begin", begin_spec())
        try:
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 1))
        except SearchError as error:
            self.fail("A timeout must be recorded: " + error.code)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual(run["status"], "terminal")
        self.assertEqual(run["termination"], "timeout")
        self.assertEqual(run["charged_units"], 1)

    def test_command_that_changes_its_frozen_input_cannot_verify(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("from pathlib import Path\nPath('job.py').write_text('changed')\n")
        invoke(self.controller, "begin", begin_spec())
        try:
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"]))
        except SearchError as error:
            self.fail("Input mutation must produce a charged terminal error: " + error.code)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual(run["termination"], "input_changed")
        self.assertEqual(run["charged_units"], 1)
        self.assertIn("Path('job.py')", (step / "job.py").read_text())

    def test_legacy_certificate_uses_the_reserved_frozen_executor(self):
        step = self.workspace / "deterministic" / "check-1"
        step.mkdir()
        (step / "check.sh").write_text("#!/bin/sh\nprintf checked\n")
        (step / "check.sh").chmod(0o755)
        (step / "certificate.txt").write_text("True.intro\n")
        invoke(self.controller, "begin", begin_spec())
        review_native_inputs(self.controller, self.slug, "check-1")
        status, out, err = self.run_cli("verify", "certificate", self.slug, "check-1")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 1)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertNotEqual(run["cwd"], str(step.resolve()))
        self.assertEqual(json.loads((step / "result.json").read_text())["status"], "pass")

    def test_nonzero_axiom_inspection_cannot_pass_with_plausible_output(self):
        step = self.workspace / "deterministic" / "formal"
        step.mkdir()
        (step / "Main.lean").write_text("theorem exact_decl : True := True.intro\n")
        (step / "lakefile.toml").write_text('name = "fixture"\n')
        (step / "lean-toolchain").write_text("fixture-version\n")
        (step / "step.json").write_text(json.dumps({"theorem": "exact_decl", "requested_type": "True"}))
        fake_bin = self.attack_root / "tools"
        fake_bin.mkdir()
        lake = fake_bin / "lake"
        lake.write_text("#!/bin/sh\nif [ \"$1\" = build ]; then exit 0; fi\nprintf \"'exact_decl' depends on axioms: []\\n\"\nexit 1\n")
        lake.chmod(0o755)
        from unittest import mock
        invoke(self.controller, "begin", begin_spec())
        with mock.patch.dict(os.environ, {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}):
            review_native_inputs(self.controller, self.slug, "formal", "lean")
            status, out, err = self.run_cli("verify", "lean", self.slug, "formal")
        self.assertEqual((status, err), (1, ""))
        self.assertEqual(json.loads((step / "result.json").read_text())["status"], "fail")
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 2)

    def test_zero_exit_with_mutated_checker_cannot_gain_certificate_acceptance(self):
        from search_controller.evidence import audit_verification
        from tests.search_fixtures import provenance
        step = self.workspace / "deterministic" / "check-1"
        step.mkdir()
        (step / "check.sh").write_text("#!/bin/sh\nprintf changed > check.sh\n")
        (step / "check.sh").chmod(0o755)
        (step / "certificate.txt").write_text("True.intro\n")
        invoke(self.controller, "begin", begin_spec())
        review_native_inputs(self.controller, self.slug, "check-1")
        self.run_cli("verify", "certificate", self.slug, "check-1")
        state = self.controller.status()
        run = state["runs"]["run-000001"]
        frozen = self.controller.store.get_blob(run["input_digest"])
        review = {"subject_digest": run["result_digest"], "claim_digest": frozen["claim_digest"], "reviewer": provenance("reviewer"),
                  "decision": "approve", "findings": {key: "Reviewed checker and exact proposition" for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}
        manifest = dict(frozen, kind="certificate", verification={"run_id": run["id"], "result_digest": run["result_digest"],
                        "policy_review": review, "requested_declaration": None, "requested_type_digest": None})
        with self.assertRaises(SearchError) as caught:
            audit_verification(state, manifest, self.controller.store)
        self.assertEqual(caught.exception.code, "verification_failed")

    def test_interrupted_journal_intent_recovers_the_exact_append_once(self):
        import attack
        from unittest import mock
        invoke(self.controller, "begin", begin_spec())
        with mock.patch.object(attack, "run_journal_add", side_effect=OSError("interrupted before append")):
            with self.assertRaises(OSError):
                self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), b"")
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)
        original = (self.workspace / "journal.jsonl").read_bytes()
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), original)

    def test_literature_finish_refuses_an_open_native_child(self):
        from tests.support import admit_native_child
        admit_native_child(self.controller, "child", self.slug)
        status, out, err = self.run_cli("finish", self.slug)
        self.assertNotEqual(status, 0)
        self.assertIn("a parent finishes after its children", err)
        self.assertFalse((self.workspace / "units/FINISHED.json").exists())

    def test_finish_cannot_hide_an_unjournalled_reserved_move(self):
        invoke(self.controller, "begin", begin_spec())
        status, out, err = self.run_cli("finish", self.slug)
        self.assertNotEqual(status, 0)
        self.assertIn("recovery_required", err)
        self.assertFalse((self.workspace / "units/FINISHED.json").exists())

    def test_finished_native_record_updates_controller_local_stage(self):
        status, out, err = self.run_cli("finish", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(self.controller.status()["nodes"]["node-000001"]["status"], "finished")
        self.assertEqual(self.controller.status()["proof_status"], "open")

    def test_declared_result_output_is_captured_with_its_exact_run(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("import os\nfrom pathlib import Path\nPath(os.environ['EXACTORY_OUTPUT_DIR'], 'answer.txt').write_text('the exact output')\n")
        invoke(self.controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        spec["expected_outputs"] = ["answer.txt"]
        invoke(self.controller, "run", spec)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertIn("outputs", run)
        self.assertEqual(run["outputs"][0]["path"], "answer.txt")
        self.assertEqual(self.controller.store.get_artifact(run["outputs"][0]["digest"]), b"the exact output")

    def test_missing_declared_output_is_a_terminal_execution_failure(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('no certificate was produced')\n")
        invoke(self.controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        spec["expected_outputs"] = ["answer.txt"]
        invoke(self.controller, "run", spec)
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["termination"], "missing_output")

    def test_native_write_without_acknowledgement_blocks_conflicting_recovery(self):
        from unittest import mock
        from search_controller import integration
        with mock.patch.object(integration, "after_legacy", side_effect=OSError("crash before success acknowledgement")):
            with self.assertRaises(OSError):
                self.run_cli("finish", self.slug)
        finished = (self.workspace / "units/FINISHED.json").read_bytes()
        before = self.controller.status()
        identity, intent = next(iter(before["service"]["native_intents"].items()))
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(caught.exception.code, "recovery_conflict")
        self.assertIsInstance(caught.exception.details, dict)
        self.assertEqual(caught.exception.details["intent_id"], identity)
        self.assertEqual(caught.exception.details["original_command"], "finish")
        self.assertEqual(caught.exception.details["original_args"], self.controller.store.get_blob(intent["args_digest"]))
        self.assertEqual(caught.exception.details["conflicting_paths"], [str(self.workspace / "units/FINISHED.json")])
        self.assertIn("Operator handoff required", caught.exception.message)
        self.assertIn("no supported replay or automatic overwrite", caught.exception.message)
        self.assertEqual(self.controller.status(), before)
        self.assertEqual((self.workspace / "units/FINISHED.json").read_bytes(), finished)

    def test_native_crash_before_any_write_can_reconcile_unchanged_inputs(self):
        import attack
        from unittest import mock
        with mock.patch.object(attack, "run_finish", side_effect=OSError("crash before finish write")):
            with self.assertRaises(OSError):
                self.run_cli("finish", self.slug)
        self.assertIn("native_intents", self.controller.status()["service"])
        self.assertEqual(len(self.controller.status()["service"]["native_intents"]), 1)
        from search_controller.scheduler import next_action
        self.assertEqual(next_action(self.controller.status())["kind"], "blocked")
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "complete", {}, None)
        self.assertEqual(caught.exception.code, "recovery_required")
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["service"]["native_intents"], {})
        self.assertFalse((self.workspace / "units/FINISHED.json").exists())

    def test_failed_native_validation_releases_only_an_unchanged_intent(self):
        (self.workspace / "ranking.json").unlink()
        status, out, err = self.run_cli("rank", self.slug)
        self.assertEqual(status, 1)
        self.assertIn("ranking.json", err)
        self.assertEqual(self.controller.status()["service"]["native_intents"], {})

    def test_known_failed_check_records_stamp_removal_without_claiming_success(self):
        directory = self.workspace / "units/1"
        directory.mkdir()
        (self.workspace / "units/INVENTORY.md").write_text("Inventory\n")
        (directory / "check-unit.json").write_text("stale stamp\n")
        status, out, err = self.run_cli("check-unit", self.slug, "1")
        self.assertEqual(status, 1)
        self.assertIn("unit.json", err)
        self.assertFalse((directory / "check-unit.json").exists())
        state = self.controller.status()
        self.assertEqual(state["service"]["native_intents"], {})
        failed = [receipt for receipt in state["service"]["native_receipts"].values() if receipt["outcome"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("unit.json", "\n".join(failed[0]["diagnostics"]))
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["service"]["native_receipts"], state["service"]["native_receipts"])


class AnalyticalAdmissionExecutionTests(WorkspaceTest):
    def test_plain_proof_admission_does_not_authorize_generic_finite_sampling(self):
        controller = admit_workspace(self.attack_root)
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        marker = self.attack_root / "sampling-ran"
        (step / "sample.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        invoke(controller, "begin", begin_spec())
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "run", command_spec(self.workspace, [sys.executable, "sample.py"]))
        self.assertEqual(caught.exception.code, "admission_required")
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["totals"]["used_runs"], 0)

    def test_native_tag_without_independent_input_review_never_launches(self):
        controller = admit_workspace(self.attack_root)
        step = self.workspace / "deterministic" / "sampling"
        step.mkdir()
        marker = self.attack_root / "sampling-ran"
        (step / "check.sh").write_text("#!/bin/sh\nprintf sampling > '" + str(marker) + "'\n")
        (step / "check.sh").chmod(0o755)
        invoke(controller, "begin", begin_spec())
        status, out, err = self.run_cli("verify", "certificate", self.slug, "sampling")
        self.assertNotEqual(status, 0)
        self.assertIn("input_review_required", err)
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["totals"]["used_runs"], 0)


class ExecutionRecoveryTests(WorkspaceTest):
    def test_executor_death_leaves_live_launcher_owned_and_never_reruns(self):
        from tests.test_search_cli import CLI, INSTALLED_BIN
        controller = admit_workspace(self.attack_root, computational=True)
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        marker = self.attack_root / "launch-count"
        (step / "job.py").write_text("import time\nfrom pathlib import Path\nwith Path(" + repr(str(marker)) + ").open('a') as f: f.write('one\\n')\ntime.sleep(1)\n")
        invoke(controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"], 3)
        spec_path = self.attack_root / "run-spec.json"
        spec_path.write_text(json.dumps(spec))
        revision = controller.status()["revision"]
        environment = dict(os.environ)
        environment["PATH"] = INSTALLED_BIN + os.pathsep + environment["PATH"]
        process = subprocess.Popen([sys.executable, str(CLI), "--attack-root", str(self.attack_root), "search", "run", "node-000001",
            "--spec", str(spec_path), "--expected-revision", str(revision), "--request-id", "crash-run", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        deadline = time.monotonic() + 4
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), process.communicate(timeout=1) if process.poll() is not None else "workload did not start")
        process.kill()
        process.communicate(timeout=2)
        invoke(controller, "reconcile", {}, None)
        self.assertEqual(controller.status()["runs"]["run-000001"]["status"], "launched")
        self.assertEqual(controller.status()["process_observations"]["run-000001"], "live")
        with self.assertRaises(SearchError):
            invoke(controller, "run", spec)
        terminal = self.attack_root / ".search/runs/run-000001/terminal.json"
        deadline = time.monotonic() + 4
        while not terminal.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(terminal.exists())
        invoke(controller, "reconcile", {}, None)
        self.assertEqual(controller.status()["runs"]["run-000001"]["status"], "terminal")
        self.assertEqual(controller.status()["process_observations"]["run-000001"], "terminal")
        self.assertEqual(marker.read_text(), "one\n")
        self.assertEqual(controller.status()["totals"]["used_runs"], 1)

    def test_exhausted_inherited_account_refuses_another_checker_launch(self):
        from tests.search_fixtures import review
        controller = admit_workspace(self.attack_root, max_runs=1, computational=True)
        candidate = controller.status()["proposals"]["proposal-000001"]["record"]
        candidate["attack_slug"] = "second"
        candidate["budget"].update(mode="inherit", account_id="account-000001")
        invoke(controller, "propose", {"proposal": candidate, "inputs": []}, None)
        invoke(controller, "review", {"proposal_id": "proposal-000002", "review": review(candidate), "inputs": []}, None)
        invoke(controller, "admit", {}, "proposal-000002")
        self.assertEqual(controller.status()["nodes"]["node-000002"]["account_id"], "account-000001")
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('one run')\n")
        invoke(controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        invoke(controller, "run", spec)
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "run", spec, "node-000002")
        self.assertEqual(caught.exception.code, "budget_exhausted")
        self.assertEqual(controller.status()["totals"]["used_runs"], 1)
