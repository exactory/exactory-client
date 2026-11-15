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


def _build_native_quantity_packet(artifacts, statement, context):
    from research_harness import native_review
    subject = dict(statement=statement)
    native = dict(protocol=native_review.PROTOCOL, kind='result', root='synthetic-root',
        objective_id='synthetic-objective', contract=dict(orchestration='problem_creation'),
        subject=subject, subject_digest=digest(subject), claim_digest=digest(subject),
        reviewer=dict(source='host', actor_id='reviewer', attestation_id='review-assignment:quantity'),
        scientific_context=context, evidence=[], execution_provenance=[], unmaterialized_digests=[])
    reference = artifacts.put(json.dumps(native).encode('utf-8'), 'application/json')
    return review_protocol.build_packet({}, artifacts, None, 'native_math', 'reviewer', native_packet=reference)


def build_scaled_stage2_native_records(quantity, size):
    """Scale native ingress records before the actual common packet builder.

    The portable envelope validates the v2 minimum's closed row shapes. Gaps
    and notices come from their production projectors; no emitted packet is
    edited to manufacture quantity coverage.
    """
    from research_harness import openness_minimum
    if quantity not in ('sources', 'gaps', 'notices'):
        raise ValueError('The v2 native factory scales source, gap and notice entries')
    with tempfile.TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))
        statement = 'Every finite synthetic system satisfies the stated bound.'
        searches = {str(index): dict(id='search-' + str(index), queries=[statement],
            found_work_ids=[], dispositions=[], responses=[], coverage=[],
            captured_at='2026-11-01T00:00:00Z') for index in range(size)}
        bindings = [dict(formulation=statement, search_digest=digest(search))
                    for search in searches.values()]
        families = [dict(search_digest=bindings[index]['search_digest'],
            work_id='work-' + str(index), version_id='family-' + str(index))
            for index in range(size)]
        minimum = dict(schema_version=2, statement=statement, neighbours=[], searches=bindings,
            reading_digests=[], settling_work_id=None, duplicate_resolutions=[],
            family_readings=families,
            supporting_reading_digests=[digest(dict(reading=index)) for index in range(size)],
            results=[dict(native_attempt_digest=digest(dict(attempt=index)),
                acceptance_id='acceptance-' + str(index), cited_as='Result ' + str(index),
                link_digest=digest(dict(link=index)), result_digest=digest(dict(result=index)))
                for index in range(size)])
        openness_minimum.validate_minimum(minimum)
        # Missing family bindings are a genuine minimum-gap shape. The quantity
        # input need not provide accepted result bodies to the portable loader.
        gaps = openness_minimum.list_minimum_gaps(dict(minimum, searches=[], results=[]),
                                                  statement, {}, artifacts)
        notices = openness_minimum.list_minimum_notices(dict(minimum, results=[], reading_digests=[]),
                                                        {'literature_search': searches}, artifacts)
        context = dict(scope='objective', openness_minimum=minimum,
            minimum_gaps=gaps, decisive_work_ids=['work-' + str(index) for index in range(size)],
            audit_forecast=dict(gaps=gaps, notices=notices,
                outcomes=openness_minimum.forecast_outcomes(gaps), searched_through='2026-11-01'))
        return _build_native_quantity_packet(artifacts, statement, context)


def build_scaled_v6_reading_records(quantity, size):
    """Build the v6 manifest and exact delivery receipts before assembling a turn."""
    from research_harness import openness_reading as reading, review_search_delivery
    from research_harness.problem_openness import serialize_record
    if quantity not in ('texts', 'searches'):
        raise ValueError('The v6 record factory scales manifest text or search receipts')
    count = size if quantity == 'searches' else 1
    formulation = 'x' * size if quantity == 'texts' else 'A finite synthetic formulation.'
    searches = [dict(id='search-' + str(index), queries=[formulation]) for index in range(count)]
    bindings = [dict(formulation=formulation, search_digest=digest(search), search=search)
                for search in searches]
    planned = review_search_delivery.plan_search_delivery(bindings)
    originals = {digest(search): serialize_record(search) for search in searches}
    bodies = dict(originals, **planned['bodies'])
    delivered = {sha256: dict(content=content, deliveries=[dict(assignment_id='reading-1',
        attempt_id='attempt-1', part=1, parts=1, start=0, end=len(content))])
        for sha256, content in bodies.items()}
    receipts = review_search_delivery.verify_search_deliveries(planned['manifest'], delivered, originals)
    record = reading._create_record(reading.PROTOCOL_V6, planned['manifest'])
    record.update(search_receipts=receipts, planned_reading_turns=count)
    record['allowances']['record'] = dict(characters=max(300000, size), used=0, not_retained={})
    with tempfile.TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))
        minimum = dict(schema_version=2, statement=formulation, neighbours=[],
            searches=[{key: row[key] for key in ('formulation', 'search_digest')} for row in bindings],
            reading_digests=[], settling_work_id=None, duplicate_resolutions=[], family_readings=[],
            supporting_reading_digests=[], results=[])
        packet = _build_native_quantity_packet(artifacts, formulation,
                                               dict(scope='objective', openness_minimum=minimum))
        return reading._assemble_reading_turn(artifacts, packet, record, [], [], [], 1)


