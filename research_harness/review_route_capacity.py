"""Route size limits and measured task profiles, outside the pinned transport identity.

These are forecasts, never authentication inputs. A stored capacity plan must pin its own snapshot.
The calibrated token ratios are assumptions measured on the source records, not tokenizer guarantees.
"""
import base64
import copy
import heapq
import json
import math
import os
import struct
from pathlib import Path
import tempfile
from . import review_transport as transport
from .review_capacity_evidence import (DEFAULT_PROBE_INPUT_TOKENS as DEFAULT_INPUT_TOKENS,
                                       describe_capacity_key, has_capacity_key, read_capacity_priors)
from .evidence import digest
POLICY = 'exactory-route-capacity-policy-v1'
VERIFICATION_CALLS_PER_OVER_LIMIT = 20
VERIFICATION_POSITION_OVER_LIMIT = 1
VERIFICATION_MINIMUM_CALLS = 20
VERIFICATION_RECORDED_OUTPUT_LIMIT = 128000
VERIFICATION_SIZE_STOP_CODES = (
    'review_output_over_cap', 'review_input_too_large', 'review_timeout',
    'review_pending_delivery_over_plan', 'review_route_capacity_below_floor',
    'review_chain_projection_over_limit', 'review_response_too_large',
)
PROFILE_PROTOCOL = 'exactory-route-output-profile-v1'
PROFILE_ATTEMPT_LIMIT = 500
CLAUDE_CHARACTERS_PER_TOKEN = 1.36
CODEX_CHARACTERS_PER_TOKEN = 2.10
TOKEN_RATIO_MARGIN = 0.05
COUNT_CARRIED_STATE = True
FRAME_PROBE_EVIDENCE = (
    ('max', 'lists', 4, 4, ('Q1', 'Q3'), 0, 'no effect', 395305, 36127, None),
    ('max', 'record', 4, 4, ('Q1', 'Q3'), 0, 'effect', 587724, 36127, None),
    ('xhigh', 'lists', 2, 2, ('Q1', 'Q3'), 0, 'no effect', 395305, 36127, None),
    ('xhigh', 'record', 2, 2, ('Q1', 'Q3'), 0, 'effect', 587724, 36127, None),
)
FRAME_PROBE_SUMMARY_SHA256 = 'fd031720ed0e8becea9ce34045922339fc840b8753af744c421865759f633f9f'
FRAME_PROBE_FIELDS = (
    'effort',
    'part',
    'calls',
    'valid_observations',
    'quarters',
    'calls_left',
    'reading',
    'largest_text_characters',
    'largest_material_characters',
    'frame_limit',
)
CAPACITY_ADAPTERS = (
    'openai_responses_v1',
    'claude_cli_v1',
    'codex_cli_v1',
    'codex_cli_framed_v1',
    'codex_cli_visual_v1',
)
MEASUREMENT_DATE = '2026-11-13'
MEASUREMENT_SOURCE = '20261113110022-every-open-record-and-review-capacity'
CLAUDE_MODELS_URL = 'https://platform.claude.com/docs/en/models/overview'
CLAUDE_WINDOW_URL = 'https://code.claude.com/docs/en/model-config'
CLAUDE_OUTPUT_URL = 'https://code.claude.com/docs/en/env-vars'
OPENAI_MODELS_URL = 'https://developers.openai.com/api/docs/models/'
IMAGE_INPUTS_URL = 'https://developers.openai.com/api/docs/guides/images-vision'
PDF_INPUTS_URL = 'https://developers.openai.com/api/docs/guides/file-inputs'
BINARY_RULE_DATE = '2026-11-14'


def describe_binary_rules(route):
    """Report rules only for the documented adapter, model and input settings."""
    adapter = route['adapter']
    if adapter not in (transport.ADAPTER, 'codex_cli_visual_v1'):
        return 'unsupported'
    image = dict(input_limits={}, request_limits={}, token_rule=None,
                 estimate=dict(kind='estimate', source_record=MEASUREMENT_SOURCE,
                               largest_request_input_tokens=None))
    if adapter == 'codex_cli_visual_v1':
        image['input_limits'] = dict(
            bytes=_describe_limit(33554432, 'transport', file='research_harness/review_codex_visual_transport.py'),
            pixels=_describe_limit(16777216, 'transport', file='research_harness/review_codex_visual_transport.py'))
        # The API's billing rules do not document the Codex subscription backend.
        image['estimate'].update(largest_bytes=474410, largest_pixels=2236350,
                                 largest_request_bytes=6832558, largest_request_images=19,
                                 largest_request_input_tokens=364458,
                                 basis='whole_request_usage_including_text',
                                 tokens_per_image=364458 / 19,
                                 tokens_per_byte=364458 / 6832558,
                                 tokens_per_pixel=364458 / 41138674)
        return dict(image=image)
    if route['endpoint'] != 'https://api.openai.com/v1/responses':
        return dict(image=image, pdf=copy.deepcopy(image))
    image['request_limits'] = dict(bytes=_describe_limit(512000000, 'documented', url=IMAGE_INPUTS_URL, date=BINARY_RULE_DATE),
                                   count=_describe_limit(1500, 'documented', url=IMAGE_INPUTS_URL, date=BINARY_RULE_DATE))
    if route['endpoint'] == 'https://api.openai.com/v1/responses' and route['model'] == 'gpt-6-astra':
        image['input_limits']['processed_patches'] = _describe_limit(30000, 'documented', url=IMAGE_INPUTS_URL, date=BINARY_RULE_DATE)
        image['token_rule'] = dict(source='documented', url=IMAGE_INPUTS_URL, date=BINARY_RULE_DATE,
                                   rule='patch_budget', patch_pixels=32, multiplier=1.2,
                                   detail_budgets=dict(low=256, high=2500, original=30000, auto=30000),
                                   maximum_dimension=65535, rounding_margin_tokens=1)
    pdf = dict(input_limits=dict(bytes=_describe_limit(50000000, 'documented', url=PDF_INPUTS_URL,
                                                      exclusive=True, date=BINARY_RULE_DATE)),
               request_limits=dict(bytes=_describe_limit(50000000, 'documented', url=PDF_INPUTS_URL, date=BINARY_RULE_DATE)),
               token_rule=None, estimate=dict(kind='estimate', source_record=MEASUREMENT_SOURCE,
                                              largest_request_input_tokens=None))
    return dict(image=image, pdf=pdf)


def _read_binary_parts(route, request):
    if route['adapter'] == 'codex_cli_visual_v1':
        return [('image', row['data_url'], 'high') for row in request.get('exactory_image_inputs', [])]
    if route['adapter'] != transport.ADAPTER:
        return []
    return [('pdf' if part['type'] == 'input_file' else 'image',
             part.get('file_data') or part.get('image_url'), part.get('detail', 'auto'))
            for message in request['input'] if isinstance(message['content'], list)
            for part in message['content'] if part['type'] in ('input_file', 'input_image')]


def _measure_binary_parts(route, request, rules):
    measured = []
    for kind, url, detail in _read_binary_parts(route, request):
        rule = rules[kind]
        data = base64.b64decode(url.split(',', 1)[1]) if isinstance(url, str) and url.startswith('data:') else None
        values = dict(bytes=len(data) if data is not None else None)
        if data is not None and data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
            width, height = struct.unpack('>II', data[16:24])
            values.update(width=width, height=height, pixels=width * height)
        token_rule = rule['token_rule']
        tokens = token_rule['tokens_upper'] if token_rule is not None and token_rule['rule'] == 'fixed_budget' else None
        if token_rule is not None and token_rule['rule'] == 'patch_budget' and detail in token_rule['detail_budgets'] and values.get('pixels'):
            # A full budget bounds any documented resizing; one extra token covers billing rounding.
            tokens = math.ceil(token_rule['detail_budgets'][detail] * token_rule['multiplier']) + token_rule['rounding_margin_tokens']
        if token_rule is not None and token_rule['rule'] == 'patch_budget' and detail in ('auto', 'original') and values.get('pixels'):
            scale = min(1, token_rule['maximum_dimension'] / max(width, height))
            patch = token_rule['patch_pixels']
            values['processed_patches'] = math.ceil(math.ceil(width * scale) / patch) * math.ceil(math.ceil(height * scale) / patch)
        over = any(values.get(name) is not None and
                   (values[name] >= limit['value'] if limit.get('exclusive') else values[name] > limit['value'])
                   for name, limit in rule['input_limits'].items())
        measured.append(dict(kind=kind, detail=detail, **values, tokens_upper=tokens,
                             token_rule=copy.deepcopy(token_rule), over_limit=over,
                             estimate=copy.deepcopy(rule['estimate'])))
    return measured
# API limits are documented; local Codex input room is established by verified sends.
MODEL_LIMITS = (
    ('claude-opus-5-5', 1000000, 967000, 128000, CLAUDE_MODELS_URL),
    ('claude-fable-5-2', 1000000, 967000, 128000, CLAUDE_MODELS_URL),
    (
        'claude-fable-5-1',
        1000000,
        967000,
        128000,
        'https://platform.claude.com/docs/en/models/fable-5-1/overview',
    ),
    ('claude-sonnet-5-5', 1000000, 967000, 128000, CLAUDE_MODELS_URL),
    ('gpt-6-astra', 1050000, 922000, 128000, OPENAI_MODELS_URL + 'gpt-6-astra'),
    ('gpt-6.1-sol', 1050000, 922000, 128000, OPENAI_MODELS_URL + 'gpt-6.1-sol'),
    ('gpt-6-luna', 1050000, 922000, 128000, OPENAI_MODELS_URL + 'gpt-6-luna'),
)
# Legacy D1 values remain available for calibration and reports, not as unkeyed admission.
VERIFIED_INPUT_TOKENS = tuple(
    (row['key']['adapter'], row['key']['model'], row['input_tokens'])
    for row in read_capacity_priors()
    if has_capacity_key(row) and row['key']['adapter'].startswith('codex_cli_')
)


def describe_route_class(route):
    adapter = route['adapter']
    effort = 'high' if adapter.startswith('codex_cli_') else route['configuration'].get('effort') if adapter == 'claude_cli_v1' else None
    return dict(adapter=adapter, model=route['model'], effort=effort)


def _describe_limit(value, source, **evidence):
    return dict(value=value, source=source, **evidence)


