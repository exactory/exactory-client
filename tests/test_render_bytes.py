"""Stored parts series render the integration base's bytes with one listing per verification."""

from collections import Counter
from contextlib import nullcontext, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from codex_route_fixtures import build_codex_response
from research_harness import (bar_sources, cli, output_views, research_decisions, review_output_correction,
                              review_parts as parts, review_parts_v3, review_protocol, review_route_capacity)
from research_harness.evidence import digest
from render_reference import read_render_outcome, use_reference_functions
from review_parts_fixtures import PartsResultCase as PartsFixture, make_output
import test_research_output_views as views
import test_review_parts_repair_journey as repair
import test_review_parts_v3 as v3_fixtures


def render_with_list_counts(store, *, reference=False, render=output_views.render_all):
    with use_reference_functions() if reference else nullcontext():
        verify = parts.verify_series
        counts = []

        def count_lists(records, artifacts, identifier):
            with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
                result = verify(records, artifacts, identifier)
            counts.append((identifier, listing.call_count))
            return result

        with patch.object(parts, 'verify_series', side_effect=count_lists):
            summary = render(store)
        return summary, counts


def copy_render_inputs(source, destination):
    store = views.copy_store(source, destination)
    for relative in output_views._CHECK_REPORT_PATHS:
        path = source / relative
        if path.is_file():
            (destination / relative).write_bytes(path.read_bytes())
    return store


