"""Synthetic probe provenance and the binding/delivery comparison for T5."""

import copy

from research_harness import problem_evidence
from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome
from test_research_problem_forming import build_outcome


def identify_account_record(entry):
    cite = entry['cite']
    return cite.get('origin'), cite['record_kind'], cite['id']


def assert_account_binding_delivery(test, binding, delivered):
    """Compare exact entries, preserving native order and each imported origin."""
    native = [entry for entry in delivered['account_records'] if 'origin' not in entry['cite']]
    imported = [entry for entry in delivered['account_records'] if 'origin' in entry['cite']]
    expected = [entry for row in binding.get('forming_imports', []) for entry in row['records']]
    for actual, wanted in ((native, binding['account_records']), (imported, expected)):
        test.assertEqual([identify_account_record(entry) for entry in actual],
                         [identify_account_record(entry) for entry in wanted])
        test.assertEqual(actual, wanted)
        test.assertEqual(len(actual), len({identify_account_record(entry) for entry in actual}))


def build_probe_check(identifier='check-probe', probe_id='probe-check'):
    return dict(id=identifier, target=dict(kind='instance', description='A bounded example.'),
        contradiction='The example exceeds the bound.', no_contradiction_reason=None,
        result='The example agrees.', standing='observation', passed=False, alternative_id=None,
        date='unknown', evidence=[dict(kind='probe', id=probe_id)], evidence_ids=[], independence=None)


def build_route_records(case):
    """Each of the eight probes is reached by exactly one delivery route."""
    case.record()
    records = case.store.snapshot()['records']
    value = records['problem_value']['value-1']
    accounts = value['accounts']
    plan_revision = records['cycle_plan']['cycle-1']['planned_revision']
    for route in ('check', 'call', 'place', 'input', 'ground', 'outcome', 'refuted', 'earlier'):
        identifier = 'probe-' + route
        revision = plan_revision - 1 if route == 'earlier' else case.store.revision + 1
        intent = dict(build_probe_intent(case.artifacts, identifier), revision=revision)
        intent['question'] = 'What is the bounded quantity for ' + route + '?'
        probe = dict(build_probe_outcome(case.artifacts, identifier), revision=revision)
        records.setdefault('probe_intent', {})[identifier] = intent
        records.setdefault('probe', {})[identifier] = probe
    def cite_probe(route):
        return problem_evidence.cite_record('probe', 'probe-' + route, records['probe']['probe-' + route])

    accounts['checks'].append(build_probe_check())
    accounts['refuted_conjectures'] = [dict(id='refuted', statement='A stronger finite bound.',
        identified_by='author', sources=[cite_probe('refuted')], evidence_ids=['use'])]
    accounts['replacement'] = [dict(id='replacement', refuted_id='refuted', form_id=None,
        evidence_ids=['use'], account='The admitted run refutes the stronger statement.')]
    call = records['forming_call']['forming-1']
    call['probes'] = [dict(probe_id='probe-call', form_id='form-1', role='origin')]
    call['question']['places'] = [dict(description='A finite setting.', evidence=[cite_probe('place')])]
    call['inputs'] = [dict(description='Earlier values.', evidence=[cite_probe('input')])]
    call['forms'][0]['grounds'][0]['evidence'] = [cite_probe('ground')]
    outcome = dict(build_outcome(), recorded_revision=case.store.revision + 1,
        evidence=[dict(kind='observation', description='A finite pattern.', standing='exploratory',
            date='2026-11-01', reference=dict(kind='probe', id='probe-outcome'), counterexample=None)])
    records['form_outcome'] = {outcome['id']: outcome}
    records['problem_candidate']['candidate-history']['source']['evidence'][0] = (
        problem_evidence.cite_record('forming_call', call['id'], call))
    return records


def build_legacy_probe_records(case):
    records = build_route_records(case)
    value = records['problem_value']['value-1']
    value.pop('accounts_version')
    # The base's two existing routes, with intents present but undelivered.
    value['accounts']['checks'] = value['accounts']['checks'][:1]
    call = records['forming_call']['forming-1']
    call['probes'] = []
    call['question']['places'] = []
    call['inputs'] = []
    call['forms'][0]['grounds'][0]['evidence'] = []
    records['form_outcome'] = {}
    records['problem_candidate']['candidate-history']['source']['evidence'][0] = (
        problem_evidence.cite_record('forming_call', call['id'], call))
    records['probe'] = {key: row for key, row in records['probe'].items()
                        if key in ('probe-refuted', 'probe-earlier')}
    records['probe_intent'] = {key: row for key, row in records['probe_intent'].items()
                               if key in records['probe']}
    return copy.deepcopy(records)
