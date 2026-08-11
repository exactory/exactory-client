#!/usr/bin/env python3
"""PreToolUse gate: a paper leaves the draft workspace only with a fresh, clean citation report.

The authoritative gate lives in the CLIs: `exactory submit` and `exactory-draft
deposit --production` run `exactory-check gate` themselves before any network
call. This hook is the second layer of the same rule, applied at the Bash
boundary. It splits the command into shell tokens and intercepts an `exactory`
token directly followed by a `submit` token (a next token of `submit-review` is
a different subcommand and stays neutral), or an `exactory-draft` token with a
later `deposit` token and a later `--production` flag (any unambiguous argparse
abbreviation down to `--prod` counts). A command that names those operations
but does not split as shell text is denied as unparseable, in and out of a
workspace. For a matched command, the gate acts only when the payload's `cwd`
is inside a draft workspace (an ancestor directory holds
`.exactory/draft.json`). The gate never touches the network: it reads the
report `exactory-check lookup` wrote to `.exactory/citation-check.json` and
denies unless the report is present, fresh (its `bib_sha256` matches the
current `draft/references.bib`), clean (`blocking == 0`), and non-trivial
(`nothing_verified` is false). A workspace with no references file at all is
denied too: a paper with zero references is not submitted from a draft
workspace. Everything else — other commands, a cwd outside any workspace, any
internal error — is neutral: exit 0, no output.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path

_LOOKUP_CMD = "exactory-check lookup --bib draft/references.bib"


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def main() -> None:
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command", "")

    try:
        tokens = shlex.split(command)
    except ValueError:
        if "exactory" in command and ("submit" in command or "deposit" in command):
            _deny(
                "The gate cannot parse this exactory command as shell text:"
                " correct the quoting, then run it again."
            )
        sys.exit(0)

    is_submit_command = any(
        token == "exactory" and next_token == "submit"
        for token, next_token in zip(tokens, tokens[1:])
    )
    is_production_deposit_command = False
    if "exactory-draft" in tokens:
        tokens_after_cli = tokens[tokens.index("exactory-draft") + 1 :]
        is_production_deposit_command = "deposit" in tokens_after_cli and any(
            token.startswith("--prod") and "--production".startswith(token)
            for token in tokens_after_cli
        )
    if not (is_submit_command or is_production_deposit_command):
        sys.exit(0)

    start_dir = Path(payload.get("cwd") or ".").resolve()
    workspace = next(
        (
            directory
            for directory in (start_dir, *start_dir.parents)
            if (directory / ".exactory" / "draft.json").is_file()
        ),
        None,
    )
    if workspace is None:
        sys.exit(0)

    references_path = workspace / "draft" / "references.bib"
    if not references_path.is_file():
        _deny(
            "The workspace has no draft/references.bib: add each reference with"
            " exactory-check add before you submit."
        )

    report_path = workspace / ".exactory" / "citation-check.json"
    if not report_path.is_file():
        _deny(f"No citation report exists: run {_LOOKUP_CMD} before you submit.")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = None
    if not isinstance(report, dict):
        _deny(f"The citation report is not readable: run {_LOOKUP_CMD} again.")

    current_bib_sha256 = hashlib.sha256(references_path.read_bytes()).hexdigest()
    if report.get("bib_sha256") != current_bib_sha256:
        _deny(
            "The citation report is stale because draft/references.bib changed:"
            f" run {_LOOKUP_CMD} again."
        )
    if report.get("blocking") != 0:
        _deny(
            "The citation report shows blocking references: correct them,"
            f" then run {_LOOKUP_CMD} again."
        )
    if report.get("nothing_verified") is not False:
        _deny(f"The citation report verified zero references: go online, then run {_LOOKUP_CMD}.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
