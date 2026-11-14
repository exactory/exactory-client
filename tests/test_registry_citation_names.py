"""Citations use the registry record of the selected locator, including its version."""

import copy
import json
import shlex
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import acquisition, literature, problem_delivery, providers
from research_harness.errors import ResearchError
from research_harness.output_views import render_all
from research_fixtures import atom, client, crossref, entry, json_response, openalex
from test_check import _check
from test_gate_override import OverrideCommandMixin
import test_problem_override_citations as override_fixtures
from test_research_problem_claims import ProblemCase


def build_datacite_response(doi="10.5281/zenodo.12345", title="A registry paper", name="Jung, Minho"):
    return {"data": {"id": doi, "type": "dois", "attributes": {
        "doi": doi, "titles": [{"title": title}], "creators": [{"name": name}], "publicationYear": 2026}}}


class RegistryCitationExportTests(OverrideCommandMixin, ProblemCase):
    add_reading = override_fixtures.OverrideCitationTests.add_reading
    add_registry_reading = override_fixtures.OverrideCitationTests.add_registry_reading
    record_value = override_fixtures.OverrideCitationTests.record_value
    export_citations = override_fixtures.OverrideCitationTests.export_citations

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()
        self.write_instruction()
        self.items = []

    def import_registry(self, provider, raw, number=1):
        self.sequence += 1
        return acquisition.import_response(self.store, provider, raw,
            source_url="https://api.datacite.org/dois/10.5281/zenodo.12345" if provider == "datacite"
                       else "https://api.crossref.org/works/10.1234/published" if provider == "crossref"
                       else "https://export.arxiv.org/api/query?id_list=2601.00001v%d" % number,
            captured_at="2026-10-%02dT12:00:00Z" % number,
            expected_revision=self.store.revision, request_id="registry-capture-%d" % self.sequence)

    def assert_checked(self, directory, raw, provider):
        references = (directory / "citation-refs.json").read_text()
        entries = _check._read_refs_json_entries(directory / "citation-refs.json", references)
        def respond(url, accept="application/json"):
            if provider == "datacite" and "crossref" in url:
                return 404, None
            return 200, raw.decode()
        with patch.object(_check, "_open_url", side_effect=respond) as request:
            checked = _check._classify_entry(entries[0])
        self.assertEqual(checked["status"], "verified", checked)
        bibliography = json.loads(references)[0]["bibliography"]
        self.assertEqual(checked["_meta"]["registry_title"], bibliography["title"])
        self.assertEqual(checked["_meta"]["authors"], bibliography["authors"])
        if "arxivId" in bibliography:
            self.assertIn(bibliography["arxivId"], request.call_args.args[0])

    def test_openalex_name_in_korean_is_listed_then_datacite_capture_supplies_the_latin_name(self):
        # Source: 20261114035541-citation-author-name-in-another-script.
        from research_harness.reading import record_reading
        response = openalex()
        response.update(id="https://openalex.org/W12345", doi="https://doi.org/10.5281/zenodo.12345",
                        title="A registry paper", display_name="A registry paper")
        response["ids"] = {"openalex": response["id"], "doi": response["doi"]}
        response["authorships"][0]["author"]["display_name"] = "정민호"
        acquisition.import_response(self.store, "openalex", json.dumps(response).encode(),
            source_url="https://api.openalex.org/works/W12345", captured_at="2026-09-07T12:00:00Z",
            expected_revision=self.store.revision, request_id="openalex-korean-name")
        records = self.store.snapshot()["records"]
        version = "openalex:W12345"
        abstract = self.artifacts.read(records["work"][version]["abstract"]).decode()
        bundle = self.bundle(version, capture=self.capture(version, abstract=abstract), bundle_id="registry-bundle")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "read-import"))
        item = dict(kind="reading", version_id=version, reading_id="read-import")
        self.items.append(item)
        self.record_value()
        before, _, citations = self.export_citations()
        self.assertEqual(citations, [{"citation": "정민호. A registry paper", "locator": "10.5281/zenodo.12345"}])
        fallback = before["missing_registry_citations"]
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]["version_id"], item["version_id"])
        self.assertIn('"identifier": "doi:10.5281/zenodo.12345"', fallback[0]["capture_command"])
        raw = json.dumps(build_datacite_response()).encode()
        self.assertEqual(_check._compare_entry_with_meta({"title": "A registry paper", "author": "정민호"},
            _check._normalize_datacite_record(json.loads(raw)["data"]["attributes"]))["status"], "author_mismatch")
        http, wire, _ = client([(404, {"Content-Type": "application/json"}, b"{}"),
                                (200, {"Content-Type": "application/vnd.api+json"}, raw)])
        result = acquisition.acquire_work(self.store, "doi:10.5281/zenodo.12345", http=http,
            expected_revision=self.store.revision, request_id="capture-datacite", max_requests=2)
        self.assertEqual(result["attempts_used"], 2)
        self.assertIn("api.datacite.org/dois/", wire.requests[-1][0])
        sources = self.store.snapshot()["records"]["source"]
        self.assertEqual([sources[key]["provider"] for key in result["source_ids"]], ["crossref", "datacite"])
        after, directory, citations = self.export_citations()
        self.assertEqual(citations, [{"citation": "Minho Jung. A registry paper", "locator": "10.5281/zenodo.12345"}])
        self.assertEqual(after["missing_registry_citations"], [])
        self.assert_checked(directory, raw, "datacite")

    def build_versions(self):
        for version in (1, 2):
            item = self.add_reading(1, version=version)
            records = self.store.snapshot()["records"]
            self.put("work", item["version_id"], dict(records["work"][item["version_id"]],
                aliases=["doi:10.1234/published"], title="Stored version %d" % version))
            raw = atom([entry("2601.00001v%d" % version, doi="10.1234/published").replace(
                "An authored example", "Registry version %d" % version)], total=1)
            self.import_registry("arxiv", raw, version)
        raw = json.dumps({"message": dict(crossref()["message"], DOI="10.1234/published",
                        title=["Published title"], author=[{"name": "A. Researcher"}])}).encode()
        self.import_registry("crossref", raw, 3)
        self.record_value(self.items)
        return raw

    def test_doi_citation_uses_the_published_title_instead_of_the_preprint_title(self):
        # Source: 20261113213949-citation-check-published-version-title.
        raw = self.build_versions()
        self.assertEqual(_check._compare_entry_with_meta({"title": "Stored version 1", "author": "A. Researcher"},
            _check._normalize_crossref_work(json.loads(raw)["message"]))["status"], "title_mismatch")
        _, directory, citations = self.export_citations()
        self.assertEqual(citations, [{"citation": "A. Researcher. Published title", "locator": "10.1234/published"}])
        self.assert_checked(directory, raw, "crossref")

    def test_datacite_import_is_a_registry_assertion_without_replacing_populated_work_metadata(self):
        item = self.add_registry_reading("read-doi", ["Stored Name"], version="doi:10.5281/zenodo.12345")
        self.record_value()
        raw = json.dumps(build_datacite_response(title="Registered DOI title")).encode()
        result = self.import_registry("datacite", raw)
        records = self.store.snapshot()["records"]
        self.assertEqual(result["work_ids"], [item["version_id"]])
        self.assertEqual(records["work"][item["version_id"]]["authors"], ["Stored Name"])
        source = records["source"][result["source_ids"][0]]
        self.assertEqual(self.artifacts.read(source["response"]), raw)
        _, directory, citations = self.export_citations()
        self.assertEqual(citations[0]["citation"], "Minho Jung. Registered DOI title")
        self.assert_checked(directory, raw, "datacite")

    def test_datacite_response_for_another_doi_does_not_become_a_registry_assertion(self):
        item = self.add_registry_reading("read-doi", ["Stored Name"], version="doi:10.5281/zenodo.12345")
        self.record_value()
        http, _, _ = client([json_response(build_datacite_response(doi="10.5281/zenodo.99999"))])
        result = acquisition.acquire_work(self.store, item["version_id"], provider="datacite", http=http,
            expected_revision=self.store.revision, request_id="wrong-doi")
        self.assertEqual(result["pending"][0]["code"], "identifier_mismatch")
        manifest, _, citations = self.export_citations()
        self.assertEqual(citations[0]["citation"], "Stored Name. A registry paper")
        self.assertEqual(manifest["missing_registry_citations"][0]["version_id"], item["version_id"])

    def test_named_arxiv_versions_choose_their_own_locators_titles_and_check_records(self):
        # Source: 20261113110022-every-open-record-and-review-capacity, N11 and G10.
        self.build_versions()
        for version in (1, 2):
            with self.subTest(version=version):
                identifier = "arxiv:2601.00001v%d" % version
                spec = {"citations": {"versions": [{"work_id": "arxiv:2601.00001", "version_id": identifier}]}}
                result, directory, citations = self.export_citations(spec)
                self.assertEqual(citations, [{"citation": "A. Researcher. Registry version %d" % version,
                                             "locator": identifier[6:]}])
                self.assertEqual(result["missing_registry_citations"], [])
                raw = atom([entry(identifier[6:]).replace("An authored example", "Registry version %d" % version)], total=1)
                self.assert_checked(directory, raw, "arxiv")
                records = copy.deepcopy(self.store.snapshot()["records"])
                records["work_assertion"] = {key: row for key, row in records["work_assertion"].items()
                                             if row["id"] != identifier}
                from research_harness.problem_evidence import validate_override_citation_spec
                files, manifest = problem_delivery.build_override_projection(records, self.artifacts,
                    problem_delivery.find_post_override(records), citation_spec=validate_override_citation_spec(spec),
                    use_registry=True)
                self.assertEqual(json.loads(files["citations.json"]), [{
                    "citation": "A. Researcher. Stored version %d" % version, "locator": identifier[6:]}])
                self.assertEqual(manifest["missing_registry_citations"][0]["locator"], identifier[6:])

    def test_latest_capture_wins_even_when_the_first_work_title_stays_populated(self):
        self.add_reading(1)
        self.record_value()
        for number in (2, 3):
            self.import_registry("arxiv", atom([entry().replace("An authored example", "Captured title %d" % number)], total=1), number)
        _, _, citations = self.export_citations()
        self.assertEqual(citations[0]["citation"], "A. Researcher. Captured title 3")
        self.assertEqual(self.store.snapshot()["records"]["work"]["arxiv:2601.00001v1"]["title"], "An authored example")

    def test_reviewed_and_override_exports_compose_the_same_registry_citation(self):
        self.build_versions()
        records = self.store.snapshot()["records"]
        records["problem_decision"] = {"post": {"id": "post", "action": "post", "recorded_revision": 1}}
        ordinary_files, ordinary = problem_delivery.build_projection(records, self.artifacts, use_registry=True)
        override_files, override = problem_delivery.build_override_projection(records, self.artifacts,
            problem_delivery.find_post_override(records), citation_spec={"versions": [], "first": []}, use_registry=True)
        for name in ("citations.json", "citation-refs.json"):
            self.assertEqual(ordinary_files[name], override_files[name])
        self.assertEqual(ordinary["schema"], override["schema"])
        self.assertEqual(ordinary["missing_registry_citations"], [])

    def test_stored_review_binding_and_packet_keep_work_record_citations(self):
        from research_harness.problem_evidence import describe_binding, describe_evidence, project_citations
        self.build_versions()
        records = self.store.snapshot()["records"]
        expected = [{"citation": "A. Researcher. Stored version 1", "locator": "10.1234/published"}]
        self.assertEqual(describe_binding(records, self.artifacts)["citations"], expected)
        self.assertEqual(describe_evidence(records, self.artifacts)["citations"], expected)
        self.assertEqual(project_citations(records, self.artifacts)[0]["citation"], "A. Researcher. Published title")

    def test_uncaptured_work_and_url_citation_text_keep_their_previous_bytes(self):
        from research_harness.problem_evidence import project_citations
        self.add_reading(1)
        self.record_value()
        records = self.store.snapshot()["records"]
        records.pop("work_assertion")
        reading = records["reading"][self.items[0]["reading_id"]]
        for identifier, locator in (("arxiv:2601.00001v1", "2601.00001v1"),
                                    ("url:https://example.org/paper", "https://example.org/paper")):
            with self.subTest(identifier=identifier):
                work = records["work"].pop(reading["version_id"])
                work.update(id=identifier, aliases=[])
                reading["version_id"] = identifier
                records["work"][identifier] = work
                if identifier.startswith("url:"):
                    records["source_bundle"][reading["bundle_id"]]["units"][0]["url"] = locator
                self.assertEqual(project_citations(records, self.artifacts, readings=[reading]),
                                 project_citations(records, self.artifacts, readings=[reading], use_registry=False))

    def test_earlier_export_formats_keep_their_bytes_and_digest_after_a_registry_capture(self):
        self.add_reading(1)
        self.record_value()
        records = self.store.snapshot()["records"]
        override = problem_delivery.find_post_override(records)
        earlier = []
        for options in ({}, {"include_references": True}, {"citation_spec": {"first": [], "versions": []}}):
            files, manifest = problem_delivery.build_override_projection(records, self.artifacts, override, **options)
            path = self.root / "final/problem/exports" / problem_delivery.compute_export_digest(files)
            problem_delivery.write_problem_export(self.store, records, files, manifest, path)
            earlier.append((path, files, problem_delivery.compute_export_digest(files)))
        self.import_registry("arxiv", atom([entry().replace("An authored example", "New registered title")], total=1), 2)
        current, _, citations = self.export_citations()
        self.assertEqual(current["schema"], "exactory-problem-export-v4")
        self.assertEqual(citations[0]["citation"], "A. Researcher. New registered title")
        render_all(self.store)
        for path, files, digest in earlier:
            retained = path if path.exists() else self.root / "final/history/problem/exports" / path.name
            self.assertEqual({p.name: p.read_bytes() for p in retained.iterdir()}, files)
            self.assertEqual(problem_delivery.compute_export_digest(files), digest)


