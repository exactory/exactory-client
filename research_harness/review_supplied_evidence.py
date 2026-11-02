"""Locate correction evidence within bytes actually supplied to prior reviewers."""

import json

from .errors import ResearchError
from .evidence import digest
from .source_links import contains, contains_locator, validate_link
from .review_objection_evidence import resolved_objection_entries

PROTOCOL = 'exactory-focused-review-evidence-v2'
PROMPT = (
    ' Native artifact references in assessments, objections and corrections are descriptive templates. '
    'For an artifact_descriptor, location is the native artifact path and sha256, size and media_type '
    'retain their native meanings; artifact_reference_template names the original artifact field. '
    'A template does not supply an entire original. Assess only original_evidence and the exact '
    'supplied_source_mappings excerpts. A mapped passage permits only its recorded locator bounds. '
    'Do not treat unsupplied portions of an original as reviewed evidence.'
)


def find_scientific_predecessor(assignment):
    """Follow a fixed scientific slot across continuations, repairs and upgrades."""
    context = assignment.get('context', {})
    repair = context.get('context_repair')
    if isinstance(repair, dict):
        return repair.get('assignment_id')
    return context.get('prior_assignment_id') or context.get('staged_upgrade', {}).get('assignment_id')


def visit_lineage(records, artifacts, assignment_ids, visit, *, validation_key=None):
    """Walk dependencies with a separate cycle guard and optional completed-node reuse."""
    from .evaluation import Evaluation
    evaluation = Evaluation.of(records, artifacts)
    records = evaluation.records
    active, seen = set(), set()
    pending = [(identifier, False) for identifier in assignment_ids]
    while pending:
        identifier, exiting = pending.pop()
        key = ('lineage_checked', evaluation._namespace, validation_key, identifier)
        if exiting:
            active.remove(identifier)
            seen.add(identifier)
            if validation_key is not None:
                evaluation._retain_once(key, lambda: True)
            continue
        if identifier in active:
            raise ResearchError('review_context_repair_mismatch', 'Reviewer lineage must be acyclic')
        if identifier in seen:
            continue
        if validation_key is not None and evaluation._retain_once(key, None):
            seen.add(identifier)
            continue
        assignment = records.get('review_assignment', {}).get(identifier)
        if assignment is None:
            raise ResearchError('review_record_missing', 'The exact evidence-supplying assignment is missing')
        active.add(identifier)
        pending.append((identifier, True))
        if assignment.get('staged_review') is not None:
            from .direction_reviews import collect_selected_task_graph
            graph = collect_selected_task_graph(records, evaluation, identifier)
            for _, packet in graph['packets']:
                visit(packet)
            if graph.get('frame_packet') is not None:
                visit(graph['frame_packet'])
            continue
        from .review_protocol import source_delivery_packet
        visit(source_delivery_packet(records, evaluation, assignment))
        parent = assignment.get('history_assignment_id') or find_scientific_predecessor(assignment)
        if parent is None and assignment['role'] == 'slate' and assignment.get('session_id') != identifier:
            parent = assignment.get('session_id')
        if parent is not None:
            pending.append((parent, False))


def lineage_packets(records, artifacts, assignment_ids):
    result = []
    visit_lineage(records, artifacts, assignment_ids, result.append)
    return result


def list_delivered_parts(packet):
    """The part deliveries of a packet that reached their reviewer: each maps one part of a stored extracted text to its
    span, so a citation within the part counts as delivered. A part that a capacity deferral withheld does not."""
    return [delivery for delivery in packet.get('source_deliveries', [])
            if 'part' in delivery and delivery.get('status') in ('delivered', 'partial')]


def list_delivered_objections(packet):
    """The material objections and historical-review objections that a packet carries. A problem evidence part
    carries material objections without their delivered evidence; the final packet carries that evidence.
    A first slate or result packet wraps each historical review with its independence."""
    historical = [entry.get('review', entry) for entry in packet.get('historical_reviews', [])]
    return packet.get('material_objections', []) + [objection for entry in historical
        for objection in entry.get('payload', entry).get('objections', [])]


