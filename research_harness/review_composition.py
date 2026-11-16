"""Compose typed accepted observations into ordinary scientific outputs.

The caller owns raw authentication, attempt selection, and scientific completion
checks. Composition never calls assignment_state or reads author files.
"""

import copy
import re

from .errors import ResearchError
from .evidence import digest
from .operations import fields as check_fields
from .problem_rubric import ADMISSION_ITEMS, GROUNDS, ITEMS, PROBLEM_CHECKS as ASSURANCES
from .storage import _canonical


# Frozen plan fields appear in PLAN_KEYS below. Findings are stable IDs and
# required_pages is ordered. terminal_choices maps scalar paths to permissible
# values supplied by the integration verifier. Plans are not author patches.
#
# Ownership: {page_id, path, type, member_key, ordinal, range}. Assurance paths
# use the assurance kind. Lists have stable keys and zero-based ordinals. An
# empty_list owns a whole field with null key/ordinal/range and content [].
# Strings use half-open Unicode character ranges; object values are atomic.
# Fragments add content and grounds to the exact ownership descriptor.
#
# Page fields appear in PAGE_KEYS. Fragment grounds reference the nonempty page
# grounding array; validate_page_grounding verifies authenticated delivery and
# the accepted, resolved scientific selection supplied by the caller. Dispositions
# contain finding_id, status, reason and grounds. Replaces is null for revision
# zero, otherwise REPLACEMENT_KEYS binds the exact predecessor. A frozen revision
# authorization adds replacement_raw_output_digest. Scientific reconciliation
# must be observed; metadata_correction changes only reassessment metadata.
#
# Observation: {attempt_id, raw_output_digest, output}; its digest is digest(output)
# under the existing canonical convention. Root: {version, header, page_tree,
# nodes: hash -> bounded node, digest}. Terminal output: {protocol, epoch_id,
# role, composition_root: digest, choices: scalar path -> value}.
#
# Frozen selections bind controller_id and kind. Ordinary selections supply
# output, output_digest, terminal_attempt_id and terminal_raw_output_digest,
# including any already authorized legacy correction. Paged selections supply
# plan, observations, root, terminal (an observation), and completion_root.
# Pending/incomplete selections have no scientific output; incomplete may carry
# its distinct outcome. currency_state/context_state pass through for all kinds.
# No selection means pending; no mutable state or consumer lookup is consulted.

PROTOCOL = 'exactory-review-composition-v1'
SCHEMA = 'exactory-problem-review-composition-v1'
TREE_FANOUT = 16
MAX_TREE_DEPTH = 8
OWNERSHIP_KEYS = ('page_id', 'path', 'type', 'member_key', 'ordinal', 'range')
PLAN_KEYS = ('protocol', 'schema', 'epoch_id', 'role', 'slot_id', 'subject', 'evidence_currency',
             'source_root', 'dependency_root', 'selected_observations_root', 'integration_root',
             'findings', 'evidence_obligations', 'required_pages', 'ownership',
             'revision_authorizations', 'terminal_choices')
PAGE_KEYS = ('protocol', 'epoch_id', 'role', 'task_id', 'page_id', 'revision', 'source_root',
             'dependency_root', 'ownership', 'fragments', 'grounding', 'findings', 'dispositions', 'replaces')
REPLACEMENT_KEYS = ('page_id', 'revision', 'attempt_id', 'raw_output_digest', 'cause', 'authorization_root')


def _reject(message, *, pending=False, **details):
    raise ResearchError('review_composition_pending' if pending else 'review_composition_conflict',
                        message, details or None)


def _check_literal_text(value, label):
    # Composition identifiers require a nonempty literal, including whitespace.
    if not isinstance(value, str) or not value:
        _reject('Use a nonempty string.', location=label)


def _check_digest(value, label):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        _reject('Use a canonical SHA-256 digest.', location=label)


def _is_nonnegative_integer(value):
    return type(value) is int and value >= 0


def _digest_json(value):
    # Bound malformed JSON nesting before invoking the canonical serializer.
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 64:
            _reject('JSON nesting exceeds the composition bound.')
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, tuple):
            _reject('Use JSON arrays in composition records.')
    _canonical(value, code='review_composition_conflict')
    return digest(value)


def _digest_domain(kind, value):
    return _digest_json({'domain': PROTOCOL + ':' + kind, 'value': value})


def _require_array(value, label):
    if not isinstance(value, list):
        _reject('Use an explicit array.', location=label)
    return value


def _check_ids(value, label):
    rows = _require_array(value, label)
    for row in rows:
        _check_literal_text(row, label)
    if len(set(rows)) != len(rows):
        _reject('Stable identities must be unique.', location=label)
    return rows


def list_page_fields(role):
    if role == 'result':
        required = {'/value/consequence': 'string', '/value/reason': 'string',
                    '/value/bar_ids': 'strings', '/value/evidence': 'objects',
                    '/value/objection_findings': 'objects', '/objections': 'objects', '/limitations': 'strings'}
        for kind in ASSURANCES:
            required['/value/assurances/' + kind + '/reason'] = 'string'
            required['/value/assurances/' + kind + '/evidence'] = 'objects'
        for item in ITEMS:
            required['/value/rubric_evidence/' + item] = 'string'
        return required, {'/reassessment': 'object', '/excerpts': 'objects'}
    if role == 'adjudicator':
        return {'/reason': 'string', '/evidence': 'objects'}, {'/reassessment': 'object'}
    if role == 'incomplete':
        return {('/' + field): 'objects' for field in
                ('known_conclusions', 'unresolved_scope', 'findings', 'recovery_work')}, {}
    _reject('Use a supported composition role.', role=role)


def list_scalar_fields(role):
    if role == 'result':
        result = {'/stage': ('result',), '/support': (None,),
                  '/value/status': ('sufficient', 'insufficient', 'unresolved')}
        for name in ('objective_adequacy', 'method_adequacy'):
            result['/value/' + name] = ('adequate', 'inadequate', 'unresolved', 'not_assessed')
        for kind in ASSURANCES:
            result['/value/assurances/' + kind + '/status'] = ('passed', 'failed', 'not_assessed')
        for item in ADMISSION_ITEMS:
            result['/value/rubric/admission/' + item] = ('pass', 'fail')
        for item in GROUNDS:
            result['/value/rubric/significance/' + item] = (0, 1, 2, 3)
        result['/value/rubric/priority'] = (0, 1, 2, 3)
        for item in ('attempt', 'calibration'):
            result['/value/rubric/' + item] = ('pass', 'fail')
        result['/value/rubric/accessibility'] = ('E-F', 'E-A', 'E-N')
        return result
    if role == 'adjudicator':
        # The exact objection ID is restricted by the frozen integration choices.
        return {'/objection_id': None, '/disposition': ('upheld', 'not_upheld', 'unresolved', 'not_binding'),
                '/correction_admissible': (None, True, False)}
    return {'/origin': ('reviewer',)}


def _check_ownership(owner, plan):
    check_fields(owner, OWNERSHIP_KEYS, code='review_composition_conflict', path='ownership')
    if owner['page_id'] not in plan['required_pages']:
        _reject('Ownership must name a required page.', page_id=owner['page_id'])
    required, optional = list_page_fields(plan['role'])
    allowed = dict(required, **optional)
    kind = allowed.get(owner['path']) if isinstance(owner['path'], str) else None
    if kind is None:
        _reject('Pages may own only schema-approved scientific fields.', path=owner['path'])
    list_field = kind in ('strings', 'objects')
    value_type = owner['type']
    if value_type == 'empty_list':
        if not list_field or any(owner[key] is not None for key in ('member_key', 'ordinal', 'range')):
            _reject('An empty list owns the complete empty field.', path=owner['path'])
        return
    expected = {'strings': 'string', 'objects': 'object'}.get(kind, kind)
    if value_type != expected:
        _reject('Fragment type must match the scientific field.', path=owner['path'])
    if list_field:
        _check_literal_text(owner['member_key'], 'member_key')
        if not _is_nonnegative_integer(owner['ordinal']):
            _reject('List members require frozen nonnegative ordinals.', path=owner['path'])
    elif owner['member_key'] is not None or owner['ordinal'] is not None:
        _reject('Only list members have member keys and ordinals.', path=owner['path'])
    span = owner['range']
    if value_type == 'string':
        if (not isinstance(span, list) or len(span) != 2 or not all(_is_nonnegative_integer(n) for n in span)
                or span[0] > span[1]):
            _reject('Strings require a half-open character range.', path=owner['path'])
    elif span is not None:
        _reject('Only strings have character ranges.', path=owner['path'])


def _check_partition(owners, plan):
    by_path = {}
    seen = set()
    for owner in _require_array(owners, 'ownership'):
        _check_ownership(owner, plan)
        identity = _digest_json(owner)
        if identity in seen:
            _reject('Duplicate field ownership is invalid.', path=owner['path'])
        seen.add(identity)
        by_path.setdefault(owner['path'], []).append(owner)
    required, optional = list_page_fields(plan['role'])
    missing = sorted(set(required) - set(by_path))
    if missing:
        _reject('Every required field needs explicit ownership, including empty lists.', pending=True, paths=missing)
    kinds = dict(required, **optional)
    for path, entries in by_path.items():
        if any(entry['type'] == 'empty_list' for entry in entries):
            if len(entries) != 1:
                _reject('An explicit empty list cannot also have members.', path=path)
            continue
        groups = {}
        if kinds[path] in ('strings', 'objects'):
            ordinals, keys = {}, {}
            for entry in entries:
                ordinal, key = entry['ordinal'], entry['member_key']
                if ordinals.get(ordinal, key) != key or keys.get(key, ordinal) != ordinal:
                    _reject('List member keys and ordinals must agree.', path=path)
                ordinals[ordinal], keys[key] = key, ordinal
                groups.setdefault(key, []).append(entry)
            if sorted(ordinals) != list(range(len(ordinals))):
                _reject('List ordinals must be complete and consecutive.', path=path)
        else:
            groups[None] = entries
        for member in groups.values():
            if member[0]['type'] != 'string':
                if len(member) != 1:
                    _reject('An object has exactly one selected owner.', path=path)
                continue
            position = 0
            for entry in sorted(member, key=lambda row: row['range']):
                start, end = entry['range']
                if start != position or (start == end and len(member) != 1):
                    _reject('String ranges must cover exactly once from zero.', path=path)
                position = end


def _validate_plan(plan):
    check_fields(plan, PLAN_KEYS, ('incomplete_source',), code='review_composition_conflict', path='plan')
    _digest_json(plan)
    if plan['protocol'] != PROTOCOL or plan['schema'] != SCHEMA:
        _reject('Use the supported composition protocol and schema.')
    list_page_fields(plan['role'])
    if 'incomplete_source' in plan:
        if plan['role'] != 'incomplete':
            _reject('Only incomplete pages have a frozen incomplete source.')
        from .review_staged import validate_incomplete_scope
        source = plan['incomplete_source']
        check_fields(source, ('known_conclusions', 'unresolved_scope', 'findings', 'recovery_work'),
                     code='review_composition_conflict')
        validate_incomplete_scope(source['unresolved_scope'], source['recovery_work'])
        for name in ('known_conclusions', 'findings'):
            for row in _require_array(source[name], name):
                if not isinstance(row, dict):
                    _reject('Incomplete page members must be objects.', path='/' + name)
    for key in ('epoch_id', 'slot_id'):
        _check_literal_text(plan[key], key)
    if not isinstance(plan['subject'], dict) or not plan['subject']:
        _reject('Bind the exact scientific subject.')
    for key in ('evidence_currency', 'source_root', 'dependency_root',
                'selected_observations_root', 'integration_root'):
        _check_digest(plan[key], key)
    _check_ids(plan['findings'], 'findings')
    _require_array(plan['evidence_obligations'], 'evidence_obligations')
    if not _check_ids(plan['required_pages'], 'required_pages'):
        _reject('A paged composition needs required pages.', pending=True)
    _check_partition(plan['ownership'], plan)
    if set(entry['page_id'] for entry in plan['ownership']) != set(plan['required_pages']):
        _reject('Every required page must own scientific fields.')
    scalar_fields = list_scalar_fields(plan['role'])
    check_fields(plan['terminal_choices'], scalar_fields, code='review_composition_conflict', path='terminal_choices')
    for path, choices in plan['terminal_choices'].items():
        if not _require_array(choices, path):
            _reject('Each terminal scalar needs an integrated permissible choice.', pending=True, path=path)
        permitted = scalar_fields[path]
        if permitted is None:
            if len(choices) != 1:
                _reject('A focused composition binds one exact objection ID.')
            _check_literal_text(choices[0], path)
        elif any(_digest_json(choice) not in {digest(value) for value in permitted} for choice in choices):
            _reject('Terminal choice is outside the scientific schema.', path=path)
    for authorization in _require_array(plan['revision_authorizations'], 'revision_authorizations'):
        check_fields(authorization, REPLACEMENT_KEYS + ('replacement_raw_output_digest',), code='review_composition_conflict', path='revision_authorization')
        _check_replacement(authorization, authorization=True)
    return plan


def _check_replacement(value, *, authorization=False):
    if not authorization:
        check_fields(value, REPLACEMENT_KEYS, code='review_composition_conflict', path='replaces')
    for key in ('page_id', 'attempt_id'):
        _check_literal_text(value[key], key)
    if not _is_nonnegative_integer(value['revision']):
        _reject('The predecessor revision must be a nonnegative integer.')
    if value['cause'] not in ('observed_reconciliation', 'metadata_correction'):
        _reject('A revision needs an allowed observed cause.')
    for key in ('raw_output_digest', 'authorization_root') + (('replacement_raw_output_digest',) if authorization else ()):
        _check_digest(value[key], key)


def _check_grounds(grounds, grounding, label):
    if not _require_array(grounds, label) or any(not isinstance(row, dict) or not row for row in grounds):
        _reject('Scientific content needs explicit grounding references.', location=label)
    for row in grounds:
        if not ('source_id' in row or 'id' in row or row.get('kind') in ('record', 'quotation', 'reviewer_observation')):
            _reject('Use a quotation or an identified scientific record as a ground.', location=label)
    if any(row not in grounding for row in grounds):
        _reject('Fragment grounds must be declared in the page grounding.', location=label)


