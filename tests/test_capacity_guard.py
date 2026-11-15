"""Closure checks keep installed implementation, executed evidence and stops independent."""

import ast
import copy
import json
from pathlib import Path
import unittest

from ci import capacity_guard as guard
from research_harness import review_budget, review_route_capacity as capacity, review_transport

ROOT = Path(__file__).resolve().parents[1]


class CapacityGuardTests(unittest.TestCase):
    def setUp(self):
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
        for identifier in ('A4', 'A5', 'A7', 'A25'):
            self.assertEqual(rows[identifier]['coverage'], 'stop_before_call')
            self.assertIn('pending:', rows[identifier]['coverage_scope'])
            self.assertTrue(rows[identifier]['evidence']['tests'])

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
        identifiers = guard.collect_evidence_tests(self.matrix)
        outcomes = guard.run_evidence_tests(identifiers)
        failed = {test: result for test, result in outcomes.items() if result != 'passed'}
        self.assertEqual(guard.validate_matrix(self.matrix, outcomes), [], failed)
        self.assertEqual(set(outcomes.values()), {'passed'}, failed)


if __name__ == '__main__':
    unittest.main()
