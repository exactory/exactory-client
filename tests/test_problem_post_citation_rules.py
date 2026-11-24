"""The posting field rules apply at the citation-binding and export boundaries."""

import io
import json
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from research_fixtures import atom, client, entry
from research_harness import cli
from research_harness.acquisition import acquire_fulltext, import_response
from research_harness.check_mode import check_mutation
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.exactory_api import validate_post_citation_field, validate_post_citations
from research_harness.literature import import_bundle
from research_harness.problem_citation_metadata import validate_citation_metadata
from research_harness.problem_claims import compute_problem_evidence_digest
from research_harness.problem_delivery import build_post_files, build_projection, export_problem
from research_harness.problem_evidence import has_correctable_locator, project_citations
from research_harness.reading import record_reading
from research_harness.storage import Store
from test_research_problem_delivery import ProblemDeliveryCase


LONG_URL = "https://example.org/" + "x" * 481
PAPER_URL = "https://example.org/paper.pdf"


class CitationCase(ProblemDeliveryCase):
    def receipt(self, command, payload):
        return self.mutate(cli.OPERATIONS[command], payload)

    def record(self, command, payload):
        return self.receipt(command, payload)["result"]

    def refuse(self, command, payload, code):
        revision = self.store.revision
        with self.assertRaises(ResearchError) as caught:
            self.record(command, payload)
        self.assertEqual(caught.exception.code, code, caught.exception.as_dict())
        self.assertEqual(self.store.revision, revision)
        return caught.exception

    def assert_post_refuses(self, rows, code):
        with self.assertRaises(ResearchError) as caught:
            validate_post_citations(rows)
        self.assertEqual(caught.exception.code, code)

    def records(self):
        return self.store.snapshot()["records"]

    def seed_value(self, item):
        value = dict(self.build_value([item]), accounts_version=2, recorded_revision=self.store.revision + 1)
        self.put("problem_value", value["id"], value)

