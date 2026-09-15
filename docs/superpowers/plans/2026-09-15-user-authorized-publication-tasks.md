# User-Authorized Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `exactory submit`, `exactory verify`, and `exactory-draft deposit` run whenever the user asks, from any directory; the managed harness records a receipt when the workspace supports one and otherwise prints one stderr line and proceeds.

**Architecture:** Each of the three commands gains one decision point before its first network write: open the surrounding workspace's store if there is one, run every check the managed path performs before its remote write, and take the managed path when they all pass. Any `ResearchError` from those checks selects the direct path (the 0.36.0 request) after one `Managed record skipped (<code>): <message>` line. The two Bash-boundary hooks that duplicated CLI refusals are deleted; the citation gate becomes a printed report.

**Tech Stack:** Python 3.9+ standard library, `unittest`, the plugin's `research_harness` package, the `bin/` CLI scripts.

**Spec:** `docs/superpowers/specs/2026-09-15-user-authorized-publication-design.md`

## Global Constraints

- Python 3.9+ standard library only.
- The SQLite schema stays at `schema_version` 1. Records stay append-only.
- `RESEARCH_CONSTITUTION.md` stays at Version 3 with its current bytes.
- The server wire API is unchanged.
- `exactory verify` keeps refusing a verdict file whose `prediction` is missing or has no `percentile`, before any network call.
- `exactory-draft deposit --production --publish` keeps requiring `--confirm-publish`.
- Both host manifests carry version `0.40.0`. `codex/generate.py --check` passes.
- All artifacts, code, and documentation are English.
- Commit messages are written to a file in the session scratchpad directory (written `<scratchpad>` below) and passed with `git commit -F <file>`; every message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Tests that read the store after a CLI run open a fresh `Store(<workspace>)` from `research_harness.storage` for the assertion, so no cached snapshot from an earlier instance is read.
- Work happens in the worktree `/Users/ryshiro/exactory/plugins/exactory-client-worktrees/user-authorized-publication` on branch `feat/user-authorized-publication`. Run every test command from that directory. Nothing is pushed until the user confirms.

## Test commands

The suite runs with `python3 -m unittest`. One module: `python3 -m unittest tests.test_transport -v`. One test by name: `python3 -m unittest tests.test_transport -k posts_the_verdict -v`. The whole suite takes about 30 minutes on Python 3.9.6; run it once at the end (Task 8). The math-solver harness suite (`skills/math-solver/harness/tests`) is unaffected by this plan and is not run here.

## File map

| File | Change |
| --- | --- |
| `hooks/enforce_citation_check.py`, `hooks/enforce_prediction.py` | Deleted |
| `hooks/hooks.json`, `codex/hooks.json` | The two entries removed; Codex file regenerated |
| `research_harness/integration.py` | `managed_store(start=None)` |
| `research_harness/cli.py` | `note_managed_record_skipped(error)` |
| `research_harness/citation_report.py` | New: `report_citation_gate(workspace, check_command)` |
| `research_harness/submission.py` | `check_verdict_preconditions(store, task, body)` extracted from `_send_verdict` |
| `research_harness/zenodo.py` | `validate_deposit(store, pdf, abstract, sources)` |
| `bin/exactory` | `_run_submit`, `_run_verify` decide between managed and direct paths |
| `bin/exactory-draft` | `_run_deposit` decides; `_deposit_managed`, `_deposit_directly`, `_open_new_version_draft`, `_mark_paper_as_preview`, `_save_deposit_state` |
| `skills/submit/SKILL.md`, `skills/verify/SKILL.md`, `skills/deposit/SKILL.md` | Rewritten from the 0.36.0 text |
| `README.md`, `docs/research-workflow.md`, `docs/research-cli.md`, `codex/README.md` | Paragraphs on the three commands |
| `docs/releases/0.40.0.md`, `docs/testing/user-authorized-publication.md` | New |
| `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` | Version `0.40.0` |
| `tests/test_hooks.py`, `tests/test_codex.py`, `tests/test_transport.py`, `tests/test_draft.py` | Changed |
| `tests/test_user_authorized_publication.py` | New: the three shared helpers |

---

### Task 1: Remove the two Bash gate hooks

**Files:**
- Delete: `hooks/enforce_citation_check.py`, `hooks/enforce_prediction.py`
- Modify: `hooks/hooks.json:14-37` (the `Bash|exec_command` group)
- Regenerate: `codex/hooks.json`
- Test: `tests/test_hooks.py`, `tests/test_codex.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `hooks/hooks.json` with the `Bash|exec_command` group holding `guard_experiment_exec.py` and `enforce_decision_log.py` only.

- [ ] **Step 1: Delete the two scripts and run the manifest test to see it fail**

```bash
git rm hooks/enforce_citation_check.py hooks/enforce_prediction.py
python3 -m unittest tests.test_hooks -k TestHooksManifest -v
```

Expected: `test_every_command_points_at_an_existing_script_under_the_plugin_root` FAILS on `hooks/enforce_citation_check.py`, and `test_each_hook_is_wired_to_its_designed_event_and_matcher` FAILS with `StopIteration`.

- [ ] **Step 2: Remove the two entries from `hooks/hooks.json`**

Replace the `Bash|exec_command` group with:

```json
      {
        "matcher": "Bash|exec_command",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/guard_experiment_exec.py\"",
            "timeout": 15
          },
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/enforce_decision_log.py\"",
            "timeout": 15
          }
        ]
      },
```

Then regenerate the Codex adapter and confirm it is current:

```bash
python3 codex/generate.py
python3 codex/generate.py --check
```

- [ ] **Step 3: Update the hook tests**

In `tests/test_hooks.py`:

1. Delete the constants `_GATE_SCRIPT_PATH`, `_PREDICTION_GATE_SCRIPT_PATH`, `_SUBMIT_CMD`, `_PRODUCTION_DEPOSIT_CMD`, and `_LOOKUP_CMD` (lines 17, 19, 21-23). Keep `_ADVISORY_SCRIPT_PATH`.
2. Delete the classes `TestCitationGate` (lines 89-179) and `TestPredictionGate` (lines 181-283).
3. In `TestHooksManifest.test_each_hook_is_wired_to_its_designed_event_and_matcher`, replace the first four statements with:

```python
        bash_matcher = next(group for group in self.config["hooks"]["PreToolUse"]
                            if any("guard_experiment_exec.py" in hook["command"] for hook in group["hooks"]))
        self.assertEqual(bash_matcher["matcher"], "Bash|exec_command")
        self.assertEqual([hook["command"].rsplit("/", 1)[1].rstrip('"') for hook in bash_matcher["hooks"]],
                         ["guard_experiment_exec.py", "enforce_decision_log.py"])
        self.assertEqual(bash_matcher["hooks"][0]["timeout"], 15)
```

In `tests/test_codex.py`, replace `test_bash_still_reaches_shared_submission_gate` (line 196) with:

```python
    def test_bash_still_reaches_the_shared_experiment_guard(self):
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory/study.json").write_text("{}")
        self.assert_denied(self.run_hook("guard_experiment_exec.py",
                          "sudo rm -rf /", tool="Bash"), "privilege")
```

- [ ] **Step 4: Run the two modules**

```bash
python3 -m unittest tests.test_hooks tests.test_codex -v
```

Expected: PASS. If `test_codex` reports Codex adapter coverage below 80 percent, run `python3 -m coverage run --rcfile=codex/coverage.ini -m unittest tests.test_codex` and read the missing lines; the replacement test above exercises the same `Bash` translation path the deleted test did.

- [ ] **Step 5: Commit**

Message file `commit-1.txt`:

```
feat: remove the Bash gate hooks for submit, deposit, and verify

The CLIs own the prediction rule and the citation report. The two
PreToolUse hooks repeated a CLI refusal at the shell boundary and
stopped commands the user had asked for.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

```bash
git add hooks/hooks.json codex/hooks.json tests/test_hooks.py tests/test_codex.py
git commit -F <scratchpad>/commit-1.txt
```

---

### Task 2: Shared helpers for the decision point

**Files:**
- Modify: `research_harness/integration.py:24-33` (after `current_store`)
- Modify: `research_harness/cli.py:68-72` (after `add_identity`)
- Create: `research_harness/citation_report.py`
- Test: `tests/test_user_authorized_publication.py`

