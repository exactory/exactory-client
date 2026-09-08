"""Pinned shared preparation at native admission and actual launch boundaries.

Lock order is the native writer lock, then a short common Store read. Common
operations never acquire the native lock. Historical proof/account replay reads
only native records. Fresh work also checks the currently selected common source
bytes and policy; migration never supplies a successful research assessment.
"""

import copy
from pathlib import Path

from . import schema as s
from .admission import reference
from .errors import SearchError
from .proof import validate_review


def validate_reference(value):
    s.closed(value, "schema_version workspace profile revision snapshot_digest preparation_digest claim_binding")
    s.integer(value["schema_version"], 1, 1)
    s.integer(value["revision"])
    s.choice(value["profile"], {"research", "verification"})
    s.require(value["workspace"] == "." or isinstance(value["workspace"], str) and all(part == ".." for part in value["workspace"].split("/")),
              "Common workspace must be the attack root or a canonical ancestor", "unsafe_path")
    for key in ("snapshot_digest", "preparation_digest"):
        s.digest_string(value[key])
    binding = value["claim_binding"]
    if value["profile"] == "research":
        s.require(binding is None, "Author research uses the complete frozen native objective")
    else:
        s.closed(binding, "root_claim_digest claim_digest evidence reason")
        s.digest_string(binding["root_claim_digest"])
        s.digest_string(binding["claim_digest"])
        s.text(binding["reason"])
        s.require(bool(s.records(binding["evidence"])), "External native verification must bind exact target claim evidence")


def require_new(proposal):
    s.require(proposal["schema_version"] == 3, "New native proposals require schema_version 3 and a reviewed common foundation snapshot",
              "research_foundation_required")


class FrozenArtifacts:
    def __init__(self, content, root):
        self.content, self.root = content, root

    def read(self, value):
        from research_harness.errors import ResearchError
        if (not isinstance(value, dict) or not {"sha256", "path", "size", "media_type"} <= value.keys()
                or value["path"] != "research/sources/objects/" + value["sha256"]):
            raise ResearchError("artifact_corrupt", "Pinned source reference is not content addressed")
        raw = self.content.get_artifact(value["sha256"])
        if len(raw) != value["size"]:
            raise ResearchError("artifact_corrupt", "Pinned source size differs")
        return raw


def _common_root(root, value):
    validate_reference(value)
    path = Path(root)
    if value["workspace"] != ".":
        for _ in value["workspace"].split("/"):
            path = path.parent
    return path


def foundation_inputs(value, content):
    from research_harness.review_delivery import references
    validate_reference(value)
    snapshot = content.get_blob(value["snapshot_digest"])
    for artifact in references(snapshot):
        FrozenArtifacts(content, None).read(artifact)
    return snapshot


def _claim_binding(state, proposal, value, records, artifacts):
    from research_harness.reading import validate_read_evidence
    from research_harness.source_links import validate_link
    config = records.get("configuration", {}).get("research")
    s.require(config is not None and config["profile"] == value["profile"], "Native foundation profile differs", "research_foundation_mismatch")
    if value["profile"] == "research":
        target = config["target"]
        s.require(target is not None and target["kind"] == "objective" and target["statement"] == state["contract"]["original_claim"]["statement"],
                  "Common author preparation must preserve the native complete original objective", "research_objective_mismatch")
    else:
        s.require(proposal["role"] == "verification", "Exact-paper verification preparation cannot admit new author research", "research_profile_mismatch")
        binding = value["claim_binding"]
        s.require(binding["root_claim_digest"] == s.digest(state["contract"]["original_claim"])
                  and binding["claim_digest"] == s.digest(proposal["claim"]),
                  "The reviewed paper-source correspondence names a different native root or claim", "research_target_mismatch")
        target = config["target"]
        for link in binding["evidence"]:
            s.require(link.get("version_id") == target["id"], "Native verification claim links must identify the exact pinned target", "research_target_mismatch")
            validate_link(records, artifacts, link)
            validate_read_evidence(records, artifacts, link, depth="fulltext", target=target)


