# Review request parity at 0.72.1

This suite pins the contract of the request reuse of release 0.72.2
(`docs/releases/0.72.2.md`): no report, refusal, packet or request changes.
Its oracle is `ceb050b26e6bdc372d06e6003836e1e3d8fc0a82`.
It changes no harness module or existing fixture.

The test runs existing fixture builders into temporary stores, then reads those
stores through the current harness. The generator runs the same replay helper
inside a clean `git archive` of the base. All existing builders, historical
modules and stored inputs in that process come from the archive. The generator
imports no harness in its parent process and rejects collection in a git checkout.
Only the generator and replay helper are copied into the export.

Each case retains the complete `status_report(counters=True)`, gate report,
assignment states, selected attempt tuples (including refused and completed
attempts), every attempt record, authentication results and stored refusal
records. Obligation and refusal lists retain their original order. Successful
reports are stored as full JSON, without selecting a subset of report fields.
An operation that refuses is represented by the complete `ResearchError.as_dict()`.
Reading the reports must leave the snapshot unchanged.

Every packet and native packet referenced in the records, and every request
referenced by an invocation, probe, attempt or call capture, has its complete
artifact descriptor, physical byte length and SHA-256 retained. ArtifactStore
reads separately pin integrity errors. Every rebuilt assignment request and
every request passed to `review_transport.observe`, including probes and failed
calls, has its byte length and SHA-256 retained. Serialization is exactly
`review_protocol._put_json`: sorted keys, `ensure_ascii=False`, `allow_nan=False`,
UTF-8 and the default JSON separators, including spaces. Logical histories use
the same serialization and include their integrity refusals. No request field
is removed or normalized during comparison.

The compressed JSON oracle keeps one small case per distinct behavior. This
follows the frozen prompt fixture's approach of retaining distinct shapes and
pinning large text by digest, rather than storing every permutation or expanded
request body. No additional stored input fixture was necessary. The frozen file
is 92,223 bytes (1,298,670 bytes of uncompressed JSON).

## Fixtures and exercised paths

| Cases | Existing inputs/builders | Paths |
| --- | --- | --- |
| `pending`, `completed` | `ReviewProtocolTests` | Ordinary HTTPS current request, no completed attempt, completed selection and provenance authentication. |
| `incomplete`, `failed` | `ReviewProtocolTests`, public attempt import | Provider-incomplete response, transport exception, stored imported `incomplete` attempt, no selected completed attempt. Full attempt records retain the failure codes. |
| `contaminated` | `ReviewProtocolTests`, context event API | Authentic observation with contaminated context; exact provenance and the contamination obligation remain distinct. |
| `corrupted-request`, `forged-provenance`, `forged-request` | `ReviewProtocolTests` | Request artifact integrity failure; wrong prompt provenance; a readable altered request with a matching claimed request digest still fails full reconstruction equality. |
| `recorded-earlier-fit` | `ReviewProtocolTests`, Codex route helpers | Real stored observation built with `EARLIER_FIT_ORDERS[1]`. History-body and scientific-history-body reductions produce two distinct candidates. After recording, all historical-construction patches end: current selection must choose the earlier request and authenticate it. The ordered candidate byte digests are also frozen. |
| `result-parts-v1` | `ResultPartsCase` | Legacy parts, continued logical history, final delivery, value-review recording and coverage checks. |
| `result-parts-v2` | `PartsResultCase` | Fresh-input read, combine and final turns, inventory/receipt authentication, final coverage and value-review recording. |
| `corrupted-history` | `ResultPartsCase` | Physical history-page corruption makes reconstruction refuse and prevents authentication of its final observation. |
| `result-history-06918` | Legacy branch of `ProblemResultHistoryTests.record_fixtures`, `problem_result_history_06918.json.gz` | Three recorded result series with two closed finals and a current part series. The original canonical 0.69.18 request must still equal its existing independently generated fixture before the complete workspace is assessed. |
| `native-math`, `native-refused`, `native-refused-retry` | `NativeOpennessReviewTests` | Native proof review, malformed native output, full persisted refusal details, no usable selection, then an accepted retry selected after the refused observation. |
| `corrupted-packet` | `NativeOpennessReviewTests` | Physical native assignment-packet corruption, artifact-read details and provenance rejection. |
| `openness-v4` | `V4ReplayTests`, frozen 0.71.8 reading/excerpt modules | Actual v4 first turn replayed and continued under current code, selected source and native proof delivery, final chain verification and recorded openness. |
| `openness-v5` | `SearchSharingCase` | Shared disposition bodies, component reading turns, original search receipts, final problem coverage, native approval and recorded openness. |
| `claude-local` | Claude clean-stream helpers and ordinary store builder | Real Claude stream inspection, probe, stored observation, current/duplicate earlier candidates and authentication. |
| `codex-framed-local` | `CodexControlTests` inspection helpers and ordinary store builder | Framed adapter registration, actual rollout inspection, probe, current request and authentication. |

