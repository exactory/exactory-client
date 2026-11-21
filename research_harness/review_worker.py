"""Owned detached review worker and process-group launch guardians.

Lock order: controller ownership, worker ownership, short Store transactions.
The inherited worker lock covers the spawn/identity handshake gap. Each local
CLI passes through a guardian: the owner of the call records the guardian's
process group, and only then lets the guardian exec the CLI, which keeps the
lock until it exits. Disposable files never authenticate a reviewer response.
"""

from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
from subprocess import Popen as _SYSTEM_POPEN
import sys
import time
import uuid

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[1])
    from research_harness.review_worker import main
    if __name__ == "__main__":
        main()
    sys.exit(0)

from . import probe_supervision
from .errors import ResearchError
from .evidence import digest
from .finishing import (LOCK_RETRY_CODES, SCOPED_FINISH_WAIT_SECONDS, retry_finishing_write, retry_store_write,
                        wait_for_store_in_command)
from .review_call_admission import check_launch
from .review_task_execution import (_check_references, _get, _mutate_current, _TransactionRecords,
                                     _transition, capture_observation)
from .storage import Store, wait_for_store_locks
from .workspace import checked_parent

# The messages of the records before a reviewer program starts, and of the capture of a staged worker, when the store
# refused one of them beyond the wait: the record of the process that starts the program (for a call that a command
# owns, and for the worker of a staged review task), the launch claim and the authorization that the launching command
# writes, and the records with which the worker takes and starts its reservation and captures its call.
CALL_CHILD_STORE_BUSY_MSG = (
    'The research store stayed busy, and the process that starts the reviewer program was not recorded. The reviewer '
    'program did not start, and no request was sent to the reviewer. The call has no outcome yet. Run review-series on '
    'its assignment, or review-run with action reconcile for the call. Each of them records the call as interrupted. '
    'The next run then starts the reviewer.')
WORKER_CHILD_STORE_BUSY_MSG = (
    'The research store stayed busy, and the process that starts the reviewer program of this review task was not '
    'recorded. The reviewer program did not start, and no request was sent to the reviewer. The worker captures this '
    'refusal as a failed observation, and its reservation ends as a known failure. The task then takes its next attempt '
    'when it has an attempt left.')
WORKER_CLAIM_STORE_BUSY_MSG = (
    'The research store stayed busy, and the launch of the worker of this review task was not claimed. No worker and '
    'no reviewer program started, and the reservation is unchanged. Run the same command again.')
WORKER_AUTHORIZE_STORE_BUSY_MSG = (
    'The research store stayed busy, and the start of the worker of this review task was not authorized. The reviewer '
    'program did not start, and no request was sent to the reviewer. The worker gets no token and ends. Run '
    'review-series on the controller assignment. It reconciles the reservation as a known failure and plans the next '
    'attempt of the task when the task has an attempt left.')
WORKER_READY_STORE_BUSY_MSG = (
    'The research store stayed busy, and the worker of this review task did not record that it is ready. The reviewer '
    'program did not start, and no request was sent to the reviewer. The worker ends here. The reconciliation of its '
    'reservation records a known failure, and the task then takes its next attempt when it has an attempt left.')
WORKER_RUNNING_STORE_BUSY_MSG = (
    'The research store stayed busy, and the worker of this review task did not record its start. The reviewer '
    'program did not start, and no request was sent to the reviewer. The worker ends here. The reconciliation of its '
    'reservation records a known failure, and the task then takes its next attempt when it has an attempt left.')
WORKER_CAPTURE_STORE_BUSY_MSG = (
    'The research store stayed busy, and the worker of this review task did not capture what its reviewer call '
    'returned, so no record holds it. The worker ends here. The reconciliation of its reservation records an unknown '
    'outcome, and the task then takes its next attempt when it has an attempt left.')


def _directory(reservation_id):
    return "research/review-workers/" + digest({"reservation_id": reservation_id})


