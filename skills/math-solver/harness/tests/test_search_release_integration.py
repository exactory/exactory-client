"""Joined release workflow. All reviewers are synthetic software test principals."""

import copy
import json

from search_controller.errors import SearchError
from search_controller.proof import coverage, root_support
from search_controller.service import Controller
from tests import test_search_execution_proof as certified
from tests.search_execution_support import begin_spec, computation_contract, invoke, review_native_inputs
from tests.search_fixtures import contract, digest, proposal, provenance
from tests.support import FIXTURE_STRATEGIES, OPENING, WorkspaceTest, make_move
from tests.strategy_refresh_support import reassess_fixture


class ReleaseWorkflowTests(WorkspaceTest):
    input_file = certified.CertifiedBridgeExecutionTests.input_file
    result_review = certified.CertifiedBridgeExecutionTests.result_review

    def admit(self, candidate, strategy=OPENING):
        number = len(self.controller.status()["proposals"]) + 1
        certified.CertifiedBridgeExecutionTests.admit(self, candidate, number, strategy)
        return next(n for n in self.controller.status()["nodes"].values() if n["attack_slug"] == candidate["attack_slug"])

    def checkpoint(self, claim, manifest, node=None):
        item = self.input_file("manifests/" + str(len(self.controller.status()["checkpoints"])) + ".json", json.dumps(manifest), "blob")
        inputs = [item]
        if node is None:
            source = self.input_file("sources/" + item["digest"] + ".md", claim["statement"])
            inputs.append(source)
            origin = {"kind": "external_result", "source": "Pinned elementary software fixture",
                      "source_digest": source["digest"], "statement": claim["statement"],
                      "statement_digest": digest(claim), "study_digest": source["digest"]}
        else:
            receipt = self.controller.status()["service"]["journal_receipts"][node["id"] + ":1"]
            origin = {"kind": "journal_move", "node_id": node["id"], "move": 1,
                      "journal_prefix_digest": receipt["journal_prefix_digest"]}
        cp = {"schema_version": 1, "kind": manifest["conclusion"]["outcome"], "claim": claim, "origin": origin,
              "evidence_digests": [item["digest"]], "what_changed": "The exact fixture result is recorded",
              "remaining_obligation_ids": [], "next_hypothesis": "Review the recorded deduction",
              "milestone_id": "root-proof" if node else None}
        invoke(self.controller, "checkpoint", {"checkpoint": cp, "inputs": inputs}, node["id"] if node else None)
        return list(self.controller.status()["checkpoints"].values())[-1]

    def accept(self, checkpoint, obligation, manifest):
        value = dict(manifest["conclusion"], obligation_id=obligation, inputs=[],
                     review=self.result_review(digest(checkpoint), digest(checkpoint["claim"])))
        invoke(self.controller, "accept", value, checkpoint["id"])
        return list(self.controller.status()["acceptances"].values())[-1]

    def analytical(self, claim, path, text, outcome="proof", bindings=None):
        artifact = self.input_file(path, text)
        self.controller.store.put_artifact((self.attack_root / path).read_bytes())
        return {"schema_version": 1, "kind": "analytical", "claim_digest": digest(claim),
                "conclusion": {"outcome": outcome, "dependency_ids": [], "route_bindings": bindings or []},
                "artifacts": [{"path": path, "digest": artifact["digest"], "role": "proof"}],
                "dependencies": [], "external_dependencies": [], "verification": None}

    def test_complete_original_objective_workflow_crosses_every_release_boundary(self):
        original = contract()
        root = dict(original["original_claim"], statement="For every integer n, n squared modulo 4 is 0 or 1.",
                    quantifiers="For every integer n.", proof_policy="certificate",
                    scope={"kind": "case_ids", "case_ids": ["0", "1", "2", "3"]})
        original.update(original_claim=root, proof_policy="certificate", root_attack_slug=self.slug,
                        required_deliverables=["Complete residue proof and Euclidean lift"])
        low = dict(root, statement="For residues r in {0,1}, r squared modulo 4 is 0 or 1.",
                   quantifiers="For r in {0,1}.", scope={"kind": "case_ids", "case_ids": ["0", "1"]})
        high = dict(root, statement="For residues r in {2,3}, r squared modulo 4 is 0 or 1.",
                    quantifiers="For r in {2,3}.", scope={"kind": "case_ids", "case_ids": ["2", "3"]})
        bridge = dict(root, statement="The low and high residue premises imply the assertion for every integer by Euclidean division.",
                      scope={"kind": "named", "name": "euclidean-division-bridge"})
        parity = dict(root, statement="An even integer has square 0 modulo 4 and an odd integer has square 1 modulo 4.",
                      scope={"kind": "named", "name": "parity-square-lemma"})
        parity_bridge = dict(root, statement="The parity-square lemma and even-odd dichotomy imply the root claim.",
                             scope={"kind": "named", "name": "parity-bridge"})
        self.controller = Controller(self.attack_root, FIXTURE_STRATEGIES)
        self.controller.command("init", {"contract": original}, 0, "release-init")
        candidate = proposal("coverage")
        candidate.update(attack_slug=self.slug, claim=high, target_obligation="new:high")
        candidate["anchor"]["digest"] = digest(original)
        candidate["decomposition"] = {"obligations": [{"key": key, "claim": value} for key, value in
            [("low", low), ("high", high), ("bridge", bridge), ("parity", parity), ("parity-bridge", parity_bridge)]],
            "routes": [{"key": "residues", "conclusion": "obligation-000001", "premises": ["new:low", "new:high"],
                        "bridge": "new:bridge", "alternative_order": []},
                       {"key": "parity", "conclusion": "obligation-000001", "premises": ["new:parity"],
                        "bridge": "new:parity-bridge", "alternative_order": []}]}
        candidate["contribution"].update(route="new:residues", deduction="These are exactly the remaining residue classes",
            coverage={"parent_obligation": "obligation-000001", "scope": high["scope"],
                      "partition_route": "new:residues", "subset_deduction": "Residues 2 and 3 are two of the four residue classes"})
        candidate["checkpoint_criteria"] = [{"kind": "verified_obstruction", "criterion_id": "root-proof",
                                               "route_id": "new:residues", "explanation": "Record why the proposed shortcut fails"}]
        candidate["retreat_criteria"] = [{"kind": "strategy_failure", "strategy_id": OPENING}]
        first = self.admit(candidate)
        state = self.controller.status()
        route, alternative = state["routes"].values()
        low_id, high_id = route["premises"]
        bridge_id = route["bridge"]
        self.assertEqual(len(state["routes"]), 2)
        self.assertEqual(state["totals"]["used_moves"], 0)
        account = first["account_id"]

        # Accept the conditional analytical bridge before admitting finite work.
        bridge_text = ("Assume the low and high residue premises. For any integer n, Euclidean division gives "
                       "n = 4q + r with r in {0,1,2,3}. Then n^2 = 16q^2 + 8qr + r^2, so n^2 and r^2 "
                       "have the same residue modulo 4. The two premises exhaust these residues and imply the root claim.\n")
        manifest = self.analytical(bridge, "bridge.md", bridge_text, bindings=[{"route_id": route["id"],
            "route_digest": digest(route), "case_obligation_ids": [low_id, high_id],
            "shared_prerequisite_ids": [], "discharged_assumption_ids": []}])
        bridge_cp = self.checkpoint(bridge, manifest)
        bridge_acceptance = self.accept(bridge_cp, bridge_id, manifest)
        bridge_digest = manifest["artifacts"][0]["digest"]
        self.assertIsNone(root_support(self.controller.status()))

        self.input_file("legacy-low/problem.json", json.dumps({"claim": low["statement"]}))
        self.input_file("legacy-low/journal.jsonl", "")
        low_manifest = self.analytical(low, "legacy-low/proof.md", "0^2 = 0 and 1^2 = 1. These are the complete low residue classes.\n")
        legacy = self.attack_root / "legacy-low"
        snapshot = {"files": [{"path": str(p.relative_to(legacy)), "digest": self.controller.store.put_artifact(p.read_bytes())}
                              for p in sorted(legacy.rglob("*")) if p.is_file()], "omitted_paths": []}
        subject = {"attack_slug": "legacy-low", "claim": low, "target_obligation": low_id, "logical_predecessor": None,
                   "snapshot_digest": digest(snapshot), "snapshot_paths": [f["path"] for f in snapshot["files"]],
                   "usage": {"moves": 0, "runs": 0}}
        before = self.controller.status()["acceptances"]
        invoke(self.controller, "adopt", {"mappings": [dict(subject, verification=None,
            review=self.result_review(digest(subject), digest(low)))], "inputs": []}, None)
        self.assertEqual(self.controller.status()["acceptances"], before)
        imported = next(n for n in self.controller.status()["nodes"].values() if n["attack_slug"] == "legacy-low")
        self.assertEqual(imported["status"], "imported")
        self.assertNotIn(imported["id"], self.controller.status()["control"]["node_facts"])
        low_cp = self.checkpoint(low, low_manifest)
        low_acceptance = self.accept(low_cp, low_id, low_manifest)
        state = self.controller.status()
        self.assertEqual(coverage(state, route["id"]), {"accepted": 2, "total": 4, "remaining": ["2", "3"]})
        self.assertEqual(state["proof_status"], "open")
        self.assertIsNone(root_support(state))

        reassess_fixture(self.controller, {(first["id"], OPENING)})
        invoke(self.controller, "begin", begin_spec(), first["id"])
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1, failed=True)))[0], 0)
        obstruction = self.analytical(high, self.slug + "/obstruction.md",
            "The shortcut that every high residue has square 0 modulo 4 fails: 3^2 = 9 has residue 1. "
            "This refutes only that shortcut and leaves the target claim open.\n", outcome="obstruction")
        obstruction_cp = self.checkpoint(high, obstruction, first)
        obstruction_acceptance = self.accept(obstruction_cp, high_id, obstruction)
        result = self.run_cli("fail", self.slug, OPENING)
        self.assertEqual(result[0], 0, result)
        invoke(self.controller, "retreat", {"criterion": first["retreat_criteria"][0],
            "failed_hypothesis": "Every high residue square is 0 modulo 4", "observation": "3 squared has residue 1",
            "last_checkpoint_id": obstruction_cp["id"], "remaining_assumption_ids": [],
            "reconsideration": "Check both permissible residues", "abandoned_route_ids": [], "abandoned_assumption_ids": []}, first["id"])
        state = self.controller.status()
        self.assertEqual(state["nodes"][first["id"]]["status"], "retreated")
        self.assertEqual(state["accounts"][account]["used_moves"], 1)
        self.assertEqual(coverage(state, route["id"])["accepted"], 2)

        successor = copy.deepcopy(state["proposals"][first["proposal_id"]]["record"])
        successor.update(schema_version=2, attack_slug="high-continuation", relationship="continuation",
            target_obligation=high_id, logical_predecessor=first["id"],
            anchor={"kind": "checkpoint", "checkpoint_id": obstruction_cp["id"], "digest": digest(obstruction_cp)},
            inherited_evidence=obstruction_cp["evidence_digests"], decomposition={"obligations": [], "routes": []},
            computation=computation_contract(bridge_digest))
        successor["contribution"].update(route=route["id"], necessity={"obligation_id": high_id,
            "omission_consequence": "The accepted Euclidean lift lacks the high residues", "domain_justification": "Exactly residues 2 and 3 remain",
            "outcomes": [{"outcome": "verified", "next_action": "Review the modular lift"},
                         {"outcome": "rejected", "next_action": "Stop without claiming the root"}],
            "stopping_condition": "Check the two remaining residue rows exactly once"})
        successor["contribution"]["coverage"]["partition_route"] = route["id"]
        successor["task"] = {"kind": "finite_proof", "purpose": "Check the remaining exhaustive residue certificate", "input_domain": "r in {2,3}"}
        successor["computation"]["domain"] = high["scope"]
        successor["computation"]["basis"].update(kind="finite_residue", reduction_acceptance_id=bridge_acceptance["id"],
            dependencies=[{"acceptance_id": bridge_acceptance["id"], "acceptance_digest": digest(bridge_acceptance), "claim_digest": digest(bridge)}])
        successor["checkpoint_criteria"] = [{"kind": "accepted_obligation", "criterion_id": "root-proof",
                                              "obligation_id": high_id, "explanation": "Accept the high residue proof"}]
        successor["budget"].update(mode="inherit", account_id=account)
        continued_strategy = "reduce-to-a-finite-computation"
        continued_entry = "reduce-to-finite-witnesses"
        successor["retreat_criteria"] = [{"kind": "strategy_failure", "strategy_id": continued_strategy}]
        continuation = self.admit(successor, strategy=continued_strategy)
        self.assertEqual(continuation["account_id"], account)
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "reassess_strategies")
        reassess_fixture(self.controller, {(continuation["id"], continued_strategy)})
        self.assertEqual(self.controller.command("next", {}, None, None),
                         {"kind": "execute_node", "node_id": continuation["id"], "strategy": continued_strategy})
        slug = continuation["attack_slug"]
        self.input_file(slug + "/deterministic/residues/certificate.txt", "2 0\n3 1\n")
        self.input_file(slug + "/deterministic/residues/check.sh", "#!/bin/sh\nset -eu\ncount=2\nwhile read -r r square; do\n  [ \"$r\" -eq \"$count\" ]\n  [ \"$square\" -eq \"$((r*r % 4))\" ]\n  [ \"$square\" -eq 0 ] || [ \"$square\" -eq 1 ]\n  count=$((count+1))\ndone < certificate.txt\n[ \"$count\" -eq 4 ]\nprintf 'Both remaining residue rows verified\\n'\n")
        (self.attack_root / slug / "deterministic/residues/check.sh").chmod(0o755)
        invoke(self.controller, "begin", dict(begin_spec(), strategy=continued_strategy, entry=continued_entry), continuation["id"])
        review_native_inputs(self.controller, slug, "residues")
        result = self.run_cli("verify", "certificate", slug, "residues")
        self.assertEqual(result[0], 0, result)
        blocked = self.run_cli("verify", "certificate", slug, "residues")
        self.assertNotEqual(blocked[0], 0)
        self.assertIn("interpretation_required", blocked[2])
        result = self.run_cli("journal", "add", slug, "--json", json.dumps(make_move(1, closes=True, steps=["residues"],
            strategy=continued_strategy, walk=continued_strategy, entry=continued_entry)))
        self.assertEqual(result[0], 0, result)
        run = next(iter(self.controller.status()["runs"].values()))
        self.assertEqual(run["status"], "terminal")
        self.assertEqual(run["account_id"], account)
        self.assertEqual(run["computation_digest"], digest(successor["computation"]))
        result = self.run_cli("verify", "certificate", slug, "residues")
        self.assertNotEqual(result[0], 0)
        self.assertEqual(len(self.controller.status()["runs"]), 1)
        invoke(self.controller, "interpret", {"result_digest": run["result_digest"], "computation_digest": run["computation_digest"],
            "outcome": "verified", "inconclusive_reason": None, "classification": "proof_candidate",
            "root_decision": {"kind": "proof_candidate", "reason": "The candidate supplies the remaining finite premise of the accepted lift"},
            "remaining_obligation_ids": [high_id], "next_action": "Review the modular lift"}, run["id"])
        manifest = dict(self.controller.store.get_blob(run["input_digest"]), kind="certificate", dependencies=[],
            conclusion={"outcome": "proof", "dependency_ids": [], "route_bindings": []}, verification={"run_id": run["id"],
                "result_digest": run["result_digest"], "policy_review": self.result_review(run["result_digest"], digest(high)),
                "requested_declaration": None, "requested_type_digest": None})
        proof_cp = self.checkpoint(high, manifest, continuation)

        child = copy.deepcopy(successor)
        child.update(schema_version=1, attack_slug="unused-child", relationship="alternative",
                     logical_predecessor=continuation["id"], native_parent=continuation["id"],
                     anchor={"kind": "checkpoint", "checkpoint_id": proof_cp["id"], "digest": digest(proof_cp)},
                     inherited_evidence=proof_cp["evidence_digests"])
        child.pop("computation")
        child["task"] = {"kind": "proof", "purpose": "Alternative analytical derivation of the same residues", "input_domain": "r in {2,3}"}
        child["contribution"]["necessity"] = None
        unused = self.admit(child, strategy=continued_strategy)
        self.assertTrue((self.attack_root / "unused-child/parent.json").is_file())
        self.assertEqual(unused["native_parent"], continuation["id"])
        accounts = self.controller.status()["accounts"]
        invoke(self.controller, "pause", {"reason": "Release fixture operator pause"}, None)
        self.controller = Controller(self.attack_root, FIXTURE_STRATEGIES)
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "paused")
        resume = {"objective_id": "objective-000001", "message_id": "release-resume", "session_id": "codex:restarted-fixture",
                  "instruction": "Continue the recorded objective under its inherited budget", "provenance": provenance("fixture-operator")}
        invoke(self.controller, "resume", resume, None)
        self.assertEqual(self.controller.status()["execution_status"], "needs_replan")
        self.assertEqual(self.controller.status()["accounts"], accounts)
        with self.assertRaises(SearchError):
            invoke(self.controller, "resume", resume, None)
        self.assertEqual(self.controller.command("next", {}, None, None),
                         {"kind": "prepare_result", "node_id": continuation["id"], "step": "acceptance"})
        high_acceptance = self.accept(proof_cp, high_id, manifest)
        state = self.controller.status(full_audit=True)
        expected_acceptances = sorted([bridge_acceptance["id"], low_acceptance["id"], high_acceptance["id"]])
        self.assertEqual(coverage(state, route["id"]), {"accepted": 4, "total": 4, "remaining": []})
        self.assertEqual(root_support(state)["acceptance_ids"], expected_acceptances)
        self.assertNotIn(obstruction_acceptance["id"], expected_acceptances)
        self.assertEqual(state["proof_status"], "open")
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "finalize_root")

        result = self.run_cli("stall", slug)
        self.assertEqual(result[0], 0, result)
        self.input_file(slug + "/units/1/proof.md", "2^2 modulo 4 = 0 and 3^2 modulo 4 = 1. The controlled certificate checks both rows.\n")
        self.input_file(slug + "/units/1/unit.json", json.dumps({"statement": high["statement"], "form": "full-proof",
            "evidence": "units/1/proof.md", "novelty": "Classical arithmetic used only as a software fixture", "moves": [1], "costs": []}))
        result = self.run_cli("check-unit", slug, "1")
        self.assertEqual(result[0], 0, result)
        for path, text in [("units/1/draft.md", high["statement"]), ("units/1/evaluation.md", "Exact controlled inputs and the two complete rows were checked."),
                           ("units/consolidation.md", "This unit contains the high residue premise."), ("HANDOFF.md", "The original objective uses this premise and the accepted low premise and bridge.")]:
            self.input_file(slug + "/" + path, text)
        result = self.run_cli("finish", slug)
        self.assertNotEqual(result[0], 0)
        self.assertEqual(result[2], "attack/unused-child: not finished; a parent finishes after its children\n")
        delivery = self.delivery(continuation, unused)
        final = self.input_file("FINAL.md", "0^2=0, 1^2=1, 2^2=4, 3^2=9. Their residues are 0,1,0,1. " + bridge_text)
        state = self.controller.status()
        subject = {"schema_version": 1, "objective_id": state["objective_id"], "contract_digest": state["contract_digest"],
            "outcome": "proof", "acceptance_ids": expected_acceptances,
            "evidence_digests": sorted({d for aid in expected_acceptances for d in state["checkpoints"][state["acceptances"][aid]["checkpoint_id"]]["evidence_digests"]}),
            "local_deliveries": [delivery], "deliverables": [{"requirement": original["required_deliverables"][0], "digest": final["digest"]}]}
        final_review = self.result_review(digest(subject), digest(root))
        final_review["reviewer"] = provenance("fixture-final-auditor")
        invoke(self.controller, "complete", {"subject": subject, "review": final_review, "inputs": [final]}, None)
        state = self.controller.status()
        self.assertEqual(state["contract"], original)
        self.assertEqual((state["proof_status"], state["execution_status"]), ("proved", "resolved"))
        self.assertEqual(self.controller.command("next", {}, None, None), {"kind": "resolved", "proof_status": "proved"})
        self.assertEqual(state["accounts"][account]["used_moves"], 2)
        self.assertEqual(state["accounts"][account]["used_runs"], 1)
        self.assertEqual(state["nodes"][unused["id"]]["status"], "admitted")
        self.assertFalse((self.attack_root / slug / "units/FINISHED.json").exists())
        self.assertEqual(state["freshness"]["status"], "unchecked")
        self.input_file(slug + "/units/1/proof.md", "Changed after final review")
        invoke(self.controller, "audit", {}, None)
        state = self.controller.status()
        self.assertEqual((state["proof_status"], state["execution_status"]), ("invalidated", "needs_replan"))
        self.assertEqual(sorted(aid for aid, value in state["acceptances"].items() if value["status"] == "invalidated"),
                         expected_acceptances)
        self.assertEqual(state["acceptances"][obstruction_acceptance["id"]]["status"], "accepted")
        self.assertEqual(state["contract"], original)
        self.assertNotIn(self.controller.command("next", {}, None, None)["kind"], ["resolved", "finalize_root"])

    def delivery(self, node, child):
        workspace = self.attack_root / node["attack_slug"]
        store = self.controller.store
        # Include the accepted source and published result versions in this package.
        paths = list((workspace / "units/1").iterdir()) + list((workspace / "deterministic/residues").iterdir())
        package = {"schema_version": 1, "node_id": node["id"], "unit_number": 1,
                   "files": [{"path": str(p.relative_to(workspace)), "digest": store.put_artifact(p.read_bytes())}
                             for p in sorted(paths) if p.is_file()]}
        def artifact(relative):
            return store.put_artifact((workspace / relative).read_bytes())
        return {"node_id": node["id"], "inventory_digest": artifact("units/INVENTORY.md"),
                "checked_unit_digests": [store.put_blob(package)], "consolidation_digest": artifact("units/consolidation.md"),
                "draft_digests": [artifact("units/1/draft.md")], "evaluation_digests": [artifact("units/1/evaluation.md")],
                "handoff_digest": artifact("HANDOFF.md"),
                "finish": {"kind": "local_finish_pending_unused_children", "unused_child_ids": [child["id"]]}}
