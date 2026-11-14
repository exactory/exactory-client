"""Read-only evidence guard for groups of reachable stop codes."""

import json
from pathlib import Path


def read_group_entries(root, report, registry, groups):
    """Read and validate the reviewed group evidence from all lane files."""
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
                if not isinstance(sites, dict) or not all(isinstance(value, str) for value in sites.values()):
                    raise ValueError('Group member needs a site-to-condition table: ' + code)
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
            if set(sites) != actual:
                raise ValueError('Group member sites differ: ' + code + ': expected ' + str(sorted(actual))
                                 + ', found ' + str(sorted(sites)))
            for condition in sites.values():
                if condition not in conditions:
                    raise ValueError('Unknown group condition at member site: ' + group + ': ' + condition)

    for code, row in registry.items():
        if 'group' in row and memberships.get(code) != row['group']:
            raise ValueError('Registry group row has no entry: ' + code)
    return {group: dict(
        members={code: dict(sorted(sites.items())) for code, sites in sorted(entry['members'].items())})
        for group, entry in sorted(merged.items())}
