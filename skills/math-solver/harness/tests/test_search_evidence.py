"""Real immutable proof artifacts, independently bound reviews and audits."""

import hashlib
import json
import unittest

from tests.test_search_cli import SearchCLIWorkspace
from tests.search_fixtures import contract, digest, provenance, review


class EvidenceTests(SearchCLIWorkspace, unittest.TestCase):
    def artifact(self, path, text):
        (self.root / path).write_text(text)
        return {"path": path, "kind": "artifact", "digest": hashlib.sha256(text.encode()).hexdigest()}

    def external_checkpoint(self):
        claim = contract()["original_claim"]
        proof = self.artifact("proof.md", "An analytical proof covering every requested case.")
        source = self.artifact("source.md", "Pinned source containing the full original statement.")
        study = self.artifact("scope.md", "Both endpoints, assumptions and equality cases were compared.")
        manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(claim),
                    "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
                    "artifacts": [{"path": "proof.md", "digest": proof["digest"], "role": "proof"}],
                    "dependencies": [], "external_dependencies": [], "verification": None}
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        cp = {"schema_version": 1, "kind": "proof", "claim": claim,
              "origin": {"kind": "external_result", "source": "Pinned reference", "source_digest": source["digest"],
                         "statement": claim["statement"], "statement_digest": digest(claim), "study_digest": study["digest"]},
              "evidence_digests": [digest(manifest)], "what_changed": "A source proof is available",
              "remaining_obligation_ids": [], "next_hypothesis": "Review the exact scope", "milestone_id": None}
        return {"checkpoint": cp, "inputs": [proof, source, study, {"path": "manifest.json", "digest": digest(manifest), "kind": "blob"}]}

    def result_review(self, subject, claim):
        return {"subject_digest": digest(subject), "claim_digest": digest(claim), "reviewer": provenance("independent-result-reviewer"),
                "decision": "approve", "findings": {key: "Inspected the exact original statement and all pinned proof inputs"
                for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}

    def accept_spec(self):
        cp = self.search("status")["checkpoints"]["checkpoint-000001"]
        return {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(cp, cp["claim"]), "inputs": []}

    def setup_external(self):
        self.initialize()
        cp = self.external_checkpoint()
        self.search("checkpoint", cp, revision=1)
        return cp

    def test_checkpoint_freezes_evidence_without_acceptance(self):
        self.setup_external()
        state = self.search("status")
        self.assertEqual(state["acceptances"], {})
        self.assertEqual(state["proof_status"], "open")
        self.assertEqual(state["totals"]["used_moves"], 0)

    def assert_corrupt_renewal_refused(self, acceptance_dependency=False):
        self.admit()
        evidence = self.external_checkpoint()
        if acceptance_dependency:
            # A proved reduction and the subsequent proof are distinct progress.
            evidence["checkpoint"]["kind"] = "reduction"
        self.search("checkpoint", evidence, revision=4)
        self.search("accept", self.accept_spec(), target="checkpoint-000001", revision=5)
        cp = self.search("status")["checkpoints"]["checkpoint-000001"]
        revision = 6
        if acceptance_dependency:
            second = self.external_checkpoint()
            second["inputs"][0] = self.artifact("proof.md", "A reviewed deduction using the separately accepted first result.")
            manifest = json.loads((self.root / "manifest.json").read_text())
            manifest["artifacts"][0]["digest"] = second["inputs"][0]["digest"]
            manifest["conclusion"]["dependency_ids"] = ["acceptance-000001"]
            (self.root / "manifest.json").write_text(json.dumps(manifest))
            second["inputs"][-1]["digest"] = digest(manifest)
            second["checkpoint"]["evidence_digests"] = [digest(manifest)]
            self.search("checkpoint", second, revision=6)
            cp = self.search("status")["checkpoints"]["checkpoint-000002"]
            acceptance = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": ["acceptance-000001"],
                          "route_bindings": [], "review": self.result_review(cp, cp["claim"]), "inputs": []}
            self.search("accept", acceptance, target=cp["id"], revision=7)
            revision = 8
        prepared = self.prepared_proposal()
        proposed = prepared["proposal"]
        proposed.update(attack_slug="renewed", method="new-method")
        proposed["studies"]["strategies"][0]["method"] = "new-method"
        proposed["budget"] = {"mode": "renew", "account_id": "account-000001",
                              "basis_checkpoint_id": cp["id"], "basis_checkpoint_digest": digest(cp),
                              "justification": "Accepted progress supports a distinct method"}
        self.search("propose", prepared, revision=revision)
        self.search("review", {"proposal_id": "proposal-000002", "review": review(proposed), "inputs": []}, revision=revision + 1)
        before = (self.root / ".search" / "tree.json").read_bytes()
        artifact = self.root / ".search" / "artifacts" / evidence["inputs"][0]["digest"]
        original = artifact.read_bytes()
        artifact.write_text("Corrupted after acceptance and before admission")
        result = self.search("admit", {}, target="proposal-000002", revision=revision + 2, success=False)
        self.assertEqual(result["error"]["code"], "corrupt_artifact")
        self.assertEqual((self.root / ".search" / "tree.json").read_bytes(), before)
        self.assertFalse((self.root / "renewed").exists())
        artifact.write_bytes(original)
        self.search("admit", {}, target="proposal-000002", revision=revision + 2)
        self.assertEqual(len(self.search("status")["accounts"]), 2)
        return artifact

    def test_renewal_refuses_corrupt_accepted_basis_without_any_committed_effect(self):
        self.assert_corrupt_renewal_refused()

    def test_renewal_audits_separately_accepted_dependency_closure(self):
        self.assert_corrupt_renewal_refused(acceptance_dependency=True)

    def assert_reused_renewal_basis_refused(self, mode, account_id=None):
        artifact = self.assert_corrupt_renewal_refused()
        prepared = self.prepared_proposal()
        proposed = prepared["proposal"]
        proposed["attack_slug"] = "reuse"
        proposed["budget"].update(mode=mode, account_id=account_id)
        self.search("propose", prepared, revision=9)
        self.search("review", {"proposal_id": "proposal-000003", "review": review(proposed), "inputs": []}, revision=10)
        original = artifact.read_bytes()
        artifact.write_text("Corrupt the implicit current allowance basis")
        before = (self.root / ".search" / "tree.json").read_bytes()
        result = self.search("admit", {}, target="proposal-000003", revision=11, success=False)
        expected = "account_superseded" if account_id == "account-000001" else "corrupt_artifact"
        self.assertEqual(result["error"]["code"], expected)
        self.assertEqual((self.root / ".search" / "tree.json").read_bytes(), before)
        self.assertFalse((self.root / "reuse").exists())
        artifact.write_bytes(original)
        if account_id != "account-000001":
            self.search("admit", {}, target="proposal-000003", revision=11)
            state = self.search("status")
            self.assertEqual(state["nodes"]["node-000003"]["account_id"], "account-000002")
            self.assertEqual(len(state["accounts"]), 2)

    def test_new_proposal_audits_implicitly_reused_current_renewal_basis(self):
        self.assert_reused_renewal_basis_refused("new")

    def test_inherit_proposal_audits_selected_current_renewal_basis(self):
        self.assert_reused_renewal_basis_refused("inherit", "account-000002")

    def test_inherit_proposal_does_not_redirect_an_explicit_superseded_account(self):
        self.assert_reused_renewal_basis_refused("inherit", "account-000001")

    def test_acceptance_uses_immutable_proof_and_full_audit(self):
        self.setup_external()
        (self.root / "proof.md").write_text("Later unrelated working revision")
        self.search("accept", self.accept_spec(), target="checkpoint-000001", revision=2)
        state = self.search("status")
        self.assertEqual(state["acceptances"]["acceptance-000001"]["standard"], "reviewed")
        self.assertEqual(state["freshness"]["status"], "unchecked")
        self.assertEqual(self.search("next")["kind"], "finalize_root")
        self.assertEqual(state["proof_status"], "open")

    def test_corrupt_immutable_dependency_prevents_acceptance(self):
        cp = self.setup_external()
        proof_digest = cp["inputs"][0]["digest"]
        (self.root / ".search" / "artifacts" / proof_digest).write_text("Corrupted accepted boundary")
        result = self.search("accept", self.accept_spec(), target="checkpoint-000001", revision=2, success=False)
        self.assertEqual(result["error"]["code"], "corrupt_artifact")
        self.assertEqual(self.search("status")["acceptances"], {})

    def test_caller_verification_flags_cannot_supply_an_audit(self):
        self.setup_external()
        spec = self.accept_spec()
        spec["audit"] = {"verified": True}
        self.search("accept", spec, target="checkpoint-000001", revision=2, success=False)
        self.assertEqual(self.search("status")["acceptances"], {})

    def test_positive_proof_cannot_be_reinterpreted_as_counterexample(self):
        self.setup_external()
        spec = self.accept_spec()
        spec["outcome"] = "counterexample"
        self.search("accept", spec, target="checkpoint-000001", revision=2, success=False)
        self.assertEqual(self.search("status")["acceptances"], {})

    def test_tampered_accepted_artifact_invalidates_on_audit(self):
        cp = self.setup_external()
        self.search("accept", self.accept_spec(), target="checkpoint-000001", revision=2)
        (self.root / ".search" / "artifacts" / cp["inputs"][0]["digest"]).write_text("Tampered immutable proof")
        self.search("audit", revision=3)
        self.assertEqual(self.search("status")["acceptances"]["acceptance-000001"]["status"], "invalidated")
        self.assertNotEqual(self.search("next")["kind"], "finalize_root")

    def test_complete_requires_exact_deliverable_and_final_review(self):
        self.setup_external()
        self.search("accept", self.accept_spec(), target="checkpoint-000001", revision=2)
        state = self.search("status")
        cp = state["checkpoints"]["checkpoint-000001"]
        output = self.artifact("final-proof.md", "Complete conventional proof with all eleven dimension cases.")
        subject = {"schema_version": 1, "objective_id": state["objective_id"], "contract_digest": state["contract_digest"],
                   "outcome": "proof", "acceptance_ids": ["acceptance-000001"], "evidence_digests": cp["evidence_digests"],
                   "local_deliveries": [], "deliverables": [{"requirement": contract()["required_deliverables"][0], "digest": output["digest"]}]}
        spec = {"subject": subject, "review": self.result_review(subject, contract()["original_claim"]), "inputs": [output]}
        result = self.search("complete", spec, revision=3)
        self.assertEqual(result["proof_status"], "proved")
        self.assertEqual(self.search("next"), {"kind": "resolved", "proof_status": "proved"})

    def test_replay_does_not_reread_removed_sources(self):
        self.initialize()
        spec = self.external_checkpoint()
        first = self.search("checkpoint", spec, revision=1, request="freeze-once")
        for item in spec["inputs"]:
            (self.root / item["path"]).unlink()
        repeated = self.search("checkpoint", spec, revision=1, request="freeze-once")
        self.assertEqual(first, repeated)

    def test_completed_deliverable_corruption_is_detected_by_full_audit(self):
        self.test_complete_requires_exact_deliverable_and_final_review()
        output = hashlib.sha256(b"Complete conventional proof with all eleven dimension cases.").hexdigest()
        (self.root / ".search" / "artifacts" / output).write_text("Corrupted final deliverable")
        self.search("audit", revision=4)
        self.assertEqual(self.search("status")["proof_status"], "invalidated")

    def test_imported_certificate_run_cannot_fabricate_controlled_verification(self):
        self.initialize()
        spec = self.external_checkpoint()
        checker = self.artifact("check.sh", "#!/bin/sh\nprintf executed > math-job-ran\n")
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["kind"] = "certificate"
        manifest["artifacts"][0]["role"] = "certificate"
        manifest["artifacts"].append({"path": "check.sh", "digest": checker["digest"], "role": "checker"})
        manifest["verification"] = {"run_id": "run-000001", "result_digest": "a" * 64,
                                    "policy_review": {}, "requested_declaration": None, "requested_type_digest": None}
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        spec["inputs"][-1]["digest"] = digest(manifest)
        spec["inputs"].append(checker)
        spec["checkpoint"]["evidence_digests"] = [digest(manifest)]
        self.search("checkpoint", spec, revision=1)
        result = self.search("accept", self.accept_spec(), target="checkpoint-000001", revision=2, success=False)
        self.assertEqual(result["error"]["code"], "verification_required")
        self.assertFalse((self.root / "math-job-ran").exists())

    def executable_renewal(self):
        """Create a reviewed renewal while the full root remains open."""
        from search_controller.service import Controller
        from tests.search_fixtures import decomposition_proposal
        from tests.search_execution_support import computation_contract
        from tests.support import (ALL_YES, FIXTURE_STRATEGIES, OPENING,
                                   make_problem, make_preconditions, run, write_study)
        self.initialize()
        first = self.prepared_proposal()
        first["proposal"]["decomposition"] = decomposition_proposal()["decomposition"]
        self.search("propose", first, revision=1)
        self.search("review", {"proposal_id": "proposal-000001",
                              "review": review(first["proposal"]), "inputs": []}, revision=2)
        self.search("admit", {}, target="proposal-000001", revision=3)
        state = self.search("status")
        base = state["obligations"]["obligation-000002"]["claim"]
        evidence = self.external_checkpoint()
        evidence["checkpoint"].update(claim=base, kind="reduction",
                                      remaining_obligation_ids=["obligation-000001"])
        evidence["checkpoint"]["origin"].update(statement=base["statement"],
                                                statement_digest=digest(base))
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["claim_digest"] = digest(base)
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        evidence["inputs"][-1]["digest"] = digest(manifest)
        evidence["checkpoint"]["evidence_digests"] = [digest(manifest)]
        self.search("checkpoint", evidence, revision=4)
        accepted = self.accept_spec()
        accepted["obligation_id"] = "obligation-000002"
        self.search("accept", accepted, target="checkpoint-000001", revision=5)
        cp = self.search("status")["checkpoints"]["checkpoint-000001"]
        prepared = self.prepared_proposal()
        proposed = prepared["proposal"]
        proposed.update(attack_slug="renewed", method=OPENING,
                        computation=computation_contract(proposed["studies"]["problem"]))
        proposed["studies"]["strategies"][0]["method"] = OPENING
        proposed["budget"] = {"mode": "renew", "account_id": "account-000001",
                              "basis_checkpoint_id": cp["id"], "basis_checkpoint_digest": digest(cp),
                              "justification": "Accepted base cases support a distinct full-root method"}
        proposed["task"] = {"kind": "finite_decision", "purpose": "Exercise the exact fixture command",
                            "input_domain": "One supplied command fixture"}
        proposed["contribution"]["necessity"] = {
            "obligation_id": "obligation-000001",
            "omission_consequence": "The declared fixture command would remain untested",
            "domain_justification": "The supplied fixture is the entire test domain",
            "outcomes": [{"outcome": "verified", "next_action": "Inspect the fixture result"},
                         {"outcome": "rejected", "next_action": "Record failure"}],
            "stopping_condition": "Stop when the bounded command terminates"}
        self.search("propose", prepared, revision=6)
        self.search("review", {"proposal_id": "proposal-000002",
                              "review": review(proposed), "inputs": []}, revision=7)
        self.search("admit", {}, target="proposal-000002", revision=8)
        workspace = self.root / "renewed"
        problem = make_problem()
        problem["claim"] = proposed["claim"]["statement"]
        problem["quadruple"]["statement"] = problem["claim"]
        (workspace / "problem.json").write_text(json.dumps(problem))
        write_study(workspace, "problem", "This test fixture exercises controller state transitions.")
        write_study(workspace, OPENING, "The complete supplied fixture is the diagnostic domain.")
        (workspace / "preconditions.json").write_text(json.dumps(make_preconditions(ALL_YES)))
        status, out, err = run(["plan", "renewed"], self.root)
        self.assertEqual((status, err), (0, ""))
        openings = json.loads((workspace / "openings.json").read_text())["openings"]
        ranking = {"generated_from": "openings.json", "order": [
            {"strategy": item["strategy"], "cites": ["shape.objects"],
             "reason": "The fixture's studied order applies"} for item in openings]}
        (workspace / "ranking.json").write_text(json.dumps(ranking))
        controller = Controller(self.root, FIXTURE_STRATEGIES)
        self.assertEqual(self.search("next")["kind"], "reassess_strategies")
        from tests.strategy_refresh_support import reassess_fixture
        reassess_fixture(controller, {("node-000002", OPENING)})
        self.assertEqual(self.search("next"),
                         {"kind": "execute_node", "node_id": "node-000002", "strategy": OPENING})
        artifact = self.root / ".search" / "artifacts" / evidence["inputs"][0]["digest"]
        return controller, workspace, artifact

    def test_renewed_node_can_reserve_without_reopening_its_predecessor_account(self):
        from search_controller.errors import SearchError
        from tests.search_execution_support import begin_spec, invoke
        controller, workspace, artifact = self.executable_renewal()
        before = controller.status()
        try:
            invoke(controller, "begin", begin_spec(), "node-000002")
        except SearchError as error:
            self.fail("A valid renewed node must reserve: " + error.code)
        state = controller.status()
        self.assertEqual(state["accounts"]["account-000001"], before["accounts"]["account-000001"])
        self.assertEqual(state["accounts"]["account-000002"]["reserved_moves"], 1)
        self.assertEqual(state["accounts"]["account-000002"]["used_moves"], 0)
        self.assertEqual(state["service"]["moves"]["move-node-000002-1"]["account_id"], "account-000002")
        self.assertEqual(state["proposals"], before["proposals"])
        self.assertEqual((workspace / "journal.jsonl").read_bytes(), b"")

    def test_renewed_node_can_execute_a_frozen_command_on_its_current_account(self):
        import sys
        from tests.search_execution_support import begin_spec, command_spec, invoke
        controller, workspace, artifact = self.executable_renewal()
        step = workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('renewed fixture')\n")
        invoke(controller, "begin", begin_spec(), "node-000002")
        invoke(controller, "run", command_spec(workspace, [sys.executable, "job.py"]), "node-000002")
        state = controller.status()
        result = state["runs"]["run-000001"]
        self.assertEqual(result["status"], "terminal")
        self.assertEqual(result["termination"], "exit")
        self.assertEqual(result["account_id"], "account-000002")
        captured = controller.store.get_blob(result["result_digest"])["commands"][0]
        self.assertEqual(captured["exit_code"], 0)
        self.assertEqual(controller.store.get_artifact(captured["stdout_digest"]),
                         b"renewed fixture\n")
        self.assertEqual(state["accounts"]["account-000002"]["used_runs"], 1)
        self.assertEqual(state["accounts"]["account-000001"]["used_runs"], 0)

    def test_renewed_begin_still_audits_corrupt_accepted_basis_without_reservation(self):
        from search_controller.errors import SearchError
        from tests.search_execution_support import begin_spec, invoke
        controller, workspace, artifact = self.executable_renewal()
        artifact.write_text("Corrupted after successful renewal admission")
        before = controller.store.tree_path.read_bytes()
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "begin", begin_spec(), "node-000002")
        self.assertEqual(caught.exception.code, "corrupt_artifact")
        self.assertEqual(controller.store.tree_path.read_bytes(), before)
        self.assertEqual((workspace / "journal.jsonl").read_bytes(), b"")

    def test_predecessor_node_still_cannot_reserve_after_renewal(self):
        from search_controller.errors import SearchError
        from tests.search_execution_support import begin_spec, invoke
        controller, workspace, artifact = self.executable_renewal()
        before = controller.store.tree_path.read_bytes()
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "begin", begin_spec(), "node-000001")
        self.assertEqual(caught.exception.code, "account_superseded")
        self.assertEqual(controller.store.tree_path.read_bytes(), before)

    def test_renewed_run_rechecks_its_basis_after_the_move_was_reserved(self):
        import sys
        from search_controller.errors import SearchError
        from tests.search_execution_support import begin_spec, command_spec, invoke
        controller, workspace, artifact = self.executable_renewal()
        step = workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('must not launch')\n")
        invoke(controller, "begin", begin_spec(), "node-000002")
        artifact.write_text("Corrupted between begin and run")
        before = controller.store.tree_path.read_bytes()
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "run", command_spec(workspace, [sys.executable, "job.py"]), "node-000002")
        self.assertEqual(caught.exception.code, "corrupt_artifact")
        self.assertEqual(controller.store.tree_path.read_bytes(), before)
        self.assertEqual(controller.status()["runs"], {})


if __name__ == "__main__":
    unittest.main()
