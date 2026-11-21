"""Authenticate a paged final from ordinary instrumented observations."""

import copy
import importlib.util
import json
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.operations import immutable_record
from research_harness.review_route_capacity import CAPACITY_PLAN_PROTOCOL
import test_research_review_protocol as fixtures


FAMILY = 'fixture-final-pages-v1'


def list_fixture_turns(records, artifacts, assignment):
    turns = copy.deepcopy(assignment.get('fixture_final'))
    if turns is None:
        return None
    # A family resolves replacements from immutable links, without rewriting
    # the original assignments' view of the final.
    for turn in turns['pages'] + [turns['closing']] + turns['condensing']:
        while True:
            replacements = [row for row in records['review_assignment'].values()
                            if row.get('fixture_replaces') == turn['assignment_id']]
            if not replacements:
                break
            turn['assignment_id'] = max(replacements, key=lambda row: row['recorded_revision'])['id']
    return turns


def read_fixture_currency(records, artifacts, assignment):
    return assignment['fixture_currency']


def validate_fixture_output(records, artifacts, assignment, packet, output):
    if output.get('summary') == 'refused':
        raise ResearchError('invalid_fixture_final', 'The fixture final is refused.')


def compose_fixture_output(packet, pages, closing):
    return dict(items=[item for page in pages for item in page['output']['items']], summary=closing['summary'])


