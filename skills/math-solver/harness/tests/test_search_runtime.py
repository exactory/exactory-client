"""Writer provenance is additive metadata and never changes recorded replay results."""

import base64
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from search_controller.errors import SearchError
from search_controller.model import replay
from search_controller.storage import Store
from tests.search_fixtures import contract


class SearchRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def read_writer(self, store):
        sidecar_path = store.root / "writer-runtime.json"
        self.assertTrue(sidecar_path.is_file(), "Every committed root must record its writer in a sidecar")
        return json.loads(sidecar_path.read_text())

    def copy_runtime(self, name):
        plugin = Path(__file__).resolve().parents[4]
        copy_root = self.root / name
        for relative in ("bin/exactory-math", ".claude-plugin/plugin.json"):
            destination = copy_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(plugin / relative, destination)
        for relative in ("skills/math-solver/harness", "research_harness"):
            shutil.copytree(plugin / relative, copy_root / relative, ignore=shutil.ignore_patterns("__pycache__"))
        return copy_root

    def read_fresh_runtime(self, launcher):
        harness = Path(__file__).resolve().parents[1]
        source = ("import json, sys; sys.path.insert(0, sys.argv[1]); "
                  "from search_controller.runtime import read_runtime_identity; "
                  "print(json.dumps(read_runtime_identity(sys.argv[2])))")
        process = subprocess.run([sys.executable, "-c", source, str(harness), str(launcher)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_initialization_records_version_and_content_identity_independent_of_install_path(self):
        store = Store(self.root / "store")
        store.initialize(contract(), "objective-000001")
        runtime = self.read_writer(store)
        from search_controller.runtime import read_runtime_identity
        plugin = Path(__file__).resolve().parents[4]
        self.assertEqual(runtime, read_runtime_identity())
        self.assertEqual(runtime["plugin_version"], json.loads((plugin / ".claude-plugin/plugin.json").read_text())[
            "version"])
        self.assertRegex(runtime["controller_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(runtime["launcher"], str(plugin / "bin/exactory-math"))
        copy_root = self.copy_runtime("plugin-copy")
        launcher = copy_root / "bin/exactory-math"
        copied = read_runtime_identity(launcher)
        self.assertEqual(copied["controller_digest"], runtime["controller_digest"])
        self.assertNotEqual(copied["launcher"], runtime["launcher"])
        scheduler = copy_root / "skills/math-solver/harness/search_controller/scheduler.py"
        scheduler.write_bytes(scheduler.read_bytes() + b"\n# Local implementation change.\n")
        self.assertNotEqual(self.read_fresh_runtime(launcher)["controller_digest"], runtime["controller_digest"])
        copied = self.read_fresh_runtime(launcher)
        common = copy_root / "research_harness/openness_minimum.py"
        common.write_bytes(common.read_bytes() + b"\n# Shared implementation change.\n")
        self.assertNotEqual(self.read_fresh_runtime(launcher)["controller_digest"], copied["controller_digest"])

    def test_both_append_paths_refresh_writer_only_when_an_event_commits(self):
        for operation in (False, True):
            with self.subTest(operation=operation):
                store = Store(self.root / str(operation))
                store.initialize(contract(), "objective-000001")
                original = self.read_writer(store)
                other = dict(original, plugin_version="fixture-local", controller_digest="f" * 64)
                identity = {"command": "render", "target": None, "spec_digest": "a" * 64}

                def append_event(request, revision):
                    if operation:
                        return store.append_operation(identity, revision, request,
                            lambda document, content: dict(identity, operations=[], effects=[]))
                    return store.append("fixture", {}, revision, request)

                with patch("search_controller.storage.read_runtime_identity", return_value=other):
                    event = append_event("new", 0)
                self.assertEqual(self.read_writer(store), other)
                before = store.tree_path.read_bytes()
                writer_before = (store.root / "writer-runtime.json").read_bytes()
                self.assertEqual(append_event("new", 99), event)
                store.initialize(contract(), "objective-000001", store.read()["events"][0])
                store.read()
                with self.assertRaises(SearchError):
                    append_event("stale", 0)
                self.assertEqual(store.tree_path.read_bytes(), before)
                self.assertEqual((store.root / "writer-runtime.json").read_bytes(), writer_before)
                append_event("fresh", 1)
                self.assertEqual(self.read_writer(store), original)

    def test_historical_events_and_replay_results_stay_identical_with_writer_sidecar(self):
        fixture = Path(__file__).parent / "fixtures/historical-v1-completed-run.json"
        document = json.loads(base64.b64decode(json.loads(fixture.read_text())["tree_base64"]))
        store = Store(self.root / "store")
        store.root.mkdir()
        store.tree_path.write_text(json.dumps(document))
        original = replay(store.read())
        source = Store(self.root / "source")
        source.initialize(contract(), "objective-000001")
        runtime = self.read_writer(source)
        (store.root / "writer-runtime.json").write_text(json.dumps(runtime))
        self.assertEqual(replay(store.read()), original)
        (store.root / "writer-runtime.json").unlink()
        identity = {"command": "render", "target": None, "spec_digest": "a" * 64}
        before = store.read()["events"]
        store.append_operation(identity, len(before), "new-render",
                               lambda record, content: dict(identity, operations=[], effects=[]))
        self.assertEqual(self.read_writer(store), runtime)
        self.assertEqual(store.read()["events"][:-1], before)
        self.assertEqual(replay(store.read())["runs"], original["runs"])

    def test_failed_validation_does_not_replace_the_last_writer(self):
        store = Store(self.root / "store")
        store.initialize(contract(), "objective-000001")
        runtime = self.read_writer(store)
        before = store.tree_path.read_bytes()
        identity = {"command": "render", "target": None, "spec_digest": "a" * 64}

        def reject_event(document, event):
            raise SearchError("fixture_rejected", "Recorded command failed validation")

        with patch("search_controller.storage.read_runtime_identity", return_value=dict(runtime,
                   plugin_version="fixture-other")), self.assertRaises(SearchError):
            store.append_operation(identity, 0, "rejected",
                lambda document, content: dict(identity, operations=[], effects=[]), reject_event)
        self.assertEqual(store.tree_path.read_bytes(), before)
        self.assertEqual(self.read_writer(store), runtime)

    def test_sidecar_renames_follow_each_commit_under_the_writer_lock(self):
        store = Store(self.root / ".search")
        renamed_files = []
        replace_file = os.replace

        def record_rename(source, target):
            source_path, target_path = Path(source), Path(target)
            self.assertEqual(source_path.parent, target_path.parent)
            self.assertEqual(target_path.parent, store.root)
            descriptor = os.open(str(store.lock_path), os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
            if target_path.name == "writer-runtime.json":
                self.assertEqual(len(store.read()["events"]), len(renamed_files) // 2)
                self.assertEqual(set(json.loads(source_path.read_text())),
                                 {"plugin_version", "controller_digest", "launcher"})
            replace_file(source, target)
            renamed_files.append(target_path.name)

        with patch("search_controller.storage.os.replace", side_effect=record_rename):
            store.initialize(contract(), "objective-000001")
            store.append("fixture", {}, 0, "first")
            identity = {"command": "render", "target": None, "spec_digest": "a" * 64}
            store.append_operation(identity, 1, "second",
                                   lambda document, content: dict(identity, operations=[], effects=[]))
        self.assertEqual(renamed_files, ["tree.json", "writer-runtime.json"] * 3)

    def test_interrupted_sidecar_publication_keeps_the_previous_writer_after_commit(self):
        store = Store(self.root / ".search")
        store.initialize(contract(), "objective-000001")
        from search_controller.runtime import read_runtime_identity
        previous_runtime = dict(read_runtime_identity(), plugin_version="previous-runtime", controller_digest="f" * 64)
        sidecar_path = store.root / "writer-runtime.json"
        sidecar_path.write_text(json.dumps(previous_runtime))
        replace_file = store._atomic_replace
        identity = {"command": "render", "target": None, "spec_digest": "a" * 64}

        def interrupt_publication(target, content):
            if target == sidecar_path:
                raise OSError("interrupted after tree commit")
            replace_file(target, content)

        def append_event():
            return store.append_operation(identity, 0, "committed",
                                          lambda document, content: dict(identity, operations=[], effects=[]))

        with patch.object(store, "_atomic_replace", side_effect=interrupt_publication):
            with self.assertRaisesRegex(OSError, "interrupted after tree commit"):
                append_event()
        self.assertEqual(replay(store.read())["revision"], 1)
        self.assertEqual(self.read_writer(store), previous_runtime)
        self.assertEqual(append_event(), store.read()["events"][0])
        self.assertEqual(self.read_writer(store), previous_runtime)

    def test_initialization_and_two_events_remain_readable_by_the_0630_controller(self):
        from search_controller.service import Controller
        plugin = Path(__file__).resolve().parents[4]
        base_root = plugin / ".lane-logs/base-61b01e9"
        base_root.mkdir(parents=True, exist_ok=True)
        archived = subprocess.run(["git", "archive", "61b01e9"], cwd=plugin, capture_output=True, timeout=30)
        self.assertEqual(archived.returncode, 0, archived.stderr)
        extracted = subprocess.run(["tar", "-x", "-C", str(base_root)], input=archived.stdout,
                                   capture_output=True, timeout=30)
        self.assertEqual(extracted.returncode, 0, extracted.stderr)
        base_launcher = base_root / "bin/exactory-math"
        controller = Controller(self.root / "original")
        controller.command("init", {"contract": contract()}, 0, "init")
        controller.command("render", {}, 1, "first")
        controller.command("render", {}, 2, "second")
        copied_root = self.root / "older-reader-copy"
        shutil.copytree(controller.root, copied_root)
        command = [sys.executable, str(base_launcher), "--attack-root", str(copied_root),
                   "search", "status", "--json"]
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), controller.status())
        self.assertEqual(len(controller.store.read()["events"]), 3)
        self.assertEqual(set(controller.store.read()), {"schema_version", "objective_id", "contract", "events"})
        self.assertEqual(controller.store.tree_path.read_bytes(), (copied_root / ".search/tree.json").read_bytes())

    def test_runtime_identity_is_computed_once_per_process_and_cannot_be_mutated_by_callers(self):
        from search_controller import runtime
        copy_root = self.copy_runtime("cached-runtime")
        launcher = copy_root / "bin/exactory-math"
        with patch.object(runtime.hashlib, "sha256", wraps=runtime.hashlib.sha256) as build_digest:
            first_runtime = runtime.read_runtime_identity(launcher)
            expected_runtime = dict(first_runtime)
            first_runtime["plugin_version"] = "caller-modification"
            second_runtime = runtime.read_runtime_identity(launcher)
        self.assertEqual(build_digest.call_count, 1, "A running process must fingerprint its runtime only once")
        self.assertEqual(second_runtime, expected_runtime)


if __name__ == "__main__":
    unittest.main()
