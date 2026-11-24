"""Input-capacity evidence is keyed by execution identity, separately from output profiles."""
import base64
import copy
import json
from collections import ChainMap

from .errors import ResearchError

COMMON_RUNTIME_MEMBERS = (
    ('adapter', 'key', 'The adapter selects the provider request and context contract.'),
    ('implementation_sha256', 'key', 'The transport and renderer bytes decide the serialized input and context controls.'),
    ('input_contract', 'key', 'The input contract states the context that reaches the provider.'),
    ('fixture', 'key', 'A synthetic executor is a different input treatment from the installed provider runtime.'),
)
LOCAL_RUNTIME_MEMBERS = (
    ('credential_mode', 'key', 'The credential mode selects the provider access path.'),
    ('output_limit', 'no_effect', 'This describes enforcement; max_output_tokens supplies the reservation.'),
    ('wall_limit', 'no_effect', 'Process termination bounds elapsed time, not the accepted input window.'),
)
CODEX_RUNTIME_MEMBERS = (
    ('version', 'key', 'A CLI version can change context handling and the accepted input window.'),
    ('controls', 'key', 'These controls determine the CLI context and model settings.'),
    ('executable', 'no_effect', 'Installation location does not change the version, transport bytes or controls.'),
)
CLAUDE_RUNTIME_MEMBERS = (
    ('flags', 'key', 'The flags select the CLI context loaders and request isolation.'),
)
HTTP_RUNTIME_MEMBERS = (
    ('host', 'no_effect', 'The host name does not change the serialized HTTPS request.'),
    ('platform', 'no_effect', 'The local platform does not change the explicit provider input.'),
    ('python', 'no_effect', 'The Python version does not change the explicit provider input contract.'),
)
COMMON_CONFIGURATION_MEMBERS = (
    ('max_output_tokens', 'reservation', 'The asking route leaves this room for the answer.'),
    ('timeout_seconds', 'no_effect', 'The timeout changes how long the caller waits, not the provider window.'),
)
CLAUDE_CONFIGURATION_MEMBERS = (
    ('effort', 'key', 'Effort changes the model reasoning settings in the request.'),
)
CODEX_CONFIGURATION_MEMBERS = (
    ('effort', 'key', 'Effort changes the model reasoning settings of the turn.'),
)
REQUIRED_RUNTIME_MEMBERS = (
    ('openai_responses_v1', ('adapter', 'implementation_sha256', 'input_contract')),
    ('claude_cli_v1', ('adapter', 'implementation_sha256', 'input_contract', 'credential_mode', 'flags')),
    ('codex_cli_v1', ('adapter', 'implementation_sha256', 'input_contract', 'credential_mode', 'version', 'controls')),
    ('codex_cli_framed_v1', ('adapter', 'implementation_sha256', 'input_contract', 'credential_mode', 'version', 'controls')),
    ('codex_cli_visual_v1', ('adapter', 'implementation_sha256', 'input_contract', 'credential_mode', 'version', 'controls')),
)
DEFAULT_PROBE_INPUT_TOKENS = 150000
INVENTORY_MESSAGES = (
    ('runtime_member_unclassified', 'Classify every runtime identity member before using its capacity evidence.'),
    ('configuration_member_unclassified', 'Classify every route configuration member before using its capacity evidence.'),
    ('prior_key_missing', 'A capacity prior must hold the execution key of its verified attempt.'),
    ('prior_join_undocumented', 'Joining capacity evidence across keys requires a documented statement and its URL.'),
)
# Serialized immutable rows: id, key JSON, verified input, source fixture row, date.
CAPACITY_PRIORS = (
    ('d1-input-0', '{"adapter":"claude_cli_v1","configuration":{"effort":"xhigh"},"endpoint":"claude://local","model":"claude-opus-5-5","runtime":{"adapter":"claude_cli_v1","credential_mode":"claude-subscription","flags":["-p","--safe-mode","--tools","","--strict-mcp-config","--mcp-config","{\\"mcpServers\\":{}}","--disable-slash-commands","--no-session-persistence","--permission-prompts","none","--input-format","stream-json","--replay-user-messages","--output-format","stream-json","--verbose"],"implementation_sha256":"ee183e027eaad81de13cfab1149000c027b4504842070537a1068c93dfb1a7bf","input_contract":"observed-print-stream-v1"}}', 687640, 0, '2026-11-13'),
    ('d1-input-1', '{"adapter":"codex_cli_v1","configuration":{},"endpoint":"codex://local","model":"gpt-6-astra","runtime":{"adapter":"codex_cli_v1","controls":["project_doc_max_bytes=0","developer_instructions=\\"\\"","features.memories=false","features.external_agent_memory_import=false","features.plugins=false","features.apps=false","features.hooks=false","features.shell_tool=false","features.multi_agent=false","features.code_mode=false","features.code_mode_host=false","features.code_mode_only=false","features.js_repl=false","features.js_repl_tools_only=false","features.browser_use=false","features.computer_use=false","features.skip_host_skill_discovery=true","features.skill_search=false","web_search=\\"disabled\\"","mcp_servers={}","model_reasoning_effort=\\"high\\""],"credential_mode":"chatgpt-subscription","implementation_sha256":"a53baa420784a146340ae595aadbd370b1917b33725a1abf805d3bab6126d078","input_contract":"observed-exec-rollout-v1","version":"codex-cli 0.163.0"}}', 359900, 1, '2026-11-13'),
    ('d1-input-2', '{"adapter":"codex_cli_visual_v1","configuration":{},"endpoint":"codex://local","model":"gpt-6-astra","runtime":{"adapter":"codex_cli_visual_v1","controls":["project_doc_max_bytes=0","developer_instructions=\\"\\"","features.memories=false","features.external_agent_memory_import=false","features.plugins=false","features.apps=false","features.hooks=false","features.shell_tool=false","features.multi_agent=false","features.browser_use=false","features.computer_use=false","features.skip_host_skill_discovery=true","features.skill_search=false","web_search=\\"disabled\\"","mcp_servers={}","model_reasoning_effort=\\"high\\""],"credential_mode":"chatgpt-subscription","implementation_sha256":"ce9cb99c70d3a43afa503ae047d8b389d142ab50451b72735c935d801bb57a9e","input_contract":"observed-exec-rollout-visual-v1","version":"codex-cli 0.163.0"}}', 364458, 2, '2026-11-13'),
    ('d1-input-3', '{"adapter":"codex_cli_visual_v1","configuration":{},"endpoint":"codex://local","model":"gpt-6.1-sol","runtime":{"adapter":"codex_cli_visual_v1","controls":["project_doc_max_bytes=0","developer_instructions=\\"\\"","features.memories=false","features.external_agent_memory_import=false","features.plugins=false","features.apps=false","features.hooks=false","features.shell_tool=false","features.multi_agent=false","features.browser_use=false","features.computer_use=false","features.skip_host_skill_discovery=true","features.skill_search=false","web_search=\\"disabled\\"","mcp_servers={}","model_reasoning_effort=\\"high\\""],"credential_mode":"chatgpt-subscription","implementation_sha256":"ce9cb99c70d3a43afa503ae047d8b389d142ab50451b72735c935d801bb57a9e","input_contract":"observed-exec-rollout-visual-v1","version":"codex-cli 0.163.0"}}', 358029, 3, '2026-11-13'),
    ('d1-input-4', '{"adapter":"codex_cli_v1","configuration":{},"endpoint":"codex://local","model":"gpt-6.1-sol","runtime":{"adapter":"codex_cli_v1","controls":["project_doc_max_bytes=0","developer_instructions=\\"\\"","features.memories=false","features.external_agent_memory_import=false","features.plugins=false","features.apps=false","features.hooks=false","features.shell_tool=false","features.multi_agent=false","features.browser_use=false","features.computer_use=false","features.skip_host_skill_discovery=true","features.skill_search=false","web_search=\\"disabled\\"","mcp_servers={}","model_reasoning_effort=\\"high\\""],"credential_mode":"chatgpt-subscription","implementation_sha256":"f2a3919312fba798f79edddc79d0b8e2f15ce62d5b1780244ff07325f62d02a6","input_contract":"observed-exec-rollout-v1","version":"codex-cli 0.163.0"}}', 316707, 4, '2026-11-13'),
    ('d1-input-5', '{"adapter":"codex_cli_framed_v1","configuration":{},"endpoint":"codex://local","model":"gpt-6-astra","runtime":{"adapter":"codex_cli_framed_v1","controls":["project_doc_max_bytes=0","developer_instructions=\\"\\"","features.memories=false","features.external_agent_memory_import=false","features.plugins=false","features.apps=false","features.hooks=false","features.shell_tool=false","features.multi_agent=false","features.browser_use=false","features.computer_use=false","features.skip_host_skill_discovery=true","features.skill_search=false","web_search=\\"disabled\\"","mcp_servers={}","model_reasoning_effort=\\"high\\""],"credential_mode":"chatgpt-subscription","implementation_sha256":"6fb9de719778b211b75b6b08bfc3b3d2acbc267878aa0bb43e3f4ccd3ec76d70","input_contract":"observed-exec-rollout-framed-v1","version":"codex-cli 0.163.0"}}', 307507, 5, '2026-11-13'),
    ('d1-input-6', '{"adapter":"codex_cli_v1","configuration":{},"endpoint":"codex://local","model":"gpt-6-luna","runtime":{"adapter":"codex_cli_v1","controls":["project_doc_max_bytes=0","developer_instructions=\\"\\"","features.memories=false","features.external_agent_memory_import=false","features.plugins=false","features.apps=false","features.hooks=false","features.shell_tool=false","features.multi_agent=false","features.browser_use=false","features.computer_use=false","features.skip_host_skill_discovery=true","features.skill_search=false","web_search=\\"disabled\\"","mcp_servers={}","model_reasoning_effort=\\"high\\""],"credential_mode":"chatgpt-subscription","implementation_sha256":"f2a3919312fba798f79edddc79d0b8e2f15ce62d5b1780244ff07325f62d02a6","input_contract":"observed-exec-rollout-v1","version":"codex-cli 0.162.1"}}', 193797, 6, '2026-11-13'),
)