class CompositeFinalTests(LiteratureCase):
    seed = fixtures.ReviewProtocolTests.seed
    records = fixtures.ReviewProtocolTests.records
    route = fixtures.ReviewProtocolTests.route
    protocol = fixtures.ReviewProtocolTests.protocol
    run_probe = fixtures.ReviewProtocolTests.run_probe
    response = fixtures.ReviewProtocolTests.response
    assign = fixtures.ReviewProtocolTests.assign
    invoke = fixtures.ReviewProtocolTests.invoke

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(importlib.util.find_spec('research_harness.review_composite'),
                             'Composite finals are not implemented')
        from research_harness import review_composite
        self.composites = review_composite
        self.route()
        self.run_probe()
        self.turns = dict(chain='page-1', packet={},
            pages=[dict(assignment_id='page-%d' % n, targets=['t%d' % n]) for n in range(1, 4)],
            closing=dict(assignment_id='closing', targets=[]),
            condensing=[dict(assignment_id='condense', targets=['t1', 't2', 't3'])])
        self.plan = dict(targets=dict(handles=['t1', 't2', 't3'], digest=digest(['t1', 't2', 't3'])))
        plan_ref = protocol._put_json(self.artifacts, self.plan)
        self.currency = dict(packet_digest=digest({'native': 'fixture'}), inventory_digest=digest(['unit-1']))
        for identifier in ['page-1', 'page-2', 'page-3', 'condense', 'closing']:
            self.assign(identifier=identifier)
            assignment = self.records()['review_assignment'][identifier]
            assignment.update(fixture_final=copy.deepcopy(self.turns), fixture_currency=copy.deepcopy(self.currency),
                capacity_plan=dict(protocol=CAPACITY_PLAN_PROTOCOL, artifact=plan_ref, digest=digest(self.plan)))
            self.seed('review_assignment', identifier, assignment)
            output = (dict(items=[identifier]) if identifier.startswith('page-') else
                      dict(summary='Recorded final 雪.') if identifier == 'closing' else dict(notes='Condensed rows.'))
            self.invoke(output, identifier='attempt-' + identifier, assignment=identifier)
        self.records_value = self.records()
        row = dict(list_turns=list_fixture_turns, read_currency=read_fixture_currency,
                   validate_output=validate_fixture_output)
        families = patch.dict(self.composites.FINAL_FAMILIES, {FAMILY: row})
        families.start()
        self.addCleanup(families.stop)
        rule = patch.object(self.composites, 'find_composition_rule',
            return_value=dict(id='exactory-final-composition-v1/fixture', compose=compose_fixture_output))
        rule.start()
        self.addCleanup(rule.stop)
        self.closing = self.records_value['review_assignment']['closing']
        self.composite, self.artifact = self.composites.build_composite(self.records_value, self.artifacts, self.closing)
        self.persist_composite()

    def persist_composite(self):
        reference, content = self.artifact
        self.assertEqual(self.artifacts.put(content, reference['media_type']), reference)
        self.records_value.setdefault('review_composite', {})[self.composite['id']] = self.composite

    def assert_refused(self, condition):
        with self.assertRaises(ResearchError) as caught:
            self.composites.authenticate_composite(self.records_value, self.artifacts, self.composite)
        self.assertEqual(caught.exception.code, 'review_composite_unverified')
        self.assertEqual(caught.exception.details['condition'], condition)

    def test_three_pages_record_authenticate_and_read_final(self):
        # T4 of 20261112235500-openness-chain-final-turn-over-route-limit-after-57-turns.
        self.assertEqual(set(self.composite), {'id', 'protocol', 'chain', 'composition', 'pages', 'closing', 'currency', 'output'})
        self.assertEqual(self.composite['protocol'], 'exactory-review-composite-final-v1')
        self.assertEqual(self.composite['chain'], dict(assignment_id='page-1', plan_digest=digest(self.plan)))
        self.assertEqual(len(self.composite['pages']), 3)
        self.assertEqual(self.composite['closing']['condensing'][0]['assignment_id'], 'condense')
        before = copy.deepcopy(self.records_value)
        output = self.composites.authenticate_composite(self.records_value, self.artifacts, self.composite)
        self.assertEqual(output, dict(items=['page-1', 'page-2', 'page-3'], summary='Recorded final 雪.'))
        self.assertEqual(self.composites.read_final_output(self.records_value, self.artifacts, self.closing),
                         (output, self.composite))
        self.assertEqual(self.records_value, before)
        change = immutable_record(self.records_value, 'review_composite', self.composite['id'], self.composite)
        self.store.mutate('fixture.composite', {}, lambda tx: tx.put(*change),
            expected_revision=self.store.revision, request_id='persist-composite')
        self.assertEqual(self.records()['review_composite'][self.composite['id']], self.composite)

    def test_condition_1_refuses_unauthenticated_page_closing_and_condensing(self):
        for identifier in ('page-2', 'closing', 'condense'):
            with self.subTest(turn=identifier):
                attempt = self.records_value['review_attempt']['attempt-' + identifier]
                original = attempt['packet_digest']
                attempt['packet_digest'] = 'changed'
                self.assert_refused(1)
                attempt['packet_digest'] = original

    def test_condition_1_refuses_unselected_attempt(self):
        attempt = self.records_value['review_attempt']['attempt-page-2']
        other = dict(attempt, id='earlier-attempt', recorded_revision=attempt['recorded_revision'] - 1)
        self.records_value['review_attempt'][other['id']] = other
        self.assertTrue(protocol.authenticate_attempt(self.records_value, self.artifacts,
            self.records_value['review_assignment']['page-2'], attempt))
        self.assert_refused(1)

    def test_condition_2_refuses_route_reviewer_plan_currency_and_chain(self):
        assignment = self.records_value['review_assignment']['page-2']
        original = copy.deepcopy(assignment)
        self.records_value['review_route']['other-route'] = self.records_value['review_route']['route']
        mutations = [dict(route_id='other-route'), dict(reviewer_id='other-reviewer'),
            dict(capacity_plan=dict(assignment['capacity_plan'], digest='different-plan')),
            dict(fixture_currency=dict(self.currency, inventory_digest='different-inventory')),
            dict(fixture_currency=dict(self.currency, packet_digest='different-packet')),
            dict(fixture_currency=dict(self.currency, series_key='other-series')),
            dict(fixture_final=dict(self.turns, chain='other-chain'))]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                assignment.update(mutation)
                self.assert_refused(2)
                assignment.clear()
                assignment.update(copy.deepcopy(original))

    def test_condition_3_refuses_overlapping_targets(self):
        self.composite['pages'][1]['targets'].append('t1')
        for assignment in self.records_value['review_assignment'].values():
            assignment['fixture_final']['pages'][1]['targets'].append('t1')
        self.assert_refused(3)

    def test_condition_3_refuses_missing_plan_target(self):
        self.composite['pages'][2]['targets'] = []
        for assignment in self.records_value['review_assignment'].values():
            assignment['fixture_final']['pages'][2]['targets'] = []
        self.assert_refused(3)

    def test_condition_4_refuses_changed_output_character_with_valid_artifact(self):
        changed = json.loads(self.artifact[1])
        changed['summary'] += '!'
        reference = protocol._put_json(self.artifacts, changed)
        self.composite['output'] = dict(artifact=reference, digest=digest(changed))
        self.assert_refused(4)

    def test_condition_4_refuses_json_types_that_compare_equal_in_python(self):
        def compose_boolean(packet, pages, closing):
            return dict(compose_fixture_output(packet, pages, closing), flag=True)
        with patch.object(self.composites, 'find_composition_rule', return_value=dict(
                id='exactory-final-composition-v1/fixture', compose=compose_boolean)):
            composite, artifact = self.composites.build_composite(self.records_value, self.artifacts, self.closing)
            altered = json.loads(artifact[1])
            altered['flag'] = 1
            composite['output']['artifact'] = protocol._put_json(self.artifacts, altered)
            with self.assertRaises(ResearchError) as caught:
                self.composites.authenticate_composite(self.records_value, self.artifacts, composite)
            self.assertEqual(caught.exception.details['condition'], 4)

    def test_condition_4_refuses_wrong_rule_and_output_digest(self):
        original = copy.deepcopy(self.composite)
        for mutation in (dict(composition='exactory-final-composition-v1/other'),
                         dict(output=dict(self.composite['output'], digest='different-output'))):
            with self.subTest(mutation=mutation):
                self.composite.update(mutation)
                self.assert_refused(4)
                self.composite.clear()
                self.composite.update(copy.deepcopy(original))

    def test_condition_5_preserves_role_validator_refusal(self):
        def refuse_output(records, artifacts, assignment, packet, output):
            raise ResearchError('invalid_fixture_final', 'The fixture final is refused.', {'path': '/summary'})

        with patch.dict(self.composites.FINAL_FAMILIES[FAMILY], validate_output=refuse_output):
            with self.assertRaises(ResearchError) as caught:
                self.composites.authenticate_composite(self.records_value, self.artifacts, self.composite)
            self.assertEqual(caught.exception.code, 'invalid_fixture_final')
            self.assertEqual(caught.exception.details, {'path': '/summary'})
            attempt = self.records_value['review_attempt']['attempt-closing']
            with self.assertRaises(ResearchError) as prepared:
                self.composites.prepare_composite(self.records_value, self.artifacts, self.closing, attempt,
                                                  dict(summary='Recorded final 雪.'))
            self.assertEqual(prepared.exception.code, 'invalid_fixture_final')

    def test_prepare_closing_writes_nothing_and_uses_selected_replacement(self):
        replacement = copy.deepcopy(self.closing)
        replacement['id'] = 'closing-replacement'
        replacement['fixture_replaces'] = 'closing'
        self.seed('review_assignment', replacement['id'], replacement)
        self.invoke(dict(summary='Replacement final.'), identifier='replacement-attempt', assignment=replacement['id'])
        records = self.records()
        attempt = records['review_attempt'].pop('replacement-attempt')
        before = copy.deepcopy(records)
        composite, artifact = self.composites.prepare_composite(records, self.artifacts, replacement, attempt,
                                                               dict(summary='Replacement final.'))
        self.assertEqual(records, before)
        self.assertEqual(composite['closing']['assignment_id'], replacement['id'])
        self.assertEqual(composite['closing']['attempt_id'], attempt['id'])
        records['review_attempt'][attempt['id']] = attempt
        records['review_composite'] = {composite['id']: composite}
        self.artifacts.put(artifact[1], artifact[0]['media_type'])
        self.assertEqual(self.composites.read_final_output(records, self.artifacts, replacement),
                         (dict(items=['page-1', 'page-2', 'page-3'], summary='Replacement final.'), composite))

    def test_condition_1_refuses_page_output_digest_and_stale_closing(self):
        self.composite['pages'][0]['output_digest'] = 'changed'
        self.assert_refused(1)
        self.composite['pages'][0]['output_digest'] = digest(dict(items=['page-1']))
        self.composite['closing']['attempt_id'] = 'missing-attempt'
        with self.assertRaises(ResearchError) as caught:
            self.composites.read_final_output(self.records_value, self.artifacts, self.closing)
        self.assertEqual(caught.exception.details['condition'], 1)

    def test_condition_3_refuses_duplicate_target_within_one_page(self):
        self.composite['pages'][0]['targets'].append('t1')
        self.assert_refused(3)

    def test_condition_4_replays_handle_expansion_and_refuses_missing_artifact(self):
        reference = self.artifacts.put(b'A separately recorded argument.', 'text/plain')
        for assignment in self.records_value['review_assignment'].values():
            assignment['fixture_final']['packet']['handle_table'] = {'m1': dict(reference=reference)}
        def compose_with_handle(packet, pages, closing):
            return dict(compose_fixture_output(packet, pages, closing), evidence=[dict(ref='m1')])
        with patch.object(self.composites, 'find_composition_rule', return_value=dict(
                id='exactory-final-composition-v1/fixture', compose=compose_with_handle)):
            composite, artifact = self.composites.build_composite(self.records_value, self.artifacts, self.closing)
            self.artifacts.put(artifact[1], artifact[0]['media_type'])
            self.assertEqual(self.composites.authenticate_composite(self.records_value, self.artifacts, composite)['evidence'],
                             [reference])
            composite['output']['artifact']['sha256'] = '0' * 64
            with self.assertRaises(ResearchError) as caught:
                self.composites.authenticate_composite(self.records_value, self.artifacts, composite)
            self.assertEqual(caught.exception.details['condition'], 4)

    def record_handled_page(self):
        reference = self.artifacts.put(b'A retained page argument.', 'text/plain')
        build_packet = protocol.build_packet
        def build_handle_packet(*args, **kwargs):
            packet = build_packet(*args, **kwargs)
            packet['handle_table'] = {'m1': dict(reference=reference)}
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet
        with patch.object(protocol, 'build_packet', side_effect=build_handle_packet):
            self.assign(identifier='handled-page')
        assignment = self.records()['review_assignment']['handled-page']
        assignment.update(fixture_final=copy.deepcopy(self.turns), fixture_currency=copy.deepcopy(self.currency),
                          capacity_plan=self.closing['capacity_plan'], fixture_replaces='page-2')
        self.seed('review_assignment', assignment['id'], assignment)
        self.invoke(dict(items=[dict(ref='m1')]), identifier='handled-attempt', assignment=assignment['id'])
        records = self.records()
        attempt = records['review_attempt']['handled-attempt']
        return records, reference, attempt

    def test_page_attempt_replays_its_real_handle_expansion_certificate(self):
        records, reference, attempt = self.record_handled_page()
        self.assertEqual(attempt['handle_expansion']['references'], 1)
        composite, artifact = self.composites.build_composite(records, self.artifacts, records['review_assignment']['closing'])
        self.artifacts.put(artifact[1], artifact[0]['media_type'])
        output = self.composites.authenticate_composite(records, self.artifacts, composite)
        self.assertEqual(output['items'], ['page-1', reference, 'page-3'])
        attempt['handle_expansion']['output_sha256'] = 'changed'
        with self.assertRaises(ResearchError) as caught:
            self.composites.authenticate_composite(records, self.artifacts, composite)
        self.assertEqual(caught.exception.details['condition'], 1)

    def test_composer_receives_ordered_targets_and_expanded_page_outputs(self):
        records, reference, _ = self.record_handled_page()
        expected = [dict(targets=['t1'], output=dict(items=['page-1'])),
                    dict(targets=['t2'], output=dict(items=[reference])),
                    dict(targets=['t3'], output=dict(items=['page-3']))]
        received = []

        def compose(packet, page_outputs, closing_output):
            self.assertEqual(page_outputs, expected)
            self.assertEqual(closing_output, dict(summary='Recorded final 雪.'))
            received.append(copy.deepcopy(page_outputs))
            return dict(items=[item for page in page_outputs for item in page['output']['items']],
                        summary=closing_output['summary'])

        def find_rule(role, packet):
            return dict(id='exactory-final-composition-v1/fixture', compose=compose)

        with patch.object(self.composites, 'find_composition_rule', side_effect=find_rule):
            closing = records['review_assignment']['closing']
            composite, artifact = self.composites.build_composite(records, self.artifacts, closing)
            self.artifacts.put(artifact[1], artifact[0]['media_type'])
            output = self.composites.authenticate_composite(records, self.artifacts, composite)
            self.assertEqual(output['items'], ['page-1', reference, 'page-3'])
            self.assertEqual(composite['composition'], 'exactory-final-composition-v1/fixture')
            prepared, _ = self.composites.prepare_composite(records, self.artifacts, closing,
                records['review_attempt']['attempt-closing'], dict(summary='Recorded final 雪.'))
            self.assertEqual(prepared, composite)
        self.assertEqual(received, [expected, expected, expected])

    def test_condition_2_refuses_mixed_page_plan_currencies_and_accepts_successor(self):
        old_turns = copy.deepcopy(self.turns)
        old_page_plan = protocol._put_json(self.artifacts, dict(currency=self.currency, turns=old_turns))
        for identifier in ['page-1', 'page-2', 'page-3', 'condense', 'closing']:
            assignment = self.records()['review_assignment'][identifier]
            assignment['fixture_page_plan'] = old_page_plan
            self.seed('review_assignment', identifier, assignment)
        old_records = self.records()
        old_assignment = old_records['review_assignment']['page-1']
        old_attempt = old_records['review_attempt']['attempt-page-1']
        old_output = self.artifacts.read(old_attempt['output'])
        old_digests = (digest(old_assignment), digest(old_attempt), old_attempt['output']['sha256'])

        currency = dict(self.currency, packet_digest=digest({'native': 'later-fixture'}),
                        inventory_digest=digest(['unit-1', 'late-unit']))
        turns = dict(chain='page-1', packet={},
            pages=[dict(assignment_id='later-page-%d' % n, targets=['t%d' % n]) for n in range(1, 4)],
            closing=dict(assignment_id='later-closing', targets=[]),
            condensing=[dict(assignment_id='later-condense', targets=['t1', 't2', 't3'])])
        page_plan = protocol._put_json(self.artifacts, dict(currency=currency, turns=turns,
            replaces=old_page_plan, cause='inventory'))
        for identifier in ['later-page-1', 'later-page-2', 'later-page-3', 'later-condense', 'later-closing']:
            self.assign(identifier=identifier)
            assignment = self.records()['review_assignment'][identifier]
            assignment.update(fixture_page_plan=page_plan, capacity_plan=old_assignment['capacity_plan'])
            self.seed('review_assignment', identifier, assignment)
            output = (dict(items=[identifier]) if identifier.startswith('later-page-') else
                      dict(summary='Later final.') if identifier == 'later-closing' else dict(notes='Later rows.'))
            self.invoke(output, identifier='attempt-' + identifier, assignment=identifier)

        def list_plan_turns(records, artifacts, assignment):
            if 'fixture_page_plan' not in assignment:
                return None
            active = records['fixture_final_plan']['page-1']['page_plan']
            return json.loads(artifacts.read(active))['turns']

        def read_plan_currency(records, artifacts, assignment):
            return json.loads(artifacts.read(assignment['fixture_page_plan']))['currency']

        def record_composite(records, reference, content, composite):
            def apply(tx):
                tx.put(*immutable_record(records, 'review_composite', composite['id'], composite))
            self.assertEqual(self.artifacts.put(content, reference['media_type']), reference)
            self.sequence += 1
            self.store.mutate('fixture.composite', {}, apply, expected_revision=self.store.revision,
                              request_id='successor-composite-' + str(self.sequence))

        self.seed('fixture_final_plan', 'page-1', dict(page_plan=page_plan))
        records = self.records()
        row = self.composites.FINAL_FAMILIES[FAMILY]
        with patch.dict(row, list_turns=list_plan_turns, read_currency=read_plan_currency):
            closing = records['review_assignment']['later-closing']
            composite, artifact = self.composites.build_composite(records, self.artifacts, closing)
            mixed = copy.deepcopy(composite)
            mixed['pages'][0] = copy.deepcopy(self.composite['pages'][0])
            with self.assertRaises(ResearchError) as caught:
                self.composites.authenticate_composite(records, self.artifacts, mixed)
            self.assertEqual(caught.exception.code, 'review_composite_unverified')
            self.assertEqual(caught.exception.details['condition'], 2)

            record_composite(records, artifact[0], artifact[1], composite)
            records = self.records()
            output = self.composites.authenticate_composite(records, self.artifacts, composite)
            self.assertEqual(output, dict(items=['later-page-1', 'later-page-2', 'later-page-3'], summary='Later final.'))
            self.assertEqual(composite['currency'], currency)
            self.assertEqual(self.composites.read_final_output(records, self.artifacts, closing), (output, composite))

        self.assertEqual([page['assignment_id'] for page in composite['pages']],
                         ['later-page-1', 'later-page-2', 'later-page-3'])
        retained = records['review_assignment']['page-1'], records['review_attempt']['attempt-page-1']
        self.assertEqual((digest(retained[0]), digest(retained[1]), retained[1]['output']['sha256']), old_digests)
        self.assertEqual(self.artifacts.read(retained[1]['output']), old_output)

    def test_build_and_prepare_do_not_publish_artifacts(self):
        before = {str(path) for path in self.root.rglob('*')}
        def compose_new_output(packet, pages, closing):
            return dict(compose_fixture_output(packet, pages, closing), extra='Not published.')
        with patch.object(self.composites, 'find_composition_rule', return_value=dict(
                id='exactory-final-composition-v1/fixture', compose=compose_new_output)):
            composite, artifact = self.composites.build_composite(self.records_value, self.artifacts, self.closing)
            self.composites.prepare_composite(self.records_value, self.artifacts, self.closing,
                self.records_value['review_attempt']['attempt-closing'], dict(summary='Recorded final 雪.'))
        self.assertEqual({str(path) for path in self.root.rglob('*')}, before)
        self.assertFalse((self.root / artifact[0]['path']).exists())
        self.assertEqual(digest(json.loads(artifact[1])), composite['output']['digest'])

    def test_prepare_preserves_the_store_snapshot_metadata_for_family_callbacks(self):
        records = self.store._read_preparation().records
        assignment = records['review_assignment']['closing']
        expected = records.read_creation_revision('review_assignment', 'closing')
        def validate_snapshot(records, artifacts, assignment, packet, output):
            self.assertEqual(records.read_creation_revision('review_assignment', 'closing'), expected)
            validate_fixture_output(records, artifacts, assignment, packet, output)
        with patch.dict(self.composites.FINAL_FAMILIES[FAMILY], validate_output=validate_snapshot):
            composite, _ = self.composites.prepare_composite(records, self.artifacts, assignment,
                records['review_attempt']['attempt-closing'], dict(summary='Recorded final 雪.'))
        self.assertEqual(composite['closing']['assignment_id'], 'closing')

    def test_prepare_refuses_a_closing_output_different_from_the_pending_attempt(self):
        with self.assertRaises(ResearchError) as caught:
            self.composites.prepare_composite(self.records_value, self.artifacts, self.closing,
                self.records_value['review_attempt']['attempt-closing'], dict(summary='Substitution.'))
        self.assertEqual(caught.exception.details['condition'], 1)

    def test_condition_2_refuses_unknown_family_and_record_members(self):
        with patch.dict(self.composites.FINAL_FAMILIES, {}, clear=True):
            self.assert_refused(2)
        self.composite['unexpected'] = 'extra'
        self.assert_refused(2)

    def test_condition_1_refuses_a_paged_closing_without_its_composite(self):
        self.records_value.pop('review_composite')
        with self.assertRaises(ResearchError) as caught:
            self.composites.read_final_output(self.records_value, self.artifacts, self.closing)
        self.assertEqual(caught.exception.details['condition'], 1)

    def test_refusal_has_a_reviewed_stored_state_registry_row(self):
        from research_harness import stop_classes
        row = stop_classes.STOP_REGISTRY.get('review_composite_unverified')
        self.assertIsNotNone(row)
        self.assertEqual(row['group'], 'stored_state')

    def test_single_response_keeps_ordinary_output_and_bytes(self):
        records = self.records()
        attempt, output = protocol.read_assignment_output(records, self.artifacts, self.closing['id'])
        before = self.artifacts.read(attempt['output'])
        with patch.dict(self.composites.FINAL_FAMILIES, {}, clear=True):
            self.assertEqual(self.composites.read_final_output(records, self.artifacts, self.closing), (output, None))
        self.assertEqual(self.artifacts.read(attempt['output']), before)


