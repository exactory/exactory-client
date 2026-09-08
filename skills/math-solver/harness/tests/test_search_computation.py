"""Computation admission and interpretation through actual public operations."""

import copy
import hashlib
import json
import sys

from search_controller.errors import SearchError
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, computation_contract, invoke
from tests.search_fixtures import contract, digest, proposal, provenance, review
from search_controller.service import Controller
from tests.support import WorkspaceTest


class ComputationHistoricalTests(WorkspaceTest):
    def historical(self):
        return admit_workspace(self.attack_root, computational=True, historical=True)

    def amendment(self, controller):
        state = controller.status()
        old = state["proposals"]["proposal-000001"]
        computation = computation_contract(old["record"]["studies"]["problem"])
        subject = {"node_id": "node-000001", "proposal_digest": old["digest"], "computation": computation}
        return {"proposal_digest": old["digest"], "computation": computation,
                "review": result_review(subject, old["record"]["claim"]), "inputs": []}

    def test_historical_finite_admission_cannot_launch_without_amendment(self):
        # Removing the historical-contract guard would execute this real producer.
        controller = admit_workspace(self.attack_root, computational=True, historical=True)
        marker = self.attack_root / "producer-ran"
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        invoke(controller, "begin", begin_spec())
        before = controller.status()["accounts"]
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "run", command_spec(self.workspace, [sys.executable, "job.py"]))
        self.assertEqual(caught.exception.code, "computation_amendment_required")
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["accounts"], before)

    def test_amendment_preserves_historical_accounts_and_enables_only_reviewed_execution(self):
        controller = self.historical()
        before = controller.status()
        spec = self.amendment(controller)
        try:
            invoke(controller, "amend-computation", spec)
        except SearchError as error:
            self.fail("A bound independent historical amendment must work: " + error.message)
        after = controller.status()
        for field in ["accounts", "totals", "proposals", "reviews", "nodes", "contract"]:
            self.assertEqual(after[field], before[field])
        invoke(controller, "begin", begin_spec())
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('bounded amended job')\n")
        invoke(controller, "run", command_spec(self.workspace, [sys.executable, "job.py"]))
        run = controller.status()["runs"]["run-000001"]
        self.assertEqual(run["computation_digest"], digest(spec["computation"]))
        self.assertEqual(controller.status()["totals"]["used_runs"], 1)

    def test_amendment_cannot_change_task_or_reuse_proposal_author_review(self):
        controller = self.historical()
        for defect in ["scope", "reviewer", "task"]:
            spec = self.amendment(controller)
            if defect == "scope":
                spec["proposal_digest"] = "0" * 64
            elif defect == "reviewer":
                spec["review"]["reviewer"] = provenance("author")
            else:
                spec["task"] = {"kind": "finite_proof"}
            before = controller.status()
            with self.subTest(defect=defect), self.assertRaises(SearchError):
                invoke(controller, "amend-computation", spec)
            self.assertEqual(controller.status(), before)

    def test_amendment_does_not_resume_pause(self):
        controller = self.historical()
        invoke(controller, "pause", {"reason": "Operator requested a retrospective only"}, None)
        try:
            invoke(controller, "amend-computation", self.amendment(controller))
        except SearchError as error:
            self.fail("Retrospective amendment does not require a resume: " + error.message)
        self.assertEqual(controller.command("next", {}, None, None)["kind"], "paused")

    def test_corrupt_amendment_review_prevents_a_new_producer(self):
        controller = self.historical()
        spec = self.amendment(controller)
        invoke(controller, "amend-computation", spec)
        review_path = controller.store.root / "blobs" / (digest(spec["review"]) + ".json")
        self.assertTrue(review_path.exists())
        review_path.write_text("{}")
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        marker = self.attack_root / "amended-producer-ran"
        (step / "job.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        before = controller.status()["accounts"]
        with self.assertRaises(SearchError):
            invoke(controller, "begin", begin_spec())
            invoke(controller, "run", command_spec(self.workspace, [sys.executable, "job.py"]))
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["accounts"], before)

    def test_native_verification_cannot_bypass_an_unamended_finite_node(self):
        from tests.search_execution_support import review_native_inputs
        controller = self.historical()
        step = self.workspace / "deterministic" / "check-1"
        step.mkdir()
        marker = self.attack_root / "native-ran"
        (step / "check.sh").write_text("#!/bin/sh\nprintf ran > '" + str(marker) + "'\n")
        (step / "check.sh").chmod(0o755)
        (step / "certificate.txt").write_text("Exact supplied proof input")
        invoke(controller, "begin", begin_spec())
        review_native_inputs(controller, self.slug, "check-1")
        before = controller.status()["accounts"]
        status, _, error = self.run_cli("verify", "certificate", self.slug, "check-1")
        self.assertNotEqual(status, 0)
        self.assertIn("computation_amendment_required", error)
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["accounts"], before)

    def test_reviewed_amendment_cannot_expand_an_existing_finite_scope(self):
        def finite_scope(candidate):
            candidate["claim"]["scope"] = {"kind": "case_ids", "case_ids": ["supplied-fixture"]}
        controller = admit_workspace(self.attack_root, computational=True, historical=True, configure=finite_scope)
        spec = self.amendment(controller)
        spec["computation"]["domain"]["case_ids"].append("another-case")
        subject = {"node_id": "node-000001", "proposal_digest": spec["proposal_digest"], "computation": spec["computation"]}
        spec["review"] = result_review(subject, controller.status()["nodes"]["node-000001"]["claim"])
        before = controller.status()["accounts"]
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "amend-computation", spec)
        self.assertIn("reviewed successor", caught.exception.message)
        self.assertEqual(controller.status()["accounts"], before)