def describe_capacity_members(route):
    adapter = route['adapter']
    runtime_rules = COMMON_RUNTIME_MEMBERS
    config_rules = COMMON_CONFIGURATION_MEMBERS
    if adapter == 'openai_responses_v1':
        runtime_rules += HTTP_RUNTIME_MEMBERS
    else:
        runtime_rules += LOCAL_RUNTIME_MEMBERS
        if adapter == 'claude_cli_v1':
            runtime_rules += CLAUDE_RUNTIME_MEMBERS
            config_rules += CLAUDE_CONFIGURATION_MEMBERS
        else:
            runtime_rules += CODEX_RUNTIME_MEMBERS
            config_rules += CODEX_CONFIGURATION_MEMBERS
    result = []
    for scope, members, rules in (('runtime', route.get('runtime', {}), runtime_rules),
                                  ('configuration', route['configuration'], config_rules)):
        for member in members:
            rule = next((row for row in rules if row[0] == member), None)
            result.append(dict(scope=scope, member=member,
                               classification=rule[1] if rule else None, reason=rule[2] if rule else None))
    return result


def describe_capacity_key(route):
    required = dict(REQUIRED_RUNTIME_MEMBERS).get(route['adapter'])
    runtime = route.get('runtime') or {}
    if required is None or any(member not in runtime or runtime[member] is None for member in required):
        return None
    members = describe_capacity_members(route)
    if any(row['classification'] is None for row in members):
        return None
    return dict(adapter=route['adapter'], endpoint=route['endpoint'], model=route['model'],
                runtime={row['member']: copy.deepcopy(runtime[row['member']]) for row in members
                         if row['scope'] == 'runtime' and row['classification'] == 'key'},
                configuration={row['member']: copy.deepcopy(route['configuration'][row['member']]) for row in members
                               if row['scope'] == 'configuration' and row['classification'] == 'key'})


