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

    def capture_search(self, query, found):
        """One saved tool response holding this query and the works it returned."""
        data = {"query": query, "results": [{"id": identifier, "title": "Later authored evidence"} for identifier in found]}
        self.sequence += 1
        result = import_response(self.store, "mcp", json.dumps(data).encode(), source_url="https://example.org/search",
            captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/results/%d/id" % i, "title": "/results/%d/title" % i} for i in range(len(found))],
            expected_revision=self.store.revision, request_id="search-capture-" + str(self.sequence))
        return {"source_id": result["source_ids"][0], "query": query,
                "query_locator": {"kind": "json", "pointer": "/query", "value": query}, "results_pointer": "/results"}

    def judgment(self, purpose, captures, disposition="out_of_scope"):
        """One nothing-new judgment over the given (query, works) captures."""
        responses = [self.capture_search(query, works) for query, works in captures]
        found = sorted({w for _, works in captures for w in works})
        self.sequence += 1
        return {"id": purpose + "-" + str(self.sequence), "profile": "research", "purpose": purpose,
                "queries": sorted({query for query, _ in captures}), "responses": responses,
                "captured_at": "2026-09-07T12:00:00Z", "scope": "The bounded-sequence contribution in this study.",
                "found_work_ids": found, "verdict": "nothing-new", "cited_work_ids": [],
                "dispositions": [{"work_id": w, "disposition": disposition, "reason": "Found by the authored search."} for w in found],
                "impact": "No matching prior contribution was exposed in this saved search.", "gaps": []}

    def search(self, purpose, found, query=None, disposition="out_of_scope"):
        return self.judgment(purpose, [(query or (purpose + " bounded sequence comparison"), found)], disposition)

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

    def test_a_query_pools_the_hits_of_all_its_responses(self):
        root = self.metadata(1)
        self.scope([root])
        hits = [self.metadata(n) for n in range(2, 14)]
        paginated = [("direct bounded pages", hits[:6]), ("direct bounded pages", hits[6:])]
        self.assert_error("invalid_search", lambda: self.mutate(record_search, self.judgment("direct", paginated)))
        distinct = [("direct bounded sequences", hits[:6]), ("direct finite examples", hits[6:])]
        self.mutate(record_search, self.judgment("direct", distinct))


class LoopTests(LineageCase):
    def test_coverage_closure_and_gaps(self):
        root = self.metadata(1)
        self.scope([root])
        found = [self.metadata(n) for n in range(2, 6)]
        self.mutate(record_search, self.search("direct", found[:3]))
        self.assertIn("loop_closure_missing", self.codes())
        state = lineage.loop_state(self.store.snapshot()["records"], "research")
        self.assertFalse(state["purposes"]["direct"]["covered"])
        self.loop_read(found[:1], "direct", "relevant")
        state = lineage.loop_state(self.store.snapshot()["records"], "research")
        self.assertTrue(state["purposes"]["direct"]["covered"])
        purposes = {p: {"status": "gap", "note": "One query tried."} for p in lineage.SEARCH_PURPOSES}
        purposes["direct"] = {"status": "covered", "note": "One relevant paper."}
        self.assert_error("invalid_input", lambda: self.mutate(lineage.record_loop_closure, {"id": "close-1", "purposes": purposes}))
        for purpose in ("originals", "theory", "adjacent"):
            self.mutate(record_search, self.search(purpose, [], query=purpose + " first"))
            self.mutate(record_search, self.search(purpose, [], query=purpose + " second"))
            purposes[purpose] = {"status": "covered", "note": "Two distinct queries returned nothing relevant."}
        # A relevant finding nobody read leaves this purpose a recorded gap after two queries.
        self.mutate(record_search, self.search("recent", found[3:], query="recent first", disposition="relevant"))
        self.mutate(record_search, self.search("recent", [], query="recent second"))
        purposes["recent"] = {"status": "gap", "note": "Two queries tried; the relevant finding is unread."}
        self.mutate(lineage.record_loop_closure, {"id": "close-1", "purposes": purposes})
        self.assertNotIn("loop_closure_missing", self.codes())
        closure = lineage.current_closure(self.store.snapshot()["records"])
        self.assertEqual(closure["purposes"]["recent"]["queries_tried"], ["recent first", "recent second"])
        self.mutate(record_search, self.search("downstream", [], query="downstream consequences"))
        self.assertNotIn("loop_closure_stale", self.codes())
        self.loop_read(found[1:2], "theory", "relevant")
        self.assertIn("loop_closure_stale", self.codes())

    def test_a_closure_states_a_policy_and_a_status_the_loop_supports(self):
        from research_harness.principles import change_policy
        root = self.metadata(1)
        self.scope([root])
        found = [self.metadata(2)]
        self.mutate(record_search, self.search("direct", found))
        for purpose in ("originals", "theory", "adjacent", "recent"):
            self.mutate(record_search, self.search(purpose, [], query=purpose + " first"))
            self.mutate(record_search, self.search(purpose, [], query=purpose + " second"))
        closure = {p: {"status": "covered", "note": "Two distinct queries returned nothing relevant."} for p in lineage.SEARCH_PURPOSES}
        closure["direct"] = {"status": "covered", "note": "One relevant paper."}
        self.assert_error("invalid_input", lambda: self.mutate(lineage.record_loop_closure, {"id": "uncovered", "purposes": closure}))
        self.loop_read(found, "direct", "relevant")
        unknown = dict(closure, direct={"status": "partial", "note": "Somewhere between the two."})
        self.assert_error("invalid_input", lambda: self.mutate(lineage.record_loop_closure, {"id": "unknown", "purposes": unknown}))
        covered_gap = dict(closure, direct={"status": "gap", "note": "Recorded as a gap after all."})
        self.assert_error("invalid_input", lambda: self.mutate(lineage.record_loop_closure, {"id": "covered-gap", "purposes": covered_gap}))
        self.mutate(change_policy, {"previous": "lineage-v1", "policy": "exhaustive-v1",
                                    "reason": "The study returns to the legacy preparation policy."})
        self.assert_error("policy_inapplicable", lambda: self.mutate(lineage.record_loop_closure, {"id": "legacy", "purposes": closure}))


