"""Command-driven capacity properties of math v2 and v7 finals in pages."""

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import codex_route_fixtures
import property_stub_b as stub
import reading_capacity_fixtures as shapes
import test_openness_final_pages as pages_fixtures
import test_openness_reading_math_v2 as math_fixtures
import test_research_problem_openness_minimum as minimum_fixtures
from test_review_output_profile import build_row
from research_harness import native_review, openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_composite, review_failure_class, review_protocol as protocol
from research_harness import review_route_capacity as capacity
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record
from research_harness.review_continuation import plan_next


class PropertyChain:
    """Follow planner actions. Only the observer can read assignments and plans."""

    def read_records(self):
        return self.store.snapshot()['records']

    def plan(self, head):
        action = plan_next(self.read_records(), self.artifacts, head)
        self.assertNotEqual(action.get('code'), 'review_route_capacity_plan_violated', action)
        return action

    def assign_next(self, action):
        self.assertEqual(action['command'], 'review-assignment', action)
        return self.mutate(protocol.record_assignment, action['payload'])['result']['id']

    def send_turn(self, head, *, fill=False, over_bound=False, capped=False, failed=False, dense=False,
                  steady=False, list_allowances=False, attempt_id=None):
        records = self.read_records()
        assignment = records['review_assignment'][head]
        plan = capacity.read_chain_capacity_plan(self.artifacts, assignment)
        attempt_id = attempt_id or 'attempt-' + head
        sent = []

        def respond(route, request, credential):
            message = request['input'][-1]['content']
            packet = stub.read_packet(message)
            stage, binding = stub.read_binding(packet)
            self.assertEqual(binding['protocol'], self.chain_protocol)
            kind = stage if stage in ('reading', 'combine') else 'final'
            check = capacity.describe_plan_preflight(records, route, request, plan, kind)
            self.assertTrue(check['plan_comparison']['fits'], check['plan_comparison'])
            for name in ('text_characters', 'tokens_upper'):
                row = check['plan_comparison']['sizes'][name]
                self.assertIsNotNone(row['bound'], row)
                self.assertLessEqual(row['value'], row['bound'], row)
            limits = capacity.derive_plan_task_limits(plan, kind if kind in plan['task'] else 'final', [
                dict(material_allowance=binding['capacity'].get('level_material_allowance',
                                                               binding['capacity']['material_allowance']))])
            self.assertLessEqual(binding['capacity']['material_allowance'], limits['material_allowance'])
            self.assertLessEqual(binding['capacity']['answer_bound'], limits['answer_bound'])
            self.assertEqual(binding['capacity']['output_tokens'], plan['limits']['output_tokens']['value'])
            if stage == 'reading':
                granted = binding['allowances']
                self.assertEqual(granted['notes'], plan['allocation']['notes'])
                target_limits = {row['handle']: row['excerpts'] for row in plan['allocation']['targets']}
                for row in granted['targets']:
                    self.assertEqual(row['characters'], target_limits[row['handle']])
                    self.assertEqual(row['remaining'], max(0, row['characters'] - row['used']))
            sent.append((packet, stage, binding))
            output = {} if capped or failed else stub.build_answer(
                request['instructions'], message, fill=fill, over_bound=over_bound, list_allowances=list_allowances)
            response = codex_route_fixtures.build_codex_response(route, request, output)
            if capped or failed:
                response['status'] = 'incomplete'
            if capped:
                response['usage']['output_tokens'] = binding['capacity']['output_tokens']
            if steady:
                response['usage']['output_tokens'] = binding['capacity']['output_tokens'] // 2
            if dense:
                response['usage']['input_tokens'] = math.ceil(
                    check['request_measure']['text_characters'] / plan['measure']['characters_per_token'] * 1.2)
            if capped or failed or steady or dense:
                return codex_route_fixtures.bind_codex_response(route, request, response)
            return response

        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            receipt = self.mutate(protocol.invoke_assignment, dict(id=attempt_id, assignment_id=head),
                                  credential='fixture')
        [turn] = sent
        records = self.read_records()
        attempt = records['review_attempt'][attempt_id]
        if not (capped or failed or 'output_refusal' in receipt):
            self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
        else:
            self.assertEqual(attempt['assignment_id'], head)
            self.assertTrue(self.artifacts.read(attempt['request']))
        return turn, attempt, receipt

    def reach_page(self, head):
        for _ in range(20):
            if reading.find_stage(self.read_packet(head)) == 'page':
                return head
            self.assertEqual(self.plan(head)['state'], 'run')
            turn, _, receipt = self.send_turn(head)
            self.assertEqual(turn[1], 'reading')
            self.assertIsNone(receipt.get('output_refusal'))
            action = self.plan(head)
            self.assertEqual(action['state'], 'continue', action)
            head = self.assign_next(action)
        self.fail('The fixture did not reach its first target page.')

    def exhaust_output_levels(self, head):
        stages = []
        attempt_id = None
        for _ in range(20):
            assignment = self.read_records()['review_assignment'][head]
            self.assertEqual(self.plan(head)['state'], 'run')
            (_, stage, binding), attempt, _ = self.send_turn(head, capped=True, attempt_id=attempt_id)
            attempt_id = None
            stages.append(stage)
            self.assertEqual(attempt['usage']['output_tokens'], binding['capacity']['output_tokens'])
            action = self.plan(head)
            if action['state'] == 'stop':
                self.assertEqual(action['code'], 'review_output_over_cap')
                self.assertIn('decision', action['details'], action)
                commands = {step.get('command') for answer in action['details']['decision']['then']
                            for step in answer['steps']}
                self.assertIn(self.floor_command, commands)
                plan = capacity.read_chain_capacity_plan(self.artifacts, assignment)
                kind = assignment['capacity_plan_kind']
                bounds = binding['capacity']
                self.assertEqual(bounds.get('level_material_allowance', bounds['material_allowance']),
                                 min(row['material_allowance'] for row in plan['task'][kind]['levels']))
                return stages, action
            if action['state'] == 'run':
                # A final at its floor allows one identical retry before the decision.
                self.assertNotEqual(stage, 'reading')
                self.assertEqual(action['details']['over_cap_attempts'], 1)
                self.assertEqual(action['payload']['assignment_id'], head)
                attempt_id = action['payload']['id']
                continue
            self.assertEqual(action['state'], 'replace', (action.get('code'), action.get('command')))
            head = self.assign_next(action)
            successor = self.read_records()['review_assignment'][head]
            self.assertEqual(successor['route_id'], assignment['route_id'])
            self.assertEqual(successor['capacity_plan'], assignment['capacity_plan'])
            if stage == 'page':
                previous = binding['page']['targets']
                after = self.read_packet(head)['reading_final']['page']['targets']
                self.assertLess(len(after), len(previous))
                self.assertEqual(after, previous[:len(after)])
            if stage == 'reading':
                plan = capacity.read_chain_capacity_plan(self.artifacts, assignment)
                levels = [row['material_allowance'] for row in plan['task']['reading']['levels']]
                previous = binding['capacity']['material_allowance']
                self.assertEqual(self.read_packet(head)['reading_turn']['capacity']['material_allowance'],
                                 max(level for level in levels if level < previous))
        self.fail('The capacity replacements did not reach the floor.')

    def exhaust_failures(self, head):
        class AfterCooldown(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(timezone.utc) + timedelta(days=1)

        request_digests = set()
        for index in range(30):
            action = self.plan(head)
            if action['state'] == 'wait':
                self.assertEqual(action['code'], 'review_sequence_wait')
                self.assertTrue(action['not_before'])
                with patch.object(review_failure_class, 'datetime', AfterCooldown):
                    action = self.plan(head)
            self.assertEqual(action['state'], 'run')
            _, attempt, _ = self.send_turn(head, failed=True, attempt_id='a%059d' % index)
            request_digests.add(attempt['request']['sha256'])
            action = self.plan(head)
            if action['state'] == 'stop':
                self.assertEqual(len(request_digests), 1)
                self.assertEqual(action['code'], 'review_turn_failures_exhausted', action)
                self.assertEqual(action['details']['decision']['then'][1]['steps'][0]['command'], 'review-context')
                self.assertEqual(index + 1, 9)  # Three sequences of three incomplete generations.
                return
        self.fail('The planner did not stop after 30 failed attempts.')

    def assert_late_delivery(self, first, final, sha256):
        inventory = json.loads(self.artifacts.read(first['reading_chain']['inventory']))
        assignment = self.read_records()['review_assignment'][final]
        state = json.loads(self.artifacts.read(assignment['reading_chain']['state']))
        [unit] = [row for row in inventory['units'] if row['sha256'] == sha256]
        if unit['identity']['record_kind'] == 'literature_search':
            self.assertIn(sha256, state['searches'])
            [search] = [row for row in inventory['search_delivery']['searches'] if row['search_digest'] == sha256]
            # Shared fields can be credited by a carried search. The new residual must be sent.
            new_digest = (search['residual'] or search['artifact'])['sha256']
        else:
            new_digest = sha256
        [body] = [row for row in inventory['material'] if row['sha256'] == new_digest]
        offset = 0
        for span in state['deliveries']:
            if span['unit'] == body['handle']:
                self.assertEqual(span['start'], offset)
                offset = span['end']
        self.assertEqual(offset, body['characters'], body['label'])
        self.assertEqual(assignment['capacity_plan'], first['capacity_plan'])

    def drive(self, head, *, fill=False, over_bound=False, steady=False, list_allowances=False):
        turns = []
        for _ in range(40):
            action = self.plan(head)
            self.assertEqual(action['state'], 'run', action)
            stage = reading.find_stage(self.read_packet(head))
            exceed = over_bound and not turns and stage == 'reading'
            turn, attempt, receipt = self.send_turn(head, fill=fill, over_bound=exceed, steady=steady,
                                                   list_allowances=list_allowances)
            self.assertIsNone(receipt.get('output_refusal'),
                              (turn[1], attempt.get('answer'), receipt.get('output_refusal')))
            if exceed:
                self.assertGreater(attempt['answer']['characters'], 2 * attempt['answer']['bound'], attempt['answer'])
                self.assertEqual(attempt['status'], 'completed')
                self.assertTrue(attempt['output'])
            if fill and turn[1] == 'reading':
                output = json.loads(self.artifacts.read(attempt['output']))
                self.assertEqual(len(output['notes']), turn[2]['allowances']['notes'])
            if fill and turn[1] in ('final', 'closing'):
                self.assertEqual(attempt['answer']['characters'], attempt['answer']['bound'])
            turns.append(turn)
            action = self.plan(head)
            if turn[1] in ('final', 'closing'):
                self.record_final(head, action)
                return head, turns
            self.assertEqual(action['state'], 'continue', action)
            head = self.assign_next(action)
        self.fail('The bounded chain did not reach its final.')

    def run_k6(self, assign, *, late_digest=None):
        kinds, record_kinds = shapes.read_inventory_kinds()
        self.assertLessEqual(kinds, set(shapes.DELIVERY_RULES))
        self.assertLessEqual(record_kinds, set(shapes.RECORD_DELIVERY_RULES))
        width = min(capacity.describe_material_levels('reading')) + 1
        types = [(kind, None) for kind in sorted(kinds - {'record', 'list_page', 'carried_reading'})]
        types += [('record', record_kind) for record_kind in sorted(record_kinds)]
        rows, bodies = [], {}
        for index, (kind, record_kind) in enumerate(types):
            shape = shapes.build_shape_inventory(self.artifacts, characters=width + index,
                                                 kind=kind, record_kind=record_kind)
            unit = shape['inventory']['units'][0]
            rows.append(dict(unit['identity'], artifact=unit['artifact'], label='Synthetic K6 %d' % index))
            bodies[unit['sha256']] = shape['bodies'][unit['sha256']]

        plan_inventory = v7.plan_inventory

        def extend_inventory(source, content, **options):
            # Supply additional logical fixture bodies to the real inventory planner and verifier.
            if late_digest is not None and late_digest not in content:
                return plan_inventory(source, content, **options)
            return plan_inventory(source + rows, dict(content, **bodies), **options)

        with patch.object(v7, 'plan_inventory', side_effect=extend_inventory):
            first = assign()
            plan = capacity.read_chain_capacity_plan(self.artifacts, first)
            self.assertLess(plan['task']['reading']['material_allowance'], width)
            _, turns = self.drive(first['id'], steady=True, list_allowances=True)
        delivered = {sha256: [] for sha256 in bodies}
        pages, carried = [], {}
        for packet, stage, binding in turns:
            if stage != 'reading':
                continue
            for row, content in stub.read_deliveries(packet, binding):
                if row['kind'] == 'list_page':
                    pages.append(json.loads(content))
                if row.get('sha256') in delivered:
                    delivered[row['sha256']].append((row['start'], row['end'], content))
                if row['kind'] == 'carried_reading':
                    carried.setdefault(row['sha256'], []).append((row['start'], row['end'], content))
        for sha256, spans in delivered.items():
            self.assertGreaterEqual(len(spans), 2, sha256)
            offset = 0
            for start, end, content in spans:
                self.assertEqual(start, offset)
                self.assertEqual(end - start, len(content))
                offset = end
            self.assertEqual(''.join(content for _, _, content in spans).encode(), bodies[sha256])
        if late_digest is not None:
            [spans] = carried.values()
            self.assertGreaterEqual(len(spans), 2)
            self.assertEqual(spans[0][0], 0)
            self.assertTrue(all(left[1] == right[0] for left, right in zip(spans, spans[1:])))
            material = ''.join(content for _, _, content in spans).encode()
            self.assertEqual(hashlib.sha256(material).hexdigest(), next(iter(carried)))
        return pages, plan


class MathChain(PropertyChain, math_fixtures.MathV2Support):
    chain_protocol = reading.MATH_PROTOCOL_V2
    capacity_probe_tokens = 487900
    floor_command = 'search review-packet'

    def setUp(self):
        super().setUp()
        forms = patch.object(v7, 'MATH_V2_FINAL_FORMS', ('request', 'pages'))
        forms.start()
        self.addCleanup(forms.stop)

    def record_final(self, head, action):
        records = self.read_records()
        assignment = records['review_assignment'][head]
        self.assertTrue(reading.verify_chain(records, self.artifacts, assignment))
        output, composite = review_composite.read_final_output(records, self.artifacts, assignment)
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
        native_review.validate_output(native, output)
        self.assertEqual(output['decision'], 'approve', action)
        finding = output['findings']['openness']
        self.assertEqual(finding['outcome'], 'supported_open')
        minimum = native['scientific_context']['openness_minimum']
        self.assertEqual(finding['minimum'], minimum)
        required = ({self.proof['sha256'], self.supplementary['sha256']}
                    | {row['search_digest'] for row in minimum['searches']}
                    | set(minimum['reading_digests']))
        self.assertLessEqual(required, set(finding['evidence']))
        if composite:
            self.assertEqual(output, review_composite.authenticate_composite(records, self.artifacts, composite))
        return output


class MathPropertyDrivers(MathChain):
    def test_math_late_units_and_denser_ratio(self):
        native = self.build_math_packet()
        initial = self.assign('math-1', native)
        plan = capacity.read_chain_capacity_plan(self.artifacts, initial)
        _, attempt, receipt = self.send_turn('math-1', dense=True)
        self.assertIsNone(receipt.get('output_refusal'))
        observed_ratio = capacity.measure_attempt_ratio(attempt)
        self.assertLessEqual(observed_ratio, plan['measure']['characters_per_token'] / 1.2)
        old_plan = self.artifacts.read(initial['capacity_plan']['artifact'])
        late = self.artifacts.put(b'A late supplementary proof note.', 'text/plain; charset=utf-8')
        packet = json.loads(self.artifacts.read(native))
        packet['reviewer']['attestation_id'] = 'review-assignment:successor'
        row = dict(kind='artifact', digest=late['sha256'], artifact=late)
        packet['evidence'].append(row)
        packet['scientific_context']['supplementary_inputs']['inputs'].append(row)
        successor = self.artifacts.put(serialize_record(packet), 'application/json')
        first = self.assign('successor', successor)
        self.assertTrue(first['carried_readings']['chains'])
        inherited = capacity.read_chain_capacity_plan(self.artifacts, first)['measure']
        self.assertEqual(inherited['characters_per_token'], observed_ratio)
        self.assertEqual(inherited['source'], dict(chain='math-1', attempt=attempt['id']))
        final, _ = self.drive('successor')
        self.assert_late_delivery(first, final, late['sha256'])
        self.assertEqual(self.artifacts.read(initial['capacity_plan']['artifact']), old_plan)
        self.assertEqual(self.read_records()['review_attempt'][attempt['id']], attempt)
        records = self.read_records()
        output, _ = review_composite.read_final_output(records, self.artifacts, records['review_assignment'][final])
        self.assertIn(late['sha256'], output['findings']['openness']['evidence'])

    def test_math_output_limit_floor(self):
        self.assign('math-1', self.build_math_packet())
        stages, _ = self.exhaust_output_levels('math-1')
        self.assertEqual(set(stages), {'reading'})

    def test_math_failed_attempts(self):
        head = 'm' * 60
        self.assign(head, self.build_math_packet(assignment_id=head))
        self.exhaust_failures(head)

    def test_math_over_bound(self):
        self.assign('math-1', self.build_math_packet())
        self.drive('math-1', over_bound=True)

    def test_math_allowances(self):
        native = self.build_math_packet()
        self.assign('math-1', native)
        self.drive('math-1', fill=True)

class V7PagesChain(PropertyChain, pages_fixtures.FinalPagesSupport):
    chain_protocol = v7.PROTOCOL
    floor_command = 'export'

    def setUp(self):
        super().setUp()
        protocols = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        protocols.start()
        self.addCleanup(protocols.stop)

    def record_final(self, head, action):
        self.assertEqual((action['state'], action['command']), ('record', 'problem-openness'), action)
        self.assert_recorded_composite(head)

    def assign_two_pages(self, head='turn-1', *, native=None, reading_fit=False):
        """Use two targets with an answer-room prior that admits one target per page."""
        native = native or self.export_targets(2, assignment_id=head)
        priors = [build_row(level=None, kind='final', count=8, thinking=109000)]
        if reading_fit:
            priors.append(build_row(level=None, count=8, thinking=1000))
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            first = self.assign(head, native)
        plan = capacity.read_chain_capacity_plan(self.artifacts, first)
        self.assertEqual(plan['allocation']['final_form'], 'pages')
        self.assertEqual(plan['task']['final']['target_allowance'], 1)
        return first


class V7PagesPropertyDrivers(V7PagesChain):
    def test_v7_pages_late_units(self):
        """A late search needs a new export and a two-page final with the carried reading."""
        initial = self.assign_two_pages(reading_fit=True)
        _, _, receipt = self.send_turn('turn-1')
        self.assertIsNone(receipt.get('output_refusal'))
        late = minimum_fixtures.ProblemOpennessMinimumTests.search(
            self, self.versions, query='A second search arrives after the first turn.')
        self.searches.append(late)
        action = self.plan('turn-1')
        self.assertEqual((action['state'], action['command']), ('handoff', 'export'))
        successor = self.export(name='successor-export', assignment_id='successor')
        first = self.assign_two_pages('successor', native=successor)
        self.assertTrue(first['carried_readings']['chains'])
        final, _ = self.drive('successor')
        self.assert_late_delivery(first, final, digest(late))
        self.assertEqual(self.read_records()['review_assignment']['turn-1']['capacity_plan'], initial['capacity_plan'])

    def test_v7_pages_output_limit_floor(self):
        """A multi-target page must split down to a singleton before the floor retry."""
        self.assign('turn-1', self.export_targets())
        stages, _ = self.exhaust_output_levels(self.reach_page('turn-1'))
        self.assertEqual(set(stages), {'page'})

    def test_v7_pages_failed_attempts(self):
        """Exhaust all three failed-attempt sequences on a page after reading."""
        first = 'v' * 57
        self.assign_two_pages(first)
        head = self.reach_page(first)
        self.assertEqual(len(head), 60)
        self.exhaust_failures(head)

    def test_v7_pages_over_bound(self):
        """Record a complete over-bound reading answer, then authenticate the two-page final."""
        self.assign_two_pages()
        self.drive('turn-1', over_bound=True)

    def test_v7_pages_k6_three_allowances(self):
        """Each segmenting kind crosses a range boundary, then two target pages need a closing."""
        self.assign_two_pages(reading_fit=True)
        _, _, receipt = self.send_turn('turn-1', fill=True)
        self.assertIsNone(receipt.get('output_refusal'))
        late = minimum_fixtures.ProblemOpennessMinimumTests.search(
            self, self.versions, query='A distinct search changes the manifest used by the notes.')
        self.searches.append(late)
        native = self.export(name='successor-export', assignment_id='successor')
        pages, plan = self.run_k6(lambda: self.assign_two_pages('successor', native=native), late_digest=digest(late))
        [page] = pages
        self.assertEqual((page['list'], page['after'], page['next']), ('allowances', None, None))
        self.assertEqual([row['id'] for row in page['rows']], plan['targets']['handles'])
