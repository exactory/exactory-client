"""Recompute numeric route priors from read-only records and the corrected measurements."""
import argparse
import hashlib
import json
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
COMPLETION_OPERATIONS = (
    ('review_attempt', 'review.invoke'),
    ('review_route_probe', 'review.route_probe'),
    ('review_output_correction', 'review.output_correction'),
)


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


def _describe_probe_sample(row, characters, carried=0, *, date=capacity.MEASUREMENT_DATE, count_carried_state=False):
    complete = _is_completed_generation(row)
    material = row['delivered_characters']
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
    return [_describe_probe_sample(
        row,
        row.get('text_characters') or sizes.get(row.get('arm')) or row['request_characters'],
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
    for row in rows:
        carried = max(0, row['text_characters'] - row['base_text_characters']) if row['part'] == 'record' else 0
        characters = row['text_characters'] if row['part'] == 'lists' else row['base_text_characters']
        item = dict(
            row,
            thinking_truncated=row.get('thinking_truncated', False),
            answer_characters=row.get('answer_characters', 0),
        )
        samples.append(
            _describe_probe_sample(item, characters, carried, date=date, count_carried_state=False),
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
