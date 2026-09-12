# Development rounds: progress and resume record

This file is the durable checkpoint of the implementation of
[the development rounds plan](2026-09-12-development-rounds.md) against
[the spec](../specs/2026-09-12-development-rounds-design.md). A session can
stop at any moment (usage limit, crash, user interrupt). Whoever resumes reads
this file first, then continues from the first unchecked item. Every agent that
finishes a task, a fix or a release step updates this file in the same commit as
the work, so the file never lags the branch.

Worktree: `/Users/ryshiro/exactory/plugins/exactory-client-worktrees/development-rounds`,
branch `feat/development-rounds`, base `main` at `19ef5d2` (0.38.0).
Runner: `python3 -m unittest discover -s tests -p <test_file.py>` from the
worktree (the `tests.<module>` form does not import; the fixtures are top-level
modules). Full suite: `python3 -m unittest discover -s tests` (about 17 minutes,
baseline 901 tests OK in 1025 s). Commit messages go through a file
(`git commit -F`), with the attribution lines the session provides.

## How to resume

1. Read this file and `git log --oneline main..HEAD` in the worktree. The branch is
   the truth; this file says what the commits mean and what is still owed.
2. Confirm the tree is clean (`git status --short`). Uncommitted work from an
   interrupted agent is inspected, finished or reverted before anything else.
3. Continue at the first unchecked item below, in order. Each plan task keeps the
   plan's TDD steps; each task ends with two independent reviews (specification
   conformance; code quality and test rigor) and fixes for every blocker and
   major finding, then a commit that also updates this file.
4. An orchestrating workflow may be used (one implementer, then two reviewers in
   parallel, then a fixer, per task; never more than two agents at once). Pass the
   first pending task number as `args.start` and the pending findings below as
   the first fix. If the workflow dies, this file is still current because every
   agent commits it with its work.

## Task status

| Task | Title | Status | Commits |
| --- | --- | --- | --- |
| 1 | Resource purpose `development` and unit `rounds` | done, reviewed | `faa7b71`, `a0e72f3` |
| 2 | The `next_round` disposition and carried developments | done, reviewed | `2d46d94` |
| 3 | Objective lineage | done, reviewed | `e92b4aa`, `fdfb4ba`, `92ec000`, `97d536f` |
| 4 | `rounds.py`: the round decision and its independent review | implemented, first fixes applied (`4d352c7`); second review's findings pending (below) | `523beb2`, `987806e`, `4d352c7` |
| 5 | `round-admit` opens the next round | pending | |
| 6 | Consequence purposes and the exemplar reading in the literature stage | pending | |
| 7 | `manuscript-prediction`: the blind cohort prediction | pending | |
| 8 | `round-assess` and the round's derived progress | pending | |
| 9 | The `round` gate, the stage transitions and the status report | pending | |
| 10 | The round packet and `export --kind round` | pending | |
| 11 | CLI commands, example catalog and CLI documentation | pending | |
| 12 | Constitution Version 3, skills, README, version 0.39.0, release note and testing record | pending | |

## Checklist

- [x] Task 1 implemented, reviewed, fixed (legacy seven-unit accounts stay readable through `_account`)
- [x] Task 2 implemented, reviewed
- [x] Task 3 implemented, reviewed, fixed twice (null wider objective refused; lineage contract recorded; reused ancestor id refused)
- [x] Task 4 implemented and first review fixed (`cycle_authors` union for independence; reopening, stop and review bindings)
- [ ] Task 4 second-review findings fixed (see "Pending findings" below), tests green, this file updated
- [ ] Task 5 implemented, reviewed, fixed
- [ ] Task 6 implemented, reviewed, fixed
- [ ] Task 7 implemented, reviewed, fixed
- [ ] Task 8 implemented, reviewed, fixed
- [ ] Task 9 implemented, reviewed, fixed
- [ ] Task 10 implemented, reviewed, fixed
- [ ] Task 11 implemented, reviewed, fixed
- [ ] Task 12 implemented, reviewed, fixed
- [ ] Full suite green (`python3 -m unittest discover -s tests`), count and time recorded in `docs/testing/development-rounds.md`
- [ ] Whole-branch two-reviewer pass (record integrity and gate semantics; specification conformance), every blocker and major fixed with a regression test, findings recorded in the testing record
- [ ] `python3 codex/generate.py --check` and `git diff --check` clean; full suite again if code changed
- [ ] Branch pushed; PR to `main` in `exactory/exactory-client` opened with the release summary and attribution; six CI jobs green; merged
- [ ] `local-dev` and `dev` fast-forwarded to the merge commit; tag `exactory--v0.39.0` ("Exactory 0.39.0"); GitHub release created from `docs/releases/0.39.0.md` with absolute links
- [ ] `main` pulled in the primary checkout `plugins/exactory-client`; the closed-gravity study's next command reports `constitution_revalidation_required`

