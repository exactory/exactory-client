"""Oversized slate packets reach a recorded review through parts v3 alone."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import bar_sources, review_parts as parts, review_parts_v3 as v3
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.artifacts import ArtifactStore
from test_review_route_capacity import build_route
import test_research_problem_slate as slate_fixtures
from test_review_output_profile import build_row
from test_review_parts_v3 import make_turn_output
from codex_route_fixtures import record_codex_route, build_codex_response, invoke_codex, limit_codex_input

LIMITED_ROUTE_CHARACTERS = 200000
ROUTE_OUTPUT_TOKENS = 128000


class SlateSeriesTests(unittest.TestCase):
    def setUp(self):
        from research_harness import review_parts_roles
        self.assertIn('problem_slate', review_parts_roles.ADAPTERS)
        for patcher in (
            patch.object(parts, 'NEW_SERIES_PROTOCOLS', (v3.PROTOCOL, parts.PROTOCOL)),
            patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)),
            patch.object(capacity, 'read_profile_priors', return_value=[
                dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
                for kind in ('single', 'read', 'combine', 'check', 'final')])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_case(self, input_tokens=None, *, candidate=True):
        """With input_tokens, the route takes that input token limit and a character limit of 200,000."""
        case = slate_fixtures.ProblemSlateTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        if candidate:
            case.record_candidate()
        case.observe_bars()
        if input_tokens is not None:
            # The observed input limit of a route is the input of its probe less the output reservation.
            case.capacity_probe_tokens = input_tokens + ROUTE_OUTPUT_TOKENS
            limit_codex_input(case, LIMITED_ROUTE_CHARACTERS)
        record_codex_route(case, 'local', output_tokens=ROUTE_OUTPUT_TOKENS)
        return case

    def assign(self, case, identifier='slate-series', **context):
        return case.mutate(protocol.record_assignment, dict(id=identifier, route_id='local', dossier_id=None,
            role='slate', reviewer_id='first', author_id='problem-author', context=context))['result']

    def test_forming_record_shared_by_candidates_rebuilds_both_occurrences(self):
        from research_harness import review_parts_roles
        record = dict(id='forming', forms=[], artifact=dict(path='objects/text', sha256='a' * 64,
                                                          size=8, media_type='text/plain'))
        cite = dict(kind='record', record_kind='forming_call', id='forming', digest=digest(record))
        wrapper = dict(record=record, cite=cite)
        packet = dict(protocol=protocol._PROBLEM_PROTOCOL, role='slate', subject=dict(kind='problem'),
                      candidates=[dict(id='one', forming_records=[wrapper]),
                                  dict(id='two', forming_records=[copy.deepcopy(wrapper)])], evidence=[])
        self.assertEqual(review_parts_roles.find_adapter(dict(role='slate'), packet), 'problem_slate')
        inventory = parts.build_inventory(packet, 'problem_slate')
        self.assertEqual(len(inventory['units']), 1)
        self.assertEqual(len(inventory['units'][0]['occurrences']), 2)
        self.assertEqual(inventory['units'][0]['citations'], [cite])
        rebuilt = list(parts._rebuild_occurrences({'u1': record}, inventory))
        self.assertEqual([item for occurrence, item in rebuilt], [wrapper, wrapper])
        self.assertEqual([occurrence['path'] for occurrence, item in rebuilt],
                         [['candidates', 0, 'forming_records'], ['candidates', 1, 'forming_records']])
        account = dict(id='account', activity='The forming computation.')
        account_cite = dict(kind='record', record_kind='resource_account', id='account', digest=digest(account))
        packet['candidates'][0]['account_records'] = [dict(record=account, cite=account_cite)]
        for candidate in packet['candidates']:
            candidate['digest'] = digest(candidate)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            route = build_route(output=128000)
            admitted = v3.admit_packet({}, artifacts, route, packet, 'problem_slate')
            self.assertEqual(admitted['state'], 'accept', admitted)
            inventory, plan = admitted['inventory'], admitted['plan']
            state = v3.build_series_state([])
            binding = dict(adapter='problem_slate', series_assignment_id='shared', series_key={},
                plan_digest=digest(plan), inventory=v3._put_value(artifacts, inventory), inventory_digest=digest(inventory),
                logical_packet=v3._put_value(artifacts, packet), logical_packet_digest=digest(packet),
                state=v3._put_value(artifacts, state), state_digest=digest(state), _targets=admitted['targets'])
            turn = v3.plan_series_turn(route, artifacts, packet, inventory, plan, state, binding=binding,
                limits_by_kind={kind: capacity.derive_plan_task_limits(plan, kind) for kind in parts.KINDS})
            v3._store_delivery_fragments(artifacts, turn, inventory)
            v3._store_context_entries(artifacts, turn)
            delivered = v3.read_series_delivery({}, artifacts, packets=[turn], inventory=inventory)
            self.assertEqual(delivered['packets'][0]['candidates'], packet['candidates'])

    def test_v2_alone_refuses_oversized_slate_as_before(self):
        case = self.make_case()
        original = protocol.build_packet

        def build_packet(*args, **kwargs):
            packet = original(*args, **kwargs)
            if packet['role'] == 'slate':
                packet['forming_records'] = [dict(record=dict(id='forming', text='x' * 1500000),
                    cite=dict(kind='record', record_kind='forming_call', id='forming', digest='a' * 64))]
                packet.pop('digest')
                packet['digest'] = digest(packet)
            return packet

        with patch.object(protocol, 'build_packet', side_effect=build_packet), \
                patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,)):
            with self.assertRaises(ResearchError) as caught:
                self.assign(case)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')

    def make_imported_packet(self, case, body_characters=24500000, record_characters=4000000):
        import test_problem_forming_import as import_fixtures
        from research_harness import problem_forming
        from test_research_problem_forming import build_forming
        fixture = import_fixtures.FormingImportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bodies = []
        for number in range(106):
            size = body_characters // 106 + (number < body_characters % 106)
            content = ('Synthetic computation %03d. ' % number + 'x' * size)[:size]
            bodies.append(fixture.source.artifacts.put(content.encode(), 'text/plain'))
        call = fixture.source.mutate(problem_forming.record_forming, build_forming('imported-call', forms=[],
            inputs=[dict(description='Synthetic forming inputs.', evidence=bodies)],
            stopping_reason='Synthetic forming record. ' + 'r' * record_characters,
            performer=dict(kind='model_lane', lane_id='fixture', model='fixture-model', effort='reported',
                           prompt=bodies[0], output=bodies[-1])))['result']
        imported = fixture.import_history(destination=case)
        cite = fixture.reference('forming_call', call, imported['source_origin'])
        for identifier in ('candidate-2', 'candidate-3'):
            case.record_candidate(identifier, source=dict(kind='computation',
                reference='The imported finite computations.', evidence=[cite]))
        # Retain the incident's pre-descriptor packet shape with a real imported
        # snapshot. Its synthetic record and bodies come from the ordinary builder.
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            packet = protocol.build_packet(case.read_records(), case.artifacts, None, 'slate', 'first')
        return packet, cite

    def drive_series(self, case, first, final_cite, *, requested_body=None):
        from research_harness.review_series import run_series
        from research_harness.review_excerpts import read_sent_packet
        sizes, kinds, lines = [], [], []
        requested = False
        body_handle = None
        if requested_body is not None:
            first_packet = protocol._read_json(case.artifacts, first['packet'])
            inventory = protocol._read_json(case.artifacts, first_packet[parts.KEY]['inventory'])
            reference, content = requested_body
            unit = next((row for row in inventory['units'] if any(component.get('artifact') == reference
                         for component in row['components'])), None)
            self.assertIsNotNone(unit, 'The forming original must have an addressable body')

        def respond(route, request, credential):
            nonlocal requested, body_handle
            packet = read_sent_packet(request)
            header = packet[parts.KEY]
            if requested_body is not None and header['kind'] == 'read':
                for row in packet[v3.MATERIAL_KEY]:
                    if isinstance(row, dict) and row['digest'] == unit['digest']:
                        body_handle = row['handle']
                    if isinstance(row, str):
                        self.assertNotIn(content, row)
            measured = capacity.measure_request(route, request)['text_characters']
            self.assertLessEqual(measured, header['capacity']['request_bound']['text_characters'])
            sizes.append(measured)
            kinds.append(header['kind'])
            output = (case.build_slate_output(list(case.read_records()['problem_candidate'])) if header['kind'] == 'final'
                      else make_turn_output(packet))
            if header['kind'] == 'final':
                if requested_body is not None:
                    if not requested:
                        requested = True
                        self.assertEqual(body_handle, unit['handle'])
                        return build_codex_response(route, request, dict(kind='requests', source_requests=[],
                            unit_requests=[dict(unit=body_handle, body=reference['sha256'])]))
                    self.assertIn(content, [row['text'] for row in packet['review_parts_requests']])
                output['value']['evidence'] = [final_cite]
                for assurance in output['value']['assurances']:
                    assurance.update(evidence=[final_cite], reason='Not assessed in this slate review.')
            return build_codex_response(route, request, output)

        def emit(row):
            if lines and lines[-1]['state'] == 'record':
                self.assertEqual(row['state'], 'done', row)
            lines.append(row)

        # Rendering is covered by the view suites. Re-rendering the entire 28.6M
        # packet after every mutation would dominate this delivery regression.
        with patch('research_harness.review_transport.send_request', side_effect=respond), \
                patch('research_harness.review_series._render_views'), \
                patch('research_harness.cli.render_workspace_views'):
            code = run_series(case.store, first['id'], emit=emit)
        self.assertEqual(code, 0, lines[-1])
        self.assertEqual(lines[-1]['state'], 'done')
        records = case.read_records()
        reviews = [row for row in records['value_review'].values() if row['payload']['stage'] == 'slate']
        self.assertEqual(len(reviews), 1)
        review = reviews[0]
        self.assertEqual(review['payload']['value']['evidence'], [final_cite])
        verified = parts.verify_series(records, case.artifacts, review['assignment_id'])
        self.assertTrue(verified['complete'], verified['issues'])
        return dict(turns=len(sizes), kinds={kind: kinds.count(kind) for kind in set(kinds)},
                    largest_request=max(sizes))

    def test_A1_incident_28600000_character_slate_reaches_value_review(self):
        case = self.make_case()
        packet, cite = self.make_imported_packet(case)
        self.assertEqual(len(packet['evidence']), 106)
        self.assertEqual(sum(len(entry['content']) for entry in packet['evidence']), 24500000)
        self.assertGreater(len(parts.render_json(packet)), 28500000)
        self.assertLess(len(parts.render_json(packet)), 28700000)
        original_evidence = protocol._evidence

        def build_evidence(artifacts, value, *args, **kwargs):
            if isinstance(value, dict) and value.get('role') == 'slate':
                return copy.deepcopy(packet['evidence'])
            return original_evidence(artifacts, value, *args, **kwargs)

        # This inventory never changes. Reuse its real initial bodies instead of
        # reopening the legacy import snapshot once per artifact on every turn.
        # The smaller imported journey exercises the unmodified evidence builder.
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch.object(protocol, '_evidence', side_effect=build_evidence):
            first = self.assign(case)
            self.assertEqual(first[parts.KEY]['protocol'], v3.PROTOCOL)
            result = self.drive_series(case, first, cite)
        print('SLATE_INCIDENT_MEASUREMENTS ' + json.dumps(result, sort_keys=True))

    def test_targets_and_key_match_the_slate_contract(self):
        from research_harness import review_composition, review_parts_slate as slate
        case = self.make_case()
        packet = protocol.build_packet(case.read_records(), case.artifacts, None, 'slate', 'first')
        rule = dict(review_composition.find_rule('slate', packet), adapter=None)
        self.assertEqual(slate.list_targets(packet), review_composition.list_targets(rule, packet))
        self.assertEqual(slate.compute_series_key(packet), dict(candidates=packet['candidate_set_digest'],
                                                              bars=digest(packet['bars'])))

    def test_A1_imported_record_cite_verifies_in_recorded_slate_final(self):
        case = self.make_case()
        packet, cite = self.make_imported_packet(case, body_characters=1400000, record_characters=1000)
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            first = self.assign(case)
            self.drive_series(case, first, cite)

    def test_small_slate_output_recovery_does_not_enter_the_adapter(self):
        from research_harness.review_single_request import build_output_replacement
        case = self.make_case()
        self.make_imported_packet(case, body_characters=10000, record_characters=1000)
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            first = self.assign(case)
        self.assertIsNone(build_output_replacement(case.read_records(), case.artifacts, first))

    def test_ordinary_assignment_keeps_packet_prompt_and_request_bytes(self):
        case = self.make_case()
        from research_harness import review_parts_roles, research_decisions
        def prepare_only(store, operation, payload, prepare, **kwargs):
            return dict(result=prepare(case.read_records(), payload)[1])

        with patch.object(protocol, '_prepare_review_mutation', side_effect=prepare_only):
            with patch.dict(review_parts_roles.ADAPTERS, {}, clear=True):
                baseline = self.assign(case)
            for preferred in ((parts.PROTOCOL,), (v3.PROTOCOL, parts.PROTOCOL)):
                with patch.object(parts, 'NEW_SERIES_PROTOCOLS', preferred):
                    assignment = self.assign(case)
                self.assertEqual(assignment, baseline)
                self.assertNotIn(parts.KEY, assignment)
        actual = self.assign(case)
        self.assertEqual(actual, baseline)
        output = case.build_slate_output(['candidate-1'])
        invoke_codex(case, baseline['id'], output)
        case.mutate(research_decisions.record_value_review, dict(output, id='small-slate-review', dossier_id=None,
                                                         assignment_id=baseline['id']))

    def test_recorded_v2_and_v3_slate_sessions_keep_their_bytes(self):
        for rules in (bar_sources.PROTOCOL, bar_sources.PROTOCOL_V3):
            with self.subTest(rules=rules):
                case = self.make_case()
                with patch.object(bar_sources, 'SESSION_PROTOCOL', rules):
                    first = self.assign(case)
                frozen = case.artifacts.read(first['packet'])
                request = protocol.build_assignment_request(case.read_records(), case.artifacts, first)
                output = case.build_slate_output(['candidate-1'])
                output['value']['status'] = 'unresolved'
                output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
                invoke_codex(case, first['id'], output)
                from research_harness import review_parts_roles
                adapters = {key: value for key, value in review_parts_roles.ADAPTERS.items() if key != 'problem_slate'}
                def prepare_only(store, operation, payload, prepare, **kwargs):
                    return dict(result=prepare(case.read_records(), payload)[1])
                with patch.object(protocol, '_prepare_review_mutation', side_effect=prepare_only), \
                        patch.dict(review_parts_roles.ADAPTERS, adapters, clear=True):
                    baseline = self.assign(case, 'slate-next', prior_assignment_id=first['id'])
                child = self.assign(case, 'slate-next', prior_assignment_id=first['id'])
                self.assertEqual(child, baseline)
                self.assertNotIn(parts.KEY, child)
                self.assertEqual(case.artifacts.read(first['packet']), frozen)
                self.assertEqual(protocol.build_assignment_request(case.read_records(), case.artifacts, first), request)
                child_packet = json.loads(case.artifacts.read(child['packet']))
                self.assertEqual(child_packet.get('source_rules'),
                                 rules if rules == bar_sources.PROTOCOL_V3 else None)

    def test_v4_session_admission_precedes_the_slate_adapter(self):
        from research_harness import review_session_v4 as session, review_parts_slate as slate
        case = self.make_case()
        logical, cite = self.make_imported_packet(case, body_characters=1400000, record_characters=1000)
        original = protocol.build_packet
        def build_packet(*args, **kwargs):
            return copy.deepcopy(logical) if args[3] == 'slate' else original(*args, **kwargs)
        with patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)), \
                patch.object(protocol, 'build_packet', side_effect=build_packet), \
                patch.object(v3, 'admit_packet', wraps=v3.admit_packet) as planner:
            first = self.assign(case)
        planner.assert_not_called()
        packet = json.loads(case.artifacts.read(first['packet']))
        self.assertIn('session_chain', first)
        self.assertEqual(packet['source_rules'], session.PROTOCOL)
        self.assertFalse(slate.applies(first, packet))
        self.assertNotIn(parts.KEY, first)

    def test_a_v2_final_of_the_slate_adapter_is_refused_as_an_unknown_binding(self):
        # Row P9 of the second writer pass: the slate adapter admits the parts protocol v3 alone and has no v2 final.
        with self.assertRaises(ResearchError) as refused:
            protocol.canonical_prompt('slate', orchestration='problem_creation',
                                      review_parts=dict(adapter='problem_slate', kind='final'))
        self.assertEqual(refused.exception.code, 'invalid_review_context')

    def test_new_slate_series_and_session_deliver_place_review(self):
        # 0.91.6 writes place_review into every new slate packet. A slate series of parts v3 and a slate session of
        # the rules v4 classify that member and deliver it: in the frame of each part and in the frame of each session
        # turn. A read turn of the series selects no version of the slate places paragraph (decision 3 of the second
        # writer pass); a final, page or closing turn selects its version 2.
        from research_harness import review_prompt_paragraphs as paragraphs, review_session_v4 as session
        member = {'protocol': paragraphs.SLATE_PLACES_PROTOCOL}
        case = self.make_case()
        logical, cite = self.make_imported_packet(case, body_characters=1400000, record_characters=1000)
        self.assertEqual(logical[paragraphs.PLACE_REVIEW_KEY], member)
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            first = self.assign(case)
        self.assertEqual(first[parts.KEY]['protocol'], v3.PROTOCOL)
        self.assertEqual(json.loads(case.artifacts.read(first['packet']))[paragraphs.PLACE_REVIEW_KEY], member)
        self.assertEqual(first[parts.KEY]['kind'], 'read')
        selected = first['prompt']['parameters'].get(paragraphs.PARAMETER, [])
        self.assertFalse({paragraphs.SLATE_PLACES_PROTOCOL, 'exactory-problem-slate-places-v2'} & set(selected))
        case = self.make_case()
        logical, cite = self.make_imported_packet(case, body_characters=1400000, record_characters=1000)
        original = protocol.build_packet
        def build_packet(*args, **kwargs):
            return copy.deepcopy(logical) if args[3] == 'slate' else original(*args, **kwargs)
        with patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3)), \
                patch.object(protocol, 'build_packet', side_effect=build_packet):
            first = self.assign(case)
        self.assertIn('session_chain', first)
        frame = session.read_session_binding(json.loads(case.artifacts.read(first['packet'])))['frame']
        self.assertEqual(frame[paragraphs.PLACE_REVIEW_KEY], member)

    def test_recorded_v2_slate_repair_final_prompt_keeps_its_bytes(self):
        from research_harness import review_parts_roles
        parameters = dict(orchestration='problem_creation', repair=True, excerpts=True,
            review_parts=dict(protocol=parts.PROTOCOL, adapter='context_repair', kind='final'))
        adapters = {key: value for key, value in review_parts_roles.ADAPTERS.items() if key != 'problem_slate'}
        with patch.dict(review_parts_roles.ADAPTERS, adapters, clear=True):
            expected = protocol.canonical_prompt('slate', **parameters)
        self.assertEqual(protocol.canonical_prompt('slate', **parameters), expected)

    def test_slate_repair_composition_retains_its_ordinary_targets(self):
        from research_harness import review_composition, review_parts_roles
        case = self.make_case()
        packet = protocol.build_packet(case.read_records(), case.artifacts, None, 'slate', 'first')
        packet['context_repair'] = {}
        adapters = {key: value for key, value in review_parts_roles.ADAPTERS.items() if key != 'problem_slate'}
        with patch.dict(review_parts_roles.ADAPTERS, adapters, clear=True):
            expected = review_composition.list_targets(review_composition.find_rule('slate', packet), packet)
        targets = review_composition.list_targets(review_composition.find_rule('slate', packet), packet)
        self.assertEqual(targets, expected)
        self.assertIn('candidate-1', [row['id'] for row in targets])

    def test_route_discovery_offers_local_parts_for_an_oversized_slate(self):
        case = self.make_case()
        packet, cite = self.make_imported_packet(case, body_characters=1400000, record_characters=1000)
        value = dict(id='slate', role='slate', context={}, route_id='local', dossier_id=None,
                     reviewer_id='first', author_id='problem-author', subject=packet['subject'])
        prompt = protocol.canonical_prompt('slate', orchestration='problem_creation')
        routes = capacity.find_serving_routes(case.read_records(), 'slate', packet,
            artifacts=case.artifacts, assignment=value, prompt=prompt)
        self.assertIn(('local', 'parts'), [(row['route_id'], row['delivery']) for row in routes])

    def import_one_body(self, case, content=b'x', media_type='text/plain', *, native=(), reason_characters=145000,
                        import_id='import-1', forming_id='imported-call', cite_candidate=True):
        from research_harness import problem_forming
        from test_problem_forming_import import FormingImportTests
        from test_research_problem_candidates import build_candidate
        from test_research_problem_forming import build_forming

        fixture = FormingImportTests()
        self.addCleanup(fixture.doCleanups)
        source = fixture.fixture()
        body = source.artifacts.put(content, media_type) if content is not None else None
        call = source.mutate(problem_forming.record_forming, build_forming(
            forming_id, forms=[], stopping_reason='a' * reason_characters,
            **({'inputs': [dict(description='One input', evidence=[body])]} if body else {})))['result']
        candidate = build_candidate()
        candidate['source']['evidence'] = [fixture.reference('forming_call', call)]
        source.mutate(slate_fixtures.problem_candidates.record_candidate, candidate)
        imported = fixture.import_history(destination=case, source=source, identifier=import_id)
        cite = fixture.reference('forming_call', call, imported['source_origin'])
        if cite_candidate:
            case.record_candidate('imported-candidate', source=dict(kind='computation', reference='Imported record',
                                                                  evidence=[cite, *native]))
        return cite, body

    def test_a_slate_over_the_token_limit_of_its_route_starts_a_series(self):
        """Drive a token-overflow import through each bounded read and its recorded final."""
        from research_harness.review_excerpts import read_sent_packet

        case = self.make_case(input_tokens=160000, candidate=False)
        body_text = 'Unique text of the imported forming record.'
        cite, body = self.import_one_body(case, body_text.encode(), reason_characters=140000)
        with patch.object(capacity, 'measure_request', wraps=capacity.measure_request) as measurements:
            first = self.assign(case)
        for call in measurements.call_args_list:
            route, ordinary = call.args[:2]
            sent = read_sent_packet(ordinary) or {}
            if sent.get('role') == 'slate' and parts.KEY not in sent:
                break
        else:
            self.fail('No ordinary slate request was measured')
        records = case.read_records()
        single = capacity.measure_request(route, ordinary, records=records, artifacts=case.artifacts)
        self.assertLessEqual(single['characters'], single['character_limit'])
        self.assertGreater(single['tokens_upper'], single['token_limit'])
        self.assertEqual((first[parts.KEY]['protocol'], first[parts.KEY]['adapter']), (v3.PROTOCOL, 'problem_slate'))
        request = protocol.build_assignment_request(records, case.artifacts, first)
        self.assertTrue(capacity.measure_request(route, request, records=records, artifacts=case.artifacts)['fits'])

        self.drive_series(case, first, cite, requested_body=(body, body_text))

    def test_an_imported_binary_body_requires_text_before_the_first_read(self):
        """Refuse a required binary, then deliver its declared text in a recorded series."""
        from types import SimpleNamespace
        from continuity_forms_fixtures import fork_store
        from research_harness.integration import pin_artifact

        case = self.make_case(input_tokens=160000, candidate=False)
        _, body = self.import_one_body(case, b'%PDF-1.4', 'application/pdf')
        with patch.object(protocol.transport, 'send_request') as send:
            with self.assertRaises(ResearchError) as caught:
                self.assign(case)
        send.assert_not_called()
        error = caught.exception
        self.assertEqual(error.code, 'review_route_capacity_below_floor')
        self.assertEqual(error.details['reason'], 'body_without_text_form')
        [answer] = error.details['then']
        [capture] = answer['steps']
        self.assertEqual(capture['command'], 'artifact')
        self.assertEqual(capture['body']['sha256'], body['sha256'])
        self.assertNotIn('slate-series', case.read_records()['review_assignment'])

        with fork_store(SimpleNamespace(case=case)):
            path = 'captured-body.txt'
            (case.store.root / path).write_text('Complete synthetic source text.', encoding='utf-8')
            captured = case.mutate(pin_artifact, dict(capture['payload'], path=path))['result']
            first = self.assign(case)
            packet = json.loads(case.artifacts.read(first['packet']))
            logical = parts.read_logical_packet(case.artifacts, packet[parts.KEY])
            self.assertIn(captured['artifact'], [row['artifact'] for row in logical['evidence']])
            self.assertEqual(first[parts.KEY]['protocol'], v3.PROTOCOL)

    def test_a_slate_within_both_limits_of_its_route_stays_a_single_request(self):
        case = self.make_case(input_tokens=40000)
        first = self.assign(case)
        self.assertNotIn(parts.KEY, first)
        records = case.read_records()
        request = protocol.build_assignment_request(records, case.artifacts, first)
        measure = capacity.measure_request(records['review_route']['local'], request, records=records,
                                           artifacts=case.artifacts)
        self.assertTrue(measure['fits'], measure)

    def test_a_recorded_single_slate_between_the_limits_keeps_its_form(self):
        # A release without the token admission recorded such a slate as one request, as in incident 20261112012956.
        # The single request keeps its bytes, a continuation follows its recorded form, and it reaches its review.
        from research_harness import research_decisions, review_parts_slate as slate
        from research_harness.review_single_request import describe_assignment_delivery
        case = self.make_case(input_tokens=21000)
        with patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,)), \
                patch.object(capacity, 'require_covered_assignment'):
            stored = self.assign(case)
        records, artifacts = case.read_records(), case.artifacts
        route = records['review_route']['local']
        request = protocol.build_assignment_request(records, artifacts, stored)
        measure = capacity.measure_request(route, request, records=records, artifacts=artifacts)
        self.assertLessEqual(measure['characters'], measure['character_limit'])
        self.assertGreater(measure['tokens_upper'], measure['token_limit'])
        self.assertNotIn(parts.KEY, stored)
        self.assertEqual(describe_assignment_delivery(records, artifacts, stored)['form'], 'single')
        self.assertFalse(slate.can_start_series(records, artifacts,
            dict(role='slate', context=dict(prior_assignment_id=stored['id'])), route,
            json.loads(artifacts.read(stored['packet'])), stored['prompt']))
        output = case.build_slate_output(sorted(records['problem_candidate']))
        invoke_codex(case, stored['id'], output)
        sent = json.loads(artifacts.read(case.read_records()['review_attempt']['attempt-' + stored['id']]['request']))
        self.assertEqual(sent, request)
        case.mutate(research_decisions.record_value_review, dict(output, id='recorded-single-slate', dossier_id=None,
                                                                 assignment_id=stored['id']))


if __name__ == '__main__':
    unittest.main()