def _list_review_objections(review):
    """The objections of a delivered review: a stored record, its payload, or a first packet's wrapper of either."""
    review = review.get('review', review)
    return review.get('payload', review).get('objections', [])


def collect_qualified_objections(packet):
    """Yield every objection that the harness delivered in a packet under a qualified identity: in its material
    objections, bars, own bars, historical reviews and historical bars, in the earlier versions and the version
    evidence reviews of each objection found, and in the packets that its scientific history carries. Only these
    objection lists are read: other delivered content and the observed outputs of a scientific history can hold an
    object of any shape. A repeated-part placeholder delivers no objections at that position. A qualified objection
    has the text fields id, stated_id and claim."""
    if packet is None:
        return
    material = packet.get('material_objections', [])
    pending = list(material) if isinstance(material, list) else []
    for key in ('bars', 'own_bars', 'historical_reviews', 'historical_bars'):
        reviews = packet.get(key, [])
        if isinstance(reviews, list):
            pending += [item for review in reviews for item in _list_review_objections(review)]
    while pending:
        objection = pending.pop(0)
        if all(isinstance(objection.get(key), str) for key in ('id', 'stated_id', 'claim')):
            yield objection
        pending += objection.get('history', [])
        pending += [item for entry in resolved_objection_entries(packet, objection, 'version_evidence')
                    if entry.get('kind') == 'record'
                    for item in _list_review_objections(entry['record'])]
    for turn in packet.get('scientific_history', []):
        yield from collect_qualified_objections(turn['packet'])


def register_supplied_artifact(references, reference):
    identity = reference['sha256']
    previous = references.get(identity)
    if previous is None:
        references[identity] = reference
    elif previous != reference:
        primary, alias = sorted((previous, reference), key=digest)
        references[identity] = primary
        references.pop(identity + ':' + digest(primary), None)
        references[identity + ':' + digest(alias)] = alias


def supplied_context(records, artifacts, assignment_ids, *, frozen=False):
    """The artifacts that the assignments' authenticated requests attached, and the passage mappings they delivered:
    prior context, an adjudicator's original and version passages, and passages delivered with a material objection or
    an objection of a historical review. Two descriptors of the same bytes are both kept: the one whose digest sorts
    first is the entry of the SHA-256, and every other one is an entry of the SHA-256 joined with its own digest.
    Historical deliveries follow the recorded citation contract."""
    references, mappings = {}, []
    from .research_decisions import read_review_citation_delivery
    for assignment_id in assignment_ids:
        delivery = read_review_citation_delivery(records, artifacts, records['review_assignment'][assignment_id])
        for reference in delivery['artifacts'].values():
            artifacts.read(reference)
            register_supplied_artifact(references, reference)
        for entry in delivery['source_mappings']:
            # Legacy mappings, with or without a saved citation, meet the receiving role's evidence contract.
            if not frozen:
                entry = deliver_passage(records, artifacts, entry)
            # A passage that a bar session and an objection both delivered counts once.
            if entry in mappings:
                continue
            artifacts.read(entry['excerpt'])
            mappings.append(entry)
    return references, mappings


def _inline_passage_received(records, artifacts, packets, link):
    """An actual inline span supplies only its bound native reading's exact bytes."""
    from .direction_sources import _find_inline_interval
    from .evaluation import Evaluation
    from .evidence import digest
    if link.get('locator', {}).get('kind') not in ('text', 'span'):
        return False
    evaluation = Evaluation.of(records, artifacts)
    try:
        evaluation.link(link)
        for packet in packets:
            for key in ('problem_evidence', 'direction_evidence'):
                evidence = packet.get(key, {})
                for passage in evidence.get('passages', []):
                    native = records.get('reading', {}).get(passage.get('reading_id'))
                    if (native is None or passage.get('version_id') != link['version_id']
                            or not any(row.get('id') == native['id'] and row.get('digest') == digest(native)
                                       for row in evidence.get('readings', []))):
                        continue
                    interval = _find_inline_interval(evaluation, passage, link)
                    if interval is None:
                        continue
                    inspection = native['inspections'][passage['inspection_index']]
                    if (inspection['link']['artifact'] == link['artifact']
                            and interval[0] <= link['locator']['start'] < link['locator']['end'] <= interval[1]):
                        return True
    except (ResearchError, KeyError, TypeError, ValueError):
        return False
    return False


