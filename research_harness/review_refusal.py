"""Authenticated output-contract refusals and bounded recovery audit records."""

import json
from contextlib import contextmanager
from functools import partial, wraps

from .artifacts import ArtifactStore, _CheckEffectRequired, validate_reference
from .errors import ResearchError, describe_error_at
from .evidence import digest
from .finishing import retry_finishing_write
from .fulltext import GENERIC_BINARY_MEDIA_TYPES, parse_base_media_type
from .operations import immutable_record, prepared_task_mutation

PROTOCOL = 'exactory-review-output-refusal-v1'
SOURCE_LINK_ACCEPTED_FORMS_NOTICE = (
    'Cite the delivered bytes by their artifact descriptor, or use a source link into a full text that this chain delivered.'
)
# details.notice of the check evidence.shape for an array of the three forms that is checked without review_binding.
EVIDENCE_FORMS_NOTICE = (
    'When this check refuses entries of the array at path, refused_indexes gives their positions, counted from 0, '
    'at most 100 of them, and refused_count gives their number. An evidence entry is one reference object in one '
    'of three forms, and the outermost members of the entry decide its form. An artifact descriptor: the entry '
    'itself is {"path":"<path>","sha256":"<sha256>","size":0,"media_type":"<media type>"}, with exactly these four '
    'members copied from one descriptor of the packet. A descriptor under a member name, such as '
    '{"artifact":{"path":"<path>","sha256":"<sha256>","size":0,"media_type":"<media type>"}}, is refused. '
    'A source link: {"kind":"source","link":{"version_id":"<version id>","source_id":"<source id>",'
    '"artifact":{"path":"<path>","sha256":"<sha256>","size":0,"media_type":"<media type>"},'
    '"locator":{"kind":"<as delivered>"}}}. The member citation of a passage that the packet delivers, for example '
    'in an entry of prior_context, is the complete value of link. The members version_id, source_id, artifact and '
    'locator written as the outermost members of the entry are refused. A source link is accepted only when its '
    'artifact is the original or the extracted text of a full-text capture that this chain delivered to you. Cite '
    'a text that was delivered at another depth, for example an abstract, by its artifact descriptor. '
    'A record: {"kind":"record","record_kind":"<record kind>","id":"<record id>","digest":"<digest>"}, with the '
    'three values copied from the place where the packet names that record. An entry that is only a digest, only '
    'a path or free text is refused. Every evidence array of the answer outside support follows these forms, so '
    'correct each of them in the answer that you return. Where the instructions of this turn give handles for '
    'citations, write each citation as a handle in the form that those instructions state.')
# The same notice for a call with review_binding, where a record reference may omit its digest.
REVIEW_BOUND_EVIDENCE_FORMS_NOTICE = (
    'When this check refuses entries of the array at path, refused_indexes gives their positions, counted from 0, '
    'at most 100 of them, and refused_count gives their number. An evidence entry is one reference object in one '
    'of three forms, and the outermost members of the entry decide its form. An artifact descriptor: the entry '
    'itself is {"path":"<path>","sha256":"<sha256>","size":0,"media_type":"<media type>"}, with exactly these four '
    'members copied from one descriptor of the packet. A descriptor under a member name, such as '
    '{"artifact":{"path":"<path>","sha256":"<sha256>","size":0,"media_type":"<media type>"}}, is refused. '
    'A source link: {"kind":"source","link":{"version_id":"<version id>","source_id":"<source id>",'
    '"artifact":{"path":"<path>","sha256":"<sha256>","size":0,"media_type":"<media type>"},'
    '"locator":{"kind":"<as delivered>"}}}. The member citation of a passage that the packet delivers, for example '
    'in an entry of prior_context, is the complete value of link. The members version_id, source_id, artifact and '
    'locator written as the outermost members of the entry are refused. A source link is accepted only when its '
    'artifact is the original or the extracted text of a full-text capture that this chain delivered to you. Cite '
    'a text that was delivered at another depth, for example an abstract, by its artifact descriptor. '
    'A record: {"kind":"record","record_kind":"<record kind>","id":"<record id>"}, with the two values copied from '
    'the place where the packet names that record; where the packet delivers a cite object with the record, copy '
    'that object unchanged. The member digest is optional in this form: add it only where the instructions of this '
    'turn ask for it, with the digest that the packet gives for that record. An entry that is only a digest, only '
    'a path or free text is refused. Every evidence array of the answer outside support follows these forms, so '
    'correct each of them in the answer that you return. Where the instructions of this turn give handles for '
    'citations, write each citation as a handle in the form that those instructions state.')
