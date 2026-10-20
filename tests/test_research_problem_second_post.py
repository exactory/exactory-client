"""An exploration retains separate post chains for its selected candidates."""

import json
from unittest.mock import patch

from research_harness import problem_attempt, review_protocol
from research_harness.cli import status_report
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.gates import gate_state
from research_harness.literature import record_search
from research_harness.problem_delivery import export_problem
from research_harness.problem_openness import export_openness
from test_research_problem_assessment import build_assessment
from test_research_problem_attempt import build_attempt
from test_research_problem_delivery import ProblemDeliveryCase


class ProblemSecondPostTests(ProblemDeliveryCase):
    def test_a_second_selected_candidate_is_posted_from_the_same_workspace(self):
        claims = self.load_problem_module()
        self.initialize_problem()
        self.record_candidate()
        second_text = r"For every $n$, does $C(n)\leq n^3$ hold?"
        self.record_candidate("candidate-2", statement=second_text)
        self.observe_bars()
        self.record_slate(selected=["candidate-1", "candidate-2"],
            comparison="The two bounds address distinct unresolved needs of the same active line.",
            reason="Both candidates meet the two field bars and warrant their own attempts.")

        # The first chain follows prepare_ready, with both candidates already selected.
        self.selected = self.select_statement(candidate_id="candidate-1")
        first_statement = self.selected
        reading = self.record_full_reading()
        self.mutate(problem_attempt.record_attempt, build_attempt(reading=reading))
        self.observe_openness()
        self.mutate(claims.record_openness, {"assignment_id": "openness-assignment"})
        self.mutate(claims.record_value, self.build_value([reading]))
        self.mutate(claims.record_assessment, build_assessment(self.selected))
        for reviewer in ("first", "second"):
            self.observe("result", reviewer)
        self.record_post_decision()
        first_directory = self.root / "first-post-export"
        export_problem(self.store, first_directory)
        first_post = self.mutate(claims.record_posted, {
            "problem_id": "problem-first", "export_directory": str(first_directory)})["result"]
        first_post_digest = digest(first_post)

        self.selected = self.select_statement(identifier="statement-2", candidate_id="candidate-2",
            title="A second finite bound problem", statement=second_text,
            resolution_criteria="Prove the cubic bound for every n or exhibit a counterexample.")
        records = self.store.snapshot()["records"]
        self.assertEqual(records["problem_selection"]["current"], {"statement_id": "statement-2"})
        report = status_report(self.store)
        self.assertEqual(report["milestone"], "formulation")
        self.assertEqual(report["next"], {
            "code": "problem_attempt_stale",
            "explanation": "Reassess the attempt for the current statement and criteria.",
            "attempt_id": "attempt-1",
            "statement_id": "statement-2"})
        pending_pair = [row for row in gate_state(records, self.artifacts, "post")["obligations"]
                        if row["code"] == "problem_review_pair_required"]
        self.assertEqual(pending_pair, [{
            "code": "problem_review_pair_required",
            "explanation": "A major scientific decision requires two distinct independent assessments",
            "review_ids": []}])

        self.mutate(problem_attempt.record_attempt,
            build_attempt("attempt-2", reading=reading, statement_id="statement-2"))
        # Openness uses the existing fixture protocol with fresh immutable record ids.
        search = self.capture_search()
        search.update(id="direct-second", question=second_text)
        self.mutate(record_search, search)
        exported = export_openness(self.store, {
            "assignment_id": "openness-second", "reviewer_id": "native-reviewer",
            "minimum": {"searches": [{"formulation": second_text, "search_id": "direct-second"}],
                        "settling_work_id": None, "duplicate_resolutions": []}},
            self.root / "openness-second-packet")
        native = json.loads(self.artifacts.read(exported["native_packet"]))
        self.mutate(review_protocol.record_assignment, {
            "id": "openness-second", "route_id": "route", "dossier_id": None,
            "role": "native_math", "reviewer_id": "native-reviewer", "author_id": "problem-author",
            "context": {"native_packet": exported["native_packet"]}})
        output = {"schema_version": 2, "subject_digest": native["subject_digest"],
            "claim_digest": native["claim_digest"], "reviewer": native["reviewer"], "decision": "approve",
            "findings": {**{key: "Assessed the exact supplied subject."
                            for key in ("statement", "assumptions", "scope", "dependencies", "policy")},
                "openness": {"outcome": "supported_open", "statement_digest": native["claim_digest"],
                    "evidence": [entry["digest"] for entry in native["evidence"]],
                    "searched_at": "2026-09-07",
                    "coverage": "The second exact formulation was searched in the captured provider response.",
                    "reason": "No settling result was exposed in the bounded captured search.",
                    "minimum": native["scientific_context"]["openness_minimum"]}}}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(review_protocol.invoke_assignment,
                {"id": "openness-second-attempt", "assignment_id": "openness-second"}, credential="test-only")
        self.mutate(claims.record_openness, {"assignment_id": "openness-second"})
        value = self.build_value([reading], "value-2")
        value["statement_id"] = "statement-2"
        self.mutate(claims.record_value, value)
        self.mutate(claims.record_assessment, build_assessment(self.selected, "assessment-2"))

        records = self.store.snapshot()["records"]
        pending_pair = [row for row in gate_state(records, self.artifacts, "post")["obligations"]
                        if row["code"] == "problem_review_pair_required"]
        self.assertEqual(pending_pair, [{
            "code": "problem_review_pair_required",
            "explanation": "A major scientific decision requires two distinct independent assessments",
            "review_ids": []}])
        with self.assertRaises(ResearchError) as caught:
            self.mutate(claims.record_decision, {"id": "reuse-first-reviews", "action": "post",
                "review_ids": ["review-result-first", "review-result-second"],
                "bar_review_ids": ["review-bar-first", "review-bar-second"],
                "reason": "Check whether first-statement result reviews can authorize the second statement."})
        self.assertEqual(caught.exception.code, "problem_review_pair_required")
        self.assertEqual(caught.exception.details, {"obligations": [{
            "code": "problem_review_pair_required", "explanation": "Use reviews of the exact current problem statement",
            "review_ids": ["review-result-first", "review-result-second"]}]})

        for reviewer in ("first", "second"):
            identifier = "result-second-" + reviewer
            self.observe("result", reviewer, identifier=identifier)
            records = self.store.snapshot()["records"]
            assignment = records["review_assignment"][identifier]
            bar = records["review_assignment"]["bar-" + reviewer]
            self.assertEqual(assignment["subject"], {"kind": "problem", "statement_id": "statement-2"})
            self.assertEqual(assignment["reviewer_id"], reviewer)
            self.assertEqual(assignment["session_id"], bar["session_id"])
            self.assertEqual(assignment["history_assignment_id"], bar["id"])
        self.mutate(claims.record_decision, {"id": "decision-2", "action": "post",
            "review_ids": ["review-result-second-first", "review-result-second-second"],
            "bar_review_ids": ["review-bar-first", "review-bar-second"],
            "reason": "Both original bar reviewers assessed the second statement's evidence in their bar sessions."})
        second_directory = self.root / "second-post-export"
        export_problem(self.store, second_directory)
        second_post = self.mutate(claims.record_posted, {
            "problem_id": "problem-second", "export_directory": str(second_directory)})["result"]

        records = self.store.snapshot()["records"]
        posts = records["problem_post"]
        self.assertEqual(len(posts), 2)
        self.assertEqual({post["problem_id"] for post in posts.values()}, {"problem-first", "problem-second"})
        self.assertNotEqual(first_post["statement_digest"], second_post["statement_digest"])
        self.assertEqual({post["statement_digest"] for post in posts.values()}, {
            claims.compute_statement_digest(first_statement), claims.compute_statement_digest(self.selected)})
        self.assertEqual(posts[first_post["id"]], first_post)
        self.assertEqual(digest(posts[first_post["id"]]), first_post_digest)
        expected = {"code": "problem_already_posted", "explanation": "This exact statement already has a recorded post.",
                    "post_id": second_post["id"], "problem_id": "problem-second"}
        self.assertEqual(gate_state(records, self.artifacts, "post")["obligations"], [expected])
        report = status_report(self.store)
        self.assertEqual(report["milestone"], "post")
        self.assertEqual(report["statement_id"], "statement-2")
        self.assertEqual(report["next"], expected)
