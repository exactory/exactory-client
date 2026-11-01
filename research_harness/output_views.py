"""Generated views of a research store: final/ holds the current step results, final/history/ the superseded and
historical ones, and intermediate/ every other output of the process.

This module is the only owner of their placement rules. PLACEMENT_BY_KIND names the view group of every record kind
that the harness writes, or none, and each group in VIEW_GROUPS builds its files from the current records. The status of
a record (current, superseded, historical or not_selected) follows the harness's own rules and decides its folder.
A render computes the whole view model from the records, writes only the files whose bytes differ from the marker's
digests and removes the owned files that the model no longer produces. Each command renders once, at its boundary,
after its last commit and outside every lock (render_workspace_views); export --kind outputs renders on request
(render_all).
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from typing import Callable, NamedTuple

from .artifacts import _Workspace, is_sealed_artifact
from .errors import ResearchError, describe_error_at
from .evidence import digest
from .evaluation import Evaluation
from .graph import obligation
from .problem_claims import find_current_statement, find_latest
from .problem_delivery import find_post
from .problem_evidence import find_formal_checks
from .provenance import plugin_version
from .publication import latest_reviews
from .review_context_repair import _lineage, _parent, historical_reviews, slot_id
from .review_protocol import _context_observed, has_ended_without_output
from .storage import Store
from .workspace import checked_parent, find_entry_kind, find_workspace, read_file, strict_json, write_projection


MARKER_PATH = ".exactory/output-views.json"
DIRTY_PATH = ".exactory/output-views-dirty.json"
_RENDERING_PATH = ".exactory/output-views-rendering.json"
_RENDER_LOCK_SECONDS = 1.0
# The folders that the export commands own, from the workspace root.
PROBLEM_EXPORTS_FOLDER = "final/problem/exports"
PACKETS_FOLDER = "intermediate/reviews/packets"
LITERATURE_EXPORTS_FOLDER = "intermediate/literature/exports"
COPY_LIMIT_BYTES = 1 << 20
# A top-level field of a summarized record is shown when its canonical JSON takes at most this many bytes.
FIELD_LIMIT_BYTES = 1 << 16
STALE_VIEWS_ACTION = "exactory-research export --kind outputs"
STALE_VIEWS_EXPLANATION = ("The final/ and intermediate/ folders do not show the latest records. This is advisory: the "
                           "research reads only the records, and no gate depends on these folders.")
_LOCK_FILE_NAME = "output-views.lock"
_MARKER_SCHEMA_NAME = "exactory-output-views-v2"
_INDEX_SCHEMA_NAME = "exactory-output-index-v2"
_SERIES_SCHEMA_NAME = "exactory-review-series-v2"
_SAFE_FILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
# The fields in which records state the revision at which they were recorded, reviewed, assessed or decided.
_REVISION_FIELDS = ("recorded_revision", "revision", "reviewed_revision", "assessed_revision", "decided_revision",
                    "admitted_revision", "analyzed_revision", "prepared_revision")
_STATUS_FOLDERS = {"current": "final/", "superseded": "final/history/", "historical": "final/history/",
                   "not_selected": "intermediate/"}
# Folders that other writers own: the controller renderer owns every math subtree of final/, final/history/ and
# intermediate/ except this store's deliveries, the export commands own their folders, and a session fills the legacy
# folder once.
_CONTROLLER_FOLDERS = ("final/math/", "final/history/math/", "intermediate/math/")
_DELIVERIES_FOLDERS = ("final/math/deliveries/", "final/history/math/deliveries/")
_EXPORT_FOLDERS = tuple(folder + "/" for folder in (PROBLEM_EXPORTS_FOLDER, PACKETS_FOLDER, LITERATURE_EXPORTS_FOLDER))
_LEGACY_FOLDER = "intermediate/legacy/"
_CHECK_REPORT_PATHS = (".exactory/citation-check.json", ".exactory/derivation-check.json")
_FOLDER_FILE_NAMES = ("README.md", "index.json")
_FOLDER_FILE_PATHS = frozenset(folder + "/" + name for folder in ("final", "intermediate")
                               for name in _FOLDER_FILE_NAMES)
_README_TEXTS = {
    "final": """# Final outputs

This folder holds the current result of each step of this workspace: the recorded reviews that count now,
the selected problem records, the current decisions and assessments of the study, the selected and the
deposited manuscript bundles with their files, the deposits, the receipts, the verification records and
the deliveries of the math solver. history/ has the same layout and holds the results that a later record
superseded or that lost their standing.

The exactory harness generates these files from its records after each command. Do not edit, add or
remove files here. The harness rewrites the files that it owns. A file that it did not write stays as
it is, and index.json lists it under "unowned". math/<controller>/ belongs to the math-solver controller,
and problem/exports/ to export --kind problem.

index.json lists each file with its type, the kind and the id of its record, the revision that the record
states and its status: current, superseded or historical.

Every other output of the process, for example each reviewer turn and each run, is in ../intermediate/.
To render this folder again from the records, run:

    exactory-research export --kind outputs
""",
    "intermediate": """# Intermediate outputs

This folder holds every output of this workspace that is not a current or earlier step result: each
reviewer turn in the order of its series (reading turns, source requests, final turns that no record
adopted, retries, failed runs, refusals and corrections), the probes of each review route, the problem
candidates, the probes and runs of the lab, the bundles that were never selected or deposited, the
score history, copies of the citation and derivation check reports, and the remote operations.

The exactory harness generates these files from its records after each command. Do not edit, add or
remove files here, except once in legacy/. The harness rewrites the files that it owns. A file that it
did not write stays as it is, and index.json lists it under "unowned". math/<controller>/ belongs to the
math-solver controller, and reviews/packets/ and literature/exports/ to the export commands.

In reviews/<role>/<series>/, series.json lists every turn and every run with its status, error, usage,
refusal, correction, the selected run and the record that adopted the turn. <NN>-<assignment>.<run>.json
holds the output of one completed run of turn NN.

To render this folder again from the records, run:

    exactory-research export --kind outputs
""",
}


class ViewFile(NamedTuple):
    """One file of the view model: its bytes, or the artifact reference whose bytes it copies, and its index entry."""

    content: object
    entry: dict


class ViewGroup(NamedTuple):
    """The record kinds that one group places, the further kinds whose records decide its files, and
    build(records, artifacts, inputs), which returns {path from the workspace root: ViewFile}."""

    name: str
    kinds: tuple
    reads: tuple
    build: Callable


class _ViewInputs(NamedTuple):
    """What the views show from outside the store: the controller adoptions by assignment id and the check reports."""

    adoptions: dict
    check_reports: dict


def _format_file_name(identifier):
    """A record id as a file name, or a digest of it when the id is not a portable file name."""
    if _SAFE_FILE_NAME_PATTERN.match(identifier):
        return identifier
    return "id-" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]


def _encode(value):
    """JSON with sorted keys, indented when it takes at most COPY_LIMIT_BYTES; a larger value stays on one line, which
    json encodes in C and in a fraction of the time that indenting it takes."""
    compact = _encode_canonical(value)
    if len(compact) > COPY_LIMIT_BYTES:
        return compact + b"\n"
    return _encode_indented(value)


def _encode_indented(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _encode_canonical(value):
    """The canonical JSON bytes of a value, whose SHA-256 is evidence.digest of it."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _summarize_record(record, entry, data):
    """What a view shows of a record whose canonical JSON, data, takes more than COPY_LIMIT_BYTES, so that no view
    copies a large record whole: the kind, id, recorded revision and status of its index entry, its size and digest,
    and each top-level field as its value when that takes at most FIELD_LIMIT_BYTES (an artifact reference always
    does), else as {omitted, bytes, sha256}."""
    fields = {}
    for name, value in sorted(record.items()):
        field = _encode_canonical(value)
        fields[name] = value if len(field) <= FIELD_LIMIT_BYTES else {
            "omitted": True, "bytes": len(field), "sha256": hashlib.sha256(field).hexdigest()}
    return {"summary": True, "kind": entry["kind"], "id": entry["id"], "recorded_revision": entry["recorded_revision"],
            "status": entry.get("status"), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "fields": fields}


