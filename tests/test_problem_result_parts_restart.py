"""Production continuations of authenticated legacy result series."""

import copy
import json
import shutil
import tempfile
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from research_harness import review_request_evaluation

from codex_route_fixtures import limit_codex_input
from problem_parts_fixtures import ResultPartsCase
from review_parts_fixtures import PartsResultCase
from research_harness import problem_delivery, output_views, report_views
from research_harness import research_decisions as decisions
from research_harness import review_parts as parts, review_problem_parts as legacy
from research_harness import review_parts_problem_result as result_parts
from research_harness import review_problem_history as history, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.evidence import digest
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store
from research_harness.review_bodies import read_history


@contextmanager
def record_legacy():
    """Freeze only fixture creation, restoring both production entries on exit."""
    with patch.object(parts, 'prepare_packet', side_effect=lambda records, artifacts, value,
                      route, packet, prompt, replay, **kwargs: packet), \
            patch.object(legacy, 'prepare_packet', legacy._prepare_legacy_packet):
        yield


class ProblemResultRestartTests(PartsResultCase):
    output_tokens = 128000

    def create_legacy(self, count=3, characters=50000, limit=130000, reviewer='first', closed=False):
        self.oversized(count=count, characters=characters, limit=limit)
        with record_legacy():
            first = self.assign(reviewer + '-legacy', reviewer=reviewer)
            if closed:
                with patch.object(self, 'output', side_effect=lambda assignment:
                                  ResultPartsCase.output(self, assignment)):
                    _, final = self.finish_parts(first, reviewer)
                self.record_final(final)
                return final
            self.invoke(first, ResultPartsCase.output(self, first))
        return first

    def pad_frame(self, characters):
        original = protocol.build_packet

        def build(*args, **kwargs):
            packet = original(*args, **kwargs)
            packet['fixture_frame'] = 'f' * characters
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet

        return patch.object(protocol, 'build_packet', side_effect=build)

    def assert_restart(self, prior, assignment):
        packet = self.packet(assignment)
        header = packet[parts.KEY]
        self.assertEqual((header['protocol'], header['adapter'], header['kind'], header['turn']),
                         (parts.PROTOCOL, 'problem_result', 'read', 1))
        self.assertEqual(header['series_assignment_id'], assignment['id'])
        self.assertEqual(header['receipts'], [])
        self.assertEqual(packet[parts.STATE_KEY], dict(cards=[], combinations=[], findings=[]))
        self.assertEqual(read_history(self.case.artifacts, assignment), [])
        self.assertEqual((assignment['slot_id'], assignment['session_id'], assignment['history_assignment_id']),
                         (prior['slot_id'], prior['session_id'], prior['id']))
        self.assertEqual(assignment['review_parts_restart']['reason'], 'review_input_too_large')
        self.assertEqual(assignment['review_parts_restart']['series_assignment_id'],
                         self.packet(prior)[legacy.KEY]['series_assignment_id'])
        self.assertNotIn('scientific_context', packet['continuation'])
        self.assertEqual(packet['continuation']['assignment_id'], prior['id'])
        self.assertNotIn('review_parts_restart', packet)
        return packet

    def test_refusal_starts_v2_even_when_ordinary_without_replay_would_fit(self):
        with patch.object(legacy, 'PART_UNIT_CHARACTERS', 60000):
            prior = self.create_legacy()
        frozen_request = self.request(prior)
        limit_codex_input(self, 500000)
        self.assertEqual(self.request(prior), frozen_request)
        records = self.case.read_records()
        ordinary_packet = protocol.build_packet(records, self.case.artifacts, None, 'result', 'first',
                                                prior_assignment_id=prior['id'])
        ordinary_prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True)
        ordinary = transport.request_body(records['review_route']['route'], ordinary_prompt['text'], ordinary_packet, [])
        self.assertLessEqual(legacy._measure(records['review_route']['route'], ordinary), 500000)
        self.assertTrue(legacy.preserves_ordinary_packet(self.case.artifacts, ordinary_packet, ordinary))
        refusal = protocol.ResearchError('review_input_too_large', 'Fixture planner refusal.',
                                         dict(stage='part', input_characters=499000, input_limit_characters=500000))
        with patch.object(history, 'bound_history', side_effect=refusal):
            assignment = self.assign('restart', prior['id'])
        self.assert_restart(prior, assignment)
        self.assertEqual(assignment['review_parts_restart']['details'], refusal.details)
        records = self.case.read_records()
        files = output_views._build_series(records, self.case.artifacts, output_views._ViewInputs({}, {}))
        documents = [json.loads(view.content) for path, view in files.items() if path.endswith('/series.json')]
        abandoned = next(document for document in documents if document.get('review_parts_restarts'))
        self.assertEqual(abandoned['review_parts_restarts'], [dict(assignment['review_parts_restart'],
                         successor_assignment_id=assignment['id'])])
        successor = next(document for document in documents if document['root_assignment_id'] == assignment['id'])
        self.assertEqual(successor['turns'][0]['review_parts_restart'], assignment['review_parts_restart'])
        self.assertTrue(any(path.endswith('.json') and prior['id'] in path and not path.endswith('series.json')
                            for path in files))
        payload = {key: assignment[key] for key in ('id', 'dossier_id', 'role', 'route_id', 'reviewer_id', 'author_id', 'context')}
        replay = protocol.record_assignment(self.case.store, payload,
            expected_revision=assignment['recorded_revision'] - 1, request_id='task3-' + str(self.case.sequence))
        self.assertEqual(replay['result'], assignment)

    def test_current_v1_continuation_precedes_generic_missing_pair_advice(self):
        prior = self.create_legacy()
        with patch.object(problem_delivery, '_assess_minimum', return_value=([], None, None)), \
                patch.object(problem_delivery, 'compute_problem_evidence_digest', return_value=digest(self.case.expected)):
            report = problem_delivery.assess_post(self.case.read_records(), self.case.artifacts)
        self.assertEqual(report['obligations'][0]['code'], 'problem_evidence_part_pending')
        action = report['obligations'][0]['next'][0]
        self.assertEqual(action['workflow'], 'review-assignment')
        self.assertEqual(action['scope']['prior_assignment_id'], prior['id'])

    def test_contaminated_v1_turn_does_not_recommend_normal_continuation(self):
        prior = self.create_legacy()
        self.case.mutate(protocol.record_context_event, dict(id='contaminated-part', assignment_id=prior['id'],
            kind='contamination', source='author_history', reason='Synthetic author context exposure.',
            evidence=[self.case.supporting]))
        with patch.object(problem_delivery, '_assess_minimum', return_value=([], None, None)), \
                patch.object(problem_delivery, 'compute_problem_evidence_digest', return_value=digest(self.case.expected)):
            report = problem_delivery.assess_post(self.case.read_records(), self.case.artifacts)
        self.assertTrue(any(row['code'] == 'review_context_contaminated' for row in report['obligations']))
        self.assertFalse(any(action.get('workflow') == 'review-assignment'
                             for row in report['obligations'] for action in row.get('next', [])))

    def test_later_planner_refusal_starts_v2(self):
        prior = self.create_legacy()
        refusal = protocol.ResearchError('review_input_too_large', 'No next unit fits.', dict(stage='final'))
        with patch.object(history, 'bound_history', side_effect=lambda *args: args[6]), \
                patch.object(legacy, 'prepare_packet', side_effect=refusal):
            assignment = self.assign('later-restart', prior['id'])
        self.assert_restart(prior, assignment)

    def test_restart_bypasses_ordinary_request_construction(self):
        prior = self.create_legacy()
        records = self.case.read_records()
        packet = protocol.build_packet(records, self.case.artifacts, None, 'result', 'first', prior_assignment_id=prior['id'])
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True)
        value = dict(id='direct-restart', role='result', reviewer_id='first', author_id='author',
                     context=dict(prior_assignment_id=prior['id']))
        replay = transport.logical_messages(self.request(prior))
        original = transport.request_body

        def request(route, text, supplied, messages=None, order=transport.FIT_ORDER, **kwargs):
            self.assertFalse(supplied is packet and messages is replay,
                             'A restart must bypass the ordinary request fast path entirely.')
            return original(route, text, supplied, messages, order=order, **kwargs)

        with patch.object(transport, 'request_body', side_effect=request), \
                patch.object(review_request_evaluation, '_build_request', side_effect=request):
            prepared = parts.prepare_packet(records, self.case.artifacts, value, records['review_route']['route'],
                packet, prompt, replay, legacy_refusal=protocol.ResearchError('review_input_too_large', 'No progress.'))
        self.assertEqual(parts.find_kind(prepared), 'read')

    def test_unfittable_next_v1_final_restarts_at_a_fresh_read(self):
        self.oversized(count=3, characters=50000, limit=130000)
        with record_legacy(), patch.object(legacy, 'PART_UNIT_CHARACTERS', 60000):
            prior = self.assign('final-bound-1')
            for turn in range(3):
                output = ResultPartsCase.output(self, prior)
                output['notes'][0]['note'] = 'n' * 25000
                self.invoke(prior, output)
                if turn < 2:
                    prior = self.assign('final-bound-' + str(turn + 2), prior['id'])
        self.assertFalse(any(self.packet(prior)[legacy.KEY]['remaining'].values()))
        with self.pad_frame(60000):
            assignment = self.assign('final-bound-restart', prior['id'])
        self.assert_restart(prior, assignment)
        self.assertEqual(assignment['review_parts_restart']['details']['stage'], 'final')

    def test_changed_legacy_evidence_starts_normally_without_restart_diagnostics(self):
        prior = self.create_legacy()
        self.case.expected['openness_searches'][0]['search']['reason'] += ' Changed evidence.'
        assignment = self.assign('changed-evidence', prior['id'])
        self.assertIn(parts.KEY, self.packet(assignment))
        self.assertNotIn('review_parts_restart', assignment)

    def test_failed_v2_planning_does_not_commit_a_successor(self):
        prior = self.create_legacy()
        revision = self.case.store.revision
        records = self.case.read_records()
        with self.pad_frame(1000000):
            self.assert_code('review_parts_frame_too_large', lambda: self.assign('cannot-restart', prior['id']))
        self.assertEqual(self.case.store.revision, revision)
        self.assertEqual(self.case.read_records(), records)
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, prior['id'])['ready'])

    def test_fitting_legacy_continuation_has_identical_packet_prompt_history_and_request(self):
        self.create_legacy(closed=True)
        self.case.expected['openness_searches'][0]['search']['reason'] += ' Refreshed evidence.'
        with record_legacy():
            prior = self.assign('refresh-first')
            self.invoke(prior, ResultPartsCase.output(self, prior))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'snapshot'
            shutil.copytree(self.case.root, root)
            with record_legacy():
                expected = self.assign('fitting', prior['id'])
            expected_packet = self.packet(expected)
            expected_bytes = self.case.artifacts.read(expected['packet'])
            expected_history = read_history(self.case.artifacts, expected)
            expected_request = self.request(expected)
            old_store, old_artifacts = self.case.store, self.case.artifacts
            self.case.store, self.case.artifacts = Store(root), ArtifactStore(root)
            try:
                actual = self.assign('fitting', prior['id'])
                self.assertEqual(self.packet(actual), expected_packet)
                self.assertEqual(self.case.artifacts.read(actual['packet']), expected_bytes)
                self.assertEqual(actual['prompt'], expected['prompt'])
                self.assertEqual(read_history(self.case.artifacts, actual), expected_history)
                self.assertEqual(self.request(actual), expected_request)
            finally:
                self.case.store, self.case.artifacts = old_store, old_artifacts
        self.assertIn('problem_refresh', expected_packet)
        self.assertNotIn(parts.KEY, expected_packet)

    def test_non_size_refusals_and_identity_or_fork_changes_do_not_restart(self):
        prior = self.create_legacy()
        refusal = protocol.ResearchError('fixture_other_error', 'Preserve the non-size failure.')
        revision = self.case.store.revision
        with patch.object(history, 'bound_history', side_effect=refusal):
            self.assert_code('fixture_other_error', lambda: self.assign('other-error', prior['id']))
        self.assertEqual(self.case.store.revision, revision)
        records = self.case.read_records()
        packet = self.packet(prior)
        for adapter in ('openai_responses_v1', 'codex_cli_visual_v1'):
            with self.subTest(excluded_adapter=adapter):
                prepared = parts.prepare_packet(records, self.case.artifacts,
                    dict(role='result', context=dict(prior_assignment_id=prior['id'])),
                    dict(records['review_route']['route'], adapter=adapter), packet, prior['prompt'], [],
                    legacy_refusal=protocol.ResearchError('review_input_too_large', 'Excluded route.'))
                self.assertIs(prepared, packet)
        self.assert_code('review_source_continuation_mismatch', lambda: self.assign('changed-reviewer', prior['id'], 'second'))
        with record_legacy():
            self.assign('fitting', prior['id'])
        self.assert_code('review_source_continuation_fork', lambda: self.assign('fork', prior['id']))

    def test_replay_reduction_that_succeeds_stays_v1(self):
        self.oversized(count=3, characters=70000, limit=200000)
        with record_legacy(), patch.object(legacy, 'PART_UNIT_CHARACTERS', 80000):
            assignment = self.assign('closed-root')
            for turn in range(3):
                output = ResultPartsCase.output(self, assignment)
                output['notes'][0]['note'] = 'n' * 20000
                self.invoke(assignment, output)
                assignment = self.assign('closed-' + str(turn + 1), assignment['id'])
            self.record_final(assignment)
            self.case.expected['openness_searches'][0]['search']['reason'] += ' Refreshed.'
            prior = self.assign('current-root')
            self.invoke(prior, ResultPartsCase.output(self, prior))
        records = self.case.read_records()
        packet = protocol.build_packet(records, self.case.artifacts, None, 'result', 'first', prior_assignment_id=prior['id'])
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True)
        replay = transport.logical_messages(self.request(prior)) + [dict(role='assistant',
            content=json.dumps(protocol.assignment_state(records, self.case.artifacts, prior['id'])['output'], sort_keys=True))]
        value = dict(id='reduced-v1', role='result', reviewer_id='first', author_id='author',
                     dossier_id=None, route_id='route', context=dict(prior_assignment_id=prior['id']))
        route = records['review_route']['route']
        # Size the synthetic fixed frame so only omission of the closed series'
        # authenticated notes permits the next plan.
        frame = legacy._prepare_legacy_packet(records, self.case.artifacts, value, route, packet, prompt, replay)
        final_prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                                                 evidence_parts=legacy.find_stage(frame))
        minimal = [transport._elide_history_packet(message) for message in replay]
        size = legacy._measure(route, transport.request_body(route, final_prompt['text'], frame, minimal))
        padding = 201000 - size
        self.assertGreater(padding, 0)
        with self.pad_frame(padding):
            assignment = self.assign('reduced-v1', prior['id'])
        self.assertNotIn(parts.KEY, self.packet(assignment))
        self.assertNotIn('review_parts_restart', assignment)
        self.assertTrue(any('output-elided-from-replayed-history' in message['content']
                            for message in read_history(self.case.artifacts, assignment)[1::2]))

    @patch.dict(result_parts.ADAPTER, unit_rule=None)
    def test_measured_21_turn_history_restarts_and_finishes_with_search_bodies_withheld(self):
        # Reproduce the inventory rule used by the earlier withholding repair.
        final = self.create_legacy(count=2, characters=750000, limit=1300000, closed=True)
        self.oversized(count=21, characters=50000, limit=1300000)
        bodies = [self.case.artifacts.put((str(index) + ' owned body ' * 7500).encode(), 'text/plain')
                  for index in range(7)]
        computation = dict(execution=dict(id='large-computation', outputs=[dict(id='out-' + str(index), artifact=body)
                           for index, body in enumerate(bodies)]), assessment=dict(reason='c' * 270000))
        computation['assessment']['reason'] += 'c' * max(0, 272211 - len(parts.render_json(computation)))
        self.assertEqual(len(parts.render_json(computation)), 272211)
        self.case.expected['computations'] = [computation]
        captures = []
        for index, entry in enumerate(self.case.expected['openness_searches'][:5]):
            capture = self.case.artifacts.put((str(index) + ' captured search ' * 40000).encode(), 'text/plain')
            entry['sources'] = [dict(id='capture-' + str(index), response=capture)]
            captures.append(capture)
        with record_legacy(), patch.object(legacy, 'PART_UNIT_CHARACTERS', 60000):
            prior = self.assign('long-1')
            for turn in range(1, 22):
                self.assertEqual(self.packet(prior)[legacy.KEY]['turn'], turn)
                output = ResultPartsCase.output(self, prior)
                output['notes'][0]['note'] = 'n' * 50000
                self.invoke(prior, output)
                if turn < 21:
                    prior = self.assign('long-' + str(turn + 1), prior['id'])
        frozen_request = copy.deepcopy(self.request(prior))
        assignment = self.assign('long-restart', prior['id'])
        packet = self.assert_restart(prior, assignment)
        logical = parts.read_logical_packet(self.case.artifacts, packet[parts.KEY])
        entries = {entry['artifact']['sha256']: entry for entry in logical['evidence']}
        omitted = [entries[capture['sha256']] for capture in captures
                   if entries[capture['sha256']].get('delivery') == 'body-elided-route-limit']
        self.assertTrue(omitted)
        self.assertTrue(all(entry['note'] and entry['content'] == '' for entry in omitted))
        for body in bodies:
            self.assertTrue(entries[body['sha256']]['content'])
        self.assertEqual(logical['problem_evidence'], self.case.expected)
        self.assertEqual(self.request(prior), frozen_request)
        self.assertTrue(protocol.assignment_state(self.case.read_records(), self.case.artifacts, prior['id'])['ready'])
        pending = problem_delivery.assess_source_requests(self.case.read_records(), self.case.artifacts)
        self.assertFalse(any(row['code'] == 'problem_evidence_part_pending' for row in pending))
        row = next(row for row in pending if row['code'] == 'review_parts_pending')
        self.assertEqual(row['next'][0]['workflow'], 'review-run')
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, assignment['id'])
        progress = report_views.describe_parts_turn(assignment, state,
            parts.verify_series(self.case.read_records(), self.case.artifacts, assignment['id']))
        self.assertEqual(progress['units_carded'], 0)
        self.invoke(assignment)
        pending = problem_delivery.assess_source_requests(self.case.read_records(), self.case.artifacts)
        row = next(row for row in pending if row['code'] == 'review_parts_pending')
        self.assertEqual(row['next'][0]['workflow'], 'review-assignment')
        second = self.assign('restarted-read-2', assignment['id'])
        turns, final = self.finish_series(second)
        output = self.final_output()
        self.invoke(final, output)
        pending = problem_delivery.assess_source_requests(self.case.read_records(), self.case.artifacts)
        row = next(row for row in pending if row['code'] == 'review_result_pending')
        self.assertEqual(row['next'][0]['workflow'], 'value-review')
        self.assertEqual(row['next'][0]['scope']['assignment_id'], final['id'])
        review = self.case.mutate(decisions.record_value_review,
            dict(output, id='restarted-final', dossier_id=None, assignment_id=final['id']))['result']
        self.assertEqual(self.coverage(review), [])
        self.assertTrue(parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])['complete'])
        # Old v1 delivery cannot fill a missing delivery in the new series.
        changed = self.case.read_records()
        missing = assignment
        del changed['review_attempt']['attempt-' + missing['id']]
        self.assertFalse(parts.verify_series(changed, self.case.artifacts, final['id'])['complete'])
        self.assertTrue(self.coverage(review, records=changed))

    @patch.dict(result_parts.ADAPTER, unit_rule=None)
    def test_prospective_withholding_protects_unit_and_requested_bodies(self):
        from review_parts_fixtures import make_packet
        packet = make_packet(count=3, characters=100000)
        owned = self.case.artifacts.put(b'owned' * 50000, 'text/plain')
        packet['problem_evidence']['assessment_evidence'] = [owned]
        packet['evidence'] = [dict(artifact=owned, encoding='utf-8', content='owned' * 50000)]
        capture = self.case.artifacts.put(b'provenance' * 100000, 'text/plain')
        requested = self.case.artifacts.put(b'requested' * 12000, 'text/plain')
        packet['problem_evidence']['openness_searches'] = [dict(search=dict(id='capture-search'),
            sources=[dict(response=reference) for reference in (capture, owned, requested)])]
        for reference in (capture, requested):
            packet['evidence'].append(dict(artifact=reference, encoding='utf-8',
                                           content=self.case.artifacts.read(reference).decode()))
        packet['source_deliveries'] = [dict(request=dict(id='requested', kind='source', depth='fulltext',
                                                       version_id='arxiv:2601.00103v1'),
                                            status='delivered', evidence=[requested])]
        packet['source_inventory'] = dict()
        value = dict(id='protected-result', role='result', context={})
        records = self.case.read_records()
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True)
        limit_codex_input(self, 400000)
        prepared = parts.prepare_packet(records, self.case.artifacts, value, records['review_route']['route'],
                                         packet, prompt, [])
        logical = parts.read_logical_packet(self.case.artifacts, prepared[parts.KEY])
        bodies = {entry['artifact']['sha256']: entry for entry in logical['evidence']}
        self.assertEqual(bodies[capture['sha256']]['content'], '')
        self.assertTrue(bodies[owned['sha256']]['content'])
        self.assertTrue(bodies[requested['sha256']]['content'])


