"""Static error-code coverage and the scoped R56 constructor recorder."""

from contextlib import contextmanager, ExitStack
import inspect
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

    def test_bound_result_gap_refusals_are_in_the_audit_constructor_domain(self):
        path = 'research_harness/openness_minimum.py'
        sources = {path: '''
def require_unblocked_outcome(condition):
    code, message = GAP_REFUSALS[condition]
    raise ResearchError(code, message)
def list_minimum_gaps(condition):
    def add_gap(condition):
        code = GAP_REFUSALS[condition][0]
        return dict(code=code)
    return add_gap(condition)
'''}
        functions = ('require_unblocked_outcome', 'list_minimum_gaps.add_gap')
        declarations = {path + ':' + function + ':code': reach.COMPUTED_CODE_SITES[
            path + ':' + function + ':code'] for function in functions}
        report = reach.analyze_sources(sources, {
            'audit': [path + ':require_unblocked_outcome'],
            'gaps': [path + ':list_minimum_gaps']}, declarations)
        for command in ('audit', 'gaps'):
            with self.subTest(command=command):
                self.assertEqual(set(report['by_command'][command]),
                    {'openness_search_gap', 'openness_reading_gap', 'openness_settling_source',
                     'openness_result_gap', 'openness_result_changed'})

    def test_result_manifest_modes_declare_both_refusal_domains(self):
        path = 'research_harness/problem_openness_reviews.py'
        sources = {path: '''
from .operations import require
def required_evidence(result_kinds):
    def visit():
        require(False, 'Manifest kind', MISSING if result_kinds else "openness_native_evidence_unsupported")
        require(False, 'Claim binding', MISSING if result_kinds else MISMATCH)
    return visit()
''', 'research_harness/operations.py': '''
def require(condition, message, code):
    if not condition:
        raise ResearchError(code, message)
'''}
        expressions = ('MISSING if result_kinds else "openness_native_evidence_unsupported"',
                       'MISSING if result_kinds else MISMATCH')
        sites = {path + ':required_evidence.visit:' + expression: reach.COMPUTED_CODE_SITES[
            path + ':required_evidence.visit:' + expression] for expression in expressions}
        report = reach.analyze_sources(sources, {'manifest': [path + ':required_evidence']}, sites)
        self.assertEqual(set(report['by_command']['manifest']),
                         {'openness_native_evidence_missing', 'openness_native_evidence_unsupported',
                          'openness_native_review_mismatch'})

    def test_native_attempt_delivery_reaches_dispatch_and_delivery_refusals(self):
        controller = 'skills/math-solver/harness/search_controller/service.py'
        delivery = 'skills/math-solver/harness/search_controller/attempt_delivery.py'
        sources = {
            'research_harness/cli.py': 'OPERATIONS = {}\n',
            'research_harness/review_continuation.py': '''
def plan_next():
    return dict(command='search deliver-attempt')
''',
            controller: '''
from .attempt_delivery import deliver_attempt
class Controller:
    def command(self, denied):
        if denied:
            raise ResearchError('native_dispatch_refusal', 'Dispatch refused')
        return deliver_attempt()
''',
            delivery: '''
def deliver_attempt():
    raise ResearchError('native_delivery_refusal', 'Delivery refused')
'''}
        with patch.object(reach, 'SERIES_COMMANDS', ()):
            try:
                roots = reach.collect_command_roots(sources)
            except ValueError as error:
                self.fail(str(error))
        self.assertEqual(roots['search deliver-attempt'], (controller + ':Controller.command',))
        report = reach.analyze_sources(sources, {'search deliver-attempt': roots['search deliver-attempt']})
        self.assertEqual(set(report['by_command']['search deliver-attempt']),
                         {'native_dispatch_refusal', 'native_delivery_refusal'})

    def test_a_previously_unavailable_command_gets_its_implemented_root(self):
        sources = {'research_harness/cli.py': """
def action():
    pass
OPERATIONS = {'review-stop': action}
def boundary():
    return dict(state='handoff', command='review-stop')
"""}
        with patch.object(reach, 'SERIES_COMMANDS', ()), patch.object(reach, 'EXTERNAL_COMMAND_ROOTS', {
                'run_series': (), 'review-series': ()}):
            roots = reach.collect_command_roots(sources)
        self.assertEqual(roots['review-stop'], ('research_harness/cli.py:action',))

    def test_notice_codes_follow_the_installed_refusal_classifier_domain(self):
        notice = 'research_harness/review_refusal_notice.py'
        refusal = 'research_harness/review_refusal.py'
        site = notice + ":_build_refused_entry:diagnosis['code']"
        self.assertIn(site, reach.COMPUTED_CODE_SITES)
        sources = {notice: '''
def _build_refused_entry(diagnosis):
    entry = {}
    entry.update(code=diagnosis['code'])
    return entry
''', refusal: '''
def classify_observed_output(changed):
    if changed:
        raise ResearchError('new_locator_refusal', 'Locator refused')
    raise ResearchError('new_delivery_refusal', 'Delivery refused')
'''}
        report = reach.analyze_sources(sources, {'notice': [notice + ':_build_refused_entry']},
                                       {site: reach.COMPUTED_CODE_SITES[site]})
        self.assertEqual(set(report['by_command']['notice']),
                         {'new_locator_refusal', 'new_delivery_refusal'})

    def test_selected_output_refusals_cover_replaced_and_superseded_heads(self):
        path = 'research_harness/review_refusal_replacement.py'
        site = path + ':require_selected_output:code'
        self.assertIn(site, reach.COMPUTED_CODE_SITES)
        sources = {path: '''
def require_selected_output(has_replacement):
    code = 'review_assignment_replaced' if has_replacement else 'review_assignment_superseded'
    raise ResearchError(code, 'The output is not selected')
'''}
        report = reach.analyze_sources(sources, {'selected': [path + ':require_selected_output']},
                                       {site: reach.COMPUTED_CODE_SITES[site]})
        self.assertEqual(set(report['by_command']['selected']),
                         {'review_assignment_replaced', 'review_assignment_superseded'})

    def test_fixture_regenerates_and_names_every_unreviewed_registry_row(self):
        expected = json.loads(FIXTURE.read_text())
        actual = reach.build_fixture(ROOT)
        self.assertEqual(actual, expected,
                         'Regenerate with /usr/bin/python3 ci/series_code_reach.py --write-fixture')
        self.assertIn('openness_search_stale', actual['reachable'])
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
