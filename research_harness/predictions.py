"""Blind cohort predictions on the exact manuscript bundle, and the measurement summary.

A prediction is the percentile a blind assessor expects the paper to reach in the study's
frozen cohort, in the shape the market's verdict carries. Predictions are recorded and
summarized as results. The round decision needs a complete measurement (three paired reviews
and predictions), and the next pin needs the contribution analysis of a bundle whose
measurement is complete. No gate rule reads the values.
"""

from statistics import median

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .development import cycle_authors
from .publication import SCORE_SCALES, _assessor_key, _bundle, latest_reviews, validate_assessor

_ERROR = "invalid_prediction"
_COHORT_ERROR = "prediction_cohort_mismatch"


def _prediction(records, value):
    fields(value, ("corpus", "category", "windowStart", "windowEnd", "percentile", "band"), code=_ERROR)
    fields(value["band"], ("best", "worst"), code=_ERROR)
    numbers = (value["percentile"], value["band"]["best"], value["band"]["worst"])
    if any(type(n) is not int or not 1 <= n <= 100 for n in numbers):
        raise ResearchError(_ERROR, "Percentile and band are integers from 1 to 100")
    if not value["band"]["best"] <= value["percentile"] <= value["band"]["worst"]:
        raise ResearchError(_ERROR, "The band contains the percentile: best <= percentile <= worst")
    # The study's primary cohort: the first collection of the research scope, which the pinned bundle's readiness proved exists.
    definition = records["collection"][records["literature_scope"]["research"]["collection_ids"][0]]["definition"]
    expected = {"corpus": definition["corpus"], "category": definition["primaryCategory"],
                "windowStart": definition["windowStart"], "windowEnd": definition["windowEnd"]}
    if {k: value[k] for k in expected} != expected:
        raise ResearchError(_COHORT_ERROR, "Predict against the study's frozen cohort", expected)
    return value


def record_prediction(store, payload, *, expected_revision, request_id):
    """One blind assessor's cohort prediction for the exact current bundle."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        evaluation = Evaluation(records, artifacts)
        fields(value, ("id", "bundle_digest", "blind", "assessor", "prediction", "reasons"), code=_ERROR)
        text(value["id"], "Prediction ID", code=_ERROR)
        bundle = _bundle(records, evaluation)
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("publication_review_stale", "Predict on the exact current manuscript bundle")
        if value["blind"] is not True:
            raise ResearchError("review_not_independent", "Supply an identified independent blind assessor")
        validate_assessor(evaluation, value["assessor"], cycle_authors(records))
        strings(value["reasons"], "Prediction reasons", nonempty=True, code=_ERROR)
        _prediction(records, value["prediction"])
        key = _assessor_key(value["assessor"]["id"])
        for saved in records.get("manuscript_prediction", {}).values():
            if saved["bundle_digest"] == bundle["digest"] and _assessor_key(saved["payload"]["assessor"]["id"]) == key:
                raise ResearchError("manuscript_prediction_duplicate", "This assessor already predicted this exact bundle",
                                    {"prediction_id": saved["id"]})
        record = {"id": value["id"], "payload": value, "bundle_digest": bundle["digest"], "prediction": value["prediction"],
                  "reviewed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "manuscript_prediction", value["id"], record)], record

    return prepared_mutation(store, "publication.predict", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _measure(values):
    return {"median": median(values) if values else None, "spread": [min(values), max(values)] if values else None}


def _pair_measurement(records, bundle):
    """(the predictions on the bundle, the latest review of each predicting assessor in assessor order, complete)."""
    selected = [saved for saved in records.get("manuscript_prediction", {}).values()
                if saved["bundle_digest"] == bundle["digest"]]
    assessors = {_assessor_key(saved["payload"]["assessor"]["id"]) for saved in selected}
    current_reviews = latest_reviews(records, bundle["digest"])
    paired = [current_reviews[key] for key in sorted(assessors) if key in current_reviews]
    return selected, paired, len(selected) == len(assessors) == len(paired) == 3


def select_measurement_reviews(records, bundle):
    """The three measurement reviews on the bundle when its measurement is complete, otherwise None."""
    _, paired, complete = _pair_measurement(records, bundle)
    return paired if complete else None


def measurement_summary(records, bundle):
    """Measure three paired blind reviews and predictions on the exact bundle.

    Predictions identify the measurement assessors. Other manuscript reviews
    remain available to the publication gate. An incomplete or ambiguous group
    reports its counts, with no measurement value.
    """
    selected, paired, complete = _pair_measurement(records, bundle)
    cores = [saved["core"] for saved in paired]
    percentiles = [saved["prediction"]["percentile"] for saved in selected] if complete else []
    reviews = {"count": len(cores)}
    reviews.update({key: _measure([core[key] for core in cores] if complete else []) for key, _ in SCORE_SCALES})
    return {"complete": complete, "reviews": reviews,
            "predictions": {"count": len(selected), "percentile": _measure(percentiles)}}
