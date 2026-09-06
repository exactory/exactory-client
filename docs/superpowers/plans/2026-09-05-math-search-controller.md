# Mathematical Search Controller Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkboxes for tracking.

Goal: Implement the approved persistent objective controller and release-ready local Exactory 0.34.0 source, without modifying a live mathematical investigation.

Architecture: Keep the existing attack harness and its record formats. A separate Python package owns the immutable objective, event storage, admission, proof dependencies, budgets, and scheduling. CLI and hooks share its decisions; semantic reviews remain explicit trusted inputs rather than simulated theorem checking.

Tech Stack: Python 3.9+, standard library, unittest, existing Claude/Codex hook adapters.

Spec: `docs/superpowers/specs/2026-09-05-math-search-controller.md`

Approved on 2026-09-06: also implement
`docs/superpowers/specs/2026-09-06-math-search-retrospective-addendum.md` and
`docs/superpowers/specs/2026-09-06-math-search-progress-addendum.md`.
Preserve completed Tasks 1-6. Execute new Tasks 9-12 next, then pending Tasks 7-8,
then independent whole-branch review and the authorized merge flow.

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
- After successful verification and independent review, integrate through local-dev,
  dev, and main, with ordinary pushes as authorized on 2026-09-06. Never force push.
- Do not tag, publish a paper, install the plugin, or change the user's hook trust.
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
| `execution_state.py` | Pure move/run reservations, command-unit accounting, and lifecycle transitions |
| `integration.py` | Existing harness admission guards, journal intents, stage/finish and verifier integration |
| `discovery.py` | Pointer-only workspace registration, session routing intents and safe publication |
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

Files: create `search_controller/execution.py`, `execution_state.py`, `integration.py`;
extend `evidence.py` and pure handler registration in `model.py`;
modify `service.py`, `attack.py`, existing harness test setup/fixtures where needed;
create `harness/tests/test_search_execution.py`, `test_search_legacy_guards.py`.

Interfaces: implement begin/run/reconcile service operations and
`Controller.guard_legacy(command, slug, details)`. Provide
`before_legacy(args) -> context` and `after_legacy(context, outcome) -> None` to the
harness. These are internal validated transactions, not caller-set bypass flags.
Existing verify commands route through the same executor and ledger as `search run`.
Pure lifecycle handlers live in `execution_state.py`, not the process I/O module.
Task 5 records its exact closed request/event variants in `SEARCH.md`; the reviewed
terminal verification and frozen-input record formats remain authoritative.

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
  For installed Lean runtime/library inputs, capture a deterministic content-addressed
  inventory of the resolved existing toolchain, referenced by the native run spec,
  input-review subject and controlled run. Check exact file membership and hashes
  before launch, after execution and during full acceptance/completion audit. This
  is run provenance, not a second evidence manifest or a replacement terminal format.
  Missing, changed or unsupported toolchain resolution fails closed; no automatic
  installation or download is permitted. Disclose the trusted read-only external
  dependency boundary, without claiming OS-hermetic execution. Use tiny synthetic
  inventories for negative tests and retain the genuine no-Mathlib Lean smoke.
  Capture the original declaration's complete printed type separately from the
  requested source type. Generate an immutable, named correspondence theorem of
  the requested type and inspect both its axioms and the original declaration's
  axioms in the same successful inspection command. Type-directed elaboration can
  introduce additional coercion dependencies; inspecting only the original theorem
  misses them. Preserve the requested type digest in the terminal result, and keep
  generated source, complete type output and both axiom reports as run provenance.
  Test a real custom-axiom coercion negative; printed type text equality alone is
  not a general proof of type equivalence.
