"""Qualify a filesystem snapshot through explicitly offline record readers.

The only executable mutations are review-assignment, value-review,
review-adjudication and problem-openness. They write only the disposable clone.
review-run is never executed, including capture recovery and reconciliation.
"""

import argparse
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from .artifacts import ArtifactStore, _CheckEffectRequired, is_sealed_artifact
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from . import review_preflight, review_protocol as protocol, review_transport as transport
from .review_continuation import list_chain_heads, plan_next
from .review_failure_class import classify_attempt
from .storage import Store, _RecordHistory, _RecordedSnapshot, _validate


LOCAL_OPERATIONS = frozenset(('review-assignment', 'value-review',
                              'review-adjudication', 'problem-openness'))
# These are scientific limitations or explicit requests for external evidence or
# an agent decision. Capacity refusals of allowed actions are handled separately.
EXPECTED_STOP_CODES = frozenset((
    'review_provider_unavailable', 'review_provider_refused', 'review_output_over_cap',
    'review_execution_incomplete', 'review_timeout', 'review_answer_malformed',
    'review_failure_unknown', 'review_turn_failures_exhausted', 'review_slot_exhausted',
    'review_parts_budget_exhausted', 'review_quality_measurement_pending',
    'review_context_unverified', 'review_context_contaminated', 'review_route_retired',
    'review_route_runtime_stale', 'review_runtime_missing', 'review_invocation_unverified',
    'review_resampling_forbidden', 'native_review_attestation_required',
    'native_review_acceptance_required', 'verification_verdict_body_required',
    'review_recording_required', 'review_worker_reconciliation_required',
    'review_context_repair_required', 'review_context_repair_stale',
    'assessment_incomplete',
    'openness_review_not_approved',
))
DEFAULT_TIME_BUDGET_SECONDS = 900
SNAPSHOT_ATTEMPTS = 3


class _CommandDeadline(BaseException):
    pass


def raise_tree_error(error):
    """Make an unreadable subtree fail qualification instead of omitting it."""
    raise error


class OfflineArtifacts(ArtifactStore):
    """Permit clone artifacts and audited local reads, refusing external effects."""

    def __init__(self, root, local_reads, *, step=None):
        super().__init__(root)
        self.local_reads = local_reads
        self.step = step

    def require_external_effect(self):
        raise _CheckEffectRequired('External effects are unavailable during record qualification')

    def require_local_read(self, kind):
        row = {'kind': kind}
        if self.step is not None:
            row['step'] = self.step
        if kind.startswith('route_runtime:'):
            adapter = kind.split(':', 1)[1]
            row['executable'] = (shutil.which('codex') if adapter.startswith('codex_') else None)
        if kind == 'pdf_render':
            row.update(executable=shutil.which('pdftoppm'), directory=tempfile.tempdir)
        self.local_reads.append(row)
        if (kind == 'route_runtime:openai_responses_v1'
                and (False, False) not in getattr(platform, '_platform_cache', {})):
            raise _CheckEffectRequired('The cold platform runtime reader may launch uname -p; '
                                       'only Codex --version is allowed offline')

    def read_runtime_fingerprint(self, route):
        if route['adapter'] != transport.ADAPTER:
            return super().read_runtime_fingerprint(route)
        # Cloud host metadata cannot change within this command. Its current
        # implementation bytes suffice for optional memo identity; scientific
        # authentication still uses the ordinary runtime reader and bindings.
        self.require_local_read('route_runtime_fingerprint:' + route['adapter'])
        return {'adapter': route['adapter'], 'implementation_sha256': hashlib.sha256(
            Path(transport.__file__).read_bytes()).hexdigest()}


class OfflineStore(Store):
    """Use the normal clone mutation validators with effect denial underneath."""

    def __init__(self, root, local_reads=None):
        self.local_reads = [] if local_reads is None else local_reads
        self.current_step = None
        super().__init__(root)

    def build_artifact_store(self):
        return OfflineArtifacts(self.root, self.local_reads, step=self.current_step)

    def describe_runtime(self):
        """Describe the harness without the Git processes, which qualification refuses."""
        root = Path(__file__).resolve().parents[1]
        version = json.loads((root / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))['version']
        return {'harness_version': version, 'harness_root': str(root),
                'python_executable': sys.executable, 'python_version': platform.python_version()}