def _encode_record(record, entry):
    """The bytes of a view file that shows one record: the record, or its summary when it is over COPY_LIMIT_BYTES."""
    data = _encode_canonical(record)
    if len(data) <= COPY_LIMIT_BYTES:
        return _encode_indented(record)
    return _encode(_summarize_record(record, entry, data))


def _bound_embedded_record(kind, key, record):
    """A record inside a view that shows several records: the record, or its summary when it is over
    COPY_LIMIT_BYTES."""
    if record is None:
        return None
    data = _encode_canonical(record)
    return record if len(data) <= COPY_LIMIT_BYTES else _summarize_record(record, _describe(kind, key, record), data)


def _read_revision(record):
    """The revision that the record states, under whichever name its kind uses, or None."""
    return next((record[name] for name in _REVISION_FIELDS if name in record), None)


def _describe(kind, key, record, status=None):
    """The index entry of a file that shows one record."""
    entry = {"kind": kind, "id": key, "recorded_revision": _read_revision(record)}
    if status is not None:
        entry["status"] = status
    return entry


def _find_latest_keys(records, kind, group_of):
    """The key of the latest record of kind in each group, by the revision that the records state."""
    latest = {}
    for key, record in records.get(kind, {}).items():
        group = group_of(record)
        if group not in latest or (_read_revision(record) or 0, key) > latest[group][0]:
            latest[group] = ((_read_revision(record) or 0, key), key)
    return {key for _, key in latest.values()}


def _find_pointed_values(records, selection_kind, field, pointer_key=None):
    """The values that the selection pointers of selection_kind (or only its pointer_key) hold in field."""
    pointers = records.get(selection_kind, {})
    selected = pointers.values() if pointer_key is None else [pointers.get(pointer_key) or {}]
    return {pointer.get(field) for pointer in selected} - {None}


def _find_selected_bundle_digest(records):
    pointer = records.get("publication_selection", {}).get("bundle")
    bundle = None if pointer is None else records.get("publication_bundle", {}).get(pointer["id"])
    return None if bundle is None else bundle["digest"]


def _has_staged_successor(records, assignment):
    """A successor installs pending standing before it records a result or disposition."""
    if assignment is None:
        return False
    slot = assignment.get("slot_id") or slot_id(records, assignment)
    head = records.get("review_slot_head", {}).get(slot)
    if head and assignment.get("staged_review", {}).get("epoch_id"):
        if head["epoch_id"] != assignment["staged_review"]["epoch_id"]:
            return True
    for epoch in records.get("review_epoch", {}).values():
        controller = records.get("review_assignment", {}).get(epoch["controller_assignment_id"])
        if (controller and epoch["role"] == assignment["role"] and epoch["slot_id"] == slot
                and controller["id"] != assignment["id"]
                and controller.get("recorded_revision", 0) > assignment.get("recorded_revision", 0)):
            return True
    return False


def _find_current_value_reviews(records):
    """(current, historical) value review ids. historical_reviews names the reviews whose independence a later
    repair or a contamination ended; of the others, the latest review of each dossier, stage, subject and slot is
    current, as the harness selects bars and problem reviews."""
    reviews = records.get("value_review", {})
    historical = set()
    for dossier_id, stage in {(review.get("dossier_id"), review["payload"]["stage"]) for review in reviews.values()}:
        historical.update(review["id"] for review in historical_reviews(records, dossier_id, stage))
    standing = {"value_review": {key: review for key, review in reviews.items() if key not in historical}}
    current = _find_latest_keys(standing, "value_review", lambda review: (
        review.get("dossier_id"), review["payload"]["stage"], digest(review.get("subject")),
        slot_id(records, records["review_assignment"][review["assignment_id"]])))
    superseded = {identifier for identifier in current if _has_staged_successor(
        records, records["review_assignment"][reviews[identifier]["assignment_id"]])}
    current.difference_update(superseded)
    historical.update(superseded)
    return current, historical


def _find_current_adjudications(records):
    current = _find_latest_keys(records, "review_adjudication", lambda record: record["assignment_id"])
    return {key for key in current if not _has_staged_successor(
        records, records.get("review_assignment", {}).get(records["review_adjudication"][key]["assignment_id"]))}


# The record kinds whose records are each one file under <tree>/<id>.json, with the rule that names the current ones;
# a record that the rule does not name is superseded.
_RECORD_TREES = {
    "review_adjudication": ("reviews/adjudicator", _find_current_adjudications),
    "manuscript_review": ("reviews/manuscript", lambda records: {review["id"] for review in latest_reviews(
        records, _find_selected_bundle_digest(records)).values()}),
    "manuscript_prediction": ("reviews/prediction", lambda records: _find_keys_of(
        records, "manuscript_prediction", [record for record in records.get("manuscript_prediction", {}).values()
                                           if record["bundle_digest"] == _find_selected_bundle_digest(records)])),
    "readiness_review": ("reviews/readiness", lambda records: _find_pointed_values(
        records, "development_selection", "review_id", "review")),
    "scoped_readiness_review": ("reviews/scoped-readiness", lambda records: _find_pointed_values(
        records, "scoped_review_selection", "id")),
    "round_review": ("reviews/round", lambda records: _find_latest_keys(
        records, "round_review", lambda record: record["round_id"])),
    "problem_statement": ("problem/statements", lambda records: _find_pointed_values(
        records, "problem_selection", "statement_id", "current")),
    "problem_slate": ("problem/slates", lambda records: _find_latest_ids(records, "problem_slate")),
    "problem_attempt": ("problem/attempts", lambda records: _find_latest_ids(records, "problem_attempt")),
    "problem_openness": ("problem/openness", lambda records: _find_latest_ids(records, "problem_openness")),
    "problem_value": ("problem/values", lambda records: _find_latest_ids(records, "problem_value")),
    "problem_assessment": ("problem/assessments", lambda records: _find_latest_ids(records, "problem_assessment")),
    "problem_decision": ("problem/decisions", lambda records: _find_latest_ids(records, "problem_decision")),
    "problem_formal_check": ("problem/formal-checks", lambda records: _find_keys_of(
        records, "problem_formal_check", find_formal_checks(records))),
    "problem_post": ("problem/posts", lambda records: _find_keys_of(
        records, "problem_post", [find_post(records, find_current_statement(records))])),
    "round_admission": ("study/round-admissions", lambda records: _find_latest_keys(
        records, "round_admission", lambda record: record["number"])),
    "round_decision": ("study/round-decisions", lambda records: _find_latest_keys(
        records, "round_decision", lambda record: record["closes"])),
    "round_assessment": ("study/round-assessments", lambda records: _find_latest_keys(
        records, "round_assessment", lambda record: record["round_id"])),
    "round_factual_assessment": ("study/round-factual-assessments", lambda records: _find_latest_keys(
        records, "round_factual_assessment", lambda record: record["research_decision_id"])),
    "research_decision": ("study/decisions", lambda records: _find_pointed_values(
        records, "strategy_selection", "id", "decision")),
    "cycle_assessment": ("study/cycle-assessments", lambda records: _find_pointed_values(
        records, "cycle", "assessment_id")),
    "contribution_analysis": ("study/contribution-analyses", lambda records: _find_latest_keys(
        records, "contribution_analysis", lambda record: record["bundle_digest"])),
    "checkpoint": ("study/checkpoints", lambda records: _find_pointed_values(
        records, "development_selection", "checkpoint_id", "candidate")),
    "submission_receipt": ("study/receipts", lambda records: set(records.get("submission_receipt", {}))),
    "verdict_receipt": ("study/receipts", lambda records: set(records.get("verdict_receipt", {}))),
    "direct_deposit": ("study/receipts", lambda records: set(records.get("direct_deposit", {}))),
    "verdict_assessment": ("verification/verdicts", lambda records: _find_pointed_values(
        records, "verification_selection", "id", "verdict")),
    "verification_task": ("verification/tasks", lambda records: _find_keys_of(
        records, "verification_task", [record for record in records.get("verification_task", {}).values()
                                       if record.get("digest") in _find_pointed_values(
                                           records, "verification_selection", "digest", "task")])),
    "native_attempt": ("math/deliveries/attempts", lambda records: _find_pointed_values(
        records, "native_attempt_current", "native_attempt_digest")),
    "native_closure": ("math/deliveries/closures", lambda records: set(records.get("native_closure", {}))),
}
# The selection pointers and other records that the rules above read.
_RECORD_TREE_READS = ("development_selection", "scoped_review_selection", "problem_selection", "strategy_selection",
                      "cycle", "verification_selection", "native_attempt_current", "publication_selection",
                      "publication_bundle")


