"""Real local process trees exercise the subscription adapter's wall limit."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from research_harness import review_codex_transport as transport


PROCESS_FIXTURE = r'''
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
if "--version" in sys.argv:
    print("codex-cli local-timeout-fixture")
    raise SystemExit(0)
role = sys.argv[1] if sys.argv[1] in {"child", "grandchild"} else "wrapper"
(root / (role + ".json")).write_text(json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}))
if role == "wrapper":
    sys.stdin.read()
    if (root / "mode").read_text() == "complete":
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok": true}'}}), flush=True)
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}), flush=True)
        raise SystemExit(0)
else:
    def receive_term(signum, frame):
        (root / (role + ".term")).write_text("SIGTERM received")
        print(json.dumps({"type": "item.started", "item": {"id": role, "type": "termination_notice"}}), flush=True)
    signal.signal(signal.SIGTERM, receive_term)
if role != "grandchild":
    hidden = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if (root / "mode").read_text() == "closed-pipes" else {}
    child = subprocess.Popen([sys.executable, __file__, "child" if role == "wrapper" else "grandchild"], stdin=subprocess.DEVNULL, **hidden)
    child.wait()
else:
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"partial": true}'}}), flush=True)
    while True:
        time.sleep(1)
'''


class CodexProcessTimeoutTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="codex-process-fixture-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.program = self.root / "codex"
        self.program.write_text("#!" + sys.executable + "\n" + PROCESS_FIXTURE)
        self.program.chmod(0o700)
        (self.root / "mode").write_text("timeout")
        self.addCleanup(self.stop_fixture_processes)
        self.route = {"adapter": "codex_cli_v1", "model": "local-process-fixture", "endpoint": "codex://local",
                      "configuration": {"max_output_tokens": 100, "timeout_seconds": 1}}
        self.request = {"instructions": "Return the local fixture output.",
                        "input": [{"role": "user", "content": "No model call is made by this fixture."}]}

    def processes(self):
        return {role: json.loads((self.root / (role + ".json")).read_text())
                for role in ("wrapper", "child", "grandchild") if (self.root / (role + ".json")).exists()}

    def stop_fixture_processes(self):
        for item in reversed(list(self.processes().values())):
            try:
                os.kill(item["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def live(self, pid):
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
        state = result.stdout.strip()
        return bool(state) and not state.startswith("Z")

    def invoke(self):
        with patch.object(transport.shutil, "which", return_value=str(self.program)):
            return transport.send_request(self.route, self.request)

    def test_timeout_stops_owned_descendants_and_preserves_partial_observation(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        def stop_unrelated():
            unrelated.kill()
            unrelated.wait()
        self.addCleanup(stop_unrelated)
        watchdog_fired = threading.Event()
        def watchdog_cleanup():
            watchdog_fired.set()
            self.stop_fixture_processes()
        watchdog = threading.Timer(10, watchdog_cleanup)
        watchdog.daemon = True
        watchdog.start()
        self.addCleanup(watchdog.cancel)

        response = self.invoke()
        processes = self.processes()
        self.assertEqual(set(processes), {"wrapper", "child", "grandchild"})
        self.assertFalse(watchdog_fired.is_set(), "The adapter must finish without the test watchdog")
        self.assertIsNone(unrelated.poll(), "An unrelated process must retain its own lifetime")
        surviving = [role for role, item in processes.items() if self.live(item["pid"])]
        self.assertEqual(surviving, [], "A timed-out wrapper must not leave its native descendants running")
        self.assertEqual({item["pgid"] for item in processes.values()}, {processes["wrapper"]["pid"]})
        for role in ("child", "grandchild"):
            self.assertTrue((self.root / (role + ".term")).is_file(), "Each owned descendant gets a termination grace period")
        self.assertEqual(response["status"], "incomplete")
        self.assertIsNone(response["execution"]["returncode"])
        self.assertTrue(response["execution"]["timeout"])
        self.assertEqual(response["usage"], {}, "A partial run cannot manufacture final token usage")
        self.assertEqual(response["output"][0]["content"][0]["text"], '{"partial": true}')
        self.assertEqual(response["execution"]["event_count"], 3, "Drain the original output and both termination notices")

    def test_completed_process_retains_exit_status_output_and_observed_usage(self):
        (self.root / "mode").write_text("complete")
        response = self.invoke()
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["execution"]["returncode"], 0)
        self.assertFalse(response["execution"]["timeout"])
        self.assertEqual(response["output"][0]["content"][0]["text"], '{"ok": true}')
        self.assertEqual(response["usage"], {"input_tokens": 3, "output_tokens": 2})

    def test_timeout_stops_descendants_that_do_not_hold_the_output_pipes(self):
        (self.root / "mode").write_text("closed-pipes")
        response = self.invoke()
        processes = self.processes()
        self.assertEqual(set(processes), {"wrapper", "child", "grandchild"})
        surviving = [role for role, item in processes.items() if self.live(item["pid"])]
        self.assertEqual(surviving, [], "Closed output pipes do not establish that native work stopped")
        for role in ("child", "grandchild"):
            self.assertTrue((self.root / (role + ".term")).is_file())
        self.assertTrue(response["execution"]["timeout"])
        self.assertIsNone(response["execution"]["returncode"])
        self.assertEqual(response["usage"], {})
