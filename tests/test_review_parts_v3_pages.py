"""Paged parts series reach the ordinary review commands through one composite."""
import copy
import json
import unittest
from unittest.mock import patch

from research_harness import review_parts as legacy, review_parts_v3 as parts
from research_harness import review_composition as composition, review_composite as composite
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import research_decisions as decisions
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from review_parts_fixtures import PartsResultCase
from test_review_parts_v3 import make_turn_output


class PagedSeriesTests(PartsResultCase):
    output_tokens = 128000

    def setUp(self):
        super().setUp()
        selected = patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, legacy.PROTOCOL))
        selected.start()
        self.addCleanup(selected.stop)
        self.turns = []
        from test_review_output_profile import build_row
        priors = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('single', 'read', 'combine', 'check')])
        priors.start()
        self.addCleanup(priors.stop)

    def start(self, objections=40, characters=400000, check=False):
        self.oversized(count=2, characters=characters, limit=1300000)
        self.objections = [dict(id='objection-%d' % index, claim='Assess material concern %d.' % index,
            version_evidence=[dict(id='evidence-%d' % index, reason='A recorded material concern.')])
            for index in range(objections)]
        def add(records, artifacts, packet, bars):
            packet['material_objections'] = copy.deepcopy(self.objections)
        amended = patch.object(protocol, 'add_problem_objections', side_effect=add)
        amended.start()
        self.addCleanup(amended.stop)
        if check:
            from research_harness.check_mode import check_mutation
            payload = dict(id='checked-series', route_id='route', dossier_id=None, role='result',
                           reviewer_id='first', author_id='author', context={})
            return check_mutation(self.case.store, protocol.record_assignment, payload,
                expected_revision=self.case.store.revision, request_id='pages-check', bounded_payload=payload)
        return self.assign()

    def answer(self, assignment):
        packet = self.packet(assignment)
        header = packet[legacy.KEY]
        if header.get('final_form') == 'combine':
            return dict(block=header['block']['handle'], summary='The entries were assessed.',
                        refs=header['block']['entries'][:1])
        if header['kind'] != 'final':
            return make_turn_output(packet)
        output = self.final_output()
        output['value']['objection_findings'] = [dict(id=row['id'], status='resolved',
            reason='The supplied record resolves this concern.', evidence=output['value']['evidence'])
            for row in self.objections]
        if 'page_plan_digest' not in header:
            return output
        logical = dict(legacy.read_logical_packet(self.case.artifacts, header), review_parts=header)
        rule = composition.find_rule('result', logical)
        reference = parts._read_value(self.case.artifacts, header['final_page_plan'])
        page_plan = parts._read_value(self.case.artifacts, reference['artifact'])
        targets = [row['targets'] for row in page_plan['turns'] if row['kind'] in ('page', 'final')]
        pages, closing = composition.split_output(rule, logical, output, targets)
        return closing if header['final_form'] == 'closing' else next(
            row['output'] for row in pages if row['targets'] == header['page']['targets'])

    def advance(self, assignment, output=None):
        self.turns.append(assignment)
        receipt = self.invoke(assignment, self.answer(assignment) if output is None else output)
        self.assertNotIn('output_refusal', receipt, receipt)
        return self.assign('page-turn-%d' % len(self.turns), assignment['id'])

    def finish(self, assignment, record=True):
        for _ in range(160):
            packet = self.packet(assignment)
            if packet[legacy.KEY].get('final_form') == 'closing':
                receipt = self.invoke(assignment, self.answer(assignment))
                self.assertNotIn('output_refusal', receipt, receipt)
                records = self.case.read_records()
                output, final = composite.read_final_output(records, self.case.artifacts, assignment)
                self.assertIsNotNone(final)
                verified = parts.verify_series(records, self.case.artifacts, assignment['id'])
                self.assertTrue(verified['complete'], verified['issues'])
                if record:
                    saved = self.case.mutate(decisions.record_value_review, dict(output,
                        id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))['result']
                    self.assertEqual(saved['payload']['value'], output['value'])
                return assignment, final
            assignment = self.advance(assignment)
        self.fail('The finite final did not finish.')

    def reach_page(self, assignment):
        for _ in range(60):
            if self.packet(assignment)[legacy.KEY].get('final_form') == 'page':
                return assignment
            assignment = self.advance(assignment)
        self.fail('No page was assigned.')

    def test_a5_q7_two_hundred_material_objections_record_a_composite(self):
        from test_review_output_profile import build_row
        measured = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('single', 'read', 'combine', 'check', 'final')])
        measured.start()
        self.addCleanup(measured.stop)
        assignment, final = self.finish(self.start(200))
        self.assertGreater(len(final['pages']), 1)
        self.assertEqual(len({target for page in final['pages'] for target in page['targets']}), 218)
        for member in self.turns + [assignment]:
            attempt = next(row for row in self.case.read_records()['review_attempt'].values()
                           if row['assignment_id'] == member['id'])
            sizes = attempt['preflight']['plan_comparison']['sizes']['text_characters']
            self.assertLessEqual(sizes['value'], sizes['bound'])
        print('parts-v3 acceptance ' + json.dumps(dict(targets=218, pages=len(final['pages']),
            condensing_turns=len(final['closing']['condensing']), closing_pages=1), sort_keys=True))

    def test_forty_targets_reach_a_recorded_review(self):
        self.finish(self.start(22))

    def test_checked_contradiction_reaches_page_after_condensing_then_records(self):
        assignment = self.start(0)
        for _ in range(60):
            packet = self.packet(assignment)
            header = packet[legacy.KEY]
            if header['kind'] == 'combine' and header.get('mode') == 'scientific':
                break
            output = self.answer(assignment)
            if header['kind'] == 'read':
                card = output['cards'][0]
                card.update(summary='"' * 12000, depends_on='Read alongside the checked finding.',
                    statements=[dict(unit=card['group']['first']['unit'], text='x' * 2000,
                        why='Inspect the source.') for _ in range(8)],
                    bearing=[dict(target=header['targets'][0]['id'], relation='context', reason='Relevant.')])
                route = self.case.read_records()['review_route']['route']
                while capacity.measure_capacity_value(route, card) > 34000:
                    card['summary'] = card['summary'][:len(card['summary']) // 2]
                output['unborne'] = output['unborne'][1:]
            assignment = self.advance(assignment, output)
        else:
            self.fail('No scientific combination was assigned.')
        scientific = dict(units=['u1'], question='Does the evidence support the implication?',
            view='It appears supported.', read=[dict(unit='u1', start=0, end=1000)])
        combination_assignment = assignment
        assignment = self.advance(assignment, dict(kind='combine', turn=header['turn'],
            combinations=[scientific], rest='No other interactions.'))
        packet = self.packet(assignment)
        header = packet[legacy.KEY]
        inventory = parts._read_value(self.case.artifacts, header['inventory'])
        quotation = inventory['units'][0]['text'][:30]
        conclusion = 'The checked evidence contradicts the initial view.'
        finding = dict(combination=header['check_combinations'][0], effect='contradicts',
            finding=conclusion + 'A' * 54000,
            statements=[dict(unit='u1', text=quotation, why='The inspected evidence.')])
        check_assignment = assignment
        assignment = self.advance(assignment, dict(kind='check', turn=header['turn'], findings=[finding]))
        state = parts._read_value(self.case.artifacts, self.packet(assignment)[legacy.KEY]['state'])
        checked = state['findings'][0]
        summary = dict(effect='contradicts', finding=conclusion,
                       verified_statements=checked['carried']['verified_statements'])
        seen_condensing = False
        while self.packet(assignment)[legacy.KEY].get('final_form') != 'page':
            header = self.packet(assignment)[legacy.KEY]
            output = self.answer(assignment)
            if header.get('final_form') == 'combine':
                refs = [ref for ref in header['block']['entries'] if ref == checked['id'] or
                        ref.startswith(checked['id'] + '@')]
                if refs:
                    output.update(summary=json.dumps(summary), refs=refs[:1])
                    seen_condensing = True
            assignment = self.advance(assignment, output)
        self.assertTrue(seen_condensing)
        state = parts._read_value(self.case.artifacts, self.packet(assignment)[legacy.KEY]['state'])
        checked = state['findings'][0]
        self.assertEqual([{key: value for key, value in row.items() if key != 'at'}
            for row in checked['carried']['verified_statements']], finding['statements'])
        self.assertEqual(checked['carried']['verified_statements'][0]['at']['unit'], 'u1')
        records = self.case.read_records()
        original = {row['id']: digest(row) for row in records['review_attempt'].values()
                    if row['assignment_id'] in (combination_assignment['id'], check_assignment['id'])}
        delivered = self.packet(assignment)[legacy.STATE_KEY]
        self.assertTrue(any(row.get('summary') == json.dumps(summary)
                            for values in delivered.values() for row in values))
        assignment = self.advance(assignment, dict(kind='requests',
            unit_requests=[dict(unit=checked['id'], start=0, end=256)], source_requests=[]))
        self.assertTrue(any(conclusion in row['text']
                            for row in self.packet(assignment)['review_parts_requests']))
        output = self.answer(assignment)
        field = next(path for path in output['targets'][0]['fields'] if path.endswith('/evidence'))
        output['targets'][0]['fields'][field].append(dict(ref='u1'))
        self.finish(self.advance(assignment, output))
        self.assertEqual(original, {key: digest(self.case.read_records()['review_attempt'][key]) for key in original})

    def test_q7_five_thousand_targets_check_admits_and_forecasts_pages(self):
        revision = self.case.store.revision
        result = self.start(4982, check=True)
        self.assertGreater(result['capacity_forecast']['by_kind']['final']['pages'], 1)
        self.assertEqual(self.case.store.revision, revision + 2)  # start seeds its two search records.

    def invoke_usage(self, assignment, output=None, tokens=64000, outcome=None, attempt_id=None, input_tokens=None):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness import review_transport as transport
        def respond(route, request, credential):
            if outcome == 'input_over_limit':
                raise ResearchError('review_input_too_large', 'Synthetic provider input limit.')
            response = build_codex_response(route, request, output if output is not None else self.answer(assignment))
            response['usage']['output_tokens'] = tokens
            if input_tokens is not None:
                response['usage']['input_tokens'] = input_tokens
            if outcome == 'timeout':
                response['status'] = 'incomplete'
                response['execution'] = dict(timeout=True)
            return bind_codex_response(route, request, response)
        with patch.object(transport, 'send_request', side_effect=respond):
            return self.case.mutate(protocol.invoke_assignment, dict(id=attempt_id or 'usage-' + assignment['id'],
                assignment_id=assignment['id']), credential='test')

    def replace_capacity(self, assignment, outcome='output_over_cap'):
        result = capacity.plan_capacity_recovery(self.case.read_records(), self.case.artifacts, assignment, outcome)
        self.assertEqual(result['kind'], 'replace', result)
        return self.case.mutate(protocol.record_assignment, result['payload'])['result']

    def pin_final(self, allowance, raised_allowance=None):
        original = capacity.plan_chain_capacity
        def build(*args, **kwargs):
            plan = original(*args, **kwargs)
            plan['task']['final']['target_allowance'] = allowance
            for level in plan['task']['final']['levels']:
                level['target_allowance'] = (raised_allowance if raised_allowance is not None and
                    level['material_allowance'] > plan['task']['final']['material_allowance'] else allowance)
            return plan
        patcher = patch.object(capacity, 'plan_chain_capacity', side_effect=build)
        patcher.start()
        self.addCleanup(patcher.stop)

    def freeze_records(self):
        return {kind: {key: digest(row) for key, row in rows.items()}
                for kind, rows in self.case.read_records().items()}

    def assert_frozen(self, frozen):
        records = self.case.read_records()
        for kind, rows in frozen.items():
            for key, checksum in rows.items():
                self.assertEqual(digest(records[kind][key]), checksum)

    def test_a14_page_at_output_limit_splits_then_records(self):
        first = self.reach_page(self.start(0))
        before = self.packet(first)[legacy.KEY]['page']['targets']
        self.invoke_usage(first, tokens=128000)
        frozen = self.freeze_records()
        replacement = self.replace_capacity(first)
        after = self.packet(replacement)[legacy.KEY]['page']['targets']
        self.assertEqual(after, before[:(len(before) + 1) // 2])
        self.finish(replacement)
        self.assert_frozen(frozen)

    def test_a6_refused_closing_is_replaced_with_notice_then_records(self):
        from research_harness.review_continuation import plan_next
        assignment = self.reach_page(self.start(0))
        while self.packet(assignment)[legacy.KEY].get('final_form') != 'closing':
            assignment = self.advance(assignment)
        refused = self.answer(assignment)
        refused['fields']['/value/status'] = 'invalid-status'
        receipt = self.invoke(assignment, refused)
        self.assertIn('output_refusal', receipt)
        frozen = self.freeze_records()
        action = plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertEqual(action['state'], 'replace', action)
        replacement = self.case.mutate(protocol.record_assignment, action['payload'])['result']
        self.assertIn('output_refusal', self.packet(replacement))
        self.finish(replacement)
        self.assert_frozen(frozen)

    def test_pending_multipart_argument_resumes_page_and_authenticates(self):
        assignment = self.reach_page(self.start(0))
        targets = self.packet(assignment)[legacy.KEY]['page']['targets']
        assignment = self.advance(assignment, dict(stage='material', title='Independent argument',
            part=1, text='First part of the finite argument.', more=True))
        self.assertEqual(self.packet(assignment)[legacy.KEY]['page']['targets'], targets)
        self.assertEqual(self.packet(assignment)['review_parts_material_request']['part'], 2)
        assignment = self.advance(assignment, dict(stage='material', title='Independent argument',
            part=2, text='Second part of the finite argument.', more=False))
        self.assertEqual(self.packet(assignment)[legacy.KEY]['page']['targets'], targets)
        output = self.answer(assignment)
        field = next(path for path in output['targets'][0]['fields'] if path.endswith('/evidence'))
        output['targets'][0]['fields'][field].append(dict(ref='m1'))
        assignment = self.advance(assignment, output)
        final, record = self.finish(assignment)
        self.assertEqual(len({key for page in record['pages'] for key in page['targets']}), 18)

    def test_currency_source_request_keeps_page_one_and_optional_source_unrequested(self):
        first_source, second_source = self.case.metadata(91), self.case.metadata(92)
        sentence = 'A captured theorem bounds every finite graph.'
        self.case.capture(first_source, pdf_text=sentence)
        self.case.capture(second_source, pdf_text='The second source stays optional.')
        first = self.reach_page(self.start(0))
        second = self.advance(first)
        currency = self.packet(first)[legacy.KEY]['inventory_digest']
        second = self.advance(second, dict(kind='requests', unit_requests=[], source_requests=[
            dict(id='requested-source', kind='source', version_id=first_source, depth='fulltext')]))
        packet = self.packet(second)
        answering = second
        self.assertEqual(packet[legacy.KEY]['inventory_digest'], currency)
        delivered = packet['review_parts_requests']
        self.assertEqual(''.join(row['text'] for row in delivered), sentence)
        inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
        optional = next(row for row in inventory['units'] if not row['required'] and
            row['source']['identity'].get('version_id') == second_source)
        output = self.answer(second)
        for item in output['targets']:
            for path in item['fields']:
                if path.endswith('/evidence'):
                    item['fields'][path].append(dict(ref=delivered[0]['unit'], part=1, start=0, end=len(sentence)))
        second = self.advance(second, output)
        final, record = self.finish(second)
        self.assertEqual(record['pages'][0]['assignment_id'], first['id'])
        verified = parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])
        self.assertNotIn(optional['handle'], verified['state']['delivered_parts'])
        records = self.case.read_records()
        required = next(row for row in self.turns if self.packet(row)[legacy.KEY]['kind'] == 'read')
        records['review_attempt'].pop('attempt-' + required['id'])
        self.assertFalse(parts.verify_series(records, self.case.artifacts, final['id'])['complete'])
        records = self.case.read_records()
        missing = copy.deepcopy(self.packet(answering))
        missing['review_parts_requests'] = []
        missing['digest'] = digest({key: value for key, value in missing.items() if key != 'digest'})
        records['review_assignment'][answering['id']].update(
            packet=parts._put_value(self.case.artifacts, missing), packet_digest=missing['digest'])
        self.assertFalse(parts.verify_series(records, self.case.artifacts, final['id'])['complete'])

    def test_currency_late_required_unit_reads_then_repeats_completed_page(self):
        first = self.reach_page(self.start(0))
        self.invoke(first, self.answer(first))
        frozen = self.freeze_records()
        search = dict(id='late-bound-search', queries=['late unit'], reason='A newly bound required record.')
        self.case.put('literature_search', search['id'], search)
        self.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        reading = self.assign('late-reading', first['id'])
        packet = self.packet(reading)
        self.assertEqual(packet[legacy.KEY]['kind'], 'read')
        self.assertEqual(packet[legacy.KEY]['final_forecast']['repeated_pages'], 1)
        forecast = capacity.describe_assignment_capacity(self.case.read_records(), self.case.artifacts, reading)['capacity_forecast']
        self.assertEqual(forecast['by_kind']['final']['repeated_pages'], 1)
        self.assertTrue(forecast['by_kind']['final']['forecast'])
        following = self.advance(reading)
        following = self.reach_page(following)
        reference = parts._read_value(self.case.artifacts, self.packet(following)[legacy.KEY]['final_page_plan'])
        page_plan = parts._read_value(self.case.artifacts, reference['artifact'])
        self.assertEqual(page_plan['cause']['kind'], 'inventory')
        self.assertTrue(set(self.packet(first)[legacy.KEY]['page']['targets']) <=
                        set(self.packet(following)[legacy.KEY]['page']['targets']))
        final, record = self.finish(following)
        self.assertNotIn(first['id'], [row['assignment_id'] for row in record['pages']])
        self.assert_frozen(frozen)
        mixed = copy.deepcopy(record)
        attempt, output = protocol.read_assignment_output(self.case.read_records(), self.case.artifacts, first['id'])
        mixed['pages'][0].update(assignment_id=first['id'], attempt_id=attempt['id'], output_digest=digest(output))
        with self.assertRaises(ResearchError):
            composite.authenticate_composite(self.case.read_records(), self.case.artifacts, mixed)

    def test_verify_refuses_changed_plan_foreign_target_and_changed_output(self):
        final, record = self.finish(self.start(0))
        for mutation in ('plan', 'target', 'output'):
            records = self.case.read_records()
            page = records['review_assignment'][record['pages'][0]['assignment_id']]
            packet = self.packet(page)
            if mutation == 'plan':
                reference = parts._read_value(self.case.artifacts, packet[legacy.KEY]['final_page_plan'])
                value = parts._read_value(self.case.artifacts, reference['artifact'])
                value['turns'][0]['targets'] = ['t18']
                reference.update(digest=digest(value), artifact=parts._put_value(self.case.artifacts, value))
                packet[legacy.KEY]['final_page_plan'] = parts._put_value(self.case.artifacts, reference)
                packet[legacy.KEY]['page_plan_digest'] = digest(value)
            elif mutation == 'target':
                packet[legacy.KEY]['page']['targets'] = ['t18']
            else:
                attempt = records['review_attempt'][record['pages'][0]['attempt_id']]
                value = parts._read_value(self.case.artifacts, attempt['output'])
                value['targets'][0]['sentence'] = 'Changed output.'
                attempt['output'] = parts._put_value(self.case.artifacts, value)
            if mutation != 'output':
                packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
                page.update(packet=parts._put_value(self.case.artifacts, packet), packet_digest=packet['digest'])
            self.assertFalse(parts.verify_series(records, self.case.artifacts, final['id'])['complete'], mutation)


    def test_reduced_allowance_keeps_split_groups_pending_requests_and_arguments(self):
        self.pin_final(7, raised_allowance=1)
        first = self.reach_page(self.start(0))
        original_targets = self.packet(first)[legacy.KEY]['page']['targets']
        self.assertEqual(len(original_targets), 7)
        self.invoke_usage(first, tokens=128000)
        split = self.replace_capacity(first)
        self.assertEqual(self.packet(split)[legacy.KEY]['page']['targets'], original_targets[:4])
        self.invoke_usage(split, dict(kind='requests', unit_requests=[dict(unit='u1', start=0, end=20)],
            source_requests=[]), tokens=64000)
        pending = self.assign('pending-split', split['id'])
        self.assertEqual(self.packet(pending)['review_parts_requests'][0]['end'], 20)
        self.invoke_usage(pending, dict(stage='material', title='Pending argument', part=1,
            text='The first part of the argument.', more=True), tokens=12)
        frozen = self.freeze_records()
        reduced = self.assign('reduced-pending', pending['id'])
        self.assertEqual(len(self.packet(reduced)[legacy.KEY]['page']['targets']), 1)
        self.assertEqual(self.packet(reduced)['review_parts_material_request']['part'], 2)
        before = self.packet(reduced)[legacy.KEY]
        self.invoke_usage(reduced, tokens=128000)
        replacement = self.replace_capacity(reduced)
        after = self.packet(replacement)[legacy.KEY]
        self.assertEqual(after['page_plan_digest'], before['page_plan_digest'])
        self.assertEqual(after['capacity']['answer_bound'], before['capacity']['answer_bound'] // 2)
        resumed = self.advance(replacement, dict(stage='material', title='Pending argument', part=2,
            text='The second part of the argument.', more=False))
        start = len(self.turns)
        final, record = self.finish(resumed)
        for assignment in self.turns[start:] + [final]:
            header = self.packet(assignment)[legacy.KEY]
            if 'page' in header:
                self.assertLessEqual(len(header['page']['targets']), 1)
                targets = set(header['page']['targets'])
                if targets & set(original_targets):
                    self.assertTrue(targets <= set(original_targets[:4]) or targets <= set(original_targets[4:]))
        self.assertEqual(len([target for row in record['pages'] for target in row['targets']]), 18)
        self.assert_frozen(frozen)

    def run_single_final_capacity_event(self, outcome):
        from test_review_output_profile import build_row
        # The normal final is one request at the measured start level.
        measured = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('read', 'combine', 'check', 'final', 'single')])
        measured.start()
        self.addCleanup(measured.stop)
        assignment = self.start(0)
        while self.packet(assignment)[legacy.KEY]['kind'] != 'final':
            assignment = self.advance(assignment)
        self.assertNotIn('page_plan_digest', self.packet(assignment)[legacy.KEY])
        self.invoke_usage(assignment, tokens=128000 if outcome == 'output_over_cap' else 64000, outcome=outcome)
        frozen = self.freeze_records()
        replacement = self.replace_capacity(assignment)
        self.assertIn('page', self.packet(replacement)[legacy.KEY])
        self.finish(replacement)
        self.assert_frozen(frozen)

    def test_single_final_output_limit_becomes_pages_and_records(self):
        self.run_single_final_capacity_event('output_over_cap')

    def test_single_final_input_limit_becomes_pages_and_records(self):
        self.run_single_final_capacity_event('input_over_limit')

    def test_single_final_timeout_becomes_pages_and_records(self):
        self.run_single_final_capacity_event('timeout')

    def test_every_level_at_limit_repeats_at_floor_then_decision_stop(self):
        from research_harness.review_continuation import plan_next
        assignment = self.reach_page(self.start(0))
        repeated = False
        for index in range(30):
            self.invoke_usage(assignment, tokens=128000, attempt_id='floor-cap-%d' % index)
            action = plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
            if action['state'] == 'replace':
                assignment = self.case.mutate(protocol.record_assignment, action['payload'])['result']
            elif action['state'] == 'run':
                repeated = True
            else:
                self.assertTrue(repeated)
                self.assertEqual((action['state'], action['code']), ('stop', 'review_output_over_cap'), action)
                return
        self.fail('The pinned floor did not reach its D2 decision stop.')


    def run_adaptation(self, starting, tokens, ratio=False):
        import math
        from test_review_output_profile import build_row
        profile = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('single', 'read', 'combine', 'check', 'final')])
        profile.start()
        self.addCleanup(profile.stop)
        original = capacity.plan_chain_capacity
        def build(*args, **kwargs):
            plan = original(*args, **kwargs)
            plan['task']['final']['material_allowance'] = starting
            plan['task']['final']['target_allowance'] = 7
            for row in plan['task']['final']['levels']:
                row['target_allowance'] = 7
            if ratio:
                plan['measure']['characters_per_token'] = 3.0
            return plan
        pinned = patch.object(capacity, 'plan_chain_capacity', side_effect=build)
        pinned.start()
        self.addCleanup(pinned.stop)
        first = self.reach_page(self.start(0))
        plan = capacity.read_chain_capacity_plan(self.case.artifacts, first)
        input_tokens = None
        if ratio:
            request = protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, first)
            characters = capacity.measure_request(self.case.read_records()['review_route']['route'], request)['text_characters']
            desired = (plan['frame']['final']['bound'] + plan['task']['final']['notice_characters'] + 240000) / plan['limits']['input_tokens']['value']
            input_tokens = math.ceil(characters * (1 - plan['measure']['ratio_margin']) / desired)
        self.invoke_usage(first, tokens=tokens, input_tokens=input_tokens)
        frozen = self.freeze_records()
        following = self.assign('adapted-page', first['id'])
        header = self.packet(following)[legacy.KEY]
        reference = parts._read_value(self.case.artifacts, header['final_page_plan'])
        value = parts._read_value(self.case.artifacts, reference['artifact'])
        self.assertEqual(value['cause']['kind'], 'ratio' if ratio else 'level')
        self.assertEqual(value['cause']['completed'], [1])
        self.assertLessEqual(header['capacity']['material_allowance'], value['cause']['page_room'])
        final, record = self.finish(following)
        self.assertEqual(record['pages'][0]['assignment_id'], first['id'])
        self.assert_frozen(frozen)

    def test_successful_page_above_seventy_five_percent_lowers_level_and_records(self):
        self.run_adaptation(480000, 100000)

    def test_successful_page_below_thirty_five_percent_raises_level_and_records(self):
        self.run_adaptation(120000, 12)

    def test_successful_page_denser_ratio_repacks_without_level_change_and_records(self):
        self.run_adaptation(480000, 64000, ratio=True)


    def test_composed_output_refusal_belongs_to_closing_with_notice(self):
        from research_harness.review_continuation import plan_next
        source = self.case.metadata(95)
        self.case.capture(source, pdf_text='An optional source was never requested.')
        assignment = self.reach_page(self.start(0))
        while self.packet(assignment)[legacy.KEY].get('final_form') != 'closing':
            assignment = self.advance(assignment)
        packet = self.packet(assignment)
        inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
        optional = next(row for row in inventory['units'] if not row['required'] and
                        row['source']['identity'].get('version_id') == source)
        output = self.answer(assignment)
        output['fields']['/value/evidence'] = [dict(ref=optional['handle'])]
        receipt = self.invoke(assignment, output)
        self.assertIn('output_refusal', receipt)
        self.assertTrue(receipt['output_refusal']['details']['composed_output'])
        self.assertFalse(self.case.read_records().get('review_composite'))
        frozen = self.freeze_records()
        action = plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertEqual(action['state'], 'replace', action)
        following = self.case.mutate(protocol.record_assignment, action['payload'])['result']
        notice = self.packet(following)['output_refusal']['composed_finding']
        self.assertEqual(notice['path'], '/value/evidence')
        final, record = self.finish(following)
        self.assertEqual(record['closing']['assignment_id'], final['id'])
        self.assert_frozen(frozen)

    def test_closing_capacity_condenses_rows_and_refused_block_keeps_its_level(self):
        from research_harness.review_continuation import plan_next
        assignment = self.reach_page(self.start(0))
        while self.packet(assignment)[legacy.KEY].get('final_form') != 'closing':
            assignment = self.advance(assignment)
        self.invoke_usage(assignment, tokens=128000)
        assignment = self.replace_capacity(assignment)
        header = self.packet(assignment)[legacy.KEY]
        self.assertEqual(header['final_form'], 'combine')
        bad = dict(block=header['block']['handle'], summary='Oversized.' * 1000, refs=header['block']['entries'][:1])
        receipt = self.invoke(assignment, bad)
        self.assertIn('output_refusal', receipt)
        frozen = self.freeze_records()
        action = plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertEqual(action['state'], 'replace', action)
        following = self.case.mutate(protocol.record_assignment, action['payload'])['result']
        self.assertEqual(self.packet(following)[legacy.KEY]['block'], header['block'])
        self.assertEqual(self.packet(following)[legacy.KEY]['capacity']['material_allowance'], header['capacity']['material_allowance'])
        final, record = self.finish(following)
        self.assertEqual(len(record['closing']['condensing']), 2)
        self.assert_frozen(frozen)


    def test_refused_page_keeps_level_notice_and_notice_survives_capacity_split(self):
        from research_harness.review_continuation import plan_next
        first = self.reach_page(self.start(0))
        header = self.packet(first)[legacy.KEY]
        output = self.answer(first)
        status = next(path for path in output['targets'][0]['fields'] if path.endswith('/status'))
        output['targets'][0]['fields'][status] = 'invalid-status'
        receipt = self.invoke(first, output)
        self.assertIn('output_refusal', receipt)
        action = plan_next(self.case.read_records(), self.case.artifacts, first['id'])
        following = self.case.mutate(protocol.record_assignment, action['payload'])['result']
        corrected = self.packet(following)
        self.assertEqual(corrected[legacy.KEY]['page'], header['page'])
        self.assertEqual(corrected[legacy.KEY]['capacity']['material_allowance'], header['capacity']['material_allowance'])
        self.invoke_usage(following, tokens=128000)
        frozen = self.freeze_records()
        smaller = self.replace_capacity(following)
        packet = self.packet(smaller)
        self.assertEqual(packet['output_refusal'], corrected['output_refusal'])
        self.assertLess(len(packet[legacy.KEY]['page']['targets']), len(header['page']['targets']))
        self.finish(smaller)
        self.assert_frozen(frozen)


