"""Read-only evidence guard for groups of reachable stop codes."""

import json
from pathlib import Path


def validate_no_series_ending(report, members, edges):
    """Repeat the entry-flow and graph-edge checks for codes without an ending."""
    series = set(report.get('by_command_functions', {}).get('run_series', ()))
    if 'run_series' not in report.get('by_command_functions', {}):
        series = set().union(*(set(keys) for keys in report.get('by_command_functions', {}).values()))
    passed = set(report.get('entry_codes_passed', ()))
    expected_edges = {}
    for code, sites in members.items():
        for site, condition in sites.items():
            if condition == 'entry':
                if code in passed or report.get('terminal_sites', {}).get(code):
                    raise ValueError('A no_series_ending entry can end a series: ' + code)
            elif condition == 'no_call':
                expected_edges.setdefault(code, set()).add(site)
    if set(edges) != set(expected_edges):
        raise ValueError('no_series_ending edges must name exactly the no_call members')
    for code, sites in expected_edges.items():
        if not isinstance(edges[code], dict) or set(edges[code]) != sites:
            raise ValueError('no_series_ending edges must name every no_call site: ' + code)
        for site, edge in edges[code].items():
            if (not isinstance(edge, dict) or set(edge) != {'caller', 'expression'}
                    or not all(isinstance(value, str) and value for value in edge.values())):
                raise ValueError('A no_call edge needs its caller and expression: ' + code + ': ' + site)
            caller = edge['caller']
            targets = (set(report.get('reference_edges', {}).get(caller, {}).get(edge['expression'], ()))
                       & set(report['graph'].get(caller, ())) & series)
            if caller not in series or not targets:
                raise ValueError('A no_call edge is absent from the series graph: ' + code + ': ' + site)
            # The named edge may lead to a helper that reaches the writing site.
            from ci.series_code_reach import collect_closure
            if site not in collect_closure(set(targets), report['graph'], series):
                raise ValueError('A no_call edge no longer reaches its writing site: ' + code + ': ' + site)


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
            fields = {'members', 'edges'} if group == 'no_series_ending' else {'members'}
            if (not isinstance(entry, dict) or set(entry) != fields
                    or not isinstance(entry['members'], dict)):
                raise ValueError('Group evidence needs only a members table'
                                 + (' and an edges table' if group == 'no_series_ending' else '')
                                 + ': ' + str(path) + ': ' + group)
            combined = merged.setdefault(group, dict(members={}))
            if group == 'no_series_ending':
                if not isinstance(entry['edges'], dict):
                    raise ValueError('Group edges must be a table: ' + str(path))
                combined.setdefault('edges', {})
                for code, edges in entry['edges'].items():
                    if code in combined['edges'] and combined['edges'][code] != edges:
                        raise ValueError('Conflicting no_call edges: ' + code)
                    combined['edges'][code] = edges
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
            if group == 'no_series_ending':
                if code in registry:
                    raise ValueError('A no_series_ending member must have no registry row: ' + code)
            elif registry.get(code, {}).get('group') != group:
                raise ValueError('Group entry has no matching registry row: ' + group + ': ' + code)
            if code in memberships and memberships[code] != group:
                raise ValueError('A code belongs to more than one stop group: ' + code)
            memberships[code] = group
            actual = set(report.get('code_sites', {}).get(code, ()))
            if not actual or set(sites) != actual:
                raise ValueError('Group member sites differ: ' + code + ': expected ' + str(sorted(actual))
                                 + ', found ' + str(sorted(sites)))
            for condition in sites.values():
                if condition not in conditions:
                    raise ValueError('Unknown group condition at member site: ' + group + ': ' + condition)
        if group == 'no_series_ending':
            validate_no_series_ending(report, entry['members'], entry['edges'])

    for code, row in registry.items():
        if 'group' in row and memberships.get(code) != row['group']:
            raise ValueError('Registry group row has no entry: ' + code)
    result = {}
    for group, entry in sorted(merged.items()):
        result[group] = dict(members={code: dict(sorted(sites.items()))
                                     for code, sites in sorted(entry['members'].items())})
        if 'edges' in entry:
            result[group]['edges'] = {code: dict(sorted(sites.items())) for code, sites in sorted(entry['edges'].items())}
    return result
