"""Normalize caller-selected workspace files once at the CLI payload boundary."""

import copy
from itertools import chain
import os
from pathlib import Path

from .artifacts import _Workspace, _relative_parts
from .errors import ResearchError
from .workspace import find_entry_kind


PAYLOAD_PATH_MESSAGE = (
    'A file path in this payload was refused. A payload names a file or a directory of the workspace by a '
    'workspace-relative path or by an absolute path inside the workspace. In either form, the path must have '
    "no empty, '.' or '..' component and no backslash. No component can be a symbolic link. Give the path from "
    'the workspace root. If the file is outside the workspace, copy it into the workspace first.')
PAYLOAD_FILE_FIELDS = {
    'artifact': (('path',),),
    'adopt': (('files', '*'),),
    'import-response': (('response_file',),),
    'import-oai-cohort': (('pages', '*', 'response_file'),),
    'manuscript': (('files', '*'),),
    'problem-posted': (('export_directory',),),
    'gate-override': (('instruction',),),
}


def normalize_payload_path(root, value, *, check_filesystem=True):
    """Return the canonical relative spelling, preserving components until checked."""
    try:
        if not isinstance(value, str):
            raise ResearchError('unsafe_path', PAYLOAD_PATH_MESSAGE)
        if value.startswith('/'):
            _relative_parts(value[1:])
            selected = Path(value)
            workspace = _Workspace(root).root
            # Resolve only workspace ancestors, retaining every component inside
            # the workspace for the existing link checks below.
            for parent in reversed(selected.parents):
                if (parent.name == workspace.name
                        and resolve_payload_ancestor(parent.parent, workspace) == workspace.parent):
                    value = selected.relative_to(parent).as_posix()
                    break
            else:
                raise ResearchError('unsafe_path', PAYLOAD_PATH_MESSAGE)
        parts = _relative_parts(value)
        if check_filesystem:
            for index in range(1, len(parts) + 1):
                kind = find_entry_kind(root, '/'.join(parts[:index]))
                if kind == 'link':
                    raise ResearchError('unsafe_path', PAYLOAD_PATH_MESSAGE)
                if kind == 'absent':
                    break
        return value
    except ResearchError as error:
        if error.code != 'unsafe_path':
            raise
        raise ResearchError('unsafe_path', PAYLOAD_PATH_MESSAGE) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise ResearchError('unsafe_path', PAYLOAD_PATH_MESSAGE) from error


def resolve_payload_ancestor(path, workspace, links=()):
    """Resolve an ancestor while refusing links inside the workspace, including in link targets."""
    if path == path.parent:
        return path.resolve()
    parent = resolve_payload_ancestor(path.parent, workspace, links)
    if path.name == '..':
        return parent.parent
    candidate = parent / path.name
    if candidate.is_symlink():
        inside_workspace = workspace in candidate.parents
        if not inside_workspace and workspace.exists():
            # Directory identity also covers case aliases on macOS volumes.
            inside_workspace = any(ancestor.samefile(workspace) for ancestor in candidate.parents)
        if candidate == workspace or inside_workspace or candidate in links:
            raise ResearchError('unsafe_path', PAYLOAD_PATH_MESSAGE)
        target = Path(os.readlink(candidate))
        if not target.is_absolute():
            target = parent / target
        return resolve_payload_ancestor(target, workspace, links + (candidate,))
    return candidate


def normalize_payload_paths(root, command, payload, *, check_filesystem=True):
    """Normalize file fields and immutable artifact references, leaving other text alone."""
    selected = select_payload_inputs(command, payload)
    result = copy.deepcopy(selected)
    for parent, key in iterate_payload_paths(command, result):
        parent[key] = normalize_payload_path(root, parent[key], check_filesystem=check_filesystem)
    if selected is not payload:
        result['context'] = dict(payload['context'], **result['context'])
    return result


def select_payload_inputs(command, payload):
    """Retain the existing exact-record boundary for a repeated publication bundle."""
    if command == 'review-assignment':
        from .review_protocol import drop_repeated_bundle
        return drop_repeated_bundle(payload)
    return payload


def iterate_payload_paths(command, payload):
    """Locate caller file fields, excluding sandbox names and foreign workspace identities."""
    payload = select_payload_inputs(command, payload)
    def select_fields(value, keys):
        if isinstance(value, dict):
            selected = list(value) if keys[0] == '*' else [keys[0]]
            for key in selected:
                if key in value:
                    if len(keys) == 1:
                        yield value, key
                    else:
                        yield from select_fields(value[key], keys[1:])
        elif isinstance(value, list) and keys[0] == '*':
            for index, item in enumerate(value):
                if len(keys) == 1:
                    yield value, index
                else:
                    yield from select_fields(item, keys[1:])

    def select_references(value):
        if isinstance(value, dict):
            if {'path', 'sha256', 'size', 'media_type'} <= value.keys():
                yield value, 'path'
            for child in value.values():
                yield from select_references(child)
        elif isinstance(value, list):
            for child in value:
                yield from select_references(child)

    fields = chain.from_iterable(select_fields(payload, keys)
                                 for keys in PAYLOAD_FILE_FIELDS.get(command, ()))
    for parent, key in chain(fields, select_references(payload)):
        if isinstance(parent[key], str):
            yield parent, key