# The positions that a refusal of evidence.shape lists; refused_count gives the number of every refused entry.
REFUSED_INDEX_LIMIT = 100
# The messages of the record of a refused reviewer output when the store refused that record beyond the wait: after a
# run, and after a recording command that was refused.
OUTPUT_REFUSAL_AFTER_RUN_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. No refusal of this reviewer '
    'output was recorded. The run and its output are recorded. details.refusal names the check that the output fails, '
    'so the assignment has no result. When the busy store did not let the check of the output run, details.refusal is '
    'absent. Until the refusal of a failing output is recorded, the assignment takes no second run and no replacement '
    'that carries the refusal. Run review-run again for the same attempt, with the request ID in details.request_id. '
    'Its payload holds the attempt ID in details.attempt_id as id, and the assignment ID in details.assignment_id as '
    'assignment_id. It returns the recorded run and calls no reviewer. It then checks the output and, when the output '
    'fails, records the refusal. When review-series sent the run, it runs that request again by itself.')
OUTPUT_REFUSAL_AFTER_RECORDING_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. No refusal of this reviewer '
    'output was recorded. The recording command was refused, and details.recording_refusal holds its refusal. '
    'details.refusal names the check that the output fails. When the busy store did not let the check of the output '
    'run, details.refusal is absent. Until the refusal of a failing output is recorded, the assignment takes no '
    'second run and no replacement that carries the refusal. Run the same recording command again with the current '
    'revision. It is refused as before. It then checks the output and, when the output fails, records the refusal of '
    'the output.')

# Only output values, frozen packet values, and measurements computed from them belong here.
# In particular, an existing review's scientific text is never a notice detail.
_SHAPE_NOTICE_FIELDS = ('expected_type', 'received_type', 'missing_fields', 'unexpected_fields')
_TEXT_NOTICE_FIELDS = ('characters', 'minimum_characters', 'maximum_characters', 'normalized_characters')
_DELIVERY_NOTICE_FIELDS = ('record_kind', 'id', 'digest', 'sha256', 'locator')
CONTRACT_CHECKS = {
    'evidence.shape': {'notice_fields': _SHAPE_NOTICE_FIELDS + ('refused_indexes', 'refused_count', 'notice')},
    'evidence.fulltext': {'notice_fields': ('version_id', 'source_id', 'artifact', 'delivered_depth', 'notice')},
    'evidence.locator': {'notice_fields': _SHAPE_NOTICE_FIELDS + ('part_relative_span',)},
    'review.shape': {'notice_fields': _SHAPE_NOTICE_FIELDS},
    'review.value': {'notice_fields': _SHAPE_NOTICE_FIELDS + _TEXT_NOTICE_FIELDS +
                     ('required', 'bar_ids', 'candidate_id', 'candidate_ids', 'items', 'minimum_items')},
    'review.objections': {'notice_fields': _SHAPE_NOTICE_FIELDS + ('objection_id', 'candidate_ids')},
    'review.reassessment': {'notice_fields': _SHAPE_NOTICE_FIELDS + _DELIVERY_NOTICE_FIELDS},
    'review.support': {'notice_fields': _SHAPE_NOTICE_FIELDS},
    'review.source_requests': {'notice_fields': _SHAPE_NOTICE_FIELDS + _TEXT_NOTICE_FIELDS +
                              ('request_id', 'page', 'minimum_page', 'maximum_page', 'page_size', 'total')},
    'review.evidence_delivery': {'notice_fields': _DELIVERY_NOTICE_FIELDS},
    'review.staged_final': {'notice_fields': _SHAPE_NOTICE_FIELDS},
    'review.staged_page': {'notice_fields': _SHAPE_NOTICE_FIELDS},
    'review.staged_task': {'notice_fields': _SHAPE_NOTICE_FIELDS},
    'adjudication.shape': {'notice_fields': _SHAPE_NOTICE_FIELDS},
    'adjudication.reassessment': {'notice_fields': _SHAPE_NOTICE_FIELDS + _DELIVERY_NOTICE_FIELDS},
    'adjudication.evidence_delivery': {'notice_fields': _DELIVERY_NOTICE_FIELDS},
    'verification.parts': {'notice_fields': _SHAPE_NOTICE_FIELDS},
    'native.output': {'notice_fields': _SHAPE_NOTICE_FIELDS + _TEXT_NOTICE_FIELDS + ('request_id',)},
    'openness.finding': {'notice_fields': _SHAPE_NOTICE_FIELDS + ('rule', 'forecast')},
}


