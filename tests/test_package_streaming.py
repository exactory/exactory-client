"""Streaming package construction preserves historical members and file boundaries."""

from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import weakref
from unittest import mock
import zipfile

from research_harness import deposit_package
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.workspace import open_file
from test_streamed_exports import BoundedStream


class PackageStreamingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.artifacts = ArtifactStore(self.root)
        self.claims = self.artifacts.put(b'[{"id":"claim","claim":"Exact observation: \\u03b2."}]', "application/json")
        self.binary = self.artifacts.put(b"\x00\xffrecorded output\n", "application/octet-stream")
        self.invalid = self.artifacts.put(b'{"historical":1,"historical":2}', "application/json")
        self.packet = self.artifacts.put(json.dumps({"binary": self.binary, "invalid": self.invalid}).encode(),
                                         "application/json; charset=utf-8")
        program = self.artifacts.put(b"print('frozen')\n", "text/x-python")
        command = {"program": program, "inputs": [self.packet], "argv": ["python3", "code/main.py"]}
        admission = {"id": "run/a", "digest": "a" * 64, "command": command}
        binding = {"admission_id": admission["id"], "admission_digest": admission["digest"],
                   "script": "code/main.py", "runtime": {"path": "python3"},
                   "inputs": [{"path": "data/input.json", "artifact": self.packet}], "outputs": []}
        execution = {"id": "execution", "cycle_id": "cycle", "origin": {"kind": "managed", "admission_id": "run/a"},
                     "command": command, "outputs": [{"id": "result", "artifact": self.binary}]}
        links = [{"claim_id": "claim", "evidence": [{"kind": "result", "execution_id": "execution",
                                                     "output_id": "result", "artifact": self.binary}]}]
        self.bundle = {"id": "paper", "digest": "b" * 64, "files": {"claims": {"artifact": self.claims}},
                       "claim_evidence": links}
        self.records = {"publication_bundle": {"paper": self.bundle},
                        "execution": {"execution": {"payload": execution}},
                        "execution_admission": {"run/a": admission}, "execution_binding": {"run/a": binding},
                        "checkpoint": {"history:a/b": {"artifact": self.packet, "text": "β"}}}
        self.store = SimpleNamespace(root=self.root, snapshot=lambda: {"records": self.records, "revision": 7})
        (self.root / "extras").mkdir()
        (self.root / "extras/a.txt").write_bytes(b"first supplement\n")
        (self.root / "extras/z.txt").write_bytes(b"last supplement\n")

    def build(self, name="package.zip", **options):
        destination = self.root / name
        deposit_package.build_package(self.store, destination, bundle="paper", **options)
        return destination

    def collect_members(self):
        with zipfile.ZipFile(self.build(includes=["extras"])) as archive:
            return [(info.filename, hashlib.sha256(archive.read(info)).hexdigest(), info.file_size,
                     info.date_time, info.compress_type, info.create_system, info.external_attr)
                    for info in archive.infolist()]

    def test_members_match_unchanged_package_including_order_and_metadata(self):
        self.assertEqual(self.collect_members(), BASELINE_MEMBERS)

    def test_many_binary_artifacts_never_use_whole_object_reads(self):
        self.records["local_artifact"] = {str(i): self.artifacts.put(bytes([i]) * (128 * 1024), "application/octet-stream")
                                          for i in range(24)}
        with mock.patch.object(Evaluation, "__init__", side_effect=AssertionError("package must bypass evaluation retention")), \
                mock.patch.object(ArtifactStore, "read", side_effect=AssertionError("whole-object payload read")):
            destination = self.build()
        with zipfile.ZipFile(destination) as archive:
            for ref in self.records["local_artifact"].values():
                self.assertEqual(hashlib.sha256(archive.read("data/objects/" + ref["sha256"])).hexdigest(), ref["sha256"])

    def test_pending_queue_contains_only_references(self):
        with zipfile.ZipFile(io.BytesIO(), "w") as zipped:
            archive = deposit_package._Archive(zipped, self.artifacts)
            archive.write_artifact(self.binary)
            self.assertEqual(archive.pending, [self.binary])

    def test_supplements_are_enumerated_only_as_the_preceding_file_is_written(self):
        original = deposit_package._iter_supplement_files
        first_written = False
        original_open = zipfile.ZipFile.open

        def observe_open(archive, name, mode="r", *args, **kwargs):
            nonlocal first_written
            if mode == "w" and getattr(name, "filename", name) == "extras/a.txt":
                first_written = True
            return original_open(archive, name, mode, *args, **kwargs)

        def enumerate_files(root, relative, **options):
            for index, item in enumerate(original(root, relative, **options)):
                if index:
                    self.assertTrue(first_written, "next supplement enumerated before the previous member was written")
                yield item

        with mock.patch.object(deposit_package, "_iter_supplement_files", enumerate_files), \
                mock.patch.object(zipfile.ZipFile, "open", observe_open):
            self.build(includes=["extras"])

    def test_supplement_payloads_use_checked_bounded_streams(self):
        contents = {"extras/a.txt": b"a" * (2 * 1024 * 1024 + 7),
                    "extras/z.txt": b"z" * (2 * 1024 * 1024 + 13)}
        for relative, data in contents.items():
            (self.root / relative).write_bytes(data)

        @contextmanager
        def open_bounded(root, relative):
            with open_file(root, relative) as source:
                yield BoundedStream(source)

        with mock.patch("research_harness.workspace.read_file", side_effect=AssertionError("whole supplement read")), \
                mock.patch.object(deposit_package, "read_file", side_effect=AssertionError("whole supplement read"), create=True), \
                mock.patch.object(deposit_package, "open_file", open_bounded, create=True):
            destination = self.build(includes=["extras"])
        with zipfile.ZipFile(destination) as archive:
            for relative, data in contents.items():
                self.assertEqual(archive.read(relative), data)

    def test_generated_json_never_uses_whole_value_encoding(self):
        original = json.JSONEncoder.encode

        def encode(encoder, value):
            if encoder.indent == 2 and encoder.sort_keys:
                raise AssertionError("whole JSON encoding")
            return original(encoder, value)

        with mock.patch.object(json.JSONEncoder, "encode", encode):
            self.build(includes=["extras"])

    def test_archive_inside_supplement_directory_does_not_include_its_private_staging_file(self):
        aliases = ["extras"]
        if (self.root / "EXTRAS").is_dir():
            aliases.append("EXTRAS")
        for relative in aliases:
            with self.subTest(relative=relative):
                destination = self.build("extras/archive.zip", includes=[relative])
                with zipfile.ZipFile(destination) as archive:
                    supplements = json.loads(archive.read("package.json"))["supplements"]
                    self.assertEqual([item["path"] for item in supplements],
                                     [relative + "/a.txt", relative + "/z.txt"])
                destination.unlink()

    def test_referenced_json_is_released_before_the_next_parse(self):
        self.records["local_artifact"] = {str(i): self.artifacts.put(
            json.dumps({"value": i, "padding": "x" * 16000}).encode(), "application/json") for i in range(24)}
        original = deposit_package.strict_json
        previous = None

        class ParsedDict(dict):
            pass

        class ParsedList(list):
            pass

        def parse(data):
            nonlocal previous
            self.assertTrue(previous is None or previous() is None, "previous JSON object retained during the next parse")
            value = original(data)
            value = ParsedDict(value) if isinstance(value, dict) else ParsedList(value)
            previous = weakref.ref(value)
            return value

        with mock.patch.object(deposit_package, "strict_json", parse):
            self.build()
        self.assertIsNone(previous())

    def test_late_corruption_leaves_no_archive_or_temporary(self):
        source = self.root / self.binary["path"]
        source.chmod(0o600)
        source.write_bytes(b"x" * self.binary["size"])
        with self.assertRaises(ResearchError) as raised:
            self.build()
        self.assertEqual(raised.exception.code, "artifact_corrupt")
        self.assertFalse((self.root / "package.zip").exists())
        self.assertFalse(list(self.root.glob(".package-*")))


