#!/usr/bin/env python3
"""Stop hook: keep a math-solver attack moving until it is finished.

The math-solver skill runs end to end. When the session tries to stop while an
attack workspace under the working directory (`attack/<slug>/`, with the
`problem.json` that `exactory-math init` wrote) has no `units/FINISHED.json`,
this hook blocks the stop and tells the agent where the attack stands. A
per-attack counter caps the run so a stuck loop pauses on its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_COUNTER_NAME = ".continue_count"
_MAX_ADVANCES = int(os.environ.get("EXACTORY_ATTACK_MAX", "40"))
_HARNESS_LAUNCHER = Path(__file__).resolve().parent.parent / "bin" / "exactory-math"


def _read_next_step(attack_root: Path, slug: str) -> str | None:
    """The `next:` line of `exactory-math status`, or None when the harness cannot say."""
    try:
        completed = subprocess.run(
            [sys.executable, str(_HARNESS_LAUNCHER), "--attack-root", str(attack_root), "status", slug],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return next((line for line in completed.stdout.splitlines() if line.startswith("next:")), None)


def _block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _find_attack_root(start_dir: Path) -> Path | None:
    for directory in (start_dir, *start_dir.parents):
        if directory.name == "attack" and directory.is_dir():
            return directory
        if (directory / "attack").is_dir():
            return directory / "attack"
    return None


def _list_open_workspaces(attack_root: Path) -> list[Path]:
    return [
        workspace
        for workspace in sorted(attack_root.iterdir())
        if (workspace / "problem.json").is_file()
        and not (workspace / "units" / "FINISHED.json").is_file()
    ]


def _describe(workspace: Path) -> str:
    journal_path = workspace / "journal.jsonl"
    moves = 0
    if journal_path.is_file():
        moves = sum(1 for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip())
    inventory = "written" if (workspace / "units" / "INVENTORY.md").is_file() else "not written"
    units = sum(1 for path in (workspace / "units").glob("*") if path.is_dir() and path.name.isdigit())
    return f"{moves} moves journalled, inventory {inventory}, {units} units"


def main() -> None:
    payload = json.load(sys.stdin)
    attack_root = _find_attack_root(Path(payload.get("cwd") or ".").resolve())
    if attack_root is None:
        sys.exit(0)
    open_workspaces = _list_open_workspaces(attack_root)
    if not open_workspaces:
        sys.exit(0)

    first = open_workspaces[0]
    counter_path = first / _COUNTER_NAME
    try:
        count = int(counter_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        count = 0
    if count >= _MAX_ADVANCES:
        counter_path.write_text("0", encoding="utf-8")
        _block(
            f"[math-solver] Reached the {_MAX_ADVANCES}-advance safety cap for"
            f" attack/{first.name}. Pausing. Summarize where the attack stands and"
            " ask whether to continue; the user can raise EXACTORY_ATTACK_MAX."
        )
    counter_path.write_text(str(count + 1), encoding="utf-8")
    standing = "; ".join(
        f"attack/{workspace.name} is not finished ({_describe(workspace)})"
        for workspace in open_workspaces
    )
    next_step = _read_next_step(attack_root, first.name)
    guidance = f" The harness says: {next_step}." if next_step else ""
    _block(
        f"[math-solver advance {count + 1}/{_MAX_ADVANCES}] {standing}.{guidance} Continue the"
        " attack from where its record stands (`exactory-math status <slug>`), at that"
        f" stage of the math-solver skill, and run `exactory-math finish {first.name}` when"
        " every unit is checked, drafted, and evaluated (or, at the stage 3 exit, once"
        " novelty.md records where the statement is solved)."
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
