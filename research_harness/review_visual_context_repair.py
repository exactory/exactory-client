"""Exact observed-history bindings for prospective native visual context repair."""

import copy
import json

from .errors import ResearchError
from .evidence import digest

MARKER = 'visual_context_repair'
PROTOCOL = 'exactory-visual-context-repair-v1'


def _require(condition, message):
    if not condition:
        raise ResearchError('review_visual_repair_mismatch', message)


def validate_profile(value):
    from .review_exact_text_cost_v1 import profile
    _require(isinstance(value, dict) and set(value) == {
        'protocol', 'attempt_id', 'request', 'output', 'history_digest', 'inherited_omissions', 'encoding'}
        and value['protocol'] == PROTOCOL and value['encoding'] == profile(),
        'Use the exact native visual repair and unchanged versioned emitter implementation')


def eligible(records, artifacts, value, repair):
    from . import review_context_repair as context, review_source_complete as complete
    prior = repair['predecessor']
    _require(value['role'] == prior['role'] == 'result' and value.get('subject', {}).get('kind') == 'problem'
        and all(value.get(key) == prior.get(key) for key in ('route_id', 'reviewer_id', 'author_id', 'dossier_id', 'subject'))
        and records['review_route'][prior['route_id']]['adapter'] == 'codex_cli_visual_v1',
        'A visual context repair retains the exact route, reviewer, author, subject and phase')
    old = json.loads(artifacts.read(prior['packet']))
    _require(complete.MARKER in old, 'Repair an existing complete-source visual result')
    complete.validate(records, artifacts, prior, old)
    _require(repair['slot_id'] == context.slot_id(records, prior), 'Retain the original fixed review slot')


def _history(artifacts, attempt):
    from . import review_transport as transport
    request, output = (json.loads(artifacts.read(attempt[key])) for key in ('request', 'output'))
    return transport.logical_messages(request) + [{'role': 'assistant', 'content': json.dumps(output, sort_keys=True)}]


def _origin_records(records, revision):
    """A later attempt or refusal cannot change a recorded repair's native selection."""
    result = dict(records)
    for kind in ('review_attempt', 'review_output_refusal'):
        result[kind] = {identifier: value for identifier, value in records.get(kind, {}).items()
                        if value['recorded_revision'] < revision}
    return result


def _science(records, artifacts, prior, attempt, history):
    from . import review_context_repair as context, review_protocol as protocol, review_transport as transport
    chain = context._exposure_lineage(records, prior)
    _require(len(history) == 2 * len(chain), 'Preserve every exact observed native history turn in order')
    result = []
    for index, ancestor in enumerate(chain):
        user, assistant = history[2 * index:2 * index + 2]
        _require(user.get('role') == 'user' and isinstance(user.get('content'), str)
            and user['content'].startswith(transport.PACKET_LABEL) and assistant.get('role') == 'assistant',
            'Scientific history must retain its exact observed native packet and output pairing')
        packet = json.loads(user['content'][len(transport.PACKET_LABEL):])
        output = json.loads(assistant['content'])
        native, _, _ = protocol._selected_attempt(records, artifacts, ancestor['id'])
        _require(native is not None and native['recorded_revision'] <= attempt['recorded_revision']
            and protocol.authenticate_provenance(records, artifacts, ancestor, native)
            and output == json.loads(artifacts.read(native['output'])),
            'Preserve every unchanged observed scientific output with its native invocation provenance')
        result.append({'assignment_id': ancestor['id'], 'packet': packet, 'observed_output': output,
                       'independence': 'no_longer_qualified'})
    return result


def _omissions(history, manifest):
    from . import review_transport as transport
    occurrences = {}
    for message in history:
        content = message.get('content')
        if message.get('role') != 'user' or not isinstance(content, str) or not content.startswith(transport.PACKET_LABEL):
            continue
        for entry in json.loads(content[len(transport.PACKET_LABEL):]).get('evidence', []):
            occurrences.setdefault(entry['artifact']['sha256'], []).append(entry)
    protected = set(manifest['protected_sha256']) | {row['selected_text_sha256'] for row in manifest['sources']}
    protected.update(row['sha256'] for row in manifest['restored_evidence'])
    eligible = {}
    for identity, entries in occurrences.items():
        if identity in protected or any(entry.get('content') for entry in entries):
            continue
        last = entries[-1]
        if last.get('content') == '' and last.get('encoding') == 'utf-8' and last.get('delivery') in {
                'body-elided-from-replayed-history', 'body-elided-html-original-route-limit'}:
            eligible[identity] = last
    return eligible


