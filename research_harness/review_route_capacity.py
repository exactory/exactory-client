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
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING
from . import review_transport as transport
from .evidence import digest
if TYPE_CHECKING:
    from .review_failure_class import RecoveryResult
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
DEFAULT_INPUT_TOKENS = 150000
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
    ('gpt-6-astra', 1050000, None, 128000, OPENAI_MODELS_URL + 'gpt-6-astra'),
    ('gpt-6.1-sol', 1050000, None, 128000, OPENAI_MODELS_URL + 'gpt-6.1-sol'),
    ('gpt-6-luna', 1050000, None, 128000, OPENAI_MODELS_URL + 'gpt-6-luna'),
)
VERIFIED_INPUT_TOKENS = (
    ('codex_cli_v1', 'gpt-6-astra', 359900),
    ('codex_cli_v1', 'gpt-6.1-sol', 316707),
    ('codex_cli_v1', 'gpt-6-luna', 193797),
    ('codex_cli_framed_v1', 'gpt-6-astra', 307507),
    ('codex_cli_visual_v1', 'gpt-6-astra', 364458),
    ('codex_cli_visual_v1', 'gpt-6.1-sol', 358029),
)


def describe_route_class(route):
    adapter = route['adapter']
    effort = 'high' if adapter.startswith('codex_cli_') else route['configuration'].get('effort') if adapter == 'claude_cli_v1' else None
    return dict(adapter=adapter, model=route['model'], effort=effort)


def _describe_limit(value, source, **evidence):
    return dict(value=value, source=source, **evidence)


def describe_route_capacity(route, profile=None):
    """Report the effective route limits, with documentation or measurement provenance."""
    adapter, config = (route['adapter'], route['configuration'])
    model = next((row for row in MODEL_LIMITS if row[0] == route['model']), None)
    output = config['max_output_tokens']
    output_limit = _describe_limit(output, 'route_configuration', field='max_output_tokens')
    if adapter == 'claude_cli_v1' and output >= (model[3] if model else 128000):
        output = model[3] if model else 128000
        output_limit = _describe_limit(
            output,
            'documented',
            url=model[4] if model else CLAUDE_OUTPUT_URL,
            date=MEASUREMENT_DATE,
        )
    if adapter == 'claude_cli_v1':
        from .review_claude_transport import INPUT_LIMIT_CHARACTERS
        characters = _describe_limit(
            INPUT_LIMIT_CHARACTERS,
            'transport',
            file='research_harness/review_claude_transport.py',
            constant='INPUT_LIMIT_CHARACTERS',
        )
        tokens = _describe_limit(
            (model[2] if model else 200000) - output,
            'documented' if model else 'default',
            url=CLAUDE_WINDOW_URL,
            date=MEASUREMENT_DATE,
        )
    elif adapter in transport.LOCAL_ADAPTERS:
        if adapter == 'codex_cli_visual_v1':
            from .review_codex_visual_transport import MAX_USER_TEXT
            characters = _describe_limit(
                MAX_USER_TEXT,
                'transport',
                file='research_harness/review_codex_visual_transport.py',
                constant='MAX_USER_TEXT',
            )
        else:
            characters = _describe_limit(
                transport.CODEX_INPUT_LIMIT_CHARS,
                'transport',
                file='research_harness/review_transport.py',
                constant='CODEX_INPUT_LIMIT_CHARS',
            )
        prior_input_tokens = next(
            (value for name, name_model, value in VERIFIED_INPUT_TOKENS if (name, name_model) == (adapter, route['model'])),
            None,
        )
        verified = max(prior_input_tokens or 0, (profile or {}).get('verified_input_tokens') or 0)
        tokens = _describe_limit(
            verified,
            'observed',
            source_record=MEASUREMENT_SOURCE,
            date=MEASUREMENT_DATE,
        ) if prior_input_tokens is not None or verified > DEFAULT_INPUT_TOKENS else _describe_limit(DEFAULT_INPUT_TOKENS, 'default')
    else:
        characters = _describe_limit(None, 'transport', file='research_harness/review_transport.py')
        tokens = _describe_limit(
            max(0, model[1] - output),
            'documented',
            url=model[4],
            date=MEASUREMENT_DATE,
        ) if model else _describe_limit(DEFAULT_INPUT_TOKENS, 'default')
    reservation_basis = 'compaction_threshold' if adapter == 'claude_cli_v1' and model is not None else 'model_window' if tokens['source'] == 'documented' else 'completed_answer' if tokens['source'] == 'observed' else 'adapter_default'
    tokens.update(output_reservation_tokens=output, reservation_basis=reservation_basis)
    calibrated = model is not None and (adapter == 'claude_cli_v1' or any(
        ((name, name_model) == (adapter, route['model']) for name, name_model, _ in VERIFIED_INPUT_TOKENS),
    ))
    ratio = CLAUDE_CHARACTERS_PER_TOKEN if adapter == 'claude_cli_v1' else CODEX_CHARACTERS_PER_TOKEN
    text_room = math.floor(tokens['value'] * ratio) if calibrated else tokens['value']
    return dict(
        protocol=POLICY,
        listed=model is not None,
        route_class=describe_route_class(route),
        input_text_characters=_describe_limit(
            text_room,
            'estimated',
            characters_per_token=ratio if calibrated else None,
            margin=TOKEN_RATIO_MARGIN if calibrated else None,
        ),
        input_characters=characters,
        input_tokens=tokens,
        output_tokens=output_limit,
        effort=describe_route_class(route)['effort'],
        request_measure='calibrated' if calibrated else 'bytes',
        provider_enforces_output=not adapter.startswith('codex_cli_'),
        binary_inputs='unmeasured' if adapter in (transport.ADAPTER, 'codex_cli_visual_v1') else 'unsupported',
        raises_capacity=['Add documented model limits with their URLs.', 'Record a verified single-send attempt to raise the observed input size and output profile.'],
    )


def _read_text_parts(content):
    if isinstance(content, str):
        return content
    return ''.join(
        (part['text'] for part in content if part.get('type') in ('input_text', 'output_text', 'text')),
    )


