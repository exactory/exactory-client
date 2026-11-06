"""Run ordinary and process-locally forced parts v2 result reviews."""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager, nullcontext
import copy
from html import escape
import json
import multiprocessing
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.review_measurement.campaign import (
    PaidBudget, confine_temporary_files, write_json,
)


def load_harness(plugin_root):
    sys.dont_write_bytecode = True
    plugin_root = Path(plugin_root).resolve()
    if not (plugin_root / 'research_harness' / 'review_parts.py').is_file():
        raise ValueError('Choose the parts v2 plugin worktree with --plugin-root.')
    sys.path.insert(0, str(plugin_root))
    from research_harness import review_parts
    if review_parts.PROTOCOL != 'exactory-review-parts-v2':
        raise ValueError('This runner requires parts v2.')


def require_paid_prompts():
    from research_harness import review_parts_prompts
    names = ('PARTS_COMMON', 'READ', 'COMBINE', 'CHECK', 'FINAL', 'FINAL_ANSWER',
             'FINAL_RESULT', 'TARGET_LABELS', 'PARTS_NOTE', 'SEGMENT_NOTE')
    unfinished = [name for name in names if getattr(review_parts_prompts, name).startswith('PLACEHOLDER:')]
    if unfinished:
        raise ValueError('Paid measurement requires integrated reviewer texts: ' + ', '.join(unfinished))



def build_schedule(cases, seed):
    generator = random.Random(seed)
    schedule = []
    for case in cases:
        schedule.append({'case_id': case['id'],
                         'first_mode': generator.choice(('single', 'parts')),
                         'partition_seed': generator.randrange(2 ** 31),
                         'extra_single_runs': 2 if case['variant'] == 'clean' else 0})
    generator.shuffle(schedule)
    return schedule


@contextmanager
def preserve_temporary_workspace(output_dir, result):
    root = Path(tempfile.mkdtemp(prefix='measurement-', dir=str(output_dir)))
    finished = False
    try:
        yield root
        finished = True
    finally:
        if finished:
            shutil.rmtree(root)
        else:
            result['retained_workspace'] = str(root)
            write_json(Path(output_dir) / (result['case_id'] + '.json'), result)


class Workspace:
    """Dispatch the supported CLI commands in process; never seed store rows."""

    def __init__(self, root):
        from research_harness.storage import Store
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root, create=True)
        self.artifacts = self.store.build_artifact_store()
        self.sequence = self.store.revision

    def execute(self, command, payload):
        from research_harness import cli
        self.sequence += 1
        payload_file = self.root / 'command.json'
        write_json(payload_file, payload)
        args = argparse.Namespace(command=command, workspace=str(self.root),
                                  file=str(payload_file), check=False, preview=False,
                                  expected_revision=self.store.revision,
                                  request_id='measurement-' + str(self.sequence))
        return cli.run(args)

    def read_records(self):
        return self.store.snapshot()['records']

    def read_json(self, reference):
        return None if reference is None else json.loads(self.artifacts.read(reference))


class FixtureHTTP:
    """An explicitly synthetic acquisition boundary, used in both run modes."""

    def __init__(self, body):
        self.body = body

    def get(self, url, **kwargs):
        from research_harness.http import Fetch
        headers = {'content-type': 'text/html'}
        attempt = dict(url=url, captured_at='2026-11-03T00:00:00Z', status=200,
                       headers=headers, body=self.body, complete=True, error=None)
        return Fetch(url, 200, headers, self.body, [attempt])