class RenderComparison:
    def setUp(self):
        select_bar = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        select_bar.start()
        self.addCleanup(select_bar.stop)
        super().setUp()

    def compare_renders(self, *, missing=(), after_write=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if after_write is None:
                current = copy_render_inputs(self.case.root, root / 'current')
                current_summary, current_counts = render_with_list_counts(current)
            else:
                current = self.case.store
                current_summary, current_counts = render_with_list_counts(current, render=after_write)
            reference = copy_render_inputs(self.case.root, root / 'reference')
            base_summary, base_counts = render_with_list_counts(reference, reference=True)
            self.assertEqual(current_summary, base_summary)
            self.assertEqual(current_summary['errors'], 0)
            self.assertEqual(output_views.read_rendered_revision(current), self.case.store.revision)
            result = read_render_outcome(current.root)
            self.assertEqual(result, read_render_outcome(reference.root))
            with self.subTest(check='list calls'):
                assignments = {identifier: assignment[parts.KEY]['protocol'] for identifier, assignment in
                               self.case.read_records()['review_assignment'].items() if parts.KEY in assignment}
                self.assertTrue(assignments)
                for counts, expected in ((base_counts, 2), (current_counts, 1)):
                    expected_counts = [(identifier, 0 if protocol == review_parts_v3.PROTOCOL
                                        else 1 if identifier in missing else expected)
                                       for identifier, protocol in assignments.items()]
                    self.assertEqual(Counter(counts), Counter(expected_counts))
            return result[0]


class RenderBytesTests(RenderComparison, views.PartsViewCase):
    continue_repair = repair.ResultRepairJourneyTests.continue_repair

    def setUp(self):
        select_legacy = patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,))
        select_legacy.start()
        self.addCleanup(select_legacy.stop)
        super().setUp()

    def start_series(self):
        self.prepare_parts(count=3)
        return self.assign()

    def start_check(self, *, after_turn=None):
        assignment = self.start_series()
        while self.packet(assignment)[parts.KEY]['kind'] == 'read':
            self.invoke(assignment)
            if after_turn is not None:
                with self.subTest(turn=assignment['id']):
                    after_turn()
            assignment = self.assign('next-' + assignment['id'], assignment['id'])
        packet = self.packet(assignment)
        self.assertEqual(packet[parts.KEY]['kind'], 'combine')
        combination = dict(units=['u1'], question='What does the original support?',
                           view='Read its opening.', read=[dict(unit='u1', item=True, start=0, end=100)])
        self.invoke(assignment, make_output(packet, [combination]))
        if after_turn is not None:
            with self.subTest(turn=assignment['id']):
                after_turn()
        check = self.assign('check', assignment['id'])
        self.assertEqual(self.packet(check)[parts.KEY]['kind'], 'check')
        return check

    def seed_view_groups(self):
        bundle = dict(id='bundle', files={})
        bundle['digest'] = digest(bundle)
        for kind, identifier, fields in (
            ('publication_bundle', 'bundle', bundle),
            ('publication_receipt', 'deposit', dict(intent_id='deposit', bundle_digest=bundle['digest'])),
            ('manuscript_review', 'score', dict(bundle_digest=bundle['digest'], assessor=dict(id='assessor'), core={})),
            ('review_capacity_policy', 'policy', {}),
            ('problem_candidate', 'candidate', {}),
            ('forming_call', 'forming', dict(forms=[dict(id='form')])),
            ('probe_intent', 'probe', {}),
            ('execution', 'execution', dict(payload=dict(outputs=[]))),
            ('remote_intent', 'remote', {}),
        ):
            self.case.put(kind, identifier, dict(fields, id=identifier))
        report = b'{ "ok": true }\n'
        (self.case.root / '.exactory/citation-check.json').write_bytes(report)
        return report

    def write_form_outcome(self, store):
        revision = store.revision
        identifier = 'outcome-' + str(revision)
        payload = dict(id=identifier, form_id='form', target=dict(kind='form', id=None),
                       standing='unresolved', disposition=dict(status='open', reason='Keep the form open.',
                                                               form_id=None, candidate_id=None),
                       lack_of_means=None, outcome='No determination yet.', evidence=[], reason='Keep examining it.')
        path = store.root / 'outcome.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        with redirect_stdout(StringIO()):
            cli.main(['form-outcome', '--workspace', str(store.root), '--file', str(path),
                      '--request-id', identifier, '--expected-revision', str(revision)])
        marker = output_views._read_marker(store.root)
        self.assertEqual(store.revision, revision + 1)
        self.assertEqual(marker['revision'], revision + 1)
        self.assertIsNone(marker['failure'])
        self.assertIn('intermediate/problem/form-outcomes/' + identifier + '.json', marker['files'])
        return output_views._summarize(marker)

    def test_read_combine_check_and_final_keep_their_bytes(self):
        """Compare automatic renders of each recorded parts prefix and a final exercising every view group."""
        report = self.seed_view_groups()

        def compare_after_write():
            return self.compare_renders(after_write=self.write_form_outcome)

        check = self.start_check(after_turn=compare_after_write)
        self.invoke(check)
        with self.subTest(turn=check['id']):
            compare_after_write()
        final = self.assign('final', check['id'])
        self.record_final(final)
        paths = compare_after_write()
        group_paths = {
            'records': 'final/problem/statements/statement-1.json',
            'value_reviews': 'final/reviews/result/review-final.json',
            'bundles': 'final/study/manuscripts/bundle/bundle.json',
            'deposits': 'final/study/deposits/deposit/receipt.json',
            'review_series': 'intermediate/reviews/result/part-1/series.json',
            'staged_reviews': 'intermediate/reviews/staged/review_capacity_policy/policy.json',
            'review_routes': 'intermediate/reviews/routes/route/route.json',
            'review_usage': 'intermediate/reviews/usage.json',
            'problem_candidates': 'intermediate/problem/candidates/candidate.json',
            'problem_forming': 'intermediate/problem/forming/forming.json',
            'lab_probes': 'intermediate/lab/probes/probe/probe.json',
            'lab_runs': 'intermediate/lab/executions/execution.json',
            'remote': 'intermediate/study/remote-intents/remote.json',
            'score_history': 'intermediate/study/score-history.json',
            'check_reports': 'intermediate/checks/citation-check.json',
        }
        self.assertEqual(set(group_paths), {group.name for group in output_views.VIEW_GROUPS})
        for group, path in group_paths.items():
            with self.subTest(group=group):
                self.assertIn(path, paths)
        self.assertEqual(paths[group_paths['check_reports']], report)

    def test_check_keeps_a_match_and_an_unmatched_statement_on_the_same_unit(self):
        check = self.start_check()
        packet = self.packet(check)
        output = make_output(packet)
        excerpt = next(entry['content'] for entry in packet['evidence'] if entry.get('unit') == 'u1')
        output['findings'][0]['statements'] = [dict(unit='u1', text=text, why='Compare the original.')
                                              for text in (excerpt[:32], 'absent quotation')]
        result = self.invoke(check, output)
        self.assertNotIn('output_refusal', result)
        records = self.case.read_records()
        observation = parts.observe_series(records, self.case.artifacts, check['id'])[-1]
        with use_reference_functions():
            reference = parts.observe_series(records, self.case.artifacts, check['id'])[-1]
        self.assertEqual(observation['delta'], reference['delta'])
        finding = observation['delta']['findings'][0]
        self.assertEqual((len(finding['statements']), finding['unverified_statements']), (1, 1))
        self.compare_renders()

    def test_refused_raw_final_and_accepted_correction_keep_their_bytes(self):
        """Reach a repaired parts final, record two native refusals, and authenticate their correction."""
        predecessor = self.assign('predecessor')
        prior_output = self.case.build_review_output('result')
        prior_review = self.record_final(predecessor, prior_output)
        self.prepare_parts(count=3)
        binding = repair.ResultRepairJourneyTests.contaminate(self, predecessor)
        first = repair.ResultRepairJourneyTests.repair_assignment(self, predecessor, binding)
        final = repair.ResultRepairJourneyTests.finish_repair(self, first)
        self.assertEqual(final[parts.KEY]['kind'], 'final')
        raw = self.final_output()
        raw['value']['reason'] = 'The assessment now rests on the delivered search.'
        raw['reassessment'] = dict(assignment_id=predecessor['id'], findings=[
            dict(review_id=prior_review['id'], disposition='confirmed', reason='Reassess the earlier finding.',
                 evidence=raw['value']['evidence'])])
        for attempt in ('refused-one', 'refused-two'):
            self.invoke(final, raw, attempt=attempt)
            refusal = self.case.read_records()['review_output_refusal'][attempt]
            self.assertEqual(refusal['code'], 'review_reassessment_mismatch')
        delta = dict(source_output_digest=digest(raw), findings=[
            dict(review_id=prior_review['id'], disposition='revised', reason='The scientific assessment differs.')])
        with patch.object(review_protocol.transport, 'send_request',
                          side_effect=lambda route, request, credential: build_codex_response(route, request, delta)):
            result = self.case.mutate(review_output_correction.invoke_correction,
                dict(id='fix', assignment_id=final['id'], route_id=final['route_id']), credential='test-only')
        self.assertEqual(result['result']['correction_status'], 'accepted')
        selected = review_output_correction.selected_correction(
            self.case.read_records(), self.case.artifacts, final['id'])
        self.assertIsNotNone(selected)
        self.assertEqual((selected['id'], selected['attempt_id']), ('fix', 'refused-two'))
        self.assertNotEqual(selected['output'], raw)
        paths = self.compare_renders()
        turns = [turn for path, content in paths.items() if path.endswith('/series.json')
                 for turn in json.loads(content)['turns'] if turn['assignment_id'] == final['id']]
        self.assertEqual(len(turns), 1)
        run = next(run for run in turns[0]['runs'] if run['run_id'] == 'refused-two')
        self.assertTrue(run['selected'])
        self.assertEqual(run['refusal']['code'], 'review_reassessment_mismatch')
        self.assertEqual(json.loads(paths[run['output_path']]), raw)
        correction = run['correction']
        self.assertTrue(correction['selected'])
        self.assertTrue(correction['corrected_output_path'].startswith('final/'))
        self.assertEqual(json.loads(paths[correction['corrected_output_path']]), selected['output'])

    def test_context_repair_keeps_its_bytes(self):
        predecessor = self.start_series()
        self.invoke(predecessor)
        binding = repair.ResultRepairJourneyTests.contaminate(self, predecessor)
        repaired = repair.ResultRepairJourneyTests.repair_assignment(self, predecessor, binding)
        packet = self.packet(repaired)
        self.assertEqual(packet[parts.KEY]['adapter'], 'context_repair')
        self.assertNotIn('output_refusal', self.invoke(repaired, make_output(packet)))
        self.compare_renders()

    def test_a_turn_from_another_session_keeps_the_empty_listing_outcome(self):
        first = self.start_series()
        self.invoke(first)
        successor = self.assign('other-turn', first['id'])
        self.case.put('review_assignment', successor['id'], dict(successor, session_id='unrelated-session'))
        self.assertEqual(parts._list_series(self.case.read_records(), self.case.artifacts, successor['id']), [])
        paths = self.compare_renders()
        turns = [turn for path, raw in paths.items() if path.endswith('/series.json')
                 for turn in json.loads(raw)['turns'] if turn['assignment_id'] == successor['id']]
        self.assertEqual(len(turns), 1)
        self.assertFalse(turns[0]['review_parts']['complete'])
        (self.case.root / successor['packet']['path']).unlink()
        self.compare_renders(missing={successor['id']})


