"""Thin argparse adapter for the authoritative search service."""

import argparse
import json
from pathlib import Path
import sys

from .errors import SearchError
from .storage import _strict_json


class SearchParser(argparse.ArgumentParser):
    def error(self, message):
        raise SearchError("invalid_command", message)


def print_error(error):
    print(json.dumps({"error": {"code": error.code, "message": error.message,
                                "details": error.details}}, ensure_ascii=False), file=sys.stderr)


def run_search(args):
    from .service import Controller
    controller = Controller(args.attack_root, args.strategies)
    spec = {}
    if getattr(args, "spec", None) is not None:
        try:
            spec = _strict_json(args.spec.read_bytes(), "invalid_input")
        except OSError as error:
            raise SearchError("invalid_input", "Cannot read the command spec", str(error)) from error
    validation = {key: getattr(args, key, None) for key in
                  ("session_id", "focus_request_id", "objective_id", "contract_digest")}
    requested_validation = any(value is not None for value in validation.values())
    internal = getattr(args, "internal", False)
    revision = getattr(args, "expected_revision", None)
    if args.search_command == "hook-stop":
        if internal:
            if not isinstance(spec, dict) or spec.get("session_id") != validation["session_id"]:
                raise SearchError("focus_required", "Internal Stop must bind its normalized session")
            revision = controller.status()["revision"] if revision is None else revision
        elif revision is None:
            raise SearchError("invalid_command", "Public hook-stop requires --expected-revision")
    elif args.search_command in {"status", "next"} and requested_validation:
        spec = validation
    result = controller.command(args.search_command, spec,
                                revision,
                                getattr(args, "request_id", None), getattr(args, "target", None),
                                workspace_root=getattr(args, "workspace_root", None),
                                hook_session=validation if internal else None)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=None if args.json else 2))


def install_parser(commands, strategies_default):
    search = commands.add_parser("search", help="manage one persistent mathematical objective")
    nested = search.add_subparsers(dest="search_command", required=True, parser_class=SearchParser)
    readonly = {"status", "next"}
    specs = {"init", "adopt", "propose", "review", "begin", "run", "checkpoint", "accept",
             "retreat", "replan", "focus", "pause", "resume", "hook-stop", "complete", "amend-computation", "interpret"}
    targets = {"admit", "begin", "run", "accept", "retreat", "amend-computation", "interpret"}
    for name in ["init", "adopt", "propose", "review", "admit", "begin", "run", "reconcile",
                 "checkpoint", "accept", "retreat", "replan", "focus", "pause", "resume",
                 "audit", "status", "next", "hook-stop", "render", "complete", "amend-computation", "interpret"]:
        parser = nested.add_parser(name)
        if name in {"init", "focus", "resume"}:
            parser.add_argument("--workspace-root", type=Path)
        if name in {"status", "next", "hook-stop"}:
            for option in ("session-id", "focus-request-id", "objective-id", "contract-digest"):
                parser.add_argument("--" + option)
        if name == "hook-stop":
            parser.add_argument("--internal", action="store_true", help="validate a host Stop in one controller process")
        if name in targets:
            parser.add_argument("target")
        elif name == "checkpoint":
            parser.add_argument("target", nargs="?")
        if name in specs:
            parser.add_argument("--spec", type=Path, required=True)
        elif name == "admit":
            parser.add_argument("--spec", type=Path)
        if name not in readonly:
            parser.add_argument("--expected-revision", type=int, required=name != "hook-stop")
            parser.add_argument("--request-id", required=True)
        parser.add_argument("--json", action="store_true")
        parser.set_defaults(run=run_search)
