"""Candidates are immutable, sourced questions with provisional potential grades."""

import copy

from research_harness.evidence import digest
from test_research_problem_claims import ProblemCase


def build_candidate(identifier="candidate-1", **changes):
    return dict({"id": identifier, "previous": None,
        "statement": r"For every $n$, does $B(n)\leq n^2$ hold?",
        "source": {"kind": "literature", "reference": "The bound is stated as open in the cited survey.", "evidence": []},
        "observation": "The survey checks the bound only for small n and states the general case as open.",
        "neighbours": [r"Does $B(n)\leq 2n^2$ hold for every $n$?"],
        "potential": {"significance": {"B1": 2, "B2": 1}, "priority": 2, "readiness": "pass"},
        "notes": "", "disposition": {"status": "open", "reason": "Compare it with the other candidates of the slate.",
                                      "evidence_needed": None}}, **changes)


def build_slate(identifier="slate-1", **changes):
    return dict({"id": identifier, "selected": ["candidate-1"], "deferred": [], "rejected": [],
        "bar_review_ids": ["review-bar-first", "review-bar-second"], "review_ids": [],
        "comparison": "The candidate removes the obstacle that the read survey names; no compared neighbour does.",
        "reason": "The candidate meets both bars at potential level 2."}, **changes)


class ProblemCandidateTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()

    def load_candidates_module(self):
        from research_harness import problem_candidates
        return problem_candidates

    def record_candidate(self, payload):
        return self.mutate(self.load_candidates_module().record_candidate, payload)["result"]

    def test_records_a_candidate_with_source_and_potential(self):
        reading = self.record_full_reading()
        saved_reading = self.store.snapshot()["records"]["reading"][reading["reading_id"]]
        payload = build_candidate(source={"kind": "literature", "reference": "Open question 3 of the read survey.",
            "evidence": [{"kind": "record", "record_kind": "reading", "id": reading["reading_id"], "digest": digest(saved_reading)}]},
            notes="The survey's construction gives the lower bound.",
            disposition={"status": "deferred", "reason": "The active line is not yet documented.",
                         "evidence_needed": "Recent results that cite the open question."})
        revision = self.store.revision
        saved = self.record_candidate(payload)
        records = self.store.snapshot()["records"]
        self.assertEqual(saved, dict(payload, recorded_revision=revision + 1))
        self.assertEqual(records["problem_candidate"]["candidate-1"], saved)
        self.assertEqual(self.load_candidates_module().find_current_candidates(records), {"candidate-1": saved})
        self.assertEqual(self.load_candidates_module().SOURCE_KINDS,
                         ("own_record", "literature", "computation", "section", "user"))
        for kind in self.load_candidates_module().SOURCE_KINDS:
            self.record_candidate(build_candidate("candidate-" + kind, source={"kind": kind, "reference": "The recorded origin.", "evidence": []}))
        self.assert_error("record_conflict", lambda: self.record_candidate(dict(payload, observation="A changed observation.")))

    def test_refuses_unknown_source_kind_and_bad_potential(self):
        cases = (("invalid_input", build_candidate(source={"kind": "rumour", "reference": "Heard elsewhere.", "evidence": []})),
                 ("invalid_input", build_candidate(source={"kind": "literature", "reference": "", "evidence": []})),
                 ("invalid_input", build_candidate(disposition={"status": "maybe", "reason": "Unclear.", "evidence_needed": None})),
                 ("invalid_rubric_grades", build_candidate(potential={"significance": {"B1": 4, "B2": 0}, "priority": 2})),
                 ("invalid_rubric_grades", build_candidate(potential={"significance": {"B1": 2, "B2": 0}})),
                 ("invalid_rubric_grades", build_candidate(potential={"significance": {"B1": 2, "B2": 0}, "priority": 2,
                                                                      "readiness": "yes"})))
        for index, (code, payload) in enumerate(cases):
            with self.subTest(case=index, code=code):
                self.assert_error(code, lambda: self.record_candidate(payload))
        self.assertNotIn("problem_candidate", self.store.snapshot()["records"])

    def test_previous_must_exist_and_supersedes(self):
        self.assert_error("problem_candidate_missing", lambda: self.record_candidate(build_candidate(previous="absent")))
        for previous in ([], {}):
            with self.subTest(previous=previous):
                self.assert_error("invalid_input", lambda: self.record_candidate(build_candidate(previous=previous)))
        first = self.record_candidate(build_candidate())
        other = self.record_candidate(build_candidate("candidate-2", statement="Does the bound hold for odd $n$?"))
        revised = self.record_candidate(build_candidate("candidate-1b", previous="candidate-1",
                                                        statement=r"For every $n \geq 3$, does $B(n)\leq n^2$ hold?"))
        records = self.store.snapshot()["records"]
        self.assertEqual(records["problem_candidate"]["candidate-1"], first)
        self.assertEqual(self.load_candidates_module().find_current_candidates(records),
                         {"candidate-2": other, "candidate-1b": revised})

    def test_candidate_evidence_references_are_validated(self):
        reading = self.record_full_reading()
        saved_reading = self.store.snapshot()["records"]["reading"][reading["reading_id"]]
        reference = {"kind": "record", "record_kind": "reading", "id": reading["reading_id"], "digest": "0" * 64}
        stale = build_candidate(source={"kind": "own_record", "reference": "The cash-out of the stalled attack.", "evidence": [reference]})
        self.assert_error("strategy_evidence_stale", lambda: self.record_candidate(stale))
        current = copy.deepcopy(stale)
        current["source"]["evidence"][0]["digest"] = digest(saved_reading)
        self.assertEqual(self.record_candidate(current)["source"], current["source"])
