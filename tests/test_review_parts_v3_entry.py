"""Drive the entry and recovery boundaries of a planned parts request."""
import copy
from contextlib import contextmanager
import itertools
import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_route_fixtures import (bind_codex_response, build_codex_response, limit_codex_input, probe_codex_route,
                                  record_codex_route)
from research_harness import bar_sources, cli, research_decisions as decisions
from research_harness import review_failure_class as failures, review_preflight as preflight
from research_harness import review_parts as legacy, review_parts_v3 as parts
from research_harness import review_prompt_paragraphs as paragraphs
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_plan_steps, review_single_request, stop_classes
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from research_harness.review_excerpts import read_sent_packet
from research_harness.review_refusal_replacement import validate_replacement_origin
from research_harness.review_series import run_series
from research_harness.storage import Store

from review_parts_fixtures import PartsResultCase, make_output
import test_research_problem_reviews as problem_fixtures
from test_review_output_profile import build_row
import test_review_parts_repair_journey as repair_fixtures
from test_review_parts_v3 import make_packet, make_turn_output
import test_review_parts_v3_turn as turn_fixtures
from test_review_route_capacity import build_route


V2_FIRST = (legacy.PROTOCOL, parts.PROTOCOL)
V3_FIRST = tuple(reversed(V2_FIRST))
BASE = dict(orchestration='problem_creation')
POSITION_FIELDS = ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')


def refusal_payload(assignment, identifier):
    return dict({key: assignment[key] for key in POSITION_FIELDS}, id=identifier,
                context=dict(assignment['context'], output_refusal=dict(assignment_id=assignment['id'])))


def profile_priors():
    return [dict(build_row(level=None, kind=kind, thinking=1), largest_output_tokens=12)
            for kind in ('single', 'read', 'combine', 'check', 'final')]


def adjudication(statement):
    packet = make_packet(0)
    packet.pop('material_objections')
    packet.update(role='adjudicator', protocol='exactory-focused-review-evidence-v2',
                  objection=dict(id='O', claim='A material objection.'))
    packet['problem_evidence']['claim']['statement'] = statement
    packet['problem_evidence_digest'] = digest(packet['problem_evidence'])
    return packet


class ExactEntryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.route = build_route()

    def prepare(self, packet, **options):
        role = packet['role']
        return parts.prepare_packet({}, self.artifacts, dict(id='entry', role=role, context={}),
            self.route, packet, protocol.canonical_prompt(role, **BASE), [],
            base_prompt_parameters=BASE, **dict({'entry': 'task'}, **options))

    def wire(self, packet, role):
        packet = dict(packet)
        declaration = packet.pop(capacity.CAPACITY_REQUEST_KEY)
        packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
        plan = capacity.read_chain_capacity_plan(self.artifacts, dict(capacity_plan=declaration['plan']))
        prompt = legacy.build_packet_prompt(role, BASE, packet)
        request = protocol.transport.request_body(self.route, prompt['text'], packet, [], order=())
        report = preflight.build_report({}, self.artifacts, self.route, role, prompt, packet, request,
            capacity_plan=plan, turn_kind=declaration['kind'])
        preflight.check_request(report, request, packet=packet, artifacts=self.artifacts)
        return plan, report

    def test_utf8_first_read_uses_a_smaller_level_or_refuses_the_fixed_labels(self):
        lowerable = self.prepare(adjudication('界' * 125000))
        self.assertIsNotNone(lowerable)
        plan, receipt = self.wire(lowerable, 'adjudicator')
        self.assertEqual(receipt['request_measure']['measure'], 'bytes')
        self.assertLess(lowerable[legacy.KEY]['capacity']['material_allowance'],
                        plan['task']['read']['material_allowance'])
        self.assertLess(lowerable[legacy.KEY]['unit_characters'], 125000)

        describe = capacity.describe_route_capacity

        def tight(*args, **kwargs):
            limits = describe(*args, **kwargs)
            limits['input_tokens']['value'] = 350000
            return limits

        with patch.object(capacity, 'describe_route_capacity', side_effect=tight):
            irreducible = self.prepare(adjudication('界' * 125000))
        self.assertTrue(irreducible is None, 'The target table alone exceeds the pinned byte limit.')

    def test_compact_areas_are_ordinary_only_with_either_notice_form(self):
        from research_harness import review_field_classes

        packet = make_packet(1, 100)
        packet['problem_evidence'].update(value={'accounts': {'answer': 'none', 'replacement': True},
            'changes_if_resolved': 'A change.', 'accounts_version': 2},
            account_records=[{'cite': {'record_kind': 'probe'}}])
        packet['problem_areas'] = [dict(area='math.CO', reason='Counting.')]
        compact = copy.deepcopy(packet)
        paragraphs.preserve_prompt_conditions('result', packet, compact)
        compact['problem_evidence'] = {'delivery': 'delivered-in-an-earlier-turn', 'note': 'In earlier evidence.'}
        compact.pop('problem_evidence_digest')
        self.assertIn('areas', compact[paragraphs.CONDITIONS])
        limit = parts.measure_turn_prompts('result', BASE, 'problem_result')['read']
        table = review_field_classes.FIELD_CLASSES['result'][packet['protocol']]
        # The archived lane lacks the T5 declaration. Supply exactly its row so
        # this test reaches the old selector rather than failing classification.
        declaration = {'class': 'fixed', 'characters': 1000, 'overflow_unit': True}
        packet['problem_evidence']['account_records'] = []
        areas = next(row.versions[-1][0] for row in paragraphs.PARAGRAPHS if row.name == 'areas')
        with patch.dict(table, {paragraphs.CONDITIONS: table.get(paragraphs.CONDITIONS, declaration)}):
            for shape in (packet, compact):
                for form in (None, 'whole', 'bounded'):
                    with self.subTest(compact=shape is compact, form=form):
                        notice = {'form': form} if form else None
                        turn = self.prepare(shape, refusal_notice=notice)
                        self.assertEqual(turn['problem_areas'], [])
                        prompt = legacy.build_packet_prompt('result', BASE, turn)
                        self.assertNotIn(areas, prompt['parameters'].get(paragraphs.PARAMETER, []))
                        self.assertLessEqual(len(prompt['text']), limit)

    def test_equal_record_digests_replay_by_identity_and_missing_owner_is_refused(self):
        row = dict(id='r', reason='same stored row')
        packet = make_packet(0)
        packet['problem_evidence']['openness_searches'] = [dict(search=row, sources=[row])]
        packet['problem_evidence']['openness_minimum'] = row
        packet['problem_evidence_digest'] = digest(packet['problem_evidence'])
        turn = self.prepare(packet)
        header = turn[legacy.KEY]
        inventory = parts._read_value(self.artifacts, header['inventory'])
        plan = capacity.read_chain_capacity_plan(self.artifacts,
            dict(capacity_plan=turn[capacity.CAPACITY_REQUEST_KEY]['plan']))
        matching = [item for item in inventory['units'] if item['digest'] == digest(row)]
        self.assertEqual(len(matching), 2)
        self.assertNotEqual(matching[0]['record_identity'], matching[1]['record_identity'])
        self.assertEqual(turn['problem_evidence']['openness_minimum']['handle'], matching[0]['handle'])
        with self.subTest('replay'):
            self.assertTrue(parts._validate_frozen_inventory(legacy.read_logical_packet(self.artifacts, header), inventory))
        with self.subTest('missing owner'), self.assertRaises(ResearchError) as invalid:
            incomplete = dict(inventory, units=[item for item in inventory['units'] if item not in matching])
            parts._build_frame(self.route, incomplete, plan, 'read')
        self.assertEqual(invalid.exception.code, 'review_parts_inventory_changed')

    def test_serving_parts_shares_the_common_version_and_entry_history(self):
        packet = make_packet(0)
        packet['problem_evidence']['openness_searches'] = [dict(search=dict(id='s', reason='short'), sources=[])]
        value = dict(id='entry', role='result', context={})
        history = [dict(role='assistant', content='a' * 160000)]
        for version in (1, 2):
            with self.subTest(version=version), patch.object(legacy, 'NEW_SERIES_PROTOCOLS', V3_FIRST), \
                    patch.object(protocol.transport, 'local_input_limit', return_value=125000):
                params = dict(BASE, common_version=version)
                ordinary = protocol.canonical_prompt('result', **params)
                normal = legacy.prepare_packet({}, self.artifacts, value, self.route, packet, ordinary, history,
                                               base_prompt_parameters=params)
                self.assertEqual(normal[legacy.KEY]['protocol'], parts.PROTOCOL)
                estimate = capacity._build_serving_parts({}, self.artifacts, value, self.route, packet, ordinary, history)
                normal.pop(capacity.CAPACITY_REQUEST_KEY)
                prompt = legacy.build_packet_prompt('result', params, normal)
                request = protocol.transport.request_body(self.route, prompt['text'], normal, [])
                self.assertEqual(estimate, request)


