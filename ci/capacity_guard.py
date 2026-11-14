"""Read-only closure assertions shared by tests and the release profiler.

The fixture records obligations, not an inference that installed code is evidence.
Source inventories are reviewed declarations; changing a site requires reviewing
its declaration, even when no fixture executes the changed branch.
"""

import ast
import hashlib
import io
import json
from pathlib import Path
import unittest

from ci.source_inventory import describe_source_ast

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / 'tests/fixtures/capacity_closure.json'
TRANSPORT_INVENTORY_PATH = ROOT / 'tests/fixtures/capacity_transport_inventory.json'
NO_CONSTRUCTION_MESSAGE = 'An entry without a planned construction cannot claim constructed coverage.'
EVIDENCE_MESSAGE = 'Every coverage and stop test must exist and pass in this guard run.'
STOP_EVIDENCE_MESSAGE = 'Every required stop keeps its executed evidence, whatever the coverage.'
MATRIX_MESSAGE = 'Every matrix entry needs its units, coverage, quantity, kinds, release and tests.'
CALL_SITE_MESSAGE = 'Every reviewer call must use a declared budgeted operation and call path.'
LIMIT_READER_MESSAGE = 'A route limit has a reader outside its reviewed homes.'
TRANSPORT_MESSAGE = 'Every adapter limit, transport signal and runtime member needs a classified row.'
PRIOR_KEY_MESSAGE = 'Every observed capacity prior needs its complete execution key, including runtime and configuration.'
JOIN_GROUND_MESSAGE = 'Capacity evidence joining keys needs a documented ground and its URL.'
MERGE_MESSAGE = 'An installed unit must add the matrix rows, stops and executed tests of its merge obligation.'
PACKET_KIND_MESSAGE = 'A packet with undeclared members has no capacity task kind.'


def read_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding='utf-8'))


def read_transport_inventory():
    return json.loads(TRANSPORT_INVENTORY_PATH.read_text(encoding='utf-8'))


def describe_coverage(entry, installed_units):
    """Installation never supplies missing acceptance evidence."""
    return entry['coverage']


def validate_entry(entry, outcomes=None, expected_stops=None, report_ids=()):
    errors = []
    units = entry.get('units', {})
    if (not all(entry.get(key) for key in ('id', 'quantity', 'kinds', 'release', 'tests'))
            or not (units.get('path') or units.get('construction'))
            or entry.get('coverage') not in ('constructed', 'recovery', 'stop_before_call')):
        errors.append(MATRIX_MESSAGE)
    evidence = entry.get('evidence', {})
    if not units.get('construction') and not entry.get('no_construction_ruling'):
        errors.append(MATRIX_MESSAGE)
    if entry.get('coverage') == 'constructed' and not units.get('construction'):
        errors.append(NO_CONSTRUCTION_MESSAGE)
    if entry.get('coverage') in ('constructed', 'recovery') and (
            not evidence.get('tests') or entry.get('verification_required') and not evidence.get('reports')):
        errors.append(EVIDENCE_MESSAGE)
    if not set(evidence.get('reports', [])).issubset(report_ids):
        errors.append(EVIDENCE_MESSAGE)
    stops = entry.get('required_stops', [])
    if expected_stops is not None and not set(expected_stops).issubset(row.get('code') for row in stops):
        errors.append(STOP_EVIDENCE_MESSAGE)
    if any(not row.get('code') or not row.get('tests') for row in stops):
        errors.append(STOP_EVIDENCE_MESSAGE)
    identifiers = set(entry.get('tests', [])) | set(evidence.get('tests', []))
    identifiers.update(test for row in stops for test in row.get('tests', []))
    if outcomes is not None and any(outcomes.get(test) != 'passed' for test in identifiers):
        errors.append(EVIDENCE_MESSAGE)
    return errors


