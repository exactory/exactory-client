"""Reference scoring of allocated paired cases, including failed executions."""

import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest


REQUIRED_CHECKS = ('problem_statement', 'problem_openness', 'problem_value',
                   'problem_citations', 'stop')
RUBRIC_ITEMS = tuple('A' + str(index) for index in range(1, 8)) + (
    'B1', 'B2', 'priority', 'attempt', 'accessibility', 'calibration')


def read_component(output, component):
    value = output.get('value') if isinstance(output, dict) else None
    if not isinstance(value, dict):
        return None
    if component == 'verdict':
        return value.get('status')
    category, item = component.split(':', 1)
    if category == 'check':
        assurances = value.get('assurances')
        if not isinstance(assurances, list):
            return None
        matching = [row.get('status') for row in assurances
                    if isinstance(row, dict) and row.get('kind') == item]
        return matching[0] if len(matching) == 1 else None
    rubric = value.get('rubric')
    if not isinstance(rubric, dict):
        return None
    if item.startswith('A') and item[1:].isdigit():
        admission = rubric.get('admission')
        return admission.get(item) if isinstance(admission, dict) else None
    if item in ('B1', 'B2'):
        significance = rubric.get('significance')
        return significance.get(item) if isinstance(significance, dict) else None
    return rubric.get(item)


def score_mode(case, mode):
    mode = mode if isinstance(mode, dict) else {}
    output = mode.get('output')
    complete = mode.get('complete') is True and isinstance(output, dict)
    observed = {component: read_component(output, component)
                for component in case['ground_truth']['components']}
    for component in tuple('check:' + check for check in REQUIRED_CHECKS) + tuple(
            'rubric:' + item for item in RUBRIC_ITEMS):
        observed[component] = read_component(output, component)
    components = {component: complete and observed[component] in acceptable
                  for component, acceptable in case['ground_truth']['components'].items()}
    # Required checks left not_assessed are an incomplete scientific assessment,
    # even if the transport and JSON contract both completed.
    complete = complete and all(observed[component] is not None
                                and observed[component] != 'not_assessed'
                                for component in case['ground_truth']['components'])
    required = case['ground_truth'].get('required_checks', REQUIRED_CHECKS)
    complete = complete and all(read_component(output, 'check:' + check) in ('passed', 'failed')
                                for check in required)
    return dict(complete=complete, correct=complete and all(components.values()),
                components=components, observed=observed,
                defect_detected=complete and all(components.get(item, False)
                    for item in case['ground_truth'].get('must_move', [])),
                error=mode.get('error'))


