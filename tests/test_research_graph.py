import copy

from literature_fixtures import LiteratureCase
from research_harness.graph import citation_graph, set_roots
from research_harness.literature import foundation_report, import_bundle
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
        self.assertEqual(tiers, {a[:-2]: 1, b[:-2]: 1, c[:-2]: 2, d[:-2]: 2, e[:-2]: 3})
        self.assertEqual(len(graph["references"]), 8)
        self.assertIn("reference_unresolved", self.codes())
        self.assertIn("bibliography_incomplete", self.codes())
        self.mutate(require_fulltext, {"id": "major", "profile": "research", "version_id": e,
                                     "purpose": "major_claim", "reason": "The main result uses E's assumption."})
        report = foundation_report(self.store, "research")
        item = next(x for x in report["inventory"] if x["version_id"] == e)
        self.assertEqual(item["tier"], 3)
        self.assertEqual(item["required_depth"], "fulltext")

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
