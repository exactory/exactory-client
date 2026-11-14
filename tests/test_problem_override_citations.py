"""Override exports cite complete readings behind the current statement's searches."""

import copy
import json
import shutil
from pathlib import Path

from research_harness import literature, problem_claims, problem_delivery
from research_harness.evidence import digest
from research_harness.output_views import render_all
from research_harness.reading import record_reading
from literature_fixtures import LiteratureCase
from test_gate_override import OverrideCommandMixin
from test_research_problem_claims import ProblemCase


class OverrideCitationTests(OverrideCommandMixin, ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement(neighbours=["A neighbouring finite bound"])
        self.write_instruction()
        self.items = []

    def add_reading(self, number, version=1, note_id=None):
        # Acquire each independent source through the public APIs before merging
        # its records. This keeps the 60-work fixture linear in acquisition cost.
        fixture = LiteratureCase()
        fixture.setUp()
        fixture.sequence = number * 100 + version * 10
        try:
            identifier = fixture.metadata(number, version)
            bundle = fixture.bundle(identifier, bundle_id="bundle-%d-v%d" % (number, version))
            fixture.mutate(literature.import_bundle, bundle)
            note = fixture.full_note(bundle, note_id or "read-%03d-v%d" % (number, version))
            fixture.mutate(record_reading, note)
            incoming = fixture.store.snapshot()["records"]
            for source in (fixture.root / "research/sources/objects").rglob("*"):
                if source.is_file():
                    destination = self.root / source.relative_to(fixture.root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
            self.sequence += 1
            def merge_records(tx):
                for kind, rows in incoming.items():
                    for key, value in rows.items():
                        if kind == "work_family":
                            previous = tx.get(kind, key)
                            if previous is not None:
                                value = dict(value, version_ids=sorted(set(value["version_ids"] + previous["version_ids"])))
                        tx.put(kind, key, value)
            self.store.mutate("test.merge_reading", {"version_id": identifier}, merge_records,
                              expected_revision=self.store.revision, request_id="merge-" + str(self.sequence))
        finally:
            fixture.doCleanups()
        item = dict(kind="reading", version_id=identifier, reading_id=note["id"])
        self.items.append(item)
        return item

    def add_registry_reading(self, note_id, authors, version="doi:10.48550/arxiv.2601.00001"):
        from research_harness.acquisition import import_response
        raw = {"id": version, "title": "A registry paper", "authors": authors, "abstract": "An authored abstract."}
        self.sequence += 1
        import_response(self.store, "mcp", json.dumps(raw).encode(), source_url="https://example.org/metadata",
                        captured_at="2026-09-07T12:00:00Z", media_type="application/json",
                        mappings=[{key: "/" + key for key in raw}], expected_revision=self.store.revision,
                        request_id="registry-" + str(self.sequence))
        bundle = self.bundle(version, capture=self.capture(version, abstract=raw["abstract"]), bundle_id="registry-bundle")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, note_id))
        item = dict(kind="reading", version_id=version, reading_id=note_id)
        self.items.append(item)
        return item

    def record_value(self, items=None):
        self.mutate(problem_claims.record_value, self.build_value(items or self.items[:1]))
        self.record_override(self.build_override(gate="post"))

    def record_search(self, identifier, works, disposition="relevant", neighbour=False,
                      captured_at="2026-09-07T12:00:00Z", question=None):
        payload = self.capture_search(found=works)
        payload.update(id=identifier, captured_at=captured_at, hits_per_query=max(10, len(works)),
                       question=question or (self.statement["neighbours"][0] if neighbour else self.statement["statement"]))
        for row in payload["dispositions"]:
            row["disposition"] = disposition
        self.mutate(literature.record_search, payload)

    def export_citations(self, spec=None):
        arguments = []
        if spec is not None:
            path = self.root / "citation-spec.json"
            path.write_text(json.dumps(spec))
            arguments = ["--spec", str(path)]
        response = self.export_problem("problem-override", *arguments)
        self.assertEqual(response.returncode, 0, response.stderr)
        manifest = json.loads(response.stdout)
        directory = Path(manifest["destination"])
        return manifest, directory, json.loads((directory / "citations.json").read_text())

    def build_incident(self, value_count, total_readings):
        for number in range(1, total_readings + 1):
            self.add_reading(number)
        self.record_value(self.items[:value_count])
        self.record_search("statement-search", [item["version_id"] for item in self.items[:29]])

    def test_four_value_citations_expand_to_29_of_40_full_readings(self):
        # Source: 20261113074500-post-on-user-order-instruction-condition-export-and-chain-stop, T4.
        self.build_incident(4, 40)
        records = self.store.snapshot()["records"]
        before = problem_claims.compute_problem_evidence_digest(records, self.artifacts)
        manifest, directory, citations = self.export_citations()
        self.assertEqual(len(citations), 29)
        self.assertEqual([row["locator"] for row in citations], ["2601.%05dv1" % n for n in range(1, 30)])
        self.assertEqual(manifest["citation_counts"], dict(value=4, attempt=0, self_assessment=0, openness=0,
                                                          searches=29, total=29, included=29, omitted=0))
        self.assertEqual(manifest["uncited_relevant_works"], [])
        self.assertEqual(len(json.loads((directory / "citation-refs.json").read_text())), 29)
        current_records = self.store.snapshot()["records"]
        self.assertEqual(problem_claims.compute_problem_evidence_digest(current_records, self.artifacts), before)
        self.assertEqual(len(problem_delivery.project_citations(current_records, self.artifacts)), 4)

    def test_six_value_citations_expand_to_the_29_read_search_works(self):
        self.build_incident(6, 29)
        manifest, _, citations = self.export_citations()
        self.assertEqual(len(citations), 29)
        self.assertEqual(manifest["citation_counts"]["value"], 6)
        self.assertEqual(manifest["citation_counts"]["searches"], 29)

    def test_60_read_works_export_50_repeatably_and_first_can_promote_an_omitted_work(self):
        for number in range(1, 61):
            self.add_reading(number)
        self.record_value()
        self.record_search("all-works", [item["version_id"] for item in reversed(self.items)])
        manifest, directory, citations = self.export_citations()
        self.assertEqual(len(citations), 50)
        self.assertEqual([row["locator"] for row in citations], ["2601.%05dv1" % n for n in range(1, 51)])
        omitted = json.loads((directory / "citations-omitted.json").read_text())
        self.assertEqual([row["locator"] for row in omitted], ["2601.%05dv1" % n for n in range(51, 61)])
        self.assertTrue(all(set(row) == {"citation", "locator"} for row in omitted))
        self.assertEqual(manifest["citation_counts"]["total"], 60)
        self.assertEqual(manifest["citation_counts"]["included"], 50)
        self.assertEqual(manifest["citation_counts"]["omitted"], 10)
        repeated, repeated_dir, repeated_citations = self.export_citations()
        self.assertEqual(repeated, manifest)
        self.assertEqual(repeated_dir, directory)
        self.assertEqual(repeated_citations, citations)
        promoted, promoted_dir, promoted_citations = self.export_citations({"citations": {"first": ["arxiv:2601.00060"]}})
        self.assertEqual(promoted_citations[0]["locator"], "2601.00060v1")
        self.assertEqual(len(promoted_citations), 50)
        references = json.loads((promoted_dir / "citation-refs.json").read_text())
        self.assertEqual([row["bibliography"]["arxivId"] for row in references],
                         [row["locator"] for row in promoted_citations])
        from test_draft import _load_bin_module
        poster = _load_bin_module("exactory", "override_citation_poster")
        self.assertEqual(poster._read_citations_file(str(promoted_dir / "citations.json")), promoted_citations)
        render_all(self.store)
        self.assertTrue(promoted_dir.is_dir())
        self.assertTrue(directory.is_dir())
        self.assertEqual(promoted["schema"], "exactory-problem-export-v3")

    def test_search_order_uses_disposition_formulation_time_then_work_id(self):
        for number in range(1, 8):
            self.add_reading(number)
        self.record_value([self.items[6]])
        self.record_search("neighbour", [self.items[0]["version_id"]], neighbour=True)
        self.record_search("late-exact", [self.items[1]["version_id"]], captured_at="2026-09-09T12:00:00Z")
        self.record_search("early-exact", [self.items[3]["version_id"], self.items[2]["version_id"]])
        self.record_search("contradiction", [self.items[4]["version_id"]], "contradictory", neighbour=True,
                           captured_at="2026-09-10T12:00:00Z")
        self.record_search("other-question", [self.items[5]["version_id"]], question="An unrelated statement")
        _, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], [
            "2601.00007v1", "2601.00005v1", "2601.00003v1", "2601.00004v1", "2601.00002v1", "2601.00001v1"])

    def test_contradictory_works_use_earliest_capture_across_formulations(self):
        for number in range(1, 6):
            self.add_reading(number)
        self.record_value([self.items[4]])
        self.record_search("early-neighbour", [self.items[0]["version_id"], self.items[2]["version_id"]],
                           "contradictory", neighbour=True)
        self.record_search("later-exact", [self.items[1]["version_id"]], "contradictory",
                           captured_at="2026-09-09T12:00:00Z")
        self.record_search("middle-exact", [self.items[3]["version_id"]], "contradictory",
                           captured_at="2026-09-08T12:00:00Z")
        self.record_search("repeated-exact", [self.items[2]["version_id"]], "contradictory",
                           captured_at="2026-09-10T12:00:00Z")
        _, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], [
            "2601.00005v1", "2601.00001v1", "2601.00003v1", "2601.00004v1", "2601.00002v1"])

    def test_counts_keep_value_attempt_assessment_and_search_origins_when_they_overlap(self):
        from research_harness import problem_attempt
        from test_research_problem_assessment import build_assessment
        from test_research_problem_attempt import build_attempt
        for number in range(1, 5):
            self.add_reading(number)
        self.record_value([self.items[2]])
        self.mutate(problem_attempt.record_attempt, build_attempt(reading=self.items[0]))
        assessment = build_assessment(self.statement)
        reading = self.store.snapshot()["records"]["reading"][self.items[1]["reading_id"]]
        assessment["items"]["A1"]["evidence"] = [{"kind": "source", "link": reading["inspections"][0]["link"]}]
        self.mutate(problem_claims.record_assessment, assessment)
        self.record_search("overlapping", [self.items[0]["version_id"], self.items[3]["version_id"]])
        manifest, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], ["2601.00001v1", "2601.00002v1", "2601.00003v1", "2601.00004v1"])
        self.assertEqual(manifest["citation_counts"], dict(value=1, attempt=1, self_assessment=1, openness=0,
                                                          searches=2, total=4, included=4, omitted=0))

    def test_a_work_without_a_complete_full_reading_is_reported_and_not_cited(self):
        self.add_reading(1)
        abstract = self.metadata(2)
        self.mutate(record_reading, self.abstract_note(abstract, "abstract-only"))
        partial = self.metadata(3)
        bundle = self.bundle(partial, bundle_id="partial-bundle")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "partial-full", omit=("bibliography",)))
        self.record_value()
        self.record_search("search", [abstract, partial, self.items[0]["version_id"]])
        manifest, _, citations = self.export_citations()
        self.assertEqual(len(citations), 1)
        self.assertEqual([row["work_id"] for row in manifest["uncited_relevant_works"]], [abstract, partial])
        self.assertTrue(all(row["search_ids"] == ["search"] for row in manifest["uncited_relevant_works"]))

    def test_a_fully_read_search_work_without_a_citation_locator_refuses_before_writing(self):
        self.add_reading(1)
        work = self.add_registry_reading("read-url", ["A. Researcher"], version="url:https://example.org/paper")
        self.record_value([self.items[0]])
        self.record_search("url-work", [work["version_id"]])
        before = self.store.snapshot()
        response = self.export_problem("problem-override")
        self.assertNotEqual(response.returncode, 0, response.stdout)
        self.assertEqual(json.loads(response.stderr.splitlines()[0])["error"]["code"], "problem_citation_locator_missing")
        self.assertEqual(self.store.snapshot(), before)
        self.assertFalse((self.root / "final/problem/exports").exists())

    def test_versionless_search_work_uses_its_read_version_and_spec_can_select_v2(self):
        first = self.add_reading(1)
        self.add_reading(1, version=2)
        second = self.add_reading(2)
        records = self.store.snapshot()["records"]
        family = "arxiv:2601.00001"
        self.put("work", family, dict(records["work"][first["version_id"]], id=family, fulltexts=[]))
        self.record_value([second])
        self.record_search("family", [family])
        _, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], ["2601.00002v1", "2601.00001v1"])
        manifest, _, citations = self.export_citations({"citations": {
            "versions": [{"work_id": family, "version_id": "arxiv:2601.00001v2"}], "first": [family]}})
        self.assertEqual([row["locator"] for row in citations], ["2601.00001v2", "2601.00002v1"])
        self.assertEqual(manifest["citation_counts"]["total"], 2)
        self.assertEqual(manifest["uncited_relevant_works"], [])

    def test_two_read_versions_of_a_cited_family_use_one_citation_and_spec_selects_v2(self):
        self.add_reading(1)
        self.add_reading(1, version=2)
        self.record_value(self.items)
        _, _, citations = self.export_citations({"citations": {"versions": [
            {"work_id": "arxiv:2601.00001", "version_id": "arxiv:2601.00001v2"}]}})
        self.assertEqual([row["locator"] for row in citations], ["2601.00001v2"])

    def test_override_excludes_an_assessment_reading_without_current_fulltext_coverage(self):
        from test_research_problem_assessment import build_assessment
        self.add_reading(1)
        second = self.add_reading(2)
        version = second["version_id"]
        self.record_value()
        assessment = build_assessment(self.statement)
        note = self.store.snapshot()["records"]["reading"][second["reading_id"]]
        assessment["items"]["A1"]["evidence"] = [{"kind": "source", "link": note["inspections"][0]["link"]}]
        self.mutate(problem_claims.record_assessment, assessment)
        self.capture(version, body="A further main text needs its own reading. References: none.")
        self.record_search("partial-assessment-search", [version])
        manifest, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], ["2601.00001v1"])
        self.assertEqual(manifest["citation_counts"]["self_assessment"], 0)
        self.assertEqual(manifest["uncited_relevant_works"], [
            {"work_id": version, "search_ids": ["partial-assessment-search"]}])

    def test_omitting_an_unread_assessment_work_does_not_require_its_citation_locator(self):
        from test_research_problem_assessment import build_assessment
        self.add_reading(1)
        second = self.add_registry_reading("read-url", ["A. Researcher"], version="url:https://example.org/paper")
        self.record_value([self.items[0]])
        assessment = build_assessment(self.statement)
        note = self.store.snapshot()["records"]["reading"][second["reading_id"]]
        assessment["items"]["A1"]["evidence"] = [{"kind": "source", "link": note["inspections"][0]["link"]}]
        self.mutate(problem_claims.record_assessment, assessment)
        self.capture(second["version_id"], body="A further main text needs reading. References: none.", abstract="An authored abstract.")
        manifest, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], ["2601.00001v1"])
        self.assertEqual(manifest["citation_counts"]["self_assessment"], 0)

    def test_registry_and_arxiv_versions_keep_the_legacy_group_when_authors_change(self):
        self.add_reading(1, note_id="read-020")
        second = self.add_reading(1, version=2, note_id="read-030")
        self.add_registry_reading("read-010", ["A. Researcher"])
        work = self.store.snapshot()["records"]["work"][second["version_id"]]
        self.put("work", second["version_id"], dict(work, authors=["Different Author"]))
        self.record_value(self.items)
        manifest, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], ["10.48550/arxiv.2601.00001"])
        self.assertEqual(manifest["citation_counts"]["total"], 1)

    def test_search_reading_can_separate_ordinary_groups_without_losing_their_order(self):
        first = self.add_reading(1, note_id="read-020")
        second = self.add_reading(1, version=2, note_id="read-010")
        registry = self.add_registry_reading("read-030", ["A. Researcher"])
        work = self.store.snapshot()["records"]["work"][second["version_id"]]
        self.put("work", second["version_id"], dict(work, authors=["Different Author"]))
        self.record_value([first, registry])
        self.record_search("earlier-read-version", [second["version_id"]])
        manifest, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], [
            "2601.00001v2", "10.48550/arxiv.2601.00001"])
        self.assertEqual(manifest["citation_counts"]["value"], 2)
        self.assertEqual(manifest["citation_counts"]["total"], 2)
        self.assertEqual(len(problem_delivery.project_citations(self.store.snapshot()["records"], self.artifacts)), 1)

    def test_changing_version_keeps_counts_and_references_aligned_with_the_work_slots(self):
        self.add_reading(1, note_id="read-010")
        second = self.add_reading(1, version=2, note_id="read-030")
        self.add_registry_reading("read-020", ["Different Author"])
        work = self.store.snapshot()["records"]["work"][second["version_id"]]
        self.put("work", second["version_id"], dict(work, authors=["Different Author"]))
        self.record_value(self.items)
        manifest, directory, citations = self.export_citations({"citations": {"versions": [
            {"work_id": "arxiv:2601.00001", "version_id": second["version_id"]}]}})
        self.assertEqual([row["locator"] for row in citations], ["2601.00001v2", "10.48550/arxiv.2601.00001"])
        self.assertEqual(manifest["citation_counts"]["total"], 2)
        self.assertEqual(manifest["citation_counts"]["included"], 2)
        self.assertEqual(len(json.loads((directory / "citation-refs.json").read_text())), 2)

    def test_spec_can_choose_a_fully_read_version_outside_the_ordinary_reading_set(self):
        first = self.add_reading(1)
        self.add_reading(1, version=2)
        self.record_value([first])
        _, _, citations = self.export_citations({"citations": {"versions": [
            {"work_id": "arxiv:2601.00001", "version_id": "arxiv:2601.00001v2"}]}})
        self.assertEqual([row["locator"] for row in citations], ["2601.00001v2"])

    def test_version_spec_can_use_any_family_represented_by_the_cited_work(self):
        first = self.add_reading(1, note_id="read-020")
        second = self.add_reading(1, version=2, note_id="read-030")
        registry = self.add_registry_reading("read-010", ["A. Researcher"])
        work = self.store.snapshot()["records"]["work"][second["version_id"]]
        self.put("work", second["version_id"], dict(work, authors=["Different Author"]))
        self.record_value([first, registry])
        manifest, directory, citations = self.export_citations({"citations": {"versions": [
            {"work_id": "arxiv:2601.00001", "version_id": second["version_id"]}]}})
        self.assertEqual([row["locator"] for row in citations], ["2601.00001v2"])
        self.assertEqual(manifest["citation_counts"]["total"], 1)
        references = json.loads((directory / "citation-refs.json").read_text())
        self.assertEqual(references[0]["bibliography"]["arxivId"], "2601.00001v2")

    def test_version_spec_cannot_repeat_one_version_in_two_legacy_work_slots(self):
        first = self.add_reading(1, note_id="read-010")
        second = self.add_reading(1, version=2, note_id="read-030")
        registry = self.add_registry_reading("read-020", ["A. Researcher"])
        work = self.store.snapshot()["records"]["work"][first["version_id"]]
        self.put("work", first["version_id"], dict(work, authors=["Different Author"]))
        self.record_value(self.items)
        spec = self.root / "citation-spec.json"
        spec.write_text(json.dumps({"citations": {"versions": [
            {"work_id": "arxiv:2601.00001", "version_id": second["version_id"]},
            {"work_id": registry["version_id"], "version_id": second["version_id"]}]}}))
        before = self.store.snapshot()
        response = self.export_problem("problem-override", "--spec", str(spec))
        self.assertNotEqual(response.returncode, 0, response.stdout)
        self.assertEqual(json.loads(response.stderr.splitlines()[0])["error"]["code"], "invalid_input")
        self.assertEqual(self.store.snapshot(), before)
        self.assertFalse((self.root / "final/problem/exports").exists())

    def test_shared_doi_fallback_respects_author_and_alias_guards(self):
        first = self.add_reading(1)
        other = self.add_reading(2)
        records = self.store.snapshot()["records"]
        found = "openalex:W123456789"
        work = copy.deepcopy(records["work"][first["version_id"]])
        work.update(id=found, aliases=["doi:10.48550/arxiv.2601.00001"], fulltexts=[])
        self.put("work", found, work)
        self.record_value([other])
        self.record_search("registry-work", [found])
        _, _, citations = self.export_citations()
        self.assertEqual([row["locator"] for row in citations], ["2601.00002v1", "2601.00001v1"])
        work["authors"] = ["Different Author"]
        self.put("work", found, work)
        manifest, _, citations = self.export_citations()
        self.assertEqual(len(citations), 1)
        self.assertEqual(manifest["uncited_relevant_works"], [{"work_id": found, "search_ids": ["registry-work"]}])

    def test_first_prefers_the_named_work_over_links_to_other_citation_groups(self):
        first = self.add_reading(1)
        registry = self.add_registry_reading("read-registry", [])
        self.record_value([first, registry])
        manifest, _, citations = self.export_citations({"citations": {"first": [registry["version_id"]]}})
        self.assertEqual([row["locator"] for row in citations], [
            "10.48550/arxiv.2601.00001", "2601.00001v1"])
        self.assertEqual(manifest["citation_counts"]["total"], 2)

    def test_version_spec_retains_the_registry_author_and_alias_guards(self):
        first = self.add_reading(1)
        second = self.add_reading(1, version=2)
        other = self.add_reading(2)
        records = self.store.snapshot()["records"]
        found = "openalex:W123456789"
        work = copy.deepcopy(records["work"][first["version_id"]])
        work.update(id=found, aliases=["doi:10.48550/arxiv.2601.00001"], fulltexts=[])
        self.put("work", found, work)
        self.record_value([other])
        self.record_search("registry", [found])
        version = records["work"][second["version_id"]]
        path = self.root / "guarded-spec.json"
        path.write_text(json.dumps({"citations": {"versions": [{"work_id": found, "version_id": second["version_id"]}]}}))
        self.put("work", second["version_id"], dict(version, authors=["Different Author"]))
        for guard in ("authors", "alias"):
            with self.subTest(guard=guard):
                if guard == "alias":
                    self.put("work", second["version_id"], version)
                    self.put("alias", second["version_id"], {"assertions": [
                        {"work_id": "arxiv:2601.00002", "source_id": "synthetic-conflict", "relation": "same_work", "locator": {}}]})
                response = self.export_problem("problem-override", "--spec", str(path))
                self.assertNotEqual(response.returncode, 0, response.stdout)
                self.assertEqual(json.loads(response.stderr.splitlines()[0])["error"]["code"], "invalid_input")

    def test_spec_refuses_unknown_work_unread_version_and_malformed_fields_without_writing(self):
        self.add_reading(1)
        self.record_value()
        self.metadata(1, version=2)
        invalid = [None, [], {"extra": {}}, {"citations": []}, {"citations": {"first": "arxiv:2601.00001"}},
                   {"citations": {"first": ["arxiv:2601.99999"]}},
                   {"citations": {"first": ["arxiv:2601.00001", "arxiv:2601.00001"]}},
                   {"citations": {"versions": [{"work_id": "arxiv:2601.00001", "version_id": "arxiv:2601.00001v2"}]}},
                   {"citations": {"versions": [{"work_id": "arxiv:2601.00001", "version_id": self.items[0]["version_id"], "extra": True}]}},
                   {"citations": {"versions": "v1"}}, {"citations": {"versions": None}},
                   {"citations": {"versions": [None]}}, {"citations": {"versions": [{}]}},
                   {"citations": {"versions": [{"work_id": "bad", "version_id": 3}]}},
                   {"citations": {"versions": [
                       {"work_id": "arxiv:2601.00001", "version_id": "arxiv:2601.00001v1"},
                       {"work_id": "arxiv:2601.00001v1", "version_id": "arxiv:2601.00001v1"}]}},
                   {"citations": {"first": ["arxiv:2601.00001", "arxiv:2601.00001v1"]}}, {"citations": {"extra": []}}]
        path = self.root / "invalid-spec.json"
        before = self.store.snapshot()
        for spec in invalid:
            with self.subTest(spec=spec):
                path.write_text(json.dumps(spec))
                response = self.export_problem("problem-override", "--spec", str(path))
                self.assertNotEqual(response.returncode, 0, response.stdout)
                self.assertEqual(json.loads(response.stderr.splitlines()[0])["error"]["code"], "invalid_input")
                self.assertEqual(self.store.snapshot(), before)
                self.assertFalse((self.root / "final/problem/exports").exists())

    def test_incomplete_or_malformed_v3_export_is_left_in_place(self):
        self.add_reading(1)
        self.record_value()
        manifest, directory, _ = self.export_citations()
        self.assertEqual(manifest["schema"], "exactory-problem-export-v3")
        original = {path.name: path.read_bytes() for path in directory.iterdir()}
        for name, spec in [("null-spec", None), ("array-spec", []), ("extra-field", {"extra": []})]:
            with self.subTest(name=name):
                damaged = directory.parent / name
                damaged.mkdir()
                files = dict(original)
                manifest = json.loads(files["problem-export.json"])
                manifest["citation_spec"] = spec
                files["problem-export.json"] = (json.dumps(manifest) + "\n").encode()
                for filename, data in files.items():
                    (damaged / filename).write_bytes(data)
                render_all(self.store)
                self.assertEqual({path.name: path.read_bytes() for path in damaged.iterdir()}, files)

    def test_stored_v2_keeps_its_bytes_and_digest_beside_the_enriched_export(self):
        self.add_reading(1)
        self.add_reading(2)
        self.record_value()
        self.record_search("second-work", [self.items[1]["version_id"]])
        records = self.store.snapshot()["records"]
        override = problem_delivery.find_post_override(records)
        fixture = Path(__file__).parent / "fixtures/problem_override_export_v2.py.txt"
        namespace = vars(problem_delivery).copy()
        exec(compile(fixture.read_bytes(), str(fixture), "exec"), namespace)
        files, manifest = namespace["build_override_projection"](records, self.artifacts, override, include_references=True)
        self.assertEqual(problem_delivery.build_override_projection(records, self.artifacts, override,
                                                                    include_references=True), (files, manifest))
        stored = self.root / "final/problem/exports/stored-v2"
        problem_delivery.write_problem_export(self.store, records, files, manifest, stored)
        old_digest = problem_delivery.compute_export_digest(files)
        result, directory, citations = self.export_citations()
        self.assertEqual(len(citations), 2)
        self.assertEqual(result["schema"], "exactory-problem-export-v3")
        render_all(self.store)
        self.assertEqual({path.name: path.read_bytes() for path in stored.iterdir()}, files)
        self.assertEqual(problem_delivery.compute_export_digest(files), old_digest)
        self.assertEqual(json.loads((stored / "problem-export.json").read_bytes())["schema"], "exactory-problem-export-v2")
        self.record_search("changed-search", [self.items[1]["version_id"]], "contradictory")
        render_all(self.store)
        self.assertFalse(directory.exists())
        historical = self.root / "final/history/problem/exports" / directory.name
        self.assertTrue(historical.is_dir())
        self.assertTrue(stored.is_dir())
        self.assertEqual(digest(records["reading"]), digest(self.store.snapshot()["records"]["reading"]))
