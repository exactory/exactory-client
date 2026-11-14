"""Supported standard-library CLI over the accepted research domain operations."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import time

from . import (acquisition, challenge, cohort_evidence, contribution, development, graph, lineage, literature, predictions,
                principles, reading, resources, rounds, sampling, screening, synthesis, visual_assets)
from .artifacts import ArtifactStore
from . import fulltext_jobs
from .import_io import read_import_input
from .check_mode import check_mutation
from .drafting import find_draft_obligations
from .errors import ResearchError, describe_tool_timeout
from .evaluation import Evaluation
from .evidence import digest
from .gates import gate_state, require_ready
from .integration import (adopt_workspace, current_store, export_workspace, matches_direct_deposit_files,
                          pin_artifact)
from .limits import describe_depth, limits_report
from .operations import fields, text
from .output_views import (PROBLEM_EXPORTS_FOLDER, find_view_advisories, find_workspace_root, render_all,
                           render_views_after_command)
from .report_views import (annotate_parts_obligations, current_obligations, next_summary, obligations_page,
                           order_obligations, status_summary)
from .storage import Store, check_command_bounds
from .source_deferrals import defer_source, resume_source, assess_deferrals
from .publication_scope import record_publication_scope, select_publication_scope, record_scoped_readiness_review
from .workspace import find_workspace, strict_json
from . import research_decisions, review_protocol, strategy
from .depth import record_depth
from .review_budget import record_budget
from .probe import record_probe_intent, record_probe, summarize_probes
from .record_check import DEFAULT_TIME_BUDGET_SECONDS
from . import problem_attempt, problem_candidates, problem_claims, problem_citation_metadata, problem_evidence
from . import problem_forming
from .problem_forming_import import import_forming
from . import overrides
from .automatic_overrides import record_automatic_override
from .problem_objection_carry import carry_objections
from .problem_post_facts import record_post_fact
from .problem_areas import record_area
from .problem_reports import describe_report_advisories, record_report
from .problem_candidate_transfers import record_candidate_transfer
from .capture_selection import select_main_capture
from .payload_paths import iterate_payload_paths, normalize_payload_paths


OPERATIONS = {
    "problem-init": problem_claims.initialize_problem,
    "problem-area": record_area,
    "problem-candidate": problem_candidates.record_candidate,
    "problem-candidate-transfer": record_candidate_transfer,
    "forming": problem_forming.record_forming,
    "form-outcome": problem_forming.record_form_outcome,
    "forming-import": import_forming,
    "problem-slate": problem_candidates.record_slate,
    "problem-objections-carry": carry_objections,
    "problem-statement": problem_claims.record_statement,
    "problem-attempt": problem_attempt.record_attempt,
    "problem-openness": problem_claims.record_openness,
    "problem-value": problem_claims.record_value,
    "problem-citation-metadata": problem_citation_metadata.record_citation_metadata,
    "problem-citation-locator": problem_evidence.record_citation_locator,
    "problem-assessment": problem_claims.record_assessment,
    "problem-formal-check": problem_claims.record_formal_check,
    "problem-decision": problem_claims.record_decision,
    "problem-posted": problem_claims.record_posted,
    "problem-post-external": problem_claims.record_post_external,
    "problem-post-fact": record_post_fact,
    "problem-report": record_report,
    "gate-override": overrides.record_override,
    "gate-auto-pass": record_automatic_override,
    "problem-post-time": problem_claims.record_post_time,
    "init": principles.initialize_research,
    "adopt": adopt_workspace,
    "artifact": pin_artifact,
    "target": principles.set_target,
    "constitution": principles.revalidate_constitution,
    "policy": principles.change_policy,
    "depth": record_depth,
    "review-budget": record_budget,
    "probe": record_probe,
    "probe-intent": record_probe_intent,
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
    "select-main-capture": select_main_capture,
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
    "intent": strategy.record_intent,
    "strategy": strategy.record_strategy,
    "work-item": strategy.record_work_item,
    "research-lead": strategy.record_lead,
    "value-review": research_decisions.record_value_review,
    "research-decision": research_decisions.record_research_decision,
    "source-impact": research_decisions.record_source_impact,
    "review-route": review_protocol.record_route,
    "review-assignment": review_protocol.record_assignment,
    "review-attempt": review_protocol.record_attempt,
    "review-context": review_protocol.record_context_event,
    "review-adjudication": review_protocol.record_adjudication,
}


def _review_probe(store, payload, **identity):
    return review_protocol.probe_route(store, payload, credential=os.environ.get("OPENAI_API_KEY"), **identity)


def _review_run(store, payload, **identity):
    fields(payload, (), None)
    if payload.get("action") == "reconcile" and "invocation_id" in payload:
        from .review_task_execution import reconcile_call
        fields(payload, ("action", "operation", "invocation_id"))
        return reconcile_call(store, {key: payload[key] for key in ("operation", "invocation_id")}, **identity)
    if "task_id" in payload:
        records = store.snapshot()["records"]
        epoch = records.get("review_epoch", {}).get(payload.get("epoch_id"))
        if epoch is not None:
            fields(payload, ("assignment_id", "epoch_id", "task_id"), ("id", "action"))
            if payload["assignment_id"] != epoch["controller_assignment_id"]:
                raise ResearchError("review_task_dependency_changed", "Run the task through its exact controller.")
            task = records.get("review_task", {}).get(payload["task_id"])
            if task is None or task["epoch_id"] != epoch["id"]:
                raise ResearchError("review_task_dependency_changed", "Run a task of the named frozen epoch.")
            execution = records.get("review_task_execution", {}).get(payload["task_id"], {})
            reservation_id = execution.get("reservation_id")
            action = payload.get("action")
            previous = store.committed_request(identity["request_id"])
            if action == "reconcile":
                from .review_task_execution import reconcile_worker
                if isinstance(previous, dict) and previous.get("operation") == "review.worker_reconcile":
                    result = previous["response"]["result"]
                    if result.get("task_id") == payload["task_id"] and result.get("epoch_id") == epoch["id"]:
                        reservation_id = previous["payload"]["reservation_id"]
                if reservation_id is None:
                    raise ResearchError("review_worker_reconciliation_required", "This task has no owned reservation to reconcile.")
                result = reconcile_worker(store, {"reservation_id": reservation_id}, **identity)
                if isinstance(result.get("result"), dict):
                    from .review_staged_protocol import describe_planning_command
                    result = dict(result, result=dict(result["result"],
                        next=[describe_planning_command(epoch["controller_assignment_id"])]))
                return result
            if action not in (None, "accept"):
                raise ResearchError("invalid_input", "Review task action must be accept or reconcile.")
            if action == "accept" and records.get("review_observation", {}).get(reservation_id, {}).get("observation", {}).get("status") != "completed":
                raise ResearchError("review_observation_acceptance_pending", "Acceptance requires a committed completed observation.")
            child = next((assignment for assignment in records.get("review_assignment", {}).values()
                          if assignment.get("staged_review", {}).get("task_id") == payload["task_id"]), None)
            if child is None:
                raise ResearchError("review_task_pending", "Assign this task's child through review-assignment first.")
            if isinstance(previous, dict) and previous.get("operation") == "review.invoke":
                original = previous["payload"]
                if original.get("assignment_id") == child["id"] and payload.get("id", original["id"]) == original["id"]:
                    return review_protocol.invoke_assignment(store, original, credential=os.environ.get("OPENAI_API_KEY"), **identity)
            accepted = records.get("review_task_acceptance", {}).get(reservation_id, {}).get("receipt", {}).get("attempt", {})
            owner = {}
            reservation = records.get("review_reservation", {}).get(reservation_id, {})
            captured = (records.get("review_observation", {}).get(reservation_id, {}).get("observation", {}).get("status") == "completed"
                        and reservation.get("state") != "refused")
            if reservation_id is not None and (not reservation.get("released", False) or captured):
                for dispatch_request, candidate in records.get("review_task_dispatch", {}).items():
                    if candidate.get("task_id") != payload["task_id"] or candidate.get("assignment_id") != child["id"]:
                        continue
                    receipt = store.committed_request(dispatch_request + ":reserve")
                    if isinstance(receipt, dict) and receipt.get("response", {}).get("result", {}).get("id") == reservation_id:
                        owner = candidate
                        break
            if owner and payload.get("id", owner["id"]) != owner["id"]:
                from .problem_delivery import require_unposted_review
                require_unposted_review(records, child, store.build_artifact_store())
                raise ResearchError("review_resampling_forbidden", "Resume the attempt that owns this captured reservation.")
            # Replaying acceptance retains its original attempt identity. Before
            # acceptance, a deterministic identity also survives CLI interruption.
            dispatch = records.get("review_task_dispatch", {}).get(identity["request_id"], {})
            identifier = (payload.get("id") or owner.get("id") or dispatch.get("id") or accepted.get("id") or
                          "task-run:" + digest([epoch["id"], payload["task_id"], identity["request_id"]]))
            payload = {"id": identifier, "assignment_id": child["id"]}
    # Capture, acceptance and output authentication stay in the protocol layer.
    return review_protocol.invoke_assignment(store, payload, credential=os.environ.get("OPENAI_API_KEY"), **identity)


def _review_output_correction(store, payload, **identity):
    from .review_output_correction import invoke_correction
    return invoke_correction(store, payload, credential=os.environ.get("OPENAI_API_KEY"), **identity)


OPERATIONS.update({"review-probe": _review_probe, "review-run": _review_run,
                   "review-output-correction": _review_output_correction})

from .execution import (bind_execution, find_recorded_execution, reconcile_execution, record_imported_execution,
                        record_reconciliation)


def _reconcile_run(store, payload, **identity):
    summary = reconcile_execution(store, payload, **identity)
    return dict(summary, **find_recorded_execution(store, payload["admission_id"]))


OPERATIONS.update({"bind-run": bind_execution, "reconcile-run": _reconcile_run, "result": record_imported_execution})
from .publication import check_claims, list_publication_receipts, prepare_publication, record_manuscript_review
from .publication_files import find_bundle_drift, restore_bundle
from .verification import bind_verdict
OPERATIONS.update({"manuscript": prepare_publication, "manuscript-review": record_manuscript_review, "bind-verdict": bind_verdict})

ACQUISITION = ("collect", "resume", "acquire", "expand", "fulltext", "fulltext-start", "fulltext-resume", "import-response", "import-oai-cohort", "visual")
GATES = ("cohort", "foundation", "preparation", "direction", "readiness", "manuscript-readiness", "execution", "verification", "manuscript", "publication", "deposited", "submitted", "round", "post")
RECORD_CHECK_HELP_MSG = "Qualify a copy of a research record offline; print a JSON report and exit 0 when ready is true, 1 otherwise."


def add_identity(parser, *, required=True):
    parser.add_argument("--expected-revision", type=int, required=required,
                        help="Revision from current status; conflicting new mutations fail.")
    parser.add_argument("--request-id", required=required,
                        help="Stable unique request identity; identical retries return the original receipt.")


def note_managed_record_skipped(error, store=None):
    """One stderr line: the command ran, and the study's receipt was not written.

    A readiness refusal carries its obligations; their codes name what the study still owes."""
    pending = [item["code"] for item in (error.details or {}).get("obligations", [])]
    suffix = " Pending: " + ", ".join(pending) + "." if pending else ""
    if store is not None:
        try:
            recorded = store.snapshot()["records"].get("gate_override", {})
        except ResearchError:
            recorded = {}
        if recorded:
            suffix += " Overrides: " + ", ".join(sorted(recorded)) + "."
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


def select_managed_path(check, start=None, *, managed_only=False, scope_contract_id=None, scope_target_digest=None,
                        public_gate=None, public_action=None):
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
    if public_gate is not None:
        from .automatic_overrides import require_public_write_instruction
        require_public_write_instruction(store, public_gate, public_action=public_action, precheck=True)
    try:
        if scope_contract_id is not None:
            from .publication_scope import validate_expected_scope
            validate_expected_scope(store, scope_contract_id, scope_target_digest)
        return store, check(store), None
    except ResearchError as error:
        if managed_only:
            raise
        note_managed_record_skipped(error, store)
        return store, None, None


def build_parser():
    parser = argparse.ArgumentParser(prog="exactory-research", allow_abbrev=False,
        description="Acquire, read, prepare and develop research with current evidence gates.",
        epilog="JSON schemas and preparation order: docs/research-cli.md in the installed plugin.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in (*OPERATIONS, *ACQUISITION):
        item = commands.add_parser(command, allow_abbrev=False,
            help=("Carry every source bar/slate objection and its authenticated evidence." if command == "problem-objections-carry"
                  else "Submit the exact " + command + " JSON payload."),
            description="Use --file with the typed JSON shape in docs/research-cli.md. Saved assertions and downloads are not successful readings.")
        item.add_argument("--workspace", default=".")
        item.add_argument("--file", required=True, help="UTF-8 JSON input, without duplicate keys or nonfinite numbers.")
        item.add_argument("--check", action="store_true",
                          help="Validate against one snapshot, write nothing; acquisitions, reviewer calls, "
                               "reconciliations, a result PDF page count (pdfinfo) and a second commit refuse with "
                               "check_unsupported.")
        add_identity(item, required=command != "import-response")
        if command == "gate-auto-pass":
            item.description = ('Record an automatic internal continuation from {"stop_id":"RECORDED_STOP"}. '
                                'The harness checks the class, ended retries and all remaining gate items. '
                                'Public writes require the user\'s instruction.')
        if command == "gate-override":
            item.description = ('Record the pinned user instruction. Optional public_action is the exact '
                                '"METHOD /path" for another public write after an automatic pass.')
        if command == "import-response":
            item.add_argument("--preview", action="store_true",
                              help="Report new DOI-family conflicts without writing the import.")
    job_status = commands.add_parser("fulltext-status", allow_abbrev=False)
    job_status.add_argument("--workspace", default=".")
    job_status.add_argument("--job-id", required=True)
    job_status.add_argument("--cursor", type=int, default=0)
    job_status.add_argument("--limit", type=int, default=30)
    series = commands.add_parser("review-series", allow_abbrev=False,
                                 help="Execute the planned actions of one review chain, in process, until the chain "
                                      "is done or needs the agent.")
    series.add_argument("--workspace", default=".")
    series.add_argument("--assignment", required=True)
    series.add_argument("--check", action="store_true",
                        help="Refused with check_unsupported: review-series has no check mode.")
    record_check = commands.add_parser("record-check", allow_abbrev=False,
                                      help=RECORD_CHECK_HELP_MSG)
    record_check.add_argument("--workspace", required=True)
    record_check.add_argument("--keep-clone", type=Path)
    record_check.add_argument("--time-budget", type=float, default=DEFAULT_TIME_BUDGET_SECONDS)
    for command in ("status", "next", "obligations", "batches", "population-query", "policy-report", "gate", "export",
                    "recover", "check-claims"):
        item = commands.add_parser(command, allow_abbrev=False,
            help={"status": "Read current obligations and source paths.", "next": "Read actionable current preparation obligations.",
                  "obligations": "Read one revision-bound page of the obligations that carry a code.",
                  "batches": "Write the current unread abstracts as reader batch files without changing the store.",
                  "population-query": "Rank the enumerated population's stored abstracts by query terms without changing the store.",
                  "policy-report": "Describe the preparation set under the recorded or a hypothetical policy, without changing the store.",
                  "gate": "Validate a current gate without mutation.", "export": "Rebuild disposable projections or deliver exact reviewer bytes.",
                  "check-claims": ("List the manuscript claim references outside the current candidate's evidence "
                                   "without changing the store."),
                  "recover": "Explicitly recover a hot SQLite journal without migrating or certifying research."}[command])
        item.add_argument("--workspace", default=".")
        if command in ("batches", "export", "recover"):
            item.add_argument("--check", action="store_true",
                              help="Refused with check_unsupported: this command writes files or recovery state "
                                   "outside the store, which check mode does not simulate.")
        if command == "check-claims":
            item.add_argument("--file", required=True, help="The claim_evidence array of a manuscript payload, "
                              "or the whole payload, as UTF-8 JSON without duplicate keys or nonfinite numbers.")
        if command in ("status", "next"):
            item.add_argument("--counters", action="store_true", help=argparse.SUPPRESS)
            item.add_argument("--summary", action="store_true",
                              help="Return a bounded advisory view; the same current evaluation still runs.")
        if command == "obligations":
            item.add_argument("--code", required=True, help="Obligation code to list, for example abstract_reading_missing.")
            item.add_argument("--limit", type=int, default=50, help="Obligations per page, 1 to 500.")
            item.add_argument("--cursor", help="REVISION:OFFSET from the previous page; fails when the store changed.")
        if command == "batches":
            item.add_argument("--depth", choices=("abstract",), default="abstract")
            item.add_argument("--size", type=int, default=60, help="Items per batch file, 1 to 100.")
            item.add_argument("--max-input-bytes", type=int, default=262144)
            item.add_argument("--max-output-tokens", type=int)
            item.add_argument("--estimated-output-tokens-per-item", type=int)
            item.add_argument("--destination", required=True, help="New directory for batch-NNN.json files.")
            item.add_argument("--profile", choices=("research", "verification"), help="Defaults to the configured profile.")
            item.add_argument("--screen", action="store_true", help="Export the unscreened members for screen-batch instead of unread abstracts.")
            item.add_argument("--loop", action="store_true", help="Export the selected searches' found works without a loop reading, plus --candidates.")
            item.add_argument("--candidates", help="JSON file: either a list of version ids or a population-query result.")
        if command == "population-query":
            item.add_argument("--terms", nargs="+", required=True, help="Query terms taken from the target statement and the parent's title and abstract.")
            item.add_argument("--limit", type=int, default=30, help="Matches to return, 1 to 100.")
            item.add_argument("--cursor")
        if command == "policy-report":
            item.add_argument("--policy", choices=principles.POLICIES, help="Report a hypothetical policy instead of the recorded one.")
            item.add_argument("--reference", help="JSON file {prior_art, contradictions, methods, doctrine} of family ids for recall.")
        if command == "gate":
            item.add_argument("action", choices=GATES)
        if command == "export":
            item.add_argument("--accept-gaps", action="store_true", help="Export a problem-openness minimum whose gaps block both resolved outcomes, and report them as a warning.")
            item.add_argument("--kind", choices=("workspace", "foundation", "readiness", "manuscript", "round", "native", "problem", "problem-override", "problem-openness", "outputs"), default="workspace")
            item.add_argument("--spec", help="JSON assignment identity, openness minimum, optional analytical native_reviews [{attempt_id,assignment_id}], and source_evidence [{source_id,reason,locator?}] for problem-openness.")
            item.add_argument("--destination", help=(
                "Directory of the export. A problem export goes under final/problem/exports/ of the workspace, by "
                "default final/problem/exports/<export digest>/; a directory that already holds exactly the exported "
                "files is reused. A problem-openness, readiness, manuscript or round packet goes into any new "
                "directory, by default intermediate/reviews/packets/<assignment id>/ or "
                "intermediate/reviews/packets/<kind>-r<revision>/. A problem-openness export whose packet a "
                "directory already holds returns that directory. A native export requires a new directory."))
            item.add_argument("--attack-root", help="Native attack root for an immutable foundation delivery.")
            item.add_argument("--claim-binding", help="JSON source correspondence for an external native verification claim.")
        if command == "recover":
            item.add_argument("--expected-revision", type=int, help="Optional check against the restored committed revision.")
    example = commands.add_parser("example", allow_abbrev=False,
        help="Print an illustrative exact JSON payload without opening a workspace.")
    example.add_argument("operation", choices=sorted((*OPERATIONS, *ACQUISITION, "problem-openness-export")))
    decision = commands.add_parser("research-decision-assess", allow_abbrev=False,
                                   help="Read the same scientific decision obligations used by managed entrypoints.")
    decision.add_argument("--workspace", default=".")
    decision.add_argument("--boundary", choices=("target", "cycle", "write", "publication", "round"), required=True)
    decision.add_argument("--decision-id")
    packet = commands.add_parser("strategy-packet", allow_abbrev=False,
                                 help="Build a canonical reviewer packet without recording approval.")
    packet.add_argument("--workspace", default=".")
    packet.add_argument("--check", action="store_true",
                        help="Refused with check_unsupported before the workspace is opened: building a packet can "
                             "store a file in the artifact store, for example a projected claim file, which check "
                             "mode does not simulate.")
    packet.add_argument("--dossier-id")
    packet.add_argument("--role", choices=review_protocol.ROLES, required=True)
    packet.add_argument("--reviewer-id", required=True)
    packet.add_argument("--context-file", help="Exact structured role context, including an adjudicated objection when applicable.")
    route = commands.add_parser("review-route-assess", allow_abbrev=False,
                                help="Read the context-isolation evidence for one exact runtime route.")
    route.add_argument("--workspace", default=".")
    route.add_argument("--route-id", required=True)
    restore = commands.add_parser("restore", allow_abbrev=False,
                                  help="Write the pinned files of a recorded manuscript bundle back to their paths. "
                                       "The command first moves each differing working file to "
                                       ".restore-backup-<32 hexadecimal digits> in its directory and stores the moved "
                                       "bytes as a backup artifact. The output names the moved file as backup_path. "
                                       "The revision does not change.")
    restore.add_argument("--workspace", default=".")
    restore.add_argument("--check", action="store_true",
                         help="Refused with check_unsupported: this command writes working files, which check mode "
                              "does not simulate.")
    restore.add_argument("--bundle", required=True,
                         help="The digest of a recorded manuscript bundle as the pin returned it: 64 lowercase "
                              "hexadecimal digits. It names any recorded bundle, not only the selected one.")
    return parser


def acquisition_command(store, command, payload, identity, *, preview=False, response_bytes=None):
    if command == "collect":
        fields(payload, ("definition",), ("max_requests", "page_size"))
        return acquisition.collect_cohort(store, **payload, **identity)
    if command == "import-oai-cohort":
        fields(payload, ("collection_id", "pages"))
        return acquisition.import_oai_cohort(store, **payload, **identity)
    if command == "resume":
        fields(payload, ("collection_id",), ("max_requests", "recheck"))
        return acquisition.resume_cohort(store, **payload, **identity)
    if command in ("acquire", "expand"):
        fields(payload, ("identifier",), ("provider", "max_requests"))
        # Expansion acquires a named unresolved graph target through the same
        # durable metadata collector. It does not declare its bibliography read.
        return acquisition.acquire_work(store, **payload, **identity)
    if command == "fulltext":
        fields(payload, ("identifier", "url"), ("max_requests", "extraction_options", "component"))
        return acquisition.acquire_fulltext(store, **payload, **identity)
    if command == "fulltext-start":
        fields(payload, ("identifier",), ("url", "source_id", "extraction_options", "allowances", "use_ranges"))
        return fulltext_jobs.start_job(store, **payload, **identity)
    if command == "fulltext-resume":
        fields(payload, ("job_id",), ("max_requests", "max_pages", "allowances", "reason"))
        return fulltext_jobs.resume_job(store, **payload, **identity)
    if command == "visual":
        fields(payload, ("link", "url"), ("max_requests",))
        return visual_assets.acquire_visual_asset(store, **payload, **identity)
    fields(payload, ("provider", "response_file", "source_url", "captured_at"), ("media_type", "mappings", "publisher_capture"))
    text(payload["response_file"], "Response file")
    values = dict(payload)
    if response_bytes is not None:
        values.pop("response_file")
        values["response"] = response_bytes
        if preview:
            return acquisition.preview_import_response(store, **values)
        return acquisition.import_response(store, **values, **identity)
    if preview:
        values["response"] = read_import_input(values.pop("response_file"), input_kind="response",
                                               workspace_root=store.root)
        return acquisition.preview_import_response(store, **values)
    return acquisition.import_response_file(store, **values, **identity)


def _review_status(records, artifacts):
    from . import review_parts, review_refusal
    from .report_views import describe_parts_turn
    from .problem_delivery import collect_review_outcomes, collect_staged_slots, describe_review_tasks, find_review_post
    from .review_continuation import describe_next_actions
    completed = sorted((attempt for attempt in records.get("review_attempt", {}).values()
                        if attempt["status"] == "completed"), key=lambda attempt: attempt["recorded_revision"])
    by_assignment = {}
    for attempt in completed:
        by_assignment.setdefault(attempt["assignment_id"], []).append(attempt)
    result = {"assignments": {identifier: {"refused_attempts": review_refusal.refused_attempts(
                                records, artifacts, by_assignment.get(identifier, []))}
                            for identifier in records.get("review_assignment", {})}}
    from .review_provider_usage import describe_usage
    usage = describe_usage(records, artifacts)
    if usage['windows']:
        result['provider_usage'] = usage
    for attempt in completed:
        if 'answer_recovery' in attempt:
            result['assignments'][attempt['assignment_id']].setdefault('answer_recoveries', {})[
                attempt['id']] = attempt['answer_recovery']
    parts_assignments = sorted((assignment for assignment in records.get('review_assignment', {}).values()
                                if review_parts.KEY in assignment),
                               key=lambda row: row['recorded_revision'])
    latest_turns = {assignment[review_parts.KEY]['series_assignment_id']: assignment
                    for assignment in parts_assignments}
    series_results = {identifier: review_parts.verify_series(records, artifacts, assignment['id'])
                      for identifier, assignment in latest_turns.items()}
    for assignment in parts_assignments:
        identifier = assignment['id']
        try:
            state = review_protocol.assignment_state(records, artifacts, identifier)
        except (ResearchError, json.JSONDecodeError):
            state = dict(ready=False)
        series = series_results[assignment[review_parts.KEY]['series_assignment_id']]
        result['assignments'][identifier]['review_parts'] = describe_parts_turn(
            assignment, state, series,
            is_posted=find_review_post(records, assignment, artifacts) is not None,
            ended_without_output=review_protocol.has_ended_without_output(records, identifier))
        progress = result['assignments'][identifier]['review_parts']
        if progress['next']:
            progress['next'] = describe_next_actions(records, artifacts, identifier, progress['next'])
    # Legacy status remains a cheap refusal listing. Staged science is selected
    # by assignment_state, never by treating a raw answer page as a final.
    if not records.get("review_epoch"):
        return result
    rows = collect_review_outcomes(records, artifacts)
    result["slot_outcomes"] = collect_staged_slots(rows)
    for row in rows:
        if row["epoch"] is None:
            continue
        assignment, state, effective = row["assignment"], row["state"], row["effective"] or {}
        tasks = describe_review_tasks(records, row["epoch"], state)
        if find_review_post(records, assignment, artifacts) is not None:
            tasks = [dict(task, next=[]) for task in tasks]
        result["assignments"][assignment["id"]].update(
            epoch_id=row["epoch"]["id"], kind=effective.get("kind", "pending"),
            raw_output_digest=None if state.get("raw_output") is None else digest(state["raw_output"]),
            effective_output_digest=effective.get("effective_output_digest"),
            composition_root=effective.get("composition_root"),
            incomplete_outcome=effective.get("incomplete_outcome"),
            tasks=tasks)
        if 'answer_recovery' in state:
            result['assignments'][assignment['id']]['answer_recovery'] = state['answer_recovery']
    for identifier, assignment in records.get('review_assignment', {}).items():
        binding = assignment.get('staged_review', {})
        if binding.get('kind') != 'task':
            continue
        execution = records.get('review_task_execution', {}).get(binding.get('task_id'), {})
        capture = records.get('review_observation', {}).get(execution.get('reservation_id'), {})
        if (not result['assignments'][identifier].get('answer_recoveries')
                and 'answer_recovery' not in capture.get('observation', {})):
            continue
        state = review_protocol.assignment_state(records, artifacts, identifier)
        if 'answer_recovery' in state:
            result['assignments'][identifier]['answer_recovery'] = state['answer_recovery']
    return result


def status_report(store, *, counters=False):
    from .review_state_evaluation import runtime_scope
    from .review_continuation import annotate_review_obligations, describe_next_actions
    started = time.monotonic()
    snapshot = store.snapshot()
    evaluation = Evaluation(snapshot['records'], store.build_artifact_store())
    runtime = store.describe_runtime()
    with runtime_scope(snapshot['records'], evaluation) as scoped_evaluation, ExitStack() as status_scope:
        scope = scoped_evaluation.find_root()._runtime_scope[0]
        if 'review_closure_roots' in scope:
            status_scope.callback(scope.__setitem__, 'review_closure_roots', scope['review_closure_roots'])
        else:
            status_scope.callback(scope.pop, 'review_closure_roots', None)
        scope['review_closure_roots'] = {}
        records = snapshot["records"]
        override_status = overrides.describe_overrides(records, store.root)
        # The views of the outputs are advisory: they never change readiness, the obligations or the next action.
        advisories = find_view_advisories(store, snapshot["revision"])
        if "problem" in records.get("workspace", {}):
            advisories += describe_report_advisories(records)
            report = gate_state(records, evaluation, "post")
            review = _review_status(records, evaluation)
            report = annotate_parts_obligations(report, review)
            slots = report.get('slot_outcomes', {})
            if (records.get("review_epoch") or slots.get('pending') or slots.get('incomplete')
                    or any('review_parts' in assignment
                           for assignment in records.get('review_assignment', {}).values())):
                report = dict(report, obligations=order_obligations(report["obligations"]))
            slate, current_slate = problem_candidates.find_slate(records), problem_candidates.find_current_slate(records)
            export_required = {"code": "problem_export_required", "explanation": "Export the reviewed parts with exactory-research export --kind problem, then post and record the delivery."}
            # A stale slate decides nothing; its stale obligation leads the report instead.
            if report.get("post") is not None:
                upcoming = report["obligations"][0]
            elif current_slate is not None and not current_slate["selected"]:
                upcoming = {"code": "problem_no_post", "reason": current_slate["reason"],
                            "explanation": "The latest slate selects no candidate; a new slate that selects one reopens the post path."}
            elif report["obligations"]:
                upcoming = report["obligations"][0]
            else:
                upcoming = export_required
            result = dict(report, revision=snapshot["revision"], profile="research", runtime=runtime,
                        problem=records["workspace"]["problem"], resources=resources.account_report(records, "research"),
                        limits={"depth": describe_depth(records)}, source_deferrals=assess_deferrals(records, evaluation, "research"),
                        # A cycle plan's literature comparison carries this digest (literature_comparison_stale).
                        literature_digest=synthesis.synthesis_state(records, evaluation, "research")["literature_digest"],
                        candidates=problem_candidates.count_candidates(records), slate_id=None if slate is None else slate["id"],
                        next=upcoming, review=review, advisories=advisories)
            if records.get("forming_call"):
                result["forming"] = problem_forming.count_forming(records)
            if records.get('problem_objection_carry'):
                result['problem_objection_carries'] = [
                    {key: carry[key] for key in ('id', 'source_workspace', 'source_origin', 'source_revision')}
                    for _, carry in sorted(records['problem_objection_carry'].items())]
            if counters:
                result['evaluation'] = dict(evaluation.counters, elapsed_seconds=round(time.monotonic() - started, 3),
                                            retention=evaluation.retention_report())
            result = annotate_review_obligations(records, evaluation, result)
            if result['next'] is None:
                result['next'] = export_required
            upcoming = result['next']
            if upcoming.get('code') == 'review_task_pending' and not upcoming.get('next'):
                pending_slots = {head['slot_id'] for head in slots.get('pending', [])}
                heads = research_decisions.select_problem_slot_assignments(
                    records, 'result', review_protocol.build_problem_subject(records))
                actions = [action for slot, assignment in heads.items()
                           if slot in pending_slots and not assignment.get('staged_review')
                           for action in describe_next_actions(records, evaluation, assignment['id'])]
                if actions:
                    result['next'] = dict(upcoming, next=actions)
            return dict(result, **override_status)
        config = records.get("configuration", {}).get("research")
        profile = config["profile"] if config else "research"
        study = records.get("workspace", {}).get("study")
        action = "verification" if profile == "verification" else "readiness"
        from .publication_scope import has_publication_scope
        scoped_report = gate_state(records, evaluation, "manuscript-readiness") if has_publication_scope(records) else None
        report = scoped_report if scoped_report is not None and action == "readiness" else gate_state(records, evaluation, action, profile=profile)
        round_report = None
        publication_report = None
        if config is not None and profile == "research":
            from .rounds import round_state, round_summary
            round_report = round_state(records, evaluation)
            if study and study["stage"] == "evaluate":
                # At evaluate the study is led first through the manuscript measurement, then to the round decision.
                from .publication import publication_state
                publication_report = publication_state(records, evaluation, "publication")
                merged = report["obligations"] + publication_report["obligations"] + round_report["obligations"]
                obligations = order_obligations({digest(o): o for o in merged}.values())
                report = dict(report, ready=not obligations, obligations=obligations, counts=dict(report["counts"], obligations=len(obligations)))
        # Before later gates are applicable, expose the actual next unread cohort
        # abstract instead of asking for a root or completed development too early.
        preparation = gate_state(records, evaluation, "cohort" if study and study["stage"] == "cohort" else "preparation", profile=profile)
        from .claim_dependencies import uses_claim_dependencies
        managed = uses_claim_dependencies(records)
        if managed:
            preparation = dict(preparation, advisory=True)
            milestone = {"initiate": "direction", "cohort": "direction", "literature": "direction", "ideate": "execution"}.get((study or {}).get("stage"))
            if milestone is not None:
                report = gate_state(records, evaluation, milestone, profile=profile)
        review = _review_status(records, evaluation)
        report = annotate_parts_obligations(report, review)
        draft_pending = find_draft_obligations(records, store.root)
        if draft_pending:
            obligations = report["obligations"] + draft_pending
            report = dict(report, ready=False, obligations=obligations,
                          counts=dict(report["counts"], obligations=len(obligations)))
        # The cohort inventory names the next unread abstract; every other stage
        # takes the highest-priority current obligation in preparation order.
        upcoming = preparation.get("next") if not managed and study and study["stage"] == "cohort" else None
        obligations = current_obligations({"obligations": report["obligations"], "preparation": preparation})
        current_challenge = challenge.find_current_challenge(records) if profile == "research" else None
        tool_status = {}
        tool_status["bundle_drift"] = find_bundle_drift(records, evaluation) if profile == "research" else None
        diagnostics = {"evaluation": dict(evaluation.counters,
                                         elapsed_seconds=round(time.monotonic() - started, 3),
                                         retention=evaluation.retention_report())} if counters else {}
        if records.get("probe_intent") or records.get("probe"):
            tool_status["probes"] = summarize_probes(records)
        provisional = [search for search in records.get("literature_search", {}).values() if search.get("provisional")]
        if provisional:
            tool_status["provisional_searches"] = [{"id": search["id"], "question": search["question"], "profile": search["profile"]}
                                                    for search in provisional]
        scope_status = {}
        if scoped_report is not None:
            scope_status = {key: scoped_report[key] for key in ("manuscript_ready", "manuscript_obligations", "objective_complete",
                                                               "objective_obligations", "scientific_target_digest", "remaining_obligations")}
            scope_status["scope_contract_id"] = scoped_report["selected_contract_id"]
            scope_status["historical_publications"] = list_publication_receipts(records)
            scope_status["historical_submissions"] = list(records.get("submission_receipt", {}).values())
            if scoped_report["contract"] is not None:
                flags = ["--managed-only", "--scope-contract-id", scoped_report["contract"]["id"],
                         "--scope-target-digest", scoped_report["scientific_target_digest"]]
                scope_status["publication_commands"] = {"deposit": ["exactory-draft", "deposit"] + flags,
                                                        "submit": ["exactory", "submit"] + flags}
        result = dict(report, revision=snapshot["revision"], profile=profile, runtime=runtime,
                    source_deferrals=assess_deferrals(records, evaluation, profile),
                    deferred_obligations=literature.foundation_state(records, evaluation, profile).get("deferred_obligations", []),
                    study=study, preparation=preparation, resources=resources.account_report(records, profile),
                    limits=limits_report(records), **tool_status, **diagnostics, **scope_status,
                    round=round_summary(round_report) if round_report else None,
                    grand_challenge=current_challenge["payload"] if current_challenge else None,
                    next=draft_pending[0] if draft_pending else (
                        upcoming or (order_obligations(obligations)[0] if obligations else None)),
                    pending_executions=[key for key in records.get("execution_admission", {}) if key not in records.get("execution_outcome", {})],
                    remote_intents=list(records.get("remote_intent", {}).values()),
                    remote_observations=list(records.get("remote_observation", {}).values()),
                    historical_adoptions=list(records.get("workspace_adoption", {}).values()), review=review,
                    advisories=advisories)
        workspace = records.get("workspace", {})
        deposit = workspace.get("deposit")
        if deposit is not None and deposit in records.get("direct_deposit", {}).values():
            from .publication import find_selected_bundle
            bundle = find_selected_bundle(records)
            context = workspace.get("direct_deposit_context", {})
            is_bound = records.get("direct_deposit", {}).get(context.get("request_id")) == deposit
            bundle_digest = context.get("bundle_digest") if is_bound else None
            is_current_bundle = (bundle_digest is not None and bundle is not None
                                 and bundle_digest == bundle["digest"]
                                 and matches_direct_deposit_files(evaluation, bundle))
            is_published = isinstance(deposit.get("doi"), str) and bool(deposit["doi"].strip())
            is_production = deposit.get("environment") == "production"
            result["external_deposit"] = dict(deposit, status=(
                "published" if is_production else "sandbox_published") if is_published else "draft",
                bundle_digest=bundle_digest, current_bundle=is_current_bundle, managed_credit=False)
            if is_published and is_production and is_current_bundle:
                if "publication_commands" in result:
                    result["publication_commands"].pop("deposit", None)
            if is_published and is_production and publication_report is not None:
                wait = next((item for item in publication_report["obligations"]
                             if item["code"] == "manuscript_reviews_required" or "review_id" in item), None)
                if study and study["stage"] == "evaluate" and wait is not None:
                    result["managed_wait"] = dict(wait, workflow="manuscript-review")
                    result["next"] = wait
        return dict(annotate_review_obligations(records, evaluation, result), **override_status)


def build_gate_report(records, artifacts, action, revision):
    """Compute the same annotated findings for the gate command and a recorded override."""
    from .review_continuation import annotate_review_obligations
    artifacts = Evaluation.of(records, artifacts)
    report = gate_state(records, artifacts, action)
    return annotate_review_obligations(records, artifacts, dict(report, revision=revision))


def load_candidates(path, *, include_coverage=False):
    """Read candidate IDs, optionally with the query's coverage for the export manifest."""
    loaded = strict_json(Path(path).read_bytes())
    if isinstance(loaded, dict):
        matches = loaded.get("matches")
        if isinstance(matches, list) and all(isinstance(m, dict) and isinstance(m.get("version_id"), str) for m in matches):
            versions = [m["version_id"] for m in matches]
            if not include_coverage:
                return versions
            coverage = {"query_coverage": "unknown",
                        "population_query": {key: value for key, value in loaded.items() if key != "matches"}}
            keys = ("total_matches", "returned", "offset", "omitted", "remaining")
            if all(type(loaded.get(key)) is int and loaded[key] >= 0 for key in keys):
                total, returned, offset, omitted, remaining = (loaded[key] for key in keys)
                if returned == len(versions) and omitted == total - returned and remaining == total - offset - returned:
                    coverage["query_coverage"] = "complete" if offset == 0 and omitted == 0 else "partial"
            return versions, coverage
    elif isinstance(loaded, list) and all(isinstance(item, str) for item in loaded):
        return (loaded, {"query_coverage": "unknown", "population_query": None}) if include_coverage else loaded
    raise ResearchError("invalid_input", "The candidates file is a population-query result or a list of version ids")


