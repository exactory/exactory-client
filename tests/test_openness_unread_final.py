"""D2 R72: retain completed readings when a provider refuses a floor unit."""

import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, bind_codex_response
from openness_reading_fixtures import OpennessReadingCase
import test_openness_reading_v7_turns as turn_fixtures
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_failure_class as failures
from research_harness import problem_openness
from research_harness.review_continuation import plan_next
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class UnreadFinalSupport(OpennessReadingCase):
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)
    completed_prefix = 1
    build_v7_answer = turn_fixtures.V7CommandTests.build_v7_answer
    build_v7_finding = turn_fixtures.V7CommandTests.build_v7_finding

    def setUp(self):
        super().setUp()
        enabled = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        enabled.start()
        self.addCleanup(enabled.stop)

    def refuse_provider(self, identifier, attempt):
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['output'][0]['content'][0]['text'] = 'API Error: safeguards flagged this message'
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            return self.mutate(protocol.invoke_assignment, dict(id=attempt, assignment_id=identifier), credential='fixture')

    def reach_unread(self, *, completed=True):
        native = self.export()
        self.assign('turn-1', native)
        head = 'turn-1'
        if completed:
            for index in range(self.completed_prefix):
                self.invoke(head, self.build_v7_answer(self.read_packet(head)))
                following = 'refused' if index + 1 == self.completed_prefix else 'read-%d' % (index + 2)
                self.assign(following, native, prior=head)
                head = following
        return self.exhaust_head(native, head)

    def exhaust_head(self, native, head):
        # Only the wait clock is advanced; every planned run and replacement is real.
        class LaterClock(failures.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2099, 1, 1, tzinfo=failures.timezone.utc)
        with patch.object(failures, 'datetime', LaterClock):
            for number in range(80):
                action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
                if action.get('details', {}).get('reason') == 'unread':
                    self.assertEqual((action['state'], action['command']), ('replace', 'review-assignment'))
                    self.assertEqual(action['payload']['context']['unread'], dict(assignment_id=head))
                    final = self.mutate(protocol.record_assignment, action['payload'])['result']
                    packet = self.read_packet(final['id'])
                    self.assertIn('reading_final', packet)
                    return native, final, packet
                self.assertIn(action['state'], ('run', 'replace'), action)
                if action['state'] == 'replace':
                    head = self.mutate(protocol.record_assignment, action['payload'])['result']['id']
                else:
                    self.refuse_provider(action['payload']['assignment_id'], action['payload']['id'])
        self.fail('A refused floor reading must reach its unread final.')

    def record_final(self, final, packet):
        answer = self.build_v7_finding(packet, 'x1')
        unread = packet['reading_final']['unread']
        answer['findings']['openness']['grounds'][0]['refs'].append(dict(ref=unread['rows'][0]['unit']))
        self.invoke(final['id'], answer)
        records = self.store.snapshot()['records']
        self.assertTrue(reading.verify_chain(records, self.artifacts, records['review_assignment'][final['id']]))
        return self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final['id']))['result']



