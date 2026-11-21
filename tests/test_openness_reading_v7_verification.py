"""D1 guard 4 / closure A4 and Q12: authenticated mixed-protocol findings."""

import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import probe_codex_route, record_codex_route
from openness_reading_fixtures import OpennessReadingCase
from test_openness_carried_integration import CarriedIntegrationSupport
from research_harness import openness_carried, openness_reading as reading, openness_reading_v7 as v7
from research_harness import problem_openness, review_protocol as protocol
from research_harness import review_codex_transport as codex, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.evaluation import Evaluation


class VerificationCases(CarriedIntegrationSupport):
    def finish_and_verify(self):
        native, receipt = self.start_chain()
        packets = self.finish_chain(native)
        records = self.store.snapshot()['records']
        final = records['review_assignment']['new-%d' % len(packets)]
        turns = reading.verify_chain(records, self.artifacts, final)
        return receipt, final, turns

    def test_guard4_a4_mixed_chain_records_finding_and_keeps_old_digests(self):
        receipt, final, turns = self.finish_and_verify()
        self.assertEqual(turns[0]['origin']['chain'], 'turn-1')
        self.assertEqual(turns[0]['origin']['protocol'], reading.find_protocol(self.read_packet('turn-1')))
        self.assertEqual(turns[-1]['assignment_id'], final['id'])
        records = self.store.snapshot()['records']
        first = records['review_assignment']['new']
        selection = json.loads(self.artifacts.read(first['carried_selection']['artifact']))
        self.assertEqual(selection['protocol'], 'exactory-openness-carried-selection-v2')
        cutoff_revision = first['recorded_revision'] - 1
        cutoff = Evaluation.of(records, self.artifacts).read_snapshot(cutoff_revision)
        cutoff_digest = digest({kind: dict(rows) for kind, rows in cutoff.records.items()})
        with patch.object(v7, 'check_v7_admission', side_effect=AssertionError('replanning')):
            result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final['id']))['result']
        self.assertEqual(result['reading_turns'], turns)
        self.assertFalse(any(row['code'] == 'problem_openness_stale' for row in
                             problem_openness.assess_openness(self.store.snapshot()['records'], self.artifacts)))
        records = self.store.snapshot()['records']
        later_cutoff = Evaluation.of(records, self.artifacts).read_snapshot(cutoff_revision)
        self.assertEqual(digest({kind: dict(rows) for kind, rows in later_cutoff.records.items()}), cutoff_digest)
        inventory = v7.read_carried_inventory(self.artifacts, first)
        identity = v7._describe_carried_identity(records, first, records['review_route'][first['route_id']],
            json.loads(self.artifacts.read(first['context']['native_packet'])))
        self.assertEqual(openness_carried.select_carried_readings(later_cutoff.records, later_cutoff,
            inventory, identity), selection['turns'])
        for group in ('review_assignment', 'review_attempt', 'review_invocation'):
            for key, row in self.old_records.get(group, {}).items():
                self.assertEqual(digest(row), digest(records[group][key]))

    def test_malformed_selection_json_is_refused_with_the_origin_code(self):
        _, receipt = self.start_chain()
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v2')
        original = self.store.snapshot()['records']
        for protocol_name in ('exactory-openness-carried-selection-v1', 'exactory-openness-carried-selection-v2'):
            for value in ([], None, 'not a selection'):
                with self.subTest(protocol=protocol_name, value=value):
                    records = copy.deepcopy(original)
                    first = records['review_assignment']['new']
                    first['carried_selection'] = dict(protocol=protocol_name, digest=digest(value),
                        artifact=self.artifacts.put(problem_openness.serialize_record(value), 'application/json'))
                    with self.assertRaises(ResearchError) as refused:
                        v7.verify_problem_coverage(records, self.artifacts, first)
                    self.assertEqual(refused.exception.code, 'openness_reading_incomplete')
                    self.assertEqual(refused.exception.message, v7.ORIGIN_VERIFICATION_MESSAGE)

    def test_unsafe_artifact_descriptors_refuse_the_selection(self):
        _, receipt = self.start_chain()
        self.assertEqual(receipt['carried_selection']['protocol'], 'exactory-openness-carried-selection-v2')
        original = self.store.snapshot()['records']
        inventory = v7.read_carried_inventory(self.artifacts, receipt)
        identity = v7._describe_carried_identity(original, receipt, original['review_route'][receipt['route_id']],
            json.loads(self.artifacts.read(receipt['context']['native_packet'])))
        for kind, key, field in (('review_assignment', 'turn-1', 'packet'),
                                  ('review_attempt', 'attempt-turn-1', 'output')):
            with self.subTest(record_kind=kind, field=field):
                records = copy.deepcopy(original)
                records[kind][key][field]['path'] = 'research/sources/objects/../unsafe'
                with self.assertRaises(ResearchError) as refused:
                    openness_carried.select_carried_readings(records, self.artifacts, inventory, identity)
                self.assertEqual(refused.exception.code, 'unsafe_path')

    def test_guard4_changed_observation_selection_projection_and_source_are_refused(self):
        receipt, final, _ = self.finish_and_verify()
        original = self.store.snapshot()['records']
        for change in ('observation', 'selection', 'projection', 'source', 'plan', 'inventory', 'state', 'prompt'):
            with self.subTest(change=change):
                records = copy.deepcopy(original)
                first = records['review_assignment']['new']
                assigned = records['review_assignment'][final['id']]
                if change == 'observation':
                    records['review_attempt']['attempt-turn-1']['output'] = self.artifacts.put(b'{}', 'application/json')
                elif change == 'selection':
                    ref = first['carried_selection']
                    selected = json.loads(self.artifacts.read(ref['artifact']))
                    selected['turns'][0]['ranges'][0]['sha256'] = '0' * 64
                    ref.update(digest=digest(selected), artifact=self.artifacts.put(problem_openness.serialize_record(selected), 'application/json'))
                elif change in ('projection', 'source'):
                    key = 'state' if change == 'projection' else 'inventory'
                    value = json.loads(self.artifacts.read(first['reading_chain'][key]))
                    if key == 'state':
                        value['notes'][0]['text'] += 'changed'
                    else:
                        value['units'][0]['sha256'] = '0' * 64
                    first['reading_chain'][key] = self.artifacts.put(problem_openness.serialize_record(value), 'application/json')
                elif change == 'prompt':
                    assigned['prompt'] = protocol.canonical_prompt('native_math', reading_stage='reading',
                        reading_protocol=v7.PROTOCOL, capacity=assigned['prompt']['parameters']['capacity'])
                else:
                    packet = json.loads(self.artifacts.read(assigned['packet']))
                    packet['reading_final'][change + '_digest'] = '0' * 64
                    assigned['packet'] = self.artifacts.put(problem_openness.serialize_record(packet), 'application/json')
                with self.assertRaises(ResearchError):
                    reading.verify_chain(records, self.artifacts, assigned)

    def test_carried_contamination_makes_recorded_finding_stale(self):
        _, final, _ = self.finish_and_verify()
        self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final['id']))
        records = copy.deepcopy(self.store.snapshot()['records'])
        records.setdefault('review_context_event', {})['contamination'] = dict(id='contamination',
            assignment_id='turn-1', kind='contamination', source='author_history',
            recorded_revision=self.store.revision + 1)
        obligations = problem_openness.assess_openness(records, self.artifacts)
        self.assertEqual(obligations[0]['code'], 'problem_openness_stale')

    def test_recursive_v7_observer_keeps_each_origin_under_its_own_chain(self):
        self.complete_earlier_readings()
        _, final, _ = self.finish_and_verify()
        own = v7.observe_v7_chain(self.store.snapshot()['records'], self.artifacts, final)
        self.assertTrue(own)
        self.assertEqual({row['origin']['chain'] for row in own}, {'new'})
        self.assertFalse(any(row['searches'] for row in own), 'Carried searches keep their original chain.')
        inventory = v7.read_carried_inventory(self.artifacts, final)
        changed = copy.deepcopy(inventory)
        inherited = next(row for row in changed['units'] if row['identity'].get('version_id') == self.versions[0])
        inherited['sha256'] = '0' * 64
        classifications = openness_carried.classify_carried_notes(changed, own)
        self.assertTrue(all(row['classification'] == 'affected' for row in classifications))
        native = self.export(name='later-export', assignment_id='later')
        receipt = self.assign('later', native)
        selected = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))['turns']
        origins = [row['origin'] for row in selected]
        self.assertEqual(len(origins), len({digest(row) for row in origins}))
        self.assertEqual({row['chain'] for row in origins}, {'turn-1', 'new'})
        self.assertTrue(all(row['origin']['chain'] == 'turn-1' for row in selected if row['searches']))
        self.finish_chain(native, first='later')
        records = self.store.snapshot()['records']
        last = max((row for row in records['review_assignment'].values()
                    if row.get('reading_chain', {}).get('first_assignment_id') == 'later'),
                   key=lambda row: row['recorded_revision'])
        self.assertEqual([row['origin'] for row in reading.verify_chain(records, self.artifacts, last)
                          if row.get('origin')], origins)


