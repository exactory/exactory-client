"""Scientific units and completion gates for verification and study readiness."""

import copy

from . import review_parts as parts, review_parts_prompts as prompts
from .errors import ResearchError
from .evidence import digest
from .operations import fields, text

SOURCE_COLLECTIONS = ('work', 'reading', 'source_bundle', 'source')
VERIFICATION_CHECKS = ('soundness', 'novelty', 'impact')


def applies_verification(value, packet):
    return (value.get('role') == 'verification'
            and packet.get('paper', {}).get('kind') == 'verification')


def applies_readiness(value, packet):
    return (value.get('role') == 'result' and 'dossier' in packet
            and packet.get('readiness', {}).get('kind') == 'readiness'
            and 'context_repair' not in packet)


def list_collection_units(packet, path):
    """Keep list indices and keyed-object identities, with existing evidence ownership."""
    from .review_protocol import collect_references

    collection = packet
    for component in path:
        collection = collection[component]
    entries = enumerate(collection) if isinstance(collection, list) else sorted(collection.items())
    evidence = {entry['artifact']['sha256']: entry for entry in packet.get('evidence', [])
                if entry.get('encoding') in ('utf-8', 'base64')}
    for address, item in entries:
        occurrence = dict(path=path, **({'index': address} if isinstance(collection, list)
                                       else {'key': address}))
        references = sorted(collect_references(item), key=digest)
        bodies = [evidence[reference['sha256']] for reference in references
                  if reference['sha256'] in evidence]
        yield dict(item=item, occurrences=[occurrence], bodies=bodies)


def list_verification_units(packet):
    for name in SOURCE_COLLECTIONS:
        if name in packet['paper'].get('sources', {}):
            yield from list_collection_units(packet, ['paper', 'sources', name])


def list_readiness_units(packet):
    inputs = packet['readiness'].get('inputs', {})
    for name, collection in sorted(inputs.items()):
        if name in ('candidate', 'synthesis') or not isinstance(collection, (list, dict)):
            continue
        path = ['readiness', 'inputs', name]
        if name == 'sources':
            for kind, records in sorted(collection.items()):
                if isinstance(records, (list, dict)):
                    yield from list_collection_units(packet, path + [kind])
        else:
            yield from list_collection_units(packet, path)
    if isinstance(packet['readiness'].get('execution_observations'), (list, dict)):
        yield from list_collection_units(packet, ['readiness', 'execution_observations'])


def list_deliverable_citations(source):
    """Retain native citations of owned bytes; inspection links become source references."""
    bodies = {body['artifact']['sha256'] for body in source['bodies']}
    candidates = {}
    pending = [source['item']]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(reversed(value))
            continue
        if not isinstance(value, dict):
            continue
        if (value.get('kind') == 'source' and 'link' in value
                or value.get('kind') == 'result'
                and {'execution_id', 'output_id', 'artifact', 'locator'} <= value.keys()
                or value.get('kind') == 'record' and {'record_kind', 'id'} <= value.keys()
                or {'path', 'sha256', 'size', 'media_type'} <= value.keys()):
            candidates[digest(value)] = copy.deepcopy(value)
        elif {'version_id', 'source_id', 'artifact', 'locator'} <= value.keys():
            reference = dict(kind='source', link=copy.deepcopy(value))
            candidates[digest(reference)] = reference
        else:
            pending.extend(reversed(list(value.values())))
    citations = []
    for name in sorted(candidates):
        reference = candidates[name]
        if reference.get('kind') == 'record':
            continue
        artifact = (reference['link']['artifact'] if reference.get('kind') == 'source'
                    else reference['artifact'] if reference.get('kind') == 'result'
                    else reference)
        if artifact['sha256'] in bodies:
            citations.append(reference)
    return citations


def list_verification_citations(source):
    return [reference['link'] for reference in list_deliverable_citations(source)
            if reference.get('kind') == 'source']


def list_readiness_citations(source):
    return list_deliverable_citations(source)


def list_verification_targets(packet):
    return [dict(id=name, label=name) for name in VERIFICATION_CHECKS]


def list_readiness_targets(packet):
    from .development import _REVIEW_CHECKS
    labels = dict(line.split(': ', 1) for line in prompts.TARGET_LABELS.splitlines()
                  if ': ' in line)
    return [dict(id=name, label=labels.get(name, prompts.TARGET_LABELS))
            for name in _REVIEW_CHECKS]


def compute_frame_key(packet, adapter, excluded=()):
    units = parts.enumerate_units(packet, adapter)
    frame = parts.build_frame(packet, units)
    for key in excluded:
        frame.pop(key, None)
    return digest(frame)


def compute_verification_key(packet):
    return compute_frame_key(packet, 'study_verification')


def compute_readiness_key(packet):
    return compute_frame_key(packet, 'study_readiness', (
        'continuation', 'source_deliveries', 'pending_source_requests',
        'prior_context', 'reviewer_excerpts', 'evidence'))


