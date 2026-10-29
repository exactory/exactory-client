"""Exercise every check command and real fixture preparations without workspace writes."""

import copy
import json
from pathlib import Path
import shutil
import sys
from unittest import mock

from development_fixtures import DevelopmentCase
from integration_fixtures import observed_candidate, prepare_manuscript
from research_harness import cli
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store
from test_research_check_mode import CheckCommandMixin
from test_research_problem_assessment import build_assessment
from test_research_problem_attempt import build_attempt
from test_research_problem_candidates import build_candidate
from test_research_problem_claims import ProblemCase


PLUGIN = Path(__file__).resolve().parents[1]
EXTERNAL_COMMANDS = frozenset((*cli.ACQUISITION, "review-probe", "review-run", "review-output-correction",
                               "reconcile-run", "batches", "export", "recover", "restore"))
OTHER_ARGUMENTS = {
    "batches": ["--destination", "check-batches"],
    "export": [],
    "recover": [],
    "restore": ["--bundle", "0" * 64],
}
REFUSED_OUTCOMES = {
    "admit": "unknown_cycle",
    "assess": "unknown_cycle",
    "availability": "invalid_availability",
    "bind-run": "execution_not_admitted",
    "bind-verdict": "verification_task_required",
    "checkpoint": "unknown_cycle",
    "constitution": "constitution_conflict",
    "contribution-analysis": "publication_bundle_missing",
    "cycle": "objective_mismatch",
    "defer-source": "artifact_missing",
    "intent": "intent_instruction_missing",
    "loop-close": "policy_inapplicable",
    "manuscript": "readiness_required",
    "manuscript-prediction": "publication_bundle_missing",
    "manuscript-review": "publication_bundle_missing",
    "probe": "probe_intent_missing",
    "probe-intent": "artifact_missing",
    "problem-decision": "problem_minimum_unmet",
    "problem-formal-check": "problem_formal_check_unbound",
    "problem-openness": "review_context_unverified",
    "problem-posted": "readiness_required",
    "problem-slate": "problem_slate_incomplete",
    "publication-scope": "publication_scope_stale",
    "read-batch": "invalid_batch",
    "research-decision": "strategy_reference_missing",
    "research-lead": "research_intent_missing",
    "result": "unknown_cycle",
    "resume-source": "source_deferral_inapplicable",
    "review": "candidate_checkpoint_missing",
    "review-adjudication": "review_adjudication_unverified",
    "review-assignment": "review_record_missing",
    "review-attempt": "review_record_missing",
    "review-context": "review_record_missing",
    "round": "publication_bundle_missing",
    "round-admit": "publication_bundle_missing",
    "round-assess": "publication_bundle_missing",
    "round-review": "publication_bundle_missing",
    "sample": "policy_inapplicable",
    "scoped-review": "publication_scope_missing",
    "screen-batch": "policy_inapplicable",
    "screening-checkpoint": "policy_inapplicable",
    "select-cohort-abstract": "invalid_cohort_selection",
    "select-publication-scope": "publication_scope_missing",
    "source-impact": "strategy_reference_missing",
    "strategy": "research_intent_missing",
    "target": "objective_locked",
    "value-review": "strategy_reference_missing",
    "work-item": "research_intent_missing",
}


