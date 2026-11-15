"""Recompute numeric route priors from read-only records and the corrected measurements."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_harness import review_route_capacity as capacity
from research_harness import review_excerpts
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
# Native wire text from the source measurement dry run, dated 2026-11-13.
PROBE_REQUEST_CHARACTERS = (
    ('W', 173774),
    ('H1', 105584),
    ('H2', 106804),
    ('Q1', 71984),
    ('Q2', 73774),
    ('Q3', 74741),
    ('Q4', 72237),
)
PROBE_FRAME_CHARACTERS = 38614
# Native body deltas from the copied source dry run, measured 2026-11-14.
# Runtime frames retain the evidence descriptors; the calibration frame above does not.
PROBE_MATERIAL_CHARACTERS = (
    ('W', 136190),
    ('H1', 67484),
    ('H2', 68706),
    ('Q1', 32846),
    ('Q2', 34638),
    ('Q3', 35605),
    ('Q4', 33101),
)
COMPLETION_OPERATIONS = (
    ('review_attempt', 'review.invoke'),
    ('review_route_probe', 'review.route_probe'),
    ('review_output_correction', 'review.output_correction'),
)


def calculate_upper_share(calls, over):
    """Invert the binomial CDF for the one-sided exact 95% upper bound."""
    if not calls or over == calls:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(64):
        share = (low + high) / 2
        terms = [math.exp(math.lgamma(calls + 1) - math.lgamma(index + 1)
                          - math.lgamma(calls - index + 1)
                          + index * math.log(share) + (calls - index) * math.log1p(-share))
                 for index in range(over + 1)]
        if sum(terms) > 0.05:
            low = share
        else:
            high = share
    return (low + high) / 2


def _describe_verification_attempt(records, artifacts, attempt, plan):
    from research_harness.review_failure_class import classify_attempt
    assignment = records['review_assignment'][attempt['assignment_id']]
    route = records['review_route'][assignment['route_id']]
    classification = (classify_attempt(records, artifacts, attempt)
                      if attempt.get('response') or attempt['status'] != 'completed' else None)
    tokens = (attempt.get('usage') or {}).get('output_tokens')
    limit = capacity.describe_route_capacity(route)['output_tokens']['value']
    excluded = (classification or {}).get('signal', {}).get('detail') == 'non_streaming_fallback'
    over = not excluded and ((classification or {}).get('outcome') == 'output_over_cap' or (
        type(tokens) is int and tokens >= limit))
    generated = bool(attempt.get('completed_generation')) or over
    task = attempt.get('task', {})
    level = task.get('material_allowance')
    if level is None and type(task.get('material_characters')) is int:
        start = plan.get('task', {}).get(task.get('kind'), {}).get('material_allowance')
        level = next((value for value in reversed(capacity.describe_material_levels(task.get('kind')))
                      if task['material_characters'] <= value and start is not None and value <= start), None)
    return dict(id=attempt['id'], assignment_id=assignment['id'],
        position=assignment.get('context', {}).get('prior_assignment_id'),
        level=level, kind=task.get('kind'), tokens=tokens,
        outcome=(classification or {}).get('outcome', 'completed' if generated else attempt['status']),
        effort=route['configuration'].get('effort'), output_limit=limit,
        route_id=route['id'],
        generated=generated, at_or_over_limit=over, recorded=attempt.get('output') is not None)


def _assess_chain(records, artifacts, members, attempts):
    first = min(members, key=lambda row: row['recorded_revision'])
    identifiers = {row['id'] for row in members}
    reference = first.get('capacity_plan')
    plan = json.loads(artifacts.read(reference['artifact'])) if reference else {}
    ordered = sorted((row for row in attempts if row['assignment_id'] in identifiers),
                     key=lambda row: (row['recorded_revision'], row['id']))
    rows = [_describe_verification_attempt(records, artifacts, row, plan) for row in ordered]
    for row in rows:
        row['position'] = 'after:' + row['position'] if row['position'] else 'initial:' + first['id']
    calls = sum(row['generated'] for row in rows)
    over = [row for row in rows if row['at_or_over_limit']]
    positions = {}
    reasons = []
    for row in over:
        positions.setdefault(row['position'], []).append(row['id'])
        following = next((other for other in rows[rows.index(row) + 1:]
                          if other['position'] == row['position']), None)
        levels = capacity.describe_material_levels(row['kind'])
        lower = next((level for level in levels if type(row['level']) is int and level < row['level']), None)
        valid = following is not None and following['effort'] == row['effort'] and (
            following['assignment_id'] != row['assignment_id'] and following['level'] == lower if lower is not None else
            following['assignment_id'] == row['assignment_id'] and row['level'] == levels[-1])
        if not valid:
            reasons.append('replacement_missing_or_not_smaller_at_same_effort')
    bound = (calls + capacity.VERIFICATION_CALLS_PER_OVER_LIMIT - 1) // capacity.VERIFICATION_CALLS_PER_OVER_LIMIT
    if len(over) > bound:
        reasons.append('chain_output_share_above_bound')
    if any(len(values) > capacity.VERIFICATION_POSITION_OVER_LIMIT for values in positions.values()):
        reasons.append('position_output_count_above_bound')
    if any(row['recorded'] and (row['tokens'] is None or
            row['tokens'] >= capacity.VERIFICATION_RECORDED_OUTPUT_LIMIT) for row in rows):
        reasons.append('recorded_answer_without_tokens_below_limit')
    stops = [row for row in records.get('review_series_stop', {}).values()
             if row.get('assignment_id') in identifiers and row.get('code') in capacity.VERIFICATION_SIZE_STOP_CODES]
    if stops:
        reasons.append('size_decision_stop')
    if any(row.get('assignment_id') in identifiers and row.get('source') == 'owner'
           for row in records.get('review_context_event', {}).values()):
        reasons.append('owner_step')
    packets = [json.loads(artifacts.read(member['packet'])) for member in members]
    if any(packet.get('source_inventory', {}).get('protocol') != 'exactory-independent-review-sources-v4'
           for packet in packets):
        reasons.append('session_rules_are_not_v4')
    if any(records['review_route'][member['route_id']]['configuration'].get('effort') != 'max'
           for member in members):
        reasons.append('effort_changed')
    if any(member['route_id'] != first['route_id'] for member in members):
        reasons.append('chain_route_changed')
    if (not reference or reference.get('digest') != capacity.digest(plan)
            or plan.get('policy') != capacity.POLICY or not plan.get('profile')
            or any(member.get('capacity_plan') != reference for member in members)):
        reasons.append('stored_capacity_plan_missing_or_changed')
    initial_profile = plan.get('profile', {})
    if any(not isinstance(initial_profile.get(kind), dict) or
           not {'state', 'share_at_or_over_limit'}.issubset(initial_profile[kind])
           for kind in {row['kind'] for row in rows}):
        reasons.append('initial_profile_missing_kind')
    recorded = any(row.get('assignment_id') in identifiers
                   for row in records.get('value_review', {}).values())
    return dict(id=first['id'], route_id=first['route_id'], calls=calls, bound=bound, at_or_over_limit=len(over),
        positions=positions, attempts=rows, initial_profile=plan.get('profile'),
        size_stops=stops, reasons=sorted(set(reasons)),
        result='fail' if reasons else 'pass' if recorded else 'incomplete')


def assess_verification_chain(records, artifacts, declaration):
    """Assess the predeclared run from a clone snapshot, including unfinished starts.

    declaration names clone, route_id, max_calls and its recorded_revision before
    the first assignment. It is retained by the supervisor before the paid run.
    No caller-supplied successful-chain list can filter the snapshot.
    """
    from research_harness.review_failure_class import list_turn_assignments
    assignments = records.get('review_assignment', {})
    roots, groups = {}, {}
    for row in sorted(assignments.values(), key=lambda item: (item['recorded_revision'], item['id'])):
        parent = row.get('context', {}).get('prior_assignment_id')
        if parent in roots:
            root = roots[parent]
        elif parent is not None or row['role'] != 'bar':
            continue
        elif (row['recorded_revision'] <= declaration['recorded_revision'] or
                records['review_route'][row['route_id']]['configuration'].get('effort') != 'max'):
            continue
        else:
            root = next((key for key in list_turn_assignments(records, row) if key in roots), row['id'])
            root = roots.get(root, root)
        roots[row['id']] = root
        groups.setdefault(root, []).append(row)
    chains = [_assess_chain(records, artifacts, members, records.get('review_attempt', {}).values())
              for members in groups.values()]
    calls = sum(chain['calls'] for chain in chains)
    over = sum(chain['at_or_over_limit'] for chain in chains)
    bound = (calls + capacity.VERIFICATION_CALLS_PER_OVER_LIMIT - 1) // capacity.VERIFICATION_CALLS_PER_OVER_LIMIT
    reasons = []
    if not declaration.get('clone') or not chains:
        reasons.append('declaration_has_no_started_chain')
    if calls > declaration['max_calls']:
        reasons.append('declared_call_budget_exceeded')
    if any(chain['route_id'] != declaration['route_id'] for chain in chains):
        reasons.append('verification_route_changed')
    if over > bound:
        reasons.append('verification_output_share_above_bound')
    failed = reasons or any(chain['result'] == 'fail' for chain in chains)
    complete = calls >= capacity.VERIFICATION_MINIMUM_CALLS and all(chain['result'] == 'pass' for chain in chains)
    return dict(policy=capacity.POLICY, declaration=dict(declaration),
        result='fail' if failed else 'pass' if complete else 'incomplete',
        calls=calls, bound=bound, at_or_over_limit=over, chains=chains, reasons=reasons,
        upper_share_95=calculate_upper_share(calls, over), assumes_independent_attempts=True)


def assess_release_output_share(samples, *, since=None):
    """Expose AR22.3's ceiling for K10, grouped by route class and turn kind."""
    groups = {}
    for sample in samples:
        if not capacity.qualifies_for_output_profile(sample):
            continue
        if since is not None and (not sample.get('observed_at') or
                datetime.fromisoformat(sample['observed_at'].replace('Z', '+00:00')) <
                datetime.fromisoformat(since.replace('Z', '+00:00'))):
            continue
        route_class = sample['route_class']
        key = tuple(route_class.get(name) for name in ('adapter', 'model', 'effort')) + (sample['task']['kind'],)
        row = groups.setdefault(key, dict(route_class=route_class, kind=key[-1], calls=0, at_or_over_limit=0))
        tokens = sample['usage'].get('output_tokens')
        over = sample.get('outcome') == 'output_over_cap' or type(tokens) is int and tokens >= sample['output_limit']
        row['calls'] += int(sample.get('completed_generation', False) or over)
        row['at_or_over_limit'] += int(over)
    for row in groups.values():
        row['result'] = ('incomplete' if row['calls'] < capacity.VERIFICATION_MINIMUM_CALLS else
                         'fail' if row['at_or_over_limit'] * capacity.VERIFICATION_CALLS_PER_OVER_LIMIT > row['calls'] else 'pass')
    return list(groups.values())


