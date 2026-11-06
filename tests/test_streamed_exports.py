"""Verified exports bound payload copies and publish only completed directories."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from research_harness import native_export, review_delivery, scientific_delivery
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from literature_fixtures import LiteratureCase
from rounds_fixtures import RoundsCase


@contextmanager
def capture_evaluations(*, force_budget=None):
    evaluations = []
    initialize = Evaluation.__init__

    def observe(evaluation, records, artifacts, **kwargs):
        if force_budget is not None:
            kwargs["retention_budget_bytes"] = force_budget
        initialize(evaluation, records, artifacts, **kwargs)
        evaluations.append(evaluation)

    with mock.patch.object(Evaluation, "__init__", observe):
        yield evaluations


def assert_shared_retains_nothing(case, evaluations):
    """The non-retaining evaluations of a scientific delivery retain nothing. Each projected source is read through a
    reader of its own (its byte view refuses any other artifact), so that reader's parse serves only the source's
    locators and goes with the reader."""
    shared = [evaluation for evaluation in evaluations if evaluation.retention_budget_bytes == 0]
    case.assertTrue(shared, "the delivery must read through a non-retaining evaluation")
    for evaluation in shared:
        report = evaluation.retention_report()
        case.assertEqual((report["optional_bytes"], report["optional_entries"]), (0, 0), report)


def assert_no_retention(case, evaluations):
    case.assertTrue(evaluations, "the export must exercise evaluation reads")
    case.assertGreater(sum(e.counters["reads"] for e in evaluations), 0)
    for evaluation in evaluations:
        report = evaluation.retention_report()
        case.assertEqual(report["optional_bytes"], 0, report)
        case.assertEqual(report["optional_entries"], 0, report)
        case.assertEqual(report["budget_bytes"], 0, report)


class DeliveryEvaluationRetentionTests(RoundsCase):
    def collect_files(self, destination):
        return {path.relative_to(destination).as_posix(): path.read_bytes()
                for path in destination.rglob("*") if path.is_file()}

    def check_delivery_retention(self, deliver):
        control, destination = self.root / "retaining", self.root / "nonretaining"
        with capture_evaluations(force_budget=64 * 1024 * 1024) as baseline:
            before = deliver(control)
        self.assertTrue(any(e.retention_report()["optional_bytes"] for e in baseline))
        with capture_evaluations() as evaluations:
            after = deliver(destination)
        self.assertEqual(self.collect_files(destination), self.collect_files(control))
        self.assertEqual(after["artifacts"], before["artifacts"])
        for path in destination.rglob("*"):
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o222, 0, str(path))
        assert_no_retention(self, evaluations)

    def test_readiness_construction_retains_no_optional_values(self):
        self.check_delivery_retention(lambda path: review_delivery.deliver_readiness(self.store, path))

    def test_manuscript_claims_and_packet_retain_no_optional_values(self):
        self.pin(self.claims(revised=("bound",)), reviews=0, measure=False)
        self.check_delivery_retention(lambda path: review_delivery.deliver_manuscript(self.store, path))

    def test_round_bundle_and_packet_retain_no_optional_values(self):
        from research_harness import rounds
        bundle = self.pin()
        self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))
        self.check_delivery_retention(lambda path: review_delivery.deliver_round(self.store, path))

    def test_native_synthesis_retains_no_optional_values(self):
        attack = self.root / "attack"
        attack.mkdir()
        control, destination = attack / "retaining", attack / "nonretaining"
        with capture_evaluations(force_budget=64 * 1024 * 1024):
            before = native_export.export_native(self.store, attack, control)
        with capture_evaluations() as evaluations:
            after = native_export.export_native(self.store, attack, destination)
        self.assertEqual((destination / "snapshot.json").read_bytes(), (control / "snapshot.json").read_bytes())
        for path in destination.rglob("*"):
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o222, 0, str(path))
        self.assertEqual(after["foundation"], before["foundation"])
        self.assertEqual([(item["kind"], item["digest"]) for item in after["inputs"]],
                         [(item["kind"], item["digest"]) for item in before["inputs"]])
        for ref in review_delivery.references(self.store.snapshot()["records"]):
            self.assertEqual((destination / "artifacts" / ref["sha256"]).read_bytes(),
                             (control / "artifacts" / ref["sha256"]).read_bytes())
        assert_no_retention(self, evaluations)


class ScientificEvaluationRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))
        self.refs = [self.artifacts.put(json.dumps({"value": i, "padding": "x" * 16000}).encode(),
                                        "application/json") for i in range(16)]
        self.locators = [{"kind": "json", "pointer": "/value", "value": i} for i in range(16)]
        self.contract = {"payload": {"scientific_delivery": [
            {"original_sha256": ref["sha256"], "disposition": "project", "projection": {
                "kind": "json_locators", "context": "Exact fixture values.", "locators": [locator]}}
            for ref, locator in zip(self.refs, self.locators)]}}

    def test_initial_private_and_terminal_reads_do_not_fill_the_supplied_evaluation(self):
        terminal = self.refs[0]
        self.refs = [self.artifacts.put(b"\xff" + self.artifacts.read(ref), "application/octet-stream")
                     for ref in self.refs]
        records = {"readiness_review": {str(i): {"artifact": ref} for i, ref in enumerate(self.refs)},
                   "execution_observation": {"run": {"terminal": terminal}}}
        supplied = Evaluation(records, self.artifacts)
        with capture_evaluations() as evaluations:
            projector = scientific_delivery.ScientificDelivery(records, supplied, self.contract, {})
        self.assertEqual(projector.private_bytes, tuple(self.artifacts.read(ref) for ref in self.refs))
        self.assertEqual(supplied.retention_report()["optional_bytes"], 0)
        assert_no_retention(self, evaluations)

    def test_unicode_private_patterns_preserve_nonretaining_evaluation(self):
        records = {"readiness_review": {str(i): {"artifact": ref} for i, ref in enumerate(self.refs)},
                   "execution_observation": {"run": {"terminal": self.refs[0]}}}
        supplied = Evaluation(records, self.artifacts)
        with capture_evaluations() as evaluations:
            projector = scientific_delivery.ScientificDelivery(records, supplied, self.contract, {})
            private_text = self.artifacts.read(self.refs[0]).decode()
            for encoding in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
                with self.subTest(encoding=encoding):
                    with self.assertRaises(ResearchError) as error:
                        projector._check_bytes(private_text.encode(encoding))
                    self.assertEqual(error.exception.code, "private_mixed_artifact_required")
        self.assertEqual(supplied.retention_report()["optional_bytes"], 0)
        assert_no_retention(self, evaluations)

    def test_projection_retains_nothing_across_sources(self):
        def project():
            projector = scientific_delivery.ScientificDelivery({}, self.artifacts, self.contract, {})
            results = [projector._project(ref, declaration["projection"])
                       for ref, declaration in zip(self.refs, self.contract["payload"]["scientific_delivery"])]
            return results, projector.derived

        with capture_evaluations(force_budget=64 * 1024 * 1024):
            before = project()
        with capture_evaluations() as evaluations:
            after = project()
        self.assertEqual(after, before)
        assert_shared_retains_nothing(self, evaluations)

    def test_mapping_and_walk_locator_reads_retain_no_optional_values(self):
        manifest = {"evidence": [{"artifact": ref, "locator": locator} for ref, locator in zip(self.refs, self.locators)]}
        with capture_evaluations(force_budget=64 * 1024 * 1024):
            before = scientific_delivery.project_delivery({}, self.artifacts, self.contract, manifest)
        supplied = Evaluation({}, self.artifacts)
        with capture_evaluations() as evaluations:
            after = scientific_delivery.project_delivery({}, supplied, self.contract, manifest)
        self.assertEqual(after, before)
        self.assertEqual([item["locator"]["value"] for item in after[0]["evidence"]], list(range(16)))
        self.assertEqual(supplied.retention_report()["optional_bytes"], 0)
        assert_shared_retains_nothing(self, evaluations)


class ScientificPublicRetentionTests(LiteratureCase):
    def test_public_provenance_and_original_component_reads_retain_no_optional_values(self):
        import test_research_public_source_delivery as fixtures
        capture, ref, projection = fixtures.PublicSourceDeliveryTests.capture_pdf(self)
        records = self.store.snapshot()["records"]
        contract = {"payload": {"scientific_delivery": [{"original_sha256": ref["sha256"],
            "disposition": "project", "projection": projection}]}}
        manifest = {"figure": ref, "original": capture["original"]}
        with capture_evaluations(force_budget=64 * 1024 * 1024):
            before = scientific_delivery.project_delivery(records, self.artifacts, contract, manifest)
        supplied = Evaluation(records, self.artifacts)
        with capture_evaluations() as evaluations:
            after = scientific_delivery.project_delivery(records, supplied, contract, manifest)
        self.assertEqual(after, before)
        self.assertEqual(supplied.retention_report()["optional_bytes"], 0)
        assert_no_retention(self, evaluations)


class BoundedStream:
    def __init__(self, source):
        self.source = source

    def read(self, size=-1):
        if not 0 < size <= 1024 * 1024:
            raise AssertionError("binary read must use bounded chunks")
        return self.source.read(size)

    def __getattr__(self, name):
        return getattr(self.source, name)


class StreamedExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.artifacts = ArtifactStore(self.root)
        self.refs = [self.artifacts.put(bytes([i]) * (128 * 1024), "application/octet-stream") for i in range(24)]
        self.records = {"configuration": {"research": {"profile": "research"}},
                        "local_artifact": {str(i): ref for i, ref in enumerate(self.refs)}}
        self.store = SimpleNamespace(root=self.root, revision=7,
                                     snapshot=lambda: {"records": self.records, "revision": 7})
        self.attack = self.root / "attack"
        self.attack.mkdir()
        self.report = {"ready": True, "digest": "a" * 64, "obligations": []}
        self.manifest = {"artifacts": self.refs, "text": "β"}

    def export_native(self):
        with mock.patch.object(native_export, "synthesis_state", return_value=self.report):
            return native_export.export_native(self.store, self.attack, self.attack / "delivery")

    def test_native_many_binary_copies_bypass_whole_object_read(self):
        with mock.patch.object(ArtifactStore, "read", side_effect=AssertionError("whole-object payload read")):
            result = self.export_native()
        self.assertEqual(len(result["inputs"]), 25)
        for ref in self.refs:
            self.assertEqual(hashlib.sha256((self.attack / "delivery/artifacts" / ref["sha256"]).read_bytes()).hexdigest(),
                             ref["sha256"])

    def test_review_many_binary_copies_bypass_whole_object_read(self):
        with mock.patch.object(ArtifactStore, "read", side_effect=AssertionError("whole-object payload read")):
            result = review_delivery.deliver_packet(self.store, self.root / "delivery", self.manifest)
        self.assertEqual(len(result["artifacts"]), 24)
        for ref in self.refs:
            self.assertEqual(hashlib.sha256((self.root / "delivery" / ref["path"]).read_bytes()).hexdigest(), ref["sha256"])

    def test_binary_stream_reads_remain_bounded_in_both_exporters(self):
        original = ArtifactStore.open_verified

        @contextmanager
        def open_bounded(artifacts, ref):
            with original(artifacts, ref) as frozen:
                yield replace(frozen, stream=BoundedStream(frozen.stream))

        with mock.patch.object(ArtifactStore, "open_verified", open_bounded), \
                mock.patch.object(ArtifactStore, "read", side_effect=AssertionError("whole-object payload read")):
            self.export_native()
            review_delivery.deliver_packet(self.store, self.root / "delivery", self.manifest, transitive=True)

    def test_native_parent_segments_cannot_escape_the_attack_root(self):
        destination = self.attack / ".." / "escaped"
        with mock.patch.object(native_export, "synthesis_state", return_value=self.report):
            with self.assertRaises(ResearchError) as raised:
                native_export.export_native(self.store, self.attack, destination)
        self.assertEqual(raised.exception.code, "unsafe_path")
        self.assertFalse((self.root / "escaped").exists())

    def test_native_intermediate_symlinks_cannot_escape_the_attack_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.attack / "link").symlink_to(outside, target_is_directory=True)
        destination = self.attack / "link" / "nested" / "delivery"
        with mock.patch.object(native_export, "synthesis_state", return_value=self.report):
            with self.assertRaises(ResearchError) as raised:
                native_export.export_native(self.store, self.attack, destination)
        self.assertEqual(raised.exception.code, "unsafe_path")
        self.assertFalse((outside / "nested/delivery").exists())

    def test_review_selected_parent_alias_keeps_existing_destination_semantics(self):
        selected = self.root / "selected"
        selected.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(selected, target_is_directory=True)
        destination = alias / "delivery"
        result = review_delivery.deliver_packet(self.store, destination, self.manifest)
        self.assertEqual(result["destination"], str(destination))
        self.assertTrue((selected / "delivery/inputs.json").is_file())

    def test_native_snapshot_uses_incremental_canonical_encoding(self):
        original = json.JSONEncoder.encode

        def encode(encoder, value):
            if isinstance(value, dict) and "records" in value and "preparation" in value:
                raise AssertionError("whole snapshot encoding")
            return original(encoder, value)

        with mock.patch.object(json.JSONEncoder, "encode", encode):
            self.export_native()

    def test_native_destination_stays_private_until_manifest_is_finished(self):
        original = native_export.write_projection

        def observe_write(root, relative, data):
            self.assertFalse((self.attack / "delivery").exists(), "incomplete native destination exposed")
            return original(root, relative, data)

        with mock.patch.object(native_export, "write_projection", side_effect=observe_write):
            self.export_native()
        self.assertTrue((self.attack / "delivery/manifest.json").is_file())

    def test_review_destination_stays_private_until_manifest_is_finished(self):
        original = review_delivery.write_projection

        def observe_write(root, relative, data):
            self.assertFalse((self.root / "delivery").exists(), "incomplete reviewer destination exposed")
            return original(root, relative, data)

        with mock.patch.object(review_delivery, "write_projection", side_effect=observe_write):
            review_delivery.deliver_packet(self.store, self.root / "delivery", self.manifest)
        self.assertTrue((self.root / "delivery/inputs.json").is_file())

    def test_late_manifest_failure_exposes_no_partial_delivery(self):
        for kind in ("review", "native"):
            with self.subTest(kind=kind):
                module = review_delivery if kind == "review" else native_export
                destination = self.root / "delivery" if kind == "review" else self.attack / "delivery"
                original = module.write_projection

                def fail_manifest(root, relative, data):
                    if relative.endswith(("inputs.json", "manifest.json")):
                        raise ResearchError("artifact_corrupt", "Late verification failure")
                    return original(root, relative, data)

                with mock.patch.object(module, "write_projection", side_effect=fail_manifest), self.assertRaises(ResearchError):
                    if kind == "review":
                        review_delivery.deliver_packet(self.store, destination, self.manifest)
                    else:
                        self.export_native()
                self.assertFalse(destination.exists(), "failed export exposed successful partial files")
                self.assertFalse(list(destination.parent.glob(".delivery-*")))

    def test_late_stream_failure_exposes_no_completed_destination(self):
        original = ArtifactStore.open_verified
        for kind in ("review", "native"):
            seen = []
            destination = self.root / "delivery" if kind == "review" else self.attack / "delivery"

            @contextmanager
            def fail_late(artifacts, ref):
                seen.append(ref)
                source = artifacts.root / ref["path"]
                saved = None
                if len(seen) == 3:
                    saved = source.read_bytes()
                    source.chmod(0o600)
                    source.write_bytes(b"corrupt" + saved[7:])
                try:
                    with original(artifacts, ref) as frozen:
                        yield frozen
                finally:
                    if saved is not None:
                        source.write_bytes(saved)

            with self.subTest(kind=kind), mock.patch.object(ArtifactStore, "open_verified", fail_late):
                with self.assertRaises(ResearchError) as raised:
                    if kind == "review":
                        review_delivery.deliver_packet(self.store, destination, self.manifest)
                    else:
                        self.export_native()
                self.assertEqual(raised.exception.code, "artifact_corrupt")
                self.assertEqual(len(seen), 3)
                self.assertFalse(destination.exists())
                self.assertFalse(list(destination.parent.glob(".delivery-*")))

    def test_transitive_review_preserves_order_and_sniffed_json_closure(self):
        leaf = self.artifacts.put(b"leaf", "text/plain")
        nested = self.artifacts.put(json.dumps({"child": leaf}).encode(), "text/plain")
        manifest = {"root": nested}
        destination = self.root / "delivery"
        result = review_delivery.deliver_packet(self.store, destination, manifest, transitive=True)
        self.assertEqual(result["artifacts"], [nested, leaf])
        self.assertEqual((destination / nested["path"]).read_bytes(), self.artifacts.read(nested))
        self.assertEqual((destination / leaf["path"]).read_bytes(), b"leaf")
        self.assertEqual((destination / "inputs.json").read_bytes(), scientific_delivery.encode_delivery(manifest))

    def test_export_members_match_unchanged_bytes_and_reference_order(self):
        native = self.export_native()
        review = review_delivery.deliver_packet(self.store, self.root / "delivery", self.manifest)
        actual = []
        for label, root in (("native", self.attack / "delivery"), ("review", self.root / "delivery")):
            for filename in sorted(item for item in root.rglob("*") if item.is_file()):
                data = filename.read_bytes()
                actual.append((label + "/" + filename.relative_to(root).as_posix(), hashlib.sha256(data).hexdigest(), len(data)))
        self.assertEqual(actual, BASELINE_EXPORTS)
        self.assertEqual(native["inputs"][1:], [{"path": "delivery/artifacts/" + ref["sha256"], "kind": "artifact", "digest": ref["sha256"]}
                                              for ref in review_delivery.references({"records": self.records})])
        self.assertEqual(review["artifacts"], list(reversed(review_delivery.references(self.manifest))))


class ScientificDeclaredSizeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))
        self.projector = scientific_delivery.ScientificDelivery({}, self.artifacts,
            {"payload": {"scientific_delivery": []}}, {})

    def test_oversized_declared_source_is_refused_before_reading(self):
        ref = self.artifacts.put(b'{"value":1}', "application/json")
        ref = dict(ref, size=scientific_delivery._MAX_BYTES + 1)
        with mock.patch.object(self.artifacts, "read", side_effect=AssertionError("oversized source read")):
            with self.assertRaises(ResearchError) as raised:
                self.projector._project(ref, {"kind": "json_locators", "locators": [], "context": "fixture"})
        self.assertEqual(raised.exception.code, "invalid_scientific_delivery")

    def test_oversized_referenced_original_is_refused_before_provenance_or_payload_read(self):
        ref = self.artifacts.put(b"jpeg", "image/jpeg")
        original = self.artifacts.put(b"%PDF-fixture", "application/pdf")
        original = dict(original, size=scientific_delivery._MAX_BYTES + 1)
        self.projector.public[original["sha256"]] = [(original, "source", None, {"original": original})]
        projection = {"kind": "source_component", "source_sha256": original["sha256"], "start": 0, "end": 4,
                      "context": "fixture"}
        with mock.patch.object(self.projector, "_check_public_reference", side_effect=AssertionError("oversized original inspected")):
            with self.assertRaises(ResearchError) as raised:
                self.projector._project(ref, projection)
        self.assertEqual(raised.exception.code, "invalid_scientific_delivery")

    def test_oversized_declared_source_is_refused_before_mapping_reads(self):
        ref = self.artifacts.put(b'{"value":1}', "application/json")
        ref = dict(ref, size=scientific_delivery._MAX_BYTES + 1)
        self.projector.declarations[ref["sha256"]] = {"disposition": "project",
            "projection": {"kind": "json_locators", "locators": [], "context": "fixture"}}
        with mock.patch.object(self.artifacts, "read", side_effect=AssertionError("oversized mapping source read")):
            with self.assertRaises(ResearchError) as raised:
                self.projector.map_reference(ref)
        self.assertEqual(raised.exception.code, "invalid_scientific_delivery")

    def test_oversized_provenance_original_is_refused_before_captured_source_reads(self):
        ref = self.artifacts.put(b"source text", "text/plain")
        original = self.artifacts.put(b"original", "application/pdf")
        original = dict(original, size=scientific_delivery._MAX_BYTES + 1)
        self.projector.records["source"] = {"source": {"id": "source", "response": original,
            "status": "captured", "response_complete": True}}
        self.projector.public[ref["sha256"]] = [(ref, "source", None, None)]
        with mock.patch.object(self.artifacts, "read", side_effect=AssertionError("oversized provenance original read")):
            with self.assertRaises(ResearchError) as raised:
                self.projector._check_public_reference(ref)
        self.assertEqual(raised.exception.code, "invalid_scientific_delivery")


# Captured from the unchanged implementation at 1227566.
BASELINE_EXPORTS = [('native/artifacts/1c18829c0c3a2c957b96fa74974c0255cde14cfd92d9bf29bcb9766382391a32',
  '1c18829c0c3a2c957b96fa74974c0255cde14cfd92d9bf29bcb9766382391a32',
  131072),
 ('native/artifacts/23e126f09a5b7b481ed7732e865e0ef57d5c62053599530fbd7d9b413f863185',
  '23e126f09a5b7b481ed7732e865e0ef57d5c62053599530fbd7d9b413f863185',
  131072),
 ('native/artifacts/3608c5b2af206b935b49350c907870aedfc05ae0746fd710ef515a6bfa163464',
  '3608c5b2af206b935b49350c907870aedfc05ae0746fd710ef515a6bfa163464',
  131072),
 ('native/artifacts/3b7bd4aa6265261ef22b922443f7c0ca4c3d880690a7411c3e4839dd1841e786',
  '3b7bd4aa6265261ef22b922443f7c0ca4c3d880690a7411c3e4839dd1841e786',
  131072),
 ('native/artifacts/3fe0aa5079e456a37e67ba63dea2e40f8a9077ef42b944da864a0a05ec151543',
  '3fe0aa5079e456a37e67ba63dea2e40f8a9077ef42b944da864a0a05ec151543',
  131072),
 ('native/artifacts/4017b7a27f5d49ed213ab864b83f7d1f706ecc1039001dadcffed8df6bccddd1',
  '4017b7a27f5d49ed213ab864b83f7d1f706ecc1039001dadcffed8df6bccddd1',
  131072),
 ('native/artifacts/51b1754edc87242e9dd3190af0d6bbc796d258fd14cc6bac980cded045b19824',
  '51b1754edc87242e9dd3190af0d6bbc796d258fd14cc6bac980cded045b19824',
  131072),
 ('native/artifacts/51caac4c4a2a04ba6ec6d56b20b017456aa31bf8232f06ec3d78297c920ea831',
  '51caac4c4a2a04ba6ec6d56b20b017456aa31bf8232f06ec3d78297c920ea831',
  131072),
 ('native/artifacts/5c78072c8b0471d147dfc9b2c1cdd996546a44331b3753ccfb594619e1a5eac4',
  '5c78072c8b0471d147dfc9b2c1cdd996546a44331b3753ccfb594619e1a5eac4',
  131072),
 ('native/artifacts/621e2bd7c5e79444a4beaa5782f70028877450be4dae38e69417030b0ed98364',
  '621e2bd7c5e79444a4beaa5782f70028877450be4dae38e69417030b0ed98364',
  131072),
 ('native/artifacts/77283b415caad926739dd46d233709e574955039eeafc2959d5affccd01eae07',
  '77283b415caad926739dd46d233709e574955039eeafc2959d5affccd01eae07',
  131072),
 ('native/artifacts/964e0165a36948b60ea2e603b1641612b64713783b789275dc51b4f260d5e0a1',
  '964e0165a36948b60ea2e603b1641612b64713783b789275dc51b4f260d5e0a1',
  131072),
 ('native/artifacts/a1e3d0bc7e98d62d646092f953f7dc486268ef2c66f78841c8c9af5f7550c349',
  'a1e3d0bc7e98d62d646092f953f7dc486268ef2c66f78841c8c9af5f7550c349',
  131072),
 ('native/artifacts/a4ae639acc0678b51339feba9816928c684f0251e26516a3879ac83188919789',
  'a4ae639acc0678b51339feba9816928c684f0251e26516a3879ac83188919789',
  131072),
 ('native/artifacts/a9d436eef49c42246cf6a2ceb0e8f065544b0f1e9a7a987cd0e4d4f863c98d01',
  'a9d436eef49c42246cf6a2ceb0e8f065544b0f1e9a7a987cd0e4d4f863c98d01',
  131072),
 ('native/artifacts/aaf6a8ab7f17b35353821ff0e7bf75abc68f963b2db36c3a1ddb27e382037c89',
  'aaf6a8ab7f17b35353821ff0e7bf75abc68f963b2db36c3a1ddb27e382037c89',
  131072),
 ('native/artifacts/add003c49a576b3154d188b91144bf32715286df35ad759c38bfb159dac3a71f',
  'add003c49a576b3154d188b91144bf32715286df35ad759c38bfb159dac3a71f',
  131072),
 ('native/artifacts/d693d44d8478475adfae30336c111ec5bc69f61ea934b46d66857c1b3ea81607',
  'd693d44d8478475adfae30336c111ec5bc69f61ea934b46d66857c1b3ea81607',
  131072),
 ('native/artifacts/eaf0544e066636822c6b952f6ba2047eec50ce378867985f78e451be4477693b',
  'eaf0544e066636822c6b952f6ba2047eec50ce378867985f78e451be4477693b',
  131072),
 ('native/artifacts/f0eef3b8eedfcecaf5545af3ef048784e91c3749a2ac3ce0126f259d0d44e636',
  'f0eef3b8eedfcecaf5545af3ef048784e91c3749a2ac3ce0126f259d0d44e636',
  131072),
 ('native/artifacts/f63776d7df004b1d43b39e00ef8143dcf8cde4bd97812bb456089b81412896f5',
  'f63776d7df004b1d43b39e00ef8143dcf8cde4bd97812bb456089b81412896f5',
  131072),
 ('native/artifacts/f9141cb2f32b9643bd96011f8084fbec4caafbddff4419194b09ce32808dc3e4',
  'f9141cb2f32b9643bd96011f8084fbec4caafbddff4419194b09ce32808dc3e4',
  131072),
 ('native/artifacts/fa43239bcee7b97ca62f007cc68487560a39e19f74f3dde7486db3f98df8e471',
  'fa43239bcee7b97ca62f007cc68487560a39e19f74f3dde7486db3f98df8e471',
  131072),
 ('native/artifacts/facea79731674ae6f9d1564041a9fa24bb69ab03f78508c54f30daf923b15438',
  'facea79731674ae6f9d1564041a9fa24bb69ab03f78508c54f30daf923b15438',
  131072),
 ('native/manifest.json', 'cdfc6d382886eb3bd385fc854022f7536a8b75b786956174c4451e1ab8ebf79d', 5020),
 ('native/snapshot.json', 'aab5a4b49fc4255024b734cad459a0b40add77c635459ecfbe9b2c02c6edfcad', 5922),
 ('review/inputs.json', '81f458d2894107c59c1b23a19e0af7e4079a3171948517fc39e1b873b88f3160', 6592),
 ('review/research/sources/objects/1c18829c0c3a2c957b96fa74974c0255cde14cfd92d9bf29bcb9766382391a32',
  '1c18829c0c3a2c957b96fa74974c0255cde14cfd92d9bf29bcb9766382391a32',
  131072),
 ('review/research/sources/objects/23e126f09a5b7b481ed7732e865e0ef57d5c62053599530fbd7d9b413f863185',
  '23e126f09a5b7b481ed7732e865e0ef57d5c62053599530fbd7d9b413f863185',
  131072),
 ('review/research/sources/objects/3608c5b2af206b935b49350c907870aedfc05ae0746fd710ef515a6bfa163464',
  '3608c5b2af206b935b49350c907870aedfc05ae0746fd710ef515a6bfa163464',
  131072),
 ('review/research/sources/objects/3b7bd4aa6265261ef22b922443f7c0ca4c3d880690a7411c3e4839dd1841e786',
  '3b7bd4aa6265261ef22b922443f7c0ca4c3d880690a7411c3e4839dd1841e786',
  131072),
 ('review/research/sources/objects/3fe0aa5079e456a37e67ba63dea2e40f8a9077ef42b944da864a0a05ec151543',
  '3fe0aa5079e456a37e67ba63dea2e40f8a9077ef42b944da864a0a05ec151543',
  131072),
 ('review/research/sources/objects/4017b7a27f5d49ed213ab864b83f7d1f706ecc1039001dadcffed8df6bccddd1',
  '4017b7a27f5d49ed213ab864b83f7d1f706ecc1039001dadcffed8df6bccddd1',
  131072),
 ('review/research/sources/objects/51b1754edc87242e9dd3190af0d6bbc796d258fd14cc6bac980cded045b19824',
  '51b1754edc87242e9dd3190af0d6bbc796d258fd14cc6bac980cded045b19824',
  131072),
 ('review/research/sources/objects/51caac4c4a2a04ba6ec6d56b20b017456aa31bf8232f06ec3d78297c920ea831',
  '51caac4c4a2a04ba6ec6d56b20b017456aa31bf8232f06ec3d78297c920ea831',
  131072),
 ('review/research/sources/objects/5c78072c8b0471d147dfc9b2c1cdd996546a44331b3753ccfb594619e1a5eac4',
  '5c78072c8b0471d147dfc9b2c1cdd996546a44331b3753ccfb594619e1a5eac4',
  131072),
 ('review/research/sources/objects/621e2bd7c5e79444a4beaa5782f70028877450be4dae38e69417030b0ed98364',
  '621e2bd7c5e79444a4beaa5782f70028877450be4dae38e69417030b0ed98364',
  131072),
 ('review/research/sources/objects/77283b415caad926739dd46d233709e574955039eeafc2959d5affccd01eae07',
  '77283b415caad926739dd46d233709e574955039eeafc2959d5affccd01eae07',
  131072),
 ('review/research/sources/objects/964e0165a36948b60ea2e603b1641612b64713783b789275dc51b4f260d5e0a1',
  '964e0165a36948b60ea2e603b1641612b64713783b789275dc51b4f260d5e0a1',
  131072),
 ('review/research/sources/objects/a1e3d0bc7e98d62d646092f953f7dc486268ef2c66f78841c8c9af5f7550c349',
  'a1e3d0bc7e98d62d646092f953f7dc486268ef2c66f78841c8c9af5f7550c349',
  131072),
 ('review/research/sources/objects/a4ae639acc0678b51339feba9816928c684f0251e26516a3879ac83188919789',
  'a4ae639acc0678b51339feba9816928c684f0251e26516a3879ac83188919789',
  131072),
 ('review/research/sources/objects/a9d436eef49c42246cf6a2ceb0e8f065544b0f1e9a7a987cd0e4d4f863c98d01',
  'a9d436eef49c42246cf6a2ceb0e8f065544b0f1e9a7a987cd0e4d4f863c98d01',
  131072),
 ('review/research/sources/objects/aaf6a8ab7f17b35353821ff0e7bf75abc68f963b2db36c3a1ddb27e382037c89',
  'aaf6a8ab7f17b35353821ff0e7bf75abc68f963b2db36c3a1ddb27e382037c89',
  131072),
 ('review/research/sources/objects/add003c49a576b3154d188b91144bf32715286df35ad759c38bfb159dac3a71f',
  'add003c49a576b3154d188b91144bf32715286df35ad759c38bfb159dac3a71f',
  131072),
 ('review/research/sources/objects/d693d44d8478475adfae30336c111ec5bc69f61ea934b46d66857c1b3ea81607',
  'd693d44d8478475adfae30336c111ec5bc69f61ea934b46d66857c1b3ea81607',
  131072),
 ('review/research/sources/objects/eaf0544e066636822c6b952f6ba2047eec50ce378867985f78e451be4477693b',
  'eaf0544e066636822c6b952f6ba2047eec50ce378867985f78e451be4477693b',
  131072),
 ('review/research/sources/objects/f0eef3b8eedfcecaf5545af3ef048784e91c3749a2ac3ce0126f259d0d44e636',
  'f0eef3b8eedfcecaf5545af3ef048784e91c3749a2ac3ce0126f259d0d44e636',
  131072),
 ('review/research/sources/objects/f63776d7df004b1d43b39e00ef8143dcf8cde4bd97812bb456089b81412896f5',
  'f63776d7df004b1d43b39e00ef8143dcf8cde4bd97812bb456089b81412896f5',
  131072),
 ('review/research/sources/objects/f9141cb2f32b9643bd96011f8084fbec4caafbddff4419194b09ce32808dc3e4',
  'f9141cb2f32b9643bd96011f8084fbec4caafbddff4419194b09ce32808dc3e4',
  131072),
 ('review/research/sources/objects/fa43239bcee7b97ca62f007cc68487560a39e19f74f3dde7486db3f98df8e471',
  'fa43239bcee7b97ca62f007cc68487560a39e19f74f3dde7486db3f98df8e471',
  131072),
 ('review/research/sources/objects/facea79731674ae6f9d1564041a9fa24bb69ab03f78508c54f30daf923b15438',
  'facea79731674ae6f9d1564041a9fa24bb69ab03f78508c54f30daf923b15438',
  131072)]