def copy_snapshot(source, destination):
    """Copy without SQLite or a subprocess; try APFS clonefile for each file."""
    clonefile = None
    if sys.platform == 'darwin':
        clonefile = ctypes.CDLL(None, use_errno=True).clonefile
        clonefile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
        clonefile.restype = ctypes.c_int

    def copy_file(source_path, destination_path):
        if clonefile is not None:
            result = clonefile(os.fsencode(source_path), os.fsencode(destination_path), 0)
            if result == 0:
                return shutil.copystat(source_path, destination_path)
            number = ctypes.get_errno()
            if number not in (errno.ENOTSUP, errno.EXDEV, errno.EINVAL, errno.ENOSYS):
                raise OSError(number, os.strerror(number), source_path)
        return shutil.copy2(source_path, destination_path)

    shutil.copytree(source, destination, symlinks=True, copy_function=copy_file)
    # Copies supplied for release qualification can be read-only. Never follow
    # their symlinks when granting writes in the disposable tree.
    for directory, directories, files in os.walk(destination, followlinks=False, onerror=raise_tree_error):
        for path in [Path(directory), *(Path(directory) / name for name in files)]:
            info = path.lstat()
            if not stat.S_ISLNK(info.st_mode):
                path.chmod(stat.S_IMODE(info.st_mode) | stat.S_IWUSR)


def check_snapshot(clone):
    """Require both SQLite integrity and the store's full history validation."""
    store = OfflineStore(clone)
    with store._connection() as connection:
        rows = [row[0] for row in connection.execute('PRAGMA integrity_check')]
        if rows != ['ok']:
            raise ResearchError('corrupt_state', 'SQLite integrity check failed', {'results': rows})
    snapshot = store.snapshot()
    return {'revision': snapshot['revision']}


def check_integrity(store):
    """Verify artifacts of every historical event, including superseded records."""
    store.snapshot()
    artifacts = store.build_artifact_store()
    verified = set()
    with store._connection() as connection:
        for event in connection.execute('SELECT payload, changes, result FROM events ORDER BY revision'):
            for encoded in event:
                for reference in protocol.collect_references(json.loads(encoded)):
                    identity = digest(reference)
                    if identity in verified:
                        continue
                    if is_sealed_artifact(reference):
                        artifacts.verify_sealed(reference)
                    else:
                        artifacts.read(reference)
                    verified.add(identity)
    return {'artifacts': len(verified)}


@contextmanager
def confine_local_reads(root):
    """Keep the pinned renderer's stdlib temporary files inside the clone."""
    with tempfile.TemporaryDirectory(prefix='.record-check-render-', dir=root) as directory:
        previous = tempfile.tempdir
        previous_environment = os.environ.get('TMPDIR')
        tempfile.tempdir = directory
        os.environ['TMPDIR'] = directory
        try:
            yield directory
        finally:
            tempfile.tempdir = previous
            if previous_environment is None:
                os.environ.pop('TMPDIR', None)
            else:
                os.environ['TMPDIR'] = previous_environment


def rebuild_invocation(records, artifacts, invocation, *, kind='review_invocation'):
    route_id = invocation.get('route_id')
    if route_id is None:
        route_id = records['review_assignment'][invocation['assignment_id']]['route_id']
    if records['review_route'][route_id]['adapter'] == 'codex_cli_visual_v1':
        with confine_local_reads(artifacts.root):
            store = artifacts.store if isinstance(artifacts, Evaluation) else artifacts
            store.require_local_read('pdf_render')
            return _rebuild_invocation(records, artifacts, invocation, kind=kind)
    return _rebuild_invocation(records, artifacts, invocation, kind=kind)