def record_document(workspace, document):
    from research_harness import acquisition
    from research_harness.reading import NOTE_FIELDS
    identifier = 'doi:10.99999/measurement.' + document['id']
    response = workspace.root / (document['id'] + '.json')
    write_json(response, {'id': identifier, 'title': document['title']})
    workspace.execute('import-response', {
        'provider': 'mcp', 'response_file': str(response),
        'source_url': 'https://measurement.invalid/metadata/' + document['id'],
        'captured_at': '2026-11-03T00:00:00Z', 'media_type': 'application/json',
        'mappings': [{'id': '/id', 'title': '/title'}]})
    body = ('<article><section class="article-body"><p>'
            + escape(document['text']) + '</p></section></article>').encode('utf-8')
    with patch.object(acquisition, 'HttpClient', return_value=FixtureHTTP(body)):
        capture = workspace.execute('fulltext', {
            'identifier': identifier,
            'url': 'https://measurement.invalid/text/' + document['id']})['capture']
    source_text = workspace.artifacts.read(capture['text']).decode('utf-8')
    link = dict(version_id=identifier, source_id=capture['source_id'],
                artifact=capture['text'], locator=dict(
                    kind='text', start=0, end=len(source_text), quote=source_text))
    bibliography = 'References: none.'
    start = source_text.index(bibliography)
    bibliography_link = dict(link, locator=dict(kind='text', start=start,
                                               end=start + len(bibliography), quote=bibliography))
    units = [dict(id='body', kind='text', required=True, link=link),
             dict(id='bibliography', kind='bibliography', required=True, link=bibliography_link)]
    workspace.execute('bundle', dict(
        id='bundle-' + document['id'], version_id=identifier, source_id=capture['source_id'],
        scope='article', completeness='complete', units=units,
        inventory=dict(text='The two listed units cover this synthetic note.', links=[link]),
        bibliography=dict(complete=True, unit_id='bibliography', entries=[]), resolutions=[]))
    notes = {field: dict(text='This synthetic note supplies the local ' + field + '.',
                         status='present', inspections=[0]) for field in NOTE_FIELDS}
    # The review packet ordinarily withholds full text. Preserve the mathematical
    # example verbatim as reading notes so both modes receive identical originals.
    notes['claims']['text'] = document['text']
    workspace.execute('read', dict(
        id='read-' + document['id'], version_id=identifier, depth='fulltext',
        bundle_id='bundle-' + document['id'], notes=notes,
        inspections=[dict(unit_id=unit['id'], link=unit['link'],
                          note='Inspected this complete synthetic unit.') for unit in units]))
    return dict(kind='reading', version_id=identifier, reading_id='read-' + document['id'])


def build_grades(case):
    grades = dict(admission={('A' + str(index)): 'pass' for index in range(1, 8)},
                  significance=dict(B1=0, B2=1), priority=1, attempt='pass',
                  accessibility='E-F', calibration='pass')
    grades['admission']['A2'] = 'fail'
    grades['calibration'] = case['ground_truth']['components']['rubric:calibration'][0]
    return grades


def build_final_output(case, references, bars, stage='result'):
    from research_harness.problem_rubric import ITEMS, PROBLEM_CHECKS
    truth = case['ground_truth']
    reason = truth['rationale'] + ' ' + truth['invariant'] + ' Decisive originals: ' + ' | '.join(
        span['text'] for span in truth['decisive_spans'])
    value = dict(status='insufficient', consequence='Only the stated local mathematical implication is assessed.',
                 bar_ids=bars, objective_adequacy='inadequate', method_adequacy='adequate',
                 reason=reason, evidence=copy.deepcopy(references), work_items=[],
                 assurances=[], objection_findings=[])
    if stage == 'bar':
        value.update(status='sufficient', bar_ids=[], objective_adequacy='not_assessed',
                     method_adequacy='not_assessed',
                     reason='Synthetic calibration: exact scope and justified implications are required.')
    else:
        for check in PROBLEM_CHECKS:
            status = truth['components'].get('check:' + check, [
                'failed' if check in ('problem_value', 'stop') else 'passed'])[0]
            value['assurances'].append(dict(kind=check, status=status, reason=reason,
                                            evidence=copy.deepcopy(references)))
        value['rubric'] = build_grades(case)
        value['rubric_evidence'] = {item: reason for item in ITEMS}
    return dict(stage=stage, support=None, value=value, objections=[],
                limitations=['Synthetic fixture; no real-world openness or significance is certified.'])


