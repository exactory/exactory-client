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
from .operations import fields, immutable_record, prepared_mutation, normalized_text, strings, text
from .predictions import measurement_state, select_measurement_reviews
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


def _check_investigation(records, evaluation, values, refresh=None):
    """Captured deciding queries or a justified assessment that current evidence suffices.

    A capture is a query with its response, because another query may return the same bytes, for example an empty
    result. The harness compares them and no more: the round assessor receives the responses and judges them."""
    if not isinstance(values, list) or (not values and refresh is None):
        raise ResearchError(_ERROR, "Investigation must be an array with captured queries or an explicit current-evidence assessment")
    for entry in values:
        fields(entry, ("query", "response", "finding"), ("question",) if refresh is not None else (), code=_ERROR)
    if refresh is not None:
        from .strategy import evidence as validate_evidence
        fields(refresh, ('status', 'reason', 'evidence', 'questions'), code=_ERROR)
        if refresh['status'] not in ('current', 'refresh_required'):
            raise ResearchError(_ERROR, 'Refresh status is current or refresh_required')
        text(refresh['reason'], 'Source-refresh reasoning', code=_ERROR)
        validate_evidence(records, evaluation, refresh['evidence'])
        strings(refresh['questions'], 'Unresolved deciding questions', code=_ERROR)
        if refresh['status'] == 'current' and refresh['questions']:
            raise ResearchError('contribution_refresh_pending', 'An unresolved deciding question requires targeted investigation')
        if refresh['status'] == 'refresh_required':
            if not refresh['questions'] or not values:
                raise ResearchError('contribution_refresh_pending', 'Capture the investigation needed by each deciding question')
            covered = {entry.get('question') for entry in values}
            if not set(refresh['questions']) <= covered:
                raise ResearchError('contribution_refresh_pending', 'Link each required deciding question to its captured investigation')
    # An analysis recorded under 0.42.0 named search ids instead and holds no captured response.
    used = {(entry["query"], entry["response"]["sha256"]) for saved in records.get("contribution_analysis", {}).values()
            for entry in saved["payload"].get("investigation", ())}
    for entry in values:
        text(entry["query"], "Investigation query", code=_ERROR)
        text(entry["finding"], "Investigation finding", code=_ERROR)
        evaluation.read(entry["response"])
        if (entry["query"], entry["response"]["sha256"]) in used and not (refresh and refresh["status"] == "current"):
            raise ResearchError("contribution_investigation_reused", "An earlier analysis already used this query with this "
                                "response; investigate again for this measurement", {"query": entry["query"]})


def _check_position(records, value, evidence):
    fields(value, ("criterion_ids", "established", "remaining", "evidence"), code=_ERROR)
    validate_criterion_ids(records, value["criterion_ids"], "Position criterion IDs", _ERROR)
    for key in ("established", "remaining"):
        text(value[key], "Position " + key, code=_ERROR)
    evidence.many(value["evidence"], "Position evidence")


def _check_steps(records, values, claim_ids, evidence, directions, *, managed=False):
    """At least one step toward the challenges, each building on current claims of the bundle."""
    if not isinstance(values, list) or (not values and not managed):
        raise ResearchError(_ERROR, "List evidenced next steps, or record a managed closure with no invented follow-up")
    steps = {}
    for step in values:
        fields(step, ("id", "statement", "criterion_ids", "direction", "reach", "builds_on", "community", "risks", "evidence"),
               ("work_item_id", "inherited_failures"), code=_ERROR)
        for key in ("id", "statement"):
            text(step[key], "Step " + key, code=_ERROR)
        if step["id"] in steps:
            raise ResearchError(_ERROR, "Step IDs must be unique")
        validate_criterion_ids(records, step["criterion_ids"], "Step criterion IDs", _ERROR)
        if step["direction"] not in directions or step["reach"] not in REACHES:
            raise ResearchError(_ERROR, "A step's direction is vertical or horizontal and its reach is this_round or next_round")
        inherited = step.get('inherited_failures', [])
        unknown = sorted(set(strings(step["builds_on"], "Step claims", nonempty=not bool(inherited), code=_ERROR)) - claim_ids)
        if inherited:
            evidence.many(inherited, 'Retained failure evidence')
        if managed and not step.get('work_item_id'):
            raise ResearchError('contribution_work_item_missing', 'Every proposed step must retain its stable scientific work item')
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


def _check_reviewer_changes(values, reviews, steps, *, managed=False):
    """Each contribution change of each measurement review, disposed of exactly once."""
    expected = {(saved["id"], change) for saved in reviews
                for axis, changes in saved["core"].get("changes_for_maximum", {}).items()
                if managed or axis == 'contribution' for change in changes}
    if not isinstance(values, list):
        raise ResearchError(_ERROR, "Reviewer changes must be an array")
    seen = set()
    for item in values:
        fields(item, ("review_id", "change", "disposition", "reason"), ("step_id", "work_item_id"), code=_ERROR)
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