def result_review(subject, claim):
    return {"subject_digest": digest(subject), "claim_digest": digest(claim),
            "reviewer": provenance("independent-result-reviewer"), "decision": "approve",
            "findings": {key: "Checked the exact fixture deduction, scope, assumptions, and full encoding"
                         for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}


class ComputationBasisTests(WorkspaceTest):
    def prepare(self, finite_root=False):
        self.controller = Controller(self.attack_root)
        original = contract()
        if not finite_root:
            original["original_claim"]["scope"] = {"kind": "named", "name": "All nonnegative integers"}
        self.controller.command("init", {"contract": original}, 0, "initialize")
        self.evidence = self.artifact("deduction.md", "The fixture's finite predicate is checked on exactly the declared domain.")
        candidate = proposal()
        candidate.update(claim=original["original_claim"], attack_slug="basis-root")
        candidate["anchor"]["digest"] = digest(original)
        candidate["studies"] = {"problem": self.evidence["digest"], "novelty": self.evidence["digest"],
                                "strategies": [{"method": "induction", "digest": self.evidence["digest"]}]}
        return candidate

    def artifact(self, path, text):
        (self.attack_root / path).write_text(text)
        return {"path": path, "kind": "artifact", "digest": hashlib.sha256(text.encode()).hexdigest()}

    def admit(self, candidate, inputs=None):
        invoke(self.controller, "propose", {"proposal": candidate, "inputs": inputs or []}, None)
        pid = sorted(self.controller.status()["proposals"])[-1]
        invoke(self.controller, "review", {"proposal_id": pid, "review": review(candidate), "inputs": []}, None)
        if candidate["category"] == "standalone":
            invoke(self.controller, "review", {"proposal_id": pid, "review": review(candidate, "reviewer-two"), "inputs": []}, None)
        invoke(self.controller, "admit", {}, pid)
        return self.controller.status()["nodes"][sorted(self.controller.status()["nodes"])[-1]]

    def finite(self, candidate, basis="root_finite_scope"):
        candidate.update(schema_version=2, computation=computation_contract(self.evidence["digest"]))
        candidate["computation"]["basis"]["kind"] = basis
        candidate["task"]["kind"] = "finite_proof"
        candidate["contribution"]["necessity"] = {
            "obligation_id": candidate["target_obligation"], "omission_consequence": "The exact finite cases remain open",
            "domain_justification": "All admitted cases are included in the encoding",
            "outcomes": [{"outcome": "verified", "next_action": "Review the exact proof candidate"},
                         {"outcome": "rejected", "next_action": "Retreat from this method"}],
            "stopping_condition": "Stop when every declared case is decided or the bound expires"}
        return candidate

    def reduction(self, unbounded_tail=False, accept=True, finite_root=False):
        candidate = self.prepare(finite_root=finite_root)
        first = copy.deepcopy(candidate["claim"])
        first.update(statement="The finite even case", scope={"kind": "case_ids", "case_ids": ["even"]})
        second = copy.deepcopy(first)
        second.update(statement="The finite odd case", scope={"kind": "case_ids", "case_ids": ["odd"]})
        if unbounded_tail:
            second["scope"] = {"kind": "named", "name": "The still unproved infinite tail"}
        bridge = copy.deepcopy(candidate["claim"])
        bridge["statement"] = "The two declared cases imply the root predicate"
        candidate["decomposition"] = {"obligations": [{"key": "even", "claim": first}, {"key": "odd", "claim": second},
                                                       {"key": "bridge", "claim": bridge}],
            "routes": [{"key": "finite-route", "conclusion": "obligation-000001", "premises": ["new:even", "new:odd"],
                        "bridge": "new:bridge", "alternative_order": []}]}
        self.admit(candidate, [self.evidence])
        state = self.controller.status()
        route = state["routes"]["route-000001"]
        accepted = None
        if accept:
            accepted = self.accept_bridge(state, bridge, route)
        finite = self.finite(copy.deepcopy(candidate), "finite_residue")
        finite.update(attack_slug="finite-residue", claim=first, target_obligation=route["premises"][0],
                      relationship="prerequisite", decomposition={"obligations": [], "routes": []})
        finite["contribution"]["route"] = route["id"]
        finite["contribution"]["necessity"]["obligation_id"] = finite["target_obligation"]
        finite["checkpoint_criteria"][0]["obligation_id"] = finite["target_obligation"]
        finite["computation"]["domain"] = first["scope"]
        basis = finite["computation"]["basis"]
        basis["reduction_acceptance_id"] = "acceptance-000001"
        basis["dependencies"] = [{"acceptance_id": "acceptance-000001", "acceptance_digest": digest(accepted) if accepted else "0" * 64,
                                   "claim_digest": digest(bridge)}]
        return finite

    def accept_bridge(self, state, claim, route):
        proof = self.artifact("bridge-proof.md", "For the parity fixture every integer lies in exactly one of the two residue cases.")
        binding = {"route_id": route["id"], "route_digest": digest(route), "case_obligation_ids": [],
                   "shared_prerequisite_ids": [], "discharged_assumption_ids": []}
        manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(claim),
                    "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": [binding]},
                    "artifacts": [{"path": proof["path"], "digest": proof["digest"], "role": "proof"}],
                    "dependencies": [], "external_dependencies": [], "verification": None}
        (self.attack_root / "bridge-manifest.json").write_text(json.dumps(manifest))
        cp = {"schema_version": 1, "kind": "reduction", "claim": claim,
              "origin": {"kind": "external_result", "source": "Synthetic parity fixture",
                         "source_digest": proof["digest"], "statement": claim["statement"],
                         "statement_digest": digest(claim), "study_digest": self.evidence["digest"]},
              "evidence_digests": [digest(manifest)], "what_changed": "An exact bridge proof is available",
              "remaining_obligation_ids": route["premises"], "next_hypothesis": "Check each finite residue", "milestone_id": None}
        invoke(self.controller, "checkpoint", {"checkpoint": cp, "inputs": [proof,
            {"path": "bridge-manifest.json", "kind": "blob", "digest": digest(manifest)}]}, None)
        checkpoint = self.controller.status()["checkpoints"]["checkpoint-000001"]
        invoke(self.controller, "accept", {"obligation_id": route["bridge"], "outcome": "proof", "dependency_ids": [],
               "route_bindings": [binding], "review": result_review(checkpoint, claim), "inputs": []}, checkpoint["id"])
        return self.controller.status()["acceptances"]["acceptance-000001"]

    def test_actual_finite_root_is_admitted(self):
        candidate = self.finite(self.prepare(finite_root=True))
        candidate["computation"]["domain"] = copy.deepcopy(candidate["claim"]["scope"])
        self.assertEqual(self.admit(candidate, [self.evidence])["status"], "admitted")

    def test_accepted_finite_reduction_allows_an_open_finite_sibling(self):
        candidate = self.reduction()
        self.assertEqual(self.admit(candidate)["status"], "admitted")
        self.assertEqual(self.controller.status()["proof_status"], "open")

    def test_proposed_prefix_tail_route_is_not_a_finite_reduction(self):
        candidate = self.reduction(unbounded_tail=True, accept=False)
        before = self.controller.status()["accounts"]
        with self.assertRaises(SearchError):
            self.admit(candidate)
        self.assertEqual(self.controller.status()["accounts"], before)

    def test_accepted_prefix_tail_bridge_still_requires_the_unbounded_tail(self):
        candidate = self.reduction(unbounded_tail=True)
        with self.assertRaises(SearchError):
            self.admit(candidate)

    def accepted_tail(self, pin=True):
        candidate = self.reduction(unbounded_tail=True)
        state = self.controller.status()
        route = state["routes"]["route-000001"]
        tail_id = route["premises"][1]
        tail_claim = state["obligations"][tail_id]["claim"]
        proof = self.artifact("tail-proof.md", "Independent analytical proof of the fixture's complete named tail.")
        manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(tail_claim),
                    "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
                    "artifacts": [{"path": proof["path"], "digest": proof["digest"], "role": "proof"}],
                    "dependencies": [], "external_dependencies": [], "verification": None}
        (self.attack_root / "tail-manifest.json").write_text(json.dumps(manifest))
        checkpoint = {"schema_version": 1, "kind": "proof", "claim": tail_claim,
            "origin": {"kind": "external_result", "source": "Independent synthetic tail proof", "source_digest": proof["digest"],
                       "statement": tail_claim["statement"], "statement_digest": digest(tail_claim), "study_digest": self.evidence["digest"]},
            "evidence_digests": [digest(manifest)], "what_changed": "The complete named tail has an independent proof",
            "remaining_obligation_ids": [route["premises"][0]], "next_hypothesis": "Check the finite residue", "milestone_id": None}
        invoke(self.controller, "checkpoint", {"checkpoint": checkpoint, "inputs": [proof,
            {"path": "tail-manifest.json", "kind": "blob", "digest": digest(manifest)}]}, None)
        checkpoint = self.controller.status()["checkpoints"]["checkpoint-000002"]
        invoke(self.controller, "accept", {"obligation_id": tail_id, "outcome": "proof", "dependency_ids": [],
            "route_bindings": [], "review": result_review(checkpoint, tail_claim), "inputs": []}, checkpoint["id"])
        tail = self.controller.status()["acceptances"]["acceptance-000002"]
        if pin:
            candidate["computation"]["basis"]["dependencies"].append(
                {"acceptance_id": tail["id"], "acceptance_digest": digest(tail), "claim_digest": digest(tail_claim)})
        return candidate, proof["digest"], tail

    def finite_producer(self, candidate):
        from tests.support import ALL_YES, FIXTURE_STRATEGIES, OPENING, make_preconditions, make_problem, write_study
        self.controller.strategies_dir = FIXTURE_STRATEGIES
        candidate["method"] = OPENING
        candidate["studies"]["strategies"] = [{"method": OPENING, "digest": self.evidence["digest"]}]
        node = self.admit(candidate)
        workspace = self.attack_root / node["attack_slug"]
        problem = make_problem()
        problem["claim"] = candidate["claim"]["statement"]
        (workspace / "problem.json").write_text(json.dumps(problem))
        write_study(workspace, "problem")
        write_study(workspace, OPENING)
        (workspace / "preconditions.json").write_text(json.dumps(make_preconditions(ALL_YES)))
        status, _, error = self.run_cli("plan", node["attack_slug"])
        self.assertEqual((status, error), (0, ""))
        openings = json.loads((workspace / "openings.json").read_text())["openings"]
        (workspace / "ranking.json").write_text(json.dumps({"generated_from": "openings.json", "order": [
            {"strategy": item["strategy"], "cites": ["shape.objects"], "reason": "The studied finite fixture applies"}
            for item in openings]}))
        marker = self.attack_root / "tail-based-producer-ran"
        step = workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        from tests.strategy_refresh_support import reassess_fixture
        reassess_fixture(self.controller, {(node["id"], OPENING)})
        invoke(self.controller, "begin", begin_spec(), node["id"])
        return node, marker, command_spec(workspace, [sys.executable, "job.py"])

    def test_unpinned_corrupted_named_tail_cannot_authorize_admission(self):
        candidate, artifact, _ = self.accepted_tail(pin=False)
        (self.controller.store.root / "artifacts" / artifact).write_text("Corrupted independent tail proof")
        marker = self.attack_root / "tail-based-producer-ran"
        before = self.controller.status()["accounts"]
        with self.assertRaises(SearchError) as caught:
            node, _, spec = self.finite_producer(candidate)
            invoke(self.controller, "run", spec, node["id"])
        self.assertEqual(caught.exception.code, "computation_required")
        self.assertEqual(self.controller.status()["accounts"], before)
        self.assertFalse(marker.exists())

    def test_valid_pinned_named_tail_allows_actual_finite_execution(self):
        candidate, _, _ = self.accepted_tail()
        node, marker, spec = self.finite_producer(candidate)
        invoke(self.controller, "run", spec, node["id"])
        self.assertTrue(marker.exists())
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 1)

    def test_pinned_tail_corruption_before_launch_preserves_the_original_account(self):
        candidate, artifact, _ = self.accepted_tail()
        node, marker, spec = self.finite_producer(candidate)
        before = self.controller.status()["accounts"]
        (self.controller.store.root / "artifacts" / artifact).write_text("Corrupted after move reservation")
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", spec, node["id"])
        self.assertEqual(caught.exception.code, "corrupt_artifact")
        self.assertEqual(self.controller.status()["accounts"], before)
        self.assertFalse(marker.exists())

    def test_withdrawn_pinned_tail_before_launch_preserves_the_original_account(self):
        candidate, artifact, tail = self.accepted_tail()
        node, marker, spec = self.finite_producer(candidate)
        before = self.controller.status()["accounts"]
        (self.controller.store.root / "artifacts" / artifact).write_text("Corrupted before explicit audit")
        invoke(self.controller, "audit", {}, None)
        self.assertNotEqual(self.controller.status()["acceptances"][tail["id"]]["status"], "accepted")
        with self.assertRaises(SearchError):
            invoke(self.controller, "run", spec, node["id"])
        self.assertEqual(self.controller.status()["accounts"], before)
        self.assertFalse(marker.exists())

    def test_stale_bound_evidence_preserves_accounts(self):
        candidate = self.reduction()
        candidate["computation"]["basis"]["bound_acceptance_id"] = "acceptance-000001"
        proof = self.controller.status()["checkpoints"]["checkpoint-000001"]["evidence_digests"][0]
        artifact = self.controller.store.get_blob(proof)["artifacts"][0]["digest"]
        (self.controller.store.root / "artifacts" / artifact).write_text("Corrupted accepted bound")
        before = self.controller.status()["accounts"]
        with self.assertRaises(SearchError):
            self.admit(candidate)
        self.assertEqual(self.controller.status()["accounts"], before)

    def test_independently_significant_standalone_finite_theorem_is_admitted(self):
        root = self.prepare()
        candidate = proposal("standalone")
        candidate.update(anchor=root["anchor"], studies=root["studies"])
        candidate["contribution"]["standalone"]["primary_sources"] = [self.evidence["digest"]]
        self.finite(candidate, "standalone")
        candidate["computation"]["domain"] = copy.deepcopy(candidate["claim"]["scope"])
        self.assertEqual(self.admit(candidate, [self.evidence])["category"], "standalone")

    def test_proof_encoding_requires_completeness_as_well_as_a_bound(self):
        candidate = self.reduction()
        candidate["computation"]["domain"] = {"kind": "bounded_encoding", "encoding_digest": self.evidence["digest"], "max_instances": 2}
        candidate["computation"]["basis"]["bound_acceptance_id"] = "acceptance-000001"
        with self.assertRaises(SearchError):
            self.admit(candidate)
        candidate["attack_slug"] = "complete-encoding"
        candidate["computation"]["basis"]["completeness_acceptance_id"] = "acceptance-000001"
        self.assertEqual(self.admit(candidate)["status"], "admitted")

    def test_reviewed_diagnostic_encoding_can_have_no_completeness_acceptance(self):
        self.reduction()
        state = self.controller.status()
        finite_candidate = self.finite(copy.deepcopy(state["proposals"]["proposal-000001"]["record"]))
        finite_candidate.update(attack_slug="encoded-diagnostic", decomposition={"obligations": [], "routes": []})
        finite_candidate["task"]["kind"] = "finite_decision"
        finite_candidate["computation"]["basis"]["kind"] = "diagnostic"
        finite_candidate["computation"]["domain"] = {"kind": "bounded_encoding", "encoding_digest": self.evidence["digest"], "max_instances": 2}
        self.assertEqual(self.admit(finite_candidate)["status"], "admitted")

    def test_finite_root_encoding_uses_the_reviewed_claim_scope(self):
        residue = self.reduction(finite_root=True)
        state = self.controller.status()
        candidate = copy.deepcopy(state["proposals"]["proposal-000001"]["record"])
        candidate.update(attack_slug="encoded-root", decomposition={"obligations": [], "routes": []})
        self.finite(candidate)
        candidate["computation"]["domain"] = {"kind": "bounded_encoding", "encoding_digest": self.evidence["digest"], "max_instances": 11}
        candidate["computation"]["basis"].update(
            dependencies=residue["computation"]["basis"]["dependencies"],
            completeness_acceptance_id="acceptance-000001", bound_acceptance_id="acceptance-000001")
        try:
            self.admit(candidate)
        except SearchError as error:
            self.fail("An accepted complete encoding may cover a finite root scope: " + error.message)


class ComputationTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        try:
            self.controller = admit_workspace(self.attack_root, computational=True)
        except SearchError as error:
            self.fail("A reviewed v2 finite diagnostic must be admitted: " + error.message)

    def producer(self):
        marker = self.attack_root / "producer-ran"
        step = self.workspace / "deterministic" / "job"
        step.mkdir(exist_ok=True)
        (step / "job.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\nprint('UNKNOWN')\n")
        return marker, command_spec(self.workspace, [sys.executable, "job.py"])

    def run_admitted_finite_question(self):
        _, spec = self.producer()
        invoke(self.controller, "begin", begin_spec())
        invoke(self.controller, "run", spec)
        return "node-000001", self.controller.status()["runs"]["run-000001"]

    def launch_again(self, node):
        _, spec = self.producer()
        return invoke(self.controller, "run", spec, node)

    def interpretation(self, run):
        return {"result_digest": run["result_digest"], "computation_digest": run["computation_digest"],
                "outcome": None, "inconclusive_reason": "The producer reports no mathematical decision",
                "classification": "undecided", "root_decision": {"kind": "undecided", "reason": "No root obligation was decided"},
                "remaining_obligation_ids": ["obligation-000001"], "next_action": "Redesign the finite diagnostic"}

    def candidate(self):
        candidate = copy.deepcopy(self.controller.status()["proposals"]["proposal-000001"]["record"])
        candidate["attack_slug"] = "next-attempt"
        return candidate

    def admit_candidate(self, candidate):
        invoke(self.controller, "propose", {"proposal": candidate, "inputs": []}, None)
        pid = sorted(self.controller.status()["proposals"])[-1]
        invoke(self.controller, "review", {"proposal_id": pid, "review": review(candidate), "inputs": []}, None)
        if candidate["category"] == "standalone":
            invoke(self.controller, "review", {"proposal_id": pid, "review": review(candidate, "reviewer-two"), "inputs": []}, None)
        return invoke(self.controller, "admit", {}, pid)

    def refuse_candidate(self, candidate):
        marker, _ = self.producer()
        before = self.controller.status()["accounts"]
        with self.assertRaises(SearchError):
            self.admit_candidate(candidate)
        self.assertFalse(marker.exists())
        self.assertEqual(self.controller.status()["accounts"], before)

    def test_infinite_prefix_without_finite_reduction_is_refused(self):
        candidate = self.candidate()
        candidate["computation"]["basis"]["kind"] = "root_finite_scope"
        candidate["computation"]["domain"] = {"kind": "integer_interval", "lower": 1, "upper": 20,
                                               "lower_inclusive": True, "upper_inclusive": True}
        self.refuse_candidate(candidate)

    def test_named_domain_is_not_a_finite_encoding(self):
        candidate = self.candidate()
        candidate["computation"]["domain"] = {"kind": "named", "name": "Every positive integer"}
        self.refuse_candidate(candidate)

    def test_predetermined_producer_is_refused(self):
        candidate = self.candidate()
        candidate["computation"]["preflight"]["already_determined"] = True
        self.refuse_candidate(candidate)

    def test_unclassified_result_cannot_launch_again(self):
        node, run = self.run_admitted_finite_question()
        # Journal acknowledgement retains precedence until the move is recorded.
        from tests.support import make_move
        status, _, error = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "interpret_run")
        before = self.controller.status()["accounts"]
        with self.assertRaises(SearchError):
            self.launch_again(node)
        self.assertEqual(self.controller.status()["accounts"], before)

    def test_interpretation_is_immutable_and_grants_no_acceptance(self):
        node, run = self.run_admitted_finite_question()
        spec = self.interpretation(run)
        revision = self.controller.status()["revision"]
        result = self.controller.command("interpret", spec, revision, "interpret-once", run["id"])
        self.assertEqual(self.controller.command("interpret", spec, revision, "interpret-once", run["id"]), result)
        state = self.controller.status()
        self.assertEqual(state["acceptances"], {})
        self.assertEqual(state["proof_status"], "open")
        with self.assertRaises(SearchError):
            invoke(self.controller, "interpret", spec, run["id"])
        with self.assertRaises(SearchError) as caught:
            self.launch_again(node)
        self.assertEqual(caught.exception.code, "strategy_reassessment_required")
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 1)
        self.assertEqual(len(self.controller.status()["reviews"]), 1)

    def test_changed_result_or_contract_cannot_be_interpreted(self):
        _, run = self.run_admitted_finite_question()
        before = self.controller.status()["accounts"]
        for field in ["result_digest", "computation_digest"]:
            spec = self.interpretation(run)
            spec[field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(SearchError):
                invoke(self.controller, "interpret", spec, run["id"])
        self.assertEqual(self.controller.status()["accounts"], before)

    def test_finite_decision_cannot_claim_proof_candidacy(self):
        _, run = self.run_admitted_finite_question()
        spec = self.interpretation(run)
        spec.update(outcome="verified", inconclusive_reason=None, classification="proof_candidate")
        spec["root_decision"] = {"kind": "proof_candidate", "reason": "A claimed theorem"}
        with self.assertRaises(SearchError):
            invoke(self.controller, "interpret", spec, run["id"])

    def test_changed_frozen_log_prevents_interpretation(self):
        _, run = self.run_admitted_finite_question()
        result = self.controller.store.get_blob(run["result_digest"])
        log = self.controller.store.root / "artifacts" / result["commands"][0]["stdout_digest"]
        log.write_text("Tampered mathematical conclusion")
        with self.assertRaises(SearchError):
            invoke(self.controller, "interpret", self.interpretation(run), run["id"])

    def test_invalid_verification_plan_does_not_allocate(self):
        for field, value in [("checker_seconds", 301), ("max_input_bytes", 0), ("producer_seconds", True)]:
            candidate = self.candidate()
            candidate["computation"]["verification_plan"][field] = value
            with self.subTest(field=field):
                self.refuse_candidate(candidate)

    def test_unknown_outcome_requires_an_inconclusive_interpretation(self):
        _, run = self.run_admitted_finite_question()
        spec = self.interpretation(run)
        spec.update(outcome="UNKNOWN", inconclusive_reason=None)
        with self.assertRaises(SearchError):
            invoke(self.controller, "interpret", spec, run["id"])
        invoke(self.controller, "interpret", self.interpretation(run), run["id"])

    def test_new_run_replay_refuses_changed_or_missing_computation_binding(self):
        from search_controller.model import replay
        _, run = self.run_admitted_finite_question()
        document = self.controller.store.read()
        for remove in [False, True]:
            changed = copy.deepcopy(document)
            operation = next(event["payload"]["operations"][0] for event in changed["events"]
                             if event["kind"] == "service_operation" and event["payload"]["command"] == "run")
            if remove:
                del operation["payload"]["run"]["computation_digest"]
            else:
                operation["payload"]["run"]["computation_digest"] = "0" * 64
            with self.subTest(remove=remove), self.assertRaises(SearchError):
                replay(changed)
        self.assertEqual(replay(document)["totals"]["used_runs"], 1)

    def test_pending_result_blocks_a_renamed_node_without_spending(self):
        candidate = self.candidate()
        self.admit_candidate(candidate)
        self.run_admitted_finite_question()
        before = self.controller.status()["accounts"]
        with self.assertRaises(SearchError) as caught:
            self.launch_again("node-000002")
        self.assertEqual(caught.exception.code, "interpretation_required")
        self.assertEqual(self.controller.status()["accounts"], before)

    def test_pause_precedes_interpretation_and_status_never_writes_it(self):
        self.run_admitted_finite_question()
        invoke(self.controller, "pause", {"reason": "Retrospective only"}, None)
        before = self.controller.store.tree_path.read_bytes()
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "paused")
        self.assertEqual(self.controller.status()["service"]["run_interpretations"], {})
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_interpret_cli_records_the_exact_run(self):
        _, run = self.run_admitted_finite_question()
        path = self.attack_root / "interpret.json"
        path.write_text(json.dumps(self.interpretation(run)))
        revision = self.controller.status()["revision"]
        status, output, error = self.run_cli("search", "interpret", run["id"], "--spec", str(path),
            "--expected-revision", str(revision), "--request-id", "cli-interpret", "--json")
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(json.loads(output)["command"], "interpret")