class V3RenderBytesTests(RenderComparison, PartsFixture):
    output_tokens = 128000

    def test_recorded_v3_dispatch_preserves_render_and_ignores_a_legacy_listing(self):
        self.oversized(count=1, characters=20, limit=1300000)
        with patch.object(review_route_capacity, 'read_profile_priors', return_value=[]), \
                patch.object(parts, 'NEW_SERIES_PROTOCOLS', (review_parts_v3.PROTOCOL, parts.PROTOCOL)):
            assignment = self.assign()
        packet = self.packet(assignment)
        self.assertEqual(packet[parts.KEY]['protocol'], review_parts_v3.PROTOCOL)
        output = v3_fixtures.make_turn_output(packet)
        header = packet[parts.KEY]
        inventory = parts.read_inventory(self.case.artifacts, header['inventory'], header['inventory_digest'])
        fragment = review_parts_v3._list_packet_fragments(packet, inventory)[0]
        output['cards'][0]['statements'] = [dict(unit=fragment['unit'], text='bounded sequence',
                                                 why='This text was delivered.')]
        self.invoke(assignment, output)
        self.compare_renders()
        records = self.case.read_records()
        artifacts = self.case.artifacts
        with use_reference_functions():
            expected = parts.observe_series(records, artifacts, assignment['id'])
        self.assertEqual(expected[0]['delta']['cards'][0]['statements'][0]['text'], 'bounded sequence')
        with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
            ordinary = parts.observe_series(records, artifacts, assignment['id'])
        listing.assert_not_called()
        with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
            observation = parts.observe_series(records, artifacts, assignment['id'], chain=[])
        listing.assert_not_called()
        self.assertEqual(ordinary, expected)
        self.assertEqual(observation, expected)
