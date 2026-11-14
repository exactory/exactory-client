"""Static error-code coverage and the scoped R56 constructor recorder."""

from contextlib import contextmanager, ExitStack
import inspect
import tempfile
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from ci import series_code_reach as reach


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/series_code_reach.json'


@contextmanager
def record_reachable_errors(path, reachable=None):
    """Assert constructor codes are reachable while running a named planner/action path.

    Patching __init__ keeps imported aliases and exception subclasses intact. Import
    this helper in G1/G7 tests and scope it to the actual planner or series action.
    ``path`` is a command name (or ``plan_next``/``run_series``); the yielded list
    contains the codes constructed, including errors caught by the action itself.
    """
    from research_harness.errors import ResearchError

    if reachable is None:
        report = json.loads(FIXTURE.read_text())
        reachable = reach.read_command_codes(report, path)
    allowed = frozenset(reachable)
    observed = []
    violations = []
    constructors = [ResearchError]
    harness = str(ROOT / 'skills/math-solver/harness')
    # Keep the controller import local: this helper alone owns the test setup.
    with patch.object(sys, 'path', [harness] + sys.path):
        from search_controller.errors import SearchError
    constructors.append(SearchError)

    def build_recorder(original):
        def record(error, code, *args, **kwargs):
            observed.append(code)
            if code not in allowed:
                frames = inspect.stack()[1:9]
                route = ' -> '.join('%s:%s:%s' % (
                    Path(frame.filename).name, frame.function, frame.lineno) for frame in frames)
                diagnostic = '%s constructed unreachable code %r along %s' % (path, code, route)
                violations.append(diagnostic)
                raise AssertionError(diagnostic)
            original(error, code, *args, **kwargs)
        return record

    with ExitStack() as stack:
        for constructor in constructors:
            stack.enter_context(patch.object(constructor, '__init__', build_recorder(constructor.__init__)))
        try:
            yield observed
        finally:
            if violations:
                raise AssertionError('\n'.join(violations))


