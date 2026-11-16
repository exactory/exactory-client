"""The separately applied writer switch selects v3, retaining recorded v2 and fallback."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import review_parts as legacy, review_parts_v3 as parts
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.artifacts import ArtifactStore
from test_review_parts_v3 import make_packet
from test_review_route_capacity import build_route


class SeriesDefaultTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.route = build_route()
        selected = patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, legacy.PROTOCOL))
        selected.start()
        self.addCleanup(selected.stop)
        self.packet = make_packet(3, 100000)
        self.value = dict(id='new-series', role='result', context={})
        self.prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True)

    def prepare(self, records=None, value=None):
        return legacy.prepare_packet(records or {}, self.artifacts, value or self.value, self.route,
            self.packet, self.prompt, [], capacity_replacement={})

    def test_new_series_selects_v3(self):
        self.assertEqual(self.prepare()[legacy.KEY]['protocol'], parts.PROTOCOL)

    def test_recorded_v2_continues_byte_for_byte_under_both_defaults(self):
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)):
            first = self.prepare()
        prior = dict(id=self.value['id'], packet=parts._put_value(self.artifacts, first), recorded_revision=1)
        records = dict(review_assignment={prior['id']: prior})
        value = dict(self.value, id='continued-v2', context=dict(prior_assignment_id=prior['id']))
        from review_parts_fixtures import observe
        observed_packet = copy.deepcopy(first)
        observed_packet['_test_inventory'] = legacy.read_inventory(self.artifacts,
            first[legacy.KEY]['inventory'], first[legacy.KEY]['inventory_digest'])
        observation = observe(observed_packet)
        with patch.object(legacy, 'observe_series', return_value=[observation]), \
                patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)):
            expected = self.prepare(records, value)
        with patch.object(legacy, 'observe_series', return_value=[observation]):
            actual = self.prepare(records, value)
        self.assertEqual(legacy.render_json(actual), legacy.render_json(expected))

    def test_v3_refused_packet_uses_identical_v2_fallback(self):
        self.route = build_route(output=1000)
        self.packet = make_packet(1, 50000)
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)):
            expected = self.prepare()
        admission = parts.admit_packet({}, self.artifacts, self.route, self.packet, 'problem_result')
        self.assertEqual(admission['state'], 'refuse')
        actual = self.prepare()
        self.assertEqual(legacy.render_json(actual), legacy.render_json(expected))
