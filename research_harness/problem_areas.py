"""Further subject areas whose standards the record's blind bars must state."""

from .errors import ResearchError
from .operations import fields, immutable_record, prepared_mutation, text
from .problem_claims import require_problem, validate_area


def record_area(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        marker = require_problem(records)
        fields(value, ("id", "area", "reason"))
        text(value["id"], "Problem area ID")
        validate_area(value["area"])
        text(value["reason"], "Reason for the further area")
        if any(assignment["role"] in ("slate", "result")
               for assignment in records.get("review_assignment", {}).values()):
            raise ResearchError("problem_area_new_record_required",
                                "A slate or result review of this record has been assigned. From then on a reviewer "
                                "can hold a candidate, and this record takes no further area. Open a new record, name "
                                "every area there with problem-area before its bars are assigned, carry the objections "
                                "with problem-objections-carry, bring the forming history with forming-import, and "
                                "record in this record with problem-candidate-transfer that the candidate was taken "
                                "up there")
        areas = records.get("problem_area", {})
        if value["area"] == marker["area"] or any(
                area["area"] == value["area"] and area["id"] != value["id"] for area in areas.values()):
            raise ResearchError("problem_area_duplicate",
                                "This subject class is the record's own area, or another problem_area record already "
                                "names it. Name a math.* subject class that the record does not hold yet")
        saved = areas.get(value["id"])
        record = dict(value, recorded_revision=saved["recorded_revision"] if saved else expected_revision + 1)
        return [immutable_record(records, "problem_area", value["id"], record)], record

    return prepared_mutation(store, "problem.area", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def find_missing_areas(records, packet):
    """The further areas not yet delivered to this bar's reviewer."""
    return [area for area in records.get("problem_area", {}).values()
            if area not in packet.get("problem_areas", [])]