def require_contract_check(name):
    """Reject an unregistered attribution before it can produce a deliverable refusal."""
    CONTRACT_CHECKS[name]
    return name


@contextmanager
def contract_check(name):
    """Identify the pure validator, rather than treating a shared error code as eligibility."""
    require_contract_check(name)
    try:
        yield
    except ResearchError as error:
        if not getattr(error, 'contract_external', False) and not hasattr(error, 'contract_check'):
            error.contract_check = name
        raise


def _frozen_pdf_capture(link, packets):
    """Find a PDF page map only where a frozen source delivery binds it to the original."""
    if parse_base_media_type(link['artifact']['media_type']) in GENERIC_BINARY_MEDIA_TYPES:
        # Native format-inference qualification is not present in this projection.
        return None
    for packet in packets:
        for delivery in packet.get('source_deliveries', []):
            if delivery.get('request', {}).get('version_id') != link['version_id']:
                continue
            for provenance in delivery.get('source_provenance', []):
                if (provenance['source_id'] != link['source_id']
                        or provenance['original_descriptor']['sha256'] != link['artifact']['sha256']):
                    continue
                # Acquisition stores an extraction as text/plain with its charset parameter.
                texts = [artifact for artifact in delivery['evidence']
                         if artifact['sha256'] in provenance['delivered_sha256']
                         and parse_base_media_type(artifact['media_type']) == 'text/plain']
                if len(texts) == 1:
                    return {'original': link['artifact'], 'text': texts[0]}
    return None


def _find_delivered_part(link, packets):
    """Use the recorded whole-text range only for this exact delivered part and source."""
    from .review_supplied_evidence import list_delivered_parts
    start, end = link['locator'].get('start'), link['locator'].get('end')
    if type(start) is not int or type(end) is not int:
        return None
    for packet in packets:
        for delivery in list_delivered_parts(packet):
            if (delivery['version_id'] == link['version_id'] and delivery['excerpt'] == link['artifact']
                    and delivery['origin']['source_id'] == link['source_id']
                    and delivery['part']['start'] <= start < end <= delivery['part']['end']):
                return delivery['part']
    return None


