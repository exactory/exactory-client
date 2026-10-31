"""The final/, final/history/ and intermediate/ views that the harness renders from a research store."""

import ast
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from literature_fixtures import LiteratureCase
from research_harness import cli, output_views
from research_harness.artifacts import ArtifactStore
from research_harness.cli import main, status_report
from research_harness.evidence import digest
from research_harness.principles import initialize_research
from research_harness.problem_claims import compute_statement_digest, initialize_problem
from research_harness.storage import Store


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
UNKNOTTING_RECORD = Path("/Users/ryshiro/exactory/math-conjectures/202610282209-unknotting-connected-sum")
# A writer whose kind argument is a variable forwards the kind of its caller; the scan reads those callers' constants.
FORWARDER_KIND_ARGUMENTS = {"immutable_record": 1, "_member": 4, "finish_intent": 2, "deliver_snapshot": 3}
FORWARDING_WRITERS = {("acquisition.py", "_member"), ("attempt_delivery.py", "deliver_snapshot"),
                      ("attempt_delivery.py", "deliver_snapshot.prepare"), ("operations.py", "prepared_mutation.apply"),
                      ("remote.py", "_finish_intent_once.prepare")}


class _KindScanner(ast.NodeVisitor):
    """Collect the record kinds that one module writes: the kind argument of Transaction.put, of immutable_record and of
    the functions that forward a kind to them, and the first item of a (kind, key, value) change."""

    def __init__(self, module, tree):
        self.module, self.functions, self.kinds, self.forwarding = module, [], set(), set()
        self.constants = {node.targets[0].id: node.value.value for node in tree.body
                          if isinstance(node, ast.Assign) and len(node.targets) == 1
                          and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)
                          and isinstance(node.value.value, str)}

    def resolve_text(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return self.constants.get(node.id) if isinstance(node, ast.Name) else None

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        index = 0 if name == "put" and len(node.args) == 3 else FORWARDER_KIND_ARGUMENTS.get(name)
        if index is not None and len(node.args) > index:
            kind = self.resolve_text(node.args[index])
            if kind is not None:
                self.kinds.add(kind)
            elif name in ("put", "immutable_record"):
                self.forwarding.add((self.module, ".".join(self.functions)))
        if name == "append" and len(node.args) == 1:
            self.add_change(node.args[0])
        self.generic_visit(node)

    def visit_List(self, node):
        for element in node.elts:
            self.add_change(element)
        self.generic_visit(node)

    def visit_Return(self, node):
        self.add_change(node.value)
        self.generic_visit(node)

    def add_change(self, node):
        if (isinstance(node, ast.Tuple) and len(node.elts) == 3 and self.resolve_text(node.elts[0]) is not None
                and not isinstance(node.elts[2], ast.Constant)):
            self.kinds.add(self.resolve_text(node.elts[0]))


def scan_written_kinds():
    """Every record kind that the harness code writes, and the writers that forward a kind they receive."""
    kinds, forwarding = set(), set()
    sources = sorted(PLUGIN_ROOT.glob("research_harness/*.py")) + sorted(
        path for path in PLUGIN_ROOT.glob("skills/*/harness/**/*.py") if "tests" not in path.parts)
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        scanner = _KindScanner(path.name, tree)
        scanner.visit(tree)
        kinds |= scanner.kinds
        forwarding |= scanner.forwarding
    return {kind for kind in kinds if re.fullmatch(r"[a-z][a-z0-9_]*", kind)}, forwarding


def read_tree(root):
    """{relative path: bytes} of every file under final/ and intermediate/."""
    files = {}
    for folder in ("final", "intermediate"):
        for directory, _, names in os.walk(root / folder):
            for name in names:
                path = Path(directory) / name
                files[str(path.relative_to(root))] = path.read_bytes()
    return files


def copy_store(source, destination):
    """A second workspace with only the store and its objects, as an existing record has before its first render."""
    (destination / ".exactory").mkdir(parents=True)
    shutil.copy2(source / ".exactory/research.sqlite3", destination / ".exactory/research.sqlite3")
    if (source / "research/sources/objects").exists():
        shutil.copytree(source / "research/sources/objects", destination / "research/sources/objects")
    return Store(destination)


def load_bin_module(command_name, module_name):
    loader = importlib.machinery.SourceFileLoader(module_name, str(PLUGIN_ROOT / "bin" / command_name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(module_name, loader))
    loader.exec_module(module)
    return module


class ViewCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        self.sequence = 0

    def put(self, *changes):
        """Commit (kind, key, value) records in one mutation, as one command does."""
        self.sequence += 1
        def apply(transaction):
            for kind, key, value in changes:
                transaction.put(kind, key, value)
            return {}
        return self.store.mutate("fixture.seed", {"sequence": self.sequence}, apply,
                                 expected_revision=self.store.revision, request_id="seed-" + str(self.sequence))

    def render(self):
        return output_views.render_pending(self.store)

    def put_json(self, value):
        return self.artifacts.put(json.dumps(value).encode(), "application/json")

    def read_json(self, relative):
        return json.loads((self.root / relative).read_bytes())

    def find_entry(self, folder, path):
        return next(entry for entry in self.read_json(folder + "/index.json")["files"] if entry["path"] == path)

    def assign(self, identifier, *, role="bar", prior=None, repair=None, slot=None, turn=None, session=None,
               history=None):
        context = {}
        if prior is not None:
            context["prior_assignment_id"] = prior
        if repair is not None:
            context["context_repair"] = {"assignment_id": repair}
        value = {"id": identifier, "role": role, "dossier_id": None, "reviewer_id": "reviewer-" + role,
                 "route_id": "route-a", "session_id": session or identifier, "context": context,
                 "packet": self.put_json({"packet": identifier}), "recorded_revision": self.store.revision + 1}
        if slot is not None:
            value["slot_id"] = slot
        if turn is not None:
            value["reading_chain"] = {"first_assignment_id": identifier, "turn": turn}
        if history is not None:
            value["history_assignment_id"] = history
        self.put(("review_assignment", identifier, value))

    def run_turn(self, assignment_id, run_id, output=None, *, status="completed", error=None, verified=True):
        """Record one invocation and its attempt; output None stands for a run that ended without output."""
        invocation = {"id": run_id, "assignment_id": assignment_id, "recorded_revision": self.store.revision + 1}
        self.put(("review_invocation", run_id, invocation))
        response = {"context_evidence": {"inspected": True, "input_verified": verified}}
        attempt = {"id": run_id, "invocation_id": run_id, "assignment_id": assignment_id, "status": status,
                   "error": error, "usage": {"input_tokens": 10, "output_tokens": 5},
                   "output": None if output is None else self.put_json(output), "response": self.put_json(response),
                   "recorded_revision": self.store.revision + 1}
        self.put(("review_attempt", run_id, attempt))
        return attempt

    def refuse(self, attempt):
        self.put(("review_output_refusal", attempt["id"], {"id": attempt["id"], "attempt_id": attempt["id"],
                  "assignment_id": attempt["assignment_id"], "code": "invalid_reading_response",
                  "check": "native.output", "details": None, "recorded_revision": self.store.revision + 1}))

    def review(self, identifier, assignment_id, stage="bar"):
        self.put(("value_review", identifier, {"id": identifier, "assignment_id": assignment_id, "dossier_id": None,
                  "payload": {"stage": stage}, "recorded_revision": self.store.revision + 1}))


class RegistryTests(unittest.TestCase):
    def test_every_written_kind_has_a_placement(self):
        kinds, forwarding = scan_written_kinds()
        self.assertGreater(len(kinds), 100)
        # Kinds that reach immutable_record through a helper: remote.finish_intent and deliver_snapshot.
        self.assertLessEqual({"publication_receipt", "submission_receipt", "verdict_receipt", "native_attempt",
                              "native_closure", "cohort_seen"}, kinds)
        self.assertEqual(kinds - set(output_views.PLACEMENT_BY_KIND), set())
        # A new writer that receives its kind must name the callers it forwards for, so the scan sees their kinds.
        self.assertLessEqual(forwarding, FORWARDING_WRITERS)

    def test_every_placed_kind_has_one_view_group(self):
        placed = [kind for group in output_views.VIEW_GROUPS for kind in group.kinds]
        self.assertEqual(len(placed), len(set(placed)))
        placement = output_views.PLACEMENT_BY_KIND
        self.assertEqual({kind for kind, group in placement.items() if group != "none"}, set(placed))
        for group in output_views.VIEW_GROUPS:
            self.assertTrue(all(placement[kind] == group.name for kind in group.kinds), group.name)
            self.assertLessEqual(set(group.reads), set(placement), group.name)


class FinalRecordTests(ViewCase):
    def test_reviews_are_current_superseded_or_historical(self):
        self.assign("bar-a")
        self.review("review-a1", "bar-a")
        self.put(("research_support", "review-a1", {"review_id": "review-a1", "assignment_id": "bar-a"}))
        self.assign("bar-b")
        self.review("review-b1", "bar-b")
        self.assign("bar-b-2", prior="bar-b")
        self.review("review-b2", "bar-b-2")
        self.assign("bar-a-repair", repair="bar-a")
        self.review("review-a2", "bar-a-repair")
        self.put(("review_adjudication", "adjudication-1", {"id": "adjudication-1", "assignment_id": "bar-a",
                  "recorded_revision": 1}),
                 ("review_adjudication", "adjudication-2", {"id": "adjudication-2", "assignment_id": "bar-a",
                  "recorded_revision": 2}),
                 ("readiness_review", "readiness-1", {"id": "readiness-1"}),
                 ("readiness_review", "readiness-2", {"id": "readiness-2"}),
                 ("development_selection", "review", {"review_id": "readiness-2"}),
                 ("round_review", "round-review-1", {"id": "round-review-1", "round_id": "decision-1",
                  "reviewed_revision": 3}))
        self.render()
        # The repair ended the independence of its predecessor's lineage; a later review of the slot supersedes.
        self.assertEqual(self.find_entry("final", "history/reviews/bar/review-a1.json")["status"], "historical")
        self.assertEqual(self.find_entry("final", "history/reviews/bar/review-a1.json")["research_support_id"],
                         "review-a1")
        self.assertEqual(self.find_entry("final", "reviews/bar/review-a2.json")["status"], "current")
        self.assertEqual(self.find_entry("final", "history/reviews/bar/review-b1.json")["status"], "superseded")
        self.assertEqual(self.read_json("final/reviews/bar/review-b2.json")["assignment_id"], "bar-b-2")
        self.assertTrue((self.root / "final/history/reviews/adjudicator/adjudication-1.json").is_file())
        self.assertTrue((self.root / "final/reviews/adjudicator/adjudication-2.json").is_file())
        self.assertTrue((self.root / "final/history/reviews/readiness/readiness-1.json").is_file())
        self.assertTrue((self.root / "final/reviews/readiness/readiness-2.json").is_file())
        self.assertTrue((self.root / "final/reviews/round/round-review-1.json").is_file())

    def test_problem_records_follow_the_selected_statement_and_the_latest_records(self):
        statement = {"id": "statement-1", "statement": "S", "resolution_criteria": "C", "recorded_revision": 1}
        computation = {"kind": "computation", "formal_check": "check-1", "execution_id": "execution-1",
                       "output_id": "output-1"}
        self.put(("problem_statement", "statement-1", statement),
                 ("problem_selection", "current", {"statement_id": "statement-1"}),
                 ("problem_slate", "slate-1", {"id": "slate-1", "recorded_revision": 1}),
                 ("problem_slate", "slate-2", {"id": "slate-2", "recorded_revision": 2}),
                 ("problem_value", "value-1", {"id": "value-1", "recorded_revision": 1,
                  "motivation": [{"evidence": [computation]}], "current_state": []}),
                 ("problem_formal_check", "formal-1", {"formal_check": "check-1", "evidence": {
                     "execution_id": "execution-1", "output_id": "output-1"}}),
                 ("problem_formal_check", "formal-0", {"formal_check": "check-0", "evidence": {
                     "execution_id": "execution-0", "output_id": "output-0"}}),
                 ("problem_post", "post-1", {"statement_digest": compute_statement_digest(statement)}))
        for kind in ("problem_attempt", "problem_openness", "problem_assessment", "problem_decision"):
            self.put((kind, kind + "-1", {"id": kind + "-1", "recorded_revision": 1}))
        self.render()
        for path in ("problem/statements/statement-1.json", "problem/slates/slate-2.json",
                     "problem/values/value-1.json",
                     "problem/formal-checks/formal-1.json", "problem/posts/post-1.json",
                     "problem/attempts/problem_attempt-1.json", "problem/openness/problem_openness-1.json",
                     "problem/assessments/problem_assessment-1.json", "problem/decisions/problem_decision-1.json"):
            self.assertEqual(self.find_entry("final", path)["status"], "current", path)
        self.assertTrue((self.root / "final/history/problem/slates/slate-1.json").is_file())
        self.assertTrue((self.root / "final/history/problem/formal-checks/formal-0.json").is_file())
        # A new statement moves the earlier one and its post to the history.
        self.put(("problem_statement", "statement-2", dict(statement, id="statement-2", statement="T")),
                 ("problem_selection", "current", {"statement_id": "statement-2"}))
        self.render()
        self.assertFalse((self.root / "final/problem/statements/statement-1.json").exists())
        self.assertEqual(self.find_entry("final", "history/problem/statements/statement-1.json")["status"],
                         "superseded")
        self.assertTrue((self.root / "final/problem/statements/statement-2.json").is_file())
        self.assertTrue((self.root / "final/history/problem/posts/post-1.json").is_file())
        self.assertFalse((self.root / "final/problem/posts").exists())

    def test_study_and_verification_records_follow_pointers_and_latest_records(self):
        task = {"id": "task-1", "digest": "d" * 64}
        self.put(("round_admission", "admission-1", {"id": "admission-1", "number": 2, "admitted_revision": 1}),
                 ("round_decision", "decision-a", {"id": "decision-a", "closes": 1, "decided_revision": 1}),
                 ("round_decision", "decision-b", {"id": "decision-b", "closes": 1, "decided_revision": 2}),
                 ("round_assessment", "assessment-1", {"id": "assessment-1", "round_id": "admission-1",
                  "assessed_revision": 3}),
                 ("research_decision", "research-1", {"id": "research-1"}),
                 ("research_decision", "research-2", {"id": "research-2"}),
                 ("strategy_selection", "decision", {"id": "research-2"}),
                 ("strategy_selection", "intent", {"id": "research-1"}),
                 ("cycle_assessment", "cycle-assessment-1", {"id": "cycle-assessment-1"}),
                 ("cycle_assessment", "cycle-assessment-2", {"id": "cycle-assessment-2"}),
                 ("cycle", "cycle-1", {"id": "cycle-1", "assessment_id": "cycle-assessment-2"}),
                 ("checkpoint", "checkpoint-1", {"id": "checkpoint-1"}),
                 ("development_selection", "candidate", {"checkpoint_id": "checkpoint-1"}),
                 ("submission_receipt", "intent-s", {"intent_id": "intent-s"}),
                 ("direct_deposit", "deposit-request", {"deposition_id": 1}),
                 ("verdict_assessment", "verdict-1", {"id": "verdict-1"}),
                 ("verification_task", "task-1", task),
                 ("verification_selection", "task", {"digest": task["digest"]}),
                 ("verification_selection", "verdict", {"id": "verdict-1"}))
        self.render()
        for path in ("study/round-admissions/admission-1.json", "study/round-decisions/decision-b.json",
                     "study/round-assessments/assessment-1.json", "study/decisions/research-2.json",
                     "study/cycle-assessments/cycle-assessment-2.json", "study/checkpoints/checkpoint-1.json",
                     "study/receipts/intent-s.json", "verification/verdicts/verdict-1.json",
                     "verification/tasks/task-1.json"):
            self.assertEqual(self.find_entry("final", path)["status"], "current", path)
        self.assertTrue((self.root / "final/study/receipts/deposit-request.json").is_file())
        for path in ("study/round-decisions/decision-a.json", "study/decisions/research-1.json",
                     "study/cycle-assessments/cycle-assessment-1.json"):
            self.assertEqual(self.find_entry("final", "history/" + path)["status"], "superseded", path)

    def test_native_deliveries_mark_the_current_attempt(self):
        self.put(("native_attempt", "a" * 64, {"objective_id": "objective-000001", "recorded_revision": 1}),
                 ("native_attempt", "b" * 64, {"objective_id": "objective-000001", "recorded_revision": 2}),
                 ("native_attempt_current", "c" * 64, {"native_attempt_digest": "b" * 64}),
                 ("native_closure", "e" * 64, {"objective_id": "objective-000001"}))
        self.render()
        self.assertEqual(self.find_entry("final", "math/deliveries/attempts/" + "b" * 64 + ".json")["status"],
                         "current")
        self.assertEqual(self.find_entry("final", "history/math/deliveries/attempts/" + "a" * 64 + ".json")["status"],
                         "superseded")
        self.assertTrue((self.root / "final/math/deliveries/closures" / ("e" * 64 + ".json")).is_file())

    def test_bundles_are_copied_when_selected_or_deposited_and_listed_otherwise(self):
        def pin(identifier, data):
            artifact = self.artifacts.put(data, "application/pdf")
            bundle = {"id": identifier, "files": {"pdf": {"path": "draft/paper.pdf", "artifact": artifact},
                                                  "sources": None}}
            return dict(bundle, digest=digest(bundle))
        first, second = pin("bundle-1", b"%PDF-first"), pin("bundle-2", b"%PDF-second")
        large = pin("bundle-3", b"%PDF-" + b"x" * (output_views.COPY_LIMIT_BYTES + 1))
        unread = pin("bundle-4", b"%PDF-unread")
        upload = self.artifacts.put(b"%PDF-second", "application/pdf")
        self.put(*[("publication_bundle", bundle["id"], bundle) for bundle in (first, second, large, unread)],
                 ("publication_receipt", "intent-1", {"intent_id": "intent-1", "bundle_digest": first["digest"],
                  "prepared_revision": 1, "uploads": []}),
                 ("publication_receipt", "intent-2", {"intent_id": "intent-2", "bundle_digest": second["digest"],
                  "prepared_revision": 2, "uploads": [{"name": "paper.pdf", "artifact": upload}]}),
                 ("publication_selection", "bundle", {"id": "bundle-3"}))
        self.render()
        self.assertEqual((self.root / "final/study/manuscripts/bundle-3/files/draft/paper.pdf").read_bytes(),
                         self.artifacts.read(large["files"]["pdf"]["artifact"]))
        self.assertEqual(self.find_entry("final", "study/manuscripts/bundle-2/bundle.json")["status"], "current")
        self.assertEqual((self.root / "final/history/study/manuscripts/bundle-1/files/draft/paper.pdf").read_bytes(),
                         b"%PDF-first")
        self.assertEqual(self.read_json("intermediate/study/bundles/bundle-4.json")["id"], "bundle-4")
        self.assertFalse((self.root / "final/study/manuscripts/bundle-4").exists())
        self.assertEqual((self.root / "final/study/deposits/intent-2/files/paper.pdf").read_bytes(), b"%PDF-second")
        self.assertEqual(self.read_json("final/study/deposits/intent-1/receipt.json")["bundle_digest"], first["digest"])

    def test_a_missing_artifact_is_recorded_on_its_entry(self):
        lost = self.artifacts.put(b"%PDF-lost", "application/pdf")
        os.chmod(self.root / lost["path"], 0o644)
        (self.root / lost["path"]).unlink()
        bundle = {"id": "bundle-1", "files": {"pdf": {"path": "draft/paper.pdf", "artifact": lost}}}
        self.put(("publication_bundle", "bundle-1", dict(bundle, digest=digest(bundle))),
                 ("publication_selection", "bundle", {"id": "bundle-1"}))
        summary = self.render()
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(self.find_entry("final", "study/manuscripts/bundle-1/files/draft/paper.pdf")["error"],
                         "artifact_missing")
        self.assertTrue((self.root / "final/study/manuscripts/bundle-1/bundle.json").is_file())

    def test_a_record_over_the_limit_is_summarized(self):
        large = "x" * output_views.COPY_LIMIT_BYTES
        artifact = self.artifacts.put(b"%PDF-small", "application/pdf")
        statement = {"id": "statement-1", "title": "Bounded", "statement": large, "recorded_revision": 1}
        bundle = {"id": "bundle-1", "files": {"pdf": {"path": "draft/paper.pdf", "artifact": artifact}},
                  "review_inputs_artifact": artifact, "candidate": {"inline": large}, "prepared_revision": 2}
        bundle["digest"] = digest(bundle)
        self.put(("problem_statement", "statement-1", statement),
                 ("problem_selection", "current", {"statement_id": "statement-1"}),
                 ("publication_bundle", "bundle-1", bundle), ("publication_selection", "bundle", {"id": "bundle-1"}),
                 ("execution_admission", "admission-1", {"id": "admission-1", "notes": large}))
        self.render()
        summary = self.read_json("final/problem/statements/statement-1.json")
        canonical = json.dumps(statement, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(summary, {
            "summary": True, "kind": "problem_statement", "id": "statement-1", "recorded_revision": 1,
            "status": "current", "bytes": len(canonical), "sha256": digest(statement),
            "fields": {"id": "statement-1", "title": "Bounded", "recorded_revision": 1, "statement": {
                "omitted": True, "bytes": len(large) + 2, "sha256": digest(large)}}})
        fields = self.read_json("final/study/manuscripts/bundle-1/bundle.json")["fields"]
        self.assertEqual((fields["files"], fields["review_inputs_artifact"], fields["candidate"]["omitted"]),
                         (bundle["files"], artifact, True))
        self.assertEqual((self.root / "final/study/manuscripts/bundle-1/files/draft/paper.pdf").read_bytes(),
                         b"%PDF-small")
        run = self.read_json("intermediate/lab/runs/admission-1/run.json")
        self.assertEqual((run["admission"]["summary"], run["admission"]["fields"]["notes"]["omitted"]), (True, True))
        self.assertLess(sum(path.stat().st_size for path in self.root.joinpath("final").rglob("*.json")),
                        output_views.COPY_LIMIT_BYTES)

    def test_evidence_records_have_no_view(self):
        self.put(("work", "work-1", {"id": "work-1"}), ("reading", "reading-1", {"id": "reading-1"}),
                 ("research_intent", "intent-1", {"id": "intent-1"}), ("grand_challenge", "challenge-1", {"id": "c"}))
        self.render()
        for folder in ("final", "intermediate"):
            self.assertEqual(self.read_json(folder + "/index.json")["files"], [])
            self.assertEqual(sorted(path.name for path in (self.root / folder).iterdir()), ["README.md", "index.json"])
            readme = (self.root / folder / "README.md").read_text(encoding="utf-8")
            self.assertLessEqual(len(readme.splitlines()), 30)
            self.assertIn("exactory-research export --kind outputs", readme)


class IntermediateRecordTests(ViewCase):
    def test_candidates_routes_remote_records_and_reports(self):
        self.put(("problem_candidate", "candidate-1", {"id": "candidate-1", "previous": None}),
                 ("problem_candidate", "candidate-2", {"id": "candidate-2", "previous": "candidate-1"}),
                 ("review_route", "route-a", {"id": "route-a"}),
                 ("review_probe_invocation", "probe-1", {"id": "probe-1", "route_id": "route-a"}),
                 ("review_route_probe", "probe-1", {"id": "probe-1", "route_id": "route-a", "status": "completed"}),
                 ("remote_intent", "intent-1", {"id": "intent-1", "status": "complete"}),
                 ("remote_observation", "observation-1", {"id": "observation-1"}),
                 ("manuscript_review", "review-1", {"id": "review-1", "bundle_digest": "x", "reviewed_revision": 4,
                  "assessor": {"id": "assessor-1"}, "core": {"overall": 6, "soundness": 3, "presentation": 3,
                                                             "contribution": 2, "decision": "accept"}}))
        (self.root / ".exactory/citation-check.json").write_text('{"ok": true}\n', encoding="utf-8")
        self.render()
        self.assertEqual(self.find_entry("intermediate", "problem/candidates/candidate-1.json")["superseded_by"],
                         "candidate-2")
        self.assertEqual(self.read_json("intermediate/reviews/routes/route-a/route.json"), {"id": "route-a"})
        self.assertEqual(self.read_json("intermediate/reviews/routes/route-a/probes/probe-1.json")["probe"]["status"],
                         "completed")
        self.assertEqual(self.read_json("intermediate/study/remote-intents/intent-1.json")["status"], "complete")
        self.assertTrue((self.root / "intermediate/study/remote-observations/observation-1.json").is_file())
        self.assertEqual(self.read_json("intermediate/study/score-history.json"), [{
            "review_id": "review-1", "bundle_id": None, "assessor_id": "assessor-1", "reviewed_revision": 4,
            "overall": 6, "soundness": 3, "presentation": 3, "contribution": 2, "decision": "accept"}])
        self.assertEqual(self.read_json("intermediate/checks/citation-check.json"), {"ok": True})
        # A changed report outside the store renders again although no event changed.
        (self.root / ".exactory/citation-check.json").write_text('{"ok": false}\n', encoding="utf-8")
        self.render()
        self.assertEqual(self.read_json("intermediate/checks/citation-check.json"), {"ok": False})

    def test_probes_and_runs_go_to_the_lab(self):
        output = self.artifacts.put(b"result", "text/plain")
        log = self.artifacts.put(b"line 1\nline 2\n", "text/plain; charset=utf-8")
        summary = {"admission_id": "admission-1", "node": "fit", "ok": True, "metric": 0.5}
        self.put(("probe_intent", "probe-a", {"id": "probe-a", "revision": 4}),
                 ("probe", "probe-a", {"id": "probe-a", "outputs": [output], "revision": 5}),
                 ("probe_artifact", "probe-a:" + output["sha256"], {"probe_id": "probe-a", "sha256": output["sha256"]}),
                 ("probe_artifact_pin", "pin-request", {"payload": {"id": "local-1", "path": "work/result.txt"},
                  "origins": [{"probe_id": "probe-a", "sha256": output["sha256"], "roles": ["outputs"]}]}),
                 ("local_artifact", "local-1", {"id": "local-1", "artifact": output}),
                 ("local_artifact", "local-2", {"id": "local-2", "artifact": output}),
                 ("execution_admission", "admission-1", {"id": "admission-1", "recorded_revision": 9}),
                 ("execution", "execution-1", {"id": "execution-1"}),
                 ("execution_outcome", "admission-1", {"execution_id": "execution-1"}),
                 ("execution_observation", "admission-1", {"summary": summary, "log": log}),
                 ("execution", "execution-2", {"id": "execution-2"}))
        self.render()
        probe = self.read_json("intermediate/lab/probes/probe-a/probe.json")
        self.assertEqual((probe["outcome"]["outputs"], [item["id"] for item in probe["local_artifacts"]]),
                         ([output], ["local-1"]))
        self.assertEqual(self.find_entry("intermediate", "lab/probes/probe-a/probe.json")["recorded_revision"], 4)
        self.assertFalse((self.root / "intermediate/lab/artifacts/local-1.json").exists())
        self.assertTrue((self.root / "intermediate/lab/artifacts/local-2.json").is_file())
        run = self.read_json("intermediate/lab/runs/admission-1/run.json")
        self.assertEqual((run["admission"]["id"], run["execution"]["id"]), ("admission-1", "execution-1"))
        # The summary as recorded, which the store keeps with sorted keys.
        self.assertEqual((self.root / "intermediate/lab/runs/admission-1/result.json").read_bytes(),
                         (json.dumps(dict(sorted(summary.items())), ensure_ascii=False, indent=2) + "\n").encode())
        self.assertEqual((self.root / "intermediate/lab/runs/admission-1/log.txt").read_bytes(), b"line 1\nline 2\n")
        self.assertTrue((self.root / "intermediate/lab/executions/execution-2.json").is_file())


class SeriesTests(ViewCase):
    def read_series(self, role, root):
        return self.read_json("intermediate/reviews/" + role + "/" + root + "/series.json")

    def test_turns_are_numbered_linked_and_named_by_run(self):
        self.assign("bar-a")
        self.run_turn("bar-a", "run-1", {"turn": 1})
        self.assign("bar-a-2", prior="bar-a")
        self.run_turn("bar-a-2", "run-2", {"turn": 2})
        self.assign("bar-a-repair", repair="bar-a-2", session="repair-session")
        self.run_turn("bar-a-repair", "run-3", {"turn": 3})
        self.assign("chain", role="native_math", turn=1)
        self.assign("chain-t2", role="native_math", prior="chain", turn=2)
        self.render()
        series = self.read_series("bar", "bar-a")
        self.assertEqual([(turn["turn"], turn["assignment_id"], turn["link"], turn["parent_assignment_id"])
                          for turn in series["turns"]],
                         [(1, "bar-a", "origin", None), (2, "bar-a-2", "source_continuation", "bar-a"),
                          (3, "bar-a-repair", "context_repair", "bar-a-2")])
        self.assertEqual((series["slot_id"], series["turns"][2]["session_id"], series["turns"][2]["slot_id"]),
                         ("bar-a", "repair-session", "bar-a"))
        self.assertEqual(series["turns"][0]["runs"][0]["selected"], True)
        folder = "intermediate/reviews/bar/bar-a/"
        self.assertEqual(self.read_json(folder + "01-bar-a.run-1.json"), {"turn": 1})
        self.assertEqual(self.read_json(folder + "03-bar-a-repair.run-3.json"), {"turn": 3})
        self.assertEqual(self.find_entry("intermediate", "reviews/bar/bar-a/02-bar-a-2.run-2.json")["kind"],
                         "review_attempt")
        chain = self.read_series("native_math", "chain")
        self.assertEqual([turn["turn"] for turn in chain["turns"]], [1, 2])
        self.assertEqual(chain["turns"][1]["runs"], [])

    def test_a_replacement_shares_the_number_of_the_turn_it_replaces(self):
        self.assign("chain", role="native_math", turn=1)
        self.run_turn("chain", "chain-run", {"turn": 1})
        self.assign("chain-t2", role="native_math", prior="chain", turn=2)
        self.run_turn("chain-t2", "chain-t2-run", status="failed", error={"code": "transport_timeout"})
        self.assign("chain-t2b", role="native_math", prior="chain", turn=2)
        self.run_turn("chain-t2b", "chain-t2b-run", {"turn": 2})
        self.render()
        turns = self.read_series("native_math", "chain")["turns"]
        self.assertEqual([(turn["turn"], turn["assignment_id"], turn["link"], turn["sibling"]) for turn in turns],
                         [(1, "chain", "origin", None), (2, "chain-t2", "source_continuation", None),
                          (2, "chain-t2b", "replacement", {"of": "chain-t2", "as": "replacement"})])
        failed = turns[1]["runs"][0]
        self.assertEqual((failed["status"], failed["error"], failed["output_file"], failed["selected"]),
                         ("failed", {"code": "transport_timeout"}, None, False))
        names = sorted(path.name for path in (self.root / "intermediate/reviews/native_math/chain").iterdir())
        self.assertEqual(names, ["01-chain.chain-run.json", "02-chain-t2b.chain-t2b-run.json", "series.json"])

    def test_refused_and_unverified_runs_are_kept_and_the_selected_run_is_marked(self):
        self.assign("chain", role="native_math", turn=1)
        self.refuse(self.run_turn("chain", "run-1", {"attempt": 1}))
        self.run_turn("chain", "run-2", {"attempt": 2}, verified=False)
        self.run_turn("chain", "run-3", {"attempt": 3})
        self.render()
        folder = "intermediate/reviews/native_math/chain/"
        for run in (1, 2, 3):
            self.assertEqual(self.read_json(folder + "01-chain.run-%d.json" % run), {"attempt": run})
        runs = self.read_series("native_math", "chain")["turns"][0]["runs"]
        self.assertEqual([(run["run_id"], run["selected"]) for run in runs],
                         [("run-1", False), ("run-2", False), ("run-3", True)])
        self.assertEqual(runs[0]["refusal"]["code"], "invalid_reading_response")
        # A later refusal changes only which run series.json marks; no output file changes its meaning.
        before = (self.root / folder / "01-chain.run-3.json").read_bytes()
        self.refuse(self.run_turn("chain", "run-4", {"attempt": 4}))
        self.refuse({"id": "run-3", "assignment_id": "chain"})
        self.render()
        self.assertEqual((self.root / folder / "01-chain.run-3.json").read_bytes(), before)
        self.assertEqual([run["run_id"] for run in self.read_series("native_math", "chain")["turns"][0]["runs"]
                          if run["selected"]], ["run-2"])

    def test_an_accepted_correction_selects_the_run_that_it_corrected(self):
        self.assign("bar-a")
        self.refuse(self.run_turn("bar-a", "run-1", {"attempt": 1}))
        self.put(("review_output_correction_invocation", "fix-1", {"id": "fix-1", "assignment_id": "bar-a",
                  "diagnostic": {"attempt_id": "run-1"}, "recorded_revision": self.store.revision + 1}))
        self.put(("review_output_correction", "fix-1", {"id": "fix-1", "assignment_id": "bar-a", "status": "completed",
                  "correction_status": "accepted", "correction_error": None, "output": self.put_json({"delta": 1}),
                  "corrected_output": self.put_json({"corrected": True}), "usage": None,
                  "recorded_revision": self.store.revision + 1}))
        self.render()
        folder = "intermediate/reviews/bar/bar-a/"
        self.assertEqual(self.read_json(folder + "01-bar-a.run-1.json"), {"attempt": 1})
        self.assertEqual(self.read_json(folder + "01-bar-a.fix-1.json"), {"delta": 1})
        self.assertEqual(self.read_json(folder + "01-bar-a.fix-1.corrected.json"), {"corrected": True})
        run = self.read_series("bar", "bar-a")["turns"][0]["runs"][0]
        self.assertEqual((run["selected"], run["correction"]["correction_status"], run["correction"]["selected"]),
                         (True, "accepted", True))

    def test_slot_replacements_refreshes_and_adoptions_are_named(self):
        self.assign("bar-a")
        self.run_turn("bar-a", "run-1", status="failed", error={"code": "transport_failed"})
        self.assign("bar-c", slot="bar-a")
        self.run_turn("bar-c", "run-2", {"stage": "bar"})
        self.review("review-c", "bar-c")
        self.assign("bar-c-refresh", prior="bar-c")
        self.render()
        self.assertEqual(self.read_series("bar", "bar-a")["replaced_by_series"], "bar-c")
        series = self.read_series("bar", "bar-c")
        self.assertEqual((series["slot_id"], series["replaced_by_series"]), ("bar-a", None))
        self.assertEqual(series["turns"][0]["adopted_by"], [{"kind": "value_review", "id": "review-c"}])
        self.assertEqual(series["turns"][1]["link"], "problem_refresh")
        self.assertTrue((self.root / "final/reviews/bar/review-c.json").is_file())

    def test_adjudicator_refreshes_stay_in_their_session(self):
        self.assign("adjudicator-1", role="adjudicator")
        self.run_turn("adjudicator-1", "run-1", {"turn": 1})
        self.assign("adjudicator-2", role="adjudicator", session="adjudicator-1", history="adjudicator-1")
        self.put(("review_adjudication", "adjudication-2", {"id": "adjudication-2", "assignment_id": "adjudicator-2"}))
        self.render()
        turns = self.read_series("adjudicator", "adjudicator-1")["turns"]
        self.assertEqual([(turn["turn"], turn["assignment_id"], turn["link"], turn["history_assignment_id"])
                          for turn in turns],
                         [(1, "adjudicator-1", "origin", None), (2, "adjudicator-2", "focused_refresh",
                                                                  "adjudicator-1")])
        self.assertEqual(turns[1]["adopted_by"], [{"kind": "review_adjudication", "id": "adjudication-2"}])
        self.assertFalse((self.root / "intermediate/reviews/adjudicator/adjudicator-2").exists())

    def test_controller_adoptions_come_from_the_controller_index(self):
        self.assign("solver-review", role="native_math")
        self.run_turn("solver-review", "run-1", {"decision": "approve"})
        self.render()
        index = self.root / "final/math/attack/index.json"
        index.parent.mkdir(parents=True)
        index.write_text(json.dumps({"adoptions": [{"review_id": "review-assignment:solver-review",
                                                    "assignment_id": "solver-review",
                                                    "adopting_record": "result_accepted"}]}), encoding="utf-8")
        self.render()
        self.assertEqual(self.read_series("native_math", "solver-review")["turns"][0]["adopted_by"], [{
            "kind": "controller", "index": "final/math/attack/index.json",
            "review_id": "review-assignment:solver-review", "adopting_record": "result_accepted"}])
        self.assertIn({"path": "math/attack/index.json", "type": "controller"},
                      self.read_json("final/index.json")["files"])

    def test_only_the_index_of_a_controller_root_is_a_controller_index(self):
        self.assign("solver-review", role="native_math")
        self.run_turn("solver-review", "run-1", {"decision": "approve"})
        adoption = {"review_id": "review-assignment:solver-review", "assignment_id": "solver-review",
                    "adopting_record": "result_accepted"}
        # An accepted artifact and a node index are also named index.json; neither is the index of a controller root.
        files = {"final/math/attack/index.json": {"adoptions": [adoption]},
                 "final/math/attack/results/acceptance-000001/artifacts/index.json": {"adoptions": [adoption]},
                 "intermediate/math/attack/index.json": {},
                 "intermediate/math/attack/nodes/sample/index.json": {}}
        for relative, value in files.items():
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_text(json.dumps(value), encoding="utf-8")
        self.render()
        self.assertEqual([item["index"] for item in
                          self.read_series("native_math", "solver-review")["turns"][0]["adopted_by"]],
                         ["final/math/attack/index.json"])
        for folder in ("final", "intermediate"):
            index = self.read_json(folder + "/index.json")
            self.assertEqual([entry["path"] for entry in index["files"] if entry["type"] == "controller"],
                             ["math/attack/index.json"], folder)
            self.assertEqual(index["unowned"], [], folder)

    def test_large_and_sealed_outputs_are_listed_by_reference(self):
        self.assign("bar-a")
        large = self.artifacts.put(b"[" + b" " * output_views.COPY_LIMIT_BYTES + b"]", "application/json")
        sealed = self.artifacts.put_sealed(b"{}", "application/json")
        for run_id, output in (("run-large", large), ("run-sealed", sealed)):
            self.put(("review_attempt", run_id, {"id": run_id, "assignment_id": "bar-a", "status": "completed",
                      "error": None, "usage": None, "output": output, "response": None,
                      "recorded_revision": self.store.revision + 1}))
        with mock.patch.object(ArtifactStore, "read", wraps=self.artifacts.read) as read:
            self.render()
        self.assertNotIn(sealed, [call.args[0] for call in read.call_args_list])
        runs = self.read_series("bar", "bar-a")["turns"][0]["runs"]
        self.assertEqual([(run["output"], run["output_file"], run["output_listed"]) for run in runs],
                         [(large, None, "over_1_mib"), (sealed, None, "sealed")])
        self.assertEqual(sorted(path.name for path in (self.root / "intermediate/reviews/bar/bar-a").iterdir()),
                         ["series.json"])


def seed_journey(case):
    """Commits that touch most view groups, in an order whose later commits change the status of earlier records."""
    case.assign("bar-a")
    case.run_turn("bar-a", "run-1", status="failed", error={"code": "transport_failed"})
    case.run_turn("bar-a", "run-2", {"stage": "bar", "turn": 1})
    case.assign("bar-a-2", prior="bar-a")
    case.refuse(case.run_turn("bar-a-2", "run-3", {"stage": "bar", "turn": 2}))
    case.run_turn("bar-a-2", "run-4", {"stage": "bar", "turn": 2, "again": True})
    case.review("review-a", "bar-a-2")
    case.put(("problem_candidate", "candidate-1", {"id": "candidate-1", "previous": None}),
             ("problem_statement", "statement-1", {"id": "statement-1"}),
             ("problem_selection", "current", {"statement_id": "statement-1"}))
    case.put(("problem_candidate", "candidate-2", {"id": "candidate-2", "previous": "candidate-1"}),
             ("problem_statement", "statement-2", {"id": "statement-2"}),
             ("problem_selection", "current", {"statement_id": "statement-2"}))
    case.put(("local_artifact", "local-1", {"id": "local-1"}), ("probe_intent", "probe-a", {"id": "probe-a"}))
    case.put(("probe_artifact_pin", "pin-1", {"payload": {"id": "local-1"}, "origins": [{"probe_id": "probe-a"}]}))
    case.put(("execution", "execution-1", {"id": "execution-1"}))
    case.put(("execution_admission", "admission-1", {"id": "admission-1"}),
             ("execution_outcome", "admission-1", {"execution_id": "execution-1"}))
    bundle = {"id": "bundle-1", "files": {"abstract": {"path": "draft/abstract.txt",
                                                       "artifact": case.artifacts.put(b"Abstract.", "text/plain")}}}
    case.put(("publication_bundle", "bundle-1", dict(bundle, digest=digest(bundle))),
             ("publication_selection", "bundle", {"id": "bundle-1"}))


class RenderTests(ViewCase):
    def test_a_commit_renders_nothing_by_itself(self):
        self.put(("problem_statement", "statement-1", {"id": "statement-1"}))
        self.assertFalse((self.root / "final").exists())
        self.assertFalse((self.root / output_views.MARKER_PATH).exists())

    def test_renders_after_each_commit_equal_one_render_of_a_copy(self):
        seed_journey(self)
        self.render()
        for number in (3, 4):
            self.put(("problem_statement", "statement-%d" % number, {"id": "statement-%d" % number}),
                     ("problem_selection", "current", {"statement_id": "statement-%d" % number}))
            self.render()
        rendered = read_tree(self.root)
        self.assertFalse((self.root / "intermediate/lab/artifacts/local-1.json").exists())
        self.assertFalse((self.root / "intermediate/lab/executions/execution-1.json").exists())
        other = copy_store(self.root, Path(self.temporary.name) / "copy")
        summary = output_views.render_all(other)
        self.assertEqual(read_tree(other.root), rendered)
        self.assertEqual(summary, {"revision": self.store.revision, "errors": 0, "files": {
            folder: sum(1 for path in rendered if path.startswith(folder + "/")) - 2
            for folder in ("final", "intermediate")}})

    def test_a_second_render_writes_nothing(self):
        seed_journey(self)
        self.render()
        with mock.patch.object(output_views, "write_projection", wraps=output_views.write_projection) as write:
            output_views.render_all(self.store)
        self.assertEqual([call.args[1] for call in write.call_args_list], [output_views.MARKER_PATH])
        # A commit that adds one record writes its file and the index of its folder, and nothing else.
        self.put(("problem_statement", "statement-9", {"id": "statement-9"}))
        with mock.patch.object(output_views, "write_projection", wraps=output_views.write_projection) as write:
            self.render()
        written = [call.args[1] for call in write.call_args_list if call.args[1] != output_views.MARKER_PATH]
        self.assertEqual(written, ["final/history/problem/statements/statement-9.json", "final/index.json"])

    def test_a_commit_of_other_kinds_skips_the_model(self):
        seed_journey(self)
        self.render()
        self.put(("work", "work-1", {"id": "work-1"}), ("literature_operation", "operation-1", {"operation": "x"}))
        with mock.patch.object(Store, "read_kinds", side_effect=AssertionError("the model was read")):
            summary = self.render()
        self.assertEqual(summary["revision"], self.store.revision)
        self.assertEqual(output_views.read_rendered_revision(self.store), self.store.revision)

    def test_the_marker_identity_and_changed_owned_files_render_again(self):
        seed_journey(self)
        self.render()
        statement = self.root / "final/problem/statements/statement-2.json"
        original = statement.read_bytes()
        marker = json.loads((self.root / output_views.MARKER_PATH).read_bytes())
        for changed in (dict(marker, plugin_version="0.0.0"), dict(marker, event_digest="0" * 64)):
            # The same size with other bytes, and a removed file: the render checks every owned file.
            statement.write_bytes(original.replace(b"statement-2", b"statement-X"))
            (self.root / "intermediate/problem/candidates/candidate-1.json").unlink()
            (self.root / output_views.MARKER_PATH).write_text(json.dumps(changed), encoding="utf-8")
            with mock.patch.object(Store, "read_kinds", wraps=self.store.read_kinds) as read:
                self.render()
            self.assertEqual(read.call_count, 1)
            self.assertEqual(statement.read_bytes(), original)
            self.assertTrue((self.root / "intermediate/problem/candidates/candidate-1.json").is_file())

    def test_the_renderer_owns_only_its_own_files(self):
        self.put(("local_artifact", "local-1", {"id": "local-1"}), ("probe_intent", "probe-a", {"id": "probe-a"}))
        self.render()
        foreign = {"final/notes.txt": "mine", "final/problem/exports/abc/statement.md": "export",
                   "intermediate/reviews/packets/assignment-1/inputs.json": "{}",
                   "intermediate/legacy/reviews/outputs/turn.json": "{}",
                   "final/math/attack/index.json": "{}", "final/math/attack/objective.json": "{}"}
        for relative, text in foreign.items():
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_text(text, encoding="utf-8")
        (self.root / "final/problem/statements").mkdir(parents=True)
        (self.root / "final/problem/statements/statement-1.json").write_text("mine", encoding="utf-8")
        self.put(("probe_artifact_pin", "pin-1", {"payload": {"id": "local-1"}, "origins": [{"probe_id": "probe-a"}]}),
                 ("problem_statement", "statement-1", {"id": "statement-1"}),
                 ("problem_selection", "current", {"statement_id": "statement-1"}))
        summary = self.render()
        self.assertFalse((self.root / "intermediate/lab/artifacts/local-1.json").exists())
        for relative, text in foreign.items():
            self.assertEqual((self.root / relative).read_text(encoding="utf-8"), text, relative)
        self.assertEqual((self.root / "final/problem/statements/statement-1.json").read_text(encoding="utf-8"), "mine")
        self.assertEqual(summary["errors"], 1)
        index = self.read_json("final/index.json")
        self.assertEqual(index["unowned"], ["notes.txt", "problem/statements/statement-1.json"])
        self.assertIn({"path": "problem/exports/abc", "type": "export"}, index["files"])
        self.assertIn({"path": "math/attack/index.json", "type": "controller"}, index["files"])
        self.assertEqual(self.find_entry("final", "problem/statements/statement-1.json")["error"],
                         "output_view_conflict")
        files = self.read_json("intermediate/index.json")["files"]
        self.assertIn({"path": "legacy/reviews/outputs/turn.json", "type": "legacy"}, files)
        self.assertIn({"path": "reviews/packets/assignment-1", "type": "export"}, files)
        advisory = status_advisories(self.store)[0]
        self.assertEqual((advisory["reason"], advisory["path"], advisory["error"]),
                         ("file_errors", "final/problem/statements/statement-1.json", "output_view_conflict"))

    def test_the_controller_history_tree_belongs_to_the_controller(self):
        self.put(("native_attempt", "a" * 64, {"objective_id": "objective-000001", "recorded_revision": 1}),
                 ("native_attempt", "b" * 64, {"objective_id": "objective-000001", "recorded_revision": 2}),
                 ("native_attempt_current", "c" * 64, {"native_attempt_digest": "b" * 64}))
        controller = {"final/math/attack/index.json": "{}",
                      "final/history/math/attack/reassessments/assessment-000001.json": "{}",
                      "intermediate/math/attack/index.json": "{}"}
        for relative, text in dict(controller, **{"final/history/math/deliveries/notes.txt": "mine"}).items():
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_text(text, encoding="utf-8")
        self.render()
        index = self.read_json("final/index.json")
        # The controller's history files are its own; the deliveries under history/ are this store's.
        self.assertEqual(index["unowned"], ["history/math/deliveries/notes.txt"])
        self.assertEqual([entry["path"] for entry in index["files"] if entry["path"].startswith("history/math/")],
                         ["history/math/deliveries/attempts/" + "a" * 64 + ".json"])
        self.assertIn({"path": "math/attack/index.json", "type": "controller"}, index["files"])
        for relative, text in controller.items():
            self.assertEqual((self.root / relative).read_text(encoding="utf-8"), text, relative)

    def test_every_advisory_carries_the_same_fields(self):
        self.put(("problem_candidate", "candidate-1", {"id": "candidate-1", "previous": None}))
        fields = {"code": "output_views_stale", "explanation": output_views.STALE_VIEWS_EXPLANATION,
                  "action": "exactory-research export --kind outputs", "path": None, "error": None,
                  "revision": self.store.revision}
        self.assertEqual(status_advisories(self.store), [dict(fields, reason="not_rendered", rendered_revision=0)])
        controller = self.root / "attack"
        (controller / ".search").mkdir(parents=True)
        (controller / ".search/tree.json").write_text("{}", encoding="utf-8")
        inner = Store(controller, create=True)
        inner.mutate("fixture.seed", {},
                     lambda tx: tx.put("problem_statement", "statement-1", {"id": "statement-1"}) or {},
                     expected_revision=0, request_id="seed")
        # The inner store renders nothing, so no rendered revision applies to it.
        self.assertEqual(status_advisories(inner), [dict(fields, reason="shared_workspace_root", rendered_revision=None,
                                                         path=str(inner.root.parent / ".exactory/research.sqlite3"),
                                                         revision=inner.revision)])

    def test_two_processes_render_one_store(self):
        seed_journey(self)
        self.render()
        script = ("import sys\nfrom pathlib import Path\nfrom research_harness import output_views\n"
                  "from research_harness.storage import Store\n"
                  "store = Store(Path(sys.argv[1]))\n"
                  "store.mutate('fixture.child', {}, lambda tx: tx.put('problem_statement', 'statement-child', "
                  "{'id': 'statement-child'}) or {}, expected_revision=store.revision, request_id='child')\n"
                  "print('committed', flush=True)\n"
                  "output_views.render_workspace_views(store.root)\n")
        before = self.store.revision
        with output_views._hold_lock(self.store.root):
            child = subprocess.Popen([sys.executable, "-c", script, str(self.root)], cwd=str(PLUGIN_ROOT),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(child.kill)
            self.assertEqual(child.stdout.readline(), b"committed\n")
            time.sleep(0.3)
            # The child has committed and waits for the lock that this process holds.
            self.assertIsNone(child.poll())
            self.assertEqual(output_views.read_rendered_revision(self.store), before)
        self.render()
        _, stderr = child.communicate(timeout=60)
        self.assertEqual((child.returncode, stderr), (0, b""))
        self.assertEqual(output_views.read_rendered_revision(self.store), before + 1)
        self.assertTrue((self.root / "final/history/problem/statements/statement-child.json").is_file())
        other = copy_store(self.root, Path(self.temporary.name) / "copy")
        output_views.render_all(other)
        self.assertEqual(read_tree(other.root), read_tree(self.root))

    def test_a_failed_render_warns_and_stays_advisory(self):
        initialize_research(self.store, {"profile": "research", "target": None, "preparation_policy": "exhaustive-v1"},
                            expected_revision=self.store.revision, request_id="init")
        output_views.render_workspace_views(self.root)
        before = status_report(self.store)
        shutil.rmtree(self.root / "final")
        (self.root / "final").write_text("in the way", encoding="utf-8")
        self.put(("problem_candidate", "candidate-1", {"id": "candidate-1", "previous": None}))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(output_views.render_workspace_views(self.root))
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertIn("exactory-research export --kind outputs", stderr.getvalue())
        report = status_report(self.store)
        self.assertEqual([(item["code"], item["reason"], item["path"], item["error"]) for item in report["advisories"]],
                         [("output_views_stale", "render_failed", "final/README.md", "unsafe_path")])
        self.assertEqual((report["ready"], report["next"], report["obligations"]),
                         (before["ready"], before["next"], before["obligations"]))
        self.assertNotIn("output_views_stale", [item["code"] for item in report["obligations"]])
        summary = cli.next_summary(report)
        self.assertEqual(summary["advisories"], [{"code": "output_views_stale", "reason": "render_failed"}])
        self.assertEqual(summary["next_hint"], cli.next_summary(before)["next_hint"])
        (self.root / "final").unlink()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            main(["export", "--kind", "outputs", "--workspace", str(self.root)])
        self.assertEqual(json.loads(stdout.getvalue())["revision"], self.store.revision)
        self.assertTrue((self.root / "intermediate/problem/candidates/candidate-1.json").is_file())
        self.assertEqual(status_report(self.store)["advisories"], [])

    def test_a_store_at_a_controller_root_renders_beside_it_or_not_at_all(self):
        controller = self.root / "attack"
        (controller / ".search").mkdir(parents=True)
        (controller / ".search/tree.json").write_text("{}", encoding="utf-8")
        store = Store(controller, create=True)
        store.mutate("fixture.seed", {},
                     lambda tx: tx.put("problem_statement", "statement-1", {"id": "statement-1"}) or {},
                     expected_revision=0, request_id="seed")
        # The workspace root also holds a store, so the inner store renders nothing.
        self.assertEqual(output_views.render_pending(store)["reason"], "shared_workspace_root")
        self.assertFalse((self.root / "final").exists())
        self.assertEqual(status_advisories(store)[0]["reason"], "shared_workspace_root")
        alone = Path(self.temporary.name) / "alone"
        (alone / "attack/.search").mkdir(parents=True)
        (alone / "attack/.search/tree.json").write_text("{}", encoding="utf-8")
        store = Store(alone / "attack", create=True)
        store.mutate("fixture.seed", {},
                     lambda tx: tx.put("problem_statement", "statement-1", {"id": "statement-1"}) or {},
                     expected_revision=0, request_id="seed")
        output_views.render_pending(store)
        self.assertTrue((alone / "final/history/problem/statements/statement-1.json").is_file())
        self.assertFalse((alone / "attack/final").exists())


def status_advisories(store):
    return output_views.find_view_advisories(store, store.revision)


class EntryPointTests(ViewCase):
    def write_payload(self, value):
        path = Path(self.temporary.name) / ("payload-%d.json" % self.sequence)
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def run_cli(self, *arguments):
        calls = []
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                main(list(arguments) + ["--workspace", str(self.root)], render_views=calls.append)
            except SystemExit:
                pass
        return calls

    def test_the_dispatcher_renders_once_after_each_recording_command(self):
        payload = self.write_payload({"profile": "research", "target": None, "preparation_policy": "exhaustive-v1"})
        identity = ["--expected-revision", "0", "--request-id", "init"]
        workspace = str(self.root)
        self.assertEqual(self.run_cli("init", "--file", payload, *identity), [workspace])
        # A replay of the committed request renders too, so that a crash between commit and render is caught up.
        self.assertEqual(self.run_cli("init", "--file", payload, *identity), [workspace])
        self.assertEqual(self.run_cli("init", "--file", payload, "--expected-revision", "0", "--request-id",
                                      "other"), [workspace])
        self.assertEqual(self.run_cli("init", "--file", payload, "--check", "--expected-revision", "1",
                                      "--request-id", "checked"), [])
        self.assertEqual(self.run_cli("status"), [])

    def test_no_render_runs_between_a_review_run_start_commit_and_its_reviewer_call(self):
        events = []

        def invoke(store, payload, *, expected_revision, request_id, credential=None):
            store.mutate("review.run_start", payload, lambda tx: tx.put("review_invocation", "run-1", {
                "id": "run-1", "assignment_id": "assignment-1", "recorded_revision": expected_revision + 1}) or {},
                expected_revision=expected_revision, request_id=request_id + ":start")
            events.append(("reviewer call", store.revision))
            store.mutate("review.run", payload, lambda tx: {}, expected_revision=store.revision, request_id=request_id)
            return {"invoked": True}

        payload = self.write_payload({"id": "run-1", "assignment_id": "assignment-1"})
        with mock.patch.object(cli.review_protocol, "invoke_assignment", side_effect=invoke):
            calls = self.run_cli("review-run", "--file", payload, "--expected-revision", "0", "--request-id", "run")
        events.extend(("render", start) for start in calls)
        self.assertEqual(events, [("reviewer call", 1), ("render", str(self.root))])
        self.assertFalse((self.root / "final").exists())

    def test_the_lab_entry_point_renders_once(self):
        lab = load_bin_module("exactory-lab", "exactory_lab_views")
        workspace = Path(self.temporary.name) / "study"
        calls = []
        arguments = ["exactory-lab", "init", "--dir", str(workspace), "--slug", "curvature"]
        with mock.patch.object(sys, "argv", arguments), contextlib.redirect_stdout(io.StringIO()):
            lab.main(render_views=calls.append)
        self.assertEqual(calls, [str(workspace)])

    def test_a_failed_workspace_lookup_leaves_the_command_result_unchanged(self):
        payload = self.write_payload({"profile": "research", "target": None, "preparation_policy": "exhaustive-v1"})
        # As Path.cwd() raises in a deleted current directory.
        lookup = mock.patch.object(output_views, "find_workspace",
                                   side_effect=FileNotFoundError(2, "No such directory"))
        for request, expected_revision, exit_code in (("init", "0", None), ("stale", "0", 1)):
            stdout, stderr = io.StringIO(), io.StringIO()
            with lookup, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    main(["init", "--file", payload, "--expected-revision", expected_revision, "--request-id", request,
                          "--workspace", str(self.root)])
                    code = None
                except SystemExit as error:
                    code = error.code
            self.assertEqual(code, exit_code, request)
            lines = stderr.getvalue().splitlines()
            self.assertIn("Output views were not rendered (FileNotFoundError", lines[-1])
            if exit_code is None:
                self.assertEqual((json.loads(stdout.getvalue())["revision"], len(lines)), (1, 1))
            else:
                self.assertEqual((json.loads(lines[0])["error"]["code"], len(lines)), ("stale_revision", 2))
        self.assertFalse((self.root / "final").exists())


class ProfileTests(LiteratureCase):
    def test_every_profile_reports_stale_views_as_advisory(self):
        verification = self.store
        initialize_research(verification, {"profile": "verification", "target": self.verification_target(),
                                           "preparation_policy": "exhaustive-v1"},
                            expected_revision=verification.revision, request_id="init-verification")
        research = Store(Path(self.temporary.name) / "research", create=True)
        initialize_research(research, {"profile": "research", "target": None, "preparation_policy": "exhaustive-v1"},
                            expected_revision=0, request_id="init-research")
        problem = Store(Path(self.temporary.name) / "problem", create=True)
        initialize_problem(problem, {"slug": "bounded-sequences", "area": "math.CO", "field": "Bounded sequences",
                                     "authors": ["author"]}, expected_revision=0, request_id="init-problem")
        for store in (verification, research, problem):
            before = status_report(store)
            self.assertEqual([item["reason"] for item in before["advisories"]], ["not_rendered"])
            output_views.render_workspace_views(store.root)
            after = status_report(store)
            self.assertEqual(after["advisories"], [])
            self.assertEqual((after["ready"], after["next"], after["obligations"]),
                             (before["ready"], before["next"], before["obligations"]))


@unittest.skipUnless((UNKNOTTING_RECORD / ".exactory/research.sqlite3").is_file(),
                     "Local acceptance check: the stored unknotting record exists only on the owner's machine")
class StoredRecordTests(unittest.TestCase):
    def test_export_renders_a_stored_problem_record(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "record"
        store = copy_store(UNKNOTTING_RECORD, root)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            main(["export", "--kind", "outputs", "--workspace", str(root)])
        self.assertEqual(json.loads(stdout.getvalue())["revision"], store.revision)
        # Both recorded bars are historical: the route retirement repaired their slots.
        self.assertFalse((root / "final/reviews").exists())
        self.assertEqual(sorted(path.name for path in (root / "final/history/reviews/bar").iterdir()),
                         ["problem-bar-a.json", "problem-bar-b.json"])
        records = store.snapshot()["records"]
        turns, runs = set(), {}
        for series_path in (root / "intermediate/reviews").glob("*/*/series.json"):
            for turn in json.loads(series_path.read_bytes())["turns"]:
                turns.add(turn["assignment_id"])
                runs.update((run["run_id"], (series_path.parent, run)) for run in turn["runs"])
        self.assertEqual(turns, set(records["review_assignment"]))
        self.assertEqual(set(runs), set(records["review_attempt"]) | set(records["review_invocation"]))
        for attempt in records["review_attempt"].values():
            folder, run = runs[attempt["id"]]
            if attempt["status"] == "completed" and attempt["output"]["size"] <= output_views.COPY_LIMIT_BYTES:
                self.assertEqual((folder / run["output_file"]).read_bytes(),
                                 ArtifactStore(root).read(attempt["output"]), attempt["id"])
        self.assertEqual(json.loads((root / "final/history/reviews/bar/problem-bar-a.json").read_bytes()),
                         records["value_review"]["problem-bar-a"])
        self.assertEqual(json.loads((root / "final/problem/statements/statement-1.json").read_bytes()),
                         records["problem_statement"]["statement-1"])


if __name__ == "__main__":
    unittest.main()
