"""New problem assignments state route limits while stored turns keep their bytes."""

import copy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from research_harness import review_prompt_paragraphs as paragraphs
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.evidence import digest
from research_harness.errors import ResearchError
import test_research_problem_reviews as problem_fixtures
from test_review_output_profile import build_row
from test_review_prompt_paragraphs import build_assignment
from test_review_route_capacity import build_route


NOTICE_PROTOCOL = 'exactory-route-capacity-notice-v1'


def build_notice(kind='request'):
    return dict(protocol=NOTICE_PROTOCOL, kind=kind, output_tokens=128000, answer_bound=15000)


class CapacityParagraphTests(unittest.TestCase):
    def test_notice_renders_numbers_and_replays_the_exact_parameters_for_every_role(self):
        for role in ('bar', 'slate', 'result'):
            with self.subTest(role=role):
                notice = build_notice()
                packet = dict(subject=dict(kind='problem'), route_capacity=notice)
                prompt = protocol.canonical_prompt(role, orchestration='problem_creation',
                    **paragraphs.select_prompt_parameters(role, packet))
                self.assertEqual(prompt['parameters']['capacity'], notice)
                self.assertIn(NOTICE_PROTOCOL, prompt['parameters']['problem_paragraphs'])
                self.assertIn('128000 tokens', prompt['text'])
                self.assertIn('15000 characters', prompt['text'])
                self.assertIn('reasoning and answer share', prompt['text'])
                self.assertIn('cut at the limit is lost', prompt['text'])
                self.assertEqual(digest(prompt['text']), prompt['rendered_prompt_hash'])
                self.assertTrue(protocol._prompt_matches(build_assignment(role, prompt), packet=packet))
                self.assertEqual(paragraphs.strip_prompt_paragraphs(prompt, role),
                    protocol.canonical_prompt(role, orchestration='problem_creation'))
                self.assertEqual(paragraphs.read_paragraph_parameters(prompt)['capacity'], notice)

    def test_missing_extra_or_malformed_capacity_parameters_are_refused(self):
        invalid = [None, {}, dict(build_notice(), extra=True), dict(build_notice(), protocol='unknown'),
                   dict(build_notice(), kind='unknown'), dict(build_notice(), output_tokens=True),
                   dict(build_notice(), answer_bound=0), dict(build_notice(), answer_bound='15000')]
        for notice in invalid:
            with self.subTest(notice=notice):
                with self.assertRaises(ResearchError) as caught:
                    protocol.canonical_prompt('bar', orchestration='problem_creation',
                        problem_paragraphs=[NOTICE_PROTOCOL], capacity=notice)
                self.assertEqual(caught.exception.code, 'invalid_review_context')
        with self.assertRaises(ResearchError) as caught:
            protocol.canonical_prompt('bar', orchestration='problem_creation', capacity=build_notice())
        self.assertEqual(caught.exception.code, 'invalid_review_context')

    def test_notice_authentication_binds_the_packet_numbers_and_rendered_text(self):
        packet = dict(subject=dict(kind='problem'), route_capacity=build_notice())
        prompt = protocol.canonical_prompt('bar', orchestration='problem_creation',
            **paragraphs.select_prompt_parameters('bar', packet))
        self.assertIn('capacity', prompt['parameters'])
        changed_packet = copy.deepcopy(packet)
        changed_packet['route_capacity']['answer_bound'] += 1
        self.assertFalse(protocol._prompt_matches(build_assignment('bar', prompt), packet=changed_packet))
        for target in ('text', 'parameters'):
            changed = copy.deepcopy(prompt)
            if target == 'text':
                changed['text'] = changed['text'].replace('128000 tokens', '128001 tokens')
                changed['template_hash'] = changed['rendered_prompt_hash'] = digest(changed['text'])
            else:
                changed['parameters']['capacity']['output_tokens'] += 1
            self.assertFalse(protocol._prompt_matches(build_assignment('bar', changed), packet=packet))

    def test_delivery_notice_keeps_the_capacity_parameter_and_registered_order(self):
        from research_harness.review_delivery_notice import _append_notice_prompt
        notice = build_notice()
        packet = dict(subject=dict(kind='problem'), route_capacity=notice)
        prompt = protocol.canonical_prompt('bar', orchestration='problem_creation',
            **paragraphs.select_prompt_parameters('bar', packet))
        selected = _append_notice_prompt(prompt, 'bar')
        self.assertEqual(selected['parameters']['capacity'], notice)
        packet['delivery_notice'] = dict(protocol=paragraphs.DELIVERY_NOTICE_PROTOCOL, sources=[])
        self.assertTrue(protocol._prompt_matches(build_assignment('bar', selected), packet=packet))

    def test_later_paragraph_text_keeps_recorded_capacity_versions_replayable(self):
        packet = dict(subject=dict(kind='problem'), route_capacity=build_notice())
        recorded = protocol.canonical_prompt('bar', orchestration='problem_creation',
            **paragraphs.select_prompt_parameters('bar', packet))
        old = next(paragraph for paragraph in paragraphs.PARAGRAPHS if paragraph.name == 'capacity-notice')
        later = replace(old, versions=old.versions + (('exactory-route-capacity-notice-v2',
            '\n\nLater notice: {output_tokens} tokens, {answer_bound} characters.'),))
        registry = tuple(later if paragraph == old else paragraph for paragraph in paragraphs.PARAGRAPHS)
        with patch.object(paragraphs, 'PARAGRAPHS', registry):
            current = protocol.canonical_prompt('bar', orchestration='problem_creation',
                **paragraphs.select_prompt_parameters('bar', packet))
            self.assertEqual(current['parameters']['capacity'], build_notice())
            self.assertIn('Later notice: 128000 tokens, 15000 characters.', current['text'])
            self.assertTrue(protocol._prompt_matches(build_assignment('bar', recorded), packet=packet))
            self.assertTrue(protocol._prompt_matches(build_assignment('bar', current), packet=packet))


class CapacityAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.case = problem_fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        self.case.prepare_review_route()
        # A route probed before output accounting has no measured task row.
        probe = self.case.read_records()['review_route_probe']['probe']
        self.case.put('review_route_probe', 'probe',
                      {key: value for key, value in probe.items() if key not in ('task', 'answer')})

    def read_packet(self, assignment):
        return protocol._read_json(self.case.artifacts, assignment['packet'])

    def invoke_turn(self, assignment, output):
        with patch.object(protocol.transport, 'send_request', return_value=self.case.build_provider_response(output)):
            return self.case.mutate(protocol.invoke_assignment,
                dict(id='attempt-' + assignment['id'], assignment_id=assignment['id']), credential='test-only')

    def assert_notice_matches_report(self, assignment, kind):
        packet = self.read_packet(assignment)
        notice = packet.get('route_capacity', {})
        self.assertEqual(notice.get('protocol'), NOTICE_PROTOCOL)
        route = self.case.read_records()['review_route']['route']
        report = capacity.describe_route_report(self.case.read_records(), route)
        self.assertEqual(notice['kind'], kind)
        self.assertEqual(notice['output_tokens'], report['route_capacity']['output_tokens']['value'])
        self.assertEqual(notice['answer_bound'], report['task_limits'][kind]['answer_bound'])
        self.assertEqual(assignment['prompt']['parameters']['capacity'], notice)
        self.assertIn(str(notice['output_tokens']) + ' tokens', assignment['prompt']['text'])
        self.assertIn(str(notice['answer_bound']) + ' characters', assignment['prompt']['text'])
        self.assertTrue(protocol._prompt_matches(assignment, packet=packet))

    def test_new_bar_slate_and_result_assignments_take_the_profile_rule(self):
        rows = [build_row(kind=kind) for kind in ('single', 'request', 'reading')]
        with patch.object(capacity, 'read_profile_priors', return_value=rows):
            for reviewer in ('first', 'second'):
                self.case.observe('bar', reviewer)
            for identifier, assignment in self.case.read_records()['review_assignment'].items():
                self.assert_notice_matches_report(assignment, 'request' if assignment['prompt']['parameters'].get('excerpts') else 'single')
            self.assert_notice_matches_report(self.case.assign('slate', 'first'), 'single')
            self.assert_notice_matches_report(self.case.assign('result', 'first'), 'single')

    def test_unprofiled_packet_prompt_and_request_keep_the_existing_bytes(self):
        captured = []
        prepare = protocol.prepare_capacity_notice
        def capture_before_notice(records, route, role, packet):
            captured.append(copy.deepcopy(packet))
            return prepare(records, route, role, packet)
        with patch.object(protocol, 'prepare_capacity_notice', side_effect=capture_before_notice):
            assignment = self.case.assign('bar', 'first', identifier='turn-1')
        packet = self.read_packet(assignment)
        self.assertEqual(packet, captured[0])
        self.assertNotIn('route_capacity', packet)
        self.assertNotIn('capacity', assignment['prompt']['parameters'])
        self.assertEqual(assignment['prompt'], protocol.canonical_prompt('bar', orchestration='problem_creation'))
        route = self.case.read_records()['review_route']['route']
        self.assertEqual(protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, assignment),
                         protocol.transport.request_body(route, assignment['prompt']['text'], captured[0], []))

    def test_running_unprofiled_session_adds_notice_without_changing_stored_turns(self):
        first = self.case.assign('bar', 'first', identifier='turn-1')
        output = self.case.build_review_output('bar')
        output['value'].update(status='unresolved', evidence=[])
        output['source_requests'] = [dict(id='inventory', kind='inventory_page', page=0)]
        self.invoke_turn(first, output)
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-turn-1']
        before = {key: self.case.artifacts.read(first[key]) for key in ('packet',)}
        request_bytes = self.case.artifacts.read(attempt['request'])
        rows = [build_row(kind='reading', thinking=1000), build_row(kind='request', over=4)]
        with patch.object(capacity, 'read_profile_priors', return_value=rows):
            second = self.case.assign('bar', 'first', identifier='turn-2', prior_assignment_id='turn-1')
            self.assert_notice_matches_report(second, 'request')
            self.assertEqual(second['prompt']['parameters']['capacity']['answer_bound'], 18000)
        self.assertEqual(self.case.read_records()['review_assignment']['turn-1'], first)
        self.assertEqual(self.case.artifacts.read(first['packet']), before['packet'])
        self.assertEqual(self.case.artifacts.read(attempt['request']), request_bytes)
        self.assertTrue(protocol.authenticate_attempt(self.case.read_records(), self.case.artifacts, first, attempt))

    def test_profile_can_come_from_stored_attempts_of_an_unlisted_route(self):
        first = self.case.assign('bar', 'first', identifier='turn-1')
        self.invoke_turn(first, self.case.build_review_output('bar'))
        second = self.case.assign('bar', 'second', identifier='turn-2')
        self.assert_notice_matches_report(second, 'single')


