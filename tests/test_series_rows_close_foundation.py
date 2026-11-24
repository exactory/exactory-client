"""Import boundaries, writing sites and evidence guards for registry-rows part 1."""

import ast
import copy
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci import series_code_reach as reach
from ci.series_code_groups import read_group_entries
from research_harness import automatic_overrides, review_continuation, stop_classes
from research_harness.errors import ResearchError
from research_harness.artifacts import ArtifactStore


class ImportClosureTests(unittest.TestCase):
    def build_sources(self):
        return {'first/core.py': '''
def entry(obj):
    obj.check()
''', 'second/core.py': '''
import first.core
class Check:
    def check(self):
        ResearchError('second', '')
def entry(obj):
    first.core.entry(obj)
'''}

    def test_untyped_receiver_stays_within_each_roots_import_closure(self):
        sources = self.build_sources()
        roots = {'first': ['first/core.py:entry'], 'second': ['second/core.py:entry']}
        report = reach.analyze_sources(sources, roots)
        self.assertEqual(report['by_command']['first'], [])
        self.assertEqual(report['by_command']['second'], ['second'])
        self.assertNotIn('second/core.py:Check.check', report['by_command_functions']['first'])
        sources['first/core.py'] = 'import second.core\n' + sources['first/core.py']
        self.assertEqual(reach.analyze_sources(sources, roots)['by_command']['first'], ['second'])

    def test_multiple_roots_keep_their_own_import_boundaries(self):
        sources = self.build_sources()
        sources['second/core.py'] = '''
def entry():
    pass
class Check:
    def check(self):
        ResearchError('unimported_method', '')
'''
        report = reach.analyze_sources(sources, {'both': ['first/core.py:entry', 'second/core.py:entry']})
        self.assertEqual(report['reachable'], [])

    def test_imports_inside_uncalled_functions_join_the_module_closure(self):
        sources = self.build_sources()
        self.assertEqual(reach.analyze_sources(sources, {'first': ['first/core.py:entry']})['reachable'], [])
        sources['first/core.py'] += '\ndef unused():\n    import second.core\n'
        self.assertEqual(reach.analyze_sources(sources, {'first': ['first/core.py:entry']})['reachable'], ['second'])

    def test_transitive_relative_and_package_imports_join_the_closure(self):
        sources = self.build_sources()
        self.assertEqual(reach.analyze_sources(sources, {'first': ['first/core.py:entry']})['reachable'], [])
        sources['first/core.py'] = 'from . import bridge\n' + sources['first/core.py']
        sources['first/bridge.py'] = 'from second import core\n'
        sources['second/__init__.py'] = 'from . import startup\n'
        sources['second/startup.py'] = "ResearchError('startup', '')\n"
        self.assertEqual(reach.analyze_sources(sources, {'first': ['first/core.py:entry']})['reachable'],
                         ['second', 'startup'])

    def test_literal_import_module_is_followed_including_a_local_alias(self):
        for expression in ("load('second.core')", "load('.core', 'second')", "load(name='second.core')"):
            with self.subTest(expression=expression):
                sources = self.build_sources()
                self.assertEqual(reach.analyze_sources(sources, {'first': ['first/core.py:entry']})['reachable'], [])
                sources['first/core.py'] += ("\ndef unused():\n    from importlib import import_module as load\n    "
                                             + expression + '\n')
                report = reach.analyze_sources(sources, {'first': ['first/core.py:entry']})
                self.assertEqual(report['reachable'], ['second'])

    def test_computed_import_adds_its_package_and_is_listed(self):
        sources = {'first/core.py': '''
import importlib
def entry(obj, name):
    importlib.import_module(name)
    obj.check()
''', 'first/adapter.py': "class Adapter:\n    def check(self):\n        ResearchError('local', '')\n",
                   'second/core.py': "class Adapter:\n    def check(self):\n        ResearchError('foreign', '')\n"}
        report = reach.analyze_sources(sources, {'entry': ['first/core.py:entry']})
        self.assertEqual(report['reachable'], ['local'])
        self.assertIn(dict(path='first/core.py', function='entry', expression='importlib.import_module(name)'),
                      report['unfollowed_sites'])

    def test_import_time_functions_and_codes_obey_the_root_import_closure(self):
        sources = {'first/core.py': 'def entry():\n    pass\n', 'second/core.py': '''
ResearchError('foreign_module', '')
def startup():
    ResearchError('foreign_startup', '')
startup()
'''}
        report = reach.analyze_sources(sources, {'first': ['first/core.py:entry']})
        self.assertEqual(report['reachable'], [])


