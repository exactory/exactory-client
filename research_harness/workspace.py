"""Checked human inputs and disposable projections of authoritative records."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import uuid

from .artifacts import _Workspace, _regular_file, _relative_parts, _filesystem_error
from .errors import ResearchError
from .storage import _canonical


def strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
        _canonical(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ResearchError("invalid_json", "Expected finite JSON without duplicate fields") from error


def find_workspace(start=None, *, required=True):
    start = Path(start or Path.cwd()).absolute()
    for directory in (start, *start.parents):
        marker = directory / ".exactory"
        if any((marker / name).exists() or (marker / name).is_symlink()
               for name in ("research.sqlite3", "study.json", "draft.json")):
            return directory
    if required:
        raise ResearchError("migration_required", "Research readiness requires a current workspace; run exactory-research init or adopt")
    return None


@contextmanager
def checked_parent(root, relative, *, create=False):
    parts = _relative_parts(relative)
    workspace = _Workspace(root)
    if len(parts) == 1:
        boundary = _Workspace(workspace.root.parent)
        with boundary.directory(workspace.root.name, create=create) as descriptor:
            yield descriptor, parts[-1]
    else:
        with workspace.directory("/".join(parts[:-1]), create=create) as descriptor:
            yield descriptor, parts[-1]


def read_file(root, relative):
    try:
        with checked_parent(root, relative) as (directory, name):
            with os.fdopen(_regular_file(directory, name), "rb") as source:
                return source.read()
    except FileNotFoundError as error:
        raise ResearchError("artifact_missing", "Required workspace file is missing", {"path": relative}) from error
    except OSError as error:
        raise _filesystem_error(error) from error


def write_projection(root, relative, data):
    """Atomically replace a disposable view; never follow an existing symlink."""
    with checked_parent(root, relative, create=True) as (directory, name):
        temporary = ".projection-" + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


def json_projection(root, relative, value):
    write_projection(root, relative, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