def build_scaled_correction_history(quantity, size):
    """Read a scaled immutable history component through its production reader.

    The correction builder copies this reader's historical_reviews subtree
    without projecting it. This factory isolates that frozen input component;
    command-backed Q11 evidence covers correction eligibility and admission.
    """
    from research_harness import problem_rubric, review_context_repair
    if quantity not in ('texts', 'reviews'):
        raise ValueError('The correction history factory scales text or retained reviews')
    count = size if quantity == 'reviews' else 1
    content = 'x' * size if quantity == 'texts' else 'A finite historical scientific finding.'
    with tempfile.TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))
        reference = artifacts.put(b'A finite supplied source.', 'text/plain')
        histories = []
        for index in range(count):
            identifier = 'historical-review-' + str(index)
            assignment_id = 'historical-assignment-' + str(index)
            objection_id = 'historical-objection-' + str(index)
            value = dict(status='sufficient', consequence=content,
                bar_ids=['historical-bar-first', 'historical-bar-second'],
                objective_adequacy='adequate', method_adequacy='adequate',
                reason=content, evidence=[reference], work_items=[],
                assurances=[dict(kind=kind, status='passed', reason=content, evidence=[reference])
                            for kind in problem_rubric.PROBLEM_CHECKS],
                objection_findings=[dict(id=objection_id, status='continuing',
                                         reason=content, evidence=[reference])],
                rubric=dict(admission={item: 'pass' for item in problem_rubric.ADMISSION_ITEMS},
                    significance=dict(B1=2, B2=0), priority=2, attempt='pass',
                    accessibility='E-F', calibration='pass'),
                rubric_evidence={item: content for item in problem_rubric.ITEMS})
            payload = dict(id=identifier, assignment_id=assignment_id, dossier_id=None,
                stage='result', support=None, value=value, limitations=[content],
                objections=[dict(id=objection_id, claim=content, reason=content,
                    resolution_condition=content, evidence=[reference])])
            saved = dict(id=identifier, assignment_id=assignment_id, reviewer_id='historical-reviewer',
                dossier_id=None, dossier_digest=None, recorded_revision=index + 1,
                subject=dict(kind='problem', statement_id='historical-statement'),
                payload=payload, support=None, independence_at_recording='verified_for_route',
                output_digest=digest(payload), packet_digest=digest(dict(assignment=assignment_id)),
                prompt_hash=digest('An independently assigned historical result review.'))
            delivered = review_protocol.deliver_review({'value_review': {identifier: saved}}, saved)
            histories.append(delivered)
        frozen = dict(protocol='exactory-problem-review-v2', role='result',
                      historical_reviews=histories)
        assignment = dict(id='frozen-history-reader', context={},
                          packet=review_protocol._put_json(artifacts, frozen))
        records = {'review_assignment': {assignment['id']: assignment}}
        return review_context_repair.frozen_reassessment_packet(records, artifacts, assignment)


