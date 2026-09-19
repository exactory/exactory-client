"""The author's contribution analysis of one measured manuscript bundle (design 7.4).

After a complete measurement, the author investigates briefly and records where the paper stands
against the study's Grand Challenge record, disposes of every contribution change the measurement
reviewers named, and lists the steps toward the challenges that the next iteration or round takes.
The investigation is kept as captured responses pinned as artifacts, not as search records: a
search record needs its capture imported first, and that import changes the record of every held
work it returns, which makes the measured bundle stale. The analysis binds the selected bundle
even when later work has made that bundle stale for publication, so a measured bundle can always
receive its analysis. The next pin and the round decision wait for it.
"""

from .artifacts import ArtifactStore
from .challenge import find_current_challenge, validate_criterion_ids
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .predictions import select_measurement_reviews
from .publication import find_selected_bundle
from .workspace import strict_json

REACHES = ("this_round", "next_round")
DISPOSITIONS = ("adopted", "rejected")
_ERROR = "invalid_contribution_analysis"


def find_analysis(records, bundle_digest):
    """The contribution analysis recorded for the bundle, or None."""
    return next((a for a in records.get("contribution_analysis", {}).values() if a["bundle_digest"] == bundle_digest), None)


def find_bundle_owing_analysis(records):
    """The selected bundle when its measurement is complete and it has no contribution analysis; otherwise None."""
    bundle = find_selected_bundle(records)
    if bundle is None or select_measurement_reviews(records, bundle) is None or find_analysis(records, bundle["digest"]) is not None:
        return None
    return bundle


def _check_investigation(records, evaluation, values):
    """At least one captured query with its saved original response and what it shows; a capture serves one analysis.

    A capture is a query with its response, because another query may return the same bytes, for example an empty
    result. The harness compares them and no more: the round assessor receives the responses and judges them."""
    if not isinstance(values, list) or not values:
        raise ResearchError(_ERROR, "Investigation must be an array with at least one captured query")
    # An analysis recorded under 0.42.0 named search ids instead and holds no captured response.
    used = {(entry["query"], entry["response"]["sha256"]) for saved in records.get("contribution_analysis", {}).values()
            for entry in saved["payload"].get("investigation", ())}
    for entry in values:
        fields(entry, ("query", "response", "finding"), code=_ERROR)
        text(entry["query"], "Investigation query", code=_ERROR)
        text(entry["finding"], "Investigation finding", code=_ERROR)
        evaluation.read(entry["response"])
        if (entry["query"], entry["response"]["sha256"]) in used:
            raise ResearchError("contribution_investigation_reused", "An earlier analysis already used this query with this "
                                "response; investigate again for this measurement", {"query": entry["query"]})


def _check_position(records, value, evidence):
    fields(value, ("criterion_ids", "established", "remaining", "evidence"), code=_ERROR)
    validate_criterion_ids(records, value["criterion_ids"], "Position criterion IDs", _ERROR)
    for key in ("established", "remaining"):
        text(value[key], "Position " + key, code=_ERROR)
    evidence.many(value["evidence"], "Position evidence")


def _check_steps(records, values, claim_ids, evidence, directions):
    """At least one step toward the challenges, each building on current claims of the bundle."""
    if not isinstance(values, list) or not values:
        raise ResearchError(_ERROR, "List at least one step toward the Grand Challenge")
    steps = {}
    for step in values:
        fields(step, ("id", "statement", "criterion_ids", "direction", "reach", "builds_on", "community", "risks", "evidence"),
               code=_ERROR)
        for key in ("id", "statement"):
            text(step[key], "Step " + key, code=_ERROR)
        if step["id"] in steps:
            raise ResearchError(_ERROR, "Step IDs must be unique")
        validate_criterion_ids(records, step["criterion_ids"], "Step criterion IDs", _ERROR)
        if step["direction"] not in directions or step["reach"] not in REACHES:
            raise ResearchError(_ERROR, "A step's direction is vertical or horizontal and its reach is this_round or next_round")
        unknown = sorted(set(strings(step["builds_on"], "Step claims", nonempty=True, code=_ERROR)) - claim_ids)
        if unknown:
            raise ResearchError("contribution_claim_unknown", "A step builds on current claims of the bundle", {"claim_ids": unknown})
        fields(step["community"], ("who", "capability", "evidence"), code=_ERROR)
        for key in ("who", "capability"):
            text(step["community"][key], "Community " + key, code=_ERROR)
        evidence.many(step["community"]["evidence"], "Community evidence")
        strings(step["risks"], "Step risks", code=_ERROR)
        evidence.many(step["evidence"], "Step evidence")
        steps[step["id"]] = step
    return steps


