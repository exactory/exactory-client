#!/usr/bin/env python3
"""PostToolUse recorder: the autosave of what a session is doing inside an attack.

A session can end before an attack does, and the harness's record says what was
decided but not what the agent was in the middle of. This hook appends one line
to `attack/<slug>/activity.jsonl` for a successful Write or Edit under that
workspace. It appends one line for a harness command (`exactory-math` or
`attack.py`) that names the attack by its slug or by a path inside it. An
ordinary shell command that names a path under the workspace is appended only
after a harness-command entry of the same host and session. An inspection by
another session, or by a session without a host identity, leaves the file
unchanged. Each entry carries the time, the tool, the target, the host-qualified
`session_id` (or null) and `harness_command`. The log keeps the last 200 entries
of all sessions. `exactory-math status` shows the last entries of the latest
session with a harness-command entry. Any other call, a failed tool result and
any internal error, including a failure of the shell parser, is silent. The hook
exits 0 with no output and appends nothing. It never blocks: it runs after the
tool already did its work, and the PreToolUse guard does not depend on it.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from math_search import (
    SHELL_SEPARATORS, build_shell_lexer, is_path_candidate, iter_simple_commands, normalize, session_id, workspace_for,
)

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
    return workspace_for(path)


def _describe_file_call(tool_input: dict, cwd: Path) -> tuple[Path, str, bool] | None:
    path = _resolve(tool_input.get("file_path", ""), cwd)
    workspace = _find_workspace(path)
    if workspace is None:
        return None
    return workspace, path.relative_to(workspace).as_posix(), False


def _split_shell_command(command: str) -> tuple[list[str], set[int]]:
    lexer = build_shell_lexer(command)
    tokens, separator_positions = [], set()
    while True:
        # Punctuation lookahead belongs to the next token's source span.
        start = lexer.instream.tell() - len(lexer._pushback_chars)
        token = lexer.get_token()
        if token is None:
            break
        end = lexer.instream.tell() - len(lexer._pushback_chars)
        if token in SHELL_SEPARATORS and command[start:end].strip() == token:
            separator_positions.add(len(tokens))
        tokens.append(token)
    return tokens, separator_positions


def _describe_shell_calls(command: str, cwd: Path) -> list[tuple[Path, str, bool]]:
    try:
        tokens, separator_positions = _split_shell_command(command)
    except ValueError:
        return []
    described_calls = {}
    names_harness = any(token.split("/")[-1] in _HARNESS_COMMANDS for token in tokens)
    for start, executable_position, end, _ in iter_simple_commands(tokens, separator_positions):
        call_tokens = tokens[start:end]
        attack_root = cwd / "attack"
        if "--attack-root" in call_tokens:
            index = call_tokens.index("--attack-root")
            if index + 1 >= len(call_tokens):
                continue
            attack_root = _resolve(call_tokens[index + 1], cwd)
        is_harness_call = (executable_position < end
                           and Path(tokens[executable_position]).name in _HARNESS_COMMANDS)
        for index in range(start, end):
            token = tokens[index]
            if not is_path_candidate(token):
                continue
            try:
                if names_harness and (attack_root / token / "problem.json").is_file():
                    workspace = attack_root / token
                    target = ("exactory-math " + token if (attack_root / ".search/tree.json").is_file()
                              else " ".join(tokens[:index + 1]))
                elif "/" in token:
                    target_path = _resolve(token, cwd)
                    workspace = _find_workspace(target_path)
                    if workspace is None:
                        continue
                    if (workspace.parent / ".search/tree.json").is_file():
                        target = target_path.relative_to(workspace).as_posix()
                    else:
                        target = command[:_TARGET_LENGTH]
                else:
                    continue
            except OSError:
                continue
            is_workspace_harness = is_harness_call and index >= executable_position
            if workspace not in described_calls:
                described_calls[workspace] = (target, is_workspace_harness)
            elif is_workspace_harness:
                described_calls[workspace] = (described_calls[workspace][0], True)
    return [(workspace, target, is_harness_call)
            for workspace, (target, is_harness_call) in described_calls.items()]


def _has_harness_session(lines: list[str], session_identity: str | None) -> bool:
    if session_identity is None:
        return False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (isinstance(entry, dict) and entry.get("session_id") == session_identity
                and entry.get("harness_command") is True):
            return True
    return False


def _append(workspace: Path, tool_name: str, target: str, session_identity: str | None,
            is_harness_call: bool) -> None:
    log_path = workspace / _LOG_NAME
    lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []
    if tool_name == "Bash" and not is_harness_call and not _has_harness_session(lines, session_identity):
        return
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": tool_name,
        "target": target,
        "session_id": session_identity,
        "harness_command": is_harness_call,
    }
    lines.append(json.dumps(entry))
    log_path.write_text("".join(line + "\n" for line in lines[-_KEPT_ENTRIES:]), encoding="utf-8")


def main() -> None:
    payload = normalize(json.load(sys.stdin))
    response = payload.get("tool_response")
    if isinstance(response, dict) and (response.get("success") is False or response.get("isError") is True
            or response.get("exit_code") not in (None, 0)):
        sys.exit(0)
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or ".").resolve()
    if tool_name in ("Write", "Edit"):
        described = _describe_file_call(tool_input, cwd)
        described_calls = [described] if described is not None else []
    elif tool_name == "Bash":
        described_calls = _describe_shell_calls(tool_input.get("command", ""), cwd)
    else:
        described_calls = []
    for workspace, target, is_harness_call in described_calls:
        _append(workspace, tool_name, target, session_id(payload), is_harness_call)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
