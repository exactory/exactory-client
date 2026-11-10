"""Derive the checks and forming history that accompany an account-bearing value."""

from .evidence import digest
from .graph import obligation
from .problem_accounts import collect_candidate_forms, collect_entries, collect_entry_evidence
from .problem_claims import compute_statement_digest


def collect_statement_lineage(records, statement):
    lineage = []
    while statement is not None:
        lineage.append(statement)
        statement = records.get('problem_statement', {}).get(statement.get('revises_statement_id'))
    return lineage


def collect_form_history(records, statement):
    forms = {form['id']: (call, index, form) for call in records.get('forming_call', {}).values()
             for index, form in enumerate(call['forms'])}
    pending = list(collect_candidate_forms(records, statement)) if statement is not None else []
    selected = set()
    while pending:
        identifier = pending.pop()
        if identifier in selected:
            continue
        selected.add(identifier)
        previous = forms[identifier][2]['previous']
        if previous is not None:
            pending.append(previous['form_id'])
    history = []
    for call, _, form in sorted((forms[identifier] for identifier in selected),
                                key=lambda row: (row[0]['recorded_revision'], row[1])):
        latest = {}
        for outcome in sorted(records.get('form_outcome', {}).values(), key=lambda row: row['recorded_revision']):
            if outcome['form_id'] == form['id']:
                latest[(outcome['target']['kind'], outcome['target']['id'])] = outcome
        history.append(dict(form=form, forming_call_id=call['id'], outcomes=list(latest.values())))
    return history


def describe_probe_computation(records, row):
    question = row['question'] if 'question' in row else records['probe_intent'][row['id']]['question']
    return dict(kind='probe', id=row['id'], question=question, revision=row['revision'])


def describe_earlier_computations(records, plan):
    earlier = [describe_probe_computation(records, row)
               for row in records.get('probe', {}).values() if row['revision'] < plan['planned_revision']]
    for row in records.get('execution', {}).values():
        if row['recorded_revision'] < plan['planned_revision']:
            earlier_plan = records['cycle_plan'][row['payload']['cycle_id']]
            earlier.append(dict(kind='execution', id=row['id'], question=earlier_plan['payload']['question'],
                                revision=row['recorded_revision']))
    from .problem_forming_import import collect_inherited
    for origin, imported in collect_inherited(records).items():
        earlier.extend(dict({key: value for key, value in row.items() if key != 'imported_revision'}, origin=origin)
                       for row in imported['computations'] if row['imported_revision'] < plan['planned_revision'])
    return sorted(earlier, key=lambda row: (row.get('origin', ''), row['revision'], row['kind'], row['id']))


def describe_run_checks(records, accounts, lineage):
    from .development import compute_plan_statement_digest
    digests = {compute_statement_digest(statement) for statement in lineage}
    current_digest = compute_statement_digest(lineage[0]) if lineage else None
    entries = {entry['id']: entry for entry in collect_entries(accounts)}
    tests = {}
    for check in accounts.get('checks', []):
        if check['standing'] == 'test':
            for reference in collect_entry_evidence(entries, check['id']):
                if reference['kind'] == 'computation':
                    tests.setdefault(reference['execution_id'], []).append(check['id'])
    checks = dict(current=[], earlier=[])
    for execution in sorted(records.get('execution', {}).values(), key=lambda row: (row['recorded_revision'], row['id'])):
        plan = records['cycle_plan'][execution['payload']['cycle_id']]
        statement_digest = compute_plan_statement_digest(records, plan)
        if statement_digest not in digests:
            continue
        statement = records['problem_statement'][plan['payload']['objective']['id']]
        assessment = max((row for row in records.get('cycle_assessment', {}).values()
                          if execution['id'] in row['payload']['execution_ids']),
                         key=lambda row: row['assessed_revision'], default=None)
        failures = {} if assessment is None else {row['signal_id']: row['status'] for row in assessment['payload']['failures']}
        row = dict(execution_id=execution['id'], plan_id=plan['payload']['id'], statement_id=statement['id'],
                   statement_digest=statement_digest, statement=statement['statement'],
                   resolution_criteria=statement['resolution_criteria'], question=plan['payload']['question'],
                   assessment_id=None if assessment is None else assessment['payload']['id'],
                   failure_signals=[dict(signal, status=failures.get(signal['id'], 'unresolved'))
                                    for signal in plan['payload']['failure_signals']],
                   test_ids=sorted(set(tests.get(execution['id'], []))))
        if row['test_ids']:
            row['earlier_computations'] = describe_earlier_computations(records, plan)
        checks['current' if statement_digest == current_digest else 'earlier'].append(row)
    return checks


