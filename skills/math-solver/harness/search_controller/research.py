"""Pinned shared evidence at native admission and actual launch boundaries.

Lock order is the native writer lock, then a common Store read. At final launch,
both are held through token delivery. Common operations never acquire the native
lock. Historical proof/account replay reads only native records. Fresh work also
checks retained source bytes. Verification also checks current preparation;
research never reruns a historical preparation evaluator.
"""

import copy
from contextlib import contextmanager
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
    validate_workspace(value["workspace"])
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
    s.require(proposal["schema_version"] == 3, "New native proposals require schema_version 3; verification requires a reviewed common foundation",
              "research_foundation_required")


def validate_workspace(value):
    s.require(value == "." or isinstance(value, str) and all(part == ".." for part in value.split("/")),
              "Common workspace must be the attack root or a canonical ancestor", "unsafe_path")


def record_review_workspace(state, payload):
    s.closed(payload, "workspace")
    validate_workspace(payload["workspace"])
    previous = state["service"].get("review_workspace")
    s.require(previous is None or previous == payload["workspace"],
              "The objective's common review workspace is immutable", "review_workspace_mismatch")
    for saved in state["proposals"].values():
        foundation = saved["record"].get("foundation")
        s.require(foundation is None or foundation["workspace"] == payload["workspace"],
                  "Review workspace conflicts with the recorded foundation", "review_workspace_mismatch")
    for saved in state["service"]["foundation_amendments"].values():
        s.require(saved["subject"]["foundation"]["workspace"] == payload["workspace"],
                  "Review workspace conflicts with a foundation amendment", "review_workspace_mismatch")
    state["service"]["review_workspace"] = payload["workspace"]


def needs_review_workspace(state):
    """Retain the historical root default once foundationless work is committed."""
    return (state["service"].get("review_workspace") is None
            and not state["service"]["foundation_amendments"]
            and not any(saved["record"].get("foundation") is not None for saved in state["proposals"].values()))


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


@contextmanager
def guard_foundation(root, value, *, review_root=None):
    from research_harness.errors import ResearchError
    from research_harness.storage import Store
    s.require(value is not None or review_root is not None,
              "A common review workspace is required for execution", "review_workspace_mismatch")
    try:
        with Store(review_root if review_root is not None else _common_root(root, value)).guarded_snapshot() as guard:
            yield guard
    except ResearchError as error:
        raise SearchError("research_readiness_required", "Common preparation could not be verified: " + error.message,
                          {"cause": error.code, "details": error.details}) from error


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


def audit_foundation(root, state, proposal, value, content, *, current=True, common_guard=None):
    from research_harness.artifacts import ArtifactStore
    from research_harness.errors import ResearchError
    from research_harness.storage import Store
    from research_harness.synthesis import synthesis_state
    if value is None and proposal["role"] == "research":
        return {"reference": None, "observed_revision": None}
    s.require(value is not None, "Verification requires search amend-foundation NODE before fresh work", "research_foundation_amendment_required")
    common = _common_root(root, value)
    snapshot = foundation_inputs(value, content)
    s.closed(snapshot, "schema_version profile research_revision preparation records")
    s.require(snapshot["schema_version"] == 1 and snapshot["profile"] == value["profile"] and snapshot["research_revision"] == value["revision"],
              "Reviewed common snapshot differs from its reference", "research_foundation_mismatch")
    try:
        if common_guard is not None:
            s.require(common_guard.root == common.resolve(), "Guarded preparation belongs to a different common workspace", "research_foundation_mismatch")
            common_guard.snapshot()
        pinned = FrozenArtifacts(content, common)
        _claim_binding(state, proposal, value, snapshot["records"], pinned)
        if proposal["role"] == "research":
            s.require(isinstance(snapshot["preparation"], dict) and snapshot["preparation"].get("digest") == value["preparation_digest"],
                      "Recorded preparation digest differs from its reference", "research_foundation_mismatch")
            # Inspect the exact recorded objects, independent of current selections
            # and preparation policy. The immutable native copies are checked above.
            from research_harness.review_delivery import references
            artifacts = ArtifactStore(common)
            for artifact in references(snapshot):
                artifacts.read(artifact)
            return {"reference": value, "observed_revision": value["revision"]}
        report = synthesis_state(snapshot["records"], pinned, value["profile"])
        s.require(report["ready"] and report == snapshot["preparation"] and report["digest"] == value["preparation_digest"],
                  "Pinned complete research preparation is unavailable or stale", "research_readiness_required")
        if current:
            if common_guard is None:
                actual = Store(common).snapshot()
            else:
                s.require(common_guard.root == common.resolve(), "Guarded preparation belongs to a different common workspace", "research_foundation_mismatch")
                actual = common_guard.snapshot()
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


EVENT_HANDLERS = {"foundation_amended": record_amendment, "review_workspace_recorded": record_review_workspace}
