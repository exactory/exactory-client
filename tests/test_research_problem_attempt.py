"""Attempts bind the exact problem to delivered findings or full readings."""

import copy

from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.reading import record_reading
from test_research_problem_assessment import build_assessment
from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase


def build_acceptance(identifier="acceptance-1", outcome="reduction", status="accepted", **changes):
    return dict({"id": identifier, "checkpoint_id": "checkpoint-1", "obligation_id": "obligation-000002",
                 "outcome": outcome, "classification": "analytical", "standard": "reviewed",
                 "status": status, "claims_openness": False, "evidence_digests": ["a" * 64]}, **changes)


def build_native_attempt(statement_text, acceptances, state):
    claim = {"statement": statement_text, "quantifiers": "For every n", "assumption_ids": [],
             "proof_policy": "reviewed", "scope": {"kind": "named", "name": "all admissible sequences"}}
    return {"schema_version": 1, "objective_id": "objective-000001", "attack_root": "/native/attack",
            "contract": {"schema_version": 1, "original_claim": claim, "assumption_ids": [],
                         "root_obligation": "obligation-000001", "root_attack_slug": "attack",
                         "requested_outcome": "decision", "proof_policy": "reviewed", "required_deliverables": [],
                         "resource_policy": {"max_total_moves": None, "max_total_runs": None, "max_workers": 1}},
            "state": dict({"proof_status": "open", "execution_status": "active", "openness": None,
                           "closure_digest": None}, **state),
            "obligations": [{"id": "obligation-000001", "status": "open", "claim": claim}],
            "checkpoints": [{"id": "checkpoint-1", "kind": "reduction",
                             "claim": dict(claim, statement="The bound reduces to the balanced case."),
                             "verification_status": "accepted", "origin_kind": "local"}],
            "acceptances": copy.deepcopy(acceptances), "retreats": [], "failed_strategies": [], "nodes": [],
            "totals": {"moves": 24, "runs": 24, "elapsed_seconds": None, "workers": 1, "historical_usage": "known"},
            "provenance": {"source": "operator", "actor_id": "search-controller",
                           "attestation_id": "filesystem-audit:deliver-attempt"}}


def put_native_attempt(case, native):
    identifier = digest({key: value for key, value in native.items() if key != "provenance"})
    case.put("native_attempt", identifier, native)
    return identifier


def build_attempt(identifier="attempt-1", native_attempt_digest=None, reading=None, **changes):
    return dict({"id": identifier, "statement_id": "statement-1", "kind": "solver" if reading is None else "literature",
                 "solver": {"native_attempt_digest": native_attempt_digest} if reading is None else None,
                 "literature": None if reading is None else {
                     "readings": [{key: reading[key] for key in ("version_id", "reading_id")}],
                     "description": "Critically assessed the exact bound and the routes tried in the full source."},
                 "outcomes": {"resolved": False, "footholds": [], "unresolved": [
                     {"description": "The general bound remains unresolved."}], "obstructions": []},
                 "proportion": {"extension_reason": None}}, **changes)


class ProblemAttemptTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()

    def attempt_module(self):
        from research_harness import problem_attempt
        return problem_attempt

    def record_attempt(self, payload):
        return self.mutate(self.attempt_module().record_attempt, payload)["result"]

    def solver_payload(self, native=None, identifier="attempt-1"):
        native = native or build_native_attempt(self.statement["statement"], [build_acceptance()], {})
        return build_attempt(identifier, put_native_attempt(self, native))

    def test_records_a_solver_attempt_bound_to_a_native_attempt(self):
        payload = self.solver_payload()
        payload["outcomes"]["footholds"] = [{"claim": "Reduction to balanced sequences.",
            "evidence": {"kind": "acceptance", "acceptance_id": "acceptance-1"}}]
        revision = self.store.revision
        saved = self.record_attempt(payload)
        self.assertEqual(saved, dict(payload, recorded_revision=revision + 1))
        records = self.store.snapshot()["records"]
        self.assertEqual(records["problem_attempt"]["attempt-1"], saved)
        self.assertEqual(self.attempt_module().find_attempt(records), saved)
        self.assertEqual(self.attempt_module().assess_attempt(records, self.artifacts), [])

    def test_solver_attempt_refuses_a_digest_absent_from_this_store(self):
        payload = build_attempt(native_attempt_digest="b" * 64)
        with self.assertRaises(ResearchError) as caught:
            self.record_attempt(payload)
        self.assertEqual(caught.exception.code, "problem_attempt_unbound")
        self.assertEqual(caught.exception.details, {"native_attempt_digest": "b" * 64, "expected_store": str(self.store.root)})

    def test_solver_attempt_refuses_a_statement_mismatch(self):
        native = build_native_attempt(self.statement["statement"] + " ", [], {})
        self.assert_error("problem_attempt_statement_mismatch", lambda: self.record_attempt(self.solver_payload(native)))

    def test_resolved_flag_must_match_the_native_state(self):
        for proof_status, resolved in (("open", False), ("proved", True), ("disproved", True), ("invalidated", False)):
            with self.subTest(proof_status=proof_status):
                payload = self.solver_payload(build_native_attempt(self.statement["statement"], [], {"proof_status": proof_status}), proof_status)
                payload["outcomes"]["resolved"] = not resolved
                self.assert_error("problem_attempt_outcome_mismatch", lambda: self.record_attempt(payload))
                payload["outcomes"]["resolved"] = resolved
                self.assertEqual(self.record_attempt(payload)["outcomes"]["resolved"], resolved)

    def test_foothold_and_obstruction_evidence_must_name_accepted_acceptances_of_the_right_outcome(self):
        for section, outcomes in (("footholds", ("proof", "reduction")), ("obstructions", ("obstruction", "counterexample"))):
            for status in ("accepted", "invalidated", "hypothesis"):
                for outcome in ("proof", "reduction", "obstruction", "counterexample"):
                    for obligation_id in ("obligation-000001", "obligation-000002"):
                        with self.subTest(section=section, status=status, outcome=outcome, obligation_id=obligation_id):
                            native = build_native_attempt(self.statement["statement"], [build_acceptance(
                                outcome=outcome, status=status, obligation_id=obligation_id)], {})
                            payload = self.solver_payload(native, "-".join((section, status, outcome, obligation_id)))
                            row = {"claim": "A scoped mathematical conclusion.",
                                   "evidence": {"kind": "acceptance", "acceptance_id": "acceptance-1"}}
                            if section == "obstructions":
                                row["scope"] = "Only the unweighted counting argument."
                            payload["outcomes"][section] = [row]
                            valid = status == "accepted" and outcome in outcomes and (
                                section == "footholds" or obligation_id != "obligation-000001")
                            if valid:
                                self.record_attempt(payload)
                            else:
                                with self.assertRaises(ResearchError) as caught:
                                    self.record_attempt(payload)
                                self.assertEqual(caught.exception.code, "problem_attempt_evidence_missing")
                                self.assertEqual(caught.exception.details["acceptance_id"], "acceptance-1")
        payload = self.solver_payload(identifier="absent-acceptance")
        payload["outcomes"]["footholds"] = [{"claim": "Absent support.", "evidence": {"kind": "acceptance", "acceptance_id": "absent"}}]
        self.assert_error("problem_attempt_evidence_missing", lambda: self.record_attempt(payload))

    def test_extension_reason_required_beyond_the_normal_limits(self):
        for dimension in ("moves", "runs"):
            native = build_native_attempt(self.statement["statement"], [], {})
            native["totals"][dimension] = 25
            payload = self.solver_payload(native, dimension)
            for reason in (None, "", "  "):
                payload["proportion"]["extension_reason"] = reason
                with self.assertRaises(ResearchError) as caught:
                    self.record_attempt(payload)
                self.assertEqual(caught.exception.code, "problem_attempt_extension_unexplained")
                self.assertEqual(caught.exception.details["totals"], native["totals"])
            payload["proportion"]["extension_reason"] = "An additional run tests the remaining boundary case."
            self.record_attempt(payload)
        self.record_attempt(self.solver_payload(identifier="at-normal-limits"))

    def test_records_a_literature_attempt_on_full_readings(self):
        reading = self.record_full_reading()
        payload = build_attempt(reading=reading)
        payload["outcomes"]["footholds"] = [{"claim": "The finite cases are established.", "evidence": reading}]
        payload["outcomes"]["obstructions"] = [{"claim": "Counting alone loses the sharp constant.",
            "scope": "The source's unweighted counting argument.", "evidence": reading}]
        saved = self.record_attempt(payload)
        self.assertEqual(saved["literature"], payload["literature"])
        self.assertEqual(self.attempt_module().assess_attempt(self.store.snapshot()["records"], self.artifacts), [])
        for section in ("footholds", "obstructions"):
            bad = copy.deepcopy(payload)
            bad["id"] = "unlisted-" + section
            bad["outcomes"][section][0]["evidence"]["reading_id"] = "unlisted"
            self.assert_error("problem_attempt_evidence_missing", lambda: self.record_attempt(bad))

    def test_literature_attempt_refuses_an_abstract_reading(self):
        reading = self.record_full_reading()
        self.mutate(record_reading, self.abstract_note(reading["version_id"], "read-abstract"))
        for identifier in ("read-abstract", "absent"):
            payload = build_attempt(reading=dict(reading, reading_id=identifier))
            self.assert_error("problem_value_reading_missing", lambda: self.record_attempt(payload))

    def test_assess_attempt_reports_missing_stale_and_resolved(self):
        module = self.attempt_module()
        self.assertEqual(module.assess_attempt(self.store.snapshot()["records"], self.artifacts)[0]["code"], "problem_attempt_missing")
        self.record_attempt(self.solver_payload())
        self.select_statement(identifier="statement-2", resolution_criteria="A changed resolution criterion.")
        pending = module.assess_attempt(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual([(row["code"], row["attempt_id"]) for row in pending], [("problem_attempt_stale", "attempt-1")])
        self.select_statement()
        payload = self.solver_payload(build_native_attempt(self.statement["statement"], [], {"proof_status": "proved"}), "resolved")
        payload["outcomes"]["resolved"] = True
        self.record_attempt(payload)
        pending = module.assess_attempt(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual([(row["code"], row["attempt_id"]) for row in pending], [("problem_attempt_resolved", "resolved")])

    def test_assessment_attempt_pass_needs_a_current_attempt(self):
        module = self.attempt_module()
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.statement))
        def unbound():
            return [row for row in module.assess_attempt(self.store.snapshot()["records"], self.artifacts)
                    if row["code"] == "problem_rubric_attempt_unbound"]
        self.assertEqual(unbound()[0]["assessment_id"], "assessment-1")
        self.record_attempt(self.solver_payload())
        self.assertEqual(unbound(), [])
        revised = self.select_statement(identifier="statement-2")
        self.assertEqual(unbound(), [])  # An old assessment has its own stale obligation.
        self.mutate(self.load_problem_module().record_assessment, build_assessment(revised, "assessment-2"))
        self.assertEqual(unbound()[0]["assessment_id"], "assessment-2")
        failed = build_assessment(revised, "assessment-3")
        failed["grades"]["attempt"] = "fail"
        self.mutate(self.load_problem_module().record_assessment, failed)
        self.assertEqual(unbound(), [])

    def test_attempt_enters_the_evidence_digest_and_reviewer_evidence(self):
        from research_harness.problem_evidence import describe_binding, describe_evidence
        claims = self.load_problem_module()
        records = self.store.snapshot()["records"]
        before = claims.compute_problem_evidence_digest(records, self.artifacts)
        self.assertIsNone(describe_binding(records, self.artifacts)["attempt"])
        self.assertIsNone(describe_evidence(records, self.artifacts)["attempt"])
        native = build_native_attempt(self.statement["statement"], [], {})
        payload = self.solver_payload(native)
        saved = self.record_attempt(payload)
        records = self.store.snapshot()["records"]
        self.assertNotEqual(before, claims.compute_problem_evidence_digest(records, self.artifacts))
        self.assertEqual(describe_binding(records, self.artifacts)["attempt"], {
            "id": "attempt-1", "digest": digest(saved), "native_attempt_digest": payload["solver"]["native_attempt_digest"]})
        self.assertEqual(describe_evidence(records, self.artifacts)["attempt"], {
            "kind": "solver", "outcomes": payload["outcomes"], "proportion": payload["proportion"],
            "state": native["state"], "totals": native["totals"]})
        reading = self.record_full_reading()
        literary = self.record_attempt(build_attempt("literature-2", reading=reading))
        records = self.store.snapshot()["records"]
        self.assertEqual(describe_binding(records, self.artifacts)["attempt"], {
            "id": "literature-2", "digest": digest(literary), "native_attempt_digest": None})
        self.assertEqual(describe_evidence(records, self.artifacts)["attempt"], {
            key: literary[key] for key in ("kind", "outcomes", "proportion")})

    def test_milestone_attempt_and_next_order(self):
        from research_harness.cli import status_report
        self.put_selecting_slate()
        self.select_statement(identifier="statement-candidate", candidate_id="candidate-1")
        report = status_report(self.store)
        self.assertEqual((report["milestone"], report["next"]["code"]), ("formulation", "problem_attempt_missing"))
        payload = self.solver_payload()
        payload["statement_id"] = "statement-candidate"
        self.record_attempt(payload)
        report = status_report(self.store)
        self.assertEqual((report["milestone"], report["next"]["code"]), ("attempt", "problem_openness_missing"))

    def test_attempt_validates_its_closed_shapes_and_current_statement(self):
        payload = self.solver_payload()
        changes = [lambda row: row.update(kind="other"), lambda row: row.update(literature={}),
                   lambda row: row.update(solver=None), lambda row: row["outcomes"].update(resolved=1),
                   lambda row: row["outcomes"].update(unresolved=[{"description": ""}]),
                   lambda row: row["outcomes"].update(footholds={}),
                   lambda row: row["outcomes"].update(obstructions=[{"claim": "A limit", "evidence": {}}])]
        for change in changes:
            bad = copy.deepcopy(payload)
            change(bad)
            self.assert_error("invalid_input", lambda: self.record_attempt(bad))
        literary = build_attempt(reading=self.record_full_reading())
        for field, value in (("readings", []), ("description", " ")):
            bad = copy.deepcopy(literary)
            bad["literature"][field] = value
            self.assert_error("invalid_input", lambda: self.record_attempt(bad))
        payload["statement_id"] = "absent"
        self.assert_error("problem_statement_missing", lambda: self.record_attempt(payload))

    def test_attempt_command_replay_and_immutable_history(self):
        from research_harness import cli
        payload = self.solver_payload()
        parser = cli.build_parser()
        args = parser.parse_args(["problem-attempt", "--file", "attempt.json", "--expected-revision",
                                  str(self.store.revision), "--request-id", "record-attempt"])
        revision = self.store.revision
        function = cli.OPERATIONS[args.command]
        first = function(self.store, payload, expected_revision=args.expected_revision, request_id=args.request_id)
        self.select_statement(identifier="statement-2")
        replay = function(self.store, payload, expected_revision=revision, request_id=args.request_id)
        self.assertEqual(first, replay)
        self.assertEqual(self.store.revision, revision + 2)
        self.select_statement()
        payload["outcomes"]["unresolved"][0]["description"] = "Changed inventory."
        self.assert_error("record_conflict", lambda: self.record_attempt(payload))
        self.assertEqual(set(cli.run(parser.parse_args(["example", "problem-attempt"]))), set(payload))


class ProblemAttemptDeliveryTests(ProblemDeliveryCase):
    def test_resolved_attempt_blocks_the_post(self):
        from research_harness.problem_attempt import record_attempt
        from research_harness.gates import gate_state
        self.prepare_ready(solver=True)
        self.record_post_decision()
        native = build_native_attempt(self.selected["statement"], [], {"proof_status": "disproved"})
        payload = build_attempt("resolved", put_native_attempt(self, native))
        payload["outcomes"]["resolved"] = True
        self.mutate(record_attempt, payload)
        report = gate_state(self.store.snapshot()["records"], self.artifacts, "post")
        self.assertFalse(report["ready"])
        self.assertIn("problem_attempt_resolved", {row["code"] for row in report["obligations"]})
        self.assert_error("problem_minimum_unmet", lambda: self.record_post_decision("resolved-decision"))