def audit_foundation(root, state, proposal, value, content, *, current=True):
    from research_harness.artifacts import ArtifactStore
    from research_harness.errors import ResearchError
    from research_harness.storage import Store
    from research_harness.synthesis import synthesis_state
    s.require(value is not None, "Historical admission requires search amend-foundation NODE before fresh work", "research_foundation_amendment_required")
    common = _common_root(root, value)
    snapshot = foundation_inputs(value, content)
    s.closed(snapshot, "schema_version profile research_revision preparation records")
    s.require(snapshot["schema_version"] == 1 and snapshot["profile"] == value["profile"] and snapshot["research_revision"] == value["revision"],
              "Reviewed common snapshot differs from its reference", "research_foundation_mismatch")
    try:
        pinned = FrozenArtifacts(content, common)
        _claim_binding(state, proposal, value, snapshot["records"], pinned)
        report = synthesis_state(snapshot["records"], pinned, value["profile"])
        s.require(report["ready"] and report == snapshot["preparation"] and report["digest"] == value["preparation_digest"],
                  "Pinned complete research preparation is unavailable or stale", "research_readiness_required")
        if current:
            actual = Store(common).snapshot()
            artifacts = ArtifactStore(common)
            _claim_binding(state, proposal, value, actual["records"], artifacts)
            latest = synthesis_state(actual["records"], artifacts, value["profile"])
            s.require(latest["ready"] and latest["digest"] == value["preparation_digest"],
                      "Current complete preparation changed; export and independently review an amendment before fresh work", "research_foundation_stale")
            return {"reference": value, "observed_revision": actual["revision"]}
        return {"reference": value, "observed_revision": value["revision"]}
    except ResearchError as error:
        raise SearchError("research_readiness_required", "Common preparation could not be verified: " + error.message,
                          {"cause": error.code, "details": error.details}) from error


def effective_foundation(state, node):
    selected = state["service"]["foundation_selection"].get(node["id"])
    if selected is not None:
        return state["service"]["foundation_amendments"][selected]["subject"]["foundation"]
    return state["proposals"][node["proposal_id"]]["record"].get("foundation")


def audit_amendment(state, node, content):
    selected = state["service"]["foundation_selection"].get(node["id"])
    if selected is not None:
        amendment = state["service"]["foundation_amendments"][selected]
        s.require(content.get_blob(selected) == amendment["subject"] and content.get_blob(s.digest(amendment["review"])) == amendment["review"],
                  "Pinned foundation amendment or independent review changed", "digest_mismatch")


def record_amendment(state, payload):
    s.closed(payload, "subject review digest")
    subject = payload["subject"]
    s.closed(subject, "node_id proposal_digest previous_snapshot_digest foundation reason")
    node = reference(state["nodes"], subject["node_id"], "foundation amendment node")
    proposal = reference(state["proposals"], node["proposal_id"], "admitted proposal")
    s.require(subject["proposal_digest"] == proposal["digest"], "An amendment cannot replace the admitted native claim or budget", "claim_mismatch")
    s.require(node["status"] != "finished" and not state["control"]["pending_moves"]
              and not any(run["status"] != "terminal" for run in state["runs"].values()),
              "Reconcile existing work before a foundation amendment", "recovery_required")
    previous = effective_foundation(state, node)
    s.require(subject["previous_snapshot_digest"] == (previous["snapshot_digest"] if previous else None),
              "The amendment must name the previously reviewed foundation", "digest_mismatch")
    validate_reference(subject["foundation"])
    s.text(subject["reason"])
    s.require(payload["digest"] == s.digest(subject), "Foundation amendment subject changed", "digest_mismatch")
    validate_review(payload["review"], payload["digest"], node["claim_digest"])
    author, reviewer = proposal["record"]["author"], payload["review"]["reviewer"]
    s.require(author["actor_id"] != reviewer["actor_id"] and author["attestation_id"] != reviewer["attestation_id"],
              "Foundation amendment requires independent review", "review_not_independent")
    s.require(payload["digest"] not in state["service"]["foundation_amendments"], "Foundation amendment already exists")
    state["service"]["foundation_amendments"][payload["digest"]] = copy.deepcopy(payload)
    state["service"]["foundation_selection"][node["id"]] = payload["digest"]


EVENT_HANDLERS = {"foundation_amended": record_amendment}
