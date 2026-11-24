"""Synthetic delivered evidence for the named openness gap contract.

The six descriptors hold authored text only. Real store fixtures use the same
content for captures, full readings and shared search fields.
"""

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading
from research_harness.acquisition import import_response
from research_harness.literature import record_search
from research_harness.problem_openness import serialize_record
from test_openness_finding_answer import build_answer
from test_research_problem_openness_minimum import ProblemOpennessMinimumTests

FIXED_PACKET_NAMES = ('variant-search', 'excluded-work', 'related-unread-work',
                      'index-only', 'unsearched-term', 'read-settling-work', 'excluded-work-long')
REQUEST_FORMS = ('v6-final', 'v7-final', 'page', 'single')
FIXTURE_DIRECTORY = Path(__file__).parent / 'fixtures'


def load_fixed_packet(name):
    """Read one self-contained synthetic case descriptor."""
    if name not in FIXED_PACKET_NAMES:
        raise ValueError('Unknown fixed packet: ' + name)
    return json.loads((FIXTURE_DIRECTORY / ('openness-named-gaps-' + name + '.json')).read_text())


def build_ground(packet, *, term=None, work=None):
    """Build a fully stated gap using the delivered quotation of a fixture."""
    supplied = packet['_fixture']
    basis = dict(supplied['identity'], text=supplied['sentence'])
    ground = dict(kind='reviewer_gap', text='A named gap remains in the delivered evidence.',
                  formulation=supplied.get('formulation', packet['native']['scientific_context']['openness_minimum']['statement']),
                  basis=[basis], need='Whether this source or term settles the finite connected case.',
                  unsettled='The bound searches and the read partial example do not answer that question.')
    if work is not None:
        ground['work'] = copy.deepcopy(work)
    else:
        ground['search'] = dict(terms=[term or supplied['missing_term']],
                                missing_term=term or supplied['missing_term'])
    return ground


def build_packet(name='variant-search', form='v6-final'):
    """Build a compact final envelope with genuine excerpt identities and shared fields."""
    from test_openness_finding_answer import build_packet as old_packet
    descriptor = load_fixed_packet(name)
    packet = old_packet('already_solved' if name == 'read-settling-work' else 'supported_open')
    minimum = packet['native']['scientific_context']['openness_minimum']
    minimum.update(schema_version=2, statement=descriptor['statement'], neighbours=[descriptor['neighbour']],
                   searches=[], reading_digests=['d' * 64], supporting_reading_digests=[],
                   settling_work_id='work-read' if name == 'read-settling-work' else None)
    fields = {hashlib.sha256(serialize_record(value)).hexdigest(): copy.deepcopy(value)
              for value in (descriptor['queries'], descriptor['scope'], descriptor['dispositions'])}
    references = {field: hashlib.sha256(serialize_record(descriptor[field])).hexdigest()
                  for field in ('queries', 'scope', 'dispositions')}
    searches = [dict(record_id='search-' + str(index), search_digest=str(index) * 64,
                     formulations=[formulation], **references)
                for index, formulation in enumerate((descriptor['statement'], descriptor['neighbour']), 1)]
    minimum['searches'] = [dict(formulation=row['formulations'][0], search_digest=row['search_digest'])
                          for row in searches]
    packet['finding_index'] = dict(searches=searches, fields=fields,
        read_works=[dict(reading_id='read-1', reading_digest='d' * 64, version_id='arxiv:2601.00001v1',
                        identifiers=['arxiv:2601.00001v1', 'work-read', descriptor['read_identifier']],
                        title=descriptor['read_title'])])
    identity = dict(version_id='arxiv:2601.00001v1', text_sha256='e' * 64)
    packet['reading_record'].update(excerpts=[dict(identity, text=descriptor['sentence'], status='verified',
                                                  purpose='The exact delivered gap basis.')], passages=[])
    if form in ('v7-final', 'page'):
        identity = dict(unit='u1')
        packet['reading_final'] = dict(protocol='exactory-openness-reading-v7', page_plan_digest='f' * 64,
                                       page=dict(targets=['t1', 't2']),
                                       entries=[dict(unit='u1', text=descriptor['sentence'], status='verified')])
        packet.pop('reading_record')
    elif form == 'single':
        packet.pop('reading_record')
        packet['evidence'] = [dict(kind='fulltext', version_id=identity['version_id'],
                                   text_sha256=identity['text_sha256'], encoding='utf-8', content=descriptor['sentence'])]
        packet['native']['evidence'] = copy.deepcopy(packet['evidence'])
    else:
        packet['reading_record']['protocol'] = 'exactory-openness-reading-v6'
    packet['_fixture'] = dict(identity=identity, sentence=descriptor['sentence'],
                              missing_term=descriptor['missing_term'])
    return packet


