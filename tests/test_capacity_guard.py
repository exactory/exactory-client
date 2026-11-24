"""Closure checks keep installed implementation, executed evidence and stops independent."""

from unittest.mock import patch
import ast
import copy
import json
from pathlib import Path
import unittest

from ci import capacity_guard as guard
from ci.evidence_tests import require_evidence_tests
from research_harness import review_budget, review_route_capacity as capacity, review_transport

ROOT = Path(__file__).resolve().parents[1]
# Requirement AR20.4: the entries with the coverage stop_before_call after 0.92.0, which 0.93.0 and 0.94.0 complete.
LATER_RELEASE_GAPS = ('A10a', 'A10b', 'A18', 'A20', 'A22', 'A23', 'A26', 'Q14')
# The entries that 0.92.0 completes and whose evidence the unit lanes executed to a recorded review.
STAGE3_CONSTRUCTED = ('A1', 'A2', 'A5', 'A6', 'A7', 'A8', 'A12', 'A14', 'A15', 'A17', 'A25',
                      'Q2', 'Q5', 'Q7', 'Q8', 'Q10', 'Q13', 'Q17')


class CapacityGuardTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        self.matrix = guard.read_matrix()
        self.inventory = guard.read_transport_inventory()

    def test_forecast_limit_readers_have_declared_sites(self):
        sources = guard.read_plugin_sources(ROOT)
        self.assertEqual(guard.inspect_limit_readers(sources, self.inventory), [])
        sources['research_harness/new_forecast.py'] = 'def estimate():\n    return SESSION_PENDING_TURN_LIMIT\n'
        self.assertTrue(guard.inspect_limit_readers(sources, self.inventory))

    def test_forecast_admission_functions_are_pinned(self):
        sites = self.inventory['admission_functions']
        self.assertIn('research_harness/review_route_capacity.py:describe_session_stop', sites)
        self.assertIn('research_harness/review_protocol.py:invoke_assignment', sites)
        sources = guard.read_plugin_sources(ROOT)
        path = 'research_harness/review_route_capacity.py'
        sources[path] = sources[path].replace('def describe_session_stop(forecast):',
            'def describe_session_stop(forecast):\n    return None', 1)
        self.assertTrue(guard.inspect_call_sources(sources, self.inventory, review_budget.CALL_OPERATIONS))

    def test_prior_guard_requires_the_release_configuration_and_runtime_key(self):
        from research_harness.review_capacity_evidence import read_capacity_priors
        row = read_capacity_priors()[1]
        self.assertEqual(guard.validate_capacity_evidence([row]), [])
        for member in ('configuration', 'credential_mode'):
            changed = copy.deepcopy(row)
            if member == 'configuration':
                del changed['key'][member]
            else:
                del changed['key']['runtime'][member]
            self.assertIn(guard.PRIOR_KEY_MESSAGE, guard.validate_capacity_evidence([changed]))

    def test_source_description_is_stable_across_empty_ast_field_versions(self):
        node = ast.parse('def build(): return None')
        expected = guard.describe_ast(node)
        function = node.body[0]
        function._fields = function._fields + ('type_params',)
        function.type_params = []
        self.assertEqual(guard.describe_ast(node), expected)
        self.assertIn('Constant(value=None)', expected)
        self.assertIn('args=[]', expected)
        self.assertNotEqual(expected, guard.describe_ast(ast.parse('def build(): return 0')))

    def test_matrix_has_every_way_quantity_and_external_task(self):
        expected = ({'A' + str(i) for i in range(1, 28)} - {'A10'}) | {
            'A10a', 'A10b', 'A10c'} | {'Q' + str(i) for i in range(1, 19)} | {'T3'}
        self.assertEqual({row['id'] for row in self.matrix['entries']}, expected)
        self.assertEqual(guard.validate_matrix(self.matrix), [])

    def test_installing_units_does_not_promote_coverage(self):
        entry = copy.deepcopy(next(row for row in self.matrix['entries'] if row['id'] == 'A5'))
        before = entry['coverage']
        for unit in entry['units']['construction']:
            self.matrix['installed_units'].append(unit)
        self.assertEqual(guard.describe_coverage(entry, self.matrix['installed_units']), before)
        entry['coverage'] = 'constructed'
        self.assertTrue(guard.validate_entry(entry, {}))

    def test_staged_recovery_never_becomes_a_construction(self):
        entry = copy.deepcopy(next(row for row in self.matrix['entries'] if row['id'] == 'A10c'))
        entry['coverage'] = 'constructed'
        self.assertIn(guard.NO_CONSTRUCTION_MESSAGE, guard.validate_entry(entry, {}))

    def test_skipped_failed_missing_and_removed_stop_evidence_fail(self):
        entry = dict(id='A5', quantity='final', kinds=['final'], release='0.92.0',
            units=dict(path=['capacity-plan'], construction=['final-pages']), coverage='constructed',
            evidence=dict(tests=['acceptance'], reports=[]), tests=['acceptance'],
            required_stops=[dict(code='review_route_capacity_below_floor', tests=['floor'])])
        for status in ('skipped', 'failed', 'missing'):
            self.assertTrue(guard.validate_entry(entry, dict(acceptance='passed', floor=status)))
        self.assertEqual(guard.validate_entry(entry, dict(acceptance='passed', floor='passed')), [])
        entry['required_stops'][0]['tests'] = []
        self.assertIn(guard.STOP_EVIDENCE_MESSAGE, guard.validate_entry(entry, dict(acceptance='passed')))

    def test_removing_a_required_stop_row_fails_even_for_constructed_coverage(self):
        row = next(row for row in self.matrix['entries'] if row['id'] == 'A26')
        row['coverage'] = 'constructed'
        row['evidence']['tests'] = row['tests'][:]
        row['required_stops'] = []
        self.assertTrue(any(guard.STOP_EVIDENCE_MESSAGE in error for error in guard.validate_matrix(self.matrix)))

    def test_final_pages_cannot_remove_the_minimum_floor_stop(self):
        for trigger in ('constructed', 'capacity-plan', 'final-pages'):
            with self.subTest(trigger=trigger):
                matrix = copy.deepcopy(self.matrix)
                row = next(row for row in matrix['entries'] if row['id'] == 'A5')
                if trigger == 'constructed':
                    row['coverage'] = 'constructed'
                    row['evidence']['tests'] = row['tests'][:]
                else:
                    matrix['installed_units'].append(trigger)
                row['required_stops'] = [stop for stop in row['required_stops']
                                         if stop['code'] != 'review_route_capacity_below_floor']
                self.assertTrue(any(guard.STOP_EVIDENCE_MESSAGE in error
                    for error in guard.validate_matrix(matrix)))

    def test_stage2_constructions_require_installed_units_and_named_acceptance_evidence(self):
        promoted = {'A11', 'A16', 'A19', 'A21', 'Q9', 'Q11', 'Q15'}
        rows = {row['id']: row for row in self.matrix['entries']}
        for identifier in promoted:
            with self.subTest(entry=identifier):
                row = rows[identifier]
                self.assertEqual(row['coverage'], 'constructed')
                self.assertTrue(set(row['units']['path'] + row['units']['construction'])
                                <= set(self.matrix['installed_units']))
                self.assertTrue(row['evidence']['tests'])
                changed = copy.deepcopy(self.matrix)
                next(entry for entry in changed['entries'] if entry['id'] == identifier)['evidence']['tests'] = []
                self.assertTrue(guard.validate_matrix(changed))

    def test_stage3_constructions_have_installed_units_and_named_acceptance_evidence(self):
        rows = {row['id']: row for row in self.matrix['entries']}
        for identifier in STAGE3_CONSTRUCTED:
            with self.subTest(entry=identifier):
                row = rows[identifier]
                self.assertEqual(row['coverage'], 'constructed')
                self.assertTrue(set(row['units']['construction']) <= set(self.matrix['installed_units']))
                changed = copy.deepcopy(self.matrix)
                next(entry for entry in changed['entries'] if entry['id'] == identifier)['evidence']['tests'] = []
                self.assertIn(identifier + ': ' + guard.EVIDENCE_MESSAGE, guard.validate_matrix(changed))
        floor = next(stop for stop in rows['A5']['required_stops'] if stop['code'] == 'review_route_capacity_below_floor')
        self.assertIn('test_continuity_guard.ContinuityGuardTests.test_final_minimum_floor_stops_before_call_even_with_final_pages',
                      floor['tests'])

    def test_stage3_gap_table_is_the_later_releases_and_the_named_missing_evidence(self):
        self.assertEqual(guard.inspect_gap_table(self.matrix, LATER_RELEASE_GAPS), [])
        table = guard.build_gap_table(self.matrix)
        self.assertEqual(table['without_construction'], ['A10c', 'T3'])
        self.assertTrue(all(row['missing_evidence'] for row in table['rows'] if row['id'] not in LATER_RELEASE_GAPS))
        # A constructed entry that loses its coverage without a statement of what is missing leaves the table wrong.
        changed = copy.deepcopy(self.matrix)
        next(row for row in changed['entries'] if row['id'] == 'A5')['coverage'] = 'stop_before_call'
        self.assertEqual(guard.inspect_gap_table(changed, LATER_RELEASE_GAPS), [guard.GAP_TABLE_MESSAGE])
        # A statement of missing evidence for an entry that has its evidence is refused as well.
        changed = copy.deepcopy(self.matrix)
        changed['missing_evidence']['A1'] = 'An entry that has its evidence.'
        self.assertEqual(guard.inspect_gap_table(changed, LATER_RELEASE_GAPS), [guard.GAP_TABLE_MESSAGE])

    def test_stage3_roles_turn_kinds_and_composition_exceptions_are_complete(self):
        from research_harness import review_composition, review_field_classes, review_protocol
        from unittest.mock import patch
        arguments = (self.inventory, capacity.list_capacity_recovery_rows(), review_protocol.ROLES,
                     review_composition.ROLES_WITHOUT_RULES, review_field_classes.FIELD_CLASSES)
        self.assertEqual(guard.inspect_stage3_tables(*arguments), [])
        # Finding 6 of part c: a role of this release in the list of roles without a rule fails the guard.
        for role in ('slate', 'native_math'):
            with self.subTest(role=role):
                listed = dict(review_composition.ROLES_WITHOUT_RULES, **{role: {}})
                self.assertTrue(guard.inspect_stage3_tables(*arguments[:3], listed, arguments[4]))
        rows = [row for row in arguments[1] if (row['kind'], row['protocol']) != ('combine', 'exactory-independent-review-sources-v4')]
        self.assertTrue(guard.inspect_stage3_tables(arguments[0], rows, *arguments[2:]))
        self.assertTrue(guard.inspect_stage3_tables(*arguments[:4], {}))
        # Every prompt of a new version records the capacity of its turn.
        from research_harness import review_parts, review_session_v4
        bounds = dict(answer_bound=1000, request_bound=20000)
        prompt = review_session_v4.build_turn_prompt('bar', 'request', bounds, orchestration='problem_creation')
        self.assertEqual(prompt['parameters']['capacity'], bounds)
        header = dict(adapter='problem_result', kind='read', protocol='exactory-review-parts-v3', capacity=bounds)
        with patch.object(review_protocol, 'canonical_prompt', side_effect=lambda role, **parameters: parameters):
            self.assertEqual(review_parts.build_turn_prompt('result', {}, header)['review_parts']['capacity'], bounds)

    def test_v6_and_v7_constructions_keep_their_unit_evidence(self):
        rows = {row['id']: row for row in self.matrix['entries']}
        obligations = {row['unit']: row for row in self.matrix['merge_obligations']}
        self.assertIn('capacity-guard-2', self.matrix['installed_units'])
        for unit in ('UB10', 'carried-selection', 'carried-integration',
                     'reading-v7-turns', 'reading-v7-verification'):
            obligation = obligations[unit]
            for identifier in obligation['entries']:
                with self.subTest(unit=unit, entry=identifier):
                    tests = set(obligation['per_entry'][identifier]['tests'])
                    self.assertTrue(tests)
                    self.assertTrue(tests <= set(rows[identifier]['tests']))
                    self.assertTrue(tests <= set(rows[identifier]['evidence']['tests']))
        for identifier in ('A4', 'Q1', 'Q4', 'Q12'):
            self.assertEqual(rows[identifier]['coverage'], 'constructed')
            self.assertIn('v6', rows[identifier]['coverage_scope'])
            self.assertIn('v7', rows[identifier]['coverage_scope'])

    def test_stage2_production_markers_require_evidence_even_when_installation_is_omitted(self):
        sources = guard.read_plugin_sources(ROOT)
        units = ('capacity-plan', 'task-size', 'handles', 'capacity-notice', 'reading-forecast',
                 'reading-v7-state', 'refusal-notice', 'refusal-replacement', 'refusal-planner',
                 'exhaustion-repair', 'UB1', 'UB2', 'UB3', 'UB5', 'UB6', 'UC3', 'UC4')
        for unit in units:
            with self.subTest(unit=unit):
                matrix = copy.deepcopy(self.matrix)
                matrix['installed_units'].remove(unit)
                obligation = next(row for row in matrix['merge_obligations'] if row['unit'] == unit)
                identifier = obligation['entries'][0]
                removed = obligation['per_entry'][identifier]['tests'][0]
                entry = next(row for row in matrix['entries'] if row['id'] == identifier)
                for tests in (entry['tests'], entry['evidence']['tests']):
                    tests[:] = [test for test in tests if test != removed]
                errors = guard.inspect_merge_obligations(matrix, sources)
                self.assertTrue(any(error == guard.MERGE_MESSAGE + ' ' + unit + ':' + identifier
                                    for error in errors), errors)

    def test_a_paid_verification_report_cannot_be_an_unregistered_label(self):
        row = next(row for row in self.matrix['entries'] if row['id'] == 'A10b')
        row['coverage'] = 'constructed'
        row['evidence'] = dict(tests=row['tests'][:], reports=['unstored-report'])
        self.assertTrue(any(guard.EVIDENCE_MESSAGE in error for error in guard.validate_matrix(self.matrix)))

    def test_gap_table_is_the_matrix_projection_and_lists_the_exception(self):
        table = guard.build_gap_table(self.matrix)
        self.assertEqual(table['gaps'], sorted(row['id'] for row in self.matrix['entries']
            if row['coverage'] == 'stop_before_call'))
        self.assertEqual(table['without_construction'], ['A10c', 'T3'])

    def test_source_calls_are_budgeted_and_new_call_sites_fail(self):
        sources = guard.read_plugin_sources(ROOT)
        self.assertEqual(guard.inspect_call_sources(sources, self.inventory, review_budget.CALL_OPERATIONS), [])
        sources['research_harness/new_adapter.py'] = 'def call(route, request):\n    return adapter.send_request(route, request)\n'
        self.assertTrue(guard.inspect_call_sources(sources, self.inventory, review_budget.CALL_OPERATIONS))
        self.assertTrue(guard.inspect_call_sources(guard.read_plugin_sources(ROOT), self.inventory, ('review.invoke',)))

    def test_aliased_sender_import_cannot_bypass_call_inventory(self):
        for name in ('send_request', 'observe'):
            sources = guard.read_plugin_sources(ROOT)
            sources['research_harness/new_call.py'] = (
                'from research_harness.review_transport import ' + name + ' as send\n'
                'def invoke(route, request):\n    return send(route, request)\n')
            self.assertTrue(guard.inspect_call_sources(sources, self.inventory, review_budget.CALL_OPERATIONS))

    def test_binary_inventory_preserves_limits_and_unknown_token_rules(self):
        route = dict(adapter='openai_responses_v1', model='gpt-6-astra',
                     endpoint='https://api.openai.com/v1/responses')
        rows = self.inventory['adapters']['openai_responses_v1'].get('binary_rules', [])
        self.assertIn(dict(endpoint=route['endpoint'], model=route['model'],
                           rules=capacity.describe_binary_rules(route)), rows)
        visual = self.inventory['adapters']['codex_cli_visual_v1']['binary_rules'][0]['rules']['image']
        self.assertEqual(visual['input_limits']['bytes']['value'], 33554432)
        self.assertIsNone(visual['token_rule'])
        changed = copy.deepcopy(self.inventory)
        changed['adapters']['openai_responses_v1']['binary_rules'] = []
        self.assertTrue(guard.inspect_transports(guard.read_plugin_sources(ROOT), changed,
                        {review_transport.ADAPTER} | set(review_transport.LOCAL_ADAPTERS)))

    def test_native_packet_producer_changes_need_a_source_review(self):
        sources = guard.read_plugin_sources(ROOT)
        module = next(path for path in sources if path.startswith('skills/math-solver/harness/search_controller/'))
        sources[module] += "\ndef build_unreachable_packet(packet):\n    packet['new_quantity'] = []\n    return packet\n"
        self.assertTrue(guard.inspect_call_sources(sources, self.inventory, review_budget.CALL_OPERATIONS))

    def test_limit_readers_have_one_reviewed_home(self):
        sources = guard.read_plugin_sources(ROOT)
        self.assertEqual(guard.inspect_limit_readers(sources, self.inventory), [])
        sources['research_harness/new_size.py'] = "def size(route):\n    return route['configuration']['max_output_tokens']\n"
        self.assertTrue(guard.inspect_limit_readers(sources, self.inventory))

    def test_every_adapter_limit_signal_and_runtime_member_has_a_row(self):
        sources = guard.read_plugin_sources(ROOT)
        adapters = {review_transport.ADAPTER} | set(review_transport.LOCAL_ADAPTERS)
        self.assertEqual(guard.inspect_transports(sources, self.inventory, adapters), [])
        self.assertTrue(guard.inspect_transports(sources, self.inventory, adapters | {'future_adapter'}))

        key = 'research_harness/review_transport.py'
        sources[key] += "\ndef future_transport():\n    raise ResearchError('review_new_failure', 'new')\n"
        self.assertTrue(guard.inspect_transports(sources, self.inventory, adapters))
        sources = guard.read_plugin_sources(ROOT)
        key = 'research_harness/review_claude_transport.py'
        sources[key] = sources[key].replace("'credential_mode':", "'new_runtime_member': 'new', 'credential_mode':", 1)
        self.assertTrue(guard.inspect_transports(sources, self.inventory, adapters))


    def test_runtime_inventory_keeps_nested_hash_inputs_inside_their_member(self):
        source = "def runtime():\n    return {'adapter': 'synthetic', 'implementation_sha256': digest({'renderer': 'bytes'})}\n"
        self.assertEqual(guard.collect_transport_facts(source)['runtime'],
                         ['adapter', 'implementation_sha256'])
        source = "def runtime():\n    unrelated = {'nested': 1}\n    result = {'adapter': 'synthetic'}\n    result.update(input_contract='explicit')\n    return result\n"
        self.assertEqual(guard.collect_transport_facts(source)['runtime'], ['adapter', 'input_contract'])

    def test_prior_keys_and_joining_grounds_are_required(self):
        from capacity_evidence_fixtures import build_capacity_runtime
        row = dict(input_tokens=200000)
        self.assertIn(guard.PRIOR_KEY_MESSAGE, guard.validate_capacity_evidence([row]))
        row['key'] = dict(adapter='codex_cli_v1', endpoint='codex://local', model='synthetic',
            runtime=build_capacity_runtime('codex_cli_v1'), configuration={})
        self.assertEqual(guard.validate_capacity_evidence([row]), [])
        row['joined_keys'] = [dict(row['key'], endpoint='codex://other')]
        self.assertIn(guard.JOIN_GROUND_MESSAGE, guard.validate_capacity_evidence([row]))
        row['documented_ground'] = dict(url='https://example.test/provider-contract',
                                       statement='Same accepted input capacity.')
        self.assertEqual(guard.validate_capacity_evidence([row]), [])

    def test_actual_prior_source_uses_the_keyed_c3_declaration(self):
        from research_harness.review_capacity_evidence import read_capacity_priors
        sources = guard.read_plugin_sources(ROOT)
        self.assertEqual(guard.inspect_prior_sources(sources, self.inventory), [])
        self.assertEqual(guard.inspect_prior_sources(sources, self.inventory, ['route-limits-C3']), [])
        module = 'research_harness/review_capacity_evidence.py'
        sources[module] = "CAPACITY_PRIORS = ({'input_tokens': 200000},)"
        self.assertIn(guard.PRIOR_KEY_MESSAGE, guard.inspect_prior_sources(sources, self.inventory))
        row = read_capacity_priors()[1]
        sources[module] = 'CAPACITY_PRIORS = ' + repr((row,))
        self.assertEqual(guard.inspect_prior_sources(sources, self.inventory), [])
        encoded = (row['id'], json.dumps(row['key']), row['input_tokens'], 0, '2026-11-14')
        sources[module] = 'CAPACITY_PRIORS = ' + repr((encoded,))
        self.assertEqual(guard.inspect_prior_sources(sources, self.inventory), [])

    def test_roles_task_kinds_and_pending_merge_markers_are_complete(self):
        from research_harness.review_protocol import ROLES
        self.assertEqual(guard.inspect_task_tables(self.inventory, ROLES, capacity.TURN_KINDS), [])
        self.assertTrue(guard.inspect_task_tables(self.inventory, ROLES + ('new_role',), capacity.TURN_KINDS))
        self.assertTrue(guard.inspect_task_tables(self.inventory, ROLES, capacity.TURN_KINDS + ('new_kind',)))
        sources = guard.read_plugin_sources(ROOT)
        self.assertEqual(guard.inspect_merge_obligations(self.matrix, sources), [])
        missing = copy.deepcopy(self.matrix)
        missing['installed_units'].remove('session-forecast-stop')
        obligation = next(row for row in missing['merge_obligations'] if row['unit'] == 'session-forecast-stop')
        obligation['per_entry']['A2']['tests'].append(
            'test_session_forecast.SessionForecastTests.test_non_session_has_no_session_forecast')
        self.assertEqual(guard.validate_matrix(missing), [])
        self.assertTrue(guard.inspect_merge_obligations(missing, sources))

    def test_every_declaration_points_into_the_closure_matrix(self):
        from ci.capacity_packet_inventory import read_packet_registry
        registry = read_packet_registry()
        self.assertEqual(guard.inspect_declaration_entries(self.matrix, self.inventory, registry), [])
        self.inventory['task_rules']['single']['entries'].append('not_a_matrix_entry')
        self.assertEqual(guard.inspect_declaration_entries(self.matrix, self.inventory, registry),
            [guard.DECLARATION_ENTRY_MESSAGE])

    def test_undeclared_member_has_no_task_kind_in_the_guard(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        import test_review_continuation as fixtures
        from research_harness import review_protocol, review_transport
        case = fixtures.ContinuationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        assignment = case.assignment
        records = case.case.read_records()
        packet = review_protocol._read_json(case.case.artifacts, assignment['packet'])
        route = records['review_route'][assignment['route_id']]
        request = review_protocol.build_assignment_request(records, case.case.artifacts, assignment)
        registry = read_packet_registry()
        self.assertTrue(guard.describe_packet_task('bar', packet, route=route, request=request,
            registry=registry)['kind'])
        changed = dict(packet, undeclared_capacity_member=['x'])
        self.assertTrue(validate_packet('bar', changed, registry))
        with self.assertRaisesRegex(ValueError, guard.PACKET_KIND_MESSAGE):
            guard.describe_packet_task('bar', changed, route=route, request=request, registry=registry)
        emitted = review_transport.request_body(route, assignment['prompt']['text'], changed)
        with self.assertRaisesRegex(ValueError, guard.PACKET_KIND_MESSAGE):
            guard.describe_packet_task('bar', packet, route=route, request=emitted, registry=registry)

    def test_pending_units_have_explicit_merge_obligations(self):
        units = {row['unit'] for row in self.matrix['merge_obligations']}
        self.assertTrue({'review-stop', 'series-driver-stop', 'call-queue', 'posted-closed-calls',
            'session-forecast-stop', 'size-reports', 'single-request-series', 'binary-input-measure',
            'uncovered-review-stop', 'capacity-probe', 'route-limits-C1', 'route-limits-C3',
            'route-limits-C10', 'capacity-plan', 'task-size', 'handles', 'capacity-notice',
            'reading-forecast', 'reading-v7-state', 'refusal-notice', 'refusal-replacement',
            'refusal-planner', 'exhaustion-repair', 'dead-assignments', 'registry-rows',
            'continuity-guard-2', 'UB1', 'UB2', 'UB3', 'UB4', 'UB5', 'UB6', 'UB7', 'UB8',
            'UB9', 'UC1', 'UC3', 'UC4'}.issubset(units))
        for row in self.matrix['merge_obligations']:
            self.assertTrue(row['entries'] and row['tests'])
            self.assertIsInstance(row['stops'], list)

    def test_matrix_evidence_executes_and_no_evidence_can_be_silently_skipped(self):
        # Each evidence test exists, loads and carries no skip or expected-failure mark. The run that executes it
        # gives its result: the verification of the run requires it to have run and passed (ci/evidence_tests.py).
        identifiers = guard.collect_evidence_tests(self.matrix)
        self.assertEqual(require_evidence_tests(identifiers), {})
        self.assertEqual(guard.validate_matrix(self.matrix), [])


if __name__ == '__main__':
    unittest.main()
