"""Static error-code coverage and the scoped R56 constructor recorder."""

from contextlib import contextmanager, ExitStack
import ast
import inspect
from functools import wraps
from types import FunctionType
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from ci import series_code_reach as reach


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/series_code_reach.json'


@contextmanager
def record_reachable_errors(path, reachable=None, *, injected=frozenset()):
    """Assert constructor codes are reachable while running a named planner/action path.

    Patching __init__ keeps imported aliases and exception subclasses intact. Import
    this helper in G1/G7 tests and scope it to the actual planner or series action.
    ``path`` is a command name (or ``plan_next``/``run_series``); the yielded list
    contains the codes constructed, including errors caught by the action itself.
    Plans and emitted lines need registry rows or pending entries. Only codes
    absent from both source packages may be supplied as ``injected``.
    """
    from research_harness.errors import ResearchError
    from research_harness import review_continuation, review_series
    from research_harness.stop_classes import STOP_REGISTRY

    report = json.loads(FIXTURE.read_text())
    if reachable is None:
        reachable = reach.read_command_codes(report, path)
    allowed = frozenset(reachable)
    injected = frozenset(injected)
    source_codes = set(report['code_vocabulary'])
    if injected - source_codes:
        for source in reach.read_sources(ROOT).values():
            source_codes.update(node.value for node in ast.walk(ast.parse(source))
                                if isinstance(node, ast.Constant) and isinstance(node.value, str))
    written = injected & source_codes
    if written:
        raise ValueError('Injected codes must be absent from source files: ' + ', '.join(sorted(written)))
    pending = frozenset(report['unreviewed_rows'])
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
            if code not in injected and code not in allowed:
                frames = inspect.stack()[1:9]
                route = ' -> '.join('%s:%s:%s' % (
                    Path(frame.filename).name, frame.function, frame.lineno) for frame in frames)
                diagnostic = '%s constructed unreachable code %r along %s' % (path, code, route)
                violations.append(diagnostic)
                raise AssertionError(diagnostic)
            original(error, code, *args, **kwargs)
        return record

    def check_line(row):
        code = row.get('code')
        if code and code not in injected and code not in STOP_REGISTRY and code not in pending:
            diagnostic = '%s returned an unlisted code %r without a registry row or unreviewed_rows entry' % (path, code)
            violations.append(diagnostic)
            raise AssertionError(diagnostic)
        return row

    original_plan = review_continuation.plan_next
    original_series = review_series.run_series

    def clone_function(function):
        if not isinstance(function, FunctionType):
            return function
        clone = FunctionType(function.__code__, function.__globals__, function.__name__,
                             function.__defaults__, function.__closure__)
        clone.__kwdefaults__ = function.__kwdefaults__
        return clone

    plan_body = clone_function(original_plan)
    series_body = clone_function(original_series)

    def create_router_code(function):
        # Match closure arity without reading the original cells. The hook is
        # a temporary keyword default, so saved aliases keep the same object.
        slots = ['slot_' + str(index) for index in range(len(function.__closure__ or ()))]
        lines = ['def factory():'] + ['    ' + slot + ' = None' for slot in slots]
        lines += ['    def route(*args, _record_reachable_hook=None, **kwargs):',
                  '        if _record_reachable_hook is None:',
                  '            return (' + ', '.join(slots) + ',)' if slots else '            return None',
                  '        return _record_reachable_hook(*args, **kwargs)',
                  '    return route']
        namespace = {}
        exec('\n'.join(lines), namespace)
        return namespace['factory']().__code__

    @wraps(original_plan)
    def record_plan(*args, **kwargs):
        return check_line(plan_body(*args, **kwargs))

    @wraps(original_series)
    def record_series(*args, **kwargs):
        emit = kwargs.pop('emit', review_series._emit_json)
        def record_line(row):
            check_line(row)
            emit(row)
        return series_body(*args, emit=record_line, **kwargs)

    with ExitStack() as stack:
        for function, callback in ((original_plan, record_plan), (original_series, record_series)):
            if isinstance(function, FunctionType):
                defaults = dict(function.__kwdefaults__ or {}, _record_reachable_hook=callback)
                for attribute, value in (('__kwdefaults__', defaults), ('__code__', create_router_code(function)),
                                         ('__defaults__', None)):
                    previous = getattr(function, attribute)
                    setattr(function, attribute, value)
                    stack.callback(setattr, function, attribute, previous)
        # Patch existing aliases too; later imports receive the patched home.
        for module in tuple(sys.modules.values()):
            if module is None:
                continue
            for name, value in tuple(vars(module).items()):
                if value is original_plan:
                    stack.enter_context(patch.object(module, name, record_plan))
                elif value is original_series:
                    stack.enter_context(patch.object(module, name, record_series))
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
from . import chosen, other
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

    def test_code_sites_ignore_mere_mentions_and_include_forwarded_literals(self):
        sources = {'pkg/core.py': """
def guard(code):
    raise ResearchError(code, '')
def entry():
    guard('forwarded')
    return dict(code='planned')
def mention():
    return 'forwarded'
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:entry']})
        self.assertEqual(report['code_sites'], {'forwarded': ['pkg/core.py:entry#1'], 'planned': ['pkg/core.py:entry#1']})
        self.assertEqual(reach.build_unreviewed_rows(report, {})['forwarded'], dict(
            unit='registry-rows', sites=['pkg/core.py:entry#1'], status_obligation=False))

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

    def test_declared_computed_sources_do_not_make_forwarders_writing_sites(self):
        sources = {'pkg/core.py': """
def producer():
    raise ResearchError('computed', '')
def check(status):
    producer()
    raise ResearchError(status['code'], '')
"""}
        report = reach.analyze_sources(sources, {'entry': ['pkg/core.py:check']}, {
            "pkg/core.py:check:status['code']": {'sources': ['pkg/core.py:producer']}})
        self.assertEqual(report['code_sites']['computed'], ['pkg/core.py:producer#1'])

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
    obj.invoke()
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
                'obj.invoke()', 'hidden()')}), roots, declared_edges=declaration)

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

    def test_registry_coverage_rejects_reviewed_and_outside_pending_entries(self):
        fixture = dict(reachable=['row', 'pending'], unreviewed_rows={'pending': {}})
        reach.validate_registry_coverage(fixture, {'row': {}})
        for code in ('row', 'absent'):
            with self.subTest(code=code), self.assertRaisesRegex(AssertionError, code):
                reach.validate_registry_coverage(dict(fixture, unreviewed_rows={'pending': {}, code: {}}), {'row': {}})

    def test_status_obligation_flag_uses_only_obligation_arguments_in_status_reach(self):
        sources = {'research_harness/cli.py': """
def obligation(code):
    return dict(code=code)
def status_report():
    return obligation('status_entry')
def outside():
    return obligation('outside_entry')
def entry():
    status_report()
    return dict(code='ordinary_plan')
"""}
        report = reach.analyze_sources(sources, {'entry': ['research_harness/cli.py:entry']})
        rows = reach.build_unreviewed_rows(report, {})
        self.assertTrue(rows['status_entry']['status_obligation'])
        self.assertFalse(rows['ordinary_plan']['status_obligation'])
        self.assertNotIn('outside_entry', rows)

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

    def test_ending_declarations_preserve_numbered_diagnostic_writers(self):
        fixture = json.loads(FIXTURE.read_text())
        # The retry and scope readers each write the name; a forwarder does not.
        sites = ['research_harness/automatic_overrides.py:record_series_stop#1',
                 'research_harness/automatic_overrides.py:record_series_stop#2',
                 'research_harness/review_series.py:_record_omission#1']
        for code in ('BrokenPipeError', 'ConnectionResetError', 'JSONDecodeError'):
            with self.subTest(code=code):
                self.assertEqual(reach.read_code_sites(fixture, code), sites)
        for site in ("research_harness/record_check.py:describe_series_stops:row['code']",
                     "research_harness/record_check.py:read_ending_files:line['code']",
                     'research_harness/review_series.py:_end_series:error.code',
                     'research_harness/review_series.py:_end_series:code'):
            self.assertIn(site, fixture['computed_sites'])
            self.assertNotIn('passes', fixture['computed_sites'][site])

    def test_deleting_a_registry_row_or_missing_row_entry_fails_coverage(self):
        fixture = dict(reachable=['listed', 'pending'],
                       unreviewed_rows={'pending': {'unit': 'registry-rows'}})
        reach.validate_registry_coverage(fixture, {'listed': {}})
        with self.assertRaisesRegex(AssertionError, 'listed'):
            reach.validate_registry_coverage(fixture, {})
        with self.assertRaisesRegex(AssertionError, 'pending'):
            reach.validate_registry_coverage(dict(fixture, unreviewed_rows={}), {'listed': {}})

    def test_recorder_checks_lines_and_allows_only_source_absent_injections(self):
        from research_harness import review_continuation, review_series
        from research_harness.errors import ResearchError
        plan = dict(state='stop', code='synthetic_unlisted', head_id='head', details={})
        def return_plan(*args, **kwargs):
            return dict(plan)
        with patch.object(review_continuation, 'plan_next', side_effect=return_plan):
            with self.assertRaisesRegex(AssertionError, 'unlisted'):
                with record_reachable_errors('synthetic', set()):
                    review_continuation.plan_next()
            with record_reachable_errors('synthetic', set(), injected={'synthetic_unlisted'}):
                ResearchError('synthetic_unlisted', '')
                self.assertEqual(review_continuation.plan_next()['code'], 'synthetic_unlisted')
        def emit_line(*args, **kwargs):
            kwargs['emit'](plan)
            return 3
        with patch.object(review_series, 'run_series', side_effect=emit_line):
            with self.assertRaisesRegex(AssertionError, 'unlisted'):
                with record_reachable_errors('synthetic', set()):
                    review_series.run_series(None, 'head', emit=lambda row: None)
            rows = []
            with record_reachable_errors('synthetic', set(), injected={'synthetic_unlisted'}):
                ResearchError('synthetic_unlisted', '')
                review_series.run_series(None, 'head', emit=rows.append)
            self.assertEqual(rows, [plan])
        for code in ('openness_search_stale', 'explicit-packet-only-v1'):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, 'source'):
                with record_reachable_errors('synthetic', set(), injected={code}):
                    self.fail('A source-written code was accepted as injected')

    def test_recorder_checks_saved_local_aliases_of_its_boundaries(self):
        from research_harness import review_continuation, review_series
        def return_plan(*args, **kwargs):
            return dict(code='synthetic_saved_alias')
        def emit_line(*args, **kwargs):
            kwargs['emit'](dict(code='synthetic_saved_alias'))
        with patch.object(review_continuation, 'plan_next', return_plan):
            saved_plan = review_continuation.plan_next
            with self.assertRaisesRegex(AssertionError, 'unlisted'):
                with record_reachable_errors('synthetic', set()):
                    saved_plan()
        with patch.object(review_series, 'run_series', emit_line):
            saved_series = review_series.run_series
            with self.assertRaisesRegex(AssertionError, 'unlisted'):
                with record_reachable_errors('synthetic', set()):
                    saved_series(None, 'head', emit=lambda row: None)

    def test_recorder_accepts_pending_source_codes_without_an_exemption(self):
        from research_harness import review_continuation
        from research_harness.errors import ResearchError
        plan = dict(state='stop', code='openness_search_stale')
        with patch.object(review_continuation, 'plan_next', return_value=plan):
            with record_reachable_errors('plan_next'):
                ResearchError('openness_search_stale', '')
                self.assertEqual(review_continuation.plan_next(), plan)

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
