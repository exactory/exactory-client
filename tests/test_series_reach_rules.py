"""Source edges and the computed inventory of unfollowed lookups."""
import unittest
from unittest.mock import patch
from ci import series_code_reach as reach


class SourceRulesTests(unittest.TestCase):
    def test_class_reference_reaches_base_and_body_callback(self):
        result = reach.analyze_sources({'pkg/core.py': '''
def callback():
    ResearchError('body', '')
class Base:
    def inherited(self):
        ResearchError('base', '')
class Child(Base):
    check = callback
def entry():
    Child
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['base', 'body'])

    def test_class_body_binding_resolves_self_super_and_untyped_attributes(self):
        sources = {'pkg/core.py': '''
def callback():
    ResearchError('body', '')
class Base:
    check = callback
class Child(Base):
    def entry(self):
        self.check()
    def parent(self):
        super().check()
def entry(obj):
    obj.check()
'''}
        result = reach.analyze_sources(sources, {name: [key] for name, key in (
            ('self', 'pkg/core.py:Child.entry'), ('super', 'pkg/core.py:Child.parent'), ('other', 'pkg/core.py:entry'))})
        for values in result['by_command'].values():
            self.assertEqual(values, ['body'])

    def test_an_instance_attribute_reaches_a_method_aliased_in_the_class_body(self):
        report = reach.analyze_sources({'pkg/core.py': """
class Check:
    def validate(self):
        ResearchError('class_alias', '')
    inspect = validate
def entry(obj):
    obj.inspect()
"""}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['reachable'], ['class_alias'])

    def test_top_level_table_additions_follow_the_table_across_imports(self):
        sources = {'pkg/core.py': '''
from .table import callbacks
def entry():
    callbacks['check']()
''', 'pkg/table.py': 'callbacks = dict()\n', 'pkg/extension.py': '''
from .table import callbacks
def first():
    ResearchError('update', '')
def second():
    ResearchError('item', '')
def third():
    ResearchError('augmented', '')
callbacks.update({'first': first})
callbacks['second'] = second
callbacks |= {'third': third}
'''}
        result = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['augmented', 'item', 'update'])

    def test_top_level_calls_and_unbound_names_belong_to_every_root(self):
        sources = {'pkg/core.py': '''
def initial():
    ResearchError('called', '')
def referenced():
    ResearchError('named', '')
def factory():
    ResearchError('assigned_call', '')
initial()
referenced
VALUE = factory()
def entry():
    pass
'''}
        result = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['assigned_call', 'called', 'named'])

    def test_import_time_control_flow_runs_but_script_only_entry_points_do_not(self):
        sources = {'pkg/core.py': """
def initialize():
    ResearchError('imported', '')
def script_main():
    ResearchError('script_only', '')
if configured:
    initialize()
for item in values:
    initialize()
if __name__ == '__main__':
    script_main()
def entry():
    pass
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['reachable'], ['imported'])

    def test_literal_getattr_resolves_an_attribute_without_a_dispatch_declaration(self):
        sources = {'pkg/core.py': '''
from . import helper
def entry():
    callback = getattr(helper, 'check')
    callback()
''', 'pkg/helper.py': "def check():\n    ResearchError('literal_attribute', '')\n"}
        result = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['literal_attribute'])

    def test_unfollowed_lookup_forms_are_listed_without_adding_edges(self):
        cases = (
            ('getattr(obj, name)', 'value = getattr(obj, name)'),
            ("local['check']()", "local['check']()"),
            ('factory()()', 'factory()()'),
        )
        for expression, statement in cases:
            sources = {'pkg/core.py': "def target():\n    ResearchError('unreached', '')\ndef entry(obj, name, local):\n    " + statement + '\n'}
            with self.subTest(expression=expression):
                report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
                self.assertEqual(report['reachable'], [])
                self.assertEqual(report['unfollowed_sites'], [dict(path='pkg/core.py', function='entry', expression=expression)])

    def test_local_calls_bound_from_unfollowed_lookups_are_listed(self):
        sources = {'pkg/core.py': """
def target():
    ResearchError('unreached', '')
def entry(obj, name):
    chosen = getattr(obj, name)
    chosen()
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['reachable'], [])
        self.assertEqual({row['expression'] for row in report['unfollowed_sites']}, {'getattr(obj, name)', 'chosen()'})

    def test_computed_module_lookup_reaches_functions_added_to_that_module(self):
        sources = {'pkg/core.py': """
from . import helper
def entry(name):
    callback = getattr(helper, name)
    callback()
""", 'pkg/helper.py': "def first():\n    ResearchError('first', '')\n"}
        roots = {'entry': ['pkg/core.py:entry']}
        report = reach.analyze_sources(sources, roots)
        self.assertEqual(report['reachable'], ['first'])
        self.assertEqual(report['unfollowed_sites'], [])
        sources['pkg/helper.py'] += "def added():\n    ResearchError('added', '')\n"
        self.assertEqual(reach.analyze_sources(sources, roots)['reachable'], ['added', 'first'])

    def test_operation_roots_follow_table_extensions_and_limit_offline_dispatch(self):
        sources = {
            'research_harness/cli.py': """
OPERATIONS = {}
def run():
    ResearchError('run', '')
def handoff():
    ResearchError('handoff', '')
OPERATIONS.update({'review-run': run, 'agent-only': handoff})
""",
            'research_harness/extension.py': """
from .cli import OPERATIONS
def record():
    ResearchError('record', '')
OPERATIONS['value-review'] = record
""",
            'research_harness/review_series.py': """
EXECUTABLE_COMMANDS = frozenset(('review-run', 'value-review'))
def run_series():
    from .cli import OPERATIONS
    OPERATIONS[command]()
""",
            'research_harness/review_continuation.py': """
def plan_next():
    return dict(state='handoff', command='agent-only')
""",
            'research_harness/record_check.py': """
def check_continuation():
    from .cli import OPERATIONS
    OPERATIONS[command]()
"""}
        external = {'plan_next': ('research_harness/review_continuation.py:plan_next',),
                    'run_series': ('research_harness/review_series.py:run_series',),
                    'review-series': ('research_harness/review_series.py:run_series',),
                    'record-check': ('research_harness/record_check.py:check_continuation',)}
        with patch.object(reach, 'SERIES_COMMANDS', ()), patch.object(reach, 'EXTERNAL_COMMAND_ROOTS', external):
            roots = reach.collect_command_roots(sources)
            self.assertEqual(roots['value-review'], ('research_harness/extension.py:record',))
            self.assertEqual(set(roots['record-check']), {
                'research_harness/record_check.py:check_continuation', 'research_harness/extension.py:record'})
            report = reach.analyze_sources(sources, roots, series_commands=reach.read_executable_commands(sources))
        self.assertEqual(report['reachable'], ['record', 'run'])
        self.assertEqual(report['by_command']['record-check'], ['record'])
        self.assertEqual(report['by_command']['agent-only'], ['handoff'])

    def test_nested_class_reference_reaches_its_nested_base(self):
        sources = {'pkg/core.py': """
class Outer:
    class Base:
        def check(self):
            ResearchError('nested_base', '')
    class Child(Base):
        pass
def entry():
    Outer.Child
"""}
        self.assertEqual(reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})['reachable'], ['nested_base'])

    def test_module_name_definitions_and_annotated_table_additions_keep_their_functions(self):
        sources = {'pkg/core.py': """
def check():
    ResearchError('retained', '')
VALUE = check if configured else None
TABLE = {}
TABLE['check']: object = check
def name():
    VALUE()
def table():
    TABLE['check']()
"""}
        report = reach.analyze_sources(sources, {'name': ['pkg/core.py:name'], 'table': ['pkg/core.py:table']})
        self.assertEqual(report['by_command']['name'], ['retained'])
        self.assertEqual(report['by_command']['table'], ['retained'])

    def test_updates_through_module_aliases_stay_with_the_table(self):
        sources = {'pkg/core.py': """
def check():
    ResearchError('retained', '')
def make():
    return {}
TABLE = make()
ALIAS = TABLE
ALIAS.update({'check': check})
def entry():
    TABLE['check']()
def empty():
    pass
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry'], 'empty': ['pkg/core.py:empty']})
        self.assertEqual(report['by_command']['entry'], ['retained'])
        self.assertEqual(report['by_command']['empty'], [])
        self.assertEqual(report['unfollowed_sites'], [])

    def test_module_control_references_are_initial_but_script_errors_are_not(self):
        sources = {'pkg/core.py': """
def assert_check():
    ResearchError('asserted', '')
def conditional_check():
    ResearchError('conditional', '')
assert assert_check
if conditional_check:
    pass
if __name__ == '__main__':
    ResearchError('script_only', '')
def entry():
    pass
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['reachable'], ['asserted', 'conditional'])
        self.assertIn('script_only', report['emitted'])
        self.assertNotIn('script_only', report['code_sites'])

    def test_a_lookup_in_a_condition_does_not_make_known_branches_unfollowed(self):
        sources = {'pkg/core.py': """
def first():
    ResearchError('first', '')
def second():
    ResearchError('second', '')
def entry(config):
    callback = first if config.get('choose') else second
    callback()
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['reachable'], ['first', 'second'])
        self.assertEqual(report['unfollowed_sites'], [])

    def test_saved_factory_results_are_listed_as_unfollowed_calls(self):
        sources = {'pkg/core.py': """
def factory():
    return value
def entry():
    callback = factory()
    callback()
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['unfollowed_sites'], [dict(path='pkg/core.py', function='entry', expression='callback()')])

    def test_status_obligation_flags_include_forwarded_code_parameters(self):
        sources = {'research_harness/cli.py': """
def wrap(code):
    return obligation(code)
def status_report():
    return wrap('forwarded_obligation')
def entry():
    status_report()
"""}
        report = reach.analyze_sources(sources, {'entry': ['research_harness/cli.py:entry']})
        self.assertTrue(reach.build_unreviewed_rows(report, {})['forwarded_obligation']['status_obligation'])

    def test_star_imports_and_class_keywords_do_not_refuse_the_analysis(self):
        for source in ('from .other import *\ndef entry(): pass',
                       'class Thing(metaclass=type): pass\ndef entry(): pass'):
            with self.subTest(source=source):
                self.assertEqual(reach.analyze_sources({'pkg/core.py': source}, {'entry': ['pkg/core.py:entry']})['reachable'], [])
