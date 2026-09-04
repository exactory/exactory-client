#!/usr/bin/env python3
"""Install a staged plugin with the real Codex app server, then inspect skills and hooks.

Run explicitly: python3 tests/smoke_codex.py --codex /path/to/codex
Uses an isolated Codex configuration. Add --live for an authenticated model
test confined to a temporary workspace. Neither mode calls the Exactory API.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true", help="Also run an authenticated, local-workspace model smoke test")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="exactory-host-smoke-") as scratch:
        root = Path(scratch)
        home = root / "codex-home"
        home.mkdir()
        market = root / "marketplace"
        plugin = market / "plugins/exactory"
        shutil.copytree(ROOT, plugin, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        manifest = market / ".agents/plugins/marketplace.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({
            "name": "exactory-smoke",
            "plugins": [{"name": "exactory", "source": {
                "source": "local", "path": "./plugins/exactory"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity"}],
        }))
        workspace = root / "workspace"
        workspace.mkdir()
        env = {**os.environ, "CODEX_HOME": str(home)}
        responses = queue.Queue()
        with (root / "server.log").open("w") as log:
            process = subprocess.Popen(
                [args.codex, "app-server", "--stdio", "--enable", "hooks"],
                cwd=workspace, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, text=True,
            )
            def read_output():
                for line in process.stdout:
                    responses.put(json.loads(line))
            threading.Thread(target=read_output, daemon=True).start()
            counter = 0
            def rpc(method, params):
                nonlocal counter
                counter += 1
                process.stdin.write(json.dumps({"id": counter, "method": method, "params": params}) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    reply = responses.get(timeout=max(0.1, deadline - time.monotonic()))
                    if reply.get("id") == counter:
                        if "error" in reply:
                            raise AssertionError(f"{method}: {reply['error']}")
                        return reply["result"]
                raise TimeoutError(method)
            try:
                rpc("initialize", {"clientInfo": {"name": "exactory_smoke", "version": "1.0.0"},
                                   "capabilities": {"experimentalApi": True}})
                process.stdin.write('{"method":"initialized"}\n')
                process.stdin.flush()
                installed = rpc("plugin/install", {"marketplacePath": str(manifest), "pluginName": "exactory"})
                skills = rpc("skills/list", {"cwds": [str(workspace)], "forceReload": True})
                hooks = rpc("hooks/list", {"cwds": [str(workspace)]})
                expected = {f"exactory:{path.parent.name}" for path in (ROOT / "skills").glob("*/SKILL.md")}
                loaded = {skill["name"] for row in skills["data"] for skill in row["skills"]
                          if skill["name"].startswith("exactory:") and skill["enabled"]}
                assert loaded == expected, (loaded, expected)
                assert all(not row["errors"] for row in skills["data"]), skills
                registered = [hook for row in hooks["data"] for hook in row["hooks"]
                              if hook.get("pluginId") == "exactory@exactory-smoke"]
                declarations = json.loads((ROOT / "codex/hooks.json").read_text())["hooks"]
                count = sum(len(group["hooks"]) for groups in declarations.values() for group in groups)
                assert len(registered) == count, registered
                assert all(hook["enabled"] and hook["sourcePath"].endswith("/codex/hooks.json")
                           for hook in registered), registered
                assert all(not row["warnings"] and not row["errors"] for row in hooks["data"]), hooks
                result = {"installed": installed, "skills": skills, "hooks": hooks}
                if args.output:
                    args.output.write_text(json.dumps(result, indent=2) + "\n")
                print(f"PASS: installed plugin, {len(loaded)} skills, {len(registered)} hooks, no load errors")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if args.live:
            # Reuse the host's login without printing or copying credential values.
            auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
            if auth.is_file():
                (home / "auth.json").symlink_to(auth)
            attack = workspace / "attack/smoke"
            (attack / "units").mkdir(parents=True)
            (attack / "problem.json").write_text('{"claim":"test fixture"}')
            (attack / "tasks.json").write_text("[]")
            (attack / "units/FINISHED.json").write_text("{}")
            prompt = (
                "This is an integration test of the installed Exactory plugin, not a research task. "
                "Do exactly these steps, then stop. Read the installed exactory:math-solver skill "
                "entrypoint and its Codex runtime guide, without starting the mathematical workflow. "
                "Use its installed CLI to run exactory-math skill-dir from this workspace, using "
                "the runtime guide's PATH instructions. Write that command's result to skill-dir.txt. "
                "Then use apply_patch exactly once to replace [] with [1] in attack/smoke/tasks.json. "
                "The hook is expected to deny this edit. Do not retry it or use another tool to "
                "modify that file. Finally use apply_patch to add safe.txt with the text smoke-ok. "
                "Report whether the protected edit was denied. Do not read credentials or call any "
                "Exactory network command. Do not spawn agents."
            )
            completed = subprocess.run(
                [args.codex, "exec", "--skip-git-repo-check",
                 "--sandbox", "workspace-write", "--dangerously-bypass-hook-trust",
                 "--enable", "hooks", "--json", prompt],
                cwd=workspace, env=env, capture_output=True, text=True, timeout=300,
            )
            if args.output:
                args.output.with_suffix(".live.jsonl").write_text(completed.stdout)
                args.output.with_suffix(".live.stderr").write_text(completed.stderr)
            assert completed.returncode == 0, completed.stderr[-2000:]
            assert (attack / "tasks.json").read_text() == "[]", "Protected record was changed"
            assert (workspace / "safe.txt").read_text().strip() == "smoke-ok", completed.stdout[-2000:]
            resolved_skill = Path((workspace / "skill-dir.txt").read_text().strip())
            assert resolved_skill.is_dir() and (resolved_skill / "SKILL.md").is_file(), resolved_skill
            transcripts = "\n".join(path.read_text() for path in (home / "sessions").rglob("*.jsonl"))
            if args.output:
                args.output.with_suffix(".transcript.jsonl").write_text(transcripts)
            assert "harness only" in transcripts, "No hook denial found in the session transcript"
            print("PASS: live Codex skill loading, installed CLI, denied protected patch, allowed normal patch")


if __name__ == "__main__":
    main()
