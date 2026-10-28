"""Run outputs retain their exact identity when cited as binary result evidence."""

import base64
import copy
import hashlib
import re
import unittest
from unittest import mock

from development_fixtures import DevelopmentCase, PROGRAM
from integration_fixtures import admit_lab
from research_harness import execution, source_links
from research_harness.errors import ResearchError
from test_research_binary_pdf import authored_pdf


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="12">Maximum: 9</text></svg>'


def create_pdf_with_page_count_in_title():
    """A real one-page PDF whose multiline title resembles an extra pdfinfo page count."""
    original = authored_pdf()
    body = bytearray(original[:original.index(b"xref\n")])
    body.extend(b"6 0 obj\n<< /Title (Authored title\\nPages: 999) >>\nendobj\n")
    offsets = [match.start() for match in re.finditer(rb"(?m)^\d+ 0 obj\n", body)]
    xref = len(body)
    body.extend(b"xref\n0 7\n0000000000 65535 f \n")
    for offset in offsets:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(b"trailer\n<< /Size 7 /Root 1 0 R /Info 6 0 R >>\nstartxref\n"
                + str(xref).encode() + b"\n%%EOF\n")
    return bytes(body)


class BinaryResultTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.result_locator = {"kind": "artifact"}

    def build_result_evidence(self, outcome, validation=False):
        if validation:
            return super().result_evidence(outcome, True)
        output = outcome["outputs"][0]
        return {"kind": "result", "execution_id": outcome["id"], "output_id": output["id"],
                "artifact": copy.deepcopy(output["artifact"]), "locator": copy.deepcopy(self.result_locator)}

    # Keep the inherited fixture hook while giving the helper a verb.
    result_evidence = build_result_evidence

    def record_output(self, data, media_type):
        """Produce real output through the historical admission path, without requiring a new binding."""
        plan = self.plan()
        self.mutate(self.development().plan_cycle, plan)
        program = (PROGRAM + b"\nfrom pathlib import Path\n"
                   + ("Path(__file__).with_suffix('.out').write_bytes(" + repr(data) + ")\n").encode())
        admission = self.admit(program_data=program)
        outcome = self.execution(admission)
        output_path = self.root / "research/fixture-runs/run-1.out"
        self.assertEqual(output_path.read_bytes(), data)
        outcome["outputs"][0]["artifact"] = self.artifacts.put(output_path.read_bytes(), media_type)
        self.mutate(self.development().record_execution, outcome)
        return plan, outcome

    def assess(self, plan, outcome, identifier="assessment-1"):
        return self.mutate(self.development().assess_cycle,
                           self.assessment(plan, outcome, identifier))["result"]

    def assert_validated(self, plan, outcome, identifier="assessment-1"):
        report = self.assess(plan, outcome, identifier)
        self.assertTrue(report["validated_result"], report["obligations"])
        self.assertEqual(report["payload"]["result"]["evidence"], [self.build_result_evidence(outcome)])
        return report

    def test_pdf_artifact_is_a_validated_result_of_its_own_run(self):
        self.assert_validated(*self.record_output(authored_pdf(), "application/pdf"))

    def test_png_artifact_is_a_validated_result_of_its_own_run(self):
        self.assert_validated(*self.record_output(PNG, "image/png"))

    def test_svg_artifact_is_a_validated_result_of_its_own_run(self):
        self.assert_validated(*self.record_output(SVG, "image/svg+xml"))

    def test_managed_launcher_binds_and_assesses_any_result_media_type(self):
        body = ("from pathlib import Path\nPath('results/result.png').write_bytes(" + repr(PNG) + ")\n"
                + PROGRAM.decode().replace('"result":', '"metric": 9, "result":'))
        outputs = [{"id": "result", "requirement_id": "measurements", "path": "results/result.png",
                    "media_type": "image/png"},
                   {"id": "validation", "requirement_id": "checks", "path": "stdout",
                    "media_type": "application/json"}]
        admission = admit_lab(self, body=body, outputs=outputs)
        launched = execution.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                              request_id="binary-launch")
        self.assertTrue(launched["ok"])
        records = self.store.snapshot()["records"]
        outcome_id = records["execution_outcome"][admission["id"]]["execution_id"]
        outcome = records["execution"][outcome_id]["payload"]
        self.assertEqual(self.artifacts.read(outcome["outputs"][0]["artifact"]), PNG)
        self.assert_validated(records["cycle_plan"][outcome["cycle_id"]]["payload"], outcome)

    def test_pdf_locator_uses_the_outputs_own_page_count_without_a_source_capture(self):
        plan, outcome = self.record_output(authored_pdf(), "application/pdf")
        self.result_locator = {"kind": "pdf", "page_index": 0, "printed_page": "99", "region": [0, 0, 1, 1]}
        self.assert_validated(plan, outcome)

    def test_result_pdf_media_spellings_keep_the_outputs_own_page_bounds(self):
        locator = {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}
        for media in ("application/pdf", "Application/PDF", "APPLICATION/PDF; version=1.4"):
            artifact = self.artifacts.put(authored_pdf(), media)
            with self.subTest(media=media):
                source_links.read_locator(self.artifacts, artifact, locator, result=True)
                with self.assertRaises(ResearchError) as refused:
                    source_links.read_locator(self.artifacts, artifact, dict(locator, page_index=1), result=True)
                self.assertEqual(refused.exception.code, "invalid_locator")
                self.assertEqual(artifact["media_type"], media)

    def test_pdf_page_outside_the_output_is_refused(self):
        plan, outcome = self.record_output(authored_pdf(), "application/pdf")
        self.result_locator = {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}
        self.assert_validated(plan, outcome)
        for page_index in (-1, 1, True):
            with self.subTest(page_index=page_index):
                self.result_locator["page_index"] = page_index
                before = self.store.snapshot()
                with self.assertRaises(ResearchError) as raised:
                    self.assess(plan, outcome, "outside-page")
                self.assertEqual(raised.exception.code, "invalid_locator")
                self.assertIn("within the saved document", raised.exception.message)
                self.assertEqual(self.store.snapshot(), before)

    def test_pdf_regions_keep_the_existing_bounds(self):
        plan, outcome = self.record_output(authored_pdf(), "application/pdf")
        self.result_locator = {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}
        self.assert_validated(plan, outcome)
        for region in ([0, 0, 0, 1], [0, 0, 2, 1], [-1, 0, 1, 1], [False, 0, 1, 1]):
            with self.subTest(region=region):
                self.result_locator["region"] = region
                with self.assertRaises(ResearchError) as raised:
                    self.assess(plan, outcome, "outside-region")
                self.assertEqual(raised.exception.code, "invalid_locator")

    def test_pdf_metadata_cannot_extend_the_output_page_count(self):
        plan, outcome = self.record_output(create_pdf_with_page_count_in_title(), "application/pdf")
        self.assert_validated(plan, outcome)
        self.result_locator = {"kind": "pdf", "page_index": 1, "printed_page": None, "region": [0, 0, 1, 1]}
        with self.assertRaises(ResearchError) as raised:
            self.assess(plan, outcome, "metadata-page-count")
        self.assertEqual(raised.exception.code, "invalid_locator")
        self.assertIn("page count", raised.exception.message)

    def test_pdfinfo_failure_cannot_establish_output_page_bounds(self):
        plan, outcome = self.record_output(authored_pdf(), "application/pdf")
        self.result_locator = {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}
        self.assert_validated(plan, outcome)
        with mock.patch("shutil.which", return_value=None), self.assertRaises(ResearchError) as raised:
            self.assess(plan, outcome, "missing-pdfinfo")
        self.assertEqual(raised.exception.code, "invalid_locator")
        self.assertIn("page count", raised.exception.message)

    def test_malformed_pdf_can_be_a_whole_artifact_but_has_no_valid_page(self):
        plan, outcome = self.record_output(b"%PDF-1.4\nnot a document\n%%EOF\n", "application/pdf")
        self.assert_validated(plan, outcome)
        self.result_locator = {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}
        with self.assertRaises(ResearchError) as raised:
            self.assess(plan, outcome, "malformed-pdf")
        self.assertEqual(raised.exception.code, "invalid_locator")
        self.assertIn("page count", raised.exception.message)

    def assert_reference_mismatch(self, field, changed):
        plan, outcome = self.record_output(PNG, "image/png")
        self.assert_validated(plan, outcome)
        forged = copy.deepcopy(outcome)
        forged["outputs"][0]["artifact"][field] = changed
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as raised:
            self.assess(plan, forged, "forged-reference")
        self.assertEqual(raised.exception.code, "result_evidence_mismatch")
        self.assertEqual(self.store.snapshot(), before)

    def test_artifact_locator_must_match_the_execution_output_hash(self):
        self.assert_reference_mismatch("sha256", "0" * 64)

    def test_artifact_locator_must_match_the_execution_output_size(self):
        self.assert_reference_mismatch("size", len(PNG) + 1)

    def assert_corrupt_output(self, replacement):
        plan, outcome = self.record_output(PNG, "image/png")
        self.assert_validated(plan, outcome)
        stored = self.root / outcome["outputs"][0]["artifact"]["path"]
        stored.chmod(0o644)
        stored.write_bytes(replacement)
        with self.assertRaises(ResearchError) as raised:
            self.assess(plan, outcome, "corrupt-output")
        self.assertEqual(raised.exception.code, "artifact_corrupt")

    def test_artifact_locator_verifies_stored_output_hash(self):
        self.assert_corrupt_output(b"X" + PNG[1:])

    def test_artifact_locator_verifies_stored_output_size(self):
        self.assert_corrupt_output(PNG[:-1])

    def test_whole_artifact_still_needs_a_result_bound_validation_check(self):
        plan, outcome = self.record_output(PNG, "image/png")
        self.assert_validated(plan, outcome)
        value = self.assessment(plan, outcome, "unresolved-check")
        value["validity_checks"][0]["status"] = "unresolved"
        report = self.mutate(self.development().assess_cycle, value)["result"]
        self.assertFalse(report["validated_result"])
        self.assertIn("validity_unresolved", {item["code"] for item in report["obligations"]})

    def test_artifact_locator_has_no_unchecked_selector_fields(self):
        plan, outcome = self.record_output(PNG, "image/png")
        self.assert_validated(plan, outcome)
        self.result_locator["page_index"] = 999
        with self.assertRaises(ResearchError) as raised:
            self.assess(plan, outcome, "extra-field")
        self.assertEqual(raised.exception.code, "invalid_locator")

    def test_artifact_locator_is_for_results_and_does_not_establish_source_reading(self):
        plan, outcome = self.record_output(PNG, "image/png")
        self.assert_validated(plan, outcome)
        with self.assertRaises(ResearchError) as raised:
            source_links.read_locator(self.artifacts, outcome["outputs"][0]["artifact"], {"kind": "artifact"})
        self.assertEqual(raised.exception.code, "invalid_locator")

    def test_existing_text_output_keeps_its_span_and_can_also_cite_the_whole_artifact(self):
        data = b"The maximum square in the admitted range is 9.\n"
        plan, outcome = self.record_output(data, "text/plain")
        self.result_locator = source_links.span_locator(data.decode(), 0, len(data))
        legacy = self.assert_validated(plan, outcome, "legacy-text")
        self.result_locator = {"kind": "artifact"}
        self.assert_validated(plan, outcome, "whole-text-output")
        context = self.development()._Context(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(context.assess_record("legacy-text"), legacy)

    def test_existing_text_span_and_json_results_keep_their_exact_checks(self):
        data = b'{"result": {"bound": 9}, "validation": {"passed": true}}'
        plan, outcome = self.record_output(data, "application/json")
        content = data.decode()
        locators = [
            {"kind": "text", "start": 0, "end": len(content), "quote": content},
            {"kind": "span", "start": 0, "end": len(content), "sha256": hashlib.sha256(data).hexdigest()},
            {"kind": "json", "pointer": "/result/bound", "value": 9},
        ]
        for index, locator in enumerate(locators):
            with self.subTest(kind=locator["kind"]):
                self.result_locator = locator
                report = self.assert_validated(plan, outcome, "legacy-" + str(index))
                invalid = copy.deepcopy(report["payload"])
                invalid["id"] = "invalid-legacy-" + str(index)
                cited = invalid["result"]["evidence"][0]["locator"]
                key = {"text": "quote", "span": "sha256", "json": "value"}[locator["kind"]]
                cited[key] = {"quote": "wrong text", "sha256": "0" * 64, "value": 10}[key]
                with self.assertRaises(ResearchError) as raised:
                    self.mutate(self.development().assess_cycle, invalid)
                self.assertEqual(raised.exception.code, "invalid_locator")
        self.result_locator = {"kind": "artifact"}
        self.assert_validated(plan, outcome, "whole-json-output")
        records = self.store.snapshot()["records"]
        context = self.development()._Context(records, self.artifacts)
        for index in range(len(locators)):
            report = context.assess_record("legacy-" + str(index))
            self.assertTrue(report["validated_result"])


if __name__ == "__main__":
    unittest.main()
