"""Source-located supplement correspondence, never article/version equivalence.

The immutable assessment is a research assertion. These checks establish its
pins, scope and inspected locations, not the truth of its scientific judgment.
"""

import json
import re

from .errors import ResearchError
from .evidence import digest
from .operations import fields, strings, text
from .source_links import (captured_source, complete_original, contains, exact_work,
                           fulltext_capture, read_locator, validate_link)


def is_component(capture):
    return capture is not None and 'component' in capture


def binding_identity(capture):
    return digest(capture['component']) if is_component(capture) else None


def _parent_link(records, artifacts, identifier, parent, link):
    if (not isinstance(link, dict) or link.get('version_id') != identifier
            or link.get('source_id') != parent['source_id']):
        raise ResearchError('component_parent_mismatch', 'Assessment evidence must identify the pinned parent source')
    context = validate_link(records, artifacts, link)
    if not complete_original(context):
        raise ResearchError('component_parent_mismatch', 'Assessment evidence requires acquired parent originals')
    # Do not demand full-depth acceptance: the missing component is precisely
    # why the main reading can be partial. Validate actual saved inspections.
    for reading in records.get('reading', {}).values():
        if reading['version_id'] != identifier:
            continue
        for inspection in reading['inspections']:
            inspected = inspection['link']
            if (inspected['source_id'] == parent['source_id'] and contains(inspected, link, records)):
                validate_link(records, artifacts, inspected)
                return
    raise ResearchError('component_evidence_uninspected', 'Inspect the cited parent evidence before binding its supplement')


def _assessment(records, artifacts, identifier, parent, capture, reference):
    try:
        assessment = json.loads(artifacts.read(reference))
    except (ValueError, UnicodeError) as error:
        raise ResearchError('invalid_component', 'The assessment must be a pinned JSON object') from error
    fields(assessment, ('status', 'scope', 'conclusion', 'limitations', 'historical_attachment_identity',
                        'main_article_equivalence', 'evidence'), code='invalid_component')
    if (assessment['status'] not in ('accepted', 'pending')
            or assessment['scope'] != 'required_supplement_only'
            or assessment['historical_attachment_identity'] is not False
            or assessment['main_article_equivalence'] is not False):
        raise ResearchError('invalid_component', 'Correspondence is scoped to the required supplement, not article or historical identity')
    text(assessment['conclusion'], 'Scoped conclusion', code='invalid_component')
    strings(assessment['limitations'], 'Correspondence limitations', nonempty=True, code='invalid_component')
    evidence = assessment['evidence']
    fields(evidence, ('requirement', 'identity', 'references', 'conditions'), code='invalid_component')
    _parent_link(records, artifacts, identifier, parent, evidence['requirement'])
    fields(evidence['identity'], ('title', 'authors'), ('affiliations',), code='invalid_component')
    pairs = list(evidence['identity'].values())
    for category in ('references', 'conditions'):
        if not isinstance(evidence[category], list) or not evidence[category]:
            raise ResearchError('invalid_component', 'Correspondence requires paired references and scientific conditions')
        pairs.extend(evidence[category])
    for pair in pairs:
        fields(pair, ('parent', 'component', 'judgment'), code='invalid_component')
        text(pair['judgment'], 'Scientific correspondence judgment', code='invalid_component')
        _parent_link(records, artifacts, identifier, parent, pair['parent'])
        location = pair['component']
        fields(location, ('document', 'locator'), code='invalid_component')
        if location['document'] not in ('original', 'text') or capture.get(location['document']) is None:
            raise ResearchError('invalid_component', 'Component evidence must locate the actual acquired original or extraction')
        read_locator(artifacts, capture[location['document']], location['locator'], capture=capture)
    if assessment['status'] != 'accepted':
        raise ResearchError('component_pending', 'The recorded correspondence assessment remains unresolved')