- [ ] Support closed `command`, `certificate`, and `lean` run variants. A generic
  command records execution/evidence, never a certificate or kernel result. Bind the
  admitted task purpose/domain and the actual argv, frozen inputs, external dependencies,
  dependency enumeration, nonsecret environment, bounded output expectation, timeout,
  and thread settings. Native verifier argv is derived from its captured project.
  Begin takes the minimal planned pass/trigger/step citations that native legality
  checks cannot derive, and derives the next move number. Later journal acknowledgement
  must match that reservation. No new per-retry significance review is required;
  a changed admitted purpose, domain or allowance requires reviewed amendment.
  A generic command requires an admitted finite task and its reviewed necessity/
  outcome map; a plain analytical `proof` task is insufficient. Native certificate/
  Lean verification may use a proof task only for produced artifacts of its exact
  admitted claim through a reserved legal entry and derived verifier protocol.
  A native tag permits neither arbitrary argv nor a changed purpose/domain. Test
  analytical-to-generic and native wrong-claim/domain refusal, and both legitimate
  finite-command and native-verification paths. Disclose the semantic code/input
  review trust boundary instead of claiming that labels establish mathematical use.
  Before native reservation/launch, require an independent input-bound `ResultReview`
  over the closed subject `{node_id, task, spec_without_input_review}` and the exact
  claim. The subject binds the full native run spec and derived protocol, using
  stable logical paths rather than a new physical run directory. Unchanged retries
  reuse that review but consume new allowance; changed inputs, dependencies or
  environment require a new input-bound review, not another significance review.
  Legacy verify reads `verification-review.json`; search run accepts `input_review`.
  Preserve the review immutably outside its own subject/snapshot to avoid circular
  hashing. Do not exclude an actual executable dependency under that metadata rule.
  Test absent, mismatched, self-authored and stale-input reviews before launch.
- [ ] One Lean verification workload reserves two command units atomically under one
  run ID, one for build and one for inspection. Certificate and generic command
  workloads reserve one. The Lean commands share the admitted total wall timeout
  and top-level workload ownership. Record reserved, started and charged units
  explicitly. An inspection proven never started after a terminal failed build can
  release its reservation; uncertain launch/crash/cancellation remains charged and
  blocks replacement. Rejected preflight consumes nothing. Test insufficient allowance
  before either Lean command starts, failed-build no-start evidence, and uncertain
  recovery. Verification acceptance still requires both successful command records.
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
  Child creation uses reviewed `search admit` with `native_parent`, preserving
  `parent.json`, the depth-one rule, opening move count and native finish checks.
  Legacy `init --from` returns `admission_required` with this concrete migration
  route; it cannot both require prior admission and recreate its already initialized
  directory. Provisional `init` without `--from` remains supported. Document this
  intentional creation-entrypoint compatibility change for 0.34.0.
- [ ] For non-journal native mutations, record a native-operation intent with exact
  relevant pre-state and original args, then acknowledge exact post-state after
  native success. Reconcile only an established effect, not mere file presence.
  Ambiguity blocks new mutations with the original command/intent and file conflicts
  identified. Explicit replay of the original non-mathematical command may recover
  only under its pinned inputs and permitted effects; preserve intervening edits.
  Reuse native derivations/validators without duplicating their semantics, and never
  rerun a mathematical job as recovery. Test crashes before/after writes, duplicate
  acknowledgement and conflicting edits.
  A caught native validation failure may also have a durable, explicitly failed
  receipt bound to its exact post-state and permitted output changes, for example
  removal of a stale check-unit stamp. Acknowledge that known failure without
  asserting success or local completion. Missing receipts remain ambiguous; file
  presence or absence cannot substitute for a recorded invocation outcome.
- [ ] Adjust existing workflow fixtures to create truly admitted states; pure legacy
  parser tests can stay pure. Do not weaken assertions or suppress gate findings.
  Use test-only helpers, not production testing modes.
- [ ] Run real tiny-process lifecycle tests, fake-Lean tests, current harness/client
  tests, and the tiny no-Mathlib Lean smoke when available. Self-review and commit.

## Task 6: Shared hooks, host normalization, and continuation ownership

Files: create `hooks/math_search.py`; modify the five math hooks, `hooks/hooks.json`,
`codex/hook.py`, `codex/generate.py`, generated hook registration;
modify `tests/test_math_hooks.py`, `test_stop_cap.py`, `test_codex.py`;
create `tests/test_math_search_hooks.py`. Add `search_controller/discovery.py` and
focused controller discovery tests; extend `cli.py`, `scheduler.py`, `service.py`,
`model.py` and `SEARCH.md` only for the routing, focus and observation contracts below.

Interfaces: `math_search.py` supplies normalized operations, registered-root/focus
discovery, protected-path classification, and one bounded controller invocation.
It consumes the JSON Controller status/next/hook-stop contracts. Narrow controller
extensions provide pointer publication and authoritative session checks; hooks do
not import mutable service internals or implement a second scheduler.

- [ ] Write RED hook subprocess tests for custom root, ambiguous focus, root-open
  with all local FINISHED files, unsupported managed payload, malformed controller
  state, exec_command/cmd/workdir normalization, and direct writes to owned snapshots.

