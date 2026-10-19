"""Cited computation support uses immutable execution assessments as scoped evidence."""

import sys
import json
import copy
from unittest.mock import patch

from development_fixtures import PROGRAM
from research_harness import development, execution, probe, research_decisions, review_protocol
from research_harness.evidence import digest
import test_research_problem_claims as claim_fixtures
import test_research_problem_reviews as review_fixtures
from test_research_problem_tools import ProblemToolsCase
from test_research_problem_delivery import ProblemDeliveryCase


class ProblemComputationTests(ProblemToolsCase):
    build_provider_response = review_fixtures.ProblemReviewTests.build_provider_response

    def build_computation_evidence(self, observed):
        return {"kind": "computation", "execution_id": observed["id"], "output_id": "result", "formal_check": None}

    def assess_support(self, plan, observed, **changes):
        payload = self.assessment(plan, observed, development=False)
        payload.update(objective_status="open", disposition="continue",
            remaining_obligations=["The general problem is unresolved."], **changes)
        return self.mutate(development.assess_cycle, payload)["result"]

    def test_value_accepts_empirical_support_with_unfinished_objective_obligations(self):
        plan, observed = self.run_problem()
        assessment = self.assess_support(plan, observed)
        self.assertTrue({"objective_incomplete", "remaining_obligations"} <=
            {row["code"] for row in assessment["obligations"]})
        saved = self.mutate(self.load_problem_module().record_value, self.build_value([self.build_computation_evidence(observed)]))["result"]
        self.assertEqual(saved["motivation"][0]["evidence"], [self.build_computation_evidence(observed)])

    def test_value_refuses_a_reconciled_execution_without_its_assessment(self):
        _, observed = self.run_problem()
        self.assert_error("problem_value_computation_missing", lambda: self.mutate(self.load_problem_module().record_value,
            self.build_value([self.build_computation_evidence(observed)])))
        self.assertFalse(self.store.snapshot()["records"].get("problem_value"))

    def test_value_refuses_a_recorded_resource_overrun(self):
        plan, observed = self.run_problem(reserved_units=0.5)
        assessment = self.assess_support(plan, observed)
        self.assertIn("resource_limit_exceeded", {row["code"] for row in assessment["obligations"]})
        self.assert_error("problem_value_computation_missing", lambda: self.mutate(self.load_problem_module().record_value,
            self.build_value([self.build_computation_evidence(observed)])))

    def test_unplanned_result_scope_cannot_supply_valid_empirical_support(self):
        plan, observed = self.run_problem()
        payload = self.assessment(plan, observed, development=False)
        payload["scope"].update(id="unplanned-scope", assumptions=[])
        payload["assumptions"] = []
        assessed = self.mutate(development.assess_cycle, payload)["result"]
        self.assertFalse(assessed["validated_result"])
        self.assertIn("result_scope_unplanned", {row["code"] for row in assessed["obligations"]})
        self.assert_error("problem_value_computation_missing", lambda: self.mutate(self.load_problem_module().record_value,
            self.build_value([self.build_computation_evidence(observed)])))

    def test_value_keeps_its_immutable_assessment_when_a_later_assessment_is_recorded(self):
        plan, observed = self.run_problem()
        first = self.assess_support(plan, observed)
        self.mutate(self.load_problem_module().record_value, self.build_value([self.build_computation_evidence(observed)]))
        before = self.load_problem_module().compute_problem_evidence_digest(self.store.snapshot()["records"], self.artifacts)
        changed = self.assessment(plan, observed, identifier="later-unplanned-assessment", development=False)
        changed["scope"].update(id="a-different-result-scope", assumptions=[])
        changed["assumptions"] = []
        self.mutate(development.assess_cycle, changed)
        after = self.load_problem_module().compute_problem_evidence_digest(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(before, after)
        supplied = self.load_problem_module().describe_problem_evidence(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(supplied["computations"][0]["assessment"]["id"], first["id"])

    def test_an_unresolved_validity_check_for_the_cited_output_blocks_support(self):
        plan, observed = self.run_problem()
        payload = self.assessment(plan, observed, development=False)
        unresolved = dict(copy.deepcopy(payload["validity_checks"][0]), id="unresolved-sensitivity", status="unresolved")
        payload["validity_checks"].append(unresolved)
        assessed = self.mutate(development.assess_cycle, payload)["result"]
        codes = {row["code"] for row in assessed["obligations"]}
        self.assertIn("validity_unresolved", codes)
        self.assertNotIn("required_validation_unverified", codes)
        self.assert_error("problem_value_computation_missing", lambda: self.mutate(self.load_problem_module().record_value,
            self.build_value([self.build_computation_evidence(observed)])))

    def test_value_refuses_probe_origin_even_after_an_execution_was_assessed(self):
        plan, observed = self.run_problem()
        self.assess_support(plan, observed)
        script = self.root / "same-observation.py"
        script.write_bytes(PROGRAM)
        probe.launch_probe(self.store, identifier="exploratory", question="What values occur on the finite inputs?",
            script=script, timeout=5)
        self.assert_error("exploratory_evidence", lambda: self.mutate(self.load_problem_module().record_value,
            self.build_value([self.build_computation_evidence(observed)])))

    def run_second_execution(self, plan):
        body = "import json\nprint(json.dumps({'result': {'values': [0, 1, 4], 'bound': 4}}))\n"
        path = self.root / "experiment/code/second.py"
        path.write_text(body)
        pinned = self.store.snapshot()["records"]["cycle_plan"][plan["id"]]
        admission = self.mutate(development.admit_execution, {"id": "second-run", "cycle_id": plan["id"],
            "plan_digest": pinned["digest"], "reserved_units": 1,
            "command": {"argv": [sys.executable, str(path)], "program": self.artifacts.put(body.encode(), "text/x-python"),
                "inputs": [], "versions": {"python": sys.version.split()[0]}, "seed": None,
                "seed_reason": "The finite fixture is deterministic."}})["result"]
        self.mutate(execution.bind_execution, {"admission_id": admission["id"], "script": "code/second.py",
            "backend": "local", "timeout_seconds": 5, "inputs": [], "usage_unit": "execution",
            "outputs": [{"id": "result", "requirement_id": "measurements", "path": "stdout", "media_type": "application/json"}]})
        execution.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="launch-second")
        records = self.store.snapshot()["records"]
        return records["execution"][records["execution_outcome"][admission["id"]]["execution_id"]]["payload"]

    def test_obligations_about_another_result_do_not_block_the_cited_valid_result(self):
        plan, observed = self.run_problem()
        other = self.run_second_execution(plan)
        payload = self.assessment(plan, observed, development=False)
        payload.update(execution_ids=[observed["id"], other["id"]], objective_status="open", disposition="continue",
            remaining_obligations=["The second result has no validation yet."])
        payload["result"]["evidence"].append(self.result_evidence(other))
        payload["validity_checks"].append(dict(copy.deepcopy(payload["validity_checks"][0]),
            id="other-output-check", status="unresolved", evidence=[self.result_evidence(other)]))
        assessed = self.mutate(development.assess_cycle, payload)["result"]
        self.assertIn("validity_evidence_unbound", {row["code"] for row in assessed["obligations"]})
        saved = self.mutate(self.load_problem_module().record_value, self.build_value([self.build_computation_evidence(observed)]))["result"]
        self.assertEqual(saved["statement_id"], "statement-1")
        self.assert_error("problem_value_computation_missing", lambda: self.mutate(self.load_problem_module().record_value,
            self.build_value([self.build_computation_evidence(other)], "invalid-other")))

    def test_an_output_named_only_as_an_outcome_is_not_an_assessed_own_result(self):
        plan, observed = self.run_problem()
        other = self.run_second_execution(plan)
        payload = self.assessment(plan, observed, development=False)
        payload.update(execution_ids=[observed["id"], other["id"]], objective_status="open", disposition="continue",
            remaining_obligations=["The second result has not been assessed for validity."])
        payload["outcomes"][0]["evidence"].append(self.result_evidence(other))
        self.mutate(development.assess_cycle, payload)
        self.assert_error("problem_value_computation_missing", lambda: self.mutate(self.load_problem_module().record_value,
            self.build_value([self.build_computation_evidence(other)])))

    def test_requested_formal_check_binds_and_is_delivered_with_real_computation_records(self):
        plan, observed = self.run_problem()
        self.assess_support(plan, observed)
        self.claim = {"statement": "The first four values satisfy the bound.", "quantifiers": "For n in [0,3]",
            "assumption_ids": [], "proof_policy": "lean-kernel", "scope": {"kind": "named", "name": "finite range"}}
        item = dict(self.build_computation_evidence(observed), formal_check={"claim": self.claim, "required_deliverables": ["range check"]})
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        from research_harness.problem_evidence import assess_formal_checks
        self.assertEqual(assess_formal_checks(self.store.snapshot()["records"])[0]["code"], "problem_formal_check_missing")
        native = claim_fixtures.ProblemFormalBindingTests.build_closure_record(self)
        identity = digest(native["closure"])
        self.put("native_closure", identity, native)
        self.mutate(self.load_problem_module().record_formal_check, {"evidence": {"execution_id": observed["id"], "output_id": "result"},
            "native_closure_digest": identity})
        self.assertEqual(assess_formal_checks(self.store.snapshot()["records"]), [])
        review_fixtures.ProblemReviewTests.prepare_review_route(self)
        self.supporting = self.artifacts.put(b"The authored field standard used by both bar fixtures.", "text/plain")
        for reviewer in ("first", "second"):
            identifier = "bar-" + reviewer
            self.mutate(review_protocol.record_assignment, {"id": identifier, "route_id": "route", "dossier_id": None,
                "role": "bar", "reviewer_id": reviewer, "author_id": "problem-author", "context": {}})
            output = review_fixtures.ProblemReviewTests.build_review_output(self, "bar")
            with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
                self.mutate(review_protocol.invoke_assignment, {"id": "attempt-" + identifier,
                    "assignment_id": identifier}, credential="test-only")
            self.mutate(research_decisions.record_value_review, dict(output, id="review-" + identifier,
                dossier_id=None, assignment_id=identifier))
        packet = review_protocol.build_packet(self.store.snapshot()["records"], self.artifacts, None, "result", "first")
        evidence = packet["problem_evidence"]
        self.assertEqual(evidence["formal_checks"][0]["native_closure"], native)
        for computation in evidence["computations"]:
            self.assertEqual(computation["execution"]["payload"], observed)
            self.assertEqual(computation["assessment"]["payload"]["execution_ids"], [observed["id"]])
        delivered = {row["artifact"]["sha256"] for row in packet["evidence"]}
        self.assertTrue({row["artifact"]["sha256"] for row in observed["outputs"]} <= delivered)
        self.assertIn("original_claim", json.dumps(evidence["formal_checks"]))


class ProblemFormalGateTests(ProblemToolsCase, ProblemDeliveryCase):
    def prepare_requested_support(self, requested=True):
        plan, observed = self.run_problem()
        self.selected = self.load_problem_module().find_current_statement(self.store.snapshot()["records"])
        ProblemComputationTests.assess_support(self, plan, observed)
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        self.claim = {"statement": "The first four values satisfy the bound.", "quantifiers": "For n in [0,3]",
            "assumption_ids": [], "proof_policy": "lean-kernel", "scope": {"kind": "named", "name": "finite range"}}
        item = dict(ProblemComputationTests.build_computation_evidence(self, observed),
                    formal_check={"claim": self.claim, "required_deliverables": ["range check"]} if requested else None)
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        from test_research_problem_assessment import build_assessment
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected))
        self.supporting = self.artifacts.put(b"The fixture's observed field standard.", "text/plain")
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer)
        self.record_slate()
        for reviewer in ("first", "second"):
            self.observe("result", reviewer)
        return observed, item

    def test_new_probe_origin_is_an_explicit_post_gate_obligation(self):
        from research_harness.gates import gate_state
        self.prepare_requested_support(requested=False)
        self.record_post_decision()
        script = self.root / "exploratory-repeat.py"
        script.write_bytes(PROGRAM)
        probe.launch_probe(self.store, identifier="new-origin", question="Explore these finite inputs.", script=script, timeout=5)
        records = self.store.snapshot()["records"]
        records["problem_decision"]["decision"]["problem_evidence_digest"] = None
        self.assertEqual({o["code"] for o in gate_state(records, self.artifacts, "post")["obligations"]}, {"exploratory_evidence"})

    def test_requested_formal_check_is_an_isolated_post_obligation_until_bound_and_delivered(self):
        from research_harness.gates import gate_state
        observed, item = self.prepare_requested_support()
        records = self.store.snapshot()["records"]
        records["problem_decision"] = {"current": {"id": "current", "action": "post", "recorded_revision": self.store.revision,
            "review_ids": ["review-result-first", "review-result-second"], "bar_review_ids": ["review-bar-first", "review-bar-second"],
            "problem_evidence_digest": self.load_problem_module().compute_problem_evidence_digest(records, self.artifacts)}}
        self.assertEqual({o["code"] for o in gate_state(records, self.artifacts, "post")["obligations"]}, {"problem_formal_check_missing"})
        native = claim_fixtures.ProblemFormalBindingTests.build_closure_record(self)
        identity = digest(native["closure"])
        self.put("native_closure", identity, native)
        self.mutate(self.load_problem_module().record_formal_check, {"evidence": {"execution_id": observed["id"], "output_id": "result"},
            "native_closure_digest": identity})
        for reviewer in ("first", "second"):
            self.observe("result", reviewer, identifier="checked-" + reviewer)
        self.mutate(self.load_problem_module().record_decision, {"id": "post-checked", "action": "post", "reason": "The requested native completion is bound and delivered.",
            "review_ids": ["review-checked-first", "review-checked-second"], "bar_review_ids": ["review-bar-first", "review-bar-second"]})
        report = gate_state(self.store.snapshot()["records"], self.artifacts, "post")
        self.assertTrue(report["ready"], report["obligations"])
        changed = copy.deepcopy(item)
        changed["formal_check"]["claim"]["statement"] = "A stronger requested support claim"
        self.mutate(self.load_problem_module().record_value, self.build_value([changed], "changed-support"))
        records = self.store.snapshot()["records"]
        records["problem_decision"]["post-checked"]["problem_evidence_digest"] = self.load_problem_module().compute_problem_evidence_digest(records, self.artifacts)
        self.assertEqual({o["code"] for o in gate_state(records, self.artifacts, "post")["obligations"]}, {"problem_formal_check_stale"})
