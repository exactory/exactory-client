"""Atomic, content-addressed objects with checked workspace-relative paths.

Objects are published only after all bytes have been written and synced. An
existing address is verified, never overwritten. Directory descriptors and
O_NOFOLLOW keep object traversal within the selected workspace on macOS/Linux.
Read-only operations never create directories or files.
"""

import errno
import hashlib
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import ResearchError


_OBJECT_DIRECTORY = "research/sources/objects"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = r"[-!#$%&'*+.^_`|~0-9A-Za-z]+"
_MEDIA_TYPE = re.compile(_TOKEN + "/" + _TOKEN + r"(?:;[\x20-\x7e]+)?\Z")


def _filesystem_error(error: OSError) -> ResearchError:
    if error.errno in (errno.ELOOP, errno.ENOTDIR):
        return ResearchError("unsafe_path", "Research paths must not contain symlinks or non-directories")
    return ResearchError("storage_io", "Research filesystem operation failed", {"errno": error.errno})


def _relative_parts(relative: str):
    if (not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ResearchError("unsafe_path", "Expected a canonical workspace-relative path")
    return relative.split("/")


class _Workspace:
    """Shared checked filesystem boundary for SQLite and content objects."""

    def __init__(self, root: Path):
        try:
            supplied = Path(root).absolute()
            # Resolve the caller-selected parent, including macOS /var aliases,
            # but retain the final component so a symlinked workspace is rejected.
            self.root = supplied.parent.resolve() / supplied.name
        except (TypeError, ValueError, OSError, RuntimeError) as error:
            raise ResearchError("unsafe_path", "Invalid research workspace path") from error

    @contextmanager
    def directory(self, relative: str, *, create: bool = False):
        parts = _relative_parts(relative)
        descriptor = None
        try:
            if create:
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for name in parts:
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        except FileNotFoundError:
            raise
        except OSError as error:
            raise _filesystem_error(error) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _regular_file(directory: int, name: str) -> int:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ResearchError("unsafe_path", "Research content must be a regular file")
    return descriptor


def _media_type(value):
    if not isinstance(value, str) or not _MEDIA_TYPE.fullmatch(value):
        raise ResearchError("invalid_input", "Expected a media type without control characters")


def _verify_object(directory: int, reference: dict) -> bytes:
    descriptor = _regular_file(directory, reference["sha256"])
    with os.fdopen(descriptor, "rb") as source:
        data = source.read()
    if len(data) != reference["size"] or hashlib.sha256(data).hexdigest() != reference["sha256"]:
        raise ResearchError("artifact_corrupt", "Stored artifact does not match its size and SHA-256",
                            {"path": reference["path"]})
    return data


class ArtifactStore:
    """Store immutable bytes and return portable, independently checked references."""

    def __init__(self, root: Path):
        self._workspace = _Workspace(root)
        self.root = self._workspace.root

    def put(self, data: bytes, media_type: str) -> dict:
        if not isinstance(data, bytes):
            raise ResearchError("invalid_input", "Artifact data must be bytes")
        _media_type(media_type)
        digest = hashlib.sha256(data).hexdigest()
        reference = {"sha256": digest, "path": _OBJECT_DIRECTORY + "/" + digest,
                     "size": len(data), "media_type": media_type}
        with self._workspace.directory(_OBJECT_DIRECTORY, create=True) as directory:
            try:
                _verify_object(directory, reference)
                return reference
            except FileNotFoundError:
                pass
            temporary = ".object-" + uuid.uuid4().hex
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "wb") as target:
                    target.write(data)
                    target.flush()
                    os.fchmod(target.fileno(), 0o444)
                    os.fsync(target.fileno())
                try:
                    os.link(temporary, digest, src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                except FileExistsError:
                    _verify_object(directory, reference)
            finally:
                os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        return reference

    def read(self, ref: dict) -> bytes:
        if not isinstance(ref, dict) or not {"sha256", "path", "size", "media_type"} <= ref.keys():
            raise ResearchError("invalid_input", "Artifact reference is missing required fields")
        if not isinstance(ref["sha256"], str) or not _SHA256.fullmatch(ref["sha256"]):
            raise ResearchError("invalid_input", "Artifact SHA-256 must have 64 lowercase hexadecimal digits")
        if type(ref["size"]) is not int or ref["size"] < 0:
            raise ResearchError("invalid_input", "Artifact size must be a nonnegative integer")
        _media_type(ref["media_type"])
        _relative_parts(ref["path"])
        if ref["path"] != _OBJECT_DIRECTORY + "/" + ref["sha256"]:
            raise ResearchError("unsafe_path", "Artifact path must identify its content-addressed object")
        try:
            with self._workspace.directory(_OBJECT_DIRECTORY) as directory:
                return _verify_object(directory, ref)
        except FileNotFoundError as error:
            raise ResearchError("artifact_missing", "Artifact has not been stored",
                                {"path": ref["path"]}) from error
