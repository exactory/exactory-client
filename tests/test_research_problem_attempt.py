"""Attempts bind the exact problem to delivered findings or full readings."""

import copy
import json
from unittest.mock import patch

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
            "totals": {"moves": 24, "runs": 24, "elapsed_seconds": None, "workers": 1, "timeout_seconds": 300, "historical_usage": "known"},
            "provenance": {"source": "operator", "actor_id": "search-controller",
                           "attestation_id": "filesystem-audit:deliver-attempt"}}


def put_native_attempt(case, native):
    identifier = digest({key: value for key, value in native.items() if key not in ("provenance", "recorded_revision")})
    if identifier not in case.store.snapshot()["records"].get("native_attempt", {}):
        case.put("native_attempt", identifier, dict(native, recorded_revision=case.store.revision + 1))
    key = {name: native[name] for name in ("attack_root", "objective_id")}
    pointer_id = digest(key)
    current = case.store.snapshot()["records"].get("native_attempt_current", {}).get(pointer_id)
    if current is None or current["native_attempt_digest"] != identifier:
        case.put("native_attempt_current", pointer_id, dict(key, native_attempt_digest=identifier,
                                                          recorded_revision=case.store.revision + 1))
    return identifier


def build_attempt(identifier="attempt-1", native_attempt_digest=None, reading=None, **changes):
    return dict({"id": identifier, "statement_id": "statement-1", "kind": "solver" if reading is None else "literature",
                 "solver": {"native_attempt_digest": native_attempt_digest,
                            "applicability": "The quantifiers cover every n, with no added assumptions and named scope matching the statement and resolution criteria."} if reading is None else None,
                 "literature": None if reading is None else {
                     "readings": [{key: reading[key] for key in ("version_id", "reading_id")}],
                     "description": "Critically assessed the exact bound and the routes tried in the full source."},
                 "outcomes": {"resolved": False, "footholds": [], "unresolved": [
                     {"description": "The general bound remains unresolved."}], "obstructions": []},
                 "proportion": {"extension_reason": None}}, **changes)


