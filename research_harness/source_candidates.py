"""Discover related abstract donors without equating family aliases with versions.

The existing same_work assertions do not establish an exact version equivalence.
OpenAlex can merge preprint and journal locations under one work and choose one
DOI. Even reparsing that work's DOI field cannot bind its abstract to that DOI's
version. Cross-identity donors therefore remain alternatives. Exact requested
identity captures continue through the ordinary source-delivery path.
"""

from .errors import ResearchError
from .identities import family_id
from .source_links import captured_source


def describe_source(source):
    """Describe provenance without attaching an alternative's bytes as evidence."""
    reference = source['response']
    return {'source_id': source['id'], 'provider': source['provider'],
            'captured_at': source.get('captured_at'),
            'capture_method': source.get('capture_method'),
            'origin_verified': source.get('origin_verified'),
            'original_descriptor': {'sha256': reference['sha256'], 'size': reference['size'],
                                    'media_type': reference['media_type'], 'location': reference['path']}}


def describe_abstract(abstract):
    reference = abstract['artifact']
    return {'source_id': abstract['source_id'], 'assertion_id': abstract['assertion_id'],
            'version': abstract['version'], 'representation': abstract['representation'],
            'completeness': abstract['completeness'],
            'artifact_descriptor': {'sha256': reference['sha256'], 'size': reference['size'],
                                    'media_type': reference['media_type'], 'location': reference['path']}}


def read_candidate_provenance(records, artifacts, source_id):
    try:
        return describe_source(captured_source(records, artifacts, source_id))
    except ResearchError as error:
        if error.code not in ('artifact_missing', 'artifact_corrupt', 'source_pending', 'unknown_source'):
            raise
        return {'source_id': source_id, 'validation_error': {'code': error.code, 'message': error.message}}


def read_abstract_candidate(records, artifacts, abstract):
    result = dict(describe_abstract(abstract), reusable=False,
                  provenance=read_candidate_provenance(records, artifacts, abstract['source_id']))
    try:
        artifacts.read(abstract['artifact'])
    except ResearchError as error:
        if error.code not in ('artifact_missing', 'artifact_corrupt'):
            raise
        result['validation_error'] = {'code': error.code, 'message': error.message}
    return result


def collect_identity_edges(records):
    edges = []
    for identifier, alias in sorted(records.get('alias', {}).items()):
        for assertion in alias['assertions']:
            if assertion['relation'] == 'same_work':
                edges.append(dict(assertion, identifier=identifier))
    return edges


def collect_related_families(edges, identifier):
    related = {identifier, family_id(identifier)}
    while True:
        expanded = set(related)
        for edge in edges:
            if edge['identifier'] in related or edge['work_id'] in related:
                expanded.update((edge['identifier'], edge['work_id']))
        if expanded == related:
            return related
        related = expanded


def describe_identity_ambiguities(edges):
    assertions = {}
    doi_aliases = {}
    for edge in edges:
        if edge['work_id'] != edge['identifier']:
            assertions.setdefault(edge['identifier'], set()).add(edge['work_id'])
        if edge['identifier'].startswith('doi:'):
            doi_aliases.setdefault(edge['work_id'], set()).add(edge['identifier'])
    return [{'identifier': identifier, 'families': sorted(families)}
            for identifier, families in sorted(assertions.items()) if len(families) > 1] + [
            {'identifier': identifier, 'doi_identities': sorted(aliases)}
            for identifier, aliases in sorted(doi_aliases.items()) if len(aliases) > 1]


def find_source_candidates(records, artifacts, identifier):
    """Report every related donor, provenance and ambiguity; never select a family."""
    edges = collect_identity_edges(records)
    related = collect_related_families(edges, identifier)
    edges = [edge for edge in edges if edge['identifier'] in related and edge['work_id'] in related]
    ambiguities = describe_identity_ambiguities(edges)
    provenance = []
    for edge in edges:
        source = read_candidate_provenance(records, artifacts, edge['source_id'])
        provenance.append(dict(source, identifier=edge['identifier'],
            work_id=edge['work_id'], locator=edge['locator'], relation=edge['relation']))
    candidates = []
    for donor in sorted(records.get('work', {}).values(), key=lambda work: work['id']):
        if donor['id'] == identifier or donor['work_id'] not in related:
            continue
        abstracts = [read_abstract_candidate(records, artifacts, abstract) for abstract in donor['abstracts']]
        candidates.append({'version_id': donor['id'], 'work_id': donor['work_id'],
            'version': donor['version'], 'identity_status': 'ambiguous' if ambiguities else 'family_only',
            'identity_provenance': provenance, 'abstracts': abstracts, 'reusable': False})
    return {'candidates': candidates, 'ambiguities': ambiguities}
