"""Supported research preparation, state and recovery commands."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from research_harness.storage import Store
from development_fixtures import DevelopmentCase


PLUGIN = Path(__file__).resolve().parents[1]
RESEARCH_RUNS = Path("/Users/ryshiro/exactory/exactory-research")


class ResearchCliTests(unittest.TestCase):
    def setUp(self):
        # Use the designated research workspace when it exists; Linux tests use
        # their normal temporary directory without introducing a host dependency.
        directory = str(RESEARCH_RUNS) if RESEARCH_RUNS.is_dir() else None
        self.temporary = tempfile.TemporaryDirectory(prefix="research-cli-", dir=directory)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_cli(self, name, *args):
        return subprocess.run([sys.executable, str(PLUGIN / "bin" / name), *args],
                              cwd=self.root, capture_output=True, text=True,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def init_lab(self):
        result = self.run_cli("exactory-lab", "init", "--slug", "bounded")
        self.assertEqual(result.returncode, 0, result.stderr)

    def research_mutation(self, command, payload, revision, request_id):
        path = self.root / (request_id + ".json")
        path.write_text(json.dumps(payload))
        return self.run_cli("exactory-research", command, "--file", str(path),
                            "--expected-revision", str(revision), "--request-id", request_id)

    def test_initialization_creates_a_pending_current_contract(self):
        self.init_lab()
        self.assertTrue((self.root / ".exactory/research.sqlite3").is_file())
        records = Store(self.root).snapshot()["records"]
        self.assertEqual(records["configuration"]["research"]["profile"], "research")
        self.assertIsNone(records["configuration"]["research"]["target"])

    def test_status_and_receipts_carry_runtime_provenance(self):
        self.init_lab()
        status = json.loads(self.run_cli("exactory-research", "status").stdout)
        self.assertEqual(status["runtime"]["schema_version"], 1)
        self.assertEqual(len(status["runtime"]["package_digest"]), 64)
        records = Store(self.root).snapshot()["records"]
        receipt = next(iter(records["literature_operation"].values()))
        self.assertEqual(receipt["runtime"]["plugin_version"], status["runtime"]["plugin_version"])

    def test_compact_reports_are_opt_in_and_read_only(self):
        self.init_lab()
        before = Store(self.root).snapshot()
        for command, limit in (("status", 16 * 1024), ("next", 4 * 1024)):
            legacy = self.run_cli("exactory-research", command)
            compact = self.run_cli("exactory-research", command, "--summary")
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertEqual(compact.returncode, 0, compact.stderr)
            original = json.loads(legacy.stdout)
            view = json.loads(compact.stdout)
            self.assertEqual(view["revision"], original["revision"])
            self.assertEqual(view["ready"], original["ready"])
            self.assertEqual(view["preparation_ready"], original["preparation"]["ready"])
            self.assertTrue(view["advisory_only"])
            self.assertNotIn("schema", original)
            self.assertLessEqual(len(compact.stdout.encode("utf-8")), limit)
        status = json.loads(self.run_cli("exactory-research", "status", "--summary").stdout)
        self.assertEqual(status["runtime"]["schema_version"], 1)
        self.assertGreaterEqual(status["evaluation"]["computed"], 1)
        code = status["next_hint"]["code"]
        page = self.run_cli("exactory-research", "obligations", "--code", code, "--limit", "1")
        self.assertEqual(page.returncode, 0, page.stderr)
        listing = json.loads(page.stdout)
        self.assertEqual(listing["returned"], 1)
        self.assertEqual(listing["obligations"][0]["code"], code)
        stale = self.run_cli("exactory-research", "obligations", "--code", code, "--cursor", "999:0")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("stale_cursor", stale.stderr)
        self.assertEqual(Store(self.root).snapshot(), before)

    def test_lab_init_records_the_chosen_preparation_policy(self):
        result = self.run_cli("exactory-lab", "init", "--slug", "screened", "--preparation-policy", "screened-v1")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = Store(self.root).snapshot()["records"]["configuration"]["research"]
        self.assertEqual(config["preparation_policy"], {"id": "screened-v1"})

    def test_fresh_verifier_can_acquire_before_exact_target_initialization(self):
        from research_fixtures import atom, entry
        pending = self.research_mutation("acquire", {"identifier": "arxiv:2601.00001v1", "max_requests": 0}, 0, "acquire-first")
        self.assertEqual(pending.returncode, 0, pending.stderr)
        self.assertEqual(json.loads(pending.stdout)["status"], "pending")
        store = Store(self.root)
        self.assertNotIn("configuration", store.snapshot()["records"])
        self.assertNotIn("source", store.snapshot()["records"])
        metadata = self.root / "metadata.xml"
        metadata.write_bytes(atom([entry()], total=1))
        imported = self.research_mutation("import-response", {"provider": "arxiv", "response_file": str(metadata),
            "source_url": "https://export.arxiv.org/api/query?id_list=2601.00001v1",
            "captured_at": "2026-09-07T00:00:00Z"}, store.revision, "exact-metadata")
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(json.loads(imported.stdout)["work_ids"], ["arxiv:2601.00001v1"])
        before = store.snapshot()
        target = {"kind": "work", "id": "arxiv:2601.00001v2", "source_id": None, "sha256": None}
        refused = self.research_mutation("init", {"profile": "verification", "target": target}, store.revision, "unknown-target")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("unknown_work", refused.stderr)
        self.assertEqual(store.snapshot(), before)
        target["id"] = "arxiv:2601.00001v1"
        initialized = self.research_mutation("init", {"profile": "verification", "target": target}, store.revision, "exact-target")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.assertEqual(store.snapshot()["records"]["configuration"]["research"]["target"], target)
        gate = self.run_cli("exactory-research", "gate", "verification")
        self.assertNotEqual(gate.returncode, 0)
        self.assertIn("readiness_required", gate.stderr)
        self.assertNotIn("reading", store.snapshot()["records"])

    def test_acquisition_does_not_implicitly_adopt_a_legacy_workspace(self):
        path = self.root / ".exactory/study.json"
        path.parent.mkdir()
        original = b'{"version":1,"stage":"cohort","status":"pending"}\n'
        path.write_bytes(original)
        result = self.research_mutation("acquire", {"identifier": "arxiv:2601.00001v1", "max_requests": 0}, 0, "legacy-acquire")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((path.parent / "research.sqlite3").exists())
        self.assertEqual(path.read_bytes(), original)

    def test_direct_stage_jump_refuses_all_requested_changes(self):
        self.init_lab()
        path = self.root / ".exactory/study.json"
        before = path.read_bytes()
        result = self.run_cli("exactory-lab", "state", "set", "--stage", "write",
                              "--waiting", "none", "--loop-budget", "100")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("readiness", result.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_cohort_cannot_finish_without_actual_abstract_reading(self):
        self.init_lab()
        entered = self.run_cli("exactory-lab", "state", "set", "--stage", "cohort")
        self.assertEqual(entered.returncode, 0, entered.stderr)
        path = self.root / ".exactory/study.json"
        before = path.read_bytes()
        result = self.run_cli("exactory-lab", "state", "set", "--status", "done")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cohort", result.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_stage_and_done_cannot_complete_the_unread_target(self):
        self.init_lab()
        store = Store(self.root)
        before = store.snapshot()
        projection = (self.root / ".exactory/study.json").read_bytes()
        result = self.run_cli("exactory-lab", "state", "set", "--stage", "cohort", "--status", "done",
                              "--waiting", "none", "--loop-budget", "100")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cohort_missing", result.stderr)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual((self.root / ".exactory/study.json").read_bytes(), projection)
        entered = self.run_cli("exactory-lab", "state", "set", "--stage", "cohort", "--status", "pending")
        self.assertEqual(entered.returncode, 0, entered.stderr)
        self.assertEqual(store.snapshot()["records"]["workspace"]["study"]["status"], "pending")

    def test_inherited_done_cannot_complete_the_unread_target(self):
        self.init_lab()
        finished = self.run_cli("exactory-lab", "state", "set", "--status", "done")
        self.assertEqual(finished.returncode, 0, finished.stderr)
        store = Store(self.root)
        before = store.snapshot()
        projection = (self.root / ".exactory/study.json").read_bytes()
        result = self.run_cli("exactory-lab", "state", "set", "--stage", "cohort")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cohort_missing", result.stderr)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual((self.root / ".exactory/study.json").read_bytes(), projection)
        entered = self.run_cli("exactory-lab", "state", "set", "--stage", "cohort", "--status", "pending")
        self.assertEqual(entered.returncode, 0, entered.stderr)

    def test_editing_the_stage_projection_does_not_authorize_work(self):
        self.init_lab()
        path = self.root / ".exactory/study.json"
        state = json.loads(path.read_text())
        state.update(stage="write", status="done")
        path.write_text(json.dumps(state))
        result = self.run_cli("exactory-lab", "state", "set", "--stage", "evaluate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("projection", result.stderr)

    def test_unadmitted_script_never_runs(self):
        self.init_lab()
        script = self.root / "experiment/code/program.py"
        script.write_text("from pathlib import Path\nPath('LAUNCHED').write_text('yes')\nprint('{\"metric\": 1}')\n")
        result = self.run_cli("exactory-lab", "run", "code/program.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "experiment/LAUNCHED").exists())
        self.assertIn("admission", result.stderr)

    def test_common_cli_exposes_json_preparation_and_readonly_status(self):
        result = self.run_cli("exactory-research", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("recover", result.stdout)
        # Examples are available during preparation without creating a store or
        # requiring the evidence that the command helps the user describe.
        example = self.run_cli("exactory-research", "example", "cycle")
        self.assertEqual(example.returncode, 0, example.stderr)
        shape = json.loads(example.stdout)
        self.assertEqual(shape["objective"]["kind"], "objective")
        self.assertEqual({item["kind"] for item in shape["evidence_requirements"]}, {"result", "validation"})
        self.assertFalse((self.root / ".exactory").exists())
        payload = self.root / "init.json"
        payload.write_text(json.dumps({"profile": "research", "target": None}))
        result = self.run_cli("exactory-research", "init", "--file", str(payload),
                              "--expected-revision", "0", "--request-id", "init")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["revision"], 1)
        before = Store(self.root).snapshot()
        status = self.run_cli("exactory-research", "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertFalse(json.loads(status.stdout)["ready"])
        self.assertEqual(Store(self.root).snapshot(), before)

    def test_authored_artifact_is_saved_without_claiming_research_credit(self):
        from research_harness.artifacts import ArtifactStore
        self.init_lab()
        raw = b"print('An authored program, not a completed research result')\n"
        (self.root / "experiment/code/program.py").write_bytes(raw)
        path = self.root / "artifact.json"
        path.write_text(json.dumps({"id": "program", "path": "experiment/code/program.py", "media_type": "text/x-python"}))
        args = ("artifact", "--file", str(path), "--expected-revision", str(Store(self.root).revision), "--request-id", "pin-program")
        result = self.run_cli("exactory-research", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(result.stdout)["result"]
        self.assertEqual(ArtifactStore(self.root).read(saved["artifact"]), raw)
        self.assertFalse(saved["scientific_validation"])
        before = Store(self.root).snapshot()
        replay = self.run_cli("exactory-research", *args)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(json.loads(replay.stdout), json.loads(result.stdout))
        self.assertEqual(Store(self.root).snapshot(), before)

    def test_mutation_replay_and_stale_revision_preserve_original_state(self):
        self.init_lab()
        revision = Store(self.root).revision
        arguments = ("state", "set", "--stage", "cohort", "--expected-revision", str(revision), "--request-id", "enter-cohort")
        first = self.run_cli("exactory-lab", *arguments)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.run_cli("exactory-lab", *arguments).stdout, first.stdout)
        before = Store(self.root).snapshot()
        stale = self.run_cli("exactory-lab", "state", "set", "--waiting", "none", "--expected-revision", str(revision), "--request-id", "stale")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("stale_revision", stale.stderr)
        self.assertEqual(Store(self.root).snapshot(), before)

    def test_decision_replay_keeps_the_original_legacy_log_and_one_new_entry(self):
        self.init_lab()
        path = self.root / ".exactory/decisions.jsonl"
        legacy = b'{"decision":"Preserved old observation"}\n'
        path.write_bytes(legacy)
        revision = Store(self.root).revision
        args = ("decide", "--decision", "Inspect the cohort abstracts", "--why", "Reading is still pending",
                "--expected-revision", str(revision), "--request-id", "decision-one")
        first = self.run_cli("exactory-lab", *args)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = Store(self.root).snapshot()
        self.assertEqual(self.run_cli("exactory-lab", *args).stdout, first.stdout)
        self.assertEqual(Store(self.root).snapshot(), before)
        self.assertTrue(path.read_bytes().startswith(legacy))
        self.assertEqual(len(path.read_bytes().splitlines()), 2)
        self.assertIn("legacy", before["records"]["workspace_decision_history"])

    def test_explicit_cli_recovery_restores_the_committed_hot_journal_state(self):
        self.init_lab()
        store = Store(self.root)
        store.mutate("fixture.large", {}, lambda tx: tx.put("fixture", "large", {"text": "a" * (4 * 1024 * 1024)}),
                     expected_revision=store.revision, request_id="large-committed")
        before = store.snapshot()
        script = """import json, os, sqlite3, sys