def measure_request(route, request):
    """Measure the exact native text, excluding unmeasured binary inputs."""
    capacity = describe_route_capacity(route)
    adapter = transport._local_adapter(route)
    representation = 'adapter_packet_text' if adapter is not None else 'serialized_request'
    native_images = None
    if route['adapter'] == 'codex_cli_visual_v1':
        from . import review_codex_transport as codex
        environment = codex.clean_environment(os.environ)
        directory = Path(environment.get('TMPDIR') or tempfile.gettempdir()).resolve() / ('review-visual-' + 'x' * 8)
        native_images = len(adapter.validated_images(request))
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
        binary_inputs=capacity['binary_inputs'],
        token_estimate=True,
        ratio_margin=TOKEN_RATIO_MARGIN if calibrated else None,
        characters_per_token=ratio if calibrated else None,
        fits=(limit is None or characters <= limit) and tokens_upper <= capacity['input_tokens']['value'],
    )
    if native_images is not None:
        result['native_images'] = native_images
    return result
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
    if 'reading_turn' in packet:
        return 'reading'
    if 'reading_record' in packet:
        return 'final'
    header = packet.get('review_parts', {})
    if header.get('kind') in ('read', 'combine', 'check', 'final'):
        return header['kind']
    if packet.get('problem_evidence_parts', {}).get('stage') == 'part':
        return 'read'
    if 'reviewer_excerpts' in packet:
        return 'request'
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
        sends = 1 if route['adapter'] == transport.ADAPTER or context.get('input_verified') is True else None
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
    return dict(
        **(dict(output_profile_eligible=False) if excluded else {}),
        **(dict(outcome=signal['outcome']) if signal is not None else {}),
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
        verified_input_tokens=usage.get('input_tokens') if completed and context.get('input_verified') is True and (sends == 1) and (type(usage.get('input_tokens')) is int) and (usage['input_tokens'] >= 0) else None,
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
    completions = ((kind, attempt) for kind in ('review_attempt', 'review_route_probe', 'review_output_correction') for attempt in records.get(kind, {}).values())
    for completion_kind, attempt in completions:
        if not qualifies_for_output_profile(attempt):
            continue
        if completion_kind != 'review_route_probe' and attempt.get('origin') != 'instrumented':
            continue
        if not all((name in attempt for name in ('task', 'answer'))):
            continue
        if 'characters' not in attempt['task']:
            continue
        assignment = assignments.get(attempt.get('assignment_id'), {})
        candidate = routes.get(attempt.get('route_id') or assignment.get('route_id'))
        if candidate is None or describe_route_class(candidate) != route_class:
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
        frame_policy=describe_frame_policy(route),
        frame_measurements=frame_rows,
    )


def find_serving_routes(records, role, packet, *, artifacts, assignment, prompt, history=()):
    """Prove registered route choices using their probes and the shared request measure.

    Binary requests await the documented bounds of binary-input-measure; a text
    adapter must never qualify by silently omitting bytes from this packet.
    """
    from .errors import ResearchError
    from .review_protocol import route_state, retired_routes
    from .review_parts_roles import find_adapter
    result = []
    retired = retired_routes(records)
    binary = any(row.get('encoding') == 'base64' for row in packet.get('evidence', []))
    visual = bool(packet.get('visual_source_delivery') or packet.get('visual_context_repair'))
    for route in records.get('review_route', {}).values():
        if route['id'] in retired or binary or visual and route['adapter'] != 'codex_cli_visual_v1':
            continue
        if route_state(records, artifacts, route['id'])['context_status'] != 'verified_for_route':
            continue
        try:
            request = transport.request_body(route, prompt, packet, history)
            measure = measure_request(route, request)
        except ResearchError:
            continue
        if measure['fits']:
            result.append(dict(route_id=route['id'], delivery='single', measure=measure))
        elif (route['adapter'] in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1')
              and find_adapter(assignment, packet) is not None
              and measure['token_limit'] > 15000 + math.ceil(len(prompt) / CLAUDE_CHARACTERS_PER_TOKEN)):
            result.append(dict(route_id=route['id'], delivery='parts', measure=measure))
    return result


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
    usable = [row for row in profile['rows'] if row['kind'] == kind and row['task_level'] != 'unmeasured' and (row['attempts'] >= 4) and (row['completed_attempts'] >= 4 and row['largest_thinking_tokens'] is not None or row['at_or_over_limit'] > 0)]
    borrowed = None
    frame_policy = profile.get('frame_policy', {})
    borrowing = frame_policy.get('borrowing_ready', False) and frame_matches_reading
    frame_limit = frame_policy.get('frame_limit')
    if frame_limit is not None and frame_characters > frame_limit:
        borrowing = False
    if not usable and kind not in ('reading', 'single') and borrowing:
        usable = [row for row in profile['rows'] if row['kind'] == 'reading' and row['task_level'] != 'unmeasured' and (row['attempts'] >= 4) and (row['completed_attempts'] >= 4 and row['largest_thinking_tokens'] is not None or row['at_or_over_limit'] > 0)]
        borrowed = 'reading'
    output_limit = describe_route_capacity(route)['output_tokens']['value']
    text_room = describe_route_capacity(route, profile)['input_text_characters']['value']
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
            qualifies = row is not None and over == 0 and (room is not None) and (room >= minimum) and (task_characters <= text_room)
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
    if selected is None:
        measured = [row for row in candidates if row['sample_size']]
        viable = [row for row in measured if row['at_or_over_limit_share'] < 1 and row['task_characters'] <= text_room]
        selected = min(
            viable,
            key=lambda row: 1 / ((1 - row['at_or_over_limit_share']) * max(1, row['material_allowance'])),
        ) if viable else measured[-1] if measured else candidates[-1]
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