class AdapterPageTests(unittest.TestCase):
    def pin_read_profile(self):
        from test_review_output_profile import build_row
        from research_harness import review_single_request
        profile = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('read', 'combine', 'check')])
        profile.start()
        self.addCleanup(profile.stop)
        entry = patch.object(review_single_request, 'describe_task_entry', return_value='task')
        entry.start()
        self.addCleanup(entry.stop)

    def setUp(self):
        from test_review_output_profile import build_row
        from research_harness import review_transport as transport
        for patcher in (
            patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, legacy.PROTOCOL)),
            patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000),
            patch.object(capacity, 'read_profile_priors', return_value=[
                dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                for kind in ('read', 'combine', 'check')])):
            patcher.start()
            self.addCleanup(patcher.stop)
        original = capacity.plan_chain_capacity
        def build(*args, **kwargs):
            plan = original(*args, **kwargs)
            plan['task']['final']['target_allowance'] = 2
            for row in plan['task']['final']['levels']:
                row['target_allowance'] = 2
            return plan
        pinned = patch.object(capacity, 'plan_chain_capacity', side_effect=build)
        pinned.start()
        self.addCleanup(pinned.stop)

    def drive(self, case, assignment, read_packet, full_output, assign, invoke, records, artifacts):
        for index in range(120):
            packet = read_packet(assignment)
            self.assertEqual(packet[legacy.KEY]['protocol'], parts.PROTOCOL)
            header = packet[legacy.KEY]
            if header.get('final_form') == 'combine':
                output = dict(block=header['block']['handle'], summary='Retained evidence.', refs=header['block']['entries'][:1])
            elif header['kind'] != 'final':
                output = make_turn_output(packet)
            else:
                native = dict(legacy.read_logical_packet(artifacts, header), review_parts=header)
                rule = composition.find_rule(assignment['role'], native)
                reference = parts._read_value(artifacts, header['final_page_plan'])
                plan = parts._read_value(artifacts, reference['artifact'])
                groups = [row['targets'] for row in plan['turns'] if row['kind'] in ('page', 'final')]
                pages, closing = composition.split_output(rule, native, full_output(assignment), groups)
                output = closing if header['final_form'] == 'closing' else next(
                    row['output'] for row in pages if row['targets'] == header['page']['targets'])
            receipt = invoke(assignment, output)
            self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
            if header.get('final_form') == 'closing':
                result, final = composite.read_final_output(records(), artifacts, assignment)
                self.assertIsNotNone(final)
                self.assertTrue(parts.verify_series(records(), artifacts, assignment['id'])['complete'])
                for row in records()['review_attempt'].values():
                    if row['assignment_id'] in {member['id'] for member, _ in parts.list_series(records(), artifacts, assignment['id'])}:
                        self.assertTrue(row['preflight']['plan_comparison']['fits'])
                return assignment, result, final
            assignment = assign('adapter-page-%d' % index, assignment['id'])
        self.fail('An adapter did not finish its finite page plan.')

    def test_verification_pages_bind_the_verdict(self):
        from test_review_parts_study import VerificationPartsTests
        from research_harness import verification, review_transport as transport
        case = VerificationPartsTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        self.pin_read_profile()
        def answer(assignment):
            output = case.final_output(assignment)
            for check in output['checks']:
                check['evidence'] = [case.case.linked]
            return output
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            final, output, record = self.drive(case, case.assign(), case.packet, answer,
                case.assign, case.invoke, case.records, case.case.artifacts)
        payload = case.case.build_bind_payload(case.task['digest'], output['verdict'], observed=False)
        payload['assessment'].update(assignment_id=final['id'], checks=output['checks'])
        saved = case.case.mutate(verification.bind_verdict, payload)['result']
        self.assertEqual(saved['assessment']['assignment_id'], final['id'])

    def test_readiness_pages_record_the_support_review(self):
        from test_review_parts_study import ReadinessPartsTests
        from research_harness import development, review_transport as transport
        case = ReadinessPartsTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        self.pin_read_profile()
        def answer(assignment):
            output = case.final_output(assignment)
            self.assertLessEqual(len(case.references), len(output['support']['checks']))
            for index, check in enumerate(output['support']['checks']):
                check['evidence'] = [case.references[index % len(case.references)]]
            return output
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            final, output, record = self.drive(case, case.assign(), case.packet,
                answer,
                case.assign, case.invoke, case.records, case.case.artifacts)
        case.case.mutate(development.record_readiness_review, output['support'])
        self.assertEqual(case.records()['readiness_review'][output['support']['id']]['payload'], output['support'])

    def test_adjudicator_pages_record_the_disposition(self):
        from test_review_parts_adjudication_journey import AdjudicationPartsCase
        from research_harness import review_transport as transport
        case = AdjudicationPartsCase()
        case.output_tokens = 128000
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized(count=2, paragraphs=800)
        self.pin_read_profile()
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            final, output, record = self.drive(case, case.assign(), case.packet,
                lambda assignment: case.final_output(), case.assign, case.invoke,
                case.case.read_records, case.case.artifacts)
        saved = case.case.mutate(protocol.record_adjudication,
            dict(output, id='paged-disposition', assignment_id=final['id']))['result']
        self.assertEqual(saved['composite']['id'], record['id'])

    def test_native_pages_authenticate_the_final_assignment(self):
        from test_review_parts_native import NativePartsTests
        from codex_route_fixtures import invoke_codex
        from research_harness.review_parts_native import find_final_assignment
        case = NativePartsTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        self.pin_read_profile()
        def output(assignment):
            return case.final_output([row['digest'] for row in case.native['evidence']])
        final, answer, record = self.drive(case, case.assign(), case.read_packet, output,
            case.assign, lambda assignment, answer: invoke_codex(case, assignment['id'], answer),
            lambda: case.store.snapshot()['records'], case.artifacts)
        self.assertEqual(find_final_assignment(case.store.snapshot()['records'], case.artifacts, 'first')['id'], final['id'])

    def test_context_repair_pages_record_the_reassessment(self):
        from test_review_parts_repair_journey import ResultRepairJourneyTests
        from research_harness import review_transport as transport
        case = ResultRepairJourneyTests()
        case.output_tokens = 128000
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized(count=1, characters=4000, limit=1300000)
        self.pin_read_profile()
        predecessor = case.assign()
        case.invoke(predecessor, make_turn_output(case.packet(predecessor)))
        binding = case.contaminate(predecessor)
        repaired = case.repair_assignment(predecessor, binding)
        def output(assignment):
            result = case.final_output()
            result['reassessment'] = dict(assignment_id=predecessor['id'], findings=[])
            return result
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            final, answer, record = self.drive(case, repaired, case.packet, output,
                lambda identifier, prior: case.continue_repair(case.case.read_records()['review_assignment'][prior], identifier),
                case.invoke, case.case.read_records, case.case.artifacts)
        saved = case.case.mutate(decisions.record_value_review, dict(answer,
            id='paged-repair', dossier_id=None, assignment_id=final['id']))['result']
        self.assertEqual(saved['composite']['id'], record['id'])


    def test_native_command_pages_record_an_acceptance_with_composite_binding(self):
        import importlib.util
        import sys
        from pathlib import Path
        import tests as package
        harness = Path(__file__).resolve().parents[1] / 'skills/math-solver/harness'
        sys.path.insert(0, str(harness))
        original_paths = package.__path__
        package.__path__ = list(original_paths) + [str(harness / 'tests')]
        self.addCleanup(lambda: sys.path.remove(str(harness)))
        self.addCleanup(setattr, package, '__path__', original_paths)
        spec = importlib.util.spec_from_file_location('parts_pages_native_command', harness / 'tests/test_review_parts_native.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        case = module.NativePartsAcceptanceTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        import tests.openness_chain_support as support
        original_route = support.record_codex_route
        with patch.object(support, 'record_codex_route', side_effect=lambda fixture: original_route(fixture, output_tokens=128000)):
            case.build_native_fixture()
        self.pin_read_profile()
        def answer(assignment):
            result = copy.deepcopy(case.review)
            result['schema_version'] = 2
            logical = legacy.read_logical_packet(case.artifacts, case.read_turn(assignment['id'])[legacy.KEY])
            result['findings']['openness'] = dict(outcome='unresolved', statement_digest=result['claim_digest'],
                searched_at='2026-11-16', coverage='The exported originals.', reason='The recorded finding stays unresolved.',
                evidence=[logical['native']['evidence'][0]['digest']])
            return result
        from research_harness import review_transport as transport
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            final, output, record = self.drive(case, case.assign_turn('turn-1'),
                lambda assignment: case.read_turn(assignment['id']), answer, case.assign_turn,
                lambda assignment, output: case.invoke(assignment['id'], output),
                lambda: case.store.snapshot()['records'], case.artifacts)
        case.review = output
        case.check_review()
        case.accept_result()
        acceptance = case.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        self.assertEqual(acceptance['composite']['id'], record['id'])
        case.controller.command('deliver-attempt', {}, case.controller.status()['revision'], 'deliver-paged-review')
        from research_harness.record_check import check_acceptance_reviews
        links = check_acceptance_reviews(case.store.snapshot()['records'], case.artifacts)
        self.assertEqual(next(iter(links.values()))['composite']['id'], record['id'])


class FinalPlanningTests(unittest.TestCase):
    def setUp(self):
        selected = patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, legacy.PROTOCOL))
        selected.start()
        self.addCleanup(selected.stop)
        import tempfile
        from pathlib import Path
        from research_harness.artifacts import ArtifactStore
        from test_review_parts_v3 import make_packet
        from test_review_route_capacity import build_route
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.route = build_route()
        self.logical = make_packet(1, 20)
        admitted = parts.admit_packet({}, self.artifacts, self.route, self.logical, 'problem_result')
        self.plan, self.inventory = admitted['plan'], admitted['inventory']
        self.packet = {legacy.KEY: dict(targets=admitted['targets'])}
        self.state = parts.build_series_state([])
        self.limits = capacity.derive_plan_task_limits(self.plan, 'final')

    def inputs(self):
        return parts.build_final_inputs(self.route, self.artifacts, self.packet, self.inventory,
            self.plan, self.state, self.limits, 60000)

    def add_cards(self, count, bearing):
        self.state['cards'] = [dict(id='c%d-1' % index, output=dict(summary='A' * 5000,
            bearing=[dict(target=target, relation='context', reason='Relevant.') for target in bearing]),
            group=dict(first=dict(unit='u1', offset=0), last=dict(unit='u1', offset=1))) for index in range(count)]
        self.state['entries'] = [row['id'] for row in self.state['cards']]

    def test_three_small_targets_at_allowance_two_take_two_pages_and_closing(self):
        from research_harness import review_final_pages as pages
        self.plan['targets']['handles'] = ['t1', 't2', 't3']
        self.packet[legacy.KEY]['targets'] = self.packet[legacy.KEY]['targets'][:3]
        self.limits['target_allowance'] = 2
        inputs, catalog = self.inputs()
        turns = pages.plan_final_turns(**inputs)
        self.assertEqual([row['kind'] for row in turns], ['page', 'page', 'closing'])
        self.assertEqual([row['targets'] for row in turns[:2]], [['t1', 't2'], ['t3']])

    def test_one_target_all_cards_condense_before_its_page(self):
        from research_harness import review_final_pages as pages
        self.plan['targets']['handles'] = ['t1']
        self.packet[legacy.KEY]['targets'] = self.packet[legacy.KEY]['targets'][:1]
        self.add_cards(30, [self.packet[legacy.KEY]['targets'][0]['id']])
        inputs, catalog = self.inputs()
        turns = pages.plan_final_turns(**inputs)
        self.assertEqual(turns[0]['kind'], 'combine')
        self.assertEqual([row['kind'] for row in turns][-2:], ['page', 'closing'])
        self.assertTrue(all(row['characters'] <= 60000 for row in turns))

    def test_card_bearing_on_two_targets_is_charged_to_each(self):
        ids = [row['id'] for row in self.packet[legacy.KEY]['targets'][:2]]
        self.add_cards(1, ids)
        inputs, catalog = self.inputs()
        self.assertEqual(inputs['targets'][0]['entries'], inputs['targets'][1]['entries'])
        self.assertTrue(inputs['targets'][0]['entries'])
        self.assertEqual(inputs['pool'], [])

    def test_oversized_carried_entry_is_segmented_before_target_condensing(self):
        from research_harness import review_final_pages as pages
        self.plan['targets']['handles'] = ['t1']
        self.packet[legacy.KEY]['targets'] = self.packet[legacy.KEY]['targets'][:1]
        self.add_cards(1, [self.packet[legacy.KEY]['targets'][0]['id']])
        self.state['cards'][0]['output']['summary'] = '"' * 29000
        frozen = digest(self.state)
        inputs, catalog = self.inputs()
        entries = inputs['pool']
        self.assertGreater(len(entries), 1)
        self.assertTrue(all(row['characters'] <= 53300 for row in entries))
        self.assertEqual(''.join(catalog[row['handle']]['text'] for row in entries),
                         legacy.render_json(catalog['c0-1']))
        turns = pages.plan_final_turns(**inputs)
        self.assertEqual(turns[0]['kind'], 'combine')
        self.assertEqual([row['kind'] for row in turns][-2:], ['page', 'closing'])
        smaller, _ = parts.split_final_entry(self.route, 'c0-1', catalog['c0-1'], 40000)
        self.assertNotEqual(smaller[0]['handle'], entries[0]['handle'])
        self.assertEqual(digest(self.state), frozen)
        pending = dict(self.state, pending_requests=[dict(unit='u1', start=0, end=20)])
        later, _ = parts.build_final_inputs(self.route, self.artifacts, self.packet, self.inventory,
            self.plan, pending, self.limits, 120000)
        self.assertEqual(later['pool'], entries)
        self.assertEqual(later['notes'], 6000)

    def test_checked_contradiction_and_verified_evidence_survive_condensing(self):
        from research_harness import review_final_pages as pages
        target = self.packet[legacy.KEY]['targets'][0]['id']
        self.add_cards(30, [target])
        scientific = dict(units=['u1'], question='Is the implication valid?', view='It appears supported.',
                          read=[dict(unit='u1')], bearing=[dict(target=target, relation='context', reason='Relevant.')])
        raw = dict(combination='scientific', finding='The original contradicts the implication.',
                   effect='contradicts', statements=[dict(unit='u1', text='The exact evidence.', why='Contradiction.')])
        carried = dict(raw, id='f1-1', combination_view=scientific,
                       verified_statements=raw['statements'], unverified_statements=0)
        self.state['findings'] = [dict(id='f1-1', output=raw, carried=carried)]
        self.state['entries'].append('f1-1')
        frozen = digest(self.state)
        inputs, catalog = self.inputs()
        turns = pages.plan_final_turns(**inputs)
        self.assertTrue(any(row['kind'] == 'combine' and 'f1-1' in row['block']['entries'] for row in turns))
        self.assertEqual(catalog['f1-1']['effect'], 'contradicts')
        self.assertEqual(catalog['f1-1']['verified_statements'], raw['statements'])
        self.assertEqual(json.loads(parts._find_stored_entry_unit(self.state, 'f1-1')['text']), raw)
        self.assertEqual(digest(self.state), frozen)

    def test_fragment_on_a_page_can_be_cited_requested_and_retained(self):
        fragment = dict(id='c1-1@0-10', entry='c1-1', start=0, end=10, text='0123456789')
        header = dict(protocol=parts.PROTOCOL, kind='final', final_form='page', final_turn=1,
            page_plan_digest='a' * 64, logical_packet_digest='b' * 64, series_key=dict(role='result'),
            inventory=parts._put_value(self.artifacts, self.inventory), inventory_digest=digest(self.inventory),
            state=parts._put_value(self.artifacts, self.state), state_digest=digest(self.state),
            targets=self.packet[legacy.KEY]['targets'], adapter='problem_result', capacity=self.limits)
        packet = {legacy.KEY: header, legacy.STATE_KEY: dict(cards=[fragment], combinations=[], findings=[])}
        resolved = parts.build_handle_packet(packet, self.artifacts, {})
        self.assertIn(fragment['id'], resolved['handle_table'])
        from research_harness.review_handles import expand_handles
        reference = parts._put_value(self.artifacts, fragment)
        self.assertEqual(expand_handles(packet, dict(ref=fragment['id']), artifacts=self.artifacts,
                                       assignment={})['sha256'], reference['sha256'])
        output = dict(kind='requests', unit_requests=[dict(unit=fragment['id'])], source_requests=[])
        parts.validate_planned_output(packet, output, self.inventory, self.plan, self.state,
                                     self.route, artifacts=self.artifacts, assignment={})
        state = copy.deepcopy(self.state)
        parts._advance_final_progress(state, dict(packet=packet, output=output,
            receipt=dict(assignment_id='page', attempt_id='attempt')))
        self.assertEqual(state['final_entries'][fragment['id']], fragment)
        self.assertEqual(json.loads(parts._find_stored_entry_unit(state, fragment['id'])['text']), fragment)

    def test_small_v3_final_keeps_the_base_packet_bytes(self):
        from test_review_output_profile import build_row
        from research_harness.review_parts_roles import get_adapter
        logical = copy.deepcopy(self.logical)
        with patch.object(capacity, 'read_profile_priors', return_value=[
                dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12) for kind in legacy.KINDS]):
            admitted = parts.admit_packet({}, self.artifacts, self.route, logical, 'problem_result')
        plan, inventory = admitted['plan'], admitted['inventory']
        state = parts.build_series_state([])
        state['cursor'] = dict(unit_index=len([row for row in inventory['units'] if row['required']]), unit_offset=0)
        binding = dict(adapter='problem_result', series_assignment_id='single-final',
            series_key=get_adapter('problem_result')['series_key'](logical), plan_digest=digest(plan),
            inventory=parts._put_value(self.artifacts, inventory), inventory_digest=digest(inventory),
            logical_packet=parts._put_value(self.artifacts, logical), logical_packet_digest=digest(logical),
            state=parts._put_value(self.artifacts, state), state_digest=digest(state), _targets=admitted['targets'])
        limits = {kind: capacity.derive_plan_task_limits(plan, kind) for kind in legacy.KINDS}
        packet = parts.plan_series_turn(self.route, self.artifacts, logical, inventory, plan, state,
            binding=binding, limits_by_kind=limits)
        before = legacy.render_json(packet)
        after = parts.prepare_series_final({}, self.artifacts, None, self.route, logical, inventory, plan, state,
            limits, packet)
        self.assertEqual(legacy.render_json(after), before)

    def test_one_target_one_block_above_floor_is_condensed_before_retry(self):
        from research_harness import review_final_pages as pages, review_condensing
        inputs = dict(page_room=120000, target_allowance=1, target_minimum=6700, notes=0, pool=[],
            targets=[dict(handle='t1', entries=[dict(handle='card-a', characters=60000),
                dict(handle='card-b', characters=10000)], rows=[])], closing_room=120000,
            row_characters=360, condense=review_condensing.plan_condensing)
        turns = pages.plan_final_turns(**inputs)
        self.assertEqual([row['kind'] for row in turns], ['final'])
        smaller = pages.split_final_turn(turns, 1, 60000, review_condensing.plan_condensing)
        self.assertGreater(sum(row['kind'] == 'combine' for row in smaller), 0)
        page = next(row for row in smaller if row['kind'] == 'page')
        self.assertLessEqual(page['characters'], 60000)
        self.assertIsNone(pages.split_final_turn(smaller, page['number'], 60000, review_condensing.plan_condensing))