```python
def test_branch_switch_does_not_rearm_stop_allowance(self):
    self.exhaust_continuation_on("node-a")
    self.select_admitted_alternative("node-b")
    decision = self.stop_event(stop_hook_active=False)
    self.assertNotEqual(decision.get("decision"), "block")
    self.assertEqual(self.objective_control()["status"], "paused")
```

- [ ] Implement pointer-only workspace discovery and explicit session focus. Outside
  registered roots preserve unrelated workflows. Within recognized managed mutations,
  corrupt/unsupported state fails closed with recovery guidance.
  Use `<workspace>/.exactory/math-search.json`, a closed versioned record with
  `schema_version`, `roots`, and `sessions`. Root identities include canonical path,
  objective ID and contract digest, because objective IDs repeat across roots.
  Host-qualified session entries carry a generation and a target root/focus request;
  cleared targets retain tombstone generations. Store no claims, budgets, nodes,
  pause flags or cached scheduling decisions in this pointer file.
  Discover exact registrations and `.search/tree.json` markers through ancestors of
  event cwd, effective shell workdir and explicit targets, without recursive scans
  or alphabetical selection. A custom root outside a workspace can identify itself
  from a node cwd; its authoritative focus record still requires a matching session.
  Missing or conflicting session/root bindings request a handoff, never implicit
  selection. Unrelated unregistered work remains unaffected.
- [ ] Keep one authoritative automatic-continuation owner in `control.focus_record`,
  preserving the existing `control.focus` enum. Bind host-qualified session identity
  and focus request ID. Other sessions may inspect or perform ordinary validated
  collaborative CLI work, but do not inherit automatic continuation. Focus changes
  neither mathematical branch selection nor allowance. The branch-switch regression
  uses genuinely admitted scheduler operations, not an invented focus node selector.
  Explicit operator resume uses existing authorization checks and updates the owner;
  hooks never issue resume or fabricate missing host identity.
- [ ] Add optional `--workspace-root` routing to init/focus/resume. By default,
  registration writes only to the canonical controller root's direct parent.
  An explicit argument authorizes only that exact workspace directory. Init registers
  identity without creating a session owner; focus/resume publish after their
  authoritative transaction commits. Never select a write destination by scanning
  ancestors. Reject symlinked registration paths and revalidate containment.
- [ ] Record a closed typed `discovery_recorded` operation only for init/focus/resume,
  in the original immutable transaction. Pin canonical routing arguments, root
  identity, request ID, session when applicable, exact expected previous pointer
  including absence/tombstone generation, and desired entry. Include canonical
  routing inputs in command identity. Keep routing intents separate from pending
  filesystem initialization effects, so failed publication cannot block fresh focus.
  New requests read the expected pointer under its lock, release that lock, then
  commit. Identical replays use the original pinned intent, not current pointer
  contents; changed routing under an existing ID returns `request_id_conflict`.
  Publish with controller-then-registry lock order, never the reverse and never two
  controller locks together. Session publication checks current owner first, then
  compare-and-set against the pinned prior entry. An already-equal desired entry is
  idempotent only after the owner check. Preserve unrelated roots and sessions.
  Test A committing without publication, B publishing another focus, and A replaying:
  A cannot overwrite B, whether B names the same or a different objective. Include
  changed workspace arguments, clear/tombstone ABA, concurrent registration and
  publication interruption. Report committed-but-unpublished operations accurately;
  stale intents require fresh explicit focus, not an unconditional replay promise.
- [ ] Route Resume to one read-only status/next call and Stop to one internal hook-stop
  transaction. Record cap and one-summary state durably. Missing IDs count conservatively;
  false/missing stop_hook_active never asserts user authorization. Explicit operator
  resume is required when host provenance cannot be authenticated.
  Resume uses a narrow read-only session validation input with `search next --json`;
  it neither refreshes durable facts nor repairs registration. Stop uses a fresh
  transaction request ID and separately normalized host delivery ID, so duplicate
  accounting still recalculates the next action. Omit expected revision only in the
  internal hook-stop CLI path, which reads it and invokes the validated service in
  the same subprocess. Concurrent revision conflicts return explicitly without
  unlimited retries; ordinary public mutations retain required revisions.
  Inside that transaction, validate focus and derive changed `node_facts_recorded`
  through `integration.observe_node` before `control_recorded`, replaying into the
  prospective state before deciding. Native/journal intents retain reconciliation
  precedence. Enforce the existing 256-operation bound with explicit recovery
  guidance, not silently omitted nodes. Read-only status/next remain read-only;
  PostToolUse activity remains advisory and cannot grant proof credit.