class CrossingTests(unittest.TestCase):
    def require_pending(self, report, code):
        self.assertIn(code, report['reachable'])
        self.assertIn(code, reach.build_unreviewed_rows(report, {}))
        self.assertNotIn(code, reach.build_unreviewed_rows(report, {code: {}}))

    def test_declared_computed_import_reaches_the_other_package(self):
        sources = {'first/core.py': """
import importlib
def entry(name):
    importlib.import_module(name).run()
""", 'second/core.py': """
from .checks import inspect
def execute():
    inspect()
""", 'second/checks.py': "def inspect():\n    ResearchError('loaded', '')\n"}
        roots = {'driver': ['first/core.py:entry']}
        before = reach.analyze_sources(sources, roots)
        self.assertEqual(before['reachable'], [])
        site = dict(path='first/core.py', function='entry', expression='importlib.import_module(name)')
        self.assertIn(site, before['unfollowed_sites'])
        report = reach.analyze_sources(sources, roots, declared_edges={
            'first/core.py:entry': {'second/core.py:execute': 'The computed import loads the selected extension.'}})
        self.require_pending(report, 'loaded')
        self.assertIn(site, report['unfollowed_sites'])
        self.assertIn('second/checks.py:inspect', report['by_command_functions']['driver'])

    def test_declared_host_receiver_reaches_its_method(self):
        sources = {'first/core.py': "def entry(receiver):\n    receiver.inspect()\n", 'second/core.py': """
class Receiver:
    def inspect(self):
        ResearchError('host_receiver', '')
"""}
        roots = {'driver': ['first/core.py:entry']}
        self.assertEqual(reach.analyze_sources(sources, roots)['reachable'], [])
        report = reach.analyze_sources(sources, roots, declared_edges={
            'first/core.py:entry': {'second/core.py:Receiver.inspect': 'The host supplies this receiver.'}})
        self.require_pending(report, 'host_receiver')

    def test_declared_child_code_producer_crosses_the_package_boundary(self):
        sources = {'first/core.py': "def entry(child):\n    ResearchError(child['code'], '')\n", 'second/core.py': """
from .checks import inspect
def result():
    inspect()
""", 'second/checks.py': "def inspect():\n    return dict(code='child_code')\n"}
        roots = {'driver': ['first/core.py:entry']}
        with self.assertRaisesRegex(ValueError, 'Undeclared computed code site'):
            reach.analyze_sources(sources, roots)
        report = reach.analyze_sources(sources, roots, {
            "first/core.py:entry:child['code']": {'sources': ['second/core.py:result']}})
        self.require_pending(report, 'child_code')
        self.assertEqual(report['code_sites']['child_code'], ['second/checks.py:inspect#1'])

    def test_crossings_expand_until_the_modules_and_functions_stop_growing(self):
        sources = {'first/core.py': 'def entry():\n    pass\n', 'second/core.py': """
from . import support
def load(child):
    ResearchError(child['code'], '')
""", 'second/support.py': "def startup():\n    ResearchError('initialized', '')\nstartup()\n",
                   'third/core.py': "def result():\n    return dict(code='nested_child')\n"}
        roots = {'driver': ['first/core.py:entry'], 'unrelated': ['first/core.py:unused']}
        sources['first/core.py'] += 'def unused():\n    pass\n'
        report = reach.analyze_sources(sources, roots, {
            "second/core.py:load:child['code']": {'sources': ['third/core.py:result']}}, {
            'first/core.py:entry': {'second/core.py:load': 'Load an external child adapter.'}})
        self.assertEqual(report['by_command']['driver'], ['initialized', 'nested_child'])
        self.assertEqual(report['by_command']['unrelated'], [])

    def test_declared_import_time_edge_runs_when_the_module_is_loaded(self):
        sources = {'first/core.py': """
import importlib
importlib.import_module(selected).execute()
def entry():
    pass
""", 'second/core.py': "def execute():\n    ResearchError('import_time_crossing', '')\n"}
        roots = {'driver': ['first/core.py:entry']}
        self.assertEqual(reach.analyze_sources(sources, roots)['reachable'], [])
        report = reach.analyze_sources(sources, roots, declared_edges={
            'first/core.py:<module>': {'second/core.py:execute': 'Module initialization loads the extension.'}})
        self.require_pending(report, 'import_time_crossing')

    def test_crossing_modules_include_their_package_initializers(self):
        sources = {'first/core.py': 'def entry():\n    pass\n',
                   'second/core.py': 'def execute():\n    pass\n',
                   'second/__init__.py': "from third import startup\n",
                   'third/startup.py': "ResearchError('package_initialized', '')\n"}
        report = reach.analyze_sources(sources, {'driver': ['first/core.py:entry']}, declared_edges={
            'first/core.py:entry': {'second/core.py:execute': 'Load an extension with a package initializer.'}})
        self.require_pending(report, 'package_initialized')

    def test_script_only_sources_do_not_expand_an_imported_root(self):
        sources = {'first/core.py': """
def entry():
    pass
if __name__ == '__main__':
    ResearchError(child['code'], '')
""", 'second/core.py': "def result():\n    return dict(code='script_only')\n"}
        report = reach.analyze_sources(sources, {'driver': ['first/core.py:entry']}, {
            "first/core.py:<module>:child['code']": {'sources': ['second/core.py:result']}})
        self.assertEqual(report['reachable'], [])
        self.assertNotIn('second/core.py:result', report['by_command_functions']['driver'])


