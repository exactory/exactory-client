"""The records of a probe are written while other writers commit to the same store."""

import json
import sqlite3
import sys
from unittest import mock

from literature_fixtures import LiteratureCase
from research_harness import cli, finishing, probe, probe_provenance, probe_worker, program_probes, resources, storage
from research_harness.errors import ResearchError
from research_harness.operations import immutable_record, prepared_mutation
from research_harness.principles import initialize_research
from research_harness.problem_accounts import describe_account_records
from research_harness.problem_evidence import cite_record
from research_harness.storage import Store

ORIGINAL_MUTATE = Store.mutate
# The commit entries of the store. A release before the one that adds mutate_serialized has the first two.
COMMIT_ENTRIES = [name for name in ("mutate", "mutate_scoped", "mutate_serialized") if hasattr(Store, name)]
OUTPUT_COUNT = 5
# The members of a probe record that 0.91.4 writes for a capture that completed.
PROBE_RECORD_MEMBERS_0_91_4 = ["command", "elapsed_seconds", "exit_status", "id", "observation", "outputs", "revision",
                               "stderr", "stdout"]


class TickingClock:
    """A clock for retry_store_write that passes one second per reading, so that its wait ends without real time."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.0
        return self.now


class ProbeContentionCase(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.mutate(initialize_research, {"profile": "research", "target": None})
        self.unrelated_count = 0
        self.attempts = {}
        waits = mock.patch.object(finishing.time, "sleep", lambda seconds: None)
        waits.start()
        self.addCleanup(waits.stop)

    def commit_unrelated(self):
        """One commit of another writer: a record of a kind that no probe code reads."""
        self.unrelated_count += 1
        number = self.unrelated_count
        other = Store(self.root)
        ORIGINAL_MUTATE(other, "fixture.unrelated", {"n": number},
                        lambda tx: tx.put("fixture_unrelated", "row-" + str(number), {"n": number}),
                        expected_revision=other.revision, request_id="unrelated-" + str(number))

    def meet_commits(self, operations, act=None, role=None):
        """Before each commit of these operations another writer acts (by default it commits one unrelated record).
        With role, only the origin writes of that role are met. Every commit entry of the store is wrapped, so a test
        does not depend on which of them a write uses."""
        act = act or self.commit_unrelated

        def wrap(original):
            def commit(store, operation, payload, apply, **kwargs):
                if operation in operations and (role is None or payload.get("roles") == [role]):
                    self.attempts[kwargs["request_id"]] = self.attempts.get(kwargs["request_id"], 0) + 1
                    act()
                return original(store, operation, payload, apply, **kwargs)
            return commit
        for name in COMMIT_ENTRIES:
            patch = mock.patch.object(Store, name, wrap(getattr(Store, name)))
            patch.start()
            self.addCleanup(patch.stop)

    def hold_write_lock(self):
        connection = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        self.addCleanup(connection.close)
        return connection

    def refuse_beyond_the_wait(self, operation, run):
        """Hold the write lock from the first commit of operation on, until run is refused; then release it."""
        held = []
        self.meet_commits((operation,), act=lambda: held or held.append(self.hold_write_lock()))
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01), \
                mock.patch.object(finishing.time, "monotonic", TickingClock()):
            with self.assertRaises(ResearchError) as caught:
                run()
        held[0].execute("ROLLBACK")
        return caught.exception

    def build_intent(self, identifier="probe-1", timeout=30):
        return {"id": identifier, "question": "What does the finished program show?", "interpreter": sys.executable,
                "program": self.artifacts.put(b"print(4)\n", "text/x-python"), "inputs": [], "timeout_seconds": timeout}

    def build_outcome(self, identifier="probe-1", **changes):
        return dict({"id": identifier, "command": "python example.py", "outputs": [],
                     "stdout": self.artifacts.put(b"4\n", "text/plain"), "stderr": self.artifacts.put(b"", "text/plain"),
                     "exit_status": 0, "elapsed_seconds": 0.25, "observation": "The finite result was 4."}, **changes)

    def launch_finished_program(self, identifier="probe-1"):
        """The records and the files of a launched probe whose program ended with status 0 and wrote its outputs."""
        self.mutate(probe.record_probe_intent, self.build_intent(identifier))
        directory = "research/probes/" + identifier + "/run-fixture"
        work = self.root / directory / "work"
        (work / ".probe-directory").mkdir(parents=True)
        config = {"id": identifier, "directory": directory, "argv": [sys.executable, "program/source.py"],
                  "command": "python program/source.py", "output_dir": ".", "timeout_seconds": 30,
                  "observation": None, "initial": {}, "owner": {"pid": 1, "token": "fixture"}}
        config_artifact = self.artifacts.put((json.dumps(config, sort_keys=True) + "\n").encode(), "application/json")
        launch = dict(config, config_artifact=config_artifact)
        self.mutate(probe.record_probe_launch, launch)
        (self.root / directory / "stdout").write_bytes(b"done\n")
        (self.root / directory / "stderr").write_bytes(b"")
        for index in range(OUTPUT_COUNT):
            (work / ("out%d.txt" % index)).write_text("output %d of the finished program" % index)
        terminal = {"exit_status": 0, "elapsed_seconds": 15.0, "config_sha256": config_artifact["sha256"]}
        (self.root / directory / "terminal.json").write_text(json.dumps(terminal))
        return launch, terminal

    def relaunch(self, identifier="probe-1"):
        script = self.root / "source.py"
        script.write_text("print(4)\n")
        return probe.launch_probe(self.store, identifier=identifier, question="What does the finished program show?",
                                  script=str(script), timeout=30)

    def read_probe(self, identifier="probe-1"):
        return self.store.snapshot()["records"].get("probe", {}).get(identifier)

    def read_wall_seconds(self):
        usage = resources.account_report(self.store.snapshot()["records"], "research")["experiment"]["wall_seconds"]
        return usage["reserved"], usage["charged"]

    def list_runs(self, identifier="probe-1"):
        return sorted((self.root / "research" / "probes" / identifier).glob("run-*"))


class ProbeCaptureContentionTests(ProbeContentionCase):
    """Incident: a program that exited with status 0 was recorded as failed, "Probe output capture failed:
    stale_revision", while the capture of a second probe committed its records."""

    def test_capture_keeps_every_output_when_a_commit_meets_every_attempt_of_an_output_pin(self):
        launch, terminal = self.launch_finished_program()
        self.meet_commits(("research.probe_artifact",), role="outputs")
        outcome = probe_worker.capture_probe_outcome(self.root, launch, terminal)
        # On 0.91.5 the outcome is failed with "Probe output capture failed: stale_revision" and no output.
        self.assertEqual((outcome["exit_status"], outcome["observation"], len(outcome["outputs"])),
                         (0, "Local probe exited with status 0.", OUTPUT_COUNT))
        self.assertNotIn("capture_failure", outcome)
        # Each output pin met one commit of the other writer and took its first attempt.
        self.assertEqual((self.unrelated_count, sorted(self.attempts.values())), (OUTPUT_COUNT, [1] * OUTPUT_COUNT))

    def test_capture_keeps_every_output_when_a_commit_meets_every_write_of_the_capture(self):
        launch, terminal = self.launch_finished_program()
        self.meet_commits(("research.probe_artifact",))
        # On 0.91.5 the first stream origin raises stale_revision after eight attempts.
        outcome = probe_worker.capture_probe_outcome(self.root, launch, terminal)
        self.assertEqual((outcome["exit_status"], len(outcome["outputs"])), (0, OUTPUT_COUNT))

    def test_launcher_records_the_program_status_when_commits_meet_its_capture(self):
        self.launch_finished_program()
        self.meet_commits(("research.probe_artifact", "research.probe"), role="outputs")
        receipt = self.relaunch()
        # On 0.91.5 the probe record is failed, and the same id returns that record for ever.
        self.assertEqual((receipt["result"]["exit_status"], len(receipt["result"]["outputs"])), (0, OUTPUT_COUNT))
        record = self.read_probe()
        self.assertEqual(record["revision"], receipt["revision"])
        self.assertEqual(self.relaunch(), receipt)

    def test_origin_write_takes_its_first_attempt_after_a_commit_of_another_writer(self):
        self.launch_finished_program()
        self.meet_commits(("research.probe_artifact",), act=lambda: self.unrelated_count or self.commit_unrelated())
        probe_provenance.record_probe_artifact_origin(self.root, "probe-1", "a" * 64, "outputs")
        # On 0.91.5 the first attempt is stale and the write needs a second one.
        self.assertEqual(self.attempts, {"probe-artifact:probe-1:" + "a" * 64 + ":outputs": 1})
        self.assertEqual(self.unrelated_count, 1)

    def test_intent_and_launch_are_recorded_when_a_commit_meets_every_attempt(self):
        self.meet_commits(("research.probe_intent", "research.probe_launch", "research.probe_recovery"))
        # On 0.91.5 each of these raises stale_revision after eight attempts.
        intent = probe.commit_probe_mutation(self.store, probe.record_probe_intent, self.build_intent(),
                                             "probe-intent:probe-1")
        self.assertEqual(intent["result"]["revision"], intent["revision"])
        launch = {"id": "probe-1", "directory": "research/probes/probe-1/run-fixture", "output_dir": ".", "initial": {}}
        probe.commit_probe_mutation(self.store, probe.record_probe_launch, launch, "probe-launch:probe-1")
        probe.commit_probe_mutation(self.store, probe.record_probe_recovery,
                                    {"id": "probe-1", "usage_incomplete": True, "owner_lost": True},
                                    "probe-recovery:probe-1")
        records = self.store.snapshot()["records"]
        self.assertEqual(records["probe_intent"]["probe-1"], intent["result"])
        self.assertEqual(records["probe_launch"]["probe-1"], launch)
        self.assertEqual(set(self.attempts.values()), {1})