- [ ] Protect controller files, blobs/artifacts, and generated views through supported
  patch and shell events. Do not pretend to parse arbitrary shell or JavaScript.
  Clearly document any unobservable wrapper boundary instead of claiming enforcement.
  Include discovery pointers and custom-root native records. Normalize supported
  direct exec_command/cmd/workdir forms and the documented PATH bootstrap before
  direct Exactory commands. Preserve patch add/update/move/delete protection and
  combined unit/draft sequencing. Unsupported payloads visibly targeting managed
  paths fail closed with specific direct-call guidance. A nested operation the host
  never exposes remains an explicitly documented observability boundary.
- [ ] Run hook/client suites and Codex subprocess coverage, regenerate hook entries,
  self-review, and commit. Use observed payload fixtures or explicitly label synthetic
  ones; no claim of an unrun live host smoke.

## Task 7: Skill alignment and independent behavioral verification

Files: modify `skills/math-solver/SKILL.md`, `SEARCH.md`, `STUDY.md`, `CASHOUT.md`,
`harness/SPEC.md`, `harness/README.md`, affected strategies/entries, Codex README;
regenerate Codex entrypoints. Behavioral artifacts stay in the research workspace.

Interfaces: documentation describes the real executable schema/CLI, not a parallel
manual implementation. Existing strategy semantics and local stages stay intact.
This task follows approved Tasks 9-12. The saved control/current conditions already
exist in the plan-scoped behavioral handoff; do not recollect those baselines.

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
- [ ] Route finite work through the implemented typed completeness, information-value,
  result-interpretation and verification-cost gates. Compare saved evidence first;
  preserve necessary diagnostics and significant standalone results. A tautological
  prefix/tail split is not a finite reduction. Tests consume saved certificates
  without silently rerunning producers. Do not claim enforcement over unreported
  tool calls, internal solver counts, or in-context reasoning.
- [ ] Document explicit cancellation separately from pause, typed local reviews and
  literature outcomes separately from root proof, and evidence-only cash-out. Route
  the three-extraction/returned-children state through the actual lawful aggregation
  transition. Link the generated parent progress view without subjective percentages.
- [ ] Provide one runnable tiny end-to-end example with real JSON specs, review trust
  disclosures, proof/decision distinctions, and exact outputs. Test the example by
  executing its commands against a temporary research workspace, not grepping the text.
- [ ] Run fresh-context forward pressure tests with the updated skill and runtime.
  For behavioral wording comparisons use five samples per compared condition;
  verify all flagged cases manually and include separate high-pressure scenarios.
  Include all prewritten forward cases F-R, retaining the genuine finite-residue
  and standalone-result positive controls. Do not show participants the rubric.
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
- [ ] Include retrospective regressions for required interpretation, typed finite
  residue, ignored cooperative interruption, cancellation acknowledgement, stale
  local review, UNKNOWN/log-audit nonproof, append-only progress, and lawful returned
  child aggregation. Resolve the deferred actionable native-recovery diagnostic and
  expected argparse stderr finding by code/assertions, never suppressing a gate.
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
  review follows. The controller performs the already authorized merge/push flow
  after that review. Do not install, merge, push, tag, or publish from the task agent.

## Task 9: Typed computation admission and mandatory result interpretation

Files: create `search_controller/computation.py`, `computation_io.py`, and
`harness/tests/test_search_computation.py`. Extend `schema.py`, `admission.py`,
`model.py`, `scheduler.py`, `service.py`, `cli.py`, `execution_state.py`,
`execution.py`, `evidence.py`, `integration.py`, affected focused test fixtures,
and `SEARCH.md`. Paths are relative to `skills/math-solver/harness/` except SEARCH.md.

Interfaces: `computation.py` owns pure closed records and replay handlers;
`computation_io.py` owns exact artifact/basis auditing. Use the existing event log,
proposal review, accounts, and executor. No separate task database or arbitrary
event command. `ComputationContract` is versioned and part of new finite proposals.