class V6VerificationTests(VerificationCases, OpennessReadingCase):
    pass


class V5VerificationTests(VerificationCases, OpennessReadingCase):
    historical_v5 = True


class RetiredRouteVerificationTests(CarriedIntegrationSupport, OpennessReadingCase):
    def test_carried_chain_on_a_retired_stale_route_record_verifies_again(self):
        """Rule RS1 (requirement R3.2), row 1 of section 4.4 of its design. Chain A has two reading turns on the
        route record "route", and chain B carries both from "second-route" and records its finding. Then a probe of
        "route" fails without an answer, and the reviewer runtime changes, so no probe can verify "route" again. The
        retirement of "route" makes B verify and its finding current, and a new chain on a renewed route record
        carries the turns of A and of B. The test needs the size of a carried chain: two reading turns of A, the
        turns of B and the first assignment of the new chain."""
        record_codex_route(self, 'second-route', model=self.route_model, output_tokens=self.route_output_tokens)
        self.complete_earlier_readings()
        native = self.export(name='new-export', assignment_id='new')
        self.mutate(protocol.record_assignment, dict(id='new', route_id='second-route', dossier_id=None,
            role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=native)))
        packets = self.finish_chain(native)
        final = 'new-%d' % len(packets)
        self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final))
        with patch.object(transport, 'send_request', side_effect=ResearchError('review_provider_failure', 'No answer.')):
            protocol.probe_route(self.store, dict(id='no-answer', route_id='route'), credential='test-only',
                                 expected_revision=self.store.revision, request_id='no-answer')
        pinned = self.store.snapshot()['records']['review_route']['route']['runtime']
        runtime = patch.object(codex, 'runtime', return_value=dict(pinned, implementation_sha256='2' * 64))
        runtime.start()
        self.addCleanup(runtime.stop)
        records = self.store.snapshot()['records']
        with self.assertRaises(ResearchError) as refused:
            reading.verify_chain(records, self.artifacts, records['review_assignment'][final])
        self.assertEqual(refused.exception.code, 'openness_reading_incomplete')
        self.assertIn('problem_openness_stale',
                      [row['code'] for row in problem_openness.assess_openness(records, self.artifacts)])
        with self.assertRaises(ResearchError) as refused:
            probe_codex_route(self, 'probe-of-the-stale-record', 'route')
        self.assertEqual(refused.exception.code, 'review_route_runtime_stale')
        self.mutate(protocol.record_context_event, dict(id='retire-route', assignment_id='earlier-2',
            kind='route_retired', source='owner', reason='The route record pins a runtime that is not installed.',
            evidence=[self.artifacts.put(b'The owner retired this route record.', 'text/plain')]))
        records = self.store.snapshot()['records']
        # On the base the verification stays refused with openness_reading_incomplete.
        turns = reading.verify_chain(records, self.artifacts, records['review_assignment'][final])
        self.assertEqual([row['origin']['assignment_id'] for row in turns if row.get('origin')],
                         ['turn-1', 'earlier-2'])
        self.assertNotIn('problem_openness_stale',
                         [row['code'] for row in problem_openness.assess_openness(records, self.artifacts)])
        # Both route records pin the runtime of before, so the new chain takes a renewed route record.
        self.mutate(protocol.record_route, {'id': 'renewed', 'adapter': 'codex_cli_v1', 'model': self.route_model,
            'endpoint': 'codex://local', 'configuration': {'max_output_tokens': self.route_output_tokens,
                                                           'timeout_seconds': 5}})
        probe_codex_route(self, 'probe-renewed', 'renewed')
        receipt = self.mutate(protocol.record_assignment, dict(id='following', route_id='renewed', dossier_id=None,
            role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=self.export(name='following-export', assignment_id='following'))))['result']
        report = receipt['carried_readings']
        self.assertEqual({row['chain']: [turn['origin']['assignment_id'] for turn in row['turns']]
                          for row in report['chains']}, {'turn-1': ['turn-1', 'earlier-2'], 'new': ['new']})
        self.assertEqual(report['remaining_characters'], 0)


class EmptySelectionVerificationTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    answer_v7 = CarriedIntegrationSupport.answer_v7
    finish_chain = CarriedIntegrationSupport.finish_chain

    def invoke(self, identifier, output, attempt=None):
        if 'findings' in output:
            output = copy.deepcopy(output)
            output['findings']['openness'].update(outcome='supported_open', grounds=[])
        return super().invoke(identifier, output, attempt)

    def test_a4_empty_selection_reaches_a_recorded_finding(self):
        self.limit_input(1300000)
        self.old_records = copy.deepcopy(self.store.snapshot()['records'])
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            native = self.export(assignment_id='new')
            self.assign('new', native)
            packets = self.finish_chain(native)
        identifier = 'new-%d' % len(packets)
        result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=identifier))['result']
        self.assertEqual(result['outcome'], 'supported_open')
        self.assertTrue(result['reading_turns'])
        self.assertTrue(all(row.get('origin') is None for row in result['reading_turns']))


class AffectedNoteVerificationTests(unittest.TestCase):
    def test_required_two_notes_need_both_ranges_but_on_request_note_can_be_undelivered(self):
        from test_openness_reading_v7_turns import MemoryArtifacts
        artifacts = MemoryArtifacts()
        bodies = []
        for number in range(3):
            reference = artifacts.put(('note%d' % number).encode('utf-8'), 'text/plain')
            bodies.append(dict(handle='u%d' % number, kind='carried_reading', units=[],
                               artifact=reference, sha256=reference['sha256'], characters=5))
        inventory = dict(units=[], material=bodies, search_delivery=dict(protocol='exactory-search-delivery-v1',
            shared_fields=[], searches=[]))
        plan = dict(task=dict(reading=dict(material_allowance=100)))
        selection = dict(affected_notes=[dict(handle=row['handle'], delivery_characters=50) for row in bodies])
        state = dict(carried_readings=[], deliveries=[dict(unit='u0', start=0, end=5)], searches=[])
        self.assertEqual([row['handle'] for row in v7.list_required_affected_notes(plan, selection)['required']], ['u0', 'u1'])
        with self.assertRaises(ResearchError) as refused:
            v7._verify_required_coverage(artifacts, inventory, state, plan, selection)
        self.assertEqual(refused.exception.code, 'openness_reading_incomplete')
        self.assertEqual(refused.exception.details['ranges'][0]['unit'], 'u1')
        state['deliveries'].append(dict(unit='u1', start=0, end=3))
        with self.assertRaises(ResearchError):
            v7._verify_required_coverage(artifacts, inventory, state, plan, selection)
        state['deliveries'].append(dict(unit='u1', start=3, end=5))
        self.assertEqual(v7._verify_required_coverage(artifacts, inventory, state, plan, selection), [])