def _rebuild_invocation(records, artifacts, invocation, *, kind):
    """Match exact retained JSON bytes to a request built under a supported order."""
    raw = artifacts.read(invocation['request'])
    recorded = json.loads(raw)
    if kind == 'review_probe_invocation':
        route = records['review_route'][invocation['route_id']]
        prompt, packet, history = recorded['instructions'], invocation['positive_controls'], []
        if digest(prompt) != invocation['input_manifest']['instructions']:
            raise ResearchError('record_request_changed', 'Probe instructions no longer match their manifest')
    else:
        assignment = records['review_assignment'][invocation['assignment_id']]
        route = records['review_route'][invocation.get('route_id', assignment['route_id'])]
        packet = protocol._read_json(artifacts, assignment['packet'])
        prompt = assignment['prompt']['text']
        history = protocol.read_history(artifacts, assignment)
        if kind == 'review_output_correction_invocation':
            from . import review_output_correction as correction
            historical = records.read_snapshot(invocation['recorded_revision'] - 1) if hasattr(
                records, 'read_snapshot') else records
            packet = correction.prepare_packet(historical, artifacts, assignment['id'])
            prompt, history = correction.PROMPT, []
    candidates = transport.list_built_requests(route, prompt, packet, history)
    if kind == 'review_invocation' and assignment.get('staged_review'):
        from .review_staged_protocol import build_assigned_request
        candidates = (build_assigned_request(artifacts, route, assignment, packet),)
    for request in candidates:
        rebuilt = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        if raw == rebuilt:
            return {'sha256': hashlib.sha256(rebuilt).hexdigest(), 'request_digest': digest(request)}
    raise ResearchError('record_request_changed', 'The recorded invocation request no longer rebuilds byte for byte',
                        {'kind': kind, 'invocation_id': invocation['id']})


def check_reconstruction(store):
    snapshot = store.snapshot()
    records = snapshot['records']
    if records.get('review_output_correction_invocation') and not hasattr(records, 'read_snapshot'):
        history = _RecordHistory()
        with store._connection() as connection:
            _validate(connection, history=history)
        records = _RecordedSnapshot(records, history=history, revision=snapshot['revision'])
    artifacts = Evaluation(records, store.build_artifact_store())
    result = {}
    for kind in ('review_probe_invocation', 'review_invocation', 'review_output_correction_invocation'):
        for identifier, invocation in sorted(records.get(kind, {}).items()):
            result[kind + ':' + identifier] = rebuild_invocation(records, artifacts, invocation, kind=kind)
    for kind in ('review_task_dispatch', 'review_execution_plan'):
        for identifier, record in sorted(records.get(kind, {}).items()):
            assignment_id = record.get('assignment_id')
            if assignment_id is None:
                assignment_id = next((row['key'] for row in record['dependencies']
                                      if row['kind'] == 'review_assignment'), None)
            if assignment_id is None:
                raise ResearchError('qualification_unavailable', 'A frozen request lacks its assignment',
                                    {'step': 'reconstruction', 'reason': kind + ':' + identifier})
            assignment = records['review_assignment'][assignment_id]
            result[kind + ':' + identifier] = rebuild_staged_request(records, artifacts, assignment, record)
    return {'invocations': result}


def rebuild_staged_request(records, artifacts, assignment, record):
    """Rebuild an immutable dispatch or plan, including unaccepted calls."""
    from .review_staged_protocol import build_assigned_request
    route = records['review_route'][assignment['route_id']]
    packet = protocol._read_json(artifacts, assignment['packet'])
    request = build_assigned_request(artifacts, route, assignment, packet)
    rebuilt = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if (digest(request) != record['request_digest']
            or record.get('request') and artifacts.read(record['request']) != rebuilt):
        raise ResearchError('record_request_changed', 'A frozen staged request no longer rebuilds byte for byte',
                            {'assignment_id': assignment['id'], 'id': record['id']})
    return {'sha256': hashlib.sha256(rebuilt).hexdigest(), 'request_digest': digest(request)}


