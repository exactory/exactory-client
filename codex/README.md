# Exactory in Codex

The Codex entrypoints use the same workflows, commands, and checks as Claude Code.
The shared instructions stay under `skills/`. The commands stay under `bin/`.

Read the [common research constitution](../RESEARCH_CONSTITUTION.md) and
[managed workflow](../docs/research-workflow.md). Every Codex entrypoint links the
same policy and its shared workflow. System, host, and user instructions govern;
administrative account/status operations do not initialize research unnecessarily.

Use Python 3.9 or later for the commands. The improvement loop also needs Git.
Paper workflows that compile a PDF need a LaTeX compiler.

## Run a skill

1. Read the common constitution and shared `SKILL.md` linked from the entrypoint.
2. Resolve its relative document links from that shared skill's directory.
3. Run its commands from the user's workspace.

The plugin root is the parent of this `codex/` directory. Use its absolute path
to add `bin/` to `PATH` at the start of **each** shell call that uses an Exactory
command. Quote paths that contain spaces. An export in one shell does not set
the environment of the next shell.

```sh
export PATH="/absolute/path/to/installed/exactory/bin:$PATH"
exactory whoami
```

Use the path of this installed copy. Do not assume a fixed cache location.
`exactory-math skill-dir` returns the shared math-solver directory.

Use `exactory-research status --summary` and `next --summary` on resume, and the
`obligations` page for one code at a time. Cohort collection, actual
reading, and the installed literature-review stage precede ideation. The complete
objective and current synthesis precede prospective cycle admission and exact run
binding. Independent research readiness review of the actual candidate precedes
writing; two blind reviews of the exact manuscript are a separate publication
requirement. Use the actual payloads from `exactory-research example OPERATION`
and the [CLI reference](../docs/research-cli.md). Preserve failed branches,
checkpoints, source changes, uncommitted user work, and all resource costs.

For a fresh math-solver objective, use the shared workflow's reviewed controller
path: `search init`, current common preparation, schema-3 `propose` with the
actual exported foundation, independent `review`, and `admit`. Follow the
[native foundation delivery](../docs/research-cli.md#native-math-preparation)
for new proposals and reviewed amendments to historical nodes. Every mutating
controller command uses the current expected revision and a unique request ID;
`search status` and `search next` are read-only. Do not create a managed child with
legacy `init --from` after admission. Reserve moves before execution, and use the
controlled frozen run or native verification route described in the shared
`SEARCH.md`. A local finish, literature hit, run result, or journal close is not
root acceptance.

After new research evidence, `search next` can require `reassess_strategies`.
Use the read-only `search strategy-context` output to review all current methods
and retained failures, obtain an independent assessment review with a fresh
context, and submit `search reassess` before starting more research. The exact
schema and recovery priorities are in the shared
`SEARCH.md#mandatory-strategy-reassessment`. Old execution reservations do not
authorize repeated producers after progress, and reassessment does not reset
budgets or grant proof credit.

The current early-release interface has durable pause and controlled deadlines,
but no immediate owned-job cancellation, typed local-package/literature receipts,
or automatic parent progress report. Do not invent those commands or infer that a
status read resumes work. Hooks enforce only operations the host reports; nested
unreported wrappers and internal solver calls remain outside that boundary.

## Tool conventions

| Shared instruction | Codex action |
|---|---|
| `/exactory:<name>` | Read and execute the corresponding installed Exactory skill |
| Run a shell command | Use the shell tool available in this session, with the command path set above |
| Write or edit a file | Use `apply_patch`; the Codex hooks check each file in the patch |
| Ask the user | Use the session's question tool or a direct question |
| Independent reviewers | Use separate agents with fresh contexts and only the prescribed review material |
| Reader or screener agents | Use one agent per `batches` file; each returns one notes file and one coordinator records it with `read-batch` or `screen-batch` |

When a workflow requires independent reviewers, confirm that the session has
agent tools. If those tools are unavailable, report that requirement before
the review stage. Do not substitute a self-review for an independent review.
When `spawn_agent` supports `fork_turns`, set `fork_turns: "none"` for those
reviewers and supply only the prescribed review material.

## Enable the checks

Install the plugin, then open `/hooks` in Codex. Review and trust the Exactory
hooks. Codex skips hooks that need trust; an enabled plugin alone does not
activate them. Review changed hooks again after an update.

The Codex manifest selects `codex/hooks.json`. It does not load the Claude Code
hook configuration. The adapter calls the original checks after it translates
Codex file events. Shell, session-start, and stop events use the shared handlers.

The CLI checks on citation integrity and submission also run within the commands.
Hook checks cover supported tool calls; the host's sandbox and permissions still
control access. Preserve the shared workflow's authorization and pacing rules.
An end-to-end study or deposit request authorizes its requested stages within
system, host, and user constraints. Preserve named stops, resource limits, and
pending evidence or independent-review conditions as well as missing credentials.

## Maintain the Codex entrypoints

After a shared skill description or hook registration changes, run:

```sh
python3 codex/generate.py
python3 codex/generate.py --check
```

This updates only the Codex entrypoints and hook configuration. It does not
change the shared skill bodies, commands, or Claude Code configuration.

The host contracts are documented in the [OpenAI plugin reference](https://developers.openai.com/plugins/build/plugins)
and [hook reference](https://learn.chatgpt.com/docs/hooks).