def build_fixed_answer(packet, *, outcome=None, ground=None):
    """Return the expected accepted answer, or an explicit ground under test."""
    if outcome is None:
        outcome = 'unresolved' if ground is not None else 'supported_open'
    return build_answer(packet, outcome, [] if ground is None else [ground])


def record_fixed_search(case, formulation, found, dispositions, *, queries=None, reasons=None):
    """Capture two searches with identical queries and supported disposition rows.

    queries replaces the queries of the descriptor; reasons maps a found work to the reason of its disposition row.
    """
    queries = queries or case.descriptor['queries']
    reasons = reasons or {}
    titles = {case.versions[0]: case.descriptor['read_title']}
    titles.update({version: 'Infinite minor models: results for infinite graphs only'
                   for version in found if version != case.versions[0]})
    responses = []
    for query in queries:
        data = dict(query=query, results=[dict(id=version, title=titles[version]) for version in found])
        case.sequence += 1
        captured = import_response(case.store, 'mcp', json.dumps(data).encode(),
            source_url='https://example.org/synthetic-minor-search', captured_at='2026-10-15T12:00:00Z',
            media_type='application/json', mappings=[dict(id='/results/%d/id' % index,
                title='/results/%d/title' % index) for index in range(len(found))],
            expected_revision=case.store.revision, request_id='fixed-search-capture-' + str(case.sequence))
        responses.append(dict(source_id=captured['source_ids'][0], query=query,
            query_locator=dict(kind='json', pointer='/query', value=query), results_pointer='/results'))
    identifier = 'fixed-search-' + str(case.sequence)
    case.mutate(record_search, dict(id=identifier, profile='research', purpose='direct', question=formulation,
        scope=case.descriptor['scope'], queries=list(queries), responses=responses,
        captured_at='2026-10-15T12:00:00Z', found_work_ids=list(found), verdict='nothing-new', cited_work_ids=[],
        dispositions=[dict(work_id=version, disposition=dispositions.get(version, 'relevant'),
            reason=reasons.get(version, 'The delivered row concerns infinite graphs only.' if version in dispositions
                               else 'The planar partial case bears on both formulations.')) for version in found],
        resolved=[], impact='Evidence for the exact synthetic formulation.', gaps=[]))
    return case.store.snapshot()['records']['literature_search'][identifier]


def bind_fixture_basis(case, packet):
    """Attach test metadata that identifies a sentence already delivered in a real packet."""
    result = copy.deepcopy(packet)
    sentence = case.descriptor['sentence']
    if 'reading_final' in packet:
        identity = dict(unit=next(row['unit'] for row in packet['reading_final']['entries']
                                  if sentence in row.get('text', '')))
    else:
        identity = dict(version_id=case.versions[0])
    result['_fixture'] = dict(identity=identity, sentence=sentence, formulation=case.descriptor['statement'],
                              missing_term=case.descriptor['missing_term'])
    return result