def _find_latest_ids(records, kind):
    latest = find_latest(records, kind)
    return set() if latest is None else {latest["id"]}


def _find_keys_of(records, kind, selected):
    """The keys of the records of kind that the harness's own function selected."""
    return {key for key, record in records.get(kind, {}).items() if record in selected}


def _build_records(records, artifacts, inputs):
    """One file per record of each kind of _RECORD_TREES, in final/ when current and in final/history/ otherwise."""
    files = {}
    for kind, (tree, find_current) in _RECORD_TREES.items():
        current = find_current(records)
        for key, record in records.get(kind, {}).items():
            status = "current" if key in current else "superseded"
            if kind == "review_adjudication" and _has_staged_successor(
                    records, records.get("review_assignment", {}).get(record["assignment_id"])):
                status = "historical"
            path = _STATUS_FOLDERS[status] + tree + "/" + _format_file_name(key) + ".json"
            entry = _describe(kind, key, record, status)
            files[path] = ViewFile(_encode_record(record, entry), entry)
    return files


def _build_value_reviews(records, artifacts, inputs):
    """Recorded reviews under the role of their assignment; the index names the research support recorded with one."""
    current, historical = _find_current_value_reviews(records)
    files = {}
    for key, review in records.get("value_review", {}).items():
        status = "historical" if key in historical else "current" if key in current else "superseded"
        role = records["review_assignment"][review["assignment_id"]]["role"]
        entry = _describe("value_review", key, review, status)
        if key in records.get("research_support", {}):
            entry["research_support_id"] = key
        path = _STATUS_FOLDERS[status] + "reviews/" + _format_file_name(role) + "/" + _format_file_name(key) + ".json"
        files[path] = ViewFile(_encode_record(review, entry), entry)
    return files


def _list_product_files(folder, items, entry):
    """{path: ViewFile} copying each (path, reference) of a product whatever its size; a sealed reference is listed on
    the entry instead."""
    files = {}
    for path, reference in items:
        if is_sealed_artifact(reference):
            entry.setdefault("files_not_copied", {})[path] = "sealed"
        else:
            files[folder + path] = ViewFile(reference, entry)
    return files


def _build_bundles(records, artifacts, inputs):
    """The selected bundle and the bundle of the latest deposit, with their pinned files, in final/; earlier deposited
    bundles in final/history/; every other bundle as its record in intermediate/study/bundles/."""
    receipts = sorted((receipt for kind in _DEPOSIT_KINDS for receipt in records.get(kind, {}).values()),
                      key=lambda receipt: (_read_revision(receipt) or 0, receipt["intent_id"]))
    deposited = {receipt["bundle_digest"] for receipt in receipts}
    current = {_find_selected_bundle_digest(records)}
    if receipts:
        current.add(receipts[-1]["bundle_digest"])
    files = {}
    for key, bundle in records.get("publication_bundle", {}).items():
        status = ("current" if bundle["digest"] in current else "superseded" if bundle["digest"] in deposited
                  else "not_selected")
        entry = _describe("publication_bundle", key, bundle, status)
        if status == "not_selected":
            files["intermediate/study/bundles/" + _format_file_name(key) + ".json"] = ViewFile(
                _encode_record(bundle, entry), entry)
            continue
        folder = _STATUS_FOLDERS[status] + "study/manuscripts/" + _format_file_name(key) + "/"
        files.update(_list_product_files(folder, [("files/" + item["path"], item["artifact"])
                                                  for item in bundle["files"].values() if item], entry))
        files[folder + "bundle.json"] = ViewFile(_encode_record(bundle, entry), entry)
    return files


_DEPOSIT_KINDS = ("publication_receipt", "publication_read_back")


def _build_deposits(records, artifacts, inputs):
    """receipt.json, read-back.json and the bytes of every uploaded file that the first of them names."""
    files = {}
    for intent_id in sorted({key for kind in _DEPOSIT_KINDS for key in records.get(kind, {})}):
        folder = "final/study/deposits/" + _format_file_name(intent_id) + "/"
        documents = [(name, kind, records[kind][intent_id]) for kind, name in zip(
            _DEPOSIT_KINDS, ("receipt.json", "read-back.json")) if intent_id in records.get(kind, {})]
        for position, (name, kind, record) in enumerate(documents):
            entry = _describe(kind, intent_id, record, "current")
            if position == 0:
                files.update(_list_product_files(folder, [
                    ("files/" + _format_file_name(upload["name"]), upload["artifact"])
                    for upload in record.get("uploads", [])], entry))
            files[folder + name] = ViewFile(_encode_record(record, entry), entry)
    return files


_SERIES_KINDS = ("review_assignment", "review_invocation", "review_attempt", "review_output_refusal",
                 "review_output_correction_invocation", "review_output_correction", "review_context_event")
# The records that state which assignment's output they adopted.
_ADOPTING_KINDS = ("value_review", "review_adjudication", "problem_openness", "manuscript_review",
                   "manuscript_prediction")


def _find_adoptions(records, inputs):
    """{assignment id: the records that adopted its output}; an openness finding also adopts its reading turns, and
    the controller adoptions come from the controller indexes."""
    adoptions = {}
    for kind in _ADOPTING_KINDS:
        for key, record in sorted(records.get(kind, {}).items()):
            if record.get("assignment_id") is not None:
                adoptions.setdefault(record["assignment_id"], []).append({"kind": kind, "id": key})
            for turn in record.get("reading_turns", []) if kind == "problem_openness" else []:
                if turn["assignment_id"] != record.get("assignment_id"):
                    adoptions.setdefault(turn["assignment_id"], []).append(
                        {"kind": kind, "id": key, "as": "reading_turn"})
    for assignment_id, items in inputs.adoptions.items():
        adoptions.setdefault(assignment_id, []).extend(items)
    return adoptions


def _find_series_root(records, assignment):
    """The series of an assignment: an adjudicator's session, else the origin of its lineage."""
    if assignment.get("staged_review", {}).get("kind") == "task":
        return _find_series_root(records, records["review_assignment"][assignment["staged_review"]["controller_assignment_id"]])
    if assignment["role"] == "adjudicator":
        return assignment.get("session_id") or assignment["id"]
    return _lineage(records, assignment)[0]["id"]


def _find_series_parent(assignment):
    if assignment.get("staged_review", {}).get("kind") == "task":
        return assignment["staged_review"]["controller_assignment_id"]
    parent = _parent(assignment)
    if parent is None and assignment["role"] == "adjudicator":
        return assignment.get("history_assignment_id")
    return parent


