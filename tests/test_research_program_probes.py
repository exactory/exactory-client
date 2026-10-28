"""Pre-plan observations remain auditable without exposing sealed completion outputs."""

import copy
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys

from development_fixtures import DevelopmentCase, PROGRAM
from literature_fixtures import LiteratureCase
from research_harness import cli, probe
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.principles import initialize_research
from research_harness.review_delivery import deliver_packet, deliver_readiness, references
from research_harness.source_links import read_locator
from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome


START = "2026-10-24T16:00:00Z"
END = "2026-10-24T16:00:01Z"
OLD_PROGRAM = b"print('earlier construction')\n"


class ProgramProbeCase:
    def build_run_context(self, cycle_id="cycle-1", read=()):
        return {"cycle_id": cycle_id, "program_path": "experiment/code/cycle.py", "read_outputs": list(read)}

    def record_program_probe(self, identifier="check-1", *, program=OLD_PROGRAM, read=(), cycle_id="cycle-1"):
        intent = build_probe_intent(self.artifacts, identifier)
        intent.update(program=self.artifacts.put(program, "text/x-python"),
                      program_run=self.build_run_context(cycle_id, read))
        try:
            self.mutate(probe.record_probe_intent, intent)
        except ResearchError as error:
            self.fail("Program-run intent must retain its cycle and read policy: " + error.code)
        sealed = not read
        put = getattr(self.artifacts, "put_sealed", None) if sealed else self.artifacts.put
        self.assertTrue(callable(put), "Unread outputs need a separate sealed artifact namespace")
        output = put(b'{"value": 7}\n', "application/json")
        stdout = put(b"validation: passed\n", "text/plain")
        stderr = put(b"", "text/plain")
        named = [{"path": "results/value.json", "sha256": output["sha256"]},
                 {"path": "stdout", "sha256": stdout["sha256"]},
                 {"path": "stderr", "sha256": stderr["sha256"]}]
        outcome = dict(build_probe_outcome(self.artifacts, identifier), outputs=[output], stdout=stdout, stderr=stderr,
                       program_run={"started_at": START, "ended_at": END, "outputs": named,
                                    "read_outputs": [item for item in named if item["path"] in read],
                                    "outputs_unread": sealed})
        try:
            receipt = self.mutate(probe.record_probe, outcome)
        except ResearchError as error:
            self.fail("Program-run outcome must retain output names and read hashes: " + error.code)
        return intent, outcome, receipt

    def disclose(self, plan, ids, program=PROGRAM):
        return dict(plan, prior_knowledge={"program_path": "experiment/code/cycle.py",
                    "program": self.artifacts.put(program, "text/x-python"), "probe_ids": ids,
                    "account": "These runs were known before this plan; retain each construction and read output."})