# Captured from the unchanged implementation at 1227566.
BASELINE_MEMBERS = [('README.txt',
  'ba1f3f62eb4bca7f801e7d13e04613bd1c720bcbb125f5c1c8bbc7a73df77b01',
  85,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('data/objects/ad2856f76aa2c8329d7d3f489aa06650b5cce00abbb33701a44c10217dca1d52',
  'ad2856f76aa2c8329d7d3f489aa06650b5cce00abbb33701a44c10217dca1d52',
  53,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('claims.json',
  'ad2856f76aa2c8329d7d3f489aa06650b5cce00abbb33701a44c10217dca1d52',
  53,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('claim-evidence.json',
  '55aa99efc6659dc27223bdd8f974aa463b4e9892c5c4c261080bcaa790f938d5',
  475,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('data/objects/eff06195bc5fbfbdea413e68679a9ae727e6a81831a47fdb83349f7f0f906832',
  'eff06195bc5fbfbdea413e68679a9ae727e6a81831a47fdb83349f7f0f906832',
  16,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('runs/run-run%2Fa/code/main.py',
  'eff06195bc5fbfbdea413e68679a9ae727e6a81831a47fdb83349f7f0f906832',
  16,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('data/objects/72525381ffe17f31e260d130d4f3b97f4f9c193d9eed8822d8535e0f8adfdd63',
  '72525381ffe17f31e260d130d4f3b97f4f9c193d9eed8822d8535e0f8adfdd63',
  483,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('runs/run-run%2Fa/data/input.json',
  '72525381ffe17f31e260d130d4f3b97f4f9c193d9eed8822d8535e0f8adfdd63',
  483,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('runs/run-run%2Fa/logs/',
  'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
  0,
  (1980, 1, 1, 0, 0, 0),
  0,
  3,
  1106051088),
 ('runs/run-run%2Fa/results/',
  'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
  0,
  (1980, 1, 1, 0, 0, 0),
  0,
  3,
  1106051088),
 ('runs/run-run%2Fa/plots/',
  'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
  0,
  (1980, 1, 1, 0, 0, 0),
  0,
  3,
  1106051088),
 ('data/execution-execution/output-result.bin',
  '2d1088eaf1b893483bbdfe2c0774249aacff629af774f18866e48926d7868ae8',
  18,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('managed-record/execution_admission/run%2Fa.json',
  '3da67c48e24268e9f64e42ef2407fc9784725baf29447fb9ee3e1b3b6197787a',
  758,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('managed-record/execution_binding/run%2Fa.json',
  '5c022945ae475cdf7b9e3ca2a068a14d4106f27193232ad171d7be9f282d4eef',
  571,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('managed-record/execution/execution.json',
  '1041b7d9429825b0eae74ba0425c0a4d3b64cd7dda201e7cf25c4082969a333c',
  1214,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('managed-record/checkpoint/history%3Aa%2Fb.json',
  '1f5f3035d8c7cacb96ce83363337d69a43ab46dfe24dc74a876aecbaf24f9447',
  296,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('managed-record/publication_bundle/paper.json',
  'aa876a591bd64f0e419f40c4e1df40b93ed1395d06ebb953120da3683aab5193',
  954,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('data/objects/c5549b7984e6fbc9c19f6ba422761bad4e3b139baa1ddce602a5843c6ba5e2b6',
  'c5549b7984e6fbc9c19f6ba422761bad4e3b139baa1ddce602a5843c6ba5e2b6',
  31,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('extras/a.txt',
  'f840d3903f48df4278f1c10119475e7effa59d9ef8d6c68f9cf371785bacbb06',
  17,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('extras/z.txt',
  'be3919ea815ada02de0466bd7f4c16f462babcb31291d70da5689c954d4dcc0c',
  16,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('data/INDEX.json',
  '246cc354545a9c929eef59657ddb00d4ae4b72a0fe36be27fcb54543c4f88723',
  848,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768),
 ('package.json',
  '6877f56e31e179bce065d282f5af16178d9358563f4f96746a21ef1c00da96d6',
  2995,
  (1980, 1, 1, 0, 0, 0),
  8,
  3,
  2175008768)]
