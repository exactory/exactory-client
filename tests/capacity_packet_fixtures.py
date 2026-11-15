"""Executable upstream-input factories for the shipped capacity properties.

Factories return the production builder's value. They never edit that value.
The guard independently traces the declared builder and checks measured paths.
"""
import copy
import json
import tempfile
from pathlib import Path

from research_harness import review_protocol, review_parts, review_staged, review_staged_protocol
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest

ARTIFACT_FACTORY_QUANTITY_MESSAGE = 'The artifact factory scales text bytes only'
STAGED_FACTORY_QUANTITY_MESSAGE = 'The staged original factory scales text bytes only'
STAGED_SEGMENT_MESSAGE = 'The canonical staged segment exceeded its actual route'
RECORD_OCCURRENCES_MESSAGE = 'The canonical record unit did not keep both actual occurrences'
SOURCE_RECORD_BODY_MESSAGE = 'The canonical source record lost its actual captured body'


def build_scaled_artifact_bodies(quantity, size):
    if quantity != 'texts':
        raise ValueError(ARTIFACT_FACTORY_QUANTITY_MESSAGE)
    with tempfile.TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))
        reference = artifacts.put(('x' * size).encode('utf-8'), 'text/plain')
        # This is an upstream held artifact, not an assembled reviewer packet.
        records = {'source': {'capture': {'id': 'capture', 'text': reference}}}
        return review_protocol._evidence(artifacts, [reference], records=records)


def _build_staged_originals(role, quantity, size):
    if quantity != 'texts':
        raise ValueError(STAGED_FACTORY_QUANTITY_MESSAGE)
    from test_review_staged_fixtures import epoch, profile
    from research_harness.review_route_capacity import measure_request
    from ci.capacity_packet_inventory import read_packet_registry, read_quantity_allowance
    from research_harness import review_transport
    registry = read_packet_registry()
    route = registry['quantity_routes']['stage1_text_route']
    allowance = read_quantity_allowance(registry, 'stage1_text_route')
    manifest = review_staged.build_manifest({'statement': 'A finite claim.'},
        [dict(key='readings', index=0, item={'text': 'x' * size}, artifacts=[], source_ids=[])], [])
    focused = {'focused_scope': dict(review_pair_roots=['a' * 64, 'b' * 64], objection_id='objection', objection_version='v1')} if role == 'adjudicator' else {}
    frozen = epoch(role=role, manifest=manifest, **focused)
    packets = []
    for source in manifest['sources']:
        for start, end in source['required_intervals']:
            while start < end:
                stop = min(end, start + allowance // 2)
                inputs = [review_staged.source_span(source, start, stop)]
                measurement = review_staged.joint_fit([stop - start], allowance, 10000,
                    profile(), claim_count=len(manifest['claims']))
                assigned = review_staged.make_task(frozen, 'read', inputs=inputs,
                    claim_ids=[row['id'] for row in manifest['claims']],
                    occurrence_ids=[row['id'] for row in manifest['occurrences']],
                    scope='original_segment', measurement=measurement)
                assigned = review_staged.freeze_task(assigned, digest(inputs), review_staged.SERIALIZATION)
                packet = review_staged_protocol.build_packet(frozen, assigned)
                request = review_transport.request_body(route, 'Return JSON.', packet)
                if measure_request(route, request)['text_characters'] > allowance:
                    raise AssertionError(STAGED_SEGMENT_MESSAGE)
                packets.append(packet)
                start = stop
    return packets


def build_scaled_staged_originals(quantity, size):
    return _build_staged_originals('result', quantity, size)


def build_scaled_staged_adjudicator_originals(quantity, size):
    return _build_staged_originals('adjudicator', quantity, size)


def build_scaled_record_units(quantity, size):
    """Build exact search/source records before the canonical result builder."""
    from review_parts_fixtures import PartsResultCase
    case = PartsResultCase()
    case.setUp()
    try:
        fixture = case.case
        count = size if quantity in ('units', 'searches', 'sources', 'identifiers') else 1
        text = 'x' * size if quantity == 'texts' else 'A captured finite result.'
        long_id = ''
        body = json.dumps({'results': [{'text': text}]}).encode('utf-8')
        artifact = fixture.artifacts.put(body, 'application/json')
        searches, sources, wrappers, accounts = {}, {}, [], []
        for index in range(count):
            search_id = 'search-' + str(index) + long_id
            source_id = 'capture-' + str(index) + long_id
            source = dict(id=source_id, response=artifact, kind='search_response')
            search = dict(id=search_id, queries=[text], reason=text,
                          responses=[dict(source_id=source_id)])
            searches[search_id] = search
            sources[source_id] = source
            wrappers.append(dict(search=search, sources=[source], search_digest=digest(search),
                                 formulation='One fixed scientific formulation.'))
            for kind, record in (('literature_search', search), ('source', source)):
                accounts.append(dict(record=record, cite=dict(kind='record', record_kind=kind,
                    id=record['id'], digest=digest(record))))
        fixture.expected['openness_searches'] = wrappers
        fixture.expected['account_records'] = accounts
        fixture.expected['value'] = dict(accounts=dict(search_coverage=[dict(
            id='coverage', search_ids=list(searches))]))
        records = dict(fixture.read_records())
        records['literature_search'] = dict(records.get('literature_search', {}), **searches)
        records['source'] = dict(records.get('source', {}), **sources)
        packet = review_protocol.build_packet(records, fixture.artifacts, None, 'result', 'first')
        # Current Q6 construction must consume these upstream records, deduplicate
        # the search across the account and openness occurrences, and own its body.
        inventory = review_parts.build_inventory(packet, 'problem_result')
        units = review_parts.enumerate_units(packet, 'problem_result', inventory)
        search_units = [unit for unit in units if unit.get('record_identity', {}).get('record_kind') == 'literature_search']
        if len(search_units) != count or any(len(unit['occurrences']) < 2 for unit in search_units):
            raise AssertionError(RECORD_OCCURRENCES_MESSAGE)
        if not any(unit.get('bodies') for unit in units):
            raise AssertionError(SOURCE_RECORD_BODY_MESSAGE)
        return packet
    finally:
        case.doCleanups()


FACTORIES = {
    'artifact_body_text': build_scaled_artifact_bodies,
    'staged_original_text': build_scaled_staged_originals,
    'staged_adjudicator_original_text': build_scaled_staged_adjudicator_originals,
    'canonical_record_units': build_scaled_record_units,
}