class ProgramProbeRecordingTests(ProgramProbeCase, LiteratureCase):
    def setUp(self):
        super().setUp()
        self.mutate(initialize_research, {"profile": "research", "target": None})

    def test_sealed_objects_cannot_be_read_as_ordinary_artifacts_or_source_passages(self):
        put = getattr(self.artifacts, "put_sealed", None)
        self.assertTrue(callable(put), "Unread captures need sealed object publication")
        sealed = put(b"unread scientific result", "text/plain")
        self.assertTrue((self.root / sealed["path"]).is_file())
        for read in (lambda: self.artifacts.read(sealed),
                     lambda: read_locator(self.artifacts, sealed,
                                          {"kind": "text", "start": 0, "end": 6, "quote": "unread"})):
            self.assert_error("artifact_sealed", read)
        ordinary = dict(sealed, path="research/sources/objects/" + sealed["sha256"])
        self.assert_error("artifact_missing", lambda: self.artifacts.read(ordinary))
        self.assert_error("artifact_sealed", lambda: self.mutate(cli.OPERATIONS["artifact"],
            {"id": "unseal", "path": sealed["path"], "media_type": "text/plain"}))

    def test_unread_outcome_retains_hashes_statement_and_original_receipt(self):
        intent, outcome, receipt = self.record_program_probe()
        saved = receipt["result"]
        self.assertTrue(saved["program_run"]["outputs_unread"])
        self.assertEqual(saved["program_run"]["read_outputs"], [])
        self.assertEqual(saved["program_run"]["outputs"][0],
                         {"path": "results/value.json", "sha256": hashlib.sha256(b'{"value": 7}\n').hexdigest()})
        self.assertEqual((saved["program_run"]["started_at"], saved["program_run"]["ended_at"]), (START, END))
        self.assertEqual(self.store.snapshot()["records"]["probe_intent"]["check-1"]["program"], intent["program"])
        self.assertEqual(self.mutate(probe.record_probe, outcome)["result"], saved)
        self.assert_error("artifact_sealed", lambda: self.artifacts.read(saved["stdout"]))

    def test_observed_outputs_are_named_by_path_and_hash_including_stdout(self):
        _, outcome, receipt = self.record_program_probe(read=("stdout", "results/value.json"))
        saved = receipt["result"]["program_run"]
        self.assertFalse(saved["outputs_unread"])
        self.assertEqual(saved["read_outputs"], outcome["program_run"]["outputs"][:2])
        self.assertEqual(self.artifacts.read(outcome["stdout"]), b"validation: passed\n")

    def test_an_existing_legacy_intent_cannot_gain_a_program_run_context(self):
        intent = build_probe_intent(self.artifacts)
        self.mutate(probe.record_probe_intent, intent)
        before = self.store.snapshot()
        self.assert_error("record_conflict", lambda: self.mutate(probe.record_probe_intent,
            dict(intent, program_run=self.build_run_context(read=("stdout",)))))
        self.assertEqual(self.store.snapshot(), before)

    def test_invalid_observed_output_hashes_paths_and_times_do_not_commit(self):
        _, outcome, _ = self.record_program_probe(read=("stdout",))
        for changes in ({"started_at": END, "ended_at": START},
                        {"read_outputs": [{"path": "stdout", "sha256": "0" * 64}]},
                        {"outputs_unread": True},
                        {"outputs": [{"path": "../escape", "sha256": outcome["stdout"]["sha256"]}]}):
            invalid = copy.deepcopy(outcome)
            invalid["program_run"].update(changes)
            before = self.store.snapshot()
            self.assert_error("invalid_input", lambda: self.mutate(probe.record_probe, invalid))
            self.assertEqual(self.store.snapshot(), before)

    def test_packets_skip_sealed_outputs_even_when_nested_in_delivered_json(self):
        _, outcome, _ = self.record_program_probe()
        wrapper = self.artifacts.put(json.dumps({"output": outcome["outputs"][0]}).encode(), "application/json")
        manifest = {"kind": "readiness", "probe": outcome, "wrapper": wrapper}
        self.assertNotIn(outcome["outputs"][0], references(manifest))
        destination = self.root / "reviewer"
        delivered = deliver_packet(self.store, destination, manifest, transitive=True)
        self.assertIn(wrapper, delivered["artifacts"])
        for descriptor in [*outcome["outputs"], outcome["stdout"], outcome["stderr"]]:
            self.assertFalse((destination / descriptor["path"]).exists())
        self.assertTrue(json.loads((destination / "inputs.json").read_text())["probe"]["program_run"]["outputs_unread"])

    def test_recovery_without_a_launch_retains_unknown_times_and_the_unread_policy(self):
        intent = dict(build_probe_intent(self.artifacts), program_run=self.build_run_context())
        try:
            saved = self.mutate(probe.record_probe_intent, intent)["result"]
        except ResearchError as error:
            self.fail("Program-run recovery needs the recorded context: " + error.code)
        with probe.hold_probe_owner(self.root, intent["id"]):
            result = probe.reconcile_probe(self.store, saved, None)["result"]
        self.assertEqual(result["exit_status"], "interrupted")
        self.assertIsNone(result["program_run"]["started_at"])
        self.assertIsNone(result["program_run"]["ended_at"])
        self.assertTrue(result["program_run"]["outputs_unread"])
        self.assert_error("artifact_sealed", lambda: self.artifacts.read(result["stdout"]))

    def test_managed_capture_seals_completion_files_and_replays_without_running_again(self):
        self.assertIn("program_run", inspect.signature(probe.launch_probe).parameters,
                      "The managed launcher needs a program-run policy")
        script = self.root / "program.py"
        script.write_text("from pathlib import Path\nPath('value.txt').write_text('not read')\nprint('not read')\n")
        arguments = dict(identifier="managed", question="Does this program complete?", script=script,
                         timeout=10, program_run=self.build_run_context())
        receipt = probe.launch_probe(self.store, **arguments)
        outcome = receipt["result"]
        self.assertTrue(outcome["program_run"]["outputs_unread"])
        self.assertIn("work/value.txt", [item["path"] for item in outcome["program_run"]["outputs"]])
        self.assertEqual(probe.launch_probe(self.store, **arguments), receipt)
        for descriptor in [*outcome["outputs"], outcome["stdout"], outcome["stderr"]]:
            self.assert_error("artifact_sealed", lambda: self.artifacts.read(descriptor))
        launch = self.store.snapshot()["records"]["probe_launch"]["managed"]
        self.assert_error("artifact_sealed", lambda: self.mutate(cli.OPERATIONS["artifact"],
            {"id": "leak", "path": launch["directory"] + "/work/value.txt", "media_type": "text/plain"}))

    def test_output_files_named_stdout_and_stderr_are_distinct_from_the_streams(self):
        self.assertIn("program_run", inspect.signature(probe.launch_probe).parameters)
        script = self.root / "streams.py"
        script.write_text("from pathlib import Path\nPath('stdout').write_text('file output')\n"
                          "Path('stderr').write_text('file error')\nprint('stream output')\n")
        receipt = probe.launch_probe(self.store, identifier="stream-names", question="Which outputs were read?",
            script=script, timeout=10, program_run=self.build_run_context(read=("stdout", "work/stdout")))
        run = receipt["result"]["program_run"]
        self.assertEqual({item["path"] for item in run["outputs"]},
                         {"stdout", "stderr", "work/stdout", "work/stderr"})
        self.assertEqual({item["path"]: item["sha256"] for item in run["read_outputs"]},
                         {"stdout": hashlib.sha256(b"stream output\n").hexdigest(),
                          "work/stdout": hashlib.sha256(b"file output").hexdigest()})

    def test_the_launcher_uses_the_committed_read_policy_after_the_caller_changes_its_context(self):
        from unittest import mock
        self.assertIn("program_run", inspect.signature(probe.launch_probe).parameters)
        script = self.root / "frozen.py"
        script.write_text("print('unread output')\n")
        context = self.build_run_context()
        commit = probe.commit_probe_mutation
        def change_caller_context(store, operation, payload, request_id):
            receipt = commit(store, operation, payload, request_id)
            if operation is probe.record_probe_intent:
                context["read_outputs"].append("stdout")
            return receipt
        with mock.patch.object(probe, "commit_probe_mutation", side_effect=change_caller_context):
            receipt = probe.launch_probe(self.store, identifier="frozen", question="Does it complete?",
                script=script, timeout=10, program_run=context)
        self.assertEqual(self.store.snapshot()["records"]["probe_intent"]["frozen"]["program_run"]["read_outputs"], [])
        self.assertTrue(receipt["result"]["program_run"]["outputs_unread"])
        self.assert_error("artifact_sealed", lambda: self.artifacts.read(receipt["result"]["stdout"]))

    def test_lab_cli_records_an_observed_stdout_with_the_program_context(self):
        script = self.root / "example.py"
        script.write_text("print('validation passed')\n")
        executable = Path(__file__).resolve().parents[1] / "bin/exactory-lab"
        run = subprocess.run([sys.executable, str(executable), "probe", "--id", "cli-run", "--question", "What ran?",
                              "--script", str(script), "--timeout", "10", "--cycle-id", "cycle-1",
                              "--program-path", "experiment/code/cycle.py", "--read-output", "stdout"],
                             cwd=self.root, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)["result"]
        self.assertEqual(result["program_run"]["read_outputs"],
                         [{"path": "stdout", "sha256": hashlib.sha256(b"validation passed\n").hexdigest()}])


