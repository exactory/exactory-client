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
# grounding array; the caller verifies their scientific validity. Dispositions
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


def _hash(value, label):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        _reject('Use a canonical SHA-256 digest.', location=label)


def _integer(value):
    return type(value) is int and value >= 0


def _json_digest(value):
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


def _domain(kind, value):
    return _json_digest({'domain': PROTOCOL + ':' + kind, 'value': value})


def _array(value, label):
    if not isinstance(value, list):
        _reject('Use an explicit array.', location=label)
    return value


def _check_ids(value, label):
    rows = _array(value, label)
    for row in rows:
        _check_literal_text(row, label)
    if len(set(rows)) != len(rows):
        _reject('Stable identities must be unique.', location=label)
    return rows


def _page_fields(role):
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
        return {'/reason': 'string', '/evidence': 'objects'}, {}
    if role == 'incomplete':
        return {('/' + field): 'objects' for field in
                ('known_conclusions', 'unresolved_scope', 'findings', 'recovery_work')}, {}
    _reject('Use a supported composition role.', role=role)


def _scalar_fields(role):
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
    required, optional = _page_fields(plan['role'])
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
        if not _integer(owner['ordinal']):
            _reject('List members require frozen nonnegative ordinals.', path=owner['path'])
    elif owner['member_key'] is not None or owner['ordinal'] is not None:
        _reject('Only list members have member keys and ordinals.', path=owner['path'])
    span = owner['range']
    if value_type == 'string':
        if (not isinstance(span, list) or len(span) != 2 or not all(_integer(n) for n in span)
                or span[0] > span[1]):
            _reject('Strings require a half-open character range.', path=owner['path'])
    elif span is not None:
        _reject('Only strings have character ranges.', path=owner['path'])


def _check_partition(owners, plan):
    by_path = {}
    seen = set()
    for owner in _array(owners, 'ownership'):
        _check_ownership(owner, plan)
        identity = _json_digest(owner)
        if identity in seen:
            _reject('Duplicate field ownership is invalid.', path=owner['path'])
        seen.add(identity)
        by_path.setdefault(owner['path'], []).append(owner)
    required, optional = _page_fields(plan['role'])
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
    check_fields(plan, PLAN_KEYS, code='review_composition_conflict', path='plan')
    _json_digest(plan)
    if plan['protocol'] != PROTOCOL or plan['schema'] != SCHEMA:
        _reject('Use the supported composition protocol and schema.')
    _page_fields(plan['role'])
    for key in ('epoch_id', 'slot_id'):
        _check_literal_text(plan[key], key)
    if not isinstance(plan['subject'], dict) or not plan['subject']:
        _reject('Bind the exact scientific subject.')
    for key in ('evidence_currency', 'source_root', 'dependency_root',
                'selected_observations_root', 'integration_root'):
        _hash(plan[key], key)
    _check_ids(plan['findings'], 'findings')
    _array(plan['evidence_obligations'], 'evidence_obligations')
    if not _check_ids(plan['required_pages'], 'required_pages'):
        _reject('A paged composition needs required pages.', pending=True)
    _check_partition(plan['ownership'], plan)
    if set(entry['page_id'] for entry in plan['ownership']) != set(plan['required_pages']):
        _reject('Every required page must own scientific fields.')
    scalar_fields = _scalar_fields(plan['role'])
    check_fields(plan['terminal_choices'], scalar_fields, code='review_composition_conflict', path='terminal_choices')
    for path, choices in plan['terminal_choices'].items():
        if not _array(choices, path):
            _reject('Each terminal scalar needs an integrated permissible choice.', pending=True, path=path)
        permitted = scalar_fields[path]
        if permitted is None:
            if len(choices) != 1:
                _reject('A focused composition binds one exact objection ID.')
            _check_literal_text(choices[0], path)
        elif any(_json_digest(choice) not in {digest(value) for value in permitted} for choice in choices):
            _reject('Terminal choice is outside the scientific schema.', path=path)
    for authorization in _array(plan['revision_authorizations'], 'revision_authorizations'):
        check_fields(authorization, REPLACEMENT_KEYS + ('replacement_raw_output_digest',), code='review_composition_conflict', path='revision_authorization')
        _check_replacement(authorization, authorization=True)
    return plan


def _check_replacement(value, *, authorization=False):
    if not authorization:
        check_fields(value, REPLACEMENT_KEYS, code='review_composition_conflict', path='replaces')
    for key in ('page_id', 'attempt_id'):
        _check_literal_text(value[key], key)
    if not _integer(value['revision']):
        _reject('The predecessor revision must be a nonnegative integer.')
    if value['cause'] not in ('observed_reconciliation', 'metadata_correction'):
        _reject('A revision needs an allowed observed cause.')
    for key in ('raw_output_digest', 'authorization_root') + (('replacement_raw_output_digest',) if authorization else ()):
        _hash(value[key], key)