def read_root_paths(path):
    return [Path(line[2:].split(' (', 1)[0].strip()) for line in path.read_text(encoding='utf-8').splitlines() if line.startswith('- /')]


def read_store_samples(root, *, count_carried_state=False):
    """Read current completion records and their frozen requests; no store writer runs."""
    database = root / '.exactory' / 'research.sqlite3'
    if not database.is_file():
        return []
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:

        def read_records(kind):
            return {key: json.loads(value) for key, value in connection.execute('select key,value from records where kind=?', (kind,))}
        routes = read_records('review_route')
        assignments = read_records('review_assignment')
        groups = {kind: read_records(kind) for kind, _ in COMPLETION_OPERATIONS}
    finally:
        connection.close()
    artifacts = ArtifactStore(root)
    samples = []
    for kind, operation in COMPLETION_OPERATIONS:
        for attempt in groups[kind].values():
            assignment = assignments.get(attempt.get('assignment_id'), {})
            route = routes.get(attempt.get('route_id') or assignment.get('route_id'))
            if route is None or not attempt.get('request'):
                continue
            if operation != 'review.route_probe' and attempt.get('origin') != 'instrumented':
                continue
            request = json.loads(artifacts.read(attempt['request']))
            response = json.loads(artifacts.read(attempt['response'])) if attempt.get('response') else {}
            packet = review_excerpts.read_sent_packet(request)
            if packet is None:
                packet = json.loads(artifacts.read(assignment['packet'])) if assignment.get('packet') else attempt.get('positive_controls', {})
            try:
                measured = capacity.describe_attempt(
                    route,
                    assignment.get('role', 'probe'),
                    packet,
                    request,
                    response,
                    operation=operation,
                    observed_at=attempt.get('observed_at'),
                    attempt_error=attempt.get('error'),
                    count_carried_state=count_carried_state,
                )
            except ResearchError as error:
                if error.code != 'review_format_unsupported':
                    raise
                measured = dict(
                    task=dict(
                        kind=capacity.describe_task_kind(assignment.get('role', 'probe'), packet, operation),
                        characters=None,
                        frame_characters=None,
                        material_characters=None,
                        carried_characters=None,
                        measurement_error=error.code,
                    ),
                    usage=dict(
                        input_tokens=None,
                        output_tokens=None,
                        thinking_tokens=None,
                        thinking_method=None,
                        sends=None,
                        request_characters=None,
                    ),
                    answer=dict(characters=None, tokens=None, bound=None),
                    completed_generation=False,
                    cli_version=None,
                    observed_at=attempt.get('observed_at'),
                    verified_input_tokens=None,
                )
            measured['cli_version'] = measured['cli_version'] or attempt.get('cli_version') or (attempt.get('runtime') or {}).get('version')
            measured['usage'] = dict(
                measured['usage'],
                **{key: value for key, value in (attempt.get('usage') or {}).items() if key not in measured['usage'] or measured['usage'][key] is None},
            )
            samples.append(
                dict(
                    measured,
                    route_class=capacity.describe_route_class(route),
                    output_limit=capacity.describe_route_capacity(route)['output_tokens']['value'],
                ),
            )
    return samples


