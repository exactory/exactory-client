#!/usr/bin/env python3
"""PreToolUse gate: a verdict is filed only with its cohort impact prediction.

The authoritative gate lives in the CLI: `exactory verify` refuses a verdict
file whose `prediction` is missing, null, or without a `percentile`, before
any network call. This hook is the second layer of the same rule, applied at
the Bash boundary. It splits the command into shell tokens and intercepts an
`exactory` token directly followed by a `verify` token. A command that names
those operations but does not split as shell text is denied as unparseable.
For a matched command, the gate reads the verdict file the `--file` flag
names (a relative path resolves against the payload's `cwd`) and denies when
the file parses as JSON but carries no prediction, or a prediction with no
percentile. Everything else — other commands, a command without `--file`, a
missing or unreadable file (the CLI reports those itself), any internal
error — is neutral: exit 0, no output.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path


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


def _extract_file_argument(tokens: list[str]) -> str | None:
    for token, next_token in zip(tokens, tokens[1:]):
        if token == "--file":
            return next_token
    for token in tokens:
        if token.startswith("--file="):
            return token[len("--file="):]
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command", "")

    try:
        tokens = shlex.split(command)
    except ValueError:
        if "exactory" in command and "verify" in command:
            _deny(
                "The gate cannot parse this exactory command as shell text:"
                " correct the quoting, then run it again."
            )
        sys.exit(0)

    is_verify_command = any(
        token == "exactory" and next_token == "verify"
        for token, next_token in zip(tokens, tokens[1:])
    )
    if not is_verify_command:
        sys.exit(0)

    file_argument = _extract_file_argument(tokens)
    if file_argument is None:
        sys.exit(0)

    verdict_path = Path(file_argument)
    if not verdict_path.is_absolute():
        verdict_path = Path(payload.get("cwd") or ".") / verdict_path
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sys.exit(0)

    prediction = verdict.get("prediction") if isinstance(verdict, dict) else None
    if not isinstance(prediction, dict):
        _deny(
            "The verdict carries no prediction, and a verdict without one is not"
            " filed. Freeze the cohort with exactory-cohort freeze, state the"
            " percentile you expect the paper to reach in it, add the prediction"
            " object (corpus, category, windowStart, windowEnd, percentile, band)"
            " to the verdict file, then send it again."
        )
    if "percentile" not in prediction:
        _deny(
            "The verdict's prediction carries no percentile. State the percentile"
            " you expect the paper to reach in its frozen cohort, add it to the"
            " prediction, then send it again."
        )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
