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


def has_export_authority(authority):
    """Recognize generated authority metadata; damaged folders remain unowned."""
    from .overrides import CONDITION_STATES
    if (not isinstance(authority, dict) or set(authority) != {'override_ids', 'conditions', 'evaluated_at_revision'}
            or type(authority['evaluated_at_revision']) is not int or authority['evaluated_at_revision'] < 0
            or not isinstance(authority['override_ids'], list)
            or not all(isinstance(item, str) for item in authority['override_ids'])
            or not isinstance(authority['conditions'], list)):
        return False
    for condition in authority['conditions']:
        if (not isinstance(condition, dict)
                or set(condition) != {'override_id', 'text', 'check', 'state', 'evidence', 'obligations', 'lifted_by'}
                or not all(isinstance(condition[key], str) for key in ('override_id', 'text', 'state'))
                or condition['state'] not in CONDITION_STATES
                or condition['check'] is not None and not isinstance(condition['check'], str)
                or not all(isinstance(condition[key], list) for key in ('evidence', 'obligations', 'lifted_by'))
                or not all(isinstance(item, str) for item in condition['lifted_by'])):
            return False
        if any(not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ('id', 'digest'))
               for item in condition['evidence']):
            return False
        if any(not isinstance(item, dict) or not isinstance(item.get('code'), str) for item in condition['obligations']):
            return False
    return True


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
    from .problem_delivery import (
        _OVERRIDE_CITATIONS_EXPORT_SCHEMA, _REFERENCE_EXPORT_SCHEMA, _REGISTRY_CITATIONS_EXPORT_SCHEMA,
        has_exported_files,
    )
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
    if 'supplied_texts' in manifest:
        base_fields.add('supplied_texts')
    use_registry = manifest.get('schema') == _REGISTRY_CITATIONS_EXPORT_SCHEMA
    expanded_citations = (manifest.get('schema') == _OVERRIDE_CITATIONS_EXPORT_SCHEMA
                          or use_registry and manifest.get('gate') == 'bypassed')
    include_references = use_registry or expanded_citations or manifest.get('schema') == _REFERENCE_EXPORT_SCHEMA
    if include_references:
        base_fields.add('schema')
    if use_registry:
        base_fields.add('missing_registry_citations')
    if expanded_citations:
        base_fields.update(('citation_counts', 'uncited_relevant_works', 'citation_spec'))
        from .problem_evidence import validate_override_citation_spec
        try:
            if validate_override_citation_spec({'citations': manifest.get('citation_spec')}) != manifest.get('citation_spec'):
                return None
        except ResearchError:
            return None
    bypassed = manifest.get('gate') == 'bypassed'
    if expanded_citations and not bypassed:
        return None
    if bypassed and 'authority' in manifest:
        if not has_export_authority(manifest['authority']):
            return None
        base_fields.add('authority')
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
    if include_references:
        required_files.add('citation-refs.json')
    if formal is not None:
        required_files.add('formal-statement.txt')
    optional_files = {'citations-omitted.json'} if expanded_citations else ({'reviews.json'} if not bypassed else set())
    if not required_files <= set(manifest['files']) <= required_files | optional_files:
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


def build_current_export(records, artifacts, bypassed, *, include_references=False, citation_spec=None,
                         authority=None, use_registry=False):
    """Build the current projection without reopening a post gate or altering any record."""
    from .problem_delivery import build_override_projection, build_projection, find_post_override
    statement, value = find_current_statement(records), find_value(records)
    if statement is None or value is None or value['statement_id'] != statement['id']:
        return None
    if bypassed:
        override = find_post_override(records)
        return None if override is None else build_override_projection(
            records, artifacts, override, include_references=include_references, citation_spec=citation_spec,
            authority=authority, use_registry=use_registry)[0]
    decision = find_latest(records, 'problem_decision')
    return None if decision is None or decision['action'] != 'post' else build_projection(
        records, artifacts, include_references=include_references, use_registry=use_registry)[0]


def iterate_problem_exports(store_root, records):
    """Read complete current and historical exports, ignoring unavailable disposable files."""
    from .output_views import PROBLEM_EXPORTS_FOLDER, PROBLEM_EXPORT_HISTORY_FOLDER, find_workspace_root
    workspace_root = find_workspace_root(store_root)
    for folder in (PROBLEM_EXPORTS_FOLDER, PROBLEM_EXPORT_HISTORY_FOLDER):
        try:
            if find_entry_kind(workspace_root, folder) != 'other':
                continue
            paths = sorted((workspace_root / folder).rglob('problem-export.json'))
        except (OSError, ResearchError):
            continue
        for path in paths:
            try:
                exported = read_problem_export(path.parent, records)
            except (OSError, ResearchError):
                continue
            if exported is not None:
                yield exported


