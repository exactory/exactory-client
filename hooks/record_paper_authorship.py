#!/usr/bin/env python3
"""PostToolUse recorder: an agent wrote this workspace's paper source.

`exactory-draft deposit` names exactory.ai as the paper's writer only when the
workspace holds that record. This hook writes it. The harness runs the hook
after the tool call itself, so the record comes from the act of writing and
not from an instruction the agent had to follow.

The hook acts only when the Write tool created a whole `.tex` file inside the
`draft/` tree of a draft workspace (an ancestor directory holds
`.exactory/draft.json`). It then writes `.exactory/authorship.json` with
`{"written_by_exactory": true}`. LaTeX sources are the only place the paper's
prose lives, so `draft/abstract.txt`, which the deposit stage writes, and
`draft/references.bib`, which `exactory-check add` writes, are not authorship.
An Edit is not authorship either: `exactory-draft init` leaves `draft/` empty,
so an agent that writes the paper creates its first `.tex` file with Write,
while an Edit also lands when an agent changes one line of a paper a person
wrote. Anything else, a workspace that already holds the record, and any
internal error are all silent: exit 0, no output.

A person who sets the value to false turns the claim off. It stays off until
an agent writes a paper source again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PAPER_WRITING_TOOL_NAME = "Write"
_PAPER_SOURCE_SUFFIX = ".tex"
_DRAFT_DIR_NAME = "draft"
_DRAFT_STATE_PATH = Path(".exactory") / "draft.json"
_AUTHORSHIP_STATE_PATH = Path(".exactory") / "authorship.json"
_AUTHORSHIP_RECORD_JSON_TEXT = json.dumps({"written_by_exactory": True}, indent=2) + "\n"


def _find_workspace(written_path: Path) -> Path | None:
    """Return the nearest ancestor directory that holds .exactory/draft.json."""
    for directory in written_path.parents:
        if (directory / _DRAFT_STATE_PATH).is_file():
            return directory
    return None


def _is_already_recorded(state_path: Path) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and state.get("written_by_exactory") is True


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != _PAPER_WRITING_TOOL_NAME:
        sys.exit(0)

    written_path = Path((payload.get("tool_input") or {}).get("file_path", ""))
    if written_path.suffix.lower() != _PAPER_SOURCE_SUFFIX:
        sys.exit(0)
    if not written_path.is_absolute():
        written_path = Path(payload.get("cwd") or ".") / written_path
    written_path = written_path.resolve()

    workspace = _find_workspace(written_path)
    if workspace is None:
        sys.exit(0)
    if not written_path.is_relative_to(workspace / _DRAFT_DIR_NAME):
        sys.exit(0)

    state_path = workspace / _AUTHORSHIP_STATE_PATH
    if _is_already_recorded(state_path):
        sys.exit(0)
    # The record is one fixed constant, so the copies of this hook that run in
    # parallel after a batch of tool calls all write the same bytes. A reader
    # that catches the file mid-write reads invalid JSON, which the deposit
    # answers with the disclosure that claims no authorship.
    state_path.write_text(_AUTHORSHIP_RECORD_JSON_TEXT, encoding="utf-8")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
