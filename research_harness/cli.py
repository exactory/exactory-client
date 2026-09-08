"""Supported standard-library CLI over the accepted research domain operations."""

import argparse
import json
from pathlib import Path
import sys

from . import acquisition, cohort_evidence, development, graph, literature, principles, reading, synthesis, visual_assets
from .artifacts import ArtifactStore
from .errors import ResearchError
from .gates import gate_report, gate_state, require_ready
from .integration import adopt_workspace, current_store, export_workspace, pin_artifact
from .operations import fields
from .storage import Store
from .workspace import find_workspace, strict_json


OPERATIONS = {
    "init": principles.initialize_research,
    "adopt": adopt_workspace,
    "artifact": pin_artifact,
    "target": principles.set_target,
    "constitution": principles.revalidate_constitution,
    "roots": graph.set_roots,
    "bundle": literature.import_bundle,
    "read": reading.record_reading,
    "search": literature.record_search,
    "require-fulltext": reading.require_fulltext,
    "availability": reading.record_availability,
    "select-cohort-abstract": cohort_evidence.select_cohort_abstract,
    "standards": synthesis.record_standards,
    "rationale": synthesis.record_rationale,
    "innovation": synthesis.record_innovation,
    "context": synthesis.record_context,
    "cycle": development.plan_cycle,
    "admit": development.admit_execution,
    "assess": development.assess_cycle,
    "checkpoint": development.checkpoint,
    "review": development.record_readiness_review,
}

from .execution import bind_execution, reconcile_execution, record_imported_execution
OPERATIONS.update({"bind-run": bind_execution, "reconcile-run": reconcile_execution, "result": record_imported_execution})
from .publication import prepare_publication, record_manuscript_review
from .verification import bind_verdict
OPERATIONS.update({"manuscript": prepare_publication, "manuscript-review": record_manuscript_review, "bind-verdict": bind_verdict})

ACQUISITION = ("collect", "resume", "acquire", "expand", "fulltext", "import-response", "visual")
GATES = ("cohort", "foundation", "preparation", "readiness", "execution", "verification", "manuscript", "publication", "deposited", "submitted")


def add_identity(parser, *, required=True):
    parser.add_argument("--expected-revision", type=int, required=required,
                        help="Revision from current status; conflicting new mutations fail.")
    parser.add_argument("--request-id", required=required,
                        help="Stable unique request identity; identical retries return the original receipt.")


