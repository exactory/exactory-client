"""The problem-result adapter: scientific collections, ownership and currency."""

import copy

from . import review_parts_prompts as prompts, review_problem_parts as legacy

from .evidence import digest
from .problem_rubric import ITEMS, PROBLEM_CHECKS


def applies(value, packet):
    return (value.get('role') == 'result'
            and packet.get('subject', {}).get('kind') == 'problem'
            and 'context_repair' not in packet
            and not value.get('context', {}).get('source_delivery')
            and 'complete_source_delivery' not in packet)


def list_units(packet):
    """Return item occurrences and the bodies the existing evidence builder owns."""
    from .review_protocol import collect_references

    evidence = {entry['artifact']['sha256']: entry for entry in packet.get('evidence', [])}
    readings = {item['id']: digest(item)
                for item in packet.get('problem_evidence', {}).get('readings', [])}
    units = []
    for key, index, item in legacy._ordered_items(packet.get('problem_evidence', {})):
        references = (list(collect_references(item)) if key == 'passages'
                      else legacy._owned_artifacts(key, item))
        unit = dict(item=item, occurrences=[dict(path=['problem_evidence', key], index=index)],
                    bodies=[evidence[reference['sha256']] for reference in references
                            if reference['sha256'] in evidence])
        if key == 'passages' and item.get('reading_id') in readings:
            unit['carded_under'] = readings[item['reading_id']]
        units.append(unit)
    objections = [(objection, ['material_objections', index, 'version_evidence'])
                  for index, objection in enumerate(packet.get('material_objections', []))]
    objections.extend(list_historical_objections(packet))
    for objection, path in objections:
        for index, item in enumerate(objection.get('version_evidence', [])):
            units.append(dict(item=item, occurrences=[dict(path=path, index=index)],
                              bodies=[evidence[reference['sha256']]
                                      for reference in collect_references(item)
                                      if reference['sha256'] in evidence]))
    return units


def list_historical_objections(packet):
    for review_index, entry in enumerate(packet.get('historical_reviews', [])):
        path = ['historical_reviews', review_index]
        if 'review' in entry:
            entry = entry['review']
            path.append('review')
        if 'payload' in entry:
            entry = entry['payload']
            path.append('payload')
        for index, objection in enumerate(entry.get('objections', [])):
            yield objection, path + ['objections', index, 'version_evidence']


def list_citations(source):
    """Copy the reference forms accepted by the ordinary result validator."""
    item = source['item']
    citations = []
    pending = [item]
    while pending:
        value = pending.pop(0)
        if not isinstance(value, dict):
            continue
        if value.get('kind') == 'record' and {'record_kind', 'id'} <= value.keys():
            citations.append({key: copy.deepcopy(value[key]) for key in
                              ('kind', 'record_kind', 'id', 'digest') if key in value})
            continue
        if value.get('kind') == 'source' and 'link' in value:
            citations.append(copy.deepcopy(value))
            continue
        if {'path', 'sha256', 'size', 'media_type'} <= value.keys():
            citations.append({key: value[key] for key in ('path', 'sha256', 'size', 'media_type')})
            continue
        for name in ('cite', 'citation', 'record_reference'):
            if isinstance(value.get(name), dict):
                pending.append(value[name])
        pending.extend(value.get('record_references', []))
        if {'version_id', 'source_id', 'artifact', 'locator'} <= value.keys():
            citations.append(dict(kind='source', link=copy.deepcopy(value)))
    if not citations and 'search' in item:
        citations.append(dict(kind='record', record_kind='literature_search', id=item['search']['id']))
    if not citations and source['occurrences'][0]['path'] == ['problem_evidence', 'formal_checks']:
        citations.append(dict(kind='record', record_kind='problem_formal_check', id=item['id']))
    return list({digest(citation): citation for citation in citations}.values())


def list_targets(packet):
    labels = dict(line.split(': ', 1) for line in prompts.TARGET_LABELS.splitlines() if ': ' in line)
    targets = [dict(id=identifier, label=labels.get(identifier, prompts.TARGET_LABELS))
               for identifier in PROBLEM_CHECKS + ITEMS]
    objections = list(packet.get('material_objections', []))
    objections.extend(objection for objection, path in list_historical_objections(packet))
    seen = {row['id'] for row in targets}
    for objection in objections:
        if objection['id'] not in seen:
            targets.append(dict(id=objection['id'],
                                label=objection.get('claim', objection['id'])[:200]))
            seen.add(objection['id'])
    return targets


def compute_series_key(packet):
    return dict(evidence=packet.get('problem_evidence_digest'),
                bars=digest(packet.get('bars', [])))


def validate_final(records, artifacts, assignment, output, *, attempt=None):
    from .research_decisions import validate_review_contract
    return validate_review_contract(records, artifacts, assignment, output, attempt=attempt)


def covers_items(packets, expected, key):
    from .review_parts import covers_items as covers_collection
    return covers_collection(packets, expected, key)


ADAPTER = dict(applies=applies, enumerate_units=list_units, targets=list_targets,
               series_key=compute_series_key, citations=list_citations, validate_final=validate_final,
               covers_items=covers_items, final_prompt=prompts.FINAL_RESULT,
               source_requests=True)