def build_turn_output(case, packet, final_output):
    header = packet.get('review_parts')
    if header is None or header['kind'] == 'final':
        return copy.deepcopy(final_output)
    kind, turn = header['kind'], header['turn']
    if kind == 'read':
        from research_harness.review_parts import read_delivered_items
        delivered_items = read_delivered_items(packet)
        cards = []
        for unit in header['units']:
            if unit['handle'] not in header['delivered_units'] or unit.get('carded_under'):
                continue
            text = json.dumps(delivered_items.get(unit['handle'], {}), ensure_ascii=False)
            text += ''.join(entry.get('content', '') for entry in packet.get('evidence', [])
                            if entry.get('unit') == unit['handle'] and entry.get('encoding') == 'utf-8')
            quote = ('Auxiliary finite check 1: 1 squared is 1; '
                     'this check concerns only that integer.')
            statements = [dict(text=quote, why='Exercise exact statement verification.')] if quote in text else []
            cards.append(dict(unit=unit['handle'], summary='A synthetic mathematical original.',
                              bearing=[], statements=statements, depends_on='', questions=''))
        return dict(kind=kind, turn=turn, cards=cards,
                    unborne=[dict(target=row['id'], reason='Dry stand-in has no scientific judgment.')
                             for row in header['targets']])
    if kind == 'combine':
        # Exercise a genuine joint original check even when the stand-in does not
        # infer a defect. Exact item spans make the combination fit a small turn.
        cards = packet['review_parts_state']['cards']
        units = list(dict.fromkeys(card['unit'] for card in cards))[:2]
        reads = [dict(unit=unit, item=True, start=0, end=100) for unit in units]
        return dict(kind=kind, turn=turn,
                    combinations=[dict(units=units, question='Check these originals together.',
                                       view='Synthetic joint check.', read=reads)] if units else [],
                    rest='The stand-in selects only these originals for its plumbing check.')
    return dict(kind=kind, turn=turn, findings=[dict(
        combination=row['id'], finding='Deterministic plumbing check completed.',
        effect='unresolved', statements=[])
        for row in packet['review_parts_state']['combinations']
        if row['id'] in header['check_combinations']])


def build_synthetic_response(route, request, output):
    from research_harness import review_claude_transport as claude
    from research_harness.evidence import digest
    text = json.dumps(output, ensure_ascii=False)
    session = 'measurement-stand-in'
    usage = dict(input_tokens=100, output_tokens=900, cost_usd_estimate=0)
    events = [dict(type='system', subtype='init', session_id=session, model=route['model'],
                   tools=[], mcp_servers=[], skills=[], slash_commands=[]),
              dict(type='user', isReplay=True, session_id=session, message=dict(
                  role='user', content=[dict(type='text', text=claude.packet_text(request))])),
              dict(type='assistant', session_id=session, message=dict(
                  role='assistant', content=[dict(type='text', text=text)])),
              dict(type='result', subtype='success', is_error=False, num_turns=1,
                   session_id=session, result=text, total_cost_usd=0,
                   usage=dict(input_tokens=100, cache_creation_input_tokens=0,
                              cache_read_input_tokens=0, output_tokens=900))]
    evidence = claude.inspect_stream(events, request, route, 'EXCLUDED_SYNTHETIC_CONTROL')
    evidence.update(inspected=True, runtime=claude.runtime(), route_digest=digest(route),
                    cli_version='synthetic-no-cli')
    return dict(id=session, model=route['model'], status='completed', usage=usage,
                output=[dict(type='message', role='assistant', content=[dict(type='output_text', text=text)])],
                context_evidence=evidence,
                execution=dict(event_count=len(events), event_types=[row['type'] for row in events],
                               synthetic=True))


def replace_model(case, workspace, final_output=None):
    from research_harness import review_transport

    def respond(route, request, credential=None):
        packet = json.loads(request['input'][-1]['content'].split('The packet below is JSON.\n', 1)[-1])
        if 'allowed_control' in packet:
            output = dict(allowed_control=packet['allowed_control'],
                          evidence_control=packet['evidence_control'], excluded_controls=[])
        elif packet['role'] == 'bar':
            references = [dict(kind='source', link=row['citation'])
                          for row in packet['prior_context'] if 'citation' in row]
            output = build_final_output(case, references, [], stage='bar')
        else:
            output = build_turn_output(case, packet, final_output)
        return build_synthetic_response(route, request, output)

    return patch.object(review_transport, 'send_request', side_effect=respond)


