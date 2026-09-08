#!/usr/bin/env python3
"""Shared host normalization and pointer discovery, without controller semantics.

Only reported direct file/shell operations are checked. Unreported nested tools
and arbitrary JavaScript or shell programs are not an enforcement boundary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import uuid

CONTROLLER_TIMEOUT = 20
LAUNCHER = Path(__file__).resolve().parents[1] / "bin/exactory-math"
_SHELL_NAMES = {"Bash", "exec_command", "functions.exec_command"}
_FILE_NAMES = {"Write", "Edit", "Delete", "Read"}


class ManagedError(ValueError):
    """A recognized managed operation needs explicit recovery or a direct call."""


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ManagedError("Duplicate JSON field: " + key)
        value[key] = item
    return value


def read_json(path):
    if path.is_symlink():
        raise ManagedError("Managed pointer cannot be a symlink: " + str(path))
    return json.loads(path.read_text(), object_pairs_hook=_unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ManagedError("Invalid JSON constant")))


def normalize(payload):
    result = dict(payload)
    tool = payload.get("tool_name")
    raw = payload.get("tool_input", {})
    if tool in _SHELL_NAMES and isinstance(raw, dict):
        command = raw.get("cmd", raw.get("command"))
        workdir = raw.get("workdir", payload.get("cwd") or ".")
        if isinstance(workdir, str):
            result["cwd"] = str(resolve(workdir, Path(payload.get("cwd") or ".")))
        result["tool_name"] = "Bash"
        result["tool_input"] = {"command": command}
    return result


def resolve(raw, cwd):
    # Keep lexical owned paths visible even if a target is a symlink.
    return Path(os.path.abspath(str(cwd / raw)))


def session_id(payload):
    identity = payload.get("session_id")
    return (payload.get("_math_host", "claude") + ":" + identity
            if isinstance(identity, str) and identity.strip() else None)


def shell_tokens(command):
    if not isinstance(command, str):
        raise ManagedError("Unsupported shell payload; use a direct exec_command with cmd and workdir")
    bootstrap = re.match(r'^\s*export PATH=([^;\n]+)[;\n]\s*', command)
    if bootstrap:
        value = bootstrap.group(1).replace('"', "").replace("'", "")
        if not value.endswith(":$PATH") or "$" in value[:-5] or "`" in value:
            raise ManagedError("Unsupported PATH bootstrap; use the documented direct command")
        command = command[bootstrap.end():]
    if any(character in command for character in ("\n", "\r", "$", "`")):
        raise ManagedError("Shell expansion and multiline programs are unsupported; use a direct command and a spec file")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def argument_data(tokens):
    """Locate interpreter source/module arguments without interpreting their code.

    A program passed to -c/-e is an argument value, never a filesystem operand.
    Other explicit paths and the actual working directory remain discoverable.
    Managed arbitrary code is still rejected by guard's supported-command check.
    """
    data, start = set(), 0
    separators = {";", "&&", "||", "|", "&"}
    while start < len(tokens):
        end = next((i for i in range(start, len(tokens)) if tokens[i] in separators), len(tokens))
        command = start
        while command < end and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[command]):
            command += 1
        if command < end and Path(tokens[command]).name == "env":
            command += 1
            while command < end and (tokens[command].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[command])):
                if tokens[command] in {"-u", "--unset", "-C", "--chdir"}:
                    command += 1
                command += 1
        if command < end:
            executable = Path(tokens[command]).name
            flags = ({"-c", "-m"} if re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", executable) else
                     {"-c"} if executable in {"sh", "bash", "zsh", "dash", "ksh"} else
                     {"-e", "--eval", "-p", "--print"} if executable in {"node", "nodejs"} else set())
            for position in range(command + 1, end):
                if tokens[position] in flags and position + 1 < end:
                    data.add(position + 1)
                    break
                if not tokens[position].startswith("-"):
                    break
        start = end + 1
    return data


def operation_paths(payload):
    tool = payload.get("tool_name")
    raw = payload.get("tool_input")
    cwd = Path(payload.get("cwd") or ".").resolve()
    if isinstance(raw, dict) and tool in _FILE_NAMES and isinstance(raw.get("file_path"), str):
        return [resolve(raw["file_path"], cwd)]
    if tool == "Bash" and isinstance(raw, dict):
        try:
            tokens = shell_tokens(raw.get("command"))
        except (ValueError, TypeError):
            return []
        paths = []
        data = argument_data(tokens)
        for index, token in enumerate(tokens):
            if index in data:
                continue
            if not token or token.startswith("-") or token in {";", "&&", "|", ">", ">>", "<"}:
                continue
            candidate = token.split("=", 1)[-1] if token.startswith(("of=", "if=")) else token
            if "\n" not in candidate and "\x00" not in candidate:
                paths.append(resolve(candidate, cwd))
        return paths
    return []


def _validate_identity(value):
    if (not isinstance(value, dict) or set(value) != {"path", "objective_id", "contract_digest"}
            or not all(isinstance(item, str) and item for item in value.values())
            or not Path(value["path"]).is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", value["contract_digest"])):
        raise ManagedError("Malformed registered root identity")


def discover(payload, paths=None):
    paths = operation_paths(payload) if paths is None else paths
    cwd = Path(payload.get("cwd") or ".").resolve()
    roots, registries, bindings, direct = {}, set(), [], set()
    starts = [cwd, *[path.parent for path in paths]]
    visited = set()
    for start in starts:
        for parent in (start, *start.parents):
            if parent in visited:
                continue
            visited.add(parent)
            if (parent / ".search/tree.json").exists() or (parent / ".search/tree.json").is_symlink():
                canonical = parent.resolve()
                roots.setdefault(canonical, None)
                direct.add(canonical)
            pointer = parent / ".exactory/math-search.json"
            explicit_registration = pointer in paths or pointer.with_suffix(".lock") in paths
            if not pointer.exists() and not pointer.is_symlink():
                continue
            if pointer.parent.is_symlink():
                if parent != cwd and not explicit_registration:
                    continue
                raise ManagedError("Discovery directory is a symlink; recover registration")
            try:
                value = read_json(pointer)
            except (OSError, ValueError):
                if parent != cwd and not explicit_registration:
                    continue
                raise
            if (not isinstance(value, dict) or set(value) != {"schema_version", "roots", "sessions"}
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or not isinstance(value["roots"], list) or not isinstance(value["sessions"], dict)):
                if parent != cwd and not explicit_registration:
                    continue
                raise ManagedError("Malformed discovery registry; recover registration")
            applicable = parent == cwd or explicit_registration or any(
                isinstance(item, dict) and isinstance(item.get("path"), str)
                and any(contains(Path(item["path"]), path) for path in [cwd, *paths])
                for item in value["roots"])
            if not applicable:
                continue
            registries.add(pointer)
            seen = set()
            for identity in value["roots"]:
                _validate_identity(identity)
                root = Path(identity["path"])
                if root in seen or root.resolve() != root or (root in roots and roots[root] not in (None, identity)):
                    raise ManagedError("Conflicting registered root identity")
                seen.add(root)
                roots[root] = identity
            for key, pointer_entry in value["sessions"].items():
                if (not isinstance(key, str) or not re.match(r"^(codex|claude):.+", key)
                        or not isinstance(pointer_entry, dict) or set(pointer_entry) != {"generation", "target"}
                        or not isinstance(pointer_entry["generation"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", pointer_entry["generation"])):
                    raise ManagedError("Malformed session discovery pointer")
                target = pointer_entry["target"]
                if target is not None:
                    if not isinstance(target, dict) or set(target) != {"root", "focus_request_id"}:
                        raise ManagedError("Malformed focus target")
                    _validate_identity(target["root"])
                    if target["root"] not in value["roots"] or not isinstance(target["focus_request_id"], str) or not target["focus_request_id"]:
                        raise ManagedError("Unregistered session focus target")
                if key == session_id(payload):
                    bindings.append(target)
    return {"roots": roots, "registries": registries, "bindings": bindings, "direct": direct}


def contains(root, path):
    return path == root or root in path.parents


def workspace_for(path):
    for directory in (path, *path.parents):
        if (directory / "problem.json").is_file() and (directory.parent.name == "attack"
                or (directory.parent / ".search/tree.json").exists()):
            return directory
    return None


def controller_call(root, command, spec=None, session=None, focus=None, identity=None):
    argv = [sys.executable, str(LAUNCHER), "--attack-root", str(root), "search", command, "--json"]
    with tempfile.TemporaryDirectory(prefix="math-hook-") as temporary:
        if spec is not None:
            path = Path(temporary) / "spec.json"
            path.write_text(json.dumps(spec))
            argv += ["--spec", str(path), "--internal", "--request-id", "hook-" + uuid.uuid4().hex]
        if session is not None:
            argv += ["--session-id", session]
        if focus is not None:
            argv += ["--focus-request-id", focus]
        if identity is not None:
            argv += ["--objective-id", identity["objective_id"], "--contract-digest", identity["contract_digest"]]
        try:
            process = subprocess.run(argv, capture_output=True, text=True, timeout=CONTROLLER_TIMEOUT)
        except subprocess.SubprocessError as error:
            raise ManagedError("Controller recovery required after bounded invocation failed: " + str(error)) from error
    if process.returncode:
        raise ManagedError("Controller recovery required: " + (process.stderr.strip() or "no diagnostic"))
    result = json.loads(process.stdout, object_pairs_hook=_unique)
    if not isinstance(result, dict):
        raise ManagedError("Controller returned an unsupported JSON response; recover the controller")
    return result


def selected_root(payload, discovery):
    if session_id(payload) is None:
        raise ManagedError("Explicit session focus is required; missing host identity cannot authorize continuation")
    bindings = discovery["bindings"]
    if bindings:
        first = bindings[0]
        if first is None or any(value != first for value in bindings):
            raise ManagedError("Session focus is ambiguous or cleared; issue explicit search focus")
        root = Path(first["root"]["path"])
        if discovery["direct"] - {root}:
            raise ManagedError("Session focus conflicts with the current root; issue explicit search focus")
        return root, first["focus_request_id"], first["root"]
    if len(discovery["direct"]) == 1:
        root = next(iter(discovery["direct"]))
        return root, None, discovery["roots"][root]
    raise ManagedError("Explicit session focus is required; no unique binding was registered")


def context(event, message):
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "[math-solver] " + message}}


def lifecycle(payload, stop=False):
    event = "Stop" if stop else "SessionStart"
    try:
        found = discover(payload)
        if not found["roots"]:
            return None
        root, focus, identity = selected_root(payload, found)
        session = session_id(payload)
        spec = None
        if stop:
            spec = {"delivery_id": payload.get("delivery_id"), "session_id": session,
                    "turn_id": payload.get("turn_id"), "stop_hook_active": payload.get("stop_hook_active")}
        result = controller_call(root, "hook-stop" if stop else "next", spec, session, focus, identity)
        if not stop:
            return context(event, "Resume /exactory:math-solver at stage 0 from controller next: " + json.dumps(result))
        decision = result["decision"]
        if decision["kind"] == "continue":
            return {"decision": "block", "reason": "[math-solver] Continue the focused objective from controller next: " + json.dumps(decision["action"])}
        if decision["kind"] == "summary_then_stop":
            return {"decision": "block", "reason": "[math-solver] Objective continuation safety cap reached. Summarize progress once, then stop. Explicit operator search resume is required."}
        return None
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return context(event, "Recovery or explicit focus is required: " + str(error))


def guard(payload):
    """Return a managed denial reason, or None; legacy gates handle unmanaged nodes."""
    payload = normalize(payload)
    tool = payload.get("tool_name")
    paths = operation_paths(payload)
    found = discover(payload, paths)
    cwd = Path(payload.get("cwd") or ".").resolve()
    raw = payload.get("tool_input")
    relevant = {root for root in found["roots"] if any(contains(root, path) for path in paths)}
    visible = json.dumps(raw)
    visible_roots = {root for root in found["roots"] if str(root) in visible
                     or os.path.relpath(root, cwd) + "/" in visible}
    if tool in {"Read", "Glob", "Grep", "WebSearch", "WebFetch"}:
        return None
    if tool not in _FILE_NAMES | {"Bash"}:
        if visible_roots or any(contains(root, cwd) for root in found["roots"]):
            return "Unsupported managed payload; use a direct apply_patch or exec_command so its targets can be checked"
        return None
    writing = tool in {"Write", "Edit", "Delete"}
    if tool == "Bash":
        try:
            tokens = shell_tokens(raw.get("command") if isinstance(raw, dict) else None)
        except ValueError as error:
            if relevant or found["direct"] or visible_roots:
                return str(error) + "; use a supported direct call"
            return None
        if not tokens:
            return None
        executable = Path(tokens[0]).name
        if executable in {"python", "python3"} and len(tokens) > 1 and Path(tokens[1]).name in {"exactory-math", "attack.py"}:
            executable = "exactory-math"
        writing = (executable in {"tee", "cp", "mv", "rm", "truncate", "dd"}
                   or any(token in {">", ">>", ">|"} for token in tokens)
                   or (executable == "sed" and any(token.startswith("-i") for token in tokens[1:])))
        simple = not any(token in {";", "&&", "||", "|", "&", "<<", "<<<"} for token in tokens)
        supported = {"exactory-math", "attack.py", "cat", "ls", "rg", "grep", "head", "tail", "wc", "pwd",
                     "printf", "echo", "tee", "cp", "mv", "rm", "truncate", "dd", "sed"}
        if (relevant or found["direct"]) and (not simple or executable not in supported):
            return "Unsupported managed shell operation; use a direct supported command or controller search run"
    if writing:
        for path in paths:
            if any(path in {pointer, pointer.with_suffix(".lock")} or contains(path, pointer)
                   for pointer in found["registries"]):
                return "Discovery pointers and locks are owned by search init/focus/resume; use the controller command"
            for root in relevant:
                if not contains(root, path):
                    continue
                relative = path.relative_to(root).as_posix()
                if (relative == "." or relative == ".search" or relative.startswith(".search/")
                        or relative == "SEARCH_TREE.md" or re.fullmatch(r"[^/]+/LINEAGE\.md", relative)):
                    return "Controller snapshots, artifacts, and generated views are owned by search commands; use the controller"
                workspace = workspace_for(path)
                if workspace is not None:
                    native = path.relative_to(workspace).as_posix()
                    if native == "." or re.fullmatch(
                            r"journal\.jsonl|openings\.json|parent\.json|tasks\.json|activity\.jsonl|"
                            r"deterministic/[^/]+/(result\.json|inspection\.log|axioms-check\.lean)|"
                            r"units/\d+/check-unit\.json|units/FINISHED\.json", native):
                        return "Native records are owned by their validated exactory-math commands; use the direct controller workflow"
    for root in relevant:
        controller_call(root, "status", identity=found["roots"][root])
    return None