class UnreadFinalTests(UnreadFinalSupport):
    def test_g1_k7_provider_refused_reaches_recorded_unresolved_and_record_check(self):
        _, final, packet = self.reach_unread()
        record = self.record_final(final, packet)
        unread = packet['reading_final']['unread']
        self.assertEqual(record['outcome'], 'unresolved')
        missing = {row['artifact']['sha256'] for row in record['unread']['rows']}
        self.assertFalse(missing.intersection(record['evidence']))
        self.assertIn(self.texts[self.versions[0]]['sha256'], record['evidence'])
        self.assertEqual(record['grounds'][0]['refs'][0]['sha256'], unread['digest'])
        self.assertEqual(record['unread']['rows'], json.loads(self.artifacts.read(unread['artifact']))['rows'])
        from research_harness.record_check import check_record
        checked = check_record(self.root, isolate=False)
        self.assertTrue(checked['ready'], checked)

    def assert_invalid_finding(self, outcome, refs, rule):
        _, final, packet = self.reach_unread()
        answer = self.build_v7_finding(packet, 'x1')
        answer['findings']['openness']['outcome'] = outcome
        if refs == 'missing':
            refs = [packet['reading_final']['notes_handle']]
        if refs == 'foreign':
            inventory = json.loads(self.artifacts.read(final['reading_chain']['inventory']))
            held = {row['unit'] for row in json.loads(self.artifacts.read(packet['reading_final']['unread']['artifact']))['rows']}
            refs = ['x1', next(row['handle'] for row in inventory['material'] if row['handle'] not in held)]
        answer['findings']['openness']['grounds'][0]['refs'] = [dict(ref=ref) for ref in refs]
        result = self.invoke(final['id'], answer)
        self.assertEqual(result['output_refusal']['check'], 'openness.finding', result)
        self.assertEqual(result['output_refusal']['details']['rule'], rule)

    def test_unread_finding_refuses_supported_open(self):
        self.assert_invalid_finding('supported_open', ['x1'], 'unread_outcome')

    def test_unread_finding_refuses_already_solved(self):
        self.assert_invalid_finding('already_solved', ['x1'], 'unread_outcome')

    def test_unread_finding_requires_the_complete_set_reference(self):
        self.assert_invalid_finding('unresolved', 'missing', 'unread_set_reference')

    def test_unread_finding_refuses_a_unit_outside_the_set(self):
        self.assert_invalid_finding('unresolved', 'foreign', 'unread_unit_reference')

    def test_a7_unread_final_survives_scientific_refusal_replacement_and_origin_tampering(self):
        _, final, packet = self.reach_unread()
        answer = self.build_v7_finding(packet, 'x1')
        answer['findings']['openness']['outcome'] = 'supported_open'
        refused = self.invoke(final['id'], answer)
        self.assertEqual(refused['output_refusal']['check'], 'openness.finding')
        action = plan_next(self.store.snapshot()['records'], self.artifacts, final['id'])
        self.assertEqual(action['state'], 'replace', action)
        corrected = self.mutate(protocol.record_assignment, action['payload'])['result']
        following = self.read_packet(corrected['id'])
        self.assertEqual(following['reading_final']['unread'], packet['reading_final']['unread'])
        self.record_final(corrected, following)
        records = self.store.snapshot()['records']
        for target in ('set', 'refusal', 'stored_finding'):
            with self.subTest(target=target):
                changed = copy.deepcopy(records)
                artifacts = v7._PlanningArtifacts(self.artifacts)
                if target == 'set':
                    reference = packet['reading_final']['unread']['artifact']
                    value = json.loads(self.artifacts.read(reference))
                    value['rows'][0]['characters'] += 1
                    artifacts.bodies[reference['sha256']] = problem_openness.serialize_record(value)
                elif target == 'stored_finding':
                    changed['problem_openness'][corrected['id']]['grounds'][0]['refs'][0]['sha256'] = 'f' * 64
                else:
                    origin = next(row for row in changed['review_output_refusal'].values()
                                  if row['assignment_id'] == final['id'])
                    origin['message'] = 'Changed refusal record.'
                with self.assertRaises(ResearchError):
                    reading.verify_chain(changed, artifacts, changed['review_assignment'][corrected['id']])

    def test_later_chain_reads_only_unread_ranges_and_finding_becomes_stale_with_carried_turn(self):
        native, final, packet = self.reach_unread()
        self.record_final(final, packet)
        records = self.store.snapshot()['records']
        before = copy.deepcopy(records)
        later = self.export(name='later-packet', assignment_id='later')
        receipt = self.assign('later', later)
        supplied = self.read_packet('later')
        plan, inventory, state = v7.read_bound_state(self.artifacts, receipt, supplied)
        expected = {}
        for row in json.loads(self.artifacts.read(packet['reading_final']['unread']['artifact']))['rows']:
            span = expected.setdefault(row['unit'], [row['start'], row['end']])
            span[1] = max(span[1], row['end'])
        self.assertEqual({body['handle']: [start, end] for body, start, end in
                          v7._find_remaining_ranges(inventory, state) if body['kind'] != 'carried_reading'}, expected)
        self.assertTrue(receipt['carried_readings']['chains'])
        for kind in ('review_assignment', 'review_attempt'):
            for key, value in before[kind].items():
                self.assertEqual(digest(self.store.snapshot()['records'][kind][key]), digest(value))
        records = copy.deepcopy(before)
        records.setdefault('review_context_event', {})['contaminated'] = dict(id='contaminated',
            assignment_id='turn-1', kind='contamination', source='author_history',
            recorded_revision=self.store.revision + 1)
        self.assertEqual(problem_openness.assess_openness(records, self.artifacts)[0]['code'], 'problem_openness_stale')

    def test_every_turn_provider_refused_including_final_ends_in_decision(self):
        _, final, packet = self.reach_unread(completed=False)
        for number in range(9):
            self.refuse_provider(final['id'], 'final-refused-%d' % number)
        action = plan_next(self.store.snapshot()['records'], self.artifacts, final['id'])
        self.assertEqual(action['state'], 'stop', action)
        self.assertEqual(action['stop_class'], 'decision')
        self.assertIn('decision', action['details'])

    def test_unread_admission_refuses_before_exhaustion(self):
        native = self.export()
        self.assign('turn-1', native)
        payload = dict(id='premature', role='native_math', route_id='route', dossier_id=None,
            reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=native, unread=dict(assignment_id='turn-1')))
        for binding in (dict(assignment_id='turn-1'), None, {}, dict(assignment_id=None),
                        dict(assignment_id='turn-1', extra=True)):
            with self.subTest(binding=binding):
                payload['context']['unread'] = binding
                with self.assertRaises(ResearchError) as caught:
                    self.mutate(protocol.record_assignment, payload)
                self.assertEqual(caught.exception.code, 'invalid_review_context')

    def test_unread_admission_refuses_a_second_final_at_the_same_position(self):
        _, final, packet = self.reach_unread()
        payload = {key: copy.deepcopy(final[key]) for key in
                   ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        payload['id'] = 'duplicate-unread'
        with self.assertRaises(ResearchError) as caught:
            self.mutate(protocol.record_assignment, payload)
        self.assertEqual(caught.exception.code, 'invalid_review_context')


    def test_g1_list_only_provider_refusal_reaches_an_empty_unread_finding(self):
        native = self.export()
        head = turn_fixtures.V7CommandTests.finish_reading(self, native)
        request = self.build_v7_answer(self.read_packet(head))
        request['passage_requests'] = [dict(id='allowances', kind='list_page', list='allowances', after=None)]
        self.invoke(head, request)
        action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        assigned = self.mutate(protocol.record_assignment, action['payload'])['result']
        head = assigned['id']
        self.assertEqual({row['kind'] for row in self.read_packet(head)['reading_turn']['plan']['now']}, {'list_page'})
        class LaterClock(failures.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2099, 1, 1, tzinfo=failures.timezone.utc)
        with patch.object(failures, 'datetime', LaterClock):
            for number in range(80):
                action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
                if action.get('details', {}).get('reason') == 'unread':
                    final = self.mutate(protocol.record_assignment, action['payload'])['result']
                    break
                self.assertIn(action['state'], ('run', 'replace'), action)
                if action['state'] == 'replace':
                    head = self.mutate(protocol.record_assignment, action['payload'])['result']['id']
                else:
                    self.refuse_provider(head, 'list-refusal-%d' % number)
            else:
                self.fail('The list-only refusal must reach its final.')
        packet = self.read_packet(final['id'])
        unread = packet['reading_final']['unread']
        self.assertEqual(unread['count'], 0)
        self.invoke(final['id'], self.build_v7_finding(packet, 'x1'))
        records = self.store.snapshot()['records']
        self.assertTrue(reading.verify_chain(records, self.artifacts, records['review_assignment'][final['id']]))
        result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final['id']))['result']
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(result['unread']['rows'], [])

    def test_v7_plan_run_identifier_at_reserved_head_length_is_accepted_by_the_real_command(self):
        identifier = 'r' * reading.RESERVED_IDENTIFIER_CHARACTERS
        native = self.export(assignment_id=identifier)
        self.assign(identifier, native)
        action = plan_next(self.store.snapshot()['records'], self.artifacts, identifier)
        self.assertEqual(action['state'], 'run', action)
        self.assertLessEqual(len(action['payload']['id']), reading.RESERVED_IDENTIFIER_CHARACTERS)
        self.refuse_provider(action['payload']['assignment_id'], action['payload']['id'])

    def test_new_capture_of_refused_text_is_read_whole_in_the_later_chain(self):
        from research_harness.capture_selection import select_main_capture
        _, final, packet = self.reach_unread()
        self.record_final(final, packet)
        original_inventory = json.loads(self.artifacts.read(final['reading_chain']['inventory']))
        refused = packet['reading_final']['unread']['rows'][0]
        body = next(row for row in original_inventory['material'] if row['handle'] == refused['unit'])
        unit = next(row for row in original_inventory['units'] if row['handle'] in body['units'])
        version = unit['identity']['version_id']
        capture = self.capture(version, pdf_text='A different capture. ' + 'z' * 49980)
        self.mutate(select_main_capture, dict(version_id=version, source_id=capture['source_id'],
            reason='Bind another complete extraction.'))
        native = self.export(name='recaptured-packet', assignment_id='recaptured')
        receipt = self.assign('recaptured', native)
        supplied = self.read_packet('recaptured')
        plan, inventory, state = v7.read_bound_state(self.artifacts, receipt, supplied)
        fresh = next(row for row in inventory['units'] if row['kind'] == 'fulltext'
                     and row['identity']['version_id'] == version)
        pending = [(start, end) for row, start, end in v7._find_remaining_ranges(inventory, state)
                   if fresh['handle'] in row['units']]
        self.assertEqual(pending, [(0, fresh['characters'])])
        self.assertEqual(fresh['sha256'], capture['text']['sha256'])
        head, spans = 'recaptured', []
        for number in range(20):
            self.invoke(head, self.build_v7_answer(self.read_packet(head)))
            request = self.read_sent_packet(head)[1]
            spans.extend((row['start'], row['end']) for row in request['reading_turn']['plan']['now']
                         if row['kind'] == 'fulltext' and row['sha256'] == fresh['sha256'])
            if sum(end - start for start, end in spans) == fresh['characters']:
                break
            following = 'recaptured-%d' % (number + 2)
            self.assign(following, native, prior=head)
            head = following
        else:
            self.fail('The later chain must read the entire new capture.')
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], fresh['characters'])
        self.assertTrue(all(left[1] == right[0] for left, right in zip(spans, spans[1:])))


    def test_unread_finding_with_carried_readings_stales_if_a_carried_turn_stops_verifying(self):
        _, first_final, first_packet = self.reach_unread()
        self.record_final(first_final, first_packet)
        native = self.export(name='carried-gap-packet', assignment_id='carried-gap')
        receipt = self.assign('carried-gap', native)
        self.assertTrue(receipt['carried_readings']['chains'])
        # The next export takes over the completed turn and refuses its still-unread unit again.
        class LaterClock(failures.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2099, 1, 1, tzinfo=failures.timezone.utc)
        head = 'carried-gap'
        with patch.object(failures, 'datetime', LaterClock):
            for number in range(80):
                action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
                if action.get('details', {}).get('reason') == 'unread':
                    final = self.mutate(protocol.record_assignment, action['payload'])['result']
                    self.record_final(final, self.read_packet(final['id']))
                    break
                self.assertIn(action['state'], ('run', 'replace'), action)
                if action['state'] == 'replace':
                    head = self.mutate(protocol.record_assignment, action['payload'])['result']['id']
                else:
                    self.refuse_provider(head, 'carried-refusal-%d' % number)
            else:
                self.fail('The carried chain must reach its unread finding.')
        records = copy.deepcopy(self.store.snapshot()['records'])
        finding = records['problem_openness'][final['id']]
        self.assertTrue(any(row.get('origin') for row in finding['reading_turns']))
        records.setdefault('review_context_event', {})['carried-contamination'] = dict(id='carried-contamination',
            assignment_id='turn-1', kind='contamination', source='author_history',
            recorded_revision=self.store.revision + 1)
        self.assertEqual(problem_openness.assess_openness(records, self.artifacts)[0]['code'], 'problem_openness_stale')


