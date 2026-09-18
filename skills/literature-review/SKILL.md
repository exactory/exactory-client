---
name: literature-review
description: Build a current source-grounded research or verification foundation before ideation, native admission, or exact-paper assessment. Use after cohort collection, for literature refresh, and whenever source, scope, or synthesis changes make current preparation pending.
---

# Literature review and synthesis

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and execute the
[managed research workflow](../../docs/research-workflow.md). It contains the
actual acquisition, reading, synthesis, gate, and continuation commands; the
[CLI reference](../../docs/research-cli.md) gives complete payload shapes.
Start with `exactory-research status --summary` and `next --summary`, and retain the current profile,
complete objective or exact target, original source versions, and resource limits.

## Establish the foundation

Complete the frozen cohort enumeration and the readings the recorded preparation
policy requires. Select one to five distinct exact root paper families and read
them in full with their complete bibliographies. Choose the few papers that the
claims actually rest on. Under `lineage-v1` and `sampled-v1` the foundation
rests on three kinds of sources, and the references of a full-read paper are
inventoried for identity without owing an abstract reading.

- The lineage is the line of results this research continues. Name each entry
  with `require-fulltext` under purpose `lineage`, give it the claim or entry it
  serves in `depends_on`, and read it in full.
- The classics are the foundational papers that line rests on, named the same
  way under purpose `classic`. Every lineage and classic entry is cited in the
  pinned manuscript bibliography (`lineage_citation_missing` at the manuscript
  gate).
- The recent work is read at abstract depth by the bounded loop below. A paper
  that a claim comes to rest on leaves the loop through `require-fulltext`:
  purpose `contradiction` for a source that opposes a claim, and purpose `core`
  for a verification's decisive papers, at most ten of them
  (`core_limit_reached` beyond that).

Under `exhaustive-v1` and `screened-v1` the references of a full-read paper are
Tier 3 and owe an abstract reading, until you select one with `require-fulltext`
for a consequential claim, novelty, innovation, or validity dependency; a
selected reference is Tier 2 wherever it appears, read in full with its complete
bibliography, and its own references become Tier 3. Under `screened-v1`, Tier 3
families are screened by family with their citation context before reading;
promoted, doctrine and pending families are read at abstract depth, excluded
families are sampled and audited, and `require-fulltext` families and unresolved
references keep their obligations whatever the screen says.

Capture and inventory each original body and inspect its required text, proofs,
equations, figures, tables, appendices, bibliography, and supplements. Keep source
bytes and exact inspection locations as `span` locators (offsets and the hash of
the exact substring), never as whole-document quotes. Read the extraction
diagnostics on a capture before dispatching a reader; re-capture with
`extraction_options` when layout padding makes the text unreadable. A download is
acquisition; a source-linked reading records the actual inspection. A reading
stays current while its bundle's units are unchanged; parsed bibliography entries
do not require re-reading. Record critical missing sources as pending.

Run all five distinct search purposes: `direct`, `originals`, `theory`, `adjacent`,
and `recent`. Save original queries, responses, complete enumeration, versions,
dates, scope, judgments, and gaps. Judge every found work with a disposition
(relevant, contradictory, potentially relevant, out of scope, duplicate,
unresolved) and a reason; a later search for the same purpose carries its
contradictory and unresolved findings forward or resolves them by name. A
judgment is reassessed when its scope, the content of the works it rests on, or
the citation frontier changes, not when an unrelated reference gains a version.
Retain a captured empty result when a search finds nothing. Distinguish earlier original results from later validation,
improvement, or use; a later paper is not evidence that its content existed earlier.
Under `lineage-v1` and `sampled-v1` a captured query keeps its top 10 hits,
counted across every response of that query (`invalid_search` above that). Under
`lineage-v1` these five captures are stage 1 of the bounded loop at the end of
this section. Under `sampled-v1` the five purposes are optional: record a search
for a finding that needs prior-art or contradiction evidence, and a recorded
search still owes the current literature scope.

