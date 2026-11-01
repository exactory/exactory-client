"""Exercise every check command and real fixture preparations without workspace writes."""

import copy
import argparse
from contextlib import contextmanager
import importlib.machinery
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
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
                               "reconcile-run", "batches", "export", "recover", "restore", "strategy-packet"))
OTHER_ARGUMENTS = {
    "batches": ["--destination", "check-batches"],
    "export": [],
    "recover": [],
    "restore": ["--bundle", "0" * 64],
    "strategy-packet": ["--role", "standalone", "--reviewer-id", "check-reviewer"],
}

READ_ONLY_COMMANDS = {
    "exactory-research": frozenset((name,) for name in (
        "status", "next", "obligations", "population-query", "policy-report", "gate", "check-claims", "example",
        "research-decision-assess", "review-route-assess")),
    "exactory-lab": frozenset({("keys",), ("state", "show"), ("colab-status",)}),
    "exactory-draft": frozenset(),
    "exactory-math": frozenset({("check-problem",), ("budget",), ("status",), ("task", "list"),
                                ("search", "status"), ("search", "next"), ("search", "strategy-context")}),
}
MATH_STORE_COMMANDS = frozenset({"pause", "side-interval", "retreat", "interpret", "render"})


@contextmanager
def read_command_parsers():
    harness_path = PLUGIN / "skills/math-solver/harness"
    with mock.patch.object(sys, "path", [str(harness_path), *sys.path]), mock.patch.dict(sys.modules):
        modules = {}
        for name in ("exactory-lab", "exactory-draft", "exactory-math"):
            loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(PLUGIN / "bin" / name))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            module = importlib.util.module_from_spec(spec)
            loader.exec_module(module)
            modules[name] = module
        modules["exactory-math"] = modules["exactory-math"]._load_harness()
        yield {"exactory-research": cli.build_parser(),
               "exactory-math": modules["exactory-math"].build_parser(),
               "exactory-lab": modules["exactory-lab"]._build_parser(),
               "exactory-draft": modules["exactory-draft"]._build_parser()}


def iter_command_parsers(parser, path=()):
    groups = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    if not groups:
        yield path, parser
    for group in groups:
        for name, child in group.choices.items():
            yield from iter_command_parsers(child, (*path, name))


def build_required_arguments(parser, payload_path):
    arguments = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if action.option_strings and not action.required and action.dest not in ("expected_revision", "request_id"):
            continue
        if action.nargs == "?":
            continue
        value = (str(payload_path) if action.dest in ("file", "spec") else
                 str(next(iter(action.choices))) if action.choices else
                 "0" if action.type in (int, float) else "check-input")
        if action.option_strings:
            arguments.append(action.option_strings[0])
        arguments.append(value)
    return arguments


