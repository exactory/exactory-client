"""A recovered bounded call is ready only while its own and carried inputs still verify."""

import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import probe_codex_route
import continuity_forms_fixtures as forms
from continuity_stage3_fixtures import fail_outcome_request
import test_chain_stop_math_successor as math_successor
import test_openness_carried_integration as carried_fixtures
from test_openness_chain_content_stop import ChainStopSupport
import test_research_problem_openness_minimum as minimum_fixtures
from research_harness import openness_reading_v7 as v7, review_failure_class as failures
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.reading import record_reading


class RecoveredReadinessTests(unittest.TestCase):
    def test_recovered_bounded_call_matches_fresh_dispatch(self):
        math = forms.prepare_unit_fixture(self, math_successor.MathSuccessorSubjectTests, 'runTest')
        case = math.prepare_math_case()
        controller, _, native = math.start_carried_math_chain(case)
        original = json.loads(case.artifacts.read(native))
        form = forms.FormTurn('math-v2', 'reading', case, case, 'head')
        missing, counts, _ = forms.drive_failures(
            self, form, fail_outcome_request(case, 'runtime_unavailable'), 'runtime_unavailable')
        self.assertEqual(counts, {1: 7})
        self.assertEqual((missing['state'], missing['code'], missing['command']),
                         ('handoff', 'review_runtime_missing', 'review-probe'))
        probe_codex_route(case, 'verified-again', missing['details']['step']['payload']['route_id'])
        due = forms.build_due_clock(failures.datetime.fromisoformat(missing['not_before']))
        with patch.object(failures, 'datetime', due), \
                patch.object(v7, 'describe_content_handoff', wraps=v7.describe_content_handoff) as own, \
                patch.object(v7, 'describe_carried_handoff', wraps=v7.describe_carried_handoff) as carried:
            ready = form.plan()
        self.assertEqual((ready['state'], ready['command']), ('run', 'review-run'))
        self.assertEqual((own.call_count, carried.call_count), (1, 1))

        for code in ('openness_packet_outdated', 'openness_carried_turn_unverified'):
            for later_search in (False, True):
                with self.subTest(code=code, later_search=later_search), forms.fork_store(form), \
                        patch.object(case, 'searches', list(case.searches)):
                    reading_id = 'read-1'
                    if code == 'openness_packet_outdated':
                        bundle = carried_fixtures.StoredSelectionIntegrationTests.hide_extraction_in_bundle(case)
                        reading_id = 'read-replacement'
                        case.mutate(record_reading, case.full_note(bundle, reading_id))
                    else:
                        ChainStopSupport.contaminate(case, 'old')
                    if later_search:
                        search = minimum_fixtures.ProblemOpennessMinimumTests.search(case, case.versions)
                        case.searches.append(search)
                    self.assert_ready_refusal(form, ready, code, due, math, controller, original, reading_id)

    def assert_ready_refusal(self, form, ready, code, due, math, controller, original, reading_id):
        case = form.case
        revision = case.store.revision
        with patch.object(failures, 'datetime', due), patch.object(protocol.transport, 'send_request',
                side_effect=AssertionError('The blocked bounded recovery must not send.')) as send:
            recovered = failures.plan_after_failure(form.records(), case.artifacts, form.head)
            public = form.plan()
            with self.assertRaises(ResearchError) as dispatch:
                forms.matrix.execute(case, ready['command'], ready['payload'])
        self.assertEqual(case.store.revision, revision)
        send.assert_not_called()
        self.assertEqual(dispatch.exception.code, code)
        expected = ('handoff', code, 'search review-packet')
        self.assertEqual((public['state'], public['code'], public['command']), expected)
        self.assertEqual((recovered['state'], recovered.get('code'), recovered.get('command')), expected)
        self.assertEqual(recovered['details']['message'], dispatch.exception.message)
        step = recovered['details']['step']
        self.assertEqual(step, dispatch.exception.details['step'])
        math.assert_scientific_fields(step, original)
        inputs = math.choose_controller_inputs(step)
        if 'openness' in step['chooses']:
            inputs['openness'] = math.build_minimum(case, reading_id)
        exported = controller.command('review-packet', inputs, None, None)
        self.assertEqual(exported['subject_digest'], original['subject_digest'])
        self.assertEqual(exported['claim_digest'], original['claim_digest'])
        resumed = math.take_math_assignment(case, step, exported['native_packet'])
        self.assertEqual((resumed['state'], resumed['command']), ('run', 'review-run'))