class UnreadArtifactTests(unittest.TestCase):
    def build_fixture(self, attempts=9, units=2001):
        artifacts = turn_fixtures.MemoryArtifacts()
        route = dict(turn_fixtures.build_route('codex_cli_v1', 'gpt-6-astra'), id='route')
        rows, bodies = [], {}
        for index in range(units):
            content = ('%06d' % index + 'r' * 194).encode('utf-8')
            reference = artifacts.put(content, 'text/plain')
            rows.append(dict(kind='fulltext', version_id='unit-%d' % index,
                             text_sha256=reference['sha256'], artifact=reference))
            bodies[reference['sha256']] = content
        built = v7.plan_inventory(rows, bodies)
        inventory = built['inventory']
        for body in inventory['material']:
            body['label'] = 'L' * 120
        if attempts == 300:
            handles = {row['handle']: 'u' + str(index).zfill(119) for index, row in enumerate(inventory['material'])}
            for body in inventory['material']:
                body['handle'] = handles[body['handle']]
            for unit in inventory['units']:
                unit['bodies'] = [handles[handle] for handle in unit['bodies']]
        identifier = 'p' * 120
        assignment = dict(id=identifier, role='native_math', route_id='route', context={}, dossier_id=None,
            subject=None, reviewer_id='reviewer', author_id='author', recorded_revision=1,
            packet=artifacts.put_json(dict(reading_turn=dict(plan=dict(now=[dict(
                handle=inventory['material'][0]['handle'], start=0, end=200)])))))
        records = dict(review_route={'route': route}, review_assignment={identifier: assignment}, review_attempt={},
                       review_context_event={})
        response = artifacts.put_json(dict(status='incomplete', output=[dict(type='message', content=[dict(
            type='output_text', text='API Error: safeguards flagged this message')])], context_evidence={}, execution={}))
        for index in range(attempts):
            key = ('a%03d' % index).ljust(120, 'a')
            revision = 2 * index + 2
            records['review_attempt'][key] = dict(id=key, assignment_id=identifier, origin='instrumented',
                status='incomplete', request=None, response=response, output=None,
                usage=dict(output_tokens=1), error=dict(code='review_transport_failure'),
                observed_at='2026-11-14T00:%02d:%02d+00:00' % (index // 60, index % 60), recorded_revision=revision)
            if index >= 8 and index % 3 == 2 and index < attempts - 1:
                event = dict(id='grant-%d' % index, assignment_id=identifier, source='owner', kind='observation',
                             recorded_revision=revision + 1)
                records['review_context_event'][event['id']] = event
        state = dict(deliveries=[], carried_readings=[], searches=[])
        assignment['reading_chain'] = dict(state=artifacts.put_json(state))
        value = v7.build_unread_set(records, artifacts, assignment, inventory, state)
        reference = artifacts.put_json(value)
        state['unread'] = dict(assignment_id=identifier, artifact=reference, digest=digest(value),
                               admitted_revision=2 * attempts + 2)
        return artifacts, route, records, inventory, state, value

    def collect_pages(self, artifacts, route, state, name):
        rows, after = [], None
        while True:
            page = v7.build_list_page(name, after, {}, state, route, 12000, artifacts=artifacts)
            self.assertLessEqual(len(problem_openness.serialize_record(page).decode('utf-8')), 12000)
            rows.extend(page['rows'])
            state.setdefault('list_pages', {})[name] = dict(next=page['next'], ended=page['next'] is None)
            if page['next'] is None:
                break
            after = page['next']
        with self.assertRaises(ResearchError):
            v7.build_list_page(name, after, {}, state, route, 12000, artifacts=artifacts)
        return rows

    def collect_planned_pages(self, artifacts, unread, name):
        from research_harness import review_route_capacity as capacity
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        _, chain = fixture.prepare()
        fixture.artifacts.bodies.update(artifacts.bodies)
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        state['unread'] = copy.deepcopy(unread)
        limits = capacity.derive_plan_task_limits(plan, 'final')
        rows, after = [], None
        for number in range(100):
            packet = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', number + 2,
                inventory, state, plan, limits, 'final', requests=[dict(id='page-%d' % number,
                    kind='list_page', list=name, after=after)])
            prompt = protocol.canonical_prompt('native_math', reading_stage='final', reading_protocol=v7.PROTOCOL,
                capacity=packet['reading_final']['capacity'], problem_paragraphs=['exactory-openness-unread-final-v1'])
            request = protocol.transport.request_body(fixture.route, prompt['text'], packet)
            comparison = capacity.describe_plan_preflight({}, fixture.route, request, plan, 'final')['plan_comparison']
            self.assertTrue(comparison['fits'], comparison)
            page = json.loads(packet['evidence'][0]['content'])
            rows.extend(page['rows'])
            state.setdefault('list_pages', {})[name] = dict(next=page['next'], ended=page['next'] is None)
            if page['next'] is None:
                return rows
            after = page['next']
        self.fail('The complete fixed list must end within its finite row count.')

    def test_large_both_cause_set_pages_reproduce_the_complete_canonical_artifact(self):
        artifacts, route, records, inventory, state, value = self.build_fixture()
        self.assertGreater(len(problem_openness.serialize_record(value)), 30000)
        self.assertEqual({row['cause'] for row in value['rows']}, {'provider_refused', 'not_delivered'})
        packet = v7.describe_unread_packet(artifacts, state)
        self.assertGreater(packet['omitted'], 0)
        self.assertLessEqual(len(problem_openness.serialize_record(packet).decode('utf-8')), 30000)
        self.assertEqual(packet['rows'], value['rows'][:len(packet['rows'])])
        rows = self.collect_pages(artifacts, route, state, 'unread')
        attempts = self.collect_pages(artifacts, route, state, 'unread_attempts')
        self.assertEqual(rows, value['rows'])
        self.assertEqual(attempts, value['attempts'])
        self.assertEqual(digest(dict(rows=rows, attempts=attempts)), packet['digest'])

    def test_300_attempts_unshown_attempt_tampering_fails_coverage_verification(self):
        artifacts, route, records, inventory, state, value = self.build_fixture(attempts=300, units=2)
        packet = dict(reading_final=dict(unread=v7.describe_unread_packet(artifacts, state)))
        output = dict(findings=dict(openness=dict(outcome='unresolved', grounds=[dict(
            kind='reviewer_gap', refs=[state['unread']['artifact']])])))
        plan = dict(task=dict(reading=dict(material_allowance=15000)))
        self.assertEqual(v7._verify_required_coverage(artifacts, inventory, state, plan, {},
            records=records, packet=packet, output=output), [])
        reference = state['unread']['artifact']
        changed = copy.deepcopy(value)
        changed['attempts'][-1]['sequence'] += 1
        artifacts.bodies[reference['sha256']] = problem_openness.serialize_record(changed)
        with self.assertRaises(ResearchError) as caught:
            v7._verify_required_coverage(artifacts, inventory, state, plan, {}, records=records, packet=packet, output=output)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')

    def test_requested_unread_page_precedes_optional_old_note_fill(self):
        from research_harness import review_route_capacity as capacity
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        _, chain = fixture.prepare()
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        value = dict(rows=[dict(id='u1:1:%d' % index, unit='u1', label='x' * 120, part=1,
            start=index, end=index + 1, characters=1, cause='not_delivered') for index in range(300)], attempts=[])
        reference = fixture.artifacts.put_json(value)
        state['unread'] = dict(artifact=reference, digest=digest(value))
        state['latest_notes'] = 'Current notes.'
        state['notes'] = [dict(classification='current', turn=index + 1, text='n' * 1000) for index in range(100)]
        limits = capacity.derive_plan_task_limits(plan, 'final')
        packet = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2,
            inventory, state, plan, limits, 'final',
            requests=[dict(id='page', kind='list_page', list='unread', after=None)])
        self.assertTrue(packet['evidence'], 'A requested page must have room before optional old notes.')
        page = json.loads(packet['evidence'][0]['content'])
        self.assertTrue(page['rows'])
        self.assertEqual(page['rows'][0], value['rows'][0])

    def test_saturated_retention_admits_unread_and_fetches_a_page_within_the_plan(self):
        from research_harness import review_route_capacity as capacity
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        _, chain = fixture.prepare()
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        body = inventory['material'][0]
        source = dict(handle=body['handle'], kind=body['kind'], label=body['label'])
        source_cost = capacity.measure_capacity_value(fixture.route, source)
        state['latest_notes'] = 'n' * state['allowances']['notes']
        for target in state['allowances']['targets']:
            left = target['characters']
            for number in range(3):
                entry = dict(handle='e%d' % (len(state['entries']) + 1), kind='excerpt', unit=body['handle'],
                    targets=[target['handle']], start=0, end=1, sha256='a' * 64,
                    text='', purpose='A retained detail.', status='verified')
                overhead = capacity.measure_capacity_value(fixture.route, dict(entry, handle='e999999999')) + source_cost
                count = min(6000, max(0, left - overhead - 20))
                entry.update(text='a' * count, end=count)
                left -= capacity.measure_capacity_value(fixture.route, dict(entry, handle='e999999999')) + source_cost
                state['entries'].append(entry)
        value = dict(rows=[dict(id='u1:1:%d' % index, unit='u1', label='x' * 120, part=1,
            start=index, end=index + 1, characters=1, cause='not_delivered') for index in range(300)], attempts=[])
        reference = fixture.artifacts.put_json(value)
        state['unread'] = dict(artifact=reference, digest=digest(value))
        limits = capacity.derive_plan_task_limits(plan, 'final')
        prompt = protocol.canonical_prompt('native_math', reading_stage='final', reading_protocol=v7.PROTOCOL,
            problem_paragraphs=['exactory-openness-unread-final-v1'])
        for requests in ([], [dict(id='page', kind='list_page', list='unread', after=None)]):
            packet = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2,
                inventory, state, plan, limits, 'final', requests=requests)
            material = {key: packet['reading_final'][key] for key in v7.FINAL_MATERIAL_FIELDS}
            used = v7._measure_final_material(fixture.route, material)
            self.assertGreater(used, limits['material_allowance'] - 5000)
            request = protocol.transport.request_body(fixture.route, prompt['text'], packet)
            comparison = capacity.describe_plan_preflight({}, fixture.route, request, plan, 'final')['plan_comparison']
            self.assertTrue(comparison['fits'], comparison)
            if requests:
                self.assertTrue(packet['evidence'])

    def test_k4_unread_schema_declares_closed_rows_context_and_packet_bounds(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        registry = read_packet_registry()
        artifacts, route, records, inventory, state, value = self.build_fixture(units=2)
        definitions = registry['definitions']
        self.assertEqual(definitions['stage3_unread_final']['bound']['serialized_characters'], 30000)
        for name in ('stage3_unread_row', 'stage3_unread_attempt'):
            self.assertEqual(definitions[name]['bound']['serialized_characters'], 2000)
        self.assertEqual(definitions['stage3_unread_context']['members']['assignment_id']['bound']['characters'], 120)
        registry = dict(registry, open_members=[], certificates={}, roles=dict(unread_fixture=dict(
            members=dict(reading_final=dict(members=dict(unread={'$ref': 'stage3_unread_final'}))))))
        packet = dict(reading_final=dict(unread=v7.describe_unread_packet(artifacts, state)))
        self.assertEqual(validate_packet('unread_fixture', packet, registry), [])
        packet['reading_final']['unread']['rows'][0]['undeclared'] = True
        self.assertTrue(validate_packet('unread_fixture', packet, registry))

    def test_refused_list_only_position_keeps_attempts_without_inventing_a_source_range(self):
        artifacts, route, records, inventory, state, value = self.build_fixture(units=2)
        origin = next(iter(records['review_assignment'].values()))
        origin['packet'] = artifacts.put_json(dict(reading_turn=dict(plan=dict(now=[dict(
            handle='list:allowances', kind='list_page', start=0, end=200)]))))
        for complete in (False, True):
            before = dict(deliveries=[dict(unit=body['handle'], start=0, end=body['characters'])
                for body in inventory['material']] if complete else [], carried_readings=[])
            with self.subTest(complete=complete):
                unread = v7.build_unread_set(records, artifacts, origin, inventory, before)
                self.assertEqual(len(unread['attempts']), 9)
                self.assertTrue(all(row['row'] is None for row in unread['attempts']))
                self.assertTrue(all(row['cause'] == 'not_delivered' for row in unread['rows']))
                self.assertEqual(len(unread['rows']), 0 if complete else 2)

    def test_300_attempts_with_escaped_reserved_identifiers_fit_serialized_row_and_page_bounds(self):
        artifacts, route, records, inventory, state, value = self.build_fixture(attempts=300, units=2)
        origin = next(iter(records['review_assignment'].values()))
        identifier = '\x02' * 120
        origin['id'] = identifier
        records['review_assignment'] = {identifier: origin}
        attempts = {}
        for index, row in enumerate(records['review_attempt'].values()):
            row.update(id=('a%03d' % index) + '\x01' * 116, assignment_id=identifier)
            attempts[row['id']] = row
        records['review_attempt'] = attempts
        for event in records['review_context_event'].values():
            event['assignment_id'] = identifier
        value = v7.build_unread_set(records, artifacts, origin, inventory, dict(deliveries=[], carried_readings=[]))
        self.assertTrue(all(len(problem_openness.serialize_record(row).decode('utf-8')) <= 2000
                            for row in value['attempts']))
        reference = artifacts.put_json(value)
        state['unread'] = dict(state['unread'], assignment_id=identifier, artifact=reference, digest=digest(value))
        self.assertEqual(self.collect_pages(artifacts, route, state, 'unread_attempts'), value['attempts'])
        self.assertEqual(self.collect_planned_pages(artifacts, state['unread'], 'unread_attempts'), value['attempts'])

    def test_300_attempts_with_reserved_identifiers_have_fixed_size_rows_and_all_details(self):
        artifacts, route, records, inventory, state, value = self.build_fixture(attempts=300, units=2)
        row = value['rows'][0]
        self.assertLessEqual(len(problem_openness.serialize_record(row).decode('utf-8')), 2000)
        self.assertEqual(row['attempts']['count'], 300)
        self.assertEqual(row['attempts']['sequences'], 100)
        self.assertEqual(row['attempts']['outcomes'], dict(provider_refused=300))
        self.assertEqual(records['review_attempt'][value['attempts'][-1]['attempt_id']]['observed_at'],
                         row['attempts']['last'])
        self.assertTrue(all(len(problem_openness.serialize_record(item).decode('utf-8')) <= 2000
                            for item in value['attempts']))
        self.assertEqual(self.collect_pages(artifacts, route, state, 'unread_attempts'), value['attempts'])
        self.assertEqual(self.collect_pages(artifacts, route, state, 'unread'), value['rows'])
        planned_rows = self.collect_planned_pages(artifacts, state['unread'], 'unread')
        planned_attempts = self.collect_planned_pages(artifacts, state['unread'], 'unread_attempts')
        self.assertEqual(planned_rows, value['rows'])
        self.assertEqual(planned_attempts, value['attempts'])
        self.assertEqual(digest(dict(rows=planned_rows, attempts=planned_attempts)), state['unread']['digest'])


class UnreadPagingTests(UnreadFinalSupport):
    text_sizes = (200, 200, 200)

    def test_fixed_artifact_order_survives_pages_and_replacement_and_rejects_omitted_tampering(self):
        from research_harness import review_route_capacity as capacity
        from test_review_output_profile import build_row
        original = v7.build_inventory
        def build(records, artifacts, native, *, review=None):
            result = original(records, artifacts, native, review=review)
            inventory = result['inventory']
            by_digest = {row['sha256']: row for row in inventory['material']}
            for index in range(2300):
                content = ('%06d' % index + 'r' * 194).encode('utf-8')
                reference = v7.describe_artifact(content, 'text/plain')
                unit = v7._append_unit(inventory, dict(kind='record', record_kind='synthetic_required',
                    record_id='required-%d' % index, artifact=reference), content)
                v7._bind_body(inventory, by_digest, unit, reference, content, 'record')
                result['bodies'][reference['sha256']] = content
            return result
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(v7, 'build_inventory', side_effect=build), patch.object(
                capacity, 'read_profile_priors', return_value=priors):
            native, final, packet = self.reach_unread()
            unread = packet['reading_final']['unread']
            artifact = json.loads(self.artifacts.read(unread['artifact']))
            self.assertGreaterEqual(sum(row['cause'] == 'not_delivered' for row in artifact['rows']), 2000)
            self.assertGreater(unread['omitted'], 0)
            self.assertLessEqual(len(problem_openness.serialize_record(unread).decode('utf-8')), 30000)
            current = final['id']
            received = dict(unread=[], unread_attempts=[])
            for name in ('unread', 'unread_attempts'):
                after = None
                for number in range(100):
                    packet = self.read_packet(current)
                    # The current page's next cursor is visible to the answer of that turn.
                    for item in packet['evidence']:
                        page = json.loads(item['content'])
                        if page.get('list') == name:
                            received[name].extend(page['rows'])
                            after = page['next']
                    if received[name] and after is None:
                        break
                    answer = self.build_v7_answer(packet)
                    answer['passage_requests'] = [dict(id='%s-%d' % (name, number), kind='list_page', list=name, after=after)]
                    self.invoke(current, answer)
                    action = plan_next(self.store.snapshot()['records'], self.artifacts, current)
                    self.assertEqual(action['state'], 'continue', action)
                    assigned = self.mutate(protocol.record_assignment, action['payload'])['result']
                    current = assigned['id']
                    packet = self.read_packet(current)
                    self.assertIn('reading_final', packet)
                    self.assertEqual(packet['reading_final']['unread']['digest'], unread['digest'])
                    records = self.store.snapshot()['records']
                    request = protocol.build_assignment_request(records, self.artifacts, assigned)
                    plan = capacity.read_chain_capacity_plan(self.artifacts, assigned)
                    self.assertTrue(capacity.describe_plan_preflight(records, records['review_route']['route'],
                        request, plan, 'final')['plan_comparison']['fits'])
                    if name == 'unread' and number == 1:
                        bad = self.build_v7_finding(packet, 'x1')
                        bad['findings']['openness']['outcome'] = 'supported_open'
                        refused = self.invoke(current, bad)
                        self.assertEqual(refused['output_refusal']['check'], 'openness.finding')
                        action = plan_next(self.store.snapshot()['records'], self.artifacts, current)
                        self.assertEqual(action['state'], 'replace', action)
                        assigned = self.mutate(protocol.record_assignment, action['payload'])['result']
                        current = assigned['id']
                        self.assertEqual(self.read_packet(current)['reading_final']['unread']['digest'], unread['digest'])
                else:
                    self.fail('Every fixed artifact list must end.')
            self.assertEqual(received['unread'], artifact['rows'])
            self.assertEqual(received['unread_attempts'], artifact['attempts'])
            self.assertEqual(digest(dict(rows=received['unread'], attempts=received['unread_attempts'])), unread['digest'])
            packet = self.read_packet(current)
            invalid = self.build_v7_answer(packet)
            invalid['passage_requests'] = [dict(id='wrong-cursor', kind='list_page', list='unread', after='missing')]
            refused = self.invoke(current, invalid)
            self.assertEqual(refused['output_refusal']['check'], 'native.output')
            action = plan_next(self.store.snapshot()['records'], self.artifacts, current)
            self.assertEqual(action['state'], 'replace', action)
            assigned = self.mutate(protocol.record_assignment, action['payload'])['result']
            current = assigned['id']
            record = self.record_final(assigned, self.read_packet(current))
            self.assertEqual(record['grounds'][0]['refs'][0]['sha256'], unread['digest'])
            records = self.store.snapshot()['records']
            for mutation in ('omitted', 'removed', 'attempt', 'finding'):
                with self.subTest(mutation=mutation):
                    changed = copy.deepcopy(records)
                    artifacts = v7._PlanningArtifacts(self.artifacts)
                    value = copy.deepcopy(artifact)
                    if mutation == 'omitted':
                        value['rows'][len(unread['rows'])]['characters'] += 1
                    elif mutation == 'removed':
                        value['rows'].pop()
                    elif mutation == 'attempt':
                        value['attempts'][-1]['time'] = '2026-01-01T00:00:00+00:00'
                    else:
                        attempt = changed['review_attempt']['attempt-' + current]
                        output = json.loads(artifacts.read(attempt['output']))
                        output['findings']['openness']['grounds'][0]['refs'][0]['sha256'] = 'f' * 64
                        attempt['output'] = artifacts.put(problem_openness.serialize_record(output), 'application/json')
                    if mutation != 'finding':
                        artifacts.bodies[unread['digest']] = problem_openness.serialize_record(value)
                    with self.assertRaises(ResearchError):
                        reading.verify_chain(changed, artifacts, changed['review_assignment'][current])


class IncidentUnreadTests(UnreadFinalSupport):
    # The reading frame of a chain with a finding index reserves 1,500 characters for the finding-units paragraph
    # (G8.11 of 0.92.0). On this route the reading task at the material level of 120,000 then passes the measured task
    # level of 240,000, so a reading turn delivers at most 60,000 characters; nine texts of 40,000 characters keep the
    # shape of the incident: six completed turns leave one text partly unread.
    text_sizes = (40000,) * 9
    completed_prefix = 6

    def test_g1_k7_six_completed_turns_survive_refusal_at_turn_seven_and_next_export_reads_one_text(self):
        _, final, packet = self.reach_unread()
        result = self.record_final(final, packet)
        self.assertEqual(len(result['reading_turns']), 7)
        self.assertEqual([row['number'] for row in result['reading_turns']], list(range(1, 8)))
        native = self.export(name='incident-successor-packet', assignment_id='incident-successor')
        receipt = self.assign('incident-successor', native)
        plan, inventory, state = v7.read_bound_state(self.artifacts, receipt, self.read_packet('incident-successor'))
        missing_texts = [(row, start, end) for row, start, end in v7._find_remaining_ranges(inventory, state)
                         if row['kind'] == 'fulltext']
        self.assertEqual(len(missing_texts), 1)
        self.assertGreater(missing_texts[0][1], 0)
        self.assertEqual(len(json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))['turns']), 6)
        head, sent_texts = 'incident-successor', []
        for number in range(20):
            supplied = self.read_packet(head)
            run = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertEqual(run['state'], 'run', run)
            if 'reading_final' in supplied:
                self.invoke(head, self.build_v7_finding(supplied, supplied['reading_final']['notes_handle']),
                            attempt=run['payload']['id'])
                result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))['result']
                self.assertEqual(result['outcome'], 'unresolved')
                records = self.store.snapshot()['records']
                self.assertTrue(reading.verify_chain(records, self.artifacts, records['review_assignment'][head]))
                break
            self.invoke(head, self.build_v7_answer(supplied), attempt=run['payload']['id'])
            attempt = self.store.snapshot()['records']['review_attempt'][run['payload']['id']]
            request = json.loads(self.artifacts.read(attempt['request']))
            delivered = protocol.review_excerpts.read_sent_packet(request)
            sent_texts.extend(row for row in delivered['reading_turn']['plan']['now'] if row['kind'] == 'fulltext')
            action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            head = self.mutate(protocol.record_assignment, action['payload'])['result']['id']
        else:
            self.fail('The successor must finish after the one remaining text range.')
        self.assertEqual([(row['start'], row['end']) for row in sent_texts],
                         [(missing_texts[0][1], missing_texts[0][2])])