def build_parser():
    parser = argparse.ArgumentParser(prog="exactory-research", allow_abbrev=False,
        description="Acquire, read, prepare and develop research with current evidence gates.",
        epilog="JSON schemas and preparation order: docs/research-cli.md in the installed plugin.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in (*OPERATIONS, *ACQUISITION):
        item = commands.add_parser(command, allow_abbrev=False,
            help="Submit the exact " + command + " JSON payload.",
            description="Use --file with the typed JSON shape in docs/research-cli.md. Saved assertions and downloads are not successful readings.")
        item.add_argument("--workspace", default=".")
        item.add_argument("--file", required=True, help="UTF-8 JSON input, without duplicate keys or nonfinite numbers.")
        add_identity(item)
    for command in ("status", "next", "gate", "export", "recover"):
        item = commands.add_parser(command, allow_abbrev=False,
            help={"status": "Read current obligations and source paths.", "next": "Read actionable current preparation obligations.",
                  "gate": "Validate a current gate without mutation.", "export": "Rebuild disposable projections or deliver exact reviewer bytes.",
                  "recover": "Explicitly recover a hot SQLite journal without migrating or certifying research."}[command])
        item.add_argument("--workspace", default=".")
        if command == "gate":
            item.add_argument("action", choices=GATES)
        if command == "export":
            item.add_argument("--kind", choices=("workspace", "foundation", "readiness", "manuscript", "native"), default="workspace")
            item.add_argument("--destination", help="New independent reviewer directory for readiness inputs and actual source bytes.")
            item.add_argument("--attack-root", help="Native attack root for an immutable foundation delivery.")
            item.add_argument("--claim-binding", help="JSON source correspondence for an external native verification claim.")
        if command == "recover":
            item.add_argument("--expected-revision", type=int, help="Optional check against the restored committed revision.")
    example = commands.add_parser("example", allow_abbrev=False,
        help="Print an illustrative exact JSON payload without opening a workspace.")
    example.add_argument("operation", choices=sorted((*OPERATIONS, *ACQUISITION)))
    return parser


def acquisition_command(store, command, payload, identity):
    if command == "collect":
        fields(payload, ("definition",), ("max_requests", "page_size"))
        return acquisition.collect_cohort(store, **payload, **identity)
    if command == "resume":
        fields(payload, ("collection_id",), ("max_requests",))
        return acquisition.resume_cohort(store, **payload, **identity)
    if command in ("acquire", "expand"):
        fields(payload, ("identifier",), ("provider", "max_requests"))
        # Expansion acquires a named unresolved graph target through the same
        # durable metadata collector. It does not declare its bibliography read.
        return acquisition.acquire_work(store, **payload, **identity)
    if command == "fulltext":
        fields(payload, ("identifier", "url"), ("max_requests",))
        return acquisition.acquire_fulltext(store, **payload, **identity)
    if command == "visual":
        fields(payload, ("link", "url"), ("max_requests",))
        return visual_assets.acquire_visual_asset(store, **payload, **identity)
    fields(payload, ("provider", "response_file", "source_url", "captured_at"), ("media_type", "mappings"))
    values = dict(payload)
    values["response"] = Path(values.pop("response_file")).read_bytes()
    return acquisition.import_response(store, **values, **identity)


def status_report(store):
    snapshot = store.snapshot()
    records = snapshot["records"]
    config = records.get("configuration", {}).get("research")
    profile = config["profile"] if config else "research"
    study = records.get("workspace", {}).get("study")
    action = "verification" if profile == "verification" else "readiness"
    report = gate_state(records, ArtifactStore(store.root), action, profile=profile)
    # Before later gates are applicable, expose the actual next unread cohort
    # abstract instead of asking for a root or completed development too early.
    preparation = gate_state(records, ArtifactStore(store.root), "cohort" if study and study["stage"] == "cohort" else "preparation", profile=profile)
    obligations = preparation["obligations"] or report["obligations"]
    return dict(report, revision=snapshot["revision"], profile=profile,
                study=study, preparation=preparation,
                next=preparation.get("next") or (obligations[0] if obligations else None),
                pending_executions=[key for key in records.get("execution_admission", {}) if key not in records.get("execution_outcome", {})],
                remote_intents=list(records.get("remote_intent", {}).values()),
                remote_observations=list(records.get("remote_observation", {}).values()),
                historical_adoptions=list(records.get("workspace_adoption", {}).values()))


def run(args):
    if args.command == "example":
        examples = strict_json((Path(__file__).resolve().parents[1] / "docs/research-cli-examples.json").read_bytes())
        return examples[args.operation]
    root = Path(args.workspace).absolute()
    if args.command in ("init", "adopt"):
        root = find_workspace(root, required=False) or root
        store = Store(root, create=True)
    elif args.command == "recover":
        root = find_workspace(root)
        if not (root / ".exactory/research.sqlite3").exists():
            raise ResearchError("migration_required", "No existing research store to recover; use explicit adoption")
        store = Store(root, create=True)
        revision = store.revision
        if args.expected_revision is not None and args.expected_revision != revision:
            raise ResearchError("stale_revision", "Recovered revision differs from the expected committed revision", {"revision": revision})
        return {"revision": revision, "recovered": True, "scientific_validation": False, "schema_migrated": False}
    else:
        root = find_workspace(root)
        store = Store(root)
    if args.command in OPERATIONS or args.command in ACQUISITION:
        payload = strict_json(Path(args.file).read_bytes())
        identity = {"expected_revision": args.expected_revision, "request_id": args.request_id}
        if args.command in OPERATIONS:
            return OPERATIONS[args.command](store, payload, **identity)
        return acquisition_command(store, args.command, payload, identity)
    if args.command in ("status", "next"):
        return status_report(store)
    if args.command == "gate":
        report = gate_report(store, args.action)
        require_ready(report, args.action)
        return report
    if args.kind == "workspace":
        return export_workspace(store)
    if args.kind == "foundation":
        profile = store.snapshot()["records"].get("configuration", {}).get("research", {}).get("profile", "research")
        return literature.export_foundation(store, profile)
    if args.kind == "native":
        from .native_export import export_native
        if not args.destination or not args.attack_root:
            raise ResearchError("invalid_input", "Native export requires --attack-root and a new --destination")
        binding = strict_json(Path(args.claim_binding).read_bytes()) if args.claim_binding else None
        return export_native(store, Path(args.attack_root), Path(args.destination), claim_binding=binding)
    from .review_delivery import deliver_readiness, deliver_manuscript
    if not args.destination:
        raise ResearchError("invalid_input", "Readiness delivery requires --destination for the separate reviewer")
    return (deliver_manuscript if args.kind == "manuscript" else deliver_readiness)(store, Path(args.destination))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except ResearchError as error:
        print(json.dumps({"error": error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": {"code": "input_io", "message": str(error)}}), file=sys.stderr)
        raise SystemExit(1)