def read_summary_rows(path):
    if path is None or not path.is_file():
        return []
    return parse_summary_rows(path.read_bytes())


def parse_summary_rows(raw):
    return [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]


def _is_completed_generation(row):
    return row['status'] == 'completed' and row.get('assistant_events', 2) == 2 and (row.get('user_events', 1) == 1)


def _has_thinking_estimate(row):
    return row.get('thinking_tokens') is not None and (not row.get('thinking_truncated', False))


def _describe_probe_sample(row, characters, carried=0, *, material_characters, date=capacity.MEASUREMENT_DATE, count_carried_state=False):
    complete = _is_completed_generation(row)
    material = material_characters
    return dict(
        route_class=dict(adapter='claude_cli_v1', model='claude-opus-5-5', effort=row['effort']),
        task=dict(
            kind='reading',
            characters=characters,
            frame_characters=max(0, characters - material - (carried if count_carried_state else 0)),
            material_characters=material,
            carried_characters=carried,
            targets=0,
            count_carried_state=count_carried_state,
        ),
        usage=dict(
            thinking_tokens=None if row['thinking_truncated'] else row['thinking_tokens'],
            output_tokens=row['output_tokens'],
            sends=row['user_events'] + row.get('api_retries', 0),
        ),
        answer=dict(characters=row['answer_characters'], tokens=None, bound=None),
        output_limit=128000,
        completed_generation=complete,
        cli_version=row.get('cli_version'),
        observed_at=row.get('observed_at') or date,
    )


