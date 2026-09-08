"""Real common writers cannot change preparation during native token delivery."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.research_support import PLUGIN
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke
from tests.support import run
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store
from research_harness.synthesis import synthesis_state
from search_controller import execution, integration
from search_controller.errors import SearchError


COMMON_WRITER = """
import json
from pathlib import Path
import sys
from research_harness.errors import ResearchError
from research_harness.storage import Store
from research_harness.synthesis import record_standards
store = Store(Path(sys.argv[1]))
payload = json.loads(sys.argv[2])
result = {"before_revision": store.revision}
try:
    result["receipt"] = record_standards(store, payload,
        expected_revision=int(sys.argv[3]), request_id=sys.argv[4])
except ResearchError as error:
    result["error"] = {"code": error.code, "message": error.message}
result["after_revision"] = store.revision
print(json.dumps(result))
"""


class ForwardedInput:
    def __init__(self, stream, observations, pid):
        self.stream = stream
        self.observations = observations
        self.pid = pid

    def write(self, value):
        written = self.stream.write(value)
        self.observations["events"].append({"kind": "token_write", "pid": self.pid,
            "bytes": written, "sha256": hashlib.sha256(value).hexdigest()})
        return written

    def flush(self):
        result = self.stream.flush()
        self.observations["events"].append({"kind": "token_flush", "pid": self.pid})
        return result

    def __getattr__(self, name):
        return getattr(self.stream, name)


class NativeTokenGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "attack"
        self.root.mkdir()
        self.assertEqual(run(["init", "sample"], self.root)[0], 0)
        self.controller = admit_workspace(self.root, computational=True)
        workspace = self.root / "sample"
        step = workspace / "deterministic/job"
        step.mkdir()
        self.marker = self.root / "producer-started"
        (step / "job.py").write_text("from pathlib import Path\nPath(" + repr(str(self.marker)) +
                                    ").write_text('executed')\nprint('guarded producer executed')\n")
        invoke(self.controller, "begin", begin_spec())
        with patch.object(execution, "launch"):
            invoke(self.controller, "run", command_spec(workspace, [sys.executable, "job.py"]))
        self.reserved = self.controller.status()["runs"]["run-000001"]
        before = Store(self.root).snapshot()
        self.revision = before["revision"]
        selected = before["records"]["synthesis_selection"]["research:standards"]["id"]
        self.standards = copy.deepcopy(before["records"]["synthesis"][selected]["payload"])
        self.standards["id"] = "standards-at-token-boundary"
        self.standards["scope"] += " Revised at the native token boundary."
        self.observations = {"events": [], "before_common_revision": self.revision}

    def common_writer(self):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONPATH", None)
        completed = subprocess.run([sys.executable, "-B", "-c", COMMON_WRITER,
            str(self.root), json.dumps(self.standards), str(self.revision), "standards-at-token-boundary"],
            cwd=PLUGIN, env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        observed = json.loads(completed.stdout)
        self.observations["events"].append({"kind": "common_writer", "result": observed})
        return observed

    def observed_launch(self):
        original = subprocess.Popen

        def start(*args, **kwargs):
            process = original(*args, **kwargs)
            if "--launcher" in args[0]:
                self.observations["launcher_pid"] = process.pid
                process.stdin = ForwardedInput(process.stdin, self.observations, process.pid)
            return process

        with patch.object(execution.subprocess, "Popen", side_effect=start):
            try:
                execution.launch(self.controller, self.reserved)
            except SearchError as error:
                self.observations["launch_error"] = {"code": error.code, "message": error.message}
        current = self.controller.status()["runs"][self.reserved["id"]]
        self.observations["run"] = {key: current[key] for key in
            ("id", "status", "termination", "started_units", "charged_units", "result_digest")}
        self.observations["producer_started"] = self.marker.exists()
        if current["result_digest"] is not None:
            result = self.controller.store.get_blob(current["result_digest"])
            self.observations["stdout"] = [self.controller.store.get_artifact(item["stdout_digest"]).decode()
                                             for item in result["commands"]]
        return current

    def assert_no_token_or_producer(self):
        detail = json.dumps(self.observations, sort_keys=True)
        self.assertFalse(any(item["kind"].startswith("token_") for item in self.observations["events"]), detail)
        self.assertFalse(self.observations["producer_started"], detail)
        current = self.observations["run"]
        self.assertEqual((current["status"], current["termination"], current["started_units"],
                          current["charged_units"]), ("terminal", "never_started", 0, 0), detail)

    def test_common_mutation_after_final_audit_cannot_commit_before_actual_token(self):
        original = integration.audit_work
        attempts = []

        def audit_then_attempt(controller, state, node, content, **kwargs):
            original(controller, state, node, content, **kwargs)
            if state["runs"][self.reserved["id"]]["status"] == "launched":
                attempts.append(self.common_writer())

        with patch.object(integration, "audit_work", side_effect=audit_then_attempt):
            current = self.observed_launch()
        self.observations["common_revision_after_launch"] = Store(self.root).revision
        detail = json.dumps(self.observations, sort_keys=True)
        self.assertEqual(len(attempts), 1, detail)
        self.assertEqual(attempts[0].get("error", {}).get("code"), "store_busy", detail)
        self.assertEqual(Store(self.root).revision, self.revision, detail)
        delivered = [item for item in self.observations["events"] if item["kind"] == "token_write"]
        self.assertEqual(len(delivered), 1, detail)
        expected = (self.reserved["token"] + "\n").encode()
        self.assertEqual(delivered[0], {"kind": "token_write", "pid": self.observations["launcher_pid"],
            "bytes": len(expected), "sha256": hashlib.sha256(expected).hexdigest()}, detail)
        self.assertEqual(sum(item["kind"] == "token_flush" for item in self.observations["events"]), 1, detail)
        self.assertTrue(self.observations["producer_started"], detail)
        self.assertEqual(self.observations["stdout"], ["guarded producer executed\n"], detail)
        self.assertEqual((current["status"], current["termination"], current["started_units"],
                          current["charged_units"]), ("terminal", "exit", 1, 1), detail)
        native_history = self.controller.store.read()
        retry = self.common_writer()
        self.observations["retry"] = retry
        self.assertEqual(retry["receipt"]["revision"], self.revision + 1)
        self.assertEqual(self.common_writer()["receipt"], retry["receipt"])
        self.assertEqual(self.controller.store.read(), native_history)
        common = Store(self.root).snapshot()
        self.assertFalse(synthesis_state(common["records"], ArtifactStore(self.root), "research")["ready"])
        with self.assertRaises(SearchError) as caught:
            original(self.controller, self.controller.status(),
                self.controller.status()["nodes"][self.reserved["node_id"]], self.controller.store)
        self.assertEqual(caught.exception.code, "research_foundation_stale")

    def test_supported_mutation_before_final_audit_refuses_actual_token(self):
        original = integration.internal_operation

        def record_then_change(controller, command, request_id, build):
            result = original(controller, command, request_id, build)
            if command == "execution-launch":
                self.assertIn("receipt", self.common_writer())
            return result

        with patch.object(integration, "internal_operation", side_effect=record_then_change):
            self.observed_launch()
        self.assertEqual(self.observations["launch_error"]["code"], "research_foundation_stale")
        self.assert_no_token_or_producer()
        self.assertEqual(Store(self.root).revision, self.revision + 1)

    def test_interruption_after_final_audit_releases_common_ownership_for_retry(self):
        original = integration.audit_work

        def audit_then_interrupt(controller, state, node, content, **kwargs):
            original(controller, state, node, content, **kwargs)
            if state["runs"][self.reserved["id"]]["status"] == "launched":
                raise SearchError("fixture_interruption", "Controlled interruption after the actual final audit")

        with patch.object(integration, "audit_work", side_effect=audit_then_interrupt):
            self.observed_launch()
        self.assertEqual(self.observations["launch_error"]["code"], "fixture_interruption")
        self.assert_no_token_or_producer()
        retry = self.common_writer()
        self.observations["retry"] = retry
        self.assertEqual(retry["receipt"]["revision"], self.revision + 1)
        self.assertEqual(self.common_writer()["receipt"], retry["receipt"])

    def test_busy_common_guard_entry_reaps_the_owned_launcher_without_a_token(self):
        original = integration.internal_operation
        owners = []
        source = """
