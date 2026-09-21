"""Inspect delivered bytes, not only the public packet's manifest keys."""

import copy
import io
import json
import tarfile
import struct
import zipfile

from source_limited_fixtures import SourceLimitedCase
from research_harness.review_delivery import deliver_readiness, deliver_manuscript, references
from research_harness.artifacts import describe_artifact


class SourceLimitedDeliveryTests(SourceLimitedCase):
    def prepare_delivery(self, accept=True, negative=False):
        from research_harness.execution_evidence import author_readiness_state
        from research_harness.review_packets import readiness_packet
        payload = self.prepare_source_limited()
        if negative:
            from integration_fixtures import observe_run
            plan = copy.deepcopy(self.store.snapshot()["records"]["cycle_plan"]["cycle-1"]["payload"])
            plan.update(id="negative-branch", question="Does a strict bound of 8 survive enumeration?", hypothesis="The finite maximum is at most 8.")
            plan["strategy"]["mechanism"] = "Test a stronger finite hypothesis and retain its counterexample."
            run = observe_run(self, plan=plan, run_id="negative-run", request_id="negative-launch")
            assessment = self.assessment(plan, run, "negative-assessment")
            assessment.update(objective_status="open", disposition="failed")
            assessment["result"]["statement"] = "The observed value 9 refutes the proposed strict bound of 8."
            assessment["outcomes"][0]["status"] = "not_observed"
            assessment["failures"][0]["status"] = "observed"
            self.mutate(self.development().assess_cycle, assessment)
            self.save_checkpoint("negative-branch", "negative-assessment", "negative-checkpoint", select=False)
            current = copy.deepcopy(self.assessment_payload)
            current["id"] = "assessment-with-negative"
            current["development"]["branches"].append({"cycle_id": "negative-branch", "disposition": "not_useful",
                "reason": "The stronger hypothesis was refuted and its counterexample is retained.", "evidence": [self.result_evidence(run)]})
            self.mutate(self.development().assess_cycle, current)
            self.save_checkpoint("cycle-1", "assessment-with-negative", "checkpoint-with-negative")
            payload = self.scope_payload()
        report = author_readiness_state(self.store.snapshot()["records"], self.artifacts)
        packet = readiness_packet(report)
        # Fixture declarations are reviewed like scientific scope, never runtime flags.
        declarations = []
        required = {}
        def gather(value):
            if isinstance(value, dict):
                if isinstance(value.get("artifact"), dict) and isinstance(value.get("locator"), dict):
                    required.setdefault(value["artifact"]["sha256"], []).append(value["locator"])
                for child in value.values():
                    gather(child)
            elif isinstance(value, list):
                for child in value:
                    gather(child)
        gather(packet)
        for ref in references(packet):
            data = self.artifacts.read(ref)
            if ref["sha256"] in required:
                locators = list({json.dumps(l, sort_keys=True): l for l in required[ref["sha256"]]}.values())
                if all(l["kind"] == "json" for l in locators):
                    projection = {"kind": "json_locators", "locators": locators, "context": "All required finite result values."}
                else:
                    continue
            elif data.lstrip().startswith(b"{"):
                structured = json.loads(data)
                projection = {"kind": "json_locators", "locators": [
                    {"kind": "json", "pointer": "/" + key.replace("~", "~0").replace("/", "~1"), "value": value}
                    for key, value in structured.items()], "context": "Typed fixture observations."}
            elif data:
                try:
                    value = data.decode()
                except UnicodeError:
                    continue
                projection = {"kind": "text_spans", "locators": [{"kind": "text", "start": 0, "end": len(value), "quote": value}],
                              "context": "The entire authored scientific fixture program or log."}
            else:
                continue
            declarations.append({"original_sha256": ref["sha256"], "disposition": "project", "projection": projection})
        payload["scientific_delivery"] = declarations
        self.contract = self.record_scope(payload)
        if accept:
            self.accept_scope()
        return payload

    def bytes_in(self, directory):
        return b"\n".join(path.read_bytes() for path in directory.rglob("*") if path.is_file())

    def test_initial_scoped_delivery_uses_typed_projections_before_acceptance(self):
        self.prepare_delivery(accept=False)
        directory = self.root / "independent-readiness"
        delivered = deliver_readiness(self.store, directory)
        self.assertTrue(delivered["artifacts"])
        content = self.bytes_in(directory)
        self.assertNotIn(b"PRIVATE", content)
        manifest = json.loads((directory / "inputs.json").read_text())
        self.assertEqual(manifest["scientific_scope"]["scope"]["statement"], self.contract["payload"]["scope"]["statement"])
        self.assertIn(self.objective["statement"].encode(), content)
        self.assertIn(b'"derivative": true', content)
        self.assertFalse(self.manuscript_readiness()["ready"])

    def test_blind_manuscript_excludes_preparer_directive_and_previous_verdict_bytes(self):
        self.prepare_delivery()
        self.scoped_manuscript()
        directory = self.root / "blind-paper"
        deliver_manuscript(self.store, directory)
        content = self.bytes_in(directory)
        for private in (b"PRIVATE", b"scope-preparer", b"scope-reviewer", b"independence_basis", b"scientific_preparers"):
            self.assertNotIn(private, content)
        self.assertIn(b"external comparison remains unavailable", content)

    def test_required_locator_cannot_be_omitted(self):
        payload = self.prepare_delivery(accept=False)
        ref = self.execution_payload["outputs"][0]["artifact"]
        declarations = payload["scientific_delivery"]
        declaration = next(d for d in declarations if d["original_sha256"] == ref["sha256"])
        declaration["projection"]["locators"] = [l for l in declaration["projection"]["locators"] if l["pointer"] != "/result"]
        payload["id"] = "omits-science"
        self.record_scope(payload)
        directory = self.root / "invalid-delivery"
        self.assert_error("scientific_projection_incomplete", lambda: deliver_readiness(self.store, directory))
        self.assertFalse(directory.exists())

    def test_raw_authored_public_label_does_not_authorize_delivery(self):
        payload = self.prepare_delivery(accept=False)
        ref = self.execution_payload["outputs"][0]["artifact"]
        declaration = next(d for d in payload["scientific_delivery"] if d["original_sha256"] == ref["sha256"])
        declaration.update(disposition="public", projection=None)
        payload["id"] = "raw-authored"
        self.record_scope(payload)
        directory = self.root / "raw-refused"
        self.assert_error("private_mixed_artifact_required", lambda: deliver_readiness(self.store, directory))
        self.assertFalse(directory.exists())

    def test_sources_archive_requires_validated_member_projection(self):
        from research_harness import publication
        self.prepare_delivery()
        bundle = self.scoped_manuscript()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, data in (("result.json", b'{"result":9}'), ("private.txt", b"PRIVATE-ARCHIVE-SENTINEL")):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        (self.root / "draft/sources.tar").write_bytes(buffer.getvalue())
        value = {"id": "with-archive", "files": {k: v["path"] if v else None for k, v in bundle["files"].items()},
                 "claim_evidence": bundle["claim_evidence"]}
        value["files"]["sources"] = "draft/sources.tar"
        self.mutate(publication.prepare_publication, value)
        directory = self.root / "archive-refused"
        self.assert_error("private_mixed_artifact_required", lambda: deliver_manuscript(self.store, directory))
        self.assertFalse(directory.exists())

    def extra_sources(self, data, projection, *, media_type="application/octet-stream"):
        from research_harness import publication
        payload = self.prepare_delivery(accept=False)
        ref = self.artifacts.put(data, media_type)
        payload["scientific_delivery"].append({"original_sha256": ref["sha256"], "disposition": "project", "projection": projection})
        payload["id"] = "scope-with-appendix"
        self.contract = self.record_scope(payload)
        self.accept_scope()
        bundle = self.scoped_manuscript()
        path = self.root / "draft/scientific-appendix"
        path.write_bytes(data)
        value = {"id": "with-appendix", "files": {k: v["path"] if v else None for k, v in bundle["files"].items()},
                 "claim_evidence": bundle["claim_evidence"]}
        value["files"]["sources"] = path.relative_to(self.root).as_posix()
        self.mutate(publication.prepare_publication, value)
        return ref

    def test_private_json_sibling_is_excluded_and_required_values_are_exact(self):
        data = json.dumps({"result": {"bound": 9}, "private": "PRIVATE-SCOPE-PREPARER"}).encode()
        original = self.extra_sources(data, {"kind": "json_locators", "context": "The finite bound is the entire appendix result.",
            "locators": [{"kind": "json", "pointer": "/result", "value": {"bound": 9}}]})
        directory = self.root / "safe-json"
        deliver_manuscript(self.store, directory)
        content = self.bytes_in(directory)
        self.assertNotIn(b"PRIVATE", content)
        manifest = json.loads((directory / "inputs.json").read_text())
        derived = json.loads((directory / manifest["files"]["sources"]["artifact"]["path"]).read_text())
        self.assertEqual(derived["original_sha256"], original["sha256"])
        self.assertEqual(derived["entries"][0]["value"], {"bound": 9})
        self.assertEqual(derived["locator_mapping"][0]["derived_pointer"], "/entries/0/value")

    def test_nested_private_reference_fails_without_output_directory(self):
        private = describe_artifact(b"PRIVATE-SCOPE-PREPARER", "text/plain")
        self.extra_sources(json.dumps({"result": private}).encode(), {"kind": "json_locators", "context": "A mixed artifact is not publishable.",
            "locators": [{"kind": "json", "pointer": "/result", "value": private}]})
        directory = self.root / "nested-refused"
        self.assert_error("private_mixed_artifact_required", lambda: deliver_manuscript(self.store, directory))
        self.assertFalse(directory.exists())

    def test_copied_log_cannot_include_private_authorization(self):
        value = "Scientific bound 9. PRIVATE-SCOPE-PREPARER"
        self.extra_sources(value.encode(), {"kind": "text_spans", "context": "A copied log contains private content.",
            "locators": [{"kind": "text", "start": 0, "end": len(value), "quote": value}]})
        directory = self.root / "log-refused"
        self.assert_error("private_mixed_artifact_required", lambda: deliver_manuscript(self.store, directory))
        self.assertFalse(directory.exists())

    def test_checked_archive_members_exclude_private_siblings(self):
        data = b'{"result":9}'
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, content in (("result.json", data), ("private.txt", b"PRIVATE-ARCHIVE-SENTINEL")):
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        ref = self.extra_sources(buffer.getvalue(), {"kind": "archive_members", "context": "The single result member is the scientific appendix.",
            "members": [{"path": "result.json", "sha256": describe_artifact(data, "application/json")["sha256"], "media_type": "application/json",
                         "projection": {"kind": "json_locators", "context": "The exact bound.",
                                        "locators": [{"kind": "json", "pointer": "/result", "value": 9}]}}]})
        directory = self.root / "safe-archive"
        deliver_manuscript(self.store, directory)
        content = self.bytes_in(directory)
        self.assertNotIn(b"PRIVATE", content)
        self.assertIn(ref["sha256"].encode(), content)
        self.assertFalse((directory / ref["path"]).exists())
        self.assertIn(b'"value": 9', content)

    def test_readiness_retains_negative_branch_and_all_locator_mappings(self):
        self.prepare_delivery(accept=False, negative=True)
        directory = self.root / "with-negative"
        deliver_readiness(self.store, directory)
        content = self.bytes_in(directory)
        self.assertIn(b"negative-branch", content)
        self.assertIn(b"refutes the proposed strict bound of 8", content)
        self.assertIn(self.objective["statement"].encode(), content)
        mapped = []
        for path in directory.rglob("*"):
            if path.is_file():
                try:
                    value = json.loads(path.read_bytes())
                except (ValueError, UnicodeError):
                    continue
                if isinstance(value, dict) and value.get("projection_kind") == "json_locators":
                    for item in value["locator_mapping"]:
                        index = int(item["derived_pointer"].split("/")[2])
                        self.assertEqual(value["entries"][index]["value"], item["original_locator"]["value"])
                        mapped.append(item["original_locator"]["pointer"])
        self.assertTrue({"/result", "/validation"} <= set(mapped))

    def test_round_preserves_current_manuscript_review_cores(self):
        from integration_fixtures import approve_publication_stop
        from research_harness.review_delivery import deliver_round
        # The investigation happens after readiness, pinning and measurement.
        # Its bytes cannot be declared prospectively in the scientific scope.
        self.prepare_delivery()
        bundle = self.scoped_manuscript()
        approve_publication_stop(self, bundle)
        directory = self.root / "round-review"
        deliver_round(self.store, directory)
        packet = json.loads((directory / "inputs.json").read_text())
        self.assertEqual(len(packet["reviews"]), 3)
        self.assertTrue(all(review["core"]["decision"] == "accept" for review in packet["reviews"]))
        self.assertNotIn(b"PRIVATE", self.bytes_in(directory))
        response = packet["contribution_analysis"]["investigation"][0]["response"]
        self.assertEqual((directory / response["path"]).read_bytes(), self.artifacts.read(response))
        blind = self.root / "blind-after-investigation"
        deliver_manuscript(self.store, blind)
        self.assertFalse((blind / response["path"]).exists())

    def test_correction_scientific_evidence_remains_deliverable_before_and_after_review(self):
        from test_research_publication_scope import PublicationScopeTests
        original = self.prepare_delivery(accept=False)
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review(verdict="not_ready"))
        corrected = PublicationScopeTests.corrected_scope(self)
        corrected["scientific_delivery"] = original["scientific_delivery"]
        response = corrected["correction"]["response"]
        quote = self.artifacts.read(response).decode()
        corrected["scientific_delivery"].append({"original_sha256": response["sha256"], "disposition": "project",
            "projection": {"kind": "text_spans", "context": "The complete corrective response for identified review.",
                "locators": [{"kind": "text", "start": 0, "end": len(quote), "quote": quote}]}})
        self.contract = self.record_scope(corrected)
        readiness = self.root / "corrective-readiness"
        deliver_readiness(self.store, readiness)
        self.assertIn(quote.encode(), self.bytes_in(readiness))
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review("fresh-corrective", assessor="new-corrective-reviewer"))
        self.assertTrue(self.manuscript_readiness()["ready"])
        self.scoped_manuscript()
        blind = self.root / "corrected-blind"
        deliver_manuscript(self.store, blind)
        self.assertNotIn(quote.encode(), self.bytes_in(blind))
        self.assertIn(b'"value": 9', self.bytes_in(blind))

    def test_correction_response_alias_cannot_declassify_authorization(self):
        from test_research_publication_scope import PublicationScopeTests
        original = self.prepare_delivery(accept=False)
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review(verdict="not_ready"))
        corrected = PublicationScopeTests.corrected_scope(self)
        response = corrected["authorization"]
        corrected["correction"]["response"] = response
        quote = self.artifacts.read(response).decode()
        corrected["scientific_delivery"] = original["scientific_delivery"] + [{"original_sha256": response["sha256"],
            "disposition": "project", "projection": {"kind": "text_spans", "context": "An invalid role alias.",
                "locators": [{"kind": "text", "start": 0, "end": len(quote), "quote": quote}]}}]
        self.record_scope(corrected)
        directory = self.root / "aliased-response-refused"
        self.assert_error("private_mixed_artifact_required", lambda: deliver_readiness(self.store, directory))
        self.assertFalse(directory.exists())

    def numpy_member(self, dtype, shape, payload, fortran=False):
        header = repr({"descr": dtype, "fortran_order": fortran, "shape": shape}).encode("ascii")
        padding = (64 - ((10 + len(header) + 1) % 64)) % 64
        header += b" " * padding + b"\n"
        return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header + payload

    def test_numpy_archive_retains_exact_array_values_and_excludes_private_members(self):
        member = self.numpy_member("<f8", (2, 2), bytes.fromhex(
            "000000000000f83f0000000000000080000000000000f87f0000000000000240"))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("values.npy", member)
            archive.writestr("private.txt", b"PRIVATE-NPZ-SIBLING")
        self.extra_sources(buffer.getvalue(), {"kind": "archive_members", "context": "The complete numerical result.",
            "members": [{"path": "values.npy", "sha256": describe_artifact(member, "application/octet-stream")["sha256"],
                         "media_type": "application/octet-stream", "projection": {"kind": "npy_array", "context": "All array values."}}]})
        directory = self.root / "numerical-review"
        deliver_manuscript(self.store, directory)
        content = self.bytes_in(directory)
        self.assertNotIn(b"PRIVATE", content)
        arrays = []
        for path in directory.rglob("*"):
            if path.is_file():
                try:
                    value = json.loads(path.read_bytes())
                except (ValueError, UnicodeError):
                    continue
                if isinstance(value, dict) and value.get("projection_kind") == "npy_array":
                    arrays.append(value["array"])
        self.assertEqual(len(arrays), 1)
        self.assertEqual(arrays[0]["shape"], [2, 2])
        self.assertEqual(arrays[0]["dtype"], "<f8")
        self.assertEqual(arrays[0]["values"], [1.5, -0.0, {"nonfinite": "nan"}, 2.25])
        self.assertEqual(arrays[0]["element_hex"], ["000000000000f83f", "0000000000000080", "000000000000f87f", "0000000000000240"])
