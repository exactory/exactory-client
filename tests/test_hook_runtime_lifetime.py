"""Exercise installed managed commands, retained releases and persistent bindings."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EXIT_CODES = {"PreToolUse": 2, "PostToolUse": 1, "SessionStart": 1, "Stop": 0}
PROBE = '''import json, os, sys
from pathlib import Path
raw = sys.stdin.buffer.read()
with open(os.environ["RUNTIME_TEST_CALLS"], "a") as stream:
    stream.write(json.dumps({"release": Path(__file__).parents[1].joinpath("label").read_text(),
        "argv": sys.argv[1:], "stdin": raw.decode(), "cwd": os.getcwd()}) + "\\n")
sys.stderr.write(os.environ.get("RUNTIME_TEST_STDERR", ""))
sys.exit(int(os.environ.get("RUNTIME_TEST_EXIT", "0")))
'''


def make_writable(root):
    if not root.exists():
        return
    for directory, _, files in os.walk(root):
        os.chmod(directory, 0o755)
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                os.chmod(path, 0o644 | (path.stat().st_mode & 0o111))


def make_read_only(root):
    for directory, _, files in os.walk(root):
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                os.chmod(path, path.stat().st_mode & ~0o222)
        os.chmod(directory, Path(directory).stat().st_mode & ~0o222)


class HookRuntimeLifetimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="runtime owner's ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.prefix = self.root / "managed prefix's runtime"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.calls = self.root / "calls.jsonl"
        self.state = self.root / "user session state"
        (self.state / "sessions").mkdir(parents=True)
        (self.state / "runtime.lock").touch(mode=0o600)
        self.environment = {"EXACTORY_RUNTIME_STATE_DIR": str(self.state)}
        self.environment_patch = patch.dict(os.environ, self.environment)
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.addCleanup(make_writable, self.root)
        self.run_cli("install", "--prefix", str(self.prefix))
        self.config = json.loads((self.prefix / "v1/hook_runtime/registrations.json").read_text())
        self.policy = runpy.run_path(str(ROOT / "hook_runtime/policy.py"))
        self.config = self.policy["build_managed_config"](self.prefix, sys.executable)

    def run_cli(self, *args):
        launcher = ([sys.executable, str(ROOT / "bin/exactory-runtime")] if args[0] == "install"
                    else [str(self.prefix / "v1/exactory-runtime")])
        result = subprocess.run([*launcher, *args],
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def make_plugin(self, label, real=False):
        root = self.root / ("plugin cache's " + label)
        if real:
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".lane-scratch"))
        else:
            (root / "codex").mkdir(parents=True)
            (root / "codex/hook.py").write_text(PROBE)
            (root / "codex/session_start.py").write_text(PROBE)
        (root / "label").write_text(label)
        return root

    def publish(self, root):
        return self.run_cli("publish", "--prefix", str(self.prefix), str(root)).strip()

    def handlers(self, event=None):
        for name, groups in self.config["hooks"].items():
            if event is None or name == event:
                for group in groups:
                    for handler in group["hooks"]:
                        yield name, handler

    def invoke(self, event, handler=None, session="A", source="startup", raw=None, **env):
        if handler is None:
            handler = next(self.handlers(event))[1]
        payload = {"hook_event_name": event, "session_id": session, "source": source,
                   "cwd": str(self.workspace), "tool_name": "Bash",
                   "tool_input": {"command": "true"}}
        return subprocess.run(["/bin/sh", "-c", handler["command"]],
            input=raw if raw is not None else json.dumps(payload), text=True,
            capture_output=True, cwd=self.workspace, timeout=60,
            env={**os.environ, "RUNTIME_TEST_CALLS": str(self.calls), **env})

    def read_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def read_binding(self, session="A"):
        from hook_runtime.state import read_binding
        return read_binding(self.state, session)

    def test_bundled_authorship_and_autopilot_hooks_publish_readonly(self):
        from test_native_writers import assert_rejects_direct_writes

        source = self.make_plugin("real writers", real=True)
        self.publish(source)
        self.assertEqual(self.invoke("SessionStart").returncode, 0)
        shutil.rmtree(source)
        state = self.workspace / ".exactory"
        state.mkdir()
        (state / "draft.json").write_text("{}")
        authorship = next(handler for _, handler in self.handlers("PostToolUse")
                          if "record_paper_authorship.py" in handler["command"])
        payload = {"hook_event_name": "PostToolUse", "session_id": "A", "cwd": str(self.workspace),
                   "tool_name": "apply_patch", "tool_input": {"command":
                   "*** Begin Patch\n*** Add File: draft/paper.tex\n+synthetic\n*** End Patch"}}
        for _ in range(2):
            record = state / "authorship.json"
            record.unlink(missing_ok=True)
            record.write_text('{"written_by_exactory": false}')
            result = self.invoke("PostToolUse", authorship, raw=json.dumps(payload))
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(json.loads(record.read_text()), {"written_by_exactory": True})
            assert_rejects_direct_writes(self, record)
        study = {"slug": "synthetic", "stage": "experiment", "status": "running",
                 "autopilot": True, "waiting": None}
        (state / "study.json").write_text(json.dumps(study))
        counter = state / "autopilot_count"
        counter.write_text("1")
        stop = next(handler for _, handler in self.handlers("Stop")
                    if "continue_autopilot.py" in handler["command"])
        payload = {"hook_event_name": "Stop", "session_id": "A", "cwd": str(self.workspace),
                   "stop_hook_active": True}
        for expected in ("2", "-1", "-1"):
            result = self.invoke("Stop", stop, raw=json.dumps(payload),
                                 EXACTORY_AUTOPILOT_MAX="2", EXACTORY_ASSIGNED_SESSION="")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(counter.read_text(), expected)
            assert_rejects_direct_writes(self, counter)
        (state / "study.json").write_text(json.dumps(dict(study, stage="complete")))
        result = self.invoke("Stop", stop, raw=json.dumps(payload))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(counter.read_text(), "0")
        assert_rejects_direct_writes(self, counter)

    def test_every_loaded_command_keeps_a_after_cache_removal_and_new_session_gets_b(self):
        plugin_a = self.make_plugin("A")
        digest_a = self.publish(plugin_a)
        self.assertEqual(self.invoke("SessionStart").returncode, 0)
        digest_b = self.publish(self.make_plugin("B"))
        self.assertNotEqual(digest_a, digest_b)
        shutil.rmtree(plugin_a)
        before = len(self.read_calls())
        for event, handler in self.handlers():
            result = self.invoke(event, handler)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.read_calls()) - before, 11)
        self.assertEqual({row["release"] for row in self.read_calls()}, {"A"})
        for source in ("resume", "compact", "clear", "fork"):
            self.assertEqual(self.invoke("SessionStart", source=source).returncode, 0)
            self.assertEqual(self.read_binding()["release"], digest_a)
        self.assertEqual(self.invoke("SessionStart", session="B").returncode, 0)
        self.assertEqual(self.read_binding("B")["release"], digest_b)
        self.assertEqual(self.read_calls()[-1]["release"], "B")

    def test_real_handlers_guard_cli_documents_and_stop_survive_cache_deletion(self):
        plugin_a = self.make_plugin("A", real=True)
        digest = self.publish(plugin_a)
        self.assertEqual(self.invoke("SessionStart").returncode, 0)
        self.publish(self.make_plugin("B"))
        shutil.rmtree(plugin_a)
        attack = self.workspace / "attack/sample"
        attack.mkdir(parents=True)
        (attack / "problem.json").write_text('{"claim":"synthetic"}')
        (attack / "journal.jsonl").write_text("")
        for event, handler in self.handlers():
            result = self.invoke(event, handler)
            self.assertEqual(result.returncode, 0, result.stderr)
            if "session_start.py" in handler["command"]:
                self.assertIn(str(self.prefix / "releases" / digest), result.stdout)
        guard = next(h for _, h in self.handlers("PreToolUse")
                     if "guard_attack_files.py" in h["command"])
        for filename, denied in (("notes.md", False), ("attack/sample/tasks.json", True)):
            payload = {"hook_event_name": "PreToolUse", "session_id": "A",
                       "cwd": str(self.workspace), "tool_name": "Write",
                       "tool_input": {"file_path": filename}}
            result = self.invoke("PreToolUse", guard, raw=json.dumps(payload))
            self.assertEqual(result.returncode, 0, result.stderr)
            if denied:
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
            else:
                self.assertEqual(result.stdout, "")
        bundle = self.prefix / "releases" / digest
        for path in (bundle / "skills").glob("*/SKILL.md"):
            self.assertTrue(path.read_text())
        for name in ("codex/README.md", "RESEARCH_CONSTITUTION.md", "docs/research-workflow.md"):
            self.assertTrue((bundle / name).read_text())
        result = subprocess.run([sys.executable, str(bundle / "bin/exactory-research"), "--help"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        from test_math_search_hooks import managed_objective
        controller = managed_objective(self.workspace / "managed", host="codex", session="A")
        stop = next(h for _, h in self.handlers("Stop") if "continue_attack.py" in h["command"])
        payload = {"hook_event_name": "Stop", "session_id": "A", "cwd": str(self.workspace),
                   "delivery_id": "retained-delivery", "stop_hook_active": False}
        for _ in range(2):
            result = self.invoke("Stop", stop, raw=json.dumps(payload))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["decision"], "block")
        self.assertEqual(controller.status()["control"]["stop_count"], 1)
        self.assertFalse(self.calls.exists(), "B's probe must never execute for session A")

    def test_stdin_cwd_arguments_and_handler_errors_are_unchanged(self):
        self.publish(self.make_plugin("A"))
        self.invoke("SessionStart")
        for event, handler in self.handlers():
            raw = ' {"session_id":"A","hook_event_name":' + json.dumps(event) + ',"extra":"\\u2603"} \n'
            result = self.invoke(event, handler, raw=raw,
                                 RUNTIME_TEST_EXIT="17", RUNTIME_TEST_STDERR="handler error\n",
                                 PYTHONPATH=str(self.workspace), PYTHONSTARTUP="invalid")
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertEqual(result.stderr, "handler error\n")
            row = self.read_calls()[-1]
            self.assertEqual(row["stdin"], raw)
            self.assertEqual(row["cwd"], str(self.workspace))
            expected = [] if "session_start.py" in handler["command"] else [handler["command"].split()[-1]]
            self.assertEqual(row["argv"], expected)

    def test_invalid_bundles_and_bindings_refuse_every_event_without_execution(self):
        from hook_runtime.state import build_binding_path
        for damage in ("missing", "partial", "corrupt", "unapproved", "binding", "wrong-session", "path",
                       "manifest", "extra-file", "symlink"):
            with self.subTest(damage=damage):
                digest = self.publish(self.make_plugin(damage))
                registry_path = self.prefix / "state/registry.json"
                original_registry = registry_path.read_bytes()
                session = damage
                self.assertEqual(self.invoke("SessionStart", session=session).returncode, 0)
                before = self.calls.read_bytes()
                bundle = self.prefix / "releases" / digest
                make_writable(bundle)
                if damage == "missing":
                    shutil.rmtree(bundle)
                elif damage == "partial":
                    (bundle / "codex/session_start.py").unlink()
                elif damage == "corrupt":
                    (bundle / "codex/hook.py").write_text("raise AssertionError('executed')")
                elif damage == "unapproved":
                    path = self.prefix / "state/registry.json"
                    registry = json.loads(path.read_text())
                    registry["approved"].remove(digest)
                    registry["default"] = None
                    path.write_text(json.dumps(registry))
                elif damage == "manifest":
                    (bundle / ".exactory-release.json").unlink()
                elif damage == "extra-file":
                    (bundle / "unexpected").write_text("unapproved bytes")
                elif damage == "symlink":
                    held = bundle.with_name(".held-" + digest)
                    bundle.rename(held)
                    bundle.symlink_to(held, target_is_directory=True)
                else:
                    path = build_binding_path(self.state, session)
                    binding = json.loads(path.read_text())
                    if damage == "binding":
                        path.write_text("{")
                    elif damage == "wrong-session":
                        binding["session_id"] = "another-session"
                        path.write_text(json.dumps(binding))
                    else:
                        binding["release"] = str(bundle)
                        path.write_text(json.dumps(binding))
                for event, handler in self.handlers():
                    result = self.invoke(event, handler, session=session)
                    self.assertEqual(result.returncode, EXIT_CODES[event], result.stderr)
                    self.assertIn("[exactory-runtime]", result.stderr)
                    self.assertEqual(result.stdout, "")
                self.assertEqual(self.calls.read_bytes(), before)
                registry_path.write_bytes(original_registry)

    def test_unknown_resume_compact_clear_fork_and_tool_cannot_choose_default(self):
        self.publish(self.make_plugin("A"))
        for source in ("resume", "compact", "clear", "fork", "unknown"):
            self.assertEqual(self.invoke("SessionStart", source=source).returncode, 1)
        for event in ("PreToolUse", "PostToolUse", "Stop"):
            self.assertEqual(self.invoke(event).returncode, EXIT_CODES[event])
        self.assertFalse(self.calls.exists())

    def test_concurrent_publication_and_enrollment_leave_one_binding(self):
        digest_a = self.publish(self.make_plugin("A"))
        plugin_b = self.make_plugin("B")
        with ThreadPoolExecutor(max_workers=8) as pool:
            publication = pool.submit(self.publish, plugin_b)
            starts = [pool.submit(self.invoke, "SessionStart") for _ in range(12)]
            for start in starts:
                result = start.result()
                self.assertEqual(result.returncode, 0, result.stderr)
            digest_b = publication.result()
        self.assertIn(self.read_binding()["release"], (digest_a, digest_b))
        self.assertEqual(len(list((self.state / "sessions").glob("*.json"))), 1)
        self.assertEqual(len(self.read_calls()), 12)
        self.assertEqual(len({row["release"] for row in self.read_calls()}), 1)

    def test_interrupted_publication_keeps_previous_default_and_install_is_stable(self):
        from hook_runtime.bundles import publish_release
        digest = self.publish(self.make_plugin("A"))
        dispatcher = (self.prefix / "v1/dispatch.py").read_bytes()
        real_replace = os.replace

        def interrupt_registry(source, destination):
            if Path(destination).name == "registry.json":
                raise OSError("synthetic interrupted publication")
            return real_replace(source, destination)

        with patch("os.replace", side_effect=interrupt_registry):
            with self.assertRaises(OSError):
                publish_release(self.prefix, self.make_plugin("B"))
        self.assertEqual(json.loads(self.run_cli("status", "--prefix", str(self.prefix)))["default"], digest)
        self.assertEqual(self.invoke("SessionStart").returncode, 0)
        self.assertEqual(self.read_calls()[-1]["release"], "A")
        self.run_cli("install", "--prefix", str(self.prefix))
        self.assertEqual((self.prefix / "v1/dispatch.py").read_bytes(), dispatcher)

    def test_referenced_bundle_cannot_be_removed_or_republished_with_changed_bytes(self):
        from hook_runtime.bundles import remove_release
        from hook_runtime.state import RuntimeRefusal
        plugin = self.make_plugin("A")
        digest = self.publish(plugin)
        self.invoke("SessionStart")
        with self.assertRaises(RuntimeRefusal):
            remove_release(self.prefix, digest)
        self.assertEqual(self.publish(plugin), digest)
        bundle = self.prefix / "releases" / digest
        self.assertEqual(bundle.stat().st_mode & 0o222, 0)
        self.assertEqual((bundle / "codex/hook.py").stat().st_mode & 0o222, 0)

    def test_symlink_bundle_and_arbitrary_entrypoint_are_rejected(self):
        plugin = self.make_plugin("A")
        (plugin / "escape").symlink_to(self.workspace)
        result = subprocess.run([sys.executable, str(ROOT / "bin/exactory-runtime"),
                                 "publish", "--prefix", str(self.prefix), str(plugin)],
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        (plugin / "escape").unlink()
        digest = self.publish(plugin)
        self.invoke("SessionStart")
        command = next(self.handlers("PreToolUse"))[1]["command"].replace("hook.py guard_attack_files.py", "../bin/exactory")
        before = self.calls.read_bytes()
        self.assertEqual(self.invoke("PreToolUse", {"command": command}).returncode, 2)
        self.assertEqual(self.calls.read_bytes(), before)
        self.assertEqual(self.read_binding()["release"], digest)

    def test_smoke_driver_uses_app_server_inventory_and_all_real_managed_commands(self):
        self.publish(self.make_plugin("A"))
        # Native fixtures can register records in the default temp directory.
        smoke_parent = "/tmp" if sys.platform == "darwin" else "/var/tmp"
        smoke_temporary = tempfile.TemporaryDirectory(prefix="runtime smoke ", dir=smoke_parent)
        self.addCleanup(smoke_temporary.cleanup)
        smoke_workspace = Path(smoke_temporary.name).resolve()
        hooks = [{"eventName": event[0].lower() + event[1:], "command": handler["command"],
                  "enabled": True, "isManaged": True, "timeoutSec": handler["timeout"]}
                 for event, handler in self.handlers()]
        answer = {"id": 2, "result": {"data": [{"hooks": hooks, "errors": []}]}}
        fake = self.root / "codex-fixture"
        fake.write_text("#!" + sys.executable + "\nimport json, sys\n"
            "for line in sys.stdin:\n"
            "    request = json.loads(line)\n"
            "    if request.get('id') == 1:\n"
            "        print(json.dumps({'id': 1, 'result': {}}), flush=True)\n"
            "    if request.get('id') == 2:\n"
            "        print(" + repr(json.dumps(answer)) + ", flush=True)\n")
        fake.chmod(0o755)
        result = subprocess.run([sys.executable, str(ROOT / "tools/codex_hook_smoke.py"),
            "--prefix", str(self.prefix), "--workspace", str(smoke_workspace), "--codex", str(fake)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "RUNTIME_TEST_CALLS": str(self.calls)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"commands_executed": 11', result.stdout)
        self.assertIn('"isManaged": true', result.stdout)
        self.assertEqual(len(self.read_calls()), 11)
        self.assertEqual(len({json.loads(row["stdin"])["session_id"] for row in self.read_calls()}), 1)
        namespace = runpy.run_path(str(ROOT / "tools/codex_hook_smoke.py"))
        hooks.append({"enabled": True, "pluginId": "exactory@exactory-ai"})
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            namespace["select_commands"](self.prefix, answer)

    def test_corrupt_registry_and_missing_binding_refuse_without_reenrollment(self):
        from hook_runtime.state import build_binding_path
        self.publish(self.make_plugin("A"))
        self.invoke("SessionStart")
        before = self.calls.read_bytes()
        path = self.prefix / "state/registry.json"
        original = path.read_bytes()
        for invalid in ("{", "[]", '{"schema":1,"approved":[],"default":"../../outside"}'):
            path.write_text(invalid)
            for event, handler in self.handlers():
                self.assertEqual(self.invoke(event, handler).returncode, EXIT_CODES[event])
        path.write_bytes(original)
        build_binding_path(self.state, "A").unlink()
        for event, handler in self.handlers():
            self.assertEqual(self.invoke(event, handler, source="resume").returncode, EXIT_CODES[event])
        self.assertEqual(self.calls.read_bytes(), before)

    def test_unreadable_source_subtree_cannot_publish_a_partial_release(self):
        from hook_runtime.bundles import publish_release
        digest = self.publish(self.make_plugin("A"))
        plugin = self.make_plugin("B")
        blocked = plugin / "unreadable"
        blocked.mkdir()
        (blocked / "required.py").write_text("synthetic dependency")
        real_scandir = os.scandir

        def refuse_subtree(path):
            if not isinstance(path, int) and Path(path) == blocked:
                raise PermissionError("synthetic unreadable subtree")
            return real_scandir(path)

        with patch("os.scandir", side_effect=refuse_subtree):
            with self.assertRaises(PermissionError):
                publish_release(self.prefix, plugin)
        self.assertEqual(json.loads(self.run_cli("status", "--prefix", str(self.prefix)))["default"], digest)

    def test_startup_hashes_every_payload_file_and_later_events_hash_none(self):
        from hook_runtime.bundles import resolve_release
        plugin = self.make_plugin("A")
        (plugin / "data").mkdir()
        for number in range(8):
            (plugin / "data" / str(number)).write_text("synthetic payload")
        digest = self.publish(plugin)
        expected = {path.relative_to(plugin) for path in plugin.rglob("*") if path.is_file()}
        hashed_paths = []
        read_bytes = Path.read_bytes

        def count_hashed_file(path):
            hashed_paths.append(path.relative_to(self.prefix / "releases" / digest))
            return read_bytes(path)

        payload = {"session_id": "cost", "source": "startup"}
        with patch.object(Path, "read_bytes", count_hashed_file):
            self.assertEqual(resolve_release(self.prefix, payload, "SessionStart"),
                             self.prefix / "releases" / digest)
            self.assertEqual(set(hashed_paths), expected)
            self.assertEqual(len(hashed_paths), len(expected))
            hashed_paths.clear()
            self.assertEqual(resolve_release(self.prefix, payload, "PreToolUse"),
                             self.prefix / "releases" / digest)
            self.assertEqual(hashed_paths, [])
            for event, source in (("PostToolUse", "startup"), ("Stop", "startup"),
                                  ("SessionStart", "resume"), ("SessionStart", "compact"),
                                  ("SessionStart", "clear")):
                resolve_release(self.prefix, {**payload, "source": source}, event)
                self.assertEqual(hashed_paths, [])

    def test_enrollment_and_all_commands_use_writable_state_with_read_only_prefix(self):
        digest = self.publish(self.make_plugin("A"))
        before = {path.relative_to(self.prefix): path.read_bytes()
                  for path in self.prefix.rglob("*") if path.is_file()}
        make_read_only(self.prefix)
        for event, handler in sorted(self.handlers(), key=lambda row: row[0] != "SessionStart"):
            result = self.invoke(event, handler)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.read_calls()), 11)
        bindings = list((self.state / "sessions").glob("*.json"))
        self.assertEqual(len(bindings), 1)
        self.assertEqual(json.loads(bindings[0].read_text())["release"], digest)
        after = {path.relative_to(self.prefix): path.read_bytes()
                 for path in self.prefix.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
        status = json.loads(self.run_cli("status", "--prefix", str(self.prefix)))
        self.assertEqual(status["bindings"][0]["release"], digest)

    def test_missing_unwritable_or_symlink_state_refuses_without_execution(self):
        self.publish(self.make_plugin("A"))
        self.assertEqual(self.invoke("SessionStart").returncode, 0)
        before = self.calls.read_bytes()
        missing = self.root / "missing user state"
        unwritable = self.root / "unwritable user state"
        shutil.copytree(self.state, unwritable)
        make_read_only(unwritable)
        alias = self.root / "symlink user state"
        alias.symlink_to(self.state, target_is_directory=True)
        loop = self.root / "looped user state"
        loop.symlink_to(loop.name, target_is_directory=True)
        missing_parent_loop = self.root / "missing ancestor" / ".." / loop.name
        for state in (missing, unwritable, alias, loop, missing_parent_loop):
            for event, handler in self.handlers():
                with self.subTest(state=state.name, event=event):
                    result = self.invoke(event, handler, EXACTORY_RUNTIME_STATE_DIR=str(state))
                    self.assertEqual(result.returncode, EXIT_CODES[event], result.stderr)
                    self.assertIn("[exactory-runtime]", result.stderr)
                    self.assertEqual(result.stdout, "")
        self.assertFalse(missing.exists())
        self.assertEqual(self.calls.read_bytes(), before)

    def test_init_state_creates_private_storage_without_writing_prefix(self):
        self.publish(self.make_plugin("A"))
        state = self.root / "new private state"
        make_read_only(self.prefix)
        self.run_cli("init-state", "--prefix", str(self.prefix), "--state-dir", str(state))
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((state / "sessions").stat().st_mode & 0o777, 0o700)
        self.assertEqual((state / "runtime.lock").stat().st_mode & 0o777, 0o600)
        result = self.invoke("SessionStart", EXACTORY_RUNTIME_STATE_DIR=str(state))
        self.assertEqual(result.returncode, 0, result.stderr)
        bindings = list((state / "sessions").glob("*.json"))
        self.assertEqual(len(bindings), 1)
        status = json.loads(self.run_cli("status", "--prefix", str(self.prefix), "--state-dir", str(state)))
        self.assertEqual(status["bindings"][0]["session_id"], "A")

    def test_default_state_is_under_user_home_when_no_override_is_set(self):
        from hook_runtime.bundles import resolve_release
        digest = self.publish(self.make_plugin("A"))
        home = self.root / "synthetic home"
        state = home / ".local/state/exactory-runtime"
        (state / "sessions").mkdir(parents=True)
        (state / "runtime.lock").touch(mode=0o600)
        with patch.dict(os.environ):
            os.environ.pop("EXACTORY_RUNTIME_STATE_DIR", None)
            with patch.object(Path, "home", return_value=home):
                self.assertEqual(resolve_release(self.prefix, {"session_id": "default", "source": "startup"},
                                                 "SessionStart"), self.prefix / "releases" / digest)
        bindings = list((state / "sessions").glob("*.json"))
        self.assertEqual(len(bindings), 1)
        self.assertEqual(json.loads(bindings[0].read_text())["session_id"], "default")

    def test_state_inside_prefix_is_refused_without_creating_it(self):
        self.publish(self.make_plugin("A"))
        state = self.prefix / "user-session-state"
        result = self.invoke("SessionStart", EXACTORY_RUNTIME_STATE_DIR=str(state))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(state.exists())
        self.assertFalse(self.calls.exists())

    def test_later_events_authenticate_manifest_and_check_both_entrypoints(self):
        digest = self.publish(self.make_plugin("A"))
        self.assertEqual(self.invoke("SessionStart").returncode, 0)
        before = self.calls.read_bytes()
        root = self.prefix / "releases" / digest
        manifest = root / ".exactory-release.json"
        original = manifest.read_bytes()
        forged = json.loads(original)
        forged["entries"][0]["path"] = "unapproved-path"
        manifest.chmod(0o644)
        manifest.write_text(json.dumps(forged))
        manifest.chmod(0o444)
        result = self.invoke("PreToolUse")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.calls.read_bytes(), before)
        manifest.chmod(0o644)
        manifest.write_bytes(original)
        manifest.chmod(0o444)
        for name in ("hook.py", "session_start.py"):
            entrypoint = root / "codex" / name
            payload = entrypoint.read_bytes()
            entrypoint.parent.chmod(0o755)
            entrypoint.unlink()
            entrypoint.parent.chmod(0o555)
            result = self.invoke("PreToolUse")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(self.calls.read_bytes(), before)
            entrypoint.parent.chmod(0o755)
            entrypoint.write_bytes(payload)
            entrypoint.chmod(0o444)
            entrypoint.parent.chmod(0o555)


if __name__ == "__main__":
    unittest.main()
