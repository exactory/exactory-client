"""Each declared call row names a call of its caller, and each packet call of a certified function has a row."""
import ast
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from ci.capacity_packet_inventory import REGISTRY_PATH, compute_ast_digest, inspect_packet_sources, read_packet_registry

ROOT = Path(__file__).resolve().parents[1]
HELPER = 'def read(value):\n    return len(value)\n\n\ndef check(value):\n    return bool(value)\n'
CALL_ROW_LISTS = ('callee_inventory', 'mapping_sites', 'non_packet_sites')


def read_production_sources():
    return {'.'.join(path.relative_to(ROOT).with_suffix('').parts): path.read_text(encoding='utf-8')
            for path in (ROOT / 'research_harness').glob('*.py')}


def make_row(caller, call, classification='listed module', targets=None):
    """Make one callee_inventory row for a call written as source text."""
    node = ast.parse(call, mode='eval').body
    callee = ast.unparse(node.func)
    return dict(ast_sha256=compute_ast_digest(node), callee=callee, caller=caller,
                classification=classification, targets=targets or [callee])


def make_assignment_row(caller, statement, callee):
    """Make one mapping_sites row for an assignment statement written as source text."""
    return dict(ast_sha256=compute_ast_digest(ast.parse(statement).body[0]), callee=callee, caller=caller,
                classification='reviewed local write site', form='reviewed local write', reason='Test row.')


def certify(sources, rows, listed=None, mapping_rows=()):
    """Build a registry that certifies each listed module at its current source, with these call rows."""
    registry = dict(modules=sorted(listed or sources), builders={}, readers=[], stdlib_readers=[], roles={},
                    source_members=[], computed_sites={}, fixtures={}, callee_inventory=list(rows),
                    mapping_sites=list(mapping_rows), non_packet_sites=[], certificates={})
    for module in registry['modules']:
        tree = ast.parse(sources[module])
        registry['certificates'][module] = dict(
            ast_sha256=compute_ast_digest(tree), reason='Test certificate.',
            functions={node.name: compute_ast_digest(node) for node in tree.body if isinstance(node, ast.FunctionDef)},
            packet_call_digests=sorted({row['ast_sha256'] for row in list(rows) + list(mapping_rows)
                                        if row['caller'].startswith(module + '.')}))
    return registry


def find_errors(errors, *words):
    return [error for error in errors if all(word in error for word in words)]


def renew_packet_call_digests(registry, module):
    """Set packet_call_digests of one module to the digests of its rows, as a lane renews it by hand."""
    registry['certificates'][module]['packet_call_digests'] = sorted(
        {row['ast_sha256'] for name in CALL_ROW_LISTS for row in registry[name] if row['caller'].startswith(module + '.')})