class StoredEntryTests(PartsResultCase):
    output_tokens = 128000

    # Reuse the two repair fixture operations without repeating unrelated journeys.
    contaminate = repair_fixtures.ResultRepairJourneyTests.contaminate
    repair_assignment = repair_fixtures.ResultRepairJourneyTests.repair_assignment
    cap_attempt = turn_fixtures.RecordedTurnTests.cap_attempt
    def setUp(self):
        super().setUp()
        selected = patch.object(legacy, 'NEW_SERIES_PROTOCOLS', V3_FIRST)
        selected.start()
        self.addCleanup(selected.stop)
        plan = capacity.plan_chain_capacity

        def request_plan(*args, **kwargs):
            return plan(*args, **dict(kwargs, final_pages=False))

        requests = patch.object(capacity, 'plan_chain_capacity', side_effect=request_plan)
        requests.start()
        self.addCleanup(requests.stop)

    def output(self, assignment):
        packet = self.packet(assignment)
        header = packet[legacy.KEY]
        return make_turn_output(packet) if header['protocol'] == parts.PROTOCOL else make_output(packet)

    def plan(self, assignment):
        return plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])

    def take(self, step):
        operations = {'review-assignment': protocol.record_assignment, 'value-review': decisions.record_value_review}
        self.assertIn(step['command'], operations)
        return self.case.mutate(operations[step['command']], step['payload'])['result']

    def test_ordinary_stored_first_read_keeps_its_v2_replacement(self):
        self.oversized(count=6, characters=65000, limit=330000)
        with patch.object(capacity, 'read_profile_priors', return_value=profile_priors()):
            with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', V2_FIRST):
                first = self.assign()
                self.assertEqual(first[legacy.KEY]['protocol'], legacy.PROTOCOL)
                self.assertIsNone(self.packet(first)[legacy.KEY].get('entry'))
                self.cap_attempt(first, 'over')
                step = self.plan(first)
                self.assertEqual(step['state'], 'replace', step.get('code'))
            with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', V3_FIRST):
                self.assertEqual(self.plan(first), step)
                retry = self.take(step)
                self.assertEqual(retry[legacy.KEY]['protocol'], legacy.PROTOCOL)
                self.assertNotIn('output_refusal', self.invoke(retry))

    def test_refused_single_keeps_the_whole_notice_when_entering_v3(self):
        self.oversized(count=1, characters=2000, limit=1300000)
        first = self.assign('single')
        self.assertNotIn(legacy.KEY, first)
        bad = self.final_output()
        bad['value']['status'] = 'invalid'
        self.invoke(first, bad)
        with self.assertRaises(ResearchError):
            self.case.mutate(decisions.record_value_review,
                dict(bad, id='bad', dossier_id=None, assignment_id=first['id']))
        noticed = self.case.mutate(protocol.record_assignment, refusal_payload(first, 'noticed'))['result']
        frozen = self.packet(noticed)['output_refusal']
        self.assertEqual(frozen['form'], 'whole')
        self.assertGreater(len(legacy.render_json(frozen)), 512)
        self.cap_attempt(noticed, 'noticed-at-cap')
        step = self.plan(noticed)
        self.assertEqual(step['state'], 'replace', step.get('code'))
        read = self.take(step)
        delivered = self.packet(read)
        self.assertEqual(delivered[legacy.KEY]['protocol'], parts.PROTOCOL)
        self.assertEqual(delivered['output_refusal'], frozen)
        validate_replacement_origin(self.case.read_records(), self.case.artifacts, read, delivered)
        self.assertNotIn('output_refusal', self.invoke(read))

    def test_ordinary_repair_conversion_stores_the_exact_planned_request(self):
        self.oversized(count=2, characters=85000, limit=1300000)
        with patch.object(review_single_request, 'describe_task_entry', return_value=None):
            parent = self.assign('parent')
            binding = self.contaminate(parent)
            first = self.repair_assignment(parent, binding)
        self.assertNotIn(legacy.KEY, first)
        self.cap_attempt(first, 'repair-at-cap')
        step = self.plan(first)
        self.assertEqual(step['state'], 'replace', step.get('code'))
        prepared = failures.plan_output_replacement(self.case.read_records(), self.case.artifacts,
            first, include_packet=True, replacement_id=step['payload']['id'])
        self.assertEqual(prepared['preflight']['protocol'], preflight.PLANNED_PROTOCOL)
        child = self.take(step)
        packet = self.packet(child)
        self.assertEqual(packet, prepared['packet'])
        self.assertEqual(child['prompt'], prepared['prompt'])
        self.assertEqual(child['capacity_plan'], prepared['capacity_plan'])
        self.assertNotIn(capacity.CAPACITY_REQUEST_KEY, packet)
        report = preflight.build_assignment_report(self.case.read_records(), self.case.artifacts, child)
        self.assertTrue(report['plan_comparison']['fits'])
        self.assertNotIn('output_refusal', self.invoke(child))

    def test_serving_repair_matches_the_independently_recorded_first_repair(self):
        self.oversized(count=1, characters=2000, limit=1300000)
        with patch.object(protocol, 'COMMON_VERSION', 1):
            parent = self.assign('older-parent')
        self.assertEqual(protocol.find_common_prompt_version(parent['prompt']), 1)
        binding = self.contaminate(parent)
        records = self.case.read_records()
        route = records['review_route']['repair-route']
        assignment = dict(parent, id='fixed-repair')
        with patch.object(review_single_request, 'describe_task_entry', return_value='task'):
            estimated = capacity._build_serving_repair(records, self.case.artifacts, assignment, parent, route)
            actual = self.repair_assignment(parent, binding, identifier=assignment['id'])
        self.assertEqual(actual[legacy.KEY]['protocol'], parts.PROTOCOL)
        # A turn of the parts protocol v3 renders its base at the common version 5 (decision 1 of the second writer pass).
        self.assertEqual(protocol.find_common_prompt_version(actual['prompt']), protocol.TURN_COMMON_VERSION)
        actual_request = self.request(actual)
        self.assertEqual(estimated['instructions'], actual_request['instructions'])
        projected = dict(estimated, instructions=actual_request['instructions'])
        threshold = capacity.measure_request(route, projected, records=records,
                                             artifacts=self.case.artifacts)['tokens_upper']
        describe = capacity.describe_route_capacity

        def limit(*args, **kwargs):
            manifest = describe(*args, **kwargs)
            manifest['input_tokens']['value'] = budget
            return manifest

        for budget, fits in ((threshold - 1, False), (threshold, True)):
            with self.subTest(budget=budget), patch.object(capacity, 'describe_route_capacity', side_effect=limit), \
                    patch.object(capacity, '_build_serving_repair', return_value=estimated):
                serving = capacity.find_serving_routes(records, actual['role'], self.packet(actual),
                    artifacts=self.case.artifacts, assignment=actual, prompt=actual['prompt'])
            self.assertEqual(any(row['route_id'] == route['id'] and row['delivery'] == 'reassessment'
                                 for row in serving), fits)

    def test_serving_estimate_lists_the_route_at_the_recorded_v2_repair_boundary(self):
        """The serving check estimates the request that recording stores for the repair of a v2 series."""
        self.oversized(count=6, characters=65000, limit=330000)
        with patch.object(capacity, 'read_profile_priors', return_value=profile_priors()), \
                patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)):
            first = self.assign('entry')
            self.assertEqual(first[legacy.KEY]['protocol'], legacy.PROTOCOL)
            self.assertNotIn('output_refusal', self.invoke(first))
            binding = self.contaminate(first)
            source = self.case.root, self.case.expected, self.case.sequence
            with self._copy_store(*source):
                child = self._take_repair_handoff(first, binding)
                self.assertEqual(child[legacy.KEY]['protocol'], legacy.PROTOCOL)
                records = self.case.read_records()
                boundary = capacity.measure_request(records['review_route']['repair-route'], self.request(child),
                    records=records, artifacts=self.case.artifacts)['tokens_upper']
            describe = capacity.describe_route_capacity

            def limit(route, *args, **kwargs):
                manifest = describe(route, *args, **kwargs)
                if route['id'] == 'repair-route':
                    manifest['input_tokens']['value'] = boundary
                return manifest

            with self._copy_store(*source), patch.object(capacity, 'describe_route_capacity', side_effect=limit):
                records = self.case.read_records()
                head = records['review_assignment']['entry']
                step = self.plan(head)['details']['step']
                prospect = dict(copy.deepcopy(step['payload']), id='fixed-repair', subject=head['subject'])
                serving = capacity.find_serving_routes(records, head['role'], self.packet(head),
                    artifacts=self.case.artifacts, assignment=prospect, prompt=head['prompt'])
                self.assertIn(('repair-route', 'reassessment'), [(row['route_id'], row['delivery']) for row in serving])
                child = self._take_repair_handoff(head, binding)
                self.assertNotIn('output_refusal', self._run_step(self.plan(child), child)[0])

    def test_unrepresentable_v3_unit_admits_the_release_first_read(self):
        """A record identity longer than a v3 unit row declines v3, and v2 admits the read that the release admits."""
        limit_codex_input(self, 100000)
        self.case.expected['openness_searches'] = []
        for identifier in ('s' * 5000, 'short'):
            search = dict(id=identifier, queries=['finite'], reason='A' * 65000)
            self.case.put('literature_search', identifier, search)
            self.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        source = self.case.root, copy.deepcopy(self.case.expected), self.case.sequence
        rows = []
        with patch.object(capacity, 'read_profile_priors', return_value=profile_priors()):
            for order in ((legacy.PROTOCOL,), V3_FIRST):
                with self.subTest(order=order[0]), self._copy_store(*source), \
                        patch.object(legacy, 'NEW_SERIES_PROTOCOLS', order):
                    head = self.assign('entry')
                    self.assertEqual(head[legacy.KEY]['protocol'], legacy.PROTOCOL)
                    row = self.case.read_records()['review_assignment']['entry']
                    rows.append([row, self.packet(row), self.request(row)])
                    self.assertNotIn('output_refusal', self._run_step(self.plan(head), head)[0])
        self.assertEqual(rows[1], rows[0])

    @contextmanager
    def _copy_store(self, source, expected, sequence):
        case = self.case
        original = case.root, case.store, case.artifacts, case.sequence, case.expected
        with tempfile.TemporaryDirectory() as directory:
            try:
                case.root = Path(directory) / 'study'
                shutil.copytree(source, case.root)
                case.store, case.artifacts = Store(case.root), ArtifactStore(case.root)
                case.sequence, case.expected = sequence, copy.deepcopy(expected)
                yield
            finally:
                case.root, case.store, case.artifacts, case.sequence, case.expected = original

    def _take_repair_handoff(self, assignment, binding):
        plan = self.plan(assignment)
        self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_context_contaminated'))
        step = plan['details']['step']
        payload = copy.deepcopy(step['payload'])
        payload.update(id='fixed-repair', reviewer_id='replacement', route_id='repair-route')
        payload['context']['context_repair'].update({name: binding[name] for name in ('probe_id', 'reason')})
        return self.take(dict(command=step['command'], payload=payload))

    def test_stored_v2_repair_handoff_preserves_the_tagged_child(self):
        fixture = os.environ.get('EXACTORY_TAGGED_PARTS_REPAIR')
        with patch.object(capacity, 'read_profile_priors', return_value=profile_priors()):
            if fixture:
                source = Path(fixture)
                binding, expected, sequence = json.loads((source / 'meta').read_text())
                source = source / 'store'
                expected_wire = json.loads((source.parent / 'wire.json').read_text())
                limit_codex_input(self, 330000)
            else:
                self.oversized(count=6, characters=65000, limit=330000)
                with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', V2_FIRST):
                    first = self.assign('entry')
                    self.assertEqual(first[legacy.KEY]['protocol'], legacy.PROTOCOL)
                    self.assertNotIn('output_refusal', self.invoke(first))
                    binding = self.contaminate(first)
                source, expected, sequence = self.case.root, self.case.expected, self.case.sequence
                expected_wire = None
            control = None
            for order in (V2_FIRST, V3_FIRST):
                with self.subTest(order=order[0]), self._copy_store(source, expected, sequence), \
                        patch.object(legacy, 'NEW_SERIES_PROTOCOLS', order):
                    first = self.case.read_records()['review_assignment']['entry']
                    child = self._take_repair_handoff(first, binding)
                    self.assertEqual(child[legacy.KEY]['protocol'], legacy.PROTOCOL)
                    wire = [self.packet(child), child['prompt'], self.request(child)]
                    if expected_wire is not None:
                        self.assertEqual(wire, expected_wire)
                    if control is None:
                        control = wire
                    else:
                        self.assertEqual(wire, control)
                    self.assertNotIn('output_refusal', self._run_step(self.plan(child), child)[0])

    def test_refused_v3_read_uses_bounded_notice_within_the_frozen_level(self):
        priors = [dict(row, task_level=240000) if row['kind'] == 'read' else row for row in profile_priors()]
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            head = self._start_case('first-read', False, 'a')
            self.assertEqual(head[legacy.KEY]['protocol'], parts.PROTOCOL)
            plan = capacity.read_chain_capacity_plan(self.case.artifacts, head)
            step = self.plan(head)
            with patch.object(protocol.transport, 'send_request',
                              side_effect=lambda route, request, credential: build_codex_response(
                                  route, request, dict(invalid='z' * 320000))):
                refused = self.case.mutate(protocol.invoke_assignment, step['payload'], credential='fixture')
            self.assertIn('output_refusal', refused)
            replacement = self.take(self.plan(head))
            packet = self.packet(replacement)
            self.assertEqual(packet['output_refusal']['form'], 'bounded')
            self.assertEqual(replacement['capacity_plan'], head['capacity_plan'])
            level = packet[legacy.KEY]['capacity']['level_material_allowance']
            frozen = next(row['task_characters'] for row in plan['task']['read']['levels']
                          if row['material_allowance'] == level)
            report = preflight.build_assignment_report(self.case.read_records(), self.case.artifacts, replacement)
            self.assertEqual(report['plan_comparison']['sizes']['text_characters']['bound'], frozen)
            self.assertLessEqual(report['request_measure']['text_characters'], frozen)
            self.assertNotIn('output_refusal', self._run_step(self.plan(replacement), replacement)[0])

    def _answer_from_request(self, route, request, credential, *, outcome=None):
        packet = read_sent_packet(request)
        header = packet.get(legacy.KEY, {})
        if outcome == 'refusal' and header:
            output = {}
        elif outcome in ('cap', 'timeout'):
            output = {}
        elif header and header['kind'] != 'final':
            output = make_turn_output(packet) if header['protocol'] == parts.PROTOCOL else make_output(packet)
            if header['protocol'] == parts.PROTOCOL and header['kind'] == 'read':
                material = packet[parts.MATERIAL_KEY]
                bar_ids = [json.loads(text) for row, text in zip(material, material[1:])
                           if isinstance(row, dict) and isinstance(text, str)
                           and any(ref['field'] == 'occurrences' and ref['row']['path'] == ['bar_ids']
                                   for ref in row['references']['rows'])]
                if bar_ids:
                    output['cards'][0]['summary'] = 'Bar IDs: ' + json.dumps(bar_ids)
        else:
            if header.get('protocol') == parts.PROTOCOL:
                cards = packet[legacy.STATE_KEY]['cards']
                evidence = dict(ref=cards[0]['group']['first']['unit'])
            elif header:
                evidence = next(cite for unit in header['units'] for cite in unit.get('citations', [])
                                if cite.get('record_kind') == 'literature_search')
            else:
                search = packet['problem_evidence']['openness_searches'][0]['search']
                evidence = dict(kind='record', record_kind='literature_search', id=search['id'], digest=digest(search))
            output = problem_fixtures.ProblemReviewTests.build_review_output(SimpleNamespace(supporting=evidence), 'result')
            if header.get('protocol') == parts.PROTOCOL:
                bar_ids = [identifier for card in cards if card['summary'].startswith('Bar IDs: ')
                           for identifier in json.loads(card['summary'][9:])]
                output['value']['bar_ids'] = list(dict.fromkeys(bar_ids))
            elif packet.get('bar_ids'):
                output['value']['bar_ids'] = packet['bar_ids']
            if outcome == 'refusal':
                output['value']['status'] = 'invalid'
            if 'context_repair' in packet:
                output['reassessment'] = dict(assignment_id=packet['context_repair']['assignment_id'], findings=[])
        response = build_codex_response(route, request, output)
        if outcome == 'cap':
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            response['incomplete_details'] = dict(reason='max_output_tokens')
        if outcome == 'timeout':
            response['output'][0]['content'][0]['text'] = ''
            response['status'] = 'failed'
            response['execution'] = dict(timeout=True)
            response = bind_codex_response(route, request, response)
        return response

    def _run_step(self, step, assignment, outcome=None):
        self.assertEqual(step['command'], 'review-run')
        records = self.case.read_records()
        artifacts = self.case.artifacts
        route = records['review_route'][assignment['route_id']]
        request = protocol.build_assignment_request(records, artifacts, assignment)
        packet = self.packet(assignment)
        report = preflight.build_assignment_report(records, artifacts, assignment, request=request)
        preflight.check_request(report, request, packet=packet, artifacts=artifacts)
        measured = report.get('request_measure') or capacity.measure_request(
            route, request, records=records, artifacts=artifacts)
        self.assertTrue(measured['fits'])
        if assignment.get(legacy.KEY, {}).get('protocol') == parts.PROTOCOL:
            plan = capacity.read_chain_capacity_plan(artifacts, assignment)
            header = packet[legacy.KEY]
            task = plan['task'][header['kind']]
            level = header['capacity'].get('level_material_allowance', header['capacity']['material_allowance'])
            text_bound = next(row['task_characters'] for row in task['levels'] if row['material_allowance'] == level)
            rule = plan['measure']
            tokens = (math.ceil(measured['text_characters'] / rule['characters_per_token'])
                      if rule['rule'] == 'calibrated' and measured['non_ascii_share'] <= rule['non_ascii_limit']
                      else measured['utf8_bytes'])
            self.assertLessEqual(tokens, task['request_bound']['tokens_upper'])
            self.assertLessEqual(measured['text_characters'], text_bound)
        else:
            self.assertLessEqual(measured['tokens_upper'], measured['token_limit'])
        self.assertLessEqual(measured['characters'], report['input_size']['limit'])
        with patch.object(protocol.transport, 'send_request') as sender:
            sender.side_effect = lambda *args: self._answer_from_request(*args, outcome=outcome)
            result = self.case.mutate(protocol.invoke_assignment, step['payload'], credential='fixture')
            self.assertEqual(sender.call_args.args[1], request)
        attempt = self.case.read_records()['review_attempt'][step['payload']['id']]
        self.assertEqual(protocol._read_json(artifacts, attempt['request']), request)
        return result, digest([packet, assignment['prompt'], request])

    def _start_case(self, entry, stored, alphabet):
        chars = 33000 if alphabet != 'a' else 3000
        self.case.expected['openness_searches'] = []
        for index in range(2):
            search = dict(id='s%d' % index, queries=['bounded sequence'], reason=alphabet * chars)
            self.case.put('literature_search', search['id'], search)
            self.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        if entry == 'context-repair':
            with patch.object(review_single_request, 'describe_task_entry', return_value=None):
                predecessor = self.assign('repair-parent')
            binding = self.contaminate(predecessor)
            with patch.object(review_single_request, 'describe_task_entry', return_value='task'):
                return self.repair_assignment(predecessor, binding)
        if entry == 'single' or entry == 'refusal' and not stored:
            with patch.object(review_single_request, 'describe_task_entry', return_value=None):
                return self.assign('entry')
        with patch.object(review_single_request, 'describe_task_entry', return_value='task'):
            return self.assign('entry')

    def _drive_case(self, entry, stored, alphabet, order):
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,) if stored else order):
            assignment = self._start_case(entry, stored, alphabet)
        if stored:
            self.assertEqual(assignment[legacy.KEY]['protocol'], legacy.PROTOCOL)
        elif entry in ('single', 'refusal'):
            self.assertNotIn(legacy.KEY, assignment)
        else:
            self.assertEqual(assignment[legacy.KEY]['protocol'], order[0])
        events = {'single': ['cap'], 'capacity': ['cap'], 'timeout': ['timeout', 'timeout'],
                  'refusal': ['refusal']}.get(entry, [])
        trace, plans = [], set()
        if entry == 'refusal' and not stored:
            setup = self.plan(assignment)
            _, wire = self._run_step(setup, assignment, 'refusal')
            trace.append(wire)
            records, artifacts = self.case.read_records(), self.case.artifacts
            attempt, bad = protocol.read_assignment_output(records, artifacts, assignment['id'])
            with self.assertRaises(ResearchError) as rejected:
                self.case.mutate(decisions.record_value_review, dict(bad, id='seed-refusal',
                    assignment_id=assignment['id'], dossier_id=None))
            self.assertEqual(rejected.exception.code, 'invalid_value_review')
            certificate = self.case.read_records()['review_output_refusal'][attempt['id']]
            self.assertEqual(certificate['code'], 'invalid_value_review')
            self.assertEqual(certificate['assignment_id'], assignment['id'])
            events = ['cap']
        replaced = False
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', order):
            for _ in range(24):
                step = self.plan(assignment)
                key = digest([step.get('state'), step.get('command'), step.get('payload'), step.get('code')])
                self.assertNotIn(key, plans, step.get('code'))
                plans.add(key)
                state, command = step['state'], step.get('command')
                if command == 'review-run' and state == 'run':
                    event = events.pop(0) if events else None
                    result, wire = self._run_step(step, assignment, event)
                    if stored:
                        self.assertEqual(assignment[legacy.KEY]['protocol'], legacy.PROTOCOL)
                    trace.append(wire)
                    if event == 'refusal' and legacy.KEY in assignment:
                        self.assertIn('output_refusal', result)
                    elif event is None:
                        self.assertNotIn('output_refusal', result)
                elif command == 'review-assignment' and state in ('replace', 'continue'):
                    replaced = replaced or state == 'replace'
                    assignment = self.take(step)
                elif command == 'value-review' and state == 'record':
                    self.assertFalse(events, 'The intended failures were never delivered.')
                    self.take(step)
                    self.assertEqual(self.plan(assignment)['state'], 'done')
                    if entry in ('single', 'refusal'):
                        self.assertTrue(replaced)
                    return trace
                elif state in ('stop', 'handoff'):
                    self.assertIn(step['code'], stop_classes.STOP_REGISTRY)
                    self.assertIn(entry, ('capacity', 'timeout'))
                    self.assertFalse(events, 'The intended failures were never delivered.')
                    self._take_capacity_decision(step)
                    return trace
                else:
                    self.fail('Unexpected entry step %s / %s (%s)' % (state, command, step.get('code')))
        self.fail('The series did not finish')

    def _take_capacity_decision(self, plan):
        branch = next(row for row in plan['details']['decision']['then'] if row['answer'] == 'Move to another route')
        step = branch['steps'][0]
        self.assertEqual(step['command'], 'review-context')
        payload = dict(step['payload'], id='accepted-capacity-step', reason='Fixture owner selects a route change.',
                       evidence=[self.case.supporting])
        self.assertEqual(set(payload) - set(step['payload']), set(step['chooses']))
        result = self.case.mutate(protocol.record_context_event, payload)['result']
        self.assertEqual(result['id'], payload['id'])
        self.assertEqual(result['assignment_id'], plan['head_id'])

    def test_entry_property_reaches_a_record_or_registered_stop(self):
        """Clone one store for each order, entry, head and input alphabet."""
        self._drive_entry_matrix()

    def _drive_entry_matrix(self, *, replay_only=False):
        case = self.case
        original = case.root, case.store, case.artifacts, case.sequence, copy.deepcopy(case.expected)
        describe = capacity.describe_route_capacity
        controls = {}

        def byte_limit(*args, **kwargs):
            manifest = describe(*args, **kwargs)
            manifest['input_tokens']['value'] = 240000
            return manifest

        entries = ('first-read', 'single', 'context-repair', 'refusal', 'capacity', 'timeout')
        for entry, stored, alphabet, order in itertools.product(entries, (False, True), ('a', '界'), (V2_FIRST, V3_FIRST)):
            # A single is charged before it has a stored parts head. A stored
            # first read of that single can be replaced by the capacity/timeout cases.
            if (entry == 'single' and stored) or (replay_only and (not stored or order != V2_FIRST)):
                continue
            with self.subTest(entry=entry, stored=stored, alphabet=alphabet, order=order[0]), \
                    tempfile.TemporaryDirectory() as directory, \
                    patch.object(capacity, 'read_profile_priors', return_value=profile_priors()), \
                    patch.object(capacity, 'describe_route_capacity', side_effect=byte_limit):
                try:
                    case.root = Path(directory) / 'study'
                    shutil.copytree(original[0], case.root)
                    case.store, case.artifacts = Store(case.root), ArtifactStore(case.root)
                    case.sequence, case.expected = original[3], copy.deepcopy(original[4])
                    wire = self._drive_case(entry, stored, alphabet, order)
                    if stored:
                        identity = entry, alphabet
                        if order == V2_FIRST:
                            controls[identity] = wire
                        elif identity in controls:
                            self.assertEqual(wire, controls[identity])
                finally:
                    case.root, case.store, case.artifacts, case.sequence, case.expected = original
        return controls


