# Mathematical Search Controller Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkboxes for tracking.

Goal: Implement the approved persistent objective controller and release-ready local Exactory 0.34.0 source, without modifying a live mathematical investigation.

Architecture: Keep the existing attack harness and its record formats. A separate Python package owns the immutable objective, event storage, admission, proof dependencies, budgets, and scheduling. CLI and hooks share its decisions; semantic reviews remain explicit trusted inputs rather than simulated theorem checking.

Tech Stack: Python 3.9+, standard library, unittest, existing Claude/Codex hook adapters.

Spec: `docs/superpowers/specs/2026-09-05-math-search-controller.md`

## Global Constraints

- Write all artifacts, code, comments, and generated reports in English.
- Use Python 3.9+ and the standard library.
- Keep 8 journal moves per pass, 3 passes, and the 24-move hard cap.
- Templates default to 24 runs, 300 seconds per run, and one compute worker.
- One admitted mathematical workload runs by default.
- One controller manages one original objective per attack root.
- Existing journal and native parent schemas remain readable and unchanged.
- No unmetered legacy execution flag is permitted.
- No new external dependency, network service, learned scheduling, or database service.
- Do not migrate the Dittert workspace as a plugin test.
- Do not automatically push, tag, publish, or change the user's hook trust.
- Work in the current checkout, explicitly authorized by the user on 2026-09-05.
- Commit only scoped task changes. Never use broad destructive cleanup or change unrelated user files.
- Research-style agent pressure-test artifacts belong under `/Users/ryshiro/exactory/exactory-research`, not the plugin checkout.
- Use one implementation agent at a time. Independent read-only reviews and behavioral probes may run alongside useful controller work.

## File and interface map

All implementation paths below are relative to this plugin root. The new package
is `skills/math-solver/harness/search_controller/`.

| File | Responsibility |
| --- | --- |
| `errors.py`, `storage.py` | Structured failures, canonical JSON, safe paths, immutable blobs, locked atomic events |
| `schema.py`, `admission.py`, `model.py` | Closed schemas, reviewed proposals, immutable obligations, budget accounts, pure event replay |
| `proof.py`, `scheduler.py` | Assumption-safe AND/OR propagation, checkpoint/retreat logic, deterministic next action |
| `service.py`, `cli.py`, `render.py` | Filesystem-backed commands, adoption, scope/evidence validation, generated views |
| `adoption.py` | Explicit preserved legacy mappings, snapshots, and stable imported usage |
| `evidence.py` | Immutable manifest schema and read-only audit in Task 4; capture/freeze support in Task 5 |
| `execution.py` | Metered processes, launcher ownership, and execution recovery |
| `integration.py` | Existing harness admission guards, journal intents, stage/finish and verifier integration |
| `hooks/math_search.py` | Shared host discovery, normalization, controller invocation, protected-path decisions |
| `skills/math-solver/SEARCH.md` | Operational search workflow and JSON contract examples |

The following public interfaces are binding between tasks:

```python
class SearchError(Exception):
    # Attributes: code: str, message: str, details: dict
    # Constructor: SearchError(code, message, details=None)
    pass

def canonical_bytes(value: object) -> bytes:  # Sorted, compact UTF-8 JSON; reject NaN.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")

class Store:
    # Store(root: Path)
    # initialize(contract: dict, objective_id: str) -> dict
    # read() -> dict
    # put_blob(value: dict) -> str
    # get_blob(digest: str) -> dict
    # put_artifact(data: bytes) -> str
    # get_artifact(digest: str) -> bytes
    # append(kind: str, payload: dict, expected_revision: int,
    #        request_id: str, validate=None) -> dict
    # validate(document, candidate_event) runs under the writer lock.
    pass

def initial_state(contract: dict, objective_id: str) -> dict:
    # Task 2 defines the validated state; there is no independently saved state cache.
    return {"objective_id": objective_id, "contract": contract, "revision": 0}

def apply_event(state: dict, event: dict) -> dict:
    # Pure validation and transition, implemented by Tasks 2 and 3.
    return EVENT_HANDLERS[event["kind"]](state, event)

def replay(document: dict) -> dict:
    state = initial_state(document["contract"], document["objective_id"])
    for event in document["events"]:
        state = apply_event(state, event)
    return state

def next_action(state: dict) -> dict:
    # Returns {"action": str, "reason": str, "node_id": str | None, ...}.
    # Task 3 owns the exact tagged action variants and their closed schema.
    return choose_frontier_action(state)

class Controller:
    # Controller(root: Path, strategies_dir: Path | None = None)
    # command(name: str, spec: dict, expected_revision: int | None,
    #         request_id: str | None, target: str | None = None) -> dict
    # status(full_audit: bool = False) -> dict
    # guard_legacy(command: str, slug: str, details: dict) -> dict
    pass
```

