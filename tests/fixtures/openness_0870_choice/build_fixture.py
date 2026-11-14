"""Run once with the archived exactory--v0.87.0 tree on PYTHONPATH (including its tests).

Synthetic root metadata is supplied before export; stored bytes are never rewritten.
No test invokes this builder or reads git history.
"""

import hashlib
import inspect
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from test_openness_search_sharing import SearchSharingCase
from research_harness import openness_reading as reading, problem_openness as openness
from research_harness.capture_selection import select_main_capture
from research_harness.errors import ResearchError
from research_harness.literature import import_bundle
from research_harness.reading import record_reading
from research_harness.storage import Store

COMPACT_TEXT = ('Paper 1 opening line.\nThe compact extraction gives the complete theorem and proof.\n'
                'References: none.')
DESTINATION = Path(__file__).resolve().parent


def choose_capture(case, capture):
    case.mutate(select_main_capture, dict(version_id=case.versions[0], source_id=capture['source_id'],
                                         reason='Deliver the complete compact extraction.'))


def record_compact_reading(case, compact):
    bundle = case.bundle(case.versions[0], capture=compact, bundle_id='bundle-compact')
    for unit in bundle['units']:
        quote = unit['link']['locator']['quote']
        unit['link'] = case.link(case.versions[0], compact['source_id'], compact['text'], quote, kind='span')
    bundle['inventory']['links'] = [bundle['units'][0]['link']]
    case.mutate(import_bundle, bundle)
    case.mutate(record_reading, case.full_note(bundle, 'read-compact'))


def complete_chain(case, native):
    turns = ['turn-1']
    case.assign(turns[0], native)
    while reading.find_stage(case.read_packet(turns[-1])) == 'reading':
        case.invoke(turns[-1], case.build_reading_response(case.read_packet(turns[-1]), excerpts=[]))
        turns.append('turn-%d' % (len(turns) + 1))
        case.assign(turns[-1], native, prior=turns[-2])
        if len(turns) >= 16:
            raise AssertionError('The synthetic chain did not finish')
    case.invoke(turns[-1], case.build_native_result(case.read_packet(turns[-1])))
    case.mutate(case.load_problem_module().record_openness, {'assignment_id': turns[-1]})
    return turns


def build_store(version, scenario):
    case = (OpennessReadingCase if version == 4 else SearchSharingCase)()
    runtime_patch = patch('research_harness.acquisition.runtime_provenance',
                          return_value={'executable': 'synthetic-runtime'})
    store_runtime_patch = patch.object(Store, 'describe_runtime', return_value={'executable': 'synthetic-runtime'})
    store_runtime_patch.start()
    runtime_patch.start()
    task_runtime_patch = patch('research_harness.review_task_execution.runtime_provenance',
                               return_value={'executable': 'synthetic-runtime'})
    task_runtime_patch.start()
    case.setUp()
    try:
        if version == 4:
            case.limit_input()
        work = case.versions[0]
        first = case.store.snapshot()['records']['work'][work]['fulltexts'][0]
        compact = None
        if scenario != 'control':
            compact = case.capture(work, pdf_text=COMPACT_TEXT)
            choose_capture(case, compact)
        if scenario == 'workaround':
            record_compact_reading(case, compact)
        # Change only synthetic workspace metadata at its input, before any digest is made.
        namespace = dict(vars(openness))
        source = inspect.getsource(openness.export_openness).replace('str(store.root.resolve())', "'synthetic-workspace'")
        exec(compile(source, '<synthetic-export-root>', 'exec'), namespace)
        with patch.object(openness, 'export_openness', namespace['export_openness']):
            try:
                native = case.export(priority=case.versions)
            except ResearchError as error:
                if error.code != 'openness_reading_gap':
                    raise
                native = case.export()
        turns = complete_chain(case, native)
        records = case.store.snapshot()['records']
        assert openness.assess_openness(records, case.artifacts) == []
        name = 'v%d-%s' % (version, scenario)
        files = {}
        with tarfile.open(DESTINATION / (name + '.tar.xz'), 'w:xz') as archive:
            for path in sorted(case.root.rglob('*')):
                relative = path.relative_to(case.root)
                if not path.is_file() or relative.parts[0] not in ('research', '.exactory'):
                    continue
                data = path.read_bytes()
                assert b'/Users/' not in data and b'/private/' not in data, str(relative)
                files[str(relative)] = hashlib.sha256(data).hexdigest()
                member = archive.gettarinfo(str(path), arcname=str(relative))
                member.uid = member.gid = 0
                member.uname = member.gname = ''
                member.mtime = 0
                member.pax_headers = {}
                with path.open('rb') as source:
                    archive.addfile(member, source)
        row = dict(protocol=version, scenario=scenario, work=work, native=native, turns=turns,
                   first=first, compact=compact, files=files)
        print(json.dumps(dict(name=name, turns=len(turns), files=len(files))), flush=True)
        return name, row
    finally:
        case.doCleanups()
        runtime_patch.stop()
        task_runtime_patch.stop()
        store_runtime_patch.stop()


if __name__ == '__main__':
    stores = dict(build_store(version, scenario) for version in (4, 5)
                  for scenario in ('control', 'choice', 'workaround'))
    (DESTINATION / 'manifest.json').write_text(json.dumps(dict(tag='exactory--v0.87.0', stores=stores),
                                                         indent=2, sort_keys=True) + '\n')
