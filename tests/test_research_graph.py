import copy

from literature_fixtures import LiteratureCase
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.graph import citation_graph, set_roots
from research_harness.literature import foundation_report, import_bundle, record_search
from research_harness.reading import require_fulltext


class GraphTests(LiteratureCase):
    def test_minimum_family_tier_cycles_and_every_reference_occurrence(self):
        a, b, c, d, e = ["arxiv:2601.%05dv1" % n for n in range(1, 6)]
        self.metadata(1, references=[{"id": c}, {"id": c}, {"unstructured": "Unresolved archive item"}])
        self.metadata(2, references=[{"id": c}, {"id": d}])
        self.metadata(3, references=[{"id": a}, {"id": d}, {"id": e}])
        self.metadata(4)
        self.metadata(5)
        self.scope([a, b])
        graph = citation_graph(self.store.snapshot()["records"], "research")
        tiers = {node["work_id"]: node["tier"] for node in graph["nodes"]}
        # References of a root are Tier 3 (abstract) until the author selects them.
        self.assertEqual(tiers, {a[:-2]: 1, b[:-2]: 1, c[:-2]: 3, d[:-2]: 3})
        self.assertEqual(len(graph["references"]), 5)
        self.assertIn("reference_unresolved", self.codes())
        self.assertIn("bibliography_incomplete", self.codes())
        self.assertNotIn(c, [x["version_id"] for x in self.store_obligations() if x["code"] == "bibliography_incomplete"])
        # Selecting C with require-fulltext makes it Tier 2: read in full, bibliography expanded, its references Tier 3.
        self.mutate(require_fulltext, {"id": "selected-c", "profile": "research", "version_id": c,
                                     "purpose": "major_claim", "reason": "The transfer rests on C's mechanism."})
        graph = citation_graph(self.store.snapshot()["records"], "research")
        tiers = {node["work_id"]: node["tier"] for node in graph["nodes"]}
        self.assertEqual(tiers, {a[:-2]: 1, b[:-2]: 1, c[:-2]: 2, d[:-2]: 3, e[:-2]: 3})
        self.assertEqual(len(graph["references"]), 8)
        self.assertIn(c, [x["version_id"] for x in self.store_obligations() if x["code"] == "bibliography_incomplete"])
        self.mutate(require_fulltext, {"id": "major", "profile": "research", "version_id": e,
                                     "purpose": "major_claim", "reason": "The main result uses E's assumption."})
        report = foundation_report(self.store, "research")
        item = next(x for x in report["inventory"] if x["version_id"] == e)
        self.assertEqual(item["tier"], 2)
        self.assertEqual(item["required_depth"], "fulltext")

    def test_a_replaced_search_judgment_stops_selecting_the_references_only_it_cited(self):
        a, b, c = ["arxiv:2601.%05dv1" % n for n in (1, 2, 3)]
        self.metadata(1, references=[{"id": b}, {"id": c}])
        self.metadata(2)
        self.metadata(3)
        self.scope([a])

        def read_tiers():
            return {node["work_id"]: node["tier"] for node in citation_graph(self.store.snapshot()["records"], "research")["nodes"]}

        first = self.capture_search("direct", [b, c])
        first["cited_work_ids"] = [b, c]
        self.mutate(record_search, first)
        self.assertEqual(read_tiers(), {a[:-2]: 1, b[:-2]: 2, c[:-2]: 2})
        second = self.capture_search("direct", [b, c])
        second["id"], second["cited_work_ids"] = "direct-2", [b]
        self.mutate(record_search, second)
        self.assertEqual(read_tiers(), {a[:-2]: 1, b[:-2]: 2, c[:-2]: 3})

    def test_a_target_whose_archive_lost_its_hyphen_is_refused_with_its_canonical_form(self):
        a = self.metadata()
        capture = self.capture(a, "A extends an old result. References: arXiv:astroph/0410063.")
        bundle = self.bundle(a, capture)
        bundle["bibliography"]["entries"] = [{"target": "arxiv:astroph/0410063", "kind": "paper",
            "link": dict(bundle["units"][1]["link"], locator=self.span(capture["text"], "arXiv:astroph/0410063")),
            "reason": "The entry prints an old arXiv identifier whose archive lost its hyphen."}]
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as raised:
            self.mutate(import_bundle, bundle)
        self.assertEqual(raised.exception.code, "invalid_bibliography")
        self.assertEqual(raised.exception.details, {"target": "arxiv:astroph/0410063", "canonical": "arxiv:astro-ph/0410063"})
        self.assertEqual(self.store.snapshot(), before)
        bundle["bibliography"]["entries"][0]["target"] = "arxiv:astro-ph/0410063"
        occurrence_id = self.mutate(import_bundle, bundle)["result"]["occurrence_ids"][0]
        self.assertEqual(self.store.snapshot()["records"]["reference_occurrence"][occurrence_id]["target"], "arxiv:astro-ph/0410063")

    def test_article_bibliography_expansion_preserves_repeated_unresolved_and_nonpaper(self):
        a, b = self.metadata(), self.metadata(2)
        self.scope([a])
        capture = self.capture(a, "A uses B. References: B; B; unresolved manuscript; laboratory notebook.")
        bundle = self.bundle(a, capture)
        base = bundle["units"][1]["link"]
        bundle["bibliography"]["entries"] = [
            {"target": b, "kind": "paper", "link": dict(base, locator=self.span(capture["text"], "B; B")), "reason": "Explicit B identifier."},
            {"target": b, "kind": "paper", "link": dict(base, locator=self.span(capture["text"], "B; unresolved")), "reason": "Repeated B citation."},
            {"target": None, "kind": "unknown", "link": dict(base, locator=self.span(capture["text"], "unresolved manuscript")), "reason": "Identity not established."},
            {"target": None, "kind": "nonpaper", "link": dict(base, locator=self.span(capture["text"], "laboratory notebook")), "reason": "The entry explicitly names a laboratory notebook."}]
        result = self.mutate(import_bundle, bundle)
        records = self.store.snapshot()["records"]
        occurrences = records["reference_occurrence"]
        self.assertEqual(len(occurrences), 4)
        self.assertEqual(len(result["result"]["occurrence_ids"]), 4)
        graph = citation_graph(records, "research")
        self.assertEqual(len(graph["references"]), 4)
        self.assertEqual(sum(x["code"] == "reference_unresolved" for x in graph["obligations"]), 1)
        self.assertNotIn(a, [x["version_id"] for x in graph["obligations"] if x["code"] == "bibliography_incomplete"])

    def test_a_rebinding_bundle_retires_the_occurrences_of_the_bundle_it_replaces(self):
        a = self.metadata(1, references=[{"unstructured": "A registry observation"}])
        b = self.metadata(2)
        self.scope([a])
        registry = set(self.store.snapshot()["records"]["reference_occurrence"])
        capture = self.capture(a, "A uses B. References: B, a preprint.")
        first = self.bundle(a, capture, "bundle-1")
        entry = {"target": None, "kind": "unknown", "reason": "The entry does not establish an identity.",
                 "link": dict(first["units"][1]["link"], locator=self.span(capture["text"], "B, a preprint"))}
        first["bibliography"]["entries"] = [entry]
        self.mutate(import_bundle, first)
        second = self.bundle(a, capture, "bundle-2")
        second["bibliography"]["entries"] = [dict(entry, target=b, kind="paper", reason="The entry names the acquired B.")]
        self.mutate(import_bundle, second)
        revised_capture = self.capture(a, "A uses B in the revised text. References: B, a revised preprint.")
        third = self.bundle(a, revised_capture, "bundle-3")
        revised_locator = self.span(revised_capture["text"], "B, a revised preprint")
        third["bibliography"]["entries"] = [{"target": b, "kind": "paper", "reason": "The revised entry names the acquired B.",
                                             "link": dict(third["units"][1]["link"], locator=revised_locator)}]
        self.mutate(import_bundle, third)
        records = self.store.snapshot()["records"]
        bundles = records["source_bundle"]
        graph = citation_graph(records, "research")
        # The second bundle replaced the first for the same original; the third is the bundle of another original.
        self.assertEqual({r["occurrence_id"] for r in graph["references"]},
                         set(bundles["bundle-2"]["occurrence_ids"] + bundles["bundle-3"]["occurrence_ids"]) | registry)
        self.assertNotIn(bundles["bundle-1"]["occurrence_ids"][0], {o.get("reference_id") for o in graph["obligations"]})

    def test_a_study_with_one_bundle_per_version_keeps_its_graph(self):
        """The expected digest was computed before the graph read only the occurrences of selected bundles."""
        a, b, c = ["arxiv:2601.%05dv1" % n for n in (1, 2, 3)]
        self.metadata(1, references=[{"unstructured": "Authored B lemma"}, {"id": c}])
        self.metadata(2)
        self.metadata(3)
        self.scope([a])
        self.mutate(require_fulltext, {"id": "selected-c", "profile": "research", "version_id": c,
                                       "purpose": "major_claim", "reason": "The transfer rests on C's mechanism."})
        observed_occurrence = next(o for o in self.store.snapshot()["records"]["reference_occurrence"].values()
                                   if o["target"] is None)
        capture = self.capture(a, "The proof uses B and C. References: Authored B lemma; Paper C; laboratory notebook.")
        bundle = self.bundle(a, capture, "bundle-a")
        base = bundle["units"][1]["link"]
        entries = [{"target": b, "kind": "paper", "reason": "The article identifies the acquired B lemma.",
                    "link": dict(base, locator=self.span(capture["text"], "Authored B lemma"))},
                   {"target": c, "kind": "paper", "reason": "The entry names the acquired C.",
                    "link": dict(base, locator=self.span(capture["text"], "Paper C"))},
                   {"target": None, "kind": "nonpaper", "reason": "The entry names a laboratory notebook.",
                    "link": dict(base, locator=self.span(capture["text"], "laboratory notebook"))}]
        bundle["bibliography"]["entries"] = entries
        bundle["resolutions"] = [dict(entries[0], reference_id=observed_occurrence["id"])]
        self.mutate(import_bundle, bundle)
        capture_c = self.capture(c, "C extends earlier work. References: an unnamed manuscript.")
        bundle_c = self.bundle(c, capture_c, "bundle-c")
        bundle_c["bibliography"]["entries"] = [{"target": None, "kind": "unknown", "reason": "The entry names no identifier.",
                                                "link": dict(bundle_c["units"][1]["link"], locator=self.span(capture_c["text"], "an unnamed manuscript"))}]
        self.mutate(import_bundle, bundle_c)
        self.assertEqual(digest(citation_graph(self.store.snapshot()["records"], "research")),
                         "5327c853eed2e6163dcc878a56617fe8ef3a3ceba564dcbf8dec5edb1193a32b")

    def build_bundle_with_unknown_entry(self, version, capture, bundle_id, quote):
        """An article bundle whose one bibliography entry, the quoted text, names no identifier."""
        bundle = self.bundle(version, capture, bundle_id)
        bundle["bibliography"]["entries"] = [{"target": None, "kind": "unknown", "reason": "The entry names no identifier.",
                                              "link": dict(bundle["units"][1]["link"], locator=self.span(capture["text"], quote))}]
        return bundle

    def test_a_verification_target_reads_the_bundle_of_each_original_whatever_its_pin(self):
        t = self.metadata(1)
        first = self.capture(t, "T body one. References: first original entry.")
        second = self.capture(t, "T body two. References: second original entry.")
        # The second original's bundle is imported first, so the version's latest bundle belongs to the first original.
        self.mutate(import_bundle, self.build_bundle_with_unknown_entry(t, second, "bundle-2", "second original entry"))
        self.mutate(import_bundle, self.build_bundle_with_unknown_entry(t, first, "bundle-1", "first original entry"))
        records = self.store.snapshot()["records"]
        occurrences = set(records["reference_occurrence"])
        originals = {c["source_id"]: c["original"]["sha256"] for c in records["work"][t]["fulltexts"]}
        for source_id in (first["source_id"], None, second["source_id"]):
            self.scope([t], profile="verification", target={"kind": "work", "id": t, "source_id": source_id, "sha256": originals.get(source_id)})
            graph = citation_graph(self.store.snapshot()["records"], "verification")
            self.assertEqual({r["occurrence_id"] for r in graph["references"]}, occurrences, source_id)
            self.assertEqual({o["reference_id"] for o in graph["obligations"] if o["code"] == "reference_unresolved"}, occurrences, source_id)

    def build_passage_bundle(self, version, bundle_id):
        """A partial passage bundle on the version's saved tool response; its one bibliography entry names no identifier."""
        records = self.store.snapshot()["records"]
        source_id = next(s for s in records["work"][version]["source_ids"] if records["source"][s]["provider"] == "mcp")

        def build_link(pointer, value):
            return {"version_id": version, "source_id": source_id, "artifact": records["source"][source_id]["response"],
                    "locator": {"kind": "json", "pointer": pointer, "value": value}}

        body = build_link("/title", "Authored references")
        bibliography = build_link("/references/0/unstructured", "A tool-reported reference")
        return {"id": bundle_id, "version_id": version, "source_id": source_id, "scope": "passage", "completeness": "partial",
                "units": [{"id": "passage", "kind": "text", "required": True, "link": body},
                          {"id": "bibliography", "kind": "bibliography", "required": True, "link": bibliography}],
                "inventory": {"text": "A passage of the article in a saved tool response.", "links": [body]},
                "bibliography": {"complete": False, "unit_id": "bibliography",
                                 "entries": [{"target": None, "kind": "unknown", "reason": "The tool entry names no identifier.",
                                              "link": bibliography}]},
                "resolutions": []}

    def test_a_passage_bundle_is_read_only_while_no_acquired_original_has_a_bundle_in_either_import_order(self):
        roots = [self.metadata(n, references=[{"unstructured": "A tool-reported reference"}]) for n in (1, 2, 3)]
        passage_first, article_first, passage_only = roots
        captures = {version: self.capture(version, "The article body. References: an article reference.") for version in roots}
        # Each version has an available original; the last one has only a passage bundle.
        for bundle in (self.build_passage_bundle(passage_first, "passage-1"),
                       self.build_bundle_with_unknown_entry(passage_first, captures[passage_first], "article-1",
                                                            "an article reference"),
                       self.build_bundle_with_unknown_entry(article_first, captures[article_first], "article-2",
                                                            "an article reference"),
                       self.build_passage_bundle(article_first, "passage-2"),
                       self.build_passage_bundle(passage_only, "passage-3")):
            self.mutate(import_bundle, bundle)
        self.scope(roots)
        records = self.store.snapshot()["records"]
        graph = citation_graph(records, "research")
        # The article bundles supersede the passages imported before and after them; the passage-only version reads its passage.
        superseded_occurrence_ids = {o for b in ("passage-1", "passage-2")
                                     for o in records["source_bundle"][b]["occurrence_ids"]}
        self.assertEqual({r["occurrence_id"] for r in graph["references"]},
                         set(records["reference_occurrence"]) - superseded_occurrence_ids)
        self.assertFalse(superseded_occurrence_ids & {o.get("reference_id") for o in graph["obligations"]})
        self.assertTrue(set(records["source_bundle"]["passage-3"]["occurrence_ids"]) <= {o.get("reference_id") for o in graph["obligations"]})

    def test_an_original_that_a_bundle_links_as_a_supplement_is_read_only_as_another_body_in_either_import_order(self):
        roots = [self.metadata(n) for n in (1, 2)]
        bundles = []
        for number, root in enumerate(roots, 1):
            article_capture = self.capture(root, "The article body. References: an article reference.")
            supplement_capture = self.capture(root, "The supplement body. References: a supplement reference.")
            main_bundle = self.build_bundle_with_unknown_entry(root, article_capture, "main-%d" % number,
                                                               "an article reference")
            supplement_unit = {"id": "supplement", "kind": "supplement", "required": True,
                               "link": self.link(root, supplement_capture["source_id"], supplement_capture["text"])}
            main_bundle["units"].append(supplement_unit)
            supplement_bundle = self.build_bundle_with_unknown_entry(root, supplement_capture, "supplement-%d" % number,
                                                                     "a supplement reference")
            # The first version imports its main bundle first, the second version the supplement's own bundle.
            bundles += [main_bundle, supplement_bundle] if number == 1 else [supplement_bundle, main_bundle]
        for bundle in bundles:
            self.mutate(import_bundle, bundle)
        self.scope(roots)
        records = self.store.snapshot()["records"]
        graph = citation_graph(records, "research")
        # Each main bundle links the other original as a supplement, so that original is another body of its version
        # and its own bundle is not read beside the main bundle.
        read_bundle_ids = {records["reference_occurrence"][r["occurrence_id"]].get("bundle_id") for r in graph["references"]}
        self.assertEqual(read_bundle_ids - {None}, {"main-1", "main-2"})
        supplement_occurrence_ids = {occurrence_id for bundle_id in ("supplement-1", "supplement-2")
                                     for occurrence_id in records["source_bundle"][bundle_id]["occurrence_ids"]}
        self.assertFalse(supplement_occurrence_ids & {o.get("reference_id") for o in graph["obligations"]})

    def test_shorter_registry_observation_does_not_prove_or_replace_bibliography(self):
        a = self.metadata(references=[{"unstructured": "Observed item " + str(i)} for i in range(6)])
        self.metadata(references=[{"unstructured": "Observed item " + str(i)} for i in range(5)])
        self.scope([a])
        graph = citation_graph(self.store.snapshot()["records"], "research")
        self.assertEqual(len(graph["references"]), 11)
        self.assertIn("bibliography_incomplete", self.codes())

    def test_roots_are_exact_unique_families_and_verifier_keeps_target(self):
        a, v2 = self.metadata(), self.metadata(version=2)
        self.assert_error("invalid_roots", lambda: self.scope([a, v2]))
        self.assert_error("invalid_roots", lambda: self.scope([a[:-2]]))
        target = {"kind": "work", "id": v2, "source_id": None, "sha256": None}
        self.assert_error("invalid_target", lambda: self.scope([a], profile="verification", target=target))
        self.scope([v2], profile="verification", target=target)
        self.assertIn("target_source_pin_missing", self.codes("verification"))

    def test_root_mutation_replay_and_compare_and_swap(self):
        a, b = self.metadata(), self.metadata(2)
        payload = {"profile": "research", "roots": [a], "collection_ids": []}
        revision = self.store.revision
        first = set_roots(self.store, payload, expected_revision=revision, request_id="roots")
        self.scope([b])
        self.assertEqual(set_roots(self.store, payload, expected_revision=revision, request_id="roots"), first)
        self.assert_error("stale_revision", lambda: set_roots(self.store, payload, expected_revision=revision, request_id="new"))
        changed = copy.deepcopy(payload)
        changed["roots"] = [b]
        self.assert_error("request_id_conflict", lambda: set_roots(self.store, changed, expected_revision=revision, request_id="roots"))

    def test_evidence_linked_resolution_keeps_original_unresolved_occurrence(self):
        a = self.metadata(references=[{"unstructured": "Authored B lemma"}])
        b = self.metadata(2)
        self.scope([a])
        original = next(iter(self.store.snapshot()["records"]["reference_occurrence"].values()))
        bundle = self.bundle(a, self.capture(a, "The proof uses B. References: Authored B lemma."))
        evidence = {"target": b, "kind": "paper", "reason": "The article identifies this citation as the acquired B lemma.",
                    "link": self.link(a, bundle["source_id"], bundle["units"][0]["link"]["artifact"], "Authored B lemma.")}
        bundle["bibliography"]["entries"] = [evidence]
        bundle["resolutions"] = [dict(evidence, reference_id=original["id"])]
        self.mutate(import_bundle, bundle)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["reference_occurrence"][original["id"]], original)
        self.assertEqual(len(citation_graph(records, "research")["references"]), 2)
        self.assertNotIn("reference_unresolved", self.codes())
