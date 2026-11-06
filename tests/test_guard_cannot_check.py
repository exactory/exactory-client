"""A failed file check preserves reads while keeping possible writes closed."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
import math_search

GUIDANCE_MSG = (
    "The guard still passes `Read`, `Glob`, `Grep`, `WebSearch`, `WebFetch` and `ToolSearch`. "
    "It also passes shell commands made only of `cat`, `ls`, `grep`, `head`, `tail`, `wc`, `echo` or `du` "
    "with plain arguments (no `$`, `*`, `?`, `[`, parentheses, braces, redirection or assignment, even quoted). "
    "Tell the user which call was denied and quote the diagnostic. Only the user can do the rest. "
    "`/cd <path>` to a directory outside the workspace that holds the record lets unrelated work continue, "
    "but other calls that name the record stay denied. To work in the record, the user can disable the plugin. "
    "On Claude Code, the user runs `/plugin disable exactory@exactory-ai`, then `/reload-plugins --force`. "
    "On Codex, the user disables the Exactory hooks in `/hooks`. "
    "An administrator-managed plugin or hook cannot be disabled this way."
)


class GuardCannotCheckTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="guard cannot check ")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()

    def write_conflicting_registry(self):
        registry = self.root / ".exactory/math-search.json"
        registry.parent.mkdir()
        identity = {"path": str(self.root / "attack"), "objective_id": "fixture", "contract_digest": "a" * 64}
        registry.write_text(json.dumps({"schema_version": 1, "sessions": {}, "roots": [
            identity, dict(identity, objective_id="conflicting-objective")]}))
        return registry

    def build_payload(self, tool, raw=None, **fields):
        return {"hook_event_name": "PreToolUse", "session_id": "fixture-session", "cwd": str(self.root),
                "tool_name": tool, "tool_input": raw or {}, **fields}

    def invoke_hook(self, payload=None, host="claude", stdin=None, injection=None):
        argv = [sys.executable, str(ROOT / "hooks/guard_attack_files.py")]
        if host == "codex":
            argv = [sys.executable, str(ROOT / "codex/hook.py"), "guard_attack_files.py"]
        if injection is not None:
            # Inject only the failing boundary, then execute the actual hook entrypoint.
            argv = [sys.executable, "-c", injection, str(ROOT)]
        result = subprocess.run(argv, input=json.dumps(payload) if stdin is None else stdin,
                                capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return result

    def assert_allows(self, result):
        self.assertEqual(result.stdout, "")

    def read_denial(self, result, guidance=True):
        self.assertTrue(result.stdout, "The hook allowed a possible write")
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")
        reason = decision["permissionDecisionReason"]
        if guidance:
            self.assertIn(GUIDANCE_MSG, reason)
        else:
            self.assertNotIn(GUIDANCE_MSG, reason)
        return reason

    def test_conflicting_root_identity_keeps_read_only_tools_working(self):
        registry = self.write_conflicting_registry()
        before = registry.read_bytes()
        for tool, raw in (
            ("Read", {"file_path": str(registry)}), ("Glob", {"pattern": "**/*"}),
            ("Grep", {"pattern": "fixture"}), ("WebSearch", {"query": "fixture"}),
            ("WebFetch", {"url": "https://example.invalid/fixture"}), ("ToolSearch", {"query": "Read"}),
        ):
            with self.subTest(tool=tool):
                self.assert_allows(self.invoke_hook(self.build_payload(tool, raw)))
        self.assertEqual(registry.read_bytes(), before)

    def test_conflicting_root_identity_keeps_read_only_shell_commands_working(self):
        self.write_conflicting_registry()
        for command in ("ls", "cat FILE", "ls | wc -l"):
            with self.subTest(command=command):
                self.assert_allows(self.invoke_hook(self.build_payload("Bash", {"command": command})))

    def test_conflicting_root_identity_denies_possible_writes_with_guidance(self):
        self.write_conflicting_registry()
        items = [("Write", {"file_path": "out"}), ("Edit", {"file_path": "out"}), ("Agent", {"prompt": "inspect"})]
        items += [("Bash", {"command": command}) for command in ("cat > out", "rm x", "ls *", "X=1 ls")]
        for tool, raw in items:
            with self.subTest(tool=tool, raw=raw):
                reason = self.read_denial(self.invoke_hook(self.build_payload(tool, raw)))
                self.assertIn("Recovery required before this managed operation", reason)
                self.assertIn("Conflicting registered root identity", reason)

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permission bits")
    def test_permission_error_allows_reads_and_denies_writes(self):
        blocked = self.root / "blocked"
        blocked.mkdir()
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o700)
        for tool, raw in (("Read", {"file_path": "notes.md"}), ("Bash", {"command": "ls"}),
                          ("Write", {"file_path": "notes.md"})):
            with self.subTest(tool=tool):
                result = self.invoke_hook(self.build_payload(tool, raw, cwd=str(blocked)))
                if tool == "Write":
                    reason = self.read_denial(result)
                    self.assertIn("Permission denied", reason)
                else:
                    self.assert_allows(result)

    def test_other_writer_runtime_allows_cat_and_names_both_runtimes_for_write(self):
        search = self.root / ".search"
        search.mkdir()
        fixture = ROOT / "tests/fixtures/math-search/standalone-finalize-begin.json"
        (search / "tree.json").write_bytes(fixture.read_bytes())
        writer = {"plugin_version": "0.64.0-local", "controller_digest": "f" * 64,
                  "launcher": str(self.root / "other runtime/bin/exactory-math")}
        (search / "writer-runtime.json").write_text(json.dumps(writer))
        self.assert_allows(self.invoke_hook(self.build_payload("Bash", {"command": "cat notes.md"})))
        reason = self.read_denial(self.invoke_hook(self.build_payload("Write", {"file_path": "notes.md"})))
        installed_version = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"]
        for text in ("cannot read a record written by", installed_version, writer["plugin_version"],
                     writer["controller_digest"], writer["launcher"], str(ROOT / "bin/exactory-math")):
            self.assertIn(text, reason)

    def test_read_only_batch_passes_but_mixed_batch_is_denied(self):
        self.write_conflicting_registry()
        reads = [self.build_payload("Read", {"file_path": "notes.md"}),
                 self.build_payload("Bash", {"command": "ls | wc -l"})]
        self.assert_allows(self.invoke_hook(self.build_payload("Read", _math_guard_batch=reads)))
        write = self.build_payload("Write", {"file_path": "out"})
        for items in (reads + [write], [write] + reads):
            with self.subTest(items=items):
                self.read_denial(self.invoke_hook(self.build_payload("Read", _math_guard_batch=items)))

    def test_empty_batch_is_denied_on_both_hosts(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": "/tmp/out", "content": "x"}, "cwd": 17, "_math_guard_batch": []}
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.read_denial(self.invoke_hook(payload, host=host))

    def test_invalid_json_is_denied(self):
        reason = self.read_denial(self.invoke_hook(stdin="{invalid"))
        self.assertIn("could not check this tool call", reason)

    def test_normalization_failure_is_denied_even_for_a_read_tool(self):
        for payload in (None, [], {"tool_name": "exec_command", "cwd": 17,
                                  "tool_input": {"cmd": "ls", "workdir": "."}}):
            with self.subTest(payload=payload):
                reason = self.read_denial(self.invoke_hook(payload))
                self.assertIn("could not check this tool call", reason)

    def test_definite_owned_file_write_keeps_its_denial_without_guidance(self):
        attack = self.root / "attack/sample"
        attack.mkdir(parents=True)
        (attack / "problem.json").write_text("{}")
        reason = self.read_denial(self.invoke_hook(self.build_payload(
            "Write", {"file_path": "attack/sample/journal.jsonl"})), guidance=False)
        self.assertIn("journal add sample", reason)

    def test_codex_exec_command_passes_and_apply_patch_is_denied_in_broken_state(self):
        self.write_conflicting_registry()
        self.assert_allows(self.invoke_hook(self.build_payload("exec_command", {"cmd": "ls"}), host="codex"))
        command = "*** Begin Patch\n*** Add File: out\n+content\n*** End Patch"
        self.read_denial(self.invoke_hook(self.build_payload("apply_patch", {"command": command}), host="codex"))

    def test_codex_empty_patch_is_denied_with_guidance(self):
        command = "*** Begin Patch\n*** End Patch"
        reason = self.read_denial(self.invoke_hook(self.build_payload("apply_patch", {"command": command}), host="codex"))
        self.assertEqual(reason, "[exactory] Cannot check this patch: The patch has no file targets. " + GUIDANCE_MSG)

    def test_tool_search_passes_inside_a_managed_root(self):
        # The managed marker is sufficient: a read must not need controller replay.
        (self.root / ".search").mkdir()
        (self.root / ".search/tree.json").write_text("{}")
        self.assert_allows(self.invoke_hook(self.build_payload("ToolSearch", {"query": "Read"})))

    def test_guidance_names_everything_that_still_passes(self):
        for name in math_search.READ_ONLY_TOOLS | math_search.READ_ONLY_FALLBACK_COMMANDS:
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", math_search.CANNOT_CHECK_GUIDANCE_MSG)

    def test_unsupported_managed_reasons_include_guidance(self):
        (self.root / ".search").mkdir()
        (self.root / ".search/tree.json").write_text("{}")
        for tool, raw in (("Agent", {"prompt": "inspect"}), ("Bash", {"command": "ls && tee out"})):
            with self.subTest(tool=tool):
                reason = self.read_denial(self.invoke_hook(self.build_payload(tool, raw)))
                self.assertIn("Unsupported managed", reason)

    def test_unparseable_managed_shell_includes_guidance(self):
        (self.root / ".search").mkdir()
        (self.root / ".search/tree.json").write_text("{}")
        expansion_msg = "Shell expansion and multiline programs are unsupported; use a direct command and a spec file"
        for command, diagnostic in (
            ("echo $(ls)", expansion_msg), ('ls "$TARGET"', expansion_msg), ("ls\npwd", expansion_msg),
            ("export PATH=/tmp/bin; ls", "Unsupported PATH bootstrap; use the documented direct command"),
            ("ls 'unterminated", "No closing quotation"),
            (None, "Unsupported shell payload; use a direct exec_command with cmd and workdir"),
        ):
            with self.subTest(command=command):
                reason = self.read_denial(self.invoke_hook(self.build_payload("Bash", {"command": command})))
                self.assertEqual(reason, "[math-solver] " + diagnostic + "; use a supported direct call. " + GUIDANCE_MSG)

    def test_guard_exceptions_use_the_read_only_fallback(self):
        injection = """