def _check_reviewer_changes(values, reviews, steps):
    """Each contribution change of each measurement review, disposed of exactly once."""
    expected = {(saved["id"], change) for saved in reviews
                for change in saved["core"].get("changes_for_maximum", {}).get("contribution", [])}
    if not isinstance(values, list):
        raise ResearchError(_ERROR, "Reviewer changes must be an array")
    seen = set()
    for item in values:
        fields(item, ("review_id", "change", "disposition", "reason"), ("step_id",), code=_ERROR)
        for key in ("review_id", "change", "reason") + (("step_id",) if "step_id" in item else ()):
            text(item[key], "Reviewer change " + key, code=_ERROR)
        key = (item["review_id"], item["change"])
        if key not in expected or key in seen:
            raise ResearchError(_ERROR, "Dispose of each contribution change of this measurement exactly once")
        seen.add(key)
        if item["disposition"] not in DISPOSITIONS:
            raise ResearchError(_ERROR, "A reviewer change is adopted or rejected")
        if (item["disposition"] == "adopted" and item.get("step_id") not in steps) or \
                (item["disposition"] == "rejected" and "step_id" in item):
            raise ResearchError(_ERROR, "An adopted change names a step of this analysis, and a rejected change names none")
    missing = sorted(expected - seen)
    if missing:
        raise ResearchError("reviewer_change_missing", "Dispose of every contribution change the measurement reviewers named",
                            {"missing": [{"review_id": review_id, "change": change} for review_id, change in missing]})


def record_contribution_analysis(store, payload, *, expected_revision, request_id):
    """Record the author's analysis of the selected, measured bundle against the study's Grand Challenge record."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        # rounds imports this module for the round gate, so its vocabulary and evidence validator are imported here.
        from .development import _Context
        from .rounds import DIRECTIONS, _RoundEvidence
        fields(value, ("id", "bundle_digest", "investigation", "position", "reviewer_changes", "steps"), code=_ERROR)
        text(value["id"], "Contribution analysis ID", code=_ERROR)
        bundle = find_selected_bundle(records)
        if bundle is None:
            raise ResearchError("publication_bundle_missing", "Prepare the exact PDF, abstract, bibliography and claims")
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("contribution_analysis_stale", "Analyze the selected manuscript bundle")
        if find_analysis(records, bundle["digest"]) is not None:
            raise ResearchError("contribution_analysis_duplicate", "This bundle already has its contribution analysis")
        reviews = select_measurement_reviews(records, bundle)
        if reviews is None:
            raise ResearchError("manuscript_measurement_missing", "Measure the bundle with three paired blind reviews and predictions first")
        current_challenge = find_current_challenge(records)
        if current_challenge is None:
            raise ResearchError("grand_challenge_missing", "Record the challenges ahead of the study with grand-challenge first")
        evaluation = Evaluation(records, artifacts)
        context = _Context(records, evaluation)
        context.require_objective()
        evidence = _RoundEvidence(context, bundle, error_code=_ERROR)
        _check_investigation(records, evaluation, value["investigation"])
        _check_position(records, value["position"], evidence)
        claims = strict_json(evaluation.read(bundle["files"]["claims"]["artifact"]))
        current_claim_ids = {claim["id"] for claim in claims if "superseded" not in claim}
        steps = _check_steps(records, value["steps"], current_claim_ids, evidence, DIRECTIONS)
        _check_reviewer_changes(value["reviewer_changes"], reviews, steps)
        # A later record may reuse or drop a criterion id, so the analysis keeps the record its ids were checked against.
        record = {"id": value["id"], "payload": value, "bundle_id": bundle["id"], "bundle_digest": bundle["digest"],
                  "grand_challenge": {"id": current_challenge["id"], "digest": current_challenge["digest"]},
                  "evidence": evidence.summary(), "analyzed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "contribution_analysis", value["id"], record)], record

    return prepared_mutation(store, "contribution.analyze", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