class CapacityProfileSelectionTests(unittest.TestCase):
    def test_profile_presence_uses_rows_including_priors_without_a_sample_floor(self):
        packet = dict(subject=dict(kind='problem'), reviewer_excerpts={}, digest='old')
        route = build_route()
        selected = protocol.prepare_capacity_notice({}, route, 'bar', packet)
        profile = capacity.read_output_profile({}, route)
        self.assertTrue(profile['rows'])
        self.assertEqual(selected['route_capacity']['kind'], 'request')
        self.assertEqual(selected['route_capacity']['answer_bound'], capacity.select_task_limits(route, profile, 'request')['answer_bound'])
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            self.assertIs(protocol.prepare_capacity_notice({}, route, 'bar', packet), packet)
        self.assertNotIn('route_capacity', packet)

    def test_effective_output_cap_and_unmeasured_task_bound_match_route_report(self):
        route = build_route(output=600000)
        packet = dict(subject=dict(kind='problem'), reviewer_excerpts={})
        rows = [build_row(kind='request', over=4), build_row(kind='reading', thinking=1000)]
        with patch.object(capacity, 'read_profile_priors', return_value=rows):
            selected = protocol.prepare_capacity_notice({}, route, 'bar', packet)
            report = capacity.describe_route_report({}, route)
        self.assertEqual(selected['route_capacity']['output_tokens'], 128000)
        self.assertEqual(selected['route_capacity']['answer_bound'], report['task_limits']['request']['answer_bound'])
        self.assertEqual(selected['route_capacity']['answer_bound'], 18000)
        self.assertEqual(report['task_limits']['reading']['answer_bound'], 67000)
        self.assertEqual(selected['digest'], digest({key: value for key, value in selected.items() if key != 'digest'}))

    def test_only_problem_bar_slate_and_result_assignments_receive_the_member(self):
        route = build_route()
        problem = dict(subject=dict(kind='problem'))
        for role, packet in (('adjudicator', problem), ('native_math', problem), ('bar', dict(role='bar'))):
            self.assertIs(protocol.prepare_capacity_notice({}, route, role, packet), packet)
        self.assertIn('route_capacity', protocol.prepare_capacity_notice({}, route, 'result', problem))
        controller = dict(problem, route_capacity=build_notice('single'), staged_review=dict(kind='controller'))
        self.assertNotIn('route_capacity', protocol.prepare_capacity_notice({}, route, 'result', controller))


