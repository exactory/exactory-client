"""Prospective reading work for the source links of an unrecorded review."""

import copy

from .components import is_component
from .errors import ResearchError
from .graph import selected_bundle
from .reading import list_reading_targets, required_unit_obligations, validate_fulltext_link, validate_read_evidence
from .review_refusal import evidence_arrays
from .source_links import complete_original, contains


READING_HANDOFF_MESSAGE = (
    'Read each named source in full, write the inspection notes and the seven notes from the text, '
    'record the reading with read, then run review-series again for this head.'
)


def list_source_citations(output):
    """Retain every citation path, including reassessed adjudication findings."""
    return [dict(path=path + '/' + str(index), **copy.deepcopy(reference['link']))
            for path, values in evidence_arrays(output, with_paths=True)
            for index, reference in enumerate(values) if reference.get('kind') == 'source']


def describe_reading_capture(records, artifacts, capture, links):
    """Choose a bundle that can contain every missing citation, or name its repair."""
    version = links[0]['version_id']
    candidates = [selected_bundle(records, version, target)
                  for target in list_reading_targets(records, links[0], capture)]
    current = selected_bundle(records, version)
    if current is not None and current in candidates:
        candidates.insert(0, current)
    bundles = [bundle for bundle in candidates if bundle is not None]
    repairs = list(bundles)
    parent = capture['component']['spec']['parent_original_sha256'] if is_component(capture) else None
    if current is not None and current not in bundles and current['original_sha256'] == parent:
        repairs.insert(0, current)
    pending = {}
    for bundle in repairs:
        try:
            pending[bundle['id']] = required_unit_obligations(records, artifacts, bundle)
        except ResearchError as error:
            pending[bundle['id']] = [error.as_dict()]
    eligible = [bundle for bundle in bundles if bundle['scope'] == 'article'
                and bundle['completeness'] == 'complete'
                and not pending[bundle['id']]
                and all(any(unit['link'] is not None and contains(unit['link'], link, records)
                            for unit in bundle['units']) for link in links)]
    bundle = eligible[0] if eligible else repairs[0] if repairs else None
    source = records['source'][capture['source_id']]
    prerequisites = []
    if not complete_original(dict(source=source, capture=capture)):
        prerequisites.append(dict(command='fulltext', payload=dict(version_id=version),
                                  alternatives=['import-fulltext']))
    if not eligible:
        source_id = (bundle['source_id'] if bundle else capture['component']['spec']['parent_source_id']
                     if is_component(capture) else capture['source_id'])
        prerequisites.append(dict(command='bundle', payload=dict(version_id=version, source_id=source_id)))
    return dict(source_id=capture['source_id'], original=capture['original'],
                bundle_id=bundle['id'] if eligible else None,
                required_units=copy.deepcopy([unit for unit in bundle['units'] if unit['required']]) if bundle else [],
                pending=pending[bundle['id']] if bundle else [],
                prerequisites=prerequisites)


def collect_missing_readings(records, artifacts, output, *, packets=()):
    """Group unsatisfied full-text links by version without treating other links as reading work."""
    missing, captures = {}, {}
    for citation in list_source_citations(output):
        from .review_delivered_evidence import resolve_part_source_link
        link = resolve_part_source_link({key: value for key, value in citation.items() if key != 'path'}, packets)
        try:
            capture = validate_fulltext_link(records, link)
        except ResearchError as error:
            if error.code != 'source_link_not_fulltext':
                raise
            continue
        try:
            validate_read_evidence(records, artifacts, link)
        except ResearchError as error:
            if error.code not in ('reading_missing', 'source_pending'):
                raise
        else:
            continue
        version = link['version_id']
        row = missing.setdefault(version, dict(version_id=version, citations=[], captures=[]))
        row['citations'].append(citation)
        captures.setdefault((version, capture['source_id']), (capture, []))[1].append(link)
    for (version, source_id), (capture, links) in captures.items():
        missing[version]['captures'].append(describe_reading_capture(records, artifacts, capture, links))
    return list(missing.values())


def plan_readings(records, artifacts, assignment, output):
    """Let the recording refuse a malformed source link before asking for any reading."""
    from .review_delivered_evidence import read_chain_delivery, resolve_part_source_link
    from .review_context_repair import _exposure_lineage
    packets = (read_chain_delivery(records, artifacts, assignment)['packets']
               if any('output_refusal' in row.get('context', {}) for row in _exposure_lineage(records, assignment)) else ())
    for citation in list_source_citations(output):
        try:
            validate_fulltext_link(records, resolve_part_source_link(citation, packets))
        except ResearchError as error:
            if error.code == 'source_link_not_fulltext':
                return None
            raise
    rows = collect_missing_readings(records, artifacts, output, packets=packets)
    if not rows:
        return None
    return dict(state='handoff', code='reading_missing', command='read',
                details=dict(assignment_id=assignment['id'], message=READING_HANDOFF_MESSAGE, readings=rows,
                             then=dict(command='review-series', assignment_id=assignment['id'])))
