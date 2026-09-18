# Lineage-v1 and sampled-v1 preparation (0.41.0) test evidence

Host: macOS 26.6.2 (Darwin 25.6.0), Python 3.9.6. Runner: `python3 -m unittest discover -s tests`
from the plugin worktree.

## Baseline

```
Ran 1047 tests in 2160.897s

OK
```

## Task 1

```
Ran 1088 tests in 2159.668s

OK
```

## Task 13

```
Ran 1141 tests in 2321.535s

OK
```

## Acceptance runs

Both policies were run on existing workspaces, from `init` to a passing
preparation gate. Both runs used the branch `feat/lineage-and-sampled-preparation`
directly, with the repository's `bin/` first on `PATH`. Neither run modified the
installed runtime. The author run sent nothing to the exactory server, and the
verification run sent only one read-only task fetch.

| Run | Workspace | Branch commit at the passing gate |
| --- | --- | --- |
| author, `lineage-v1` | `/Users/ryshiro/exactory/exactory-research/harness-dev/lineage-acceptance/author` | `455ec1a9ca5719275077a6ffe38cc4eb7fba2274` |
| verification, `sampled-v1` | `/Users/ryshiro/exactory/exactory-research/harness-dev/lineage-acceptance/verify` | `6f49d6fff5b893c75288a080a19a154c502b59c5` |

The author run started at `d81a190` and finished at `455ec1a`, because other
commits landed on the branch while it ran. No commit in that range touches the
`lineage-v1` author path. The verification run reached its gate on the third
attempt, at `6f49d6f`.

### Author study under lineage-v1

Subject: the haar-purity objective, the same frozen population as the original
study (`math-ph`, 2026-03-01 to 2026-08-31, 893 members).

| Pass criterion (design section 9) | Measured | Met |
| --- | --- | --- |
| Registered abstract readings in the loop, at most 100 (expected 60 to 90) | 52 | yes |
| Ten innovation candidates read at abstract depth | 10 families | yes |
| Five of those candidates read in full | 5 external cases | yes |
| Mirsohi 2026, Bulutoglu and Cheng 2004, Cheng 1997 and Facchi 2010 each read in full | all four, every reading `complete` | yes |
| At least two of `2604.02269v2`, `2605.01887v1`, `2605.12468v1` surfaced in the loop | all three, from `population-query` | yes |
| The preparation gate passes without a harness repair | `ready: true`, no obligations, no repair | yes |
| At most 2 hours from initialization to the gate | 41 minutes 23 seconds | yes |

The criterion about the four lineage and classic entries also asks that each one
is cited in the existing manuscript. The preparation gate does not read the
manuscript. That check belongs to the manuscript gate, which reports
`lineage_citation_missing`.

`status.limits` at the gate reported policy `lineage-v1`, loop 52 readings
against the limit of 100 with all five purposes covered, and 10 innovation
candidate families against the 10 required. The run came in below the expected
band of 60 to 90 because each of the five stage-1 queries was written narrowly
enough to enumerate completely, which returned 17 hits in total instead of the
50 the estimate assumed. Of the other 35 loop readings, 30 came from
`population-query` and 5 carry the source `author`, for innovation candidates
that no search returned.

Obligation codes that blocked the gate, and how each was resolved:

| Code | Count | Resolution |
| --- | --- | --- |
| `cohort_missing` | 1 | `collect` froze the collection and `import-oai-cohort` completed the enumeration from the 71 saved OAI pages |
| `fulltext_reading_missing` | 4 | `fulltext`, `bundle` and `read` for the two roots and the two lineage entries |
| `search_purpose_missing` | 5 | one captured search per purpose, each bound to its saved arXiv Atom response |
| `loop_closure_missing` | 1 | `loop-close` with all five purposes `covered` |
| `standards_missing`, `rationale_missing`, `innovation_missing`, `context_missing` | 1 each | the four synthesis records |

`candidate_checkpoint_missing` stayed open at the gate. It is a readiness
obligation, not a preparation one, and `preparation_ready` is `true`.

### Verification under sampled-v1

Subject: 10.5281/zenodo.22773593, population `quant-ph` 2026-03-01 to
2026-08-31, 8,064 members after 2,463 exclusions.

| Pass criterion (design section 9) | Measured | Met |
| --- | --- | --- |
| A sample of 100 drawn from the population, stratified over the six months, seed recorded | 100 of 8,064, months 17/17/17/17/16/16, seed `2026-09-18T07:27:30Z-acceptance-verify` | yes |
| The preparation gate passes with at most 120 registered abstract readings | 100, all of them sampled members and none from searches | yes |
| At most ten core papers read in full, including the four the verdict relies on | 4 of 10: Mirsohi 2026, Bulutoglu and Cheng 2004, Morales and Bulutoglu 2023, Harrow 2013 | yes |
| At most 1 hour from enumeration complete to the gate | 21 minutes 34 seconds, 07:26:52Z to 07:48:26Z | yes |

