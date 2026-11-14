"""Build only the additional r4.2 stopped stores with the already archived 0.87.0 tree."""

from contextlib import ExitStack
import hashlib
import inspect
import json
import tarfile
from unittest.mock import patch

from build_fixture import DESTINATION
from openness_reading_fixtures import OpennessReadingCase, build_sized_body
from research_harness import openness_reading as reading, problem_openness as openness
from research_harness.capture_selection import select_main_capture
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.reading import record_reading
from research_harness.storage import Store


class TwoWorkCase(OpennessReadingCase):
    text_sizes = (120000, 60000)
    reading_protocol = None


def install_synthetic_inputs(stack):
    for target in ('research_harness.acquisition.runtime_provenance',
                   'research_harness.review_task_execution.runtime_provenance'):
        stack.enter_context(patch(target, return_value={'executable': 'synthetic-runtime'}))
    stack.enter_context(patch.object(Store, 'describe_runtime', return_value={'executable': 'synthetic-runtime'}))
    namespace = dict(vars(openness))
    source = inspect.getsource(openness.export_openness).replace('str(store.root.resolve())', "'synthetic-workspace'")
    exec(compile(source, '<synthetic-export-root>', 'exec'), namespace)
    stack.enter_context(patch.object(openness, 'export_openness', namespace['export_openness']))


def record_chosen_reading(case, work, compact, first=None):
    bundle = case.bundle(work, capture=compact, bundle_id='bundle-compact')
    if first is not None:
        bundle['units'].append(dict(id='earlier-extraction', kind='text', required=True,
            link=case.link(work, first['source_id'], first['text'], 'Paper 2 opening line.', kind='span')))
    for unit in bundle['units']:
        link = unit['link']
        if link['locator']['kind'] == 'text':
            unit['link'] = case.link(work, link['source_id'], link['artifact'], link['locator']['quote'], kind='span')
    bundle['inventory']['links'] = [bundle['units'][0]['link']]
    case.mutate(import_bundle, bundle)
    case.mutate(record_reading, case.full_note(bundle, 'read-compact'))


def describe_delivered_units(case, native):
    """Read delivery credit with the tag's own verifier, separately from its pending inventory."""
    from research_harness.review_protocol import assignment_state
    records = case.store.snapshot()['records']
    assignment = records['review_assignment']['turn-1']
    if not assignment_state(records, case.artifacts, 'turn-1')['ready']:
        return []
    packet = json.loads(case.artifacts.read(native))
    stored = json.loads(case.artifacts.read(assignment['packet']))
    rows = reading.list_reading_rows(records, case.artifacts, packet)
    manifest = (reading._plan_search_delivery(case.artifacts, packet)['manifest']
                if reading.find_protocol(stored) == reading.PROTOCOL_V5 else None)
    rows = reading.list_delivery_rows(rows, manifest) if manifest is not None else rows
    observations = reading._observe_turns(records, case.artifacts, [assignment], rows,
                                         reading._compute_spans(case.artifacts, rows), manifest)
    return observations[0]['turn']['delivered_units']


def save_store(case, name, metadata):
    files = {}
    with tarfile.open(DESTINATION / (name + '.tar.xz'), 'w:xz') as archive:
        for path in sorted(case.root.rglob('*')):
            relative = path.relative_to(case.root)
            if not path.is_file() or relative.parts[0] not in ('research', '.exactory'):
                continue
            data = path.read_bytes()
            for forbidden in (b'/Users/', b'/private/', b'math-conjectures/', b'exactory-research/'):
                assert forbidden not in data, (str(relative), forbidden)
            files[str(relative)] = hashlib.sha256(data).hexdigest()
            member = archive.gettarinfo(str(path), arcname=str(relative))
            member.uid = member.gid = 0
            member.uname = member.gname = ''
            member.mtime = 0
            member.pax_headers = {}
            with path.open('rb') as source:
                archive.addfile(member, source)
    return dict(metadata, files=files)


def build_stopped(version, state, *, case_two=False, both=False):
    with ExitStack() as stack:
        install_synthetic_inputs(stack)
        case = TwoWorkCase()
        case.setUp()
        stack.callback(case.doCleanups)
        if version == 4:
            case.limit_input(400000)
            stack.enter_context(patch.object(reading, 'find_chain_protocol', return_value=reading.PROTOCOL_V4))
        work = case.versions[1]
        first = case.store.snapshot()['records']['work'][work]['fulltexts'][0]
        compact = case.capture(work, pdf_text=build_sized_body(2, 50000))
        case.mutate(select_main_capture, dict(version_id=work, source_id=compact['source_id'],
                                             reason='Choose the complete second extraction.'))
        if not case_two:
            record_chosen_reading(case, work, compact, first if both else None)
        native = case.export(priority=case.versions if not case_two else ())
        case.assign('turn-1', native)
        packet = case.read_packet('turn-1')
        plan = packet['reading_turn']['reading_plan']
        assert all(row.get('version_id') != work for row in plan['delivered_now'])
        if not case_two:
            assert any(row.get('version_id') == work and row.get('text_sha256') == compact['text']['sha256']
                       for row in plan['remaining'])
        if state == 'observed':
            case.invoke('turn-1', case.build_reading_response(packet, excerpts=[]))
        carried = {row['artifact']['sha256'] for row in json.loads(case.artifacts.read(native))['evidence']}
        scenario = 'stopped-' + ('choice' if case_two else 'both' if both else 'chosen') + '-' + state
        name = 'v%d-%s' % (version, scenario)
        metadata = dict(protocol=version, scenario=scenario, work=work, first_work=case.versions[0], native=native,
                        turns=['turn-1'], first=first, compact=compact, input_limit=400000 if version == 4 else None,
                        planned_now=plan['delivered_now'], planned_remaining=plan['remaining'],
                        delivered_units=describe_delivered_units(case, native),
                        carries_first=first['text']['sha256'] in carried,
                        carries_chosen=compact['text']['sha256'] in carried)
        print(json.dumps(dict(name=name, carries_first=metadata['carries_first'],
            carries_chosen=metadata['carries_chosen'], now=[(row.get('kind'), row.get('version_id'))
                for row in plan['delivered_now']], remaining=[(row.get('kind'), row.get('version_id'))
                for row in plan['remaining']])), flush=True)
        return name, save_store(case, name, metadata)


if __name__ == '__main__':
    manifest_path = DESTINATION / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for version in (4, 5):
        for state in ('observed', 'uncalled'):
            name, metadata = build_stopped(version, state)
            manifest['stores'][name] = metadata
        name, metadata = build_stopped(version, 'observed', case_two=True)
        manifest['stores'][name] = metadata
        name, metadata = build_stopped(version, 'observed', both=True)
        manifest['stores'][name] = metadata
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