def read_capacity_priors():
    return [dict(id=row[0], key=json.loads(row[1]), input_tokens=row[2], capacity_probe=False,
                 source_record='20261113110022-every-open-record-and-review-capacity',
                 fixture='tests/fixtures/route_capacity_measurements.json', fixture_row=row[3], date=row[4])
            if isinstance(row, tuple) else copy.deepcopy(row) for row in CAPACITY_PRIORS]


def has_capacity_key(prior):
    key = prior.get('key')
    if not isinstance(key, dict) or set(key) != {'adapter', 'endpoint', 'model', 'runtime', 'configuration'}:
        return False
    if any(not isinstance(key[field], str) or not key[field] for field in ('adapter', 'endpoint', 'model')):
        return False
    if not isinstance(key['runtime'], dict) or not isinstance(key['configuration'], dict):
        return False
    return describe_capacity_key(key) == key


def validate_capacity_inventory(routes, *, priors=None):
    """K3's command-edge check; unknown members and unsupported joins cannot pass silently."""
    failures = []
    for route in routes:
        for row in describe_capacity_members(route):
            if row['classification'] is None:
                failures.append(dict(reason=row['scope'] + '_member_unclassified',
                                     adapter=route['adapter'], member=row['member']))
    for prior in read_capacity_priors() if priors is None else priors:
        if not has_capacity_key(prior):
            failures.append(dict(reason='prior_key_missing', prior_id=prior['id']))
        if prior.get('joined_keys') and not _has_documented_join(prior):
            failures.append(dict(reason='prior_join_undocumented', prior_id=prior['id']))
    if failures:
        first = failures[0]
        raise ResearchError('review_capacity_evidence_invalid', dict(INVENTORY_MESSAGES)[first['reason']],
                            dict(first, failures=failures))