class FinalRecordingTests(unittest.TestCase):
    def prepare_fixture(self, cls):
        case = cls()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def check_record_binding(self, case, command, payload, kind, identifier, *, output=None):
        """Compare complete legacy record bytes with the optional composite sidecar."""
        import shutil
        from research_harness import review_composite
        from research_harness.storage import Store, _canonical
        other_root = case.root.parent / 'composite-copy'
        shutil.copytree(case.root, other_root)
        other = Store(other_root)
        revision = case.store.revision
        legacy = command(case.store, payload, expected_revision=revision, request_id='record-final')['result']
        assignment_id = payload.get('assignment_id') or payload['assessment']['assignment_id']
        if output is None:
            _, output = protocol.read_assignment_output(other.snapshot()['records'], other.build_artifact_store(), assignment_id)
        composite = dict(id='fixture-composite', output=dict(digest=digest(output)))
        read_state = protocol.assignment_state
        def read_closing_state(records, artifacts, identifier):
            state = read_state(records, artifacts, identifier)
            if identifier == assignment_id:
                partial = {'closing': 'Only the closing fields were returned.'}
                return dict(state, output=partial, output_digest=digest(partial))
            return state
        with patch.object(review_composite, 'read_final_output', return_value=(output, composite)) as reader, \
                patch.object(protocol, 'assignment_state', side_effect=read_closing_state):
            recorded = command(other, payload, expected_revision=revision, request_id='record-final')['result']
            records, artifacts = other.snapshot()['records'], other.build_artifact_store()
            if kind == 'value_review':
                from research_harness import research_decisions
                self.assertEqual(research_decisions._independence(records, artifacts, [recorded]), [])
            if kind == 'review_adjudication':
                self.assertTrue(protocol.adjudication_state(records, artifacts, identifier)['ready'])
            if kind == 'problem_openness':
                from research_harness import problem_openness
                self.assertNotIn('problem_openness_stale', [row['code'] for row in
                    problem_openness.assess_openness(records, artifacts)])
            # Historical consumers must also retain the exact composite identity.
            bound = records[kind][identifier]
            bound['composite'] = dict(bound['composite'], digest='changed-binding')
            if kind == 'value_review':
                self.assertTrue(research_decisions._independence(records, artifacts, [bound]))
            if kind == 'review_adjudication':
                self.assertFalse(protocol.adjudication_state(records, artifacts, identifier)['ready'])
            if kind == 'problem_openness':
                self.assertIn('problem_openness_stale', [row['code'] for row in
                    problem_openness.assess_openness(records, artifacts)])
            if kind == 'verdict_assessment':
                from research_harness import verification
                with self.assertRaises(ResearchError):
                    verification.require_verdict_assignment(records, artifacts, bound, output['verdict'])
        self.assertTrue(reader.called)
        self.assertEqual(recorded.get('composite'), dict(id=composite['id'], digest=digest(composite)))
        recorded.pop('composite')
        # Verdict assessments digest the whole record, including its optional binding.
        if kind == 'verdict_assessment':
            recorded.pop('digest')
            recorded['digest'] = digest(recorded)
        self.assertEqual(_canonical(recorded), _canonical(legacy))
        saved = other.snapshot()['records'][kind][identifier]
        self.assertEqual(saved['composite'], dict(id=composite['id'], digest=digest(composite)))
        self.assertNotIn('composite', case.store.snapshot()['records'][kind][identifier])

    def test_value_review_records_composite_binding_and_preserves_legacy_bytes(self):
        import test_research_problem_reviews as problem_fixtures
        from research_harness import research_decisions
        case = self.prepare_fixture(problem_fixtures.ProblemReviewTests)
        case.supporting = case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        case.prepare_review_route()
        payload = case.observe('bar', 'first', record=False)
        self.check_record_binding(case, research_decisions.record_value_review, payload, 'value_review', payload['id'])

    def test_problem_openness_records_composite_binding_and_preserves_legacy_bytes(self):
        import test_research_problem_reviews as problem_fixtures
        case = self.prepare_fixture(problem_fixtures.ProblemOpennessReviewTests)
        case.observe_openness('unresolved')
        self.check_record_binding(case, case.load_problem_module().record_openness,
            dict(assignment_id='openness-assignment'), 'problem_openness', 'openness-assignment')

    def test_adjudication_records_composite_binding_and_preserves_legacy_bytes(self):
        case = self.prepare_fixture(fixtures.ReviewProtocolTests)
        evidence = case.bar_reviews()
        case.mutate(protocol.record_assignment, dict(id='adjudicator', route_id='route', dossier_id='dossier',
            role='adjudicator', reviewer_id='third', author_id='author',
            context=dict(review_ids=['review-first', 'review-second'], objection_id='objection-first')))
        output = dict(objection_id='objection-first', disposition='not_upheld', reason='The supplied source settles it.',
                      evidence=[evidence], correction_admissible=None)
        case.invoke(output, 'adjudicator-attempt', 'adjudicator')
        self.check_record_binding(case, protocol.record_adjudication, dict(output, id='finding', assignment_id='adjudicator'),
                                  'review_adjudication', 'finding')

    def test_verification_records_composite_binding_and_preserves_legacy_bytes(self):
        import test_research_verification as verification_fixtures
        case = self.prepare_fixture(verification_fixtures.ResearchVerificationTests)
        api = case.verifier()
        task = case.mutate(api.record_task, dict(task=case.task))['result']
        payload = case.build_bind_payload(task['digest'], case.verdict())
        self.check_record_binding(case, api.bind_verdict, payload, 'verdict_assessment', payload['id'])