class CapacityChainTests(unittest.TestCase):
    def test_legacy_parts_refresh_binds_the_prompt_after_capacity_selection(self):
        import problem_parts_fixtures as fixtures
        case = fixtures.ResultPartsCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.oversized(count=6, characters=24000, limit=110000)
        build = protocol.build_packet
        def build_refresh(*args, **kwargs):
            packet = build(*args, **kwargs)
            packet['problem_refresh'] = dict(assignment_id='earlier-recorded-final',
                template_id='earlier-template', prompt_hash='0' * 64)
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet
        with patch.object(protocol, 'build_packet', side_effect=build_refresh):
            assignment = case.assign('refresh')
        packet = case.packet(assignment)
        self.assertEqual(packet['route_capacity']['kind'], 'read')
        self.assertEqual(packet['problem_refresh']['prompt_hash'], assignment['prompt_hash'])
        self.assertEqual(packet['problem_refresh']['template_id'], assignment['prompt']['template_id'])

    def test_profiled_ordinary_review_can_upgrade_to_the_separately_sized_staged_protocol(self):
        import problem_parts_fixtures as fixtures
        case = fixtures.ResultPartsCase()
        case.staged_entry = True
        case.setUp()
        self.addCleanup(case.doCleanups)
        prior = case.assign('predecessor')
        self.assertIn('route_capacity', case.packet(prior))
        controller = case.case.assign('result', 'first', identifier='controller',
            staged_upgrade=dict(assignment_id=prior['id']))
        self.assertEqual(controller['staged_review']['kind'], 'controller')
        self.assertNotIn('capacity', controller['prompt']['parameters'])

    def test_legacy_parts_chain_adds_current_turn_bounds_and_authenticates_old_turn(self):
        import review_parts_fixtures as fixtures
        case = fixtures.PartsResultCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.oversized(count=12, characters=10000, limit=110000)
        with patch.object(protocol, 'prepare_capacity_notice', side_effect=lambda records, route, role, packet: packet):
            first = case.assign('legacy-part')
        self.assertEqual(case.packet(first)['review_parts']['kind'], 'read')
        self.assertNotIn('route_capacity', case.packet(first))
        frozen_prompt = copy.deepcopy(first['prompt'])
        case.invoke(first)
        assignment = case.assign('new-part', first['id'])
        turns = []
        while True:
            packet = case.packet(assignment)
            kind = packet['review_parts']['kind']
            turns.append(kind)
            notice = packet.get('route_capacity', {})
            self.assertEqual(notice.get('kind'), kind)
            route = case.case.read_records()['review_route']['route']
            report = capacity.describe_route_report(case.case.read_records(), route)
            self.assertEqual(notice['answer_bound'], report['task_limits'][kind]['answer_bound'])
            self.assertEqual(assignment['prompt']['parameters']['capacity'], notice)
            self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
            if kind == 'final':
                break
            self.assertLess(len(turns), 20)
            case.invoke(assignment)
            assignment = case.assign('new-part-' + str(len(turns)), assignment['id'])
        records = case.case.read_records()
        self.assertEqual(records['review_assignment'][first['id']]['prompt'], frozen_prompt)
        self.assertTrue(protocol.authenticate_attempt(records, case.case.artifacts, first,
            records['review_attempt']['attempt-' + first['id']]))

    def test_fitted_session_keeps_capacity_and_delivery_notices_together(self):
        import test_review_session_delivery as fixtures
        case = fixtures.SessionDeliveryTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        final_id = case.build_reduced_session()
        assignment = case.case.read_records()['review_assignment'][final_id]
        packet = case.read_packet(final_id)
        self.assertIn('delivery_notice', packet)
        self.assertEqual(packet.get('route_capacity', {}).get('kind'), 'request')
        self.assertEqual(assignment['prompt']['parameters']['capacity'], packet['route_capacity'])
        self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
