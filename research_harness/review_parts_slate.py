"""Slate records, their occurrences and carried artifact bodies for parts v3."""

from . import review_parts_prompts as prompts
from .evidence import digest

RECORD_UNIT_RULE = 'exactory-slate-record-units-v1'
SERIES_PROTOCOL = 'exactory-review-parts-v3'


def applies(value, packet):
    from .review_session_v4 import PROTOCOL as SESSION_V4
    return (value.get('role') == 'slate'
            and packet.get('subject', {}).get('kind') == 'problem'
            and 'context_repair' not in packet
            and packet.get('source_rules') != SESSION_V4)


def list_units(packet):
    """Keep record wrappers as occurrences; bodies have one independent owner."""
    units = []

    def visit(value, path):
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict) and {'record', 'cite'} <= item.keys():
                    cite = item['cite']
                    identity = {key: cite[key] for key in ('record_kind', 'id', 'digest')}
                    occurrence = dict(path=path, index=index, record_field='record', digest=digest(item),
                                      fields={key: member for key, member in item.items() if key != 'record'})
                    units.append(dict(item=item['record'], record_identity=identity,
                                      occurrences=[occurrence], citations=[cite], bodies=[]))
                else:
                    visit(item, path + [index])
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(item, path + [key])

    for key in ('forming_records', 'problem_forming_imports', 'candidates', 'account_records', 'problem_evidence'):
        visit(packet.get(key, []), [key])
    for entry in packet.get('evidence', []):
        units.append(dict(item=entry['artifact'], occurrences=[], citations=[entry['artifact']], bodies=[entry]))
    return units


def list_targets(packet):
    from .review_composition import find_rule, list_targets as list_composition_targets
    # The composition rule normally delegates to registered adapters. Its slate
    # fallback owns these same targets, so read it without delegating back here.
    rule = dict(find_rule('slate', packet), adapter=None)
    return list_composition_targets(rule, packet)


def compute_series_key(packet):
    return dict(candidates=packet.get('candidate_set_digest'), bars=digest(packet.get('bars', [])))


def can_start_series(records, artifacts, value, route, packet, prompt, history=()):
    """A new slate enters parts only after its first session request cannot fit."""
    from . import review_parts, review_protocol, review_session_v4, review_transport as transport
    from .review_problem_parts import _measure, preserves_ordinary_packet
    prior_id = value.get('context', {}).get('prior_assignment_id')
    if prior_id is not None:
        prior = records['review_assignment'][prior_id]
        return prior.get(review_parts.KEY, {}).get('adapter') == 'problem_slate'
    if (SERIES_PROTOCOL not in review_parts.NEW_SERIES_PROTOCOLS
            or not applies(value, packet) or transport.local_input_limit(route) is None):
        return False
    request = transport.request_body(route, prompt['text'], packet, history)
    if (_measure(route, request) <= transport.local_input_limit(route)
            and preserves_ordinary_packet(artifacts, packet, request)):
        return False
    for rules in review_protocol.NEW_SESSION_RULES:
        if rules != review_session_v4.PROTOCOL:
            break
        admission = review_session_v4.check_v4_admission(records, artifacts, route, 'slate', packet)
        if admission['state'] == 'record':
            return False
    return True


def validate_final(records, artifacts, assignment, output, *, attempt=None):
    from .research_decisions import validate_review_contract
    return validate_review_contract(records, artifacts, assignment, output, attempt=attempt)


def covers_items(packets, expected, key):
    from .review_parts import covers_items as covers_collection
    return covers_collection(packets, expected, key)


ADAPTER = dict(applies=applies, enumerate_units=list_units, enumerate_record_units=list_units,
               unit_rule=RECORD_UNIT_RULE, targets=list_targets, series_key=compute_series_key,
               validate_final=validate_final, covers_items=covers_items,
               final_prompt=prompts.SLATE_ADAPTER + '\n\n' + prompts.FINAL_SLATE,
               source_requests=True, protocols=(SERIES_PROTOCOL,), can_start_series=can_start_series)
