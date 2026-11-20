"""A retry receives bounded diagnostics about its own refused output only."""

import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import review_refusal as refusal
from research_harness.errors import ResearchError, describe_error_at
from research_harness.evidence import digest
from test_part_locator_refusal import RefusalCase


def serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class WireNoticeTests(unittest.TestCase):
    def build_refused_output(self, shape, characters):
        patterns = dict(quotes='"', backslashes='\\', newlines='\n', json='{"answer": ["quoted", "\\\\", 1]}\n')
        pattern = patterns[shape]
        room = characters - len(serialize(dict(value='')))
        repeats = room // (len(serialize(pattern)) - 2)
        output = dict(value=pattern * repeats)
        output['value'] += 'x' * (characters - len(serialize(output)))
        self.assertEqual(len(serialize(output)), characters)
        return output

    def build_records(self, output):
        from test_openness_reading_v7_turns import MemoryArtifacts
        artifacts = MemoryArtifacts()
        reference = artifacts.put(serialize(output).encode('utf-8'))
        certificate = dict(id='attempt', assignment_id='final', attempt_id='attempt', output_digest=digest(output),
            request_digest='a' * 64, protocol=refusal.PROTOCOL, code='openness_finding_invalid',
            check='openness.finding', details=dict(path='/value', message='Return the finding contract.'),
            recorded_revision=2)
        records = dict(review_attempt=dict(attempt=dict(id='attempt', assignment_id='final', output=reference,
            recorded_revision=1)), review_output_refusal=dict(attempt=certificate))
        diagnosis = dict(code=certificate['code'], check=certificate['check'], details=certificate['details'])
        return records, artifacts, dict(attempt=diagnosis)

    def test_t5_bounded_notices_obey_route_wire_bounds_for_escaped_outputs(self):
        from research_harness import review_refusal_notice as notices, review_route_capacity as capacity
        from v7_capacity_fixtures import build_route
        for adapter, model in (('codex_cli_v1', 'gpt-6-astra'), ('claude_cli_v1', 'claude-opus-5-5')):
            route = build_route(adapter, model)
            def measure(value):
                return capacity.measure_capacity_value(route, value)
            for shape in ('quotes', 'backslashes', 'newlines', 'json'):
                for characters in (10000, 100000, 1000000, 5000000):
                    with self.subTest(adapter=adapter, shape=shape, characters=characters):
                        records, artifacts, diagnoses = self.build_records(self.build_refused_output(shape, characters))
                        diagnoses['attempt']['details']['rule'] = dict(expected='"\\\n' * 10000)
                        notice = notices.build_refusal_notice(records, artifacts, ('attempt',), form='bounded',
                            diagnoses=diagnoses, measure=measure, enforce_bounded_limit=True)
                        self.assertLessEqual(measure(notice), 45000)
                        self.assertLessEqual(measure(notice['refused_part']), 20000)
                        self.assertTrue(all(measure(row) <= 6000 for row in notice['refused']))

    def test_t5_notices_without_a_plan_keep_the_base_serialized_bytes(self):
        from research_harness import review_refusal_notice as notices
        baseline = json.loads((Path(__file__).parent / 'fixtures' / 'refusal-notice-no-plan-2f9507fd.json').read_text())
        for shape in ('quotes', 'backslashes', 'newlines', 'json'):
            for characters in (10000, 100000, 1000000, 5000000):
                records, artifacts, diagnoses = self.build_records(self.build_refused_output(shape, characters))
                for form in ('whole', 'bounded'):
                    with self.subTest(shape=shape, characters=characters, form=form):
                        notice = notices.build_refusal_notice(records, artifacts, ('attempt',), form=form, diagnoses=diagnoses)
                        actual = hashlib.sha256(notices._serialize_value(notice).encode('utf-8')).hexdigest()
                        self.assertEqual(actual, baseline['notices']['%s-%d' % (shape, characters)][form])

    def test_t5_no_plan_keeps_base_acceptance_and_errors_without_changing_records(self):
        from research_harness import review_refusal_notice as notices
        baseline = json.loads((Path(__file__).parent / 'fixtures' / 'refusal-notice-no-plan-2f9507fd.json').read_text())
        for case, expected in baseline['behavior'].items():
            with self.subTest(case=case):
                records, artifacts, diagnoses = self.build_records(dict(value='short'))
                if case == 'bounded_descriptor_above_45000':
                    records['review_attempt']['attempt']['output']['path'] += 'x' * 45000
                else:
                    records['review_output_refusal']['attempt']['assignment_id'] = 'x' * 7000
                before = serialize(records).encode('utf-8')
                old_digest = digest(records)
                bodies = copy.deepcopy(artifacts.bodies)
                if expected['accepted']:
                    notice = notices.build_refusal_notice(records, artifacts, ('attempt',), form='bounded', diagnoses=diagnoses)
                    actual = notices._serialize_value(notice).encode('utf-8')
                    self.assertGreater(len(actual), 45000)
                    self.assertEqual(hashlib.sha256(actual).hexdigest(), expected['sha256'])
                else:
                    with self.assertRaises(ResearchError) as caught:
                        notices.build_refusal_notice(records, artifacts, ('attempt',), form='bounded', diagnoses=diagnoses)
                    self.assertEqual(caught.exception.as_dict(), expected['error'])
                self.assertEqual(serialize(records).encode('utf-8'), before)
                self.assertEqual(digest(records), old_digest)
                self.assertEqual(artifacts.bodies, bodies)

    def test_t5_wire_entry_refuses_fixed_metadata_that_only_fits_before_escaping(self):
        from research_harness import review_refusal_notice as notices, review_route_capacity as capacity
        from v7_capacity_fixtures import build_route
        records, artifacts, diagnoses = self.build_records(dict(value='short'))
        diagnoses['attempt']['details']['message'] = '"' * 1800
        self.assertTrue(notices.build_refusal_notice(records, artifacts, ('attempt',), diagnoses=diagnoses))
        before = serialize(records).encode('utf-8')
        old_digest = digest(records)
        bodies = copy.deepcopy(artifacts.bodies)
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        with self.assertRaises(ResearchError) as caught:
            notices.build_refusal_notice(records, artifacts, ('attempt',), diagnoses=diagnoses,
                measure=lambda value: capacity.measure_capacity_value(route, value), enforce_bounded_limit=True)
        self.assertEqual(caught.exception.code, 'review_refusal_notice_metadata_too_large')
        self.assertEqual(caught.exception.details['limit_characters'], 6000)
        self.assertEqual(serialize(records).encode('utf-8'), before)
        self.assertEqual(digest(records), old_digest)
        self.assertEqual(artifacts.bodies, bodies)

    def test_t5_selection_applies_the_total_limit_only_with_a_capacity_plan(self):
        from research_harness import review_refusal_notice as notices, review_route_capacity as capacity
        from test_openness_reading_v7_turns import TurnPacketTests
        records, artifacts, diagnoses = self.build_records(dict(value='short'))
        records['review_attempt']['attempt']['output']['path'] += 'x' * 45000
        fixture = TurnPacketTests()
        fixture.setUp()
        packet, chain = fixture.prepare()
        artifacts.bodies.update(fixture.artifacts.bodies)
        records['review_route'] = dict(route=fixture.route)
        plan = capacity.read_chain_capacity_plan(artifacts, chain)
        before = serialize(records).encode('utf-8')
        old_digest = digest(records)
        bodies = copy.deepcopy(artifacts.bodies)
        options = dict(route=fixture.route, role='native_math', prompt='Return JSON.', packet=packet,
                       forms=('bounded',), diagnoses=diagnoses, turn_kind='reading')
        legacy = notices.select_refusal_notice(records, artifacts, ('attempt',), **options)
        self.assertIsNotNone(legacy['notice'])
        self.assertGreater(len(notices._serialize_value(legacy['notice'])), 45000)
        baseline = json.loads((Path(__file__).parent / 'fixtures' / 'refusal-notice-no-plan-2f9507fd.json').read_text())
        self.assertEqual(hashlib.sha256(notices._serialize_value(legacy['notice']).encode('utf-8')).hexdigest(),
                         baseline['behavior']['bounded_descriptor_above_45000']['sha256'])
        with self.assertRaises(ResearchError) as caught:
            notices.select_refusal_notice(records, artifacts, ('attempt',), capacity_plan=plan, **options)
        self.assertEqual(caught.exception.code, 'review_refusal_notice_metadata_too_large')
        self.assertEqual(caught.exception.details['limit_characters'], 45000)
        self.assertEqual(serialize(records).encode('utf-8'), before)
        self.assertEqual(digest(records), old_digest)
        self.assertEqual(artifacts.bodies, bodies)

    def test_t5_bounded_notice_refuses_fixed_metadata_over_the_total_wire_bound(self):
        from research_harness import review_refusal_notice as notices, review_route_capacity as capacity
        from v7_capacity_fixtures import build_route
        records, artifacts, diagnoses = self.build_records(dict(value='short'))
        records['review_attempt']['attempt']['output']['path'] += 'x' * 45000
        before = serialize(records).encode('utf-8')
        old_digest = digest(records)
        bodies = copy.deepcopy(artifacts.bodies)
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        with self.assertRaises(ResearchError) as caught:
            notices.build_refusal_notice(records, artifacts, ('attempt',), form='bounded', diagnoses=diagnoses,
                measure=lambda value: capacity.measure_capacity_value(route, value), enforce_bounded_limit=True)
        self.assertEqual(caught.exception.code, 'review_refusal_notice_metadata_too_large')
        self.assertEqual(caught.exception.details['limit_characters'], 45000)
        self.assertEqual(serialize(records).encode('utf-8'), before)
        self.assertEqual(digest(records), old_digest)
        self.assertEqual(artifacts.bodies, bodies)

    def test_t6_registered_refusal_paragraphs_and_member_key_fit_the_stated_bound(self):
        from research_harness import review_prompt_paragraphs as paragraphs, review_refusal_notice as notices
        from research_harness import review_route_capacity as capacity, review_transport as transport
        from v7_capacity_fixtures import build_route
        for adapter, model in (('codex_cli_v1', 'gpt-6-astra'), ('claude_cli_v1', 'claude-opus-5-5')):
            route = build_route(adapter, model)
            empty = transport.request_body(route, '', {})
            base = capacity.measure_request(route, empty)['text_characters']
            for paragraph in paragraphs.PARAGRAPHS:
                if not paragraph.name.startswith('output-refusal-'):
                    continue
                for version, text in paragraph.versions:
                    with self.subTest(adapter=adapter, version=version):
                        request = transport.request_body(route, text, dict(output_refusal=None))
                        characters = capacity.measure_request(route, request)['text_characters'] - base - 4
                        self.assertLessEqual(characters, notices.NOTICE_PARAGRAPH_CHARACTERS)


