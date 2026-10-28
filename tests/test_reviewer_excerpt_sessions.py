"""Bar, slate and result sessions keep checked reviewer excerpts, answer passages and send each full text once."""

import importlib
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import (invoke_codex, limit_codex_input, probe_codex_route, read_sent_packet,
                                  record_codex_route)
from literature_fixtures import LiteratureCase
from strategy_fixtures import StrategyCase
import test_research_problem_reviews as problem_fixtures
from test_research_strategic_source_requests import StrategicSourceHelpers
from research_harness import bar_sources, research_decisions, review_excerpts, review_protocol as protocol


def build_lemma(index):
    return 'Lemma %d. The bounded comparator of source %d holds for every finite input.' % (index, index)


def build_body(index):
    return (build_lemma(index) + '\n' + ('Exact complete mathematical source %d with its proof. ' % index) * 1100
            + 'References: none.\f')


def continue_under_earlier_rules(*arguments, **options):
    """A continuation as the release before the excerpt rules built it: no excerpt record in its packet."""
    result = REAL_CONTINUATION(*arguments, **options)
    result.pop(review_excerpts.KEY)
    return result


REAL_CONTINUATION = bar_sources.continuation


class ExcerptSessionCase(StrategyCase):
    metadata = LiteratureCase.metadata
    capture = LiteratureCase.capture

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        record_codex_route(self)
        self.versions = []
        for index in (1, 2, 3):
            version = self.metadata(index)
            self.capture(version, pdf_text=build_body(index))
            self.versions.append(version)
        self.requests = [{'id': 'source-%d' % index, 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}
                         for index, version in enumerate(self.versions, 1)]

    def assign(self, identifier, reviewer='reviewer-a', **context):
        return self.mutate(protocol.record_assignment, {'id': identifier, 'route_id': 'route',
            'dossier_id': 'dossier-1', 'role': 'bar', 'reviewer_id': reviewer, 'author_id': 'author',
            'context': context})['result']

    def build_request_turn(self, requests, excerpts=None):
        output = self.review_response('bar', status='unresolved')
        output['value']['evidence'] = []
        output['source_requests'] = list(requests)
        if excerpts is not None:
            output['excerpts'] = list(excerpts)
        return output

    def invoke(self, identifier, output):
        return invoke_codex(self, identifier, output)

    def packet(self, identifier):
        assignment = self.store.snapshot()['records']['review_assignment'][identifier]
        return json.loads(self.artifacts.read(assignment['packet']))

    def deliveries(self, identifier):
        return {delivery['request']['id']: delivery for delivery in self.packet(identifier)['source_deliveries']}

    def state(self, identifier):
        return protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, identifier)

    def record_final(self, identifier, output, review_id='final-bar'):
        return self.mutate(research_decisions.record_value_review,
                           dict(output, id=review_id, dossier_id='dossier-1', assignment_id=identifier))['result']

    def body_sha256(self, version):
        return self.store.snapshot()['records']['work'][version]['fulltexts'][0]['text']['sha256']

    def copy_lemma(self, index):
        return {'version_id': self.versions[index - 1], 'text': build_lemma(index).replace(' ', ' \n '),
                'purpose': 'The comparator that sets the field standard.'}


