#!/usr/bin/env python3
"""PreToolUse gate: a stage closes only after its key decision is on the record.

Exactory AI Science keeps an append-only decision log so a human can later
reconstruct how a result was produced. This hook blocks an
`exactory-lab state set` that closes a stage (`--status done`, or advancing to
`--stage complete`) until `.exactory/decisions.jsonl` carries at least one
decision for that stage. Everything else is neutral: exit 0, no output.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

_STUDY_STATE_PATH = Path(".exactory") / "study.json"
_DECISIONS_PATH = Path(".exactory") / "decisions.jsonl"


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _find_workspace(start_dir: Path) -> Path | None:
    for directory in (start_dir, *start_dir.parents):
        if (directory / _STUDY_STATE_PATH).is_file():
            return directory
    return None


def _flag_value(tokens: list[str], flag: str) -> str | None:
    for token, next_token in zip(tokens, tokens[1:]):
        if token == flag:
            return next_token
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command", "")
    try:
        tokens = shlex.split(command)
    except ValueError:
        sys.exit(0)

    is_state_set = any(
        token == "exactory-lab" and "state" in tokens[index + 1:]
        and "set" in tokens[index + 1:]
        for index, token in enumerate(tokens)
    )
    if not is_state_set:
        sys.exit(0)
    target_stage = _flag_value(tokens, "--stage")
    closes_stage = _flag_value(tokens, "--status") == "done" or target_stage == "complete"
    if not closes_stage:
        sys.exit(0)

    workspace = _find_workspace(Path(payload.get("cwd") or ".").resolve())
    if workspace is None:
        sys.exit(0)
    if target_stage is None or target_stage == "complete":
        try:
            state = json.loads((workspace / _STUDY_STATE_PATH).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sys.exit(0)
        stage = state.get("stage", "")
    else:
        stage = target_stage

    decisions_path = workspace / _DECISIONS_PATH
    decision_count = 0
    if decisions_path.is_file():
        for line in decisions_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("stage") == stage:
                decision_count += 1
    if decision_count == 0:
        _deny(
            f"[decision-log] Stage '{stage}' has no recorded decision, so it"
            " cannot be closed. Log the key decision first:\n"
            "  exactory-lab decide --decision \"<what>\" --why \"<why>\""
            " [--evidence \"<file>\"]\n"
            "The append-only log is how a human later reconstructs how the"
            " result was produced. Then re-run the set command."
        )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
