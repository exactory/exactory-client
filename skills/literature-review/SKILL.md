---
name: literature-review
description: Use when a research question needs literature search, source reading, citation expansion or source-grounded synthesis at any stage.
---

# Literature review and synthesis

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and execute the
[managed research workflow](../../docs/research-workflow.md). It contains the
actual acquisition, reading, synthesis, gate, and continuation commands; the
[CLI reference](../../docs/research-cli.md) gives complete payload shapes.
Start with `exactory-research status --summary` and `next --summary`, and retain the current profile,
complete objective or exact target, original source versions, and resource limits.

## State the tool call

Use this tool at any stage. Before work, state the question and scope, how many
distinct works to survey, how many to read at abstract depth only and how many
to read in full. Also state hits per query, source providers, expansion direction
and levels, required units and resource allowance. Explain the selection and
stopping rule. Record the effective amounts, actual completed counts, captured
coverage, gaps, stopping reason and cost. The caller can revise depth within its
authorized budget; a research default change uses `depth` with a reason and
consuming calls accept applicable overrides.

The [tool catalog](../../docs/research-tools.md) gives the shared contracts.
Preparation policies supply presets. Research `read-batch` calls keep
`depth: "abstract"` and put loop, round-loop and search-hit amount overrides
in `depth_values`; the saved batch records `effective_depth`.
Tool records grant no approval; a claim's
fixed minimum and independent assessment determine sufficiency. Managed
`status` marks preparation `advisory: true` and `next` names the current
milestone's missing condition. Verification retains its fixed contract.

## Search, read and expand

Population work can supply candidates whenever useful. Select one to five
exact root families when establishing a literature scope; a dependent direction
claim requires them read in full with their complete bibliographies. Choose the few papers that the
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
equations, figures, tables, appendices, bibliography, and supplements. A figure
unit of an HTML page that has a `style` element or a stylesheet link keeps a
`visual_document_style_unsupported` item, so its full reading stays `partial`
even after `visual` acquired its image. The figure units of a PDF capture of the
same version, with `pdf` locators, can complete. Keep source bytes and exact
inspection locations as `span` locators (offsets and the hash of the exact
substring), never as whole-document quotes. Read the extraction
diagnostics on a capture before dispatching a reader; re-capture with
`extraction_options` when layout padding makes the text unreadable. A download is
acquisition; a source-linked reading records the actual inspection. A reading
stays current while its bundle's units are unchanged; parsed bibliography entries
do not require re-reading. Record critical missing sources as pending.

Identity conflicts stay stored: provider families are never merged. Before an
import that registers the arXiv version of a paper the store knows by its DOI,
run `exactory-research import-response --preview --file PAYLOAD` to see the
DOI families that the batch makes ambiguous; the real import then reports the
same under `identity_conflicts` and continues. A reference that the conflict
turns `reference_identity_ambiguous`, in an earlier reading or in the new
bundle, is repaired by a new `bundle` of its body whose entry or resolution
names `target_family`, the acquired family that the evidence supports, with the
same link and reason as any entry. The choice resolves that occurrence only; the
conflicting assertions and the original evidence stay recorded.

