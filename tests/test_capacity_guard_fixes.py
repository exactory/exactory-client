"""Regressions for the three defects found by the v7 capacity guard."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import openness_reading as reading
from research_harness import openness_reading_v7 as v7
from research_harness import review_transport as transport
from research_harness.errors import ResearchError


class CapacityGuardFixTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def make_case(self, characters=50000):
        from test_route_capacity_properties import ReadingCapacityCommandTests
        driver = ReadingCapacityCommandTests()
        self.addCleanup(driver.doCleanups)
        return driver, driver.make_case(characters=characters)

    def test_output_caps_execute_all_lower_levels_on_the_same_route(self):
        driver, case = self.make_case()
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            native = case.export()
            case.assign('turn-1', native)
            result = driver.drive_commands(case, native, stub='every_level')
        self.assertEqual(result['end'], 'decision_after_floor')
        # The frame of a v7 turn reserves its notes cost and its list rows, so the first reading turn of this fixture
        # takes 60,000 characters, and the cap walks the two lower levels.
        self.assertEqual(result['replacements'], [(60000, 30000), (30000, 15000)])

    def test_new_search_hands_off_before_the_first_v7_request(self):
        from research_harness.review_continuation import plan_next
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        _, case = self.make_case()
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            native = case.export()
            case.assign('turn-1', native)
            case.searches.append(ProblemOpennessMinimumTests.search(case, case.versions,
                query='A synthetic search recorded after export.'))
            before = copy.deepcopy(case.store.snapshot()['records'])
            action = plan_next(before, case.artifacts, 'turn-1')
        self.assertEqual((action['state'], action.get('command'), action.get('code')),
                         ('handoff', 'export', 'openness_packet_outdated'))
        self.assertEqual(case.store.snapshot()['records'], before)

    def test_visual_runtime_identity_keeps_the_093_file_bytes(self):
        # Release 0.93.0 changes the Codex transports in place (design codex-routes, D1): a stored visual route record
        # of 0.92.1 pins 470283f4... and reads as stale, while its stored reviews and manifests still verify.
        from research_harness import review_codex_visual_transport as visual, review_codex_transport as text
        with patch.object(text, 'runtime', return_value={}):
            observed = visual.runtime()
        self.assertEqual(observed['implementation_sha256'],
            '8af94b60f01b960e3ba9f28c23c944225dec09e236ab07f8134daabe7f1a4191')

    def test_visual_v7_admission_declares_a_text_route_before_inventory_or_request(self):
        from reading_capacity_fixtures import build_reading_route
        from test_openness_reading_v7_turns import MemoryArtifacts, build_native
        from research_harness.stop_classes import STOP_REGISTRY
        route = build_reading_route('codex_cli_visual_v1', 'fits')
        # An exported problem openness packet carries its kind and its contract, which the protocol choice reads.
        native = dict(build_native(), kind='result', contract=dict(orchestration='problem_creation'))
        with patch.object(v7, 'build_inventory', side_effect=AssertionError('No visual inventory is built')), \
                patch.object(transport, 'request_body', side_effect=AssertionError('No visual request is built')):
            result = v7.check_v7_admission({}, MemoryArtifacts(), route, native)
        self.assertEqual((result['state'], result.get('code')), ('stop', 'review_visual_text_route_required'))
        self.assertEqual(result['reason'], 'text_route_required')
        self.assertEqual(STOP_REGISTRY[result['code']]['group'], 'visual_text_reading')
        self.assertEqual(STOP_REGISTRY[result['code']]['class'], 'decision')
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)), \
                patch.object(reading, 'list_reading_rows', side_effect=AssertionError('No unsupported v6 fallback')):
            selection = reading.choose_chain_protocol({}, MemoryArtifacts(), route, native)
        self.assertIsNone(selection['protocol'])
        self.assertEqual(selection['admissions'][0]['code'], 'review_visual_text_route_required')

    def test_visual_v7_command_stops_before_requests_and_executes_the_text_answer(self):
        from research_harness import review_protocol as protocol
        from reading_capacity_fixtures import build_reading_route, execute_visual_text_answer
        from capacity_evidence_fixtures import build_capacity_runtime
        from research_harness import review_codex_visual_transport as visual
        _, case = self.make_case()
        route = build_reading_route(visual.ADAPTER, 'fits')
        route.pop('runtime')
        route['id'] = 'visual-route'
        with patch.object(visual, 'runtime', return_value=build_capacity_runtime(visual.ADAPTER)):
            case.mutate(protocol.record_route, route)
            with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                native = case.export()
                before = copy.deepcopy(case.store.snapshot()['records'])
                with patch.object(transport, 'send_request', side_effect=AssertionError('No visual request')), \
                        self.assertRaises(ResearchError) as caught:
                    case.mutate(protocol.record_assignment, dict(id='turn-1', route_id=route['id'], dossier_id=None,
                        role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
                        context=dict(native_packet=native)))
                self.assertEqual(caught.exception.code, 'review_visual_text_route_required')
                self.assertEqual(case.store.snapshot()['records'], before)
                execute_visual_text_answer(self, route, caught.exception.details, case=case)

    def test_text_v7_exception_never_removes_visual_page_requirements(self):
        from research_harness import review_codex_visual_transport as visual
        from test_research_visual_input_projection import VisualInputProjectionTests
        request = VisualInputProjectionTests().request()
        packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
        packet.update(role='native_math', reading_turn=dict(protocol=v7.PROTOCOL))
        request['input'][0]['content'] = transport.PACKET_LABEL + json.dumps(packet)
        request['exactory_image_inputs'] = []
        with self.assertRaises(ResearchError) as caught:
            visual.native_user_parts(request, '/fixture')
        self.assertEqual(caught.exception.code, 'review_image_input_invalid')

    def test_stored_v6_requests_stay_identical_when_v7_admission_is_enabled(self):
        from research_harness import review_protocol as protocol
        _, case = self.make_case(characters=200000)
        native = case.export()
        assignment = case.assign('turn-1', native)
        self.assertEqual(reading.find_protocol(case.read_packet('turn-1')), reading.PROTOCOL_V6)
        records = case.store.snapshot()['records']
        assignment = records['review_assignment']['turn-1']
        before = protocol.build_assignment_request(records, case.artifacts, assignment)
        frozen = copy.deepcopy(assignment)
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            after = protocol.build_assignment_request(records, case.artifacts, assignment)
        self.assertEqual(after, before)
        self.assertEqual(case.store.snapshot()['records']['review_assignment']['turn-1'], frozen)


if __name__ == '__main__':
    unittest.main()
