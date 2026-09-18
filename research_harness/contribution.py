"""The author's contribution analysis of one measured manuscript bundle (design 7.4).

After a complete measurement, the author records where the paper stands against the study's
Grand Challenge record, disposes of every contribution change the measurement reviewers named,
and lists the steps toward the challenges that the next iteration or round takes. The analysis
binds the selected bundle even when later work has made that bundle stale for publication, so a
measured bundle can always receive its analysis. The next pin and the round decision wait for it.
"""

from .artifacts import ArtifactStore
from .challenge import resolve_criterion_ids, validate_criterion_ids
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .literature import GRAND_CHALLENGE_PURPOSE
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .predictions import select_measurement_reviews
from .workspace import strict_json

REACHES = ("this_round", "next_round")
DISPOSITIONS = ("adopted", "rejected")
_ERROR = "invalid_contribution_analysis"


def find_analysis(records, bundle_digest):
    """The contribution analysis recorded for the bundle, or None."""
    return next((a for a in records.get("contribution_analysis", {}).values() if a["bundle_digest"] == bundle_digest), None)


def find_bundle_owing_analysis(records):
    """The selected bundle when its measurement is complete and it has no contribution analysis; otherwise None."""
    selected = records.get("publication_selection", {}).get("bundle")
    if selected is None:
        return None
    bundle = records["publication_bundle"][selected["id"]]
    if select_measurement_reviews(records, bundle) is None or find_analysis(records, bundle["digest"]) is not None:
        return None
    return bundle


def _check_searches(records, bundle, values):
    """Every search is a research grand_challenge search, and at least one was recorded after the pin."""
    identifiers = strings(values, "Contribution analysis searches", nonempty=True, code=_ERROR)
    saved = records.get("literature_search", {})
    if any(i not in saved or saved[i]["profile"] != "research" or saved[i]["purpose"] != GRAND_CHALLENGE_PURPOSE for i in identifiers):
        raise ResearchError(_ERROR, "Name the analysis's own research searches with the grand_challenge purpose")
    # A bundle pinned before 0.42.0 has no snapshot of searches; any grand_challenge search then counts.
    if set(identifiers) <= set(bundle.get("search_ids", ())):
        raise ResearchError("contribution_search_missing", "Record at least one grand_challenge search after the bundle was pinned")


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
        fields(value, ("id", "bundle_digest", "searches", "position", "reviewer_changes", "steps"), code=_ERROR)
        text(value["id"], "Contribution analysis ID", code=_ERROR)
        selected = records.get("publication_selection", {}).get("bundle")
        if selected is None:
            raise ResearchError("publication_bundle_missing", "Prepare the exact PDF, abstract, bibliography and claims")
        bundle = records["publication_bundle"][selected["id"]]
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("contribution_analysis_stale", "Analyze the selected manuscript bundle")
        if find_analysis(records, bundle["digest"]) is not None:
            raise ResearchError("contribution_analysis_duplicate", "This bundle already has its contribution analysis")
        reviews = select_measurement_reviews(records, bundle)
        if reviews is None:
            raise ResearchError("manuscript_measurement_missing", "Measure the bundle with three paired blind reviews and predictions first")
        if not resolve_criterion_ids(records):
            raise ResearchError("grand_challenge_missing", "Record the study's Grand Challenge first")
        evaluation = Evaluation(records, artifacts)
        context = _Context(records, evaluation)
        context.require_objective()
        evidence = _RoundEvidence(context, bundle)
        _check_searches(records, bundle, value["searches"])
        _check_position(records, value["position"], evidence)
        claims = strict_json(evaluation.read(bundle["files"]["claims"]["artifact"]))
        current_claim_ids = {claim["id"] for claim in claims if "superseded" not in claim}
        steps = _check_steps(records, value["steps"], current_claim_ids, evidence, DIRECTIONS)
        _check_reviewer_changes(value["reviewer_changes"], reviews, steps)
        record = {"id": value["id"], "payload": value, "bundle_id": bundle["id"], "bundle_digest": bundle["digest"],
                  "evidence": evidence.summary(), "analyzed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "contribution_analysis", value["id"], record)], record

    return prepared_mutation(store, "contribution.analyze", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