The code blocks above define interfaces, not permission to ship unimplemented
stubs. Each task delivers implemented functions and behavioral tests. Use ordinary
Python 3.9 annotations in production, not the explanatory union syntax in comments.
Record exact normalized event/payload schemas in `SEARCH.md` as they become executable;
later tasks consume those schemas, not alternate spellings. The controller has no
public arbitrary-event injection command. Internal verified facts are produced by
the service, not accepted as a caller's `verified: true` assertion.

## Task 1: Crash-safe event and artifact storage

Files: create `search_controller/__init__.py`, `errors.py`, `storage.py`; create
`harness/tests/test_search_storage.py`.

Interfaces: produce `SearchError`, `canonical_bytes`, `Store`, and
`safe_path(root: Path, relative: str) -> Path`. Store envelopes have exactly
`schema_version`, `objective_id`, `contract`, and `events`. Event fields are
`sequence`, `request_id`, `kind`, and `payload`; revision equals event count.

- [ ] Write storage tests before the package exists. Include this behavioral core:

```python
def test_stale_writer_cannot_replace_committed_event(self):
    store = Store(self.root)
    store.initialize({"claim": "A"}, "objective-a")
    store.append("record", {"value": 1}, 0, "request-one")
    with self.assertRaises(SearchError) as caught:
        store.append("record", {"value": 2}, 0, "request-two")
    self.assertEqual(caught.exception.code, "revision_conflict")
    self.assertEqual(len(store.read()["events"]), 1)
```

- [ ] Run `python3 -m unittest discover -s skills/math-solver/harness/tests -t skills/math-solver/harness -p test_search_storage.py -v`; record the expected missing-module RED.
- [ ] Implement canonical serialization, duplicate-key/nonfinite rejection on reads,
  envelope validation, idempotent replay of identical request IDs, rejection of
  conflicting ID reuse, and validation before append. Identical replay takes priority
  over the stale revision check and must not call a side-effecting validator twice.
- [ ] Use a per-root advisory OS lock with bounded acquisition on supported POSIX hosts;
  use temporary sibling files, flush/fsync, atomic replace, and directory fsync.
  Write content-addressed artifacts with digest checks. Reject path traversal, escaped
  symlinks, corrupt blobs, malformed revisions, and a symlinked controller directory.
  Do not run subprocesses while holding this storage lock.
- [ ] Add real two-writer tests, interrupted replacement tests, corrupt-state rejection,
  immutable-blob collision rejection, and path containment tests. Test preserved old
  bytes after a rejected transaction, not merely a mocked call count.
- [ ] Run the focused test and existing harness suite, self-review, and commit the task.

## Task 2: Closed records, proposal review, and inherited accounts

Files: create `search_controller/schema.py`, `admission.py`, `model.py`;
create `harness/tests/test_search_admission.py`, `tests/search_fixtures.py`;
start executable-schema documentation in `skills/math-solver/SEARCH.md` without
changing `SKILL.md` guidance yet.

Interfaces: consume Store/error utilities; produce `initial_state`, `apply_event`,
`replay`, closed-record validators, and proposal/admission event handlers. State
contains contract, obligations, routes, nodes, proposals, reviews, accounts, checkpoints,
acceptances, runs, control, and revision. IDs are stable controller-assigned sequences,
not attack slugs. Fixtures expose `contract()`, `proposal(category)`, and `review(subject,
reviewer, decision)` with explicit meaningful fields rather than arbitrary truth flags.

- [ ] Write RED tests for rejected unreviewed admission, reviewer-as-author, missing
  necessity assessment, conflicting scopes, standalone proposals with fewer than
  two approving independent reviews, and incompatible input digests.

