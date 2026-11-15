"""G15: bounded v6 chains and unchanged historical assignments."""

import copy
import json
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, review_protocol as protocol
from research_harness.errors import ResearchError


class V6ChainTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (80000, 80000)

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)

    def test_export_selects_v6_and_keeps_math_on_v1(self):
        reference = self.export()
        native = json.loads(self.artifacts.read(reference))
        context = native['scientific_context']
        self.assertEqual(context['openness_minimum']['schema_version'], 2)
        self.assertIn('minimum_gaps', context)
        self.assertIn('audit_forecast', context)
        self.assertEqual(reading.find_chain_protocol(native), reading.PROTOCOL_V6)
        self.assertEqual(reading.find_chain_protocol(native, reading.PROTOCOL_V5), reading.PROTOCOL_V5)
        self.assertEqual(reading.find_chain_protocol(native, reading.PROTOCOL_V6), reading.PROTOCOL_V6)
        assigned = self.assign('turn-1', reference)
        packet = self.read_packet('turn-1')
        self.assertEqual(reading.find_protocol(packet), reading.PROTOCOL_V6)
        self.assertEqual(assigned['prompt']['template_id'], reading.PROTOCOL_V6 + '/reading')
        self.assertGreaterEqual(reading._find_record(packet)['allowances']['record']['characters'],
                                reading.RECORD_ALLOWANCE_FLOOR)
        self.assertTrue(assigned['reading_chain_admission']['projection']['finite'])
        self.assertIn('output', assigned['capacity_warning'])

    def test_v6_admission_uses_the_verified_probe_of_its_new_codex_runtime(self):
        from codex_route_fixtures import build_codex_response
        from research_harness import review_codex_transport as codex, review_excerpts
        from research_harness import review_route_capacity as capacity, review_transport as transport
        codex.runtime.return_value = dict(codex.runtime(), version='reading-capacity-runtime')
        self.mutate(protocol.record_route, dict(id='keyed-route', adapter=codex.ADAPTER,
            model='gpt-6-astra', endpoint='codex://local',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        records = self.store.snapshot()['records']
        route = records['review_route']['keyed-route']
        self.assertEqual(capacity.describe_route_capacity(route, records=records,
            artifacts=self.artifacts)['input_tokens']['value'], 0)

        def send_probe(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            response = build_codex_response(route, request, dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = 1000000
            return response

        with patch.object(transport, 'send_request', side_effect=send_probe):
            probe = self.mutate(protocol.probe_route, dict(id='keyed-capacity', route_id='keyed-route',
                capacity=dict(input_tokens=1000000)))['result']
        self.assertTrue(probe['capacity_verified'])
        reference = self.export(assignment_id='keyed-turn')
        assigned = self.mutate(protocol.record_assignment, dict(id='keyed-turn', route_id='keyed-route',
            dossier_id=None, role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=reference)))['result']
        self.assertEqual(reading.find_protocol(self.read_packet('keyed-turn')), reading.PROTOCOL_V6)
        admission = assigned['reading_chain_admission']
        self.assertEqual(admission['state'], 'record')
        self.assertEqual(admission['limit']['tokens'], 999000)
        self.assertGreaterEqual(admission['projection']['record_total'], reading.RECORD_ALLOWANCE_FLOOR)
        request = assigned['capacity_warning']['input']['request']
        self.assertEqual(request['token_limit'], 999000)
        self.assertTrue(request['fits'])
        records = self.store.snapshot()['records']
        changed = dict(route, endpoint='codex://different-endpoint')
        self.assertEqual(capacity.describe_route_capacity(changed, records=records,
            artifacts=self.artifacts)['input_tokens']['value'], 0)
        refused = reading.admit_reading_chain(changed, admission['projection'], records=records,
                                              artifacts=self.artifacts, assignment=assigned)
        self.assertEqual(refused['state'], 'stop')
        self.assertEqual(refused['code'], reading.PROJECTION_STOP)
        self.assertEqual(refused['details']['limit']['tokens'], 0)

    def test_current_finding_prompt_is_separate_from_the_math_result_prompt(self):
        current = protocol.canonical_prompt('native_math', version=7)
        old = protocol.canonical_prompt('native_math', version=6)
        self.assertNotEqual(current['template_hash'], old['template_hash'])
        self.assertFalse(protocol.is_previous_prompt(dict(role='native_math', prompt=current)))
        self.assertTrue(protocol._prompt_matches(dict(role='native_math', prompt=current)))
        self.assertTrue(protocol._prompt_matches(dict(role='native_math', prompt=old)))

    def test_source_settles_inventory_includes_only_completely_read_decisive_works(self):
        from research_harness.problem_openness import export_openness
        from research_harness.native_review import validate_finding
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        from test_openness_finding_answer import build_answer
        # A missing second main capture blocks resolution, while the first fully read bound source remains eligible.
        self.capture(self.versions[0], body='A different complete HTML main body, without a qualifying reading.')
        unread = self.metadata(3)
        self.capture(unread, pdf_text='An entirely unread decisive source with no full reading.')
        search = ProblemOpennessMinimumTests.search(self, self.versions + [unread])
        result = export_openness(self.store, dict(assignment_id='turn-1', reviewer_id='openness-reviewer',
            minimum=dict(searches=[dict(formulation=self.selected['statement'], search_id=row['id'])
                                   for row in [self.search, search]],
                         settling_work_id=None, duplicate_resolutions=[])), accept_gaps=True)
        native = json.loads(self.artifacts.read(result['native_packet']))
        self.assertNotIn(unread, native['scientific_context']['decisive_work_ids'])
        self.assertIn(self.versions[0], native['scientific_context']['decisive_work_ids'])
        ground = dict(kind='source_settles', work_id=self.versions[0], text='The fully read bound capture settles the claim.')
        validate_finding(native, build_answer(native, 'unresolved', [ground]))
        with self.assertRaises(ResearchError) as caught:
            validate_finding(native, build_answer(native, 'unresolved', [dict(ground, work_id=unread)]))
        self.assertEqual(caught.exception.code, 'openness_finding_invalid')

    def test_unused_old_packet_refuses_before_c8(self):
        native = json.loads(self.artifacts.read(self.export()))
        minimum = native['scientific_context']['openness_minimum']
        minimum['schema_version'] = 1
        for key in ('family_readings', 'supporting_reading_digests', 'results'):
            minimum.pop(key, None)
        native['scientific_context'].pop('minimum_gaps', None)
        native['scientific_context'].pop('audit_forecast', None)
        reference = self.artifacts.put(json.dumps(native).encode(), 'application/json')
        with patch.object(reading, '_require_initial_captures', side_effect=AssertionError('C8 ran first')):
            with self.assertRaises(ResearchError) as caught:
                self.assign('turn-1', reference)
        self.assertEqual(caught.exception.code, 'openness_packet_outdated')
        self.assertEqual(caught.exception.details['handoff'], 'export')
        self.assertFalse(self.store.snapshot()['records'].get('review_assignment'))

    def test_first_assignment_below_floor_stops_without_a_call(self):
        self.limit_input(150000)
        reference = self.export()
        with self.assertRaises(ResearchError) as caught:
            self.assign('turn-1', reference)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        refusal = caught.exception.details['admissions'][0]['details']
        self.assertEqual(refusal['reason'], 'below_floor')
        self.assertLess(refusal['projection']['record_total'], reading.RECORD_ALLOWANCE_FLOOR)
        self.assertFalse(self.store.snapshot()['records'].get('review_assignment'))

    def build_stub_output(self, packet, shape, number, purpose_characters=500):
        excerpts, passages = [], []
        if shape == 'short-excerpts-long-purposes':
            for row in packet['reading_turn']['reading_plan']['delivered_now']:
                if row['kind'] != 'fulltext':
                    continue
                content = self.artifacts.read(row['artifact']).decode()
                excerpts.extend(dict(version_id=row['version_id'], text=content[start:start + 20],
                                     purpose='p' * purpose_characters)
                                for start in range(0, min(len(content) - 20, 15000), 23))
        elif shape in ('uncharged-diagnostics', 'passage-turns'):
            count = 400
            if shape == 'passage-turns':
                allowance = packet['reading_turn']['reading_record']['allowances']['record']
                # Leave room for finite one-character passage turns after retaining diagnostics.
                count = max(0, min(400, (allowance['characters'] - allowance['used'] - 12000) // 3200))
            excerpts = [dict(version_id='unknown-' + str(number) + '-' + str(index) + 'v' * 2000,
                text='Synthetic unmatched excerpt %d %d. ' % (number, index) + 'x' * 200,
                purpose='purpose-%d. ' % index + 'p' * 5000) for index in range(count)]
            passages = [dict(id='missing-' + str(index) + 'p' * 2000,
                version_id=self.versions[0], anchor='An anchor absent from every synthetic full text.', characters=20)
                for index in range(4 if shape == 'uncharged-diagnostics' else 0)]
        return self.build_reading_response(packet, excerpts=excerpts, passages=passages)

    def drive_stub(self, shape, purpose_characters=500):
        from test_openness_finding_answer import build_answer
        from research_harness.review_continuation import plan_next
        from research_harness.review_route_capacity import measure_request
        native = self.export(assignment_id='h' * 120 if shape == 'long-identifiers' else 'turn-1')
        prior, first_projection, stopped = None, None, False
        requests, passage_turns = [], 0
        for number in range(1, 22):
            identifier = ('h' * 120 if number == 1 else str(number).ljust(120, 't')) if shape == 'long-identifiers' else 'turn-' + str(number)
            assigned = self.assign(identifier, native, prior)
            packet = self.read_packet(identifier)
            records = self.store.snapshot()['records']
            route = records['review_route']['route']
            request = protocol.build_assignment_request(records, self.artifacts, assigned)
            measured = measure_request(route, request, records=records, artifacts=self.artifacts)
            first_projection = first_projection or assigned['reading_chain_admission']['projection']
            self.assertLessEqual(measured['characters'], first_projection['characters'])
            self.assertTrue(measured['fits'])
            requests.append(measured['characters'])
            record = reading._find_record(packet)
            extra = [row for row in record['turns'] if row.get('stage') == 'final']
            cost = sum(reading.measure_record_entries(route, record[key]) for key in ('excerpts', 'passages'))
            cost += reading.measure_record_entries(route, extra) + (2 if extra and len(record['turns']) > len(extra) else 0)
            self.assertEqual(record['allowances']['record']['used'], cost)
            self.assertLessEqual(cost, record['allowances']['record']['characters'])
            if reading.find_stage(packet) == 'reading':
                output = self.build_stub_output(packet, shape, number, purpose_characters)
            else:
                output = build_answer(packet)
                if shape == 'passage-turns' and not stopped:
                    passage = dict(id='one-character-' + str(number), version_id=self.versions[0],
                                   anchor='Paper 1 opening line.', characters=1)
                    candidate = self.build_reading_response(packet, excerpts=[], passages=[passage])
                    stopped = not reading.has_deliverable_passage(self.store.snapshot()['records'],
                                                                 self.artifacts, packet, candidate)
                    if not stopped:
                        output = candidate
                        passage_turns += 1
            attempt = None
            if shape == 'long-identifiers':
                planned = plan_next(self.store.snapshot()['records'], self.artifacts, identifier)
                self.assertLessEqual(len(planned['payload']['id']), 120)
                attempt = planned['payload']['id']
            self.invoke(identifier, output, attempt=attempt)
            if shape == 'long-identifiers' and output.get('stage') == 'reading':
                planned = plan_next(self.store.snapshot()['records'], self.artifacts, identifier)
                self.assertLessEqual(len(planned['payload']['id']), 120)
            if shape == 'long-identifiers' and number == 1:
                for failed in range(30):
                    self.mutate(protocol.record_attempt, dict(id=('failure-' + str(failed)).ljust(120, 'f'),
                        assignment_id=identifier, status='failed', output=None, usage=None,
                        reason='Synthetic failed attempt retained outside the reading record.'))
            if output.get('stage') != 'reading':
                plan = plan_next(self.store.snapshot()['records'], self.artifacts, identifier)
                self.assertEqual(plan['state'], 'record', plan)
                result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=identifier))['result']
                self.assertEqual(result['outcome'], 'supported_open')
                reading.verify_chain(self.store.snapshot()['records'], self.artifacts,
                                     self.store.snapshot()['records']['review_assignment'][identifier])
                return record, first_projection, requests, passage_turns
            prior = identifier
        self.fail('The admitted stub must reach its recorded finding within the fixture bound')

    def test_g15_short_excerpts_long_purposes_fit_every_request(self):
        record, _, _, _ = self.drive_stub('short-excerpts-long-purposes')
        self.assertTrue(record['excerpts'])

    def test_g15_long_purposes_are_bounded_before_record_charging(self):
        record, _, _, _ = self.drive_stub('short-excerpts-long-purposes', 5000)
        self.assertTrue(any('purpose_sha256' in row for row in record['excerpts']))

    def test_g15_uncharged_diagnostics_stop_retaining_and_charge_no_text(self):
        record, _, _, _ = self.drive_stub('uncharged-diagnostics')
        self.assertGreater(record['allowances']['record']['not_retained']['unmatched_excerpt'], 0)
        self.assertEqual(record['allowances']['excerpts']['used'], 0)
        self.assertEqual(record['allowances']['passages']['used'], 0)

    def test_g15_passage_turns_end_when_the_record_has_no_room(self):
        record, _, _, turns = self.drive_stub('passage-turns')
        self.assertGreater(turns, 0)
        self.assertTrue(record['passages'])

    def test_g15_reserved_identifiers_and_failed_attempts_fit_the_projection(self):
        record, _, _, _ = self.drive_stub('long-identifiers')
        self.assertEqual(len(record['turns'][0]['attempt_id']), 120)
        self.assertNotIn('failure-', json.dumps(record))

    def test_v6_assignment_and_attempt_ids_above_the_reservation_are_refused(self):
        native = self.export()
        for identifier in ('turn-' + 'x' * 120,):
            with self.assertRaises(ResearchError) as caught:
                self.assign(identifier, native)
            self.assertEqual(caught.exception.code, 'invalid_input')
            self.assertEqual(caught.exception.details['maximum_characters'], 120)
        self.assign('turn-1', native)
        with self.assertRaises(ResearchError) as caught:
            self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')), attempt='a' * 121)
        self.assertEqual(caught.exception.code, 'invalid_input')
        for identifier in ('a' * 121, 123, None, [], {}):
            with self.subTest(identifier=identifier), self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_attempt, dict(id=identifier, assignment_id='turn-1', status='failed',
                    output=None, usage=None, reason='Synthetic imported failed attempt.'))
            self.assertEqual(caught.exception.code, 'invalid_input')

    def test_g15_stored_v5_chain_keeps_packets_prompts_and_requests_while_continuing(self):
        from test_openness_finding_answer import build_answer
        self.historical_v5 = True
        self.limit_input(180000)
        native = self.export()
        first = self.assign('turn-1', native)
        before = copy.deepcopy(first)
        first_packet = self.artifacts.read(first['packet'])
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1'), excerpts=[]))
        observed = copy.deepcopy(self.store.snapshot()['records']['review_attempt']['attempt-turn-1'])
        request = self.artifacts.read(observed['request'])
        prior = 'turn-1'
        for number in range(2, 10):
            identifier = 'turn-' + str(number)
            assigned = self.assign(identifier, native, prior)
            packet = self.read_packet(identifier)
            self.assertEqual(reading.find_protocol(packet), reading.PROTOCOL_V5)
            self.assertTrue(assigned['prompt']['template_id'].startswith(reading.PROTOCOL_V5 + '/'))
            if reading.find_stage(packet) == 'final':
                self.invoke(identifier, self.build_native_result(packet))
                saved = self.mutate(self.load_problem_module().record_openness,
                                    dict(assignment_id=identifier))['result']
                self.assertEqual(reading.verify_chain(self.store.snapshot()['records'], self.artifacts,
                    self.store.snapshot()['records']['review_assignment'][identifier]), saved['reading_turns'])
                break
            self.invoke(identifier, self.build_reading_response(packet, excerpts=[]))
            prior = identifier
        else:
            self.fail('The historical v5 chain must finish')
        stored = self.store.snapshot()['records']['review_assignment']['turn-1']
        for key in stored:
            self.assertEqual(stored[key], before[key])
        self.assertEqual(self.artifacts.read(stored['packet']), first_packet)
        self.assertEqual(self.artifacts.read(observed['request']), request)
        self.historical_v5 = False
        current = json.loads(self.artifacts.read(self.export(name='new-export', assignment_id='new-head')))
        self.assertEqual(reading.find_chain_protocol(current), reading.PROTOCOL_V6)

    def test_result_units_plan_validate_and_verify_with_the_v6_kinds_only(self):
        from research_harness import review_excerpts
        from test_openness_finding_answer import build_answer
        native = json.loads(self.artifacts.read(self.export()))
        proof = self.artifacts.put(('A synthetic accepted proof with its hypotheses. ' * 3000).encode(), 'text/plain')
        identity = dict(native_attempt_digest='c' * 64, acceptance_id='acceptance-1')
        body = dict(identity, cited_as='Synthetic result', objective_id='objective-1', objective_claim='A finite bound',
            acceptance=dict(id='acceptance-1', outcome='proof', standard='analytical', status='accepted',
                classification='theorem', checkpoint_id='checkpoint-1', evidence_digests=[proof['sha256']]),
            checkpoint=dict(id='checkpoint-1', kind='analytical', claim='A finite bound', verification_status='reviewed'),
            review=dict(assignment_id='solver-first', final_assignment_id='solver-final', attempt_id='solver-attempt',
                        output_digest='d' * 64), manifests=[],
            artifacts=[dict(sha256=proof['sha256'], path='proof.txt', role='proof', media_type='text/plain',
                            size=proof['size'], delivery='text')], note='Accepted in the bound solver objective.')
        descriptor = self.artifacts.put(json.dumps(body, sort_keys=True).encode(), 'application/json')
        native['scientific_context']['openness_minimum']['results'].append(dict(identity,
            cited_as='Synthetic result', link_digest='e' * 64, result_digest=descriptor['sha256']))
        for reference in (proof, descriptor):
            native['evidence'].append(dict(digest=reference['sha256'], artifact=reference, kind='artifact'))
        reference = self.artifacts.put(json.dumps(native).encode(), 'application/json')
        prior, kinds = None, set()
        for number in range(1, 12):
            identifier = 'turn-' + str(number)
            assigned = self.assign(identifier, reference, prior)
            packet = self.read_packet(identifier)
            if reading.find_stage(packet) == 'final':
                answer = build_answer(packet, 'unresolved', [dict(kind='packet_defect', text='A synthetic descriptor is under test.')])
                self.invoke(identifier, answer)
                reading.verify_chain(self.store.snapshot()['records'], self.artifacts,
                    self.store.snapshot()['records']['review_assignment'][identifier])
                break
            plan = packet['reading_turn']['reading_plan']['delivered_now']
            excerpts = []
            for row in plan:
                kinds.add(row.get('record_kind', row['kind']))
                if row['kind'] == 'result_text' or row.get('record_kind') == 'accepted_result':
                    excerpt = dict(reading._describe_identity(row), text=self.artifacts.read(row['artifact']).decode()[:40],
                                   purpose='The accepted result text.')
                    excerpt.pop('kind')
                    review_excerpts.validate_excerpts([excerpt], 'invalid_reading_response', review_excerpts.UNIT_KINDS_V6)
                    with self.assertRaises(ResearchError):
                        review_excerpts.validate_excerpts([excerpt], 'invalid_reading_response', review_excerpts.UNIT_KINDS)
                    excerpts.append(excerpt)
            self.invoke(identifier, self.build_reading_response(packet, excerpts=excerpts))
            prior = identifier
        else:
            self.fail('Result units must reach the final')
        self.assertTrue({'result_text', 'accepted_result'} <= kinds)



class IncidentV6ChainTests(OpennessReadingCase):
    """The 20-search, 29-full-text incident shape uses synthetic source bodies."""
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (8000,) * 29

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions[(index % 3) * 10:(index % 3 + 1) * 10])
                for index in range(20)]

    def build_stub_output(self, *args, **kwargs):
        return V6ChainTests.build_stub_output(self, *args, **kwargs)

    def test_g15_incident_twenty_searches_twenty_nine_texts_is_admitted_or_stops_below_floor(self):
        self.limit_input(1300000)
        try:
            record, projection, requests, _ = V6ChainTests.drive_stub(self, 'uncharged-diagnostics')
        except ResearchError as error:
            self.assertEqual(error.code, 'review_route_capacity_below_floor')
            refusal = error.details['admissions'][0]['details']
            self.assertEqual(refusal['reason'], 'below_floor')
            projection = refusal['projection']
            self.assertLess(projection['record_total'], projection['record_floor'])
            self.assertFalse(self.store.snapshot()['records'].get('review_assignment'))
            state, largest = 'stop', 0
        else:
            state, largest = 'recorded_final', max(requests)
        self.assertEqual(sum(row['count'] for row in projection['units'] if row['kind'] == 'fulltext'), 29)
        print(json.dumps(dict(incident_v6=state, record_total=projection['record_total'],
            record_floor=projection['record_floor'], fixed_parts=projection['fixed_parts'],
            projection_characters=projection['characters'], largest_request=largest), sort_keys=True))