class CallRowTests(unittest.TestCase):
    BUILDER = 'import helper\n\n\ndef build(packet):\n    return helper.read(packet)\n'
    READ_ROW = dict(caller='builder.build', call='helper.read(packet)', targets=['helper.read'])

    def inspect(self, builder, rows, **sources):
        sources = dict(dict(builder=builder, helper=HELPER), **sources)
        return inspect_packet_sources(sources, certify(sources, rows, listed=['builder', 'helper']))

    def test_a_row_for_each_packet_call_passes(self):
        self.assertEqual(self.inspect(self.BUILDER, [make_row(**self.READ_ROW)]), [])

    def test_a_row_whose_call_left_the_source_is_refused(self):
        stale = make_row('builder.build', 'helper.check(packet)', targets=['helper.check'])
        errors = self.inspect(self.BUILDER, [make_row(**self.READ_ROW), stale])
        self.assertEqual(len(find_errors(errors, 'builder.build', 'helper.check', stale['ast_sha256'][:12])), 1)

    def test_a_packet_call_without_a_row_is_refused(self):
        errors = self.inspect(self.BUILDER, [])
        self.assertEqual(len(find_errors(errors, 'builder.build', 'helper.read', 'at line 5', 'no declaration')), 1)

    def test_the_union_of_the_rows_of_two_versions_of_a_function_is_refused(self):
        # One side of a merge replaced helper.read by helper.check. A list union keeps the replaced row.
        changed = self.BUILDER.replace('helper.read', 'helper.check')
        rows = [make_row(**self.READ_ROW), make_row('builder.build', 'helper.check(packet)', targets=['helper.check'])]
        errors = self.inspect(changed, rows)
        self.assertEqual(len(find_errors(errors, 'builder.build', 'helper.read')), 1)
        self.assertEqual(find_errors(errors, 'helper.check'), [])

    def test_a_row_repeated_more_often_than_its_call_is_refused(self):
        errors = self.inspect(self.BUILDER, [make_row(**self.READ_ROW), make_row(**self.READ_ROW)])
        self.assertEqual(len(find_errors(errors, 'builder.build', 'helper.read')), 1)
        twice = 'import helper\n\n\ndef build(packet):\n    return helper.read(packet) or helper.read(packet)\n'
        self.assertEqual(self.inspect(twice, [make_row(**self.READ_ROW), make_row(**self.READ_ROW)]), [])
        self.assertEqual(self.inspect(twice, [make_row(**self.READ_ROW)]), [])

    def test_a_part_and_an_alias_of_a_packet_are_packets(self):
        for call, alias in (('helper.read(packet["nested"])', ''), ('helper.read(second)', '    second = packet\n'),
                            ('helper.read(third)', '    second = packet["nested"]\n    third = second\n'),
                            ('helper.read(value=packet)', ''), ('helper.read(*packet)', ''),
                            ('helper.read(len(packet))', '')):
            source = 'import helper\n\n\ndef build(packet):\n' + alias + '    return ' + call + '\n'
            rows = [make_row('builder.build', call, targets=['helper.read'])]
            if call == 'helper.read(len(packet))':
                rows.append(make_row('builder.build', 'len(packet)', 'standard library', ['builtins.len']))
            with self.subTest(call=call):
                self.assertEqual(len(find_errors(self.inspect(source, rows[1:]), 'builder.build', 'helper.read')), 1)
                self.assertEqual(self.inspect(source, rows), [])

    def test_a_call_that_passes_no_packet_needs_no_row(self):
        source = 'import helper\n\n\ndef build(packet, name):\n    return helper.read(name), packet.get("known")\n'
        self.assertEqual(self.inspect(source, []), [])

    def test_methods_nested_functions_and_the_plural_name_are_inspected(self):
        cases = {
            'builder.Holder.keep': 'import helper\n\n\nclass Holder:\n    def keep(self, packet):\n        return helper.read(packet)\n',
            'builder.outer': ('import helper\n\n\ndef outer(rows):\n    def visit(packet):\n'
                              '        return helper.read(packet)\n    return [visit(row) for row in rows]\n'),
            'builder.project': 'import helper\n\n\ndef project(packets):\n    return helper.read(packets)\n',
        }
        calls = {'builder.Holder.keep': 'helper.read(packet)', 'builder.outer': 'helper.read(packet)',
                 'builder.project': 'helper.read(packets)'}
        for caller, source in cases.items():
            with self.subTest(caller=caller):
                self.assertEqual(len(find_errors(self.inspect(source, []), caller, 'helper.read')), 1)
                self.assertEqual(self.inspect(source, [make_row(caller, calls[caller], targets=['helper.read'])]), [])

    def test_a_declared_builder_passes_the_packet_that_it_holds_in_a_local(self):
        source = 'import helper\n\n\ndef build(records):\n    packet = {}\n    helper.read(packet)\n    return packet\n'
        sources = dict(builder=source, helper=HELPER)
        registry = certify(sources, [])
        self.assertEqual(inspect_packet_sources(sources, registry), [])
        registry['builders'] = {'builder.build': {'parameters': []}}
        self.assertEqual(len(find_errors(inspect_packet_sources(sources, registry), 'builder.build', 'helper.read')), 1)

    def test_a_row_names_the_plugin_function_that_its_call_reaches(self):
        for classification, targets in (('standard library', ['helper.read']), ('listed module', ['helper.check'])):
            row = make_row('builder.build', 'helper.read(packet)', classification, targets)
            with self.subTest(classification=classification):
                errors = self.inspect(self.BUILDER, [row])
                self.assertEqual(len(find_errors(errors, 'builder.build', 'at line 5', 'reaches helper.read')), 1)

    def test_a_call_of_a_nested_function_is_not_taken_for_the_name_that_it_shadows(self):
        from ci.capacity_packet_inventory import apply_call_rows, describe_call_rows
        # The module imports helper.read, and the caller defines its own read and calls that one.
        source = ('from helper import read\n\n\ndef build(packet):\n    def read(value):\n        return value\n'
                  '    return read(packet)\n')
        sources = dict(builder=source, helper=HELPER)
        registry = certify(sources, [])
        differences = describe_call_rows(sources, registry)
        self.assertEqual([(item['kind'], item['row']['classification'], item['row']['targets']) for item in differences],
                         [('missing', 'listed module', ['builder.build.read'])])
        self.assertEqual(apply_call_rows(registry, differences), [])
        self.assertEqual(inspect_packet_sources(sources, registry), [])

    def test_a_packet_cannot_reach_an_unlisted_plugin_module_as_standard_library(self):
        source = 'import outside\n\n\ndef build(packet):\n    return outside.mutate(packet)\n'
        outside = 'def mutate(value):\n    value["known"] = []\n'
        for classification in ('standard library', 'listed module'):
            row = make_row('builder.build', 'outside.mutate(packet)', classification, ['outside.mutate'])
            with self.subTest(classification=classification):
                errors = find_errors(self.inspect(source, [row], outside=outside), 'builder.build', 'outside.mutate')
                self.assertTrue(errors)
                self.assertTrue(all('list and certify that module' in error for error in errors))

    def test_a_call_from_an_uncertified_builder_into_a_certified_module_has_a_row(self):
        sources = dict(builder='import helper\n\n\ndef build_packet(packet):\n    return helper.read(packet)\n', helper=HELPER)
        row = make_row('builder.build_packet', 'helper.read(packet)', targets=['helper.read'])
        for rows, expected in (([], 1), ([row], 0)):
            registry = certify(sources, rows, listed=['builder', 'helper'])
            del registry['certificates']['builder']
            registry['builders'] = {'builder.build_packet': {'parameters': ['packet']}}
            with self.subTest(rows=len(rows)):
                self.assertEqual(len(find_errors(inspect_packet_sources(sources, registry), 'builder.build_packet', 'helper.read')), expected)

    def test_a_row_names_a_function_or_a_method_of_a_certified_module(self):
        row = make_row('builder.removed', 'helper.read(packet)', targets=['helper.read'])
        errors = self.inspect(self.BUILDER, [make_row(**self.READ_ROW), row])
        self.assertEqual(len(find_errors(errors, 'builder.removed', 'helper.read')), 1)

    def test_a_row_whose_call_changed_stays_for_the_reviewer_with_the_digest_of_the_new_call(self):
        from ci.capacity_packet_inventory import apply_call_rows, describe_call_rows
        # The declared call changed its arguments. The first new form passes no packet name, so no comparison
        # asks a row for it, and a removal of the old row would lose the declaration of the packet edge.
        row = make_row(**self.READ_ROW)
        for call in ("helper.read(first['packet'])", 'helper.read(packet, 1)'):
            source = 'import helper\n\n\ndef build(packet, first):\n    return ' + call + '\n'
            sources = dict(builder=source, helper=HELPER)
            registry = certify(sources, [row], listed=['builder', 'helper'])
            new = make_row('builder.build', call)['ast_sha256']
            with self.subTest(call=call):
                errors = inspect_packet_sources(sources, registry)
                self.assertEqual(len(errors), 1)
                self.assertEqual(len(find_errors(errors, 'builder.build', 'helper.read', row['ast_sha256'][:12],
                                                 'digest ' + new[:12] + ' at line 5')), 1)
                differences = describe_call_rows(sources, registry)
                self.assertEqual([(item['kind'], item['digest'], item['undeclared']) for item in differences],
                                 [('changed', row['ast_sha256'], [dict(digest=new, line=5)])])
                self.assertEqual(apply_call_rows(registry, differences), differences)
                self.assertEqual(registry['callee_inventory'], [row])
                self.assertEqual(registry['certificates']['builder']['packet_call_digests'], [row['ast_sha256']])

    def test_a_changed_assignment_statement_keeps_its_mapping_row(self):
        from ci.capacity_packet_inventory import apply_call_rows, describe_call_rows
        template = 'def build(packet):\n    row = {{}}\n    {statement}\n    return row\n'
        before, after = "row['known'] = len(packet)", "row['known'] = len(packet) + 1"
        length = make_row('builder.build', 'len(packet)', 'standard library', ['builtins.len'])
        mapping = make_assignment_row('builder.build', before, 'row.known')
        sources = dict(builder=template.format(statement=before))
        self.assertEqual(inspect_packet_sources(sources, certify(sources, [length], mapping_rows=[mapping])), [])
        sources = dict(builder=template.format(statement=after))
        registry = certify(sources, [length], mapping_rows=[mapping])
        differences = describe_call_rows(sources, registry)
        self.assertEqual([(item['kind'], item['collection'], item['undeclared']) for item in differences],
                         [('changed', 'mapping_sites', [dict(digest=make_assignment_row(
                             'builder.build', after, 'row.known')['ast_sha256'], line=3)])])
        self.assertEqual(apply_call_rows(registry, differences), differences)
        self.assertEqual(registry['mapping_sites'], [mapping])

    def test_rows_of_one_call_in_one_list_are_equal(self):
        from ci.capacity_packet_inventory import apply_call_rows, describe_call_rows
        twice = 'import helper\n\n\ndef build(packet):\n    return helper.read(packet), helper.read(packet)\n'
        other = make_row('builder.build', 'helper.read(packet)', targets=['helper.read', 'helper.check'])
        rows = [make_row(**self.READ_ROW), other]
        errors = self.inspect(twice, rows)
        self.assertEqual(len(find_errors(errors, 'callee_inventory', 'builder.build', 'helper.read', 'not equal')), 1)
        sources = dict(builder=twice, helper=HELPER)
        registry = certify(sources, rows, listed=['builder', 'helper'])
        self.assertEqual([item['kind'] for item in apply_call_rows(registry, describe_call_rows(sources, registry))],
                         ['unequal'])
        self.assertEqual(registry['callee_inventory'], rows)