def validate_evidence_contract(records, artifacts, values, *, required=True, packets=(), review_binding=False,
                               positions=None, forms_notice=True):
    """Shared evidence grammar and locators over exact bytes; no live source/reading lookup.

    The shape check reads every entry and raises the error of the first refused one. Its details give the positions
    of the refused entries in the array of the answer, at most REFUSED_INDEX_LIMIT of them, and the number of all
    refused entries; positions gives the positions when values is a selection of that array. forms_notice is false
    for an array of another grammar, whose refusal carries no text of the three forms."""
    from . import strategy
    from .source_links import read_locator, validate_link_fields
    notice = {}
    if forms_notice:
        notice['notice'] = REVIEW_BOUND_EVIDENCE_FORMS_NOTICE if review_binding else EVIDENCE_FORMS_NOTICE
    with contract_check('evidence.shape'):
        try:
            references = strategy.items(values, 'Evidence', nonempty=required)
        except ResearchError as error:
            if notice:
                error.details = dict(error.details or {}, **notice)
            raise
        first, refused = None, []
        for index, reference in enumerate(references):
            try:
                strategy.evidence_fields(reference, review_binding=review_binding)
                if {'path', 'sha256', 'size', 'media_type'} <= reference.keys():
                    validate_reference(reference)
                elif reference.get('kind') == 'source':
                    validate_link_fields(reference['link'], locator=False)
                    validate_reference(reference['link']['artifact'])
            except ResearchError as error:
                if first is None:
                    first = error
                refused.append(index)
            except (ValueError, TypeError, KeyError):
                # Malformed input that is no output finding propagates as before. After a refused entry it is one
                # more refused entry, so the refusal of the first refused entry stays.
                if first is None:
                    raise
                refused.append(index)
        if first is not None:
            indexes = refused if positions is None else [positions[index] for index in refused]
            first.details = dict(first.details or {}, refused_indexes=indexes[:REFUSED_INDEX_LIMIT],
                                 refused_count=len(indexes), **notice)
            raise first
    for reference in references:
        if reference.get('kind') != 'source':
            continue
        link = reference['link']
        media = parse_base_media_type(link['artifact']['media_type'])
        # Missing bytes are evidence-availability failures, before any locator check.
        try:
            artifacts.read(link['artifact'])
            kind = link['locator'].get('kind') if isinstance(link['locator'], dict) else None
            capture = _frozen_pdf_capture(link, packets) if kind == 'pdf' else None
            if (kind == 'pdf' and capture is None
                    and (media == 'application/pdf' or media in GENERIC_BINARY_MEDIA_TYPES)):
                # Page-map-dependent checks cannot classify an output without a delivered page map.
                continue
            if capture is not None:
                artifacts.read(capture['text'])
        except ResearchError as error:
            error.contract_external = True
            raise
        try:
            validate_link_fields(link)
            part = _find_delivered_part(link, packets) if kind == 'span' else None
            read_locator(artifacts, link['artifact'], link['locator'], capture=capture, part=part)
        except ResearchError as error:
            if error.code == 'invalid_locator':
                error.contract_check = require_contract_check('evidence.locator')
            else:
                error.contract_external = True
            raise


def evidence_arrays(output, *, with_paths=False):
    """Traverse completed scientific fields, never a raw task or page envelope."""
    if 'objection_id' in output and 'value' not in output:
        arrays = [('/evidence', output['evidence'])]
        arrays.extend(('/reassessment/findings/' + str(index) + '/evidence', row['evidence'])
                      for index, row in enumerate(output.get('reassessment', {}).get('findings', [])))
        return arrays if with_paths else [values for path, values in arrays]
    value = output['value']
    arrays = [('/value/evidence', value['evidence'])]
    for path, rows in (('/value/assurances', value['assurances']), ('/value/work_items', value['work_items']),
                       ('/value/objection_findings', value['objection_findings']), ('/objections', output['objections']),
                       ('/reassessment/findings', output.get('reassessment', {}).get('findings', []))):
        arrays.extend((path + '/' + str(index) + '/evidence', row['evidence']) for index, row in enumerate(rows))
    return arrays if with_paths else [evidence for path, evidence in arrays]