class PostCitationRulesTests(CitationCase):
    def read_paper(self, url=PAPER_URL, *, title="Fixture paper", locator=None, extraction=None):
        version = "url:" + url
        import_response(self.store, "web", json.dumps({"id": version, "title": title}).encode(),
            source_url="https://example.org/catalog", captured_at="2026-09-07T12:00:00Z",
            media_type="application/json", mappings=[{"id": "/id", "title": "/title"}],
            expected_revision=self.store.revision, request_id="citation-work")
        http, _, _ = client([(200, {"Content-Type": "application/pdf"}, b"%PDF-1.4\nfixture\n%%EOF")])
        capture = acquire_fulltext(self.store, version, url, http=http,
            extractor=lambda data: {"status": "extracted", "text": extraction or "Bounded sequences. References: none."},
            expected_revision=self.store.revision, request_id="citation-pdf")["capture"]
        bundle = self.bundle(version, capture)
        if locator is not None:
            bundle["units"][0]["url"] = locator
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "citation-reading"))
        item = {"kind": "reading", "version_id": version, "reading_id": "citation-reading"}
        return item, capture

    def build_metadata(self, title, *, url=PAPER_URL, locator=PAPER_URL, work_title="Fixture paper", cited=True, author="A"):
        self.initialize_problem()
        self.selected = self.select_statement()
        item, capture = self.read_paper(url, title=work_title, locator=locator,
                                        extraction=title + "\n" + author + "\nBounded sequences. References: none.")
        if cited:
            self.seed_value(item)
        content = self.artifacts.read(capture["text"])
        start = content.index(title.encode())
        end = start + len(title.encode())
        author_start = content.index(b"\n" + author.encode() + b"\n", end) + 1
        span = {"kind": "fulltext", "source_id": capture["source_id"], "sha256": capture["text"]["sha256"]}
        return {"id": "metadata", "version_id": item["version_id"], "title": title, "authors": [author],
                "evidence": {"title": dict(span, start=start, end=end),
                             "authors": [dict(span, start=author_start, end=author_start + len(author.encode()))]}}, item, capture

    def build_locator(self, item, capture, url):
        return {"id": "locator", "version_id": item["version_id"], "reading_id": item["reading_id"],
                "original_sha256": capture["original"]["sha256"], "url": url, "source_id": capture["source_id"]}

    def test_metadata_refuses_a_post_citation_character(self):
        payload, _, _ = self.build_metadata("A title\nwith a \x1bcharacter")
        error = self.refuse("problem-citation-metadata", payload, "problem_citation_text_character")
        self.assertEqual((error.details["character"], error.details["line"], error.details["column"]), ("U+001B", 2, 8))

    def test_nul_in_either_metadata_component_uses_the_post_character_code(self):
        title, author = "An adequate title", "Ada"
        payload, _, _ = self.build_metadata(title + "\x00tail", author=author + "\x00tail", cited=False)
        title_span, author_spans = payload["evidence"]["title"], payload["evidence"]["authors"]
        good_title = dict(title_span, end=title_span["start"] + len(title))
        good_author = dict(author_spans[0], end=author_spans[0]["start"] + len(author))
        cases = {
            "title": dict(payload, authors=[author], evidence=dict(payload["evidence"], authors=[good_author])),
            "author": dict(payload, title=title, evidence=dict(payload["evidence"], title=good_title)),
        }
        for component, candidate in cases.items():
            with self.subTest(component=component):
                error = self.refuse("problem-citation-metadata", candidate, "problem_citation_text_character")
                self.assertEqual(error.details["character"], "U+0000")
                self.assertEqual(error.details["step"]["command"], "problem-citation-metadata")
        good = dict(payload, title=title, authors=[author], evidence=dict(title=good_title, authors=[good_author]))
        for component, candidate in (("no author", dict(good, title="Bad", authors=[])),
                                     ("blank title", dict(good, title=" "))):
            with self.subTest(component=component):
                self.refuse("problem-citation-metadata", candidate, "invalid_input")
        self.assertEqual(self.record("problem-citation-metadata", good)["authors"], [author])

    def test_metadata_refuses_a_post_citation_below_the_minimum_length(self):
        payload, _, _ = self.build_metadata("123456\ufeff")
        error = self.refuse("problem-citation-metadata", payload, "problem_citation_text_length")
        self.assertEqual((error.details["characters"], error.details["minimum"]), (9, 10))

        provenance, _ = validate_citation_metadata(self.records(), self.artifacts, payload)
        record = dict(payload, provenance=provenance, recorded_revision=self.store.revision + 1)
        self.put("problem_citation_metadata", record["id"], record)
        self.assert_stored_citation({"citation": "A. 123456\ufeff", "locator": PAPER_URL}, "d66d3890f4a8c0ce3c637fe44bd5a943e83e2389c677af80df1728c7f16f2618")

    def test_metadata_refuses_a_post_citation_above_the_maximum_length(self):
        payload, _, _ = self.build_metadata("\U0001f4d6" * 499)
        error = self.refuse("problem-citation-metadata", payload, "problem_citation_text_length")
        self.assertEqual((error.details["characters"], error.details["maximum"]), (1001, 1000))

    def test_locator_refuses_a_post_locator_above_the_maximum_length(self):
        self.initialize_problem()
        self.select_statement()
        item, capture = self.read_paper(LONG_URL)
        self.seed_value(item)
        payload = self.build_locator(item, capture, LONG_URL)
        error = self.refuse("problem-citation-locator", payload, "problem_citation_locator_length")
        self.assertEqual((error.details["characters"], error.details["maximum"]), (501, 500))

        records = self.records()
        record = dict(payload, reading_digest=digest(records["reading"][item["reading_id"]]),
                      source_digest=digest(records["source"][payload["source_id"]]),
                      recorded_revision=self.store.revision + 1)
        self.put("citation_locator", payload["id"], record)
        self.assert_stored_citation({"citation": "Fixture paper", "locator": LONG_URL}, "149724f2e2c46141481f80b8c6fbbab8475279acd33ab4252229b0f99e521609")

    def assert_stored_citation(self, expected, expected_digest):
        records = Store(self.root).snapshot()["records"]
        self.assertEqual(project_citations(records, self.artifacts), [expected])
        self.assertEqual(json.loads(build_post_files(records, self.artifacts)["citations.json"]), [expected])
        self.assertEqual(compute_problem_evidence_digest(records, self.artifacts), expected_digest)

    def test_metadata_leaves_an_unchanged_locator_refusal(self):
        payload, _, _ = self.build_metadata("An adequate title", url=LONG_URL, locator=LONG_URL)
        self.assert_post_refuses(project_citations(self.records(), self.artifacts), "problem_citation_locator_length")
        self.record("problem-citation-metadata", payload)
        rows = project_citations(self.records(), self.artifacts)
        self.assertEqual(rows[0]["citation"], "A. An adequate title")
        self.assert_post_refuses(rows, "problem_citation_locator_length")

    def test_two_stale_binders_can_be_repaired_in_either_order(self):
        commands = ("problem-citation-metadata", "problem-citation-locator")
        for index, order in enumerate((commands, commands[::-1])):
            with self.subTest(first=order[0]):
                if index:
                    self.doCleanups()
                    self.setUp()
                metadata, item, capture = self.build_metadata("An adequate title", locator=None)
                locator = self.build_locator(item, capture, PAPER_URL)
                self.record(commands[0], metadata)
                self.record(commands[1], locator)
                source = self.records()["source"][capture["source_id"]]
                self.put("source", source["id"], dict(source, captured_at="2026-09-08T12:00:00Z"))
                repairs = {commands[0]: dict(metadata, id="renew-text"), commands[1]: dict(locator, id="renew-locator")}
                self.record(order[0], repairs[order[0]])
                with self.assertRaises(ResearchError) as caught:
                    project_citations(self.records(), self.artifacts)
                expected = ("problem_citation_locator_unbound" if order[0] == commands[0]
                            else "problem_citation_evidence_unbound")
                self.assertEqual(caught.exception.code, expected)
                self.record(order[1], repairs[order[1]])
                validate_post_citations(project_citations(self.records(), self.artifacts))


    def test_uncited_metadata_checks_its_own_text_and_accepts_a_valid_correction(self):
        payload, _, _ = self.build_metadata("Bad\nA repaired title", cited=False, locator=None)
        span = payload["evidence"]["title"]
        bad = dict(payload, title="Bad", evidence=dict(payload["evidence"], title=dict(span, end=span["start"] + 3)))
        error = self.refuse("problem-citation-metadata", bad, "problem_citation_text_length")
        self.assertEqual(error.details["step"]["command"], "problem-citation-metadata")
        self.assertEqual(error.details["step"]["payload"], {"version_id": payload["version_id"]})
        saved = self.record("problem-citation-metadata", payload)
        self.assertEqual(saved["title"], payload["title"])

    def test_shadowed_locator_checks_its_own_character_length_and_format(self):
        self.initialize_problem()
        self.select_statement()
        item, capture = self.read_paper(LONG_URL, locator=PAPER_URL)
        self.seed_value(item)
        payload = self.build_locator(item, capture, LONG_URL)
        for url, code in ((LONG_URL, "problem_citation_locator_length"),
                          (PAPER_URL + "\x1b", "problem_citation_locator_character"),
                          (PAPER_URL + "\x00", "problem_citation_locator_character"),
                          ("http://example.org/paper.pdf", "problem_citation_locator_format")):
            with self.subTest(code=code):
                self.assert_post_refuses([{"citation": "Fixture paper", "locator": url}], code)
                error = self.refuse("problem-citation-locator", dict(payload, url=url), code)
                self.assertEqual(error.details["step"]["command"], "problem-citation-locator")
                self.assertEqual(error.details["step"]["payload"]["reading_id"], item["reading_id"])
        self.refuse("problem-citation-locator", dict(payload, url=" "), "problem_citation_locator_unbound")
        self.assertEqual(project_citations(self.records(), self.artifacts)[0]["locator"], PAPER_URL)

    def test_value_write_keeps_the_released_receipt_beside_a_short_title_and_a_damaged_work(self):
        self.initialize_problem()
        self.select_statement()
        item, _ = self.read_paper(title="Bad")
        work = self.records()["work"][item["version_id"]]
        nested = None
        for _ in range(98):
            nested = [nested]
        self.put("work", work["id"], dict(work, work_id=nested))
        payload = self.build_value([item], "value-short-title")
        identity = dict(expected_revision=self.store.revision, request_id="value-short-title")
        receipt = cli.OPERATIONS["problem-value"](self.store, payload, **identity)
        self.assertEqual(self.store.revision, identity["expected_revision"] + 1)
        self.assertEqual(sorted(receipt), ["request_id", "result", "revision", "size_report"])
        checked = check_mutation(self.store, cli.OPERATIONS["problem-value"], payload, bounded_payload=payload, **identity)
        for result in (receipt["result"], checked["result"]):
            self.assertNotIn("citation_report", result)
        self.assertNotIn("citation_report", checked)

    def test_a_url_work_without_a_unit_url_takes_its_locator_correction(self):
        """The decision on the locator stop of a refused export: for a URL work whose bundle has no HTTPS unit URL,
        the record of problem-citation-locator is the locator of its citation, and the store takes the command."""
        self.initialize_problem()
        self.select_statement()
        item, capture = self.read_paper()
        reading = self.records()["reading"][item["reading_id"]]
        self.assertTrue(has_correctable_locator(self.records(), reading))
        self.assertEqual(self.record("problem-citation-locator", self.build_locator(item, capture, PAPER_URL))["url"],
                         PAPER_URL)

    def test_a_locator_that_comes_first_is_no_locator_correction(self):
        """For a URL work whose bundle has an HTTPS unit URL, for an arXiv work and for a DOI work, the locator of
        the citation comes before a recorded one, so a refused export of such a locator does not name
        problem-citation-locator."""
        self.initialize_problem()
        self.select_statement()
        item, _ = self.read_paper(locator=LONG_URL)
        arxiv = self.record_full_reading(note_id="read-arxiv")
        records = self.records()
        work = records["work"][arxiv["version_id"]]
        self.put("work", work["id"], dict(work, aliases=[*work.get("aliases", []), "doi:10.1234/fixture"]))
        for name, version, reading_id in (("url", item["version_id"], item["reading_id"]),
                                          ("arxiv", arxiv["version_id"], arxiv["reading_id"])):
            with self.subTest(work=name):
                self.assertFalse(has_correctable_locator(records, records["reading"][reading_id]))
        doi = self.records()
        self.assertFalse(has_correctable_locator(doi, doi["reading"][arxiv["reading_id"]]))

    def test_valid_locator_is_saved_beside_an_invalid_title(self):
        _, item, capture = self.build_metadata("A valid replacement", locator=None, work_title="Bad")
        payload = self.build_locator(item, capture, PAPER_URL)
        self.assertEqual(self.record("problem-citation-locator", payload)["url"], PAPER_URL)
        self.assert_post_refuses(project_citations(self.records(), self.artifacts), "problem_citation_text_length")