def describe_route_report(records, route):
    profile = read_output_profile(records, route)
    limits = {kind: select_task_limits(route, profile, kind) for kind in TURN_KINDS}
    result = dict(
        route_capacity=describe_route_capacity(route, profile),
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
        report = describe_route_report(records, records['review_route'][route_id])
        report.pop('capacity_warning', None)
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
            forecasts = {}
            for head in plans:
                if 'capacity_plan_kind' in head:
                    plan = read_chain_capacity_plan(artifacts, head)
                    forecasts[head['id']] = describe_capacity_forecast(plan, route, report['output_profile'],
                        records=records, turn_kind=head['capacity_plan_kind'])
                else:
                    plan = json.loads(artifacts.read(head['capacity_plan']['artifact']))
                statuses.append(
                    dict(assignment_id=head['id'], **describe_cli_version_status(plan['profile'], installed)),
                )
            if forecasts:
                report['capacity_forecast'] = forecasts
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


# These constants belong to POLICY. Existing chains use the values in their stored plan.
CAPACITY_PLAN_PROTOCOL = 'exactory-route-capacity-plan-v1'
CAPACITY_REQUEST_KEY = 'capacity_request'
EXCERPT_TEXT_CHARACTERS = 6000
EXCERPT_FIELDS_CHARACTERS = 700
CITED_SOURCE_CHARACTERS = 600
GAP_ROW_CHARACTERS = 400
CARD_ENTRY_CHARACTERS = 2200
DECISION_ROW_CHARACTERS = 360
NOTES_MINIMUM_CHARACTERS = 6000
NOTES_MAXIMUM_CHARACTERS = 20000
TARGET_PREFERRED_CHARACTERS = 30000
TARGET_MAXIMUM_CHARACTERS = 60000
FRAME_MAXIMUM_CHARACTERS = 200000
PLAN_VIOLATED_MESSAGE = 'The exact reviewer request exceeds its stored capacity plan. Retain the plan and request as evidence of a harness defect; use gate-auto-pass with the recorded harness stop for an eligible internal step.'
BELOW_FLOOR_MESSAGE = 'The route cannot hold the frame and minimum task or the final minimums of this chain. Record a serving route with review-route.'
CAPACITY_PLAN_DESCRIPTION = 'The capacity plan pins the route limits, measurement rule, profile, frames, inventory, targets and allowances at the first assignment. Later turns reuse its digest.'
CAPACITY_FORECAST_DESCRIPTION = 'The capacity forecast estimates remaining turns and calls from the current profile. It may change without changing the stored plan or its allowances.'


def divide_room(room, parts):
    """Give every part its serialized minimum, then share space by preferred size up to its cap."""
    from .errors import ResearchError
    if type(room) is not int or room < 0 or any(
            any(type(part[key]) is not int for key in ('minimum', 'preferred', 'maximum'))
            or not 0 <= part['minimum'] <= part['preferred'] <= part['maximum'] for part in parts):
        raise ResearchError('invalid_input', 'Room and part sizes must be nonnegative integers with minimum <= preferred <= maximum.')
    minimum = sum(part['minimum'] for part in parts)
    if minimum > room:
        raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                            dict(room=room, minimum=minimum, command='review-route'))
    result = [dict(copy.deepcopy(part), allowance=part['minimum']) for part in parts]
    remaining = room - minimum
    active = [part for part in result if part['allowance'] < part['maximum']]
    while remaining and active:
        weight = sum(max(1, part['preferred']) for part in active)
        shares = [remaining * max(1, part['preferred']) // weight for part in active]
        # Integer remainders have stable input order; every iteration consumes room or saturates a part.
        residue = remaining - sum(shares)
        for index in range(residue):
            shares[index] += 1
        for part, share in zip(active, shares):
            granted = min(share, part['maximum'] - part['allowance'])
            part['allowance'] += granted
            remaining -= granted
        active = [part for part in active if part['allowance'] < part['maximum']]
    return dict(room=room, minimum=minimum, parts=result, fill=remaining)


def _summarize_capacity_inventory(inventory):
    unique = {}
    for row in inventory:
        unique.setdefault(row.get('sha256', row['handle']), row)
    kinds = {}
    for row in unique.values():
        group = kinds.setdefault(row['kind'], dict(units=0, characters=0))
        group['units'] += 1
        group['characters'] += row['characters']
    return dict(digest=digest(inventory), units=len(unique),
                characters=sum(row['characters'] for row in unique.values()), by_kind=kinds)


def plan_chain_capacity(route, profile, *, frames, inventory, targets, carried_budget=None):
    """Pin a chain's budgets; builders supply serialized field sizes and stable target handles.

    No current packet protocol opts in. The fixture declaration is consumed at assignment preparation;
    future protocol builders call this function and bind the returned artifact under their own version.
    """
    from .review_refusal_notice import BOUNDED_NOTICE_CHARACTERS
    from .errors import ResearchError
    limits = describe_route_capacity(route, profile)
    text_room = limits['input_text_characters']['value']
    handles = [row if isinstance(row, str) else row['handle'] for row in targets]
    inventory_summary = _summarize_capacity_inventory(inventory)
    # A reading request carries the latest notes and every allowance row, even before entries exist.
    maximum_rows = []
    for handle, target in zip(handles, targets):
        counts = target if isinstance(target, dict) else {}
        overhead = counts.get('gap_rows', 0) * GAP_ROW_CHARACTERS + counts.get('source_rows', 0) * CITED_SOURCE_CHARACTERS
        maximum_rows.append(dict(handle=handle, excerpts=TARGET_MAXIMUM_CHARACTERS, rows=overhead,
                                 allowance=TARGET_MAXIMUM_CHARACTERS + overhead))
    carried_bound = NOTES_MAXIMUM_CHARACTERS + measure_capacity_value(route, maximum_rows)
    frame = {}
    rooms = {}
    for kind, fields in frames.items():
        floor = describe_material_levels(kind)[-1]
        carried_minimum = carried_bound if kind in ('reading', 'read') else 0
        bound = min(sum(fields.values()), FRAME_MAXIMUM_CHARACTERS,
                    text_room - BOUNDED_NOTICE_CHARACTERS - floor - carried_minimum)
        if bound < 0:
            raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                                dict(kind=kind, room=text_room, minimum=sum(fields.values()) + floor +
                                     carried_minimum + BOUNDED_NOTICE_CHARACTERS, command='review-route'))
        # Lower the frame at a measured range boundary when even its floor would exceed that range.
        own = [row for row in profile['rows'] if row['kind'] == kind and row['attempts'] >= 4
               and row['task_level'] not in (None, 'unmeasured')]
        unsafe = [row['task_level'] for row in own if row['at_or_over_limit'] / row['attempts'] > 0.05]
        if unsafe:
            preceding = max((level for level in TASK_LEVELS if type(level) is int and level < min(unsafe)), default=0)
            lowered = preceding - floor - carried_minimum - BOUNDED_NOTICE_CHARACTERS
            if lowered < 0:
                raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                    dict(kind=kind, room=preceding, minimum=floor + carried_minimum + BOUNDED_NOTICE_CHARACTERS,
                         command='review-route'))
            bound = min(bound, lowered)
        remaining = bound
        bounded_fields = {}
        unit_fields = []
        for name, size in fields.items():
            bounded_fields[name] = min(size, remaining)
            remaining -= bounded_fields[name]
            if bounded_fields[name] < size:
                unit_fields.append(name)
        frame[kind] = dict(fields=copy.deepcopy(fields), characters=sum(fields.values()),
                           bound=bound, bounded_fields=bounded_fields, unit_fields=unit_fields)
        rooms[kind] = text_room - bound - BOUNDED_NOTICE_CHARACTERS
    admission_room = rooms.get('final', min(rooms.values()))
    if 'final' in frame:
        final = select_task_limits(route, profile, 'final', targets=len(handles),
            frame_characters=frame['final']['bound'] + BOUNDED_NOTICE_CHARACTERS)
        page_room = min(admission_room, final['material_allowance'])
    else:
        page_room = admission_room
    pool = min(admission_room, carried_budget) if carried_budget is not None else admission_room
    parts = [dict(id='notes', minimum=NOTES_MINIMUM_CHARACTERS,
                  preferred=NOTES_MAXIMUM_CHARACTERS, maximum=NOTES_MAXIMUM_CHARACTERS)]
    for handle, target in zip(handles, targets):
        counts = target if isinstance(target, dict) else {}
        overhead = counts.get('gap_rows', 0) * GAP_ROW_CHARACTERS + counts.get('source_rows', 0) * CITED_SOURCE_CHARACTERS
        minimum = EXCERPT_TEXT_CHARACTERS + EXCERPT_FIELDS_CHARACTERS + overhead
        maximum = min(TARGET_MAXIMUM_CHARACTERS + overhead, page_room // 2)
        if minimum > maximum:
            raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                                dict(room=maximum, minimum=minimum, target=handle, command='review-route'))
        parts.append(dict(id=handle, minimum=minimum, preferred=min(TARGET_PREFERRED_CHARACTERS + overhead, maximum),
                          maximum=maximum, overhead=overhead))
    carried = divide_room(pool, parts)
    allocations = [dict(handle=row['id'], excerpts=row['allowance'] - row['overhead'],
                        rows=row['overhead'], allowance=row['allowance']) for row in carried['parts'][1:]]
    notes = carried['parts'][0]['allowance']
    allowance_rows = measure_capacity_value(route, allocations)
    # Splitting all units to the floor gives the largest card count, independent of the planned turn count.
    unique_units = {row.get('sha256', row['handle']): row for row in inventory}
    cards = sum(max(1, math.ceil(row['characters'] / describe_material_levels('read')[-1]))
                for row in unique_units.values())
    card_reserve = cards * CARD_ENTRY_CHARACTERS
    late_reserve = pool // 10
    card_budget = max(0, pool - card_reserve - late_reserve)
    tasks = {}
    for kind, value in frame.items():
        carried_characters = notes + allowance_rows if kind in ('reading', 'read') else 0
        selected = select_task_limits(route, profile, kind, targets=len(handles),
            frame_characters=value['bound'] + BOUNDED_NOTICE_CHARACTERS,
            carried_characters=carried_characters, calibrated=limits['request_measure'] == 'calibrated')
        level_limits = select_task_limits(route, profile, kind, targets=selected['target_allowance'],
            frame_characters=value['bound'] + BOUNDED_NOTICE_CHARACTERS,
            carried_characters=carried_characters, calibrated=limits['request_measure'] == 'calibrated')
        selected['levels'] = level_limits['levels']
        tasks[kind] = dict(selected, carried_characters=carried_characters,
                           notice_characters=BOUNDED_NOTICE_CHARACTERS)
        policy_ceiling = (describe_material_levels(kind)[0] if selected['state'] == 'unmeasured'
                          else selected['material_allowance'])
        input_ceiling = max(row['material_allowance'] for row in selected['levels']
                            if row['task_characters'] <= text_room and row['material_allowance'] <= policy_ceiling)
        tasks[kind]['adaptation'] = dict(lower_at_percent=75, raise_at_percent=35,
                                         policy_ceiling=policy_ceiling, ceiling=input_ceiling)
        tasks[kind]['request_bound'] = dict(text_characters=selected['task_characters'],
            characters=limits['input_characters']['value'], tokens_upper=limits['input_tokens']['value'])
        minimum = value['bound'] + carried_characters + BOUNDED_NOTICE_CHARACTERS + describe_material_levels(kind)[-1]
        if minimum > text_room:
            raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                                dict(kind=kind, room=text_room, minimum=minimum, command='review-route'))
    pinned_profile = dict(copy.deepcopy(profile), states={kind: task['state'] for kind, task in tasks.items()})
    return dict(protocol=CAPACITY_PLAN_PROTOCOL, policy=POLICY, route_class=describe_route_class(route),
        limits=copy.deepcopy(limits), measure=dict(rule=limits['request_measure'],
            characters_per_token=(CLAUDE_CHARACTERS_PER_TOKEN if route['adapter'] == 'claude_cli_v1'
                                  else CODEX_CHARACTERS_PER_TOKEN) if limits['request_measure'] == 'calibrated' else None,
            count_carried_state=COUNT_CARRIED_STATE, non_ascii_limit=0.05, ratio_margin=TOKEN_RATIO_MARGIN),
        profile=pinned_profile, frame=frame, inventory=inventory_summary,
        targets=dict(handles=handles, digest=digest(handles)),
        allocation=dict(rooms=rooms, admission_room=admission_room, page_room=page_room, carried=carried, targets=allocations, notes=notes,
            carried_turn_pool=pool, card_rate=card_budget / max(1, inventory_summary['characters']),
            card_reserve=card_reserve, card_budget=card_budget, late_unit_reserve=late_reserve,
            largest_card_count=cards, entry_sizes=dict(excerpt_text=EXCERPT_TEXT_CHARACTERS,
                excerpt_fields=EXCERPT_FIELDS_CHARACTERS, cited_source=CITED_SOURCE_CHARACTERS,
                gap=GAP_ROW_CHARACTERS, card=CARD_ENTRY_CHARACTERS, decision=DECISION_ROW_CHARACTERS)), task=tasks)