The five-purpose preparation preset covers `direct`, `originals`, `theory`, `adjacent`,
and `recent`. Choose the purposes the question needs, retaining original
queries, every captured response, bounded or complete coverage, versions,
dates, scope, judgments, and gaps. Judge every found work with a disposition
(relevant, contradictory, potentially relevant, out of scope, duplicate,
unresolved) and a reason; a later search for the same purpose carries its
contradictory and unresolved findings forward or resolves them by name. A
judgment is reassessed when its scope, the content of the works it rests on, or
the citation frontier changes, not when an unrelated reference gains a version.
Retain a captured empty result when a search finds nothing. Distinguish earlier original results from later validation,
improvement, or use; a later paper is not evidence that its content existed earlier.
Under the bounded presets, research uses the call's effective hit count
(default 10). Save requested, returned and inspected counts per query, captured
page positions, provider and request ordering, and completeness evidence.
`complete` requires enumeration evidence, `top_k` ordering evidence, and
`bounded_unordered` describes inspection without that ordering. Author labels
supply neither. Bounded inspection leaves results beyond it unknown; parser or
capture errors remain integrity gaps for research. Verification keeps every recorded enumeration
pending item and its `search_pending` obligation, including under the current
coverage schema. Legacy captures retain established
coverage and use `unspecified` only when ambiguous. See the
[coverage contract](../../docs/research-tools.md#search-coverage).

A search may state a provisional `question` before roots exist, retaining
`scope_digest: null` and `provisional: true`. It appears in status and
remains exploratory. A later scoped search can reuse those captured responses
through a recorded binding. Claim minimums count searches bound to current scope.
Under `sampled-v1`, the five purposes remain optional; verification uses
targeted searches and its existing fixed amounts.

In a development round, inspect what the next scientific decision needs from the
four consequence purposes: `downstream` (whose bottleneck remains), `next_step`
(whether the result is already known), `exemplars` (how related work advanced),
and `changes` (what evidence changed). Refresh affected purposes when the admitted
question, scope or sources require it, and reuse verified sources and findings
that still answer them. Do not produce new searches solely to obtain another
receipt. Retain actual captured queries, decisive passages and reading evidence.
A scooped or contradicted goal returns to the canonical research decision with
its original evidence retained.

When an exemplar is scientifically needed, read its primary source in full and
analyze the transferable mechanism as an innovation case. State the effective candidate and case amounts and preserve each section's
recorded counts. A new amount changes the call's depth; it grants no claim approval.
Refresh affected rationale, context, innovation and dependent judgments with
explicit carried findings. The [decision lifecycle](../../docs/research-decisions.md)
keeps those source judgments distinct from independent support/value approval.

### The five-purpose loop (lineage-v1)

Stage 1 is the five captured searches above plus two local sources. The first is
`exactory-research population-query --terms TERM [TERM ...] --limit N`, which
ranks the enumerated population's stored abstracts by terms taken from the
complete objective and the parent's title and abstract; pass its result straight
to `exactory-research batches --destination DIR --loop --candidates FILE`, which
exports the selected searches' unread hits together with those candidates. The
second is the papers that cite the parent within the window, an OpenAlex query
with the filter `cites:<parent id>` saved with `import-response` and recorded as
a `recent` search response. Bind the whole filter value as that response's
query, for example `cites:W123,from_publication_date:2025-01-01` when the filter
also holds the window's date clause.

Read every exported abstract and register it with `read-batch` as one loop entry
carrying `loop: {purposes, disposition, source}`, where source is `search`,
`population`, `citing` or `author`. A purpose is covered when one loop reading
judged `relevant` or `contradictory` answers its question, or when two distinct
captured queries for that purpose returned no relevant hit. For each uncovered
purpose, capture a new query and read up to 20 more abstracts. The loop stops
when every purpose is covered, when a round adds no `relevant` or
`contradictory` paper to any uncovered purpose, or at the call's effective study limit (default 100, `loop_limit_reached`)
or development-round limit (default 40, `round_loop_limit_reached`). `status` reports the
registered readings, the limit and the covered purposes under `limits.loop`.

Close the loop with `exactory-research loop-close`, which records all five
purposes at once, each `covered` or `gap` with a note, and keeps the queries
each purpose tried and its effective limit; uncovered purposes are recorded gaps.
Later defaults leave a saved closure's recorded amount unchanged. A loop
reading or search recorded after the closure reports `loop_closure_stale`, and
no closure at all reports `loop_closure_missing`. Promote a paper that a claim
rests on out of the loop with `require-fulltext`.

## Synthesis notes and direction evidence

Record challenges ahead with `grand-challenge`: ultimate and nearer goals,
advance criteria and full-read source evidence. Retain the current record until
evidence changes the direction. Read field lineage, reviews, open problems and
posted Grand Challenges as source material when useful.

Ai-science's direction minimum requires a scoped valid `direct` search with
explicit bounded or complete coverage, accounted dispositions and resolved
integrity gaps; full readings of every relevant or contradictory hit and root;
selected standards with valid own sources and resolved applicability; and a
current Grand Challenge record. A deferral leaves a decisive reading missing.
The complete preparation report remains advisory.

Use the [direction decision contract](../../docs/research-decisions.md#before-target-commitment)
when the orchestration commits a direction. Two assessors establish the Q1 bar
before receiving the slate. Recording a version-2 investigate/pivot decision and
committing a target require the minimum and source content actually delivered
in each reviewer's observed history. Complete inspected ranges and every image
asset named by an inspection are required. Missing ranges or image bytes keep
`direction_evidence_unassessed`. Inventory, reading notes and chronology alone
supply no delivery coverage; qualifying historical attachments remain usable. The decision binds `direction_evidence_digest`; newly read decisive
evidence needs a new decision with reviews that saw it. `q1_venue` assesses
potential in slate reviews and realized supported advance at results.
Target commitment retains its exact approved content and source-impact declaration.
Native mathematical objectives and external verification hold their own gates.

Record source-grounded field standards and cohort doctrine, And/But/Therefore
rationale, innovation studies, and scientific context. Under
`lineage-v1` the default innovation depth is ten abstract-read candidates and
five full-read cases. State effective amounts for the call and record them.
Register each candidate with `innovation_candidate: true`
in its abstract reading (`innovation_candidates_missing` until the effective
count is read), and choose the effective number of external cases among them
(`innovation_case_not_candidate`, `external_cases_insufficient`,
`external_cases_excess`); the lineage supplies the within-field case. Under
`exhaustive-v1` and `screened-v1`, a new section uses the recorded
`innovation_cases` as its minimum external-family count and
`max(10, innovation_cases)` as its maximum, alongside a within-field case.
Historical sections without recorded depth retain five to ten external families. Analyze each mechanism,
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

## Return and resume

Return the completed work, actual coverage and remaining gaps to the caller.
The orchestration evaluates its current gate. In managed research
`gate direction` lists the minimum's missing evidence before a cycle decision
exists and governs completing literature and entering investigation planning;
`gate execution` governs admission to experiment. `gate preparation` remains
an advisory preparation report there. Native mathematical research can use an
optional common foundation; verification retains its required preparation.

When relevant sources or claim scope change, revisit affected evidence and its
gate. Unrelated reading, synthesis or constitution changes leave a claim current.
Preserve full dependency maps and historical inputs for audit and compatibility.
Resume retained collection IDs at eligible retry times and preserve failed access
and reading gaps. A pause, availability
qualification, old receipt, or completed form does not discharge current work.
