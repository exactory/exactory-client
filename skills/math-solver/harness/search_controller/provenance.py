"""Shared exploratory origins retained across native byte imports and wrappers."""

import copy
import json
from pathlib import Path

from . import schema as s


def collect_probe_origins(root, state, digests, *, common_guard=None, content=None, common_workspace=None):
    from research_harness.errors import ResearchError
    from research_harness.probe_provenance import find_probe_artifact_origins
    from research_harness.storage import Store
    from .errors import SearchError
    from .reviewer import common_root
    common = common_root(root, state) if common_workspace is None else Path(common_workspace).resolve()
    if content is not None:
        digests = collect_evidence_identities(digests, content, state)
    digests = set(digests)
    aliases = state["service"].get("import_identities", {})
    for identity in list(digests):
        digests.update(aliases.get(identity, []))
    try:
        cache = getattr(content, "probe_snapshots", {}) if content is not None else {}
        key = (str(common), id(common_guard) if common_guard is not None else None)
        if key not in cache:
            cache[key] = (common_guard.snapshot() if common_guard is not None else Store(common).snapshot())["records"]
        records = cache[key]
        if content is not None:
            content.probe_snapshots = cache
        if common_guard is not None:
            s.require(Path(common_guard.root).resolve() == common, "Probe origin guard names another workspace", "review_workspace_mismatch")
        origins = find_probe_artifact_origins(records, [{"sha256": identity} for identity in digests])
    except ResearchError as error:
        # An untouched historical objective can precede any common store. Once
        # associated, absence or corruption must fail rather than erase origins.
        if (error.code == "store_missing" and (
                common_workspace is not None and not state["proposals"]
                or state["service"].get("review_workspace") is None
                and not any(row["record"].get("foundation") for row in state["proposals"].values()))):
            origins = []
        else:
            raise SearchError("probe_origin_unavailable", "The associated common probe origins cannot be audited",
                              {"cause": error.code}) from error
    retained = state["service"].get("probe_origins", {})
    by_identity = {s.digest(row): row for row in origins}
    for identity in digests:
        for row in retained.get(identity, []):
            by_identity[s.digest(row)] = row
    return [by_identity[key] for key in sorted(by_identity)]


def record_probe_origins(state, payload):
    s.closed(payload, "bindings")
    retained = state["service"].setdefault("probe_origins", {})
    for binding in s.records(payload["bindings"]):
        s.closed(binding, "digest origins" + (" raw_digest" if "raw_digest" in binding else ""))
        s.digest_string(binding["digest"])
        if "raw_digest" in binding:
            s.digest_string(binding["raw_digest"])
            identities = state["service"].setdefault("import_identities", {}).setdefault(binding["digest"], [])
            if binding["raw_digest"] not in identities:
                identities.append(binding["raw_digest"])
        entries = retained.setdefault(binding["digest"], [])
        for row in s.records(binding["origins"]):
            s.closed(row, "probe_id sha256 roles")
            s.text(row["probe_id"])
            s.digest_string(row["sha256"])
            s.strings(row["roles"], nonempty=True)
            for role in row["roles"]:
                s.choice(role, {"outputs", "stdout", "stderr"})
            if row not in entries:
                entries.append(copy.deepcopy(row))


def collect_evidence_identities(digests, content, state):
    """Follow explicit JSON content identities through renamed native wrappers."""
    from .reviewer import _digests, _read_optional
    pending, seen = list(digests), set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        s.require(len(seen) <= 10000, "Probe provenance dependency closure is excessive", "dependency_cycle")
        value = _read_optional(content, identity, "blob")
        if value is None:
            raw = _read_optional(content, identity, "artifact")
            if raw is not None and raw.lstrip().startswith((b"{", b"[")):
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    pass
        if value is not None:
            verification = value.get("verification") if isinstance(value, dict) else None
            run_id = verification.get("run_id") if isinstance(verification, dict) else None
            run = state["runs"].get(run_id) if isinstance(run_id, str) else None
            if (isinstance(value, dict) and set(value) == {
                    "schema_version", "kind", "claim_digest", "conclusion", "artifacts", "dependencies", "external_dependencies", "verification"}
                    and isinstance(verification, dict) and set(verification) == {
                        "run_id", "result_digest", "policy_review", "requested_declaration", "requested_type_digest"}
                    and run is not None and run.get("status") == "terminal"
                    and run.get("kind") == value["kind"] and run.get("result_digest") == verification["result_digest"]):
                # Native execution receipts authenticate a checker; their log
                # hashes are not imported scientific dependencies of the proof.
                from .evidence import audit_verification
                audit_verification(state, value, content)
                pending.extend(_digests({key: item for key, item in value.items() if key != "verification"}))
                # Only the actual receipt's result pointer is exempt. Other
                # fields, including conclusion and review evidence, stay inputs.
                pending.extend(identity for identity in _digests(verification)
                               if identity != run["result_digest"])
            else:
                pending.extend(_digests(value))
    return seen


def audit_probe_credit(root, state, digests, *, common_guard=None, content=None):
    origins = collect_probe_origins(root, state, digests, common_guard=common_guard, content=content)
    s.require(not origins, "Exploratory probe output needs native admission, interpretation and verification before proof or decision credit",
              "exploratory_evidence")


def audit_strategy_inputs(root, state, assessment, content, *, common_guard=None):
    from .evidence import audit_dependencies
    from .reviewer import objects
    references = {identity for row in objects(assessment) for identity in row.get("evidence_ids", [])}
    checkpoints = {identity.split(":", 1)[1] for identity in references if identity.startswith("checkpoint:")}
    acceptances = {identity.split(":", 1)[1] for identity in references if identity.startswith("acceptance:")}
    audit_dependencies(root, state, content, checkpoints, acceptance_ids=acceptances, common_guard=common_guard)


EVENT_HANDLERS = {"probe_origins_recorded": record_probe_origins}