class WritingSiteTests(unittest.TestCase):
    def analyze_places(self):
        return reach.analyze_sources({'pkg/core.py': """
def entry(payload, records):
    if not isinstance(payload, dict):
        ResearchError('mixed', '')
    if payload['id'] not in records:
        ResearchError('mixed', '')
"""}, {'driver': ['pkg/core.py:entry']})

    def read_members(self, report, sites, group='named_cause'):
        definition = stop_classes.STOP_GROUPS.get(group, {})
        row = {key: copy.deepcopy(value) for key, value in definition.items() if key not in ('id', 'conditions')}
        row.update(code='mixed', group=group)
        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch) / 'tests/fixtures/series_code_groups'
            folder.mkdir(parents=True)
            (folder / 'lane.json').write_text(json.dumps({group: {'members': {'mixed': sites}}}))
            return read_group_entries(scratch, report, {'mixed': row}, stop_classes.STOP_GROUPS)

    def test_two_refusals_in_one_function_have_separate_places_and_conditions(self):
        report = self.analyze_places()
        sites = {'pkg/core.py:entry#1': ['form'], 'pkg/core.py:entry#2': ['record']}
        self.assertEqual(report['code_sites']['mixed'], list(sites))
        self.assertEqual(reach.build_unreviewed_rows(report, {})['mixed']['sites'], list(sites))
        self.assertEqual(self.read_members(report, sites), {'named_cause': {'members': {'mixed': sites}}})

    def test_missing_a_place_fails_the_member_guard(self):
        report = self.analyze_places()
        with self.assertRaisesRegex(ValueError, 'Group member sites differ.*mixed'):
            self.read_members(report, {'pkg/core.py:entry#1': ['form']})

    def test_only_direct_writers_have_places_and_terminal_places_take_precedence(self):
        sources = {'pkg/core.py': """
def refuse(code):
    raise ResearchError(code, '')
def producer():
    refuse('direct')
    ResearchError(error.code, '')
    return dict(code='entry_only')
def entry(error):
    producer()
    dict(code='direct')
    raise ResearchError(error.code, '')
"""}
        declarations = {'pkg/core.py:entry:error.code': {'sources': ['pkg/core.py:producer']},
                        'pkg/core.py:producer:error.code': {'codes': ['declared']}}
        report = reach.analyze_sources(sources, {'driver': ['pkg/core.py:entry']}, declarations)
        self.assertEqual(report['code_sites']['direct'], ['pkg/core.py:producer#1'])
        self.assertEqual(report['code_sites']['declared'], ['pkg/core.py:producer#1'])
        self.assertEqual(report['code_sites']['entry_only'], ['pkg/core.py:producer#1'])
        sources['pkg/core.py'] += "\nResearchError('entry_only', '')\n"
        report = reach.analyze_sources(sources, {'driver': ['pkg/core.py:entry']}, declarations)
        self.assertEqual(report['code_sites']['entry_only'], ['pkg/core.py:<module>#1'])

    def test_numbers_follow_source_positions_and_are_separate_for_each_code(self):
        report = reach.analyze_sources({'pkg/core.py': """
def refuse(code):
    ResearchError(code, '')
def entry():
    refuse('first'); refuse('second'); refuse('first')
"""}, {'driver': ['pkg/core.py:entry']})
        self.assertEqual(report['code_sites'], {'first': ['pkg/core.py:entry#1', 'pkg/core.py:entry#2'],
                                              'second': ['pkg/core.py:entry#1']})

    def test_a_constructor_and_a_plan_in_one_function_keep_both_places(self):
        report = reach.analyze_sources({'research_harness/review_continuation.py': """
def plan_next():
    ResearchError('mixed', '')
    return dict(state='stop', code='mixed')
"""}, {'driver': ['research_harness/review_continuation.py:plan_next']})
        self.assertEqual(report['code_sites']['mixed'], [
            'research_harness/review_continuation.py:plan_next#1',
            'research_harness/review_continuation.py:plan_next#2'])
        self.assertEqual([report['code_places']['mixed'][site]['expression']
                          for site in report['code_sites']['mixed']], ["'mixed'", "'mixed'"])

    def test_each_place_accepts_multiple_sorted_conditions(self):
        sites = {'pkg/core.py:entry#1': ['content', 'form'], 'pkg/core.py:entry#2': ['record']}
        self.assertEqual(self.read_members(self.analyze_places(), sites), {'named_cause': {'members': {'mixed': sites}}})

    def test_condition_lists_must_be_nonempty_unique_and_sorted(self):
        for conditions in ([], ['form', 'content'], ['form', 'form'], 'form', [1]):
            with self.subTest(conditions=conditions), self.assertRaisesRegex(ValueError, 'sorted.*conditions'):
                self.read_members(self.analyze_places(), {
                    'pkg/core.py:entry#1': conditions, 'pkg/core.py:entry#2': ['record']})

    def test_stored_state_rejects_a_condition_outside_its_three(self):
        with self.assertRaisesRegex(ValueError, 'conditions require named_cause'):
            self.read_members(self.analyze_places(), {
                'pkg/core.py:entry#1': ['form'], 'pkg/core.py:entry#2': ['content']}, group='stored_state')

    def test_named_cause_rejects_a_union_containing_only_stored_conditions(self):
        with self.assertRaisesRegex(ValueError, 'conditions require stored_state'):
            self.read_members(self.analyze_places(), {
                'pkg/core.py:entry#1': ['payload', 'store'], 'pkg/core.py:entry#2': ['content']})

    def test_a_condition_not_defined_by_the_group_fails(self):
        with self.assertRaisesRegex(ValueError, 'Unknown group condition.*unknown'):
            self.read_members(self.analyze_places(), {
                'pkg/core.py:entry#1': ['unknown'], 'pkg/core.py:entry#2': ['record']})


