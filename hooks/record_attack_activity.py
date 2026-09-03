#!/usr/bin/env python3
"""PostToolUse recorder: the autosave of what a session is doing inside an attack.

A session can end before an attack does, and the harness's record says what was
decided but not what the agent was in the middle of. This hook appends one line
to `attack/<slug>/activity.jsonl` for every Write, Edit, or Bash call that
touched that workspace: a file under it, a harness command naming its slug, or
a shell command naming a path under it. `exactory-math status` reports the last
entries, so a resumed session sees where the previous one stopped. The log keeps
the last 200 entries. Any other call, and any internal error, is silent: exit 0,
no output. It never blocks: it runs after the tool already did its work.
"""

from __future__ import annotations

import datetime
import json
import shlex
import sys
from pathlib import Path

_LOG_NAME = "activity.jsonl"
_KEPT_ENTRIES = 200
_TARGET_LENGTH = 160
_HARNESS_COMMANDS = ("exactory-math", "attack.py")


def _resolve(raw: str, cwd: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _find_workspace(path: Path) -> Path | None:
    for directory in (path, *path.parents):
        if directory.parent.name == "attack" and (directory / "problem.json").is_file():
            return directory
    return None


def _describe_file_call(tool_input: dict, cwd: Path) -> tuple[Path, str] | None:
    path = _resolve(tool_input.get("file_path", ""), cwd)
    workspace = _find_workspace(path)
    if workspace is None:
        return None
    return workspace, path.relative_to(workspace).as_posix()


def _describe_shell_call(command: str, cwd: Path) -> tuple[Path, str] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    attack_root = cwd / "attack"
    if "--attack-root" in tokens:
        attack_root = _resolve(tokens[tokens.index("--attack-root") + 1], cwd)
    is_harness_call = any(token.split("/")[-1] in _HARNESS_COMMANDS for token in tokens)
    for index, token in enumerate(tokens):
        if is_harness_call and (attack_root / token / "problem.json").is_file():
            return attack_root / token, " ".join(tokens[: index + 1])
        if "/" in token:
            workspace = _find_workspace(_resolve(token, cwd))
            if workspace is not None:
                return workspace, command[:_TARGET_LENGTH]
    return None


def _append(workspace: Path, tool_name: str, target: str) -> None:
    log_path = workspace / _LOG_NAME
    lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": tool_name,
        "target": target,
    }
    lines.append(json.dumps(entry))
    log_path.write_text("".join(line + "\n" for line in lines[-_KEPT_ENTRIES:]), encoding="utf-8")


def main() -> None:
    payload = json.load(sys.stdin)
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or ".").resolve()
    if tool_name in ("Write", "Edit"):
        described = _describe_file_call(tool_input, cwd)
    elif tool_name == "Bash":
        described = _describe_shell_call(tool_input.get("command", ""), cwd)
    else:
        described = None
    if described is None:
        sys.exit(0)
    workspace, target = described
    _append(workspace, tool_name, target)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