def read_probe_samples(summary, *, date=capacity.MEASUREMENT_DATE):
    sizes = dict(PROBE_REQUEST_CHARACTERS)
    materials = dict(PROBE_MATERIAL_CHARACTERS)
    return [_describe_probe_sample(
        row,
        row.get('text_characters') or sizes.get(row.get('arm')) or row['request_characters'],
        material_characters=materials[row['arm']],
        date=date,
    ) for row in read_summary_rows(summary)]


def read_frame_probe_samples(summary, base_rows, *, date=capacity.MEASUREMENT_DATE):
    """Read all available calls, preserving inconclusive and failed observations."""
    raw = summary.read_bytes() if summary is not None and summary.is_file() else None
    rows = parse_summary_rows(raw) if raw is not None else []
    evidence = []
    for effort in ('max', 'xhigh'):
        base = [row for row in base_rows if row['effort'] == effort and row.get('size') == 'quarter' and _is_completed_generation(row) and _has_thinking_estimate(row)]
        base_thinking = max((row['thinking_tokens'] for row in base)) if base else None
        planned = 4 if effort == 'max' else 2
        minimum = 3 if effort == 'max' else 2
        for part in ('lists', 'record'):
            calls = [row for row in rows if row['effort'] == effort and row['part'] == part]
            valid = [row for row in calls if _is_completed_generation(row) and _has_thinking_estimate(row) or (row.get('output_tokens') or 0) >= 128000]
            quarters = sorted({row['arm'].rsplit('-', 1)[-1] for row in valid})
            effect = any(((row.get('output_tokens') or 0) >= 128000 for row in valid)) or (base_thinking is not None and any(((row.get('thinking_tokens') or 0) * 100 > base_thinking * 115 for row in valid)))
            complete = base_thinking is not None and len(calls) >= planned and (len(valid) >= minimum) and {'Q1', 'Q3'}.issubset(quarters)
            reading = 'effect' if effect else 'no effect' if complete else 'inconclusive'
            evidence.append(
                dict(
                    effort=effort,
                    part=part,
                    calls=len(calls),
                    valid_observations=len(valid),
                    quarters=quarters,
                    calls_left=max(0, planned - len(calls)),
                    reading=reading,
                    largest_text_characters=max((row['text_characters'] for row in valid), default=0),
                    largest_material_characters=max((row['base_text_characters'] - PROBE_FRAME_CHARACTERS for row in valid), default=0),
                    frame_limit=None,
                ),
            )
    samples = []
    materials = dict(PROBE_MATERIAL_CHARACTERS)
    for row in rows:
        carried = max(0, row['text_characters'] - row['base_text_characters']) if row['part'] == 'record' else 0
        characters = row['text_characters'] if row['part'] == 'lists' else row['base_text_characters']
        item = dict(
            row,
            thinking_truncated=row.get('thinking_truncated', False),
            answer_characters=row.get('answer_characters', 0),
        )
        samples.append(
            _describe_probe_sample(item, characters, carried,
                                   material_characters=materials[row['arm'].rsplit('-', 1)[-1]],
                                   date=date, count_carried_state=False),
        )
    summary_digest = hashlib.sha256(raw).hexdigest() if raw is not None else None
    return (samples, dict(count_carried_state=True, evidence=evidence, summary_sha256=summary_digest))