def require_complete_series(records, artifacts, assignment_id):
    """Return authenticated series evidence only for its completed scientific final."""
    assignment = records.get('review_assignment', {}).get(assignment_id, {})
    if parts.KEY not in assignment:
        return None
    result = parts.verify_series(records, artifacts, assignment_id)
    if not result['complete']:
        from .review_continuation import describe_next_actions
        raise ResearchError(
            'review_parts_pending', 'Complete the review series before crediting its final',
            dict(assignment_id=assignment_id, issues=result['issues'],
                 next=describe_next_actions(records, artifacts, assignment_id)))
    return result


def list_bearing_references(series, references):
    """Credit exact inventory citations of units assessed against readiness targets."""
    from .development import _REVIEW_CHECKS

    candidates = {digest(reference): reference for reference in references}
    credited = set()
    final = series['packets'][-1]
    targets = {target['id'] for target in final[parts.KEY]['targets']} & set(_REVIEW_CHECKS)
    for card in final[parts.STATE_KEY]['cards']:
        if not any(bearing['target'] in targets for bearing in card['bearing']):
            continue
        citations = {digest(reference) for reference in card['citations']}
        credited.update(citations & candidates.keys())
    return [candidates[name] for name in sorted(credited)]


def validate_study_references(records, artifacts, references, delivery):
    """Check native support locators and exact bytes in an authenticated delivery."""
    from .research_decisions import find_review_undelivered_references
    from .review_refusal import validate_evidence_contract
    from .strategy import items

    citations = []
    for reference in items(references, 'Readiness evidence', nonempty=True):
        if isinstance(reference, dict) and reference.get('kind') == 'result':
            fields(reference, ('kind', 'execution_id', 'output_id', 'artifact', 'locator'),
                   code='invalid_value_review')
            citations.append(reference['artifact'])
        else:
            citations.append(reference)
    validate_evidence_contract(records, artifacts, citations, packets=delivery['packets'])
    missing = find_review_undelivered_references(
        records, artifacts, citations, delivery, require_complete_artifacts=True)
    if missing:
        raise ResearchError('review_evidence_undelivered',
                            'Cite only evidence delivered by the study review series',
                            dict(references=missing))


def validate_series_evidence(records, artifacts, assignment_id, candidate_references,
                             assessed_references):
    """Credit bearing references only after checking completion and actual evidence."""
    series = require_complete_series(records, artifacts, assignment_id)
    if series is None:
        return []
    credited = list_bearing_references(series, candidate_references)
    validate_study_references(records, artifacts, assessed_references + credited,
                              series['delivery'])
    return credited


def validate_verification_final(records, artifacts, assignment, output, *, attempt=None):
    """Check the ordinary verdict's three source-linked checks without selecting this turn."""
    from .research_decisions import (find_review_undelivered_references,
                                     read_review_citation_delivery)
    from .review_refusal import validate_evidence_contract
    from .verification import validate_verdict_body
    from .workspace import strict_json

    fields(output, ('verdict', 'checks'), code='invalid_value_review')
    validate_verdict_body(output['verdict'])
    if not isinstance(output['checks'], list):
        raise ResearchError('invalid_value_review', 'Verification checks are an array')
    packets = parts.collect_contract_packets(records, artifacts, assignment)
    delivery = read_review_citation_delivery(records, artifacts, assignment, attempt=attempt)
    target = strict_json(artifacts.read(assignment['packet']))['paper']['target']
    seen = set()
    for check in output['checks']:
        fields(check, ('dimension', 'reason', 'evidence'), code='invalid_value_review')
        if check['dimension'] not in VERIFICATION_CHECKS or check['dimension'] in seen:
            raise ResearchError('invalid_value_review',
                                'Assess each verification dimension exactly once')
        seen.add(check['dimension'])
        text(check['reason'], 'Verification reasoning', code='invalid_value_review')
        if not isinstance(check['evidence'], list):
            raise ResearchError('invalid_value_review',
                                'Verification evidence is an array of source links')
        references = [dict(kind='source', link=link) for link in check['evidence']]
        validate_evidence_contract(records, artifacts, references, packets=packets)
        if find_review_undelivered_references(records, artifacts, references, delivery,
                                            require_complete_artifacts=True):
            raise ResearchError('review_evidence_undelivered',
                                'Cite only source content delivered by this series')
        if check['dimension'] == 'soundness' and not any(link['version_id'] == target['id']
                                                       for link in check['evidence']):
            raise ResearchError('invalid_value_review',
                                'Soundness evidence includes the exact target')
    if seen != set(VERIFICATION_CHECKS):
        raise ResearchError('invalid_value_review',
                            'Assess soundness, novelty and impact exactly once each')


def validate_readiness_final(records, artifacts, assignment, output, *, attempt=None):
    from .research_decisions import validate_review_contract
    return validate_review_contract(records, artifacts, assignment, output, attempt=attempt)


VERIFICATION_ADAPTER = dict(
    applies=applies_verification, enumerate_units=list_verification_units,
    targets=list_verification_targets, series_key=compute_verification_key,
    citations=list_verification_citations, validate_final=validate_verification_final,
    final_prompt=prompts.FINAL_VERIFICATION, source_requests=False)

READINESS_ADAPTER = dict(
    applies=applies_readiness, enumerate_units=list_readiness_units,
    targets=list_readiness_targets, series_key=compute_readiness_key,
    citations=list_readiness_citations, validate_final=validate_readiness_final,
    final_prompt=prompts.FINAL_READINESS, source_requests=True)