class LegacyUnreadTests(UnreadFinalSupport):
    historical_v5 = True
    text_sizes = (70000, 70000, 70000, 70000)

    def test_g1_k7_incident_v5_two_safeguards_retry_then_last_sequence_names_row4(self):
        self.limit_input(1300000)
        native = self.export()
        self.assign('turn-1', native)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1'), excerpts=[]))
        self.assign('refused', native, prior='turn-1')
        for number in range(2):
            self.refuse_provider('refused', 'legacy-provider-%d' % number)
            action = plan_next(self.store.snapshot()['records'], self.artifacts, 'refused')
            self.assertEqual(action['state'], 'run', action)
        for number in range(2, 9):
            self.refuse_provider('refused', 'legacy-provider-%d' % number)
        action = plan_next(self.store.snapshot()['records'], self.artifacts, 'refused')
        self.assertEqual(action['state'], 'stop', action)
        self.assertEqual(action['stop_class'], 'decision')
        self.assertEqual(action['details']['row'], 4)
        step = action['details']['decision']['then'][-1]['steps'][0]
        self.assertEqual(step['id'], 4)
        self.assertEqual(step['then'][-1]['payload']['route_id'], 'route')
        self.historical_v5 = False
        successor = self.export(name='successor-packet', assignment_id='successor')
        receipt = self.assign('successor', successor)
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        self.assertTrue(receipt['carried_readings']['chains'])


