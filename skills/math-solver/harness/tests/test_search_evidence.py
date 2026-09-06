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

    def test_renewal_refuses_corrupt_accepted_basis_without_any_committed_effect(self):
        self.assert_corrupt_renewal_refused()

    def test_renewal_audits_separately_accepted_dependency_closure(self):
        self.assert_corrupt_renewal_refused(acceptance_dependency=True)

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


if __name__ == "__main__":
    unittest.main()