class RefusalNoticeTests(RefusalCase):
    def setUp(self):
        super().setUp()
        from research_harness import review_refusal_notice
        self.notices = review_refusal_notice
        self.output = {'value': {'evidence': [self.evidence], 'reason': 'Synthetic independent finding.'}}
        self.records = {'review_attempt': {}, 'review_output_refusal': {},
                        'review_assignment': {'other': {'reviewer_id': 'OTHER-REVIEWER-ASSIGNMENT'}},
                        'value_review': {'other': {'reason': 'OTHER-REVIEWER-JUDGMENT'}}}
        other = self.artifacts.put(serialize({'reason': 'OTHER-REVIEWER-OUTPUT'}).encode(), 'application/json')
        self.records['review_attempt']['other'] = {'id': 'other', 'assignment_id': 'other', 'output': other}
        self.add_refusal('first', self.output)

    def add_refusal(self, identifier, output, *, check='evidence.locator', details=None, revision=1):
        reference = self.artifacts.put(serialize(output).encode(), 'application/json')
        self.records['review_attempt'][identifier] = dict(id=identifier, assignment_id='bar-final',
            status='completed', output=reference, request_digest='a' * 64, recorded_revision=revision)
        if details is None:
            with self.assertRaises(ResearchError) as caught, describe_error_at('/value/evidence'):
                refusal.validate_evidence_contract(self.records, self.artifacts, [self.evidence], packets=(self.packet,))
            error = caught.exception
            details = dict(error.details, message=error.message)
        self.records['review_output_refusal'][identifier] = dict(id=identifier, assignment_id='bar-final',
            attempt_id=identifier, output_digest=digest(output), request_digest='a' * 64,
            protocol=refusal.PROTOCOL, code='invalid_locator', check=check, details=details,
            recorded_revision=revision + 1)

    def read_diagnoses(self, records=None):
        records = self.records if records is None else records
        return {key: {name: certificate[name] for name in ('code', 'check', 'details')}
                for key, certificate in records['review_output_refusal'].items()}

    def select_notice(self, records, artifacts, attempt_ids, **options):
        return self.notices.select_refusal_notice(records, artifacts, attempt_ids,
            diagnoses=self.read_diagnoses(records), **options)

    def build_notice(self, form='whole', attempt_ids=('first',)):
        return self.notices.build_refusal_notice(self.records, self.artifacts, attempt_ids,
            form=form, diagnoses=self.read_diagnoses())

    def build_verified_route(self, adapter='openai_responses_v1'):
        from capacity_evidence_fixtures import build_capacity_runtime
        from research_harness import review_route_capacity as capacity
        route = dict(id='route', adapter=adapter, model='fixture-model',
            runtime=build_capacity_runtime(adapter),
            endpoint='codex://local' if adapter.startswith('codex_') else 'https://api.openai.com/v1/responses',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5))
        self.records['review_route'] = {route['id']: route}
        self.records['review_route_probe'] = dict(probe=dict(id='probe', route_id=route['id'],
            capacity=dict(input_tokens=2000000),
            capacity_evidence=dict(key=capacity.describe_capacity_key(route), input_tokens=2000000)))
        return route

    def test_part_offset_notice_preserves_expected_offsets_and_every_stored_digest(self):
        before = copy.deepcopy(self.records)
        frozen = {kind: digest(value) for kind, value in before.items()}
        stored = self.artifacts.read(before['review_attempt']['first']['output'])
        notice = self.build_notice()
        self.assertEqual(notice['protocol'], 'exactory-review-output-refusal-notice-v1')
        self.assertEqual(notice['form'], 'whole')
        self.assertEqual(notice['output'], self.output)
        row = notice['refused'][0]
        self.assertIn('start 0 and end 38629', row['message'])
        self.assertEqual(row['path'], '/value/evidence')
        self.assertEqual(row['details']['part_relative_span'], {'start': 0, 'end': 38629})
        self.assertEqual(row['refusal_digest'], digest(before['review_output_refusal']['first']))
        self.assertEqual(row['output_digest'], digest(self.output))
        self.assertEqual(self.records, before)
        self.assertEqual({kind: digest(value) for kind, value in self.records.items()}, frozen)
        self.assertEqual(self.artifacts.read(before['review_attempt']['first']['output']), stored)
        notice['output']['value']['reason'] = 'Mutated by a caller.'
        self.assertEqual(self.build_notice()['output'], self.output)
        # Admission supplies a fresh diagnosis, even when the stored certificate predates W5.
        legacy_records = copy.deepcopy(self.records)
        legacy = legacy_records['review_output_refusal']['first']
        legacy['details'] = {'message': 'The span must lie within the saved text', 'path': '/value/evidence'}
        legacy_before = copy.deepcopy(legacy_records)
        for form in ('whole', 'bounded'):
            saved = self.notices.build_refusal_notice(legacy_records, self.artifacts, ('first',),
                form=form, diagnoses=self.read_diagnoses())
            self.assertIn('start 0 and end 38629', saved['refused'][0]['message'])
            self.assertEqual(saved['refused'][0]['details']['part_relative_span'], {'start': 0, 'end': 38629})
            self.assertEqual(saved['refused'][0]['refusal_digest'], digest(legacy))
        self.assertEqual(legacy_records, legacy_before)

    def test_revived_entry_keeps_the_same_fixed_metadata_bound(self):
        self.records['review_output_refusal']['first']['assignment_id'] = 'legacy-' + 'x' * 7000
        self.add_refusal('latest', self.output, revision=3)
        diagnoses = self.read_diagnoses()
        diagnoses['first'] = None
        with self.assertRaises(ResearchError) as caught:
            self.notices.build_refusal_notice(self.records, self.artifacts, ('first', 'latest'),
                form='bounded', diagnoses=diagnoses)
        self.assertEqual(caught.exception.code, 'review_refusal_notice_metadata_too_large')


    def test_refusals_are_ordered_by_recorded_revision_and_latest_output_is_shown(self):
        latest = dict(self.output, latest=True)
        self.add_refusal('second', latest, revision=3)
        notice = self.build_notice(attempt_ids=('second', 'first'))
        self.assertEqual([row['attempt_id'] for row in notice['refused']], ['first', 'second'])
        self.assertEqual(notice['output'], latest)

    def test_each_registered_check_withholds_unlisted_details_in_both_forms(self):
        for check in refusal.CONTRACT_CHECKS:
            with self.subTest(check=check):
                owned = {field: 'Own output or frozen packet ' + field
                         for field in refusal.CONTRACT_CHECKS[check]['notice_fields']}
                details = dict(owned, message='Correct the output shape.', path='/value',
                               other_review=self.records['value_review']['other'],
                               other_assignment=self.records['review_assignment']['other'],
                               other_output={'reason': 'OTHER-REVIEWER-OUTPUT'})
                self.add_refusal('first', self.output, check=check, details=details)
                for form in ('whole', 'bounded'):
                    notice = self.build_notice(form)
                    text = serialize(notice)
                    self.assertNotIn('OTHER-REVIEWER', text)
                    self.assertEqual(notice['refused'][0]['details']['other_review'],
                                     {'sha256': digest(details['other_review'])})
                    self.assertEqual(notice['refused'][0]['check'], check)
                    for field, value in owned.items():
                        self.assertEqual(notice['refused'][0]['details'][field], value)

    def test_every_registered_validator_produces_a_notice_without_the_other_reviewers_markers(self):
        from research_harness import native_review, research_decisions as decisions
        from test_openness_finding_answer import build_packet
        from research_harness import review_parts_study, review_protocol as protocol, review_staged
        from test_research_problem_reviews import ProblemReviewTests
        from test_review_composition import make_fixture
        from test_review_staged_fixtures import epoch, task

        case = ProblemReviewTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.supporting = case.artifacts.put(b'An exact synthetic field standard.', 'text/plain')
        case.prepare_review_route()
        case.observe('bar', 'first')
        other = case.build_review_output('bar')
        other['value']['reason'] = 'OTHER-REVIEWER-OUTPUT'
        case.observe('bar', 'OTHER-REVIEWER-ASSIGNMENT', output=other, identifier='OTHER-REVIEWER-RECORD')
        artifacts = case.artifacts
        artifacts.put(self.artifacts.read(self.evidence['link']['artifact']),
                      self.evidence['link']['artifact']['media_type'])
        base = case.read_records()
        self.assertEqual(base['review_assignment']['OTHER-REVIEWER-RECORD']['reviewer_id'],
                         'OTHER-REVIEWER-ASSIGNMENT')
        self.assertIn('OTHER-REVIEWER-OUTPUT', artifacts.read(
            base['review_attempt']['attempt-OTHER-REVIEWER-RECORD']['output']).decode('utf-8'))
        self.assertEqual(base['value_review']['review-OTHER-REVIEWER-RECORD']['payload']['value']['reason'],
                         'OTHER-REVIEWER-OUTPUT')
        assignment = base['review_assignment']['bar-first']
        ordinary = case.build_review_output('bar')
        unseen = artifacts.put(b'An exact citation not delivered to this reviewer.', 'text/plain')

        focused_packet = {'protocol': protocol.PROTOCOL, 'role': 'adjudicator',
                          'subject': {'kind': 'problem'}, 'objection': {'id': 'own-objection'},
                          'evidence': [], 'source_deliveries': []}
        focused_packet['digest'] = digest(focused_packet)
        focused = dict(assignment, id='own-focused', role='adjudicator',
                       packet=protocol._put_json(artifacts, focused_packet), packet_digest=focused_packet['digest'])
        base['review_assignment'][focused['id']] = focused
        focused_output = {'objection_id': 'own-objection', 'disposition': 'upheld',
                          'reason': 'The delivered statement has a gap.', 'evidence': [unseen],
                          'correction_admissible': None}

        plan, pages, _, _ = make_fixture()
        staged_epoch = epoch(id=plan['epoch_id'])
        ownership = [row for row in plan['ownership'] if row['page_id'] == pages[0]['output']['page_id']]
        assigned = task(staged_epoch, 'answer', answer_ranges=ownership, composition_role=plan['role'],
                        composition_plan_root=digest(plan))
        paged = dict(assignment, composition_plan_id=digest(plan))
        base['review_composition_plan'] = {digest(plan): plan}

        def check_ordinary(records, output):
            decisions.validate_review_contract(records, artifacts, assignment, output)

        def check_focused(records, output):
            protocol.validate_adjudication_contract(records, artifacts, focused, focused_packet, output)

        abstract_link = next(iter(case.supporting_links.values()))
        cases = {
            'evidence.fulltext': (check_ordinary, dict(ordinary, value=dict(ordinary['value'],
                evidence=[dict(kind='source', link=abstract_link)]))),
            'evidence.shape': (check_ordinary, dict(ordinary, value=dict(ordinary['value'], evidence=[{}]))),
            'evidence.locator': (check_ordinary,
                                 dict(ordinary, value=dict(ordinary['value'], evidence=[self.evidence]))),
            'review.shape': (check_ordinary, {}),
            'review.value': (check_ordinary, dict(ordinary, value={})),
            'review.objections': (check_ordinary, dict(ordinary, objections=[{}])),
            'review.reassessment': (check_ordinary, dict(ordinary, reassessment={})),
            'review.support': (check_ordinary, dict(ordinary, support={})),
            'review.source_requests': (check_ordinary, dict(ordinary, source_requests=[{}])),
            'review.evidence_delivery': (check_ordinary,
                                         dict(ordinary, value=dict(ordinary['value'], evidence=[unseen]))),
            'review.staged_final': (lambda records, output: refusal._project_staged_final('result', output),
                                    {'value': {}}),
            'review.staged_task': (lambda records, output: review_staged.validate_task_output(
                task(staged_epoch, 'frame'), output), {}),
            'review.staged_page': (lambda records, output: refusal._validate_staged_pages(
                records, paged, staged_epoch, assigned, output),
                {'pages': [dict(pages[0]['output'], task_id='another-answer-task')]}),
            'adjudication.shape': (check_focused, {}),
            'adjudication.reassessment': (check_focused, dict(focused_output, reassessment={})),
            'adjudication.evidence_delivery': (check_focused, focused_output),
            'verification.parts': (lambda records, output: review_parts_study.validate_verification_final(
                records, artifacts, dict(assignment, role='verification'), output), {}),
            'openness.finding': (lambda records, output: native_review.validate_finding(build_packet(), output), {}),
            'native.output': (lambda records, output: native_review.validate_output({'kind': 'result'}, output),
                              {'decision': 'reject', 'reason': ''}),
        }
        self.assertEqual(set(cases), set(refusal.CONTRACT_CHECKS))
        for check, (validate, output) in cases.items():
            with self.subTest(check=check):
                records = copy.deepcopy(base)
                with self.assertRaises(ResearchError) as caught, refusal.contract_check(check):
                    validate(records, output)
                error = caught.exception
                self.assertEqual(error.contract_check, check)
                attempt_id = 'guard-attempt'
                records['review_attempt'][attempt_id] = dict(id=attempt_id, assignment_id=assignment['id'],
                    status='completed', output=protocol._put_json(artifacts, output), recorded_revision=1000)
                records['review_output_refusal'] = {attempt_id: dict(id=attempt_id, assignment_id=assignment['id'],
                    attempt_id=attempt_id, output_digest=digest(output), request_digest='a' * 64,
                    protocol=refusal.PROTOCOL, code=error.code, check=error.contract_check,
                    details=dict(error.details or {}, message=error.message), recorded_revision=1001)}
                for form in ('whole', 'bounded'):
                    notice = self.notices.build_refusal_notice(records, artifacts, (attempt_id,), form=form,
                        diagnoses={attempt_id: dict(code=error.code, check=check, details=dict(error.details or {}, message=error.message))})
                    self.assertNotIn('OTHER-REVIEWER-', serialize(notice))
                    self.assertEqual(notice['refused'][0]['code'], error.code)
                    if check == 'evidence.fulltext':
                        self.assertEqual(notice['refused'][0]['details']['artifact'], abstract_link['artifact'])
                        self.assertEqual(notice['refused'][0]['details']['delivered_depth'], 'passage')
                        self.assertEqual(notice['refused'][0]['details']['notice'],
                                         refusal.SOURCE_LINK_ACCEPTED_FORMS_NOTICE)
                    if check == 'evidence.shape':
                        # A problem review binds its record references, so its notice is the bound form.
                        self.assertEqual(notice['refused'][0]['details'], dict(refused_indexes=[0], refused_count=1,
                                         notice=refusal.REVIEW_BOUND_EVIDENCE_FORMS_NOTICE))

    def test_bounded_notice_limits_serialized_entries_details_and_part_for_large_outputs(self):
        for characters in (10_000, 2_000_000, 5_000_000):
            for character in ('x', '"', '\n', '\\', 'α'):
                with self.subTest(characters=characters, character=repr(character)):
                    output = {'value': {'reason': character * characters}}
                    for index in range(4):
                        self.add_refusal(str(index), output, details={'message': 'Repair the field.',
                            'path': '/value/reason', 'part_relative_span': {'start': 0, 'end': 38629},
                            'opaque': character * 10_000}, revision=2 * index)
                    notice = self.build_notice('bounded', attempt_ids=('3', '1', '0', '2'))
                    self.assertLessEqual(len(serialize(notice)), 45_000)
                    for row in notice['refused']:
                        self.assertLessEqual(len(serialize(row)), 6_000)
                    part = notice['refused_part']
                    self.assertEqual(part['path'], '/value/reason')
                    if characters >= 20_000 or character in ('"', '\n', '\\'):
                        self.assertFalse(part['complete'])
                        self.assertEqual(part['value']['characters'], len(serialize(output['value']['reason'])))
                        self.assertEqual(part['value']['sha256'], hashlib.sha256(
                            serialize(output['value']['reason']).encode()).hexdigest())
                    else:
                        self.assertTrue(part['complete'])
                        self.assertEqual(part['value'], output['value']['reason'])
                    self.assertEqual(notice['output_ref']['artifact'], self.records['review_attempt']['3']['output'])
                    self.assertEqual(notice['output_ref']['characters'], len(serialize(output)))

    def test_large_allowed_details_and_messages_obey_the_entry_bound(self):
        output = {'value': {'reason': 'x' * 2_000_000}}
        for index in range(4):
            self.add_refusal(str(index), output, details={'message': 'x' * 3000, 'path': '/value',
                'part_relative_span': {'start': 'x' * 10_000, 'end': 38629}}, revision=2 * index)
        notice = self.build_notice('bounded', attempt_ids=('0', '1', '2', '3'))
        for row in notice['refused']:
            self.assertLessEqual(len(serialize(row)), 6_000)
            self.assertEqual(row['message'], 'x' * 3000)
            self.assertFalse(row['details']['complete'])
            self.assertEqual(row['details']['characters'], len(serialize(
                {'part_relative_span': {'start': 'x' * 10_000, 'end': 38629}})))
        self.assertLessEqual(len(serialize(notice)), 45_000)

    def test_unbounded_legacy_identity_or_diagnostic_metadata_is_reported_without_changing_types(self):
        from research_harness.stop_classes import classify_stop
        original = copy.deepcopy(self.records)
        for field in ('assignment_id', 'attempt_id', 'message', 'path'):
            with self.subTest(field=field):
                self.records = copy.deepcopy(original)
                certificate = self.records['review_output_refusal']['first']
                target = certificate['details'] if field in ('message', 'path') else certificate
                target[field] = 'x' * 7000
                attempt_ids = ('first',)
                if field == 'attempt_id':
                    attempt = self.records['review_attempt'].pop('first')
                    attempt['id'] = certificate['attempt_id']
                    self.records['review_attempt'][attempt['id']] = attempt
                    certificate['id'] = attempt['id']
                    self.records['review_output_refusal'] = {attempt['id']: certificate}
                    attempt_ids = (attempt['id'],)
                if field == 'assignment_id':
                    self.records['review_attempt']['first']['assignment_id'] = certificate['assignment_id']
                before = digest(self.records)
                for form in ('whole', 'bounded'):
                    with self.assertRaises(ResearchError) as caught:
                        self.build_notice(form, attempt_ids)
                    self.assertEqual(caught.exception.code, 'review_refusal_notice_metadata_too_large')
                    self.assertEqual(classify_stop(caught.exception.code), 'decision')
                    self.assertGreater(caught.exception.details['characters'], 6000)
                    self.assertEqual(caught.exception.details['limit_characters'], 6000)
                self.assertEqual(digest(self.records), before)
                with self.assertRaises(ResearchError) as caught:
                    self.select_notice(self.records, self.artifacts, attempt_ids, route={},
                        role='bar', prompt='Frozen reviewer prompt.', packet={})
                self.assertEqual(caught.exception.code, 'review_refusal_notice_metadata_too_large')

    def test_bounded_part_resolves_json_pointer_arrays_escapes_root_and_missing_fields(self):
        output = {'a/b': [{'~key': 'Exact short value.'}], 'null': None}
        for path, expected, complete in (('/a~1b/0/~0key', 'Exact short value.', True),
                ('', output, True), ('/absent', None, False), ('/null', None, True),
                ('/a~1b/4', None, False), ('/a~1b/-1', None, False), ('/a~1b/01', None, False)):
            with self.subTest(path=path):
                self.add_refusal('first', output, details={'message': 'Repair this field.', 'path': path})
                part = self.build_notice('bounded')['refused_part']
                self.assertEqual(part, {'path': path, 'value': expected, 'complete': complete})

    def test_unregistered_check_and_invalid_form_or_attempt_count_have_no_notice(self):
        with self.assertRaises(KeyError):
            with refusal.contract_check('unregistered.output'):
                pass
        self.records['review_output_refusal']['first']['check'] = 'unregistered.output'
        with self.assertRaises(KeyError):
            self.build_notice()
        for form, ids in (('other', ('first',)), ('whole', ()), ('whole', ('first', 'first')),
                          ('bounded', ('one', 'two', 'three', 'four', 'five'))):
            with self.subTest(form=form, ids=ids), self.assertRaises(ValueError):
                self.build_notice(form, ids)

    def test_form_selection_counts_notice_in_task_and_reports_both_failed_trials(self):
        route = self.build_verified_route()
        self.output['value']['reason'] = 'x' * 100_000
        self.add_refusal('first', self.output)
        packet = {'role': 'bar'}
        def select(**options):
            return self.select_notice(self.records, self.artifacts, ('first',), route=route,
                role='bar', prompt='Frozen reviewer prompt.', packet=packet, task_characters=1000, **options)
        whole = select()
        self.assertEqual(whole['notice']['form'], 'whole')
        self.assertEqual(len(whole['trials']), 1)
        trial = whole['trials'][0]
        self.assertEqual(trial['task']['notice_characters'], len(serialize(whole['notice'])))
        self.assertEqual(trial['task']['characters'], 1000 + trial['task']['notice_characters'])
        self.assertEqual(select(task_limit_characters=trial['task']['characters'])['notice']['form'], 'whole')
        self.assertEqual(select(task_limit_characters=trial['task']['characters'] - 1)['notice']['form'], 'bounded')
        bounded = select(task_limit_characters=50_000)
        self.assertEqual(bounded['notice']['form'], 'bounded')
        self.assertEqual(len(bounded['trials']), 2)
        self.assertLessEqual(bounded['trials'][-1]['task']['characters'], 50_000)
        failed = select(task_limit_characters=1000)
        self.assertIsNone(failed['notice'])
        self.assertEqual([row['form'] for row in failed['trials']], ['whole', 'bounded'])
        self.assertTrue(all(not row['fits'] for row in failed['trials']))
        self.assertEqual(packet, {'role': 'bar'})

    def test_form_selection_uses_only_the_routes_keyed_capacity_witness(self):
        route = self.build_verified_route('codex_cli_v1')
        before = copy.deepcopy(self.records)
        selected = self.select_notice(self.records, self.artifacts, ('first',), route=route,
            role='bar', prompt='Frozen reviewer prompt.', packet={'role': 'bar'})
        self.assertEqual(selected['notice']['form'], 'whole')
        self.assertEqual(selected['trials'][0]['request_measure']['token_limit'], 1999000)
        changed = dict(route, runtime=dict(route['runtime'], version='fixture-cli-updated'))
        refused = self.select_notice(self.records, self.artifacts, ('first',), route=changed,
            role='bar', prompt='Frozen reviewer prompt.', packet={'role': 'bar'})
        self.assertIsNone(refused['notice'])
        self.assertTrue(all(trial['request_measure']['token_limit'] == 0 for trial in refused['trials']))
        self.assertEqual(self.records, before)

    def test_preflight_selects_bounded_form_and_does_not_hide_unrelated_errors(self):
        from research_harness import review_preflight
        from research_harness import review_transport as transport
        route = self.build_verified_route()
        self.output['value']['reason'] = 'x' * 100_000
        self.add_refusal('first', self.output)
        def select():
            return self.select_notice(self.records, self.artifacts, ('first',), route=route,
                role='bar', prompt='Frozen reviewer prompt.', packet={'role': 'bar'})
        with patch.object(transport, 'local_input_limit', return_value=50_000):
            selected = select()
        self.assertEqual(selected['notice']['form'], 'bounded')
        self.assertEqual(selected['trials'][0]['error']['code'], 'review_input_too_large')
        with patch.object(transport, 'local_input_limit', return_value=1):
            failed = select()
        self.assertIsNone(failed['notice'])
        self.assertEqual(len(failed['trials']), 2)
        with patch.object(review_preflight, 'check_request', side_effect=ResearchError('unavailable', 'Missing input.')):
            with self.assertRaises(ResearchError) as caught:
                select()
        self.assertEqual(caught.exception.code, 'unavailable')

    def test_each_form_measures_its_recorded_prompt_and_non_notice_task(self):
        from research_harness import review_transport as transport
        route = self.build_verified_route()
        self.output['value']['reason'] = 'x' * 100_000
        self.add_refusal('first', self.output)
        texts = {'whole': 'Whole retry placeholder.', 'bounded': 'Bounded retry placeholder. ' + 'b' * 60_000}
        rendered_forms = []

        def render_prompt(form):
            rendered_forms.append(form)
            return {'template_id': 'synthetic-refusal-retry-v1', 'parameters': {'output_refusal': form},
                    'text': texts[form], 'rendered_prompt_hash': digest(texts[form])}

        def select(limit, task_limit=65_000):
            with patch.object(transport, 'local_input_limit', return_value=limit):
                return self.select_notice(self.records, self.artifacts, ('first',), route=route,
                    role='bar', prompt=render_prompt, packet={'role': 'bar'},
                    task_characters=lambda form: 1000 + len(texts[form]), task_limit_characters=task_limit)

        self.assertIsNone(select(50_000)['notice'])
        self.assertEqual(rendered_forms, ['whole', 'bounded'])
        rendered_forms.clear()
        selected = select(70_000)
        self.assertEqual(rendered_forms, ['whole', 'bounded'])
        self.assertEqual(selected['notice']['form'], 'bounded')
        self.assertEqual(selected['prompt']['parameters'], {'output_refusal': 'bounded'})
        self.assertEqual(selected['request']['instructions'], texts['bounded'])
        self.assertEqual(selected['prompt']['rendered_prompt_hash'], digest(texts['bounded']))
        task = selected['trials'][-1]['task']
        self.assertEqual(task['characters'], 1000 + len(texts['bounded']) + task['notice_characters'])
        self.assertIsNone(select(70_000, task_limit=60_000)['notice'])

    def test_selected_request_keeps_the_exact_notice_and_rebuild_does_not_reselect(self):
        from research_harness import review_excerpts, review_transport as transport
        route = self.build_verified_route()
        real_request = transport.request_body
        def strip_notice(route, prompt, packet, **options):
            supplied = dict(packet)
            if packet['output_refusal']['form'] == 'whole':
                supplied.pop('output_refusal')
            return real_request(route, prompt, supplied, **options)
        packet = {'role': 'bar'}
        packet['digest'] = digest(packet)
        before = copy.deepcopy(packet)
        with patch.object(transport, 'request_body', side_effect=strip_notice):
            selected = self.select_notice(self.records, self.artifacts, ('first',), route=route,
                role='bar', prompt='Frozen reviewer prompt.', packet=packet)
        self.assertEqual(selected['notice']['form'], 'bounded')
        sent = review_excerpts.read_sent_packet(selected['request'])
        self.assertEqual(sent['output_refusal'], selected['notice'])
        self.assertEqual(sent['digest'], digest({key: value for key, value in sent.items() if key != 'digest'}))
        self.assertEqual(packet, before)
        with patch.object(self.notices, 'select_refusal_notice', side_effect=AssertionError('Do not reselect.')), \
                patch.object(refusal, 'classify_observed_output', side_effect=AssertionError('Do not revalidate.')), \
                patch.object(self.notices, 'build_refusal_notice', side_effect=AssertionError('Read the frozen packet.')):
            rebuilt = transport.request_body(route, 'Frozen reviewer prompt.', copy.deepcopy(sent))
            self.assertEqual(rebuilt, selected['request'])


