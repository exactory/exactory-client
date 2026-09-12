"""Blind cohort predictions on the exact manuscript bundle, and the measurement summary.

A prediction is the percentile a blind assessor expects the paper to reach in the study's
frozen cohort, in the shape the market's verdict carries. Predictions are recorded and
summarized as results; no gate rule reads them.
"""

from statistics import median

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .development import cycle_authors
from .publication import _assessor_key, _bundle, latest_reviews, validate_assessor

_ERROR = "invalid_prediction"
_COHORT_ERROR = "prediction_cohort_mismatch"
_SCORE_KEYS = ("overall", "contribution", "soundness", "presentation")


def _cohort(records):
    """The study's primary cohort definition: the first collection of the research scope."""
    scope = records.get("literature_scope", {}).get("research", {})
    for identifier in scope.get("collection_ids", []):
        collection = records.get("collection", {}).get(identifier)
        if collection is not None:
            return collection["definition"]
    raise ResearchError(_COHORT_ERROR, "The study has no frozen cohort to predict against")


def _prediction(records, value):
    fields(value, ("corpus", "category", "windowStart", "windowEnd", "percentile", "band"), code=_ERROR)
    fields(value["band"], ("best", "worst"), code=_ERROR)
    numbers = (value["percentile"], value["band"]["best"], value["band"]["worst"])
    if any(type(n) is not int or not 1 <= n <= 100 for n in numbers):
        raise ResearchError(_ERROR, "Percentile and band are integers from 1 to 100")
    if not value["band"]["best"] <= value["percentile"] <= value["band"]["worst"]:
        raise ResearchError(_ERROR, "The band contains the percentile: best <= percentile <= worst")
    definition = _cohort(records)
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


def measurement_summary(records, bundle):
    """Latest review per assessor and every prediction on the bundle, as medians and spreads."""
    cores = [saved["core"] for saved in latest_reviews(records, bundle["digest"]).values()]
    percentiles = [saved["prediction"]["percentile"] for saved in records.get("manuscript_prediction", {}).values()
                   if saved["bundle_digest"] == bundle["digest"]]
    reviews = {"count": len(cores)}
    reviews.update({key: _measure([core[key] for core in cores]) for key in _SCORE_KEYS})
    return {"reviews": reviews, "predictions": {"count": len(percentiles), "percentile": _measure(percentiles)}}
