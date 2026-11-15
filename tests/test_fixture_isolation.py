"""Synthetic integration journeys must leave shared review backends unchanged."""

import sys
import unittest
from unittest.mock import patch

from research_harness import review_route_capacity as capacity
from research_harness import review_codex_transport as codex
from problem_parts_fixtures import ResultPartsCase
import test_problem_post_head_planning as posted_fixtures
from test_draft import _DepositTestCase
from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading
import test_review_preflight as preflight_fixtures
import test_review_staged_integration_partitions as integration_fixtures
import test_review_staged_sizing as sizing_fixtures
from research_harness import review_staged_protocol as producer


class FixtureIsolationTests(unittest.TestCase):
    def test_managed_research_and_manuscript_restore_binary_backend(self):
        original = capacity.describe_binary_rules
        fixture = _DepositTestCase()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.addCleanup(fixture.research.doCleanups)
        self.assertIs(capacity.describe_binary_rules, original)

    def test_failed_nested_setup_restores_claims_and_codex_runtime(self):
        original_claims = sys.modules['research_harness.problem_claims']
        original_runtime = codex.runtime
        original_declarations = codex.NATIVE_DECLARATION_SETS
        setup = ResultPartsCase.setUp

        def fail_after_setup(fixture):
            self.addCleanup(fixture.doCleanups)
            setup(fixture)
            raise RuntimeError('Synthetic failure after nested review setup')

        case = posted_fixtures.PostedHeadPlanningTests('test_native_openness_closes_pending_plan_after_post')
        result = unittest.TestResult()
        with patch.object(ResultPartsCase, 'setUp', fail_after_setup):
            case.run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIn('Synthetic failure after nested review setup', result.errors[0][1])
        self.assertIs(sys.modules['research_harness.problem_claims'], original_claims)
        self.assertIs(codex.runtime, original_runtime)
        self.assertIs(codex.NATIVE_DECLARATION_SETS, original_declarations)

    def test_failed_staged_integration_setup_restores_observation_backend(self):
        original = producer.authenticate_observation
        setup = integration_fixtures.IntegrationPartitionTests.setUp

        def fail_after_setup(fixture):
            self.addCleanup(fixture.doCleanups)
            setup(fixture)
            raise RuntimeError('Synthetic failure after staged integration setup')

        case = sizing_fixtures.RenderedSizingTests('test_packet_output_tokens_exclude_the_reserved_margin')
        result = unittest.TestResult()
        with patch.object(integration_fixtures.IntegrationPartitionTests, 'setUp', fail_after_setup):
            case.run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIn('Synthetic failure after staged integration setup', result.errors[0][1])
        self.assertIs(producer.authenticate_observation, original)

    def test_failed_openness_setup_restores_current_protocol(self):
        original = openness_reading.find_chain_protocol

        def fail_initialization(fixture):
            self.addCleanup(fixture.doCleanups)
            raise RuntimeError('Synthetic failure after protocol setup')

        case = preflight_fixtures.HistoricalAdmissionTests('test_single_turn_native_review_has_no_hypothetical_chain_floor')
        result = unittest.TestResult()
        with patch.object(OpennessReadingCase, 'initialize_problem', fail_initialization):
            case.run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIn('Synthetic failure after protocol setup', result.errors[0][1])
        self.assertIs(openness_reading.find_chain_protocol, original)


if __name__ == '__main__':
    unittest.main()