from pathlib import Path
import runpy
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(sys.argv[1]) / "hooks"))
import math_search
error = ERROR("injected check failure")
with patch.object(math_search, "guard", side_effect=error):
    runpy.run_path(str(Path(sys.argv[1]) / "hooks/guard_attack_files.py"), run_name="__main__")
"""
        for error in ("OSError", "ValueError", "KeyError", "TypeError", "math_search.ControllerReadError"):
            for tool in ("Read", "Write"):
                with self.subTest(error=error, tool=tool):
                    result = self.invoke_hook(self.build_payload(tool, {"file_path": "notes.md"}),
                                              injection=injection.replace("ERROR", error))
                    if tool == "Read":
                        self.assert_allows(result)
                    else:
                        self.read_denial(result)

    def test_later_check_exception_uses_the_whole_batch(self):
        injection = """
from pathlib import Path
import runpy
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(sys.argv[1]) / "hooks"))
import math_search
with patch.object(math_search, "guard", return_value=None):
    with patch.object(Path, "resolve", side_effect=RuntimeError("later check failure")):
        runpy.run_path(str(Path(sys.argv[1]) / "hooks/guard_attack_files.py"), run_name="__main__")
"""
        reads = [self.build_payload("Read"), self.build_payload("Bash", {"command": "ls"})]
        self.assert_allows(self.invoke_hook(self.build_payload("Read", _math_guard_batch=reads), injection=injection))
        reason = self.read_denial(self.invoke_hook(self.build_payload(
            "Read", _math_guard_batch=reads + [self.build_payload("Write", {"file_path": "out"})]), injection=injection))
        self.assertIn("could not check this tool call", reason)
        self.assertIn("later check failure", reason)


class ReadOnlyCallTests(unittest.TestCase):
    def test_accepts_literal_read_commands_and_supported_chains(self):
        for command in ("cat FILE", "ls", "grep pattern FILE", "head FILE", "tail FILE",
                        "wc -l FILE", "echo text", "du -sh .",
                        "ls ; echo text", "ls && echo text", "ls || echo text", "ls | wc -l"):
            with self.subTest(command=command):
                self.assertTrue(math_search.is_read_only({"tool_name": "Bash", "tool_input": {"command": command}}))

    def test_rejects_writing_ambiguous_and_unparseable_shell(self):
        for command in ("rg --pre command pattern", "rg --pre=command pattern", "rg --hostname-bin command pattern",
                        "rg --hostname-bin=command pattern", "tee out", "sed -i s/x/y/ FILE", "rm x",
                        "rg pattern .", "rg --p{re,re}=/bin/echo . /etc/hosts",
                        "rg '|' echo --pre=/bin/echo /etc/hosts", "rg . /etc/hosts", "rg -z", "rg -z . /etc/hosts",
                        "printf '%s' text", "printf -v PATH /no/such/path; ls", "printf 'hello%n' PATH", "pwd", "pwd -P",
                        "cat {a,b}", "cat file{", "cat file}",
                        "cat > out", "cat >> out", "cat >| out", "cat < FILE", "cat << EOF", "cat <<< text",
                        "ls &", "ls ;; pwd", "(ls)", "echo hi (rm x)", "ls *", "ls file?", "ls [ab]", "X=1 ls", "X=1",
                        "env ls", "env X=1 ls", "ls 'unterminated", "echo $(ls)", "ls\npwd", "ls\r\npwd",
                        "export PATH=/bin:$PATH; ls", "ls && rm x", "./work/cat", "/tmp/x/ls", "bin/grep", "/bin/ls",
                        "", "ls ;", "; ls", "ls ; ; pwd", None, 17):
            with self.subTest(command=command):
                self.assertFalse(math_search.is_read_only({"tool_name": "Bash", "tool_input": {"command": command}}))

    def test_rejects_ripgrep_with_configured_preprocessor(self):
        with tempfile.TemporaryDirectory(prefix="guard rg config ") as temporary:
            config = Path(temporary) / "ripgrep.conf"
            config.write_text("--pre=/bin/echo\n")
            with patch.dict(os.environ, {"RIPGREP_CONFIG_PATH": str(config)}):
                self.assertFalse(math_search.is_read_only({
                    "tool_name": "Bash", "tool_input": {"command": "rg . /etc/hosts"}}))

    def test_parser_exception_means_not_read_only(self):
        for function in ("shell_tokens", "iter_simple_commands"):
            with self.subTest(function=function), patch.object(math_search, function, side_effect=RuntimeError("parser")):
                self.assertFalse(math_search.is_read_only({"tool_name": "Bash", "tool_input": {"command": "ls"}}))


if __name__ == "__main__":
    unittest.main()