def _name_link(records, assignment, earlier_siblings, adoptions):
    """How a turn continues its parent: origin, source_continuation, context_repair, problem_refresh, replacement or
    focused_refresh."""
    context = assignment.get("context", {})
    if isinstance(context.get("context_repair"), dict):
        return "context_repair"
    if context.get("prior_assignment_id") is not None:
        if any(item["kind"] == "value_review" for item in adoptions.get(context["prior_assignment_id"], [])):
            return "problem_refresh"
        if earlier_siblings and has_ended_without_output(records, earlier_siblings[-1]["id"]):
            return "replacement"
        return "source_continuation"
    if _find_series_parent(assignment) is not None:
        return "focused_refresh"
    return "origin"


def _number_turns(records, members):
    """{assignment id: turn number}: the reading turn of a chain, else the position in the series' lineage."""
    numbers = {}
    for assignment in sorted(members, key=lambda item: item["recorded_revision"]):
        parent = _find_series_parent(assignment)
        position = numbers[parent] + 1 if parent in numbers else len(_lineage(records, assignment))
        numbers[assignment["id"]] = (assignment.get("reading_chain") or {}).get("turn") or position
    return numbers


def _describe_run(artifacts, files, stem, run, attempt, invocation, refusal):
    """One run of a turn: its record fields and its output, copied to <stem>.<run>.json when small and readable."""
    record = attempt or invocation
    item = {"run_id": run, "status": attempt["status"] if attempt else "pending", "error": record.get("error"),
            "usage": record.get("usage"), "recorded_revision": record["recorded_revision"],
            "refusal": None if refusal is None else {key: refusal.get(key) for key in (
                "code", "check", "details", "recorded_revision")},
            "correction": None, "selected": False}
    item.update(_copy_output(artifacts, files, stem + "." + _format_file_name(run) + ".json", record.get("output"),
                             _describe("review_attempt", run, record)))
    return item


def _copy_output(artifacts, files, path, reference, entry):
    """The output fields of a run: its reference, the name of the file that holds its bytes, and the reason or the error
    that kept it from being copied. A sealed reference is never read."""
    if reference is None:
        return {"output": None, "output_file": None, "output_listed": None}
    reason = None
    if is_sealed_artifact(reference):
        reason = "sealed"
    elif reference["size"] > COPY_LIMIT_BYTES:
        reason = "over_1_mib"
    else:
        try:
            files[path] = ViewFile(artifacts.read(reference), entry)
        except ResearchError as error:
            if error.code not in ("artifact_missing", "artifact_corrupt"):
                raise
            reason = error.code
    return {"output": reference, "output_file": None if reason else path.rsplit("/", 1)[1], "output_listed": reason}


def _describe_turn(records, artifacts, files, stem, assignment, by_assignment, refusals):
    """The runs of one turn in order, with the selected one marked: the first completed run without a refusal record
    whose context the transport observed, else the first completed run without a refusal record, else the run that an
    accepted correction corrected. This is the binding rule of review_protocol._selected_attempt over the recorded
    refusals; the view does not check the output contracts again."""
    attempts = {item["id"]: item for item in by_assignment["review_attempt"].get(assignment["id"], [])}
    invocations = {item["id"]: item for item in by_assignment["review_invocation"].get(assignment["id"], [])}
    ordered = sorted(set(attempts) | set(invocations),
                     key=lambda run: ((attempts.get(run) or invocations[run])["recorded_revision"], run))
    runs = {run: _describe_run(artifacts, files, stem, run, attempts.get(run), invocations.get(run), refusals.get(run))
            for run in ordered}
    usable = [attempts[run] for run in ordered
              if run in attempts and attempts[run]["status"] == "completed" and run not in refusals]
    binding = ([attempt for attempt in usable if _context_observed(artifacts, attempt)] or usable or [None])[0]
    if binding is not None:
        runs[binding["id"]]["selected"] = True
    if assignment.get("staged_review") or any(epoch["controller_assignment_id"] == assignment["id"] for epoch in records.get("review_epoch", {}).values()):
        from .review_protocol import assignment_state
        state = assignment_state(records, artifacts, assignment["id"])
        # Raw task selection is distinct from authorization of an effective
        # final. The protocol selects pages and terminal attempts itself.
        for run in runs.values():
            run["raw_output"] = run["output"]
            run["selected"] = run["run_id"] == state.get("attempt_id")
    for invocation in by_assignment["review_output_correction_invocation"].get(assignment["id"], []):
        result = records.get("review_output_correction", {}).get(invocation["id"]) or {}
        record = result or invocation
        entry = _describe("review_output_correction", invocation["id"], record)
        correction = {"run_id": invocation["id"], "status": result.get("status", "pending"),
                      "correction_status": result.get("correction_status"),
                      "error": result.get("correction_error") or result.get("error"), "usage": result.get("usage"),
                      "recorded_revision": record["recorded_revision"], "selected": False}
        correction.update(_copy_output(artifacts, files, stem + "." + _format_file_name(invocation["id"]) + ".json",
                                       result.get("output"), entry))
        corrected = _copy_output(artifacts, files, stem + "." + _format_file_name(invocation["id"]) + ".corrected.json",
                                 result.get("corrected_output"), entry)
        correction.update({"corrected_" + key: value for key, value in corrected.items()})
        corrected_run = invocation.get("diagnostic", {}).get("attempt_id")
        if binding is None and result.get("correction_status") == "accepted":
            correction["selected"] = True
            if corrected_run in runs:
                runs[corrected_run]["selected"] = True
        if corrected_run in runs:
            runs[corrected_run]["correction"] = correction
        else:
            runs[invocation["id"]] = dict(correction, correction=None)
    return list(runs.values())


