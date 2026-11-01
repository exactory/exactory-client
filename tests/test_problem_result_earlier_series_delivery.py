"""Restarted result series retain only authenticated deliveries of their slot."""

import copy
from unittest.mock import patch

from problem_parts_fixtures import KEY, ResultPartsCase
from research_harness import research_decisions as decisions, review_problem_parts as parts
from research_harness import review_context_repair, review_protocol as protocol, review_transport as transport
from research_harness.direction_sources import build_inline_passages, has_attachment
from research_harness.evidence import digest


class EarlierSeriesDeliveryTests(ResultPartsCase):
    def setUp(self):
        super().setUp()
        self.oversized()
        request_body = transport.request_body

        def elide_replay(route, prompt, packet, history=(), **kwargs):
            history = [transport._elide_history_packet(message) if message['role'] == 'user'
                       else dict(message, content='Earlier response omitted from replay.') for message in history]
            return request_body(route, prompt, packet, history, **kwargs)

        patcher = patch.object(transport, 'request_body', side_effect=elide_replay)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.case.add_reading_and_computation()
        self.reading = copy.deepcopy(self.case.expected['readings'][0])
        self.reference = {'kind': 'record', 'record_kind': 'reading', 'id': self.reading['id']}

    def clear_reading(self):
        self.case.expected['readings'] = []
        self.case.expected['passages'] = []

    def restart(self, prior, identifier='restarted'):
        self.case.expected['value']['motivation'][0]['text'] += ' refreshed'
        return self.assign(identifier, prior['id'])

    def finish_parts(self, first):
        assignment, turns = first, []
        while self.packet(assignment)[KEY]['stage'] == 'part':
            turns.append(assignment)
            self.assertLess(len(turns), 30)
            self.invoke(assignment)
            assignment = self.assign(first['id'] + '-turn-' + str(len(turns) + 1), assignment['id'])
        return turns, assignment

    def deliver_reading(self, first):
        assignment, turns = first, []
        while True:
            turns.append(assignment)
            self.assertLess(len(turns), 30)
            self.invoke(assignment)
            if self.packet(assignment)['problem_evidence']['readings']:
                return turns, assignment
            assignment = self.assign(first['id'] + '-turn-' + str(len(turns) + 1), assignment['id'])

    def finish_earlier(self, recorded=False):
        first = self.assign('earlier')
        turns, final = self.finish_parts(first) if recorded else self.deliver_reading(first)
        self.assertTrue(any(self.reading in packet['problem_evidence']['readings']
                            for packet in parts.collect_series_packets(
                                self.case.read_records(), self.case.artifacts, turns[-1]['id'])))
        if recorded:
            self.record_final(final)
            return turns, final
        return turns, final

    def finish_current(self, prior):
        self.clear_reading()
        first = self.restart(prior)
        _, final = self.finish_parts(first)
        self.assertNotEqual(self.packet(first)[KEY]['series_assignment_id'],
                            self.packet(prior)[KEY]['series_assignment_id'])
        self.assertEqual(self.packet(first)[KEY]['delivered_before'], [])
        return first, final

    def cited_output(self, reference=None):
        output = self.final_output()
        output['value']['evidence'] = [copy.deepcopy(self.reference if reference is None else reference)]
        return output

    def assert_records_and_audits(self, final, reference=None):
        output = self.cited_output(reference)
        self.invoke(final, output, attempt='attempt-record-' + final['id'])
        records = self.case.read_records()
        current = parts.collect_contract_packets(records, self.case.artifacts, final)
        self.assertFalse(any(row[:2] == ('reading', self.reading['id'])
                             for packet in current for row in protocol._list_packet_records(packet)))
        with patch.object(parts, 'collect_series_packets', wraps=parts.collect_series_packets) as collect:
            decisions.validate_review_contract(records, self.case.artifacts, final, output)
        self.assertNotIn(final['id'], [call.args[2] for call in collect.call_args_list])
        review = self.case.mutate(decisions.record_value_review,
            dict(output, id='review-' + final['id'], dossier_id=None, assignment_id=final['id']))['result']
        self.assertEqual(decisions.find_undelivered_review_references(
            self.case.read_records(), self.case.artifacts, review), [])
        self.assertEqual(self.coverage(review), [])
        return review

    def assert_refused(self, final, reference=None):
        output = self.cited_output(reference)
        self.invoke(final, output)
        records = self.case.read_records()
        error = self.assert_code('review_evidence_undelivered', lambda: decisions.validate_review_contract(
            records, self.case.artifacts, final, output))
        self.assertEqual(error.details['path'], '/value/evidence')
        self.assert_code('review_evidence_undelivered', lambda: self.case.mutate(decisions.record_value_review,
            dict(output, id='refused-' + final['id'], dossier_id=None, assignment_id=final['id'])))
        review = {'subject': final['subject'], 'assignment_id': final['id'], 'payload': output}
        self.assertTrue(decisions.find_undelivered_review_references(records, self.case.artifacts, review))

    def test_digestless_reading_from_recorded_earlier_final_records_and_passes_audit(self):
        _, earlier = self.finish_earlier(recorded=True)
        _, final = self.finish_current(earlier)
        self.assert_records_and_audits(final)

    def test_abandoned_series_contributes_its_delivered_prefix(self):
        _, earlier = self.finish_earlier()
        self.assertFalse(any(review['assignment_id'] == earlier['id']
                             for review in self.case.read_records()['value_review'].values()))
        _, final = self.finish_current(earlier)
        self.assert_records_and_audits(final)

    def test_reading_survives_two_restarts(self):
        _, earlier = self.finish_earlier()
        self.clear_reading()
        middle = self.restart(earlier, 'middle')
        self.invoke(middle)
        _, final = self.finish_current(middle)
        self.assert_records_and_audits(final)

    def test_saved_packet_does_not_credit_planned_but_unsent_reading(self):
        request_body, project = transport.request_body, parts._project
        planned = dict(self.reference, digest=self.reading['digest'], record=self.reading['reading'], artifacts=[])

        def omit_planned_record(route, prompt, packet, *args, **kwargs):
            packet = copy.deepcopy(packet)
            packet.pop('version_evidence', None)
            return request_body(route, prompt, packet, *args, **kwargs)

        def plan_record(*args, **kwargs):
            packet = project(*args, **kwargs)
            packet['version_evidence'] = [planned]
            packet.pop('digest')
            packet['digest'] = digest(packet)
            return packet

        patcher = patch.object(transport, 'request_body', side_effect=omit_planned_record)
        patcher.start()
        self.addCleanup(patcher.stop)
        with patch.object(parts, '_project', side_effect=plan_record):
            first = self.assign('planned')
        self.assertEqual(self.packet(first)[KEY]['remaining']['readings'], [[0, 1]])
        self.assertTrue(any(row[:2] == ('reading', self.reading['id'])
                            for row in protocol._list_packet_records(self.packet(first))))
        self.invoke(first)
        delivered = parts.collect_series_packets(self.case.read_records(), self.case.artifacts, first['id'])
        self.assertTrue(delivered)
        self.assertFalse(any(row[:2] == ('reading', self.reading['id'])
                             for packet in delivered for row in protocol._list_packet_records(packet)))
        _, final = self.finish_current(first)
        self.assert_refused(final)

    def test_credit_stops_at_each_slot_identity_boundary(self):
        _, earlier = self.finish_earlier()
        self.clear_reading()
        middle = self.restart(earlier, 'middle')
        self.invoke(middle)
        _, final = self.finish_current(middle)
        self.invoke(final, self.final_output())
        records = self.case.read_records()
        for key, value in (('reviewer_id', 'another reviewer'), ('route_id', 'another route'),
                           ('slot_id', 'another slot'), ('session_id', 'another session'),
                           ('author_id', 'another author'), ('role', 'bar')):
            with self.subTest(boundary=key):
                altered = copy.deepcopy(records)
                altered['review_assignment'][middle['id']][key] = value
                self.assert_code('review_evidence_undelivered', lambda: decisions.validate_review_contract(
                    altered, self.case.artifacts, final, self.cited_output()))

    def test_missing_ancestor_stops_credit(self):
        _, earlier = self.finish_earlier()
        first, final = self.finish_current(earlier)
        self.invoke(final, self.final_output())
        records = self.case.read_records()
        records['review_assignment'][first['id']]['context']['prior_assignment_id'] = 'missing'
        # Isolate delivery from the separate repair-lineage validator's malformed-graph refusal.
        with patch.object(review_context_repair, 'frozen_reassessment_packet', return_value=self.packet(final)):
            self.assert_code('review_evidence_undelivered', lambda: decisions.validate_review_contract(
                records, self.case.artifacts, final, self.cited_output()))

    def test_cycle_stops_before_revisiting_current_series(self):
        _, earlier = self.finish_earlier()
        self.clear_reading()
        middle = self.restart(earlier, 'middle')
        self.invoke(middle)
        first, final = self.finish_current(middle)
        self.invoke(final, self.final_output())
        records = self.case.read_records()
        records['review_assignment'][middle['id']]['context']['prior_assignment_id'] = first['id']
        with patch.object(review_context_repair, 'frozen_reassessment_packet', return_value=self.packet(final)), \
                patch.object(parts, 'collect_series_packets', wraps=parts.collect_series_packets) as collect:
            self.assert_code('review_evidence_undelivered', lambda: decisions.validate_review_contract(
                records, self.case.artifacts, final, self.cited_output()))
        self.assertNotIn(final['id'], [call.args[2] for call in collect.call_args_list])

    def test_two_delivered_digests_require_explicit_selection(self):
        _, earlier = self.finish_earlier()
        updated = copy.deepcopy(self.reading)
        updated['reading']['notes']['claims']['text'] += ' A different delivered version.'
        updated['digest'] = digest(updated['reading'])
        self.case.put('reading', updated['id'], updated['reading'])
        self.case.expected['readings'] = [updated]
        middle = self.restart(earlier, 'middle')
        turns, _ = self.deliver_reading(middle)
        _, final = self.finish_current(turns[-1])
        self.assert_refused(final)
        self.assert_code('review_evidence_undelivered', lambda: decisions.validate_review_contract(
            self.case.read_records(), self.case.artifacts, final,
            self.cited_output(dict(self.reference, digest='0' * 64))))
        self.assert_records_and_audits(final, dict(self.reference, digest=updated['digest']))

    def test_repeated_deliveries_at_one_digest_are_unambiguous(self):
        _, earlier = self.finish_earlier()
        middle = self.restart(earlier, 'middle')
        turns, _ = self.deliver_reading(middle)
        _, final = self.finish_current(turns[-1])
        self.assert_records_and_audits(final)

    def test_earlier_exact_attachment_and_bounded_passage_are_citable(self):
        native = self.reading['reading']
        passages = build_inline_passages(self.case.read_records(), self.case.artifacts, [native])
        self.case.expected['passages'] = [passage for passage, _ in passages if 'text' in passage]
        self.case.expected['readings'][0]['reading'] = protocol._reference_templates(self.reading['reading'])
        self.reading = copy.deepcopy(self.case.expected['readings'][0])
        artifact = self.case.expected['computations'][0]['execution']['outputs'][0]['artifact']
        _, earlier = self.finish_earlier()
        packets = parts.collect_series_packets(self.case.read_records(), self.case.artifacts, earlier['id'])
        self.assertTrue(any(has_attachment(self.case.artifacts, packet, artifact) for packet in packets))
        self.case.expected['computations'] = []
        _, final = self.finish_current(earlier)
        self.assert_records_and_audits(final, artifact)
        bounded = {'kind': 'source', 'link': copy.deepcopy(native['inspections'][-1]['link'])}
        decisions.validate_review_contract(self.case.read_records(), self.case.artifacts, final, self.cited_output(bounded))
        outside = copy.deepcopy(bounded)
        body = self.case.artifacts.read(bounded['link']['artifact']).decode('utf-8')
        outside['link']['locator'] = {'kind': 'text', 'start': 70, 'end': 100, 'quote': body[70:100]}
        self.assert_code('review_evidence_undelivered', lambda: decisions.validate_review_contract(
            self.case.read_records(), self.case.artifacts, final, self.cited_output(outside)))
        self.assert_code('review_evidence_undelivered', lambda: decisions.validate_review_contract(
            self.case.read_records(), self.case.artifacts, final, self.cited_output(bounded['link']['artifact'])))

    def test_unverified_imported_and_invalid_earlier_turns_supply_no_credit(self):
        _, earlier = self.finish_earlier()
        _, final = self.finish_current(earlier)
        self.invoke(final, self.final_output())
        records = self.case.read_records()
        real_state = protocol.assignment_state
        for defect in ('unverified', 'imported', 'notes', 'receipt'):
            with self.subTest(defect=defect):
                altered = copy.deepcopy(records)
                if defect == 'imported':
                    altered['review_attempt']['attempt-' + earlier['id']]['origin'] = 'imported'
                def state(saved, artifacts, identifier):
                    result = real_state(saved, artifacts, identifier)
                    if identifier == earlier['id']:
                        if defect == 'unverified':
                            result['ready'] = False
                        elif defect == 'notes':
                            result['output']['notes'] = []
                    return result
                sent_packet = parts._sent_packet
                def sent(saved, artifacts, assignment, attempt):
                    packet = sent_packet(saved, artifacts, assignment, attempt)
                    if defect == 'receipt' and assignment['id'] == earlier['id']:
                        packet[KEY]['delivered_before'] = []
                    return packet
                with patch.object(protocol, 'assignment_state', side_effect=state), \
                        patch.object(parts, '_sent_packet', side_effect=sent):
                    delivered = parts.collect_earlier_series_packets(altered, self.case.artifacts, final)
                self.assertFalse(any(row[:2] == ('reading', self.reading['id'])
                                     for packet in delivered for row in protocol._list_packet_records(packet)))

    def test_same_digest_earlier_series_cannot_supply_current_reading_or_artifact(self):
        _, earlier = self.finish_earlier(recorded=True)
        with patch.object(protocol, '_is_problem_refresh', return_value=True):
            first = self.assign('same-evidence-refresh')
        _, final = self.finish_parts(first)
        self.assertEqual(self.packet(first)['problem_evidence_digest'], self.packet(earlier)['problem_evidence_digest'])
        self.assertNotEqual(self.packet(first)[KEY]['series_assignment_id'], self.packet(earlier)[KEY]['series_assignment_id'])
        review = self.record_final(final)
        self.assertEqual(self.coverage(review), [])
        original = parts.collect_series_packets
        current = original(self.case.read_records(), self.case.artifacts, final['id'])
        artifact = self.case.expected['computations'][0]['execution']['outputs'][0]['artifact']
        for missing in ('reading', 'artifact'):
            with self.subTest(missing=missing):
                altered = copy.deepcopy(current)
                for packet in altered:
                    if missing == 'reading':
                        packet['problem_evidence']['readings'] = []
                        packet[KEY]['delivered_now']['readings'] = []
                    else:
                        for entry in packet['evidence']:
                            if entry['artifact'] == artifact:
                                entry.update(content='', encoding='omitted')
                def collect(saved, artifacts, identifier):
                    return altered if identifier == final['id'] else original(saved, artifacts, identifier)
                with patch.object(parts, 'collect_series_packets', side_effect=collect):
                    pending = self.coverage(review)
                self.assertEqual([row['code'] for row in pending], ['problem_evidence_unassessed'])
                if missing == 'reading':
                    self.assertIn(self.reading['id'], pending[0]['reading_ids'])
                else:
                    self.assertIn(artifact['sha256'], pending[0]['artifact_digests'])

    def test_earlier_deliveries_cannot_fill_missing_current_unit_or_receipt(self):
        _, earlier = self.finish_earlier()
        _, final = self.finish_current(earlier)
        review = self.assert_records_and_audits(final)
        records = self.case.read_records()
        original = parts.collect_series_packets
        current = original(records, self.case.artifacts, final['id'])
        for missing in ('unit', 'receipt'):
            with self.subTest(missing=missing):
                altered = copy.deepcopy(current)
                if missing == 'unit':
                    for packet in altered:
                        packet['problem_evidence']['openness_searches'] = []
                        packet[KEY]['delivered_now']['openness_searches'] = []
                else:
                    altered.pop(0)
                def collect(saved, artifacts, identifier):
                    return altered if identifier == final['id'] else original(saved, artifacts, identifier)
                with patch.object(parts, 'collect_series_packets', side_effect=collect):
                    pending = self.coverage(review)
                self.assertEqual([row['code'] for row in pending], ['problem_evidence_unassessed'])
                self.assertIn('openness_searches', pending[0]['missing'])

    def test_earlier_objections_and_bars_do_not_expand_current_contract(self):
        add_objections = protocol.add_problem_objections

        def add_earlier_objection(*args):
            add_objections(*args)
            args[2]['bars'].append(dict(args[2]['bars'][0], id='earlier-only-bar'))
            args[2]['material_objections'].append({'id': 'earlier-only-objection', 'claim': 'An old limitation.',
                'material_version': '1' * 64, 'version_evidence': [], 'version_source_mappings': []})

        with patch.object(protocol, 'add_problem_objections', side_effect=add_earlier_objection):
            _, earlier = self.finish_earlier()
        _, final = self.finish_current(earlier)
        review = self.assert_records_and_audits(final)
        output = copy.deepcopy(review['payload'])
        output.pop('id')
        output.pop('dossier_id')
        output.pop('assignment_id')
        output['value']['objection_findings'].append({'id': 'earlier-only-objection', 'status': 'continuing',
            'reason': 'This old finding is outside the current contract.', 'evidence': [self.reference]})
        self.assert_code('review_objection_history_incomplete', lambda: decisions.validate_review_contract(
            self.case.read_records(), self.case.artifacts, final, output))
        output = self.cited_output()
        output['value']['bar_ids'] = ['review-bar-first']
        self.assert_code('review_bar_pending', lambda: decisions.validate_review_contract(
            self.case.read_records(), self.case.artifacts, final, output))
        output = self.cited_output()
        output['value']['bar_ids'].append('earlier-only-bar')
        self.assert_code('review_bar_mismatch', lambda: decisions.validate_review_contract(
            self.case.read_records(), self.case.artifacts, final, output))