class ProgramProbePlanTests(ProgramProbeCase, DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()

    def test_plan_omitting_an_observed_run_is_refused_with_its_id(self):
        self.record_program_probe(read=("stdout",))
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.mutate(self.development().plan_cycle, self.plan())
        self.assertEqual(caught.exception.code, "preplan_probe_missing")
        self.assertIn("check-1", json.dumps(caught.exception.as_dict()))
        self.assertEqual(self.store.snapshot(), before)

    def test_plan_omitting_a_completion_check_is_refused_with_its_id(self):
        self.record_program_probe()
        self.assert_error("preplan_probe_missing", lambda: self.mutate(self.development().plan_cycle, self.plan()))

    def test_plan_requires_all_versions_of_the_program_even_for_other_cycle_ids(self):
        self.record_program_probe("old", cycle_id="old-cycle", read=("stdout",))
        self.record_program_probe("current", program=PROGRAM, read=("results/value.json",))
        plan = self.disclose(self.plan(), ["current"])
        self.assert_error("preplan_probe_missing", lambda: self.mutate(self.development().plan_cycle, plan))
        saved = self.mutate(self.development().plan_cycle, self.disclose(plan, ["old", "current"]))["result"]
        self.assertEqual(saved["payload"]["prior_knowledge"]["probe_ids"], ["old", "current"])

    def test_admission_refuses_a_different_program_from_the_frozen_account(self):
        self.record_program_probe(read=("stdout",))
        self.mutate(self.development().plan_cycle, self.disclose(self.plan(), ["check-1"]))
        self.assert_error("preplan_program_mismatch", lambda: self.admit(program_data=OLD_PROGRAM))

    def test_admission_cannot_omit_an_identical_program_run_under_another_cycle_id(self):
        self.record_program_probe(cycle_id="earlier-cycle", program=PROGRAM, read=("stdout",))
        self.mutate(self.development().plan_cycle, self.plan())
        self.assert_error("preplan_probe_missing", self.admit)

    def test_plan_cannot_freeze_while_a_declared_program_probe_is_pending(self):
        intent = dict(build_probe_intent(self.artifacts), program_run=self.build_run_context(read=("stdout",)))
        try:
            self.mutate(probe.record_probe_intent, intent)
        except ResearchError as error:
            self.fail("Pending program-run context must be recordable: " + error.code)
        self.assert_error("preplan_probe_pending", lambda: self.mutate(self.development().plan_cycle, self.plan()))

    def test_a_new_observed_admitted_run_can_reproduce_sealed_output_bytes(self):
        from integration_fixtures import admit_lab
        from research_harness.execution import launch_execution
        self.assertIn("program_run", inspect.signature(probe.launch_probe).parameters)
        body = "print('{\"metric\": 7}')\n"
        script = self.root / "check.py"
        script.write_text(body)
        receipt = probe.launch_probe(self.store, identifier="sealed-check", question="Does the program complete?",
                                     script=script, timeout=10, program_run=self.build_run_context())
        sealed = receipt["result"]["stdout"]
        admission = admit_lab(self, body=body, timeout=10,
                              plan=self.disclose(self.plan(), ["sealed-check"], body.encode()))
        result = launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                  request_id="observed-rerun")
        self.assertTrue(result["ok"])
        records = self.store.snapshot()["records"]
        run = records["execution"][records["execution_outcome"][admission["id"]]["execution_id"]]
        output = run["payload"]["outputs"][0]["artifact"]
        self.assertEqual(output["sha256"], sealed["sha256"])
        self.assertNotIn("probe_origins", run)
        self.assertEqual(self.artifacts.read(output), b'{"metric": 7}\n')
        self.assert_error("artifact_sealed", lambda: self.artifacts.read(sealed))

    def test_readiness_exports_each_probe_beside_admission_with_program_diff_and_output_hashes(self):
        self.record_program_probe("completion")
        self.record_program_probe("observed", program=PROGRAM, read=("stdout",))
        plan, execution = self.run_cycle(self.disclose(self.plan(), ["completion", "observed"]))
        self.mutate(self.development().assess_cycle, self.assessment(plan, execution))
        self.save_checkpoint()
        report = self.development().readiness_report(self.store)
        inventory = report["review_inputs"]["branches"]["cycle-1"]["program_probes"]
        self.assertEqual(set(inventory), {"completion", "observed"})
        earlier = inventory["completion"]["admitted_runs"][0]
        self.assertEqual((earlier["admission_id"], earlier["earlier_version"]), ("run-1", True))
        self.assertIn("-print('earlier construction')", earlier["program_diff"])
        self.assertFalse(inventory["observed"]["admitted_runs"][0]["earlier_version"])
        self.assertEqual(inventory["observed"]["program_run"]["read_outputs"],
                         [{"path": "stdout", "sha256": hashlib.sha256(b"validation: passed\n").hexdigest()}])
        destination = self.root / "independent"
        delivered = deliver_readiness(self.store, destination)
        saved = json.loads((destination / "inputs.json").read_text())["inputs"]["branches"]["cycle-1"]
        self.assertEqual(set(saved["program_probes"]), {"completion", "observed"})
        completion = self.store.snapshot()["records"]["probe"]["completion"]
        for ref in [*completion["outputs"], completion["stdout"], completion["stderr"]]:
            self.assertNotIn(ref, delivered["artifacts"])
            self.assertFalse((destination / ref["path"]).exists())
        from research_harness.review_packets import readiness_packet
        from research_harness.review_protocol import _evidence
        from research_harness.execution_evidence import author_readiness_state
        author_report = author_readiness_state(self.store.snapshot()["records"], self.artifacts)
        supplied = _evidence(self.artifacts, readiness_packet(author_report), transitive=True)
        self.assertFalse(any(item["artifact"]["path"] == completion["stdout"]["path"] for item in supplied))

    def test_a_frozen_plan_cannot_gain_undeclared_program_runs(self):
        self.mutate(self.development().plan_cycle, self.plan())
        intent = dict(build_probe_intent(self.artifacts), program_run=self.build_run_context())
        self.assert_error("preplan_cycle_frozen", lambda: self.mutate(probe.record_probe_intent, intent))

    def test_native_export_keeps_unread_metadata_without_delivering_the_output_objects(self):
        from research_harness.native_export import export_native
        _, outcome, _ = self.record_program_probe()
        attack = self.root / "attack"
        attack.mkdir()
        destination = attack / "review"
        exported = export_native(self.store, attack, destination)
        data = json.loads((destination / "snapshot.json").read_text())
        self.assertTrue(data["records"]["probe"]["check-1"]["program_run"]["outputs_unread"])
        paths = {item["path"] for item in exported["inputs"]}
        for ref in [*outcome["outputs"], outcome["stdout"], outcome["stderr"]]:
            self.assertNotIn("review/artifacts/" + ref["sha256"], paths)

    def test_legacy_probe_plan_admission_and_readiness_stay_unchanged(self):
        self.mutate(probe.record_probe_intent, build_probe_intent(self.artifacts))
        old = self.mutate(probe.record_probe, build_probe_outcome(self.artifacts))["result"]
        plan, execution = self.run_cycle()
        self.mutate(self.development().assess_cycle, self.assessment(plan, execution))
        self.save_checkpoint()
        before = self.development().readiness_report(self.store)
        self.assertNotIn("program_probes", before["review_inputs"]["branches"]["cycle-1"])
        self.assertEqual(self.mutate(probe.record_probe, build_probe_outcome(self.artifacts))["result"], old)
        after = self.development().readiness_report(self.store)
        self.assertEqual({key: value for key, value in after.items() if key != "revision"},
                         {key: value for key, value in before.items() if key != "revision"})
