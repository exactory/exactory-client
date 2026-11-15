"""Session citations use authenticated turns even after replay loses their bodies."""

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, probe_codex_route, record_codex_route
import test_research_problem_reviews as problem_fixtures
import test_review_delivered_evidence as delivery_fixtures
from test_reviewer_excerpt_sessions import build_body, build_lemma
from research_harness import research_decisions as decisions, review_excerpts
from research_harness import review_protocol as protocol, review_refusal, review_transport as transport
from research_harness.evidence import digest
from research_harness.errors import ResearchError


class SessionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.case = problem_fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        record_codex_route(self.case, model='gpt-6-astra')
        # Retain the reduced-history fitting budget on a route that can admit
        # the projected replay. The dispatch guard keeps the real route limits.
        fitting_limit = patch.object(transport, 'local_input_limit', return_value=105000)
        fitting_limit.start()
        self.addCleanup(fitting_limit.stop)
        self.versions = []
        for index in (1, 2, 3):
            version = self.case.metadata(index)
            capture = self.case.capture(version, pdf_text=build_body(index))
            if index == 1:
                from research_harness.literature import import_bundle
                from research_harness.reading import record_reading
                bundle = self.case.bundle(version, capture=capture)
                self.case.mutate(import_bundle, bundle)
                self.case.mutate(record_reading, self.case.full_note(bundle))
            self.versions.append(version)
        self.requests = [dict(id='source-' + str(index), kind='source', version_id=version, depth='fulltext')
                         for index, version in enumerate(self.versions, 1)]

    def assign_turn(self, identifier, parent=None):
        return self.case.assign('bar', 'first', identifier=identifier,
                                **({'prior_assignment_id': parent} if parent else {}))

    def invoke_turn(self, identifier, requests, *, excerpts=None):
        output = self.case.build_review_output('bar')
        output['value'].update(status='unresolved', evidence=[])
        output['source_requests'] = requests
        if excerpts is not None:
            output['excerpts'] = excerpts
        return invoke_codex(self.case, identifier, output)

    def read_packet(self, identifier):
        assignment = self.case.read_records()['review_assignment'][identifier]
        return protocol._read_json(self.case.artifacts, assignment['packet'])

    def read_request(self, identifier):
        attempt = self.case.read_records()['review_attempt']['attempt-' + identifier]
        return protocol._read_json(self.case.artifacts, attempt['request'])

    def build_source_reference(self, index=0, *, excerpt=False):
        version = self.versions[index]
        capture = self.case.read_records()['work'][version]['fulltexts'][0]
        link = self.case.link(version, capture['source_id'], capture['text'],
                              quote=build_lemma(index + 1) if excerpt else None, kind='span')
        return dict(kind='source', link=link)

    def build_reduced_session(self):
        self.assign_turn('turn-1')
        self.invoke_turn('turn-1', self.requests)
        parent = 'turn-1'
        for number in range(2, 8):
            identifier = 'turn-' + str(number)
            self.assign_turn(identifier, parent)
            packet = self.read_packet(identifier)
            if not packet['pending_source_requests']:
                return identifier
            self.invoke_turn(identifier, [], excerpts=[
                dict(version_id=self.versions[0], text=build_lemma(1), purpose='The field comparator.'),
                dict(version_id=self.versions[0], text='This invented sentence is absent from the source text.',
                     purpose='An unmatched copy does not count.')])
            parent = identifier
        self.fail('Synthetic session did not finish its deliveries.')

    def test_final_credits_the_actual_delivering_turn_after_history_reduction(self):
        # Real shape of 20261111235418-bar-final-refused-for-text-delivered-in-reduced-history, T1.
        final_id = self.build_reduced_session()
        output = self.case.build_review_output('bar')
        output['value']['evidence'] = [self.build_source_reference()]
        invoke_codex(self.case, final_id, output)
        records = self.case.read_records()
        assignment = records['review_assignment'][final_id]
        attempt = records['review_attempt']['attempt-' + final_id]
        from research_harness.review_delivered_evidence import read_attempt_delivery
        actual = read_attempt_delivery(records, self.case.artifacts, assignment, attempt)
        body = output['value']['evidence'][0]['link']['artifact']
        self.assertNotIn(body['sha256'], actual['current_input']['artifacts'])
        finding = review_refusal.classify_observed_output(records, self.case.artifacts, attempt['id'])
        self.assertIsNone(finding and finding['code'])
        payload = dict(output, id='final-review', dossier_id=None, assignment_id=final_id)
        saved = self.case.mutate(decisions.record_value_review, payload)['result']
        self.assertEqual(saved['assignment_id'], final_id)
        self.assertEqual(decisions.find_undelivered_review_references(self.case.read_records(),
                                                                    self.case.artifacts, saved), [])

    def test_final_citations_follow_the_union_of_two_delivered_parts(self):
        # N2, 20261114054500-bar-cannot-be-recorded-after-instalment-deliveries.
        # Keep a held tail so the negative citation is valid but was not delivered.
        bibliography = 'References: none.'
        content = 'a' * 60000 + 'b' * 37404 + 'c' * (2596 - len(bibliography)) + bibliography
        version = self.case.metadata(4)
        captured = self.case.capture(version, pdf_text=content)
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading
        bundle = self.case.bundle(version, capture=captured, bundle_id='split-bundle')
        self.case.mutate(import_bundle, bundle)
        self.case.mutate(record_reading, self.case.full_note(bundle, note_id='split-reading'))
        readings = copy.deepcopy(self.case.read_records()['reading'])
        capture = self.case.read_records()['work'][version]['fulltexts'][0]
        request = dict(id='split-source', kind='source', version_id=version, depth='fulltext')
        limit_codex_input(self.case, 500000)
        spans = [(0, 60000), (60000, 97404), (97404, 100000)]
        with patch.object(review_excerpts, 'compute_part_spans', return_value=spans):
            self.assign_turn('request')
            self.invoke_turn('request', [request])
            with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 60000):
                self.assign_turn('first-part', 'request')
            self.invoke_turn('first-part', [])
            with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 37404):
                self.assign_turn('second-part', 'first-part')
            output = self.case.build_review_output('bar')
            output['value'].update(status='unresolved', evidence=[])
            output['settled_requests'] = [request['id']]
            invoke_codex(self.case, 'second-part', output)
            self.assign_turn('final', 'second-part')
        for identifier, expected in (('first-part', (0, 60000)), ('second-part', (60000, 97404))):
            packet = review_excerpts.read_sent_packet(self.read_request(identifier))
            delivered = packet['source_deliveries']
            self.assertEqual(len(delivered), 1)
            self.assertEqual((delivered[0]['part']['start'], delivered[0]['part']['end']), expected)
            self.assertNotIn(capture['text'], [entry['artifact'] for entry in packet['evidence']])
        packet = self.read_packet('final')
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertEqual(packet['source_deliveries'], [])
        output = self.case.build_review_output('bar')
        output['limitations'].extend(packet['settled_source_requests'][0]['limitations'])
        reference = dict(kind='source', link=dict(version_id=version, source_id=capture['source_id'],
            artifact=capture['text'], locator=dict(kind='span', start=0, end=97404,
                sha256=hashlib.sha256(content[:97404].encode()).hexdigest())))
        output['value']['evidence'] = [reference]
        refused = copy.deepcopy(output)
        refused['value']['evidence'][0]['link']['locator'].update(end=97405,
            sha256=hashlib.sha256(content[:97405].encode()).hexdigest())
        invoke_codex(self.case, 'final', refused, 'undelivered-tail')
        finding = review_refusal.classify_observed_output(self.case.read_records(), self.case.artifacts,
                                                         'undelivered-tail')
        self.assertEqual(finding['code'], 'review_evidence_undelivered')
        payload = dict(refused, id='refused-final', dossier_id=None, assignment_id='final')
        with self.assertRaises(ResearchError) as error:
            self.case.mutate(decisions.record_value_review, payload)
        self.assertEqual(error.exception.code, 'review_evidence_undelivered')
        invoke_codex(self.case, 'final', output)
        payload = dict(output, id='accepted-final', dossier_id=None, assignment_id='final')
        saved = self.case.mutate(decisions.record_value_review, payload)['result']
        self.assertEqual(saved['payload']['value']['evidence'], [reference])
        self.assertEqual(decisions.find_undelivered_review_references(self.case.read_records(),
                                                                    self.case.artifacts, saved), [])
        self.assertEqual(self.case.read_records()['reading'], readings)

    def test_chain_view_keeps_current_input_separate_and_excludes_undelivered_sources(self):
        final_id = self.build_reduced_session()
        output = self.case.build_review_output('bar')
        # A fourth captured source exists locally, but no turn requested or delivered its text.
        version = self.case.metadata(4)
        self.case.capture(version, pdf_text=build_body(4))
        self.versions.append(version)
        output['value']['evidence'] = [self.build_source_reference(3)]
        invoke_codex(self.case, final_id, output)
        records = self.case.read_records()
        finding = review_refusal.classify_observed_output(records, self.case.artifacts, 'attempt-' + final_id)
        self.assertEqual(finding['code'], 'review_evidence_undelivered')
        from research_harness.review_delivered_evidence import read_chain_delivery
        delivery = read_chain_delivery(records, self.case.artifacts, records['review_assignment'][final_id],
                                       attempt=records['review_attempt']['attempt-' + final_id])
        self.assertIn(self.build_source_reference()['link']['artifact']['sha256'], delivery['artifacts'])
        self.assertNotIn(output['value']['evidence'][0]['link']['artifact']['sha256'], delivery['artifacts'])

    def test_notice_lists_lost_texts_with_verified_excerpts_and_the_final_can_cite_them(self):
        # T3 of the same incident: the final reads a notice and cites a checked excerpt.
        final_id = self.build_reduced_session()
        packet = self.read_packet(final_id)
        notice = packet.get('delivery_notice', {})
        self.assertEqual(notice.get('protocol'), 'exactory-review-delivery-notice-v1')
        self.assertEqual([row['version_id'] for row in notice['sources']], [self.versions[0], self.versions[2]])
        first = notice['sources'][0]
        self.assertEqual(first['turns'], [dict(assignment_id='turn-2', attempt_id='attempt-turn-2',
                                              characters=len(build_body(1)))])
        self.assertEqual(first['characters'], len(build_body(1)))
        self.assertEqual(first['verified_excerpts'], 1)
        self.assertEqual(notice['sources'][1]['verified_excerpts'], 0)
        assignment = self.case.read_records()['review_assignment'][final_id]
        self.assertIn('exactory-review-delivery-notice-v1', assignment['prompt']['parameters']['problem_paragraphs'])
        self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
        output = self.case.build_review_output('bar')
        output['value']['evidence'] = [self.build_source_reference(excerpt=True)]
        invoke_codex(self.case, final_id, output)
        finding = review_refusal.classify_observed_output(self.case.read_records(), self.case.artifacts,
                                                         'attempt-' + final_id)
        self.assertIsNone(finding and finding['code'])

    def test_an_intact_continuation_keeps_its_packet_prompt_and_request_bytes(self):
        self.assign_turn('turn-1')
        self.invoke_turn('turn-1', self.requests[:1])
        # Capture the old builder's complete result, then compare a prospective call.
        from research_harness import review_delivery_notice
        from research_harness.review_source_batches import plan_packet
        captured = []
        def build_without_notice(*args):
            records, artifacts, route, _, packet, prompt, history = args
            captured.append((self.case.read_records(), self.case.artifacts, copy.deepcopy(route),
                             copy.deepcopy(args[3]), copy.deepcopy(packet), copy.deepcopy(prompt), copy.deepcopy(history)))
            return plan_packet(records, artifacts, route, packet, prompt, history), prompt, history
        with patch.object(review_delivery_notice, 'prepare_notice', side_effect=build_without_notice):
            self.assign_turn('turn-2', 'turn-1')
        assignment = self.case.read_records()['review_assignment']['turn-2']
        packet = self.read_packet('turn-2')
        history = protocol.read_history(self.case.artifacts, assignment)
        result, prompt, rebuilt_history = review_delivery_notice.prepare_notice(*captured[0])
        self.assertEqual(result, packet)
        self.assertEqual(prompt, assignment['prompt'])
        self.assertEqual(rebuilt_history, history)
        self.assertFalse('delivery_notice' in packet)
        invoke_codex(self.case, 'turn-2', self.case.build_review_output('bar'))
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-turn-2']
        saved = self.case.artifacts.read(attempt['request'])
        self.assertEqual(json.loads(saved), protocol.build_assignment_request(records, self.case.artifacts, assignment))
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt))

    def test_a_fresh_context_repair_drops_the_old_sessions_notice(self):
        final_id = self.build_reduced_session()
        self.assertIn('delivery_notice', self.read_packet(final_id))
        self.invoke_turn(final_id, [dict(id='inventory', kind='inventory_page', page=0)])
        self.case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id=final_id,
            kind='contamination', source='author_history', reason='A synthetic context exposure.',
            evidence=[self.case.supporting]))
        probe_codex_route(self.case, 'repair-probe')
        from research_harness.review_context_repair import prepare_repair
        from research_harness.review_prompt_paragraphs import select_prompt_parameters
        records = self.case.read_records()
        value = dict(id='repair', role='bar', route_id='route', dossier_id=None, reviewer_id='replacement',
            author_id='author', subject=records['review_assignment'][final_id]['subject'], context=dict(
                context_repair=dict(assignment_id=final_id, event_ids=['exposure'], probe_id='repair-probe',
                reason='A fresh independent input reassesses the same field.', evidence=[self.case.supporting])))
        repair = prepare_repair(records, self.case.artifacts, value)
        packet = protocol._repair_packet(records, self.case.artifacts, value, repair)
        self.assertNotIn('delivery_notice', packet)
        self.assertNotIn('exactory-review-delivery-notice-v1',
                         select_prompt_parameters('bar', packet).get('problem_paragraphs', []))