def build_workspace(case, root, configuration, dry_run):
    from research_harness.evidence import digest
    workspace = Workspace(root)
    workspace.execute('problem-init', dict(slug=case['id'], area='math.CO',
                                         field='Synthetic measurement examples', authors=['fixture-author']))
    readings = [record_document(workspace, document) for document in case['documents']]
    workspace.execute('review-route', dict(id='route', adapter='claude_cli_v1',
                                          endpoint='claude://local', model=configuration['model'],
                                          configuration=configuration['configuration']))
    with replace_model(case, workspace) if dry_run else nullcontext():
        workspace.execute('review-probe', dict(id='probe', route_id='route'))
    # These are fixed fixture calibration records, not outcomes being compared.
    with replace_model(case, workspace):
        first_reading = workspace.read_records()['reading'][readings[0]['reading_id']]
        for reviewer in ('reviewer-a', 'reviewer-b'):
            assignment = workspace.execute('review-assignment', dict(
                id='bar-' + reviewer, route_id='route', dossier_id=None, role='bar',
                reviewer_id=reviewer, author_id='fixture-author', context=dict(
                    source_links=[first_reading['inspections'][0]['link']])))['result']
            attempt = workspace.execute('review-run', dict(id='attempt-' + assignment['id'],
                                                           assignment_id=assignment['id']))['result']
            output = workspace.read_json(attempt['output'])
            workspace.execute('value-review', dict(output, id='review-' + assignment['id'],
                                                   dossier_id=None, assignment_id=assignment['id']))
    workspace.execute('problem-statement', dict(
        id='statement', title='Local implication: ' + case['family'], area='math.CO',
        statement=case['statement'], resolution_criteria='Establish the local implication or give a counterexample.',
        neighbours=[], formal_statement=None))
    workspace.execute('problem-value', dict(id='value', statement_id='statement',
        motivation=[dict(text=('Check the local argument and its exact scope; '
                               'this is not a claim of research significance.'),
                         evidence=readings)],
        current_state=[dict(text='The supplied elementary examples do not establish openness.', evidence=readings)]))
    workspace.execute('problem-attempt', dict(
        id='attempt', statement_id='statement', kind='literature', solver=None,
        literature=dict(readings=[{key: row[key] for key in ('version_id', 'reading_id')}
                                  for row in readings],
                        description='Critical reading of both synthetic local arguments.'),
        outcomes=dict(resolved=False, footholds=[],
                      unresolved=[dict(description='The implication is to be assessed.')],
                      obstructions=[]), proportion=dict(extension_reason=None)))
    # The self-assessment deliberately makes the same claimed calibration in all
    # variants. Reference answers stay outside the reviewer packet.
    from research_harness.problem_rubric import ITEMS
    clean_reference = copy.deepcopy(case)
    clean_reference['ground_truth']['components']['rubric:calibration'] = ['pass']
    grades = build_grades(clean_reference)
    reference = dict(kind='record', record_kind='problem_attempt', id='attempt',
                     digest=digest(workspace.read_records()['problem_attempt']['attempt']))
    workspace.execute('problem-assessment', dict(
        id='assessment', statement_id='statement', grades=grades,
        items={item: dict(evidence=[reference],
                         note='The exact synthetic argument is available for critical assessment.')
               for item in ITEMS}, primary=dict(ground='B2', criterion='V1')))
    return workspace


def choose_input_limit(workspace, assignment, partition_seed):
    from research_harness import review_parts as parts, review_protocol as protocol
    from research_harness import review_transport as transport, review_problem_parts as legacy
    from research_harness.errors import ResearchError
    packet = workspace.read_json(assignment['packet'])
    request = protocol.build_assignment_request(workspace.read_records(), workspace.artifacts, assignment)
    route = workspace.read_records()['review_route']['route']
    ordinary_size = legacy._measure(route, request)
    native_limit = transport.local_input_limit(route)
    if ordinary_size > native_limit:
        raise ValueError('Fixture ordinary request does not fit the unmodified Claude input limit.')
    inventory = parts.build_inventory(packet, 'problem_result')
    parameters = dict(orchestration='problem_creation', excerpts=True,
                      common_version=protocol.find_common_prompt_version(assignment['prompt']))

    def measure(candidate):
        prompt = parts.build_turn_prompt('result', parameters, candidate['review_parts'])
        return legacy._measure(route, transport.request_body(route, prompt['text'], candidate, [], order=()))

    # Search measured rendered requests, with no assumed prompt overhead. The
    # random increment varies permitted boundaries independently of defect labels.
    low, high = 1, ordinary_size - 1
    while low < high:
        middle = (low + high) // 2
        try:
            parts.plan_turn(packet, inventory, [], series_id='result', input_limit=middle,
                            measure=measure, output_tokens=route['configuration']['max_output_tokens'])
        except ResearchError as error:
            if error.code not in ('review_parts_frame_too_large', 'review_parts_inventory_too_large',
                                  'review_parts_final_too_large', 'review_parts_no_progress'):
                raise
            low = middle + 1
        else:
            high = middle
    room = ordinary_size - low
    limit = low + room // 3 + random.Random(partition_seed).randrange(max(1, room // 8))
    if limit >= ordinary_size:
        raise ValueError('Fixture leaves no room for a forced parts comparison.')
    return limit, ordinary_size, native_limit


