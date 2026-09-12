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
                "dispositions": [{"work_id": w, "disposition": "relevant", "reason": "Found by the authored search."} for w in found],
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

    def test_registry_abstract_absence_qualifies_noncritical_abstract_depth(self):
        from research_harness.acquisition import acquire_work
        from research_fixtures import client, json_response, crossref, openalex
        a = self.metadata(references=[{"id": "openalex:W123"}])
        self.scope([a])
        without_crossref, without_openalex = crossref(), openalex()
        del without_crossref["message"]["abstract"]
        del without_openalex["abstract_inverted_index"]
        http, _, _ = client([json_response(without_openalex), json_response(without_crossref), json_response(crossref())])
        acquire_work(self.store, "W123", request_id="oa-absent", expected_revision=self.store.revision, http=http)
        acquire_work(self.store, "10.1234/example", request_id="cr-absent", expected_revision=self.store.revision, http=http)
        self.assertIn("abstract_reading_missing", self.codes())
        records = self.store.snapshot()["records"]
        sources = {s["provider"]: s["id"] for s in records["source"].values()}
        payload = {"id": "registry-absent", "profile": "research", "version_id": "openalex:W123", "depth": "abstract",
                   "source_ids": [sources["openalex"], sources["crossref"]],
                   "reason": "Neither registry publishes an abstract for this journal article.",
                   "policy": {"id": "registry-abstract-absent", "minimum_attempts": 2, "allowed_statuses": [200],
                              "rationale": "Complete records from every supported registry without an abstract document that none is available."}}
        one_registry = copy.deepcopy(payload)
        one_registry["id"], one_registry["source_ids"], one_registry["policy"]["minimum_attempts"] = "one-registry", [sources["openalex"]], 1
        self.assert_error("invalid_availability", lambda: self.mutate(record_availability, one_registry))
        arxiv_version = copy.deepcopy(payload)
        arxiv_version["id"], arxiv_version["version_id"] = "arxiv-absent", a
        self.assert_error("invalid_availability", lambda: self.mutate(record_availability, arxiv_version))
        self.mutate(record_availability, payload)
        self.assertNotIn("abstract_reading_missing", self.codes())
        report = foundation_report(self.store, "research")
        self.assertEqual(report["availability_qualified"][0]["qualification"], "noncritical_only")
        self.assertEqual(report["availability_qualified"][0]["policy"]["allowed_statuses"], [200])
        acquire_work(self.store, "10.1234/example", request_id="cr-present", expected_revision=self.store.revision, http=http)
        self.assertIn("abstract_reading_missing", self.codes())
        present = next(s["id"] for s in self.store.snapshot()["records"]["source"].values() if s["operation_id"] == "cr-present")
        with_abstract = copy.deepcopy(payload)
        with_abstract["id"], with_abstract["source_ids"] = "abstract-present", [sources["openalex"], present]
        self.assert_error("invalid_availability", lambda: self.mutate(record_availability, with_abstract))

    def test_registry_abstract_absence_requires_only_the_registries_that_address_the_work(self):
        from research_harness.acquisition import acquire_work, import_response
        from research_fixtures import client, json_response, openalex
        a = self.metadata(references=[{"id": "openalex:W123"}, {"id": "url:https://inspirehep.net/api/literature/198154"}])
        self.scope([a])
        without_doi = openalex()
        del without_doi["abstract_inverted_index"], without_doi["doi"], without_doi["ids"]["doi"]
        http, _, _ = client([json_response(without_doi)])
        acquire_work(self.store, "W123", request_id="oa-only", expected_revision=self.store.revision, http=http)
        raw = {"hits": [{"links": {"json": "https://inspirehep.net/api/literature/198154"},
                         "metadata": {"titles": [{"title": "Quantum creation of an inflationary universe"}]}}]}
        import_response(self.store, "web", json.dumps(raw).encode(), source_url="https://inspirehep.net/api/literature?q=recid+198154",
                        captured_at="2026-09-09T12:00:00Z", media_type="application/json",
                        mappings=[{"id": "/hits/0/links/json", "title": "/hits/0/metadata/titles/0/title"}],
                        expected_revision=self.store.revision, request_id="inspire-198154")
        self.assertEqual(len([o for o in self.store_obligations() if o["code"] == "abstract_reading_missing"]), 2)
        records = self.store.snapshot()["records"]
        openalex_source = next(s["id"] for s in records["source"].values() if s["provider"] == "openalex")
        web_source = next(s["id"] for s in records["source"].values() if s["provider"] == "web")
        policy = {"id": "registry-abstract-absent", "minimum_attempts": 1, "allowed_statuses": [200],
                  "rationale": "Every registry that addresses the work's identifiers returned a complete record without an abstract."}
        self.mutate(record_availability, {"id": "openalex-only", "profile": "research", "version_id": "openalex:W123", "depth": "abstract",
                                          "source_ids": [openalex_source], "reason": "OpenAlex is the only registry that knows this work.", "policy": policy})
        self.mutate(record_availability, {"id": "inspire-only", "profile": "research", "version_id": "url:https://inspirehep.net/api/literature/198154",
                                          "depth": "abstract", "source_ids": [web_source], "reason": "The saved INSPIRE record carries no abstract and no registry identifier.", "policy": policy})
        self.assertNotIn("abstract_reading_missing", self.codes())
        swapped = {"id": "swapped", "profile": "research", "version_id": "openalex:W123", "depth": "abstract", "source_ids": [web_source],
                   "reason": "A capture of another work.", "policy": policy}
        self.assert_error("invalid_availability", lambda: self.mutate(record_availability, swapped))

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