def read_chain_capacity_plan(artifacts, assignment):
    """Read the authenticated artifact; never replan a stored chain from current priors."""
    from .errors import ResearchError
    reference = assignment['capacity_plan']
    plan = json.loads(artifacts.read(reference['artifact']))
    if reference['protocol'] != CAPACITY_PLAN_PROTOCOL or digest(plan) != reference['digest']:
        raise ResearchError('artifact_corrupt', 'The stored capacity plan does not match its pinned digest.')
    return plan


def describe_capacity_forecast(plan, route, profile, *, records=None, turn_kind=None,
                               remaining_inventory=None, remaining_targets=None, completed_kinds=(), observations_by_kind=None):
    """Compute advisory calls separately from the pinned budgets, including expected replacements."""
    by_kind = {}
    selected = {}
    inventory = (_summarize_capacity_inventory(remaining_inventory) if remaining_inventory is not None
                 else plan['inventory'])
    handles = plan['targets']['handles'] if remaining_targets is None else remaining_targets
    total_characters = inventory['characters']
    for kind in plan['task']:
        pinned = derive_plan_task_limits(plan, kind, (observations_by_kind or {}).get(kind, ()))
        current = select_task_limits(route, profile, kind, targets=len(handles),
            frame_characters=plan['frame'][kind]['bound'] + pinned['notice_characters'],
            carried_characters=pinned['carried_characters'])
        # Current priors estimate the cost of the pinned task; they do not choose a new allowance.
        sample = next(row for row in current['levels'] if row['material_allowance'] == pinned['material_allowance'])
        current.update(sample)
        current['target_allowance'] = pinned['target_allowance']
        selected[kind] = dict(current, kind=kind)
        allowance = pinned['material_allowance']
        if kind in ('reading', 'read'):
            turns = math.ceil(total_characters / allowance)
            largest = math.ceil(total_characters / describe_material_levels(kind)[-1])
        elif kind == 'final':
            entries = [row for row in plan['allocation']['targets'] if row['handle'] in handles]
            pages = _count_capacity_pages(allowance, entries, plan['allocation']['notes'],
                                          max(1, current['target_allowance']))
            turns = sum(pages.values())
            largest = sum(_count_capacity_pages(describe_material_levels(kind)[-1], entries,
                                                 plan['allocation']['notes'], 1).values())
        else:
            turns = 1
            largest = 1
        if kind in completed_kinds:
            turns = 0
            largest = 0
        share = current['at_or_over_limit_share']
        by_kind[kind] = dict(turns=turns, material_allowance=allowance, state=current['state'],
            at_or_over_limit_share=share, sample_size=current['sample_size'],
            expected_calls=0 if not turns else turns / (1 - share) if share is not None and share < 1 else None,
            largest_state_turns=largest)
    expected = [row['expected_calls'] for row in by_kind.values()]
    return dict(profile_digest=profile['digest'], by_kind=by_kind, units=copy.deepcopy(inventory['by_kind']),
        basis='remaining_inputs' if remaining_inventory is not None or remaining_targets is not None else 'initial_inventory',
        turns=sum(row['turns'] for row in by_kind.values()),
        largest_state_turns=sum(row['largest_state_turns'] for row in by_kind.values()),
        expected_calls=sum(expected) if all(value is not None for value in expected) else None,
        next_call=describe_next_capacity_call(records or {}, route, selected[turn_kind or next(iter(selected))]),
        capacity_warning=dict(input=not all(row['request_bound']['text_characters'] <=
            plan['limits']['input_text_characters']['value'] for row in plan['task'].values()),
            output=describe_output_warning(selected)))


