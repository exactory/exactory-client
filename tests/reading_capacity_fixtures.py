"""Synthetic finite v7 inventories; no incident text or provider calls."""

import ast
import copy
import inspect
import json
import textwrap

from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_route_capacity as capacity
from research_harness import review_transport as transport
from research_harness.evidence import digest
from test_openness_reading_v7_turns import MemoryArtifacts, build_native
from v7_capacity_fixtures import build_route


# These rules describe physical delivery, including JSON records. JSON is not
# parsed until its complete artifact is reconstructed from authenticated ranges.
DELIVERY_RULES = {
    'fulltext': dict(delivery='segments'),
    'search_response': dict(delivery='segments'),
    'record': dict(delivery='segments'),
    'result_text': dict(delivery='segments'),
    'fixed_field': dict(delivery='segments'),
    'target_page': dict(delivery='segments'),
    'carried_reading': dict(delivery='segments'),
    'list_page': dict(delivery='whole', largest_serialized_row=2000,
                      largest_size=120000, bound='current material allowance, including evidence and plan.now'),
}
RECORD_DELIVERY_RULES = {name: dict(delivery='segments') for name in (
    'literature_search', 'reading', 'accepted_result', 'problem_statement',
    'search_shared_field', 'search_residual', 'native_evidence', 'openness_minimum',
    'minimum_gaps', 'audit_notices', 'native_reviews', 'openness_delivery', 'source_evidence')}


