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


def lineage_packets(records, artifacts, assignment_ids):
    pending, seen, result = list(assignment_ids), set(), []
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        assignment = records.get('review_assignment', {}).get(identifier)
        if assignment is None:
            raise ResearchError('review_record_missing', 'The exact evidence-supplying assignment is missing')
        from .review_protocol import source_delivery_packet
        packet = source_delivery_packet(records, artifacts, assignment)
        result.append(packet)
        parent = assignment.get('history_assignment_id') or assignment.get('context', {}).get('prior_assignment_id')
        repair = assignment.get('context', {}).get('context_repair')
        if parent is None and isinstance(repair, dict):
            parent = repair.get('assignment_id')
        if parent is None and assignment['role'] == 'slate' and assignment.get('session_id') != identifier:
            parent = assignment.get('session_id')
        if parent is not None:
            pending.append(parent)
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
    """The artifacts that the packets of the assignments' lineages attached, and the passage mappings they delivered:
    prior context, an adjudicator's original and version passages, and passages delivered with a material objection or
    an objection of a historical review. Two descriptors of the same bytes are both kept: the one whose digest sorts
    first is the entry of the SHA-256, and every other one is an entry of the SHA-256 joined with its own digest.
    Focused refreshes and corrections retain their packet lineage."""
    references, mappings = {}, []
    from .review_source_capacity import BODY_DELIVERY
    from .review_source_complete import ALTERNATE
    for packet in lineage_packets(records, artifacts, assignment_ids):
        for entry in packet.get('evidence', []):
            if entry.get('delivery') in (BODY_DELIVERY, ALTERNATE) or entry.get('encoding') == 'omitted':
                continue
            reference = entry['artifact']
            artifacts.read(reference)
            register_supplied_artifact(references, reference)
        delivered = (packet.get('prior_context', []) + packet.get('version_source_mappings', [])
                     + packet.get('supplied_source_mappings', []))
        delivered += [mapping for objection in list_delivered_objections(packet)
                      for mapping in resolved_objection_entries(packet, objection, 'version_source_mappings')]
        delivered += list_delivered_parts(packet)
        for entry in delivered:
            if 'origin' not in entry or 'excerpt' not in entry:
                continue
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


def adjudicator_undelivered_references(records, artifacts, assignment_id, evidence, *, frozen=False):
    """Check the adjudicator's actual packet delivery, including its focused refresh/correction ancestry."""
    from .review_protocol import collect_carried_records
    packets = lineage_packets(records, artifacts, [assignment_id]) if frozen else None
    carried = collect_carried_records(records, artifacts, assignment_id, packets=packets)
    references, mappings = supplied_context(records, artifacts, [assignment_id], frozen=frozen)
    return undelivered_references(records, artifacts, evidence, carried, references, mappings, frozen=frozen)


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