class UnreadCompatibilityTests(unittest.TestCase):
    def test_no_unread_v7_final_and_prompt_keep_the_frozen_bytes(self):
        from test_openness_reading_v7_refusal import NoticeRoomTests
        fixture = NoticeRoomTests()
        fixture.test_t7_no_notice_packets_change_only_plan_digest_and_keep_prompt_hashes()
        self.assertEqual(reading.NEW_PROBLEM_CHAIN_PROTOCOLS, (reading.PROTOCOL_V7, reading.PROTOCOL_V6))

    def test_without_unread_validator_keeps_existing_v6_v7_and_single_turn_rules(self):
        from research_harness.native_review import validate_finding
        native = turn_fixtures.build_native()
        native['scientific_context']['audit_forecast']['outcomes']['supported_open'] = 'possible'
        packet = dict(native=native)
        for protocol_name in (None, reading.PROTOCOL_V6, v7.PROTOCOL):
            supplied = copy.deepcopy(packet)
            if protocol_name == v7.PROTOCOL:
                supplied['reading_final'] = dict(protocol=protocol_name, page=dict(targets=['t1']))
            elif protocol_name:
                supplied['reading_final'] = dict(protocol=protocol_name)
            for outcome, grounds, accepted in (
                    ('supported_open', [], True), ('already_solved', [], False),
                    ('unresolved', [], False), ('unresolved', [dict(kind='reviewer_gap', text='A gap.')], True)):
                output = dict(schema_version=3, **{key: native[key] for key in
                    ('subject_digest', 'claim_digest', 'reviewer')}, findings=dict(
                        {key: 'Checked.' for key in ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                        openness=dict(outcome=outcome, coverage='Checked coverage.', reason='A finding.', grounds=grounds)))
                with self.subTest(protocol=protocol_name, outcome=outcome, accepted=accepted):
                    if accepted:
                        validate_finding(supplied, output)
                    else:
                        with self.assertRaises(ResearchError):
                            validate_finding(supplied, output)


class UnreadNoticeReplacementTests(UnreadFinalSupport):
    """U-T1/U-T2: full grants and a large real-shape unread set share the notice reserve."""
    text_sizes = (1500000,)

    def prepare_filled_unread(self):
        from test_openness_reading_v7_refusal import FullGrantFinalRefusalTests
        original = v7.build_inventory

        def build(records, artifacts, native, *, review=None):
            result = original(records, artifacts, native, review=review)
            inventory = result['inventory']
            by_digest = {row['sha256']: row for row in inventory['material']}
            for index in range(400):
                content = ('%06d' % index + 'r' * 19994).encode('utf-8')
                reference = v7.describe_artifact(content, 'text/plain')
                unit = v7._append_unit(inventory, dict(kind='record', record_kind='synthetic_required',
                    record_id='required-%d' % index, artifact=reference), content)
                v7._bind_body(inventory, by_digest, unit, reference, content, 'record')
                result['bodies'][reference['sha256']] = content
            return result

        enabled = patch.object(v7, 'build_inventory', side_effect=build)
        enabled.start()
        self.addCleanup(enabled.stop)
        head = FullGrantFinalRefusalTests.prepare_full_final(self, stop_when_full=True)
        native = self.store.snapshot()['records']['review_assignment'][head]['context']['native_packet']
        _, final, packet = self.exhaust_head(native, head)
        unread = packet['reading_final']['unread']
        value = json.loads(self.artifacts.read(unread['artifact']))
        self.assertGreater(len(problem_openness.serialize_record(value)), 30000)
        self.assertGreater(unread['omitted'], 0)
        from research_harness import review_route_capacity as capacity
        route = self.store.snapshot()['records']['review_route']['route']
        self.assertGreater(v7._measure_final_material(route,
            {key: packet['reading_final'][key] for key in v7.FINAL_MATERIAL_FIELDS}), 476000)
        return final, packet

    def record_complete_set(self, final, packet):
        self.invoke(final['id'], self.build_v7_finding(packet, 'x1'))
        records = self.store.snapshot()['records']
        self.assertTrue(reading.verify_chain(records, self.artifacts, records['review_assignment'][final['id']]))
        result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final['id']))['result']
        unread = packet['reading_final']['unread']
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(result['unread']['rows'], json.loads(self.artifacts.read(unread['artifact']))['rows'])
        self.assertGreater(len(result['unread']['rows']), len(unread['rows']))

    def replace_refused_final(self, final, packet, characters):
        from test_openness_reading_v7_refusal import FullGrantFinalRefusalTests
        from research_harness import review_route_capacity as capacity
        FullGrantFinalRefusalTests.refuse_final(self, final['id'], characters)
        records = self.store.snapshot()['records']
        action = plan_next(records, self.artifacts, final['id'])
        self.assertEqual(action['state'], 'replace', action)
        assigned = self.mutate(protocol.record_assignment, action['payload'])['result']
        following = self.read_packet(assigned['id'])
        before, after = packet['reading_final'], following['reading_final']
        for key in v7.FINAL_MATERIAL_FIELDS:
            self.assertEqual(after[key], before[key], key)
        for key in ('count', 'causes', 'artifact', 'digest'):
            self.assertEqual(after['unread'][key], before['unread'][key], key)
        full_rows = json.loads(self.artifacts.read(after['unread']['artifact']))['rows']
        self.assertEqual(after['unread']['rows'], full_rows[:len(after['unread']['rows'])])
        request = protocol.build_assignment_request(self.store.snapshot()['records'], self.artifacts, assigned)
        plan = capacity.read_chain_capacity_plan(self.artifacts, assigned)
        self.assertTrue(capacity.describe_plan_preflight(records, records['review_route']['route'],
            request, plan, 'final')['plan_comparison']['fits'])
        return assigned, following

    def test_u_t5_real_full_grant_fixture_preserves_the_final_level_and_allocation(self):
        from research_harness import review_route_capacity as capacity
        from research_harness.native_review import load_packet
        from test_openness_reading_v7_refusal import FullGrantFinalRefusalTests
        from test_review_output_profile import build_row
        head = FullGrantFinalRefusalTests.prepare_full_final(self, stop_after_assignment=True)
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][head]
        route = records['review_route']['route']
        plan = capacity.read_chain_capacity_plan(self.artifacts, assignment)
        native = load_packet(self.artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(v7, 'UNREAD_CORE_CHARACTERS', 0), patch.object(capacity,
                'read_profile_priors', return_value=priors):
            admission = v7.check_v7_admission(records, self.artifacts, route, native)
        self.assertEqual(admission['state'], 'record')
        previous = admission['capacity_plan']
        self.assertEqual(plan['task']['final']['material_allowance'], 480000)
        self.assertEqual(previous['task']['final']['material_allowance'], plan['task']['final']['material_allowance'])
        for key in ('page_room', 'carried_turn_pool', 'notes', 'targets'):
            self.assertEqual(previous['allocation'][key], plan['allocation'][key], key)
        self.assertEqual(plan['frame']['final']['bound'] - previous['frame']['final']['bound'], 1500)

    def test_u_t1_short_and_long_refused_full_unread_final_replace_and_record_the_complete_set(self):
        final, packet = self.prepare_filled_unread()
        short, short_packet = self.replace_refused_final(final, packet, 4000)
        long, long_packet = self.replace_refused_final(short, short_packet, 100000)
        self.assertEqual(short_packet['output_refusal']['form'], 'whole')
        self.assertEqual(long_packet['output_refusal']['form'], 'bounded')
        self.assertLess(len(long_packet['reading_final']['unread']['rows']),
                        len(short_packet['reading_final']['unread']['rows']))
        self.record_complete_set(long, long_packet)

    def test_u_t2_refused_pending_unread_page_replays_the_same_cursor_and_records(self):
        final, packet = self.prepare_filled_unread()
        answer = self.build_v7_answer(packet)
        answer['notes'] = packet['reading_final']['notes']
        answer['passage_requests'] = [dict(id='unread-page', kind='list_page', list='unread', after=None)]
        self.invoke(final['id'], answer)
        action = plan_next(self.store.snapshot()['records'], self.artifacts, final['id'])
        paged = self.mutate(protocol.record_assignment, action['payload'])['result']
        packet = self.read_packet(paged['id'])
        page = json.loads(packet['evidence'][0]['content'])
        self.assertEqual((page['list'], page['after']), ('unread', None))
        replaced, following = self.replace_refused_final(paged, packet, 100000)
        repeated = json.loads(following['evidence'][0]['content'])
        self.assertEqual((repeated['list'], repeated['after']), (page['list'], page['after']))
        self.assertTrue(repeated['rows'])
        self.assertEqual(repeated['rows'], page['rows'][:len(repeated['rows'])])
        self.record_complete_set(replaced, following)


class UnreadFrameTests(unittest.TestCase):
    def build_unread_value(self, artifacts):
        reference = artifacts.put(b'A synthetic unread text.', 'text/plain')
        value = dict(rows=[dict(id='u1:1:%d' % index, unit='u1', label='x' * 120, part=index + 1,
            start=index, end=index + 1, characters=1, cause='not_delivered', artifact=reference)
            for index in range(300)], attempts=[])
        return dict(artifact=artifacts.put_json(value), digest=digest(value))

    def check_unread_wire_case(self, fixture, logical, identifier, inventory, state, plan, limits):
        from research_harness import review_route_capacity as capacity, review_prompt_paragraphs as paragraphs
        from test_openness_reading_v7_refusal import NoticeRoomTests
        state = dict(state, unread=self.build_unread_value(fixture.artifacts))
        page_request = dict(id='unread-page', kind='list_page', list='unread', after=None)
        bound = plan['task']['final']['request_bound']['text_characters']
        base = protocol.canonical_prompt('native_math', reading_stage='final', reading_protocol=v7.PROTOCOL)
        for size in (45000, 30000, 0):
            notice = NoticeRoomTests().build_bounded_notice(fixture.artifacts, route=fixture.route)
            if 0 < size < 45000:
                notice['refused'] = notice['refused'][:1]
                notice['output_ref']['artifact']['path'] += 'x' * (size - capacity.measure_capacity_value(fixture.route, notice))
            if size:
                self.assertEqual(capacity.measure_capacity_value(fixture.route, notice), size)
            else:
                notice = None
            packet = v7.build_turn_packet(fixture.artifacts, fixture.route, logical, identifier, 999999999,
                inventory, state, plan, limits, 'final', requests=[page_request], notice=notice)
            binding = packet['reading_final']
            self.assertEqual(binding['unread']['count'], 300)
            self.assertEqual(plan['frame']['final']['fields']['unread'], v7.UNREAD_CORE_CHARACTERS)
            versions = [paragraphs.UNREAD_FINAL_PARAGRAPH_VERSION]
            if size:
                if size == 45000:
                    self.assertEqual(binding['unread']['rows'], [])
                    self.assertEqual(packet['evidence'], [])
                    prompt = paragraphs.append_prompt_paragraphs(base, 'native_math', versions=versions)
                    request = protocol.transport.request_body(fixture.route, prompt['text'], packet)
                    self.assertLessEqual(capacity.measure_request(fixture.route, request)['text_characters'], bound - 47000)
                else:
                    self.assertTrue(packet['evidence'])
                from research_harness.review_refusal_notice import attach_refusal_notice
                packet = attach_refusal_notice(packet, notice)
                versions.append(paragraphs.REFUSAL_PARAGRAPH_VERSIONS['bounded'])
            else:
                self.assertTrue(packet['evidence'])
                member_size = capacity.measure_capacity_value(fixture.route, binding['unread'])
                self.assertLessEqual(member_size, 30000)
                self.assertGreater(member_size, 29000)
            prompt = paragraphs.append_prompt_paragraphs(base, 'native_math', versions=versions)
            request = protocol.transport.request_body(fixture.route, prompt['text'], packet)
            self.assertLessEqual(capacity.measure_request(fixture.route, request)['text_characters'], bound)

    def test_u_t3_every_admitted_count_and_label_set_keeps_unread_and_notice_within_the_bound(self):
        from test_openness_reading_v7_refusal import NoticeRoomTests
        NoticeRoomTests().run_final_wire_matrix(unread=True)

    def test_u_t4_largest_unread_core_with_key_and_paragraph_fits_the_frame(self):
        from research_harness import review_route_capacity as capacity, review_prompt_paragraphs as paragraphs
        artifacts, route, _, _, state, _ = UnreadArtifactTests().build_fixture(units=2)
        core = v7.describe_unread_packet(artifacts, state, route=route, allowance=0)
        largest = 9999999999999999999
        core.update(count=largest, omitted=largest,
            causes={cause:dict(rows=largest, characters=largest) for cause in core['causes']})
        core['artifact']['size'] = largest
        self.assertEqual(core['rows'], [])
        charge = capacity.measure_capacity_value(route, dict(unread=core)) + len(paragraphs.UNREAD_FINAL_PROMPT)
        self.assertLessEqual(charge, v7.UNREAD_CORE_CHARACTERS)

    def test_u_t5_unread_frame_preserves_levels_and_allocation_on_both_fixtures(self):
        from research_harness import review_route_capacity as capacity
        from test_review_capacity_closure import build_capacity_records
        from test_review_output_profile import build_row
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        records = build_capacity_records(fixture.route, tokens=487900, capacity_probe=True)
        for targets, priors in ((3, []), (8, [build_row(level=None, thinking=1000, count=8),
                build_row(kind='final', level=None, thinking=1000, count=8)])):
            with self.subTest(targets=targets), patch.object(v7, 'build_inventory', return_value=fixture.built), patch.object(
                    capacity, 'read_profile_priors', return_value=priors):
                plans = []
                for reserve in (0, v7.UNREAD_CORE_CHARACTERS):
                    with patch.object(v7, 'UNREAD_CORE_CHARACTERS', reserve):
                        admitted = v7.check_v7_admission(records, fixture.artifacts, fixture.route,
                                                       turn_fixtures.build_native(targets))
                        self.assertEqual(admitted['state'], 'record')
                        plans.append(admitted['capacity_plan'])
                self.assertEqual(plans[0]['task']['final']['material_allowance'], plans[1]['task']['final']['material_allowance'])
                for key in ('page_room', 'carried_turn_pool', 'notes', 'targets'):
                    self.assertEqual(plans[0]['allocation'][key], plans[1]['allocation'][key], key)
                self.assertEqual(plans[1]['frame']['final']['bound'] - plans[0]['frame']['final']['bound'], 1500)

    def test_unread_rows_yield_free_material_room_to_a_whole_notice_above_the_reserve(self):
        from research_harness import review_route_capacity as capacity, review_prompt_paragraphs as paragraphs
        from research_harness.review_refusal_notice import attach_refusal_notice, PROTOCOL
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        _, chain = fixture.prepare()
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        state = json.loads(fixture.artifacts.read(chain['state']))
        limits = capacity.derive_plan_task_limits(plan, 'final')
        state['latest_notes'] = 'n' * (limits['material_allowance'] - 30000)
        state['unread'] = self.build_unread_value(fixture.artifacts)
        notice = dict(protocol=PROTOCOL, form='whole', refused=[], refused_output='')
        notice['refused_output'] = 'x' * (55000 - capacity.measure_capacity_value(fixture.route, notice))
        self.assertEqual(capacity.measure_capacity_value(fixture.route, notice), 55000)
        packet = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2,
            inventory, state, plan, limits, 'final', notice=notice)
        member = packet['reading_final']['unread']
        self.assertTrue(member['rows'])
        self.assertLessEqual(v7.compute_unread_rows_charge(fixture.route, member), 20000)
        self.assertEqual(packet['reading_final']['capacity']['material_allowance'], limits['material_allowance'])
        packet = attach_refusal_notice(packet, notice)
        prompt = protocol.canonical_prompt('native_math', reading_stage='final', reading_protocol=v7.PROTOCOL)
        prompt = paragraphs.append_prompt_paragraphs(prompt, 'native_math', versions=[
            paragraphs.UNREAD_FINAL_PARAGRAPH_VERSION, paragraphs.REFUSAL_PARAGRAPH_VERSIONS['whole']])
        request = protocol.transport.request_body(fixture.route, prompt['text'], packet)
        self.assertLessEqual(capacity.measure_request(fixture.route, request)['text_characters'],
                             limits['request_bound']['text_characters'])