def validate_matrix(matrix, outcomes=None):
    errors = []
    seen = set()
    for entry in matrix['entries']:
        if entry['id'] in seen:
            errors.append(MATRIX_MESSAGE + ' ' + entry['id'])
        seen.add(entry['id'])
        errors.extend(entry['id'] + ': ' + error for error in validate_entry(entry, outcomes, matrix.get('stop_contracts', {}).get(entry['id'], []), matrix.get('verification_reports', {})))
        for pending in entry.get('pending_stops', []):
            if (set(pending.get('trigger_units', [pending['unit']])) & set(matrix['installed_units'])
                    or entry['coverage'] == 'constructed'):
                stops = {row['code']: row for row in entry['required_stops']}
                if pending['code'] not in stops or not set(pending['tests']).issubset(stops[pending['code']]['tests']):
                    errors.append(entry['id'] + ': ' + STOP_EVIDENCE_MESSAGE)
    for obligation in matrix['merge_obligations']:
        if obligation['unit'] not in matrix['installed_units']:
            continue
        entries = [entry for entry in matrix['entries'] if entry['id'] in obligation['entries']]
        for entry in entries:
            required = obligation['per_entry'][entry['id']]
            tests = set(entry['tests']) | set(entry['evidence']['tests'])
            codes = {row['code'] for row in entry['required_stops']}
            if not set(required['tests']).issubset(tests) or not set(required['stops']).issubset(codes):
                errors.append(MERGE_MESSAGE + ' ' + obligation['unit'] + ':' + entry['id'])

    return errors


def build_gap_table(matrix):
    return dict(rows=[dict(id=entry['id'], kinds=entry['kinds'], quantity=entry['quantity'],
            pending_units=sorted((set(entry['units']['path']) | set(entry['units']['construction'])) -
                set(matrix['installed_units']))) for entry in matrix['entries']
            if entry['coverage'] == 'stop_before_call'],
        gaps=sorted(entry['id'] for entry in matrix['entries'] if entry['coverage'] == 'stop_before_call'),
        without_construction=sorted(entry['id'] for entry in matrix['entries']
            if not entry['units']['construction']))


def collect_evidence_tests(matrix):
    return sorted({test for entry in matrix['entries'] for test in entry['tests'] +
        entry['evidence']['tests'] + [test for stop in entry['required_stops'] for test in stop['tests']]})


class EvidenceResult(unittest.TextTestResult):
    """Record actual execution, including skipped tests and failing subtests."""
    def __init__(self, *args):
        super().__init__(*args)
        self.outcomes = {}

    def startTest(self, test):
        super().startTest(test)
        self.outcomes[test.id()] = 'failed'

    def addSuccess(self, test):
        super().addSuccess(test)
        if self.outcomes.get(test.id()) != 'skipped':
            self.outcomes[test.id()] = 'passed'

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        parent = getattr(test, 'test_case', test)
        self.outcomes[parent.id()] = 'skipped'

    def addExpectedFailure(self, test, error):
        super().addExpectedFailure(test, error)
        self.outcomes[test.id()] = 'failed'

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            self.outcomes[test.id()] = 'failed'


