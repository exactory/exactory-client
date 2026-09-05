# Marketplace parity fixes

Verified on 2026-09-04 (America/Vancouver). The candidate version is 0.33.1.
This report records local validation, not a published release.

## Changes and regression evidence

The shared math and AI Science Stop handlers previously restarted their
counter immediately after requesting a cap summary. Both now retain a paused
state, allow the summary to finish, and restart after a new user turn.
The tests exercise Claude's shared entrypoints and Codex's adapter with real
subprocesses. Four new lifecycle tests failed before the fix and pass after
it. The integrated RED/GREEN checkpoints are `d4d65c5` and `d346733`.
See [the focused Stop report](stop-caps.tdd.md).

The Codex patch adapter previously rejected boundary markers containing
whitespace that the host parser accepts. Two new regression tests reproduced
the rejection. The fix trims the boundary markers while preserving protected
path checks and rejection of malformed envelopes. RED/GREEN checkpoints are
`a738c9d` and `f737f56`.

The release-version assertion failed against 0.33.0 before both manifests
were updated to 0.33.1. RED/GREEN checkpoints are `ff97cdc` and `413f7c9`.
All six manifest and layout tests then passed, with no skips. The guides now
state Python 3.9+, the shared workflow's authorization rules, and how to
request reviewers with fresh contexts.

The 14 shared skills and seven CLI implementations are unchanged by these
fixes. The candidate's Codex generator consistency check passes.

## Combined validation

Commands ran in the candidate worktree. Every shell using an Exactory CLI
first exported the installed plugin's `bin` directory onto `PATH`. Tests use
the candidate paths explicitly.

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 370 passed, no skips, 814.469 seconds |
| Codex suite under `codex/coverage.ini` | 28 passed; 94% statement/branch coverage |
| Shared Stop suites with subprocess coverage | 18 passed; 93% statement/branch coverage |
| Final manifest/deadline suite | 7 passed, including the new timeout regression |
| `python codex/generate.py --check` | Passed |
| Python compilation, JSON parsing, `git diff --check` | Passed |
| Candidate Claude Code integration | Skill, CLI, protected write denial, ordinary write passed |
| Candidate Codex integration | 14 enabled skills, 14 hooks, CLI, protected write denial, ordinary write passed |

The full suite includes the focused regression tests; the coverage runs do
not add to the 370 distinct tests. Shared Stop coverage is 95% for math and
91% for AI Science. The unchanged math harness had already passed all 230
tests, including its real Lean smoke test, during the preceding audit.

The candidate Claude test uses the real host, plugin loader, CLI, tools and
hooks with a scripted localhost model API. The candidate Codex test uses the
real host and a live authenticated model. Both use isolated temporary
configurations and a locally staged candidate. They do not establish that
0.33.1 is already available from the public marketplace.

An independent code reviewer found no important or critical issues in the
initial client fixes. Additional real-host testing then exposed an existing
timeout mismatch: Claude gave math Stop 15 seconds while its status lookup
could take 20 seconds. A controlled 16-second status delay caused the host
to stop before receiving its continuation. The math Stop registration now
allows 30 seconds, matching Codex. RED/GREEN checkpoints are `2082b41` and
`b99fe87`. The new deadline contract test failed with `15 not greater than
20`; all seven manifest tests pass after the change. The generator check
also passes, since Codex already used 30 seconds.
The identical 16-second delayed-status host test then passed on `b99fe87`:
four API requests delivered both advances and one cap summary, and the host
finished with counter `-1` in 20.127 seconds. The independent reviewer found
no remaining important or critical issues.

The 370-test full run and coverage results above precede this final one-line
timeout configuration change. The additional regression and focused checks
validate that change without repeating unrelated suites.

Real Claude host tests also demonstrated two advances, one cap summary, and
a successful Stop with the counter paused at `-1`. A separate invocation
starting from `-1` completed the same cycle. A minor clarification about
restarting only after a cap pause was incorporated in the release note.

## Public distribution and limits

The preceding audit separately installed the public `exactory/marketplace`
catalog in fresh Claude Code and Codex configurations. Both fetched public
client main `8bdd9a8977a619b680ab793dd7d56cd53488cdb7`, reported 0.33.0,
and matched all 192 tracked files. Public installation and the exercised
host integrations passed.

The `exactory--v0.33.0` tag points to
`ffd165d77d943c0db8ae3f07aabedf4826b9e723`, which predates the Codex
entrypoints. No GitHub Release object existed at audit time. Thus public
main and the tag had different contents under the same version number.
The 0.33.1 manifests and release note prepare a distinct corrective version;
publishing it and verifying a public upgrade are separate remaining steps.

Codex requires the user to review and trust installed hooks in `/hooks`.
The isolated live test used the documented trust override after source
review. It did not change the user's normal configuration.

Claude Code ends a turn after eight consecutive Stop blocks. The shared
plugin caps therefore do not guarantee identical uninterrupted run lengths
between hosts. The handlers implement the same pause/resume rule, subject
to each host's limits. References: [Claude Code Stop
contract](https://code.claude.com/docs/en/hooks#stop), [Codex hooks
contract](https://learn.chatgpt.com/docs/hooks).

This validation covers distribution, skill availability, shared source,
deterministic behavior and exercised host integrations. It does not claim
identical mathematical results from different models or test every complete
research and publication workflow.

## Raw local evidence

The audit directory is `/tmp/exactory-port-audit-20260904/`:

- `AUDIT.md` describes the public 0.33.0 audit before fixes.
- `client-fixed-tests.log`, `codex-fixed-coverage.log`, and
  `shared-hooks-coverage.log` record the candidate automated checks.
- `claude-fixed-host.jsonl`, `codex-fixed-host.json`, and
  `codex-fixed-host.live.jsonl` record candidate host checks.
- `claude-cap-candidate-findings.md` records the real-host cap and delayed
  status RED/GREEN checks; `stop-deadline-{red,green}.log` records the
  deadline regression.
- `harness-tests.log` records the unchanged 230-test harness result.
- `public-content-parity.json` and `host-summary.json` record the separate
  public installation audit.