def read_turn(workspace, assignment, attempt, duration):
    from research_harness import review_problem_parts as legacy, review_transport
    records = workspace.read_records()
    request = workspace.read_json(attempt['request'])
    packet = workspace.read_json(assignment['packet'])
    return dict(assignment_id=assignment['id'], attempt_id=attempt['id'],
                kind=packet.get('review_parts', {}).get('kind', 'single'),
                usage=attempt.get('usage'), status=attempt['status'], error=attempt.get('error'),
                seconds=duration, history_messages=len(review_transport.logical_messages(request)) - 1,
                input_characters=legacy._measure(records['review_route']['route'], request),
                packet=packet, request=request, output=workspace.read_json(attempt.get('output')),
                response=workspace.read_json(attempt.get('response')))


def run_mode(case, root, mode, limit, dry_run, max_turns, checkpoint_path=None):
    from research_harness import review_parts, review_protocol, review_transport
    from research_harness.errors import ResearchError
    workspace = Workspace(root)
    references = [dict(kind='record', record_kind='reading', id='read-' + doc['id'])
                  for doc in case['documents']]
    final_output = build_final_output(case, references, ['review-bar-reviewer-a', 'review-bar-reviewer-b'])
    result = dict(complete=False, output=None, turns=[], error=None)
    original_limit = review_transport.local_input_limit
    with ExitStack() as stack:
        if checkpoint_path is not None:
            stack.callback(write_json, checkpoint_path, result)
        if mode == 'parts':
            stack.enter_context(patch.object(review_transport, 'local_input_limit',
                                             side_effect=lambda route: limit if route['id'] == 'route'
                                             else original_limit(route)))
        if dry_run:
            stack.enter_context(replace_model(case, workspace, final_output))
        prior = None
        try:
            for number in range(1, max_turns + 1):
                context = {} if prior is None else dict(prior_assignment_id=prior)
                assignment = workspace.execute('review-assignment', dict(
                    id='result-' + str(number), route_id='route', dossier_id=None,
                    role='result', reviewer_id='reviewer-a', author_id='fixture-author', context=context))['result']
                packet = workspace.read_json(assignment['packet'])
                if mode == 'parts' and 'review_parts' not in packet:
                    raise ValueError('The selected limit did not force parts v2.')
                if mode == 'single' and 'review_parts' in packet:
                    raise ValueError('The ordinary request unexpectedly entered parts.')
                started = time.monotonic()
                attempt = workspace.execute('review-run', dict(
                    id='attempt-result-' + str(number), assignment_id=assignment['id']))['result']
                result['turns'].append(read_turn(workspace, assignment, attempt, time.monotonic() - started))
                if checkpoint_path is not None:
                    write_json(checkpoint_path, result)
                state = review_protocol.assignment_state(workspace.read_records(), workspace.artifacts,
                                                         assignment['id'])
                if not state['ready']:
                    result['error'] = dict(code='assessment_incomplete', state=state)
                    return result
                output = workspace.read_json(attempt['output'])
                is_final = output.get('stage') == 'result'
                if is_final:
                    if mode == 'parts':
                        verification = review_parts.verify_series(workspace.read_records(), workspace.artifacts,
                                                                  assignment['id'])
                        result['verification'] = dict(
                            complete=verification['complete'], issues=verification['issues'],
                            packet_count=len(verification['packets']))
                        if not verification['complete']:
                            result['error'] = dict(code='review_parts_pending')
                            return result
                    workspace.execute('value-review', dict(output, id='review-result',
                                                           dossier_id=None, assignment_id=assignment['id']))
                    result.update(complete=True, output=output)
                    return result
                prior = assignment['id']
            result['error'] = dict(code='measurement_turn_limit', max_turns=max_turns)
        except ResearchError as error:
            result['error'] = error.as_dict()
    return result