@contextmanager
def _owner(root, reservation_id, *, worker=False):
    relative = _directory(reservation_id) + ("/worker.lock" if worker else "/controller.lock")
    with checked_parent(root, relative, create=True) as (directory, name):
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ResearchError("unsafe_path", "Review ownership requires a regular unaliased lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ResearchError("review_worker_reconciliation_required", "The review reservation still has a live owner") from error
        yield descriptor
    finally:
        # Do not LOCK_UN: a detached child inherits this same open file description.
        os.close(descriptor)


def _project_process_identity(identity):
    if identity is None:
        return None
    return {"pid": identity["pid"], "pgid": identity["process_group"],
            "started": ":".join(str(value) for value in identity["start_identity"])}


def _process_table(identities=()):
    """Read live identities for the owned leaders and their relevant groups."""
    result = {}
    try:
        for identity in identities:
            current = probe_supervision.find_process_identity(identity["pid"])
            if current is not None:
                result[current["pid"]] = _project_process_identity(current)
        for group in {identity["pgid"] for identity in identities}:
            for current in probe_supervision.find_process_group_members(group):
                result[current["pid"]] = _project_process_identity(current)
    except OSError as error:
        raise ResearchError("review_worker_reconciliation_required", "Process identities could not be checked") from error
    return result


def _identity(pid=None):
    pid = os.getpid() if pid is None else pid
    try:
        result = _project_process_identity(probe_supervision.find_process_identity(pid))
    except OSError as error:
        raise ResearchError("review_worker_reconciliation_required", "Process identity check is unavailable") from error
    if result is None:
        raise ResearchError("review_worker_reconciliation_required", "The expected process identity is no longer live")
    return result


def _same_process(current, identity):
    return current is not None and all(current.get(key) == identity.get(key) for key in ("pid", "pgid", "started"))


def _has_processes(identity, table):
    return (_same_process(table.get(identity["pid"]), identity)
            or any(process["pgid"] == identity["pgid"] for process in table.values()))


@contextmanager
def _termination_guard(store, reservation_id):
    with ExitStack() as stack:
        try:
            stack.enter_context(_owner(store.root, reservation_id, worker=True))
            reservation = _get(store.snapshot()["records"], "review_reservation", reservation_id)
            identities = ([reservation["worker"]] if reservation["worker"] is not None else []) + reservation["children"]
            table = _process_table(identities)
            if any(_has_processes(identity, table) for identity in identities):
                termination = None
            else:
                termination = {"reservation_id": reservation_id, "worker": reservation["worker"],
                               "children": reservation["children"], "confirmed": True}
        except ResearchError as error:
            if error.code != "review_worker_reconciliation_required":
                raise
            termination = None
        yield termination


def confirm_termination(store, reservation_id):
    """Read-only lifecycle check; release rechecks while holding both ownership locks."""
    with _owner(store.root, reservation_id):
        with _termination_guard(store, reservation_id) as termination:
            return termination is not None


@contextmanager
def _guard_call_termination(store, lifecycle_id):
    """The controller lock proves inline ownership ended; child groups must also end."""
    with ExitStack() as stack:
        try:
            stack.enter_context(_owner(store.root, lifecycle_id, worker=True))
            termination = read_call_termination(store, lifecycle_id)
        except ResearchError as error:
            if error.code != "review_worker_reconciliation_required":
                raise
            termination = None
        yield termination


def read_call_termination(store, lifecycle_id):
    """Called only while holding controller ownership and the worker lock."""
    current = store.snapshot()["records"].get("review_pool_membership", {}).get(lifecycle_id)
    # Historical calls never held these locks or registered their child groups.
    # Acquiring newly created locks cannot establish that those calls ended.
    if current is None:
        return None
    try:
        table = _process_table(current["children"])
    except ResearchError as error:
        if error.code != "review_worker_reconciliation_required":
            raise
        return None
    if any(_has_processes(child, table) for child in current["children"]):
        return None
    return {"reservation_id": lifecycle_id, "owner": current["owner"], "children": current["children"], "confirmed": True}


class _CallAuthority:
    """Live inline ownership for ordinary capture and guarded termination."""

    def __init__(self, store, operation, lifecycle_id, descriptor, ownership):
        self.root, self.operation, self.reservation_id = store.root, operation, lifecycle_id
        self.descriptor, self.token, self.identity = descriptor, uuid.uuid4().hex, _identity()
        self._ownership = ownership

    def verify(self, store, membership):
        if (self.root != store.root or self.identity['pid'] != os.getpid() or self.identity != _identity()
                or membership['id'] != self.reservation_id or membership['operation'] != self.operation
                or membership['owner'] != self.identity or membership['token'] != self.token
                or membership['released']):
            raise ResearchError('review_instrumented_capture_required', 'Capture belongs to a different live call')
        _WorkerAuthority._check_lock(self)

    def finish_transport(self):
        # Close our inherited descriptor before checking for surviving owners.
        self._ownership.close()


@contextmanager
def own_call(store, operation, invocation_id):
    from .review_task_execution import identify_call
    lifecycle_id = identify_call(operation, invocation_id)
    with _owner(store.root, lifecycle_id):
        with ExitStack() as ownership:
            descriptor = ownership.enter_context(_owner(store.root, lifecycle_id, worker=True))
            yield _CallAuthority(store, operation, lifecycle_id, descriptor, ownership)



class _WorkerAuthority:
    """An in-process capture capability tied to the worker identity and inherited lock."""

    def __init__(self, store, reservation_id, token, descriptor):
        self.root, self.reservation_id, self.token = store.root, reservation_id, token
        self.descriptor = descriptor
        self.identity = _identity()
        if self.identity["pgid"] != os.getpid():
            raise ResearchError("review_instrumented_capture_required", "The review worker must own its process group")
        self._check_lock()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ResearchError("review_instrumented_capture_required", "The review worker does not own its inherited lock") from error

    def _check_lock(self):
        with checked_parent(self.root, _directory(self.reservation_id) + "/worker.lock") as (directory, name):
            path_info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        descriptor_info = os.fstat(self.descriptor)
        if (not stat.S_ISREG(path_info.st_mode) or path_info.st_nlink != 1
                or (path_info.st_dev, path_info.st_ino) != (descriptor_info.st_dev, descriptor_info.st_ino)):
            raise ResearchError("review_instrumented_capture_required", "The worker ownership lock changed")

    def verify(self, store, reservation):
        if (self.root != store.root or self.identity["pid"] != os.getpid() or self.identity != _identity()
                or reservation["id"] != self.reservation_id or reservation["token"] != self.token
                or reservation["worker"] != self.identity):
            raise ResearchError("review_instrumented_capture_required", "Capture belongs to a different live worker")
        self._check_lock()


def _entry_command():
    return [sys.executable, str(Path(__file__).resolve())]


def _claim_launch(store, reservation_id):
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        reservation = _get(records, "review_reservation", reservation_id)
        check_launch(records, "review.worker_claim", reservation)
        return _transition(tx, reservation, revision, launch={"authorized": False})
    return _mutate_current(store, "review.worker_claim", {"reservation_id": reservation_id}, apply,
                           request_id="review-worker-claim:" + reservation_id)["result"]


def _authorize_launch(store, reservation_id, identity):
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        reservation = _get(records, "review_reservation", reservation_id)
        if reservation["released"] or reservation["state"] != "reserved" or reservation["worker"] != identity:
            raise ResearchError("review_worker_reconciliation_required", "Worker handshake identity changed")
        _check_references(records, _get(records, "review_execution_plan", reservation["plan_id"]))
        return _transition(tx, reservation, revision, launch={"authorized": True})
    return _mutate_current(store, "review.worker_authorize", {"reservation_id": reservation_id, "worker": identity}, apply,
                           request_id="review-worker-authorize:" + reservation_id)["result"]


def read_polling_store(read):
    """Return no value for a busy read; the polling caller owns its wait and deadline, so the read waits the busy
    timeout and not the store wait of its command."""
    try:
        with wait_for_store_locks(None):
            return read()
    except ResearchError as error:
        if error.code != "store_busy":
            raise
        return None


def launch_worker(store, reservation_id, *, handshake_timeout=SCOPED_FINISH_WAIT_SECONDS):
    """Allow a loaded worker to start, retaining a reconcilable claim on refusal. The handshake waits for the record
    of the worker as long as the worker waits for a busy store, and ends when the worker ends."""
    if not isinstance(handshake_timeout, (int, float)) or not 0 < handshake_timeout <= SCOPED_FINISH_WAIT_SECONDS:
        raise ResearchError("invalid_input", "Worker handshake timeout must be positive and at most %d seconds"
                            % SCOPED_FINISH_WAIT_SECONDS)
    with _owner(store.root, reservation_id):
        snapshot = retry_store_write(store.snapshot, codes=("store_busy",))
        current = _get(snapshot["records"], "review_reservation", reservation_id)
        if current["launch"] is not None or current["state"] != "reserved" or current["released"]:
            raise ResearchError("review_invocation_pending", "Resume the original worker reservation instead of relaunching")
        with _owner(store.root, reservation_id, worker=True) as descriptor:
            reservation = retry_finishing_write(lambda: _claim_launch(store, reservation_id),
                                                WORKER_CLAIM_STORE_BUSY_MSG, kept={"reservation_id": reservation_id},
                                                codes=LOCK_RETRY_CODES)
            relative = _directory(reservation_id) + "/worker.log"
            with checked_parent(store.root, relative, create=True) as (directory, name):
                log_descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(log_descriptor, "wb") as diagnostics:
                info = os.fstat(diagnostics.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ResearchError("unsafe_path", "Review diagnostics require a regular unaliased file")
                os.ftruncate(diagnostics.fileno(), 0)
                try:
                    process = subprocess.Popen(_entry_command() + [str(store.root), reservation_id,
                        reservation["token"], str(descriptor)], stdin=subprocess.PIPE, bufsize=0,
                        stdout=diagnostics, stderr=diagnostics, start_new_session=True, pass_fds=(descriptor,))
                except OSError as error:
                    raise ResearchError("review_worker_reconciliation_required", "The claimed review worker did not start") from error
            try:
                deadline = time.monotonic() + handshake_timeout
                while time.monotonic() < deadline and process.poll() is None:
                    snapshot = read_polling_store(store.snapshot)
                    if snapshot is not None:
                        current = _get(snapshot["records"], "review_reservation", reservation_id)
                        identity = current["worker"]
                        if identity is not None:
                            if identity["pid"] != process.pid or identity["pgid"] != process.pid or identity != _identity(process.pid):
                                raise ResearchError("review_worker_reconciliation_required", "The detached worker did not establish its process identity")
                            retry_finishing_write(lambda: _authorize_launch(store, reservation_id, identity),
                                                  WORKER_AUTHORIZE_STORE_BUSY_MSG,
                                                  kept={"reservation_id": reservation_id}, codes=LOCK_RETRY_CODES)
                            process.stdin.write((reservation["token"] + "\n").encode())
                            return process
                    time.sleep(.01)
                raise ResearchError("review_worker_reconciliation_required", "The worker launch handshake did not complete")
            except OSError as error:
                raise ResearchError("review_worker_reconciliation_required", "The worker launch handshake was interrupted") from error
            finally:
                process.stdin.close()


def _bind_worker(store, reservation_id, token, identity):
    def apply(tx, value, revision):
        reservation = _get(_TransactionRecords(tx), "review_reservation", reservation_id)
        if (reservation["token"] != token or reservation["released"] or reservation["worker"] is not None
                or reservation["state"] != "reserved" or reservation["launch"] is None):
            raise ResearchError("review_worker_reconciliation_required", "The launch claim no longer permits a worker")
        return _transition(tx, reservation, revision, worker=identity)
    return _mutate_current(store, "review.worker_ready", {"reservation_id": reservation_id, "worker": identity}, apply,
                           request_id="review-worker-ready:" + reservation_id)["result"]


def _start_worker(store, authority):
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        reservation = _get(records, "review_reservation", authority.reservation_id)
        authority.verify(store, reservation)
        check_launch(records, "review.worker_running", reservation)
        plan = _get(records, "review_execution_plan", reservation["plan_id"])
        _transition(tx, reservation, revision, state="running")
        return plan
    return _mutate_current(store, "review.worker_running", {"reservation_id": authority.reservation_id}, apply,
                           request_id="review-worker-running:" + authority.reservation_id)["result"]


def _register_child(authority, process):
    """The owner of a call records the process group of a transport child (its guardian) before that child starts its
    program. The record waits for a busy store here, before the transport starts the time limit of the program."""
    is_call = isinstance(authority, _CallAuthority)
    reservation_id, token = authority.reservation_id, authority.token
    identity = _identity(process.pid)
    if identity["pid"] != identity["pgid"]:
        raise ResearchError("review_worker_reconciliation_required", "A transport child must own its process group")
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        if is_call:
            current = _get(records, "review_pool_membership", reservation_id)
            check_launch(records, "review.call_child", current, token=token, read_identity=_identity)
            tx.put("review_pool_membership", reservation_id, dict(current, children=current["children"] + [identity], recorded_revision=revision))
            return current
        reservation = _get(records, "review_reservation", reservation_id)
        check_launch(records, "review.worker_child", reservation, token=token, read_identity=_identity)
        return _transition(tx, reservation, revision, children=reservation["children"] + [identity])
    # Opening the store is part of each attempt, so a store that is busy at that moment is waited for as well.
    retry_finishing_write(lambda: _mutate_current(Store(authority.root),
        "review.call_child" if is_call else "review.worker_child", {"reservation_id": reservation_id, "child": identity},
        apply, request_id=("review-call-child:" if is_call else "review-worker-child:") + digest([reservation_id, identity])),
        CALL_CHILD_STORE_BUSY_MSG if is_call else WORKER_CHILD_STORE_BUSY_MSG, kept={"reservation_id": reservation_id},
        codes=LOCK_RETRY_CODES)
    return identity


def _stop_unreleased_child(process):
    """End a guardian that never received its release, so that it starts no program."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    process.wait()


class _ChildRecordRefused(BaseException):
    """A refused record of a transport child. A transport turns a ResearchError raised while it sends into a failed
    observation, so the refusal passes the transports in this form, and _own_transport_processes raises the
    ResearchError again where its block ends."""

    def __init__(self, error):
        super().__init__(str(error))
        self.error = error


@contextmanager
def _own_transport_processes(authority):
    """Guard local transport launches for a detached worker or an owned inline call. The owner records each transport
    child before the child starts its program; a refused record leaves this block as its ResearchError, and no program
    of that child runs."""
    original = subprocess.Popen
    def owned_popen(command, *args, **kwargs):
        if isinstance(authority, _CallAuthority):
            # Inline visual calls also invoke renderers that omit this option.
            kwargs.setdefault("start_new_session", True)
        if not isinstance(command, (list, tuple)) or kwargs.get("shell") or not kwargs.get("start_new_session"):
            raise ResearchError("review_worker_reconciliation_required", "Owned transports require an explicit new process group")
        # The guardian starts its program after one byte on this pipe, which the owner writes after the record. Only
        # the owner holds the write end, and the guardian inherits the read end alone.
        release, grant = os.pipe()
        try:
            inherited = tuple(kwargs.get("pass_fds", ()))
            kwargs["pass_fds"] = tuple(dict.fromkeys(inherited + (authority.descriptor, release)))
            guardian = [sys.executable, str(Path(__file__).resolve()), "--child", str(authority.root),
                        authority.reservation_id, str(authority.descriptor), str(release), "--"] + list(command)
            process = original(guardian, *args, **kwargs)
            try:
                _register_child(authority, process)
                os.write(grant, b"1")
            except ResearchError as error:
                _stop_unreleased_child(process)
                raise _ChildRecordRefused(error) from error
            except BaseException:
                _stop_unreleased_child(process)
                raise
            return process
        finally:
            os.close(release)
            os.close(grant)
    subprocess.Popen = owned_popen
    try:
        yield
    except _ChildRecordRefused as refused:
        raise refused.error
    finally:
        subprocess.Popen = original


def _await_release(root, reservation_id, descriptor, release):
    """The guardian keeps the inherited ownership lock and starts its program only after its owner recorded it."""
    with checked_parent(root, _directory(reservation_id) + "/worker.lock") as (directory, name):
        lock_info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    descriptor_info = os.fstat(descriptor)
    if (lock_info.st_dev, lock_info.st_ino) != (descriptor_info.st_dev, descriptor_info.st_ino):
        raise ResearchError("review_worker_reconciliation_required", "Transport child lost its ownership lock")
    granted = os.read(release, 1)
    os.close(release)
    if granted != b"1":
        raise ResearchError("review_worker_reconciliation_required", "The owner of this transport child did not record it")


def _observe_live(store, route, request):
    from .review_protocol import _observed
    return _observed(store.build_artifact_store(), route, request, None)


def run_worker(root, reservation_id, token, descriptor):
    """Worker entry point: handshake, observe once, retry capture only, then exit. Each record of the worker waits for
    a busy store; the worker writes a refusal after the wait to its log."""
    kept = {"reservation_id": reservation_id}
    store = retry_finishing_write(lambda: Store(Path(root)), WORKER_READY_STORE_BUSY_MSG, kept=kept,
                                  codes=LOCK_RETRY_CODES)
    authority = _WorkerAuthority(store, reservation_id, token, descriptor)
    try:
        retry_finishing_write(lambda: _bind_worker(store, reservation_id, token, authority.identity),
                              WORKER_READY_STORE_BUSY_MSG, kept=kept, codes=LOCK_RETRY_CODES)
        if sys.stdin.readline().strip() != token:
            return
        plan = retry_finishing_write(lambda: _start_worker(store, authority), WORKER_RUNNING_STORE_BUSY_MSG, kept=kept,
                                     codes=LOCK_RETRY_CODES)
        route = plan["route"]
        request = json.loads(store.build_artifact_store().read(plan["request"]))
        try:
            with _own_transport_processes(authority):
                observation = _observe_live(store, route, request)
        except Exception as error:
            observation = {"status": "failed", "request": plan["request"], "response": None, "output": None,
                           "error": {"code": error.code if isinstance(error, ResearchError) else "review_worker_failed",
                                     "message": str(error)}}
        retry_finishing_write(lambda: capture_observation(store, reservation_id, observation,
            request_id="review-capture:" + reservation_id, _authority=authority), WORKER_CAPTURE_STORE_BUSY_MSG,
            kept=kept, codes=LOCK_RETRY_CODES)
    finally:
        os.close(descriptor)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--child":
        if len(argv) < 7 or argv[5] != "--":
            raise ResearchError("invalid_input", "Malformed owned transport launch")
        descriptor = int(argv[3])
        _await_release(Path(argv[1]), argv[2], descriptor, int(argv[4]))
        # The descriptor intentionally remains inherited by the CLI process.
        os.set_inheritable(descriptor, True)
        os.execvpe(argv[6], argv[6:], os.environ)
    else:
        if len(argv) != 4:
            raise ResearchError("invalid_input", "Worker needs root, reservation, token and ownership descriptor")
        # The worker is a command of its own: its store access waits for a busy store within the store wait.
        with wait_for_store_in_command():
            run_worker(Path(argv[0]), argv[1], argv[2], int(argv[3]))