def _build_series(records, artifacts, inputs):
    """series.json of every reviewer series and one file per completed run with output."""
    assignments = records.get("review_assignment", {})
    by_assignment = {kind: {} for kind in _SERIES_KINDS[1:]}
    for kind, grouped in by_assignment.items():
        for record in records.get(kind, {}).values():
            grouped.setdefault(record["assignment_id"], []).append(record)
    refusals = {refusal["attempt_id"]: refusal for refusal in records.get("review_output_refusal", {}).values()}
    adoptions = _find_adoptions(records, inputs)
    series = {}
    for assignment in assignments.values():
        series.setdefault(_find_series_root(records, assignment), []).append(assignment)
    origins = [assignment for assignment in assignments.values()
               if assignment["role"] != "adjudicator" and _find_series_parent(assignment) is None]
    files = {}
    for root_id, members in sorted(series.items()):
        root = assignments.get(root_id) or min(members, key=lambda item: item["recorded_revision"])
        folder = "intermediate/reviews/" + _format_file_name(root["role"]) + "/" + _format_file_name(root_id) + "/"
        numbers = _number_turns(records, members)
        members = sorted(members, key=lambda item: (numbers[item["id"]], item["recorded_revision"]))
        slot = slot_id(records, root) if root["role"] != "adjudicator" else None
        turns = []
        for assignment in members:
            identifier, parent = assignment["id"], _find_series_parent(assignment)
            siblings = [other for other in members if other["id"] != identifier and _find_series_parent(other) == parent
                        and parent is not None and other["recorded_revision"] < assignment["recorded_revision"]]
            link = _name_link(records, assignment, siblings, adoptions)
            stem = folder + "%02d-%s" % (numbers[identifier], _format_file_name(identifier))
            turns.append({
                "turn": numbers[identifier], "assignment_id": identifier, "parent_assignment_id": parent, "link": link,
                "sibling": None if not siblings else {"of": siblings[-1]["id"], "as": {
                    "replacement": "replacement", "context_repair": "repair"}.get(link, "fork")},
                "session_id": assignment.get("session_id"),
                "history_assignment_id": assignment.get("history_assignment_id"),
                "slot_id": slot_id(records, assignment) if assignment["role"] != "adjudicator" else None,
                "recorded_revision": assignment["recorded_revision"], "reviewer_id": assignment["reviewer_id"],
                "route_id": assignment["route_id"], "packet": assignment.get("packet"),
                "runs": _describe_turn(records, artifacts, files, stem, assignment, by_assignment, refusals),
                "context_events": [{key: event.get(key) for key in ("id", "kind", "source", "reason",
                                                                     "recorded_revision")}
                                   for event in by_assignment["review_context_event"].get(identifier, [])],
                "adopted_by": adoptions.get(identifier, [])})
            if assignment.get("staged_review", {}).get("kind") == "task":
                turns[-1]["task_id"] = assignment["staged_review"]["task_id"]
            if any(epoch["controller_assignment_id"] == identifier for epoch in records.get("review_epoch", {}).values()):
                from .review_protocol import assignment_state
                state = assignment_state(records, artifacts, identifier)
                effective = state.get("effective") or {"kind": "pending"}
                turns[-1].update(raw_output_digest=None if state.get("raw_output") is None else digest(state["raw_output"]),
                    effective={key: value for key, value in effective.items() if key not in ("effective_output", "incomplete_outcome")})
                if "effective_output" in effective:
                    entry = _describe("review_assignment", identifier, assignment)
                    entry["output_kind"] = "effective"
                    files[stem + ".effective.json"] = ViewFile(_encode_record(effective["effective_output"], entry), entry)
                    turns[-1]["effective"]["output_file"] = stem.rsplit("/", 1)[1] + ".effective.json"
                if "incomplete_outcome" in effective:
                    turns[-1]["effective"]["outcome_id"] = effective["incomplete_outcome"]["id"]
        replacements = sorted((other for other in origins
                               if other["id"] != root_id and slot is not None and other.get("slot_id") == slot
                               and other["recorded_revision"] > root["recorded_revision"]),
                              key=lambda other: other["recorded_revision"])
        document = {"schema": _SERIES_SCHEMA_NAME, "role": root["role"], "root_assignment_id": root_id,
                    "slot_id": slot, "replaced_by_series": replacements[0]["id"] if replacements else None,
                    "turns": turns}
        files[folder + "series.json"] = ViewFile(_encode(document), _describe("review_assignment", root_id, root))
    return files


_STAGED_KINDS = ("review_epoch", "review_task", "assessment_incomplete", "review_execution_plan",
                 "review_task_execution", "review_reservation", "review_observation", "review_task_acceptance",
                 "review_pool_membership", "review_capacity_policy", "review_slot_head", "review_task_dispatch",
                 "review_composition_plan")


def _build_staged_records(records, artifacts, inputs):
    """Keep task observations and lifecycle facts intermediate; incompleteness is a distinct slot outcome."""
    files = {}
    for kind in _STAGED_KINDS:
        for key, record in records.get(kind, {}).items():
            incomplete = kind == "assessment_incomplete"
            head = records.get("review_slot_head", {}).get(record.get("slot_id"))
            status = ("historical" if head and head["epoch_id"] != record["epoch_id"] else "incomplete") if incomplete else None
            folder = (("final/history/" if status == "historical" else "final/") + "reviews/incomplete/" if incomplete
                      else "intermediate/reviews/staged/" + kind + "/")
            entry = _describe(kind, key, record, status)
            files[folder + _format_file_name(key) + ".json"] = ViewFile(_encode_record(record, entry), entry)
    return files


def _build_routes(records, artifacts, inputs):
    """route.json and probes/<probe>.json, which holds the probe's invocation and its observed result."""
    files = {}
    for kind in ("review_route", "review_probe_invocation", "review_route_probe"):
        for key, record in records.get(kind, {}).items():
            route_id = key if kind == "review_route" else record["route_id"]
            folder = "intermediate/reviews/routes/" + _format_file_name(route_id) + "/"
            if kind == "review_route":
                entry = _describe(kind, key, record)
                files[folder + "route.json"] = ViewFile(_encode_record(record, entry), entry)
                continue
            invocation = records.get("review_probe_invocation", {}).get(key)
            probe = records.get("review_route_probe", {}).get(key)
            entry = (_describe("review_route_probe", key, probe) if probe is not None
                     else _describe("review_probe_invocation", key, invocation))
            files[folder + "probes/" + _format_file_name(key) + ".json"] = ViewFile(
                _encode({"invocation": _bound_embedded_record("review_probe_invocation", key, invocation),
                         "probe": _bound_embedded_record("review_route_probe", key, probe)}), entry)
    return files


def _build_candidates(records, artifacts, inputs):
    """Every problem candidate; a candidate that a later one names as its previous one is superseded by it."""
    candidates = records.get("problem_candidate", {})
    successors = {}
    for key, candidate in sorted(candidates.items()):
        if candidate.get("previous") is not None:
            successors.setdefault(candidate["previous"], key)
    files = {}
    for key, candidate in candidates.items():
        entry = _describe("problem_candidate", key, candidate)
        if key in successors:
            entry["superseded_by"] = successors[key]
        files["intermediate/problem/candidates/" + _format_file_name(key) + ".json"] = ViewFile(
            _encode_record(candidate, entry), entry)
    return files


_PROBE_KINDS = ("probe_intent", "probe_launch", "probe", "probe_recovery")


def _build_probes(records, artifacts, inputs):
    """lab/probes/<probe>/probe.json with intent, launch, outcome and recovery and the outputs' references, the
    artifacts and pins of its files, and lab/artifacts/<id>.json for each local artifact that no pin ties to a probe."""
    probes = {}
    names = {"probe_intent": "intent", "probe_launch": "launch", "probe": "outcome", "probe_recovery": "recovery"}
    for kind in _PROBE_KINDS:
        for key, record in records.get(kind, {}).items():
            probes.setdefault(key, {"artifacts": [], "pins": [], "local_artifacts": []})[names[kind]] = (
                _bound_embedded_record(kind, key, record))
    for key, record in sorted(records.get("probe_artifact", {}).items()):
        probes.setdefault(record["probe_id"], {"artifacts": [], "pins": [], "local_artifacts": []})["artifacts"].append(
            _bound_embedded_record("probe_artifact", key, record))
    pinned = set()
    for key, pin in sorted(records.get("probe_artifact_pin", {}).items()):
        local_id = pin["payload"]["id"]
        local = records.get("local_artifact", {}).get(local_id)
        for origin in pin["origins"]:
            view = probes.setdefault(origin["probe_id"], {"artifacts": [], "pins": [], "local_artifacts": []})
            view["pins"].append(_bound_embedded_record("probe_artifact_pin", key, pin))
            if local is not None and all(item["id"] != local_id for item in view["local_artifacts"]):
                view["local_artifacts"].append(_bound_embedded_record("local_artifact", local_id, local))
            pinned.add(local_id)
    files = {}
    for probe_id, view in probes.items():
        document = dict({name: None for name in names.values()}, **view)
        intent = records.get("probe_intent", {}).get(probe_id) or {}
        files["intermediate/lab/probes/" + _format_file_name(probe_id) + "/probe.json"] = ViewFile(
            _encode(document), _describe("probe_intent", probe_id, intent))
    for key, record in records.get("local_artifact", {}).items():
        if key not in pinned:
            entry = _describe("local_artifact", key, record)
            files["intermediate/lab/artifacts/" + _format_file_name(key) + ".json"] = ViewFile(
                _encode_record(record, entry), entry)
    return files


_RUN_KINDS = ("execution_admission", "execution_binding", "execution_claim", "execution_prelaunch",
              "execution_outcome", "execution_observation", "execution_remote_release")


