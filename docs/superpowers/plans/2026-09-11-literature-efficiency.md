# Literature Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task inline (the user asked for quota-conscious execution; delegate only reviews). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 0.38.0 of the exactory-client plugin: one evaluation per invocation, bounded reports, reference-based reading evidence, typed batches, dependency-specific freshness, neutral review packets, resource accounts, and an opt-in screened preparation policy, for Claude Code and Codex alike.

**Architecture:** Every gate keeps its `(records, artifacts, ...)` signature; the `artifacts` argument becomes an `Evaluation` that verifies bytes once and memoizes derived reports for one snapshot. New locators, batches, dispositions, screenings, budgets, and packets are additive record kinds and operations on the unchanged schema-1 store. Skills and docs are shared by both hosts; Codex entrypoints are regenerated.

**Tech Stack:** Python 3.9+ standard library, `unittest`, SQLite, optional `pdftotext`.

**Spec:** `docs/superpowers/specs/2026-09-11-literature-efficiency-design.md`

## Global Constraints

- Python 3.9+ standard library only; `pdftotext` optional.
- `schema_version` stays 1; events and receipts stay append-only; no history rewrite or compaction.
- Read commands write nothing (no event, no projection repair, no recovery).
- Full history validation runs at least once per process before evidence is used and inside every write transaction.
- Legacy text locators, readings, searches without dispositions, and configurations without a policy stay readable with the credit stated in the spec.
- No cache outlives one process; reuse is scoped to one snapshot in one invocation.
- Compact output, budgets, screenings, and runtime provenance never grant readiness.
- Exhaustion and saturation produce checkpoints, never readiness.
- Three fresh blind manuscript reviews per measurement; two accepting reviews before publication; five to ten external innovation families plus one within-field case.
- All artifacts English; research runs under `/Users/ryshiro/exactory/exactory-research`, uncommitted.
- Both manifests carry 0.38.0; `python3 codex/generate.py --check` passes; every existing test stays enabled.
- Run tests from the plugin root: `python3 -m unittest discover -s tests -p <file> -v`; the full suite is `python3 -m unittest discover -s tests`.
- Commit after each task on branch `feat/literature-efficiency`, message via `-F` file, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## Release A

### Task 1: Runtime provenance and versioned User-Agent (P0)

**Files:**
- Create: `research_harness/provenance.py`, `tests/test_research_provenance.py`
- Modify: `research_harness/operations.py:81-87` (receipt record), `research_harness/acquisition.py:99-103` (admission record), `research_harness/cli.py:116-134`, `research_harness/http.py:242`, `bin/exactory-research`

**Interfaces:**
- Produces: `provenance.runtime_provenance(root=None) -> dict` with keys `plugin_version, source_commit, dirty, executable, package_digest, constitution, schema_version`; `provenance.plugin_version(root=None) -> str`.
- `literature_operation/<request_id>` gains `runtime`; `acquisition_operation/<request_id>` gains `runtime`; `status`/`next` gain `runtime`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_research_provenance.py
import json, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]


class ProvenanceTests(unittest.TestCase):
    def test_runtime_provenance_names_the_build(self):
        from research_harness.provenance import runtime_provenance
        value = runtime_provenance()
        self.assertEqual(set(value), {"plugin_version", "source_commit", "dirty", "executable", "package_digest", "constitution", "schema_version"})
        self.assertEqual(value["plugin_version"], json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())["version"])
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(len(value["package_digest"]), 64)
        self.assertEqual(value["constitution"]["version"], "2")
        self.assertEqual(value, runtime_provenance())

    def test_git_fields_are_null_outside_a_checkout(self):
        from research_harness.provenance import runtime_provenance
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            (root / ".claude-plugin").mkdir(parents=True)
            (root / ".claude-plugin/plugin.json").write_text('{"version": "9.9.9"}')
            (root / "research_harness").mkdir()
            (root / "bin").mkdir()
            (root / "RESEARCH_CONSTITUTION.md").write_text("Version: 2\n")
            value = runtime_provenance(root)
        self.assertEqual((value["plugin_version"], value["source_commit"], value["dirty"]), ("9.9.9", None, None))

    def test_http_client_sends_the_plugin_version(self):
        from research_fixtures import client
        from research_harness.provenance import plugin_version
        http, wire, _ = client([(200, {"Content-Type": "application/json"}, b"{}")])
        http.get("https://example.org/x", accept=("application/json",))
        self.assertEqual(wire.requests[0][1]["User-Agent"], "exactory-research/" + plugin_version())
```

Add to `tests/test_research_cli.py` (inside `ResearchCliTests`, after `test_common_cli_exposes_json_preparation_and_readonly_status`):

```python
    def test_status_and_receipts_carry_runtime_provenance(self):
        from research_harness.storage import Store
        self.init_lab()
        status = json.loads(self.run_cli("exactory-research", "status").stdout)
        self.assertEqual(status["runtime"]["schema_version"], 1)
        self.assertEqual(len(status["runtime"]["package_digest"]), 64)
        records = Store(self.root).snapshot()["records"]
        receipt = next(iter(records["literature_operation"].values()))
        self.assertEqual(receipt["runtime"]["plugin_version"], status["runtime"]["plugin_version"])
