"""A slate reviewer receives the text of each work that a current candidate cites as evidence (design slate-reviews,
requirements R1 to R5, R10 to R12). Such a slate review is a session of its own in the slot of the reviewer's bar."""

import copy
from unittest.mock import patch

from research_harness import bar_sources, review_protocol as protocol
from research_harness import review_session_v4 as session, review_transport as transport
from research_harness import research_decisions as decisions
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.reading import record_reading
import test_research_problem_slate as slate_fixtures
from test_research_problem_delivery import ProblemDeliveryCase

DELIVERY_PROTOCOL = 'exactory-problem-slate-delivery-v1'


class SlateDeliveryCase(ProblemDeliveryCase):
    build_slate_output = slate_fixtures.ProblemSlateTests.build_slate_output
    read_assignment = slate_fixtures.ProblemSlateTests.read_assignment

    def setUp(self):
        super().setUp()
        self.initialize_problem()

    def read_records(self):
        return self.store.snapshot()['records']

    def cite_reading(self, reading_id):
        saved = self.read_records()['reading'][reading_id]
        return {'kind': 'record', 'record_kind': 'reading', 'id': reading_id, 'digest': digest(saved)}

    def record_cited_candidate(self, identifier='candidate-1', evidence=()):
        return self.record_candidate(identifier, source={'kind': 'literature', 'evidence': list(evidence),
            'reference': 'The bound is stated as open in the cited work.'})

    def record_long_reading(self, number, words):
        """A work whose captured full text holds the given count of words, with its full reading."""
        version = self.metadata(number)
        capture = self.capture(version, body='The long body. ' + 'word ' * words + 'References: none.')
        bundle = self.bundle(version, capture=capture, bundle_id='bundle-%d' % number)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, 'read-long-%d' % number))
        return version, 'read-long-%d' % number

    def prepare_cited_slate(self, words=None):
        """A work with its full text and its full reading, a candidate that cites the reading, and two bars that did
        not request the work. Returns the version of the work."""
        if words is None:
            reading = self.record_full_reading()
            version, reading_id = reading['version_id'], reading['reading_id']
        else:
            version, reading_id = self.record_long_reading(1, words)
        self.record_cited_candidate(evidence=[self.cite_reading(reading_id)])
        self.observe_bars()
        return version

    def cite_everywhere(self, output, reference):
        """The output with every evidence list of its value citing the reference alone."""
        output = copy.deepcopy(output)
        output['value']['evidence'] = [reference]
        for assurance in output['value']['assurances']:
            assurance['evidence'] = [reference]
        return output

    def answer(self, assignment, output):
        with patch('research_harness.review_transport.send_request', return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {'id': 'attempt-' + assignment['id'],
                                                     'assignment_id': assignment['id']}, credential='test-only')
        return dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id'])

    def read_delivered_text(self, packet):
        delivery = packet['slate_delivery']['cited_works'][0]['delivery']
        return delivery['evidence'][0]