def validate_source_links_contract(records, output, packets):
    """Check capture membership for every link before the recording reads any prerequisite.

    Capture identities are immutable; current readings and bundle selection do not
    participate in this output refusal. Delivery depth comes from the frozen chain.
    """
    from .reading import validate_fulltext_link
    from .review_delivered_evidence import resolve_part_source_link
    for path, values in evidence_arrays(output, with_paths=True):
        for index, reference in enumerate(values):
            if reference.get('kind') != 'source':
                continue
            link = reference['link']
            try:
                validate_fulltext_link(records, resolve_part_source_link(link, packets))
            except ResearchError as error:
                if error.code in ('unknown_work', 'missing_version'):
                    # Catalog prerequisites cannot hide another pure contract
                    # failure. The recording's source check still requires them.
                    continue
                if error.code != 'source_link_not_fulltext':
                    raise
                depths = [delivery['request'].get('depth') for packet in packets
                          for delivery in packet.get('source_deliveries', [])
                          if delivery.get('request', {}).get('version_id') == link['version_id']
                          and link['artifact'] in delivery.get('evidence', [])]
                if not depths:
                    depths = [mapping['inspection']['depth'] for packet in packets
                              for key in ('prior_context', 'version_source_mappings', 'supplied_source_mappings')
                              for mapping in packet.get(key, [])
                              if mapping.get('version_id') == link['version_id'] and 'inspection' in mapping
                              and mapping.get('origin', {}).get('artifact_descriptor', {}).get('sha256')
                              == link['artifact']['sha256']]
                error.details.update(path=path + '/' + str(index),
                    delivered_depth=depths[0] if depths else None, notice=SOURCE_LINK_ACCEPTED_FORMS_NOTICE)
                error.contract_check = require_contract_check('evidence.fulltext')
                raise


def _validate_staged_objections(records, artifacts, values, evidence_validator):
    """Check each complete ordinary objection before reading its evidence or comparing identities."""
    from . import strategy
    from .operations import fields, text
    with contract_check('review.objections'), describe_error_at('/objections'):
        seen = set()
        for index, objection in enumerate(strategy.items(values, 'Material objections')):
            path = '/objections/' + str(index)
            fields(objection, ('id', 'claim', 'reason', 'evidence', 'resolution_condition'),
                   code='invalid_value_review', path=path)
            for key in ('id', 'claim', 'reason', 'resolution_condition'):
                with describe_error_at(path + '/' + key):
                    text(objection[key], 'Material objection ' + key)
            with describe_error_at(path + '/evidence'):
                evidence_validator(records, artifacts, objection['evidence'], review_binding=True)
            if objection['id'] in seen:
                raise ResearchError('invalid_value_review', 'Material objection identities must be unique',
                                    {'path': path + '/id'})
            seen.add(objection['id'])