- [ ] Write RED tests before source changes for an admitted infinite-prefix producer
  with no finite reduction, a nominal prefix/tail AND split, predetermined means,
  stale bound evidence, and another launch before interpretation. Assert rejection
  precedes producer side effects and preserves the original account.

```python
def test_unclassified_result_cannot_launch_again(self):
    node, run = self.run_admitted_finite_question()
    self.assertEqual(self.controller.command("next", {}, None, None)["kind"],
                     "interpret_run")
    before = self.controller.status()["accounts"]
    with self.assertRaises(SearchError):
        self.launch_again(node)
    self.assertEqual(self.controller.status()["accounts"], before)
```

  Implement these public-operation fixture helpers in the named test file. A real
  tiny producer writes a marker; a refusal must leave it absent. Do not inject a
  caller-supplied accepted theorem into the public service.
- [ ] Add proposal schema version 2 with one additional `computation` field. It is
  null for analytical proof tasks and required for the three finite task kinds.
  Keep version 1 readable during replay. New finite proposals/admissions require
  version 2; historical accepted facts and usage remain unchanged. The existing
  independent proposal review binds the entire new contract, avoiding duplicate
  reviews for an unchanged new proposal.
- [ ] Define the closed computation record with `schema_version`, `domain`, `basis`,
  `preflight`, and `verification_plan`. A domain is an existing case-ID or integer
  interval scope, or a `bounded_encoding` with immutable `encoding_digest` and
  positive `max_instances`; a bare named/unbounded domain is not finite.
  Basis identifies exactly one of `root_finite_scope`, `finite_residue`, `diagnostic`,
  `standalone`, or `counterexample`, with exact deduction evidence and any accepted
  completeness/bound dependencies. Document its complete tagged schema in SEARCH.md.
  Root finite-scope work must cover an actual subdomain of the finite root; a named
  unbounded root requires accepted finite-residue reduction, reviewed diagnostic
  necessity, or independently admitted standalone significance. A proposed
  prefix/tail route does not suffice. Existing claim/assumption/policy/freshness
  checks apply to all imported basis acceptances. Review must cover completeness
  of the encoding for the exact claim, not only the parameter bounds.
  Common basis fields are `kind`, `deduction_digest`, `dependencies`,
  `completeness_acceptance_id`, `bound_acceptance_id`; finite_residue additionally
  has `reduction_acceptance_id`. Each dependency pins `acceptance_id`,
  `acceptance_digest`, `claim_digest`. Referenced acceptance IDs occur in that list.
  Proof-producing bounded encodings require both completeness and bound receipts;
  diagnostic/standalone/counterexample purposes may leave those IDs null under their
  actual reviewed basis. The residue receipt is an accepted proof of the exact root
  route bridge, not a nonclosing reduction result. Other unresolved premises must
  have explicit finite scope under the reviewed reduction; named/unbounded missing
  premises are refused. Do not require all other finite residue cases to be proved
  before admitting this case. Independent review still checks scope correctness.
- [ ] Preflight has exact fields `uncertainty`, `inspected_evidence`,
  `already_determined`, `cheapest_sufficient_check`, and `failure_signal`.
  Evidence is a list of immutable digests and already_determined is boolean.
  Refuse a producer when its declared mathematical outcome is already determined;
  retain legitimate independent verification under its separate input-review
  contract. Reuse the proposal's existing necessity outcome/action map and stopping
  condition rather than duplicating them. Admission is a recorded semantic review,
  not a claim that string comparison decides arbitrary mathematical relevance.
- [ ] Verification plan has exact fields `certificate_shape`, `checker_method`,
  `producer_seconds`, `checker_seconds`, `checker_cap_seconds`, `fallback`,
  `max_input_bytes`. Durations and byte limit are positive integers; expected checker
  time cannot exceed its cap without redesign. All execution limits remain bounded
  by the admitted node allowance. Default max_input_bytes is 67108864 (64 MiB)
  in examples/fixtures, with an explicitly reviewed larger value allowed.
- [ ] Add `search amend-computation NODE --spec FILE` for an already admitted version-1
  finite node. Spec binds the original proposal digest, computation contract,
  independent ResultReview and explicit artifact inputs. It can add missing
  guarantees, not change the original task, claim, method, limits or accounts.
  Reject a material scope change and point to a reviewed successor. No new budget,
  automatic resume, reclassified historical output or overwritten review follows.
  New execution on an unamended historical finite node must fail closed with this
  recovery route; old journals, root contracts and proposal/review digests remain.
  Amendment spec is `{proposal_digest, computation, review, inputs}` and its review
  subject is `{node_id, proposal_digest, computation}`. Bind every contract artifact
  and basis dependency through the existing immutable audit path.