The result helpers seed review-only search records. After recording the series,
the replay adds the scope fields that complete status reads (`profile`, `purpose`,
empty work lists and `responses`). These are fixture construction mutations,
not report exclusions. The 0.69.18 request is checked before those mutations.
The legacy replay retains the existing in-process observation helper because
0.69.18 predates durable call captures; the other cases exercise current captures.

## Determinism and exclusions

There are exactly two comparison exclusions, both at the status report root:

1. `runtime`: this describes the current plugin version, source revision,
   dirtiness, executable location and package bytes. It differs between a clean
   archive and a worktree, and the package digest necessarily changes with the
   implementation under test. Scientific route/runtime pins remain compared.
2. `evaluation`: per-command counters, retention accounting and elapsed time.
   These are the invocation diagnostics that the optimization intentionally
   changes; release 0.72.2 also adds this field to problem status reports.

Other variable values are fixed when constructing fixture inputs, not removed
from recorded outputs: UUIDs use a deterministic sequence; workspace creation
and update timestamps use a fixed UTC datetime; the transport clock is fixed
so observed wall time is zero. HTTPS and Codex runtimes use synthetic fixture
observations, as the existing Codex builders do. Claude uses its actual pinned
runtime. Both openness inputs receive `/fixture/request-parity` as their native
root before any assignment or observation is recorded. This avoids embedding
temporary directory names in emitted requests. All these values and their
derived digests remain in the comparisons.

## Regeneration and validation

Run from the worktree root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests /opt/homebrew/bin/python3.12 tests/generate_review_request_parity.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests /opt/homebrew/bin/python3.12 -m unittest test_review_request_parity
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests /usr/bin/python3 -m unittest test_review_request_parity
```

`--output /tmp/another-oracle.json.gz` generates an independent oracle for a
byte-for-byte reproducibility check. Gzip uses `mtime=0`.

Validation on the base worktree: 22 tests pass on Python 3.12.8 (37.403 seconds)
and Python 3.9.6 (39.121 seconds). An independent archive regeneration is byte
identical. A negative control that changes a rebuilt request's instructions
fails the parity assertion. Generated-file and whitespace checks also pass.

## Limits

This suite does not exercise visual requests or staged-review task graphs, which
release 0.72.2 leaves on their existing paths. The framed case uses ordinary
text, not the opt-in verbatim evidence marker. The v2 series does not include an
original-check turn or segmented bodies. Only one distinct earlier-order shape
is recorded; later-candidate exceptions and exhaustive lazy iteration behavior
belong to the iterator tests of `test_review_request_evaluation.py`. Budget
pressure, eviction, zero/tiny budgets, cross-view sharing, mutation during a
warm evaluation, unsupported codec values, key-component perturbations and
derivation counts also belong to `test_review_request_evaluation.py` and the
evaluation budget tests. This is a behavioral oracle for small stored
workspaces, not the 141-assignment workload measurement.