class RegistryRequestTests(unittest.TestCase):
    def test_datacite_request_and_invalid_responses(self):
        provider = providers.DataCite()
        self.assertEqual(provider.work_request("10.5281/zenodo.12345").url,
                         "https://api.datacite.org/dois/10.5281%2Fzenodo.12345")
        with self.assertRaises(ResearchError) as error:
            provider.work_request("arxiv:2601.00001v1")
        self.assertEqual(error.exception.code, "invalid_identifier")
        invalid = [{}, {"data": []}, build_datacite_response(title="")]
        for key, bad in (("titles", []), ("titles", "A title"), ("creators", "A name"),
                         ("creators", [{"name": 7}]), ("creators", [{}]), ("creators", [{"name": ""}]),
                         ("publicationYear", "2026"),
                         ("publicationYear", 0), ("publicationYear", 10000)):
            value = build_datacite_response()
            value["data"]["attributes"][key] = bad
            invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ResearchError) as error:
                provider.parse(json.dumps(value).encode())
            self.assertEqual(error.exception.code, "invalid_response")

    def test_capture_command_keeps_a_doi_with_shell_characters_as_json_data(self):
        from research_harness.problem_citation_metadata import compose_citation
        identifier = "doi:10.1234/a'$(id)"
        work = {"id": identifier, "title": "A synthetic paper", "authors": ["A. Researcher"]}
        projected = compose_citation({}, None, work, identifier[4:])
        command = shlex.split(projected["fallback"]["capture_command"])
        self.assertEqual(command[:2], ["printf", "%s\\n"])
        self.assertEqual(json.loads(command[2]), {"identifier": identifier})

    def test_check_preserves_new_and_old_arxiv_versions_in_every_identifier_field(self):
        for identifier in ("2601.00001v1", "math/0301001v2"):
            for entry in ({"archiveprefix": "arXiv", "eprint": identifier},
                          {"eprint": identifier}, {"url": "https://arxiv.org/abs/" + identifier}):
                with self.subTest(entry=entry):
                    self.assertEqual(_check._extract_arxiv_id(entry), identifier)

    def test_a_datacite_arxiv_doi_is_checked_as_that_doi(self):
        doi = "10.48550/arxiv.2601.00001"
        raw = json.dumps(build_datacite_response(doi=doi)).encode()
        def respond(url, accept="application/json"):
            if "api.crossref.org" in url:
                return 404, None
            self.assertIn("api.datacite.org/dois/10.48550%2Farxiv.2601.00001", url)
            return 200, raw.decode()
        with patch.object(_check, "_open_url", side_effect=respond):
            checked = _check._classify_entry({"doi": doi, "title": "A registry paper", "author": "Minho Jung"})
        self.assertEqual(checked["status"], "verified")
        self.assertEqual(checked["_meta"]["source"], "datacite")

    def test_organizational_datacite_names_keep_their_registered_order(self):
        raw = build_datacite_response(name="Geometry, Working Group")
        raw["data"]["attributes"]["creators"][0]["nameType"] = "Organizational"
        work = providers.DataCite().parse(json.dumps(raw).encode()).works[0]
        meta = _check._normalize_datacite_record(raw["data"]["attributes"])
        self.assertEqual(work["authors"], ["Geometry, Working Group"])
        self.assertEqual(work["authors"], meta["authors"])
        self.assertEqual(_check._compare_entry_with_meta({"title": work["title"],
            "author": " and ".join(work["authors"])}, meta)["status"], "verified")

    def test_exact_non_latin_registered_names_verify(self):
        meta = _check._normalize_datacite_record(build_datacite_response(name="정민호")["data"]["attributes"])
        self.assertEqual(_check._compare_entry_with_meta({"title": "A registry paper", "author": "정민호"}, meta)["status"],
                         "verified")

    def test_json_author_arrays_keep_registered_name_boundaries(self):
        attributes = build_datacite_response()["data"]["attributes"]
        attributes["creators"] = [{"name": "A", "nameType": "Organizational"},
                                  {"name": "B and C", "nameType": "Organizational"}]
        meta = _check._normalize_datacite_record(attributes)
        for names, expected in ((["A", "B and C"], "verified"), (["A and B", "C"], "author_mismatch")):
            references = [{"referenceString": "A synthetic citation", "bibliography": {
                "title": meta["registry_title"], "authors": names, "doi": attributes["doi"]}}]
            parsed = _check._read_refs_json_entries(Path("references.json"), json.dumps(references))[0]
            self.assertEqual(_check._compare_entry_with_meta(parsed, meta)["status"], expected)

    def test_check_and_export_read_the_same_fixture_record_for_each_registry(self):
        from research_harness.providers import Arxiv, Crossref
        for provider, raw, normalize in (
            (Crossref(), json.dumps(crossref()).encode(), lambda data: _check._normalize_crossref_work(json.loads(data)["message"])),
            (providers.DataCite(), json.dumps(build_datacite_response()).encode(), lambda data: _check._normalize_datacite_record(json.loads(data)["data"]["attributes"])),
            (Arxiv(), atom([entry()], total=1), lambda data: _check._parse_arxiv_feed(data.decode())[0])):
            with self.subTest(provider=provider.name):
                work = provider.parse(raw).works[0]
                meta = normalize(raw)
                self.assertEqual((work["title"], work["authors"]), (meta["registry_title"], meta["authors"]))

    def test_named_registry_locator_precedes_a_published_alias_and_other_named_ids_keep_the_priority(self):
        from research_harness.problem_evidence import find_citation_locator
        work = {"id": "arxiv:2601.00001v2", "aliases": ["doi:10.1234/published"]}
        self.assertEqual(find_citation_locator(work, []), "10.1234/published")
        self.assertEqual(find_citation_locator(work, [], named_version=work["id"]), "2601.00001v2")
        self.assertEqual(find_citation_locator(work, [], named_version="doi:10.1234/selected"), "10.1234/selected")
        self.assertEqual(find_citation_locator(work, [], named_version="url:https://example.org/paper"), "10.1234/published")
