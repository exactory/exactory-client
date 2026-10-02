"""Distributed research guidance resolves its real files and command interfaces."""

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[1]


def linked_paths(source):
    return {(source.parent / value.split("#", 1)[0]).resolve()
            for value in re.findall(r"\[[^\]]+\]\(([^)]+)\)", source.read_text())
            if not value.startswith(("https://", "http://", "#"))}


def bin_parser(name):
    loader = importlib.machinery.SourceFileLoader("guidance_" + name.replace("-", "_"), str(PLUGIN / "bin" / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module._build_parser()


class ResearchGuidanceTests(unittest.TestCase):
    def test_every_shared_and_generated_skill_links_the_same_root_constitution(self):
        constitution = (PLUGIN / "RESEARCH_CONSTITUTION.md").resolve()
        self.assertTrue(constitution.is_file())
        shared = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
        self.assertIn("literature-review", {path.parent.name for path in shared})
        generated = sorted((PLUGIN / "codex/skills").glob("*/SKILL.md"))
        self.assertEqual({path.parent.name for path in shared}, {path.parent.name for path in generated})
        for path in shared + generated:
            with self.subTest(skill=str(path.relative_to(PLUGIN))):
                self.assertIn(constitution, linked_paths(path))
        for path in generated:
            with self.subTest(entry=path.parent.name):
                self.assertIn((PLUGIN / "codex/README.md").resolve(), linked_paths(path))
                self.assertIn((PLUGIN / "skills" / path.parent.name / "SKILL.md").resolve(), linked_paths(path))

    def test_literature_workflow_and_primary_stages_resolve_the_executable_recipe(self):
        workflow = (PLUGIN / "docs/research-workflow.md").resolve()
        self.assertTrue(workflow.is_file())
        for name in ("ai-science", "cohort", "literature-review", "ideate", "experiment", "write", "evaluate", "verify", "math-solver"):
            path = PLUGIN / "skills" / name / "SKILL.md"
            with self.subTest(skill=name):
                self.assertIn(workflow, linked_paths(path))
        for target in linked_paths(workflow):
            self.assertTrue(target.is_file(), str(target))

    def test_workflow_shell_examples_are_accepted_by_the_actual_command_parsers(self):
        from research_harness.cli import build_parser
        workflow = PLUGIN / "docs/research-workflow.md"
        self.assertTrue(workflow.is_file())
        parsers = {"exactory-research": build_parser(), "exactory-lab": bin_parser("exactory-lab"),
                   "exactory-cohort": bin_parser("exactory-cohort"), "exactory-draft": bin_parser("exactory-draft")}
        observed = set()
        interpreted = set()
        for block in re.findall(r"```sh\n(.*?)```", workflow.read_text(), re.S):
            for line in block.replace("\\\n", " ").splitlines():
                arguments = shlex.split(line, comments=True)
                if not arguments:
                    continue
                # A command started by a named interpreter: INTERPRETER "$(command -v COMMAND)" ARGUMENTS.
                started = re.fullmatch(r"\$\(command -v (\S+)\)", arguments[1]) if len(arguments) > 1 else None
                if started:
                    arguments = [started.group(1)] + arguments[2:]
                self.assertIn(arguments[0], parsers, line)
                if ">" in arguments:
                    arguments = arguments[:arguments.index(">")]
                arguments = ["0" if value == "REVISION" else value for value in arguments]
                with self.subTest(command=line):
                    parsed = parsers[arguments[0]].parse_args(arguments[1:])
                    observed.add((arguments[0], parsed.command))
                    if started:
                        interpreted.add((arguments[0], parsed.command))
        required = {("exactory-research", command) for command in
                    ("target", "read", "search", "cycle", "admit", "bind-run", "assess", "checkpoint", "review", "gate")}
        required.update({("exactory-lab", "init"), ("exactory-lab", "run"), ("exactory-cohort", "freeze")})
        self.assertLessEqual(required, observed)
        # An admission of a venv interpreter binds and launches under that interpreter.
        self.assertLessEqual({("exactory-research", "bind-run"), ("exactory-lab", "run")}, interpreted)

    def test_guidance_states_that_an_earlier_release_binding_starts_the_resolved_interpreter(self):
        documents = {name: " ".join((PLUGIN / name).read_text().split())
                     for name in ("docs/research-workflow.md", "docs/research-cli.md", "skills/experiment/SKILL.md")}
        # A venv binding that 0.47.0 recorded names the resolved file, so its program runs without the venv's packages.
        for name, text in documents.items():
            with self.subTest(document=name):
                self.assertIn("exactory-client 0.47.0 or earlier", text)
        self.assertIn("To run the program with the venv's packages, admit and bind a new run.", documents["docs/research-workflow.md"])

    def test_guidance_names_the_outcome_of_an_interpreter_change_after_the_claim(self):
        documents = {name: " ".join((PLUGIN / name).read_text().split())
                     for name in ("docs/research-workflow.md", "docs/research-cli.md")}
        # Only the worker sees a change after the claim, so the launch cannot report execution_runtime_changed.
        for name, text in documents.items():
            with self.subTest(document=name):
                self.assertIn("If the interpreter changes after the claim, the worker refuses to start the program, "
                              "and the launch returns `execution_recovery_required`.", text)
                self.assertIn("Record that run with `reconcile-run`, giving `resolution: \"interrupted\"` and a reason.", text)

    def test_guidance_states_that_reconcile_run_cannot_record_a_run_of_an_earlier_release_beyond_the_store_bounds(self):
        workflow, reference = (" ".join((PLUGIN / name).read_text().split())
                               for name in ("docs/research-workflow.md", "docs/research-cli.md"))
        # A limit of H13 that the user accepted on 2026-09-30: the store refuses the observation of such a run, so
        # reconcile-run does not complete it, and its strategy admits no further run.
        self.assertIn("One exception is a run that exactory-client 0.47.0 or earlier claimed whose metric makes the "
                      "observation exceed the store bounds.", workflow)
        self.assertIn("For such a run, `reconcile-run` fails with `invalid_input`, the run has an outcome but no "
                      "observation, and its strategy admits no further run.", workflow)
        self.assertIn("`reconcile-run` of such a run without an observation, when the metric nests more than 98 "
                      "levels.", reference)

    def test_workflow_preserves_supported_delivery_and_authorized_objective_change(self):
        workflow = " ".join((PLUGIN / "docs/research-workflow.md").read_text().split())
        self.assertIn("Manuscript development requires both current support for retained claims and an "
                      "independently sufficient realized consequence.", workflow)
        self.assertIn("An unconditional user instruction through writing, deposit or submission can "
                      "support `deliver_requested` with current validity and honest limits.", workflow)
        self.assertIn("Author-selected narrowing alone cannot create that authority.", workflow)
        self.assertIn("Preserve the full objective and all remaining scientific obligations after a limited delivery.", workflow)
        self.assertIn("For an actual authorized objective change, retain the original user instruction "
                      "and use the native `target` authorization and lineage flow.", workflow)
        self.assertIn("do not rewrite an earlier objective or pretend old evidence was collected under the new scope.", workflow)
        self.assertIn("Neither a policy migration nor a limited delivery requires all old cycles to be executed again.", workflow)

    def test_release_manifests_and_notes_describe_the_same_final_version(self):
        for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            self.assertEqual(json.loads((PLUGIN / relative).read_text())["version"], "0.50.1")
        self.assertTrue((PLUGIN / "docs/releases/0.50.1.md").is_file())

    def test_staged_plugin_runs_common_and_native_entrypoints_without_repository_cwd(self):
        from research_harness.cli import ACQUISITION, OPERATIONS
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            plugin = root / "Plugin with spaces"
            shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns(
                ".git", ".superpowers", ".lake", "__pycache__", ".coverage*"))
            workspace = root / "Independent workspace"
            workspace.mkdir()
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            environment.pop("PYTHONPATH", None)

            def invoke(command, *arguments):
                result = subprocess.run([sys.executable, "-B", str(plugin / "bin" / command), *arguments],
                    cwd=workspace, env=environment, capture_output=True, text=True, timeout=20)
                self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout)
                return result.stdout

            examples = json.loads((plugin / "docs/research-cli-examples.json").read_text())
            self.assertEqual(set(examples), set(OPERATIONS) | set(ACQUISITION))
            for operation, expected in examples.items():
                with self.subTest(operation=operation):
                    self.assertEqual(json.loads(invoke("exactory-research", "example", operation)), expected)
            self.assertFalse((workspace / ".exactory").exists())
            invoke("exactory-lab", "init", "--slug", "distribution", "--expected-revision", "0", "--request-id", "init")
            status = json.loads(invoke("exactory-research", "status"))
            self.assertEqual(status["revision"], 1)
            self.assertFalse(status["ready"])
            self.assertEqual(Path(invoke("exactory-math", "skill-dir").strip()), plugin / "skills/math-solver")
            for host in ("skills", "codex/skills"):
                entry = plugin / host / "literature-review/SKILL.md"
                self.assertTrue(entry.is_file())
                self.assertIn(plugin / "RESEARCH_CONSTITUTION.md", linked_paths(entry))


if __name__ == "__main__":
    unittest.main()
