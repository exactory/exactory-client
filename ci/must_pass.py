#!/usr/bin/env python3
"""List and run the test modules that must pass before the pull request of a release.

The set has three parts.

Always: the modules of ALWAYS. They compare the harness with frozen values, or they pin a fixture, a version or a
release file, so a change far from them can break them. When the diff changes a file of research_harness/ or of
tests/fixtures/, the modules of HARNESS_PINS join them: they pin the certificates of the changed code and take
long.

From the files of tests/ that the diff changes: each changed test module, and each test module that reaches a
changed file of tests/ through the imports between the files of tests/. So a test that uses a changed fixture
builder through two other test modules is in the set.

From the other files that the diff changes: each test module that imports a changed module of research_harness,
ci or tools, and each test module whose text names a changed file of another kind (the file name of a document or
of a fixture, the quoted name of a command of bin/). This part takes the direct users only. The full suites of the
CI cover the rest.

    python3 ci/must_pass.py list [--base origin/main]
    python3 ci/must_pass.py run [--base origin/main] [--python EXECUTABLE ...] [--jobs N] [--logs DIRECTORY]

run starts one process for each module and each Python executable, through ci/unittest_shards.py run-modules, so
each result is the result of a CI shard. It ends with status 1 when a module fails. The tests of
skills/math-solver/harness have their own command and are not in the set.
"""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
# The modules that compare with frozen values or pin a fixture, a version or a release file.
ALWAYS = ("test_capacity_packet_inventory", "test_codex", "test_incident_ledger", "test_manifest",
          "test_packet_call_rows", "test_release_records", "test_research_guidance", "test_review_request_parity",
          "test_series_code_reach")
# The slow modules that pin the certificates of the harness code. They run first, because they end last.
HARNESS_PINS = ("test_capacity_guard", "test_continuity_guard", "test_continuity_guard_stage3",
                "test_capacity_guard_fixes")
MODULE_PACKAGES = ("research_harness", "ci", "tools")


def list_changed_files(root, base):
    """The files that differ from the merge base with the base, committed or not, and the untracked files."""
    def read_git(*arguments):
        return subprocess.run(("git",) + arguments, cwd=root, check=True, text=True,
                              stdout=subprocess.PIPE).stdout.split("\n")
    merge_base = read_git("merge-base", base, "HEAD")[0]
    names = read_git("diff", "--name-only", merge_base) + read_git("ls-files", "--others", "--exclude-standard")
    return sorted({name for name in names if name})


def read_imported_names(source):
    """Each module that the source imports, and each name that it imports from a module, as dotted names."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(node.module + "." + alias.name for alias in node.names)
    return names


def describe_test_files(root):
    """The text and the imported names of each Python file of tests/, by its module name."""
    files = {}
    for path in sorted((root / "tests").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        files[path.stem] = {"source": source, "imports": read_imported_names(source)}
    return files


def changes_test_file(name, changed):
    return "tests/" + name + ".py" in changed


def uses_changed_file(file, changed):
    """Whether a file of tests/ imports a changed module, or names in its text a changed file of another kind."""
    for changed_name in changed:
        path = Path(changed_name)
        if path.parent == Path("tests") and path.suffix == ".py":
            continue
        if path.suffix == ".py" and path.parts[0] in MODULE_PACKAGES:
            parts = path.with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            if any(imported == module or imported.startswith(module + ".") for imported in file["imports"]):
                return True
        elif path.parts[0] == "bin":
            # A test loads a command of bin/ by its file name, which the text holds as a string of its own.
            if re.search("[\"']" + re.escape(path.name) + "[\"']", file["source"]):
                return True
        elif path.name in file["source"]:
            return True
    return False


def find_touched_modules(files, changed):
    """The test modules that reach a changed file of tests/ through their imports, and the test modules that use
    a changed file of another kind themselves."""
    importers = {name: set() for name in files}
    for name, file in files.items():
        for imported in file["imports"]:
            parts = imported.split(".")
            target = parts[1] if parts[0] == "tests" and len(parts) > 1 else parts[0]
            if target in importers and target != name:
                importers[target].add(name)
    reached = {name for name in files if changes_test_file(name, changed)}
    pending = list(reached)
    while pending:
        for importer in importers[pending.pop()] - reached:
            reached.add(importer)
            pending.append(importer)
    reached.update(name for name, file in files.items() if uses_changed_file(file, changed))
    return {name for name in reached if name.startswith("test_")}


def select_modules(root, changed):
    """The must-pass set for a diff, with the slow pins first: the fixed modules and the modules of the diff."""
    files = describe_test_files(root)
    missing = [name for name in ALWAYS + HARNESS_PINS if name not in files]
    if missing:
        raise SystemExit("ALWAYS or HARNESS_PINS names test modules that tests/ does not hold: " + ", ".join(missing))
    pins = HARNESS_PINS if any(name.startswith(("research_harness/", "tests/fixtures/")) for name in changed) else ()
    return list(pins) + sorted((set(ALWAYS) | find_touched_modules(files, changed)) - set(pins))


def run_module(root, executable, module, log_path):
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        status = subprocess.run((executable, "ci/unittest_shards.py", "run-modules", "tests." + module), cwd=root,
                                stdout=log, stderr=subprocess.STDOUT).returncode
    return executable, module, status, time.monotonic() - started


def run_modules(root, executables, modules, jobs, logs):
    """Run each module with each executable, one process each, and return the count of failed runs."""
    runs = []
    for index, executable in enumerate(executables):
        directory = logs / ("python-%d" % index)
        directory.mkdir(parents=True, exist_ok=True)
        runs.extend((executable, module, directory / (module + ".log")) for module in modules)
    failed_count = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        # Each result is printed when its run ends, so the slow pins that start first do not hold back the others.
        for finished in as_completed([pool.submit(run_module, root, *run) for run in runs]):
            executable, module, status, seconds = finished.result()
            failed_count += status != 0
            print("%s  %s  %s  %.0f s" % ("ok    " if status == 0 else "FAILED", module, executable, seconds), flush=True)
    return failed_count


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "run"):
        command = commands.add_parser(name)
        command.add_argument("--base", default="origin/main", help="The branch that the release merges into.")
    run = commands.choices["run"]
    run.add_argument("--python", action="append", help="A Python executable. Give the option once for each version.")
    run.add_argument("--jobs", type=int, default=4, help="The count of test processes at the same time.")
    run.add_argument("--logs", type=Path, help="The directory for one log file of each run.")
    args = parser.parse_args()
    changed = list_changed_files(ROOT, args.base)
    modules = select_modules(ROOT, changed)
    if any(name.startswith("skills/math-solver/harness/") for name in changed):
        print("The diff changes skills/math-solver/harness. Run its tests with their own command.", file=sys.stderr)
    if args.command == "list":
        print("\n".join(modules))
        return 0
    logs = args.logs or Path(tempfile.mkdtemp(prefix="must-pass-"))
    executables = args.python or [sys.executable]
    failed_count = run_modules(ROOT, executables, modules, args.jobs, logs)
    print("%d modules, %d Python executables, %d failed runs. Logs: %s"
          % (len(modules), len(executables), failed_count, logs))
    return 1 if failed_count else 0


if __name__ == "__main__":
    sys.exit(main())
