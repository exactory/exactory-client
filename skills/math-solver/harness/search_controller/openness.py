"""Native compatibility boundary over the shared record-level openness rules."""

from research_harness import openness_minimum

from .errors import SearchError


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except openness_minimum.OpennessError as error:
        raise SearchError(error.code, error.message, error.details) from error


def validate_minimum(value):
    return _call(openness_minimum.validate_minimum, value)


def check_search_coverage(search):
    return openness_minimum.check_search_coverage(search)


def find_reassessment(search, formulation, selected, *, work_id=None):
    return openness_minimum.find_reassessment(search, formulation, selected, work_id=work_id)


def audit_minimum(finding, claim, records, artifacts, delivered):
    return _call(openness_minimum.audit_minimum, finding, claim["statement"], records, artifacts, delivered)


def validate_minimum_bindings(minimum, claim, records):
    return _call(openness_minimum.validate_minimum_bindings, minimum, claim["statement"], records)