def describe_plan_preflight(records, route, request, plan, kind, *, observations=()):
    """Compare the exact native text with the pinned rule and bound; forecasts remain advisory."""
    from .review_preflight import PLANNED_PROTOCOL
    measured = measure_request(route, request)
    rule = plan['measure']
    calibrated = rule['rule'] == 'calibrated' and measured['non_ascii_share'] <= rule['non_ascii_limit']
    measured['tokens_upper'] = (math.ceil(measured['text_characters'] / rule['characters_per_token'])
                                if calibrated else measured['utf8_bytes'])
    measured['measure'] = 'calibrated' if calibrated else 'bytes'
    measured['characters_per_token'] = rule['characters_per_token'] if calibrated else None
    bounds = derive_plan_task_limits(plan, kind, observations)['request_bound']
    comparisons = {name: dict(value=measured[name], bound=value,
                              fits=value is None or measured[name] <= value)
                   for name, value in bounds.items()}
    fits = all(row['fits'] for row in comparisons.values())
    measured['fits'] = fits
    return dict(protocol=PLANNED_PROTOCOL, request_measure=measured,
        plan_comparison=dict(digest=digest(plan), kind=kind, fits=fits, sizes=comparisons),
        capacity_forecast=describe_capacity_forecast(plan, route, read_output_profile(records, route), records=records, turn_kind=kind,
            observations_by_kind={kind: observations}))