def _has_documented_join(prior):
    ground = prior.get('documented_ground') or {}
    return bool(ground.get('statement') and str(ground.get('url', '')).startswith('https://'))


def describe_capacity_observation(route, response, *, sends, request, probe_controls=None):
    if not isinstance(response, dict):
        return None
    context = response.get('context_evidence') or {}
    usage = response.get('usage') or {}
    if not isinstance(context, dict) or not isinstance(usage, dict):
        return None
    verified = context.get('input_verified', route['adapter'] == 'openai_responses_v1')
    if sends != 1 or verified is not True or context.get('issues') or context.get('compacted'):
        return None
    from .review_codex_invocation import verify_context
    try:
        if not verify_context(route, request, response):
            return None
    except (AttributeError, KeyError, TypeError, ValueError, ResearchError):
        return None
    tokens = usage.get('input_tokens')
    if type(tokens) is not int or tokens < 0:
        return None
    if probe_controls is not None:
        if response.get('status') != 'completed':
            return None
        from .review_answer_recovery import extract_answer_text
        try:
            output = json.loads(extract_answer_text(response) or '')
        except (TypeError, ValueError):
            return None
        if (not isinstance(output, dict) or output.get('excluded_controls') != []
                or any(output.get(key) != probe_controls.get(key) for key in ('allowed_control', 'evidence_control'))):
            return None
    runtime = context.get('runtime') or route.get('runtime', {})
    if not isinstance(runtime, dict):
        return None
    identity = dict(route, runtime=runtime)
    key = describe_capacity_key(identity)
    return dict(key=key, input_tokens=tokens) if key is not None else None


def read_capacity_sends(route, response):
    if not isinstance(response, dict):
        return None
    if route['adapter'] == 'openai_responses_v1':
        return 1
    execution = response.get('execution') or {}
    if not isinstance(execution, dict):
        return None
    context = response.get('context_evidence') or {}
    events = execution.get('event_types') or []
    if not isinstance(context, dict) or not isinstance(events, list):
        return None
    if route['adapter'] != 'claude_cli_v1':
        return (events.count('turn.started') or 1) if context.get('input_verified') is True else None
    retention = execution.get('system_events') or {}
    if not isinstance(retention, dict) or retention.get('truncated'):
        return None
    try:
        retries = sum(json.loads(base64.b64decode(row['raw_base64'])).get('subtype') == 'api_retry'
                      for row in retention.get('events', []))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return events.count('user') + retries if events.count('user') else None


def _match_capacity_key(prior, key):
    return key is not None and (prior.get('key') == key or
                               _has_documented_join(prior) and key in prior.get('joined_keys', []))


def read_capacity_records(records, kind):
    """Bind provider observations without depending on unrelated imported attempts."""
    from .storage import read_filtered_records
    return (records.get(kind, {}) if kind == 'review_route_probe'
            else read_filtered_records(records, kind, origin='instrumented'))