def record_solver_attempt(case, statement, identifier="attempt-1"):
    from research_harness.problem_attempt import record_attempt
    native = build_native_attempt(statement["statement"], [], {})
    payload = build_attempt(identifier, put_native_attempt(case, native), statement_id=statement["id"])
    return case.mutate(record_attempt, payload)["result"]


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
        payload = self.solver_payload(native)
        with self.assertRaises(ResearchError) as caught:
            self.record_attempt(payload)
        self.assertEqual(caught.exception.code, "problem_attempt_statement_mismatch")
        self.assertEqual(caught.exception.details, {"attempt_id": "attempt-1", "statement_id": "statement-1",
            "native_attempt_digest": payload["solver"]["native_attempt_digest"]})

    def test_resolved_flag_must_match_the_native_state(self):
        for proof_status, resolved in (("open", False), ("proved", True), ("disproved", True), ("invalidated", False)):
            with self.subTest(proof_status=proof_status):
                payload = self.solver_payload(build_native_attempt(self.statement["statement"], [], {"proof_status": proof_status}), proof_status)
                payload["outcomes"]["resolved"] = not resolved
                with self.assertRaises(ResearchError) as caught:
                    self.record_attempt(payload)
                self.assertEqual(caught.exception.code, "problem_attempt_outcome_mismatch")
                self.assertEqual(caught.exception.details, {"attempt_id": proof_status,
                    "native_attempt_digest": payload["solver"]["native_attempt_digest"]})
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
                            identifier = "-".join((section, status, outcome, obligation_id))
                            native["attack_root"] = "/native/" + identifier
                            payload = self.solver_payload(native, identifier)
                            payload["outcomes"]["resolved"] = (status == "accepted" and obligation_id == "obligation-000001"
                                                                 and outcome in ("proof", "counterexample"))
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
                                self.assertEqual(caught.exception.details, {"acceptance_id": "acceptance-1"})
        payload = self.solver_payload(identifier="absent-acceptance")
        payload["outcomes"]["footholds"] = [{"claim": "Absent support.", "evidence": {"kind": "acceptance", "acceptance_id": "absent"}}]
        with self.assertRaises(ResearchError) as caught:
            self.record_attempt(payload)
        self.assertEqual(caught.exception.code, "problem_attempt_evidence_missing")
        self.assertEqual(caught.exception.details, {"acceptance_id": "absent"})

    def test_extension_reason_required_beyond_the_normal_limits(self):
        for dimension, limit in (("moves", 24), ("runs", 24), ("workers", 1), ("timeout_seconds", 300)):
            native = build_native_attempt(self.statement["statement"], [], {})
            native["totals"].update(moves=1, runs=1)
            native["totals"][dimension] = {"workers": 8, "timeout_seconds": 600}.get(dimension, limit + 1)
            payload = self.solver_payload(native, dimension)
            for reason in (None, "", "  "):
                payload["proportion"]["extension_reason"] = reason
                with self.assertRaises(ResearchError) as caught:
                    self.record_attempt(payload)
                self.assertEqual(caught.exception.code, "problem_attempt_extension_unexplained")
                self.assertEqual(caught.exception.details, {"totals": native["totals"], "exceeded": [dimension]})
            payload["proportion"]["extension_reason"] = "An additional run tests the remaining boundary case."
            self.record_attempt(payload)
        self.record_attempt(self.solver_payload(identifier="at-normal-limits"))

    def test_solver_scope_must_be_named_and_applicability_nonempty(self):
        scopes = [{"kind": "case_ids", "case_ids": ["n=1"]},
                  {"kind": "integer_interval", "lower": 1, "upper": 10,
                   "lower_inclusive": True, "upper_inclusive": True}]
        for scope in scopes:
            with self.subTest(scope=scope):
                native = build_native_attempt(self.statement["statement"], [], {})
                native["contract"]["original_claim"]["scope"] = scope
                payload = self.solver_payload(native)
                with self.assertRaises(ResearchError) as caught:
                    self.record_attempt(payload)
                self.assertEqual(caught.exception.code, "problem_attempt_scope_restricted")
                self.assertEqual(caught.exception.details, {
                    "native_attempt_digest": payload["solver"]["native_attempt_digest"], "scope": scope})
        payload = self.solver_payload()
        for variant in (None, "", "  ", "missing"):
            with self.subTest(applicability=variant):
                bad = copy.deepcopy(payload)
                if variant == "missing":
                    del bad["solver"]["applicability"]
                else:
                    bad["solver"]["applicability"] = variant
                with self.assertRaises(ResearchError) as caught:
                    self.record_attempt(bad)
                self.assertEqual(caught.exception.code, "invalid_input")
                self.assertIsNone(caught.exception.details)

    def test_native_current_pointer_defines_snapshot_succession(self):
        first = build_native_attempt(self.statement["statement"], [], {})
        first_digest = put_native_attempt(self, first)
        paused = copy.deepcopy(first)
        paused["state"]["execution_status"] = "paused"
        paused_digest = put_native_attempt(self, paused)
        before = copy.deepcopy(self.store.snapshot()["records"]["native_attempt"])
        self.assertEqual(put_native_attempt(self, first), first_digest)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["native_attempt"], before)
        self.assertEqual(len(records["native_attempt"]), 2)
        key = {name: first[name] for name in ("attack_root", "objective_id")}
        self.assertEqual(records["native_attempt_current"][digest(key)], dict(key,
            native_attempt_digest=first_digest, recorded_revision=self.store.revision))
        self.record_attempt(build_attempt(native_attempt_digest=first_digest))
        with self.assertRaises(ResearchError) as caught:
            self.record_attempt(build_attempt("paused", paused_digest))
        self.assertEqual(caught.exception.code, "problem_attempt_superseded")
        self.assertEqual(caught.exception.details, {
            "native_attempt_digest": paused_digest, "latest_native_attempt_digest": first_digest})

    def test_native_record_without_a_current_pointer_is_unbound(self):
        payload = self.solver_payload()
        records = self.store.snapshot()["records"]
        records.pop("native_attempt_current")
        with self.assertRaises(ResearchError) as caught:
            self.attempt_module().validate_attempt(records, self.artifacts, payload)
        self.assertEqual(caught.exception.code, "problem_attempt_unbound")
        self.assertEqual(caught.exception.details, {
            "native_attempt_digest": payload["solver"]["native_attempt_digest"], "expected_store": str(self.store.root)})

    def test_two_attack_roots_have_independent_successions_and_resolutions(self):
        reading = self.record_full_reading()
        for index, (primary, other) in enumerate((("/native/a", "/native/b"), ("/native/b", "/native/a"))):
            with self.subTest(primary=primary):
                self.statement = self.select_statement(identifier="statement-root-" + str(index))
                native = build_native_attempt(self.statement["statement"], [], {})
                native["attack_root"] = primary
                bound = put_native_attempt(self, native)
                foreign = dict(copy.deepcopy(native), attack_root=other)
                foreign["state"]["proof_status"] = "proved"
                put_native_attempt(self, foreign)
                identifier = "independent-" + str(index)
                payload = build_attempt(identifier, bound, statement_id=self.statement["id"])
                self.record_attempt(payload)
                self.assertEqual(self.attempt_module().assess_attempt(self.store.snapshot()["records"], self.artifacts), [])
                native["state"]["proof_status"] = "proved"
                payload["id"] = "resolved-" + identifier
                payload["solver"]["native_attempt_digest"] = put_native_attempt(self, native)
                payload["outcomes"]["resolved"] = True
                self.record_attempt(payload)
                foreign["state"]["proof_status"] = "open"
                put_native_attempt(self, foreign)
                self.record_attempt(build_attempt("literature-" + identifier, reading=reading,
                                                  statement_id=self.statement["id"]))
                self.assertEqual(self.attempt_module().assess_attempt(self.store.snapshot()["records"], self.artifacts), [{
                    "code": "problem_attempt_resolved", "explanation": "The solver resolved this statement: deliver the result through math-solver's own delivery; the candidate leaves the open-problem path.",
                    "attempt_id": payload["id"]}])

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
            with self.assertRaises(ResearchError) as caught:
                self.record_attempt(bad)
            self.assertEqual(caught.exception.code, "problem_attempt_evidence_missing")
            self.assertEqual(caught.exception.details, {"version_id": reading["version_id"], "reading_id": "unlisted"})

    def test_literature_attempt_refuses_an_abstract_reading(self):
        reading = self.record_full_reading()
        self.mutate(record_reading, self.abstract_note(reading["version_id"], "read-abstract"))
        for identifier in ("read-abstract", "absent"):
            payload = build_attempt(reading=dict(reading, reading_id=identifier))
            with self.assertRaises(ResearchError) as caught:
                self.record_attempt(payload)
            self.assertEqual(caught.exception.code, "problem_value_reading_missing")
            self.assertEqual(caught.exception.details, {"kind": "reading", "version_id": reading["version_id"], "reading_id": identifier})

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
        native = build_native_attempt(self.statement["statement"], [
            build_acceptance("obstruction", outcome="obstruction", checkpoint_id="checkpoint-obstruction"),
            build_acceptance("reduction"), build_acceptance("uncited"),
            build_acceptance("proof", outcome="proof", checkpoint_id="checkpoint-proof")], {})
        proof_claim = dict(native["contract"]["original_claim"], statement="The first case satisfies the bound.")
        obstruction_claim = dict(native["contract"]["original_claim"], statement="Unweighted counting loses the sharp constant.")
        native["checkpoints"].extend([
            dict(native["checkpoints"][0], id="checkpoint-proof", kind="proof", claim=proof_claim),
            dict(native["checkpoints"][0], id="checkpoint-obstruction", kind="obstruction", claim=obstruction_claim)])
        payload = self.solver_payload(native)
        payload["outcomes"]["footholds"] = [
            {"claim": "The first case holds.", "evidence": {"kind": "acceptance", "acceptance_id": "proof"}},
            {"claim": "Reduction to balanced sequences.", "evidence": {"kind": "acceptance", "acceptance_id": "reduction"}},
            {"claim": "Reuse the first case.", "evidence": {"kind": "acceptance", "acceptance_id": "proof"}}]
        payload["outcomes"]["obstructions"] = [{"claim": "Counting loses the sharp constant.",
            "scope": "The unweighted counting argument.", "evidence": {"kind": "acceptance", "acceptance_id": "obstruction"}}]
        saved = self.record_attempt(payload)
        records = self.store.snapshot()["records"]
        self.assertNotEqual(before, claims.compute_problem_evidence_digest(records, self.artifacts))
        self.assertEqual(describe_binding(records, self.artifacts)["attempt"], {
            "id": "attempt-1", "digest": digest(saved), "native_attempt_digest": payload["solver"]["native_attempt_digest"]})
        native_id = payload["solver"]["native_attempt_digest"]
        self.assertEqual(describe_evidence(records, self.artifacts)["attempt"], {
            "id": "attempt-1", "digest": digest(saved),
            "cite": {"kind": "record", "record_kind": "problem_attempt", "id": "attempt-1"},
            "record_references": [{"kind": "record",
                "record_kind": "native_attempt", "id": native_id, "digest": digest(records["native_attempt"][native_id])}],
            "kind": "solver", "outcomes": payload["outcomes"], "proportion": payload["proportion"],
            "state": native["state"], "totals": native["totals"], "literature": None,
            "applicability": payload["solver"]["applicability"],
            **{key: native[key] for key in ("contract", "obligations", "failed_strategies", "retreats", "nodes")},
            "acceptances": [
                {"acceptance_id": "proof", "claim": proof_claim, "outcome": "proof", "standard": "reviewed"},
                {"acceptance_id": "reduction", "claim": native["checkpoints"][0]["claim"], "outcome": "reduction", "standard": "reviewed"},
                {"acceptance_id": "obstruction", "claim": obstruction_claim, "outcome": "obstruction", "standard": "reviewed"}]})
        reading = self.record_full_reading()
        literary = self.record_attempt(build_attempt("literature-2", reading=reading))
        records = self.store.snapshot()["records"]
        self.assertEqual(describe_binding(records, self.artifacts)["attempt"], {
            "id": "literature-2", "digest": digest(literary), "native_attempt_digest": None})
        self.assertEqual(describe_evidence(records, self.artifacts)["attempt"], {
            **{key: literary[key] for key in ("id", "kind", "outcomes", "proportion", "literature")},
            "digest": digest(literary), "acceptances": [],
            "cite": {"kind": "record", "record_kind": "problem_attempt", "id": "literature-2"}})

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
    def test_accepted_root_support_before_closure_counts_as_resolved(self):
        from research_harness.problem_attempt import record_attempt
        from research_harness.gates import gate_state
        self.prepare_ready(solver=True)
        self.record_post_decision()
        for variant in ("root-status", "root-proof", "root-counterexample"):
            with self.subTest(variant=variant):
                native = build_native_attempt(self.selected["statement"], [], {})
                if variant == "root-status":
                    native["obligations"][0]["status"] = "accepted"
                else:
                    native["acceptances"] = [build_acceptance(outcome=variant[5:], obligation_id="obligation-000001")]
                payload = build_attempt(variant, put_native_attempt(self, native))
                if variant == "root-proof":
                    payload["outcomes"]["footholds"] = [{"claim": "The root claim is proved.",
                        "evidence": {"kind": "acceptance", "acceptance_id": "acceptance-1"}}]
                self.assertEqual(native["state"]["proof_status"], "open")
                with self.assertRaises(ResearchError) as caught:
                    self.mutate(record_attempt, payload)
                self.assertEqual(caught.exception.code, "problem_attempt_outcome_mismatch")
                self.assertEqual(caught.exception.details, {"attempt_id": variant,
                    "native_attempt_digest": payload["solver"]["native_attempt_digest"]})
                payload["outcomes"]["resolved"] = True
                saved = self.mutate(record_attempt, payload)["result"]
                self.assertTrue(saved["outcomes"]["resolved"])
                records = self.store.snapshot()["records"]
                records["problem_decision"]["decision"]["problem_evidence_digest"] = self.load_problem_module().compute_problem_evidence_digest(records, self.artifacts)
                report = gate_state(records, self.artifacts, "post")
                self.assertEqual({row["code"] for row in report["obligations"]}, {"problem_attempt_resolved"})
                self.assertEqual(report["obligations"][0]["attempt_id"], variant)

    def test_attempt_readings_are_delivered_covered_and_cited(self):
        from research_harness import problem_attempt, problem_evidence, research_decisions, review_protocol
        from research_harness.literature import import_bundle
        from research_harness.problem_delivery import export_problem
        self.prepare_ready(solver=True)
        version = self.metadata(2)
        bundle = self.bundle(version, bundle_id="attempt-only-body")
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "read-attempt-only"))
        reading = {"kind": "reading", "version_id": version, "reading_id": "read-attempt-only"}
        self.mutate(problem_attempt.record_attempt, build_attempt("literature-only", reading=reading))
        records = self.store.snapshot()["records"]
        saved_reading = records["reading"]["read-attempt-only"]
        self.assertNotIn(reading, problem_evidence.collect_value_items(self.load_problem_module().find_value(records)))
        self.assertNotIn(digest(saved_reading), records["problem_openness"]["openness-assignment"]["minimum"]["reading_digests"])
        self.assertIn({"id": "read-attempt-only", "digest": digest(saved_reading)},
                      problem_evidence.describe_binding(records, self.artifacts)["readings"])
        for reviewer in ("first", "second"):
            identifier = "attempt-reading-" + reviewer
            self.observe("result", reviewer, identifier=identifier)
            assignment = self.store.snapshot()["records"]["review_assignment"][identifier]
            packet = json.loads(self.artifacts.read(assignment["packet"]))
            self.assertIn("read-attempt-only", [row["id"] for row in packet["problem_evidence"]["readings"]])
            self.assertIn("read-attempt-only", [row["reading_id"] for row in packet["problem_evidence"]["passages"]])
            self.assertIn("2601.00002v1", [row["locator"] for row in packet["problem_evidence"]["citations"]])
        assessed = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue(assessed["ready"], assessed["obligations"])
        self.mutate(self.load_problem_module().record_decision, {"id": "attempt-citations", "action": "post",
            "review_ids": assessed["review_ids"], "bar_review_ids": assessed["bar_review_ids"],
            "reason": "The result reviewers received the assessed literature attempt."})
        export_problem(self.store, self.root / "attempt-export")
        citations = json.loads((self.root / "attempt-export" / "citations.json").read_text())
        self.assertIn("2601.00002v1", [row["locator"] for row in citations])
        stale = copy.deepcopy(self.store.snapshot()["records"])
        stale["problem_attempt"]["literature-only"]["statement_id"] = "old-statement"
        self.assertNotIn("read-attempt-only", [row["id"] for row in problem_evidence.collect_readings(stale)])
        payload = build_attempt("literature-omitted", reading=reading)
        payload["literature"]["description"] += " The critical assessment also distinguishes the finite cases."
        self.mutate(problem_attempt.record_attempt, payload)
        original = review_protocol.add_problem_evidence
        def omit_reading(records, artifacts, packet):
            original(records, artifacts, packet)
            evidence = packet["problem_evidence"]
            evidence["readings"] = [row for row in evidence["readings"] if row["id"] != "read-attempt-only"]
            evidence["passages"] = [row for row in evidence["passages"] if row["reading_id"] != "read-attempt-only"]
        with patch.object(review_protocol, "add_problem_evidence", side_effect=omit_reading):
            for reviewer in ("first", "second"):
                self.observe("result", reviewer, identifier="without-reading-" + reviewer)
        assessed = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        pending = [row for row in assessed["obligations"] if row["code"] == "problem_evidence_unassessed"]
        self.assertEqual({row["review_id"] for row in pending}, {"review-without-reading-first", "review-without-reading-second"})
        self.assertTrue(all(row["missing"] == ["readings"] and row["reading_ids"] == ["read-attempt-only"] for row in pending), pending)

    def test_native_snapshot_succession_preserves_resolution_until_invalidation(self):
        from research_harness.gates import gate_state
        from research_harness import problem_attempt
        self.prepare_ready(solver=True)
        records = self.store.snapshot()["records"]
        old_digest = records["problem_attempt"]["attempt-1"]["solver"]["native_attempt_digest"]
        native = build_native_attempt(self.selected["statement"], [], {"proof_status": "proved"})
        latest_digest = put_native_attempt(self, native)
        resolved = {"code": "problem_attempt_resolved", "explanation": "The solver resolved this statement: deliver the result through math-solver's own delivery; the candidate leaves the open-problem path.",
                    "attempt_id": "attempt-1"}
        def attempt_obligations():
            return [row for row in gate_state(self.store.snapshot()["records"], self.artifacts, "post")["obligations"]
                    if row["code"].startswith("problem_attempt_")]
        self.assertEqual(attempt_obligations(), [{
            "code": "problem_attempt_stale",
            "explanation": "Record a new attempt bound to the latest delivered snapshot of this objective.",
            "attempt_id": "attempt-1", "statement_id": "statement-1",
            "native_attempt_digest": old_digest, "latest_native_attempt_digest": latest_digest}, resolved])
        with self.assertRaises(ResearchError) as caught:
            self.mutate(problem_attempt.record_attempt, build_attempt("old-again", old_digest))
        self.assertEqual(caught.exception.code, "problem_attempt_superseded")
        self.assertEqual(caught.exception.details, {
            "native_attempt_digest": old_digest, "latest_native_attempt_digest": latest_digest})
        payload = build_attempt("resolved-latest", latest_digest)
        with self.assertRaises(ResearchError) as caught:
            self.mutate(problem_attempt.record_attempt, payload)
        self.assertEqual(caught.exception.code, "problem_attempt_outcome_mismatch")
        self.assertEqual(caught.exception.details, {"attempt_id": "resolved-latest", "native_attempt_digest": latest_digest})
        payload["outcomes"]["resolved"] = True
        self.mutate(problem_attempt.record_attempt, payload)
        resolved["attempt_id"] = "resolved-latest"
        self.assertEqual(attempt_obligations(), [resolved])
        reading = {"version_id": self.store.snapshot()["records"]["reading"]["read-full"]["version_id"],
                   "reading_id": "read-full"}
        self.mutate(problem_attempt.record_attempt, build_attempt("literature-later", reading=reading))
        self.assertEqual(attempt_obligations(), [resolved])
        invalidated = copy.deepcopy(native)
        invalidated["state"]["proof_status"] = "invalidated"
        invalidated["obligations"][0]["status"] = "accepted"
        invalidated["acceptances"] = [build_acceptance(outcome="proof", obligation_id="obligation-000001")]
        put_native_attempt(self, invalidated)
        self.assertEqual(attempt_obligations(), [])
        literary = build_attempt("literature-resolved", reading=reading)
        literary["outcomes"]["resolved"] = True
        self.mutate(problem_attempt.record_attempt, literary)
        self.mutate(problem_attempt.record_attempt, build_attempt("literature-unresolved", reading=reading))
        self.assertEqual(attempt_obligations(), [dict(
            resolved, attempt_id="literature-resolved",
            explanation="The literature resolved this statement: redirect to the established result and its source; "
                        "the candidate leaves the open-problem path.")])
        self.select_statement(identifier="statement-2")
        self.assertEqual(attempt_obligations(), [{"code": "problem_attempt_stale",
            "explanation": "Reassess the attempt for the current statement and criteria.",
            "attempt_id": "literature-unresolved", "statement_id": "statement-2"}])
        self.mutate(problem_attempt.record_attempt,
                    build_attempt("second-statement", reading=reading, statement_id="statement-2"))
        self.assertEqual(attempt_obligations(), [])

    def test_resolved_attempt_blocks_the_post(self):
        from research_harness.problem_attempt import record_attempt
        from research_harness.gates import gate_state
        self.prepare_ready(solver=True)
        self.record_post_decision()
        native = build_native_attempt(self.selected["statement"], [], {"proof_status": "disproved"})
        payload = build_attempt("resolved", put_native_attempt(self, native))
        payload["outcomes"]["resolved"] = True
        self.mutate(record_attempt, payload)
        records = self.store.snapshot()["records"]
        records["problem_decision"]["decision"]["problem_evidence_digest"] = self.load_problem_module().compute_problem_evidence_digest(records, self.artifacts)
        report = gate_state(records, self.artifacts, "post")
        self.assertFalse(report["ready"])
        self.assertEqual({row["code"] for row in report["obligations"]}, {"problem_attempt_resolved"})
        self.assert_error("problem_minimum_unmet", lambda: self.record_post_decision("resolved-decision"))
