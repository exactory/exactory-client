"""A v7 reading chain stops before its next assignment and its next call when it can give no finding (R3.4).

Rules C23 to C27: the records no longer give a unit of the chain's own packet. Source: record
20261120063649-v7-chain-takes-turns-after-its-own-content-changed, tests V1 to V8, K1 to K3, J1, M1 and M2 of its
design and its review. Rules C28 to C32: a turn that the chain carries has no verified context any more. Source: record
20261120092756-v7-chain-takes-calls-after-a-carried-turn-is-contaminated, tests W1 to W9 and K1 to K3 of its design.

In most classes the v7 chain 'new' carries turn-1 of an earlier v6 chain (CarriedIntegrationSupport). Each content
change is written by the functions of its commands. Every reviewer answer comes from a stub of the transport.
"""

import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, probe_codex_route, record_codex_route
from openness_reading_fixtures import OpennessReadingCase
import test_openness_carried_integration as integration
import test_openness_reading_v7_turns as turn_fixtures
from test_openness_reading_math_v2 import MathV2Support, MATH_V2
from test_openness_unread_final import UnreadFinalSupport
from research_harness import cli, openness_carried, openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_excerpts, review_failure_class as failures, review_protocol as protocol
from research_harness import review_route_capacity as capacity, review_transport as transport
from research_harness.capture_selection import select_main_capture
from research_harness.check_mode import check_mutation
from research_harness.errors import ResearchError
from research_harness.literature import import_bundle
from research_harness.problem_openness import assess_openness, assess_packet_freshness, export_openness, serialize_record
from research_harness.reading import record_reading
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series

OUTDATED = 'openness_packet_outdated'
CARRIED = 'openness_carried_turn_unverified'
EVENT_ID = 'carried-turn-exposure'


def make_stale(case, route_id):
    """The installed reviewer runtime differs from the one that this route record pins; other records keep theirs."""
    installed = transport.runtime

    def read_runtime(route=None):
        runtime = installed(route)
        stale = route is not None and route['id'] == route_id
        return dict(runtime, implementation_sha256='0' * 64) if stale else runtime
    patcher = patch.object(transport, 'runtime', side_effect=read_runtime)
    patcher.start()
    case.addCleanup(patcher.stop)


