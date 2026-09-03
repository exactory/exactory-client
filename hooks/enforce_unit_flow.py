#!/usr/bin/env python3
"""PreToolUse gate: a unit is written after the cash-out started, and drafted after its check.

In an attack workspace (`attack/<slug>/`), the cash-out starts when
`exactory-math stall` writes `units/INVENTORY.md`, and `stall` refuses while
no rule started it. This hook denies a Write or an Edit under `units/<n>/`
while that inventory is missing. It also denies `units/<n>/draft.md` and
`units/<n>/evaluation.md` while `units/<n>/check-unit.json`, the stamp
`check-unit` writes, is missing or does not match the current `unit.json`.
Any other file, any other tool, and any internal error are silent: exit 0,
no output.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

_UNIT_PATH_RE = re.compile(r"^units/(\d+)/[^/]+$")
_DRAFT_FILES = ("draft.md", "evaluation.md")


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _find_workspace(path: Path) -> Path | None:
    for directory in path.parents:
        if directory.parent.name == "attack" and (directory / "problem.json").is_file():
            return directory
    return None


def _is_checked_as_it_stands(unit_dir: Path) -> bool:
    stamp_path = unit_dir / "check-unit.json"
    unit_path = unit_dir / "unit.json"
    if not stamp_path.is_file() or not unit_path.is_file():
        return False
    try:
        stamped = json.loads(stamp_path.read_text(encoding="utf-8"))["unit_sha256"]
    except (ValueError, KeyError, TypeError):
        return False
    return stamped == hashlib.sha256(unit_path.read_bytes()).hexdigest()


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)
    path = Path((payload.get("tool_input") or {}).get("file_path", ""))
    if not path.is_absolute():
        path = Path(payload.get("cwd") or ".") / path
    path = path.resolve()
    workspace = _find_workspace(path)
    if workspace is None:
        sys.exit(0)
    match = _UNIT_PATH_RE.match(path.relative_to(workspace).as_posix())
    if match is None:
        sys.exit(0)
    slug, number = workspace.name, match.group(1)
    if not (workspace / "units" / "INVENTORY.md").is_file():
        _deny(
            f"[math-solver] units/INVENTORY.md is missing, so no cash-out rule has started"
            f" and units/{number}/ cannot be written yet. Run:\n  exactory-math stall {slug}\n"
            "It refuses while the attack is open; in that case continue the attack at stage 5."
        )
    if path.name in _DRAFT_FILES and not _is_checked_as_it_stands(workspace / "units" / number):
        _deny(
            f"[math-solver] units/{number}/unit.json has not passed check-unit as it now"
            f" stands, so {path.name} cannot be written. Run:\n"
            f"  exactory-math check-unit {slug} {number}\nthen write it."
        )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
