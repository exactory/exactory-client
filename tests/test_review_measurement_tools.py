"""Offline regressions for the retained paired-measurement tooling."""

from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from research_harness import review_protocol, review_transport
from research_harness.errors import ResearchError
from tools.review_measurement import run_pair
from tools.review_measurement.fixtures import FixtureTests, build_fixtures
from tools.review_measurement.run_pair import RunnerTests
from tools.review_measurement.score import ScoringTests


class CampaignStopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        boundary = patch.object(review_transport, 'send_request',
                                side_effect=AssertionError('Tests cannot call a model.'))
        boundary.start()
        self.addCleanup(boundary.stop)

    def make_route(self, adapter='claude_cli_v1'):
        return dict(id='route', adapter=adapter, model='fixed-model',
                    configuration=dict(effort='xhigh', max_output_tokens=128000))

    def make_limit_response(self, answer, thread='quota-thread'):
        return dict(id=thread, model='fixed-model', status='incomplete',
                    output=[dict(type='message', role='assistant',
                                 content=[dict(type='output_text', text=answer)])],
                    usage=dict(input_tokens=1, output_tokens=0),
                    context_evidence=dict(thread_id=thread, inspected=False), execution={})

    def test_claude_limit_shapes_stop_and_restart_refuses_dispatch(self):
        for index, answer in enumerate((
                "You've hit your weekly limit · resets Nov 6 at 9am (America/Phoenix)",
                'You’ve reached your usage limit')):
            with self.subTest(answer=answer):
                path = self.root / str(index) / 'usage.json'
                budget = run_pair.PaidBudget(10, path)
                response = self.make_limit_response(answer)
                calls = []

                def sender(route, request, credential):
                    calls.append((route, request, credential))
                    return response

                send = budget.wrap_sender(sender)
                self.assertIs(send(self.make_route(), {}), response)
                saved = json.loads(path.read_text())
                self.assertEqual(saved.get('stop', {}).get('outcome'), 'usage_limit')
                restarted = run_pair.PaidBudget(10, path)
                with self.assertRaises(ResearchError) as caught:
                    restarted.wrap_sender(sender)(self.make_route(), {})
                self.assertEqual(caught.exception.code, 'measurement_usage_limit')
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(restarted.calls), 1)

    def test_codex_empty_limit_rollout_stops_including_probe(self):
        home = self.root / 'codex-home'
        sessions = home / 'sessions'
        sessions.mkdir(parents=True)
        event = dict(type='event_msg', payload=dict(type='task_complete', error=dict(
            codex_error_info='usage_limit_exceeded',
            message='You have hit your usage limit. Try again at Nov 6th, 2026 9:00 AM.')))
        (sessions / 'rollout-quota-thread.jsonl').write_text(json.dumps(event) + '\n')
        budget = run_pair.PaidBudget(10, self.root / 'usage.json')
        calls = []

        def sender(route, request, credential):
            calls.append(request)
            return self.make_limit_response('')

        probe = dict(input=[dict(content='{"allowed_control":"positive"}')])
        with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
            budget.wrap_sender(sender)(self.make_route('codex_cli_v1'), probe)
        saved = json.loads(budget.path.read_text())
        self.assertEqual(saved.get('stop', {}).get('outcome'), 'usage_limit')
        self.assertEqual(saved['calls'][0]['kind'], 'probe')
        with self.assertRaises(ResearchError):
            budget.wrap_sender(sender)(self.make_route(), {})
        self.assertEqual(len(calls), 1)

    def test_cap_is_durable_and_checked_before_sender(self):
        budget = run_pair.PaidBudget(1, self.root / 'usage.json')
        calls = []
        def sender(route, request, credential):
            calls.append(request)
            return dict(status='completed', usage=None)
        budget.wrap_sender(sender)(self.make_route(), {})
        restarted = run_pair.PaidBudget(1, budget.path)
        with self.assertRaises(ResearchError) as caught:
            restarted.wrap_sender(sender)(self.make_route(), {})
        self.assertEqual(caught.exception.code, 'measurement_budget_exhausted')
        self.assertEqual(len(calls), 1)

    def test_interrupted_admission_stays_charged_after_restart(self):
        budget = run_pair.PaidBudget(1, self.root / 'usage.json')
        def interrupt(route, request, credential):
            saved = json.loads(budget.path.read_text())
            self.assertEqual(saved['calls'][0]['status'], 'admitted')
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            budget.wrap_sender(interrupt)(self.make_route(), {})
        restarted = run_pair.PaidBudget(1, budget.path)
        self.assertEqual(len(restarted.calls), 1)
        with self.assertRaises(ResearchError):
            restarted.wrap_sender(interrupt)(self.make_route(), {})

    def test_sender_receives_identical_inputs_and_response_is_unchanged(self):
        route = self.make_route()
        request = dict(model='fixed-model', reasoning=dict(effort='xhigh'),
                       input=[dict(role='user', content='unchanged evidence')])
        before = copy.deepcopy((route, request))
        response = self.make_limit_response('API Error: response exceeded the 128000 output token maximum')
        def sender(actual_route, actual_request, credential):
            self.assertIs(actual_route, route)
            self.assertIs(actual_request, request)
            self.assertEqual(credential, 'synthetic-credential')
            return response
        budget = run_pair.PaidBudget(2, self.root / 'usage.json')
        self.assertIs(budget.wrap_sender(sender)(route, request, 'synthetic-credential'), response)
        self.assertEqual((route, request), before)
        self.assertIsNone(json.loads(budget.path.read_text()).get('stop'))

    def test_usage_limit_prevents_later_modes_repeats_and_cases(self):
        calls = []
        case = build_fixtures()[0]
        def sender(route, request, credential):
            calls.append(copy.deepcopy((route, request)))
            packet = json.loads(request['input'][-1]['content'].split(
                'The packet below is JSON.\n', 1)[-1])
            if 'allowed_control' in packet:
                return run_pair.build_synthetic_response(route, request, dict(
                    allowed_control=packet['allowed_control'],
                    evidence_control=packet['evidence_control'], excluded_controls=[]))
            response = run_pair.build_synthetic_response(route, request, {})
            response['output'][0]['content'][0]['text'] = "You've hit your weekly limit"
            return response
        budget = run_pair.PaidBudget(50, self.root / 'usage.json')
        modes = []
        real_mode = run_pair.run_mode
        def mode(*args, **kwargs):
            modes.append(args[2])
            return real_mode(*args, **kwargs)
        with patch.object(review_transport, 'send_request', side_effect=sender), patch.object(
                run_pair, 'run_mode', side_effect=mode):
            first = run_pair.run_pair(case, self.root / 'pairs', dry_run=False,
                                      budget=budget, extra_single_runs=2)
            second = run_pair.run_pair(build_fixtures()[1], self.root / 'pairs',
                                       dry_run=False, budget=budget)
        self.assertEqual(len(calls), 2, 'The probe and first review exhaust this campaign.')
        self.assertEqual(modes, ['single'])
        self.assertEqual(first['model_calls'], 2)
        self.assertEqual(second['model_calls'], 0)
        self.assertFalse(first['parts']['complete'])
        self.assertFalse(second['single']['complete'])
        self.assertEqual(len(first['repeats']), 0)


    def test_cli_stops_scheduling_after_a_probe_usage_limit(self):
        argv = ['run_pair.py', '--paid', '--all', '--plugin-root',
                str(Path(__file__).resolve().parents[1]), '--max-calls', '20',
                '--output', str(self.root / 'campaign')]
        response = self.make_limit_response("You've hit your weekly limit")
        with patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()), patch.object(
                review_transport, 'send_request', return_value=response), patch.object(
                run_pair, 'run_pair', wraps=run_pair.run_pair) as pair:
            exit_code = run_pair.main()
        self.assertEqual(exit_code, 1)
        self.assertEqual(pair.call_count, 1)
        report = json.loads((self.root / 'campaign/report.json').read_text())
        self.assertEqual(report['allocated'], 36)
        self.assertEqual(report['incomplete'], dict(single=36, parts=36))

    def test_cli_restart_reads_stop_before_writing_campaign_outputs(self):
        output = self.root / 'campaign'
        budget = run_pair.PaidBudget(20, output / 'usage.json')
        budget.wrap_sender(lambda *args: self.make_limit_response(
            "You've hit your weekly limit"))(self.make_route(), {})
        before = {str(path): path.read_bytes() for path in output.rglob('*') if path.is_file()}
        argv = ['run_pair.py', '--paid', '--all', '--plugin-root',
                str(Path(__file__).resolve().parents[1]), '--max-calls', '20', '--output', str(output)]
        with patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()):
            self.assertEqual(run_pair.main(), 3)
        self.assertEqual({str(path): path.read_bytes() for path in output.rglob('*') if path.is_file()}, before)


    def test_hard_interruption_blocks_restart_even_below_the_cap(self):
        script = """
import os
from pathlib import Path
import sys
from tools.review_measurement.campaign import PaidBudget
budget = PaidBudget(5, Path(sys.argv[1]))
def killed_after_response(route, request, credential):
    os._exit(17)
budget.wrap_sender(killed_after_response)({'id': 'route'}, {})
"""
        path = self.root / 'usage.json'
        completed = subprocess.run([sys.executable, '-c', script, str(path)],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 17, completed.stderr)
        restarted = run_pair.PaidBudget(5, path)
        self.assertEqual(len(restarted.calls), 1)
        dispatched = []
        with self.assertRaises(ResearchError) as caught:
            restarted.wrap_sender(lambda *args: dispatched.append(args))(self.make_route(), {})
        self.assertEqual(caught.exception.code, 'measurement_call_unresolved')
        self.assertEqual(dispatched, [])


    def test_interruption_during_classification_cannot_reopen_campaign(self):
        for retained, code in ((False, 'measurement_call_unresolved'),
                               (True, 'measurement_usage_limit')):
            with self.subTest(retained=retained):
                path = self.root / str(retained) / 'usage.json'
                budget = run_pair.PaidBudget(5, path)
                classify = budget.retain_classification
                def interrupt(route, response, call):
                    if retained:
                        classify(route, response, call)
                    raise KeyboardInterrupt()
                with patch.object(budget, 'retain_classification', side_effect=interrupt), \
                        self.assertRaises(KeyboardInterrupt):
                    budget.wrap_sender(lambda *args: self.make_limit_response(
                        "You've hit your weekly limit"))(self.make_route(), {})
                restarted = run_pair.PaidBudget(5, path)
                dispatched = []
                def unexpected_sender(*args):
                    dispatched.append(args)
                    return dict(status='completed')
                with self.assertRaises(ResearchError) as caught:
                    restarted.wrap_sender(unexpected_sender)(self.make_route(), {})
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(dispatched, [])


if __name__ == '__main__':
    unittest.main()