def validate_page_grounding(page, *, epoch, selection, tasks, actual_input, delivered):
    """Check page grounds against authenticated delivery and verified resolved science.

    Callers supply the accepted observation closure and the actual answer packet,
    never a catalog asserted by the page. Partial completion can supply resolved
    records; an unresolved record cannot acquire credit from page membership.
    """
    from . import review_staged as staged
    from .review_excerpts import normalize_with_offsets
    work = staged._verify_selection(epoch, selection, tasks=tasks)
    supplied = set()
    pending = [delivered]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            supplied.add(_digest_json(value))
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    sources = {row['id']: row for row in epoch['manifest']['sources']}
    grounding = _require_array(page['grounding'], 'grounding')
    _check_grounds(grounding, grounding, 'grounding')
    for ground in grounding:
        span = ground.get('span') if ground.get('kind') == 'quotation' else ground
        if ground.get('kind') == 'quotation':
            check_fields(ground, ('kind', 'span', 'text'), code='review_composition_conflict')
            if not isinstance(ground['text'], str):
                _reject('A quotation must carry original text.')
        if isinstance(span, dict) and 'source_id' in span:
            check_fields(span, ('source_id', 'start', 'end', 'sha256'), code='review_composition_conflict')
            _check_literal_text(span['source_id'], 'source_id')
            source = sources.get(span['source_id'])
            if source is None or source['kind'] == 'binary':
                _reject('A page quotation must name a frozen text original.')
            try:
                quotation = staged.materialize_quotation(source, span,
                    delivered=actual_input['intervals'],
                    matching_text=ground.get('text') if ground.get('kind') == 'quotation' else None)
            except ResearchError as error:
                _reject('A page quotation must match its delivered original.', cause=error.code)
            if not normalize_with_offsets(quotation['text'])[0]:
                _reject('A scientific page quotation must contain original text.')
        else:
            identifier = ground.get('id')
            identity = work['record_identities'].get(identifier) if isinstance(identifier, str) else None
            if (identity not in work['resolved_records'] or
                    staged.identify_scientific_record(identity[0], ground) != identity
                    or _digest_json(ground) not in supplied):
                _reject('A page ground must be an exact delivered, accepted and resolved scientific record.')
    for fragment in _require_array(page['fragments'], 'fragments'):
        _check_grounds(fragment['grounds'], grounding, 'fragment.grounds')
    for disposition in _require_array(page['dispositions'], 'dispositions'):
        _check_grounds(disposition['grounds'], grounding, 'disposition.grounds')
    return copy.deepcopy(page)


def validate_page(page, plan):
    """Validate one typed page against its externally frozen ownership plan."""
    _validate_plan(plan)
    _validate_page(page, plan)
    return copy.deepcopy(page)


def _validate_page(page, plan, expected_ownership=None):
    check_fields(page, PAGE_KEYS, code='review_composition_conflict', path='page')
    _digest_json(page)
    for key in ('protocol', 'epoch_id', 'role', 'source_root', 'dependency_root'):
        if page[key] != plan[key]:
            _reject('Page scope differs from its frozen plan.', field=key, page_id=page['page_id'])
    _check_literal_text(page['task_id'], 'task_id')
    if page['page_id'] not in plan['required_pages'] or not _is_nonnegative_integer(page['revision']):
        _reject('Bind a required page and an exact nonnegative revision.')
    expected = ([entry for entry in plan['ownership'] if entry['page_id'] == page['page_id']]
                if expected_ownership is None else expected_ownership)
    actual = _require_array(page['ownership'], 'page.ownership')
    if sorted(map(_digest_json, actual)) != sorted(map(_digest_json, expected)):
        _reject('Page ownership differs from the frozen ownership plan.', page_id=page['page_id'])
    for owner in actual:
        _check_ownership(owner, plan)
        if owner['page_id'] != page['page_id']:
            _reject('A page may return only its own fields.')
    if len({_digest_json(owner) for owner in actual}) != len(actual):
        _reject('A page cannot duplicate field ownership.')
    grounding = _require_array(page['grounding'], 'grounding')
    _check_grounds(grounding, grounding, 'grounding')
    fragments = _require_array(page['fragments'], 'fragments')
    actual_owners = []
    for fragment in fragments:
        check_fields(fragment, OWNERSHIP_KEYS + ('content', 'grounds'), code='review_composition_conflict', path='fragment')
        owner = {key: fragment[key] for key in OWNERSHIP_KEYS}
        _check_ownership(owner, plan)
        actual_owners.append(owner)
        content = fragment['content']
        if fragment['type'] == 'string':
            if not isinstance(content, str) or len(content) != fragment['range'][1] - fragment['range'][0]:
                _reject('String content must have exactly its declared character length.', path=fragment['path'])
        elif fragment['type'] == 'object':
            if not isinstance(content, dict):
                _reject('Object fragments require object content.', path=fragment['path'])
            if fragment['path'] in ('/value/objection_findings', '/objections'):
                if content.get('id') != fragment['member_key']:
                    _reject('Finding content must preserve its stable member identity.', path=fragment['path'])
        elif content != []:
            _reject('An explicit empty_list must contain exactly [].', path=fragment['path'])
        _check_grounds(fragment['grounds'], grounding, 'fragment.grounds')
    if sorted(map(_digest_json, actual_owners)) != sorted(map(_digest_json, expected)):
        _reject('Every owned fragment must be returned exactly once.', pending=True, page_id=page['page_id'])
    findings = _check_ids(page['findings'], 'page.findings')
    if any(identifier not in plan['findings'] for identifier in findings):
        _reject('Every observed finding must enter the frozen finding inventory.')
    dispositions = _require_array(page['dispositions'], 'dispositions')
    disposition_ids = []
    for entry in dispositions:
        check_fields(entry, ('finding_id', 'status', 'reason', 'grounds'), code='review_composition_conflict', path='disposition')
        _check_literal_text(entry['finding_id'], 'disposition.finding_id')
        if entry['status'] not in ('retained', 'answered', 'unresolved', 'revised', 'conflict'):
            _reject('Use an explicit finding disposition.')
        _check_literal_text(entry['reason'], 'disposition.reason')
        _check_grounds(entry['grounds'], grounding, 'disposition.grounds')
        disposition_ids.append(entry['finding_id'])
    if sorted(disposition_ids) != sorted(findings):
        _reject('Each page finding needs exactly one disposition.')
    if page['revision'] == 0:
        if page['replaces'] is not None:
            _reject('Revision zero cannot replace a page.')
    else:
        _check_replacement(page['replaces'])
        if page['replaces']['page_id'] != page['page_id'] or page['replaces']['revision'] + 1 != page['revision']:
            _reject('Revisions must name the immediately prior version of the same page.')


def _read_observation(observation):
    check_fields(observation, ('attempt_id', 'raw_output_digest', 'output'), code='review_composition_conflict', path='observation')
    _check_literal_text(observation['attempt_id'], 'attempt_id')
    _check_digest(observation['raw_output_digest'], 'raw_output_digest')
    if _digest_json(observation['output']) != observation['raw_output_digest']:
        _reject('Observation output differs from its exact raw digest.', attempt_id=observation['attempt_id'])
    return observation['output']


def _project_metadata(page):
    result = copy.deepcopy({key: page[key] for key in ('fragments', 'grounding', 'findings', 'dispositions')})
    for fragment in result['fragments']:
        if fragment['path'] != '/reassessment':
            continue
        content = fragment['content']
        for finding in _require_array(content.get('findings'), 'reassessment.findings'):
            if isinstance(finding, dict) and finding.get('disposition') in ('confirmed', 'revised'):
                finding['disposition'] = 'confirmed'
                finding['reason'] = None
    return result


def _validate_metadata_revision(previous, page):
    if _project_metadata(page) != _project_metadata(previous):
        _reject('Metadata correction cannot change scientific content.', page_id=page['page_id'])
    for old, new in zip(previous['fragments'], page['fragments']):
        if old['path'] != '/reassessment':
            continue
        for before, after in zip(old['content']['findings'], new['content']['findings']):
            if before != after and (before.get('disposition'), after.get('disposition')) != ('confirmed', 'revised'):
                _reject('Metadata correction changes only confirmed findings to revised.')


def _select_pages(plan, observations):
    by_page, attempts = {}, {}
    ownership_by_page = {page_id: [] for page_id in plan['required_pages']}
    for owner in plan['ownership']:
        ownership_by_page[owner['page_id']].append(owner)
    for observation in _require_array(observations, 'observations'):
        page = _read_observation(observation)
        check_fields(page, PAGE_KEYS, code='review_composition_conflict', path='page')
        # Historical accepted revisions retain their original ranges. The current
        # ownership plan applies to the selected head after exact-chain checks.
        _validate_page(page, plan, expected_ownership=page['ownership'])
        attempt_id = observation['attempt_id']
        if attempt_id in attempts:
            if attempts[attempt_id] != observation:
                _reject('One attempt cannot identify conflicting observations.', attempt_id=attempt_id)
            continue
        attempts[attempt_id] = observation
        revisions = by_page.setdefault(page['page_id'], {})
        if page['revision'] in revisions:
            _reject('A page revision has one observed owner; replay must name the same attempt.', page_id=page['page_id'])
        revisions[page['revision']] = observation
    missing = [page_id for page_id in plan['required_pages'] if page_id not in by_page]
    if missing:
        _reject('Required answer pages are missing.', pending=True, page_ids=missing)
    selected, descriptors = [], []
    finding_ids = []
    for page_id in plan['required_pages']:
        revisions = by_page[page_id]
        if sorted(revisions) != list(range(len(revisions))):
            _reject('A revision chain must retain every exact predecessor.', page_id=page_id)
        chain = []
        for revision in range(len(revisions)):
            observation = revisions[revision]
            page = observation['output']
            if revision:
                prior = revisions[revision - 1]
                replaces = page['replaces']
                if replaces['attempt_id'] != prior['attempt_id'] or replaces['raw_output_digest'] != prior['raw_output_digest']:
                    _reject('Revision predecessor binding differs from the accepted prior observation.', page_id=page_id)
                authorization = dict(replaces, replacement_raw_output_digest=observation['raw_output_digest'])
                if authorization not in plan['revision_authorizations']:
                    _reject('Every revision requires its exact frozen observed authorization.', page_id=page_id)
                if replaces['cause'] == 'metadata_correction':
                    _validate_metadata_revision(prior['output'], page)
            chain.append({'revision': revision, 'attempt_id': observation['attempt_id'],
                          'raw_output_digest': observation['raw_output_digest']})
        head = revisions[len(revisions) - 1]
        page = head['output']
        _validate_page(page, plan, expected_ownership=ownership_by_page[page_id])
        selected.append(page)
        finding_ids.extend(page['findings'])
        descriptors.append({'page_id': page_id, 'task_id': page['task_id'], 'revision': page['revision'],
                            'attempt_id': head['attempt_id'], 'raw_output_digest': head['raw_output_digest'],
                            'ownership': ownership_by_page[page_id],
                            'revision_chain': chain})
    if len(set(finding_ids)) != len(finding_ids):
        _reject('A finding has one selected page owner.')
    if set(finding_ids) != set(plan['findings']):
        _reject('Every frozen adverse or new finding must survive composition.', pending=True,
                finding_ids=sorted(set(plan['findings']) - set(finding_ids)))
    return selected, descriptors, len(attempts)


def _build_tree(descriptors):
    nodes, level = {}, []
    for offset in range(0, len(descriptors), TREE_FANOUT):
        node = {'kind': 'leaf', 'pages': descriptors[offset:offset + TREE_FANOUT]}
        reference = _digest_domain('page-tree', node)
        nodes[reference] = node
        level.append(reference)
    depth = 1
    while len(level) > 1:
        if depth >= MAX_TREE_DEPTH:
            _reject('The descriptor tree exceeds its frozen depth bound.')
        next_level = []
        for offset in range(0, len(level), TREE_FANOUT):
            node = {'kind': 'branch', 'children': level[offset:offset + TREE_FANOUT]}
            reference = _digest_domain('page-tree', node)
            nodes[reference] = node
            next_level.append(reference)
        level, depth = next_level, depth + 1
    return level[0], nodes, depth


def _build_root(plan, descriptors, observation_count):
    page_tree, nodes, depth = _build_tree(descriptors)
    header = {key: copy.deepcopy(plan[key]) for key in
              ('epoch_id', 'role', 'subject', 'evidence_currency', 'slot_id', 'schema',
               'source_root', 'dependency_root', 'selected_observations_root', 'integration_root')}
    header.update(plan_digest=_digest_domain('plan', plan), ownership_plan_root=_digest_domain('ownership', plan['ownership']),
                  findings_root=_digest_domain('findings', plan['findings']),
                  evidence_obligations_root=_digest_domain('evidence-obligations', plan['evidence_obligations']),
                  counts={'pages': len(descriptors), 'observations': observation_count,
                          'fragments': len(plan['ownership']), 'findings': len(plan['findings']), 'tree_depth': depth})
    root = {'version': PROTOCOL, 'header': header, 'page_tree': page_tree, 'nodes': nodes}
    root['digest'] = _digest_domain('root', {'version': PROTOCOL, 'header': header, 'page_tree': page_tree})
    return root


def build_root(plan, observations):
    """Bind the complete required page set, authorized revisions and integration."""
    _validate_plan(plan)
    _, descriptors, count = _select_pages(plan, observations)
    return copy.deepcopy(_build_root(plan, descriptors, count))


