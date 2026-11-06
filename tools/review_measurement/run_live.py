"""Measure the real review-series driver on a qualified disposable prefix."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
from unittest.mock import patch

sys.dont_write_bytecode = True
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research_harness import cli, record_check, review_series, review_transport, review_worker
from research_harness.errors import ResearchError
from research_harness.storage import Store, _canonical, _digest
from tools.review_measurement.campaign import (
    PaidBudget, confine_temporary_files, open_directory, write_json,
)


def require_safe_paths(source, output):
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ResearchError('measurement_unsafe_path',
                            'Measurement output must be separate from the source copy.')
    for root in (source, output):
        try:
            root.stat()
        except FileNotFoundError:
            continue
        for directory, directories, files in os.walk(root, followlinks=False,
                                                     onerror=record_check.raise_tree_error):
            for name in directories + files:
                mode = (Path(directory) / name).lstat().st_mode
                if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise ResearchError('measurement_unsafe_path',
                                        'Measurement trees must contain only regular files and directories.')
    return source, output


def build_fixture(source_copy, snapshot_revision, output):
    source, output = require_safe_paths(source_copy, output)
    if type(snapshot_revision) is not int or snapshot_revision < 0:
        raise ResearchError('measurement_revision_invalid', 'A snapshot revision must be nonnegative.')
    output.mkdir(parents=True, exist_ok=True)
    snapshot_root = output / 'source-snapshot'
    fixture_root = output / 'fixture'
    record_check.copy_snapshot(source, snapshot_root)
    snapshot_store = Store(snapshot_root)
    snapshot = snapshot_store.snapshot()
    if snapshot_revision > snapshot['revision']:
        raise ResearchError('measurement_revision_invalid', 'The requested revision is absent from the source copy.')
    # Construct a new database from authenticated original rows. No event or
    # receipt is removed from an existing record, including its posted suffix.
    prefix_store = Store(output / 'prefix-store', create=True)
    projections = {}
    with snapshot_store._connection() as original, prefix_store._connection(writable=True) as prefix:
        for row in original.execute('SELECT * FROM events WHERE revision <= ? ORDER BY revision',
                                    (snapshot_revision,)):
            prefix.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)', tuple(row))
            for change in json.loads(row['changes']):
                encoded = _canonical(change['value'])
                projections[(change['kind'], change['key'])] = (encoded, _digest(encoded))
        for row in original.execute('SELECT * FROM receipts WHERE revision <= ? ORDER BY revision',
                                    (snapshot_revision,)):
            prefix.execute('INSERT INTO receipts VALUES (?, ?, ?, ?)', tuple(row))
        for (kind, key), (encoded, checksum) in projections.items():
            prefix.execute('INSERT INTO records VALUES (?, ?, ?, ?)', (kind, key, encoded, checksum))
        prefix.execute('UPDATE metadata SET revision = ? WHERE id = 1', (snapshot_revision,))
    prefix_store.snapshot()
    record_check.copy_snapshot(snapshot_root, fixture_root)
    with open_directory(prefix_store._path.parent) as prefix_directory, \
            open_directory(fixture_root / '.exactory') as fixture_directory:
        os.replace('research.sqlite3', 'research.sqlite3',
                   src_dir_fd=prefix_directory, dst_dir_fd=fixture_directory)
    Store(fixture_root).snapshot()
    write_json(output / 'fixture.json', dict(source_copy=str(source),
        source_revision=snapshot['revision'], snapshot_revision=snapshot_revision,
        fixture=str(fixture_root)))
    return fixture_root


def select_assignment(store, case, assignment_id):
    if assignment_id is not None:
        return assignment_id
    from tools.review_measurement.live_cases import prepare_assignment
    return prepare_assignment(store, case)


def run_live(*, case, source_copy, snapshot_revision, max_calls, output, assignment_id=None):
    source, output = require_safe_paths(source_copy, output)
    if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', case) is None:
        raise ResearchError('measurement_case_invalid', 'A case must be a portable directory name.')
    budget = PaidBudget(max_calls, output / 'usage.json')
    budget.require_available()
    case_output = output / 'cases' / case
    try:
        case_output.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ResearchError('measurement_case_exists', 'A previous case cannot be overwritten or resampled.')
    fixture = build_fixture(source, snapshot_revision, case_output)
    charged_before = len(budget.calls)
    with confine_temporary_files(output / 'temporary'):
        store = Store(fixture)
        head = select_assignment(store, case, assignment_id)
        args = cli.build_parser().parse_args(['record-check', '--workspace', str(fixture)])
        qualification = cli.run(args)
        write_json(case_output / 'record-check.json', qualification)
        if qualification.get('ready') is not True:
            raise ResearchError('measurement_record_check_required',
                                'The candidate record-check must be ready before model dispatch.')
        steps = []
        def emit(step):
            steps.append(step)
            write_json(case_output / 'steps.json', steps)
            # The operation has already retained the provider observation. Stop
            # here before the real driver can enter a wait or select a retry.
            budget.require_running()
        def refuse_worker(*args, **kwargs):
            # A staged task calls its reviewer from a worker process, outside the wrapped sender.
            raise ResearchError('measurement_worker_unsupported',
                                'A staged review calls its reviewer from a worker; the campaign cannot count it.')
        try:
            with patch.object(review_transport, 'send_request',
                              side_effect=budget.wrap_sender(review_transport.send_request)), \
                    patch.object(review_worker, 'launch_worker', side_effect=refuse_worker):
                exit_code = review_series.run_series(store, head, emit=emit)
        except ResearchError as error:
            if error.code not in ('measurement_usage_limit', 'measurement_budget_exhausted'):
                raise
            exit_code = 3
        complete = exit_code == 0
        code = steps[-1].get('code') if steps else None
        if not complete:
            try:
                budget.require_available()
            except ResearchError as error:
                code = error.code
        result = dict(case=case, fixture=str(fixture), assignment_id=head,
                      complete=complete, exit_code=exit_code, code=code,
                      model_calls=len(budget.calls) - charged_before,
                      campaign_calls=len(budget.calls), max_calls=max_calls)
        write_json(case_output / 'result.json', result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--source-copy', type=Path, required=True)
    parser.add_argument('--snapshot-revision', type=int, required=True)
    parser.add_argument('--assignment', dest='assignment_id')
    parser.add_argument('--max-calls', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_live(**vars(args))
    except ResearchError as error:
        print(json.dumps(dict(complete=False, error=error.as_dict())), flush=True)
        return 3
    print(json.dumps(result), flush=True)
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main())