def build_prior_rows(samples, *, source, date):
    groups = {}
    for sample in samples:
        key = tuple((sample['route_class'][name] for name in ('adapter', 'model', 'effort')))
        groups.setdefault(key, []).append(dict(sample, source=source))
    rows = []
    for key, values in sorted(groups.items(), key=lambda item: str(item[0])):
        for row in capacity.build_profile_rows(values):
            rows.append(
                dict(
                    row,
                    route_class=dict(zip(('adapter', 'model', 'effort'), key)),
                    source=source,
                    source_record=capacity.MEASUREMENT_SOURCE,
                    date=date,
                    thinking_method='cli_estimate' if key[0] == 'claude_cli_v1' else 'provider_usage',
                ),
            )
    return rows


RELEASE_EVIDENCE_MESSAGE = 'The capacity release check is blocked. Resolve the reported cause and retain its coverage evidence before release.'


def assess_release_records(records, artifacts, *, matrix=None, inventory=None, check_plans=True, check_share=True, since=None):
    """K10 inspects every retained attempt, independent of its classified outcome.

    This is a release audit, not admission or a migration. No stored record,
    prompt, request, packet, profile or route identity is rewritten.
    """
    from ci import capacity_guard as guard
    from ci.capacity_packet_inventory import read_packet_registry, validate_packet
    from research_harness import review_failure_class as failures, stop_classes
    from research_harness.review_continuation import list_chain_heads, plan_next
    matrix = guard.read_matrix() if matrix is None else matrix
    inventory = guard.read_transport_inventory() if inventory is None else inventory
    registry = read_packet_registry()
    entries = {row['id']: row for row in matrix['entries']}
    blocks, observations, samples = [], [], []

    def block(reason, identifier, **details):
        blocks.append(dict(reason=reason, id=identifier, message=RELEASE_EVIDENCE_MESSAGE, **details))

    assignments = records.get('review_assignment', {})
    for assignment in assignments.values():
        if assignment.get('packet') is None:
            continue
        packet = json.loads(artifacts.read(assignment['packet']))
        errors = validate_packet(assignment['role'], packet, registry)
        if errors:
            block('undeclared_packet_member', assignment['id'], errors=errors)
    for record_kind, operation in COMPLETION_OPERATIONS:
        for attempt in records.get(record_kind, {}).values():
            assignment = assignments.get(attempt.get('assignment_id'), {})
            route = records.get('review_route', {}).get(attempt.get('route_id') or assignment.get('route_id'))
            if route is None:
                block('attempt_without_route', attempt['id'])
                continue
            adapter = inventory['adapters'].get(route['adapter'])
            if adapter is None:
                block('unlisted_adapter', attempt['id'])
                continue
            response, answer = failures.read_attempt_evidence(artifacts, attempt)
            classification = (failures.classify_attempt(records, artifacts, attempt)
                if record_kind == 'review_attempt' else failures.find_capacity_signal(route, attempt, response, answer))
            outcome = classification['outcome'] if classification else None
            retained = failures.find_capacity_signal(route, attempt, response, answer)
            code = (attempt.get('error') or {}).get('code')
            known = {key for row in inventory['adapters'].values() for key in row['signals']['codes']}
            known.update(failures.OUTCOME_CODES.values())
            if code and code not in known:
                block('unlisted_transport_code', attempt['id'], code=code)
            issues = list(response.get('context_evidence', {}).get('issues', []))
            if route['adapter'] == 'claude_cli_v1' and response.get('execution', {}).get('system_events') is not None:
                from research_harness.review_claude_transport import inspect_retained_system_events
                try:
                    inspected = inspect_retained_system_events(response)
                except ResearchError as error:
                    block('unreadable_context_events', attempt['id'], code=error.code)
                else:
                    issues = sorted(set(issues) | set(inspected['issues']))
                    if inspected['truncated']:
                        block('truncated_context_events', attempt['id'])
            issue_signals = [row for row in inventory.get('recognized_evidence', [])
                if row['adapter'] == route['adapter'] and row['field'] == 'context_issue'
                and any(issue == row['prefix'] or row['prefix'].endswith('*')
                    and issue.startswith(row['prefix'][:-1]) for issue in issues)]
            for issue in issues:
                matches = [name for name in adapter['signals']['issues'] if issue == name or
                    name.endswith('*') and issue.startswith(name[:-1])]
                if not matches and not any(row['prefix'] == issue for row in issue_signals):
                    block('unlisted_context_issue', attempt['id'], issue=issue)
                if issue.startswith('system_event_') and issue != 'system_event_compact_boundary':
                    block('unverified_context_event', attempt['id'], issue=issue)
            usage = dict(response.get('usage') or {})
            usage.update({key: value for key, value in (attempt.get('usage') or {}).items() if value is not None})
            output_limit = capacity.describe_route_capacity(route)['output_tokens']['value']
            raw_compaction = next((row for row in issue_signals if row['detail'] == 'compacted'), None)
            size_outcomes = ('output_over_cap', 'input_over_limit', 'response_over_limit', 'timeout')
            # A non-size classifier signal cannot erase independent size evidence.
            # The transport's non-2xx response-size precedence remains in retained.
            size_outcome = raw_compaction['outcome'] if raw_compaction else (
                retained['outcome'] if retained and retained['outcome'] in size_outcomes else
                'output_over_cap' if type(usage.get('output_tokens')) is int and usage['output_tokens'] >= output_limit else
                'input_over_limit' if code == 'review_input_too_large' else
                'timeout' if attempt.get('status') == 'timed_out' or response.get('execution', {}).get('timeout') else None)
            entry = ('A26' if size_outcome == 'response_over_limit' else
                'A27' if size_outcome == 'input_over_limit' and (raw_compaction is not None or
                    retained and retained['signal'].get('detail') == 'compacted')
                    and route['adapter'] == 'claude_cli_v1' else
                'A20' if size_outcome == 'input_over_limit' and route['adapter'].startswith('codex_') else
                'A9' if size_outcome == 'input_over_limit' else
                'A21' if size_outcome == 'timeout' else None)
            request = json.loads(artifacts.read(attempt['request'])) if attempt.get('request') else None
            packet = review_excerpts.read_sent_packet(request) if request else None
            if packet is not None:
                packet_role = {'review.route_probe': 'probe', 'review.output_correction': 'correction'}.get(
                    operation, assignment.get('role', 'probe'))
                errors = validate_packet(packet_role, packet, registry)
                if errors:
                    block('undeclared_emitted_packet_member', attempt['id'], errors=errors)
            if packet is None:
                packet = json.loads(artifacts.read(assignment['packet'])) if assignment.get('packet') else {}
            kind = capacity.describe_task_kind(assignment.get('role', 'probe'), packet, operation)
            if size_outcome == 'output_over_cap':
                entry = dict(request='A12', reading='A13', final='A14', read='A15', combine='A15',
                    check='A15', single='A16', staged='A10c', correction='A11', probe='A24').get(kind)
            if size_outcome in size_outcomes:
                if outcome is not None and outcome not in size_outcomes:
                    block('size_evidence_without_size_outcome', attempt['id'], outcome=outcome, entry=entry)
                if entry not in entries or kind not in entries[entry]['kinds']:
                    block('uncovered_size_attempt', attempt['id'], kind=kind, entry=entry)
                if not entries.get(entry, {}).get('tests') or not any(entries.get(entry, {}).get('units', {}).values()):
                    block('size_attempt_without_unit_or_test', attempt['id'], entry=entry)
            if request is not None:
                measure = capacity.measure_request(route, request, records=records, artifacts=artifacts)
                if size_outcome == 'input_over_limit' and measure['fits']:
                    block('input_limit_inside_measure', attempt['id'], measure=measure)
                reported_input = usage.get('input_tokens')
                if measure['tokens_upper'] is None:
                    block(measure['reason'], attempt['id'], measure=measure, entry='A10b')
                elif type(reported_input) is int and reported_input > measure['tokens_upper']:
                    block('token_measure_below_usage', attempt['id'], measure=measure, input_tokens=reported_input)
                sample = capacity.describe_attempt(route, assignment.get('role', 'probe'), packet, request, response,
                    operation=operation, attempt_error=attempt.get('error'), observed_at=attempt.get('observed_at'))
                samples.append(dict(sample, route_class=capacity.describe_route_class(route), output_limit=output_limit))
            observations.append(dict(id=attempt['id'], operation=operation, outcome=outcome,
                retained_outcome=size_outcome, kind=kind, entry=entry, code=code, issues=issues))
    if check_plans:
        for identifier in list_chain_heads(records, artifacts):
            assignment = assignments[identifier]
            plan = plan_next(records, artifacts, identifier)
            if plan['state'] not in ('run', 'resume', 'continue', 'record', 'wait', 'handoff', 'stop', 'done'):
                block('unlisted_plan_form', assignment['id'], state=plan['state'])
            if plan.get('code') in capacity.VERIFICATION_SIZE_STOP_CODES and plan['code'] not in stop_classes.STOP_REGISTRY:
                block('size_stop_without_registry', assignment['id'], code=plan['code'])
    shares = assess_release_output_share(samples, since=since)
    for row in shares:
        if check_share and row['result'] == 'fail':
            block('output_share_above_ceiling', row['kind'], share=row)
    return dict(result='fail' if blocks else 'pass', attempts=observations, blocks=blocks, shares=shares)


