"""Read-only evidence guard for groups of reachable stop codes."""

import json
from pathlib import Path


def read_group_entries(root, report, registry, groups):
    """Validate each writing place and derive its group from all its conditions."""
    merged = {}
    folder = Path(root) / 'tests/fixtures/series_code_groups'
    for path in sorted(folder.glob('*.json')):
        entries = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(entries, dict):
            raise ValueError('Group evidence must be a table: ' + str(path))
        for group, entry in entries.items():
            if group not in groups:
                raise ValueError('Unknown stop group in evidence: ' + group)
            if (not isinstance(entry, dict) or set(entry) != {'members'}
                    or not isinstance(entry['members'], dict)):
                raise ValueError('Group evidence needs only a members table: ' + str(path) + ': ' + group)
            combined = merged.setdefault(group, dict(members={}))
            for code, sites in entry['members'].items():
                if not isinstance(sites, dict):
                    raise ValueError('Group member needs a site-to-conditions table: ' + code)
                for site, conditions in sites.items():
                    if (not isinstance(conditions, list) or not conditions
                            or not all(isinstance(condition, str) for condition in conditions)
                            or conditions != sorted(set(conditions))):
                        raise ValueError('A place needs a nonempty sorted list of unique conditions: ' + code + ': ' + site)
                if code in combined['members'] and combined['members'][code] != sites:
                    raise ValueError('Conflicting group member entries: ' + code)
                combined['members'][code] = sites

    memberships = {}
    for group, entry in merged.items():
        conditions = groups[group]['conditions']
        for code, sites in entry['members'].items():
            if registry.get(code, {}).get('group') != group:
                raise ValueError('Group entry has no matching registry row: ' + group + ': ' + code)
            memberships[code] = group
            actual = set(report.get('code_sites', {}).get(code, ()))
            if not actual or set(sites) != actual:
                raise ValueError('Group member sites differ: ' + code + ': expected ' + str(sorted(actual))
                                 + ', found ' + str(sorted(sites)))
            union = {condition for values in sites.values() for condition in values}
            known = set(conditions)
            if group == 'stored_state':
                known.update(groups['named_cause']['conditions'])
            for condition in sorted(union - known):
                raise ValueError('Unknown group condition at member site: ' + group + ': ' + condition)
            if group in ('stored_state', 'named_cause'):
                expected = ('stored_state' if union <= set(groups['stored_state']['conditions']) else 'named_cause')
                if group != expected:
                    raise ValueError('Group member conditions require ' + expected + ': ' + code)

    for code, row in registry.items():
        if 'group' in row and memberships.get(code) != row['group']:
            raise ValueError('Registry group row has no entry: ' + code)
    return {group: dict(members={code: dict(sorted(sites.items()))
                                for code, sites in sorted(entry['members'].items())})
            for group, entry in sorted(merged.items())}
