# Stop-cap pause regression

The user requested fixes for the broken shared workflow behavior in Claude Code and Codex. The journey tested here is an automatic run reaching its safety cap, asking the user whether to continue, returning control, and receiving a new budget after the user resumes. The same defect affected math attacks and AI Science autopilot.

## Evidence

The RED checkpoint is `694822b`. No runtime change preceded it. The new tests invoked each real hook directly for Claude Code and through `codex/hook.py` for Codex.

Every shell command that invokes an Exactory CLI began with:

```sh
export PATH=/Users/ryshiro/.codex/plugins/cache/exactory-ai/exactory/0.33.0/bin:"$PATH"
```

RED and GREEN used this exact command:

```sh
/opt/homebrew/bin/python3.13 -m unittest discover -s /tmp/exactory-port-fixes-20260904/stop/tests -p test_stop_cap.py -v
```

RED: four tests failed because the Stop immediately after the cap summary returned `decision: block` and began advance 1/2. GREEN: four tests passed in 166.549 seconds.

Existing continuation tests ran from the fix worktree:

```sh
PYTHONPATH=tests /opt/homebrew/bin/python3.13 -m unittest test_hooks.TestContinueAutopilot test_math_hooks.TestContinueAttack -v
```

All 14 tests passed in 78.088 seconds. `git diff --check` also passed.

## Guarantees and limits

| Guarantee | Evidence |
| --- | --- |
| Ordinary stops accumulate against the cap, including stops with `stop_hook_active: false`. | Four `test_stop_cap.py` lifecycle cases |
| The cap requests one summary, then repeated automatic stops return control. | Four lifecycle cases |
| Missing continuation metadata does not restart a paused run. | Four lifecycle cases |
| A new user turn starts a fresh budget and reaches a second working cap. | Four lifecycle cases |
| Completed, parked, disabled, and absent workspaces preserve their prior stop behavior. | Existing continuation tests |

The paused state stays in the existing integer counter file. SessionStart handlers do not reset it, so a compaction does not reset the cap. Restart uses the documented `stop_hook_active: false` signal from a new user turn or the first turn after session resume. No separate SessionStart behavior changed.

The tests cover both outcomes of the new pause branch with actual subprocesses and files. No coverage percentage was measured in this focused run. These tests validate handler behavior through both plugin entrypoints; they do not launch either model host. Root review and combined validation remain separate.

Raw logs are `/tmp/exactory-port-fixes-20260904/stop-red.log`, `stop-green.log`, and `stop-existing.log`. Preserve this RED/GREEN evidence if the checkpoint commits are squashed.
