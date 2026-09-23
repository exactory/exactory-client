"""Supported standard-library CLI over the accepted research domain operations."""

import argparse
import json
from pathlib import Path
import sys
import time

from . import (acquisition, challenge, cohort_evidence, contribution, development, graph, lineage, literature, predictions,
                principles, reading, resources, rounds, sampling, screening, synthesis, visual_assets)
from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .gates import gate_report, gate_state, require_ready
from .integration import adopt_workspace, current_store, export_workspace, pin_artifact
from .limits import limits_report
from .operations import fields
from .provenance import runtime_provenance
from .report_views import current_obligations, next_summary, obligations_page, order_obligations, status_summary
from .storage import Store
from .source_deferrals import defer_source, resume_source, assess_deferrals
from .publication_scope import record_publication_scope, select_publication_scope, record_scoped_readiness_review
from .workspace import find_workspace, strict_json


OPERATIONS = {
    "init": principles.initialize_research,
    "adopt": adopt_workspace,
    "artifact": pin_artifact,
    "target": principles.set_target,
    "constitution": principles.revalidate_constitution,
    "policy": principles.change_policy,
    "roots": graph.set_roots,
    "bundle": literature.import_bundle,
    "read": reading.record_reading,
    "read-batch": reading.record_reading_batch,
    "search": literature.record_search,
    "loop-close": lineage.record_loop_closure,
    "require-fulltext": reading.require_fulltext,
    "availability": reading.record_availability,
    "defer-source": defer_source,
    "resume-source": resume_source,
    "select-cohort-abstract": cohort_evidence.select_cohort_abstract,
    "sample": sampling.record_sample,
    "standards": synthesis.record_standards,
    "rationale": synthesis.record_rationale,
    "innovation": synthesis.record_innovation,
    "context": synthesis.record_context,
    "grand-challenge": challenge.record_grand_challenge,
    "cycle": development.plan_cycle,
    "admit": development.admit_execution,
    "assess": development.assess_cycle,
    "checkpoint": development.checkpoint,
    "review": development.record_readiness_review,
    "publication-scope": record_publication_scope,
    "select-publication-scope": select_publication_scope,
    "scoped-review": record_scoped_readiness_review,
    "budget": resources.set_budget,
    "screen-batch": screening.record_screening_batch,
    "screening-checkpoint": screening.record_screening_checkpoint,
    "round": rounds.record_round,
    "round-review": rounds.record_round_review,
    "round-admit": rounds.admit_round,
    "round-assess": rounds.assess_round,
    "manuscript-prediction": predictions.record_prediction,
    "contribution-analysis": contribution.record_contribution_analysis,
}

from .execution import bind_execution, reconcile_execution, record_imported_execution
OPERATIONS.update({"bind-run": bind_execution, "reconcile-run": reconcile_execution, "result": record_imported_execution})
from .publication import prepare_publication, record_manuscript_review
from .verification import bind_verdict
OPERATIONS.update({"manuscript": prepare_publication, "manuscript-review": record_manuscript_review, "bind-verdict": bind_verdict})

ACQUISITION = ("collect", "resume", "acquire", "expand", "fulltext", "import-response", "import-oai-cohort", "visual")
GATES = ("cohort", "foundation", "preparation", "readiness", "manuscript-readiness", "execution", "verification", "manuscript", "publication", "deposited", "submitted", "round")


def add_identity(parser, *, required=True):
    parser.add_argument("--expected-revision", type=int, required=required,
                        help="Revision from current status; conflicting new mutations fail.")
    parser.add_argument("--request-id", required=required,
                        help="Stable unique request identity; identical retries return the original receipt.")


def note_managed_record_skipped(error):
    """One stderr line: the command ran, and the study's receipt was not written.

    A readiness refusal carries its obligations; their codes name what the study still owes."""
    pending = [item["code"] for item in (error.details or {}).get("obligations", [])]
    suffix = " Pending: " + ", ".join(pending) + "." if pending else ""
    print("Managed record skipped (" + error.code + "): " + error.message + suffix, file=sys.stderr)