class SearchDispositionTests(LiteratureCase):
    def search(self, *args, **kwargs):
        return LiteratureTests.search(self, *args, **kwargs)

    def test_dispositions_are_required_complete_and_bound_to_citations(self):
        a = self.metadata()
        self.scope([a])
        search = self.search("direct", ["arxiv:2602.00009v1", "arxiv:2602.00010v1"])
        missing = copy.deepcopy(search)
        del missing["dispositions"]
        self.assert_error("invalid_search", lambda: self.mutate(record_search, missing))
        partial = copy.deepcopy(search)
        partial["dispositions"] = partial["dispositions"][:1]
        self.assert_error("invalid_search", lambda: self.mutate(record_search, partial))
        out = copy.deepcopy(search)
        out["dispositions"][0]["disposition"] = "out_of_scope"
        out["cited_work_ids"] = ["arxiv:2602.00009v1"]
        self.assert_error("invalid_search", lambda: self.mutate(record_search, out))
        out["cited_work_ids"] = ["arxiv:2602.00010v1"]
        self.mutate(record_search, out)
        self.assertNotIn("search_dispositions_missing", self.codes())

    def test_contradictory_findings_are_carried_forward_or_resolved(self):
        a = self.metadata()
        self.scope([a])
        first = self.search("direct", ["arxiv:2602.00009v1", "arxiv:2602.00010v1"])
        first["dispositions"][0]["disposition"] = "contradictory"
        self.mutate(record_search, first)
        second = self.search("direct", ["arxiv:2602.00010v1"])
        second["id"] = "direct-2"
        self.assert_error("search_findings_dropped", lambda: self.mutate(record_search, second))
        second["resolved"] = [{"work_id": "arxiv:2602.00009v1", "reason": "The contradiction concerns a different regime; recorded in the rationale."}]
        self.mutate(record_search, second)
        third = self.search("direct", ["arxiv:2602.00009v1", "arxiv:2602.00010v1"])
        third["id"] = "direct-3"
        third["dispositions"][0]["disposition"] = "unresolved"
        self.mutate(record_search, third)
        fourth = self.search("direct", ["arxiv:2602.00009v1", "arxiv:2602.00010v1"])
        fourth["id"] = "direct-4"
        fourth["dispositions"][0]["disposition"] = "contradictory"
        self.mutate(record_search, fourth)
        dismissed = self.search("direct", ["arxiv:2602.00009v1", "arxiv:2602.00010v1"])
        dismissed["id"] = "direct-5"
        dismissed["dispositions"][0]["disposition"] = "out_of_scope"
        self.assert_error("search_findings_dropped", lambda: self.mutate(record_search, dismissed))

    def test_recording_another_purpose_does_not_stale_a_selected_search(self):
        a = self.metadata()
        self.scope([a])
        self.mutate(record_search, self.search("direct"))
        self.mutate(record_search, self.search("adjacent", ["arxiv:2602.00011v1"]))
        self.assertFalse({"search_frontier_stale", "search_evidence_stale", "search_scope_stale"} & self.codes())

    def test_legacy_search_without_dispositions_is_an_obligation_not_a_crash(self):
        a = self.metadata()
        self.scope([a])
        search = self.search("direct")
        self.mutate(record_search, search)
        records = self.store.snapshot()["records"]
        legacy = dict(records["literature_search"]["direct"])
        del legacy["dispositions"]
        self.store.mutate("legacy", {}, lambda tx: tx.put("literature_search", "direct", legacy),
                          expected_revision=self.store.revision, request_id="legacy-search")
        self.assertIn("search_dispositions_missing", self.codes())

    def test_unrelated_reference_changes_do_not_stale_but_a_new_frontier_family_does(self):
        a = self.metadata(1, references=[{"id": "arxiv:2601.00002v1"}])
        b = self.metadata(2)
        self.scope([a])
        self.mutate(record_search, self.search("direct"))
        self.assertNotIn("search_evidence_stale", self.codes())
        self.capture(b, "The referenced paper's body. References: none.")
        self.assertNotIn("search_evidence_stale", self.codes())
        self.assertNotIn("search_frontier_stale", self.codes())
        self.metadata(1, references=[{"id": "arxiv:2601.00002v1"}, {"id": "arxiv:2601.00003v1"}])
        self.metadata(3)
        self.assertIn("search_frontier_stale", self.codes())