def plan_incomplete_composition(epoch, *, outcome, known_conclusions, findings, source_root, dependency_root,
                                selected_observations_root, integration_root, page_characters):
    """Freeze bounded page ownership for a reviewer-origin incomplete outcome.

    Object members remain atomic, as required by the composition schema. The
    caller measures and admits the rendered answer tasks before dispatch.
    """
    from .review_staged import validate_incomplete_scope
    check_fields(outcome, ('unresolved_scope', 'recovery_work'), code='review_composition_conflict')
    validate_incomplete_scope(**outcome)
    if not _is_nonnegative_integer(page_characters) or not page_characters:
        _reject('Incomplete pages need a positive content allowance.')
    values = dict(known_conclusions=_require_array(known_conclusions, 'known_conclusions'),
                  findings=_require_array(findings, 'findings'), **outcome)
    finding_ids = _check_ids([row['id'] for row in findings], 'findings')
    plan = dict(protocol=PROTOCOL, schema=SCHEMA, epoch_id=epoch['id'], role='incomplete', slot_id=epoch['slot_id'],
                subject=copy.deepcopy(epoch['subject']), evidence_currency=(epoch['evidence_currency'] if isinstance(epoch['evidence_currency'], str) else digest(epoch['evidence_currency'])),
                source_root=source_root, dependency_root=dependency_root,
                selected_observations_root=selected_observations_root, integration_root=integration_root,
                findings=finding_ids, evidence_obligations=[], required_pages=[], ownership=[],
                revision_authorizations=[], terminal_choices={'/origin': ['reviewer']},
                incomplete_source=copy.deepcopy(values))
    used = page_characters
    for field in list_page_fields('incomplete')[0]:
        members = values[field[1:]]
        for ordinal, member in enumerate(members or [None]):
            if members and not isinstance(member, dict):
                _reject('Incomplete page members must be objects.', path=field)
            size = len(_canonical(member, code='review_composition_conflict')) if members else 2
            if size > page_characters:
                _reject('An incomplete member needs a smaller scientific scope.', pending=True, path=field, ordinal=ordinal)
            if used + size > page_characters:
                plan['required_pages'].append('incomplete-page:' + str(len(plan['required_pages']) + 1))
                used = 0
            used += size
            plan['ownership'].append(dict(page_id=plan['required_pages'][-1], path=field,
                 type='object' if members else 'empty_list', member_key=(str(member.get('id', field + ':' + str(ordinal))) if members else None),
                 ordinal=ordinal if members else None, range=None))
    _validate_plan(plan)
    return copy.deepcopy(plan)


def _validate_root(root):
    check_fields(root, ('version', 'header', 'page_tree', 'nodes', 'digest'), code='review_composition_conflict', path='root')
    _digest_json(root)
    if root['version'] != PROTOCOL or not isinstance(root['header'], dict) or not isinstance(root['nodes'], dict):
        _reject('Use the versioned composition root and node table.')
    _check_digest(root['digest'], 'root.digest')
    expected = _digest_domain('root', {key: root[key] for key in ('version', 'header', 'page_tree')})
    if root['digest'] != expected:
        _reject('Composition root header or membership was altered.')
    visited = set()
    stack = [(root['page_tree'], 1)]
    while stack:
        reference, depth = stack.pop()
        _check_digest(reference, 'page_tree.reference')
        if reference in visited or depth > MAX_TREE_DEPTH:
            _reject('Composition tree must be acyclic and within its depth bound.')
        visited.add(reference)
        node = root['nodes'].get(reference)
        if not isinstance(node, dict):
            _reject('A referenced page-tree node is missing.')
        if node.get('kind') == 'leaf':
            check_fields(node, ('kind', 'pages'), code='review_composition_conflict', path='leaf')
            rows = _require_array(node['pages'], 'leaf.pages')
        elif node.get('kind') == 'branch':
            check_fields(node, ('kind', 'children'), code='review_composition_conflict', path='branch')
            rows = _require_array(node['children'], 'branch.children')
            stack.extend((child, depth + 1) for child in rows)
        else:
            _reject('Use a typed leaf or branch node.')
        if not rows or len(rows) > TREE_FANOUT or _digest_domain('page-tree', node) != reference:
            _reject('Page-tree node content or fanout differs from its digest.')
    if visited != set(root['nodes']):
        _reject('The root node table must contain exactly the reachable tree.')


def validate_terminal_choices(plan, terminal, root):
    """Check exact root authorization and integration-approved scalar choices."""
    _validate_plan(plan)
    _validate_root(root)
    if root['header'].get('plan_digest') != _digest_domain('plan', plan):
        _reject('The terminal root must bind this exact frozen plan.')
    if isinstance(terminal, dict) and 'decisions' in terminal:
        from .review_staged import derive_composition_terminal
        terminal = derive_composition_terminal({'id': plan['epoch_id'], 'role': plan['role']}, terminal)
    check_fields(terminal, ('protocol', 'epoch_id', 'role', 'composition_root', 'choices'), code='review_composition_conflict', path='terminal')
    for key in ('protocol', 'epoch_id', 'role'):
        if terminal[key] != plan[key]:
            _reject('Terminal scope differs from its composition.', field=key)
    if terminal['composition_root'] != root['digest']:
        _reject('Terminal must authorize the exact complete composition root.')
    check_fields(terminal['choices'], plan['terminal_choices'], code='review_composition_conflict', path='terminal.choices')
    for path, value in terminal['choices'].items():
        if _digest_json(value) not in {_digest_json(item) for item in plan['terminal_choices'][path]}:
            _reject('Terminal choice conflicts with integrated scientific constraints.', path=path)
    return copy.deepcopy(terminal['choices'])


def _put(result, path, content):
    parts = path.split('/')[1:]
    parent = result
    for part in parts[:-1]:
        parent = parent.setdefault(part, {})
    parent[parts[-1]] = copy.deepcopy(content)


def _join(fragments):
    if fragments[0]['type'] == 'string':
        return ''.join(fragment['content'] for fragment in sorted(fragments, key=lambda row: row['range']))
    return copy.deepcopy(fragments[0]['content'])


def assemble_result(plan, observations, root, terminal):
    """Reverify complete membership and assemble only the ordinary scientific fields."""
    _validate_plan(plan)
    selected, descriptors, count = _select_pages(plan, observations)
    _validate_root(root)
    if root != _build_root(plan, descriptors, count):
        _reject('Composition root differs from the complete frozen observation set.')
    choices = validate_terminal_choices(plan, terminal, root)
    result = {}
    for path, value in choices.items():
        _put(result, path, value)
    by_path = {}
    for page in selected:
        for fragment in page['fragments']:
            by_path.setdefault(fragment['path'], []).append(fragment)
    required, optional = list_page_fields(plan['role'])
    kinds = dict(required, **optional)
    for path, fragments in by_path.items():
        if fragments[0]['type'] == 'empty_list':
            content = []
        elif kinds[path] in ('objects', 'strings'):
            members = {}
            for fragment in fragments:
                members.setdefault(fragment['ordinal'], []).append(fragment)
            content = [_join(members[ordinal]) for ordinal in sorted(members)]
        else:
            content = _join(fragments)
        _put(result, path, content)
    if plan['role'] == 'result':
        result['value']['work_items'] = []
        assurances = result['value']['assurances']
        result['value']['assurances'] = [dict(kind=kind, **assurances[kind]) for kind in ASSURANCES]
    return result


def effective_review_output(controller_id, frozen_selection=None):
    """Return an isolated scientific result from an explicit nonrecursive selection."""
    _check_literal_text(controller_id, 'controller_id')
    if frozen_selection is None:
        frozen_selection = {'controller_id': controller_id, 'kind': 'pending'}
    if not isinstance(frozen_selection, dict) or frozen_selection.get('controller_id') != controller_id:
        _reject('The frozen selection must bind this controller.')
    _digest_json(frozen_selection)
    selection = frozen_selection
    kind = selection.get('kind')
    if kind not in ('ordinary', 'paged', 'incomplete', 'pending'):
        _reject('Use a supported effective-output kind.')
    result = {'kind': kind, 'composition_version': None, 'composition_root': None,
              'selected_observations_root': selection.get('selected_observations_root'),
              'completion_root': selection.get('completion_root'),
              'terminal_attempt_id': selection.get('terminal_attempt_id'),
              'terminal_raw_output_digest': selection.get('terminal_raw_output_digest'),
              'currency_state': selection.get('currency_state'), 'context_state': selection.get('context_state')}
    if kind in ('pending', 'incomplete'):
        if 'output' in selection or 'effective_output' in selection:
            _reject('Pending and incomplete outcomes cannot supply a completed scientific output.')
        if kind == 'incomplete' and 'outcome' in selection:
            result['incomplete_outcome'] = copy.deepcopy(selection['outcome'])
        return copy.deepcopy(result)
    if kind == 'ordinary':
        if not isinstance(selection.get('output'), dict):
            _reject('An ordinary selection needs its exact existing scientific object.')
        output = selection['output']
        if 'terminal' in output:
            from .review_staged import project_ordinary_final
            output, staged_records = project_ordinary_final(output)
            result['staged_records'] = staged_records
        if _digest_json(output) != selection.get('output_digest'):
            _reject('Ordinary output must preserve its existing effective digest.')
        _check_literal_text(result['terminal_attempt_id'], 'terminal_attempt_id')
        _check_digest(result['terminal_raw_output_digest'], 'terminal_raw_output_digest')
    else:
        missing = [key for key in ('plan', 'observations', 'root', 'terminal', 'completion_root') if key not in selection]
        if missing:
            _reject('A paged selection requires complete frozen composition bindings.', pending=True, fields=missing)
        terminal = _read_observation(selection['terminal'])
        if isinstance(terminal, dict) and 'terminal' in terminal:
            terminal = terminal['terminal']
        _check_digest(selection['completion_root'], 'completion_root')
        output = assemble_result(selection['plan'], selection['observations'], selection['root'], terminal)
        if selection['plan']['role'] == 'incomplete':
            _reject('An incomplete composition must be recorded as a distinct incomplete outcome.')
        result.update(composition_version=PROTOCOL, composition_root=selection['root']['digest'],
                      selected_observations_root=selection['plan']['selected_observations_root'],
                      terminal_attempt_id=selection['terminal']['attempt_id'],
                      terminal_raw_output_digest=selection['terminal']['raw_output_digest'])
    result.update(effective_output=copy.deepcopy(output), effective_output_digest=_digest_json(output))
    return copy.deepcopy(result)


# Final-page rules are separate from the frozen staged-observation protocol above.
# Recorded pages wrap {targets, output}; output is the page answer described here.
# Page answers are {targets: [{target, fields: {JSON-pointer: value}, sentence}]}.
# Finding pages instead use {targets: [{formulation, coverage, grounds, sentence}]}.
# Math pages use {targets: [{formulation, coverage, sentence}]}.
# Ordinary closing answers are {fields: {JSON-pointer: value}, bindings: [path]}.
# bindings selects optional harness fields, never their values. Finding closings
# have the five result findings, outcome, reason and grounds directly.
FINAL_RULE_PREFIX = 'exactory-final-composition-v1/'
SENTENCE_CHARACTERS = 300
PAGE_FIELDS_CHARACTERS = 1500
PAGE_ROW_CHARACTERS = 1900
PAGE_ENVELOPE_CHARACTERS = 32
CLOSING_FIELDS_CHARACTERS = 16000
GROUND_TEXT_CHARACTERS = 600
COVERAGE_CHARACTERS = 800
COVERAGE_LINE_BREAKS = '\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029'
FINDING_REASON_CHARACTERS = 4000
ROLES_WITHOUT_RULES = {
    role: dict(unit='paper-series', release='0.94.0',
               reason='No parts adapter or chain family applies in this release.')
    for role in ('manuscript', 'standalone')
}
# These validators expose a complete object, not an individual-field checker.
# Use the original object checker with neutral siblings wherever possible.
INDIVIDUAL_CHECK_LIMITATIONS = (
    'research_decisions._validate_value: work items, objection findings and rubric evidence are inline',
    'native_review.validate_output: native finding strings and math coverage are inline',
    'native_review.validate_finding: coverage, grounds and closing findings are inline',
    'review_protocol.validate_adjudication_contract: disposition and correction admissibility are inline',
    'review_parts_study.validate_verification_final: verification checks are inline and require series delivery',
    'development._review: readiness checks require a live candidate context',
    'review_context_repair.validate_reassessment: confirmation compares the complete scientific output',
    'research_decisions.validate_review_contract: new objections and support require assignment state',
)
FINAL_PAGE_INVALID_MESSAGE = 'Return exactly the assigned targets, their owned fields and a sentence of at most 300 characters.'
FINAL_PAGE_BOUND_MESSAGE = 'The serialized fields or row of this page exceed the limits of its composition rule.'
FINAL_OWNER_INVALID_MESSAGE = 'Every output field must have exactly one owner in the composition rule.'
FINAL_CLOSING_INVALID_MESSAGE = 'Return only the closing fields and the optional harness field paths of this composition rule.'
PACKET_ARTIFACT_MISSING_MESSAGE = 'The packet does not carry the bytes of this reference.'
TARGET_WITHOUT_ASSESSMENT_SENTENCE = 'The output supplies no separate assessment for this target.'
MATH_COVERAGE_INVALID_MESSAGE = 'Split only a math coverage string composed by this rule, with one labeled line for each formulation.'
ANSWER_SCHEMA_INVALID_MESSAGE = 'This answer exceeds its versioned field schema. Cite longer supporting material by a recorded handle.'
VALIDATOR_FIELDS_UNOWNED_MESSAGE = 'A validator accepts fields absent from this composition schema. Declare their owners and bounds.'
JSON_FIELD_CHARACTERS = 8000
JSON_STRING_CHARACTERS = 4000
JSON_COLLECTION_ITEMS = 64
GROUND_ITEMS = 8
GROUND_REFERENCES = 8


def _make_field(path, owner, target=None):
    return dict(path=path, owner=owner, target=target)