def build_scaled_correction_references(quantity, size):
    """Build a real eligible correction from scaled held records/source bytes."""
    from research_harness import research_decisions, review_output_correction
    from research_harness.errors import ResearchError
    from research_harness.evaluation import Evaluation
    from test_problem_result_reassessment_binding import ResultReassessmentBindingTests
    if quantity not in ('identifiers', 'sources'):
        raise ValueError('The correction reference factory scales record identities or source bytes')
    case = ResultReassessmentBindingTests()
    case.setUp()
    try:
        fixture = case.fixture
        identifier_size = size if quantity == 'identifiers' else 12
        identifier = 'value-' + 'x' * identifier_size
        value = dict(case.value, id=identifier)
        fixture.put('problem_value', identifier, value)
        fixture.expected['value'] = dict(value, digest=digest(value))
        case.reference = dict(kind='record', record_kind='problem_value', id=identifier)
        record_kind = 'quantity_record_' + 'k' * identifier_size
        held = dict(id='quantity-held-record', text='A held finite account record.')
        fixture.put(record_kind, held['id'], held)
        typed = dict(kind='record', record_kind=record_kind, id=held['id'], digest=digest(held))
        fixture.expected['account_records'] = [dict(record=held, cite=typed)]

        respond = fixture.build_provider_response
        def build_response(output):
            response = respond(output)
            if 'allowed_control' in output:
                # An explicit synthetic provider observation gives this real
                # construction journey room. The quantity benchmark remains
                # the separately keyed stage1 route; this is no claim that its
                # smaller allowance admits the complete stress packet.
                response['usage']['input_tokens'] = max(1000000, size * 32)
            return response
        fixture.build_provider_response = build_response
        build_review = fixture.build_review_output
        def build_output(stage, *args, **kwargs):
            output = build_review(stage, *args, **kwargs)
            if stage == 'result':
                output['objections'] = [dict(id='prior-objection',
                    claim='The scope needs an explicit finite restriction.',
                    reason='The supplied standard has a finite scope.',
                    resolution_condition='Retain the finite scope.', evidence=[fixture.supporting])]
            return output
        fixture.build_review_output = build_output
        capture = fixture.capture
        def capture_source(identifier, *args, **kwargs):
            kwargs['body'] = 'x' * (size if quantity == 'sources' else 30) + ' References: none.'
            return capture(identifier, *args, **kwargs)
        fixture.capture = capture_source
        fixture.add_reading_and_computation()
        from research_harness.direction_sources import build_reading_passages
        # The complete source remains supplied through its actual held/read
        # artifact. Use the production option that avoids also emitting every
        # whole-text chunk; partial bibliography/abstract units retain passages.
        fixture.expected['passages'] = []
        readings = [row['reading'] for row in fixture.expected['readings']]
        for passage, data in build_reading_passages(
                fixture.read_records(), fixture.artifacts, readings, whole_text=False):
            if fixture.artifacts.put(data, passage['excerpt']['media_type']) != passage['excerpt']:
                raise AssertionError('The canonical reading passage lost its actual held bytes')
            fixture.expected['passages'].append(passage)
        source_link = fixture.read_records()['reading']['decisive-reading']['inspections'][0]['link']
        case.prepare()
        references = [case.reference, typed, dict(kind='source', link=source_link), fixture.supporting]
        frozen = review_protocol._read_json(fixture.artifacts, case.assignment['packet'])
        prior = frozen['historical_reviews'][0]['payload']['objections'][0]
        output = copy.deepcopy(case.output)
        output['value']['reason'] = 'This retained scientific reasoning differs from its predecessor.'
        output['value']['objection_findings'] = [dict(id=prior['id'], status='continuing',
            reason='The scoped finding remains material.', evidence=copy.deepcopy(references))]
        output['value']['evidence'] = copy.deepcopy(references)
        output['reassessment']['findings'][0]['evidence'] = copy.deepcopy(references)
        for row in output['value']['assurances'] + output['objections']:
            row['evidence'] = copy.deepcopy(references)
        # This stress shape can exceed the default optional arena by itself.
        # Retain the same exact inputs across authentication; request capacity,
        # Store mutations and validation stay unchanged.
        stress_bytes = sum(len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                           for value in (output, frozen))
        raw_artifacts = fixture.artifacts
        arena = Evaluation(fixture.read_records(), raw_artifacts,
                           retention_budget_bytes=max(64 * 1024 * 1024, 8 * stress_bytes))
        fixture.artifacts = arena
        fixture.store.build_artifact_store = lambda: arena
        for attempt_id in ('refused-one', 'refused-two'):
            case.observe(output, attempt_id)
            try:
                fixture.mutate(research_decisions.record_value_review, case.payload(output))
            except ResearchError as error:
                if error.code != 'review_reassessment_mismatch':
                    raise
            else:
                raise AssertionError('The changed confirmed review was not natively refused')
        records = fixture.read_records()
        packet = review_output_correction.prepare_packet(records, arena, case.assignment['id'])
        if size <= 64:
            default = review_output_correction.prepare_packet(records, raw_artifacts, case.assignment['id'])
            if default != packet:
                raise AssertionError('The local retention budget changed the same-record correction output')
        return packet
    finally:
        case.doCleanups()


FACTORIES = {
    'artifact_body_text': build_scaled_artifact_bodies,
    'staged_original_text': build_scaled_staged_originals,
    'staged_adjudicator_original_text': build_scaled_staged_adjudicator_originals,
    'canonical_record_units': build_scaled_record_units,
    'stage2_native_records': build_scaled_stage2_native_records,
    'v6_reading_records': build_scaled_v6_reading_records,
    'correction_history_component': build_scaled_correction_history,
    'correction_reference_records': build_scaled_correction_references,
}
