"""Every place that reads the installed reviewer runtime is declared with its purpose.

A stored observation (a probe, an attempt, a staged observation, an output correction) is authenticated against the
runtime that its route record pins. The installed runtime is read to pin a new route record, to admit or plan a new
call, to key the state memo and to report. A function that reads it for another purpose fails this test: declare
its purpose here, and never read the installed runtime to authenticate a stored observation.
"""

import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCES_PATH = Path(__file__).resolve().parents[1] / 'research_harness'
# The functions that read the installed runtime, and the functions that only pass such a reading on.
READER_NAMES = frozenset(('read_route_runtime', '_require_route_runtime', 'read_runtime_fingerprint',
                          '_runtime_fingerprint', 'read_installed_cli_version', 'is_route_stale',
                          'require_installed_runtime'))
ADAPTER_MODULE_NAMES = frozenset(('review_transport', 'review_codex_transport', 'review_codex_framed_transport',
                                  'review_codex_visual_transport', 'review_claude_transport'))
# The two local names that hold an adapter module chosen at run time. Every imported name is read from the source.
RUN_TIME_ALIASES = frozenset(('local', 'adapter'))
PURPOSES = ('reader', 'new_record', 'new_call', 'plan', 'memo_key', 'report')
DECLARED_SITES = {
    'review_transport.runtime': 'reader',
    'review_codex_visual_transport.runtime': 'reader',
    'review_protocol.read_route_runtime': 'reader',
    'review_protocol._require_route_runtime': 'reader',
    'review_protocol.is_route_stale': 'reader',
    'review_protocol.require_installed_runtime': 'reader',
    'artifacts.ArtifactStore.read_runtime_fingerprint': 'reader',
    'record_check.OfflineArtifacts.read_runtime_fingerprint': 'reader',
    'review_session_v4._InventoryArtifacts.read_runtime_fingerprint': 'reader',
    'review_state_evaluation._runtime_fingerprint': 'reader',
    'review_route_capacity.read_installed_cli_version': 'reader',
    'review_protocol.record_route': 'new_record',
    'review_transport.observe': 'new_call',
    'review_codex_transport.send_request': 'new_call',
    'review_codex_framed_transport.send_request': 'new_call',
    'review_codex_visual_transport.send_request': 'new_call',
    'review_claude_transport.send_request': 'new_call',
    'review_protocol._observed': 'new_call',
    'review_protocol.probe_route': 'new_call',
    'review_protocol.prepare_assignment_call': 'new_call',
    'review_protocol._prepare_assignment': 'new_call',
    'review_staged_protocol.invoke_task': 'new_call',
    'review_staged_protocol.plan_frontier': 'new_call',
    'review_refusal_replacement.prepare_replacement': 'new_call',
    'review_output_correction.invoke_correction': 'new_call',
    'review_route_capacity.find_serving_routes': 'plan',
    'review_failure_class.plan_refusal_recovery': 'plan',
    'review_route_plan.read_route_standing': 'plan',
    'review_route_plan.list_current_routes': 'plan',
    'review_state_evaluation.runtime_scope': 'memo_key',
    'review_state_evaluation.evaluate_state': 'memo_key',
    'review_route_capacity.describe_open_route_reports': 'report',
}


def _find_adapter_names(tree):
    """The names under which one module holds an adapter module, and the names under which it holds the runtime
    reader of one."""
    modules, readers = set(RUN_TIME_ALIASES), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = (node.module or '').rsplit('.', 1)[-1]
            for name in node.names:
                if name.name in ADAPTER_MODULE_NAMES:
                    modules.add(name.asname or name.name)
                elif source in ADAPTER_MODULE_NAMES and name.name == 'runtime':
                    readers.add(name.asname or name.name)
        elif isinstance(node, ast.Import):
            for name in node.names:
                if name.name.rsplit('.', 1)[-1] in ADAPTER_MODULE_NAMES:
                    modules.add(name.asname or name.name.rsplit('.', 1)[-1])
    return modules, readers


def _find_sites():
    """The outermost function or method of every reference to a reader of the installed runtime."""
    sites = set()
    for path in sorted(SOURCES_PATH.glob('*.py')):
        module = path.stem
        tree = ast.parse(path.read_text(encoding='utf-8'))
        modules, readers = _find_adapter_names(tree)
        if module in ADAPTER_MODULE_NAMES:
            readers.add('runtime')

        def visit(node, owner):
            for child in ast.iter_child_nodes(node):
                name = owner
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
                        owner is None or isinstance(node, ast.ClassDef)):
                    name = child.name if owner is None else owner + '.' + child.name
                reads = (isinstance(child, ast.Name) and child.id in READER_NAMES
                         or isinstance(child, ast.Attribute) and child.attr in READER_NAMES
                         or isinstance(child, ast.Attribute) and child.attr == 'runtime'
                         and isinstance(child.value, ast.Name) and child.value.id in modules
                         or isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                         and child.func.id in readers)
                if reads and isinstance(getattr(child, 'ctx', ast.Load()), ast.Load):
                    sites.add(module + '.' + (name or '<module>'))
                visit(child, name)
        visit(tree, None)
    return sites


class InstalledRuntimeReaderTests(unittest.TestCase):
    def test_every_reader_of_the_installed_runtime_is_declared(self):
        self.assertEqual(set(DECLARED_SITES.values()) - set(PURPOSES), set())
        self.assertEqual(sorted(_find_sites() - set(DECLARED_SITES)), [],
                         'These functions read the installed reviewer runtime without a declared purpose.')

    def test_every_declared_site_still_reads_the_installed_runtime(self):
        self.assertEqual(sorted(set(DECLARED_SITES) - _find_sites()), [],
                         'These declared functions no longer read the installed runtime; remove their rows.')


class RuntimeOverlayTests(unittest.TestCase):
    def test_route_standing_passes_through_the_inventory_overlay(self):
        """The inventory overlay raises AttributeError on the base when route standing reads the runtime."""
        from research_harness.artifacts import ArtifactStore
        from research_harness.evaluation import Evaluation
        from research_harness.review_route_plan import read_route_standing
        from research_harness.review_session_v4 import _InventoryArtifacts

        route = dict(id='fixture', adapter='codex_cli', runtime=dict(version='recorded'))
        records = dict(review_route={'fixture': route})
        with tempfile.TemporaryDirectory() as root:
            store = ArtifactStore(Path(root))
            for runtime in (route['runtime'], dict(version='changed')):
                with patch.object(store, 'read_runtime_fingerprint', return_value=runtime):
                    expected = read_route_standing(records, store, route['id'])
                    self.assertEqual(expected['stale'], runtime != route['runtime'])
                    for source in (store, Evaluation(records, store)):
                        with self.subTest(source=type(source).__name__, runtime=runtime):
                            self.assertEqual(read_route_standing(
                                records, _InventoryArtifacts(source), route['id']), expected)

    def test_session_inventory_does_not_read_the_installed_runtime(self):
        """Contract on the base and with runtime forwarding. Inventory alone reads no reviewer state."""
        from research_harness.artifacts import ArtifactStore
        from research_harness.review_session_v4 import build_session_inventory

        with tempfile.TemporaryDirectory() as root:
            store = ArtifactStore(Path(root))
            with patch.object(store, 'read_runtime_fingerprint') as read_runtime:
                inventory = build_session_inventory({}, store, {})
            self.assertEqual(inventory['inventory']['units'], [])
            self.assertEqual(read_runtime.call_count, 0)


if __name__ == '__main__':
    unittest.main()
