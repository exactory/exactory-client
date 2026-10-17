"""Probe bytes remain exploratory through every result import and later reference."""

import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

from development_fixtures import DevelopmentCase
from integration_fixtures import admit_lab
from research_harness.evidence import digest
from research_harness.errors import ResearchError


RESULT = b'{"result":{"values":[0,1,4,9],"bound":9},"validation":{"passed":true}}'


class ResearchProbeProvenanceTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()

    def save_probe(self, artifact, *, role="outputs", identifier="probe-1"):
        revision = self.store.revision + 1
        program = self.artifacts.put(b"print('exploratory fixture')\n", "text/x-python")
        probe = {"id": identifier, "command": "python probe.py", "outputs": [],
            "stdout": self.artifacts.put(b"fixture probe stdout\n", "text/plain"),
            "stderr": self.artifacts.put(b"fixture probe stderr\n", "text/plain"), "exit_status": 0,
            "elapsed_seconds": 0.01, "observation": "Exploratory fixture observation.", "revision": revision}
        if role == "outputs":
            probe[role] = [artifact]
        else:
            probe[role] = artifact
        intent = {"id": identifier, "question": "What does the exploratory fixture show?", "interpreter": sys.executable,
            "program": program, "inputs": [], "timeout_seconds": 1, "revision": revision}
        def prepare(transaction):
            transaction.put("probe_intent", identifier, intent)
            transaction.put("probe", identifier, probe)
            return probe
        self.store.mutate("fixture.probe", probe, prepare, expected_revision=self.store.revision,
                          request_id="fixture-" + identifier)
        return probe

    def build_import(self, artifact, identifier="imported-probe"):
        return {"id": identifier, "cycle_id": "cycle-1",
            "origin": {"kind": "imported", "original": {"run_id": "external-run", "executed_at": None,
                "provenance": self.artifacts.put(b"External import attribution; chronology unknown.\n", "text/plain")},
                "reason": "Retain earlier observations for independent assessment.",
                "deduction": "The import is history and grants no prospective execution credit."},
            "command": None, "status": "completed", "exit_code": 0,
            "usage": {"units": None, "reason": "Original usage is unknown."},
            "outputs": [{"id": "result", "requirement_id": "measurements", "artifact": artifact},
                        {"id": "validation", "requirement_id": "checks", "artifact": artifact}],
            "notes": "Retain the original bytes and chronology."}

    def export_native_json(self, raw):
        """Publish through the real native packet exporter into this common store."""
        harness = str(Path(__file__).resolve().parents[1] / "skills/math-solver/harness")
        with mock.patch.object(sys, "path", [harness] + sys.path):
            from search_controller.service import Controller
        claim = {"statement": self.objective["statement"], "quantifiers": "Every integer n in [0, 3]",
            "assumption_ids": [], "proof_policy": "reviewed",
            "scope": {"kind": "integer_interval", "lower": 0, "upper": 3,
                "lower_inclusive": True, "upper_inclusive": True}}
        contract = {"schema_version": 1, "original_claim": claim, "assumption_ids": [],
            "root_obligation": "obligation-000001", "root_attack_slug": "root", "requested_outcome": "proof",
            "proof_policy": "reviewed", "required_deliverables": ["A proof of the finite bound"],
            "resource_policy": {"max_total_moves": None, "max_total_runs": None, "max_workers": 1}}
        controller = Controller(self.root)
        controller.command("init", {"contract": contract}, 0, "native-init")
        (self.root / "noncanonical.json").write_bytes(raw)
        identity = digest(json.loads(raw))
        exported = controller.command("review-packet", {"kind": "result",
            "subject": {"claim": claim, "evidence_digests": [identity]}, "claim_digest": digest(claim),
            "reviewer": {"source": "host", "actor_id": "reviewer", "attestation_id": "review-assignment:probe-export"},
            "inputs": [{"path": "noncanonical.json", "kind": "blob", "digest": identity}]}, None, None)
        self.assertEqual(Path(exported["workspace"]).resolve(), self.root.resolve())
        packet = json.loads(self.artifacts.read(exported["native_packet"]))
        artifact = next(row["artifact"] for row in packet["evidence"] if row["digest"] == identity)
        self.assertEqual(artifact["raw_identities"][0]["sha256"], hashlib.sha256(raw).hexdigest())
        return artifact

    def launch_json_probe(self, raw):
        from research_harness.probe import launch_probe
        script = self.root / "normalization-probe.py"
        script.write_text("from pathlib import Path\nPath('result.json').write_bytes(" + repr(raw) + ")\n")
        return launch_probe(self.store, identifier="json-probe", question="What does this finite enumeration return?",
            script=str(script), timeout=2)["result"]

    def test_canonical_native_export_keeps_probe_origin_through_execution_import_and_result_reference(self):
        from research_harness.execution import record_imported_execution
        raw = json.dumps(json.loads(RESULT), indent=2).encode() + b"\n"
        self.launch_json_probe(raw)
        canonical = self.export_native_json(raw)
        expected = [{"probe_id": "json-probe", "sha256": canonical["sha256"],
            "raw_sha256": hashlib.sha256(raw).hexdigest(), "roles": ["outputs"]}]
        plan = self.plan()
        self.mutate(self.development().plan_cycle, plan)
        admission = self.admit()
        for index, operation in enumerate((self.development().record_execution, record_imported_execution)):
            payload = self.build_import(canonical, "canonical-import-" + str(index))
            saved = self.mutate(operation, payload)["result"]
            self.assertEqual(saved.get("probe_origins"), expected)
            before = self.store.snapshot()
            evidence = self.development()._Evidence(self.development()._Context(before["records"], self.artifacts))
            self.assertEqual(evidence.one(self.result_evidence(payload))["probe_origins"], expected)
            self.assertEqual(self.store.snapshot(), before)
        managed = dict(payload, id="canonical-managed", origin={"kind": "managed", "admission_id": admission["id"]},
            command=admission["command"], usage={"units": 1, "reason": "An attempted declarative relabel."})
        self.assert_error("probe_exploratory_origin", lambda: self.mutate(self.development().record_execution, managed))
        assessed = self.mutate(self.development().assess_cycle, self.assessment(plan, payload))["result"]
        self.assertIn("prospective_execution_missing", {item["code"] for item in assessed["obligations"]})

    def test_probe_origin_recorded_after_export_and_import_is_found_without_rewriting_history(self):
        raw = json.dumps(json.loads(RESULT), indent=2).encode() + b"\n"
        canonical = self.export_native_json(raw)
        plan = self.plan()
        self.mutate(self.development().plan_cycle, plan)
        payload = self.build_import(canonical)
        revision = self.store.revision
        saved = self.development().record_execution(self.store, payload, expected_revision=revision,
            request_id="before-probe")["result"]
        self.assertNotIn("probe_origins", saved)
        self.launch_json_probe(raw)
        before = self.store.snapshot()
        replay = self.development().record_execution(self.store, payload, expected_revision=revision,
            request_id="before-probe")["result"]
        self.assertEqual(replay, saved)
        evidence = self.development()._Evidence(self.development()._Context(before["records"], self.artifacts))
        self.assertEqual(evidence.one(self.result_evidence(payload)).get("probe_origins"), [
            {"probe_id": "json-probe", "sha256": canonical["sha256"],
             "raw_sha256": hashlib.sha256(raw).hexdigest(), "roles": ["outputs"]}])
        self.assertEqual(self.store.snapshot(), before)
        later = self.mutate(self.development().record_execution, dict(payload, id="after-probe"))["result"]
        self.assertTrue(later.get("probe_origins"))
        assessed = self.mutate(self.development().assess_cycle, self.assessment(plan, payload))["result"]
        self.assertIn("prospective_execution_missing", {item["code"] for item in assessed["obligations"]})

    def assert_export_preserves_recorded_result(self, *, record_origin):
        from research_harness import operations
        from research_harness.artifacts import ArtifactStore, describe_artifact
        from research_harness.probe_provenance import record_probe_artifact_origin
        self.mutate(self.development().plan_cycle, self.plan())
        for index, boundary in enumerate(("raw", "alias", "canonical")):
            with self.subTest(boundary=boundary, record_origin=record_origin):
                raw = ('{ "result" : %d }\n' % (7 + index)).encode()
                original = describe_artifact(raw, "application/json")
                canonical = self.artifacts.put(('{"result":%d}' % (7 + index)).encode(), "application/json")
                payload = self.build_import(canonical, "before-export-" + boundary)
                saved = self.mutate(self.development().record_execution, payload)["result"]
                reference = self.result_evidence(payload)
                if record_origin:
                    self.save_pending_launch(identifier="probe-" + boundary)
                    record_probe_artifact_origin(self.root, "probe-" + boundary, original["sha256"], "outputs")
                expected_origins = ([{"probe_id": "probe-" + boundary, "sha256": canonical["sha256"],
                    "raw_sha256": original["sha256"], "roles": ["outputs"]}] if record_origin else [])

                def read_evidence():
                    before = self.store.snapshot()
                    evidence = self.development()._Evidence(self.development()._Context(before["records"], self.artifacts))
                    try:
                        result = evidence.one(reference)
                    except ResearchError as error:
                        self.fail("Existing result became unreadable: " + error.code)
                    self.assertEqual(self.store.snapshot(), before)
                    self.assertEqual(before["records"]["execution"][payload["id"]], saved)
                    return result

                baseline = read_evidence()
                observed = []

                def inspect_boundary(stage):
                    observed.append(stage)
                    self.assertEqual(observed, ["raw", "alias", "canonical"][:len(observed)])
                    result = read_evidence()
                    self.assertEqual({key: value for key, value in result.items() if key != "probe_origins"}, baseline)
                    self.assertEqual(result.get("probe_origins", []), expected_origins if "alias" in observed else [])
                    if stage == boundary:
                        raise RuntimeError("Interrupted at " + boundary)

                put = ArtifactStore.put
                def publish(artifacts, data, media_type):
                    result = put(artifacts, data, media_type)
                    if data == raw:
                        inspect_boundary("raw")
                    elif result["sha256"] == canonical["sha256"]:
                        inspect_boundary("canonical")
                    return result

                commit = operations.prepared_mutation
                def commit_alias(store, operation, value, prepare, **identity):
                    result = commit(store, operation, value, prepare, **identity)
                    if operation == "native_math.import_identity":
                        inspect_boundary("alias")
                    return result

                with mock.patch.object(ArtifactStore, "put", new=publish), \
                        mock.patch.object(operations, "prepared_mutation", new=commit_alias):
                    with self.assertRaisesRegex(RuntimeError, "Interrupted at " + boundary):
                        self.export_native_json(raw)
                read_evidence()
                exported = self.export_native_json(raw)
                self.assertEqual(exported["sha256"], canonical["sha256"])
                self.assertEqual(self.artifacts.read(original), raw)
                self.assertEqual(read_evidence().get("probe_origins", []), expected_origins)

    def test_interrupted_native_export_preserves_recorded_result_without_a_probe(self):
        self.assert_export_preserves_recorded_result(record_origin=False)

    def test_interrupted_native_export_preserves_recorded_result_and_completed_probe_origin(self):
        self.assert_export_preserves_recorded_result(record_origin=True)

    def test_shared_alias_chain_authenticates_each_edge_and_keeps_the_requested_output(self):
        from research_harness.probe_provenance import find_probe_artifact_origins
        raw = self.artifacts.put(b'{ "result": "\\u03bb" }\n', "application/json")
        middle = self.artifacts.put(b'{"result":"\\u03bb"}', "application/json")
        canonical = self.artifacts.put('{"result":"λ"}'.encode(), "application/json")
        self.save_probe(raw)
        aliases = [(canonical, middle), (middle, raw), (middle, canonical)]
        def record_aliases(transaction):
            for index, (output, original) in enumerate(aliases):
                transaction.put("native_import_identity", str(index),
                    {"schema_version": 1, "canonical_digest": output["sha256"], "raw": original})
        self.store.mutate("fixture.aliases", {}, record_aliases, expected_revision=self.store.revision,
            request_id="alias-chain")
        before = self.store.snapshot()
        expected = [{"probe_id": "probe-1", "sha256": canonical["sha256"],
            "raw_sha256": raw["sha256"], "roles": ["outputs"]}]
        self.assertEqual(find_probe_artifact_origins(before["records"], [canonical], artifacts=self.artifacts), expected)
        self.assertEqual(self.store.snapshot(), before)
        # The canonical object remains valid; a missing or corrupt intermediate
        # original must still fail rather than silently discard its probe origin.
        (self.root / middle["path"]).chmod(0o600)
        (self.root / middle["path"]).write_bytes(b"corrupt")
        self.assert_error("artifact_corrupt", lambda: find_probe_artifact_origins(
            before["records"], [canonical], artifacts=self.artifacts))

    def test_shared_lookup_refuses_an_unauthenticated_or_forged_normalization_alias(self):
        from research_harness.probe_provenance import find_probe_artifact_origins
        raw = self.artifacts.put(b'{ "other": 8 }\n', "application/json")
        canonical = self.artifacts.put(b'{"result":7}', "application/json")
        records = {"native_import_identity": {"forged":
            {"schema_version": 1, "canonical_digest": canonical["sha256"], "raw": raw}}}
        self.assert_error("invalid_import_identity", lambda: find_probe_artifact_origins(records, [canonical]))
        self.assert_error("invalid_import_identity", lambda: find_probe_artifact_origins(
            records, [canonical], artifacts=self.artifacts))

    def test_completed_managed_canonical_output_keeps_its_verified_origin_after_a_later_probe(self):
        from research_harness.execution import launch_execution
        canonical = self.export_native_json(b'{ "metric": 7 }\n')
        admission = admit_lab(self, body="import sys\nsys.stdout.write('{\"metric\":7}')\n")
        self.assertTrue(launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
            request_id="canonical-rerun")["ok"])
        self.launch_json_probe(b'{ "metric": 7 }\n')
        before = self.store.snapshot()
        records = before["records"]
        identifier = records["execution_outcome"][admission["id"]]["execution_id"]
        artifact = records["execution"][identifier]["payload"]["outputs"][0]["artifact"]
        self.assertEqual(artifact["sha256"], canonical["sha256"])
        reference = {"kind": "result", "execution_id": identifier, "output_id": "result", "artifact": artifact,
            "locator": {"kind": "json", "pointer": "/metric", "value": 7}}
        evidence = self.development()._Evidence(self.development()._Context(records, self.artifacts)).one(reference)
        self.assertEqual(evidence["origin"]["kind"], "managed")
        self.assertNotIn("probe_origins", evidence)
        self.assertEqual(self.store.snapshot(), before)

    def test_late_probe_origin_keeps_unsealed_historical_result_readable_as_exploratory(self):
        from research_harness import execution
        from research_harness.execution_evidence import validate_observed_execution
        from research_harness.storage import _canonical
        from research_harness.workspace import strict_json
        canonical = self.export_native_json(b'{ "metric": 7 }\n')
        admission = admit_lab(self, body="import sys\nsys.stdout.write('{\"metric\":7}')\n")
        original = execution.prepared_mutation
        def retain_legacy_observation(store, operation, payload, prepare, **identity):
            if operation == "execution.observe":
                def prepare_legacy(records, value):
                    changes, observation = prepare(records, value)
                    terminal = strict_json(self.artifacts.read(observation["terminal"]))
                    terminal.pop("output_seal")
                    observation["terminal"] = self.artifacts.put(_canonical(terminal).encode(), "application/json")
                    observation.pop("files")
                    return changes, observation
                return original(store, operation, payload, prepare_legacy, **identity)
            return original(store, operation, payload, prepare, **identity)
        with mock.patch.object(execution, "prepared_mutation", side_effect=retain_legacy_observation):
            execution.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                request_id="legacy-canonical-run")
        self.launch_json_probe(b'{ "metric": 7 }\n')
        before = self.store.snapshot()
        records = before["records"]
        identifier = records["execution_outcome"][admission["id"]]["execution_id"]
        artifact = records["execution"][identifier]["payload"]["outputs"][0]["artifact"]
        self.assertEqual(artifact["sha256"], canonical["sha256"])
        reference = {"kind": "result", "execution_id": identifier, "output_id": "result", "artifact": artifact,
            "locator": {"kind": "json", "pointer": "/metric", "value": 7}}
        evidence = self.development()._Evidence(self.development()._Context(records, self.artifacts)).one(reference)
        self.assertEqual(evidence["origin"]["kind"], "exploratory")
        self.assertTrue(evidence["probe_origins"])
        self.assert_error("execution_output_seal_required", lambda: validate_observed_execution(records, self.artifacts, identifier))
        self.assertEqual(self.store.snapshot(), before)

    def save_pending_launch(self, identifier="pending-probe", output_dir="results", initial=None):
        from research_harness.probe import record_probe_intent, record_probe_launch
        from test_research_probe_lifecycle import build_probe_intent
        self.mutate(record_probe_intent, build_probe_intent(self.artifacts, identifier))
        launch = {"id": identifier, "directory": "research/probes/" + identifier + "/run-fixture",
            "output_dir": output_dir, "initial": initial or {}}
        (self.root / launch["directory"] / "work" / output_dir).mkdir(parents=True)
        self.mutate(record_probe_launch, launch)
        return launch

    def test_real_worker_artifacts_cannot_be_laundered_before_outcome_commit(self):
        from research_harness import execution, probe
        plan = self.plan()
        self.mutate(self.development().plan_cycle, plan)
        admission = self.admit()
        script = self.root / "gap-probe.py"
        script.write_text("from pathlib import Path\nvalue = " + repr(RESULT.decode())
                          + "\nPath('result.json').write_text(value + '\\n')\nprint(value)\n")
        original = probe.record_probe
        imported = []
        def import_before_outcome(store, payload, **identity):
            if not imported:
                self.assertNotIn(payload["id"], store.snapshot()["records"].get("probe", {}))
                value = self.build_import(dict(payload["stdout"], media_type="application/json"), "gap-import")
                saved = self.mutate(execution.record_imported_execution, value)["result"]
                self.assertEqual(saved["probe_origins"], [{"probe_id": payload["id"],
                    "sha256": value["outputs"][0]["artifact"]["sha256"], "roles": ["outputs", "stdout"]}])
                managed = dict(value, id="gap-managed", origin={"kind": "managed", "admission_id": admission["id"]},
                    command=admission["command"], usage={"units": 1, "reason": "A declarative managed label."})
                self.assert_error("probe_exploratory_origin", lambda: self.mutate(self.development().record_execution, managed))
                assessed = self.mutate(self.development().assess_cycle, self.assessment(plan, value))["result"]
                self.assertIn("prospective_execution_missing", {item["code"] for item in assessed["obligations"]})
                imported.append(saved)
            return original(store, payload, **identity)
        with mock.patch.object(probe, "record_probe", side_effect=import_before_outcome):
            probe.launch_probe(self.store, identifier="gap-probe", question="What do the exploratory bytes show?",
                               script=str(script), timeout=2)
        records = self.store.snapshot()["records"]
        self.assertEqual(len(imported), 1)
        self.assertNotIn(admission["id"], records.get("execution_outcome", {}))
        self.assertIn("gap-probe", records["probe"])

    def test_same_bytes_input_rewrite_with_changed_timestamp_is_retained_as_output(self):
        from research_harness.probe import launch_probe
        from research_harness.probe_provenance import find_probe_artifact_origins
        path = self.root / "rewrite-input.json"
        path.write_bytes(RESULT)
        script = self.root / "rewrite-input.py"
        script.write_text("from pathlib import Path\nimport os, sys\npath = Path(sys.argv[1])\n"
            "before = path.stat()\npath.write_bytes(path.read_bytes())\n"
            "os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2000000000))\n")
        saved = launch_probe(self.store, identifier="same-bytes-rewrite", question="Was the copied input rewritten?",
            script=str(script), inputs=[str(path)], timeout=2)["result"]
        self.assertEqual([self.artifacts.read(artifact) for artifact in saved["outputs"]], [RESULT])
        self.assertEqual(find_probe_artifact_origins(self.store.snapshot()["records"], saved["outputs"]),
            [{"probe_id": saved["id"], "sha256": saved["outputs"][0]["sha256"], "roles": ["outputs"]}])

    def test_pending_launch_local_pins_retain_output_and_raw_stream_origins(self):
        from research_harness.execution import record_imported_execution
        from research_harness.integration import pin_artifact
        launch = self.save_pending_launch()
        self.mutate(self.development().plan_cycle, self.plan())
        for role, suffix in (("outputs", "/work/results/result.json"), ("stdout", "/stdout"), ("stderr", "/stderr")):
            relative = launch["directory"] + suffix
            (self.root / relative).write_bytes(RESULT + role.encode())
            pinned = self.mutate(pin_artifact, {"id": "pending-" + role, "path": relative,
                "media_type": "application/octet-stream"})["result"]
            saved = self.mutate(record_imported_execution, self.build_import(pinned["artifact"], "pending-import-" + role))["result"]
            self.assertEqual(saved["probe_origins"], [{"probe_id": launch["id"],
                "sha256": pinned["artifact"]["sha256"], "roles": [role]}])
        self.assertNotIn("probe", self.store.snapshot()["records"])

    def test_prior_imports_stay_exact_after_matching_pending_probe_local_pin(self):
        from research_harness.integration import pin_artifact
        self.mutate(self.development().plan_cycle, self.plan())
        payload = self.build_import(self.artifacts.put(RESULT, "application/json"), "prior-import")
        saved = self.mutate(self.development().record_execution, payload)["result"]
        launch = self.save_pending_launch()
        relative = launch["directory"] + "/stdout"
        (self.root / relative).write_bytes(RESULT)
        self.mutate(pin_artifact, {"id": "later-probe-pin", "path": relative, "media_type": "application/json"})
        self.assertEqual(self.store.snapshot()["records"]["execution"][payload["id"]], saved)
        evidence = self.development()._Evidence(self.development()._Context(self.store.snapshot()["records"], self.artifacts))
        self.assertEqual(evidence.one(self.result_evidence(payload))["probe_origins"], [
            {"probe_id": launch["id"], "sha256": payload["outputs"][0]["artifact"]["sha256"], "roles": ["stdout"]}])

    def test_pinning_registers_origin_before_publishing_bytes_and_retries_only_stale(self):
        from research_harness import probe_provenance
        from research_harness.artifacts import ArtifactStore, describe_artifact
        self.save_pending_launch()
        data = b"Unpublished exploratory output bytes."
        descriptor = describe_artifact(data, "text/plain")
        original = ArtifactStore.put
        def inspect_publication(artifacts, value, media_type):
            self.assertEqual(probe_provenance.find_probe_artifact_origins(self.store.snapshot()["records"], [descriptor]),
                [{"probe_id": "pending-probe", "sha256": descriptor["sha256"], "roles": ["outputs"]}])
            self.assertFalse((self.root / descriptor["path"]).exists())
            return original(artifacts, value, media_type)
        prepare = probe_provenance.prepared_mutation
        attempts = []
        def race_once(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise ResearchError("stale_revision", "Fixture concurrent mutation")
            return prepare(*args, **kwargs)
        with mock.patch.object(probe_provenance, "prepared_mutation", side_effect=race_once):
            with mock.patch.object(ArtifactStore, "put", new=inspect_publication):
                result = probe_provenance.pin_probe_artifact(self.root, "pending-probe", data, "text/plain", "outputs")
        self.assertEqual(result, descriptor)
        self.assertEqual(len(attempts), 2)
        with mock.patch.object(probe_provenance, "prepared_mutation", side_effect=ResearchError("storage_busy", "Fixture lock")) as failed:
            self.assert_error("storage_busy", lambda: probe_provenance.pin_probe_artifact(
                self.root, "pending-probe", b"Never published", "text/plain", "stdout"))
        self.assertEqual(failed.call_count, 1)

    def test_raw_stream_identity_is_registered_before_oversized_prefix_publication(self):
        from research_harness import probe_provenance, probe_worker
        from research_harness.artifacts import ArtifactStore, describe_artifact
        launch = self.save_pending_launch()
        data = b"A full raw stream that exceeds the fixture bound."
        (self.root / launch["directory"] / "stdout").write_bytes(data)
        full = describe_artifact(data, "text/plain")
        original = ArtifactStore.put
        def inspect_prefix(artifacts, value, media_type):
            self.assertEqual(probe_provenance.find_probe_artifact_origins(self.store.snapshot()["records"], [full]),
                [{"probe_id": launch["id"], "sha256": full["sha256"], "roles": ["stdout"]}])
            return original(artifacts, value, media_type)
        with mock.patch.object(probe_worker, "STREAM_LIMIT", 8), mock.patch.object(ArtifactStore, "put", new=inspect_prefix):
            outcome = probe_worker.capture_probe_outcome(self.root, dict(launch, command="python fixture.py"),
                {"exit_status": 0, "elapsed_seconds": 0.1})
        self.assertEqual(self.artifacts.read(outcome["stdout"]), data[:8])
        self.assertEqual(outcome["exit_status"], "output_bound")

    def test_origin_registration_validates_intent_identity_role_and_replays_per_role(self):
        from research_harness.probe_provenance import find_probe_artifact_origins, pin_probe_artifact, record_probe_artifact_origin
        from research_harness.artifacts import describe_artifact
        data = b"An output whose origin must precede publication."
        descriptor = describe_artifact(data, "text/plain")
        self.assert_error("probe_intent_missing", lambda: pin_probe_artifact(self.root, "missing", data, "text/plain", "stdout"))
        self.assertFalse((self.root / descriptor["path"]).exists())
        self.save_pending_launch()
        for probe_id, sha256, role in (("../unsafe", descriptor["sha256"], "stdout"),
                                      ("pending-probe", "A" * 64, "stdout"),
                                      ("pending-probe", descriptor["sha256"], "inputs")):
            self.assert_error("invalid_input", lambda: record_probe_artifact_origin(self.root, probe_id, sha256, role))
        pin_probe_artifact(self.root, "pending-probe", data, "text/plain", "stdout")
        before = self.store.snapshot()
        pin_probe_artifact(self.root, "pending-probe", data, "text/plain", "stdout")
        self.assertEqual(self.store.snapshot(), before)
        pin_probe_artifact(self.root, "pending-probe", data, "text/plain", "stderr")
        self.assertEqual(find_probe_artifact_origins(self.store.snapshot()["records"], [descriptor]),
            [{"probe_id": "pending-probe", "sha256": descriptor["sha256"], "roles": ["stderr", "stdout"]}])

    def test_both_import_paths_pin_probe_origin_by_bytes_despite_media_relabeling(self):
        from research_harness import execution
        artifact = self.artifacts.put(RESULT, "application/json")
        self.save_probe(self.artifacts.put(RESULT, "text/plain"))
        self.mutate(self.development().plan_cycle, self.plan())
        for index, operation in enumerate((self.development().record_execution, execution.record_imported_execution)):
            payload = self.build_import(artifact, "import-" + str(index))
            record = self.mutate(operation, payload)["result"]
            self.assertEqual(record["payload"], payload)
            self.assertEqual(record["probe_origins"], [{"probe_id": "probe-1", "sha256": artifact["sha256"], "roles": ["outputs"]}])
            self.assertIsNone(record["admitted_revision"])
        self.assertNotIn("execution_outcome", self.store.snapshot()["records"])

    def test_imports_of_each_probe_stream_as_outputs_keep_the_stream_origin(self):
        from research_harness.execution import record_imported_execution
        self.mutate(self.development().plan_cycle, self.plan())
        for index, role in enumerate(("stdout", "stderr")):
            artifact = self.artifacts.put(RESULT + str(index).encode(), "application/octet-stream")
            self.save_probe(artifact, role=role, identifier="probe-" + role)
            record = self.mutate(record_imported_execution, self.build_import(artifact, "import-" + role))["result"]
            self.assertEqual(record["probe_origins"], [{"probe_id": "probe-" + role,
                "sha256": artifact["sha256"], "roles": [role]}])

    def test_import_of_full_raw_stream_uses_catalog_when_outcome_retains_only_a_prefix(self):
        from research_harness.execution import record_imported_execution
        artifact = self.artifacts.put(RESULT + b"\n", "application/json")
        self.save_probe(self.artifacts.put(RESULT[:12], "text/plain"), role="stdout")
        origin = {"probe_id": "probe-1", "sha256": artifact["sha256"], "roles": ["stdout"]}
        def prepare(transaction):
            transaction.put("probe_artifact", "probe-1:" + artifact["sha256"], origin)
            return origin
        self.store.mutate("fixture.probe_artifact", origin, prepare, expected_revision=self.store.revision,
                          request_id="fixture-full-stream-origin")
        self.mutate(self.development().plan_cycle, self.plan())
        record = self.mutate(record_imported_execution, self.build_import(artifact))["result"]
        self.assertEqual(record["probe_origins"], [origin])

    def test_managed_label_cannot_launder_probe_bytes_through_low_level_recording(self):
        self.mutate(self.development().plan_cycle, self.plan())
        admission = self.admit()
        payload = self.execution(admission)
        self.save_probe(payload["outputs"][0]["artifact"])
        before = self.store.snapshot()
        self.assert_error("probe_exploratory_origin", lambda: self.mutate(self.development().record_execution, payload))
        self.assertEqual(self.store.snapshot(), before)

    def test_probe_origin_survives_assessment_and_result_references_without_prospective_credit(self):
        artifact = self.artifacts.put(RESULT, "application/json")
        self.save_probe(artifact)
        plan = self.plan()
        self.mutate(self.development().plan_cycle, plan)
        payload = self.build_import(artifact)
        record = self.mutate(self.development().record_execution, payload)["result"]
        assessed = self.mutate(self.development().assess_cycle, self.assessment(plan, payload))["result"]
        self.assertTrue(assessed["validated_result"])
        self.assertIn("prospective_execution_missing", {item["code"] for item in assessed["obligations"]})
        self.save_checkpoint()
        candidate = self.candidate()
        evidence = self.development()._Evidence(self.development()._Context(self.store.snapshot()["records"], self.artifacts))
        referenced = evidence.one(self.result_evidence(payload))
        self.assertEqual(referenced["probe_origins"], record["probe_origins"])
        self.assertIn("prospective_execution_missing", self.readiness_codes())
        self.assertFalse(self.development().readiness_report(self.store)["ready"])
        self.assertIsNotNone(candidate)

    def test_ordinary_import_preserves_exact_record_and_report_with_unrelated_probe(self):
        artifact = self.artifacts.put(RESULT, "application/json")
        self.save_probe(self.artifacts.put(b"unrelated exploratory bytes", "text/plain"))
        plan = self.plan()
        pinned = self.mutate(self.development().plan_cycle, plan)["result"]
        payload = self.build_import(artifact, "ordinary-import")
        revision, request_id = self.store.revision, "ordinary-import-request"
        record = self.development().record_execution(self.store, payload, expected_revision=revision, request_id=request_id)["result"]
        expected = {"id": payload["id"], "payload": payload, "plan_digest": pinned["digest"],
            "recorded_revision": revision + 1, "request_id": request_id, "admitted_revision": None}
        expected["digest"] = digest(expected)
        self.assertEqual(record, expected)
        self.assertTrue(self.mutate(self.development().assess_cycle, self.assessment(plan, payload))["result"]["validated_result"])
        self.save_checkpoint()
        before = self.development().readiness_state(self.store.snapshot()["records"], self.artifacts)
        self.save_probe(self.artifacts.put(b"second unrelated exploratory bytes", "text/plain"), identifier="probe-2")
        self.assertEqual(self.development().readiness_state(self.store.snapshot()["records"], self.artifacts), before)
        evidence = self.development()._Evidence(self.development()._Context(self.store.snapshot()["records"], self.artifacts))
        self.assertNotIn("probe_origins", evidence.one(self.result_evidence(payload)))

    def test_real_observed_rerun_can_validate_the_same_bytes_as_an_earlier_probe(self):
        from research_harness.execution import launch_execution
        from research_harness.execution_evidence import validate_observed_execution
        artifact = self.artifacts.put(b'{"metric": 7}\n', "text/plain")
        self.save_probe(artifact)
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        result = launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="observe-rerun")
        self.assertTrue(result["ok"])
        records = self.store.snapshot()["records"]
        identifier = records["execution_outcome"][admission["id"]]["execution_id"]
        record = records["execution"][identifier]
        self.assertEqual(record["payload"]["outputs"][0]["artifact"]["sha256"], artifact["sha256"])
        self.assertNotIn("probe_origins", record)
        self.assertIsNotNone(validate_observed_execution(records, self.artifacts, identifier)["observation"])

    def test_observed_identical_rerun_recovers_once_after_losing_observation_publication(self):
        from research_harness import execution
        self.save_probe(self.artifacts.put(b'{"metric": 7}\n', "text/plain"))
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        prepare = execution.prepared_mutation
        def interrupt_observation(store, operation, *args, **kwargs):
            if operation == "execution.observe":
                raise OSError("Fixture interruption before observation publication")
            return prepare(store, operation, *args, **kwargs)
        with mock.patch.object(execution, "prepared_mutation", side_effect=interrupt_observation):
            with self.assertRaisesRegex(OSError, "Fixture interruption"):
                execution.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                           request_id="interrupted-identical-rerun")
        before = self.store.snapshot()["records"]
        self.assertEqual(len(before["execution"]), 1)
        self.assertNotIn("execution_observation", before)
        result = execution.reconcile_execution(self.store, {"admission_id": admission["id"]},
            expected_revision=self.store.revision, request_id="recover-identical-rerun")
        self.assertTrue(result["ok"])
        after = self.store.snapshot()["records"]
        self.assertEqual(after["execution"], before["execution"])
        self.assertEqual(after["strategy_account"], before["strategy_account"])
        self.assertIn(admission["id"], after["execution_observation"])

    def test_interrupted_recovery_retains_matching_probe_bytes_without_prospective_credit(self):
        self.assert_interrupted_recovery_keeps_probe_origins(late_origin=False)

    def test_late_probe_origin_keeps_interrupted_recovery_readable_without_prospective_credit(self):
        self.assert_interrupted_recovery_keeps_probe_origins(late_origin=True)

    def assert_interrupted_recovery_keeps_probe_origins(self, *, late_origin):
        from research_harness import execution
        artifact = self.artifacts.put(RESULT, "application/json")
        if not late_origin:
            self.save_probe(artifact)
        admission = admit_lab(self, body="raise RuntimeError('must never launch')\n",
            outputs=[{"id": "result", "requirement_id": "measurements", "path": "stdout", "media_type": "application/json"},
                     {"id": "validation", "requirement_id": "checks", "path": "stderr", "media_type": "application/json"}])
        execution._claim(self.store, admission["id"], self.store.revision, "interrupted-probe-reuse")
        directory = self.root / execution._directory(admission["id"])
        (directory / "stdout").write_bytes(RESULT)
        (directory / "stderr").write_bytes(RESULT)
        result = execution.reconcile_execution(self.store, {"admission_id": admission["id"], "resolution": "interrupted",
            "reason": "The producer has no live owner and retained only partial bytes."},
            expected_revision=self.store.revision, request_id="recover-partial-probe-reuse")
        self.assertFalse(result["ok"])
        if late_origin:
            self.save_probe(artifact)
        records = self.store.snapshot()["records"]
        identifier = records["execution_outcome"][admission["id"]]["execution_id"]
        record = records["execution"][identifier]
        self.assertEqual(record["payload"]["status"], "interrupted")
        origins = [{"probe_id": "probe-1", "sha256": artifact["sha256"], "roles": ["outputs"]}]
        if late_origin:
            self.assertNotIn("probe_origins", record)
        else:
            self.assertEqual(record["probe_origins"], origins)
        reference = {"kind": "result", "execution_id": identifier, "output_id": "result", "artifact": artifact,
            "locator": {"kind": "json", "pointer": "/result", "value": {"values": [0, 1, 4, 9], "bound": 9}}}
        evidence = self.development()._Evidence(self.development()._Context(records, self.artifacts)).one(reference)
        self.assertEqual(evidence["origin"]["kind"], "exploratory")
        self.assertEqual(evidence["probe_origins"], origins)
        self.assertEqual(records["strategy_account"][admission["strategy_key"]]["charged_units"], admission["reserved_units"])
        assessment = self.assessment(records["cycle_plan"][admission["cycle_id"]]["payload"], record["payload"])
        assessment["validity_checks"][0]["status"] = "unresolved"
        assessed = self.mutate(self.development().assess_cycle, assessment)["result"]
        self.assertFalse(assessed["validated_result"])
        self.assertTrue({"prospective_execution_missing", "result_execution_incomplete"}
                        <= {item["code"] for item in assessed["obligations"]})