class ProbeBusyStoreTests(ProbeContentionCase):
    def test_store_busy_through_nine_attempts_of_an_output_pin_is_waited_for(self):
        launch, terminal = self.launch_finished_program()
        held, calls = [], []

        def hold_through_nine_attempts():
            calls.append(True)
            if len(calls) == 1:
                held.append(self.hold_write_lock())
            elif len(calls) == 10:
                held[0].execute("ROLLBACK")
        self.meet_commits(("research.probe_artifact",), act=hold_through_nine_attempts, role="outputs")
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01):
            outcome = probe_worker.capture_probe_outcome(self.root, launch, terminal)
        # On 0.91.5 the outcome is failed with "Probe output capture failed: store_busy" after eight attempts.
        self.assertEqual((outcome["exit_status"], outcome["observation"], len(outcome["outputs"])),
                         (0, "Local probe exited with status 0.", OUTPUT_COUNT))

    def test_store_busy_beyond_the_wait_records_no_outcome_and_the_same_id_finishes(self):
        self.launch_finished_program()
        held = []
        self.meet_commits(("research.probe_artifact",), act=lambda: held or held.append(self.hold_write_lock()),
                          role="outputs")
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01), \
                mock.patch.object(finishing.time, "monotonic", TickingClock()):
            with self.assertRaises(ResearchError) as caught:
                self.relaunch()
        # On 0.91.5 the launcher records failed with "Probe output capture failed: store_busy".
        self.assertEqual(caught.exception.code, "store_busy")
        self.assertEqual(caught.exception.message, probe_provenance.PROBE_STORE_BUSY_MSG)
        self.assertEqual(caught.exception.details["cause"]["code"], "store_busy")
        held[0].execute("ROLLBACK")
        self.assertIsNone(self.read_probe())
        self.assertEqual(self.read_wall_seconds(), (30, 0))
        receipt = self.relaunch()
        self.assertEqual((receipt["result"]["exit_status"], len(receipt["result"]["outputs"])), (0, OUTPUT_COUNT))

    def test_intent_refused_beyond_the_wait_records_nothing_and_the_same_command_runs_the_program(self):
        refusal = self.refuse_beyond_the_wait("research.probe_intent", self.relaunch)
        # On 0.91.5 the refusal carries the message of the store, which does not say what the same command does.
        self.assertEqual((refusal.code, refusal.message), ("store_busy", probe_provenance.PROBE_INTENT_STORE_BUSY_MSG))
        self.assertEqual(refusal.details["cause"]["code"], "store_busy")
        records = self.store.snapshot()["records"]
        # No intent holds a reservation, and no run folder exists.
        self.assertEqual((records.get("probe_intent", {}), records.get("probe_launch", {}), self.list_runs()), ({}, {}, []))
        receipt = self.relaunch()
        self.assertEqual(receipt["result"]["exit_status"], 0)
        self.assertEqual(self.artifacts.read(receipt["result"]["stdout"]), b"4\n")
        self.assertEqual(len(self.list_runs()), 1)

    def test_launch_claim_refused_beyond_the_wait_keeps_the_intent_and_the_same_command_records_an_interruption(self):
        refusal = self.refuse_beyond_the_wait("research.probe_launch", self.relaunch)
        # On 0.91.5 the refusal carries the message of the store, which does not say what the same command does.
        self.assertEqual((refusal.code, refusal.message), ("store_busy", probe_provenance.PROBE_LAUNCH_STORE_BUSY_MSG))
        self.assertEqual(refusal.details["cause"]["code"], "store_busy")
        records = self.store.snapshot()["records"]
        self.assertEqual((list(records["probe_intent"]), records.get("probe_launch", {})), (["probe-1"], {}))
        self.assertEqual(self.read_wall_seconds(), (30, 0))
        [run] = self.list_runs()
        self.assertFalse((run / "stdout").exists())
        receipt = self.relaunch()
        self.assertEqual((receipt["result"]["exit_status"], receipt["result"]["outputs"]), ("interrupted", []))
        self.assertEqual((self.list_runs(), (run / "stdout").exists()), ([run], False))
        self.assertEqual(self.read_wall_seconds(), (0, 0))
        self.assertEqual(self.relaunch("probe-2")["result"]["exit_status"], 0)

    def test_probe_write_waits_with_the_values_of_a_review_call_finish(self):
        self.assertEqual((finishing.SCOPED_FINISH_ATTEMPT_COUNT, finishing.SCOPED_FINISH_WAIT_SECONDS,
                          finishing.SCOPED_FINISH_RETRY_CODES),
                         (600, 600.0, ("store_busy", "stale_revision", "review_task_dependency_changed")))
        for code in finishing.SCOPED_FINISH_RETRY_CODES + ("probe_intent_missing",):
            with self.subTest(code=code):
                calls = []

                def refuse():
                    calls.append(True)
                    raise ResearchError(code, "Fixture refusal")
                with self.assertRaises(ResearchError) as caught:
                    finishing.retry_finishing_write(refuse, probe_provenance.PROBE_STORE_BUSY_MSG)
                if code == "probe_intent_missing":
                    self.assertEqual((caught.exception.code, len(calls)), (code, 1))
                else:
                    self.assertEqual((caught.exception.code, caught.exception.details["cause"]["code"], len(calls)),
                                     ("store_busy", code, finishing.SCOPED_FINISH_ATTEMPT_COUNT))