In a development round (round number 2 or more), also run the four consequence
purposes, each recorded after the round's admission and judged like the five
above: `downstream` asks who is blocked by what the paper does not yet do, and
its found works carry the passage that states the bottleneck; `next_step` asks
whether the next step was already taken, and a `scooped` verdict withdraws the
goal; `exemplars` asks how a comparable first result was developed into a larger
contribution; `changes` asks what changed since the previous round, and a
`contradicted` verdict returns the affected claims to a cycle. Select at least
one exemplar with `require-fulltext` under the purpose `exemplar` and read it in
full (`round_exemplar_missing` until the requirement exists, then
`fulltext_reading_missing` until it is read); analyze it as one more innovation
case (`within_field` when it comes from the study's field; an `external` exemplar
counts toward the external cases, so replace an older external case rather than
exceed the count the policy allows). When the frontier changes, re-record the earlier
judgments with their carried findings, and re-record `rationale`, `context` and
`innovation` for the round.

### The five-purpose loop (lineage-v1)

Stage 1 is the five captured searches above plus two local sources. The first is
`exactory-research population-query --terms TERM [TERM ...] --limit N`, which
ranks the enumerated population's stored abstracts by terms taken from the
complete objective and the parent's title and abstract; pass its result straight
to `exactory-research batches --destination DIR --loop --candidates FILE`, which
exports the selected searches' unread hits together with those candidates. The
second is the papers that cite the parent within the window, an OpenAlex query
with the filter `cites:<parent id>` saved with `import-response` and recorded as
a `recent` search response.

Read every exported abstract and register it with `read-batch` as one loop entry
carrying `loop: {purposes, disposition, source}`, where source is `search`,
`population`, `citing` or `author`. A purpose is covered when one loop reading
judged `relevant` or `contradictory` answers its question, or when two distinct
captured queries for that purpose returned no relevant hit. For each uncovered
purpose, capture a new query and read up to 20 more abstracts. The loop stops
when every purpose is covered, when a round adds no `relevant` or
`contradictory` paper to any uncovered purpose, or at the store's limit of 100
registered loop readings per study (`loop_limit_reached`, and 40 while a
development round is open, `round_loop_limit_reached`). `status` reports the
registered readings, the limit and the covered purposes under `limits.loop`.

Close the loop with `exactory-research loop-close`, which records all five
purposes at once, each `covered` or `gap` with a note, and keeps the queries
each purpose tried; the uncovered purposes are the study's recorded gaps. A loop
reading or search recorded after the closure reports `loop_closure_stale`, and
no closure at all reports `loop_closure_missing`. Promote a paper that a claim
rests on out of the loop with `require-fulltext`.

## Synthesize before ideation

For a research profile, fix the complete original objective with `target` while
still in `literature`. Record source-grounded field standards and cohort doctrine,
And/But/Therefore rationale, innovation studies, and scientific context. Under
`lineage-v1` the innovation study reads ten candidate papers at abstract depth,
five of them in full: register each candidate with `innovation_candidate: true`
in its loop reading (`innovation_candidates_missing` until ten are read), and
choose exactly five external cases among them
(`innovation_case_not_candidate`, `external_cases_insufficient`,
`external_cases_excess`); the lineage supplies the within-field case. Under
`exhaustive-v1` and `screened-v1`, study within-field innovations plus five to
ten distinct external original paper families in full. Analyze each mechanism,
original constraint, evidence, transfer assumptions, limits, distinguishing
test, and failure signal. Several versions or case labels of one paper do not
create more families. Shared examples can suggest papers but must be reread and
assessed for the current objective.

Investigate beneficiaries, downstream scientific capabilities, social connections,
barriers, and uncertainty. Basic science can have unknown immediate applications.
Record quantitative conditions accurately and keep unknown or inapplicable
dimensions with reasons. Separate a source's support or contradiction from an
independent scientific proof, replication, or refutation.

For a verification profile, bind the exact original target and complete the same
source network, readings, and applicable standards; every policy except
`sampled-v1` also requires the five searches. Under `sampled-v1` the population
work is the drawn sample, and prior-art evidence for a specific finding comes
from targeted searches, at most 20 abstract readings per verification, each
carrying `search_hit: true` (`search_reading_limit_reached`
beyond that). Respect the historical cutoff for prior-art judgments. Judge the
paper independently of author ambitions and prior verdicts; author innovation and
impact goals are not verifier requirements.

## Exit and resume

Run `exactory-research gate preparation` against current evidence. In an author
study, log the decision and enter `ideate` with `--status pending` only after it
passes. Native math work next exports the actual foundation for reviewed admission.
External verification next binds the task and its exact independent verdict.

When sources, policy, objective-relevant scope, or synthesis change, revisit the
affected records and current gate. Resume retained collection IDs at eligible
retry times and preserve all failed access and reading gaps. A pause, availability
qualification, old receipt, or completed form does not discharge current work.
