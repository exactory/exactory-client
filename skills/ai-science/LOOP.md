# The improvement loop

Stages 3 to 5 are a loop, not a straight line. It measures the paper, changes
the highest-leverage thing, measures again, keeps only what improves the paper,
and repeats until the quality saturates. The measured metric is the blind
review's overall score from `/exactory:evaluate` and its RUBRIC.md. A truthful
low score beats an inflated one; the target never moves a score.

## Activation

At the first entry to stage 5:

1. Read the pacing the user stated. `study.json.loop.notes` holds it; a target
   in `loop.target` and a budget in `loop.budget` are optional overrides. The
   default, with no target set, is to run while the paper clearly improves and
   stop at saturation.
2. If the workspace has no `reviews/` history yet, measure the baseline: three
   independent blind reviews, median (the measurement protocol is in the write
   skill's WORKSPACE.md). This is iteration 001's actual score; log it like any
   iteration.
3. Decide:
   - A target was set and the baseline already clears it: skip the loop, go to
     the stage summary.
   - Otherwise: run the loop.

## Each iteration

1. **Read the record and the inbox.** Read the recent `learnings/iter_*.md`
   newest first, `research/literature.md` in full, and `cohort/doctrine.md`.
   Re-read `context/`: the user drops new material there while the loop runs.
   Decide explicitly whether to follow, adjust, or drop the previous plan, and
   say why.
2. **Refresh the literature and the doctrine.** When the planned change adds a
   claim or reframes the contribution, search for that specific change before
   making it, and log the pass under the closed verdict vocabulary. A result
   that was novel last iteration can be scooped by now. When the change touches
   how the paper positions against the field, refresh the doctrine's authority
   and open-problem lists too.
3. **Pick the highest-leverage fix.** Merge the weaknesses from the latest
   measurement's three reviews, rank them by how much each holds the overall
   score down, and take the ones revision can fix: framing, structure, clarity,
   citation coverage. When the dominant weakness is an evidence gap — a missing
   experiment, a missing seed, an unrun ablation — the fix is a bounded return
   to stage 3: run the experiment through `exactory-lab run`, journal it, bring
   the numbers into `evidence/claims.json`, then revise. A new quantitative
   claim goes through the evidence ledger first.
4. **Make the change and keep the citations and the math clean.** Add every
   reference through `exactory-check add` at the moment you cite it, compile the
   PDF, and pass `exactory-check lookup` before measuring. Fix blocking citation
   findings at the reference. When the change touches the paper's equations,
   check them with `exactory-derive check`
   and fix any `invalid` step at the math. A change that cannot be made to
   compile and verify inside the iteration is reverted and counts as not adopted.
5. **Commit the change.** Files under `draft/` (and, for an experiment return,
   under `experiment/`) only, with the iteration number in the message.
6. **Predict, then measure.** Record the expected overall score with brief
   reasoning in `learnings/iter_NNN.md`, then measure: three fresh blind
   reviews, median. The review is blind — the draft carries no revision markers
   and the reviewers see no prior scores.
7. **Adopt or revert.** A median above the best kept median stays and becomes
   the new best. Otherwise `git revert` the change commit.
8. **Write the record.** Append the `score_history.jsonl` line, finish
   `learnings/iter_NNN.md` with the actual score, the delta, and why the gap,
   then commit every changed file outside `draft/` and `experiment/` as a
   separate records commit. Records are never reverted.

## Stop at saturation

Stop the loop when any of these holds:

- **Saturation:** two consecutive iterations were not adopted, or the median's
  gains have fallen inside review noise (a change smaller than the spread of
  the three reviews). This is the default stop, and the one the user asked for:
  run while the paper clearly improves, stop when it stops improving.
- **Target:** a stated `loop.target` is reached.
- **Budget:** a stated `loop.budget` of iterations is spent.

When honest work plateaus below a stated target, report the plateau and what it
would take to clear it — never a more generous reviewer.

## After the loop

Run the pre-deposit dual-reviewer gate (in `/exactory:evaluate`): two
independent reviewers, both must return accept, fresh reviewers on any re-run.
The gate is a gate, not a measurement. Then advance to stage 6.

Under autopilot, a saturated study proceeds to a sandbox deposit and parks
before production deposit and submission, unless the invocation pre-authorized
those (SKILL.md, autopilot).

## Resume

The loop needs no extra state to resume. A fresh agent reads the learning
ledger, `reviews/score_history.jsonl`, and `git log`, and continues at the next
iteration. If an iteration was interrupted mid-way, working-tree changes with
no measurement record are reset to the last commit and the iteration is redone.
