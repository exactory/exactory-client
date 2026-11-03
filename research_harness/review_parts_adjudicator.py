"""Focused problem adjudications over exact originals delivered in fresh parts."""

import copy

from . import review_parts_problem_result as problem_result
from . import review_parts_prompts as prompts
from .evidence import digest


def applies(value, packet):
    return (value.get('role') == 'adjudicator'
            and packet.get('subject', {}).get('kind') == 'problem'
            and 'context_repair' not in packet)


def list_units(packet):
    """Keep the problem ownership and add original, version and current sources."""
    from .review_protocol import collect_references

    units = problem_result.list_units(dict(packet, material_objections=[], historical_reviews=[]))
    evidence = {entry['artifact']['sha256']: entry for entry in packet.get('evidence', [])}
    # Focused packets attach search captures and reading originals as current
    # evidence too; the result adapter's elidable-provenance ownership is narrower.
    for unit in units:
        references = sorted(collect_references(unit['item']), key=digest)
        unit['bodies'] = [evidence[reference['sha256']] for reference in references
                          if reference['sha256'] in evidence]
    collections = [(packet, key, [key]) for key in ('original_evidence', 'version_evidence')]
    manifest = packet.get('current_source_delivery', {})
    collections.append((manifest, 'sources', ['current_source_delivery', 'sources']))
    for parent, key, path in collections:
        for index, item in enumerate(parent.get(key, [])):
            references = sorted(collect_references(item), key=digest)
            units.append(dict(item=item, occurrences=[dict(path=path, index=index)],
                bodies=[evidence[reference['sha256']] for reference in references
                        if reference['sha256'] in evidence]))
    return units


def list_citations(source):
    """Offer the exact record, passage and complete-body reference forms."""
    citations = problem_result.list_citations(source)
    item = source['item']
    path = source['occurrences'][0]['path']
    if path == ['problem_evidence', 'openness_searches']:
        citations = [dict(kind='record', record_kind='literature_search', id=item['search']['id'],
                          digest=digest(item['search']))]
        citations.extend(dict(kind='record', record_kind='source', id=row['id'], digest=digest(row))
                         for row in item.get('sources', []))
    elif path in (['problem_evidence', 'readings'], ['problem_evidence', 'formal_checks']):
        record_kind = 'reading' if path[-1] == 'readings' else 'problem_formal_check'
        citations = [citation for citation in citations
                     if not (citation.get('kind') == 'record' and citation.get('record_kind') == record_kind
                             and citation.get('id') == item['id'])]
        citations.append(dict(kind='record', record_kind=record_kind, id=item['id'], digest=item['digest']))
    citations.extend(copy.deepcopy(body['artifact']) for body in source['bodies'])
    by_digest = {digest(citation): citation for citation in citations}
    return [by_digest[key] for key in sorted(by_digest)]


def list_targets(packet):
    evidence = packet.get('problem_evidence', {})
    claim = evidence.get('claim') or {}
    targets = [dict(id='objection', label=packet['objection'].get('claim', packet['objection']['id'])),
               dict(id='statement', label=claim.get('statement', '')),
               dict(id='resolution_criteria', label=claim.get('resolution_criteria', ''))]
    value = evidence.get('value') or {}
    targets.extend(dict(id='value:' + section + ':' + str(index), label=paragraph['text'])
                   for section in ('motivation', 'current_state')
                   for index, paragraph in enumerate(value.get(section, [])))
    if 'rubric_definition' in packet:
        definition = packet['rubric_definition']
        targets.append(dict(id='rubric:' + definition['item'], label=definition['definition']))
    return targets


def compute_series_key(packet):
    return dict(evidence=packet.get('problem_evidence_digest'),
                material_version=packet['objection'].get('material_version'),
                current_sources=digest(packet.get('current_source_delivery')))


def validate_final(records, artifacts, assignment, output, *, attempt=None):
    from .review_protocol import _read_json, validate_adjudication_contract
    return validate_adjudication_contract(records, artifacts, assignment,
        _read_json(artifacts, assignment['packet']), output, attempt=attempt)


ADAPTER = dict(applies=applies, enumerate_units=list_units, targets=list_targets,
               series_key=compute_series_key, citations=list_citations,
               validate_final=validate_final, covers_items=problem_result.covers_items,
               final_prompt=prompts.FINAL_ADJUDICATOR, source_requests=False)