class SlateDeliveryTests(SlateDeliveryCase):
    def test_A1_a_cited_work_reaches_a_slate_of_its_own_session(self):
        version = self.prepare_cited_slate()
        assignment = self.assign('slate', 'first')
        records = self.read_records()
        _, packet = self.read_assignment(assignment['id'])
        member = packet['slate_delivery']
        self.assertEqual(member['protocol'], DELIVERY_PROTOCOL)
        self.assertEqual([(work['version_id'], work['depth'], work['candidate_ids']) for work in member['cited_works']],
                         [(version, 'fulltext', ['candidate-1'])])
        delivery = member['cited_works'][0]['delivery']
        self.assertEqual((delivery['status'], delivery['request']),
                         ('delivered', {'id': 'cited-work:' + version, 'kind': 'source', 'version_id': version,
                                        'depth': 'fulltext'}))
        bodies = {entry['artifact']['sha256']: entry for entry in packet['evidence']}
        for reference in delivery['evidence']:
            self.assertEqual(bodies[reference['sha256']]['content'], self.artifacts.read(reference).decode('utf-8'))
        self.assertEqual(packet['source_deliveries'], [])
        bar = records['review_assignment']['bar-first']
        self.assertEqual((assignment['session_id'], assignment['history_assignment_id'], assignment['slot_id']),
                         (assignment['id'], None, protocol.context_repair.slot_id(records, bar)))
        request = protocol.build_assignment_request(records, self.artifacts, assignment)
        self.assertEqual(len(transport.logical_messages(request)), 1)
        self.assertIn(DELIVERY_PROTOCOL, assignment['prompt']['parameters']['problem_paragraphs'])
        self.assertIn(protocol.prompt_paragraphs.SLATE_DELIVERY_PROMPT, assignment['prompt']['text'])
        self.assertTrue(protocol._prompt_matches(assignment, packet=packet))

    def test_A2_a_slate_without_a_cited_work_continues_the_bar_session(self):
        self.record_candidate()
        self.observe_bars()
        assignment = self.assign('slate', 'first')
        _, packet = self.read_assignment(assignment['id'])
        self.assertNotIn('slate_delivery', packet)
        self.assertNotIn(DELIVERY_PROTOCOL, assignment['prompt']['parameters'].get('problem_paragraphs', []))
        self.assertEqual((assignment['session_id'], assignment['history_assignment_id']), ('bar-first', 'bar-first'))

    def test_A3_the_review_cites_the_delivered_text_and_not_a_text_of_the_bar_session(self):
        self.prepare_cited_slate()
        assignment = self.assign('slate', 'first')
        _, packet = self.read_assignment(assignment['id'])
        payload = self.answer(assignment, self.cite_everywhere(self.build_slate_output(['candidate-1']), self.supporting))
        self.assert_error('review_evidence_undelivered', lambda: self.mutate(decisions.record_value_review, payload))
        second = self.assign('slate', 'second')
        cited = self.cite_everywhere(self.build_slate_output(['candidate-1']), self.read_delivered_text(packet))
        review = self.mutate(decisions.record_value_review, self.answer(second, cited))['result']
        self.assertEqual(review['assignment_id'], 'slate-second')

    def test_A4_a_continuation_lists_the_cited_works_without_their_texts(self):
        version = self.prepare_cited_slate()
        first = self.assign('slate', 'first')
        _, first_packet = self.read_assignment(first['id'])
        request = self.build_slate_output(['candidate-1'])
        request['value']['status'] = 'unresolved'
        request['source_requests'] = [{'id': 'page', 'kind': 'inventory_page', 'page': 0}]
        self.answer(first, request)
        continued = self.assign('slate', 'first', identifier='slate-first-continued', prior_assignment_id=first['id'])
        _, packet = self.read_assignment(continued['id'])
        self.assertEqual(packet['slate_delivery'], {'protocol': DELIVERY_PROTOCOL, 'cited_works': [
            {'version_id': version, 'depth': 'fulltext', 'candidate_ids': ['candidate-1']}]})
        texts = {reference['sha256'] for reference in first_packet['slate_delivery']['cited_works'][0]['delivery']['evidence']}
        self.assertFalse(texts & {entry['artifact']['sha256'] for entry in packet['evidence']})
        self.assertEqual((continued['session_id'], continued['history_assignment_id']), (first['id'], first['id']))
        self.assertIn(DELIVERY_PROTOCOL, continued['prompt']['parameters']['problem_paragraphs'])
        self.assertTrue(protocol._prompt_matches(continued, packet=packet))

    def test_A5_one_entry_for_each_cited_work_with_its_depth_and_candidates(self):
        full = self.record_full_reading()
        self.mutate(record_reading, self.abstract_note(full['version_id'], note_id='read-abstract'))
        linked, _ = self.record_long_reading(2, 10)
        abstract = self.metadata(3)
        self.mutate(record_reading, self.abstract_note(abstract, note_id='read-abstract-only'))
        capture = self.read_records()['work'][linked]['fulltexts'][0]
        link = {'kind': 'source', 'link': self.link(linked, capture['source_id'], capture['text'], 'The long body.')}
        self.record_cited_candidate('candidate-1', [self.cite_reading('read-abstract')])
        self.record_cited_candidate('candidate-2', [self.cite_reading('read-full')])
        self.record_cited_candidate('candidate-3', [link, self.cite_reading('read-abstract-only')])
        self.observe_bars()
        _, packet = self.read_assignment(self.assign('slate', 'first')['id'])
        self.assertEqual([(work['version_id'], work['depth'], work['candidate_ids'])
                          for work in packet['slate_delivery']['cited_works']],
                         [(full['version_id'], 'fulltext', ['candidate-1', 'candidate-2']),
                          (linked, 'fulltext', ['candidate-3']), (abstract, 'abstract', ['candidate-3'])])
        delivered = packet['slate_delivery']['cited_works'][2]['delivery']
        self.assertEqual(delivered['status'], 'delivered')
        self.assertIn(delivered['evidence'][0]['sha256'], {entry['artifact']['sha256'] for entry in packet['evidence']})


