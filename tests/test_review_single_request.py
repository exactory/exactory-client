"""Single-request output capacity enters the existing, authenticated parts series."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import native_review, review_parts as parts, review_protocol as protocol
from research_harness import review_failure_class as failures, review_route_capacity as capacity
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.review_parts_roles import ADAPTERS
from review_parts_fixtures import make_packet
from test_review_route_capacity import build_route
import test_review_parts_native as native_cases


def build_adapter_cases():
    problem = make_packet(2, 30000)
    problem.update(protocol=protocol._PROBLEM_PROTOCOL,
                   subject=dict(kind='problem', statement_id='statement'), dossier_id=None)
    problem['problem_evidence']['claim'].update(statement_id='statement', resolution_criteria='A finite bound.')
    problem['problem_evidence_digest'] = digest(problem['problem_evidence'])
    body = 'x' * 60000
    checksum = hashlib.sha256(body.encode()).hexdigest()
    reference = dict(path='research/sources/objects/' + checksum, sha256=checksum,
                     size=len(body), media_type='text/plain')
    subject = dict(statement='Every bounded fixture has a finite witness.')
    native = dict(protocol=protocol.PROTOCOL, role='native_math', dossier_id=None,
                  native=dict(protocol=native_review.PROTOCOL, kind='result', root='.', objective_id='objective',
                      contract=dict(proof_policy='analytical'), subject=subject, subject_digest=digest(subject),
                      claim_digest=digest(subject), reviewer=dict(source='host', actor_id='reviewer',
                      attestation_id='review-assignment:first'), scientific_context=dict(scope='objective'),
                      evidence=[dict(digest=checksum, kind='artifact', artifact=reference)],
                      execution_provenance=[], unmaterialized_digests=[]),
                  evidence=[dict(artifact=reference, encoding='utf-8', content=body)])
    verification = dict(protocol=protocol.PROTOCOL, role='verification', dossier_id=None,
                        paper=dict(kind='verification', task_digest='b' * 64,
                                   sources=dict(work=[dict(text='x' * 60000)])), evidence=[])
    readiness = dict(protocol=protocol.PROTOCOL, role='result', dossier_id='dossier', dossier={},
                     readiness=dict(kind='readiness', inputs=dict(branches=[dict(text='x' * 60000)])), evidence=[])
    focused = dict(problem, role='adjudicator', objection=dict(id='objection'),
                   assessments=[dict(id='review-a'), dict(id='review-b')])
    focused['problem_evidence'] = copy.deepcopy(problem['problem_evidence'])
    focused['problem_evidence']['openness_searches'] = [dict(search=dict(id='search', reason='x' * 60000), sources=[])]
    focused['problem_evidence_digest'] = digest(focused['problem_evidence'])
    repair = dict(problem, context_repair=dict(assignment_id='previous'), scientific_history=[])
    cases = dict(problem_result=problem, problem_adjudicator=focused, native_math=native,
                 study_verification=verification, study_readiness=readiness, context_repair=repair)
    for packet in cases.values():
        packet['reviewer_id'] = 'reviewer'
    return cases


def build_adapter_prompt(packet):
    return protocol.canonical_prompt(packet['role'],
        orchestration='problem_creation' if packet.get('subject', {}).get('kind') == 'problem' else None,
        repair='context_repair' in packet)


class SingleEntryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.route = build_route()
        self.records = dict(review_route={'route': self.route}, review_assignment={
            'previous': dict(id='previous', role='result', route_id='route', dossier_id=None,
                             reviewer_id='prior-reviewer', author_id='author', context={},
                             slot_id='previous', recorded_revision=0)})
        profile = patch.object(capacity, 'read_output_profile', return_value=dict(rows=[]))
        profile.start()
        self.addCleanup(profile.stop)

    def prepare(self, packet, **options):
        value = dict(id='first', role=packet['role'], route_id='route', reviewer_id='reviewer',
                     author_id='author', context={}, dossier_id=packet.get('dossier_id'))
        for entry in packet.get('evidence', []):
            self.artifacts.put(entry['content'].encode(), entry['artifact']['media_type'])
        prompt = build_adapter_prompt(packet)
        return parts.prepare_packet(self.records, self.artifacts, value, self.route,
                                    packet, prompt, [], **options)

    def test_each_adapter_enters_by_task_with_profile_read_start(self):
        cases = build_adapter_cases()
        self.assertEqual(set(cases), set(ADAPTERS))
        for adapter, packet in cases.items():
            with self.subTest(adapter=adapter):
                planned = self.prepare(packet)
                self.assertEqual(planned[parts.KEY]['entry'], 'task')
                self.assertEqual(planned[parts.KEY]['adapter'], adapter)
                self.assertEqual(parts.find_kind(planned), 'read')
                self.assertLessEqual(planned[parts.KEY]['unit_characters'], 15000)

    def test_entry_compares_whole_request_at_the_exact_start_value(self):
        packet = build_adapter_cases()['study_verification']
        body = 'A complete visible source. ' * 300
        checksum = hashlib.sha256(body.encode()).hexdigest()
        reference = dict(path='research/sources/objects/' + checksum, sha256=checksum,
                         size=len(body), media_type='text/plain')
        packet['evidence'] = [dict(artifact=reference, encoding='utf-8', content=body) for _ in range(2)]
        request = protocol.transport.request_body(self.route, protocol.canonical_prompt('verification')['text'], packet, [])
        characters = capacity.measure_request(self.route, request)['text_characters']
        for allowance, expected in ((characters, None), (characters - 1, 'task')):
            with self.subTest(allowance=allowance), patch.object(capacity, 'select_task_limits',
                    return_value=dict(task_characters=allowance, material_allowance=15000, target_allowance=10)):
                planned = self.prepare(packet)
                self.assertEqual(planned.get(parts.KEY, {}).get('entry'), expected)

    def test_profile_read_allowance_caps_legacy_replacement_history(self):
        from review_parts_fixtures import plan
        turn = plan(make_packet(4), room=500000, read_allowance=15000,
                    replacement=dict(unit_characters=80000, over_cap=True))
        self.assertLessEqual(turn[parts.KEY]['unit_characters'], 15000)
        self.assertEqual(turn[parts.KEY]['plan']['unit_allowance'], 15000)
        with self.assertRaises(ResearchError) as caught:
            plan(make_packet(5), room=50000, read_allowance=15000,
                 replacement=dict(unit_characters=80000, over_cap=True))
        self.assertEqual(caught.exception.code, 'review_parts_inventory_too_large')

    def test_single_does_not_borrow_reading_rows(self):
        from test_review_output_profile import build_row
        profile = dict(rows=[build_row()], frame_policy=dict(borrowing_ready=True,
                       largest_text_characters=1000000, largest_material_characters=1000000))
        limits = capacity.select_task_limits(self.route, profile, 'single', frame_characters=0,
                                              frame_matches_reading=True)
        self.assertEqual(limits['state'], 'unmeasured')
        self.assertEqual(limits['material_allowance'], 15000)
        self.assertIsNone(limits['borrowed_kind'])

    def test_single_start_uses_the_observed_input_room_of_its_profile(self):
        from test_review_output_profile import build_row
        route = build_route('codex_cli_v1', model='fixture')
        limits = capacity.select_task_limits(route, dict(
            rows=[build_row(level=None, kind='single', thinking=1000)], verified_input_tokens=400000),
            'single', frame_characters=0)
        self.assertEqual(limits['material_allowance'], 300000)

    def test_adapterless_review_reports_output_warning_and_retains_its_packet(self):
        from types import SimpleNamespace
        from research_harness import cli
        from research_harness.review_single_request import describe_assignment_delivery
        packet = dict(protocol=protocol.PROTOCOL, role='manuscript', evidence=[], text='x' * 60000)
        assignment = dict(id='single', role='manuscript', route_id='route',
                          prompt=protocol.canonical_prompt('manuscript'), history=[],
                          packet=protocol._put_json(self.artifacts, packet))
        report = describe_assignment_delivery(self.records, self.artifacts, assignment)
        self.assertEqual(report['form'], 'single')
        self.assertEqual(report['reason'], 'no_parts_adapter')
        self.assertIn('output', report['capacity_warning'])
        records = dict(self.records, review_assignment={'single': assignment})
        warning = capacity.describe_assignment_warning(records, 'single', artifacts=self.artifacts)
        store = SimpleNamespace(snapshot=lambda: dict(records=records), build_artifact_store=lambda: self.artifacts)
        receipt = cli.describe_review_receipt(store, 'review-assignment', dict(id='single'), dict(result=assignment))
        for key, value in warning['output'].items():
            if key != 'message':
                self.assertEqual(receipt['capacity_warning']['output'][key], value)
        self.assertTrue(receipt['capacity_warning']['output']['above_start'])

    def test_staged_controller_delivery_does_not_require_a_packet(self):
        from research_harness.review_single_request import describe_assignment_delivery
        assignment = dict(id='controller', role='adjudicator', route_id='route', packet=None,
                          staged_review=dict(kind='controller'))
        self.assertEqual(describe_assignment_delivery(self.records, self.artifacts, assignment),
                         dict(form='staged', reason='protocol'))

    def test_no_units_has_no_output_replacement(self):
        packet = build_adapter_cases()['study_verification']
        packet['paper']['sources'] = {}
        assignment = dict(id='first', role='verification', route_id='route', dossier_id=None,
            reviewer_id='reviewer', author_id='author', context={}, history=[],
            context_state=dict(context_status='verified_for_route'),
            packet=protocol._put_json(self.artifacts, packet), prompt=protocol.canonical_prompt('verification'))
        from research_harness.review_single_request import build_output_replacement
        self.assertIsNone(build_output_replacement(self.records, self.artifacts, assignment))
        from research_harness.review_single_request import prepare_replacement
        records = dict(self.records, review_assignment={'first': assignment}, review_attempt={
            'over-cap': dict(id='over-cap', assignment_id='first', status='failed', origin='instrumented',
                             response=None, output=None, error=None, usage=dict(output_tokens=128000))})
        payload = {key: assignment[key] for key in
                   ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
        payload.update(id='replacement', context=dict(capacity_replacement='first'))
        with self.assertRaises(ResearchError) as caught:
            prepare_replacement(records, self.artifacts, payload, 2)
        self.assertEqual(caught.exception.code, 'invalid_review_context')

    def test_protocol_kinds_without_a_smaller_task_do_not_start_single_series(self):
        from research_harness.review_single_request import build_output_replacement
        for marker in (dict(reading_turn={}), dict(reading_record={}), dict(staged_review={}),
                       dict(reviewer_excerpts={}), dict(visual_probe={}),
                       dict(allowed_control='control', evidence_control='evidence'),
                       dict(protocol='exactory-reassessment-disposition-correction-v1')):
            with self.subTest(marker=marker):
                packet = dict(build_adapter_cases()['study_verification'], **marker)
                assignment = dict(id='first', role='verification', route_id='route',
                                  packet=protocol._put_json(self.artifacts, packet))
                self.assertIsNone(build_output_replacement(self.records, self.artifacts, assignment))

    def test_each_adapter_output_replacement_passes_preflight(self):
        from research_harness.review_single_request import build_output_replacement
        for adapter, packet in build_adapter_cases().items():
            with self.subTest(adapter=adapter):
                for entry in packet.get('evidence', []):
                    self.artifacts.put(entry['content'].encode(), entry['artifact']['media_type'])
                assignment = dict(id='first', role=packet['role'], route_id='route', dossier_id=None,
                    reviewer_id='reviewer', author_id='author', context={}, history=[],
                    packet=protocol._put_json(self.artifacts, packet), prompt=build_adapter_prompt(packet))
                planned = build_output_replacement(self.records, self.artifacts, assignment)
                self.assertIsNotNone(planned)
                header = planned['packet'][parts.KEY]
                self.assertEqual(header['adapter'], adapter)
                self.assertEqual(header['entry'], 'output')
                self.assertEqual(header['kind'], 'read')
                self.assertLessEqual(planned['preflight']['input_size']['value'],
                                     planned['preflight']['input_size']['limit'])

    def test_each_adapter_replacement_is_accepted_by_the_assignment_command(self):
        from research_harness.storage import Store
        from research_harness.operations import prepared_mutation
        from research_harness import review_preflight
        from research_harness.review_single_request import build_output_replacement
        for adapter, packet in build_adapter_cases().items():
            with self.subTest(adapter=adapter), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory), create=True)
                artifacts = store.build_artifact_store()
                packet = copy.deepcopy(packet)
                packet['reviewer_id'] = 'reviewer'
                context = {}
                if 'native' in packet:
                    packet['native']['root'] = str(store.root)
                    context['native_packet'] = protocol._put_json(artifacts, packet['native'])
                if 'paper' in packet:
                    context['verification_task_id'] = packet['paper']['task_digest']
                if packet['role'] == 'adjudicator':
                    context.update(review_ids=[row['id'] for row in packet['assessments']],
                                   objection_id=packet['objection']['id'])
                if 'context_repair' in packet:
                    context['context_repair'] = dict(assignment_id='previous')
                for entry in packet.get('evidence', []):
                    artifacts.put(entry['content'].encode(), entry['artifact']['media_type'])
                packet.pop('digest', None)
                packet['digest'] = digest(packet)
                assignment = dict(id='first', role=packet['role'], route_id='route', dossier_id=packet['dossier_id'],
                    reviewer_id='reviewer', author_id='author', context=context, history=[], session_id='first',
                    history_assignment_id=None, recorded_revision=1, packet=protocol._put_json(artifacts, packet),
                    packet_digest=packet['digest'], prompt=build_adapter_prompt(packet),
                    reassessment_required='context_repair' in packet,
                    context_state=dict(context_status='verified_for_route'))
                if 'subject' in packet:
                    assignment['subject'] = packet['subject']
                if 'context_repair' in packet:
                    assignment['slot_id'] = 'previous'
                attempt = dict(id='over-cap', assignment_id='first', origin='instrumented',
                    status='failed', response=None, output=None, error=None,
                    usage=dict(output_tokens=128000), recorded_revision=1)
                def seed(records, value):
                    return [('review_route', 'route', self.route), ('review_assignment', 'first', assignment),
                            ('review_attempt', 'over-cap', attempt),
                            ('review_assignment', 'previous', dict(id='previous', role='result', context={},
                                reviewer_id='prior-reviewer', author_id='author', dossier_id=None,
                                route_id='route', slot_id='previous', recorded_revision=0)),
                            ('research_intent', 'intent', dict(payload=dict(authors=['author']))),
                            ('strategy_dossier', 'dossier', dict(payload=dict(intent_id='intent')))], {}
                prepared_mutation(store, 'fixture.single', {}, seed,
                                  expected_revision=store.revision, request_id='seed')
                planned = build_output_replacement(store.snapshot()['records'], artifacts, assignment)
                payload = {key: assignment[key] for key in
                           ('role', 'route_id', 'reviewer_id', 'author_id', 'dossier_id')}
                payload.update(id='recorded-replacement', context=planned['context'])
                record = protocol.record_assignment(store, payload, expected_revision=store.revision,
                                                    request_id='replace')['result']
                request = protocol.build_assignment_request(store.snapshot()['records'], artifacts, record)
                report = review_preflight.check_request(review_preflight.build_assignment_report(
                    store.snapshot()['records'], artifacts, record, request), request)
                self.assertLessEqual(report['input_size']['value'], report['input_size']['limit'])
                self.assertEqual(record.get('slot_id', 'first'), assignment.get('slot_id', 'first'))
                planned_packet = protocol._read_json(artifacts, record['packet'])
                self.assertTrue(protocol._prompt_matches(record, packet=planned_packet))
                from research_harness.review_input_projection import packets_from_request
                self.assertEqual(packets_from_request(request, assignments=[record])[-1], planned_packet)


class FrozenSingleTests(unittest.TestCase):
    def setUp(self):
        self.case = native_cases.NativePartsTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        # Model a stored ordinary assignment, before prospective task entry existed.
        with patch.object(parts, 'prepare_packet', side_effect=lambda records, artifacts, value, route,
                          packet, prompt, history, **options: packet):
            self.first = self.case.assign()

    def record_over_cap(self, tokens=20000, *, completed=False, output=None):
        from codex_route_fixtures import build_codex_response
        def respond(route, request, credential):
            response = build_codex_response(route, request, {} if output is None else output)
            response['status'] = 'completed' if completed else 'incomplete'
            response['usage']['output_tokens'] = tokens
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            protocol.invoke_assignment(self.case.store, dict(id='over-cap', assignment_id='first'),
                expected_revision=self.case.store.revision, request_id='over-cap')

    def test_output_limit_plans_frozen_series_and_real_assignment_accepts_it(self):
        from research_harness.review_continuation import plan_next
        self.record_over_cap()
        records = self.case.store.snapshot()['records']
        plan = plan_next(records, self.case.artifacts, 'first')
        self.assertEqual(plan['state'], 'replace', plan)
        self.assertEqual(plan['details']['reason'], 'capacity')
        replacement = protocol.record_assignment(self.case.store, plan['payload'],
            expected_revision=self.case.store.revision, request_id='replacement')['result']
        packet = self.case.read_packet(replacement)
        self.assertEqual(packet[parts.KEY]['entry'], 'output')
        logical = parts.read_logical_packet(self.case.artifacts, packet[parts.KEY])
        self.assertEqual(logical, self.case.read_packet(self.first))
        inventory = parts.read_inventory(self.case.artifacts, packet[parts.KEY]['inventory'])
        self.assertLessEqual(packet[parts.KEY]['unit_characters'], inventory['characters'] // 2)
        self.assertEqual(replacement['route_id'], self.first['route_id'])
        self.assertEqual(replacement['reviewer_id'], self.first['reviewer_id'])

    def test_first_output_read_timeout_replaces_it_without_changing_the_frozen_origin(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness.review_continuation import plan_next
        from test_review_output_profile import build_row
        self.record_over_cap()
        profile = dict(rows=[build_row(level=None, kind='single', thinking=1000),
                             build_row(level=None, kind='read', thinking=1000)])
        with patch.object(capacity, 'read_output_profile', return_value=profile):
            planned = plan_next(self.case.store.snapshot()['records'], self.case.artifacts, 'first')
            first_read = protocol.record_assignment(self.case.store, planned['payload'],
                expected_revision=self.case.store.revision, request_id='replace-single')['result']
            before = protocol.build_assignment_request(self.case.store.snapshot()['records'],
                                                        self.case.artifacts, first_read)
            old = self.case.read_packet(first_read)[parts.KEY]['unit_characters']
            self.assertGreater(old, 30000)
            def timeout(route, request, credential):
                response = build_codex_response(route, request, {})
                response['output'][0]['content'][0]['text'] = ''
                response['usage']['output_tokens'] = 1
                response['status'] = 'failed'
                response['execution'] = dict(timeout=True)
                return bind_codex_response(route, request, response)
            for number in range(2):
                with patch.object(protocol.transport, 'send_request', side_effect=timeout):
                    protocol.invoke_assignment(self.case.store,
                        dict(id='timeout-' + str(number), assignment_id=first_read['id']),
                        expected_revision=self.case.store.revision, request_id='timeout-' + str(number))
                if number == 0:
                    revision = self.case.store.revision
                    payload = {key: first_read[key] for key in
                               ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
                    payload.update(id='premature-replacement', context=dict(capacity_replacement=first_read['id']))
                    with self.assertRaises(ResearchError) as caught:
                        protocol.record_assignment(self.case.store, payload,
                            expected_revision=revision, request_id='premature-replacement')
                    self.assertEqual(caught.exception.code, 'invalid_review_context')
                    self.assertEqual(self.case.store.revision, revision)
            next_plan = plan_next(self.case.store.snapshot()['records'], self.case.artifacts, first_read['id'])
            self.assertEqual(next_plan['state'], 'replace', next_plan)
            replacement = protocol.record_assignment(self.case.store, next_plan['payload'],
                expected_revision=self.case.store.revision, request_id='replace-read')['result']
        header = self.case.read_packet(replacement)[parts.KEY]
        self.assertEqual((header['entry'], header['replaces_assignment_id']), ('output', self.first['id']))
        self.assertLessEqual(header['unit_characters'], old // 2)
        self.assertEqual(replacement['slot_id'], first_read['slot_id'])
        self.assertEqual(replacement['capacity_replacement_of'], first_read['id'])
        self.assertEqual(replacement['context'], first_read['context'])
        self.assertEqual(parts.read_logical_packet(self.case.artifacts, header), self.case.read_packet(self.first))
        self.assertEqual(protocol.build_assignment_request(self.case.store.snapshot()['records'],
                         self.case.artifacts, first_read), before)

    def test_completed_capped_answer_without_usable_output_enters_its_series(self):
        from research_harness.review_continuation import plan_next
        self.record_over_cap(completed=True)
        records = self.case.store.snapshot()['records']
        attempt = records['review_attempt']['over-cap']
        self.assertEqual(attempt['status'], 'completed')
        self.assertEqual(failures.classify_attempt(records, self.case.artifacts, attempt)['outcome'], 'output_over_cap')
        planned = plan_next(records, self.case.artifacts, 'first')
        self.assertEqual(planned['state'], 'replace')
        replacement = protocol.record_assignment(self.case.store, planned['payload'],
            expected_revision=self.case.store.revision, request_id='replacement')['result']
        self.assertEqual(parts.find_kind(self.case.read_packet(replacement)), 'read')

    def test_usable_completed_output_at_the_cap_cannot_be_resampled(self):
        from research_harness.review_single_request import build_output_replacement
        self.assertIsNotNone(build_output_replacement(self.case.store.snapshot()['records'],
                                                     self.case.artifacts, self.first))
        output = self.case.final_output([row['digest'] for row in self.case.native['evidence']])
        self.record_over_cap(completed=True, output=output)
        records = self.case.store.snapshot()['records']
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, 'first')['ready'])
        self.assertIsNone(failures.classify_attempt(records, self.case.artifacts,
                                                  records['review_attempt']['over-cap']))
        payload = {key: self.first[key] for key in
                   ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
        payload.update(id='replacement', context=dict(capacity_replacement='first'))
        with self.assertRaises(ResearchError) as caught:
            protocol.record_assignment(self.case.store, payload,
                expected_revision=self.case.store.revision, request_id='replacement')
        self.assertEqual(caught.exception.code, 'invalid_review_context')

    def test_replacement_refuses_changed_reviewer_route_author_or_role_and_a_fork(self):
        self.record_over_cap()
        from research_harness.review_continuation import plan_next
        records = self.case.store.snapshot()['records']
        payload = plan_next(records, self.case.artifacts, 'first')['payload']
        route = {key: copy.deepcopy(records['review_route']['route'][key]) for key in
                 ('adapter', 'model', 'endpoint', 'configuration')}
        protocol.record_route(self.case.store, dict(route, id='other-route'),
            expected_revision=self.case.store.revision, request_id='other-route')
        for changes in (dict(reviewer_id='other'), dict(author_id='other'), dict(role='manuscript'),
                        dict(route_id='other-route')):
            with self.subTest(changes=changes), self.assertRaises(ResearchError) as caught:
                protocol.record_assignment(self.case.store, dict(payload, **changes),
                    expected_revision=self.case.store.revision, request_id='wrong-' + next(iter(changes)))
            self.assertEqual(caught.exception.code, 'review_source_continuation_mismatch')
        protocol.record_assignment(self.case.store, payload, expected_revision=self.case.store.revision,
                                   request_id='replacement')
        with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Replaced call')):
            with self.assertRaises(ResearchError) as caught:
                protocol.invoke_assignment(self.case.store, dict(id='late-original', assignment_id='first'),
                    expected_revision=self.case.store.revision, request_id='late-original')
        self.assertEqual(caught.exception.code, 'review_assignment_replaced')
        with self.assertRaises(ResearchError) as caught:
            protocol.record_assignment(self.case.store, dict(payload, id='fork'),
                expected_revision=self.case.store.revision, request_id='fork')
        self.assertEqual(caught.exception.code, 'review_source_continuation_fork')

    def test_check_reports_series_form_without_writing(self):
        self.record_over_cap()
        from research_harness.review_continuation import plan_next
        from research_harness.check_mode import check_mutation
        payload = plan_next(self.case.store.snapshot()['records'], self.case.artifacts, 'first')['payload']
        revision = self.case.store.revision
        result = check_mutation(self.case.store, protocol.record_assignment, payload,
            expected_revision=revision, request_id='check-replacement', bounded_payload=payload)
        self.assertEqual(result['review_delivery'], dict(form='series', reason='output'))
        self.assertEqual(self.case.store.revision, revision)

    def test_receipt_explanation_preserves_replay_when_a_packet_artifact_is_missing(self):
        from research_harness import cli
        from research_harness.review_single_request import describe_assignment_delivery
        records = self.case.store.snapshot()['records']
        self.assertEqual(describe_assignment_delivery(records, self.case.artifacts, self.first)['reason'],
                         'recorded_single')
        records['review_assignment']['first']['packet'] = dict(self.first['packet'],
            path='research/sources/objects/' + '0' * 64, sha256='0' * 64, size=0)
        receipt = dict(revision=self.case.store.revision, result=self.first)
        with patch.object(self.case.store, 'snapshot', return_value=dict(records=records)):
            reported = cli.describe_review_receipt(self.case.store, 'review-assignment', dict(id='first'), receipt)
        self.assertEqual(reported['result'], receipt['result'])
        self.assertNotIn('review_delivery', reported)

    def test_within_start_assignment_is_byte_for_byte_the_ordinary_preparation(self):
        from test_review_output_profile import build_row
        from research_harness.storage import Store
        import shutil
        # Compare the old preparation and the new one over identical store snapshots.
        with tempfile.TemporaryDirectory() as directory:
            clone_root = Path(directory) / 'clone'
            shutil.copytree(self.case.root, clone_root)
            clone = Store(clone_root)
            native = copy.deepcopy(self.case.native)
            native['reviewer']['attestation_id'] = 'review-assignment:within-start'
            body = self.case.artifacts.put(b'A short exact argument.', 'text/plain')
            clone.build_artifact_store().put(b'A short exact argument.', 'text/plain')
            native['evidence'] = [dict(kind='artifact', digest=body['sha256'], artifact=body)]
            reference = self.case.artifacts.put(json.dumps(native).encode(), 'application/json')
            clone.build_artifact_store().put(json.dumps(native).encode(), 'application/json')
            payload = dict(id='within-start', role='native_math', route_id='route', dossier_id=None,
                reviewer_id='reviewer', author_id='author', context=dict(native_packet=reference))
            with patch.object(parts, 'prepare_packet', side_effect=lambda records, artifacts, value, route,
                              packet, prompt, history, **options: packet):
                expected = protocol.record_assignment(clone, payload, expected_revision=clone.revision,
                                                      request_id='within-start')
            with patch.object(capacity, 'read_profile_priors', return_value=[
                    dict(build_row(level=None, kind='single', thinking=1000), largest_output_tokens=12)]):
                actual = protocol.record_assignment(self.case.store, payload,
                    expected_revision=self.case.store.revision, request_id='within-start')
                from research_harness.review_single_request import describe_assignment_delivery
                self.assertEqual(describe_assignment_delivery(self.case.store.snapshot()['records'],
                    self.case.artifacts, actual['result']), dict(form='single', reason='within_start'))
            self.assertEqual(actual, expected)
            from research_harness import cli
            records = self.case.store.snapshot()['records']
            warning = capacity.describe_assignment_warning(records, payload['id'], artifacts=self.case.artifacts)
            expected_receipt = dict(actual, capacity_warning=warning) if warning is not None else actual
            self.assertEqual(cli.describe_review_receipt(self.case.store, 'review-assignment', payload, actual),
                             expected_receipt)

    def test_capacity_entry_uses_a_new_prompt_and_legacy_series_keeps_its_prompt(self):
        old = parts.build_turn_prompt('native_math', {}, dict(adapter='native_math', kind='read'))
        new = parts.build_turn_prompt('native_math', {}, dict(adapter='native_math', kind='read', entry='task'))
        self.assertNotEqual(new['template_id'], old['template_id'])
        self.assertNotIn('does not fit one input', new['text'])
        self.assertIn('does not fit one input', old['text'])
        self.assertEqual(old, protocol.canonical_prompt('native_math', review_parts=dict(adapter='native_math', kind='read')))

    def test_timeout_does_not_offer_an_unauthorized_single_output_replacement(self):
        records = self.case.store.snapshot()['records']
        allowed = failures.plan_capacity_recovery(records, self.case.artifacts, self.first, 'output_over_cap')
        self.assertEqual(allowed['kind'], 'replace')
        recovery = failures.plan_capacity_recovery(records, self.case.artifacts, self.first, 'timeout')
        self.assertEqual(recovery['kind'], 'decision')

    def test_stored_single_diagnostic_keeps_its_existing_form_and_names_its_adapter(self):
        from research_harness.review_single_request import describe_assignment_delivery
        report = describe_assignment_delivery(self.case.store.snapshot()['records'], self.case.artifacts, self.first)
        self.assertEqual(report['form'], 'single')
        self.assertEqual(report['reason'], 'recorded_single')
        self.assertEqual(report['capacity_warning']['output']['adapter'], 'native_math')

    def test_capacity_lookup_preserves_preparation_after_an_unrelated_assignment(self):
        from research_harness.operations import prepared_task_mutation
        from research_harness.review_continuation import plan_next
        self.record_over_cap()
        store = self.case.store
        protocol.record_assignment(store, plan_next(store.snapshot()['records'], self.case.artifacts, 'first')['payload'],
                                   expected_revision=store.revision, request_id='replacement')
        commit = store.mutate_scoped
        before = store.revision

        def intervene(operation, *args, **kwargs):
            foreign = dict(self.first, id='foreign', recorded_revision=store.revision + 1)
            store.mutate('fixture.foreign', {}, lambda tx: tx.put('review_assignment', 'foreign', foreign),
                         expected_revision=store.revision, request_id='foreign')
            return commit(operation, *args, **kwargs)

        with patch.object(store, 'mutate_scoped', side_effect=intervene):
            result = prepared_task_mutation(store, 'fixture.lookup', {}, lambda records, payload:
                ([], dict(replaced=protocol.find_replacement(records, self.first) is not None)),
                expected_revision=before, request_id='lookup')
        self.assertTrue(result['result']['replaced'])
        self.assertEqual(result['revision'], before + 2)

    def test_recorded_capacity_replacement_invalidates_a_prepared_original_lookup(self):
        from research_harness.operations import prepared_task_mutation
        self.record_over_cap()
        store = self.case.store
        commit = store.mutate_scoped
        payload = {key: self.first[key] for key in
                   ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
        payload.update(id='replacement', context=dict(capacity_replacement='first'))

        def intervene(operation, *args, **kwargs):
            if operation == 'fixture.lookup':
                protocol.record_assignment(store, payload, expected_revision=store.revision,
                                           request_id='replacement')
            return commit(operation, *args, **kwargs)

        with patch.object(store, 'mutate_scoped', side_effect=intervene), self.assertRaises(ResearchError) as caught:
            prepared_task_mutation(store, 'fixture.lookup', {}, lambda records, value:
                ([], dict(replaced=protocol.find_replacement(records, self.first) is not None)),
                expected_revision=store.revision, request_id='lookup')
        self.assertEqual(caught.exception.code, 'review_task_dependency_changed')

    def test_opus_max_128000_output_replacement_reaches_a_recorded_native_final(self):
        from research_harness import review_claude_transport as claude
        from claude_review_fixtures import build_recorded_response, RECORDED_CONTEXTS
        from research_harness.review_continuation import plan_next
        from review_parts_fixtures import make_output
        runtime = patch.object(claude, 'runtime', return_value=dict(adapter='claude_cli_v1', fixture=True))
        runtime.start()
        self.addCleanup(runtime.stop)
        route = build_route()
        route['id'] = 'opus'
        protocol.record_route(self.case.store, route, expected_revision=self.case.store.revision, request_id='opus')

        def invoke(assignment, output, over_cap=False):
            def respond(route, request, credential):
                response = build_recorded_response(route, request, output, RECORDED_CONTEXTS[0])
                response['context_evidence']['runtime'] = claude.runtime()
                if over_cap:
                    response['status'] = 'incomplete'
                    response['usage']['output_tokens'] = 128000
                return response
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                return protocol.invoke_assignment(self.case.store, dict(id='attempt-' + assignment['id'],
                    assignment_id=assignment['id']), expected_revision=self.case.store.revision,
                    request_id='invoke-' + assignment['id'])

        def probe(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(protocol.transport.PACKET_LABEL):])
            response = build_recorded_response(route, request,
                dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'],
                     excluded_controls=[]), RECORDED_CONTEXTS[0])
            response['context_evidence']['runtime'] = claude.runtime()
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=probe):
            protocol.probe_route(self.case.store, dict(id='probe-opus', route_id='opus'),
                expected_revision=self.case.store.revision, request_id='probe-opus')
        self.case.native['reviewer']['attestation_id'] = 'review-assignment:historic-opus'
        self.case.reference = self.case.artifacts.put(json.dumps(self.case.native).encode(), 'application/json')
        payload = dict(id='historic-opus', route_id='opus', role='native_math', dossier_id=None,
                       reviewer_id='reviewer', author_id='author', context=dict(native_packet=self.case.reference))
        with patch.object(parts, 'prepare_packet', side_effect=lambda records, artifacts, value, route,
                          packet, prompt, history, **options: packet):
            original = protocol.record_assignment(self.case.store, payload,
                expected_revision=self.case.store.revision, request_id='historic-opus')['result']
        invoke(original, {}, over_cap=True)
        recovery = capacity.plan_capacity_recovery(self.case.store.snapshot()['records'],
                                                   self.case.artifacts, original, 'output_over_cap')
        self.assertEqual(recovery['kind'], 'replace')
        self.assertEqual(recovery['details']['effort'], 'max')
        self.assertEqual(recovery['details']['kind'], 'single')
        self.assertEqual(recovery['details']['attempts'][0]['output_tokens'], 128000)
        assignment = protocol.record_assignment(self.case.store,
            plan_next(self.case.store.snapshot()['records'], self.case.artifacts, original['id'])['payload'],
            expected_revision=self.case.store.revision, request_id='enter-series')['result']
        self.assertEqual(self.case.read_packet(assignment)[parts.KEY]['entry'], 'output')
        for number in range(40):
            packet = self.case.read_packet(assignment)
            if parts.find_kind(packet) == 'final':
                invoke(assignment, self.case.final_output([self.case.native['evidence'][0]['digest']]))
                break
            invoke(assignment, make_output(packet))
            assignment = protocol.record_assignment(self.case.store, dict(id='turn-' + str(number),
                route_id='opus', role='native_math', dossier_id=None, reviewer_id='reviewer', author_id='author',
                context=dict(native_packet=self.case.reference, prior_assignment_id=assignment['id'])),
                expected_revision=self.case.store.revision, request_id='turn-' + str(number))['result']
        else:
            self.fail('The reviewer stub did not finish the replacement series')
        records = self.case.store.snapshot()['records']
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, assignment['id'])['ready'])
        self.assertTrue(parts.verify_series(records, self.case.artifacts, assignment['id'])['complete'])
        from research_harness.review_parts_native import find_final_assignment
        self.assertEqual(find_final_assignment(records, self.case.artifacts, original['id'])['id'], assignment['id'])

    def test_replacement_refuses_without_an_output_cap_attempt(self):
        from research_harness.review_single_request import build_output_replacement
        candidate = build_output_replacement(self.case.store.snapshot()['records'], self.case.artifacts, self.first)
        self.assertEqual(parts.find_kind(candidate['packet']), 'read')
        payload = {key: copy.deepcopy(self.first[key]) for key in
                   ('id', 'route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id')}
        payload.update(id='replacement', context=dict(capacity_replacement='first'))
        with self.assertRaises(ResearchError) as caught:
            protocol.record_assignment(self.case.store, payload, expected_revision=self.case.store.revision,
                                       request_id='unjustified')
        self.assertEqual(caught.exception.code, 'invalid_review_context')

    def test_stored_single_request_is_identical_after_profile_changes(self):
        records = self.case.store.snapshot()['records']
        before = protocol.build_assignment_request(records, self.case.artifacts, self.first)
        measured = capacity.describe_assignment_task(records, self.case.artifacts, self.first)
        self.assertEqual(measured['kind'], 'single')
        self.record_over_cap()
        records = self.case.store.snapshot()['records']
        from research_harness.review_continuation import plan_next
        planned = plan_next(records, self.case.artifacts, 'first')
        self.assertEqual(planned['state'], 'replace')
        protocol.record_assignment(self.case.store, planned['payload'],
            expected_revision=self.case.store.revision, request_id='replacement')
        records = self.case.store.snapshot()['records']
        self.assertEqual(protocol.build_assignment_request(records, self.case.artifacts, self.first), before)
        self.assertEqual(digest(before), records['review_attempt']['over-cap']['request_digest'])


if __name__ == '__main__':
    unittest.main()
