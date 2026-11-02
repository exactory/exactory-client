"""Bounded admission of new import responses and command payload files."""

import os
import stat

from .errors import ResearchError


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


def read_import_input(path, *, input_kind, limit_bytes=None):
    """Read a regular file through one descriptor under a byte allowance."""
    limit_bytes = validate_import_size(0, input_kind=input_kind, limit_bytes=limit_bytes)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ResearchError("invalid_input", "Import " + input_kind + " must be a regular file")
            validate_import_size(info.st_size, input_kind=input_kind, limit_bytes=limit_bytes)
            data = bytearray()
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                while True:
                    block = source.read(min(_READ_CHUNK_BYTES, limit_bytes - len(data) + 1))
                    if not block:
                        break
                    data.extend(block)
                    validate_import_size(len(data), input_kind=input_kind, limit_bytes=limit_bytes)
            return bytes(data)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ResearchError("invalid_input", "Import " + input_kind + " could not be read",
                            {"input_kind": input_kind}) from error