class SessionDeliveryProjectionTests(unittest.TestCase):
    def setUp(self):
        self.case = delivery_fixtures.DeliveredEvidenceTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def record_session(self, packet):
        case = self.case
        prior, selected = case.record_attempt(packet, 'prior', session_id='session')
        stub = transport._elide_history_packet(case.build_message(packet))
        final, attempt = case.record_attempt(case.build_packet(role='bar'), 'final', history=[stub],
                                             history_assignment_id='prior', session_id='session')
        return prior, selected, final, attempt

    def read_chain(self, final, attempt, *, ready=True):
        selected = self.case.records['review_attempt']['prior-attempt']
        with patch.object(protocol, 'assignment_state', return_value=dict(ready=ready, attempt_id=selected['id'])):
            return decisions.read_review_citation_delivery(self.case.records, self.case.artifacts, final, attempt=attempt)

    def test_digest_stub_recovers_actual_body_and_record_without_changing_stored_bytes(self):
        case = self.case
        body = case.artifacts.put(b'Exact synthetic body in an authenticated prior turn.', 'text/plain')
        record = dict(id='value', digest='a' * 64, motivation=['A synthetic field fact.'])
        packet = case.build_packet(role='bar', evidence=protocol._evidence(case.artifacts, [body]),
                                   problem_evidence=dict(value=record))
        _, _, final, attempt = self.record_session(packet)
        before = copy.deepcopy(case.records)
        delivered = self.read_chain(final, attempt)
        self.assertIn(body, delivered['artifacts'].values())
        self.assertEqual(protocol.find_carried_record({}, delivered['carried_records'],
            dict(kind='record', record_kind='problem_value', id='value'), frozen=True), (record, []))
        self.assertEqual(case.read_delivery(final, attempt)['current_input']['artifacts'], {})
        self.assertEqual(case.records, before)
        self.assertTrue(protocol.authenticate_attempt(case.records, case.artifacts, final, attempt))

    def test_unverified_imported_and_foreign_turns_supply_no_missing_body(self):
        case = self.case
        body = case.artifacts.put(b'Content delivered only by the prior selected turn.', 'text/plain')
        prior, selected, final, attempt = self.record_session(case.build_packet(
            role='bar', evidence=protocol._evidence(case.artifacts, [body])))
        self.assertIn(body, self.read_chain(final, attempt)['artifacts'].values())
        self.assertEqual(self.read_chain(final, attempt, ready=False)['artifacts'], {})
        for change in (dict(origin='imported'), dict(status='failed')):
            with self.subTest(change=change):
                case.records['review_attempt'][selected['id']] = dict(selected, **change)
                self.assertEqual(self.read_chain(final, attempt)['artifacts'], {})
        case.records['review_attempt'][selected['id']] = selected
        for change in (dict(session_id='another-session'), dict(reviewer_id='another-reviewer')):
            with self.subTest(change=change):
                case.records['review_assignment'][prior['id']] = dict(prior, **change)
                self.assertEqual(self.read_chain(final, attempt)['artifacts'], {})
        case.records['review_assignment'][prior['id']] = prior
        case.records['review_attempt'][selected['id']] = dict(selected, request_digest='f' * 64)
        with self.assertRaises(ResearchError) as error:
            self.read_chain(final, attempt)
        self.assertEqual(error.exception.code, 'review_invocation_unverified')

    def test_counted_turns_use_the_existing_normalized_reviewer_identity(self):
        case = self.case
        body = case.artifacts.put(b'Content delivered by the same normalized reviewer.', 'text/plain')
        _, _, final, attempt = self.record_session(case.build_packet(role='bar', reviewer_id=' Reviewer ',
            evidence=protocol._evidence(case.artifacts, [body])))
        self.assertIn(body, self.read_chain(final, attempt)['artifacts'].values())

    def test_partial_body_keeps_its_original_locator_after_history_becomes_a_stub(self):
        case = self.case
        original = case.artifacts.put(b'Before. The exact delivered passage. After.', 'text/plain')
        excerpt = case.artifacts.put(b'The exact delivered passage.', 'text/plain')
        from research_harness.bar_sources import descriptor
        mapping = dict(version_id='version', origin=dict(artifact_descriptor=descriptor(original),
            source_id='source', locator=dict(kind='span', start=8, end=36, sha256=excerpt['sha256'])), excerpt=excerpt)
        packet = case.build_packet(role='bar', evidence=protocol._evidence(case.artifacts, [excerpt]),
                                   prior_context=[mapping])
        _, _, final, attempt = self.record_session(packet)
        view = self.read_chain(final, attempt)
        self.assertEqual(view['source_mappings'], [mapping])
        for start, end, received in ((8, 36, True), (0, 36, False), (8, 42, False)):
            reference = dict(kind='source', link=dict(version_id='version', source_id='source', artifact=original,
                                                     locator=dict(kind='span', start=start, end=end)))
            self.assertEqual(not decisions.find_review_undelivered_references(
                case.records, case.artifacts, [reference], view), received)

    def test_part_union_citations_preserve_v2_and_stored_inputs(self):
        from research_harness import bar_sources
        from research_harness.review_delivered_evidence import project_delivery_view
        case = self.case
        content = 'First part. Second part.'
        original = case.artifacts.put(content.encode(), 'text/plain')
        request = dict(id='split', kind='source', version_id='version', depth='fulltext')
        capture = dict(source_id='source', text=original)
        parts = [bar_sources._describe_part(case.artifacts, request, capture, content, 'delivered', index,
                  [(0, 12), (12, 24)]) for index in (1, 2)]
        packet = case.build_packet(role='bar', source_deliveries=parts,
            evidence=protocol._evidence(case.artifacts, [part['excerpt'] for part in parts]))
        prior, selected, final, attempt = self.record_session(packet)
        packet_bytes = case.artifacts.read(prior['packet'])
        request_bytes = case.artifacts.read(selected['request'])
        prompt = copy.deepcopy(prior['prompt'])
        view = self.read_chain(final, attempt)
        reference = dict(kind='source', link=dict(version_id='version', source_id='source', artifact=original,
            locator=dict(kind='span', start=0, end=24, sha256=original['sha256'])))
        self.assertNotIn('segments', view)
        self.assertEqual(view['packets'][0], packet)
        self.assertEqual(decisions.find_review_undelivered_references(
            case.records, case.artifacts, [reference], view),
            [dict(sha256=original['sha256'], locator=reference['link']['locator'])])
        current = case.build_packet(role='bar', source_rules=bar_sources.PROTOCOL_V3,
            source_deliveries=parts, evidence=packet['evidence'])
        (case.artifacts.root / original['path']).unlink()
        view = project_delivery_view(case.records, case.artifacts, [current])
        self.assertEqual(view['packets'], [current])
        self.assertEqual(decisions.find_review_undelivered_references(
            case.records, case.artifacts, [reference], view), [])
        other_version = copy.deepcopy(reference)
        other_version['link']['version_id'] = 'another-version'
        self.assertTrue(decisions.find_review_undelivered_references(
            case.records, case.artifacts, [other_version], view))
        older = project_delivery_view(case.records, case.artifacts, [current, packet])
        self.assertEqual(decisions.find_review_undelivered_references(
            case.records, case.artifacts, [reference], older),
            [dict(sha256=original['sha256'], locator=reference['link']['locator'])])
        self.assertEqual(case.artifacts.read(prior['packet']), packet_bytes)
        self.assertEqual(case.artifacts.read(selected['request']), request_bytes)
        self.assertEqual(prior['prompt'], prompt)
        self.assertTrue(protocol.authenticate_attempt(case.records, case.artifacts, prior, selected))