def add_managed_scope(parser):
    parser.add_argument("--managed-only", action="store_true", help="Fail before remote writes unless current managed publication checks pass.")
    parser.add_argument("--scope-contract-id", help="Exact intended selected source-limited contract.")
    parser.add_argument("--scope-target-digest", help="Exact intended scientific target SHA-256.")


def parse_managed_scope_arguments(args):
    strict = getattr(args, "managed_only", False)
    contract, target = getattr(args, "scope_contract_id", None), getattr(args, "scope_target_digest", None)
    if bool(contract) != bool(target) or (contract is not None and not strict):
        raise ResearchError("managed_scope_arguments", "Both scope arguments require each other and --managed-only")
    if target is not None and (len(target) != 64 or any(c not in "0123456789abcdef" for c in target)):
        raise ResearchError("managed_scope_arguments", "Supply the exact lowercase scientific target SHA-256")
    return {"managed_only": strict, "scope_contract_id": contract, "scope_target_digest": target}


def select_managed_path(check, start=None, *, managed_only=False, scope_contract_id=None, scope_target_digest=None):
    """Return (store, check(store), store_open_error) for the workspace around `start`.

    `check` runs every check the managed path performs before its first remote
    write and returns what the managed path needs. A refused check returns
    (store, None, None), so a command that writes its own record on the direct
    path reuses the store this call already opened instead of deciding again. No
    workspace, no store file, or a workspace outside the research contract
    returns (None, None, None) silently. A store file that exists and does not
    open returns its ResearchError as the third value, because a record the
    command keeps elsewhere is then the only copy of it. Every refusal except
    the silent ones is noted on stderr once, and the ordinary command sends the
    user's request directly. With managed_only, every refusal propagates and
    the command never selects the direct path."""
    workspace = find_workspace(start, required=False)
    if workspace is None:
        if managed_only:
            raise ResearchError("managed_workspace_required", "Managed-only publication requires an existing research workspace")
        return None, None, None
    try:
        store = current_store(workspace)
    except ResearchError as error:
        if managed_only:
            raise
        if error.code == "migration_required":
            return None, None, None
        note_managed_record_skipped(error)
        return None, None, error
    try:
        if scope_contract_id is not None:
            from .publication_scope import validate_expected_scope
            validate_expected_scope(store, scope_contract_id, scope_target_digest)
        return store, check(store), None
    except ResearchError as error:
        if managed_only:
            raise
        note_managed_record_skipped(error)
        return store, None, None


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
    for command in ("status", "next", "obligations", "batches", "population-query", "policy-report", "gate", "export", "recover"):
        item = commands.add_parser(command, allow_abbrev=False,
            help={"status": "Read current obligations and source paths.", "next": "Read actionable current preparation obligations.",
                  "obligations": "Read one revision-bound page of the obligations that carry a code.",
                  "batches": "Write the current unread abstracts as reader batch files without changing the store.",
                  "population-query": "Rank the enumerated population's stored abstracts by query terms without changing the store.",
                  "policy-report": "Describe the preparation set under the recorded or a hypothetical policy, without changing the store.",
                  "gate": "Validate a current gate without mutation.", "export": "Rebuild disposable projections or deliver exact reviewer bytes.",
                  "recover": "Explicitly recover a hot SQLite journal without migrating or certifying research."}[command])
        item.add_argument("--workspace", default=".")
        if command in ("status", "next"):
            item.add_argument("--summary", action="store_true",
                              help="Return a bounded advisory view; the same current evaluation still runs.")
        if command == "obligations":
            item.add_argument("--code", required=True, help="Obligation code to list, for example abstract_reading_missing.")
            item.add_argument("--limit", type=int, default=50, help="Obligations per page, 1 to 500.")
            item.add_argument("--cursor", help="REVISION:OFFSET from the previous page; fails when the store changed.")
        if command == "batches":
            item.add_argument("--depth", choices=("abstract",), default="abstract")
            item.add_argument("--size", type=int, default=60, help="Items per batch file, 1 to 100.")
            item.add_argument("--destination", required=True, help="New directory for batch-NNN.json files.")
            item.add_argument("--profile", choices=("research", "verification"), help="Defaults to the configured profile.")
            item.add_argument("--screen", action="store_true", help="Export the unscreened members for screen-batch instead of unread abstracts.")
            item.add_argument("--loop", action="store_true", help="Export the selected searches' found works without a loop reading, plus --candidates.")
            item.add_argument("--candidates", help="JSON file: either a list of version ids or a population-query result.")
        if command == "population-query":
            item.add_argument("--terms", nargs="+", required=True, help="Query terms taken from the target statement and the parent's title and abstract.")
            item.add_argument("--limit", type=int, default=30, help="Matches to return, 1 to 100.")
        if command == "policy-report":
            item.add_argument("--policy", choices=principles.POLICIES, help="Report a hypothetical policy instead of the recorded one.")
            item.add_argument("--reference", help="JSON file {prior_art, contradictions, methods, doctrine} of family ids for recall.")
        if command == "gate":
            item.add_argument("action", choices=GATES)
        if command == "export":
            item.add_argument("--kind", choices=("workspace", "foundation", "readiness", "manuscript", "round", "native"), default="workspace")
            item.add_argument("--destination", help="New independent reviewer directory for the readiness, manuscript or round packet and its actual source bytes.")
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
    if command == "import-oai-cohort":
        fields(payload, ("collection_id", "pages"))
        return acquisition.import_oai_cohort(store, **payload, **identity)
    if command == "resume":
        fields(payload, ("collection_id",), ("max_requests",))
        return acquisition.resume_cohort(store, **payload, **identity)
    if command in ("acquire", "expand"):
        fields(payload, ("identifier",), ("provider", "max_requests"))
        # Expansion acquires a named unresolved graph target through the same
        # durable metadata collector. It does not declare its bibliography read.
        return acquisition.acquire_work(store, **payload, **identity)
    if command == "fulltext":
        fields(payload, ("identifier", "url"), ("max_requests", "extraction_options", "component"))
        return acquisition.acquire_fulltext(store, **payload, **identity)
    if command == "visual":
        fields(payload, ("link", "url"), ("max_requests",))
        return visual_assets.acquire_visual_asset(store, **payload, **identity)
    fields(payload, ("provider", "response_file", "source_url", "captured_at"), ("media_type", "mappings"))
    values = dict(payload)
    values["response"] = Path(values.pop("response_file")).read_bytes()
    return acquisition.import_response(store, **values, **identity)