def review_request_id(review_id, change):
    """A request keeps its identity across analyses and work-item revisions."""
    return review_id + ':' + digest(change)


def _work_links(records, value, reviews, steps):
    from .strategy import get_record
    linked = set()
    by_review = {review['id']: review for review in reviews}
    def current(identifier):
        saved = get_record(records, 'research_work_item', identifier, 'contribution_work_item_missing')
        selected = records.get('work_item_selection', {}).get(saved['payload']['item_id'])
        if selected is None or selected['id'] != identifier:
            raise ResearchError('contribution_work_item_stale', 'Use the current immutable revision of the retained work item')
        linked.add(identifier)
        return saved['payload']
    for step in steps.values():
        current(step.get('work_item_id'))
    for change in value['reviewer_changes']:
        work = current(change.get('work_item_id'))
        reviewer = by_review[change['review_id']]['assessor']['id']
        request_id = review_request_id(change['review_id'], change['change'])
        if not any(request['origin'] == 'manuscript_review' and request['request_id'] == request_id
                   and normalized_text(request['statement']) == normalized_text(change['change'])
                   and normalized_text(request['reviewer']) == normalized_text(reviewer)
                   for request in work['requests']):
            raise ResearchError('contribution_work_item_mismatch', 'The work item must preserve the original reviewer request',
                                {'review_id': change['review_id'], 'request_id': request_id})
        if change['disposition'] == 'adopted' and steps[change['step_id']]['work_item_id'] != work['id']:
            raise ResearchError('contribution_work_item_mismatch', 'The adopted request and proposed step must identify the same work revision')
        if change['disposition'] == 'rejected' and work['disposition'] not in ('rejected', 'withdrawn', 'deferred'):
            raise ResearchError('contribution_work_item_mismatch', 'A rejected request keeps its actual rejection, deferral or withdrawal and reopening condition')
    return sorted(linked)


def record_contribution_analysis(store, payload, *, expected_revision, request_id):
    """Record the author's analysis of the selected, measured bundle against the study's Grand Challenge record."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        # rounds imports this module for the round gate, so its vocabulary and evidence validator are imported here.
        from .development import _Context
        from .rounds import DIRECTIONS, _RoundEvidence
        fields(value, ("id", "bundle_digest", "investigation", "position", "reviewer_changes", "steps"), ("refresh",), code=_ERROR)
        text(value["id"], "Contribution analysis ID", code=_ERROR)
        bundle = find_selected_bundle(records)
        if bundle is None:
            raise ResearchError("publication_bundle_missing", "Prepare the exact PDF, abstract, bibliography and claims")
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("contribution_analysis_stale", "Analyze the selected manuscript bundle")
        if find_analysis(records, bundle["digest"]) is not None:
            raise ResearchError("contribution_analysis_duplicate", "This bundle already has its contribution analysis")
        from .strategy import managed_research
        managed = managed_research(records)
        reviews = select_measurement_reviews(records, bundle)
        if reviews is None:
            raise ResearchError("manuscript_measurement_missing", "Measure the bundle with three paired blind reviews and predictions first")
        if managed:
            measurement = measurement_state(records, artifacts, bundle)
            if not measurement['complete']:
                raise ResearchError('manuscript_measurement_unverified',
                    'The current reviewer contexts must establish independent measurement before new contribution analysis',
                    {'obligations': measurement['independence_obligations']})
        current_challenge = find_current_challenge(records)
        if current_challenge is None:
            raise ResearchError("grand_challenge_missing", "Record the challenges ahead of the study with grand-challenge first")
        evaluation = Evaluation(records, artifacts)
        context = _Context(records, evaluation)
        context.require_objective()
        evidence = _RoundEvidence(context, bundle, error_code=_ERROR)
        _check_investigation(records, evaluation, value["investigation"], value.get('refresh'))
        _check_position(records, value["position"], evidence)
        claims = strict_json(evaluation.read(bundle["files"]["claims"]["artifact"]))
        current_claim_ids = {claim["id"] for claim in claims if "superseded" not in claim}
        steps = _check_steps(records, value["steps"], current_claim_ids, evidence, DIRECTIONS, managed=managed)
        _check_reviewer_changes(value["reviewer_changes"], reviews, steps, managed=managed)
        work_items = _work_links(records, value, reviews, steps) if managed else []
        # A later record may reuse or drop a criterion id, so the analysis keeps the record its ids were checked against.
        record = {"id": value["id"], "payload": value, "bundle_id": bundle["id"], "bundle_digest": bundle["digest"],
                  "grand_challenge": {"id": current_challenge["id"], "digest": current_challenge["digest"]},
                  "evidence": evidence.summary(), "work_item_ids": work_items,
                  "analyzed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "contribution_analysis", value["id"], record)], record

    return prepared_mutation(store, "contribution.analyze", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
