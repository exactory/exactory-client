"""Synthetic, local-only calibration cases and their reference assessments."""

import argparse
import json
from pathlib import Path
import unittest


# Each row supplies a separate elementary argument and a checkable counterexample.
# These are authored examples, not attributed publications or open problems.
LOCAL_FAMILIES = (
    ('parity', 'An admissible integer is divisible by 2.',
     'Every admissible integer has the form 2*k for an integer k.',
     'Every admissible integer has the form 2*k+1 for an integer k.',
     'The admissible integer 4 equals 2*2.',
     'The admissible integer 4 equals 2*3.',
     '4 is admissible but cannot equal 2*k+1 for integer k.'),
    ('set-union', 'Admissible sets A and B are finite and disjoint.',
     'For admissible sets, |A union B| = |A| + |B|.',
     'For admissible sets, |A union B| = |A| + |B| - 1.',
     'For A={1} and B={2}, |A union B|=2.',
     'For A={1} and B={2}, |A union B|=3.',
     'A={1}, B={2} are disjoint and their union has size 2, not 1.'),
    ('modular-residue', 'An admissible integer is congruent to 1 modulo 3.',
     'The square of an admissible integer is congruent to 1 modulo 3.',
     'The square of an admissible integer is congruent to 0 modulo 3.',
     'The admissible integer 4 has square 16, which has residue 1 modulo 3.',
     'The admissible integer 4 has square 16, which has residue 0 modulo 3.',
     '4 has residue 1, and 16 also has residue 1 modulo 3.'),
    ('diagonal-matrix', 'An admissible matrix is diag(a,b) with a,b nonzero real numbers.',
     'An admissible matrix has determinant a*b and is invertible.',
     'Every admissible matrix has determinant zero and is singular.',
     'The determinant of diag(2,3) equals 6.',
     'The determinant of diag(2,3) equals 5.',
     'diag(2,3) is admissible and has nonzero determinant 6.'),
    ('regular-graph', 'An admissible graph is finite, simple, and every vertex has degree 2.',
     'In an admissible graph, the number of edges equals the number of vertices.',
     'In an admissible graph, the number of edges is twice the number of vertices.',
     'A triangle has three vertices and three edges.',
     'A triangle has three vertices and six edges.',
     'The triangle is admissible and has 3 edges, not 6.'),
    ('uniform-probability', 'An admissible experiment is uniform on four distinct outcomes.',
     'A single outcome in an admissible experiment has probability 1/4.',
     'A single outcome in an admissible experiment has probability 1/2.',
     'The probability of two designated distinct outcomes is 2/4 = 1/2.',
     'The probability of two designated distinct outcomes is 2/4 = 3/4.',
     'Four equiprobable singleton probabilities sum to 1, so each is 1/4.'),
)

SCOPE_FAMILIES = (
    ('division', 'For real x with x nonzero, x/x = 1.',
     'For every real x, x/x = 1.', 'x=0 makes x/x undefined.',
     'For real x with x nonzero, x/x = 1.'),
    ('polynomial-roots', 'A nonzero real polynomial of degree d has at most d distinct real roots.',
     'Every real polynomial, including the zero polynomial, has finitely many real roots.',
     'The zero polynomial vanishes at every real number.',
     'A nonzero real polynomial of degree d has at most d distinct real roots.'),
    ('strict-metric', 'In a metric space, if x and y are distinct, d(x,y) > 0.',
     'In a metric space, d(x,y) > 0 for all x,y.',
     'Taking x=y gives d(x,x)=0.',
     'In a metric space, if x and y are distinct, d(x,y) > 0.'),
    ('positive-integral', 'A continuous nonnegative function on [0,1] with a positive value has positive integral.',
     'Every continuous nonnegative function on [0,1] has positive integral.',
     'The identically zero function is continuous, nonnegative, and has integral zero.',
     'A continuous nonnegative function on [0,1] with a positive value has positive integral.'),
    ('group-cancellation', 'In a group, if a*b=a*c then b=c.',
     'In any monoid, if a*b=a*c then b=c.',
     'In the multiplicative monoid of integers, 0*1=0*2 but 1 is not 2.',
     'In a group, if a*b=a*c then b=c.'),
    ('sequence-limit', 'A real sequence which converges to a nonzero limit is eventually nonzero.',
     'Every convergent real sequence is eventually nonzero.',
     'The identically zero sequence converges to zero and is never nonzero.',
     'A real sequence which converges to a nonzero limit is eventually nonzero.'),
)


def build_document(identifier, text):
    # Independent, true examples give the planner enough real text to partition.
    # They carry no answer labels, and use line boundaries for exact segmentation.
    appendix = '\n'.join(
        'Auxiliary finite check %d: %d squared is %d; this check concerns only that integer.'
        % (index, index, index * index) for index in range(1, 241))
    return {'id': identifier, 'title': 'Synthetic note ' + identifier,
            'text': text + '\n\n' + appendix + '\n\nReferences: none.'}