class ProductionCallRowTests(unittest.TestCase):
    """The same comparisons on the production sources and their registry."""

    CALLER = 'research_harness.review_input_projection._require_review_packet'
    CALLEE = 'research_harness.review_context_repair.is_same_subject'
    MARKER = "    subject = packet.get('subject') if isinstance(packet, dict) else None\n"

    def setUp(self):
        self.sources = read_production_sources()
        self.registry = read_packet_registry()

    def find_row(self, caller=CALLER, callee=CALLEE):
        rows = [row for row in self.registry['callee_inventory'] if row['caller'] == caller and row['callee'] == callee]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_a_stale_production_row_is_refused(self):
        row = dict(self.find_row(), ast_sha256='0' * 64, callee='research_harness.review_context_repair.removed_helper')
        self.registry['callee_inventory'].append(row)
        module = self.CALLER.rpartition('.')[0]
        self.registry['certificates'][module]['packet_call_digests'].append(row['ast_sha256'])
        errors = inspect_packet_sources(self.sources, self.registry)
        self.assertEqual(len(find_errors(errors, self.CALLER, row['callee'], '0' * 12)), 1)

    def test_a_production_packet_call_without_its_row_is_refused(self):
        row = self.find_row()
        self.registry['callee_inventory'].remove(row)
        renew_packet_call_digests(self.registry, self.CALLER.rpartition('.')[0])
        errors = inspect_packet_sources(self.sources, self.registry)
        self.assertEqual(len(find_errors(errors, self.CALLER, self.CALLEE, row['ast_sha256'][:12], 'no declaration')), 1)

    def test_a_new_packet_call_needs_a_row_after_the_certificate_is_renewed(self):
        # A lane adds a packet call and renews the digests of the module and of the function. It adds no row.
        module = self.CALLER.rpartition('.')[0]
        self.assertEqual(self.sources[module].count(self.MARKER), 1)
        self.sources[module] = self.sources[module].replace(
            self.MARKER, self.MARKER + '    if False:\n        unlisted_helper.mutate(packet)\n')
        tree = ast.parse(self.sources[module])
        certificate = self.registry['certificates'][module]
        certificate['ast_sha256'] = compute_ast_digest(tree)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == self.CALLER.rpartition('.')[2])
        certificate['functions'][function.name] = compute_ast_digest(function)
        errors = inspect_packet_sources(self.sources, self.registry)
        self.assertEqual(len(find_errors(errors, self.CALLER, 'unlisted_helper.mutate', 'no declaration')), 1)

    def test_the_changed_find_protocol_call_of_describe_chain_keeps_its_row(self):
        # 0.91.4 changed reading.find_protocol(packet) into the call below. Its first argument holds no packet
        # name, so no comparison asks a row for the new call; the row of the old call has to stay for review.
        caller = 'research_harness.openness_carried._describe_chain'
        module = caller.rpartition('.')[0]
        row = self.find_row(caller, 'research_harness.openness_reading.find_protocol')
        old = compute_ast_digest(ast.parse('reading.find_protocol(packet)', mode='eval').body)
        function = next(node for node in ast.parse(self.sources[module]).body
                        if isinstance(node, ast.FunctionDef) and node.name == caller.rpartition('.')[2])
        call = next(node for node in ast.walk(function) if isinstance(node, ast.Call)
                    and ast.unparse(node) == "reading.find_protocol(json.loads(artifacts.read(first['packet'])))")
        new = compute_ast_digest(call)
        row['ast_sha256'] = old
        renew_packet_call_digests(self.registry, module)
        errors = inspect_packet_sources(self.sources, self.registry)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(find_errors(errors, caller, 'find_protocol', old[:12],
                                         'digest {} at line {}'.format(new[:12], call.lineno))), 1)
        from ci.capacity_packet_inventory import apply_call_rows, describe_call_rows
        differences = describe_call_rows(self.sources, self.registry)
        self.assertEqual([(item['kind'], item['caller'], item['digest'], item['undeclared']) for item in differences],
                         [('changed', caller, old, [dict(digest=new, line=call.lineno)])])
        self.assertEqual(apply_call_rows(self.registry, differences), differences)
        self.assertIn(row, self.registry['callee_inventory'])

    def test_the_production_rows_and_call_sites_agree(self):
        from ci.capacity_packet_inventory import describe_call_rows
        self.assertEqual(describe_call_rows(self.sources, self.registry), [])

    def test_the_command_writes_the_rows_that_the_source_determines(self):
        from ci.capacity_packet_inventory import apply_call_rows, describe_call_rows
        row = self.find_row()
        stale = dict(row, ast_sha256='0' * 64, callee='research_harness.review_context_repair.removed_helper')
        self.registry['callee_inventory'].remove(row)
        self.registry['callee_inventory'].append(stale)
        differences = describe_call_rows(self.sources, self.registry)
        self.assertEqual(sorted(difference['kind'] for difference in differences), ['missing', 'stale'])
        self.assertEqual(apply_call_rows(self.registry, differences), [])
        self.assertEqual(describe_call_rows(self.sources, self.registry), [])
        self.assertEqual(inspect_packet_sources(self.sources, self.registry), [])
        written = self.registry['callee_inventory'][-1]
        self.assertEqual({key: written[key] for key in ('ast_sha256', 'caller', 'classification', 'targets')},
                         {key: row[key] for key in ('ast_sha256', 'caller', 'classification', 'targets')})
        self.assertEqual(self.registry['certificates'], read_packet_registry()['certificates'])

    def test_the_command_leaves_a_callee_without_a_determined_target_to_the_reviewer(self):
        from ci.capacity_packet_inventory import apply_call_rows, describe_call_rows
        module = self.CALLER.rpartition('.')[0]
        self.sources[module] = self.sources[module].replace(self.MARKER, self.MARKER + '    callback(packet)\n')
        before = copy.deepcopy(self.registry['callee_inventory'])
        remaining = apply_call_rows(self.registry, describe_call_rows(self.sources, self.registry))
        self.assertEqual([(row['kind'], row['callee'], row['row']) for row in remaining], [('missing', 'callback', None)])
        self.assertEqual(self.registry['callee_inventory'], before)


