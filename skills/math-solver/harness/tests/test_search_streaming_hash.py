"""Identity hashing reads real dependency files in bounded chunks."""

import contextlib
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from search_controller import evidence, execution
from search_controller.errors import SearchError
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke
from tests.support import WorkspaceTest


class StreamingHashTests(WorkspaceTest):
    @contextlib.contextmanager
    def bounded_reads(self, target):
        original = Path.open
        reads = []
        case = self

        class Reader:
            def __enter__(self):
                self.stream = original(target, "rb")
                return self

            def read(self, size=-1):
                case.assertGreater(size, 0, "Identity hashing must not request the whole file")
                case.assertLessEqual(size, 1024 * 1024)
                reads.append(size)
                return self.stream.read(size)

            def __exit__(self, *args):
                self.stream.close()

        def opened(path, *args, **kwargs):
            if path == target and (args == ("rb",) or kwargs.get("mode") == "rb"):
                return Reader()
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", opened):
            yield
        self.assertTrue(reads, "The real dependency must actually be read")

    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root, computational=True)
        self.external = self.attack_root / "external.bin"
        self.external.write_bytes(b"abc")
        self.identity = {"path": str(self.external), "digest": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}
        step = self.workspace / "deterministic/job"
        step.mkdir()
        (step / "job.py").write_text("print('bounded fixture')\n")

    def test_external_freeze_streams_and_rejects_changed_bytes(self):
        spec = command_spec(self.workspace, ["/bin/sh", "job.py"])
        spec["external_dependencies"] = [self.identity]
        node = self.controller.status()["nodes"]["node-000001"]
        with self.bounded_reads(self.external):
            frozen = evidence.freeze_execution_inputs(self.attack_root, node, spec, self.controller.store)
        self.assertEqual(self.controller.store.get_blob(frozen)["external_dependencies"], [self.identity])
        self.external.write_bytes(b"changed")
        with self.assertRaises(SearchError) as caught:
            evidence.freeze_execution_inputs(self.attack_root, node, spec, self.controller.store)
        self.assertEqual(caught.exception.code, "digest_mismatch")

    def test_manifest_audit_streams_and_rejects_changed_bytes(self):
        proof = self.controller.store.put_artifact(b"A proof fixture.")
        manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": "a" * 64,
                    "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
                    "artifacts": [{"path": "proof.md", "digest": proof, "role": "proof"}],
                    "dependencies": [], "external_dependencies": [self.identity], "verification": None}
        frozen = self.controller.store.put_blob(manifest)
        with self.bounded_reads(self.external):
            evidence.audit_manifest(self.attack_root, self.controller.status(), frozen, self.controller.store)
        self.external.write_bytes(b"changed")
        with self.assertRaises(SearchError) as caught:
            evidence.audit_manifest(self.attack_root, self.controller.status(), frozen, self.controller.store)
        self.assertEqual(caught.exception.code, "digest_mismatch")

    def test_command_executable_identity_streams_real_bytes(self):
        executable = self.attack_root.parent / "fixture-command"
        executable.write_bytes(b"#!/bin/sh\nprintf fixture\\n\n")
        executable.chmod(0o755)
        expected = hashlib.sha256(executable.read_bytes()).hexdigest()
        invoke(self.controller, "begin", begin_spec())
        with self.bounded_reads(executable):
            reservation = execution.build_run(self.controller, self.controller.status(),
                command_spec(self.workspace, [str(executable)]), "node-000001", self.controller.store)
        self.assertIn({"path": str(executable), "digest": expected}, reservation["executable_bindings"])

    def test_native_executable_identity_streams_real_bytes(self):
        step = self.workspace / "deterministic/job"
        (step / "certificate.txt").write_text("fixture")
        (step / "check.sh").write_text("#!/bin/sh\nexit 0\n")
        (step / "check.sh").chmod(0o755)
        executable = Path("/bin/sh").resolve()
        expected = hashlib.sha256(executable.read_bytes()).hexdigest()
        args = SimpleNamespace(verify_command="certificate", step_dir="job", attack_root=self.attack_root, slug=self.slug)
        with self.bounded_reads(executable):
            spec = execution.native_spec(self.controller, self.controller.status()["nodes"]["node-000001"], args)
        self.assertIn({"path": str(executable), "digest": expected}, spec["external_dependencies"])