class ChainStopSupport(integration.ContentChangeSupport):
    """The v7 chain 'new' on chain_route carries turn-1 of the earlier chain, whose route record is 'route'."""
    chain_route = 'route'

    def setUp(self):
        super().setUp()
        if self.chain_route != 'route':
            record_codex_route(self, self.chain_route, model=self.route_model, output_tokens=self.route_output_tokens)

    def read_plan(self, head):
        return plan_next(self.store.snapshot()['records'], self.artifacts, head)

    def describe_plan(self, head):
        plan = self.read_plan(head)
        return plan['state'], plan.get('command'), plan.get('code')

    def build_payload(self, native, identifier, prior=None):
        context = dict(native_packet=native)
        if prior is not None:
            context['prior_assignment_id'] = prior
        return dict(id=identifier, route_id=self.chain_route, dossier_id=None, role='native_math',
                    reviewer_id='openness-reviewer', author_id='problem-author', context=context)

    def start_carrying_chain(self, identifier='new'):
        """Record the first assignment of the v7 chain, which carries turn-1."""
        native = self.export(name=identifier + '-export', assignment_id=identifier)
        receipt = self.mutate(protocol.record_assignment, self.build_payload(native, identifier))['result']
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        self.assertEqual(integration.list_carried_turns(receipt['carried_readings']), {'turn-1': ['turn-1']})
        return native

    def start_observed_chain(self):
        """A v7 chain whose first turn has an observed reading output, so its next step is its next assignment."""
        native = self.start_carrying_chain()
        self.invoke('new', self.answer_v7(self.read_packet('new')))
        return native

    def count_assignments(self):
        return len(self.store.snapshot()['records']['review_assignment'])

    def refuse_assignment(self, payload, code):
        """review-assignment is refused with code and records nothing."""
        before = self.count_assignments()
        with self.assertRaises(ResearchError) as caught:
            self.mutate(protocol.record_assignment, payload)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.count_assignments(), before)
        return caught.exception

    def refuse_call(self, identifier, code):
        """review-run of a recorded assignment is refused with code and sends no request."""
        sent = []

        def respond(route, request, credential):
            sent.append(request)
            return build_codex_response(route, request, self.answer_v7(self.read_packet(identifier)))
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            with self.assertRaises(ResearchError) as caught:
                protocol.invoke_assignment(self.store, dict(id='attempt-' + identifier, assignment_id=identifier),
                    credential='test-only', expected_revision=self.store.revision, request_id='invoke-' + identifier)
        self.assertEqual((caught.exception.code, sent), (code, []))
        return caught.exception

    def contaminate(self, assignment_id='turn-1'):
        """review-context: a contamination event on a turn."""
        exposure = self.artifacts.put(b'Synthetic exposure of a turn.', 'text/plain')
        self.mutate(protocol.record_context_event, dict(id=EVENT_ID, assignment_id=assignment_id, kind='contamination',
            source='author_history', reason='The author saw the answer of the turn.', evidence=[exposure]))

    def fail_probe(self, route_id='route', probe_id='failed-probe', *, answered=False):
        """review-probe that does not verify the route record: the provider fails, so the probe has no answer, or,
        with answered, the reviewer answers with the wrong controls."""
        def respond(route, request, credential):
            if not answered:
                raise ResearchError('review_provider_failure', 'A provider error.')
            return build_codex_response(route, request, dict(allowed_control='wrong', evidence_control='wrong',
                                                             excluded_controls=[]))
        with patch.object(transport, 'send_request', side_effect=respond):
            protocol.probe_route(self.store, dict(id=probe_id, route_id=route_id), credential='test-only',
                                 expected_revision=self.store.revision, request_id=probe_id)
        self.assertEqual(protocol.route_state(self.store.snapshot()['records'], self.artifacts, route_id)
                         ['context_status'], 'unverified')

    def answer_final(self, packet):
        binding = packet['reading_final']
        return dict(schema_version=3, **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
            findings=dict({key: 'Assessed the supplied subject.' for key in
                           ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                openness=dict(outcome='unresolved', coverage='Reviewed the required units.', reason='A remaining gap.',
                    grounds=[dict(kind='own_argument' if 'finding_index' in packet else 'reviewer_gap',
                                  text='The reviewer argument requires further checking.' if 'finding_index' in packet
                                  else 'Further work is needed.')])))

    def drive(self, head, *, after_first_call=None):
        """Run review-series with a stub transport. after_first_call runs once, after the line of the first reviewer
        call. Return the exit code, the lines, and one flag for each reviewer call: whether it came after that."""
        calls, lines, written = [], [], []

        def respond(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            calls.append(bool(written))
            return build_codex_response(route, request, self.answer_v7(packet) if 'reading_turn' in packet
                                        else self.answer_final(packet))

        def emit(row):
            lines.append(row)
            if (after_first_call is not None and not written and calls and row.get('command') == 'review-run'
                    and 'error' not in row):
                after_first_call()
                written.append(True)
        with patch.object(protocol.transport, 'send_request', side_effect=respond), \
                patch('research_harness.review_series.signal'):
            code = run_series(self.store, head, emit=emit)
        return code, lines, calls

    def take_export(self, step, identifier):
        """Take the export and both assignment actions of the plan using an accepted synthetic minimum."""
        minimum = dict(searches=[dict(formulation=self.selected['statement'], search_id=search['id'])
                                 for search in self.searches], settling_work_id=None, duplicate_resolutions=[])
        self.assertEqual(step['payload']['kind'], 'problem-openness')
        native = export_openness(self.store, dict(reviewer_id=step['payload']['reviewer_id'],
                                                assignment_id=identifier, minimum=minimum),
                                 self.root / (identifier + '-export'), accept_gaps=True)['native_packet']
        checked, recorded = [dict(row['payload'], id=identifier, context=dict(native_packet=native))
                             for row in step['then']]
        check_mutation(self.store, protocol.record_assignment, checked, expected_revision=self.store.revision,
                       request_id='check-' + identifier, bounded_payload=checked)
        return self.mutate(protocol.record_assignment, recorded)['result']

    def read_head(self, packets, first='new'):
        return first if len(packets) == 1 else '%s-%d' % (first, len(packets))

    def assert_units(self, details, version):
        """details of the stop of rule C26: the full text of version left, with the main captures of today, and the
        step of a new packet on the route record of the chain."""
        self.assertEqual(details['message'], v7.UNITS_CHANGED_MESSAGE)
        self.assertEqual([(row['state'], row['unit']['kind'], row['unit']['version_id']) for row in details['units']],
                         [('left', 'fulltext', version)])
        captures = details['units'][0]['main_captures']
        self.assertTrue(captures)
        self.assertNotIn(details['units'][0]['unit']['text_sha256'], [row['text_sha256'] for row in captures])
        self.assert_export_step(details['step'])

    def assert_export_step(self, step):
        """The step of a new packet under the same reviewer, assigned on the route record of the chain, first in check
        mode."""
        self.assertEqual((step['command'], step['payload']['reviewer_id']), ('export', 'openness-reviewer'))
        self.assertEqual([(row['command'], row.get('check', False), row['payload']['route_id'])
                          for row in step['then']],
                         [('review-assignment', True, self.chain_route),
                          ('review-assignment', False, self.chain_route)])

    def assert_units_handoff(self, plan, version):
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', OUTDATED), plan)
        self.assert_units(plan['details'], version)

    def assert_contaminated_turn(self, details, head):
        self.assertEqual((details['assignment_id'], details['message']), (head, v7.CARRIED_TURN_MESSAGE))
        self.assertEqual(details['turns'], [dict(chain='turn-1', assignment_id='turn-1', events=[EVENT_ID],
            route=dict(id='route', context_status='verified_for_route'), count=1, recovery='export')])
        self.assert_export_step(details['step'])

    def assert_unverified_route(self, details, head):
        self.assertEqual(details['assignment_id'], head)
        self.assertEqual(details['turns'], [dict(chain='turn-1', assignment_id='turn-1', events=[],
            route=dict(id='route', context_status='unverified'), count=1, recovery='probe')])
        step = details['step']
        self.assertEqual((step['command'], step['payload'], step['chooses']),
                         ('review-probe', dict(route_id='route'), ['id']))
        self.assertEqual(step['then'], [dict(command='review-series', assignment_id=head)])


class OwnContentStopTests(ChainStopSupport, OpennessReadingCase):
    """Rules C23 to C27 on a chain whose own packet holds the full text of work 3."""

    def test_v1_next_assignment_is_refused_after_a_bundle_names_an_own_capture_as_a_supplement(self):
        """On the base review-assignment records the next turn: assertRaises fails."""
        native = self.start_observed_chain()
        version = self.hide_extraction_in_bundle(3)['version_id']
        refused = self.refuse_assignment(self.build_payload(native, 'new-next', prior='new'), OUTDATED)
        self.assertEqual(refused.message, v7.UNITS_CHANGED_MESSAGE)
        self.assert_units(refused.details, version)
        self.assert_units_handoff(self.read_plan('new'), version)

    def test_v3_recorded_assignment_is_not_sent_after_the_change(self):
        """On the base review-run sends the request: assertRaises fails."""
        native = self.start_observed_chain()
        self.mutate(protocol.record_assignment, self.build_payload(native, 'new-next', prior='new'))
        version = self.hide_extraction_in_bundle(3)['version_id']
        self.assert_units(self.refuse_call('new-next', OUTDATED).details, version)
        self.assert_units_handoff(self.read_plan('new-next'), version)

    def test_v4_refused_turn_is_not_replaced_after_the_change(self):
        """On the base the plan stays the replacement of the refused turn: the assertion on the plan fails."""
        self.start_carrying_chain()
        self.invoke('new', dict(stage='reading', notes=42))
        replacement = self.read_plan('new')
        self.assertEqual((replacement['state'], replacement['command']), ('replace', 'review-assignment'))
        version = self.hide_extraction_in_bundle(3)['version_id']
        self.assert_units_handoff(self.read_plan('new'), version)
        self.refuse_assignment(replacement['payload'], OUTDATED)

    def test_v5_finding_of_a_changed_chain_names_the_units(self):
        """On the base the refusal of the finding names no unit: the assertion on the units fails."""
        head = self.read_head(self.finish_chain(self.start_carrying_chain()))
        version = self.hide_extraction_in_bundle(3)['version_id']
        self.assert_stale(head, version)
        with self.assertRaises(ResearchError) as caught:
            self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))
        self.assertEqual(caught.exception.message, v7.UNITS_CHANGED_MESSAGE)
        self.assert_units_handoff(self.read_plan(head), version)

    def test_k1_chain_whose_units_did_not_change_keeps_its_plan(self):
        """Contract: a capture chosen with select-main-capture leaves the units and the plan; a new search changes the
        gaps, and the plan is the handoff of the freshness check without units; review-assignment records the next
        turn."""
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        native = self.start_observed_chain()
        version = self.versions[1]
        corrected = self.capture(version, pdf_text=self.build_text(2).replace('continues here', 'continues  here'))
        self.mutate(select_main_capture, dict(version_id=version, source_id=corrected['source_id'],
                                              reason='The first extraction lost the spacing of the formulas.'))
        self.assertEqual(self.describe_plan('new'), ('continue', 'review-assignment', None))
        self.searches.append(ProblemOpennessMinimumTests.search(self, self.versions))
        plan = self.read_plan('new')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', OUTDATED))
        self.assertNotIn('units', plan['details'])
        self.mutate(protocol.record_assignment, self.build_payload(native, 'new-next', prior='new'))
        self.assertIn('new-next', self.store.snapshot()['records']['review_assignment'])

    def test_k2_chain_with_a_recorded_finding_keeps_its_plan(self):
        """Contract: the plan of a chain whose finding is recorded stays done after a change of its unit and after a
        contamination of its carried turn."""
        head = self.read_head(self.finish_chain(self.start_carrying_chain()))
        self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))
        self.assertEqual(self.read_plan(head)['state'], 'done')
        self.hide_extraction_in_bundle(3)
        self.contaminate()
        self.assertEqual(self.read_plan(head)['state'], 'done')

    def test_recorded_stale_finding_points_to_reassessment(self):
        head = self.read_head(self.finish_chain(self.start_carrying_chain()))
        finding = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))['result']
        self.hide_extraction_in_bundle(3)
        records = self.store.snapshot()['records']
        with self.assertRaises(ResearchError) as caught:
            self.verify_final(head)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        [obligation] = assess_openness(records, self.artifacts)
        self.assertEqual(obligation['code'], 'problem_openness_stale')
        self.assertEqual(caught.exception.details.get('reassessment'),
                         dict(obligation=obligation['code'], openness_id=finding['id'], command='export'))
        self.assertEqual(obligation['openness_id'], finding['id'])
        self.assertEqual(self.read_plan(head)['state'], 'done')
        self.assertEqual(self.store.snapshot()['records']['problem_openness'][finding['id']], finding)

        minimum = dict(searches=[dict(formulation=self.selected['statement'], search_id=search['id'])
                                 for search in self.searches], settling_work_id=None, duplicate_resolutions=[])
        native = export_openness(self.store, dict(assignment_id='latest', reviewer_id='openness-reviewer',
                                  minimum=minimum), self.root / 'latest-export', accept_gaps=True)['native_packet']
        self.mutate(protocol.record_assignment, self.build_payload(native, 'latest'))
        self.assertEqual(self.describe_plan('latest'), ('run', 'review-run', None))
        code, lines, _ = self.drive('latest')
        self.assertEqual((code, lines[-1]['state']), (0, 'done'), lines[-1])
        latest_head = lines[-1]['head_id']
        findings = self.store.snapshot()['records']['problem_openness']
        self.assertEqual(set(findings), {head, latest_head})
        self.assertEqual(findings[head], finding)
        [current] = assess_openness(self.store.snapshot()['records'], self.artifacts)
        self.assertEqual((current['code'], current['openness_id']), ('problem_openness_unresolved', latest_head))

        with self.assertRaises(ResearchError) as superseded:
            self.verify_final(head)
        self.assertEqual(superseded.exception.code, 'openness_reading_incomplete')
        self.assertIn('details.latest_openness_id', superseded.exception.message)
        self.assertNotIn('reassessment', superseded.exception.details)
        self.assertEqual(superseded.exception.details['openness_id'], head)
        self.assertEqual(superseded.exception.details['latest_openness_id'], latest_head)
        self.assertEqual(superseded.exception.details['units'], caught.exception.details['units'])
        self.assertEqual(self.read_plan(head)['state'], 'done')
        self.assertEqual(self.store.snapshot()['records']['problem_openness'], findings)

    def test_successor_probes_its_destination_before_the_only_export(self):
        self.start_observed_chain()
        version = self.hide_extraction_in_bundle(3)['version_id']
        self.fail_probe()
        plan = self.read_plan('new')
        self.assertEqual((plan['state'], plan['command'], plan['code']), ('handoff', 'review-probe', OUTDATED))
        details = plan['details']
        self.assertEqual([row['unit']['version_id'] for row in details['units']], [version])
        self.assertEqual(details['destination_probe'], 'route')
        self.assertNotIn('renewal', details)
        step = details['step']
        self.assertEqual(step['then'], [dict(command='review-series', assignment_id='new')])
        probe_codex_route(self, 'good', step['payload']['route_id'])
        step = self.read_plan('new')['details']['step']
        self.assertEqual(step['command'], 'export')
        successor = self.take_export(step, 'fresh')
        self.assertEqual(successor['context_state']['context_status'], 'verified_for_route')
        self.assertEqual(self.describe_plan('fresh'), ('run', 'review-run', None))
        packet = self.read_packet('fresh')
        output = self.answer_v7(packet) if 'reading_turn' in packet else self.answer_final(packet)
        self.assertNotIn('output_refusal', self.invoke('fresh', output))

    def test_k3_chain_whose_inventory_holds_the_finding_index_keeps_its_plan(self):
        """Contract (review F1): the comparison rebuilds with the finding index of the capacity plan, so a chain whose
        stored inventory holds the finding-index unit keeps its plan, and review-assignment records its next turn."""
        from research_harness.review_search_delivery import FINDING_INDEX_UNIT_FIELD
        with patch.object(v7, 'FINDING_INDEX_CHARACTERS', 100):
            native = self.start_observed_chain()
            inventory = json.loads(self.artifacts.read(
                self.store.snapshot()['records']['review_assignment']['new']['reading_chain']['inventory']))
            self.assertIn(FINDING_INDEX_UNIT_FIELD, [row['identity'].get('field') for row in inventory['units']])
            self.assertEqual(self.describe_plan('new'), ('continue', 'review-assignment', None))
            self.mutate(protocol.record_assignment, self.build_payload(native, 'new-next', prior='new'))
        self.assertIn('new-next', self.store.snapshot()['records']['review_assignment'])

    def test_stop_on_a_stale_route_record_takes_the_renewal_and_then_the_export(self):
        """Review F3 and lane route-plan: the route record of the chain pins another runtime. The plan keeps the stop
        with its units and gives the step that records a route record with the installed runtime, then its probe,
        then the export and the assignment on that record. The store accepts each step in that order. On the base
        the plan is the stale-route procedure of the head, which names no unit."""
        native = self.start_observed_chain()
        version = self.hide_extraction_in_bundle(3)['version_id']
        make_stale(self, 'route')
        plan = self.read_plan('new')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'review-route', OUTDATED))
        self.assertEqual([row['unit']['version_id'] for row in plan['details']['units']], [version])
        self.assertEqual(plan['details']['renewal'], dict(admitted=True, route_id=None, lacks='record'))
        step = plan['details']['step']
        self.assertEqual([row['command'] for row in step['then']], ['review-probe', 'review-series'])
        self.mutate(cli.OPERATIONS['review-route'], dict(step['payload'], id='route-renewed'))
        plan = self.read_plan('new')
        self.assertEqual((plan.get('command'), plan.get('code'), plan.get('details', {}).get('renewal')),
                         ('review-probe', OUTDATED, dict(admitted=True, route_id='route-renewed', lacks='probe')))
        probe_codex_route(self, 'probe-route-renewed', plan['details']['step']['payload']['route_id'])
        plan = self.read_plan('new')
        self.assertEqual((plan.get('command'), plan.get('code')), ('export', OUTDATED))
        self.assertNotIn('renewal', plan['details'])
        step = plan['details']['step']
        self.assertEqual([row['payload']['route_id'] for row in step['then']], ['route-renewed', 'route-renewed'])
        minimum = dict(searches=[dict(formulation=self.selected['statement'], search_id=search['id'])
                                 for search in self.searches], settling_work_id=None, duplicate_resolutions=[])
        later = export_openness(self.store, dict(assignment_id='later', reviewer_id=step['payload']['reviewer_id'],
                                minimum=minimum), self.root / 'later-export', accept_gaps=True)['native_packet']
        checked = dict(step['then'][0]['payload'], id='later', context=dict(native_packet=later))
        check_mutation(self.store, protocol.record_assignment, checked, expected_revision=self.store.revision,
                       request_id='check-later', bounded_payload=checked)
        receipt = self.mutate(protocol.record_assignment, dict(step['then'][1]['payload'], id='later',
                                                               context=dict(native_packet=later)))['result']
        self.assertEqual((receipt['route_id'], receipt['protocol_selection']['protocol']),
                         ('route-renewed', v7.PROTOCOL))