- [ ] Pin the effective computation digest in each new finite run and validate it
  both at the service boundary and pure reservation replay. Native verification
  under a finite node cannot bypass the contract; analytical verification of an
  already produced exact proof keeps the existing independent input-review route.
- [ ] Add `search interpret RUN --spec FILE`. Its immutable record pins run ID,
  actual result digest, effective computation digest, a declared outcome or explicit
  inconclusive reason, classification, root decision, remaining obligation IDs and
  next action. Use classifications `observation`, `audit_only`, `undecided`,
  `proof_candidate`, and `counterexample_candidate`. Interpretation supplies no
  proof acceptance or permission itself. Timeout/cancel/UNKNOWN interpretations
  cannot claim a decided theorem. Do not parse arbitrary stdout to guess a verdict.
  Exact interpretation spec fields are `result_digest`, `computation_digest`,
  `outcome`, `inconclusive_reason`, `classification`, `root_decision`,
  `remaining_obligation_ids`, `next_action`; the positional run ID is also pinned
  in the immutable subject. Exactly one of outcome/inconclusive_reason is non-null.
  root_decision is `{kind, reason}`, with kind `undecided`, `proof_candidate` or
  `counterexample_candidate` and nonempty reason explaining the actual route effect.
  A candidate root decision requires the matching candidate classification and
  a non-standalone root connection. A candidate classification may instead have
  root_decision undecided with a reason, for example an independently significant
  standalone theorem or a partial result that does not decide the root. Diagnostic
  and audit-only work cannot claim a theorem candidate. Failed/inconclusive runs
  remain undecided.
  Historical run reservations without computation_digest remain replayable only
  under their old finite admission shape; fresh public reservations include it
  and cannot use that compatibility path to bypass the gate.
- [ ] After reconciling an actual finite run, expose `interpret_run` before another
  mathematical launch across the objective. Preserve pause and live-work ownership
  precedence and allow required journal acknowledgement/reconciliation. Require
  interpretation even for a retry; unchanged authorized retries retain original
  usage and scope without a duplicate proposal review. Read-only status never
  creates an interpretation. A renamed node cannot hide a pending result.
- [ ] Add positive tests for a genuine accepted finite reduction, finite-root cases,
  independently significant standalone theorem and necessary diagnostic. Add replay,
  changed-input, historical-amendment, timeout/log-audit and scope-change tests.
  Run the focused computation/execution/admission suites, update SEARCH.md with
  actual closed fields and executable CLI shapes, self-review and commit.

## Task 10: Bounded snapshots, measured execution and owned cancellation

Files: extend `search_controller/evidence.py`, `execution.py`, `execution_state.py`,
`service.py`, `model.py`, `cli.py`, `integration.py`, `SEARCH.md`; create
`harness/tests/test_search_execution_resources.py` and update affected fixtures.
Split focused resource/ownership helpers if necessary, without a second executor.

Interfaces: consume Task 9's verification_plan and effective computation digest.
Keep the existing frozen-input review and launcher-owned process object as the
authority. Add a public `search cancel RUN --spec FILE` with explicit reason and
operator provenance. It requests cancellation; only the owning launcher signals
its own child process group and acknowledges the terminal result.

- [ ] Write RED tests for a growing/oversized input before allocation, an owned
  process that ignores cooperative interruption, cancellation while streams are
  open or already closed, and cancellation/reconciliation with an uncertain owner.
  Use tiny fixture processes, short declared caps and no external solver jobs.

```python
def test_cancel_is_acknowledged_without_refunding_started_work(self):
    run = self.start_owned_ignoring_interrupts()
    self.cancel_with_operator_reason(run)
    self.await_owned_terminal(run)
    state = self.controller.status()
    self.assertEqual(state["runs"][run]["termination"], "cancelled")
    self.assertGreater(state["runs"][run]["charged_units"], 0)
```

- [ ] Bound total local snapshot bytes using the declared max_input_bytes before
  materializing them. Stat preflight alone is insufficient: bounded reads must
  reject growth during capture. Stream file hashes for read-only external tools
  and dependencies. Apply equivalent explicit caps to native proof verification,
  bound in its existing input review; update exact subject helpers and fixtures.
  Do not silently cap a successful snapshot or label this an OS memory sandbox.
