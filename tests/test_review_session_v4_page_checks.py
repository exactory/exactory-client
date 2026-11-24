"""Answer measures, evidence, prompt order and admission of session v4 pages."""

import atexit
import copy
import functools
import json
import shutil
import sys
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from codex_route_fixtures import invoke_codex
from review_parts_fixtures import PartsResultCase
from strategy_fixtures import StrategyCase
from test_openness_finding_answer import build_answer as build_finding_answer, build_packet as build_finding_packet
from test_review_composition import build_serialized_sentence
import test_review_session_v4_pages as pages
from test_research_problem_candidates import build_candidate
from test_research_problem_forming import build_forming

from research_harness import bar_sources, development, native_review, openness_reading, openness_reading_v7, problem_forming
from research_harness import review_composition as composition, review_condensing, review_continuation, review_handles
from research_harness import openness_final_pages, research_decisions, review_composite, review_parts
from research_harness import review_parts_roles, review_parts_v3, review_refusal, review_transport
from research_harness import review_prompt_paragraphs as paragraphs, review_protocol as protocol
from research_harness import review_route_capacity as capacity, review_session_v4 as session
from research_harness.errors import ResearchError
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.review_failure_class import plan_after_failure
from research_harness.review_refusal_replacement import validate_replacement_origin
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.reading import record_reading
from research_harness.source_links import span_locator
from research_harness.storage import Store


def measure_json(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def make_study_result():
    body = 'A synthetic exact source for the finite claim.\n'
    artifact = describe_artifact(body.encode(), 'text/plain')
    linked = dict(version_id='arxiv:2401.00001v1', source_id='capture', artifact=artifact,
                  locator=span_locator(body, 0, 12, excerpt_length=12))
    source = dict(kind='record', record_kind='problem_reading', id='reading', digest='d' * 64)
    output = StrategyCase.review_response(SimpleNamespace(evidence=source), 'result')
    assessor = dict(id='independent-reader', kind='agent', provenance=artifact,
                    relationship='A separate reader.', independence_basis='A separate review context.')
    output['support'] = dict(id='readiness', candidate_digest='c' * 64, assessor=assessor, verdict='ready',
        limitations=['Only the supplied evidence was assessed.'], checks=[dict(kind=key, status='passed',
            reason='The exact candidate was checked.', evidence=[dict(kind='source', link=linked)])
            for key in development._REVIEW_CHECKS])
    packet = dict(role='result', dossier={}, readiness=dict(kind='readiness'),
        support_contract=dict(candidate_digest='c' * 64), assessor=assessor,
        evidence=[dict(artifact=artifact, content=body, encoding='utf-8')], bar_ids=output['value']['bar_ids'])
    return packet, output


def require_size_refusal(check, *args, **kwargs):
    with unittest.TestCase().assertRaises(ResearchError) as caught:
        check(*args, **kwargs)
    error = caught.exception
    assert (error.code, error.contract_check) == ('review_composition_conflict', 'review.shape')
    return error.details or {}


def require_v7_coverage_bound(check, *args, **kwargs):
    with unittest.TestCase().assertRaises(ResearchError) as caught:
        check(*args, **kwargs)
    error = caught.exception
    assert (error.code, error.contract_check) == ('openness_finding_invalid', 'openness.finding')
    return error.details


def fit_fields_reason(row):
    fields = row['fields']
    path = next(path for path in fields if path.endswith('/reason'))
    fields[path] = ''
    fields[path] = 'x' * (1500 - measure_json(dict(fields=fields)))
    assert measure_json(dict(fields=fields)) == 1500
    assert measure_json(fields) < 1500
    assert measure_json(row) < 1900
    assert all(measure_json(value) < 1500 for value in fields.values())
    return path


def make_parts_final(logical, artifacts, *, targets=None, state=None, adapter='study_readiness'):
    inventory = dict(protocol=review_parts_v3.PROTOCOL, units=[])
    state = review_parts_v3.build_series_state([]) if state is None else state
    header = dict(protocol=review_parts_v3.PROTOCOL, kind='final', page_plan_digest='a' * 64,
                  final_form='closing' if targets is None else 'page', adapter=adapter,
                  capacity={}, targets=[], logical_packet=protocol._put_json(artifacts, logical),
                  logical_packet_digest=digest(logical), inventory=protocol._put_json(artifacts, inventory),
                  inventory_digest=digest(inventory), state=protocol._put_json(artifacts, state), state_digest=digest(state))
    if targets is None:
        header['closing'] = dict(rows=[])
    else:
        header['page'] = dict(targets=targets)
        header['targets'] = composition.list_targets(composition.find_rule(logical['role'], logical), logical)
    return {'review_parts': header}, inventory, state


def validate_parts_final(logical, output, *, targets=None):
    with TemporaryDirectory() as root:
        artifacts = ArtifactStore(Path(root))
        packet, inventory, state = make_parts_final(logical, artifacts, targets=targets)
        review_parts_v3.validate_planned_output(packet, output, inventory, {}, state, {},
                                              artifacts=artifacts, assignment={})


def native_result_packet():
    return dict(role='native_math', native=dict(kind='result', subject_digest='a' * 64, claim_digest='b' * 64,
        reviewer=dict(source='host', actor_id='reader', attestation_id='review-assignment:synthetic'),
        contract={}, scientific_context={}))


def capture_expansion(packet, answer, artifacts, assignment):
    response = dict(id='test-response', status='completed',
                    output=[dict(type='message', content=[dict(type='output_text', text=json.dumps(answer))])])
    observation = dict(status='completed', output=protocol._put_json(artifacts, answer),
                       response=protocol._put_json(artifacts, response))
    capture = review_handles.record_handle_output(packet, observation, artifacts, assignment=assignment)
    expanded = protocol._read_json(artifacts, capture['output'])
    assert capture['handle_expansion']['references'] > 0
    assert capture['handle_expansion']['output_sha256'] == digest(expanded)
    assert review_handles.derive_expanded_output(packet, dict(capture, output=expanded), response,
                                                artifacts=artifacts, assignment=assignment) == expanded
    return expanded


def make_v7_page():
    packet = build_finding_packet()
    packet['native']['scientific_context']['openness_minimum']['neighbours'] = ['Neighbour one', 'Neighbour two']
    packet['reading_final'] = dict(protocol=openness_reading_v7.PROTOCOL, page_plan_digest='a' * 64,
                                   page=dict(targets=['t1']))
    original = build_finding_answer(packet)
    original['findings']['openness']['coverage'] = ['Checked the exact statement.', 'Checked one.', 'Checked two.']
    rule = composition.find_rule('native_math', packet)
    pages, closing = composition.split_output(rule, packet, original, [['t1'], ['t2'], ['t3']])
    return rule, packet, pages, closing


def bind_v7_final(packet, artifacts, references=None):
    plan, inventory, state = {}, {}, dict(entries=[], references=references or {})
    packet['reading_final'].update(plan_digest=digest(plan), inventory_digest=digest(inventory),
                                   state_digest=digest(state), capacity={})
    return dict(context=dict(native_packet=protocol._put_json(artifacts, packet['native'])),
        reading_chain=dict(inventory=protocol._put_json(artifacts, inventory), state=protocol._put_json(artifacts, state)),
        capacity_plan=dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan),
                           artifact=protocol._put_json(artifacts, plan)))


def validate_v7_page(packet, output):
    with TemporaryDirectory() as root:
        artifacts = ArtifactStore(Path(root))
        assignment = bind_v7_final(packet, artifacts)
        openness_reading.validate_turn_output(packet, output, artifacts=artifacts, assignment=assignment)


