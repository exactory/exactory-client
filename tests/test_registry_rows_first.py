"""RR8/RR9 exercise registry rows through retained records and public commands."""

import copy
from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import patch

import codex_route_fixtures as codex
import openness_reading_fixtures as reading_fixtures
import problem_parts_fixtures as legacy_fixtures
import test_review_continuation as continuation_fixtures
import test_review_staged_scale_entry as staged_fixtures
from research_harness import record_check, review_continuation, review_series
from research_harness import review_protocol as protocol, stop_classes
from research_harness.errors import ResearchError
from research_harness.review_plan_steps import complete_plan


class RegistryRowsFirstTests(unittest.TestCase):
    def prepare(self, cls, **attributes):
        fixture = cls()
        for key, value in attributes.items():
            setattr(fixture, key, value)
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_a10c_refused_upgrade_runs_the_preserved_slot_to_a_recorded_review(self):
        fixture = self.prepare(staged_fixtures.StagedScaleEntryTests, output_tokens=1000)
        fixture.install_scale_packet(large=False)
        # Every original fits one legacy part; the all-claim staged card exceeds
        # the output allowance even though this input route serves the slot.
        codex.limit_codex_input(fixture, 1000000)
        first = fixture.assign('preserved')
        before = copy.deepcopy(fixture.case.store.snapshot())
        with self.assertRaises(ResearchError) as caught:
            fixture.case.assign('result', 'first', identifier='unsupported',
                                staged_upgrade=dict(assignment_id=first['id']))
        self.assertEqual(caught.exception.code, 'review_staged_scale_unsupported')
        self.assertEqual(fixture.case.store.snapshot(), before)
        terminal = stop_classes.describe_terminal(caught.exception.code)
        plan = complete_plan(before['records'], fixture.case.artifacts, first, terminal)
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'review-series'))
        self.assertEqual(plan['details']['step']['payload'], dict(assignment_id=first['id']))

        def build_response(route, request, credential):
            packet = protocol.review_excerpts.read_sent_packet(request)
            if packet['problem_evidence_parts']['stage'] == 'part':
                output = dict(stage='result', phase='evidence_part', part=packet['problem_evidence_parts']['turn'],
                    notes=[dict(unit=unit, note='This delivered item supports the bounded assessment.')
                           for unit in legacy_fixtures.note_units(packet)])
            else:
                from research_harness.review_refusal import evidence_arrays
                output = fixture.case.build_review_output('result')
                for array in evidence_arrays(output):
                    array[:] = [copy.deepcopy(fixture.case.expected['computations'][0]['artifacts'][0])]
            return codex.build_codex_response(route, request, output)

        rows = []
        with patch.object(protocol.transport, 'send_request', side_effect=build_response):
            exit_code = review_series.run_series(fixture.case.store,
                plan['details']['step']['payload']['assignment_id'], emit=rows.append)
        self.assertEqual(exit_code, 0, rows[-1])
        self.assertEqual(rows[-1]['state'], 'done')
        records = fixture.case.read_records()
        self.assertTrue(any(row['payload']['stage'] == 'result' for row in records['value_review'].values()))
        for kind in ('review_assignment', 'review_attempt', 'value_review'):
            for key, row in before['records'].get(kind, {}).items():
                self.assertEqual(records[kind][key], row)

    def test_openness_search_stale_executes_search_before_the_export_handoff(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        from research_harness.literature import record_search
        case = self.prepare(reading_fixtures.OpennessReadingCase)
        reference = case.export(assignment_id='reading')
        turns = case.run_reading_turns(reference, first='reading')
        head = turns[-1]
        case.invoke(head, case.build_native_result(case.read_packet(head)))
        material = ProblemOpennessMinimumTests.search(case, case.versions,
            captured_at='2026-10-17T12:00:00Z')
        records = case.store.snapshot()['records']
        assignment = records['review_assignment'][head]
        from research_harness.openness_minimum import validate_minimum_bindings
        minimum = case.read_packet(head)['native']['scientific_context']['openness_minimum']
        with self.assertRaises(ResearchError) as caught:
            validate_minimum_bindings(minimum, case.selected['statement'], records)
        self.assertEqual(caught.exception.code, 'openness_search_stale')
        # The registry also covers a refusal of a planner read outside prepare_openness.
        with patch.object(review_continuation, '_plan_head', side_effect=caught.exception):
            plan = review_continuation.plan_next(records, case.artifacts, head)
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'search'))
        step = plan['details']['step']
        payload = {key: copy.deepcopy(material[key]) for key in
            ('queries', 'responses', 'captured_at', 'found_work_ids', 'verdict', 'cited_work_ids',
             'impact', 'gaps', 'dispositions', 'question', 'scope')}
        payload.update(step['payload'], id='renewed-search')
        saved = case.mutate(record_search, payload)['result']
        self.assertEqual(saved['id'], 'renewed-search')
        after = review_continuation.plan_next(case.store.snapshot()['records'], case.artifacts, head)
        self.assertEqual((after['state'], after['code'], after['command']),
                         ('handoff', 'openness_review_not_approved', 'export'))
        self.assertEqual(after['details']['refusal']['code'], 'openness_search_stale')
        self.assertEqual(after['details']['step']['payload'], dict(kind='problem-openness'))
        self.assertNotEqual(after, plan)
        self.assertEqual(case.store.snapshot()['records']['review_assignment'][head], assignment)

    def test_openness_minimum_gaps_describes_both_export_choices(self):
        terminal = stop_classes.describe_terminal('openness_minimum_gaps')
        self.assertEqual((terminal['state'], terminal['command']), ('handoff', 'export'))
        row = stop_classes.STOP_REGISTRY['openness_minimum_gaps']
        self.assertEqual(row['owner'], 'D3')
        self.assertEqual(terminal['details']['next']['payload'], dict(kind='problem-openness'))
        self.assertIn('accept_gaps', terminal['details']['next']['chooses'])
        self.assertTrue(terminal['details']['next']['prerequisites'])
        self.assertNotIn('unlisted', terminal['details'])

    def test_gate_override_condition_unmet_describes_lift_or_condition_work(self):
        terminal = stop_classes.describe_terminal('gate_override_condition_unmet')
        self.assertEqual(terminal['state'], 'stop')
        self.assertEqual(stop_classes.STOP_REGISTRY['gate_override_condition_unmet']['owner'], 'D3')
        decision = terminal['details']['decision']
        self.assertTrue(decision['owner_decides'])
        lift, work = decision['then']
        self.assertEqual(lift['steps'][0]['command'], 'gate-override')
        self.assertIn('lifts', lift['steps'][0]['chooses'])
        self.assertEqual(work['steps'], [])
        self.assertTrue(work['prerequisites'])
        self.assertNotIn('unlisted', terminal['details'])

    def test_stored_content_proof_keeps_the_last_line_and_first_record_check_failure(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        case = fixture.case
        assignment = copy.deepcopy(fixture.assignment)
        assignment['packet'] = case.artifacts.put(b'{broken', 'application/json')
        assignment['review_parts'] = dict(adapter='problem_result')

        def write_inconsistent_content(transaction):
            transaction.put('review_assignment', assignment['id'], assignment)
            return assignment

        # The Store accepts the event and authenticates its history. Review
        # commands never write this inconsistent parts binding and packet.
        case.store.mutate('fixture.inconsistent_content', {}, write_inconsistent_content,
                          expected_revision=case.store.revision, request_id='inconsistent-content')
        before = copy.deepcopy(case.store.snapshot()['records']['review_assignment'])
        rows = []
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            self.assertEqual(review_series.run_series(case.store, assignment['id'], emit=rows.append), 3)
        self.assertIn('Output views were not rendered', diagnostics.getvalue())
        terminal = rows[-1]
        self.assertEqual((terminal['head_id'], terminal['state'], terminal['code']),
                         (assignment['id'], 'stop', 'review_input_unreadable'))
        self.assertNotIn('unlisted', terminal['details'])
        self.assertEqual(terminal['details']['decision']['then'][0]['steps'][0]['command'], 'record-check')
        self.assertEqual(stop_classes.STOP_REGISTRY[terminal['code']]['group'], 'stored_state')
        report = record_check.check_record(case.store.root, isolate=False)
        failed = next(row for row in report['steps'] if row['status'] == 'failed')
        self.assertEqual(failed['step'], 'authentication')
        self.assertEqual(failed['error']['code'], 'record_json_invalid')
        self.assertEqual([(row['step'], row['status']) for row in report['steps'][:3]],
                         [('snapshot', 'passed'), ('integrity', 'passed'), ('reconstruction', 'passed')])
        self.assertEqual(case.store.snapshot()['records']['review_assignment'], before)

    def test_pending_output_replans_to_an_accepted_call_and_recording(self):
        self.check_pending_replans(False)

    def test_pending_sources_replan_to_an_accepted_continuation_and_recording(self):
        self.check_pending_replans(True)

    def check_pending_replans(self, sources):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        output = fixture.case.build_review_output('bar')
        if sources:
            output['source_requests'] = [dict(id='inventory', kind='inventory_page', page=0)]
            output['value']['status'] = 'unresolved'
            fixture.invoke(output)
        planned = fixture.plan()
        stale = dict(planned, state='record', command='value-review', payload=dict(output,
            id='premature', assignment_id='bar-first', dossier_id=None))
        original = review_series._read_plan
        pending = [stale]

        def read_plan(store, identifier):
            if pending:
                return store.snapshot(), pending.pop()
            return original(store, identifier)

        rows = []
        with patch.object(review_series, '_read_plan', side_effect=read_plan), \
                patch.object(protocol.transport, 'send_request',
                    return_value=fixture.case.build_provider_response(fixture.case.build_review_output('bar'))):
            self.assertEqual(review_series.run_series(fixture.case.store, 'bar-first', emit=rows.append), 0)
        error = next(row['error'] for row in rows if 'error' in row)
        self.assertEqual(error['code'], 'review_sources_pending' if sources else 'review_output_pending')
        self.assertEqual(rows[-1]['state'], 'done')
        self.assertNotEqual(fixture.plan(), planned)
        self.assertNotIn('review_series_stop', fixture.case.read_records())


if __name__ == '__main__':
    unittest.main()