def score_pairs(cases, pairs):
    by_id = {}
    invalid_records = 0
    for pair in pairs:
        if not isinstance(pair, dict) or not isinstance(pair.get('case_id'), str):
            invalid_records += 1
            continue
        if pair['case_id'] in by_id:
            raise ValueError('Duplicate paired case: ' + pair['case_id'])
        by_id[pair['case_id']] = pair
    expected = {case['id'] for case in cases}
    if set(by_id) - expected:
        raise ValueError('Results contain unallocated cases.')
    report = dict(allocated=len(cases), paired_regressions=0, both_mode_misses=0,
                  incomplete=dict(single=0, parts=0), severe_defect_veto=False,
                  full_full=dict(allocated=0, observed=0, different=0, incomplete=0),
                  components={}, families={}, cases=[],
                  invalid_result_records=invalid_records,
                  dry_run=all(pair.get('dry_run', False) for pair in by_id.values()) if by_id else None)
    for case in cases:
        pair = by_id.get(case['id'], {})
        single = score_mode(case, pair.get('single'))
        parts = score_mode(case, pair.get('parts'))
        regressed = [component for component in single['components']
                     if single['components'][component] and not parts['components'][component]]
        regression = bool(regressed) or (single['correct'] and not parts['complete'])
        both_miss = not single['correct'] and not parts['correct']
        targets = case['ground_truth'].get('must_move')
        if targets is None:
            targets = [key for key in case['ground_truth']['components'] if key != 'verdict']
        severe_miss = case['ground_truth']['severe'] and any(
            not mode['complete'] or not all(mode['components'].get(key, False) for key in targets)
            for mode in (single, parts))
        report['paired_regressions'] += int(regression)
        report['both_mode_misses'] += int(both_miss)
        report['severe_defect_veto'] |= severe_miss
        family = report['families'].setdefault(case['family'], dict(
            allocated=0, paired_regressions=0, both_mode_misses=0,
            single_incomplete=0, parts_incomplete=0, severe_misses=0, parts_turns=0))
        family['allocated'] += 1
        family['paired_regressions'] += int(regression)
        family['both_mode_misses'] += int(both_miss)
        family['severe_misses'] += int(severe_miss)
        parts_payload = pair.get('parts')
        turns = parts_payload.get('turns') if isinstance(parts_payload, dict) else None
        family['parts_turns'] += len(turns) if isinstance(turns, list) else 0
        for name, mode in (('single', single), ('parts', parts)):
            report['incomplete'][name] += int(not mode['complete'])
            family[name + '_incomplete'] += int(not mode['complete'])
            for component, correct in mode['components'].items():
                counts = report['components'].setdefault(component, dict(single_misses=0, parts_misses=0,
                                                                         regressions=0))
                counts[name + '_misses'] += int(not correct)
        for component in regressed:
            report['components'][component]['regressions'] += 1
        repeats = []
        if case['variant'] == 'clean':
            for index in range(2):
                repeat_modes = pair.get('repeats')
                repeat_modes = repeat_modes if isinstance(repeat_modes, list) else []
                repeat = score_mode(case, repeat_modes[index] if index < len(repeat_modes) else None)
                report['full_full']['allocated'] += 1
                if single['complete'] and repeat['complete']:
                    report['full_full']['observed'] += 1
                    report['full_full']['different'] += int(single['observed'] != repeat['observed'])
                else:
                    report['full_full']['incomplete'] += 1
                repeats.append(repeat)
        report['cases'].append(dict(case_id=case['id'], family=case['family'], defect=case['defect'],
                                    single=single, parts=parts, repeats=repeats,
                                    regressed_components=regressed, regression=regression,
                                    both_mode_miss=both_miss, severe_miss=severe_miss))
    return report


