# The study workspace

`exactory-lab init` creates the study layer; `exactory-draft init` adds the
draft layer at stage 4, when the title and category exist. One study is one
workspace is one paper. Every path below is relative to the workspace root, the
directory that holds `.exactory/`.

```
<workspace>/
├── .exactory/
│   ├── study.json          study state machine (exactory-lab owns it)
│   ├── decisions.jsonl     append-only decision log
│   ├── draft.json          draft state (exactory-draft, from stage 4)
│   ├── deposit.json        Zenodo deposit record (exactory-draft, from stage 6)
│   ├── citation-check.json exactory-check lookup report
│   ├── citation-cache.json positive-only verification cache
│   └── autopilot_count     Stop-hook advance counter
├── context/                stage-0 human intake; README.md explains it
├── cohort/
│   ├── cohort.json         membership + reading ledger
│   ├── notes/<id>.md       full-text reading notes (core + authorities)
│   └── doctrine.md         the extracted doctrine
├── idea/idea.md            problem, hypothesis, contribution, experiment sketch
├── experiment/
│   ├── code/  logs/  results/  plots/
│   └── journal.jsonl       search journal, one node per line
├── draft/                  LaTeX sources; references.bib lives here
├── evidence/claims.json    claim -> source ledger
├── research/literature.md  append-only survey log
├── reviews/                reviews + score_history.jsonl
└── learnings/iter_NNN.md   predict-before-review ledger
```

The study runs inside a git repository. `exactory-lab init` runs `git init`
when the directory is not already under git, and writes a `.gitignore` for
LaTeX build artifacts (`*.aux`, `*.log`, `*.bbl`, `*.blg`, `*.out`, `*.pdf`).
Every stage commits its artifacts, so a crash resumes from the record.

## .exactory/study.json

`exactory-lab` owns this file; do not edit it by hand.

```json
{"version": 1, "slug": "curvature-scalar", "stage": "cohort",
 "status": "running", "autopilot": true, "waiting": null,
 "loop": {"target": null, "budget": null, "notes": ""},
 "created": "...", "updated": "..."}
```

- `stage` ∈ `initiate | cohort | ideate | experiment | write | evaluate |
  deposit | submit | complete`.
- `status` ∈ `pending | running | done` (free-form; these are the words the
  skills use).
- `waiting` is null, or the name of the wait the run is parked on. The Stop
  hook rests the session while it is set.
- `loop.target` is the overall score that stops the improvement loop, `budget`
  its iteration cap, `notes` the user's pacing policy in their own words. All
  optional; the loop's default stop is saturation (LOOP.md).

Advance it: `exactory-lab state set --stage <s> --status <s>`; park and release
a wait with `--waiting <reason>` and `--waiting none`; record pacing with
`--autopilot on|off` and `--loop-target/-budget/-notes`.

## .exactory/decisions.jsonl

Append-only, one JSON object per line:

```json
{"ts": "...", "stage": "ideate", "decision": "...", "why": "...", "evidence": "..."}
```

Write it only through `exactory-lab decide --decision ... --why ...
[--stage ...] [--evidence ...]`. The decision-log hook blocks a stage from
closing until it carries a decision, so the record of how the result was
produced is always complete.

## cohort/cohort.json

Written by `/exactory:cohort`. The membership and the reading ledger:

```json
{"version": 1, "corpus": "arxiv", "category": "cs.LG", "frozen": "...",
 "members": [{"id": "...", "title": "...", "year": 2026,
              "role": "member | core | authority",
              "abstract_read": true, "fulltext_read": false,
              "notes": "cohort/notes/<id>.md or null"}]}
```

Every member carries an abstract; the `core` papers and the `authority` papers
carry full text with a note file. See the cohort skill for the reading
discipline and how the doctrine is extracted.

## experiment/journal.jsonl

Append-only, one node per line:

```json
{"id": "n3", "parent": "n1", "phase": "preliminary", "plan": "...",
 "code": "experiment/code/n3.py", "backend": "local", "metric": 0.83,
 "buggy": false, "seeds": [0, 1, 2], "notes": "...", "ts": "..."}
```

`phase` ∈ `preliminary | tuning | research | ablation`. A node runs through
`exactory-lab run`, which writes `experiment/results/<node>.json` and prints
the record the journal line is built from.

## evidence/claims.json

A JSON array, one object per quantitative claim the paper makes:

```json
[{"claim": "mean absolute error drops from 0.41 to 0.29",
  "source": "experiment/results/n7.json",
  "note": "mean over 5 seeds"}]
```

`source` is a path (experiment results, or a user-provided file under
`evidence/`) or the exact command that reproduces the number. The ledger comes
before the draft: a claim appears here first, then in the text. An experiment
node's reported numbers become claim entries at the handoff into stage 4.

## research/literature.md, reviews/, learnings/

These carry the same contracts the write skill's WORKSPACE.md defines: the
append-only survey log with its closed verdict vocabulary, the review JSON and
`score_history.jsonl`, and the predict-before-review learning ledger. LOOP.md
references them; WORKSPACE.md is their authoritative description.