def status_report(store, *, counters=False):
    started = time.monotonic()
    snapshot = store.snapshot()
    records = snapshot["records"]
    evaluation = Evaluation(records, ArtifactStore(store.root))
    config = records.get("configuration", {}).get("research")
    profile = config["profile"] if config else "research"
    study = records.get("workspace", {}).get("study")
    action = "verification" if profile == "verification" else "readiness"
    from .publication_scope import has_publication_scope, assess_manuscript_readiness
    scoped_report = assess_manuscript_readiness(records, evaluation) if has_publication_scope(records) else None
    report = scoped_report if scoped_report is not None and action == "readiness" else gate_state(records, evaluation, action, profile=profile)
    round_report = None
    if config is not None and profile == "research":
        from .rounds import round_state, round_summary
        round_report = round_state(records, evaluation)
        if study and study["stage"] == "evaluate":
            # At evaluate the study is led first through the manuscript measurement, then to the round decision.
            from .publication import publication_state
            merged = report["obligations"] + publication_state(records, evaluation, "publication")["obligations"] + round_report["obligations"]
            obligations = order_obligations({digest(o): o for o in merged}.values())
            report = dict(report, ready=not obligations, obligations=obligations, counts=dict(report["counts"], obligations=len(obligations)))
    # Before later gates are applicable, expose the actual next unread cohort
    # abstract instead of asking for a root or completed development too early.
    preparation = gate_state(records, evaluation, "cohort" if study and study["stage"] == "cohort" else "preparation", profile=profile)
    diagnostics = {"evaluation": dict(evaluation.counters, elapsed_seconds=round(time.monotonic() - started, 3))} if counters else {}
    # The cohort inventory names the next unread abstract; every other stage
    # takes the highest-priority current obligation in preparation order.
    upcoming = preparation.get("next") if study and study["stage"] == "cohort" else None
    obligations = current_obligations({"obligations": report["obligations"], "preparation": preparation})
    current_challenge = challenge.find_current_challenge(records) if profile == "research" else None
    scope_status = {}
    if scoped_report is not None:
        scope_status = {key: scoped_report[key] for key in ("manuscript_ready", "manuscript_obligations", "objective_complete",
                                                           "objective_obligations", "scientific_target_digest", "remaining_obligations")}
        scope_status["scope_contract_id"] = scoped_report["selected_contract_id"]
        scope_status["historical_publications"] = list(records.get("publication_receipt", {}).values())
        scope_status["historical_submissions"] = list(records.get("submission_receipt", {}).values())
        if scoped_report["contract"] is not None:
            flags = ["--managed-only", "--scope-contract-id", scoped_report["contract"]["id"],
                     "--scope-target-digest", scoped_report["scientific_target_digest"]]
            scope_status["publication_commands"] = {"deposit": ["exactory-draft", "deposit"] + flags,
                                                    "submit": ["exactory", "submit"] + flags}
    return dict(report, revision=snapshot["revision"], profile=profile, runtime=runtime_provenance(),
                source_deferrals=assess_deferrals(records, evaluation, profile),
                deferred_obligations=literature.foundation_state(records, evaluation, profile).get("deferred_obligations", []),
                study=study, preparation=preparation, resources=resources.account_report(records, profile),
                limits=limits_report(records), **diagnostics, **scope_status,
                round=round_summary(round_report) if round_report else None,
                grand_challenge=current_challenge["payload"] if current_challenge else None,
                next=upcoming or (order_obligations(obligations)[0] if obligations else None),
                pending_executions=[key for key in records.get("execution_admission", {}) if key not in records.get("execution_outcome", {})],
                remote_intents=list(records.get("remote_intent", {}).values()),
                remote_observations=list(records.get("remote_observation", {}).values()),
                historical_adoptions=list(records.get("workspace_adoption", {}).values()))