def run_evidence_tests(identifiers):
    """Run trusted repository test IDs; a missing method is a failed suite row."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(identifier) for identifier in identifiers)
    result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=EvidenceResult).run(suite)
    return {identifier: result.outcomes.get(identifier, 'missing') for identifier in identifiers}


def read_plugin_sources(root):
    sources = {}
    for directory in (root / 'research_harness', root / 'skills/math-solver/harness'):
        for path in directory.rglob('*.py'):
            if 'tests' not in path.relative_to(directory).parts:
                sources[path.relative_to(root).as_posix()] = path.read_text(encoding='utf-8')
    return sources


def describe_ast(node):
    return describe_source_ast(node)


def collect_call_sites(sources):
    sites = []
    sinks = ('send_request', 'observe', '_observed')
    for path, source in sources.items():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in sinks:
                        sites.append(path + ':import:' + describe_ast(node))
            if isinstance(node, ast.Attribute) and node.attr in sinks:
                sites.append(path + ':reference:' + describe_ast(node))
            if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id in sinks or
                    isinstance(node.func, ast.Name) and node.func.id == 'getattr' and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value in sinks):
                sites.append(path + ':call:' + describe_ast(node))
    return sorted(sites)


def collect_operation_sites(sources):
    return sorted(path + ':' + describe_ast(node) for path, source in sources.items()
        for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == '_invoke_once')
             or (isinstance(node.func, ast.Attribute) and node.func.attr == '_invoke_once')))


def inspect_call_sources(sources, inventory, operations):
    errors = []
    if (collect_call_sites(sources) != inventory['call_sites']
            or collect_operation_sites(sources) != inventory['operation_sites']
            or sorted(operations) != inventory['call_operations']):
        errors.append(CALL_SITE_MESSAGE)
    native = {path: hashlib.sha256(describe_ast(ast.parse(source)).encode()).hexdigest()
        for path, source in sources.items() if path.startswith('skills/math-solver/harness/')}
    if native != inventory['native_packet_producers']:
        errors.append(CALL_SITE_MESSAGE)
    for site, checksum in inventory['admission_functions'].items():
        path, function = site.split(':')
        node = next((node for node in ast.walk(ast.parse(sources.get(path, '')))
            if isinstance(node, ast.FunctionDef) and node.name == function), None)
        if node is None or hashlib.sha256(describe_ast(node).encode()).hexdigest() != checksum:
            errors.append(CALL_SITE_MESSAGE + ' ' + site)
    return errors


def collect_limit_readers(sources):
    names = {'INPUT_LIMIT_CHARACTERS', 'CODEX_INPUT_LIMIT_CHARS', 'MAX_USER_TEXT', 'local_input_limit'}
    return sorted(path for path, source in sources.items() if any(
        isinstance(node, ast.Name) and node.id in names or
        isinstance(node, ast.Attribute) and node.attr in names or
        isinstance(node, ast.Constant) and node.value == 'max_output_tokens'
        for node in ast.walk(ast.parse(source))))


def inspect_limit_readers(sources, inventory):
    return [] if collect_limit_readers(sources) == inventory['limit_readers'] else [LIMIT_READER_MESSAGE]


def _read_prefix(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        prefix = _read_prefix(node.left)
        return prefix + '*' if prefix and not prefix.endswith('*') else prefix
    if isinstance(node, ast.JoinedStr) and node.values:
        return (_read_prefix(node.values[0]) or '') + '*'
    return None


def _derive_issue_values(node, functions, bindings, seen=()):
    prefix = _read_prefix(node)
    if prefix:
        return {prefix}
    if isinstance(node, ast.Name) and node.id not in seen:
        values = set()
        for value in bindings.get(node.id, []):
            values.update(_derive_issue_values(value, functions, bindings, seen + (node.id,)))
        return values
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions and node.func.id not in seen:
        values = set()
        for statement in ast.walk(functions[node.func.id]):
            if isinstance(statement, ast.Return) and statement.value is not None:
                values.update(_derive_issue_values(statement.value, functions, bindings, seen + (node.func.id,)))
        return values
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return {value for item in node.elts for value in _derive_issue_values(item, functions, bindings, seen)}
    return set()


def collect_transport_facts(source):
    tree = ast.parse(source)
    codes, issues, runtime = set(), set(), set()
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    bindings = {}
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(statement.value)
        if isinstance(statement, ast.For) and isinstance(statement.target, ast.Tuple) and isinstance(statement.iter, (ast.Tuple, ast.List)):
            for row in statement.iter.elts:
                if isinstance(row, (ast.Tuple, ast.List)):
                    for target, value in zip(statement.target.elts, row.elts):
                        if isinstance(target, ast.Name):
                            bindings.setdefault(target.id, []).append(value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == 'ResearchError' and node.args:
                codes.add(_read_prefix(node.args[0]) or '<computed>')
            if isinstance(node.func, ast.Attribute) and node.func.attr in ('append', 'extend') and (
                    isinstance(node.func.value, ast.Name) and node.func.value.id == 'issues'):
                if node.args:
                    issues.update(_derive_issue_values(node.args[0], functions, bindings) or {'<computed>'})
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or function.name != 'runtime':
            continue
        returns = [node.value for node in ast.walk(function) if isinstance(node, ast.Return)]
        names = {node.id for node in returns if isinstance(node, ast.Name)}
        assignments = [node for node in ast.walk(function) if isinstance(node, ast.Assign)]
        previous = set()
        while names != previous:
            previous = set(names)
            for node in assignments:
                if any(isinstance(target, ast.Name) and target.id in names for target in node.targets):
                    if isinstance(node.value, ast.Name):
                        names.add(node.value.id)
        values = returns + [node.value for node in assignments
            if any(isinstance(target, ast.Name) and target.id in names for target in node.targets)]
        for value in values:
            if isinstance(value, ast.Dict):
                runtime.update(key.value for key in value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str))
            elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'dict':
                runtime.update(item.arg for item in value.keywords if item.arg is not None)
        for node in ast.walk(function):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'update' and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in names):
                runtime.update(item.arg for item in node.keywords if item.arg is not None)
            elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                    and isinstance(node.value, ast.Name) and node.value.id in names
                    and isinstance(node.slice, ast.Constant)):
                runtime.add(node.slice.value)
    return dict(codes=sorted(codes), issues=sorted(issues), runtime=sorted(runtime))


def collect_binary_rules(adapter):
    from research_harness import review_route_capacity as capacity
    if adapter not in ('openai_responses_v1', 'codex_cli_visual_v1'):
        return []
    models = [row[0] for row in capacity.MODEL_LIMITS if row[0].startswith('gpt-')] + ['unlisted-model']
    endpoints = ('https://api.openai.com/v1/responses', 'https://capacity.invalid/v1/responses') if adapter == 'openai_responses_v1' else ('codex://local',)
    return [dict(endpoint=endpoint, model=model, rules=capacity.describe_binary_rules(
        dict(adapter=adapter, endpoint=endpoint, model=model))) for endpoint in endpoints for model in models]


def inspect_transports(sources, inventory, adapters):
    rows = inventory['adapters']
    errors = inspect_prior_sources(sources, inventory)
    if set(rows) != set(adapters):
        errors.append(TRANSPORT_MESSAGE)
    for adapter, row in rows.items():
        source = sources.get(row['module'], '')
        if row.get('binary_rules', []) != collect_binary_rules(adapter):
            errors.append(TRANSPORT_MESSAGE + ' ' + adapter)
        if hashlib.sha256(describe_ast(ast.parse(source)).encode()).hexdigest() != row['source_ast_sha256']:
            errors.append(TRANSPORT_MESSAGE + ' ' + adapter)
        facts = collect_transport_facts(source)
        if row.get('runtime_inherits'):
            inherited = inventory['adapters'][row['runtime_inherits']]['module']
            facts['runtime'] = sorted(set(facts['runtime']) | set(collect_transport_facts(sources.get(inherited, ''))['runtime']))
        config = set(collect_configuration_members(sources.get(row['module'], '')))
        if row.get('runtime_inherits'):
            config.update(collect_configuration_members(sources.get(inherited, '')))
        if config != set(row['configuration']):
            errors.append(TRANSPORT_MESSAGE + ' ' + adapter)
        signals = row['signals']
        if (set(facts['codes']) != set(signals['codes']) or set(facts['issues']) != set(signals['issues'])
                or set(facts['runtime']) != set(row['runtime'])
                or set(row['limits']) != {'input_characters', 'input_tokens', 'output_tokens', 'time',
                    'response_bytes', 'binary_input', 'binary_request'}):
            errors.append(TRANSPORT_MESSAGE + ' ' + adapter)
        for group in signals.values():
            for signal in group.values():
                if not {'limit', 'outcome', 'detail'}.issubset(signal):
                    errors.append(TRANSPORT_MESSAGE + ' ' + adapter)
        for member in list(row['runtime'].values()) + list(row['configuration'].values()):
            if member.get('class') not in ('key', 'reservation', 'no_effect') or not member.get('reason'):
                errors.append(TRANSPORT_MESSAGE + ' ' + adapter)
        for limit in row['limits'].values():
            if not {'source', 'entry', 'signal'}.issubset(limit):
                errors.append(TRANSPORT_MESSAGE + ' ' + adapter)
    return errors


def validate_capacity_evidence(rows):
    from research_harness.errors import ResearchError
    from research_harness.review_capacity_evidence import validate_capacity_inventory
    priors = [dict(row, id=row.get('id', 'prior:' + str(index))) for index, row in enumerate(rows)]
    try:
        validate_capacity_inventory((), priors=priors)
    except ResearchError as error:
        return [JOIN_GROUND_MESSAGE if row['reason'] == 'prior_join_undocumented' else PRIOR_KEY_MESSAGE
                for row in error.details['failures']]
    return []


def inspect_merge_obligations(matrix, sources):
    """A pending row becomes mandatory as soon as its production marker exists."""
    detected = set(matrix['installed_units'])
    for row in matrix['merge_obligations']:
        marker = row['presence']
        tree = ast.parse(sources.get(marker['module'], ''))
        nodes = ast.walk(tree)
        if marker['kind'] == 'function':
            present = any(isinstance(node, ast.FunctionDef) and node.name == marker['name'] for node in nodes)
        else:
            present = any(isinstance(node, ast.Constant) and node.value == marker['name'] for node in nodes)
        if present:
            detected.add(row['unit'])
    copy = dict(matrix, installed_units=sorted(detected))
    return validate_matrix(copy)


def inspect_task_tables(inventory, roles, kinds):
    """Pin all currently returned kinds and each role's declared delivery gap."""
    errors = []
    if set(inventory['roles']) != set(roles) or set(inventory['task_rules']) != set(kinds):
        errors.append(TRANSPORT_MESSAGE)
    for row in inventory['task_rules'].values():
        if not all(row.get(key) for key in ('members', 'levels', 'answer_bound', 'protocols', 'entries')):
            errors.append(TRANSPORT_MESSAGE)
    return errors


