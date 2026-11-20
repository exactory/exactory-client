"""Build historical reading chains with the exact code of their releases.

The fixtures pin openness_reading.py and review_excerpts.py at 448b6da (0.65.2, also kept by 0.66.0 to 0.66.2) and
c408fea (0.71.8) by SHA-256. CI checks out a single commit, so the bytes are kept here rather than read from history.
The 0.72.0 builder at 6f2121a also pins v4 chains with excerpt-v3 retention.
"""

import contextlib
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

from research_harness import review_protocol as protocol

# The modules of release 0.65.2 (448b6da) that built its reading chains, each with the SHA-256 of its exact bytes.
RELEASE_0_65_2_MODULES = (
    ('review_excerpts', 'fixtures/review_excerpts_0_65_2.py.txt',
     '4070b149c027bb248bbc6912492417c6cd44c7f0486073da43ad7bf5f45b80b3'),
    ('openness_reading', 'fixtures/openness_reading_0_65_2.py.txt',
     '4dfe0e00d8f5819c870dcd3c910be6e90750f5d351e0a8bd353342c9f15e6bf8'))


def load_0_65_2_openness_reading():
    """The openness reading module of 0.65.2 with the review_excerpts module of 0.65.2. Both run inside the
    research_harness package, so their other imports resolve to the current modules."""
    modules = {}
    for name, path, sha256 in RELEASE_0_65_2_MODULES:
        location = Path(__file__).resolve().parent / path
        source = location.read_bytes()
        if hashlib.sha256(source).hexdigest() != sha256:
            raise AssertionError('The 0.65.2 source fixture changed: ' + path)
        spec = importlib.util.spec_from_loader('research_harness.%s_0_65_2' % name, loader=None)
        module = importlib.util.module_from_spec(spec)
        exec(compile(source, str(location), 'exec'), module.__dict__)
        modules[name] = module
    modules['openness_reading'].review_excerpts = modules['review_excerpts']
    return modules['openness_reading']


def find_release_protocol(packet):
    """The reading protocol that record_assignment passes for a 0.65.2 packet: none, so that canonical_prompt renders
    the release's own current protocol, as 0.65.2 always did."""
    return None


@contextlib.contextmanager
def build_as_0_65_2():
    """Record native_math assignments with the reading chain of 0.65.2: its v2 prompts, plan entries without a kind, its
    verbose reading record and no record units. The release had no find_protocol lookup, which the current
    record_assignment calls; the module gets find_release_protocol in its place."""
    released = load_0_65_2_openness_reading()
    released.find_protocol = find_release_protocol
    with patch.object(protocol, 'openness_reading', released):
        yield


RELEASE_0_71_8_MODULES = (
    ('review_excerpts', 'fixtures/review_excerpts_0_71_8.py.txt',
     '2f294787a7349d035edcd476c8f49b16422ef4a36e410cd4e833cb354e605cc1'),
    ('openness_reading', 'fixtures/openness_reading_0_71_8.py.txt',
     '72e00ff9abf56ceabcc4ef3b8c5c9d394e3dd9d3ddafc56abd40a11f8a7ee723'))


def load_0_71_8_openness_reading():
    """Load the exact reading builders from c408fea (0.71.8), never a relabeled current chain."""
    modules = {}
    for name, path, sha256 in RELEASE_0_71_8_MODULES:
        location = Path(__file__).resolve().parent / path
        source = location.read_bytes()
        if hashlib.sha256(source).hexdigest() != sha256:
            raise AssertionError('The 0.71.8 source fixture changed: ' + path)
        spec = importlib.util.spec_from_loader('research_harness.%s_0_71_8' % name, loader=None)
        module = importlib.util.module_from_spec(spec)
        exec(compile(source, str(location), 'exec'), module.__dict__)
        modules[name] = module
    modules['openness_reading'].review_excerpts = modules['review_excerpts']
    return modules['openness_reading']


