"""Deliver shared material-objection evidence once and resolve it within its packet."""

from .errors import ResearchError
from .evidence import digest

FIELDS = ('version_evidence', 'version_source_mappings')
REPEATED = 'repeated-in-material-objections'
REPEATED_OBJECTION_EVIDENCE_NOTE = (
    'This reference stands in for the full entry, which is in material_objections of this packet under the '
    'objection that objection_id names, in the list that field names, at the position that index gives. The full '
    'entry belongs to the evidence of the objection that contains this note. The citation rules of the prompt apply '
    'to the full entry, and this reference is not a citation.'
)


def deduplicate_material_objections(packet):
    """Replace later equal entries in a new final projection, retaining the first entry's exact position."""
    seen = {}
    for objection in packet.get('material_objections', []):
        for field in FIELDS:
            for index, entry in enumerate(objection.get(field, [])):
                identity = digest(entry)
                if identity in seen:
                    objection[field][index] = dict(delivery=REPEATED, **seen[identity], digest=identity,
                                                   note=REPEATED_OBJECTION_EVIDENCE_NOTE)
                else:
                    seen[identity] = {'objection_id': objection['id'], 'field': field, 'index': index}


def resolve_objection_entry(packet, entry):
    """Resolve a repetition only to the named full entry at its canonical digest in this same packet."""
    if entry.get('delivery') != REPEATED:
        return entry
    try:
        field, index = entry['field'], entry['index']
        if field not in FIELDS or type(index) is not int or index < 0:
            raise ValueError
        objection = next(item for item in packet.get('material_objections', [])
                         if item['id'] == entry['objection_id'])
        full = objection[field][index]
        if full.get('delivery') == REPEATED or digest(full) != entry['digest']:
            raise ValueError
    except (KeyError, IndexError, StopIteration, TypeError, ValueError) as error:
        raise ResearchError('review_objection_evidence_invalid',
                            'Repeated objection evidence must name its full entry in the same packet') from error
    return full


def resolved_objection_entries(packet, objection, field):
    """Read an objection's evidence without changing the frozen packet or earlier full deliveries."""
    return [resolve_objection_entry(packet, entry) for entry in objection.get(field, [])]