def select_capacity_probe_size(records, route, request_tokens, *, artifacts=None):
    key = describe_capacity_key(route)
    probes = [row for row in records.get('review_route_probe', {}).values()
              if key is not None and 'capacity' in row and describe_capacity_key(records['review_route'][row['route_id']]) == describe_capacity_key(route)]
    last = max(probes, key=lambda row: row['recorded_revision'], default=None)
    if last is not None:
        return request_tokens + route['configuration']['max_output_tokens'] if last.get('capacity_verified') else max(1, last['capacity']['input_tokens'] // 2)
    from .review_capacity_evidence import read_capacity_evidence
    return read_capacity_evidence(route, records, artifacts=artifacts)['probe_input_tokens']


def build_capacity_probe_request(route, prompt, packet, tokens):
    """Fill only evidence, keeping both controls in the unchanged three-member packet."""
    from .errors import ResearchError
    if route['adapter'] == 'codex_cli_visual_v1':
        raise ResearchError('invalid_review_capacity_probe', 'A capacity probe requires a text adapter.')
    pattern = 'capacity!probe:0123;'
    def build(size, use_utf8=False):
        # Four-byte code points and a one-code-point tail represent each byte size exactly.
        packet['evidence'] = ('🧪' * (size // 4) + ('', '!', 'é', '界')[size % 4] if use_utf8 else
            (pattern * ((size + len(pattern) - 1) // len(pattern)))[:size])
        request = transport.request_body(route, prompt, packet)
        return request, measure_request(route, request)
    _, empty = build(0)
    limit = describe_route_capacity(route)['input_characters']['value']
    room = max(0, limit - empty['characters']) if limit is not None else tokens * 3
    for use_utf8 in (False, True):
        low, high = 0, min(room * (4 if use_utf8 else 1), tokens * (4 if use_utf8 else 3))
        while low <= high:
            middle = (low + high) // 2
            try:
                request, measure = build(middle, use_utf8)
            except ResearchError as error:
                if error.code != 'review_input_too_large':
                    raise
                high = middle - 1
                continue
            if limit is not None and measure['characters'] > limit:
                high = middle - 1
            elif measure['tokens_upper'] == tokens:
                return request
            elif measure['tokens_upper'] < tokens:
                low = middle + 1
            else:
                high = middle - 1
    raise ResearchError('invalid_review_capacity_probe', 'The requested probe size could not be constructed within this transport.',
                        dict(input_tokens=tokens, character_limit=limit))


def scale_capacity_probe_size(records, artifacts, route, tokens):
    """Keep the requested token target while correcting padding from an exact earlier send."""
    key = describe_capacity_key(route)
    previous = max((row for row in records.get('review_route_probe', {}).values()
                    if key is not None and row.get('capacity_verified') and (row.get('usage', {}).get('input_tokens') or 0) > 0
                    and describe_capacity_key(records['review_route'][row['route_id']]) == key),
                   key=lambda row: row['recorded_revision'], default=None)
    if previous is None:
        return tokens
    request = json.loads(artifacts.read(previous['request']))
    measured = measure_request(route, request)['tokens_upper']
    actual = previous['usage']['input_tokens']
    return (tokens * measured + actual - 1) // actual


def describe_route_capacity(route, profile=None, *, records=None, artifacts=None):
    """Keep input-evidence keys separate from the class of the output profile."""
    from . import review_capacity_evidence
    adapter, config = route['adapter'], route['configuration']
    provider_prefix = 'claude-' if adapter == 'claude_cli_v1' else 'gpt-'
    model = next((row for row in MODEL_LIMITS if row[0] == route['model']
                  and row[0].startswith(provider_prefix)), None)
    output = min(config['max_output_tokens'], model[3] if model else 128000) if adapter == 'claude_cli_v1' else config['max_output_tokens']
    output_limit = _describe_limit(output, 'route_configuration', field='max_output_tokens')
    if adapter == 'claude_cli_v1' and config['max_output_tokens'] >= output:
        output_limit = _describe_limit(output, 'documented', url=model[4] if model else CLAUDE_OUTPUT_URL, date=MEASUREMENT_DATE)
    threshold_models = tuple(row[0] for row in MODEL_LIMITS if row[2] == model[2]) if adapter == 'claude_cli_v1' and model is not None else ()
    evidence = review_capacity_evidence.read_capacity_evidence(route, {} if records is None else records, artifacts=artifacts,
                                                               threshold_models=threshold_models, reservation=output)
    observed = _describe_limit(evidence['input_tokens'], 'observed', key=evidence['key'], evidence=evidence['evidence'],
                               source_record=MEASUREMENT_SOURCE, date=MEASUREMENT_DATE)
    documented = None
    if adapter == 'claude_cli_v1':
        from .review_claude_transport import INPUT_LIMIT_CHARACTERS
        characters = _describe_limit(INPUT_LIMIT_CHARACTERS, 'transport', file='research_harness/review_claude_transport.py', constant='INPUT_LIMIT_CHARACTERS')
        if model is not None:
            documented = _describe_limit(max(0, model[2] - output), 'documented', threshold=model[2], url=CLAUDE_WINDOW_URL, date=MEASUREMENT_DATE)
            tokens = dict(observed if observed['value'] <= documented['value'] else documented)
        else:
            tokens = dict(observed) if evidence.get('verified_capacity_probe') else _describe_limit(
                max(0, 200000 - output), 'default', url=CLAUDE_WINDOW_URL, date=MEASUREMENT_DATE)
    elif adapter in transport.LOCAL_ADAPTERS:
        if adapter == 'codex_cli_visual_v1':
            from .review_codex_visual_transport import MAX_USER_TEXT
            characters = _describe_limit(MAX_USER_TEXT, 'transport', file='research_harness/review_codex_visual_transport.py', constant='MAX_USER_TEXT')
        else:
            characters = _describe_limit(transport.CODEX_INPUT_LIMIT_CHARS, 'transport', file='research_harness/review_transport.py', constant='CODEX_INPUT_LIMIT_CHARS')
        tokens = dict(observed)
    else:
        characters = _describe_limit(None, 'transport', file='research_harness/review_transport.py')
        if model is not None:
            documented = _describe_limit(max(0, min(model[2] or model[1], model[1] - output)), 'documented', url=model[4], date=MEASUREMENT_DATE)
            tokens = dict(documented)
        else:
            tokens = dict(observed)
    reservation_basis = 'compaction_threshold' if adapter == 'claude_cli_v1' and model is not None else 'model_window' if tokens['source'] == 'documented' else 'verified_input' if tokens['source'] == 'observed' else 'adapter_default'
    if evidence.get('verified_capacity_probe'):
        tokens['probe_id'] = evidence['verified_capacity_probe']['id']
    tokens.update(output_reservation_tokens=output, reservation_basis=reservation_basis)
    calibrated = model is not None and (adapter == 'claude_cli_v1' or any((name, name_model) == (adapter, route['model']) for name, name_model, _ in VERIFIED_INPUT_TOKENS))
    ratio = CLAUDE_CHARACTERS_PER_TOKEN if adapter == 'claude_cli_v1' else CODEX_CHARACTERS_PER_TOKEN
    return dict(protocol=POLICY, listed=model is not None, route_class=describe_route_class(route),
                input_text_characters=_describe_limit(math.floor(tokens['value'] * ratio) if calibrated else tokens['value'], 'estimated',
                                                      characters_per_token=ratio if calibrated else None, margin=TOKEN_RATIO_MARGIN if calibrated else None),
                input_characters=characters, input_tokens=tokens, input_limits=dict(documented=documented, observed=observed),
                capacity_evidence=evidence, output_tokens=output_limit, effort=describe_route_class(route)['effort'],
                request_measure='calibrated' if calibrated else 'bytes', provider_enforces_output=not adapter.startswith('codex_cli_'),
                binary_inputs=describe_binary_rules(route),
                raises_capacity=['Add documented model limits with their URLs.', 'Record a verified capacity probe under this execution key to raise its observed input limit.'])


def _read_text_parts(content):
    if isinstance(content, str):
        return content
    return ''.join(
        (part['text'] for part in content if part.get('type') in ('input_text', 'output_text', 'text')),
    )


def measure_request(route, request, profile=None, *, records=None, artifacts=None):
    """Bound the exact text and every binary input in the same request."""
    capacity = describe_route_capacity(route, profile, records=records, artifacts=artifacts)
    binaries = _measure_binary_parts(route, request, capacity['binary_inputs'])
    adapter = transport._local_adapter(route)
    representation = 'adapter_packet_text' if adapter is not None else 'serialized_request'
    native_images = None
    if route['adapter'] == 'codex_cli_visual_v1':
        from . import review_codex_transport as codex
        environment = codex.clean_environment(os.environ)
        directory = Path(environment.get('TMPDIR') or tempfile.gettempdir()).resolve() / ('review-visual-' + 'x' * 8)
        native_images = len(binaries)
        packet = adapter.packet_text(request) + ''.join(
            ('<image name=[Image #' + str(index) + '] path="' + str(directory / ('image-' + str(index).zfill(3) + '.png')) + '"></image>' for index in range(1, native_images + 1)),
        )
        representation = 'native_user_text'
    elif adapter is not None:
        packet = adapter.packet_text(request)
    else:
        packet = ''.join((_read_text_parts(item['content']) for item in request['input']))
    characters = len(packet) if adapter is not None else len(json.dumps(request, sort_keys=True, ensure_ascii=False))
    text = request.get('instructions', '') + packet
    non_ascii_share = (len(text) - len(text.encode('ascii', errors='ignore'))) / max(1, len(text))
    calibrated = capacity['request_measure'] == 'calibrated' and non_ascii_share <= 0.05
    utf8_bytes = len(text.encode('utf-8'))
    ratio = CLAUDE_CHARACTERS_PER_TOKEN if route['adapter'] == 'claude_cli_v1' else CODEX_CHARACTERS_PER_TOKEN
    tokens_upper = math.ceil(len(text) / ratio) if calibrated else utf8_bytes
    limit = capacity['input_characters']['value']
    result = dict(
        characters=characters,
        text_characters=len(text),
        utf8_bytes=utf8_bytes,
        non_ascii_share=non_ascii_share,
        tokens_upper=tokens_upper,
        measure='calibrated' if calibrated else 'bytes',
        character_limit=limit,
        token_limit=capacity['input_tokens']['value'],
        representation=representation,
        binary_inputs=binaries,
        text_tokens_upper=tokens_upper,
        binary_tokens_upper=0,
        output_reservation_tokens=capacity['input_tokens']['output_reservation_tokens'],
        token_estimate=True,
        ratio_margin=TOKEN_RATIO_MARGIN if calibrated else None,
        characters_per_token=ratio if calibrated else None,
        fits=(limit is None or characters <= limit) and tokens_upper <= capacity['input_tokens']['value'],
    )
    if native_images is not None:
        result['native_images'] = native_images
    reason = None
    if binaries:
        for kind, rule in capacity['binary_inputs'].items():
            selected = [row for row in binaries if row['kind'] == kind]
            if not selected:
                continue
            totals = dict(count=len(selected), bytes=sum(row['bytes'] or 0 for row in selected))
            if kind == 'image' and route['adapter'] == transport.ADAPTER:
                totals['bytes'] = len(json.dumps(request, ensure_ascii=False).encode('utf-8'))
            if any(totals[name] > limit['value'] for name, limit in rule['request_limits'].items()):
                reason = 'binary_input_over_measure'
        if any(row['over_limit'] for row in binaries):
            reason = 'binary_input_over_measure'
        unknown = any(row['tokens_upper'] is None for row in binaries)
        result['binary_tokens_upper'] = None if unknown else sum(row['tokens_upper'] for row in binaries)
        result['tokens_upper'] = None if unknown else tokens_upper + result['binary_tokens_upper']
        if unknown:
            reason = reason or 'binary_input_without_bound'
        elif result['tokens_upper'] > result['token_limit']:
            reason = reason or 'binary_input_over_measure'
    if reason is not None:
        result.update(fits=False, reason=reason)
    return result


CAPACITY_STOP_MESSAGE = 'This review has no bounded delivery on the selected route. Use a listed next step when one is available.'
UNSERVED_REVIEW_MESSAGE = 'No registered verified route serves this review; it is outside the solved part on the routes currently available.'
CAPACITY_OWNER_DECISION = 'Whether to authorize a listed recovery step or leave this review outside the solved part.'
UNSOLVED_REVIEW_ITEMS = (
    ('https_parts', 'https-series', 'Delivery in parts on the HTTPS route.'),
    ('binary_inputs', 'visual-series', 'Binary inputs without a documented or counted token bound, or above one request.'),
    ('body_without_text_form', 'visual-series', 'A body without a text form above one read turn.'),
    ('staged_scale', None, 'A staged review above its supported scale; the slot retains its ordinary delivery.'),
)


def describe_unsolved_reviews():
    from .review_protocol import ROLES
    result = {role: [dict(reason=reason, unit=unit, message=message)
                     for reason, unit, message in UNSOLVED_REVIEW_ITEMS] for role in ROLES}
    for role in ('manuscript', 'standalone'):
        result[role].append(dict(reason='no_parts_delivery', unit='paper-series',
                                 message='This role has no delivery in parts.'))
    result['native_math'].append(dict(reason='math_reading_v1', unit='reading-math-v2',
                                      message='Math reading protocol v1 has no finite final projection.'))
    return result


def _can_deliver_parts(assignment, packet, route):
    from .review_parts_roles import find_adapter
    return (route['adapter'] in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1')
            and find_adapter(assignment, packet) is not None)


def _build_serving_parts(records, artifacts, assignment, route, packet, prompt):
    from . import review_parts, review_prompt_paragraphs
    from .review_staged_protocol import ControllerArtifacts
    staged = ControllerArtifacts(artifacts)
    parameters = prompt.get('parameters', {})
    base = dict(orchestration=parameters.get('orchestration'), repair=bool(parameters.get('context_repair')),
                excerpts='reviewer_excerpts' in parameters, common_version=parameters.get('common_prompt_version'))
    base.update(review_prompt_paragraphs.read_paragraph_parameters(prompt))
    if assignment['role'] in ('bar', 'slate', 'result'):
        base['excerpts'] = True
    planned = review_parts.prepare_packet(records, staged, dict(assignment, route_id=route['id']),
        route, copy.deepcopy(packet), prompt, [], base_prompt_parameters=base)
    if review_parts.find_kind(planned) is None:
        return None
    turn_prompt = review_parts.build_turn_prompt(assignment['role'], base, planned[review_parts.KEY])
    return transport.request_body(route, turn_prompt['text'], planned, [])



def _build_serving_repair(records, artifacts, assignment, parent, route):
    """Measure the reassessment that the preserved-slot steps will create, with buffered writes."""
    from . import review_context_repair as repair_context, review_protocol, review_parts, review_parts_repair
    from . import review_prompt_paragraphs, bar_sources
    from .evaluation import Evaluation
    from .review_staged_protocol import ControllerArtifacts
    from .review_delivery_notice import prepare_notice
    from .review_plan_steps import CAPACITY_ROUTE_RETIRE_REASON
    if parent.get('staged_review'):
        return None
    event = dict(id='capacity-retirement', assignment_id=parent['id'], kind='route_retired',
                 source='owner', reason=CAPACITY_ROUTE_RETIRE_REASON, evidence=[],
                 recorded_revision=parent['recorded_revision'] + 1)
    prospective = dict(records, review_context_event=dict(records.get('review_context_event', {}),
                        **{event['id']: event}))
    staged = Evaluation.of(prospective, ControllerArtifacts(artifacts))
    chain = repair_context._exposure_lineage(prospective, parent)
    roles = repair_context.ROLES + ('adjudicator',)
    scientific = repair_context.collect_scientific_history(prospective, staged, chain, roles)
    state = review_protocol.assignment_state(prospective, staged, parent['id'])
    events = repair_context.cause_events(prospective, {row['id'] for row in chain})
    repair = dict(predecessor=parent, slot_id=repair_context.slot_id(prospective, parent),
        packet=next(row['packet'] for row in scientific if row['assignment_id'] == parent['id']),
        predecessor_binding=repair_context.bind_predecessor(prospective, parent, state, scientific),
        historical_reviews=repair_context._reviews(prospective, {row['id'] for row in chain if row['role'] == parent['role']}),
        scientific_history=scientific, event_ids=[row['id'] for row in events], probe_id='capacity-probe',
        cause=[{key: row[key] for key in ('id', 'kind', 'source', 'reason')} for row in events])
    value = dict(parent, id=assignment['id'], route_id=route['id'], context={})
    if 'subject' in assignment:
        value['subject'] = assignment['subject']
    with transport.text_only_delivery(route['adapter'] in transport.TEXT_ONLY_ADAPTERS):
        packet = review_protocol._repair_packet(prospective, staged, value, repair)
    parameters = dict(repair=parent['role'] != 'adjudicator',
        orchestration='problem_creation' if value.get('subject', {}).get('kind') == 'problem' else None,
        excerpts=parent['role'] in bar_sources.ROLES)
    parameters.update(review_prompt_paragraphs.select_prompt_parameters(parent['role'], packet))
    prompt = review_protocol.canonical_prompt(parent['role'], **parameters)
    packet = review_parts_repair.prepare_packet(prospective, staged, value, route, packet, prompt, [],
        base_prompt_parameters=parameters, slot_id=repair['slot_id'], repair=repair)
    if review_parts.find_kind(packet) is not None:
        prompt = review_parts.build_turn_prompt(parent['role'], parameters, packet[review_parts.KEY])
    packet, prompt, history = prepare_notice(prospective, staged, route,
        dict(value, session_id=value['id'], history_assignment_id=None), packet, prompt, [])
    return transport.request_body(route, prompt['text'], packet, history)


def find_serving_routes(records, role, packet, *, artifacts, assignment, prompt, history=()):
    """List verified routes for this role and input without sending or changing a record."""
    from .errors import ResearchError
    from .review_protocol import route_state, retired_routes, canonical_prompt, _require_route_runtime
    from .review_excerpts import read_sent_packet
    from .review_problem_parts import preserves_ordinary_packet
    from .review_plan_steps import find_capacity_parent, select_route_steps
    parent = find_capacity_parent(records, assignment, packet)
    result = []
    prompt_record = prompt if isinstance(prompt, dict) else dict(canonical_prompt(role), text=prompt)
    if _find_body_without_text(records, packet) is not None:
        return result
    visual = bool(packet.get('visual_source_delivery') or packet.get('visual_context_repair'))
    for route in records.get('review_route', {}).values():
        if route['id'] in retired_routes(records):
            continue
        if visual and route['adapter'] != 'codex_cli_visual_v1':
            continue
        if route_state(records, artifacts, route['id'])['context_status'] != 'verified_for_route':
            continue
        try:
            _require_route_runtime(route)
        except ResearchError:
            continue
        delivery = 'single'
        candidate_packet = copy.deepcopy(packet)
        profile = read_output_profile(records, route)
        try:
            if parent is not None and 1 in select_route_steps(records, artifacts, parent):
                request = _build_serving_repair(records, artifacts, assignment, parent, route)
                if request is not None:
                    measure = measure_request(route, request, profile, records=records, artifacts=artifacts)
                    if measure['fits']:
                        result.append(dict(route_id=route['id'], delivery='reassessment', measure=measure))
                continue
            try:
                request = transport.request_body(route, prompt_record['text'], packet, history)
            except ResearchError as error:
                if error.code != 'review_input_too_large':
                    raise
                request = None
            if request is not None:
                sent = read_sent_packet(request) or packet
                if route['adapter'] in transport.TEXT_ONLY_ADAPTERS:
                    omitted = {row['artifact']['sha256']: row for row in sent.get('evidence', [])
                               if row.get('encoding') == 'omitted'}
                    candidate_packet['evidence'] = [omitted.get(row['artifact']['sha256'], row)
                        if row.get('encoding') == 'base64' else row for row in packet.get('evidence', [])]
                if _find_body_without_text(records, sent) is not None:
                    continue
                if not preserves_ordinary_packet(artifacts, candidate_packet, request):
                    request = None
                elif not measure_request(route, request, profile, records=records, artifacts=artifacts)['fits']:
                    request = None
            if request is None:
                if not _can_deliver_parts(assignment, packet, route):
                    continue
                request = _build_serving_parts(records, artifacts, assignment, route, candidate_packet, prompt_record)
                if request is None:
                    continue
                delivery = 'parts'
            measure = measure_request(route, request, profile, records=records, artifacts=artifacts)
            if measure['fits']:
                result.append(dict(route_id=route['id'], delivery=delivery, measure=measure))
        except ResearchError:
            continue
    return result


def _find_body_without_text(records, packet):
    from .review_parts import READ_CHARACTERS
    delivered = {row['artifact']['sha256'] for row in packet.get('evidence', []) if row.get('encoding') == 'utf-8'}
    represented = {capture['original']['sha256']
                   for work in records.get('work', {}).values() for capture in work.get('fulltexts', [])
                   if capture.get('original') and capture.get('text') and capture['text']['sha256'] in delivered}
    return next((row['artifact'] for row in packet.get('evidence', [])
                 if (row.get('delivery') == transport.RESULT_BYTES_DELIVERY or row.get('encoding') == 'omitted')
                 and 4 * ((row['artifact']['size'] + 2) // 3) + 2 > READ_CHARACTERS
                 and row['artifact']['sha256'] not in represented), None)


def require_covered_assignment(records, artifacts, assignment, packet, prompt, request=None, history=()):
    """Refuse an uncovered assignment before any review call or assignment is recorded."""
    from .errors import ResearchError
    from .review_plan_steps import describe_capacity_steps
    from . import openness_reading
    prompt_text = prompt['text'] if isinstance(prompt, dict) else prompt
    body = _find_body_without_text(records, packet)
    if request is None:
        if body is None:
            return
        request = transport.request_body(records['review_route'][assignment['route_id']], prompt_text, packet, history)
    route = records['review_route'][assignment['route_id']]
    # Running protocols retain their character admission; a zero-capacity runtime still needs its probe.
    earlier_reading = openness_reading.find_protocol(packet) is not None
    capacity = describe_route_capacity(route, records=records, artifacts=artifacts)
    measure = measure_request(route, request, records=records, artifacts=artifacts)
    if body is None and (measure['fits'] or (earlier_reading and measure['token_limit'] > 0 and not measure['binary_inputs']
                          and (measure['character_limit'] is None or measure['characters'] <= measure['character_limit']))):
        return
    reason = 'body_without_text_form' if body is not None else measure.get('reason', 'no_parts_delivery')
    serving = find_serving_routes(records, assignment['role'], packet, artifacts=artifacts,
                                  assignment=assignment, prompt=prompt, history=history)
    probe = None
    unavailable_probe = None
    if (reason == 'no_parts_delivery' and route['adapter'] != 'codex_cli_visual_v1'
            and capacity['input_tokens']['source'] in ('observed', 'default')):
        probe = select_capacity_probe_size(records, route, measure['tokens_upper'] or measure['text_tokens_upper'], artifacts=artifacts)
        from .review_protocol import build_probe_prompt
        size = scale_capacity_probe_size(records, artifacts, route, probe)
        try:
            build_capacity_probe_request(route, build_probe_prompt(route),
                dict(allowed_control='a'*32, evidence_control='b'*32, evidence=''), size)
        except ResearchError as error:
            if error.code != 'invalid_review_capacity_probe':
                raise
            unavailable_probe = dict(input_tokens=probe, measure_tokens=size,
                                     code=error.code, message=error.message, details=error.details)
            probe = None
    details = dict(reason=reason, measure=measure, capacity_source=capacity['input_tokens']['source'],
                   serving_routes=serving, owner_decides=CAPACITY_OWNER_DECISION,
                   then=describe_capacity_steps(records, artifacts, assignment, serving, probe, body=body, packet=packet))
    if unavailable_probe is not None:
        details['probe_unavailable'] = unavailable_probe
    details['closing_unit'] = ('capacity-probe' if probe is not None or unavailable_probe is not None else 'visual-series'
        if reason != 'no_parts_delivery' else 'paper-series' if assignment['role'] in ('manuscript', 'standalone')
        else 'https-series' if route['adapter'] == transport.ADAPTER else 'single-request-series')
    if not serving:
        details['coverage'] = UNSERVED_REVIEW_MESSAGE
    raise ResearchError('review_route_capacity_below_floor', CAPACITY_STOP_MESSAGE, details)

TASK_LEVELS = (15000, 30000, 60000, 120000, 240000, 480000, None)
TURN_KINDS = (
    'reading',
    'read',
    'combine',
    'check',
    'final',
    'request',
    'single',
    'staged',
    'probe',
    'correction',
)
PROFILE_ROW_FIELDS = (
    'kind',
    'task_level',
    'attempts',
    'completed_attempts',
    'other_attempts',
    'measurement_attempts',
    'at_or_over_limit',
    'thinking_attempts',
    'largest_thinking_tokens',
    'largest_output_tokens',
    'largest_answer_tokens',
    'cli_versions',
    'source',
    'date',
    'thinking_method',
)
# Frozen numeric measurements only; fields and per-CLI date ranges are named above.
OUTPUT_PROFILE_PRIORS = (
    ('claude_cli_v1', 'claude-fable-5-1', 'high', 'single', 30000, 1, 1, 0, 0, 0, 0, None, 1332, None, (('2.1.306 (Claude Code)', 1, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-fable-5-1', 'high', 'single', 60000, 1, 1, 0, 0, 0, 0, None, 11377, None, (('2.1.307 (Claude Code)', 1, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-fable-5-1', 'high', 'single', 120000, 1, 1, 0, 0, 0, 0, None, 8192, None, (('2.1.307 (Claude Code)', 1, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-fable-5-1', 'high', 'single', 480000, 6, 6, 0, 0, 0, 0, None, 14489, None, (('2.1.306 (Claude Code)', 1, None, None), ('2.1.307 (Claude Code)', 5, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-fable-5-1', 'high', 'single', None, 8, 6, 2, 0, 0, 0, None, 23696, None, ((None, 1, None, None), ('2.1.306 (Claude Code)', 4, None, None), ('2.1.307 (Claude Code)', 3, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-fable-5-1', 'high', 'probe', 15000, 2, 2, 0, 0, 0, 0, None, 199, None, (('2.1.306 (Claude Code)', 2, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-fable-5-1', 'max', 'probe', 15000, 3, 3, 0, 0, 0, 0, None, 408, None, (('2.1.306 (Claude Code)', 3, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'high', 'single', 30000, 2, 2, 0, 0, 0, 0, None, 1808, None, (('2.1.306 (Claude Code)', 2, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'high', 'single', 120000, 2, 2, 0, 0, 0, 0, None, 11371, None, (('2.1.310 (Claude Code)', 2, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'high', 'single', 240000, 5, 5, 0, 0, 0, 0, None, 11705, None, (('2.1.310 (Claude Code)', 5, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'high', 'single', 480000, 5, 5, 0, 0, 0, 0, None, 7591, None, (('2.1.306 (Claude Code)', 1, None, None), ('2.1.310 (Claude Code)', 4, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'high', 'single', None, 10, 9, 1, 0, 0, 0, None, 25787, None, ((None, 1, None, None), ('2.1.306 (Claude Code)', 7, None, None), ('2.1.307 (Claude Code)', 2, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'high', 'probe', 15000, 3, 3, 0, 0, 0, 0, None, 126, None, (('2.1.306 (Claude Code)', 2, None, None), ('2.1.310 (Claude Code)', 1, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'reading', 120000, 10, 7, 2, 0, 1, 7, 96500, 110979, None, (('2.1.314 (Claude Code)', 5, None, None), ('2.1.316 (Claude Code)', 5, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'reading', 240000, 27, 23, 2, 0, 2, 23, 110150, 123372, None, (('2.1.314 (Claude Code)', 8, None, None), ('2.1.316 (Claude Code)', 19, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'read', None, 19, 19, 0, 0, 0, 19, 101150, 123721, None, (('2.1.316 (Claude Code)', 19, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'final', None, 1, 1, 0, 0, 0, 1, 95350, 110191, None, (('2.1.316 (Claude Code)', 1, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'request', 240000, 3, 2, 1, 0, 0, 2, 87100, 98481, None, (('2.1.322 (Claude Code)', 3, '2026-11-12', '2026-11-12'),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'request', 480000, 4, 1, 0, 0, 3, 1, 55900, 61747, None, (('2.1.314 (Claude Code)', 1, None, None), ('2.1.322 (Claude Code)', 3, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'request', None, 23, 16, 1, 0, 6, 16, 108800, 121467, None, (('2.1.314 (Claude Code)', 7, None, None), ('2.1.316 (Claude Code)', 16, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'single', 30000, 2, 2, 0, 0, 0, 2, 15850, 18192, None, (('2.1.322 (Claude Code)', 2, '2026-11-12', '2026-11-12'),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'single', 60000, 4, 2, 0, 0, 2, 0, None, 93818, None, (('2.1.310 (Claude Code)', 4, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'single', 120000, 6, 5, 0, 0, 1, 0, None, 115766, None, (('2.1.310 (Claude Code)', 6, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'single', 240000, 8, 8, 0, 0, 1, 0, None, 130982, None, (('2.1.310 (Claude Code)', 8, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'single', 480000, 12, 12, 0, 0, 0, 0, None, 117571, None, (('2.1.310 (Claude Code)', 12, None, None),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'single', None, 10, 6, 3, 0, 1, 0, None, 103372, None, ((None, 3, None, None), ('2.1.306 (Claude Code)', 1, None, None), ('2.1.310 (Claude Code)', 6, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'probe', 15000, 15, 15, 0, 0, 0, 6, 517, 633, None, (('2.1.306 (Claude Code)', 3, None, None), ('2.1.310 (Claude Code)', 6, None, None), ('2.1.314 (Claude Code)', 2, None, None), ('2.1.316 (Claude Code)', 2, None, None), ('2.1.322 (Claude Code)', 2, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', 60000, 3, 3, 0, 0, 0, 3, 31850, 40406, None, (('2.1.316 (Claude Code)', 2, None, None), ('2.1.317 (Claude Code)', 1, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', 120000, 73, 72, 1, 0, 0, 72, 51000, 62066, None, (('2.1.316 (Claude Code)', 30, None, None), ('2.1.317 (Claude Code)', 38, None, None), ('2.1.318 (Claude Code)', 5, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', 240000, 244, 232, 12, 0, 0, 232, 37850, 50297, None, (('2.1.316 (Claude Code)', 63, None, None), ('2.1.317 (Claude Code)', 108, None, None), ('2.1.318 (Claude Code)', 50, None, None), ('2.1.322 (Claude Code)', 15, '2026-11-12', '2026-11-12'), ('2.1.323 (Claude Code)', 8, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', 480000, 163, 158, 5, 0, 0, 158, 56500, 66578, None, (('2.1.322 (Claude Code)', 99, '2026-11-12', '2026-11-12'), ('2.1.323 (Claude Code)', 64, '2026-11-12', '2026-11-13')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', None, 27, 27, 0, 0, 0, 27, 36750, 51314, None, (('2.1.323 (Claude Code)', 27, '2026-11-13', '2026-11-13'),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'read', None, 191, 180, 11, 0, 0, 180, 53450, 68664, None, ((None, 1, None, None), ('2.1.316 (Claude Code)', 93, None, None), ('2.1.318 (Claude Code)', 95, None, None), ('2.1.322 (Claude Code)', 2, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'combine', 480000, 1, 1, 0, 0, 0, 1, 17300, 25926, None, (('2.1.322 (Claude Code)', 1, '2026-11-12', '2026-11-12'),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'check', None, 1, 1, 0, 0, 0, 1, 62200, 76858, None, (('2.1.322 (Claude Code)', 1, '2026-11-12', '2026-11-12'),), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'final', None, 13, 13, 0, 0, 0, 13, 47250, 72468, None, (('2.1.316 (Claude Code)', 4, None, None), ('2.1.317 (Claude Code)', 4, None, None), ('2.1.318 (Claude Code)', 1, None, None), ('2.1.322 (Claude Code)', 3, '2026-11-12', '2026-11-12'), ('2.1.323 (Claude Code)', 1, '2026-11-13', '2026-11-13')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'request', 120000, 5, 5, 0, 0, 0, 5, 7250, 12309, None, (('2.1.316 (Claude Code)', 3, None, None), ('2.1.322 (Claude Code)', 2, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'request', 240000, 11, 11, 0, 0, 0, 11, 25450, 35221, None, (('2.1.316 (Claude Code)', 1, None, None), ('2.1.317 (Claude Code)', 2, None, None), ('2.1.322 (Claude Code)', 8, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'request', 480000, 21, 21, 0, 0, 0, 21, 46600, 64400, None, (('2.1.316 (Claude Code)', 5, None, None), ('2.1.317 (Claude Code)', 4, None, None), ('2.1.322 (Claude Code)', 12, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'request', None, 91, 91, 0, 0, 0, 91, 62650, 84151, None, (('2.1.316 (Claude Code)', 29, None, None), ('2.1.317 (Claude Code)', 23, None, None), ('2.1.318 (Claude Code)', 4, None, None), ('2.1.322 (Claude Code)', 35, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'single', 30000, 13, 13, 0, 0, 0, 13, 1700, 3697, None, (('2.1.316 (Claude Code)', 2, None, None), ('2.1.317 (Claude Code)', 2, None, None), ('2.1.322 (Claude Code)', 9, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'single', 60000, 11, 11, 0, 0, 0, 11, 28800, 32009, None, (('2.1.316 (Claude Code)', 4, None, None), ('2.1.317 (Claude Code)', 7, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'single', 120000, 19, 19, 0, 0, 0, 19, 23400, 27939, None, (('2.1.316 (Claude Code)', 6, None, None), ('2.1.317 (Claude Code)', 7, None, None), ('2.1.322 (Claude Code)', 6, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'single', 240000, 18, 18, 0, 0, 0, 18, 29250, 33816, None, (('2.1.316 (Claude Code)', 3, None, None), ('2.1.317 (Claude Code)', 5, None, None), ('2.1.322 (Claude Code)', 10, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'single', 480000, 17, 17, 0, 0, 0, 17, 36700, 41658, None, (('2.1.316 (Claude Code)', 5, None, None), ('2.1.317 (Claude Code)', 5, None, None), ('2.1.322 (Claude Code)', 7, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'single', None, 23, 17, 6, 0, 0, 3, 70200, 80732, None, ((None, 6, None, None), ('2.1.307 (Claude Code)', 14, None, None), ('2.1.322 (Claude Code)', 3, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'probe', 15000, 27, 27, 0, 0, 0, 26, 276, 216, None, (('2.1.307 (Claude Code)', 1, None, None), ('2.1.316 (Claude Code)', 7, None, None), ('2.1.317 (Claude Code)', 6, None, None), ('2.1.322 (Claude Code)', 13, '2026-11-12', '2026-11-12')), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-sonnet-5-5', 'high', 'single', None, 12, 11, 1, 0, 0, 0, None, 25660, None, ((None, 1, None, None), ('2.1.306 (Claude Code)', 5, None, None), ('2.1.307 (Claude Code)', 6, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-sonnet-5-5', 'high', 'probe', 15000, 2, 2, 0, 0, 0, 0, None, 225, None, (('2.1.306 (Claude Code)', 1, None, None), ('2.1.307 (Claude Code)', 1, None, None)), 'live-records', '2026-11-13', 'cli_estimate'),
    ('codex_cli_framed_v1', 'gpt-6-astra', 'high', 'single', None, 1, 1, 0, 0, 0, 1, 317, 4732, 4415, (('codex-cli 0.163.0', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_framed_v1', 'gpt-6-astra', 'high', 'probe', 15000, 1, 1, 0, 0, 0, 1, 0, 54, 54, (('codex-cli 0.163.0', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'reading', 60000, 2, 2, 0, 0, 0, 2, 108, 996, 900, (('codex-cli 0.163.0', 2, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'reading', 120000, 36, 31, 5, 0, 0, 31, 853, 11563, 11061, (('codex-cli 0.163.0', 36, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'reading', 240000, 90, 87, 3, 0, 0, 87, 1034, 15030, 14514, (('codex-cli 0.163.0', 90, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'reading', None, 1, 0, 1, 0, 0, 0, None, None, None, (('codex-cli 0.163.0', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'final', None, 2, 2, 0, 0, 0, 2, 1331, 1583, 597, (('codex-cli 0.163.0', 2, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'request', 240000, 2, 2, 0, 0, 0, 2, 271, 5344, 5083, (('codex-cli 0.163.0', 2, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'request', 480000, 5, 5, 0, 0, 0, 5, 285, 3501, 3216, (('codex-cli 0.163.0', 5, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'request', None, 25, 24, 1, 0, 0, 24, 1962, 7613, 5651, (('codex-cli 0.163.0', 25, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'single', 15000, 1, 1, 0, 0, 0, 1, 0, 263, 263, (('codex-cli 0.163.0', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'single', 30000, 1, 1, 0, 0, 0, 1, 48, 352, 304, (('codex-cli 0.162.1', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'single', 60000, 4, 3, 1, 0, 0, 3, 437, 1250, 813, (('codex-cli 0.163.0', 4, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'single', 120000, 12, 12, 0, 0, 0, 12, 1395, 2078, 1116, (('codex-cli 0.162.1', 2, None, None), ('codex-cli 0.163.0', 10, None, None)), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'single', 240000, 11, 11, 0, 0, 0, 11, 1016, 2103, 1087, (('codex-cli 0.162.1', 8, None, None), ('codex-cli 0.163.0', 3, None, None)), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'single', 480000, 17, 17, 0, 0, 0, 17, 1829, 3072, 2556, (('codex-cli 0.162.1', 3, None, None), ('codex-cli 0.163.0', 14, None, None)), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'single', None, 40, 31, 9, 0, 0, 31, 937, 5663, 4745, (('codex-cli 0.162.1', 8, None, None), ('codex-cli 0.163.0', 32, None, None)), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'single', 'unmeasured', 1, 0, 1, 0, 0, 0, None, None, None, (('codex-cli 0.162.1', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'probe', 15000, 18, 17, 1, 0, 0, 17, 19, 70, 63, (('codex-cli 0.162.1', 3, None, None), ('codex-cli 0.163.0', 15, None, None)), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-astra', 'high', 'correction', 60000, 1, 1, 0, 0, 0, 1, 0, 137, 137, (('codex-cli 0.163.0', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-luna', 'high', 'single', 240000, 1, 1, 0, 0, 0, 1, 234, 487, 253, (('codex-cli 0.162.1', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-luna', 'high', 'single', 480000, 1, 1, 0, 0, 0, 1, 991, 2822, 1831, (('codex-cli 0.162.1', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-luna', 'high', 'single', None, 5, 3, 2, 0, 0, 3, 3097, 5142, 3088, (('codex-cli 0.162.1', 5, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6-luna', 'high', 'probe', 15000, 1, 1, 0, 0, 0, 1, 95, 152, 57, (('codex-cli 0.162.1', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6.1-sol', 'high', 'single', 15000, 1, 1, 0, 0, 0, 1, 50, 305, 255, (('codex-cli 0.163.0', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6.1-sol', 'high', 'single', 30000, 1, 1, 0, 0, 0, 1, 120, 347, 227, (('codex-cli 0.162.1', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6.1-sol', 'high', 'single', 60000, 1, 1, 0, 0, 0, 1, 283, 833, 550, (('codex-cli 0.162.1', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6.1-sol', 'high', 'single', 480000, 3, 3, 0, 0, 0, 3, 1420, 3763, 2412, (('codex-cli 0.163.0', 3, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6.1-sol', 'high', 'single', None, 10, 5, 5, 0, 0, 5, 1552, 4778, 3226, (('codex-cli 0.162.1', 6, None, None), ('codex-cli 0.163.0', 4, None, None)), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_v1', 'gpt-6.1-sol', 'high', 'probe', 15000, 12, 12, 0, 0, 0, 12, 50, 102, 64, (('codex-cli 0.162.1', 2, None, None), ('codex-cli 0.163.0', 10, None, None)), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_visual_v1', 'gpt-6-astra', 'high', 'single', None, 3, 2, 1, 0, 0, 2, 403, 4462, 4059, (('codex-cli 0.163.0', 3, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_visual_v1', 'gpt-6-astra', 'high', 'probe', 15000, 2, 2, 0, 0, 0, 2, 58, 119, 61, (('codex-cli 0.163.0', 2, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_visual_v1', 'gpt-6.1-sol', 'high', 'single', None, 1, 1, 0, 0, 0, 1, 792, 3662, 2870, (('codex-cli 0.163.0', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('codex_cli_visual_v1', 'gpt-6.1-sol', 'high', 'probe', 15000, 1, 1, 0, 0, 0, 1, 66, 128, 62, (('codex-cli 0.163.0', 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('openai_responses_v1', 'gpt-6-astra', None, 'probe', 15000, 2, 0, 2, 0, 0, 0, None, None, None, ((None, 2, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('openai_responses_v1', 'gpt-6.1-sol', None, 'probe', 15000, 1, 0, 1, 0, 0, 0, None, None, None, ((None, 1, None, None),), 'live-records', '2026-11-13', 'provider_usage'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'reading', 120000, 8, 8, 0, 8, 0, 8, 70550, 78441, None, (('2.1.325 (Claude Code)', 8, '2026-11-13', '2026-11-13'),), 'probe', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'reading', 240000, 4, 4, 0, 4, 0, 4, 85250, 97134, None, (('2.1.325 (Claude Code)', 4, '2026-11-13', '2026-11-13'),), 'probe', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', 120000, 4, 4, 0, 4, 0, 4, 15050, 21583, None, (('2.1.325 (Claude Code)', 4, '2026-11-13', '2026-11-13'),), 'probe', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', 240000, 2, 2, 0, 2, 0, 2, 12550, 19990, None, (('2.1.325 (Claude Code)', 2, '2026-11-13', '2026-11-13'),), 'probe', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'reading', 120000, 4, 4, 0, 4, 0, 4, 88050, 101855, None, (('2.1.325 (Claude Code)', 4, '2026-11-13', '2026-11-13'),), 'probe-frame', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'max', 'reading', 480000, 4, 4, 0, 4, 0, 4, 62300, 72066, None, (('2.1.325 (Claude Code)', 4, '2026-11-13', '2026-11-13'),), 'probe-frame', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', 120000, 2, 2, 0, 2, 0, 2, 21050, 31769, None, (('2.1.325 (Claude Code)', 2, '2026-11-13', '2026-11-13'),), 'probe-frame', '2026-11-13', 'cli_estimate'),
    ('claude_cli_v1', 'claude-opus-5-5', 'xhigh', 'reading', 480000, 2, 2, 0, 2, 0, 2, 14900, 19977, None, (('2.1.325 (Claude Code)', 2, '2026-11-13', '2026-11-13'),), 'probe-frame', '2026-11-13', 'cli_estimate'),
)


def render_profile_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(',', ':'),
    )


def describe_text_room(route, instructions_characters=0, *, legacy_protocol=False):
    """Room for new plans; an earlier protocol retains its own transport admission."""
    capacity = describe_route_capacity(route)
    transport_limit = capacity['input_characters']['value']
    if legacy_protocol:
        return transport_limit
    room = max(0, capacity['input_text_characters']['value'] - instructions_characters)
    return min(room, transport_limit) if transport_limit is not None else room


def describe_task_kind(role, packet, operation='review.invoke'):
    if operation == 'review.route_probe' or 'visual_probe' in packet or ('allowed_control' in packet and 'evidence_control' in packet):
        return 'probe'
    if operation == 'review.output_correction' or packet.get('protocol') == 'exactory-reassessment-disposition-correction-v1':
        return 'correction'
    if 'staged_review' in packet:
        return 'staged'
    if 'reviewer_excerpts' in packet:
        return 'request'
    if 'reading_turn' in packet:
        return 'reading'
    if 'reading_record' in packet:
        return 'final'
    header = packet.get('review_parts', {})
    if header.get('kind') in ('read', 'combine', 'check', 'final'):
        return header['kind']
    if packet.get('problem_evidence_parts', {}).get('stage') == 'part':
        return 'read'
    return 'single'


def _read_encoded_sources(codec, encoded):
    """Read already-validated visible source blocks for a diagnostic projection."""
    if codec.PROTOCOL.endswith('-v2'):
        from .review_exact_text_cost_v1 import _sources
        return _sources(encoded)
    line, remaining = encoded[len(codec.PREFIX):].split('\n', 1)
    selected = set(json.loads(line)['source_sha256'])
    import hashlib
    sources = {}
    while remaining:
        line, remaining = remaining.split('\n', 1)
        length = int(line.split()[3])
        body, remaining = (remaining[:length], remaining[length + 1:])
        checksum = hashlib.sha256(body.encode('utf-8')).hexdigest()
        if checksum in selected:
            sources[checksum] = body
    return sources


def _collect_strings(value):
    if isinstance(value, str):
        yield value
        if value.startswith(transport.PACKET_LABEL):
            yield from _collect_strings(json.loads(value[len(transport.PACKET_LABEL):]))
    elif isinstance(value, dict):
        for item in value.values():
            yield from _collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_strings(item)


def _project_request(request, packet):
    """Project only the current packet for accounting; never save or dispatch the result."""
    from . import review_exact_text, review_exact_text_v2
    messages = copy.deepcopy(transport.logical_messages(request))
    current = messages[-1]['content']
    text = current if isinstance(current, str) else next((part['text'] for part in current if part['type'] == 'input_text'))
    original = json.loads(text[len(transport.PACKET_LABEL):])
    compact = text[len(transport.PACKET_LABEL):] == render_profile_json(original)
    changed = transport.PACKET_LABEL + (render_profile_json(packet) if compact else json.dumps(packet, sort_keys=True, ensure_ascii=False))
    if isinstance(current, str):
        messages[-1]['content'] = changed
    else:
        next((part for part in current if part['type'] == 'input_text'))['text'] = changed
    result = dict(request, input=messages)
    if len(request['input']) == 1 and isinstance(request['input'][0].get('content'), str):
        encoded = request['input'][0]['content']
        codec = next(
            (codec for codec in (review_exact_text, review_exact_text_v2) if codec.is_encoded(encoded)),
            None,
        )
        if codec is not None:
            sources = _read_encoded_sources(codec, encoded)
            strings = list(_collect_strings(messages))
            sources = {key: body for key, body in sources.items() if any((body in text for text in strings))}
            result['input'] = [dict(role='user', content=codec.encode(messages, sources))]
            if codec is review_exact_text_v2:
                from .review_exact_text_cost_v1 import optimize_request
                baseline = dict(
                    request,
                    input=[dict(
                        role='user',
                        content=codec.encode(transport.logical_messages(request), _read_encoded_sources(codec, encoded)),
                    )],
                )
                if baseline['input'] != request['input'] and optimize_request(baseline)['input'] == request['input']:
                    result = optimize_request(result)
    return result


def _clear_carried_answers(packet, kind):
    record = packet.get('reading_turn', {}).get('reading_record') if kind == 'reading' else packet.get('reading_record')
    if record is not None:
        for key in ('turns', 'sources', 'excerpts', 'passages', 'search_receipts'):
            if key in record:
                record[key] = []
        for allowance in record.get('allowances', {}).values():
            allowance['used'] = 0
    state = packet.get('review_parts_state')
    if state is not None:
        for key in state:
            state[key] = []


def _clear_material(packet, kind):
    for row in packet.get('evidence', []) if isinstance(packet.get('evidence'), list) else []:
        if isinstance(row.get('content'), str) and row.get('encoding') != 'base64':
            row['content'] = ''
    if kind in ('final', 'combine', 'check'):
        _clear_carried_answers(packet, kind)


def describe_task(role, packet, *, route, request, operation='review.invoke', count_carried_state=False):
    """Count native request text, separating its serialized material, frame and carried answers."""
    from .review_excerpts import read_sent_packet
    actual = read_sent_packet(request)
    actual = packet if actual is None else actual
    kind = describe_task_kind(role, actual, operation)
    total = measure_request(route, request)['text_characters']
    projected = copy.deepcopy(actual)
    seen = set()
    duplicates = False
    for row in projected.get('evidence', []) if isinstance(projected.get('evidence'), list) else []:
        checksum = row.get('artifact', {}).get('sha256')
        if checksum and row.get('content') and (row.get('encoding') != 'base64'):
            if checksum in seen:
                row['content'] = ''
                duplicates = True
            seen.add(checksum)
    material_sources = copy.deepcopy(projected.get('evidence', []))

    def measure(value):
        from .errors import ResearchError
        try:
            return measure_request(route, _project_request(request, value))['text_characters']
        except ResearchError as error:
            if error.code != 'review_verbatim_evidence_invalid':
                raise
            # Preserve the pinned verbatim wrappers when an empty-body counterfactual is invalid.
            framed = copy.deepcopy(value)
            visible = 0
            for original, destination in zip(material_sources, framed.get('evidence', [])):
                if original.get('encoding') == 'utf-8' and original.get('content') and (original.get('delivery') is None):
                    destination['content'] = original['content']
                    visible += len(original['content'])
            if not visible:
                raise
            return measure_request(route, _project_request(request, framed))['text_characters'] - visible
    unique = min(total, measure(projected)) if duplicates else total
    count_carried = COUNT_CARRIED_STATE if count_carried_state is None else count_carried_state
    without_carried = unique
    if kind in ('reading', 'read'):
        _clear_carried_answers(projected, kind)
        without_carried = min(unique, measure(projected)) if projected != actual else unique
    carried = unique - without_carried
    characters = unique if count_carried else without_carried
    frame = copy.deepcopy(projected)
    _clear_material(frame, kind)
    frame_characters = min(without_carried, measure(frame)) if frame != projected else without_carried
    minimum = actual.get('native', {}).get('scientific_context', {}).get('openness_minimum', {})
    targets = len(actual.get('review_parts', {}).get('targets', [])) if kind == 'final' and 'review_parts' in actual else 1 + len(minimum.get('neighbours', [])) if kind in ('single', 'final') else 0
    return dict(
        kind=kind,
        characters=characters,
        frame_characters=frame_characters,
        material_characters=without_carried - frame_characters,
        carried_characters=carried,
        duplicate_characters=total - unique,
        targets=targets,
        count_carried_state=count_carried,
    )


def describe_attempt(route, role, packet, request, response, *, operation='review.invoke', observed_at=None, count_carried_state=None, attempt_error=None):
    """Record last-send estimates and generation outcomes, without losing malformed failures."""
    from .review_answer_recovery import extract_answer_text
    response = response if isinstance(response, dict) else {}
    usage = response.get('usage')
    usage = usage if isinstance(usage, dict) else {}
    context = response.get('context_evidence')
    context = context if isinstance(context, dict) else {}
    execution = response.get('execution')
    execution = execution if isinstance(execution, dict) else {}
    thinking, method, sends = (None, None, None)
    completed = response.get('status') == 'completed'
    if route['adapter'] == 'claude_cli_v1':
        retention = execution.get('system_events')
        try:
            if retention is not None:
                total, estimates, retries = (0, 0, 0)
                for entry in retention['events']:
                    event = json.loads(base64.b64decode(entry['raw_base64']))
                    if event.get('subtype') == 'api_retry':
                        total, estimates, retries = (0, 0, retries + 1)
                    elif event.get('subtype') == 'thinking_tokens' and type(event.get('estimated_tokens_delta')) is int:
                        total += event['estimated_tokens_delta']
                        estimates += 1
                if estimates and (not retention.get('truncated')):
                    thinking, method = (total, 'cli_estimate')
                users = execution.get('event_types', []).count('user')
                if users and (not retention.get('truncated')):
                    sends = users + retries
                completed = completed and users == 1
        except (KeyError, ValueError, TypeError, AttributeError):
            thinking, method, sends, completed = (None, None, None, False)
    elif response:
        details = usage.get('output_tokens_details')
        details = details if isinstance(details, dict) else {}
        value = usage.get('reasoning_output_tokens', details.get('reasoning_tokens'))
        if type(value) is int and value >= 0:
            thinking, method = (value, 'provider_usage')
        from .review_capacity_evidence import read_capacity_sends
        sends = read_capacity_sends(route, response)
        completed = completed and (route['adapter'] == transport.ADAPTER or context.get('input_verified') is True)
    answer_text = extract_answer_text(response) if response else None
    output = usage.get('output_tokens')
    answer_tokens = output - thinking if method == 'provider_usage' and type(output) is int and (output >= thinking) else None
    counts = {key: value if type(value) is int and value >= 0 else None for key, value in ((key, usage.get(key)) for key in ('input_tokens', 'output_tokens'))}
    from .review_failure_class import find_capacity_signal
    signal = find_capacity_signal(route, dict(error=attempt_error), response, answer_text or '')
    excluded = signal is not None and (signal['outcome'] == 'response_over_limit'
        or signal['signal'].get('detail') == 'non_streaming_fallback'
        or (attempt_error or {}).get('code') == 'review_response_too_large')
    from .review_capacity_evidence import describe_capacity_observation
    capacity_evidence = describe_capacity_observation(route, response, sends=sends, request=request,
        probe_controls=packet if 'allowed_control' in packet and 'evidence_control' in packet else None)
    return dict(
        **(dict(output_profile_eligible=False) if excluded else {}),
        **(dict(outcome=signal['outcome']) if signal is not None else {}),
        capacity_evidence=capacity_evidence,
        usage=dict(
            counts,
            thinking_tokens=thinking,
            thinking_method=method,
            sends=sends,
            request_characters=measure_request(route, request)['text_characters'],
        ),
        task=describe_task(
            role,
            packet,
            route=route,
            request=request,
            operation=operation,
            count_carried_state=False,
        ),
        answer=dict(
            characters=len(answer_text) if answer_text is not None else None,
            tokens=answer_tokens,
            bound=packet.get('review_parts', {}).get('output_allowance'),
        ),
        completed_generation=completed,
        cli_version=context.get('cli_version') or (context.get('runtime') or {}).get('version') or execution.get('cli_version'),
        observed_at=observed_at,
        verified_input_tokens=usage.get('input_tokens') if completed
        and (context.get('input_verified') is True or route['adapter'] == transport.ADAPTER
             and transport.context_verified(route, request, response) and 'previous_response_id' not in request)
        and sends == 1 and type(usage.get('input_tokens')) is int and usage['input_tokens'] >= 0 else None,
    )


def _find_task_level(characters):
    if characters is None:
        return 'unmeasured'
    return next((level for level in TASK_LEVELS if level is None or characters <= level), None)


def _create_profile_row(kind, level):
    return dict(
        kind=kind,
        task_level=level,
        attempts=0,
        completed_attempts=0,
        other_attempts=0,
        measurement_attempts=0,
        at_or_over_limit=0,
        thinking_attempts=0,
        largest_thinking_tokens=None,
        largest_output_tokens=None,
        largest_answer_tokens=None,
        cli_versions=[],
    )


def _merge_version_dates(versions, version, count, first, last):
    target = next((row for row in versions if row['version'] == version), None)
    if target is None:
        target = dict(version=version, attempts=0, first_date=None, last_date=None)
        versions.append(target)
    target['attempts'] += count
    if first is not None:
        target['first_date'] = min(target['first_date'] or first, first)
    if last is not None:
        target['last_date'] = max(target['last_date'] or last, last)
    versions.sort(key=lambda row: row['version'] or '')


def _sort_profile_row(row):
    level = row['task_level']
    return (
        TURN_KINDS.index(row['kind']),
        level if type(level) is int else float('inf'),
        level == 'unmeasured',
    )


def qualifies_for_output_profile(sample):
    return (sample.get('output_profile_eligible') is not False and
            (sample.get('error') or {}).get('code') != 'review_response_too_large')


def build_profile_rows(samples):
    """Keep every attempt; only completed generations supply the usage maxima."""
    groups = {}
    for sample in samples:
        if not qualifies_for_output_profile(sample):
            continue
        usage = sample['usage']
        output = usage.get('output_tokens')
        over = sample.get('outcome') == 'output_over_cap' or type(output) is int and output >= sample['output_limit']
        complete = sample.get('completed_generation', False)
        task = sample['task']
        key = (task['kind'], _find_task_level(task['characters']))
        row = groups.setdefault(key, _create_profile_row(*key))
        row['attempts'] += 1
        row['completed_attempts'] += int(complete)
        row['at_or_over_limit'] += int(over)
        row['other_attempts'] += int(not complete and (not over))
        row['measurement_attempts'] += int(sample.get('source') in ('probe', 'probe-frame'))
        if complete:
            row['thinking_attempts'] += int(usage.get('thinking_tokens') is not None)
            for name, value in (
                ('thinking', usage.get('thinking_tokens')),
                ('output', output),
                ('answer', sample['answer'].get('tokens')),
            ):
                if value is not None:
                    field = 'largest_' + name + '_tokens'
                    row[field] = max(row[field] or 0, value)
        date = (sample.get('observed_at') or '')[:10] or None
        _merge_version_dates(row['cli_versions'], sample.get('cli_version'), 1, date, date)
    return sorted(groups.values(), key=_sort_profile_row)


def merge_profile_rows(rows):
    groups = {}
    for source in rows:
        key = (source['kind'], source['task_level'])
        row = groups.setdefault(key, _create_profile_row(*key))
        for name in (
            'attempts',
            'completed_attempts',
            'other_attempts',
            'measurement_attempts',
            'at_or_over_limit',
            'thinking_attempts',
        ):
            row[name] += source[name]
        for name in ('largest_thinking_tokens', 'largest_output_tokens', 'largest_answer_tokens'):
            if source[name] is not None:
                row[name] = max(row[name] or 0, source[name])
        for version in source.get('cli_versions', []):
            _merge_version_dates(
                row['cli_versions'],
                version['version'],
                version['attempts'],
                version['first_date'],
                version['last_date'],
            )
    return sorted(groups.values(), key=_sort_profile_row)


def pack_profile_prior(row):
    values = []
    for field in PROFILE_ROW_FIELDS:
        value = row[field]
        if field == 'cli_versions':
            value = tuple(
                ((item['version'], item['attempts'], item['first_date'], item['last_date']) for item in value),
            )
        values.append(value)
    return tuple((row['route_class'][field] for field in ('adapter', 'model', 'effort'))) + tuple(values)


def read_profile_priors(route):
    selected = []
    route_class = describe_route_class(route)
    for values in OUTPUT_PROFILE_PRIORS:
        if dict(zip(('adapter', 'model', 'effort'), values[:3])) != route_class:
            continue
        row = dict(zip(PROFILE_ROW_FIELDS, values[3:]), source_record=MEASUREMENT_SOURCE)
        row['cli_versions'] = [dict(zip(('version', 'attempts', 'first_date', 'last_date'), value)) for value in row['cli_versions']]
        selected.append(row)
    return selected


def describe_frame_policy(route):
    route_class = describe_route_class(route)
    measured_class = route_class['adapter'] == 'claude_cli_v1' and route_class['model'] == 'claude-opus-5-5'
    rows = [dict(zip(FRAME_PROBE_FIELDS, row)) for row in FRAME_PROBE_EVIDENCE if row[0] == route_class['effort']] if measured_class else []
    lists = next((row for row in rows if row['part'] == 'lists'), None)
    return dict(
        count_carried_state=COUNT_CARRIED_STATE,
        borrowing_ready=lists is not None and lists['reading'] == 'no effect',
        largest_text_characters=lists['largest_text_characters'] if lists else 0,
        largest_material_characters=lists['largest_material_characters'] if lists else 0,
        frame_limit=lists['frame_limit'] if lists else None,
        readings=rows,
        summary_sha256=FRAME_PROBE_SUMMARY_SHA256,
        source='probe-frame',
    )




def read_output_profile(records, route, *, priors=None):
    route_class = describe_route_class(route)
    assignments = records.get('review_assignment', {})
    routes = records.get('review_route', {})
    recent_heap = []
    verified = 0
    from .review_capacity_evidence import read_capacity_evidence, read_capacity_records
    probe = read_capacity_evidence(route, records).get('verified_capacity_probe')
    probe_id = probe['id'] if probe is not None else None
    completions = ((kind, attempt) for kind in ('review_attempt', 'review_route_probe', 'review_output_correction') for attempt in read_capacity_records(records, kind).values())
    for completion_kind, attempt in completions:
        if not qualifies_for_output_profile(attempt):
            continue
        assignment = assignments.get(attempt.get('assignment_id'), {})
        candidate = routes.get(attempt.get('route_id') or assignment.get('route_id'))
        if candidate is None:
            continue
        if (describe_route_class(candidate) != route_class or not all(name in attempt for name in ('task', 'answer'))
                or 'characters' not in attempt['task']):
            continue
        if attempt.get('completed_generation'):
            verified = max(verified, attempt.get('verified_input_tokens') or 0)
        entry = (attempt.get('recorded_revision', 0), completion_kind + ':' + attempt['id'], attempt, candidate)
        if len(recent_heap) < PROFILE_ATTEMPT_LIMIT:
            heapq.heappush(recent_heap, entry)
        else:
            heapq.heappushpop(recent_heap, entry)
    samples = [dict(entry[2], output_limit=describe_route_capacity(entry[3])['output_tokens']['value']) for entry in sorted(recent_heap)]
    prior_rows = read_profile_priors(route) if priors is None else copy.deepcopy(list(priors))
    frame_rows = [row for row in prior_rows if row.get('source') == 'probe-frame']
    rows = merge_profile_rows(
        [row for row in prior_rows if row.get('source') != 'probe-frame'] + build_profile_rows(samples),
    )
    return dict(
        protocol=PROFILE_PROTOCOL,
        route_class=route_class,
        rows=rows,
        priors=prior_rows,
        digest=digest(rows),
        store_attempts=len(samples),
        verified_input_tokens=verified,
        capacity_probe_id=probe_id,
        frame_policy=describe_frame_policy(route),
        frame_measurements=frame_rows,
    )




def describe_material_levels(kind, *, block_room=480000):
    if kind in ('reading', 'request'):
        return (120000, 60000, 30000, 15000)
    if kind == 'probe':
        return (15000,)
    if kind == 'staged':
        return (0,)
    floor = 15000 if kind in ('read', 'single', 'correction') else 60000
    value = 600000 if kind in ('read', 'single', 'correction') else max(floor, block_room)
    levels = []
    while value > floor:
        levels.append(value)
        value //= 2
    return tuple(levels + [floor])


def _describe_answer_bounds(kind, targets, card_minimum):
    if kind == 'probe':
        return (5000, 1000)
    if kind in ('reading', 'request', 'correction'):
        return (67000, 18000)
    if kind == 'read':
        return (80000, card_minimum)
    if kind in ('final', 'single'):
        return (min(80000, 20000 + 1500 * targets), 6000 + 1500 * targets)
    return (60000, 15000)


def select_task_limits(route, profile, kind, *, targets=1, card_minimum=4000, calibrated=None, block_room=480000, frame_characters=40000, carried_characters=0, frame_matches_reading=False):
    """Report each material level against its serialized frame plus task, at unchanged effort."""
    if kind == 'staged':
        return dict(
            state='planner_sized',
            sizing='joint_fit',
            material_allowance=None,
            task_characters=None,
            answer_bound=None,
            levels=[],
            sample_size=0,
            largest_output_tokens=None,
        )
    levels = describe_material_levels(kind, block_room=block_room)
    if calibrated is None:
        calibrated = describe_route_capacity(route)['request_measure'] == 'calibrated'
    target_levels = [max(1, targets)] if kind in ('final', 'single') else [0]
    while target_levels[-1] > 1:
        target_levels.append(max(1, target_levels[-1] // 2))
    usable = [row for row in profile['rows'] if row['kind'] == kind and row['task_level'] != 'unmeasured' and (row['attempts'] >= 4) and (row['completed_attempts'] >= 4 or row['at_or_over_limit'] > 0)]
    borrowed = None
    frame_policy = profile.get('frame_policy', {})
    borrowing = frame_policy.get('borrowing_ready', False) and frame_matches_reading
    frame_limit = frame_policy.get('frame_limit')
    if frame_limit is not None and frame_characters > frame_limit:
        borrowing = False
    if not usable and kind not in ('reading', 'single') and borrowing:
        usable = [row for row in profile['rows'] if row['kind'] == 'reading' and row['task_level'] != 'unmeasured' and (row['attempts'] >= 4) and (row['completed_attempts'] >= 4 or row['at_or_over_limit'] > 0)]
        borrowed = 'reading'
    output_limit = describe_route_capacity(route)['output_tokens']['value']
    candidates = []
    for level in levels:
        task_characters = frame_characters + level + (carried_characters if profile.get('frame_policy', {}).get('count_carried_state', COUNT_CARRIED_STATE) else 0)
        row_level = _find_task_level(task_characters)
        scoped = borrowed is None or (task_characters <= frame_policy.get('largest_text_characters', 0) and level <= frame_policy.get('largest_material_characters', 0))
        larger = [row for row in usable if row['task_level'] is None or (row_level is not None and row['task_level'] >= row_level)] if scoped else []
        row = min(larger, key=lambda row: row['task_level'] or float('inf')) if larger else None
        thinking = row['largest_thinking_tokens'] if row is not None else None
        reserve = (thinking * 115 + 99) // 100 if thinking is not None else None
        room = output_limit - reserve if reserve is not None else None
        if room is not None and calibrated:
            room = room * 18 // 10
        for target_count in target_levels:
            default, minimum = _describe_answer_bounds(kind, target_count, card_minimum)
            attempts = row['attempts'] if row is not None else 0
            over = row['at_or_over_limit'] if row is not None else 0
            qualifies = row is not None and over == 0 and (room is not None) and (room >= minimum)
            state = 'fits' if qualifies else 'over_limit_expected' if row is not None else 'unmeasured'
            candidates.append(
                dict(
                    state=state,
                    qualifies=qualifies,
                    material_allowance=level,
                    frame_characters=frame_characters,
                    task_characters=task_characters,
                    task_level=row['task_level'] if row else row_level,
                    target_allowance=target_count,
                    thinking_reserve_tokens=reserve,
                    answer_room_characters=room,
                    default_answer_bound=default,
                    smallest_answer_bound=minimum,
                    sample_size=attempts,
                    measurement_attempts=row['measurement_attempts'] if row else 0,
                    at_or_over_limit=over,
                    at_or_over_limit_share=over / attempts if attempts else None,
                    largest_thinking_tokens=thinking,
                    largest_output_tokens=row['largest_output_tokens'] if row else None,
                    cli_versions=copy.deepcopy(row['cli_versions']) if row else [],
                ),
            )
    selected = next((row for row in candidates if row['qualifies']), None)
    start_rule = 'fit' if selected is not None else 'unmeasured'
    step5 = selected
    if selected is None:
        measured = [row for row in candidates if row['sample_size']]
        viable = [row for row in measured if row['at_or_over_limit_share'] < 1]
        step5 = min(
            viable,
            key=lambda row: 1 / ((1 - row['at_or_over_limit_share']) * max(1, row['material_allowance'])),
        ) if viable else measured[-1] if measured else candidates[-1]
        risk_rows = [row for row in profile['rows'] if row['kind'] == (borrowed or kind)
                     and row['task_level'] != 'unmeasured' and row['attempts'] >= 4
                     and row['at_or_over_limit'] * 20 > row['attempts']]
        ceilings = [TASK_LEVELS[TASK_LEVELS.index(row['task_level']) - 1] if TASK_LEVELS.index(row['task_level']) else 0 for row in risk_rows]
        ceiling = min(ceilings) if ceilings else None
        below = [row for row in candidates if ceiling is not None and row['task_characters'] <= ceiling]
        selected = below[0] if measured and below else step5
        start_rule = 'below_risky_ranges' if measured and below else 'step5' if measured else 'unmeasured'
    bound = min(selected['default_answer_bound'], selected['answer_room_characters']) if selected['qualifies'] else selected['smallest_answer_bound']
    reported = []
    for level in levels:
        choices = [row for row in candidates if row['material_allowance'] == level]
        reported.append(
            copy.deepcopy(next((row for row in choices if row['qualifies']), choices[0])),
        )
    return dict(
        selected,
        answer_bound=bound,
        borrowed_kind=borrowed if selected['sample_size'] else None,
        policy=POLICY,
        start_rule=start_rule,
        step5_material_allowance=step5['material_allowance'],
        levels=reported,
    )


def derive_material_allowance(kind, start, observations, output_limit):
    levels = describe_material_levels(kind)
    allowance = start['material_allowance']
    ceiling = levels[0] if start['state'] == 'unmeasured' else allowance
    for observation in observations:
        value = observation['material_allowance'] if 'material_allowance' in observation else observation['material_characters']
        current = next((index for index in range(len(levels) - 1, -1, -1) if levels[index] >= value), 0)
        output = observation.get('output_tokens')
        if output is not None and output * 100 >= output_limit * 75:
            current = min(current + 1, len(levels) - 1)
        elif output is not None and output * 100 <= output_limit * 35:
            current = max(0, current - 1)
        allowance = min(ceiling, levels[current])
    return allowance
OUTPUT_WARNING_MESSAGE = 'The measured output profile does not establish a fitting task size for the listed turn kinds. Keep the route effort; use a smaller task after an answer reaches the output limit.'


def describe_output_warning(limits):
    uncertain = {kind: value for kind, value in limits.items() if value['state'] in ('unmeasured', 'over_limit_expected')}
    if not uncertain:
        return None
    state = 'over_limit_expected' if any((row['state'] == 'over_limit_expected' for row in uncertain.values())) else 'unmeasured'
    row = next((value for value in uncertain.values() if value['state'] == state))
    return dict(
        output=dict(
            state=state,
            sample_size=row['sample_size'],
            measurement_attempts=row['measurement_attempts'],
            at_or_over_limit=row['at_or_over_limit'],
            largest_output_tokens=row['largest_output_tokens'],
            at_or_over_limit_share=row['at_or_over_limit_share'],
            kinds=list(uncertain),
            message=OUTPUT_WARNING_MESSAGE,
        ),
    )


def describe_near_limit_attempts(records, route):
    assignments = records.get('review_assignment', {})
    limit = describe_route_capacity(route)['output_tokens']['value']
    return [dict(
        attempt_id=attempt['id'],
        assignment_id=attempt['assignment_id'],
        output_tokens=output,
        output_limit=limit,
        share=output / limit,
    ) for attempt in records.get('review_attempt', {}).values() if assignments.get(attempt.get('assignment_id'), {}).get('route_id') == route['id'] for output in [(attempt.get('usage') or {}).get('output_tokens')] if output is not None and output * 100 >= limit * 75]


def describe_final_projection(final, reading, *, entries=520000, minimums=12400):
    allowance = final.get('task_characters')
    if allowance is None:
        return None
    pool = max(0, allowance - final['frame_characters'] - minimums)
    remaining = max(0, entries - pool)
    reading_room = reading.get('material_allowance') or 15000
    return dict(
        state=final['state'],
        task_characters=allowance,
        frame_characters=final['frame_characters'],
        sample_size=final['sample_size'],
        largest_output_tokens=final['largest_output_tokens'],
        minimums=minimums,
        carried_entries=entries,
        entry_pool=pool,
        carried_reading_characters=remaining,
        reading_turns_lower_bound=math.ceil(remaining / reading_room),
        source_record='20261112235500-openness-chain-final-turn-over-route-limit-after-57-turns',
    )


def read_installed_cli_version(route):
    """Read the local version only when comparing an actual pinned plan."""
    adapter = transport._local_adapter(route)
    if adapter is None:
        return None
    if route['adapter'] == 'claude_cli_v1':
        return adapter._cli()[1]
    return adapter.runtime().get('version')


def describe_cli_version_status(profile, installed_version):
    versions = {row['version'] for item in profile['rows'] for row in item['cli_versions']}
    return dict(
        installed=installed_version,
        measured=installed_version in versions if installed_version is not None else None,
        state='unmeasured_version' if installed_version is not None and installed_version not in versions else 'measured' if installed_version is not None else 'unknown',
    )


def describe_route_report(records, route, artifacts=None):
    profile = read_output_profile(records, route)
    limits = {kind: select_task_limits(route, profile, kind) for kind in TURN_KINDS}
    result = dict(
        route_capacity=describe_route_capacity(route, profile, records=records, artifacts=artifacts),
        outside_solved_part=describe_unsolved_reviews(),
        output_profile=profile,
        task_limits=limits,
        final_reference_projection=describe_final_projection(limits['final'], limits['reading']),
        near_limit_attempts=describe_near_limit_attempts(records, route),
    )
    warning = describe_output_warning(limits)
    if warning is not None:
        result['capacity_warning'] = warning
    return result


def describe_assignment_kind(assignment):
    if 'task' in assignment:
        return assignment['task']['kind']
    header = assignment.get('review_parts', {})
    if header.get('kind') in TURN_KINDS:
        return header['kind']
    parameters = assignment.get('prompt', {}).get('parameters', {})
    if parameters.get('reading_stage') == 'reading':
        return 'reading'
    if parameters.get('reading_stage') == 'final':
        return 'final'
    if str(parameters.get('evidence_parts', '')).endswith('/part'):
        return 'read'
    if assignment.get('staged_review'):
        return 'staged'
    if parameters.get('excerpts'):
        return 'request'
    return 'single'


def describe_assignment_limits(route, profile, assignment):
    task = assignment.get('task', {})
    return select_task_limits(
        route,
        profile,
        describe_assignment_kind(assignment),
        targets=task.get('targets', 1),
        frame_characters=task.get('frame_characters', 40000),
        carried_characters=task.get('carried_characters', 0),
    )


def describe_assignment_task(records, artifacts, assignment):
    """Measure advisory work without changing the assignment that scientific digests bind."""
    if 'task' in assignment:
        return assignment['task']
    kind = describe_assignment_kind(assignment)
    if kind == 'staged':
        return dict(kind=kind, sizing='joint_fit')
    latest = max((attempt for attempt in records.get('review_attempt', {}).values()
                  if attempt.get('assignment_id') == assignment['id']
                  and attempt.get('origin') == 'instrumented' and 'task' in attempt),
                 key=lambda attempt: attempt['recorded_revision'], default=None)
    if latest is not None:
        return latest['task']
    if artifacts is not None and assignment.get('packet'):
        from .errors import ResearchError
        from .review_protocol import build_assignment_request
        from .review_excerpts import read_sent_packet
        try:
            request = build_assignment_request(records, artifacts, assignment)
            return describe_task(assignment['role'], read_sent_packet(request) or {},
                                 route=records['review_route'][assignment['route_id']], request=request)
        except (ResearchError, ValueError):
            # A capacity report must preserve the scientific report's existing refusal, also when the stored
            # packet does not decode (JSONDecodeError and UnicodeDecodeError are ValueErrors).
            pass
    return dict(kind=kind)


def describe_assignment_warning(records, assignment_id, *, artifacts=None):
    assignment = records['review_assignment'][assignment_id]
    assignment = dict(assignment, task=describe_assignment_task(records, artifacts, assignment))
    route = records['review_route'][assignment['route_id']]
    limits = describe_assignment_limits(route, read_output_profile(records, route), assignment)
    return describe_output_warning({describe_assignment_kind(assignment): limits})


def describe_open_route_reports(records, artifacts=None):
    from .errors import ResearchError
    from .review_continuation import list_chain_heads, _list_lineage, _list_recorded_finals
    assignments = records.get('review_assignment', {})
    open_ids = set()
    heads = []
    for head in list_chain_heads(records, None):
        lineage = {row['id'] for row in _list_lineage(records, assignments[head])}
        if not _list_recorded_finals(records, lineage):
            open_ids.update(lineage)
            heads.append(assignments[head])
    routes = {assignments[key]['route_id'] for key in open_ids if assignments[key]['route_id'] in records.get('review_route', {})}
    result = {}
    for route_id in sorted(routes):
        report = describe_route_report(records, records['review_route'][route_id], artifacts)
        report.pop('capacity_warning', None)
        report.pop('outside_solved_part', None)
        selected = {}
        for head in heads:
            if head['route_id'] != route_id:
                continue
            head = dict(head, task=describe_assignment_task(records, artifacts, head))
            kind = describe_assignment_kind(head)
            limits = describe_assignment_limits(
                records['review_route'][route_id],
                report['output_profile'],
                head,
            )
            if kind not in selected or limits['state'] == 'over_limit_expected' or (selected[kind]['state'] == 'fits' and limits['state'] == 'unmeasured'):
                selected[kind] = limits
        report['task_limits'].update(selected)
        warning = describe_output_warning(selected)
        if warning is not None:
            report['capacity_warning'] = warning
        report['near_limit_attempts'] = [row for row in report['near_limit_attempts'] if row['assignment_id'] in open_ids]
        plans = [head for head in heads if head['route_id'] == route_id and head.get('capacity_plan')]
        if plans and artifacts is not None:
            route = records['review_route'][route_id]
            try:
                installed = read_installed_cli_version(route)
            except (OSError, ValueError, KeyError, ResearchError):
                installed = None
            statuses = []
            for head in plans:
                plan = json.loads(artifacts.read(head['capacity_plan']['artifact']))
                statuses.append(
                    dict(assignment_id=head['id'], **describe_cli_version_status(plan['profile'], installed)),
                )
            report['cli_profile_status'] = dict(
                installed=installed,
                plans=statuses,
                state='unmeasured_version' if any((row['state'] == 'unmeasured_version' for row in statuses)) else 'measured' if installed is not None else 'unknown',
            )
        result[route_id] = report
    return result


def describe_work_units(role, packet):
    """Count existing work units without inventing a new partition protocol."""
    from . import openness_reading, review_problem_parts
    if packet.get('protocol') == 'exactory-reassessment-disposition-correction-v1':
        return {'type': 'correction', 'unit': 'finding', 'count': len(packet['diagnostic']['findings'])}
    plan = packet.get(review_problem_parts.KEY, {})
    if plan.get('stage') == 'part':
        count = sum(
            (end - start for key, ranges in plan['delivered_now'].items() if key != 'passages' for start, end in ranges),
        )
        return {'type': 'result_part', 'unit': 'required_note', 'count': count}
    turn = packet.get(openness_reading.TURN_KEY)
    if turn is not None:
        return {
            'type': 'openness_reading',
            'unit': 'reading_unit',
            'count': len(turn['reading_plan']['delivered_now']),
        }
    kind = 'result_final' if plan else 'openness_final' if openness_reading.RECORD_KEY in packet else 'probe' if role == 'probe' else 'ordinary'
    if kind == 'ordinary' and role == 'native_math':
        kind = 'native_' + str(packet.get('native', {}).get('kind', 'unknown'))
    return {'type': kind, 'unit': 'response', 'count': 1}