def _build_runs(records, artifacts, inputs):
    """lab/runs/<admission>/run.json with every record of one admitted run, result.json and log.txt with the summary
    and the log that reconcile-run observed, and lab/executions/<id>.json for each execution that no outcome names."""
    files = {}
    for admission_id in sorted({key for kind in _RUN_KINDS for key in records.get(kind, {})}):
        view = {kind.replace("execution_", ""): _bound_embedded_record(kind, admission_id, records.get(kind, {}).get(
            admission_id)) for kind in _RUN_KINDS}
        outcome = records.get("execution_outcome", {}).get(admission_id)
        observation = records.get("execution_observation", {}).get(admission_id)
        if outcome is not None:
            execution_id = outcome["execution_id"]
            view["execution"] = _bound_embedded_record("execution", execution_id,
                                                      records.get("execution", {}).get(execution_id))
        else:
            view["execution"] = None
        kind = next(kind for kind in _RUN_KINDS if admission_id in records.get(kind, {}))
        entry = _describe(kind, admission_id, records[kind][admission_id])
        folder = "intermediate/lab/runs/" + _format_file_name(admission_id) + "/"
        files[folder + "run.json"] = ViewFile(_encode(view), entry)
        if observation is not None:
            # The same bytes that json_projection gives the run's summary.json under research/runs/.
            files[folder + "result.json"] = ViewFile(
                (json.dumps(observation["summary"], ensure_ascii=False, indent=2) + "\n").encode("utf-8"), entry)
            if observation.get("log") is not None and not is_sealed_artifact(observation["log"]):
                files[folder + "log.txt"] = ViewFile(observation["log"], entry)
    admitted = {outcome["execution_id"] for outcome in records.get("execution_outcome", {}).values()}
    for key, execution in records.get("execution", {}).items():
        if key not in admitted:
            entry = _describe("execution", key, execution)
            files["intermediate/lab/executions/" + _format_file_name(key) + ".json"] = ViewFile(
                _encode_record(execution, entry), entry)
    return files


def _build_remote(records, artifacts, inputs):
    files = {}
    for kind, tree in (("remote_intent", "study/remote-intents"), ("remote_observation", "study/remote-observations")):
        for key, record in records.get(kind, {}).items():
            entry = _describe(kind, key, record)
            files["intermediate/" + tree + "/" + _format_file_name(key) + ".json"] = ViewFile(
                _encode_record(record, entry), entry)
    return files


def _build_score_history(records, artifacts, inputs):
    """intermediate/study/score-history.json: the scores of every recorded manuscript review in recorded order."""
    reviews = sorted(records.get("manuscript_review", {}).values(),
                     key=lambda review: (_read_revision(review) or 0, review["id"]))
    if not reviews:
        return {}
    bundles = {bundle["digest"]: key for key, bundle in records.get("publication_bundle", {}).items()}
    history = [dict({key: review["core"].get(key) for key in ("overall", "soundness", "presentation", "contribution",
                                                               "decision")},
                    review_id=review["id"], bundle_id=bundles.get(review["bundle_digest"]),
                    assessor_id=review["assessor"]["id"], reviewed_revision=_read_revision(review))
               for review in reviews]
    return {"intermediate/study/score-history.json": ViewFile(_encode(history), {
        "kind": "manuscript_review", "id": None, "recorded_revision": _read_revision(reviews[-1])})}


def _build_check_reports(records, artifacts, inputs):
    """intermediate/checks/: copies of the citation and derivation check reports that the gates read in .exactory/."""
    return {"intermediate/checks/" + name: ViewFile(data, {"source": ".exactory/" + name})
            for name, data in inputs.check_reports.items()}


VIEW_GROUPS = (
    ViewGroup("records", tuple(_RECORD_TREES), _RECORD_TREE_READS, _build_records),
    ViewGroup("value_reviews", ("value_review", "research_support"), _SERIES_KINDS + ("review_epoch",), _build_value_reviews),
    ViewGroup("bundles", ("publication_bundle",), ("publication_selection",) + _DEPOSIT_KINDS, _build_bundles),
    ViewGroup("deposits", _DEPOSIT_KINDS, (), _build_deposits),
    ViewGroup("review_series", _SERIES_KINDS, _ADOPTING_KINDS + _STAGED_KINDS, _build_series),
    ViewGroup("staged_reviews", _STAGED_KINDS, _SERIES_KINDS, _build_staged_records),
    ViewGroup("review_routes", ("review_route", "review_probe_invocation", "review_route_probe"), (), _build_routes),
    ViewGroup("problem_candidates", ("problem_candidate",), (), _build_candidates),
    ViewGroup("lab_probes", _PROBE_KINDS + ("probe_artifact", "probe_artifact_pin", "local_artifact"), (),
              _build_probes),
    ViewGroup("lab_runs", _RUN_KINDS + ("execution",), (), _build_runs),
    ViewGroup("remote", ("remote_intent", "remote_observation"), (), _build_remote),
    ViewGroup("score_history", (), ("manuscript_review", "publication_bundle"), _build_score_history),
    ViewGroup("check_reports", (), (), _build_check_reports),
)
# Records of evidence, bookkeeping, plans, configuration and selection pointers have no view of their own; a group
# reads a pointer to name the record it selects as current.
_UNPLACED_KINDS = (
    "acquisition_operation", "alias", "availability", "bundle_selection", "cohort_abstract_selection",
    "cohort_enumeration", "cohort_enumeration_history", "cohort_exclusion", "cohort_member", "cohort_partition_entry",
    "cohort_partition_member", "cohort_sample", "cohort_sample_selection", "cohort_seen", "collection",
    "collection_page", "configuration", "constitution", "fulltext_requirement", "literature_operation",
    "literature_scope", "literature_search", "loop_closure", "loop_closure_selection", "native_import_identity",
    "problem_citation_metadata", "reading", "reading_batch", "reference_occurrence", "reference_resolution",
    "screening", "screening_checkpoint",
    "screening_checkpoint_selection", "search_binding", "search_selection", "source", "source_bundle",
    "source_deferral", "source_deferral_selection", "source_import", "source_resumption", "synthesis",
    "synthesis_selection", "visual_asset", "visual_asset_selection", "work", "work_assertion", "work_family",
    # Budgets and resource accounting.
    "resource_account", "resource_budget", "strategy_account",
    # Workspace state, the objective's configuration, and the plan records of the managed study.
    "workspace", "workspace_adoption", "workspace_decision", "workspace_decision_history", "research_intent",
    "research_objective", "objective_lineage", "research_commitment", "research_lead", "research_work_item",
    "research_source_impact", "grand_challenge", "strategy_dossier", "cycle", "cycle_plan", "depth_plan",
    "development_scope", "publication_scope", "round_adapter",
    # Selection pointers.
    "depth_plan_selection", "development_selection", "grand_challenge_selection", "native_attempt_current",
    "problem_selection", "publication_scope_selection", "publication_selection", "scoped_review_selection",
    "strategy_selection", "verification_selection", "work_item_selection",
)
PLACEMENT_BY_KIND = dict({kind: "none" for kind in _UNPLACED_KINDS},
                         **{kind: group.name for group in VIEW_GROUPS for kind in group.kinds})
# The kinds whose records a render reads; an event that writes none of them leaves the views as they are.
_VIEW_KINDS = frozenset(kind for group in VIEW_GROUPS for kind in group.kinds + group.reads)


def compute_view_model(records, artifacts, inputs):
    """{path from the workspace root: ViewFile} of every view file except the README and index of each folder."""
    artifacts = Evaluation.of(records, artifacts)
    model = {}
    for group in VIEW_GROUPS:
        model.update(group.build(records, artifacts, inputs))
    return model


def find_workspace_root(store_root):
    """The directory that holds final/ and intermediate/: the store root, or its parent when the store root is a
    math-solver controller root, whose direct subfolders are node workspaces."""
    return store_root.parent if find_entry_kind(store_root, ".search/tree.json") == "regular" else store_root