```python
def test_standalone_needs_two_independent_approvals(self):
    state, proposal_id = self.proposed("standalone")
    state = self.add_review(state, proposal_id, "reviewer-one", "approve")
    with self.assertRaises(SearchError) as caught:
        self.admit(state, proposal_id)
    self.assertEqual(caught.exception.code, "admission_required")
    state = self.add_review(state, proposal_id, "reviewer-two", "approve")
    admitted = self.admit(state, proposal_id)
    self.assertEqual(len(admitted["nodes"]), 1)
```

- [ ] Run the focused admission test and observe RED before implementing handlers.
- [ ] Validate all required fields and enums from spec sections 4, 5, and 9. Reject
  unknown fields, bool-as-integer allowances, empty claims, dangling references,
  malformed scope intervals, and root-claim/proof-policy mutation. Direct root attacks
  need no artificial self-bridge. Necessary decisions carry omission, domain,
  outcome-to-action, and stopping fields; mere prose references grant no theorem status.
- [ ] Review bindings use canonical subject digests and host/operator provenance;
  do not claim cryptographic authentication of reviewer independence. Review status
  must be approve with no unresolved blocking objections. A child/new slug alone
  does not count as admission.
- [ ] Implement account identity and cumulative limits. Continuations inherit
  remaining limits; new obligations require reviewed decomposition. An exhausted
  attempt cannot reset through renaming, copied scope, cash-out, or a hypothesis
  checkpoint. Replan rounds without qualifying progress cap at three. Track unknown
  imported usage explicitly; never turn it into zero.
- [ ] Run admission/storage tests, self-review, and commit. Document the actual JSON
  shapes and event names so downstream tasks can use them exactly.

## Task 3: Checkpoints, proof joins, retreat, and scheduling

Files: create `search_controller/proof.py`, `scheduler.py`; extend `model.py`;
create `harness/tests/test_search_proof.py`, `test_search_scheduler.py`;
extend schema fixtures/documentation with exact action tags.

Interfaces: consume normalized state from Task 2; produce `next_action(state)` and
handlers for checkpoint, acceptance, retreat, replan, control, and complete events.
Acceptance events contain service-validated evidence facts; public callers cannot
inject such events through the eventual CLI. Pure logic does not perform filesystem I/O.

- [ ] Write RED cases for complete B but open C/D, invalid conditional imports, finite
  coverage gaps/overlaps, all local nodes finished but root open, and a verified
  alternative not requiring abandoned routes.

```python
def test_b_success_selects_ready_c_without_running_parent(self):
    state = self.route_fixture(premises=["b", "c", "d"])
    state = self.accept_obligation(state, "b")
    action = next_action(state)
    self.assertEqual(action["node_id"], "node-c")
    self.assertNotEqual(state["proof_status"], "proved")
```

- [ ] Run RED tests, then implement AND premises plus explicit bridge and OR routes.
  Reject cycles and self-supporting acceptance. Preserve hypothesis contexts; a
  conditional theorem closes its implication, not its consequent. Cover named,
  finite integer, and explicit-case scopes; quantified tails require separate obligations.
- [ ] Implement immutable checkpoint origins (`journal_move`, `external_result`),
  typed milestone/retreat predicates, success-driven descent and ancestor backtracking.
  Preserve independent accepted facts; no-hit or hypothesis-only records confer
  neither proof credit nor fresh budgets.
- [ ] Implement the exact next-action priority order in spec section 8.2, including
  pending reconciliation, evidence acceptance, root final review, local cash-out,
  active continuation, ready prerequisites, ancestor alternatives, root traversal,
  and bounded replan. A root-ready proof does not automatically become `proved`.
- [ ] Handle pause/resume/focus, one-summary continuation cap, deduplication where
  event IDs exist, and conservative missing-metadata behavior. Completed root delivery
  permits an accurately reported native parent finish pending only on unused children.
- [ ] Run storage/admission/proof/scheduler tests, self-review, and commit.

## Task 4: CLI controller, adoption, evidence acceptance, and generated views

