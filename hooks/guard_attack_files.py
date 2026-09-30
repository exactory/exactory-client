#!/usr/bin/env python3
"""PreToolUse gate: the files the math-solver harness writes are written by it alone.

An attack workspace is `attack/<slug>/`, laid out by `exactory-math init`. Eight
of its files are the record the harness and the hooks write themselves:
`journal.jsonl` (`journal add`), `openings.json` (`plan`),
`parent.json` (`init --from`), `tasks.json` (`task`), `activity.jsonl`
(the activity hook), `deterministic/<step>/result.json` (`verify`),
`units/<n>/check-unit.json` (`check-unit`), and `units/FINISHED.json`
(`finish`).

The hook first runs `math_search.guard`. In a managed search tree (a workspace
whose parent holds `.search/tree.json`), that check denies a write to anything
the search controller owns, the payload of another tool that names the tree,
and any operation whose check fails with an error. The hook then denies a Write
or an Edit to any of the eight files. It also denies a Bash command that writes,
that is, one that holds a word that begins with `>`, or `tee`, `cp`, `mv`,
`rm`, `truncate` or `dd`, or `sed` with `-i`, when one of its words, with a
leading `>` removed, is a path that contains a `/` and resolves, from the
payload's working directory, to one of the eight files. The check reads words,
not write targets: it denies a writing command that only reads one of the
files, and it misses a file named without a `/`, a write after a `cd`, a
redirection whose word does not begin with `>` (such as `2>`, `&>` or
`x>file`), `>|file` without a space, and `dd of=`. A command that does not
write is untouched. Any other file, any other tool, and any error of the hook's
own checks are silent: exit 0, no output.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from math_search import guard, normalize, workspace_for

# Each pair holds a compiled pattern of a path within the workspace and the
# instruction that the denial of a write to that path gives: the harness
# command that writes the file, or, for a file that a hook writes, the hook
# that writes it.
_OWNED_FILES = (
    (re.compile(r"^journal\.jsonl$"), "exactory-math journal add <slug> --json '<move>'"),
    (re.compile(r"^openings\.json$"), "exactory-math plan <slug>"),
    (re.compile(r"^parent\.json$"), "exactory-math init <slug> --from <parent>, when the workspace is created"),
    (re.compile(r"^tasks\.json$"), "exactory-math task add <slug> <text>, or task done <slug> <id>"),
    (re.compile(r"^activity\.jsonl$"), "nothing by hand: the plugin's activity hook appends it after every tool call"),
    (re.compile(r"^deterministic/[^/]+/result\.json$"), "exactory-math verify lean|certificate <slug> <step-dir>"),
    (re.compile(r"^units/\d+/check-unit\.json$"), "exactory-math check-unit <slug> <n>"),
    (re.compile(r"^units/FINISHED\.json$"), "exactory-math finish <slug>"),
)
_WRITING_TOKENS = {">", ">>", "tee", "cp", "mv", "rm", "truncate", "dd"}


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _resolve(raw: str, cwd: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _find_workspace(path: Path) -> Path | None:
    """The attack workspace holding `path`: an ancestor that carries the `problem.json` `init` wrote, inside a
    directory named `attack` or inside a managed search tree (a parent that holds `.search/tree.json`)."""
    return workspace_for(path)


def _find_owner(path: Path) -> tuple[str, str] | None:
    """(the path within the workspace, the instruction that its denial gives), or None when the solver owns the file."""
    workspace = _find_workspace(path)
    if workspace is None:
        return None
    relative = path.relative_to(workspace).as_posix()
    for path_re, instruction in _OWNED_FILES:
        if path_re.match(relative):
            return relative, instruction.replace("<slug>", workspace.name)
    return None


def _find_shell_write_target(command: str, cwd: Path) -> tuple[str, str] | None:
    """The owned file a shell command writes to, when it carries a writing construct."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    is_writing = any(token in _WRITING_TOKENS or token.startswith(">") for token in tokens) or (
        "sed" in tokens and any(token.startswith("-i") for token in tokens)
    )
    if not is_writing:
        return None
    for token in tokens:
        candidate = token.lstrip(">")
        if not candidate or candidate in _WRITING_TOKENS or "/" not in candidate:
            continue
        owner = _find_owner(_resolve(candidate, cwd))
        if owner is not None:
            return owner
    return None


def main() -> None:
    payload = normalize(json.load(sys.stdin))
    try:
        reason = guard(payload)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _deny("[math-solver] Recovery required before this managed operation: " + str(error))
    if reason is not None:
        _deny("[math-solver] " + reason)
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or ".").resolve()
    if tool_name in ("Write", "Edit"):
        owner = _find_owner(_resolve(tool_input.get("file_path", ""), cwd))
    elif tool_name == "Bash":
        owner = _find_shell_write_target(tool_input.get("command", ""), cwd)
    else:
        owner = None
    if owner is None:
        sys.exit(0)
    relative, instruction = owner
    _deny(
        f"[math-solver] {relative} is written by the harness only, which validates"
        f" what it puts there. Run the command instead of editing the file:\n  {instruction}"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
