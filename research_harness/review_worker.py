"""Owned detached review worker and process-group launch guardians.

Lock order: controller ownership, worker ownership, short Store transactions.
The inherited worker lock covers the spawn/identity handshake gap. Each local
CLI passes through a guardian that records its group before exec, retaining the
lock until it exits. Disposable files never authenticate a reviewer response.
"""

from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
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
from .finishing import retry_store_write
from .review_task_execution import (_check_references, _get, _mutate_current, _TransactionRecords,
                                     _transition, capture_observation)
from .storage import Store
from .workspace import checked_parent


_CAPTURE_ATTEMPTS = 600
_CAPTURE_SECONDS = 600.0


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
    current = _get(store.snapshot()["records"], "review_pool_membership", lifecycle_id)
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
    """Invocation-local ownership; no response or recovery state lives in files."""

    def __init__(self, store, operation, lifecycle_id, descriptor, ownership):
        self.root, self.operation, self.reservation_id = store.root, operation, lifecycle_id
        self.descriptor, self.token, self.identity = descriptor, uuid.uuid4().hex, _identity()
        self._ownership = ownership

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
        reservation = _get(_TransactionRecords(tx), "review_reservation", reservation_id)
        if reservation["released"] or reservation["launch"] is not None or reservation["state"] != "reserved":
            raise ResearchError("review_invocation_pending", "Reconcile the original worker instead of launching twice")
        _check_references(_TransactionRecords(tx), _get(_TransactionRecords(tx), "review_execution_plan", reservation["plan_id"]))
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


def launch_worker(store, reservation_id, *, handshake_timeout=5.0):
    """Launch once and return Popen after the durable identity/token handshake."""
    if not isinstance(handshake_timeout, (int, float)) or not 0 < handshake_timeout <= 60:
        raise ResearchError("invalid_input", "Worker handshake timeout must be positive and at most 60 seconds")
    with _owner(store.root, reservation_id):
        current = _get(store.snapshot()["records"], "review_reservation", reservation_id)
        if current["launch"] is not None or current["state"] != "reserved" or current["released"]:
            raise ResearchError("review_invocation_pending", "Resume the original worker reservation instead of relaunching")
        with _owner(store.root, reservation_id, worker=True) as descriptor:
            reservation = _claim_launch(store, reservation_id)
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
                    current = _get(store.snapshot()["records"], "review_reservation", reservation_id)
                    identity = current["worker"]
                    if identity is not None:
                        if identity["pid"] != process.pid or identity["pgid"] != process.pid or identity != _identity(process.pid):
                            raise ResearchError("review_worker_reconciliation_required", "The detached worker did not establish its process identity")
                        _authorize_launch(store, reservation_id, identity)
                        process.stdin.write((reservation["token"] + "\n").encode())
                        return process
                    time.sleep(.01)
                raise ResearchError("review_worker_reconciliation_required", "The worker launch handshake did not complete")
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
        if (reservation["released"] or reservation["state"] != "reserved" or not reservation["launch"]["authorized"]):
            raise ResearchError("review_worker_reconciliation_required", "The worker's launch was not authorized")
        plan = _get(records, "review_execution_plan", reservation["plan_id"])
        _check_references(records, plan)
        _transition(tx, reservation, revision, state="running")
        return plan
    return _mutate_current(store, "review.worker_running", {"reservation_id": authority.reservation_id}, apply,
                           request_id="review-worker-running:" + authority.reservation_id)["result"]


