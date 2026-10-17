"""Shared exploratory origins retained across native byte imports and wrappers."""

import copy
import hashlib
import json
from pathlib import Path

from . import schema as s


def collect_probe_origins(root, state, digests, *, common_guard=None, content=None, common_workspace=None):
    from research_harness.errors import ResearchError
    from research_harness.probe_provenance import match_probe_artifact_origins
    from research_harness.storage import Store
    from .errors import SearchError
    from .reviewer import common_root
    common = common_root(root, state) if common_workspace is None else Path(common_workspace).resolve()
    if content is not None:
        digests = collect_evidence_identities(digests, content, state)
    digests = set(digests)
    aliases = state["service"].get("import_identities", {})
    aliases = {key: set(values) for key, values in aliases.items()}
    for key, values in getattr(content, "import_identities", {}).items():
        aliases.setdefault(key, set()).update(values)
    pending = list(digests)
    while pending:
        for identity in aliases.get(pending.pop(), []):
            if identity not in digests:
                digests.add(identity)
                pending.append(identity)
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
        from research_harness.artifacts import ArtifactStore
        digests = expand_import_identities(records, ArtifactStore(common), digests, native_aliases=aliases, content=content)
        origins = match_probe_artifact_origins(records, [{"sha256": identity} for identity in digests])
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


def check_import_identity(identity, artifacts, *, content=None):
    """Authenticate a raw-to-canonical alias by its original immutable bytes."""
    s.closed(identity, "schema_version canonical_digest raw")
    s.integer(identity["schema_version"], 1, 1)
    s.digest_string(identity["canonical_digest"])
    from research_harness.artifacts import describe_artifact
    from research_harness.errors import ResearchError
    from .reviewer import _read_optional
    try:
        raw = artifacts.read(identity["raw"])
    except ResearchError as error:
        if error.code != "artifact_missing" or content is None:
            raise
        raw = _read_optional(content, identity["raw"]["sha256"], "artifact")
        if raw is None:
            raise
        s.require(describe_artifact(raw, identity["raw"]["media_type"]) == identity["raw"],
                  "Recovered raw identity differs from its committed descriptor", "invalid_import_identity")
    check_normalized_identity(identity["canonical_digest"], raw)
    return raw


def check_normalized_identity(digest, raw):
    """Accept only the two existing lossless native JSON encodings."""
    from research_harness.errors import ResearchError
    from research_harness.probe_provenance import check_normalized_identity as check_shared_identity
    from .errors import SearchError
    try:
        return check_shared_identity(digest, raw)
    except ResearchError as error:
        raise SearchError(error.code, error.message, error.details) from error


def expand_import_identities(records, artifacts, digests, *, native_aliases=None, content=None):
    """Resolve exported aliases before asking the shared probe-origin resolver."""
    expanded = set(digests)
    aliases = {}
    for identity in records.get("native_import_identity", {}).values():
        aliases.setdefault(identity["canonical_digest"], []).append(identity)
    pending = list(expanded)
    while pending:
        digest = pending.pop()
        related = set((native_aliases or {}).get(digest, []))
        for identity in aliases.get(digest, []):
            check_import_identity(identity, artifacts, content=content)
            related.add(identity["raw"]["sha256"])
        for raw_digest in related:
            if raw_digest not in expanded:
                expanded.add(raw_digest)
                pending.append(raw_digest)
        s.require(len(expanded) <= 10000, "Probe identity closure is excessive", "dependency_cycle")
    return expanded


def retain_import_identity(content, digest, raw):
    """Stage original bytes with every content-producing JSON normalization."""
    raw_digest = hashlib.sha256(raw).hexdigest()
    if digest != raw_digest:
        content.put_artifact(raw)
        if not hasattr(content, "import_identities"):
            content.import_identities = {}
        content.import_identities.setdefault(digest, set()).add(raw_digest)


def append_import_identities(payload, content):
    """Persist staged normalization aliases in the same native event as content."""
    recorded = {(row["digest"], row.get("raw_digest")) for operation in payload["operations"]
                if operation["kind"] == "probe_origins_recorded" for row in operation["payload"]["bindings"]}
    bindings = [{"digest": digest, "raw_digest": raw_digest, "origins": []}
                for digest, sources in sorted(getattr(content, "import_identities", {}).items())
                for raw_digest in sorted(sources) if (digest, raw_digest) not in recorded]
    if bindings:
        payload["operations"].append({"kind": "probe_origins_recorded", "payload": {"bindings": bindings}})


def collect_import_sources(digest, state, content, artifacts, records):
    """Recover checked original JSON bytes without publishing anything."""
    from research_harness.artifacts import describe_artifact
    from .reviewer import _read_optional
    sources, pending, seen = {}, [digest], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        s.require(len(seen) <= 10000, "Export identity closure is excessive", "dependency_cycle")
        for identity in records.get("native_import_identity", {}).values():
            if identity["canonical_digest"] == current:
                sources[identity["raw"]["sha256"]] = check_import_identity(identity, artifacts, content=content)
        raw_digests = set(state["service"].get("import_identities", {}).get(current, []))
        raw_digests.update(getattr(content, "import_identities", {}).get(current, []))
        for raw_digest in sorted(raw_digests):
            if raw_digest in seen or raw_digest in sources:
                continue
            raw = _read_optional(content, raw_digest, "artifact")
            s.require(raw is not None,
                      "Export the original artifact bytes when a canonical identity's raw bytes cannot be preserved",
                      "exploratory_evidence" if state["service"].get("probe_origins", {}).get(current) else "missing_evidence")
            check_normalized_identity(current, raw)
            sources[raw_digest] = raw
        pending.extend(key for key in sources if key not in seen)
    # Flatten the checked chain into portable aliases of the exported identity.
    for raw in sources.values():
        check_normalized_identity(digest, raw)
    sources.pop(digest, None)
    return [(describe_artifact(sources[key], "application/json"), sources[key]) for key in sorted(sources)]


def publish_import_identity(artifacts, digest, raw, *, publish=True):
    """Commit the checked alias before its canonical export becomes importable."""
    from research_harness.errors import ResearchError
    from research_harness.operations import immutable_record, prepared_mutation
    from research_harness.storage import Store
    from research_harness.artifacts import describe_artifact
    reference = describe_artifact(raw, "application/json")
    encodings = check_normalized_identity(digest, raw)
    store = Store(artifacts.root)
    for canonical in sorted(encodings - {reference["sha256"]}):
        identity = {"schema_version": 1, "canonical_digest": canonical, "raw": reference}
        key = canonical + ":" + reference["sha256"]

        def prepare(records, value):
            return [immutable_record(records, "native_import_identity", key, value)], value

        while True:
            try:
                prepared_mutation(store, "native_math.import_identity", identity, prepare,
                                  expected_revision=store.revision, request_id="native-import-identity:" + key)
                break
            except ResearchError as error:
                if error.code != "stale_revision":
                    raise
    if publish:
        artifacts.put(raw, "application/json")


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
