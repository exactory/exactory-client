"""Owned bodies can arrive across authenticated part turns without losing item coverage."""

import copy
import json
from unittest.mock import patch

from problem_parts_fixtures import KEY, ResultPartsCase
from research_harness import review_problem_parts as parts, review_protocol as protocol
from research_harness import review_excerpts, review_transport as transport
from research_harness import research_decisions as decisions, review_supplied_evidence as supplied
from research_harness import review_objection_evidence as objection_evidence
from research_harness.cli import OPERATIONS
from research_harness.direction_sources import has_attachment
from research_harness.evidence import digest


class ProblemResultPartsContinuedTests(ResultPartsCase):
    def continued_unit(self, duplicates=1, aliases=False, body_characters=4000, limit=42000):
        self.oversized(count=0, limit=limit)
        if aliases:
            body = self.case.artifacts.put(b'a' * body_characters, 'text/plain')
            references = [body, dict(body, media_type='text/plain; charset=utf-8'),
                          dict(body, media_type='text/plain; charset=ascii')]
        else:
            references = [self.case.artifacts.put(bytes([97 + i]) * body_characters, 'text/plain')
                          for i in range(3)]
        item = {'execution': {'id': 'large-execution',
                             'outputs': [{'id': str(i), 'artifact': ref} for i, ref in enumerate(references)]},
                'assessment': {'reason': 'c' * 22000}}
        self.case.expected['computations'] = [copy.deepcopy(item) for _ in range(duplicates)]
        return references

    def final_output(self):
        from research_harness.review_refusal import evidence_arrays
        output = self.case.build_review_output('result')
        reference = self.case.expected['computations'][0]['execution']['outputs'][0]['artifact']
        for array in evidence_arrays(output):
            array[:] = [copy.deepcopy(reference)]
        return output

    def sent(self, assignment):
        return review_excerpts.read_sent_packet(self.request(assignment))

    def add_material_objection(self, body_characters=60000):
        body = self.case.artifacts.put(b'o' * body_characters, 'text/plain')
        alias = dict(body, media_type='text/plain; charset=utf-8')
        objection = {'id': 'inherited-objection', 'claim': 'A material limitation.',
                     'material_version': '1' * 64, 'version_evidence': [body,
                         {'kind': 'record', 'record_kind': 'local_artifact', 'id': 'objection-record',
                          'record': {}, 'artifacts': [alias]}], 'version_source_mappings': []}
        self.install_material_objections([objection])
        return objection, [body, alias]

    def install_material_objections(self, objections):
        real = protocol.add_problem_objections

        def add_objection(*args):
            real(*args)
            args[2]['material_objections'] = copy.deepcopy(objections)

        patcher = patch.object(protocol, 'add_problem_objections', side_effect=add_objection)
        patcher.start()
        self.addCleanup(patcher.stop)

    def shared_objections(self):
        body = self.case.artifacts.put(b'Shared objection body.' * 50, 'text/plain')
        alias = dict(body, media_type='text/plain; charset=utf-8')
        stored = {'id': 'shared-objection-record', 'artifact': body, 'reason': 'r' * 18000}
        self.case.put('local_artifact', stored['id'], stored)
        record = {'kind': 'record', 'record_kind': 'local_artifact', 'id': stored['id'],
                  'digest': digest(stored), 'record': protocol._reference_templates(stored), 'artifacts': [alias]}
        link = self.case.supporting_links[self.case.supporting['sha256']]
        mapping = {'version_id': link['version_id'], 'excerpt': self.case.supporting,
                   'origin': {'source_id': link['source_id'],
                              'locator': dict(link['locator'], sha256=self.case.supporting['sha256']),
                              'artifact_descriptor': protocol._reference_templates(link['artifact'])['artifact_descriptor']}}
        objections = [{'id': 'shared-' + str(index), 'stated_id': 'shared-' + str(index),
                       'claim': 'A shared material limitation.', 'material_version': str(index + 1) * 64,
                       'version_evidence': [body, record, alias], 'version_source_mappings': [mapping]}
                      for index in range(3)]
        return objections, [body, alias, self.case.supporting]

    def project_objections(self, objections, stage='final'):
        records = self.case.read_records()
        packet = protocol.build_packet(records, self.case.artifacts, None, 'result', 'first')
        packet['material_objections'] = copy.deepcopy(objections)
        before = copy.deepcopy(packet)
        projected = parts._project(records, self.case.artifacts, packet, [], [], [], [], 'series', stage, 1)
        self.assertEqual(packet, before)
        return projected

    def test_final_deduplicates_entries_in_objection_and_field_order_before_digest(self):
        objections, _ = self.shared_objections()
        # Canonical equality ignores dictionary insertion order. It preserves descriptor aliases.
        objections[0]['version_evidence'].append(dict(reversed(list(objections[0]['version_evidence'][0].items()))))
        objections[0]['version_source_mappings'].append(copy.deepcopy(objections[0]['version_evidence'][1]))
        objections[1]['version_evidence'].append(copy.deepcopy(objections[0]['version_source_mappings'][0]))
        packet = self.project_objections(objections)
        note = ('This reference stands in for the full entry, which is in material_objections of this packet under '
                'the objection that objection_id names, in the list that field names, at the position that index '
                'gives. The full entry belongs to the evidence of the objection that contains this note. The citation '
                'rules of the prompt apply to the full entry, and this reference is not a citation.')
        self.assertEqual(objection_evidence.REPEATED_OBJECTION_EVIDENCE_NOTE, note)
        first = objections[0]

        def repeated(field, index):
            return {'delivery': 'repeated-in-material-objections', 'objection_id': 'shared-0',
                    'field': field, 'index': index, 'digest': digest(first[field][index]), 'note': note}

        body, record, alias = first['version_evidence'][:3]
        mapping = first['version_source_mappings'][0]
        body_repeat = repeated('version_evidence', 0)
        record_repeat = repeated('version_evidence', 1)
        alias_repeat = repeated('version_evidence', 2)
        mapping_repeat = repeated('version_source_mappings', 0)
        expected = [
            dict(first, version_evidence=[body, record, alias, body_repeat],
                 version_source_mappings=[mapping, record_repeat]),
            dict(objections[1], version_evidence=[body_repeat, record_repeat, alias_repeat, mapping_repeat],
                 version_source_mappings=[mapping_repeat]),
            dict(objections[2], version_evidence=[body_repeat, record_repeat, alias_repeat],
                 version_source_mappings=[mapping_repeat])]
        self.assertEqual(packet['material_objections'], expected)
        full = [entry for objection in packet['material_objections'] for field in objection_evidence.FIELDS
                for entry in objection[field] if entry.get('delivery') != objection_evidence.REPEATED]
        self.assertEqual(full, [body, record, alias, mapping])
        self.assertEqual(packet['digest'], digest({key: value for key, value in packet.items() if key != 'digest'}))

    def test_part_projection_still_omits_all_objection_evidence(self):
        objections, references = self.shared_objections()
        packet = self.project_objections(objections, 'part')
        stripped = [{key: value for key, value in objection.items() if key not in objection_evidence.FIELDS}
                    for objection in objections]
        self.assertEqual(packet['material_objections'], stripped)
        self.assertEqual(packet, self.project_objections(stripped, 'part'))
        self.assertFalse(any(has_attachment(self.case.artifacts, packet, ref) for ref in references[:2]))

    def test_objection_readers_resolve_full_records_mappings_and_nested_objections(self):
        objections, references = self.shared_objections()
        record, mapping = objections[0]['version_evidence'][1], objections[0]['version_source_mappings'][0]
        nested = {'id': 'nested-qualified', 'stated_id': 'nested', 'claim': 'A nested claim.'}
        record['record']['objections'] = [nested]
        # Put each full entry in the other field so a reader must resolve the later entry to find it.
        objections[0]['version_evidence'] = [mapping]
        objections[0]['version_source_mappings'] = [record]
        objections[1]['version_evidence'] = [copy.deepcopy(record)]
        objections[1]['version_source_mappings'] = [copy.deepcopy(mapping)]
        packet = self.project_objections(objections[:2])
        repeated = packet['material_objections'][1]
        self.assertEqual(objection_evidence.resolved_objection_entries(packet, repeated, 'version_evidence'), [record])
        self.assertEqual(objection_evidence.resolved_objection_entries(packet, repeated, 'version_source_mappings'), [mapping])
        self.assertIn(('local_artifact', record['id'], record['digest'], record['record'], record['artifacts']),
                      list(protocol._list_packet_records(packet)))
        self.assertIn(nested, list(supplied.collect_qualified_objections(packet)))
        with patch.object(supplied, 'lineage_packets', return_value=[packet]):
            received, mappings = supplied.supplied_context(self.case.read_records(), self.case.artifacts, [], frozen=True)
            _, current_mappings = supplied.supplied_context(self.case.read_records(), self.case.artifacts, [])
        self.assertEqual(mappings, [mapping])
        self.assertEqual(current_mappings, [supplied.deliver_passage(self.case.read_records(), self.case.artifacts, mapping)])
        self.assertIn(references[1], received.values())
        self.assertEqual(decisions._visual_supplied_context(self.case.read_records(), self.case.artifacts, [packet])[1], [mapping])

    def test_repeated_objection_entry_rejects_another_digest_or_missing_full_target(self):
        objections, _ = self.shared_objections()
        packet = self.project_objections(objections)
        entry = packet['material_objections'][1]['version_evidence'][0]
        for change in ({'digest': '0' * 64}, {'objection_id': 'absent'}, {'index': -1},
                       {'field': 'evidence'}, {'index': 100}, {'objection_id': 'shared-1'}):
            with self.subTest(change=change):
                self.assert_code('review_objection_evidence_invalid', lambda: objection_evidence.resolve_objection_entry(
                    packet, dict(entry, **change)))

    def test_final_fits_only_after_deduplication_and_delivers_distinct_entries_and_bodies(self):
        self.continued_unit()
        objections, references = self.shared_objections()
        self.install_material_objections(objections)
        last = self.deliver_units()
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            final = self.assign('compact-final', last['id'])
        self.assertEqual(fit.call_args.args[-1], references[:2])
        request = self.request(final)
        route = self.case.read_records()['review_route']['route']
        uncompressed = self.packet(final)
        uncompressed['material_objections'] = objections
        uncompressed['digest'] = digest({key: value for key, value in uncompressed.items() if key != 'digest'})
        full_size = parts._measure(route, transport.request_body(route, final['prompt']['text'], uncompressed, final['history']))
        compact_size = parts._measure(route, request)
        self.assertGreater(full_size, compact_size + 20000)
        limit = transport.local_input_limit(route)
        self.assertLessEqual(compact_size, limit)
        self.assertGreater(full_size, limit)
        self.assertFalse(parts._fits(self.case.artifacts, route, final['prompt'], uncompressed, final['history'], references)[0])
        output = self.final_output()
        output['value']['objection_findings'] = [{'id': objection['id'], 'status': 'continuing',
            'reason': 'The shared delivered record retains this limitation.', 'evidence': [
                {key: value for key, value in objections[0]['version_evidence'][1].items()
                 if key in ('kind', 'record_kind', 'id', 'digest')}, references[1]]} for objection in objections]
        review = self.record_final(final, output)
        records = self.case.read_records()
        actual = json.loads(self.case.artifacts.read(records['review_attempt']['attempt-' + final['id']]['request']))
        sent = review_excerpts.read_sent_packet(actual)
        self.assertEqual(sent['material_objections'], self.packet(final)['material_objections'])
        for original, delivered in zip(objections, sent['material_objections']):
            for field in objection_evidence.FIELDS:
                self.assertEqual(objection_evidence.resolved_objection_entries(sent, delivered, field), original[field])
        self.assertTrue(all(has_attachment(self.case.artifacts, sent, reference) for reference in references))
        for reference in references:
            self.assertEqual(sum(entry['artifact'] == reference for entry in sent['evidence']), 1)
        self.assertLessEqual(parts._measure(route, actual), limit)
        self.assertEqual(decisions.find_undelivered_review_references(records, self.case.artifacts, review), [])
        self.assertEqual(self.coverage(review), [])

    def test_final_records_digestless_citations_carried_with_material_objections(self):
        self.continued_unit()
        openness = copy.deepcopy(self.case.expected['openness'])
        self.case.put('problem_openness', openness['id'], openness)
        self.case.expected['openness']['digest'] = digest(openness)
        value = copy.deepcopy(self.case.expected['value'])
        self.case.put('problem_value', value['id'], value)
        references = [{'kind': 'record', 'record_kind': kind, 'id': record['id']}
                      for kind, record in (('problem_openness', openness), ('problem_value', value))]
        evidence = [dict(reference, record=record, artifacts=[])
                    for reference, record in zip(references, (openness, value))]
        objections = [{'id': 'record-objection-' + str(index), 'claim': 'A delivered record needs qualification.',
                       'material_version': str(index + 1) * 64, 'version_evidence': copy.deepcopy(evidence),
                       'version_source_mappings': []} for index in range(2)]
        self.install_material_objections(objections)
        turns, final = self.finish_parts()
        self.assertTrue(turns)
        packet = self.packet(final)
        self.assertEqual(packet[KEY]['stage'], 'final')
        self.assertTrue(all(entry['delivery'] == objection_evidence.REPEATED
                            for entry in packet['material_objections'][1]['version_evidence']))
        output = self.final_output()
        output['value']['evidence'] = copy.deepcopy(references)
        output['value']['objection_findings'] = [{'id': objection['id'], 'status': 'continuing',
            'reason': 'The delivered records retain this limitation.', 'evidence': copy.deepcopy(references)}
            for objection in objections]
        self.invoke(final, output)
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-' + final['id']]
        request = json.loads(self.case.artifacts.read(attempt['request']))
        sent = review_excerpts.read_sent_packet(request)
        carried = list(protocol._list_packet_records(sent))
        self.assertEqual({row[2] for row in carried if row[:2] == ('problem_openness', openness['id'])},
                         {digest(openness), None})
        value_rows = [row for row in carried if row[:2] == ('problem_value', value['id'])]
        self.assertGreater(len(value_rows), 1)
        self.assertEqual({row[2] for row in value_rows}, {None})
        self.assertEqual(len({digest(row[3]) for row in value_rows}), 1)
        payload = dict(output, id='review-' + final['id'], dossier_id=None, assignment_id=final['id'])
        review = self.case.mutate(OPERATIONS['value-review'], payload)['result']
        self.assertEqual(review['payload'], payload)
        self.assertTrue(all('digest' not in reference for reference in review['payload']['value']['evidence']))
        records = self.case.read_records()
        self.assertEqual(records['value_review'][review['id']], review)
        self.assertEqual(decisions.find_undelivered_review_references(records, self.case.artifacts, review), [])
        self.assertEqual(self.coverage(review), [])

    def test_final_requires_body_when_full_record_is_in_source_mappings(self):
        self.continued_unit()
        objections, _ = self.shared_objections()
        record = objections[0]['version_evidence'][1]
        body = self.case.artifacts.put(b'Required cross-field body.' * 3000, 'text/plain')
        record['artifacts'] = [body]
        objections[0]['version_evidence'] = []
        objections[0]['version_source_mappings'] = [record]
        for objection in objections[1:]:
            objection['version_evidence'] = [copy.deepcopy(record)]
            objection['version_source_mappings'] = []
        self.install_material_objections(objections)
        last = self.deliver_units()
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            error = self.assert_code('review_input_too_large', lambda: self.assign('final', last['id']))
        self.assertEqual((error.details['stage'], error.details['unit']), ('final', None))
        self.assertEqual(fit.call_args.args[-1], [body])
        artifacts, route, prompt, packet, history, _ = fit.call_args.args
        sent = review_excerpts.read_sent_packet(transport.request_body(route, prompt['text'], packet, history))
        self.assertTrue(has_attachment(artifacts, packet, body))
        self.assertFalse(has_attachment(artifacts, sent, body))

    def test_final_source_reply_deduplicates_objections_and_credits_earlier_final_bodies(self):
        self.oversized(characters=90000, limit=500000)
        self.case.add_reading_and_computation()
        objections, references = self.shared_objections()
        self.install_material_objections(objections)
        last = self.deliver_units()
        final = self.assign('final', last['id'])
        output = self.case.build_review_output('result')
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [{'id': 'read-source', 'kind': 'source', 'depth': 'fulltext',
                                      'version_id': self.case.expected['readings'][0]['version_id']}]
        self.invoke(final, output)
        reply = self.assign('source-reply', final['id'])
        sent = self.sent(reply)
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertTrue(sent['source_deliveries'])
        self.assertEqual(sent['pending_source_requests'], [])
        self.assertEqual(sent['material_objections'], self.packet(final)['material_objections'])
        earlier = parts._artifact_turns(parts.observe_series(
            self.case.read_records(), self.case.artifacts, final['id']))
        for reference in references[:2]:
            self.assertFalse(has_attachment(self.case.artifacts, sent, reference))
            self.assertEqual([entry for entry in sent['evidence'] if entry['artifact'] == reference],
                [{'artifact': reference, 'content': '', 'encoding': 'omitted',
                  'delivery': 'delivered-in-an-earlier-part-turn', 'turn': earlier[digest(reference)],
                  'note': parts.OMITTED_BODY_NOTE}])
        self.assertIn(digest(references[2]), earlier)
        self.assertTrue(has_attachment(self.case.artifacts, sent, references[2]))
        self.assertTrue(all(has_attachment(self.case.artifacts, sent, reference)
                            for reference in protocol.collect_references(sent['source_deliveries'])))

    def test_recorded_final_with_full_objections_is_not_rewritten(self):
        self.continued_unit(duplicates=3, limit=150000)
        for item in self.case.expected['computations']:
            item['assessment']['reason'] = 'c' * 90000
        objections, _ = self.shared_objections()
        self.install_material_objections(objections)
        last = self.deliver_units()
        with patch.object(parts, 'deduplicate_material_objections'):
            final = self.assign('legacy-final', last['id'])
        frozen = self.case.artifacts.read(final['packet'])
        output = self.final_output()
        output['value']['objection_findings'] = [{'id': objection['id'], 'status': 'continuing',
            'reason': 'This remains a limitation.', 'evidence': [objections[0]['version_evidence'][0]]}
            for objection in objections]
        self.invoke(final, output)
        self.assertTrue(protocol.assignment_state(self.case.read_records(), self.case.artifacts, final['id'])['ready'])
        self.assertEqual(self.sent(final)['material_objections'], objections)
        self.assertEqual(self.case.artifacts.read(final['packet']), frozen)

    def deliver_units(self):
        assignment = self.assign()
        for turn in range(1, 31):
            packet = self.packet(assignment)
            self.assertEqual(packet[KEY]['stage'], 'part')
            self.invoke(assignment)
            if not any(packet[KEY]['remaining'].values()):
                return assignment
            assignment = self.assign('part-' + str(turn + 1), assignment['id'])
        self.fail('Each part must advance the finite unit queue')

    def test_final_refuses_when_not_even_one_objection_body_fits(self):
        self.continued_unit()
        _, references = self.add_material_objection()
        last = self.deliver_units()
        revision = self.case.store.revision
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            error = self.assert_code('review_input_too_large', lambda: self.assign('final', last['id']))
        self.assertEqual((error.details['stage'], error.details['unit']), ('final', None))
        self.assertEqual(self.case.store.revision, revision)
        final_call = next(call for call in fit.call_args_list if parts.find_stage(call.args[3]) == 'final')
        artifacts, route, prompt, packet, history, attached = final_call.args
        self.assertEqual(attached, references)
        request = transport.request_body(route, prompt['text'], packet, history)
        self.assertLessEqual(parts._measure(route, request), transport.local_input_limit(route))
        self.assertTrue(all(has_attachment(artifacts, packet, reference) for reference in references))
        self.assertFalse(all(has_attachment(artifacts, review_excerpts.read_sent_packet(request), reference)
                             for reference in references))
        body_calls = [call for call in fit.call_args_list if parts.find_stage(call.args[3]) == 'part']
        self.assertEqual([call.args[-1] for call in body_calls], [references, references[:1]])
        for call in body_calls:
            self.assertEqual(call.args[3][KEY]['objection_bodies'], call.args[-1])
            self.assertFalse(parts._fits(*call.args)[0])

    def objection_body_series(self, body_characters=24000):
        self.continued_unit(duplicates=4, limit=80000)
        bodies = [self.case.artifacts.put(bytes([100 + index]) * body_characters, 'text/plain')
                  for index in range(3)]
        objection = {'id': 'body-only-objection', 'claim': 'A material limitation.', 'material_version': '1' * 64,
                     'version_evidence': [{'kind': 'record', 'record_kind': 'local_artifact', 'id': 'body-record',
                                          'record': {'reason': 'r' * 22000}, 'artifacts': bodies}],
                     'version_source_mappings': []}
        self.install_material_objections([objection])
        return objection, bodies, self.deliver_units()

    def test_body_only_part_delivers_largest_fitting_prefix_then_final_credits_it(self):
        objection, bodies, last = self.objection_body_series()
        records, artifacts = self.case.read_records(), self.case.artifacts
        earlier = parts.observe_series(records, artifacts, last['id'])
        self.assertTrue(earlier)
        self.assertTrue(all(digest(body) not in parts._artifact_turns(earlier) for body in bodies))
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            body_part = self.assign('body-part', last['id'])
        final_call, full_call, prefix_call = fit.call_args_list
        self.assertEqual(parts.find_stage(final_call.args[3]), 'final')
        self.assertEqual(final_call.args[-1], bodies)
        self.assertFalse(parts._fits(*final_call.args)[0])
        # The same final frame fits if its objection bodies are left out.
        frame = copy.deepcopy(final_call.args[3])
        frame['evidence'] = [entry for entry in frame['evidence'] if entry['artifact'] not in bodies]
        frame['digest'] = digest({key: value for key, value in frame.items() if key != 'digest'})
        self.assertTrue(parts._fits(artifacts, final_call.args[1], final_call.args[2], frame,
                                    final_call.args[4], [])[0])
        self.assertEqual(full_call.args[-1], bodies)
        self.assertFalse(parts._fits(*full_call.args)[0])
        self.assertEqual(prefix_call.args[-1], bodies[:2])
        self.assertTrue(parts._fits(*prefix_call.args)[0])
        packet, sent = self.packet(body_part), self.sent(body_part)
        plan = packet[KEY]
        self.assertEqual(plan['stage'], 'part')
        self.assertEqual(plan['series_assignment_id'], self.packet(last)[KEY]['series_assignment_id'])
        self.assertEqual(plan['turn'], len(earlier) + 1)
        self.assertEqual(plan['delivered_before'], [row['receipt'] for row in earlier])
        self.assertEqual(plan['objection_bodies'], bodies[:2])
        self.assertEqual(sent[KEY], plan)
        for frame in (packet, sent):
            self.assertEqual(parts._delivered_units(frame), [])
            for key in parts.SPLIT_KEYS:
                self.assertEqual(frame['problem_evidence'][key], [])
                self.assertEqual(frame[KEY]['delivered_now'][key], [])
                self.assertEqual(frame[KEY]['remaining'][key], [])
            self.assertTrue(all(has_attachment(artifacts, frame, body) for body in bodies[:2]))
            self.assertFalse(has_attachment(artifacts, frame, bodies[2]))
        self.assertFalse(parts._makes_progress([], bodies[:2], earlier))
        self.assertTrue(parts.has_new_objection_body(sent, bodies[:2], earlier))
        self.assertFalse(parts.has_new_objection_body(sent, bodies[2:], earlier))
        output = {'stage': 'result', 'phase': 'evidence_part', 'part': plan['turn'], 'notes': []}
        self.assertEqual(self.output(body_part), output)
        self.invoke(body_part, output)
        records = self.case.read_records()
        self.assertTrue(protocol.assignment_state(records, artifacts, body_part['id'])['ready'])
        observed = parts.observe_series(records, artifacts, body_part['id'])
        self.assertEqual(observed[:-1], earlier)
        self.assertEqual(observed[-1]['units'], [])
        self.assertEqual(observed[-1]['receipt'], {'turn': plan['turn'], 'assignment_id': body_part['id'],
                         'attempt_id': 'attempt-' + body_part['id'], 'units': plan['delivered_now']})
        self.assertTrue(all(body in observed[-1]['artifacts'] for body in bodies[:2]))
        self.assertNotIn(bodies[2], observed[-1]['artifacts'])
        self.assertFalse(parts.has_new_objection_body(sent, bodies[:2], observed))
        final = self.assign('final', body_part['id'])
        request, sent = self.request(final), self.sent(final)
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertEqual(sent[KEY]['series_assignment_id'], plan['series_assignment_id'])
        self.assertEqual(sent[KEY]['turn'], plan['turn'] + 1)
        self.assertEqual(sent[KEY]['delivered_before'], [row['receipt'] for row in observed])
        self.assertEqual(sent['material_objections'], [objection])
        self.assertEqual(parts.find_credited_objection_bodies(sent, parts._artifact_turns(observed)),
                         {digest(body) for body in bodies[:2]})
        for body in bodies[:2]:
            self.assertFalse(has_attachment(artifacts, sent, body))
            self.assertEqual([entry for entry in sent['evidence'] if entry['artifact'] == body],
                [{'artifact': body, 'content': '', 'encoding': 'omitted',
                  'delivery': 'delivered-in-an-earlier-part-turn', 'turn': plan['turn'],
                  'note': parts.OMITTED_BODY_NOTE}])
        self.assertTrue(has_attachment(artifacts, sent, bodies[2]))
        route = records['review_route']['route']
        self.assertLessEqual(parts._measure(route, request), transport.local_input_limit(route))
        self.assertTrue(parts._fits(artifacts, route, final['prompt'], self.packet(final), final['history'],
                                    bodies[2:])[0])

    def test_invalid_body_only_part_response_gives_no_objection_body_credit(self):
        _, bodies, last = self.objection_body_series(body_characters=16000)
        artifacts = self.case.artifacts
        earlier = parts.observe_series(self.case.read_records(), artifacts, last['id'])
        body_part = self.assign('invalid-body-part', last['id'])
        packet = self.packet(body_part)
        self.assertEqual(packet[KEY]['stage'], 'part')
        self.assertEqual(packet[KEY]['objection_bodies'], bodies)
        self.assertEqual(parts._delivered_units(packet), [])
        self.assertTrue(all(has_attachment(artifacts, self.sent(body_part), body) for body in bodies))
        output = dict(self.output(body_part), notes=[{'unit': {'key': 'computations', 'index': 0},
                                                    'note': 'No computation was delivered in this turn.'}])
        self.assert_code('invalid_value_review', lambda: parts.validate_part_output(packet, output))
        self.invoke(body_part, output)
        records = self.case.read_records()
        self.assertFalse(protocol.assignment_state(records, artifacts, body_part['id'])['ready'])
        observed = parts.observe_series(records, artifacts, body_part['id'])
        self.assertEqual(observed, earlier)
        full = protocol.build_packet(records, artifacts, None, 'result', 'first')
        self.assertEqual(parts.find_credited_objection_bodies(full, parts._artifact_turns(observed)), set())
        value = {'id': 'retry-body-part', 'role': 'result', 'context': {'prior_assignment_id': body_part['id']}}
        planned = parts.prepare_packet(records, artifacts, value, records['review_route']['route'], full,
                                       body_part['prompt'], body_part['history'])
        self.assertEqual(planned[KEY]['stage'], 'part')
        self.assertEqual(planned[KEY]['turn'], packet[KEY]['turn'])
        self.assertEqual(planned[KEY]['objection_bodies'], bodies)
        revision = self.case.store.revision
        self.assert_code('review_source_continuation_unverified', lambda: self.assign('next', body_part['id']))
        self.assertEqual(self.case.store.revision, revision)

    def test_fitting_final_attaches_every_objection_descriptor_in_the_actual_request(self):
        self.continued_unit()
        objection, references = self.add_material_objection(body_characters=6000)
        last = self.deliver_units()
        final = self.assign('final', last['id'])
        self.assertEqual(self.packet(final)['material_objections'], [objection])
        output = self.final_output()
        output['value']['objection_findings'] = [{'id': objection['id'], 'status': 'continuing',
            'reason': 'The delivered evidence retains this limitation.', 'evidence': [references[0]]}]
        self.invoke(final, output)
        records = self.case.read_records()
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, final['id'])['ready'])
        request = json.loads(self.case.artifacts.read(records['review_attempt']['attempt-final']['request']))
        sent = review_excerpts.read_sent_packet(request)
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertTrue(all(has_attachment(self.case.artifacts, sent, reference) for reference in references))

    def test_final_credits_current_series_objection_bodies_and_records_their_citations(self):
        references = self.continued_unit()
        alias = dict(references[0], media_type='text/plain; charset=utf-8')
        fresh = self.case.artifacts.put(b'New objection evidence for the final.', 'text/plain')
        objection = {'id': 'shared-with-parts', 'claim': 'A material limitation.', 'material_version': '1' * 64,
                     'version_evidence': [references[0],
                         {'kind': 'record', 'record_kind': 'local_artifact', 'id': 'objection-record',
                          'record': {}, 'artifacts': [references[1]]}, alias, fresh],
                     'version_source_mappings': []}
        self.install_material_objections([objection])
        last = self.deliver_units()
        observations = parts.observe_series(self.case.read_records(), self.case.artifacts, last['id'])
        earlier = parts._artifact_turns(observations)
        self.assertTrue(all(digest(reference) in earlier for reference in references[:2]))
        self.assertNotIn(digest(alias), earlier)
        self.assertNotIn(digest(fresh), earlier)
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            final = self.assign('final', last['id'])
        self.assertEqual(fit.call_args.args[-1], [alias, fresh])
        packet, sent = self.packet(final), self.sent(final)
        for frame in (packet, sent):
            self.assertEqual(frame['material_objections'], [objection])
            for reference in references[:2]:
                self.assertFalse(has_attachment(self.case.artifacts, frame, reference))
                self.assertEqual([entry for entry in frame['evidence'] if entry['artifact'] == reference],
                    [{'artifact': reference, 'content': '', 'encoding': 'omitted',
                      'delivery': 'delivered-in-an-earlier-part-turn', 'turn': earlier[digest(reference)],
                      'note': parts.OMITTED_BODY_NOTE}])
            self.assertTrue(all(has_attachment(self.case.artifacts, frame, reference) for reference in (alias, fresh)))
        output = self.final_output()
        output['value']['objection_findings'] = [{'id': objection['id'], 'status': 'continuing',
            'reason': 'The evidence delivered across this series retains this limitation.',
            'evidence': references[:2] + [alias, fresh]}]
        review = self.record_final(final, output)
        records = self.case.read_records()
        request = json.loads(self.case.artifacts.read(records['review_attempt']['attempt-final']['request']))
        self.assertEqual(review_excerpts.read_sent_packet(request)['evidence'], sent['evidence'])
        self.assertEqual(decisions.find_undelivered_review_references(records, self.case.artifacts, review), [])
        self.assertEqual(self.coverage(review), [])

    def test_final_fits_without_repeating_large_objection_bodies_from_its_series(self):
        references = self.continued_unit(duplicates=3, body_characters=16000, limit=60000)
        objection = {'id': 'large-shared-objection', 'claim': 'A material limitation.',
                     'material_version': '1' * 64, 'version_evidence': references, 'version_source_mappings': []}
        self.install_material_objections([objection])
        last = self.deliver_units()
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            final = self.assign('final', last['id'])
        self.assertEqual(fit.call_args.args[-1], [])
        sent = self.sent(final)
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertEqual(sent['material_objections'], [objection])
        self.assertFalse(any(has_attachment(self.case.artifacts, sent, reference) for reference in references))

    def test_final_requires_objection_bodies_delivered_only_by_an_invalid_part_attempt(self):
        references = self.continued_unit()
        objection = {'id': 'invalid-part-objection', 'claim': 'A material limitation.',
                     'material_version': '1' * 64, 'version_evidence': references, 'version_source_mappings': []}
        self.install_material_objections([objection])
        first = self.assign()
        self.assertTrue(any(has_attachment(self.case.artifacts, self.sent(first), reference) for reference in references))
        self.invoke(first, dict(self.output(first), notes=[]))
        records, artifacts = self.case.read_records(), self.case.artifacts
        observations = parts.observe_series(records, artifacts, first['id'])
        self.assertEqual(observations, [])
        packet = protocol.build_packet(records, artifacts, None, 'result', 'first')
        projected = parts._project(records, artifacts, packet, [], [], observations, [], first['id'], 'final', 1)
        sent = review_excerpts.read_sent_packet(transport.request_body(
            records['review_route']['route'], first['prompt']['text'], projected))
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertEqual(sent['material_objections'], [objection])
        self.assertTrue(all(has_attachment(artifacts, sent, reference) for reference in references))

    def test_final_requires_objection_bodies_planned_but_not_sent_by_a_valid_part(self):
        self.continued_unit(duplicates=3, limit=70000)
        objection, references = self.add_material_objection(body_characters=6000)
        self.case.expected['citations'] = [{'artifact': reference} for reference in references]
        with patch.object(parts, 'PART_UNIT_CHARACTERS', 1000):
            first = self.assign()
        self.assertTrue(all(has_attachment(self.case.artifacts, self.packet(first), reference) for reference in references))
        request_body = transport.request_body

        def elide_part_bodies(route, prompt, packet, *args, **kwargs):
            if parts.find_stage(packet) == 'part':
                packet = copy.deepcopy(packet)
                packet['evidence'] = [entry for entry in packet['evidence'] if entry['artifact'] not in references]
            return request_body(route, prompt, packet, *args, **kwargs)

        patcher = patch.object(transport, 'request_body', side_effect=elide_part_bodies)
        patcher.start()
        self.addCleanup(patcher.stop)
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            turns, final = self.finish_parts(first)
        observations = parts.observe_series(self.case.read_records(), self.case.artifacts, turns[-1]['id'])
        self.assertTrue(observations)
        self.assertTrue(all(digest(reference) not in parts._artifact_turns(observations) for reference in references))
        self.assertEqual(fit.call_args.args[-1], references)
        sent = self.sent(final)
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertEqual(sent['material_objections'], [objection])
        self.assertTrue(all(has_attachment(self.case.artifacts, sent, reference) for reference in references))

    def test_final_requires_objection_body_delivered_only_in_an_earlier_series(self):
        references = self.continued_unit()
        objection = {'id': 'earlier-series-objection', 'claim': 'A material limitation.',
                     'material_version': '1' * 64, 'version_evidence': [references[0]], 'version_source_mappings': []}
        self.install_material_objections([objection])
        last = self.deliver_units()
        observations = parts.observe_series(self.case.read_records(), self.case.artifacts, last['id'])
        self.assertIn(digest(references[0]), parts._artifact_turns(observations))
        self.case.expected['computations'] = []
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            final = self.assign('refreshed-final', last['id'])
        self.assertEqual(fit.call_args.args[-1], [references[0]])
        sent = self.sent(final)
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertNotEqual(sent[KEY]['series_assignment_id'], self.packet(last)[KEY]['series_assignment_id'])
        self.assertEqual(sent[KEY]['delivered_before'], [])
        self.assertEqual(sent['material_objections'], [objection])
        self.assertTrue(has_attachment(self.case.artifacts, sent, references[0]))

    def test_final_keeps_objection_body_independently_required_by_a_source_response(self):
        references = self.continued_unit()
        objection = {'id': 'requested-again', 'claim': 'A material limitation.', 'material_version': '1' * 64,
                     'version_evidence': references[:2], 'version_source_mappings': []}
        self.install_material_objections([objection])
        last = self.deliver_units()
        records, artifacts = self.case.read_records(), self.case.artifacts
        packet = protocol.build_packet(records, artifacts, None, 'result', 'first')
        packet['source_deliveries'] = [{'status': 'delivered', 'evidence': [references[0]]}]
        value = {'id': 'source-final', 'role': 'result', 'context': {'prior_assignment_id': last['id']}}
        route = records['review_route']['route']
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            planned = parts.prepare_packet(records, artifacts, value, route, packet, last['prompt'], [])
        self.assertEqual(fit.call_args.args[-1], [references[0]])
        sent = review_excerpts.read_sent_packet(transport.request_body(route, last['prompt']['text'], planned))
        self.assertEqual(sent['source_deliveries'], packet['source_deliveries'])
        self.assertTrue(has_attachment(artifacts, sent, references[0]))
        self.assertFalse(has_attachment(artifacts, sent, references[1]))
        earlier = parts._artifact_turns(parts.observe_series(records, artifacts, last['id']))
        self.assertEqual(parts.find_credited_objection_bodies(sent, earlier), {digest(references[1])})
        self.assertEqual([entry for entry in sent['evidence'] if entry['artifact'] == references[1]],
            [{'artifact': references[1], 'content': '', 'encoding': 'omitted',
              'delivery': 'delivered-in-an-earlier-part-turn', 'turn': earlier[digest(references[1])],
              'note': parts.OMITTED_BODY_NOTE}])

    def test_final_keeps_objection_excerpt_required_by_a_new_source_mapping(self):
        references = self.continued_unit()
        objections, _ = self.shared_objections()
        mapping = objections[0]['version_source_mappings'][0]
        excerpt = mapping['excerpt']
        self.case.expected['computations'][0]['execution']['outputs'].append({'id': 'mapped', 'artifact': excerpt})
        objections[0]['version_evidence'].extend([excerpt, references[0]])
        self.install_material_objections(objections)
        last = self.deliver_units()
        records, artifacts = self.case.read_records(), self.case.artifacts
        earlier = parts._artifact_turns(parts.observe_series(records, artifacts, last['id']))
        self.assertIn(digest(excerpt), earlier)
        self.assertIn(digest(references[0]), earlier)
        final = self.assign('mapped-final', last['id'])
        sent = self.sent(final)
        self.assertTrue(has_attachment(artifacts, sent, excerpt))
        self.assertFalse(has_attachment(artifacts, sent, references[0]))
        self.assertEqual(parts.find_credited_objection_bodies(sent, earlier), {digest(references[0])})
        self.assertEqual([entry for entry in sent['evidence'] if entry['artifact'] == references[0]],
            [{'artifact': references[0], 'content': '', 'encoding': 'omitted',
              'delivery': 'delivered-in-an-earlier-part-turn', 'turn': earlier[digest(references[0])],
              'note': parts.OMITTED_BODY_NOTE}])
        self.assertIn(mapping, decisions._visual_supplied_context(records, artifacts, [sent])[1])

    def test_final_source_reply_delivers_new_objection_bodies_in_a_body_only_part(self):
        self.oversized()
        self.case.add_reading_and_computation()
        objection, references = self.add_material_objection(body_characters=45000)
        last = self.deliver_units()
        final = self.assign('final', last['id'])
        self.assertTrue(all(has_attachment(self.case.artifacts, self.sent(final), reference) for reference in references))
        output = self.case.build_review_output('result')
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [{'id': 'read-source', 'kind': 'source', 'depth': 'fulltext',
                                      'version_id': self.case.expected['readings'][0]['version_id']}]
        self.invoke(final, output)
        fresh = self.case.artifacts.put(b'New objection body.' * 2500, 'text/plain')
        references = [fresh, dict(fresh, media_type='text/plain; charset=utf-8')]
        objection['version_evidence'].extend(references)
        with patch.object(parts, '_fits', wraps=parts._fits) as fit:
            body_part = self.assign('source-reply', final['id'])
        final_call = next(call for call in fit.call_args_list if parts.find_stage(call.args[3]) == 'final')
        artifacts, route, prompt, packet, history, attached = final_call.args
        self.assertEqual(attached, references)
        self.assertFalse(parts._fits(*final_call.args)[0])
        request = transport.request_body(route, prompt['text'], packet, history)
        sent = review_excerpts.read_sent_packet(request)
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertTrue(sent['source_deliveries'])
        self.assertEqual(sent['pending_source_requests'], [])
        self.assertLessEqual(parts._measure(route, request), transport.local_input_limit(route))
        # Equal hashes do not supply a missing descriptor: the transport kept only one of the two bodies.
        self.assertEqual(sum(has_attachment(artifacts, sent, reference) for reference in references), 1)
        packet, sent = self.packet(body_part), self.sent(body_part)
        self.assertEqual(sent[KEY]['stage'], 'part')
        self.assertEqual(sent[KEY]['objection_bodies'], references[:1])
        self.assertEqual(parts._delivered_units(sent), [])
        self.assertTrue(has_attachment(artifacts, sent, references[0]))
        self.assertFalse(has_attachment(artifacts, sent, references[1]))
        self.assertTrue(sent['source_deliveries'])
        self.assertTrue(all(has_attachment(artifacts, sent, reference)
                            for reference in protocol.collect_references(sent['source_deliveries'])))
        self.invoke(body_part)
        reply = self.assign('source-reply-final', body_part['id'])
        sent = self.sent(reply)
        self.assertEqual(sent[KEY]['stage'], 'final')
        self.assertFalse(has_attachment(artifacts, sent, references[0]))
        self.assertTrue(has_attachment(artifacts, sent, references[1]))
        self.assertEqual([entry for entry in sent['evidence'] if entry['artifact'] == references[0]],
            [{'artifact': references[0], 'content': '', 'encoding': 'omitted',
              'delivery': 'delivered-in-an-earlier-part-turn', 'turn': packet[KEY]['turn'],
              'note': parts.OMITTED_BODY_NOTE}])
        self.assertTrue(all(has_attachment(artifacts, sent, reference)
                            for reference in protocol.collect_references(sent['source_deliveries'])))
        self.assertLessEqual(parts._measure(route, self.request(reply)), transport.local_input_limit(route))

    def test_fallback_skips_unfittable_body_and_delivers_later_body(self):
        self.oversized()
        first = self.assign()
        small = self.case.artifacts.put(b's' * 4001, 'text/plain')
        for index in range(32):
            large = self.case.artifacts.put(str(index).encode().ljust(60000, b'l'), 'text/plain')
            if digest(large) < digest(small):
                break
        self.assertLess(digest(large), digest(small))
        item = {'execution': {'id': 'unequal-outputs', 'outputs': [
            {'id': 'large', 'artifact': large}, {'id': 'small', 'artifact': small}]}}
        self.case.expected['openness_searches'] = []
        self.case.expected['computations'] = [item]
        self.assertEqual(parts._owned_artifacts('computations', item), [large, small])
        records, artifacts = self.case.read_records(), self.case.artifacts
        packet = protocol.build_packet(records, artifacts, None, 'result', 'first')
        route = records['review_route']['route']
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                                           evidence_parts='part')
        value = {'id': 'unequal-part', 'role': 'result', 'context': {'prior_assignment_id': first['id']}}
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 30000):
            planned = parts.prepare_packet(records, artifacts, value, route, packet, prompt, [])
            request = transport.request_body(route, prompt['text'], planned)
        sent = review_excerpts.read_sent_packet(request)
        self.assertLessEqual(parts._measure(route, request), 30000)
        self.assertTrue(has_attachment(artifacts, sent, small))
        entry = next(entry for entry in sent['evidence'] if entry['artifact'] == large)
        self.assertEqual((entry['encoding'], entry['content'], entry['delivery']),
                         ('omitted', '', 'delivered-in-a-later-part-turn'))
        self.assertEqual(sent[KEY]['delivered_now']['computations'], [[0, 1]])
        self.assertEqual(sent[KEY]['remaining']['computations'], [[0, 1]])
        self.assertTrue(parts._makes_progress(parts._delivered_units(sent), [small], []))

    def test_unit_continues_with_pending_and_earlier_markers_then_passes_post_coverage(self):
        references = self.continued_unit()
        # The fallback must bypass the whole-unit cap as well as the request's room.
        with patch.object(parts, 'PART_UNIT_CHARACTERS', 1000):
            first = self.assign()
            first_packet = self.sent(first)
            attached = [ref for ref in references if has_attachment(self.case.artifacts, first_packet, ref)]
            self.assertGreater(len(attached), 0)
            self.assertLess(len(attached), len(references))
            self.assertEqual(first_packet[KEY]['delivered_now']['computations'], [[0, 1]])
            self.assertEqual(first_packet[KEY]['remaining']['computations'], [[0, 1]])
            for entry in first_packet['evidence']:
                if entry['artifact'] in references and entry['artifact'] not in attached:
                    self.assertEqual((entry['content'], entry['encoding'], entry['delivery']),
                                     ('', 'omitted', 'delivered-in-a-later-part-turn'))
                    self.assertEqual(entry['note'],
                                     'This artifact body is not delivered in this turn. It arrives in a later part '
                                     'turn of this review. That turn carries its unit again.')
                    self.assertNotIn('turn', entry)
            self.invoke(first)
            observed = parts.observe_series(self.case.read_records(), self.case.artifacts, first['id'])
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]['artifacts'],
                             [ref for ref in parts._owned_artifacts('computations', self.case.expected['computations'][0])
                              if ref in attached])
            second = self.assign('part-2', first['id'])
            second_packet = self.sent(second)
            self.assertEqual(second_packet['problem_evidence']['computations'], self.case.expected['computations'])
            self.assertEqual(second_packet[KEY]['delivered_now']['computations'], [[0, 1]])
            self.assertEqual(second_packet[KEY]['remaining']['computations'], [])
            self.assertEqual(second_packet[KEY]['delivered_before'][0]['units']['computations'], [[0, 1]])
            for entry in second_packet['evidence']:
                if entry['artifact'] in attached:
                    self.assertEqual((entry['content'], entry['encoding'], entry['delivery'], entry['turn']),
                                     ('', 'omitted', 'delivered-in-an-earlier-part-turn', 1))
                    self.assertEqual(entry['note'],
                                     'This artifact body was delivered in the earlier part turn of this review '
                                     'that the turn field of this entry names. It is not repeated in this turn. '
                                     'The notes of that turn hold what the final assessment needs from it.')
                elif entry['artifact'] in references:
                    self.assertTrue(has_attachment(self.case.artifacts, second_packet, entry['artifact']))
            self.invoke(second)
            final = self.assign('final', second['id'])
        self.assertEqual(self.packet(final)[KEY]['stage'], 'final')
        self.assertEqual(self.coverage(self.record_final(final)), [])

    def test_duplicate_units_deliver_their_items_with_only_earlier_body_entries(self):
        references = self.continued_unit(duplicates=3)
        first = self.assign()
        self.invoke(first)
        second = self.assign('part-2', first['id'])
        self.invoke(second)
        assignment = self.assign('part-3', second['id'])
        indices = []
        while self.packet(assignment)[KEY]['stage'] == 'part':
            packet = self.sent(assignment)
            indices.extend(unit['index'] for unit in parts._delivered_units(packet))
            self.assertTrue(packet['problem_evidence']['computations'])
            for entry in packet['evidence']:
                if entry['artifact'] in references:
                    self.assertEqual(entry['delivery'], 'delivered-in-an-earlier-part-turn')
                    self.assertEqual(entry['encoding'], 'omitted')
                    self.assertEqual(entry['content'], '')
                    self.assertIn(entry['turn'], (1, 2))
            self.invoke(assignment)
            assignment = self.assign('part-' + str(packet[KEY]['turn'] + 1), assignment['id'])
            self.assertLessEqual(packet[KEY]['turn'], 4)
        self.assertEqual(indices, [1, 2])
        self.assertEqual(self.coverage(self.record_final(assignment)), [])

    def test_body_identity_is_the_full_descriptor_even_for_equal_hashes(self):
        references = self.continued_unit(aliases=True)
        first = self.assign()
        packet = self.sent(first)
        attached = [ref for ref in references if has_attachment(self.case.artifacts, packet, ref)]
        self.assertGreater(len(attached), 0)
        self.assertLess(len(attached), 3)
        self.invoke(first)
        second = self.assign('part-2', first['id'])
        packet = self.sent(second)
        for reference in references:
            entry = next(row for row in packet['evidence'] if row['artifact'] == reference)
            if reference in attached:
                self.assertEqual(entry['delivery'], 'delivered-in-an-earlier-part-turn')
            else:
                self.assertTrue(has_attachment(self.case.artifacts, packet, reference))
        self.invoke(second)
        self.assertEqual(self.packet(self.assign('final', second['id']))[KEY]['stage'], 'final')

    def test_body_attached_in_an_earlier_valid_frame_is_not_delivered_again(self):
        references = self.continued_unit()
        search = {'id': 'frame-search', 'queries': ['bounded sequence'], 'reason': 's' * 24000}
        self.case.put('literature_search', search['id'], search)
        self.case.expected['openness_searches'] = [{'search': search, 'sources': []}]
        self.case.expected['citations'] = [{'artifact': references[0]}]
        first = self.assign()
        self.assertEqual(self.packet(first)['problem_evidence']['computations'], [])
        self.assertTrue(has_attachment(self.case.artifacts, self.sent(first), references[0]))
        self.invoke(first)
        observations = parts.observe_series(self.case.read_records(), self.case.artifacts, first['id'])
        self.assertIn(references[0], observations[0]['artifacts'])
        second = self.assign('part-2', first['id'])
        entry = next(row for row in self.sent(second)['evidence'] if row['artifact'] == references[0])
        self.assertEqual((entry['delivery'], entry['turn'], entry['encoding'], entry['content']),
                         ('delivered-in-an-earlier-part-turn', 1, 'omitted', ''))

    def test_part_objections_omit_only_the_two_evidence_fields_and_final_keeps_them(self):
        self.continued_unit()
        objection_body = self.case.artifacts.put(b'objection evidence only', 'text/plain')
        objection = {'id': 'inherited-objection', 'claim': 'A material limitation.', 'reason': 'Retain its identity.',
                     'material_version': '1' * 64, 'version_evidence': [objection_body],
                     'version_source_mappings': [], 'history': [{'id': 'earlier-version'}]}
        real = protocol.add_problem_objections

        def add_objection(*args):
            real(*args)
            args[2]['material_objections'] = [copy.deepcopy(objection)]

        with patch.object(protocol, 'add_problem_objections', side_effect=add_objection):
            first = self.assign()
            packet = self.packet(first)
            self.assertEqual(packet['material_objections'],
                             [{key: value for key, value in objection.items()
                               if key not in ('version_evidence', 'version_source_mappings')}])
            self.assertFalse(any(entry['artifact'] == objection_body for entry in packet['evidence']))
            _, final = self.finish_parts(first)
        final_packet = self.packet(final)
        self.assertEqual(final_packet['material_objections'], [objection])
        self.assertTrue(has_attachment(self.case.artifacts, final_packet, objection_body))

    def test_recorded_0691_prompt_authenticates_and_continuation_uses_new_suffix(self):
        self.oversized()
        canonical = protocol.canonical_prompt

        def earlier(*args, **kwargs):
            if kwargs.get('evidence_parts') == 'part':
                kwargs['part_suffix'] = parts.PART_PROMPT_0691
            return canonical(*args, **kwargs)

        with patch.object(protocol, 'canonical_prompt', side_effect=earlier):
            first = self.assign()
        self.assertNotIn('delivered-in-a-later-part-turn', first['prompt']['text'])
        self.invoke(first)
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-' + first['id']]
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, first, attempt))
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, first['id'])['ready'])
        second = self.assign('new-prompt-part', first['id'])
        self.assertIn('delivered-in-a-later-part-turn', second['prompt']['text'])
        self.assertIn('In a part turn, each entry of material_objections keeps every field except version_evidence '
                      'and version_source_mappings; a part response gives no finding on an objection; the final '
                      'turn delivers every material objection with its evidence.', second['prompt']['text'])
        self.assertEqual(self.packet(second)[KEY]['turn'], 2)
        self.assertTrue(protocol._prompt_matches(second))
        tampered = copy.deepcopy(first)
        tampered['prompt']['text'] += ' '
        self.assertFalse(protocol._prompt_matches(tampered))

    def test_both_prompt_envelopes_authenticate_under_all_common_and_excerpt_versions(self):
        for rules in review_excerpts.PHASE_PROMPTS:
            for common in (1, protocol.COMMON_VERSION):
                for suffix in (parts.PART_PROMPT_0691, parts.PROMPTS['part']):
                    with self.subTest(rules=rules, common=common, earlier=suffix == parts.PART_PROMPT_0691):
                        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                            evidence_parts='part', excerpt_rules=rules, common_version=common, part_suffix=suffix)
                        assignment = {'role': 'result', 'subject': {'kind': 'problem'}, 'context': {}, 'prompt': prompt}
                        self.assertTrue(protocol._prompt_matches(assignment))
                        changed = copy.deepcopy(assignment)
                        changed['prompt']['template_hash'] = '0' * 64
                        self.assertFalse(protocol._prompt_matches(changed))

    def test_frame_and_item_alone_must_fit_and_record_nothing_on_refusal(self):
        self.continued_unit()
        self.case.expected['computations'][0]['assessment']['reason'] = 'c' * 50000
        revision = self.case.store.revision
        error = self.assert_code('review_input_too_large', self.assign)
        self.assertEqual(error.details['unit']['key'], 'computations')
        self.assertEqual(self.case.store.revision, revision)

    def test_a_fitted_item_with_no_new_body_or_completed_unit_is_refused(self):
        self.continued_unit(duplicates=2, body_characters=60000)
        # A fitted request can elide these large bodies, but that delivers no progress.
        revision = self.case.store.revision
        error = self.assert_code('review_input_too_large', self.assign)
        self.assertEqual(error.details['stage'], 'part')
        self.assertEqual(self.case.store.revision, revision)

    def test_invalid_part_response_does_not_supply_attachment_credit(self):
        self.continued_unit()
        first = self.assign()
        self.invoke(first, dict(self.output(first), notes=[]))
        self.assertEqual(parts.observe_series(self.case.read_records(), self.case.artifacts, first['id']), [])
        self.assert_code('review_source_continuation_unverified', lambda: self.assign('next', first['id']))

    def test_valid_response_without_delivery_progress_is_not_an_observed_turn(self):
        self.continued_unit(duplicates=2, body_characters=60000)
        # Construct a fitted, authenticated assignment whose item arrived without any owned body.
        with patch.object(parts, '_makes_progress', return_value=True):
            first = self.assign()
        self.invoke(first)
        records = self.case.read_records()
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, first['id'])['ready'])
        self.assertEqual(parts.observe_series(records, self.case.artifacts, first['id']), [])

    def test_tiny_bodies_can_fit_when_their_pending_markers_do_not(self):
        self.check_tiny_bodies(lambda index: str(index))

    def test_tiny_unicode_bodies_can_fit_when_their_pending_markers_do_not(self):
        self.check_tiny_bodies(lambda index: 'Sum: ' + '\u2211' * 50 + str(index))

    def check_tiny_bodies(self, content):
        self.oversized()
        first = self.assign()
        self.case.expected['openness_searches'] = []
        references = [self.case.artifacts.put(content(i).encode(), 'text/plain') for i in range(24)]
        self.case.expected['computations'] = [{'execution': {'id': 'tiny-outputs', 'outputs': [
            {'id': str(i), 'artifact': reference} for i, reference in enumerate(references)]},
            'assessment': {'reason': 'c' * 30000}}]
        records, artifacts = self.case.read_records(), self.case.artifacts
        packet = protocol.build_packet(records, artifacts, None, 'result', 'first')
        units = parts.list_units(artifacts, self.case.expected)
        route = records['review_route']['route']
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                                           evidence_parts='part')
        full = parts._project(records, artifacts, packet, units, units, [], units, 'tiny-part', 'part', 1)
        pending = parts._project(records, artifacts, packet, units, units, [], units, 'tiny-part', 'part', 1, [])
        full_size = parts._measure(route, transport.request_body(route, prompt['text'], full))
        pending_size = parts._measure(route, transport.request_body(route, prompt['text'], pending))
        self.assertLess(full_size, pending_size)
        limit = (full_size + pending_size) // 2
        value = {'id': 'tiny-part', 'role': 'result', 'context': {'prior_assignment_id': first['id']}}
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit), patch.object(parts, 'PART_UNIT_CHARACTERS', 1000):
            planned = parts.prepare_packet(records, artifacts, value, route, packet, prompt, [])
            request = transport.request_body(route, prompt['text'], planned)
        sent = review_excerpts.read_sent_packet(request)
        self.assertLessEqual(parts._measure(route, request), limit)
        self.assertEqual(sent[KEY]['remaining']['computations'], [])
        self.assertTrue(all(has_attachment(artifacts, sent, reference) for reference in references))