class NamedGapStoreCase(OpennessReadingCase):
    """One reading and two searches, suitable for fixed packet acceptance stores."""

    reading_protocol = None
    use_released_chain_default = True
    text_sizes = (1000,)
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    capacity_probe_tokens = 487900
    packet_name = 'variant-search'
    request_form = 'single'

    def setUp(self):
        self.descriptor = load_fixed_packet(self.packet_name)
        selected_protocol = (openness_reading.PROTOCOL_V6 if self.request_form in ('single', 'v6')
                             else 'exactory-openness-reading-v7')
        if self.request_form == 'v6':
            self.text_sizes = (120100,)
        if self.request_form == 'page':
            from research_harness import openness_reading_v7
            pages = patch.object(openness_reading_v7, 'V7_FINAL_FORMS', ('request', 'pages'))
            pages.start()
            self.addCleanup(pages.stop)
            room = patch('research_harness.openness_final_pages.measure_page_room', return_value=10000)
            room.start()
            self.addCleanup(room.stop)
            from research_harness import openness_final_pages
            build_inputs = openness_final_pages.build_final_inputs
            def split_targets(*args, **kwargs):
                inputs, catalog, full = build_inputs(*args, **kwargs)
                inputs['target_allowance'] = 1
                return inputs, catalog, full
            split = patch.object(openness_final_pages, 'build_final_inputs', side_effect=split_targets)
            split.start()
            self.addCleanup(split.stop)
        if self.request_form in ('single', 'v6', 'v7', 'page'):
            enabled = patch.object(openness_reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (selected_protocol,))
            enabled.start()
            self.addCleanup(enabled.stop)
        super().setUp()
        work = copy.deepcopy(self.store.snapshot()['records']['work'][self.versions[0]])
        work['title'] = self.descriptor['read_title']
        work['aliases'] = list(dict.fromkeys(work.get('aliases', []) + [self.descriptor['read_identifier']]))
        self.put('work', self.versions[0], work)
        self.selected = self.select_statement(identifier='statement-fixed', candidate_id='candidate-1',
            statement=self.descriptor['statement'], neighbours=[self.descriptor['neighbour']])
        # All searches remain bound to their exact records, now under the selected formulations.
        self.searches = self.record_searches()
        self.search = self.searches[0]
        # The reading stub keeps these texts as excerpts wherever a delivered body holds them: the delivered sentence
        # and, in the long excluded case, the recorded judgment row of the excluded work.
        self.kept_texts = [self.descriptor['sentence']]
        if self.packet_name == 'excluded-work-long':
            self.kept_texts.append(serialize_record(self.search['dispositions'][1]).decode('utf-8'))

    def build_text(self, number):
        sentence = self.descriptor['sentence']
        partial = (
            'All graphs in this authored fixture are finite and simple. A proper colouring assigns different '
            'colours to adjacent vertices; chi(G) is the smallest number of colours. A graph minor is obtained '
            'by vertex deletions, edge deletions and edge contractions. The Hadwiger number h(G) is the largest '
            't for which the complete graph K_t is a minor of G. The problem asks whether chi(G) <= h(G).\n'
            'A planar partial case: forests. A nonempty edgeless forest has chi=h=1. A forest with an edge '
            'is bipartite, so chi=2, and that edge gives a K_2 minor, so h>=2. The empty graph has chi=h=0. '
            'Thus every forest satisfies the inequality. This proves no statement for all finite simple graphs.\n')
        head = sentence + '\n' + partial
        body = 'The planar partial-case argument leaves the general minor question open in this fixture. ' * (self.text_sizes[0] // 20 + 1)
        return head + body[:max(0, self.text_sizes[0] - len(head) - 18)] + '\nReferences: none.'

    def record_searches(self):
        # The first call from the parent happens before we select the synthetic statement.
        if self.selected['statement'] != self.descriptor['statement']:
            return [ProblemOpennessMinimumTests.search(self, self.versions,
                        query=self.descriptor['queries'][0])]
        found = self.versions
        dispositions, reasons = {}, {}
        if self.packet_name in ('excluded-work', 'excluded-work-long'):
            # Every disposition row of the descriptor after the first judges one more found work.
            rows = self.descriptor['dispositions'][1:]
            excluded = [self.metadata(2 + number) for number in range(len(rows))]
            found = list(found) + excluded
            dispositions = {work: row['disposition'] for work, row in zip(excluded, rows)}
            reasons = {work: row['reason'] for work, row in zip(excluded, rows)}
        result = []
        for formulation in (self.descriptor['statement'], self.descriptor['neighbour']):
            row = record_fixed_search(self, formulation, found, dispositions, reasons=reasons)
            result.append(row)
        return result

    def export(self, priority=(), name='openness-packet', assignment_id='turn-1'):
        from research_harness.problem_openness import export_openness
        minimum = dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in self.searches],
                       settling_work_id=self.versions[0] if self.packet_name == 'read-settling-work' else None,
                       duplicate_resolutions=[])
        return export_openness(self.store, dict(assignment_id=assignment_id, reviewer_id='openness-reviewer',
            minimum=minimum), self.root / name)['native_packet']

    def build_reading_response(self, packet, excerpts=None, passages=()):
        if packet.get('reading_turn', {}).get('protocol') == 'exactory-openness-reading-v7' or self.request_form in ('v7', 'page'):
            binding = packet['reading_turn']
            excerpts = [dict(unit=row['handle'], text=text, purpose='The delivered scientific basis.',
                             targets=['t1', 't2'])
                        for row in packet.get('evidence', []) for text in self.kept_texts
                        if text in row.get('content', '')]
            return dict(schema_version=2, stage='reading', notes='Read the complete partial example.',
                excerpts=excerpts, gaps=[], passage_requests=[],
                **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})
        if excerpts is None:
            excerpts = [dict(version_id=self.versions[0], text=self.descriptor['sentence'],
                              purpose='The delivered scientific basis.')
                        for row in packet.get('reading_turn', {}).get('reading_plan', {}).get('delivered_now', [])
                        if row.get('version_id') == self.versions[0]]
        return super().build_reading_response(packet, excerpts, passages)

    def finish_reading(self, native, first='turn-1'):
        self.assign(first, native)
        current = first
        for number in range(40):
            packet = self.read_packet(current)
            if 'reading_turn' not in packet:
                return current, packet
            self.invoke(current, self.build_reading_response(packet))
            following = 'read-%d' % (number + 1)
            self.assign(following, native, prior=current)
            current = following
        self.fail('The finite synthetic inventory must finish reading.')