class CodeReachTests(unittest.TestCase):
    def test_forwards_error_parameters_to_a_fixed_point_with_defaults_and_keywords(self):
        sources = {'pkg/core.py': '''
from errors import ResearchError
def emit(code='default_code'):
    raise ResearchError(code, 'failure')
def relay(condition, selected='relay_default'):
    emit(code=selected)
def entry():
    relay(True, 'forwarded_code')
    emit()
def unrelated():
    emit('unreachable_code')
'''}
        result = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(set(result['emitted']), {'default_code', 'relay_default', 'forwarded_code', 'unreachable_code'})
        self.assertEqual(set(result['by_command']['entry']), {'default_code', 'relay_default', 'forwarded_code'})
        self.assertIn('pkg/core.py:relay', result['forwarding'])

    def test_reaches_callbacks_attributes_aliases_and_imports_inside_functions(self):
        sources = {
            'pkg/start.py': '''
def entry(obj):
    from .checks import validate as check
    callback = check
    consume(callback)
    obj.inspect()
def consume(fn):
    fn()
''',
            'pkg/checks.py': '''
def validate():
    raise ResearchError('callback_code', 'failure')
class Validator:
    def inspect(self):
        raise ResearchError('attribute_code', 'failure')
def outside():
    raise ResearchError('outside_code', 'failure')
'''}
        result = reach.analyze_sources(sources, {'entry': ['pkg/start.py:entry']})
        self.assertEqual(set(result['reachable']), {'callback_code', 'attribute_code'})

    def test_forwards_codes_through_callback_parameters_and_imported_helpers(self):
        result = reach.analyze_sources({
            'pkg/core.py': '''
def relay(callback, code):
    callback(code)
def entry():
    from .checks import fail
    alias = fail
    relay(alias, 'callback_forwarded')
    from . import checks as helper
    helper.fail('attribute_forwarded')
''',
            'pkg/checks.py': '''
from errors import ResearchError as Failure
def fail(code):
    raise Failure(code, 'failure')
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['emitted'], ['attribute_forwarded', 'callback_forwarded'])

    def test_reaches_imported_module_level_callback_tables(self):
        result = reach.analyze_sources({
            'pkg/core.py': '''
def entry():
    from .checks import callbacks
    callbacks[0]()
''',
            'pkg/checks.py': '''
def validate():
    raise ResearchError('table_callback', 'failure')
callbacks = [validate]
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['table_callback'])

    def test_reaches_validator_references_inside_lambda_callbacks(self):
        result = reach.analyze_sources({'pkg/core.py': '''
def validate():
    raise ResearchError('lambda_callback_code', 'failure')
def consume(callback):
    callback()
def entry():
    consume(lambda: validate())
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['lambda_callback_code'])

    def test_forwards_parameters_captured_by_nested_validation_callbacks(self):
        result = reach.analyze_sources({'pkg/core.py': '''
def guard(code):
    def validate():
        raise ResearchError(code, 'failure')
    consume(validate)
def consume(callback):
    callback()
def entry():
    guard('nested_callback_code')
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['emitted'], ['nested_callback_code'])

    def test_forwards_code_parameters_through_bound_methods_and_constructors(self):
        result = reach.analyze_sources({'pkg/core.py': '''
class Guard:
    def __init__(self, code):
        raise ResearchError(code, 'failure')
    def check(self, code):
        raise ResearchError(code, 'failure')
def entry(guard):
    Guard('constructor_code')
    guard.check('method_code')
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['emitted'], ['constructor_code', 'method_code'])

    def test_raises_for_undeclared_computed_site_and_checks_declared_sites_remain(self):
        sources = {'pkg/core.py': '''
def entry(status):
    raise ResearchError('context_' + status, 'failure')
'''}
        with self.assertRaisesRegex(ValueError, '(?s)Undeclared computed code site.*pkg/core.py:entry'):
            reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        declaration = {'pkg/core.py:entry:context': ['context_verified', 'context_stale']}
        with self.assertRaisesRegex(ValueError, 'Unused computed code declaration'):
            reach.analyze_sources({'pkg/core.py': 'def entry(): pass'},
                                  {'entry': ['pkg/core.py:entry']}, declaration)
        site = "pkg/core.py:entry:'context_' + status"
        result = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']},
                                       {site: ['context_verified', 'context_stale']})
        self.assertEqual(result['reachable'], ['context_stale', 'context_verified'])

    def test_tracks_constructor_subclasses_and_imported_constants(self):
        result = reach.analyze_sources({
            'pkg/codes.py': "MISMATCH = 'packet_mismatch'\n",
            'pkg/core.py': '''
from .codes import MISMATCH
class PacketError(ResearchError): pass
def entry():
    raise PacketError(MISMATCH, 'failure')
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['emitted'], ['packet_mismatch'])

    def test_tracks_constructor_assignment_aliases_and_subclasses_of_import_aliases(self):
        result = reach.analyze_sources({'pkg/core.py': '''
from errors import ResearchError as Failure
Alias = Failure
class Child(Alias): pass
def entry():
    Alias('alias_only', 'failure')
    Child('child_only', 'failure')
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['emitted'], ['alias_only', 'child_only'])

    def test_emits_and_reaches_module_level_constructor_guards(self):
        result = reach.analyze_sources({'pkg/core.py': '''
if configured:
    raise ResearchError('module_guard', 'failure')
def entry():
    pass
'''}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['emitted'], ['module_guard'])
        self.assertEqual(result['reachable'], ['module_guard'])

    def test_rejects_unknown_planned_code_subscripts_calls_and_attributes(self):
        for expression in ("config['stop']", 'choose_code(config)', 'config.stop'):
            with self.subTest(expression=expression):
                path = 'research_harness/review_continuation.py'
                sources = {path: 'def plan_next(config):\n    return dict(state="stop", code=' + expression + ')\n'}
                roots = {'plan_next': [path + ':plan_next']}
                with self.assertRaisesRegex(ValueError, 'Undeclared computed code site'):
                    reach.analyze_sources(sources, roots)
                result = reach.analyze_sources(sources, roots, {path + ':plan_next:' + expression: ['declared_stop']})
                self.assertEqual(result['reachable'], ['declared_stop'])

    def test_reaches_codes_returned_by_helpers_outside_planner_modules(self):
        sources = {
            'research_harness/review_continuation.py': '''
from .helper import terminal
def plan_next():
    return terminal('helper_only')
''',
            'research_harness/helper.py': '''
def terminal(code):
    return dict(state='stop', code=code)
'''}
        result = reach.analyze_sources(sources, {'plan_next': ['research_harness/review_continuation.py:plan_next']})
        self.assertEqual(result['reachable'], ['helper_only'])

    def test_discovers_planned_commands_returned_by_cross_module_helpers(self):
        sources = {
            'research_harness/review_continuation.py': '''
from .helper import next_step
def plan_next():
    return next_step()
''',
            'research_harness/helper.py': '''
def next_step():
    return dict(state='run', command='new-action', payload={})
'''}
        commands = reach.discover_planned_commands(sources)
        self.assertIn('new-action', commands)
        with self.assertRaisesRegex(ValueError, 'new-action'):
            reach.validate_command_roots(commands, {}, {})

    def test_planner_returned_codes_join_the_reachable_set(self):
        result = reach.analyze_sources({'research_harness/review_continuation.py': '''
def plan_next():
    return dict(state='stop', code='planner_boundary')
'''}, {'plan_next': ['research_harness/review_continuation.py:plan_next']})
        self.assertEqual(result['emitted'], [])
        self.assertEqual(result['reachable'], ['planner_boundary'])

    def test_discovers_commands_in_planner_policy_tables_and_role_choices(self):
        commands = reach.discover_planned_commands({'research_harness/review_continuation.py': '''
def plan_next(role):
    command = {'bar': 'value-review', 'slate': 'review-adjudication'}.get(role)
    return dict(command=command, next=[dict(command='new-public-command')])
'''})
        self.assertEqual(commands, {'value-review', 'review-adjudication', 'new-public-command'})
        with self.assertRaisesRegex(ValueError, 'new-public-command'):
            reach.validate_command_roots(commands, {'value-review': [], 'review-adjudication': []}, {})

    def test_a_previously_unavailable_command_gets_its_implemented_root(self):
        sources = {'research_harness/cli.py': """
def action():
    pass
OPERATIONS = {'review-stop': action}
def boundary():
    return dict(state='handoff', command='review-stop')
""", 'research_harness/review_series.py': 'EXECUTABLE_COMMANDS = frozenset()'}
        with patch.object(reach, 'SERIES_COMMANDS', ()), patch.object(reach, 'EXTERNAL_COMMAND_ROOTS', {
                'run_series': (), 'review-series': ()}):
            roots = reach.collect_command_roots(sources)
        self.assertEqual(roots['review-stop'], ('research_harness/cli.py:action',))


    def test_resolves_nested_functions_and_local_shadowing_before_module_names(self):
        result = reach.analyze_sources({'pkg/core.py': """
def prepare():
    ResearchError('module', '')
def first():
    def prepare():
        ResearchError('first', '')
    prepare()
def second():
    def prepare():
        ResearchError('second', '')
    prepare()
def third(prepare):
    prepare()
"""}, {'first': ['pkg/core.py:first'], 'third': ['pkg/core.py:third']})
        self.assertEqual(result['by_command']['first'], ['first'])
        self.assertEqual(result['by_command']['third'], [])

    def test_unresolved_names_add_no_edge(self):
        result = reach.analyze_sources({
            'pkg/core.py': 'def entry():\n    missing()\n',
            'pkg/other.py': "def missing():\n    ResearchError('unrelated', '')\n",
        }, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], [])

    def test_resolves_class_family_and_super_without_other_classes(self):
        sources = {'pkg/core.py': """
class Base:
    def __init__(self):
        ResearchError('base', '')
    def check(self):
        ResearchError('base_check', '')
class Child(Base):
    def __init__(self):
        super().__init__()
    def check(self):
        ResearchError('child_check', '')
    def entry(self):
        self.check()
    @classmethod
    def enter(cls):
        cls.check()
class Other:
    def __init__(self):
        ResearchError('other', '')
    def check(self):
        ResearchError('other_check', '')
def entry():
    Child
"""}
        result = reach.analyze_sources(sources, {
            'super': ['pkg/core.py:Child.__init__'], 'self': ['pkg/core.py:Child.entry'],
            'cls': ['pkg/core.py:Child.enter'], 'class': ['pkg/core.py:entry']})
        self.assertEqual(result['by_command']['super'], ['base'])
        self.assertEqual(result['by_command']['self'], ['base_check', 'child_check'])
        self.assertEqual(result['by_command']['cls'], ['base_check', 'child_check'])
        self.assertNotIn('other', result['by_command']['class'])

    def test_untyped_attributes_reach_methods_and_modules_used_as_values(self):
        sources = {
            'pkg/core.py': """
from . import chosen
def select():
    return chosen
def entry(receiver):
    receiver.check()
""",
            'pkg/chosen.py': "def check():\n    ResearchError('module_value', '')\n",
            'pkg/other.py': """
def check():
    ResearchError('other_function', '')
class Validator:
    def check(self):
        ResearchError('method', '')
"""}
        result = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['method', 'module_value'])

    def test_callbacks_belong_to_the_caller_that_supplies_them(self):
        sources = {'pkg/core.py': """
def consume(callback):
    callback()
def first():
    def prepare():
        ResearchError('first', '')
    consume(prepare)
def second():
    def prepare():
        ResearchError('second', '')
    consume(prepare)
"""}
        result = reach.analyze_sources(sources, {
            'first': ['pkg/core.py:first'], 'second': ['pkg/core.py:second']})
        self.assertEqual(result['by_command']['first'], ['first'])
        self.assertEqual(result['by_command']['second'], ['second'])

    def test_module_guards_belong_to_all_roots_without_module_reference_edges(self):
        result = reach.analyze_sources({
            'pkg/core.py': """
from .other import helper
def entry():
    pass
""",
            'pkg/other.py': """
ResearchError('module_guard', '')
def helper():
    ResearchError('unreached_helper', '')
callbacks = [helper]
"""}, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['module_guard'])

    def test_resolves_enclosing_module_alias_and_respects_receiver_shadowing(self):
        sources = {
            'pkg/core.py': """
from . import other as helper
def entry():
    from . import chosen as helper
    def call():
        alias = helper
        alias.check()
    call()
def shadow(helper):
    helper.check()
""",
            'pkg/chosen.py': "def check():\n    ResearchError('chosen', '')\n",
            'pkg/other.py': "def check():\n    ResearchError('other', '')\n",
        }
        result = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry'], 'shadow': ['pkg/core.py:shadow']})
        self.assertEqual(result['by_command']['entry'], ['chosen'])
        self.assertNotIn('other', result['by_command']['shadow'])

    def test_classifies_codes_by_all_source_homes_and_preserves_provenance(self):
        sources = {
            'research_harness/review_checks.py': """
def entry():
    from . import storage
    storage.check()
    ResearchError('review_code', '')
    return dict(code='report_code')
""",
            'research_harness/storage.py': """
def check():
    ResearchError('outside_code', '')
    ResearchError('review_code', '')
"""}
        report = reach.analyze_sources(sources, {'entry': ['research_harness/review_checks.py:entry']})
        parts = reach.build_coverage_parts(report, {}, {
            'report_code': dict(reason='report_entry', builder='research_harness/review_checks.py:entry')})
        self.assertEqual(set(parts['unreviewed_rows']), {'review_code'})
        self.assertEqual(parts['unreviewed_rows']['review_code']['functions'], [
            'research_harness/review_checks.py:entry', 'research_harness/storage.py:check'])
        self.assertEqual(set(parts['no_stop_of_own']), {'report_code'})
        self.assertEqual(set(parts['outside_review_modules']), {'outside_code'})

    def test_resolves_inheritance_through_empty_and_enclosing_classes(self):
        result = reach.analyze_sources({'pkg/core.py': """
def outer():
    class Base:
        def check(self):
            ResearchError('base', '')
    class Middle(Base):
        pass
    class Child(Middle):
        def entry(self):
            super().check()
class Other:
    def check(self):
        ResearchError('unrelated', '')
"""}, {'entry': ['pkg/core.py:outer.Child.entry']})
        self.assertEqual(result['reachable'], ['base'])

    def test_resolves_cyclic_tables_without_caching_partial_references(self):
        result = reach.analyze_sources({'pkg/core.py': """
def fail_a():
    ResearchError('a', '')
def fail_b():
    ResearchError('b', '')
A = [fail_a]
B = [A, fail_b]
A = [B, fail_a]
C = [A]
def fail_c():
    ResearchError('unrelated', '')
OTHER = [fail_c]
def first():
    A[0]()
def second():
    C[0]()
"""}, {'first': ['pkg/core.py:first'], 'second': ['pkg/core.py:second']})
        self.assertEqual(result['by_command']['first'], ['a', 'b'])
        self.assertEqual(result['by_command']['second'], ['a', 'b'])

    def test_resolves_module_aliases_stored_in_module_tables(self):
        result = reach.analyze_sources({
            'pkg/core.py': """
from . import other
alias = other
def entry():
    alias.check()
""",
            'pkg/other.py': "def check():\n    ResearchError('alias_code', '')\n",
            'pkg/unrelated.py': "def check():\n    ResearchError('unrelated', '')\n",
        }, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(result['reachable'], ['alias_code'])

    def test_loop_exception_and_variadic_names_shadow_global_functions(self):
        result = reach.analyze_sources({'pkg/core.py': """
def callback():
    ResearchError('global', '')
def loop(callbacks):
    for callback in callbacks:
        callback()
def positional(*callback):
    callback[0]()
def keyword(**callback):
    callback['run']()
def caught():
    try:
        pass
    except Exception as callback:
        callback()
"""}, {name: ['pkg/core.py:' + name] for name in ('loop', 'positional', 'keyword', 'caught')})
        for values in result['by_command'].values():
            self.assertEqual(values, [])

    def test_comprehension_targets_only_shadow_names_inside_the_comprehension(self):
        result = reach.analyze_sources({'pkg/core.py': """
def callback():
    ResearchError('global', '')
def entry(callbacks):
    [callback() for callback in callbacks]
def outside(callbacks):
    [callback() for callback in callbacks]
    callback()
"""}, {'entry': ['pkg/core.py:entry'], 'outside': ['pkg/core.py:outside']})
        self.assertEqual(result['by_command']['entry'], [])
        self.assertEqual(result['by_command']['outside'], ['global'])

    def test_accepts_payload_reason_for_a_declared_computed_constructor(self):
        sources = {'pkg/core.py': """
def producer():
    raise ResearchError('payload_code', '')
def check(status):
    raise ResearchError(status['code'], '')
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:check']}, {
            "pkg/core.py:check:status['code']": {'sources': ['pkg/core.py:producer']}})
        row = {'payload_code': dict(reason='payload', check='pkg/core.py:check')}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'lane.json').write_text(json.dumps(row))
            self.assertEqual(reach.read_no_stop_entries(Path(folder), sources, report, {}), row)

    def test_computed_producers_follow_the_new_edges(self):
        sources = {'pkg/core.py': """
def consume(callback):
    callback()
def producer():
    def check():
        ResearchError('produced', '')
    consume(check)
def other():
    def check():
        ResearchError('unrelated', '')
    consume(check)
def entry(status):
    ResearchError(status['code'], '')
"""}
        result = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']}, {
            "pkg/core.py:entry:status['code']": {'sources': ['pkg/core.py:producer']}})
        self.assertEqual(result['reachable'], ['produced'])

    def test_declared_edges_require_an_existing_uninferred_call_and_a_reason(self):
        sources = {'pkg/core.py': """
def entry(obj):
    getattr(obj, 'hidden')()
def hidden():
    ResearchError('declared', '')
"""}
        roots = {'entry': ['pkg/core.py:entry']}
        declaration = {'pkg/core.py:entry': {'pkg/core.py:hidden': 'A computed getattr call.'}}
        result = reach.analyze_sources(sources, roots, declared_edges=declaration)
        self.assertEqual(result['reachable'], ['declared'])
        for invalid in (
            {'pkg/core.py:absent': {'pkg/core.py:hidden': 'Missing caller.'}},
            {'pkg/core.py:entry': {'pkg/core.py:absent': 'Missing target.'}},
            {'pkg/core.py:entry': {'pkg/core.py:hidden': ''}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                reach.analyze_sources(sources, roots, declared_edges=invalid)
        with self.assertRaisesRegex(ValueError, 'Unused declared edge'):
            reach.analyze_sources(dict(sources, **{'pkg/core.py': sources['pkg/core.py'].replace(
                "getattr(obj, 'hidden')()", 'hidden()')}), roots, declared_edges=declaration)

    def test_series_roots_use_only_the_commands_declared_by_driver_source(self):
        sources = {
            'research_harness/cli.py': """
from .actions import run, handoff
OPERATIONS = {'review-run': run, 'agent-only': handoff}
""",
            'research_harness/review_continuation.py': """
def plan_next():
    return dict(state='handoff', command='agent-only')
""",
            'research_harness/review_series.py': """
EXECUTABLE_COMMANDS = frozenset(('review-run',))
def run_series():
    from .cli import OPERATIONS
    OPERATIONS[command]()
""",
            'research_harness/actions.py': """
def run():
    ResearchError('executed', '')
def handoff():
    ResearchError('agent_only', '')
"""}
        with patch.object(reach, 'SERIES_COMMANDS', ()), patch.object(reach, 'EXTERNAL_COMMAND_ROOTS', {
                'plan_next': ('research_harness/review_continuation.py:plan_next',),
                'run_series': ('research_harness/review_series.py:run_series',),
                'review-series': ('research_harness/review_series.py:run_series',)}):
            roots = reach.collect_command_roots(sources)
            result = reach.analyze_sources(sources, roots, series_commands=reach.read_executable_commands(sources))
        self.assertEqual(result['reachable'], ['executed'])
        self.assertEqual(result['by_command']['agent-only'], ['agent_only'])
        self.assertEqual(result['by_command']['run_series'], ['executed'])
        self.assertEqual(result['by_command']['review-series'], ['executed'])

    def test_registry_coverage_rejects_overlaps_and_entries_outside_reach(self):
        fixture = dict(reachable=['row', 'pending', 'payload', 'outside'],
                       unreviewed_rows={'pending': {}}, no_stop_of_own={'payload': {}},
                       outside_review_modules={'outside': {}})
        reach.validate_registry_coverage(fixture, {'row': {}})
        for field in ('unreviewed_rows', 'no_stop_of_own', 'outside_review_modules'):
            for code in ('row', 'absent'):
                with self.subTest(field=field, code=code), self.assertRaisesRegex(AssertionError, code):
                    reach.validate_registry_coverage(dict(fixture, **{field: dict(fixture[field], **{code: {}})}),
                                                     {'row': {}})
        with self.assertRaisesRegex(AssertionError, 'outside'):
            reach.validate_registry_coverage(dict(fixture, no_stop_of_own={'payload': {}, 'outside': {}}), {'row': {}})

    def test_checks_no_stop_reasons_against_the_named_source_functions(self):
        sources = {'research_harness/review_checks.py': """
from .errors import ResearchError as Failure
def check():
    raise Failure('payload_code', '')
def builder():
    return dict(code='entry_code')
def handler():
    try:
        check()
    except Failure:
        return dict(state='stop', code='replacement')
def ordinary():
    check()
"""}
        report = reach.analyze_sources(sources, {'entry': ['research_harness/review_checks.py:handler']})
        rows = {
            'payload_code': dict(reason='payload', check='research_harness/review_checks.py:check'),
            'entry_code': dict(reason='report_entry', builder='research_harness/review_checks.py:builder'),
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lane.json'
            path.write_text(json.dumps(rows))
            self.assertEqual(reach.read_no_stop_entries(Path(folder), sources, report, {'replacement': {}}), rows)
            invalid = [
                {'payload_code': dict(reason='unknown')},
                {'payload_code': dict(reason='payload', check='missing')},
                {'payload_code': dict(reason='payload', check='research_harness/review_checks.py:builder')},
                {'payload_code': dict(reason='report_entry', builder='research_harness/review_checks.py:ordinary')},
                {'payload_code': dict(reason='replaced', handler='research_harness/review_checks.py:ordinary', becomes='plan')},
                {'entry_code': dict(reason='replaced', handler='research_harness/review_checks.py:handler', becomes='plan')},
                {'payload_code': dict(reason='replaced', handler='research_harness/review_checks.py:handler', becomes='absent')},
            ]
            for row in invalid:
                path.write_text(json.dumps(row))
                with self.subTest(row=row), self.assertRaises(ValueError):
                    reach.read_no_stop_entries(Path(folder), sources, report, {'replacement': {}})
            for becomes in ('plan', 'replacement'):
                row = {'payload_code': dict(reason='replaced', handler='research_harness/review_checks.py:handler',
                                            becomes=becomes)}
                path.write_text(json.dumps(row))
                self.assertEqual(reach.read_no_stop_entries(Path(folder), sources, report, {'replacement': {}}), row)
            (Path(folder) / 'duplicate.json').write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                reach.read_no_stop_entries(Path(folder), sources, report, {'replacement': {}})

    def test_fixture_regenerates_and_names_every_unreviewed_registry_row(self):
        expected = json.loads(FIXTURE.read_text())
        actual = reach.build_fixture(ROOT)
        self.assertEqual(actual, expected,
                         'Regenerate with /usr/bin/python3 ci/series_code_reach.py --write-fixture')
        self.assertIn('openness_search_stale', actual['reachable'])
        self.assertLess(FIXTURE.stat().st_size, 600_000)
        self.assertEqual(reach.read_command_codes(actual, 'run_series'), actual['reachable'])
        self.assertEqual(reach.read_command_codes(actual, 'review-series'), actual['reachable'])
        self.assertNotEqual(reach.read_command_codes(actual, 'review-run'),
                            reach.read_command_codes(actual, 'problem-openness'))
        sets = list(actual['code_sets'].values())
        self.assertEqual(len(sets), len({tuple(values) for values in sets}))
        self.assertTrue(set(actual['reachable']) <= set(actual['emitted']) | set(actual['planned']))
        for code, row in actual['unreviewed_rows'].items():
            self.assertEqual(row['unit'], 'registry-rows', code)
        from research_harness.stop_classes import STOP_REGISTRY
        reach.validate_registry_coverage(actual, STOP_REGISTRY)
        registry = dict(STOP_REGISTRY)
        del registry['review_context_unverified']
        with self.assertRaisesRegex(AssertionError, 'review_context_unverified'):
            reach.validate_registry_coverage(actual, registry)

    def test_deleting_a_registry_row_or_missing_row_entry_fails_coverage(self):
        fixture = dict(reachable=['listed', 'pending'],
                       unreviewed_rows={'pending': {'unit': 'registry-rows'}})
        reach.validate_registry_coverage(fixture, {'listed': {}})
        with self.assertRaisesRegex(AssertionError, 'listed'):
            reach.validate_registry_coverage(fixture, {})
        with self.assertRaisesRegex(AssertionError, 'pending'):
            reach.validate_registry_coverage(dict(fixture, unreviewed_rows={}), {'listed': {}})

    def test_recorder_checks_both_constructors_and_reports_the_action_path(self):
        from research_harness.errors import ResearchError
        with record_reachable_errors('synthetic action', {'known'}) as observed:
            ResearchError('known', 'caught by the caller')
            from search_controller.errors import SearchError
            SearchError('known', 'caught by the caller')
        self.assertEqual(observed, ['known', 'known'])
        with self.assertRaisesRegex(AssertionError, 'synthetic action.*missing.*test_recorder'):
            with record_reachable_errors('synthetic action', {'known'}):
                ResearchError('missing', 'failure')
        self.assertEqual(ResearchError('missing', 'restored').code, 'missing')

    def test_recorder_rejects_missed_codes_even_when_the_action_catches_the_assertion(self):
        from research_harness.errors import ResearchError
        with self.assertRaisesRegex(AssertionError, 'swallowed action.*missing'):
            with record_reachable_errors('swallowed action', {'known'}):
                try:
                    ResearchError('missing', 'failure')
                except Exception:
                    pass
        self.assertEqual(ResearchError('missing', 'restored').code, 'missing')


if __name__ == '__main__':
    unittest.main()