class CallRowCommandTests(unittest.TestCase):
    """The command on a small plugin tree in a temporary folder."""

    BUILDER = 'from research_harness import helper\n\n\ndef build(packet):\n    return helper.read(packet)\n'

    def write_tree(self, builder):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'research_harness').mkdir()
        sources = {'research_harness.builder': builder, 'research_harness.helper': HELPER}
        for module, source in sources.items():
            (root / (module.replace('.', '/') + '.py')).write_text(source, encoding='utf-8')
        path = root / REGISTRY_PATH.relative_to(ROOT)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(certify(sources, []), indent=2) + '\n', encoding='utf-8')
        return root, path

    def run_command(self, *argv):
        from ci.capacity_packet_inventory import run_cli
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = run_cli(list(argv))
        return status, json.loads(output.getvalue())['differences']

    def test_the_command_prints_each_difference_and_writes_nothing(self):
        root, path = self.write_tree(self.BUILDER)
        stored = path.read_bytes()
        status, differences = self.run_command('--root', str(root), '--call-rows')
        self.assertEqual(status, 1)
        self.assertEqual([(item['kind'], item['callee'], item['line'], item['row']['classification'])
                          for item in differences], [('missing', 'research_harness.helper.read', 5, 'listed module')])
        self.assertEqual(path.read_bytes(), stored)

    def test_the_command_writes_the_determined_rows_and_exits_with_zero(self):
        root, path = self.write_tree(self.BUILDER)
        status, differences = self.run_command('--root', str(root), '--write-call-rows')
        self.assertEqual((status, differences), (0, []))
        written = path.read_text(encoding='utf-8')
        registry = json.loads(written)
        self.assertEqual(written, json.dumps(registry, indent=2) + '\n')
        row = make_row('research_harness.builder.build', 'helper.read(packet)')
        self.assertEqual(registry['callee_inventory'], [dict(row, callee='research_harness.helper.read',
                                                              targets=['research_harness.helper.read'])])
        self.assertEqual(registry['certificates']['research_harness.builder']['packet_call_digests'], [row['ast_sha256']])
        self.assertEqual(self.run_command('--root', str(root), '--write-call-rows'), (0, []))
        self.assertEqual(path.read_text(encoding='utf-8'), written)

    def test_the_command_exits_with_one_while_a_call_needs_a_reviewer(self):
        builder = self.BUILDER.replace('def build(packet):\n', 'def build(packet, callback):\n    callback(packet)\n')
        root, path = self.write_tree(builder)
        status, differences = self.run_command('--root', str(root), '--write-call-rows')
        self.assertEqual(status, 1)
        self.assertEqual([(item['kind'], item['callee'], item['line'], item['row']) for item in differences],
                         [('missing', 'callback', 5, None)])
        registry = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual([row['callee'] for row in registry['callee_inventory']], ['research_harness.helper.read'])

    def test_the_stored_registry_has_the_format_that_the_command_writes(self):
        from ci.capacity_packet_inventory import format_packet_registry
        self.assertEqual(format_packet_registry(read_packet_registry()).encode('utf-8'), REGISTRY_PATH.read_bytes())


if __name__ == '__main__':
    unittest.main()
