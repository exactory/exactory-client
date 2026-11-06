"""Durable, serial admission and retained failure evidence for measurements."""

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import uuid
from unittest.mock import patch


@contextmanager
def open_directory(path, *, create=False):
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in Path(path).absolute().parts[1:]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        yield descriptor
    except OSError as error:
        if error.errno not in (errno.ELOOP, errno.ENOTDIR):
            raise
        from research_harness.errors import ResearchError
        raise ResearchError('measurement_unsafe_path',
                            'The output directory changed to a symlink or a non-directory.') from error
    finally:
        os.close(descriptor)


def write_json(path, value):
    path = Path(path)
    with open_directory(path.parent, create=True) as directory:
        temporary = '.measurement-' + uuid.uuid4().hex + '.tmp'
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


@contextmanager
def confine_temporary_files(output):
    """Keep temporary files under the output directory.

    The native CLIs keep their own homes: credentials are never copied into a measurement's output.
    """
    output = Path(output)
    with open_directory(output, create=True):
        pass
    previous = tempfile.tempdir
    try:
        with patch.dict(os.environ, {'TMPDIR': str(output), 'PYTHONDONTWRITEBYTECODE': '1'}):
            tempfile.tempdir = str(output)
            yield
    finally:
        tempfile.tempdir = previous


class PaidBudget:
    """Count before dispatch and share the first quota stop across processes."""

    def __init__(self, maximum, path):
        if type(maximum) is not int or maximum < 1:
            raise ValueError('Paid runs require a positive --max-calls allowance.')
        self.maximum = maximum
        self.path = Path(path).parent.resolve() / Path(path).name
        self.read_state()

    def read_state(self):
        from research_harness.errors import ResearchError

        try:
            state = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return dict(max_calls=self.maximum, calls=[], stop=None)
        if (not isinstance(state, dict) or state.get('max_calls') != self.maximum
                or not isinstance(state.get('calls'), list)):
            raise ResearchError('measurement_state_invalid',
                                'The retained campaign and call allowance must agree.')
        return state

    @property
    def calls(self):
        return self.read_state()['calls']

    def require_running(self):
        from research_harness.errors import ResearchError

        state = self.read_state()
        stop = state.get('stop')
        if stop is None:
            stop = next((dict(call['classification'], call_number=call['number'],
                              route_id=call['route_id']) for call in state['calls']
                         if (call.get('classification') or {}).get('outcome') == 'usage_limit'), None)
        if stop is not None:
            raise ResearchError('measurement_usage_limit',
                                'This campaign stopped at a provider usage limit.', stop)

    def require_available(self):
        from research_harness.errors import ResearchError

        self.require_running()
        calls = self.calls
        if len(calls) >= self.maximum:
            raise ResearchError('measurement_budget_exhausted',
                                'The measurement call allowance is exhausted.')
        if any(call.get('status') == 'admitted' for call in calls):
            raise ResearchError('measurement_call_unresolved',
                                'A previous admitted call has no retained outcome; this campaign cannot dispatch.')

    @contextmanager
    def hold_admission(self):
        with open_directory(self.path.parent, create=True) as directory:
            descriptor = os.open(self.path.name + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            with os.fdopen(descriptor, 'r+') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def retain_classification(self, route, response, call):
        from research_harness.artifacts import ArtifactStore
        from research_harness import review_failure_class, review_protocol

        artifacts = ArtifactStore(self.path.parent)
        reference = artifacts.put(json.dumps(response, ensure_ascii=False,
                                             allow_nan=False).encode('utf-8'), 'application/json')
        observation = dict(id=str(call['number']), assignment_id='measurement',
                           status='failed', output=None, response=reference,
                           usage=response.get('usage'), error=None,
                           observed_at=datetime.now(timezone.utc).isoformat())
        answer = ''.join(part['text'] for item in response.get('output', [])
                         if item.get('type') == 'message' for part in item.get('content', [])
                         if part.get('type') == 'output_text')
        if route.get('adapter', '').startswith('codex_cli') and not answer.strip():
            diagnostic = review_protocol.read_codex_diagnostic(response)
            if diagnostic is not None:
                observation['provider_diagnostic'] = diagnostic
        records = dict(review_assignment={'measurement': dict(id='measurement', route_id=route['id'])},
                       review_route={route['id']: route})
        classification = review_failure_class.classify_attempt(records, artifacts, observation)
        call.update(response=reference, classification=classification)
        if 'provider_diagnostic' in observation:
            call['provider_diagnostic'] = observation['provider_diagnostic']
        return classification

    def wrap_sender(self, sender):
        def send(route, request, credential=None):
            with self.hold_admission():
                self.require_available()
                state = self.read_state()
                call = dict(number=len(state['calls']) + 1, route_id=route['id'],
                            model=route.get('model'), effort=route.get('configuration', {}).get('effort'),
                            status='admitted', usage=None, kind='probe' if any(
                                isinstance(message.get('content'), str)
                                and '"allowed_control":' in message['content']
                                for message in request.get('input', [])) else 'review')
                state['calls'].append(call)
                write_json(self.path, state)
                started = time.monotonic()
                try:
                    response = sender(route, request, credential)
                    classification = self.retain_classification(route, response, call)
                    if classification['outcome'] == 'usage_limit':
                        state['stop'] = dict(classification, call_number=call['number'],
                                             route_id=route['id'])
                    call.update(status=response.get('status'), usage=response.get('usage'))
                    return response
                finally:
                    call['seconds'] = time.monotonic() - started
                    write_json(self.path, state)
        return send
