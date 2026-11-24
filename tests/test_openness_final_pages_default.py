"""Release 0.93.0 selects the final of protocol v7 in pages."""
import unittest
from unittest.mock import patch

from research_harness import openness_reading as reading, openness_reading_v7 as v7
from test_openness_reading_v7_turns import MemoryArtifacts, build_native
from v7_capacity_fixtures import build_route


class FinalPagesDefaultTests(unittest.TestCase):
    def test_request_and_pages_are_the_shipped_default_and_v6_remains_a_fallback(self):
        self.assertEqual(v7.V7_FINAL_FORMS, ('request', 'pages'))
        self.assertEqual(reading.NEW_PROBLEM_CHAIN_PROTOCOLS, (v7.PROTOCOL, reading.PROTOCOL_V6))
        with patch.object(v7, 'build_inventory', return_value=v7.plan_inventory([], {})):
            result = v7.check_v7_admission({}, MemoryArtifacts(), build_route('codex_cli_v1', 'gpt-6-astra'), build_native(9))
        self.assertEqual(result['state'], 'record')
        self.assertEqual(result['final_form'], 'pages')