def prepare(records, artifacts, value, repair, packet):
    from . import review_protocol as protocol, review_source_complete as complete
    from .review_exact_text_cost_v1 import profile
    eligible(records, artifacts, value, repair)
    prior = repair['predecessor']
    state = protocol.assignment_state(records, artifacts, prior['id'])
    attempt = records['review_attempt'][state['attempt_id']]
    history = _history(artifacts, attempt)
    complete.prepare(records, artifacts, prior, state, packet)
    manifest = packet[complete.MARKER]
    omissions = _omissions(history, manifest)
    manifest['history_digest'] = digest([])
    # Rebuild current source evidence before inserting the exact actual historical
    # dictionaries, whose metadata must not silently reattach omitted artifacts.
    packet['evidence'] = protocol._evidence(artifacts, packet)
    packet['evidence'] = [copy.deepcopy(omissions.get(entry['artifact']['sha256'], entry)) for entry in packet['evidence']]
    packet['scientific_history'] = _science(records, artifacts, prior, attempt, history)
    packet[MARKER] = {'protocol': PROTOCOL, 'attempt_id': attempt['id'], 'request': attempt['request'],
        'output': attempt['output'], 'history_digest': digest(history),
        'inherited_omissions': sorted(omissions), 'encoding': profile()}
    packet.pop('digest', None)
    packet['digest'] = digest(packet)


def validate(records, artifacts, assignment, packet):
    """Return the frozen predecessor history without inheriting its independence."""
    from . import review_context_repair as context, review_protocol as protocol, review_source_complete as complete
    from .evaluation import Evaluation
    artifacts = Evaluation.of(records, artifacts)
    binding = packet.get(MARKER)
    validate_profile(binding)
    repair = assignment.get('context', {}).get('context_repair')
    _require(isinstance(repair, dict) and set(assignment['context']) == {'context_repair'}
        and assignment.get('history') == [] and assignment.get('history_assignment_id') is None
        and assignment.get('session_id') == assignment['id'], 'Use a fresh native context-repair session')
    prior = records.get('review_assignment', {}).get(repair['assignment_id'])
    _require(prior is not None, 'The exact visual repair predecessor must exist')
    eligible(records, artifacts, assignment, {'predecessor': prior, 'slot_id': assignment['slot_id']})
    context.validate_repair_origin(records, artifacts, assignment)
    origin = artifacts.once(('review_visual_repair_origin', assignment['id'], assignment['recorded_revision']),
        lambda: Evaluation.of(_origin_records(records, assignment['recorded_revision']), artifacts))
    selected, _, _ = protocol._selected_attempt(origin.records, origin, prior['id'])
    attempt = records.get('review_attempt', {}).get(binding['attempt_id'])
    _require(attempt is not None and selected is not None and selected['id'] == attempt['id']
        and attempt['assignment_id'] == prior['id']
        and attempt['recorded_revision'] < assignment['recorded_revision']
        and all(binding[key] == attempt.get(key) for key in ('request', 'output'))
        and protocol.authenticate_provenance(records, artifacts, prior, attempt),
        'Bind the exact instrumented predecessor request and unchanged observed output')
    history = _history(artifacts, attempt)
    _require(binding['history_digest'] == digest(history)
        and packet.get('scientific_history') == _science(origin.records, origin, prior, attempt, history),
        'Retain the complete exact observed scientific history')
    omissions = _omissions(history, packet[complete.MARKER])
    _require(binding['inherited_omissions'] == sorted(omissions), 'Only exact already omitted historical bodies may stay omitted')
    entries = {entry['artifact']['sha256']: entry for entry in packet['evidence']}
    _require(all(entries.get(identity) == entry for identity, entry in omissions.items()),
        'Preserve the exact actual inherited omission entries')
    for row in packet[complete.MARKER]['sources']:
        complete._complete(entries.get(row['selected_text_sha256']))
    for reference in packet[complete.MARKER]['restored_evidence']:
        complete._complete(entries.get(reference['sha256']))
    return prior, history, json.loads(artifacts.read(attempt['output']))
