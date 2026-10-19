"""Problem claims use real common-store records without ai-science authority."""

import copy
import importlib
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.gates import gate_state
from research_harness.integration import adopt_workspace, initialize_workspace
from research_harness.literature import import_bundle
from research_harness.principles import initialize_research, set_target
from research_harness.reading import record_reading
from research_harness import strategy


class ProblemCase(LiteratureCase):
    def load_problem_module(self):
        return importlib.import_module("research_harness.problem_claims")

    def initialize_problem(self):
        return self.mutate(self.load_problem_module().initialize_problem, {
            "slug": "bounded-sequences", "area": "math.CO", "field": "Extremal sequences and finite bounds",
            "authors": ["problem-author"]})["result"]

    def build_statement(self, identifier="statement-1", **changes):
        return dict({"id": identifier, "title": "A finite bound problem", "area": "math.CO",
            "statement": r"For every $n$, does $B(n)\leq n^2$ hold?",
            "resolution_criteria": r"Prove the bound for all $n$ or exhibit a counterexample.",
            "neighbours": [], "formal_statement": None}, **changes)

    def select_statement(self, **changes):
        return self.mutate(self.load_problem_module().record_statement, self.build_statement(**changes))["result"]

    def put_selecting_slate(self):
        """Stand in for a recorded slate that selects candidate-1; the slate tests record validated slates."""
        from test_research_problem_candidates import build_candidate, build_slate
        self.put("problem_candidate", "candidate-1", dict(build_candidate(), recorded_revision=self.store.revision + 1))
        self.put("problem_slate", "slate-1", dict(build_slate(), candidate_ids=["candidate-1"],
                                                  recorded_revision=self.store.revision + 1))

    def put(self, kind, key, value):
        self.sequence += 1
        return self.store.mutate("test.record", {"kind": kind, "key": key, "value": value},
            lambda tx: tx.put(kind, key, value), expected_revision=self.store.revision,
            request_id="record-" + str(self.sequence))

    def assert_error(self, code, function):
        with self.assertRaises(ResearchError) as caught:
            function()
        self.assertEqual(caught.exception.code, code, caught.exception.as_dict())

    def build_value(self, evidence, identifier="value-1"):
        return {"id": identifier, "statement_id": "statement-1",
            "motivation": [{"text": "The bound would connect the finite constructions.", "evidence": evidence}],
            "current_state": [{"text": "Only bounded examples have been established.", "evidence": evidence}]}

    def record_full_reading(self):
        version = self.metadata()
        bundle = self.bundle(version)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "read-full"))
        return {"kind": "reading", "version_id": version, "reading_id": "read-full"}