class CommandTableCheckTests(CheckCommandMixin, DevelopmentCase, ProblemCase):
    def setUp(self):
        super().setUp()
        self.checked_outcomes = {}
        self.audit_stage = "initial"

    def assert_command_preserves_workspace(self, command, payload=None, *, accepted=False, arguments=()):
        workspace_before = self.read_workspace()
        revision = self.store.revision
        find_executable = shutil.which

        def find_cli(name, *args, **kwargs):
            return sys.executable if name in ("codex", "claude") else find_executable(name, *args, **kwargs)

        with mock.patch("research_harness.http.HttpClient.get",
                        side_effect=AssertionError("Check acquired a source")), \
                mock.patch("research_harness.review_transport.send_request",
                           side_effect=AssertionError("Check dispatched a reviewer")), \
                mock.patch("research_harness.review_codex_transport.shutil.which", side_effect=find_cli), \
                mock.patch("research_harness.review_codex_transport._run_owned",
                           side_effect=AssertionError("Check started Codex")), \
                mock.patch("research_harness.review_codex_framed_transport._run_owned",
                           side_effect=AssertionError("Check started framed Codex")), \
                mock.patch("research_harness.review_claude_transport._run_owned",
                           side_effect=AssertionError("Check started Claude")):
            if command in OTHER_ARGUMENTS:
                response = self.invoke_arguments([command, *arguments, "--workspace", str(self.root), "--check"])
            else:
                response = self.invoke_command(command, payload)
        self.assertEqual(self.read_workspace(), workspace_before, command + " changed workspace bytes or metadata")
        if accepted:
            self.assert_accepted_check(response, revision)
        elif command in EXTERNAL_COMMANDS:
            self.assertEqual(response[0], 1, response[2])
            self.assertEqual(json.loads(response[2])["error"]["code"], "check_unsupported")
        else:
            self.assertIn(response[0], (0, 1), response[2])
            if response[0] == 0:
                self.assert_accepted_check(response, revision)
            else:
                self.assertEqual(json.loads(response[2])["error"]["code"], REFUSED_OUTCOMES[command])
        outcome_code = "accepted" if response[0] == 0 else json.loads(response[2])["error"]["code"]
        if not accepted and command not in EXTERNAL_COMMANDS:
            self.assertEqual(outcome_code, REFUSED_OUTCOMES.get(command, "accepted"))
        self.checked_outcomes.setdefault(command, []).append((self.audit_stage, outcome_code))

    def mutate(self, function, payload, **kwargs):
        command = next((name for name, operation in cli.OPERATIONS.items() if operation is function), None)
        if command is not None:
            with self.subTest(stage=self.audit_stage, command=command, payload_id=payload.get("id")):
                self.assert_command_preserves_workspace(command, payload, accepted=True)
        return super().mutate(function, payload, **kwargs)

    def build_workspace(self, name):
        self.root = Path(self.temporary.name) / name
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        self.sequence = 0

    def test_check_preserves_every_command_workspace(self):
        examples = json.loads((PLUGIN / "docs/research-cli-examples.json").read_text(encoding="utf-8"))
        authored_path = self.root / "authored.txt"
        authored_path.write_bytes(b"Authored check audit bytes.\n")
        for command, payload in (
                ("init", {"profile": "research", "target": None}),
                ("problem-init", examples["problem-init"]),
                ("adopt", dict(examples["adopt"], files=["authored.txt"])),
                ("artifact", {"id": "authored", "path": "authored.txt", "media_type": "text/plain"})):
            with self.subTest(stage=self.audit_stage, command=command):
                self.assert_command_preserves_workspace(command, payload, accepted=True)

        self.audit_stage = "preparation"
        self.prepared_study()
        for command in (*cli.OPERATIONS, *cli.ACQUISITION):
            if command.startswith("problem-") or command in self.checked_outcomes:
                continue
            with self.subTest(stage=self.audit_stage, command=command):
                self.assert_command_preserves_workspace(command, examples[command])
        for command, arguments in OTHER_ARGUMENTS.items():
            with self.subTest(stage=self.audit_stage, command=command):
                self.assert_command_preserves_workspace(command, arguments=arguments)

        self.audit_stage = "candidate"
        execution = observed_candidate(self)
        imported_execution = copy.deepcopy(execution)
        imported_execution["id"] = "audit-imported-result"
        imported_execution["origin"] = dict(examples["result"]["origin"], original={
            "run_id": "audit-external-run", "executed_at": None,
            "provenance": self.artifacts.put(b"Authored external run provenance.\n", "text/plain")})
        self.assert_command_preserves_workspace("result", imported_execution, accepted=True)
        draft_directory = self.root / "draft"
        draft_directory.mkdir()
        (draft_directory / "paper.pdf").write_bytes(b"%PDF-1.4\n% Authored check audit fixture.\n%%EOF")
        (draft_directory / "abstract.txt").write_text("The finite bound was enumerated.", encoding="utf-8")
        (draft_directory / "references.bib").write_text("@article{bounded,title={Authored bound}}\n", encoding="utf-8")
        prepare_manuscript(self, stop=True)

        self.audit_stage = "problem"
        self.build_workspace("problem")
        self.initialize_problem()
        statement = self.select_statement()
        reading = self.record_full_reading()
        self.mutate(cli.OPERATIONS["problem-candidate"], build_candidate())
        self.mutate(cli.OPERATIONS["problem-value"], self.build_value([reading]))
        self.mutate(cli.OPERATIONS["problem-assessment"], build_assessment(statement))
        self.mutate(cli.OPERATIONS["problem-attempt"], build_attempt(reading=reading))
        for command in cli.OPERATIONS:
            if command.startswith("problem-") and command not in self.checked_outcomes:
                with self.subTest(stage=self.audit_stage, command=command):
                    self.assert_command_preserves_workspace(command, examples[command])

        parser = cli.build_parser()
        command_action = next(action for action in parser._actions if hasattr(action, "choices") and action.choices)
        check_commands = {name for name, command_parser in command_action.choices.items()
                          if any("--check" in action.option_strings for action in command_parser._actions)}
        self.assertEqual(set(self.checked_outcomes), check_commands)
        self.assertEqual(check_commands, set(cli.OPERATIONS) | set(cli.ACQUISITION) | set(OTHER_ARGUMENTS))