class ProbeWriteDisciplineTests(ProbeContentionCase):
    """What the commit of a probe record still depends on, and what the commands keep."""

    def test_second_intent_is_refused_when_another_intent_took_the_allowance_before_its_commit(self):
        self.mutate(resources.set_budget, {"profile": "research", "purpose": "experiment", "reason": "Bound the time.",
                    "limits": {unit: 40 if unit == "wall_seconds" else None for unit in resources.UNITS}})
        rival = self.build_intent("rival")
        taken = []

        def take_allowance():
            if not taken:
                taken.append(True)
                other = Store(self.root)
                probe.record_probe_intent(other, rival, expected_revision=other.revision, request_id="probe-intent:rival")
        self.meet_commits(("research.probe_intent",), act=take_allowance)
        with self.assertRaises(ResearchError) as caught:
            probe.commit_probe_mutation(self.store, probe.record_probe_intent, self.build_intent(), "probe-intent:probe-1")
        self.assertEqual(caught.exception.code, "resource_budget_exhausted")
        self.assertEqual(sorted(self.store.snapshot()["records"]["probe_intent"]), ["rival"])

    def test_commands_keep_the_compare_and_swap_on_the_revision(self):
        for command, payload in (("probe-intent", self.build_intent()), ("probe", self.build_outcome())):
            with self.subTest(command=command):
                revision = self.store.revision
                self.commit_unrelated()
                with self.assertRaises(ResearchError) as caught:
                    cli.OPERATIONS[command](self.store, payload, expected_revision=revision, request_id="stale-" + command)
                self.assertEqual(caught.exception.code, "stale_revision")
                receipt = self.mutate(cli.OPERATIONS[command], payload)
                self.assertEqual(receipt["result"]["revision"], receipt["revision"])

    def test_record_of_a_complete_capture_has_the_members_of_0_91_4(self):
        self.launch_finished_program()
        receipt = self.relaunch()
        self.assertEqual(sorted(receipt["result"]), PROBE_RECORD_MEMBERS_0_91_4)
        self.assertEqual(sorted(self.store.snapshot()["records"]["probe_intent"]["probe-1"]),
                         ["id", "inputs", "interpreter", "program", "question", "revision", "timeout_seconds"])

    def test_request_that_one_commit_recorded_is_replayed_by_the_other(self):
        intent = self.build_intent()
        receipt = probe.record_probe_intent(self.store, intent, expected_revision=self.store.revision,
                                            request_id="probe-intent:probe-1")
        revision = self.store.revision
        self.assertEqual(probe.commit_probe_mutation(self.store, probe.record_probe_intent, intent,
                                                     "probe-intent:probe-1"), receipt)
        # The origin record as 0.91.4 commits it: the compare-and-swap on the revision of the store.
        identifier = "probe-1:" + "b" * 64 + ":outputs"
        payload = {"probe_id": "probe-1", "sha256": "b" * 64, "roles": ["outputs"]}
        origin = prepared_mutation(self.store, "research.probe_artifact", payload,
                                   lambda records, value: ([immutable_record(records, "probe_artifact", identifier, value)],
                                                           value),
                                   expected_revision=self.store.revision, request_id="probe-artifact:" + identifier)
        revision = self.store.revision
        self.assertEqual(probe_provenance.record_probe_artifact_origin(self.root, "probe-1", "b" * 64, "outputs"), origin)
        self.assertEqual(self.store.revision, revision)


