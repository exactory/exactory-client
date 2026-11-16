"""Close the reviewed reach and retain executable inspection endings."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci import series_code_reach as reach
from ci.series_code_groups import read_group_entries
from research_harness import cli, ocr, review_series, stop_classes
from research_harness import review_transport
from research_harness.errors import ResearchError
from test_series_code_reach import record_reachable_errors
import test_review_continuation as continuation_fixtures


ROOT = Path(__file__).resolve().parents[1]
MEMBERS = ROOT / 'tests/fixtures/series_code_groups/d2-rows-close-closing.json'


class ClosingAnalysisTests(unittest.TestCase):
    def test_every_reachable_code_has_a_reviewed_registry_entry(self):
        report = json.loads((ROOT / 'tests/fixtures/series_code_reach.json').read_text())
        self.assertEqual(report['unreviewed_rows'], {})
        self.assertLessEqual(set(report['reachable']), set(stop_classes.STOP_REGISTRY))

    def test_all_member_files_pass_the_live_place_and_condition_guard(self):
        report = reach.analyze_repository(ROOT)
        self.assertEqual(reach.build_unreviewed_rows(report, stop_classes.STOP_REGISTRY), {})
        groups = read_group_entries(ROOT, report, stop_classes.STOP_REGISTRY, stop_classes.STOP_GROUPS)
        fixture = json.loads((ROOT / 'tests/fixtures/series_code_reach.json').read_text())
        self.assertEqual(groups, fixture['groups'])
        self.assertEqual(report['reachable'], fixture['reachable'])
        self.assertEqual(fixture['unreviewed_rows'], {})
        closing = json.loads(MEMBERS.read_text())
        for group, name in (('named_cause', '_NAMED_CAUSE_CODES_CLOSING'),
                            ('stored_state', '_STORED_STATE_CODES_CLOSING')):
            self.assertEqual(set(closing[group]['members']), set(getattr(stop_classes, name)))

    def test_non_refusal_exception_names_are_diagnostics_instead_of_series_codes(self):
        from ci import series_code_reach as reach
        sources = {'pkg/core.py': '''
def describe(error):
    return (error.as_dict() if isinstance(error, ResearchError) else
            dict(code=type(error).__name__, message=str(error)))
def describe_mapping(error):
    return (error.as_dict() if isinstance(error, ResearchError) else
            {'code': type(error).__name__, 'message': str(error)})
def entry():
    describe(ResearchError('actual_refusal', 'failure'))
    describe(OSError('io failure'))
    describe_mapping(OSError('io failure'))
'''}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['reachable'], ['actual_refusal'])
        self.assertFalse(report['computed_sites'])

    def test_exception_name_rule_preserves_real_refusals_and_unproved_expressions(self):
        from ci import series_code_reach as reach
        sources = {'pkg/core.py': '''
def entry(error):
    ResearchError('OSError', 'a deliberately named refusal')
    return (error.as_dict() if isinstance(error, ResearchError) else
            ResearchError(type(error).__name__, 'a constructed refusal'))
'''}
        roots = {'entry': ['pkg/core.py:entry']}
        with self.assertRaisesRegex(ValueError, 'Undeclared computed code site'):
            reach.analyze_sources(sources, roots)
        report = reach.analyze_sources(sources, roots,
            {'pkg/core.py:entry:type(error).__name__': ['RuntimeError']})
        self.assertEqual(report['reachable'], ['OSError', 'RuntimeError'])
        for expression in (
                'dict(code=type(error).__name__)',
                '(dict(code=type(error).__name__) if isinstance(error, ResearchError) else error.as_dict())'):
            with self.subTest(expression=expression):
                unguarded = {'pkg/core.py': 'def entry(error):\n    return ' + expression + '\n'}
                with self.assertRaisesRegex(ValueError, 'Undeclared computed code site'):
                    reach.analyze_sources(unguarded, roots)

    def test_exception_name_rule_keeps_planner_codes_and_shadowed_builtins(self):
        from ci import series_code_reach as reach
        body = '''
def entry(error%s):
    return (error.as_dict() if isinstance(error, ResearchError) else
            dict(code=type(error).__name__, message=str(error)))
'''
        for path, parameters in (('research_harness/review_continuation.py', ''),
                                 ('pkg/core.py', ', type'), ('pkg/core.py', ', isinstance')):
            with self.subTest(path=path, parameters=parameters):
                with self.assertRaisesRegex(ValueError, 'Undeclared computed code site'):
                    reach.analyze_sources({path: body % parameters}, {'entry': [path + ':entry']})

    def test_exception_name_rule_preserves_loop_and_comprehension_builtin_shadows(self):
        from ci import series_code_reach as reach
        diagnostic = ('error.as_dict() if isinstance(error, ResearchError) else '
                      'dict(code=type(error).__name__, message=str(error))')
        bodies = (
            'for type in functions:\n        return (' + diagnostic + ')',
            'for isinstance in functions:\n        return (' + diagnostic + ')',
            'return [(' + diagnostic + ') for type in functions]',
            'return [(' + diagnostic + ') for isinstance in functions]',
        )
        for body in bodies:
            with self.subTest(body=body):
                source = 'def entry(error, functions):\n    ' + body + '\n'
                with self.assertRaisesRegex(ValueError, 'Undeclared computed code site'):
                    reach.analyze_sources({'pkg/core.py': source}, {'entry': ['pkg/core.py:entry']})

    def test_exception_name_rule_requires_an_unshadowed_bare_refusal_type(self):
        from ci import series_code_reach as reach
        diagnostic = ('error.as_dict() if isinstance(error, %s) else '
                      'dict(code=type(error).__name__, message=str(error))')
        cases = (
            ('def entry(error, ResearchError):\n    return (' + diagnostic + ')\n', 'ResearchError'),
            ('def entry(error, functions):\n    for ResearchError in functions:\n        return (' + diagnostic + ')\n', 'ResearchError'),
            ('def entry(error, functions):\n    return [(' + diagnostic + ') for ResearchError in functions]\n', 'ResearchError'),
            ('def entry(error, errors):\n    return (' + diagnostic + ')\n', 'errors.ResearchError'),
        )
        for source, refusal_type in cases:
            with self.subTest(source=source, refusal_type=refusal_type):
                with self.assertRaisesRegex(ValueError, 'Undeclared computed code site'):
                    reach.analyze_sources({'pkg/core.py': source % refusal_type},
                                          {'entry': ['pkg/core.py:entry']})
        imported = {'pkg/core.py': '''
from errors import ResearchError as Failure
def entry(error):
    return (error.as_dict() if isinstance(error, Failure) else
            dict(code=type(error).__name__, message=str(error)))
'''}
        report = reach.analyze_sources(imported, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['reachable'], [])


class ClosingRowTests(unittest.TestCase):
    def prepare_case(self):
        fixture = continuation_fixtures.ContinuationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def run_refusal(self, fixture, code, *, recorded=True):
        def refuse(store, payload, **identity):
            raise ResearchError(code, 'The operation names its retained cause.', {'cause': code})
        lines = []
        if recorded:
            with record_reachable_errors('run_series') as observed, patch.dict(
                    cli.OPERATIONS, {'review-run': refuse}):
                result = review_series.run_series(fixture.case.store, fixture.assignment['id'], emit=lines.append)
            self.assertIn(code, observed)
        else:
            with patch.dict(cli.OPERATIONS, {'review-run': refuse}):
                result = review_series.run_series(fixture.case.store, fixture.assignment['id'], emit=lines.append)
        self.assertEqual(result, 3)
        return lines[-1]

    def check_group(self, group, codes):
        evidence = json.loads(MEMBERS.read_text())[group]['members']
        self.assertEqual(set(codes), set(evidence))
        fixture = self.prepare_case()
        before = copy.deepcopy(fixture.case.read_records())
        packet = fixture.case.artifacts.read(fixture.assignment['packet'])
        for code in codes:
            with self.subTest(code=code):
                last = self.run_refusal(fixture, code)
                self.assertEqual((last['state'], last['code'], last['stop_class']), ('stop', code, 'decision'))
                self.assertNotIn('unlisted', last['details'])
                self.assertEqual(stop_classes.STOP_REGISTRY[code]['group'], group)
                self.assertEqual(last['details']['decision']['owner_decides'],
                                 stop_classes.STOP_GROUPS[group]['next']['owner_decides'])
                self.assertEqual(last['error']['details'], {'cause': code})
        after = fixture.case.read_records()
        for kind, records in before.items():
            for key, record in records.items():
                self.assertEqual(after[kind][key], record)
        self.assertEqual(fixture.case.artifacts.read(fixture.assignment['packet']), packet)

    def test_every_closing_named_cause_member_ends_inside_the_driver_recorder(self):
        self.check_group('named_cause', stop_classes._NAMED_CAUSE_CODES_CLOSING)

    def test_every_closing_stored_state_member_ends_inside_the_driver_recorder(self):
        self.check_group('stored_state', stop_classes._STORED_STATE_CODES_CLOSING)

    def execute_printed_step(self, step):
        self.assertEqual(step.get('chooses', []), [])
        argv = step['command'].split()
        for option, value in step['arguments'].items():
            argv.extend((option, str(value)))
        return cli.run(cli.build_parser().parse_args(argv))

    def check_listed_ending(self, fixture, line):
        for answer in line['details']['decision']['then']:
            for step in answer['steps']:
                report = self.execute_printed_step(step)
                if step['command'] == 'record-check':
                    ending = next(row for row in report['series_stops'] if row.get('id') == line['stop_id'])
                    self.assertEqual((ending['code'], ending['stop_class'], ending['refused_action']),
                                     (line['code'], 'decision', line['details']['refused_action']))
                    self.assertTrue(ending['listed'])
                    self.assertNotIn('finding', ending)
                else:
                    self.assertEqual(step['command'], 'status')
                    self.assertIsInstance(report['obligations'], list)
                    self.assertIsInstance(report['next'], dict)

    def test_named_cause_ending_is_retained_and_both_printed_answers_run(self):
        fixture = self.prepare_case()
        line = self.run_refusal(fixture, 'invalid_review_material')
        self.assertEqual({key: line['details']['receipt'][key] for key in ('place', 'id')},
                         {'place': 'store', 'id': line['stop_id']})
        self.assertEqual(len(line['details']['decision']['then']), 2)
        self.check_listed_ending(fixture, line)

    def check_pdf_failure(self, code, operation):
        fixture = self.prepare_case()

        def extract(store, payload, **identity):
            return operation()

        lines = []
        with record_reachable_errors('run_series') as observed, patch.dict(
                cli.OPERATIONS, {'review-run': extract}):
            result = review_series.run_series(fixture.case.store, fixture.assignment['id'], emit=lines.append)
        self.assertEqual(result, 3)
        self.assertIn(code, observed)
        line = lines[-1]
        self.assertEqual((line['code'], line['stop_class']), (code, 'decision'))
        self.assertNotIn('unlisted', line['details'])
        self.assertEqual(stop_classes.STOP_REGISTRY[code]['group'], 'named_cause')
        self.check_listed_ending(fixture, line)

    def test_missing_pdf_tool_retains_a_named_cause_with_executable_inspections(self):
        self.check_pdf_failure('extractor_missing', lambda: ocr.run_page_tool([None], 1024, 0))

    def test_malformed_pdf_retains_a_named_cause_with_executable_inspections(self):
        def read_bad_metadata(*args, **kwargs):
            return b'Pages: 0\n'

        self.check_pdf_failure('malformed_pdf', lambda: ocr.count_pdf_pages(
            'synthetic.pdf', 'pdfinfo', run=read_bad_metadata, deadline=0))

    def test_source_code_outside_rule_ten_retains_unlisted_finding_until_it_has_a_row(self):
        fixture = self.prepare_case()
        code = 'checkpoint_assessment_mismatch'
        report = json.loads((ROOT / 'tests/fixtures/series_code_reach.json').read_text())
        self.assertIn(code, report['code_vocabulary'])
        self.assertNotIn(code, reach.read_command_codes(report, 'run_series'))
        self.assertNotIn(code, stop_classes.STOP_REGISTRY)
        line = self.run_refusal(fixture, code, recorded=False)
        self.assertEqual((line['state'], line['code'], line['stop_class']), ('stop', code, 'decision'))
        self.assertTrue(line['details']['unlisted'])
        self.assertEqual(line['details']['refused_action']['command'], 'review-run')
        self.assertEqual({key: line['details']['receipt'][key] for key in ('place', 'id')},
                         {'place': 'store', 'id': line['stop_id']})
        step = line['details']['decision']['then'][0]['steps'][0]
        self.assertEqual(step['command'], 'record-check')
        first = self.execute_printed_step(step)
        retained = next(row for row in first['series_stops'] if row.get('id') == line['stop_id'])
        self.assertEqual(retained['finding'], 'record_unlisted_stop')
        self.assertEqual(retained['code'], code)
        self.assertFalse(first['ready'])
        row = dict(code=code, **{'class': 'decision'}, owner='D2',
                   next=copy.deepcopy(stop_classes.STOP_GROUPS['named_cause']['next']))
        with patch.dict(stop_classes.STOP_REGISTRY, {code: row}):
            second = self.execute_printed_step(step)
        retained = next(row for row in second['series_stops'] if row.get('id') == line['stop_id'])
        self.assertTrue(retained['listed'])
        self.assertNotIn('finding', retained)
        self.assertEqual(retained['refused_action'], line['details']['refused_action'])

    def test_runtime_drift_replans_the_captured_call_then_records_its_review(self):
        fixture = self.prepare_case()
        runtime = review_transport.runtime()
        observations = []
        armed = []
        def observe_runtime(route=None):
            if armed:
                index = len(observations)
                observations.append(index)
                if index == 1:
                    return dict(runtime, fixture_runtime_changed=True)
            return runtime
        def respond(route, request, credential):
            armed.append(True)
            return fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
        lines = []
        with patch.object(review_transport, 'runtime', side_effect=observe_runtime), patch.object(
                review_transport, 'send_request', side_effect=respond):
            result = review_series.run_series(fixture.case.store, fixture.assignment['id'], emit=lines.append)
        self.assertEqual(result, 0, lines)
        refused = [line for line in lines if line.get('error', {}).get('code') == 'review_runtime_changed']
        self.assertEqual(len(refused), 1, lines)
        self.assertEqual(stop_classes.STOP_REGISTRY['review_runtime_changed']['next'], {'kind': 'continue'})
        self.assertTrue(any(line['state'] == 'resume' for line in lines))
        self.assertEqual(len(armed), 1)
        self.assertEqual(lines[-1]['state'], 'done')
        self.assertEqual(len(fixture.case.read_records()['value_review']), 1)
        following = []
        self.assertEqual(review_series.run_series(fixture.case.store, fixture.assignment['id'], emit=following.append), 0)
        self.assertEqual(following[-1]['state'], 'done')
        self.assertFalse(any(line.get('error', {}).get('code') == 'review_runtime_changed' for line in following))


class ClosingConditionTests(unittest.TestCase):
    def read_members(self):
        result = {}
        for path in sorted((ROOT / 'tests/fixtures/series_code_groups').glob('*.json')):
            for group, entry in json.loads(path.read_text()).items():
                for code, sites in entry['members'].items():
                    self.assertNotIn(code, result)
                    result[code] = (group, sites)
        return result

    def read_evidence(self, code, group, sites, report):
        definition = stop_classes.STOP_GROUPS[group]
        row = {key: copy.deepcopy(value) for key, value in definition.items()
               if key not in ('id', 'conditions')}
        row.update(code=code, group=group)
        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch) / 'tests/fixtures/series_code_groups'
            folder.mkdir(parents=True)
            (folder / 'lane.json').write_text(json.dumps({group: {'members': {code: sites}}}))
            return read_group_entries(scratch, report, {code: row}, stop_classes.STOP_GROUPS)

    def test_a_sole_planner_built_payload_shape_gets_payload_and_stored_state(self):
        report = reach.analyze_sources({'pkg/core.py': '''
def plan_next():
    return {'id': 'planned-record'}
def driver():
    execute(plan_next())
def execute(payload):
    if not isinstance(payload, dict):
        raise ResearchError('shape', 'The planner supplied a malformed payload')
'''}, {'driver': ['pkg/core.py:driver']})
        sites = {'pkg/core.py:execute#1': ['payload']}
        self.assertEqual(self.read_evidence('shape', 'stored_state', sites, report),
                         {'stored_state': {'members': {'shape': sites}}})
        with self.assertRaisesRegex(ValueError, 'conditions require named_cause'):
            self.read_evidence('shape', 'stored_state', {'pkg/core.py:execute#1': ['form']}, report)

    def test_actual_planner_built_payload_checks_keep_payload_before_form(self):
        members = self.read_members()
        group, sites = members['value_review_output_mismatch']
        self.assertEqual(group, 'stored_state')
        self.assertEqual(sites, {
            'research_harness/research_decisions.py:record_value_review.prepare#1': ['payload']})
        group, sites = members['review_output_mismatch']
        self.assertEqual(group, 'stored_state')
        self.assertEqual(sites['research_harness/review_protocol.py:record_adjudication.prepare#1'],
                         ['payload'])
        for code, (group, sites) in members.items():
            with self.subTest(code=code):
                if group not in ('stored_state', 'named_cause'):
                    # A group with its own next step, such as visual_text_reading, admits only its own conditions.
                    allowed = set(stop_classes.STOP_GROUPS[group]['conditions'])
                    self.assertTrue(all(set(conditions) <= allowed for conditions in sites.values()))
                    continue
                stored = all(set(conditions) <= {'store', 'content', 'payload'}
                             for conditions in sites.values())
                self.assertEqual(group, 'stored_state' if stored else 'named_cause')

    def test_an_external_unsupported_route_keeps_form_and_named_cause(self):
        group, sites = self.read_members()['invalid_review_route']
        self.assertEqual(group, 'named_cause')
        for function in ('research_harness/review_transport.py:validate_route',
                         'research_harness/review_codex_transport.py:validate_route',
                         'research_harness/review_codex_framed_transport.py:validate_route'):
            with self.subTest(function=function):
                self.assertEqual(sites[function + '#1'], ['form'])
                self.assertEqual(sites[function + '#2'], ['form'])
                self.assertEqual(sites[function + '#3'], ['form', 'limit'])
                self.assertEqual(sites[function + '#4'], ['form', 'limit'])

    def test_a_missing_record_place_is_independent_of_the_payload_shape_place(self):
        report = reach.analyze_sources({'pkg/core.py': '''
def plan_next():
    return {'id': 'planned-record'}
def driver(records):
    execute(plan_next(), records)
def execute(payload, records):
    if not isinstance(payload, dict):
        raise ResearchError('mixed', 'The planner supplied a malformed payload')
    if payload['id'] not in records:
        raise ResearchError('mixed', 'Record the missing workflow record')
'''}, {'driver': ['pkg/core.py:driver']})
        sites = {'pkg/core.py:execute#1': ['payload'], 'pkg/core.py:execute#2': ['record']}
        self.assertEqual(self.read_evidence('mixed', 'named_cause', sites, report),
                         {'named_cause': {'members': {'mixed': sites}}})
        with self.assertRaisesRegex(ValueError, 'conditions require named_cause'):
            self.read_evidence('mixed', 'stored_state', sites, report)
        with self.assertRaisesRegex(ValueError, 'Group member sites differ.*mixed'):
            self.read_evidence('mixed', 'named_cause', {'pkg/core.py:execute#1': ['payload']}, report)

    def test_actual_input_reader_keeps_missing_ancestry_apart_from_retained_content(self):
        group, sites = self.read_members()['review_input_unreadable']
        self.assertEqual(group, 'named_cause')
        function = 'research_harness/review_delivered_evidence.py:_read_authenticated_delivery.project'
        self.assertEqual(sites[function + '#1'], ['record'])
        for number in (2, 3, 4, 5):
            self.assertEqual(sites[function + '#' + str(number)], ['content'])
        report = {'code_sites': {'review_input_unreadable': sorted(sites)}}
        self.read_evidence('review_input_unreadable', group, sites, report)
        missing = {site: conditions for site, conditions in sites.items() if site != function + '#1'}
        with self.assertRaisesRegex(ValueError, 'Group member sites differ.*review_input_unreadable'):
            self.read_evidence('review_input_unreadable', group, missing, report)


class ClosingAdmissionTests(unittest.TestCase):
    def test_only_the_executed_runtime_recovery_has_a_closing_row_of_its_own(self):
        codes = {
            "artifact_sealed", "invalid_input", "unsafe_path", "provider_state_unavailable",
            "review_correction_duplicate", "openness_reading_gap", "openness_search_gap",
            "openness_settling_source", "review_context_repair_history_unrecorded",
            "review_context_repair_pending", "review_observation_acceptance_pending",
            "invalid_review_material", "review_parts_target_label_missing", "review_runtime_changed",
        }
        own = {code for code in codes if "group" not in stop_classes.STOP_REGISTRY[code]}
        self.assertEqual(own, {"review_runtime_changed"})
        self.assertEqual(stop_classes.STOP_REGISTRY["review_runtime_changed"]["next"], {"kind": "continue"})
        for code in codes - own:
            self.assertEqual(stop_classes.STOP_REGISTRY[code]["group"], "named_cause")



class MalformedContextAnswerTests(unittest.TestCase):
    def test_malformed_peer_is_reported_before_scanning_refusal_replacements(self):
        from research_harness.review_continuation import list_chain_heads

        fixture = continuation_fixtures.ContinuationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        case = fixture.case
        healthy = case.assign('bar', 'second', **case.deliver_supporting('second'))
        damaged = copy.deepcopy(fixture.assignment)
        for context in (['bad'], {'output_refusal': None}, {'output_refusal': ['bad']},
                        {'output_refusal': {}}, {'output_refusal': {'assignment_id': []}}):
            with self.subTest(context=context):
                damaged['context'] = context
                case.put('review_assignment', damaged['id'], damaged)
                failures = []
                heads = list_chain_heads(case.read_records(), case.artifacts, context_failures=failures)
                self.assertEqual(heads, [healthy['id']])
                self.assertEqual([(row['assignment_id'], row['code']) for row in failures],
                                 [(damaged['id'], 'invalid_review_context')])

    def prepare_context_case(self, *, malformed):
        fixture = continuation_fixtures.ContinuationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        case = fixture.case
        healthy = case.assign('bar', 'second', **case.deliver_supporting('second'))
        # Retained content can be malformed even though the assignment command
        # validated its original payload. Keep the stored packet and prompt exact.
        assignment = copy.deepcopy(fixture.assignment)
        if malformed == 'ancestry':
            assignment['history_assignment_id'] = ''
        else:
            assignment['subject']['statement_id'] = 'missing-statement'
        case.put('review_assignment', assignment['id'], assignment)
        case.put('problem_post', 'another-post', dict(id='another-post',
            statement_digest='another-synthetic-claim', recorded_revision=1))
        return fixture, healthy

    def run_printed_answers(self, fixture, *, code):
        case = fixture.case
        rows = []
        self.assertEqual(review_series.run_series(case.store, fixture.assignment['id'], emit=rows.append), 3)
        last = rows[-1]
        self.assertEqual((last['code'], last['stop_class']), (code, 'decision'))
        self.assertNotIn('unlisted', last['details'])
        answers = last['details']['decision']['then']
        self.assertEqual([step['command'] for answer in answers for step in answer['steps']],
                         ['record-check', 'status'])
        before = case.store.snapshot()
        statuses = []
        for answer in answers:
            for step in answer['steps']:
                argv = step['command'].split()
                for option, value in step['arguments'].items():
                    argv.extend((option, str(value)))
                report = cli.run(cli.build_parser().parse_args(argv))
                if step['command'] == 'record-check':
                    stop = next(row for row in report['series_stops'] if row['id'] == last['stop_id'])
                    self.assertEqual((stop['code'], stop['stop_class']), (code, 'decision'))
                    self.assertTrue(stop['listed'])
                    self.assertNotIn('finding', stop)
                else:
                    self.assertIsInstance(report['obligations'], list)
                    self.assertIn('next', report)
                    statuses.append(report)
        self.assertEqual(case.store.snapshot(), before)
        return statuses[0]

    def test_malformed_retained_ancestry_keeps_every_named_cause_answer_executable(self):
        fixture, healthy = self.prepare_context_case(malformed='ancestry')
        packet = fixture.case.artifacts.read(fixture.assignment['packet'])
        prompt = copy.deepcopy(fixture.assignment['prompt'])
        report = self.run_printed_answers(fixture, code='invalid_review_context')
        failures = report['review_context_failures']
        self.assertEqual([(row['assignment_id'], row['code']) for row in failures],
                         [('bar-first', 'invalid_review_context')])
        self.assertTrue(failures[0]['message'])
        pending = next(row for row in report['obligations']
                       if row.get('assignment_id') == healthy['id'])
        self.assertEqual((pending['code'], pending['next'][0]['state']), ('review_output_pending', 'run'))
        self.assertEqual(pending['next'][0]['payload']['assignment_id'], healthy['id'])
        self.assertEqual(fixture.case.artifacts.read(fixture.assignment['packet']), packet)
        self.assertEqual(fixture.case.read_records()['review_assignment']['bar-first']['prompt'], prompt)

    def test_mixed_record_and_content_code_keeps_the_inspection_answers(self):
        fixture, healthy = self.prepare_context_case(malformed='statement')
        report = self.run_printed_answers(fixture, code='problem_statement_missing')
        self.assertEqual([(row['assignment_id'], row['code']) for row in report['review_context_failures']],
                         [('bar-first', 'problem_statement_missing')])
        self.assertIsInstance(report['next'], dict)
        pending = next(row for row in report['obligations']
                       if row.get('assignment_id') == healthy['id'])
        self.assertEqual(pending['next'][0]['state'], 'run')

    def test_status_names_each_failure_without_dropping_unaffected_contexts(self):
        fixture, healthy = self.prepare_context_case(malformed='ancestry')
        case = fixture.case
        second = copy.deepcopy(healthy)
        second['id'] = 'missing-statement-context'
        second['slot_id'] = second['id']
        second['subject']['statement_id'] = 'missing-statement'
        case.put('review_assignment', second['id'], second)
        before = case.store.snapshot()
        report = cli.status_report(case.store)
        self.assertEqual({(row['assignment_id'], row['code']) for row in report['review_context_failures']},
                         {('bar-first', 'invalid_review_context'),
                          ('missing-statement-context', 'problem_statement_missing')})
        pending = next(row for row in report['obligations']
                       if row.get('assignment_id') == healthy['id'])
        self.assertEqual(pending['next'][0]['state'], 'run')
        self.assertEqual(case.store.snapshot(), before)

    def test_unposted_malformed_context_returns_all_printed_inspections(self):
        for field in ('context', 'staged_review', 'context_repair', 'staged_upgrade', 'output_refusal',
                      'history_assignment_id', 'prior_assignment_id'):
            malformed_values = ([], {}) if field.endswith('assignment_id') else ('bad', ['bad'])
            if field == 'output_refusal':
                malformed_values = (None, 'bad', ['bad'], {}, {'assignment_id': []})
            for malformed in malformed_values:
                with self.subTest(field=field, malformed=malformed):
                    fixture = continuation_fixtures.ContinuationTests()
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    case = fixture.case
                    healthy = case.assign('bar', 'second', **case.deliver_supporting('second'))
                    assignment = copy.deepcopy(fixture.assignment)
                    if field in ('context', 'staged_review', 'history_assignment_id'):
                        assignment[field] = malformed
                    else:
                        assignment['context'][field] = malformed
                    case.put('review_assignment', assignment['id'], assignment)
                    report = self.run_printed_answers(fixture, code='invalid_review_context')
                    self.assertEqual([(row['assignment_id'], row['code'])
                                      for row in report['review_context_failures']],
                                     [('bar-first', 'invalid_review_context')])
                    self.assertTrue(any(row.get('assignment_id') == healthy['id']
                                        for row in report['obligations']))
                    self.assertIn('problem_value_missing', {row['code'] for row in report['obligations']})

    def test_unposted_missing_controller_returns_all_printed_inspections(self):
        for controller in ('absent', '', ['absent']):
            with self.subTest(controller=controller):
                fixture = continuation_fixtures.ContinuationTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                case = fixture.case
                case.assign('bar', 'second', **case.deliver_supporting('second'))
                assignment = copy.deepcopy(fixture.assignment)
                assignment['staged_review'] = dict(kind='task', controller_assignment_id=controller)
                case.put('review_assignment', assignment['id'], assignment)
                report = self.run_printed_answers(fixture, code='invalid_review_context')
                self.assertEqual([(row['assignment_id'], row['code'])
                                  for row in report['review_context_failures']],
                                 [('bar-first', 'invalid_review_context')])
                self.assertIn('problem_value_missing', {row['code'] for row in report['obligations']})

    def test_malformed_context_preserves_nonreview_obligations(self):
        fixture, healthy = self.prepare_context_case(malformed='ancestry')
        report = cli.status_report(fixture.case.store)
        self.assertIn('problem_value_missing', {row['code'] for row in report['obligations']})
        self.assertFalse(report['ready'])
        self.assertEqual(report['next']['code'], 'problem_slate_missing')
        self.assertTrue(any(row.get('assignment_id') == healthy['id'] for row in report['obligations']))

    def test_unreadable_retained_packet_returns_its_code_and_preserves_other_heads(self):
        fixture = continuation_fixtures.ContinuationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        case = fixture.case
        healthy = case.assign('bar', 'second', **case.deliver_supporting('second'))
        assignment = copy.deepcopy(fixture.assignment)
        assignment['packet'] = case.artifacts.put(b'{', 'application/json')
        from review_parts_fixtures import make_packet, plan
        assignment['review_parts'] = plan(make_packet())['review_parts']
        assignment['review_parts']['series_assignment_id'] = assignment['id']
        case.put('review_assignment', assignment['id'], assignment)
        report = self.run_printed_answers(fixture, code='review_input_unreadable')
        self.assertEqual([(row['assignment_id'], row['code']) for row in report['review_context_failures']],
                         [('bar-first', 'review_input_unreadable')])
        pending = next(row for row in report['obligations']
                       if row.get('assignment_id') == healthy['id'])
        self.assertEqual(pending['next'][0]['state'], 'run')


if __name__ == '__main__':
    unittest.main()