def compute_history_counts(records, history, checks):
    form_ids = {row['form']['id'] for row in history}
    calls = [call for call in records.get('forming_call', {}).values()
             if any(form['id'] in form_ids for form in call['forms'])
             or any(probe['form_id'] in form_ids for probe in call['probes'])]
    probes = {probe['probe_id'] for call in calls for probe in call['probes']
              if probe['form_id'] is None or probe['form_id'] in form_ids}
    runs = {row['execution_id'] for row in checks['current'] + checks['earlier']}
    for call in calls:
        grounds = call['question']['places'] + call['inputs']
        grounds += [ground for form in call['forms'] if form['id'] in form_ids for ground in form['grounds']]
        for ground in grounds:
            for reference in ground['evidence']:
                if reference.get('kind') == 'record':
                    if reference['record_kind'] == 'probe':
                        probes.add(reference['id'])
                    elif reference['record_kind'] == 'execution':
                        runs.add(reference['id'])
        runs.update(check['execution_id'] for form in call['forms'] if form['id'] in form_ids
                    for check in form['correction_checks'] if 'execution_id' in check)
    # All attempts count, including evidence that a later outcome superseded.
    for outcome in records.get('form_outcome', {}).values():
        if outcome['form_id'] in form_ids:
            for item in outcome['evidence']:
                reference = item['reference']
                if reference['kind'] == 'probe':
                    probes.add(reference['id'])
                elif reference['kind'] == 'computation':
                    runs.add(reference['execution_id'])
    return dict(lineage=dict(forming_calls=len(calls), forms=len(form_ids),
                            recorded_comparisons=sum(call['comparison_count'] or 0 for call in calls),
                            probes=len(probes), runs=len(runs)),
                exploration=dict(forming_calls=len(records.get('forming_call', {})),
                                 forms=sum(len(call['forms']) for call in records.get('forming_call', {}).values()),
                                 recorded_comparisons=sum(call['comparison_count'] or 0 for call in records.get('forming_call', {}).values()),
                                 probes=len(records.get('probe', {})), runs=len(records.get('execution', {}))))


def describe_account_derivations(records, value):
    statement = records.get('problem_statement', {}).get(value['statement_id'])
    history = collect_form_history(records, statement)
    checks = describe_run_checks(records, value['accounts'], collect_statement_lineage(records, statement))
    derived = dict(checks=checks, history=history, counts=compute_history_counts(records, history, checks))
    from .problem_forming_import import collect_inherited
    inherited = collect_inherited(records)
    if inherited:
        derived['inherited'] = [dict(origin=origin, source_revision=row['source_revision'], history=row['history'],
                                    checks=row['checks'], counts=row['counts']) for origin, row in sorted(inherited.items())]
    return derived


def assess_account_currency(records, value):
    """Name all later evidence in one obligation; historical values have no new contract."""
    if 'accounts' not in value:
        return []
    from .problem_evidence import collect_value_items
    from .problem_attempt import find_latest_native_attempt
    derived = describe_account_derivations(records, value)
    revision = value['recorded_revision']
    changes = [dict(kind='execution', id=row['execution_id'], reason='later_run_on_current_text')
               for row in derived['checks']['current']
               if records['execution'][row['execution_id']]['recorded_revision'] > revision]
    form_ids = {row['form']['id'] for row in derived['history']}
    changes.extend(dict(kind='form_outcome', id=row['id'], reason='later_outcome_in_history')
                   for row in sorted(records.get('form_outcome', {}).values(), key=lambda row: (row['recorded_revision'], row['id']))
                   if row['form_id'] in form_ids and row['recorded_revision'] > revision)
    for reference in collect_value_items(value):
        if reference['kind'] != 'result':
            continue
        identifier = reference.get('native_attempt_digest')
        if identifier is None:
            identifier = records['problem_attempt'][reference['attempt_id']]['solver']['native_attempt_digest']
        native = records['native_attempt'][identifier]
        latest, _ = find_latest_native_attempt(records, native)
        if latest is None or latest == identifier:
            continue
        pointer = records['native_attempt_current'][digest({name: native[name] for name in ('attack_root', 'objective_id')})]
        if pointer['recorded_revision'] > revision:
            changes.append(dict(kind='native_attempt', id=latest, reason='later_snapshot_of_cited_objective',
                                previous_native_attempt_digest=identifier))
    if not changes:
        return []
    return [obligation('problem_value_stale', 'Record a new problem-value record. changes lists each later run on the current text, each '
                       'later outcome of a form in the history and each later delivered snapshot of a cited objective. '
                       'Enter each change in the accounts, and cite the latest snapshot of each objective.',
                       value_id=value['id'], changes=list({digest(row): row for row in changes}.values()))]