class ComputationVerificationTests(WorkspaceTest):
    def test_already_determined_inputs_still_allow_independent_native_verification(self):
        from tests.search_execution_support import review_native_inputs
        def configure(candidate):
            candidate["role"] = "verification"
            candidate["computation"]["preflight"]["already_determined"] = True
        controller = admit_workspace(self.attack_root, computational=True, configure=configure)
        step = self.workspace / "deterministic" / "check-1"
        step.mkdir()
        (step / "check.sh").write_text("#!/bin/sh\nprintf checked\n")
        (step / "check.sh").chmod(0o755)
        (step / "certificate.txt").write_text("True.intro\n")
        invoke(controller, "begin", begin_spec())
        review_native_inputs(controller, self.slug, "check-1")
        status, _, error = self.run_cli("verify", "certificate", self.slug, "check-1")
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(controller.status()["totals"]["used_runs"], 1)

    def test_standalone_candidate_cannot_claim_a_root_decision(self):
        from search_controller.model import apply_event, replay
        def configure(candidate):
            candidate.update(category="standalone", relationship="standalone", target_obligation=None)
            candidate["task"]["kind"] = "finite_proof"
            candidate["claim"]["scope"] = {"kind": "case_ids", "case_ids": ["supplied-fixture"]}
            candidate["contribution"]["standalone"] = proposal("standalone")["contribution"]["standalone"]
            candidate["contribution"]["standalone"]["primary_sources"] = [candidate["studies"]["problem"]]
            candidate["contribution"]["necessity"]["obligation_id"] = None
            candidate["computation"]["basis"]["kind"] = "standalone"
        controller = admit_workspace(self.attack_root, computational=True, configure=configure)
        # Existing pure operator control allocates the standalone test interval.
        # This is no acceptance and is not an arbitrary public event API.
        controller.store.append("control_recorded", {"action": "side_interval", "node_id": "node-000001", "max_moves": 1,
            "message_id": "fixture-side-allocation", "provenance": provenance("operator")},
            controller.status()["revision"], "fixture-side-allocation",
            validate=lambda document, event: apply_event(replay(document), event))
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('standalone proof candidate')\n")
        invoke(controller, "begin", begin_spec())
        invoke(controller, "run", command_spec(self.workspace, [sys.executable, "job.py"]))
        run = controller.status()["runs"]["run-000001"]
        spec = {"result_digest": run["result_digest"], "computation_digest": run["computation_digest"],
                "outcome": "verified", "inconclusive_reason": None, "classification": "proof_candidate",
                "root_decision": {"kind": "proof_candidate", "reason": "An asserted root consequence"},
                "remaining_obligation_ids": ["obligation-000001"], "next_action": "Review the exact correspondence with the proposition"}
        with self.assertRaises(SearchError):
            invoke(controller, "interpret", spec, run["id"])
        spec["root_decision"] = {"kind": "undecided", "reason": "This standalone candidate proves no root obligation"}
        invoke(controller, "interpret", spec, run["id"])
        self.assertEqual(controller.status()["proof_status"], "open")