## Deviations from the plan so far

- Tests run with `python3 -m unittest discover -s tests -p <file>`; the plan's `tests.<module>` form does not import.
- Task 1: `tests/test_research_resources.py` has a module-level `limits()` helper, not a method; a legacy `resource_account` persisted with seven units is normalized at the read seam (`resources._account`) so upgraded stores keep working.
- Task 3: `principles._validate_objective` was extracted; `widen_objective` refuses a null target, an unchanged statement, and any objective id that already exists (the current one or an ancestor); the lineage record carries the containment contract.
- Task 4: `development.cycle_authors(records)` is the union of plan and assessment authors and is used by readiness, round reviews and (later) predictions; a stop decision on a bundle is refused while an admitted round is unassessed; reopening and review bindings follow the spec text.

## Pending findings

Recorded verbatim from the second review of Task 4 (two independent reviewers). Each is fixed in code with a regression test, never by weakening a test.

### Finding 1 (major): `research_harness/rounds.py`:211

- [ ] fixed

Summary: _reopening accepts a reopening of any assessed round, including one assessed successful. Spec 5.1: 'A goal that repeats an earlier withdrawn or unsuccessful goal is accepted only with next.reopening'; a successful round's goal has no reopening path and must be round_goal_repeated. Commit 4d352c7's message claims 'only a withdrawn or unsuccessful goal reopens' but the code never reads assessment['successful']. Confirmed empirically: with round-2 written successful=True on the current bundle, record_round accepted a closes=2 decision whose goal repeats round-2's goal with reopening round_id=round-2 (probe in the scratchpad, built on RoundsCase.write_round).

Fix: In _reopening replace lines 210-212 with: admission = records.get('round_admission', {}).get(value['round_id']); assessment = assessment_for(records, admission['id']) if admission else None; if assessment is None or assessment['successful']: raise ResearchError(_ERROR, 'Reopen an assessed unsuccessful earlier round'). _direction_open already restricts reopening to the two unsuccessful rounds, so nothing else changes. Add to test_a_repeated_round_goal_reopens_that_round_with_changed_evidence (tests/test_research_rounds.py) a case that writes the repeated round with successful=True and asserts invalid_round for a decision that reopens it.

### Finding 2 (major): `research_harness/rounds.py`:250

- [ ] fixed

Summary: The guard 'An unchanged objective has no lineage' (lines 250-252; spec 5.1: next.objective identical to the configured objective has objective_lineage null) has no test in this task or in any later plan task (Task 5 tests only the widened path via open_round(objective=wider, lineage=lineage)). A stated behavior without a test.

Fix: In tests/test_research_rounds.py, RoundDecisionTests, add: same = self.decision_payload(bundle, lineage={'previous_id': self.objective['id'], 'containment': 'Unchanged.'}); self.assert_error('invalid_round', lambda: self.mutate(rounds.record_round, same)). One line inside test_a_decision_binds_the_exact_current_bundle_and_the_current_round is enough.

### Finding 3 (major): `research_harness/rounds.py`:211

- [ ] fixed

Summary: _reopening accepts any assessed round, including a successful one, as the reopened round. Spec 5.1 admits a repeated goal only when it repeats an earlier withdrawn or unsuccessful goal (both end as an assessment with successful: false); a goal that repeats a successful round's goal must stay round_goal_repeated. Today: write_round(..., successful=True) for round-2, then a decision closing 2 that repeats round-2's statement with reopening {round_id: 'round-2'} plus one review evidence item is accepted. The commit message applies the spec sentence only to rejected candidates.

