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

    def test_computed_sources_use_the_root_closure_without_changing_the_domain_for_passes(self):
        sources = self.build_sources()
        sources['first/core.py'] = "def entry(error):\n    ResearchError(error.code, '')\n"
        declaration = {'sources': ['second/core.py:Check.check']}
        for passes in ('error', 'plan', 'entry'):
            with self.subTest(passes=passes):
                site = 'first/core.py:entry:error.code'
                declarations = {site: dict(declaration, passes=passes)}
                report = reach.analyze_sources(sources, {'first': ['first/core.py:entry'],
                    'second': ['second/core.py:entry']}, declarations)
                self.assertEqual(report['by_command']['first'], [])
                self.assertEqual(report['by_command']['second'], ['second'])
                first = reach.analyze_sources(sources, {'first': ['first/core.py:entry']}, declarations)
                self.assertEqual(first['computed_sites'][site]['values'], [])

    def test_import_time_functions_and_codes_obey_the_root_import_closure(self):
        sources = {'first/core.py': 'def entry():\n    pass\n', 'second/core.py': '''
ResearchError('foreign_module', '')
def startup():
    ResearchError('foreign_startup', '')
startup()
'''}
        report = reach.analyze_sources(sources, {'first': ['first/core.py:entry']})
        self.assertEqual(report['reachable'], [])


class WritingSiteTests(unittest.TestCase):
    def test_only_direct_writers_are_sites_and_terminal_sites_take_precedence_over_entries(self):
        sources = {'pkg/core.py': '''
def refuse(code):
    raise ResearchError(code, '')
def producer():
    refuse('direct')
    ResearchError(error.code, '')
    return dict(code='entry_only')
def entry(error):
    producer()
    return dict(code='direct')
    raise ResearchError(error.code, '')
'''}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']}, {
            'pkg/core.py:entry:error.code': {'sources': ['pkg/core.py:producer'], 'passes': 'error'},
            'pkg/core.py:producer:error.code': {'codes': ['declared']}})
        self.assertEqual(report['code_sites']['direct'], ['pkg/core.py:producer'])
        self.assertEqual(report['code_sites']['entry_only'], ['pkg/core.py:producer'])
        pending = reach.build_unreviewed_rows(report, {})
        self.assertEqual(pending['direct']['sites'], ['pkg/core.py:producer'])
        self.assertEqual(report['code_sites']['declared'], ['pkg/core.py:producer'])
        sources['pkg/core.py'] += "\nResearchError('entry_only', '')\n"
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']}, {
            'pkg/core.py:entry:error.code': {'sources': ['pkg/core.py:producer'], 'passes': 'error'},
            'pkg/core.py:producer:error.code': {'codes': ['declared']}})
        self.assertEqual(report['code_sites']['entry_only'], ['pkg/core.py:<module>'])

    def test_sources_require_a_valid_passes_member(self):
        for passes in (None, 'unknown'):
            with self.subTest(passes=passes):
                declaration = {'sources': ['pkg/core.py:producer']}
                if passes is not None:
                    declaration['passes'] = passes
                with self.assertRaisesRegex(ValueError, 'sources need passes'):
                    reach.analyze_sources({'pkg/core.py': '''
def producer():
    ResearchError('direct', '')
def entry(error):
    ResearchError(error.code, '')
'''}, {'entry': ['pkg/core.py:entry']}, {'pkg/core.py:entry:error.code': declaration})