def describe_review_budget_refusal(error):
    """Attach the same allowance recovery to recording and check-mode refusals."""
    if error.code == "review_budget_below_charged":
        error.details = dict(error.details, minimum_max_calls=error.details["charged_calls"],
                             recovery=["exactory-research", "review-budget", "--file", "FILE",
                                       "--expected-revision", "CURRENT_REVISION", "--request-id", "NEW_ID"])
        error.message += "; retry with max_calls at least " + str(error.details["charged_calls"]) + " or null"
    return error


def read_payload_receipt(root, request_id):
    """Read a retry only from an existing store, leaving legacy adoption available."""
    if root is None:
        return None
    database = root / '.exactory' / 'research.sqlite3'
    if not database.exists() and not database.is_symlink():
        return None
    return Store(root).committed_request(request_id)


def normalize_request_payload(root, command, payload, committed=None, *, request_id=None):
    """Admit fresh file inputs while retaining authenticated historical retries."""
    if committed is None and request_id:
        try:
            return normalize_payload_paths(root, command, payload)
        except ResearchError as error:
            if error.code != 'unsafe_path':
                raise
            # Ordinary inputs need only the operation's preparation and commit
            # reads. Authenticate a receipt here only to replay an old input
            # whose path can no longer pass admission.
            try:
                committed = read_payload_receipt(root, request_id)
            except ResearchError as receipt_error:
                if command != 'gate-override' or receipt_error.code not in ('corrupt_state', 'storage_io'):
                    raise
            if committed is None:
                raise error
    if committed is not None and payload == committed['payload']:
        return payload
    if committed is not None and command not in ACQUISITION:
        # A retry returns its receipt or conflict without reopening old inputs.
        try:
            return normalize_payload_paths(root, command, payload, check_filesystem=False)
        except ResearchError as error:
            if error.code != 'unsafe_path':
                raise
            return payload
    return normalize_payload_paths(root, command, payload)


