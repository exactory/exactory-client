"""Shared ownership and idempotent application of native journal intents."""

from contextlib import contextmanager
import errno
import fcntl
import json
import os
import stat

from . import schema as s
from .errors import SearchError
from .problem_records import audit_journal_append
from .storage import safe_path


def acquire_journal_ownership(controller, node_id):
    """Normal writers and recovery use the same nonblocking per-node lock."""
    path = safe_path(controller.store.root, "journal-" + node_id + ".lock")
    s.require(not path.is_symlink(), "Journal ownership path cannot be a symlink", "unsafe_path")
    descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    ownership = os.fdopen(descriptor, "r+b")
    try:
        s.require(stat.S_ISREG(os.fstat(descriptor).st_mode), "Journal ownership must be a regular file", "unsafe_path")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise SearchError("journal_owned", "The journal is owned by a live writer or recovery operation") from error
            raise
        return ownership
    except BaseException:
        ownership.close()
        raise


@contextmanager
def journal_ownership(controller, node_id):
    ownership = acquire_journal_ownership(controller, node_id)
    try:
        yield ownership
    finally:
        ownership.close()


def apply_journal_intent(controller, state, reservation_id, content, line=None):
    """The caller owns the journal through this write and its durable ack."""
    from .admission import reference
    from .render import replace_text
    move = reference(state["service"]["moves"], reservation_id, "reserved move")
    intent = reference(state["service"]["legacy_intents"], reservation_id, "journal intent")
    node = state["nodes"][move["node_id"]]
    before, after = audit_journal_append(controller, node, move, intent, content)
    if line is not None:
        s.require(after == before + (json.dumps(line) + "\n").encode("utf-8"),
                  "Native journal line differs from its durable intent", "recovery_conflict")
    path = safe_path(controller.root, node["attack_slug"] + "/journal.jsonl")
    actual = path.read_bytes()
    s.require(actual in {before, after}, "Journal conflicts with its durable intent", "recovery_conflict")
    if actual == before:
        replace_text(path, after.decode("utf-8"))


def append_managed_journal(args, line):
    context = getattr(args, "_journal_context", None)
    if context is None:
        return False
    s.require(not context["journal_ownership"].closed, "Journal ownership was released before append", "journal_owned")
    controller = context["controller"]
    apply_journal_intent(controller, controller.status(), context["reservation_id"], controller.store, line)
    return True