def read_inventory_kinds(sources=None):
    """Derive vocabulary from the source that supplies logical and physical units.

    Read only constructors on the inventory path, not prompts or validator lists.
    A newly introduced literal kind therefore fails without a built fixture.
    """
    functions = (reading.list_reading_texts, reading.list_math_response_rows,
        reading.list_math_text_rows, reading.list_record_rows, reading.list_result_texts,
        reading.list_delivery_rows, v7.build_inventory, v7.prepare_inventory,
        v7._append_carried_reading, v7._pack_deliveries)
    if sources is None:
        from research_harness import capture_selection
        modules = {reading.__name__, v7.__name__, capture_selection.__name__}
        pending, visited, sources = list(functions), set(), {}
        while pending:
            function = pending.pop()
            if function in visited:
                continue
            visited.add(function)
            source = inspect.getsource(function)
            sources[function.__name__] = source
            for node in ast.walk(ast.parse(textwrap.dedent(source))):
                if not isinstance(node, ast.Call):
                    continue
                parts, name = [], node.func
                while isinstance(name, ast.Attribute):
                    parts.append(name.attr)
                    name = name.value
                candidate = function.__globals__.get(name.id) if isinstance(name, ast.Name) else None
                for part in reversed(parts):
                    candidate = getattr(candidate, part, None)
                if inspect.isfunction(candidate) and candidate.__module__ in modules:
                    pending.append(candidate)
    kinds, records = set(), set()
    def read_strings(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return read_strings(node.body) | read_strings(node.orelse)
        if isinstance(node, (ast.Tuple, ast.List)):
            return set().union(*(read_strings(child) for child in node.elts))
        return set()
    for name, source in sources.items():
        tree = ast.parse(textwrap.dedent(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg in ('kind', 'record_kind'):
                (kinds if node.arg == 'kind' else records).update(read_strings(node.value))
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value in ('kind', 'record_kind'):
                        (kinds if key.value == 'kind' else records).update(read_strings(value))
            # The minimum's record kinds and movable field kinds are loop data.
            if name == 'build_inventory' and isinstance(node, (ast.Tuple, ast.List)):
                if len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant):
                    if node.elts[0].value in RECORD_DELIVERY_RULES:
                        records.add(node.elts[0].value)
            if name == 'prepare_inventory' and isinstance(node, ast.Tuple) and len(node.elts) == 4:
                kinds.update(read_strings(node.elts[-1]))
        if name == 'list_record_rows':
            # The bound list is (record_kind, digest); constructor uses its name.
            for node in ast.walk(tree):
                if isinstance(node, ast.Tuple) and len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant):
                    records.update(read_strings(node.elts[0]))
        if name == 'build_inventory':
            for node in ast.walk(tree):
                if isinstance(node, ast.Tuple) and node.elts and isinstance(node.elts[0], ast.Constant):
                    records.update(read_strings(node.elts[0]))
            for node in ast.walk(tree):
                if isinstance(node, ast.Tuple) and all(isinstance(x, ast.Constant) for x in node.elts):
                    if 'native_reviews' in read_strings(node):
                        records.update(read_strings(node))
    return kinds - {'kind'}, records - {'record_kind'}


def build_reading_route(adapter, category):
    model = ('unlisted-model' if category == 'unlisted' else
             'claude-opus-5-5' if adapter == 'claude_cli_v1' else 'gpt-6-astra')
    # Unlisted models intentionally have no keyed prior: admission must stop on
    # adapters without a usable default, before a provider sees any request.
    if category == 'unlisted':
        from test_review_route_capacity import build_route as unbound
        route = unbound(adapter, model, effort='max')
    else:
        route = build_route(adapter, model, effort='xhigh' if category == 'fits' else 'max')
    if adapter == transport.ADAPTER:
        route['endpoint'] = 'https://api.openai.com/v1/responses'
    return route


def build_shape_inventory(artifacts, *, characters=200, count=1, kind='fulltext', record_kind=None,
                          non_ascii=False):
    rows, bodies = [], {}
    for number in range(count):
        prefix = '%08d:' % number
        if non_ascii:
            non_ascii_characters = characters * 2 // 5
            ascii_characters = characters - non_ascii_characters
            text = prefix + ('abc' * (ascii_characters // 3 + 1))[:ascii_characters - len(prefix)]
            text += ('日本' * (non_ascii_characters // 2 + 1))[:non_ascii_characters]
        else:
            text = (prefix + 'abcde' * (characters // 5 + 1))[:characters]
        reference = artifacts.put(text.encode('utf-8'), 'text/plain; charset=utf-8')
        if kind in ('fixed_field', 'target_page'):
            identity = dict(field='fixture-%d' % number)
        elif kind == 'carried_reading':
            identity = dict(origin=dict(chain='origin-%d' % number, assignment_id='old-%d' % number,
                attempt_id='attempt-%d' % number, protocol=reading.PROTOCOL_V6), content_kind='affected_notes')
        elif kind == 'record':
            identity = dict(record_kind=record_kind or 'reading', record_id='record-%d' % number)
        elif kind == 'search_response':
            identity = dict(source_id='source-%d' % number, text_sha256=reference['sha256'])
        elif kind == 'result_text':
            identity = dict(native_attempt_digest='a' * 64, acceptance_id='acceptance-%d' % number,
                            text_sha256=reference['sha256'])
        else:
            identity = dict(version_id='work-%d' % number, text_sha256=reference['sha256'])
        rows.append(dict(kind=kind, artifact=reference, **identity))
        bodies[reference['sha256']] = text.encode('utf-8')
    # Every v7 final names the canonical minimum-gap record, including an empty one.
    gaps = artifacts.put(b'[]', 'application/json')
    rows.append(dict(kind='record', record_kind='minimum_gaps',
                     record_id='minimum_gaps', artifact=gaps))
    bodies[gaps['sha256']] = b'[]'
    return v7.plan_inventory(rows, bodies)


def build_reading_answer(packet):
    binding = packet['reading_turn']
    return dict(schema_version=2, stage='reading', notes='Read the supplied ranges.', excerpts=[], gaps=[],
        passage_requests=[], **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})


def build_adapter_response(route, request, output):
    """Use each adapter's own input inspection, with only its provider replaced."""
    from research_harness import review_claude_transport as claude, review_codex_visual_transport as visual
    if route['adapter'] == claude.ADAPTER:
        from unittest.mock import patch
        from test_research_claude_transport import clean_stream, completed
        events = clean_stream(request, output=json.dumps(output), model=route['model'])
        if 'allowed_control' in output:
            events[-1]['usage']['input_tokens'] = 500000
        with patch.object(claude, '_cli', return_value=('fixture-claude', 'fixture-version')), patch.object(
                claude, '_run_owned', return_value=completed(events)):
            return claude.send_request(route, request, 'fixture')
    if route['adapter'] in ('codex_cli_v1', 'codex_cli_framed_v1'):
        from codex_route_fixtures import build_codex_response
        response = build_codex_response(route, request, output)
    else:
        response = dict(id='fixture-response', model=route['model'], status='completed',
            output=[dict(type='message', role='assistant', content=[dict(type='output_text', text=json.dumps(output))])],
            usage=dict(input_tokens=37, output_tokens=12))
        if route['adapter'] == visual.ADAPTER:
            from research_harness.review_visual_inputs import control_image
            events = [dict(type='session_meta', payload=dict(id=response['id'], source='exec',
                base_instructions=dict(text=request['instructions']))),
                dict(type='turn_context', payload=dict(model=route['model'], effort='high', cwd='/fixture')),
                dict(type='response_item', payload=dict(type='message', role='user',
                    content=visual.native_user_parts(request, '/fixture')))]
            events.extend(dict(type='response_item', payload=copy.deepcopy(row)) for row in response['output'])
            inspected = visual.inspect_rollout(events, request, response['id'], '/fixture', [],
                excluded_images=[control_image('fixture-excluded-control')])
            inspected.update(inspected=True, route_digest=digest(route), runtime=route['runtime'],
                             event_digest=digest(events), rollout_digest=digest(events))
            response['context_evidence'] = inspected
    if 'allowed_control' in output:
        response['usage']['input_tokens'] = 500000
    return response


def assert_turn_fits(case, route, packet, plan, kind):
    from research_harness.review_protocol import canonical_prompt
    request = transport.request_body(route, canonical_prompt('native_math', reading_stage=kind,
        reading_protocol=v7.PROTOCOL)['text'], packet)
    measured = capacity.describe_plan_preflight({}, route, request, plan, kind)
    case.assertTrue(measured['plan_comparison']['fits'], measured)
    case.assertLessEqual(measured['request_measure']['text_characters'],
                         plan['limits']['input_text_characters']['value'])
    return request


def build_scaled_reading_deliveries(quantity, size):
    """Return distinct canonical reading emissions for a large upstream inventory."""
    from unittest.mock import patch
    if quantity != 'units':
        raise ValueError('The v7 delivery factory scales distinct upstream units.')
    artifacts = MemoryArtifacts()
    route = build_reading_route('codex_cli_v1', 'over_limit_expected')
    built = build_shape_inventory(artifacts, count=size)
    native = build_native()
    logical = dict(protocol='exactory-independent-review-v1', role='native_math', dossier_id=None,
                   reviewer_id=native['reviewer'], native=native)
    with patch.object(v7, 'build_inventory', return_value=built):
        packet, chain = v7.prepare_first_assignment({}, artifacts, dict(id='quantity-chain'), route, logical, [])
    plan = capacity.read_chain_capacity_plan(artifacts, chain)
    inventory = json.loads(artifacts.read(chain['inventory']))
    state = json.loads(artifacts.read(chain['state']))
    packets, turns = [], []
    while v7._find_remaining_ranges(inventory, state):
        number = len(turns) + 1
        packet = v7.build_turn_packet(artifacts, route, logical, 'quantity-chain', number, inventory, state,
            plan, capacity.derive_plan_task_limits(plan, 'reading'), 'reading')
        if not packet['reading_turn']['plan']['now']:
            raise AssertionError('A finite v7 quantity fixture did not make progress.')
        packets.append(packet)
        turns.append(v7.project_own_turn(artifacts, route, packet, build_reading_answer(packet),
            'turn-%d' % number, 'attempt-%d' % number, state=state, inventory=inventory))
        state = v7.build_chain_state(inventory, turns, route=route, plan=plan)['state']
    delivered = {row['handle'] for packet in packets for row in packet['reading_turn']['plan']['now']}
    if delivered != {row['handle'] for row in inventory['material']}:
        raise AssertionError('The quantity fixture must deliver every distinct upstream unit.')
    return packets