def _validate_staged_final(records, artifacts, assignment, packet, epoch, output):
    """Check ordinary scientific grammar without selecting a staged effective result or its delivery graph."""
    from . import review_context_repair, review_excerpts, review_supplied_evidence, strategy
    from .operations import strings, text
    frame = epoch.get('manifest', {}).get('frame', {})
    evidence = partial(validate_evidence_contract, packets=(packet, frame))
    with contract_check('review.reassessment'):
        review_context_repair.validate_reassessment(records, artifacts, assignment, output, reading_checks=False,
                                                    packet=frame, evidence_validator=evidence)
    if assignment['role'] == 'adjudicator':
        from .review_composition import list_scalar_fields
        with contract_check('adjudication.shape'):
            for path, choices in list_scalar_fields('adjudicator').items():
                value = output[path[1:]]
                if choices is not None and not any(type(value) is type(choice) and value == choice for choice in choices):
                    raise ResearchError('invalid_review_adjudication', 'Use a focused adjudication scalar from its contract.',
                                        {'path': path})
            text(output['objection_id'], 'Adjudicated objection ID')
            text(output['reason'], 'Adjudication reason')
            expected = epoch.get('focused_scope', {}).get('objection_id')
            if expected is None:
                expected = frame.get('objection', {}).get('id')
            if expected is not None and output['objection_id'] != expected:
                raise ResearchError('review_adjudication_mismatch', 'Disposition must concern the assigned objection')
            with describe_error_at('/evidence'):
                evidence(records, artifacts, output['evidence'])
        validate_source_links_contract(records, output, (packet, frame))
        return
    from .research_decisions import _validate_value
    from .problem_rubric import PROBLEM_CHECKS
    with contract_check('review.shape'):
        strategy.choice(output['stage'], (assignment['role'],), 'Review phase', 'invalid_value_review')
        if 'excerpts' in output:
            review_excerpts.validate_excerpts(output['excerpts'], 'invalid_value_review')
    with contract_check('review.value'):
        _validate_value(records, artifacts, output['value'], output['stage'], {'payload': {}},
                        expected_checks=PROBLEM_CHECKS, problem={},
                        problem_objections=review_supplied_evidence.list_delivered_objections(frame),
                        evidence_validator=evidence)
        strings(output['limitations'], 'Independent assessment limitations', nonempty=True)
    _validate_staged_objections(records, artifacts, output['objections'], evidence)
    validate_source_links_contract(records, output, (packet, frame))
    with contract_check('review.support'):
        if output['support'] is not None:
            raise ResearchError('invalid_value_review', 'A problem review supplies problem assurances, not native proof acceptance')


def _project_staged_final(role, output):
    """Recognize partial scientific objects by their role fields before requiring the complete ordinary shape."""
    from .operations import fields
    from .review_composition import list_page_fields, list_scalar_fields
    from .review_staged import project_ordinary_final
    required, optional = list_page_fields(role)
    required_fields = {path.split('/')[1] for path in list(required) + list(list_scalar_fields(role))}
    scientific_fields = required_fields | {path.split('/')[1] for path in optional}
    if not scientific_fields.intersection(output):
        return None
    with contract_check('review.staged_final'):
        fields(output, required_fields, optional=None, code='invalid_review_staged', path='')
        return project_ordinary_final(output)[0]


def _validate_staged_pages(records, assignment, epoch, task, output):
    """Separate unavailable frozen page scope from output that changes its assigned task or ownership."""
    from .review_staged_protocol import validate_answer_pages, validate_composition_task
    plan = records['review_composition_plan'][assignment['composition_plan_id']]
    validate_composition_task(epoch, task, plan)
    with contract_check('review.staged_page'):
        validate_answer_pages(task, output, plan)


