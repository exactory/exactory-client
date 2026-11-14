#!/usr/bin/env python3
"""Read-only, conservative error-code analysis for the D2 continuity guard.

This is a function/reference graph, not an import closure. Resolved imports and
lexical names take precedence; unresolved attribute calls include every function
of that name. Function references include callbacks and locally imported aliases.
Code parameters propagate to a fixed point before their call arguments are read.
No harness or controller module is imported to perform the source analysis.

Unresolved receiver types deliberately overapproximate: a mapping's ``get`` can
reach a package method named ``get``. Several command roots consequently share a
large domain. This guard does not claim that each such path is feasible.
"""

import argparse
import ast
from collections import defaultdict
import json
from pathlib import Path
import sys


SOURCE_FOLDERS = ('research_harness', 'skills/math-solver/harness')
PLANNER_MODULES = frozenset(('research_harness.review_continuation',
                            'research_harness.review_failure_class',
                            'research_harness.review_plan_steps',
                            'research_harness.stop_classes'))

# Each nonconstant constructor-code expression has a reviewed finite domain.
# Keys use path, qualified function and source expression, not shifting line numbers.
COMPUTED_CODE_SITES = {
    'research_harness/automatic_overrides.py:_refuse:"gate_override_" + code': {
        'codes': ['gate_override_' + suffix for suffix in (
            'stop_missing', 'stop_untrusted', 'stop_class', 'budget_exhausted', 'quality_condition',
            'internal_only', 'user_required', 'stop_stale', 'gate_ready', 'other_obligations',
            'reviews_uncovered', 'public_write')]},
    "research_harness/direction_reviews.py:_collect_task_graph:problem['code']": {
        'sources': ['research_harness/review_staged_protocol.py:collect_observations']},
    'research_harness/extraction_units.py:_SupervisedTools.__call__:error["code"]': {
        'sources': ['research_harness/extraction_units.py:run_bounded_tool']},
    'research_harness/fulltext_transfer.py:FulltextTransfer.run:error.code': {
        'sources': ['research_harness/fulltext_transfer.py:FulltextTransfer.run']},
    'research_harness/http.py:HttpClient._get:code': {'codes': ['rate_limited', 'http_status']},
    'research_harness/http.py:HttpClient._get:error.code': {
        'sources': ['research_harness/http.py:HttpClient._get']},
    'research_harness/http.py:_StreamingReader.read:self.limit_code': {
        'codes': ['fulltext_storage_limit', 'response_too_large', 'fulltext_transfer_changed']},
    'research_harness/oai_cohort.py:validate_chain:error.code': {
        'sources': ['research_harness/oai_cohort.py:validate_chain']},
    'research_harness/openness_minimum.py:require_unblocked_outcome:code': {
        'codes': ['openness_search_gap', 'openness_reading_gap', 'openness_settling_source']},
    'research_harness/openness_minimum.py:require_unblocked_outcome:error["code"]': {
        'sources': ['research_harness/openness_minimum.py:list_minimum_gaps']},
    'research_harness/probe_provenance.py:read_probe_pin_source:code': {'codes': ['unsafe_path', 'storage_io']},
    'research_harness/problem_accounts.py:validate_account_evidence:code': {
        'codes': ['invalid_input', 'problem_answer_evidence_missing', 'problem_boundary_evidence_missing',
                  'problem_explanatory_evidence_missing', 'problem_relation_evidence_missing']},
    'research_harness/problem_accounts.py:validate_accounts:"problem_relation_evidence_missing" if entry["standing"] == "established" else None': {
        'codes': ['problem_relation_evidence_missing']},
    'research_harness/problem_accounts.py:validate_fact:required_code or "invalid_input"': {'codes': ['invalid_input']},
    'research_harness/problem_candidates.py:_verify_bars:pending[0]["code"]': {
        'sources': ['research_harness/research_decisions.py:_independence']},
    'research_harness/problem_delivery.py:prepare_decision:pending[0]["code"]': {
        'sources': ['research_harness/problem_delivery.py:prepare_decision']},
    'research_harness/publication_files.py:restore_bundle:error.code': {
        'sources': ['research_harness/publication_files.py:restore_bundle']},
    "research_harness/record_check.py:check_continuation:plan.get('code') or 'record_unknown_stop'": {
        'codes': ['record_unknown_stop'], 'sources': ['research_harness/review_continuation.py:plan_next']},
    'research_harness/research_decisions.py:prepare_objective_commitment:code': {
        'codes': ['direction_evidence_unassessed', 'research_decision_required']},
    "research_harness/review_composition.py:_reject:'review_composition_pending' if pending else 'review_composition_conflict'": {
        'codes': ['review_composition_pending', 'review_composition_conflict']},
    "research_harness/review_output_correction.py:invoke_correction.finish:error['code']": {
        'sources': ['research_harness/review_output_correction.py:invoke_correction']},
    "research_harness/review_protocol.py:_assignment_state:'review_context_' + state['context_status']": {
        'codes': ['review_context_unverified', 'review_context_contaminated']},
    "research_harness/review_staged_followups.py:_raise_composition_error:'review_composition_pending' if pending else 'review_composition_conflict'": {
        'codes': ['review_composition_pending', 'review_composition_conflict']},
    "research_harness/review_staged_protocol.py:build_state:'review_context_' + state['context_status']": {
        'codes': ['review_context_unverified', 'review_context_contaminated']},
    "research_harness/review_staged_protocol.py:collect_current_planning_records:obligations[0]['code']": {
        'sources': ['research_harness/review_staged_protocol.py:collect_observations']},
    "research_harness/review_staged_protocol.py:invoke_task.validate_capture:obligations[0]['code']": {
        'sources': ['research_harness/review_staged_protocol.py:collect_observations']},
    "research_harness/review_staged_protocol.py:invoke_task:'assessment_incomplete' if head['state'] == 'incomplete' else 'review_resampling_forbidden'": {
        'codes': ['assessment_incomplete', 'review_resampling_forbidden']},
    "research_harness/review_staged_protocol.py:plan_frontier.prepare:obligations[0]['code']": {
        'sources': ['research_harness/review_staged_protocol.py:collect_observations']},
    "research_harness/review_staged_protocol.py:plan_remaining_tasks:obligations[0]['code']": {
        'sources': ['research_harness/review_staged_protocol.py:collect_observations']},
    "research_harness/review_staged_protocol.py:prepare_attempt:obligations[0]['code']": {
        'sources': ['research_harness/review_staged_protocol.py:collect_observations']},
    "research_harness/review_staged_protocol.py:prepare_partitions:obligations[0]['code']": {
        'sources': ['research_harness/review_staged_protocol.py:collect_observations']},
    "research_harness/review_staged_protocol.py:prepare_task_plan:obligations[0]['code']": {
        'sources': ['research_harness/review_staged_protocol.py:collect_observations']},
    'research_harness/review_task_execution.py:reserve_task:exhaustion["code"]': {
        'sources': ['research_harness/review_task_execution.py:_consume_resources']},
    'research_harness/rounds.py:_RoundEvidence.many:self.error_code': {'codes': ['invalid_round']},
    "research_harness/rounds.py:_canonical_adapter.prepare:item['code']": {
        'sources': ['research_harness/rounds.py:find_claim_revision_obligations']},
    "research_harness/rounds.py:record_round.prepare:item['code']": {
        'sources': ['research_harness/rounds.py:find_claim_revision_obligations']},
    'research_harness/rounds.py:record_round.prepare:owed[0]["code"]': {
        'sources': ['research_harness/rounds.py:_list_analysis_obligations']},
    'research_harness/synthesis.py:_record.prepare:item["code"]': {
        'codes': ['configuration_missing', 'profile_mismatch', 'constitution_revalidation_required', 'objective_mismatch']},
    'research_harness/synthesis.py:_synthesis_state:kind + "_missing"': {
        'codes': ['rationale_missing', 'innovation_missing', 'standards_missing', 'context_missing']},
    'skills/math-solver/harness/search_controller/attempt_delivery.py:deliver_snapshot:error.code': {
        'sources': ['skills/math-solver/harness/search_controller/attempt_delivery.py:deliver_snapshot']},
    'skills/math-solver/harness/search_controller/openness.py:_call:error.code': {
        'sources': ['research_harness/openness_minimum.py:validate_minimum',
                    'research_harness/openness_minimum.py:audit_minimum',
                    'research_harness/openness_minimum.py:validate_minimum_bindings']},
    'skills/math-solver/harness/search_controller/provenance.py:check_normalized_identity:error.code': {
        'sources': ['skills/math-solver/harness/search_controller/provenance.py:check_normalized_identity']},
    'skills/math-solver/harness/search_controller/provenance.py:collect_import_sources:"exploratory_evidence" if state["service"].get("probe_origins", {}).get(current) else "missing_evidence"': {
        'codes': ['exploratory_evidence', 'missing_evidence']},
    'skills/math-solver/harness/search_controller/reviewer.py:require_review:"review_parts_incomplete" if review_parts.KEY in assignment else "openness_reading_incomplete"': {
        'codes': ['review_parts_incomplete', 'openness_reading_incomplete']},
    'research_harness/acquisition.py:_page_update:error.code': {
        'sources': ['research_harness/acquisition.py:_page_update']},
    'research_harness/acquisition.py:acquire_fulltext:extracted["status"]': {
        'sources': ['research_harness/acquisition.py:acquire_fulltext']},
    'research_harness/automatic_overrides.py:record_series_stop:code': {
        'sources': ['research_harness/review_continuation.py:plan_next']},
    'research_harness/cli.py:run:args.code': {
        'sources': ['research_harness/cli.py:status_report']},
    'research_harness/openness_minimum.py:list_minimum_gaps.add_gap:code': {
        'codes': ['openness_search_gap', 'openness_reading_gap', 'openness_settling_source']},
    'research_harness/problem_delivery.py:assess_source_requests:task["code"]': {
        'sources': ['research_harness/problem_delivery.py:assess_source_requests']},
    'research_harness/publication_files.py:find_bundle_drift:code': {
        'sources': ['research_harness/publication_files.py:find_bundle_drift']},
    'research_harness/reading.py:required_unit_obligations:p["code"]': {
        'sources': ['research_harness/reading.py:required_unit_obligations']},
    'research_harness/record_check.py:check_record:type(error).__name__': {
        'codes': ['OSError', 'JSONDecodeError', 'Error', 'SameFileError', 'SpecialFileError',
                  'ExecError', 'ReadError', 'RegistryError', 'BlockingIOError', 'ChildProcessError',
                  'ConnectionError', 'BrokenPipeError', 'ConnectionAbortedError', 'ConnectionRefusedError',
                  'ConnectionResetError', 'FileExistsError', 'FileNotFoundError', 'InterruptedError',
                  'IsADirectoryError', 'NotADirectoryError', 'PermissionError', 'ProcessLookupError', 'TimeoutError']},
    'research_harness/record_check.py:describe_capacity_decision:error.code': {
        'sources': ['research_harness/record_check.py:check_prospective_request']},
    'research_harness/report_views.py:_obligations:_short(code, 48)': {
        'sources': ['research_harness/report_views.py:_obligations']},
    'research_harness/report_views.py:_summarize_bundle_drift:item["code"]': {
        'sources': ['research_harness/publication_files.py:find_bundle_drift']},
    'research_harness/report_views.py:next_summary:item["code"]': {
        'sources': ['research_harness/report_views.py:next_summary']},
    'research_harness/research_decisions.py:_assess_problem_coverage.assess:delivery.code': {
        'sources': ['research_harness/research_decisions.py:_assess_problem_coverage']},
    "research_harness/review_call_capture.py:capture_call:getattr(error, 'code', 'review_correction_invalid')": {
        'codes': ['review_correction_invalid'], 'sources': ['research_harness/review_call_capture.py:capture_call']},
    "research_harness/review_codex_invocation.py:capture_observation:getattr(error, 'code', 'review_context_unverified')": {
        'codes': ['review_context_unverified'], 'sources': ['research_harness/review_codex_invocation.py:capture_observation']},
    "research_harness/review_continuation.py:_plan_head:blocking[0]['code']": {
        'sources': ['research_harness/review_continuation.py:_plan_head']},
    "research_harness/review_continuation.py:_plan_head:item['code']": {
        'sources': ['research_harness/review_continuation.py:_plan_head']},
    "research_harness/review_continuation.py:_plan_staged_action:task['code']": {
        'sources': ['research_harness/review_continuation.py:_plan_staged_action']},
    "research_harness/review_continuation.py:plan_next:getattr(detail_error, 'code', 'review_input_unreadable')": {
        'codes': ['review_input_unreadable'], 'sources': ['research_harness/review_continuation.py:plan_next']},
    "research_harness/review_continuation.py:plan_next:result['code']": {
        'sources': ['research_harness/review_continuation.py:plan_next']},
    "research_harness/review_failure_class.py:plan_after_failure:recovery.get('code', OUTCOME_CODES[outcome])": {
        'sources': ['research_harness/review_failure_class.py:plan_after_failure']},
    "research_harness/review_failure_class.py:plan_capacity_recovery:(replacement or {}).get('code', OUTCOME_CODES[outcome])": {
        'sources': ['research_harness/review_failure_class.py:plan_capacity_recovery']},
    "research_harness/review_output_correction.py:_prepare_packet:failure['code']": {
        'sources': ['research_harness/review_refusal.py:refused_attempts']},
    "research_harness/review_output_correction.py:invoke_correction.finish:getattr(error, 'code', 'review_correction_invalid')": {
        'codes': ['review_correction_invalid'], 'sources': ['research_harness/review_output_correction.py:invoke_correction']},
    'research_harness/review_plan_steps.py:complete_plan:code': {
        'sources': ['research_harness/review_continuation.py:plan_next']},
    "research_harness/review_refusal.py:refused_attempts:failure['code']": {
        'sources': ['research_harness/review_refusal.py:refused_attempts']},
    'research_harness/review_staged.py:_record_completion_rejection:error.code': {
        'sources': ['research_harness/review_staged.py:_record_completion_rejection']},
    'research_harness/review_staged.py:_summarize_completion_issues:code': {
        'sources': ['research_harness/review_staged.py:_summarize_completion_issues']},
    'research_harness/review_staged_protocol.py:invoke_task.prepare_exhaustion:error.code': {
        'sources': ['research_harness/review_staged_protocol.py:invoke_task']},
    'research_harness/review_staged_protocol.py:prepare_capture_recovery:error.code': {
        'sources': ['research_harness/review_staged_protocol.py:prepare_capture_recovery']},
    "research_harness/review_transport.py:observe:getattr(error, 'code', 'review_transport_failure')": {
        'codes': ['review_transport_failure'], 'sources': ['research_harness/review_transport.py:observe']},
    'research_harness/review_worker.py:run_worker:error.code if isinstance(error, ResearchError) else "review_worker_failed"': {
        'codes': ['review_worker_failed'], 'sources': ['research_harness/review_worker.py:run_worker']},
    'skills/math-solver/harness/search_controller/cli.py:print_error:error.code': {
        'sources': ['skills/math-solver/harness/search_controller/cli.py:run_search']},
    'skills/math-solver/harness/search_controller/execution.py:launch.require_launcher_end:refusal_error.code': {
        'sources': ['skills/math-solver/harness/search_controller/execution.py:launch']},
}

