"""A problem record whose openness packet carries three full readings, reviewed on a Codex route."""

import json

from codex_route_fixtures import invoke_codex, limit_codex_input, read_sent_packet, record_codex_route
from test_research_problem_claims import ProblemCase
from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
from research_harness import review_protocol as protocol
from research_harness.literature import import_bundle
from research_harness.reading import record_reading

# Two of the three full texts fit one reading turn; the single-turn packet with all three does not.
INPUT_LIMIT_CHARACTERS = 26000
RESULT_FINDINGS = ('statement', 'assumptions', 'scope', 'dependencies', 'policy')


def build_theorem(number):
    return 'Theorem %d.1. Every bounded sequence of type %d has a finite extremal example.' % (number, number)


def build_body(number):
    return ('Paper %d opening line.\n' % number + build_theorem(number) + '\n'
            + ('Supporting argument %d continues here. ' % number) * 220 + 'References: none.')


def build_sized_body(number, characters):
    """A full text of exactly the given length, in paragraphs separated by blank lines."""
    head = 'Paper %d opening line.\n' % number + build_theorem(number) + '\n\n'
    tail = 'References: none.'
    paragraph = ('Supporting argument %d continues here. ' % number) * 25 + '\n\n'
    filler = characters - len(head) - len(tail)
    return head + (paragraph * (filler // len(paragraph) + 1))[:filler] + tail


class OpennessReadingCase(ProblemCase):
    text_sizes = None

    def build_text(self, number):
        """The full text of work `number`: the short default body, or a body of the case's own size."""
        if self.text_sizes is None:
            return build_body(number)
        return build_sized_body(number, self.text_sizes[number - 1])

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.put_selecting_slate()
        self.selected = self.select_statement(candidate_id='candidate-1')
        self.versions, self.texts = [], {}
        for number in range(1, 1 + (3 if self.text_sizes is None else len(self.text_sizes))):
            version = self.metadata(number)
            capture = self.capture(version, pdf_text=self.build_text(number))
            bundle = self.bundle(version, capture=capture, bundle_id='bundle-%d' % number)
            # Span locators keep each reading record small next to its full text, as in a real record.
            for unit in bundle['units']:
                quote = unit['link']['locator']['quote']
                unit['link'] = self.link(version, capture['source_id'], capture['text'], quote, kind='span')
            bundle['inventory']['links'] = [bundle['units'][0]['link']]
            self.mutate(import_bundle, bundle)
            self.mutate(record_reading, self.full_note(bundle, 'read-%d' % number))
            self.versions.append(version)
            self.texts[version] = capture['text']
        self.search = ProblemOpennessMinimumTests.search(self, self.versions)
        record_codex_route(self)

    def limit_input(self, characters=INPUT_LIMIT_CHARACTERS):
        limit_codex_input(self, characters)

    def export(self, priority=(), name='openness-packet', assignment_id='turn-1'):
        from research_harness.problem_openness import export_openness
        minimum = {'searches': [{'formulation': self.selected['statement'], 'search_id': self.search['id']}],
                   'settling_work_id': None, 'duplicate_resolutions': []}
        if priority:
            minimum['text_priority'] = list(priority)
        return export_openness(self.store, {'assignment_id': assignment_id, 'reviewer_id': 'openness-reviewer',
                                            'minimum': minimum}, self.root / name)['native_packet']

    def assign(self, identifier, native_packet, prior=None):
        context = {'native_packet': native_packet}
        if prior is not None:
            context['prior_assignment_id'] = prior
        return self.mutate(protocol.record_assignment, {'id': identifier, 'route_id': 'route', 'dossier_id': None,
            'role': 'native_math', 'reviewer_id': 'openness-reviewer', 'author_id': 'problem-author',
            'context': context})['result']

    def invoke(self, identifier, output, attempt=None):
        return invoke_codex(self, identifier, output, attempt)

    def read_packet(self, identifier):
        assignment = self.store.snapshot()['records']['review_assignment'][identifier]
        return json.loads(self.artifacts.read(assignment['packet']))

    def read_sent_packet(self, identifier):
        return read_sent_packet(self, 'attempt-' + identifier)

    def copy_theorem(self, version):
        """The theorem of a delivered text as a reviewer copies it, with its white space changed."""
        number = self.versions.index(version) + 1
        return {'version_id': version, 'text': build_theorem(number).replace(' ', '  '),
                'purpose': 'The result that bears on the exact statement.'}

    def build_reading_response(self, packet, excerpts=None, passages=()):
        binding = packet['reading_turn'] if 'reading_turn' in packet else packet['native']
        if excerpts is None:
            excerpts = [self.copy_theorem(row['version_id'])
                        for row in packet.get('reading_turn', {}).get('reading_plan', {}).get('delivered_now', [])]
        return {'schema_version': 1, 'stage': 'reading', 'subject_digest': binding['subject_digest'],
                'claim_digest': binding['claim_digest'], 'reviewer': binding['reviewer'], 'excerpts': list(excerpts),
                'notes': 'Each delivered paper proves a finite extremal example for its own type of sequence. '
                         'None of them settles the exact statement or its neighbours.',
                'passage_requests': list(passages)}

    def build_native_result(self, packet, outcome='supported_open'):
        native = packet['native']
        openness = {'outcome': outcome, 'statement_digest': native['claim_digest'],
                    'evidence': [entry['digest'] for entry in native['evidence']], 'searched_at': '2026-10-15',
                    'coverage': 'The recorded search for the exact statement with bounded coverage.',
                    'reason': 'The three read papers prove other cases and leave the statement open.'}
        if outcome != 'unresolved':
            openness['minimum'] = native['scientific_context']['openness_minimum']
        return {'schema_version': 2, 'subject_digest': native['subject_digest'], 'claim_digest': native['claim_digest'],
                'reviewer': native['reviewer'], 'decision': 'approve',
                'findings': dict({key: 'Assessed the exact supplied subject.' for key in RESULT_FINDINGS},
                                 openness=openness)}

    def run_reading_turns(self, native_packet, first='turn-1', turn_count=None):
        """Assign and observe reading turns until the chain reaches its final turn; return the turn ids. The chain
        must end within turn_count turns, by default one more than the number of texts."""
        turn_count = turn_count or len(self.versions) + 1
        identifiers = [first]
        self.assign(first, native_packet)
        while 'reading_turn' in self.read_packet(identifiers[-1]):
            self.invoke(identifiers[-1], self.build_reading_response(self.read_packet(identifiers[-1])))
            identifiers.append('turn-%d' % (len(identifiers) + 1))
            self.assign(identifiers[-1], native_packet, prior=identifiers[-2])
            self.assertLessEqual(len(identifiers), turn_count, 'The chain must end within its bounded number of turns')
        return identifiers
