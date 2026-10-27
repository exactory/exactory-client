"""Admission and legacy reconciliation complete only the missing workspace skeleton."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import attack
from search_controller import service
from search_controller.errors import SearchError
from search_controller.model import replay
from tests.native_reviewer_support import observe_review
from tests.search_execution_support import admit_workspace, begin_spec, invoke
from tests.search_fixtures import contract, proposal, review
from tests.strategy_refresh_support import reassess_fixture
from tests.support import ALL_YES, OPENING, FIXTURE_STRATEGIES, make_move, make_preconditions, make_problem, run


class WorkspaceSkeletonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()

    def prepare_proposal(self, name, agent_files=True, journal=None):
        root = self.directory / name / "attack"
        workspace = root / "attempt"
        (workspace / "study").mkdir(parents=True)
        objective_contract = contract()
        candidate_proposal = proposal()
        candidate_proposal.update(schema_version=3, computation=None, foundation=None, method=OPENING)
        candidate_proposal["studies"]["strategies"][0]["method"] = OPENING
        inputs = []
        for name, relative in [("problem", "study/problem.md"), ("novelty", "novelty.md"),
                               ("strategy", "study/" + OPENING + ".md")]:
            path = (workspace if agent_files else root) / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(("Recorded study for " + name + ".\r\n").encode())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            inputs.append({"path": path.relative_to(root).as_posix(), "digest": digest, "kind": "artifact"})
            if name == "strategy":
                candidate_proposal["studies"]["strategies"][0]["digest"] = digest
            else:
                candidate_proposal["studies"][name] = digest
        if agent_files:
            problem = make_problem()
            problem["claim"] = objective_contract["original_claim"]["statement"]
            (workspace / "problem.json").write_bytes(json.dumps(problem, indent=2).encode() + b"\r\n")
            (workspace / "preconditions.json").write_text(json.dumps(make_preconditions(ALL_YES)))
        (workspace / "study/notes.md").write_bytes(b"Agent notes retained byte for byte.\r\n")
        if journal is not None:
            (workspace / "journal.jsonl").write_bytes(journal)
        controller = service.Controller(root, FIXTURE_STRATEGIES)
        controller.command("init", {"contract": objective_contract}, 0, "initialize")
        controller.command("propose", {"proposal": candidate_proposal, "inputs": inputs}, 1, "proposal")
        observed_review = observe_review(controller, review(candidate_proposal), candidate_proposal)
        controller.command("review", {"proposal_id": "proposal-000001", "review": observed_review, "inputs": []},
                           2, "review")
        return controller, workspace

    def read_workspace_files(self, workspace):
        return {path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*") if path.is_file() and path.name != "LINEAGE.md"}

    def test_admit_completes_missing_items_and_preserves_existing_research_boundary(self):
        for name, agent_files, journal in [("studied", True, None), ("empty", True, b""),
                                            ("templates", False, None), ("nonempty", True, b'{"move":1}\n')]:
            with self.subTest(name=name):
                controller, workspace = self.prepare_proposal(name, agent_files, journal)
                before = self.read_workspace_files(workspace)
                document = controller.store.read()
                if journal:
                    with self.assertRaises(SearchError) as caught:
                        controller.command("admit", {}, 3, "admit", "proposal-000001")
                    self.assertEqual(caught.exception.code, "adoption_required")
                    self.assertEqual(controller.store.read(), document)
                    self.assertEqual(self.read_workspace_files(workspace), before)
                    continue
                controller.command("admit", {}, 3, "admit", "proposal-000001")
                self.assertTrue((workspace / "journal.jsonl").is_file(), "Admission must create its journal")
                self.assertEqual((workspace / "journal.jsonl").read_bytes(), b"")
                for relative, raw in before.items():
                    self.assertEqual((workspace / relative).read_bytes(), raw, relative)
                for directory in ["deterministic", "units", "study"]:
                    self.assertTrue((workspace / directory).is_dir(), directory)
                effects = [effect for effect in controller.status()["service"]["effects"]
                           if effect["kind"] == "initialize_workspace"]
                self.assertEqual(len(effects), 1)
                snapshot = controller.store.get_blob(effects[0]["snapshot_digest"])
                expected = {"journal.jsonl"} if journal is None else set()
                if not agent_files:
                    expected.update({"problem.json", "novelty.md"})
                    self.assertEqual(attack.read_json(workspace / "problem.json"), attack.build_problem_skeleton())
                    self.assertEqual((workspace / "novelty.md").read_bytes(), b"")
                self.assertEqual({item["path"] for item in snapshot["files"]}, expected)
                if agent_files:
                    status, out, err = run(["plan", "attempt"], controller.root)
                    self.assertEqual((status, err), (0, ""), out)
                    openings = attack.read_json(workspace / "openings.json")["openings"]
                    ranking = {"generated_from": "openings.json", "order": [
                        {"strategy": item["strategy"], "cites": ["shape.objects"], "reason": "The study applies"}
                        for item in openings]}
                    (workspace / "ranking.json").write_text(json.dumps(ranking))
                    reassess_fixture(controller, {("node-000001", OPENING)})
                    invoke(controller, "begin", begin_spec())
                    state = controller.status()
                    self.assertEqual(state["control"]["pending_moves"], ["move-node-000001-1"])
                    self.assertEqual(state["service"]["moves"]["move-node-000001-1"]["move"], 1)

    def test_interrupted_existing_workspace_admission_recovers_and_refuses_conflicting_bytes(self):
        for recovery in ["retry", "render", "conflict"]:
            with self.subTest(recovery=recovery):
                controller, workspace = self.prepare_proposal(recovery)
                before = self.read_workspace_files(workspace)
                replace_original_text = service.replace_text

                def interrupt_journal(path, value):
                    if path.name == "journal.jsonl":
                        raise OSError("Interrupted skeleton initialization")
                    return replace_original_text(path, value)

                with mock.patch.object(service, "replace_text", side_effect=interrupt_journal):
                    with self.assertRaisesRegex(OSError, "Interrupted skeleton initialization"):
                        controller.command("admit", {}, 3, "admit-interrupted", "proposal-000001")
                self.assertEqual(controller.status()["revision"], 4)
                self.assertTrue(controller.status()["service"]["pending_effect_ids"])
                if recovery == "conflict":
                    (workspace / "journal.jsonl").write_bytes(b"conflicting bytes\n")
                    with self.assertRaises(SearchError) as caught:
                        controller.command("admit", {}, 3, "admit-interrupted", "proposal-000001")
                    self.assertEqual(caught.exception.code, "recovery_conflict")
                    self.assertEqual((workspace / "journal.jsonl").read_bytes(), b"conflicting bytes\n")
                    continue
                if recovery == "retry":
                    controller.command("admit", {}, 3, "admit-interrupted", "proposal-000001")
                else:
                    controller.command("render", {}, 4, "recover")
                self.assertEqual((workspace / "journal.jsonl").read_bytes(), b"")
                self.assertFalse(controller.status()["service"]["pending_effect_ids"])
                self.assertEqual(len(controller.status()["nodes"]), 1)
                for relative, raw in before.items():
                    self.assertEqual((workspace / relative).read_bytes(), raw, relative)

    def test_missing_native_journal_names_reconcile_only_inside_managed_root(self):
        root = self.directory / "attack"
        status, out, err = run(["init", "attempt"], root)
        self.assertEqual((status, err), (0, ""), out)
        workspace = root / "attempt"
        (workspace / "journal.jsonl").unlink()
        for command in ["budget", "status"]:
            status, out, err = run([command, "attempt"], root)
            self.assertEqual((status, out, err), (1, "", "journal.jsonl: missing\n"))
        service.Controller(root).command("init", {"contract": contract()}, 0, "initialize")
        for command in ["budget", "status"]:
            with self.subTest(command=command):
                status, out, err = run([command, "attempt"], root)
                self.assertEqual((status, out), (1, ""))
                self.assertIn("admission skeleton is incomplete", err)
                self.assertIn("search reconcile", err)

    def test_reconcile_completes_legacy_skeleton_without_changing_recorded_replay(self):
        for repair in ["missing", "interrupted", "legacy-init", "populated", "recorded-move"]:
            with self.subTest(repair=repair):
                root = self.directory / repair / "attack"
                status, out, err = run(["init", "sample"], root)
                self.assertEqual((status, err), (0, ""), out)
                controller = admit_workspace(root, historical=True)
                workspace = root / "sample"
                if repair in {"populated", "recorded-move"}:
                    reassess_fixture(controller, {("node-000001", OPENING)})
                    invoke(controller, "begin", begin_spec())
                    if repair == "populated":
                        status, out, err = run(["journal", "add", "sample", "--json", json.dumps(make_move(1))], root)
                        self.assertEqual((status, err), (0, ""), out)
                document = controller.store.read()
                recorded = replay(document)
                self.assertFalse(any(effect["kind"] == "initialize_workspace"
                                     for effect in recorded["service"]["effects"]))
                before = self.read_workspace_files(workspace)
                if repair != "populated":
                    (workspace / "journal.jsonl").unlink()
                    for directory in ["deterministic", "units"]:
                        (workspace / directory).rmdir()
                if repair == "legacy-init":
                    backup = workspace.with_name("sample-preadmission")
                    workspace.rename(backup)
                    status, out, err = run(["init", "sample"], root)
                    self.assertEqual((status, err), (0, ""), out)
                    for relative, raw in before.items():
                        if relative == "journal.jsonl":
                            continue
                        path = workspace / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(raw)
                self.assertEqual(replay(controller.store.read()), recorded)
                revision = recorded["revision"]
                if repair == "recorded-move":
                    with self.assertRaises(SearchError) as caught:
                        controller.command("reconcile", {}, revision, "repair-skeleton")
                    self.assertEqual(caught.exception.code, "recovery_conflict")
                    self.assertFalse((workspace / "journal.jsonl").exists())
                    self.assertEqual(controller.store.read(), document)
                    continue
                if repair == "interrupted":
                    replace_original_text = service.replace_text

                    def interrupt_journal(path, value):
                        if path.name == "journal.jsonl":
                            raise OSError("Interrupted legacy skeleton repair")
                        return replace_original_text(path, value)

                    with mock.patch.object(service, "replace_text", side_effect=interrupt_journal):
                        with self.assertRaisesRegex(OSError, "Interrupted legacy skeleton repair"):
                            controller.command("reconcile", {}, revision, "repair-skeleton")
                controller.command("reconcile", {}, revision, "repair-skeleton")
                self.assertEqual((workspace / "journal.jsonl").read_bytes(), before["journal.jsonl"])
                for directory in ["deterministic", "units", "study"]:
                    self.assertTrue((workspace / directory).is_dir(), directory)
                for relative, raw in before.items():
                    self.assertEqual((workspace / relative).read_bytes(), raw, relative)
                repaired = controller.store.read()
                self.assertEqual(repaired["events"][:-1], document["events"])
                self.assertEqual(replay(dict(repaired, events=repaired["events"][:-1])), recorded)
                for key in ["nodes", "accounts", "runs", "totals", "acceptances", "proof_status"]:
                    self.assertEqual(controller.status()[key], recorded[key], key)
                initialized = [effect for effect in controller.status()["service"]["effects"]
                               if effect["kind"] == "initialize_workspace"]
                self.assertEqual(len(initialized), 0 if repair in {"legacy-init", "populated"} else 1)
                self.assertFalse(controller.status()["service"]["pending_effect_ids"])
                controller.command("reconcile", {}, revision, "repair-skeleton")
                self.assertEqual(controller.store.read(), repaired)