from pathlib import Path
c = sqlite3.connect(Path(sys.argv[1]) / '.exactory/research.sqlite3')
c.execute('PRAGMA cache_size=8')
c.execute('PRAGMA cache_spill=8')
c.execute('BEGIN IMMEDIATE')
c.execute("UPDATE records SET value = ? WHERE kind = 'fixture'", (json.dumps({'text': 'b' * (4 * 1024 * 1024)}),))
os._exit(23)
"""
        crashed = subprocess.run([sys.executable, "-c", script, str(self.root)], capture_output=True, timeout=10)
        self.assertEqual(crashed.returncode, 23, crashed.stderr)
        journal = self.root / ".exactory/research.sqlite3-journal"
        self.assertTrue(journal.is_file())
        pending_bytes = journal.read_bytes()
        status = self.run_cli("exactory-research", "status")
        self.assertIn("store_recovery_required", status.stderr)
        self.assertEqual(journal.read_bytes(), pending_bytes)
        acquisition = self.research_mutation("acquire", {"identifier": "arxiv:2601.00001v1", "max_requests": 0},
                                             before["revision"], "hot-acquisition")
        self.assertNotEqual(acquisition.returncode, 0)
        self.assertIn("store_recovery_required", acquisition.stderr)
        self.assertEqual(journal.read_bytes(), pending_bytes)
        result = self.run_cli("exactory-research", "recover", "--expected-revision", str(before["revision"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["scientific_validation"])
        self.assertEqual(Store(self.root).snapshot(), before)


class ResearchPreparationTests(DevelopmentCase):
    def cli(self, command, *args):
        result = subprocess.run([sys.executable, str(PLUGIN / "bin" / command), *args], cwd=self.root,
                                capture_output=True, text=True)
        return result

    def json_command(self, command, payload):
        self.sequence += 1
        path = self.root / "payload.json"
        path.write_text(json.dumps(payload))
        result = self.cli("exactory-research", command, "--file", str(path),
                          "--expected-revision", str(self.store.revision), "--request-id", "cli-" + str(self.sequence))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_null_objective_preparation_order_reaches_ideate_after_actual_reading(self):
        initialized = self.cli("exactory-lab", "init", "--slug", "ordered")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.assertIsNone(self.store.snapshot()["records"]["configuration"]["research"]["target"])
        self.assertEqual(self.cli("exactory-lab", "state", "set", "--stage", "cohort").returncode, 0)
        collection = self.cohort((1,))
        early = self.cli("exactory-lab", "state", "set", "--stage", "literature")
        self.assertNotEqual(early.returncode, 0)
        self.assertIn("cohort_abstract_reading_missing", early.stderr)
        self.json_command("read", self.abstract_note("arxiv:2601.00001v1"))
        entered = self.cli("exactory-lab", "state", "set", "--stage", "literature")
        self.assertEqual(entered.returncode, 0, entered.stderr)
        self.assertNotIn("literature_scope", self.store.snapshot()["records"])
        self.assertNotEqual(self.cli("exactory-lab", "state", "set", "--stage", "ideate").returncode, 0)
        work = self.metadata()
        self.json_command("roots", {"profile": "research", "roots": [work], "collection_ids": [collection]})
        self.links = [self.read_source(n) for n in range(1, 7)]
        self.foundation_searches()
        self.objective = {"kind": "objective", "id": "ordered-objective", "statement": "Establish the complete finite bound."}
        self.json_command("target", {"target": self.objective, "reason": "Fix the full objective while preparing literature."})
        self.json_command("standards", self.standards(self.links[0]))
        self.json_command("rationale", self.rationale(self.links[0]))
        self.json_command("context", self.context(self.links[0]))
        cases = [self.case(self.links[0], 0, "within_field")]
        cases.extend(self.case(link, n) for n, link in enumerate(self.links[1:], 1))
        self.json_command("innovation", self.innovation(cases))
        entered = self.cli("exactory-lab", "state", "set", "--stage", "ideate")
        self.assertEqual(entered.returncode, 0, entered.stderr)
        self.assertEqual(self.store.snapshot()["records"]["workspace"]["study"]["stage"], "ideate")
        self.assertNotEqual(self.cli("exactory-lab", "state", "set", "--stage", "experiment").returncode, 0)


if __name__ == "__main__":
    unittest.main()
