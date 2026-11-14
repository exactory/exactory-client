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


def describe_refusal_replacements(samples):
    """Report measured notice-bearing replacements separately for the release check (F14)."""
    return [dict(route_class=sample['route_class'], task=sample['task'],
                 usage=sample['usage'], completed_generation=sample['completed_generation'],
                 **{key: sample[key] for key in ('attempt_id', 'assignment_id') if key in sample})
            for sample in samples if 'notice_characters' in sample.get('task', {})]


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
            if 'notice_characters' in measured['task']:
                measured.update(attempt_id=attempt['id'], assignment_id=assignment['id'])
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


def run_cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roots-file', type=Path, required=True)
    parser.add_argument('--probe-summary', type=Path)
    parser.add_argument('--frame-summary', type=Path)
    parser.add_argument('--date', default=capacity.MEASUREMENT_DATE)
    args = parser.parse_args(argv)
    frame_samples, frame_policy = read_frame_probe_samples(
        args.frame_summary,
        read_summary_rows(args.probe_summary),
        date=args.date,
    )
    samples = [sample for root in read_root_paths(args.roots_file)
               for sample in read_store_samples(root, count_carried_state=False)]
    rows = build_prior_rows(
        samples,
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
            dict(protocol=capacity.PROFILE_PROTOCOL, rows=rows, frame_policy=frame_policy,
                 refusal_replacements=describe_refusal_replacements(samples)),
            sort_keys=True,
            indent=2,
        ),
    )
    return 0
if __name__ == '__main__':
    raise SystemExit(run_cli())