def _is_shared_workspace_root(store_root):
    """Whether a store at a controller root would share its workspace root with the store of the parent directory."""
    return (store_root.parent == find_workspace_root(store_root)
            and find_entry_kind(store_root.parent, ".exactory/research.sqlite3") == "regular")


def _is_controller_index(path):
    """Whether path is the index of a controller root, final/math/<rel>/index.json or intermediate/math/<rel>/index.json:
    <rel> is the controller root's name, and this store's deliveries are no controller root."""
    parts = path.split("/")
    return (len(parts) == 4 and parts[0] in ("final", "intermediate") and parts[1] == "math"
            and parts[3] == "index.json" and not path.startswith(_DELIVERIES_FOLDERS))


def _read_inputs(store_root, workspace_root):
    """(_ViewInputs, {path: sha256}) for the controller indexes under final/math/ and the check reports."""
    adoptions, check_reports, digests = {}, {}, {}
    for relative in _CHECK_REPORT_PATHS:
        if find_entry_kind(store_root, relative) == "regular":
            check_reports[relative.rsplit("/", 1)[1]] = data = read_file(store_root, relative)
            digests[relative] = hashlib.sha256(data).hexdigest()
    for directory, subdirectories, names in os.walk(workspace_root / "final/math"):
        relative = os.path.relpath(directory, workspace_root).replace(os.sep, "/") + "/"
        if relative != "final/math/":
            subdirectories[:] = []
        if ("index.json" in names and _is_controller_index(relative + "index.json")
                and find_entry_kind(workspace_root, relative + "index.json") == "regular"):
            data = read_file(workspace_root, relative + "index.json")
            digests[relative + "index.json"] = hashlib.sha256(data).hexdigest()
            try:
                index = strict_json(data)
            except ResearchError:
                continue
            for adoption in index.get("adoptions", []) if isinstance(index, dict) else []:
                if isinstance(adoption, dict) and isinstance(adoption.get("assignment_id"), str):
                    adoptions.setdefault(adoption["assignment_id"], []).append(
                        {"kind": "controller", "index": relative + "index.json", "review_id": adoption.get("review_id"),
                         "adopting_record": adoption.get("adopting_record")})
    return _ViewInputs(adoptions, check_reports), digests


