"""Paged parts series reach the ordinary review commands through one composite."""
import copy
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from research_harness import review_parts as legacy, review_parts_v3 as parts
from research_harness import review_composition as composition, review_composite as composite
from research_harness import review_continuation as continuation
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import research_decisions as decisions
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from review_parts_fixtures import PartsResultCase
from test_review_parts_v3 import build_native_acceptance_case, make_turn_output, set_up_parts_v2_fixture


class PagedSeriesTests(PartsResultCase):
    output_tokens = 128000

    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
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

    def start(self, objections=40, characters=400000, check=False, first_objection_id=None):
        self.oversized(count=2, characters=characters, limit=1300000)
        self.objections = [dict(id='objection-%d' % index, claim='Assess material concern %d.' % index,
            version_evidence=[dict(id='evidence-%d' % index, reason='A recorded material concern.')])
            for index in range(objections)]
        if first_objection_id is not None:
            self.objections[0]['id'] = first_objection_id
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

    def assign_planned_successor(self, assignment):
        action = continuation.plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertEqual((action['state'], action['command']), ('continue', 'review-assignment'))
        saved = self.case.mutate(protocol.record_assignment, action['payload'])['result']
        self.assertEqual(saved['context']['prior_assignment_id'], assignment['id'])
        return saved

    @contextmanager
    def copy_store(self):
        from pathlib import Path
        from shutil import copytree
        from tempfile import TemporaryDirectory
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'copy'
            copytree(self.case.root, root)
            with patch.multiple(self.case, root=root, store=Store(root), artifacts=ArtifactStore(root),
                                expected=copy.deepcopy(self.case.expected)):
                yield

    def advance_from_plan(self, assignment, output=None):
        self.turns.append(assignment)
        receipt = self.invoke(assignment, self.answer(assignment) if output is None else output)
        self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
        return self.assign_planned_successor(assignment)

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
                    # The page plan holds through the closing, and the planner's recording step is the one taken.
                    action = continuation.plan_next(records, self.case.artifacts, assignment['id'])
                    self.assertEqual((action['state'], action['command']), ('record', 'value-review'), action)
                    saved = self.case.mutate(decisions.record_value_review, action['payload'])['result']
                    self.assertEqual(saved['payload']['value'], output['value'])
                    self.assertEqual(continuation.plan_next(self.case.read_records(), self.case.artifacts,
                                                            assignment['id'])['state'], 'done')
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

    def answer_citing(self, assignment, citation):
        """Answer a page and add one citation to the first evidence field of its first target."""
        output = self.answer(assignment)
        field = next(path for path in output['targets'][0]['fields'] if path.endswith('/evidence'))
        output['targets'][0]['fields'][field].append(citation)
        return output, field

    def assert_cited_span(self, assignment, field, text):
        _, accepted = protocol.read_assignment_output(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertEqual(self.case.artifacts.read(accepted['targets'][0]['fields'][field][-1]),
                         text[1:4].encode('utf-8'))

    def test_checked_contradiction_reaches_page_after_condensing_then_records(self):
        """Read a late required search while originals of a checked, condensed page are queued.

        Twenty characters of a fragment record are delivered after the currency change. The first delivery asks
        for four of them again by the printed handle. Pages cite three of them on the delivery and after a refusal
        replacement and a capacity replacement, and the closing takes the planner's recording step.
        """
        self.pin_final(9)
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
            assignment = self.advance_from_plan(assignment, output)
        else:
            self.fail('No scientific combination was assigned.')
        target = header['targets'][0]['id']
        scientific = dict(units=['u1'], question='Does the evidence support the implication?',
            view='It appears supported.', read=[dict(unit='u1', start=0, end=1000)],
            bearing=[dict(target=target)])
        combination_assignment = assignment
        assignment = self.advance_from_plan(assignment, dict(kind='combine', turn=header['turn'],
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
        assignment = self.advance_from_plan(assignment, dict(kind='check', turn=header['turn'], findings=[finding]))
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
            assignment = self.advance_from_plan(assignment, output)
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
        self.assertIn('t1', self.packet(assignment)[legacy.KEY]['page']['targets'])
        condensed = next(row for values in delivered.values() for row in values
                         if row.get('summary') == json.dumps(summary))
        block = condensed['block']
        fragment = next(handle for handle in state['final_entries'] if handle.startswith(checked['id'] + '@'))
        receipt = self.invoke(assignment, dict(kind='requests',
            unit_requests=[dict(unit='u1'), dict(unit=checked['id'], start=0, end=256), dict(unit=block, entries=True),
                           dict(unit=fragment, start=0, end=20)],
            source_requests=[]))
        self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
        search = dict(id='late-checked-original', queries=['late original'], reason='New required evidence.')
        self.case.put('literature_search', search['id'], search)
        self.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        assignment = self.assign_planned_successor(assignment)
        from research_harness.artifacts import describe_artifact
        fragment_text = legacy.render_json(state['final_entries'][fragment])
        fragment_digest = describe_artifact(fragment_text.encode(), 'application/json')['sha256']
        requested, versions, read_new_inventory, asked_again = {}, {}, False, False
        for _ in range(70):
            packet = self.packet(assignment)
            header = packet[legacy.KEY]
            if header['kind'] == 'read':
                self.assertNotEqual(header['inventory_digest'], state['final_currency']['inventory_digest'])
                read_new_inventory = True
            for row in packet.get('review_parts_requests', []):
                requested.setdefault(row['unit'], []).append(row['text'])
                versions[row['unit']] = row['descriptor']['digest']
            printed = [row['unit'] for row in packet.get('review_parts_requests', [])
                       if row['descriptor']['digest'] == fragment_digest]
            if printed and not asked_again:
                # The catalog name of the fragment was replaced; the printed handle of this delivery names it.
                asked_again = True
                assignment = self.advance_from_plan(assignment, dict(kind='requests',
                    unit_requests=[dict(unit=printed[0], start=0, end=4)], source_requests=[]))
                continue
            if header['kind'] == 'final' and not header.get('pending_ranges'):
                break
            output = (dict(kind='requests', unit_requests=[], source_requests=[])
                      if header.get('pending_ranges') else self.answer(assignment))
            assignment = self.advance_from_plan(assignment, output)
        else:
            self.fail('The late inventory prevented the original requests from completing.')
        self.assertTrue(read_new_inventory)
        planned_headers = [self.packet(turn)[legacy.KEY] for turn in self.turns]
        self.assertTrue(any(header.get('mode') == 'condense' and 'final_form' not in header
                            for header in planned_headers))
        finding_ref, block_ref, fragment_ref = [
            describe_artifact(legacy.render_json(record).encode(), 'application/json')
            for record in (checked['output'], state['final_blocks'][block], state['final_entries'][fragment])]
        finding_handle, block_handle, fragment_handle = [
            next(handle for handle, checksum in versions.items() if checksum == reference['sha256'])
            for reference in (finding_ref, block_ref, fragment_ref)]
        self.assertIn(conclusion, ''.join(requested[finding_handle]))
        self.assertEqual(''.join(requested[block_handle]), legacy.render_json(state['final_blocks'][block]))
        self.assertEqual(requested[fragment_handle], [fragment_text[:20], fragment_text[:4]])
        span = dict(ref=fragment_handle, start=1, end=4)
        output, field = self.answer_citing(assignment, span)
        output['targets'][0]['fields'][field].insert(-1, dict(ref=block_handle))
        following = self.advance_from_plan(assignment, output)
        _, accepted = protocol.read_assignment_output(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertIn(block_ref, accepted['targets'][0]['fields'][field])
        self.assert_cited_span(assignment, field, fragment_text)
        self.assertEqual(self.packet(following)[legacy.KEY]['final_form'], 'page')
        foreign = self.answer(following)
        foreign['targets'][0]['target'] = 't' + '9' * 12
        receipt = self.invoke(following, foreign)
        self.assertEqual(receipt.get('output_refusal', {}).get('code'), 'review_composition_conflict')
        action = continuation.plan_next(self.case.read_records(), self.case.artifacts, following['id'])
        self.assertEqual(action['command'], 'review-assignment', action)
        refused = self.case.mutate(protocol.record_assignment, action['payload'])['result']
        self.invoke_usage(refused, self.answer_citing(refused, span)[0], tokens=128000)
        smaller = self.replace_capacity(refused)
        output, field = self.answer_citing(smaller, span)
        following = self.advance_from_plan(smaller, output)
        self.assert_cited_span(smaller, field, fragment_text)
        self.finish(following)
        self.assertEqual(original, {key: digest(self.case.read_records()['review_attempt'][key]) for key in original})

    def test_q7_eight_hundred_targets_check_admits_and_forecasts_pages(self):
        revision = self.case.store.revision
        result = self.start(782, check=True)  # With the 18 fixed targets of a result review: 800 targets.
        self.assertGreater(result['capacity_forecast']['by_kind']['final']['pages'], 1)
        self.assertEqual(self.case.store.revision, revision + 2)  # start seeds its two search records.
        # The read frame holds the table of the 800 targets beside its four reserves, each at its whole size.
        read = result['capacity_plan']['frame']['read']
        reserves = ('$groups', '$continued_unit', '$seam', '$protocol')
        self.assertEqual({name: read['bounded_fields'][name] for name in reserves},
                         {name: read['fields'][name] for name in reserves})

    def test_q7_five_thousand_targets_start_the_series_of_the_next_protocol(self):
        # About 4 s. The count is the subject: the table of 5,000 targets is larger than the frame of a turn.
        assignment = self.start(4982)
        header = self.packet(assignment)[legacy.KEY]
        self.assertEqual((header['protocol'], header['kind'], len(header['targets'])), (legacy.PROTOCOL, 'read', 5000))
        self.assertNotIn('capacity_plan', assignment)

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
        assignment = self.advance_from_plan(assignment, dict(stage='material', title='Independent argument',
            part=1, text='First part of the finite argument.', more=True))
        self.assertEqual(self.packet(assignment)[legacy.KEY]['page']['targets'], targets)
        self.assertEqual(self.packet(assignment)['review_parts_material_request']['part'], 2)
        assignment = self.advance_from_plan(assignment, dict(stage='material', title='Independent argument',
            part=2, text='Second part of the finite argument.', more=False))
        self.assertEqual(self.packet(assignment)[legacy.KEY]['page']['targets'], targets)
        output = self.answer(assignment)
        field = next(path for path in output['targets'][0]['fields'] if path.endswith('/evidence'))
        output['targets'][0]['fields'][field].append(dict(ref='m1'))
        assignment = self.advance_from_plan(assignment, output)
        final, record = self.finish(assignment)
        self.assertEqual(len({key for page in record['pages'] for key in page['targets']}), 18)

    def start_request_fixture(self, objections=0, characters=50000, **options):
        from research_harness import review_single_request
        with patch.object(review_single_request, 'describe_task_entry', return_value='task'):
            return self.start(objections, characters=characters, **options)

    def advance_partial_request(self, assignment, output):
        self.turns.append(assignment)
        receipt = self.invoke_usage(assignment, output, tokens=64000)
        self.assertNotIn('output_refusal', receipt, receipt)
        return self.assign('page-turn-%d' % len(self.turns), assignment['id'])

    def prepare_partial_original(self):
        self.pin_final(9)
        first = self.reach_page(self.start_request_fixture(characters=220000))
        request = dict(kind='requests', unit_requests=[dict(unit='u1')], source_requests=[])
        packet = self.packet(first)
        inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
        length = len(next(row['text'] for row in inventory['units'] if row['handle'] == 'u1'))
        fragment = self.advance_partial_request(first, request)
        following = self.packet(fragment)
        delivered, = following['review_parts_requests']
        self.assertLess(delivered['end'], length)
        with self.subTest(rule='B5a'):
            self.assertEqual(following[legacy.KEY].get('pending_ranges'),
                             dict(total=1, rows=[dict(unit='u1', start=delivered['end'], end=length)]))
        return fragment

    def test_answer_abandons_pending_original_and_next_page_records(self):
        """Use an original larger than a page to leave a range the answer can end."""
        fragment = self.prepare_partial_original()
        following = self.advance(fragment)
        self.assertNotIn('review_parts_requests', self.packet(following))
        self.assertNotIn('pending_ranges', self.packet(following)[legacy.KEY])
        self.finish(following)

    def test_empty_request_continues_pending_original_twice(self):
        """Use an original spanning three pages of delivery to test empty requests twice."""
        fragment = self.prepare_partial_original()
        empty = dict(kind='requests', unit_requests=[], source_requests=[])
        for _ in range(2):
            previous = self.packet(fragment)['review_parts_requests'][-1]['end']
            fragment = self.advance_partial_request(fragment, empty)
            delivered, = self.packet(fragment)['review_parts_requests']
            self.assertEqual(delivered['start'], previous)
            self.assertGreater(delivered['end'], previous)
        self.finish(fragment)

    def test_closing_lists_binding_pointers_and_original_page_citations(self):
        """Complete three short pages, then inspect their authenticated closing rows."""
        self.pin_final(10)
        assignment = self.reach_page(self.start_request_fixture(objections=9))
        expected, cited_pages = {}, 0
        while self.packet(assignment)[legacy.KEY].get('final_form') != 'closing':
            header = self.packet(assignment)[legacy.KEY]
            output = self.answer(assignment)
            if header.get('final_form') == 'page':
                before = len(expected)
                for item in output['targets']:
                    paths = [path for path in item['fields'] if path.endswith('/evidence')]
                    if paths:
                        path = paths[0]
                        item['fields'][path] = [dict(ref='u2'), dict(ref='u1', start=0, end=5),
                                               dict(ref='u2', start=5, end=15)]
                        item['sentence'] = 'The originals support this assessment.'
                        expected[item['target']] = path
                cited_pages += len(expected) > before
            assignment = self.advance_from_plan(assignment, output)
        self.assertEqual(cited_pages, 3)
        header = self.packet(assignment)[legacy.KEY]
        output = self.answer(assignment)
        with self.subTest(rule='B10'):
            bindings = header['closing'].get('bindings')
            self.assertEqual(bindings, ['/stage', '/value/bar_ids'])
            self.assertLessEqual(set(output['bindings']), set(bindings))
        rows = {row[0]: row for row in header['closing']['rows']}
        for handle in expected:
            with self.subTest(rule='B11', handle=handle):
                self.assertEqual(rows[handle][3], ['u2', 'u1'])
        assignment = self.advance_from_plan(assignment, dict(kind='requests',
            unit_requests=[dict(unit='u1', start=0, end=20)], source_requests=[]))
        self.assertEqual(self.packet(assignment)[legacy.KEY]['final_form'], 'closing')
        assignment = self.advance_from_plan(assignment, dict(stage='material', title='Closing argument',
            part=1, text='The closing argument.', more=False))
        self.assertEqual(self.packet(assignment)[legacy.KEY]['final_form'], 'closing')
        self.assertEqual(self.packet(assignment)[legacy.KEY]['arguments']['rows'][0]['handle'], 'm1')
        final, record = self.finish(assignment)
        self.assertEqual(len(record['pages']), 3)
        records = self.case.read_records()
        attempt = next(row for row in records['review_attempt'].values() if row['assignment_id'] == final['id'])
        self.assertTrue(attempt['preflight']['plan_comparison']['fits'])
        final_header = self.packet(final)[legacy.KEY]
        self.assertEqual(final_header['closing']['rows'], list(rows.values()))
        self.assertLessEqual(capacity.measure_capacity_value(records['review_route']['route'], final_header['closing'])
                             + parts.FINAL_REFERENCE_CHARACTERS, final_header['capacity']['material_allowance'])
        records.setdefault('review_output_refusal', {})[attempt['id']] = dict(details=dict(composed_output=True))
        handle = next(iter(expected))
        finding = parts.describe_composed_refusal(records, self.case.artifacts, attempt, 'review.value', expected[handle])
        self.assertEqual(finding['decision_row'], rows[handle])

    def test_currency_source_request_keeps_page_one_and_optional_source_unrequested(self):
        first_source, second_source, unrequested = [self.case.metadata(i) for i in (91, 92, 93)]
        sentence = 'A captured theorem bounds every finite graph.'
        second_sentence = 'The captured source supplies a comparison.'
        self.case.capture(first_source, pdf_text=sentence)
        self.case.capture(second_source, pdf_text=second_sentence)
        self.case.capture(unrequested, pdf_text='This source stays optional.')
        first = self.reach_page(self.start(0))
        second = self.advance(first)
        currency = self.packet(first)[legacy.KEY]['inventory_digest']
        requests = [
            dict(id='requested-source', kind='source', version_id=first_source, depth='fulltext',
                 why='Compare the bounds.'),
            dict(id='requested-source', kind='source', version_id=second_source, depth='fulltext')]
        receipt = self.invoke(second, dict(kind='requests', unit_requests=[], source_requests=requests))
        self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
        status = protocol.assignment_state(self.case.read_records(), self.case.artifacts, second['id'])
        second = self.assign_planned_successor(second)
        self.assertEqual(status['source_request_status'], 'pending')
        self.assertEqual(status['source_requests'], requests)
        packet = self.packet(second)
        answering = second
        self.assertEqual(packet[legacy.KEY]['inventory_digest'], currency)
        delivered = packet['review_parts_requests']
        self.assertEqual([row['text'] for row in delivered], [sentence, second_sentence])
        inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
        optional = next(row for row in inventory['units'] if not row['required'] and
            row['source']['identity'].get('version_id') == unrequested)
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

    def test_page_handle_and_colliding_objection_keep_both_decisions(self):
        self.pin_final(9)
        assignment = self.reach_page(self.start_request_fixture(objections=1, first_objection_id='t1'))
        expected = {'t1': 'The problem statement was assessed.', 't19': 'The objection was resolved.'}
        seen = {}
        while True:
            header = self.packet(assignment)[legacy.KEY]
            if header.get('final_form') == 'closing':
                break
            if header.get('final_form') != 'page':
                assignment = self.advance_from_plan(assignment)
                continue
            logical = dict(legacy.read_logical_packet(self.case.artifacts, header), review_parts=header)
            rule = composition.find_rule('result', logical)
            by_handle = {row['handle']: row['id'] for row in composition.list_targets(rule, logical)}
            self.assertEqual((by_handle['t1'], by_handle['t19']), ('problem_statement', 't1'))
            handles = header['page']['targets']
            final = self.final_output()
            final['value']['objection_findings'] = [dict(id='t1', status='resolved',
                reason='The supplied record resolves the concern.', evidence=final['value']['evidence'])]
            pages, _ = composition.split_output(rule, logical, final, [[dict(id=by_handle[h]) for h in handles]])
            output = pages[0]['output']
            for row, handle in zip(output['targets'], handles):
                row['target'] = handle
                if handle in expected:
                    seen[handle] = row['sentence'] = expected[handle]
            assignment = self.advance_from_plan(assignment, output)
        self.assertEqual(seen, expected)
        rows = {row[0]: row[2] for row in header['closing']['rows']}
        self.assertEqual({handle: rows[handle] for handle in expected}, expected)
        last, record = self.finish(assignment)
        self.assertIn('t19', [handle for page in record['pages'] for handle in page['targets']])
        saved = next(row for row in self.case.read_records()['value_review'].values() if row['assignment_id'] == last['id'])
        self.assertEqual(saved['payload']['value']['objection_findings'][0]['id'], 't1')

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

    def test_late_unit_replans_split_final_as_one_role_answer(self):
        """The stored page plan becomes one request after a late read."""
        from test_review_output_profile import build_row
        self.pin_final(18)
        self.case.capture(self.case.metadata(93), pdf_text='A captured source.')
        measured = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('read', 'combine', 'check', 'final', 'single')])
        measured.start()
        self.addCleanup(measured.stop)
        assignment = self.start_request_fixture()
        while self.packet(assignment)[legacy.KEY]['kind'] != 'final':
            assignment = self.advance(assignment)
        self.assertNotIn('final_page_plan', self.packet(assignment)[legacy.KEY])
        self.invoke_usage(assignment, tokens=128000)
        page = self.replace_capacity(assignment)
        self.assertEqual(self.packet(page)[legacy.KEY]['final_form'], 'page')

        request = dict(kind='requests', unit_requests=[dict(unit='u1', start=0, end=20)], source_requests=[])
        receipt = self.invoke(page, request)
        self.assertNotIn('output_refusal', receipt)
        search = dict(id='late-original', queries=['late original'], reason='New required evidence.')
        self.case.put('literature_search', search['id'], search)
        self.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        following = self.assign('late-original-turn', page['id'])
        self.assertEqual(self.packet(following)[legacy.KEY]['kind'], 'read')
        while self.packet(following)[legacy.KEY]['kind'] != 'final':
            following = self.advance(following)
        packet = self.packet(following)
        header = packet[legacy.KEY]
        reference = parts._read_value(self.case.artifacts, header['final_page_plan'])
        page_plan = parts._read_value(self.case.artifacts, reference['artifact'])
        self.assertEqual([row['kind'] for row in page_plan['turns']], ['final'])
        self.assertFalse({'page_plan_digest', 'final_turn', 'final_form', 'page'} & header.keys())
        self.assertEqual([(row['unit'], row['start'], row['end']) for row in packet['review_parts_requests']],
                         [('u1', 0, 20)])
        following = self.advance_from_plan(following, dict(kind='requests',
            unit_requests=[dict(unit='u1', start=20, end=40)], source_requests=[]))
        with self.copy_store():
            self.refuse_legacy_source_fields(following)
        following = self.advance_from_plan(following, dict(stage='material', title='Singleton proof',
            part=1, text='The singleton argument.', more=False))
        self.assertEqual(self.packet(following)[legacy.KEY]['arguments']['rows'][0]['handle'], 'm1')
        self.assertNotIn('page', self.packet(following)[legacy.KEY])
        output = self.answer(following)
        receipt = self.invoke(following, output)
        self.assertNotIn('output_refusal', receipt, receipt)
        records = self.case.read_records()
        verified = parts.verify_series(records, self.case.artifacts, following['id'])
        self.assertTrue(verified['complete'], verified['issues'])
        saved = self.case.mutate(decisions.record_value_review, dict(output,
            id='late-single-reviewed', dossier_id=None, assignment_id=following['id']))['result']
        self.assertEqual(saved['payload']['value'], output['value'])

    def test_ordinary_final_plans_store_accepted_request_and_material_steps(self):
        self.pin_final(18)
        self.oversized(count=1, characters=50000, limit=1300000)
        self.objections = []
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            assignment = self.assign()
        while self.packet(assignment)[legacy.KEY]['kind'] != 'final':
            assignment = self.advance_from_plan(assignment)
        self.assertNotIn('final_page_plan', self.packet(assignment)[legacy.KEY])
        assignment = self.advance_from_plan(assignment, dict(kind='requests',
            unit_requests=[dict(unit='u1', start=0, end=20)], source_requests=[]))
        material = dict(stage='material', title='Proof', part=1, text='The argument.', more=True)
        receipt = self.invoke(assignment, material)
        self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
        search = dict(id='late-multipart', queries=['evidence'], reason='New required evidence.')
        self.case.put('literature_search', search['id'], search)
        self.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        assignment = self.assign_planned_successor(assignment)
        self.assertEqual(self.packet(assignment)[legacy.KEY]['kind'], 'read')
        while self.packet(assignment)[legacy.KEY]['kind'] != 'final':
            packet = self.packet(assignment)
            receipt = self.invoke(assignment, make_turn_output(packet))
            self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
            self.assertNotIn('review_parts_material_request', packet)
            assignment = self.assign_planned_successor(assignment)
        request = self.packet(assignment)['review_parts_material_request']
        assignment = self.advance_from_plan(assignment,
            dict(material, part=request['part'], title=request['title'], more=False))
        self.assertNotIn('final_page_plan', self.packet(assignment)[legacy.KEY])
        self.assertEqual(self.packet(assignment)[legacy.KEY]['arguments']['rows'][0]['handle'], 'm1')
        self.assertNotIn('output_refusal', self.invoke(assignment, self.answer(assignment)))
        records = self.case.read_records()
        self.assertTrue(parts.verify_series(records, self.case.artifacts, assignment['id'])['complete'])
        action = continuation.plan_next(records, self.case.artifacts, assignment['id'])
        self.assertEqual(action['command'], 'value-review')
        self.case.mutate(decisions.record_value_review, action['payload'])
        self.assertEqual(continuation.plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])['state'],
                         'done')

    def refuse_legacy_source_fields(self, assignment):
        output = dict(stage='result', support=None, value=dict(status='unresolved'),
                      objections=[], limitations=[])
        captured = next(row for row in self.packet(assignment)[legacy.KEY]['sources']['rows']
                        if row['depth'] == 'fulltext')
        receipt = self.invoke(assignment, dict(output, source_requests=[
            dict(id='legacy', kind='source', version_id=captured['version_id'], depth='fulltext')]))
        self.assertEqual(receipt.get('output_refusal', {}).get('code'), 'invalid_value_review')

    def test_ordinary_final_refuses_legacy_source_request(self):
        self.pin_final(18)
        self.oversized(count=1, characters=50000, limit=1300000)
        self.objections = []
        self.case.capture(self.case.metadata(93), pdf_text='A captured source.')
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            assignment = self.assign()
        while self.packet(assignment)[legacy.KEY]['kind'] != 'final':
            assignment = self.advance_from_plan(assignment)
        self.refuse_legacy_source_fields(assignment)

    def test_unknown_positional_page_target_is_a_recorded_refusal(self):
        self.pin_final(9)
        page = self.reach_page(self.start_request_fixture())
        output = self.answer(page)
        output['targets'][0]['target'] = 't' + '9' * 5000
        receipt = self.invoke(page, output)
        self.assertEqual(receipt.get('output_refusal', {}).get('code'), 'review_composition_conflict')
        action = continuation.plan_next(self.case.read_records(), self.case.artifacts, page['id'])
        self.assertEqual(action['command'], 'review-assignment')
        self.assertIn('output_refusal', action['payload']['context'])

    def run_matrix_step(self, assignment, seen, stub=None, *, output_tokens=12, action=None):
        from codex_route_fixtures import bind_codex_response, build_codex_response
        from research_harness import review_transport
        from research_harness.review_excerpts import read_sent_packet
        if action is None:
            action = continuation.plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
        step = digest([action['state'], action.get('command'), action.get('payload')])
        self.assertNotIn(step, seen, (action['state'], action.get('command'), action.get('payload')))
        seen.add(step)
        command = action.get('command')
        if command == 'review-run':
            self.assertIsNotNone(stub)

            def respond(route, request, credential):
                response = build_codex_response(route, request, stub(read_sent_packet(request)))
                response['usage']['output_tokens'] = output_tokens
                return bind_codex_response(route, request, response)

            with patch.object(review_transport, 'send_request', side_effect=respond):
                return self.case.mutate(protocol.invoke_assignment, action['payload'], credential='test')
        if command == 'review-assignment':
            return self.case.mutate(protocol.record_assignment, action['payload'])['result']
        if command == 'value-review':
            return self.case.mutate(decisions.record_value_review, action['payload'])['result']
        self.fail((action['state'], action.get('code'), command))

    def test_final_request_matrix_from_one_recorded_store(self):
        """Reuse the recorded reads and block. Vary the final request, timing, currency and delivery size."""
        from itertools import product
        from research_harness.review_refusal import evidence_arrays
        from test_review_output_profile import build_row

        self.pin_final(18)
        self.oversized(count=1, characters=4000, limit=1300000)
        self.objections = []
        version = self.case.metadata(91)
        self.case.capture(version, pdf_text='The source records a finite example.')
        profile = [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                   for kind in ('single', 'read', 'combine', 'check', 'final')]

        choose_block = parts._select_condensing_block
        def one_block(route, plan, limits, state, **options):
            if not options['reading'] and state['entries'] and not state['combinations']:
                entries = parts._describe_entry_rows(route, parts._list_state_entries(state))
                return parts.cut_condensing_blocks(entries, limits['material_allowance'])[0]
            return choose_block(route, plan, limits, state, **options)

        def read_or_combine(packet):
            answer = make_turn_output(packet)
            if packet[legacy.KEY]['kind'] == 'read':
                for card in answer['cards']:
                    card['summary'] = 'Original note. ' * 180
            return answer

        with patch.object(capacity, 'read_profile_priors', return_value=profile), \
                patch.object(parts, '_select_condensing_block', side_effect=one_block):
            from research_harness import review_single_request
            with patch.object(review_single_request, 'describe_task_entry', return_value='task'):
                seed = self.assign()
            seed_seen = set()
            while self.packet(seed)[legacy.KEY]['kind'] != 'final':
                receipt = self.run_matrix_step(seed, seed_seen, read_or_combine)
                self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
                seed = self.run_matrix_step(seed, seed_seen)
            self.assertNotIn('final_page_plan', self.packet(seed)[legacy.KEY])
            cases = [(request, when, event, split)
                     for request, when, event, split in product(
                         ('unit', 'record', 'entries', 'source'), ('before', 'after', 'unfinished'),
                         ('none', 'late', 'currency'), (False, True))]
            cases.extend((request, when, 'none', False)
                         for request, when in product(('legacy_source', 'settlement'), ('before', 'after')))
            example = self.case.build_review_output('result')
            for request, when, event, split in cases:
                with self.subTest(request=request, when=when, event=event, split=split), self.copy_store():
                    self.drive_final_request_case(seed, request, when, event, split, example,
                                                  read_or_combine, evidence_arrays)

    def drive_final_request_case(self, seed, request, when, event, split, example, read_or_combine, evidence_arrays):
        seen, rows, expected = set(), [], []
        assignment = seed
        material = dict(stage='material', title='Proof', part=1, text='A finite argument.', more=False)
        source = dict(id='capture', kind='source', depth='fulltext')

        def accept(stub):
            receipt = self.run_matrix_step(assignment, seen, stub)
            self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
            return self.run_matrix_step(assignment, seen)

        def first_original(header):
            return next(place['units'][0] for place in header['places']['rows']
                        if place['path'] == '/problem_evidence/openness_searches')

        def find_captured_source(header):
            return next(row for row in header['sources']['rows'] if row['depth'] == 'fulltext')

        def final_answer(packet):
            answer = copy.deepcopy(example)
            header = packet[legacy.KEY]
            if header.get('final_form') == 'page':
                del answer['value']['bar_ids']  # The closing fills the harness fields of a final in pages.
            else:
                answer['value']['bar_ids'] = header['binding_values']['/value/bar_ids']
            for array in evidence_arrays(answer):
                array[:] = [dict(ref=first_original(header))]
            if rows and request != 'source':
                # A citation of a captured source first needs its recorded reading, a step outside this matrix.
                next(iter(evidence_arrays(answer))).append(dict(ref=rows[-1]['unit']))
            if header.get('final_form') == 'page':
                rule = composition.find_rule('result', packet)
                return composition.split_output(rule, packet, answer, [header['page']['targets']])[0][0]['output']
            return answer

        if event == 'currency':
            self.run_matrix_step(assignment, seen, final_answer, output_tokens=128000)
            assignment = self.run_matrix_step(assignment, seen)
            self.assertEqual(self.packet(assignment)[legacy.KEY].get('final_form'), 'page')
        if when == 'after':
            assignment = accept(lambda packet: material)
            self.assertEqual(self.packet(assignment)[legacy.KEY]['arguments']['rows'][0]['handle'], 'm1')
        if request in ('legacy_source', 'settlement'):
            invalid = dict(stage='result', support=None, value=dict(status='unresolved'), objections=[], limitations=[])
            def legacy_answer(packet):
                if request == 'settlement':
                    return dict(invalid, settled_requests=['capture'])
                captured = find_captured_source(packet[legacy.KEY])
                return dict(invalid, source_requests=[dict(source, version_id=captured['version_id'])])
            receipt = self.run_matrix_step(assignment, seen, legacy_answer)
            self.assertEqual(receipt.get('output_refusal', {}).get('code'), 'invalid_value_review')
            assignment = self.run_matrix_step(assignment, seen)
            receipt = self.run_matrix_step(assignment, seen, final_answer)
            self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
            self.run_matrix_step(assignment, seen)
            self.assertEqual(continuation.plan_next(self.case.read_records(), self.case.artifacts,
                                                   assignment['id'])['state'], 'done')
            return

        def request_answer(packet):
            # Ask from what the packet prints, and keep the text that the request names for the check of delivery.
            header = packet[legacy.KEY]
            entries = packet[legacy.STATE_KEY]['combinations']
            if not entries:
                entries = [entry for entry in packet[legacy.STATE_KEY]['cards'] if 'block' in entry]
            inventory = {row['handle']: row for row in legacy.read_inventory(self.case.artifacts,
                                                                            header['inventory'])['units']}
            unit_requests, source_requests = [], []
            if request == 'source':
                captured = find_captured_source(header)
                source_requests = [dict(source, version_id=captured['version_id'], why='Inspect the example.')]
                expected.append(inventory[captured['unit']]['text'])
            elif request == 'unit':
                unit_requests = [dict(unit=first_original(header), start=0, end=40)]
                expected.append(inventory[unit_requests[0]['unit']]['text'][:40])
            else:
                unit_requests = [dict(unit=entries[0]['block'], entries=request == 'entries')]
                state = parts._read_value(self.case.artifacts, header['state'], header['state_digest'])
                named = unit_requests[0]['unit'] + ('/entries' if request == 'entries' else '')
                expected.append(legacy.render_json(parts._list_requestable_records(state)[named]))
            return dict(kind='requests', unit_requests=unit_requests, source_requests=source_requests)

        receipt = self.run_matrix_step(assignment, seen, request_answer)
        self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
        original = self.packet(assignment)[legacy.KEY]
        def add_late_original():
            search = dict(id='matrix-late', queries=['late evidence'], reason='A new assigned original.')
            self.case.put('literature_search', search['id'], search)
            self.case.expected['openness_searches'].append(dict(search=search, sources=[]))

        during_partial_argument = when == 'unfinished' and split and event != 'none'
        if event != 'none' and not during_partial_argument:
            add_late_original()

        deliver = parts._deliver_requested_units
        def fit_request(route, inventory, scopes, room, *args, **options):
            proposed = deliver(route, inventory, scopes, room, *args, **options)
            if split and proposed and not proposed[0]['start'] and proposed[0]['end'] > 1:
                row = proposed[0]
                middle = row['end'] // 2
                smaller = dict(row, end=middle, text=row['text'][:middle])
                room = min(room, capacity.measure_capacity_value(route, [smaller]))
                return deliver(route, inventory, scopes, room, *args, **options)
            return proposed

        def respond(packet):
            header = packet[legacy.KEY]
            if header['kind'] != 'final':
                self.assertNotIn('review_parts_material_request', packet)
                return read_or_combine(packet)
            rows.extend(packet.get('review_parts_requests', []))
            argument = packet.get('review_parts_material_request')
            if argument:
                return dict(material, part=argument['part'], title=argument['title'])
            if when == 'unfinished' and rows and not header.get('arguments', {}).get('total'):
                return dict(material, more=True)
            if header.get('pending_ranges'):
                return dict(kind='requests', unit_requests=[], source_requests=[])
            return final_answer(packet)

        with patch.object(parts, '_deliver_requested_units', side_effect=fit_request):
            assignment = self.run_matrix_step(assignment, seen)
            for _ in range(12):
                receipt = self.run_matrix_step(assignment, seen, respond)
                self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))
                if during_partial_argument and rows and receipt['result'].get('material'):
                    add_late_original()
                    during_partial_argument = False
                records = self.case.read_records()
                action = continuation.plan_next(records, self.case.artifacts, assignment['id'])
                if action['command'] == 'value-review':
                    saved = self.run_matrix_step(assignment, seen, action=action)
                    self.assertEqual(saved['assignment_id'], assignment['id'])
                    self.assertEqual(continuation.plan_next(self.case.read_records(), self.case.artifacts,
                                                           assignment['id'])['state'], 'done')
                    break
                assignment = self.run_matrix_step(assignment, seen, action=action)
            else:
                self.fail('The matrix fixture did not record a review.')
        self.assertEqual(len(rows), 2 if split else 1)
        self.assertEqual(''.join(row['text'] for row in rows), expected[0])
        if split:
            self.assertEqual(rows[0]['end'], rows[1]['start'])
            self.assertEqual(rows[0]['unit'], rows[1]['unit'])
        final = self.packet(assignment)[legacy.KEY]
        if event != 'none':
            self.assertNotEqual(final['inventory_digest'], original['inventory_digest'])
        if event == 'currency':
            self.assertNotEqual(final['final_page_plan'], original['final_page_plan'])

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

    def test_floor_decision_through_plan_next_is_taken_by_the_store(self):
        """S-F4 of the review of the merged tree of 0.93.0, for the parts protocol v3: a page whose answers are whole
        at the output limit ends, through plan_next, with the decision of its capacity recovery. Its answer moves the
        series to a route of another class (the condition of item A16), because the store refuses a route of the same
        class for this series."""
        from research_harness.review_continuation import plan_next
        from research_harness.review_route_capacity import OUTPUT_CLASS_ROUTE_CONDITION
        assignment = self.reach_page(self.start(0))
        for index in range(30):
            self.invoke_usage(assignment, tokens=128000, attempt_id='floor-cap-%d' % index)
            action = plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
            if action['state'] == 'replace':
                assignment = self.case.mutate(protocol.record_assignment, action['payload'])['result']
            elif action['state'] != 'run':
                break
        self.assertEqual((action['state'], action['code']), ('stop', 'review_output_over_cap'), action)
        self.assertEqual(action['details']['decision']['then'], action['details']['recovery']['then'])
        [answer] = action['details']['decision']['then']
        self.assertEqual((answer['answer'], answer['condition']), ('Move to another route', OUTPUT_CLASS_ROUTE_CONDITION))


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

    def test_page_citing_an_unrequested_unit_is_refused_at_the_page(self):
        """S-F1 of the review of the merged tree of 0.93.0. The rule that a page cites only what this series delivered
        reads the records, so it ran only on the composed answer. A page that cites an optional unit that no turn
        requested is refused at its own place, and the plan replaces the page."""
        source = self.case.metadata(95)
        self.case.capture(source, pdf_text='An optional source was never requested.')
        assignment = self.reach_page(self.start(0))
        packet = self.packet(assignment)
        inventory = legacy.read_inventory(self.case.artifacts, packet[legacy.KEY]['inventory'])
        optional = next(row for row in inventory['units'] if not row['required'] and
                        row['source']['identity'].get('version_id') == source)
        output, field = self.answer_citing(assignment, dict(ref=optional['handle']))
        receipt = self.invoke(assignment, output)
        refusal = receipt['output_refusal']
        self.assertEqual(refusal['check'], 'review.evidence_delivery', refusal)
        self.assertEqual(refusal['details']['path'], '/targets/0/fields/' + composition._escape_field(field))
        action = continuation.plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertEqual(action['state'], 'replace', action)

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


    def test_closing_after_a_capped_single_final_counts_only_its_own_cap_and_records(self):
        """A final in one request that splits into pages whose closing condenses after its cap. Each turn after the
        caps measures the room of the plan again, and the page plans that the turns leave replay in verify_series."""
        from test_review_output_profile import build_row
        from research_harness import review_failure_class as failures
        measured = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('read', 'combine', 'check', 'final', 'single')])
        measured.start()
        self.addCleanup(measured.stop)
        assignment = self.start(0)
        while self.packet(assignment)[legacy.KEY]['kind'] != 'final':
            assignment = self.advance(assignment)
        self.assertNotIn('page_plan_digest', self.packet(assignment)[legacy.KEY])
        self.invoke_usage(assignment, tokens=128000)
        assignment = self.replace_capacity(assignment)
        while self.packet(assignment)[legacy.KEY].get('final_form') != 'closing':
            assignment = self.advance(assignment)
        closing = assignment
        records = self.case.read_records()
        self.assertEqual(failures.count_position(records, self.case.artifacts, closing)['failed_attempts'], 0)
        self.invoke_usage(assignment, tokens=128000)
        assignment = self.replace_capacity(assignment)
        while self.packet(assignment)[legacy.KEY].get('final_form') == 'combine':
            assignment = self.advance(assignment)
        self.assertEqual(self.packet(assignment)[legacy.KEY]['final_form'], 'closing')
        records = self.case.read_records()
        self.assertEqual(failures.read_paged_position(self.case.artifacts, closing),
                         failures.read_paged_position(self.case.artifacts, assignment))
        self.assertEqual(failures.count_position(records, self.case.artifacts, assignment)['failed_attempts'], 1)
        self.finish(assignment)

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
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
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
        case = set_up_parts_v2_fixture(self, VerificationPartsTests())
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
        case = set_up_parts_v2_fixture(self, ReadinessPartsTests())
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
        action = continuation.plan_next(case.case.read_records(), case.case.artifacts, final['id'])
        self.assertEqual((action['state'], action['command']), ('record', 'review-adjudication'), action)
        saved = case.case.mutate(protocol.record_adjudication, action['payload'])['result']
        self.assertEqual(saved['composite']['id'], record['id'])
        self.assertEqual(continuation.plan_next(case.case.read_records(), case.case.artifacts, final['id'])['state'],
                         'done')

    def answer_page(self, assignment, read_packet, full_output, artifacts):
        """The answer of a page: the part of the full answer that holds the targets of the page."""
        header = read_packet(assignment)[legacy.KEY]
        native = dict(legacy.read_logical_packet(artifacts, header), review_parts=header)
        rule = composition.find_rule(assignment['role'], native)
        reference = parts._read_value(artifacts, header['final_page_plan'])
        plan = parts._read_value(artifacts, reference['artifact'])
        groups = [row['targets'] for row in plan['turns'] if row['kind'] in ('page', 'final')]
        pages, _ = composition.split_output(rule, native, full_output(assignment), groups)
        return next(row['output'] for row in pages if row['targets'] == header['page']['targets'])

    def reach_adapter_page(self, assignment, read_packet, full_output, invoke, assign, artifacts):
        """Take the turns before the first page of an adapter series. Return the page and its answer."""
        for index in range(60):
            packet = read_packet(assignment)
            header = packet[legacy.KEY]
            if header.get('final_form') == 'page':
                return assignment, self.answer_page(assignment, read_packet, full_output, artifacts)
            if header.get('final_form') == 'combine':
                output = dict(block=header['block']['handle'], summary='Retained evidence.', refs=header['block']['entries'][:1])
            else:
                output = make_turn_output(packet)
            self.assertNotIn('output_refusal', invoke(assignment, output))
            assignment = assign('adapter-turn-%d' % index, assignment['id'])
        self.fail('No page was assigned.')

    def replace_refused_page(self, records, artifacts, page, mutate):
        action = continuation.plan_next(records(), artifacts, page['id'])
        self.assertEqual((action['state'], action['command']), ('replace', 'review-assignment'), action)
        return mutate(protocol.record_assignment, action['payload'])['result']

    def test_verification_page_citing_an_undelivered_source_is_refused_at_the_page(self):
        """S-F1 of the review of the merged tree of 0.93.0, for the verification adapter. The rule that a verification
        cites only source content that its series delivered reads the records, so it ran only on the composed answer.
        A page whose evidence cites stored bytes that no turn delivered is refused at its own place, and the
        replacement of the page is accepted."""
        from test_review_parts_study import VerificationPartsTests
        from research_harness import review_transport as transport
        case = set_up_parts_v2_fixture(self, VerificationPartsTests())
        self.pin_read_profile()
        quote = 'An unrequested note.'
        unsent = case.case.artifacts.put(quote.encode('utf-8'), 'text/plain; charset=utf-8')
        def answer(assignment):
            output = case.final_output(assignment)
            for check in output['checks']:
                check['evidence'] = [case.case.linked]
            return output
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            page, output = self.reach_adapter_page(case.assign(), case.packet, answer, case.invoke, case.assign,
                                                   case.case.artifacts)
            field = next(path for path in output['targets'][0]['fields'] if path.endswith('/evidence'))
            output['targets'][0]['fields'][field].append(dict(case.case.linked, artifact=unsent,
                locator=dict(kind='text', start=0, end=len(quote), quote=quote)))
            refusal = case.invoke(page, output)['output_refusal']
            self.assertEqual((refusal['code'], refusal['check']), ('review_evidence_undelivered', 'verification.parts'),
                             refusal)
            self.assertEqual(refusal['details']['path'], '/targets/0/fields/' + composition._escape_field(field))
            replacement = self.replace_refused_page(case.records, case.case.artifacts, page, case.case.mutate)
            receipt = case.invoke(replacement, self.answer_page(replacement, case.packet, answer, case.case.artifacts))
        self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))

    def test_adjudicator_page_citing_an_undelivered_record_is_refused_at_the_page(self):
        """S-F1 of the review of the merged tree of 0.93.0, for the problem adjudicator adapter. A page whose evidence
        cites a record that no packet of the adjudicator carried is refused at its own place, and the
        replacement of the page is accepted."""
        from test_review_parts_adjudication_journey import AdjudicationPartsCase
        from research_harness import review_transport as transport
        case = AdjudicationPartsCase()
        case.output_tokens = 128000
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized(count=2, paragraphs=800)
        self.pin_read_profile()
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            page, output = self.reach_adapter_page(case.assign(), case.packet, lambda assignment: case.final_output(),
                                                   case.invoke, case.assign, case.case.artifacts)
            field = next(path for path in output['targets'][0]['fields'] if path.endswith('/evidence'))
            output['targets'][0]['fields'][field].append(
                dict(output['targets'][0]['fields'][field][0], id='unsent-statement'))
            refusal = case.invoke(page, output)['output_refusal']
            self.assertEqual((refusal['code'], refusal['check']),
                             ('review_evidence_undelivered', 'adjudication.evidence_delivery'), refusal)
            self.assertEqual(refusal['details']['path'], '/targets/0/fields/' + composition._escape_field(field))
            replacement = self.replace_refused_page(case.case.read_records, case.case.artifacts, page,
                                                    case.case.mutate)
            receipt = case.invoke(replacement, self.answer_page(replacement, case.packet,
                                                                lambda assignment: case.final_output(),
                                                                case.case.artifacts))
        self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))

    def test_adjudicator_page_citing_a_record_without_its_digest_is_refused_at_the_page(self):
        """S-F1 of the review of the merged tree of 0.93.0, for the problem adjudicator adapter. The composed check
        reads /evidence in the grammar that requires the digest of a record reference; the page check admitted a
        reference without it, and the closing was refused. Such a page is refused at its own place, and the
        replacement of the page is accepted."""
        from test_review_parts_adjudication_journey import AdjudicationPartsCase
        from research_harness import review_transport as transport
        case = AdjudicationPartsCase()
        case.output_tokens = 128000
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized(count=2, paragraphs=800)
        self.pin_read_profile()
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            page, output = self.reach_adapter_page(case.assign(), case.packet, lambda assignment: case.final_output(),
                                                   case.invoke, case.assign, case.case.artifacts)
            field = next(path for path in output['targets'][0]['fields'] if path.endswith('/evidence'))
            output['targets'][0]['fields'][field][0].pop('digest')
            refusal = case.invoke(page, output)['output_refusal']
            self.assertEqual((refusal['code'], refusal['check']), ('invalid_strategy', 'evidence.shape'), refusal)
            self.assertEqual(refusal['details']['path'], '/targets/0/fields/' + composition._escape_field(field))
            replacement = self.replace_refused_page(case.case.read_records, case.case.artifacts, page,
                                                    case.case.mutate)
            receipt = case.invoke(replacement, self.answer_page(replacement, case.packet,
                                                                lambda assignment: case.final_output(),
                                                                case.case.artifacts))
        self.assertNotIn('output_refusal', receipt, receipt.get('output_refusal'))

    def test_native_pages_authenticate_the_final_assignment(self):
        from test_review_parts_native import NativePartsTests
        from codex_route_fixtures import invoke_codex
        from research_harness.review_parts_native import find_final_assignment
        case = set_up_parts_v2_fixture(self, NativePartsTests())
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
        case = build_native_acceptance_case(self, 'parts_pages_native_command')
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
        records = case.store.snapshot()['records']
        links = check_acceptance_reviews(records, case.artifacts)
        self.assertEqual(next(iter(links.values()))['composite']['id'], record['id'])
        identifier, link = next(iter(records['native_acceptance_review'].items()))
        link['review']['composite']['digest'] = '0' * 64
        with self.assertRaises(ResearchError) as caught:
            check_acceptance_reviews(records, case.artifacts)
        self.assertEqual(caught.exception.code, 'record_acceptance_review_changed')
        self.assertEqual(caught.exception.details, dict(link_id=identifier, acceptance_id=link['acceptance_id']))


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

    def test_final_refuses_incomplete_ranges_and_blank_body_selectors(self):
        row = dict(handle='u1', kind='record', text='abcd', required=True,
                   components=[dict(kind='item', start=0, end=4)])
        packet = {legacy.KEY: dict(kind='final', adapter='problem_result')}
        for selector in (dict(start=1), dict(end=1), dict(body=''), dict(item=True, body='')):
            with self.subTest(selector=selector):
                with self.assertRaises(ResearchError) as caught:
                    parts.validate_planned_output(packet,
                        dict(kind='requests', unit_requests=[dict(unit='u1', **selector)], source_requests=[]),
                        dict(units=[row]), self.plan, self.state, self.route, artifacts=self.artifacts, assignment={})
                self.assertEqual(caught.exception.code, 'invalid_value_review')
        self.assertFalse(self.state['pending_requests'])

    def test_empty_request_without_pending_range_is_refused(self):
        packet = {legacy.KEY: dict(kind='final', adapter='problem_result')}
        with self.assertRaises(ResearchError) as caught:
            parts.validate_planned_output(packet, dict(kind='requests', unit_requests=[], source_requests=[]),
                self.inventory, self.plan, self.state, self.route, artifacts=self.artifacts, assignment={})
        self.assertEqual(caught.exception.code, 'invalid_value_review')

    def test_page_refuses_unhashable_target_and_id_under_shape_contract(self):
        candidate = 'candidate'
        logical = dict(role='slate', subject=dict(kind='problem'), candidates=[dict(id=candidate)])
        packet = {legacy.KEY: dict(protocol=parts.PROTOCOL, kind='final', final_form='page',
            page_plan_digest='f' * 64, logical_packet=parts._put_value(self.artifacts, logical),
            logical_packet_digest=digest(logical), targets=[dict(id=candidate)], page=dict(targets=['t1']))}
        for target in ([], dict(id=[]), 't0', 't01', 't' + '9' * 5000):
            with self.subTest(target=target):
                with self.assertRaises(ResearchError) as caught:
                    parts.validate_planned_output(packet, dict(targets=[dict(target=target, fields={}, sentence='Reviewed.')]),
                        dict(units=[]), self.plan, self.state, self.route, artifacts=self.artifacts, assignment={},
                        expanded=True)
                self.assertEqual(caught.exception.code, 'review_composition_conflict')
                self.assertEqual(caught.exception.contract_check, 'review.shape')
        definitions = parts.list_page_targets(dict(targets=[dict(id='t01'), dict(id='t1')]))
        parts._require_page_target_keys(dict(targets=[dict(target='t01')]), definitions)
        self.assertEqual(composition._resolve_target_id(definitions, 't01'), 't01')
        self.assertEqual(composition._resolve_target_id(definitions, {'id': 't1'}), 't1')

    def test_first_page_after_currency_change_binds_requested_record_before_fold(self):
        handle = 'b' + 'a' * 64
        block = dict(handle=handle, entries=['g1'], characters=10000)
        old = dict(block=handle, summary='The original summary.', refs=['g1'])
        parts._put_value(self.artifacts, old)
        parts._put_value(self.artifacts, block)
        header = dict(protocol=parts.PROTOCOL, kind='final', final_form='page', final_turn=1, turn=2,
                      page_plan_digest='f' * 64, logical_packet_digest='a' * 64,
                      inventory_digest='b' * 64, series_key={}, adapter='problem_result')
        self.state.update(final_currency=parts.read_final_currency(header), final_catalog={handle: old},
                          final_blocks={handle: block}, final_entries={}, final_progress={})
        packet = {legacy.KEY: dict(header, inventory_digest='c' * 64)}
        inventory = dict(units=[])
        output = dict(kind='requests', unit_requests=[dict(unit=handle), dict(unit=handle, entries=True)],
                      source_requests=[])
        delta = parts.validate_planned_output(packet, output, inventory, self.plan, self.state, self.route,
                                              artifacts=self.artifacts, assignment={})
        parts.advance_series_state(self.state, dict(packet=packet, delta=delta, inventory=inventory,
            output=output, receipt=dict(assignment_id='page')))
        self.state['final_catalog'][handle] = dict(old, summary='A regenerated summary.')
        self.state['final_blocks'][handle] = dict(block, entries=['g2'])
        shipments = parts._deliver_requested_units(self.route, inventory, self.state['pending_requests'],
                                                   60000, artifacts=self.artifacts)
        self.assertEqual([row['text'] for row in shipments], [legacy.render_json(old), legacy.render_json(block)])
        packet['review_parts_requests'] = shipments
        packet[legacy.KEY]['pending_ranges'] = dict(total=1)
        parts.validate_planned_output(packet, dict(output, unit_requests=[]), inventory, self.plan,
                                      self.state, self.route, artifacts=self.artifacts, assignment={})

    def test_two_versions_of_a_requested_block_keep_their_delivered_citations(self):
        from research_harness.review_handles import expand_handles
        handle = 'b' + 'a' * 64
        output = dict(kind='requests', unit_requests=[dict(unit=handle)], source_requests=[])
        inventory = dict(protocol=parts.PROTOCOL, units=[])
        logical = dict(role='result', subject=dict(kind='problem'), bar_ids=['bar'])
        records = [dict(block=handle, summary=text, refs=['g1']) for text in ('Original.', 'Regenerated.')]
        references = []
        for record in records:
            references.append(parts._put_value(self.artifacts, record))
            self.state.update(final_catalog={handle: record}, final_blocks={handle: dict(entries=['g1'])})
            self.state['pending_requests'].extend(parts._resolve_requests(inventory, output, self.state))
        self.state['final_catalog'].clear()
        self.state['final_blocks'].clear()
        header = dict(protocol=parts.PROTOCOL, kind='final', adapter='problem_result',
            capacity=self.limits, targets=[], inventory=parts._put_value(self.artifacts, inventory),
            inventory_digest=digest(inventory), state=parts._put_value(self.artifacts, self.state),
            state_digest=digest(self.state), turn=1)
        packet = {legacy.KEY: header, legacy.STATE_KEY: dict(cards=[], combinations=[], findings=[])}
        built = parts.build_final_packet(self.route, self.artifacts, logical, inventory, self.plan,
            self.state, dict(final=self.limits), packet, {}, {}, dict(kind='final'), {}, room=60000)
        deliveries = built['review_parts_requests']
        self.assertEqual([json.loads(row['text']) for row in deliveries], records)
        for row, reference in zip(deliveries, references):
            self.assertEqual(expand_handles(built, dict(ref=row['unit']), artifacts=self.artifacts), reference)
        self.assertNotEqual(deliveries[0]['unit'], deliveries[1]['unit'])
        parts.advance_series_state(self.state, dict(packet=built, inventory=inventory,
            output=output, delta=dict(requests=[]), receipt=dict(assignment_id='page')))
        header.update(state=parts._put_value(self.artifacts, self.state), state_digest=digest(self.state))
        built.pop('review_parts_requests')
        for row, reference in zip(deliveries, references):
            self.assertEqual(expand_handles(built, dict(ref=row['unit']), artifacts=self.artifacts), reference)

    def build_stored_record_final(self, inventory):
        """Build the next final from the bound state, as the next turn and each replacement of it do."""
        header = dict(protocol=parts.PROTOCOL, kind='final', adapter='problem_result',
            capacity=self.limits, targets=[], inventory=parts._put_value(self.artifacts, inventory),
            inventory_digest=digest(inventory), state=parts._put_value(self.artifacts, self.state),
            state_digest=digest(self.state), turn=1)
        packet = {legacy.KEY: header, legacy.STATE_KEY: dict(cards=[], combinations=[], findings=[])}
        logical = dict(role='result', subject=dict(kind='problem'), bar_ids=['bar'])
        return parts.build_final_packet(self.route, self.artifacts, logical, inventory, self.plan,
            self.state, dict(final=self.limits), packet, {}, {}, dict(kind='final'), {}, room=60000)

    def request_part_of_stored_block(self):
        """Ask for 8 characters of a block whose catalog name a currency change then replaces."""
        handle = 'b' + 'a' * 64
        record = dict(block=handle, summary='The original summary of the block.', refs=['g1'])
        reference = parts._put_value(self.artifacts, record)
        self.state.update(final_catalog={handle: record}, final_blocks={handle: dict(entries=['g1'])})
        output = dict(kind='requests', unit_requests=[dict(unit=handle, start=0, end=8)], source_requests=[])
        inventory = dict(protocol=parts.PROTOCOL, units=[])
        self.state['pending_requests'].extend(parts._resolve_requests(inventory, output, self.state))
        self.state['final_catalog'].clear()
        self.state['final_blocks'].clear()
        built = self.build_stored_record_final(inventory)
        delivery, = built['review_parts_requests']
        text = legacy.render_json(record)
        self.assertEqual((delivery['start'], delivery['end'], delivery['text']), (0, 8, text[:8]))
        return inventory, built, delivery['unit'], text, reference

    def test_explicit_range_of_a_stored_block_is_citable_by_its_printed_handle(self):
        from research_harness.review_handles import expand_handles
        inventory, built, alias, text, reference = self.request_part_of_stored_block()

        def assert_citations(packet):
            span = expand_handles(packet, dict(ref=alias, start=1, end=4), artifacts=self.artifacts)
            self.assertEqual(self.artifacts.read(span), text[1:4].encode('utf-8'))
            part = expand_handles(packet, dict(ref=alias, part=1), artifacts=self.artifacts)
            self.assertEqual(self.artifacts.read(part), text[:8].encode('utf-8'))
            self.assertEqual(expand_handles(packet, dict(ref=alias), artifacts=self.artifacts), reference)
            with self.assertRaises(ResearchError) as caught:
                expand_handles(packet, dict(ref=alias, part=2), artifacts=self.artifacts)
            self.assertEqual(caught.exception.code, 'invalid_reference_handle')

        assert_citations(built)
        assert_citations(self.build_stored_record_final(inventory))  # A replacement of the delivery turn.
        answer = dict(stage='result')
        parts.advance_series_state(self.state, dict(packet=built, inventory=inventory, output=answer,
            delta=dict(cards=[], combinations=[], findings=[]), receipt=dict(assignment_id='page')))
        later = self.build_stored_record_final(inventory)
        self.assertNotIn('review_parts_requests', later)
        assert_citations(later)

    def test_request_by_the_printed_handle_is_admitted_on_its_first_delivery(self):
        inventory, built, alias, text, reference = self.request_part_of_stored_block()
        again = dict(kind='requests', unit_requests=[dict(unit=alias, start=0, end=4)], source_requests=[])
        observed = [parts.validate_planned_output(built, again, inventory, self.plan, self.state, self.route,
                                                  artifacts=self.artifacts, assignment={}) for _ in range(2)]
        self.assertEqual(observed[0], observed[1])  # Admission and replay read the same bound state.
        self.assertEqual(observed[0]['requests'], [dict(unit=alias, record=reference, start=0, end=4)])
        parts.advance_series_state(self.state, dict(packet=built, inventory=inventory, output=again,
            delta=observed[0], receipt=dict(assignment_id='page')))
        following = self.build_stored_record_final(inventory)
        self.assertEqual([(row['unit'], row['start'], row['end'], row['text'])
                          for row in following['review_parts_requests']], [(alias, 0, 4, text[:4])])

    def test_small_ranges_share_one_stored_record(self):
        handle = 'b' + 'a' * 64
        record = dict(block=handle, summary='x' * 48000, refs=['g1'])
        reference = parts._put_value(self.artifacts, record)
        self.state['final_catalog'] = {handle: record}
        output = dict(kind='requests', unit_requests=[dict(unit=handle, start=i, end=i+1) for i in range(128)])
        self.state['pending_requests'] = parts._resolve_requests(dict(units=[]), output, self.state)
        self.assertLess(len(legacy.render_json(self.state)), 100000)
        self.assertEqual({scope['record']['sha256'] for scope in self.state['pending_requests']}, {reference['sha256']})
    def test_places_include_large_and_nested_lists_of_duplicate_units(self):
        item = dict(id='only-bar')
        logical = dict(self.logical, bars=[item], own_bars=[item] * 65)
        logical['problem_evidence'] = dict(logical['problem_evidence'], minimum_gaps=[item, item])
        inventory = parts.build_series_inventory(self.route, logical, 'problem_result')
        by_path = {row['path']: row for row in parts._list_final_unit_tables(logical, inventory)[0]}
        self.assertEqual(by_path['/own_bars'], dict(path='/own_bars', entries=65))
        self.assertEqual(by_path['/problem_evidence/minimum_gaps']['units'], by_path['/bars']['units'] * 2)

    def test_escaped_slate_page_prices_the_pointer_map_before_delivery(self):
        candidate = '/' * 180
        logical = dict(role='slate', subject=dict(kind='problem'), bar_ids=['bar'], bars=[dict(id='bar')],
                       candidates=[dict(id=candidate)])
        rule = composition.find_rule('slate', logical)
        targets = composition.list_targets(rule, logical)
        handles = ['t%d' % index for index in range(1, len(targets) + 1)]
        candidate_handle = next(handle for handle, target in zip(handles, targets) if target['id'] == candidate)
        limits = dict(self.limits, target_allowance=1)
        room = min(60000, limits['material_allowance'])
        unit = dict(self.inventory['units'][0], text='X' * room, occurrences=[])
        inventory = dict(units=[unit])
        header = dict(protocol=parts.PROTOCOL, kind='final', adapter='problem_slate', targets=targets,
                      logical_packet=parts._put_value(self.artifacts, logical),
                      logical_packet_digest=digest(logical), inventory_digest=digest(inventory), series_key={})
        turn = dict(kind='page', number=1, targets=[candidate_handle], pool=[], parts=[])
        page_plan = dict(currency=parts.read_final_currency(header), turns=[turn])
        self.state['pending_requests'] = [dict(unit=unit['handle'], start=0, end=room)]
        # Use T3's implementation when merged. The isolated lane emulates its
        # candidate pointer until T3 contributes the field writer.
        def slate_pointers(rule, packet, targets):
            return {handle: ['/value/rubric_potential/' + candidate.replace('/', '~1')] if handle == candidate_handle
                    else [] for handle in targets}

        list_pointers = getattr(composition, 'list_page_pointers', slate_pointers)
        with patch.object(composition, 'list_page_pointers', list_pointers, create=True):
            plan = dict(self.plan, targets=dict(self.plan['targets'], handles=handles))
            inputs, _ = parts.build_final_inputs(self.route, self.artifacts, {legacy.KEY: dict(header)},
                inventory, plan, self.state, limits, room)
            packet = {legacy.KEY: dict(header)}
            following = parts.build_final_packet(self.route, self.artifacts, logical, inventory,
                plan, self.state, dict(final=limits), packet, page_plan, {}, turn, {}, room=room)
        header = following[legacy.KEY]
        envelope = dict(logical, review_parts=header)
        pointer_map = list_pointers(rule, envelope, [candidate_handle])
        pointer_cost = capacity.measure_capacity_value(self.route, pointer_map)
        self.assertGreater(pointer_cost, parts.PAGE_POINTER_CHARACTERS)
        self.assertEqual(header['page'].get('fields', pointer_map), pointer_map)
        deliveries = following['review_parts_requests']
        self.assertLess(deliveries[0]['end'], room)
        self.assertLessEqual(capacity.measure_capacity_value(self.route, deliveries) + pointer_cost,
                             room - parts.FINAL_REFERENCE_CHARACTERS)
        self.assertGreaterEqual(inputs['notes'], parts.FINAL_REFERENCE_CHARACTERS +
                                parts.REQUEST_ROW_RESERVE_CHARACTERS + pointer_cost)
        from research_harness import review_preflight, review_transport
        header['page'].setdefault('fields', pointer_map)
        prompt = 'Return the page answer in the requested form.'
        request = review_transport.request_body(self.route, prompt, following, [])
        preflight = review_preflight.build_report({}, self.artifacts, self.route, 'slate', prompt, following, request,
                                                  capacity_plan=plan, turn_kind='final')
        self.assertTrue(preflight['plan_comparison']['fits'], preflight['plan_comparison'])
        review_preflight.check_request(preflight, request)

    def test_decision_row_skips_oversized_citation_before_later_short_citation(self):
        from research_harness.review_final_pages import build_page_plan
        logical = dict(role='adjudicator', objection=dict(id='o'), subject=dict(kind='problem'))
        short, long = 'u1', 'b' + 'a' * 64
        field = '/reason'
        output = dict(targets=[dict(target='t1', fields={field: 'The objection was assessed.'}, sentence='S' * 290)])
        inventory = dict(units=[])
        header = dict(protocol=parts.PROTOCOL, kind='final', targets=[dict(id='objection')],
                      adapter='problem_adjudicator', logical_packet_digest=digest(logical),
                      inventory_digest=digest(inventory), series_key={})
        header['logical_packet'] = parts._put_value(self.artifacts, logical)
        currency = parts.read_final_currency(header)
        self.state.update(final_currency=currency, final_progress={'1': dict(
            terminal=True, output=output, citation_handles=[[long, short]])})
        header.update(state=parts._put_value(self.artifacts, self.state), state_digest=digest(self.state))
        turn = dict(kind='closing', number=2, rows=[dict(handle='decision:t1')])
        plan = build_page_plan('first', digest(self.plan), digest(self.state), currency,
                               [dict(kind='page', number=1, targets=['t1']), turn])
        reference = dict(artifact=parts._put_value(self.artifacts, plan), digest=digest(plan))
        packet = parts.build_final_packet(self.route, self.artifacts, logical, inventory, self.plan, self.state,
            dict(final=self.limits), {legacy.KEY: header}, plan, reference, turn, {}, room=60000)
        measure = lambda value: capacity.measure_capacity_value(self.route, value) + 1
        row, = packet[legacy.KEY]['closing']['rows']
        self.assertGreater(measure(row[:3] + [[long]]), capacity.DECISION_ROW_CHARACTERS)
        self.assertEqual(row[3], [short])
        assignment = dict(id='closing', route_id='route', role='adjudicator', packet=parts._put_value(self.artifacts, packet))
        attempt = dict(id='attempt', assignment_id='closing', output=parts._put_value(self.artifacts, {}))
        records = dict(review_assignment={'closing': assignment}, review_route={'route': self.route},
                       review_output_refusal={'attempt': dict(details=dict(composed_output=True))})
        refusal = parts.describe_composed_refusal(records, self.artifacts, attempt, 'review.value', field)
        self.assertEqual(refusal['decision_row'], row)

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
        self.assertEqual(later['notes'], 6000 + 9000 + 300 * self.limits['target_allowance'])

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
        self.assertEqual(parts._list_requestable_records(self.state)['f1-1'], raw)
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
        self.assertEqual(parts._list_requestable_records(state)[fragment['id']], fragment)

    def test_final_block_and_fragment_requests_are_delivered(self):
        block = dict(handle='b1', entries=['g1@0-10'], characters=1000)
        fragment = dict(id='g1@0-10', entry='g1', start=0, end=10, text='0123456789')
        refs = [parts._put_value(self.artifacts, entry) for entry in (block, fragment)]
        self.state.update(final_catalog={'b1': dict(block='b1', summary='Retained.', refs=block['entries'])},
                          final_blocks={'b1': block})
        header = dict(self.packet[legacy.KEY], protocol=parts.PROTOCOL, kind='final', final_form='page',
                      final_turn=1, turn=2, page_plan_digest='a' * 64, logical_packet_digest='b' * 64,
                      inventory_digest=digest(self.inventory), series_key=dict(role='result'),
                      adapter='problem_result', capacity=self.limits)
        packet = {legacy.KEY: header, legacy.STATE_KEY: dict(cards=[fragment], combinations=[], findings=[])}
        self.state['final_currency'] = parts.read_final_currency(header)
        self.state.update(final_entries={}, final_progress={})
        output = dict(kind='requests', unit_requests=[dict(unit='b1', entries=True),
                      dict(unit=fragment['id'])], source_requests=[])
        delta = parts.validate_planned_output(packet, output, self.inventory, self.plan, self.state,
                                             self.route, artifacts=self.artifacts, assignment={})
        parts.advance_series_state(self.state, dict(packet=packet, inventory=self.inventory, output=output,
            delta=delta, receipt=dict(assignment_id='page', attempt_id='attempt')))
        turn = dict(kind='page', number=1, targets=['t1'], pool=[], parts=[])
        page_plan = dict(currency=self.state['final_currency'], turns=[turn])
        packet = {legacy.KEY: dict(header)}
        following = parts.build_final_packet(self.route, self.artifacts, self.logical, self.inventory, self.plan,
            self.state, dict(final=self.limits), packet, page_plan, {}, turn, {}, room=60000)
        deliveries = following['review_parts_requests']
        self.assertEqual([row['descriptor']['digest'] for row in deliveries], [ref['sha256'] for ref in refs])
        self.assertEqual([json.loads(row['text']) for row in deliveries], [block, fragment])
        for index, delivery in enumerate(deliveries):
            replayed = parts._find_stored_entry_unit(self.artifacts, self.state, delivery['unit'], index, {})
            self.assertEqual(replayed['text'], delivery['text'])

    def test_capacity_keeps_fixed_room_for_final_references_and_page_fields(self):
        self.limits['target_allowance'] = 2
        self.state['pending_requests'] = [dict(unit='u1', start=0, end=20)]
        inputs, _ = self.inputs()
        self.assertEqual(inputs['notes'], 6000 + 9000 + 300 * 2)
        self.assertEqual(inputs['closing_room'], 60000 - 9000 - 6000)
        self.state['pending_requests'].clear()
        without, _ = self.inputs()
        self.assertEqual(without['notes'], inputs['notes'])
        self.assertEqual(without['closing_room'], inputs['closing_room'])

    def build_reference_final(self, logical, adapter, inventory=None):
        inventory = inventory or dict(self.inventory, units=[])
        self.state['cursor'] = dict(unit_index=sum(row['required'] for row in inventory['units']), unit_offset=0)
        limits = {kind: capacity.derive_plan_task_limits(self.plan, kind) for kind in legacy.KINDS}
        packet = parts.plan_series_turn(self.route, self.artifacts, logical, inventory, self.plan, self.state,
            binding=dict(adapter=adapter, _targets=[]), limits_by_kind=limits)
        return packet, limits

    def test_each_adapter_uses_complete_packet_binding_pointers(self):
        native = dict(kind='recovery', subject_digest='subject', reviewer=dict(id='reviewer'), scientific_context={})
        result = dict(role='result', subject=dict(kind='problem'), bar_ids=['bar'])
        cases = [
            ('problem_slate', dict(result, role='slate'), ['/stage', '/value/bar_ids']),
            ('problem_adjudicator', dict(role='adjudicator', subject=dict(kind='problem'), objection=dict(id='o')),
             ['/objection_id']),
            ('native_math', dict(role='native_math', native=native), ['/reviewer', '/schema_version', '/subject_digest']),
            ('context_repair', dict(result, context_repair=dict(assignment_id='prior')),
             ['/reassessment/assignment_id', '/stage', '/value/bar_ids']),
            ('study_verification', dict(role='verification', paper=dict(kind='verification')), []),
            ('study_readiness', dict(role='result', dossier={}, readiness=dict(kind='readiness'),
                support_contract=dict(candidate_digest='candidate'), assessor=dict(id='reviewer')),
             ['/stage', '/support/assessor', '/support/candidate_digest', '/value/bar_ids']),
        ]
        for adapter, logical, pointers in cases:
            with self.subTest(adapter=adapter):
                packet, _ = self.build_reference_final(logical, adapter)
                self.assertEqual(sorted(packet[legacy.KEY]['binding_values']), pointers)

    def test_adjudicator_places_name_evidence_and_omit_long_unit_lists(self):
        logical = dict(role='adjudicator', subject=dict(kind='problem'), objection=dict(id='objection'),
                       original_evidence=[dict(id='original')], version_evidence=[dict(id='version')] * 65)
        units = [dict(handle='u' + str(index), kind='record', required=True, digest=digest(logical[key][0]),
                      occurrences=[dict(path=[key], index=0)])
                 for index, key in enumerate(('original_evidence', 'version_evidence'), 1)]
        packet, _ = self.build_reference_final(logical, 'problem_adjudicator', dict(self.inventory, units=units))
        self.assertEqual(packet[legacy.KEY]['places'], dict(total=2, rows=[
            dict(path='/original_evidence', entries=1, units=['u1']),
            dict(path='/version_evidence', entries=65)]))

    def test_adapters_without_source_capture_refuse_source_requests(self):
        capture = dict(handle='u1', kind='source', required=False, text='Original.',
                       source=dict(kind='abstract', identity=dict(version_id='version')))
        inventory = dict(self.inventory, units=[capture])
        output = dict(kind='requests', unit_requests=[], source_requests=[
            dict(kind='source', id='request', depth='abstract', version_id='version')])
        for adapter in ('native_math', 'problem_adjudicator', 'study_verification'):
            with self.subTest(adapter=adapter):
                packet = {legacy.KEY: dict(kind='final', adapter=adapter)}
                with self.assertRaises(ResearchError) as caught:
                    parts.validate_planned_output(packet, output, inventory, self.plan, self.state, self.route,
                                                 artifacts=self.artifacts, assignment={})
                self.assertEqual(caught.exception.code, 'invalid_value_review')

    def test_reference_tables_are_prefixes_within_the_final_material_bound(self):
        logical = dict(self.logical, bar_ids=['b' * 1800])
        item = dict(id='original')
        for index in range(50):
            logical['long-list-name-' + str(index)] = [item]
        text = 'x' * (self.limits['material_allowance'] + 1)
        original = dict(handle='u1', kind='record', required=True, digest=digest(item), text=text, occurrences=[], descriptor=dict(handle='u1'))
        sources = [dict(handle='source-' + str(index), kind='source', required=False, text='A capture.',
                        source=dict(kind='abstract', identity=dict(version_id='version-' + str(index))))
                   for index in range(50)]
        inventory = dict(self.inventory, units=[original] + sources)
        self.state['material_state']['arguments'] = {'m' + str(index): dict(title='A' * 120, parts=[{}], complete=True)
                                                     for index in range(1, 21)}
        self.state['pending_requests'] = [dict(unit='u1', start=0, end=len(text)) for _ in range(40)]
        packet, limits = self.build_reference_final(logical, 'problem_result', inventory)
        material = capacity.measure_capacity_value
        bounds = dict(places=3000, sources=2000, arguments=1000, pending_ranges=1000)
        for form in ('single', 'page', 'closing'):
            with self.subTest(form=form):
                if form == 'single':
                    following = packet
                else:
                    header = dict(packet[legacy.KEY], targets=self.packet[legacy.KEY]['targets'])
                    turn = dict(kind=form, number=1, targets=['t1'], rows=[], pool=[], parts=[])
                    page_plan = dict(currency={}, turns=[turn])
                    following = parts.build_final_packet(self.route, self.artifacts, logical, inventory,
                        self.plan, self.state, limits, {legacy.KEY: header}, page_plan, {}, turn, {}, room=60000)
                header = following[legacy.KEY]
                used = sum(material(self.route, following[key])
                           for key in (legacy.STATE_KEY, 'review_parts_requests'))
                for name, bound in bounds.items():
                    table = header[name]
                    characters = material(self.route, table)
                    self.assertGreater(table['total'], len(table['rows']))
                    self.assertLessEqual(characters, bound)
                    used += characters
                if form == 'single':
                    binding_characters = material(self.route, header['binding_values'])
                    self.assertLessEqual(binding_characters, 2000)
                    used += binding_characters
                if form == 'page' and 'fields' in header['page']:
                    pointers = material(self.route, header['page']['fields'])
                    self.assertLessEqual(pointers, 300 * limits['final']['target_allowance'])
                    used += pointers
                self.assertLessEqual(used, header['capacity']['material_allowance'])

    def test_single_final_refuses_binding_values_that_cannot_fit_whole(self):
        logical = dict(self.logical, bar_ids=['b' * 2000])
        inventory = dict(self.inventory, units=[])
        packet, limits = self.build_reference_final(logical, 'problem_result', inventory)
        with self.assertRaises(ResearchError) as caught:
            parts.prepare_series_final({}, self.artifacts, None, self.route, logical, inventory,
                                       self.plan, self.state, limits, packet)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        self.assertGreater(caught.exception.details['characters'], 2000)

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