def build_span(document, text):
    start = document['text'].index(text)
    return {'document': document['id'], 'start': start, 'end': start + len(text),
            'text': text}


def build_case(family, variant, defect, texts, decisive, explanation):
    documents = [build_document('definition', texts[0]),
                 build_document('argument', texts[1])]
    is_clean = defect is None
    components = {
        'verdict': ['insufficient'],
        'check:problem_openness': ['failed'],
        'check:problem_citations': ['failed' if defect == 'valid_quotation' else 'passed'],
        'rubric:A2': ['fail'],
        'rubric:calibration': ['pass' if is_clean else 'fail'],
    }
    spans = [build_span(documents[index], text) for index, text in decisive]
    return {
        'id': family + '-' + variant, 'family': family, 'variant': variant,
        'defect': defect, 'documents': documents,
        'statement': ('Assess the exact local implication in these two synthetic notes: '
                      + texts[1].split('\n')[0]),
        'ground_truth': {
            'severe': not is_clean, 'components': components,
            'must_move': [] if is_clean else ['rubric:calibration'] + (
                ['check:problem_citations'] if defect == 'valid_quotation' else []),
            'decisive_spans': spans, 'rationale': explanation,
            'required_assessment': 'Assess the supplied implication and distinguish it from an open problem.',
            'invariant': ('The notes are elementary synthetic examples. No actual openness '
                          'search or community significance is established, even in the clean case. '
                          'A complete correct review must therefore decline posting readiness.'),
        },
    }


def build_fixtures():
    cases = []
    for family, definition, lemma, bad_lemma, proof, bad_proof, counter in LOCAL_FAMILIES:
        clean = lemma + '\n' + proof
        cases.append(build_case(family, 'clean', None, (definition, clean),
                                [(0, definition), (1, proof)],
                                'The stated local implication and finite check are correct.'))
        cases.append(build_case(family, 'variant-1', 'within_unit',
                                (definition, lemma + '\n' + bad_proof), [(1, bad_proof)],
                                'The finite calculation is false. ' + counter))
        cases.append(build_case(family, 'variant-2', 'two_units',
                                (definition, bad_lemma), [(0, definition), (1, bad_lemma)],
                                'The lemma contradicts the definition in the other note. ' + counter))
    for family, theorem, unqualified, counter, quote in SCOPE_FAMILIES:
        source = theorem + '\nThe displayed hypothesis is part of the theorem.'
        application = theorem + '\nThe application retains every hypothesis of the source.'
        cases.append(build_case(family, 'clean', None, (source, application),
                                [(0, theorem), (1, theorem)],
                                'The application has exactly the scope of the valid theorem.'))
        cases.append(build_case(family, 'variant-1', 'omitted_condition',
                                (source, unqualified), [(0, theorem), (1, unqualified)],
                                'A required hypothesis is omitted. ' + counter))
        misuse = 'The source states "' + quote + '". Therefore ' + unqualified
        cases.append(build_case(family, 'variant-2', 'valid_quotation',
                                (source, misuse), [(0, quote), (1, misuse)],
                                'The quotation is exact and true, but does not support the application. ' + counter))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(build_fixtures(), indent=2) + '\n', encoding='utf-8')


class FixtureTests(unittest.TestCase):
    def test_families_have_clean_controls_and_localized_defects(self):
        cases = build_fixtures()
        self.assertEqual(len(cases), 36)
        self.assertEqual(len({case['family'] for case in cases}), 12)
        self.assertEqual(sum(case['variant'] == 'clean' for case in cases), 12)
        counts = {}
        for case in cases:
            for span in case['ground_truth']['decisive_spans']:
                document = next(row for row in case['documents'] if row['id'] == span['document'])
                self.assertEqual(document['text'][span['start']:span['end']], span['text'])
            if case['variant'] != 'clean':
                counts[case['defect']] = counts.get(case['defect'], 0) + 1
        self.assertEqual(counts, {'within_unit': 6, 'two_units': 6,
                                  'omitted_condition': 6, 'valid_quotation': 6})

    def test_cross_unit_defects_need_both_distinct_originals(self):
        cases = [case for case in build_fixtures() if case['defect'] == 'two_units']
        for case in cases:
            self.assertEqual(len({span['document'] for span in
                                  case['ground_truth']['decisive_spans']}), 2)

    def test_generation_returns_independent_cases(self):
        cases = build_fixtures()
        cases[0]['documents'][0]['text'] = 'changed'
        self.assertNotEqual(build_fixtures()[0]['documents'][0]['text'], 'changed')


if __name__ == '__main__':
    main()