def is_received(records, reference, references, mappings, *, artifacts, frozen=False, packets=()):
    """Whether a cited artifact descriptor or source link names content that the given delivery supplied: the
    artifact in full, or, for a source link, a locator within a supplied passage of the same artifact (text locators
    by offsets; another kind equal to the passage's). A raw descriptor of an artifact delivered only as passages names
    those passages, without widening their bounds."""
    artifact = reference['link']['artifact'] if reference.get('kind') == 'source' else reference
    if packets and _is_staged_original_received(artifacts, artifact, reference, packets):
        return True
    if (reference.get('kind') == 'source' and packets
            and _inline_passage_received(records, artifacts, packets, reference['link'])):
        return True
    if reference.get('kind') == 'source' and reference['link']['locator'].get('kind') == 'pdf' and packets:
        from .evaluation import Evaluation
        from .review_visual_sources import has_pdf_page
        evaluation = Evaluation.of(records, artifacts)
        if any(has_pdf_page(evaluation, packet, reference['link']) for packet in packets):
            return True
    if any(item['sha256'] == artifact['sha256'] for item in references.values()):
        return True
    passages = [mapping for mapping in mappings if mapping['origin']['artifact_descriptor']['sha256'] == artifact['sha256']]
    if reference.get('kind') != 'source':
        return bool(passages) if not packets else False
    if frozen:
        # Delivery supplies the same version's exact bytes and bounds. Current captured-source and component
        # qualification remains a separate recording check; it cannot create or remove a pure refusal.
        return any(mapping['version_id'] == reference['link']['version_id']
                   and contains_locator(cite_passage(artifacts, mapping)['locator'], reference['link']['locator'])
                   for mapping in passages)
    return any(contains(cite_passage(artifacts, mapping), reference['link'], records) for mapping in passages)


def _is_staged_original_received(artifacts, artifact, reference, packets):
    """Credit the union of exact delivered spans without widening a citation."""
    from .review_staged import interval_gaps, make_source, materialize_quotation
    source_id = 'artifact:' + digest(artifact)
    originals = [row for packet in packets for row in packet.get('staged_review', {}).get('originals', [])
                 if row.get('span', {}).get('source_id') == source_id]
    if not originals:
        return False
    try:
        content = artifacts.read(artifact).decode('utf-8')
        source = make_source(source_id, 'artifact', content, identity={'artifact': artifact}, descriptor=artifact)
        spans = [row['span'] for row in originals
                 if materialize_quotation(source, row['span'])['text'] == row.get('text')]
        locator = reference.get('link', {}).get('locator')
        if locator is None:
            required = [[0, len(content)]]
        elif locator.get('kind') in ('text', 'span'):
            required = [[locator['start'], locator['end']]]
        else:
            # Other native locators need their existing full-body or mapping contract.
            return False
        return not interval_gaps(required, [[span['start'], span['end']] for span in spans])
    except (ResearchError, UnicodeError, KeyError, TypeError, ValueError):
        return False


def cite_passage(artifacts, mapping):
    """The exact link of a delivered passage; correction input does not require output citation eligibility."""
    origin = mapping['origin']
    descriptor = origin['artifact_descriptor']
    artifact = {'path': descriptor['location'], **{key: descriptor[key] for key in ('sha256', 'size', 'media_type')}}
    locator = origin['locator']
    if locator['kind'] in ('text', 'span'):
        locator = {'kind': 'span', **{key: locator[key] for key in ('start', 'end', 'sha256')}}
    elif locator['kind'] == 'json':
        content = artifacts.read(mapping['excerpt']).decode('utf-8')
        value = json.loads(content) if mapping['excerpt']['media_type'] == 'application/json' else content
        locator = {'kind': 'json', 'pointer': locator['pointer'], 'value': value}
    else:
        raise ResearchError('invalid_review_correction', 'The saved passage mapping has an unsupported locator')
    return {'version_id': mapping['version_id'], 'source_id': origin['source_id'], 'artifact': artifact, 'locator': locator}