class ContractCheckRegistryTests(unittest.TestCase):
    def test_every_check_attribution_is_registered_and_registry_fields_are_explicit(self):
        names = set()
        root = Path(__file__).resolve().parents[1] / 'research_harness'
        for path in root.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    function = node.func.id if isinstance(node.func, ast.Name) else (
                        node.func.attr if isinstance(node.func, ast.Attribute) else None)
                    if (function in ('contract_check', 'require_contract_check')
                            and node.args and isinstance(node.args[0], ast.Constant)):
                        names.add(node.args[0].value)
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute) and target.attr == 'contract_check':
                            if isinstance(node.value, ast.Constant):
                                names.add(node.value.value)
        self.assertEqual(names, set(refusal.CONTRACT_CHECKS))
        for row in refusal.CONTRACT_CHECKS.values():
            self.assertEqual(set(row), {'notice_fields'})
            self.assertEqual(len(row['notice_fields']), len(set(row['notice_fields'])))
            self.assertNotIn('message', row['notice_fields'])
            self.assertNotIn('path', row['notice_fields'])


class UndeliveredNoticeTests(unittest.TestCase):
    def test_reduced_history_final_refusal_is_delivered_without_other_reviewer_judgments(self):
        from research_harness import review_refusal_notice
        from research_harness import review_protocol as protocol, review_transport as transport
        from research_harness import research_decisions as decisions
        from test_review_actual_delivery_contract import ActualDeliveryContractTests
        case = ActualDeliveryContractTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        fixture = case.case
        reference = {'kind': 'record', 'record_kind': 'problem_value', 'id': 'unseen-value'}
        case.fit_local_requests()
        first = fixture.assign('result', 'first', identifier='history')
        request_output = fixture.build_review_output('result')
        request_output['value']['status'] = 'unresolved'
        request_output['source_requests'] = [{'id': 'inventory', 'kind': 'inventory_page', 'page': 0}]
        with patch.object(transport, 'send_request', return_value=fixture.build_provider_response(request_output)):
            fixture.mutate(protocol.invoke_assignment, {'id': 'attempt-history', 'assignment_id': first['id']},
                           credential='test-only')
        assignment = fixture.assign('result', 'first', identifier='continued', prior_assignment_id=first['id'])
        output = fixture.build_review_output('result')
        output['value']['evidence'] = [reference]
        with patch.object(transport, 'send_request', return_value=fixture.build_provider_response(output)):
            fixture.mutate(protocol.invoke_assignment, {'id': 'attempt-continued', 'assignment_id': assignment['id']},
                           credential='test-only')
        self.assertTrue(any('packet-elided-route-limit' in message.get('content', '')
                            for message in case.read_request('continued')['input']))
        # A record delivered in an earlier authenticated turn is still citable.
        # This record first exists after the final call, so no turn delivered it.
        fixture.put('problem_value', 'unseen-value', dict(fixture.expected['value'], id='unseen-value'))
        payload = dict(output, id='review-continued', dossier_id=None, assignment_id=assignment['id'])
        with self.assertRaises(ResearchError) as caught:
            fixture.mutate(decisions.record_value_review, payload)
        self.assertEqual(caught.exception.code, 'review_evidence_undelivered')
        records = fixture.read_records()
        before = digest(records)
        # The other slot's judgment is present in this store, as in the incident.
        records['value_review']['review-bar-second']['payload']['value']['reason'] = 'OTHER-SLOT-JUDGMENT'
        notice = review_refusal_notice.build_refusal_notice(records, fixture.artifacts,
                                                           ('attempt-continued',), form='whole')
        row = notice['refused'][0]
        self.assertEqual(row['code'], 'review_evidence_undelivered')
        self.assertEqual(row['check'], 'review.evidence_delivery')
        self.assertEqual(row['path'], '/value/evidence')
        self.assertEqual(row['details']['record_kind'], 'problem_value')
        self.assertEqual(row['details']['id'], 'unseen-value')
        self.assertNotIn('OTHER-SLOT-JUDGMENT', serialize(notice))
        self.assertEqual(digest(fixture.read_records()), before)
