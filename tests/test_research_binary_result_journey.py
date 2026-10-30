"""Binary result evidence survives observed review, scientific delivery and manuscript pinning."""

import base64
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

import integration_fixtures
from development_fixtures import DevelopmentCase
from managed_strategy_fixtures import ManagedStrategy, result_decision
from source_limited_fixtures import SourceLimitedCase
from research_harness import publication, review_delivery, review_input_projection, review_protocol, review_transport
from research_harness import scientific_delivery
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from test_research_binary_pdf import authored_pdf
from test_research_binary_results import PNG


class UnscopedBinaryResultJourneyTests(DevelopmentCase):
    def build_result_evidence(self, outcome, validation=False):
        if validation:
            return super().result_evidence(outcome, True)
        output = outcome["outputs"][0]
        return {"kind": "result", "execution_id": outcome["id"], "output_id": output["id"],
                "artifact": output["artifact"], "locator": {"kind": "artifact"}}

    result_evidence = build_result_evidence

    def run_binary_manuscript_journey(self, data, media, suffix):
        self.prepared_study()
        original_admit = integration_fixtures.admit_lab
        body = integration_fixtures.OBSERVED_PROGRAM + (
            "Path('results/result." + suffix + "').write_bytes(" + repr(data) + ")\n")
        outputs = [{"id": "result", "requirement_id": "measurements", "path": "results/result." + suffix,
                    "media_type": media},
                   {"id": "validation", "requirement_id": "checks", "path": "results/validation.json",
                    "media_type": "application/json"}]

        def admit_binary(case, *args, **kwargs):
            return original_admit(case, *args, **dict(kwargs, body=body, outputs=outputs))

        with patch.object(integration_fixtures, "admit_lab", side_effect=admit_binary):
            outcome = integration_fixtures.observed_candidate(self)
        reference = self.build_result_evidence(outcome)
        files = {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt",
                 "bibliography": "draft/references.bib", "claims": "evidence/claims.json", "sources": None}
        for kind, content in {"pdf": authored_pdf(), "abstract": b"The finite maximum is 9.",
                              "bibliography": b"Authored fixture references.",
                              "claims": b'[{"id":"bound","claim":"The finite maximum is 9."}]'}.items():
            path = self.root / files[kind]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        bundle = self.mutate(publication.prepare_publication, {"id": "binary-paper", "files": files,
            "claim_evidence": [{"claim_id": "bound", "evidence": [reference]}],
            "citation_accounting": integration_fixtures.account_fixture_citations(self)})["result"]
        review = integration_fixtures.build_manuscript_review(self, bundle, "binary-reviewer")
        self.mutate(publication.record_manuscript_review, review)
        records = self.store.snapshot()["records"]
        assignment = records["review_assignment"][review["assignment_id"]]
        attempt = next(a for a in records["review_attempt"].values() if a["assignment_id"] == assignment["id"])
        self.assertEqual(attempt["status"], "completed")
        request = json.loads(self.artifacts.read(attempt["request"]))
        content = review_transport.logical_messages(request)[-1]["content"]
        if suffix == "npy":
            text = content if isinstance(content, str) else content[0]["text"]
            actual = json.loads(text[len(review_transport.PACKET_LABEL):])
            attached = {} if isinstance(content, str) else review_input_projection.collect_attachments(content)
            review_input_projection.project_evidence(actual, attached)
            entry = next(row for row in actual["evidence"] if row["artifact"] == reference["artifact"])
            self.assertEqual(entry["delivery"], "inline-result-bytes")
            self.assertEqual(base64.b64decode(entry["content"]), data)
        else:
            attached = review_input_projection.collect_attachments(content)
            self.assertEqual(base64.b64decode(review_input_projection.find_attachment(attached, reference["artifact"])), data)
        self.assertEqual(records["execution_binding"]["lab-run"]["outputs"][0]["media_type"], media)
        self.assertTrue(records["cycle_assessment"]["assessment-1"]["assessment"]["validated_result"])
        self.assertEqual(publication._bundle(records, self.artifacts)["digest"], bundle["digest"])
        observation = records["execution_observation"]["lab-run"]
        self.assertIn(reference["artifact"], [entry["artifact"] for entry in observation["files"]])
        from codex_route_fixtures import invoke_codex, read_sent_packet, record_codex_route
        record_codex_route(self, "binary-text-route")
        self.mutate(review_protocol.record_assignment, {"id": "binary-text-review", "route_id": "binary-text-route",
            "dossier_id": None, "role": "manuscript", "reviewer_id": "binary-text-reviewer",
            "author_id": bundle["candidate"]["authors"][0], "context": {"bundle": bundle}})
        completed = invoke_codex(self, "binary-text-review", json.loads(self.artifacts.read(attempt["output"])))
        self.assertEqual(completed["result"]["status"], "completed")
        text_request, text_packet = read_sent_packet(self, "attempt-binary-text-review")
        entry = next(row for row in text_packet["evidence"] if row["artifact"] == reference["artifact"])
        self.assertEqual(entry["encoding"], "omitted")
        self.assertEqual(entry["delivery"], "bytes-omitted-on-text-route")
        self.assertNotIn(base64.b64encode(data).decode("ascii"), json.dumps(text_request))
        generic = self.artifacts.put(data, "application/octet-stream")
        self.store.mutate("fixture.alias", {}, lambda tx: tx.put("fixture", "binary-alias", generic),
                          expected_revision=self.store.revision, request_id="binary-alias")
        from research_harness.native_export import export_native
        attack = self.root / "attack"
        attack.mkdir()
        exported = export_native(self.store, attack, attack / "inputs")
        paths = [entry["path"] for entry in exported["inputs"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual((attack / "inputs/artifacts" / generic["sha256"]).read_bytes(), data)

    def test_png_output_reaches_an_observed_manuscript_review(self):
        self.run_binary_manuscript_journey(PNG, "image/png", "png")

    def test_pdf_output_reaches_an_observed_manuscript_review(self):
        self.run_binary_manuscript_journey(authored_pdf(), "application/pdf", "pdf")

    def test_opaque_output_reaches_an_observed_manuscript_review(self):
        self.run_binary_manuscript_journey(b"\x93NUMPY\x01\x00\xff\x00finite-result", "application/x-npy", "npy")


class BinaryResultJourneyTests(SourceLimitedCase):
    def build_result_evidence(self, outcome, validation=False):
        if validation:
            return super().result_evidence(outcome, True)
        output = outcome["outputs"][0]
        return {"kind": "result", "execution_id": outcome["id"], "output_id": output["id"],
                "artifact": output["artifact"], "locator": {"kind": "artifact"}}

    # Keep the inherited fixture hook while giving the helper a verb.
    result_evidence = build_result_evidence

    def build_binary_references(self, outcome):
        pdf = self.build_result_evidence(outcome)
        image = dict(pdf, output_id="figure", artifact=outcome["outputs"][2]["artifact"])
        page = dict(pdf, locator={"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]})
        return [pdf, image, page]

    def build_assessment(self, plan, outcome, *args, **kwargs):
        value = super().assessment(plan, outcome, *args, **kwargs)
        value["result"]["evidence"] = self.build_binary_references(outcome)
        return value

    # SourceLimitedCase prepares its run through this existing hook.
    assessment = build_assessment

    def review(self, outcome, identifier="review-1", verdict="ready"):
        value = DevelopmentCase.review(self, outcome, identifier, verdict)
        report = self.manuscript_readiness()
        value.update(candidate_digest=report["candidate_digest"], target={
            "kind": "source_limited_manuscript", "contract_id": report["contract"]["id"],
            "scientific_target_digest": report["scientific_target_digest"]})
        value["checks"].append(dict(value["checks"][0], kind="source_limits"))
        return value

    def prepare_binary_delivery(self):
        original_admit = integration_fixtures.admit_lab
        pdf = authored_pdf()
        body = integration_fixtures.OBSERVED_PROGRAM + (
            "Path('results/result.pdf').write_bytes(" + repr(pdf) + ")\n"
            "Path('results/figure.png').write_bytes(" + repr(PNG) + ")\n")
        outputs = [
            {"id": "result", "requirement_id": "measurements", "path": "results/result.pdf",
             "media_type": "application/pdf"},
            {"id": "validation", "requirement_id": "checks", "path": "results/validation.json",
             "media_type": "application/json"},
            {"id": "figure", "requirement_id": "measurements", "path": "results/figure.png",
             "media_type": "image/png"}]

        def admit_binary(case, *args, **kwargs):
            # Extend the authored producer; admission, binding and observation still run unchanged.
            return original_admit(case, *args, **dict(kwargs, body=body, outputs=outputs))

        with patch.object(integration_fixtures, "admit_lab", side_effect=admit_binary):
            payload = self.prepare_delivery(accept=False)
        references = self.build_binary_references(self.execution_payload)
        payload["id"] = "binary-delivery"
        payload["supported_claims"][0]["evidence"] = references
        for reference in references[:2]:
            payload["scientific_delivery"].append({"original_sha256": reference["artifact"]["sha256"],
                "disposition": "project", "projection": {"kind": "result_output",
                "context": "The complete recorded finite-result output, with its original bytes."}})
        comparison = ManagedStrategy(self).evidence
        content = self.artifacts.read(comparison).decode("utf-8")
        payload["scientific_delivery"].append({"original_sha256": comparison["sha256"], "disposition": "project",
            "projection": {"kind": "text_spans",
                           "context": "The authored comparison delivered by the strategy fixture.",
                           "locators": [{"kind": "text", "start": 0, "end": len(content), "quote": content}]}})
        self.contract = self.record_scope(payload)
        return references

    def assert_binary_bytes(self, directory, references):
        for reference, data in zip(references, (authored_pdf(), PNG)):
            self.assertEqual((directory / reference["artifact"]["path"]).read_bytes(), data)

    def test_binary_results_reach_scoped_review_decision_and_manuscript_with_exact_native_delivery(self):
        self.store.mutate("fixture.managed", {}, lambda tx: tx.put("workspace", "study", {"stage": "literature"}),
                          expected_revision=self.store.revision, request_id="managed-binary")
        references = self.prepare_binary_delivery()
        outcome = self.execution_payload
        records = self.store.snapshot()["records"]
        assessment = records["cycle_assessment"]["assessment-1"]
        self.assertTrue(assessment["assessment"]["validated_result"])
        self.assertEqual(assessment["payload"]["result"]["evidence"], references)
        binding = records["execution_binding"]["lab-run"]
        self.assertEqual([o["media_type"] for o in binding["outputs"]],
                         ["application/pdf", "application/json", "image/png"])
        directory = self.root / "binary-readiness"
        review_delivery.deliver_readiness(self.store, directory)
        self.assert_binary_bytes(directory, references)
        self.mutate(self.scope_api().record_scoped_readiness_review, self.review(outcome))
        self.assertTrue(self.manuscript_readiness()["manuscript_ready"])
        ManagedStrategy(self).ensure_intent()
        decision = result_decision(self)
        self.assertEqual(decision["payload"]["phase"], "result")
        bundle = self.scoped_manuscript()
        self.assertEqual(bundle["claim_evidence"][0]["evidence"], references)
        directory = self.root / "binary-manuscript"
        review_delivery.deliver_manuscript(self.store, directory)
        self.assert_binary_bytes(directory, references)
        pinned = publication._bundle(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(pinned["digest"], bundle["digest"])
        integration_fixtures.build_manuscript_review(self, bundle, "binary-paper-reviewer")
        records = self.store.snapshot()["records"]
        paper_assignment = next(a for a in records["review_assignment"].values()
                                if a["role"] == "manuscript")
        paper_attempt = next(a for a in records["review_attempt"].values()
                             if a["assignment_id"] == paper_assignment["id"])
        self.assertEqual(paper_attempt["status"], "completed")
        paper_request = json.loads(self.artifacts.read(paper_attempt["request"]))
        attached = review_input_projection.collect_attachments(
            review_transport.logical_messages(paper_request)[-1]["content"])
        for reference in references[:2]:
            self.assertIsNotNone(review_input_projection.find_attachment(attached, reference["artifact"]))
        observed = records["execution_observation"]["lab-run"]["files"]
        for reference in references[:2]:
            self.assertIn(reference["artifact"], [entry["artifact"] for entry in observed])
        for identifier in decision["payload"]["review_ids"]:
            assignment = records["review_assignment"][records["value_review"][identifier]["assignment_id"]]
            attempt = next(a for a in records["review_attempt"].values() if a["assignment_id"] == assignment["id"])
            request = json.loads(self.artifacts.read(attempt["request"]))
            parts = review_transport.logical_messages(request)[-1]["content"]
            attached = review_input_projection.collect_attachments(parts)
            self.assertEqual([p["type"] for p in parts].count("input_file"), 1)
            self.assertEqual([p["type"] for p in parts].count("input_image"), 1)
            for reference in references[:2]:
                self.assertIsNotNone(review_input_projection.find_attachment(attached, reference["artifact"]))
        with review_transport.text_only_delivery(True):
            omitted = review_protocol._evidence(self.artifacts, references)
        self.assertEqual(len(omitted), 2)
        self.assertTrue(all(entry["encoding"] == "omitted" and entry["content"] == ""
                            and entry["delivery"] == "bytes-omitted-on-text-route" for entry in omitted))

    def test_result_projection_requires_the_exact_recorded_output_and_valid_locator(self):
        references = self.prepare_binary_delivery()
        records = self.store.snapshot()["records"]
        manifest = {"evidence": references}
        projected, _ = scientific_delivery.project_delivery(records, self.artifacts, self.contract, manifest)
        self.assertEqual(projected, manifest)
        for field, value, code in (
                ("output_id", "absent", "result_evidence_mismatch"),
                ("execution_id", "absent", "unknown_execution"),
                ("artifact", dict(references[2]["artifact"], size=1), "result_evidence_mismatch"),
                ("locator", dict(references[2]["locator"], page_index=1), "invalid_locator")):
            with self.subTest(field=field):
                forged = dict(references[2], **{field: value})
                with self.assertRaises(ResearchError) as refused:
                    scientific_delivery.project_delivery(records, self.artifacts, self.contract, {"evidence": [forged]})
                self.assertEqual(refused.exception.code, code)

    def test_result_projection_keeps_private_roles_and_unbound_artifacts_refused(self):
        references = self.prepare_binary_delivery()
        records = self.store.snapshot()["records"]
        projected, _ = scientific_delivery.project_delivery(
            records, self.artifacts, self.contract, {"evidence": references})
        self.assertEqual(projected["evidence"], references)
        with self.assertRaises(ResearchError) as refused:
            scientific_delivery.project_delivery(
                records, self.artifacts, self.contract, {"file": references[0]["artifact"]})
        self.assertEqual(refused.exception.code, "result_evidence_mismatch")
        for private in (False, True):
            with self.subTest(private=private):
                current = copy.deepcopy(records)
                if private:
                    scope = current["publication_scope"][self.contract["id"]]["payload"]
                    scope["authorization"] = references[0]["artifact"]
                else:
                    current["execution"] = {}
                with self.assertRaises(ResearchError) as refused:
                    scientific_delivery.project_delivery(
                        current, self.artifacts, self.contract, {"evidence": references})
                expected = "private_mixed_artifact_required" if private else "unknown_execution"
                self.assertEqual(refused.exception.code, expected)


class ResultOutputProjectionTests(unittest.TestCase):
    def test_multiple_recorded_media_aliases_project_independently_of_result_order(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(directory)
            output = artifacts.put(PNG, "image/png")
            alternate = artifacts.put(PNG, "image/PNG")
            observed = artifacts.put(PNG, "application/octet-stream")
            terminal = artifacts.put(b'{"status":"completed","exit_code":0}', "application/json")
            references = [{"kind": "result", "execution_id": "png", "output_id": name,
                           "artifact": ref, "locator": {"kind": "artifact"}}
                          for name, ref in (("figure", output), ("alternate", alternate))]
            records = {"execution": {"png": {"payload": {"origin": {"kind": "managed", "admission_id": "run"},
                       "outputs": [{"id": "figure", "artifact": output}, {"id": "alternate", "artifact": alternate}]}}},
                       "execution_observation": {"run": {"terminal": terminal,
                           "files": [{"path": "figure.png", "artifact": observed}]}}}
            contract = {"payload": {"scientific_delivery": [{"original_sha256": output["sha256"],
                "disposition": "project", "projection": {"kind": "result_output",
                "context": "Both exact recorded media descriptors refer to the observed PNG bytes."}}]}}
            delivered = []
            for ordered in (references, list(reversed(references))):
                manifest = {"observed_file": observed, "evidence": ordered}
                projected, derived = scientific_delivery.project_delivery(records, artifacts, contract, manifest)
                self.assertEqual(projected["evidence"], ordered)
                self.assertIn(projected["observed_file"], (output, alternate))
                self.assertEqual(derived, {})
                delivered.append(projected["observed_file"])
            self.assertEqual(delivered[0], delivered[1])

    def test_result_output_keeps_its_context_when_public_source_bytes_have_another_media_type(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(directory)
            output = artifacts.put(authored_pdf(), "application/pdf")
            source = artifacts.put(authored_pdf(), "application/octet-stream")
            reference = {"kind": "result", "execution_id": "pdf", "output_id": "paper",
                         "artifact": output, "locator": {"kind": "artifact"}}
            records = {"execution": {"pdf": {"payload": {"origin": {"kind": "imported"},
                       "outputs": [{"id": "paper", "artifact": output}]}}}}
            contract = {"payload": {"scientific_delivery": [{"original_sha256": output["sha256"],
                "disposition": "project", "projection": {"kind": "result_output",
                "context": "A recorded PDF output remains independent of source media metadata."}}]}}
            manifest = {"evidence": [reference]}
            projected, _ = scientific_delivery.project_delivery(records, artifacts, contract, manifest)
            self.assertEqual(projected, manifest)
            records["source"] = {"public": {"id": "public", "status": "captured", "response_complete": True,
                                           "response": source}}
            terminal = artifacts.put(b'{"status":"completed","exit_code":0}', "application/json")
            for observed in (False, True):
                if observed:
                    records["execution"]["pdf"]["payload"]["origin"] = {"kind": "managed", "admission_id": "run"}
                    records["execution_observation"] = {"run": {"terminal": terminal,
                        "files": [{"path": "paper.pdf", "artifact": source}]}}
                for value in (manifest, {"source": source, "evidence": [reference]}):
                    with self.subTest(source_first="source" in value, observed=observed):
                        projected, derived = scientific_delivery.project_delivery(records, artifacts, contract, value)
                        self.assertEqual(projected, value)
                        self.assertEqual(derived, {})

    def test_result_descriptor_extensions_cannot_deliver_private_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(directory)
            output = artifacts.put(PNG, "image/png")
            private = artifacts.put(b"Private descriptor extension evidence.", "text/plain")
            reference = {"kind": "result", "execution_id": "png", "output_id": "figure",
                         "artifact": output, "locator": {"kind": "artifact"}}
            records = {"execution": {"png": {"payload": {"origin": {"kind": "imported"},
                       "outputs": [{"id": "figure", "artifact": output}]}}},
                       "publication_scope": {"scope": {"payload": {"authorization": private,
                           "scientific_preparers": [], "correction": None}}}}
            contract = {"payload": {"scientific_delivery": [{"original_sha256": output["sha256"],
                "disposition": "project", "projection": {"kind": "result_output",
                "context": "The exact recorded PNG with no internal provenance."}}]}}
            projected, _ = scientific_delivery.project_delivery(records, artifacts, contract, {"evidence": [reference]})
            self.assertEqual(projected["evidence"], [reference])
            extended = dict(output, internal_provenance=private)
            records["execution"]["png"]["payload"]["outputs"][0]["artifact"] = extended
            reference = dict(reference, artifact=extended)
            with self.assertRaises(ResearchError) as refused:
                scientific_delivery.project_delivery(records, artifacts, contract, {"evidence": [reference]})
            self.assertEqual(refused.exception.code, "private_mixed_artifact_required")

    def test_observation_media_alias_can_precede_the_result_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(directory)
            output = artifacts.put(PNG, "image/png")
            observed = artifacts.put(PNG, "application/octet-stream")
            terminal = artifacts.put(b'{"status":"completed","exit_code":0}', "application/json")
            reference = {"kind": "result", "execution_id": "png", "output_id": "figure",
                         "artifact": output, "locator": {"kind": "artifact"}}
            records = {"execution": {"png": {"payload": {"origin": {"kind": "managed", "admission_id": "run"},
                       "outputs": [{"id": "figure", "artifact": output}]}}},
                       "execution_observation": {"run": {"terminal": terminal,
                           "files": [{"path": "figure.png", "artifact": observed}]}}}
            contract = {"payload": {"scientific_delivery": [{"original_sha256": output["sha256"],
                "disposition": "project", "projection": {"kind": "result_output",
                "context": "The original PNG, including its generically captured observation."}}]}}
            manifest = {"observed_file": observed, "evidence": [reference]}
            for public in (False, True):
                with self.subTest(public=public):
                    if public:
                        records["source"] = {"public": {"id": "public", "status": "captured",
                            "response_complete": True, "response": output}}
                    projected, derived = scientific_delivery.project_delivery(records, artifacts, contract, manifest)
                    self.assertEqual(projected, {"observed_file": output, "evidence": [reference]})
                    self.assertEqual(derived, {})

    def test_whole_output_delivery_preserves_empty_bytes_and_their_locator(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(directory)
            for media in ("application/pdf", "image/png", "application/json", "text/plain"):
                with self.subTest(media=media):
                    artifact = artifacts.put(b"", media)
                    reference = {"kind": "result", "execution_id": "empty", "output_id": "empty",
                                 "artifact": artifact, "locator": {"kind": "artifact"}}
                    records = {"execution": {"empty": {"payload": {"origin": {"kind": "imported"},
                               "outputs": [{"id": "empty", "artifact": artifact}]}}}}
                    contract = {"payload": {"scientific_delivery": [{"original_sha256": artifact["sha256"],
                        "disposition": "project", "projection": {"kind": "result_output",
                        "context": "The entire empty output is the retained observation."}}]}}
                    manifest = {"evidence": [reference]}
                    projected, derived = scientific_delivery.project_delivery(records, artifacts, contract, manifest)
                    self.assertEqual(projected, manifest)
                    self.assertEqual(derived, {})