import sqlite3,sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("BEGIN EXCLUSIVE")
print("held", flush=True)
sys.stdin.readline()
connection.rollback()
connection.close()
"""

        def record_then_hold(controller, command, request_id, build):
            result = original(controller, command, request_id, build)
            if command == "execution-launch":
                owner = subprocess.Popen([sys.executable, "-B", "-c", source,
                    str(self.root / ".exactory/research.sqlite3")], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                owners.append(owner)
                self.assertEqual(owner.stdout.readline().strip(), "held")
            return result

        try:
            with patch.object(integration, "internal_operation", side_effect=record_then_hold):
                self.observed_launch()
            self.assertEqual(self.observations["launch_error"]["code"], "research_readiness_required")
            self.assert_no_token_or_producer()
        finally:
            for owner in owners:
                output, error = owner.communicate("release\n", timeout=8)
                self.assertEqual((owner.returncode, output, error), (0, "", ""))
        self.assertEqual(Store(self.root).revision, self.revision)
        self.assertEqual(self.common_writer()["receipt"]["revision"], self.revision + 1)

    def test_audit_rejects_a_guard_for_another_workspace_or_an_expired_guard(self):
        state = self.controller.status()
        node = state["nodes"][self.reserved["node_id"]]
        other = Store(self.root / "other-common-workspace", create=True)
        with other.guarded_snapshot() as other_guard:
            with self.assertRaises(SearchError) as caught:
                integration.audit_work(self.controller, state, node, self.controller.store,
                                       common_guard=other_guard)
            self.assertEqual(caught.exception.code, "research_foundation_mismatch")
        with Store(self.root).guarded_snapshot() as guard:
            integration.audit_work(self.controller, state, node, self.controller.store, common_guard=guard)
        with self.assertRaises(SearchError) as caught:
            integration.audit_work(self.controller, state, node, self.controller.store, common_guard=guard)
        self.assertEqual(caught.exception.code, "research_readiness_required")
        self.assertEqual(caught.exception.details["cause"], "invalid_snapshot_guard")
        self.assertEqual(Store(self.root).revision, self.revision)
        self.assertEqual(self.controller.status(), state)


if __name__ == "__main__":
    unittest.main()