# These are public command entry points, not a second registry of error codes.
# The CLI's operation table supplies the ordinary commands below; these commands
# have dispatchers outside that table.
EXTERNAL_COMMAND_ROOTS = {
    'plan_next': ('research_harness/review_continuation.py:plan_next',),
    'run_series': ('research_harness/review_series.py:run_series',),
    'review-series': ('research_harness/review_series.py:run_series',),
    'search review-packet': ('skills/math-solver/harness/search_controller/reviewer.py:export_packet',),
    'search accept': ('skills/math-solver/harness/search_controller/service.py:Controller.command',),
    'export': ('research_harness/integration.py:export_workspace',
               'research_harness/problem_openness.py:export_openness',
               'research_harness/native_export.py:export_native'),
    'record-check': ('research_harness/record_check.py:check_record',),
    'fulltext': ('research_harness/acquisition.py:acquire_fulltext',),
}
SERIES_COMMANDS = (
    'review-run', 'review-assignment', 'review-attempt', 'review-output-correction',
    'review-probe', 'review-route', 'review-context', 'review-budget',
    'review-adjudication', 'value-review', 'problem-openness', 'bind-verdict',
    'manuscript-review', 'review', 'gate-auto-pass', 'select-main-capture',
)
UNAVAILABLE_COMMANDS = {'review-stop': 'The installed CLI has no public review-stop operation yet.'}