def read_capacity_evidence(route, records, *, artifacts=None, threshold_models=(), reservation=None):
    """Read only witnesses that can raise the bound or the proposed probe size."""
    key = describe_capacity_key(route)
    priors = read_capacity_priors()
    reservation = route['configuration']['max_output_tokens'] if reservation is None else reservation
    best, hint, sources = 0, 0, []
    verified_probe = None

    def matches_scope(candidate):
        if candidate is None:
            return False
        if threshold_models:
            return candidate['adapter'] == 'claude_cli_v1' and candidate['model'] in threshold_models
        return candidate == key and key is not None

    def matches_probe_class(candidate):
        return candidate is not None and candidate != key and all(candidate[field] == route[field]
                                             for field in ('adapter', 'endpoint', 'model'))

    def observe(witness, source, capacity_probe, *, applies=False):
        nonlocal best, hint, sources, verified_probe
        candidate = witness['key']
        tokens = witness['input_tokens']
        if matches_probe_class(candidate):
            hint = max(hint, tokens)
        if not matches_scope(candidate) and not applies:
            return
        value = max(0, tokens - reservation) if not threshold_models or capacity_probe else tokens
        entry = dict(source, key=copy.deepcopy(candidate), input_tokens=tokens,
                     value=value, capacity_probe=capacity_probe)
        if capacity_probe and (verified_probe is None or value > verified_probe['value']):
            verified_probe = entry
        if value > best:
            best, sources = value, [entry]

    for prior in priors:
        prior_key = prior.get('key')
        if not has_capacity_key(prior):
            continue
        witness = dict(key=prior_key, input_tokens=prior['input_tokens'])
        applies = not threshold_models and _match_capacity_key(prior, key)
        source = dict(id=prior['id'], source='prior', fixture=prior.get('fixture'), fixture_row=prior.get('fixture_row'))
        if applies and prior_key != key:
            source['documented_ground'] = copy.deepcopy(prior['documented_ground'])
        observe(witness, source, prior.get('capacity_probe', False), applies=applies)
    assignments = records.get('review_assignment', {})
    routes = records.get('review_route', {})
    candidates = []
    for kind in ('review_attempt', 'review_route_probe', 'review_output_correction'):
        for record in read_capacity_records(records, kind).values():
            saved_route = routes.get(record.get('route_id') or assignments.get(record.get('assignment_id'), {}).get('route_id'))
            if saved_route is None:
                continue
            saved_route = dict(saved_route, runtime=record.get('runtime') or saved_route.get('runtime', {}))
            witness = record.get('capacity_evidence')
            candidate_key = witness['key'] if witness else describe_capacity_key(saved_route)
            if not matches_scope(candidate_key) and not matches_probe_class(candidate_key):
                continue
            tokens = witness['input_tokens'] if witness else (record.get('usage') or {}).get('input_tokens')
            if type(tokens) is int:
                candidates.append((tokens, len(candidates), kind, record, saved_route, candidate_key))
    for tokens, _, kind, record, saved_route, candidate_key in sorted(candidates, reverse=True):
        capacity_probe = kind == 'review_route_probe' and record.get('capacity') is not None
        value = max(0, tokens - reservation) if not threshold_models or capacity_probe else tokens
        needs_probe = capacity_probe and matches_scope(candidate_key) and verified_probe is None
        if (not needs_probe and (not matches_scope(candidate_key) or value <= best)
                and (not matches_probe_class(candidate_key) or tokens <= hint)):
            continue
        witness = record.get('capacity_evidence')
        if witness is None and artifacts is not None and record.get('response'):
            from .evaluation import Evaluation
            evaluation = Evaluation.of(records, artifacts)
            response = evaluation.read_json(record['response'])
            request = evaluation.read_json(record['request'])
            witness = describe_capacity_observation(saved_route, response, request=request, sends=read_capacity_sends(saved_route, response),
                                                    probe_controls=record.get('positive_controls') if kind == 'review_route_probe' else None)
            if witness is not None and kind == 'review_route_probe':
                from .review_protocol import _route_state
                trial = ChainMap({'review_route_probe': {record['id']: record}}, records)
                if _route_state(trial, artifacts, saved_route['id'])['probe_id'] != record['id']:
                    witness = None
        if witness is not None:
            observe(witness, dict(id=record['id'], source=kind), capacity_probe)
    result = dict(key=key, evidence=sources, input_tokens=best, probe_input_tokens=hint or DEFAULT_PROBE_INPUT_TOKENS,
                  classification=describe_capacity_members(route))
    if verified_probe is not None:
        result['verified_capacity_probe'] = verified_probe
    return result
