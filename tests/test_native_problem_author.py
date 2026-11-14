"""A native review assignment explains which immutable problem author it needs."""

import json

from codex_route_fixtures import record_codex_route
from research_harness import native_review, review_protocol
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from test_research_problem_claims import ProblemCase


class NativeProblemAuthorTests(ProblemCase):
    def test_native_assignment_refusal_lists_original_problem_authors(self):
        self.initialize_problem(authors=["Casey Example", "Jordan Example"])
        record_codex_route(self)
        subject = {"statement": "Every finite fixture satisfies its stated bound."}
        native = {
            "protocol": native_review.PROTOCOL, "kind": "result", "root": str(self.root),
            "objective_id": "objective", "contract": {"proof_policy": "reviewed"},
            "subject": subject, "subject_digest": digest(subject), "claim_digest": digest(subject),
            "reviewer": {"source": "host", "actor_id": "independent-reviewer",
                         "attestation_id": "review-assignment:native-assessment"},
            "scientific_context": {"scope": "objective", "previous_assessments": [], "current": {}},
            "evidence": [], "execution_provenance": [], "unmaterialized_digests": [],
        }
        packet = self.artifacts.put(json.dumps(native).encode(), "application/json")
        payload = {"id": "native-assessment", "route_id": "route", "dossier_id": None,
                   "role": "native_math", "reviewer_id": "independent-reviewer",
                   "author_id": "unlisted-author", "context": {"native_packet": packet}}
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.mutate(review_protocol.record_assignment, payload)
        self.assertEqual(caught.exception.code, "problem_author_unknown")
        self.assertEqual(caught.exception.details, {"authors": ["Casey Example", "Jordan Example"]})
        self.assertIn("author_id", caught.exception.message)
        self.assertIn("one of", caught.exception.message)
        self.assertEqual(self.store.snapshot(), before)
        # Author matching keeps its established normalization; diagnostics retain the original strings.
        assignment = self.mutate(review_protocol.record_assignment,
                                 dict(payload, author_id="  casey   example  "))["result"]
        self.assertEqual(assignment["author_id"], "  casey   example  ")
        saved_packet = self.artifacts.read(assignment["packet"])
        saved_prompt = dict(assignment["prompt"])
        with self.assertRaises(ResearchError):
            self.mutate(review_protocol.record_assignment, dict(payload, id="unknown-author-again"))
        stored = self.store.snapshot()["records"]["review_assignment"][assignment["id"]]
        self.assertEqual(stored, assignment)
        self.assertEqual(self.artifacts.read(stored["packet"]), saved_packet)
        self.assertEqual(stored["prompt"], saved_prompt)
        self.assertTrue(review_protocol._prompt_matches(stored))
