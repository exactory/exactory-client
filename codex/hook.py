#!/usr/bin/env python3
"""Translate Codex patch events for the unchanged Claude Code hook scripts."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import runpy
import sys

SHARED_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def parse_patch_targets(command: str) -> list[tuple[str, str]]:
    """Read file headers, including both ends of a move, without applying edits."""
    lines = command.strip().splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        raise ValueError("Invalid apply_patch envelope")
    targets = []
    in_update = False
    for raw_line in lines[1:-1]:
        # Codex trims both ends between files, but only the end in an Update
        # hunk: a leading space there denotes file content, not a header.
        line = raw_line.rstrip() if in_update else raw_line.strip()
        for prefix, operation in (
            ("*** Add File: ", "Write"),
            ("*** Update File: ", "Edit"),
            ("*** Delete File: ", "Delete"),
            ("*** Move to: ", "Edit"),
        ):
            if line == prefix.rstrip():
                raise ValueError("Empty apply_patch path")
            if line.startswith(prefix):
                path = line[len(prefix):]
                if not path.strip():
                    raise ValueError("Empty apply_patch path")
                targets.append((path, operation))
                in_update = operation == "Edit"
                break
    if not targets:
        raise ValueError("The patch has no file targets")
    return targets


def run_shared_hook(script: Path, payload: dict) -> dict | None:
    output = io.StringIO()
    original_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps(payload))
        with contextlib.redirect_stdout(output):
            try:
                runpy.run_path(str(script), run_name="__main__")
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    raise RuntimeError(f"{script.name} exited with {exc.code}") from exc
    finally:
        sys.stdin = original_stdin
    return json.loads(output.getvalue()) if output.getvalue().strip() else None


def build_file_payloads(payload: dict, script_name: str) -> list[dict]:
    targets = parse_patch_targets(payload["tool_input"]["command"])
    cwd = Path(payload.get("cwd") or ".")
    resolved = {(cwd / path).resolve() for path, _ in targets}
    if script_name == "enforce_unit_flow.py":
        for path in resolved:
            workspace = path.parent.parent.parent
            is_attack_unit = (
                path.parent.name.isdigit() and path.parent.parent.name == "units"
                and workspace.parent.name == "attack"
                and (workspace / "problem.json").is_file()
            )
            if (is_attack_unit and path.name in ("draft.md", "evaluation.md")
                    and path.parent / "unit.json" in resolved):
                raise ValueError(
                    "Run check-unit after editing unit.json, then write the draft in a separate patch"
                )
    is_pre = payload.get("hook_event_name") == "PreToolUse"
    return [
        {**payload, "tool_name": "Write" if operation == "Write" else "Edit",
         "tool_input": {"file_path": path}}
        for path, operation in targets
        if is_pre or operation != "Delete" or script_name == "record_attack_activity.py"
    ]


def main() -> None:
    script_name = sys.argv[1]
    if script_name not in {path.name for path in SHARED_HOOKS.glob("*.py")}:
        raise ValueError("Unknown shared hook")
    payload = json.load(sys.stdin)
    try:
        inputs = (build_file_payloads(payload, script_name)
                  if payload.get("tool_name") == "apply_patch" else [payload])
    except (KeyError, TypeError, ValueError) as exc:
        if payload.get("hook_event_name") != "PreToolUse":
            raise
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": f"[exactory] Cannot check this patch: {exc}",
        }}))
        return
    contexts = []
    for adapted in inputs:
        result = run_shared_hook(SHARED_HOOKS / script_name, adapted)
        if result is None:
            continue
        specific = result.get("hookSpecificOutput", {})
        if specific.get("permissionDecision") == "deny" or result.get("decision") == "block":
            print(json.dumps(result))
            return
        if specific.get("additionalContext"):
            contexts.append(specific["additionalContext"])
    if contexts:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": payload["hook_event_name"],
            "additionalContext": "\n\n".join(contexts),
        }}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, IndexError) as exc:
        print(f"[exactory] Codex hook failed: {exc}", file=sys.stderr)
        sys.exit(2)