def render_report(report):
    label = ('Deterministic dry run. These scores validate tooling only; they do not measure model quality.'
             if report['dry_run'] else
             'Reference component scoring. Two blinded human assessments of reasoning remain required.')
    lines = ['# Option A measurement report', '', label, '',
             'Allocated paired cases: %d.' % report['allocated'],
             'Paired regressions: %d. Both-mode misses: %d.' % (
                 report['paired_regressions'], report['both_mode_misses']),
             'Incomplete: single %d; parts %d.' % (
                 report['incomplete']['single'], report['incomplete']['parts']),
             'Severe planted-defect veto: %s.' % ('yes' if report['severe_defect_veto'] else 'no'),
             'Ordinary repeat comparisons: %(observed)d/%(allocated)d; different %(different)d; '
             'incomplete %(incomplete)d.' % report['full_full'], '',
             '| Family | Cases | Regressions | Both miss | Single incomplete | '
             'Parts incomplete | Severe misses | Parts turns |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for family, row in sorted(report['families'].items()):
        lines.append('| %s | %d | %d | %d | %d | %d | %d | %d |' % (
            family, row['allocated'], row['paired_regressions'], row['both_mode_misses'],
            row['single_incomplete'], row['parts_incomplete'], row['severe_misses'], row['parts_turns']))
    lines += ['', '| Component | Single misses | Parts misses | Regressions |',
              '| --- | ---: | ---: | ---: |']
    for component, row in sorted(report['components'].items()):
        lines.append('| %s | %d | %d | %d |' % (
            component, row['single_misses'], row['parts_misses'], row['regressions']))
    lines += ['', 'Families are the clusters. The 36 variants are not 36 independent confirmatory fixtures.',
              'All allocations remain in the denominator, including absent or invalid result files.',
              'The composite counts any reference component correct in single input and wrong in parts.',
              'A completed unresolved scientific conclusion is scored against its reference value;',
              'execution incompleteness is always reported separately.', '',
              'Verdict, specified rubric items and checks are automatic. Exact spans and rationales in fixtures.json',
              'support human assessment of scope, objections, unsupported conclusions and reconciliation.',
              'Automatic scores do not certify the reasoning or establish universal quality equivalence.', '']
    return '\n'.join(lines)


def read_result_file(path):
    try:
        data = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {'load_error': 'invalid_json'}


def read_pairs(directory, cases):
    pairs = []
    for case in cases:
        prefix = directory / 'pairs' / case['id']
        saved = read_result_file(prefix.with_suffix('.json'))
        pair = dict(saved) if isinstance(saved, dict) else {}
        if pair.get('case_id', case['id']) != case['id']:
            raise ValueError('Result case ID differs from its allocated filename.')
        pair['case_id'] = case['id']
        for mode in ('single', 'parts'):
            if not isinstance(pair.get(mode), dict):
                pair[mode] = read_result_file(Path(str(prefix) + '.' + mode + '.json'))
        repeats = pair.get('repeats')
        repeats = repeats if isinstance(repeats, list) else []
        if case['variant'] == 'clean':
            repeats += [read_result_file(Path(str(prefix) + '.repeat-' + str(index + 1) + '.json'))
                        for index in range(len(repeats), 2)]
        pair['repeats'] = repeats
        pairs.append(pair)
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    cases = json.loads((args.directory / 'fixtures.json').read_text(encoding='utf-8'))
    pairs = read_pairs(args.directory, cases)
    report = score_pairs(cases, pairs)
    manifest = read_result_file(args.directory / 'manifest.json')
    if isinstance(manifest, dict) and isinstance(manifest.get('dry_run'), bool):
        report['dry_run'] = manifest['dry_run']
    (args.directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (args.directory / 'report.md').write_text(render_report(report), encoding='utf-8')
    print(render_report(report))


class ScoringTests(unittest.TestCase):
    def make_case(self):
        return {'id': 'test-variant', 'family': 'test', 'variant': 'variant-1',
                'defect': 'two_units', 'ground_truth': {
                    'required_checks': ['problem_statement', 'problem_openness',
                                        'problem_value', 'problem_citations', 'stop'],
                    'severe': True, 'components': {'verdict': ['insufficient'],
                    'check:problem_value': ['failed'], 'rubric:calibration': ['fail']},
                    'decisive_spans': [], 'rationale': 'The two definitions conflict.'}}

    def make_mode(self):
        return {'complete': True, 'output': {'stage': 'result', 'value': {
            'status': 'insufficient', 'assurances': [
                {'kind': 'problem_value', 'status': 'failed'}] + [
                    {'kind': check, 'status': 'passed'} for check in (
                        'problem_statement', 'problem_openness', 'problem_citations', 'stop')],
            'rubric': {'calibration': 'fail'}}}}

    def test_incomplete_parts_counts_against_a_correct_single(self):
        case = self.make_case()
        pair = {'case_id': case['id'], 'single': self.make_mode(),
                'parts': {'complete': False, 'output': None}, 'repeats': []}
        report = score_pairs([case], [pair])
        self.assertEqual(report['allocated'], 1)
        self.assertEqual(report['paired_regressions'], 1)
        self.assertEqual(report['incomplete']['parts'], 1)

    def test_both_mode_severe_miss_vetoes_even_without_regression(self):
        case = self.make_case()
        mode = self.make_mode()
        mode['output']['value']['assurances'][0]['status'] = 'passed'
        pair = {'case_id': case['id'], 'single': mode, 'parts': mode, 'repeats': []}
        report = score_pairs([case], [pair])
        self.assertEqual(report['both_mode_misses'], 1)
        self.assertEqual(report['paired_regressions'], 0)
        self.assertTrue(report['severe_defect_veto'])

    def test_missing_allocations_are_incomplete_and_never_dropped(self):
        report = score_pairs([self.make_case()], [])
        self.assertEqual(report['allocated'], 1)
        self.assertEqual(report['incomplete'], {'single': 1, 'parts': 1})

    def test_component_regression_survives_other_single_mode_errors(self):
        case = self.make_case()
        single = self.make_mode()
        single['output']['value']['status'] = 'sufficient'
        parts = self.make_mode()
        parts['output']['value']['status'] = 'sufficient'
        parts['output']['value']['rubric']['calibration'] = 'pass'
        report = score_pairs([case], [{'case_id': case['id'], 'single': single,
                                      'parts': parts, 'repeats': []}])
        self.assertEqual(report['paired_regressions'], 1)

    def test_full_full_variability_counts_different_component_results(self):
        case = self.make_case()
        case['variant'] = 'clean'
        repeat = copy.deepcopy(self.make_mode())
        repeat['output']['value']['rubric']['calibration'] = 'pass'
        report = score_pairs([case], [{'case_id': case['id'], 'single': self.make_mode(),
                                      'parts': self.make_mode(), 'repeats': [repeat]}])
        self.assertEqual(report['full_full']['different'], 1)

    def test_full_full_variability_includes_rubric_grades_without_a_gold_grade(self):
        case = self.make_case()
        case['variant'] = 'clean'
        single = self.make_mode()
        single['output']['value']['rubric']['priority'] = 2
        repeat = copy.deepcopy(single)
        repeat['output']['value']['rubric']['priority'] = 1
        report = score_pairs([case], [dict(case_id=case['id'], single=single,
                                          parts=single, repeats=[repeat])])
        self.assertEqual(report['full_full']['different'], 1)

    def test_justified_unresolved_answer_is_a_complete_scientific_outcome(self):
        case = self.make_case()
        case['ground_truth']['components']['verdict'] = ['unresolved']
        mode = self.make_mode()
        mode['output']['value']['status'] = 'unresolved'
        self.assertTrue(score_mode(case, mode)['correct'])
        self.assertTrue(score_mode(case, mode)['complete'])

    def test_invalid_output_shapes_are_incomplete(self):
        case = self.make_case()
        for output in ([], ['invalid'], {'value': ['invalid']},
                       {'value': {'assurances': [None], 'rubric': []}}):
            with self.subTest(output=output):
                result = score_mode(case, dict(complete=True, output=output))
                self.assertFalse(result['complete'])
                self.assertFalse(result['correct'])

    def test_duplicate_case_results_are_refused(self):
        case = self.make_case()
        pair = dict(case_id=case['id'])
        with self.assertRaises(ValueError):
            score_pairs([case], [pair, pair])

    def test_unscored_required_check_still_requires_an_assessment(self):
        case = self.make_case()
        for status in ('not_assessed', None):
            mode = self.make_mode()
            mode['output']['value']['assurances'][1]['status'] = status
            self.assertFalse(score_mode(case, mode)['complete'])

    def test_duplicate_required_assurance_is_incomplete(self):
        mode = self.make_mode()
        mode['output']['value']['assurances'].append(
            copy.deepcopy(mode['output']['value']['assurances'][0]))
        self.assertFalse(score_mode(self.make_case(), mode)['complete'])

    def test_structurally_invalid_result_records_remain_allocated(self):
        case = self.make_case()
        for record in (None, [], {}, {'case_id': case['id'], 'single': ['invalid']}):
            with self.subTest(record=record):
                report = score_pairs([case], [record])
                self.assertEqual(report['allocated'], 1)
                self.assertEqual(report['incomplete'], {'single': 1, 'parts': 1})

    def test_scorer_recovers_checkpoint_after_pair_interruption(self):
        case = self.make_case()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            (root / 'pairs').mkdir()
            checkpoint = root / 'pairs' / (case['id'] + '.parts.json')
            checkpoint.write_text(json.dumps(self.make_mode()), encoding='utf-8')
            pairs = read_pairs(root, [case])
            report = score_pairs([case], pairs)
        self.assertEqual(report['incomplete'], {'single': 1, 'parts': 0})


if __name__ == '__main__':
    main()
