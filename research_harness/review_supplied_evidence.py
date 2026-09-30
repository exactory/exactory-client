"""Locate correction evidence within bytes actually supplied to prior reviewers."""

import json

from .errors import ResearchError
from .source_links import contains, validate_link

PROTOCOL = 'exactory-focused-review-evidence-v2'
PROMPT = (
    ' Native artifact references in assessments, objections and corrections are descriptive templates. '
    'For an artifact_descriptor, location is the native artifact path and sha256, size and media_type '
    'retain their native meanings; artifact_reference_template names the original artifact field. '
    'A template does not supply an entire original. Assess only original_evidence and the exact '
    'supplied_source_mappings excerpts. A mapped passage permits only its recorded locator bounds. '
    'Do not treat unsupplied portions of an original as reviewed evidence.'
)


def lineage_packets(records, artifacts, assignment_ids):
    pending, seen, result = list(assignment_ids), set(), []
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        assignment = records.get('review_assignment', {}).get(identifier)
        if assignment is None:
            raise ResearchError('review_record_missing', 'The exact evidence-supplying assignment is missing')
        packet = json.loads(artifacts.read(assignment['packet']))
        result.append(packet)
        parent = assignment.get('history_assignment_id') or assignment.get('context', {}).get('prior_assignment_id')
        repair = assignment.get('context', {}).get('context_repair')
        if parent is None and isinstance(repair, dict):
            parent = repair.get('assignment_id')
        if parent is None and assignment['role'] == 'slate' and assignment.get('session_id') != identifier:
            parent = assignment.get('session_id')
        if parent is not None:
            pending.append(parent)
    return result


def supplied_context(records, artifacts, assignment_ids):
    references, mappings = {}, []
    for packet in lineage_packets(records, artifacts, assignment_ids):
        for entry in packet.get('evidence', []):
            reference = entry['artifact']
            artifacts.read(reference)
            references[reference['sha256']] = reference
        for entry in packet.get('prior_context', []):
            if 'origin' not in entry or 'excerpt' not in entry:
                continue
            artifacts.read(entry['excerpt'])
            mappings.append(entry)
    return references, mappings


def _mapped_link(artifacts, mapping):
    origin = mapping['origin']
    descriptor = origin['artifact_descriptor']
    artifact = {'path': descriptor['location'], **{key: descriptor[key] for key in ('sha256', 'size', 'media_type')}}
    locator = origin['locator']
    if locator['kind'] in ('text', 'span'):
        locator = {'kind': 'span', **{key: locator[key] for key in ('start', 'end', 'sha256')}}
    elif locator['kind'] == 'json':
        content = artifacts.read(mapping['excerpt']).decode('utf-8')
        value = json.loads(content) if mapping['excerpt']['media_type'] == 'application/json' else content
        locator = {'kind': 'json', 'pointer': locator['pointer'], 'value': value}
    else:
        raise ResearchError('invalid_review_correction', 'The saved passage mapping has an unsupported locator')
    return {'version_id': mapping['version_id'], 'source_id': origin['source_id'], 'artifact': artifact, 'locator': locator}


def validate_correction(records, artifacts, evidence, references, mappings):
    """A raw original hash does not widen permission granted to an exact excerpt."""
    count = 0

    def walk(value):
        nonlocal count
        if isinstance(value, dict):
            if {'version_id', 'source_id', 'artifact', 'locator'} <= value.keys():
                validate_link(records, artifacts, value)
                supplied = references.get(value['artifact']['sha256'])
                if supplied != value['artifact']:
                    allowed = False
                    for mapping in mappings:
                        outer = _mapped_link(artifacts, mapping)
                        validate_link(records, artifacts, outer)
                        if contains(outer, value, records):
                            allowed = True
                            break
                    if not allowed:
                        raise ResearchError('invalid_review_correction', 'A correction must stay inside an exact source passage actually supplied earlier')
                count += 1
            elif {'path', 'sha256', 'size', 'media_type'} <= value.keys():
                if references.get(value['sha256']) != value:
                    raise ResearchError('invalid_review_correction', 'An unsupplied original requires a permitted exact source locator')
                artifacts.read(value)
                count += 1
            else:
                for item in value.values():
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(evidence)
    if not count:
        raise ResearchError('invalid_review_correction', 'Correction evidence must have been supplied in the original scientific history')
