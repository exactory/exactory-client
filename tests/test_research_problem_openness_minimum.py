"""Every shared openness refusal is exercised against common problem evidence."""

import copy
import json

from test_research_problem_claims import ProblemCase
from research_harness.acquisition import import_response
from research_harness.evidence import digest
from research_harness.literature import import_bundle, record_search
from research_harness.openness_minimum import audit_minimum, check_search_coverage, find_reassessment
from research_harness.reading import record_availability, record_reading, require_fulltext
from research_harness.source_deferrals import assess_deferrals, defer_source


class ProblemOpennessMinimumTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.selected = self.select_statement()

    def search(self, found=(), *, formulation=None, dispositions=None, gaps=(), resolved=(),
               captured_at="2026-10-15T12:00:00Z", query=None):
        formulation = formulation or self.selected["statement"]
        query = query or formulation
        data = {"query": query, "results": [{"id": work, "title": "Authored mathematical source"} for work in found]}
        self.sequence += 1
        captured = import_response(self.store, "mcp", json.dumps(data).encode(),
            source_url="https://example.org/problem-search", captured_at=captured_at,
            media_type="application/json", mappings=[{"id": "/results/%d/id" % i,
                "title": "/results/%d/title" % i} for i in range(len(found))],
            expected_revision=self.store.revision, request_id="problem-search-capture-" + str(self.sequence))
        identifier = "search-" + str(self.sequence)
        self.mutate(record_search, {"id": identifier, "profile": "research", "purpose": "direct",
            "question": formulation, "scope": formulation, "queries": [query],
            "responses": [{"source_id": captured["source_ids"][0], "query": query,
                "query_locator": {"kind": "json", "pointer": "/query", "value": query},
                "results_pointer": "/results"}], "captured_at": captured_at,
            "found_work_ids": list(found), "verdict": "nothing-new", "cited_work_ids": [],
            "dispositions": [{"work_id": work, "disposition": (dispositions or {}).get(work, "relevant"),
                "reason": "Explicit relevance judgment of the captured source."} for work in found],
            "resolved": [{"work_id": work, "reason": "The captured reassessment explicitly resolves the prior judgment."}
                         for work in resolved],
            "impact": "Evidence for the exact problem formulation.", "gaps": list(gaps)})
        return self.store.snapshot()["records"]["literature_search"][identifier]

    def read(self, work, identifier="full"):
        bundle = self.bundle(work, bundle_id="bundle-" + identifier)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, identifier))
        return self.store.snapshot()["records"]["reading"][identifier]

    def build_finding(self, searches=(), readings=(), *, outcome="supported_open", neighbours=(), settling=None):
        minimum = {"schema_version": 1, "statement": self.selected["statement"],
            "neighbours": list(neighbours), "searches": [{"formulation": row["question"],
                "search_digest": digest(row)} for row in searches],
            "reading_digests": [digest(row) for row in readings], "settling_work_id": settling}
        return {"outcome": outcome, "searched_at": "2026-10-17", "minimum": minimum,
            "evidence": [digest(row) for row in searches] + [digest(row) for row in readings]}

    def audit(self, finding, *, records=None, delivered=None, statement=None):
        return audit_minimum(finding, statement or self.selected["statement"],
            self.store.snapshot()["records"] if records is None else records, self.artifacts,
            set(finding["evidence"]) if delivered is None else delivered)

    def test_resolved_finding_requires_a_minimum(self):
        self.assert_error("openness_minimum_required", lambda: self.audit({"outcome": "supported_open", "evidence": []}))

    def test_exact_statement_and_every_declared_neighbour_need_bound_searches(self):
        exact = self.search()
        neighbour = "An equivalent extremal sequence formulation."
        finding = self.build_finding([exact], neighbours=[neighbour])
        self.assert_error("openness_search_missing", lambda: self.audit(finding))
        adjacent = self.search(formulation=neighbour)
        self.audit(self.build_finding([exact, adjacent], neighbours=[neighbour]))

    def test_minimum_statement_must_match_the_claim_text(self):
        finding = self.build_finding([self.search()])
        self.assert_error("openness_statement_mismatch", lambda: self.audit(finding, statement="A different exact claim."))

    def test_bound_search_must_address_its_declared_formulation(self):
        other = self.search(formulation="A different mathematical question.")
        finding = self.build_finding([other])
        finding["minimum"]["searches"][0]["formulation"] = self.selected["statement"]
        self.assert_error("openness_statement_mismatch", lambda: self.audit(finding))

    def test_search_digest_must_name_an_exact_record_in_this_store(self):
        finding = self.build_finding()
        finding["minimum"]["searches"] = [{"formulation": self.selected["statement"], "search_digest": "a" * 64}]
        finding["evidence"] = ["a" * 64]
        self.assert_error("openness_search_missing", lambda: self.audit(finding))

    def test_search_must_be_bound_by_the_finding_and_delivered_to_its_reviewer(self):
        finding = self.build_finding([self.search()])
        self.assert_error("openness_evidence_mismatch", lambda: self.audit(finding, delivered=set()))
        finding["evidence"] = []
        self.assert_error("openness_evidence_mismatch", lambda: self.audit(finding))

    def test_reading_digests_must_name_exact_records_in_this_store(self):
        finding = self.build_finding([self.search()])
        finding["minimum"]["reading_digests"] = ["b" * 64]
        finding["evidence"].append("b" * 64)
        self.assert_error("openness_evidence_mismatch", lambda: self.audit(finding))

    def test_full_reading_must_be_delivered_as_well_as_named_in_the_minimum(self):
        work = self.metadata()
        reading = self.read(work)
        search = self.search([work])
        finding = self.build_finding([search], [reading])
        self.assert_error("openness_evidence_mismatch", lambda: self.audit(finding, delivered={digest(search)}))
        finding["evidence"].remove(digest(reading))
        self.assert_error("openness_evidence_mismatch", lambda: self.audit(finding))

    def test_capture_gaps_require_a_pinned_later_coverage_reassessment(self):
        incomplete = self.search(gaps=["The captured enumeration leaves a source gap."])
        finding = self.build_finding([incomplete])
        self.assertFalse(check_search_coverage(incomplete))
        self.assert_error("openness_search_gap", lambda: self.audit(finding))
        complete = self.search(captured_at="2026-10-16T12:00:00Z")
        selected = [({"formulation": self.selected["statement"]}, complete)]
        self.assertEqual(find_reassessment(incomplete, self.selected["statement"], selected), complete)
        self.audit(self.build_finding([incomplete, complete]))
        self.assert_error("openness_search_stale", lambda: self.audit(self.build_finding([complete])))

    def test_integrity_failure_remains_a_search_gap_even_with_a_coverage_label(self):
        search = self.search()
        records = copy.deepcopy(self.store.snapshot()["records"])
        damaged = records["literature_search"][search["id"]]
        damaged["integrity_errors"] = [{"code": "captured_result_unaccounted", "reason": "A captured result was omitted."}]
        finding = self.build_finding([damaged])
        self.assert_error("openness_search_gap", lambda: self.audit(finding, records=records))

    def test_finding_cannot_predate_a_captured_search(self):
        finding = self.build_finding([self.search()])
        finding["searched_at"] = "2026-10-14"
        self.assert_error("openness_search_date", lambda: self.audit(finding))

    def test_new_material_search_requires_reassessment_but_an_empty_search_does_not(self):
        finding = self.build_finding([self.search()])
        self.search(captured_at="2026-10-16T12:00:00Z")
        self.audit(finding)
        work = self.metadata()
        self.search([work], captured_at="2026-10-17T12:00:00Z")
        self.assert_error("openness_search_stale", lambda: self.audit(finding))

    def test_relevant_and_contradictory_sources_require_their_pinned_full_reading(self):
        relevant, contradictory = self.metadata(), self.metadata(2)
        search = self.search([relevant, contradictory], dispositions={contradictory: "contradictory"})
        finding = self.build_finding([search])
        self.assert_error("openness_reading_gap", lambda: self.audit(finding))
        abstract = self.abstract_note(relevant)
        self.mutate(record_reading, abstract)
        partial = self.build_finding([search], [self.store.snapshot()["records"]["reading"][abstract["id"]]])
        self.assert_error("openness_reading_gap", lambda: self.audit(partial))
        full = self.read(relevant, "relevant-full")
        self.assert_error("openness_reading_gap", lambda: self.audit(self.build_finding([search], [full])))
        other = self.read(contradictory, "contradictory-full")
        self.audit(self.build_finding([search], [full, other]))

    def test_uncertain_result_needs_explicit_resolution_before_it_can_be_out_of_scope(self):
        work = self.metadata()
        uncertain = self.search([work], dispositions={work: "potentially_relevant"})
        unbound_resolution = self.search([work], dispositions={work: "out_of_scope"},
            captured_at="2026-10-16T12:00:00Z")
        self.assert_error("openness_reading_gap", lambda: self.audit(self.build_finding([uncertain, unbound_resolution])))
        resolved = self.search([work], dispositions={work: "out_of_scope"}, resolved=[work],
            captured_at="2026-10-17T12:00:00Z")
        finding = self.build_finding([uncertain, unbound_resolution, resolved])
        self.audit(finding)
        self.assertNotIn("reading", self.store.snapshot()["records"])

    def test_reassessment_cannot_use_an_earlier_capture_or_an_unrelated_query(self):
        work = self.metadata()
        uncertain = self.search([work], dispositions={work: "unresolved"}, captured_at="2026-10-16T12:00:00Z")
        earlier = self.search([work], dispositions={work: "out_of_scope"}, resolved=[work])
        other_query = self.search([work], dispositions={work: "out_of_scope"}, resolved=[work],
            captured_at="2026-10-17T12:00:00Z", query="A different query with different coverage")
        self.assert_error("openness_reading_gap", lambda: self.audit(self.build_finding([uncertain, earlier, other_query])))

    def test_duplicate_requires_an_explicit_fully_read_decisive_canonical_source(self):
        duplicate, canonical = self.metadata(), self.metadata(2)
        search = self.search([duplicate, canonical], dispositions={duplicate: "duplicate"})
        reading = self.read(canonical)
        finding = self.build_finding([search], [reading])
        self.assert_error("openness_reading_gap", lambda: self.audit(finding))
        finding["minimum"]["duplicate_resolutions"] = [{"search_digest": digest(search),
            "work_id": duplicate, "canonical_work_id": canonical}]
        self.audit(finding)
        self.assertEqual({row["version_id"] for row in self.store.snapshot()["records"]["reading"].values()}, {canonical})
        finding["minimum"]["reading_digests"] = []
        finding["evidence"].remove(digest(reading))
        self.assert_error("openness_reading_gap", lambda: self.audit(finding))

    def test_duplicate_canonical_source_must_be_decisive_in_the_same_search(self):
        duplicate, canonical = self.metadata(), self.metadata(2)
        search = self.search([duplicate, canonical], dispositions={duplicate: "duplicate", canonical: "out_of_scope"})
        finding = self.build_finding([search], [self.read(canonical)])
        finding["minimum"]["duplicate_resolutions"] = [{"search_digest": digest(search),
            "work_id": duplicate, "canonical_work_id": canonical}]
        self.assert_error("openness_reading_gap", lambda: self.audit(finding))

    def test_unavailable_and_authorized_deferred_sources_remain_openness_gaps(self):
        work = self.metadata()
        finding = self.build_finding([self.search([work])])
        capture = self.capture(work, status=404)
        self.mutate(record_availability, {"id": "missing", "profile": "research", "version_id": work,
            "depth": "fulltext", "source_ids": [capture["source_id"]], "reason": "The exact original returned 404.",
            "policy": {"id": "recorded-origin-404", "minimum_attempts": 1, "allowed_statuses": [404],
                       "rationale": "Unavailability does not replace a full reading."}})
        self.assert_error("openness_reading_gap", lambda: self.audit(finding))
        self.mutate(require_fulltext, {"id": "decisive", "profile": "research", "version_id": work,
            "purpose": "validity", "reason": "This prior source is decisive for openness."})
        self.mutate(defer_source, {"id": "gap", "profile": "research", "version_id": work,
            "reason": "The prior original is unavailable.",
            "authorization": self.artifacts.put(b"Continue with this source gap.", "text/plain"),
            "acquisition_evidence": [self.artifacts.put(b"Captured retrieval log: original unavailable.", "text/plain")],
            "dependent_claims": ["Openness cannot be settled without this original."],
            "continuing_work": ["Explore independently supported formulations."]})
        self.assertEqual(assess_deferrals(self.store.snapshot()["records"], self.artifacts)[0]["status"], "active")
        self.assert_error("openness_reading_gap", lambda: self.audit(finding))
        reading = self.read(work)
        self.assertEqual(assess_deferrals(self.store.snapshot()["records"], self.artifacts)[0]["status"], "stale")
        self.assert_error("openness_reading_gap", lambda: self.audit(self.build_finding(
            list(self.store.snapshot()["records"]["literature_search"].values()), [reading])))

    def test_settling_source_must_be_decisive_and_cannot_support_an_open_outcome(self):
        work = self.metadata()
        reading = self.read(work)
        search = self.search([work])
        finding = self.build_finding([search], [reading], outcome="already_solved")
        self.assert_error("openness_settling_source", lambda: self.audit(finding))
        finding["minimum"]["settling_work_id"] = "arxiv:2601.99999v1"
        self.assert_error("openness_settling_source", lambda: self.audit(finding))
        finding["minimum"]["settling_work_id"] = work
        self.audit(finding)
        finding["outcome"] = "supported_open"
        self.assert_error("openness_settling_source", lambda: self.audit(finding))

    def test_minimum_schema_keeps_the_native_invalid_record_refusals(self):
        baseline = self.build_finding([self.search()])
        mutations = [lambda m: m.update(extra="unexpected"), lambda m: m.update(schema_version=True),
            lambda m: m.update(statement=""), lambda m: m.update(neighbours=[m["statement"]]),
            lambda m: m.update(reading_digests=["not-a-digest"]),
            lambda m: m.update(duplicate_resolutions=[{"search_digest": "a" * 64,
                "work_id": "same-source", "canonical_work_id": "same-source"}])]
        for change in mutations:
            with self.subTest(change=change):
                finding = copy.deepcopy(baseline)
                change(finding["minimum"])
                self.assert_error("invalid_record", lambda: self.audit(finding))
