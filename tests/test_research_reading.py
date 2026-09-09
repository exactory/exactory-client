import copy

from literature_fixtures import LiteratureCase
from research_harness.literature import foundation_report, import_bundle
from research_harness.reading import require_fulltext, record_reading, validate_read_evidence


class ReadingTests(LiteratureCase):
    def test_abstract_reading_leaves_fulltext_missing_and_short_absence_notes_are_honest(self):
        a = self.metadata(abstract="This memorial honors the life of an algebraist.")
        self.scope([a])
        self.mutate(record_reading, self.abstract_note(a, absent=True))
        self.assertIn("fulltext_reading_missing", self.codes())
        self.assertEqual(len(self.store.snapshot()["records"]["reading"]), 1)

    def test_bad_locators_partial_abstract_and_cross_version_links_are_rejected(self):
        a, b = self.metadata(), self.metadata(2)
        original = self.abstract_note(a)
        bad = copy.deepcopy(original)
        bad["inspections"][0]["link"]["locator"]["quote"] = "Invented source text"
        self.assert_error("invalid_locator", lambda: self.mutate(record_reading, bad))
        bad = copy.deepcopy(original)
        bad["version_id"] = b
        self.assert_error("source_mismatch", lambda: self.mutate(record_reading, bad))
        bad = copy.deepcopy(original)
        link = bad["inspections"][0]["link"]
        link["locator"] = self.span(link["artifact"], "Source 1")
        result = self.mutate(record_reading, bad)
        self.assertEqual(result["result"]["status"], "partial")

    def test_fulltext_without_included_abstract_does_not_cover_cohort_abstract(self):
        collection = self.cohort((1,))
        a = "arxiv:2601.00001v1"
        self.scope([a], [collection])
        bundle = self.bundle(a, self.capture(a, abstract=False))
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        self.assertNotIn("fulltext_reading_missing", self.codes())
        self.assertIn("cohort_abstract_reading_missing", self.codes())

    def test_different_abstract_in_body_does_not_certify_selected_cohort_abstract(self):
        collection = self.cohort((1,))
        a = "arxiv:2601.00001v1"
        self.scope([a], [collection])
        bundle = self.bundle(a, self.capture(a, abstract="This body contains a different abstract assertion."))
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        self.assertIn("cohort_abstract_reading_missing", self.codes())

    def test_required_visual_and_missing_supplement_units_remain_pending(self):
        a = self.metadata()
        self.scope([a])
        capture = self.capture(a, pdf_text="Article A. Figure 1. References: none.\fNeighbor B.\f")
        bundle = self.bundle(a, capture)
        figure = {"version_id": a, "source_id": capture["source_id"], "artifact": capture["original"],
                  "locator": {"kind": "pdf", "page_index": 0, "printed_page": "230", "region": [0.1, 0.1, 0.5, 0.5]}}
        bundle["units"][0]["link"]["locator"] = self.span(capture["text"], "Article A. Figure 1. References: none.")
        bundle["units"][1]["link"]["locator"] = self.span(capture["text"], "References: none.")
        bundle["units"].extend([
            {"id": "figure1", "kind": "figure", "required": True, "link": figure},
            {"id": "supplement", "kind": "supplement", "required": True, "link": None,
             "reason": "The article requires the unavailable derivation supplement.", "url": "https://example.org/supplement"}])
        self.mutate(import_bundle, bundle)
        result = self.mutate(record_reading, self.full_note(bundle, omit=("figure1",)))
        self.assertEqual(result["result"]["status"], "partial")
        self.assertIn("required_unit_uninspected", self.codes())
        self.assertIn("required_unit_missing", self.codes())
        wrong = self.full_note(bundle, "wrong")
        wrong["inspections"][0]["link"]["locator"] = self.span(capture["text"], "Neighbor B.")
        self.assert_error("outside_reading_unit", lambda: self.mutate(record_reading, wrong))
        wrong = self.full_note(bundle, "wrong-page")
        wrong["inspections"][-1]["link"]["locator"]["page_index"] = 99
        self.assert_error("invalid_locator", lambda: self.mutate(record_reading, wrong))

    def test_new_bundle_required_unit_invalidates_full_depth_without_deleting_reading(self):
        a = self.metadata()
        self.scope([a])
        bundle = self.bundle(a)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        before = foundation_report(self.store, "research")
        later = copy.deepcopy(bundle)
        later["id"] = "later"
        later["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": None,
                              "reason": "A required proof is now identified.", "url": "https://example.org/proof"})
        self.mutate(import_bundle, later)
        after = foundation_report(self.store, "research")
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertIn("fulltext_reading_missing", self.codes())
        self.assertEqual(len(self.store.snapshot()["records"]["reading"]), 1)
        erased = copy.deepcopy(bundle)
        erased["id"] = "erased-supplement"
        self.assert_error("invalid_bundle", lambda: self.mutate(import_bundle, erased))

    def test_verification_pins_version_and_original_body_not_only_the_doi_or_extraction(self):
        a, v2 = self.metadata(), self.metadata(version=2)
        first = self.bundle(a)
        self.mutate(import_bundle, first)
        self.mutate(record_reading, self.full_note(first))
        second = self.bundle(v2, self.capture(v2, "A changed theorem is stated. References: none."), bundle_id="v2")
        self.mutate(import_bundle, second)
        source = self.store.snapshot()["records"]["source"][second["source_id"]]
        target = {"kind": "work", "id": v2, "source_id": source["id"], "sha256": source["response"]["sha256"]}
        self.scope([v2], profile="verification", target=target)
        self.assertIn("fulltext_reading_missing", self.codes("verification"))
        self.mutate(record_reading, self.full_note(second, "read-v2"))
        self.assertNotIn("fulltext_reading_missing", self.codes("verification"))
        evidence = validate_read_evidence(self.store.snapshot()["records"], self.artifacts, second["units"][0]["link"], depth="fulltext")
        self.assertEqual(evidence["reading_id"], "read-v2")

    def test_reading_replay_survives_later_required_bundle_change(self):
        a = self.metadata()
        note = self.abstract_note(a)
        revision = self.store.revision
        first = record_reading(self.store, note, expected_revision=revision, request_id="read")
        self.metadata(version=2)
        self.assertEqual(record_reading(self.store, note, expected_revision=revision, request_id="read"), first)

    def test_identical_original_bytes_can_reuse_reading_through_a_new_capture_pin(self):
        a = self.metadata()
        b = self.metadata(2)
        def cited_bundle(bundle_id):
            capture = self.capture(a, "A uses the authored B lemma. References: B lemma.")
            bundle = self.bundle(a, capture, bundle_id=bundle_id)
            bundle["bibliography"]["entries"] = [{"target": b, "kind": "paper", "reason": "Explicit authored citation to B.",
                                                  "link": self.link(a, capture["source_id"], capture["text"], "References: B lemma.")}]
            return bundle
        first = cited_bundle("first")
        self.mutate(import_bundle, first)
        self.mutate(record_reading, self.full_note(first))
        later = cited_bundle("recaptured")
        self.mutate(import_bundle, later)
        source = self.store.snapshot()["records"]["source"][later["source_id"]]
        target = {"kind": "work", "id": a, "source_id": source["id"], "sha256": source["response"]["sha256"]}
        self.scope([a], profile="verification", target=target)
        missing = [x["version_id"] for x in foundation_report(self.store, "verification")["obligations"] if x["code"] == "fulltext_reading_missing"]
        self.assertNotIn(a, missing)

    def test_different_body_under_same_work_id_cannot_reuse_old_reading(self):
        a = self.metadata()
        first = self.bundle(a)
        self.mutate(import_bundle, first)
        self.mutate(record_reading, self.full_note(first))
        changed = self.bundle(a, self.capture(a, "The claimed theorem has changed. References: none."), bundle_id="changed")
        self.mutate(import_bundle, changed)
        source = self.store.snapshot()["records"]["source"][changed["source_id"]]
        target = {"kind": "work", "id": a, "source_id": source["id"], "sha256": source["response"]["sha256"]}
        self.scope([a], profile="verification", target=target)
        self.assertIn("fulltext_reading_missing", self.codes("verification"))

    def test_new_distinct_body_needs_its_own_bundle_but_the_fixed_target_stays_pinned(self):
        a = self.metadata()
        original = self.bundle(a)
        self.mutate(import_bundle, original)
        self.mutate(record_reading, self.full_note(original))
        self.scope([a])
        self.assertNotIn("fulltext_reading_missing", self.codes())
        original_source = self.store.snapshot()["records"]["source"][original["source_id"]]
        target = {"kind": "work", "id": a, "source_id": original_source["id"], "sha256": original_source["response"]["sha256"]}
        self.scope([a], profile="verification", target=target)
        changed = self.capture(a, "A substantively changed body is now available. References: none.")
        report = foundation_report(self.store, "research")
        missing = [x for x in report["obligations"] if x["code"] == "fulltext_reading_missing"]
        self.assertTrue(missing)
        self.assertIn(changed["original"]["path"], missing[0]["paths"])
        self.assertNotIn("fulltext_reading_missing", self.codes("verification"))
        changed_bundle = self.bundle(a, changed, "changed")
        self.mutate(import_bundle, changed_bundle)
        self.mutate(record_reading, self.full_note(changed_bundle, "changed-read"))
        self.assertNotIn("fulltext_reading_missing", self.codes())

    def test_typed_errors_for_invalid_source_links_leave_store_unchanged(self):
        a = self.metadata()
        original = self.abstract_note(a)
        for field, value in (("source_id", []), ("locator", None), ("artifact", [])):
            note = copy.deepcopy(original)
            note["inspections"][0]["link"][field] = value
            before = self.store.snapshot()
            from research_harness.errors import ResearchError
            with self.assertRaises(ResearchError):
                self.mutate(record_reading, note)
            self.assertEqual(before, self.store.snapshot())
        from research_harness.errors import ResearchError
        from research_harness.source_links import read_locator
        with self.subTest(boundary="request_id"):
            with self.assertRaises(ResearchError):
                record_reading(self.store, original, expected_revision=self.store.revision, request_id=[])
        document = self.artifacts.put(b'{"items": [{"count": 1}]}', "application/json")
        with self.subTest(boundary="nested_json_types"):
            self.assert_error("invalid_locator", lambda: read_locator(self.artifacts, document,
                {"kind": "json", "pointer": "/items", "value": [{"count": True}]}))
        html = self.artifacts.put(b'<figure id="f1">Authored figure</figure>', "text/html")
        with self.subTest(boundary="html_anchor"):
            self.assert_error("invalid_locator", lambda: read_locator(self.artifacts, html, {"kind": "html", "anchor": None}))

    def test_inventory_exposes_actual_full_reading_even_when_only_abstract_is_required(self):
        a, b, c = "arxiv:2601.00001v1", "arxiv:2601.00002v1", "arxiv:2601.00003v1"
        self.metadata(1, references=[{"id": b}])
        self.metadata(2, references=[{"id": c}])
        self.metadata(3)
        self.scope([a])
        # B is selected for full reading, so its reference C enters the network at abstract depth.
        self.mutate(require_fulltext, {"id": "selected-b", "profile": "research", "version_id": b,
                                     "purpose": "major_claim", "reason": "The claim rests on B."})
        bundle = self.bundle(c)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        item = next(x for x in foundation_report(self.store, "research")["inventory"] if x["version_id"] == c)
        self.assertEqual(item["required_depth"], "abstract")
        self.assertTrue(item["fulltext_read"])

    def test_captured_metadata_or_extraction_cannot_be_the_original_target_pin(self):
        a = self.metadata()
        capture = self.capture(a)
        for source_id, sha in ((capture["source_id"], capture["text"]["sha256"]),
                               (self.store.snapshot()["records"]["work"][a]["source_ids"][0],
                                self.store.snapshot()["records"]["work"][a]["abstract"]["sha256"])):
            self.assert_error("invalid_target", lambda: self.scope([a], profile="verification", target={
                "kind": "work", "id": a, "source_id": source_id, "sha256": sha}))