Files: create `search_controller/service.py`, `cli.py`, `render.py`, `evidence.py`,
`adoption.py`; extend storage and pure service-operation handling as needed;
modify `harness/attack.py` parser dispatch and `bin/exactory-math` package loading;
create `harness/tests/test_search_cli.py`, `test_search_adoption.py`.

Interfaces: implement `Controller.command`, `Controller.status`, and
`install_parser(commands, strategies_default)` in `cli.py`. The CLI keeps global
`--attack-root` and `--strategies` placement. It returns machine-readable objects
with structured error codes; human output and `--json` share the same decision.

- [ ] Write CLI subprocess RED tests: init/propose/review/admit, stale revision,
  unknown command/field, traversal rejection, generated tree lineage, legacy adoption
  preserving bytes, and imports not automatically proving anything.

```python
def test_finished_legacy_attack_does_not_complete_adopted_objective(self):
    self.create_finished_legacy_attack()
    result = self.search("adopt", self.adoption_spec())
    self.assertEqual(result["proof_status"], "open")
    self.assertEqual(self.legacy_finish_bytes(), self.original_finish_bytes)
```

- [ ] Implement the commands listed in spec section 9.1 and tie every mutation to
  locked expected-revision/request-ID storage. Run/begin/reconcile command registration
  delegates to the execution/integration boundary delivered next; until that task,
  these paths report explicit unavailable capability rather than succeeding.
- [ ] Use sequential internal milestones for CLI/projections, evidence acceptance/
  completion, and adoption/integration. Keep one task report and review the entire
  Task 4 base-to-head range. A narrow under-lock payload-construction extension may
  build a closed service-operation event from isolated input state. Preserve the
  existing append contract and four-field envelope. Bind canonical public command
  identity separately from derived observations, and replay before any builder or
  mutable-source read. Stale/conflicting requests cannot run the builder. Bounded
  typed internal operation batches must reject nesting and unknown operations and
  commit no prefix on validation failure. Persist staged immutable content under
  the existing lock without recursive lock acquisition. Legacy mutable writes use
  recoverable intents outside that event transaction; no subprocess runs under it.
- [ ] Resolve and validate source/subject manifests, reviews, exact scope bindings,
  immutable journal prefixes, and external-result imports. Audits hash artifact
  dependencies, never execute mathematical jobs. Bounded status reports unchecked
  freshness honestly. All acceptance and complete operations require full audit.
- [ ] Protect live source versus immutable evidence distinctions. Review manifests
  must not hash themselves through their own reviews. Final package validation checks
  exact artifact versions, not only `unit.json` stamps.
- [ ] Implement explicit adoption snapshots and stable import/verification account
  identities. Preserve native depth-one parents, journal and FINISHED bytes, manual
  index, unknown prior usage, and deeper logical lineage. Create a reviewed verification
  prerequisite for imported evidence needing its first trusted run, without reopening
  the historical attack or granting repeated fresh import budgets.
- [ ] Render `SEARCH_TREE.md` and node `LINEAGE.md` from state, with exact residual
  obligations and status distinctions. Repeated identical commands must not duplicate
  directories, snapshots, events, or execution allowances.
- [ ] Run CLI/adoption and prior controller tests, self-review, and commit.

## Task 5: Metered execution and existing harness enforcement

Files: create `search_controller/execution.py`, `integration.py`; extend `evidence.py`;
modify `service.py`, `attack.py`, existing harness test setup/fixtures where needed;
create `harness/tests/test_search_execution.py`, `test_search_legacy_guards.py`.

Interfaces: implement begin/run/reconcile service operations and
`Controller.guard_legacy(command, slug, details)`. Provide
`before_legacy(args) -> context` and `after_legacy(context, outcome) -> None` to the
harness. These are internal validated transactions, not caller-set bypass flags.
Existing verify commands route through the same executor and ledger as `search run`.

- [ ] Write RED for unadmitted `verify` not launching a process, attempts without
  journalling, exhausted inherited accounts, false certificate status, wrong Lean
  declaration, and axiom inspection exiting nonzero after printing plausible text.

```python
def test_unadmitted_verify_never_launches_checker(self):
    self.write_checker_that_creates_marker()
    status, out, err = self.run_cli("verify", "certificate", self.slug, "check-1")
    self.assertNotEqual(status, 0)
    self.assertIn("admission_required", err)
    self.assertFalse(self.marker.exists())
```