**Interfaces:**
- Produces:
  - `integration.managed_store(start=None) -> Store | None`: the store of the workspace around `start` (default: the current directory); `None` when there is no workspace or the workspace has no configured store (`migration_required`); any other `ResearchError` propagates.
  - `cli.note_managed_record_skipped(error: ResearchError) -> None`: prints `Managed record skipped (<code>): <message>` to stderr.
  - `citation_report.report_citation_gate(workspace: Path, check_command: Path) -> bool`: runs `python3 <check_command> gate --workspace <workspace>`; on a nonzero exit prints `Citation report: <stderr, stripped>` to stderr; returns whether the gate passed.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_user_authorized_publication.py`:

```python
"""The shared decision point of submit, verify, and deposit: managed when the
workspace supports a receipt, direct otherwise, never refused."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from research_harness.cli import note_managed_record_skipped
from research_harness.citation_report import report_citation_gate
from research_harness.errors import ResearchError
from research_harness.integration import managed_store
from research_harness.storage import Store

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_CHECK_COMMAND_PATH = _PLUGIN_ROOT / "bin" / "exactory-check"


class TestManagedStore(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def test_no_workspace_gives_none(self) -> None:
        self.assertIsNone(managed_store(self.root))

    def test_a_legacy_draft_workspace_without_a_store_gives_none(self) -> None:
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory" / "draft.json").write_text(json.dumps({"title": "Legacy"}))
        self.assertIsNone(managed_store(self.root))

    def test_a_store_without_a_research_configuration_gives_none(self) -> None:
        Store(self.root, create=True)
        self.assertIsNone(managed_store(self.root))

    def test_a_configured_store_is_returned_from_a_subdirectory(self) -> None:
        from integration_fixtures import prepare_research
        case = prepare_research(self.root)
        nested = self.root / "draft" / "figures"
        nested.mkdir(parents=True)
        self.assertEqual(managed_store(nested).revision, case.store.revision)


class TestNote(unittest.TestCase):
    def test_the_note_names_the_code_and_the_message(self) -> None:
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            note_managed_record_skipped(ResearchError("readiness_required", "Publish the reviewed bundle first"))
        self.assertEqual(sink.getvalue(),
                         "Managed record skipped (readiness_required): Publish the reviewed bundle first\n")


class TestCitationReport(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.workspace = Path(scratch.name)
        (self.workspace / ".exactory").mkdir()
        (self.workspace / "draft").mkdir()
        (self.workspace / ".exactory" / "draft.json").write_text(json.dumps({"title": "Report"}))

    def test_a_failing_gate_is_printed_and_reported_as_failed(self) -> None:
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            passed = report_citation_gate(self.workspace, _CHECK_COMMAND_PATH)
        self.assertFalse(passed)
        self.assertTrue(sink.getvalue().startswith("Citation report: "))
        self.assertIn("references.bib", sink.getvalue())

    def test_a_passing_gate_prints_nothing(self) -> None:
        from test_transport import _write_passing_citation_report
        _write_passing_citation_report(self.workspace)
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            passed = report_citation_gate(self.workspace, _CHECK_COMMAND_PATH)
        self.assertTrue(passed)
        self.assertEqual(sink.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the module to see it fail**

```bash
python3 -m unittest tests.test_user_authorized_publication -v
```

Expected: FAIL at import with `ImportError: cannot import name 'note_managed_record_skipped'`.

- [ ] **Step 3: Implement the three helpers**

In `research_harness/integration.py`, after `current_store`:

```python
def managed_store(start=None):
    """The store of the workspace around `start`, or None outside a managed workspace.

    A directory with no workspace, or a workspace with no configured store,
    selects the direct path of submit, verify, and deposit. Any other error is
    the store's own and propagates."""
    workspace = find_workspace(start, required=False)
    if workspace is None:
        return None
    try:
        return current_store(workspace)
    except ResearchError as error:
        if error.code == "migration_required":
            return None
        raise
```

`find_workspace` is already imported in `integration.py` from `.workspace`; add it to that import line if it is not.

In `research_harness/cli.py`, after `add_identity`:

```python
def note_managed_record_skipped(error):
    """One stderr line: the command ran, and the study's receipt was not written."""
    print("Managed record skipped (" + error.code + "): " + error.message, file=sys.stderr)
```

Add `import sys` to the module imports if it is absent.

Create `research_harness/citation_report.py`:

```python
"""The offline citation report that submit and a production deposit print before a remote write."""

import subprocess
import sys


def report_citation_gate(workspace, check_command):
    """Run `exactory-check gate` for the workspace and print a failing report.

    The report informs the user; the calling command continues either way.
    Returns True when the gate passed."""
    completed = subprocess.run(
        [sys.executable, str(check_command), "gate", "--workspace", str(workspace)],
        capture_output=True, text=True)
    if completed.returncode != 0:
        print("Citation report: " + completed.stderr.strip(), file=sys.stderr)
    return completed.returncode == 0
```

- [ ] **Step 4: Run the module**

```bash
python3 -m unittest tests.test_user_authorized_publication -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

Message file `commit-2.txt`:

```
feat: add the shared decision helpers for user-authorized commands

managed_store opens the surrounding workspace's store or returns None,
note_managed_record_skipped prints the one-line note, and
report_citation_gate prints the offline citation report without
stopping the command.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

```bash
git add research_harness/integration.py research_harness/cli.py research_harness/citation_report.py tests/test_user_authorized_publication.py
git commit -F <scratchpad>/commit-2.txt
```

---

### Task 3: `exactory verify` runs from anywhere

**Files:**
- Modify: `research_harness/submission.py:99-130` (`send_verdict`, `_send_verdict`)
- Modify: `bin/exactory:26-33` (imports), `bin/exactory:265-299` (`_run_verify`)
- Test: `tests/test_transport.py:354-409` (`TestVerifyPredictionGate`)

**Interfaces:**
- Consumes: `managed_store`, `note_managed_record_skipped` from Task 2.
- Produces: `submission.check_verdict_preconditions(store, task, body) -> (report, binding)`: every check `_send_verdict` runs before `begin_intent`; raises `ResearchError` (`readiness_required`, `verification_target_mismatch`, `verdict_assessment_required`, `verdict_assessment_stale`, `verdict_body_mismatch`, `verdict_reconciliation_pending`, `verdict_revision_required`) when the workspace cannot record the verdict as it stands.

- [ ] **Step 1: Write the failing tests**

In `tests/test_transport.py`, replace the class `TestVerifyPredictionGate` (keep its three refusal tests unchanged) with `TestVerify`, and add these three tests after `test_verify_refuses_a_prediction_without_a_percentile`:

```python
    def _task(self) -> dict:
        return {"verificationId": _VERIFICATION_ID, "doi": "10.48550/arxiv.2601.00001",
                "source": "arxiv", "sourceId": "2601.00001", "sourceVersion": 1,
                "url": "https://arxiv.org/abs/2601.00001v1", "viewerVerdictId": None}

    def test_verify_without_a_workspace_posts_the_verdict(self) -> None:
        _write_verdict_file(self.scratch_dir / "verdict.json")
        self.responses[("GET", f"/api/v1/tasks/{_VERIFICATION_ID}")] = (self._task(), 200)
        self.responses[("POST", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts")] = (
            {"id": "22222222-2222-4222-8222-222222222222"}, 201)
        stdout_text, stderr_text = self._run_verify()
        self.assertEqual(self.requested_paths,
                         [f"/api/v1/tasks/{_VERIFICATION_ID}", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"])
        self.assertEqual(self.request_bodies[1], json.loads((self.scratch_dir / "verdict.json").read_text()))
        self.assertEqual(json.loads(stdout_text)["id"], "22222222-2222-4222-8222-222222222222")
        self.assertEqual(stderr_text, "")

    def test_verify_in_a_bound_verification_workspace_writes_the_receipt(self) -> None:
        from integration_fixtures import prepare_verification
        from research_harness.verification import bind_verdict, record_task

        case = prepare_verification(self.scratch_dir)
        _write_verdict_file(self.scratch_dir / "verdict.json")
        task = self._task()
        pinned = case.mutate(record_task, {"task": task})["result"]
        case.mutate(bind_verdict, {"id": "transport-verdict", "task_digest": pinned["digest"],
            "body": case.artifacts.put((self.scratch_dir / "verdict.json").read_bytes(), "application/json"),
            "assessment": {"assessor": "transport-independent-verifier",
                "provenance": case.artifacts.put(b"Authored independent verification context.", "text/plain"),
                "independence_basis": "Separate context read the exact source and no other verdicts.", "blind": True,
                "checks": [{"dimension": dimension, "reason": "The exact scoped source supports this separate judgment.",
                            "evidence": [case.linked]} for dimension in ("soundness", "novelty", "impact")]}})
        self.responses[("GET", f"/api/v1/tasks/{_VERIFICATION_ID}")] = (task, 200)
        self.responses[("POST", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts")] = (
            {"id": "22222222-2222-4222-8222-222222222222"}, 201)
        _, stderr_text = self._run_verify()
        self.assertEqual(stderr_text, "")
        self.assertEqual(self.requested_paths,
                         [f"/api/v1/tasks/{_VERIFICATION_ID}", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"])
        from research_harness.storage import Store
        receipts = Store(self.scratch_dir).snapshot()["records"]["verdict_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(next(iter(receipts.values()))["response"]["id"], "22222222-2222-4222-8222-222222222222")

    def test_verify_in_an_unbound_workspace_notes_the_skip_and_posts(self) -> None:
        from integration_fixtures import prepare_verification

        case = prepare_verification(self.scratch_dir)
        _write_verdict_file(self.scratch_dir / "verdict.json")
        self.responses[("GET", f"/api/v1/tasks/{_VERIFICATION_ID}")] = (self._task(), 200)
        self.responses[("POST", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts")] = (
            {"id": "22222222-2222-4222-8222-222222222222"}, 201)
        _, stderr_text = self._run_verify()
        self.assertTrue(stderr_text.startswith("Managed record skipped (verdict_assessment_required): "))
        self.assertEqual(self.requested_paths,
                         [f"/api/v1/tasks/{_VERIFICATION_ID}", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"])
        from research_harness.storage import Store
        self.assertNotIn("verdict_receipt", Store(self.scratch_dir).snapshot()["records"])
```

Delete the old `test_verify_sends_a_verdict_that_carries_the_prediction`; the second test above replaces it.

- [ ] **Step 2: Run to see the new tests fail**

```bash
python3 -m unittest tests.test_transport -k TestVerify -v
```

Expected: the three refusal tests PASS; `test_verify_without_a_workspace_posts_the_verdict` and `test_verify_in_an_unbound_workspace_notes_the_skip_and_posts` FAIL with `ResearchError: migration_required` or `verdict_assessment_required`; the bound test PASSES already.

- [ ] **Step 3: Extract the preconditions in `research_harness/submission.py`**

Replace `_send_verdict` with:

```python
def check_verdict_preconditions(store, task, body):
    """Every check the managed path runs before its remote write.

    Returns the validation report and the intent binding. Raises ResearchError
    when the workspace cannot record this verdict as it stands; the CLI then
    sends the verdict directly."""
    report = validate_verdict(store, task, body)
    binding = {"assessment": report["assessment"], "body": report["body"], "task_identity": task_identity(task),
               "verification_id": task["verificationId"],
               "preparation_digest": report["preparation_digest"]}
    records = store.snapshot()["records"]
    prior = [r for r in records.get("remote_intent", {}).values()
             if r["kind"] == "verdict" and r["binding"]["verification_id"] == task["verificationId"]]
    uncertain = next((r for r in prior if r["pending"] is not None), None)
    if uncertain is not None:
        _observation(store, uncertain["id"], "verdict_response_unknown", {"verificationId": task["verificationId"], "viewerVerdictId": task.get("viewerVerdictId")})
        raise ResearchError("verdict_reconciliation_pending", "The previous POST has an unknown outcome. The task exposes only your verdict ID, so the existing API cannot confirm the exact body without exposing other verdicts; no duplicate POST was sent",
                            {"intent_id": uncertain["id"], "viewerVerdictId": task.get("viewerVerdictId")})
    if not any(r["binding"] == binding for r in prior):
        current_id = task.get("viewerVerdictId")
        confirmed = [(r["created_revision"], records["verdict_receipt"][r["id"]]["response"].get("id"))
                     for r in prior if r["status"] == "complete" and r["id"] in records.get("verdict_receipt", {})]
        if confirmed and (current_id is None or current_id in {item[1] for item in confirmed}):
            current_id = max(confirmed, key=lambda item: item[0])[1]
            if not isinstance(current_id, str) or not current_id:
                raise ResearchError("verdict_reconciliation_pending", "The known prior success needs an exact own verdict ID before an explicit revision")
        if current_id != body.get("supersedesVerdictId"):
            raise ResearchError("verdict_revision_required", "A new verdict revision must explicitly name the latest known own verdict ID")
    return report, binding


def _send_verdict(store, task, body, client, *, expected_revision, request_id):
    report, binding = check_verdict_preconditions(store, task, body)
    intent = begin_intent(store, "verdict", binding, expected_revision=expected_revision, request_id=request_id)
    with _owner(store.root, "remote:" + intent["id"]):
        current = get_intent(store, intent["id"])
        if current["status"] == "complete":
            return store.snapshot()["records"]["verdict_receipt"][intent["id"]]
        validate_verdict(store, task, body)
        response = remote_step(store, intent["id"], "verdict", body,
            lambda: client("POST", "/api/v1/verifications/" + quote(task["verificationId"], safe="") + "/verdicts", body)[0])
        return finish_intent(store, intent["id"], "verdict_receipt", {"intent_id": intent["id"], "body": body,
            "assessment_digest": report["assessment"]["digest"], "task": task, "response": response})
```

The body of `check_verdict_preconditions` is the former first half of `_send_verdict`, moved verbatim; `send_verdict` is unchanged.

- [ ] **Step 4: Rewrite `_run_verify` in `bin/exactory`**

Change the imports:

```python
from research_harness.cli import add_identity, note_managed_record_skipped
from research_harness.errors import ResearchError
from research_harness.integration import command_identity, current_store, managed_store
from research_harness.submission import check_verdict_preconditions, continue_submission, send_verdict, submit_managed, validate_submission
from research_harness.verification import record_task
from research_harness.workspace import find_workspace, strict_json
```

Replace the tail of `_run_verify`, from the comment `# The command takes the verification id the page names` to the end of the function, with:

```python
    # The command takes the verification id the page names, or the paper's DOI or arXiv
    # id; a non-UUID identifier resolves through the task route, which reads all three.
    identifier = _resolve_paper_identifier(args.identifier)
    task, _ = _send_request("GET", "/api/v1/tasks/" + _quote_identifier_path(identifier))

    # The workspace records a receipt when it bound this exact verdict; otherwise
    # the verdict goes out directly, and the user reads why on stderr.
    store = managed_store()
    if store is not None:
        try:
            check_verdict_preconditions(store, task, body)
        except ResearchError as error:
            note_managed_record_skipped(error)
            store = None
    if store is not None:
        payload = send_verdict(store, task, body, _send_request, **command_identity(store, args))
    else:
        payload, _ = _send_request(
            "POST",
            "/api/v1/verifications/" + urllib.parse.quote(task["verificationId"], safe="") + "/verdicts",
            body,
        )
    _print_json(payload)
```

- [ ] **Step 5: Run the verify tests and the verification module**

```bash
python3 -m unittest tests.test_transport -k TestVerify -v
python3 -m unittest tests.test_research_verification -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Message file `commit-3.txt`:

```
feat: send a verdict from any directory

exactory verify records the managed receipt when the workspace bound
this exact verdict, and otherwise posts the verdict directly after one
stderr line naming why no receipt was written.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

```bash
git add bin/exactory research_harness/submission.py tests/test_transport.py
git commit -F <scratchpad>/commit-3.txt
```

---

### Task 4: `exactory submit` runs from anywhere and prints the citation report

**Files:**
- Modify: `bin/exactory:163-200` (`_run_submit`)
- Test: `tests/test_transport.py:275-330` (`TestSubmitCitationGate`)

**Interfaces:**
- Consumes: `managed_store`, `note_managed_record_skipped`, `report_citation_gate` (Task 2); `validate_submission`, `submit_managed` (existing).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

In `tests/test_transport.py`, rename `TestSubmitCitationGate` to `TestSubmitDecision` and replace its tests with:

```python
    def test_submit_in_a_legacy_workspace_prints_the_report_and_posts(self) -> None:
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertTrue(stderr_text.startswith("Citation report: "))
        self.assertIn("references.bib", stderr_text)
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])
        self.assertEqual(self.request_bodies, [{"doi": "10.5281/zenodo.1"}])

    def test_submit_from_a_workspace_subdirectory_still_prints_the_report(self) -> None:
        os.chdir(self.scratch_dir / "draft")
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertTrue(stderr_text.startswith("Citation report: "))
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])

    def test_submit_outside_a_workspace_prints_nothing_and_posts(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        os.chdir(outside.name)
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(stderr_text, "")
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])

    def test_submit_in_an_unready_study_notes_the_skip_and_posts(self) -> None:
        from integration_fixtures import prepare_research

        case = prepare_research(self.scratch_dir, candidate=True)
        _write_passing_citation_report(self.scratch_dir)
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertTrue(stderr_text.startswith("Managed record skipped (readiness_required): "))
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])
        from research_harness.storage import Store
        self.assertNotIn("submission_receipt", Store(self.scratch_dir).snapshot()["records"])

    def test_submit_in_a_ready_study_writes_the_receipt(self) -> None:
        from integration_fixtures import prepare_manuscript, prepare_research
        from research_harness.zenodo import deposit

        case = prepare_research(self.scratch_dir, candidate=True)
        _write_passing_citation_report(self.scratch_dir)
        (self.scratch_dir / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Authored finite-result fixture.\n%%EOF")
        (self.scratch_dir / "draft/abstract.txt").write_text("The exact finite bound is 9.")
        bundle = prepare_manuscript(case, stop=True)
        binding = {"bundle_digest": bundle["digest"], "prepared_revision": bundle["prepared_revision"],
            "base_url": "https://zenodo.org/api", "environment": "production", "new_version": False,
            "publish": True, "metadata": {"title": "Authored finite result", "description": "The finite bound is 9."},
            "uploads": [{"name": "paper.pdf", "artifact": bundle["files"]["pdf"]["artifact"]}], "prior": None}

        def publisher(method, url, **kwargs):
            if method == "POST" and url.endswith("/deposit/depositions"):
                return {"id": 1, "links": {"bucket": "https://zenodo.org/api/files/fixture"}}
            if url.endswith("/actions/publish"):
                return {"id": 1, "doi": "10.5281/zenodo.1", "conceptdoi": "10.5281/zenodo.0",
                        "links": {"record_html": "https://zenodo.org/records/1"}}
            return {}

        deposit(case.store, binding, publisher,
                expected_revision=case.store.revision, request_id="transport-publication")
        self.responses[("POST", "/api/v1/verifications")] = ({"verificationId": _VERIFICATION_ID}, 201)
        task_path = "/api/v1/tasks/" + _VERIFICATION_ID
        self.responses[("GET", task_path)] = ({"verificationId": _VERIFICATION_ID,
            "doi": "10.5281/zenodo.0", "source": "zenodo", "sourceId": "1", "sourceVersion": None,
            "url": "https://zenodo.org/records/1"}, 200)
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(stderr_text, "")
        self.assertEqual(self.requested_paths, ["/api/v1/verifications", task_path])
        self.assertEqual(self.request_bodies, [{"doi": "10.5281/zenodo.1"}, None])
        from research_harness.storage import Store
        receipts = Store(self.scratch_dir).snapshot()["records"]["submission_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(next(iter(receipts.values()))["matched"])
```

Also update the module docstring's "the citation gate" to "the citation report".

- [ ] **Step 2: Run to see them fail**

```bash
python3 -m unittest tests.test_transport -k TestSubmitDecision -v
```

Expected: the legacy, subdirectory, and unready-study tests FAIL (exit code 1 or `ResearchError`); the outside and ready-study tests PASS.

- [ ] **Step 3: Rewrite `_run_submit` in `bin/exactory`**

Add near the other module constants (after `_UUID_RE` is fine):

```python
_CHECK_COMMAND_PATH = Path(__file__).with_name("exactory-check")
```

and the import `from research_harness.citation_report import report_citation_gate`. Replace `_run_submit` with:

```python
def _run_submit(args: argparse.Namespace) -> None:
    if args.arxiv_id:
        body = {"arxivId": args.arxiv_id}
    elif args.doi:
        body = {"doi": args.doi}
    else:
        body = {"url": args.url}
    if args.challenge:
        if len(args.challenge) > _MAX_DECLARED_CHALLENGES:
            _exit_with_error(f"The command names {len(args.challenge)} challenges."
                             f" The limit is {_MAX_DECLARED_CHALLENGES}. Remove some.")
        body["grandChallengeIds"] = args.challenge

    # Inside a draft workspace the offline citation report is printed first; the
    # submit continues either way, because the user asked for it.
    workspace = find_workspace(required=False)
    if workspace is not None and (workspace / ".exactory" / "draft.json").is_file():
        report_citation_gate(workspace, _CHECK_COMMAND_PATH)

    # A study whose publication gate is ready records the submission receipt;
    # otherwise the request goes out directly, and the user reads why on stderr.
    store = managed_store()
    if store is not None:
        try:
            validate_submission(store, body)
        except ResearchError as error:
            note_managed_record_skipped(error)
            store = None
    if store is not None:
        payload = submit_managed(store, body, _send_request, **command_identity(store, args))
        status = 201
    else:
        payload, status = _send_request("POST", "/api/v1/verifications", body)
    if status == 200:
        print("The server returned the existing open request for this paper.",
              file=sys.stderr)
    _print_json(payload)
```

Remove the `import subprocess` from `bin/exactory` if nothing else uses it, and update the module docstring line 8-9 ("Inside a draft workspace, submit first runs the offline citation gate ... and stops when the gate fails.") to: "Inside a draft workspace, submit first prints the offline citation report (exactory-check gate) and continues."

- [ ] **Step 4: Run the transport module**

```bash
python3 -m unittest tests.test_transport -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Message file `commit-4.txt`:

```
feat: submit a paper from any directory

exactory submit records the managed receipt when the study's
publication gate is ready and otherwise posts the request directly.
The offline citation report is printed inside a draft workspace and
never stops the submit.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

```bash
git add bin/exactory tests/test_transport.py
git commit -F <scratchpad>/commit-4.txt
```

---

### Task 5: `exactory-draft deposit` runs on the user's instruction

**Files:**
- Modify: `research_harness/zenodo.py` (add `validate_deposit` before `deposit`)
- Modify: `bin/exactory-draft:28-40` (imports), `bin/exactory-draft:41-57` (constants), `bin/exactory-draft:250-302` (`_run_deposit`), plus the restored helpers
- Test: `tests/test_draft.py`

**Interfaces:**
- Consumes: `managed_store`, `note_managed_record_skipped`, `report_citation_gate` (Task 2).
- Produces: `zenodo.validate_deposit(store, pdf, abstract, sources) -> report`: `validate_upload` plus the round gate; raises `ResearchError` when the workspace cannot record this deposit.

- [ ] **Step 1: Write the failing tests**

In `tests/test_draft.py`:

1. Add two fixtures after `_DepositTestCase`:

```python
class _PlainWorkspaceDepositTestCase(unittest.TestCase):
    """A draft workspace as `exactory-draft init` leaves it: a store, no research."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.workspace_dir = Path(scratch.name)
        _run_draft_command(
            ["init", "--dir", str(self.workspace_dir),
             "--title", "Cohort Percentiles", "--category", "cs.MA"],
            None,
            self,
        )
        (self.workspace_dir / "draft" / "paper.pdf").write_bytes(b"%PDF-1.4 fake paper")
        (self.workspace_dir / "draft" / "abstract.txt").write_text(
            "We predict cohort percentiles & bound their error.\n"
        )
        _write_passing_citation_report(self.workspace_dir)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.workspace_dir)
        self.fake_api = _FakeZenodoApi()
        self.addCleanup(setattr, _draft, "_open_url", _draft._open_url)
        _draft._open_url = self.fake_api
        env_patcher = unittest.mock.patch.dict(
            os.environ,
            {"ZENODO_SANDBOX_TOKEN": "sandbox-token", "ZENODO_TOKEN": "production-token"},
            clear=True,
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _deposit(self, argv_tail: list[str], expected_exit_code: int | None = None) -> str:
        return _run_draft_command(
            ["deposit", "--abstract-file", "draft/abstract.txt", *argv_tail],
            expected_exit_code, self,
        )

    def read_deposit_state(self) -> dict:
        return json.loads((self.workspace_dir / ".exactory" / "deposit.json").read_text())

    def requested(self) -> list[tuple[str, str]]:
        return [(request.get_method(), request.full_url) for request in self.fake_api.requests]


class _LegacyWorkspaceDepositTestCase(_PlainWorkspaceDepositTestCase):
    """A draft workspace from before 0.38.0: draft.json and no store."""

    def setUp(self) -> None:
        super().setUp()
        # The store and its journal files, which Store() looks for together.
        for path in (self.workspace_dir / ".exactory").glob("research.sqlite3*"):
            path.unlink()
```

2. Add the test classes:

```python
class TestDirectDeposit(_PlainWorkspaceDepositTestCase):
    def test_an_unready_workspace_notes_the_skip_and_uploads(self) -> None:
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Managed record skipped (readiness_required): ", output)
        self.assertEqual(self.requested()[0], ("POST", "https://sandbox.zenodo.org/api/deposit/depositions"))
        self.assertIn(("PUT", "https://sandbox.zenodo.org/api/files/bucket-1/paper.pdf"), self.requested())
        self.assertIn(("PUT", "https://sandbox.zenodo.org/api/deposit/depositions/4242"), self.requested())
        self.assertIn(("PUT", "https://sandbox.zenodo.org/api/records/4242/draft"), self.requested())
        state = self.read_deposit_state()
        self.assertEqual(state["environment"], "sandbox")
        self.assertEqual(state["deposition_id"], 4242)
        self.assertIn("deposit/4242", state["draft_url"])
        self.assertNotIn("doi", state)

    def test_a_published_direct_deposit_writes_the_dois(self) -> None:
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        state = self.read_deposit_state()
        self.assertEqual(state["doi"], "10.5281/zenodo.4242")
        self.assertEqual(state["concept_doi"], "10.5281/zenodo.4241")
        self.assertIn("records/4242", state["record_url"])
        # The combined stream holds the skip note, the publish line, then the state JSON.
        self.assertIn("The record is published: ", output)
        self.assertIn('"doi": "10.5281/zenodo.4242"', output)

    def test_the_direct_metadata_carries_the_disclosure(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        metadata_request = next(request for request in self.fake_api.requests
                                if request.get_method() == "PUT"
                                and request.full_url.endswith("/deposit/depositions/4242"))
        metadata = json.loads(metadata_request.data.decode())["metadata"]
        self.assertEqual(metadata["title"], "Cohort Percentiles")
        self.assertEqual(metadata["creators"], [{"name": "Shiroshita, Ryosuke"}])
        self.assertIn(_DEPOSITED_THROUGH_EXACTORY_SENTENCE, metadata["description"])
        self.assertNotIn("keywords", metadata)

    def test_a_production_deposit_prints_a_failing_citation_report_and_continues(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        output = self._deposit(["--production", "--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Citation report: ", output)
        self.assertIn("exactory-check lookup", output)
        self.assertEqual(self.requested()[0], ("POST", "https://zenodo.org/api/deposit/depositions"))

    def test_a_production_publish_still_needs_confirmation(self) -> None:
        stderr_text = self._deposit(["--production", "--publish", "--creator", "Shiroshita, Ryosuke"],
                                    expected_exit_code=1)
        self.assertIn("--confirm-publish", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_new_version_reuses_the_stored_deposition(self) -> None:
        self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        self.fake_api.requests.clear()
        self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"])
        self.assertEqual(self.requested()[0],
                         ("POST", "https://sandbox.zenodo.org/api/deposit/depositions/4242/actions/newversion"))
        self.assertEqual(self.requested()[1], ("GET", "https://sandbox.zenodo.org/api/deposit/depositions/4343"))
        self.assertIn(("PUT", "https://sandbox.zenodo.org/api/files/bucket-2/paper.pdf"), self.requested())
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4343)

    def test_a_new_version_refuses_an_environment_mismatch(self) -> None:
        self._deposit(["--production", "--publish", "--confirm-publish", "--creator", "Shiroshita, Ryosuke"])
        self.fake_api.requests.clear()
        stderr_text = self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertIn("same environment", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_new_version_without_a_prior_record_is_an_error(self) -> None:
        stderr_text = self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertIn("Run a plain deposit first", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_blank_abstract_file_is_refused_before_any_request(self) -> None:
        (self.workspace_dir / "draft" / "abstract.txt").write_text(" \n\n")
        stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
        self.assertIn("abstract", stderr_text)
        self.assertEqual(self.fake_api.requests, [])


class TestLegacyWorkspaceDeposit(_LegacyWorkspaceDepositTestCase):
    def test_a_legacy_workspace_uploads_without_a_note(self) -> None:
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertNotIn("Managed record skipped", output)
        self.assertEqual(self.requested()[0], ("POST", "https://sandbox.zenodo.org/api/deposit/depositions"))
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4242)
```

3. Change these existing tests in the ready-study fixture (`_DepositTestCase` subclasses):

- `test_a_pdf_from_outside_the_draft_tree_is_refused_before_deposit` becomes:

```python
    def test_a_pdf_from_outside_the_draft_tree_moves_the_deposit_to_the_direct_path(self) -> None:
        _write_authorship_record(self.workspace_dir, _AGENT_WROTE_THE_PAPER_RECORD_TEXT)
        outside = self._write_pdf_outside_the_workspace()
        self.assertFalse(_draft._has_exactory_authorship_evidence(outside))
        output = self._deposit(["--creator", "Shiroshita, Ryosuke", "--pdf", str(outside)])
        self.assertIn("Managed record skipped (publication_artifact_mismatch): ", output)
        self.assertEqual(self.fake_api.requests[0].get_method(), "POST")
        self.assertNotIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])
```

- `test_a_pdf_symlinked_out_of_the_draft_tree_is_refused_before_deposit` becomes:

```python
    def test_a_pdf_symlinked_out_of_the_draft_tree_moves_the_deposit_to_the_direct_path(self) -> None:
        _write_authorship_record(self.workspace_dir, _AGENT_WROTE_THE_PAPER_RECORD_TEXT)
        paper_path = self.workspace_dir / "draft" / "paper.pdf"
        paper_path.unlink()
        paper_path.symlink_to(self._write_pdf_outside_the_workspace())
        self.assertFalse(_draft._has_exactory_authorship_evidence(paper_path))
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Managed record skipped (", output)
        self.assertEqual(self.fake_api.requests[0].get_method(), "POST")
        self.assertNotIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])
```

Add `from research_harness.storage import Store` to the module imports of `tests/test_draft.py`.

- `test_a_blank_abstract_file_is_refused_before_any_request` and `test_a_missing_abstract_file_is_refused_before_any_request` in `TestDeposit`: change `expected_exit_code=1` and the positional `1` to `2` (the abstract reader exits 2 before any store check).
- `TestProductionDepositGate` becomes `TestProductionDepositCitationReport`:

```python
class TestProductionDepositCitationReport(_DepositTestCase):
    def test_a_failing_report_is_printed_and_the_managed_deposit_continues(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        output = self._deposit(["--production", "--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Citation report: ", output)
        self.assertIn("exactory-check lookup", output)
        self.assertTrue(self.fake_api.requests[0].full_url.startswith("https://zenodo.org/api/"))
        self.assertIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])

    def test_a_changed_bibliography_moves_the_deposit_to_the_direct_path(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        (self.workspace_dir / "draft" / "references.bib").unlink()
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Managed record skipped (", output)
        self.assertEqual(self.fake_api.requests[0].get_method(), "POST")
        self.assertNotIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])
```

- `TestDepositPreconditions.test_deposit_outside_a_workspace_points_at_init`: change `expected_exit_code=1` to `expected_exit_code=2`.
- Add to `TestDeposit`, after `test_deposit_stays_a_draft_by_default_and_prints_the_deposition_url`:

```python
    def test_a_ready_study_writes_the_publication_receipt(self) -> None:
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        self.assertNotIn("Managed record skipped", output)
        receipts = Store(self.workspace_dir).snapshot()["records"]["publication_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(next(iter(receipts.values()))["doi"], "10.5281/zenodo.4242")
```

- [ ] **Step 2: Run to see them fail**

```bash
python3 -m unittest tests.test_draft -k TestDirectDeposit -k TestLegacyWorkspaceDeposit -k TestProductionDepositCitationReport -v
```

Expected: every `TestDirectDeposit` and `TestLegacyWorkspaceDeposit` test FAILS with `readiness_required` or `migration_required` in the output; `test_a_changed_bibliography_moves_the_deposit_to_the_direct_path` FAILS.

- [ ] **Step 3: Add `validate_deposit` to `research_harness/zenodo.py`**

Before `def deposit(`:

```python
def validate_deposit(store, pdf, abstract, sources):
    """Every check the managed deposit runs before its first remote write.

    Raises ResearchError when the workspace cannot record this deposit; the
    CLI then deposits directly."""
    report = validate_upload(store, pdf, abstract, sources)
    records = store.snapshot()["records"]
    require_ready(round_state(records, Evaluation(records, ArtifactStore(store.root))),
                  "depositing the final development round")
    return report
```

Add `from .publication import publication_state, validate_upload` to the imports (replacing the existing `publication_state` import line).

- [ ] **Step 4: Rewrite the deposit command in `bin/exactory-draft`**

Change the imports to:

```python
from research_harness.errors import ResearchError
from research_harness.integration import command_identity, current_store, initialization_payload, initialize_workspace, managed_store
from research_harness.storage import Store
from research_harness.workspace import find_workspace
from research_harness.citation_report import report_citation_gate
from research_harness.cli import add_identity, note_managed_record_skipped
from research_harness.zenodo import deposit as deposit_bundle, continue_deposit, validate_deposit
```

(`gate_report`, `require_ready`, `validate_upload`, and `ArtifactStore` are no longer used here; drop them. `current_store` and `find_workspace` stay for `_run_reconcile`; `Store` stays for `_run_init`.)

Add the constants after `_SOURCES_UPLOAD_STEM`:

```python
_CHECK_COMMAND_PATH = Path(__file__).with_name("exactory-check")
_RDM_JSON_MEDIA_TYPE = "application/vnd.inveniordm.v1+json"
```

Replace `_run_deposit` with these five functions:

```python
def _run_deposit(args: argparse.Namespace) -> None:
    state = _load_draft_state()
    pdf_path = Path(args.pdf) if args.pdf else _find_newest_pdf()
    if not pdf_path.exists():
        _exit_with_error(f"The file {pdf_path} does not exist.", 2)
    sources_path = Path(args.sources) if args.sources else None
    if sources_path is not None and not sources_path.exists():
        _exit_with_error(f"The file {sources_path} does not exist.", 2)
    abstract_path = Path(args.abstract_file)
    abstract_text = _read_abstract_text(abstract_path)
    has_exactory_authorship_evidence = _has_exactory_authorship_evidence(pdf_path)
    token = _read_zenodo_token(args.production)
    if args.publish and args.production and not args.confirm_publish:
        _exit_with_error("A production publish is permanent. Get the user's approval"
                         " first. Then run the command again with --confirm-publish.")
    if args.production:
        # The offline citation report is printed before the first remote write;
        # the deposit continues either way, because the user asked for it.
        report_citation_gate(Path.cwd(), _CHECK_COMMAND_PATH)

    base_url = _PRODUCTION_API_URL if args.production else _SANDBOX_API_URL
    environment = "production" if args.production else "sandbox"
    metadata = _build_deposit_metadata(state["title"], args.creator, abstract_text,
                                       has_exactory_authorship_evidence)

    # A study whose publication and round gates are ready records the receipt;
    # otherwise the record is created directly, and the user reads why on stderr.
    store = managed_store()
    report = None
    if store is not None:
        try:
            report = validate_deposit(store, pdf_path, abstract_path, sources_path)
        except ResearchError as error:
            note_managed_record_skipped(error)
            store = None
    if store is not None:
        _deposit_managed(store, report, args, base_url, environment, metadata, sources_path, token)
    else:
        _deposit_directly(args, base_url, environment, metadata, pdf_path, sources_path, token)


def _deposit_managed(store, report: dict, args: argparse.Namespace, base_url: str, environment: str,
                     metadata: dict, sources_path: Path | None, token: str) -> None:
    bundle = report["bundle"]
    uploads = [{"name": _PAPER_UPLOAD_NAME, "artifact": bundle["files"]["pdf"]["artifact"]}]
    if sources_path is not None:
        uploads.append({"name": _name_sources_upload(sources_path), "artifact": bundle["files"]["sources"]["artifact"]})
    binding = {"bundle_digest": bundle["digest"], "prepared_revision": bundle["prepared_revision"],
               "base_url": base_url, "environment": environment, "new_version": args.new_version,
               "publish": args.publish, "metadata": metadata, "uploads": uploads,
               "prior": store.snapshot()["records"].get("workspace", {}).get("deposit") if args.new_version else None}
    identity = command_identity(store, args)
    # CAS validates the snapshot that supplied the exact upload bytes.
    if args.expected_revision is None:
        identity["expected_revision"] = report["revision"]
    receipt = deposit_bundle(store, binding, lambda method, url, **kwargs: _call_zenodo(method, url, token, **kwargs), **identity)
    print(json.dumps(receipt, indent=2))


def _deposit_directly(args: argparse.Namespace, base_url: str, environment: str, metadata: dict,
                      pdf_path: Path, sources_path: Path | None, token: str) -> None:
    if args.new_version:
        deposition = _open_new_version_draft(base_url, environment, token)
    else:
        deposition = _call_zenodo("POST", f"{base_url}/deposit/depositions", token, json_body={})
    bucket_url = deposition["links"]["bucket"]
    _call_zenodo("PUT", f"{bucket_url}/{_PAPER_UPLOAD_NAME}", token, file_bytes=pdf_path.read_bytes())
    if sources_path is not None:
        _call_zenodo("PUT", f"{bucket_url}/{_name_sources_upload(sources_path)}", token,
                     file_bytes=sources_path.read_bytes())
    _call_zenodo("PUT", f"{base_url}/deposit/depositions/{deposition['id']}", token,
                 json_body={"metadata": metadata})
    _mark_paper_as_preview(base_url, deposition["id"], token)
    published = None
    if args.publish:
        # Per https://developers.zenodo.org, doi is present only on published
        # depositions; conceptdoi names the paper across all its versions.
        published = _call_zenodo("POST", f"{base_url}/deposit/depositions/{deposition['id']}/actions/publish", token)
    state = _save_deposit_state(environment, deposition, published)
    if published is not None:
        print(f"The record is published: {state['record_url']}")
    else:
        print(f"The draft deposit is created. Review it at: {state['draft_url']}")
    print(json.dumps(state, indent=2))


def _mark_paper_as_preview(base_url: str, record_id: int, token: str) -> None:
    """Make the paper the record's previewed file. The RDM draft document is
    the working surface for this: the file-sort endpoint that
    https://developers.zenodo.org still documents answers HTTP 405 on
    zenodo.org (verified 2026-08-30). The PUT replaces the whole draft
    document, so the fetched document goes back with only default_preview
    added."""
    draft_url = f"{base_url}/records/{record_id}/draft"
    document = _call_zenodo("GET", draft_url, token, accept=_RDM_JSON_MEDIA_TYPE)
    document.setdefault("files", {})["default_preview"] = _PAPER_UPLOAD_NAME
    _call_zenodo("PUT", draft_url, token, json_body=document)


def _open_new_version_draft(base_url: str, environment: str, token: str) -> dict:
    try:
        prior = json.loads(_DEPOSIT_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _exit_with_error(f"{_DEPOSIT_STATE_PATH} does not exist here."
                         " Run a plain deposit first; --new-version revises it.")
    except json.JSONDecodeError as error:
        _exit_with_error(f"{_DEPOSIT_STATE_PATH} is not valid JSON. Cause: {error}")
    if prior.get("environment") != environment:
        _exit_with_error(
            f"The stored deposit is on {prior.get('environment')}, not"
            f" {environment}. Run --new-version against the same environment.")
    created = _call_zenodo(
        "POST",
        f"{base_url}/deposit/depositions/{prior['deposition_id']}/actions/newversion",
        token,
    )
    draft_url = created.get("links", {}).get("latest_draft", "")
    if draft_url == "":
        _exit_with_error("Zenodo returned no new-version draft link.")
    return _call_zenodo("GET", draft_url, token)


def _save_deposit_state(environment: str, deposition: dict, published: dict | None) -> dict:
    """Write .exactory/deposit.json with the fields the managed projection writes, and return them."""
    state = {
        "environment": environment,
        "deposition_id": deposition["id"],
        "draft_url": deposition.get("links", {}).get("html", ""),
    }
    if published is not None:
        state["doi"] = published.get("doi", "")
        state["concept_doi"] = published.get("conceptdoi", "")
        state["record_url"] = published.get("links", {}).get("record_html", "")
    _DEPOSIT_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state
```

Update the module docstring (lines 2-15): replace "A production deposit first runs the offline citation gate (exactory-check gate)." with "A production deposit first prints the offline citation report (exactory-check gate) and continues." and add one sentence: "A workspace whose publication and round gates are ready records the deposit as a managed receipt; any other workspace deposits directly and prints one Managed record skipped line."

Check that `_run_reconcile` still imports what it needs (`current_store` from `research_harness.integration`); keep that import if so.

- [ ] **Step 5: Run the draft module and the publication and round modules**

```bash
python3 -m unittest tests.test_draft -v
python3 -m unittest tests.test_research_publication tests.test_research_round_integrity -v
```

Expected: PASS. The round-integrity module still passes because `validate_deposit` runs the same round check the managed path runs.

- [ ] **Step 6: Commit**

Message file `commit-5.txt`:

```
feat: deposit on the user's instruction

exactory-draft deposit records the managed receipt when the study's
publication and round gates are ready, and otherwise creates the Zenodo
record directly and writes .exactory/deposit.json. The production
citation report is printed and never stops the deposit.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

```bash
git add bin/exactory-draft research_harness/zenodo.py tests/test_draft.py
git commit -F <scratchpad>/commit-5.txt
```

---

### Task 6: Rewrite the three skills

**Files:**
- Modify: `skills/submit/SKILL.md`, `skills/verify/SKILL.md`, `skills/deposit/SKILL.md`
- Check: `python3 codex/generate.py --check` (the descriptions do not change, so `codex/skills/*` stays current)

**Interfaces:** none. Each skill states the section-1 rule of the spec in one paragraph.

The 0.36.0 text is the base. Restore it, then apply the edits listed for each file. Every edit is an exact old paragraph and its replacement.

- [ ] **Step 1: Restore the 0.36.0 text of the three skills**

```bash
git show exactory--v0.36.0:skills/submit/SKILL.md > skills/submit/SKILL.md
git show exactory--v0.36.0:skills/verify/SKILL.md > skills/verify/SKILL.md
git show exactory--v0.36.0:skills/deposit/SKILL.md > skills/deposit/SKILL.md
```

- [ ] **Step 2: Edit `skills/submit/SKILL.md`**

After the paragraph that starts "The `exactory` command is on PATH", insert:

```markdown
The command runs from any directory, and the user's request is its authorization.
Inside a study whose publication gate is ready, the CLI also records the submission
receipt the study needs to close its `submit` stage. In any other workspace it prints
one line, `Managed record skipped (<code>): <message>`, and sends the request anyway.
Inside a draft workspace it first prints the offline citation report; a failing
report is information for the user, and the submit continues.
```

Replace the second bullet of step 1 ("The user named none and you are in a study workspace: ...") with:

```markdown
   - The user named none and you are in a study workspace: the paper is the one this
     study deposited. Read `.exactory/deposit.json` and take `doi`, the record's DOI.
     That file also carries `environment`. A `sandbox` record is a test deposit the
     server cannot fetch; when the record is a sandbox one, tell the user and deposit
     to production first.
```

- [ ] **Step 3: Edit `skills/verify/SKILL.md`**

1. After the paragraph that ends "Do not ask the user to paste the key into the chat.", insert:

```markdown
The command runs from any directory, and the user's request is its authorization.
When you work inside a verification workspace that bound this exact verdict with
`exactory-research bind-verdict`, the CLI also records the verdict receipt there. In
any other directory it sends the verdict directly; inside a workspace that did not
bind it, it first prints one line, `Managed record skipped (<code>): <message>`.
Neither case stops the send.
```

2. In the task-field list of step 1, replace the line starting "Each task carries `verificationId`, `source`, `sourceId`, `url`," with:

```markdown
Each task carries `verificationId`, `source`, `sourceId`, `sourceVersion`, `url`, `title`, `authors`,
```

3. Replace the paragraph starting "`requestedByViewer` is true when this account submitted the paper." with:

```markdown
`requestedByViewer` means this account opened the verification request. It does not
establish paper authorship: request creation is no evidence about soundness or the
author's identity. Work the task as any other; the page marks the verdict as the
submitter's.
```

4. In step 3, replace the closing paragraph "When the checks contradict each other, say so and file on the balance. An honest split is information; a verdict withheld is not." with:

```markdown
When the checks contradict each other, say so and file on the balance. An honest split is
information; a verdict withheld is not. A source you could not open is not evidence of
unsoundness: name what you could not read, and judge on what you did read.
```

5. In the "Rules the server holds you to" list, replace the sentence "A verdict without it does not send: the CLI refuses the file before any network call, and a session gate refuses the command." with "A verdict without it does not send: the CLI refuses the file before any network call." (When the 0.36.0 text already reads that way, there is nothing to change.)

- [ ] **Step 4: Edit `skills/deposit/SKILL.md`**

1. Replace the first bullet of "Before anything else" ("Production publishing is permanent, and invoking this stage is the authorization to run it: proceed through sandbox and production without stopping. Park before production (...) only when the user named that stop (...).") with:

```markdown
- The user's instruction to deposit is the authorization, and the deposit runs at
  the time the user names. Production publishing is permanent; the
  `--confirm-publish` flag records that the user asked for it. Park before
  production (`exactory-lab state set --waiting production-deposit`) only when
  the user named that stop ("prepare the deposit but let me publish it").
- The command runs in any draft workspace. In a study whose publication and
  round gates are ready, the CLI records the publication receipt the study needs
  to close its `deposit` stage. In any other workspace it prints one line,
  `Managed record skipped (<code>): <message>`, and creates the record anyway.
```

2. Replace step 1 ("**Confirm the citations are clean.** ...") with:

```markdown
1. **Read the citation report.** Run `exactory-check lookup` and read the report.
   Fix any blocking finding at the reference, never in the report. A production
   deposit prints the same report again on stderr as `Citation report:` and
   continues. When the user has asked for the deposit now, deposit now and
   report the blocking findings beside the DOI.
```

3. Replace step 3 ("**Deposit to the sandbox first.**" with its command and paragraph) and step 4 ("**Deposit and publish to production.**") with:

```markdown
3. **Deposit and publish to production.**
   ```
   exactory-draft deposit --production --publish --confirm-publish --creator "<Family, Given>" --abstract-file draft/abstract.txt
   ```
   Repeat `--creator` for more authors. The record's description opens with the
   abstract and closes with a disclosure naming the human as the responsible
   author. There are two disclosures, and the command picks between them: it
   names exactory.ai as the paper's writer when the PDF comes from this
   workspace's `draft/` tree and `.exactory/authorship.json` reads
   `written_by_exactory` true, which the plugin writes when an agent writes a
   LaTeX source under `draft/`, and otherwise it states only that the paper was
   prepared with AI assistance and deposited through exactory.ai. The PDF is
   uploaded as `paper.pdf` and listed first; the sources archive follows it.
   State the command in the report beside its result: the record DOI and the
   concept DOI. The concept DOI names the paper across all its versions;
   `/exactory:submit` reads the record DOI from `.exactory/deposit.json`. Read
   the disclosure back to the user from the record, never from memory. When the
   user named a stop before production, park instead and hand them the exact
   command.
4. **A test record, when the user asks for one.** The same command without
   `--production` (and without `--confirm-publish`) creates the record on the
   Zenodo sandbox with `ZENODO_SANDBOX_TOKEN`. A sandbox record is a rehearsal:
   the server cannot fetch it, so it is never the record to submit.
```

4. In "What not to do", replace the first bullet ("Do not stop before production unless the user named that stop; and when they did, do not publish until they release it.") with:

```markdown
- Do not stop before production unless the user named that stop; and when they
  did, do not publish until they release it.
- Do not make a sandbox deposit a step of the procedure; it is a test the user
  asks for.
```

- [ ] **Step 5: Read the three files once in full**

Read each rewritten file top to bottom and confirm: no sentence names `status --summary`, `next --summary`, `gate`, `bind-verdict`, `--expected-revision`, `--request-id`, `reconcile`, or the research constitution; the four fenced commands still parse; the Codex check passes.

```bash
grep -n "status --summary\|next --summary\|gate \|bind-verdict\|expected-revision\|request-id\|reconcile\|constitution" skills/submit/SKILL.md skills/verify/SKILL.md skills/deposit/SKILL.md
python3 codex/generate.py --check
```

Expected: the grep prints one line, the verify skill's sentence that names `exactory-research bind-verdict` as the optional managed binding, and nothing else; the check passes.

- [ ] **Step 6: Commit**

Message file `commit-6.txt`:

```
docs(skills): submit, verify, and deposit run on the user's instruction

The three skills return to their direct procedures. Each states that the
CLI records a study receipt when the study is ready and runs the command
either way. The deposit skill makes the sandbox a test the user asks for
and goes straight to production.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

```bash
git add skills/submit/SKILL.md skills/verify/SKILL.md skills/deposit/SKILL.md
git commit -F <scratchpad>/commit-6.txt
```

---

### Task 7: Documentation, release note, testing record, version

**Files:**
- Modify: `README.md:38-40`, `README.md:322-346`
- Modify: `docs/research-workflow.md:321-325`, `docs/research-workflow.md:402-414`
- Modify: `docs/research-cli.md:317-331`
- Modify: `codex/README.md:42-43`
- Create: `docs/releases/0.40.0.md`, `docs/testing/user-authorized-publication.md`
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`
- Test: `tests/test_manifest.py`

- [ ] **Step 1: README**

Replace the first two sentences of the Install paragraph ("Version 0.39.2 enforces the final round decision at deposit, separates internal claim history from blind review, and measures the three paired reviewers.") with:

```markdown
Version 0.40.0 runs `exactory submit`, `exactory verify`, and `exactory-draft
deposit` on the user's instruction from any directory: a ready study records
its receipt, any other workspace gets one stderr line and the request goes out.
Version 0.39.2 enforces the final round decision at deposit, separates internal
claim history from blind review, and measures the three paired reviewers.
```

Replace the section "## Citation gate and hooks" through its two bullets ("- **Advisory.** ..." and "- **Blocking.** ...") with:

```markdown
## Citation report and hooks

`exactory submit` inside a draft workspace and `exactory-draft deposit
--production` run the offline citation check (`exactory-check gate`) before
their first network call. The check passes when the report matches the current
references file, shows zero blocking findings, and verified at least one entry.
A failing check is printed on stderr as `Citation report:` with the exact
command to run next, and the command continues: the user asked for the submit
or the deposit, and the report informs that decision.

One hook watches the references file in a draft workspace. Outside a workspace
it does nothing.

- **Advisory.** After each edit of a `.bib` file, the plugin validates the
  file offline. It reports duplicate keys and entries that have no DOI and
  no arXiv id.
```

- [ ] **Step 2: `docs/research-workflow.md`**

Replace the paragraph starting "Publication uses the current exact bundle, two applicable accepting reviews," with:

```markdown
Publication uses the current exact bundle, two applicable accepting reviews, and
the existing citation/remote receipt checks. Changes invalidate affected current
decisions and require reassessment. `exactory-draft deposit` and `exactory submit`
run on the user's instruction: when these gates pass they record the study's
publication and submission receipts, and otherwise they print one
`Managed record skipped` line and create the record or request directly.
Reconcile an unknown remote intent before any new managed write.
```

Replace the sentence pair "Use the [verification skill](../skills/verify/SKILL.md) and CLI's task/bind-verdict flow for the independent exact-target assessment and original outgoing verdict." with:

```markdown
`exactory verify` sends a verdict from any directory; a verification workspace that
bound the verdict with `task --bind` and `bind-verdict` also records its receipt.
The [verification skill](../skills/verify/SKILL.md) states the direct procedure.
```

- [ ] **Step 3: `docs/research-cli.md`**

Replace the paragraph starting "Existing `exactory-draft deposit` options remain available," with:

```markdown
Existing `exactory-draft deposit` options remain available, including sandbox, production, source archive, publication confirmation, and new version. When the workspace's publication gate is ready, the round gate is ready, and the upload matches the exact reviewed bundle, the command saves a remote intent before external writes and uploads those validated bytes; `exactory-draft reconcile INTENT_ID` reconciles an unknown outcome through remote reads before continuing. Otherwise the command prints one `Managed record skipped (<code>): <message>` line and creates the record directly from the given files, writing `.exactory/deposit.json`. Production prints the offline citation report first and continues. Local and remote storage are not an atomic transaction.
```

Replace the paragraph starting "Managed author `exactory submit DOI` binds the current published production record." with:

```markdown
`exactory submit` records a submission receipt when the current published production record matches the reviewed bundle; the receipt keeps Zenodo's concrete record DOI distinct from the concept DOI the server may return, and `exactory reconcile INTENT_ID` reads a later task without repeating the original POST. In any other directory or workspace the command prints one `Managed record skipped` line and posts the request directly.
```

Replace "For verification, acquire exact metadata and original body, initialize the `verification` profile, set matching roots/target, complete its required literature and standards, then run:" with:

```markdown
`exactory verify IDENTIFIER --file verdict.json` sends the verdict from any directory. A verification workspace that acquired the exact body, prepared the `verification` profile, and bound the task and the verdict also records the verdict receipt:
```

- [ ] **Step 4: `codex/README.md`**

Replace "deposit needs an approved `stop` (`gate round`) and the publication gate." with "the study's `deposit` stage closes on the receipt that `exactory-draft deposit` records when `gate round` and the publication gate pass."

- [ ] **Step 5: Release note `docs/releases/0.40.0.md`**

```markdown
# Exactory 0.40.0

This release runs the three commands that reach a public record on the user's
instruction. `exactory submit`, `exactory verify`, and `exactory-draft deposit`
run from any directory. A study that is ready records its receipt. Any other
workspace gets one stderr line, and the request goes out.

## One rule for three commands

Each command decides before its first network write. It opens the store of the
surrounding workspace when there is one. It runs every check the managed path
runs before its remote write. When all of them pass, it takes the managed path
and records the receipt the study needs. When one of them fails, it prints
`Managed record skipped (<code>): <message>` and sends the request directly.

A directory with no workspace, or a draft workspace initialized before 0.38.0,
takes the direct path with no message.

## What each command does

`exactory verify` posts the verdict file to the verification. A verification
workspace that bound this exact verdict with `bind-verdict` also records the
verdict receipt. The verdict still requires its cohort prediction.

`exactory submit` posts the request. A study whose publication gate is ready
and whose production record matches the submitted DOI also records the
submission receipt. Inside a draft workspace the offline citation report is
printed first.

`exactory-draft deposit` creates the Zenodo record from the given PDF and
abstract, marks the PDF as the preview, and writes `.exactory/deposit.json`.
A study whose publication and round gates are ready records the publication
receipt through the managed path with its intent reconciliation. A production
deposit prints the offline citation report first and continues. A production
publish still requires `--confirm-publish`.

## Hooks

The `enforce_citation_check.py` and `enforce_prediction.py` hooks are removed.
The CLI holds the prediction rule; the citation check is a printed report.
The advisory `.bib` hook and every study, autopilot, and math hook are
unchanged.

## Skills

The submit, verify, and deposit skills return to their direct procedures. The
deposit skill goes straight to production; a sandbox deposit is a test the
user asks for. Each skill states that the CLI records a study receipt when the
study is ready and runs the command either way.

## Upgrade and resume

The constitution remains Version 3 and the store schema remains version 1. An
upgrade from 0.39.2 requires no store migration.

A study in its `deposit` or `submit` stage behaves as before when its gates
pass. A study whose gates do not pass can now deposit and submit on the user's
instruction; its stage stays open until a managed receipt exists.

The [testing record](../testing/user-authorized-publication.md) contains the
regression cases and the validation results.
```

- [ ] **Step 6: Testing record `docs/testing/user-authorized-publication.md`**

Write the file after Task 8 has run the suite, with the measured numbers. Its shape:

```markdown
# User-authorized publication validation

Baseline: `fc081fd`, the head of PR 19 and release 0.39.2.
Release under validation: 0.40.0.

## Reproductions

- On the baseline, `exactory verify` from a directory with no workspace exits
  with `migration_required`; `exactory-draft deposit` in a workspace without
  reviews exits with `readiness_required`; `exactory submit` in a draft
  workspace without a citation report exits 1. The three regression tests that
  state the new behavior fail against the baseline and pass after Tasks 3-5.
- <the failing-then-passing counts and times from the task runs>

Zenodo and the exactory API are in-memory fakes at the transport boundary. No
test publishes a real paper.

## Coverage

`tests/test_user_authorized_publication.py` covers the shared decision helpers.
`tests/test_transport.py` covers submit and verify on both paths.
`tests/test_draft.py` covers deposit on the managed, plain, and legacy
workspaces, the citation report, and `--new-version` on the direct path.

## Final validation

On Python 3.9.6, `python3 -m unittest discover -s tests -v` passed <N> tests
in <seconds>. All Python sources compile. Codex generation verification, JSON
validation, and `git diff --check` pass.
```

- [ ] **Step 7: Version**

Set `"version": "0.40.0"` in `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`.

```bash
python3 -m unittest tests.test_manifest -v
python3 codex/generate.py --check
```

Expected: PASS.

- [ ] **Step 8: Commit**

Message file `commit-7.txt`:

```
docs: release 0.40.0, user-authorized publication

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

```bash
git add README.md docs/research-workflow.md docs/research-cli.md codex/README.md docs/releases/0.40.0.md docs/testing/user-authorized-publication.md .claude-plugin/plugin.json .codex-plugin/plugin.json
git commit -F <scratchpad>/commit-7.txt
```

---

### Task 8: Full validation and pull request

**Files:** none new; `docs/testing/user-authorized-publication.md` receives the measured numbers.

- [ ] **Step 1: Run the CI steps locally**

```bash
python3 -m compileall -q bin hooks research_harness codex tests
python3 codex/generate.py --check
python3 -m unittest discover -s tests -v 2>&1 | tail -5
for f in $(git ls-files '*.json'); do python3 -m json.tool "$f" > /dev/null || echo "invalid: $f"; done
git diff --check
```

Expected: every command exits 0; the suite reports `OK`. Record the test count and the wall time in `docs/testing/user-authorized-publication.md` (Task 7 Step 6) and amend the docs commit or add a commit `docs: record the 0.40.0 full-suite result`.

- [ ] **Step 2: Two-reviewer pass**

Run `superpowers:requesting-code-review` on the branch diff against `main` with two independent reviewers: one for correctness (the decision point runs before every remote write; the direct path writes the same `deposit.json` fields; the managed paths are byte-for-byte the 0.39.2 behavior), one for conformance to the spec and to `CLAUDE.md` (no defensive text in the skills; English only; simple-english for README and release note). Fix every finding with a regression test.

- [ ] **Step 3: Ask the user before pushing**

Report: the branch, the commit list, the suite result, and the review findings. Push and open the pull request only after the user says so:

```bash
git push -u origin feat/user-authorized-publication
gh pr create --base main --title "feat: user-authorized publication (0.40.0)" --body-file <scratchpad>/pr-body.md
```

The body file holds the release note's first two sections and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 4: After the six CI jobs pass**

Merge with `gh pr merge --admin --merge`, fast-forward `local-dev` and `dev` to the same commit, create the annotated tag `exactory--v0.40.0` ("Exactory 0.40.0"), and create the GitHub release from `docs/releases/0.40.0.md` with its links made absolute. Each of these steps happens only on the user's instruction.