class ResearchProbeOriginLookupTests(unittest.TestCase):
    def test_shared_lookup_bounds_the_identity_closure(self):
        from research_harness.probe_provenance import find_probe_artifact_origins
        descriptors = [{"sha256": format(index, "064x")} for index in range(10001)]
        with self.assertRaises(ResearchError) as caught:
            find_probe_artifact_origins({}, descriptors)
        self.assertEqual(caught.exception.code, "dependency_cycle")

    def test_pending_local_paths_exclude_unchanged_copied_inputs_and_sibling_directories(self):
        from research_harness.probe_provenance import find_probe_artifact_origins
        first = {"sha256": "a" * 64}
        changed = {"sha256": "b" * 64}
        root = "research/probes/pending/run-fixture"
        launch = {"id": "pending", "directory": root, "output_dir": ".",
            "initial": {"program/program.py": {"sha256": first["sha256"]}, "inputs/0/input.txt": {"sha256": first["sha256"]}}}
        local = {"program": {"path": root + "/work/program/program.py", "artifact": first},
            "input": {"path": root + "/work/inputs/0/input.txt", "artifact": first},
            "sibling": {"path": root + "-other/work/result.txt", "artifact": first},
            "changed": {"path": root + "/work/inputs/0/input.txt", "artifact": changed}}
        records = {"probe_launch": {"pending": launch}, "local_artifact": local}
        self.assertEqual(find_probe_artifact_origins(records, [first, changed]), [
            {"probe_id": "pending", "sha256": changed["sha256"], "roles": ["outputs"]}])
        scoped = dict(launch, output_dir="results")
        self.assertEqual(find_probe_artifact_origins(dict(records, probe_launch={"pending": scoped}), [changed]), [])

    def test_lookup_collects_all_probe_roles_by_sha256_and_ignores_inputs(self):
        from research_harness.probe_provenance import find_probe_artifact_origins
        first = {"sha256": "a" * 64, "media_type": "text/plain"}
        relabeled = dict(first, media_type="application/json")
        second = {"sha256": "b" * 64, "media_type": "text/plain"}
        records = {"probe_intent": {"probe-2": {"program": second, "inputs": [second]}},
            "probe": {"probe-2": {"outputs": [first, first], "stdout": first, "stderr": second},
                      "probe-1": {"outputs": [], "stdout": first, "stderr": second}}}
        self.assertEqual(find_probe_artifact_origins(records, [relabeled, first]), [
            {"probe_id": "probe-1", "sha256": first["sha256"], "roles": ["stdout"]},
            {"probe_id": "probe-2", "sha256": first["sha256"], "roles": ["outputs", "stdout"]}])
        self.assertEqual(find_probe_artifact_origins({"probe_intent": records["probe_intent"]}, [second]), [])
        self.assertEqual(find_probe_artifact_origins({}, [first]), [])
