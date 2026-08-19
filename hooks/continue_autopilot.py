#!/usr/bin/env python3
"""Stop hook: keep an autopilot study moving until it finishes or parks.

Exactory AI Science runs end to end by default. When the session tries to stop
while the study workspace is on autopilot, this hook blocks the stop and tells
the agent to continue the loop, unless the run is finished or parked at a wait
(the context grace phase, or a production deposit or submission awaiting
approval). A per-study counter caps the run so a stuck loop pauses on its own.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_STUDY_STATE_PATH = Path(".exactory") / "study.json"
_COUNTER_PATH = Path(".exactory") / "autopilot_count"
_MAX_ADVANCES = int(os.environ.get("EXACTORY_AUTOPILOT_MAX", "50"))


def _allow_stop(counter_path: Path) -> None:
    if counter_path.exists():
        counter_path.write_text("0", encoding="utf-8")
    sys.exit(0)


def _block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _find_workspace(start_dir: Path) -> Path | None:
    for directory in (start_dir, *start_dir.parents):
        if (directory / _STUDY_STATE_PATH).is_file():
            return directory
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    workspace = _find_workspace(Path(payload.get("cwd") or ".").resolve())
    if workspace is None:
        sys.exit(0)
    counter_path = workspace / _COUNTER_PATH
    try:
        state = json.loads((workspace / _STUDY_STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _allow_stop(counter_path)

    if not state.get("autopilot"):
        _allow_stop(counter_path)
    if state.get("stage") == "complete":
        _allow_stop(counter_path)
    if state.get("waiting"):
        _allow_stop(counter_path)

    try:
        count = int(counter_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        count = 0
    if count >= _MAX_ADVANCES:
        counter_path.write_text("0", encoding="utf-8")
        _block(
            f"[ai-science] Reached the {_MAX_ADVANCES}-advance safety cap for"
            f" study '{state.get('slug')}'. Pausing. Summarize progress for the"
            " user and ask whether to continue; they can raise"
            " EXACTORY_AUTOPILOT_MAX."
        )
    counter_path.write_text(str(count + 1), encoding="utf-8")
    _block(
        f"[ai-science advance {count + 1}/{_MAX_ADVANCES}] Study"
        f" '{state.get('slug')}' is at stage '{state.get('stage')}'"
        f" (status={state.get('status')}): continue the Exactory AI Science"
        " loop. Park with `exactory-lab state set --waiting <reason>` when"
        " input is genuinely needed, and set the study to stage 'complete'"
        " when the work is done."
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