def _register_child(root, reservation_id, token, descriptor, *, call=False):
    """The guardian commits its identity before executing any external command."""
    store = Store(root)
    identity = _identity()
    if identity["pid"] != identity["pgid"]:
        raise ResearchError("review_worker_reconciliation_required", "A transport child must own its process group")
    # Verify the inherited lock's inode before registering the child.
    with checked_parent(root, _directory(reservation_id) + "/worker.lock") as (directory, name):
        lock_info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    descriptor_info = os.fstat(descriptor)
    if (lock_info.st_dev, lock_info.st_ino) != (descriptor_info.st_dev, descriptor_info.st_ino):
        raise ResearchError("review_worker_reconciliation_required", "Transport child lost its ownership lock")
    def apply(tx, value, revision):
        if call:
            current = _get(_TransactionRecords(tx), "review_pool_membership", reservation_id)
            if (current["released"] or current["token"] != token
                    or _identity(current["owner"]["pid"]) != current["owner"]):
                raise ResearchError("review_worker_reconciliation_required", "The call's owner is no longer live")
            tx.put("review_pool_membership", reservation_id, dict(current, children=current["children"] + [identity], recorded_revision=revision))
            return current
        reservation = _get(_TransactionRecords(tx), "review_reservation", reservation_id)
        if (reservation["released"] or reservation["state"] != "running" or reservation["token"] != token
                or _identity(reservation["worker"]["pid"]) != reservation["worker"]):
            raise ResearchError("review_worker_reconciliation_required", "The transport child's worker is no longer live")
        return _transition(tx, reservation, revision, children=reservation["children"] + [identity])
    retry_store_write(lambda: _mutate_current(store, "review.call_child" if call else "review.worker_child",
        {"reservation_id": reservation_id, "child": identity}, apply,
        request_id=("review-call-child:" if call else "review-worker-child:") + digest([reservation_id, identity])))
    return identity


@contextmanager
def _own_transport_processes(authority):
    """Guard local transport launches for a detached worker or an owned inline call."""
    original = subprocess.Popen
    def owned_popen(command, *args, **kwargs):
        if isinstance(authority, _CallAuthority):
            # Inline visual calls also invoke renderers that omit this option.
            kwargs.setdefault("start_new_session", True)
        if not isinstance(command, (list, tuple)) or kwargs.get("shell") or not kwargs.get("start_new_session"):
            raise ResearchError("review_worker_reconciliation_required", "Owned transports require an explicit new process group")
        inherited = tuple(kwargs.get("pass_fds", ()))
        kwargs["pass_fds"] = tuple(dict.fromkeys(inherited + (authority.descriptor,)))
        guardian = [sys.executable, str(Path(__file__).resolve()),
                    "--call-child" if isinstance(authority, _CallAuthority) else "--child", str(authority.root),
                    authority.reservation_id, authority.token, str(authority.descriptor), "--"] + list(command)
        return original(guardian, *args, **kwargs)
    subprocess.Popen = owned_popen
    try:
        yield
    finally:
        subprocess.Popen = original


def _observe_live(store, route, request):
    from .review_protocol import _observed
    return _observed(store.build_artifact_store(), route, request, None)


def run_worker(root, reservation_id, token, descriptor):
    """Worker entry point: handshake, observe once, retry capture only, then exit."""
    store = Store(Path(root))
    authority = _WorkerAuthority(store, reservation_id, token, descriptor)
    try:
        retry_store_write(lambda: _bind_worker(store, reservation_id, token, authority.identity))
        if sys.stdin.readline().strip() != token:
            return
        plan = retry_store_write(lambda: _start_worker(store, authority))
        route = plan["route"]
        request = json.loads(store.build_artifact_store().read(plan["request"]))
        try:
            with _own_transport_processes(authority):
                observation = _observe_live(store, route, request)
        except Exception as error:
            observation = {"status": "failed", "request": plan["request"], "response": None, "output": None,
                           "error": {"code": error.code if isinstance(error, ResearchError) else "review_worker_failed",
                                     "message": str(error)}}
        retry_store_write(lambda: capture_observation(store, reservation_id, observation,
            request_id="review-capture:" + reservation_id, _authority=authority),
            attempts=_CAPTURE_ATTEMPTS, seconds=_CAPTURE_SECONDS)
    finally:
        os.close(descriptor)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("--child", "--call-child"):
        if len(argv) < 7 or argv[5] != "--":
            raise ResearchError("invalid_input", "Malformed owned transport launch")
        root, reservation_id, token, descriptor = Path(argv[1]), argv[2], argv[3], int(argv[4])
        _register_child(root, reservation_id, token, descriptor, call=argv[0] == "--call-child")
        # The descriptor intentionally remains inherited by the CLI process.
        os.set_inheritable(descriptor, True)
        os.execvpe(argv[6], argv[6:], os.environ)
    else:
        if len(argv) != 4:
            raise ResearchError("invalid_input", "Worker needs root, reservation, token and ownership descriptor")
        run_worker(Path(argv[0]), argv[1], argv[2], int(argv[3]))