def prepare_assignment_capacity(records, artifacts, route, declaration):
    """Validate a builder declaration once at assignment admission and publish or reuse its plan."""
    from .operations import fields, text
    from .errors import ResearchError
    fields(declaration, ('kind',), ('plan', 'frames', 'inventory', 'targets', 'carried_budget'))
    if 'plan' in declaration:
        fields(declaration, ('kind', 'plan'))
        reference = declaration['plan']
        fields(reference, ('protocol', 'digest', 'artifact'))
        plan = read_chain_capacity_plan(artifacts, dict(capacity_plan=reference))
        if plan['route_class'] != describe_route_class(route):
            raise ResearchError('invalid_input', 'A continued capacity plan keeps its route class.')
    else:
        fields(declaration, ('kind', 'frames', 'inventory', 'targets'), ('carried_budget',))
        frames, inventory, targets = (declaration[key] for key in ('frames', 'inventory', 'targets'))
        if not isinstance(frames, dict) or not frames or any(kind not in TURN_KINDS or kind == 'staged' for kind in frames):
            raise ResearchError('invalid_input', 'A capacity declaration needs bounded frames for supported turn kinds.')
        for frame in frames.values():
            if not isinstance(frame, dict) or any(type(size) is not int or size < 0 for size in frame.values()):
                raise ResearchError('invalid_input', 'Frame fields state their serialized nonnegative character sizes.')
        if not isinstance(inventory, list) or not isinstance(targets, list):
            raise ResearchError('invalid_input', 'A capacity inventory and its targets must be lists.')
        unit_handles = []
        for row in inventory:
            fields(row, ('handle', 'kind', 'characters'), ('sha256',))
            text(row['handle'], 'Unit handle')
            text(row['kind'], 'Unit kind')
            if type(row['characters']) is not int or row['characters'] < 0:
                raise ResearchError('invalid_input', 'An inventory unit states its nonnegative serialized characters.')
            unit_handles.append(row['handle'])
        handles = []
        for target in targets:
            if isinstance(target, dict):
                fields(target, ('handle',), ('source_rows', 'gap_rows'))
                if any(type(count) is not int or count < 0 for key, count in target.items() if key != 'handle'):
                    raise ResearchError('invalid_input', 'Target row counts must be nonnegative integers.')
                handle = target['handle']
            else:
                handle = target
            text(handle, 'Target handle')
            handles.append(handle)
        if len(set(handles)) != len(handles) or len(set(unit_handles)) != len(unit_handles):
            raise ResearchError('invalid_input', 'Capacity target and unit handles must be unique.')
        if declaration['kind'] not in frames:
            raise ResearchError('invalid_input', 'The assignment turn kind must have a frame in its capacity plan.')
        budget = declaration.get('carried_budget')
        if budget is not None and (type(budget) is not int or budget < 0):
            raise ResearchError('invalid_input', 'The carried budget must be a nonnegative integer.')
        plan = plan_chain_capacity(route, read_output_profile(records, route), frames=frames,
                                   inventory=inventory, targets=targets, carried_budget=budget)
        from .review_protocol import _put_json
        reference = dict(protocol=CAPACITY_PLAN_PROTOCOL, digest=digest(plan), artifact=_put_json(artifacts, plan))
    if declaration['kind'] not in plan['task']:
        raise ResearchError('invalid_input', 'The assignment turn kind must have a frame in its capacity plan.')
    return dict(capacity_plan=copy.deepcopy(reference), capacity_plan_kind=declaration['kind'])


def describe_assignment_capacity(records, artifacts, assignment):
    """Expose a stored plan and a live forecast outside the immutable assignment."""
    plan = read_chain_capacity_plan(artifacts, assignment)
    route = records['review_route'][assignment['route_id']]
    return dict(capacity_plan=plan, capacity_forecast=describe_capacity_forecast(
        plan, route, read_output_profile(records, route), records=records, turn_kind=assignment['capacity_plan_kind']))


def derive_plan_task_limits(plan, kind, observations=(), *, notice_characters=0):
    """Derive allowances from pinned levels and authenticated observations supplied by the verifier."""
    task = copy.deepcopy(plan['task'][kind])
    levels = [row['material_allowance'] for row in task['levels']]
    allowance = task['material_allowance']
    adaptation = task['adaptation']
    output_limit = plan['limits']['output_tokens']['value']
    for observation in observations:
        outcome = observation.get('outcome')
        if outcome is not None and outcome not in ('output_over_cap', 'input_over_limit', 'timeout', 'provider_refused'):
            continue
        value = observation.get('material_allowance', observation.get('material_characters', allowance))
        index = next((index for index in range(len(levels) - 1, -1, -1) if levels[index] >= value), 0)
        output = observation.get('output_tokens')
        if outcome is not None or output is not None and output * 100 >= output_limit * adaptation['lower_at_percent']:
            index = min(index + 1, len(levels) - 1)
        elif output is not None and output * 100 <= output_limit * adaptation['raise_at_percent']:
            index = max(0, index - 1)
        allowance = min(adaptation['ceiling'], levels[index])
    if allowance != task['material_allowance']:
        selected = next(row for row in task['levels'] if row['material_allowance'] == allowance)
        task.update(copy.deepcopy(selected))
        task['answer_bound'] = (min(selected['default_answer_bound'], selected['answer_room_characters'])
                                if selected['qualifies'] else selected['smallest_answer_bound'])
        task['request_bound']['text_characters'] = selected['task_characters']
    if notice_characters:
        task.update(level_material_allowance=allowance, notice_characters=notice_characters,
                    material_allowance=max(levels[-1], allowance - notice_characters))
        task['task_characters'] = (plan['frame'][kind]['bound'] + task.get('carried_characters', 0)
                                   + task['material_allowance'] + notice_characters)
        task['request_bound']['text_characters'] = task['task_characters']
    return task


def derive_turn_task_limits(records, artifacts, assignment, *, kind=None, notice_characters=0):
    """Read the chain's attempts against its stored plan; later profiles cannot change an allowance."""
    from . import openness_reading, review_failure_class as failures

    plan = read_chain_capacity_plan(artifacts, assignment)
    kind = kind or assignment.get('capacity_plan_kind') or describe_assignment_kind(assignment)
    identifiers = set()
    for member in openness_reading._list_chain(records, assignment):
        identifiers.update(failures.list_turn_assignments(records, member))
    observations = []
    for attempt in sorted(records.get('review_attempt', {}).values(),
                          key=lambda row: (row.get('recorded_revision', 0), row['id'])):
        if attempt['assignment_id'] not in identifiers:
            continue
        member = records['review_assignment'][attempt['assignment_id']]
        task = attempt.get('task') or describe_assignment_task(records, None, member)
        if member.get('capacity_plan_kind', task['kind']) != kind or member['route_id'] != assignment['route_id']:
            continue
        classification = failures.classify_attempt(records, artifacts, attempt)
        observation = dict(output_tokens=(attempt.get('usage') or {}).get('output_tokens'),
                           outcome=classification['outcome'] if classification else None)
        value = task.get('level_material_allowance', task.get('material_allowance', task.get('material_characters')))
        if value is not None:
            observation['material_allowance'] = value
        observations.append(observation)
    return derive_plan_task_limits(plan, kind, observations, notice_characters=notice_characters)