def check_authentication(store):
    snapshot = store.snapshot()
    records = snapshot['records']
    artifacts = Evaluation(records, store.build_artifact_store())
    from .review_state_evaluation import runtime_scope
    with runtime_scope(records, artifacts):
        attempts = {}
        for identifier, attempt in sorted(records.get('review_attempt', {}).items()):
            assignment = records['review_assignment'][attempt['assignment_id']]
            # Failed/imported observations legitimately return False. That is the
            # authentication result, never a requirement to manufacture readiness.
            attempts[identifier] = describe_observation(records, artifacts, assignment, attempt)
        probes = {}
        for identifier, probe in sorted(records.get('review_route_probe', {}).items()):
            probes[identifier] = describe_probe_observation(records, artifacts, probe)
        corrections = {}
        for identifier, observation in sorted(records.get('review_output_correction', {}).items()):
            invocation = records['review_output_correction_invocation'][identifier]
            corrections[identifier] = describe_correction_observation(records, artifacts, invocation, observation)
        captures = {}
        from .review_call_capture import read_call_observation
        for identifier, captured in sorted(records.get('review_call_observation', {}).items()):
            if captured['operation'] != 'review.invoke':
                # Probe and correction captures still get their ownership and
                # artifact bindings checked by their existing capture reader.
                invocation_kind = captured['invocation']['kind']
                invocation = records[invocation_kind][captured['invocation_id']]
                keys = ('id', 'route_id') if captured['operation'] == 'review.route_probe' else (
                    'id', 'assignment_id', 'route_id')
            else:
                invocation = records['review_invocation'][captured['invocation_id']]
                keys = ('id', 'assignment_id')
            payload = {key: invocation[key] for key in keys}
            invocation, observation = read_call_observation(
                records, artifacts, captured['operation'], payload, captured['request_id'])
            if captured['operation'] == 'review.invoke':
                assignment = records['review_assignment'][invocation['assignment_id']]
                attempt = dict(observation, id=invocation['id'], assignment_id=assignment['id'],
                               origin=captured['origin'], packet_digest=invocation['packet_digest'],
                               prompt_hash=invocation['prompt_hash'], recorded_revision=captured['recorded_revision'])
                captures[identifier] = describe_observation(records, artifacts, assignment, attempt)
            elif captured['operation'] == 'review.route_probe':
                captures[identifier] = describe_probe_observation(records, artifacts, dict(invocation, **observation))
            else:
                captures[identifier] = describe_correction_observation(records, artifacts, invocation, observation)
        for identifier, captured in sorted(records.get('review_observation', {}).items()):
            plan = records['review_execution_plan'][captured['plan_id']]
            assignment_id = next((row['key'] for row in plan['dependencies']
                                  if row['kind'] == 'review_assignment'), None)
            if assignment_id is None:
                captures[identifier] = {'authenticated': False, 'status': captured['observation']['status']}
                continue
            assignment = records['review_assignment'][assignment_id]
            attempt = dict(captured['observation'], id=identifier, assignment_id=assignment_id,
                           origin=captured['origin'], reservation_id=identifier,
                           recorded_revision=captured['recorded_revision'])
            captures[identifier] = describe_observation(records, artifacts, assignment, attempt)
        assignments = {}
        for identifier in sorted(records.get('review_assignment', {})):
            state = protocol.assignment_state(records, artifacts, identifier)
            assignments[identifier] = {'ready': state['ready'], 'attempt_id': state.get('attempt_id'),
                                       'refusal_codes': sorted(row['code'] for row in state['obligations'])}
    return {'attempts': attempts, 'assignments': assignments, 'probes': probes,
            'captures': captures, 'corrections': corrections}


def describe_observation(records, artifacts, assignment, attempt):
    return {'authenticated': protocol.authenticate_attempt(records, artifacts, assignment, attempt),
            'status': attempt['status'], 'origin': attempt.get('origin'),
            'classification': classify_attempt(records, artifacts, attempt)}


def describe_probe_observation(records, artifacts, probe):
    selected = dict(records, review_route_probe={probe['id']: probe})
    state = protocol.route_state(selected, Evaluation(selected, artifacts), probe['route_id'])
    return {'authenticated': state['context_status'] == 'verified_for_route', 'status': probe['status']}