class SuppliedOwnContentStopTests(integration.SuppliedWorkSupport, ChainStopSupport, OpennessReadingCase):
    """Work 1 is supplied with import-fulltext. Its publisher copy arrives later with the bytes of the supplied file
    and another extracted text, so the gaps of the minimum stay as they were (change C of the design)."""

    def test_v2_planner_hands_off_when_the_gaps_of_the_minimum_did_not_change(self):
        """On the base the plan is the next assignment: the assertion on the plan fails."""
        native = self.start_observed_chain()
        self.fetch_publisher_copy(same_bytes=True)
        records = self.store.snapshot()['records']
        packet = json.loads(self.artifacts.read(native))
        self.assertTrue(assess_packet_freshness(records, self.artifacts, packet)['current'])
        self.assert_units_handoff(self.read_plan('new'), self.DOI)


class SeriesOwnContentStopTests(integration.SuppliedWorkSupport, ChainStopSupport, OpennessReadingCase):
    """Texts of 150,000 characters, so the chain takes three reading turns and its final."""
    text_sizes = (150000, 150000, 150000)

    def test_j1_series_stops_and_a_later_chain_carries_the_ready_turns_of_the_stopped_chain(self):
        """J1, with the assertions of V6 (review F7). The publisher copy of the supplied work arrives after the first
        reviewer call of the chain. On the base the driver makes three more calls (the first assertion fails), and
        with rules C16 to C22 alone the later chain starts only after them. Here the driver stops with the units, the
        later chain on the step carries turn-1 and the ready turn of the stopped chain, and it records its finding
        after two calls. It takes 5 to 7 s: the chain needs texts of this size for three reading turns."""
        turn_fixtures.stub_fit_for_full_v7_reading_level(self)
        self.start_carrying_chain()
        code, lines, calls = self.drive('new', after_first_call=lambda: self.fetch_publisher_copy(same_bytes=True))
        self.assertEqual(calls, [False])
        self.assertEqual(code, 3)
        self.assert_units_handoff(lines[-1], self.DOI)
        self.assertEqual(self.store.snapshot()['records'].get('problem_openness', {}), {})
        step = lines[-1]['details']['step']
        minimum = dict(searches=[dict(formulation=self.selected['statement'], search_id=search['id'])
                                 for search in self.searches], settling_work_id=None, duplicate_resolutions=[])
        later = export_openness(self.store, dict(assignment_id='later', reviewer_id=step['payload']['reviewer_id'],
                                minimum=minimum), self.root / 'later-export', accept_gaps=True)['native_packet']
        receipt = self.mutate(protocol.record_assignment, dict(step['then'][1]['payload'], id='later',
                                                               context=dict(native_packet=later)))['result']
        report = receipt['carried_readings']
        self.assertEqual(integration.list_carried_turns(report), {'turn-1': ['turn-1'], 'new': ['new']})
        self.assertGreaterEqual(report['credited_characters'], 200000)
        self.assertLess(report['remaining_characters'], 100000)
        code, lines, calls = self.drive('later')
        self.assertEqual((code, len(calls)), (0, 2))
        self.assertEqual(sorted(self.store.snapshot()['records']['problem_openness']), [lines[-1]['head_id']])