def collect_configuration_members(source):
    """Collect route configuration reads, including validation's closed-key sets."""
    members = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            base = node.value
            if ((isinstance(base, ast.Name) and base.id == 'config') or
                    isinstance(base, ast.Subscript) and isinstance(base.slice, ast.Constant)
                    and base.slice.value == 'configuration'):
                members.add(node.slice.value)
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Call):
            call = node.left
            if isinstance(call.func, ast.Name) and call.func.id == 'set' and call.args and (
                    isinstance(call.args[0], ast.Name) and call.args[0].id == 'config'):
                for comparator in node.comparators:
                    if isinstance(comparator, (ast.Set, ast.Tuple, ast.List)):
                        members.update(item.value for item in comparator.elts if isinstance(item, ast.Constant))
    return sorted(members)


def describe_packet_task(role, packet, *, route, request, registry, operation='review.invoke'):
    """K2 assigns no kind before both logical and emitted packets have closed schemas."""
    from ci.capacity_packet_inventory import validate_packet
    from research_harness.review_excerpts import read_sent_packet
    from research_harness.review_route_capacity import describe_task
    actual = read_sent_packet(request)
    if (validate_packet(role, packet, registry) or
            actual is not None and validate_packet(role, actual, registry)):
        raise ValueError(PACKET_KIND_MESSAGE)
    return describe_task(role, packet, route=route, request=request, operation=operation)