def validate_spec(records, artifacts, identifier, capture, spec):
    """Check the request against acquired bytes, existing units and inspections."""
    fields(spec, ('kind', 'unit_id', 'parent_source_id', 'parent_original_sha256',
                  'expected_original_sha256', 'basis', 'assessment'), code='invalid_component')
    if spec['kind'] != 'supplement' or spec['basis'] != 'component_correspondence':
        raise ResearchError('invalid_component', 'Only explicit supplement component correspondence is supported')
    text(spec['unit_id'], 'Required supplement unit ID', code='invalid_component')
    for name in ('parent_original_sha256', 'expected_original_sha256'):
        if not isinstance(spec[name], str) or not re.fullmatch('[0-9a-f]{64}', spec[name]):
            raise ResearchError('invalid_component', 'Component and parent originals require SHA-256 pins')
    work = exact_work(records, identifier)
    parent = fulltext_capture(work, spec['parent_source_id'])
    if (parent is None or is_component(parent) or parent.get('original') is None
            or parent['original']['sha256'] != spec['parent_original_sha256']):
        raise ResearchError('component_parent_mismatch', 'The parent pin must identify this exact work and main original')
    source = captured_source(records, artifacts, parent['source_id'])
    if not complete_original({'source': source, 'capture': parent}):
        raise ResearchError('component_parent_mismatch', 'The parent main original must be completely acquired')
    artifacts.read(parent['original'])
    bundles = [b for b in records.get('source_bundle', {}).values()
               if b['version_id'] == identifier and b['source_id'] == parent['source_id']
               and b['original_sha256'] == spec['parent_original_sha256'] and b['scope'] == 'article']
    if not any(u['id'] == spec['unit_id'] and u['kind'] == 'supplement' and u['required']
               for b in bundles for u in b['units']):
        raise ResearchError('component_requirement_missing', 'Import the parent bundle with this required supplement first')
    if capture.get('original') is None or capture['original']['sha256'] != spec['expected_original_sha256']:
        raise ResearchError('component_hash_mismatch', 'Fresh component bytes differ from the expected original')
    artifacts.read(capture['original'])
    if capture.get('text') is None or capture['extraction_status'] != 'extracted':
        raise ResearchError('component_pending', 'Complete extraction is required to validate component evidence')
    _assessment(records, artifacts, identifier, parent, capture, spec['assessment'])


def validate_binding(records, artifacts, identifier, capture, *, main_sha256=None):
    """Revalidate persisted relationship and exact parent on every source use."""
    if not is_component(capture):
        return
    binding = capture['component']
    if not isinstance(binding, dict) or binding.get('status') != 'bound':
        raise ResearchError('component_pending', 'The supplement relationship is not bound')
    fields(binding, ('status', 'parent_version_id', 'spec', 'identity'), code='invalid_component')
    if binding['parent_version_id'] != identifier:
        raise ResearchError('component_parent_mismatch', 'Component relationships cannot migrate between exact versions')
    if binding['identity'] != digest([identifier, binding['spec']]):
        raise ResearchError('invalid_component', 'The immutable component relationship identity has changed')
    validate_spec(records, artifacts, identifier, capture, binding['spec'])
    source = captured_source(records, artifacts, capture['source_id'])
    if not complete_original({'source': source, 'capture': capture}):
        raise ResearchError('component_pending', 'Only complete acquired component originals establish links')
    if main_sha256 is not None and binding['spec']['parent_original_sha256'] != main_sha256:
        raise ResearchError('component_parent_mismatch', 'The bundle main original differs from the component parent pin')


def component_page_obligations(records, artifacts, bundle):
    """Every PDF component page needs an inventoried original-page inspection."""
    from .graph import obligation
    pending, seen = [], set()
    work = exact_work(records, bundle['version_id'])
    for unit in bundle['units']:
        link = unit['link']
        if link is None:
            continue
        capture = fulltext_capture(work, link['source_id'])
        if not is_component(capture) or capture['source_id'] in seen:
            continue
        seen.add(capture['source_id'])
        validate_binding(records, artifacts, work['id'], capture, main_sha256=bundle['original_sha256'])
        spec = capture['component']['spec']
        if not any(u['id'] == spec['unit_id'] and u['kind'] == 'supplement' and u['required']
                   and u['link'] is not None and u['link']['source_id'] == capture['source_id'] for u in bundle['units']):
            pending.append(obligation('component_requirement_missing', 'Fill the existing required supplement unit.',
                                      version_id=work['id'], unit_id=spec['unit_id']))
        if capture['extraction']['media_type'] != 'application/pdf':
            continue
        pages = artifacts.read(capture['text']).decode('utf-8').split('\f')
        if not pages[-1].strip():
            pages.pop()
        inventoried = {u['link']['locator']['page_index'] for u in bundle['units']
                       if u['required'] and u['kind'] in ('figure', 'table', 'equation') and u['link'] is not None
                       and u['link']['source_id'] == capture['source_id']
                       and u['link']['artifact'] == capture['original']
                       and u['link']['locator']['kind'] == 'pdf' and u['link']['locator']['region'] == [0, 0, 1, 1]}
        for page in sorted(set(range(len(pages))) - inventoried):
            pending.append(obligation('component_page_missing', 'Inventory and inspect this whole original component page.',
                                      version_id=work['id'], unit_id=spec['unit_id'], page_index=page))
    return pending