def run_pair(case, output_dir, *, dry_run, configuration=None, partition_seed=72,
             first_mode='single', extra_single_runs=0, max_turns=60, budget=None):
    from research_harness import review_transport
    from research_harness.errors import ResearchError
    if not dry_run and budget is None:
        raise ValueError('A paid run requires an explicit PaidBudget.')
    if not dry_run:
        require_paid_prompts()
    configuration = configuration or dict(model='claude-opus-5-5', configuration=dict(
        effort='max', max_output_tokens=128000, timeout_seconds=3600))
    result = dict(case_id=case['id'], family=case['family'], dry_run=dry_run,
                  first_mode=first_mode, partition_seed=partition_seed,
                  model_calls=0 if dry_run else None, single=None, parts=None, repeats=[])
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    charged_before = len(budget.calls) if budget is not None else 0
    model_boundary = (patch.object(review_transport, 'send_request',
                                   side_effect=budget.wrap_sender(review_transport.send_request))
                      if not dry_run else nullcontext())
    temporary_boundary = (confine_temporary_files(output_dir / 'temporary')
                          if not dry_run else nullcontext())
    with model_boundary, temporary_boundary, preserve_temporary_workspace(output_dir, result) as root:
        try:
            if budget is not None:
                budget.require_available()
            seed = build_workspace(case, root / 'seed', configuration, dry_run)
            # Preview on a disposable clone, preserving the identical frozen seed
            # for every measured run, including the ordinary repeats.
            shutil.copytree(seed.root, root / 'preview')
            preview = Workspace(root / 'preview')
            assignment = preview.execute('review-assignment', dict(
                id='preview', route_id='route', dossier_id=None, role='result',
                reviewer_id='reviewer-a', author_id='fixture-author', context={}))['result']
            limit, ordinary_size, native_limit = choose_input_limit(preview, assignment, partition_seed)
            result.update(forced_input_limit=limit, ordinary_input_characters=ordinary_size,
                          native_input_limit=native_limit,
                          evidence_digest=preview.read_json(assignment['packet'])['problem_evidence_digest'],
                          calibration=dict(synthetic=True, probe_synthetic=dry_run,
                                           records=seed.read_records()['value_review']))
            modes = [first_mode, 'parts' if first_mode == 'single' else 'single']
            for mode in modes:
                if budget is not None:
                    budget.require_available()
                shutil.copytree(seed.root, root / mode)
                result[mode] = run_mode(case, root / mode, mode, limit, dry_run, max_turns,
                                       checkpoint_path=output_dir / (case['id'] + '.' + mode + '.json'))
                write_json(output_dir / (case['id'] + '.json'), result)
            for index in range(extra_single_runs):
                if budget is not None:
                    budget.require_available()
                repeat_root = root / ('repeat-' + str(index + 1))
                shutil.copytree(seed.root, repeat_root)
                result['repeats'].append(run_mode(
                    case, repeat_root, 'single', limit, dry_run, max_turns,
                    checkpoint_path=output_dir / (case['id'] + '.repeat-' + str(index + 1) + '.json')))
        except ResearchError as error:
            result['setup_error'] = error.as_dict()
        for mode in ('single', 'parts'):
            if result[mode] is None:
                result[mode] = dict(complete=False, output=None, turns=[],
                                    error=result.get('setup_error', dict(code='not_run')))
    result['model_calls'] = len(budget.calls) - charged_before if budget is not None else 0
    write_json(output_dir / (case['id'] + '.json'), result)
    return result


