#!/usr/bin/env python3
"""Read-only, conservative error-code analysis for the D2 continuity guard.

This is a function/reference graph, not an import closure. Resolved imports and
lexical names take precedence; unresolved attributes reach methods and functions
of modules used as values. Function references include callbacks and locally imported aliases.
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
# Only calls hidden from the source rules belong here. Each target needs its reason.
DECLARED_EDGES = {}

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
    "research_harness/acquisition.py:import_fulltext:extracted['status']": {
        'sources': ['research_harness/acquisition.py:import_fulltext']},
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
    'recover': ('research_harness/cli.py:run',),
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
    class_bodies = {}
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
                    symbol = module + '.' + prefix + child.name
                    classes[symbol]
                    qualified = prefix + child.name + '.<body>'
                    key = path + ':' + qualified
                    own_nodes = list(walk_scope(child))
                    functions[key] = dict(key=key, path=path, module=module, qualified=qualified,
                        node=child, nodes=own_nodes, parameters=[], positional=[], defaults={}, parent=parent,
                        bindings=collect_bindings(own_nodes), imports=collect_imports(own_nodes, module))
                    class_bodies[symbol] = key
                    register(child, prefix + child.name + '.', parent)
                else:
                    register(child, prefix, parent)
        register(tree)
        key = path + ':<module>'
        functions[key] = dict(key=key, path=path, module=module, qualified='<module>',
            node=tree, nodes=nodes, parameters=[], positional=[], defaults={}, parent=None,
            bindings=modules[module]['bindings'], imports=modules[module]['imports'])
    return dict(functions=functions, modules=modules, by_name=by_name,
                by_symbol=by_symbol, classes=classes, class_bodies=class_bodies, constructors=constructors, sources=sources,
                references={}, code_references={})


def resolve_symbol(expression, function, index):
    if isinstance(expression, ast.Name):
        return function['imports'].get(expression.id,
            index['modules'][function['module']]['imports'].get(expression.id,
                function['module'] + '.' + expression.id))
    if isinstance(expression, ast.Attribute):
        parent = resolve_symbol(expression.value, function, index)
        return parent + '.' + expression.attr if parent else None
    return None


def resolve_code_references(expression, function, index, seen=frozenset()):
    """Resolve lexical/import aliases, then conservatively match a terminal name."""
    marker = (function['key'], ast.dump(expression))
    if not seen:
        if marker not in index['code_references']:
            index['code_references'][marker] = resolve_code_references(expression, function, index, {('root', '')})
        return index['code_references'][marker]
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
            return set().union(*(resolve_code_references(child, owner, index, seen)
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
            return set().union(*(resolve_code_references(child, function, index, seen)
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


def iter_reference_scopes(function, index):
    """Visit lexical functions followed by their module namespace."""
    scope = function
    while scope is not None:
        yield scope
        if scope['key'].endswith(':<module>'):
            return
        scope = index['functions'].get(scope.get('parent'))
    yield index['functions'][function['path'] + ':<module>']


def collect_reference_bindings(function, index):
    """Keep opaque local bindings and expression-local comprehension scopes."""
    names = set(function['parameters'])
    arguments = getattr(function['node'], 'args', None)
    if arguments is not None:
        names.update(argument.arg for argument in (arguments.vararg, arguments.kwarg) if argument is not None)

    def visit(node, shadowed=frozenset()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not function['node']:
            return
        if shadowed:
            index['expression_shadows'][id(node)] = shadowed
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            targets = {child.id for generator in node.generators for child in ast.walk(generator.target)
                       if isinstance(child, ast.Name)}
            inner = shadowed | targets
            for position, generator in enumerate(node.generators):
                visit(generator.iter, shadowed if position == 0 else inner)
                visit(generator.target, inner)
                for condition in generator.ifs:
                    visit(condition, inner)
            for field in ('elt', 'key', 'value'):
                if hasattr(node, field):
                    visit(getattr(node, field), inner)
            return
        if isinstance(node, ast.Lambda):
            for value in node.args.defaults + [value for value in node.args.kw_defaults if value is not None]:
                visit(value, shadowed)
            parameters = {arg.arg for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs}
            parameters.update(arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None)
            visit(node.body, shadowed | parameters)
            return
        if not shadowed and isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        for child in ast.iter_child_nodes(node):
            visit(child, shadowed)
    visit(function['node'])
    function['scope_names'] = names


def resolve_reference_symbol(expression, function, index, seen=frozenset()):
    """Resolve imported receivers through lexical scopes without guessing types."""
    marker = (function['key'], ast.dump(expression))
    if marker in seen:
        return None
    seen = seen | {marker}
    if isinstance(expression, ast.Attribute):
        parent = resolve_reference_symbol(expression.value, function, index, seen)
        return parent + '.' + expression.attr if parent else None
    if isinstance(expression, ast.Name):
        if expression.id in index['expression_shadows'].get(id(expression), ()):
            return None
        for scope in iter_reference_scopes(function, index):
            prefix = '' if scope['key'].endswith(':<module>') else scope['qualified'].removesuffix('.<body>') + '.'
            symbol = scope['module'] + '.' + prefix + expression.id
            if symbol in index['classes'] or symbol in index['by_symbol']:
                return symbol
            if expression.id in scope['imports']:
                return scope['imports'][expression.id]
            if expression.id in scope['bindings']:
                values = scope['bindings'][expression.id]
                if len(values) == 1 and isinstance(values[0], (ast.Name, ast.Attribute)):
                    return resolve_reference_symbol(values[0], scope, index, seen)
                return None
            if expression.id in scope['scope_names']:
                return None
        return None
    return None


def resolve_table_symbol(expression, function, index, seen=frozenset()):
    """Resolve a retained module name, following aliases used for its additions."""
    while isinstance(expression, ast.Subscript):
        expression = expression.value
    if isinstance(expression, ast.Name):
        for scope in iter_reference_scopes(function, index):
            name = expression.id
            if name in scope['imports']:
                symbol = scope['imports'][name]
                break
            if name in scope['bindings']:
                if not scope['key'].endswith(':<module>'):
                    return None
                symbol = scope['module'] + '.' + name
                break
            if name in scope['scope_names']:
                return None
        else:
            return None
    elif isinstance(expression, ast.Attribute):
        symbol = resolve_reference_symbol(expression, function, index)
    else:
        return None
    if not symbol or symbol in seen:
        return None
    module, _, name = symbol.rpartition('.')
    definition = index['modules'].get(module)
    if definition is None or name not in definition['bindings']:
        return None
    values = definition['bindings'][name]
    if len(values) == 1 and isinstance(values[0], (ast.Name, ast.Attribute)):
        owner = index['functions'][definition['path'] + ':<module>']
        target = resolve_table_symbol(values[0], owner, index, seen | {symbol})
        if target:
            return target
    return symbol


def collect_table_additions(index):
    """Retain additions by top-level statements in any source module."""
    additions = defaultdict(list)
    for module, definition in index['modules'].items():
        owner = index['functions'][definition['path'] + ':<module>']
        for node in owner['nodes']:
            target = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = node.func.value
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                assigned = node.targets if isinstance(node, ast.Assign) else [node.target]
                targets = [member.value for member in assigned if isinstance(member, ast.Subscript)]
                for member in targets:
                    symbol = resolve_table_symbol(member, owner, index)
                    if symbol:
                        additions[symbol].append((node, owner))
            elif isinstance(node, ast.AugAssign):
                target = node.target
            if target is not None:
                symbol = resolve_table_symbol(target, owner, index)
                if symbol:
                    additions[symbol].append((node, owner))
    index['table_additions'] = additions


def collect_reference_context(index):
    """Index class families and modules passed as values for the edge rules."""
    bases, children = defaultdict(set), defaultdict(set)
    index['expression_shadows'] = {}
    for function in index['functions'].values():
        collect_reference_bindings(function, index)
    for module, definition in index['modules'].items():
        path = definition['path']
        owner = index['functions'][path + ':<module>']

        def visit(node, prefix='', scope=owner):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    symbol = module + '.' + prefix + child.name
                    for base in child.bases:
                        target = resolve_reference_symbol(base, scope, index)
                        if target in index['classes']:
                            bases[symbol].add(target)
                            children[target].add(symbol)
                    visit(child, prefix + child.name + '.', index['functions'][index['class_bodies'][symbol]])
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, prefix + child.name + '.', index['functions'][path + ':' + prefix + child.name])
                else:
                    visit(child, prefix, scope)
        visit(owner['node'])
    value_modules = set()
    for function in index['functions'].values():
        receivers = {id(node.value) for node in function['nodes'] if isinstance(node, ast.Attribute)}
        for node in function['nodes']:
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and id(node) not in receivers and not function['key'].endswith(':<module>')):
                symbol = resolve_reference_symbol(node, function, index)
                if symbol in index['modules']:
                    value_modules.add(symbol)
    index.update(bases=bases, children=children, value_modules=value_modules)
    collect_table_additions(index)


def collect_closure(entries, graph):
    seen, pending = set(), list(entries)
    while pending:
        key = pending.pop()
        if key not in seen:
            seen.add(key)
            pending.extend(graph.get(key, set()) - seen)
    return seen


def resolve_references(expression, function, index, seen=frozenset()):
    """Resolve reference equations, including cyclic aliases, to a fixed point."""
    marker = (function['key'], id(expression))
    stack = index.setdefault('reference_stack', [])
    parents = index.setdefault('reference_parents', defaultdict(set))
    if stack:
        parents[marker].add(stack[-1])
    if marker in index['references']:
        return index['references'][marker]
    index['references'][marker] = set()
    stack.append(marker)
    try:
        found = resolve_reference_expression(expression, function, index, seen | {marker})
    finally:
        stack.pop()
    index['references'][marker].update(found)
    pending = [marker]
    while pending:
        child = pending.pop()
        for parent in parents[child]:
            values = index['references'][parent]
            added = index['references'][child] - values
            if added:
                values.update(added)
                pending.append(parent)
    return index['references'][marker]


def resolve_reference_expression(expression, function, index, seen):
    """Collect direct targets and dependencies of one lexical reference."""
    # Dynamic dispatch is supplied by EXECUTABLE_COMMANDS, never the whole CLI.
    if (isinstance(expression, ast.Name) and expression.id == 'OPERATIONS'
            and (function['key'].startswith('research_harness/review_series.py:run_series')
                 or function['key'] == 'research_harness/record_check.py:check_continuation')):
        return set()

    def resolve_values(values, owner):
        result = set()
        for value in values:
            # Ordinary call results are not lexical callable aliases. Their
            # callees and arguments already occur in the caller's own graph.
            literal_attribute = (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == 'getattr' and len(value.args) >= 2
                and isinstance(value.args[1], ast.Constant) and isinstance(value.args[1].value, str))
            if not owner['key'].endswith((':<module>', '.<body>')):
                if isinstance(value, ast.Call) and not literal_attribute:
                    continue
                if isinstance(value, ast.Subscript) and not resolve_table_symbol(value.value, owner, index):
                    continue
            for child in ast.walk(value):
                if isinstance(child, (ast.Name, ast.Attribute, ast.Call)):
                    result.update(resolve_references(child, owner, index, seen))
        return result

    def resolve_target(symbol):
        if symbol in index['by_symbol']:
            return set(index['by_symbol'][symbol])
        if symbol in index['classes']:
            family = {symbol} | collect_closure(index['bases'][symbol], index['bases'])
            return {key for member in family for key in index['classes'][member]} | {
                index['class_bodies'][member] for member in family}

        module, _, name = symbol.rpartition('.')
        if module in index['modules']:
            values = index['modules'][module]['bindings'].get(name, ())
            owner = index['functions'][index['modules'][module]['path'] + ':<module>']
            result = resolve_values(values, owner)
            for value, contributor in index['table_additions'].get(symbol, ()):
                result.update(resolve_values([value], contributor))
            return result
        return set()

    def resolve_class_name(symbol, name):
        result = {key for key in index['classes'][symbol] if key.rsplit('.', 1)[-1] == name}
        body = index['functions'][index['class_bodies'][symbol]]
        result.update(resolve_values(body['bindings'].get(name, ()), body))
        if name in body['imports']:
            result.update(resolve_target(body['imports'][name]))
        return result

    result = set()
    if isinstance(expression, ast.Name):
        if expression.id in index['expression_shadows'].get(id(expression), ()):
            return set()
        for scope in iter_reference_scopes(function, index):
            prefix = '' if scope['key'].endswith(':<module>') else scope['qualified'].removesuffix('.<body>') + '.'
            symbol = scope['module'] + '.' + prefix + expression.id
            if symbol in index['by_symbol'] or symbol in index['classes']:
                result = resolve_target(symbol)
                break
            if expression.id in scope['imports']:
                result = resolve_target(scope['imports'][expression.id])
                break
            if expression.id in scope['bindings']:
                result = (resolve_target(scope['module'] + '.' + expression.id)
                          if scope['key'].endswith(':<module>') else resolve_values(scope['bindings'][expression.id], scope))
                break
            if expression.id in scope['scope_names']:
                break
    elif isinstance(expression, ast.Attribute):
        symbol = resolve_reference_symbol(expression, function, index)
        module = symbol.rpartition('.')[0] if symbol else None
        if module in index['modules'] or symbol in index['by_symbol'] or symbol in index['classes']:
            result = resolve_target(symbol)
        else:
            cls = None
            parts = function['qualified'].split('.')
            for size in range(len(parts) - 1, 0, -1):
                candidate = function['module'] + '.' + '.'.join(parts[:size])
                if candidate in index['classes']:
                    cls = candidate
                    break
            receiver = expression.value
            if cls and isinstance(receiver, ast.Name) and receiver.id in ('self', 'cls'):
                family = ({cls} | collect_closure(index['bases'][cls], index['bases'])
                          | collect_closure(index['children'][cls], index['children']))
            elif (cls and isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name)
                  and receiver.func.id == 'super'):
                family = collect_closure(index['bases'][cls], index['bases'])
            else:
                family = None
            if family is not None:
                result = set().union(*(resolve_class_name(symbol, expression.attr) for symbol in family))
            else:
                result = {key for key in index['by_name'].get(expression.attr, ())
                          if key in index['methods'] or (index['functions'][key]['module'] in index['value_modules']
                              and '.' not in index['functions'][key]['qualified'])}
                result.update(set().union(*(resolve_class_name(symbol, expression.attr) for symbol in index['classes'])))
    elif isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name) and expression.func.id in ('getattr', 'setattr'):
        if (expression.func.id == 'getattr' and len(expression.args) >= 2
                and isinstance(expression.args[1], ast.Constant) and isinstance(expression.args[1].value, str)):
            attribute = index.setdefault('literal_attributes', {}).setdefault(id(expression),
                ast.Attribute(value=expression.args[0], attr=expression.args[1].value, ctx=ast.Load()))
            result = resolve_references(attribute, function, index, seen)
        elif (len(expression.args) >= 2 and not (isinstance(expression.args[1], ast.Constant)
                and isinstance(expression.args[1].value, str))):
            module = resolve_reference_symbol(expression.args[0], function, index)
            if module in index['modules']:
                result = {key for key, definition in index['functions'].items()
                          if definition['module'] == module and not key.endswith(':<module>')}
    return result


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
    return resolve_code_references(call.func, function, index)


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


def find_unfollowed_sites(index):
    """List lookup forms whose callable targets the source rules do not follow."""
    sites = {}
    lookups = {'vars', 'globals', 'locals', 'eval', 'exec', '__import__', 'import_module', 'attrgetter', 'methodcaller'}
    for function in index['functions'].values():
        def is_lookup(node):
            if not isinstance(node, ast.Call):
                return False
            name = getattr(node.func, 'id', getattr(node.func, 'attr', ''))
            imported = resolve_symbol(node.func, function, index)
            terminal = imported.rsplit('.', 1)[-1] if imported else name
            if name in lookups or terminal in lookups:
                return True
            if name in ('getattr', 'setattr'):
                literal = (len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                           and isinstance(node.args[1].value, str))
                module = resolve_reference_symbol(node.args[0], function, index) if node.args else None
                return not literal and module not in index['modules']
            return False

        def unresolved_subscript(node):
            if not isinstance(node, ast.Subscript):
                return False
            value = node.value
            while isinstance(value, ast.Subscript):
                value = value.value
            return not isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set, ast.Constant)) and not resolve_table_symbol(value, function, index)

        def is_direct(node):
            if is_lookup(node):
                return True
            if not isinstance(node, ast.Call):
                return False
            if unresolved_subscript(node.func):
                return True
            if isinstance(node.func, ast.Call):
                inner = node.func
                name = getattr(inner.func, 'id', getattr(inner.func, 'attr', ''))
                if name in ('getattr', 'setattr') and not is_lookup(inner):
                    return False
                return resolve_reference_symbol(inner.func, function, index) not in index['classes']
            return False

        def binds_dispatch(node, seen=frozenset()):
            marker = id(node)
            if marker in seen:
                return False
            seen = seen | {marker}
            if unresolved_subscript(node) or is_direct(node):
                return True
            if isinstance(node, ast.Call):
                name = getattr(node.func, 'id', getattr(node.func, 'attr', ''))
                if name in ('getattr', 'setattr') and not is_lookup(node):
                    return False
                return resolve_reference_symbol(node.func, function, index) not in index['classes']
            if isinstance(node, ast.Name) and node.id in function['bindings']:
                return any(binds_dispatch(value, seen) for value in function['bindings'][node.id])
            if isinstance(node, ast.IfExp):
                return binds_dispatch(node.body, seen) or binds_dispatch(node.orelse, seen)
            if isinstance(node, ast.BoolOp):
                return any(binds_dispatch(child, seen) for child in node.values)
            return False

        for node in function['nodes']:
            if isinstance(node, ast.Call) and (is_direct(node) or (isinstance(node.func, ast.Name)
                    and any(binds_dispatch(value) for value in function['bindings'].get(node.func.id, ())))):
                sites[describe_site(function, node, index)] = dict(path=function['path'], function=function['qualified'],
                    expression=' '.join(ast.get_source_segment(index['sources'][function['path']], node).split()))
    return sites


def walk_initial_scope(node):
    """Walk import-time module code, excluding script-only entry branches."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    yield node
    if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
        test = node.test
        if (isinstance(test.left, ast.Name) and test.left.id == '__name__'
                and len(test.ops) == len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value == '__main__'
                and isinstance(test.ops[0], (ast.Eq, ast.NotEq))):
            statements = node.orelse if isinstance(test.ops[0], ast.Eq) else node.body
            for statement in statements:
                yield from walk_initial_scope(statement)
            return
    for child in ast.iter_child_nodes(node):
        yield from walk_initial_scope(child)


