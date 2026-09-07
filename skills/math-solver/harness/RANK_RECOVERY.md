# Operator recovery for an interrupted legacy rank

This opt-in operation addresses the initial rank interruption produced by a
top-level ranking list, followed by a correction of that list and four task
additions. Ordinary reconciliation continues to reject ambiguous changed state.
This operation neither replays the interrupted command nor rewrites its inputs.

Before invoking it, an authorized operator must inspect the original source and
incident evidence, preserve all existing records, establish a period without
concurrent native-file writers, and obtain an independent review of the exact
recovery subject. Recovery code must itself have been reviewed and tested. This
is not a general-purpose acknowledgment or a substitute for an original receipt.

## Command

```sh
exactory-math --attack-root attack search reconcile --spec recovery.json \
  --expected-revision REVISION --request-id UNIQUE_REQUEST --json
```

Without `--spec`, or with an empty object, the original reconciliation behavior
is unchanged. The new opt-in spec has exactly this shape:

```text
{
  rank_recovery: {
    subject: {
      schema_version: 1,
      root: AbsolutePath,
      objective_id: ID,
      contract_digest: Digest,
      revision: Integer,
      node_id: ID,
      intent: OriginalNativeIntent,
      current_snapshot: {files: [{path: RelativePath, digest: Digest}]},
      operator: Provenance,
      source_evidence: [FileEvidence],
      incident: FileEvidence,
      authorization: FileEvidence,
      quiescence: FileEvidence
    },
    review: {
      schema_version: 1,
      subject_digest: Digest,
      reviewer: Provenance,
      decision: "approve",
      findings: {
        read_only_origin: Text,
        delta: Text,
        authority: Text,
        preservation: Text,
        quiescence: Text
      },
      unresolved_objections: []
    }
  }
}

FileEvidence = {path: CanonicalAbsoluteRegularFilePath, digest: RawSHA256}
Provenance = {source: "host" | "operator", actor_id: Text, attestation_id: Text}
```

All records are closed. The original intent includes its unchanged argument,
pre-state and ownership digests. The complete current snapshot must use the same
ordered file inventory as native snapshot capture. The original snapshot and
all original content remain in the controller's immutable store.

`source_evidence` contains two to eight files, including the original executing
rank implementation and its integration code. The review must establish that
these sources apply to the interrupted invocation, not merely to a newer
installation. Each evidence file is nonempty and at most 2 MiB. All evidence
bytes, the full spec and review, and the current snapshot are preserved in the
transaction's immutable content store. The diagnostics reference the spec's
digest, not just a mutable pathname.

The host or operator supplies genuine authorization and independent-review
provenance. The operator and reviewer must differ in both actor and attestation
identity. The CLI checks their structure and exact subject binding; it cannot
authenticate an invented identity. An agent must never author a fictitious
independent approval or treat an approving boolean as external authorization.

## Eligibility and preservation

The root must contain one initial research node with no moves, runs,
reservations, unknown historical usage, accepted results or checkpoints. Only
one pending `rank` intent with no output paths and no native terminal receipt is
eligible. Pending initialization or other execution blocks recovery.

The complete original/current snapshot difference must contain exactly:

- `ranking.json`: a nonempty list becomes exactly an object whose sole `order`
  field contains that unchanged list in the same order.
- `tasks.json`: absent before, now containing exactly four open tasks numbered
  1 through 4, with no executed moves or completion claims.
- `activity.jsonl`: the original six-line raw prefix remains unchanged, with
  one appended `Edit` record naming `ranking.json`. It may additionally contain
  the independently attested read-only inspection pair produced by the activity
  hook: `Bash` targeting `ranking.json`, then `Bash` targeting `activity.jsonl`,
  both at the same recorded time, optionally followed by one further `Bash`
  inspection of `activity.jsonl`. No other suffix is eligible. The incident
  evidence and independent review must establish those commands' read-only
  origin; their labels alone do not prove it.

The independent review pins the exact task text, timestamps and bytes of every
file. Review frozen copies outside the native workspace so the activity hook
does not change the subject while it is inspected. The service still compares
the actual native bytes at application. These structural constraints do not
permit accepting unreviewed variants.
An additional file, a changed journal, or an unexplained log entry rejects the
operation even if an operator asks to accept it.

The service holds the transaction lock and the original inode-bound native lock
through durable event commitment. It checks the complete snapshot and evidence
again after persistence and before committing. Live, missing, replaced or
symlinked ownership evidence and any native receipt block abandonment.

These locks do not control arbitrary editors or the existing activity hook.
Operational quiescence is a reviewed precondition, not a property inferred from
a successful stat or hash check. Detected intervening edits are preserved and
the operation is refused. Do not disable hooks to manufacture quiescence.

## Outcome and retry

The existing `native_acknowledged` event records `outcome: "failed"` with explicit
operator-abandonment diagnostics. Here failed means that the operator terminated
the intent without successful completion. The original process outcome remains
unknown. No native receipt file is fabricated, and the ordinary native-effect
validator is unchanged. Existing 0.34.1 readers can replay this event shape.

Only the matching pending intent and its recovery error are cleared. No original
event or native input is rewritten, no account usage is refunded, and no proof
credit, terminal status, pause change or focus change is granted. The opt-in
path does not run unrelated workload, journal, filesystem initialization or
lifecycle observation. Generated controller views are rendered only when no
unrelated initialization is pending, including on an idempotent retry.

After success, perform a new ordinary `rank`, then read `search status` and
`search next` before continuing the same research node. Abandonment is not a
successful ranking validation. A retry with the identical request and spec is
idempotent, including after a crash following event commitment. A changed spec
cannot reuse the original request ID.
