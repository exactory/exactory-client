"""Fresh bounded sessions for the two source-turn capacity incidents (D1/N3)."""

import copy
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, record_codex_route
import test_research_problem_reviews as problem_fixtures
from research_harness import bar_sources, research_decisions as decisions
from research_harness import review_handles, review_protocol as protocol
from research_harness import review_route_capacity as capacity, review_session_v4 as session
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.review_bodies import read_history


class ProbeClock(datetime):
    """The fixed instant of the fixture route's capacity probe. Its UTC date enters the output profile of every
    capacity plan, so the pinned packet and plan bytes must not take it from the wall clock."""
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 17, tzinfo=timezone.utc).astimezone(tz)


class SessionTurnsTests(unittest.TestCase):
    def setUp(self):
        self.case = problem_fixtures.ProblemReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        with patch.object(protocol, 'datetime', ProbeClock):
            record_codex_route(self.case, model='gpt-6-astra', output_tokens=128000)
        preferred = patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3))
        preferred.start()
        self.addCleanup(preferred.stop)
        self.sizes = []

    def add_sources(self, sizes):
        versions = []
        for number, size in enumerate(sizes, 1):
            version = self.case.metadata(number)
            self.case.capture(version, pdf_text=('Source %d. ' % number + 'x' * size)[:size - 18] + ' References: none.')
            versions.append(version)
        return versions

    def read_packet(self, assignment):
        return protocol._read_json(self.case.artifacts, assignment['packet'])

    def respond(self, assignment, requests=(), excerpts=(), notes='Latest bounded notes.'):
        packet = self.read_packet(assignment)
        self.assertNotIn('handle_table', packet)
        self.assertEqual(read_history(self.case.artifacts, assignment), [])
        request = protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, assignment)
        measured = capacity.measure_request(self.case.read_records()['review_route']['route'], request)['text_characters']
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, assignment)
        binding = packet.get('session_turn', packet.get('session_final'))
        bound = binding['capacity']['request_bound']
        self.assertLessEqual(measured, bound)
        self.sizes.append(measured)
        if 'session_turn' in packet:
            output = dict(schema_version=1, stage='request', notes=notes, excerpts=list(excerpts),
                          requests=list(requests), settled_requests=[])
        else:
            output = self.case.build_review_output(assignment['role'])
            output['value'].update(status='unresolved', evidence=[])
        invoke_codex(self.case, assignment['id'], output)
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertTrue(state['ready'], (state['obligations'], self.case.read_records().get('review_output_refusal')))
        return output

    def finish(self, assignment, evidence=()):
        if not evidence:
            plan, inventory, state = session.read_bound_session(self.case.artifacts, assignment)
            delivered = {row['unit'] for row in state['deliveries']}
            evidence = [dict(ref=next(row['handle'] for row in inventory['units']
                                      if row['kind'] == 'fulltext' and row['handle'] in delivered))]
        plan, inventory, state = session.read_bound_session(self.case.artifacts, assignment)
        for reference in evidence:
            unit = next((row for row in inventory['units'] if row['handle'] == reference.get('ref')), None)
            if unit and unit['kind'] == 'fulltext':
                from research_harness.literature import import_bundle
                from research_harness.reading import record_reading
                version = unit['identity']['version_id']
                capture = self.case.read_records()['work'][version]['fulltexts'][0]
                bundle = self.case.bundle(version, capture=capture, bundle_id='bundle-' + unit['handle'])
                self.case.mutate(import_bundle, bundle)
                self.case.mutate(record_reading, self.case.full_note(bundle, note_id='read-' + unit['handle']))
        output = self.case.build_review_output(assignment['role'])
        output['value'].update(status='unresolved', evidence=list(evidence))
        if assignment['role'] == 'slate':
            output['value']['rubric_potential'] = {}
            for check in output['value']['assurances']:
                check['status'] = 'not_assessed'
        invoke_codex(self.case, assignment['id'], output)
        records = self.case.read_records()
        state = protocol.assignment_state(records, self.case.artifacts, assignment['id'])
        self.assertTrue(state['ready'], (state['obligations'], self.case.read_records().get('review_output_refusal')))
        expanded, _ = __import__('research_harness.review_composite', fromlist=['read_final_output']).read_final_output(
            records, self.case.artifacts, assignment)
        saved = self.case.mutate(decisions.record_value_review,
            dict(expanded, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))['result']
        self.assertEqual(saved['assignment_id'], assignment['id'])
        return saved

    def test_forty_source_turns_are_bounded_and_turn_three_handle_records_bar(self):
        versions = self.add_sources([1000] * 40)
        current = self.case.assign('bar', 'first', identifier='turn-1')
        retained = None
        for number, version in enumerate(versions, 1):
            packet = self.read_packet(current)
            self.assertIn('session_turn', packet)
            rows = packet['session_turn']['plan']['now']
            if number == 3:
                retained = next(row['unit'] for row in rows if row.get('request_id') == 'text-2')
            self.respond(current, [dict(id='text-%d' % number, kind='source', version_id=version, depth='fulltext')])
            current = self.case.assign('bar', 'first', identifier='turn-%d' % (number + 1),
                                       prior_assignment_id=current['id'])
        self.respond(current)
        final = self.case.assign('bar', 'first', identifier='final', prior_assignment_id=current['id'])
        self.assertIn('session_final', self.read_packet(final))
        self.finish(final, [dict(ref=retained)])
        self.assertEqual(len(self.case.read_records()['value_review']), 1)
        self.assertLess(max(self.sizes), 300000)

    def test_n3_six_large_texts_deliver_bounded_parts_and_assessment_once(self):
        versions = self.add_sources([30000, 60000, 90000, 120000, 150000, 180000])
        current = self.case.assign('bar', 'first', identifier='request')
        self.respond(current, [dict(id='text-%d' % number, kind='source', version_id=version, depth='fulltext')
                               for number, version in enumerate(versions)])
        delivery_sizes = []
        for number in range(100):
            current = self.case.assign('bar', 'first', identifier='delivery-%d' % number,
                                       prior_assignment_id=current['id'])
            packet = self.read_packet(current)
            if 'session_final' in packet:
                break
            output = self.respond(current)
            self.assertEqual(set(output), {'schema_version', 'stage', 'notes', 'excerpts', 'requests', 'settled_requests'})
            delivery_sizes.append(self.sizes[-1])
        else:
            self.fail('The requested texts never completed')
        self.finish(current)
        self.assertTrue(delivery_sizes)
        self.assertEqual(len(self.case.read_records()['value_review']), 1)
        print('N3 delivery turns=%d input_characters=%s' % (len(delivery_sizes), delivery_sizes))

    def test_c1_request_rows_control_max_instead_of_reading_rows(self):
        from test_review_session_v4_record import build_store
        from v7_capacity_fixtures import build_route
        route = build_route(effort='max')
        records = build_store(self.case.artifacts)
        packet = protocol.build_packet(self.case.read_records(), self.case.artifacts, None, 'bar', 'first')
        admission = session.check_v4_admission(records, self.case.artifacts, route, 'bar', packet)
        self.assertIn(admission['state'], ('record', 'stop'))
        if admission['state'] == 'record':
            task = admission['capacity_plan']['task']['request']
            self.assertEqual(task['state'], 'over_limit_expected')
            self.assertIsNone(task['borrowed_kind'])
        else:
            self.assertEqual(admission['reason'], 'below_floor')

    def test_default_remains_v3_and_new_v3_bytes_match_fallback(self):
        with patch.object(session, 'check_v4_admission', return_value=dict(state='stop', reason='final_needs_pages')):
            first = self.case.assign('bar', 'first', identifier='legacy')
        self.assertNotIn('session_chain', first)
        self.assertEqual(self.read_packet(first)['source_rules'], bar_sources.PROTOCOL_V3)

    def test_request_output_rejects_assessment(self):
        current = self.case.assign('bar', 'first', identifier='request')
        invoke_codex(self.case, current['id'], self.case.build_review_output('bar'))
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertFalse(state['ready'])
        self.assertTrue(state['refused_attempts'])

    def install_measured_profile(self):
        route = self.case.read_records()['review_route']['route']
        profile = capacity.read_output_profile({}, route)
        rows = []
        for kind in ('request', 'final'):
            row = capacity._create_profile_row(kind, None)
            row.update(attempts=4, completed_attempts=4, measurement_attempts=4,
                       largest_thinking_tokens=1000, largest_output_tokens=2000)
            rows.append(row)
        profile.update(rows=rows, digest=digest(rows))
        patcher = patch.object(capacity, 'read_output_profile', return_value=profile)
        patcher.start()
        self.addCleanup(patcher.stop)
        return profile

    def build_final(self):
        self.add_sources([2000])
        current = self.case.assign('bar', 'first', identifier='request')
        plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        self.respond(current, [dict(id='source', kind='source', version_id=unit['identity']['version_id'], depth='fulltext')])
        for number in range(20):
            current = self.case.assign('bar', 'first', identifier='next-%d' % number, prior_assignment_id=current['id'])
            if 'session_final' in self.read_packet(current):
                return current, unit
            self.respond(current)
        self.fail('No final was reached')

    def test_delivered_span_authenticates_and_undelivered_source_is_refused(self):
        versions = self.add_sources([2000, 2000])
        current = self.case.assign('bar', 'first', identifier='request')
        self.respond(current, [dict(id='one', kind='source', version_id=versions[0], depth='fulltext')])
        current = self.case.assign('bar', 'first', identifier='delivery', prior_assignment_id=current['id'])
        self.respond(current)
        final = self.case.assign('bar', 'first', identifier='final', prior_assignment_id=current['id'])
        plan, inventory, state = session.read_bound_session(self.case.artifacts, final)
        first, second = [row for row in inventory['units'] if row['kind'] == 'fulltext']
        output = self.case.build_review_output('bar')
        output['value']['evidence'] = [dict(ref=second['handle'], start=0, end=20)]
        invoke_codex(self.case, final['id'], output, 'undelivered')
        refusal = self.case.read_records()['review_output_refusal']['undelivered']
        self.assertEqual(refusal['code'], 'review_evidence_undelivered')
        self.finish(final, [dict(ref=first['handle'], start=0, end=20)])
        records = self.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, final,
                                                       records['review_attempt']['attempt-final']))

    def test_refused_final_keeps_level_and_material_with_notice_then_records(self):
        from research_harness.review_failure_class import plan_after_failure
        final, unit = self.build_final()
        packet = self.read_packet(final)
        output = self.case.build_review_output('bar')
        output['unexpected'] = 'refused'
        invoke_codex(self.case, final['id'], output)
        step = plan_after_failure(self.case.read_records(), self.case.artifacts, final['id'])
        self.assertEqual(step['state'], 'replace', step)
        replacement = self.case.mutate(protocol.record_assignment, step['payload'])['result']
        replaced = self.read_packet(replacement)
        self.assertIn('output_refusal', replaced)
        for member in ('notes', 'entries', 'sources', 'fill', 'capacity'):
            self.assertEqual(replaced['session_final'][member], packet['session_final'][member])
        self.finish(replacement, [dict(ref=unit['handle'])])

    def test_multipart_argument_resumes_final_and_authenticates_completed_handle(self):
        final, unit = self.build_final()
        for part in (1, 2):
            invoke_codex(self.case, final['id'], dict(stage='material', title='An argument', part=part,
                                                     text='Argument part %d.' % part, more=part == 1))
            state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, final['id'])
            self.assertTrue(state['ready'], state['obligations'])
            final = self.case.assign('bar', 'first', identifier='argument-%d' % part, prior_assignment_id=final['id'])
            self.assertIn('session_final', self.read_packet(final))
        saved = self.finish(final, [dict(ref='m1')])
        reference = saved['payload']['value']['evidence'][0]
        self.assertEqual(self.case.artifacts.read(reference), b'Argument part 1.Argument part 2.')
        from research_harness.evaluation import Evaluation
        records = self.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, Evaluation.of(records, self.case.artifacts), final,
                                                       records['review_attempt']['attempt-' + final['id']]))

    def test_capacity_at_every_request_level_lowers_then_retries_floor_and_stops(self):
        from codex_route_fixtures import build_codex_response
        from research_harness import review_failure_class as failures
        self.install_measured_profile()
        self.add_sources([180000])
        current = self.case.assign('bar', 'first', identifier='request')
        levels = []
        for number in range(10):
            packet = self.read_packet(current)
            levels.append(packet['session_turn']['capacity']['level_material_allowance'])
            def over_limit(route, request, credential):
                response = build_codex_response(route, request, dict())
                response['usage']['output_tokens'] = 138371
                return response
            with patch.object(protocol.transport, 'send_request', side_effect=over_limit):
                self.case.mutate(protocol.invoke_assignment, dict(id='cap-%d' % number, assignment_id=current['id']), credential='test-only')
            step = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
            if step['state'] == 'run':
                continue
            if step['state'] == 'stop':
                self.assertEqual(step['code'], 'review_output_over_cap')
                break
            self.assertEqual(step['state'], 'replace', step)
            payload = step['details']['recovery']['payload']
            current = self.case.mutate(protocol.record_assignment, payload)['result']
            self.assertEqual(current['route_id'], 'route')
        else:
            self.fail('The floor never stopped')
        self.assertEqual(levels, [120000, 60000, 30000, 15000, 15000])

    def test_pending_argument_capacity_reduces_answer_bound_and_keeps_final_material(self):
        from codex_route_fixtures import build_codex_response
        from research_harness import review_failure_class as failures
        self.install_measured_profile()
        final, unit = self.build_final()
        invoke_codex(self.case, final['id'], dict(stage='material', title='Argument', part=1, text='First.', more=True))
        pending = self.case.assign('bar', 'first', identifier='pending', prior_assignment_id=final['id'])
        frozen = self.read_packet(pending)['session_final']
        def over_limit(route, request, credential):
            response = build_codex_response(route, request, dict())
            response['usage']['output_tokens'] = 138371
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=over_limit):
            self.case.mutate(protocol.invoke_assignment, dict(id='over-cap', assignment_id=pending['id']), credential='test-only')
        step = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, pending['id'])
        self.assertEqual(step['state'], 'replace', step)
        replacement = self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result']
        after = self.read_packet(replacement)['session_final']
        self.assertEqual(after['capacity']['answer_bound'], frozen['capacity']['answer_bound'] // 2)
        for member in ('notes', 'entries', 'sources', 'fill', 'material_request'):
            self.assertEqual(after[member], frozen[member])
        invoke_codex(self.case, replacement['id'], dict(stage='material', title='Argument', part=2, text='Last.', more=False))
        final = self.case.assign('bar', 'first', identifier='corrected', prior_assignment_id=replacement['id'])
        self.finish(final, [dict(ref='m1')])

    def test_frozen_state_mutation_fails_fresh_authentication_without_new_priors(self):
        final, unit = self.build_final()
        self.finish(final, [dict(ref=unit['handle'])])
        records = self.case.read_records()
        assignment = records['review_assignment'][final['id']]
        attempt = records['review_attempt']['attempt-' + final['id']]
        with patch.object(capacity, 'read_output_profile', side_effect=AssertionError('verification read live profile')):
            self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt))
        plan, inventory, state = session.read_bound_session(self.case.artifacts, assignment)
        state['delivered_sources'] += 1
        assignment['session_chain']['state'] = protocol._put_json(self.case.artifacts, state)
        self.assertFalse(protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt))

    def record_legacy_bars(self):
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)):
            for reviewer in ('first', 'second'):
                context = self.case.deliver_supporting(reviewer)
                assignment = self.case.assign('bar', reviewer, **context)
                output = self.case.build_review_output('bar')
                invoke_codex(self.case, assignment['id'], output)
                self.case.mutate(decisions.record_value_review,
                    dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))

    def run_allowance_case(self, role, length):
        if role != 'bar':
            self.record_legacy_bars()
        self.install_measured_profile()
        versions = self.add_sources([12000] * 10)
        requests = [dict(id='source-%d' % number, kind='source', version_id=version, depth='fulltext')
                    for number, version in enumerate(versions)]
        current = self.case.assign(role, 'first', identifier=role + '-start')
        for number in range(30):
            packet = self.read_packet(current)
            if 'session_final' in packet:
                break
            binding = packet['session_turn']
            excerpts = []
            for delivery, entry in zip(binding['plan']['now'], packet['evidence']):
                if delivery.get('kind') == 'fulltext' and len(entry['content']) >= length:
                    for target in binding['targets']:
                        excerpts.append(dict(unit=delivery['unit'], text=entry['content'][:length],
                                             purpose='Retain the field comparison.', targets=[target['handle']]))
            self.respond(current, requests, excerpts, notes='n' * binding['allowances']['notes'])
            current = self.case.assign(role, 'first', identifier=role + '-next-%d' % number,
                                       prior_assignment_id=current['id'])
        else:
            self.fail('Stable requests never settled')
        self.finish(current)
        self.assertEqual(len(self.case.read_records()['value_review']), 1 if role == 'bar' else 3)

    def test_bar_shortest_excerpts_maximum_notes_and_ten_requests_record(self):
        self.run_allowance_case('bar', 1)

    def test_bar_longest_excerpts_maximum_notes_and_ten_requests_record(self):
        self.run_allowance_case('bar', 6000)

    def test_slate_shortest_excerpts_maximum_notes_and_ten_requests_record(self):
        self.run_allowance_case('slate', 1)

    def test_slate_longest_excerpts_maximum_notes_and_ten_requests_record(self):
        self.run_allowance_case('slate', 6000)

    def test_result_shortest_excerpts_maximum_notes_and_ten_requests_record(self):
        self.run_allowance_case('result', 1)

    def test_result_longest_excerpts_maximum_notes_and_ten_requests_record(self):
        self.run_allowance_case('result', 6000)

    def test_changed_logical_contract_and_material_title_are_refused(self):
        final, unit = self.build_final()
        self.finish(final, [dict(ref=unit['handle'])])
        records = self.case.read_records()
        assignment = records['review_assignment'][final['id']]
        logical = protocol._read_json(self.case.artifacts, assignment['session_chain']['logical'])
        logical['field'] = 'A changed scientific contract.'
        assignment['session_chain']['logical'] = protocol._put_json(self.case.artifacts, logical)
        self.assertFalse(protocol.authenticate_attempt(records, self.case.artifacts, assignment,
            records['review_attempt']['attempt-' + final['id']]))
        with self.assertRaises(ResearchError) as caught:
            review_handles.validate_material(self.read_packet(final), dict(stage='material', title='x' * 121,
                part=1, text='An argument.', more=False), artifacts=self.case.artifacts, assignment=final)
        self.assertEqual(caught.exception.code, 'invalid_review_material')

    def test_thousand_sources_are_one_count_without_delivered_rows_in_request(self):
        from test_review_session_v4_record import build_store, build_turn
        records = dict(self.case.read_records(), **build_store(self.case.artifacts, 1000, 50))
        route = self.case.read_records()['review_route']['route']
        logical = protocol.build_packet(self.case.read_records(), self.case.artifacts, None, 'bar', 'first')
        admission = session.check_v4_admission(records, self.case.artifacts, route, 'bar', logical)
        self.assertEqual(admission['state'], 'record', {key: value for key, value in admission.items() if key not in ('inventory', 'bodies')})
        inventory = admission['inventory']['inventory']
        for row in inventory['units']:
            self.case.artifacts.put(admission['inventory']['bodies'][row['sha256']], row['artifact']['media_type'])
        plan = admission['capacity_plan']
        units = [row for row in inventory['units'] if row['kind'] == 'fulltext']
        observations = [build_turn(unit, self.case.artifacts.read(unit['artifact']).decode(), number)
                        for number, unit in enumerate(units, 1)]
        state = session.build_session_state(inventory, observations, route, plan)['state']
        packet = session.build_session_packet(self.case.artifacts, route, logical, 'chain', 1001,
            inventory, state, plan, capacity.derive_plan_task_limits(plan, 'request'), 'request')
        self.assertEqual(packet['session_turn']['plan']['delivered']['sources'], 1000)
        self.assertEqual(packet['session_turn']['plan']['now'], [])
        self.assertNotIn('deliveries', packet['session_turn'])
        self.assertLess(len(json.dumps(packet)), 10000)

    def test_request_rows_at_480000_control_c1_with_qualifying_reading_rows(self):
        route = self.case.read_records()['review_route']['route']
        rows = []
        for kind, level, over in (('reading', 120000, 0), ('request', 480000, 1), ('final', None, 0)):
            row = capacity._create_profile_row(kind, level)
            row.update(attempts=4, completed_attempts=4, measurement_attempts=4,
                       at_or_over_limit=over, largest_thinking_tokens=100, largest_output_tokens=138371 if over else 1000)
            rows.append(row)
        profile = capacity.read_output_profile({}, route)
        profile.update(rows=rows, digest=digest(rows))
        logical = protocol.build_packet(self.case.read_records(), self.case.artifacts, None, 'bar', 'first')
        with patch.object(capacity, 'read_output_profile', return_value=profile):
            admission = session.check_v4_admission(self.case.read_records(), self.case.artifacts, route, 'bar', logical)
        self.assertEqual(admission['state'], 'record', {key: value for key, value in admission.items() if key not in ('inventory', 'bodies')})
        task = admission['capacity_plan']['task']['request']
        self.assertEqual(task['state'], 'over_limit_expected')
        self.assertEqual(task['sample_size'], 4)
        self.assertEqual(task['at_or_over_limit_share'], .25)
        self.assertIsNone(task['borrowed_kind'])
        self.assertTrue(task['count_carried_state'] if 'count_carried_state' in task else admission['capacity_plan']['measure']['count_carried_state'])

    def test_running_v2_and_v3_continuations_keep_exact_check_bytes(self):
        from research_harness.check_mode import check_mutation
        version = self.add_sources([2000])[0]
        for number, rules in enumerate((bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3)):
            reviewer = 'first' if number == 0 else 'second'
            with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)), patch.object(bar_sources, 'SESSION_PROTOCOL', rules):
                assignment = self.case.assign('bar', reviewer, identifier='legacy-%d' % number)
                output = self.case.build_review_output('bar')
                output['value'].update(status='unresolved', evidence=[])
                output['source_requests'] = [dict(id='text', kind='source', version_id=version, depth='fulltext')]
                invoke_codex(self.case, assignment['id'], output)
                payload = dict(id='continued-%d' % number, role='bar', dossier_id=None, route_id='route',
                    reviewer_id=reviewer, author_id='author', context=dict(prior_assignment_id=assignment['id']))
                responses = []
                for preference in ((bar_sources.PROTOCOL_V3,), (session.PROTOCOL, bar_sources.PROTOCOL_V3)):
                    with patch.object(protocol, 'NEW_SESSION_RULES', preference):
                        responses.append(check_mutation(self.case.store, protocol.record_assignment, payload,
                            expected_revision=self.case.store.revision, request_id='check-%d' % number, bounded_payload=payload))
                self.assertEqual(responses[0], responses[1])

    def test_inventory_list_and_passage_requests_are_delivered_and_settled(self):
        final, unit = self.build_final()
        # Build a fresh reviewer whose request asks for independent inventory, list and passage material.
        current = self.case.assign('bar', 'second', identifier='lists')
        plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        requests = [dict(id='inventory', kind='inventory_page', page=0),
                    dict(id='allowances', kind='list_page', list='allowances', after=None),
                    dict(id='passage', kind='passage', unit=unit['handle'], anchor=None, characters=20)]
        self.respond(current, requests)
        current = self.case.assign('bar', 'second', identifier='list-delivery', prior_assignment_id=current['id'])
        packet = self.read_packet(current)
        self.assertEqual({row['request_id'] for row in packet['session_turn']['plan']['now']},
                         {'inventory', 'allowances', 'passage'})
        self.respond(current)
        current = self.case.assign('bar', 'second', identifier='lists-final', prior_assignment_id=current['id'])
        self.finish(current, [dict(ref=unit['handle'], start=0, end=20)])

    def test_final_rejects_legacy_requests_instead_of_bypassing_assessment(self):
        final, unit = self.build_final()
        output = self.case.build_review_output('bar')
        output['value'] = {}
        output['source_requests'] = [dict(id='inventory', kind='inventory_page', page=0)]
        invoke_codex(self.case, final['id'], output)
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, final['id'])
        self.assertFalse(state['ready'])
        self.assertEqual(self.case.read_records()['review_output_refusal']['attempt-' + final['id']]['code'],
                         'invalid_review_session_turn')
        result = invoke_codex(self.case, final['id'], 12, 'scalar-final')
        self.assertEqual(result['result']['status'], 'failed')
        self.assertFalse(protocol.assignment_state(self.case.read_records(), self.case.artifacts, final['id'])['ready'])

    def test_actual_final_needs_pages_is_admitted_and_check_reports_allocation(self):
        from research_harness import review_composition
        from research_harness.check_mode import check_mutation
        payload = dict(id='check', role='bar', route_id='route', dossier_id=None, reviewer_id='first',
                       author_id='author', context={})
        with patch.object(review_composition, 'list_targets', return_value=[dict(id='target-%d' % n, label='Target') for n in range(10)]):
            checked = check_mutation(self.case.store, protocol.record_assignment, payload,
                expected_revision=self.case.store.revision, request_id='admission-check', bounded_payload=payload)
            actual = self.case.assign('bar', 'first', identifier='check')
        self.assertEqual(checked['session_admission']['state'], 'record')
        forecast = checked['session_admission']['forecast']['by_kind']['final']
        self.assertGreater(forecast['pages'], 1)
        self.assertEqual(forecast['closing_pages'], 1)
        self.assertIn('session_chain', actual)
        self.assertIn('session_turn', self.read_packet(actual))

    def test_rejected_final_after_material_keeps_argument_credit_on_replacement(self):
        from research_harness.review_failure_class import plan_after_failure
        final, unit = self.build_final()
        invoke_codex(self.case, final['id'], dict(stage='material', title='Argument', part=1, text='Recorded argument.', more=False))
        final = self.case.assign('bar', 'first', identifier='arg-final', prior_assignment_id=final['id'])
        output = self.case.build_review_output('bar')
        output['wrong'] = True
        invoke_codex(self.case, final['id'], output)
        step = plan_after_failure(self.case.read_records(), self.case.artifacts, final['id'])
        self.assertEqual(step['state'], 'replace')
        replacement = self.case.mutate(protocol.record_assignment, step['payload'])['result']
        self.finish(replacement, [dict(ref='m1')])

    def test_opus_max_138371_replaces_request_on_same_route_then_records_bar(self):
        from research_harness import review_claude_transport as claude
        from test_research_claude_transport import clean_stream, completed
        from v7_capacity_fixtures import build_route
        self.install_measured_profile()
        route = build_route('claude_cli_v1', model='claude-opus-5-5', effort='max')
        route['id'] = 'opus'
        phase = ['cap']
        def respond(selected, request, credential):
            from research_harness.review_excerpts import read_sent_packet
            packet = read_sent_packet(request)
            if 'allowed_control' in packet:
                output = dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[])
            elif 'session_turn' in packet:
                output = dict(schema_version=1, stage='request', notes='Current notes.', excerpts=[], requests=[], settled_requests=[])
            elif phase[0] == 'material':
                output = dict(stage='material', title='Field comparison', part=1, text='A recorded field argument.', more=False)
            else:
                output = self.case.build_review_output('bar')
                output['value']['evidence'] = [dict(ref='m1')]
            events = clean_stream(request, output=json.dumps(output), model=route['model'])
            events[-1]['usage']['output_tokens'] = 900 if 'allowed_control' in packet or phase[0] != 'cap' else 138371
            with patch.object(claude, '_run_owned', return_value=completed(events)):
                return claude.send_request(selected, request, credential)
        with patch.object(claude, '_cli', return_value=('/fixture/claude', 'fixture-version')), patch.object(
                protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.record_route, route)
            self.case.mutate(protocol.probe_route, dict(id='opus-probe', route_id='opus'), credential='fixture')
            value = dict(id='opus-turn', route_id='opus', role='bar', dossier_id=None,
                         reviewer_id='first', author_id='author', context={})
            first = self.case.mutate(protocol.record_assignment, value)['result']
            self.case.mutate(protocol.invoke_assignment, dict(id='opus-cap', assignment_id=first['id']), credential='fixture')
            recovery = capacity.plan_capacity_recovery(self.case.read_records(), self.case.artifacts, first, 'output_over_cap')
            self.assertEqual(recovery['kind'], 'replace')
            replacement = self.case.mutate(protocol.record_assignment, recovery['payload'])['result']
            self.assertEqual(replacement['route_id'], 'opus')
            self.assertEqual(self.read_packet(replacement)['session_turn']['capacity']['material_allowance'], 60000)
            phase[0] = 'request'
            self.case.mutate(protocol.invoke_assignment, dict(id='opus-request', assignment_id=replacement['id']), credential='fixture')
            value.update(id='opus-final', context=dict(prior_assignment_id=replacement['id']))
            final = self.case.mutate(protocol.record_assignment, value)['result']
            phase[0] = 'material'
            self.case.mutate(protocol.invoke_assignment, dict(id='opus-material', assignment_id=final['id']), credential='fixture')
            value.update(id='opus-assessment', context=dict(prior_assignment_id=final['id']))
            final = self.case.mutate(protocol.record_assignment, value)['result']
            phase[0] = 'assessment'
            self.case.mutate(protocol.invoke_assignment, dict(id='opus-assessment', assignment_id=final['id']), credential='fixture')
            from research_harness.review_composite import read_final_output
            output, _ = read_final_output(self.case.read_records(), self.case.artifacts, final)
            saved = self.case.mutate(decisions.record_value_review,
                dict(output, id='recorded-opus-bar', assignment_id=final['id'], dossier_id=None))['result']
        self.assertEqual(saved['assignment_id'], final['id'])

    def test_dossier_result_records_native_support_from_frozen_assessor(self):
        import test_research_strategic_source_requests as strategic
        case = strategic.ResultSourceRequestTests()
        self.addCleanup(case.doCleanups)
        record_route = protocol.record_route
        def register_session_route(store, payload, **kwargs):
            # The legacy fixture's 1,500 tokens cannot hold the v4 final floor.
            value = dict(payload, configuration=dict(payload['configuration'], max_output_tokens=16000))
            return record_route(store, value, **kwargs)
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)), patch.object(
                protocol, 'record_route', side_effect=register_session_route):
            case.setUp()
        self.install_measured_profile()
        current = case.assignment('dossier-request')
        self.assertIn('session_chain', current)
        _, inventory, _ = session.read_bound_session(case.artifacts, current)
        requested = [dict(id='native-' + unit['handle'], kind='source',
                          version_id=unit['identity']['version_id'], depth='fulltext')
                     for unit in inventory['units'] if unit['kind'] == 'fulltext']
        for number in range(30):
            packet = protocol._read_json(case.artifacts, current['packet'])
            if 'session_final' in packet:
                break
            output = dict(schema_version=1, stage='request', notes='Current dossier notes.', excerpts=[], requests=requested, settled_requests=[])
            case.invoke(output, current['id'])
            current = case.assignment('dossier-next-%d' % number, context=dict(prior_assignment_id=current['id']))
        else:
            self.fail('The dossier frame never finished')
        logical = protocol._read_json(case.artifacts, current['session_chain']['logical'])
        output = case.review_response('result')
        support = case.review(case.execution_payload, identifier='native-support')
        support['assessor'] = copy.deepcopy(logical['assessor'])
        references = support['checks'][0]['evidence']
        self.assertLessEqual(len(references), len(support['checks']))
        for index, check in enumerate(support['checks']):
            check['evidence'] = [references[index % len(references)]]
        output['support'] = support
        from research_harness import review_composition as composition, review_composite
        for number in range(30):
            packet = protocol._read_json(case.artifacts, current['packet'])
            binding = session.read_session_binding(packet)
            answer = output
            if 'session_combine' in packet:
                block = binding['block']
                answer = dict(block=block['handle'], summary='Retain the native support assessment.',
                    refs=[block['entries'][0].get('handle', block['entries'][0].get('block'))])
            elif 'page_plan_digest' in binding:
                plan, _, _ = session.read_bound_session(case.artifacts, current)
                envelope = session.build_composition_packet(case.artifacts, current, packet)
                rule = composition.find_rule('result', envelope)
                targets = binding.get('page', {}).get('targets', plan['targets']['handles'])
                pages, closing = composition.split_output(rule, envelope, output, [targets])
                answer = closing if 'closing' in binding else pages[0]['output']
            case.invoke(answer, current['id'])
            checked = protocol.assignment_state(case.store.snapshot()['records'], case.artifacts, current['id'])
            self.assertTrue(checked['ready'], (checked['obligations'], case.store.snapshot()['records'].get('review_output_refusal')))
            if 'page_plan_digest' not in binding or 'closing' in binding:
                output, _ = review_composite.read_final_output(case.store.snapshot()['records'], case.artifacts, current)
                saved = case.record_final(output, current['id'])['result']
                break
            current = case.assignment('dossier-final-%d' % number, context=dict(prior_assignment_id=current['id']))
        else:
            self.fail('The dossier final never closed')
        self.assertEqual(saved['assignment_id'], current['id'])
        self.assertTrue(case.store.snapshot()['records']['research_support'])

    def test_malformed_passage_and_unhashable_excerpt_unit_are_output_refusals(self):
        self.add_sources([2000])
        current = self.case.assign('bar', 'first', identifier='malformed')
        _, inventory, _ = session.read_bound_session(self.case.artifacts, current)
        unit = next(row['handle'] for row in inventory['units'] if row['kind'] == 'fulltext')
        for number, changes in enumerate((dict(requests=[dict(id='bad', kind='passage', unit=unit, anchor=None, characters='20')]),
                dict(excerpts=[dict(unit={}, text='x', purpose='Use it.', targets=['t1'])]))):
            output = dict(schema_version=1, stage='request', notes='Notes.', excerpts=[], requests=[], settled_requests=[])
            output.update(changes)
            attempt = 'malformed-%d' % number
            invoke_codex(self.case, current['id'], output, attempt)
            self.assertEqual(self.case.read_records()['review_output_refusal'][attempt]['code'], 'invalid_review_session_turn')

    def test_actual_request_and_final_have_closed_packet_declarations(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        final, unit = self.build_final()
        records = self.case.read_records()
        registry = read_packet_registry()
        for assignment in records['review_assignment'].values():
            packet = self.read_packet(assignment)
            self.assertEqual(validate_packet('bar', packet, registry), [])
            self.assertNotIn('handle_table', packet)

    def test_notice_request_capacity_replacement_spends_notice_inside_material_level(self):
        from research_harness import review_failure_class as failures
        from codex_route_fixtures import build_codex_response
        self.install_measured_profile()
        self.case.put('problem_area', 'aux', dict(id='aux', area='math.AG', reason='x' * 100000))
        current = self.case.assign('bar', 'first', identifier='notice-request')
        invoke_codex(self.case, current['id'], dict(wrong=True))
        step = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace')
        current = self.case.mutate(protocol.record_assignment, step['payload'])['result']
        def over_limit(route, request, credential):
            response = build_codex_response(route, request, dict())
            response['usage']['output_tokens'] = 138371
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=over_limit):
            self.case.mutate(protocol.invoke_assignment, dict(id='notice-cap', assignment_id=current['id']), credential='fixture')
        step = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace', step)
        current = self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result']
        packet = self.read_packet(current)
        cap = packet['session_turn']['capacity']
        charge = capacity.measure_capacity_value(self.case.read_records()['review_route']['route'], packet['output_refusal'])
        self.assertEqual(cap['level_material_allowance'], 60000)
        self.assertEqual(cap['material_allowance'], max(15000, 60000 - charge))
        self.respond(current)