@contextmanager
def _hold_lock(store_root):
    """Serialize renderers with a bounded wait outside the authoritative store lock."""
    with _Workspace(store_root).directory(".exactory") as directory:
        descriptor = os.open(_LOCK_FILE_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        deadline = time.monotonic() + _RENDER_LOCK_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ResearchError("output_view_render_deferred", "The output renderer is busy.") from error
                time.sleep(min(0.025, remaining))
        yield
    finally:
        os.close(descriptor)


def _read_marker(store_root):
    if find_entry_kind(store_root, MARKER_PATH) == "absent":
        return {"schema": _MARKER_SCHEMA_NAME, "revision": 0, "event_digest": None, "plugin_version": None,
                "inputs": {}, "files": {}, "errors": {}, "failure": None}
    return strict_json(read_file(store_root, MARKER_PATH))


def read_rendered_revision(store):
    """The store revision that the views show; 0 before the first render."""
    return _read_marker(store.root)["revision"]


def find_view_advisories(store, revision):
    """The advisory output_views_stale while the views may not show the records at revision: with its reason, and
    with the failing path and error of the last render when one failed. A field that does not apply to the reason is
    None, and the controller's advisory has the same fields. No gate reads it."""
    def advise(reason, rendered=None, path=None, error=None):
        return [obligation("output_views_stale", STALE_VIEWS_EXPLANATION, action=STALE_VIEWS_ACTION, reason=reason,
                           rendered_revision=rendered, revision=revision, path=path, error=error)]
    if _is_shared_workspace_root(store.root):
        return advise("shared_workspace_root", path=str(store.root.parent / ".exactory/research.sqlite3"))
    try:
        marker = _read_marker(store.root)
    except ResearchError as error:
        return advise("marker_unreadable", path=MARKER_PATH, error=error.code)
    rendered = marker["revision"]
    if any(find_entry_kind(store.root, path) != "absent" for path in (DIRTY_PATH, _RENDERING_PATH)):
        return advise("render_deferred", rendered, DIRTY_PATH)
    if marker["failure"] is not None:
        return advise("render_failed", rendered, marker["failure"]["path"], marker["failure"]["error"])
    if marker["errors"]:
        path = sorted(marker["errors"])[0]
        return advise("file_errors", rendered, path, marker["errors"][path])
    if rendered == 0:
        return advise("not_rendered", rendered) if revision > 0 else []
    if marker["plugin_version"] != plugin_version():
        return advise("plugin_changed", rendered)
    _, digests, _ = store.read_changes(rendered)
    if digests[rendered] != marker["event_digest"]:
        return advise("history_changed", rendered)
    return advise("behind", rendered) if rendered < revision else []


def render_workspace_views(store_root):
    """Render the views of the research store at store_root after a command, once its last commit is made and every
    store and controller lock is released. store_root is the directory that holds .exactory/research.sqlite3; None,
    or a directory without a store, renders nothing. A failure prints one warning line on stderr, records the failing
    path and error in the marker and never raises, so the command's response and exit status stay as they are.
    Returns the summary of the render, or None when nothing was rendered."""
    return _render_safely(lambda: store_root)


def render_views_after_command(start):
    """Render the views of the workspace that holds start, the directory a command worked on (None for the current
    directory), as render_workspace_views does. The workspace lookup runs inside the same protection, so a failure to
    find it, for example a deleted current directory, also leaves the command's result as it is."""
    return _render_safely(lambda: find_workspace(start, required=False))


def _render_safely(find_store_root):
    store_root = None
    try:
        store_root = find_store_root()
        if store_root is None or find_entry_kind(store_root, ".exactory/research.sqlite3") != "regular":
            return None
        return render_pending(Store(store_root))
    except Exception as error:  # The command's own result stands whatever its views do.
        failure = _describe_failure(error)
        place = " at " + failure["path"] if failure["path"] else ""
        print("Output views were not rendered (" + failure["error"] + place + "): " + " ".join(str(error).split())
              + " Run " + STALE_VIEWS_ACTION + " after fixing the cause.", file=sys.stderr)
        if store_root is not None:
            _record_failure(store_root, failure)
        return None


def _describe_failure(error):
    if isinstance(error, ResearchError):
        return {"path": (error.details or {}).get("path"), "error": error.code}
    return {"path": getattr(error, "filename", None), "error": type(error).__name__}


def _record_failure(store_root, failure):
    """Keep the failure in the marker, without advancing its revision, so that status and next report it."""
    try:
        with _hold_lock(store_root):
            write_projection(store_root, MARKER_PATH, _encode(dict(_read_marker(store_root), failure=failure)))
    except (ResearchError, OSError):
        # A store whose marker cannot be written still reports its views as behind.
        try:
            _mark_dirty(store_root)
        except (ResearchError, OSError):
            pass


def _mark_dirty(store_root):
    """Durably request a full render without needing the busy rendering lock."""
    write_projection(store_root, DIRTY_PATH, _encode({"schema": "exactory-output-views-dirty-v1", "render": "full"}))


def _claim_dirty(store_root):
    """Move only work known before this render; concurrent requests keep their own marker."""
    if find_entry_kind(store_root, DIRTY_PATH) != "absent":
        with checked_parent(store_root, DIRTY_PATH) as (directory, name):
            os.replace(name, _RENDERING_PATH.rsplit("/", 1)[1], src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
    return find_entry_kind(store_root, _RENDERING_PATH) != "absent"


def _finish_dirty(store_root):
    with checked_parent(store_root, _RENDERING_PATH) as (directory, name):
        try:
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
        except FileNotFoundError:
            pass


def render_pending(store):
    """Render the views unless no event after the marker wrote a kind that they show and nothing else they show
    changed; the marker then only advances."""
    return _render(store, full=False)


def render_all(store):
    """Render every view from the current records, for export --kind outputs."""
    return _render(store, full=True)


def _render(store, *, full):
    if _is_shared_workspace_root(store.root):
        return {"revision": None, "rendered": False, "reason": "shared_workspace_root"}
    try:
        with _hold_lock(store.root):
            return _render_locked(store, full=full)
    except ResearchError as error:
        if error.code != "output_view_render_deferred":
            raise
        _mark_dirty(store.root)
        return {"revision": None, "rendered": False, "deferred": True,
                "reason": "render_lock_timeout", "dirty_marker": DIRTY_PATH}


def _render_locked(store, *, full):
    workspace_root = find_workspace_root(store.root)
    full = _claim_dirty(store.root) or full
    marker = _read_marker(store.root)
    inputs, input_digests = _read_inputs(store.root, workspace_root)
    version = plugin_version()
    if (not full and marker["revision"] > 0 and not marker["errors"] and marker["failure"] is None and marker["plugin_version"] == version
            and marker["inputs"] == input_digests):
        revision, digests, kinds = store.read_changes(marker["revision"])
        if digests[marker["revision"]] == marker["event_digest"] and not kinds & _VIEW_KINDS:
            if revision != marker["revision"]:
                marker = dict(marker, revision=revision, event_digest=digests[revision])
                write_projection(store.root, MARKER_PATH, _encode(marker))
            return _summarize(marker)
    revision, event_digest, records = store.read_kinds(sorted(_VIEW_KINDS))
    artifacts = store.build_artifact_store()
    model = compute_view_model(records, artifacts, inputs)
    files, errors = _write_views(workspace_root, store.root, marker, model, artifacts)
    marker = {"schema": _MARKER_SCHEMA_NAME, "revision": revision, "event_digest": event_digest,
              "plugin_version": version, "inputs": input_digests, "files": files, "errors": errors,
              "failure": None}
    write_projection(store.root, MARKER_PATH, _encode(marker))
    if not errors:
        _finish_dirty(store.root)
    return _summarize(marker, model)


def _write_views(workspace_root, store_root, marker, model, artifacts):
    """Write the files of the model whose bytes differ from the owned files, remove the owned files that the model no
    longer produces, then write both indexes. Returns ({path: size, mtime and digest} of the owned files, {path: error}
    of the files that could not be written)."""
    owned, errors = marker["files"], {}
    added = sorted(set(model) - set(owned))
    for path in added:
        if find_entry_kind(workspace_root, path) != "absent":
            errors[path] = "output_view_conflict"
    if set(added) - set(errors):
        # Record the ownership of each new file before writing it, so that an interrupted render resumes on it.
        pending = {path: {"size": None, "mtime_ns": None, "sha256": None} for path in added if path not in errors}
        write_projection(store_root, MARKER_PATH, _encode(dict(marker, files=dict(owned, **pending))))
    files = {}
    for path, view in sorted(model.items()):
        if path not in errors:
            try:
                files[path] = _write_view_file(workspace_root, path, view.content, owned.get(path), artifacts)
            except (ResearchError, OSError) as error:
                errors[path] = error.code if isinstance(error, ResearchError) else type(error).__name__
                # The path stays owned, so that the next render writes it again instead of taking it for a foreign file.
                files[path] = owned.get(path) or {"size": None, "mtime_ns": None, "sha256": None}
    for path in sorted(set(owned) - set(model) - _FOLDER_FILE_PATHS):
        with describe_error_at(path):
            _remove_view_file(workspace_root, path)
    for folder, readme in _README_TEXTS.items():
        for name, data in zip(_FOLDER_FILE_NAMES, (readme.encode("utf-8"),
                                                   _encode(_build_index(workspace_root, folder, model, errors)))):
            path = folder + "/" + name
            with describe_error_at(path):
                files[path] = _write_view_file(workspace_root, path, data, owned.get(path), artifacts)
    return files, errors


def _summarize(marker, model=None):
    """The revision, the number of view files in each folder, and the number of files that could not be written."""
    counts = {folder: 0 for folder in _README_TEXTS}
    for path in marker["files"] if model is None else model:
        folder, name = path.split("/", 1)
        if name not in _FOLDER_FILE_NAMES:
            counts[folder] += 1
    return {"revision": marker["revision"], "files": counts, "errors": len(marker["errors"])}


def _build_index(workspace_root, folder, model, errors):
    """The folder's entries: each view file, each controller index, each export and each legacy file, and the paths of
    the files under the folder that no writer owns."""
    prefix = folder + "/"
    entries = []
    for path, view in sorted(model.items()):
        if path.startswith(prefix):
            entry = dict(view.entry, path=path[len(prefix):], type="view")
            if path in errors:
                entry["error"] = errors[path]
            entries.append(entry)
    exports, unowned = set(), []
    for directory, subdirectories, names in os.walk(workspace_root / folder):
        relative = os.path.relpath(directory, workspace_root).replace(os.sep, "/") + "/"
        links = [name for name in subdirectories if os.path.islink(os.path.join(directory, name))]
        for name in names + links:
            path = relative + name
            export = next((item for item in _EXPORT_FOLDERS if path.startswith(item)), None)
            if export is not None:
                exports.add(export + path[len(export):].split("/", 1)[0])
            elif path.startswith(_LEGACY_FOLDER):
                entries.append({"path": path[len(prefix):], "type": "legacy"})
            elif path.startswith(_CONTROLLER_FOLDERS) and not path.startswith(_DELIVERIES_FOLDERS):
                if _is_controller_index(path):
                    entries.append({"path": path[len(prefix):], "type": "controller"})
            elif (path not in model or path in errors) and path[len(prefix):] not in _FOLDER_FILE_NAMES:
                unowned.append(path[len(prefix):])
    entries.extend({"path": path[len(prefix):], "type": "export"} for path in exports)
    return {"schema": _INDEX_SCHEMA_NAME, "folder": folder, "files": sorted(entries, key=lambda item: item["path"]),
            "unowned": sorted(unowned)}


def _write_view_file(workspace_root, relative, content, owned, artifacts):
    """Write one view file unless the owned file already holds its bytes; returns its size, modification time and
    digest. content is bytes, or an artifact reference that is read only when the file must be written."""
    expected = content["sha256"] if isinstance(content, dict) else hashlib.sha256(content).hexdigest()
    if owned is not None and owned["sha256"] == expected:
        info = _stat_view_file(workspace_root, relative)
        if info is not None and (info.st_size, info.st_mtime_ns) == (owned["size"], owned["mtime_ns"]):
            return owned
        if info is not None and hashlib.sha256(read_file(workspace_root, relative)).hexdigest() == expected:
            return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": expected}
    write_projection(workspace_root, relative, content if isinstance(content, bytes) else artifacts.read(content))
    info = _stat_view_file(workspace_root, relative)
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": expected}


def _stat_view_file(workspace_root, relative):
    """The stat of a regular view file, or None when it is absent or not a regular file."""
    if find_entry_kind(workspace_root, relative) != "regular":
        return None
    with checked_parent(workspace_root, relative) as (directory, name):
        return os.stat(name, dir_fd=directory, follow_symlinks=False)


def _remove_view_file(workspace_root, relative):
    """Remove an owned file, then each parent directory inside the folder that it leaves empty."""
    with checked_parent(workspace_root, relative) as (directory, name):
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass
    parts = relative.split("/")
    for depth in range(len(parts) - 1, 1, -1):
        with checked_parent(workspace_root, "/".join(parts[:depth])) as (directory, name):
            try:
                os.rmdir(name, dir_fd=directory)
            except OSError:
                return