class CapacityReplacementStopTests(integration.SuppliedWorkSupport, OpennessReadingCase):
    """A v7 chain of its own (no carried turn) whose second turn fails with a response over the limit."""
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)
    build_v7_answer = turn_fixtures.V7CommandTests.build_v7_answer

    def setUp(self):
        super().setUp()
        enabled = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        enabled.start()
        self.addCleanup(enabled.stop)

    def test_v8_capacity_replacement_is_not_assigned_after_the_change(self):
        """On the base the plan after the change is still the replacement: the assertion on the plan fails."""
        from test_review_output_profile import build_row
        with patch.object(capacity, 'read_profile_priors', return_value=[
                build_row(level=None, thinking=1000, count=8, over=1),
                build_row(kind='final', level=None, thinking=1000, count=8, over=1)]):
            native = self.export()
            self.assign('turn-1', native)
            self.invoke('turn-1', self.build_v7_answer(self.read_packet('turn-1')))
            head = self.assign('size-position', native, prior='turn-1')['id']
            self.assertIn('reading_turn', self.read_packet(head))

            def respond(route, request, credential):
                raise ResearchError('review_response_too_large', 'Synthetic retained response body limit.')
            for number in range(8):
                with patch.object(protocol.transport, 'send_request', side_effect=respond):
                    self.mutate(protocol.invoke_assignment, dict(id='size-%d' % number, assignment_id=head),
                                credential='fixture')
                action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
                if action['state'] != 'run':
                    break
            self.assertEqual((action['state'], action['command']), ('replace', 'review-assignment'), action)
            self.assertEqual(action['payload']['context'].get('reading_replacement'), head)
            self.fetch_publisher_copy(same_bytes=True)
            plan = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', OUTDATED))
            self.assertEqual([row['unit']['version_id'] for row in plan['details']['units']], [self.DOI])
            before = len(self.store.snapshot()['records']['review_assignment'])
            with self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_assignment, action['payload'])
            self.assertEqual(caught.exception.code, OUTDATED)
            self.assertEqual(len(self.store.snapshot()['records']['review_assignment']), before)