class ParserCheckTests(CheckCommandMixin, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "workspace"
        self.root.mkdir()
        self.store = Store(self.root, create=True)
        self.payload_path = self.root.parent / "input.json"
        self.payload_path.write_text("{}", encoding="utf-8")

    def invoke_program(self, program, arguments):
        environment = dict(os.environ, PYTHONPATH=str(PLUGIN), EXACTORY_API_KEY="", PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, str(PLUGIN / "bin" / program), *arguments], cwd=self.root,
                              env=environment, capture_output=True, text=True, timeout=20)

    def test_every_writing_parser_accepts_check(self):
        with read_command_parsers() as parsers:
            for program, parser in parsers.items():
                commands = dict(iter_command_parsers(parser))
                self.assertLessEqual(READ_ONLY_COMMANDS[program], commands.keys())
                for path, command_parser in commands.items():
                    if path in READ_ONLY_COMMANDS[program]:
                        continue
                    with self.subTest(program=program, command=path):
                        self.assertTrue(any("--check" in action.option_strings for action in command_parser._actions),
                                        "Writing command lacks --check: " + program + " " + " ".join(path))

    def test_external_checks_refuse_without_workspace_writes(self):
        with read_command_parsers() as parsers:
            for program, parser in parsers.items():
                for path, command_parser in iter_command_parsers(parser):
                    if path in READ_ONLY_COMMANDS[program]:
                        continue
                    if (program == "exactory-research" and path[0] not in EXTERNAL_COMMANDS
                            or program == "exactory-lab" and path in {("state", "set"), ("decide",)}
                            or program == "exactory-math" and path[0] == "search"
                            and path[1] in MATH_STORE_COMMANDS):
                        continue
                    with self.subTest(program=program, command=path):
                        arguments = [*path, *build_required_arguments(command_parser, self.payload_path), "--check"]
                        before = self.read_workspace()
                        result = self.invoke_program(program, arguments)
                        self.assertEqual(self.read_workspace(), before)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertEqual(json.loads(result.stderr)["error"]["code"], "check_unsupported")

    def test_lab_store_checks_preserve_acceptance_refusal_and_replay_without_projection_writes(self):
        initialized = self.invoke_program("exactory-lab", ["init", "--slug", "checked-study"])
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.store = Store(self.root)
        for arguments in (["state", "set", "--status", "running"],
                          ["decide", "--decision", "Keep the plan", "--why", "The evidence still supports it"]):
            with self.subTest(command=arguments[:2]):
                revision = self.store.revision
                identity = ["--expected-revision", str(revision), "--request-id", "checked-" + arguments[0]]
                before = self.read_workspace()
                checked = self.invoke_program("exactory-lab", [*arguments, *identity, "--check"])
                self.assertEqual(self.read_workspace(), before)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                self.assertEqual(json.loads(checked.stdout)["check"], "accepted")
                self.assertEqual(json.loads(checked.stdout)["revision"], revision)
                written = self.invoke_program("exactory-lab", [*arguments, *identity])
                self.assertEqual(written.returncode, 0, written.stderr)
                before = self.read_workspace()
                replayed = self.invoke_program("exactory-lab", [*arguments, *identity, "--check"])
                self.assertEqual(self.read_workspace(), before)
                self.assertEqual(replayed.returncode, 0, replayed.stderr)
                self.assertEqual(json.loads(replayed.stdout)["result"], json.loads(written.stdout))
        for arguments in (["state", "set", "--loop-budget", "-1"],
                          ["decide", "--decision", "Keep the plan", "--why", ""]):
            before = self.read_workspace()
            checked = self.invoke_program("exactory-lab", [*arguments, "--check"])
            self.assertEqual(self.read_workspace(), before)
            written = self.invoke_program("exactory-lab", arguments)
            self.assertEqual(checked.returncode, written.returncode, checked.stderr)
            self.assertEqual(json.loads(checked.stderr), json.loads(written.stderr))

    def test_math_store_checks_use_native_validation_without_events_artifacts_or_views(self):
        with read_command_parsers():
            from search_controller.service import Controller
            fixtures = runpy.run_path(str(PLUGIN / "skills/math-solver/harness/tests/search_fixtures.py"))
            attack_root = self.root / "attack"
            attack_root.mkdir()
            controller = Controller(attack_root)
            controller.command("init", {"contract": fixtures["contract"]()}, 0, "initialize")
            (attack_root / ".search/tree.lock").unlink()
            for command, spec in (("render", {}),
                                  ("pause", {"reason": "Check the evidence"})):
                spec_path = self.root.parent / "native-spec.json"
                spec_path.write_text(json.dumps(spec), encoding="utf-8")
                revision = controller.status()["revision"]
                arguments = ["--attack-root", str(attack_root), "search", command,
                             "--expected-revision", str(revision), "--request-id", "checked-" + command]
                if command == "pause":
                    arguments.extend(["--spec", str(spec_path)])
                before = self.read_workspace()
                checked = self.invoke_program("exactory-math", [*arguments, "--check"])
                self.assertEqual(self.read_workspace(), before)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                self.assertEqual(json.loads(checked.stdout)["check"], "accepted")
                self.assertEqual(json.loads(checked.stdout)["revision"], revision)
                written = self.invoke_program("exactory-math", arguments)
                self.assertEqual(written.returncode, 0, written.stderr)
                before = self.read_workspace()
                replayed = self.invoke_program("exactory-math", [*arguments, "--check"])
                self.assertEqual(self.read_workspace(), before)
                self.assertEqual(json.loads(replayed.stdout), json.loads(written.stdout))
            spec_path.write_text("{}", encoding="utf-8")
            arguments = ["--attack-root", str(attack_root), "search", "pause", "--spec", str(spec_path),
                         "--expected-revision", str(controller.status()["revision"]), "--request-id", "refused-pause"]
            before = self.read_workspace()
            checked = self.invoke_program("exactory-math", [*arguments, "--check"])
            self.assertEqual(self.read_workspace(), before)
            written = self.invoke_program("exactory-math", arguments)
            self.assertEqual(checked.returncode, 1, checked.stderr)
            self.assertEqual(json.loads(checked.stderr), json.loads(written.stderr))
            for command in ("side-interval", "retreat", "interpret"):
                with self.subTest(command=command):
                    arguments = ["--attack-root", str(attack_root), "search", command, "unknown-node",
                                 "--spec", str(spec_path), "--expected-revision",
                                 str(controller.status()["revision"]), "--request-id", "refused-" + command]
                    before = self.read_workspace()
                    checked = self.invoke_program("exactory-math", [*arguments, "--check"])
                    self.assertEqual(self.read_workspace(), before)
                    written = self.invoke_program("exactory-math", arguments)
                    self.assertEqual(checked.returncode, 1, checked.stderr)
                    self.assertEqual(json.loads(checked.stderr), json.loads(written.stderr))

    def test_native_missing_content_reads_do_not_create_namespace_directories(self):
        with read_command_parsers():
            from search_controller.storage import Store as NativeStore
            from search_controller.errors import SearchError
            native_root = self.root / ".search"
            native_root.mkdir()
            store = NativeStore(native_root)
            before = self.read_workspace()
            for read in (store.get_blob, store.get_artifact):
                with self.assertRaises(SearchError):
                    read("0" * 64)
                self.assertEqual(self.read_workspace(), before)

    def test_math_check_refuses_a_corrupt_existing_staged_artifact(self):
        with read_command_parsers():
            from search_controller.service import Controller
            fixtures = runpy.run_path(str(PLUGIN / "skills/math-solver/harness/tests/search_fixtures.py"))
            attack_root = self.root / "attack"
            attack_root.mkdir()
            controller = Controller(attack_root)
            controller.command("init", {"contract": fixtures["contract"]()}, 0, "initialize")
            manual = (attack_root / "SEARCH_TREE.md").read_bytes()
            target = attack_root / ".search/artifacts" / hashlib.sha256(manual).hexdigest()
            target.write_bytes(b"Corrupt existing immutable bytes")
            arguments = ["--attack-root", str(attack_root), "search", "render", "--expected-revision", "1",
                         "--request-id", "preserve-manual"]
            before = self.read_workspace()
            checked = self.invoke_program("exactory-math", [*arguments, "--check"])
            self.assertEqual(self.read_workspace(), before)
            written = self.invoke_program("exactory-math", arguments)
            self.assertEqual(written.returncode, 1, written.stderr)
            self.assertEqual(json.loads(written.stderr)["error"]["code"], "immutable_collision")
            self.assertEqual(checked.returncode, written.returncode, checked.stdout)
            self.assertEqual(json.loads(checked.stderr), json.loads(written.stderr))

    def test_a_symlink_at_a_generated_view_refuses_check_and_write_before_any_event(self):
        with read_command_parsers():
            from search_controller.service import Controller
            fixtures = runpy.run_path(str(PLUGIN / "skills/math-solver/harness/tests/search_fixtures.py"))
            attack_root = self.root / "attack"
            attack_root.mkdir()
            controller = Controller(attack_root)
            controller.command("init", {"contract": fixtures["contract"]()}, 0, "initialize")
            view = attack_root / "SEARCH_TREE.md"
            view.unlink()
            view.symlink_to("missing-view.md")
            arguments = ["--attack-root", str(attack_root), "search", "render", "--expected-revision", "1",
                         "--request-id", "linked-view"]
            before = self.read_workspace()
            checked = self.invoke_program("exactory-math", [*arguments, "--check"])
            self.assertEqual(self.read_workspace(), before)
            written = self.invoke_program("exactory-math", arguments)
            self.assertEqual(written.returncode, 1, written.stderr)
            self.assertEqual(json.loads(written.stderr)["error"]["code"], "unsafe_path")
            self.assertEqual(controller.status()["revision"], 1)
            self.assertEqual(checked.returncode, written.returncode, checked.stdout)
            self.assertEqual(json.loads(checked.stderr), json.loads(written.stderr))

    def test_math_check_refuses_an_unsafe_writer_lock_without_changing_it(self):
        with read_command_parsers():
            from search_controller.service import Controller
            fixtures = runpy.run_path(str(PLUGIN / "skills/math-solver/harness/tests/search_fixtures.py"))
            attack_root = self.root / "attack"
            attack_root.mkdir()
            controller = Controller(attack_root)
            controller.command("init", {"contract": fixtures["contract"]()}, 0, "initialize")
            lock = attack_root / ".search/tree.lock"
            lock.unlink()
            lock.symlink_to("tree.json")
            arguments = ["--attack-root", str(attack_root), "search", "render", "--expected-revision", "1",
                         "--request-id", "unsafe-lock"]
            before = self.read_workspace()
            checked = self.invoke_program("exactory-math", [*arguments, "--check"])
            self.assertEqual(self.read_workspace(), before)
            written = self.invoke_program("exactory-math", arguments)
            self.assertEqual(written.returncode, 1, written.stderr)
            self.assertEqual(json.loads(written.stderr)["error"]["code"], "unsafe_path")
            self.assertEqual(checked.returncode, written.returncode, checked.stdout)
            self.assertEqual(json.loads(checked.stderr), json.loads(written.stderr))

    def test_math_check_refuses_a_directory_at_the_writer_lock_path(self):
        with read_command_parsers():
            from search_controller.service import Controller
            fixtures = runpy.run_path(str(PLUGIN / "skills/math-solver/harness/tests/search_fixtures.py"))
            attack_root = self.root / "attack"
            attack_root.mkdir()
            controller = Controller(attack_root)
            controller.command("init", {"contract": fixtures["contract"]()}, 0, "initialize")
            lock = attack_root / ".search/tree.lock"
            lock.unlink()
            lock.mkdir()
            arguments = ["--attack-root", str(attack_root), "search", "render", "--expected-revision", "1",
                         "--request-id", "lock-directory"]
            before = self.read_workspace()
            checked = self.invoke_program("exactory-math", [*arguments, "--check"])
            self.assertEqual(self.read_workspace(), before)
            written = self.invoke_program("exactory-math", arguments)
            self.assertEqual(written.returncode, 1, written.stderr)
            self.assertEqual(json.loads(written.stderr)["error"]["code"], "storage_error")
            self.assertEqual(checked.returncode, written.returncode, checked.stdout)
            self.assertEqual(json.loads(checked.stderr), json.loads(written.stderr))
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
    "problem-citation-metadata": "problem_citation_work_ineligible",
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