def classify_observed_output(records, artifacts, attempt_id):
    """Return the first pure failure of an authenticated attempt's own saved output."""
    from . import review_parts, review_protocol as protocol
    from .review_protocol import authenticate_attempt, validate_adjudication_contract
    from .research_decisions import validate_review_contract
    attempt = records.get('review_attempt', {}).get(attempt_id)
    if attempt is None or attempt.get('status') != 'completed':
        return None
    assignment = records.get('review_assignment', {}).get(attempt['assignment_id'])
    if assignment is None or not authenticate_attempt(records, artifacts, assignment, attempt):
        return None
    try:
        output = protocol.read_raw_observation(records, artifacts, attempt_id)['raw_output']
        packet = json.loads(artifacts.read(assignment['packet']))
        from .review_handles import expand_handles, is_material_output, validate_material
        output = expand_handles(packet, output, artifacts=artifacts, assignment=assignment,
                                raw='handle_expansion' not in attempt)
        if is_material_output(packet, output):
            validate_material(packet, output, artifacts=artifacts, assignment=assignment)
            return None
        staged = packet.get('staged_review')
        if staged is not None:
            from .review_staged import validate_task_output
            binding = assignment.get('staged_review', {})
            task = assignment.get('staged_task', staged.get('task'))
            epoch = records.get('review_epoch', {}).get(binding.get('epoch_id'), staged.get('epoch', {}))
            with contract_check('review.staged_task'):
                validate_task_output(task, output, manifest=epoch.get('manifest'))
            if task['type'] == 'answer':
                _validate_staged_pages(records, assignment, epoch, task, output)
            if task['type'] == 'final':
                scientific = _project_staged_final(assignment['role'], output)
                if scientific is not None:
                    _validate_staged_final(records, artifacts, assignment, packet, epoch, scientific)
        elif (review_parts.find_kind(packet) is not None
              and (review_parts.find_kind(packet) != 'final'
                   or isinstance(output, dict) and output.get('kind') == 'requests')):
            with contract_check('review.shape'):
                review_parts.validate_turn_output(records, artifacts, assignment, packet, output, attempt=attempt)
        elif assignment['role'] in ('bar', 'slate', 'result'):
            validate_review_contract(records, artifacts, assignment, output, attempt=attempt)
        elif assignment['role'] == 'adjudicator':
            packet = json.loads(artifacts.read(assignment['packet']))
            validate_adjudication_contract(records, artifacts, assignment, packet, output, attempt=attempt)
        elif assignment['role'] == 'verification' and 'review_parts' in assignment:
            from .review_parts_study import validate_verification_final
            with contract_check('verification.parts'):
                validate_verification_final(records, artifacts, assignment, output, attempt=attempt)
        elif assignment['role'] == 'native_math':
            from .native_review import uses_finding_contract, validate_finding, validate_output
            from .openness_reading import (INVALID_RESPONSE, PASSAGE_PROGRESS_ERR_MSG, find_stage,
                                           has_deliverable_passage, validate_turn_output)
            with contract_check('native.output'):
                packet = json.loads(artifacts.read(assignment['packet']))
                if find_stage(packet) is None:
                    if uses_finding_contract(assignment):
                        validate_finding(packet, output)
                    else:
                        validate_output(packet['native'], output)
                else:
                    validate_turn_output(packet, output, artifacts=artifacts, assignment=assignment)
            # Reading the bound bodies stays outside the contract check: an unavailable body is not an output failure.
            if not has_deliverable_passage(records, artifacts, packet, output, assignment=assignment, attempt=attempt):
                with contract_check('native.output'):
                    raise ResearchError(INVALID_RESPONSE, PASSAGE_PROGRESS_ERR_MSG, {'path': '/passage_requests'})
    except ResearchError as error:
        check = getattr(error, 'contract_check', None)
        if check is not None:
            return {'code': error.code, 'details': dict(error.details or {}, message=error.message), 'check': check}
    except (ValueError, TypeError, KeyError):
        # Unavailable/malformed frozen inputs are not an output-contract finding.
        return None
    return None


def refused_attempts(records, artifacts, completed):
    """Recheck certificates against each exact observation, never effective assignment state."""
    result = []
    for attempt in completed:
        if attempt['id'] in records.get('review_output_refusal', {}):
            failure = classify_observed_output(records, artifacts, attempt['id'])
            if failure is not None:
                result.append({'attempt_id': attempt['id'], 'code': failure['code']})
    return result


def record_refusal_on_error(record_function):
    """Keep command tables unchanged and raise the recording call's original exception, or store_busy when the store
    stays busy beyond the wait for the refusal record of the output."""
    @wraps(record_function)
    def wrapped(store, payload, *, expected_revision, request_id):
        try:
            return record_function(store, payload, expected_revision=expected_revision, request_id=request_id)
        except ResearchError as error:
            if error.code in ('stale_revision', 'store_busy'):
                # The store refused the recording before it read the output, or after the output passed its checks.
                raise
            if error.code == 'source_link_not_fulltext':
                from .review_reading import collect_missing_readings
                try:
                    snapshot = store.snapshot()
                    evaluation = store.build_artifact_store()
                    error.details['readings'] = collect_missing_readings(snapshot['records'], evaluation, payload)
                except (_CheckEffectRequired, ResearchError):
                    # Optional store-state details cannot replace a pure refusal
                    # or prevent its independent certificate attempt below.
                    pass
            assignment_id = payload.get('assignment_id') if isinstance(payload, dict) else None
            try:
                _record_refusal(store, assignment_id, None, request_id=request_id + ':refusal',
                                busy_msg=OUTPUT_REFUSAL_AFTER_RECORDING_STORE_BUSY_MSG,
                                kept=dict(recording_refusal=error.as_dict()))
            except ResearchError as unrecorded:
                # A store that stayed busy beyond the wait is reported, because the refusal of the output is what
                # admits the next run or replacement. Any other refusal of that record leaves the original refusal.
                if unrecorded.code == 'store_busy':
                    raise unrecorded from error
            except (_CheckEffectRequired, ValueError, TypeError, KeyError):
                pass
            raise
    return wrapped


