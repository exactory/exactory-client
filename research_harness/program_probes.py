"""Program-run disclosures and unread capture policy for prospective plans."""

from datetime import datetime, timezone
import difflib

from .artifacts import _relative_parts, is_sealed_artifact
from .errors import ResearchError
from .operations import fields, strings, text, timestamp


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_program_run_context(records, context, *, new=True):
    fields(context, ("cycle_id", "program_path", "read_outputs"))
    text(context["cycle_id"], "Program-run cycle ID")
    _relative_parts(context["program_path"])
    strings(context["read_outputs"], "Read output paths")
    for path in context["read_outputs"]:
        _relative_parts(path)
    if new and context["cycle_id"] in records.get("cycle_plan", {}):
        raise ResearchError("preplan_cycle_frozen", "Record program runs before freezing their plan",
                            {"cycle_id": context["cycle_id"]})


def build_program_run(launch, terminal, outputs, logs):
    context = launch["program_run"]
    named = outputs + [{"path": name, "sha256": logs[name]["sha256"]} for name in ("stdout", "stderr")]
    return {"started_at": terminal.get("started_at"), "ended_at": terminal.get("ended_at"), "outputs": named,
            "read_outputs": [item for item in named if item["path"] in context["read_outputs"]],
            "outputs_unread": not context["read_outputs"]}


def validate_program_run(intent, outcome):
    context, run = intent.get("program_run"), outcome.get("program_run")
    if context is None and run is None:
        return
    if context is None or run is None:
        raise ResearchError("invalid_input", "Program-run outcomes must retain their intent's disclosure context")
    fields(run, ("started_at", "ended_at", "outputs", "read_outputs", "outputs_unread"))
    started = timestamp(run["started_at"]) if run["started_at"] is not None else None
    ended = timestamp(run["ended_at"]) if run["ended_at"] is not None else None
    if (started is None or ended is None) and type(outcome["exit_status"]) is int:
        raise ResearchError("invalid_input", "Completed program runs must retain their observed times")
    if started is not None and ended is not None and started > ended:
        raise ResearchError("invalid_input", "Program-run end must follow its start")
    if type(run["outputs_unread"]) is not bool or run["outputs_unread"] != (not context["read_outputs"]):
        raise ResearchError("invalid_input", "Retain the intent's unread-output policy")
    if not isinstance(run["outputs"], list) or len(run["outputs"]) != len(outcome["outputs"]) + 2:
        raise ResearchError("invalid_input", "Name every captured program output and stream")
    descriptors = outcome["outputs"] + [outcome[name] for name in ("stdout", "stderr")]
    names = []
    for item, descriptor in zip(run["outputs"], descriptors):
        fields(item, ("path", "sha256"))
        _relative_parts(item["path"])
        names.append(item["path"])
        if item["sha256"] != descriptor["sha256"] or is_sealed_artifact(descriptor) != run["outputs_unread"]:
            raise ResearchError("invalid_input", "Output names must retain their captured hashes and unread policy")
    if len(set(names)) != len(names) or names[-2:] != ["stdout", "stderr"]:
        raise ResearchError("invalid_input", "Output paths must be unique and distinguish stdout and stderr")
    expected = [item for item in run["outputs"] if item["path"] in context["read_outputs"]]
    if run["read_outputs"] != expected:
        raise ResearchError("invalid_input", "Read outputs must name their actual captured paths and hashes")


def validate_prior_knowledge(records, artifacts, plan):
    prior = plan.get("prior_knowledge")
    if prior is not None:
        fields(prior, ("program", "program_path", "probe_ids", "account"), code="invalid_development")
        artifacts.read(prior["program"])
        _relative_parts(prior["program_path"])
        strings(prior["probe_ids"], "Pre-plan probe IDs", code="invalid_development")
        text(prior["account"], "What was known before the plan", code="invalid_development")
    declared = set(prior["probe_ids"]) if prior else set()
    relevant = set()
    for identifier, intent in records.get("probe_intent", {}).items():
        context = intent.get("program_run")
        if context is None:
            continue
        if (context["cycle_id"] == plan["id"] or prior is not None and (
                context["program_path"] == prior["program_path"]
                or intent["program"]["sha256"] == prior["program"]["sha256"])):
            if identifier not in records.get("probe", {}):
                raise ResearchError("preplan_probe_pending", "Reconcile each program run before freezing the plan",
                                    {"probe_id": identifier})
            relevant.add(identifier)
    missing = sorted(relevant - declared)
    if missing:
        raise ResearchError("preplan_probe_missing", "Name every pre-plan run in the account of what was known",
                            {"probe_ids": missing})
    for identifier in sorted(declared):
        intent = records.get("probe_intent", {}).get(identifier, {})
        if intent.get("program_run") is None or identifier not in records.get("probe", {}):
            raise ResearchError("preplan_probe_unknown", "Name an existing completed program-run probe",
                                {"probe_id": identifier})


def validate_prior_program(records, plan, program):
    prior = plan["payload"].get("prior_knowledge")
    if prior is not None and prior["program"]["sha256"] != program["sha256"]:
        raise ResearchError("preplan_program_mismatch", "Admit the program frozen in the plan's account",
                            {"cycle_id": plan["id"]})
    if prior is None:
        missing = sorted(identifier for identifier, intent in records.get("probe_intent", {}).items()
            if intent.get("program_run") is not None and intent["program"]["sha256"] == program["sha256"]
            and intent["revision"] < plan["planned_revision"])
        if missing:
            raise ResearchError("preplan_probe_missing", "Name every pre-plan run before admitting its program",
                                {"probe_ids": missing})


def _inventory_artifact(reference):
    if not is_sealed_artifact(reference):
        return reference
    return dict({key: value for key, value in reference.items() if key != "path"},
                sealed_path=reference["path"])


def program_probe_inventory(records, artifacts, plan, admissions):
    prior = plan["payload"].get("prior_knowledge")
    inventory = {}
    for identifier in sorted(prior["probe_ids"] if prior else []):
        intent, outcome = records["probe_intent"][identifier], records["probe"][identifier]
        comparisons = []
        for admission_id, admission in sorted(admissions.items()):
            admitted = admission["command"]["program"]
            earlier = intent["program"]["sha256"] != admitted["sha256"]
            difference = None
            if earlier:
                try:
                    before = artifacts.read(intent["program"]).decode("utf-8").splitlines(keepends=True)
                    after = artifacts.read(admitted).decode("utf-8").splitlines(keepends=True)
                except UnicodeError:
                    pass
                else:
                    difference = "".join(difflib.unified_diff(before, after,
                        fromfile="probe:" + identifier, tofile="admission:" + admission_id))
            comparisons.append({"admission_id": admission_id, "program_sha256": admitted["sha256"],
                                "earlier_version": earlier, "program_diff": difference})
        inventory[identifier] = {"id": identifier, "program": intent["program"], "inputs": intent["inputs"],
            "interpreter": intent["interpreter"], "context": intent["program_run"],
            "program_run": outcome["program_run"],
            "outputs": [_inventory_artifact(ref) for ref in outcome["outputs"]],
            "stdout": _inventory_artifact(outcome["stdout"]), "stderr": _inventory_artifact(outcome["stderr"]),
            "exit_status": outcome["exit_status"], "elapsed_seconds": outcome["elapsed_seconds"],
            "statement": "The outputs were not read." if outcome["program_run"]["outputs_unread"]
                         else "The listed outputs were read before the plan.",
            "admitted_runs": comparisons}
    return inventory
