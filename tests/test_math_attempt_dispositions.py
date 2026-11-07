"""Problem reviewers receive solver dispositions without granting proof credit."""

import json
from pathlib import Path

import test_research_problem_attempt as attempts
import test_research_problem_claims as claims
from research_harness import problem_attempt, problem_evidence
from research_harness.problem_claims import compute_problem_evidence_digest


class AttemptDispositionConsumerTests(claims.ProblemCase):
    def test_common_store_accepts_dispositions_and_problem_reviewer_sees_them(self):
        self.initialize_problem()
        statement = self.select_statement()
        native = attempts.build_native_attempt(statement["statement"], [], {"execution_status": "ended"})
        dispositions = [{"kind": "objective_ended", "objective_id": native["objective_id"],
                         "session_id": "claude:session", "message_id": "user-end",
                         "instruction": "End this objective with the findings unaccepted.",
                         "provenance": {"source": "reported_by_agent"}}]
        native["dispositions"] = dispositions
        identifier = attempts.put_native_attempt(self, native)
        payload = attempts.build_attempt(native_attempt_digest=identifier)
        self.mutate(problem_attempt.record_attempt, payload)
        records = self.store.snapshot()["records"]
        self.assertEqual(problem_attempt.assess_attempt(records, self.artifacts), [])
        self.assertFalse(problem_attempt.is_native_resolved(native))
        projected = problem_evidence.describe_evidence(records, self.artifacts)
        self.assertIn("dispositions", projected["attempt"])
        self.assertEqual(projected["attempt"]["dispositions"], dispositions)
        self.assertEqual(projected["attempt"].get("dispositions_note"),
            "Each entry holds text submitted as a user instruction and its submitted provenance. reported_by_agent "
            "means that the agent reports the text as the user's words. host or operator records a claim of "
            "authentication by the named actor. The controller checks the form only; it does not verify authorship "
            "or whether authentication occurred. An entry is not a review, and it grants no proof credit. A "
            "result_set_aside entry removes its checkpoint from mandatory acceptance while the disposition is "
            "active; a later acceptance ends that effect and records ended_by_acceptance_id. An objective_ended "
            "entry records the end of further research, not a mathematical resolution. Neither entry determines "
            "the current mathematical status: read the attempt's proof status, obligations and acceptance "
            "evidence.")

    def test_without_dispositions_evidence_bytes_and_problem_digest_are_unchanged(self):
        self.initialize_problem()
        statement = self.select_statement()
        native = attempts.build_native_attempt(statement["statement"], [], {})
        identifier = attempts.put_native_attempt(self, native)
        self.mutate(problem_attempt.record_attempt, attempts.build_attempt(native_attempt_digest=identifier))
        records = self.store.snapshot()["records"]
        projected = problem_evidence.describe_evidence(records, self.artifacts)
        self.assertNotIn("dispositions", projected["attempt"])
        self.assertNotIn("dispositions_note", projected["attempt"])
        encoded = json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = Path(__file__).parent / "fixtures/math-attempt-without-dispositions.json"
        self.assertEqual(encoded, expected.read_bytes())
        self.assertEqual(compute_problem_evidence_digest(records, self.artifacts),
                         "d3375ec415292f24404f2fd084cffae794e8f763bdd45b5bf9d4f19a4f1c336b")
