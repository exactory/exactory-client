"""Durable atomic publication of native files; callers retain their ownership locks."""

from contextlib import contextmanager
import os
from pathlib import Path
import uuid


@contextmanager
def atomic_output(path, *, mode=0o400, directory_fd=None):
    """Publish complete bytes with their final mode, keeping the old target on failure.

    A supplied directory descriptor comes from the caller's checked path boundary.
    Writable modes are reserved for files that an agent or workload edits by design.
    """
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            yield stream
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        if directory_fd is None:
            os.replace(str(temporary), str(path))
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        else:
            os.replace(temporary, path, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def write_atomic(path, data, *, mode=0o400, directory_fd=None):
    with atomic_output(path, mode=mode, directory_fd=directory_fd) as stream:
        stream.write(data)