def _make_value_fields(stage, problem):
    fields = [_make_field('/stage', 'harness'), _make_field('/support/**', 'closing'),
              _make_field('/objections/**', 'closing'), _make_field('/limitations/**', 'closing')]
    for name in ('status', 'consequence', 'objective_adequacy', 'method_adequacy', 'reason', 'evidence'):
        fields.append(_make_field('/value/' + name + ('/**' if name == 'evidence' else ''), 'closing'))
    fields.append(_make_field('/value/bar_ids/**', 'harness'))
    for collection, identity, names in (
            ('assurances', 'kind', ('kind', 'status', 'reason', 'evidence')),
            ('work_items', 'id', ('id', 'classification', 'status', 'reason', 'evidence')),
            ('objection_findings', 'id', ('id', 'status', 'reason', 'evidence'))):
        for name in names:
            fields.append(_make_field('/value/' + collection + '/*/' + name +
                                      ('/**' if name == 'evidence' else ''), 'page', identity))
    if problem and stage == 'result':
        for item in ITEMS:
            section = 'admission/' if item in ADMISSION_ITEMS else 'significance/' if item in GROUNDS else ''
            fields.append(_make_field('/value/rubric/' + section + item, 'page', item))
            fields.append(_make_field('/value/rubric_evidence/' + item, 'page', item))
    if problem and stage == 'slate':
        fields.append(_make_field('/value/rubric_potential/*/**', 'page', 'candidate'))
    for name in ('source_requests', 'settled_requests', 'excerpts'):
        fields.append(_make_field('/' + name + '/**', 'closing'))
    return fields


def find_rule(role, packet):
    """Return a fresh table; no store, composite reader or process cache is used."""
    from . import native_review, review_parts_roles
    if role in ROLES_WITHOUT_RULES:
        return None
    if 'context_repair' in packet:
        ordinary = dict(packet)
        ordinary.pop('context_repair')
        rule = find_rule(role, ordinary)
        if rule is None:
            return None
        rule['id'] = FINAL_RULE_PREFIX + 'context-repair-' + rule['id'][len(FINAL_RULE_PREFIX):]
        rule['base_adapter'] = rule['adapter']
        rule['adapter'] = 'context_repair'
        rule['fields'].append(_make_field('/reassessment/assignment_id', 'harness'))
        rule['fields'].extend(_make_field('/reassessment/findings/*/' + key + ('/**' if key == 'evidence' else ''),
                                        'page', 'historical') for key in ('review_id', 'disposition', 'reason', 'evidence'))
        rule['checks'].append('review.reassessment')
        return _attach_answer_schemas(rule)
    native = packet.get('native', packet)
    adapter = review_parts_roles.find_adapter(dict(role=role), packet)
    problem = packet.get('subject', {}).get('kind') == 'problem'
    if role == 'native_math':
        kind = native.get('kind')
        if kind not in native_review.KINDS:
            return None
        context = native.get('scientific_context', {})
        finding = kind == 'result' and 'audit_forecast' in context
        math = (kind == 'result' and 'openness_minimum' in context and not finding
                and native.get('contract', {}).get('orchestration') != 'problem_creation')
        name = 'problem-openness' if finding else 'math-openness' if math else 'native-' + kind
        fields = [_make_field('/' + key + '/**', 'harness')
                  for key in ('subject_digest', 'reviewer')]
        if kind != 'recovery':
            fields.append(_make_field('/claim_digest', 'harness'))
        fields.append(_make_field('/schema_version', 'harness' if finding or math or kind != 'result' else 'closing'))
        if finding or math:
            fields.extend(_make_field('/findings/' + key, 'closing') for key in native_review.RESULT_FINDINGS)
            fields.extend(_make_field('/findings/openness/' + key + ('/**' if key in ('coverage', 'grounds', 'evidence') else ''),
                                      'page' if key == 'coverage' else 'closing', 'formulation' if key == 'coverage' else None)
                          for key in (('outcome', 'coverage', 'reason', 'grounds') if finding else
                                      ('outcome', 'coverage', 'reason', 'evidence', 'searched_at')))
            if finding:
                # The harness owns the concatenated list; neither contributor
                # can overwrite the other contributor's entries.
                fields[-1]['owner'] = 'harness'
            else:
                fields.extend((_make_field('/findings/openness/statement_digest', 'harness'),
                               _make_field('/findings/openness/minimum/**', 'harness')))
        else:
            names = {'proposal': native_review.PROPOSAL_FINDINGS, 'result': native_review.RESULT_FINDINGS,
                     'recovery': native_review.RECOVERY_FINDINGS}[kind]
            fields.extend(_make_field('/findings/' + key, 'page', key) for key in names)
            if kind == 'result':
                fields.extend(_make_field('/findings/openness/' + key + '/**', 'page', 'openness')
                              for key in ('outcome', 'coverage', 'reason', 'evidence', 'searched_at'))
                fields.append(_make_field('/findings/openness/statement_digest', 'harness'))
                fields.append(_make_field('/findings/openness/minimum/**',
                    'harness' if 'openness_minimum' in context else 'page', 'openness'))
        if not finding:
            fields.extend(_make_field('/' + key + '/**', 'closing')
                          for key in ('decision', 'reason', 'objections', 'unresolved_objections'))
        check = 'openness.finding' if finding else 'native.output'
    elif role == 'verification':
        adapter = adapter or 'study_verification'
        name, check = 'study-verification', 'review.shape'
        fields = [_make_field('/verdict/**', 'closing')]
        fields.extend(_make_field('/checks/*/' + key + ('/**' if key == 'evidence' else ''), 'page', 'dimension')
                      for key in ('dimension', 'reason', 'evidence'))
    elif role == 'adjudicator':
        name, check = 'adjudicator', 'adjudication.shape'
        fields = [_make_field('/objection_id', 'harness')]
        fields.extend(_make_field('/' + key + ('/**' if key == 'evidence' else ''), 'page', 'objection')
                      for key in ('reason', 'evidence'))
        fields.extend(_make_field('/' + key, 'closing') for key in ('disposition', 'correction_admissible'))
    elif role in ('bar', 'slate', 'result'):
        name = 'study-readiness' if adapter == 'study_readiness' else role
        check = 'review.value'
        fields = _make_value_fields(role, problem)
        if adapter == 'study_readiness':
            fields = [row for row in fields if row['path'] != '/support/**']
            # The adapter's six targets are readiness checks. The separate
            # strategic value assessment belongs to the closing answer.
            for field in fields:
                if field['owner'] == 'page':
                    field.update(owner='closing', target=None)
            for key in ('id', 'verdict', 'limitations'):
                fields.append(_make_field('/support/' + key + '/**', 'closing'))
            for key in ('candidate_digest', 'assessor'):
                fields.append(_make_field('/support/' + key + '/**', 'harness'))
            for key in ('kind', 'status', 'reason', 'evidence'):
                fields.append(_make_field('/support/checks/*/' + key + ('/**' if key == 'evidence' else ''),
                                          'page', 'readiness'))
    else:
        return None
    native_kind = native.get('kind') if role == 'native_math' else None
    if native_kind in ('result', 'recovery') and not finding:
        fields.append(dict(path='/**', owner='closing', target=None, when='not_approved'))
    rule = dict(id=FINAL_RULE_PREFIX + name, role=role, adapter=adapter, fields=fields,
                native_kind=native_kind,
                field_paths=[field['path'] for field in fields],
                checks=[check],
                limits=dict(sentence=SENTENCE_CHARACTERS, fields=PAGE_FIELDS_CHARACTERS,
                            row=PAGE_ROW_CHARACTERS, envelope=PAGE_ENVELOPE_CHARACTERS,
                            closing=CLOSING_FIELDS_CHARACTERS, coverage=COVERAGE_CHARACTERS,
                            ground_text=GROUND_TEXT_CHARACTERS, reason=FINDING_REASON_CHARACTERS))
    if rule['id'].endswith('/problem-openness') and 'finding_index' in packet:
        rule['finding_version'] = 2
    return _attach_answer_schemas(rule)


def list_targets(rule, packet):
    """Keep adapter identities and order; formulations have short page handles."""
    from . import native_review, review_parts_roles, research_decisions
    name = rule['id'][len(FINAL_RULE_PREFIX):]
    if name in ('problem-openness', 'math-openness'):
        minimum = packet.get('native', packet)['scientific_context']['openness_minimum']
        targets = [dict(id=value, label=value) for value in [minimum['statement']] + minimum['neighbours']]
        if 'page_plan_digest' in packet.get('reading_final', {}):
            for index, target in enumerate(targets, 1):
                target['handle'] = 't%d' % index
    elif rule['adapter'] == 'context_repair' and rule.get('base_adapter') is None:
        ordinary = dict(packet)
        ordinary.pop('context_repair')
        targets = list_targets(find_rule(rule['role'], ordinary), ordinary)
        seen = {row['id'] for row in targets}
        targets.extend(dict(id=row['id'], label=row['id']) for row in packet.get('historical_reviews', [])
                       if row['id'] not in seen)
    elif rule['adapter'] is not None:
        targets = copy.deepcopy(review_parts_roles.get_adapter(rule['adapter'])['targets'](packet))
    elif rule['role'] == 'native_math':
        targets = [dict(id=key, label=key) for key in {
            'proposal': native_review.PROPOSAL_FINDINGS,
            'result': native_review.RESULT_FINDINGS + ('openness',),
            'recovery': native_review.RECOVERY_FINDINGS}[packet.get('native', packet)['kind']]]
    elif rule['role'] == 'adjudicator':
        targets = [dict(id='objection', label=packet['objection']['id'])]
    else:
        ids = [] if rule['role'] == 'bar' else list(ASSURANCES if packet.get('subject', {}).get('kind') == 'problem'
                                                   else research_decisions.ALL_CHECKS)
        if rule['role'] == 'slate':
            ids.extend(row['id'] for row in packet.get('candidates', []))
        ids.extend(packet.get('dossier', {}).get('work_items', []))
        material = packet.get('dossier', {}).get('material_change') or {}
        ids.extend(row['id'] for row in material.get('prior_objections', []))
        ids.extend(row['id'] for row in packet.get('material_objections', []))
        for entry in packet.get('historical_reviews', []):
            review = entry.get('review', entry)
            review = review.get('payload', review)
            ids.extend(row['id'] for row in review.get('objections', []))
        targets = [dict(id=key, label=key) for key in dict.fromkeys(ids or ['bar'])]
    if packet.get('review_parts', {}).get('protocol') == 'exactory-review-parts-v3' and 'page_plan_digest' in packet['review_parts']:
        for index, target in enumerate(targets, 1):
            target['handle'] = 't%d' % index
    return targets