CAPACITY_EFFORT_MESSAGE = 'The harness keeps the route and its effort. A change of effort requires an owner decision and a new route recorded with review-route.'
OUTPUT_OVER_CAP_DECISION_MESSAGE = 'No smaller admissible task remains within the capacity recovery allowance. Review the measured share, profile rows and turn attempts; decide whether this review moves to a route with another effort or output limit using review-route.'
CAPACITY_DECISION_MESSAGE = 'No smaller admissible task is available on this protocol and route. Use the outcome row and its route decision steps.'


def list_capacity_recovery_rows():
    """D1 11.2: every installed kind and protocol has a handler or explicitly has none.

    Reading-v7-turns adds its reading/material-level row when that builder ships.
    Parts v3, session rules v4 and final pages likewise add their own rows, never change legacy packets.
    """
    from . import openness_reading, review_excerpts, review_parts, review_problem_parts
    from . import review_protocol, review_staged, review_output_correction, bar_sources

    rows = []
    for version in (openness_reading.PROTOCOL_V1, openness_reading.PROTOCOL_V2, openness_reading.PROTOCOL,
                    openness_reading.PROTOCOL_V4, openness_reading.PROTOCOL_V5, openness_reading.PROTOCOL_V6,
                    openness_reading.MATH_PROTOCOL):
        unit = 'reading-math-v2' if version == openness_reading.MATH_PROTOCOL else 'reading-v7-turns'
        for kind in ('reading', 'final'):
            rows.append(dict(kind=kind, protocol=version, handler='nothing', required_unit=unit))
    for kind in review_parts.KINDS:
        rows.append(dict(kind=kind, protocol=review_parts.PROTOCOL,
                         handler='parts_v2_read' if kind == 'read' else 'nothing'))
    # Its frozen final also carries reviewer_excerpts, so its measured kind is request.
    for kind in ('read', 'request'):
        rows.append(dict(kind=kind, protocol=review_problem_parts.PROTOCOL, handler='nothing'))
    for version in (review_excerpts.PROTOCOL_V1, review_excerpts.PROTOCOL, review_excerpts.SESSION_PROTOCOL):
        rows.append(dict(kind='request', protocol=version, handler='nothing'))
    for version in dict.fromkeys((review_protocol.PROTOCOL, review_protocol._VERIFICATION_PROTOCOL,
                                 bar_sources.PROTOCOL, review_protocol._PROBLEM_PROTOCOL_V1,
                                 review_protocol._PROBLEM_PROTOCOL, review_protocol._PROBLEM_RESULT_PROTOCOL_V3,
                                 review_protocol._PROBLEM_RESULT_PROTOCOL_V4, review_protocol.PROBLEM_PROTOCOL)):
        rows.append(dict(kind='single', protocol=version, handler='single_request_series'))
    rows.append(dict(kind='staged', protocol=review_staged.PROTOCOL, handler='staged_controller'))
    rows.append(dict(kind='correction', protocol=review_output_correction.PROTOCOL, handler='nothing',
                     next='refusal_replacement'))
    for adapter in tuple(transport.LOCAL_ADAPTERS) + (transport.ADAPTER,):
        rows.append(dict(kind='probe', protocol=adapter, handler='nothing', next='route_unverified'))
    return rows


def describe_capacity_recovery_row(assignment, packet, route):
    from . import openness_reading, review_excerpts, review_parts, review_problem_parts, review_staged
    from .review_protocol import PROTOCOL

    kind = describe_task_kind(assignment['role'], packet)
    version = openness_reading.find_protocol(packet)
    if version is None:
        if kind in review_parts.KINDS and review_parts.KEY in packet:
            version = packet[review_parts.KEY]['protocol']
        elif 'problem_evidence_parts' in packet:
            version = review_problem_parts.PROTOCOL
        elif kind == 'request':
            version = packet[review_excerpts.KEY]['protocol']
        elif kind == 'staged':
            version = review_staged.PROTOCOL
        elif kind == 'probe':
            version = route['adapter']
        else:
            version = packet.get('protocol', PROTOCOL)
    row = next(row for row in list_capacity_recovery_rows()
               if row['kind'] == kind and row['protocol'] == version)
    # A repair is the existing initial-size handler even when its ordinary packet is single.
    from .review_parts_repair import applies
    if applies(assignment, packet) and kind not in ('read', 'combine', 'check', 'final'):
        row = dict(row, handler='context_repair')
    return row


def describe_capacity_decision(records, artifacts, assignment, outcome, details):
    """Use D2's role-specific route steps for the decision member of R52."""
    from .review_failure_class import OUTCOME_CODES, ROUTE_DECISION_QUESTION
    from .review_plan_steps import select_route_steps, describe_step

    # Diagnostics without a native packet can name route setup, but cannot fill an attestation.
    numbers = (select_route_steps(records, artifacts, assignment)
               if assignment['role'] != 'native_math' or assignment.get('context', {}).get('native_packet')
               else [7])
    if numbers in ([4], [5]):
        numbers = [7] + numbers
    steps = [describe_step(records, artifacts, assignment, number) for number in numbers]
    if details.get('required_unit'):
        for step in steps:
            if step['id'] in (4, 5):
                step['prerequisites'].insert(0, dict(condition='The installed release holds ' + details['required_unit'] + '.'))
    details = dict(details, message=(OUTPUT_OVER_CAP_DECISION_MESSAGE if outcome == 'output_over_cap'
                                    else CAPACITY_DECISION_MESSAGE))
    return dict(kind='decision', code=details.get('code', OUTCOME_CODES[outcome]),
                owner_decides=ROUTE_DECISION_QUESTION,
                then=[dict(answer='Move to another route', steps=steps)], details=details)