class UnreadOwnContentStopTests(UnreadFinalSupport):
    hide_extraction_in_bundle = integration.StoredSelectionIntegrationTests.hide_extraction_in_bundle

    def test_v7_final_over_unread_ranges_is_not_assigned_after_the_change(self):
        """On the base review-assignment records the final over the unread ranges: assertRaises fails."""
        native = self.export()
        self.assign('turn-1', native)
        self.invoke('turn-1', self.build_v7_answer(self.read_packet('turn-1')))
        self.assign('refused', native, prior='turn-1')
        head = 'refused'

        class LaterClock(failures.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2099, 1, 1, tzinfo=failures.timezone.utc)
        with patch.object(failures, 'datetime', LaterClock):
            for number in range(80):
                action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
                if action.get('details', {}).get('reason') == 'unread':
                    break
                self.assertIn(action['state'], ('run', 'replace'), action)
                if action['state'] == 'replace':
                    head = self.mutate(protocol.record_assignment, action['payload'])['result']['id']
                else:
                    self.refuse_provider(action['payload']['assignment_id'], action['payload']['id'])
            else:
                self.fail('A refused floor reading must reach the plan of its unread final.')
            version = self.hide_extraction_in_bundle(1)['version_id']
            before = len(self.store.snapshot()['records']['review_assignment'])
            with self.assertRaises(ResearchError) as caught:
                self.mutate(protocol.record_assignment, action['payload'])
            self.assertEqual(caught.exception.code, OUTDATED)
            self.assertEqual([row['unit']['version_id'] for row in caught.exception.details['units']], [version])
            self.assertEqual(len(self.store.snapshot()['records']['review_assignment']), before)
            plan = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', OUTDATED))