class ComputationProofTests(WorkspaceTest):
    def configure(self, candidate):
        candidate["task"]["kind"] = "finite_proof"
        candidate["claim"]["scope"] = {"kind": "case_ids", "case_ids": ["supplied-fixture"]}
        candidate["computation"]["basis"]["kind"] = "root_finite_scope"

    def run_question(self, timeout=False):
        controller = admit_workspace(self.attack_root, computational=True, configure=self.configure)
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("import time\ntime.sleep(20)\n" if timeout else "print('proof candidate')\n")
        invoke(controller, "begin", begin_spec())
        invoke(controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 1 if timeout else 2))
        run = controller.status()["runs"]["run-000001"]
        spec = {"result_digest": run["result_digest"], "computation_digest": run["computation_digest"],
                "outcome": "verified", "inconclusive_reason": None, "classification": "proof_candidate",
                "root_decision": {"kind": "proof_candidate", "reason": "The complete finite domain has a candidate proof"},
                "remaining_obligation_ids": ["obligation-000001"], "next_action": "Review the exact correspondence with the proposition"}
        return controller, run, spec

    def test_successful_finite_proof_can_be_interpreted_as_a_candidate_only(self):
        controller, run, spec = self.run_question()
        try:
            invoke(controller, "interpret", spec, run["id"])
        except SearchError as error:
            self.fail("A successful finite proof may have candidate interpretation: " + error.message)
        self.assertEqual(controller.status()["acceptances"], {})
        self.assertEqual(controller.status()["proof_status"], "open")

    def test_timeout_cannot_claim_a_proof_candidate(self):
        controller, run, spec = self.run_question(timeout=True)
        self.assertEqual(run["termination"], "timeout")
        with self.assertRaises(SearchError):
            invoke(controller, "interpret", spec, run["id"])
        spec.update(outcome=None, inconclusive_reason="The time limit expired", classification="undecided",
                    root_decision={"kind": "undecided", "reason": "No theorem was decided"}, next_action="Redesign the checker")
        invoke(controller, "interpret", spec, run["id"])
        self.assertEqual(controller.status()["totals"]["used_runs"], 1)

    def test_partial_candidate_can_leave_the_root_undecided(self):
        controller, run, spec = self.run_question()
        spec["root_decision"] = {"kind": "undecided", "reason": "The candidate still needs independent verification"}
        try:
            invoke(controller, "interpret", spec, run["id"])
        except SearchError as error:
            self.fail("Partial candidates may leave the root undecided: " + error.message)

    def test_explicit_unknown_outcome_cannot_claim_a_theorem_candidate(self):
        configure = self.configure
        def with_unknown(candidate):
            configure(candidate)
            candidate["contribution"]["necessity"]["outcomes"][0]["outcome"] = "UNKNOWN"
        self.configure = with_unknown
        controller, run, spec = self.run_question()
        spec["outcome"] = "UNKNOWN"
        with self.assertRaises(SearchError):
            invoke(controller, "interpret", spec, run["id"])
