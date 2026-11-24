"""A math chain's successor reviews the exact subject of its first assignment."""

import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from codex_route_fixtures import probe_codex_route
import continuity_forms_fixtures as forms
import test_openness_carried_integration as carried_fixtures
import test_openness_reading_math_v2 as math_fixtures
import test_research_problem_openness_minimum as minimum_fixtures
from test_openness_chain_content_stop import ChainStopSupport, make_stale
from research_harness import openness_reading as reading, openness_reading_v7 as v7, openness_successor
from research_harness import review_protocol as protocol
from research_harness.check_mode import check_mutation
from research_harness.evidence import digest
from research_harness.reading import record_reading
from research_harness.review_continuation import plan_next


class MathSuccessorSubjectTests(unittest.TestCase):
    def setUp(self):
        harness = Path(__file__).resolve().parents[1] / 'skills' / 'math-solver' / 'harness'
        with patch.object(sys, 'path', [str(harness), str(harness / 'tests')] + sys.path):
            self.controller_fixtures = importlib.import_module('search_fixtures')
            self.controller_service = importlib.import_module('search_controller.service')
            self.controller_error = importlib.import_module('search_controller.errors').SearchError

    def build_reviewer(self, assignment_id):
        return dict(source='host', actor_id='openness-reviewer', attestation_id='review-assignment:' + assignment_id)

    def build_minimum(self, case, reading_id, *, neighbours=()):
        row = case.store.snapshot()['records']['reading'][reading_id]
        return minimum_fixtures.ProblemOpennessMinimumTests.build_finding(
            case, case.searches, [row], neighbours=neighbours)['minimum']

    def prepare_math_case(self):
        return forms.prepare_unit_fixture(self, math_fixtures.MathV2RecoveryTests,
                                          'test_reading_at_output_limit_is_replaced_at_next_lower_level')

    def start_controller(self, case):
        contract = self.controller_fixtures.contract()
        claim = contract['original_claim']
        claim['statement'] = case.selected['statement']
        controller = self.controller_service.Controller(case.root)
        controller.command('init', dict(contract=contract), 0, 'initialize-native')
        return controller, claim

    def choose_controller_inputs(self, step, identifier='successor'):
        reviewer = dict(step['payload'].get('reviewer', {}))
        self.assertIn('inputs', step['chooses'])
        if reviewer:
            self.assertIn('reviewer.attestation_id', step['chooses'])
            reviewer['attestation_id'] = 'review-assignment:' + identifier
        else:
            self.assertIn('reviewer', step['chooses'])
            reviewer = self.build_reviewer(identifier)
        self.assertEqual(reviewer, self.build_reviewer(identifier))
        return dict(step['payload'], reviewer=reviewer, inputs=[])

    def take_math_assignment(self, case, step, native, identifier='successor'):
        check, assign = step['then']
        self.assertEqual((check['command'], check['check'], assign['command']),
                         ('review-assignment', True, 'review-assignment'))
        checked = dict(check['payload'], id=identifier, context=dict(native_packet=native))
        check_mutation(case.store, protocol.record_assignment, checked, expected_revision=case.store.revision,
                       request_id='check-' + identifier, bounded_payload=checked)
        payload = dict(assign['payload'], id=identifier, context=dict(native_packet=native))
        case.mutate(protocol.record_assignment, payload)
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, identifier)
        self.assertEqual(plan['state'], 'run')
        return plan

    def assert_scientific_fields(self, step, original):
        self.assertEqual(step['command'], 'search review-packet')
        self.assertEqual(step['payload'].get('kind'), original['kind'])
        self.assertEqual(digest(step['payload'].get('subject')), original['subject_digest'])
        self.assertEqual(step['payload']['claim_digest'], original['claim_digest'])
        self.assertEqual(step['root'], original['root'])
        self.assertEqual(step['objective_id'], original['objective_id'])
        self.assertEqual(step['checkpoint'], original['scientific_context'].get('checkpoint'))
        self.assertEqual(step['reviewer_id'], original['reviewer']['actor_id'])

    def export_legacy_math_packet(self, controller, spec):
        from search_controller import openness

        # The 0.67.0 writer checked the minimum's shape inside packet(), without comparing the reviewed statement.
        with patch.object(openness, 'validate_minimum_bindings'):
            return controller.command('review-packet', spec, None, None)['native_packet']

    def test_historical_wrong_claim_minimum_opens_both_public_exports(self):
        class HistoricalMathCase(math_fixtures.MathV2RecoveryTests):
            text_sizes = (135000,)

        case = forms.prepare_unit_fixture(self, HistoricalMathCase,
                                         'test_reading_at_output_limit_is_replaced_at_next_lower_level')
        controller, claim = self.start_controller(case)
        good = self.build_minimum(case, 'read-1')
        other = 'A different mathematical assertion'
        search = minimum_fixtures.ProblemOpennessMinimumTests.search(case, case.versions, formulation=other)
        wrong = dict(good, statement=other, searches=[dict(formulation=other, search_digest=digest(search))])
        spec = dict(kind='result', subject=dict(evidence_digests=[]), claim_digest=digest(claim),
                    reviewer=self.build_reviewer('math-1'), inputs=[], openness=wrong)
        native = self.export_legacy_math_packet(controller, spec)
        original = json.loads(case.artifacts.read(native))
        self.assertEqual(original['scientific_context']['openness_minimum'], wrong)
        form = forms.FormTurn('math-v2', 'final', case, case, 'math-1')

        for version in (reading.MATH_PROTOCOL, reading.MATH_PROTOCOL_V2):
            with self.subTest(protocol=version), forms.fork_store(form), \
                    patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (version,)):
                form.head = 'math-1'
                if version == reading.MATH_PROTOCOL:
                    case.assign(form.head, native)
                    self.assertEqual(reading.find_protocol(form.packet()), version)
                    ChainStopSupport.contaminate(case, form.head)
                    stopped = form.plan()
                    self.assertEqual((stopped['state'], stopped['code']),
                                     ('handoff', 'review_context_contaminated'))
                    step = stopped['details']['step']
                else:
                    form.head, packet = case.reach_final(native)
                    self.assertEqual(reading.find_stage(packet), 'final')
                    stopped, _, _ = forms.drive_failures(self, form, forms.fail_size_request('input_over_limit', None),
                                                         'input_over_limit', limit=4)
                    self.assertEqual((stopped['state'], stopped['code']), ('stop', 'review_input_too_large'))
                    route_answer = stopped['details']['decision']['then'][0]
                    self.assertEqual(route_answer['answer'], reading.MATH_V2_ROUTE_ANSWER)
                    [step] = route_answer['steps']
                    self.assertEqual([child.get('check', False) for child in step['then']], [True, False])
                self.assert_scientific_fields(step, original)
                inputs = self.choose_controller_inputs(step)
                with self.assertRaises(self.controller_error) as mismatch:
                    controller.command('review-packet', dict(inputs, openness=wrong), None, None)
                self.assertEqual(mismatch.exception.code, 'openness_statement_mismatch')
                self.assertEqual(form.plan(), stopped)
                if 'openness' in step['chooses']:
                    inputs['openness'] = good
                exported = controller.command('review-packet', inputs, None, None)
                self.assertEqual(step['chooses'].count('openness'), 1)
                self.assertNotIn('openness', step['payload'])
                self.assertEqual(step['prerequisites'][0], dict(
                    condition=openness_successor.MATH_CLAIM_MINIMUM_PREREQUISITE, claim_digest=digest(claim)))
                successor = json.loads(case.artifacts.read(exported['native_packet']))
                self.assertEqual(successor['scientific_context']['openness_minimum'], good)
                for key in ('subject_digest', 'claim_digest', 'root', 'objective_id'):
                    self.assertEqual(successor[key], original[key])
                self.assertEqual(successor['reviewer'], self.build_reviewer('successor'))
                for child in step['then']:
                    self.assertEqual(child['command'], 'review-assignment')
                    payload = dict(child['payload'], id='successor', route_id='route', reviewer_id=step['reviewer_id'],
                                   context=dict(native_packet=exported['native_packet']))
                    if child.get('check'):
                        checked = check_mutation(case.store, protocol.record_assignment, payload,
                            expected_revision=case.store.revision, request_id='successor-check', bounded_payload=payload)
                        self.assertEqual(checked['check'], 'accepted')
                    else:
                        case.mutate(protocol.record_assignment, payload)
                next_plan = form.plan('successor')
                self.assertEqual((next_plan['state'], next_plan['command']), ('run', 'review-run'))

    def test_minimum_is_open_when_only_controller_state_can_establish_the_claim(self):
        case = self.prepare_math_case()
        controller, claim = self.start_controller(case)
        minimum = self.build_minimum(case, 'read-1')
        spec = dict(kind='result', subject=dict(evidence_digests=[]), claim_digest=digest(claim),
                    reviewer=self.build_reviewer('math-1'), inputs=[], openness=minimum)
        exported = controller.command('review-packet', spec, None, None)
        native = json.loads(case.artifacts.read(exported['native_packet']))
        records = case.store.snapshot()['records']
        for portable in (True, False):
            with self.subTest(portable_claim=portable):
                # Feed only the portable proof available to the binder. The controller state stays intact.
                retained = native if portable else dict(native, contract={})
                step = dict(payload={}, chooses=['reviewer', 'inputs'], prerequisites=[])
                openness_successor.bind_math_export(step, retained, 'openness-reviewer', records)
                if portable:
                    self.assertEqual(step['payload']['openness'], minimum)
                else:
                    self.assertNotIn('openness', step['payload'])
                    self.assertIn('openness', step['chooses'])
                    self.assertEqual(step['prerequisites'][0]['claim_digest'], digest(claim))
                    chosen = dict(self.choose_controller_inputs(step), openness=minimum)
                    accepted = controller.command('review-packet', chosen, None, None)
                    case.assign('successor', accepted['native_packet'])
                    self.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, 'successor')['state'],
                                     'run')

    def test_compact_final_preserves_attested_subject_in_controller_export(self):
        case = self.prepare_math_case()
        controller, claim = self.start_controller(case)
        subject = dict(claim=claim, evidence_digests=[], proof_context='A bound claim. ' * 8600)
        exported = controller.command('review-packet', dict(kind='result', subject=subject, claim_digest=digest(claim),
            reviewer=self.build_reviewer('math-1'), inputs=[], openness=self.build_minimum(case, 'read-1')), None, None)
        native = exported['native_packet']
        original = json.loads(case.artifacts.read(native))
        head, packet = case.reach_final(native)
        self.assertEqual(reading.find_stage(packet), 'final')
        self.assertEqual(packet['native']['subject'], {'unit': 'statement'})

        bundle = carried_fixtures.StoredSelectionIntegrationTests.hide_extraction_in_bundle(case)
        case.mutate(record_reading, case.full_note(bundle, 'read-replacement'))
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, head)
        self.assertEqual(plan['code'], 'openness_packet_outdated')
        step = plan['details']['step']
        self.assert_scientific_fields(step, original)
        self.assertIn('openness', step['chooses'])
        self.assertNotIn('openness', step['payload'])

        make_stale(case, 'route')
        renew = plan_next(case.store.snapshot()['records'], case.artifacts, head)
        self.assertEqual(renew['command'], 'review-route')
        case.mutate(protocol.record_route, dict(renew['details']['step']['payload'], id='renewed-route'))
        probe = plan_next(case.store.snapshot()['records'], case.artifacts, head)
        self.assertEqual(probe['command'], 'review-probe')
        probe_codex_route(case, 'renewal-probe', probe['details']['step']['payload']['route_id'])
        ready = plan_next(case.store.snapshot()['records'], case.artifacts, head)
        step = ready['details']['step']
        self.assert_scientific_fields(step, original)
        self.assertEqual([row['payload']['route_id'] for row in step['then']], ['renewed-route'] * 2)
        spec = dict(self.choose_controller_inputs(step), openness=self.build_minimum(case, 'read-replacement'))
        successor = controller.command('review-packet', spec, None, None)
        self.assertEqual(successor['subject_digest'], exported['subject_digest'])
        self.assertEqual(successor['claim_digest'], exported['claim_digest'])
        self.assertNotEqual(ready, self.take_math_assignment(case, step, successor['native_packet']))

    def start_carried_math_chain(self, case, route_id='route'):
        controller, claim = self.start_controller(case)
        minimum = self.build_minimum(case, 'read-1')
        spec = dict(kind='result', subject=dict(claim=claim, evidence_digests=[]), claim_digest=digest(claim),
                    inputs=[], openness=minimum)
        old = controller.command('review-packet', dict(spec, reviewer=self.build_reviewer('old')), None, None)
        prior = case.assign('old', old['native_packet'])
        case.invoke('old', case.build_v7_answer(case.read_packet('old')))
        exported = controller.command('review-packet', dict(spec, reviewer=self.build_reviewer('head')), None, None)
        payload = {key: prior[key] for key in ('dossier_id', 'role', 'reviewer_id', 'author_id')}
        head = case.mutate(protocol.record_assignment, dict(payload, id='head', route_id=route_id,
                           context=dict(native_packet=exported['native_packet'])))['result']
        self.assertTrue(head['carried_readings'])
        return controller, minimum, exported['native_packet']

    def test_carried_stop_opens_minimum_rejected_by_the_controller(self):
        case = self.prepare_math_case()
        controller, minimum, native = self.start_carried_math_chain(case)
        added_search = minimum_fixtures.ProblemOpennessMinimumTests.search(case, case.versions)
        case.searches.append(added_search)
        ChainStopSupport.contaminate(case, 'old')
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, 'head')
        self.assertEqual(plan['code'], 'openness_carried_turn_unverified')
        step = plan['details']['step']
        original = json.loads(case.artifacts.read(native))
        self.assert_scientific_fields(step, original)

        inputs = self.choose_controller_inputs(step)
        with self.assertRaises(self.controller_error) as stale:
            controller.command('review-packet', dict(inputs, openness=minimum), None, None)
        self.assertEqual((stale.exception.code, stale.exception.details),
                         ('openness_search_stale', dict(search_ids=[added_search['id']])))
        if 'openness' in step['chooses']:
            inputs['openness'] = self.build_minimum(case, 'read-1')
        successor = controller.command('review-packet', inputs, None, None)
        self.assertEqual(plan['details']['stale_minimum'],
                         dict(cause='openness_search_stale', search_ids=[added_search['id']]))
        self.assertNotIn('openness', step['payload'])
        self.assertEqual(step['prerequisites'][0], dict(condition=v7.MATH_UNITS_PREREQUISITE,
                                                       search_ids=[added_search['id']]))
        self.assertNotEqual(plan, self.take_math_assignment(case, step, successor['native_packet']))

    def test_carried_math_origin_probe_and_retirement_advance_the_plan(self):
        case = self.prepare_math_case()
        route = case.store.snapshot()['records']['review_route']['route']
        configuration = {key: route[key] for key in ('adapter', 'model', 'endpoint', 'configuration')}
        case.mutate(protocol.record_route, dict(configuration, id='second-route'))
        probe_codex_route(case, 'second-route-probe', 'second-route')
        self.start_carried_math_chain(case, 'second-route')

        def plan():
            return plan_next(case.store.snapshot()['records'], case.artifacts, 'head')

        ChainStopSupport.fail_probe(case)
        stopped = plan()
        self.assertEqual((stopped['code'], stopped['command']),
                         ('openness_carried_turn_unverified', 'review-probe'))
        probe_codex_route(case, 'recovery-probe', stopped['details']['step']['payload']['route_id'])
        self.assertEqual(plan()['state'], 'run')

        ChainStopSupport.fail_probe(case, probe_id='probe-before-retirement')
        make_stale(case, 'route')
        retiring = plan()
        self.assertEqual((retiring['code'], retiring['command']),
                         ('openness_carried_turn_unverified', 'review-context'))
        case.mutate(protocol.record_context_event, dict(retiring['details']['step']['payload'],
                    id='retire-origin', reason='The owner retired the stale origin route.',
                    evidence=[case.artifacts.put(b'Owner retirement.', 'text/plain')]))
        self.assertEqual(plan()['state'], 'run')

    def test_combine_keeps_the_required_controller_fields_and_fixed_minimum(self):
        """Follow the public carried stop from each paged stage, including a combine with no native frame."""
        formulations = ['Synthetic neighbour %d' % number for number in range(32)]

        def attested_packet(case, *, neighbours):
            controller, claim = self.start_controller(case)
            for formulation in formulations:
                case.searches.append(minimum_fixtures.ProblemOpennessMinimumTests.search(
                    case, case.versions, formulation=formulation))
            spec = dict(kind='result', subject=dict(claim=claim, evidence_digests=[]), claim_digest=digest(claim),
                        inputs=[], openness=self.build_minimum(case, 'read-1', neighbours=formulations))
            origin = controller.command('review-packet', dict(spec, reviewer=self.build_reviewer('origin')),
                                        None, None)
            case.assign('origin', origin['native_packet'])
            case.invoke('origin', case.build_v7_answer(case.read_packet('origin')))
            case.math_controller = controller
            return controller.command('review-packet', dict(spec, reviewer=self.build_reviewer('math-1')),
                                      None, None)['native_packet']

        def attested_export(case, neighbours=0):
            native = attested_packet(case, neighbours=neighbours)
            return case.math_controller, [], native

        with patch.object(forms, 'export_controller_math_packet', attested_export):
            for form in forms.iterate_turns(self, 'math-v2', ('page', 'closing', 'combine')):
                if form.turn == 'combine':
                    self.assertNotIn('native', form.packet())
                for stale in (False, True):
                    with self.subTest(turn=form.turn, stale=stale), forms.fork_store(form):
                        self.take_paged_carried_export(form, formulations, stale=stale)

    def take_paged_carried_export(self, form, formulations, *, stale):
        case = form.case
        first = form.assignment('math-1')
        self.assertTrue(first['carried_selection'])
        original = json.loads(case.artifacts.read(first['context']['native_packet']))
        original_minimum = original['scientific_context']['openness_minimum']
        ChainStopSupport.contaminate(case, 'origin')
        added = minimum_fixtures.ProblemOpennessMinimumTests.search(case, case.versions) if stale else None
        stop = form.plan()
        self.assertEqual((stop['state'], stop['code'], stop['command']),
                         ('handoff', 'openness_carried_turn_unverified', 'search review-packet'))
        step = stop['details']['step']
        self.assert_scientific_fields(step, original)
        self.assertEqual(step['payload']['reviewer'], dict(source='host', actor_id='openness-reviewer'))

        spec = self.choose_controller_inputs(step)
        if stale:
            self.assertCountEqual(step['chooses'], ['reviewer.attestation_id', 'inputs', 'openness'])
            self.assertNotIn('openness', spec)
            with patch.object(case, 'searches', [*case.searches, added]):
                spec['openness'] = self.build_minimum(case, 'read-1', neighbours=formulations)
        else:
            self.assertCountEqual(step['chooses'], ['reviewer.attestation_id', 'inputs'])
            self.assertEqual(spec['openness'], original_minimum)

        exported = case.math_controller.command('review-packet', spec, None, None)
        self.assertEqual(exported['subject_digest'], original['subject_digest'])
        self.assertEqual(exported['claim_digest'], original['claim_digest'])
        native = json.loads(case.artifacts.read(exported['native_packet']))
        self.assertEqual(native['reviewer'], self.build_reviewer('successor'))
        successor = self.take_math_assignment(case, step, exported['native_packet'])
        self.assertEqual((successor['state'], successor['command']), ('run', 'review-run'))
        if stale:
            self.assertEqual(step['prerequisites'][0].get('search_ids'), [added['id']])
