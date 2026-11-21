"""Session repairs enter v4 while recorded v2/v3 sessions keep their bytes."""

import copy
import json
import unittest
from unittest.mock import patch

from ci.capacity_packet_inventory import read_packet_registry, validate_packet
from codex_route_fixtures import build_codex_response, invoke_codex, probe_codex_route, record_codex_route
from research_harness import bar_sources, cli, research_decisions as decisions
from research_harness import review_context_repair as repair, review_field_classes as classes
from research_harness import review_parts as parts, review_protocol as protocol, review_session_v4 as session
from research_harness.check_mode import check_mutation
from research_harness.errors import ResearchError
from research_harness.review_composite import read_final_output
from research_harness.review_delivered_evidence import read_chain_delivery
import test_review_session_v4_pages as page_fixtures
import test_review_session_v4_turns as turn_fixtures


class SessionEntryTests(unittest.TestCase):
    setUp = turn_fixtures.SessionTurnsTests.setUp
    add_sources = turn_fixtures.SessionTurnsTests.add_sources
    read_packet = turn_fixtures.SessionTurnsTests.read_packet
    respond = turn_fixtures.SessionTurnsTests.respond
    record_legacy_bars = turn_fixtures.SessionTurnsTests.record_legacy_bars
    install_measured_profile = turn_fixtures.SessionTurnsTests.install_measured_profile
    start_pages = page_fixtures.SessionPagesTests.start
    answer = page_fixtures.SessionPagesTests.answer
    finish_pages = page_fixtures.SessionPagesTests.finish_pages

    def build_repair_payload(self, head, route_id='route', *, exhaustion=False):
        records = self.case.read_records()
        events = repair.cause_events(records, {row['id'] for row in repair._exposure_lineage(records, head)})
        binding = dict(assignment_id=head['id'], event_ids=[row['id'] for row in events],
            evidence=[item for row in events for item in row['evidence']], probe_id='repair-probe',
            reason='Reassess the retained scientific history in a verified context.')
        if exhaustion:
            binding['exhaustion'] = repair.build_exhaustion_binding(records, self.case.artifacts, head)
        return dict(id='repair', route_id=route_id, dossier_id=None, role=head['role'], reviewer_id='first',
                    author_id='author', context=dict(context_repair=binding))

    def continue_repair(self, current, identifier):
        self.assertEqual(validate_packet(current['role'], self.read_packet(current), read_packet_registry()), [])
        value = {key: current[key] for key in ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id')}
        return self.case.mutate(protocol.record_assignment,
            dict(value, id=identifier, context=dict(prior_assignment_id=current['id'])))['result']

    def finish_repair(self, current, evidence):
        for number in range(60):
            packet = self.read_packet(current)
            if 'session_final' in packet:
                break
            self.respond(current)
            current = self.continue_repair(current, 'repair-turn-%d' % number)
        else:
            self.fail('The history units did not finish')
        output = self.case.build_review_output(current['role'])
        output['value'].update(status='unresolved', evidence=[evidence])
        for check in output['value']['assurances']:
            check['status'], check['evidence'] = 'not_assessed', [copy.deepcopy(evidence)]
        output['reassessment'] = dict(assignment_id='predecessor', findings=[])
        invoke_codex(self.case, current['id'], output)
        records = self.case.read_records()
        checked = protocol.assignment_state(records, self.case.artifacts, current['id'])
        self.assertTrue(checked['ready'], (checked['obligations'], records.get('review_output_refusal')))
        output, _ = read_final_output(records, self.case.artifacts, current)
        saved = self.case.mutate(decisions.record_value_review,
            dict(output, id='repaired-review', dossier_id=None, assignment_id=current['id']))['result']
        return current, saved

    def test_retirement_repair_starts_v4_and_delivers_history_as_bounded_units(self):
        self.install_measured_profile()
        self.add_sources([2000])
        head = self.case.assign('bar', 'first', identifier='predecessor')
        self.respond(head)
        self.case.mutate(protocol.record_context_event, dict(id='retirement', assignment_id=head['id'],
            kind='route_retired', source='owner', reason='The earlier route is retired.', evidence=[self.case.supporting]))
        record_codex_route(self.case, 'repair-route', model='gpt-6-astra', output_tokens=128000)
        probe_codex_route(self.case, 'repair-probe', 'repair-route')
        saved = self.case.mutate(protocol.record_assignment, self.build_repair_payload(head, 'repair-route'))['result']
        self.assertIn('session_chain', saved)
        self.assertTrue(saved['reassessment_required'])
        plan, inventory, state = session.read_bound_session(self.case.artifacts, saved)
        history = [row for row in inventory['units'] if row['kind'] == 'history']
        self.assertTrue(history)
        self.assertTrue(all(row['required'] for row in history))
        frame = self.read_packet(saved)['session_turn']['frame']
        self.assertNotIn('observed_output', json.dumps(frame))
        route = self.case.read_records()['review_route']['repair-route']
        logical = protocol._read_json(self.case.artifacts, saved['session_chain']['logical'])
        projected = classes.classify_packet(route, 'bar', session._describe_logical_fields(logical),
                                           bounded_fields=plan['frame']['request']['bounded_fields'])
        self.assertEqual(session._replace_frame_handles(projected['frame'], {
            row['identity']['frame_handle']: row['handle'] for row in inventory['units'] if row['kind'] == 'frame'}), frame)
        self.assertFalse(state['deliveries'])
        final, recorded = self.finish_repair(saved, dict(ref=history[0]['handle']))
        self.assertEqual(recorded['assignment_id'], final['id'])

    def test_repair_after_a_recorded_final_in_pages_starts_v4_with_the_composed_review(self):
        final, _, composite = self.finish_pages(self.start_pages())
        self.case.mutate(protocol.record_context_event, dict(id='cause', assignment_id=final['id'], kind='contamination',
            source='author_history', reason='A recorded exposure.', evidence=[self.case.supporting]))
        probe_codex_route(self.case, 'repair-probe', 'route')
        saved = self.case.mutate(protocol.record_assignment, self.build_repair_payload(final))['result']
        self.assertIn('session_chain', saved)
        logical = protocol._read_json(self.case.artifacts, saved['session_chain']['logical'])
        self.assertEqual([row['id'] for row in logical['historical_reviews']], ['review-pages'])
        self.assertEqual(self.case.read_records()['value_review']['review-pages']['composite']['id'], composite['id'])

    def build_exhausted_legacy_slate(self, cause=None, *, route_alias=False, reviewer='first'):
        self.record_legacy_bars()
        version = next(version for version, work in self.case.read_records()['work'].items()
                       if any(row.get('artifact', {}).get('sha256') == self.case.supporting['sha256']
                              for row in work.get('abstracts', [])))
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)):
            head = self.case.assign('slate', 'first', identifier='predecessor')
            for number in range(3):
                output = self.case.build_review_output('slate')
                output['value']['status'] = 'invalid'
                invoke_codex(self.case, head['id'], output)
                with self.assertRaises(ResearchError):
                    self.case.mutate(decisions.record_value_review,
                        dict(output, id='refused-review-' + str(number), dossier_id=None, assignment_id=head['id']))
                if number < 2:
                    payload = {key: copy.deepcopy(head[key]) for key in
                        ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
                    payload.update(id='refused-%d' % number)
                    payload['context']['output_refusal'] = dict(assignment_id=head['id'])
                    head = self.case.mutate(protocol.record_assignment, payload)['result']
        if cause:
            self.case.mutate(protocol.record_context_event, dict(id='cause', assignment_id=head['id'], kind=cause,
                source='owner' if cause == 'route_retired' else 'author_history', reason='The cause requires fresh reading.',
                evidence=[self.case.supporting]))
        route = 'repair-route' if route_alias or cause == 'route_retired' else 'route'
        if route != 'route':
            record_codex_route(self.case, route, model='gpt-6-astra', output_tokens=128000)
        probe_codex_route(self.case, 'repair-probe', route)
        payload = self.build_repair_payload(head, route, exhaustion=True)
        payload['reviewer_id'] = reviewer
        return head, payload, version

    def check_exhaustion_credit(self, cause=None, *, route_alias=False, reviewer='first'):
        self.install_measured_profile()
        head, payload, version = self.build_exhausted_legacy_slate(cause, route_alias=route_alias, reviewer=reviewer)
        saved = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertIn('session_chain', saved)
        plan, inventory, state = session.read_bound_session(self.case.artifacts, saved)
        unit = next(row for row in inventory['units'] if row['kind'] == 'abstract' and row['identity']['version_id'] == version)
        credited = any(row['unit'] == unit['handle'] and row['end'] == unit['characters'] for row in state['deliveries'])
        self.assertEqual(credited, cause is None and reviewer == 'first')
        self.assertFalse(any(row.get('unit') == unit['handle'] for row in self.read_packet(saved)['session_turn']['plan']['now']))
        return head, saved, unit

    def list_body_turns(self, assignment, unit):
        """The turns of the repair chain that delivered the body; 0 is the credit of the predecessor."""
        _, inventory, state = session.read_bound_session(self.case.artifacts, assignment)
        handles = {row['handle'] for row in inventory['units'] if row['sha256'] == unit['sha256']}
        return {row['turn'] for row in state['deliveries'] if row['unit'] in handles}

    def test_clean_exhaustion_credits_delivered_body_and_citation_audit_passes(self):
        head, saved, unit = self.check_exhaustion_credit()
        final = self.finish_exhaustion_repair(head, saved, unit)
        self.assertEqual(self.list_body_turns(final, unit), {0})

    def finish_exhaustion_repair(self, head, saved, unit):
        current = saved
        for number in range(60):
            if 'session_final' in self.read_packet(current):
                break
            self.respond(current)
            current = self.continue_repair(current, 'reassess-%d' % number)
        else:
            self.fail('Repair never reached a final')
        _, inventory, _ = session.read_bound_session(self.case.artifacts, current)
        body = next(row for row in inventory['units'] if row['kind'] == 'history' and row['sha256'] == unit['sha256'])
        output = self.case.build_review_output('slate')
        output['value'].update(status='unresolved', evidence=[dict(ref=body['handle'])], rubric_potential={})
        for check in output['value']['assurances']:
            check['status'], check['evidence'] = 'not_assessed', [dict(ref=body['handle'])]
        output['reassessment'] = dict(assignment_id=head['id'], findings=[])
        invoke_codex(self.case, current['id'], output)
        records = self.case.read_records()
        checked = protocol.assignment_state(records, self.case.artifacts, current['id'])
        self.assertTrue(checked['ready'], (checked['obligations'], records.get('review_output_refusal')))
        current = records['review_assignment'][current['id']]
        delivered = read_chain_delivery(records, self.case.artifacts, current)
        self.assertIn(unit['sha256'], delivered['artifacts'])
        expanded, _ = read_final_output(records, self.case.artifacts, current)
        self.case.mutate(decisions.record_value_review,
            dict(expanded, id='repaired-slate', dossier_id=None, assignment_id=current['id']))
        return current

    def check_cause_reads_again(self, cause):
        head, saved, unit = self.check_exhaustion_credit(cause, route_alias=True)
        final = self.finish_exhaustion_repair(head, saved, unit)
        self.assertTrue(self.list_body_turns(final, unit))
        self.assertNotIn(0, self.list_body_turns(final, unit))

    def test_exhaustion_with_contamination_reads_bodies_again(self):
        self.check_cause_reads_again('contamination')

    def test_exhaustion_with_retirement_reads_bodies_again(self):
        self.check_cause_reads_again('route_retired')

    def test_clean_exhaustion_same_route_identity_under_another_id_credits_body(self):
        self.check_exhaustion_credit(route_alias=True)

    def test_clean_exhaustion_changed_reviewer_reads_bodies_again(self):
        self.check_exhaustion_credit(reviewer='replacement-reviewer')

    def check_base_path(self, payload):
        """A context repair that the v4 admission stops for capacity is recorded as under the rules v3 alone."""
        responses = []
        for preference in ((bar_sources.PROTOCOL_V3,), (session.PROTOCOL, bar_sources.PROTOCOL_V3)):
            with patch.object(protocol, 'NEW_SESSION_RULES', preference):
                responses.append(check_mutation(self.case.store, protocol.record_assignment, payload,
                    expected_revision=self.case.store.revision, request_id='check-' + payload['id'], bounded_payload=payload))
        self.assertEqual(responses[1].pop('session_admission')['reason'], 'below_floor')
        # The observed artifacts also hold those that the refused admission read.
        self.assertLessEqual(set(responses[0].pop('artifact_digests')), set(responses[1].pop('artifact_digests')))
        self.assertEqual(responses[0], responses[1])
        saved = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertNotIn('session_chain', saved)
        self.assertTrue(saved['reassessment_required'])
        self.assertEqual(self.read_packet(saved)['context_repair']['assignment_id'],
                         payload['context']['context_repair']['assignment_id'])
        return saved

    def test_new_slot_below_the_v4_floor_stops_and_a_context_repair_takes_the_next_rule_set(self):
        """Rule V5 and its scope: a new slot ends before any write with the stop and step 14 on the serving route,
        and a context repair of a v4 chain on the same route takes the next rule set."""
        self.add_sources([2000])
        record_codex_route(self.case, 'small-route', model='gpt-6-astra', output_tokens=1500)
        payload = dict(id='small-slot', route_id='small-route', dossier_id=None, role='bar', reviewer_id='second',
                       author_id='author', context={})
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)):
            checked = check_mutation(self.case.store, protocol.record_assignment, payload,
                expected_revision=self.case.store.revision, request_id='check-' + payload['id'], bounded_payload=payload)
        self.assertEqual(checked['check'], 'accepted')
        revision = self.case.store.revision
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, payload)
        details = caught.exception.details
        self.assertEqual((caught.exception.code, details['reason'], details['admission']['reason']),
                         ('review_route_capacity_below_floor', session.SESSION_BELOW_FLOOR_REASON, 'below_floor'))
        self.assertEqual(self.case.store.revision, revision)
        self.assertEqual([(answer['route_id'], [step['id'] for step in answer['steps']]) for answer in details['then']],
                         [('route', [14])])
        head = self.case.assign('bar', 'first', identifier='predecessor')
        self.assertIn('session_chain', head)
        self.respond(head)
        self.case.mutate(protocol.record_context_event, dict(id='cause', assignment_id=head['id'], kind='contamination',
            source='author_history', reason='A recorded exposure.', evidence=[self.case.supporting]))
        probe_codex_route(self.case, 'repair-probe', 'small-route')
        self.check_base_path(self.build_repair_payload(head, 'small-route'))

    def test_repair_of_a_v3_chain_that_v4_stops_for_capacity_is_recorded_under_the_rules_v3(self):
        self.add_sources([2000])
        record_codex_route(self.case, 'small-route', model='gpt-6-astra', output_tokens=1500)
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)):
            head = self.case.assign('bar', 'first', identifier='predecessor')
        self.assertEqual(self.read_packet(head)['source_rules'], bar_sources.PROTOCOL_V3)
        self.case.mutate(protocol.record_context_event, dict(id='cause', assignment_id=head['id'], kind='contamination',
            source='author_history', reason='A recorded exposure.', evidence=[self.case.supporting]))
        probe_codex_route(self.case, 'repair-probe', 'small-route')
        self.check_base_path(self.build_repair_payload(head, 'small-route'))


