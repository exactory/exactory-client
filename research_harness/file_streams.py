"""Bounded copies and privately owned, verified file snapshots."""

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import io
import os
import stat
import tempfile
from typing import BinaryIO

from .errors import ResearchError


_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class FrozenFile:
    stream: BinaryIO
    size: int
    sha256: str
    md5: str


def _check_file_size(source, expected):
    if expected is None:
        return
    # A wrapper's descriptor can describe encoded storage rather than read() bytes.
    stream = source
    if type(source) in (io.BufferedReader, io.BufferedRandom):
        source = source.raw
    if type(source) is not io.FileIO:
        return
    information = os.fstat(source.fileno())
    if (stat.S_ISREG(information.st_mode)
            and max(0, information.st_size - stream.tell()) != expected["size"]):
        raise ResearchError("artifact_corrupt", "Stored artifact does not match its size and SHA-256",
                            {"path": expected["path"]})


def _copy_stream(source, target=None, expected=None):
    """Hash a bounded copy; callers validate descriptors and own the I/O boundary."""
    _check_file_size(source, expected)
    size, sha256, md5 = 0, hashlib.sha256(), hashlib.md5()
    while True:
        try:
            data = source.read(_CHUNK_SIZE)
        except InterruptedError:
            continue
        if not isinstance(data, bytes) or len(data) > _CHUNK_SIZE:
            raise ResearchError("invalid_input", "Expected a bounded binary stream")
        if not data:
            break
        size += len(data)
        if expected is not None and size > expected["size"]:
            raise ResearchError("artifact_corrupt", "Stored artifact does not match its size and SHA-256",
                                {"path": expected["path"]})
        sha256.update(data)
        md5.update(data)
        if target is not None:
            remaining = memoryview(data)
            while remaining:
                try:
                    written = target.write(remaining)
                except InterruptedError:
                    continue
                if type(written) is not int or not 0 < written <= len(remaining):
                    raise OSError(errno.EIO, "File copy did not make write progress")
                remaining = remaining[written:]
    result = {"size": size, "sha256": sha256.hexdigest(), "md5": md5.hexdigest()}
    if expected is not None and (size != expected["size"] or result["sha256"] != expected["sha256"]):
        raise ResearchError("artifact_corrupt", "Stored artifact does not match its size and SHA-256",
                            {"path": expected["path"]})
    return result


@contextmanager
def freeze_file(source, expected=None):
    """Yield a seekable private copy only after its complete identity is checked."""
    # Imported at invocation time because the artifact boundary also uses these copies.
    from .artifacts import _artifact_operation, validate_reference
    if expected is not None:
        validate_reference(expected)
    with _artifact_operation():
        _check_file_size(source, expected)
        stream = tempfile.TemporaryFile(mode="w+b")
    try:
        with _artifact_operation():
            identity = _copy_stream(source, stream, expected)
            stream.flush()
            stream.seek(0)
            if _copy_stream(stream) != identity:
                raise ResearchError("artifact_corrupt", "Frozen copy does not match its source")
            stream.seek(0)
        yield FrozenFile(stream=stream, **identity)
    finally:
        stream.close()
