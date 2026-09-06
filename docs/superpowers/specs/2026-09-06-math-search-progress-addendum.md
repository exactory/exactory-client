# Progress report addition to the mathematical search controller

Date: 2026-09-05
Status: Approved on 2026-09-06 together with `2026-09-06-math-search-retrospective-addendum.md`.
This document states required behavior, not implementation completion.

This supplements `2026-09-05-math-search-controller.md`. It does not replace the immutable root
objective, proof-policy checks, budget rules, or original implementation plan.

## Purpose

Let the user inspect one local Markdown file without asking the solver for status.
Checkpoint and investigation-result updates automatically add a concise historical
entry. A separate marked region in the same file shows the latest investigation tree.
All generated text is English.

## Approaches

| Approach | Benefit | Cost |
| --- | --- | --- |
| One Markdown file with a mutable visual region and append-only history | One place to read, matching the request | The writer must preserve history bytes when replacing the visual region |
| Separate history and current-tree files | Physical append-only writes are straightforward | The user must open two files |

Use the first approach. Derive content from committed controller events and observed
local workflow status, not from an independently maintained narrative or an agent's
unverified completion claim.

## Location and ownership

For the conventional `<study>/attack/` root, use `<study>/PROGRESS.md`. The containing
study directory is the direct parent of the attack root. The report identifies the
objective ID and canonical root so two objectives cannot silently share ownership.

Do not overwrite an existing unowned file. Report the conflict and request a different
basename in that same parent directory. A custom root can use an explicitly selected
basename, also confined to its direct parent. Reject traversal and symlink escapes.
Initial publication is exclusive, so two roots racing to claim the same filename
cannot overwrite each other despite having different objective locks.
This narrowly scoped parent-directory output is an explicit exception to the usual
root-contained generated files, not permission to write arbitrary ancestors.

The initial fixed header and every historical entry are preserved byte for byte.
Only the marked latest-visual region may be replaced. Direct supported-tool writes
to the owned report are guarded like generated indexes. The controller remains the
source of proof and resource state; the report cannot create accepted facts.

## Update events

Append an entry when a checkpoint is created or accepted, an accepted result is
invalidated, a node is admitted or changes result/lifecycle status, a retreat changes
the frontier, contributing cash-out status changes, or the objective pauses, resumes,
or completes. A result ready for acceptance is reported as ready, not as accepted.

Do not append on read-only `status` or `next`, repeated hook delivery, an unchanged
observation, or a rejected mutation. A transaction affecting several listed fields
produces one entry with its event reference. Events remain ordered by sequence,
not by timestamp.

Recognized legacy mutations report through their existing controller integration
after successful acknowledgement. A manually edited local draft becomes visible
when the controller observes and records its workflow-state change. There is no
new unrestricted filesystem watcher or polling mathematical job.

## Historical entry

Each entry has three to five nonempty lines, normally five:

```text
2026-09-05T18:30:00Z | Event 42: checkpoint cp-004 accepted.
Main: B proved; C active; D and bridge-1 remain open.
Branches: 4 total; 1 active, 1 waiting, 1 finished, 1 retreated.
Cash-out: 1 unit checked; draft pending.
Coverage: 2/11 cases (18.2%); overall proof progress is not quantifiable.
```

The displayed counts come from the recorded node set. Pending proposals are labelled
separately from admitted nodes. The retained root is not silently counted as a side
branch. Standalone results are distinguishable from root-contributing investigations.
Imported historical nodes and unused alternatives remain distinguishable from the
selected work. An investigation's local `active` status is not evidence of a live
compute process; report the controller's actual run state when a run is present.

Entry identifiers bind their source objective and event sequence. The reason for
the displayed coverage or estimate is included on the final line or its continuation,
within the five-line limit. Links to exact checkpoints or residual obligations may
be included without copying the full proof into the report.

Flatten user-supplied multiline labels and escape Markdown control content before
display. Claims, paths, and failure reasons cannot create owned-region markers,
extra history entries, or unbounded physical lines. Keep the complete original text
in the authoritative event records; abbreviated report labels link to those records.

## Percentage semantics

The recommended default is a mechanically justified exact coverage fraction where
one exists, and `not quantifiable` otherwise. State the denominator and the unmet
common obligations. A percentage of dimension cases is not a percentage of proof
effort, confidence, or a general theorem. Scalar fragments do not close dimension cases.

Even 11/11 accepted cases does not establish the objective if a required common lemma,
formal bridge, deliverable, or final audit remains open. In that state the report
shows full case coverage while explicitly leaving objective completion pending.
Only an audited `search complete` permits the objective's completion label of 100%.
A verified counterexample is reported as a disproof, not a 100% proof. If the
contract requested a decision, its resolution can be complete while the exact
outcome remains `disproved`; a proof-only request must expose that conflict.

The integrated retrospective proposal includes no subjective research percentage
in this release. It uses exact justified coverage or `not quantifiable`, while
reporting the remaining obligations and next decision. No estimate is inferred
from elapsed time, runs, paper length, or finished node count. This is the proposed
design, not a claim that the report feature is implemented.

## Latest visual

Use a compact fenced text tree so the file remains readable without a diagram
extension. Show the original objective and proof standard, selected route and AND
requirements, active continuation, accepted checkpoints, failed alternatives, and
standalone work. Use stable IDs and short English labels. Shared proof dependencies
are references, not duplicated accepted cases.

The visual is bounded for readability: show active and unresolved work first, and
link the full generated `attack/SEARCH_TREE.md` for the complete retained history.
Never remove a historical entry to keep the visual short.

## Deterministic generation and recovery

Record optional `recorded_at_utc` metadata on the event envelope, using the exact
UTC format `YYYY-MM-DDTHH:MM:SSZ`; newly committed storage events always include it.
The four existing event fields remain required. This is storage-owned audit
metadata, assigned after idempotency and revision checks; it does not affect proof
decisions, ranking, or replay. Identical request replay retains the original timestamp.
Existing timestamp-free fixtures or explicitly imported history must be labelled
time-unavailable rather than assigned fabricated historical times.

Serialize report projection updates under the same objective writer lock as event
updates, without holding that lock across process execution. Render each unreported
relevant event from its corresponding event-prefix state, in sequence. Preserve all
previous history bytes and atomically replace only the visual plus appended entries.

A crash may commit an event before updating the report. The next managed mutation,
explicit render, or reconciliation fills the missing suffix without rerunning research
or duplicating an entry. Read-only status reports a stale projection without writing it.
Historical entries use a versioned deterministic format and event-prefix facts,
including recorded cash-out observations, so their expected content can be checked
without trusting the report itself. Report corruption, an ownership mismatch, or
changed historical bytes produces an
explicit repair requirement; it is not silently corrected by overwriting history.

## Tests and integration scope

Add focused tests for five-line entries; event timestamps and replay; repeated and
concurrent updates; interrupted replacement; exact preservation of old entry bytes;
parent path conflicts and symlinks; correct node and cash-out counts; unknown general
progress; case fractions with remaining common obligations; and 100% only after
audited root completion. Verify visual updates do not alter historical text.
Include multiline labels and marker-like text in the report-format tests, so supplied
proof prose cannot change the report's ownership or append-only boundaries.

After approval, add a dedicated hardening task before final skill alignment and
release validation: event metadata and report projection, legacy observations,
hook protection, executable examples and adversarial integration. Coordinate the
metadata schema with the existing reducer without reopening or relabelling prior
task results. Already approved implementation continues while this addition is reviewed.