def read_provenance(plugin_root, configuration):
    from research_harness import review_claude_transport
    revision = subprocess.run(['git', '-C', str(plugin_root), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
    return dict(plugin_root=str(plugin_root), revision=revision, python=sys.version,
                configuration=configuration, runtime=review_claude_transport.runtime())


def main():
    from tools.review_measurement.fixtures import build_fixtures
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--paid', action='store_true', help='Use only after the owner grants a budget.')
    parser.add_argument('--plugin-root', type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--all', action='store_true', help='Option A: 36 pairs and 24 ordinary repeats.')
    selection.add_argument('--fixture', help='One case ID from fixtures.py.')
    parser.add_argument('--output', type=Path, required=True, help='A new, empty result directory.')
    parser.add_argument('--seed', type=int, default=72)
    parser.add_argument('--max-calls', type=int)
    parser.add_argument('--max-turns', type=int, default=60)
    parser.add_argument('--workers', type=int, default=1,
                        help='Independent dry-run processes (1 to 4); paid runs are serial.')
    parser.add_argument('--model', default='claude-opus-5-5')
    parser.add_argument('--effort', default='max', choices=('low', 'medium', 'high', 'xhigh', 'max'))
    parser.add_argument('--max-output-tokens', type=int, default=128000)
    parser.add_argument('--timeout-seconds', type=int, default=3600)
    args = parser.parse_args()
    if args.paid and (args.max_calls is None or args.max_calls < 1):
        parser.error('--paid requires a positive --max-calls budget.')
    if args.max_turns < 1:
        parser.error('--max-turns must be positive.')
    if args.workers not in range(1, 5) or (args.paid and args.workers != 1):
        parser.error('--workers must be 1 to 4, and must be 1 for paid runs.')
    args.plugin_root = args.plugin_root.resolve()
    args.output = args.output.resolve()
    load_harness(args.plugin_root)
    budget = PaidBudget(args.max_calls, args.output / 'usage.json') if args.paid else None
    if budget is not None:
        from research_harness.errors import ResearchError
        try:
            budget.require_available()
        except ResearchError as error:
            print(json.dumps(dict(complete=False, error=error.as_dict())), flush=True)
            return 3
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Use a new output directory; previous outcomes are never overwritten or resampled.')
    if args.paid:
        try:
            require_paid_prompts()
        except ValueError as error:
            parser.error(str(error))
    cases = build_fixtures()
    if args.fixture:
        cases = [case for case in cases if case['id'] == args.fixture]
        if not cases:
            parser.error('Unknown fixture ID.')
    configuration = dict(model=args.model, configuration=dict(
        effort=args.effort, max_output_tokens=args.max_output_tokens,
        timeout_seconds=args.timeout_seconds))
    schedule = build_schedule(cases, args.seed)
    write_json(args.output / 'fixtures.json', cases)
    write_json(args.output / 'manifest.json', dict(
        schema='measurement-option-a-v1', dry_run=args.dry_run,
        seed=args.seed, schedule=schedule, max_turns=args.max_turns,
        provenance=read_provenance(args.plugin_root, configuration)))
    pairs = []
    by_id = {case['id']: case for case in cases}
    from tools.review_measurement.score import score_pairs, render_report
    with ExitStack() as stack:
        jobs = [(by_id[item['case_id']], dict(
            dry_run=args.dry_run, configuration=configuration,
            partition_seed=item['partition_seed'], first_mode=item['first_mode'],
            extra_single_runs=item['extra_single_runs'], max_turns=args.max_turns, budget=budget))
                for item in schedule]
        if args.workers > 1:
            executor = stack.enter_context(ProcessPoolExecutor(
                max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')))
            futures = [executor.submit(run_pair, case, args.output / 'pairs', **options)
                       for case, options in jobs]
            results = (future.result() for future in as_completed(futures))
        else:
            results = (run_pair(case, args.output / 'pairs', **options) for case, options in jobs)
        for result in results:
            pairs.append(result)
            report = score_pairs(cases, pairs)
            write_json(args.output / 'report.json', report)
            (args.output / 'report.md').write_text(render_report(report), encoding='utf-8')
            print('%d/%d %s single=%s parts=%s turns=%d' % (
                len(pairs), len(cases), result['case_id'], result['single']['complete'],
                result['parts']['complete'], len(result['parts']['turns'])), flush=True)
            if budget is not None:
                try:
                    budget.require_available()
                except ResearchError:
                    break
    turns = sum(len(pair['parts']['turns']) for pair in pairs)
    singles = sum(len(pair['single']['turns']) for pair in pairs)
    repeats = sum(len(mode['turns']) for pair in pairs for mode in pair['repeats'])
    write_json(args.output / 'summary.json', dict(
        pairs=len(pairs), parts_turns=turns, single_turns=singles, repeat_turns=repeats,
        model_calls=sum(pair['model_calls'] for pair in pairs),
        projected_diagnostic_calls=turns + singles + repeats,
        route_probe_calls=sum(call['kind'] == 'probe' for call in budget.calls) if budget is not None else 0,
        synthetic_setup='One probe in dry run and two fixed bar reviews per case.'))
    print(render_report(report))
    return 1 if any(report['incomplete'].values()) else 0


class RunnerTests(unittest.TestCase):
    def setUp(self):
        from research_harness import review_transport
        boundary = patch.object(review_transport, 'send_request',
                                side_effect=AssertionError('Tests cannot call a model.'))
        boundary.start()
        self.addCleanup(boundary.stop)

    def test_real_harness_dry_run_finishes_and_restores_the_route_limit(self):
        import tempfile
        from pathlib import Path
        from tools.review_measurement.fixtures import build_fixtures
        from research_harness import review_transport
        original = review_transport.local_input_limit
        from research_harness import review_claude_transport
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.object(
                review_claude_transport, '_cli', side_effect=AssertionError('Dry run reached Claude')):
            result = run_pair(build_fixtures()[1], Path(directory), dry_run=True)
            checkpoint = Path(directory) / (build_fixtures()[1]['id'] + '.parts.json')
            self.assertTrue(checkpoint.is_file())
            self.assertTrue(json.loads(checkpoint.read_text())['complete'])
        self.assertTrue(result['single']['complete'], result['single'].get('error'))
        self.assertTrue(result['parts']['complete'], result['parts'].get('error'))
        kinds = [turn['kind'] for turn in result['parts']['turns']]
        self.assertTrue({'read', 'combine', 'check', 'final'} <= set(kinds), kinds)
        self.assertTrue(all(turn['history_messages'] == 0 for turn in result['parts']['turns']))
        self.assertTrue(all(turn['usage']['output_tokens'] is not None
                            for turn in result['parts']['turns']))
        self.assertEqual(result['model_calls'], 0)
        self.assertIs(review_transport.local_input_limit, original)
        final_packet = result['parts']['turns'][-1]['packet']
        cards = final_packet['review_parts_state']['cards']
        self.assertTrue(any(card['statements'] for card in cards))
        self.assertTrue(all(statement.get('at') for card in cards for statement in card['statements']))
        self.assertEqual(result['single']['turns'][0]['packet']['problem_evidence_digest'],
                         final_packet['problem_evidence_digest'])

    def test_schedule_has_all_cases_and_two_clean_repeats(self):
        from tools.review_measurement.fixtures import build_fixtures
        schedule = build_schedule(build_fixtures(), seed=72)
        self.assertEqual(len(schedule), 36)
        self.assertEqual(sum(row['extra_single_runs'] for row in schedule), 24)
        self.assertEqual({row['first_mode'] for row in schedule}, {'single', 'parts'})

    def test_paid_budget_persists_charge_before_call_and_stops_at_cap(self):
        from research_harness.errors import ResearchError
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            ledger = PaidBudget(1, Path(directory) / 'usage.json')

            def respond(route, request, credential=None):
                saved = json.loads(ledger.path.read_text(encoding='utf-8'))
                self.assertEqual(len(saved['calls']), 1)
                self.assertEqual(saved['calls'][0]['status'], 'admitted')
                return dict(status='completed', usage=dict(input_tokens=2, output_tokens=3))

            send = ledger.wrap_sender(respond)
            send(dict(id='route'), {})
            with self.assertRaises(ResearchError) as raised:
                send(dict(id='route'), {})
            self.assertEqual(raised.exception.code, 'measurement_budget_exhausted')
            self.assertEqual(len(ledger.calls), 1)
            self.assertEqual(ledger.calls[0]['usage']['output_tokens'], 3)

    def test_paid_api_requires_an_explicit_budget(self):
        from tools.review_measurement.fixtures import build_fixtures
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            with patch(__name__ + '.build_workspace', side_effect=AssertionError('Reached paid setup')):
                with self.assertRaises(ValueError):
                    run_pair(build_fixtures()[0], Path(directory), dry_run=False)

    def test_paid_refuses_placeholder_prompts_before_setup(self):
        from tools.review_measurement.fixtures import build_fixtures
        from research_harness import review_parts_prompts
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            budget = PaidBudget(1, Path(directory) / 'usage.json')
            with patch.object(review_parts_prompts, 'READ', 'PLACEHOLDER: test'), patch(
                    __name__ + '.build_workspace', side_effect=AssertionError('Reached paid setup')):
                with self.assertRaises(ValueError):
                    run_pair(build_fixtures()[0], Path(directory), dry_run=False, budget=budget)

    def test_probe_call_accounting_uses_admitted_calls(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            budget = PaidBudget(2, Path(directory) / 'usage.json')
            sender = budget.wrap_sender(lambda route, request, credential: dict(status='completed'))
            sender(dict(id='route'), dict(input=[dict(content='{"allowed_control":"abc"}')]))
            self.assertEqual([call['kind'] for call in budget.calls], ['probe'])

    def test_unexpected_failure_retains_workspace_and_surfaces(self):
        from tools.review_measurement.fixtures import build_fixtures
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            with patch(__name__ + '.build_workspace', side_effect=RuntimeError('Interrupted setup')):
                with self.assertRaises(RuntimeError):
                    run_pair(build_fixtures()[0], Path(directory), dry_run=True)
            self.assertTrue(list(Path(directory).glob('measurement-*')))


if __name__ == '__main__':
    sys.exit(main())
