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

    def build_notice(self, form='whole', attempt_ids=('first',)):
        return self.notices.build_refusal_notice(self.records, self.artifacts, attempt_ids, form=form)

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
        # A pre-W5 certificate stays a frozen diagnosis even when today's validator gives more detail.
        legacy_records = copy.deepcopy(self.records)
        legacy = legacy_records['review_output_refusal']['first']
        legacy['details'] = {'message': 'The span must lie within the saved text', 'path': '/value/evidence'}
        legacy_before = copy.deepcopy(legacy_records)
        with patch.object(refusal, 'classify_observed_output', side_effect=AssertionError('Keep the saved diagnosis.')):
            for form in ('whole', 'bounded'):
                saved = self.notices.build_refusal_notice(legacy_records, self.artifacts, ('first',), form=form)
                self.assertEqual(saved['refused'][0]['message'], legacy['details']['message'])
                self.assertEqual(saved['refused'][0]['details'], {})
                self.assertEqual(saved['refused'][0]['refusal_digest'], digest(legacy))
        self.assertEqual(legacy_records, legacy_before)

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

        cases = {
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
                    notice = self.notices.build_refusal_notice(records, artifacts, (attempt_id,), form=form)
                    self.assertNotIn('OTHER-REVIEWER-', serialize(notice))
                    self.assertEqual(notice['refused'][0]['code'], error.code)

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
                    self.notices.select_refusal_notice(self.records, self.artifacts, attempt_ids, route={},
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
        from research_harness import review_transport as transport
        route = {'id': 'route', 'adapter': transport.ADAPTER, 'model': 'fixture-model', 'runtime': {},
                 'endpoint': 'https://api.openai.com/v1/responses',
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}}
        self.output['value']['reason'] = 'x' * 100_000
        self.add_refusal('first', self.output)
        packet = {'role': 'bar'}
        def select(**options):
            return self.notices.select_refusal_notice(self.records, self.artifacts, ('first',), route=route,
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

    def test_preflight_selects_bounded_form_and_does_not_hide_unrelated_errors(self):
        from research_harness import review_preflight
        from research_harness import review_transport as transport
        route = {'id': 'route', 'adapter': transport.ADAPTER, 'model': 'fixture-model', 'runtime': {},
                 'endpoint': 'https://api.openai.com/v1/responses',
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}}
        self.output['value']['reason'] = 'x' * 100_000
        self.add_refusal('first', self.output)
        def select():
            return self.notices.select_refusal_notice(self.records, self.artifacts, ('first',), route=route,
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
        route = {'id': 'route', 'adapter': transport.ADAPTER, 'model': 'fixture-model', 'runtime': {},
                 'endpoint': 'https://api.openai.com/v1/responses',
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}}
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
                return self.notices.select_refusal_notice(self.records, self.artifacts, ('first',), route=route,
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
        route = {'id': 'route', 'adapter': transport.ADAPTER, 'model': 'fixture-model', 'runtime': {},
                 'endpoint': 'https://api.openai.com/v1/responses',
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}}
        real_request = transport.request_body
        def strip_notice(route, prompt, packet):
            supplied = dict(packet)
            if packet['output_refusal']['form'] == 'whole':
                supplied.pop('output_refusal')
            return real_request(route, prompt, supplied)
        packet = {'role': 'bar'}
        packet['digest'] = digest(packet)
        before = copy.deepcopy(packet)
        with patch.object(transport, 'request_body', side_effect=strip_notice):
            selected = self.notices.select_refusal_notice(self.records, self.artifacts, ('first',), route=route,
                role='bar', prompt='Frozen reviewer prompt.', packet=packet)
        self.assertEqual(selected['notice']['form'], 'bounded')
        sent = review_excerpts.read_sent_packet(selected['request'])
        self.assertEqual(sent['output_refusal'], selected['notice'])
        self.assertEqual(sent['digest'], digest({key: value for key, value in sent.items() if key != 'digest'}))
        self.assertEqual(packet, before)
        with patch.object(self.notices, 'select_refusal_notice', side_effect=AssertionError('Do not reselect.')), \
                patch.object(refusal, 'classify_observed_output', side_effect=AssertionError('Do not revalidate.')):
            self.assertEqual(self.build_notice(selected['notice']['form']), selected['notice'])


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
        reference = {'kind': 'record', 'record_kind': 'problem_value', 'id': 'value-1'}
        fixture.put('problem_value', 'value-1', dict(fixture.expected['value']))
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
        self.assertNotIn('OTHER-SLOT-JUDGMENT', serialize(notice))
        self.assertEqual(digest(fixture.read_records()), before)