@contextlib.contextmanager
def build_as_0_71_8():
    """Build and prompt v4 assignments using the release's unchanged bytes."""
    with patch.object(protocol, 'openness_reading', load_0_71_8_openness_reading()):
        yield


def load_0_72_0_openness_reading():
    """Load the exact v4 reading builder from 6f2121a, with its unchanged excerpt-v3 module."""
    location = Path(__file__).resolve().parent / 'fixtures/openness_reading_0_72_0.py.txt'
    source = location.read_bytes()
    if hashlib.sha256(source).hexdigest() != 'abe357a573cfc30c15ae001e70debcbac4e3eb9fb20f8f33762d3801e3b317ea':
        raise AssertionError('The 0.72.0 source fixture changed: ' + str(location))
    spec = importlib.util.spec_from_loader('research_harness.openness_reading_0_72_0', loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(location), 'exec'), module.__dict__)
    return module


def load_0_87_0_openness_reading():
    """Load the v5 builder used before new exports select v6, pinned without repository history."""
    location = Path(__file__).resolve().parent / 'fixtures/openness_reading_0_87_0.py.txt'
    source = location.read_bytes()
    if hashlib.sha256(source).hexdigest() != '1b33b0fa64a5be8b74bae463c254346aa6d744981273321c60a87a6ffb3964e1':
        raise AssertionError('The 0.87.0 reading builder changed')
    spec = importlib.util.spec_from_loader('research_harness.openness_reading_0_87_0', loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(location), 'exec'), module.__dict__)
    return module


def export_as_0_85_3(*args, **kwargs):
    """Build the stored minimum-v1 export with its original function, without relabeling new packets."""
    from research_harness import problem_openness
    location = Path(__file__).resolve().parent / 'fixtures/openness_export_0_85_3.py.txt'
    source = location.read_bytes()
    if hashlib.sha256(source).hexdigest() != '300a91030ed7034ce6abd065d01be6a70caca4a045d67abe08af254508eeabb2':
        raise AssertionError('The 0.85.3 export builder changed')
    namespace = dict(vars(problem_openness))
    exec(compile(source, str(location), 'exec'), namespace)
    return namespace['export_openness'](*args, **kwargs)


def select_as_0_91_1(records, artifacts, inventory, identity):
    """The selection statements of 605a682 (0.91.2, unchanged since 0.91.1).

    Keep the credit-only turn rule here so compatibility tests never manufacture
    a version 1 selection by relabeling the current selection.
    """
    from research_harness import openness_carried as carried
    candidates = []
    for chain in carried.list_qualifying_chains(records, artifacts, identity):
        observations = carried.observe_qualifying_chain(records, artifacts, chain, identity)
        first = records['review_assignment'][chain['chain']]
        classes = carried.classify_carried_notes(inventory, observations,
            search_dependencies=carried._list_search_dependencies(records, artifacts, first))
        for observation, notes in zip(observations, classes):
            if observation['origin']['chain'] != chain['chain'] or observation['turn']['kind'] != 'reading':
                continue
            credit = carried.credit_carried_turn(inventory, observation)
            credit['notes'] = notes
            candidates.append((observation, credit, dict(credit, ranges=[], searches=[])))
    for unit in inventory['units']:
        covered = []
        for observation, credit, selected in candidates:
            if unit['identity'].get('record_kind') == 'literature_search':
                if unit['sha256'] in credit['searches']:
                    selected['searches'].append(unit['sha256'])
                    break
            else:
                for span in credit['ranges']:
                    if span['unit'] != unit['handle']:
                        continue
                    extended = carried._merge_ranges(covered + [(span['start'], span['end'])])
                    if carried._count_characters(extended) > carried._count_characters(covered):
                        selected['ranges'].append(span)
                        covered = extended
                if carried._count_characters(covered) >= unit['characters']:
                    break
    selected = [row for observation, credit, row in candidates if row['ranges'] or row['searches']]
    return sorted(selected, key=lambda row: records['review_attempt'][row['origin']['attempt_id']]['recorded_revision'])