def describe_correction_observation(records, artifacts, invocation, observation):
    from .review_output_correction import _authenticate_observation
    result = {'authenticated': False, 'status': observation['status']}
    if observation['status'] == 'completed':
        try:
            _authenticate_observation(records, artifacts, invocation, observation)
        except (ResearchError, json.JSONDecodeError) as error:
            result['error_code'] = error.code if isinstance(error, ResearchError) else 'record_json_invalid'
        else:
            result['authenticated'] = True
    return result


def check_prospective_request(records, artifacts, plan):
    """Validate delivery and preflight only, with no admission or dispatch path."""
    identifier = plan.get('payload', {}).get('assignment_id', plan['head_id'])
    assignment = records['review_assignment'][identifier]
    if assignment.get('staged_review', {}).get('kind') == 'controller':
        task_id = plan.get('payload', {}).get('task_id')
        child = next((row for row in records.get('review_assignment', {}).values()
                      if row.get('staged_review', {}).get('task_id') == task_id), None)
        if child is None:
            raise ResearchError('qualification_unavailable', 'No frozen child request can be checked',
                                {'step': 'continuation', 'reason': 'staged child not assigned'})
        assignment = child
    request = protocol.build_assignment_request(records, artifacts, assignment)
    packet = protocol._read_json(artifacts, assignment['packet'])
    from .review_source_complete import validate
    validate(records, artifacts, assignment, packet)
    protocol.validate_assignment_inventories(records, artifacts, assignment['id'])
    report = review_preflight.build_assignment_report(records, artifacts, assignment, request)
    review_preflight.check_request(report, request, packet=packet, artifacts=artifacts)
    return {'assignment_id': assignment['id'], 'request_digest': digest(request), 'preflight': report}


def describe_capacity_decision(error, plan):
    """Recognize material that cannot finish on the selected route."""
    if (error.code in ('review_parts_frame_too_large', 'review_parts_inventory_too_large',
                       'review_parts_final_too_large')
            or (error.code == 'review_input_too_large'
                and (error.details or {}).get('phase') == 'final_preflight')):
        return {'head_id': plan['head_id'], 'command': plan['command'],
                'code': error.code, 'details': error.details}
    return None


def check_continuation(store):
    from .cli import OPERATIONS
    snapshot = store.snapshot()
    heads = list_chain_heads(snapshot['records'], store.build_artifact_store())
    chains, decision_stops = [], []
    for head_id in heads:
        actions, executed = [], set()
        chain = {'head_id': head_id, 'actions': actions}
        chains.append(chain)
        while True:
            snapshot = store.snapshot()
            records = snapshot['records']
            artifacts = Evaluation(records, store.build_artifact_store())
            plan = plan_next(records, artifacts, head_id)
            actions.append(plan)
            assignment = records['review_assignment'][head_id]
            if assignment['role'] == 'native_math':
                from .native_review import load_packet
                from .openness_reading import is_openness_packet
                native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
                if not is_openness_packet(native):
                    chain['boundary'] = 'controller'
                    break
            state = plan['state']
            if state == 'done':
                chain['boundary'] = 'done'
                break
            if state in ('handoff', 'stop'):
                if plan.get('code') not in EXPECTED_STOP_CODES:
                    raise ResearchError(plan.get('code') or 'record_unknown_stop',
                                        'Continuation reached an unqualified stop', plan)
                chain['boundary'] = state
                break
            if state in ('run', 'wait', 'resume'):
                if plan.get('command') != 'review-run':
                    raise ResearchError('qualification_unavailable', 'The offline executor refuses this action',
                                        {'step': 'continuation', 'reason': 'Operation outside allowlist: '
                                         + str(plan.get('command'))})
                pending = (state == 'resume' or plan.get('code') in (
                    'review_invocation_pending', 'review_capacity_wait')
                    or plan.get('payload', {}).get('action') in ('accept', 'reconcile'))
                try:
                    chain['request'] = check_prospective_request(records, artifacts, plan)
                except ResearchError as error:
                    decision = describe_capacity_decision(error, plan)
                    if decision is None:
                        raise
                    decision_stops.append(decision)
                    chain['boundary'] = 'decision'
                    break
                chain['boundary'] = 'pending' if pending else state
                break
            if state not in ('continue', 'replace', 'plan', 'record'):
                raise ResearchError('record_unknown_action', 'The planner returned an unknown continuation state', plan)
            command = plan.get('command')
            if command not in LOCAL_OPERATIONS:
                raise ResearchError('qualification_unavailable', 'The offline executor refuses this action',
                                    {'step': 'continuation', 'reason': 'Operation outside allowlist: ' + str(command)})
            position = digest([command, plan['payload'], plan['progress']])
            if position in executed:
                raise ResearchError('review_series_no_progress', 'Offline continuation repeated an unchanged action', plan)
            executed.add(position)
            try:
                OPERATIONS[command](store, plan['payload'], expected_revision=snapshot['revision'],
                                    request_id='record-check:' + uuid.uuid4().hex)
            except ResearchError as error:
                decision = describe_capacity_decision(error, plan)
                if decision is not None:
                    decision_stops.append(decision)
                    chain['boundary'] = 'decision'
                    break
                current = store.snapshot()['records']
                if current.get('review_output_refusal', {}) != records.get('review_output_refusal', {}):
                    plan['refusal'] = error.as_dict()
                    continue
                if error.code in EXPECTED_STOP_CODES:
                    actions.append({'state': 'handoff', 'code': error.code, 'details': error.details})
                    chain['boundary'] = 'handoff'
                    break
                raise
    return {'chains': chains, 'decision_stops': decision_stops}