class NamedCauseRowTests(unittest.TestCase):
    def test_named_cause_offers_record_check_and_status_as_separate_inspections(self):
        self.assertIn('named_cause', stop_classes.STOP_GROUPS)
        group = stop_classes.STOP_GROUPS['named_cause']
        self.assertEqual((group['class'], group['owner']), ('decision', 'D2'))
        row = dict(code='mixed', group='named_cause', **{key: copy.deepcopy(value)
            for key, value in group.items() if key not in ('id', 'conditions')})
        stop_classes.validate_registry({'mixed': row})
        with patch.dict(stop_classes.STOP_REGISTRY, mixed=row):
            terminal = stop_classes.describe_terminal('mixed')
        self.assertEqual(terminal, dict(state='stop', code='mixed', stop_class='decision', details=dict(
            decision=dict(owner_decides='How should the cause that the refusal names be answered?', then=[
                dict(answer='Check the retained record', steps=[dict(command='record-check')]),
                dict(answer='List what the workspace owes', steps=[dict(command='status')])]))))
        self.assertEqual(set(group['conditions']), {'entry', 'record', 'environment', 'form', 'limit',
                                                  'harness', 'store', 'content', 'payload'})
        for condition in ('store', 'content', 'payload'):
            self.assertIs(group['conditions'][condition], stop_classes.STOP_GROUPS['stored_state']['conditions'][condition])


class ExcludedCodeRecorderTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_driver_recorder_rejects_a_source_written_code_excluded_by_rule_ten(self):
        from research_harness import cli, review_series
        from test_series_code_reach import record_reachable_errors, FIXTURE
        report = json.loads(FIXTURE.read_text())
        self.assertIn('contract_changed', report['emitted'])
        self.assertNotIn('contract_changed', reach.read_command_codes(report, 'run_series'))
        def plan(records, artifacts, assignment_id):
            return dict(state='run', command='review-run', payload={'assignment_id': assignment_id},
                        head_id=assignment_id, chain='session', progress={})
        def refuse(store, payload, **identity):
            raise ResearchError('contract_changed', 'The external controller contract changed.')
        import test_review_continuation as continuation_fixtures
        fixture = continuation_fixtures.ContinuationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        store = fixture.case.store
        head = fixture.assignment['id']
        with patch.object(review_continuation, 'plan_next', plan), patch.object(
                review_series, 'plan_next', plan), patch.dict(cli.OPERATIONS, {'review-run': refuse}):
            with self.assertRaisesRegex(AssertionError, 'constructed unreachable code.*contract_changed'):
                with record_reachable_errors('run_series'):
                    review_series.run_series(store, head, emit=lambda row: None)
        def plan_terminal(*args):
            return dict(state='stop', code='contract_changed', head_id=head, details={})
        with patch.object(review_continuation, 'plan_next', plan_terminal), patch.object(
                review_series, 'plan_next', plan_terminal):
            with self.assertRaisesRegex(AssertionError, 'returned an unlisted code.*contract_changed'):
                with record_reachable_errors('run_series'):
                    review_series.run_series(store, head, emit=lambda row: None)


class LiteralRefusalTests(unittest.TestCase):
    def test_all_refusals_preserve_their_complete_codes_messages_and_details(self):
        expected = json.loads((Path(__file__).parent / 'fixtures/automatic_override_refusals.json').read_text())
        tree = ast.parse(inspect.getsource(automatic_overrides))
        calls = sorted((node for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == '_refuse'), key=lambda node: node.lineno)
        self.assertEqual(len(calls), len(expected))
        values = dict(stop={'code': 'review_series_no_progress', 'read_errors': {'scope': {'code': 'artifact_missing'}}},
                      classification='harness_defect',
                      report={'obligations': [{'code': 'pending'}]}, row={'id': 'review'},
                      gate='submitted', public_action='POST /synthetic')
        for call, refusal in zip(calls, expected):
            with self.subTest(code=refusal['code'], message=refusal['message']):
                self.assertIsInstance(call.args[0], ast.Constant)
                self.assertEqual(call.args[0].value, refusal['code'])
                namespace = dict(values, _refuse=automatic_overrides._refuse)
                with self.assertRaises(ResearchError) as caught:
                    eval(compile(ast.Expression(call), '<refusal>', 'eval'), namespace)
                self.assertEqual(caught.exception.as_dict(), refusal)

    def test_default_decision_does_not_depend_on_a_registry_sentinel(self):
        row = dict(code='unrecognized', **{'class': 'decision'}, owner='D2', next=dict(kind='done'))
        assignment = dict(id='head')
        with tempfile.TemporaryDirectory() as scratch, patch.dict(stop_classes.STOP_REGISTRY, unrecognized=row), patch.object(
                review_continuation, '_find_head', return_value=assignment), patch.object(
                review_continuation, '_describe_chain', return_value='session'), patch.object(
                review_continuation, '_describe_progress', return_value={}), patch.object(
                review_continuation, '_plan_head', side_effect=ResearchError('artifact_missing', 'synthetic')), patch(
                'research_harness.problem_delivery.find_review_post', return_value=None), patch(
                'research_harness.review_plan_steps.complete_plan', side_effect=ResearchError('invalid_input', 'details')):
            plan = review_continuation.plan_next({}, ArtifactStore(Path(scratch)), 'head')
        expected = stop_classes.describe_unlisted_step()
        expected.pop('kind')
        self.assertEqual(plan['details']['decision'], expected)
        self.assertEqual(plan['code'], 'invalid_input')

    def test_missing_head_default_keeps_the_unlisted_decision(self):
        row = dict(code='unrecognized', **{'class': 'decision'}, owner='D2', next=dict(kind='done'))
        handoff = dict(code='synthetic_handoff', **{'class': 'decision'}, owner='D2',
                       next=dict(kind='handoff', command='status'))
        with tempfile.TemporaryDirectory() as scratch, patch.dict(stop_classes.STOP_REGISTRY, unrecognized=row, synthetic_handoff=handoff), patch.object(
                review_continuation, '_find_head', side_effect=ResearchError('synthetic_handoff', 'synthetic')):
            plan = review_continuation.plan_next({}, ArtifactStore(Path(scratch)), 'head')
        expected = stop_classes.describe_unlisted_step()
        expected.pop('kind')
        self.assertEqual(plan['details']['decision'], expected)
        self.assertEqual(plan['state'], 'stop')
        self.assertTrue(plan['details']['unlisted'])

    def test_status_has_its_public_analysis_root(self):
        self.assertEqual(reach.EXTERNAL_COMMAND_ROOTS['status'], ('research_harness/cli.py:status_report',))


if __name__ == '__main__':
    unittest.main()
