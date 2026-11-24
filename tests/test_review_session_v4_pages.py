"""Bounded final pages of source sessions v4, through ordinary review recording."""
import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, build_codex_response
import test_review_session_v4_turns as turn_fixtures
from research_harness import review_session_v4 as session, review_protocol as protocol
from research_harness import review_route_capacity as capacity, review_composition as composition
from research_harness import review_composite as composite, research_decisions as decisions
from research_harness.evidence import digest


class SessionPagesTests(unittest.TestCase):
    setUp = turn_fixtures.SessionTurnsTests.setUp
    read_packet = turn_fixtures.SessionTurnsTests.read_packet
    add_sources = turn_fixtures.SessionTurnsTests.add_sources
    respond = turn_fixtures.SessionTurnsTests.respond
    record_legacy_bars = turn_fixtures.SessionTurnsTests.record_legacy_bars

    def start(self, role='bar', targets=9, request_answer=None, continuation_prefix='read-', check=False,
              *, distinct_objections=False):
        if role != 'bar':
            self.record_legacy_bars()
        original = protocol.build_packet
        def build(*args, **kwargs):
            packet = original(*args, **kwargs)
            if packet['role'] == role:
                objections = [dict(id='objection-%d' % n,
                    claim='A synthetic material gap %d.' % n if distinct_objections else 'A synthetic material gap.',
                    evidence=[self.case.supporting], severity='material') for n in range(targets)]
                packet['historical_reviews'] = [dict(id='history', objections=objections)]
                packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
            return packet
        with patch.object(protocol, 'build_packet', side_effect=build):
            if check:
                from research_harness.check_mode import check_mutation
                payload = dict(id='start', route_id='route', dossier_id=None, role=role, reviewer_id='first', author_id='author', context={})
                revision = self.case.store.revision
                self.admission_check = check_mutation(self.case.store, protocol.record_assignment, payload,
                    expected_revision=revision, request_id='check-pages', bounded_payload=payload)
                self.assertEqual(self.case.store.revision, revision)
            current = self.case.assign(role, 'first', identifier='start')
        self.assertIn('session_chain', current)
        for number in range(100):
            if 'session_turn' not in self.read_packet(current):
                return current
            if request_answer is None:
                self.respond(current)
            else:
                request_answer(number, current, self.read_packet(current))
            current = self.case.assign(role, 'first', identifier=continuation_prefix + str(number),
                                       prior_assignment_id=current['id'])
        self.fail('The frame did not complete')

    def answer(self, assignment, evidence=()):
        plan, inventory, state = session.read_bound_session(self.case.artifacts, assignment)
        logical = protocol._read_json(self.case.artifacts, assignment['session_chain']['logical'])
        output = self.case.build_review_output(assignment['role'])
        output['value'].update(status='unresolved', evidence=list(evidence) or [self.case.supporting],
            objection_findings=[dict(id=row['id'], status='continuing', reason='A gap remains.', evidence=[self.case.supporting])
                                for row in logical.get('historical_reviews', [{}])[0].get('objections', [])])
        if assignment['role'] == 'slate':
            output['value']['rubric_potential'] = {}
            for row in output['value']['assurances']:
                row['status'] = 'not_assessed'
        packet = self.read_packet(assignment)
        binding = session.read_session_binding(packet)
        if 'session_combine' in packet:
            answer = dict(block=binding['block']['handle'], summary='Condensed entries.',
                refs=[binding['block']['entries'][0].get('handle', binding['block']['entries'][0].get('block'))])
            if getattr(self, 'fill_combinations', False):
                route = self.case.read_records()['review_route'][assignment['route_id']]
                low, high = 0, binding['capacity']['answer_bound']
                while low < high:
                    size = (low + high + 1) // 2
                    if capacity.measure_capacity_value(route, dict(answer, summary='x' * size)) <= binding['capacity']['answer_bound']:
                        low = size
                    else:
                        high = size - 1
                answer['summary'] = 'x' * low
            return answer
        if 'page_plan_digest' not in binding:
            return output
        rule_packet = session.build_composition_packet(self.case.artifacts, assignment, packet)
        rule = composition.find_rule(assignment['role'], rule_packet)
        targets = binding.get('page', {}).get('targets', plan['targets']['handles'])
        pages, closing = composition.split_output(rule, rule_packet, output, [targets])
        return closing if 'closing' in binding else pages[0]['output']

    def finish_pages(self, current):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        registry = read_packet_registry()
        seen = []
        for number in range(500):
            packet = self.read_packet(current)
            binding = session.read_session_binding(packet)
            self.assertEqual(validate_packet(current['role'], packet, registry), [])
            seen.append(packet)
            request = protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, current)
            route = self.case.read_records()['review_route']['route']
            self.assertLessEqual(capacity.measure_request(route, request)['text_characters'], binding['capacity']['request_bound'])
            if 'session_final' in packet:
                self.assertLessEqual(session.measure_final_material(route, binding), binding['capacity']['material_allowance'])
            if getattr(self, 'steady_output_tokens', None) is not None:
                self.invoke_usage(current, self.answer(current), self.steady_output_tokens)
            else:
                invoke_codex(self.case, current['id'], self.answer(current))
            checked = protocol.assignment_state(self.case.read_records(), self.case.artifacts, current['id'])
            self.assertTrue(checked['ready'], (checked['obligations'], self.case.read_records().get('review_output_refusal')))
            if 'closing' in binding:
                records = self.case.read_records()
                output, record = composite.read_final_output(records, self.case.artifacts, current)
                self.assertIsNotNone(record)
                self.assertEqual(output, composite.authenticate_composite(records, self.case.artifacts, record))
                saved = self.case.mutate(decisions.record_value_review, dict(output, id='review-pages',
                    dossier_id=None, assignment_id=current['id']))['result']
                self.assertEqual(saved['composite']['id'], record['id'])
                return current, seen, record
            current = self.case.assign(current['role'], 'first', identifier='final-%d' % self.case.store.revision,
                                       prior_assignment_id=current['id'])
        self.fail('The final did not close')

    def test_a5_a14_bar_pages_reach_recorded_review(self):
        current = self.start()
        final, packets, record = self.finish_pages(current)
        self.assertGreater(len(record['pages']), 1)

    def test_a5_a14_slate_pages_reach_recorded_review(self):
        self.finish_pages(self.start('slate'))

    def test_a5_a14_result_200_objections_reach_recorded_review(self):
        self.finish_pages(self.start('result', 200))

    def test_admission_forecasts_pages_condensing_and_closing(self):
        current = self.start(check=True)
        self.assertEqual(self.admission_check['check'], 'accepted')
        self.assertFalse(self.admission_check['recorded'])
        checked = self.admission_check['session_admission']
        self.assertEqual(checked['state'], 'record')
        self.assertGreater(checked['forecast']['by_kind']['final']['pages'], 1)
        self.assertEqual(checked['forecast']['by_kind']['final']['closing_pages'], 1)
        self.assertIn('condensing_turns', checked['forecast']['by_kind']['final'])
        final = current['capacity_forecast']['by_kind']['final']
        self.assertGreater(final['pages'], 1)
        self.assertEqual(final['closing_pages'], 1)
        self.assertIn('condensing_turns', final)

    def advance(self, current, output=None):
        output = self.answer(current) if output is None else output
        if getattr(self, 'steady_output_tokens', None) is not None:
            self.invoke_usage(current, output, self.steady_output_tokens)
        else:
            invoke_codex(self.case, current['id'], output)
        checked = protocol.assignment_state(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertTrue(checked['ready'], (checked['obligations'], self.case.read_records().get('review_output_refusal')))
        return self.case.assign(current['role'], 'first', identifier='advance-%d' % self.case.store.revision,
                                prior_assignment_id=current['id'])

    def fail_capacity(self, current, outcome='output_over_cap'):
        from research_harness import review_failure_class as failures
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            if outcome == 'output_over_cap':
                response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            else:
                from research_harness.errors import ResearchError
                if outcome == 'input_over_limit':
                    raise ResearchError('review_input_too_large', 'Synthetic input limit.')
                response.update(status='incomplete', execution=dict(timeout=True))
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.invoke_assignment, dict(id='cap-%d' % self.case.store.revision,
                assignment_id=current['id']), credential='test-only')
        return failures.plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])

    def replace_capacity(self, current, outcome='output_over_cap'):
        step = self.fail_capacity(current, outcome)
        if step['state'] == 'run':
            step = self.fail_capacity(current, outcome)
        self.assertEqual(step['state'], 'replace', step)
        return self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result']

    def test_a12_page_at_output_limit_splits_and_records(self):
        current = self.start(targets=9)
        before = self.read_packet(current)['session_final']['page']['targets']
        replacement = self.replace_capacity(current)
        after = self.read_packet(replacement)['session_final']['page']['targets']
        self.assertEqual(after, before[:(len(before) + 1) // 2])
        _, _, record = self.finish_pages(replacement)
        self.assertEqual(len({key for row in record['pages'] for key in row['targets']}), 9)

    def test_astra_b7_one_request_output_limit_replaced_by_pages(self):
        current = self.start(targets=7)
        self.assertNotIn('page', self.read_packet(current)['session_final'])
        self.finish_pages(self.replace_capacity(current))

    def test_astra_b7_one_request_input_limit_replaced_by_pages(self):
        self.finish_pages(self.replace_capacity(self.start(targets=7), 'input_over_limit'))

    def test_astra_b7_one_request_timeout_replaced_by_pages(self):
        self.finish_pages(self.replace_capacity(self.start(targets=7), 'timeout'))

    def test_refused_closing_replacement_keeps_page_plan_and_notice(self):
        from research_harness.review_failure_class import plan_after_failure
        current = self.start()
        while 'closing' not in self.read_packet(current).get('session_final', {}):
            current = self.advance(current)
        frozen = self.read_packet(current)
        output = self.answer(current)
        output['unexpected'] = True
        invoke_codex(self.case, current['id'], output)
        step = plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace', step)
        replacement = self.case.mutate(protocol.record_assignment, step['payload'])['result']
        self.assertIn('output_refusal', self.read_packet(replacement))
        self.assertEqual(frozen['session_final'], self.read_packet(replacement)['session_final'])
        self.finish_pages(replacement)

    def test_astra_b9_multipart_argument_resumes_pending_page_and_cites_handle(self):
        current = self.start()
        targets = self.read_packet(current)['session_final']['page']['targets']
        current = self.advance(current, dict(stage='material', title='Argument', part=1, text='First.', more=True))
        self.assertEqual(self.read_packet(current)['session_final']['page']['targets'], targets)
        current = self.advance(current, dict(stage='material', title='Argument', part=2, text='Last.', more=False))
        output = self.answer(current, [dict(ref='m1')])
        # The closing answer owns overall evidence; per-target findings can cite it too.
        first = output['targets'][0]['fields']
        for path in list(first):
            if path.endswith('/evidence'):
                first[path] = [dict(ref='m1')]
        current = self.advance(current, output)
        _, _, record = self.finish_pages(current)
        result = composite.authenticate_composite(self.case.read_records(), self.case.artifacts, record)
        self.assertTrue(any(self.case.artifacts.read(row['evidence'][0]) == b'First.Last.'
            for row in result['value']['objection_findings'] if row['evidence'][0].get('sha256')))

    def test_changed_plan_targets_and_output_fail_composite_authentication(self):
        from research_harness.errors import ResearchError
        final, _, record = self.finish_pages(self.start())
        records = self.case.read_records()
        page_id = record['pages'][0]['assignment_id']
        for mutation in ('plan', 'target', 'output'):
            changed = copy.deepcopy(records)
            page = changed['review_assignment'][page_id]
            if mutation == 'plan':
                reference = page['session_chain']['final_page_plan']
                value = protocol._read_json(self.case.artifacts, reference['artifact'])
                value['turns'][0]['targets'].reverse()
                reference.update(artifact=protocol._put_json(self.case.artifacts, value), digest=digest(value))
            elif mutation == 'target':
                packet = self.read_packet(page)
                packet['session_final']['page']['targets'] = record['pages'][1]['targets']
                page['packet'] = protocol._put_json(self.case.artifacts, packet)
            else:
                attempt = changed['review_attempt'][record['pages'][0]['attempt_id']]
                output = protocol._read_json(self.case.artifacts, attempt['output'])
                output['targets'][0]['sentence'] = 'Changed after recording.'
                attempt['output'] = protocol._put_json(self.case.artifacts, output)
            with self.subTest(mutation=mutation), self.assertRaises(ResearchError):
                composite.authenticate_composite(changed, self.case.artifacts, record)

    def test_page_floor_retries_then_decision_stop(self):
        from research_harness import review_failure_class as failures
        current = self.start()
        levels = []
        for number in range(20):
            binding = session.read_session_binding(self.read_packet(current))
            levels.append(len(binding.get('page', {}).get('targets', [])))
            step = self.fail_capacity(current)
            if step['state'] == 'replace':
                current = self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result']
            elif step['state'] == 'stop':
                self.assertEqual(step['code'], 'review_output_over_cap')
                self.assertEqual(step['details']['recovery']['kind'], 'decision')
                break
            else:
                self.assertEqual(step['state'], 'run', step)
        else:
            self.fail('The floor never stopped')
        self.assertEqual(levels[-2:], [1, 1])

    def test_page_cites_text_delivered_on_turn_three_and_composite_audit_credits_it(self):
        self.add_sources([4000, 4000])
        delivered = []
        def answer(number, current, packet):
            plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
            units = [row for row in inventory['units'] if row['kind'] == 'fulltext']
            requests = [dict(id='source-%d' % number, kind='unit', unit=units[number]['handle'])] if number < 2 else []
            if packet['session_turn']['turn'] == 3:
                delivered.extend(row['unit'] for row in packet['session_turn']['plan']['now'] if row.get('kind') == 'fulltext')
            self.respond(current, requests)
        current = self.start(request_answer=answer)
        self.assertEqual(len(delivered), 1)
        plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['handle'] == delivered[0])
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading
        version = unit['identity']['version_id']
        capture = self.case.read_records()['work'][version]['fulltexts'][0]
        bundle = self.case.bundle(version, capture=capture, bundle_id='citation-bundle')
        self.case.mutate(import_bundle, bundle)
        self.case.mutate(record_reading, self.case.full_note(bundle, note_id='citation-reading'))
        output = self.answer(current)
        for path in list(output['targets'][0]['fields']):
            if path.endswith('/evidence'):
                output['targets'][0]['fields'][path] = [dict(ref=unit['handle'], start=0, end=20)]
        current = self.advance(current, output)
        self.finish_pages(current)

    def invoke_usage(self, current, output, output_tokens=64000, room=None):
        import math
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, current)
        def respond(route, request, credential):
            response = build_codex_response(route, request, output)
            response['usage']['output_tokens'] = output_tokens
            if room is not None:
                # A measured ratio is kept to hundredths, so the page room moves in steps of one hundredth of the
                # input limit. The reported tokens give the smallest such room at or above the requested one.
                fixed = plan['frame']['final']['bound'] + plan['task']['final']['notice_characters']
                ratio = math.ceil((fixed + room) * 100 / plan['limits']['input_tokens']['value']) / 100
                size = capacity.measure_request(route, request)['text_characters']
                response['usage']['input_tokens'] = math.floor(size * (1 - capacity.TOKEN_RATIO_MARGIN) / ratio)
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.invoke_assignment, dict(id='attempt-' + current['id'], assignment_id=current['id']), credential='test-only')

    def limit_observed_input(self, tokens):
        """The route's probe observed this many input tokens, so a measured ratio moves the page room in steps of
        tokens / 100 characters. A room between the floor and the notes needs steps finer than the fixture probe's."""
        from research_harness import review_capacity_evidence as evidence
        read = evidence.read_capacity_evidence
        patcher = patch.object(evidence, 'read_capacity_evidence',
                               side_effect=lambda *args, **kwargs: dict(read(*args, **kwargs), input_tokens=tokens))
        patcher.start()
        self.addCleanup(patcher.stop)

    def install_final_profile(self, thinking=109000, rows=()):
        original = capacity.read_output_profile
        def profile(records, route, **kwargs):
            value = original(records, route, **kwargs)
            additions = []
            for level, tokens in [(None, thinking)] + list(rows):
                row = capacity._create_profile_row('final', level)
                row.update(attempts=10, completed_attempts=10, largest_output_tokens=100, largest_thinking_tokens=tokens)
                additions.append(row)
            value['rows'] = [row for row in value['rows'] if row['kind'] != 'final'] + additions
            return value
        return patch.object(capacity, 'read_output_profile', side_effect=profile)

    def test_astra_b3_completed_high_low_and_ratio_successors_replay(self):
        with self.install_final_profile():
            current = self.start(targets=9)
        earlier = {}
        for index, tokens in enumerate((100000, 10000, 64000)):
            earlier[current['id']] = digest(self.case.read_records()['review_assignment'][current['id']])
            previous = current['session_chain']['final_page_plan']['digest']
            self.invoke_usage(current, self.answer(current), tokens, room=30000 if index == 2 else None)
            current = self.case.assign('bar', 'first', identifier='adapt-%d' % index, prior_assignment_id=current['id'])
            reference = current['session_chain']['final_page_plan']
            successor = protocol._read_json(self.case.artifacts, reference['artifact'])
            self.assertEqual(successor['replaces'], previous)
            self.assertEqual(successor['cause']['kind'], 'ratio' if index == 2 else 'level')
            self.assertTrue(successor['cause']['completed'])
        self.finish_pages(current)
        records = self.case.read_records()
        for key, expected in earlier.items():
            self.assertEqual(digest(records['review_assignment'][key]), expected)

    def start_condensing_session(self, characters=600000, room=25000, notes_characters=6000):
        self.add_sources([characters])
        def answer(number, current, packet):
            plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
            unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
            requests = [dict(id='full', kind='unit', unit=unit['handle'])] if number == 0 else []
            excerpts = []
            for row, entry in zip(packet['session_turn']['plan']['now'], packet['evidence']):
                if row.get('kind') == 'fulltext':
                    route = self.case.read_records()['review_route'][current['route_id']]
                    remaining = state['allowances']['targets'][0]['characters'] - state['allowances']['targets'][0]['used']
                    unit_row = next(unit for unit in inventory['units'] if unit['handle'] == row['unit'])
                    source_charge = capacity.measure_capacity_value(route, {key: unit_row[key] for key in ('handle', 'kind', 'label')})
                    def charge(size):
                        excerpt = dict(unit=row['unit'], text=entry['content'][:size], purpose='A retained field comparison.', targets=['t1'])
                        observation = dict(number=packet['session_turn']['turn'], assignment_id=current['id'], attempt_id='sizing',
                            deliveries=[dict(row, text=entry['content'])], output=dict(stage='request', notes='',
                            excerpts=excerpts + [excerpt], requests=[], settled_requests=[]))
                        projected = session.project_session_turn(inventory, observation)['excerpts'][-1]
                        return capacity.measure_capacity_value(route, projected) + source_charge
                    low, high = 0, min(6000, len(entry['content']))
                    budget = min(remaining, packet['session_turn']['allowances']['excerpts'])
                    while low < high:
                        size = (low + high + 1) // 2
                        if charge(size) <= budget:
                            low = size
                        else:
                            high = size - 1
                    if low:
                        excerpts.append(dict(unit=row['unit'], text=entry['content'][:low], purpose='A retained field comparison.', targets=['t1']))
            output = dict(schema_version=1, stage='request', notes='n' * min(notes_characters, packet['session_turn']['allowances']['notes']),
                          excerpts=excerpts, requests=requests, settled_requests=[])
            self.invoke_usage(current, output, output_tokens=64000, room=room)
        return self.start(targets=1, request_answer=answer)

    def test_single_target_at_allowance_condenses_under_frozen_final_limits_then_records(self):
        current = self.start_condensing_session()
        self.assertIn('session_combine', self.read_packet(current))
        plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
        self.assertLessEqual(state['allowances']['targets'][0]['characters'] - state['allowances']['targets'][0]['used'], 1)
        bound = self.read_packet(current)['session_combine']['capacity']
        self.assertNotIn('combine', plan['task'])
        self.assertEqual(bound['request_bound'], plan['task']['final']['request_bound']['text_characters'])
        self.assertLessEqual(bound['answer_bound'], self.read_packet(current)['session_combine']['block']['characters'] // 4)
        with patch.object(capacity, 'read_output_profile', side_effect=AssertionError('Live prior during authentication')):
            self.assertTrue(protocol.authenticate_attempt(self.case.read_records(), self.case.artifacts,
                self.case.read_records()['review_assignment'][current['history_assignment_id']],
                self.case.read_records()['review_attempt']['attempt-' + current['history_assignment_id']]))
        _, packets, record = self.finish_pages(current)
        self.assertTrue(record['closing']['condensing'])
        self.assertTrue(any('page' in row.get('session_final', {}) for row in packets))

    def test_astra_b10_spare_page_room_does_not_extend_exhausted_target_grants(self):
        from research_harness.errors import ResearchError

        self.add_sources([600000])
        def answer(number, current, packet):
            _, inventory, state = session.read_bound_session(self.case.artifacts, current)
            unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
            requests = [dict(id='initial-source', kind='unit', unit=unit['handle'])] if number == 0 else []
            remaining = state['allowances']['targets'][0]['characters'] - state['allowances']['targets'][0]['used']
            excerpts = [dict(unit=row['unit'], text=entry['content'][:6000], purpose='A retained comparison.', targets=['t1'])
                for row, entry in zip(packet['session_turn']['plan']['now'], packet['evidence'])
                if row.get('kind') == 'fulltext' and remaining >= 7000]
            self.respond(current, requests, excerpts)
        current = self.start(targets=9, request_answer=answer)
        _, inventory, state = session.read_bound_session(self.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        for index, targets in enumerate((['t1'], ['t1', 't2'])):
            request = dict(schema_version=1, stage='request', notes='Page notes.', excerpts=[], settled_requests=[],
                requests=[dict(id='extra-%d' % index, kind='passage', unit=unit['handle'], anchor=None, characters=10000)])
            current = self.advance(current, request)
            packet = self.read_packet(current)
            self.assertTrue(packet['evidence'])
            _, _, before = session.read_bound_session(self.case.artifacts, current)
            output = dict(schema_version=1, stage='request', notes='Page notes.', settled_requests=[], requests=[],
                excerpts=[dict(unit=unit['handle'], text=packet['evidence'][0]['content'][:6000], purpose='The page comparison.', targets=targets)])
            with self.assertRaises(ResearchError) as caught:
                session.validate_final_turn_output(self.case.read_records(), self.case.artifacts, current, packet, output)
            self.assertEqual(caught.exception.details, dict(path='/excerpts', items=1, bound=0))
            current = self.advance(current, dict(output, excerpts=[]))
            _, _, after = session.read_bound_session(self.case.artifacts, current)
            self.assertEqual(after['excerpts'], before['excerpts'])
            self.assertEqual(after['allowances']['targets'], before['allowances']['targets'])
        self.finish_pages(current)

    def test_astra_b9_pending_material_capacity_halves_text_without_splitting_targets(self):
        current = self.start()
        current = self.advance(current, dict(stage='material', title='Argument', part=1, text='First.', more=True))
        binding = self.read_packet(current)['session_final']
        replacement = self.replace_capacity(current)
        after = self.read_packet(replacement)['session_final']
        self.assertEqual(after['page'], binding['page'])
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, current)
        self.assertEqual(after['capacity']['answer_bound'], max(plan['task']['final']['smallest_answer_bound'],
            binding['capacity']['answer_bound'] // 2))
        current = self.advance(replacement, dict(stage='material', title='Argument', part=2, text='Last.', more=False))
        self.finish_pages(current)

    def test_astra_r2_split_seven_then_level_keeps_four_and_three_and_records(self):
        with self.install_final_profile(thinking=100):
            current = self.start(targets=7)
        current = self.replace_capacity(current)
        reference = current['session_chain']['final_page_plan']
        page_plan = protocol._read_json(self.case.artifacts, reference['artifact'])
        self.assertEqual([len(row['targets']) for row in page_plan['turns'] if row['kind'] == 'page'], [4, 3])
        self.assertEqual(page_plan['cause']['kind'], 'level')
        self.finish_pages(current)

    def test_astra_r3_split_seven_four_three_then_one_keeps_pending_requests_arguments_and_digests(self):
        self.add_sources([3000])
        with self.install_final_profile(99000, [(480000, 109000)]):
            current = self.start(targets=14)
        initial = self.read_packet(current)['session_final']['page']['targets']
        self.assertEqual(len(initial), 7)
        plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        request = dict(schema_version=1, stage='request', notes='Current notes.', excerpts=[], settled_requests=[],
                       requests=[dict(id='pending-source', kind='unit', unit=unit['handle'])])
        self.invoke_usage(current, request)
        current = self.case.assign('bar', 'first', identifier='pending-seven', prior_assignment_id=current['id'])
        self.assertTrue(self.read_packet(current)['evidence'])
        earlier = {key: digest(row) for key, row in self.case.read_records()['review_assignment'].items()}
        current = self.replace_capacity(current)
        reference = current['session_chain']['final_page_plan']
        ancestors = [protocol._read_json(self.case.artifacts, row['artifact']) for row in reference['predecessors']]
        divided = next(row for row in ancestors if row.get('cause', {}).get('kind') == 'split')
        groups = [row['targets'] for row in divided['turns'] if row['kind'] == 'page' and row.get('split_of') is not None]
        self.assertEqual([len(row) for row in groups], [4, 3])
        self.assertLessEqual(len(self.read_packet(current)['session_final']['page']['targets']), 1)
        self.assertTrue(self.read_packet(current)['evidence'])
        self.steady_output_tokens = 64000
        current = self.advance(current, dict(stage='material', title='Split argument', part=1, text='First.', more=True))
        self.assertEqual(self.read_packet(current)['session_final']['material_request']['part'], 2)
        current = self.advance(current, dict(stage='material', title='Split argument', part=2, text='Last.', more=False))
        final, packets, record = self.finish_pages(current)
        for packet in packets:
            page = packet.get('session_final', {}).get('page')
            if page:
                self.assertLessEqual(len(page['targets']), 1)
                if set(page['targets']) <= set(initial):
                    self.assertTrue(any(set(page['targets']) <= set(group) for group in groups))
        self.assertEqual(len([target for row in record['pages'] for target in row['targets']]), 14)
        records = self.case.read_records()
        for key, expected in earlier.items():
            self.assertEqual(digest(records['review_assignment'][key]), expected)

    def test_incident_opus_max_five_sources_records_bar_and_reports_final_turns(self):
        from research_harness import review_claude_transport as claude
        from test_research_claude_transport import clean_stream, completed
        from v7_capacity_fixtures import build_route
        self.add_sources([30000, 45000, 60000, 75000, 83539])
        route = build_route('claude_cli_v1', model='claude-opus-5-5', effort='max')
        route['id'] = 'opus'
        answer = [None]
        def respond(selected, request, credential):
            from research_harness.review_excerpts import read_sent_packet
            packet = read_sent_packet(request)
            output = (dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[])
                      if 'allowed_control' in packet else answer[0])
            events = clean_stream(request, output=json.dumps(output), model=route['model'])
            events[-1]['usage']['output_tokens'] = 64000 if 'allowed_control' not in packet else 900
            with patch.object(claude, '_run_owned', return_value=completed(events)):
                return claude.send_request(selected, request, credential)
        with patch.object(claude, '_cli', return_value=('/fixture/claude', 'fixture-version')), patch.object(
                protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.record_route, route)
            self.case.mutate(protocol.probe_route, dict(id='opus-probe', route_id='opus'), credential='test-only')
            current = self.case.mutate(protocol.record_assignment, dict(id='opus-first', route_id='opus', role='bar',
                dossier_id=None, reviewer_id='first', author_id='author', context=self.case.deliver_supporting('first')))['result']
            final_forms = []
            for number in range(100):
                packet = self.read_packet(current)
                plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
                binding = session.read_session_binding(packet)
                request = protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, current)
                self.assertLessEqual(capacity.measure_request(route, request)['text_characters'], binding['capacity']['request_bound'])
                if 'session_turn' in packet:
                    requests = [dict(id='source-' + row['handle'], kind='unit', unit=row['handle'])
                                for row in inventory['units'] if row['kind'] == 'fulltext' or row['kind'] == 'abstract'
                                and row['artifact']['sha256'] == self.case.supporting['sha256']] if number == 0 else []
                    excerpts = []
                    for row, entry in zip(binding['plan']['now'], packet['evidence']):
                        if row.get('kind') == 'fulltext':
                            excerpts.append(dict(unit=row['unit'], text=entry['content'][:6000], purpose='Compare the field standard.', targets=['t1']))
                    answer[0] = dict(schema_version=1, stage='request', notes='n' * binding['allowances']['notes'],
                        excerpts=excerpts, requests=requests, settled_requests=[])
                else:
                    final_forms.append(session.find_session_form(packet))
                    answer[0] = self.case.build_review_output('bar')
                    if 'page_plan_digest' in binding:
                        answer[0] = self.answer(current)
                self.case.mutate(protocol.invoke_assignment, dict(id='opus-attempt-%d' % number, assignment_id=current['id']), credential='test-only')
                if 'session_final' in packet and 'page_plan_digest' not in binding or 'closing' in binding:
                    output, record = composite.read_final_output(self.case.read_records(), self.case.artifacts, current)
                    self.case.mutate(decisions.record_value_review, dict(output, id='opus-recorded', dossier_id=None, assignment_id=current['id']))
                    from research_harness.openness_final_pages import measure_page_room
                    limits = session.derive_session_task_limits(self.case.read_records(), self.case.artifacts, current, 'final')
                    room = measure_page_room(self.case.read_records(), self.case.artifacts, current, plan, limits)
                    print('Opus max: page_room=%d final_turns=%s allocation_forecast=%s' %
                        (room, final_forms, capacity.count_capacity_final_turns(room, plan['allocation']['targets'],
                         plan['allocation']['notes'], limits['target_allowance'])))
                    break
                current = self.case.mutate(protocol.record_assignment, dict(id='opus-next-%d' % number, route_id='opus', role='bar',
                    dossier_id=None, reviewer_id='first', author_id='author', context=dict(prior_assignment_id=current['id'])))['result']
            else:
                self.fail('The Opus fixture did not reach its bar')
        self.assertEqual(len(self.case.read_records()['value_review']), 1)

    def test_single_request_v4_packet_prompt_and_plan_bytes_match_base(self):
        current = self.case.assign('bar', 'first', identifier='single')
        self.assertEqual(digest(self.read_packet(current)), '529f1ce6c61b64972e327f03033a645a6f59d6d3e05e93ee69693433574bd68a')
        self.assertEqual(digest(current['prompt']), '3affb99c835a3c2df5cc6a607b1a66beecb76051488a57b31b5e10304c3b39ca')
        self.assertEqual(current['capacity_plan']['digest'], 'cf0caa6c49beedcfe34174edb04a27c61afbef2318449e4a178bed2fabd58700')
        self.respond(current)
        final = self.case.assign('bar', 'first', identifier='single-final', prior_assignment_id=current['id'])
        self.assertEqual(digest(self.read_packet(final)), 'e00daff39154c00f0255a932bc020c8ea790e6d30cd9956d25def9817c7ef00b')
        self.assertEqual(digest(final['prompt']), '974a172a844426e617e902091f5f4e5eac4070d2c97e16648323e7ca0bc758d5')

    test_v2_and_v3_running_sessions_keep_exact_bytes = turn_fixtures.SessionTurnsTests.test_running_v2_and_v3_continuations_keep_exact_check_bytes

    def test_terminal_page_accounts_for_delivered_source_and_composite_part_citation(self):
        self.add_sources([3000])
        current = self.start()
        plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading
        version = unit['identity']['version_id']
        capture = self.case.read_records()['work'][version]['fulltexts'][0]
        bundle = self.case.bundle(version, capture=capture, bundle_id='terminal-bundle')
        self.case.mutate(import_bundle, bundle)
        self.case.mutate(record_reading, self.case.full_note(bundle, note_id='terminal-reading'))
        request = dict(schema_version=1, stage='request', notes='Page notes.', excerpts=[], settled_requests=[],
                       requests=[dict(id='terminal-source', kind='unit', unit=unit['handle'])])
        current = self.advance(current, request)
        delivered = self.read_packet(current)['session_final']['plan']['now'][0]
        output = self.answer(current)
        for path in list(output['targets'][0]['fields']):
            if path.endswith('/evidence'):
                output['targets'][0]['fields'][path] = [dict(ref=unit['handle'], part=delivered['part'], start=0, end=20)]
        current = self.advance(current, output)
        _, _, state = session.read_bound_session(self.case.artifacts, current)
        self.assertEqual(state['pending_requests'], [])
        self.assertTrue(any(row['unit'] == unit['handle'] and row['part'] == delivered['part'] for row in state['deliveries']))
        self.finish_pages(current)

    def test_condensing_capacity_halves_block_then_records(self):
        current = self.start_condensing_session()
        while len(self.read_packet(current)['session_combine']['block']['entries']) < 2:
            current = self.advance(current)
        before = self.read_packet(current)['session_combine']['block']['characters']
        current = self.replace_capacity(current)
        self.assertLess(self.read_packet(current)['session_combine']['block']['characters'], before)
        self.finish_pages(current)

    def test_refused_page_replaced_at_same_level_with_notice_then_records(self):
        from research_harness.review_failure_class import plan_after_failure
        current = self.start()
        original = self.read_packet(current)
        output = self.answer(current)
        output['targets'][0]['sentence'] = 'x' * 301
        invoke_codex(self.case, current['id'], output)
        step = plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace', step)
        current = self.case.mutate(protocol.record_assignment, step['payload'])['result']
        self.assertEqual(self.read_packet(current)['session_final'], original['session_final'])
        self.assertIn('output_refusal', self.read_packet(current))
        self.finish_pages(current)

    def test_refused_condensing_replaced_at_same_level_with_notice_then_records(self):
        from research_harness.review_failure_class import plan_after_failure
        current = self.start_condensing_session()
        original = self.read_packet(current)
        output = self.answer(current)
        output['summary'] = 'x' * 20000
        invoke_codex(self.case, current['id'], output)
        step = plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace', step)
        current = self.case.mutate(protocol.record_assignment, step['payload'])['result']
        self.assertEqual(self.read_packet(current)['session_combine'], original['session_combine'])
        self.assertIn('output_refusal', self.read_packet(current))
        self.finish_pages(current)

    def test_composed_refusal_belongs_to_closing_and_correction_records(self):
        from research_harness.review_failure_class import plan_after_failure
        current = self.start()
        while 'closing' not in self.read_packet(current).get('session_final', {}):
            current = self.advance(current)
        output = self.answer(current)
        # The closing shape permits a subset of harness bindings; composing
        # without the role binding fails the complete assessment's contract.
        output['bindings'].remove('/stage')
        invoke_codex(self.case, current['id'], output)
        refusal = self.case.read_records()['review_output_refusal']['attempt-' + current['id']]
        self.assertEqual(refusal['assignment_id'], current['id'])
        self.assertTrue(refusal['details']['composed_review'])
        step = plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace', step)
        current = self.case.mutate(protocol.record_assignment, step['payload'])['result']
        self.assertIn('output_refusal', self.read_packet(current))
        self.finish_pages(current)

    def test_closing_capacity_condenses_decision_rows_then_records(self):
        current = self.start()
        while 'closing' not in self.read_packet(current).get('session_final', {}):
            current = self.advance(current)
        current = self.replace_capacity(current)
        self.assertIn('session_combine', self.read_packet(current))
        self.finish_pages(current)

    def test_closing_after_a_capped_one_request_final_counts_only_its_own_cap(self):
        """Seven targets: a final in one request that splits into pages whose closing condenses after its cap."""
        from research_harness import review_failure_class as failures
        current = self.start(targets=7)
        self.assertNotIn('page', self.read_packet(current)['session_final'])
        current = self.replace_capacity(current)
        while 'closing' not in self.read_packet(current).get('session_final', {}):
            current = self.advance(current)
        closing = current
        records = self.case.read_records()
        self.assertEqual(failures.count_position(records, self.case.artifacts, closing)['failed_attempts'], 0)
        current = self.replace_capacity(current)
        while 'session_combine' in self.read_packet(current):
            current = self.advance(current)
        self.assertIn('closing', self.read_packet(current)['session_final'])
        records = self.case.read_records()
        self.assertEqual(failures.read_paged_position(self.case.artifacts, closing),
                         failures.read_paged_position(self.case.artifacts, current))
        self.assertEqual(failures.count_position(records, self.case.artifacts, current)['failed_attempts'], 1)

    def test_astra_b8_single_block_above_material_floor_condenses_then_records(self):
        from research_harness import review_condensing
        # A pinned grant can outlive the policy that created it. Restore the
        # current policy before capacity replacement and fresh authentication.
        with patch.object(capacity, 'TARGET_MAXIMUM_CHARACTERS', 120000), self.install_final_profile(thinking=100):
            current = self.start_condensing_session(characters=1200000, room=None)
        packet = self.read_packet(current)
        self.assertNotIn('page', packet['session_final'])
        plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
        route = self.case.read_records()['review_route']['route']
        limits = session.derive_session_task_limits(self.case.read_records(), self.case.artifacts, current, 'final')
        logical = protocol._read_json(self.case.artifacts, current['session_chain']['logical'])
        inputs, _, _ = session.build_final_inputs(self.case.artifacts, route, logical, 'start', 1,
            inventory, state, plan, limits, limits['material_allowance'])
        entries = inputs['targets'][0]['entries'] + inputs['targets'][0]['rows']
        self.assertEqual(len(review_condensing.cut_condensing_blocks(entries, limits['material_allowance'])), 1)
        self.assertGreater(sum(row['characters'] for row in entries), plan['task']['final']['levels'][-1]['material_allowance'])
        current = self.replace_capacity(current)
        self.assertIn('session_combine', self.read_packet(current))
        self.finish_pages(current)

    def test_page_allowances_remain_explicit_when_session_summary_omits_unused_targets(self):
        current = self.start(targets=200)
        packet = self.read_packet(current)
        plan, inventory, state = session.read_bound_session(self.case.artifacts, current)
        # A compressed global summary must not erase the page's actual grants.
        original = session.describe_session_allowances
        def summarized(*args, **kwargs):
            return dict(original(*args, **kwargs), targets=[], omitted=200, target_count=200)
        logical = protocol._read_json(self.case.artifacts, current['session_chain']['logical'])
        route = self.case.read_records()['review_route']['route']
        limits = session.derive_session_task_limits(self.case.read_records(), self.case.artifacts, current, 'final')
        page_plan = protocol._read_json(self.case.artifacts, current['session_chain']['final_page_plan']['artifact'])
        turn = page_plan['turns'][0]
        _, catalog, full = session.build_final_inputs(self.case.artifacts, route, logical, 'start', 2,
                                                     inventory, state, plan, limits, turn['context']['page_room'])
        with patch.object(session, 'describe_session_allowances', side_effect=summarized):
            rebuilt = session.build_paged_packet(self.case.artifacts, route, logical, 'start', 2,
                inventory, state, plan, limits, page_plan, turn, catalog, full)
        rows = rebuilt['session_final']['request_allowances']['targets']
        self.assertEqual([row['handle'] for row in rows], packet['session_final']['page']['targets'])
        self.assertTrue(all(row['remaining'] == row['characters'] for row in rows))

    def test_partial_requested_source_keeps_page_pending_until_delivery_completes(self):
        self.add_sources([150000])
        current = self.start()
        _, inventory, _ = session.read_bound_session(self.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        request = dict(schema_version=1, stage='request', notes='Page notes.', excerpts=[], settled_requests=[],
                       requests=[dict(id='long-source', kind='unit', unit=unit['handle'])])
        current = self.advance(current, request)
        from research_harness.errors import ResearchError
        with self.assertRaises(ResearchError):
            session.validate_final_turn_output(self.case.read_records(), self.case.artifacts, current,
                                               self.read_packet(current), self.answer(current))
        for number in range(20):
            _, _, state = session.read_bound_session(self.case.artifacts, current)
            delivered = self.read_packet(current)['session_final']['plan']['now']
            if all(any(row.get('request_id') == request['id'] and row.get('request_complete') for row in delivered)
                   for request in state['pending_requests']):
                break
            current = self.advance(current, dict(schema_version=1, stage='request', notes='Page notes.', excerpts=[], requests=[], settled_requests=[]))
        else:
            self.fail('The source request did not complete on its page')
        self.finish_pages(current)

    def test_valid_page_answer_at_output_limit_is_capacity_event_and_cannot_advance(self):
        current = self.start()
        output = self.answer(current)
        def respond(route, request, credential):
            response = build_codex_response(route, request, output)
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.invoke_assignment, dict(id='valid-at-limit', assignment_id=current['id']), credential='test-only')
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertFalse(state['ready'])
        self.assertIn('review_output_over_cap', [row['code'] for row in state['obligations']])
        from research_harness.review_failure_class import plan_after_failure
        step = plan_after_failure(self.case.read_records(), self.case.artifacts, current['id'])
        self.assertEqual(step['state'], 'replace', step)
        current = self.case.mutate(protocol.record_assignment, step['details']['recovery']['payload'])['result']
        self.finish_pages(current)

    def test_condensed_block_lists_and_delivers_original_entry_without_changing_currency(self):
        self.limit_observed_input(3435400)
        self.fill_combinations = True
        with patch.object(capacity, 'TARGET_MAXIMUM_CHARACTERS', 120000), self.install_final_profile(thinking=100):
            # A block of blocks needs the page room of 13,875 characters that this fixture had before the frame
            # reserved the base prompt; at the ratio 0.02 of 3,435,400 input tokens that room is one step.
            current = self.start_condensing_session(characters=1200000, room=13875)
        def resume_page(current):
            while 'session_combine' in self.read_packet(current):
                current = self.advance(current)
            return current
        current = resume_page(current)
        _, _, state = session.read_bound_session(self.case.artifacts, current)
        self.assertTrue(any(any(row['handle'] in state['final_blocks'] for row in
            protocol._read_json(self.case.artifacts, reference)['entries']) for reference in state['final_blocks'].values()))
        block = next(handle for handle, reference in state['final_blocks'].items()
            if any(row['handle'].startswith('e') for row in protocol._read_json(self.case.artifacts, reference)['entries']))
        currency = current['session_chain']['inventory'], current['session_chain']['logical']
        targets = self.read_packet(current)['session_final']['page']['targets']
        current = resume_page(self.advance(current, dict(schema_version=1, stage='request', notes='n', excerpts=[], settled_requests=[],
            requests=[dict(id='block-list', kind='list_page', list=block, after=None)])))
        packet = self.read_packet(current)
        self.assertTrue(packet['evidence'])
        listed = json.loads(packet['evidence'][0]['content'])
        entry = next(row['handle'] for row in listed['rows'] if row['handle'].startswith('e'))
        current = resume_page(self.advance(current, dict(schema_version=1, stage='request', notes='n', excerpts=[], settled_requests=[],
            requests=[dict(id='original-entry', kind='unit', unit=entry)])))
        chunks = []
        for number in range(30):
            packet = self.read_packet(current)
            rows = packet['session_final'].get('plan', {}).get('now', [])
            pieces = [(row, evidence) for row, evidence in zip(rows, packet['evidence']) if row.get('unit') == entry]
            self.assertTrue(pieces)
            chunks.extend(evidence['content'] for row, evidence in pieces)
            if any(row.get('request_complete') for row, evidence in pieces):
                break
            current = resume_page(self.advance(current, dict(schema_version=1, stage='request', notes='n', excerpts=[], requests=[], settled_requests=[])))
        else:
            self.fail('The archived entry did not complete')
        value = json.loads(''.join(chunks))
        self.assertEqual(value['handle'], entry)
        self.assertTrue(value['text'])
        self.assertEqual((current['session_chain']['inventory'], current['session_chain']['logical']), currency)
        self.assertEqual(packet['session_final']['page']['targets'], targets)
        self.finish_pages(current)

    def test_capacity_replacement_bounds_a_maximum_length_assignment_id(self):
        current = self.start(continuation_prefix='s' * 119)
        self.assertEqual(len(current['id']), 120)
        replacement = self.replace_capacity(current)
        self.assertLessEqual(len(replacement['id']), 120)
        self.finish_pages(replacement)

    def test_page_request_keeps_latest_notes_after_short_initial_notes(self):
        def answer(number, current, packet):
            self.respond(current, notes='n')
        current = self.start(request_answer=answer)
        notes = 'Updated notes for this pending page.'
        current = self.advance(current, dict(schema_version=1, stage='request', notes=notes, excerpts=[], settled_requests=[],
            requests=[dict(id='inventory-notes', kind='inventory_page', page=0)]))
        self.assertEqual(self.read_packet(current)['session_final']['notes'], notes)
        self.finish_pages(current)

    def test_page_request_refreshes_notes_and_pool_after_notes_condensation(self):
        self.limit_observed_input(1000000)
        current = self.start_condensing_session(notes_characters=20000)
        while 'session_combine' in self.read_packet(current):
            current = self.advance(current)
        self.assertIsNone(self.read_packet(current)['session_final']['notes'])
        notes = 'Fresh notes replace the earlier condensed notes.'
        current = self.advance(current, dict(schema_version=1, stage='request', notes=notes, excerpts=[], settled_requests=[],
            requests=[dict(id='condensed-notes', kind='inventory_page', page=0)]))
        self.assertEqual(self.read_packet(current)['session_final']['notes'], notes)
        self.assertFalse(any(row.get('handle', '').startswith('notes') for row in self.read_packet(current)['session_final']['entries']))
        self.finish_pages(current)

    def test_session_target_handles_are_unambiguous_when_a_scientific_id_is_t1(self):
        packet = dict(dossier=dict(work_items=['t1']))
        rule = composition.find_rule('result', packet)
        targets = composition.list_targets(rule, packet)
        packet['session_targets'] = [dict(row, handle='t%d' % number) for number, row in enumerate(targets, 1)]
        targets = composition.list_targets(rule, packet)
        self.assertNotEqual(targets[0]['id'], 't1')
        self.assertEqual(composition._resolve_target_id(targets, 't1'), targets[0]['id'])
        self.assertEqual(composition._resolve_target_id(targets, dict(id='t1')), 't1')

    def test_closing_request_survives_ratio_repacking_and_records(self):
        current = self.start()
        while 'closing' not in self.read_packet(current).get('session_final', {}):
            current = self.advance(current)
        currency = current['session_chain']['inventory'], current['session_chain']['logical']
        previous = current['session_chain']['final_page_plan']['digest']
        request = dict(schema_version=1, stage='request', notes='Closing evidence check.', excerpts=[],
            settled_requests=[], requests=[dict(id='closing-inventory', kind='inventory_page', page=0)])
        self.invoke_usage(current, request, output_tokens=64000, room=30000)
        current = self.case.assign('bar', 'first', identifier='closing-request', prior_assignment_id=current['id'])
        successor = protocol._read_json(self.case.artifacts, current['session_chain']['final_page_plan']['artifact'])
        self.assertEqual(successor['replaces'], previous)
        self.assertEqual(successor.get('cause', {}).get('kind'), 'ratio', successor['turns'][-1]['context'])
        self.assertEqual(successor['cause']['attempt_id'], 'attempt-' + current['history_assignment_id'])
        packet = self.read_packet(current)
        self.assertIn('closing', packet['session_final'])
        self.assertEqual(packet['session_final']['plan']['now'][0]['request_id'], 'closing-inventory')
        self.assertEqual((current['session_chain']['inventory'], current['session_chain']['logical']), currency)
        self.finish_pages(current)

    def test_high_level_page_delivers_more_than_request_floor_and_records_with_closed_schema(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        self.add_sources([200000])
        with self.install_final_profile(thinking=100):
            current = self.start(targets=200)
        self.assertIn('page', self.read_packet(current)['session_final'])
        _, inventory, _ = session.read_bound_session(self.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        current = self.advance(current, dict(schema_version=1, stage='request', notes='Read the original text.',
            excerpts=[], settled_requests=[], requests=[dict(id='large-page-source', kind='unit', unit=unit['handle'])]))
        packet = self.read_packet(current)
        self.assertGreater(sum(row['characters'] for row in packet['session_final']['plan']['now']), 120000)
        self.assertEqual(validate_packet('bar', packet, read_packet_registry()), [])
        self.finish_pages(current)