def run_timed_check(name, check, *, budget=None):
    """Report known record refusals; let implementation defects surface."""
    started = time.monotonic()
    previous_handler = None

    def expire(signum, frame):
        raise _CommandDeadline()

    def describe_timeout():
        return {'code': 'record_command_timeout', 'message': 'The record reader exceeded its time budget',
                'details': {'command': name, 'budget_seconds': budget}}

    row = {'step': name, 'status': 'passed'}
    try:
        if budget is not None and hasattr(signal, 'setitimer'):
            previous_timer = signal.getitimer(signal.ITIMER_REAL)
            previous_handler = signal.signal(signal.SIGALRM, expire)
            signal.setitimer(signal.ITIMER_REAL, budget)
        row['result'] = check()
    except _CommandDeadline:
        row.update(status='failed', error=describe_timeout())
    except _CheckEffectRequired as error:
        row.update(status='failed', error={
            'code': 'qualification_unavailable', 'message': 'An external effect is required',
            'details': {'step': name, 'reason': str(error) or 'External tool effect refused'}})
    except (ResearchError, json.JSONDecodeError) as error:
        description = (error.as_dict() if isinstance(error, ResearchError) else
                       {'code': 'record_json_invalid', 'message': str(error)})
        row.update(status='failed', error=description)
    finally:
        if previous_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
            signal.signal(signal.SIGALRM, previous_handler)
    row['seconds'] = time.monotonic() - started
    if budget is not None and row['seconds'] > budget:
        row.update(status='failed', error=describe_timeout())
    return row


def build_sandbox_profile(clone, report_directory, source):
    """Deny network, live-record reads and writes outside the two destinations."""
    return '\n'.join((
        '(version 1)', '(allow default)', '(deny network-outbound)',
        '(deny file-read* (subpath ' + json.dumps(str(source.resolve())) + '))',
        '(deny file-write* (require-all (require-not (subpath ' + json.dumps(str(clone.resolve()))
        + ')) (require-not (subpath ' + json.dumps(str(report_directory.resolve()))
        + ')) (require-not (literal "/dev/null"))))',
    ))


