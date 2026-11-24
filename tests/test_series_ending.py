"""RR8 endings and receipts over a synthetic retained review chain."""

from contextlib import ExitStack
import copy
from functools import partial
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import automatic_overrides, cli, record_check, review_plan_steps, review_series, stop_classes
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError
import test_review_continuation as fixtures
import test_session_forecast as forecast_fixtures


INJECTED_CODES = frozenset(('rr8_injected_alpha', 'rr8_injected_beta'))
INJECTED_CODE = sorted(INJECTED_CODES)[0]


def read_answers(plan):
    """Read each answer of a decision as the id, command and payload of each of its steps."""
    return [(answer['answer'], [(step.get('id'), step['command'], step.get('payload', {})) for step in answer['steps']])
            for answer in plan['details']['decision']['then']]


class SeriesEndingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case
        self.plan = self.fixture.plan()
        self.snapshot = self.case.store.snapshot()

    def run_series(self, *args, **kwargs):
        from test_series_code_reach import record_reachable_errors
        with record_reachable_errors('run_series', injected=INJECTED_CODES):
            return review_series.run_series(*args, **kwargs)

    def read_current_plans(self, events):
        events = iter(events)
        def read(store, assignment_id):
            event = next(events)
            if isinstance(event, BaseException):
                raise event
            snapshot, plan = event
            return dict(snapshot, revision=store.revision), plan
        return read

    def assert_only_driver_lifetime_changed(self, before, after):
        self.assertEqual(after['revision'], before['revision'] + 2)
        for kind in set(before['records']) | set(after['records']):
            if kind not in ('review_series_run', 'literature_operation'):
                self.assertEqual(after['records'].get(kind), before['records'].get(kind), kind)
        for identifier, record in before['records'].get('literature_operation', {}).items():
            self.assertEqual(after['records']['literature_operation'][identifier], record)
        runs = after['records']['review_series_run']
        previous = before['records'].get('review_series_run', {})
        self.assertEqual(len(runs), len(previous) + 1)
        for identifier, record in previous.items():
            self.assertEqual(runs[identifier], record)
        self.assertTrue(all(record['ended'] is not None for record in runs.values()))

    def build_error(self, code=INJECTED_CODE):
        return ResearchError(code, 'Synthetic refusal.', dict(marker='retained'))

    def run_refusal(self, code=INJECTED_CODE):
        rows = []
        with patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error(code)}):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        return rows[-1]

    def raise_error(self, code=INJECTED_CODE):
        raise self.build_error(code)

    def check_copy(self, **kwargs):
        # Receipt discovery must survive failures of any later reader.
        with patch.object(record_check, 'check_integrity', side_effect=self.build_error()):
            return record_check.check_record(self.case.root, isolate=False, **kwargs)

    def assert_unlisted(self, line, code=INJECTED_CODE):
        self.assertEqual((line['head_id'], line['code'], line['stop_class']),
                         ('bar-first', code, 'decision'))
        self.assertTrue(line['details']['unlisted'])
        self.assertEqual(line['error']['code'], code)
        self.assertEqual(line['details']['decision']['then'][0]['steps'][0]['command'], 'record-check')

    def test_refusal_receipt_survives_without_replaying_the_review_run(self):
        line = self.run_refusal()
        self.assert_unlisted(line)
        self.assertEqual(line['details']['refused_action'], {k: self.plan[k] for k in ('command', 'state', 'payload')})
        report = self.check_copy()
        stop = report['series_stops'][0]
        self.assertEqual((stop['id'], stop['code'], stop['finding']),
                         (line['stop_id'], INJECTED_CODE, 'record_unlisted_stop'))
        self.assertFalse(report['ready'])
        self.assertEqual(stop['refused_action'], line['details']['refused_action'])
        with patch.dict(stop_classes.STOP_REGISTRY, {INJECTED_CODE: dict(code=INJECTED_CODE,
                **{'class': 'decision'}, owner='test', next=dict(kind='done'))}):
            stop = self.check_copy()['series_stops'][0]
        self.assertTrue(stop['listed'])
        self.assertNotIn('finding', stop)

    def test_wait_second_read_refusal_ends_the_series(self):
        waiting = dict(self.plan, state='wait', not_before='2000-01-01T00:00:00Z')
        rows = []
        with patch.object(review_series, '_read_plan', side_effect=[(self.snapshot, waiting), self.build_error()]):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        self.assert_unlisted(rows[-1])
        self.assertNotIn('not_before', review_series._recover_ending_line(rows[-1]))

    def test_terminal_completion_failure_keeps_original_refusal(self):
        with patch.object(review_series, '_read_plan', return_value=(self.snapshot, self.plan)), \
                patch('research_harness.review_plan_steps.complete_plan', side_effect=self.build_error()):
            line = self.run_refusal()
        self.assert_unlisted(line)
        self.assertEqual(line['details']['omitted']['step']['code'], INJECTED_CODE)

    def test_stale_route_completion_failure_keeps_route_handoff(self):
        rows = []
        with patch.object(review_series, '_read_plan', return_value=(self.snapshot, self.plan)), \
                patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error('review_route_runtime_stale')}), \
                patch('research_harness.review_plan_steps.complete_plan', side_effect=self.build_error()):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        line = rows[-1]
        self.assertEqual((line['code'], line['command']), ('review_route_runtime_stale', 'record-check'))
        self.assertEqual(line['details']['omitted']['step']['code'], INJECTED_CODE)
        self.assertEqual(line['error']['code'], 'review_route_runtime_stale')

    def test_capacity_and_usage_and_views_failures_are_named_omissions(self):
        for target, member in [('research_harness.review_provider_usage.describe_usage', 'provider_usage'),
                ('research_harness.review_route_capacity.describe_assignment_warning', 'capacity_warning'),
                ('research_harness.review_series.render_workspace_views', 'views')]:
            with self.subTest(member=member), patch(target, side_effect=self.build_error()):
                line = self.run_refusal()
            self.assert_unlisted(line)
            self.assertEqual(line['details']['omitted'][member]['code'], INJECTED_CODE)

    def test_capacity_forecast_failure_is_an_omission(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['records']['review_assignment']['bar-first']['capacity_plan_kind'] = 'synthetic'
        rows = []
        with patch.object(review_series, '_read_plan', return_value=(snapshot, self.plan)), \
                patch.object(self.case.store, 'snapshot', return_value=snapshot), \
                patch('research_harness.review_route_capacity.describe_assignment_capacity', side_effect=self.build_error()), \
                patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error()}):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        self.assert_unlisted(rows[-1])
        self.assertEqual(rows[-1]['details']['omitted']['capacity_forecast']['code'], INJECTED_CODE)




    def test_packet_and_request_read_failures_still_retain_the_stop(self):
        for target, member, code in [('research_harness.automatic_overrides._scope', 'scope', INJECTED_CODE),
                ('research_harness.automatic_overrides._describe_plan_violation_proof', 'retry',
                 'review_route_capacity_plan_violated')]:
            with self.subTest(member=member), patch(target, side_effect=self.build_error()):
                line = self.run_refusal(code)
            stop = self.case.read_records()['review_series_stop'][line['stop_id']]
            self.assertIsNone(stop[member])
            self.assertEqual(stop['read_errors'][member]['code'], INJECTED_CODE)
            with self.assertRaises(ResearchError) as caught:
                automatic_overrides._trusted_stop(self.case.store, self.case.read_records(), stop['id'])
            self.assertEqual(caught.exception.code, 'gate_override_stop_untrusted')

    def test_listed_code_keeps_row_step_and_refused_action(self):
        with patch.object(review_series, '_read_plan', return_value=(self.snapshot, self.plan)), \
                patch('research_harness.review_plan_steps.complete_plan', side_effect=self.build_error()):
            line = self.run_refusal('artifact_missing')
        self.assertNotIn('unlisted', line['details'])
        self.assertEqual(line['details']['refused_action']['payload'], self.plan['payload'])
        self.assertEqual(line['details']['decision']['owner_decides'],
                         stop_classes.describe_terminal('artifact_missing')['details']['decision']['owner_decides'])


    def test_latest_per_assignment_and_all_older_unlisted_stops_are_reported(self):
        first = self.run_refusal()
        second = self.run_refusal('artifact_missing')
        third = self.run_refusal('review_budget_exhausted')
        stops = self.check_copy()['series_stops']
        self.assertEqual([row['id'] for row in stops], [first['stop_id'], third['stop_id']])
        self.assertNotIn(second['stop_id'], [row['id'] for row in stops])

    def test_unlisted_receipt_blocks_ready_even_when_all_steps_pass(self):
        line = self.run_refusal()
        with ExitStack() as stack:
            for name in ('integrity', 'reconstruction', 'authentication', 'continuation'):
                stack.enter_context(patch.object(record_check, 'check_' + name, return_value={}))
            stack.enter_context(patch.object(record_check, 'check_report', return_value=dict(ready=True)))
            report = record_check.check_record(self.case.root, isolate=False)
        self.assertTrue(all(row['status'] == 'passed' for row in report['steps']))
        self.assertFalse(report['ready'])
        self.assertEqual(report['series_stops'][0]['id'], line['stop_id'])

    def test_missing_packet_keeps_a_null_scope_receipt_and_refuses_auto_pass(self):
        records = self.case.read_records()
        assignment = dict(records['review_assignment']['bar-first'], role='result')
        study = dict(records.get('workspace', {}).get('study', {}), stage='experiment')
        self.case.store.mutate('fixture.result', {}, lambda tx: (
            tx.put('review_assignment', assignment['id'], assignment), tx.put('workspace', 'study', study)),
            expected_revision=self.case.store.revision, request_id='result-fixture')
        path = self.case.root / assignment['packet']['path']
        path.unlink()
        # The driver already holds a selected action when its packet disappears.
        with patch.object(review_series, '_read_plan', return_value=(self.case.store.snapshot(), self.plan)), \
                patch.object(automatic_overrides, '_describe_plan_violation_proof', side_effect=self.build_error()):
            line = self.run_refusal('review_route_capacity_plan_violated')
        stop = self.case.read_records()['review_series_stop'][line['stop_id']]
        self.assertIsNone(stop['scope'])
        self.assertEqual(stop['read_errors']['scope']['code'], 'artifact_missing')
        with self.assertRaises(ResearchError) as caught:
            cli.OPERATIONS['gate-auto-pass'](self.case.store, dict(stop_id=stop['id']),
                expected_revision=self.case.store.revision, request_id='auto-pass')
        self.assertEqual(caught.exception.code, 'gate_override_stop_untrusted')

    def test_legacy_stop_receipt_keeps_its_bytes_and_renders_after_listing(self):
        from research_harness.evidence import digest
        from research_harness.output_views import render_workspace_views
        plan = dict(state='stop', code='artifact_missing', head_id='bar-first', progress={}, details={})
        legacy = dict(id='legacy-stop', origin='harness', code=plan['code'], assignment_id='bar-first',
                      plan=plan, retry=None, scope=None, request_id='legacy-stop',
                      recorded_revision=self.case.store.revision + 1)
        self.case.store.mutate('review.series_stop', dict(stop=legacy), lambda tx: (
            tx.put('review_series_stop', legacy['id'], legacy)),
            expected_revision=self.case.store.revision, request_id='legacy-stop')
        before = self.case.store.snapshot()
        receipt = self.case.store.committed_request('legacy-stop')
        render_workspace_views(self.case.root)
        views = {str(p.relative_to(self.case.root)): p.read_bytes() for p in self.case.root.rglob('*.md')}
        report = self.check_copy()
        render_workspace_views(self.case.root)
        self.assertEqual(report['series_stops'][0]['refused_action'], None)
        self.assertEqual(self.case.store.snapshot(), before)
        self.assertEqual(digest(self.case.read_records()['review_series_stop']['legacy-stop']), digest(legacy))
        self.assertEqual(self.case.store.committed_request('legacy-stop'), receipt)
        self.assertEqual({str(p.relative_to(self.case.root)): p.read_bytes() for p in self.case.root.rglob('*.md')}, views)





    def test_reading_receipt_calls_are_independent_diagnostic_failures(self):
        targets = ('_read_json', 'build_packet', 'build_assignment_request', 'describe_reading_receipt')
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['records']['review_assignment']['bar-first']['reading_chain'] = True
        from research_harness import review_protocol, openness_reading
        for target in targets:
            with self.subTest(target=target), ExitStack() as stack:
                stack.enter_context(patch.object(review_series, '_read_plan', return_value=(snapshot, self.plan)))
                stack.enter_context(patch.object(self.case.store, 'snapshot', return_value=snapshot))
                stack.enter_context(patch('research_harness.review_route_capacity.describe_assignment_warning', return_value=None))
                for name in targets:
                    module = openness_reading if name == 'describe_reading_receipt' else review_protocol
                    stack.enter_context(patch.object(module, name, side_effect=self.build_error() if name == target else None,
                        return_value=dict(capacity_forecast={}, capacity_warning={})))
                line = self.run_refusal()
            self.assert_unlisted(line)
            for member in ('capacity_forecast', 'capacity_warning'):
                self.assertEqual(line['details']['omitted'][member]['code'], INJECTED_CODE)

    def run_ending(self, plan, *, operation_error=None):
        rows = []
        with patch.object(review_series, '_read_plan', return_value=(self.snapshot, plan)):
            if operation_error is None:
                result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
            else:
                with patch.dict(cli.OPERATIONS, {plan['command']: lambda *a, **k: self.raise_error(operation_error)}):
                    result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        return result, rows[-1]

    def test_every_ending_state_is_retained_once_at_the_same_progress(self):
        kinds = [('stop', INJECTED_CODE, 3), ('handoff', 'review_source_no_progress', 3),
                 ('wait', 'review_capacity_wait', 4), ('done', 'problem_already_posted', 0)]
        for state, code, expected in kinds:
            with self.subTest(state=state):
                plan = dict(self.plan, state=state, code=code, details={})
                if state == 'wait':
                    plan['not_before'] = '2099-01-01T00:00:00Z'
                first_result, first = self.run_ending(plan)
                before_repeat = self.case.store.snapshot()
                second_result, second = self.run_ending(plan)
                self.assertEqual((first_result, second_result), (expected, expected))
                self.assertEqual(first['state'], state)
                self.assertEqual(first['details']['receipt']['place'], 'store')
                self.assertEqual(first['stop_id'], second['stop_id'])
                self.assert_only_driver_lifetime_changed(before_repeat, self.case.store.snapshot())
                retained = self.case.read_records()['review_series_stop'][first['stop_id']]
                self.assertEqual(retained['plan']['state'], state)

    def test_operation_handoff_wait_and_done_keep_their_exit_codes_and_actions(self):
        for code, state, expected in [('review_source_no_progress', 'handoff', 3),
                ('review_capacity_wait', 'wait', 4), ('problem_already_posted', 'done', 0)]:
            with self.subTest(code=code):
                result, line = self.run_ending(self.plan, operation_error=code)
                self.assertEqual((result, line['state'], line['code']), (expected, state, code))
                self.assertEqual(line['details']['refused_action']['payload'], self.plan['payload'])
                self.assertIn('stop_id', line)

    def test_store_failure_retains_a_file_and_record_check_reads_it_every_time(self):
        before = self.case.store.snapshot()
        with patch.object(automatic_overrides, 'record_series_stop', side_effect=self.build_error()):
            line = self.run_refusal()
        self.assertEqual(line['stop_record_error']['code'], INJECTED_CODE)
        receipt = line['details']['receipt']
        self.assertEqual(receipt['place'], 'workspace')
        saved = json.loads((self.case.root / receipt['path']).read_text())
        self.assertEqual(saved['code'], INJECTED_CODE)
        for unused in range(2):
            report = self.check_copy()
            self.assertEqual(report['series_stops'][0]['place'], 'workspace')
            self.assertEqual(report['series_stops'][0]['finding'], 'record_unlisted_stop')
        self.assert_only_driver_lifetime_changed(before, self.case.store.snapshot())
        with patch.dict(stop_classes.STOP_REGISTRY, {INJECTED_CODE: dict(code=INJECTED_CODE,
                **{'class': 'decision'}, owner='test', next=dict(kind='done'))}):
            self.assertNotIn('finding', self.check_copy()['series_stops'][0])

    def test_two_unretained_refusals_in_the_same_store_stay_distinct(self):
        with patch.object(automatic_overrides, 'record_series_stop', side_effect=self.build_error()):
            lines = [self.run_refusal(code) for code in sorted(INJECTED_CODES)]
        stops = self.check_copy()['series_stops']
        self.assertEqual({row['code'] for row in stops}, INJECTED_CODES)
        self.assertEqual({row['path'] for row in stops}, {line['details']['receipt']['path'] for line in lines})

    def test_workspace_receipt_is_reported_when_snapshot_fails(self):
        with patch.object(automatic_overrides, 'record_series_stop', side_effect=self.build_error()):
            line = self.run_refusal()
        with patch.object(record_check, 'check_snapshot', side_effect=self.build_error()):
            report = record_check.check_record(self.case.root, isolate=False)
        self.assertEqual(report['steps'][0]['status'], 'failed')
        self.assertEqual(report['series_stops'][0]['code'], line['code'])
        self.assertEqual(report['series_stops'][0]['finding'], 'record_unlisted_stop')

    def test_both_places_and_states_are_reported_with_the_same_members(self):
        first = self.run_refusal()
        with patch.object(automatic_overrides, 'record_series_stop', side_effect=self.build_error()):
            second = self.run_refusal(sorted(INJECTED_CODES)[1])
        stops = self.check_copy()['series_stops']
        self.assertEqual({row['place'] for row in stops}, {'store', 'workspace'})
        self.assertEqual(set(stops[0]), set(stops[1]))
        self.assertEqual({row['code'] for row in stops}, INJECTED_CODES)

    def test_no_receipt_keeps_the_code_and_names_both_errors_and_the_fallback(self):
        with patch.object(automatic_overrides, 'record_series_stop', side_effect=self.build_error()), \
                patch.object(review_series, '_write_ending_file', side_effect=OSError('Synthetic file failure.')):
            line = self.run_refusal()
        self.assert_unlisted(line)
        self.assertIsNone(line['details']['receipt']['place'])
        self.assertEqual(set(line['details']['receipt']['errors']), {'store', 'workspace'})
        self.assertEqual((line['state'], line['command']), ('handoff', 'record-check'))

    def execute_check_step(self, step):
        argv = [step['command']]
        for key, value in step['arguments'].items():
            argv.extend([key, value])
        with patch.object(record_check, 'check_integrity', side_effect=self.build_error()):
            return cli.run(cli.build_parser().parse_args(argv))

    def test_unknown_stop_step_runs_exactly_as_written(self):
        line = self.run_refusal()
        report = self.execute_check_step(line['details']['step'])
        self.assertEqual(report['series_stops'][0]['code'], INJECTED_CODE)
        self.assertEqual(line['details']['step']['chooses'], [])
        self.assertEqual(line['details']['step']['prerequisites'], [])
        native = dict(state='handoff', head_id='bar-first', details=dict(step=dict(
            command='search review-packet', root=str(self.case.root / 'controller'))))
        review_series._complete_ending_step(self.case.store, native)
        self.assertEqual(native['details']['step']['arguments'], {'--attack-root': str(self.case.root / 'controller')})

    def test_stop_step_that_names_another_chain_runs_that_chain(self):
        """Review M-F2: a review-series step of a stop that names another chain in payload.assignment_id runs that
        chain, in details.step and in its answer; a review-series step without one runs the head."""
        step = dict(command='review-series', payload=dict(assignment_id='successor'), chooses=[])
        row = dict(state='stop', head_id='bar-first', details=dict(step=copy.deepcopy(step), decision=dict(
            owner_decides='Which chain goes on?', then=[dict(answer='Go on', steps=[
                copy.deepcopy(step), dict(command='review-series')])])))
        review_series._complete_ending_step(self.case.store, row)
        self.assertEqual(row['details']['step']['arguments']['--assignment'], 'successor')
        named, head = row['details']['decision']['then'][0]['steps']
        self.assertEqual((named['arguments']['--assignment'], head['arguments']['--assignment']),
                         ('successor', 'bar-first'))

    def test_failed_step_reading_uses_executable_fallback_and_then_resumes_the_head(self):
        with patch.object(review_series, '_read_plan', return_value=(self.snapshot, self.plan)), \
                patch('research_harness.review_plan_steps.complete_plan', side_effect=self.build_error()):
            line = self.run_refusal('review_route_runtime_stale')
        self.assertEqual(line['code'], 'review_route_runtime_stale')
        self.assertEqual(line['details']['intended_step']['command'], 'review-route')
        self.execute_check_step(line['details']['step'])
        then = line['details']['step']['then'][0]
        argv = [then['command']]
        for key, value in then['arguments'].items():
            argv.extend([key, value])
        output = io.StringIO()
        with patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error('review_route_runtime_stale')}), \
                patch('sys.stdout', output):
            result = cli.run(cli.build_parser().parse_args(argv))
        self.assertEqual(result, 3)
        resumed = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(resumed['details']['step']['command'], 'review-context')
        self.assertTrue(resumed['details']['step']['payload'])

    def build_defect_stop(self):
        def unchanged(store, payload, **identity):
            return store.mutate('fixture.unchanged', {}, lambda tx: None, **identity)
        rows = []
        with patch.dict(cli.OPERATIONS, {'review-run': unchanged}):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        return rows[-1]

    def test_harness_defect_without_store_record_uses_fallback_then_recovers_its_step(self):
        with patch.object(automatic_overrides, 'record_series_stop', side_effect=self.build_error()):
            line = self.build_defect_stop()
        self.assertEqual((line['code'], line['state'], line['command']),
                         ('review_series_no_progress', 'handoff', 'record-check'))
        self.assertEqual(line['details']['intended_step']['command'], 'gate-auto-pass')
        self.assertEqual(line['details']['receipt']['place'], 'workspace')
        self.execute_check_step(line['details']['step'])
        line = self.build_defect_stop()
        self.assertEqual(line['details']['step']['command'], 'gate-auto-pass')
        self.assertEqual(line['details']['step']['payload'], dict(stop_id=line['stop_id']))

    def test_failed_scope_reading_keeps_the_receipt_and_uses_the_fallback(self):
        with patch.object(automatic_overrides, '_scope', side_effect=self.build_error()):
            line = self.build_defect_stop()
        self.assertEqual((line['state'], line['command']), ('handoff', 'record-check'))
        self.assertEqual(line['details']['receipt']['place'], 'store')
        self.assertEqual(line['details']['intended_step']['command'], 'gate-auto-pass')
        with self.assertRaises(ResearchError) as caught:
            automatic_overrides._trusted_stop(self.case.store, self.case.read_records(), line['stop_id'])
        self.assertEqual(caught.exception.code, 'gate_override_stop_untrusted')

    def test_failed_post_refusal_snapshot_never_replaces_the_refused_operation(self):
        original = self.case.store.snapshot
        refused, failed_read = [], []
        def refuse(*args, **kwargs):
            refused.append(True)
            raise self.build_error()
        def read():
            if refused and not failed_read:
                failed_read.append(True)
                raise self.build_error(sorted(INJECTED_CODES)[1])
            return original()
        rows = []
        with patch.object(self.case.store, 'snapshot', side_effect=read), \
                patch('research_harness.review_stop.read_driver_boundary', return_value=None), \
                patch.dict(cli.OPERATIONS, {'review-run': refuse}):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        line = rows[-1]
        self.assertEqual(line['code'], INJECTED_CODE)
        self.assertEqual(line['details']['omitted']['snapshot']['code'], sorted(INJECTED_CODES)[1])

    def test_printing_failure_preserves_the_mandatory_members(self):
        calls = []
        def output(row):
            calls.append(copy.deepcopy(row))
            if len(calls) == 1:
                raise RuntimeError('Synthetic output failure.')
        with patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error()}), patch('sys.stdout', io.StringIO()):
            result = self.run_series(self.case.store, 'bar-first', emit=output)
        self.assertEqual(result, 3)
        recovered = calls[-1]
        self.assertEqual((recovered['state'], recovered['code']), ('stop', INJECTED_CODE))
        self.assertEqual(recovered['details']['omitted']['output']['code'], 'RuntimeError')
        expected = review_series._recover_ending_line(calls[0])
        expected['details'].setdefault('omitted', {})['output'] = recovered['details']['omitted']['output']
        self.assertEqual(recovered, expected)
        self.assertEqual(recovered['details']['refused_action'], calls[0]['details']['refused_action'])
        self.assertIn(recovered['details']['receipt']['id'], self.case.read_records()['review_series_stop'])

    def test_second_printing_failure_is_a_crash_after_one_receipt(self):
        with patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error()}):
            with self.assertRaises(ResearchError):
                self.run_series(self.case.store, 'bar-first', emit=lambda row: self.raise_error())
        stops = self.case.read_records()['review_series_stop']
        self.assertEqual(len(stops), 1)
        self.assertEqual(next(iter(stops.values()))['plan']['details']['receipt']['place'], 'store')

    def test_non_research_exceptions_in_later_phases_keep_the_original_code(self):
        for error_type in (RuntimeError, KeyError, TypeError, OSError, SystemExit):
            for target, member in [('research_harness.review_plan_steps.complete_plan', 'step'),
                    ('research_harness.review_provider_usage.describe_usage', 'provider_usage'),
                    ('research_harness.review_route_capacity.describe_assignment_warning', 'capacity_warning'),
                    ('research_harness.review_series.render_workspace_views', 'views')]:
                with self.subTest(error=error_type.__name__, target=target), \
                        patch.object(review_series, '_read_plan', return_value=(self.snapshot, self.plan)), \
                        patch(target, side_effect=error_type('Synthetic diagnostic failure.')):
                    line = self.run_refusal()
                self.assertEqual(line['code'], INJECTED_CODE)
                self.assertEqual(line['details']['omitted'][member]['code'], error_type.__name__)

    def test_corrupt_or_linked_receipt_is_a_finding_and_is_never_followed(self):
        directory = self.case.root / review_series.ENDING_RECEIPTS_DIRECTORY
        directory.mkdir()
        bad = directory / 'bad.json'
        bad.write_text('{invalid')
        report = self.check_copy()
        self.assertEqual(report['series_stops'][0]['finding'], 'record_json_invalid')
        self.assertFalse(report['ready'])
        bad.unlink()
        bad.symlink_to(self.case.root / '.exactory/research.sqlite3')
        report = self.check_copy()
        self.assertEqual(report['series_stops'][0]['error']['code'], 'unsafe_path')

    def test_first_plan_read_and_sleep_are_phase_one_refusals(self):
        for target in ('_read_plan', 'time.sleep'):
            rows = []
            with self.subTest(target=target), ExitStack() as stack:
                if target == '_read_plan':
                    stack.enter_context(patch.object(review_series, '_read_plan', side_effect=self.build_error()))
                else:
                    from datetime import datetime, timedelta, timezone
                    waiting = dict(self.plan, state='wait', not_before=(
                        datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat())
                    stack.enter_context(patch.object(review_series, '_read_plan', return_value=(self.snapshot, waiting)))
                    stack.enter_context(patch.object(review_series.time, 'sleep', side_effect=self.build_error()))
                result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
            self.assertEqual(result, 3)
            self.assert_unlisted(rows[-1])

    def test_stale_route_handoff_read_failure_uses_the_fallback(self):
        with patch.object(review_series, 'plan_route_handoff', side_effect=RuntimeError('Synthetic handoff failure.')):
            line = self.run_refusal('review_route_runtime_stale')
        self.assertEqual(line['code'], 'review_route_runtime_stale')
        self.assertEqual(line['details']['omitted']['step']['code'], 'RuntimeError')
        self.assertEqual(line['details']['step']['command'], 'record-check')
        self.execute_check_step(line['details']['step'])

    def test_ending_snapshot_failure_does_not_change_the_refusal(self):
        rows = []
        with patch.object(self.case.store, 'snapshot', side_effect=RuntimeError('Synthetic snapshot failure.')):
            result = review_series._end_series(self.case.store, self.plan, error=self.build_error(),
                exit_code=3, output=rows.append, capacity_records=self.snapshot['records'])
        self.assertEqual(result, 3)
        self.assert_unlisted(rows[-1])
        self.assertEqual(rows[-1]['details']['omitted']['snapshot']['code'], 'RuntimeError')
        self.assertEqual(rows[-1]['details']['receipt']['place'], 'workspace')

    def test_renderers_retained_failure_marker_is_in_the_line(self):
        with patch('research_harness.output_views.compute_view_model', side_effect=self.build_error()), \
                patch('sys.stderr', io.StringIO()):
            line = self.run_refusal()
        self.assert_unlisted(line)
        self.assertEqual(line['details']['omitted']['views']['error'], INJECTED_CODE)

    def test_non_research_receipt_failures_try_the_workspace(self):
        for error_type in (TypeError, RuntimeError, SystemExit):
            with self.subTest(error=error_type.__name__), \
                    patch.object(automatic_overrides, 'record_series_stop', side_effect=error_type('Synthetic record failure.')):
                line = self.run_refusal()
            self.assertEqual(line['code'], INJECTED_CODE)
            self.assertEqual(line['stop_record_error']['code'], 'stop_record_error')
            self.assertEqual(line['details']['omitted']['stop_record']['code'], error_type.__name__)
            self.assertEqual(line['details']['receipt']['place'], 'workspace')

    def test_non_stop_harness_defect_receipts_never_authorize_an_automatic_pass(self):
        line = self.build_defect_stop()
        line = dict(line, state='handoff')
        retained = automatic_overrides.record_series_stop(self.case.store, line)
        with self.assertRaises(ResearchError) as caught:
            automatic_overrides._trusted_stop(self.case.store, self.case.read_records(), retained['id'])
        self.assertEqual(caught.exception.code, 'gate_override_stop_class')

    def test_phase_call_inventory_covers_the_driver_and_ending(self):
        import ast
        import collections
        import inspect
        # Package calls include memory-only assembly and ResearchError.as_dict;
        # only standard-library functions and built-in value methods are omitted.
        standard = {'copy.deepcopy', 'datetime.fromisoformat', 'datetime.now', 'uuid.uuid4', 'json.dumps',
                    'ValueError', 'dict', 'isinstance', 'type', 'str', 'range', 'max', 'time.time_ns', 'timedelta'}
        value_methods = {'get', 'setdefault', 'pop', 'update', 'replace', 'total_seconds', 'items',
                         'encode', 'isoformat', 'startswith', 'append'}
        cases = {
            '_emit_json': {'print': 1},
            '_identify_action': {'digest': 1},
            '_describe_stop': {'describe_terminal': 1},
            '_record_omission': {'error.as_dict': 1},
            '_render_views': {'render_workspace_views': 1, '_read_marker': 1, '_record_omission': 1},
            '_read_plan': {'store.snapshot': 1, 'Evaluation': 1, 'plan_next': 1, 'ArtifactStore': 1,
                'describe_usage': 1, '_record_omission': 1},
            '_enrich_line': {'Evaluation': 1, 'describe_usage': 1, 'describe_assignment_warning': 1,
                'store.snapshot': 1, 'ArtifactStore': 1, 'describe_assignment_capacity': 1,
                'describe_session_forecast': 1, 'store.build_artifact_store': 4, 'review_protocol._read_json': 1,
                'review_protocol.build_packet': 1, 'review_protocol.build_assignment_request': 1,
                'openness_reading.describe_reading_receipt': 1, 'read_position_context': 1,
                'openness_reading.describe_unavailable_forecast': 1, '_record_omission': 6, 'classify_stop': 1},
            '_build_check_step': {},
            '_use_fallback_step': {'_build_check_step': 1},
            '_complete_ending_step': {},
            '_bind_ending_receipt': {},
            '_identify_ending': {'_identify_action': 1},
            '_recover_ending_line': {},
            '_write_ending_file': {'checked_parent': 1, 'write_atomic': 1},
            '_end_series': {'_enrich_line': 1, '_render_views': 1, 'output': 2, '_recover_ending_line': 1, 'print': 1, 'store.snapshot': 1,
                'complete_plan': 1, 'plan_route_handoff': 1, 'Evaluation': 1, 'record_series_stop': 1,
                '_write_ending_file': 1, 'ArtifactStore': 1, '_describe_stop': 2, 'classify_stop': 1,
                '_bind_ending_receipt': 1,
                '_complete_ending_step': 3, '_use_fallback_step': 6, '_record_omission': 8, 'end_driver': 1,
                'error.as_dict': 1, 'recording_error.as_dict': 1},
            '_describe_requested_ending': {'_describe_stop': 1, 'record_signal_boundary': 1, 'settle_stop': 1,
                'store.snapshot': 1, 'describe_driver_stop': 1, '_record_omission': 1,
                'error.as_dict': 1, 'error.describe_error': 1},
            '_run_series': {'_end_series': 1, '_render_views': 1, 'emit': 1, '_enrich_line': 1,
                'emit_progress': 8, '_ProgressOutputFailure': 1, "OPERATIONS[plan['command']]": 1, '_read_plan': 3,
                'time.sleep': 2, 'store.snapshot': 1, '_identify_action': 3, 'digest': 1, 'ResearchError': 1,
                '_record_omission': 5, 'error.as_dict': 3, 'read_error.as_dict': 1,
                'read_polling_store': 2, 'read_boundary': 4, 'read_driver_boundary': 2, 'settle_stop': 1, 'min': 1,
                '_describe_requested_ending': 1},
            'run_series': {'start_driver': 1, '_end_owned_processes': 1, 'ReviewStopSignal': 2,
                'threading.current_thread': 1, 'threading.main_thread': 1, '_run_series': 1, 'signal.signal': 3,
                'request_stop': 1, 'store.snapshot': 1, '_select_stop_calls': 1, '_describe_requested_ending': 1,
                '_end_series': 2, 'end_driver': 1, 'signal.Signals': 1}}
        module = ast.parse(inspect.getsource(review_series))
        self.assertEqual(set(cases), {node.name for node in module.body if isinstance(node, ast.FunctionDef)})
        for name, expected in cases.items():
            tree = ast.parse(inspect.getsource(getattr(review_series, name)))
            actual = collections.Counter()
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                if isinstance(call.func, ast.Attribute) and call.func.attr in value_methods:
                    continue
                callee = ast.unparse(call.func)
                if callee not in standard:
                    actual[callee] += 1
            self.assertEqual(dict(actual), expected, name)
        tree = ast.parse(inspect.getsource(review_series._run_series))
        outer = tree.body[0]
        class LoopReturns(ast.NodeVisitor):
            def __init__(self):
                self.returns = []
            def visit_FunctionDef(self, node):
                if node is outer:
                    self.generic_visit(node)
            def visit_Return(self, node):
                self.returns.append(node)
        visitor = LoopReturns()
        visitor.visit(outer)
        returns = visitor.returns
        self.assertEqual(len(returns), 1)
        self.assertEqual(ast.unparse(returns[0].value.func), '_end_series')
        self.assert_unlisted(self.run_refusal())

    def test_each_plan_read_dependency_preserves_a_phase_one_refusal(self):
        targets = [('research_harness.review_series', 'plan_next'),
                   ('research_harness.review_series', 'ArtifactStore'),
                   ('research_harness.evaluation', 'Evaluation')]
        import importlib
        for module_name, name in targets:
            with self.subTest(target=name):
                module = importlib.import_module(module_name)
                original = getattr(module, name)
                if name == 'Evaluation':
                    owner, attribute = original, '__init__'
                    original = original.__init__
                else:
                    owner, attribute = module, name
                injected = []
                def fail_once(*args, **kwargs):
                    if not injected:
                        injected.append(True)
                        raise self.build_error()
                    return original(*args, **kwargs)
                with patch.object(owner, attribute, fail_once):
                    line = self.run_refusal()
                self.assert_unlisted(line)
                self.assertEqual(line['details']['receipt']['place'], 'store')

    def test_each_artifact_store_call_in_diagnostics_is_a_named_omission(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['records']['review_assignment']['bar-first'].update(capacity_plan_kind='synthetic', reading_chain=True)
        from research_harness import review_protocol, openness_reading, review_route_capacity
        for ordinal, member in [(1, 'capacity_forecast'), (2, 'capacity_warning'), (3, 'capacity_forecast')]:
            with self.subTest(ordinal=ordinal), ExitStack() as stack:
                original = self.case.store.build_artifact_store
                calls = []
                def build():
                    calls.append(True)
                    if len(calls) == ordinal:
                        raise RuntimeError('Synthetic artifact reader failure.')
                    return original()
                stack.enter_context(patch.object(self.case.store, 'build_artifact_store', build))
                stack.enter_context(patch('research_harness.review_provider_usage.describe_usage', return_value={}))
                stack.enter_context(patch.object(review_route_capacity, 'describe_assignment_capacity',
                                                return_value=dict(capacity_forecast={})))
                stack.enter_context(patch.object(review_route_capacity, 'describe_assignment_warning', return_value=None))
                for target in ('_read_json', 'build_packet', 'build_assignment_request'):
                    stack.enter_context(patch.object(review_protocol, target, return_value={}))
                stack.enter_context(patch.object(openness_reading, 'describe_reading_receipt',
                                                return_value=dict(capacity_forecast={}, capacity_warning={})))
                rows = []
                review_series._end_series(self.case.store, self.plan, error=self.build_error(), exit_code=3,
                    output=rows.append, current=snapshot)
            self.assertEqual(rows[-1]['code'], INJECTED_CODE)
            self.assertEqual(rows[-1]['details']['omitted'][member]['code'], 'RuntimeError')

    def test_file_publication_sites_and_marker_reads_keep_the_code(self):
        for target in ('research_harness.workspace.checked_parent', 'research_harness.native_writes.write_atomic'):
            with self.subTest(target=target), \
                    patch.object(automatic_overrides, 'record_series_stop', side_effect=self.build_error()), \
                    patch(target, side_effect=RuntimeError('Synthetic publication failure.')):
                line = self.run_refusal()
            self.assertEqual(line['code'], INJECTED_CODE)
            self.assertIsNone(line['details']['receipt']['place'])
        with patch.object(review_series, 'render_workspace_views', return_value=None), \
                patch('research_harness.output_views._read_marker', side_effect=RuntimeError('Synthetic marker failure.')):
            line = self.run_refusal()
        self.assertEqual(line['code'], INJECTED_CODE)
        self.assertEqual(line['details']['omitted']['views']['code'], 'RuntimeError')

    def test_wait_step_runs_as_written_and_done_has_no_step(self):
        waiting = dict(self.plan, state='wait', code='review_capacity_wait',
                       not_before='2099-01-01T00:00:00Z', details={})
        result, line = self.run_ending(waiting)
        self.assertEqual(result, 4)
        step = line['details']['step']
        self.assertEqual(step['command'], 'review-series')
        self.assertEqual(step['prerequisites'], [dict(not_before=waiting['not_before'])])
        argv = [step['command']]
        for key, value in step['arguments'].items():
            argv.extend([key, value])
        with patch.object(review_series, '_read_plan', return_value=(self.snapshot, waiting)), \
                patch('sys.stdout', io.StringIO()):
            self.assertEqual(cli.run(cli.build_parser().parse_args(argv)), 4)
        result, done = self.run_ending(dict(self.plan, state='done', code='problem_already_posted', details={}))
        self.assertEqual((result, done['state']), (0, 'done'))
        self.assertNotIn('step', done['details'])
        self.assertNotIn('decision', done['details'])
        self.assertIn('stop_id', done)

    def test_run_and_resume_at_one_progress_keep_distinct_receipts(self):
        first = self.run_refusal()
        self.assertEqual(first['details']['receipt']['place'], 'store')
        result, second = self.run_ending(dict(self.plan, state='resume'), operation_error=INJECTED_CODE)
        self.assertEqual(result, 3)
        self.assertNotEqual(first['stop_id'], second['stop_id'])
        stops = self.case.read_records()['review_series_stop']
        self.assertEqual(stops[first['stop_id']]['plan']['details']['refused_action']['state'], 'run')
        self.assertEqual(stops[second['stop_id']]['plan']['details']['refused_action']['state'], 'resume')

    def test_only_the_latest_clean_ending_can_be_reused(self):
        first = self.run_refusal()
        self.run_refusal(sorted(INJECTED_CODES)[1])
        third = self.run_refusal()
        self.assertNotEqual(first['stop_id'], third['stop_id'])
        self.assertEqual(self.run_refusal()['stop_id'], third['stop_id'])
        self.assertEqual(len(self.case.read_records()['review_series_stop']), 3)

    def test_action_identity_ignores_fresh_ids_but_preserves_resume_ids(self):
        for state in ('run', 'resume'):
            with self.subTest(state=state):
                plans = [dict(self.plan, state=state, payload=dict(self.plan['payload'], id=value))
                         for value in ('fresh-one', 'fresh-two')]
                ids = [self.run_ending(plan, operation_error=INJECTED_CODE)[1]['stop_id'] for plan in plans]
                self.assertEqual(ids[0] == ids[1], state == 'run')

    def test_row_step_and_fallback_at_one_progress_get_distinct_receipts(self):
        first = self.run_refusal('review_route_runtime_stale')
        with patch.object(review_series, '_read_plan', return_value=(self.snapshot, self.plan)), \
                patch('research_harness.review_plan_steps.complete_plan', side_effect=self.build_error()):
            second = self.run_refusal('review_route_runtime_stale')
        self.assertEqual((first['state'], second['state']), ('handoff', 'handoff'))
        self.assertEqual(first['details']['step']['command'], 'review-context')
        self.assertEqual(second['details']['step']['command'], 'record-check')
        self.assertNotEqual(first['stop_id'], second['stop_id'])
        for line in (first, second):
            stored = self.case.read_records()['review_series_stop'][line['stop_id']]['plan']
            self.assertEqual((stored['state'], stored['details']['step']['command']),
                             (line['state'], line['details']['step']['command']))

    def test_failed_optional_readings_never_reuse_the_old_receipt(self):
        with patch.object(automatic_overrides, '_scope', side_effect=self.build_error()):
            first = self.build_defect_stop()
            second = self.build_defect_stop()
        self.assertNotEqual(first['stop_id'], second['stop_id'])
        self.assertEqual(second['details']['step']['command'], 'record-check')

    def test_reading_a_plan_never_claims_the_last_executed_action_was_refused(self):
        rows, executed = [], []
        known_plan = dict(self.plan, provider_usage=dict(windows=[], warnings=[]))
        def succeed(store, payload, **identity):
            executed.append(identity['request_id'])
            return store.mutate('fixture.executed', {}, lambda tx: None, **identity)
        with patch.object(review_series, '_read_plan', side_effect=self.read_current_plans([
                (self.snapshot, known_plan), self.build_error(), self.build_error()])), \
                patch.dict(cli.OPERATIONS, {'review-run': succeed}):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        self.assertEqual(len(executed), 1)
        self.assertNotIn('refused_action', rows[-1]['details'])
        self.assertEqual(rows[0]['details']['omitted']['next_plan']['code'], INJECTED_CODE)
        self.assertEqual(rows[0]['provider_usage'], known_plan['provider_usage'])

    def test_operation_and_post_operation_rendering_failures_keep_the_operation_code(self):
        with patch.object(review_series, 'render_workspace_views', side_effect=RuntimeError('Synthetic renderer failure.')):
            line = self.run_refusal()
        self.assertEqual(line['code'], INJECTED_CODE)
        self.assertEqual(line['details']['omitted']['views']['code'], 'RuntimeError')
        self.assertEqual(line['details']['refused_action']['state'], 'run')

    def test_successful_operation_counts_even_when_post_operation_rendering_fails(self):
        rows, executed = [], []
        def succeed(store, payload, **identity):
            executed.append(identity['request_id'])
            return store.mutate('fixture.executed', {}, lambda tx: None, **identity)
        done = dict(self.plan, state='done', details={})
        with patch.object(review_series, '_read_plan', side_effect=self.read_current_plans([
                (self.snapshot, self.plan), (self.snapshot, done)])), \
                patch.dict(cli.OPERATIONS, {'review-run': succeed}), \
                patch.object(review_series, 'render_workspace_views', side_effect=RuntimeError('Synthetic renderer failure.')):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 0)
        self.assertEqual(len(executed), 1)
        self.assertIsNotNone(self.case.store.committed_request(executed[0]))
        self.assertEqual(rows[0]['details']['omitted']['views']['code'], 'RuntimeError')
        self.assertEqual(rows[-1]['state'], 'done')

    def test_nonending_print_failure_leaves_the_command_after_its_committed_step(self):
        requests = []
        def succeed(store, payload, **identity):
            requests.append(identity['request_id'])
            return store.mutate('fixture.executed', {}, lambda tx: None, **identity)
        failure = RuntimeError('Synthetic nonterminal output failure.')
        def fail_output(row):
            raise failure
        with patch.dict(cli.OPERATIONS, {'review-run': succeed}):
            with self.assertRaises(RuntimeError) as caught:
                self.run_series(self.case.store, 'bar-first', emit=fail_output)
        self.assertIs(caught.exception, failure)
        self.assertIsNotNone(self.case.store.committed_request(requests[0]))
        self.assertNotIn('review_series_stop', self.case.read_records())
        self.assertEqual(self.check_copy()['series_stops'], [])

    def test_nonending_output_refusal_enters_the_ending_after_its_committed_step(self):
        requests, rows = [], []
        failure = self.build_error()
        def succeed(store, payload, **identity):
            requests.append(identity['request_id'])
            return store.mutate('fixture.executed', {}, lambda tx: None, **identity)
        def output(row):
            if not rows:
                rows.append(copy.deepcopy(row))
                raise failure
            rows.append(copy.deepcopy(row))
        with patch.dict(cli.OPERATIONS, {'review-run': succeed}):
            result = self.run_series(self.case.store, 'bar-first', emit=output)
        self.assertEqual(result, 3)
        self.assertEqual(len(requests), 1)
        self.assertIsNotNone(self.case.store.committed_request(requests[0]))
        self.assertEqual(len(rows), 2)
        self.assertNotIn(rows[0]['state'], ('stop', 'handoff', 'done'))
        self.assert_unlisted(rows[-1])
        self.assertEqual(rows[-1]['error'], dict(code=INJECTED_CODE, message='Synthetic refusal.',
                                               details=dict(marker='retained')))
        stops = self.case.read_records()['review_series_stop']
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[rows[-1]['stop_id']]['plan']['error'], rows[-1]['error'])

    def test_partial_output_recovers_exact_mandatory_values_for_row_and_fallback(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback), ExitStack() as stack:
                output, printed = io.StringIO(), []
                before_stops = self.case.read_records().get('review_series_stop', {})
                def print_partial_once(row):
                    if not printed:
                        printed.append(copy.deepcopy(row))
                        output.write(json.dumps(row)[:35])
                        raise RuntimeError('Synthetic partial write.')
                    review_series._emit_json(row)
                stack.enter_context(patch('sys.stdout', output))
                stack.enter_context(patch.object(review_series, '_read_plan', return_value=(self.snapshot, self.plan)))
                stack.enter_context(patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error('review_source_no_progress')}))
                if fallback:
                    stack.enter_context(patch('research_harness.review_plan_steps.complete_plan', side_effect=self.build_error()))
                result = self.run_series(self.case.store, 'bar-first', emit=print_partial_once)
                recovered = json.loads(output.getvalue().splitlines()[-1])
                expected = review_series._recover_ending_line(printed[0])
                expected['details'].setdefault('omitted', {})['output'] = dict(code='RuntimeError', message='Synthetic partial write.')
                self.assertEqual(result, 3)
                self.assertEqual(recovered, expected)
                self.assertEqual(recovered['details']['step']['command'], 'record-check' if fallback else 'fulltext')
                self.assertEqual('intended_step' in recovered['details'], fallback)
                stops = self.case.read_records()['review_series_stop']
                self.assertEqual(len(stops), len(before_stops) + 1)
                stored = stops[printed[0]['stop_id']]['plan']
                self.assertEqual(review_series._recover_ending_line(stored), review_series._recover_ending_line(printed[0]))
                self.assertEqual(stored['code'], recovered['code'])
                for member in ('refused_action', 'step', 'intended_step'):
                    self.assertEqual(stored['details'].get(member), recovered['details'].get(member))
                stored_omissions = stored['details'].get('omitted', {})
                self.assertNotIn('output', stored_omissions)
                self.assertEqual(recovered['details']['omitted'], dict(stored_omissions,
                    output=dict(code='RuntimeError', message='Synthetic partial write.')))
                if fallback:
                    self.assertEqual(stored_omissions['step']['code'], INJECTED_CODE)

    def test_store_only_receipt_is_explicitly_unread_when_snapshot_fails(self):
        line = self.run_refusal()
        with patch.object(record_check, 'check_snapshot', side_effect=self.build_error()):
            report = record_check.check_record(self.case.root, isolate=False)
        self.assertFalse(report['ready'])
        self.assertEqual(len(report['series_stops']), 1)
        unread = report['series_stops'][0]
        self.assertEqual((unread['place'], unread['status']), ('store', 'not_read'))
        self.assertEqual(unread['error'], report['steps'][0]['error'])
        self.assertEqual(self.check_copy()['series_stops'][0]['id'], line['stop_id'])

    def test_memory_only_stop_description_and_classification_need_no_store(self):
        with patch.object(self.case.store, 'snapshot', side_effect=self.build_error()), \
                patch.object(self.case.store, 'build_artifact_store', side_effect=self.build_error()):
            row = review_series._describe_stop(self.plan, INJECTED_CODE, {})
            self.assertEqual(stop_classes.classify_stop(INJECTED_CODE), 'decision')
            self.assertEqual(self.build_error().as_dict(), dict(code=INJECTED_CODE,
                message='Synthetic refusal.', details=dict(marker='retained')))
        self.assertTrue(row['details']['unlisted'])
        self.assertNotIn('refused_action', row['details'])

    def test_readings_are_held_for_a_nonending_pending_invocation_line(self):
        resume = dict(self.plan, state='resume')
        done = dict(self.plan, state='done', details={})
        for target, member in [('research_harness.review_provider_usage.describe_usage', 'provider_usage'),
                ('research_harness.review_route_capacity.describe_assignment_warning', 'capacity_warning')]:
            rows = []
            with self.subTest(target=target), \
                    patch.object(review_series, '_read_plan', side_effect=[(self.snapshot, resume), (self.snapshot, done)]), \
                    patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error('review_invocation_pending')}), \
                    patch.object(review_series.time, 'sleep', return_value=None), \
                    patch(target, side_effect=RuntimeError('Synthetic diagnostic failure.')):
                result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
            self.assertEqual(result, 0)
            self.assertEqual(rows[0]['error']['code'], 'review_invocation_pending')
            self.assertEqual(rows[0]['details']['omitted'][member]['code'], 'RuntimeError')
            self.assertEqual(rows[-1]['state'], 'done')

    def test_latest_file_uses_retention_order_instead_of_its_random_name(self):
        directory = self.case.root / review_series.ENDING_RECEIPTS_DIRECTORY
        directory.mkdir()
        for name, retained_at in [('z.json', 1), ('a.json', 2)]:
            line = dict(state='stop', head_id='bar-first', code='artifact_missing', details=dict(
                receipt=dict(place='workspace', path=review_series.ENDING_RECEIPTS_DIRECTORY + '/' + name,
                             retained_at=retained_at)))
            (directory / name).write_text(json.dumps(line))
        self.assertTrue(self.check_copy()['series_stops'][0]['path'].endswith('/a.json'))

    def test_malformed_retention_metadata_fails_the_receipt_reader_without_crashing(self):
        directory = self.case.root / review_series.ENDING_RECEIPTS_DIRECTORY
        directory.mkdir()
        for key, value in [('revision', 'invalid'), ('retained_at', 'invalid')]:
            line = dict(state='stop', head_id='bar-first', code=INJECTED_CODE, details=dict(receipt={}))
            if key == 'revision':
                line[key] = value
            else:
                line['details']['receipt'][key] = value
            (directory / 'bad.json').write_text(json.dumps(line))
            self.assertEqual(self.check_copy()['series_stops'][0]['finding'], 'record_json_invalid')

    def test_old_planner_refused_action_keeps_its_bytes_and_does_not_block_new_receipts(self):
        from research_harness.evidence import digest
        legacy_plan = dict(state='stop', code=INJECTED_CODE, head_id='bar-first', progress=self.plan['progress'],
                           details=dict(refused_action=dict(command='plan_next', assignment_id='bar-first')))
        legacy = dict(id='old-planner-stop', origin='harness', code=INJECTED_CODE, assignment_id='bar-first',
                      plan=legacy_plan, retry=None, scope=None, request_id='old-planner-stop',
                      recorded_revision=self.case.store.revision + 1)
        self.case.store.mutate('review.series_stop', dict(stop=legacy),
            lambda tx: tx.put('review_series_stop', legacy['id'], legacy),
            expected_revision=self.case.store.revision, request_id=legacy['request_id'])
        line = self.run_refusal()
        self.assertIn('stop_id', line)
        self.assertNotEqual(line['stop_id'], legacy['id'])
        self.assertEqual(line['details']['receipt']['place'], 'store')
        self.assertEqual(digest(self.case.read_records()['review_series_stop'][legacy['id']]), digest(legacy))

    def test_identity_refusals_use_the_plan_in_hand_and_other_errors_crash(self):
        for target in ('_identify_action', 'digest'):
            rows = []
            with self.subTest(target=target), patch.object(review_series, target, side_effect=self.build_error()):
                result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
            self.assertEqual(result, 3)
            self.assertEqual(rows[-1]['code'], INJECTED_CODE)
            self.assertEqual(rows[-1]['details']['refused_action'],
                             {key: self.plan[key] for key in ('command', 'state', 'payload')})
        with patch.object(review_series, 'digest', side_effect=ValueError('Synthetic invalid JSON identity.')):
            with self.assertRaises(ValueError):
                self.run_series(self.case.store, 'bar-first', emit=lambda row: None)

    def test_initial_and_wait_replans_keep_their_distinct_intermediate_lines(self):
        done = dict(self.plan, state='done', code='problem_already_posted', details={})
        waiting = dict(self.plan, state='wait', not_before='2000-01-01T00:00:00Z')
        for after_wait in (False, True):
            sequence = ([(self.snapshot, waiting)] if after_wait else []) + [
                self.build_error('store_busy'), (self.snapshot, done)]
            rows = []
            with self.subTest(after_wait=after_wait), patch.object(review_series, '_read_plan', side_effect=sequence):
                result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
            self.assertEqual(result, 0)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].get('error', {}).get('code'), None if after_wait else 'store_busy')
            self.assertNotIn('refused_action', rows[-1]['details'])
            self.assertEqual(rows[-1]['details']['receipt']['place'], 'store')

    def test_snapshot_replan_names_both_refusals_before_continuing(self):
        done = dict(self.plan, state='done', code='problem_already_posted', details={})
        original = self.case.store.snapshot
        refused, reads = [], []
        def refuse(*args, **kwargs):
            refused.append(True)
            raise self.build_error()
        def snapshot():
            if refused and not reads:
                reads.append(True)
                raise self.build_error('store_busy')
            return original()
        rows = []
        with patch.object(review_series, '_read_plan', side_effect=[(self.snapshot, self.plan), (self.snapshot, done)]), \
                patch.object(self.case.store, 'snapshot', side_effect=snapshot), \
                patch('research_harness.review_stop.read_driver_boundary', return_value=None), \
                patch.dict(cli.OPERATIONS, {'review-run': refuse}):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 0)
        self.assertEqual(rows[0]['error']['code'], INJECTED_CODE)
        self.assertEqual(rows[0]['read_error']['code'], 'store_busy')
        self.assertEqual(rows[0]['details']['omitted']['snapshot']['code'], 'store_busy')
        self.assertEqual(rows[-1]['state'], 'done')

    def test_nonending_forecast_and_reading_receipt_failures_are_held(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['records']['review_assignment']['bar-first']['capacity_plan_kind'] = 'synthetic'
        resume = dict(self.plan, state='resume')
        resume.pop('capacity_forecast', None)  # This test isolates the plan/reading diagnostic, not session forecasting.
        done = dict(self.plan, state='done', details={})
        rows = []
        with patch.object(review_series, '_read_plan', side_effect=[(snapshot, resume), (self.snapshot, done)]), \
                patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error('review_invocation_pending')}), \
                patch.object(review_series.time, 'sleep', return_value=None), \
                patch('research_harness.review_route_capacity.describe_assignment_warning', return_value=None), \
                patch('research_harness.review_route_capacity.describe_assignment_capacity', side_effect=self.build_error()):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 0)
        self.assertEqual(rows[0]['details']['omitted']['capacity_forecast']['code'], INJECTED_CODE)
        self.assertNotIn('capacity_forecast', rows[0])
        snapshot['records']['review_assignment']['bar-first'].pop('capacity_plan_kind')
        snapshot['records']['review_assignment']['bar-first']['reading_chain'] = True
        rows = []
        with patch.object(review_series, '_read_plan', side_effect=[(snapshot, resume), (self.snapshot, done)]), \
                patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **k: self.raise_error('review_invocation_pending')}), \
                patch.object(review_series.time, 'sleep', return_value=None), \
                patch('research_harness.review_route_capacity.describe_session_forecast', return_value=None), \
                patch('research_harness.review_provider_usage.describe_usage', return_value={}), \
                patch('research_harness.review_route_capacity.describe_assignment_warning', return_value=None), \
                patch('research_harness.review_protocol._read_json', side_effect=TypeError('Synthetic reading failure.')):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 0)
        self.assertEqual(rows[0]['details']['omitted']['capacity_forecast']['code'], 'TypeError')
        self.assertNotIn('capacity_forecast', rows[0])

    def test_failed_usage_of_the_next_plan_keeps_the_last_known_usage(self):
        known = dict(windows=[], warnings=[], marker='last-observed')
        plan = dict(self.plan, provider_usage=known)
        done = dict(self.plan, state='done', code='problem_already_posted',
                    details=dict(omitted=dict(provider_usage=self.build_error().as_dict())))
        rows = []
        def succeed(store, payload, **identity):
            return store.mutate('fixture.executed', {}, lambda tx: None, **identity)
        with patch.object(review_series, '_read_plan', side_effect=[(self.snapshot, plan), (self.snapshot, done)]), \
                patch.dict(cli.OPERATIONS, {'review-run': succeed}):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 0)
        self.assertEqual(rows[-1]['provider_usage'], known)
        self.assertEqual(rows[-1]['details']['omitted']['provider_usage']['code'], INJECTED_CODE)

    def test_enrichment_helper_is_held_for_endings_and_nonendings(self):
        with patch.object(review_series, '_enrich_line', side_effect=TypeError('Synthetic diagnostic failure.')):
            line = self.run_refusal()
        self.assertEqual(line['code'], INJECTED_CODE)
        self.assertEqual(line['details']['omitted']['diagnostics']['code'], 'TypeError')
        done = dict(self.plan, state='done', details={})
        rows = []
        with patch.object(review_series, '_read_plan', side_effect=[(self.snapshot, self.plan), (self.snapshot, done)]), \
                patch.dict(cli.OPERATIONS, {'review-run': lambda store, payload, **identity:
                    store.mutate('fixture.executed', {}, lambda tx: None, **identity)}), \
                patch.object(review_series, '_enrich_line', side_effect=self.build_error()):
            result = self.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 0)
        self.assertEqual(rows[0]['details']['omitted']['diagnostics']['code'], INJECTED_CODE)
        self.assertEqual(rows[-1]['state'], 'done')

    def end_after_exhausted_turns(self):
        """End the series of a bar whose turn position provider refusals have exhausted."""
        with patch('research_harness.review_transport.send_request', side_effect=OSError('The call ended.')):
            self.case.mutate(protocol.invoke_assignment, dict(id='refused', assignment_id='bar-first'),
                             credential='fixture')
        # The stop of the failure planner after nine provider refusals, as test_d2_outcomes counts them.
        exhausted = dict(state='stop', code='review_turn_failures_exhausted', stop_class='decision', not_before=None,
                         details=dict(outcome='provider_refused', sequence=3, failed_attempts=3, sequences_remaining=0))
        lines = []
        with patch('research_harness.review_failure_class.plan_after_failure', return_value=exhausted):
            self.assertEqual(self.run_series(self.case.store, 'bar-first', emit=lines.append), 3)
        return lines[-1]

    def end_over_pending_delivery(self):
        """End a bar session whose three requested full texts exceed the input of its route."""
        forecast = forecast_fixtures.SessionForecastTests()
        forecast.setUp()
        self.addCleanup(forecast.doCleanups)
        requested = forecast.request_texts([180000, 180000, 180000])
        lines = []
        self.assertEqual(self.run_series(forecast.case.store, forecast.assignment['id'], emit=lines.append), 3)
        return lines[-1], [row['version_id'] for row in requested]

    def test_keep_this_route_shows_its_own_observation_step(self):
        # Source: 20261115234509-bar-slot-ends-on-evidence-form-and-route-input-limit.
        line = self.end_after_exhausted_turns()
        self.assertEqual((line['state'], line['code']), ('stop', 'review_turn_failures_exhausted'))
        answers = {answer['answer']: answer['steps'] for answer in line['details']['decision']['then']}
        keep = answers['Keep this route'][0]
        self.assertEqual((keep['id'], keep['command'], keep['payload']['kind']), (9, 'review-context', 'observation'))
        self.assertEqual(keep['arguments'], {'--workspace': str(self.case.store.root)})
        self.assertEqual(answers['Move to another route'][0]['payload']['kind'], 'route_retired')
        stop = self.case.read_records()['review_series_stop'][line['stop_id']]
        self.assertEqual(stop['plan']['details']['decision'], line['details']['decision'])

    def test_capture_answer_names_each_pending_source(self):
        # Source: 20261115234509-bar-slot-ends-on-evidence-form-and-route-input-limit, where seven capture steps
        # of the stored ending named the first pending source.
        line, versions = self.end_over_pending_delivery()
        self.assertEqual((line['state'], line['code']), ('stop', 'review_pending_delivery_over_plan'))
        captures = [step for step in line['details']['decision']['then'][0]['steps']
                    if step['command'] == 'select-main-capture']
        self.assertEqual(len(set(versions)), 3)
        self.assertEqual([step['payload']['version_id'] for step in captures], versions)

    def test_every_planned_decision_keeps_its_steps_in_the_ending(self):
        # Each registry code whose plan has a decision in the fixtures of this module and of test_d2_outcomes.
        # The harness defect review_route_capacity_plan_violated is left out: its only answer is the step of
        # the ending, which the ending rebuilds with the id of the retained stop.
        endings = [(code, partial(self.run_refusal, code))
                   for code in ('artifact_missing', 'review_budget_exhausted', 'review_output_over_cap')]
        endings += [('review_turn_failures_exhausted', self.end_after_exhausted_turns),
                    ('review_pending_delivery_over_plan', lambda: self.end_over_pending_delivery()[0])]
        complete_plan = review_plan_steps.complete_plan
        planned = []
        def record_planned_answers(*args):
            plan = complete_plan(*args)
            if plan.get('details', {}).get('decision'):
                planned.append(copy.deepcopy(read_answers(plan)))
            return plan
        for code, end_series in endings:
            planned.clear()
            with self.subTest(code=code), patch.object(review_plan_steps, 'complete_plan', record_planned_answers):
                line = end_series()
                self.assertEqual(line['code'], code)
                self.assertEqual(read_answers(line), planned[-1])