def read_root_capacity_check(root, *, since=None):
    """Read a live root without ignoring committed records in its WAL."""
    database = root / '.exactory/research.sqlite3'
    if not database.is_file():
        return dict(result='fail', attempts=[], shares=[], blocks=[dict(
            reason='store_missing', message=RELEASE_EVIDENCE_MESSAGE)])
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            records = {}
            for kind, key, value in connection.execute('select kind,key,value from records'):
                records.setdefault(kind, {})[key] = json.loads(value)
        finally:
            connection.close()
        return assess_release_records(records, ArtifactStore(root), check_share=False, since=since)
    except (ResearchError, ValueError, sqlite3.DatabaseError) as error:
        return dict(result='fail', attempts=[], shares=[], blocks=[dict(reason='audit_input_unreadable',
            code=getattr(error, 'code', type(error).__name__), message=RELEASE_EVIDENCE_MESSAGE)])


def assess_release_roots(checks):
    """Apply the class/kind ceiling to all audited roots together."""
    groups = {}
    for check in checks:
        for share in check['shares']:
            route_class = share['route_class']
            key = tuple(route_class.get(name) for name in ('adapter', 'model', 'effort')) + (share['kind'],)
            row = groups.setdefault(key, dict(route_class=route_class, kind=share['kind'], calls=0, at_or_over_limit=0))
            row['calls'] += share['calls']
            row['at_or_over_limit'] += share['at_or_over_limit']
    blocks = []
    for row in groups.values():
        row['result'] = ('incomplete' if row['calls'] < capacity.VERIFICATION_MINIMUM_CALLS else
            'fail' if row['at_or_over_limit'] * capacity.VERIFICATION_CALLS_PER_OVER_LIMIT > row['calls'] else 'pass')
        if row['result'] == 'fail':
            blocks.append(dict(reason='output_share_above_ceiling', share=row, message=RELEASE_EVIDENCE_MESSAGE))
    if not checks:
        blocks.append(dict(reason='roots_missing', message=RELEASE_EVIDENCE_MESSAGE))
    return dict(protocol='exactory-capacity-release-check-v1', roots=checks, shares=list(groups.values()),
        blocks=blocks, result='fail' if blocks or any(check['result'] == 'fail' for check in checks) else 'pass')


