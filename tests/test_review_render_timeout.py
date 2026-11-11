"""A PDF page render that does not finish in its time limit is a failed run, never evidence about a record."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from research_harness import cli, record_check, review_protocol as protocol
from research_harness import review_visual_route as bridge
from research_harness.errors import ResearchError


REAL_RUN = subprocess.run


def run_with_unfinished_render(arguments, *args, **kwargs):
    """Let every process run except a page render, which ends as subprocess.run ends it at its time limit."""
    if Path(str(arguments[0])).name == 'pdftoppm' and arguments[-1] != '-v':
        raise subprocess.TimeoutExpired(arguments, kwargs['timeout'])
    return REAL_RUN(arguments, *args, **kwargs)


def run_isolated_child_in_process(clone, source, report, report_directory):
    """Run the isolated child's own entry point in this process, so the patched renderer applies to it."""
    path = report_directory / 'report.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    record_check.main(['--snapshot', str(clone), '--report', str(path)])
    return json.loads(path.read_text(encoding='utf-8'))


class RenderTimeoutTests(unittest.TestCase):
    def setUp(self):
        from test_visual_result_delivery import VisualResultDeliveryTests
        self.fixture = VisualResultDeliveryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.case = self.fixture.case
        # The review cites a native PDF page, so recording it and every later reader render that page.
        self.output = self.fixture.observe([{'kind': 'source', 'link': self.fixture.pdf_link}])

    def unfinished_render(self):
        return patch('subprocess.run', side_effect=run_with_unfinished_render)

    def assert_timeout_error(self, error):
        self.assertEqual(error['code'], 'local_tool_timeout')
        self.assertEqual(error['details'], {'tool': 'pdftoppm', 'budget_seconds': 60})
        self.assertIn('pdftoppm', error['message'])
        self.assertIn('less load', error['message'])

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as stopped:
            cli.main(argv, render_views=lambda workspace: None)
        return stopped.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_unfinished_render_records_no_refusal_and_the_review_records_after_it_finishes(self):
        with self.unfinished_render(), self.assertRaises(subprocess.TimeoutExpired):
            self.fixture.record(self.output)
        records = self.case.read_records()
        self.assertEqual(records.get('review_output_refusal', {}), {})
        self.assertNotIn('recorded-visual-result', records.get('value_review', {}))
        review = self.fixture.record(self.output)
        records = self.case.read_records()
        self.assertEqual(review['id'], 'recorded-visual-result')
        self.assertEqual(records.get('review_output_refusal', {}), {})
        self.assertTrue(protocol.authenticate_attempt(records, self.fixture.artifacts, self.fixture.assignment,
                                                      records['review_attempt']['actual-visual-attempt']))

    def test_visual_bridge_lets_the_unfinished_render_pass_and_validates_once_it_finishes(self):
        records = self.case.read_records()
        child = self.fixture.assignment
        parent = records['review_assignment'][child['history_assignment_id']]
        with self.unfinished_render(), self.assertRaises(subprocess.TimeoutExpired):
            bridge.validate_lineage_bridge(records, self.fixture.artifacts, child, parent)
        bridge.validate_lineage_bridge(records, self.fixture.artifacts, child, parent)

    def test_record_check_reports_the_unfinished_render_as_a_failed_step_and_passes_once_it_finishes(self):
        self.fixture.record(self.output)
        before = record_check.check_record(self.case.root, isolate=False)
        self.assertTrue(before['ready'], before)
        with self.unfinished_render():
            report = record_check.check_record(self.case.root, isolate=False)
        self.assertFalse(report['ready'])
        failed = [row for row in report['steps'] if row['status'] != 'passed']
        self.assertEqual(len(failed), 1, report['steps'])
        self.assert_timeout_error(failed[0]['error'])
        after = record_check.check_record(self.case.root, isolate=False)
        self.assertTrue(after['ready'], after)
        authentication = [[row['result'] for row in result['steps'] if row['step'] == 'authentication']
                          for result in (before, after)]
        self.assertEqual(authentication[0], authentication[1])

    def test_record_check_command_prints_its_report_and_exits_one_from_the_isolated_child(self):
        self.fixture.record(self.output)
        with self.unfinished_render(), patch.object(record_check, 'check_isolated_snapshot',
                                                    side_effect=run_isolated_child_in_process):
            code, stdout, stderr = self.run_cli(['record-check', '--workspace', str(self.case.root)])
        self.assertEqual(code, 1)
        self.assertEqual(stderr, '')
        report = json.loads(stdout)
        self.assertFalse(report['ready'])
        self.assert_timeout_error(report['steps'][-1]['error'])

    def test_status_next_and_gate_post_end_with_one_json_error(self):
        self.fixture.record(self.output)
        for argv in (['status'], ['next'], ['gate', 'post']):
            with self.subTest(command=argv), self.unfinished_render():
                code, stdout, stderr = self.run_cli(argv + ['--workspace', str(self.case.root)])
                self.assertEqual(code, 1)
                self.assertEqual(stdout, '')
                self.assert_timeout_error(json.loads(stderr)['error'])

    def test_boundary_reports_only_an_unfinished_tool(self):
        from research_harness.errors import describe_tool_timeout
        with self.assertRaises(ResearchError) as caught:
            with describe_tool_timeout():
                raise subprocess.TimeoutExpired(['/opt/tools/pdftoppm', '-r', '150'], 60)
        self.assert_timeout_error(caught.exception.as_dict())
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)
        with self.assertRaises(ResearchError) as kept:
            with describe_tool_timeout():
                raise ResearchError('review_visual_source_mismatch', 'A real mismatch')
        self.assertEqual(kept.exception.code, 'review_visual_source_mismatch')


if __name__ == '__main__':
    unittest.main()