class CurrentResultSlotTests(PartsResultCase):
    def test_non_result_slot_summary_retains_existing_pending_occupancy(self):
        records = self.case.read_records()
        prior = records['review_assignment']['bar-first']
        records['review_assignment']['pending-bar'] = dict(prior, id='pending-bar',
            context=dict(prior_assignment_id=prior['id']), history_assignment_id=prior['id'],
            recorded_revision=self.case.store.revision + 1)
        slots = decisions.collect_problem_slot_outcomes(records, self.case.artifacts, prior['subject'], stage='bar')
        self.assertEqual(slots['occupied'], 2)
        self.assertEqual(len(slots['pending']), 1)

    @patch.dict(result_parts.ADAPTER, unit_rule=None)
    def test_later_read_withholds_newly_available_search_body(self):
        self.oversized(count=12, characters=24000, limit=200000)
        capture = self.case.artifacts.put(b'captured provenance ' * 50000, 'text/plain')
        self.case.expected['openness_searches'][0]['sources'] = [dict(response=capture)]
        original = protocol.build_packet

        def build(*args, **kwargs):
            packet = original(*args, **kwargs)
            for entry in packet['evidence']:
                if entry['artifact'] == capture:
                    entry.update(content='', delivery='body-elided-route-limit',
                                 note=transport.ELIDED_SEARCH_RESPONSE_NOTE)
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet

        # An earlier frozen turn omitted permitted provenance. The next
        # prospective packet restores its body without changing inventory units.
        with patch.object(protocol, 'build_packet', side_effect=build):
            first = self.assign('later-withholding-root')
        self.invoke(first)
        continued = self.assign('later-withholding-read', first['id'])
        self.assertEqual(continued[parts.KEY]['series_assignment_id'], first['id'])
        self.assertEqual(continued[parts.KEY]['inventory_digest'], first[parts.KEY]['inventory_digest'])
        packet = self.packet(continued)
        logical = parts.read_logical_packet(self.case.artifacts, packet[parts.KEY])
        body = next(entry for entry in logical['evidence'] if entry['artifact'] == capture)
        self.assertEqual(body['delivery'], 'body-elided-route-limit')
        self.assertEqual(body['content'], '')
        self.assertEqual(logical['problem_evidence'], self.case.expected)
        self.assertTrue(packet[parts.STATE_KEY]['cards'])

    def test_unobserved_v2_final_cannot_be_recorded(self):
        self.oversized()
        _, final = self.finish_series()
        output = self.final_output()
        self.assert_code('review_output_pending', lambda: self.case.mutate(decisions.record_value_review,
            dict(output, id='unobserved-final', dossier_id=None, assignment_id=final['id'])))

    def test_pending_successors_remove_old_finals_from_all_result_selection(self):
        self.oversized()
        saved = []
        for reviewer in ('first', 'second'):
            with record_legacy():
                assignment = self.assign(reviewer + '-old', reviewer=reviewer)
                while self.packet(assignment)[legacy.KEY]['stage'] != 'final':
                    self.invoke(assignment, ResultPartsCase.output(self, assignment))
                    assignment = self.assign(assignment['id'] + '-next', assignment['id'], reviewer)
                saved.append(self.record_final(assignment))
        subject = saved[0]['subject']
        self.case.expected['openness_searches'][0]['search']['reason'] += ' New evidence.'
        for reviewer, review in zip(('first', 'second'), saved):
            self.assign(reviewer + '-pending', reviewer=reviewer)
        records = self.case.read_records()
        self.assertEqual(decisions._collect_problem_reviews(records, 'result', subject), [])
        for identifiers in (None, [row['id'] for row in saved]):
            selected = decisions.assess_problem_reviews(records, self.case.artifacts, identifiers)
            self.assertFalse(selected['ready'])
            self.assertFalse(any(row['code'] == 'problem_rubric_below_threshold' for row in selected['obligations']))
            if identifiers is None:
                self.assertEqual(selected['review_ids'], [])
            else:
                self.assertTrue(any(row['code'] == 'review_series_superseded' for row in selected['obligations']))
        slots = decisions.collect_problem_slot_outcomes(records, self.case.artifacts, subject)
        self.assertEqual(len(slots['pending']), 2)
        self.assertEqual(slots['complete'], [])
        current, historical = output_views._find_current_value_reviews(records)
        for review in saved:
            self.assertNotIn(review['id'], current)
            self.assertIn(review['id'], historical)
            self.assertTrue(protocol.assignment_state(records, self.case.artifacts, review['assignment_id'])['ready'])
            # Previously recorded results retain idempotent returns even after replacement.
            self.case.mutate(decisions.record_value_review, review['payload'])
        files = output_views._build_value_reviews(records, self.case.artifacts, output_views._ViewInputs({}, {}))
        for review in saved:
            self.assertIn('final/history/reviews/result/' + review['id'] + '.json', files)
        self.case.put('problem_decision', 'saved-post', dict(id='saved-post', action='post',
            problem_evidence_digest=digest(self.case.expected), review_ids=[row['id'] for row in saved],
            bar_review_ids=['review-bar-first', 'review-bar-second']))
        with patch.object(problem_delivery, '_assess_minimum', return_value=([], None, None)), \
                patch.object(problem_delivery, 'compute_problem_evidence_digest', return_value=digest(self.case.expected)):
            status = problem_delivery.assess_post(self.case.read_records(), self.case.artifacts)
        self.assertTrue(any(row['code'] == 'review_series_superseded' for row in status['obligations']))

    def test_unrecorded_old_final_cannot_record_after_pending_successor(self):
        self.oversized()
        _, final = self.finish_series()
        output = self.final_output()
        review = self.record_final(final, output)
        self.case.expected['openness_searches'][0]['search']['reason'] += ' New evidence.'
        self.assign('pending-successor')
        self.assert_code('review_series_superseded', lambda: self.case.mutate(decisions.record_value_review,
                         dict(output, id='late-review', dossier_id=None, assignment_id=final['id'])))

    def test_first_pending_result_slot_is_visible_before_any_final(self):
        self.oversized()
        assignment = self.assign()
        slots = decisions.collect_problem_slot_outcomes(self.case.read_records(), self.case.artifacts,
                                                        assignment['subject'])
        self.assertEqual(slots['occupied'], 1)
        self.assertEqual(len(slots['pending']), 1)
