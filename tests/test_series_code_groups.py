"""Evidence checks for reviewed groups of reachable stop codes."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from research_harness import stop_classes
from ci.series_code_groups import read_group_entries


class StopGroupRegistryTests(unittest.TestCase):
    def test_stored_state_retains_the_record_check_decision(self):
        groups = getattr(stop_classes, 'STOP_GROUPS', {})
        self.assertIn('stored_state', groups)
        group = groups['stored_state']
        self.assertEqual(group['id'], 'stored_state')
        self.assertEqual(group['class'], 'decision')
        self.assertEqual(group['owner'], 'D2')
        self.assertEqual(set(group['conditions']), {'store', 'content', 'payload'})
        self.assertEqual(group['next'], stop_classes.STOP_REGISTRY['artifact_corrupt']['next'])

    def test_registry_refuses_a_row_that_references_an_unknown_group(self):
        row = copy.deepcopy(stop_classes.STOP_REGISTRY['artifact_corrupt'])
        row['group'] = 'absent'
        with self.assertRaisesRegex(ValueError, 'Unknown stop group'):
            stop_classes.validate_registry({row['code']: row})

    def test_registry_requires_the_group_body_without_group_metadata(self):
        valid = dict(code='grouped', group='stored_state', **{'class': 'decision'}, owner='D2',
                     next=copy.deepcopy(stop_classes.STOP_GROUPS['stored_state']['next']))
        stop_classes.validate_registry({'grouped': valid})
        for changed in ({'class': 'waiting'}, {'owner': 'another'}, {'next': {'kind': 'done'}},
                        {'id': 'stored_state'}, {'conditions': {'store': 'metadata'}}):
            with self.subTest(changed=changed):
                row = dict(valid, **changed)
                with self.assertRaisesRegex(ValueError, 'does not match its stop group'):
                    stop_classes.validate_registry({'grouped': row})


class GroupEntryTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.fixtures = self.root / 'tests/fixtures/series_code_groups'
        self.fixtures.mkdir(parents=True)
        self.groups = copy.deepcopy(stop_classes.STOP_GROUPS)
        self.registry = {'grouped': dict(code='grouped', group='stored_state',
            **{'class': 'decision'}, owner='D2', next=copy.deepcopy(self.groups['stored_state']['next']))}
        self.report = {'code_sites': {'grouped': ['pkg/core.py:check', 'pkg/store.py:read']}}
        self.entry = {'stored_state': {
            'members': {'grouped': {'pkg/core.py:check': 'store', 'pkg/store.py:read': 'store'}}}}

    def write(self, entry, lane='lane'):
        (self.fixtures / (lane + '.json')).write_text(json.dumps(entry))

    def read(self):
        return read_group_entries(self.root, self.report, self.registry, self.groups)

    def test_reads_empty_group_files(self):
        self.write({})
        self.assertEqual(read_group_entries(self.root, {}, {}, {}), {})

    def test_accepts_member_only_entries_without_proofs_or_command_function_sets(self):
        self.entry['stored_state']['members']['grouped']['pkg/core.py:check'] = 'content'
        self.entry['stored_state']['members']['grouped']['pkg/store.py:read'] = 'payload'
        self.write(self.entry)
        self.assertEqual(self.read(), {'stored_state': {'members': {'grouped': {
            'pkg/core.py:check': 'content', 'pkg/store.py:read': 'payload'}}}})

    def test_merges_members_across_lanes(self):
        self.registry['other'] = dict(self.registry['grouped'], code='other')
        self.report['code_sites']['other'] = ['pkg/core.py:other']
        self.write(self.entry, 'a_members')
        self.write({'stored_state': {'members': {'other': {'pkg/core.py:other': 'content'}}}}, 'b_members')
        self.assertEqual(self.read(), {'stored_state': {
            'members': {'grouped': {'pkg/core.py:check': 'store', 'pkg/store.py:read': 'store'},
                        'other': {'pkg/core.py:other': 'content'}}}})

    def test_refuses_the_obsolete_proofs_field(self):
        self.registry = {}
        self.write({'stored_state': {'members': {}, 'proofs': {}}})
        with self.assertRaisesRegex(ValueError, 'Group evidence needs only a members table'):
            self.read()

    def test_refuses_other_fields_or_a_missing_members_table(self):
        self.registry = {}
        for entry in ({}, {'members': {}, 'extra': {}}, {'members': []}):
            with self.subTest(entry=entry):
                self.write({'stored_state': entry})
                with self.assertRaisesRegex(ValueError, 'Group evidence needs only a members table'):
                    self.read()

    def test_refuses_added_removed_or_moved_member_sites(self):
        for sites in ({'pkg/core.py:check': 'store'},
                      {'pkg/core.py:check': 'store', 'pkg/store.py:read': 'store', 'pkg/extra.py:check': 'store'},
                      {'pkg/core.py:check': 'store', 'pkg/store.py:renamed': 'store'}):
            with self.subTest(sites=sites):
                self.entry['stored_state']['members']['grouped'] = sites
                self.write(self.entry)
                with self.assertRaisesRegex(ValueError, 'Group member sites differ.*grouped'):
                    self.read()

    def test_refuses_a_condition_the_group_does_not_define(self):
        self.entry['stored_state']['members']['grouped']['pkg/core.py:check'] = 'network'
        self.write(self.entry)
        with self.assertRaisesRegex(ValueError, 'Unknown group condition.*network'):
            self.read()

    def test_refuses_a_registry_group_row_without_an_entry(self):
        self.write({})
        with self.assertRaisesRegex(ValueError, 'Registry group row has no entry.*grouped'):
            self.read()

    def test_refuses_an_entry_without_the_matching_registry_group_row(self):
        self.write(self.entry)
        for registry in ({}, {'grouped': {'code': 'grouped'}},
                         {'grouped': {'code': 'grouped', 'group': 'another'}}):
            with self.subTest(registry=registry):
                self.registry = registry
                with self.assertRaisesRegex(ValueError, 'Group entry has no matching registry row.*grouped'):
                    self.read()

    def test_refuses_an_undefined_group(self):
        self.write({'absent': self.entry['stored_state']})
        with self.assertRaisesRegex(ValueError, 'Unknown stop group.*absent'):
            self.read()


if __name__ == '__main__':
    unittest.main()
