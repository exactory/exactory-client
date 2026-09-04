# Codex support: test evidence

The journeys came from the implementation request: install the same Exactory
workflows in Codex, preserve Claude Code behavior, test both hosts, and push
the tested changes. No external plan file was used.

## Design and regression boundary

Codex uses `.codex-plugin/plugin.json`, thin skill entrypoints, and an event
adapter under `codex/`. The adapter runs the existing hook scripts. The
Claude Code manifest, `skills/`, `hooks/`, and `bin/` are byte-for-byte unchanged
from `ffd165d`. The companion marketplace keeps its Claude catalog unchanged
from `601f1f2` and adds `.agents/plugins/marketplace.json`.

`exactory-verifier` is retired; its workflow is already part of `exactory`.
The existing Claude marketplace migration remains in place.

## TDD checkpoints

| Behavior | RED evidence | GREEN evidence |
|---|---|---|
| Discover Codex skills and run the existing guards | `3230e43`: tests fail because the Codex manifest and adapter are absent | `6ba8bfc`: all initial 14 Codex tests pass |
| Keep patch checks inside recognized attack workspaces; retain file advisories | `2777061`: the batch guard rejects an unrelated `units/` directory | Restrict the guard to an attack with `problem.json`; final 26 tests pass |
| Follow host path parsing; preserve human authorship on rename; record deletions | `ea83e01`: review regressions fail before the adapter fix | Trim headers according to the Codex parser, map moves to edits, retain deletion activity; final 26 tests pass |
| Publish a separate Codex catalog | Marketplace `b8cabb3`: catalog test fails before implementation | Marketplace `114a8a5`: both catalog tests pass |

The checkpoint commits remain in the feature branch history. An independent
review found the whitespace, rename, and deletion issues; regression tests
reproduced them before the fixes. This report preserves the evidence if the
branch is later squash-merged.

## Automated checks

| Guarantee | Command | Result |
|---|---|---|
| Existing client behavior has a passing baseline | `python3 -m unittest discover -s tests` before implementation | 338 tests, OK |
| Existing and new tests pass together after the final adapter fixes | `python3 -m unittest discover -s tests -v` | 364 tests, OK |
| Existing math harness has a passing baseline | `python3 -m unittest discover -s skills/math-solver/harness/tests -t skills/math-solver/harness` | 230 tests, OK |
| File guards check add, update, delete, move, multiple files, absolute paths, spaces, and malformed input | `python3 -m unittest discover -s tests -p test_codex.py` | 26 tests, OK |
| Citation, unit-flow, authorship, activity, stop, and resume handlers still run through the adapter | Same Codex test command; subprocesses execute the actual shared handlers | PASS |
| Generated skill descriptions and registrations remain current | `python3 codex/generate.py --check` | PASS |
| Both marketplace catalogs keep the same plugin source and the Claude migration | In the marketplace repository: `python3 -m unittest discover -s tests -v` | 2 tests, OK |
| Python sources compile | `python3 -m compileall -q hooks codex tests skills/math-solver/harness` | PASS |
| Claude accepts both plugin and marketplace manifests | `claude plugin validate <repository> --json --strict` for both repositories | Valid, no warnings |

## Host integration checks

Host versions: Codex CLI **0.153.3**, Claude Code **2.1.261**.

```sh
python3 tests/smoke_codex.py --live --output /tmp/exactory-codex-host.json
python3 tests/smoke_claude.py --output /tmp/exactory-claude-host.jsonl
```

Both passed. Each test stages this plugin in an isolated temporary configuration
and workspace. Neither calls the Exactory API or submits a paper.

- **Codex:** the real app server installs the plugin and discovers 14 enabled
  skills and 14 enabled hooks, with no load errors. A live authenticated model
  reads a skill and its runtime guide, runs the installed CLI, receives a real
  hook denial for an `apply_patch` edit to a protected record, and creates a
  normal file. The assertions inspect files and the host session transcript.
- **Claude Code:** the real host loads a shared skill, runs the CLI, denies a
  protected `Write`, and allows a normal write. A localhost API scripts only
  the model responses; the host, tools, CLI, and hooks are real. This is a host
  integration check, not a live Anthropic model evaluation.

The Codex live check needs an existing login and makes a model request. It
trusts only the staged test hooks through the CLI trust override. Normal
installations must review and trust the hooks in `/hooks`. These host checks
run explicitly; ordinary CI does not need host credentials.

## Coverage and limits

Coverage uses `codex/coverage.ini`, including branch and subprocess tracing:

```sh
python3 -m coverage run --rcfile=codex/coverage.ini -m unittest discover -s tests -p test_codex.py -v
python3 -m coverage combine --rcfile=codex/coverage.ini
python3 -m coverage report --rcfile=codex/coverage.ini
```

CI uses coverage 7.16.0 and requires at least 80%. The instrumented 26-test
run passed with **94%** combined statement and branch coverage (141 statements,
64 branches). An earlier instrumented run exceeded the original short child
timeouts; the test subprocess timeout is now 60 seconds. The later run is the
coverage evidence, not the timed-out run.

The local plugin-creator validation helper predates custom skill paths and
the manifest `hooks` field, and rejects those current host features. The
actual Codex app-server installation above validates this package against the
host instead. The contracts are documented in the
[OpenAI plugin reference](https://developers.openai.com/plugins/build/plugins)
and [hook reference](https://learn.chatgpt.com/docs/hooks).

These checks do not evaluate an entire research project, live publication,
or every host/model version. Workflow stages needing independent agents
still require agent tools in the running host, as the runtime guide states.