class NaturalEntryTests(unittest.TestCase):
    def _start(self, tokens, identifier):
        from development_fixtures import DevelopmentCase
        import problem_parts_fixtures

        case = StoredEntryTests()
        self.addCleanup(case.doCleanups)

        def record_route(fixture, *args, **kwargs):
            fixture.capacity_probe_tokens = 128000 + tokens
            return record_codex_route(fixture, *args, **dict(kwargs, model='gpt-6-astra'))

        with patch.object(problem_parts_fixtures, 'record_codex_route', side_effect=record_route), \
                patch.object(DevelopmentCase, 'capacity_probe_tokens', 128000 + tokens, create=True):
            PartsResultCase.setUp(case)
        profile = [dict(row, task_level=120000) if row['kind'] == 'single' else row for row in profile_priors()]
        for patcher in (patch.object(capacity, 'read_profile_priors', return_value=profile),
                        patch.object(legacy, 'NEW_SERIES_PROTOCOLS', V3_FIRST)):
            patcher.start()
            self.addCleanup(patcher.stop)
        for index, (letter, size) in enumerate((('A', 65000), ('🧪', 60000))):
            search = dict(id='s%d' % index, queries=['finite'], reason=letter * size)
            case.case.put('literature_search', search['id'], search)
            case.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        assignment = case.assign(identifier)
        self.assertEqual(assignment[legacy.KEY]['protocol'], parts.PROTOCOL)
        return case, assignment

    def test_projected_problem_identity_is_read_from_the_authenticated_logical_packet(self):
        case, head = self._start(97000, 'entry')
        records = case.case.read_records()
        packet = case.packet(head)
        for name in ('area', 'field'):
            self.assertIsInstance(packet[name], dict)
            self.assertNotEqual(packet[name], records['workspace']['problem'][name])
        self.assertNotIn('output_refusal', case._run_step(case.plan(head), head)[0])
        step = case.plan(head)
        self.assertEqual((step['state'], step['payload']['id']), ('continue', 'entry-t2'))
        child = case.take(step)
        self.assertEqual(child['context']['prior_assignment_id'], head['id'])
        self.assertNotIn('output_refusal', case._run_step(case.plan(child), child)[0])
        records = copy.deepcopy(case.case.read_records())
        records['workspace']['problem']['field'] = 'A different field.'
        with self.assertRaises(ResearchError) as mismatch:
            protocol._source_parent(records, case.case.artifacts, child['id'],
                                    child['dossier_id'], child['reviewer_id'], child['role'])
        self.assertEqual(mismatch.exception.code, 'review_bar_mismatch')

    def test_unfitting_later_byte_interval_offers_the_capacity_step(self):
        case, head = self._start(105000, '界' * 5000)
        requests, seen = [], set()
        for _ in range(16):
            step = case.plan(head)
            signature = digest([step.get('state'), step.get('command'), step.get('payload'), step.get('code')])
            self.assertNotIn(signature, seen)
            seen.add(signature)
            if step['state'] == 'run':
                result, wire = case._run_step(step, head)
                self.assertNotIn('output_refusal', result)
                requests.append(wire)
            elif step['state'] == 'continue':
                head = case.take(step)
            else:
                self.assertGreaterEqual(len(requests), 2)
                self.assertEqual((step['state'], step['code']), ('stop', 'review_input_too_large'))
                sizes = step['details']['sizes']
                self.assertTrue(sizes['text_characters']['fits'])
                self.assertGreater(sizes['tokens_upper']['value'], sizes['tokens_upper']['bound'])
                case._take_capacity_decision(step)
                return
        self.fail('No capacity decision appeared before the later byte-heavy interval.')

    def test_refusal_notices_over_the_frozen_level_offer_the_route_decision(self):
        """One head, two refused answers: a notice that fits is replaced, and one that cannot gives the decision."""
        case, head = self._start(97000, 'entry')
        step = case.plan(head)
        source = case.case.root, case.case.expected, case.case.sequence
        for invalid, fits in (('z' * 150000, True), ('\U0001f9ea' * 50000, False)):
            with self.subTest(fits=fits), case._copy_store(*source):
                with patch.object(protocol.transport, 'send_request', side_effect=lambda route, request, credential:
                                  build_codex_response(route, request, dict(invalid=invalid))):
                    refused = case.case.mutate(protocol.invoke_assignment, step['payload'], credential='fixture')
                self.assertIn('output_refusal', refused)
                plan = case.plan(head)
                if fits:
                    self.assertEqual(plan['state'], 'replace', plan.get('code'))
                    replacement = case.take(plan)
                    self.assertEqual(case.packet(replacement)['output_refusal']['form'], 'bounded')
                    self.assertNotIn('output_refusal', case._run_step(case.plan(replacement), replacement)[0])
                    continue
                self.assertEqual((plan['state'], plan['code']), ('stop', 'review_input_too_large'))
                self.assertEqual(plan['details']['refusal'], 'review_route_capacity_plan_violated')
                emitted = []
                with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('No call is planned.')):
                    run_series(case.case.store, head['id'], emit=emitted.append)
                driven = emitted[-1]
                self.assertEqual((driven['state'], driven['code']), ('stop', 'review_input_too_large'))
                case._take_capacity_decision(driven)
                after = case.plan(head)
                self.assertEqual((after['state'], after['code'], after['details']['step']['command']),
                                 ('handoff', 'review_route_retired', 'review-assignment'))

    def _contaminate(self, case, head, room):
        case.case.capacity_probe_tokens = 128000 + room

        def record_route(fixture, *args, **kwargs):
            return record_codex_route(fixture, *args, **dict(kwargs, model='fixture-repair'))

        with patch.object(repair_fixtures, 'record_codex_route', side_effect=record_route):
            return case.contaminate(head)

    def test_repair_of_a_v3_head_is_offered_only_on_a_serving_route(self):
        """Each side of the admission boundary of the repair that a recorded v3 series keeps, under both orders."""
        case, head = self._start(97000, 'entry')
        self.assertNotIn('output_refusal', case._run_step(case.plan(head), head)[0])
        source = case.case.root, case.case.expected, case.case.sequence
        for room, local_limit, served in ((65000, 150000, False), (200000, 150000, True), (700000, None, True)):
            for order in ((legacy.PROTOCOL,), V3_FIRST):
                with self.subTest(room=room, order=order[0]), case._copy_store(*source), \
                        patch.object(legacy, 'NEW_SERIES_PROTOCOLS', order), \
                        patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS',
                                     local_limit or protocol.transport.CODEX_INPUT_LIMIT_CHARS):
                    binding = self._contaminate(case, head, room)
                    if not served:
                        plan = case.plan(head)
                        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_input_too_large'))
                        self.assertEqual(plan['details']['unserved_route_ids'], ['repair-route'])
                        records = case.case.read_records()
                        payload = review_plan_steps.describe_step(records, case.case.artifacts, head, 1)['payload']
                        payload.update(id='refused-repair', reviewer_id='replacement', route_id='repair-route')
                        payload['context']['context_repair'].update(
                            {name: binding[name] for name in ('probe_id', 'reason')})
                        with self.assertRaises(ResearchError) as refused:
                            case.case.mutate(protocol.record_assignment, payload)
                        self.assertEqual(refused.exception.code, 'review_route_capacity_below_floor')
                        self.assertTrue(refused.exception.details)
                        answer = next(row for row in plan['details']['decision']['then']
                                      if row['answer'] == 'Move to another route')
                        route_step = answer['steps'][0]
                        self.assertEqual(route_step['command'], 'review-route')
                        case.case.capacity_probe_tokens = 128000 + 200000
                        case.case.mutate(cli.OPERATIONS[route_step['command']],
                                         dict(route_step['payload'], id='larger-route', model='fixture-larger'))
                        probe_codex_route(case.case, 'probe-larger-route', 'larger-route')
                        binding = dict(binding, probe_id='probe-larger-route')
                    plan = case.plan(head)
                    self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_context_contaminated'))
                    payload = copy.deepcopy(plan['details']['step']['payload'])
                    payload.update(id='fixed-repair', reviewer_id='replacement',
                                   route_id='repair-route' if served else 'larger-route')
                    payload['context']['context_repair'].update({name: binding[name] for name in ('probe_id', 'reason')})
                    child = case.take(dict(command=plan['details']['step']['command'], payload=payload))
                    self.assertEqual((child[legacy.KEY]['protocol'], child[legacy.KEY]['adapter']),
                                     (parts.PROTOCOL, 'context_repair'))
                    self.assertNotIn('output_refusal', case._run_step(case.plan(child), child)[0])

    def test_unserved_repair_stop_names_only_steps_that_the_store_takes(self):
        """The stop of a v3 repair that no verified route serves holds no refused step; each answer step is taken."""
        case, head = self._start(97000, 'entry')
        self.assertNotIn('output_refusal', case._run_step(case.plan(head), head)[0])
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)), \
                patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 150000):
            binding = self._contaminate(case, head, 65000)
            plan = case.plan(head)
            self.assertEqual((plan['state'], plan['code']), ('stop', 'review_input_too_large'))
            self.assertNotIn('command', plan)
            self.assertNotIn('step', plan['details'])
            self.assertNotIn('steps', plan['details'])
            self.assertEqual(plan['details']['recovery']['then'], plan['details']['decision']['then'])
            answer, = plan['details']['decision']['then']
            self.assertEqual([step['command'] for step in answer['steps']], ['review-route', 'review-assignment'])
            # Repairs A16: the floor of this stop is the input limit of the routes, so no class condition stands.
            self.assertNotIn('condition', answer)
            route_step, assignment_step = answer['steps']
            case.case.capacity_probe_tokens = 128000 + 200000
            case.case.mutate(cli.OPERATIONS[route_step['command']],
                             dict(route_step['payload'], id='larger-route', model='fixture-larger'))
            probe_codex_route(case.case, 'probe-larger-route', 'larger-route')
            payload = copy.deepcopy(assignment_step['payload'])
            payload.update(id='served-repair', reviewer_id='replacement', route_id='larger-route')
            payload['context']['context_repair'].update(probe_id='probe-larger-route', reason=binding['reason'])
            child = case.take(dict(command=assignment_step['command'], payload=payload))
            self.assertEqual(child['route_id'], 'larger-route')

    def _exhaust(self):
        """A v3 head whose answers are refused until its slot is exhausted; its plan names step 2."""
        case, head = self._start(97000, 'entry')
        for _ in range(8):
            step = case.plan(head)
            if step['state'] == 'run':
                with patch.object(protocol.transport, 'send_request', side_effect=lambda route, request, credential:
                                  build_codex_response(route, request, dict(invalid='z' * 10))):
                    case.case.mutate(protocol.invoke_assignment, step['payload'], credential='fixture')
            elif step['state'] == 'replace':
                head = case.take(step)
            else:
                break
        self.assertEqual((step['state'], step['code'], step['details']['step']['id']),
                         ('handoff', 'review_slot_exhausted', 2))
        return case, head

    def _take_exhaustion_repair(self, case, step, identifier, route_id, probe_id):
        payload = copy.deepcopy(step['payload'])
        payload.update(id=identifier, route_id=route_id)
        payload['context']['context_repair'].update(probe_id=probe_id, reason='Reassess the exhausted slot.')
        return case.take(dict(command=step['command'], payload=payload))

    def test_exhaustion_repair_of_a_v3_head_is_offered_only_on_a_serving_route(self):
        """Repairs A6. After the slot of a v3 head is exhausted, its repair (step 2) is named only when a verified route
        serves it; otherwise the stop names a new route and then that repair, and the store takes each step."""
        case, head = self._exhaust()
        source = case.case.root, case.case.expected, case.case.sequence
        for room, served in ((65000, False), (700000, True)):
            with self.subTest(room=room), case._copy_store(*source), \
                    patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)), \
                    patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 150000):
                case.case.capacity_probe_tokens = 128000 + room
                record_codex_route(case.case, 'repair-route', model='fixture-repair', output_tokens=128000)
                plan = case.plan(head)
                route_id, probe_id = 'repair-route', 'probe-repair-route'
                if not served:
                    self.assertEqual((plan['state'], plan['code']), ('stop', 'review_input_too_large'))
                    self.assertNotIn('step', plan['details'])
                    self.assertEqual(plan['details']['unserved_route_ids'], ['repair-route'])
                    answer, = plan['details']['decision']['then']
                    self.assertEqual([(row['id'], row['command']) for row in answer['steps']],
                                     [(7, 'review-route'), (2, 'review-assignment')])
                    route_step, step = answer['steps']
                    case.case.capacity_probe_tokens = 128000 + 200000
                    case.case.mutate(cli.OPERATIONS[route_step['command']],
                                     dict(route_step['payload'], id='larger-route', model='fixture-larger'))
                    probe_codex_route(case.case, 'probe-larger-route', 'larger-route')
                    route_id, probe_id = 'larger-route', 'probe-larger-route'
                else:
                    self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_slot_exhausted'))
                    step = plan['details']['step']
                payload = copy.deepcopy(step['payload'])
                payload.update(id='exhaustion-repair', route_id=route_id)
                payload['context']['context_repair'].update(probe_id=probe_id, reason='Reassess the exhausted slot.')
                self.assertEqual(case.take(dict(command=step['command'], payload=payload))['route_id'], route_id)

    def test_stale_exhaustion_repair_of_a_v3_head_is_named_only_on_a_serving_route(self):
        """Repairs A6b. A later event on an exhausted v3 head makes the binding of its repair stale; the plan of the
        repair names step 2 of the head again only when a verified route serves it, otherwise the stop names a new
        route and then that step, and the store takes each step."""
        case, head = self._exhaust()
        with patch.object(legacy, 'NEW_SERIES_PROTOCOLS', (legacy.PROTOCOL,)), \
                patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 150000):
            case.case.capacity_probe_tokens = 128000 + 700000
            record_codex_route(case.case, 'repair-route', model='fixture-repair', output_tokens=128000)
            repair = self._take_exhaustion_repair(case, case.plan(head)['details']['step'], 'exhaustion-repair',
                                                  'repair-route', 'probe-repair-route')
            case.case.mutate(protocol.record_context_event, dict(id='later-contamination', assignment_id=head['id'],
                kind='contamination', source='author_history', reason='A later actual exposure.',
                evidence=[case.case.supporting]))
            case.case.capacity_probe_tokens = 128000 + 65000
            record_codex_route(case.case, 'small-route', model='fixture-small', output_tokens=128000)
            plan = case.plan(repair)
            # The stop keeps the code of the plan that it replaces, which states the stale binding.
            self.assertEqual((plan['state'], plan['code']), ('stop', 'review_context_repair_stale'))
            self.assertNotIn('command', plan)
            self.assertNotIn('step', plan['details'])
            self.assertEqual(plan['details']['unserved_route_ids'], ['small-route'])
            answer, = plan['details']['decision']['then']
            self.assertEqual([(row['id'], row['command']) for row in answer['steps']],
                             [(7, 'review-route'), (2, 'review-assignment')])
            route_step, step = answer['steps']
            self.assertEqual(step['payload']['context']['context_repair']['exhaustion'],
                             repair['context']['context_repair']['exhaustion'])
            case.case.capacity_probe_tokens = 128000 + 200000
            case.case.mutate(cli.OPERATIONS[route_step['command']],
                             dict(route_step['payload'], id='larger-route', model='fixture-larger'))
            probe_codex_route(case.case, 'probe-larger-route', 'larger-route')
            rebound = self._take_exhaustion_repair(case, step, 'rebound-repair', 'larger-route', 'probe-larger-route')
            self.assertEqual(rebound['context']['context_repair']['event_ids'], ['later-contamination'])
