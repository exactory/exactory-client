"""Merged planned families share capture and handles without transmitting tables."""
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_handles as handles, review_parts as legacy, review_parts_v3 as parts
from research_harness import review_codex_invocation as capture, openness_reading_v7 as v7
from research_harness.review_excerpts import read_sent_packet
from research_harness.review_refusal import evidence_arrays
import test_openness_final_pages as page_fixtures
import test_review_parts_v3 as series_fixtures
import test_review_session_v4_turns as session_fixtures


@contextmanager
def build_case(case):
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()


class PlannedFamilyIntegrationTests(unittest.TestCase):
    def assert_captured_family(self, store, cited, final):
        records = store.snapshot()['records']
        artifacts = store.build_artifact_store()
        for attempt in records['review_attempt'].values():
            request = json.loads(artifacts.read(attempt['request']))
            packet = read_sent_packet(request)
            self.assertIsNotNone(packet)
            self.assertNotIn(handles.HANDLE_TABLE, packet)
            response = json.loads(artifacts.read(attempt['response']))
            self.assertIn(capture.KEY, response)
        for identifier in dict.fromkeys((cited, final)):
            assignment = records['review_assignment'][identifier]
            attempt = next(row for row in records['review_attempt'].values()
                           if row['assignment_id'] == identifier and row['status'] == 'completed')
            self.assertIn('handle_expansion', attempt)
            if identifier == cited:
                self.assertGreater(attempt['handle_expansion']['references'], 0)
            self.assertTrue(protocol.authenticate_attempt(records, store.build_artifact_store(), assignment, attempt))

    def run_condensing_family(self):
        with build_case(page_fixtures.CondensingCommandTests()) as case:
            native = case.export_targets(1)
            with patch.object(v7, 'V7_FINAL_FORMS', ('request',)):
                case.assign('turn-1', native)
            assigned = case.store.snapshot()['records']['review_assignment']['turn-1']
            plan = capacity.read_chain_capacity_plan(case.artifacts, assigned)
            current = 'turn-1'
            for number in range(20):
                packet = case.read_packet(current)
                binding = packet['reading_turn']
                output = case.build_v7_answer(packet)
                allowance = binding['allowances']['targets'][0]['remaining']
                fulltexts = {row['handle'] for row in binding['plan']['now'] if row['kind'] == 'fulltext'}
                for source in packet['evidence']:
                    if source['handle'] not in fulltexts:
                        continue
                    for offset in range(0, min(len(source['content']), 30000), 6000):
                        if allowance < 7200:
                            break
                        output['excerpts'].append(dict(unit=source['handle'], text=source['content'][offset:offset + 6000],
                            purpose='Relevant supporting material.', targets=['t1']))
                        allowance -= 7200
                last = sum(row['characters'] for row in binding['plan']['now']) >= binding['plan']['remaining']['characters']
                if last:
                    fixed = plan['frame']['final']['bound'] + plan['task']['final']['notice_characters']
                    case.invoke_with_ratio(current, output, (fixed + 18000) / plan['limits']['input_tokens']['value'])
                else:
                    case.invoke(current, output)
                following = 'reading-%d' % number
                case.assign(following, native, prior=current)
                current = following
                if last:
                    break
            self.assertIn('reading_combine', case.read_packet(current))
            cited = []
            def cite(identifier, packet):
                if 'reading_combine' in packet:
                    block = packet['reading_combine']['block']
                    cited.append(identifier)
                    return dict(block=block['handle'], summary='Condensed authenticated material.',
                        refs=[dict(ref=block['entries'][0].get('handle', block['entries'][0].get('block')))])
                return None
            final, _ = case.finish_pages(native, first=current, before_answer=cite, assign=False)
            case.assert_recorded_composite(final)
            self.assertTrue(cited)
            self.assert_captured_family(case.store, cited[0], final)

    def run_series_family(self):
        with build_case(series_fixtures.RecordedSeriesTests()) as case:
            case.oversized(count=3, characters=50000, limit=1300000)
            with patch.object(capacity, 'read_profile_priors', return_value=[]):
                first = case.assign()
            _, final = case.finish_series(first)
            packet = case.packet(final)
            self.assertEqual(packet[legacy.KEY]['protocol'], parts.PROTOCOL)
            state = parts._read_value(case.case.artifacts, packet[legacy.KEY]['state'])
            output = case.final_output()
            for array in evidence_arrays(output):
                array[:] = [dict(ref='u1'), dict(ref=state['cards'][0]['id'])]
            case.record_final(final, output)
            self.assert_captured_family(case.case.store, final['id'], final['id'])

    def run_session_family(self):
        with build_case(session_fixtures.SessionTurnsTests()) as case:
            version = case.add_sources([180000])[0]
            current = case.case.assign('bar', 'first', identifier='session-first')
            case.respond(current, [dict(id='source', kind='source', version_id=version, depth='fulltext')])
            last = None
            for number in range(10):
                current = case.case.assign('bar', 'first', identifier='session-%d' % number,
                                           prior_assignment_id=current['id'])
                packet = case.read_packet(current)
                if 'session_final' in packet:
                    break
                for row in packet['session_turn']['plan']['now']:
                    if row.get('kind') == 'fulltext':
                        last = row
                case.respond(current)
            self.assertIn('session_final', packet)
            self.assertIsNotNone(last)
            self.assertGreater(last['start'], 0)
            case.finish(current, [dict(ref=last['unit'], part=last['part'], start=0, end=20)])
            self.assert_captured_family(case.case.store, current['id'], current['id'])

    def test_condensing_series_and_session_capture_record_and_fresh_authenticate_without_inline_tables(self):
        self.assertEqual(set(handles.HANDLE_TABLE_BUILDERS),
            {'reading_turn', 'reading_final', 'reading_combine', 'review_parts', 'session_turn', 'session_final',
             'session_combine'})
        for family, run in (('v7 condensing', self.run_condensing_family),
                            ('v3 series', self.run_series_family), ('v4 session', self.run_session_family)):
            with self.subTest(family=family):
                run()
