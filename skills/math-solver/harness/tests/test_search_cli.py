"""Source CLI exercises the controller and its real filesystem boundary."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.search_fixtures import contract, proposal, review, provenance


CLI = Path(__file__).resolve().parents[4] / "bin" / "exactory-math"
INSTALLED_BIN = "/Users/ryshiro/.codex/plugins/cache/exactory-ai/exactory/0.33.1/bin"


class SearchCLIWorkspace:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "attack"
        self.root.mkdir()
        self.sequence = 0

    def search(self, command, spec=None, target=None, revision=0, request=None, success=True):
        self.sequence += 1
        argv = [sys.executable, str(CLI), "--attack-root", str(self.root), "search", command]
        if target is not None:
            argv.append(target)
        if spec is not None:
            source = Path(self.temporary.name) / ("spec-%d.json" % self.sequence)
            source.write_text(json.dumps(spec))
            argv += ["--spec", str(source)]
        if command not in {"status", "next"}:
            argv += ["--expected-revision", str(revision), "--request-id", request or "request-%d" % self.sequence]
        argv += ["--json"]
        environment = dict(os.environ)
        environment["PATH"] = INSTALLED_BIN + os.pathsep + environment.get("PATH", "")
        result = subprocess.run(argv, capture_output=True, text=True, env=environment)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        try:
            return json.loads(result.stdout or result.stderr)
        except ValueError:
            self.fail("CLI must return a structured result: " + result.stdout + result.stderr)

    def initialize(self):
        return self.search("init", {"contract": contract()}, request="initialize")

    def prepared_proposal(self):
        value = proposal()
        inputs = []
        for name in ["problem", "novelty", "induction"]:
            path = self.root / (name + ".md")
            path.write_text("Primary-source study for " + name)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            inputs.append({"path": path.name, "digest": digest, "kind": "artifact"})
            if name == "induction":
                value["studies"]["strategies"][0]["digest"] = digest
            else:
                value["studies"][name] = digest
        return {"proposal": value, "inputs": inputs}

    def admit(self):
        self.initialize()
        proposed = self.prepared_proposal()
        self.search("propose", proposed, revision=1)
        self.search("review", {"proposal_id": "proposal-000001", "review": review(proposed["proposal"]), "inputs": []}, revision=2)
        return self.search("admit", {}, target="proposal-000001", revision=3, request="admit-root")


class SearchCLITests(SearchCLIWorkspace, unittest.TestCase):
    def test_init_propose_review_admit_and_generated_lineage(self):
        result = self.admit()
        self.assertEqual(result["revision"], 4)
        state = self.search("status")
        self.assertEqual(state["proof_status"], "open")
        self.assertEqual(state["nodes"]["node-000001"]["status"], "admitted")
        self.assertTrue((self.root / "attempt" / "problem.json").exists())
        self.assertIn("For every integer n from 5 to 15", (self.root / "SEARCH_TREE.md").read_text())
        self.assertIn("obligation-000001", (self.root / "attempt" / "LINEAGE.md").read_text())
        self.assertEqual(self.search("next")["kind"], "execute_node")

    def test_stale_revision_and_unknown_field_do_not_mutate(self):
        self.initialize()
        value = self.prepared_proposal()
        result = self.search("propose", value, revision=0, success=False)
        self.assertEqual(result["error"]["code"], "revision_conflict")
        value["verified"] = True
        self.search("propose", value, revision=1, success=False)
        self.assertEqual(self.search("status")["revision"], 1)

    def test_path_traversal_rejected_before_snapshot(self):
        self.initialize()
        value = self.prepared_proposal()
        value["inputs"][0]["path"] = "../outside.md"
        result = self.search("propose", value, revision=1, success=False)
        self.assertEqual(result["error"]["code"], "unsafe_path")
        self.assertEqual(self.search("status")["revision"], 1)

    def test_duplicate_admission_is_idempotent(self):
        original = self.admit()
        journal = self.root / "attempt" / "journal.jsonl"
        before = journal.stat().st_mtime_ns
        replay = self.search("admit", {}, target="proposal-000001", revision=3, request="admit-root")
        self.assertEqual(original, replay)
        self.assertEqual(journal.stat().st_mtime_ns, before)
        self.assertEqual(self.search("status")["revision"], 4)

    def test_execution_requires_closed_specs_and_reconcile_is_available(self):
        self.initialize()
        for command in ["begin", "run"]:
            result = self.search(command, {}, target="node-000001",
                                 revision=1, success=False)
            self.assertEqual(result["error"]["code"], "invalid_record")
        self.assertEqual(self.search("status")["revision"], 1)
        self.assertEqual(self.search("reconcile", revision=1)["revision"], 2)

    def test_unknown_command_is_structured(self):
        result = self.search("arbitrary-event", {}, success=False)
        self.assertEqual(result["error"]["code"], "invalid_command")

    def test_duplicate_stop_returns_current_control_with_one_charge(self):
        self.admit()
        self.search("focus", {"focus": "focused", "session_id": "codex:session-one", "provenance": provenance("operator")}, revision=4)
        stop = {"delivery_id": "delivery-one", "session_id": "codex:session-one", "turn_id": "turn-one", "stop_hook_active": False}
        first = self.search("hook-stop", stop, revision=5, request="stop-one")
        self.assertEqual(first["decision"]["kind"], "continue")
        self.search("pause", {"reason": "User requested a pause"}, revision=6)
        repeated = self.search("hook-stop", stop, revision=5, request="stop-one")
        self.assertEqual(repeated["decision"], {"kind": "allow_stop"})
        self.assertEqual(self.search("status")["control"]["stop_count"], 1)

    def test_managed_verifier_is_refused_before_result_write(self):
        self.admit()
        step = self.root / "attempt" / "deterministic" / "fake"
        step.mkdir()
        (step / "step.json").write_text(json.dumps({"checker": "check.sh"}))
        (step / "check.sh").write_text("#!/bin/sh\nprintf executed > proof-job-ran\n")
        environment = dict(os.environ)
        environment["PATH"] = INSTALLED_BIN + os.pathsep + environment.get("PATH", "")
        result = subprocess.run([sys.executable, str(CLI), "--attack-root", str(self.root),
                                 "verify", "certificate", "attempt", "fake"], capture_output=True, text=True, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('"code": "reservation_required"', result.stderr)
        self.assertFalse((step / "result.json").exists())
        self.assertFalse((step / "proof-job-ran").exists())

    def test_provisional_path_traversal_is_refused(self):
        from tests.support import run
        result = run(["init", "../escape"], self.root)
        self.assertNotEqual(result[0], 0)
        self.assertFalse((self.root.parent / "escape").exists())

    def test_continuation_limit_summary_is_not_reissued_on_request_replay(self):
        from search_controller.service import Controller
        self.admit()
        controller = Controller(self.root)
        controller.command("focus", {"focus": "focused", "session_id": "codex:session", "provenance": provenance("operator")}, 4, "focus")
        for number in range(40):
            spec = {"delivery_id": "delivery-%d" % number, "session_id": "codex:session", "turn_id": None, "stop_hook_active": None}
            response = controller.command("hook-stop", spec, 5 + number, "stop-%d" % number)
        self.assertEqual(response["decision"]["kind"], "summary_then_stop")
        response = controller.command("hook-stop", spec, 44, "stop-39")
        self.assertEqual(response["decision"], {"kind": "allow_stop"})
        self.assertEqual(controller.status()["control"]["stop_count"], 40)

    def test_interrupted_admission_recovers_without_a_second_admission(self):
        from unittest import mock
        from search_controller import service
        self.initialize()
        proposed = self.prepared_proposal()
        self.search("propose", proposed, revision=1)
        self.search("review", {"proposal_id": "proposal-000001", "review": review(proposed["proposal"]), "inputs": []}, revision=2)
        controller = service.Controller(self.root)
        original = service.replace_text
        def interrupted(path, value):
            if path.name == "journal.jsonl":
                raise OSError("Simulated interrupted legacy initialization")
            return original(path, value)
        with mock.patch.object(service, "replace_text", side_effect=interrupted):
            with self.assertRaises(OSError):
                controller.command("admit", {}, 3, "admit-interrupted", "proposal-000001")
        self.assertEqual(controller.status()["revision"], 4)
        self.assertEqual(self.search("next")["kind"], "blocked")
        controller.command("admit", {}, 3, "admit-interrupted", "proposal-000001")
        self.assertEqual(len(controller.status()["nodes"]), 1)
        self.assertEqual((self.root / "attempt" / "journal.jsonl").read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
