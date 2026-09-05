# Exactory in Codex

The Codex entrypoints use the same workflows, commands, and checks as Claude Code.
The shared instructions stay under `skills/`. The commands stay under `bin/`.

Use Python 3.9 or later for the commands. The improvement loop also needs Git.
Paper workflows that compile a PDF need a LaTeX compiler.

## Run a skill

1. Read the shared `SKILL.md` linked from the Codex entrypoint.
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

## Tool conventions

| Shared instruction | Codex action |
|---|---|
| `/exactory:<name>` | Read and execute the corresponding installed Exactory skill |
| Run a shell command | Use the shell tool available in this session, with the command path set above |
| Write or edit a file | Use `apply_patch`; the Codex hooks check each file in the patch |
| Ask the user | Use the session's question tool or a direct question |
| Independent reviewers | Use separate agents with fresh contexts and only the prescribed review material |

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
Invoking a study or deposit authorizes its completion, including production
steps. Pause at the stops the user names and when required credentials are absent.

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