Fix: In _reopening replace lines 210-212 with: admission = records.get('round_admission', {}).get(value['round_id']); assessment = assessment_for(records, admission['id']) if admission is not None else None; if assessment is None or assessment['successful']: raise ResearchError(_ERROR, 'Reopen an earlier round that was assessed unsuccessful'). Add to test_a_repeated_round_goal_reopens_that_round_with_changed_evidence (or a new test): self.write_round(first, 'round-2', successful=True, bundle_digest=bundle['digest']); repeated = self.decision_payload(bundle, closes=2, reopening={'round_id': 'round-2', 'reason': 'The picture changed.', 'evidence': changed}); self.assert_error('invalid_round', lambda: self.mutate(rounds.record_round, repeated)).

### Finding 4 (major): `research_harness/rounds.py`:138

- [ ] fixed

Summary: _carried selects the closing round's carried developments by cycle id (cycles absent from the admission's opening cycle_ids). A next_round item written during the closing round by a re-assessment of a cycle planned in an earlier round is therefore neither required (no carried_development_missing) nor accepted (key not in expected -> invalid_round), so the round gate cannot dispose of it. Spec 1 says 'The round gate must dispose of it' and 5.1 says 'every carried development recorded by any cycle assessment of the closing round'; section 7's `changes`/`contradicted` path returns claims to a cycle, which can be an earlier round's cycle, so the case is reachable. The plan's reference code prescribed the cycle-id filter, so this is a plan-level choice the user may confirm; the spec wording favors selecting by assessment time.

Fix: Select by assessment revision instead of cycle id: expected = {_carried_key(item): item for item in development.carried_developments(records) if latest is None or records['cycle_assessment'][item['assessment_id']]['assessed_revision'] > latest['admitted_revision']} (cycle_assessment records already carry assessed_revision, development.py:903; spec 5.3 and the plan's Task 5 record carry admitted_revision). In tests/rounds_fixtures.py write_round, write 'admitted_revision': self.store.revision + 1 on the admission (drop opening.cycle_ids if nothing else reads it) and let write_round write the assessment separately so a test can: decide, write_round('round-2') without assessment, self.recandidate('late') on cycle-1, bundle2 = self.pin(), write the round-2 assessment on bundle2, then assert_error('carried_development_missing', ...) for a closes=2 decision that omits assessment-late's alternative, and accept it once it is in carried.

### Finding 5 (major): `tests/test_research_rounds.py`:7

- [ ] fixed

Summary: Spec-stated validations of section 5.1/5.2 that rounds.py implements have no test in this task and none in a later plan task: (a) next.number must equal closes + 1 (rounds.py:248); (b) the goal must state the pursued candidate, same direction and normalized statement (rounds.py:163); (c) an unchanged objective with non-null objective_lineage is refused, and a widened objective goes through principles.widen_objective so a wrong previous_id raises objective_locked from record_round (rounds.py:250-254; Task 5 later tests only the happy path via open_round); (d) field_change on a vertical goal is refused (rounds.py:169); (e) round_review_stale when the decision's bundle is no longer the current bundle (second half of rounds.py:315; only the digest half is tested); (f) publication_bundle_missing for a decision recorded before any pin (spec: 'There is no decision without an exact manuscript').

Fix: Add test_the_next_round_is_well_formed to RoundDecisionTests: bundle = self.pin(); for each of the four payload edits assert invalid_round: p['next']['number'] = 3; p['next']['goal']['statement'] = 'Another statement.' (candidate unchanged); p['next']['objective_lineage'] = {'previous_id': self.objective['id'], 'containment': 'x'} with the unchanged objective; p['next']['goal']['field_change'] = {'corpus': 'arxiv', 'primaryCategory': 'math.CO'} on the default vertical goal; then wider = dict(self.objective, id='objective-wide', statement=self.objective['statement'] + ' and beyond') with lineage {'previous_id': 'absent', 'containment': 'x'} -> assert_error('objective_locked', ...). In RoundReviewTests add: decision on bundle one, rewrite draft/abstract.txt, self.pin() again, then assert_error('round_review_stale', lambda: self.mutate(rounds.record_round_review, self.review_payload(decision))). Add test_a_decision_needs_a_pinned_bundle: assert_error('publication_bundle_missing', lambda: self.mutate(rounds.record_round, self.decision_payload({'digest': '0' * 64}))).

