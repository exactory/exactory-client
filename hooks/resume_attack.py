#!/usr/bin/env python3
"""SessionStart hook: a session that begins over an open attack resumes it.

When the working directory, or one of its ancestors, holds `attack/<slug>/`
with the `problem.json` that `exactory-math init` wrote and no
`units/FINISHED.json`, this hook runs `exactory-math status <slug>` and hands
the result to the session as context, with the instruction to resume the
math-solver skill at its stage 0 rather than start over. Every source of a
session start (a new session, a resume, a clear, a compaction) gets it, since
each one begins with an empty context. No open attack, and any internal error,
is silent: exit 0, no output.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_HARNESS_LAUNCHER = _PLUGIN_ROOT / "bin" / "exactory-math"


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


def _read_status(attack_root: Path, slug: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(_HARNESS_LAUNCHER), "--attack-root", str(attack_root), "status", slug],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        first_line = (completed.stderr.strip().splitlines() or ["no output"])[0]
        return "status unavailable: %s" % first_line
    return completed.stdout.rstrip()


def main() -> None:
    payload = json.load(sys.stdin)
    attack_root = _find_attack_root(Path(payload.get("cwd") or ".").resolve())
    if attack_root is None:
        sys.exit(0)
    open_workspaces = _list_open_workspaces(attack_root)
    if not open_workspaces:
        sys.exit(0)
    blocks = []
    for workspace in open_workspaces:
        status = "\n".join("  " + line for line in _read_status(attack_root, workspace.name).splitlines())
        blocks.append(
            "[math-solver] An attack is open under attack/%s; `exactory-math status %s` says:\n%s"
            % (workspace.name, workspace.name, status)
        )
    context = "\n\n".join(blocks) + (
        "\n\nResume the /exactory:math-solver skill at its stage 0: read this status and"
        " attack/<slug>/tasks.json, and continue from the `next:` line. Do not run"
        " `exactory-math init` again and do not restart at stage 1; the record is the save."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
