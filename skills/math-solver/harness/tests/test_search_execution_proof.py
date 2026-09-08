"""A real finite certificate supports a separately reviewed analytical bridge."""

import copy
import hashlib
import json

from search_controller.service import Controller
from search_controller.errors import SearchError
from search_controller.proof import root_support
from tests.search_fixtures import contract, proposal, review, digest, provenance
from tests.search_execution_support import begin_spec, computation_contract, invoke, review_native_inputs
from tests.support import WorkspaceTest, make_problem, make_preconditions, ALL_YES, OPENING, FIXTURE_STRATEGIES, make_move, write_ranking


class CertifiedBridgeExecutionTests(WorkspaceTest):
    def input_file(self, relative, text, kind="artifact"):
        path = self.attack_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        value = digest(json.loads(text)) if kind == "blob" else hashlib.sha256(text.encode()).hexdigest()
        return {"path": relative, "digest": value, "kind": kind}

    def result_review(self, subject_digest, claim_digest):
        return {"subject_digest": subject_digest, "claim_digest": claim_digest,
            "reviewer": provenance("independent-mathematical-reviewer"), "decision": "approve", "findings": {
                "statement": "The exact modular statement is established",
                "assumptions": "The claims use only integer arithmetic and the accepted residue premise",
                "scope": "All four residues are checked, and Euclidean division covers every integer",
                "dependencies": "The shell builtins verify the complete table; the bridge names the certified premise",
                "policy": "The certificate checker, its completeness argument, and the algebraic bridge have been reviewed"}}

    def admit(self, candidate, node_number, strategy=OPENING):
        inputs = []
        for name, text in [("problem", candidate["claim"]["statement"]),
                           ("novelty", "This is a classical elementary modular fact used as an integration fixture."),
                           (strategy, "Check all four residues, then use the exact Euclidean division identity.")]:
            item = self.input_file("studies-" + str(node_number) + "/" + name + ".md", text)
            inputs.append(item)
            if name == strategy:
                candidate["studies"]["strategies"] = [{"method": strategy, "digest": item["digest"]}]
            else:
                candidate["studies"][name] = item["digest"]
        candidate["method"] = strategy
        invoke(self.controller, "propose", {"proposal": candidate, "inputs": inputs}, None)
        pid = "proposal-{:06d}".format(node_number)
        invoke(self.controller, "review", {"proposal_id": pid, "review": review(candidate), "inputs": []}, None)
        invoke(self.controller, "admit", {}, pid)
        slug = candidate["attack_slug"]
        problem = make_problem()
        problem["claim"] = candidate["claim"]["statement"]
        self.input_file(slug + "/problem.json", json.dumps(problem))
        self.input_file(slug + "/study/problem.md", candidate["claim"]["statement"])
        self.input_file(slug + "/study/" + strategy + ".md", "The exact finite residue check and modular identity apply.")
        self.input_file(slug + "/preconditions.json", json.dumps(make_preconditions(ALL_YES)))
        status, out, err = self.run_cli("plan", slug)
        self.assertEqual((status, err), (0, ""))
        openings = json.loads((self.attack_root / slug / "openings.json").read_text())["openings"]
        openings.sort(key=lambda row: row["strategy"] != strategy)
        self.input_file(slug + "/ranking.json", json.dumps({"generated_from": "openings.json", "order": [
            {"strategy": row["strategy"], "cites": ["shape.objects"], "reason": "The studied opening applies"} for row in openings]}))
        return "node-{:06d}".format(node_number)

    def record_result(self, node_id, manifest):
        state = self.controller.status()
        node = state["nodes"][node_id]
        manifest_input = self.input_file(node["attack_slug"] + "/manifest.json", json.dumps(manifest), "blob")
        receipt = state["service"]["journal_receipts"][node_id + ":1"]
        cp = {"schema_version": 1, "kind": "proof", "claim": node["claim"], "origin": {
            "kind": "journal_move", "node_id": node_id, "move": 1, "journal_prefix_digest": receipt["journal_prefix_digest"]},
            "evidence_digests": [manifest_input["digest"]], "what_changed": "The exact proof result is available",
            "remaining_obligation_ids": [], "next_hypothesis": "Review the complete root deduction", "milestone_id": "root-proof"}
        invoke(self.controller, "checkpoint", {"checkpoint": cp, "inputs": [manifest_input]}, node_id)
        self.assertEqual(self.controller.status()["control"]["node_facts"][node_id]["result_action"], "acceptance")
        state = self.controller.status()
        checkpoint = list(state["checkpoints"].values())[-1]
        acceptance = dict(manifest["conclusion"], obligation_id=node["obligation_id"], inputs=[],
                          review=self.result_review(digest(checkpoint), node["claim_digest"]))
        invoke(self.controller, "accept", acceptance, checkpoint["id"])
        self.assertIsNone(self.controller.status()["control"]["node_facts"][node_id]["result_action"])

    def test_certified_residue_premise_and_analytical_bridge_reach_the_full_claim(self):
        original = contract()
        root_claim = dict(original["original_claim"], statement="For every integer n, n squared is not congruent to 2 modulo 4",
                          quantifiers="For every integer n", scope={"kind": "named", "name": "all integers"}, proof_policy="certificate")
        original.update(original_claim=root_claim, proof_policy="certificate", root_attack_slug=self.slug)
        residue_claim = dict(root_claim, statement="For every r in {0,1,2,3}, r squared modulo 4 is 0 or 1",
                             quantifiers="For each of the four residues", scope={"kind": "case_ids", "case_ids": ["0", "1", "2", "3"]})
        bridge_claim = dict(root_claim, statement="The certified four-residue claim implies the assertion for every integer",
                            scope={"kind": "named", "name": "Euclidean division bridge"})
        candidate = proposal()
        candidate.update(attack_slug=self.slug, claim=residue_claim, target_obligation="new:residues")
        candidate["anchor"]["digest"] = digest(original)
        candidate["task"] = {"kind": "finite_proof", "purpose": "Verify the complete residue certificate", "input_domain": "r in {0,1,2,3}"}
        candidate["contribution"].update(route="new:lift", deduction="The residue theorem is the premise of the modular lift",
            necessity={"obligation_id": "new:residues", "omission_consequence": "The modular lift lacks its residue premise",
                "domain_justification": "Every integer has exactly one residue in {0,1,2,3}",
                "outcomes": [{"outcome": "verified", "next_action": "Review the modular lift"}, {"outcome": "rejected", "next_action": "Stop without claiming the root"}],
                "stopping_condition": "Stop after checking the four rows exactly once"})
        candidate["decomposition"] = {"obligations": [{"key": "residues", "claim": residue_claim}, {"key": "bridge", "claim": bridge_claim}],
            "routes": [{"key": "lift", "conclusion": "obligation-000001", "premises": ["new:residues"], "bridge": "new:bridge", "alternative_order": []}]}
        candidate["checkpoint_criteria"][0]["obligation_id"] = "new:residues"
        self.controller = Controller(self.attack_root, FIXTURE_STRATEGIES)
        self.controller.command("init", {"contract": original}, 0, "initialize")
        bridge = copy.deepcopy(candidate)
        bridge.update(attack_slug="bridge", claim=bridge_claim, target_obligation="new:bridge")
        bridge["task"] = {"kind": "proof", "purpose": "Prove the conditional Euclidean division bridge", "input_domain": "Every integer"}
        bridge["contribution"].update(necessity=None, deduction="Euclidean division reduces the root to the finite residue premise")
        bridge["checkpoint_criteria"][0]["obligation_id"] = "new:bridge"
        bridge_node = self.admit(bridge, 1)
        invoke(self.controller, "begin", begin_spec(), bridge_node)
        proof = self.input_file("bridge/proof.md", "Assume that r^2 modulo 4 lies in {0,1} for every r in {0,1,2,3}. Let n be any integer. Euclidean division gives n = 4q + r with 0 <= r < 4. Then n^2 = 16q^2 + 8qr + r^2, so n^2 and r^2 have the same residue modulo 4. The assumed finite premise therefore implies that n^2 is never congruent to 2 modulo 4. This conditional implication does not assume that its premise has already been proved.\n")
        self.controller.store.put_artifact((self.attack_root / proof["path"]).read_bytes())
        journal_result = self.run_cli("journal", "add", "bridge", "--json", json.dumps(make_move(1)))
        self.assertEqual(journal_result[0], 0, journal_result)
        route = self.controller.status()["routes"]["route-000001"]
        analytical = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(bridge_claim),
            "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": [{
                "route_id": route["id"], "route_digest": digest(route), "case_obligation_ids": [],
                "shared_prerequisite_ids": ["obligation-000002"], "discharged_assumption_ids": []}]},
            "artifacts": [{"path": proof["path"], "digest": proof["digest"], "role": "proof"}],
            "dependencies": [], "external_dependencies": [], "verification": None}
        self.record_result(bridge_node, analytical)
        self.assertIsNone(root_support(self.controller.status()))
        accepted_bridge = self.controller.status()["acceptances"]["acceptance-000001"]
        candidate.update(schema_version=2, target_obligation="obligation-000002",
                         computation=computation_contract(proof["digest"]), decomposition={"obligations": [], "routes": []})
        candidate["contribution"]["route"] = "route-000001"
        candidate["contribution"]["necessity"]["obligation_id"] = "obligation-000002"
        candidate["checkpoint_criteria"][0]["obligation_id"] = "obligation-000002"
        candidate["computation"]["domain"] = residue_claim["scope"]
        candidate["computation"]["basis"].update(kind="finite_residue", reduction_acceptance_id=accepted_bridge["id"],
            dependencies=[{"acceptance_id": accepted_bridge["id"], "acceptance_digest": digest(accepted_bridge), "claim_digest": digest(bridge_claim)}])
        premise_node = self.admit(candidate, 2)
        self.input_file(self.slug + "/deterministic/residues/certificate.txt", "0 0\n1 1\n2 0\n3 1\n")
        self.input_file(self.slug + "/deterministic/residues/check.sh", "#!/bin/sh\nset -eu\ncount=0\nwhile read -r r square; do\n  [ \"$r\" -eq \"$count\" ]\n  [ \"$square\" -eq \"$((r*r % 4))\" ]\n  [ \"$square\" -ne 2 ]\n  count=$((count+1))\ndone < certificate.txt\n[ \"$count\" -eq 4 ]\nprintf 'All four residue rows verified\\n'\n")
        (self.workspace / "deterministic/residues/check.sh").chmod(0o755)
        from tests.strategy_refresh_support import reassess_fixture
        reassess_fixture(self.controller, {(premise_node, OPENING)})
        invoke(self.controller, "begin", begin_spec(), premise_node)
        review_native_inputs(self.controller, self.slug, "residues")
        self.assertEqual(self.run_cli("verify", "certificate", self.slug, "residues")[0], 0)
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1, steps=["residues"])))[0], 0)
        self.assertEqual(self.controller.status()["control"]["node_facts"][premise_node]["result_action"], "snapshot")
        with self.assertRaises(SearchError):
            invoke(self.controller, "begin", begin_spec(), premise_node)
        run = self.controller.status()["runs"]["run-000001"]
        invoke(self.controller, "interpret", {"result_digest": run["result_digest"], "computation_digest": run["computation_digest"],
            "outcome": "verified", "inconclusive_reason": None, "classification": "proof_candidate",
            "root_decision": {"kind": "proof_candidate", "reason": "The finite residue candidate completes the already accepted modular reduction"},
            "remaining_obligation_ids": ["obligation-000002"], "next_action": "Review the modular lift"}, run["id"])
        manifest = dict(self.controller.store.get_blob(run["input_digest"]), kind="certificate", dependencies=[],
            conclusion={"outcome": "proof", "dependency_ids": [], "route_bindings": []}, verification={"run_id": run["id"],
                "result_digest": run["result_digest"], "policy_review": self.result_review(run["result_digest"], digest(residue_claim)),
                "requested_declaration": None, "requested_type_digest": None})
        self.record_result(premise_node, manifest)
        state = self.controller.status(full_audit=True)
        self.assertEqual(state["freshness"]["status"], "fresh")
        self.assertEqual(root_support(state)["acceptance_ids"], ["acceptance-000001", "acceptance-000002"])
        self.assertEqual(state["acceptances"]["acceptance-000002"]["standard"], "certificate")
        self.assertEqual(state["acceptances"]["acceptance-000001"]["classification"], "analytical")
        self.assertEqual(state["totals"]["used_moves"], 2)
        self.assertEqual(state["totals"]["used_runs"], 1)
        self.assertEqual(state["proof_status"], "open")