class OutsideReadingTests(LineageCase):
    def test_a_full_reading_of_a_source_the_study_does_not_hold_leaves_the_foundation_unchanged(self):
        # A contribution analysis cites such a reading after the bundle is pinned, under the research default policy.
        from research_harness.literature import foundation_report, import_bundle
        from research_harness.reading import record_reading
        root = self.metadata(1)
        self.scope([root])
        self.mutate(record_search, self.search("direct", [self.metadata(2)]))
        before = foundation_report(self.store, "research")
        outside = self.metadata(50)
        bundle = self.bundle(outside, bundle_id="outside-bundle")
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, note_id="outside-reading"))
        after = foundation_report(self.store, "research")
        self.assertEqual((after["digest"], after["stable_digest"], after["frontier_digest"]),
                         (before["digest"], before["stable_digest"], before["frontier_digest"]))


class ExportTests(LineageCase):
    def test_loop_export_lists_search_hits_without_a_loop_reading_and_candidates(self):
        from pathlib import Path
        from research_harness.batches import export_batches
        root = self.metadata(1)
        self.scope([root])
        found = [self.metadata(n) for n in range(2, 5)]
        self.mutate(record_search, self.search("direct", found))
        self.loop_read(found[:1], "direct", "relevant")
        extra = self.metadata(9)
        result = export_batches(self.store, destination=str(Path(self.temporary.name) / "loop"), loop=True, candidates=[extra])
        listed = [item["version_id"] for item in json.loads(Path(result["files"][0]).read_text())["items"]]
        self.assertEqual(listed, found[1:] + [extra])
        template = json.loads((Path(result["files"][0]).parent / "README.json").read_text())["notes_shape"]["items"][0]
        self.assertEqual(set(template["loop"]), {"purposes", "disposition", "source"})
        self.assertEqual(template["loop"]["source"], "|".join(lineage.LOOP_SOURCES))
        self.assertIs(template["innovation_candidate"], True)

    def test_a_plain_export_is_refused_under_lineage(self):
        from pathlib import Path
        from research_harness.batches import export_batches
        self.cohort((1, 2, 3))
        self.assert_error("policy_inapplicable",
                          lambda: export_batches(self.store, destination=str(Path(self.temporary.name) / "plain")))

    def test_population_query_ranks_members_by_matched_terms(self):
        collection = self.cohort((1, 2, 3))
        report = lineage.population_query(self.store, ["bounded", "finite", "missingterm"], limit=2)
        self.assertEqual(len(report["matches"]), 2)
        self.assertEqual(report["matches"][0]["matched_terms"], ["bounded", "finite"])
        self.assert_error("invalid_input", lambda: lineage.population_query(self.store, ["bounded", "bounded"]))
