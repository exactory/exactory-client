#!/usr/bin/env python3
"""PreToolUse gate: experiment code is model-written, so keep it inside the study.

Stage 3 of Exactory AI Science runs code the agent wrote. This hook is the
enforcement behind the experiment skill's safety rules: it blocks the
catastrophic class of shell commands when the run is inside a study workspace
(an ancestor of the payload's `cwd` holds `.exactory/study.json`). Outside a
workspace, and for any tool other than Bash, it stays neutral.

The hook is a denylist, not a sandbox: it cannot contain a process that writes
through an absolute path. `exactory-lab run` confines the working directory and
refuses scripts outside `experiment/`; this hook stops the shell commands that
would escape or damage the machine regardless of cwd. A block means redesign
the experiment, never route around the guard.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_STUDY_STATE_PATH = Path(".exactory") / "study.json"

# (compiled pattern, reason). First match denies. IGNORECASE throughout.
_DENY_RULES = [
    (r"\bsudo\b|\bdoas\b|\bsu\s+-", "privilege escalation is not allowed in experiments"),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r",
     "recursive force-delete: keep deletions inside the workspace"),
    (r"\bmkfs\b|\bdd\b[^\n]*\bof=/dev/|>\s*/dev/(sd|disk|nvme)|diskutil\s+.*erase",
     "raw disk or device write"),
    (r"(curl|wget|fetch)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|c|tc|k)?sh\b",
     "piping a downloaded script straight into a shell"),
    (r"(curl|wget|fetch)\b[^|;&]*\|\s*python[0-9.]*\b",
     "piping downloaded content into python"),
    (r"/etc/shadow|/etc/sudoers|~/\.ssh/|/\.ssh/id_|~/\.aws/credentials|\.aws/credentials",
     "access to credentials or secret material"),
    (r"\bsecurity\s+find-(generic|internet)-password\b|\bkeychain\b.*\bdump",
     "keychain credential extraction"),
    (r"\bcrontab\b|\blaunchctl\s+(load|unload|bootstrap)|/Library/LaunchDaemons|"
     r"/Library/LaunchAgents", "installing a persistence mechanism"),
    (r"(^|[\s;&|])(>|>>)\s*/etc/|\btee\s+/etc/", "writing into /etc"),
    (r"\bkillall\b|\bpkill\s+-9\b|\bkill\s+-9\s+-1\b", "broad process kill"),
    (r"\.claude/(settings(\.local)?\.json|hooks/)",
     "modifying Claude Code config or hooks"),
    # The whole .exactory directory, not a list of the files in it: the CLI
    # and the hooks own every file there, and a list of names goes stale each
    # time the workspace gains a state file.
    (r"(>|>>|\btee\b)[^\n]*\.exactory/",
     "writing a workspace state file through the shell"),
    (r"\bchmod\s+-R?\s*0?777\b", "world-writable chmod 777"),
]
_COMPILED_DENY_RULES = [(re.compile(pattern, re.IGNORECASE), reason)
                        for pattern, reason in _DENY_RULES]


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"[guard-experiment-exec] Blocked: {reason}. Redesign the"
                " experiment to stay inside the workspace and avoid this"
                " action; do not route around the guard."
            ),
        }
    }))
    sys.exit(0)


def _is_inside_study_workspace(start_dir: Path) -> bool:
    for directory in (start_dir, *start_dir.parents):
        if (directory / _STUDY_STATE_PATH).is_file():
            return True
    return False


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command", "")
    if command == "":
        sys.exit(0)
    if not _is_inside_study_workspace(Path(payload.get("cwd") or ".").resolve()):
        sys.exit(0)
    for pattern, reason in _COMPILED_DENY_RULES:
        if pattern.search(command):
            _deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