class MathContentStopTests(MathV2Support):
    """A math chain of exactory-openness-reading-math-v2 over the one text of the fixture."""
    hide_extraction_in_bundle = integration.StoredSelectionIntegrationTests.hide_extraction_in_bundle

    def attest(self, native, assignment_id):
        packet = json.loads(self.artifacts.read(native))
        packet['reviewer']['attestation_id'] = 'review-assignment:' + assignment_id
        return self.artifacts.put(serialize_record(packet), 'application/json')

    def check_math_assignment(self, native, assignment_id):
        payload = dict(id=assignment_id, route_id='route', dossier_id=None, role='native_math',
                       reviewer_id='openness-reviewer', author_id='problem-author',
                       context=dict(native_packet=self.attest(native, assignment_id)))
        return check_mutation(self.store, protocol.record_assignment, payload, expected_revision=self.store.revision,
                              request_id='check-' + assignment_id, bounded_payload=payload)

    def test_m1_math_chain_is_not_sent_after_a_bundle_names_its_capture_as_a_supplement(self):
        """M1 with M2 (review F2). On the base the plan stays the reviewer call and review-run sends the request: the
        assertion on the plan fails. The step leaves the openness minimum open, because a packet on the old minimum
        is refused at its first assignment, and a minimum that binds a full reading of the present main capture is
        admitted."""
        native = self.build_math_packet()
        self.assign('math-1', self.attest(native, 'math-1'))
        packet = self.read_packet('math-1')
        self.assertEqual(reading.find_protocol(packet), MATH_V2)
        bundle = self.hide_extraction_in_bundle(1)
        records = self.store.snapshot()['records']
        plan = plan_next(records, self.artifacts, 'math-1')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')),
                         ('handoff', 'search review-packet', OUTDATED))
        self.assertEqual([(row['state'], row['unit']['version_id']) for row in plan['details']['units']],
                         [('left', bundle['version_id'])])
        step = plan['details']['step']
        self.assertNotIn('openness', step['payload'])
        self.assertIn('openness', step['chooses'])
        self.assertEqual(step['prerequisites'][0], dict(condition=v7.MATH_UNITS_PREREQUISITE))
        sent = []

        def respond(route, request, credential):
            sent.append(request)
            return build_codex_response(route, request, self.build_v7_answer(packet))
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            with self.assertRaises(ResearchError) as caught:
                protocol.invoke_assignment(self.store, dict(id='attempt-math-1', assignment_id='math-1'),
                    credential='test-only', expected_revision=self.store.revision, request_id='invoke-math-1')
        self.assertEqual((caught.exception.code, sent), (OUTDATED, []))
        # M2: the old minimum gives no packet; a full reading of the present main capture in a new minimum does.
        with self.assertRaises(ResearchError) as caught:
            self.check_math_assignment(native, 'math-old')
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        self.assertEqual(caught.exception.details['version_id'], bundle['version_id'])
        self.mutate(record_reading, self.full_note(bundle, 'read-main-1'))
        self.check_math_assignment(self.build_math_packet(name='math-export-2', assignment_id='math-2'), 'math-2')


class MathBindingStopTests(MathV2Support):
    text_sizes = (1000, 1000)
    hide_extraction_in_bundle = integration.StoredSelectionIntegrationTests.hide_extraction_in_bundle

    def test_math_stop_lists_both_changed_versions(self):
        self.assign('math-1', self.build_math_packet())
        for number in (1, 2):
            self.hide_extraction_in_bundle(number)
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, 'math-1')
        self.assertEqual(plan['code'], OUTDATED)
        self.assertEqual({row['unit']['version_id'] for row in plan['details']['units']}, set(self.versions))
        self.assertEqual({row['reading_id'] for row in plan['details']['bindings']}, {'read-1', 'read-2'})

    def test_math_stop_keeps_the_other_original_of_the_same_version(self):
        version = self.versions[0]
        second = self.capture(version, body='A distinct complete original. References: none.')
        bundle = self.bundle(version, capture=second, bundle_id='second-original')
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, 'read-second-original'))
        assigned = self.assign('math-1', self.build_math_packet())
        inventory = v7.read_carried_inventory(self.artifacts, assigned)
        self.assertEqual({row['sha256'] for row in inventory['units']
                          if row['identity'].get('version_id') == version},
                         {self.texts[version]['sha256'], second['text']['sha256']})
        self.hide_extraction_in_bundle(1)
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, 'math-1')
        self.assertEqual(plan['code'], OUTDATED)
        self.assertEqual([(row['state'], row['unit']['text_sha256']) for row in plan['details']['units']],
                         [('left', self.texts[version]['sha256'])])
        self.assertEqual([row['reading_id'] for row in plan['details']['bindings']], ['read-1'])


class CarriedTurnStopTests(ChainStopSupport, OpennessReadingCase):
    """Rules C28 to C32 on the chain 'new' on the route record of turn-1."""

    def test_w3_next_assignment_is_refused_after_a_contamination_of_a_carried_turn(self):
        """On the base review-assignment records the next turn: assertRaises fails."""
        native = self.start_observed_chain()
        self.contaminate()
        refused = self.refuse_assignment(self.build_payload(native, 'new-next', prior='new'), CARRIED)
        self.assertEqual(refused.message, v7.CARRIED_TURN_MESSAGE)
        self.assert_contaminated_turn(refused.details, 'new')
        plan = self.read_plan('new')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', CARRIED))
        self.assert_contaminated_turn(plan['details'], 'new')

    def test_w4_recorded_assignment_is_not_sent_after_a_contamination_of_a_carried_turn(self):
        """On the base review-run sends the request: assertRaises fails."""
        native = self.start_observed_chain()
        self.mutate(protocol.record_assignment, self.build_payload(native, 'new-next', prior='new'))
        self.contaminate()
        self.assert_contaminated_turn(self.refuse_call('new-next', CARRIED).details, 'new-next')
        self.assertEqual(self.describe_plan('new-next'), ('handoff', 'export', CARRIED))

    def test_w6_refused_turn_is_not_replaced_after_a_contamination_of_a_carried_turn(self):
        """On the base the plan stays the replacement of the refused turn: the assertion on the plan fails."""
        self.start_carrying_chain()
        self.invoke('new', dict(stage='reading', notes=42))
        replacement = self.read_plan('new')
        self.assertEqual((replacement['state'], replacement['command']), ('replace', 'review-assignment'))
        self.contaminate()
        self.assertEqual(self.describe_plan('new'), ('handoff', 'export', CARRIED))
        self.refuse_assignment(replacement['payload'], CARRIED)

    def test_w7_returned_finding_names_the_carried_turn_and_the_export(self):
        """On the base the plan of the head with the returned finding is a stop with openness_reading_incomplete and
        the step record-check: the assertion on the plan fails."""
        head = self.read_head(self.finish_chain(self.start_carrying_chain()))
        self.assertEqual(self.describe_plan(head), ('record', 'problem-openness', None))
        self.contaminate()
        plan = self.read_plan(head)
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', CARRIED))
        self.assert_contaminated_turn(plan['details'], head)

    def test_w8_status_row_of_the_head_holds_the_handoff(self):
        """On the base the row review_output_pending of the head keeps the action run, review-run."""
        native = self.start_observed_chain()
        self.mutate(protocol.record_assignment, self.build_payload(native, 'new-next', prior='new'))

        def read_actions():
            report = cli._build_status_report(self.store)
            return [(action.get('state'), action.get('command'), action.get('code'))
                    for row in report['obligations'] if row.get('assignment_id') == 'new-next'
                    and row['code'] == 'review_output_pending' for action in row['next']]
        self.assertEqual(read_actions(), [('run', 'review-run', None)])
        self.contaminate()
        self.assertEqual(read_actions(), [('handoff', 'export', CARRIED)])

    def test_k1_contamination_after_the_last_carried_turn_leaves_the_plan(self):
        """Contract: the earlier chain takes a second turn after the first assignment of the v7 chain. A
        contamination of that turn takes nothing from the carried selection, so the plan and the next assignment
        stay."""
        native = self.start_observed_chain()
        self.enabled.stop()
        self.assign('earlier-2', self.native, prior='turn-1')
        self.invoke('earlier-2', self.build_reading_response(self.read_packet('earlier-2'), excerpts=[]))
        self.enabled.start()
        before = self.describe_plan('new')
        self.assertEqual(before[:2], ('continue', 'review-assignment'))
        self.contaminate('earlier-2')
        self.assertEqual(self.describe_plan('new'), before)
        self.mutate(protocol.record_assignment, self.build_payload(native, 'new-next', prior='new'))
        self.assertIn('new-next', self.store.snapshot()['records']['review_assignment'])

    def test_k3_listed_turns_are_the_turns_whose_state_lost_its_verified_context(self):
        """Contract: the check reads two kinds of record. It lists turn-1 exactly when the state of turn-1 holds a
        context obligation: after a failed probe, after a probe that verifies the route record, and after a
        contamination."""
        from research_harness.openness_carried import list_unverified_carried_turns
        self.start_observed_chain()

        def compare(expected):
            records = self.store.snapshot()['records']
            rows = list_unverified_carried_turns(records, self.artifacts, records['review_assignment']['new'])
            state = protocol.assignment_state(records, self.artifacts, 'turn-1')
            self.assertEqual((bool(rows), sorted(item['code'] for item in state['obligations'])), expected)
        compare((False, []))
        self.fail_probe()
        compare((True, ['review_context_unverified']))
        probe_codex_route(self, 'completed-probe', 'route')
        compare((False, []))
        self.contaminate()
        compare((True, ['review_context_contaminated']))