def run(args):
    if args.command == "record-check":
        from .record_check import check_record
        return check_record(Path(args.workspace), keep_clone=args.keep_clone, time_budget=args.time_budget)
    if args.command == "example":
        examples = strict_json((Path(__file__).resolve().parents[1] / "docs/research-cli-examples.json").read_bytes())
        return examples[args.operation]
    if getattr(args, "check", False) and args.command in (
            "batches", "export", "recover", "restore", "strategy-packet", "review-series"):
        raise ResearchError("check_unsupported", "Check mode cannot run commands with external or recovery effects",
                            {"command": args.command})
    preview = args.command == "import-response" and args.preview
    if (args.command == "import-response" and not preview
            and (args.expected_revision is None or args.request_id is None)):
        raise ResearchError("invalid_input", "An import requires --expected-revision and --request-id")
    root = Path(args.workspace).absolute()
    checking = getattr(args, 'check', False)
    response_bytes = None
    if args.command in OPERATIONS or args.command in ACQUISITION:
        if args.command == "import-response":
            existing = find_workspace(root, required=False)
            original = (read_payload_receipt(existing, args.request_id)
                        if not preview and existing is not None and args.request_id else None)
            if original is not None and original["operation"] != "response.import":
                raise ResearchError("request_id_conflict", "Request ID was committed for another operation")
            raw = Path(args.file).read_bytes() if original is not None else read_import_input(args.file, input_kind="payload")
            payload = strict_json(raw)
        else:
            payload = strict_json(Path(args.file).read_bytes())
        bounded_payload = (review_protocol.drop_repeated_bundle(payload)
                           if args.command == "review-assignment" else payload)
        within_bounds = True
        if not checking:
            try:
                check_command_bounds(bounded_payload)
            except ResearchError:
                if preview:
                    raise
                # Oversized historical requests still replay their original receipt.
                existing_workspace = find_workspace(root, required=False)
                if read_payload_receipt(existing_workspace, args.request_id) is None:
                    raise
                within_bounds = False
        has_paths = (not checking and within_bounds
                     and any(True for _ in iterate_payload_paths(args.command, bounded_payload)))
        existing_workspace = find_workspace(root, required=False) if has_paths else None
        committed = None
        if args.command == 'import-response':
            committed = original
        # Historical receipts replay their original payload without reopening inputs.
        # Equivalent new spellings use the same normalized payload and receipt too.
        if has_paths and not checking:
            payload = normalize_request_payload(existing_workspace or root, args.command, payload, committed,
                request_id=(args.request_id if existing_workspace is not None and args.command != 'import-response'
                            else None))
            bounded_payload = (review_protocol.drop_repeated_bundle(payload)
                               if args.command == "review-assignment" else payload)
        if checking:
            def normalize_check_payload(store, value):
                if any(True for _ in iterate_payload_paths(args.command, value)):
                    return normalize_request_payload(find_workspace(root), args.command, value,
                                                     store.committed_request(args.request_id))
                return value
            operation, replay = OPERATIONS.get(args.command), None
            if args.command == "gate-auto-pass":
                def operation(store, value, **identity):
                    return record_automatic_override(store, value, _diagnostic=True, **identity)
            elif args.command in ACQUISITION:
                def replay_acquisition(store, value, **identity):
                    return acquisition_command(store, args.command, value, identity)
                replay = replay_acquisition
            elif args.command in ("review-probe", "review-run", "review-output-correction"):
                replay = operation
            elif args.command == "reconcile-run":
                def replay_reconciliation(store, value, **identity):
                    return record_reconciliation(store, value, None, **identity)
                replay = replay_reconciliation
            try:
                return check_mutation(Store(find_workspace(root)), operation, payload,
                                      expected_revision=args.expected_revision, request_id=args.request_id,
                                      bounded_payload=bounded_payload, replay=replay,
                                      prepare_payload=normalize_check_payload)
            except ResearchError as error:
                if args.command == "review-budget":
                    describe_review_budget_refusal(error)
                raise
    if args.command == "gate-override":
        root = find_workspace(root)
        identity = {"expected_revision": args.expected_revision, "request_id": args.request_id}
        try:
            return OPERATIONS[args.command](Store(root), payload, **identity)
        except ResearchError as error:
            if error.code not in ("corrupt_state", "storage_io"):
                raise
            return overrides.append_unrecorded_override(root, payload, **identity)
    if args.command == "import-response" and original is None:
        fields(payload, ("provider", "response_file", "source_url", "captured_at"), ("media_type", "mappings", "publisher_capture"))
        text(payload["response_file"], "Response file")
        response_bytes = read_import_input(payload["response_file"], input_kind="response",
                                           workspace_root=find_workspace(root, required=False) or root)
    if args.command in ("init", "adopt", "problem-init"):
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
    elif args.command == "import-oai-cohort" or preview:
        root = find_workspace(root)
        store = Store(root)
    elif args.command in ACQUISITION:
        existing_workspace = find_workspace(root, required=False)
        store = Store(existing_workspace) if existing_workspace is not None else Store(root, create=True)
    else:
        root = find_workspace(root)
        store = Store(root)
    if args.command in OPERATIONS or args.command in ACQUISITION:
        identity = {"expected_revision": args.expected_revision, "request_id": args.request_id}
        if args.command in OPERATIONS:
            try:
                return OPERATIONS[args.command](store, payload, **identity)
            except ResearchError as error:
                if args.command == "review-budget":
                    describe_review_budget_refusal(error)
                raise
        return acquisition_command(store, args.command, payload, identity, preview=preview, response_bytes=response_bytes)
    if args.command == "fulltext-status":
        return fulltext_jobs.read_job_status(store, args.job_id, cursor=args.cursor, limit=args.limit)
    if args.command == "review-series":
        from .review_series import run_series
        return run_series(store, args.assignment)
    if args.command in ("research-decision-assess", "strategy-packet", "review-route-assess"):
        snapshot = store.snapshot()
        artifacts = Evaluation(snapshot["records"], ArtifactStore(root))
        from .review_state_evaluation import runtime_scope
        with runtime_scope(snapshot["records"], artifacts):
            if args.command == "research-decision-assess":
                return dict(research_decisions.decision_state(snapshot["records"], artifacts, args.boundary,
                                                             decision_id=args.decision_id), revision=snapshot["revision"])
            if args.command == "review-route-assess":
                return dict(review_protocol.route_state(snapshot["records"], artifacts, args.route_id), revision=snapshot["revision"])
            context = strict_json(Path(args.context_file).read_bytes()) if args.context_file else {}
            if not isinstance(context, dict):
                raise ResearchError("invalid_input", "Reviewer context must be a JSON object")
            return review_protocol.build_packet(snapshot["records"], artifacts, args.dossier_id, args.role,
                                                args.reviewer_id, **context)
    if args.command in ("status", "next"):
        report = status_report(store, counters=args.summary or args.counters)
        if args.summary:
            return next_summary(report, counters=args.counters) if args.command == "next" else status_summary(report)
        return report
    if args.command == "restore":
        return restore_bundle(store, args.bundle)
    if args.command == "obligations":
        return obligations_page(status_report(store), args.code, limit=args.limit, cursor=args.cursor)
    if args.command == "check-claims":
        payload = strict_json(Path(args.file).read_bytes())
        check_command_bounds(payload)
        payload = normalize_payload_paths(root, args.command, payload)
        ledger = payload.get("claim_evidence") if isinstance(payload, dict) else payload
        return check_claims(store, ledger)
    if args.command == "batches":
        from .batches import export_batches
        if args.candidates and not args.loop:
            raise ResearchError("invalid_input", "--candidates needs --loop")
        if (args.max_output_tokens is None) != (args.estimated_output_tokens_per_item is None):
            raise ResearchError("invalid_input", "Supply --max-output-tokens and --estimated-output-tokens-per-item together")
        candidates, coverage = load_candidates(args.candidates, include_coverage=True) if args.candidates else ((), None)
        destination = Path(args.destination).absolute()
        try:
            return export_batches(store, depth=args.depth, size=args.size, destination=destination, profile=args.profile,
                                  screen=args.screen, loop=args.loop, candidates=candidates,
                                  max_input_bytes=args.max_input_bytes, max_output_tokens=args.max_output_tokens,
                                  estimated_output_tokens_per_item=args.estimated_output_tokens_per_item,
                                  candidate_coverage=coverage)
        except ResearchError as error:
            if error.code in ("batch_item_too_large", "batch_output_budget_too_small"):
                parameters = (error.details.get("parameters", {}) if error.code == "batch_item_too_large" else
                              {"max_output_tokens": args.estimated_output_tokens_per_item})
                recovery = ["exactory-research", "batches", "--workspace", str(root), "--destination", "NEW_DIRECTORY",
                            "--size", str(parameters.get("size", args.size)), "--max-input-bytes",
                            str(parameters.get("max_input_bytes", args.max_input_bytes))]
                if args.max_output_tokens is not None:
                    recovery.extend(["--max-output-tokens", str(parameters.get("max_output_tokens", args.max_output_tokens)),
                                     "--estimated-output-tokens-per-item", str(args.estimated_output_tokens_per_item)])
                for option in ("profile", "candidates"):
                    if getattr(args, option):
                        recovery.extend(["--" + option, getattr(args, option)])
                for option in ("screen", "loop"):
                    if getattr(args, option):
                        recovery.append("--" + option)
                error.details = dict(error.details, recovery=recovery)
                if error.code == "batch_item_too_large":
                    error.details["manifest"] = str(destination / "manifest.json")
            raise
    if args.command == "population-query":
        try:
            return lineage.population_query(store, args.terms, limit=args.limit, cursor=args.cursor)
        except ResearchError as error:
            if error.code in ("stale_cursor", "invalid_input"):
                error.details = dict(error.details or {}, recovery=["exactory-research", "population-query",
                                                                   "--workspace", str(root), "--terms", *args.terms])
            raise
    if args.command == "policy-report":
        reference = strict_json(Path(args.reference).read_bytes()) if args.reference else None
        return screening.policy_report(store, policy=args.policy, reference=reference)
    if args.command == "gate":
        snapshot = store.snapshot()
        evaluation = Evaluation(snapshot['records'], ArtifactStore(store.root))
        report = build_gate_report(snapshot['records'], evaluation, args.action, snapshot['revision'])
        require_ready(report, args.action)
        return report
    if args.kind in ("problem", "problem-override"):
        folder = find_workspace_root(store.root) / PROBLEM_EXPORTS_FOLDER
        if args.destination is not None and folder not in Path(os.path.realpath(args.destination)).parents:
            raise ResearchError("export_destination_outside_final",
                                "Leave out --destination, or give a directory under " + PROBLEM_EXPORTS_FOLDER + "/ of the workspace. Without "
                                "--destination the export goes to " + PROBLEM_EXPORTS_FOLDER + "/<export digest>/.",
                                {"destination": args.destination, "folder": str(folder)})
        from .problem_delivery import export_problem, export_problem_override
        export = export_problem if args.kind == "problem" else export_problem_override
        return export(store, args.destination)
    if args.kind == "problem-openness":
        if not args.spec:
            raise ResearchError("invalid_input", "Problem openness export requires --spec")
        from .problem_openness import export_openness
        return export_openness(store, strict_json(Path(args.spec).read_bytes()), args.destination,
                               accept_gaps=args.accept_gaps)
    if args.kind == "workspace":
        return export_workspace(store)
    if args.kind == "outputs":
        return render_all(store)
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
    deliver = {"readiness": deliver_readiness, "manuscript": deliver_manuscript, "round": deliver_round}[args.kind]
    return deliver(store, args.destination)


def _is_recording_command(args):
    """Whether the command can commit a record: a mutation or an acquisition that is not a check or a preview."""
    return ((args.command in OPERATIONS or args.command in ACQUISITION or args.command == "review-series")
            and not getattr(args, "check", False)
            and not getattr(args, "preview", False))


def main(argv=None, render_views=render_views_after_command):
    """Run one command. After a command that can commit a record, whether it succeeded, failed or replayed a committed
    request, render_views(--workspace) renders the output views once, outside every store lock."""
    args = build_parser().parse_args(argv)
    try:
        with describe_tool_timeout():
            result = run(args)
        if args.command == "review-series":
            raise SystemExit(result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if args.command == "record-check" and not result['ready']:
            raise SystemExit(1)
    except ResearchError as error:
        print(json.dumps({"error": error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": {"code": "input_io", "message": str(error)}}), file=sys.stderr)
        raise SystemExit(1)
    finally:
        if _is_recording_command(args):
            render_views(args.workspace)


if __name__ == '__main__':
    main()