def check_math_roots(clone, harness_root, *, time_budget=DEFAULT_TIME_BUDGET_SECONDS):
    """Discover roots on disk, never read or follow the absolute-path registry."""
    results = []
    for directory, directories, files in os.walk(clone, followlinks=False, onerror=raise_tree_error):
        directories[:] = [name for name in directories if not (Path(directory) / name).is_symlink()]
        if Path(directory).name != '.search' or 'tree.json' not in files:
            continue
        root = Path(directory).parent.resolve()
        root.relative_to(clone.resolve())
        for command in ('status', 'next'):
            # The actual search CLI accepts no positional slug. The explicit
            # attack root identifies its one controller, including its slug.
            arguments = [sys.executable, str(harness_root / 'bin/exactory-math'),
                         '--attack-root', str(root), 'search', command, '--json']
            started = time.monotonic()
            try:
                completed = subprocess.run(arguments, cwd=harness_root, capture_output=True,
                                           text=True, timeout=time_budget,
                                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            except subprocess.TimeoutExpired as error:
                raise ResearchError('record_command_timeout', 'Math reader exceeded its time budget',
                                    {'command': command, 'attack_root': str(root)}) from error
            result = {'attack_root': str(root), 'command': arguments, 'returncode': completed.returncode,
                      'seconds': time.monotonic() - started}
            if completed.returncode:
                raise ResearchError('record_math_read_failed', 'Math reader refused the cloned controller',
                                    dict(result, stderr=completed.stderr, stdout=completed.stdout))
            result['result'] = json.loads(completed.stdout)
            results.append(result)
    return results


def check_report(store, *, time_budget=DEFAULT_TIME_BUDGET_SECONDS):
    from .cli import status_report
    from .report_views import next_summary, obligations_page
    commands = {}
    for command in ('status', 'next', 'obligations'):
        def read_command():
            report = status_report(store)
            if command == 'next':
                return next_summary(report)
            if command == 'obligations':
                codes = sorted({row['code'] for row in report['obligations']}) or ['review_output_pending']
                return [obligations_page(report, code, limit=50, cursor=None) for code in codes]
            return report
        result = run_timed_check(command, read_command, budget=time_budget)
        # Keep reports bounded without dropping their timing or refusal.
        result.pop('result', None)
        commands[command] = result
    math = run_timed_check('math', lambda: check_math_roots(
        store.root, Path(__file__).resolve().parents[1], time_budget=time_budget))
    return {'commands': commands, 'math': math,
            'ready': math['status'] == 'passed' and all(row['status'] == 'passed' for row in commands.values())}


def qualify_snapshot(clone, report):
    store = OfflineStore(clone, report['local_reads'])
    with confine_local_reads(clone):
        for name, check in (('integrity', check_integrity), ('reconstruction', check_reconstruction),
                            ('authentication', check_authentication), ('continuation', check_continuation),
                            ('report', lambda store: check_report(
                                store, time_budget=report.get('time_budget_seconds', DEFAULT_TIME_BUDGET_SECONDS)))):
            store.current_step = name
            row = run_timed_check(name, lambda: check(store))
            if name == 'report' and row['status'] == 'passed' and not row['result']['ready']:
                row.update(status='failed', error={
                    'code': 'record_report_failed', 'message': 'One or more record readers failed qualification'})
            report['steps'].append(row)
            if row['status'] != 'passed':
                break
    report['ready'] = (len(report['steps']) == 6
                       and all(row['status'] == 'passed' for row in report['steps'])
                       and report['steps'][-1]['result']['ready'])
    return report


def check_isolated_snapshot(clone, source, report, report_directory):
    """Apply the macOS profile before running any qualification readers."""
    executable = Path('/usr/bin/sandbox-exec')
    if sys.platform != 'darwin':
        report['isolation_reason'] = 'sandbox-exec is available only on macOS'
        return qualify_snapshot(clone, report)
    try:
        executable.stat()
    except OSError as error:
        report['isolation_reason'] = 'sandbox-exec is unavailable: ' + str(error)
        return qualify_snapshot(clone, report)
    profile = build_sandbox_profile(clone, report_directory, source)
    try:
        probe = subprocess.run([str(executable), '-p', profile, '/usr/bin/true'],
                               capture_output=True, text=True, timeout=15,
                               cwd=Path(__file__).resolve().parents[1])
    except (OSError, subprocess.TimeoutExpired) as error:
        report['isolation_reason'] = 'sandbox-exec could not apply its profile: ' + str(error)
        return qualify_snapshot(clone, report)
    if probe.returncode:
        report['isolation_reason'] = probe.stderr.strip() or 'sandbox-exec could not apply its profile'
        return qualify_snapshot(clone, report)
    report['isolation'] = 'sandbox-exec'
    report_path = report_directory / 'report.json'
    report_path.write_text(json.dumps(report), encoding='utf-8')
    harness_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run([
        str(executable), '-p', profile, sys.executable, '-m', 'research_harness.record_check',
        '--snapshot', str(clone), '--report', str(report_path)], cwd=harness_root,
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(harness_root), PYTHONDONTWRITEBYTECODE='1',
                 TMPDIR=str(report_directory)))
    if completed.returncode:
        raise RuntimeError('Isolated record check failed: ' + completed.stderr)
    return json.loads(report_path.read_text(encoding='utf-8'))


