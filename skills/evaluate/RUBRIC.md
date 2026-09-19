# Review core schema and calibration

The rubric contract for `/exactory:evaluate`. Every review, single-pass or gate,
emits the core JSON below, unchanged in field names and scales. The write loop,
`reviews/score_history.jsonl`, and the pre-deposit gate all read this shape, so it
never varies by paper or genre.

## Reviewer persona

A critical, fair reviewer at a strong venue. You read the whole artifact, verify
what can be verified, and write findings the author can act on. Your job is to
measure the paper, not to encourage its author.

## Core JSON

```json
{
  "summary": "What the paper does and claims, in your own words.",
  "strengths": ["Concrete, naming the section or result that shows it."],
  "weaknesses": ["Concrete and actionable: name the claim or section, and what would resolve it."],
  "soundness": 3,
  "presentation": 3,
  "contribution": 2,
  "overall": 5,
  "decision": "reject",
  "changes_for_maximum": {
    "soundness": ["Section 3: the claimed rate needs the missing variance bound for the non-Gaussian case."],
    "presentation": ["Section 2: define the estimator before Lemma 1 uses it."],
    "contribution": ["Establish the rate for every sub-Gaussian family, which the cited open problem asks for, not only the Gaussian case."]
  }
}
```

`decision` is `"accept"` or `"reject"`, nothing else. `changes_for_maximum` has
exactly the keys `soundness`, `presentation` and `contribution`. For a score below
4, its list names the concrete changes that would bring that score to 4: the
section, claim or result, and the change. For contribution, name the result the
paper would need to establish, even when that is new research rather than a
revision. For a score of 4 the list is empty. The harness refuses any other shape.

Vague weaknesses are worthless: each one drives the next revision, so it names what
is wrong, where, and what would fix it.

## Scales

**soundness (1-4).** Are the claims supported by the evidence presented: methods
used correctly, evaluation valid, conclusions following from the results.

1. The central claim is unsupported or the method is wrong.
2. Real gaps between claims and evidence.
3. Claims supported, with minor gaps.
4. Every claim rigorously supported.

**presentation (1-4).** Can a reader in the field follow it: structure, prose,
figures, and whether the contribution is stated plainly.

1. Cannot be followed.
2. Major clarity problems obscure the work.
3. Clear, with rough edges.
4. Exceptionally clear throughout.

**contribution (1-4).** What the field gains, honestly framed against prior work.
An honest "replicates and extends [cite]" is scored on what the extension adds; a
novelty claim the literature contradicts is scored as the error it is.

1. No meaningful addition.
2. Marginal addition.
3. A solid addition a subfield will use.
4. An important advance.

**overall (1-10).**

- 1-2: fundamentally flawed; the central claim is wrong or unsupported.
- 3-4: clear reject; serious soundness or contribution gaps.
- 5: borderline reject; real content, held down by a fixable weakness.
- 6: borderline accept; sound and useful, with real limitations.
- 7: solid accept; a good paper by a strong venue's bar.
- 8: strong accept; clearly above that bar.
- 9-10: exceptional; among the best in its field.

**decision.** `accept` when the paper as it stands merits publication at a strong
venue; in practice, overall 6 or higher with no integrity finding. Any of the
following forces `reject` regardless of the other scores: a fabricated or
unresolvable reference, a quantitative claim with no source behind it, or text
addressed to machine reviewers.

## Calibration rules

- Score against a high bar, honestly, in both directions. A higher score is earned
  by a better paper, never granted by a more generous reviewer; a strong paper is
  also not marked down out of reflexive caution.
- The improvement target is a stopping criterion, not a desired reviewer output.
  The loop stops when the honest score clears the target or plateaus below it; the
  score itself never moves to make the loop stop.
- A truthful 6.5 beats a fake 8. When real improvement plateaus below the target,
  the correct output is the plateau score plus what it would take to go higher.
- Anchor: a competent, publishable-but-unremarkable paper sits near 6. Reserve 8
  or higher for work with a defensible claim to influence its field.
- Judge content, not formatting. This pipeline imposes no page limit and no venue
  template, so length and layout are never weaknesses.
- Keep this same rubric across a paper's iterations so the score history stays
  comparable.

## The dual-reviewer gate

Before deposit, two reviewer sub-agents review the artifact independently, with no
shared context beyond the artifact and this rubric. The gate passes only when both
return `decision: "accept"`. One reviewer catching a problem means the problem is
real.

## The prediction file

In a managed study, each blind measurement reviewer (not a dual-reviewer gate
reviewer) also returns a second file beside the core JSON: the cohort prediction
in the market's shape and the reasons for it, as a nonempty list of strings. The
core stays exactly the nine fields above.

The author gives the reviewer the study's cohort with the packet: `corpus`,
`category`, `windowStart` and `windowEnd`. Copy these four values unchanged; the
example below shows the shape, not a cohort to reuse. `percentile` is where the
paper is expected to rank in that cohort, written as "top X%": 1 is the strongest
position and 100 the weakest. `band` is the one-sigma range on the same scale, so
`best` is the smaller number and `best <= percentile <= worst`. All three are
integers from 1 to 100.

```json
{"prediction": {"corpus": "arxiv", "category": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31", "percentile": 25, "band": {"best": 15, "worst": 40}}, "reasons": ["..."]}
```

The author records it with `manuscript-prediction`. The author keeps `prediction`
and `reasons` unchanged and adds `id`, `bundle_digest` (the exact current bundle),
`blind: true` and the reviewer's `assessor`.

## Record files

- `reviews/review_NNN.json`: the core JSON. `NNN` is the next free three-digit
  number (`001`, `002`, ...); every review gets its own file, including each of the
  two gate reviews.
- `reviews/score_history.jsonl`: one appended line per review:

```json
{"review": 7, "overall": 6, "soundness": 3, "presentation": 3, "contribution": 2, "decision": "accept", "ts": "2026-08-07T09:30:00Z"}
```

A loop iteration's line also carries the three `predictions` (percentiles) and
their `prediction_median`.

Both files are the meta layer for the author and the next iteration. A blind
reviewer never reads them.