class ProbeEarlierReleaseTests(ProbeContentionCase):
    def write_outcome_file(self, launch, observation):
        """The outcome.json that a worker of 0.91.4 leaves for a capture that it gave up."""
        outcome = {"id": launch["id"], "command": launch["command"], "outputs": [],
                   "stdout": self.artifacts.put(b"done\n", "text/plain"), "stderr": self.artifacts.put(b"", "text/plain"),
                   "exit_status": "failed", "elapsed_seconds": 15.0, "observation": observation}
        for name in ("stdout", "stderr"):
            probe_provenance.record_probe_artifact_origin(self.root, launch["id"], outcome[name]["sha256"], name)
        (self.root / launch["directory"] / "outcome.json").write_text(json.dumps(
            {"config_sha256": launch["config_artifact"]["sha256"], "outcome": outcome}))

    def test_outcome_file_of_a_refused_store_is_captured_again_from_the_terminal_evidence(self):
        for observation in probe.REFUSED_STORE_OBSERVATIONS:
            with self.subTest(observation=observation):
                identifier = "probe-" + observation.rsplit(" ", 1)[1].replace("_", "-")
                launch, _ = self.launch_finished_program(identifier)
                self.write_outcome_file(launch, observation)
                receipt = self.relaunch(identifier)
                # On 0.91.5 the launcher records the stored failure.
                self.assertEqual((receipt["result"]["exit_status"], len(receipt["result"]["outputs"])), (0, OUTPUT_COUNT))

    def test_outcome_file_of_an_unsafe_output_is_recorded_as_it_stands(self):
        launch, _ = self.launch_finished_program()
        self.write_outcome_file(launch, "Probe output capture failed: unsafe_path")
        receipt = self.relaunch()
        self.assertEqual((receipt["result"]["exit_status"], receipt["result"]["observation"], receipt["result"]["outputs"]),
                         ("failed", "Probe output capture failed: unsafe_path", []))
        self.assertNotIn("capture_failure", receipt["result"])

    def test_stored_probe_record_of_0_91_4_is_delivered_unchanged_in_a_problem_packet(self):
        stored = dict(self.build_outcome(exit_status="failed", observation="Probe output capture failed: stale_revision"),
                      revision=7)
        current = dict(self.build_outcome("probe-2", exit_status="failed",
                                          observation="Probe output capture failed: unsafe_path"),
                       capture_failure={"code": "unsafe_path", "program_exit_status": 0}, revision=9)
        records = {"probe": {"probe-1": stored, "probe-2": current}}
        accounts = {"answer": {"evidence": []}, "relations": [], "refuted_conjectures": [],
                    "checks": [{"evidence": [{"kind": "probe", "id": "probe-1"}, {"kind": "probe", "id": "probe-2"}]}]}
        delivered = describe_account_records(records, accounts, accounts_version=2)
        self.assertEqual(delivered, [{"record": stored, "cite": cite_record("probe", "probe-1", stored)},
                                     {"record": current, "cite": cite_record("probe", "probe-2", current)}])
        self.assertEqual(sorted(delivered[0]["record"]), PROBE_RECORD_MEMBERS_0_91_4)


