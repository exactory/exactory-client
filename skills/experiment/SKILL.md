---
description: Run the experiments for a study on exactory - a best-first search that writes, runs, debugs, and improves experiment code across preliminary, tuning, research, and ablation phases, with an optional autoresearch optimization mode, recording a journal, metrics, and plots. Use after the problem is set and the user wants results.
---

# Experiment

Implement and run the experiments that test the hypothesis. You write each
node's code, run it through `exactory-lab run` (guarded, confined to the
workspace), read the result, debug, and improve, keeping a journal so the
search is resumable and honest. Every number the paper will report comes from
here and traces to a results file.

Run every command from the workspace root. The tool is `exactory-lab`, on PATH
while this plugin is enabled.

## Security and safety, before anything else

- Experiment code is model-written. All code, data, and outputs stay inside
  `experiment/`. `exactory-lab run` refuses a script outside it, and the
  `guard-experiment-exec` hook blocks the catastrophic shell class. A block is a
  redesign signal: fix the experiment so it does not need the action, never
  route around the guard.
- Datasets are tiny, synthetic, or small built-in sets. Scripts generate their
  own data; do not download large datasets, and do not use credentialed data
  sources in this version.
- Set seeds everywhere and log versions.

## The four phases

1. **Preliminary** — a minimal correct implementation running end to end on
   tiny or synthetic data, establishing that the pipeline produces a metric.
2. **Tuning** — tune the baseline to a sensible operating point; record the
   baseline metric.
3. **Research** — the hypothesis tests themselves, compared against the
   baseline, multi-seed for variance.
4. **Ablation** — remove or vary components to isolate what matters.

Honest defaults for a laptop, stated so you can be honest about the trade-off:
3 initial drafts, up to 3 consecutive debug attempts per branch, 3 seeds, and a
few iterations per phase unless the user asks for more.

## The search

Maintain `experiment/journal.jsonl`, one line per node (schema in STUDY.md).
Each iteration:

1. **Draft.** Create the initial implementations as root nodes, each a
   self-contained script under `experiment/code/`, each a different approach.
   Run genuinely independent branches as parallel sub-agents, one per branch;
   each writes and runs its own node and reports its metric, then you merge them
   into the journal and pick the best. Use this for real parallelism, not for
   debugging one branch.
2. **Execute** each node:
   ```
   exactory-lab run code/n3.py --timeout 1800 [--seed 0]
   ```
   The script prints a JSON metrics line (a JSON object with a `"metric"` key,
   e.g. `{"metric": 0.83, "loss": 0.1}`) or writes
   `experiment/results/<node>.json`. `exactory-lab run` captures the log to
   `experiment/logs/<node>.log`, writes the result record, and prints it; append
   a journal line from it.
3. **Evaluate.** Parse the metric. A node with a non-zero exit, a timeout, or no
   metric is `buggy`.
4. **Expand best-first.** Pick the best non-buggy node and improve it, or debug
   a buggy node up to the debug-depth limit before abandoning that branch.
5. **Stop the phase** when the iteration cap is hit or the metric plateaus, then
   seed the next phase from the best node.
6. **Multi-seed** the best research node across the seed count and record
   mean ± std.

## Compute routing

Decide per node where it runs; the artifacts come back in the same layout
either way.

- **Local** (default): preliminary, debugging, plotting, and anything that fits
  CPU or MPS inside the timeout. Keep it small.
- **Colab GPU** (`--backend colab`): only when the node genuinely needs a GPU
  and a runner is alive (`exactory-lab colab-status` reports `runner_alive:
  true`). If no runner is alive, run the node locally and small, or ask the user
  to start the runner notebook — never block an unattended autopilot run on a
  dead runner. Colab is compute only.

The compute layer is pluggable: `local` and `colab` are the backends today, and
more can be added behind the same run contract. Ask the user which backend to
use for heavy work; do not assume Colab is available.

## Autoresearch mode (optional)

Some sub-tasks are pure metric optimization: a scalar goal metric exists and the
search space is parameters, not ideas — hyperparameter tuning, a mathematical
optimization task. For those, and only those, run the keep-or-revert discipline
that autonomous optimizers use:

1. **Activate** only when the goal is a single scalar metric and the moves are
   parameter changes, not new hypotheses. Log the engagement as a decision. It
   is on by default when that condition clearly holds; otherwise stay in the
   ordinary search.
2. **Measure a baseline**, noise-aware: the median across seeds, so a change
   smaller than the seed spread does not read as improvement.
3. **Change one thing**, re-measure, and **keep only what beats the best kept
   result**; revert otherwise. Journal every attempt as a node.
4. **Stop** on plateau (two consecutive non-improvements) or the budget.

This raises the accuracy of the reported result on tasks that have a real
optimization target, without turning the search into unbounded compute.

## Outputs

- `experiment/results/` — per-node metrics, and a `summary.json` with the best
  results per phase (baseline, research, ablation), the seeds, and mean ± std.
- `experiment/plots/` — one clear figure per claim, with axis labels, legends,
  and a caption saved alongside as `<fig>.caption.txt`.
- `experiment/journal.jsonl` — the full search trace.
- `evidence/claims.json` — every number the paper will report, entered here as
  a claim pointing at its results file. This is the handoff into the draft:
  a claim with no source does not enter the paper.

When the results feed derived equations that will enter the paper, check those
manipulations with `exactory-derive check`
so a wrong step is caught here, not by a verifier later.

Log the stage decision (what worked, what did not, the key numbers, honestly)
and set the state: `exactory-lab state set --stage write --status pending`.

## Output to the user

Summarize the best result against the baseline, what the ablations showed, the
surprises and failures, and the wall-clock cost. Report failures truthfully; a
result that did not hold is a finding, not something to hide.

## What not to do

- Do not put a number in the study that has no results file behind it.
- Do not route around the guard; redesign the experiment.
- Do not download large or credentialed datasets.
- Do not block an unattended run on a Colab runner that is not alive.