def run_cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roots-file', type=Path, required=True)
    parser.add_argument('--probe-summary', type=Path)
    parser.add_argument('--frame-summary', type=Path)
    parser.add_argument('--date', default=capacity.MEASUREMENT_DATE)
    parser.add_argument('--check-closure', action='store_true')
    parser.add_argument('--since')
    args = parser.parse_args(argv)
    if args.check_closure:
        checks = [read_root_capacity_check(root, since=args.since) for root in read_root_paths(args.roots_file)]
        report = assess_release_roots(checks)
        print(json.dumps(report, indent=2, sort_keys=True))
        return int(report['result'] == 'fail')
    frame_samples, frame_policy = read_frame_probe_samples(
        args.frame_summary,
        read_summary_rows(args.probe_summary),
        date=args.date,
    )
    rows = build_prior_rows(
        (sample for root in read_root_paths(args.roots_file) for sample in read_store_samples(root, count_carried_state=False)),
        source='live-records',
        date=args.date,
    )
    if args.probe_summary is not None:
        rows += build_prior_rows(
            read_probe_samples(args.probe_summary, date=args.date),
            source='probe',
            date=args.date,
        )
    rows += build_prior_rows(frame_samples, source='probe-frame', date=args.date)
    print(
        json.dumps(
            dict(protocol=capacity.PROFILE_PROTOCOL, rows=rows, frame_policy=frame_policy),
            sort_keys=True,
            indent=2,
        ),
    )
    return 0
if __name__ == '__main__':
    raise SystemExit(run_cli())
