#!/usr/bin/env python3
"""Exercise real Claude Code plugin tools and hooks with a local scripted API.

Only model responses are scripted. Claude Code loads the actual plugin and
executes its real tools, CLI, and hooks in a temporary workspace. No login,
paid inference, or Exactory API request is involved.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="exactory-claude-smoke-") as scratch:
        root = Path(scratch)
        plugin = root / "plugin"
        shutil.copytree(ROOT, plugin, ignore=shutil.ignore_patterns(".git", "__pycache__", ".coverage*"))
        workspace = root / "workspace"
        attack = workspace / "attack/smoke"
        (attack / "units").mkdir(parents=True)
        (attack / "problem.json").write_text('{"claim":"smoke fixture"}')
        (attack / "tasks.json").write_text("[]")
        (attack / "units/FINISHED.json").write_text("{}")
        actions = [
            ("Skill", {"skill": "exactory:status"}),
            ("Bash", {"command": f"python3 {shlex.quote(str(plugin / 'bin/exactory-math'))} skill-dir > skill-dir.txt"}),
            ("Write", {"file_path": str(attack / "tasks.json"), "content": "[1]"}),
            ("Write", {"file_path": str(workspace / "safe.txt"), "content": "smoke-ok"}),
        ]
        requests = []
        step = 0

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                nonlocal step
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if "count_tokens" in self.path:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":100}')
                    return
                requests.append(request)
                use_tool = bool(request.get("tools")) and step < len(actions)
                if use_tool:
                    name, arguments = actions[step]
                    step += 1
                    block = {"type": "tool_use", "id": f"tool_{step}", "name": name, "input": arguments}
                else:
                    block = {"type": "text", "text": "Smoke test complete."}
                message = {
                    "id": f"msg_{len(requests)}", "type": "message", "role": "assistant",
                    "model": request["model"], "content": [block],
                    "stop_reason": "tool_use" if use_tool else "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 100, "output_tokens": 30},
                }
                self.send_response(200)
                if not request.get("stream"):
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(message).encode())
                    return
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                initial_block = {**block, "input": {}} if use_tool else {"type": "text", "text": ""}
                delta = ({"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
                         if use_tool else {"type": "text_delta", "text": block["text"]})
                events = [
                    {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
                    {"type": "content_block_start", "index": 0, "content_block": initial_block},
                    {"type": "content_block_delta", "index": 0, "delta": delta},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                     "usage": {"output_tokens": 30}},
                    {"type": "message_stop"},
                ]
                for event in events:
                    self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))}
        env.update({"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                    "ANTHROPIC_API_KEY": "local-smoke-fixture", "CLAUDE_CONFIG_DIR": str(root / "config"),
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        try:
            completed = subprocess.run(
                [args.claude, "-p", "Run the Exactory integration smoke test.",
                 "--plugin-dir", str(plugin), "--setting-sources", "",
                 "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                 "--allowedTools", "Skill,Read,Bash,Write,Edit", "--permission-mode", "acceptEdits",
                 "--no-session-persistence", "--output-format", "stream-json", "--verbose"],
                cwd=workspace, env=env, capture_output=True, text=True, timeout=180,
            )
        finally:
            server.shutdown()
            server.server_close()
        if args.output:
            args.output.write_text(completed.stdout)
            args.output.with_suffix(".stderr").write_text(completed.stderr)
        assert completed.returncode == 0, completed.stderr + completed.stdout[-2000:]
        assert step == len(actions), completed.stdout[-2000:]
        assert (attack / "tasks.json").read_text() == "[]", "Protected record was changed"
        assert (workspace / "safe.txt").read_text() == "smoke-ok", completed.stdout[-2000:]
        assert Path((workspace / "skill-dir.txt").read_text().strip()).resolve() == (plugin / "skills/math-solver").resolve()
        assert "harness only" in json.dumps(requests), "Claude did not receive the hook denial"
        assert "exactory:status" in completed.stdout, "The shared skill was not discovered"
        print("PASS: real Claude Code skill, CLI, denied protected write, allowed normal write (scripted model API)")


if __name__ == "__main__":
    main()
