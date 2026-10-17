"""Canonical review exports keep the checked identity of their raw probe bytes."""

import hashlib
import json
import shutil
import unittest
from unittest import mock

from research_harness.artifacts import ArtifactStore
from search_controller.service import Controller
from search_controller.errors import SearchError
from tests.search_fixtures import digest
from tests.test_search_cli import SearchCLIWorkspace
from tests import test_search_common_evidence as common_tests
from tests import test_search_evidence as evidence_tests


class ExportOriginTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = evidence_tests.EvidenceTests.artifact
    external_checkpoint = evidence_tests.EvidenceTests.external_checkpoint
    record_probe_output = common_tests.CommonEvidenceTests.record_probe_output

    def export_json_input(self, *, record_origin=True, import_first=False, interrupt_before_bytes=False):
        self.initialize()
        controller = Controller(self.root)
        value = {"result": "An exploratory finite computation."}
        raw = b'{ "result" : "An exploratory finite computation." }\n'
        self.assertNotEqual(hashlib.sha256(raw).hexdigest(), digest(value))
        if record_origin:
            self.record_probe_output(raw)
        (self.root / "probe.json").write_bytes(raw)
        if interrupt_before_bytes:
            from research_harness.artifacts import describe_artifact
            from research_harness.storage import Store
            # Replay an alias left by the historical alias-before-bytes exporter.
            store = Store(self.root, create=True)
            identity = {"schema_version": 1, "canonical_digest": digest(value),
                        "raw": describe_artifact(raw, "application/json")}
            def record_legacy_alias(transaction):
                transaction.put("native_import_identity", digest(value) + ":" + identity["raw"]["sha256"], identity)
            store.mutate("fixture.legacy_import_identity", identity, record_legacy_alias,
                         expected_revision=store.revision, request_id="legacy-import-identity")
        item = {"path": "probe.json", "digest": digest(value), "kind": "blob"}
        claim = controller.status()["contract"]["original_claim"]
        subject = {"claim": claim, "evidence_digests": [digest(value)]}
        inputs = [item]
        if import_first:
            spec = self.external_checkpoint()
            spec["checkpoint"]["kind"] = "hypothesis"
            spec["inputs"].append(item)
            self.search("checkpoint", spec, revision=1)
            inputs = []
        before = controller.store.read()
        exported = controller.command("review-packet", {"kind": "result", "subject": subject,
            "claim_digest": digest(claim), "reviewer": {"source": "host", "actor_id": "export-reviewer",
                "attestation_id": "review-assignment:export-origin"}, "inputs": inputs}, None, None)
        self.assertEqual(controller.store.read(), before)
        artifacts = ArtifactStore(exported["workspace"])
        packet = json.loads(artifacts.read(exported["native_packet"]))
        entry = next(row for row in packet["evidence"] if row["digest"] == digest(value))
        self.assertEqual([ref["sha256"] for ref in entry["artifact"]["raw_identities"]],
                         [hashlib.sha256(raw).hexdigest()])
        return raw, artifacts.read(entry["artifact"])

    def assert_export_cannot_gain_proof_credit(self, *, record_origin=True, import_first=False):
        raw, canonical = self.export_json_input(record_origin=record_origin, import_first=import_first)
        if not record_origin:
            self.record_probe_output(raw)
        spec = self.external_checkpoint()
        item = self.artifact("exported-proof.json", canonical.decode())
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["artifacts"][0].update(path=item["path"], digest=item["digest"])
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        spec["inputs"][0] = item
        spec["inputs"][-1]["digest"] = digest(manifest)
        spec["checkpoint"]["evidence_digests"] = [digest(manifest)]
        controller = Controller(self.root)
        before = controller.store.read()
        result = self.search("checkpoint", spec, revision=controller.status()["revision"], success=False)
        self.assertEqual(result["error"]["code"], "exploratory_evidence")
        self.assertEqual(controller.store.read(), before)

    def test_packet_exported_canonical_probe_json_cannot_be_imported_as_analytical_proof(self):
        self.assert_export_cannot_gain_proof_credit()

    def test_export_of_a_previously_imported_blob_preserves_its_raw_probe_identity(self):
        self.assert_export_cannot_gain_proof_credit(import_first=True)

    def test_raw_probe_origin_recorded_after_export_still_blocks_canonical_proof_credit(self):
        self.assert_export_cannot_gain_proof_credit(record_origin=False)

    def test_packet_assessment_and_historical_replay_publish_no_content_or_aliases(self):
        from research_harness.storage import Store
        from search_controller.evidence import import_inputs
        from search_controller.provenance import record_probe_origins
        from search_controller.reviewer import packet
        from search_controller.storage import ContentSink
        self.export_json_input()
        controller = Controller(self.root)
        store = Store(self.root)
        records = store.snapshot()["records"]
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        state = controller.status(full_audit=True)
        identity = next(iter(records["native_import_identity"].values()))["canonical_digest"]
        content = ContentSink(controller.store)
        bindings = import_inputs(self.root, [{"path": "probe.json", "kind": "blob", "digest": identity}], content, state=state)
        record_probe_origins(state, {"bindings": bindings})
        assessed = packet(self.root, state, content, {"evidence_digests": [identity]},
               "result", digest(state["contract"]["original_claim"]),
               {"source": "host", "actor_id": "reviewer", "attestation_id": "review-assignment:assessment"},
               ArtifactStore(self.root), common_records=records)
        self.assertTrue(next(row for row in assessed["evidence"] if row["digest"] == identity)["artifact"]["raw_identities"])
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_unchecked_raw_alias_cannot_attach_an_unrelated_canonical_artifact(self):
        from search_controller.provenance import expand_import_identities
        self.initialize()
        artifacts = ArtifactStore(self.root)
        raw = artifacts.put(b'{ "other": true }\n', "application/json")
        canonical = digest({"result": "An exploratory finite computation."})
        records = {"native_import_identity": {"forged": {
            "schema_version": 1, "canonical_digest": canonical, "raw": raw}}}
        with self.assertRaises(SearchError) as caught:
            expand_import_identities(records, artifacts, [canonical])
        self.assertEqual(caught.exception.code, "invalid_import_identity")

    def test_publication_helper_makes_original_readable_before_committing_its_alias(self):
        from research_harness import operations
        from research_harness.artifacts import describe_artifact
        from research_harness.storage import Store
        from search_controller.provenance import publish_import_identity
        self.initialize()
        store = Store(self.root, create=True)
        artifacts = ArtifactStore(self.root)
        raw = b'{ "result" : 7 }\n'
        reference = describe_artifact(raw, "application/json")
        commit = operations.prepared_mutation
        def commit_after_original(*args, **kwargs):
            self.assertEqual(artifacts.read(reference), raw)
            return commit(*args, **kwargs)
        with mock.patch.object(operations, "prepared_mutation", new=commit_after_original):
            publish_import_identity(artifacts, digest({"result": 7}), raw)
        self.assertTrue(store.snapshot()["records"]["native_import_identity"])

    def assert_unreadable_original_refuses_alias(self, *, corrupt):
        from research_harness.errors import ResearchError
        from research_harness.storage import Store
        from search_controller.provenance import publish_import_identity
        self.initialize()
        store = Store(self.root, create=True)
        artifacts = ArtifactStore(self.root)
        raw = b'{ "result" : 7 }\n'
        if corrupt:
            reference = artifacts.put(raw, "application/json")
            path = self.root / reference["path"]
            path.chmod(0o600)
            path.write_bytes(b"corrupt")
        before = store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            publish_import_identity(artifacts, digest({"result": 7}), raw, publish=False)
        self.assertEqual(caught.exception.code, "artifact_corrupt" if corrupt else "artifact_missing")
        self.assertEqual(store.snapshot(), before)

    def test_alias_only_publication_refuses_a_missing_original_without_committing(self):
        self.assert_unreadable_original_refuses_alias(corrupt=False)

    def test_alias_only_publication_refuses_a_corrupt_original_without_committing(self):
        self.assert_unreadable_original_refuses_alias(corrupt=True)

    def test_export_refuses_an_exploratory_blob_whose_original_bytes_cannot_be_preserved(self):
        from search_controller.provenance import collect_probe_origins, record_probe_origins
        from search_controller.reviewer import packet
        from search_controller.storage import ContentSink
        self.initialize()
        raw = b'{ "answer" : 42 }\n'
        output = self.record_probe_output(raw)
        controller = Controller(self.root)
        state, content = controller.status(), ContentSink(controller.store)
        identity = content.put_blob({"answer": 42})
        origins = collect_probe_origins(self.root, state, [output["sha256"]])
        record_probe_origins(state, {"bindings": [{"digest": identity, "raw_digest": output["sha256"], "origins": origins}]})
        with self.assertRaises(SearchError) as caught:
            packet(self.root, state, content, {"evidence_digests": [identity]}, "result",
                   digest(state["contract"]["original_claim"]), {}, ArtifactStore(self.root), write=True)
        self.assertEqual(caught.exception.code, "exploratory_evidence")

    def assert_plan_export_cannot_gain_proof_credit(self, name):
        import attack
        from search_controller.evidence import audit_manifest, import_inputs
        from search_controller.provenance import record_probe_origins
        from search_controller.storage import ContentSink
        from tests.search_execution_support import admit_workspace
        from tests.strategy_refresh_support import assessment_spec
        from tests.support import run
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root)
        raw = (self.root / "sample" / (name + ".json")).read_bytes()
        self.record_probe_output(raw)
        value = json.loads(raw)
        identity = attack.compute_problem_digest(value) if name == "problem" else digest(value)
        self.assertNotEqual(hashlib.sha256(raw).hexdigest(), identity)
        subject = assessment_spec(controller.command("strategy-context", {}, None, None))["assessment"]
        exported = controller.command("review-packet", {"kind": "result", "subject": subject,
            "claim_digest": digest(controller.status()["contract"]["original_claim"]),
            "reviewer": {"source": "host", "actor_id": "plan-reviewer", "attestation_id": "review-assignment:plan-origin"},
            "inputs": []}, None, None)
        artifacts = ArtifactStore(exported["workspace"])
        packet = json.loads(artifacts.read(exported["native_packet"]))
        entry = next(row for row in packet["evidence"] if row["digest"] == identity)
        spec = self.external_checkpoint()
        item = self.artifact("exported-plan.json", artifacts.read(entry["artifact"]).decode())
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["artifacts"][0].update(path=item["path"], digest=item["digest"])
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        spec["inputs"][0] = item
        spec["inputs"][-1]["digest"] = digest(manifest)
        state, content = controller.status(), ContentSink(controller.store)
        bindings = import_inputs(self.root, spec["inputs"], content, state=state)
        record_probe_origins(state, {"bindings": bindings})
        with self.assertRaises(SearchError) as caught:
            audit_manifest(self.root, state, digest(manifest), content, fresh=True)
        self.assertEqual(caught.exception.code, "exploratory_evidence")

    def test_plan_blob_export_preserves_its_raw_probe_identity(self):
        self.assert_plan_export_cannot_gain_proof_credit("preconditions")

    def test_problem_snapshot_export_preserves_its_raw_probe_identity(self):
        self.assert_plan_export_cannot_gain_proof_credit("problem")

    def build_mixed_alias_chain(self):
        from search_controller.problem_records import problem_bytes
        from search_controller.provenance import publish_import_identity, record_probe_origins
        from search_controller.storage import ContentSink
        self.initialize()
        value = {"result": "finite computation α"}
        raw = (json.dumps(value, indent=2) + "\n").encode()
        self.record_probe_output(raw)
        ascii_bytes = problem_bytes(value)
        ascii_digest = hashlib.sha256(ascii_bytes).hexdigest()
        controller = Controller(self.root)
        state, content = controller.status(), ContentSink(controller.store)
        content.put_artifact(raw)
        record_probe_origins(state, {"bindings": [{"digest": ascii_digest,
            "raw_digest": hashlib.sha256(raw).hexdigest(), "origins": []}]})
        publish_import_identity(ArtifactStore(self.root), digest(value), ascii_bytes)
        content.put_blob(value)
        return controller, state, content, digest(value)

    def test_probe_origin_resolution_follows_mixed_common_and_native_json_aliases(self):
        from search_controller.provenance import collect_probe_origins
        controller, state, content, identity = self.build_mixed_alias_chain()
        origins = collect_probe_origins(self.root, state, [identity], content=content)
        self.assertEqual([row["probe_id"] for row in origins], ["explore"])

    def test_export_flattens_the_checked_alias_chain_for_other_native_importers(self):
        from research_harness.storage import Store
        from search_controller.provenance import collect_probe_origins
        from search_controller.reviewer import packet
        controller, state, content, identity = self.build_mixed_alias_chain()
        packet(self.root, state, content, {"evidence_digests": [identity]}, "result",
               digest(state["contract"]["original_claim"]), {}, ArtifactStore(self.root), write=True,
               common_records=Store(self.root).snapshot()["records"])
        origins = collect_probe_origins(self.root, controller.status(), [identity])
        self.assertEqual([row["probe_id"] for row in origins], ["explore"])

    def build_unpublished_non_ascii_chain(self, *, original=None, mixed_cycle=False):
        """Stage a normalization closure without publishing its common aliases."""
        from research_harness.artifacts import describe_artifact
        from search_controller.provenance import record_probe_origins
        from search_controller.storage import ContentSink
        self.initialize()
        forms = ['{ "result" : "λ" }\n'.encode(), '{"result":"λ"}'.encode(), b'{"result":"\\u03bb"}']
        if original is not None:
            forms[0] = original
        self.record_probe_output(forms[2] if mixed_cycle else forms[0])
        controller = Controller(self.root)
        state, content = controller.status(), ContentSink(controller.store)
        identities = [content.put_artifact(raw) for raw in forms]
        utf8_source = identities[2] if mixed_cycle or original == forms[1] else identities[0]
        record_probe_origins(state, {"bindings": [
            {"digest": identities[1], "raw_digest": utf8_source, "origins": []},
            {"digest": identities[2], "raw_digest": identities[1], "origins": []}]})
        if mixed_cycle:
            record_probe_origins(state, {"bindings": [
                {"digest": identities[1], "raw_digest": identities[0], "origins": []}]})
        references = [describe_artifact(raw, "application/json") for raw in forms]
        return controller, state, content, references

    def assert_interrupted_export_keeps_origins(self, *, original=None, target_index=2, mixed_cycle=False):
        from research_harness import operations
        from research_harness.errors import ResearchError
        from research_harness.probe_provenance import find_probe_artifact_origins
        from research_harness.storage import Store
        from search_controller.reviewer import _closure
        controller, state, content, references = self.build_unpublished_non_ascii_chain(
            original=original, mixed_cycle=mixed_cycle)
        probe_digest = references[2 if mixed_cycle else 0]["sha256"]
        native_before = controller.store.read()
        target = references[target_index]["sha256"]
        seed = {"evidence_digests": [target]}
        expected_sources = {reference["sha256"]: reference for reference in references if reference["sha256"] != target}

        def run_export(stop_after=None):
            root = self.root.parent / ("chain-publication-" + str(stop_after))
            shutil.copytree(self.root, root)
            store, artifacts = Store(root), ArtifactStore(root)
            self.assertNotIn("native_import_identity", store.snapshot()["records"])
            steps = []

            def check_readable_origins():
                before = store.snapshot()
                for alias in before["records"].get("native_import_identity", {}).values():
                    artifacts.read(alias["raw"])
                for reference in references:
                    try:
                        artifacts.read(reference)
                    except ResearchError as error:
                        if error.code != "artifact_missing":
                            raise
                        continue
                    expected = []
                    if not mixed_cycle or reference != references[0]:
                        origin = {"probe_id": "explore", "sha256": reference["sha256"], "roles": ["outputs", "stdout"]}
                        if reference["sha256"] != probe_digest:
                            origin["raw_sha256"] = probe_digest
                        expected.append(origin)
                    self.assertEqual(find_probe_artifact_origins(before["records"], [reference], artifacts=artifacts), expected)
                self.assertEqual(store.snapshot(), before)

            def record_step(step):
                steps.append(step)
                check_readable_origins()
                if len(steps) == stop_after:
                    raise RuntimeError("Interrupted chain publication")

            put = ArtifactStore.put
            def publish(artifacts, raw, media_type):
                published = hashlib.sha256(raw).hexdigest()
                if published in {reference["sha256"] for reference in references[1:]}:
                    aliases = store.snapshot()["records"].get("native_import_identity", {})
                    for original in references:
                        if original["sha256"] == published:
                            continue
                        try:
                            artifacts.read(original)
                        except ResearchError as error:
                            if error.code != "artifact_missing":
                                raise
                        else:
                            self.assertIn(published + ":" + original["sha256"], aliases)
                reference = put(artifacts, raw, media_type)
                record_step(("artifact", reference["sha256"]))
                return reference

            commit = operations.prepared_mutation
            def commit_alias(store, operation, value, prepare, **identity):
                result = commit(store, operation, value, prepare, **identity)
                record_step(("alias", value["canonical_digest"], value["raw"]["sha256"]))
                return result

            def export():
                return _closure(seed, state, content, artifacts, write=True, common_records=store.snapshot()["records"])

            with mock.patch.object(ArtifactStore, "put", new=publish), \
                    mock.patch.object(operations, "prepared_mutation", new=commit_alias):
                if stop_after is None:
                    export()
                else:
                    with self.assertRaisesRegex(RuntimeError, "Interrupted chain publication"):
                        export()
            check_readable_origins()
            evidence, unavailable, _ = export()
            self.assertEqual(unavailable, [])
            self.assertEqual(evidence[0]["artifact"]["raw_identities"],
                             [expected_sources[key] for key in sorted(expected_sources)])
            for reference in references:
                artifacts.read(reference)
            check_readable_origins()
            self.assertEqual(controller.store.read(), native_before)
            return steps

        complete_steps = run_export()
        self.assertEqual(complete_steps[-1], ("artifact", target))
        for step in range(1, len(complete_steps) + 1):
            with self.subTest(interrupt_after=step):
                self.assertEqual(run_export(step), complete_steps[:step])

    def test_interrupted_non_ascii_chain_keeps_every_readable_form_exploratory(self):
        self.assert_interrupted_export_keeps_origins()

    def test_interrupted_mixed_cycle_keeps_an_ascii_probe_origin_on_both_encodings(self):
        self.assert_interrupted_export_keeps_origins(mixed_cycle=True)

    def test_non_ascii_chain_assessment_and_historical_replay_publish_nothing(self):
        from research_harness.storage import Store
        from search_controller.reviewer import _closure
        controller, state, content, references = self.build_unpublished_non_ascii_chain()
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        controller.status(full_audit=True)
        evidence, unavailable, _ = _closure({"evidence_digests": [references[2]["sha256"]]}, state, content,
            ArtifactStore(self.root), write=False, common_records=Store(self.root).snapshot()["records"])
        self.assertEqual(unavailable, [])
        self.assertEqual(len(evidence[0]["artifact"]["raw_identities"]), 2)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_interrupted_cycle_exports_utf8_from_an_ascii_original(self):
        self.assert_interrupted_export_keeps_origins(original=b'{"result":"\\u03bb"}', target_index=1)

    def test_interrupted_cycle_exports_ascii_from_an_ascii_original(self):
        self.assert_interrupted_export_keeps_origins(original=b'{"result":"\\u03bb"}', target_index=2)

    def test_interrupted_cycle_exports_ascii_from_a_utf8_original(self):
        self.assert_interrupted_export_keeps_origins(original='{"result":"λ"}'.encode(), target_index=2)

    def test_interrupted_cycle_exports_utf8_from_a_utf8_original(self):
        self.assert_interrupted_export_keeps_origins(original='{"result":"λ"}'.encode(), target_index=1)

    def assert_unreadable_canonical_original_refuses_export(self, *, corrupt):
        from research_harness.errors import ResearchError
        from research_harness.storage import Store
        from search_controller.reviewer import _closure
        controller, state, content, references = self.build_unpublished_non_ascii_chain(original=b'{"result":"\\u03bb"}')
        path = self.root / references[0]["path"]
        if corrupt:
            path.chmod(0o600)
            path.write_bytes(b"corrupt")
        else:
            path.unlink()
        store = Store(self.root)
        before = store.snapshot()
        for target_index in (1, 2):
            with self.subTest(target=references[target_index]["sha256"]):
                with self.assertRaises(ResearchError) as caught:
                    _closure({"evidence_digests": [references[target_index]["sha256"]]}, state, content, ArtifactStore(self.root),
                             write=True, common_records=before["records"])
                self.assertEqual(caught.exception.code, "artifact_corrupt" if corrupt else "artifact_missing")
                self.assertFalse((self.root / references[1]["path"]).exists())
                self.assertEqual(store.snapshot(), before)

    def test_chain_export_refuses_a_missing_canonical_original_before_exposing_its_normalization(self):
        self.assert_unreadable_canonical_original_refuses_export(corrupt=False)

    def test_chain_export_refuses_a_corrupt_canonical_original_before_exposing_its_normalization(self):
        self.assert_unreadable_canonical_original_refuses_export(corrupt=True)

    def test_interrupted_export_recovers_its_checked_alias_from_unchanged_original_bytes(self):
        raw, canonical = self.export_json_input(record_origin=False, interrupt_before_bytes=True)
        self.assertEqual(json.loads(raw), json.loads(canonical))

    def test_native_move_commit_retains_its_original_problem_bytes_and_identity(self):
        import attack
        from tests.search_execution_support import admit_workspace, begin_spec, invoke
        from tests.support import run
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        controller = admit_workspace(self.root)
        raw = (self.root / "sample/problem.json").read_bytes()
        identity = attack.compute_problem_digest(json.loads(raw))
        raw_digest = hashlib.sha256(raw).hexdigest()
        self.assertNotEqual(identity, raw_digest)
        invoke(controller, "begin", begin_spec())
        self.assertIn(raw_digest, controller.status()["service"]["import_identities"][identity])
        self.assertEqual(controller.store.get_artifact(raw_digest), raw)
