"""Bounded admission of new import responses and command payload files."""

from contextlib import contextmanager, ExitStack
import os
import stat

from .errors import ResearchError
from .artifacts import _filesystem_error
from .workspace import checked_parent


MAX_IMPORT_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_IMPORT_PAYLOAD_BYTES = 8 * 1024 * 1024
_READ_CHUNK_BYTES = 65536


def validate_import_size(size, *, input_kind, limit_bytes=None):
    """Return the byte allowance or refuse an oversized new input."""
    if input_kind not in ("response", "payload"):
        raise ResearchError("invalid_input", "Import input kind must be response or payload")
    if limit_bytes is None:
        limit_bytes = MAX_IMPORT_RESPONSE_BYTES if input_kind == "response" else MAX_IMPORT_PAYLOAD_BYTES
    if type(limit_bytes) is not int or limit_bytes < 0:
        raise ResearchError("invalid_input", "Import byte allowance must be a nonnegative integer")
    if size > limit_bytes:
        raise ResearchError("import_input_too_large", "Import " + input_kind + " exceeds the " + str(limit_bytes)
                            + " byte allowance; observed at least " + str(size) + " bytes",
                            {"input_kind": input_kind, "limit_bytes": limit_bytes, "observed_bytes_at_least": size})
    return limit_bytes


@contextmanager
def open_import_descriptor(path, *, workspace_root=None):
    """Hold one input descriptor, checking workspace traversal without changing import refusals."""
    try:
        with ExitStack() as scope:
            if workspace_root is None:
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            else:
                directory, name = scope.enter_context(checked_parent(workspace_root, path))
                descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            try:
                yield descriptor
            finally:
                os.close(descriptor)
    except OSError as error:
        if workspace_root is not None:
            checked = _filesystem_error(error)
            if checked.code == 'unsafe_path':
                raise checked from error
        raise


def read_import_input(path, *, input_kind, limit_bytes=None, workspace_root=None):
    """Read a regular file through one descriptor under a byte allowance."""
    limit_bytes = validate_import_size(0, input_kind=input_kind, limit_bytes=limit_bytes)
    try:
        with open_import_descriptor(path, workspace_root=workspace_root) as descriptor:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ResearchError("invalid_input", "Import " + input_kind + " must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                return read_import_stream(source, input_kind=input_kind, limit_bytes=limit_bytes,
                                          observed_size=info.st_size)
    except OSError as error:
        raise ResearchError("invalid_input", "Import " + input_kind + " could not be read",
                            {"input_kind": input_kind}) from error


def read_import_stream(source, *, input_kind, limit_bytes, observed_size):
    """Bound an already opened response, including growth after its initial stat."""
    validate_import_size(observed_size, input_kind=input_kind, limit_bytes=limit_bytes)
    data = bytearray()
    while True:
        block = source.read(min(_READ_CHUNK_BYTES, limit_bytes - len(data) + 1))
        if not block:
            return bytes(data)
        data.extend(block)
        validate_import_size(len(data), input_kind=input_kind, limit_bytes=limit_bytes)