class ProbeCaptureFailureTests(ProbeContentionCase):
    def test_pin_that_the_artifact_store_refuses_raises_and_records_no_outcome(self):
        from research_harness.artifacts import ArtifactStore
        launch, terminal = self.launch_finished_program()
        original = ArtifactStore.put

        def refuse_outputs(artifacts, data, media_type):
            if data.startswith(b"output "):
                raise OSError(28, "No space left on device")
            return original(artifacts, data, media_type)
        with mock.patch.object(ArtifactStore, "put", refuse_outputs):
            # On 0.91.5 the capture returns failed with the text of the operating-system error.
            with self.assertRaises(OSError):
                probe_worker.capture_probe_outcome(self.root, launch, terminal)
            with self.assertRaises(OSError):
                self.relaunch()
        self.assertIsNone(self.read_probe())
        self.assertEqual(self.relaunch()["result"]["exit_status"], 0)

    def test_unreadable_output_gives_the_code_storage_io(self):
        launch, terminal = self.launch_finished_program()
        original = probe_worker.read_file

        def refuse_one(root, relative):
            if relative.endswith("out3.txt"):
                raise OSError(13, "Permission denied")
            return original(root, relative)
        with mock.patch.object(probe_worker, "read_file", refuse_one):
            outcome = probe_worker.capture_probe_outcome(self.root, launch, terminal)
        self.assertEqual(outcome["capture_failure"], {"code": "storage_io", "program_exit_status": 0})
        self.assertEqual((outcome["exit_status"], len(outcome["outputs"])), ("failed", 3))

    def test_unsafe_output_keeps_the_exit_status_of_the_program_beside_the_failed_capture(self):
        launch, terminal = self.launch_finished_program()
        (self.root / launch["directory"] / "work" / "zz-link").symlink_to(self.root / "outside")
        outcome = probe_worker.capture_probe_outcome(self.root, launch, terminal)
        self.assertEqual((outcome["exit_status"], outcome["observation"]),
                         ("failed", "Probe output capture failed: unsafe_path"))
        # On 0.91.5 the outcome has no member that holds the status of the program.
        self.assertEqual(outcome["capture_failure"], {"code": "unsafe_path", "program_exit_status": 0})
        self.assertEqual(len(outcome["outputs"]), OUTPUT_COUNT)
        receipt = self.relaunch()
        self.assertEqual(receipt["result"]["capture_failure"], {"code": "unsafe_path", "program_exit_status": 0})

    def test_outcome_validation_takes_a_capture_failure_and_refuses_another_shape(self):
        self.mutate(probe.record_probe_intent, self.build_intent())
        outcome = self.build_outcome(exit_status="failed", observation="Probe output capture failed: unsafe_path")
        for invalid in ({"code": "unsafe_path"}, {"code": "", "program_exit_status": 0},
                        {"code": "unsafe_path", "program_exit_status": "unknown"},
                        {"code": "unsafe_path", "program_exit_status": 0, "extra": 1}):
            with self.assertRaises(ResearchError) as caught:
                self.mutate(cli.OPERATIONS["probe"], dict(outcome, capture_failure=invalid))
            self.assertEqual(caught.exception.code, "invalid_input")
        saved = self.mutate(cli.OPERATIONS["probe"],
                            dict(outcome, capture_failure={"code": "unsafe_path", "program_exit_status": 3}))
        self.assertEqual(saved["result"]["capture_failure"]["program_exit_status"], 3)

    def test_inventory_row_of_a_disclosed_run_holds_capture_failure_only_when_its_outcome_does(self):
        context = {"cycle_id": "cycle-1", "program_path": "experiment/code/cycle.py", "read_outputs": []}
        run = {"started_at": "2026-11-19T00:00:00Z", "ended_at": "2026-11-19T00:00:15Z", "outputs": [],
               "read_outputs": [], "outputs_unread": True}
        failure = {"code": "unsafe_path", "program_exit_status": 0}
        records = {"probe_intent": {}, "probe": {}}
        for identifier, changes in (("complete", {}), ("unsafe", {"capture_failure": failure})):
            records["probe_intent"][identifier] = dict(self.build_intent(identifier), program_run=context)
            records["probe"][identifier] = dict(self.build_outcome(identifier), program_run=run, **changes)
        plan = {"payload": {"prior_knowledge": {"probe_ids": ["complete", "unsafe"]}}}
        inventory = program_probes.build_program_probe_inventory(records, self.artifacts, plan, {})
        self.assertNotIn("capture_failure", inventory["complete"])
        self.assertEqual(sorted(inventory["complete"]),
                         ["admitted_runs", "context", "elapsed_seconds", "exit_status", "id", "inputs", "interpreter",
                          "outputs", "program", "program_run", "statement", "stderr", "stdout"])
        # On 0.91.5 the row leaves out the exit status of the program.
        self.assertEqual(inventory["unsafe"], dict(inventory["complete"], id="unsafe", capture_failure=failure))


if __name__ == "__main__":
    import unittest
    unittest.main()