def walk_scope(node):
    """Walk a function body without charging a nested function's body to it."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield from walk_scope(child)


def describe_module(path):
    name = path[:-3].replace('/', '.')
    if '/harness/' in path:
        name = path.split('/harness/', 1)[1][:-3].replace('/', '.')
    return name[:-9] if name.endswith('.__init__') else name


def collect_bindings(nodes):
    bindings = defaultdict(list)

    def bind(target, value):
        if isinstance(target, ast.Name):
            bindings[target.id].append(value)
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for member, supplied in zip(target.elts, value.elts):
                bind(member, supplied)

    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                bind(target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            bind(node.target, node.value)
        elif isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.iter, (ast.Tuple, ast.List)):
            for value in node.iter.elts:
                bind(node.target, value)
    return bindings


def collect_imports(nodes, module):
    imports = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = module.split('.')[:-node.level]
                target = '.'.join(prefix + ([node.module] if node.module else []))
            else:
                target = node.module or ''
            for alias in node.names:
                imports[alias.asname or alias.name] = target + '.' + alias.name
    return imports


def build_index(sources):
    functions, modules, by_name, by_symbol = {}, {}, defaultdict(set), defaultdict(set)
    classes = defaultdict(set)
    class_symbols = set()
    constructors = {'ResearchError', 'SearchError'}
    trees = {path: ast.parse(source, filename=path) for path, source in sources.items()}
    changed = True
    while changed:
        before = set(constructors)
        for tree in trees.values():
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    constructors.update(alias.asname or alias.name for alias in node.names
                                        if alias.name.rsplit('.', 1)[-1] in constructors)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    name = getattr(node.value, 'id', getattr(node.value, 'attr', ''))
                    if name in constructors:
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        constructors.update(target.id for target in targets if isinstance(target, ast.Name))
                if isinstance(node, ast.ClassDef) and any(
                        getattr(base, 'id', getattr(base, 'attr', '')) in constructors for base in node.bases):
                    constructors.add(node.name)
        changed = constructors != before

    for path, tree in trees.items():
        module = describe_module(path)
        nodes = list(walk_scope(tree))
        modules[module] = dict(path=path, bindings=collect_bindings(nodes),
                               imports=collect_imports(nodes, module))

        def register(node, prefix='', parent=None):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = prefix + child.name
                    key = path + ':' + qualified
                    positional = child.args.posonlyargs + child.args.args
                    parameters = [argument.arg for argument in positional + child.args.kwonlyargs]
                    defaults = dict(zip([a.arg for a in positional][-len(child.args.defaults):],
                                        child.args.defaults)) if child.args.defaults else {}
                    defaults.update({argument.arg: value for argument, value in
                                     zip(child.args.kwonlyargs, child.args.kw_defaults) if value is not None})
                    own_nodes = list(walk_scope(child))
                    functions[key] = dict(key=key, path=path, module=module, qualified=qualified,
                        node=child, nodes=own_nodes, parameters=parameters,
                        parent=parent,
                        positional=[argument.arg for argument in positional], defaults=defaults,
                        bindings=collect_bindings(own_nodes), imports=collect_imports(own_nodes, module))
                    by_name[child.name].add(key)
                    by_symbol[module + '.' + qualified].add(key)
                    if module + '.' + prefix.rstrip('.') in class_symbols:
                        classes[module + '.' + prefix.rstrip('.')].add(key)
                    register(child, qualified + '.', key)
                elif isinstance(child, ast.ClassDef):
                    class_symbols.add(module + '.' + prefix + child.name)
                    register(child, prefix + child.name + '.', parent)
                else:
                    register(child, prefix, parent)
        register(tree)
        key = path + ':<module>'
        functions[key] = dict(key=key, path=path, module=module, qualified='<module>',
            node=tree, nodes=nodes, parameters=[], positional=[], defaults={}, parent=None,
            bindings=modules[module]['bindings'], imports=modules[module]['imports'])
    return dict(functions=functions, modules=modules, by_name=by_name,
                by_symbol=by_symbol, classes=classes, constructors=constructors, sources=sources,
                references={})


def resolve_symbol(expression, function, index):
    if isinstance(expression, ast.Name):
        return function['imports'].get(expression.id,
            index['modules'][function['module']]['imports'].get(expression.id,
                function['module'] + '.' + expression.id))
    if isinstance(expression, ast.Attribute):
        parent = resolve_symbol(expression.value, function, index)
        return parent + '.' + expression.attr if parent else None
    return None


def resolve_references(expression, function, index, seen=frozenset()):
    """Resolve lexical/import aliases, then conservatively match a terminal name."""
    marker = (function['key'], ast.dump(expression))
    if not seen:
        if marker not in index['references']:
            index['references'][marker] = resolve_references(expression, function, index, {('root', '')})
        return index['references'][marker]
    if marker in seen:
        return set()
    seen = seen | {marker}
    symbol = resolve_symbol(expression, function, index)
    if symbol in index['by_symbol']:
        return set(index['by_symbol'][symbol])
    if symbol:
        module, _, name = symbol.rpartition('.')
        if module in index['modules'] and name in index['modules'][module]['bindings']:
            owner = dict(function, module=module, bindings={}, imports={})
            values = index['modules'][module]['bindings'][name]
            return set().union(*(resolve_references(child, owner, index, seen)
                for value in values for child in ast.walk(value)
                if isinstance(value, (ast.Name, ast.Attribute, ast.List, ast.Tuple, ast.Dict, ast.Set))
                and isinstance(child, (ast.Name, ast.Attribute))))
    if isinstance(expression, ast.Name):
        prefix = function['qualified'].split('.')[:-1]
        while prefix:
            key = function['path'] + ':' + '.'.join(prefix + [expression.id])
            if key in index['functions']:
                return {key}
            prefix.pop()
        bindings = function['bindings'].get(expression.id)
        if bindings is None:
            bindings = index['modules'][function['module']]['bindings'].get(expression.id)
        if bindings:
            return set().union(*(resolve_references(child, function, index, seen)
                for value in bindings for child in ast.walk(value)
                if isinstance(value, (ast.Name, ast.Attribute, ast.List, ast.Tuple, ast.Dict, ast.Set))
                and isinstance(child, (ast.Name, ast.Attribute))))
    if symbol:
        # A referenced class may invoke any of its methods through its instances.
        methods = set(index['classes'].get(symbol, ()))
        if methods:
            return methods
    name = getattr(expression, 'id', getattr(expression, 'attr', ''))
    return set(index['by_name'].get(name, ()))


def evaluate_code(expression, function, index, seen=frozenset()):
    """Return literal values, forwarded parameters, and whether a declaration is needed."""
    marker = (function['key'], ast.dump(expression))
    if marker in seen:
        return set(), set(), True
    seen = seen | {marker}
    if isinstance(expression, ast.Constant):
        return ({expression.value} if isinstance(expression.value, str) else set()), set(), False
    if isinstance(expression, ast.Name):
        parameters = {expression.id} if expression.id in function['parameters'] else set()
        bindings = function['bindings'].get(expression.id)
        parent = function.get('parent')
        while bindings is None and not parameters and parent is not None:
            outer = index['functions'][parent]
            if expression.id in outer['parameters'] or expression.id in outer['bindings']:
                return evaluate_code(expression, outer, index, seen)
            parent = outer.get('parent')
        if bindings is None:
            bindings = index['modules'][function['module']]['bindings'].get(expression.id)
        if bindings:
            values, forwarded, computed = set(), parameters, False
            for value in bindings:
                found, passed, dynamic = evaluate_code(value, function, index, seen)
                values.update(found)
                forwarded.update(passed)
                computed = computed or dynamic
            return values, forwarded, computed
        if parameters:
            return set(), parameters, False
    symbol = resolve_symbol(expression, function, index)
    if symbol:
        module, _, name = symbol.rpartition('.')
        if module in index['modules'] and name in index['modules'][module]['bindings']:
            owner = dict(function, module=module, bindings={}, parameters=[], imports={})
            values, parameters, computed = set(), set(), False
            for value in index['modules'][module]['bindings'][name]:
                found, passed, dynamic = evaluate_code(value, owner, index, seen)
                values.update(found)
                parameters.update(passed)
                computed = computed or dynamic
            return values, parameters, computed
    # A value selected or assembled at run time must be reviewed even when some
    # of its literal pieces could be inferred from its syntax.
    forwarded = set()
    if isinstance(expression, ast.BoolOp):
        for value in expression.values:
            _, parameters, _ = evaluate_code(value, function, index, seen)
            forwarded.update(parameters)
    return set(), forwarded, True


def resolve_call_targets(call, function, index):
    """Use actual callable bindings for code arguments; graph edges stay broader."""
    symbol = resolve_symbol(call.func, function, index)
    if symbol in index['classes']:
        return {key for key in index['classes'][symbol] if key.endswith('.__init__')}
    if isinstance(call.func, ast.Name):
        name = call.func.id
        if name in function['parameters']:
            return set()
        lexical = function['qualified'].split('.')[:-1]
        known = any(function['path'] + ':' + '.'.join(lexical[:size] + [name]) in index['functions']
                    for size in range(len(lexical) + 1))
        imported = name in function['imports'] or name in index['modules'][function['module']]['imports']
        if not known and not imported and name not in function['bindings']:
            return set()
    return resolve_references(call.func, function, index)


def resolve_callback_targets(expression, function, index, pointers, seen=frozenset()):
    marker = (function['key'], ast.dump(expression))
    if marker in seen:
        return set()
    seen = seen | {marker}
    if isinstance(expression, ast.Name):
        if expression.id in function['parameters']:
            return set(pointers[function['key']][expression.id])
        if expression.id in function['bindings']:
            return set().union(*(resolve_callback_targets(value, function, index, pointers, seen)
                                 for value in function['bindings'][expression.id]))
    if isinstance(expression, (ast.Name, ast.Attribute)):
        return resolve_call_targets(ast.Call(func=expression, args=[], keywords=[]), function, index)
    return set()


def collect_resolved_calls(functions, index):
    """Propagate known callback arguments before forwarding their code parameters."""
    pointers = {key: defaultdict(set) for key in functions}
    call_nodes = {key: [node for node in function['nodes'] if isinstance(node, ast.Call)]
                  for key, function in functions.items()}
    changed = True
    while changed:
        changed = False
        calls = {}
        for key, function in functions.items():
            calls[key] = []
            for call in call_nodes[key]:
                targets = resolve_call_targets(call, function, index) | resolve_callback_targets(
                    call.func, function, index, pointers)
                calls[key].append((call, targets))
                for target in targets:
                    definition = functions[target]
                    for parameter in definition['parameters']:
                        value = bind_argument(call, definition, parameter)
                        owner = function
                        if value is None:
                            value, owner = definition['defaults'].get(parameter), definition
                        if value is not None:
                            found = resolve_callback_targets(value, owner, index, pointers)
                            previous = len(pointers[target][parameter])
                            pointers[target][parameter].update(found)
                            changed = changed or previous != len(pointers[target][parameter])
    return calls


def bind_argument(call, target, parameter):
    for keyword in call.keywords:
        if keyword.arg == parameter:
            return keyword.value
    positional = target['positional']
    bound = isinstance(call.func, ast.Attribute) or target['qualified'].endswith('.__init__')
    offset = 1 if positional and positional[0] in ('self', 'cls') and bound else 0
    if parameter in positional:
        position = positional.index(parameter) - offset
        if 0 <= position < len(call.args):
            return call.args[position]
    return None


def collect_sinks(function, index):
    sinks = []
    for node in function['nodes']:
        if isinstance(node, ast.Call):
            name = getattr(node.func, 'id', getattr(node.func, 'attr', ''))
            symbol = resolve_symbol(node.func, function, index)
            if name in index['constructors'] or (symbol and symbol.rsplit('.', 1)[-1] in index['constructors']):
                value = node.args[0] if node.args else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == 'code'), None)
                if value is not None:
                    sinks.append(('emitted', value))
            if name == 'obligation' and node.args:
                sinks.append(('domain', node.args[0]))
            if name in ('dict', 'update'):
                kind = 'planned' if function['module'] in PLANNER_MODULES or getattr(function['node'], 'name', '') == 'plan_next' else 'domain'
                sinks.extend((kind, keyword.value) for keyword in node.keywords if keyword.arg == 'code')
        elif isinstance(node, ast.Dict):
            kind = 'planned' if function['module'] in PLANNER_MODULES else 'domain'
            sinks.extend((kind, value) for key, value in zip(node.keys, node.values)
                         if isinstance(key, ast.Constant) and key.value == 'code')
    return sinks


def describe_site(function, expression, index):
    segment = ast.get_source_segment(index['sources'][function['path']], expression)
    return function['key'] + ':' + ' '.join(segment.split())


def collect_planned_values(expression, function, index, seen=frozenset()):
    """Follow finite policy-table selections while preserving error-code forwarding."""
    marker = (function['key'], ast.dump(expression))
    if marker in seen:
        return set()
    seen = seen | {marker}
    values, _, computed = evaluate_code(expression, function, index)
    if not computed:
        return values
    if isinstance(expression, ast.Name):
        bindings = function['bindings'].get(expression.id,
            index['modules'][function['module']]['bindings'].get(expression.id, ()))
        return set().union(*(collect_planned_values(value, function, index, seen) for value in bindings))
    if isinstance(expression, ast.Dict):
        children = expression.values
    elif isinstance(expression, ast.Subscript):
        children = [expression.value]
    elif isinstance(expression, ast.Call) and isinstance(expression.func, ast.Attribute) and expression.func.attr == 'get':
        children = [expression.func.value] + expression.args[1:]
    elif isinstance(expression, ast.IfExp):
        children = [expression.body, expression.orelse]
    elif isinstance(expression, ast.BoolOp):
        children = expression.values
    else:
        children = []
    return values | set().union(*(collect_planned_values(value, function, index, seen) for value in children))


def prove_code_selection(expression, function, index, allow_parameters=True, seen=frozenset()):
    """Recognize finite policy selections or typed exception-code forwarding."""
    marker = (function['key'], ast.dump(expression), allow_parameters)
    if marker in seen:
        return False
    seen = seen | {marker}
    if isinstance(expression, ast.Constant):
        return True
    if isinstance(expression, ast.Name):
        if expression.id in function['parameters']:
            return allow_parameters
        bindings = function['bindings'].get(expression.id,
            index['modules'][function['module']]['bindings'].get(expression.id, ()))
        if bindings:
            return all(prove_code_selection(value, function, index, allow_parameters, seen) for value in bindings)
    symbol = resolve_symbol(expression, function, index)
    if symbol:
        module, _, name = symbol.rpartition('.')
        bindings = index['modules'].get(module, {}).get('bindings', {}).get(name, ())
        if bindings:
            owner = dict(function, module=module, bindings={}, parameters=[], imports={})
            return all(prove_code_selection(value, owner, index, allow_parameters, seen) for value in bindings)
    if isinstance(expression, ast.Attribute) and expression.attr == 'code' and isinstance(expression.value, ast.Name):
        if expression.value.id == 'self' and function['qualified'].split('.')[0] in index['constructors']:
            return True
        for node in function['nodes']:
            if isinstance(node, ast.ExceptHandler) and node.name == expression.value.id and node.type is not None:
                if any(getattr(member, 'id', getattr(member, 'attr', '')) in index['constructors']
                       for member in ast.walk(node.type)):
                    return True
    if isinstance(expression, ast.Dict):
        return all(prove_code_selection(value, function, index, False, seen) for value in expression.values)
    if isinstance(expression, ast.Subscript):
        return prove_code_selection(expression.value, function, index, False, seen)
    if isinstance(expression, ast.Call) and isinstance(expression.func, ast.Attribute) and expression.func.attr == 'get':
        return (prove_code_selection(expression.func.value, function, index, False, seen)
                and all(prove_code_selection(value, function, index, allow_parameters, seen) for value in expression.args[1:]))
    if isinstance(expression, ast.IfExp):
        return all(prove_code_selection(value, function, index, False, seen)
                   for value in (expression.body, expression.orelse))
    if isinstance(expression, ast.BoolOp):
        return all(prove_code_selection(value, function, index, allow_parameters, seen) for value in expression.values)
    return False


def analyze_sources(sources, roots, computed_sites=None):
    """Analyze source strings and explicit public-operation roots without execution."""
    declarations = {} if computed_sites is None else computed_sites
    index = build_index(sources)
    functions = index['functions']
    sinks = {key: collect_sinks(function, index) for key, function in functions.items()}
    calls, graph = collect_resolved_calls(functions, index), {}
    for key, function in functions.items():
        graph[key] = set().union(*(resolve_references(node, function, index)
            for node in function['nodes'] if isinstance(node, (ast.Name, ast.Attribute))))
        graph[key].update(set().union(*(targets for _, targets in calls[key])))
        module_key = function['path'] + ':<module>'
        if key != module_key:
            graph[key].add(module_key)
        for target in function['imports'].values():
            while target and target not in index['modules']:
                target = target.rpartition('.')[0]
            if target:
                graph[key].add(index['modules'][target]['path'] + ':<module>')

    nested_functions = defaultdict(list)
    for key, definition in functions.items():
        if definition.get('parent'):
            nested_functions[definition['parent']].append(key)
    forwarding = {key: {'emitted': set(), 'planned': set(), 'domain': set()} for key in functions}
    changed = True
    while changed:
        changed = False
        for key, function in functions.items():
            candidates = list(sinks[key])
            for nested in nested_functions[key]:
                definition = functions[nested]
                for kind, parameters in forwarding[nested].items():
                    previous = len(forwarding[key][kind])
                    forwarding[key][kind].update(parameters - set(definition['parameters']))
                    changed = changed or previous != len(forwarding[key][kind])
            for call, targets in calls[key]:
                for target in targets:
                    for kind, parameters in forwarding[target].items():
                        candidates.extend((kind, value) for parameter in parameters
                            for value in [bind_argument(call, functions[target], parameter)] if value is not None)
            for kind, value in candidates:
                _, parameters, _ = evaluate_code(value, function, index)
                old = len(forwarding[key][kind])
                forwarding[key][kind].update(parameters)
                changed = changed or len(forwarding[key][kind]) != old

    codes = {key: {'emitted': set(), 'planned': set(), 'domain': set()} for key in functions}
    used, unknown = set(), set()
    deferred = []
    for key, function in functions.items():
        candidates = list(sinks[key])
        for kind, parameters in forwarding[key].items():
            candidates.extend((kind, function['defaults'][parameter]) for parameter in parameters
                              if parameter in function['defaults'])
        for call, targets in calls[key]:
            for target in targets:
                for kind, parameters in forwarding[target].items():
                    candidates.extend((kind, value) for parameter in parameters
                        for value in [bind_argument(call, functions[target], parameter)] if value is not None)
        for kind, expression in candidates:
            values, _, computed = evaluate_code(expression, function, index)
            if computed:
                site = describe_site(function, expression, index)
                if site in declarations:
                    used.add(site)
                    declaration = declarations[site]
                    if isinstance(declaration, dict):
                        values.update(declaration.get('codes', ()))
                        deferred.append((key, kind, site, declaration.get('sources', ())))
                    else:
                        values.update(declaration)
                elif kind in ('planned', 'domain'):
                    # Retained errors inherit the codes emitted by the same
                    # command's reachable functions; policy tables add literals.
                    values.update(collect_planned_values(expression, function, index))
                    if not prove_code_selection(expression, function, index):
                        unknown.add(site)
                else:
                    unknown.add(site)
            codes[key][kind].update(values)
    if unknown:
        raise ValueError('Undeclared computed code site(s):\n' + '\n'.join(sorted(unknown)))
    unused = set(declarations) - used
    if unused:
        raise ValueError('Unused computed code declaration(s):\n' + '\n'.join(sorted(unused)))
    changed = True
    while changed:
        changed = False
        for key, kind, site, entries in deferred:
            missing = set(entries) - set(functions)
            if missing:
                raise ValueError('Unknown computed-code producer for %s: %s' % (site, ', '.join(sorted(missing))))
            seen, pending = set(), list(entries)
            while pending:
                producer = pending.pop()
                if producer not in seen:
                    seen.add(producer)
                    pending.extend(graph[producer] - seen)
            before = len(codes[key][kind])
            codes[key][kind].update(set().union(*(set().union(*codes[producer].values()) for producer in seen)))
            changed = changed or before != len(codes[key][kind])
    emitted = set().union(*(row['emitted'] for row in codes.values()))
    planned = set().union(*(row['planned'] | row['domain'] for row in codes.values()))
    resolved_declarations = {}
    for site in sorted(used):
        declaration = declarations[site]
        if isinstance(declaration, dict):
            values = set(declaration.get('codes', ()))
            entries = declaration.get('sources', ())
            seen, pending = set(), list(entries)
            while pending:
                producer = pending.pop()
                if producer not in seen:
                    seen.add(producer)
                    pending.extend(graph[producer] - seen)
            values.update(set().union(*(set().union(*codes[producer].values()) for producer in seen)))
            resolved_declarations[site] = dict(values=sorted(values), sources=list(entries))
        else:
            resolved_declarations[site] = dict(values=sorted(declaration), sources=[])
    by_command = {}
    for command, entries in sorted(roots.items()):
        missing = set(entries) - set(functions)
        if missing:
            raise ValueError('Unknown operation root for %s: %s' % (command, ', '.join(sorted(missing))))
        seen, pending = set(), list(entries)
        while pending:
            key = pending.pop()
            if key not in seen:
                seen.add(key)
                pending.extend(graph[key] - seen)
        by_command[command] = sorted(set().union(*(set().union(*codes[key].values()) for key in seen)))
    return dict(emitted=sorted(emitted), planned=sorted(planned),
        reachable=sorted(set().union(*(set(values) for values in by_command.values()))),
        by_command=by_command, forwarding={key: sorted(row['emitted']) for key, row in sorted(forwarding.items())
                                         if row['emitted']},
        computed_sites=resolved_declarations,
        source_files=sorted(sources))


def read_sources(root):
    return {path.relative_to(root).as_posix(): path.read_text(encoding='utf-8')
            for folder in SOURCE_FOLDERS for path in sorted((root / folder).rglob('*.py'))
            if 'tests' not in path.relative_to(root / folder).parts}


def discover_planned_commands(sources):
    """Enumerate command fields and finite role/policy selections from plan sources."""
    index = build_index(sources)
    homes = PLANNER_MODULES | {'research_harness.review_staged_protocol',
                               'research_harness.review_parts_repair',
                               'research_harness.review_output_correction'}
    contexts = list(index['functions'].values())
    commands = set()
    for function in contexts:
        nodes = function['nodes']
        if function['module'] not in homes:
            # A helper can supply a complete plan or return a bare command step.
            # Scan these shapes in every source module, rather than silently
            # depending on where the current planner helpers happen to live.
            shapes = []
            for node in nodes:
                value = node.value if isinstance(node, ast.Return) else node
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'dict':
                    fields = {keyword.arg for keyword in value.keywords}
                elif isinstance(value, ast.Dict):
                    fields = {key.value for key in value.keys if isinstance(key, ast.Constant)}
                else:
                    continue
                if 'command' in fields and ('state' in fields or isinstance(node, ast.Return)):
                    shapes.append(value)
            nodes = [child for value in shapes for child in ast.walk(value)]
        for node in nodes:
            values = []
            if isinstance(node, ast.keyword) and node.arg == 'command':
                values.append(node.value)
            elif isinstance(node, ast.Dict):
                values.extend(value for key, value in zip(node.keys, node.values)
                              if isinstance(key, ast.Constant) and key.value == 'command')
            elif isinstance(node, ast.Assign):
                if any(isinstance(target, ast.Name) and target.id in ('command', '_command')
                       for target in node.targets):
                    values.append(node.value)
                elif any(isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                         and target.slice.value == 'command' for target in node.targets):
                    values.append(node.value)
            for value in values:
                commands.update(collect_planned_values(value, function, index))
    return commands


def validate_command_roots(commands, roots, unavailable):
    missing = set(commands) - set(roots) - set(unavailable)
    if missing:
        raise ValueError('Planned commands need public operation roots: ' + ', '.join(sorted(missing)))


def collect_command_roots(sources):
    """Read the real CLI dispatch table instead of maintaining duplicate wrappers."""
    index = build_index(sources)
    module = index['modules']['research_harness.cli']
    owner = dict(key='research_harness/cli.py:<module>', module='research_harness.cli',
                 path='research_harness/cli.py', qualified='', imports={}, bindings={}, parameters=[])
    operations = {}
    tree = ast.parse(sources[module['path']])
    for node in tree.body:
        mapping = None
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'OPERATIONS'
                                                for target in node.targets):
            mapping = node.value
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == 'OPERATIONS' and call.func.attr == 'update' and call.args):
                mapping = call.args[0]
        if isinstance(mapping, ast.Dict):
            for key, value in zip(mapping.keys, mapping.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    operations[key.value] = tuple(sorted(resolve_references(value, owner, index)))
    roots = dict(EXTERNAL_COMMAND_ROOTS)
    planned = discover_planned_commands(sources)
    for command in sorted(set(SERIES_COMMANDS) | planned):
        if command in roots or (command in UNAVAILABLE_COMMANDS and command not in operations):
            continue
        if command not in operations or not operations[command]:
            raise ValueError('No public operation found for planned command ' + command)
        roots[command] = operations[command]
    validate_command_roots(planned, roots, UNAVAILABLE_COMMANDS)
    # The driver indexes OPERATIONS dynamically. Attach the discovered action
    # entries explicitly so a scoped driver recorder covers its dispatched calls.
    action_entries = {entry for command in planned for entry in roots.get(command, ())
                      if command not in ('plan_next', 'run_series', 'review-series')}
    for command in ('run_series', 'review-series'):
        roots[command] = tuple(sorted(set(roots[command]) | action_entries))
    return roots


def analyze_repository(root):
    sources = read_sources(Path(root))
    return analyze_sources(sources, collect_command_roots(sources), COMPUTED_CODE_SITES)


def build_fixture(root):
    report = analyze_repository(root)
    # Read the one real registry only after the static source analysis succeeds.
    from research_harness.stop_classes import STOP_REGISTRY
    report['unreviewed_rows'] = {code: dict(unit='registry-rows')
        for code in report['reachable'] if code not in STOP_REGISTRY}
    report['unavailable_commands'] = {command: reason for command, reason in UNAVAILABLE_COMMANDS.items()
                                      if command not in report['by_command']}
    report['planned_commands'] = sorted(discover_planned_commands(read_sources(Path(root))))
    # Equal conservative domains share one named set, leaving the command and
    # computed-site tables reviewable without repeating thousands of codes.
    names = {tuple(report[field]): field for field in ('emitted', 'planned', 'reachable')}
    additional = {}

    def retain_codes(values):
        values = tuple(values)
        if values not in names:
            name = 'codes_' + str(len(additional) + 1)
            names[values] = name
            additional[name] = list(values)
        return names[values]

    report['by_command'] = {command: retain_codes(values) for command, values in report['by_command'].items()}
    report['computed_sites'] = {site: dict(row, values=retain_codes(row['values']))
                                for site, row in report['computed_sites'].items()}
    report['code_sets'] = additional
    return report


def read_command_codes(fixture, command):
    """Expand a command's named code set in the generated fixture."""
    name = fixture['by_command'][command]
    return fixture[name] if name in fixture else fixture['code_sets'][name]


def validate_registry_coverage(fixture, registry):
    missing = set(fixture['reachable']) - set(registry) - set(fixture['unreviewed_rows'])
    assert not missing, 'Reachable codes need reviewed rows or R55 owners: ' + ', '.join(sorted(missing))
    stale = set(fixture['unreviewed_rows']) & set(registry)
    assert not stale, 'Reviewed codes must leave the R55 table: ' + ', '.join(sorted(stale))


def run_cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--write-fixture', action='store_true')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.root))
    report = build_fixture(args.root)
    if args.write_fixture:
        destination = args.root / 'tests/fixtures/series_code_reach.json'
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    summary = dict(emitted=len(report['emitted']), reachable=len(report['reachable']),
                   unreviewed_rows=len(report['unreviewed_rows']),
                   by_command={command: dict(reachable=len(read_command_codes(report, command)), unreviewed_rows=len(
                       set(read_command_codes(report, command)) & set(report['unreviewed_rows'])))
                       for command in report['by_command']})
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == '__main__':
    run_cli()