@contextmanager
def open_session():
    fixture = pages.SessionPagesTests()
    try:
        fixture.setUp()
        yield fixture
    finally:
        fixture.doCleanups()


def check_request_bound(fixture, assignment):
    records = fixture.case.read_records()
    request = protocol.build_assignment_request(records, fixture.case.artifacts, assignment)
    plan = session.read_session_binding(fixture.read_packet(assignment))['capacity']
    route = records['review_route'][assignment['route_id']]
    assert capacity.measure_request(route, request)['text_characters'] <= plan['request_bound']


def request_page_passage(fixture):
    fixture.add_sources([3000])
    current = fixture.start()
    _, inventory, _ = session.read_bound_session(fixture.case.artifacts, current)
    unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
    check_request_bound(fixture, current)
    request = dict(schema_version=1, stage='request', notes='Page notes.', excerpts=[], settled_requests=[],
                   requests=[dict(id='passage', kind='passage', unit=unit['handle'], anchor=None, characters=20)])
    current = fixture.advance(current, request)
    packet = fixture.read_packet(current)
    delivery, = packet['session_final']['plan']['now']
    assert (delivery['unit'], delivery['start'], delivery['end']) == (unit['handle'], 0, 20)
    answer = fixture.answer(current)
    fields = answer['targets'][0]['fields']
    path = next(path for path in fields if path.endswith('/evidence'))
    fields[path] = [dict(ref=unit['handle'], part=delivery['part'], start=0, end=20)]
    return current, packet, answer, unit, path


def require_recorded_answer_bound(fixture, assignment, answer):
    check_request_bound(fixture, assignment)
    invoke_codex(fixture.case, assignment['id'], answer)
    records = fixture.case.read_records()
    refusal = records.get('review_output_refusal', {}).get('attempt-' + assignment['id'])
    assert refusal is not None, 'Expected a size refusal on the turn.'
    assert (refusal['code'], refusal['check']) == ('review_composition_conflict', 'review.shape')
    assert not protocol.assignment_state(records, fixture.case.artifacts, assignment['id'])['ready']
    return refusal


def finish_captured_refusal(case, current, invoke, answer, code, check):
    artifacts, store = case.artifacts, case.store
    with patch.object(protocol, '_finish_call', side_effect=RuntimeError('Interrupted after capture.')):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, 'Interrupted after capture'):
            invoke(current, answer)
    resume = review_continuation.plan_next(case.read_records(), artifacts, current['id'])
    assert (resume['state'], resume['command']) == ('resume', 'review-run'), resume
    with patch.object(review_transport, 'send_request', side_effect=AssertionError('The call was already captured.')):
        receipt = protocol.invoke_assignment(store, resume['payload'], expected_revision=store.revision,
            request_id=resume['request_id'], credential='test-only')
    records = case.read_records()
    attempt_id = resume['payload']['id']
    assert protocol.authenticate_attempt(records, artifacts, current, records['review_attempt'][attempt_id])
    refusal = records['review_output_refusal'][attempt_id]
    assert (refusal['code'], refusal['check']) == (code, check), refusal
    assert receipt['output_refusal']['details'] == refusal['details']
    replacement = review_continuation.plan_next(records, artifacts, current['id'])
    assert (replacement['state'], replacement['command']) == ('replace', 'review-assignment'), replacement
    assignment = case.mutate(protocol.record_assignment, replacement['payload'])['result']
    records = case.read_records()
    packet = protocol._read_json(artifacts, assignment['packet'])
    validate_replacement_origin(records, artifacts, assignment, packet)
    following = review_continuation.plan_next(records, artifacts, assignment['id'])
    assert following['head_id'] == assignment['id'] and following['state'] == 'run', following
    assert following['payload'] != resume['payload']
    notice = packet['output_refusal']
    assert notice['refused'][-1]['code'] == code
    return notice


@functools.lru_cache(maxsize=None)
def build_closing_state(passage):
    """P3. Reach the closing of a nine-target session once for the module; each test works on its own copy.

    With passage, the first page requests and cites a delivered part, and the source of that part is read."""
    directory = TemporaryDirectory()
    atexit.register(directory.cleanup)
    with open_session() as fixture:
        citation = None
        if passage:
            current, packet, answer, unit, evidence = request_page_passage(fixture)
            citation, = answer['targets'][0]['fields'][evidence]
            version = unit['identity']['version_id']
            capture = fixture.case.read_records()['work'][version]['fulltexts'][0]
            bundle = fixture.case.bundle(version, capture=capture, bundle_id='closing-source')
            fixture.case.mutate(import_bundle, bundle)
            fixture.case.mutate(record_reading, fixture.case.full_note(bundle, note_id='closing-reading'))
        else:
            fixture.add_sources([3000])
            current = fixture.start()
        while 'closing' not in fixture.read_packet(current)['session_final']:
            check_request_bound(fixture, current)
            current = fixture.advance(current)
        check_request_bound(fixture, current)
        root = Path(directory.name) / 'study'
        shutil.copytree(fixture.case.root, root)
        return SimpleNamespace(directory=directory, root=root, assignment=current, citation=citation,
                               supporting=fixture.case.supporting, sequence=fixture.case.sequence)


@contextmanager
def open_closing(*, passage=False):
    """Open a fresh session fixture whose store is a copy of the module's closing state."""
    state = build_closing_state(passage)
    with open_session() as fixture:
        case = fixture.case
        case.root = Path(case.temporary.name) / 'closing'
        shutil.copytree(state.root, case.root)
        case.store, case.artifacts = Store(case.root), ArtifactStore(case.root)
        case.supporting, case.sequence = state.supporting, state.sequence
        yield fixture, copy.deepcopy(state.assignment), copy.deepcopy(state.citation)