def check_record(workspace, *, keep_clone=None, isolate=True, time_budget=DEFAULT_TIME_BUDGET_SECONDS):
    """Clone W before opening any SQLite connection; always remove temporary work."""
    if not isinstance(time_budget, (int, float)) or not math.isfinite(time_budget) or time_budget <= 0:
        raise ResearchError('invalid_input', 'The time budget must be a finite positive number of seconds')
    source = Path(workspace).resolve(strict=True)
    harness_root = Path(__file__).resolve().parents[1]
    version = json.loads((harness_root / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))['version']
    report = {'ready': False, 'workspace': str(source), 'steps': [], 'local_reads': [],
              'time_budget_seconds': time_budget,
              'isolation': 'unavailable', 'runtime': {
                  'harness_version': version, 'harness_root': str(harness_root),
                  'python_executable': sys.executable, 'python_version': platform.python_version()}}
    # gettempdir probes candidates by creating a file. Validate the paths before
    # choosing one, so a TMPDIR inside W receives no probe or transient write.
    candidates = [Path(value).resolve() for value in
                  (os.environ.get('TMPDIR'), os.environ.get('TEMP'), os.environ.get('TMP')) if value]
    candidates.append(Path('/tmp').resolve())
    temporary_parent = None
    for parent in candidates:
        if parent == source or source in parent.parents:
            continue
        try:
            info = parent.stat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISDIR(info.st_mode):
            temporary_parent = parent
            break
    if temporary_parent is None:
        raise ResearchError('unsafe_path', 'No temporary directory is outside the source workspace')
    with tempfile.TemporaryDirectory(prefix='exactory-record-check-', dir=temporary_parent) as temporary:
        report_directory = Path(temporary).resolve()
        clone = Path(keep_clone).resolve() if keep_clone is not None else report_directory / 'workspace'
        if clone == source or source in clone.parents:
            raise ResearchError('unsafe_path', 'The clone must be outside the source workspace')
        try:
            clone.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ResearchError('unsafe_path', 'The clone destination must not exist')
        report['clone'] = str(clone)
        failures = []
        started = time.monotonic()
        for attempt in range(SNAPSHOT_ATTEMPTS):
            try:
                copy_snapshot(source, clone)
                result = check_snapshot(clone)
            except FileExistsError as error:
                raise ResearchError('unsafe_path', 'The clone destination appeared before copying; '
                                    'its existing content was preserved') from error
            except (ResearchError, json.JSONDecodeError, OSError, shutil.Error) as error:
                failures.append(error.as_dict() if isinstance(error, ResearchError) else
                                {'code': type(error).__name__, 'message': str(error)})
                try:
                    clone.lstat()
                except FileNotFoundError:
                    pass
                else:
                    shutil.rmtree(clone)
            else:
                report['steps'].append({'step': 'snapshot', 'status': 'passed',
                                        'seconds': time.monotonic() - started,
                                        'result': dict(result, attempts=attempt + 1)})
                break
        else:
            report['steps'].append({'step': 'snapshot', 'status': 'failed',
                                    'seconds': time.monotonic() - started, 'error': {
                                        'code': 'record_snapshot_inconsistent',
                                        'message': 'No consistent snapshot after three copies',
                                        'details': {'attempts': failures}}})
            return report
        if isolate:
            return check_isolated_snapshot(clone, source, report, report_directory)
        report['isolation_reason'] = 'Isolation disabled by the in-process caller'
        return qualify_snapshot(clone, report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding='utf-8'))
    result = qualify_snapshot(args.snapshot, report)
    args.report.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