class DeliveryNoticeProjectionTests(unittest.TestCase):
    def test_complete_visible_notice_keeps_frozen_history_and_replays_its_stored_request(self):
        import test_research_complete_source_continuation as complete_sources
        from research_harness import review_source_complete as complete
        from research_harness.review_bodies import read_history
        fixture = complete_sources.NativeCompleteSourceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.assign('ordinary')
        fixture.invoke('ordinary', fixture.output([fixture.second_request]))
        fixture.assign('unavailable', prior='ordinary')
        fixture.invoke('unavailable', fixture.output([]))
        assignment = fixture.assign('complete', prior='unavailable', source_delivery=complete.MODE)
        packet = fixture.packet(assignment)
        history = read_history(fixture.artifacts, assignment)
        self.assertEqual(packet[complete.MARKER]['history_digest'], digest(history))
        self.assertEqual(packet['delivery_notice']['protocol'], 'exactory-review-delivery-notice-v1')
        self.assertTrue(packet['delivery_notice']['sources'])
        fixture.invoke('complete', fixture.output([fixture.second_request]))
        records = fixture.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-complete']
        request = json.loads(fixture.artifacts.read(attempt['request']))
        self.assertEqual(transport.request_body(records['review_route']['route'], assignment['prompt']['text'],
                                               packet, history), request)
        self.assertTrue(protocol.authenticate_attempt(records, fixture.artifacts, assignment, attempt))

    def test_complete_visible_encoding_cycle_uses_the_existing_capacity_refusal(self):
        from research_harness import bar_sources, review_delivery_notice as notice
        from research_harness import review_exact_text, review_exact_text_v2
        from research_harness import review_source_batches as batches, review_source_complete as complete
        from test_review_source_complete import CompleteSourceTests
        from test_review_html_delivery import ROUTE
        fixture = CompleteSourceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        history = []
        manifest = dict(complete.selection(fixture.records, fixture.artifacts, [fixture.delivery]),
            protocol=complete.PROTOCOL, history_digest=digest(history), restored_evidence=[],
            historical_search_elisions=[], source_requests=[fixture.request])
        packet = dict(protocol=bar_sources.PROTOCOL, role='result', reviewer_id='r', dossier_id='d',
            continuation=dict(assignment_id='prior'), source_deliveries=[fixture.delivery],
            pending_source_requests=[], padding='x' * 71000, **{complete.MARKER: manifest})
        # Repeated history shapes make v2 shorter. Padding tunes v1 to the
        # boundary where its own rendered-size field gains a digit.
        packet['scientific_history'] = [dict(assignment_id='history-' + str(index),
            packet=dict(digest=digest(str(index)), scientific_payload='Synthetic payload ' + str(index)),
            observed_output=dict(finding='Synthetic finding ' + str(index))) for index in range(50)]
        packet['evidence'] = protocol._evidence(fixture.artifacts, packet)
        packet['digest'] = digest(packet)
        bodies = [dict(version_id=fixture.version, depth='fulltext', text_sha256=reference['sha256'],
            artifact=reference, span=[0, len(fixture.artifacts.read(reference).decode())],
            turn=dict(assignment_id='prior', attempt_id='attempt-prior'))
            for reference in (fixture.html['text'], fixture.pdf['text'])]
        assignment = dict(id='next', role='result', reviewer_id='r', session_id='s', history_assignment_id='prior')
        records = dict(fixture.records, review_assignment={'prior': {'id': 'prior'}})
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation')
        notice_prompt = notice._append_notice_prompt(prompt, 'result')
        rows = notice._describe_missing_sources(bodies, [], packet)
        with patch.object(protocol, 'source_delivery_packet', return_value={}), \
                patch.object(notice, '_collect_source_bodies', return_value=bodies), \
                patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 100000):
            for _ in range(4):
                reserved = copy.deepcopy(packet)
                reserved['delivery_notice'] = dict(protocol='exactory-review-delivery-notice-v1', sources=rows)
                reserved = batches.plan_packet(records, fixture.artifacts, ROUTE, reserved, notice_prompt, history)
                request, actual = notice._read_fitted_request(
                    records, fixture.artifacts, ROUTE, reserved, notice_prompt, history)
                missing = notice._describe_missing_sources(bodies, actual, actual[-1])
                trimmed = copy.deepcopy(reserved)
                trimmed['delivery_notice']['sources'] = missing
                trimmed.pop('digest')
                trimmed['digest'] = digest(trimmed)
                raw = transport.request_body(ROUTE, notice_prompt['text'], trimmed, history)
                logical = transport.logical_messages(raw)
                _, sources = complete.project(trimmed, history)
                forced = dict(raw, input=[dict(role='user', content=review_exact_text.encode(logical, sources))])
                length = batches.measure_request(ROUTE, forced)['rendered_characters']
                if length == 100000:
                    break
                packet['padding'] = 'x' * (len(packet['padding']) + 100000 - length)
                packet.pop('digest')
                packet['digest'] = digest(packet)
            self.assertEqual(length, 100000)
            self.assertEqual(len(missing), 1)
            self.assertTrue(review_exact_text_v2.is_encoded(request['input'][0]['content']))
            self.assertLess(batches.measure_request(ROUTE, request)['rendered_characters'], 100000)
            # Retain the reservation's smaller v2 measurement, then show that
            # each real measurement update switches the encoding again.
            steps = []
            for _ in range(3):
                raw = transport.request_body(ROUTE, notice_prompt['text'], trimmed, history)
                measured = batches.measure_request(ROUTE, raw)
                steps.append((review_exact_text_v2.is_encoded(raw['input'][0]['content']), measured))
                trimmed[batches.MARKER]['measurement'] = measured
                trimmed.pop('digest')
                trimmed['digest'] = digest(trimmed)
            self.assertEqual([version for version, _ in steps], [False, True, False])
            self.assertEqual(steps[0][1], steps[2][1])
            fit = notice._read_fitted_request
            trials = []
            def fit_with_bound(*args):
                trials.append(None)
                if len(trials) > 8:
                    self.fail('The complete-visible notice did not stabilize or refuse at its encoding boundary.')
                return fit(*args)
            with patch.object(notice, '_read_fitted_request', side_effect=fit_with_bound):
                with self.assertRaises(ResearchError) as caught:
                    notice.prepare_notice(records, fixture.artifacts, ROUTE, assignment, packet, prompt, history)
            self.assertEqual(caught.exception.code, 'review_input_too_large')
            self.assertEqual(caught.exception.details['limiting_component'], 'complete_visible_closure')
            self.assertFalse(caught.exception.details['measurement_stable'])

    def test_parts_protocols_keep_their_continuations_without_a_session_notice(self):
        from research_harness import review_delivery_notice as notice, review_parts, review_problem_parts
        case = delivery_fixtures.DeliveredEvidenceTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        body = case.artifacts.put(b'A source body owned by the existing parts protocol.', 'text/plain')
        parent_packet = case.build_packet(role='bar', evidence=protocol._evidence(case.artifacts, [body]),
            source_deliveries=[dict(request=dict(id='source', kind='source', depth='abstract', version_id='source'),
                                    status='delivered', evidence=[body])])
        parent, attempt = case.record_attempt(parent_packet, 'prior', session_id='session')
        assignment = dict(id='new', role='result', reviewer_id=parent['reviewer_id'], session_id='session',
                          history_assignment_id=parent['id'])
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                                           evidence_parts='part')
        history = [transport._elide_history_packet(case.build_message(parent_packet))]
        for key, version in ((review_problem_parts.KEY, review_problem_parts.PROTOCOL),
                             (review_parts.KEY, review_parts.PROTOCOL)):
            with self.subTest(key=key):
                packet = case.build_packet(role='result', continuation=dict(assignment_id=parent['id']),
                                           **{key: dict(protocol=version, stage='part')})
                with patch.object(protocol, 'assignment_state', return_value=dict(ready=True, attempt_id=attempt['id'])):
                    result = notice.prepare_notice(case.records, case.artifacts, case.route, assignment,
                                                   packet, prompt, history)
                self.assertEqual(result, (packet, prompt, history))

    def test_restoration_at_a_measurement_digit_boundary_stabilizes_the_notice(self):
        from research_harness import review_delivery_notice as notice
        from research_harness.review_source_batches import MARKER, measure_request, plan_packet
        case = delivery_fixtures.DeliveredEvidenceTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        earlier_text = 'Earlier source text. ' * 60
        earlier = case.artifacts.put(earlier_text.encode(), 'text/plain')
        parent_packet = case.build_packet(role='bar', evidence=protocol._evidence(case.artifacts, [earlier]),
            source_deliveries=[dict(request=dict(id='earlier', kind='source', depth='abstract', version_id='earlier'),
                                    status='delivered', evidence=[earlier])], pending_source_requests=[])
        parent, attempt = case.record_attempt(parent_packet, 'prior', session_id='session')
        current = case.artifacts.put(b'A current requested source. ' * 10, 'text/plain')
        repeated = case.artifacts.put(b'R' * 20000, 'text/plain')
        packet = case.build_packet(role='bar', evidence=protocol._evidence(case.artifacts, [current, repeated]),
            continuation=dict(assignment_id=parent['id']), pending_source_requests=[],
            source_deliveries=[dict(request=dict(id='current', kind='source', depth='abstract', version_id='current'),
                                    status='delivered', evidence=[current])],
            previously_supplied_evidence=[repeated], scientific_history=[dict(assignment_id='history',
                packet=dict(digest='a' * 64, scientific_payload='history ' * 5000),
                observed_output=dict(finding='An unresolved synthetic observation.'))])
        assignment = dict(id='new', role='bar', reviewer_id=packet['reviewer_id'], session_id='session',
                          history_assignment_id=parent['id'])
        repeated_index = next(index for index, entry in enumerate(packet['evidence'])
                              if entry['artifact'] == repeated)
        route = dict(case.route, adapter='codex_cli_v1', endpoint='codex://local')
        prompt = protocol.canonical_prompt('bar', orchestration='problem_creation')
        history = [transport._elide_history_packet(case.build_message(parent_packet))]
        selected = dict(ready=True, attempt_id=attempt['id'])
        with patch.object(protocol, 'assignment_state', return_value=selected), \
                patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 10000):
            # Tune only the input: its restored body ends exactly at 10,000
            # characters while the stored rendered-size field still has four digits.
            rows = notice._describe_missing_sources(notice._collect_source_bodies(
                case.records, case.artifacts, assignment), [], packet)
            notice_prompt = notice._append_notice_prompt(prompt, 'bar')
            for _ in range(4):
                trial = copy.deepcopy(packet)
                trial['delivery_notice'] = dict(protocol='exactory-review-delivery-notice-v1', sources=rows)
                trial.pop('digest')
                trial['digest'] = digest(trial)
                planned = plan_packet(case.records, case.artifacts, route, trial, notice_prompt, history)
                raw = transport.request_body(route, notice_prompt['text'], planned, history)
                supplied = review_excerpts.read_sent_packet(raw)
                supplied['evidence'][repeated_index] = packet['evidence'][repeated_index]
                restored = transport._build_local_request(route, notice_prompt['text'], supplied,
                                                          transport.logical_messages(raw)[:-1])
                length = len(transport._local_adapter(route).packet_text(restored))
                if length == 10000:
                    break
                repeated = case.artifacts.put(b'R' * (repeated['size'] + 10000 - length), 'text/plain')
                packet['evidence'][repeated_index] = protocol._evidence(case.artifacts, [repeated])[0]
                packet['previously_supplied_evidence'] = [repeated]
                packet.pop('digest')
                packet['digest'] = digest(packet)
            self.assertEqual(length, 10000)
            fit = notice._read_fitted_request
            trials = []
            def fit_with_bound(*args):
                trials.append(None)
                if len(trials) > 8:
                    self.fail('The notice did not stabilize for one body at an integer-width boundary.')
                return fit(*args)
            with patch.object(notice, '_read_fitted_request', side_effect=fit_with_bound):
                planned, selected_prompt, fitted_history = notice.prepare_notice(
                    case.records, case.artifacts, route, assignment, packet, prompt, history)
            request, actual = fit(case.records, case.artifacts, route, planned, selected_prompt, fitted_history)
        self.assertLessEqual(len(transport._local_adapter(route).packet_text(request)), 10000)
        self.assertEqual(planned[MARKER]['measurement'], measure_request(route, request))
        self.assertEqual(planned['delivery_notice']['sources'][0]['characters'], len(earlier_text))
        self.assertEqual(actual[-1]['evidence'][repeated_index]['content'], '')

    def test_overlapping_and_repeated_parts_count_only_missing_original_characters(self):
        from research_harness.review_delivery_notice import _describe_missing_sources
        case = delivery_fixtures.DeliveredEvidenceTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        first = case.artifacts.put(b'abcdefghij', 'text/plain')
        second = case.artifacts.put(b'fghijklmno', 'text/plain')
        original = case.artifacts.put(b'abcdefghijklmno', 'text/plain')
        base = dict(version_id='version', depth='fulltext', text_sha256=original['sha256'],
                    turn=dict(assignment_id='first', attempt_id='first-attempt'))
        bodies = [dict(base, artifact=first, span=[0, 10]),
                  dict(base, artifact=second, span=[5, 15],
                       turn=dict(assignment_id='second', attempt_id='second-attempt')),
                  dict(base, artifact=first, span=[0, 10])]
        packet = dict(reviewer_excerpts=dict(excerpts=[
            dict(version_id='version', status='verified', artifact_descriptor=original),
            dict(version_id='version', status='unmatched', artifact_descriptor=original),
            dict(version_id='other', status='verified', artifact_descriptor=original)]))
        [row] = _describe_missing_sources(bodies, [], packet)
        self.assertEqual((row['characters'], row['verified_excerpts']), (15, 1))
        self.assertEqual(row['turns'], [dict(assignment_id='first', attempt_id='first-attempt', characters=10),
                                       dict(assignment_id='second', attempt_id='second-attempt', characters=5)])
        held = dict(evidence=[dict(artifact=second, encoding='utf-8', content='fghijklmno')])
        [row] = _describe_missing_sources(bodies, [held], packet)
        self.assertEqual(row['characters'], 5)
        held['evidence'] = [dict(artifact=original, encoding='utf-8', content='abcdefghijklmno')]
        self.assertEqual(_describe_missing_sources(bodies, [held], packet), [])

    def test_notice_paragraph_applies_to_study_and_problem_sessions_and_replays_its_version(self):
        from dataclasses import replace
        from research_harness import review_prompt_paragraphs as paragraphs
        for problem in (False, True):
            for role in ('bar', 'slate', 'result'):
                with self.subTest(problem=problem, role=role):
                    packet = dict(role=role, delivery_notice=dict(protocol='exactory-review-delivery-notice-v1'))
                    if problem:
                        packet['subject'] = dict(kind='problem')
                    parameters = paragraphs.select_prompt_parameters(role, packet)
                    self.assertEqual(parameters, dict(problem_paragraphs=['exactory-review-delivery-notice-v1']))
                    prompt = protocol.canonical_prompt(role, orchestration='problem_creation' if problem else None,
                                                       **parameters)
                    assignment = dict(role=role, prompt=prompt, context={}, reassessment_required=False)
                    if problem:
                        assignment['subject'] = dict(kind='problem')
                    self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
                    old = paragraphs.PARAGRAPHS[-1]
                    later = replace(old, versions=old.versions + (('exactory-review-delivery-notice-v2',
                                                                  '\n\nA later synthetic notice paragraph.'),))
                    with patch.object(paragraphs, 'PARAGRAPHS', paragraphs.PARAGRAPHS[:-1] + (later,)):
                        self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
                        self.assertEqual(protocol.canonical_prompt(role,
                            orchestration='problem_creation' if problem else None, **parameters), prompt)


if __name__ == '__main__':
    unittest.main()
