"""Native mathematics units, exact evidence citations and final bindings."""

from collections import deque

from . import native_review, review_parts_prompts as prompts
from .errors import ResearchError
from .evidence import digest


def applies(value, packet):
    native = packet.get('native')
    return (value.get('role') == 'native_math' and isinstance(native, dict)
            and 'openness_minimum' not in native['scientific_context']
            and 'reading_turn' not in packet and 'reading_record' not in packet)


def list_context_units(value, path):
    """Stop at each list: its entries own any nested scientific content."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield dict(item=item, occurrences=[dict(path=path, index=index)])
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from list_context_units(item, path + [key])


def list_units(packet):
    from .review_protocol import collect_references

    native = packet['native']
    evidence = {entry['artifact']['sha256']: entry for entry in packet.get('evidence', [])}
    available = {entry['digest'] for entry in native['evidence']}
    units = [dict(item=item, occurrences=[dict(path=['native', 'evidence'], index=index)])
             for index, item in enumerate(native['evidence'])]
    units.extend(list_context_units(native['scientific_context'], ['native', 'scientific_context']))
    for unit in units:
        unit['evidence_digests'] = available
        unit['bodies'] = [evidence[reference['sha256']]
                          for reference in collect_references(unit['item'])
                          if reference['sha256'] in evidence]
    return units


def list_citations(source):
    """Native findings cite the exact digests of the exported evidence closure."""
    available = source['evidence_digests']
    citations = []
    pending = deque([source['item']])
    item_digest = digest(source['item'])
    if item_digest in available:
        citations.append(item_digest)
    while pending:
        item = pending.popleft()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and item in available and item not in citations:
            citations.append(item)
    return citations


def list_targets(packet):
    kind = packet['native']['kind']
    findings = {'proposal': native_review.PROPOSAL_FINDINGS,
                'result': native_review.RESULT_FINDINGS + ('openness',),
                'recovery': native_review.RECOVERY_FINDINGS}[kind]
    return [dict(id=key, label=key) for key in findings]


def compute_series_key(packet):
    from .review_parts import build_frame, enumerate_units
    return digest(build_frame(packet, enumerate_units(packet, 'native_math')))


def compute_replacement_key(packet):
    """Compare exact exports across a failed first turn's required re-attestation."""
    native = dict(packet['native'])
    native['reviewer'] = dict(native['reviewer'])
    native['reviewer'].pop('attestation_id')
    return digest(native)


def validate_final(records, artifacts, assignment, output, *, attempt=None):
    from .workspace import strict_json
    packet = strict_json(artifacts.read(assignment['packet']))
    return native_review.validate_output(packet['native'], output)


def read_prior_assignment(records, artifacts, value):
    """Bind a pending native parts continuation to its observed predecessor."""
    from .operations import normalized_text
    from .review_parts import KEY, PROTOCOL
    from .review_protocol import assignment_state, find_replacement, has_ended_without_output

    prior_id = value['context']['prior_assignment_id']
    prior = records.get('review_assignment', {}).get(prior_id)
    if prior is None:
        raise ResearchError('review_record_missing', 'Missing review_assignment: ' + str(prior_id))
    if find_replacement(records, prior) is not None:
        raise ResearchError('review_assignment_replaced', 'Continue the replacement of this native turn')
    binding = prior.get(KEY, {})
    if (prior['role'] != 'native_math' or binding.get('protocol') != PROTOCOL
            or binding.get('adapter') != 'native_math'
            or prior['context']['native_packet'] != value['context']['native_packet']
            or prior['dossier_id'] != value['dossier_id'] or prior['route_id'] != value['route_id']
            or any(normalized_text(prior[key]) != normalized_text(value[key])
                   for key in ('reviewer_id', 'author_id'))):
        raise ResearchError('review_source_continuation_mismatch',
                            'The native series changed its packet, reviewer, route or author')
    if any(saved['role'] == 'native_math' and saved['context'].get('prior_assignment_id') == prior_id
           and saved['id'] != value['id'] and not has_ended_without_output(records, saved['id'])
           for saved in records.get('review_assignment', {}).values()):
        raise ResearchError('review_source_continuation_fork', 'Continue the latest native parts turn')
    state = assignment_state(records, artifacts, prior_id)
    if not state['ready']:
        raise ResearchError('review_source_continuation_unverified', 'The native predecessor needs a ready output')
    if binding['kind'] == 'final' and state['output'].get('kind') != 'requests':
        raise ResearchError('review_resampling_forbidden', 'A native final ends its review series')
    return prior


def find_final_assignment(records, artifacts, identifier):
    """Follow live continuations of one attested series, retaining failed-turn replacements."""
    from .review_parts import KEY, find_kind
    from .review_protocol import assignment_state, find_replacement, has_ended_without_output
    from .workspace import strict_json

    assignments = records.get('review_assignment', {})
    current = assignments[identifier]
    seen = set()
    while current['id'] not in seen:
        seen.add(current['id'])
        live = [saved for saved in assignments.values()
                if saved['role'] == 'native_math'
                and saved['context'].get('prior_assignment_id') == current['id']
                and not (has_ended_without_output(records, saved['id']) and find_replacement(records, saved))]
        if len(live) > 1:
            raise ResearchError('review_source_continuation_fork', 'The native series has multiple live continuations',
                                dict(assignment_ids=sorted(saved['id'] for saved in live)))
        if not live:
            packet = strict_json(artifacts.read(current['packet']))
            state = assignment_state(records, artifacts, current['id'])
            if (find_kind(packet) == 'final' and packet[KEY]['series_assignment_id'] == identifier
                    and state['ready'] and state['output'].get('kind') != 'requests'):
                return current
            break
        current = live[0]
    raise ResearchError('review_parts_incomplete', 'The native series needs an observed final',
                        dict(assignment_id=current['id']))


ADAPTER = dict(applies=applies, enumerate_units=list_units, targets=list_targets,
               series_key=compute_series_key, citations=list_citations, validate_final=validate_final,
               replacement_key=compute_replacement_key, final_prompt=prompts.FINAL_NATIVE,
               source_requests=False)