class SlateDeliveryFormTests(SlateDeliveryCase):
    def test_C1_the_rules_v4_require_the_unit_of_each_cited_work(self):
        from codex_route_fixtures import record_codex_route
        from test_review_session_v4_turns import ProbeClock
        version = self.prepare_cited_slate()
        with patch.object(protocol, 'datetime', ProbeClock):
            record_codex_route(self, 'v4-route', model='gpt-6-astra', output_tokens=128000)
        with patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)):
            head = self.mutate(protocol.record_assignment, {'id': 'slate-first', 'route_id': 'v4-route',
                'dossier_id': None, 'role': 'slate', 'reviewer_id': 'first', 'author_id': 'problem-author',
                'context': {}})['result']
        self.assertEqual(head['session_chain']['first_assignment_id'], head['id'])
        _, inventory, _ = session.read_bound_session(self.artifacts, head)
        cited = [row for row in inventory['units'] if row['identity'].get('version_id') == version
                 and row['kind'] == 'fulltext']
        self.assertTrue(cited)
        self.assertTrue(all(row['required'] for row in cited), cited)

    def test_C2_a_slate_that_does_not_fit_enters_the_series_planner_without_history(self):
        """Decision D9: a refresh chain and a first slate that delivers a cited text reach the slate series planner
        with an empty history, and the planner starts a series for them on a route below their size; the parts
        protocol v3 reads each cited text as one unit. The planner's call ends the assignment here: the series turn
        itself belongs to lane slate-series-start-3."""
        from codex_route_fixtures import limit_codex_input, record_codex_route
        from research_harness import review_parts as parts, review_parts_slate as slate_parts
        from research_harness import review_parts_v3 as v3, review_route_capacity as capacity
        from test_review_output_profile import build_row

        class EnteredSeriesPlanner(Exception):
            pass
        calls = []
        original = slate_parts.can_start_series

        def enter(records, artifacts, value, route, packet, prompt, history=()):
            calls.append((value['context'], list(history), original(records, artifacts, value, route, packet, prompt,
                                                                    history)))
            raise EnteredSeriesPlanner()
        self.record_candidate()
        self.observe_bars()
        self.observe('slate', 'first', self.build_slate_output(['candidate-1']))
        version, reading_id = self.record_long_reading(1, 12000)
        self.record_cited_candidate('candidate-1-revised', [self.cite_reading(reading_id)])
        record_codex_route(self, 'local', output_tokens=128000)
        limit_codex_input(self, 20000)
        priors = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                  for kind in ('single', 'read', 'combine', 'check', 'final')]
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', (v3.PROTOCOL, parts.PROTOCOL)), \
                patch.object(capacity, 'read_profile_priors', return_value=priors), \
                patch.dict(slate_parts.ADAPTER, can_start_series=enter):
            for reviewer in ('first', 'second'):
                with self.assertRaises(EnteredSeriesPlanner):
                    self.mutate(protocol.record_assignment, {'id': 'slate-' + reviewer + '-local', 'route_id': 'local',
                        'dossier_id': None, 'role': 'slate', 'reviewer_id': reviewer, 'author_id': 'problem-author',
                        'context': {}})
        self.assertEqual(calls, [({'session_refresh': {'assignment_id': 'slate-first'}}, [], True), ({}, [], True)])
        records = self.read_records()
        packet = protocol.build_packet(records, self.artifacts, None, 'slate', 'second')
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            admitted = v3.admit_packet(records, self.artifacts, records['review_route']['local'], packet,
                                       'problem_slate')
        self.assertEqual(admitted['state'], 'accept', admitted)
        [work] = packet['slate_delivery']['cited_works']
        self.assertEqual(work['version_id'], version)
        for text in work['delivery']['evidence']:
            units = [unit for unit in admitted['inventory']['units'] if unit['citations'] == [text]]
            self.assertEqual(len(units), 1, text)

    def test_A6_an_html_capture_of_a_cited_work_is_delivered_once(self):
        """P3-1: the text-only delivery of an HTML full text holds its extraction and not the HTML original as well."""
        version = self.prepare_cited_slate()
        _, packet = self.read_assignment(self.assign('slate', 'first')['id'])
        [work] = packet['slate_delivery']['cited_works']
        [capture] = self.read_records()['work'][version]['fulltexts']
        self.assertEqual(capture['original']['media_type'].split(';')[0], 'text/html')
        self.assertEqual([reference['sha256'] for reference in work['delivery']['evidence']], [capture['text']['sha256']])
        self.assertEqual({sha256 for row in work['delivery']['source_provenance']
                          for sha256 in row.get('delivered_sha256', [])}, {capture['text']['sha256']})
        bodies = {entry['artifact']['sha256'] for entry in packet['evidence']}
        self.assertIn(capture['text']['sha256'], bodies)
        self.assertNotIn(capture['original']['sha256'], bodies)