- [ ] Freeze input/checker bytes before launch and execute an isolated snapshot with
  separate build/output locations. Bind toolchain/dependency hashes, command, exit
  states, declaration/type/axioms, and immutable artifacts. A changed or incomplete
  dependency boundary prevents proof acceptance. Certificate success additionally
  needs reviewed checker/completeness and exact claim correspondence.
- [ ] Implement reserved/launched/terminal/indeterminate lifecycle and launcher
  handshake. Use safe process identity, single top-level workload ownership, explicit
  timeouts, appropriate thread settings, and bounded output storage. Charge retries,
  cancellations, and uncertain crashes. Recovery never auto-runs a job or kills an
  uncertain reused PID. A live or indeterminate workload blocks replacement.
- [ ] Begin reserves a legal skill entry/next move only after its studies, problem,
  strategy, and budget checks. Journal append uses a recorded intent and idempotent
  acknowledgement. Preserve the exact legacy journal shape. Reconcile interrupted
  append/result operations without duplicate effects.
- [ ] Enforce guarded mutation across init-child/plan/rank/journal/verify/fail/stall/
  check-unit/finish while permitting provisional problem formulation and read-only
  legacy inspection. Preserve all current cash-out conditions and strengthen every
  finish path's native-child and terminal checks. Never add a legacy execution flag.
- [ ] Adjust existing workflow fixtures to create truly admitted states; pure legacy
  parser tests can stay pure. Do not weaken assertions or suppress gate findings.
  Use test-only helpers, not production testing modes.
- [ ] Run real tiny-process lifecycle tests, fake-Lean tests, current harness/client
  tests, and the tiny no-Mathlib Lean smoke when available. Self-review and commit.

## Task 6: Shared hooks, host normalization, and continuation ownership

Files: create `hooks/math_search.py`; modify the five math hooks, `hooks/hooks.json`,
`codex/hook.py`, `codex/generate.py`, generated hook registration;
modify `tests/test_math_hooks.py`, `test_stop_cap.py`, `test_codex.py`;
create `tests/test_math_search_hooks.py`.

Interfaces: `math_search.py` supplies normalized operations, registered-root/focus
discovery, protected-path classification, and one bounded controller invocation.
It consumes only the JSON Controller status/next/hook-stop contracts from earlier tasks.

- [ ] Write RED hook subprocess tests for custom root, ambiguous focus, root-open
  with all local FINISHED files, unsupported managed payload, malformed controller
  state, exec_command/cmd/workdir normalization, and direct writes to owned snapshots.

```python
def test_branch_switch_does_not_rearm_stop_allowance(self):
    self.exhaust_continuation_on("node-a")
    self.focus_node("node-b")
    decision = self.stop_event(stop_hook_active=False)
    self.assertNotEqual(decision.get("decision"), "block")
    self.assertEqual(self.objective_control()["status"], "paused")
```

- [ ] Implement pointer-only workspace discovery and explicit session focus. Outside
  registered roots preserve unrelated workflows. Within recognized managed mutations,
  corrupt/unsupported state fails closed with recovery guidance.
- [ ] Route Resume to one read-only status/next call and Stop to one internal hook-stop
  transaction. Record cap and one-summary state durably. Missing IDs count conservatively;
  false/missing stop_hook_active never asserts user authorization. Explicit operator
  resume is required when host provenance cannot be authenticated.
- [ ] Protect controller files, blobs/artifacts, and generated views through supported
  patch and shell events. Do not pretend to parse arbitrary shell or JavaScript.
  Clearly document any unobservable wrapper boundary instead of claiming enforcement.
- [ ] Run hook/client suites and Codex subprocess coverage, regenerate hook entries,
  self-review, and commit. Use observed payload fixtures or explicitly label synthetic
  ones; no claim of an unrun live host smoke.

## Task 7: Skill alignment and independent behavioral verification

Files: modify `skills/math-solver/SKILL.md`, `SEARCH.md`, `STUDY.md`, `CASHOUT.md`,
`harness/SPEC.md`, `harness/README.md`, affected strategies/entries, Codex README;
regenerate Codex entrypoints. Behavioral artifacts stay in the research workspace.