Prediction: `n` 100, `placed` 98 (50 above, 48 below), `unplaced` 2,
`percentile` 49, standard error 5.05, `band` 44 to 54, `widen_required` false.
`bind-verdict` accepted that prediction at store revision 621. The verdict was
not sent, and `exactory verify` was not run.

Obligation codes met, and how each was resolved:

| Code | Count | Resolution |
| --- | --- | --- |
| `target_mismatch`, `target_source_pin_missing` | 1 each | re-captured the PDF on the fixed branch, then `target` with the new `source_id` and the same `sha256` |
| `cohort_missing`, `roots_missing` | 1 each | `collect` froze the collection and `roots` named it with the pinned target |
| `historical_version_unresolved` | 41 | caused by a `historical_cutoff` on the whole literature scope, which no acquisition clears for a non-arXiv work. Re-recorded `roots` with the cutoff on the `require-fulltext` entries only |
| `collection_pending` | 1 | the arXiv result ceiling, corrected by `6f49d6f`, then nine further `resume` calls |
| `source_bundle_missing`, `bibliography_incomplete`, `fulltext_reading_missing` | 1 each per core paper | `fulltext`, `bundle` with a complete bibliography, and `read` at fulltext depth |
| `reference_identity_ambiguous` | 15 | a second target bundle whose entries name the arXiv family and whose `resolutions` retarget the 15 earlier occurrences |
| `reference_unresolved` | 4 | two cleared by acquiring the DOI they named, two resolved to `openalex:W2142809459` and `doi:10.1198/004017004000000095` |
| `sample_missing` | 1 | `sample --size 100` with a fresh seed, after the enumeration completed |
| `sample_reading_missing`, `placement_missing` | 100 each | four `read-batch` files of 25 items, every item carrying a `placement` |
| `standards_missing` | 1 | `standards` with 15 claims, each evidenced by a span inside one of the five full readings |

### The two blockers of the verification run

The verification run was blocked once on each of its first two attempts. Neither
fault is in the new `sampled-v1` code. Both were repairs that existed only in
the installed runtime and in no branch of this repository.

1. Attempt 1, at `16b6034`, stopped at the target capture. Zenodo serves every
   file URL with `Content-Type: application/octet-stream`, and the branch routed
   the body by the declared media type, so the capture landed `unexpected_mime`
   with `text: null` and `target` then failed with `invalid_target`. Commit `455ec1a`
   accepts such a body when its bytes are a PDF. The re-capture extracts 29
   pages and 80,062 text bytes from the same original bytes.
2. Attempt 2, at `455ec1a`, stopped at the enumeration. The arXiv Atom API caps
   a query at 10,000 results, this population returns 10,527 entries, and the
   branch set `Arxiv.query_ceiling` to 30000, so the partition never split and
   every further `resume` returned `http_status`. Commit `6f49d6f` sets the
   ceiling to 10000 and splits a partition whose cursor reaches it. On resume
   the partition split into 2026-03-01 to 2026-05-31 (5,162 results) and
   2026-06-01 to 2026-08-31 (5,365 results), and the collection completed.

A third fix, commit `89604d8`, landed before the run started. It makes the five
search purposes optional under `sampled-v1`, which is what section 5.3 of the
design states. Without it the foundation gate reported `search_purpose_missing`
for a verification whose searches are targeted.

### Observations for follow-up

The verification run recorded two observations about the policy.

1. `sampled-v1` removes the Tier 3 abstract obligation but not reference
   identity. Preparing the target and the four core papers still took 157
   reference acquisitions, 65 registry lookups and 14 manual identity decisions.
   That work fell before the enumeration completed, so it is outside the measured
   hour. The limit table of section 6 bounds readings and does not bound it.
2. Requiring a complete enumeration before the draw makes the collector's
   result-ceiling handling a hard precondition of every verification whose
   population is larger than one arXiv query returns. This population is such a
   case.

The author run recorded one observation about `lineage-v1`. A native registry
query is enumerated, so a capture that returns fewer records than the reported
total keeps `search_response_incomplete` and blocks the preparation gate. The
alternative route, a mapped capture, skips that check, but `parse_mapped` marks a
mapped abstract `partial`, and `batches --loop` skips a work with no complete
abstract. The two routes are therefore exclusive under the current code: a native
capture is readable and must enumerate completely, and a mapped capture
enumerates freely and cannot be read. This did not block the run, which stayed
inside the native route with five narrow queries.

## Final

The whole suite, run once on the final fix tree:

```
Ran 1155 tests in 2287.630s

OK
```
