"""Provider admission is durable and shared across local HTTP workers."""

import fcntl
import io
import json
import os
import select
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness.http import HttpClient, HttpFailure, RawResponse, RequestBudget, SystemClock
from research_harness.provider_pacing import ProviderPacing
from research_fixtures import Clock, client


ROOT = Path(__file__).resolve().parents[1]


def run_worker():
    options = json.loads(sys.argv[1])
    clock = SystemClock() if options.get("real_clock") else Clock()
    if not options.get("real_clock"):
        clock.seconds += options.get("elapsed", 0)
    resolved, starts = [], []
    budget = RequestBudget(1)

    def resolve(host, timeout):
        resolved.append(host)
        return ["93.184.216.34"]

    class Stream(io.BytesIO):
        def read(self, amount):
            if options.get("hold"):
                print(json.dumps({"phase": "body"}), flush=True)
                sys.stdin.readline()
            if options.get("broken_body"):
                from http.client import IncompleteRead
                raise IncompleteRead(b"partial")
            return super().read(amount)

    def transport(url, headers, **kwargs):
        starts.append(clock.now())
        return RawResponse(options.get("status", 200), options.get("headers", {}),
                           Stream(b"response"), "93.184.216.34")

    pacing = ProviderPacing(clock=clock, intervals=options["intervals"]) if "intervals" in options else None
    client = HttpClient(clock=clock, pacing=pacing, transport=transport, resolver=resolve, max_retries=0,
                        total_timeout=options.get("total_timeout", 120),
                        max_retry_after=options.get("max_retry_after", 60))
    try:
        result = client.get(options["url"], budget=budget)
        outcome = {"code": None, "attempts": len(result.attempts)}
    except HttpFailure as error:
        outcome = {"code": error.code, "attempts": len(error.attempts), "details": error.details}
    outcome.update({"starts": starts, "dns": len(resolved), "charged": budget.used,
                    "sleeps": getattr(clock, "sleeps", [])})
    print(json.dumps(outcome), flush=True)


class ProviderPacingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environ = dict(os.environ, HOME=str(self.root / "home"), XDG_STATE_HOME=str(self.root / "state"),
                            PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / "tests"))),
                            PYTHONDONTWRITEBYTECODE="1")
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {key: self.environ[key] for key in ("HOME", "XDG_STATE_HOME")}).start()

    def make_client(self, pacing, **kwargs):
        resolved, requested = [], []

        def resolve(host, timeout):
            resolved.append(host)
            return ["93.184.216.34"]

        def transport(url, headers, **options):
            requested.append(url)
            return RawResponse(200, {}, io.BytesIO(b"ok"), "93.184.216.34")

        http = HttpClient(clock=pacing.clock, pacing=pacing, resolver=resolve, transport=transport,
                          max_retries=0, **kwargs)
        return http, resolved, requested

    def assert_refused_before_network(self, http, resolved, requested, code, url="https://other.org/2"):
        budget = RequestBudget(1)
        with self.assertRaises(HttpFailure) as caught:
            http.get(url, budget=budget)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual((caught.exception.attempts, resolved, requested, budget.used), ([], [], [], 0))
        return caught.exception

    def start_worker(self, workspace, **options):
        directory = self.root / workspace
        directory.mkdir(exist_ok=True)
        process = subprocess.Popen(
            [sys.executable, "-c", "from test_research_provider_pacing import run_worker; run_worker()",
             json.dumps(options)], cwd=directory, env=self.environ, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_worker, process)
        return process

    def stop_worker(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def finish_worker(self, process):
        output, errors = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, errors)
        return json.loads(output)

    def test_separate_processes_share_arxiv_spacing_across_workspaces(self):
        first = self.finish_worker(self.start_worker("one", url="https://arxiv.org/abs/1"))
        second = self.finish_worker(self.start_worker("two", url="https://export.arxiv.org/api/query?id=2"))
        self.assertEqual(first["starts"], ["2026-09-08T00:00:00+00:00"])
        self.assertEqual(second["starts"], ["2026-09-08T00:00:03+00:00"])
        self.assertEqual(second["sleeps"], [3.0])
        self.assertEqual(second["charged"], 1)

    def test_live_connection_exclusion_expires_without_dns_or_charge(self):
        holder = self.start_worker("one", url="https://arxiv.org/a", real_clock=True, hold=True)
        self.assertTrue(select.select([holder.stdout], [], [], 5)[0])
        self.assertEqual(json.loads(holder.stdout.readline()), {"phase": "body"})
        second = self.finish_worker(self.start_worker("two", url="https://export.arxiv.org/b", real_clock=True,
                                                       total_timeout=0.2))
        self.assertEqual(second["code"], "rate_limited")
        self.assertEqual((second["attempts"], second["dns"], second["charged"]), (0, 0, 0))
        self.assertEqual(second["details"]["provider_group"], "arxiv")
        holder.kill()
        holder.communicate(timeout=5)
        recovered = self.finish_worker(self.start_worker("two", url="https://arxiv.org/c", real_clock=True))
        self.assertIsNone(recovered["code"])
        self.assertEqual(recovered["charged"], 1)

    def test_non_arxiv_connections_overlap_and_share_learned_cooldown(self):
        for workspace in ("one", "two"):
            holder = self.start_worker(workspace, url="https://api.crossref.org/works", hold=True,
                                       total_timeout=0.2)
            self.assertTrue(select.select([holder.stdout], [], [], 5)[0])
            self.assertEqual(json.loads(holder.stdout.readline()), {"phase": "body"})
        learned = self.finish_worker(self.start_worker("three", url="https://api.crossref.org/works/3",
                                                       status=429, headers={"Retry-After": "120"}))
        self.assertEqual((learned["code"], learned["attempts"], learned["charged"]), ("rate_limited", 1, 1))
        for workspace in ("one", "two"):
            blocked = self.finish_worker(self.start_worker(workspace, url="https://api.crossref.org/works/4",
                                                           max_retry_after=2))
            self.assertEqual(blocked["code"], "rate_limited")
            self.assertEqual(blocked["details"]["next_eligible_at"], "2026-09-08T00:02:00+00:00")
            self.assertEqual((blocked["attempts"], blocked["dns"], blocked["charged"]), (0, 0, 0))

    def test_different_non_arxiv_hosts_do_not_exclude_each_other(self):
        # These hosts collided under the former 64-stripe lock allocation.
        holder = self.start_worker("one", url="https://example.org/a", hold=True)
        self.assertTrue(select.select([holder.stdout], [], [], 5)[0])
        self.assertEqual(json.loads(holder.stdout.readline()), {"phase": "body"})
        other = self.finish_worker(self.start_worker("two", url="https://host9.example.org/b", total_timeout=0.2))
        self.assertIsNone(other["code"])
        self.assertEqual((other["attempts"], other["dns"], other["charged"]), (1, 1, 1))

    def test_configured_non_arxiv_interval_is_shared_without_connection_exclusion(self):
        intervals = {"https://api.crossref.org": 2.0}
        holder = self.start_worker("one", url="https://api.crossref.org/works/1", hold=True, intervals=intervals)
        self.assertTrue(select.select([holder.stdout], [], [], 5)[0])
        self.assertEqual(json.loads(holder.stdout.readline()), {"phase": "body"})
        second = self.finish_worker(self.start_worker("two", url="https://api.crossref.org/works/2",
                                                       intervals=intervals, total_timeout=5))
        self.assertIsNone(second["code"])
        self.assertEqual(second["starts"], ["2026-09-08T00:00:02+00:00"])
        self.assertEqual(second["sleeps"], [2.0])

    def test_only_connection_policies_create_dedicated_lock_files(self):
        pacing = ProviderPacing(clock=Clock())
        for url in ("https://api.crossref.org/works", "https://example.org/1", "https://arxiv.org/abs/1"):
            with pacing.admit(url, deadline=pacing.clock.monotonic() + 5, max_wait=5):
                pass
        self.assertEqual(sorted(path.name for path in pacing.state_dir.glob("*.lock")), ["provider-arxiv.lock"])

    def test_retry_after_survives_body_failure_process_exit_and_alias_change(self):
        first = self.finish_worker(self.start_worker("one", url="https://export.arxiv.org/api/query?id=1",
                                                      status=429, headers={"Retry-After": "120"}, broken_body=True))
        self.assertEqual(first["code"], "incomplete_response")
        second = self.finish_worker(self.start_worker("two", url="https://arxiv.org/abs/2", max_retry_after=2))
        self.assertEqual(second["code"], "rate_limited")
        self.assertEqual((second["attempts"], second["dns"], second["charged"]), (0, 0, 0))
        self.assertEqual(second["details"]["next_eligible_at"], "2026-09-08T00:02:00+00:00")
        self.assertEqual(second["sleeps"], [])

    def test_arxiv_exclusion_outlives_the_start_interval_and_releases_on_death(self):
        holder = self.start_worker("one", url="https://export.arxiv.org/1", hold=True)
        self.assertTrue(select.select([holder.stdout], [], [], 5)[0])
        self.assertEqual(json.loads(holder.stdout.readline()), {"phase": "body"})
        blocked = self.finish_worker(self.start_worker("two", url="https://arxiv.org/2",
                                                       elapsed=10, total_timeout=0.2))
        self.assertEqual(blocked["code"], "rate_limited")
        self.assertEqual((blocked["attempts"], blocked["dns"], blocked["charged"]), (0, 0, 0))
        holder.kill()
        holder.communicate(timeout=5)
        recovered = self.finish_worker(self.start_worker("two", url="https://arxiv.org/2", elapsed=10))
        self.assertIsNone(recovered["code"])
        self.assertEqual(recovered["charged"], 1)

    def test_cooldown_is_committed_while_the_response_body_is_still_open(self):
        holder = self.start_worker("one", url="https://arxiv.org/abs/1", hold=True,
                                   status=503, headers={"Retry-After": "120"})
        self.assertTrue(select.select([holder.stdout], [], [], 5)[0])
        self.assertEqual(json.loads(holder.stdout.readline()), {"phase": "body"})
        holder.kill()
        holder.communicate(timeout=5)
        second = self.finish_worker(self.start_worker("two", url="https://export.arxiv.org/api/query?id=2",
                                                       max_retry_after=2))
        self.assertEqual(second["code"], "rate_limited")
        self.assertEqual(second["details"]["next_eligible_at"], "2026-09-08T00:02:00+00:00")
        self.assertEqual(second["charged"], 0)

    def test_new_clients_import_historical_pacing_into_shared_state(self):
        clock = Clock()
        first = HttpClient(clock=clock)
        first.pace_from("export.arxiv.org", clock.now())
        second = self.finish_worker(self.start_worker("two", url="https://arxiv.org/abs/2"))
        self.assertEqual(second["sleeps"], [3.0])

    def test_fixture_clients_use_independent_temporary_state(self):
        first, _, _ = client([(429, {"Retry-After": "120"}, b"wait")], max_retries=0)
        with self.assertRaises(HttpFailure):
            first.get("https://arxiv.org/1")
        second, _, _ = client([(200, {}, b"ok")])
        self.assertNotEqual(first.pacing.state_dir, second.pacing.state_dir)
        self.assertEqual(second.get("https://export.arxiv.org/2").body, b"ok")
        self.assertFalse((self.root / "state").exists())

    def test_errors_of_the_admitted_request_are_not_scheduler_errors(self):
        pacing = ProviderPacing(clock=Clock())
        for url in ("https://arxiv.org/abs/1", "https://example.org/1"):
            with self.subTest(url=url):
                with self.assertRaises(ConnectionResetError):
                    with pacing.admit(url, deadline=pacing.clock.monotonic() + 5, max_wait=5):
                        raise ConnectionResetError("network failure inside the admitted request")
        lock = pacing.state_dir / "provider-arxiv.lock"
        with open(lock, "rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_default_state_location_uses_xdg_or_home(self):
        for xdg in (True, False):
            with self.subTest(xdg=xdg), patch.dict(os.environ):
                if not xdg:
                    os.environ.pop("XDG_STATE_HOME", None)
                pacing = ProviderPacing(clock=Clock())
                expected = self.root / ("state" if xdg else "home/.local/state") / "exactory"
                self.assertEqual(pacing.state_dir, expected)
                with pacing.admit("https://example.org/1", deadline=pacing.clock.monotonic() + 5, max_wait=5):
                    self.assertTrue((expected / "provider-pacing.sqlite3").is_file())

    def test_capacity_reclaims_expired_rows_without_losing_active_cooldown(self):
        clock = Clock()
        pacing = ProviderPacing(clock=clock, row_limit=2)
        pacing.defer_until("https://arxiv.org/1", "2026-09-08T00:02:00+00:00",
                           deadline=clock.monotonic() + 5, max_wait=5)
        pacing.defer_until("https://example.org/1", "2026-09-08T00:00:10+00:00",
                           deadline=clock.monotonic() + 5, max_wait=5)
        http, resolved, requested = self.make_client(pacing, total_timeout=1)
        self.assert_refused_before_network(http, resolved, requested, "provider_state_capacity")
        clock.seconds += 11
        self.assertEqual(http.get("https://other.org/2").body, b"ok")
        blocked, resolved, requested = self.make_client(pacing, total_timeout=1)
        error = self.assert_refused_before_network(blocked, resolved, requested, "rate_limited",
                                                   "https://export.arxiv.org/3")
        self.assertEqual(error.details["next_eligible_at"], "2026-09-08T00:02:00+00:00")
        with sqlite3.connect(str(pacing.state_dir / "provider-pacing.sqlite3")) as connection:
            self.assertLessEqual(connection.execute("SELECT count(*) FROM provider_state").fetchone()[0], 2)

    def test_capacity_does_not_reclaim_an_expired_row_with_a_live_connection(self):
        clock = Clock()
        pacing = ProviderPacing(clock=clock, row_limit=1)
        with pacing.admit("https://arxiv.org/1", deadline=clock.monotonic() + 5, max_wait=5):
            clock.seconds += 10
            http, resolved, requested = self.make_client(pacing)
            self.assert_refused_before_network(http, resolved, requested, "provider_state_capacity")
        self.assertEqual(http.get("https://other.org/2").body, b"ok")

    def test_corrupt_or_unavailable_state_refuses_without_network(self):
        for kind in ("database", "directory", "schema", "timestamps", "expired_timestamps", "provider",
                     "infinite_timestamp", "null_timestamp", "unrepresentable_timestamp"):
            with self.subTest(kind=kind):
                state_dir = self.root / kind
                pacing = ProviderPacing(clock=Clock(), state_dir=state_dir)
                if kind == "directory":
                    state_dir.write_bytes(b"not a directory")
                else:
                    state_dir.mkdir()
                    database = state_dir / "provider-pacing.sqlite3"
                    if kind == "database":
                        database.write_bytes(b"corrupt database")
                    else:
                        with sqlite3.connect(str(database)) as connection:
                            if kind == "schema":
                                connection.execute("CREATE TABLE provider_state (provider TEXT PRIMARY KEY)")
                            else:
                                connection.execute("CREATE TABLE provider_state (provider TEXT PRIMARY KEY, next_start, cooldown)")
                                provider = None if kind == "provider" else "https://other.org"
                                start = {"provider": 0, "expired_timestamps": -1, "infinite_timestamp": float("inf"),
                                         "null_timestamp": None, "unrepresentable_timestamp": 1e300}.get(kind, "invalid")
                                connection.execute("INSERT INTO provider_state VALUES (?, ?, 0)", (provider, start))
                http, resolved, requested = self.make_client(pacing)
                self.assert_refused_before_network(http, resolved, requested, "provider_state_unavailable")

    def test_sqlite_busy_expires_without_network_or_losing_the_deadline(self):
        pacing = ProviderPacing(clock=Clock())
        pacing.defer_until("https://arxiv.org/1", "2026-09-08T00:02:00+00:00",
                           deadline=pacing.clock.monotonic() + 5, max_wait=5)
        connection = sqlite3.connect(str(pacing.state_dir / "provider-pacing.sqlite3"), isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("BEGIN IMMEDIATE")
        busy = self.finish_worker(self.start_worker("busy", url="https://arxiv.org/2", total_timeout=0.2))
        self.assertEqual(busy["code"], "provider_state_unavailable")
        self.assertEqual((busy["attempts"], busy["dns"], busy["charged"]), (0, 0, 0))
        connection.execute("ROLLBACK")
        recovered = self.finish_worker(self.start_worker("recovered", url="https://export.arxiv.org/3", total_timeout=1))
        self.assertEqual(recovered["code"], "rate_limited")
        self.assertEqual(recovered["details"]["next_eligible_at"], "2026-09-08T00:02:00+00:00")

    def test_sqlite_busy_commit_retries_until_reader_releases_state(self):
        clock = Clock()
        pacing = ProviderPacing(clock=clock)
        with pacing.admit("https://example.org/1", deadline=clock.monotonic() + 5, max_wait=5):
            pass
        reader = sqlite3.connect(str(pacing.state_dir / "provider-pacing.sqlite3"), isolation_level=None)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM provider_state").fetchall()
        sleep = clock.sleep

        def release_reader(seconds):
            reader.execute("ROLLBACK")
            clock.sleep = sleep
            sleep(seconds)

        clock.sleep = release_reader
        pacing.defer_until("https://arxiv.org/1", "2026-09-08T00:02:00+00:00",
                           deadline=clock.monotonic() + 5, max_wait=5)
        self.assertEqual(clock.sleeps, [0.05])
        http, resolved, requested = self.make_client(pacing, total_timeout=1)
        self.assert_refused_before_network(http, resolved, requested, "rate_limited", "https://arxiv.org/2")

    def test_explicit_wait_reports_provider_and_preserves_shared_deadline(self):
        pacing = ProviderPacing(clock=Clock())
        http, resolved, requested = self.make_client(pacing, total_timeout=1)
        with self.assertRaises(HttpFailure) as caught:
            http.get("https://arxiv.org/1", not_before="2026-09-08T00:02:00+00:00")
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(caught.exception.details.get("provider_group"), "arxiv")
        self.assert_refused_before_network(http, resolved, requested, "rate_limited", "https://export.arxiv.org/2")


if __name__ == "__main__":
    unittest.main()