def plan_capacity_recovery(records, artifacts, assignment, outcome) -> 'RecoveryResult':
    """Return a proved smaller request or D2's decision member, preserving route and effort."""
    from . import review_failure_class as failures, review_protocol

    packet = review_protocol._read_json(artifacts, assignment['packet'])
    route = records['review_route'][assignment['route_id']]
    row = describe_capacity_recovery_row(assignment, packet, route)
    plan = read_chain_capacity_plan(artifacts, assignment) if assignment.get('capacity_plan') else None
    profile = plan['profile'] if plan else read_output_profile(records, route)
    profile_rows = [copy.deepcopy(value) for value in profile['rows'] if value['kind'] == row['kind']]
    sample = sum(value['attempts'] for value in profile_rows)
    share = sum(value['at_or_over_limit'] for value in profile_rows) / sample if sample else None
    identifiers = set(failures.list_turn_assignments(records, assignment))
    attempts = []
    for attempt in sorted(records.get('review_attempt', {}).values(),
                          key=lambda value: (value.get('recorded_revision', 0), value['id'])):
        if attempt['assignment_id'] not in identifiers:
            continue
        member = records['review_assignment'][attempt['assignment_id']]
        task = attempt.get('task') or describe_assignment_task(records, None, member)
        frozen = review_protocol._read_json(artifacts, member['packet'])
        header = frozen.get('review_parts', {})
        material = task.get('level_material_allowance', task.get('material_allowance',
                       header.get('unit_characters', task.get('material_characters'))))
        levels = ([value['material_allowance'] for value in plan['task'][row['kind']]['levels']]
                  if plan and row['kind'] in plan['task'] else describe_material_levels(row['kind']))
        level = (next((index for index in range(len(levels) - 1, -1, -1)
                       if levels[index] >= material), 0) if material is not None else None)
        attempts.append(dict(attempt_id=attempt['id'], assignment_id=member['id'],
            output_tokens=(attempt.get('usage') or {}).get('output_tokens'),
            material_characters=task.get('material_characters'), task_level=task.get('task_level'),
            material_allowance=material, level=level))
    details = dict(row, route_id=assignment['route_id'], effort=describe_route_class(route)['effort'],
                   effort_message=CAPACITY_EFFORT_MESSAGE,
                   at_or_over_limit_share=share, profile_rows=profile_rows, attempts=attempts)
    replacement = (failures.plan_output_replacement(records, artifacts, assignment,
                    allow_single=outcome == 'output_over_cap')
                   if row['handler'] in ('single_request_series', 'parts_v2_read', 'context_repair')
                   and outcome in ('output_over_cap', 'input_over_limit', 'timeout', 'provider_refused') else None)
    if replacement is not None:
        details.update(replacement)
        if outcome != 'output_over_cap':
            details['code'] = failures.OUTCOME_CODES[outcome]
    if replacement is not None and replacement['state'] == 'replace':
        payload = {key: copy.deepcopy(assignment[key]) for key in
                   ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        payload['id'] += '-replacement'
        payload['context'] = replacement['context']
        return dict(kind='replace', reason='capacity', payload=payload,
                    preflight=replacement['preflight'], details=details)
    return describe_capacity_decision(records, artifacts, assignment, outcome, details)


def describe_next_capacity_call(records, route, selected):
    """Report medians only from retained, eligible observations in the selected kind and task range."""
    from statistics import median
    assignments = records.get('review_assignment', {})
    observations = []
    attempts = ((group, attempt) for group in ('review_attempt', 'review_route_probe', 'review_output_correction')
                for attempt in records.get(group, {}).values())
    for group, attempt in attempts:
        assignment = assignments.get(attempt.get('assignment_id'), {})
        candidate = records.get('review_route', {}).get(attempt.get('route_id') or assignment.get('route_id'))
        task = attempt.get('task', {})
        if (candidate is not None and describe_route_class(candidate) == describe_route_class(route)
                and (group == 'review_route_probe' or attempt.get('origin') == 'instrumented')
                and qualifies_for_output_profile(attempt)
                and attempt.get('completed_generation') and task.get('kind') == selected.get('kind')
                and _find_task_level(task.get('characters')) == selected['task_level']):
            observations.append(attempt)
    observations.sort(key=lambda row: row['recorded_revision'], reverse=True)
    result = {}
    for name, field in (('output_tokens', 'output_tokens'), ('duration_seconds', 'wall_seconds')):
        values = [(row.get('usage') or {}).get(field) for row in observations[:PROFILE_ATTEMPT_LIMIT]]
        values = [value for value in values if isinstance(value, (int, float))]
        result[name] = median(values) if values else None
    if not any(value is not None for value in result.values()):
        result['reason'] = 'The selected profile row has no retained medians.'
    return result


def _count_capacity_pages(room, targets, notes, targets_per_page):
    """Pack the granted entries under the same room admission function used to grant them."""
    from .errors import ResearchError
    pages = 0
    condensing = 0
    parts = []
    note = dict(id='notes', minimum=notes, preferred=notes, maximum=notes)
    for target in targets:
        allowance = target['allowance']
        if allowance + notes > room:
            allowance = room // 2
            condensing += math.ceil(target['allowance'] / room)
        part = dict(id=target['handle'], minimum=allowance, preferred=allowance, maximum=allowance)
        try:
            if len(parts) >= targets_per_page:
                parts = []
            divide_room(room, [note] + parts + [part])
        except ResearchError as error:
            if error.code != 'review_route_capacity_below_floor':
                raise
            parts = []
            divide_room(room, [note, part])
        if not parts:
            pages += 1
        parts.append(part)
    return dict(pages=pages, condensing_turns=condensing, closing_pages=int(pages > 1))


def measure_capacity_value(route, value):
    """Measure a serialized value inside native packet framing, including its escaped field quotes."""
    request = transport._build_local_request(route, '', {'carried': value}, [])
    empty = transport._build_local_request(route, '', {'carried': None}, [])
    return measure_request(route, request)['text_characters'] - measure_request(route, empty)['text_characters'] + len('null')