def collect_initial_functions(index):
    """Follow import-time calls and names outside retained module definitions."""
    targets = set()
    for module, definition in index['modules'].items():
        owner = index['functions'][definition['path'] + ':<module>']
        for node in walk_initial_scope(owner['node']):
            if isinstance(node, ast.Call):
                targets.update(resolve_references(node.func, owner, index))
                if isinstance(node.func, ast.Name) and node.func.id == 'getattr':
                    targets.update(resolve_references(node, owner, index))
            if not isinstance(node, ast.stmt):
                continue
            assigned = []
            if isinstance(node, ast.Assign):
                assigned = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                assigned = [node.target]
            retains_name = any(isinstance(target, (ast.Name, ast.Tuple, ast.List))
                               or resolve_table_symbol(target, owner, index) for target in assigned)
            adds_table = (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                          and isinstance(node.value.func, ast.Attribute)
                          and resolve_table_symbol(node.value.func.value, owner, index))
            if retains_name or adds_table:
                continue
            pending = list(ast.iter_child_nodes(node))
            while pending:
                child = pending.pop()
                if isinstance(child, ast.stmt):
                    continue
                if isinstance(child, (ast.Name, ast.Attribute)) and isinstance(child.ctx, ast.Load):
                    targets.update(resolve_references(child, owner, index))
                pending.extend(ast.iter_child_nodes(child))
    return targets