def _escape_field(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def _read_path(value, path):
    for key in path.strip('/').split('/') if path else []:
        key = key.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _write_path(value, path, content):
    keys = path.strip('/').split('/')
    for index, key in enumerate(keys):
        key = key.replace('~1', '/').replace('~0', '~')
        address = int(key) if isinstance(value, list) else key
        if isinstance(value, list):
            while len(value) <= address:
                value.append(None)
        if index == len(keys) - 1:
            value[address] = copy.deepcopy(content)
        else:
            if (address >= len(value) if isinstance(value, list) else address not in value) or value[address] is None:
                value[address] = [] if keys[index + 1].isdigit() and key != 'rubric_potential' else {}
            value = value[address]


def _list_output_fields(value, path=''):
    if isinstance(value, (dict, list)) and value:
        entries = enumerate(value) if isinstance(value, list) else value.items()
        for key, item in entries:
            yield from _list_output_fields(item, path + '/' + _escape_field(key))
    else:
        yield path, value


def _list_rule_values(rule, value, path=''):
    for field in rule['fields']:
        if not field.get('when') and field['path'].endswith('/**') and _matches_field(field['path'][:-3], path):
            yield path, value
            return
    if isinstance(value, (dict, list)) and value:
        entries = enumerate(value) if isinstance(value, list) else value.items()
        for key, item in entries:
            yield from _list_rule_values(rule, item, path + '/' + _escape_field(key))
    else:
        yield path, value


def _matches_field(pattern, path):
    parts = pattern.strip('/').split('/')
    keys = path.strip('/').split('/')
    if parts[-1] == '**':
        parts = parts[:-1]
        return len(keys) >= len(parts) and all(p == '*' or p == k for p, k in zip(parts, keys))
    return len(keys) == len(parts) and all(p == '*' or p == k for p, k in zip(parts, keys))


def _uses_native_assessment(rule, output):
    return (rule.get('native_kind') in ('result', 'recovery')
            and output.get('decision') in ('revise', 'reject'))


def _find_field_owners(rule, path, value, output=None):
    fields = [field for field in rule['fields'] if bool(field.get('when')) ==
              bool(output is not None and _uses_native_assessment(rule, output))]
    found = [field for field in fields if _matches_field(field['path'], path)]
    if not found and isinstance(value, (dict, list)) and not value:
        prefix = path + '/'
        found = [field for field in fields if field['path'].startswith(prefix)]
        # An empty collection is the shared container of disjoint member paths.
        if found and len({field['owner'] for field in found}) == 1:
            found = found[:1]
    return found


def describe_owners(rule):
    """Describe disjoint paths. Grounds are a concatenation, never overwrites."""
    owners = {}
    for field in rule['fields']:
        path = field['path'] + ('@' + field['when'] if field.get('when') else '')
        if path in owners or field['owner'] not in ('page', 'closing', 'harness'):
            raise ResearchError('review_composition_conflict', FINAL_OWNER_INVALID_MESSAGE, dict(path=field['path']))
        owners[path] = field['owner']
    if set(owners) != set(rule['field_paths']):
        raise ResearchError('review_composition_conflict', FINAL_OWNER_INVALID_MESSAGE)
    return owners


def validate_output_owners(rule, output):
    """Guard part 2: check complete actual outputs, including empty containers."""
    describe_owners(rule)
    for path, value in _list_output_fields(output):
        if len(_find_field_owners(rule, path, value, output)) != 1:
            raise ResearchError('review_composition_conflict', FINAL_OWNER_INVALID_MESSAGE, dict(path=path))


def _find_target_id(rule, packet, output, field, path):
    target = field['target']
    parts = path.strip('/').split('/')
    if target in ('kind', 'id'):
        parent = _read_path(output, '/' + '/'.join(parts[:3]))
        return parent.get(target)
    if target == 'dimension':
        return output['checks'][int(parts[1])]['dimension']
    if target == 'readiness':
        return output['support']['checks'][int(parts[2])]['kind']
    if target == 'historical':
        return output['reassessment']['findings'][int(parts[2])]['review_id']
    if target == 'candidate':
        return parts[2].replace('~1', '/').replace('~0', '~')
    return target


def _build_harness_fields(rule, packet):
    native = packet.get('native', packet)
    if rule['role'] == 'native_math':
        values = {'/' + key: copy.deepcopy(native[key]) for key in ('subject_digest', 'reviewer')}
        if native['kind'] != 'recovery':
            values['/claim_digest'] = native['claim_digest']
        if rule['id'].endswith('/problem-openness'):
            values['/schema_version'] = 3
        elif native['kind'] != 'result':
            values['/schema_version'] = 1
        if native['kind'] == 'result' and not rule['id'].endswith('/problem-openness'):
            values['/findings/openness/statement_digest'] = native['claim_digest']
            if 'openness_minimum' in native.get('scientific_context', {}):
                values['/findings/openness/minimum'] = copy.deepcopy(native['scientific_context']['openness_minimum'])
    elif rule['role'] in ('bar', 'slate', 'result'):
        values = {'/stage': rule['role'], '/value/bar_ids': copy.deepcopy(packet.get('bar_ids',
                    [row['id'] for row in packet.get('bars', [])]))}
        if rule['id'].endswith('study-readiness'):
            values['/support/candidate_digest'] = packet['support_contract']['candidate_digest']
            values['/support/assessor'] = copy.deepcopy(packet['assessor'])
    else:
        values = {'/objection_id': packet['objection']['id']} if rule['role'] == 'adjudicator' else {}
    if 'context_repair' in packet:
        values['/reassessment/assignment_id'] = packet['context_repair']['assignment_id']
    return values


def split_output(rule, packet, output, pages):
    """Project stored output without normalizing, validating or altering its bytes."""
    if rule['id'].endswith('/math-openness'):
        return _split_math_output(rule, packet, output, pages)
    if rule['id'].endswith('/problem-openness'):
        return _split_finding(rule, packet, output, pages)
    if _uses_native_assessment(rule, output):
        return _split_native_assessment(rule, packet, output, pages)
    targets = list_targets(rule, packet)
    ids = [row['id'] for row in targets]
    rows = {row['id']: dict(target=row.get('handle', 't' + str(index)), fields={}, sentence='')
            for index, row in enumerate(targets)}
    closing = dict(fields={}, bindings=[])
    harness = _build_harness_fields(rule, packet)
    for path, value in _list_rule_values(rule, output):
        owners = _find_field_owners(rule, path, value)
        if len(owners) != 1:
            raise ResearchError('review_composition_conflict', FINAL_OWNER_INVALID_MESSAGE, dict(path=path))
        field = owners[0]
        if field['owner'] == 'harness':
            for root in harness:
                if path == root or path.startswith(root + '/'):
                    if root not in closing['bindings']:
                        closing['bindings'].append(root)
                    break
        elif field['owner'] == 'closing':
            closing['fields'][path] = copy.deepcopy(value)
        else:
            target = (ids[0] if isinstance(value, (dict, list)) and not value else
                      _find_target_id(rule, packet, output, field, path))
            if target not in rows:
                raise ResearchError('review_composition_conflict', FINAL_OWNER_INVALID_MESSAGE, dict(path=path, target=target))
            rows[target]['fields'][path] = copy.deepcopy(value)
    for row in rows.values():
        row['sentence'] = _describe_target_sentence(row['fields'])
    return [_wrap_page_output(page, [copy.deepcopy(rows[_resolve_target_id(targets, key)]) for key in page])
            for page in pages], closing


def _resolve_target_id(targets, value):
    key = value.get('id') if isinstance(value, dict) else value
    ids = [row['id'] for row in targets]
    if key in ids:
        return key
    by_handle = {row['handle']: row['id'] for row in targets if 'handle' in row}
    if key in by_handle:
        return by_handle[key]
    if isinstance(key, str) and re.fullmatch(r't[0-9]+', key) and int(key[1:]) < len(ids):
        return ids[int(key[1:])]
    raise ResearchError('review_composition_conflict', FINAL_PAGE_INVALID_MESSAGE, dict(target=key))


def _wrap_page_output(targets, rows):
    return dict(targets=copy.deepcopy(targets), output=dict(targets=rows))


def _render_formulation_label(target):
    # Preserve mathematical notation; escape only breaks in a multiline label.
    return ''.join('\\u%04x' % ord(character) if character in COVERAGE_LINE_BREAKS else character
                   for character in target['label'])


def _compose_math_output(rule, packet, pages, closing):
    if closing['decision'] != 'approve':
        return copy.deepcopy(closing)
    native = packet.get('native', packet)
    output = copy.deepcopy(closing)
    output.update(schema_version=2, **{key: copy.deepcopy(native[key])
                  for key in ('subject_digest', 'claim_digest', 'reviewer')})
    targets = list_targets(rule, packet)
    entries = [row for page in pages for row in page['output']['targets']]
    lines = []
    for row in entries:
        identity = _resolve_target_id(targets, row['formulation'])
        target = next(target for target in targets if target['id'] == identity)
        lines.append(_render_formulation_label(target) + ': ' + row['coverage'])
    output['findings']['openness'].update(
        statement_digest=native['claim_digest'],
        minimum=copy.deepcopy(native['scientific_context']['openness_minimum']),
        coverage='\n'.join(lines))
    return output


def _split_math_output(rule, packet, output, pages):
    if output['decision'] != 'approve':
        return [], copy.deepcopy(output)
    targets = list_targets(rule, packet)
    coverage = output['findings']['openness']['coverage']
    lines = coverage.split('\n')
    rows = {}
    if len(lines) != len(targets):
        raise ResearchError('review_composition_conflict', MATH_COVERAGE_INVALID_MESSAGE)
    for index, (target, line) in enumerate(zip(targets, lines)):
        prefix = _render_formulation_label(target) + ': '
        if not line.startswith(prefix):
            raise ResearchError('review_composition_conflict', MATH_COVERAGE_INVALID_MESSAGE)
        text = line[len(prefix):]
        rows[target['id']] = dict(formulation='t' + str(index), coverage=text,
                                  sentence=_describe_target_sentence({'coverage': text}))
    closing = dict(decision='approve', findings=copy.deepcopy(output['findings']))
    for key in ('statement_digest', 'minimum', 'coverage'):
        closing['findings']['openness'].pop(key)
    return [_wrap_page_output(page, [copy.deepcopy(rows[_resolve_target_id(targets, key)]) for key in page])
            for page in pages], closing


def compose_output(rule, packet, page_outputs, closing_output):
    """Assemble only owned fields. Authentication and edge validation are callers' work."""
    if rule['id'].endswith('/math-openness'):
        return _compose_math_output(rule, packet, page_outputs, closing_output)
    if rule['id'].endswith('/problem-openness'):
        return _compose_finding(rule, packet, page_outputs, closing_output)
    if 'assessment' in closing_output:
        return copy.deepcopy(closing_output['assessment'])
    output = {}
    for path, value in closing_output['fields'].items():
        _write_path(output, path, value)
    harness = _build_harness_fields(rule, packet)
    for path in closing_output['bindings']:
        _write_path(output, path, harness[path])
    for page in page_outputs:
        for row in page['output']['targets']:
            for path, value in row['fields'].items():
                _write_path(output, path, value)
    return output


def _split_finding(rule, packet, output, pages):
    targets = list_targets(rule, packet)
    coverage = output['findings']['openness']['coverage']
    rows = {}
    for index, target in enumerate(targets):
        value = coverage[index] if isinstance(coverage, list) else coverage
        rows[target['id']] = dict(formulation='t' + str(index), coverage=copy.deepcopy(value), grounds=[],
                                  sentence=_describe_target_sentence({'coverage': value}))
    closing = {key: copy.deepcopy(output['findings'][key]) for key in ('statement', 'assumptions', 'scope', 'dependencies', 'policy')}
    closing.update({key: copy.deepcopy(output['findings']['openness'][key]) for key in ('outcome', 'reason', 'grounds')})
    # Legacy findings allow one unstructured string; keep this inverse exact.
    if isinstance(coverage, str):
        closing['coverage_format'] = 'text'
    return [_wrap_page_output(page, [copy.deepcopy(rows[_resolve_target_id(targets, key)]) for key in page])
            for page in pages], closing


def _compose_finding(rule, packet, page_outputs, closing):
    from .native_review import RESULT_FINDINGS
    output = {}
    for path, value in _build_harness_fields(rule, packet).items():
        _write_path(output, path, value)
    output['findings'] = {key: copy.deepcopy(closing[key]) for key in RESULT_FINDINGS}
    entries = [row for page in page_outputs for row in page['output']['targets']]
    coverage = [copy.deepcopy(row['coverage']) for row in entries]
    if closing.get('coverage_format') == 'text':
        coverage = _compose_coverage_text(coverage)
    output['findings']['openness'] = dict(outcome=closing['outcome'], coverage=coverage, reason=closing['reason'],
        grounds=[copy.deepcopy(ground) for row in entries for ground in row['grounds']] + copy.deepcopy(closing['grounds']))
    return output


def _measure_final_value(value):
    import json
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def _compose_coverage_text(values):
    # An earlier native final has one collective assessment. Replay it on each
    # formulation without cutting a sentence into invented per-target claims.
    # Fresh, different assessments retain their order in the native string field.
    return values[0] if all(value == values[0] for value in values) else '\n'.join(values)


def _describe_target_sentence(fields):
    for path, value in fields.items():
        if isinstance(value, str) and value.strip() and (path.endswith('/reason') or path.startswith('/findings/') or path == 'coverage'):
            return value[:SENTENCE_CHARACTERS]
        if isinstance(value, dict) and isinstance(value.get('assessment'), str) and value['assessment'].strip():
            return value['assessment'][:SENTENCE_CHARACTERS]
    return TARGET_WITHOUT_ASSESSMENT_SENTENCE


class _PacketArtifacts:
    """Read only bytes carried by the supplied packet; never consult a path."""
    def __init__(self, packet):
        self.packet = packet

    def read(self, reference):
        import base64
        for row in self.packet.get('evidence', []):
            if row.get('artifact') == reference:
                if row.get('encoding') == 'utf-8':
                    return row['content'].encode('utf-8')
                if row.get('encoding') == 'base64':
                    return base64.b64decode(row['content'])
        raise ResearchError('artifact_missing', PACKET_ARTIFACT_MISSING_MESSAGE, dict(artifact=reference))


def _validate_packet_evidence(packet, values, **kwargs):
    from .review_refusal import validate_evidence_contract
    return validate_evidence_contract({}, _PacketArtifacts(packet), values, packets=[packet], **kwargs)


def _build_neutral_value(rule, packet):
    from . import research_decisions
    stage = rule['role']
    problem = packet.get('subject', {}).get('kind') == 'problem'
    evidence = [dict(kind='record', record_kind='review', id='shape-only', digest='0' * 64)]
    expected = () if stage == 'bar' else ASSURANCES if problem else research_decisions.ALL_CHECKS
    value = dict(status='unresolved', consequence='Shape check.', objective_adequacy='not_assessed',
                 method_adequacy='not_assessed', reason='Shape check.', evidence=evidence,
                 bar_ids=[] if stage == 'bar' else ['shape-a', 'shape-b'], work_items=[], objection_findings=[],
                 assurances=[dict(kind=key, status='not_assessed' if problem else 'unresolved',
                                  reason='Shape check.', evidence=evidence) for key in expected])
    if problem and stage == 'result':
        value['rubric'] = dict(admission={key: 'pass' for key in ADMISSION_ITEMS}, significance={key: 0 for key in GROUNDS},
                               priority=0, attempt='pass', accessibility='E-F', calibration='pass')
        value['rubric_evidence'] = {key: 'Shape check.' for key in ITEMS}
    if problem and stage == 'slate':
        value['bar_ids'] = packet['bar_ids']
        value['rubric_potential'] = {row['id']: dict(significance={key: 0 for key in GROUNDS}, priority=0)
                                     for row in packet['candidates']}
    return value


def _validate_value_fields(rule, packet, fields, required=None):
    from . import research_decisions
    from .review_refusal import contract_check
    value = _build_neutral_value(rule, packet)
    neutral_checks = copy.deepcopy(value['assurances'])
    if required is not None:
        value['assurances'] = [row for row in value['assurances'] if row['kind'] != required]
        if 'rubric' in value and required in ITEMS:
            grades = value['rubric']['admission'] if required in ADMISSION_ITEMS else value['rubric']['significance'] if required in GROUNDS else value['rubric']
            grades.pop(required)
            value['rubric_evidence'].pop(required)
        if 'rubric_potential' in value:
            value['rubric_potential'].pop(required, None)
    output = dict(value=value)
    # Collections are replaced, so a malformed/missing member is not repaired by
    # neutral siblings. Other targets' collections are unnecessary to this check.
    for name in ('assurances', 'work_items', 'objection_findings'):
        if any(path.startswith('/value/' + name + '/') or path == '/value/' + name for path in fields):
            value[name] = []
    for path, item in fields.items():
        if path.startswith('/value/'):
            _write_path(output, path, item)
    for name in ('assurances', 'work_items', 'objection_findings'):
        if isinstance(value[name], list):
            value[name] = [row for row in value[name] if row is not None]
    if any(path.startswith('/value/assurances') for path in fields):
        changed_kinds = [row.get('kind') for row in value['assurances'] if isinstance(row, dict)]
        value['assurances'] = [row for row in neutral_checks if row['kind'] not in changed_kinds] + value['assurances']
    problem = packet.get('subject', {}).get('kind') == 'problem'
    expected = tuple(row['kind'] for row in neutral_checks)
    work_ids = [row.get('id') for row in value['work_items'] if isinstance(row, dict)]
    if required in packet.get('dossier', {}).get('work_items', []) and required not in work_ids:
        work_ids.append(required)
    objections = [dict(id=row.get('id')) for row in value['objection_findings'] if isinstance(row, dict)]
    from .review_parts_problem_result import list_historical_objections
    prior = list(packet.get('material_objections', []))
    prior.extend(row for row, path in list_historical_objections(packet))
    prior.extend((packet.get('dossier', {}).get('material_change') or {}).get('prior_objections', []))
    if required in {row['id'] for row in prior} and required not in [row['id'] for row in objections]:
        objections.append(dict(id=required))
    dossier = dict(payload=dict(work_items=work_ids, material_change=dict(prior_objections=objections)))
    with contract_check('review.value'):
        research_decisions._validate_value({}, None, value, rule['role'], dossier,
            expected_checks=expected, problem={} if problem else None,
            problem_objections=objections if problem else (), slate_packet=packet,
            evidence_validator=lambda records, artifacts, values, **kwargs: _validate_packet_evidence(packet, values, **kwargs),
            require_two_bars=False)


def _build_neutral_native(packet):
    from . import native_review
    native = packet.get('native', packet)
    kind = native['kind']
    names = {'proposal': native_review.PROPOSAL_FINDINGS, 'result': native_review.RESULT_FINDINGS,
             'recovery': native_review.RECOVERY_FINDINGS}[kind]
    output = dict(subject_digest=native['subject_digest'], reviewer=copy.deepcopy(native['reviewer']), decision='approve',
                  findings={key: 'Shape check.' for key in names})
    if kind != 'recovery':
        output['claim_digest'] = native['claim_digest']
    if kind != 'result':
        output.update(schema_version=1, unresolved_objections=[])
    return output


def _validate_native_fields(packet, fields, required=None):
    from . import native_review
    from .review_refusal import contract_check
    output = _build_neutral_native(packet)
    if required is not None:
        output['findings'].pop(required, None)
    for path, value in fields.items():
        _write_path(output, path, value)
    if output.get('schema_version') == 2 and 'openness' not in output['findings']:
        output['findings']['openness'] = dict(outcome='unresolved',
            statement_digest=packet.get('native', packet)['claim_digest'], evidence=['0' * 64],
            searched_at='2000-01-01', coverage='Shape check.', reason='Shape check.')
    if 'openness' in output['findings']:
        output['schema_version'] = 2
        output['findings']['openness'].setdefault('statement_digest', packet.get('native', packet)['claim_digest'])
    with contract_check('native.output'):
        native_review.validate_output(packet.get('native', packet), output)


def _validate_finding_fields(packet, rows=None, closing=None, *, finding_version=1):
    from . import native_review
    # The full finding validator is the only home of these checks. A blocked
    # neutral forecast admits either ground presence for a page or no grounds.
    neutral = copy.deepcopy(packet)
    native = neutral.get('native', neutral)
    if rows is not None:
        from .review_refusal import contract_check
        for row in rows:
            with contract_check('openness.finding'):
                native_review._require_finding(isinstance(row['grounds'], list), 'grounds_list',
                    '/findings/openness/grounds', native['scientific_context']['audit_forecast'])
    native['scientific_context']['audit_forecast']['outcomes'] = dict(supported_open='blocked', already_solved='blocked')
    count = len(rows) if rows is not None else 1
    if packet.get('reading_final', {}).get('protocol') == 'exactory-openness-reading-v7':
        neutral['reading_final'] = dict(neutral['reading_final'], page=dict(targets=list(range(count))))
        # Unread-set relations concern the complete composed answer. Keep the
        # checked excerpts here for named gaps, without running that final check.
        neutral['reading_final'].pop('unread', None)
    else:
        native['scientific_context']['openness_minimum']['neighbours'] = list(
            native['scientific_context']['openness_minimum']['neighbours'][:max(0, count - 1)])
    findings = {key: 'Shape check.' for key in native_review.RESULT_FINDINGS}
    openness = dict(outcome='unresolved', coverage=[row['coverage'] for row in rows] if rows is not None else ['Checked.'],
                    reason='Shape check.', grounds=[ground for row in rows for ground in row['grounds']] if rows is not None else [])
    if closing is not None:
        findings.update({key: closing[key] for key in native_review.RESULT_FINDINGS})
        openness.update({key: closing[key] for key in ('outcome', 'reason', 'grounds')})
        # Preserve the actual outcome vocabulary, while testing its relation to
        # page grounds only on the final composed finding.
        native['scientific_context']['audit_forecast']['outcomes'] = dict(supported_open='possible', already_solved='possible')
        if openness['outcome'] == 'unresolved' and not openness['grounds']:
            native['scientific_context']['audit_forecast']['outcomes'] = dict(supported_open='blocked', already_solved='blocked')
    findings['openness'] = openness
    output = dict(schema_version=3, findings=findings, **{key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})
    native_review.validate_finding(neutral, output, finding_version=finding_version)


def _validate_readiness_fields(packet, output, required=None):
    from . import development
    from .review_refusal import contract_check
    with contract_check('review.support'):
        checks = output.get('support', {}).get('checks', [])
        if required in development._REVIEW_CHECKS and not checks:
            development._fields({}, ('kind', 'status', 'reason', 'evidence'))
        for check in development._items(checks, 'Independent candidate checks'):
            development._fields(check, ('kind', 'status', 'reason', 'evidence'))
            development._choice(check['kind'], development._REVIEW_CHECKS, 'Readiness check')
            development._choice(check['status'], ('passed', 'failed', 'unresolved'), 'Independent check outcome')
            development._text(check['reason'], 'Independent check reasoning')
            for reference in development._items(check['evidence'], 'Independent review evidence', True):
                if not isinstance(reference, dict) or reference.get('kind') not in ('source', 'result'):
                    raise ResearchError('invalid_development', 'Evidence must identify an actual source Link or an execution output and locator')
                if reference['kind'] == 'source':
                    development._fields(reference, ('kind', 'link'))
                    _validate_packet_evidence(packet, [reference])
                else:
                    # The result grammar and locator are checked by their owner;
                    # execution currency remains with the ordinary role validator.
                    from .operations import fields
                    from .artifacts import validate_reference
                    from .source_links import read_locator
                    fields(reference, ('kind', 'execution_id', 'output_id', 'artifact', 'locator'), code='invalid_development')
                    validate_reference(reference['artifact'])
                    read_locator(_PacketArtifacts(packet), reference['artifact'], reference['locator'], result=True)


def _validate_verification_fields(packet, output, required=None):
    from .operations import fields, text
    from .review_parts_study import VERIFICATION_CHECKS
    from .review_refusal import contract_check
    with contract_check('review.shape'):
        seen = set()
        checks = output.get('checks', [])
        if required in VERIFICATION_CHECKS and not checks:
            fields({}, ('dimension', 'reason', 'evidence'), code='invalid_value_review')
        for check in checks:
            fields(check, ('dimension', 'reason', 'evidence'), code='invalid_value_review')
            if check['dimension'] not in VERIFICATION_CHECKS or check['dimension'] in seen:
                raise ResearchError('invalid_value_review', 'Assess each verification dimension exactly once')
            seen.add(check['dimension'])
            text(check['reason'], 'Verification reasoning', code='invalid_value_review')
            if not isinstance(check['evidence'], list):
                raise ResearchError('invalid_value_review', 'Verification evidence is an array of source links')
            _validate_packet_evidence(packet, [dict(kind='source', link=link) for link in check['evidence']])
            if check['dimension'] == 'soundness' and not any(link['version_id'] == packet['paper']['target']['id']
                                                            for link in check['evidence']):
                raise ResearchError('invalid_value_review', 'Soundness evidence includes the exact target')


def _validate_adjudicator_fields(packet, output):
    from .operations import text
    from .review_refusal import contract_check
    with contract_check('adjudication.shape'):
        allowed = ('upheld', 'not_upheld', 'unresolved')
        if packet.get('subject', {}).get('kind') == 'problem' and 'rubric_definition' not in packet:
            allowed += ('not_binding',)
        if output.get('disposition') not in allowed:
            raise ResearchError('invalid_review_adjudication', 'Use a focused objection disposition, not a third vote')
        if output.get('correction_admissible') is not None and type(output['correction_admissible']) is not bool:
            raise ResearchError('invalid_review_adjudication', 'Correction admissibility must be null or a boolean finding')
        text(output.get('reason'), 'Adjudication reason')


def _validate_historical_fields(packet, output, required=None):
    from .operations import fields, text
    from .strategy import choice
    from .review_refusal import contract_check
    with contract_check('review.reassessment'):
        findings = output.get('reassessment', {}).get('findings', [])
        identities = {row['id'] for row in packet.get('historical_reviews', [])}
        if required in identities and not findings:
            raise ResearchError('review_reassessment_incomplete', 'Address every inherited final review')
        seen = set()
        for finding in findings:
            fields(finding, ('review_id', 'disposition', 'reason', 'evidence'), code='review_reassessment_mismatch')
            text(finding['review_id'], 'Historical review ID', code='review_reassessment_mismatch')
            if finding['review_id'] in seen or finding['review_id'] not in identities:
                raise ResearchError('review_reassessment_incomplete', 'Address each inherited final review exactly once')
            seen.add(finding['review_id'])
            choice(finding['disposition'], ('confirmed', 'revised', 'unresolved'), 'Reassessment disposition', 'review_reassessment_mismatch')
            text(finding['reason'], 'Reassessment scientific reasoning', code='review_reassessment_mismatch')
            _validate_packet_evidence(packet, finding['evidence'], review_binding=packet.get('subject', {}).get('kind') == 'problem')


def _compact_page_lists(value):
    if isinstance(value, list):
        return [_compact_page_lists(item) for item in value if item is not None]
    if isinstance(value, dict):
        return {key: _compact_page_lists(item) for key, item in value.items()}
    return value


def validate_page_output(rule, packet, targets, output, *, finding_version=1):
    """Refuse page-owned fields here under the original role's check name."""
    from .review_refusal import contract_check
    definitions = list_targets(rule, packet)
    expected = [_resolve_target_id(definitions, key) for key in targets]
    finding = rule['id'].endswith('/problem-openness')
    math = rule['id'].endswith('/math-openness')
    key = 'formulation' if finding or math else 'target'
    with contract_check('review.shape'):
        check_fields(output, ('targets',), code='review_composition_conflict')
        if not isinstance(output['targets'], list):
            raise ResearchError('review_composition_conflict', FINAL_PAGE_INVALID_MESSAGE)
        actual = []
        for row in output['targets']:
            check_fields(row, (key, 'coverage', 'grounds', 'sentence') if finding else
                         (key, 'coverage', 'sentence') if math else ('target', 'fields', 'sentence'),
                         code='review_composition_conflict')
            actual.append(_resolve_target_id(definitions, row[key]))
            if not isinstance(row['sentence'], str) or not row['sentence'].strip() or len(row['sentence']) > SENTENCE_CHARACTERS:
                raise ResearchError('review_composition_conflict', FINAL_PAGE_INVALID_MESSAGE)
        if actual != expected or len(set(actual)) != len(actual):
            raise ResearchError('review_composition_conflict', FINAL_PAGE_INVALID_MESSAGE)
    if finding:
        _validate_finding_fields(packet, output['targets'], finding_version=finding_version)
    elif math:
        from . import native_review
        with contract_check('native.output'):
            for row in output['targets']:
                coverage = row['coverage']
                native_review._require(native_review._nonempty(coverage) and len(coverage) <= COVERAGE_CHARACTERS
                    and not any(character in COVERAGE_LINE_BREAKS for character in coverage),
                    'Formulation coverage is nonempty text of at most 800 characters on one line',
                    path='/findings/openness/coverage')
    else:
        for target, row in zip(actual, output['targets']):
            if not isinstance(row['fields'], dict):
                raise ResearchError('review_composition_conflict', FINAL_PAGE_INVALID_MESSAGE)
            projected = {}
            for path, value in row['fields'].items():
                owners = _find_field_owners(rule, path, value)
                if len(owners) != 1 or owners[0]['owner'] != 'page':
                    raise ResearchError('review_composition_conflict', FINAL_OWNER_INVALID_MESSAGE, dict(path=path))
                _write_path(projected, path, value)
            if rule['role'] in ('bar', 'slate', 'result'):
                _validate_value_fields(rule, packet, row['fields'], required=target)
            if rule['role'] == 'native_math':
                _validate_native_fields(packet, row['fields'], required=target if target != 'openness' else None)
            adapter = rule.get('base_adapter', rule['adapter'])
            if adapter == 'study_verification':
                _validate_verification_fields(packet, _compact_page_lists(projected), required=target)
            if adapter == 'study_readiness':
                _validate_readiness_fields(packet, _compact_page_lists(projected), required=target)
            if rule['role'] == 'adjudicator' and target == 'objection':
                _validate_adjudicator_fields(packet, dict(disposition='unresolved', correction_admissible=None, **projected))
                _validate_packet_evidence(packet, projected['evidence'], review_binding=packet.get('subject', {}).get('kind') == 'problem')
            if 'context_repair' in packet:
                _validate_historical_fields(packet, _compact_page_lists(projected), required=target)
            for path, value in row['fields'].items():
                owner = _find_field_owners(rule, path, value)[0]
                assigned = (target if isinstance(value, (dict, list)) and not value else
                            _find_target_id(rule, packet, projected, owner, path))
                if assigned not in (target, 'formulation') and not isinstance(value, (dict, list)):
                    raise ResearchError('review_composition_conflict', FINAL_PAGE_INVALID_MESSAGE, dict(path=path))
    with contract_check('review.shape'):
        for row in output['targets']:
            values = {k: v for k, v in row.items() if k not in ('sentence', key)}
            if _measure_final_value(values) > PAGE_FIELDS_CHARACTERS or _measure_final_value(row) > PAGE_ROW_CHARACTERS:
                raise ResearchError('review_composition_conflict', FINAL_PAGE_BOUND_MESSAGE)
        _validate_answer_schema(rule, rule['page_schema'], output)


def describe_page_answer_bound(rule, targets):
    """A serialized row cap counts every field, pointer, quote and escape."""
    return PAGE_ENVELOPE_CHARACTERS + (rule['limits']['row'] + 1) * targets


def _collect_reference_handles(packet, value):
    references = {digest(entry['reference']): handle for handle, entry in packet.get('handle_table', {}).items()
                  if 'reference' in entry}
    found = set()
    pending = [value]
    while pending:
        item = pending.pop()
        handle = references.get(digest(item))
        if handle is not None:
            found.add(handle)
        if isinstance(item, dict):
            if isinstance(item.get('ref'), str):
                found.add(item['ref'])
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return sorted(found, key=lambda handle: (_measure_final_value(handle), handle))


def build_decision_rows(rule, packet, page_outputs, *, measure=None):
    """Rows are [target handle, grade/status, sentence, evidence handles].

    Select the largest number of whole handles that fits each actual row, with
    shorter handles first. JSON escapes count. A sentence is retained up to the
    space left by the target and status; the page answer remains unchanged.
    """
    from .review_route_capacity import DECISION_ROW_CHARACTERS
    measure = measure or _measure_final_value
    definitions = list_targets(rule, packet)
    handles = {target['id']: target.get('handle', 't' + str(index)) for index, target in enumerate(definitions)}
    result = []
    for page in page_outputs:
        for item in page['output']['targets']:
            formulation = 'formulation' in item
            target = _resolve_target_id(definitions, item['formulation'] if formulation else item['target'])
            if formulation and 'grounds' in item:
                status = sorted({ground['kind'] for ground in item['grounds']})
            elif formulation:
                status = 'assessed'
            else:
                grades = [value for path, value in item['fields'].items()
                          if path.endswith(('/status', '/disposition', '/classification')) or '/rubric/' in path]
                status = grades[0] if len(grades) == 1 else grades if grades else 'assessed' if item['fields'] else 'not_assessed'
            row = [handles[target], status, item['sentence'], []]
            while measure(row) > DECISION_ROW_CHARACTERS and row[2]:
                row[2] = row[2][:-1]
            for handle in _collect_reference_handles(packet, item.get('grounds', item.get('fields'))):
                if measure(row[:3] + [row[3] + [handle]]) <= DECISION_ROW_CHARACTERS:
                    row[3].append(handle)
                else:
                    break
            if measure(row) > DECISION_ROW_CHARACTERS:
                raise ResearchError('review_composition_conflict', FINAL_PAGE_BOUND_MESSAGE)
            result.append(row)
    return result


def validate_closing_output(rule, packet, rows, output, *, finding_version=1):
    """Check closing fields; cross-page scientific checks run on composition."""
    from . import native_review
    from .operations import strings
    from .review_refusal import contract_check
    if rule['id'].endswith('/math-openness'):
        _validate_math_closing(rule, packet, output)
        return
    if rule['id'].endswith('/problem-openness'):
        check_fields(output, native_review.RESULT_FINDINGS + ('outcome', 'reason', 'grounds'), ('coverage_format',),
                     code='review_composition_conflict')
        _validate_finding_fields(packet, closing=output, finding_version=finding_version)
        if _measure_final_value(output) > CLOSING_FIELDS_CHARACTERS:
            raise ResearchError('review_composition_conflict', FINAL_PAGE_BOUND_MESSAGE)
        with contract_check('review.shape'):
            _validate_answer_schema(rule, rule['closing_schema'], output)
        return
    if rule.get('native_kind') in ('result', 'recovery') and 'assessment' in output:
        with contract_check('native.output'):
            native_review.validate_output(packet.get('native', packet), output['assessment'])
        with contract_check('review.shape'):
            _validate_answer_schema(rule, rule['closing_schema'], output)
        return
    with contract_check('review.shape'):
        check_fields(output, ('fields', 'bindings'), code='review_composition_conflict')
        if not isinstance(output['fields'], dict) or not isinstance(output['bindings'], list):
            raise ResearchError('review_composition_conflict', FINAL_CLOSING_INVALID_MESSAGE)
        harness = _build_harness_fields(rule, packet)
        if (not all(isinstance(path, str) for path in output['bindings'])
                or len(set(output['bindings'])) != len(output['bindings'])
                or any(path not in harness for path in output['bindings'])):
            raise ResearchError('review_composition_conflict', FINAL_CLOSING_INVALID_MESSAGE)
        for path, value in output['fields'].items():
            owners = _find_field_owners(rule, path, value)
            if len(owners) != 1 or owners[0]['owner'] != 'closing':
                raise ResearchError('review_composition_conflict', FINAL_OWNER_INVALID_MESSAGE, dict(path=path))
    fields = output['fields']
    if rule['role'] == 'native_math':
        _validate_native_fields(packet, fields)
    elif rule['role'] in ('bar', 'slate', 'result'):
        _validate_value_fields(rule, packet, fields)
        with contract_check('review.value'):
            strings(fields.get('/limitations', []), 'Independent assessment limitations', nonempty=True)
        if '/objections' in fields:
            from .review_refusal import _validate_staged_objections
            objections = fields['/objections']
            if rule['role'] == 'slate' and packet.get('subject', {}).get('kind') == 'problem':
                from .operations import fields as validate_fields
                from . import strategy
                candidates = {row['id'] for row in packet['candidates']}
                with contract_check('review.objections'):
                    for row in strategy.items(objections, 'Material objections'):
                        validate_fields(row, ('id', 'claim', 'reason', 'evidence', 'resolution_condition', 'candidate_ids'),
                                        code='invalid_value_review')
                        strings(row['candidate_ids'], 'Candidates of a slate objection', code='invalid_value_review')
                        if not set(row['candidate_ids']) <= candidates:
                            raise ResearchError('invalid_value_review', 'Name only candidates of the slate packet')
                objections = [{key: value for key, value in row.items() if key != 'candidate_ids'} for row in objections]
            _validate_staged_objections({}, None, objections,
                lambda records, artifacts, values, **kwargs: _validate_packet_evidence(packet, values, **kwargs))
        if rule.get('base_adapter', rule['adapter']) == 'study_readiness':
            from . import development
            with contract_check('review.support'):
                development._text(fields.get('/support/id'), 'Readiness review ID')
                development._choice(fields.get('/support/verdict'), ('ready', 'not_ready', 'unresolved'), 'Independent readiness verdict')
                development._strings(fields.get('/support/limitations'), 'Independent assessment limits', True)
    elif rule['adapter'] == 'study_verification':
        from .verification import validate_verdict_body
        projected = {}
        for path, value in fields.items():
            _write_path(projected, path, value)
        with contract_check('review.shape'):
            validate_verdict_body(projected['verdict'])
    elif rule['role'] == 'adjudicator':
        projected = dict(reason='Shape check.')
        for path, value in fields.items():
            _write_path(projected, path, value)
        _validate_adjudicator_fields(packet, projected)
    if _measure_final_value(output) > CLOSING_FIELDS_CHARACTERS:
        raise ResearchError('review_composition_conflict', FINAL_PAGE_BOUND_MESSAGE)
    with contract_check('review.shape'):
        _validate_answer_schema(rule, rule['closing_schema'], output)


def _make_json_schema(characters):
    return dict(type='json', max_serialized_characters=characters,
                max_string_serialized_characters=min(characters, JSON_STRING_CHARACTERS),
                max_items=JSON_COLLECTION_ITEMS, max_properties=JSON_COLLECTION_ITEMS,
                max_key_serialized_characters=512, max_depth=16, overflow='recorded_handle')


def _make_text_schema(characters, serialized=None):
    return dict(type='string', max_characters=characters,
                max_serialized_characters=serialized if serialized is not None else 6 * characters + 2,
                overflow='recorded_handle')


def _make_object_schema(properties, required=None, bound=16000):
    return dict(type='object', properties=properties,
                required=list(properties) if required is None else list(required),
                additional_properties=False, max_serialized_characters=bound)


def _attach_answer_schemas(rule):
    """Version both answer grammars, including open native assessment branches.

    A json field has a bound at every descendant, including list lengths, key
    lengths and nesting. The joint budgets remain separate constraints: fields
    cannot all consume their independent maxima in the same answer.
    """
    rule['field_paths'] = [field['path'] + ('@' + field['when'] if field.get('when') else '')
                           for field in rule['fields']]
    for field in rule['fields']:
        field['schema'] = _make_json_schema(PAGE_FIELDS_CHARACTERS if field['owner'] == 'page' else JSON_FIELD_CHARACTERS)
    sentence = _make_text_schema(SENTENCE_CHARACTERS)
    identity = _make_text_schema(120, 722)
    math = rule['id'].endswith('/math-openness')
    finding = rule['id'].endswith('/problem-openness')
    fields = dict(type='field_map', owner='page', max_properties=64,
                  max_serialized_characters=PAGE_FIELDS_CHARACTERS)
    if math:
        properties = dict(formulation=identity, coverage=_make_text_schema(COVERAGE_CHARACTERS), sentence=sentence)
    elif finding:
        from .native_review import GROUND_RULES
        ground = _make_object_schema(dict(kind=dict(type='enum', values=list(GROUND_RULES), max_serialized_characters=32),
            text=_make_text_schema(GROUND_TEXT_CHARACTERS), work_id=_make_text_schema(256),
            refs=dict(type='array', items=_make_json_schema(1024), max_items=GROUND_REFERENCES,
                      max_serialized_characters=6000),
            # Stored v6 findings keep the reference spelling of their protocol.
            turns=dict(type='array', items=_make_text_schema(256), max_items=GROUND_REFERENCES,
                       max_serialized_characters=6000)), required=('kind', 'text'), bound=8000)
        if rule.get('finding_version') == 2:
            ground['properties'].update(formulation=_make_text_schema(6000),
                basis=dict(type='array', items=_make_json_schema(6000), max_items=GROUND_REFERENCES,
                           max_serialized_characters=6000), need=_make_text_schema(6000),
                unsettled=_make_text_schema(6000), work=_make_json_schema(6000), search=_make_json_schema(6000))
        grounds = dict(type='array', items=ground, max_items=GROUND_ITEMS, max_serialized_characters=8000)
        properties = dict(formulation=identity, coverage=_make_json_schema(COVERAGE_CHARACTERS),
                          grounds=grounds, sentence=sentence)
    else:
        properties = dict(target=identity, fields=fields, sentence=sentence)
    row = _make_object_schema(properties, bound=PAGE_ROW_CHARACTERS)
    page = _make_object_schema(dict(targets=dict(type='array', items=row, max_items=5000,
                                                max_serialized_characters=79988)), bound=80000)
    page.update(id=rule['id'] + '/page', target_payload_bound=PAGE_FIELDS_CHARACTERS)
    closing_fields = dict(type='field_map', owner='closing', max_properties=256,
                          max_serialized_characters=CLOSING_FIELDS_CHARACTERS)
    ordinary = _make_object_schema(dict(fields=closing_fields,
        bindings=dict(type='array', items=_make_text_schema(256), max_items=64, max_serialized_characters=16000)))
    if math:
        from .native_review import RESULT_FINDINGS
        findings = {key: _make_text_schema(2000) for key in RESULT_FINDINGS}
        findings['openness'] = _make_object_schema(dict(outcome=_make_text_schema(32),
            reason=_make_text_schema(FINDING_REASON_CHARACTERS), searched_at=_make_text_schema(10),
            evidence=dict(type='array', items=_make_text_schema(64, 66), max_items=32, max_serialized_characters=2200)))
        approval = _make_object_schema(dict(decision=dict(type='enum', values=['approve'], max_serialized_characters=9),
                                           findings=_make_object_schema(findings)))
        closing = dict(type='native_math_closing', approval=approval,
                       assessment=_make_json_schema(CLOSING_FIELDS_CHARACTERS),
                       max_serialized_characters=CLOSING_FIELDS_CHARACTERS)
    elif finding:
        from .native_review import RESULT_FINDINGS
        closing_properties = {key: _make_text_schema(2000) for key in RESULT_FINDINGS}
        closing_properties.update(outcome=_make_text_schema(32), reason=_make_text_schema(FINDING_REASON_CHARACTERS),
                                  grounds=grounds, coverage_format=dict(type='enum', values=['text'], max_serialized_characters=6))
        closing = _make_object_schema(closing_properties,
            required=RESULT_FINDINGS + ('outcome', 'reason', 'grounds'))
    elif rule.get('native_kind') in ('result', 'recovery'):
        closing = dict(type='native_closing', approval=ordinary,
            assessment=_make_object_schema(dict(assessment=_make_json_schema(CLOSING_FIELDS_CHARACTERS))),
            max_serialized_characters=CLOSING_FIELDS_CHARACTERS)
    else:
        closing = ordinary
    closing['id'] = rule['id'] + '/closing'
    rule.update(page_schema=page, closing_schema=closing,
                output_variants=_describe_output_variants(rule),
                validator_contracts=_describe_validator_contracts(rule))
    return rule


def _describe_output_variants(rule):
    kind = rule.get('native_kind')
    if rule['id'].endswith('/problem-openness'):
        return ['finding-structured-coverage', 'finding-legacy-text-coverage']
    if kind == 'proposal':
        return ['proposal-approve', 'proposal-revise', 'proposal-reject']
    if kind in ('result', 'recovery'):
        approvals = ['approval-v1-implicit', 'approval-v1-explicit', 'approval-v2'] if kind == 'result' else ['approval-v1']
        return approvals + ['revise-with-optional-extra-fields', 'reject-with-optional-extra-fields']
    if rule['role'] in ('bar', 'slate', 'result'):
        return ['assessment', 'source-requests', 'settled-requests', 'excerpts', 'reassessment',
                'support-null', 'support-object', 'current-rubric', 'previous-rubric', 'potential-readiness']
    if rule['role'] == 'verification':
        return ['verdict-with-required-fields', 'verdict-with-optional-fields']
    return ['focused-adjudication', 'focused-reassessment']


def _raise_schema_error(path):
    raise ResearchError('review_composition_conflict', ANSWER_SCHEMA_INVALID_MESSAGE, dict(path=path))


def _validate_json_limits(schema, value, path, depth=0):
    if depth > schema['max_depth']:
        _raise_schema_error(path)
    if isinstance(value, str) and _measure_final_value(value) > schema['max_string_serialized_characters']:
        _raise_schema_error(path)
    if isinstance(value, list):
        if len(value) > schema['max_items']:
            _raise_schema_error(path)
        for index, item in enumerate(value):
            _validate_json_limits(schema, item, path + '/' + str(index), depth + 1)
    elif isinstance(value, dict):
        if len(value) > schema['max_properties']:
            _raise_schema_error(path)
        for key, item in value.items():
            if _measure_final_value(key) > schema['max_key_serialized_characters']:
                _raise_schema_error(path)
            _validate_json_limits(schema, item, path + '/' + _escape_field(key), depth + 1)
    elif value is not None and type(value) not in (str, int, float, bool):
        _raise_schema_error(path)


def _validate_answer_schema(rule, schema, value, path=''):
    if _measure_final_value(value) > schema['max_serialized_characters']:
        _raise_schema_error(path)
    kind = schema['type']
    if kind == 'json':
        _validate_json_limits(schema, value, path)
    elif kind == 'string':
        if not isinstance(value, str) or len(value) > schema['max_characters']:
            _raise_schema_error(path)
    elif kind == 'enum':
        if value not in schema['values']:
            _raise_schema_error(path)
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > schema['max_items']:
            _raise_schema_error(path)
        for index, item in enumerate(value):
            _validate_answer_schema(rule, schema['items'], item, path + '/' + str(index))
    elif kind == 'object':
        if (not isinstance(value, dict) or not set(schema['required']) <= set(value)
                or not set(value) <= set(schema['properties'])):
            _raise_schema_error(path)
        for key, item in value.items():
            _validate_answer_schema(rule, schema['properties'][key], item, path + '/' + key)
    elif kind == 'field_map':
        if not isinstance(value, dict) or len(value) > schema['max_properties']:
            _raise_schema_error(path)
        for field_path, item in value.items():
            owners = _find_field_owners(rule, field_path, item)
            if len(owners) != 1 or owners[0]['owner'] != schema['owner']:
                _raise_schema_error(path + '/' + _escape_field(field_path))
            _validate_answer_schema(rule, owners[0]['schema'], item, field_path)
    elif kind in ('native_closing', 'native_math_closing'):
        assessment = 'assessment' in value if kind == 'native_closing' else value.get('decision') != 'approve'
        _validate_answer_schema(rule, schema['assessment' if assessment else 'approval'], value, path)


def _split_native_assessment(rule, packet, output, pages):
    targets = list_targets(rule, packet)
    rows = {}
    for index, target in enumerate(targets):
        fields = {} if target['id'] == 'openness' else {'/findings/' + target['id']: output['reason']}
        rows[target['id']] = dict(target='t' + str(index), fields=fields, sentence=output['reason'][:SENTENCE_CHARACTERS])
    return [_wrap_page_output(page, [copy.deepcopy(rows[_resolve_target_id(targets, key)]) for key in page])
            for page in pages], dict(assessment=copy.deepcopy(output))


def _validate_math_closing(rule, packet, output):
    from . import native_review
    from .review_refusal import contract_check
    with contract_check('native.output'):
        if isinstance(output, dict) and output.get('decision') == 'approve':
            native = packet.get('native', packet)
            scaffold = copy.deepcopy(output)
            scaffold.update(schema_version=2, **{key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})
            findings = scaffold.get('findings')
            if isinstance(findings, dict) and isinstance(findings.get('openness'), dict):
                findings['openness'].update(statement_digest=native['claim_digest'],
                    minimum=native['scientific_context']['openness_minimum'], coverage='Shape check.')
            native_review.validate_output(native, scaffold)
        else:
            native_review.validate_output(packet.get('native', packet), output)
    with contract_check('review.shape'):
        _validate_answer_schema(rule, rule['closing_schema'], output)


def _describe_validator_contracts(rule):
    """Independent closed-object declarations, including optional/legacy branches.

    The guard compares these with supplied validator source, not fixture values
    or a manifest regenerated from the ownership table. No source is read here.
    """
    from . import native_review, research_decisions
    def declare(validator, subject, path, fields, omit=(), bindings=None, call=None):
        return dict(validator=validator, subject=subject, path=path, fields=list(fields),
                    omit=list(omit), bindings=bindings or {}, call=call)
    contracts = []
    repair = 'base_adapter' in rule
    kind = rule.get('native_kind')
    if rule['id'].endswith('/problem-openness'):
        named = rule.get('finding_version') == 2
        ground_fields = ('kind', 'text', 'work_id', 'refs', 'turns')
        if named:
            ground_fields += ('formulation', 'basis', 'need', 'unsettled', 'work', 'search')
        for subject, path, names in (
                ('output', '', ('schema_version', 'subject_digest', 'claim_digest', 'reviewer', 'findings')),
                ('findings', '/findings', native_review.RESULT_FINDINGS + ('openness',)),
                ('openness', '/findings/openness', ('outcome', 'coverage', 'reason', 'grounds')),
                ('ground', '/findings/openness/grounds/*', ground_fields)):
            contracts.append(declare('native_review.validate_finding', subject, path, names,
                                     bindings=dict(gap=named) if subject == 'ground' else None))
        if named:
            contracts.extend((
                declare('native_review._validate_named_gap', 'ground', '/findings/openness/grounds/*',
                    ('kind', 'text', 'formulation', 'basis', 'need', 'unsettled', 'turns', 'refs', 'work', 'search')),
                declare('native_review._validate_named_gap', 'work', '/findings/openness/grounds/*/work',
                    ('title', 'identifiers')),
                declare('native_review._validate_named_gap', 'search', '/findings/openness/grounds/*/search',
                    ('terms', 'missing_term'))))
    elif kind:
        root = ('schema_version', 'subject_digest', 'reviewer', 'decision', 'findings')
        if kind != 'recovery':
            root += ('claim_digest',)
        if kind != 'result':
            root += ('unresolved_objections',)
        names = {'proposal': native_review.PROPOSAL_FINDINGS, 'result': native_review.RESULT_FINDINGS + ('openness',),
                 'recovery': native_review.RECOVERY_FINDINGS}[kind]
        contracts.extend((declare('native_review.validate_output', 'output', '', root, bindings=dict(kind=kind)),
                          declare('native_review.validate_output', 'output/findings', '/findings', names,
                                  bindings=dict(kind=kind))))
        if kind == 'result':
            contracts.append(declare('native_review._validate_openness', 'openness', '/findings/openness',
                ('outcome', 'statement_digest', 'evidence', 'searched_at', 'coverage', 'reason', 'minimum')))
    elif rule['role'] == 'verification':
        contracts.extend((declare('review_parts_study.validate_verification_final', 'output', '', ('verdict', 'checks')),
            declare('review_parts_study.validate_verification_final', 'check', '/checks/*', ('dimension', 'reason', 'evidence')),
            declare('verification.validate_verdict_body', 'body', '/verdict',
                ('stance', 'summary', 'prediction', 'rationaleSections', 'wouldChange', 'suggestions', 'findings', 'supersedesVerdictId'))))
    elif rule['role'] == 'adjudicator':
        contracts.append(declare('review_protocol.validate_adjudication_contract', 'output', '',
            ('objection_id', 'disposition', 'reason', 'evidence', 'correction_admissible', 'reassessment'),
            omit=() if repair else ('reassessment',)))
    else:
        contracts.extend((declare('research_decisions.validate_review_contract', 'output', '',
            ('stage', 'support', 'value', 'objections', 'limitations', 'source_requests', 'settled_requests', 'reassessment', 'excerpts'),
            omit=() if repair else ('reassessment',)),
            declare('research_decisions._validate_value', 'value', '/value',
                ('status', 'consequence', 'bar_ids', 'objective_adequacy', 'method_adequacy', 'reason', 'evidence',
                 'work_items', 'assurances', 'objection_findings', 'rubric', 'rubric_evidence', 'rubric_potential'),
                omit=tuple(name for name in ('rubric', 'rubric_evidence', 'rubric_potential')
                           if not any(field['path'].startswith('/value/' + name + '/') for field in rule['fields']))),
            declare('research_decisions._validate_checks', 'check', '/value/assurances/*', ('kind', 'status', 'reason', 'evidence')),
            declare('research_decisions._validate_value', 'finding', '/value/work_items/*',
                ('id', 'classification', 'status', 'reason', 'evidence'), call=0),
            declare('research_decisions._validate_value', 'finding', '/value/objection_findings/*',
                ('id', 'status', 'reason', 'evidence'), call=1)))
        if rule.get('base_adapter', rule['adapter']) == 'study_readiness':
            contracts.extend((declare('development._review', 'value', '/support',
                ('id', 'candidate_digest', 'assessor', 'verdict', 'checks', 'limitations')),
                declare('development._review', 'assessor', '/support/assessor',
                    ('id', 'kind', 'provenance', 'relationship', 'independence_basis', 'author_memory_notes_present')),
                declare('development._review', 'check', '/support/checks/*', ('kind', 'status', 'reason', 'evidence'))))
    if repair:
        contracts.extend((declare('review_context_repair.validate_reassessment', 'reassessment', '/reassessment',
            ('assignment_id', 'findings')), declare('review_context_repair.validate_reassessment', 'finding',
            '/reassessment/findings/*', ('review_id', 'disposition', 'reason', 'evidence'))))
    return contracts


def _read_validator_subject(node):
    import ast
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Index):
            key = key.value
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return _read_validator_subject(node.value) + '/' + key.value
    return ''


