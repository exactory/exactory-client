"""Problem workspaces share tools without inheriting ai-science milestones."""

import copy
import json
from pathlib import Path
import subprocess
import sys

from development_fixtures import DevelopmentCase, PROGRAM
from integration_fixtures import admit_lab
from research_harness import depth, development, execution, literature, probe, reading, resources
from test_research_problem_claims import ProblemCase
from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome
from test_research_source_deferrals import DeferralCase
import test_research_direction_sources as direction_fixtures


PLUGIN = Path(__file__).resolve().parents[1]


class ProblemToolsCase(ProblemCase, DevelopmentCase):
    def prepare_problem(self):
        self.initialize_problem()
        statement = self.select_statement(statement=
            "For every integer n in [0, 3], n squared is at most 9, with equality at n = 3.")
        self.objective = {"id": statement["id"], "statement": statement["statement"]}
        self.links = [self.read_source()]
        self.scope([self.links[0]["version_id"]])

    def run_problem(self, *, reserved_units=1):
        self.prepare_problem()
        plan = self.plan()
        data = {"result": {"values": [0, 1, 4, 9], "bound": 9}, "validation": {"passed": True}}
        body = PROGRAM.decode() + "\nfrom pathlib import Path\nPath('validation.json').write_text(" + repr(json.dumps(data)) + ")\n"
        admission = admit_lab(self, body=body, plan=plan, reserved_units=reserved_units, outputs=[
                {"id": "result", "requirement_id": "measurements", "path": "stdout", "media_type": "application/json"},
                {"id": "validation", "requirement_id": "checks", "path": "validation.json", "media_type": "application/json"}])
        launched = execution.launch_execution(self.store, admission["id"],
            expected_revision=self.store.revision, request_id="problem-launch")
        self.assertTrue(launched["ok"])
        self.mutate(execution.reconcile_execution, {"admission_id": admission["id"]})
        records = self.store.snapshot()["records"]
        observed = next(iter(records["execution"].values()))["payload"]
        return plan, observed


class ProblemExecutionToolsTests(ProblemToolsCase):
    def test_cycle_admit_bind_reconcile_assess_without_ai_science_prerequisites(self):
        plan, observed = self.run_problem()
        assessed = self.mutate(development.assess_cycle,
            self.assessment(plan, observed))["result"]
        records = self.store.snapshot()["records"]
        context = development._Context(records, self.artifacts)
        self.assertEqual(context.objective, self.objective)
        self.assertFalse(context.compatible)
        self.assertFalse(context.managed)
        self.assertIsNone(records["configuration"]["research"]["target"])
        for kind in ("research_intent", "strategy_dossier", "research_decision", "synthesis", "preparation"):
            self.assertFalse(records.get(kind), kind)
        self.assertEqual(assessed["obligations"], [])
        self.assertTrue(assessed["validated_result"])
        self.assertNotIn("claim_dependencies", records["cycle_plan"][plan["id"]])
        self.assertEqual(set(context.dependencies()), {"configuration", "constitution", "objective", "preparation"})

    def test_execution_requires_the_current_statement(self):
        self.prepare_problem()
        plan = self.plan()
        self.select_statement(identifier="statement-2", statement="Does the bound extend to every integer?")
        self.error("objective_mismatch", lambda: self.mutate(development.plan_cycle, plan))

    def test_empirical_assessment_retains_unfinished_objective_obligations(self):
        plan, observed = self.run_problem()
        assessment = self.assessment(plan, observed, development=False)
        assessment.update(objective_status="open", disposition="continue",
            remaining_obligations=["A finite computation supplies no proof of the general problem."])
        assessed = self.mutate(development.assess_cycle, assessment)["result"]
        self.assertTrue(assessed["validated_result"])
        self.assertFalse(assessed["complete"])
        self.assertTrue({"objective_incomplete", "remaining_obligations"} <=
            {item["code"] for item in assessed["obligations"]})
        self.assertFalse(self.store.snapshot()["records"].get("native_closure"))

    def test_execution_requires_statement_and_current_constitution(self):
        self.initialize_problem()
        context = development._Context(self.store.snapshot()["records"], self.artifacts)
        self.error("configuration_not_ready", context.require_ready)
        self.select_statement()
        records = copy.deepcopy(self.store.snapshot()["records"])
        records["configuration"]["research"]["constitution"]["sha256"] = "0" * 64
        context = development._Context(records, self.artifacts)
        self.error("configuration_not_ready", context.require_ready)

    def test_study_execution_still_requires_preparation(self):
        self.mutate(self.api("principles").initialize_research, {"profile": "research",
            "target": {"kind": "objective", "id": "study", "statement": "A complete study objective."}})
        context = development._Context(self.store.snapshot()["records"], self.artifacts)
        self.error("research_not_ready", context.require_ready)


