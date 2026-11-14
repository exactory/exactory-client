"""Build the r4.2 stopped native math stores with archived release 0.87.0."""

from contextlib import ExitStack
import inspect
import json
from unittest.mock import patch

from build_stopped import DESTINATION, describe_delivered_units, install_synthetic_inputs, record_chosen_reading, save_store
from openness_reading_fixtures import build_sized_body
from tests.test_openness_reading_chain import MathOpennessReadingTests
from research_harness.capture_selection import select_main_capture
from research_harness.evidence import digest
from research_harness.reading import fulltext_coverage
from search_controller import reviewer


def build_stopped_math(state):
    with ExitStack() as stack:
        install_synthetic_inputs(stack)
        namespace = dict(vars(reviewer))
        source = inspect.getsource(reviewer.packet).replace('str(Path(root).resolve())', "'synthetic-workspace'")
        exec(compile(source, '<synthetic-native-root>', 'exec'), namespace)
        stack.enter_context(patch.object(reviewer, 'packet', namespace['packet']))
        case = MathOpennessReadingTests()
        case.setUp()
        stack.callback(case.doCleanups)
        case.build_fixture(response_sizes=(1000,), text_sizes=(119000, 60000))
        work = case.versions[1]
        first = case.store.snapshot()['records']['work'][work]['fulltexts'][0]
        compact = case.case.capture(work, pdf_text=build_sized_body(2, 50000))
        case.case.mutate(select_main_capture, dict(version_id=work, source_id=compact['source_id'],
                                                  reason='Choose the complete second extraction.'))
        record_chosen_reading(case.case, work, compact)
        records = case.store.snapshot()['records']
        readings = [digest(records['reading'][key]) for version in case.versions
                    for key in fulltext_coverage(records, case.artifacts, version)['readings']]
        case.review['findings']['openness']['minimum']['reading_digests'] = readings
        checkpoint = case.controller.status()['checkpoints']['checkpoint-000001']
        case.export_fixture(checkpoint)
        native = case.exported['native_packet']
        packet = json.loads(case.artifacts.read(native))
        case.review['findings']['openness']['evidence'] = [row['digest'] for row in packet['evidence']]
        case.assign_turn('turn-1')
        plan = case.read_turn('turn-1')['reading_turn']['reading_plan']
        assert any(row.get('version_id') == case.versions[0] for row in plan['delivered_now'])
        assert all(row.get('version_id') != work for row in plan['delivered_now'])
        assert any(row.get('version_id') == work for row in plan['remaining'])
        if state == 'observed':
            case.invoke('turn-1', case.build_reading_response(case.read_turn('turn-1')))
        carried = {row['artifact']['sha256'] for row in packet['evidence']}
        scenario = 'stopped-chosen-' + state
        name = 'math-' + scenario
        metadata = dict(protocol='math', scenario=scenario, work=work, first_work=case.versions[0],
            native=native, turns=['turn-1'], first=first, compact=compact, review=case.review,
            planned_now=plan['delivered_now'], planned_remaining=plan['remaining'],
            delivered_units=describe_delivered_units(case, native),
            carries_first=first['text']['sha256'] in carried, carries_chosen=compact['text']['sha256'] in carried)
        print(json.dumps(dict(name=name, carries_first=metadata['carries_first'], carries_chosen=metadata['carries_chosen'],
            now=[(row.get('kind'), row.get('version_id')) for row in plan['delivered_now']],
            remaining=[(row.get('kind'), row.get('version_id')) for row in plan['remaining']])), flush=True)
        return name, save_store(case, name, metadata)


if __name__ == '__main__':
    path = DESTINATION / 'manifest.json'
    manifest = json.loads(path.read_text())
    for state in ('observed', 'uncalled'):
        name, metadata = build_stopped_math(state)
        manifest['stores'][name] = metadata
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
