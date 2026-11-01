"""Complete source representations remain delivered after exact-byte deduplication."""

import json
import unittest
from unittest.mock import patch

import test_problem_openness_native_reviews as native_fixtures
import test_research_problem_openness_minimum as minimum_fixtures
from research_harness import openness_reading, problem_openness, review_transport
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.literature import import_bundle
from research_harness.reading import record_reading


class SourceDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.case = native_fixtures.NativeOpennessReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        f = self.case
        self.work = f.metadata()
        abstract = f.artifacts.read(f.store.snapshot()["records"]["work"][self.work]["abstract"]).decode()
        self.pdf_text = abstract + "\nComplete authored source λ. " * 24 + "\nReferences: none.\f"
        self.html = f.capture(self.work, body="Alternative complete authored source. " * 35 + "References: none.")
        self.pdf = f.capture(self.work, pdf_text=self.pdf_text)
        self.readings = []
        for name, capture in [("html", self.html), ("pdf", self.pdf)]:
            bundle = f.bundle(self.work, capture, bundle_id=name + "-bundle")
            def spans(value):
                if isinstance(value, dict):
                    if "locator" in value and value["locator"].get("kind") == "text":
                        value["locator"] = f.span(value["artifact"], value["locator"]["quote"], "span")
                    for item in value.values():
                        spans(item)
                elif isinstance(value, list):
                    for item in value:
                        spans(item)
            spans(bundle)
            f.mutate(import_bundle, bundle)
            f.mutate(record_reading, f.full_note(bundle, name + "-reading"))
            self.readings.append(f.store.snapshot()["records"]["reading"][name + "-reading"])
        f.search = minimum_fixtures.ProblemOpennessMinimumTests.search(f, [self.work])
        self.prefix = "Required native study α.\n".encode()
        data = f.artifacts.read(self.pdf["text"])
        f.proof = f.artifacts.put(self.prefix + data + b"\nEnd of native study.\n", "text/plain")
        manifest = dict(f.manifest, artifacts=[{"path": "attempt/proof.md", "digest": f.proof["sha256"], "role": "proof"}])
        f.replace_manifest(manifest, identifier="contained-proof")

    def export(self, identifier="source-openness", *, support="contained-proof", priority=False):
        f = self.case
        if not priority:
            return f.export(assignment=identifier, support=support)
        spec = {"assignment_id": identifier, "reviewer_id": "openness-assessor",
            "minimum": {"searches": [{"formulation": f.selected["statement"], "search_id": f.search["id"]}],
                        "settling_work_id": None, "duplicate_resolutions": [], "text_priority": [self.work]},
            "native_reviews": [{"attempt_id": f.attempt["id"], "assignment_id": support}]}
        exported = problem_openness.export_openness(f.store, spec, f.root / ("packet-" + identifier))
        return json.loads(f.artifacts.read(exported["native_packet"]))

    def delivery(self, packet):
        self.assertIn("openness_delivery", packet["scientific_context"],
                      "The exporter needs a canonical source-representation and exact-containment contract")
        return packet["scientific_context"]["openness_delivery"]

    def request(self, packet, identifier):
        f = self.case
        observed = f.observe_packet(packet, identifier, invoke=False)
        assignment = observed["assignment"]
        outer = json.loads(f.artifacts.read(assignment["packet"]))
        route = f.store.snapshot()["records"]["review_route"][assignment["route_id"]]
        return review_transport.request_body(route, assignment["prompt"]["text"], outer)

    def supplied(self, request):
        content = request["input"][-1]["content"]
        if isinstance(content, list):
            content = next(part["text"] for part in content if part["type"] == "input_text")
        return json.loads(content.removeprefix(review_transport.PACKET_LABEL))

    def test_selects_complete_main_and_preserves_all_readings_and_proof_bytes(self):
        f = self.case
        packet = self.export(priority=True)
        self.assertEqual(packet["scientific_context"]["text_delivery_priority"],
                         sorted(capture["text"]["sha256"] for capture in (self.html, self.pdf)))
        delivery = self.delivery(packet)
        self.assertEqual([row["text_sha256"] for row in
                          openness_reading.list_reading_texts(f.store.snapshot()["records"], packet)],
                         [self.pdf["text"]["sha256"]])
        self.assertEqual(delivery["schema_version"], 1)
        self.assertEqual(delivery["sources"][0]["selected_source_id"], self.pdf["source_id"])
        self.assertEqual(set(packet["scientific_context"]["openness_minimum"]["reading_digests"]),
                         {digest(row) for row in self.readings})
        [contained] = delivery["contained_sources"]
        self.assertEqual(contained, {"sha256": self.pdf["text"]["sha256"], "container_sha256": f.proof["sha256"],
            "byte_start": len(self.prefix), "byte_end": len(self.prefix) + self.pdf["text"]["size"]})
        saved = f.record_openness(packet, "source-openness")
        self.assertEqual(saved["openness_delivery"], delivery)
        self.assertEqual(f.assessment(), [])
        attempt = f.store.snapshot()["records"]["review_attempt"]["observed-source-openness"]
        supplied = self.supplied(json.loads(f.artifacts.read(attempt["request"])))
        entries = {row["artifact"]["sha256"]: row for row in supplied["evidence"]}
        self.assertEqual(entries[f.proof["sha256"]]["content"].encode(), f.artifacts.read(f.proof))
        self.assertEqual(entries[self.pdf["text"]["sha256"]]["content"], "")
        self.assertEqual(entries[self.pdf["text"]["sha256"]]["delivery"], "complete-source-contained-in-required-proof")
        for reference in (self.html["original"], self.html["text"]):
            self.assertEqual(entries[reference["sha256"]]["delivery"], "alternate-main-representation")
        self.assertEqual(supplied["native"]["scientific_context"]["openness_delivery"], delivery)

    def test_false_offsets_hash_and_cyclic_containment_are_refused(self):
        f = self.case
        for mode in ("offset", "hash", "cycle", "proof-target"):
            with self.subTest(mode=mode):
                identifier = "bad-" + mode
                packet = self.export(identifier)
                delivery = self.delivery(packet)
                contained = delivery["contained_sources"][0]
                if mode == "offset":
                    contained["byte_start"] += 1
                elif mode == "hash":
                    contained["sha256"] = "d" * 64
                elif mode == "cycle":
                    contained["container_sha256"] = contained["sha256"]
                else:
                    contained["sha256"] = f.proof["sha256"]
                with self.assertRaises(ResearchError) as raised:
                    self.request(packet, identifier)
                self.assertEqual(raised.exception.code, "openness_source_delivery_mismatch")

    def test_missing_or_changed_actual_container_cannot_supply_a_contained_source(self):
        f = self.case
        packet = self.export()
        self.delivery(packet)
        original = review_transport.request_body
        for altered in (False, True):
            identifier = "container-" + str(altered)
            packet = self.export(identifier)
            def changed(route, prompt, outer, history=()):
                request = original(route, prompt, outer, history)
                if outer.get("native", {}).get("reviewer", {}).get("attestation_id") != "review-assignment:" + identifier:
                    return request
                content = request["input"][-1]["content"]
                text_part = None if isinstance(content, str) else next(part for part in content if part["type"] == "input_text")
                supplied = self.supplied(request)
                entry = next(row for row in supplied["evidence"] if row["artifact"]["sha256"] == f.proof["sha256"])
                entry["content"] = "Changed required study" if altered else ""
                rendered = review_transport.PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
                if text_part is None:
                    request["input"][-1]["content"] = rendered
                else:
                    text_part["text"] = rendered
                return request
            with self.subTest(altered=altered), patch.object(review_transport, "request_body", side_effect=changed):
                f.assert_error("openness_native_evidence_undelivered", lambda: f.record_openness(packet, identifier))

    def test_new_source_capture_stales_selection_without_erasing_prior_readings(self):
        f = self.case
        packet = self.export()
        self.delivery(packet)
        f.record_openness(packet, "source-openness")
        f.capture(self.work, body="A newly held distinct complete body. References: none.")
        self.assertEqual(f.assessment()[0]["code"], "problem_openness_stale")
        self.assertTrue({row["id"] for row in self.readings} <= set(f.store.snapshot()["records"]["reading"]))

    def test_unverified_source_cannot_be_selected_or_hidden_as_alternative(self):
        f = self.case
        packet = self.export()
        self.delivery(packet)
        records = f.store.snapshot()["records"]
        source = records["source"][self.html["source_id"]]
        f.put("source", source["id"], dict(source, origin_verified=False))
        with self.assertRaises(ResearchError):
            self.export("unverified")

    def test_no_containment_is_inferred_from_normalized_or_similar_text(self):
        f = self.case
        f.proof = f.artifacts.put(b"Required study.\n" + f.artifacts.read(self.pdf["text"]).replace(b"\n", b" "), "text/plain")
        f.replace_manifest(dict(f.manifest, artifacts=[{"path": "attempt/proof.md", "digest": f.proof["sha256"], "role": "proof"}]),
                           identifier="similar-proof")
        packet = f.export(assignment="similar", support="similar-proof")
        delivery = self.delivery(packet)
        self.assertEqual(delivery["contained_sources"], [])
        supplied = self.supplied(self.request(packet, "similar"))
        entry = next(row for row in supplied["evidence"] if row["artifact"]["sha256"] == self.pdf["text"]["sha256"])
        self.assertEqual(entry["content"].encode(), f.artifacts.read(self.pdf["text"]))

    def standalone_packet(self, identifier):
        f = self.case
        f.proof = f.artifacts.put(b"Independent complete required proof.\n", "text/plain")
        f.replace_manifest(dict(f.manifest, artifacts=[{"path": "attempt/proof.md", "digest": f.proof["sha256"], "role": "proof"}]),
                           identifier="standalone-proof")
        return self.export(identifier, support="standalone-proof", priority=True)

    def test_fitting_never_elides_required_proof_or_selected_standalone_text(self):
        f = self.case
        packet = self.standalone_packet("protected")
        self.assertEqual(packet["scientific_context"]["text_delivery_priority"],
                         sorted(capture["text"]["sha256"] for capture in (self.html, self.pdf)))
        packet["scientific_context"]["text_delivery_priority"].append(f.proof["sha256"])
        # This fixture uses the Responses route. Exercise the local fitting routine on its actual supplied packet.
        supplied = self.supplied(self.request(packet, "protected"))
        request = review_transport._fit_local_input({"model": "test", "configuration": {"max_output_tokens": 100}},
            "Return JSON.", supplied, [], 1, lambda value: json.dumps(value))
        entries = {row["artifact"]["sha256"]: row for row in self.supplied(request)["evidence"]}
        for reference in (self.pdf["text"], f.proof):
            self.assertEqual(entries[reference["sha256"]]["content"].encode(), f.artifacts.read(reference))
        for reference in (self.html["original"], self.html["text"]):
            self.assertEqual(entries[reference["sha256"]]["delivery"], "alternate-main-representation")

    def test_actual_selected_standalone_text_tampering_is_rejected(self):
        f = self.case
        packet = self.standalone_packet("text-tampered")
        original = review_transport.request_body
        def changed(route, prompt, outer, history=()):
            request = original(route, prompt, outer, history)
            if outer.get("native", {}).get("reviewer", {}).get("attestation_id") != "review-assignment:text-tampered":
                return request
            supplied = self.supplied(request)
            entry = next(row for row in supplied["evidence"] if row["artifact"]["sha256"] == self.pdf["text"]["sha256"])
            entry["content"] = "A changed source body."
            content = request["input"][-1]["content"]
            rendered = review_transport.PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
            if isinstance(content, str):
                request["input"][-1]["content"] = rendered
            else:
                next(part for part in content if part["type"] == "input_text")["text"] = rendered
            return request
        with patch.object(review_transport, "request_body", side_effect=changed):
            f.assert_error("openness_source_delivery_mismatch", lambda: f.record_openness(packet, "text-tampered"))

    def test_legacy_packet_without_delivery_keeps_all_source_bodies(self):
        f = self.case
        packet = self.export("legacy")
        packet["scientific_context"].pop("openness_delivery")
        f.record_openness(packet, "legacy")
        self.assertEqual(f.assessment(), [])
        attempt = f.store.snapshot()["records"]["review_attempt"]["observed-legacy"]
        entries = {row["artifact"]["sha256"]: row for row in self.supplied(json.loads(f.artifacts.read(attempt["request"])))["evidence"]}
        for reference in (self.html["original"], self.html["text"], self.pdf["text"], f.proof):
            self.assertEqual(entries[reference["sha256"]]["content"].encode(), f.artifacts.read(reference))

    def test_different_or_unobserved_versions_cannot_be_merged(self):
        f = self.case
        source = f.store.snapshot()["records"]["source"][self.html["source_id"]]
        for observed in (self.work[:-1] + "2", None):
            with self.subTest(observed=observed):
                f.put("source", source["id"], dict(source, observed_identifier=observed))
                with self.assertRaises(ResearchError):
                    self.export("different-version")
        f.put("source", source["id"], source)
        packet = self.export("map-version")
        self.delivery(packet)["sources"][0]["representations"][0]["version_id"] = self.work[:-1] + "2"
        f.assert_error("openness_source_delivery_mismatch", lambda: self.request(packet, "map-version"))

    def test_required_supplement_is_not_an_alternate_main_body(self):
        f = self.case
        bundle = f.bundle(self.work, self.pdf, bundle_id="pdf-with-supplement")
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True,
            "link": f.link(self.work, self.html["source_id"], self.html["text"], kind="span")})
        f.mutate(import_bundle, bundle)
        f.mutate(record_reading, f.full_note(bundle, "supplement-read"))
        packet = self.export("supplement")
        delivery = self.delivery(packet)
        for reference in (self.html["original"], self.html["text"]):
            self.assertNotIn(reference["sha256"], delivery["alternate_body_digests"])
        entries = {row["artifact"]["sha256"]: row for row in self.supplied(self.request(packet, "supplement"))["evidence"]}
        for reference in (self.html["original"], self.html["text"]):
            self.assertEqual(entries[reference["sha256"]]["content"].encode(), f.artifacts.read(reference))

    def test_actual_delivery_contract_must_match_the_immutable_packet(self):
        f = self.case
        packet = self.export("changed-map")
        original = review_transport.request_body
        def changed(route, prompt, outer, history=()):
            request = original(route, prompt, outer, history)
            if outer.get("native", {}).get("reviewer", {}).get("attestation_id") != "review-assignment:changed-map":
                return request
            supplied = self.supplied(request)
            supplied["native"]["scientific_context"]["openness_delivery"]["contained_sources"][0]["byte_start"] += 1
            content = request["input"][-1]["content"]
            rendered = review_transport.PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
            if isinstance(content, str):
                request["input"][-1]["content"] = rendered
            else:
                next(part for part in content if part["type"] == "input_text")["text"] = rendered
            return request
        with patch.object(review_transport, "request_body", side_effect=changed):
            f.assert_error("openness_native_evidence_undelivered", lambda: f.record_openness(packet, "changed-map"))

    def test_explicit_required_visual_keeps_its_original_and_text(self):
        f = self.case
        table = "<table><tr><td>Complete static table.</td></tr></table>"
        visual = f.capture(self.work, body="A distinct complete source body. " + table + " References: none.")
        bundle = f.bundle(self.work, visual, bundle_id="html-with-table")
        link = {"version_id": self.work, "source_id": visual["source_id"], "artifact": visual["original"],
                "locator": {"kind": "html", "anchor": f.span(visual["original"], table, "span"), "assets": []}}
        bundle["units"].append({"id": "table", "kind": "table", "required": True, "link": link})
        f.mutate(import_bundle, bundle)
        f.mutate(record_reading, f.full_note(bundle, "table-read"))
        packet = self.export("table")
        delivery = self.delivery(packet)
        self.assertEqual(delivery["sources"][0]["selected_source_id"], self.pdf["source_id"])
        for reference in (visual["original"], visual["text"]):
            self.assertIn(reference["sha256"], delivery["protected_source_digests"])
            self.assertNotIn(reference["sha256"], delivery["alternate_body_digests"])
        entries = {row["artifact"]["sha256"]: row for row in self.supplied(self.request(packet, "table"))["evidence"]}
        for reference in (visual["original"], visual["text"]):
            self.assertEqual(entries[reference["sha256"]]["content"].encode(), f.artifacts.read(reference))