def _read_schema_condition(node, bindings):
    import ast
    def read_value(item):
        if isinstance(item, ast.Constant):
            return item.value
        if isinstance(item, ast.Name) and not isinstance(bindings.get(item.id), ast.AST):
            return bindings.get(item.id)
        return None
    if isinstance(node, ast.Constant) and type(node.value) is bool:
        return node.value
    if isinstance(node, ast.Name) and type(bindings.get(node.id)) is bool:
        return bindings[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _read_schema_condition(node.operand, bindings)
        return None if value is None else not value
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = read_value(node.left), read_value(node.comparators[0])
        if left is not None and right is not None:
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
    return None


def _read_schema_names(node, bindings, seen=()):
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name) and node.id not in seen:
        value = bindings.get(node.id)
        if isinstance(value, ast.AST):
            return _read_schema_names(value, bindings, seen + (node.id,))
        if isinstance(value, (tuple, list, set)):
            return set(value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set().union(*(_read_schema_names(item, bindings, seen) for item in node.elts))
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.BitOr, ast.Sub)):
        left = _read_schema_names(node.left, bindings, seen)
        right = _read_schema_names(node.right, bindings, seen)
        return left - right if isinstance(node.op, ast.Sub) else left | right
    if isinstance(node, ast.IfExp):
        condition = _read_schema_condition(node.test, bindings)
        return (_read_schema_names(node.body, bindings, seen) if condition is True else
                _read_schema_names(node.orelse, bindings, seen) if condition is False else
                _read_schema_names(node.body, bindings, seen) | _read_schema_names(node.orelse, bindings, seen))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('set', 'tuple', 'list') and node.args:
        return _read_schema_names(node.args[0], bindings, seen)
    return set()


