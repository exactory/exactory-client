"""Lineage preparation: no Tier 3 obligations, the loop, staleness notices, and hits per query."""

import json

from literature_fixtures import FIELDS, LiteratureCase
from research_harness import lineage
from research_harness.acquisition import import_response
from research_harness.literature import record_search
from research_harness.reading import record_reading_batch


class LineageCase(LiteratureCase):
    def setUp(self):
        super().setUp()
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "lineage-v1"})

    def search(self, purpose, found, query=None):
        query = query or (purpose + " bounded sequence comparison")
        data = {"query": query, "results": [{"id": identifier, "title": "Later authored evidence"} for identifier in found]}
        self.sequence += 1
        result = import_response(self.store, "mcp", json.dumps(data).encode(), source_url="https://example.org/search",
            captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/results/%d/id" % i, "title": "/results/%d/title" % i} for i in range(len(found))],
            expected_revision=self.store.revision, request_id="search-capture-" + str(self.sequence))
        source = result["source_ids"][0]
        return {"id": purpose + "-" + str(self.sequence), "profile": "research", "purpose": purpose, "queries": [query],
                "responses": [{"source_id": source, "query": query,
                               "query_locator": {"kind": "json", "pointer": "/query", "value": query}, "results_pointer": "/results"}],
                "captured_at": "2026-09-07T12:00:00Z", "scope": "The bounded-sequence contribution in this study.",
                "found_work_ids": list(found), "verdict": "nothing-new", "cited_work_ids": [],
                "dispositions": [{"work_id": w, "disposition": "out_of_scope", "reason": "Found by the authored search."} for w in found],
                "impact": "No matching prior contribution was exposed in this saved search.", "gaps": []}

    def loop_read(self, versions, purpose, disposition):
        items = [{"version_id": v, "note": "Read the complete abstract.",
                  "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS},
                  "loop": {"purposes": [purpose], "disposition": disposition, "source": "search"}} for v in versions]
        self.sequence += 1
        return self.mutate(record_reading_batch, {"id": "loop-" + str(self.sequence), "depth": "abstract", "items": items})


class TierAndStalenessTests(LineageCase):
    def test_tier_three_references_owe_no_abstract_reading(self):
        root = self.metadata(1, references=[{"id": "arxiv:2601.00002v1"}])
        self.metadata(2)
        self.scope([root])
        from research_harness.literature import foundation_report
        report = foundation_report(self.store, "research")
        self.assertEqual(report["counts"]["tier_3"], 1)
        self.assertNotIn("abstract_reading_missing", {o["code"] for o in report["obligations"]})

    def test_a_query_keeps_at_most_ten_hits_and_source_changes_are_notices(self):
        root = self.metadata(1)
        self.scope([root])
        hits = [self.metadata(n) for n in range(2, 13)]
        self.assert_error("invalid_search", lambda: self.mutate(record_search, self.search("direct", hits)))
        self.mutate(record_search, self.search("direct", hits[:10]))
        self.capture(hits[0])
        from research_harness.literature import foundation_report
        report = foundation_report(self.store, "research")
        self.assertNotIn("search_evidence_stale", {o["code"] for o in report["obligations"]})
        self.assertIn("search_evidence_stale", {o["code"] for o in report["notices"]})
        self.assertIn("stable_digest", report)


class LoopTests(LineageCase):
    def test_coverage_closure_and_gaps(self):
        root = self.metadata(1)
        self.scope([root])
        found = [self.metadata(n) for n in range(2, 5)]
        self.mutate(record_search, self.search("direct", found))
        self.assertIn("loop_closure_missing", self.codes())
        state = lineage.loop_state(self.store.snapshot()["records"], "research")
        self.assertFalse(state["purposes"]["direct"]["covered"])
        self.loop_read(found[:1], "direct", "relevant")
        state = lineage.loop_state(self.store.snapshot()["records"], "research")
        self.assertTrue(state["purposes"]["direct"]["covered"])
        purposes = {p: {"status": "gap", "note": "One query tried."} for p in lineage.SEARCH_PURPOSES}
        purposes["direct"] = {"status": "covered", "note": "One relevant paper."}
        self.assert_error("invalid_input", lambda: self.mutate(lineage.record_loop_closure, {"id": "close-1", "purposes": purposes}))
        for purpose in ("originals", "theory", "adjacent", "recent"):
            self.mutate(record_search, self.search(purpose, [], query=purpose + " first"))
            self.mutate(record_search, self.search(purpose, [], query=purpose + " second"))
            purposes[purpose] = {"status": "covered", "note": "Two distinct queries returned nothing relevant."}
        self.mutate(lineage.record_loop_closure, {"id": "close-1", "purposes": purposes})
        self.assertNotIn("loop_closure_missing", self.codes())
        self.loop_read(found[1:2], "theory", "relevant")
        self.assertIn("loop_closure_stale", self.codes())