class CitationExportCompatibilityTests(ProblemDeliveryCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def record_short_registry_title(self):
        self.prepare_ready()
        self.record_post_decision()
        tag = self.full_reading["version_id"].split(":")[-1]
        body = atom([entry(tag).replace("A. Researcher", "A").replace("An authored example", "X")], 1)
        import_response(self.store, "arxiv", body, source_url="https://export.arxiv.org/api/query?id_list=" + tag,
            captured_at="2026-12-01T12:00:00Z", expected_revision=self.store.revision, request_id="new-registry")

    def test_approved_short_registry_title_is_refused_after_composition(self):
        self.record_short_registry_title()
        files, _ = build_projection(self.store.snapshot()["records"], self.artifacts, use_registry=True)
        citations = json.loads(files["citations.json"])
        self.assertEqual(citations[0]["citation"], "A. X")
        with self.assertRaises(ResearchError) as post:
            validate_post_citations(citations)

        destination = self.root / "reviewed-short-title"
        with self.assertRaises(ResearchError) as export:
            export_problem(self.store, destination)
        refusal = export.exception.as_dict()
        for member in ("version_id", "refusal", "decision"):
            refusal["details"].pop(member)
        self.assertEqual(refusal, post.exception.as_dict())
        self.assertEqual(export.exception.code, "problem_citation_text_length")
        self.assertFalse(destination.exists())

    def test_registry_citation_stop_names_a_step_that_the_store_takes(self):
        """Repairs ST-F2 of the review of the merged tree. The text of a citation of an arXiv work comes from the
        registry, and problem-citation-metadata refuses such a work, so the export refusal names the version and
        carries the stop of that command's refusal, whose steps read the record."""
        from research_harness.stop_classes import describe_terminal
        self.record_short_registry_title()
        version = self.full_reading["version_id"]
        with self.assertRaises(ResearchError) as export:
            export_problem(self.store, self.root / "registry-title")
        details = export.exception.details
        self.assertEqual(details["version_id"], version)
        self.assertEqual(details["refusal"]["code"], "problem_citation_work_ineligible")
        self.assertEqual(details["decision"], describe_terminal("problem_citation_work_ineligible")["details"]["decision"])
        commands = [step["command"] for answer in details["decision"]["then"] for step in answer["steps"]]
        self.assertEqual(commands, ["record-check", "status"])

        span = {"kind": "response", "source_id": "s", "sha256": "0" * 64, "start": 0, "end": 1}
        correction = {"id": "metadata-1", "version_id": version, "title": "A sufficiently long title of the work",
                      "authors": ["Ada Researcher"], "evidence": {"title": span, "authors": [span]}}
        revision = self.store.revision
        with self.assertRaises(ResearchError) as metadata:
            self.mutate(cli.OPERATIONS["problem-citation-metadata"], correction)
        self.assertEqual(metadata.exception.as_dict(), details["refusal"])
        self.assertEqual(self.store.revision, revision)
        self.take_status(revision)

    def take_status(self, revision):
        output = io.StringIO()
        with redirect_stdout(output):
            cli.main(["status", "--workspace", str(self.root)], render_views=lambda store: None)
        self.assertEqual(json.loads(output.getvalue())["revision"], revision)

    def test_a_doi_locator_stop_names_steps_that_the_store_takes(self):
        """The decision on the locator stop of a refused export: the locator of a DOI work is its DOI, which no
        record of problem-citation-locator replaces, so the refusal carries the decision of a refusal that names its
        cause, whose steps read the record."""
        import literature_fixtures
        from research_harness.stop_classes import STOP_GROUPS

        def build_entry(*args, **kwargs):
            return entry(*args, **dict(kwargs, doi="10.1234/" + "x" * 500))

        with patch.object(literature_fixtures, "entry", side_effect=build_entry):
            self.prepare_ready()
        self.record_post_decision()
        version = self.full_reading["version_id"]
        with self.assertRaises(ResearchError) as export:
            export_problem(self.store, self.root / "doi-locator")
        self.assertEqual(export.exception.code, "problem_citation_locator_length")
        details = export.exception.details
        self.assertEqual(details["version_id"], version)
        self.assertEqual(details["decision"],
                         {key: value for key, value in STOP_GROUPS["named_cause"]["next"].items() if key != "kind"})
        self.assertNotIn("problem-citation-locator",
                         [step["command"] for answer in details["decision"]["then"] for step in answer["steps"]])
        self.take_status(self.store.revision)


class CitationFieldRuleTests(unittest.TestCase):
    def test_utf16_bounds_normalization_and_allowed_locators(self):
        for field, raw, normalized in (
            ("citation", " \ufeffA citation\r\non two lines \r", "A citation\non two lines"),
            ("citation", "x" * 10, "x" * 10),
            ("citation", "\U0001f4d6" * 500, "\U0001f4d6" * 500),
            ("locator", LONG_URL[:-1], LONG_URL[:-1]),
            ("locator", " 10.1234/test ", "10.1234/test"),
            ("locator", "arXiv:2601.00001v2", "arXiv:2601.00001v2"),
        ):
            with self.subTest(field=field, size=len(raw)):
                self.assertEqual(validate_post_citation_field(field, raw), normalized)

    def test_public_validation_preserves_composed_row_order(self):
        short = {"citation": "Bad", "locator": PAPER_URL}
        long = {"citation": "A valid citation", "locator": LONG_URL}
        for rows, code in (
            ([short, long], "problem_citation_text_length"),
            ([long, short], "problem_citation_locator_length"),
        ):
            with self.subTest(code=code), self.assertRaises(ResearchError) as caught:
                validate_post_citations(rows)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.details["index"], 1)

    def test_every_citation_code_of_the_stop_registry_is_raised(self):
        """Repairs A8. The registry lists a stop of a citation only where a module of the harness raises it."""
        from pathlib import Path
        from research_harness.stop_classes import STOP_REGISTRY
        root = Path(__file__).resolve().parent.parent
        sources = [path for path in (root / "research_harness").glob("*.py") if path.name != "stop_classes.py"]
        text = "\n".join(path.read_text(encoding="utf-8") for path in sources + [root / "bin" / "exactory"])
        unraised = [code for code in STOP_REGISTRY if code.startswith("problem_citation")
                    and '"%s"' % code not in text and "'%s'" % code not in text]
        self.assertEqual(unraised, [])