def collect_initial_codes(index, calls, forwarding, resolved_sites):
    """Keep only error constructors executed by module initialization in roots."""
    initial = {}
    for definition in index['modules'].values():
        key = definition['path'] + ':<module>'
        function = dict(index['functions'][key])
        function['nodes'] = list(walk_initial_scope(function['node']))
        nodes = {id(node) for node in function['nodes']}
        candidates = [value for kind, value in collect_sinks(function, index) if kind == 'emitted']
        for call, targets in calls[key]:
            if id(call) not in nodes:
                continue
            for target in targets:
                candidates.extend(value for parameter in forwarding[target]['emitted']
                    for value in [bind_argument(call, index['functions'][target], parameter)] if value is not None)
        values = set()
        for expression in candidates:
            found, _, _ = evaluate_code(expression, function, index)
            site = describe_site(function, expression, index)
            if site in resolved_sites:
                found.update(resolved_sites[site]['values'])
            values.update(found)
        initial[key] = values
    return initial


def collect_obligation_codes(functions, calls, index, resolved_sites):
    """Forward code arguments specifically into obligation constructors."""
    sinks = {key: [node.args[0] for node in function['nodes'] if isinstance(node, ast.Call)
                  and getattr(node.func, 'id', getattr(node.func, 'attr', '')) == 'obligation' and node.args]
             for key, function in functions.items()}
    parameters = {key: set() for key in functions}
    nested = defaultdict(list)
    for key, function in functions.items():
        if function.get('parent'):
            nested[function['parent']].append(key)
    changed = True
    while changed:
        changed = False
        for key, function in functions.items():
            candidates = list(sinks[key])
            before = len(parameters[key])
            for child in nested[key]:
                parameters[key].update(parameters[child] - set(functions[child]['parameters']))
            for call, targets in calls[key]:
                candidates.extend(value for target in targets for parameter in parameters[target]
                    for value in [bind_argument(call, functions[target], parameter)] if value is not None)
            for expression in candidates:
                _, forwarded, _ = evaluate_code(expression, function, index)
                parameters[key].update(forwarded)
            changed = changed or len(parameters[key]) != before
    values = {}
    for key, function in functions.items():
        candidates = list(sinks[key]) + [function['defaults'][parameter] for parameter in parameters[key]
                                         if parameter in function['defaults']]
        for call, targets in calls[key]:
            candidates.extend(value for target in targets for parameter in parameters[target]
                for value in [bind_argument(call, functions[target], parameter)] if value is not None)
        values[key] = set()
        for expression in candidates:
            found, _, _ = evaluate_code(expression, function, index)
            site = describe_site(function, expression, index)
            if site in resolved_sites:
                found.update(resolved_sites[site]['values'])
            values[key].update(found)
    return values