class LegacySessionBytesTests(unittest.TestCase):
    """Prospective defaults do not rewrite a recorded source-session boundary."""

    def setUp(self):
        self.case = turn_fixtures.problem_fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        record_codex_route(self.case, model='gpt-6-astra', output_tokens=128000)

    add_sources = turn_fixtures.SessionTurnsTests.add_sources
    read_packet = turn_fixtures.SessionTurnsTests.read_packet
    record_legacy_bars = turn_fixtures.SessionTurnsTests.record_legacy_bars

    def assign_legacy_session(self, rules, number, role='bar'):
        reviewer = 'first' if number == 0 else 'second'
        with patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)), \
                patch.object(bar_sources, 'SESSION_PROTOCOL', rules):
            assignment = self.case.assign(role, reviewer, identifier='legacy-%s-%d' % (role, number))
        packet = self.read_packet(assignment)
        self.assertNotIn('session_chain', assignment)
        self.assertEqual(packet.get('source_rules', bar_sources.PROTOCOL), rules)
        return assignment

    def build_legacy_payload(self, assignment, identifier, context):
        payload = {key: copy.deepcopy(assignment[key]) for key in
                   ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
        return dict(payload, id=identifier, context=context)

    def read_legacy_bytes(self, assignment):
        request = protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, assignment)
        encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False,
                                          separators=(',', ':')).encode('utf-8')
        return (self.case.artifacts.read(assignment['packet']), encode(assignment['prompt']), encode(request))

    def check_legacy_bytes(self, payload, *, expected_error=None):
        path = self.case.store.root / 'legacy-session-check.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        revision = self.case.store.revision
        before = self.case.store.snapshot()
        responses = []
        preferences = ((bar_sources.PROTOCOL_V3,), (session.PROTOCOL, bar_sources.PROTOCOL_V3))
        for preference in preferences:
            with patch.object(protocol, 'NEW_SESSION_RULES', preference):
                args = cli.build_parser().parse_args(['review-assignment', '--workspace', str(self.case.store.root),
                    '--file', str(path), '--expected-revision', str(revision),
                    '--request-id', 'legacy-check-' + payload['id'], '--check'])
                if expected_error is None:
                    response = cli.run(args)
                    self.assertEqual(response['check'], 'accepted')
                    self.assertFalse(response['recorded'])
                    self.assertEqual(response['operation'], 'review.assignment')
                    self.assertNotIn('session_admission', response)
                else:
                    with self.assertRaises(ResearchError) as caught:
                        cli.run(args)
                    self.assertEqual(caught.exception.code, expected_error)
                    response = caught.exception.as_dict()
                responses.append(json.dumps(response, sort_keys=True, ensure_ascii=False,
                                            separators=(',', ':')).encode('utf-8'))
                self.assertEqual(self.case.store.snapshot(), before)
        self.assertEqual(responses[0], responses[1])

    def record_legacy_over_cap(self, assignment):
        def respond_over_cap(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response

        with patch.object(protocol.transport, 'send_request', side_effect=respond_over_cap):
            self.case.mutate(protocol.invoke_assignment,
                dict(id='over-cap-' + assignment['id'], assignment_id=assignment['id']), credential='fixture')

    def test_running_v2_and_v3_continuations_keep_packet_prompt_request_and_check_bytes(self):
        version = self.add_sources([2000])[0]
        for number, rules in enumerate((bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3)):
            with self.subTest(rules=rules):
                assignment = self.assign_legacy_session(rules, number)
                output = self.case.build_review_output('bar')
                output['value'].update(status='unresolved', evidence=[])
                output['source_requests'] = [dict(id='text', kind='source', version_id=version, depth='fulltext')]
                invoke_codex(self.case, assignment['id'], output)
                frozen = self.read_legacy_bytes(assignment)
                payload = self.build_legacy_payload(assignment, 'continued-%d' % number,
                    dict(prior_assignment_id=assignment['id']))
                self.check_legacy_bytes(payload)
                with patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)):
                    continued = self.case.mutate(protocol.record_assignment, payload)['result']
                self.assertEqual(self.read_packet(continued).get('source_rules', bar_sources.PROTOCOL), rules)
                self.assertNotIn('session_chain', continued)
                self.assertEqual(self.read_legacy_bytes(assignment), frozen)

    def test_running_v2_and_v3_capacity_replacements_keep_exact_check_bytes(self):
        self.record_legacy_bars()
        self.add_sources([2000])
        self.case.expected['assessment_evidence'] = [self.case.artifacts.put(
            b'A synthetic bound and its scientific assessment. ' * 400, 'text/plain')]
        for number, rules in enumerate((bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3)):
            with self.subTest(rules=rules):
                assignment = self.assign_legacy_session(rules, number, role='result')
                self.assertNotIn(parts.KEY, self.read_packet(assignment))
                self.record_legacy_over_cap(assignment)
                frozen = self.read_legacy_bytes(assignment)
                payload = self.build_legacy_payload(assignment, 'capacity-%d' % number,
                    dict(capacity_replacement=assignment['id']))
                self.check_legacy_bytes(payload)
                with patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)):
                    replacement = self.case.mutate(protocol.record_assignment, payload)['result']
                self.assertEqual(replacement['capacity_replacement_of'], assignment['id'])
                self.assertIn(parts.KEY, self.read_packet(replacement))
                self.assertNotIn('session_chain', replacement)
                self.assertEqual(self.read_legacy_bytes(assignment), frozen)

    def test_running_v2_and_v3_capacity_refusals_keep_exact_check_bytes(self):
        version = self.add_sources([2000])[0]
        for number, rules in enumerate((bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3)):
            with self.subTest(rules=rules):
                assignment = self.assign_legacy_session(rules, number)
                output = self.case.build_review_output('bar')
                output['value'].update(status='unresolved', evidence=[])
                output['source_requests'] = [dict(id='text', kind='source', version_id=version, depth='fulltext')]
                invoke_codex(self.case, assignment['id'], output)
                continued = self.case.mutate(protocol.record_assignment,
                    self.build_legacy_payload(assignment, 'continued-%d' % number,
                        dict(prior_assignment_id=assignment['id'])))['result']
                self.record_legacy_over_cap(continued)
                frozen = self.read_legacy_bytes(continued)
                payload = self.build_legacy_payload(continued, 'inadmissible-capacity-%d' % number,
                    dict(capacity_replacement=continued['id']))
                self.check_legacy_bytes(payload, expected_error='invalid_review_context')
                self.assertEqual(self.read_legacy_bytes(continued), frozen)

    def test_running_v2_and_v3_refusal_replacements_keep_exact_check_bytes(self):
        self.add_sources([2000])
        marker = dict(self.case.read_records()['workspace']['problem'])
        marker['field'] = 'Extremal bounded sequences. ' * 64
        self.case.put('workspace', 'problem', marker)
        for number, rules in enumerate((bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3)):
            with self.subTest(rules=rules):
                assignment = self.assign_legacy_session(rules, number)
                output = self.case.build_review_output('bar')
                output['value']['status'] = 'invalid'
                invoke_codex(self.case, assignment['id'], output)
                with self.assertRaises(ResearchError):
                    self.case.mutate(decisions.record_value_review,
                        dict(output, id='refused-%d' % number, dossier_id=None, assignment_id=assignment['id']))
                self.assertIn('attempt-' + assignment['id'], self.case.read_records()['review_output_refusal'])
                frozen = self.read_legacy_bytes(assignment)
                payload = self.build_legacy_payload(assignment, 'refusal-%d' % number,
                    dict(output_refusal=dict(assignment_id=assignment['id'])))
                self.check_legacy_bytes(payload)
                with patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)):
                    replacement = self.case.mutate(protocol.record_assignment, payload)['result']
                packet = self.read_packet(replacement)
                self.assertEqual(packet.get('source_rules', bar_sources.PROTOCOL), rules)
                self.assertIn('output_refusal', packet)
                self.assertEqual(replacement['slot_id'], assignment['slot_id'])
                self.assertNotIn('session_chain', replacement)
                self.assertEqual(self.read_legacy_bytes(assignment), frozen)