def fixture_case(name, protocol='single'):
    """Create a disposable real store; the caller must call doCleanups()."""
    case = NamedGapStoreCase()
    case.packet_name = name
    case.request_form = protocol
    try:
        case.setUp()
    except BaseException:
        case.doCleanups()
        raise
    return case


def build_reason(label, characters):
    """Return an authored recorded judgment of exactly the given length."""
    sentence = 'Recorded judgment %s: the delivered row concerns infinite graphs only. ' % label
    return (sentence * (characters // len(sentence) + 1))[:characters]


def build_query(label, characters):
    """Return an authored bound query of exactly the given length."""
    words = 'Hadwiger colouring minor variant %s ' % label
    return (words * (characters // len(words) + 1))[:characters].strip() + ' ' + label


class IndexStoreCase(NamedGapStoreCase):
    """A store whose bound searches follow search_shapes: rows {formulation, queries?, reason?}.

    formulation 0 is the statement and 1 the neighbour. Equal reasons give two searches one shared dispositions value.
    """

    search_shapes = ()

    def record_searches(self):
        if self.selected['statement'] != self.descriptor['statement']:
            return super().record_searches()
        formulations = (self.descriptor['statement'], self.descriptor['neighbour'])
        return [record_fixed_search(self, formulations[shape['formulation']], self.versions, {},
                                    queries=shape.get('queries'),
                                    reasons={self.versions[0]: shape['reason']} if shape.get('reason') else None)
                for shape in self.search_shapes]


def index_case(shapes, protocol='v7', name='variant-search'):
    """Create a disposable store of IndexStoreCase; the caller must call doCleanups()."""
    case = IndexStoreCase()
    case.packet_name = name
    case.request_form = protocol
    case.search_shapes = shapes
    case.setUp()
    return case