class ExcerptSessionTests(ExcerptSessionCase):
    def test_a_session_beyond_the_route_limit_reaches_a_recordable_final_bar(self):
        limit_codex_input(self, 105000)
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests))
        parent, delivered_in = 'a', {}
        for turn in range(1, len(self.versions) + 3):
            child = 'turn-%d' % turn
            self.assign(child, prior_assignment_id=parent)
            statuses = {delivery['status'] for delivery in self.packet(child)['source_deliveries']}
            if not self.packet(child)['pending_source_requests'] and statuses == {'already_delivered'}:
                break
            # The reviewer asks for every text again, as the reviewer of the incident did.
            self.invoke(child, self.build_request_turn(self.requests))
            _, sent = read_sent_packet(self, 'attempt-' + child)
            for delivery in sent['source_deliveries']:
                if delivery['status'] == 'delivered':
                    delivered_in.setdefault(delivery['request']['version_id'], child)
            parent = child
        else:
            self.fail('The session did not settle within the number of requested texts.')
        self.assertEqual(set(delivered_in), set(self.versions))
        # The route holds one text in each turn, so the texts arrive in three turns and the fourth finds all delivered.
        self.assertEqual(sorted(delivered_in.values()), ['turn-1', 'turn-2', 'turn-3'])
        self.assertEqual(turn, len(self.versions) + 1)
        final = self.review_response('bar')
        self.invoke(child, final)
        self.assertEqual(self.record_final(child, final)['assignment_id'], child)
        for delivery in self.packet(child)['source_deliveries']:
            version = delivery['request']['version_id']
            self.assertEqual(delivery['turn']['assignment_id'], delivered_in[version])

    def test_a_repeated_full_text_request_is_answered_as_already_delivered(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        self.assign('b', prior_assignment_id='a')
        self.assertEqual(self.deliveries('b')['source-1']['status'], 'delivered')
        again = dict(self.requests[0], id='source-1-again')
        self.invoke('b', self.build_request_turn([again]))
        self.assertEqual(self.state('b')['pending_source_requests'], [again])
        self.assign('c', prior_assignment_id='b')
        delivery = self.deliveries('c')['source-1-again']
        self.assertEqual((delivery['status'], delivery['turn'], delivery['reading_credit']),
                         ('already_delivered', {'assignment_id': 'b', 'attempt_id': 'attempt-b'}, False))
        self.assertTrue(delivery['reason'])
        packet = self.packet('c')
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertNotIn(self.body_sha256(self.versions[0]),
                         [entry['artifact']['sha256'] for entry in packet['evidence']])
        final = self.review_response('bar')
        self.invoke('c', final)
        self.record_final('c', final)

    def test_a_text_delivered_under_the_earlier_rules_is_delivered_once_more(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        with patch.object(bar_sources, 'continuation', side_effect=continue_under_earlier_rules):
            earlier = self.assign('b', prior_assignment_id='a')
        self.assertNotIn(review_excerpts.KEY, self.packet('b'))
        self.assertEqual(earlier['prompt'], protocol.canonical_prompt('bar'))
        self.invoke('b', self.build_request_turn([dict(self.requests[0], id='source-1-again')]))
        self.assertTrue(self.state('b')['ready'])
        self.assign('c', prior_assignment_id='b')
        self.assertEqual(self.deliveries('c')['source-1-again']['status'], 'delivered')
        self.assertIn(self.body_sha256(self.versions[0]),
                      [entry['artifact']['sha256'] for entry in self.packet('c')['evidence']])

    def test_passage_requests_are_answered_and_never_pending(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        self.assign('b', prior_assignment_id='a')
        passage = {'id': 'lemma', 'kind': 'passage', 'version_id': self.versions[0],
                   'anchor': 'Lemma 1. The bounded comparator', 'characters': 120}
        missing = {'id': 'absent', 'kind': 'passage', 'version_id': self.versions[0],
                   'anchor': 'A sentence that this source does not contain.', 'characters': 50}
        self.invoke('b', self.build_request_turn([passage, missing]))
        self.assertEqual(self.state('b')['pending_source_requests'], [passage, missing])
        self.assign('c', prior_assignment_id='b')
        deliveries = self.deliveries('c')
        self.assertEqual((deliveries['lemma']['status'], deliveries['lemma']['text']),
                         ('delivered', build_body(1)[:120]))
        self.assertEqual(deliveries['lemma']['locator']['start'], 0)
        self.assertEqual((deliveries['absent']['status'], deliveries['absent']['reason']),
                         ('not_found', 'anchor_not_found'))
        packet = self.packet('c')
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertEqual(packet[review_excerpts.KEY]['allowances']['passages']['used'], 120)
        final = self.review_response('bar')
        self.invoke('c', final)
        self.record_final('c', final)

    def test_a_passage_request_outside_the_excerpt_rules_is_refused(self):
        self.assign('a')
        passage = {'id': 'lemma', 'kind': 'passage', 'version_id': self.versions[0],
                   'anchor': 'Lemma 1. The bounded comparator', 'characters': 120}
        output = self.build_request_turn([passage])
        self.invoke('a', output)
        self.error('invalid_review_source_request', lambda: self.assign('b', prior_assignment_id='a'))
        refusal = self.assert_refusal('invalid_review_source_request', lambda: self.record_final('a', output),
                                      'attempt-a')
        self.assertEqual(refusal['check'], 'review.source_requests')

    def test_excerpts_are_checked_and_carried_in_order(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:2]))
        self.assign('b', prior_assignment_id='a')
        self.assertEqual(self.packet('b')[review_excerpts.KEY], {'protocol': review_excerpts.PROTOCOL, 'excerpts': [],
                                                                 'allowances': review_excerpts.create_allowances()})
        unmatched = {'version_id': self.versions[0], 'text': 'A sentence that the first source never states anywhere.',
                     'purpose': 'A claim to check against the source later.'}
        page = {'id': 'page-0', 'kind': 'inventory_page', 'page': 0}
        self.invoke('b', self.build_request_turn([page], excerpts=[self.copy_lemma(1), unmatched]))
        self.assign('c', prior_assignment_id='b')
        carried = self.packet('c')[review_excerpts.KEY]
        self.assertEqual([(row['status'], row['version_id']) for row in carried['excerpts']],
                         [('verified', self.versions[0]), ('unmatched', self.versions[0])])
        self.assertEqual(carried['excerpts'][0]['text'], build_lemma(1))
        self.assertEqual(carried['excerpts'][1]['text'], unmatched['text'])
        self.assertEqual(carried['allowances']['excerpts']['used'], len(build_lemma(1)))
        self.invoke('c', self.build_request_turn([dict(page, id='page-0-again')], excerpts=[self.copy_lemma(2)]))
        self.assign('d', prior_assignment_id='c')
        carried = self.packet('d')[review_excerpts.KEY]
        self.assertEqual([row['text'] for row in carried['excerpts']],
                         [build_lemma(1), unmatched['text'], build_lemma(2)])
        self.assertEqual(carried['allowances']['excerpts']['used'], len(build_lemma(1)) + len(build_lemma(2)))
        final = dict(self.review_response('bar'), excerpts=[self.copy_lemma(1)])
        self.invoke('d', final)
        self.assertEqual(self.record_final('d', final)['payload']['excerpts'], [self.copy_lemma(1)])

    def test_excerpts_outside_the_excerpt_rules_are_refused(self):
        self.assign('a')
        final = dict(self.review_response('bar'), excerpts=[self.copy_lemma(1)])
        self.invoke('a', final)
        refusal = self.assert_refusal('invalid_value_review', lambda: self.record_final('a', final), 'attempt-a')
        self.assertEqual(refusal['check'], 'review.shape')

    def test_new_continuations_carry_the_excerpt_rules_under_the_same_template(self):
        initial = self.assign('a')
        self.assertEqual(initial['prompt'], protocol.canonical_prompt('bar'))
        self.assertNotIn('reviewer_excerpts', initial['prompt']['parameters'])
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        continued = self.assign('b', prior_assignment_id='a')
        prompt = continued['prompt']
        self.assertEqual(prompt, protocol.canonical_prompt('bar', excerpts=True))
        self.assertEqual(prompt['template_id'], initial['prompt']['template_id'])
        self.assertEqual(prompt['parameters']['reviewer_excerpts'], review_excerpts.PROTOCOL)
        self.assertTrue(prompt['text'].startswith(initial['prompt']['text']))
        self.assertTrue(prompt['text'].endswith(review_excerpts.PHASE_PROMPT))
        self.assertEqual(prompt['text'], prompt['text'].strip())
        # Earlier template versions only replay records made before the excerpt rules.
        self.assertEqual(protocol.canonical_prompt('bar', version=1, excerpts=True),
                         protocol.canonical_prompt('bar', version=1))
        records = self.store.snapshot()['records']
        self.assertTrue(protocol._prompt_matches(records['review_assignment']['b']))
        self.assertFalse(protocol._prompt_matches(dict(records['review_assignment']['a'], prompt=prompt)))
        altered = dict(records['review_assignment']['b'], prompt=dict(prompt, text=prompt['text'] + ' '))
        self.assertFalse(protocol._prompt_matches(altered))

    def test_a_context_repair_carries_the_excerpt_rules_and_answers_passages(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        self.assign('b', prior_assignment_id='a')
        passage = {'id': 'lemma', 'kind': 'passage', 'version_id': self.versions[0],
                   'anchor': 'Lemma 1. The bounded comparator', 'characters': 80}
        self.invoke('b', self.build_request_turn([passage, self.requests[1]], excerpts=[self.copy_lemma(1)]))
        self.mutate(protocol.record_context_event, {'id': 'exposure', 'assignment_id': 'b', 'kind': 'contamination',
            'source': 'author_history', 'reason': 'The continuation saw an author note.', 'evidence': [self.evidence]})
        probe_codex_route(self, 'repair-probe')
        repaired = self.assign('repair', reviewer='repair-reviewer', context_repair={
            'assignment_id': 'b', 'event_ids': ['exposure'], 'probe_id': 'repair-probe',
            'reason': 'A fresh verified context reassesses the session.', 'evidence': [self.evidence]})
        self.assertEqual(repaired['prompt'], protocol.canonical_prompt('bar', repair=True, excerpts=True))
        packet = self.packet('repair')
        self.assertEqual(packet['pending_source_requests'], [self.requests[1]])
        answer = next(delivery for delivery in packet['source_deliveries'] if delivery['request'] == passage)
        self.assertEqual((answer['status'], answer['text']), ('delivered', build_body(1)[:80]))
        self.assertEqual([row['text'] for row in packet[review_excerpts.KEY]['excerpts']], [build_lemma(1)])
        self.assertEqual(packet[review_excerpts.KEY]['allowances']['passages']['used'], 80)


class SlateExcerptRulesTests(StrategicSourceHelpers, StrategyCase):
    phase = 'slate'
    metadata = LiteratureCase.metadata
    capture = LiteratureCase.capture

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.source = self.metadata(1)
        self.capture(self.source, pdf_text=build_body(1))
        self.seed_bars()

    def test_a_slate_continuation_carries_the_excerpt_rules_and_answers_a_repeated_text(self):
        initial = self.assignment()
        self.assertEqual(initial['prompt'], protocol.canonical_prompt('slate'))
        request = {'id': 'body', 'kind': 'source', 'version_id': self.source, 'depth': 'fulltext'}
        self.request([request])
        continued = self.continue_review()
        self.assertEqual(continued['prompt'], protocol.canonical_prompt('slate', excerpts=True))
        self.request([dict(request, id='body-again')], 'source-b')
        self.continue_review('source-c', 'source-b')
        packet = self.packet('source-c')
        [delivery] = packet['source_deliveries']
        self.assertEqual((delivery['status'], delivery['turn']['assignment_id']), ('already_delivered', 'source-b'))
        self.assertEqual(packet['pending_source_requests'], [])
        final = self.review_response('slate')
        self.invoke(final, 'source-c')
        self.record_final(final, 'source-c')


class EarlierProblemBarSessionTests(unittest.TestCase):
    def setUp(self):
        # Load the native candidate dependency before the existing claim fixture replaces its module.
        importlib.import_module('research_harness.problem_candidates')
        self.case = problem_fixtures.ProblemReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.case.prepare_review_route()
        self.case.supporting = self.case.artifacts.put(b'The original comparison establishes the field standard.',
                                                       'text/plain')

    def request_turn(self):
        output = self.case.build_review_output('bar')
        output['value'].update(status='unresolved', evidence=[])
        output['source_requests'] = [{'id': 'page-0', 'kind': 'inventory_page', 'page': 0}]
        return output

    def invoke(self, identifier, output):
        case = self.case
        with patch('research_harness.review_transport.send_request', return_value=case.build_provider_response(output)):
            case.mutate(protocol.invoke_assignment, {'id': 'attempt-' + identifier, 'assignment_id': identifier},
                        credential='test-only')

    def observe_session(self, reviewer, *, earlier):
        case, first, second = self.case, 'bar-' + reviewer, 'bar-' + reviewer + '-2'
        case.assign('bar', reviewer, identifier=first, **case.deliver_supporting(reviewer))
        self.invoke(first, self.request_turn())
        if earlier:
            with patch.object(bar_sources, 'continuation', side_effect=continue_under_earlier_rules):
                case.assign('bar', reviewer, identifier=second, prior_assignment_id=first)
        else:
            case.assign('bar', reviewer, identifier=second, prior_assignment_id=first)
        output = case.build_review_output('bar')
        self.invoke(second, output)
        case.mutate(research_decisions.record_value_review, dict(output, id='review-bar-' + reviewer, dossier_id=None,
                                                                  assignment_id=second))

    def test_a_bar_session_recorded_under_the_earlier_prompts_stays_valid_and_current(self):
        case = self.case
        self.observe_session('first', earlier=True)
        self.observe_session('second', earlier=False)
        records = case.read_records()
        earlier, current = (records['review_assignment'][identifier] for identifier in ('bar-first-2', 'bar-second-2'))
        bar_prompt = protocol.canonical_prompt('bar', orchestration='problem_creation')
        self.assertEqual(earlier['prompt'], bar_prompt)
        self.assertEqual(current['prompt'],
                         protocol.canonical_prompt('bar', orchestration='problem_creation', excerpts=True))
        self.assertEqual(current['prompt']['template_id'], bar_prompt['template_id'])
        for assignment in (earlier, current):
            state = protocol.assignment_state(records, case.artifacts, assignment['id'])
            self.assertTrue(state['ready'], state['obligations'])
            attempt = records['review_attempt']['attempt-' + assignment['id']]
            self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, assignment, attempt))
        self.assertEqual(sorted(protocol.find_current_problem_bar_ids(records, case.artifacts)),
                         ['review-bar-first', 'review-bar-second'])
        for reviewer in ('first', 'second'):
            self.assertIsNone(protocol._find_problem_refresh_parent(records, case.artifacts, {
                'role': 'bar', 'reviewer_id': reviewer, 'subject': earlier['subject'], 'context': {}}))
            case.assert_error('review_resampling_forbidden',
                              lambda: case.assign('bar', reviewer, identifier='bar-' + reviewer + '-again'))
