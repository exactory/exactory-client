"""Study writing entry creates the marker used by the unchanged authorship hook."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from research_harness.cli import status_report
from research_harness.errors import ResearchError
from research_harness.integration import adopt_workspace, export_workspace, initialize_workspace, update_study
from research_harness.principles import initialize_research, set_target
from research_harness.storage import Store


PLUGIN = Path(__file__).resolve().parents[1]
OBJECTIVE = {"kind": "objective", "id": "finite-bound", "statement": "Establish the complete finite bound."}
METADATA = {"title": "A finite bound", "category": "math.CO"}


class DraftBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.store = Store(self.root, create=True)
        self.sequence = 0
        self.study = {"slug": "finite-bound", "stage": "evaluate", "status": "in_progress",
                      "autopilot": True, "waiting": None, "loop": {"target": None, "budget": None, "notes": ""},
                      "created": "2026-09-29T00:00:00Z", "updated": "2026-09-29T00:00:00Z"}
        self.mutate(initialize_workspace, {"kind": "study", "state": self.study})

    def mutate(self, function, value):
        self.sequence += 1
        return function(self.store, value, expected_revision=self.store.revision,
                        request_id="draft-" + str(self.sequence))

    def seed(self, changes, result=None, request_id=None):
        self.sequence += 1
        def apply(transaction):
            for kind, key, value in changes:
                transaction.put(kind, key, value)
            return result
        return self.store.mutate("fixture.historical", {}, apply, expected_revision=self.store.revision,
                                 request_id=request_id or "history-" + str(self.sequence))

    def set_objective(self, metadata=METADATA):
        # Seed a future-format objective so transition tests isolate marker creation from schema validation.
        target = dict(OBJECTIVE, **metadata)
        config = self.store.snapshot()["records"]["configuration"]["research"]
        self.seed([("configuration", "research", dict(config, target=target)),
                   ("research_objective", target["id"], target)])
        return target

    def enter_writing(self):
        state = dict(self.store.snapshot()["records"]["workspace"]["study"], stage="write")
        self.seed([("workspace", "study", state)])
        export_workspace(self.store)

    def write_source(self, path="draft/paper.tex", content=b"\\section{Results}\nA finite bound.\n"):
        source = self.root / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        return source

    def run_hook(self, path):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(self.root),
                   "tool_input": {"file_path": str(path)}}
        command = [sys.executable, str(PLUGIN / "hooks/record_paper_authorship.py")]
        completed = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                                   cwd=self.root, timeout=15,
                                   env=dict(os.environ, EXACTORY_API_KEY="", PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, "", ""))

    def initialize_draft(self, **metadata):
        return self.mutate(initialize_workspace, {"kind": "draft", "state": dict(METADATA, **metadata)})

    def test_objective_accepts_explicit_draft_metadata_without_requiring_it_for_older_targets(self):
        target = dict(OBJECTIVE, **METADATA)
        metadata_store = Store(self.root.parent / "objective", create=True)
        initialize_research(metadata_store, {"profile": "research", "target": None},
                            expected_revision=0, request_id="pending-objective")
        try:
            result = set_target(metadata_store, {"target": target, "reason": "Record the objective and title."},
                                expected_revision=1, request_id="objective-metadata")
        except ResearchError as error:
            self.fail("explicit objective title/category must be accepted: " + error.code)
        self.assertEqual(result["result"]["target"], target)
        self.assertEqual(metadata_store.snapshot()["records"]["research_objective"]["finite-bound"], target)
        legacy = Store(self.root.parent / "legacy", create=True)
        initialize_research(legacy, {"profile": "research", "target": OBJECTIVE},
                            expected_revision=0, request_id="legacy-objective")
        self.assertEqual(legacy.snapshot()["records"]["configuration"]["research"]["target"], OBJECTIVE)
        for field in ("title", "category"):
            for invalid in (None, "", "  ", 4, False):
                with self.subTest(field=field, invalid=invalid):
                    other = Store(self.root.parent / (field + "-" + str(invalid)), create=True)
                    with self.assertRaises(ResearchError) as caught:
                        initialize_research(other, {"profile": "research", "target": dict(target, **{field: invalid})},
                                            expected_revision=0, request_id="invalid-metadata")
                    self.assertEqual(caught.exception.code, "invalid_target")

    def test_entering_write_atomically_creates_draft_from_exact_objective_metadata(self):
        self.set_objective()
        before = self.store.snapshot()
        result = self.mutate(update_study, {"stage": "write"})
        snapshot = self.store.snapshot()
        self.assertIn("draft", snapshot["records"]["workspace"], "write entry must record a draft in the same mutation")
        marker = snapshot["records"]["workspace"]["draft"]
        self.assertEqual(snapshot["revision"], before["revision"] + 1)
        self.assertEqual({key: marker[key] for key in METADATA}, METADATA)
        self.assertNotIn("corpus", marker)
        self.assertEqual(marker["created"], result["result"]["updated"])
        self.assertEqual(marker["version"], 2)
        self.assertEqual(marker["research"], {"store": ".exactory/research.sqlite3", "profile": "research"})
        self.assertEqual(json.loads((self.root / ".exactory/draft.json").read_text()), marker)
        self.assertEqual(snapshot["records"]["research_objective"], before["records"]["research_objective"])
        self.assertTrue((self.root / "draft").is_dir())
        preserved = copy.deepcopy(marker)
        self.mutate(update_study, {"status": "in_progress"})
        self.assertEqual(self.store.snapshot()["records"]["workspace"]["draft"], preserved)

    def test_same_stage_repair_and_replay_restore_marker_without_rewriting_historical_results(self):
        revision = self.store.revision
        original = self.mutate(update_study, {"status": "in_progress"})
        self.set_objective()
        self.enter_writing()
        self.mutate(update_study, {"status": "in_progress"})
        marker = self.root / ".exactory/draft.json"
        self.assertTrue(marker.is_file(), "a same-stage write mutation must repair a missing draft record")
        marker.unlink()
        before = self.store.snapshot()
        replay = update_study(self.store, {"status": "in_progress"}, expected_revision=revision,
                              request_id=original["request_id"])
        self.assertEqual(replay, original)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(json.loads(marker.read_text()), before["records"]["workspace"]["draft"])

    def test_missing_metadata_leaves_no_draft_and_summary_names_each_missing_field(self):
        self.set_objective({})
        self.enter_writing()
        before = self.store.snapshot()
        completed = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "status", "--summary"],
                                   cwd=self.root, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        codes = {entry["code"] for entry in report["obligations"]["by_code"]}
        self.assertIn("draft_marker_missing", codes, "writing status must expose the absent draft marker")
        self.assertEqual(report["next_hint"]["code"], "draft_marker_missing")
        self.assertIn("title", report["next_hint"]["explanation"])
        self.assertIn("category", report["next_hint"]["explanation"])
        detailed = status_report(self.store)
        missing = next(item for item in detailed["obligations"] if item["code"] == "draft_marker_missing")
        self.assertEqual(missing["missing_fields"], ["title", "category"])
        self.assertIn("title", missing["explanation"])
        self.assertIn("category", missing["explanation"])
        self.assertFalse((self.root / ".exactory/draft.json").exists())
        self.assertNotIn("draft", before["records"]["workspace"])
        self.assertEqual(self.store.snapshot(), before)

    def test_each_individual_missing_metadata_field_prevents_creation_and_is_named(self):
        for missing in ("title", "category"):
            with self.subTest(missing=missing):
                self.set_objective({key: value for key, value in METADATA.items() if key != missing})
                self.mutate(update_study, {"stage": "write"})
                try:
                    report = status_report(self.store)
                except ResearchError as error:
                    self.fail("partial objective metadata must yield a marker obligation: " + error.code)
                obligations = [item for item in report["obligations"] if item["code"] == "draft_marker_missing"]
                self.assertEqual(len(obligations), 1)
                self.assertEqual(obligations[0]["missing_fields"], [missing])
                self.assertIn(missing, obligations[0]["explanation"])
                self.assertNotIn("draft", self.store.snapshot()["records"]["workspace"])

    def test_missing_projection_is_reported_until_export_and_existing_metadata_is_preserved(self):
        self.set_objective({})
        self.enter_writing()
        self.initialize_draft(title="Existing manuscript", corpus="openalex")
        marker = self.root / ".exactory/draft.json"
        marker.unlink()
        before = self.store.snapshot()
        report = status_report(self.store)
        self.assertIn("draft_marker_missing", {item["code"] for item in report["obligations"]})
        self.assertEqual(self.store.snapshot(), before)
        self.mutate(update_study, {"status": "in_progress"})
        self.assertEqual(json.loads(marker.read_text()), before["records"]["workspace"]["draft"])
        self.assertEqual(json.loads(marker.read_text())["title"], "Existing manuscript")

    def test_unadopted_legacy_marker_is_preserved_and_reported_instead_of_replaced(self):
        self.set_objective({})
        self.enter_writing()
        marker = self.root / ".exactory/draft.json"
        legacy = '{"version": 1, "title": "Legacy title", "category": "cs.LG"}\n'
        marker.write_text(legacy)
        report = status_report(self.store)
        obligations = [item for item in report["obligations"] if item["code"] == "draft_marker_missing"]
        self.assertEqual(len(obligations), 1, "unadopted legacy metadata needs an explicit adoption obligation")
        self.assertIn("adopt", obligations[0]["explanation"].lower())
        self.set_objective()
        self.mutate(update_study, {"status": "in_progress"})
        self.assertEqual(marker.read_text(), legacy)
        self.assertNotIn("draft", self.store.snapshot()["records"]["workspace"])

    def find_marker_obligation(self):
        report = status_report(self.store)
        found = [item for item in report["obligations"] if item["code"] == "draft_marker_missing"]
        self.assertLessEqual(len(found), 1)
        if found:
            self.assertLessEqual(len(found[0]["explanation"]), 120, "the next action must fit the summary hint")
        return found[0] if found else None

    def adopt_marker(self):
        config = self.store.snapshot()["records"]["configuration"]["research"]
        return self.mutate(adopt_workspace, {"id": "adopt-draft", "profile": config["profile"],
                                             "target": config["target"], "files": [".exactory/draft.json"],
                                             "reason": "Adopt the legacy draft marker."})

    def move_marker_aside(self):
        saved = self.root / ".exactory/draft.json.saved"
        os.rename(self.root / ".exactory/draft.json", saved)
        return saved

    def test_unrecorded_link_marker_is_moved_aside_before_bootstrap(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        (outside / "draft.json").write_text('{"version": 2, "title": "Outside"}\n')
        (self.root / "notes").mkdir()
        (self.root / "notes/draft.json").write_text('{"version": 2, "title": "Inside"}\n')
        targets = {"internal": "../notes/draft.json", "external": str(outside / "draft.json"),
                   "missing": str(outside / "absent.json"), "directory": str(outside)}
        self.set_objective()
        self.enter_writing()
        marker = self.root / ".exactory/draft.json"
        for case, target in targets.items():
            with self.subTest(case=case):
                if marker.is_symlink():
                    marker.unlink()
                os.symlink(target, marker)
                obligation = self.find_marker_obligation()
                self.assertIn("unused name", obligation["explanation"])
                self.assertNotIn("adopt", obligation["explanation"].lower())
                with self.assertRaises(ResearchError) as caught:
                    self.adopt_marker()
                self.assertEqual(caught.exception.code, "unsafe_path")
                saved = self.move_marker_aside()
                self.assertIn("state set --stage write", self.find_marker_obligation()["explanation"])
                self.assertEqual(os.readlink(saved), target)
                saved.unlink()
        if marker.is_symlink():
            marker.unlink()
        self.assertEqual((outside / "draft.json").read_text(), '{"version": 2, "title": "Outside"}\n')
        self.mutate(update_study, {"status": "in_progress"})
        self.assertIsNone(self.find_marker_obligation())

    def test_unadoptable_regular_marker_is_moved_aside_instead_of_adopted(self):
        self.set_objective()
        self.enter_writing()
        marker = self.root / ".exactory/draft.json"
        for case, content in {"malformed": "{", "duplicate": '{"version": 1, "version": 2}',
                              "array": "[]", "version": '{"version": 3, "title": "Later"}'}.items():
            with self.subTest(case=case):
                marker.write_text(content)
                obligation = self.find_marker_obligation()
                self.assertIn("unused name", obligation["explanation"])
                self.assertEqual(marker.read_text(), content)
                with self.assertRaises(ResearchError) as caught:
                    self.adopt_marker()
                self.assertIn(caught.exception.code, ("invalid_json", "migration_required"))
                self.move_marker_aside().unlink()
        if marker.exists():
            marker.unlink()
        self.initialize_draft()
        self.assertIsNone(self.find_marker_obligation())

    def test_directory_marker_is_moved_aside_with_and_without_a_record(self):
        self.set_objective()
        self.enter_writing()
        marker = self.root / ".exactory/draft.json"
        marker.mkdir()
        (marker / "kept.txt").write_text("kept")
        self.assertIn("unused name", self.find_marker_obligation()["explanation"])
        saved = self.move_marker_aside()
        self.mutate(update_study, {"status": "in_progress"})
        self.assertIsNone(self.find_marker_obligation())
        self.assertEqual((saved / "kept.txt").read_text(), "kept")
        recorded = marker.read_text()
        marker.unlink()
        marker.mkdir()
        self.assertIn("unused name", self.find_marker_obligation()["explanation"])
        os.rename(marker, self.root / ".exactory/draft.json.second")
        self.assertIn("export", self.find_marker_obligation()["explanation"])
        export_workspace(self.store)
        self.assertIsNone(self.find_marker_obligation())
        self.assertEqual(marker.read_text(), recorded)

    def test_completed_write_stage_names_explicit_draft_initialization(self):
        self.set_objective()
        self.seed([("workspace", "study", dict(self.store.snapshot()["records"]["workspace"]["study"],
                                                stage="write", status="done"))])
        objective = self.store.snapshot()["records"]["configuration"]["research"]["target"]
        obligation = self.find_marker_obligation()
        self.assertIn("exactory-draft init", obligation["explanation"])
        self.assertEqual(obligation["missing_fields"], [])
        self.initialize_draft()
        snapshot = self.store.snapshot()
        self.assertIsNone(self.find_marker_obligation())
        self.assertEqual(snapshot["records"]["workspace"]["study"]["status"], "done")
        self.assertEqual(snapshot["records"]["configuration"]["research"]["target"], objective)

    def test_versionless_regular_marker_is_adopted_as_stored(self):
        self.set_objective({})
        self.enter_writing()
        marker = self.root / ".exactory/draft.json"
        marker.write_text('{"title": "Legacy", "category": "cs.LG"}\n')
        self.assertIn("adopt", self.find_marker_obligation()["explanation"].lower())
        self.adopt_marker()
        self.assertEqual(self.store.snapshot()["records"]["workspace"]["draft"],
                         {"title": "Legacy", "category": "cs.LG"})
        self.assertIsNone(self.find_marker_obligation())

    def test_failed_transition_creates_no_draft_and_successful_return_to_write_still_bootstraps(self):
        self.set_objective()
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.mutate(update_study, {"stage": "complete"})
        self.assertEqual(caught.exception.code, "readiness_transition")
        self.assertEqual(self.store.snapshot(), before)
        self.assertFalse((self.root / ".exactory/draft.json").exists())
        self.mutate(update_study, {"stage": "write"})
        self.assertIn("draft", self.store.snapshot()["records"]["workspace"])

    def test_write_after_writing_entry_records_authorship_through_the_host_hook(self):
        self.set_objective()
        self.mutate(update_study, {"stage": "write"})
        source = self.write_source()
        before = self.store.snapshot()
        self.run_hook(source)
        authorship = self.root / ".exactory/authorship.json"
        self.assertTrue(authorship.is_file(), "writing entry must supply the marker needed by the host's Write hook")
        self.assertEqual(json.loads(authorship.read_text()), {"written_by_exactory": True})
        self.assertEqual(self.store.snapshot(), before, "the authorship hook must not mutate the study store")

    def test_pre_marker_write_and_export_do_not_claim_authorship(self):
        self.enter_writing()
        source = self.write_source()
        before = self.store.snapshot()
        self.run_hook(source)
        self.assertEqual(self.store.snapshot(), before, "a pre-marker Write must not mutate the study store")
        authorship = self.root / ".exactory/authorship.json"
        self.assertFalse(authorship.exists())
        self.set_objective()
        self.mutate(update_study, {"status": "in_progress"})
        self.assertTrue((self.root / ".exactory/draft.json").is_file())
        export_workspace(self.store)
        self.assertFalse(authorship.exists(), "marker creation and export must not credit a pre-marker source")

    def test_lab_study_enters_write_after_real_observed_candidate_without_separate_draft_init(self):
        from integration_fixtures import prepare_research
        root = self.root.parent / "from-lab"
        completed = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-lab"), "init",
                                    "--dir", str(root), "--slug", "from-lab"], text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        objective = dict(OBJECTIVE, **METADATA)
        try:
            case = prepare_research(root, objective, candidate=True)
        except ResearchError as error:
            self.fail("the real study must accept its explicit manuscript metadata: " + error.code)
        study = dict(case.store.snapshot()["records"]["workspace"]["study"], stage="experiment",
                     status="in_progress", waiting=None)
        case.store.mutate("fixture.historical_experiment_stage", {},
                          lambda transaction: transaction.put("workspace", "study", study),
                          expected_revision=case.store.revision, request_id="experiment-stage")
        export_workspace(case.store)
        completed = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-lab"), "state", "set",
                                    "--stage", "write"], cwd=root, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads((root / ".exactory/draft.json").read_text())["title"], "A finite bound")

    def test_deposit_from_bootstrapped_study_reaches_token_preflight_without_explicit_draft_init(self):
        self.set_objective()
        self.mutate(update_study, {"stage": "write"})
        (self.root / "draft").mkdir(exist_ok=True)
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("A finite bound.")
        env = dict(os.environ, EXACTORY_API_KEY="", ZENODO_SANDBOX_TOKEN="", ZENODO_TOKEN="")
        completed = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-draft"), "deposit",
                                    "--creator", "Fixture Author", "--abstract-file", "draft/abstract.txt"],
                                   cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("ZENODO_SANDBOX_TOKEN is not set", completed.stderr)


if __name__ == "__main__":
    unittest.main()