class ProblemSharedToolsTests(DeferralCase, ProblemToolsCase):
    def test_budget_depth_scope_search_and_reading_before_statement(self):
        self.initialize_problem()
        self.mutate(resources.set_budget, {"profile": "research", "purpose": "literature",
            "limits": {unit: None for unit in resources.UNITS}, "reason": "Allow bounded source exploration."})
        self.mutate(depth.record_depth, {"id": "depth", "values": {"loop_abstracts": 125},
            "reason": "Explore neighbouring formulations."})
        version = self.metadata()
        self.scope([version])
        search = self.capture_search(found=[version], verdict="replicate-extend")
        search["cited_work_ids"] = [version]
        self.mutate(literature.record_search, search)
        bundle = self.bundle(version)
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle))
        records = self.store.snapshot()["records"]
        self.assertEqual(depth.resolve_depth_value(records, "loop_abstracts", 100), 125)
        self.assertTrue(reading.fulltext_coverage(records, self.artifacts, version)["complete"])
        self.assertEqual(len(records["literature_search"]), 1)
        self.assertFalse(records.get("problem_statement"))

    def test_availability_and_deferral_keep_missing_sources_unread(self):
        self.initialize_problem()
        root, missing = self.metadata(), self.metadata(2)
        self.scope([root])
        self.require_source(missing)
        self.capture(missing, status=404)
        self.defer(missing)
        records = self.store.snapshot()["records"]
        self.assertEqual(len(records["source_deferral"]), 1)
        self.assertFalse(reading.fulltext_coverage(records, self.artifacts, missing)["complete"])

    def test_availability_qualification_before_statement_does_not_grant_a_reading(self):
        self.initialize_problem()
        version = self.metadata()
        self.scope([version])
        capture = self.capture(version, status=404)
        self.mutate(reading.record_availability, {"id": "unavailable", "profile": "research",
            "version_id": version, "depth": "fulltext", "source_ids": [capture["source_id"]],
            "reason": "The captured origin response reports missing material.",
            "policy": {"id": "terminal-origin", "minimum_attempts": 1,
                "allowed_statuses": [403, 404, 410, 451], "rationale": "Retain the original access failure."}})
        records = self.store.snapshot()["records"]
        self.assertEqual(len(records["availability"]), 1)
        self.assertFalse(reading.fulltext_coverage(records, self.artifacts, version)["complete"])

    def test_probe_before_statement_preserves_budget_and_exploratory_status(self):
        self.initialize_problem()
        self.mutate(probe.record_probe_intent, build_probe_intent(self.artifacts))
        self.mutate(probe.record_probe, build_probe_outcome(self.artifacts))
        records = self.store.snapshot()["records"]
        self.assertFalse(records.get("execution_admission"))
        self.assertFalse(records.get("cycle_assessment"))
        self.assertEqual(resources.account_report(records, "research")["experiment"]["wall_seconds"]["charged"], 0.25)

    def test_openness_packet_delivers_separate_image_bytes_of_a_decisive_reading(self):
        self.initialize_problem()
        selected = self.select_statement()
        version, asset = direction_fixtures.DirectionSourceTests.prepare_visual_reading(self)
        self.scope([version])
        search = self.capture_search(found=[version], verdict="replicate-extend")
        search.update(question=selected["statement"], cited_work_ids=[version])
        self.mutate(literature.record_search, search)
        from research_harness.problem_openness import export_openness
        exported = export_openness(self.store, {"assignment_id": "visual-openness", "reviewer_id": "visual-reviewer",
            "minimum": {"searches": [{"formulation": selected["statement"], "search_id": search["id"]}],
                "settling_work_id": None, "duplicate_resolutions": []}}, self.root / "visual-openness")
        packet = json.loads(self.artifacts.read(exported["native_packet"]))
        self.assertIn(asset["artifact"]["sha256"], {row["digest"] for row in packet["evidence"]})
        from research_harness.evidence import digest
        self.assertIn(digest(selected), {row["digest"] for row in packet["evidence"]})
        from research_harness.review_protocol import build_packet
        assigned = build_packet(self.store.snapshot()["records"], self.artifacts, None, "native_math", "visual-reviewer",
            native_packet=exported["native_packet"])
        self.assertIn(asset["artifact"]["sha256"], {row["artifact"]["sha256"] for row in assigned["evidence"]})


class ProblemDeliveryTextTests(ProblemCase):
    def test_skill_preserves_unrecorded_posts_and_uses_record_export_for_recorded_posts(self):
        text = (PLUGIN / "skills/math-conjecturer/SKILL.md").read_text()
        section = text.split("## Compose and post a problem", 1)[1].split("## Formal statements", 1)[0]
        for required in ("exactory-research gate post", "exactory-research export --kind problem",
                         "exactory-research problem-posted", "problem-export.json", "research record"):
            self.assertIn(required, section)
        self.assertIn("exactory-check lookup --refs-json problem-refs.json", section)
        self.assertIn("exactory problems --query", section)
        self.assertIn("--statement-file statement.md --motivation-file motivation.md", section)
        self.assertIn("new statement", section)

    def test_documents_describe_problem_claim_milestones_and_native_support_limits(self):
        cli_text = (PLUGIN / "docs/research-cli.md").read_text()
        workflow = (PLUGIN / "docs/research-workflow.md").read_text()
        self.assertIn("## Problem creation", cli_text)
        self.assertIn("## Problem creation milestones", workflow)
        for required in ("problem_creation", "problem-formal-check", "problem_evidence_digest",
                         "problem_export_mismatch", "problem_openness_stale", "problem_value_computation_missing"):
            self.assertIn(required, cli_text)
        for document in (cli_text, workflow):
            self.assertIn("| Problem: precisely stated, open, worth solving |", document)
            self.assertIn("empirical", document)

    def test_generated_codex_wrapper_is_current(self):
        result = subprocess.run([sys.executable, str(PLUGIN / "codex/generate.py"), "--check"],
            cwd=PLUGIN, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
