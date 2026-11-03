"""Captured availability evidence built through the public acquisition APIs."""

import json

from research_harness.acquisition import acquire_work, import_response
from research_fixtures import client, crossref, json_response, openalex


def registry_absence(case, *, with_doi=True, partial=False, padding=0):
    observed = openalex()
    del observed['abstract_inverted_index']
    if not with_doi:
        del observed['doi'], observed['ids']['doi']
    observed['fixture_padding'] = 'Registry response evidence. ' * padding
    response, _, _ = client([json_response(observed)])
    case.sequence += 1
    result = acquire_work(case.store, 'W123', http=response,
        expected_revision=case.store.revision, request_id='absence-oa-' + str(case.sequence))
    sources = result['source_ids']
    if with_doi:
        observed = crossref()
        del observed['message']['abstract']
        observed['message']['fixture_padding'] = 'Crossref response evidence. ' * padding
        response, _, _ = client([json_response(observed)])
        case.sequence += 1
        result = acquire_work(case.store, '10.1234/example', http=response,
            expected_revision=case.store.revision, request_id='absence-cr-' + str(case.sequence))
        sources += result['source_ids']
    if partial:
        raw = {'id': 'openalex:W123', 'title': 'Authored registry work',
               'abstract': 'A partial publisher abstract snippet.'}
        case.sequence += 1
        import_response(case.store, 'web', json.dumps(raw).encode(),
            source_url='https://example.org/article', captured_at='2026-09-07T12:00:00Z',
            media_type='application/json', mappings=[{'id': '/id', 'title': '/title', 'abstract': '/abstract'}],
            expected_revision=case.store.revision, request_id='absence-partial-' + str(case.sequence))
    return {'id': 'registry-absent', 'profile': 'research', 'version_id': 'openalex:W123',
            'depth': 'abstract', 'source_ids': sources, 'reason': 'The captured registries carry no complete abstract.',
            'policy': {'id': 'registry-abstract-absent', 'minimum_attempts': len(sources),
                       'allowed_statuses': [200], 'rationale': 'Inspect every registry addressing these identifiers.'}}


def terminal_absence(case, version):
    failure = case.capture(version, abstract=False, status=404)
    return {'id': 'terminal-absent', 'profile': 'research', 'version_id': version, 'depth': 'fulltext',
            'source_ids': [failure['source_id']], 'reason': 'The captured origin returns HTTP 404.',
            'policy': {'id': 'terminal-origin', 'minimum_attempts': 1, 'allowed_statuses': [403, 404, 410, 451],
                       'rationale': 'A complete terminal origin response documents the retrieval failure.'}}