def can_cite_passage(records, artifacts, mapping):
    """Whether the receiving reviewer or adjudicator can record this passage as source evidence."""
    if mapping['origin']['locator']['kind'] not in ('text', 'span'):
        return False
    from .reading import validate_read_evidence
    try:
        validate_read_evidence(records, artifacts, cite_passage(artifacts, mapping))
    except ResearchError:
        return False
    return True


def deliver_passage(records, artifacts, mapping):
    """Offer a copyable citation only when it meets the output evidence validator."""
    delivered = {key: value for key, value in mapping.items() if key != 'citation'}
    if can_cite_passage(records, artifacts, mapping):
        delivered['citation'] = cite_passage(artifacts, mapping)
    return delivered


def undelivered_references(records, artifacts, evidence, carried, references, mappings, *, frozen=False, packets=()):
    """The delivery failures of validated evidence, in the review refusal's reference detail format."""
    from .review_protocol import find_carried_record
    missing = []
    for reference in evidence:
        if reference.get('kind') == 'record':
            if find_carried_record(records, carried, reference, frozen=frozen) is None:
                missing.append({'record_kind': reference['record_kind'], 'id': reference['id'],
                                'digest': reference.get('digest')})
        elif not is_received(records, reference, references, mappings, artifacts=artifacts, frozen=frozen, packets=packets):
            artifact = reference['link']['artifact'] if reference.get('kind') == 'source' else reference
            details = {'sha256': artifact['sha256']}
            if reference.get('kind') == 'source':
                details['locator'] = reference['link']['locator']
            missing.append(details)
    return missing


def adjudicator_undelivered_references(records, artifacts, assignment_id, evidence, *, frozen=False, attempt=None):
    """Check the adjudicator's actual packet delivery, including its focused refresh/correction ancestry."""
    from .review_delivered_evidence import project_delivery_view
    from .research_decisions import find_review_undelivered_references, read_review_citation_delivery
    assignment = records['review_assignment'][assignment_id]
    if assignment.get('staged_review', {}).get('kind') == 'controller':
        delivery = project_delivery_view(records, artifacts, lineage_packets(records, artifacts, [assignment_id]))
    else:
        delivery = read_review_citation_delivery(records, artifacts, assignment, attempt=attempt)
    return find_review_undelivered_references(records, artifacts, evidence, delivery,
        require_complete_artifacts=assignment.get('staged_review') is not None)


def validate_correction(records, artifacts, evidence, references, mappings):
    """A raw original hash does not widen permission granted to an exact excerpt."""
    count = 0

    def walk(value):
        nonlocal count
        if isinstance(value, dict):
            if {'version_id', 'source_id', 'artifact', 'locator'} <= value.keys():
                validate_link(records, artifacts, value)
                if value['artifact'] not in references.values():
                    allowed = False
                    for mapping in mappings:
                        outer = cite_passage(artifacts, mapping)
                        validate_link(records, artifacts, outer)
                        if contains(outer, value, records):
                            allowed = True
                            break
                    if not allowed:
                        raise ResearchError('invalid_review_correction', 'A correction must stay inside an exact source passage actually supplied earlier')
                count += 1
            elif {'path', 'sha256', 'size', 'media_type'} <= value.keys():
                if value not in references.values():
                    raise ResearchError('invalid_review_correction', 'An unsupplied original requires a permitted exact source locator')
                artifacts.read(value)
                count += 1
            else:
                for item in value.values():
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(evidence)
    if not count:
        raise ResearchError('invalid_review_correction', 'Correction evidence must have been supplied in the original scientific history')