def _check_grounds(grounds, grounding, label):
    if not _array(grounds, label) or any(not isinstance(row, dict) or not row for row in grounds):
        _reject('Scientific content needs explicit grounding references.', location=label)
    if any(row not in grounding for row in grounds):
        _reject('Fragment grounds must be declared in the page grounding.', location=label)


def validate_page(page, plan):
    """Validate one typed page against its externally frozen ownership plan."""
    _validate_plan(plan)
    _validate_page(page, plan)
    return copy.deepcopy(page)


def _validate_page(page, plan, expected_ownership=None):
    check_fields(page, PAGE_KEYS, code='review_composition_conflict', path='page')
    _json_digest(page)
    for key in ('protocol', 'epoch_id', 'role', 'source_root', 'dependency_root'):
        if page[key] != plan[key]:
            _reject('Page scope differs from its frozen plan.', field=key, page_id=page['page_id'])
    _check_literal_text(page['task_id'], 'task_id')
    if page['page_id'] not in plan['required_pages'] or not _integer(page['revision']):
        _reject('Bind a required page and an exact nonnegative revision.')
    expected = ([entry for entry in plan['ownership'] if entry['page_id'] == page['page_id']]
                if expected_ownership is None else expected_ownership)
    actual = _array(page['ownership'], 'page.ownership')
    if sorted(map(_json_digest, actual)) != sorted(map(_json_digest, expected)):
        _reject('Page ownership differs from the frozen ownership plan.', page_id=page['page_id'])
    for owner in actual:
        _check_ownership(owner, plan)
        if owner['page_id'] != page['page_id']:
            _reject('A page may return only its own fields.')
    if len({_json_digest(owner) for owner in actual}) != len(actual):
        _reject('A page cannot duplicate field ownership.')
    grounding = _array(page['grounding'], 'grounding')
    _check_grounds(grounding, grounding, 'grounding')
    fragments = _array(page['fragments'], 'fragments')
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
    if sorted(map(_json_digest, actual_owners)) != sorted(map(_json_digest, expected)):
        _reject('Every owned fragment must be returned exactly once.', pending=True, page_id=page['page_id'])
    findings = _check_ids(page['findings'], 'page.findings')
    if any(identifier not in plan['findings'] for identifier in findings):
        _reject('Every observed finding must enter the frozen finding inventory.')
    dispositions = _array(page['dispositions'], 'dispositions')
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
    _hash(observation['raw_output_digest'], 'raw_output_digest')
    if _json_digest(observation['output']) != observation['raw_output_digest']:
        _reject('Observation output differs from its exact raw digest.', attempt_id=observation['attempt_id'])
    return observation['output']


def _metadata_projection(page):
    result = copy.deepcopy({key: page[key] for key in ('fragments', 'grounding', 'findings', 'dispositions')})
    for fragment in result['fragments']:
        if fragment['path'] != '/reassessment':
            continue
        content = fragment['content']
        for finding in _array(content.get('findings'), 'reassessment.findings'):
            if isinstance(finding, dict) and finding.get('disposition') in ('confirmed', 'revised'):
                finding['disposition'] = 'confirmed'
                finding['reason'] = None
    return result


def _validate_metadata_revision(previous, page):
    if _metadata_projection(page) != _metadata_projection(previous):
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
    for observation in _array(observations, 'observations'):
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
        reference = _domain('page-tree', node)
        nodes[reference] = node
        level.append(reference)
    depth = 1
    while len(level) > 1:
        if depth >= MAX_TREE_DEPTH:
            _reject('The descriptor tree exceeds its frozen depth bound.')
        next_level = []
        for offset in range(0, len(level), TREE_FANOUT):
            node = {'kind': 'branch', 'children': level[offset:offset + TREE_FANOUT]}
            reference = _domain('page-tree', node)
            nodes[reference] = node
            next_level.append(reference)
        level, depth = next_level, depth + 1
    return level[0], nodes, depth


