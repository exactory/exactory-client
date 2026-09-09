"""Authored literature built through the public acquisition APIs."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from html import escape

from research_harness.acquisition import acquire_fulltext, collect_cohort, import_response
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store
from research_fixtures import atom, client, entry, xml_response


FIELDS = ("problem", "claims", "assumptions", "methods", "evidence", "limitations", "relevance")


class LiteratureCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "study"
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        self.sequence = 0

    def mutate(self, function, payload, **kwargs):
        self.sequence += 1
        return function(self.store, payload, expected_revision=self.store.revision,
                        request_id="task3-" + str(self.sequence), **kwargs)

    def metadata(self, number=1, version=1, abstract=None, references=None):
        identifier = "arxiv:2601.%05dv%d" % (number, version)
        text = abstract if abstract is not None else "Source %d studies bounded sequences and reports a finite example." % number
        self.sequence += 1
        import_response(self.store, "arxiv", atom([entry(identifier[6:], abstract=text)], total=1),
                        source_url="https://export.arxiv.org/api/query?id_list=" + identifier[6:],
                        captured_at="2026-09-07T12:00:00Z", expected_revision=self.store.revision,
                        request_id="metadata-" + str(self.sequence))
        if references is not None:
            raw = {"id": identifier, "title": "Authored references", "references": references}
            self.sequence += 1
            import_response(self.store, "mcp", json.dumps(raw).encode(), source_url="https://example.org/tool",
                            captured_at="2026-09-07T12:00:00Z", media_type="application/json",
                            mappings=[{"id": "/id", "title": "/title", "references": "/references"}],
                            expected_revision=self.store.revision, request_id="refs-" + str(self.sequence))
        return identifier

    def cohort(self, numbers=(1, 2, 3)):
        http, _, _ = client([xml_response(atom([entry("2601.%05dv1" % n,
            abstract="Source %d studies bounded sequences and reports a finite example." % n) for n in numbers], total=len(numbers)))])
        self.sequence += 1
        result = collect_cohort(self.store, {"corpus": "arxiv", "primaryCategory": "cs.LG",
            "windowStart": "2026-01-01", "windowEnd": "2026-01-31"}, http=http,
            expected_revision=self.store.revision, request_id="cohort-" + str(self.sequence))
        return result["collection_id"]

    def capture(self, identifier, body="The finite example holds only for bounded inputs. References: none.",
                abstract=True, status=200, pdf_text=None):
        if pdf_text is None:
            abstract_text = abstract if isinstance(abstract, str) else self.artifacts.read(
                self.store.snapshot()["records"]["work"][identifier]["abstract"]).decode() if abstract else ""
            abstract_html = '<div class="abstract">' + escape(abstract_text) + '</div>' if abstract else ""
            data = ('<article>' + abstract_html + '<section class="article-body"><p>' + body + '</p></section></article>').encode()
            media = "text/html"
        else:
            data = b"%PDF-1.4\n% Authored fixture for injected extraction.\n%%EOF"
            media = "application/pdf"
        http, _, _ = client([(status, {"Content-Type": media}, data)], max_retries=0)
        self.sequence += 1
        result = acquire_fulltext(self.store, identifier, "https://arxiv.org/" + ("pdf/" if pdf_text else "html/") + identifier[6:],
            http=http, extractor=(lambda data: {"status": "extracted", "text": pdf_text}) if pdf_text else None,
            expected_revision=self.store.revision, request_id="body-" + str(self.sequence))
        return result["capture"]

    def span(self, artifact, quote=None):
        text = self.artifacts.read(artifact).decode()
        quote = text if quote is None else quote
        start = text.index(quote)
        return {"kind": "text", "start": start, "end": start + len(quote), "quote": quote}

    def link(self, identifier, source_id, artifact, quote=None):
        return {"version_id": identifier, "source_id": source_id, "artifact": artifact,
                "locator": self.span(artifact, quote)}

    def abstract_note(self, identifier, note_id="abstract", absent=False):
        item = self.store.snapshot()["records"]["work"][identifier]["abstracts"][0]
        link = self.link(identifier, item["source_id"], item["artifact"])
        return {"id": note_id, "version_id": identifier, "depth": "abstract",
                "inspections": [{"unit_id": None, "link": link, "note": "Read the complete saved abstract for " + identifier}],
                "notes": {field: {"text": ("The abstract does not report " + field + ".") if absent else
                           ("The authored source discusses " + field + " for bounded sequences."),
                           "status": "absent" if absent else "present", "inspections": [0]} for field in FIELDS}}

    def bundle(self, identifier, capture=None, bundle_id="bundle", entries=(), complete=True):
        capture = capture or self.capture(identifier)
        link = self.link(identifier, capture["source_id"], capture["text"])
        bibliography = self.link(identifier, capture["source_id"], capture["text"],
                                 "References:" + self.artifacts.read(capture["text"]).decode().split("References:", 1)[1])
        units = [{"id": "body", "kind": "text", "required": True, "link": link},
                 {"id": "bibliography", "kind": "bibliography", "required": True, "link": bibliography}]
        if capture["includes_abstract"] is True:
            abstract_text = self.artifacts.read(capture["text"]).decode().split("\n", 1)[0]
            units.append({"id": "abstract", "kind": "abstract", "required": True,
                          "link": self.link(identifier, capture["source_id"], capture["text"], abstract_text)})
        return {"id": bundle_id, "version_id": identifier, "source_id": capture["source_id"],
                "scope": "article", "completeness": "complete" if complete else "partial", "units": units,
                "inventory": {"text": "The article contains the listed text and bibliography; no other material is required.",
                              "links": [link]},
                "bibliography": {"complete": complete, "unit_id": "bibliography", "entries": list(entries)},
                "resolutions": []}

    def full_note(self, bundle, note_id="full", omit=()):
        inspections = [{"unit_id": u["id"], "link": copy.deepcopy(u["link"]),
                        "note": "Inspected the source unit " + u["id"]} for u in bundle["units"]
                       if u["id"] not in omit and u["link"] is not None]
        return {"id": note_id, "version_id": bundle["version_id"], "depth": "fulltext", "bundle_id": bundle["id"],
                "inspections": inspections,
                "notes": {field: {"text": "The bounded example provides the article's " + field + ".",
                                   "status": "present", "inspections": [0]} for field in FIELDS}}

    def scope(self, roots, collections=(), profile="research", **kwargs):
        from research_harness.graph import set_roots
        return self.mutate(set_roots, dict(profile=profile, roots=roots, collection_ids=list(collections), **kwargs))

    def store_obligations(self, profile="research"):
        from research_harness.literature import foundation_report
        return foundation_report(self.store, profile)["obligations"]

    def codes(self, profile="research"):
        from research_harness.literature import foundation_report
        return {item["code"] for item in foundation_report(self.store, profile)["obligations"]}

    def assert_error(self, code, action):
        from research_harness.errors import ResearchError
        with self.assertRaises(ResearchError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
