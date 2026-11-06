"""Resolve one approved session release, then execute its own hook entrypoint."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from .bundles import resolve_release
from .state import RuntimeRefusal


EXIT_CODES = {"PreToolUse": 2, "PostToolUse": 1, "SessionStart": 1, "Stop": 0}


def validate_invocation(event, arguments):
    config = json.loads((Path(__file__).parent / "registrations.json").read_text(encoding="utf-8"))
    allowed = [shlex.split(handler["command"])[6:]
               for group in config["hooks"].get(event, []) for handler in group["hooks"]]
    if not arguments or list(arguments) not in allowed or len(arguments) not in (1, 2):
        raise RuntimeRefusal("Unregistered hook entrypoint or arguments")


def dispatch_event(prefix, event, arguments, raw):
    validate_invocation(event, arguments)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise RuntimeRefusal("Invalid hook input") from error
    if not isinstance(payload, dict) or payload.get("hook_event_name") != event:
        raise RuntimeRefusal("Hook input does not match the registered event")
    release = resolve_release(prefix, payload, event)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PYTHON")}
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                        "PLUGIN_ROOT": str(release), "CLAUDE_PLUGIN_ROOT": str(release)})
    result = subprocess.run([sys.executable, "-I", "-B", str(release / "codex" / arguments[0]),
                             *arguments[1:]], input=raw, env=environment)
    return result.returncode


def main(prefix):
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        return dispatch_event(prefix, event, sys.argv[2:], sys.stdin.buffer.read())
    except (RuntimeRefusal, OSError) as error:
        print("[exactory-runtime] " + str(error), file=sys.stderr)
        return EXIT_CODES.get(event, 2)
