"""Explicit historical imports preserve bytes and never manufacture proof."""

import copy
import hashlib
import json
import shutil
import unittest

from tests.test_search_cli import SearchCLIWorkspace
from tests.search_fixtures import claim, digest, provenance, review


class AdoptionTests(SearchCLIWorkspace, unittest.TestCase):
    def create_legacy(self):
        node = self.root / "legacy"
        (node / "units").mkdir(parents=True)
        (node / "problem.json").write_text(json.dumps({"claim": claim()["statement"]}))
        (node / "journal.jsonl").write_bytes(b"")
        self.finish = b'{"outcome":"solved-in-literature","units":[]}\n'
        (node / "units" / "FINISHED.json").write_bytes(self.finish)
        (node / "LINEAGE.md").write_text("Historical hand-authored lineage.\n")
        (self.root / "SEARCH_TREE.md").write_text("Historical hand-authored search index.\n")

    def adoption_spec(self, slug="legacy"):
        node = self.root / slug
        snapshot = {"files": [{"path": str(path.relative_to(node)), "digest": hashlib.sha256(path.read_bytes()).hexdigest()}
                               for path in sorted(node.rglob("*")) if path.is_file()], "omitted_paths": []}
        subject = {"attack_slug": slug, "claim": claim(), "target_obligation": "obligation-000001",
                   "logical_predecessor": None, "snapshot_digest": digest(snapshot),
                   "snapshot_paths": [v["path"] for v in snapshot["files"]], "usage": {"moves": None, "runs": None}}
        review = {"subject_digest": digest(subject), "claim_digest": digest(claim()), "reviewer": provenance("adoption-reviewer"),
                  "decision": "approve", "findings": {key: "Inspected the historical snapshot and explicit unknown resource history"
                  for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}
        return {"mappings": [dict(subject, review=review, verification=None)], "inputs": []}

    def test_finished_legacy_attack_does_not_complete_adopted_objective(self):
        self.create_legacy()
        self.initialize()
        self.assertEqual((self.root / "SEARCH_TREE.md").read_text(), "Historical hand-authored search index.\n")
        result = self.search("adopt", self.adoption_spec(), revision=1)
        self.assertEqual(result["proof_status"], "open")
        self.assertEqual((self.root / "legacy" / "units" / "FINISHED.json").read_bytes(), self.finish)
        state = self.search("status")
        self.assertEqual(state["nodes"]["node-000001"]["status"], "finished")
        self.assertEqual(state["totals"]["historical_usage"], "unknown")
        self.assertIsNone(state["accounts"]["account-000001"]["historical_runs"])
        self.assertEqual(state["acceptances"], {})
        self.assertTrue(any(path.read_bytes() == b"Historical hand-authored search index.\n"
                            for path in (self.root / ".search" / "artifacts").iterdir()))

    def test_repeated_import_reuses_stable_account(self):
        self.create_legacy()
        self.initialize()
        spec = self.adoption_spec()
        self.search("adopt", spec, revision=1, request="adopt-once")
        self.search("adopt", spec, revision=1, request="adopt-once")
        self.assertEqual(len(self.search("status")["accounts"]), 1)

    def test_bad_later_mapping_commits_no_prefix(self):
        self.create_legacy()
        self.initialize()
        spec = self.adoption_spec()
        spec["mappings"].append(dict(spec["mappings"][0], attack_slug="missing"))
        self.search("adopt", spec, revision=1, success=False)
        state = self.search("status")
        self.assertEqual(state["nodes"], {})
        self.assertEqual(state["revision"], 1)

    def test_unfinished_import_is_readonly_without_invented_retreat(self):
        self.create_legacy()
        (self.root / "legacy" / "units" / "FINISHED.json").unlink()
        self.initialize()
        self.search("adopt", self.adoption_spec(), revision=1)
        state = self.search("status")
        self.assertEqual(state["nodes"]["node-000001"]["status"], "imported")
        self.assertEqual(state["control"]["retreats"], [])
        self.assertEqual(self.search("next")["kind"], "replan")

    def verification_spec(self):
        spec = self.adoption_spec()
        prepared = self.prepared_proposal()
        proposal = prepared["proposal"]
        proposal.update(attack_slug="verify-legacy", role="verification", relationship="prerequisite")
        proposal["task"]["purpose"] = "Verify the exact preserved claim and certificate"
        allowance_subject = {"import_id": "import-000001", "snapshot_digest": spec["mappings"][0]["snapshot_digest"],
                             "claim_digest": digest(claim()), "proposal_digest": digest(proposal), "limits": proposal["limits"]}
        allowance_review = {"subject_digest": digest(allowance_subject), "claim_digest": digest(claim()),
                            "reviewer": provenance("allowance-reviewer"), "decision": "approve",
                            "findings": {key: "Approve only the first bounded verification, with unknown earlier usage retained"
                                         for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}
        spec["mappings"][0]["verification"] = {"proposal": proposal, "review": review(proposal),
                                                 "allowance_review": allowance_review}
        spec["inputs"] = prepared["inputs"]
        return spec

    def test_first_verification_gets_reviewed_allowance_without_reopening_history(self):
        self.create_legacy()
        self.initialize()
        spec = self.verification_spec()
        self.search("adopt", spec, revision=1)
        state = self.search("status")
        self.assertEqual(state["nodes"]["node-000001"]["status"], "finished")
        self.assertEqual(state["nodes"]["node-000002"]["role"], "verification")
        account = state["accounts"][state["nodes"]["node-000002"]["account_id"]]
        self.assertEqual(account["historical_usage"], "unknown")
        self.assertIsNone(account["historical_runs"])
        self.assertEqual(account["adoption_basis"]["import_id"], "import-000001")
        self.assertEqual(self.search("next"), {"kind": "execute_node", "node_id": "node-000002"})
        self.search("adopt", spec, revision=2)
        self.assertEqual(len(self.search("status")["accounts"]), 2)

    def test_research_account_cannot_use_arbitrary_adoption_basis(self):
        from search_controller.admission import remaining_allowance
        from search_controller.errors import SearchError
        account = {"historical_usage": "unknown", "renewal_basis": None,
                   "adoption_basis": {"import_id": "made-up", "snapshot_digest": "a" * 64,
                                      "review_digest": "b" * 64, "claim_digest": digest(claim())},
                   "max_moves": 24, "max_runs": 24, "used_moves": 0, "used_runs": 0,
                   "reserved_moves": 0, "reserved_runs": 0, "role": "research"}
        with self.assertRaises(SearchError):
            remaining_allowance(account)

    def test_copied_source_name_cannot_create_another_verification_account(self):
        self.create_legacy()
        self.initialize()
        original = self.verification_spec()
        self.search("adopt", original, revision=1)
        shutil.copytree(self.root / "legacy", self.root / "copy")
        copied = self.adoption_spec("copy")
        verification = original["mappings"][0]["verification"]
        verification["proposal"]["attack_slug"] = "verify-copy"
        verification["review"] = review(verification["proposal"])
        subject = {"import_id": "import-000002", "snapshot_digest": copied["mappings"][0]["snapshot_digest"],
                   "claim_digest": digest(claim()), "proposal_digest": digest(verification["proposal"]),
                   "limits": verification["proposal"]["limits"]}
        verification["allowance_review"]["subject_digest"] = digest(subject)
        copied["mappings"][0]["verification"] = verification
        self.search("adopt", copied, revision=2)
        state = self.search("status")
        self.assertEqual(len(state["accounts"]), 2)
        self.assertEqual(state["nodes"]["node-000002"]["account_id"], state["nodes"]["node-000004"]["account_id"])

    def test_missing_relevant_source_refuses_adoption(self):
        self.create_legacy()
        self.initialize()
        spec = self.adoption_spec()
        (self.root / "legacy" / "required-proof.lean").write_text("theorem root : True := True.intro\n")
        result = self.search("adopt", spec, revision=1, success=False)
        self.assertEqual(result["error"]["code"], "snapshot_incomplete")

    def test_build_cache_and_hidden_files_are_explicitly_omitted(self):
        self.create_legacy()
        self.initialize()
        spec = self.adoption_spec()
        cache = self.root / "legacy" / ".lake"
        cache.mkdir()
        (cache / "large.olean").write_bytes(b"Unrelated compiled cache bytes")
        (self.root / "legacy" / ".private").write_bytes(b"Unrelated hidden data")
        mapping = spec["mappings"][0]
        snapshot = {"files": [{"path": name, "digest": hashlib.sha256((self.root / "legacy" / name).read_bytes()).hexdigest()}
                               for name in mapping["snapshot_paths"]], "omitted_paths": [".lake", ".private"]}
        mapping["snapshot_digest"] = digest(snapshot)
        subject = {key: value for key, value in mapping.items() if key not in {"review", "verification"}}
        mapping["review"]["subject_digest"] = digest(subject)
        self.search("adopt", spec, revision=1)
        captured = [path.read_bytes() for path in (self.root / ".search" / "artifacts").iterdir()]
        self.assertNotIn(b"Unrelated compiled cache bytes", captured)
        self.assertNotIn(b"Unrelated hidden data", captured)

    def test_same_path_repair_pins_a_new_version_without_new_allowance(self):
        self.create_legacy()
        self.initialize()
        original = self.verification_spec()
        self.search("adopt", original, revision=1)
        (self.root / "legacy" / "repair.md").write_text("A reviewed correction to the preserved proof inputs.\n")
        amended = self.adoption_spec()
        verification = original["mappings"][0]["verification"]
        subject = {"import_id": "import-000001", "snapshot_digest": amended["mappings"][0]["snapshot_digest"],
                   "claim_digest": digest(claim()), "proposal_digest": digest(verification["proposal"]),
                   "limits": verification["proposal"]["limits"]}
        verification["allowance_review"]["subject_digest"] = digest(subject)
        amended["mappings"][0]["verification"] = verification
        self.search("adopt", amended, revision=2, request="repair-once")
        state = self.search("status")
        self.assertEqual(len(state["accounts"]), 2)
        self.assertEqual(len(state["nodes"]), 2)
        self.assertEqual(len(state["service"]["import_versions"]), 1)
        self.assertEqual(state["service"]["adoption_allowances"][-1].get("snapshot_digest"), subject["snapshot_digest"])
        basis = state["accounts"]["account-000002"]["adoption_basis"]
        self.assertEqual(basis["snapshot_digest"], original["mappings"][0]["snapshot_digest"])
        self.search("adopt", amended, revision=2, request="repair-once")
        self.assertEqual(len(self.search("status")["service"]["import_versions"]), 1)

    def test_import_amendment_cannot_remove_history_or_change_claim(self):
        self.create_legacy()
        (self.root / "legacy" / "journal.jsonl").write_text('{"move":1}\n')
        self.initialize()
        self.search("adopt", self.adoption_spec(), revision=1)
        self.assertEqual(self.search("status")["totals"]["used_moves"], 1)
        (self.root / "legacy" / "journal.jsonl").write_bytes(b"")
        self.search("adopt", self.adoption_spec(), revision=2, success=False)
        self.assertEqual(len(self.search("status")["service"]["import_versions"]), 0)
        changed = self.adoption_spec()
        changed["mappings"][0]["claim"]["statement"] = "A narrower new objective"
        self.search("adopt", changed, revision=2, success=False)

    def test_unknown_history_under_a_finite_total_cap_refuses_first_verification(self):
        from tests.search_fixtures import contract
        self.create_legacy()
        frozen = contract()
        frozen["resource_policy"]["max_total_runs"] = 100
        self.search("init", {"contract": frozen}, request="initialize")
        spec = self.verification_spec()
        verification = spec["mappings"][0]["verification"]
        verification["proposal"]["anchor"]["digest"] = digest(frozen)
        verification["review"] = review(verification["proposal"])
        subject = {"import_id": "import-000001", "snapshot_digest": spec["mappings"][0]["snapshot_digest"],
                   "claim_digest": digest(claim()), "proposal_digest": digest(verification["proposal"]),
                   "limits": verification["proposal"]["limits"]}
        verification["allowance_review"]["subject_digest"] = digest(subject)
        result = self.search("adopt", spec, revision=1, success=False)
        self.assertEqual(result["error"]["code"], "usage_unknown")
        self.assertEqual(self.search("status")["accounts"], {})

    def test_verification_account_cannot_renew_into_unrelated_research(self):
        from search_controller.admission import choose_account
        from search_controller.errors import SearchError
        from tests.search_fixtures import checkpoint
        self.create_legacy()
        self.initialize()
        self.search("adopt", self.verification_spec(), revision=1)
        state = self.search("status")
        progress = checkpoint(state)
        proposed = self.prepared_proposal()["proposal"]
        proposed["method"] = "a-new-research-method"
        proposed["budget"] = {"mode": "renew", "account_id": "account-000002", "basis_checkpoint_id": progress["id"],
                              "basis_checkpoint_digest": digest(progress), "justification": "Attempt to leave verification-only scope"}
        with self.assertRaises(SearchError):
            choose_account(state, proposed, "obligation-000001")

    def managed_verification_state(self):
        self.create_legacy()
        self.initialize()
        proposed = self.prepared_proposal()
        proposed["proposal"].update(role="verification", attack_slug="managed-verification")
        self.search("propose", proposed, revision=1)
        self.search("review", {"proposal_id": "proposal-000001", "review": review(proposed["proposal"]), "inputs": []}, revision=2)
        self.search("admit", {}, target="proposal-000001", revision=3)
        return self.search("status")

    def assert_mixed_adoption_preserves_recorded_account(self, state, account_id):
        from search_controller.adoption import build_import
        from search_controller.errors import SearchError
        from search_controller.model import apply_event
        from search_controller.storage import Store
        spec = self.verification_spec()
        imported = build_import(self.root, state, spec["mappings"][0], Store(self.root / ".search"))
        allowance = dict(spec["mappings"][0]["verification"], import_id="import-000001")
        # Task 5 owns reservation events. These counters represent its future
        # persisted predecessor state; no production mutation API is bypassed.
        account = state["accounts"][account_id]
        account.update(used_moves=7, used_runs=9, reserved_moves=1, reserved_runs=2,
                       historical_usage="unknown", historical_moves=None, historical_runs=None)
        state["totals"].update(used_moves=7, used_runs=9, reserved_moves=1, reserved_runs=2,
                               historical_usage="unknown")
        before = copy.deepcopy(state)
        event = {"sequence": state["revision"] + 1, "request_id": "mixed-adoption", "kind": "service_operation",
                 "payload": {"command": "adopt", "target": None, "spec_digest": digest(spec), "effects": [],
                             "operations": [{"kind": "legacy_imported", "payload": imported},
                                            {"kind": "adoption_allowance_recorded", "payload": allowance}]}}
        with self.assertRaises(SearchError) as caught:
            apply_event(state, event)
        self.assertEqual(caught.exception.code, "managed_verification_amendment_required")
        self.assertEqual(caught.exception.details["current_account"], before["accounts"][account_id])
        self.assertEqual(state, before)

    def test_first_legacy_adoption_refuses_a_fresh_allowance_over_managed_verification(self):
        self.managed_verification_state()
        before = (self.root / ".search" / "tree.json").read_bytes()
        result = self.search("adopt", self.verification_spec(), revision=4, success=False)
        self.assertEqual(result["error"]["code"], "managed_verification_amendment_required")
        self.assertEqual(result["error"]["details"]["current_account"]["id"], "account-000001")
        self.assertEqual((self.root / ".search" / "tree.json").read_bytes(), before)

    def test_first_legacy_adoption_preserves_managed_used_reserved_and_unknown_history(self):
        self.assert_mixed_adoption_preserves_recorded_account(self.managed_verification_state(), "account-000001")

    def test_first_legacy_adoption_resolves_a_superseded_managed_account_before_refusal(self):
        from tests.search_fixtures import proposal
        from tests.test_search_proof import accepted, admitted
        state = accepted(self.managed_verification_state(), "obligation-000001")
        checkpoint = state["checkpoints"]["checkpoint-000001"]
        renewed = proposal()
        renewed.update(role="verification", attack_slug="managed-renewal", method="formal-recheck")
        renewed["studies"]["strategies"] = [{"method": "formal-recheck", "digest": "5" * 64}]
        renewed["budget"] = {"mode": "renew", "account_id": "account-000001", "basis_checkpoint_id": checkpoint["id"],
                             "basis_checkpoint_digest": digest(checkpoint), "justification": "Use accepted progress in the new verified method"}
        state = admitted(state, renewed)
        self.assertEqual(state["nodes"]["node-000001"]["account_id"], "account-000001")
        self.assertEqual(state["accounts"]["account-000002"]["predecessor_account_id"], "account-000001")
        self.assert_mixed_adoption_preserves_recorded_account(state, "account-000002")


if __name__ == "__main__":
    unittest.main()