def check_captured_closing_refusal(reason_characters, code, check):
    with open_closing() as (fixture, current, _):
        packet = fixture.read_packet(current)
        artifacts = fixture.case.artifacts
        _, inventory, _ = session.read_bound_session(artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        assert not session.build_handle_packet(packet, artifacts, current)['handle_table'][unit['handle']]['parts']
        answer = fixture.answer(current)
        answer['fields'].update({'/value/evidence': [dict(ref=unit['handle'])], '/value/reason': 'r' * reason_characters})
        notice = finish_captured_refusal(fixture.case, current,
            lambda assignment, output: invoke_codex(fixture.case, assignment['id'], output), answer, code, check)
        return notice, unit['artifact']


def make_unreadable_link(artifact):
    """A source link whose version id does not parse as a URL. Release 0.92.1 raised ValueError on it; the identifier
    check now refuses it with invalid_identifier (repairs A4)."""
    return dict(kind='source', link=dict(version_id='https://[', source_id='capture', artifact=artifact,
                                         locator=dict(kind='artifact')))


def require_unreadable_refusal(check, *args, **kwargs):
    with unittest.TestCase().assertRaises(ResearchError) as caught:
        check(*args, **kwargs)
    error = caught.exception
    assert (error.code, error.contract_check, error.message) == (
        'review_composition_conflict', 'review.shape', review_refusal.UNREADABLE_ANSWER_ERR_MSG), error.as_dict()
    # Repairs A7: the class of the exception and the function where it rose, for the operator.
    details = dict(error.details)
    assert set(details.pop('harness_exception')) == {'class', 'function'}, error.details
    return details


def check_page_refused(mutate):
    with open_session() as fixture:
        current = fixture.start()
        answer = fixture.answer(current)
        mutate(answer['targets'][0]['fields'])
        invoke_codex(fixture.case, current['id'], answer)
        records, artifacts = fixture.case.read_records(), fixture.case.artifacts
        refusal = records.get('review_output_refusal', {}).get('attempt-' + current['id'])
        assert refusal is not None, 'The page was accepted.'
        assert refusal['check'] == 'review.shape', refusal
        assert refusal['details']['path'].startswith('/targets/0/'), refusal
        assert plan_after_failure(records, artifacts, current['id'])['state'] == 'replace'


def raise_unhashable(*args, **kwargs):
    raise TypeError('unhashable type')


def check_study_admission(fixture, packet):
    records, artifacts = fixture.case.read_records(), fixture.case.artifacts
    for row in packet.get('evidence', []):
        artifacts.put(row['content'].encode(), row['artifact']['media_type'])
    logical = dict(packet, protocol=bar_sources.PROTOCOL, source_rules=bar_sources.PROTOCOL_V3)
    return session.check_v4_admission(records, artifacts, records['review_route']['route'], 'result', logical)


class SessionPageMeasureTests(unittest.TestCase):
    """The measures of page and closing answers against their bounds."""

    def test_study_page_charges_literal_source_and_execution_references(self):
        """P1.1. Both written reference types occur in study readiness evidence."""
        packet, output = make_study_result()
        check = output['support']['checks'][0]
        source = check['evidence'][0]
        execution = dict(kind='result', execution_id='run-1', output_id='out-1',
                         artifact=source['link']['artifact'], locator=dict(kind='artifact'))
        rule = composition.find_rule('result', packet)
        targets = [check['kind']]
        page, _ = composition.split_output(rule, packet, output, [targets])
        row = page[0]['output']['targets'][0]
        # A bound v3 page names its target by handle or by scientific id, not by the position of the unbound split.
        row['target'] = check['kind']
        path = next(path for path in row['fields'] if path.endswith('/evidence'))
        for reference, count in ((source, 6), (execution, 9)):
            row['fields'][path] = [reference] * 2
            reason = fit_fields_reason(row)
            validate_parts_final(packet, page[0]['output'], targets=targets)
            row['fields'][reason] += 'x'
            require_size_refusal(lambda answer: validate_parts_final(packet, answer, targets=targets), page[0]['output'])
            row['fields'][reason] = 'Checked the source.'
            row['fields'][path] = [reference] * count
            assert measure_json(page[0]['output']) > composition.describe_page_answer_bound(rule, 1)
            require_size_refusal(lambda answer: validate_parts_final(packet, answer, targets=targets), page[0]['output'])

    def test_parts_block_entries_handle_is_charged_at_its_expanded_size(self):
        """P1.1. A real block entries citation contracts and a real card citation grows."""
        block = review_condensing._build_block([dict(handle='one', characters=2)], 2)
        state = review_parts_v3.build_series_state([])
        state['combinations'] = [dict(id=block['handle'], mode='condense', block=block, output={'summary': 'Checked.'})]
        state['cards'] = [dict(id='c1-1', output={'summary': 'Checked.'})]
        with TemporaryDirectory() as root:
            artifacts = ArtifactStore(Path(root))
            packet, inventory, state = make_parts_final(native_result_packet(), artifacts, state=state, adapter='native_math')
            def validate(answer, *, expanded=False):
                return review_parts_v3.validate_planned_output(packet, answer, inventory, {}, state, {},
                                                              artifacts=artifacts, assignment={}, expanded=expanded)
            for handle, difference in ((block['handle'] + '/entries', -17), ('c1-1', 52)):
                for size in (15999, 16000, 16001):
                    assessment = dict(decision='revise', reason='Further checks are needed.', notes=['x' * 3500] * 4 + [''],
                                      reference=dict(ref=handle))
                    closing = dict(assessment=assessment)
                    assessment['notes'][-1] = 'x' * (size - measure_json(closing) - difference)
                    expanded = capture_expansion(packet, closing, artifacts, {})
                    assert measure_json(expanded) == size
                    assert measure_json(expanded) - measure_json(closing) == difference
                    if size <= 16000:
                        validate(expanded, expanded=True)
                        validate(closing)
                    else:
                        assert require_size_refusal(validate, closing) == require_size_refusal(validate, expanded, expanded=True)

    def test_page_text_over_the_fields_limit_is_refused(self):
        """P1.1 and P2.1. One legal 70-character reference fits at the joint boundary."""
        packet = dict(role='result', subject=dict(kind='problem'), problem_evidence={})
        rule = composition.find_rule('result', packet)
        target = composition.ASSURANCES[0]
        citation = dict(kind='record', record_kind='problem_statement', id='statement-1')
        assert measure_json(citation) == 70
        row = dict(target=target, sentence='Checked.', fields={
            '/value/assurances/0/kind': target, '/value/assurances/0/status': 'passed',
            '/value/assurances/0/reason': '', '/value/assurances/0/evidence': [citation],
            '/value/objection_findings': [], '/value/work_items': []})
        reason = fit_fields_reason(row)
        answer = dict(targets=[row])
        composition.validate_page_output(rule, packet, [target], answer)
        row['fields'][reason] += 'x'
        assert measure_json(dict(fields=row['fields'])) == 1501
        require_size_refusal(composition.validate_page_output, rule, packet, [target], answer)

    def test_parts_native_closing_counts_opaque_evidence_at_the_full_answer_limit(self):
        """P1.2. Native revise extras can contain arbitrary data under evidence-like keys."""
        packet = native_result_packet()
        assessment = dict(decision='revise', reason='Further checks are needed.',
                          evidence=[dict(note='x' * 3500) for _ in range(4)] + [dict(note='')])
        closing = dict(assessment=assessment)
        tail = assessment['evidence'][-1]
        tail['note'] = 'x' * (16000 - measure_json(closing))
        assert measure_json(closing) == 16000
        validate_parts_final(packet, closing)
        tail['note'] += 'x'
        assert measure_json(closing) == 16001
        require_size_refusal(validate_parts_final, packet, closing)
        assessment.pop('evidence')
        assessment['notes'] = [{'note/evidence': [{} for _ in range(30)]} for _ in range(7)]
        assert measure_json(closing) < 1000
        validate_parts_final(packet, closing)

    def test_v7_coverage_counts_ordinary_objects_at_its_own_limit(self):
        """P1.2. Structured coverage uses ordinary JSON even under a key ending in /evidence."""
        rule, packet, pages, closing = make_v7_page()
        answer = pages[0]['output']
        row = answer['targets'][0]
        row['coverage'] = {'note/evidence': [{} for _ in range(10)], 'note': ''}
        row['coverage']['note'] = 'x' * (800 - measure_json(row['coverage']))
        assert measure_json(row['coverage']) == 800
        validate_v7_page(packet, answer)
        complete = dict(packet, reading_final=dict(packet['reading_final'], page=dict(targets=['t1', 't2', 't3'])))
        native_review.validate_finding(complete, composition.compose_output(rule, packet, pages, closing))
        row['coverage']['note'] += 'x'
        assert require_v7_coverage_bound(validate_v7_page, packet, answer) == dict(
            rule='coverage_bound', path='/targets/0/coverage', size='coverage', characters=801, maximum_characters=800)

    def test_v7_page_counts_opaque_coverage_at_the_joint_limit(self):
        """P1.2. Coverage and two individually valid grounds share the joint page bound."""
        _, packet, pages, _ = make_v7_page()
        answer = pages[0]['output']
        row = answer['targets'][0]
        row['coverage'] = dict(evidence=[dict(note='a' * 695)])
        row['grounds'] = [dict(kind='own_argument', text='b' * 500), dict(kind='own_argument', text='')]
        values = dict(coverage=row['coverage'], grounds=row['grounds'])
        row['grounds'][-1]['text'] = 'c' * (1500 - measure_json(values))
        assert measure_json(values) == 1500
        validate_v7_page(packet, answer)
        row['grounds'][-1]['text'] += 'c'
        assert require_size_refusal(validate_v7_page, packet, answer) == dict(
            path='/targets/0', size='page_fields', characters=1501, maximum_characters=1500)
        row['grounds'][-1]['text'] = row['grounds'][-1]['text'][:-1]
        row['sentence'] = ''
        row['sentence'] = build_serialized_sentence(1900 - measure_json(row))
        assert measure_json(row) == 1900
        validate_v7_page(packet, answer)
        row['sentence'] += 'x'
        assert require_size_refusal(validate_v7_page, packet, answer) == dict(
            path='/targets/0', size='page_row', characters=1901, maximum_characters=1900)

    def test_v7_written_page_and_closing_use_the_captured_expansion(self):
        """Exercise the v7 coverage probe and put a real ground citation on both form boundaries."""
        _, packet, pages, closing = make_v7_page()
        with TemporaryDirectory() as root:
            artifacts = ArtifactStore(Path(root))
            reading = dict(reading_turn=dict(turn=1, allowances=dict(targets=[])))
            references = openness_reading_v7.project_own_turn(artifacts, {}, reading, dict(notes='A search gap.'),
                                                              'reading-1', 'attempt-reading-1')['references']
            assignment = bind_v7_final(packet, artifacts, references)
            def validate(turn_packet, answer, *, expanded=False):
                openness_reading.validate_turn_output(turn_packet, answer, artifacts=artifacts, assignment=assignment,
                                                      expanded=expanded)
            answer = pages[0]['output']
            row = answer['targets'][0]
            row['coverage'] = dict(citations=[dict(ref='n1')] * 4)
            expanded = capture_expansion(packet, answer, artifacts, assignment)
            assert measure_json(row['coverage']) < 800 < measure_json(expanded['targets'][0]['coverage'])
            details = require_v7_coverage_bound(validate, packet, answer)
            assert details == require_v7_coverage_bound(validate, packet, expanded, expanded=True)
            assert details['size'] == 'coverage' and details['maximum_characters'] == 800

            row['coverage'] = 'Checked.'
            row['grounds'] = [dict(kind='own_argument', text='First ground.', refs=[dict(ref='n1')]),
                              dict(kind='own_argument', text='x' * 570), dict(kind='own_argument', text='')]
            for size in (1499, 1500, 1501):
                row['grounds'][-1]['text'] = ''
                expanded = review_handles.expand_handles(packet, answer, artifacts=artifacts, assignment=assignment, raw=True)
                values = {key: expanded['targets'][0][key] for key in ('coverage', 'grounds')}
                row['grounds'][-1]['text'] = 'x' * (size - measure_json(values))
                expanded = capture_expansion(packet, answer, artifacts, assignment)
                assert measure_json({key: expanded['targets'][0][key] for key in values}) == size
                if size <= 1500:
                    validate(packet, answer)
                    validate(packet, expanded, expanded=True)
                else:
                    details = require_size_refusal(validate, packet, answer)
                    assert details['characters'] == size
                    assert details == require_size_refusal(validate, packet, expanded, expanded=True)

            packet['reading_final'].pop('page')
            packet['reading_final']['closing'] = dict(rows=[])
            closing.update(outcome='unresolved', grounds=[dict(kind='own_argument', text='x' * 570,
                                                                   refs=[dict(ref='n1')]) for _ in range(7)])
            for key in native_review.RESULT_FINDINGS:
                closing[key] = 'f' * 1980
            for size in (15999, 16000, 16001):
                closing['reason'] = ''
                expanded = review_handles.expand_handles(packet, closing, artifacts=artifacts, assignment=assignment, raw=True)
                closing['reason'] = 'r' * (size - measure_json(expanded))
                expanded = capture_expansion(packet, closing, artifacts, assignment)
                assert measure_json(expanded) == size
                if size <= 16000:
                    validate(packet, closing)
                    validate(packet, expanded, expanded=True)
                else:
                    details = require_size_refusal(validate, packet, closing)
                    assert details['characters'] == size
                    assert details == require_size_refusal(validate, packet, expanded, expanded=True)

    def test_math_evidence_digests_keep_their_json_size(self):
        """T-B3. Thirty math evidence digests have the same measure on both trees."""
        digests = [digest(index) for index in range(30)]
        value = {'findings': {'openness': {'evidence': digests}}}
        assert composition._measure_final_value(value) == measure_json(value)
        schema = dict(type='array', max_items=32, items=dict(type='string', max_characters=64,
                      max_serialized_characters=66), max_serialized_characters=measure_json(digests))
        composition._validate_answer_schema({}, schema, digests, '/findings/openness/evidence')

    def test_oversized_page_with_delivered_citation_records_a_refusal(self):
        """T-G2 and P2.1. One delivered citation leaves a one-character joint-boundary pair."""
        with open_session() as fixture:
            current, packet, answer, _, path = request_page_passage(fixture)
            artifacts = fixture.case.artifacts
            expanded = review_handles.expand_handles(packet, answer, artifacts=artifacts, assignment=current)
            row = expanded['targets'][0]
            assert measure_json(row['fields'][path][0]) == 505
            reason = fit_fields_reason(row)
            records = fixture.case.read_records()
            written = answer['targets'][0]['fields']
            for text in (row['fields'][reason][:-1], row['fields'][reason]):
                written[reason] = text
                session.validate_final_turn_output(records, artifacts, current, packet, answer)
            written[reason] += 'x'
            details = dict(path='/targets/0/fields', size='page_fields', characters=1501, maximum_characters=1500)
            assert require_size_refusal(session.validate_final_turn_output, records, artifacts, current, packet, answer) == details
            require_recorded_answer_bound(fixture, current, answer)
            records = fixture.case.read_records()
            plan = plan_after_failure(records, artifacts, current['id'])
            assert plan['state'] == 'replace'
            replacement = fixture.case.mutate(protocol.record_assignment, plan['payload'])['result']
            refused = fixture.read_packet(replacement)['output_refusal']['refused'][-1]
            assert refused['path'] == details.pop('path') and refused['details'] == details
            written[reason] = written[reason][:-1]
            check_request_bound(fixture, replacement)
            invoke_codex(fixture.case, replacement['id'], answer)
            records = fixture.case.read_records()
            attempt = records['review_attempt']['attempt-' + replacement['id']]
            assert attempt['handle_expansion']['references'] == 1
            assert protocol.authenticate_attempt(records, artifacts, replacement, attempt)
            assert protocol.assignment_state(records, artifacts, replacement['id'])['ready']
            assert 'page-check-bundle' not in records.get('source_bundle', {})
            assert 'page-check-reading' not in records.get('reading', {})

    def test_session_page_refuses_repeated_written_links_over_its_bound(self):
        """P1.1. Reproduce the review's large written page using the delivered passage."""
        with open_session() as fixture:
            current, packet, answer, _, path = request_page_passage(fixture)
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            expanded = review_handles.expand_handles(packet, answer, artifacts=artifacts, assignment=current)
            session.validate_final_turn_output(records, artifacts, current, packet, expanded)
            reference = expanded['targets'][0]['fields'][path][0]
            for row in expanded['targets']:
                evidence = next(path for path in row['fields'] if path.endswith('/evidence'))
                row['fields'][evidence] = [reference] * 14
            assert measure_json(expanded) > 15240
            require_recorded_answer_bound(fixture, current, expanded)

    def test_session_closing_enforces_its_expanded_answer_bound(self):
        """P1.1. A cited part and text on several fields share the 16,000-character closing cap."""
        with open_closing(passage=True) as (fixture, current, citation):
            packet = fixture.read_packet(current)
            answer = fixture.answer(current)
            fields = answer['fields']
            fields.update({'/value/evidence': [citation] * 14, '/value/reason': 'r' * 3800,
                           '/value/consequence': 'c' * 3800, '/limitations': ['']})
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            expanded = review_handles.expand_handles(packet, answer, artifacts=artifacts, assignment=current)
            limit = 'l' * (16000 - measure_json(expanded))
            for text in (limit[:-1], limit):
                fields['/limitations'] = [text]
                session.validate_final_turn_output(records, artifacts, current, packet, answer)
            fields['/limitations'] = [limit + 'l']
            details = dict(path='', size='closing_total', characters=16001, maximum_characters=16000)
            assert require_size_refusal(session.validate_final_turn_output, records, artifacts, current, packet, answer) == details
            refusal = require_recorded_answer_bound(fixture, current, answer)
            assert all(refusal['details'][key] == value for key, value in details.items())
            plan = plan_after_failure(fixture.case.read_records(), artifacts, current['id'])
            assert plan['state'] == 'replace'
            replacement = fixture.case.mutate(protocol.record_assignment, plan['payload'])['result']
            refused = fixture.read_packet(replacement)['output_refusal']['refused'][-1]
            assert refused['path'] == details.pop('path') and refused['details'] == details
            fields['/limitations'] = [limit]
            check_request_bound(fixture, replacement)
            invoke_codex(fixture.case, replacement['id'], answer)
            records = fixture.case.read_records()
            attempt = records['review_attempt']['attempt-' + replacement['id']]
            assert attempt['handle_expansion']['references'] == 14
            assert protocol.authenticate_attempt(records, artifacts, replacement, attempt)
            assert measure_json(protocol._read_json(artifacts, attempt['output'])) == 16000
            assert protocol.assignment_state(records, artifacts, replacement['id'])['ready']
            review_composite.authenticate_composite(records, artifacts, records['review_composite'][attempt['id']])

    def test_invalid_closing_handle_is_refused_before_size_measurement(self):
        """A retained answer with four resolvable handles and an unknown fifth cannot reach composition."""
        with open_closing(passage=True) as (fixture, current, citation):
            answer = fixture.answer(current)
            answer['fields']['/value/evidence'] = [citation] * 4 + [dict(ref='missing-unit')]
            check_request_bound(fixture, current)
            with patch.object(composition, 'validate_closing_output', wraps=composition.validate_closing_output) as validate:
                receipt = invoke_codex(fixture.case, current['id'], answer)
            validate.assert_not_called()
            refusal = receipt['output_refusal']
            assert refusal['code'] == 'invalid_reference_handle'
            assert refusal['details']['path'] == '/fields/~1value~1evidence/4'
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            attempt = records['review_attempt']['attempt-' + current['id']]
            assert 'handle_expansion' not in attempt
            assert protocol.authenticate_attempt(records, artifacts, current, attempt)
            assert attempt['id'] in records['review_output_refusal']

    def test_page_sentence_size_survives_replacement_notice(self):
        with open_session() as fixture:
            current = fixture.start()
            answer = fixture.answer(current)
            answer['targets'][0]['sentence'] = 's' * 301
            require_recorded_answer_bound(fixture, current, answer)
            plan = plan_after_failure(fixture.case.read_records(), fixture.case.artifacts, current['id'])
            replacement = fixture.case.mutate(protocol.record_assignment, plan['payload'])['result']
            notice = fixture.read_packet(replacement)['output_refusal']
            entry = notice['refused'][-1]
            assert entry['path'] == '/targets/0/sentence'
            assert entry['details'] == dict(size='text_characters', characters=301, maximum_characters=300)
            assert notice['refused_part']['value'] == 's' * 301

    def test_v7_page_ground_text_size_names_the_submitted_field(self):
        _, packet, pages, _ = make_v7_page()
        answer = pages[0]['output']
        answer['targets'][0]['grounds'] = [dict(kind='own_argument', text='g' * 601)]
        with unittest.TestCase().assertRaises(ResearchError) as caught:
            validate_v7_page(packet, answer)
        error = caught.exception
        assert (error.code, error.contract_check) == ('openness_finding_invalid', 'openness.finding')
        assert error.details == dict(path='/targets/0/grounds/0/text', rule='ground_bound',
                                     size='text_characters', characters=601, maximum_characters=600)

    def test_v7_closing_statement_text_size_names_the_submitted_field(self):
        _, packet, _, closing = make_v7_page()
        packet['reading_final'].pop('page')
        packet['reading_final']['closing'] = dict(rows=[])
        closing['statement'] = 's' * 2001
        assert require_size_refusal(validate_v7_page, packet, closing) == dict(
            path='/statement', size='text_characters', characters=2001, maximum_characters=2000)

    def test_oversized_page_is_refused_even_if_a_cited_unit_never_arrived(self):
        """The size limit is enforceable before the packet-only artifact read."""
        with open_session() as fixture:
            fixture.add_sources([3000])
            current = fixture.start()
            packet = fixture.read_packet(current)
            artifacts = fixture.case.artifacts
            _, inventory, _ = session.read_bound_session(artifacts, current)
            unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
            table = session.build_handle_packet(packet, artifacts, current)['handle_table']
            assert not table[unit['handle']]['parts']
            answer = fixture.answer(current)
            fields = answer['targets'][0]['fields']
            fields[next(path for path in fields if path.endswith('/evidence'))] = [dict(ref=unit['handle'])]
            fields[next(path for path in fields if path.endswith('/reason'))] = 'r' * 1501
            require_recorded_answer_bound(fixture, current, answer)


class SessionAdmissionTests(unittest.TestCase):
    """The fixture, the prompt order and the admission of session v4."""

    def test_partial_session_setup_restores_the_inner_fixture(self):
        """P2.2. The inner setup can fail after replacing problem_claims."""
        key = 'research_harness.problem_claims'
        before = sys.modules[key]
        problem = pages.turn_fixtures.problem_fixtures
        fixture = pages.SessionPagesTests('test_terminal_page_accounts_for_delivered_source_and_composite_part_citation')
        try:
            with patch.object(problem, 'install_rubric_anchors', side_effect=RuntimeError('Fixture stopped.')):
                result = fixture.run()
            assert len(result.errors) == 1 and 'Fixture stopped.' in result.errors[0][1], result.errors
            assert sys.modules[key] is before
        finally:
            fixture.case.doCleanups()
            sys.modules[key] = before

    def test_slate_forming_paragraph_precedes_the_request_text_and_the_turn_verifies(self):
        """T-O1. The recorded slate needs two prior bars and one formed candidate."""
        with open_session() as fixture:
            call = fixture.case.mutate(problem_forming.record_forming, build_forming())['result']
            reference = dict(kind='record', record_kind='forming_call', id=call['id'], digest=digest(call))
            candidate = build_candidate(source=dict(kind='own_record', reference='A formed comparison.',
                                                   evidence=[reference]))
            fixture.case.put('problem_candidate', candidate['id'],
                             dict(candidate, recorded_revision=fixture.case.store.revision + 1))
            fixture.record_legacy_bars()
            assignment = fixture.case.assign('slate', 'first', identifier='formed-slate')
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            packet = fixture.read_packet(assignment)
            prompt = assignment['prompt']
            assert 'exactory-problem-slate-forming-v3' in prompt['parameters'][paragraphs.PARAMETER]
            assert session.authenticate_session_binding(records, artifacts, assignment, packet)
            check_request_bound(fixture, assignment)
            invoke_codex(fixture.case, assignment['id'], dict(unexpected='Refuse this request.'))
            plan = plan_after_failure(fixture.case.read_records(), artifacts, assignment['id'])
            assert plan['state'] == 'replace', plan['state']
            replacement = fixture.case.mutate(protocol.record_assignment, plan['payload'])['result']
            replacement_packet = fixture.read_packet(replacement)
            assert session.authenticate_session_binding(fixture.case.read_records(), artifacts, replacement, replacement_packet)
            for rendered in (prompt, replacement['prompt']):
                assert rendered['text'].index(paragraphs.SLATE_FORMING_PROMPT_V3) < rendered['text'].index(session.SLATE_REQUEST_PROMPT)
                assert rendered['text'].endswith(session.TURN_LIMITS_PROMPT)
            assert paragraphs.REFUSAL_PARAGRAPH_VERSIONS['bounded'] in replacement['prompt']['parameters'][paragraphs.PARAMETER]
            text = replacement['prompt']['text']
            assert text.index(paragraphs.REFUSAL_BOUNDED_PROMPT) < text.index(session.SLATE_REQUEST_PROMPT)

    def test_scoped_study_result_stops_session_v4_admission(self):
        """T-S1. The target in the study support contract selects the next rule set."""
        packet = dict(role='result', protocol=bar_sources.PROTOCOL, source_rules=bar_sources.PROTOCOL_V3,
            readiness={}, support_contract=dict(target=dict(kind='source_limited_manuscript',
                contract_id='contract-1', scientific_target_digest='d' * 64),
                required_checks=list(development._REVIEW_CHECKS) + ['source_limits']))
        one = session.check_v4_admission({}, None, dict(adapter='codex_cli_v1'), 'result', packet)
        assert one.get('state') == 'stop', one.get('state')
        assert one == dict(state='stop', protocol=session.PROTOCOL, reason='scoped_support_contract')

    def test_unscoped_study_result_is_admitted_to_session_v4(self):
        """T-S2. An unscoped support contract is admitted on both trees."""
        packet, _ = make_study_result()
        with open_session() as fixture:
            admission = check_study_admission(fixture, packet)
        assert admission['state'] == 'record', admission.get('details')


class SessionPageRefusalTests(unittest.TestCase):
    """The refusals of page, closing and request answers of a final, and their replacements."""

    def test_page_validates_the_delivered_part_and_records_the_review(self):
        """T-G1. Nine targets force a bar into pages, where one request delivers a cited part."""
        with open_session() as fixture:
            current, packet, answer, unit, _ = request_page_passage(fixture)
            records = fixture.case.read_records()
            assert 'page-check-bundle' not in records.get('source_bundle', {})
            assert 'page-check-reading' not in records.get('reading', {})
            version = unit['identity']['version_id']
            capture = records['work'][version]['fulltexts'][0]
            bundle = fixture.case.bundle(version, capture=capture, bundle_id='page-check-bundle')
            fixture.case.mutate(import_bundle, bundle)
            fixture.case.mutate(record_reading, fixture.case.full_note(bundle, note_id='page-check-reading'))
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            session.validate_final_turn_output(records, artifacts, current, packet, answer)
            check_request_bound(fixture, current)
            following = fixture.advance(current, answer)
            _, _, record = fixture.finish_pages(following)
            saved = fixture.case.read_records()['value_review']['review-pages']
            assert saved['composite']['id'] == record['id']
            reference = saved['payload']['value']['objection_findings'][0]['evidence'][0]
            assert reference['link']['artifact'] == unit['artifact']
            assert (reference['link']['locator']['start'], reference['link']['locator']['end']) == (0, 20)

    def test_released_parts_v2_passage_refusal_keeps_replacement_bytes(self):
        """Reproduce the 0.92.0 late final with enough input room to record a fresh replacement."""
        with ExitStack() as stack:
            fixture = PartsResultCase()
            stack.callback(fixture.doCleanups)
            for module, member, value in (
                    (protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)),
                    (review_parts, 'NEW_SERIES_PROTOCOLS', (review_parts.PROTOCOL,)),
                    (openness_reading, 'NEW_MATH_CHAIN_PROTOCOLS', (openness_reading.MATH_PROTOCOL,)),
                    (openness_reading_v7, 'V7_FINAL_FORMS', ('request',))):
                stack.enter_context(patch.object(module, member, value))
            fixture.setUp()
            fixture.oversized()
            version = fixture.case.metadata(92)
            fixture.case.capture(version, pdf_text='A captured theorem gives the exact bound for every finite graph.')
            _, current = fixture.finish_series()
            for index in range(3):
                query = 'query' + str(index)
                fixture.invoke(current, dict(kind='requests', unit_requests=[],
                    source_requests=[dict(id=query, kind='inventory_query', query=query, page=0)]))
                current = fixture.assign('further-' + str(index), current['id'])
            assert current['review_parts']['protocol'] == review_parts.PROTOCOL
            request = dict(id='passage', kind='passage', version_id=version,
                           anchor='A captured theorem gives the exact bound', characters=20001)
            receipt = fixture.invoke(current, dict(kind='requests', unit_requests=[], source_requests=[request]))
            refusal = receipt['output_refusal']
            assert (refusal['code'], refusal['check'], refusal['details']['characters']) == (
                'invalid_review_source_request', 'review.shape', 20001)
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            assert protocol.authenticate_attempt(records, artifacts, current, records['review_attempt']['attempt-' + current['id']])
            plan = plan_after_failure(records, artifacts, current['id'])
            assert plan['state'] == 'replace', plan
            replacement = fixture.case.mutate(protocol.record_assignment, plan['payload'])['result']
            packet = fixture.packet(replacement)
            validate_replacement_origin(fixture.case.read_records(), artifacts, replacement, packet)
            assert packet['output_refusal']['refused'][-1]['details']['characters'] == {'sha256': digest(20001)}
            assert replacement['packet_digest'] == '359fc166e5e7178a1bc03fc6340ad124e19602db11a72eed2beb7eb17a16872e'
            assert replacement['prompt_hash'] == '655e866d1fa4e461c20e577ec939d39fd6ea4fa0db35ff19edd7a53540f32a49'

    def test_captured_closing_above_string_limit_records_its_refusal(self):
        notice, _ = check_captured_closing_refusal(4000, 'review_composition_conflict', 'review.shape')
        entry = notice['refused'][-1]
        assert entry['path'] == '/fields/~1value~1reason'
        assert entry['details'] == dict(size='serialized_string', characters=4002, maximum_characters=4000)

    def test_captured_closing_within_string_limit_records_delivery_refusal(self):
        notice, artifact = check_captured_closing_refusal(3998, 'review_evidence_undelivered', 'review.evidence_delivery')
        entry = notice['refused'][-1]
        assert entry['path'] == '/fields/~1value~1evidence'
        assert entry['details']['sha256'] == artifact['sha256']
        assert notice['refused_part']['complete']
        reference, = notice['refused_part']['value']
        assert reference['link']['artifact'] == artifact

    def test_captured_closing_pointer_conflict_records_a_replacement(self):
        with open_closing() as (fixture, current, _):
            answer = fixture.answer(current)
            answer['fields']['/value/evidence/oops'] = fixture.case.supporting
            notice = finish_captured_refusal(fixture.case, current,
                lambda assignment, output: invoke_codex(fixture.case, assignment['id'], output), answer,
                'review_composition_conflict', 'review.shape')
            path = '/fields/~1value~1evidence~1oops'
            assert notice['refused'][-1]['path'] == path
            packet = session.build_composition_packet(fixture.case.artifacts, current, fixture.read_packet(current))
            rule = composition.find_rule(current['role'], packet)
            assert require_size_refusal(composition.compose_output, rule, packet, [], answer)['path'] == path
            # An invalid list component is also refused when the evidence array is supplied only by the neutral view.
            answer['fields'].pop('/value/evidence')
            assert require_size_refusal(composition.validate_closing_output, rule, packet, [], answer)['path'] == path

    def test_captured_parts_v3_closing_empty_request_records_a_replacement(self):
        from test_review_parts_v3_pages import PagedSeriesTests
        fixture = PagedSeriesTests()
        try:
            fixture.setUp()
            current = fixture.reach_page(fixture.start(0))
            while fixture.packet(current)['review_parts'].get('final_form') != 'closing':
                current = fixture.advance(current)
            answer = dict(kind='requests', unit_requests=[], source_requests=[])
            finish_captured_refusal(fixture.case, current, fixture.invoke, answer, 'invalid_value_review', 'review.shape')
        finally:
            fixture.doCleanups()

    def test_captured_closing_oversized_key_records_a_usable_replacement(self):
        with open_closing() as (fixture, current, _):
            answer = fixture.answer(current)
            key = '/' * 3200
            answer['fields']['/value/evidence'] = [{key: 'x'}]
            notice = finish_captured_refusal(fixture.case, current,
                lambda assignment, output: invoke_codex(fixture.case, assignment['id'], output), answer,
                'review_composition_conflict', 'review.shape')
            entry = notice['refused'][-1]
            assert entry['path'] == '/fields/~1value~1evidence/0'
            assert entry['details'] == dict(size='serialized_key', characters=3202, maximum_characters=512,
                                            key_sha256=digest(key))
            assert notice['refused_part']['complete']

            ancestor = '/' * 480
            answer['fields']['/value/evidence'] = [{ancestor: {ancestor: {ancestor: {key: 'x'}}}}]
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            details = require_size_refusal(session.validate_final_turn_output, records, artifacts, current,
                                           fixture.read_packet(current), answer)
            parent = entry['path'] + ('/' + '~1' * 480) * 3
            assert details['path'] == entry['path'] + '/' + '~1' * 480
            assert details['path_sha256'] == digest(parent) and details['key_sha256'] == digest(key)

    def test_captured_closing_with_an_unreadable_link_records_a_replacement(self):
        """Round 5, P1. The evidence check of a closing refuses a link that does not parse; the exact resume records the
        refusal and a replacement."""
        with open_closing() as (fixture, current, _):
            answer = fixture.answer(current)
            answer['fields']['/value/evidence'] = [make_unreadable_link(fixture.case.supporting)]
            notice = finish_captured_refusal(fixture.case, current,
                lambda assignment, output: invoke_codex(fixture.case, assignment['id'], output), answer,
                'invalid_identifier', 'evidence.shape')
            entry = notice['refused'][-1]
            assert (entry['path'], entry['message']) == ('/fields/~1value~1evidence',
                                                         'A supported exact identifier is required'), entry

    def test_long_unknown_closing_pointer_records_a_usable_replacement(self):
        """Round 5, P2. The owner check names the submitted field within the notice bound."""
        with open_closing() as (fixture, current, _):
            answer = fixture.answer(current)
            pointer = '/' + 'u' * 6100
            answer['fields'][pointer] = 'a'
            notice = finish_captured_refusal(fixture.case, current,
                lambda assignment, output: invoke_codex(fixture.case, assignment['id'], output), answer,
                'review_composition_conflict', 'review.shape')
            entry = notice['refused'][-1]
            assert entry['path'] == '/fields'
            assert entry['details'] == dict(path_sha256=dict(sha256=digest(digest('/fields/' + '~1' + 'u' * 6100))))

    def test_closing_turn_request_on_which_its_check_fails_is_refused(self):
        """Round 5, P1. The request branch of a final turn holds the same scope as its page and closing checks."""
        with open_closing() as (fixture, current, _):
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            request = dict(schema_version=1, stage='request', notes='Notes.', excerpts=[], requests=[], settled_requests=[])
            with patch.object(session, 'validate_request_output', side_effect=TypeError('unhashable type')):
                assert require_unreadable_refusal(session.validate_final_turn_output, records, artifacts, current,
                                                  fixture.read_packet(current), request) == {}

    def test_excerpt_of_a_final_turn_is_refused_by_the_rule_of_the_turn_before_its_form(self):
        """S-F6 of the review of the merged tree of 0.93.0. A page and a closing turn take no excerpt. A request answer of
        such a turn whose excerpt also fails its form gets the refusal of the rule of the turn, which removing the excerpt
        answers."""
        with open_closing() as (fixture, current, _):
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            request = dict(schema_version=1, stage='request', notes='Notes.', requests=[], settled_requests=[],
                           excerpts=[dict(unit='u-unknown', text='Not delivered.', purpose='', targets=[])])
            with unittest.TestCase().assertRaises(ResearchError) as caught:
                session.validate_final_turn_output(records, artifacts, current, fixture.read_packet(current), request)
            assert (caught.exception.code, caught.exception.details) == (
                'invalid_review_session_turn', dict(path='/excerpts', items=1, bound=0)), caught.exception.as_dict()

    def test_page_with_an_unreadable_link_is_refused_and_the_closing_records(self):
        """Round 5, P1 case 1. A page whose evidence link does not parse is refused; the chain still closes."""
        with open_session() as fixture:
            current = fixture.start()
            answer = fixture.answer(current)
            path = '/value/objection_findings/0/evidence'
            answer['targets'][0]['fields'][path] = [make_unreadable_link(fixture.case.supporting)]
            invoke_codex(fixture.case, current['id'], answer)
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            refusal = records['review_output_refusal']['attempt-' + current['id']]
            assert (refusal['code'], refusal['check']) == ('invalid_identifier', 'evidence.shape'), refusal
            assert refusal['details']['path'] == '/targets/0/fields/~1value~1objection_findings~10~1evidence'
            assert not protocol.assignment_state(records, artifacts, current['id'])['ready']
            plan = plan_after_failure(records, artifacts, current['id'])
            assert plan['state'] == 'replace', plan
            replacement = fixture.case.mutate(protocol.record_assignment, plan['payload'])['result']
            notice = fixture.read_packet(replacement)['output_refusal']
            assert notice['refused'][-1]['path'] == refusal['details']['path']
            _, _, record = fixture.finish_pages(replacement)
            assert len(record['pages']) > 1

    def test_page_citing_a_delivered_abstract_by_a_source_link_is_refused_at_the_page(self):
        """S-F1 of the review of the merged tree of 0.93.0. The full-text rule of a source link reads the records, so it
        ran only on the composed answer, which then failed at the place of an accepted page three times. The page that
        cites a delivered abstract by a source link is refused at its own place, and the plan replaces the page."""
        with open_session() as fixture:
            version = fixture.add_sources([3000])[0]
            abstract = fixture.case.read_records()['work'][version]['abstracts'][0]
            link = dict(kind='source', link=fixture.case.link(version, abstract['source_id'], abstract['artifact']))

            def answer_request(number, current, packet):
                requests = [dict(id='abstract', kind='source', version_id=version, depth='abstract')] if number == 0 else []
                invoke_codex(fixture.case, current['id'], dict(schema_version=1, stage='request', notes='Notes.',
                                                               excerpts=[], requests=requests, settled_requests=[]))
            current = fixture.start(request_answer=answer_request)
            answer = fixture.answer(current)
            fields = answer['targets'][0]['fields']
            pointer = next(key for key in fields if key.endswith('/evidence'))
            fields[pointer] = [link]
            invoke_codex(fixture.case, current['id'], answer)
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            refusal = records['review_output_refusal']['attempt-' + current['id']]
            assert (refusal['code'], refusal['check']) == ('source_link_not_fulltext', 'evidence.fulltext'), refusal
            assert refusal['details']['path'] == '/targets/0/fields/%s/0' % composition._escape_field(pointer), refusal
            plan = plan_after_failure(records, artifacts, current['id'])
            assert plan['state'] == 'replace', plan

    def test_paged_checks_refuse_an_answer_on_which_they_fail(self):
        """Round 5, P1. Each paged check and composer of an answer refuses where the harness raises inside it."""
        packet = dict(role='result', subject=dict(kind='problem'), problem_evidence={})
        rule = composition.find_rule('result', packet)
        target = composition.ASSURANCES[0]
        # The page check raises after the target and size checks of the page.
        page = dict(targets=[dict(target=target, sentence='Checked.', fields={})])
        with patch.object(composition, '_list_page_entry_ids', side_effect=TypeError('unhashable type')):
            assert require_unreadable_refusal(composition.validate_page_output, rule, packet, [target], page) == {}
        stored = [dict(targets=[target], output=dict(targets=[dict(target=target, sentence='Checked.', fields=[])]))]
        assert require_unreadable_refusal(composition.compose_output, rule, packet, stored, dict(fields={}, bindings=[])) == {}
        block = review_condensing._build_block([dict(handle='one', characters=2)], 2)
        combination = dict(block=block['handle'], summary='Condensed.', refs=['one'])
        require_unreadable_refusal(review_condensing.validate_combination, block, combination, lambda value: int('x'), 100)
        with patch.object(research_decisions, 'validate_review_contract', side_effect=KeyError('value')):
            assert require_unreadable_refusal(session.validate_composite_output, {}, None, {}, {}, {}) == dict(
                composed_review=True)
        failing = dict(validate_final=lambda *args, **kwargs: [][0])
        with patch.object(review_parts_roles, 'get_adapter', return_value=failing):
            assert require_unreadable_refusal(review_parts_v3.validate_composite_output, {}, None, {},
                                              {'review_parts': dict(adapter='study_readiness')}, {}) == dict(composed_output=True)
        with patch.object(openness_reading_v7, 'describe_protocol', return_value=failing):
            assert require_unreadable_refusal(openness_final_pages.validate_composite_output, {}, None, {}, {}, {}) == dict(
                composed_finding=True)
        requests = dict(kind='requests', unit_requests=[],
                        source_requests=[dict(id='query', kind='inventory_query', query='query', page=0)])
        with patch.object(review_parts_v3, '_resolve_requests', side_effect=TypeError('unhashable type')):
            require_unreadable_refusal(validate_parts_final, native_result_packet(), requests)

    def test_page_link_failure_is_refused_and_page_pointers_are_checked(self):
        """Round 5, P1. The page check projects its fields with the closing's checked pointers."""
        packet = dict(role='result', subject=dict(kind='problem'), problem_evidence={})
        rule = composition.find_rule('result', packet)
        target = composition.ASSURANCES[0]
        artifact = describe_artifact(b'A synthetic source.', 'text/plain')
        row = dict(target=target, sentence='Checked.', fields={
            '/value/assurances/0/kind': target, '/value/assurances/0/status': 'passed',
            '/value/assurances/0/reason': 'Checked.', '/value/assurances/0/evidence': [make_unreadable_link(artifact)],
            '/value/objection_findings': [], '/value/work_items': []})
        answer = dict(targets=[row])
        # The page check gives the refusal of the link at the original index of the page's assurance.
        with unittest.TestCase().assertRaises(ResearchError) as caught:
            composition.validate_page_output(rule, packet, [target], answer)
        assert (caught.exception.code, caught.exception.details['path']) == (
            'invalid_identifier', '/targets/0/fields/~1value~1assurances~10~1evidence'), caught.exception.as_dict()
        row['fields']['/value/assurances/0/evidence'] = [dict(kind='record', record_kind='problem_statement', id='statement-1')]
        row['fields']['/value/assurances/oops/reason'] = row['fields'].pop('/value/assurances/0/reason')
        # The exact pointers of page.fields refuse the unknown list key before the projection.
        assert require_size_refusal(composition.validate_page_output, rule, packet, [target], answer) == dict(
            path='/targets/0/fields', missing_fields=['/value/assurances/0/reason'],
            unexpected_fields=['/value/assurances/oops/reason'])

    def test_unreadable_page_refusal_tells_the_operator_where_the_harness_failed(self):
        """Repairs A7. The record of the refusal of a page on which a check of the harness raises keeps the class of the
        exception and the function where it rose; the plan shows them, and the notice of the replacement does not."""
        # The check fails on every reading of the answer, as a defect of the harness does until it is repaired.
        with open_session() as fixture, patch.object(composition, '_check_serialized_size', raise_unhashable):
            current = fixture.start()
            answer = fixture.answer(current)
            invoke_codex(fixture.case, current['id'], answer)
            records, artifacts = fixture.case.read_records(), fixture.case.artifacts
            refusal = records['review_output_refusal']['attempt-' + current['id']]
            expected = {'class': 'TypeError', 'function': raise_unhashable.__module__ + '.raise_unhashable'}
            assert (refusal['code'], refusal['check']) == ('review_composition_conflict', 'review.shape'), refusal
            assert refusal['details']['harness_exception'] == expected, refusal
            plan = plan_after_failure(records, artifacts, current['id'])
            assert plan['state'] == 'replace', plan
            assert plan['details']['refused_attempts'][-1]['harness_exception'] == expected, plan['details']
            replacement = fixture.case.mutate(protocol.record_assignment, plan['payload'])['result']
            entry = fixture.read_packet(replacement)['output_refusal']['refused'][-1]
            assert entry['code'] == 'review_composition_conflict' and 'harness_exception' not in entry['details'], entry

    def test_page_pointer_with_an_unknown_list_key_is_refused_at_its_page(self):
        """Case 2 of the round-4 review."""
        def mutate(fields):
            fields['/value/objection_findings/oops/id'] = fields.pop('/value/objection_findings/0/id')
        check_page_refused(mutate)

    def test_page_pointer_below_a_scalar_is_refused_at_its_page(self):
        """Case 3 of the round-4 review."""
        def mutate(fields):
            fields['/value/objection_findings/0/reason/x'] = fields.pop('/value/objection_findings/0/reason')
        check_page_refused(mutate)

    def test_released_v7_finding_keeps_native_ground_diagnosis(self):
        packet = build_finding_packet('blocked')
        packet['reading_final'] = dict(protocol=openness_reading_v7.PROTOCOL, page=dict(targets=['t0']))
        output = build_finding_answer(packet, 'unresolved', [dict(kind='own_argument', text='g' * 601)])
        with unittest.TestCase().assertRaises(ResearchError) as caught:
            validate_v7_page(packet, output)
        error = caught.exception
        assert (error.code, error.contract_check) == ('openness_finding_invalid', 'openness.finding')
        assert error.details == dict(path='/findings/openness/grounds/0/text', rule='ground_bound',
                                     forecast=packet['native']['scientific_context']['audit_forecast'])