class NativeDeliveryVerificationTests(unittest.TestCase):
    def test_compact_final_records_native_proof_and_selected_source_delivery(self):
        import test_problem_openness_source_delivery as source_fixtures
        from codex_route_fixtures import record_codex_route, invoke_codex
        fixture = source_fixtures.SourceDeliveryTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        case = fixture.case
        record_codex_route(case, 'reading-route', output_tokens=128000, model='gpt-6-astra')
        packet = fixture.export(priority=True)
        native = case.pin_json(packet)
        prior = None
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            for number in range(1, 40):
                name = 'source-openness' if number == 1 else 'source-openness-%d' % number
                context = dict(native_packet=native)
                if prior:
                    context['prior_assignment_id'] = prior
                assigned = case.mutate(protocol.record_assignment, dict(id=name, route_id='reading-route',
                    dossier_id=None, role='native_math', reviewer_id=packet['reviewer']['actor_id'],
                    author_id='problem-author', context=context))['result']
                supplied = json.loads(case.artifacts.read(assigned['packet']))
                self.assertEqual(reading.find_protocol(supplied), v7.PROTOCOL)
                binding = supplied.get('reading_turn', supplied.get('reading_final'))
                if 'reading_turn' in supplied:
                    output = dict(schema_version=2, stage='reading', notes='Read the scientific units.',
                        excerpts=[], gaps=[], passage_requests=[], **{key: binding[key] for key in
                            ('subject_digest', 'claim_digest', 'reviewer')})
                else:
                    output = dict(schema_version=3, **{key: binding[key] for key in
                        ('subject_digest', 'claim_digest', 'reviewer')}, findings=dict(
                            {key: 'Checked the supplied subject.' for key in
                                ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                            openness=dict(outcome='unresolved', coverage='Read the required proof and sources.',
                                reason='A remaining gap.', grounds=[dict(kind='own_argument', text='The recorded argument requires further checking.')])) )
                invoke_codex(case, name, output)
                if 'reading_final' in supplied:
                    result = case.mutate(case.load_problem_module().record_openness, dict(assignment_id=name))['result']
                    self.assertEqual(result['openness_delivery'], packet['scientific_context']['openness_delivery'])
                    self.assertEqual(result['native_reviews'], packet['scientific_context']['native_reviews'])
                    forged = copy.deepcopy(packet)
                    forged['subject_digest'] = '0' * 64
                    with self.assertRaises(ResearchError):
                        v7.project_native_delivery(case.store.snapshot()['records'], case.artifacts, assigned, forged, set())
                    return
                prior = name
        self.fail('The compact native-evidence chain must finish.')


class PromptAndAdmissionVerificationTests(unittest.TestCase):
    def test_v7_matches_recorded_paragraph_id_and_packet_condition(self):
        from research_harness import review_prompt_paragraphs as paragraphs
        extra = paragraphs.PromptParagraph('fixture-reading', ('native_math',),
            lambda role, packet: 'reading_turn' in packet,
            (('fixture-v1', '\n\nRead this addition.'), ('fixture-v2', '\n\nRead the revised addition.')))
        capacity = dict(output_tokens=128000, answer_bound=20000, material_allowance=60000)
        packet = dict(reading_turn=dict(protocol=v7.PROTOCOL, capacity=capacity))
        base = protocol.canonical_prompt('native_math', reading_stage='reading', reading_protocol=v7.PROTOCOL)
        with patch.object(paragraphs, 'PARAGRAPHS', paragraphs.PARAGRAPHS + (extra,)):
            prompt = paragraphs.append_prompt_paragraphs(base, 'native_math', versions=['fixture-v1'])
            prompt['parameters']['capacity'] = capacity
            assigned = dict(role='native_math', prompt=prompt)
            self.assertTrue(v7.matches_turn_prompt(assigned, packet))
            self.assertFalse(v7.matches_turn_prompt(assigned, dict(reading_final=packet['reading_turn'])))
            changed = copy.deepcopy(assigned)
            changed['prompt']['parameters']['problem_paragraphs'] = ['fixture-v2']
            self.assertFalse(v7.matches_turn_prompt(changed, packet))

    def test_v7_refuses_binary_required_proof_before_text_inventory(self):
        import test_problem_openness_native_reviews as native_fixtures
        from test_review_route_capacity import build_route
        for content, media in ((b'%PDF-1.4\nASCII-only PDF bytes\n%%EOF', 'application/pdf'),
                               (b'\x89PNG\r\n\x1a\n', 'image/png')):
            with self.subTest(media=media):
                case = native_fixtures.NativeOpennessReviewTests()
                self.addCleanup(case.doCleanups)
                case.setUp()
                case.proof = case.artifacts.put(content, media)
                manifest = dict(case.manifest, artifacts=[dict(path='attempt/proof',
                    digest=case.proof['sha256'], role='proof')])
                case.replace_manifest(manifest, identifier='binary-proof')
                native = case.export(assignment='binary-openness', support='binary-proof')
                result = v7.check_v7_admission(case.store.snapshot()['records'], case.artifacts, build_route(), native)
                self.assertEqual((result['state'], result['reason']), ('stop', 'binary_required_evidence'))



class RequiredNotesChainTests(CarriedIntegrationSupport, OpennessReadingCase):
    text_sizes = (50000,) * 5

    def build_reading_response(self, packet, excerpts=None, passages=()):
        output = super().build_reading_response(packet, excerpts=excerpts, passages=passages)
        output['notes'] = 'Earlier synthesis. ' + 'n' * 2000
        return output

    def test_chain_requires_both_affected_notes_and_leaves_on_request_note_unread(self):
        from research_harness import review_route_capacity as capacity
        self.enabled.stop()
        prior = 'turn-1'
        for number in (2, 3):
            name = 'old-%d' % number
            self.assign(name, self.native, prior=prior)
            self.invoke(name, self.build_reading_response(self.read_packet(name), excerpts=[]))
            prior = name
        self.enabled.start()
        original = v7.check_v7_admission
        def admit(*args):
            result = original(*args)
            plan = result['capacity_plan']
            plan['task']['reading'] = capacity.derive_plan_task_limits(plan, 'reading', [dict(material_allowance=15000)])
            return result
        with self.change_inventory(self.versions[0]), patch.object(v7, 'check_v7_admission', side_effect=admit):
            native, first = self.start_chain()
            selection = json.loads(self.artifacts.read(first['carried_selection']['artifact']))
            plan = capacity.read_chain_capacity_plan(self.artifacts, first)
            groups = v7.list_required_affected_notes(plan, selection)
            self.assertEqual((len(groups['required']), len(groups['on_request'])), (2, 1))
            packets = self.finish_chain(native)
            records = self.store.snapshot()['records']
            final = records['review_assignment']['new-%d' % len(packets)]
            verified = v7.verify_problem_coverage(records, self.artifacts, final, final=True)
            optional = groups['on_request'][0]['handle']
            self.assertFalse(any(row['unit'] == optional for row in verified['state']['deliveries']))
            missing = copy.deepcopy(verified['state'])
            omitted = groups['required'][1]['handle']
            missing['deliveries'] = [row for row in missing['deliveries'] if row['unit'] != omitted]
            with self.assertRaises(ResearchError):
                v7._verify_required_coverage(self.artifacts, verified['inventory'], missing, plan, selection)


class V7PartialOriginTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (270000, 50000)
    answer_v7 = CarriedIntegrationSupport.answer_v7
    finish_chain = CarriedIntegrationSupport.finish_chain

    def test_v7_partial_origin_ranges_carry_after_the_first_slice(self):
        self.limit_input(1300000)
        self.old_records = copy.deepcopy(self.store.snapshot()['records'])
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            native = self.export(assignment_id='new')
            self.assign('new', native)
            self.finish_chain(native)
            later = self.export(name='later-export', assignment_id='later')
            receipt = self.assign('later', later)
            selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
            self.assertTrue(any(row['start'] > 0 for turn in selection['turns'] for row in turn['ranges']))
            packets = self.finish_chain(later, first='later')
            records = self.store.snapshot()['records']
            final = records['review_assignment']['later-%d' % len(packets)]
            self.assertTrue(reading.verify_chain(records, self.artifacts, final))


class CapacityReplacementVerificationTests(CarriedIntegrationSupport, OpennessReadingCase):
    text_sizes = (270000, 50000)

    def assert_replacement_verifies(self, later):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness.review_failure_class import plan_after_failure
        native, _ = self.start_chain()
        target = 'new'
        if later:
            self.invoke(target, self.answer_v7(self.read_packet(target)))
            self.assign('new-next', native, prior=target)
            target = 'new-next'
        self.assertIn('reading_turn', self.read_packet(target))
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response.update(status='incomplete')
            response['usage']['output_tokens'] = 128001
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.mutate(protocol.invoke_assignment, dict(id='failed-new', assignment_id=target), credential='fixture')
        recovery = plan_after_failure(self.store.snapshot()['records'], self.artifacts, target)
        self.assertEqual(recovery['state'], 'replace')
        payload = dict(recovery['details']['recovery']['payload'], id='replacement')
        self.mutate(protocol.record_assignment, payload)
        packets = self.finish_chain(native, first='replacement')
        records = self.store.snapshot()['records']
        final = records['review_assignment']['replacement-%d' % len(packets)]
        self.assertTrue(reading.verify_chain(records, self.artifacts, final))

    def test_capacity_replacement_of_first_turn_keeps_initial_selection(self):
        self.assert_replacement_verifies(False)

    def test_capacity_replacement_of_later_turn_uses_failed_positions_allowance(self):
        self.assert_replacement_verifies(True)


if __name__ == '__main__':
    unittest.main()
