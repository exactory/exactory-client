# Problem changes during a reserved move

A mathematical move can refine `problem.json` before it is journaled. This does
not change the admitted claim or authorize a different task. The controller
preserves the reservation and validates the problem change as part of the
durable journal intent.

## Normal flow

`search begin` stores the canonical native problem bytes under the digest in
the move reservation. After carrying out the admitted move, submit its ordinary
native journal line with `journal add`. No additional option is needed for a
reservation created by this version.

If the current problem digest differs from the reserved digest, the intent
contains `problem_transition: {before, after, line}`. Both complete problem
values must pass native validation and have exactly the admitted claim. Their
native digests must match the reservation and journal intent respectively.
The line must validate against the updated problem and match the reserved move,
pass, strategy, entry, walk, triggers and citations. Both canonical snapshots
are retained as immutable artifacts.

The reservation keeps its original problem digest. The journal line and its
acknowledgment record the updated digest. `problem_changed` still compares the
current problem with the previous journal line, or with the plan when there is
no previous line. It is not redefined as a comparison with `search begin`.

## Older pending reservations

Older versions recorded a problem digest without preserving its exact value as
an artifact. An unchanged problem can still be journaled without an additional
option. A changed problem needs its exact original value:

```sh
exactory-math --attack-root attack journal add SLUG \
  --problem-before SLUG/evidence/problem-before.json --json 'NATIVE_MOVE_JSON'
```

`--problem-before` is relative to the attack root, not the individual workspace.
The file must be a regular, nonsymlinked JSON file within that root. Use preserved
evidence of the actual original value. Native canonical JSON hashing, including
ASCII escaping, must reproduce the original reservation digest; whitespace in
the supplied file does not affect that digest. Its claim must match admission.
If a stored snapshot already exists, the supplied value must agree with it.

A missing original snapshot returns `problem_snapshot_required`. An incorrect
snapshot is rejected without a journal intent or budget charge. Existing corrupt
evidence cannot be replaced by this option. Preserve the records and resolve the
evidence issue. Do not invent a preimage, edit the original reservation, roll back
the problem to bypass validation, or reset the account.

## Ownership and interrupted writes

The normal writer takes the same nonblocking per-node lock as recovery before
creating the intent and holds it through the append and durable acknowledgment.
`journal_owned` means another writer or recovery operation holds that lock.
Wait for that operation and inspect status; do not remove its lock file or
start replacement work.

The lock coordinates controller writers and recovery, not arbitrary external
editors. Keep the native inputs unchanged while an intent is pending. Detected
external changes are rejected rather than overwritten.

Ordinary `search reconcile` verifies the current problem against the intent's
updated digest, checks the stored transition snapshots when present, and
validates that the frozen journal artifact is precisely the reserved prefix
followed by its one expected native line. The actual journal must equal either
the original prefix or that complete append. Recovery writes only the frozen
append if necessary, then acknowledges the original reservation exactly once.
A live writer blocks recovery both before and after its filesystem append.

A changed working problem or conflicting journal returns `recovery_conflict`.
Preserve all evidence and resolve the discrepancy against the recorded intent
before retrying. Recovery does not choose between competing problem versions.

## Compatibility and research boundaries

This version reads old reservations and old journal intents without a transition.
The native journal format and its existing flow rules are unchanged. Version
0.34.1 cannot replay the new optional transition payload. Upgrade every writer
and start a new host session before recording transitions; do not mix old and
new processes against the same root.

The journal transition does not relax frozen-input checks. Launching a run after
changing a reserved problem still fails those checks. Journaling a refinement
after an already-terminal run does not rewrite its inputs, result, or charges.
Further work still needs its normal admissibility and input review. Journaling
alone neither accepts a proof nor closes the full objective.