def analyze_sources(sources, roots, computed_sites=None, declared_edges=None, series_commands=None):
    """Analyze source strings and explicit public-operation roots without execution."""
    declarations = {} if computed_sites is None else computed_sites
    index = build_index(sources)
    functions = index['functions']
    sinks = {key: collect_sinks(function, index) for key, function in functions.items()}
    calls = collect_resolved_calls(functions, index)
    collect_reference_context(index)
    index['methods'] = set().union(*index['classes'].values())
    graph = {key: set().union(*(resolve_references(node, function, index)
        for node in function['nodes'] if isinstance(node, ast.Call) or (
            isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load))))
        for key, function in functions.items()}
    unfollowed = find_unfollowed_sites(index)
    initial = collect_initial_functions(index)
    for caller, targets in (declared_edges or {}).items():
        if caller not in functions:
            raise ValueError('Unknown declared edge caller: ' + caller)
        for target, reason in targets.items():
            if target not in functions or not isinstance(reason, str) or not reason.strip():
                raise ValueError('Declared edge needs an existing target and a reason: ' + caller + ' -> ' + target)
            if target in graph[caller]:
                raise ValueError('Unused declared edge: ' + caller + ' -> ' + target)
            graph[caller].add(target)

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
    direct_codes = {key: {kind: set(values) for kind, values in row.items()} for key, row in codes.items()}
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
    initial_codes = collect_initial_codes(index, calls, forwarding, resolved_declarations)
    module_codes = set().union(*initial_codes.values())
    by_command = {}
    by_command_functions = {}
    for command, entries in sorted(roots.items()):
        missing = set(entries) - set(functions)
        if missing:
            raise ValueError('Unknown operation root for %s: %s' % (command, ', '.join(sorted(missing))))
        seen = collect_closure(set(entries) | initial, graph)
        by_command_functions[command] = seen
        by_command[command] = sorted(module_codes | set().union(*(set().union(*codes[key].values()) for key in seen)))
    if series_commands is None:
        reachable = set().union(*(set(values) for values in by_command.values()))
    else:
        series_roots = ('plan_next', 'run_series') + tuple(series_commands)
        reachable = set().union(*(set(by_command[command]) for command in series_roots))
        for command in ('run_series', 'review-series'):
            by_command[command] = sorted(reachable)
            by_command_functions[command] = set().union(*(by_command_functions[name] for name in series_roots))
    series_functions = set().union(*(by_command_functions[command] for command in (
        ('plan_next', 'run_series') + tuple(series_commands) if series_commands is not None else roots)))
    sites = defaultdict(set)
    for key in series_functions:
        for code in set().union(*codes[key].values()):
            sites[code].add(key)
    for key, values in initial_codes.items():
        for code in values:
            sites[code].add(key)
    status_key = 'research_harness/cli.py:status_report'
    status_functions = collect_closure({status_key} | initial, graph) if status_key in functions else set()
    obligation_codes = collect_obligation_codes(functions, calls, index, resolved_declarations)
    status_obligations = set().union(*(obligation_codes[key] for key in status_functions))
    return dict(emitted=sorted(emitted), planned=sorted(planned),
        reachable=sorted(reachable),
        code_sites={code: sorted(keys) for code, keys in sorted(sites.items())},
        by_command_functions={command: sorted(keys) for command, keys in sorted(by_command_functions.items())},
        initial_functions=sorted(initial), status_obligations=sorted(status_obligations),
        unfollowed_sites=[unfollowed[site] for site in sorted(unfollowed)],
        graph=graph, codes_by_function=codes, direct_codes=direct_codes,
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


def read_executable_commands(sources):
    """Read the driver's single command vocabulary without importing runtime code."""
    tree = ast.parse(sources['research_harness/review_series.py'])
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id == 'EXECUTABLE_COMMANDS' for target in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'frozenset':
                value = value.args[0] if value.args else ast.Tuple(elts=[], ctx=ast.Load())
            return frozenset(ast.literal_eval(value))
    raise ValueError('Driver source must declare EXECUTABLE_COMMANDS')


def read_table_entries(symbol, index, seen=frozenset()):
    """Read finite string keys from a defining table and its source additions."""
    if symbol in seen:
        return {}
    seen = seen | {symbol}
    module, _, name = symbol.rpartition('.')
    definition = index['modules'][module]
    owner = index['functions'][definition['path'] + ':<module>']
    entries = {}

    def read_mapping(value, scope):
        if isinstance(value, ast.Dict):
            for key, member in zip(value.keys, value.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    entries[key.value] = tuple(sorted(resolve_references(member, scope, index)))
                elif key is None:
                    read_mapping(member, scope)
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'dict':
            for argument in value.args:
                read_mapping(argument, scope)
            for keyword in value.keywords:
                if keyword.arg is None:
                    read_mapping(keyword.value, scope)
                else:
                    entries[keyword.arg] = tuple(sorted(resolve_references(keyword.value, scope, index)))
        elif isinstance(value, (ast.Name, ast.Attribute)):
            target = resolve_table_symbol(value, scope, index)
            if target:
                entries.update(read_table_entries(target, index, seen))

    for value in definition['bindings'].get(name, ()):
        read_mapping(value, owner)
    for node, contributor in index['table_additions'].get(symbol, ()):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            assigned = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in assigned:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                    entries[target.slice.value] = tuple(sorted(resolve_references(node.value, contributor, index)))
        elif isinstance(node, ast.AugAssign):
            read_mapping(node.value, contributor)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'update':
            for argument in node.args:
                read_mapping(argument, contributor)
            for keyword in node.keywords:
                if keyword.arg is None:
                    read_mapping(keyword.value, contributor)
                else:
                    entries[keyword.arg] = tuple(sorted(resolve_references(keyword.value, contributor, index)))
    return entries


def collect_command_roots(sources):
    """Read the real CLI dispatch table instead of maintaining duplicate wrappers."""
    index = build_index(sources)
    collect_reference_context(index)
    index['methods'] = set().union(*index['classes'].values())
    operations = read_table_entries('research_harness.cli.OPERATIONS', index)
    roots = dict(EXTERNAL_COMMAND_ROOTS)
    planned = discover_planned_commands(sources)
    executable = read_executable_commands(sources)
    for command in sorted(set(SERIES_COMMANDS) | planned | executable):
        if command in roots or (command in UNAVAILABLE_COMMANDS and command not in operations):
            continue
        if command not in operations or not operations[command]:
            raise ValueError('No public operation found for planned command ' + command)
        roots[command] = operations[command]
    validate_command_roots(planned, roots, UNAVAILABLE_COMMANDS)
    if 'record-check' in roots:
        local = executable - {'review-run'}
        roots['record-check'] = tuple(sorted(set(roots['record-check']) | {
            entry for command in local for entry in operations[command]}))
    return roots


def analyze_repository(root):
    sources = read_sources(Path(root))
    return analyze_sources(sources, collect_command_roots(sources), COMPUTED_CODE_SITES,
                           DECLARED_EDGES, read_executable_commands(sources))


def build_unreviewed_rows(report, registry):
    """Keep every reachable code pending until its own or grouped row exists."""
    return {code: dict(unit='registry-rows', sites=report['code_sites'].get(code, []),
                       status_obligation=code in report['status_obligations'])
            for code in report['reachable'] if code not in registry}


def build_fixture(root):
    report = analyze_repository(root)
    # Read the one real registry only after the static source analysis succeeds.
    from research_harness.stop_classes import STOP_REGISTRY, STOP_GROUPS
    from ci.series_code_groups import read_group_entries
    report['groups'] = read_group_entries(root, report, STOP_REGISTRY, STOP_GROUPS)
    report['unreviewed_rows'] = build_unreviewed_rows(report, STOP_REGISTRY)
    validate_registry_coverage(report, STOP_REGISTRY)
    for field in ('graph', 'codes_by_function', 'direct_codes', 'code_sites',
                  'by_command_functions', 'status_obligations', 'initial_functions'):
        del report[field]
    report['unavailable_commands'] = {command: reason for command, reason in UNAVAILABLE_COMMANDS.items()
                                      if command not in report['by_command']}
    report['planned_commands'] = sorted(discover_planned_commands(read_sources(Path(root))))
    # Share identical sets. Additional sets use indices into one vocabulary so
    # distinct command domains do not repeat long code strings in the fixture.
    names = {tuple(report[field]): field for field in ('emitted', 'planned', 'reachable')}
    additional = {}
    vocabulary = sorted(set(report['emitted']) | set(report['planned']) | set(report['reachable']))
    positions = {code: position for position, code in enumerate(vocabulary)}

    def retain_codes(values):
        values = tuple(values)
        if values not in names:
            name = 'codes_' + str(len(additional) + 1)
            names[values] = name
            additional[name] = [positions[code] for code in values]
        return names[values]

    site_vocabulary = sorted({site for row in report['unreviewed_rows'].values() for site in row['sites']})
    site_positions = {site: position for position, site in enumerate(site_vocabulary)}
    for row in report['unreviewed_rows'].values():
        row['sites'] = [site_positions[site] for site in row['sites']]
    report['site_vocabulary'] = site_vocabulary
    report['by_command'] = {command: retain_codes(values) for command, values in report['by_command'].items()}
    report['computed_sites'] = {site: dict(row, values=retain_codes(row['values']))
                                for site, row in report['computed_sites'].items()}
    report['code_sets'] = additional
    report['code_vocabulary'] = vocabulary
    return report


def read_command_codes(fixture, command):
    """Expand a command's named code set in the generated fixture."""
    name = fixture['by_command'][command]
    return read_code_set(fixture, name)


def read_code_set(fixture, name):
    """Expand one named fixture set, whose indices share a single vocabulary."""
    if name in fixture:
        return fixture[name]
    return [fixture['code_vocabulary'][position] for position in fixture['code_sets'][name]]


def read_code_sites(fixture, code):
    """Expand a pending code's source sites from the shared fixture vocabulary."""
    return [fixture['site_vocabulary'][position] for position in fixture['unreviewed_rows'][code]['sites']]


def validate_registry_coverage(fixture, registry):
    reachable, pending = set(fixture['reachable']), set(fixture['unreviewed_rows'])
    missing = reachable - set(registry) - pending
    assert not missing, 'Reachable codes need rows or pending entries: ' + ', '.join(sorted(missing))
    stale = pending - reachable
    assert not stale, 'Pending entries outside reachable: ' + ', '.join(sorted(stale))
    reviewed = pending & set(registry)
    assert not reviewed, 'Reviewed codes must leave unreviewed_rows: ' + ', '.join(sorted(reviewed))


def format_fixture(report):
    """Keep code lists compact while leaving the tables readable for review."""
    def render(value, level=0):
        if not isinstance(value, dict):
            return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        if not value:
            return '{}'
        prefix = '  ' * (level + 1)
        return '{\n' + ',\n'.join(prefix + json.dumps(key) + ': ' + render(value[key], level + 1)
                                     for key in sorted(value)) + '\n' + '  ' * level + '}'
    return render(report) + '\n'


def run_cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--write-fixture', action='store_true')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.root))
    report = build_fixture(args.root)
    if args.write_fixture:
        destination = args.root / 'tests/fixtures/series_code_reach.json'
        destination.write_text(format_fixture(report), encoding='utf-8')
    summary = dict(emitted=len(report['emitted']), reachable=len(report['reachable']),
                   unreviewed_rows=len(report['unreviewed_rows']),
                   by_command={command: dict(reachable=len(read_command_codes(report, command)), unreviewed_rows=len(
                       set(read_command_codes(report, command)) & set(report['unreviewed_rows'])))
                       for command in report['by_command']})
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == '__main__':
    run_cli()