class NoSeriesEndingGuardTests(unittest.TestCase):
    def read_entries(self, sources, members, declarations=None, edges=None, roots=None):
        report = reach.analyze_sources(sources, roots or {'run_series': ['pkg/core.py:entry']}, declarations)
        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch) / 'tests/fixtures/series_code_groups'
            folder.mkdir(parents=True)
            (folder / 'lane.json').write_text(json.dumps({'no_series_ending':
                dict(members=members, edges=edges or {})}))
            groups = read_group_entries(scratch, report, {}, stop_classes.STOP_GROUPS)
        return report, groups

    def test_entry_members_have_the_r56_row_without_a_registry_entry_or_pending_row(self):
        report, groups = self.read_entries({'pkg/core.py': '''
def entry():
    return dict(code='report_only')
'''}, {'report_only': {'pkg/core.py:entry': 'entry'}})
        self.assertEqual(reach.build_unreviewed_rows(report, {}, groups), {})
        reach.validate_registry_coverage(dict(report, groups=groups, unreviewed_rows={}), {})
        with self.assertRaisesRegex(AssertionError, 'Reviewed codes must leave unreviewed_rows'):
            reach.validate_registry_coverage(dict(report, groups=groups, unreviewed_rows={'report_only': {}}), {})
        self.assertEqual(stop_classes.STOP_GROUPS['no_series_ending']['next'], stop_classes.describe_unlisted_step())

    def test_refuses_an_entry_that_a_computed_site_can_pass_on(self):
        sources = {'pkg/core.py': '''
def producer():
    return dict(code='entry_code')
def entry(issue):
    producer()
    ResearchError(issue['code'], '')
'''}
        with self.assertRaisesRegex(ValueError, 'entry can end a series.*entry_code'):
            self.read_entries(sources, {'entry_code': {'pkg/core.py:producer': 'entry'}}, {
                "pkg/core.py:entry:issue['code']": {'sources': ['pkg/core.py:producer'], 'passes': 'entry'}})

    def test_refuses_an_entry_with_a_constructor_or_a_plan_site(self):
        for path, body in (('pkg/core.py', "ResearchError('entry_code', '')"),
                           ('research_harness/review_continuation.py', "return dict(state='stop', code='entry_code')")):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, 'entry can end a series.*entry_code'):
                    self.read_entries({path: 'def entry():\n    ' + body + '\n'},
                        {'entry_code': {path + ':entry': 'entry'}}, roots={'run_series': [path + ':entry']})

    def test_no_call_edges_must_remain_in_the_series_graph_and_reach_the_site(self):
        sources = {'pkg/core.py': '''
class Archive:
    def write(self):
        ResearchError('false_match', '')
def entry(stream):
    stream.write()
def outside(stream):
    stream.write()
'''}
        members = {'false_match': {'pkg/core.py:Archive.write': 'no_call'}}
        valid = {'false_match': {'pkg/core.py:Archive.write':
            dict(caller='pkg/core.py:entry', expression='stream.write()')}}
        report, groups = self.read_entries(sources, members, edges=valid)
        report['graph']['pkg/core.py:entry'].remove('pkg/core.py:Archive.write')
        from ci.series_code_groups import validate_no_series_ending
        with self.assertRaisesRegex(ValueError, 'edge is absent from the series graph'):
            validate_no_series_ending(report, members, valid)
        attribute = copy.deepcopy(valid)
        attribute['false_match']['pkg/core.py:Archive.write']['expression'] = 'stream.write'
        self.read_entries(sources, members, edges=attribute)
        for caller, expression in (('pkg/core.py:outside', 'stream.write()'),
                                    ('pkg/core.py:entry', 'stream.read()')):
            with self.subTest(caller=caller, expression=expression):
                edges = copy.deepcopy(valid)
                edges['false_match']['pkg/core.py:Archive.write'].update(caller=caller, expression=expression)
                with self.assertRaisesRegex(ValueError, 'edge is absent from the series graph'):
                    self.read_entries(sources, members, edges=edges)

    def test_no_call_requires_evidence_for_every_site_and_no_other_site(self):
        sources = {'pkg/core.py': "def entry():\n    ResearchError('member', '')\n"}
        with self.assertRaisesRegex(ValueError, 'edges must name exactly'):
            self.read_entries(sources, {'member': {'pkg/core.py:entry': 'no_call'}})

    def test_registry_refuses_a_row_for_a_no_series_ending_member(self):
        row = dict(code='member', **{'class': 'decision'}, owner='D2', next=dict(kind='done'))
        with self.assertRaisesRegex(ValueError, 'must have no registry row'):
            stop_classes.validate_registry({'member': row}, no_series_ending=('member',))
        row['group'] = 'no_series_ending'
        with self.assertRaisesRegex(ValueError, 'must have no registry row'):
            stop_classes.validate_registry({'member': row})


class NewGroupRowTests(unittest.TestCase):
    def test_prerequisite_names_status_then_review_series(self):
        group = stop_classes.STOP_GROUPS['prerequisite']
        self.assertEqual((group['class'], group['owner']), ('decision', 'D2'))
        self.assertEqual(set(group['conditions']), {'obligation', 'record'})
        self.assertEqual(group['next'], dict(kind='handoff', command='status', payload={}, chooses=[],
            then=[dict(command='review-series', prerequisites=[dict(condition=
                'Complete the record that this line names with the command that owns it.')])]))

    def test_removable_cause_names_both_decision_answers(self):
        group = stop_classes.STOP_GROUPS['removable_cause']
        self.assertEqual((group['class'], group['owner']), ('decision', 'D2'))
        self.assertEqual(set(group['conditions']), {'environment', 'form', 'limit', 'harness', 'record', 'content'})
        self.assertEqual(group['next'], dict(kind='decision',
            owner_decides='Whether the cause that the refusal names is removed.', then=[
                dict(answer='Remove the cause', steps=[dict(command='review-series',
                    prerequisites=[dict(condition='Remove the cause that the refusal names.')])]),
                dict(answer='Check the retained record', steps=[dict(command='record-check')])]))

    def test_no_series_ending_keeps_the_unlisted_row_and_mutations_do_not_share_state(self):
        group = stop_classes.STOP_GROUPS['no_series_ending']
        self.assertEqual((group['class'], group['owner']), ('decision', 'D2'))
        self.assertEqual(set(group['conditions']), {'entry', 'no_call'})
        self.assertEqual(group['next'], stop_classes.describe_unlisted_step())
        result = stop_classes.describe_unlisted_step()
        result['then'][0]['steps'].clear()
        self.assertTrue(stop_classes.describe_unlisted_step()['then'][0]['steps'])


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