def record_native_refusal(store, assignment_id, attempt_id, *, request_id):
    """Record the refusal of the output of a native review run when the run completes: the native controller records
    the review in another program, so the run is where the harness refuses it and lets the assignment run again.
    Return the refusal record of that output, or None when the output passes its checks or its provenance is
    unavailable. A store that stays busy beyond the wait refuses the run command with store_busy, and the same request
    records the refusal."""
    return _record_refusal(store, assignment_id, attempt_id, request_id=request_id + ':refusal',
                           busy_msg=OUTPUT_REFUSAL_AFTER_RUN_STORE_BUSY_MSG,
                           kept=dict(request_id=request_id, assignment_id=assignment_id, attempt_id=attempt_id))


def _record_refusal(store, assignment_id, attempt_id, *, request_id, busy_msg, kept):
    """Record the refusal of an output of an assignment that fails a contract check, and return the refusal record that
    the store then holds for that output, or None. The output is that of attempt_id, or the selected output of the
    assignment when attempt_id is None. The record commits against the records that its check read and waits for a
    busy store as the finish of a review call does; kept gains what the refusal names, for a refusal after the wait."""
    from .evaluation import Evaluation
    from .review_protocol import assignment_state
    if not callable(getattr(store, '_read_scoped_preparation', None)):
        # A check prepares no second mutation.
        return None

    def write():
        preparation = store._read_scoped_preparation()
        records = preparation.records
        if not isinstance(assignment_id, str) or assignment_id not in records.get('review_assignment', {}):
            return None
        artifacts = Evaluation.of(records, store.build_artifact_store())
        try:
            output_id = assignment_state(records, artifacts, assignment_id)['attempt_id'] if attempt_id is None else attempt_id
            if output_id is None:
                return None
            existing = records.get('review_output_refusal', {}).get(output_id)
            if existing is not None:
                return existing
            failure = classify_observed_output(records, artifacts, output_id)
            if failure is None:
                return None
            attempt = records['review_attempt'][output_id]
            value = dict(id=output_id, assignment_id=assignment_id, attempt_id=output_id,
                         output_digest=digest(json.loads(artifacts.read(attempt['output']))),
                         request_digest=attempt['request_digest'], protocol=PROTOCOL, **failure)
        except (_CheckEffectRequired, ResearchError, ValueError, TypeError, KeyError):
            # Unavailable provenance is no finding about the output.
            return None
        kept.update(assignment_id=assignment_id, attempt_id=output_id,
                    refusal={key: value[key] for key in ('code', 'check', 'details')})

        def prepare(current, submitted):
            record = dict(submitted, recorded_revision=None)
            return [immutable_record(current, 'review_output_refusal', output_id, record)], record

        def stamp_commit_revision(changes, result, revision):
            record = dict(result, recorded_revision=revision)
            return [('review_output_refusal', output_id, record)], record
        return prepared_task_mutation(store, 'review.refusal', value, prepare, request_id=request_id,
                                      finalize=stamp_commit_revision, _snapshot=preparation)['result']
    return retry_finishing_write(write, busy_msg, kept=kept)