- [ ] Record producer versus checker role and measured elapsed execution seconds
  in exact run provenance/terminal records. Derive role from the authorized run
  kind, not a label that lets a producer consume checker authority. Use monotonic
  elapsed measurement, preserve null/unknown for unmeasured historical data, and
  validate finite nonnegative numbers. Report CPU, RAM, internal solver calls and
  hidden retries as unknown rather than fabricated totals.
- [ ] Persist cancellation intent before notifying the existing launcher ownership
  channel. Bind token, run and request identity; only that launcher can acknowledge.
  A pre-start cancellation may avoid launch; a started one keeps consumed charges.
  Poll the request while monitoring the child, including after stdout/stderr close.
  A crash between intent and notification is recoverable without signalling a PID
  from a stale file. Never unlink the ownership lock or touch unrelated processes.
  Cancellation is allowed while user-paused and while a native verification intent
  is pending; it does not resume research or fabricate native acknowledgement.
- [ ] Distinguish plain pause (blocks future launches), pending cancellation,
  acknowledged cancellation, timeout, launch failure and indeterminate ownership
  in status and recovery. Terminal cancel is idempotent; repeated notification
  cannot spend another launch or change a completed result. Pending cancellation
  and failed notification remain visible with an exact next recovery action.
- [ ] Bound producer and checker execution using the reviewed plan, including the
  shared Lean build/inspection deadline. Test external expiry even when the child
  ignores a cooperative interrupt. Keep existing one-workload, two-command Lean,
  charge/retry, exact input and dependency rules.
- [ ] Run focused resource/execution/native-verification suites and actual tiny
  cancellation fixtures, update SEARCH.md and command examples, self-review, commit.

## Task 11: Exact local review receipts and meaningful cash-out

Files: create `search_controller/local_review.py` and
`harness/tests/test_search_local_review.py`; extend `service.py`, `model.py`,
`integration.py`, `evidence.py`, `proof.py`, `scheduler.py`, `cli.py`, `attack.py`,
`SEARCH.md` and affected tests. The controller's scoped interface decisions are
in the plan-scoped `task-11-controller-handoff.md`.

Interfaces: add `search review-local NODE --spec FILE` for typed receipts of exact
local packages and literature-resolution records. Reuse immutable manifests,
ResultReview/provenance primitives and the existing local/native transaction path.
No replacement journal, native parent schema, proof policy, or arbitrary verdict
inference from prose. The root's existing accepted-proof requirement remains.

- [ ] Write RED tests for unchanged unit.json with mutated proof/draft/dependency,
  a negative or partial review attached to a full-proof unit, unresolved literature
  marked solved, and a log-audit receipt substituted for feasibility verification.

```python
def test_mutating_proof_invalidates_local_full_review(self):
    node = self.review_complete_local_package()
    self.change_pinned_proof(node)
    with self.assertRaises(SearchError):
        self.finish_native(node)
    self.assertNotEqual(self.controller.status(full_audit=True)["proof_status"],
                        "proved")
```

- [ ] Receipt binds exact claim and assumptions, evidence manifest, dependencies,
  proof/draft identities where applicable, review scope, verdict, trusted base,
  reviewer provenance and unresolved obligations. Separate mathematics, paper
  evaluation and literature resolution. Accept recorded negative/partial receipts
  as history, but they cannot satisfy a complete-positive requirement. Identity
  hashes establish what was reviewed, not mathematical truth by themselves.
- [ ] Guard new native check-unit/finish and literature-only completion with these
  receipts. Preserve old local FINISHED and unit stamps as historical facts;
  never promote them silently. A literal FAIL in a prose file is not a machine
  verdict; the structured receipt must be explicit. A literature solution needs
  an exact source identity and solved outcome; unresolved novelty stays unresolved.
  Preserve the native drafting order: a pre-draft check-unit receipt can pin the
  unit/claim/proof, while final package review must pin the later draft, evaluation
  and dependency closure. A pre-draft receipt cannot satisfy final package review.
- [ ] Bind each new cash-out unit to either a contributing admitted obligation or
  independently admitted standalone theorem. A pipeline or unbounded prefix without
  such a basis remains evidence, not a publishable mathematical unit. Preserve
  genuine finite standalone work and local workflow completion distinct from root
  resolution. Necessary diagnostic evidence can be cited without becoming a theorem.
