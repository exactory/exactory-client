"""Retire generated problem exports when their source records cease to be current.

Source receipts are projection metadata outside the export: existing exported bytes,
manifest formats, and posted export digests retain their meaning.
"""

import hashlib
import json
import os

from .errors import ResearchError, describe_error_at
from .evidence import digest
from .evaluation import Evaluation
from .problem_claims import compute_problem_evidence_digest, compute_statement_digest, find_current_statement, find_latest, find_value
from .workspace import checked_parent, find_entry_kind, read_file, strict_json, write_projection


EXPORT_SOURCES_FOLDER = '.exactory/problem-export-sources'


def find_export_source_path(relative):
    return EXPORT_SOURCES_FOLDER + '/' + digest(relative) + '.json'


def record_export_sources(store_root, workspace_root, directory, files, source_digest):
    """Bind generated exports to source identities without adding bytes to the posted file set."""
    from .output_views import PROBLEM_EXPORTS_FOLDER
    from .problem_delivery import compute_export_digest
    try:
        relative = directory.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return
    if not relative.startswith(PROBLEM_EXPORTS_FOLDER + '/'):
        return
    receipt = dict(destination=relative, export_digest=compute_export_digest(files),
                   source_digest=source_digest)
    write_projection(store_root, find_export_source_path(relative),
                     (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode())


def read_problem_export(directory, records):
    """Recognize a complete generated export; leave foreign or edited folders alone."""
    from .problem_delivery import has_exported_files
    if directory.is_symlink() or find_entry_kind(directory, 'problem-export.json') != 'regular':
        return None
    manifest_bytes = read_file(directory, 'problem-export.json')
    try:
        manifest = strict_json(manifest_bytes)
    except ResearchError:
        return None
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), dict):
        return None
    base_fields = {'statement_id', 'statement_digest', 'area', 'formal_statement_language', 'files',
                   'decision_id', 'problem_evidence_digest'}
    bypassed = manifest.get('gate') == 'bypassed'
    if set(manifest) != base_fields | ({'gate', 'override_id'} if bypassed else set()):
        return None
    statement_id = manifest['statement_id']
    if not isinstance(statement_id, str) or statement_id not in records.get('problem_statement', {}):
        return None
    statement = records['problem_statement'][statement_id]
    formal = statement['formal_statement']
    if (manifest['statement_digest'] != compute_statement_digest(statement) or manifest['area'] != statement['area']
            or manifest['formal_statement_language'] != (None if formal is None else formal['language'])):
        return None
    source_kind, source_id = (('gate_override', manifest.get('override_id')) if manifest.get('gate') == 'bypassed'
                              else ('problem_decision', manifest.get('decision_id')))
    if not isinstance(source_id, str) or source_id not in records.get(source_kind, {}):
        return None
    required_files = {'title.txt', 'statement.md', 'criteria.md', 'motivation.md', 'state.md', 'citations.json'}
    if formal is not None:
        required_files.add('formal-statement.txt')
    if not required_files <= set(manifest['files']) <= required_files | ({'reviews.json'} if not bypassed else set()):
        return None
    files = {'problem-export.json': manifest_bytes}
    for name, expected in manifest['files'].items():
        if not name or '/' in name or name in ('.', '..', 'problem-export.json'):
            return None
        if find_entry_kind(directory, name) != 'regular':
            return None
        data = read_file(directory, name)
        if hashlib.sha256(data).hexdigest() != expected:
            return None
        files[name] = data
    for name, value in [('title.txt', statement['title']), ('statement.md', statement['statement']),
                        ('criteria.md', statement['resolution_criteria'])]:
        if files[name] != (value + '\n').encode('utf-8'):
            return None
    return (manifest, files) if has_exported_files(directory, files) else None


def build_current_export(records, artifacts, bypassed):
    """Build the current projection without reopening a post gate or altering any record."""
    from .problem_delivery import build_override_projection, build_projection, find_post_override
    statement, value = find_current_statement(records), find_value(records)
    if statement is None or value is None or value['statement_id'] != statement['id']:
        return None
    if bypassed:
        override = find_post_override(records)
        return None if override is None else build_override_projection(records, artifacts, override)[0]
    decision = find_latest(records, 'problem_decision')
    return None if decision is None or decision['action'] != 'post' else build_projection(records, artifacts)[0]


def archive_problem_exports(store_root, workspace_root, records, artifacts):
    """Move only complete generated exports, preserving conflicts and every historical byte."""
    from .output_views import PROBLEM_EXPORTS_FOLDER, PROBLEM_EXPORT_HISTORY_FOLDER
    from .problem_delivery import compute_export_digest
    folder = workspace_root / PROBLEM_EXPORTS_FOLDER
    with describe_error_at(PROBLEM_EXPORTS_FOLDER):
        if not folder.is_dir() or find_entry_kind(workspace_root, PROBLEM_EXPORTS_FOLDER) != 'other':
            return {}
    artifacts = Evaluation.of(records, artifacts)
    current, errors = {}, {}
    source_digest = None
    for path in sorted(folder.rglob('problem-export.json')):
        directory = path.parent
        relative = directory.relative_to(workspace_root).as_posix()
        try:
            exported = read_problem_export(directory, records)
            if exported is None:
                continue
            manifest, files = exported
            bypassed = manifest.get('gate') == 'bypassed'
            if bypassed not in current:
                try:
                    current[bypassed] = build_current_export(records, artifacts, bypassed)
                except ResearchError:
                    # An export with unavailable source evidence has lost its standing.
                    current[bypassed] = None
            stale = files != current[bypassed]
            receipt_path = find_export_source_path(relative)
            if not stale:
                if source_digest is None:
                    source_digest = compute_problem_evidence_digest(records, artifacts)
                if find_entry_kind(store_root, receipt_path) == 'regular':
                    receipt = strict_json(read_file(store_root, receipt_path))
                    stale = (receipt['export_digest'] != compute_export_digest(files)
                             or receipt['source_digest'] != source_digest)
            if stale:
                history = workspace_root / PROBLEM_EXPORT_HISTORY_FOLDER / directory.relative_to(folder)
                destination, number = history, 1
                while destination.exists() or destination.is_symlink():
                    if read_problem_export(destination, records) != exported:
                        errors[relative] = 'output_view_conflict'
                        break
                    number += 1
                    destination = history.with_name(history.name + '-' + str(number))
                if relative in errors:
                    continue
                history_relative = destination.relative_to(workspace_root).as_posix()
                with checked_parent(workspace_root, history_relative, create=True) as (parent, name):
                    if find_entry_kind(workspace_root, history_relative) != 'absent':
                        errors[relative] = 'output_view_conflict'
                        continue
                    with checked_parent(workspace_root, relative) as (source_parent, source_name):
                        os.rename(source_name, name, src_dir_fd=source_parent, dst_dir_fd=parent)
                    os.fsync(parent)
            else:
                record_export_sources(store_root, workspace_root, directory, files, source_digest)
        except (OSError, ResearchError) as error:
            errors[relative] = error.code if isinstance(error, ResearchError) else type(error).__name__
    return errors