```

Add to `tests/test_research_storage.py`-adjacent domain test (append to `tests/test_research_reading.py`):

```python
    def test_replay_ignores_runtime_differences(self):
        from unittest import mock
        from research_harness import operations
        from research_harness.reading import record_reading
        a = self.metadata()
        payload = self.abstract_note(a)
        first = record_reading(self.store, payload, expected_revision=self.store.revision, request_id="same-request")
        with mock.patch.object(operations, "runtime_provenance", return_value={"plugin_version": "0.0.0-other"}):
            again = record_reading(self.store, payload, expected_revision=self.store.revision, request_id="same-request")
        self.assertEqual(first, again)
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -p test_research_provenance.py -v` → FAIL (`No module named research_harness.provenance`).

- [ ] **Step 3: Implement**

```python
# research_harness/provenance.py
"""Identity of the running build. Recorded next to receipts; never a gate input."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .storage import _SCHEMA_VERSION

_ROOT = Path(__file__).resolve().parents[1]


def plugin_version(root=None):
    root = Path(root) if root else _ROOT
    return json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]


def _git(root):
    if not (root / ".git").exists() or shutil.which("git") is None:
        return None, None
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if commit.returncode or status.returncode:
        return None, None
    return commit.stdout.strip(), bool(status.stdout.strip())


def _package_digest(root):
    digest = hashlib.sha256()
    for path in sorted(list((root / "research_harness").glob("*.py")) + list((root / "bin").iterdir()) + [root / "RESEARCH_CONSTITUTION.md"]):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def runtime_provenance(root=None):
    root = Path(root) if root else _ROOT
    commit, dirty = _git(root)
    constitution = (root / "RESEARCH_CONSTITUTION.md").read_bytes()
    version = next((line[len("Version: "):].strip() for line in constitution.decode("utf-8").splitlines() if line.startswith("Version: ")), None)
    return {"plugin_version": plugin_version(root), "source_commit": commit, "dirty": dirty,
            "executable": str(Path(__file__).resolve().parents[1] / "bin/exactory-research"),
            "package_digest": _package_digest(root), "schema_version": _SCHEMA_VERSION,
            "constitution": {"version": version, "sha256": hashlib.sha256(constitution).hexdigest()}}
```

`operations.prepared_mutation`: `from .provenance import runtime_provenance`; the receipt put becomes `transaction.put("literature_operation", request_id, {"operation": operation, "runtime": runtime})` with `runtime = runtime_provenance()` computed once before `store.mutate`. `acquisition._begin`: add `"runtime": runtime_provenance()` to the admission record. `cli.status_report`: add `runtime=runtime_provenance()`. `http.HttpClient.__init__`: `user_agent=None` default, then `self.user_agent = user_agent or "exactory-research/" + plugin_version()` (import inside `__init__` to avoid a cycle: `from .provenance import plugin_version`). `bin/exactory-research`: keep `_USER_AGENT` (manifest test requires it).

The constitution version test expects `"2"`; Task 14 bumps the constitution. Until then, assert `value["constitution"]["version"] == "1"` and change the literal in Task 14.

- [ ] **Step 4: Run** the three test files → PASS. Run `python3 -m unittest discover -s tests -p test_research_transport.py` and `-p test_manifest.py` → PASS.
- [ ] **Step 5: Commit** `feat(research): record runtime provenance on receipts and status`

### Task 2: One history replay per invocation (P2, store)

**Files:**
- Modify: `research_harness/storage.py:138-193, 256-266`
- Test: `tests/test_research_storage.py`

**Interfaces:**
- `_validate(connection, *, replay=True)`: structural checks always (metadata, schema, quick_check, journal mode); event replay and record comparison only when `replay` is true.
- `Store.__init__` calls `_validate(connection, replay=False)`; `revision`, `snapshot`, `guarded_snapshot`, `committed_request`, and `mutate` keep the full replay.

- [ ] **Step 1: Failing tests** (append to `StorageTests` in `tests/test_research_storage.py`)

```python
    def test_construction_checks_structure_and_first_read_replays_history(self):
        from unittest import mock
        from research_harness import storage
        calls = []
        original = storage._validate
        def counting(connection, **kwargs):
            calls.append(kwargs.get("replay", True))
            return original(connection, **kwargs)
        with mock.patch.object(storage, "_validate", counting):
            store = storage.Store(self.workspace)
            store.snapshot()
        self.assertEqual(calls, [False, True])

    def test_record_tampering_is_caught_at_first_use(self):
        import sqlite3
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE records SET value = '{\"x\":1}', digest = 'bad' WHERE rowid = (SELECT MIN(rowid) FROM records)")
        connection.commit(); connection.close()
        store = Store(self.workspace)
        self.assert_error("corrupt_state", store.snapshot)
        self.assert_error("corrupt_state", lambda: store.mutate("op", {}, lambda tx: None, expected_revision=store_revision_placeholder, request_id="x"))
```

Replace `store_revision_placeholder` with the revision the fixture committed (read `self.store.revision` before tampering). Existing tests that assert `Store(root)` raises after `UPDATE records`/`UPDATE metadata` change to `Store(root).snapshot()`; tests asserting `unsupported_schema`, `DROP TABLE`, header, or journal failures at construction stay unchanged (structural checks remain in the constructor).

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -p test_research_storage.py -v` → the two new tests FAIL.
- [ ] **Step 3: Implement** in `_validate`: wrap the loop from `projections = {}` through the record comparison in `if replay:`; return `revision` either way. In `Store.__init__`: `_validate(connection, replay=False)`.
- [ ] **Step 4: Run** storage, snapshot-guard, CLI, and transport suites → PASS. Time `status` on a copy-free read of the closed-gravity study (read-only, `--workspace`) into the scratchpad and note wall time.
- [ ] **Step 5: Commit** `perf(store): replay history once per invocation`

### Task 3: Evaluation context (P2, gates)

**Files:**
- Create: `research_harness/evaluation.py`, `tests/test_research_evaluation.py`
- Modify: `research_harness/reading.py` (`_assess`, `current_readings`, `validate_read_evidence`, `fulltext_coverage`, `required_unit_obligations`, `record_reading`), `research_harness/cohort_evidence.py` (`cohort_report`, `cohort_reading_report`), `research_harness/literature.py` (`_search_evidence_digest`, `foundation_report`, `foundation_state`, `_work_paths`), `research_harness/synthesis.py` (`_record`, `synthesis_state`, `synthesis_report`), `research_harness/principles.py` (`configuration_state`), `research_harness/development.py` (`_Context`, `readiness_report`), `research_harness/gates.py` (`gate_report`), `research_harness/cli.py` (`status_report`), `research_harness/publication.py`, `research_harness/review_delivery.py`, `research_harness/native_export.py`, `research_harness/verification.py`

**Interfaces:**
- `Evaluation(records, artifacts)`: attributes `records`, `store`, `root`, `counters`; methods `read(reference)`, `text(reference)`, `put(data, media_type)`, `once(key, compute)`, `readings_for(version_id)`, `link(link)`; classmethod `of(records, artifacts)` returns `artifacts` itself when it is an `Evaluation` over the same `records` object, else a new one.
- Every gate function keeps its `(records, artifacts, ...)` signature and starts with `evaluation = Evaluation.of(records, artifacts)`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_research_evaluation.py
from literature_fixtures import LiteratureCase


class EvaluationTests(LiteratureCase):
    def test_bytes_are_verified_once_per_evaluation_and_tampering_is_still_caught(self):
        from research_harness.evaluation import Evaluation
        from research_harness.reading import record_reading
        a = self.metadata()
        self.mutate(record_reading, self.abstract_note(a))
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        reference = records["work"][a]["abstract"]
        self.assertEqual(evaluation.read(reference), evaluation.read(reference))
        self.assertEqual(evaluation.counters["artifacts_verified"], 1)
        self.assertEqual(evaluation.counters["reads"], 2)
        path = self.root / reference["path"]
        path.chmod(0o600); path.write_bytes(b"changed")
        self.assert_error("artifact_corrupt", lambda: Evaluation(records, self.artifacts).read(reference))

    def test_of_shares_memo_for_the_same_records_and_not_for_others(self):
        from research_harness.evaluation import Evaluation
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        self.assertIs(Evaluation.of(records, evaluation), evaluation)
        self.assertIsNot(Evaluation.of(self.store.snapshot()["records"], evaluation), evaluation)
        self.assertEqual(evaluation.once("k", lambda: 1), 1)
        self.assertEqual(evaluation.once("k", lambda: 2), 1)

    def test_status_computes_the_cohort_report_once(self):
        from research_harness.cli import status_report
        from research_harness import cohort_evidence
        from unittest import mock
        self.cohort((1,))
        original = cohort_evidence.cohort_report
        calls = []
        def counting(records, artifacts, ids, **kwargs):
            calls.append(1)
            return original(records, artifacts, ids, **kwargs)
        with mock.patch.object(cohort_evidence, "cohort_report", counting):
            report = status_report(self.store)
        self.assertEqual(report["preparation"], report["synthesis"]["foundation"]["cohort"]) if report.get("synthesis") else None
        self.assertLessEqual(len(calls), 1)

    def test_reports_are_identical_between_fresh_and_shared_evaluations(self):
        from research_harness.evaluation import Evaluation
        from research_harness.literature import foundation_state
        a = self.metadata()
        self.scope([a])
        records = self.store.snapshot()["records"]
        shared = Evaluation(records, self.artifacts)
        first = foundation_state(records, shared, "research")
        second = foundation_state(records, shared, "research")
        fresh = foundation_state(records, self.artifacts, "research")
        self.assertIs(first, second)
        self.assertEqual(first, fresh)
```

The third test needs the cohort gate to run through `foundation_state`; `status_report` calls gate_state("readiness") then gate_state("cohort") for a cohort-stage study; both must hit the memo. Assert `len(calls) == 1` once the memo exists (mocking the module attribute intercepts only the calls that go through `cohort_evidence.cohort_report`; `literature.foundation_state` imports the name directly, so patch `research_harness.literature.cohort_report` too, or assert on `evaluation.counters["cohort_reports"]`). Use the counter: `Evaluation.once` increments `counters["computed"]` per key and the test asserts `report["evaluation"]["cohort_reports"] == 1` after Task 4 exposes counters; until then assert through `mock.patch` of both module attributes.

- [ ] **Step 2: Run** → FAIL (`No module named research_harness.evaluation`).
- [ ] **Step 3: Implement**

```python
# research_harness/evaluation.py
"""Evidence access for one snapshot: verified bytes, indexes and memoized reports.