- [ ] Exercise the exact state with three negative-side extraction moves, two
  finished required children and an already reviewed reduction. Supply the actual
  legal administrative composition path when all deductions exist. If a new lemma
  remains, return its precise missing admission/allowance; do not create a fourth
  extraction, fake stall, generic order override or renamed budget. Preserve the
  existing exception for root-ready evidence with unused native children, while
  exposing local_finish_pending_unused_children honestly.
- [ ] Reject changed or withdrawn dependencies during full local/root audit and
  downstream delivery. Test a restricted-family receipt cannot close an unrestricted
  claim. Run focused local-review/finish/delivery/legacy suites, document exact
  receipt schemas and lawful composition in SEARCH.md, self-review and commit.

## Task 12: Parent progress report with immutable history

Files: create `search_controller/progress.py` and
`harness/tests/test_search_progress.py`; extend `storage.py`, `model.py`,
`service.py`, `render.py`, `cli.py`, `hooks/math_search.py`, relevant hook tests and
`SEARCH.md`. Consume the full approved progress addendum, not only this task summary.

Interfaces: optional storage-owned envelope `recorded_at_utc`; four existing fields
stay required. The timestamp is YYYY-MM-DDTHH:MM:SSZ metadata only. New events include
it; old events remain readable without fabricated time. One versioned projection
owns a fixed header, marked mutable current tree, and append-only historical suffix.

- [ ] Write RED tests using public mutations for event timestamps, pause/checkpoint/
  acceptance updates, history preservation, duplicate replay and read-only status.

```python
def test_replay_preserves_old_entries(self):
    self.create_checkpoint_and_report()
    before = self.read_history_bytes()
    self.accept_checkpoint_and_replay_same_request()
    after = self.read_history_bytes()
    self.assertTrue(after.startswith(before))
    self.assertEqual(self.count_last_event_entries(), 1)
```

- [ ] Publish PROGRESS.md in the controller root's direct parent by default. Add
  explicit `search configure-report --spec FILE` for a different basename in that
  same parent when an unowned file conflicts. Keep ownership in immutable controller
  state; initial creation is exclusive across roots. Reject symlinks and traversal.
  Do not overwrite an unowned report or take ownership by reading its apparent title.
- [ ] Append one three-to-five-line English entry per relevant committed transaction,
  derived from that exact event-prefix state: timestamp/event, main obligations,
  branch counts/status, cash-out and exact coverage with reason. Keep all historical
  bytes and the initial header immutable. Only the marked current-tree region can
  change. Render active/unresolved/shared/standalone/failed work distinctly and link
  the complete generated SEARCH_TREE. Flatten/escape labels and marker-like input.
- [ ] Compute only justified finite-root case coverage with its denominator and
  open common premises. Do not infer percentages from activity or arbitrary weights.
  General progress is not quantifiable until resolution; only audited root proof
  completion has a 100% proof label. Keep disproof separately labelled.
- [ ] Assign storage timestamps after idempotency/revision validation. Project under
  the objective writer lock without holding it during jobs. A committed-event/
  failed-projection interruption must report accurate state and be repaired on a
  later managed mutation, explicit render or reconciliation, without duplicate
  entries or new research. Read-only status reports stale view without writing.
  Corrupted history/ownership requires explicit repair; never silently rewrite it.
- [ ] Test concurrent cross-root creation, concurrent updates, interrupted atomic
  replacement, custom basenames, symlink/marker injection, old timestamp-free events,
  partial/all-case-with-open-bridge/root-complete views, and supported shell/patch
  protection including containing-directory operations. Run focused storage/progress/
  hook suites, update SEARCH.md, self-review and commit.

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
| Retrospective sections 1-3: finite completeness, information value, actual outcomes | 9, 7, 8 |
| Retrospective section 4: verification design, bounded capture and resource records | 9, 10, 7, 8 |
| Retrospective section 5: pause/cancel, exact review and lawful aggregation | 6, 10, 11, 7, 8 |
| Retrospective section 6 and complete progress addendum | 11, 12, 7, 8 |
| Approved merge route and preservation of upstream changes | Controller after final review and integrated-delta validation |

The task reviews check both spec compliance and code quality. Record findings and
fixes in the plan-scoped SDD ledger. A local implementation is not an installed or
published release; state that distinction in the final report.
