import copy
import json

from literature_fixtures import LiteratureCase
from research_harness.acquisition import import_response
from research_harness.literature import export_foundation, foundation_report, import_bundle, record_search
from research_harness.reading import record_availability, record_reading, require_fulltext


class LiteratureTests(LiteratureCase):
    def search(self, purpose="direct", found=(), verdict="nothing-new"):
        query = purpose + " bounded sequence comparison"
        data = {"query": query, "results": [{"id": identifier, "title": "Later authored evidence"} for identifier in found]}
        self.sequence += 1
        result = import_response(self.store, "mcp", json.dumps(data).encode(), source_url="https://example.org/search",
            captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/results/%d/id" % i, "title": "/results/%d/title" % i} for i in range(len(found))],
            expected_revision=self.store.revision, request_id="search-capture-" + str(self.sequence))
        source = result["source_ids"][0]
        return {"id": purpose, "profile": "research", "purpose": purpose, "queries": [query],
                "responses": [{"source_id": source, "query": query,
                               "query_locator": {"kind": "json", "pointer": "/query", "value": query},
                               "results_pointer": "/results"}],
                "captured_at": "2026-09-07T12:00:00Z", "scope": "The bounded-sequence contribution in this study.",
                "found_work_ids": list(found), "verdict": verdict, "cited_work_ids": [],
                "impact": "No matching prior contribution was exposed in this saved search.", "gaps": []}

    def test_graph_reading_does_not_discharge_all_cohort_abstracts(self):
        collection = self.cohort()
        a = "arxiv:2601.00001v1"
        self.scope([a], [collection])
        bundle = self.bundle(a)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        self.mutate(record_reading, self.abstract_note("arxiv:2601.00002v1", "b"))
        report = foundation_report(self.store, "research")
        pending = [x for x in report["obligations"] if x["code"] == "cohort_abstract_reading_missing"]
        self.assertEqual([x["version_id"] for x in pending], ["arxiv:2601.00003v1"])
        self.assertTrue(pending[0]["paths"])
        self.assertEqual(report["counts"]["cohort_families"], 3)

    def test_five_independent_purposes_require_saved_responses_and_keep_later_work_outside_graph(self):
        a = self.metadata()
        self.scope([a])
        empty = self.search()
        self.mutate(record_search, empty)
        missing = [x["purpose"] for x in foundation_report(self.store, "research")["obligations"] if x["code"] == "search_purpose_missing"]
        self.assertEqual(set(missing), {"originals", "theory", "adjacent", "recent"})
        bad = copy.deepcopy(empty)
        bad["id"], bad["responses"] = "fabricated", []
        self.assert_error("invalid_search", lambda: self.mutate(record_search, bad))
        later = "arxiv:2602.00009v1"
        self.mutate(record_search, self.search("recent", [later]))
        item = next(x for x in foundation_report(self.store, "research")["inventory"] if x["version_id"] == later)
        self.assertIsNone(item["tier"])
        self.assertTrue(item["source_paths"])

    def test_scope_digest_ignores_unrelated_notes_and_tracks_relevant_versions_and_aliases(self):
        a, b = self.metadata(), self.metadata(2)
        self.scope([a])
        before = foundation_report(self.store, "research")["digest"]
        self.mutate(record_reading, self.abstract_note(b, "unrelated"))
        self.assertEqual(before, foundation_report(self.store, "research")["digest"])
        self.scope([b])
        self.assertNotEqual(before, foundation_report(self.store, "research")["digest"])

    def test_temporal_cutoff_keeps_current_capture_but_exposes_historical_content_gap(self):
        collection = self.cohort((1,))
        a = "arxiv:2601.00001v1"
        self.scope([a], [collection], historical_cutoff="2026-01-15")
        self.mutate(record_reading, self.abstract_note(a))
        report = foundation_report(self.store, "research")
        self.assertNotIn("cohort_abstract_reading_missing", {x["code"] for x in report["obligations"]})
        self.assertIn("historical_version_unresolved", {x["code"] for x in report["obligations"]})
        item = next(x for x in report["inventory"] if x["version_id"] == a)
        self.assertTrue(item["date_assertions"])
        self.assertEqual(report["counts"]["cohort_families"], 1)

    def test_noncritical_unavailability_is_qualified_but_critical_dependency_stays_open(self):
        a = self.metadata()
        self.scope([a])
        capture = self.capture(a, status=404)
        payload = {"id": "unavailable", "profile": "research", "version_id": a, "depth": "fulltext",
                   "source_ids": [capture["source_id"]], "reason": "The origin returns a terminal missing-page response.",
                   "policy": {"id": "terminal-origin", "minimum_attempts": 1, "allowed_statuses": [403, 404, 410, 451],
                              "rationale": "A terminal origin response documents inaccessible noncritical material."}}
        self.mutate(record_availability, payload)
        report = foundation_report(self.store, "research")
        self.assertTrue(report["availability_qualified"])
        self.assertFalse(report["ready"])
        self.mutate(require_fulltext, {"id": "critical", "profile": "research", "version_id": a,
                                     "purpose": "validity", "reason": "Validity depends on the unavailable proof."})
        self.assertIn("critical_source_unavailable", self.codes())
        limited = self.capture(a, status=429)
        bad = copy.deepcopy(payload)
        bad["id"], bad["source_ids"] = "rate-limit", [limited["source_id"]]
        self.assert_error("invalid_availability", lambda: self.mutate(record_availability, bad))

    def test_complete_mechanical_foundation_and_untrusted_human_exports(self):
        collection = self.cohort((1,))
        a = "arxiv:2601.00001v1"
        self.scope([a], [collection])
        bundle = self.bundle(a)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        for purpose in ("direct", "originals", "theory", "adjacent", "recent"):
            self.mutate(record_search, self.search(purpose))
        report = foundation_report(self.store, "research")
        self.assertTrue(report["ready"], report["obligations"])
        self.assertIn("comprehension", report["limits"])
        exported = export_foundation(self.store, "research")
        self.assertIn("inventory", exported)
        self.assertTrue((self.root / exported["inventory"]).exists())
        (self.root / exported["coverage"]).write_text("Fabricated readiness", encoding="utf-8")
        self.assertEqual(report["digest"], foundation_report(self.store, "research")["digest"])

    def test_tool_passage_is_scoped_to_its_article_and_cannot_repair_failed_origin(self):
        a, b = self.metadata(), self.metadata(2)
        failed = self.capture(a, status=403)
        data = {"items": [{"id": a, "title": "Article A", "abstract": "A reports bounded inputs."},
                          {"id": b, "title": "Neighbor B", "abstract": "B reports unbounded inputs."}]}
        imported = import_response(self.store, "web", json.dumps(data).encode(), source_url="https://example.org/passages",
            captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/items/%d/id" % i, "title": "/items/%d/title" % i, "abstract": "/items/%d/abstract" % i} for i in range(2)],
            expected_revision=self.store.revision, request_id="web-passages")
        source = self.store.snapshot()["records"]["source"][imported["source_ids"][0]]
        note = self.abstract_note(a, "passage")
        note["depth"] = "passage"
        note["inspections"][0]["link"] = {"version_id": a, "source_id": source["id"], "artifact": source["response"],
            "locator": {"kind": "json", "pointer": "/items/1/abstract", "value": "B reports unbounded inputs."}}
        self.assert_error("source_mismatch", lambda: self.mutate(record_reading, note))
        note["inspections"][0]["link"]["locator"] = {"kind": "json", "pointer": "/items/0/abstract", "value": "A reports bounded inputs."}
        self.mutate(record_reading, note)
        self.scope([a])
        self.assertIn("fulltext_reading_missing", self.codes())
        self.assertEqual(self.store.snapshot()["records"]["source"][failed["source_id"]]["status"], "failed")
        bundle = {"id": "tool", "version_id": a, "source_id": source["id"], "scope": "article", "completeness": "complete",
                  "units": [], "inventory": {}, "bibliography": {}, "resolutions": []}
        self.assert_error("invalid_bundle", lambda: self.mutate(import_bundle, bundle))

    def test_search_rejects_hidden_nonempty_results_and_requires_cited_replication(self):
        a = self.metadata()
        self.scope([a])
        search = self.search("direct", ["arxiv:2602.00009v1"])
        search["found_work_ids"] = []
        self.assert_error("invalid_search", lambda: self.mutate(record_search, search))
        replicate = self.search("theory", ["arxiv:2602.00010v1"], "replicate-extend")
        self.assert_error("invalid_search", lambda: self.mutate(record_search, replicate))
        replicate["cited_work_ids"] = replicate["found_work_ids"]
        self.mutate(record_search, replicate)
        invented = self.search("adjacent")
        invented["queries"] = ["example"]
        invented["responses"][0]["query"] = "example"
        del invented["responses"][0]["query_locator"]
        self.assert_error("invalid_search", lambda: self.mutate(record_search, invented))

    def test_cohort_report_is_independent_of_later_roots_and_searches(self):
        from research_harness.cohort_evidence import cohort_reading_report
        collection = self.cohort((1,))
        before = cohort_reading_report(self.store, [collection])
        self.assertFalse(before["ready"])
        self.assertEqual({x["code"] for x in before["obligations"]}, {"cohort_abstract_reading_missing"})
        self.mutate(record_reading, self.abstract_note("arxiv:2601.00001v1"))
        after = cohort_reading_report(self.store, [collection])
        self.assertTrue(after["ready"], after["obligations"])
        self.assertEqual(after["counts"]["cohort_families"], 1)
        self.assertNotIn("literature_scope", self.store.snapshot()["records"])

    def test_explicit_versionless_cohort_selection_preserves_history_then_requires_reading(self):
        from research_harness.acquisition import collect_cohort, collection_status
        from research_harness.cohort_evidence import cohort_reading_report, select_cohort_abstract
        from research_fixtures import atom, client, entry, xml_response
        definition = {"corpus": "arxiv", "primaryCategory": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31"}
        http, _, _ = client([xml_response(atom([entry("2601.00001", abstract=None)], total=1))])
        original = collect_cohort(self.store, definition, http=http, expected_revision=0, request_id="versionless")
        collection = original["collection_id"]
        old = self.store.snapshot()["records"]["work"]["arxiv:2601.00001"]
        version = self.metadata()
        selected = self.store.snapshot()["records"]["work"][version]
        self.assertFalse(cohort_reading_report(self.store, [collection])["ready"])
        payload = {"collection_id": collection, "work_id": old["work_id"], "unresolved_assertion_id": old["assertion_ids"][0],
                   "selected_assertion_id": selected["assertion_ids"][0], "reason": "Select the acquired current v1 abstract without dating the old capture's text."}
        result = self.mutate(select_cohort_abstract, payload)
        status = collection_status(self.store, collection)
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["reading_obligations"][0]["version_id"], version)
        self.assertTrue(status["historical_unresolved"])
        self.assertEqual(self.store.snapshot()["records"]["work"][old["id"]], old)
        self.assertEqual(status["returned_count"], original["returned_count"])
        self.assertFalse(cohort_reading_report(self.store, [collection])["ready"])
        self.mutate(record_reading, self.abstract_note(version))
        self.assertTrue(cohort_reading_report(self.store, [collection])["ready"])
        self.assertEqual(collect_cohort(self.store, definition, http=http, expected_revision=0, request_id="versionless"), original)
        self.assertEqual(select_cohort_abstract(self.store, payload, expected_revision=0, request_id=result["request_id"]), result)

    def test_cohort_selection_rejects_known_version_wrong_family_and_bad_dates(self):
        from research_harness.cohort_evidence import select_cohort_abstract
        collection = self.cohort((1,))
        a, b = "arxiv:2601.00001v1", self.metadata(2)
        records = self.store.snapshot()["records"]
        payload = {"collection_id": collection, "work_id": a[:-2], "unresolved_assertion_id": records["work"][a]["assertion_ids"][0],
                   "selected_assertion_id": records["work"][b]["assertion_ids"][0], "reason": "A fabricated replacement must fail."}
        self.assert_error("invalid_cohort_selection", lambda: self.mutate(select_cohort_abstract, payload))

    def test_search_decision_is_stale_after_relevant_source_change_and_promotes_cited_depth(self):
        a = self.metadata()
        self.scope([a])
        search = self.search("theory", ["arxiv:2602.00009v1"], "replicate-extend")
        search["cited_work_ids"] = search["found_work_ids"]
        self.mutate(record_search, search)
        report = foundation_report(self.store, "research")
        cited = next(x for x in report["inventory"] if x["version_id"] == search["found_work_ids"][0])
        self.assertEqual(cited["required_depth"], "fulltext")
        self.assertIsNone(cited["tier"])
        b = self.metadata(2)
        self.mutate(record_reading, self.abstract_note(b, "unrelated-search-note"))
        self.assertNotIn("search_evidence_stale", self.codes())
        self.capture(a, "New relevant source evidence is available. References: none.")
        self.assertIn("search_evidence_stale", self.codes())
        refreshed = self.search("theory")
        refreshed["id"] = "theory-refreshed"
        self.mutate(record_search, refreshed)
        self.assertNotIn("search_evidence_stale", self.codes())
        self.assertEqual(len(self.store.snapshot()["records"]["literature_search"]), 2)

    def test_versionless_selection_cannot_clear_paused_enumeration_or_terminal_evidence_gaps(self):
        from research_harness.acquisition import collect_cohort, collection_status
        from research_harness.cohort_evidence import select_cohort_abstract
        from research_fixtures import atom, client, entry, xml_response
        http, _, _ = client([xml_response(atom([entry("2601.00001")], total=2, size=1))])
        initial = collect_cohort(self.store, {"corpus": "arxiv", "primaryCategory": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31"},
            page_size=1, max_requests=1, http=http, expected_revision=0, request_id="partial-cohort")
        a = self.metadata()
        records = self.store.snapshot()["records"]
        payload = {"collection_id": initial["collection_id"], "work_id": a[:-2],
            "unresolved_assertion_id": records["work"][a[:-2]]["assertion_ids"][0],
            "selected_assertion_id": records["work"][a]["assertion_ids"][0], "reason": "Choose current abstract evidence only."}
        self.mutate(select_cohort_abstract, payload)
        current = collection_status(self.store, initial["collection_id"])
        self.assertEqual(current["pending"], initial["pending"][:1])
        self.assertEqual(current["status"], "paused")
        self.assertEqual(current["pending_partitions"], initial["pending_partitions"])

    def test_versionless_selection_rejects_different_family_category_window_and_partial_abstract(self):
        from research_harness.acquisition import collect_cohort
        from research_harness.cohort_evidence import select_cohort_abstract
        from research_fixtures import atom, client, entry, xml_response
        http, _, _ = client([xml_response(atom([entry("2601.00001")], total=1))])
        initial = collect_cohort(self.store, {"corpus": "arxiv", "primaryCategory": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31"},
            http=http, expected_revision=0, request_id="unresolved-cohort")
        original = self.store.snapshot()["records"]["work"]["arxiv:2601.00001"]["assertion_ids"][0]
        variants = [entry("2601.00002v1"), entry("2601.00001v1", category="cs.AI"),
                    entry("2601.00001v2", published="2026-02-02T12:00:00Z"), entry("2601.00001v3", abstract=None)]
        for index, raw in enumerate(variants):
            imported = import_response(self.store, "arxiv", atom([raw], total=1), source_url="https://example.org/arxiv",
                captured_at="2026-09-07T12:00:00Z", expected_revision=self.store.revision, request_id="invalid-selection-" + str(index))
            assertions = self.store.snapshot()["records"]["work_assertion"]
            selected = next(a["assertion_id"] for a in assertions.values() if a["source_id"] == imported["source_ids"][0])
            payload = {"collection_id": initial["collection_id"], "work_id": "arxiv:2601.00001", "unresolved_assertion_id": original,
                       "selected_assertion_id": selected, "reason": "This mismatch must not replace current evidence."}
            self.assert_error("invalid_cohort_selection", lambda: self.mutate(select_cohort_abstract, payload))

    def test_pending_retrievals_and_deadlines_remain_visible_without_unavailability(self):
        from research_harness.acquisition import acquire_fulltext, collect_cohort
        from research_fixtures import client
        a = self.metadata()
        http, _, _ = client([(429, {"Retry-After": "120"}, b"Limited")], max_retries=0)
        collection = collect_cohort(self.store, {"corpus": "arxiv", "primaryCategory": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31"},
            http=http, expected_revision=self.store.revision, request_id="limited-collection", max_requests=1)
        self.scope([a], [collection["collection_id"]])
        acquire_fulltext(self.store, a, "https://arxiv.org/html/" + a[6:], max_requests=0,
                         expected_revision=self.store.revision, request_id="paused-body")
        report = foundation_report(self.store, "research")
        self.assertEqual(report["collections"][0]["next_eligible_at"], collection["next_eligible_at"])
        self.assertFalse(report["availability_qualified"])
        self.assertIn("collection_pending", {x["code"] for x in report["obligations"]})
        self.assertEqual(report["inventory"][0]["retrievals"][0]["extraction_status"], "request_budget")
