"""Validate and assess the open-problem rubric without accessing the store."""

from .errors import ResearchError
from .operations import fields


ADMISSION_ITEMS_BEFORE_A7 = ("A1", "A2", "A3", "A4", "A5", "A6")
ADMISSION_ITEMS = ADMISSION_ITEMS_BEFORE_A7 + ("A7",)
GROUNDS = ("B1", "B2")
CRITERIA = ("V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8")
ACCESSIBILITY_CLASSES = ("E-F", "E-A", "E-N")
VERDICTS = ("pass", "fail")
LEVELS = (0, 1, 2, 3)
NON_ADMISSION_ITEMS = GROUNDS + ("priority", "attempt", "accessibility", "calibration")
ITEMS = ADMISSION_ITEMS + NON_ADMISSION_ITEMS
_SHORTFALL_ITEMS = ADMISSION_ITEMS + ("significance", "priority", "attempt", "calibration")

# The normative anchors of RUBRIC.md 0.3, one text per item: what the item requires and the
# threshold for posting. The reviewer prompts state them, the adjudicator packet delivers the one
# it adjudicates, and the skill restates them for the author.
ANCHORS = {
    "A1": {"definition": "Precision: the statement and the resolution criteria are exact mathematical statements, "
                         "and a proof or a disproof meets them without ambiguity. Formalization is optional; when a "
                         "formal statement is supplied, its correspondence to the intended mathematics is assessed.",
           "threshold": "pass"},
    "A2": {"definition": "Openness: a current supported-open finding covers the exact statement, with dated "
                         "coverage and the known status recorded for each declared neighbouring formulation, and "
                         "every decisive prior or settling source is fully read. An unresolved finding fails; an "
                         "already-solved finding redirects to the established result.",
           "threshold": "pass"},
    "A3": {"definition": "Contribution: the record identifies the contribution beyond existing formulations. For an "
                         "equivalence, reduction or sharpening of a known open question, the relationship is "
                         "established by proof or by a full reading, the original is named, and the new contribution "
                         "is distinguished from what remains unresolved.",
           "threshold": "pass"},
    "A4": {"definition": "Clarity: an appropriate researcher can understand the statement with the necessary "
                         "definitions and context. No fixed length or elementary vocabulary is required; simplicity "
                         "is a preference, never graded.",
           "threshold": "pass"},
    "A5": {"definition": "Non-routine, considered: the record lists the routine routes considered and answers each "
                         "specific objection that the question is trivial, as far as examined, from the attempt "
                         "record and the readings. The record certifies nothing about what routine methods cannot do.",
           "threshold": "pass"},
    "A6": {"definition": "Readiness: the harness has read in full every decisive source of the claim and can state "
                         "the claim's standing in its literature. A decisive source that is unavailable, or "
                         "mathematics the harness cannot assess, fails.",
           "threshold": "pass"},
    "A7": {"definition": "Direction after a refutation: a reported counterexample or another reported proof against a "
                         "conjecture that the question concerns counts as a refutation unless the record shows it wrong, and "
                         "the question then studies the world that the refutation reveals, such as the mechanism or the "
                         "structure of the counterexamples, how far and how strongly the statement fails, what other results "
                         "fall with it, or the statement that replaces it. A question whose purpose is to find where the "
                         "refuted statement still holds fails whatever its other grades and whatever form it takes; its usual "
                         "forms ask for a restricted class or for added hypotheses under which the refuted statement holds, "
                         "with a value case that rests on that survival. The purpose is read from the question and its value "
                         "case, not from its surface form, and an added value argument changes neither the purpose nor the "
                         "grade. A question that no refutation concerns passes.",
           "threshold": "pass"},
    "significance": {
        "definition": "Significance is graded on two grounds on one four-level scale, and the higher ground counts; "
                      "the grounds never add. Level 0, absent: no specific account. Level 1, asserted: an account is "
                      "stated, but its established premises lack evidence, or its argued part does not answer the "
                      "strongest objection. Level 2, supported: the established premises have appropriate evidence "
                      "(verified proofs, decisive readings or admitted computations); the account identifies a "
                      "substantial mathematical advance, explains its relevance, and answers the strongest objection; "
                      "the remaining uncertainty is explicit. Level 3, compelling: level 2, plus a demonstrated or "
                      "compellingly argued account that a resolution would remove a consequential obstacle, meet an "
                      "important unmet need, or change an organizing explanation or connection, with an explanation "
                      "of why existing results and nearby alternatives do not already provide that gain; a conceptual "
                      "case need not contain a proved implication. Ground B1, standing: the question meets an "
                      "unresolved need of a line the community is working on now; the line is active and the "
                      "question's place in it is fixed by a reduction or another mathematical connection; activity "
                      "alone is insufficient, and shared notation is not a connection. Ground B2, influence: a "
                      "resolution changes something for mathematics whether or not the line is active, by the "
                      "carrying criterion (implications and named results that become unconditional; the competing "
                      "explanations or the mechanism distinguished; the known bounds, constructions and the gap; a "
                      "delimited limitation of a named method; implications under each answer; connections to "
                      "several methods or areas), with the argued part saying why that change matters.",
        "threshold": "max(B1, B2) >= 2, and >= 3 when the accessibility class is E-A or E-N"},
    "priority": {
        "definition": "Comparative priority: why this question and not its neighbours. Level 0: no comparison. "
                      "Level 1: the neighbours are listed (strengthenings, relaxations, model versions, siblings, "
                      "the next parameter), but the strongest case that the question is uninteresting is not named. "
                      "Level 2: credible nearby alternatives are compared by their mathematical payoff, and the "
                      "strongest relevant objection (a reformulation of a known result; a parameter increment "
                      "without a new mechanism; a resolution that settles nothing else; a neighbour that is the "
                      "better question) is answered; explanatory significance can suffice without downstream "
                      "theorems. Level 3: a concrete advantage of this formulation or target is supported by "
                      "documented mathematical distinctions; a neighbour's settledness or equivalence alone does not "
                      "establish superiority.",
        "threshold": ">= 2"},
    "attempt": {
        "definition": "The attempt passes when the harness undertook a bounded attack on the exact statement under "
                      "the solver's normal limits, or critically assessed an existing substantive attempt on the "
                      "exact statement by full reading, and the record states what was proved (footholds), what "
                      "stayed unresolved, and what limitation was demonstrated (an obstruction, with its scope). It "
                      "fails when no substantive attempt or critical assessment exists. A failure of the attempt is "
                      "no evidence of difficulty or importance, and a limitation of one argument is not a "
                      "limitation of a method.",
        "threshold": "pass"},
    "accessibility": {
        "definition": "Accessibility is descriptive and carries no value. E-F: relevant footholds provide a "
                      "meaningful entry or partial progress, established previously or in this research, and a "
                      "smallest unresolved case is identified where applicable, relative to the assessed state of "
                      "knowledge. E-A: a proposed route without established conventional footholds. E-N: no "
                      "conventional footholds and no proposed route. No class states human difficulty or a "
                      "completion time.",
        "threshold": "descriptive; E-A and E-N require significance level 3"},
    "calibration": {
        "definition": "Calibration passes when material claims are calibrated: established results, including "
                      "proved conditional implications, are distinguished from conjectures, experimental findings, "
                      "and reasoned judgments or forecasts; the scope of every computational finding is stated; no "
                      "community outcome is asserted before it is observed; no human difficulty or completion time "
                      "is claimed; the conjectured answer is kept apart from the question, with its grounds; and an "
                      "unresolved attempt is not called an obstruction.",
        "threshold": "pass"},
}


def was_graded_before_a7(grades):
    admission = grades["admission"]
    return isinstance(admission, dict) and set(admission) == set(ADMISSION_ITEMS_BEFORE_A7)


def validate_grades(grades, *, potential=False, allow_previous=False):
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
        admission = grades["admission"]
        previous = allow_previous and was_graded_before_a7(grades)
        fields(admission, ADMISSION_ITEMS_BEFORE_A7 if previous else ADMISSION_ITEMS, code=code)
        verdicts.extend(grades["admission"].values())
        verdicts.extend((grades["attempt"], grades["calibration"]))
        if grades["accessibility"] not in ACCESSIBILITY_CLASSES:
            raise ResearchError(code, "Use accessibility class E-F, E-A or E-N")
    if any(verdict not in VERDICTS for verdict in verdicts):
        raise ResearchError(code, "Rubric verdicts must be pass or fail")
    return grades


def assess_grades(grades):
    validate_grades(grades, allow_previous=True)
    shortfalls = [{"item": item, "required": "pass", "graded": grades["admission"][item]}
                  for item in ADMISSION_ITEMS if item in grades["admission"] and grades["admission"][item] != "pass"]
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