An Evaluation wraps the artifact store for one records snapshot. Every object
is read and hash-verified once per Evaluation; derived reports are computed
once per key. Nothing here survives the process or the snapshot it was built
for, and nothing here grants readiness.
"""

from .evidence import digest
from .source_links import validate_link


class Evaluation:
    def __init__(self, records, artifacts):
        self.records, self.store, self.root = records, artifacts, artifacts.root
        self._bytes, self._text, self._memo = {}, {}, {}
        self.counters = {"reads": 0, "artifacts_verified": 0, "bytes_verified": 0, "computed": 0,
                         "readings_assessed": 0, "links_validated": 0, "graph_builds": 0}

    @classmethod
    def of(cls, records, artifacts):
        if isinstance(artifacts, cls) and artifacts.records is records:
            return artifacts
        return cls(records, artifacts)

    def read(self, reference):
        self.counters["reads"] += 1
        key = (reference.get("sha256"), reference.get("size"), reference.get("path"), reference.get("media_type"))
        if key not in self._bytes:
            data = self.store.read(reference)
            self.counters["artifacts_verified"] += 1
            self.counters["bytes_verified"] += len(data)
            self._bytes[key] = data
        return self._bytes[key]

    def text(self, reference):
        key = reference["sha256"]
        if key not in self._text:
            self._text[key] = self.read(reference).decode("utf-8")
        return self._text[key]

    def put(self, data, media_type):
        return self.store.put(data, media_type)

    def once(self, key, compute):
        if key not in self._memo:
            self.counters["computed"] += 1
            self._memo[key] = compute()
        return self._memo[key]

    def readings_for(self, version_id):
        index = self.once(("readings",), lambda: _index(self.records.get("reading", {}).values()))
        return index.get(version_id, [])

    def link(self, link):
        def compute():
            self.counters["links_validated"] += 1
            return validate_link(self.records, self, link)
        return self.once(("link", digest(link)), compute)


def _index(readings):
    index = {}
    for reading in sorted(readings, key=lambda r: r["id"]):
        index.setdefault(reading["version_id"], []).append(reading)
    return index
```

Wire-up (each function begins with `evaluation = Evaluation.of(records, artifacts)` and uses `evaluation` where it used `artifacts`):
- `reading._assess`: use `evaluation.link(inspection["link"])` and pass `evaluation` down.
- `reading.current_readings`: iterate `evaluation.readings_for(version_id)`; `assessment = evaluation.once(("assessed", value["id"]), lambda: _assess(records, evaluation, value))` (increment `counters["readings_assessed"]` inside).
- `reading.validate_read_evidence`, `reading.fulltext_coverage`, `reading.required_unit_obligations`: pass `evaluation`.
- `cohort_evidence.cohort_report`: memo the whole report under `("cohort", tuple(collection_ids), digest(target))`; `abstract_reading` uses `evaluation.text(...)`.
- `literature.foundation_state`: memo under `("foundation", profile)`; `_search_evidence_digest(evaluation, scope, found)` uses `evaluation.once(("graph", profile), lambda: citation_graph(records, profile))`; `_work_paths(work, records, evaluation)`.
- `synthesis.synthesis_state`: memo under `("synthesis", profile)`; `synthesis._record.prepare` builds `Evaluation(records, artifacts)`.
- `principles.configuration_state`: memo under `("configuration", profile)`.
- `development._Context.__init__`: `self.artifacts = Evaluation.of(records, artifacts)`.
- Store-level wrappers (`foundation_report`, `synthesis_report`, `configuration_report`, `readiness_report`, `gate_report`, `cohort_reading_report`, `publication_report`, `validate_upload`, `deliver_*`, `export_native`, `validate_verdict`): construct one `Evaluation` from the snapshot and pass it.
- `cli.status_report`: one `Evaluation`; `preparation` for a non-cohort stage is `evaluation.once(("synthesis","research"|profile))` through `gate_state`, so it is free; for the cohort stage `gate_state(evaluation, "cohort")` hits the cohort memo populated by the readiness path.
- `literature.foundation_state` must call `cohort_report(records, evaluation, ids, target=target)` with the same `ids` list order as `gate_state` uses (`scope.get("collection_ids", sorted(records.get("collection", {})))`) so the memo key matches; make `gate_state` and `foundation_state` share one helper `_cohort_ids(records, profile)` in `cohort_evidence.py`.

Delete the dead `cohort_selection` read in `gates.py:42-43` (no writer exists); `gate_state` uses `_cohort_ids`.

- [ ] **Step 4: Run** the new file, then `test_research_reading.py`, `test_research_source_contracts.py`, `test_research_literature.py`, `test_research_synthesis.py`, `test_research_gates.py`, `test_research_development.py`, `test_research_publication.py`, `test_research_cli.py` → PASS. Run the math harness suite `python3 -m unittest discover -s skills/math-solver/harness/tests -t skills/math-solver/harness` → PASS.
- [ ] **Step 5: Commit** `perf(research): evaluate one snapshot once through an Evaluation`

### Task 4: Bounded reports, obligations pages, next priority (P1)

**Files:**
- Create: `research_harness/report_views.py`, `tests/test_research_report_views.py`
- Modify: `research_harness/cli.py` (parser, dispatch, `status_report`), `research_harness/synthesis.py:309-310` and `research_harness/literature.py:478,504` (`next` ordering), `docs/research-cli.md` (status section), `tests/test_research_cli.py`

**Interfaces:**
- `report_views.status_summary(report, counters=None, elapsed=None) -> dict`, `report_views.next_summary(report) -> dict`, `report_views.obligations_page(report, code, *, limit, cursor) -> dict`, `report_views.priority(obligation) -> int`, `report_views.order_obligations(obligations) -> list`.
- CLI: `status --summary`, `next --summary`, `obligations --code CODE [--limit N] [--cursor C]`.
- `status_report(store, *, counters=False)` adds `evaluation` (counters and elapsed seconds) when asked.

- [ ] **Step 1: Failing tests** — use the eight tests from `misc/harness-improvement/v2/compact-agent-reports-implementation-plan.md` Task 1 verbatim, plus:

```python
    def test_obligations_page_is_bound_to_the_revision(self):
        from research_harness.report_views import obligations_page
        report = report_fixture()
        report["obligations"] = [{"code": "abstract_reading_missing", "version_id": "arxiv:2601.%05dv1" % i} for i in range(30)]
        page = obligations_page(report, "abstract_reading_missing", limit=10, cursor=None)
        self.assertEqual((page["total"], page["offset"], page["returned"]), (30, 0, 10))
        second = obligations_page(report, "abstract_reading_missing", limit=10, cursor=page["next_cursor"])
        self.assertEqual(second["offset"], 10)
        with self.assertRaises(ResearchError) as raised:
            obligations_page(dict(report, revision=28), "abstract_reading_missing", limit=10, cursor=page["next_cursor"])
        self.assertEqual(raised.exception.code, "stale_cursor")

    def test_next_prefers_earlier_preparation_stages(self):
        from research_harness.report_views import order_obligations
        items = [{"code": "synthesis_dependencies_stale"}, {"code": "abstract_reading_missing", "version_id": "b"},
                 {"code": "abstract_reading_missing", "version_id": "a"}, {"code": "collection_pending"}, {"code": "objective_missing"}]
        self.assertEqual([o["code"] for o in order_obligations(items)][:3], ["objective_missing", "collection_pending", "abstract_reading_missing"])
        self.assertEqual(order_obligations(items)[2]["version_id"], "a")
```

CLI test (from the compact plan Task 2 Step 1, plus): `obligations --code cohort_abstract_reading_missing --limit 1` returns JSON with `total`, `returned == 1`, `next_cursor`, and the store snapshot is unchanged; `--summary` output sizes are bounded; `status --summary` contains `runtime`, `evaluation`, and `resources` keys.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `report_views.py` from the compact plan (`_short`, `_hint`, `_obligations`, `next_summary`, `status_summary`) and add:

```python
PRIORITY = ("migration_required", "profile_mismatch", "configuration_missing", "constitution_revalidation_required",
            "constitution_archive_missing", "collection_pending", "cohort_missing", "cohort_abstract_reading_missing",
            "screening_missing", "screening_audit_reading_missing", "screening_audit_failed", "doctrine_coverage_missing",
            "objective_missing", "objective_mismatch", "roots_missing", "root_missing", "target_source_pin_missing",
            "target_source_pin_invalid", "target_mismatch", "critical_source_unavailable", "fulltext_reading_missing",
            "source_bundle_missing", "source_bundle_incomplete", "required_unit_missing", "required_unit_incomplete",
            "required_unit_uninspected", "reading_bundle_stale", "bibliography_incomplete", "reference_identity_ambiguous",
            "reference_unresolved", "search_purpose_missing", "search_scope_stale", "search_frontier_stale",
            "search_evidence_stale", "search_dispositions_missing", "search_pending", "abstract_reading_missing",
            "historical_version_unresolved", "standards_missing", "rationale_missing", "innovation_missing",
            "context_missing", "synthesis_dependencies_stale", "resource_budget_exhausted")


def priority(item):
    code = item.get("code")
    return PRIORITY.index(code) if code in PRIORITY else len(PRIORITY)


def order_obligations(obligations):
    return sorted(obligations, key=lambda o: (priority(o), o.get("version_id") or "", digest(o)))


def obligations_page(report, code, *, limit, cursor):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ResearchError("invalid_input", "Page limit must be between 1 and 500")
    offset = 0
    if cursor is not None:
        try:
            revision, offset = (int(part) for part in cursor.split(":"))
        except (AttributeError, ValueError) as error:
            raise ResearchError("invalid_input", "Cursor must be REVISION:OFFSET") from error
        if revision != report["revision"]:
            raise ResearchError("stale_cursor", "The store changed; restart the listing", {"revision": report["revision"]})
    items = [o for o in order_obligations(report["obligations"]) if o.get("code") == code]
    page = items[offset:offset + limit]
    end = offset + len(page)
    return {"schema": "research-obligations-page-v1", "revision": report["revision"], "code": code, "total": len(items),
            "offset": offset, "returned": len(page), "next_cursor": None if end >= len(items) else str(report["revision"]) + ":" + str(end),
            "obligations": page}
```

`status_summary` adds `runtime`, `resources` (from Task 13; until then `report.get("resources", {})`), and `evaluation` (counters plus `elapsed_seconds`). `synthesis._unique` and `literature` sort keep their digest order for the *report*; `status_report` sets `next` with `order_obligations(...)[0]` and the compact hint uses it. CLI: add `--summary` on `status`/`next`, a new `obligations` subparser with `--workspace`, `--code` (required), `--limit` (default 50), `--cursor`; dispatch `report = status_report(store, counters=args.summary)`; `obligations` returns `obligations_page(status_report(store), args.code, limit=args.limit, cursor=args.cursor)`.

Document in `docs/research-cli.md` "Status, projections, and recovery": the flags, the 16 KiB / 4 KiB bounds, the advisory rule, the `obligations` page and `stale_cursor`, and that `next` orders by preparation stage. Add the two commands to the `## Initiate and resume` block of `docs/research-workflow.md` as ```sh lines (`exactory-research status --summary`, `exactory-research next --summary`, `exactory-research obligations --code abstract_reading_missing --limit 20`).

- [ ] **Step 4: Run** views, CLI, guidance suites → PASS.
- [ ] **Step 5: Commit** `feat(research): bounded status, next and obligations pages`

## Release B

### Task 5: Span locators (P3)

**Files:**
- Modify: `research_harness/source_links.py` (`read_locator`, `link_identity`, `contains`, `covers_text`, `_within_metadata_assertion`), `research_harness/cohort_evidence.py` (`abstract_reading`), `research_harness/reading.py:118-122` (abstract unit check), `docs/research-cli.md:39`, `docs/research-cli-examples.json` (every `"kind": "text"` locator → span), `tests/literature_fixtures.py` (`span`), `tests/test_research_source_contracts.py`
- Test: `tests/test_research_source_contracts.py`

**Interfaces:**
- Locator `{kind:"span", start, end, sha256, excerpt?}`; `source_links.span_locator(text, start, end, excerpt_length=200) -> dict` builds one from decoded content.
- `link_identity` returns `{"version_id", "original_sha256", "sha256", "locator": {"kind": "text-span", "start", "end", "sha256"}}` for both text kinds.

- [ ] **Step 1: Failing tests**

```python
    def test_span_locator_validates_by_hash_and_shares_identity_with_text(self):
        import hashlib
        from research_harness.source_links import contains, covers_text, link_identity, read_locator, span_locator
        a = self.metadata()
        records = self.store.snapshot()["records"]
        item = records["work"][a]["abstracts"][0]
        content = self.artifacts.read(item["artifact"]).decode()
        span = span_locator(content, 0, len(content))
        self.assertEqual(span["sha256"], hashlib.sha256(content.encode()).hexdigest())
        self.assertEqual(read_locator(self.artifacts, item["artifact"], span), content)
        text = {"kind": "text", "start": 0, "end": len(content), "quote": content}
        as_text = {"version_id": a, "source_id": item["source_id"], "artifact": item["artifact"], "locator": text}
        as_span = dict(as_text, locator=span)
        self.assertEqual(link_identity(as_text, records), link_identity(as_span, records))
        self.assertTrue(contains(as_text, as_span, records) and contains(as_span, as_text, records))
        self.assertTrue(covers_text(self.artifacts, as_span))
        self.assert_error("invalid_locator", lambda: read_locator(self.artifacts, item["artifact"], dict(span, sha256="0" * 64)))
        self.assert_error("invalid_locator", lambda: read_locator(self.artifacts, item["artifact"], dict(span, excerpt="x" * 201)))

    def test_span_offsets_are_code_points_across_combining_marks_and_crlf(self):
        from research_harness.source_links import read_locator, span_locator
        content = "Áb\r\nc☃\fd"
        artifact = self.artifacts.put(content.encode("utf-8"), "text/plain; charset=utf-8")
        span = span_locator(content, 2, 7)
        self.assertEqual(read_locator(self.artifacts, artifact, span), content[2:7])

    def test_span_abstract_reading_discharges_a_cohort_member(self):
        from research_harness.cohort_evidence import cohort_reading_report
        from research_harness.reading import record_reading
        from research_harness.source_links import span_locator
        collection = self.cohort((1,))
        a = "arxiv:2601.00001v1"
        note = self.abstract_note(a)
        content = self.artifacts.read(note["inspections"][0]["link"]["artifact"]).decode()
        note["inspections"][0]["link"]["locator"] = span_locator(content, 0, len(content))
        self.mutate(record_reading, note)
        self.assertTrue(cohort_reading_report(self.store, [collection])["ready"])
```

- [ ] **Step 2: Run** `-p test_research_source_contracts.py` → FAIL.
- [ ] **Step 3: Implement** in `source_links.py`:

```python
TEXT_KINDS = ("text", "span")


def span_locator(content, start, end, excerpt_length=200):
    span = content[start:end]
    locator = {"kind": "span", "start": start, "end": end, "sha256": hashlib.sha256(span.encode("utf-8")).hexdigest()}
    if excerpt_length:
        locator["excerpt"] = span[:excerpt_length]
    return locator
```

`read_locator`: branch `kind in TEXT_KINDS` decodes; for `span`: `fields(locator, ("kind", "start", "end", "sha256"), ("excerpt",))`, offsets as for text, `hashlib.sha256(content[start:end].encode("utf-8")).hexdigest() == locator["sha256"]`, `excerpt` (when present) is a str of at most 200 characters equal to `content[start:start+len(excerpt)]`; the span must be non-blank; return `content[start:end]`. `_within_metadata_assertion`: accept both kinds. `link_identity`: normalize `locator` to `{"kind": "text-span", "start", "end", "sha256"}` for text kinds (text: hash of `quote`). `contains`: `a["kind"] in TEXT_KINDS and b["kind"] in TEXT_KINDS` → offset containment; `html` anchors accept both kinds. `covers_text`: `kind in TEXT_KINDS`. `cohort_evidence.abstract_reading`: compare `" ".join(read_locator(evaluation, unit_link.artifact, unit_link.locator).split())` with `abstract_text` (the locator returns the substring for both kinds). `reading._assess` line 119: `u["link"]["locator"]["kind"] in TEXT_KINDS`. `literature_fixtures.span` keeps building `text` locators (legacy coverage) and gains `span=True` parameter used by new tests; `docs/research-cli-examples.json` switches every `text` locator to `span` (sha256 of `"Source passage."`), `docs/research-cli.md:39` documents both kinds and states that `span` is the recommended form.

- [ ] **Step 4: Run** source contracts, reading, literature, synthesis, development, guidance, CLI suites → PASS.
- [ ] **Step 5: Commit** `feat(research): span locators reference saved text by hash`

### Task 6: Unit digest and bibliography digest (P3)

**Files:**
- Modify: `research_harness/reading.py` (`bundle_digest` → `unit_digest` + `bibliography_digest`, `current_readings`, `_assess`), `research_harness/literature.py:239` (search evidence uses `unit_digest`), `research_harness/literature.py:176` (bundle result digest)
- Test: `tests/test_research_reading.py`

**Interfaces:**
- `reading.unit_digest(bundle, records) -> str`, `reading.bibliography_digest(bundle) -> str`; `bundle_digest` remains as an alias of `unit_digest` for callers; reading assessments store `unit_digest` and `bibliography_digest`.
- `current_readings` compares `unit_digest(records["source_bundle"][reading["bundle_id"]])` with `unit_digest(selected)`.

- [ ] **Step 1: Failing tests**

```python
    def test_parsed_bibliography_does_not_invalidate_a_fulltext_reading(self):
        from research_harness.literature import import_bundle
        from research_harness.reading import current_readings, record_reading
        a = self.metadata()
        capture = self.capture(a)
        bundle = self.bundle(a, capture)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        entry = {"target": None, "kind": "nonpaper", "reason": "A book.", "link": bundle["units"][1]["link"]}
        self.mutate(import_bundle, dict(bundle, id="bundle-bib", bibliography=dict(bundle["bibliography"], entries=[entry])))
        accepted, partial = current_readings(self.store.snapshot()["records"], self.artifacts, a)
        self.assertEqual([r["id"] for r in accepted], ["full"])
        self.assertEqual(partial, [])

    def test_a_new_required_unit_still_stales_the_reading(self):
        from research_harness.literature import import_bundle
        from research_harness.reading import current_readings, record_reading
        a = self.metadata()
        capture = self.capture(a)
        bundle = self.bundle(a, capture)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        extra = {"id": "supplement", "kind": "supplement", "required": True, "link": None, "reason": "Data appendix.", "url": "https://example.org/s"}
        self.mutate(import_bundle, dict(bundle, id="bundle-2", units=bundle["units"] + [extra]))
        accepted, partial = current_readings(self.store.snapshot()["records"], self.artifacts, a)
        self.assertEqual(accepted, [])
        self.assertEqual(partial[0]["assessment"]["pending"][0]["code"], "reading_bundle_stale")

    def test_legacy_assessment_digest_is_not_compared(self):
        from research_harness.literature import import_bundle
        from research_harness.reading import current_readings, record_reading
        a = self.metadata()
        bundle = self.bundle(a)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        records = self.store.snapshot()["records"]
        records["reading"]["full"]["assessment"]["bundle_digest"] = "0" * 64
        accepted, _ = current_readings(records, self.artifacts, a)
        self.assertEqual([r["id"] for r in accepted], ["full"])
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: split the digest; `_assess` returns `unit_digest` and `bibliography_digest` (keep `bundle_digest` key equal to `unit_digest` for compatibility); `current_readings`: `own = records["source_bundle"].get(value.get("bundle_id"))`; stale when `own is None or selected is None or unit_digest(own, records) != unit_digest(selected, records)`.
- [ ] **Step 4: Run** reading, literature, source-contract, synthesis suites → PASS.
- [ ] **Step 5: Commit** `feat(research): readings depend on units, not bibliography entries`

### Task 7: Extraction diagnostics and options (P3)

**Files:**
- Modify: `research_harness/fulltext.py` (`PdfExtractor.__init__(layout=True)`, `PdfExtractor.version()`, `extraction_measures(text, original_size)`, `extract(..., layout=True)`), `research_harness/acquisition.py:472-520` (`extraction_options`), `research_harness/cli.py:104-106`, `docs/research-cli.md:94`, `docs/research-cli-examples.json` (`fulltext`)
- Test: `tests/test_research_fulltext.py`, `tests/test_research_acquisition.py`

**Interfaces:**
- `fulltext.extraction_measures(text: str, original_size: int) -> dict` with `text_bytes, page_count, max_line_length, whitespace_fraction, expansion_ratio`.
- Capture gains `"extraction": {"extractor", "version", "options": {"layout"}, ...measures}`.
- `acquire_fulltext(..., extraction_options=None)`; CLI `fulltext` payload optional `extraction_options: {"layout": bool}`.

- [ ] **Step 1: Failing tests**

```python
    def test_extraction_measures_describe_layout_padding(self):
        from research_harness.fulltext import extraction_measures
        text = "a" + " " * 999 + "\n\fb\n"
        measures = extraction_measures(text, original_size=100)
        self.assertEqual(measures["page_count"], 2)
        self.assertEqual(measures["max_line_length"], 1000)
        self.assertGreater(measures["whitespace_fraction"], 0.99)
        self.assertAlmostEqual(measures["expansion_ratio"], len(text.encode()) / 100)

    def test_pdf_extractor_records_options_and_version(self):
        # authored executable prints "pdftotext version 9.9" on -v and echoes argv otherwise (see existing fixture pattern)
        ...
        self.assertEqual(extractor.version(), "9.9")
        self.assertNotIn("-layout", argv_without_layout)
```

Acquisition test: `acquire_fulltext(..., extractor=lambda data: {...}, extraction_options={"layout": False})` stores `capture["extraction"]["options"] == {"layout": False}` and the measures; a second capture with `{"layout": True}` of the same original is appended and `fulltext_coverage` accepts a reading whose bundle uses either capture.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: `PdfExtractor(layout=True)` builds argv with `-layout` only when `layout`; `version()` runs `[exe, "-v"]` with `capture_output=True, timeout=5` and parses `version (\S+)` from stderr+stdout, returning `None` on any failure; `extract(data, media_type, *, extractor=None, layout=True)` passes `layout` to the default extractor and returns `extractor_name` (`"pdftotext"`/`"html"`), `extractor_version`; `acquire_fulltext` validates `extraction_options` (`fields(..., (), ("layout",))`, bool), computes `extraction_measures(text, len(response.body))`, and records `capture["extraction"]`. `cli.acquisition_command` passes the optional field.
- [ ] **Step 4: Run** fulltext, acquisition, reading suites → PASS.
- [ ] **Step 5: Commit** `feat(research): record extraction diagnostics and options per capture`

### Task 8: `read-batch` and `batches` export (P4)

**Files:**
- Create: `research_harness/batches.py`, `tests/test_research_batches.py`
- Modify: `research_harness/reading.py` (`record_reading_batch`), `research_harness/cli.py` (OPERATIONS, `batches` subparser), `docs/research-cli.md`, `docs/research-cli-examples.json` (`read-batch`), `docs/research-workflow.md` (cohort block)

**Interfaces:**
- `reading.record_reading_batch(store, payload, *, expected_revision, request_id)`; payload per spec §6.1; result `{"id", "items": [{"version_id", "reading_id", "status", "includes_abstract"}], "count"}`; errors `invalid_batch` with `details["items"] = [{"index", "code", "message"}]`.
- `batches.export_batches(store, *, depth, size, destination, screen=False, profile="research") -> {"files": [...], "entries": int}`.
- Reading ids: `"reading:" + digest([batch_id, version_id])`.

- [ ] **Step 1: Failing tests**

```python
from literature_fixtures import LiteratureCase, FIELDS


def item(version_id, **extra):
    return dict({"version_id": version_id, "note": "Read the complete abstract.",
                 "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS}}, **extra)


class ReadBatchTests(LiteratureCase):
    def test_batch_records_expanded_abstract_readings_in_one_event(self):
        from research_harness.reading import record_reading_batch
        ids = [self.metadata(n) for n in (1, 2, 3)]
        before = self.store.revision
        result = self.mutate(record_reading_batch, {"id": "batch-1", "depth": "abstract", "items": [item(i) for i in ids]})
        self.assertEqual(self.store.revision, before + 1)
        self.assertEqual(result["result"]["count"], 3)
        records = self.store.snapshot()["records"]
        self.assertEqual(len(records["reading"]), 3)
        reading = next(iter(records["reading"].values()))
        self.assertEqual(reading["inspections"][0]["link"]["locator"]["kind"], "span")
        self.assertEqual(reading["assessment"]["status"], "complete")

    def test_invalid_item_rejects_the_whole_batch_with_indices(self):
        from research_harness.reading import record_reading_batch
        ids = [self.metadata(n) for n in (1, 2)]
        bad = item(ids[1]); bad["notes"]["problem"]["status"] = "maybe"
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as raised:
            self.mutate(record_reading_batch, {"id": "batch-2", "depth": "abstract", "items": [item(ids[0]), bad]})
        self.assertEqual(raised.exception.code, "invalid_batch")
        self.assertEqual([x["index"] for x in raised.exception.details["items"]], [1])
        self.assertEqual(self.store.snapshot(), before)

    def test_duplicate_versions_and_oversized_batches_are_refused(self): ...
    def test_replay_and_conflict_follow_request_identity(self): ...
    def test_screening_items_store_screening_records(self):  # relevance/reason stored under screening/<key>
        ...

class BatchExportTests(LiteratureCase):
    def test_export_writes_unread_abstracts_without_touching_the_store(self):
        from research_harness.batches import export_batches
        collection = self.cohort((1, 2, 3))
        before = self.store.snapshot()
        result = export_batches(self.store, depth="abstract", size=2, destination=self.root / "cohort/batches")
        self.assertEqual(result["entries"], 3)
        self.assertEqual(len(result["files"]), 2)
        first = json.loads((self.root / "cohort/batches/batch-001.json").read_text())
        self.assertEqual(set(first["items"][0]), {"version_id", "title", "authors", "published", "categories", "text", "link", "extraction"})
        self.assertEqual(self.store.snapshot(), before)
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `record_reading_batch.prepare`: validate `fields(value, ("id","depth","items"), ("usage",))`, `depth == "abstract"`, `1 <= len(items) <= 100`, unique `version_id`s; build one `Evaluation(records, ArtifactStore(store.root))`; for each item: `fields(item, ("version_id","note","notes"), ("screening","audit","consequential"))`, resolve `work = exact_work(records, version_id)`, `abstract = next(a for a in work["abstracts"] if a["completeness"] == "complete")` else error `abstract_missing`, link = `{version_id, source_id: abstract["source_id"], artifact: abstract["artifact"], locator: span_locator(evaluation.text(abstract["artifact"]), 0, len(text))}`, expand to a `read` payload with `notes[f]["inspections"] = [0]`, run `_assess`, collect `immutable_record("reading", ...)`; collect failures as `{index, code, message}` and raise `invalid_batch` if any; `screening` present → Task 15 record (in this task store `screening_note` on the reading assessment as `screening` and leave the screening record to Task 15); `usage` shape validated (`model` str|null, three numbers|null) and returned. `batches.export_batches`: build the current obligation list from `status_report`-equivalent (`gate_state(evaluation, "cohort")` inventory items with `reading_id None` plus `abstract_reading_missing` obligations), fetch each work's title/authors/date/categories/text, write `batch-NNN.json` files with `{"id": "batch-NNN", "depth": "abstract", "items": [...]}` under `destination` (created with `mkdir(parents=True, exist_ok=False)`), and a `README.json` naming the expected notes file shape. CLI: `OPERATIONS["read-batch"] = reading.record_reading_batch`; subparser `batches` with `--workspace`, `--depth` (choices abstract), `--size` (default 60), `--destination`, `--screen`, `--profile`. Docs table row and example.
- [ ] **Step 4: Run** batches, reading, CLI, guidance suites → PASS.
- [ ] **Step 5: Commit** `feat(research): read-batch operation and batches export`

### Task 9: Entry-level collection completeness and retained exclusions (P4)

**Files:**
- Modify: `research_harness/acquisition.py` (`_page_update`, `_collection_summary`, `resume_cohort`, `acquire_work`, `acquire_fulltext`)
- Test: `tests/test_cohort_collection.py`, `tests/test_research_acquisition.py`

**Interfaces:**
- `cohort_partition_entry/<digest([collection_id, partition_id, epoch, exact_id])>` records; partition gains `entries_seen`.
- Failure codes `out_of_scope_date`, `out_of_partition_date`, `missing_primary_category`, `invalid_primary_category` become per-entry `cohort_exclusion` reasons (`exclusion_reason`) and never restart.
- `acquire_work`/`acquire_fulltext` pass `not_before` from the latest source record for the identifier.

- [ ] **Step 1: Failing tests** (in `test_cohort_collection.py`): (a) a listing returning `2601.00001v1` and `2601.00001v2` with total 2 completes with `member_count == 1` and `entries == 2`; (b) an entry with an invalid primary category becomes an exclusion with `exclusion_reason == "invalid_primary_category"` and the partition completes; (c) failure on the last page then resume fetches only the last page (assert wire request count); (d) a page returned twice (same start) is recorded as `duplicate_page` pending without restart; (e) `acquire_work` with a stored `next_eligible_at` in the future performs zero requests and returns `rate_limited` pending.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: in `_page_update`, per prepared entry write `cohort_partition_entry` keyed by exact id and count `partition["entries_seen"]`; completion when `offset == total and entries_seen == total`; move the four parse failures out of `failures` into `_member(..., "cohort_exclusion")` with `exclusion_reason`; remove `invalid_alias` and the four codes from the restart list; detect `page.start < partition["offset"]` as `duplicate_page` (pending, no restart, no re-count). `_collection_summary` counts `entries` and reports `exclusion_reasons`. `acquire_work`/`acquire_fulltext`: `not_before = max((s["next_eligible_at"] for s in records["source"].values() if s["requested_identifier"] == identifier and s["next_eligible_at"]), default=None)` passed to `http.get`.
- [ ] **Step 4: Run** cohort collection, acquisition, transport, CLI (`exactory-cohort`) suites → PASS.
- [ ] **Step 5: Commit** `fix(acquisition): complete partitions by entry and retain parse exclusions`

### Task 10: Search dispositions and frontier digests (P5, searches)

**Files:**
- Modify: `research_harness/literature.py` (`record_search`, `_search_evidence_digest` → `_content_digest`, new `frontier`, `foundation_state` search checks), `docs/research-cli.md:99`, `docs/research-cli-examples.json` (`search`), `tests/test_research_synthesis.py:95-106` (`foundation_searches` adds dispositions)
- Test: `tests/test_research_literature.py`

**Interfaces:**
- `literature.frontier(evaluation, profile) -> list[[family_id, tier]]`, `literature.frontier_digest(evaluation, profile) -> str`, `literature._content_digest(evaluation, scope, families) -> str`.
- Search record gains `dispositions`, `resolved`, `frontier_digest`; `evidence_digest` now covers only relevant families.
- Obligations `search_frontier_stale`, `search_dispositions_missing`; error `search_findings_dropped`.

- [ ] **Step 1: Failing tests**: (a) `record_search` without `dispositions` → `invalid_search`; legacy record (insert via fixture with old shape through `import_response` + a direct `store.mutate` of a `literature_search` record) → `search_dispositions_missing`; (b) `cited_work_ids` containing an `out_of_scope` work → `invalid_search`; (c) a second `direct` search that omits a `contradictory` work from the selected search → `search_findings_dropped`; with `resolved: [{work_id, reason}]` → accepted; (d) a new capture of a Tier 3 reference that is neither found nor cited leaves `search_evidence_stale` absent; a new family entering the graph produces `search_frontier_stale`; the existing root-capture test still passes.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per spec §7.1 and §7.2: `dispositions` required list of `{work_id, disposition, reason}` covering exactly `found_work_ids`; vocabulary check; `cited ⊆ relevant|contradictory`; carry-forward check against the selected search of the same purpose; `_content_digest` over families = roots ∪ requirement families ∪ found ∪ cited; `frontier_digest` over graph nodes + requirement families + selected searches' found/cited families; `foundation_state` compares all three and emits the three obligations; records lacking `dispositions` get `search_dispositions_missing`. Update the fixture `foundation_searches` and the example payload.
- [ ] **Step 4: Run** literature, synthesis, development, CLI, guidance suites → PASS.
- [ ] **Step 5: Commit** `feat(research): search dispositions, carry-forward and frontier freshness`

### Task 11: Section dependencies and preparation digest (P5, synthesis and development)

**Files:**
- Modify: `research_harness/synthesis.py:297-302` (`_assess` dependencies), `research_harness/synthesis.py:377` (`synthesis_state` digest and `preparation_digest`), `research_harness/development.py:132-135, 221-227, 655-663, 990-1000` (`preparation_digest` in place of `foundation["digest"]`), `docs/research-cli.md` (`cycle.literature.preparation_digest`), `docs/research-cli-examples.json` (`cycle`, `assess`), `tests/development_fixtures.py`
- Test: `tests/test_research_synthesis.py`, `tests/test_research_development.py`

**Interfaces:**
- `synthesis.section_dependencies(evaluation, kind, profile, configuration, evidence) -> dict` per spec §7.3.
- `synthesis_state(...)["preparation_digest"]`; plans, novelty, and candidates carry `preparation_digest` (field name replaces `foundation_digest` in `cycle.literature` and `development.novelty`; the legacy name is rejected with `invalid_development` naming the new field).

- [ ] **Step 1: Failing tests**: (a) after `prepared_study()`, recording one more cohort abstract reading leaves `rationale`, `innovation`, `context` current and stales nothing in synthesis; (b) recording a new `direct` search with a changed verdict stales `rationale`, `innovation`, `context` but not `standards`; (c) a new cohort member (resume adds one) stales `standards`; (d) `plan()` fixture uses `preparation_digest`; the existing `plan_dependencies_stale` test passes because `refresh_synthesis` changes section digests.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per spec §7.3; `synthesis_state` returns `preparation_digest = digest({configuration, scope, frontier, searches: selected judgments digests, sections: {kind: section digest}, requirements})`; development binds `preparation_digest` where it bound `foundation["digest"]` (`_Context.dependencies` key `preparation`; `_literature`, `_development` novelty, `_candidate.literature_digest`).
- [ ] **Step 4: Run** synthesis, development, gates, publication, execution, CLI, math harness suites → PASS.
- [ ] **Step 5: Commit** `feat(research): bind synthesis and plans to the dependencies they use`

### Task 12: Neutral review packets and duplicate review refusal (P7)

**Files:**
- Create: `research_harness/review_packets.py`, `tests/test_research_review_packets.py`
- Modify: `research_harness/review_delivery.py` (`deliver_readiness`, `deliver_manuscript`), `research_harness/publication.py` (`record_manuscript_review`, `publication_state`), `docs/research-cli.md:293-299`

**Interfaces:**
- `review_packets.readiness_packet(report) -> dict`, `review_packets.manuscript_packet(evaluation, bundle) -> dict`, `review_packets.scrub(value) -> value` (recursive removal of `request_id`, `token`, `authors`, keys ending `_revision`).
- Error `manuscript_review_duplicate`.

- [ ] **Step 1: Failing tests**: (a) `scrub` removes the forbidden keys at every depth including inside lists and nested manifests; (b) `deliver_manuscript` writes `inputs.json` whose JSON text contains none of `"request_id"`, `"token"`, `"authors"`, `"_revision"`, `"history"`, `"plan"`, `"strategy_accounts"`, `"next_hypothesis"`, and whose `evidence` covers every link in `claim_evidence`; every referenced artifact file exists with matching bytes; (c) `deliver_readiness` keeps `branches`, `plan`, `assessment`, `checkpoint`, `synthesis.sections` and drops labels; (d) a second `manuscript-review` by the same assessor id (case/whitespace variants) on the same `bundle_digest` → `manuscript_review_duplicate`; after a new bundle it is accepted.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per spec §8; `publication_state` uses the *first* review per assessor on a bundle (`reviewed_revision` minimum) and `record_manuscript_review` refuses duplicates.
- [ ] **Step 4: Run** packets, publication, gates suites → PASS.
- [ ] **Step 5: Commit** `feat(research): neutral reviewer packets and one review per assessor per bundle`

### Task 13: Resource budgets and accounts (P8)

**Files:**
- Create: `research_harness/resources.py`, `tests/test_research_resources.py`
- Modify: `research_harness/acquisition.py` (`_begin` reserve, `_finish` reconcile), `research_harness/reading.py` (`record_reading_batch` charge), `research_harness/screening.py` (Task 15 charge), `research_harness/literature.py` (`foundation_state` obligation), `research_harness/cli.py` (`budget` operation), `research_harness/report_views.py` (`resources`), `docs/research-cli.md`, `docs/research-cli-examples.json` (`budget`)

**Interfaces:**
- `resources.set_budget(store, payload, *, expected_revision, request_id)`; records `resource_budget/<profile>:<purpose>`.
- `resources.charge(records, profile, purpose, amounts: dict, *, unknown: dict) -> (kind, key, value)` returns the updated `resource_account` change and raises `resource_budget_exhausted` when a limit would be exceeded.
- `resources.reserve(records, profile, purpose, amounts)`, `resources.reconcile(records, profile, purpose, reserved, actual)`.
- `resources.account_report(records, profile) -> dict` for gates and summaries.

- [ ] **Step 1: Failing tests**: budget set/raise/lower rules; `read-batch` charges `readings` and `unknown` for null usage; acquisition reserves `max_requests` and reconciles to `attempts_used`; a batch that would exceed `readings` limit fails with `resource_budget_exhausted` and leaves the store unchanged; the foundation reports `resource_budget_exhausted` while charged ≥ limit; `status --summary` shows accounts; read commands unchanged.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per spec §10 (units list, null limits, reserve/reconcile inside the existing admission/finish events, obligation in `foundation_state`).
- [ ] **Step 4: Run** resources, batches, acquisition, literature, CLI suites → PASS.
- [ ] **Step 5: Commit** `feat(research): resource budgets and accounts`

## Release C

### Task 14: Preparation policy record and constitution Version 2 (P6, P0)

**Files:**
- Modify: `research_harness/principles.py` (`prepare_initialization`, `preparation_policy(records)`, `change_policy`), `research_harness/cli.py` (`policy` operation), `bin/exactory-lab` (`init --preparation-policy`), `RESEARCH_CONSTITUTION.md` (Version 2 section), `docs/research-cli.md`, `docs/research-cli-examples.json` (`init`, `policy`), `tests/test_research_principles.py`, `tests/test_research_provenance.py` (constitution version literal)

**Interfaces:**
- `principles.POLICIES = ("exhaustive-v1", "screened-v1")`; `principles.preparation_policy(records) -> str` (default `exhaustive-v1`); `principles.change_policy(store, payload, ...)` with `{previous, policy, reason}`.

- [ ] **Step 1: Failing tests**: default policy; `init` with `preparation_policy: "screened-v1"`; unknown policy → `invalid_input`; `policy` change requires matching `previous` and stales synthesis; constitution file has exactly one `Version: 2` line; `exactory-lab init --preparation-policy screened-v1` records it.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; write the constitution section per spec §9.7.
- [ ] **Step 4: Run** principles, CLI, guidance, lab suites → PASS.
- [ ] **Step 5: Commit** `feat(research): versioned preparation policy and constitution version 2`

### Task 15: Screening records and the screened cohort gate (P6)

**Files:**
- Create: `research_harness/screening.py`, `tests/test_research_screening.py`
- Modify: `research_harness/cohort_evidence.py` (`cohort_report` policy branch), `research_harness/literature.py` (Tier 3 branch), `research_harness/reading.py` (`read-batch` screening/audit fields), `research_harness/batches.py` (`--screen`), `research_harness/cli.py` (`screen-batch`), docs and examples

**Interfaces:**
- `screening.record_screening_batch(store, payload, ...)`; `screening.screenings_for(records, collection_id) -> dict`; `screening.audit_sample(collection_id, excluded_work_ids, size=150) -> list`; `screening.cohort_obligations(evaluation, collection, summary, readings) -> (obligations, counts)`; `screening.tier3_obligations(evaluation, profile, graph) -> list`.
- Obligations per spec §9.3 and §9.4.

- [ ] **Step 1: Failing tests** (one per rule): screening_missing; strong→promote required; exclude requires none; missing abstract cannot be excluded; promote/doctrine/pending need readings; audit sample deterministic and sized; screening_audit_reading_missing; screening_audit_failed until a higher round passes; doctrine_coverage_missing; Tier 3 screening keyed by family with context; `require-fulltext` family keeps obligations regardless; screen-batch limit 200 and charge; exhaustive policy ignores screenings.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per spec §9.2 to §9.4.
- [ ] **Step 4: Run** screening, cohort, literature, batches, CLI suites → PASS.
- [ ] **Step 5: Commit** `feat(research): screened preparation policy records and gate`

### Task 16: Saturation checkpoint (P6)

**Files:**
- Modify: `research_harness/screening.py` (`record_screening_checkpoint`, `saturation_covers(records, profile)`), `research_harness/cohort_evidence.py`, `research_harness/cli.py` (`screening-checkpoint`), docs and examples
- Test: `tests/test_research_screening.py`

- [ ] **Step 1: Failing tests**: checkpoint accepted only for the two most recent pending-only batches with no consequential item; unread pending members drop their obligation and appear in `counts.inventoried_unread`; a later consequential batch, a new round, or a policy change reinstates the obligations.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** per spec §9.5. **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(research): saturation checkpoint for screened preparation`

### Task 17: Policy report (P6 validation tooling)

**Files:**
- Modify: `research_harness/screening.py` (`policy_report(store, *, policy=None, reference=None)`), `research_harness/cli.py` (`policy-report`), `docs/research-cli.md`
- Test: `tests/test_research_screening.py`

- [ ] **Step 1: Failing tests**: report counts by disposition, sample, unread; recall per category against a reference file; read-only; `--policy screened-v1` on an exhaustive study reports the hypothetical set.
- [ ] **Step 2–5**: implement per spec §9.6, run, commit `feat(research): policy report for preparation-set validation`.

### Task 18: Hosts, skills, docs, version, measurement

**Files:**
- Modify: skills listed in spec §11, `codex/README.md`, `docs/research-workflow.md`, `docs/research-cli.md`, `README.md`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `tests/test_research_guidance.py:86-87`, `tests/test_manifest.py:40`, `tests/test_codex.py:25`
- Create: `docs/releases/0.38.0.md`, `docs/testing/literature-efficiency.md`
- Regenerate: `codex/skills/*/SKILL.md`, `codex/hooks.json`

- [ ] **Step 1**: update the three version literals and manifests to 0.38.0; create the release note (sections: Research workflow, Reliability corrections, Distribution and validation, Upgrade and resume; state that existing studies must revalidate the constitution, re-record synthesis sections, re-record searches with dispositions, and re-export native foundations).
- [ ] **Step 2**: edit skills and docs: resume reads `status --summary` and `next --summary`; cohort skill describes both policies and `batches`/`read-batch`; literature-review skill describes span locators, dispositions, Tier 3 screening under `screened-v1`; evaluate/write describe packets; workflow ```sh blocks parse.
- [ ] **Step 3**: `python3 codex/generate.py && python3 codex/generate.py --check`.
- [ ] **Step 4**: full suite `python3 -m unittest discover -s tests`, math harness suite, `python3 -m compileall -q hooks codex research_harness tests skills/math-solver/harness`, JSON validation loop from `ci.yml`.
- [ ] **Step 5**: measure on the closed-gravity study, read-only: `status` (wall, user, sys, RSS, bytes), `status --summary` bytes, `policy-report --policy screened-v1`; write `docs/testing/literature-efficiency.md` with the numbers and the baseline (78.03 s, 2.35 GB, 87,631,431 bytes).
- [ ] **Step 6: Commit** `release: 0.38.0 literature efficiency`

## Self-review

- Spec coverage: §2 → Task 1; §3 → Task 4; §4 → Tasks 2–3; §5 → Tasks 5–7; §6 → Tasks 8–9; §7 → Tasks 10–11; §8 → Task 12; §9 → Tasks 14–17; §10 → Task 13; §11–12 → Task 18.
- Placeholders: Task 7 Step 1 second test and Task 8 three tests are outlined; write them fully during execution following the neighbouring tests' pattern.
- Type consistency: `Evaluation.of`, `unit_digest`, `span_locator`, `preparation_digest`, `record_reading_batch`, `record_screening_batch`, `set_budget`, `readiness_packet`, `manuscript_packet` are the names used across tasks.