class ProblemRecordTests(ProblemCase):
    def test_marker_has_discriminator_immutable_authors_and_no_history(self):
        marker = self.initialize_problem()
        records = self.store.snapshot()["records"]
        self.assertEqual(marker["version"], 1)
        self.assertEqual(marker["orchestration"], "problem_creation")
        self.assertEqual(strategy.find_orchestration(records), "problem_creation")
        self.assertFalse(strategy.managed_research(records))
        from research_harness.claim_dependencies import uses_claim_dependencies
        self.assertFalse(uses_claim_dependencies(records))
        self.assertFalse(hasattr(records, "read_snapshot"))
        self.assertIsNone(records["configuration"]["research"]["target"])
        self.assertEqual(marker["authors"], ["problem-author"])
        self.assert_error("orchestration_conflict", self.initialize_problem)

    def test_ai_science_markers_conflict_with_problem_initialization(self):
        for kind, key in (("workspace", "study"), ("workspace", "draft"), ("research_intent", "intent")):
            with self.subTest(kind=kind, key=key):
                records = {kind: {key: {}}}
                self.assertEqual(strategy.find_orchestration(records), "ai_science")
        self.put("workspace", "study", {"stage": "initiate"})
        self.assert_error("orchestration_conflict", self.initialize_problem)

    def test_a_store_with_a_research_target_refuses_a_problem_record(self):
        self.mutate(initialize_research, {"profile": "research", "target": None})
        self.mutate(set_target, {"target": {"kind": "objective", "id": "complete-objective",
            "statement": "Establish the stated bound over the complete declared domain."},
            "reason": "Fix the complete objective before planning."})
        self.assert_error("orchestration_conflict", self.initialize_problem)

    def test_a_configured_store_without_a_target_accepts_a_problem_record(self):
        self.mutate(initialize_research, {"profile": "research", "target": None})
        self.assertEqual(self.initialize_problem()["orchestration"], "problem_creation")

    def test_problem_refuses_study_adoption_and_intent(self):
        self.initialize_problem()
        self.assert_error("orchestration_conflict", lambda: self.mutate(initialize_workspace, {"kind": "study", "state": {}}))
        self.assert_error("orchestration_conflict", lambda: self.mutate(adopt_workspace, {}))
        self.assert_error("orchestration_conflict", lambda: self.mutate(strategy.record_intent, {}))

    def test_statement_selection_is_append_only_and_digest_covers_exact_claim(self):
        self.initialize_problem()
        first = self.select_statement()
        second = self.select_statement(identifier="statement-2", neighbours=["A sharper bound"])
        records = self.store.snapshot()["records"]
        self.assertEqual(self.load_problem_module().find_current_statement(records), second)
        self.assertEqual(records["problem_statement"][first["id"]], first)
        self.assertEqual(self.load_problem_module().compute_statement_digest(first), self.load_problem_module().compute_statement_digest(second))
        self.assert_error("record_conflict", lambda: self.select_statement(title="Changed title"))

    def test_formal_statement_can_be_omitted_and_stays_optional(self):
        self.initialize_problem()
        payload = self.build_statement()
        payload.pop("formal_statement")
        saved = self.mutate(self.load_problem_module().record_statement, payload)["result"]
        self.assertIsNone(saved["formal_statement"])

    def test_post_gate_dispatch_is_exclusive(self):
        from research_harness.cli import GATES
        self.initialize_problem()
        records = self.store.snapshot()["records"]
        for action in GATES:
            report = gate_state(records, self.artifacts, action)
            codes = {item["code"] for item in report["obligations"]}
            self.assertIn("problem_statement_missing" if action == "post" else "gate_inapplicable", codes)
        self.assertEqual(gate_state({"workspace": {"study": {}}}, self.artifacts, "post")["obligations"][0]["code"], "gate_inapplicable")

    def test_value_accepts_full_reading_and_rejects_abstract_or_unknown_reading(self):
        self.initialize_problem()
        self.select_statement()
        evidence = self.record_full_reading()
        saved = self.mutate(self.load_problem_module().record_value, self.build_value([evidence]))["result"]
        self.assertEqual(saved["motivation"], self.build_value([evidence])["motivation"])
        self.mutate(record_reading, self.abstract_note(evidence["version_id"], note_id="read-abstract"))
        for identifier in ("read-abstract", "deferred-source", "absent"):
            bad = dict(evidence, reading_id=identifier)
            self.assert_error("problem_value_reading_missing", lambda: self.mutate(self.load_problem_module().record_value,
                self.build_value([bad], "bad-" + identifier)))

    def test_value_requires_a_reconciled_assessed_admitted_computation(self):
        self.initialize_problem()
        self.select_statement()
        evidence = {"kind": "computation", "execution_id": "run", "output_id": "result", "formal_check": None}
        self.assert_error("problem_value_computation_missing", lambda: self.mutate(self.load_problem_module().record_value, self.build_value([evidence])))

    def test_status_names_formulation_and_first_missing_condition(self):
        from research_harness.cli import status_report
        self.initialize_problem()
        report = status_report(self.store)
        self.assertEqual(report["orchestration"], "problem_creation")
        self.assertEqual(report["milestone"], "exploration")
        self.assertEqual(report["next"]["code"], "problem_slate_missing")
        self.put_selecting_slate()
        self.assertEqual(status_report(self.store)["next"]["code"], "problem_statement_missing")
        self.select_statement(candidate_id="candidate-1")
        report = status_report(self.store)
        self.assertEqual(report["milestone"], "formulation")
        self.assertEqual(report["next"]["code"], "problem_openness_missing")

    def test_post_decision_requires_the_problem_minimum(self):
        self.initialize_problem()
        self.assert_error("problem_minimum_unmet", lambda: self.mutate(self.load_problem_module().record_decision, {
            "id": "decision", "action": "post", "review_ids": [], "bar_review_ids": [], "reason": "Ready."}))
        revised = self.mutate(self.load_problem_module().record_decision, {
            "id": "revise", "action": "revise", "review_ids": [], "bar_review_ids": [], "reason": "Narrow the claim."})["result"]
        self.assertEqual(revised["action"], "revise")


class ProblemFormalBindingTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.select_statement()
        self.claim = {"statement": "The first four values satisfy the bound.", "quantifiers": "For n in [0,3]",
            "assumption_ids": [], "proof_policy": "lean-kernel", "scope": {"kind": "named", "name": "finite range"}}
        self.request = {"claim": self.claim, "required_deliverables": ["range check"]}
        self.item = {"kind": "computation", "execution_id": "execution", "output_id": "result", "formal_check": self.request}
        self.put("problem_value", "value", dict(self.build_value([self.item], "value"), recorded_revision=self.store.revision + 1))

    def build_closure_record(self):
        contract = {"original_claim": copy.deepcopy(self.claim), "proof_policy": "lean-kernel", "required_deliverables": ["range check"]}
        subject = {"contract_digest": digest(contract), "outcome": "proof", "acceptance_ids": ["acceptance"],
            "evidence_digests": ["a" * 64], "deliverables": [{"requirement": "range check", "digest": "b" * 64}]}
        closure = {"subject": subject, "review": {"decision": "approve", "subject_digest": digest(subject)},
            "audit": {"subject_digest": digest(subject), "acceptance_ids": subject["acceptance_ids"],
                      "evidence_digests": subject["evidence_digests"]}}
        return {"schema_version": 1, "objective_id": "objective-000001", "attack_root": "/native/range",
                "contract": contract, "closure": closure, "provenance": {"source": "host"}}

    def bind(self, record):
        identifier = digest(record["closure"])
        self.put("native_closure", identifier, record)
        return self.mutate(self.load_problem_module().record_formal_check, {
            "evidence": {"execution_id": "execution", "output_id": "result"},
            "native_closure_digest": identifier})["result"]

    def test_exact_native_completion_binds_and_keeps_closure(self):
        bound = self.bind(self.build_closure_record())
        self.assertEqual(bound["formal_check"], self.request)
        self.assertEqual(bound["evidence"], {"execution_id": "execution", "output_id": "result"})

    def test_original_claim_fields_policy_deliverables_and_audit_cannot_differ(self):
        mutations = [
            lambda r: r["contract"]["original_claim"].update(statement="Other claim"),
            lambda r: r["contract"]["original_claim"].update(quantifiers="For every integer"),
            lambda r: r["contract"]["original_claim"].update(assumption_ids=["extra"]),
            lambda r: r["contract"]["original_claim"].update(scope={"kind": "named", "name": "other"}),
            lambda r: r["contract"]["original_claim"].update(proof_policy="certificate"),
            lambda r: r["contract"].update(proof_policy="certificate"),
            lambda r: r["contract"].update(required_deliverables=[]),
            lambda r: r["closure"]["subject"].update(deliverables=[]),
            lambda r: r["closure"]["subject"].update(contract_digest="0" * 64),
            lambda r: r["closure"]["subject"].update(outcome="counterexample"),
            lambda r: r["closure"]["audit"].update(subject_digest="0" * 64),
            lambda r: r["closure"]["audit"].update(acceptance_ids=[]),
            lambda r: r["closure"]["audit"].update(evidence_digests=[]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                record = self.build_closure_record()
                mutate(record)
                self.assert_error("problem_formal_check_unbound", lambda: self.bind(record))

    def test_absent_closure_and_changed_value_refuse_binding(self):
        payload = {"evidence": {"execution_id": "execution", "output_id": "result"}, "native_closure_digest": "c" * 64}
        self.assert_error("problem_formal_check_unbound", lambda: self.mutate(self.load_problem_module().record_formal_check, payload))
        bound = self.bind(self.build_closure_record())
        changed = copy.deepcopy(self.item)
        changed["formal_check"]["claim"]["statement"] = "Different bound"
        self.put("problem_value", "later", dict(self.build_value([changed], "later"), recorded_revision=self.store.revision + 1))
        from research_harness.problem_evidence import assess_formal_checks
        self.assertIn("problem_formal_check_stale", {o["code"] for o in assess_formal_checks(self.store.snapshot()["records"])})

    def test_changed_support_can_be_rebound_without_rewriting_earlier_binding(self):
        original = self.bind(self.build_closure_record())
        changed = copy.deepcopy(self.item)
        changed["formal_check"]["claim"]["statement"] = "A stronger requested bound"
        self.put("problem_value", "later", dict(self.build_value([changed], "later"), recorded_revision=self.store.revision + 1))
        self.claim = changed["formal_check"]["claim"]
        replacement = self.bind(self.build_closure_record())
        from research_harness.problem_evidence import assess_formal_checks
        records = self.store.snapshot()["records"]
        self.assertEqual(assess_formal_checks(records), [])
        self.assertEqual(records["problem_formal_check"][original["id"]], original)
        self.assertNotEqual(original["id"], replacement["id"])

    def test_uncited_computation_does_not_leave_a_permanent_formal_check_obligation(self):
        original = self.bind(self.build_closure_record())
        reading = self.record_full_reading()
        self.mutate(self.load_problem_module().record_value, self.build_value([reading], "reading-only"))
        from research_harness.problem_evidence import assess_formal_checks
        records = self.store.snapshot()["records"]
        self.assertEqual(assess_formal_checks(records), [])
        self.assertEqual(records["problem_formal_check"][original["id"]], original)

    def test_each_requested_claim_on_the_same_output_needs_its_own_binding(self):
        changed = copy.deepcopy(self.item)
        changed["formal_check"]["claim"]["statement"] = "A second requested support claim"
        value = self.build_value([self.item], "two-requests")
        value["current_state"][0]["evidence"] = [changed]
        self.put("problem_value", "two-requests", dict(value, recorded_revision=self.store.revision + 1))
        self.bind(self.build_closure_record())
        from research_harness.problem_evidence import assess_formal_checks
        self.assertEqual(assess_formal_checks(self.store.snapshot()["records"])[0]["code"], "problem_formal_check_missing")
        self.claim = changed["formal_check"]["claim"]
        self.bind(self.build_closure_record())
        self.assertEqual(assess_formal_checks(self.store.snapshot()["records"]), [])

    def test_same_claim_with_distinct_requested_deliverables_can_bind_each_item(self):
        changed = copy.deepcopy(self.item)
        changed["formal_check"]["required_deliverables"].append("second artifact")
        value = self.build_value([self.item], "two-deliverable-requests")
        value["current_state"][0]["evidence"] = [changed]
        self.put("problem_value", value["id"], dict(value, recorded_revision=self.store.revision + 1))
        native = self.build_closure_record()
        native["contract"]["required_deliverables"].append("second artifact")
        subject = native["closure"]["subject"]
        subject["contract_digest"] = digest(native["contract"])
        subject["deliverables"].append({"requirement": "second artifact", "digest": "c" * 64})
        native["closure"]["audit"]["subject_digest"] = digest(subject)
        first = self.bind(native)
        second = self.bind(native)
        self.assertNotEqual(first["formal_check"], second["formal_check"])
        from research_harness.problem_evidence import assess_formal_checks
        self.assertEqual(assess_formal_checks(self.store.snapshot()["records"]), [])