def _build_root(plan, descriptors, observation_count):
    page_tree, nodes, depth = _build_tree(descriptors)
    header = {key: copy.deepcopy(plan[key]) for key in
              ('epoch_id', 'role', 'subject', 'evidence_currency', 'slot_id', 'schema',
               'source_root', 'dependency_root', 'selected_observations_root', 'integration_root')}
    header.update(plan_digest=_domain('plan', plan), ownership_plan_root=_domain('ownership', plan['ownership']),
                  findings_root=_domain('findings', plan['findings']),
                  evidence_obligations_root=_domain('evidence-obligations', plan['evidence_obligations']),
                  counts={'pages': len(descriptors), 'observations': observation_count,
                          'fragments': len(plan['ownership']), 'findings': len(plan['findings']), 'tree_depth': depth})
    root = {'version': PROTOCOL, 'header': header, 'page_tree': page_tree, 'nodes': nodes}
    root['digest'] = _domain('root', {'version': PROTOCOL, 'header': header, 'page_tree': page_tree})
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
    if not _integer(page_characters) or not page_characters:
        _reject('Incomplete pages need a positive content allowance.')
    values = dict(known_conclusions=_array(known_conclusions, 'known_conclusions'),
                  findings=_array(findings, 'findings'), **outcome)
    finding_ids = _check_ids([row['id'] for row in findings], 'findings')
    plan = dict(protocol=PROTOCOL, schema=SCHEMA, epoch_id=epoch['id'], role='incomplete', slot_id=epoch['slot_id'],
                subject=copy.deepcopy(epoch['subject']), evidence_currency=(epoch['evidence_currency'] if isinstance(epoch['evidence_currency'], str) else digest(epoch['evidence_currency'])),
                source_root=source_root, dependency_root=dependency_root,
                selected_observations_root=selected_observations_root, integration_root=integration_root,
                findings=finding_ids, evidence_obligations=[], required_pages=[], ownership=[],
                revision_authorizations=[], terminal_choices={'/origin': ['reviewer']})
    used = page_characters
    for field in _page_fields('incomplete')[0]:
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
    _json_digest(root)
    if root['version'] != PROTOCOL or not isinstance(root['header'], dict) or not isinstance(root['nodes'], dict):
        _reject('Use the versioned composition root and node table.')
    _hash(root['digest'], 'root.digest')
    expected = _domain('root', {key: root[key] for key in ('version', 'header', 'page_tree')})
    if root['digest'] != expected:
        _reject('Composition root header or membership was altered.')
    visited = set()
    stack = [(root['page_tree'], 1)]
    while stack:
        reference, depth = stack.pop()
        _hash(reference, 'page_tree.reference')
        if reference in visited or depth > MAX_TREE_DEPTH:
            _reject('Composition tree must be acyclic and within its depth bound.')
        visited.add(reference)
        node = root['nodes'].get(reference)
        if not isinstance(node, dict):
            _reject('A referenced page-tree node is missing.')
        if node.get('kind') == 'leaf':
            check_fields(node, ('kind', 'pages'), code='review_composition_conflict', path='leaf')
            rows = _array(node['pages'], 'leaf.pages')
        elif node.get('kind') == 'branch':
            check_fields(node, ('kind', 'children'), code='review_composition_conflict', path='branch')
            rows = _array(node['children'], 'branch.children')
            stack.extend((child, depth + 1) for child in rows)
        else:
            _reject('Use a typed leaf or branch node.')
        if not rows or len(rows) > TREE_FANOUT or _domain('page-tree', node) != reference:
            _reject('Page-tree node content or fanout differs from its digest.')
    if visited != set(root['nodes']):
        _reject('The root node table must contain exactly the reachable tree.')


def validate_terminal_choices(plan, terminal, root):
    """Check exact root authorization and integration-approved scalar choices."""
    _validate_plan(plan)
    _validate_root(root)
    if root['header'].get('plan_digest') != _domain('plan', plan):
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
        if _json_digest(value) not in {_json_digest(item) for item in plan['terminal_choices'][path]}:
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
    required, optional = _page_fields(plan['role'])
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
    _json_digest(frozen_selection)
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
        if _json_digest(output) != selection.get('output_digest'):
            _reject('Ordinary output must preserve its existing effective digest.')
        _check_literal_text(result['terminal_attempt_id'], 'terminal_attempt_id')
        _hash(result['terminal_raw_output_digest'], 'terminal_raw_output_digest')
    else:
        missing = [key for key in ('plan', 'observations', 'root', 'terminal', 'completion_root') if key not in selection]
        if missing:
            _reject('A paged selection requires complete frozen composition bindings.', pending=True, fields=missing)
        terminal = _read_observation(selection['terminal'])
        if isinstance(terminal, dict) and 'terminal' in terminal:
            terminal = terminal['terminal']
        _hash(selection['completion_root'], 'completion_root')
        output = assemble_result(selection['plan'], selection['observations'], selection['root'], terminal)
        if selection['plan']['role'] == 'incomplete':
            _reject('An incomplete composition must be recorded as a distinct incomplete outcome.')
        result.update(composition_version=PROTOCOL, composition_root=selection['root']['digest'],
                      selected_observations_root=selection['plan']['selected_observations_root'],
                      terminal_attempt_id=selection['terminal']['attempt_id'],
                      terminal_raw_output_digest=selection['terminal']['raw_output_digest'])
    result.update(effective_output=copy.deepcopy(output), effective_output_digest=_json_digest(output))
    return copy.deepcopy(result)
