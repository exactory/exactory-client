"""Frozen public v1 terminal history survives current computation amendments."""

import base64
import hashlib
import json
from pathlib import Path
import sys

from search_controller.errors import SearchError
from search_controller.model import replay
from search_controller.service import Controller
from tests import test_search_computation as computation
from tests.search_execution_support import command_spec, invoke
from tests.search_fixtures import digest
from tests.support import FIXTURE_STRATEGIES, WorkspaceTest


class HistoricalReleaseTests(WorkspaceTest):
    def test_real_v1_terminal_history_replays_once_and_new_run_requires_amendment(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/historical-v1-completed-run.json").read_text())
        raw = base64.b64decode(fixture["tree_base64"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "a0e56bc52f341484cb19fa7f8e54eab3159ae160ce7774cd6f67729f95e6a2a6")
        state = replay(json.loads(raw))
        self.assertEqual(state["revision"], 10)
        old_run = state["runs"]["run-000001"]
        self.assertEqual(old_run["status"], "terminal")
        self.assertNotIn("computation_digest", old_run)
        for counters in [state["totals"], state["accounts"]["account-000001"]]:
            self.assertEqual((counters["used_runs"], counters["reserved_runs"]), (1, 0))
        # Restore archived content locally. Historical absolute path strings remain
        # immutable provenance; no old executable or historical workspace is used.
        for relative, encoded in fixture["files"].items():
            path = self.attack_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded))
        tree = self.attack_root / ".search/tree.json"
        tree.write_bytes(raw)
        controller = Controller(self.attack_root, FIXTURE_STRATEGIES)
        marker = self.attack_root / "new-producer-ran"
        (self.workspace / "deterministic/job/job.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('one fresh run')\n")
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        before = controller.status()
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "run", spec)
        self.assertEqual(caught.exception.code, "computation_amendment_required")
        self.assertEqual(tree.read_bytes(), raw)
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["accounts"], before["accounts"])
        amendment = computation.ComputationHistoricalTests.amendment(self, controller)
        invoke(controller, "amend-computation", amendment)
        self.assertEqual(controller.status()["accounts"], before["accounts"])
        self.assertEqual(controller.status()["runs"]["run-000001"], old_run)
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "run", spec)
        self.assertEqual(caught.exception.code, "strategy_reassessment_required")
        after = controller.status()
        self.assertFalse(marker.exists())
        self.assertEqual(after["runs"]["run-000001"], old_run)
        self.assertEqual(len(after["runs"]), 1)
        self.assertEqual(after["contract"], before["contract"])
        for counters in [after["totals"], after["accounts"]["account-000001"]]:
            self.assertEqual((counters["used_runs"], counters["reserved_runs"]), (1, 0))