def load_candidates(path):
    """Read a --candidates file as version ids: a population-query result's matches, or a plain list of ids."""
    loaded = strict_json(Path(path).read_bytes())
    if isinstance(loaded, dict):
        matches = loaded.get("matches")
        if isinstance(matches, list) and all(isinstance(m, dict) and isinstance(m.get("version_id"), str) for m in matches):
            return [m["version_id"] for m in matches]
    elif isinstance(loaded, list) and all(isinstance(item, str) for item in loaded):
        return loaded
    raise ResearchError("invalid_input", "The candidates file is a population-query result or a list of version ids")


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
    elif args.command == "import-oai-cohort":
        root = find_workspace(root)
        store = Store(root)
    elif args.command in ACQUISITION:
        existing = find_workspace(root, required=False)
        store = Store(existing) if existing is not None else Store(root, create=True)
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
        report = status_report(store, counters=args.summary)
        if args.summary:
            return (next_summary if args.command == "next" else status_summary)(report)
        return report
    if args.command == "obligations":
        return obligations_page(status_report(store), args.code, limit=args.limit, cursor=args.cursor)
    if args.command == "batches":
        from .batches import export_batches
        if args.candidates and not args.loop:
            raise ResearchError("invalid_input", "--candidates needs --loop")
        return export_batches(store, depth=args.depth, size=args.size, destination=args.destination, profile=args.profile,
                              screen=args.screen, loop=args.loop,
                              candidates=load_candidates(args.candidates) if args.candidates else ())
    if args.command == "population-query":
        return lineage.population_query(store, args.terms, limit=args.limit)
    if args.command == "policy-report":
        reference = strict_json(Path(args.reference).read_bytes()) if args.reference else None
        return screening.policy_report(store, policy=args.policy, reference=reference)
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
    from .review_delivery import deliver_manuscript, deliver_readiness, deliver_round
    if not args.destination:
        raise ResearchError("invalid_input", "Reviewer delivery requires --destination for the separate reviewer")
    deliver = {"readiness": deliver_readiness, "manuscript": deliver_manuscript, "round": deliver_round}[args.kind]
    return deliver(store, Path(args.destination))


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
