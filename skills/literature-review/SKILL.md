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

Complete the frozen cohort enumeration and the abstract readings the recorded
preparation policy requires. Select one to five distinct exact root paper families
and expand their captured bibliography occurrences through three tiers. Read
Tier 1 (the roots) in full with complete bibliographies. Their references are
Tier 3, read at abstract depth, until you select a reference with
`require-fulltext` for a consequential claim, novelty, innovation, or validity
dependency; a selected reference is Tier 2 wherever it appears, read in full with
its complete bibliography, and its own references become Tier 3. Choose the few
papers that the claims actually rest on. Under `screened-v1`, Tier 3 families are
screened by family with their citation context before reading; promoted,
doctrine and pending families are read at abstract depth, excluded families are
sampled and audited, and `require-fulltext` families and unresolved references
keep their obligations whatever the screen says.

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
case. When the frontier changes, re-record the earlier judgments with their
carried findings, and re-record `rationale`, `context` and `innovation` for the
round.

## Synthesize before ideation

For a research profile, fix the complete original objective with `target` while
still in `literature`. Record source-grounded field standards and cohort doctrine,
And/But/Therefore rationale, innovation studies, and scientific context. Study
within-field innovations plus five to ten distinct external original paper
families in full. Analyze each mechanism, original constraint, evidence, transfer
assumptions, limits, distinguishing test, and failure signal. Several versions or
case labels of one paper do not create more families. Shared examples can suggest
papers but must be reread and assessed for the current objective.

Investigate beneficiaries, downstream scientific capabilities, social connections,
barriers, and uncertainty. Basic science can have unknown immediate applications.
Record quantitative conditions accurately and keep unknown or inapplicable
dimensions with reasons. Separate a source's support or contradiction from an
independent scientific proof, replication, or refutation.

For a verification profile, bind the exact original target and complete the same
source network, readings, five searches, and applicable standards. Respect the
historical cutoff for prior-art judgments. Judge the paper independently of author
ambitions and prior verdicts; author innovation and impact goals are not verifier
requirements.

## Exit and resume

Run `exactory-research gate preparation` against current evidence. In an author
study, log the decision and enter `ideate` with `--status pending` only after it
passes. Native math work next exports the actual foundation for reviewed admission.
External verification next binds the task and its exact independent verdict.

When sources, policy, objective-relevant scope, or synthesis change, revisit the
affected records and current gate. Resume retained collection IDs at eligible
retry times and preserve all failed access and reading gaps. A pause, availability
qualification, old receipt, or completed form does not discharge current work.
