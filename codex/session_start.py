#!/usr/bin/env python3
"""Give Codex the installed command path and the shared workflow conventions."""
import json
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": (
            f"Exactory is enabled in Codex. Read {ROOT / 'codex/README.md'} before using its skills. "
            "Run commands from the user's workspace. In EACH shell call that uses an Exactory CLI, "
            f"first set export PATH={shlex.quote(str(ROOT / 'bin'))}:\"$PATH\". "
            "A previous shell's export does not persist. The shared /exactory:<name> references "
            "mean the corresponding installed Exactory skill; read that skill's SKILL.md. "
            "Use the current session's tools for shell commands, file edits, questions, and "
            "independent reviewers. Preserve all workspace checks and approval requirements."
        ),
    }}))
