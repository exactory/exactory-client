"""Shared admission is transactional across command processes."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from research_harness import review_task_execution as execution
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.storage import Store, _canonical


class TaskExecutionFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.store = Store(self.root, create=True)
        self.put("review_route", "route", {"id": "route", "adapter": "codex_cli_v1", "configuration": {}})
        self.put("review_epoch", "epoch", {"id": "epoch", "generation": 1, "context": "original"})
        self.put("context", "slot", {"version": 1})
        self.request = self.store.build_artifact_store().put(_canonical({"history": [], "packet": {}}).encode(),
                                                            "application/json")
        for name in ("a", "b", "c"):
            self.put("review_task", name, {"id": name, "epoch_id": "epoch", "generation": 1})

    def put(self, kind, key, value):
        return self.store.mutate("fixture", {"kind": kind, "key": key, "value": value},
                                 lambda tx: tx.put(kind, key, value), expected_revision=self.store.revision,
                                 request_id="fixture-" + str(self.store.revision))

    def payload(self, name="a"):
        return {"task_id": name, "task_generation": 1, "epoch_id": "epoch", "route_id": "route",
                "request": self.request, "selectors": [{"kind": "context", "key": "slot",
                    "digest": digest(self.store.snapshot()["records"]["context"]["slot"])}]}

    def reserve(self, name="a", request_id=None):
        return execution.reserve_task(self.store, self.payload(name), request_id=request_id or "reserve-" + name)

    def refuse(self, code, action):
        with self.assertRaises(ResearchError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)


class TaskExecutionTests(TaskExecutionFixture):
    def test_planning_is_durable_without_consuming_capacity(self):
        result = execution.plan_task(self.store, self.payload(), request_id="plan")
        self.assertEqual(result["result"]["state"], "planned")
        self.assertEqual(self.store.snapshot()["records"].get("review_reservation", {}), {})
        self.assertEqual(self.reserve()["result"]["state"], "reserved")

    def test_default_capacity_one_atomic_membership_and_duplicate_task(self):
        first = self.reserve()
        self.assertEqual(self.reserve(), first)
        self.refuse("review_invocation_pending", lambda: self.reserve(request_id="duplicate"))
        self.refuse("review_capacity_wait", lambda: self.reserve("b"))
        records = self.store.snapshot()["records"]
        reservation = first["result"]
        self.assertEqual(reservation["state"], "reserved")
        self.assertTrue(records["review_pool_membership"][reservation["id"]]["active"])
        self.assertEqual(reservation["recorded_revision"], first["revision"])

    def test_record_capacity_route_pool_and_lowering_drains(self):
        execution.record_capacity(self.store, {"store_limit": 2, "route_limits": {"route": 2}}, request_id="capacity")
        self.reserve()
        self.reserve("b")
        execution.record_capacity(self.store, {"store_limit": 1, "route_limits": {}}, request_id="lower")
        self.refuse("review_capacity_wait", lambda: self.reserve("c"))
        self.assertEqual(len([r for r in self.store.snapshot()["records"]["review_reservation"].values()
                              if not r["released"]]), 2)

    def test_route_pool_can_be_smaller_than_store_pool(self):
        execution.record_capacity(self.store, {"store_limit": 2, "route_limits": {"route": 1}}, request_id="capacity")
        self.reserve()
        self.refuse("review_capacity_wait", lambda: self.reserve("b"))

    def test_preexisting_pending_legacy_invocation_occupies_capacity(self):
        self.put("review_invocation", "legacy", {"id": "legacy", "route_id": "route"})
        self.refuse("review_capacity_wait", lambda: self.reserve())
        self.put("review_attempt", "legacy", {"id": "legacy", "status": "failed"})
        self.assertEqual(self.reserve()["result"]["state"], "reserved")

    def test_task_generation_epoch_selector_and_absence_are_checked(self):
        payload = self.payload()
        payload["task_generation"] = 2
        self.refuse("review_task_dependency_changed", lambda: execution.reserve_task(self.store, payload, request_id="bad-gen"))
        payload = self.payload()
        payload["selectors"][0]["digest"] = "0" * 64
        self.refuse("review_task_dependency_changed", lambda: execution.reserve_task(self.store, payload, request_id="bad-selector"))
        payload = self.payload()
        payload["dependencies"] = [{"kind": "review_epoch", "key": "epoch", "digest": "0" * 64}]
        self.refuse("review_task_dependency_changed", lambda: execution.reserve_task(self.store, payload, request_id="bad-epoch"))
        payload = self.payload()
        payload["absence"] = [{"kind": "review_task", "key": "b"}]
        self.refuse("review_task_dependency_changed", lambda: execution.reserve_task(self.store, payload, request_id="bad-absence"))

    def test_unowned_capture_and_release_are_refused(self):
        reservation = self.reserve()["result"]
        self.refuse("review_instrumented_capture_required", lambda: execution.capture_observation(
            self.store, reservation["id"], {"status": "completed"}, request_id="forged"))
        self.refuse("review_worker_reconciliation_required", lambda: execution.release_reservation(
            self.store, {"reservation_id": reservation["id"]}, request_id="release"))

    def race(self, names):
        gate = self.root / "gate"
        program = '''import json, sys, time
from pathlib import Path
from research_harness.storage import Store
from research_harness.review_task_execution import reserve_task
from research_harness.errors import ResearchError
root, gate, payload, request = sys.argv[1:]
while not Path(gate).exists(): time.sleep(.005)
try:
    result = reserve_task(Store(Path(root)), json.loads(payload), request_id=request)
    print(json.dumps({"ok": result["result"]["id"]}), flush=True)
except ResearchError as error: print(json.dumps({"error": error.code}), flush=True)
'''
        processes = [subprocess.Popen([sys.executable, "-c", program, str(self.root), str(gate),
                         json.dumps(self.payload(name)), "race-" + str(i)], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True) for i, name in enumerate(names)]
        try:
            gate.touch()
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, stderr)
                results.append(json.loads(stdout))
            return results
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)

    def test_two_processes_race_for_last_capacity(self):
        results = self.race(("a", "b"))
        self.assertEqual(sum("ok" in item for item in results), 1, results)
        self.assertEqual([r["error"] for r in results if "error" in r], ["review_capacity_wait"])

    def test_two_processes_cannot_reserve_the_same_task_twice(self):
        execution.record_capacity(self.store, {"store_limit": 2, "route_limits": {}}, request_id="capacity")
        results = self.race(("a", "a"))
        self.assertEqual(sum("ok" in item for item in results), 1, results)
        self.assertEqual([r["error"] for r in results if "error" in r], ["review_invocation_pending"])