class OtherRouteRecordTests(ChainStopSupport, OpennessReadingCase):
    """Rules C28 to C32 on the chain 'new' on 'second-route', a route record of the same identity as 'route'."""
    chain_route = 'second-route'

    def test_w5_first_call_waits_for_a_probe_of_the_route_record_of_a_carried_chain(self):
        """On the base review-run sends the first request of the chain: assertRaises fails."""
        self.start_carrying_chain()
        self.fail_probe()
        self.assert_unverified_route(self.refuse_call('new', CARRIED).details, 'new')
        plan = self.read_plan('new')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'review-probe', CARRIED))
        self.assert_unverified_route(plan['details'], 'new')
        probe_codex_route(self, 'completed-probe', 'route')
        self.assertEqual(self.describe_plan('new')[:2], ('run', 'review-run'))
        self.invoke('new', self.answer_v7(self.read_packet('new')))
        self.assertEqual(self.describe_plan('new')[:2], ('continue', 'review-assignment'))

    def test_own_contamination_preempts_origin_probe_and_retirement(self):
        native = self.start_observed_chain()
        self.mutate(protocol.record_assignment, self.build_payload(native, 'next', prior='new'))
        self.fail_probe()
        self.contaminate('new')
        for stale in (False, True):
            with self.subTest(stale=stale):
                if stale:
                    make_stale(self, 'route')
                plan = self.read_plan('next')
                self.assertEqual((plan['command'], plan['code']), ('export', CARRIED))
                details = plan['details']
                self.assertEqual(details['turns'][0]['recovery'], 'retire' if stale else 'probe')
                self.assertEqual(details['own_turn']['events'], [EVENT_ID])
                self.assertEqual(details['own_turn']['assignment_id'], 'new')
                refusal = self.refuse_call('next', 'review_context_contaminated')
                self.assertEqual(refusal.details['next']['details']['step'], details['step'])

    def test_stale_route_record_of_a_carried_chain_is_retired_and_the_chain_continues(self):
        """Review P2-2: the latest probe of 'route' has no answer, and 'route' pins another runtime, so it takes no
        probe. The step is its retirement; after it, the state of 'route' is that of its probe with an answer, and
        the chain continues. On the base the plan stays the next assignment: the first assertion fails."""
        self.start_observed_chain()
        self.fail_probe()
        make_stale(self, 'route')
        plan = self.read_plan('new')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'review-context', CARRIED))
        self.assertEqual([(row['assignment_id'], row['route'], row['recovery']) for row in plan['details']['turns']],
                         [('turn-1', dict(id='route', context_status='unverified'), 'retire')])
        step = plan['details']['step']
        self.assertEqual((step['id'], step['payload']), (8, dict(assignment_id='turn-1', source='owner',
                                                                 kind='route_retired')))
        from research_harness.review_route_plan import RETIREMENT_ORDER_TEXT
        self.assertIn(dict(condition=RETIREMENT_ORDER_TEXT, route_id='route'), step['prerequisites'])
        self.assertEqual(step['then'], [dict(command='review-series', assignment_id='new')])
        self.mutate(cli.OPERATIONS[step['command']], dict(step['payload'], id='retire-route',
            reason='The owner retired the stale route record.', evidence=[self.artifacts.put(b'Owner.', 'text/plain')]))
        self.assertEqual(self.describe_plan('new'), ('continue', 'review-assignment', None))

    def test_stale_route_record_whose_answer_does_not_verify_it_gives_the_export(self):
        """Review P2-2: the latest probe of 'route' answers with the wrong controls, and 'route' pins another runtime.
        Retirement would leave the route unverified, so the plan gives the export immediately."""
        self.start_observed_chain()
        self.fail_probe(answered=True)
        self.fail_probe(probe_id='unanswered-probe')
        make_stale(self, 'route')
        plan = self.read_plan('new')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', CARRIED))
        self.assertEqual(plan['details']['turns'][0]['recovery'], 'export')
        self.assertEqual([row['payload']['route_id'] for row in plan['details']['step']['then']],
                         ['second-route', 'second-route'])


