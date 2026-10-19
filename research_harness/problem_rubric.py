"""Validate and assess the open-problem rubric without accessing the store."""

from .errors import ResearchError
from .operations import fields


ADMISSION_ITEMS = ("A1", "A2", "A3", "A4", "A5", "A6")
GROUNDS = ("B1", "B2")
CRITERIA = ("V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8")
ACCESSIBILITY_CLASSES = ("E-F", "E-A", "E-N")
VERDICTS = ("pass", "fail")
LEVELS = (0, 1, 2, 3)
ITEMS = ADMISSION_ITEMS + GROUNDS + ("priority", "attempt", "accessibility", "calibration")
_SHORTFALL_ITEMS = ADMISSION_ITEMS + ("significance", "priority", "attempt", "calibration")


def validate_grades(grades, *, potential=False):
    code = "invalid_rubric_grades"
    required = ("significance", "priority") if potential else (
        "admission", "significance", "priority", "attempt", "accessibility", "calibration")
    fields(grades, required, ("readiness",) if potential else (), code=code)
    fields(grades["significance"], GROUNDS, code=code)
    for level in list(grades["significance"].values()) + [grades["priority"]]:
        if type(level) is not int or level not in LEVELS:
            raise ResearchError(code, "Rubric levels must be integers from 0 to 3")
    verdicts = [grades["readiness"]] if potential and "readiness" in grades else []
    if not potential:
        fields(grades["admission"], ADMISSION_ITEMS, code=code)
        verdicts.extend(grades["admission"].values())
        verdicts.extend((grades["attempt"], grades["calibration"]))
        if grades["accessibility"] not in ACCESSIBILITY_CLASSES:
            raise ResearchError(code, "Use accessibility class E-F, E-A or E-N")
    if any(verdict not in VERDICTS for verdict in verdicts):
        raise ResearchError(code, "Rubric verdicts must be pass or fail")
    return grades


def assess_grades(grades):
    validate_grades(grades)
    shortfalls = [{"item": item, "required": "pass", "graded": grades["admission"][item]}
                  for item in ADMISSION_ITEMS if grades["admission"][item] != "pass"]
    minimum = 2 if grades["accessibility"] == "E-F" else 3
    if max(grades["significance"].values()) < minimum:
        shortfalls.append({"item": "significance", "required": "max(B1, B2) >= " + str(minimum),
                           "graded": grades["significance"]})
    if grades["priority"] < 2:
        shortfalls.append({"item": "priority", "required": ">= 2", "graded": grades["priority"]})
    for item in ("attempt", "calibration"):
        if grades[item] != "pass":
            shortfalls.append({"item": item, "required": "pass", "graded": grades[item]})
    return shortfalls


def assess_potential(grades, *, require_readiness=True):
    validate_grades(grades, potential=True)
    shortfalls = []
    if max(grades["significance"].values()) < 2:
        shortfalls.append({"item": "significance", "required": "max(B1, B2) >= 2",
                           "graded": grades["significance"]})
    if require_readiness and grades.get("readiness") != "pass":
        shortfalls.append({"item": "readiness", "required": "pass", "graded": grades.get("readiness")})
    return shortfalls


def rubric_objection_id(review_id, item):
    return "rubric:" + review_id + ":" + item


def parse_rubric_objection_id(value):
    if not isinstance(value, str) or not value.startswith("rubric:") or ":" not in value[7:]:
        return None
    review_id, item = value[7:].rsplit(":", 1)
    return (review_id, item) if review_id and item in _SHORTFALL_ITEMS else None
