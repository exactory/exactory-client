#!/usr/bin/env python3
"""Print actual Codex hooks/list and execute each installed managed command."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys
import tempfile
import time
import uuid


EVENT_NAMES = {"sessionStart": "SessionStart", "preToolUse": "PreToolUse",
               "postToolUse": "PostToolUse", "stop": "Stop"}


def read_response(process, request_id, timeout=30):
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise RuntimeError("Codex app-server response timed out")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("Codex app-server closed before replying")
            response = json.loads(line)
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(json.dumps(response["error"]))
                return response


def send_message(process, message):
    process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
    process.stdin.flush()


def list_hooks(codex, workspace):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([codex, "app-server", "--stdio"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=errors, bufsize=0)
        try:
            send_message(process, {"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "exactory_managed_hook_smoke", "version": "1"},
                "capabilities": {"experimentalApi": True}}})
            read_response(process, 1)
            send_message(process, {"method": "initialized", "params": {}})
            send_message(process, {"id": 2, "method": "hooks/list", "params": {"cwds": [str(workspace)]}})
            return read_response(process, 2)
        finally:
            process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            errors.seek(0)
            diagnostics = errors.read().decode("utf-8", errors="replace")
            if diagnostics:
                print(diagnostics, file=sys.stderr, end="")


def select_commands(prefix, answer):
    sys.path.insert(0, str(prefix / "v1"))
    from hook_runtime.policy import build_managed_config
    interpreter = json.loads((prefix / "v1/interpreter.json").read_text())
    expected = build_managed_config(prefix, interpreter)["hooks"]
    expected_commands = Counter((event, handler["command"])
        for event, groups in expected.items() for group in groups for handler in group["hooks"])
    commands = []
    for item in answer["result"]["data"]:
        if item.get("errors"):
            raise RuntimeError("hooks/list contains errors: " + json.dumps(item["errors"]))
        for hook in item["hooks"]:
            command = hook.get("command") or ""
            if (hook.get("pluginId") == "exactory@exactory-ai" and hook.get("enabled")):
                raise RuntimeError("Duplicate Exactory plugin registration is enabled")
            if str(prefix / "v1/dispatch.py") not in shlex.split(command):
                continue
            if not hook.get("isManaged") or not hook.get("enabled"):
                raise RuntimeError("Expected an enabled administrator-managed hook")
            event = EVENT_NAMES.get(hook["eventName"])
            commands.append((event, command, hook.get("timeoutSec") or 30))
    if Counter((event, command) for event, command, _ in commands) != expected_commands:
        raise RuntimeError("hooks/list does not contain exactly the installed managed registrations")
    return sorted(commands, key=lambda row: row[0] != "SessionStart")


def run_commands(commands, workspace):
    session = "exactory-smoke-" + uuid.uuid4().hex
    for event, command, timeout in commands:
        payload = {"session_id": session, "hook_event_name": event, "source": "startup",
                   "cwd": str(workspace), "tool_name": "Bash", "tool_input": {"command": "true"},
                   "tool_response": {"exit_code": 0, "stdout": "", "stderr": ""},
                   "stop_hook_active": False}
        result = subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload),
                                capture_output=True, text=True, cwd=workspace, timeout=timeout)
        print(json.dumps({"event": event, "command": command, "exit_code": result.returncode,
                          "stdout": result.stdout, "stderr": result.stderr}), flush=True)
        if result.returncode:
            raise RuntimeError("Managed command failed")
    return session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--codex", default="codex")
    args = parser.parse_args()
    prefix, workspace = args.prefix.resolve(), args.workspace.resolve()
    if any(workspace.iterdir()):
        parser.error("Use an empty disposable workspace")
    for ancestor in (workspace, *workspace.parents):
        for name in (".exactory", ".search", "attack"):
            try:
                (ancestor / name).stat()
            except FileNotFoundError:
                continue
            parser.error("Use a disposable workspace outside research records")
    answer = list_hooks(args.codex, workspace)
    print(json.dumps(answer, indent=2), flush=True)
    commands = select_commands(prefix, answer)
    session = run_commands(commands, workspace)
    print(json.dumps({"session_id": session, "commands_executed": len(commands)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
