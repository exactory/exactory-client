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
reading, and literature preparation still precede execution. In managed author
research, follow the [scientific decision lifecycle](../docs/research-decisions.md):
record intent and a proposed-content dossier, obtain two independent bars before
the slate, and assess both objective and approach before target commitment.
Commit exact approved content with its source/preparation delta. Investigations
use bounded question/resource tranches and the existing prospective cycle
admission and exact run binding.

Before writing, each of two assessors evaluates support and realized consequence
in one response. The same canonical decision governs new commitments, manuscript
development, justified limited delivery and post-measurement rounds. Old `round`,
`round-review` and `round-admit` commands adapt it without a second strategic
approval. Blind reviews of the exact manuscript remain separate. Preserve actual
publication authority and the full objective's remaining obligations; an
unconditional named endpoint does not require another permission question because
the scientific bar is unmet. Explicit quality conditions remain binding.
Use the actual payloads from `exactory-research example OPERATION`
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
| Independent reviewers | Use canonical assignments on an actually verified route/version with only the permitted context and evidence |
| Reader or screener agents | Use one agent per `batches` file; each returns one notes file and one coordinator records it with `read-batch` or `screen-batch` |

Before an independent review, check the configured delivery route and actual
context evidence. `fork_turns: "none"` avoids intentional conversation forking
where supported, but does not by itself exclude automatic memory, project
instructions, hooks or later tool access. A fresh agent or a clean packet alone
cannot be marked isolated. Use canonical prompts, exact packet hashes, actual
invocation provenance, excluded synthetic-token tests and an allowed-input
positive control for the specific route/version. Only a verified assignment
supplies independent approval; preserve unverified, failed and contaminated
returns and repair affected current findings before relying on them.

Use a different model family when configured and authorized, recording a
common-family limitation otherwise. An unavailable route is an operational
dependency, not a negative scientific rating or permission to self-review.
Continue authorized independent work while repairing it. Native math,
standalone evaluation and verification retain their own decision prerequisites;
shared reviewer-context requirements apply wherever independence is claimed.

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