def _collect_validator_declarations(source, contract):
    import ast
    from . import native_review
    tree = ast.parse(source)
    bindings = dict(PROPOSAL_FINDINGS=native_review.PROPOSAL_FINDINGS, RESULT_FINDINGS=native_review.RESULT_FINDINGS,
                    RECOVERY_FINDINGS=native_review.RECOVERY_FINDINGS, items=ITEMS, **contract['bindings'])
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, node.value)
    declarations = []
    def visit(node):
        if isinstance(node, ast.If):
            condition = _read_schema_condition(node.test, bindings)
            for child in (node.body if condition is True else node.orelse if condition is False else node.body + node.orelse):
                visit(child)
            return
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)) and node.args:
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if name in ('fields', '_fields', '_closed') and _read_validator_subject(node.args[0]) == contract['subject']:
                names = _read_schema_names(node.args[1], bindings)
                if name != '_closed' and len(node.args) > 2:
                    names |= _read_schema_names(node.args[2], bindings)
                declarations.append(names)
        if isinstance(node, ast.Compare):
            values = [node.left] + node.comparators
            for left, right in zip(values, values[1:]):
                for candidate, fields in ((left, right), (right, left)):
                    removed = set()
                    if isinstance(candidate, ast.BinOp) and isinstance(candidate.op, ast.Sub):
                        removed = _read_schema_names(candidate.right, bindings)
                        candidate = candidate.left
                    if (isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Name)
                            and candidate.func.id == 'set' and candidate.args
                            and _read_validator_subject(candidate.args[0]) == contract['subject']):
                        declarations.append(_read_schema_names(fields, bindings) | removed)
        for child in ast.iter_child_nodes(node):
            visit(child)
    visit(tree)
    if contract['call'] is not None:
        return declarations[contract['call']] if len(declarations) > contract['call'] else set()
    return set().union(*declarations)


def validate_validator_ownership(rule, validator_sources):
    """Guard complete declarations from validators, even without fixture witnesses.

    The caller supplies source strings so this module remains independent of
    files, inspect.getsource, the record store and process-local mutation.
    """
    describe_owners(rule)
    for contract in rule['validator_contracts']:
        accepted = _collect_validator_declarations(validator_sources[contract['validator']], contract)
        if accepted != set(contract['fields']):
            raise ResearchError('review_composition_conflict', VALIDATOR_FIELDS_UNOWNED_MESSAGE,
                                dict(validator=contract['validator'], subject=contract['subject'],
                                     declared=contract['fields'], accepted=sorted(accepted)))
        for name in accepted - set(contract['omit']):
            path = contract['path'] + '/' + _escape_field(name)
            if (not _find_field_owners(rule, path, None)
                    and not any(field['path'].startswith(path + '/') for field in rule['fields'] if not field.get('when'))):
                raise ResearchError('review_composition_conflict', VALIDATOR_FIELDS_UNOWNED_MESSAGE, dict(path=path))