def find_export_authority(store_root, records, export_digest, statement_digest):
    """Read the authority of the exact complete export, including a retired export."""
    from .problem_delivery import compute_export_digest
    for manifest, files in iterate_problem_exports(store_root, records):
        if (manifest['statement_digest'] == statement_digest and compute_export_digest(files) == export_digest
                and 'authority' in manifest):
            return dict(source='export', export_digest=export_digest,
                        **{key: manifest['authority'][key] for key in ('override_ids', 'conditions')})
    return None


def find_export_supplied_texts(store_root, records, export_digest, statement_digest):
    """Read the supplied-text labels of the exact export that was posted, even after retirement."""
    from .problem_delivery import compute_export_digest
    for manifest, files in iterate_problem_exports(store_root, records):
        if manifest['statement_digest'] == statement_digest and compute_export_digest(files) == export_digest:
            return manifest.get('supplied_texts', [])
    return None


def find_reusable_override_export(store_root, records, manifest):
    """Reuse previously composed bytes when the material and authority remain identical."""
    expected = {key: value for key, value in manifest.items() if key != 'authority'}
    authority = {key: manifest['authority'][key] for key in ('override_ids', 'conditions')}
    for previous, files in iterate_problem_exports(store_root, records):
        if ('authority' in previous and expected == {key: value for key, value in previous.items() if key != 'authority'}
                and authority == {key: previous['authority'][key] for key in ('override_ids', 'conditions')}):
            return files, previous
    return None


def archive_problem_exports(store_root, workspace_root, records, artifacts):
    """Move only complete generated exports, preserving conflicts and every historical byte."""
    from .output_views import PROBLEM_EXPORTS_FOLDER, PROBLEM_EXPORT_HISTORY_FOLDER
    from .problem_delivery import compute_export_digest
    from .problem_delivery import _REGISTRY_CITATIONS_EXPORT_SCHEMA
    folder = workspace_root / PROBLEM_EXPORTS_FOLDER
    with describe_error_at(PROBLEM_EXPORTS_FOLDER):
        if not folder.is_dir() or find_entry_kind(workspace_root, PROBLEM_EXPORTS_FOLDER) != 'other':
            return {}
    artifacts = Evaluation.of(records, artifacts)
    current, errors = {}, {}
    source_digest, override_source_digest = None, None
    for path in sorted(folder.rglob('problem-export.json')):
        directory = path.parent
        relative = directory.relative_to(workspace_root).as_posix()
        try:
            exported = read_problem_export(directory, records)
            if exported is None:
                continue
            manifest, files = exported
            bypassed = manifest.get('gate') == 'bypassed'
            include_references = 'schema' in manifest
            citation_spec = manifest.get('citation_spec')
            use_registry = manifest.get('schema') == _REGISTRY_CITATIONS_EXPORT_SCHEMA
            authority = None
            authorized = True
            if 'authority' in manifest:
                from .overrides import assess_write_authority, read_unrecorded_overrides
                assessment = assess_write_authority(records, read_unrecorded_overrides(store_root) or [],
                                                     dict(kind='problem-override'), artifacts=artifacts)
                authorized = assessment['authorized']
                authority = dict(override_ids=assessment['override_ids'], conditions=assessment['conditions'],
                                 evaluated_at_revision=manifest['authority']['evaluated_at_revision'])
            export_format = (bypassed, include_references, digest(citation_spec), digest(authority), use_registry)
            if export_format not in current:
                try:
                    current[export_format] = (build_current_export(
                        records, artifacts, bypassed, include_references=include_references,
                        citation_spec=citation_spec, authority=authority, use_registry=use_registry) if authorized else None)
                except ResearchError:
                    # An export with unavailable source evidence has lost its standing.
                    current[export_format] = None
            stale = files != current[export_format]
            receipt_path = find_export_source_path(relative)
            if not stale:
                if citation_spec is not None:
                    if override_source_digest is None:
                        from .problem_evidence import compute_override_source_digest
                        override_source_digest = compute_override_source_digest(records)
                    bound_source_digest = override_source_digest
                else:
                    if source_digest is None:
                        source_digest = compute_problem_evidence_digest(records, artifacts)
                    bound_source_digest = source_digest
                if find_entry_kind(store_root, receipt_path) == 'regular':
                    receipt = strict_json(read_file(store_root, receipt_path))
                    stale = (receipt['export_digest'] != compute_export_digest(files)
                             or receipt['source_digest'] != bound_source_digest)
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
                record_export_sources(store_root, workspace_root, directory, files, bound_source_digest)
        except (OSError, ResearchError) as error:
            errors[relative] = error.code if isinstance(error, ResearchError) else type(error).__name__
    return errors
