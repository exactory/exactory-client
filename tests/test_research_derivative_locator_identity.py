"""A JSON-locator derivative stores each selected value once, under its locator's identity."""

import json
from pathlib import Path
import tempfile
import unittest

from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.scientific_delivery import encode_delivery, project_delivery


class DerivativeLocatorIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))

    def test_json_locator_values_are_stored_once_and_still_map(self):
        population = {"rows": [{"index": i, "value": i / 7} for i in range(2000)], "summary": {"count": 2000}}
        ref = self.artifacts.put(json.dumps(population, indent=2).encode(), "application/json")
        whole = {"kind": "json", "pointer": "", "value": population}
        summary = {"kind": "json", "pointer": "/summary", "value": {"count": 2000}}
        contract = {"payload": {"scientific_delivery": [{"original_sha256": ref["sha256"], "disposition": "project",
            "projection": {"kind": "json_locators", "context": "Complete synthetic population.",
                           "locators": [whole, summary]}}]}}
        packet = {"whole": {"artifact": ref, "locator": whole}, "summary": {"artifact": ref, "locator": summary}}
        manifest, derived = project_delivery({}, self.artifacts, contract, packet)
        data = derived[manifest["whole"]["artifact"]["path"]]
        derivative = json.loads(data)
        values = len(encode_delivery(population)) + len(encode_delivery({"count": 2000}))
        self.assertLess(len(data), values + 2048)
        for entry, locator in zip(derivative["entries"], (whole, summary)):
            self.assertEqual(entry["original_locator"],
                             {"kind": "json", "pointer": locator["pointer"], "locator_digest": digest(locator)})
            self.assertEqual(entry["value"], locator["value"])
        self.assertEqual([item["original_locator"] for item in derivative["locator_mapping"]],
                         [entry["original_locator"] for entry in derivative["entries"]])
        self.assertEqual([item["derived_pointer"] for item in derivative["locator_mapping"]],
                         ["/entries/0/value", "/entries/1/value"])
        self.assertEqual(manifest["whole"]["original_locator"], whole)
        self.assertEqual(manifest["whole"]["locator"], {"kind": "json", "pointer": "/entries/0/value", "value": population})
        self.assertEqual(manifest["summary"]["locator"], {"kind": "json", "pointer": "/entries/1/value", "value": {"count": 2000}})
        self.assertEqual(manifest["summary"]["artifact"], manifest["whole"]["artifact"])

    def test_a_reordered_manifest_locator_maps_by_digest(self):
        population = {"count": 3, "rows": [1, 2, 3]}
        ref = self.artifacts.put(json.dumps(population).encode(), "application/json")
        declared = {"kind": "json", "pointer": "", "value": population}
        reordered = {"value": {"rows": [1, 2, 3], "count": 3}, "pointer": "", "kind": "json"}
        contract = {"payload": {"scientific_delivery": [{"original_sha256": ref["sha256"], "disposition": "project",
            "projection": {"kind": "json_locators", "context": "Complete synthetic population.", "locators": [declared]}}]}}
        manifest, derived = project_delivery({}, self.artifacts, contract, {"whole": {"artifact": ref, "locator": reordered}})
        self.assertEqual(manifest["whole"]["locator"], {"kind": "json", "pointer": "/entries/0/value", "value": population})
        self.assertEqual(manifest["whole"]["original_locator"], reordered)
        derivative = json.loads(derived[manifest["whole"]["artifact"]["path"]])
        self.assertEqual(derivative["entries"][0]["original_locator"]["locator_digest"], digest(declared))
        self.assertEqual(digest(declared), digest(reordered))

    def test_text_span_locators_keep_their_identity_fields(self):
        text = "Observed 12 of 12 checks passed.\nRetained failure: none.\n"
        ref = self.artifacts.put(text.encode(), "text/plain")
        span = {"kind": "span", "start": 0, "end": len(text), "sha256": __import__("hashlib").sha256(text.encode()).hexdigest(),
                "excerpt": text[:40]}
        contract = {"payload": {"scientific_delivery": [{"original_sha256": ref["sha256"], "disposition": "project",
            "projection": {"kind": "text_spans", "context": "Complete log.", "locators": [span]}}]}}
        manifest, derived = project_delivery({}, self.artifacts, contract, {"log": {"artifact": ref, "locator": span}})
        derivative = json.loads(derived[manifest["log"]["artifact"]["path"]])
        self.assertEqual(derivative["entries"][0]["original_locator"], dict(span, locator_digest=digest(span)))
        self.assertEqual(derivative["entries"][0]["value"], text)
        self.assertEqual(manifest["log"]["original_locator"], span)