DECLARATION_ENTRY_MESSAGE = 'Every task, limit and growing packet declaration must name an existing closure entry.'


def inspect_declaration_entries(matrix, transport_inventory, packet_registry):
    from ci.capacity_packet_inventory import _walk_declarations
    known = {row['id'] for row in matrix['entries']}
    references = set()
    for adapter in transport_inventory['adapters'].values():
        references.update(row['entry'] for row in adapter['limits'].values())
    for rule in transport_inventory['task_rules'].values():
        references.update(rule['entries'])
    for role in packet_registry['roles'].values():
        for schema in role['protocols'].values():
            references.update(member['entry'] for _, member in _walk_declarations(schema, packet_registry, ()))
    return [] if references <= known else [DECLARATION_ENTRY_MESSAGE]


def inspect_prior_sources(sources, inventory, installed_units=()):
    """Check actual observed-prior declarations; only the pinned pending base is deferred."""
    errors = []
    for declaration in inventory['capacity_prior_constants']:
        matches = [node.value for node in ast.walk(ast.parse(sources.get(declaration['module'], '')))
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
                target.id == declaration['name'] for target in node.targets)]
        if len(matches) != 1:
            errors.append(PRIOR_KEY_MESSAGE)
            continue
        node = matches[0]
        checksum = hashlib.sha256(describe_ast(node).encode()).hexdigest()
        if (declaration['pending_unit'] not in installed_units and
                checksum == declaration['legacy_ast_sha256']):
            continue
        try:
            rows = ast.literal_eval(node)
            if declaration.get('encoding') == 'keyed-json-tuples':
                rows = [dict(id=row[0], key=json.loads(row[1]), input_tokens=row[2])
                        if isinstance(row, tuple) else row for row in rows]
        except (ValueError, TypeError, IndexError):
            errors.append(PRIOR_KEY_MESSAGE)
            continue
        if not isinstance(rows, (list, tuple)) or any(not isinstance(row, dict) for row in rows):
            errors.append(PRIOR_KEY_MESSAGE)
        else:
            errors.extend(validate_capacity_evidence(rows))
    return errors