class SeriesCarriedTurnStopTests(ChainStopSupport, OpennessReadingCase):
    text_sizes = (50000, 50000, 50000)

    def test_w1_series_makes_no_call_after_a_contamination_of_a_carried_turn(self):
        """One successful call leaves work available when the origin is contaminated."""
        self.start_carrying_chain()
        with patch('signal.signal') as installer:
            code, lines, calls = self.drive('new', after_first_call=self.contaminate)
        installer.assert_not_called()
        self.assertEqual((calls, code), ([False], 3))
        last = lines[-1]
        self.assertEqual((last['state'], last.get('command'), last.get('code')), ('handoff', 'export', CARRIED))
        self.assert_contaminated_turn(last['details'], 'new')
        self.assertEqual(self.store.snapshot()['records'].get('problem_openness', {}), {})


class SeriesOtherRouteRecordTests(ChainStopSupport, OpennessReadingCase):
    chain_route = 'second-route'
    text_sizes = (50000, 50000, 50000)

    def test_w2_series_waits_for_a_probe_and_then_records_its_finding(self):
        """A failed probe holds the next call until a verifying probe lets the driver record the finding.
        Texts of 150,000 characters give the chain three reading turns and its final."""
        turn_fixtures.stub_fit_for_full_v7_reading_level(self)
        self.start_carrying_chain()
        code, lines, calls = self.drive('new', after_first_call=self.fail_probe)
        self.assertEqual((calls, code), ([False], 3))
        last = lines[-1]
        self.assertEqual((last['state'], last.get('command'), last.get('code')), ('handoff', 'review-probe', CARRIED))
        self.assert_unverified_route(last['details'], 'new')
        probe_codex_route(self, 'completed-probe', 'route')
        code, lines, calls = self.drive('new')
        self.assertEqual(code, 0)
        self.assertTrue(calls)
        self.assertEqual(len(self.store.snapshot()['records']['problem_openness']), 1)


class NestedChainTests(ChainStopSupport, OpennessReadingCase):
    def test_w9_chain_that_carries_a_carrying_chain_stops_for_the_innermost_turn(self):
        """The chain 'outer' carries the v7 chain 'new', which carries turn-1, and turn-1 directly. On the base the
        plan of 'outer' stays the next assignment after a contamination of turn-1: the assertion on the plan fails.
        The handoff holds one item for turn-1 (review P2-1)."""
        self.finish_chain(self.start_carrying_chain())
        outer = self.export(name='outer-export', assignment_id='outer')
        receipt = self.mutate(protocol.record_assignment, self.build_payload(outer, 'outer'))['result']
        self.assertEqual(set(integration.list_carried_turns(receipt['carried_readings'])), {'turn-1', 'new'})
        self.invoke('outer', self.answer_v7(self.read_packet('outer')))
        self.contaminate()
        plan = self.read_plan('outer')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', CARRIED))
        self.assert_contaminated_turn(plan['details'], 'outer')

    def test_w9_nested_origin_is_checked_without_a_direct_selection(self):
        self.start_observed_chain()
        outer = self.export(name='outer-export', assignment_id='outer')
        middle = self.store.snapshot()['records']['review_assignment']['new']['recorded_revision']
        list_chains = openness_carried.list_qualifying_chains

        def list_outer_candidates(records, artifacts, identity, **options):
            candidates = list_chains(records, artifacts, identity, **options)
            return [row for row in candidates
                    if identity['recorded_revision'] <= middle or row['chain'] != 'turn-1']

        # Restrict the outer candidate list only. Its selected new turn still has the genuine stored origin.
        with patch.object(openness_carried, 'list_qualifying_chains', side_effect=list_outer_candidates):
            receipt = self.mutate(protocol.record_assignment, self.build_payload(outer, 'outer'))['result']
        self.assertEqual(set(integration.list_carried_turns(receipt['carried_readings'])), {'new'})
        self.contaminate()
        self.assert_contaminated_turn(self.read_plan('outer')['details'], 'outer')


class InventoryOrderStopTests(ChainStopSupport, OpennessReadingCase):
    def start_ranked_chain(self):
        native = self.export(priority=self.versions[::-1], name='ranked-packet', assignment_id='ranked')
        self.mutate(protocol.record_assignment, self.build_payload(native, 'ranked'))

    def change_priority(self):
        # This is the same original with a second extraction. The main text stays in place, but another bound
        # work's text now changes the priority of this version.
        self.capture(self.versions[0], pdf_text=self.build_text(3))

    def test_reordered_equal_units_stop_plan_and_send(self):
        self.start_ranked_chain()
        self.change_priority()
        plan = self.read_plan('ranked')
        with self.subTest(command='plan'):
            self.assertEqual((plan['state'], plan['command'], plan.get('code')), ('handoff', 'export', OUTDATED))
            self.assertTrue(all(row['state'] == 'changed' for row in plan['details']['units']))
            reordered = {row['unit']['version_id']: row for row in plan['details']['units']
                         if row['unit']['kind'] == 'fulltext'}
            self.assertEqual(set(reordered), set(self.versions))
            self.assertEqual((reordered[self.versions[0]]['stored']['position'],
                              reordered[self.versions[0]]['current']['position']), (3, 1))
            self.assertNotEqual(reordered[self.versions[0]]['stored']['handle'],
                                reordered[self.versions[0]]['current']['handle'])
            self.assert_export_step(plan['details']['step'])
        with self.subTest(command='review-run'):
            self.assertEqual(self.refuse_call('ranked', OUTDATED).details, plan['details'])

    def test_series_stops_after_an_alternate_extraction_reorders_equal_units(self):
        self.start_ranked_chain()
        code, lines, calls = self.drive('ranked', after_first_call=self.change_priority)
        self.assertEqual((code, calls), (3, [False]))
        self.assertEqual((lines[-1]['state'], lines[-1]['command'], lines[-1]['code']),
                         ('handoff', 'export', OUTDATED))
        self.assert_export_step(lines[-1]['details']['step'])


if __name__ == '__main__':
    unittest.main()