Interfaces: documentation describes the real executable schema/CLI, not a parallel
manual implementation. Existing strategy semantics and local stages stay intact.

- [ ] Before changing SKILL guidance, collect fresh-context no-guidance controls and
  current-skill pressure responses covering low-value finite prefixes, necessary
  sampling, B-only cash-out, literature versus formalization, and budget-renamed
  successors. Keep exact responses, observed compliance, and failure rationales.
- [ ] Read the observed results. Address demonstrated behavioral gaps and required
  new command routing. Do not invent baseline failures. Use conditional instructions
  keyed to actual admission/status results and structured required fields.
- [ ] Update stage 0 to resume the objective, stage 1/2 to establish the contract,
  study/admission to preserve the original request, moves to use begin/run/journal,
  verification to bind evidence, and cash-out/finish to distinguish root closure.
  Every affected entry that creates a second record or finite task must respect the gate.
- [ ] Provide one runnable tiny end-to-end example with real JSON specs, review trust
  disclosures, proof/decision distinctions, and exact outputs. Test the example by
  executing its commands against a temporary research workspace, not grepping the text.
- [ ] Run fresh-context forward pressure tests with the updated skill and runtime.
  For behavioral wording comparisons use five samples per compared condition;
  verify all flagged cases manually and include separate high-pressure scenarios.
- [ ] Validate skill frontmatter using the supplied validator, regenerate Codex files,
  run all documentation examples and relevant suites, self-review, and commit.

## Task 8: Adversarial integration and local 0.34.0 release validation

Files: modify both plugin manifests, manifest tests, release notes and release-facing
README as needed; add focused integration tests for discovered cross-task defects.

Interfaces: no new workflow API. Consume all implemented surfaces and verify the
approved spec scenario matrix. The final reviewer evaluates the whole branch diff.

- [ ] Exercise a complete tiny objective with AND/OR routes, partial accepted coverage,
  failed alternative, successful continuation, native-child pending local finish,
  imported evidence, paused/restarted control, and final audited closure. Literal
  expected status must distinguish partial, ready, complete, and invalidated.
- [ ] Attempt admission, budget, stale-evidence, arbitrary-event, legacy verify, and
  hook normalization bypasses. Add a failing test for each actual defect before fixing.
- [ ] Set both manifest versions to `0.34.0`, update pinned manifest expectations and
  release notes, and regenerate Codex artifacts. Do not alter marketplace settings
  unrelated to versioned source delivery.
- [ ] Run `python3 -m unittest discover -s tests -v` and
  `python3 -m unittest discover -s skills/math-solver/harness/tests -t skills/math-solver/harness -v`.
  Run syntax/JSON validation, `python3 codex/generate.py --check`, and the existing
  Codex coverage gate. Preserve exact command outputs in the report.
- [ ] Conduct available supported live host smoke only within user authorization and
  resource limits. Report any host verification that could not run; never label
  fixture-only coverage as end-to-end installed enforcement.
- [ ] Self-review and commit local release-ready changes. Independent whole-branch
  review follows. Do not install, merge, push, tag, or publish as part of this task.

## Plan self-review and acceptance mapping

| Specification | Implementing tasks |
| --- | --- |
| Sections 1-4: immutable objective and logical model | 1, 2, 3, 7 |
| Section 5: substantive admission and finite necessity | 2, 4, 5, 7 |
| Section 6: scope, policy, freshness, exact evidence | 3, 4, 5 |
| Section 7: checkpoint/successor/retreat | 2, 3, 4 |
| Section 8: accounts, run lifecycle, scheduling, control | 2, 3, 5, 6 |
| Section 9: persistence, records, commands | 1, 2, 4, 5 |
| Section 10: hooks and trust boundary | 6, 7, 8 |
| Sections 11-12: cash-out, import, adoption, compatibility | 3, 4, 5, 7 |
| Section 13: regression, behavioral, and release gates | Every task; final matrix in 8 |
| Section 14: prior research attribution | Approved spec retained; concise routing in 7 |

The task reviews check both spec compliance and code quality. Record findings and
fixes in the plan-scoped SDD ledger. A local implementation is not an installed or
published release; state that distinction in the final report.
