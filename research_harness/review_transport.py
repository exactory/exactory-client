"""Stateless reviewer transport with a complete, instrumented input boundary.

Only explicitly supplied packet bytes and canonical instructions reach the API.
The transport never loads local agent configuration, memory, history, or tools.
Credentials are a separate argument and are never placed in a saved receipt.
"""

import base64
import contextlib
import hashlib
import http.client
import importlib
import json
import os
import platform
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ResearchError
from .evidence import digest
from .fulltext import detect_media_type

ADAPTER = 'openai_responses_v1'
EXCLUDED_SOURCES = ('author_history', 'prior_assessments', 'startup_memory',
                    'project_instructions', 'hooks', 'shared_cache', 'tool_context', 'fork_resume')
# Local reviewer adapters run a subscription CLI of the host and inspect its observed input. The Claude Code adapter is
# the reviewer of a Claude Code session; the Codex adapter is the reviewer of a Codex session.
LOCAL_ADAPTERS = {'claude_cli_v1': 'review_claude_transport', 'codex_cli_v1': 'review_codex_transport',
                  'codex_cli_framed_v1': 'review_codex_framed_transport',
                  'codex_cli_visual_v1': 'review_codex_visual_transport'}
# Both local adapters carry text only: a binary artifact is named in the packet without its bytes.
TEXT_ONLY_ADAPTERS = frozenset(name for name in LOCAL_ADAPTERS if name != 'codex_cli_visual_v1')
TEXT_ONLY_DELIVERY_VARIABLE = 'EXACTORY_REVIEW_TEXT_ONLY_DELIVERY'


def text_only_enabled():
    """Whether packets are being built for a text-only route."""
    return os.environ.get(TEXT_ONLY_DELIVERY_VARIABLE) == '1'


def is_binary_evidence(data, media_type):
    """Whether evidence bytes travel as an original rather than as text: a PDF, including one that its server
    reported as generic binary, or an image."""
    delivered_type, _ = detect_media_type(data, media_type)
    return delivered_type == 'application/pdf' or delivered_type.startswith('image/')


@contextlib.contextmanager
def text_only_delivery(enabled):
    """Build packets with text-only source deliveries while enabled: binary originals are named without their bytes."""
    previous = os.environ.get(TEXT_ONLY_DELIVERY_VARIABLE)
    if enabled:
        os.environ[TEXT_ONLY_DELIVERY_VARIABLE] = '1'
    try:
        yield
    finally:
        if enabled:
            if previous is None:
                os.environ.pop(TEXT_ONLY_DELIVERY_VARIABLE, None)
            else:
                os.environ[TEXT_ONLY_DELIVERY_VARIABLE] = previous
OMITTED_BYTES_NOTE = ('The original bytes of this artifact are not delivered on the text-only reviewer route. When the '
                      'harness captured an extraction of a full text, its text is a separate evidence entry.')


def _local_adapter(route):
    """The module of a local reviewer adapter, or None for the HTTPS Responses adapter."""
    name = LOCAL_ADAPTERS.get(route['adapter'])
    return None if name is None else importlib.import_module('.' + name, __package__)


def runtime(route=None):
    local = None if not route else _local_adapter(route)
    if local is not None:
        return local.runtime()
    return {'adapter': ADAPTER, 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'host': platform.node(), 'platform': platform.platform(), 'python': platform.python_version(),
            'input_contract': 'explicit-packet-only-v1'}


def validate_route(route):
    local = _local_adapter(route)
    if local is not None:
        return local.validate_route(route)
    if route['adapter'] != ADAPTER:
        raise ResearchError('review_route_unsupported', 'Only the instrumented stateless Responses adapter can execute')
    url = urlsplit(route['endpoint'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment):
        raise ResearchError('invalid_review_route', 'The reviewer endpoint must be an explicit HTTPS URL without credentials')
    config = route['configuration']
    if set(config) != {'max_output_tokens', 'timeout_seconds'}:
        raise ResearchError('invalid_review_route', 'Configure only max_output_tokens and timeout_seconds')
    if type(config['max_output_tokens']) is not int or config['max_output_tokens'] < 1:
        raise ResearchError('invalid_review_route', 'max_output_tokens must be positive')
    if type(config['timeout_seconds']) not in (int, float) or not 0 < config['timeout_seconds'] <= 600:
        raise ResearchError('invalid_review_route', 'timeout_seconds must be positive and at most 600')


CODEX_INPUT_LIMIT_CHARS = 1048576
# The Codex routes whose input the transport fits to the app-server limit.
FITTED_ADAPTERS = ('codex_cli_v1', 'codex_cli_framed_v1')
PACKET_LABEL = 'The packet below is JSON.\n'
ELIDED_TEXT_NOTE = ('The body of this full text is withheld because the reviewer route input limit does not admit '
                    'it; the reading record of this work, with its notes and inspected excerpts, is delivered in full, '
                    'and the artifact reference identifies the exact bytes.')
ELIDED_SEARCH_RESPONSE_NOTE = ('The body of this captured search response is withheld because the reviewer route input '
                               'limit does not admit it; the search record in problem_evidence.openness_searches, with '
                               'its queries, coverage and the disposition of every result, is delivered in full, and the '
                               'artifact reference identifies the exact bytes.')
ELIDED_EVIDENCE_NOTE = ('The body of this evidence artifact is withheld because the reviewer route input limit does not '
                        'admit it; the artifact reference identifies the exact bytes.')
ELIDED_BODY_NOTE = ('This evidence body was delivered in this earlier turn of the same review. It is elided from '
                    'the replayed history so that the exchange fits the reviewer route input limit; request the '
                    'artifact again by its reference if the review needs its text.')
# A packet whose history_delivery names this value replays its session's earlier turns without long evidence
# bodies and without the packets of a scientific history; a packet without a marker replays unchanged.
HISTORY_COMPACT = 'earlier-bodies-and-history-packets-elided'
HISTORY_PACKET_NOTE = ('This packet of an earlier turn was delivered in that turn of the same review session. It is '
                       'elided from the replayed history; the observed output of that turn is kept.')
HISTORY_PACKET_LIMIT_NOTE = ('This packet of an earlier turn of this slot exceeds the reviewer route input limit together with '
                             'the current packet. Its observed output and independence are kept here, and the record '
                             'holds the packet at this digest.')
HISTORY_BODY_NOTE = ('This evidence body was delivered in this earlier turn of the same review and is omitted from the '
                     'replay. The review is verified only when it calls no tool, so request its work with '
                     'source_requests if the review needs this text again.')
# The second marker also drops, from each replayed turn, a part that the current packet repeats unchanged and
# summarizes each replayed source delivery to its request and status. Packets with the first marker replay as before.
HISTORY_COMPACT_V2 = 'earlier-bodies-history-packets-and-repeated-parts-elided'
REPEATABLE_KEYS = ('bars', 'own_bars', 'material_objections', 'source_inventory', 'problem_evidence')
REPEATED_PART_NOTE = 'This part of the earlier packet is repeated unchanged in the current packet.'
DELIVERY_SUMMARY_NOTE = ('This source delivery of an earlier turn is summarized in the replay: its request and status are '
                         'kept, and its evidence was delivered in that turn.')
HTML_ORIGINAL_OMISSION_NOTE = (
    'The HTML original body is withheld because the local reviewer input limit does not admit it. '
    'The exact native plain-text extraction from the same capture is supplied in full as separate evidence. '
    'The HTML artifact descriptor identifies the retained original bytes. Visual layout, figures, or formulas '
    'may lose information in extraction. This delivery does not establish inspection of the HTML original. '
    'Use the delivered extraction for citations, retain any source gaps, and request needed original material.')
REPEATED_EVIDENCE_NOTE = ('This problem evidence was delivered in full in an earlier turn of this review session, and the '
                          'replayed history carries that packet.')


def _elide_history_bodies(message):
    """An earlier user turn without the bodies of its evidence, stating the omission."""
    content = message.get('content')
    if message.get('role') != 'user' or not isinstance(content, str) or not content.startswith(PACKET_LABEL):
        return message
    replayed = json.loads(content[len(PACKET_LABEL):])
    changed = False
    for evidence in replayed.get('evidence', []):
        if isinstance(evidence, dict) and isinstance(evidence.get('content'), str) and len(evidence['content']) > 4096:
            evidence['content'] = ''
            evidence['delivery'] = 'body-elided-from-replayed-history'
            evidence['note'] = ELIDED_BODY_NOTE
            changed = True
    if not changed:
        return message
    return dict(message, content=PACKET_LABEL + json.dumps(replayed, sort_keys=True, ensure_ascii=False))


def _elide_body(evidence, delivery, note):
    if isinstance(evidence, dict) and isinstance(evidence.get('content'), str) and len(evidence['content']) > 4096:
        evidence['content'] = ''
        evidence['delivery'] = delivery
        evidence['note'] = note
        return True
    return False


def _compact_history_message(message, repeated=None):
    """An earlier user turn without its evidence bodies longer than 4096 characters and without the packets of the
    scientific history that it carried; with `repeated`, also without the parts the current packet repeats."""
    content = message.get('content')
    if message.get('role') != 'user' or not isinstance(content, str) or not content.startswith(PACKET_LABEL):
        return message
    replayed = json.loads(content[len(PACKET_LABEL):])
    changed = False
    for evidence in replayed.get('evidence', []):
        changed = _elide_body(evidence, 'body-elided-from-replayed-history', HISTORY_BODY_NOTE) or changed
    for turn in replayed.get('scientific_history', []):
        if isinstance(turn, dict) and isinstance(turn.get('packet'), dict):
            turn['packet'] = {'delivery': 'packet-elided-from-replayed-history',
                              'packet_digest': turn['packet'].get('digest'), 'note': HISTORY_PACKET_NOTE}
            changed = True
    for key, value in (repeated or {}).items():
        if key in replayed and replayed[key] == value:
            replayed[key] = {'delivery': 'repeated-in-the-current-packet', 'note': REPEATED_PART_NOTE}
            changed = True
    if repeated is not None:
        # With the second marker a replayed source delivery keeps its request and status.
        deliveries = replayed.get('source_deliveries')
        if isinstance(deliveries, list) and deliveries:
            replayed['source_deliveries'] = [
                {'request': item.get('request'), 'status': item.get('status'),
                 'delivery': 'summarized-in-replayed-history', 'note': DELIVERY_SUMMARY_NOTE}
                if isinstance(item, dict) else item for item in deliveries]
            changed = True
    if not changed:
        return message
    return dict(message, content=PACKET_LABEL + json.dumps(replayed, sort_keys=True, ensure_ascii=False))


def _collect_source_links(value):
    """The (version_id, sha256) of every source link under a packet, whether it names its artifact directly or, as a
    delivered review payload does, through an artifact reference template."""
    if isinstance(value, dict):
        descriptor = value.get('artifact') or (value.get('artifact_reference_template') or {}).get('artifact_descriptor') or {}
        own = ([(value['version_id'], descriptor['sha256'])] if isinstance(value.get('version_id'), str)
               and isinstance(descriptor, dict) and isinstance(descriptor.get('sha256'), str) else [])
        return own + [link for item in value.values() for link in _collect_source_links(item)]
    if isinstance(value, list):
        return [link for item in value for link in _collect_source_links(item)]
    return []


def _list_evidence(supplied):
    return [evidence for evidence in supplied.get('evidence', []) if isinstance(evidence, dict)]


def _sha256(evidence):
    return (evidence.get('artifact') or {}).get('sha256')


def _reduce_history_bodies(fit):
    """The evidence bodies of the replayed earlier turns, oldest turn first."""
    for index in range(len(fit.messages)):
        fit.messages[index] = _elide_history_bodies(fit.messages[index])
        yield


def _reduce_history_packets(fit):
    """The replayed packets of earlier turns, oldest first, give way to a digest reference; the earlier assistant
    outputs stay as they were observed."""
    for index in range(len(fit.messages)):
        fit.messages[index] = _elide_history_packet(fit.messages[index])
        yield


def _reduce_scientific_history_bodies(fit):
    """The evidence bodies that the packets of a context repair's scientific history re-deliver, in one step."""
    for turn in fit.supplied.get('scientific_history', []):
        for evidence in (turn.get('packet') or {}).get('evidence', []):
            _elide_body(evidence, 'body-elided-from-replayed-history', ELIDED_BODY_NOTE)
    yield


def _reduce_previously_supplied(fit):
    """The bodies that an earlier turn of the session supplied, in one step."""
    supplied_before = {entry.get('sha256') for entry in fit.supplied.get('previously_supplied_evidence', [])
                       if isinstance(entry, dict)}
    kept = fit.protected | fit.requested()
    for evidence in _list_evidence(fit.supplied):
        if _sha256(evidence) in supplied_before and _sha256(evidence) not in kept:
            _elide_body(evidence, 'body-elided-previously-supplied', ELIDED_BODY_NOTE)
    yield


def _reduce_text_priority(fit):
    """A native packet may declare the delivery priority of its full texts; bodies are elided from the lowest
    priority upwards, one per step."""
    priority = ((fit.supplied.get('native') or {}).get('scientific_context') or {}).get('text_delivery_priority') or []
    entries = {_sha256(evidence): evidence for evidence in _list_evidence(fit.supplied)}
    kept = fit.protected | fit.requested()
    for sha256 in reversed(priority):
        if (sha256 not in kept and sha256 in entries
                and _elide_body(entries[sha256], 'body-elided-route-limit', ELIDED_TEXT_NOTE)):
            yield


def _reduce_search_responses(fit):
    """A problem packet attaches the captured responses of the searches that its openness finding binds, and its
    problem evidence delivers each search record with its queries, coverage and result dispositions. The response
    bodies give way, largest first, one per step."""
    searches = (fit.supplied.get('problem_evidence') or {}).get('openness_searches') or []
    responses = {source['response']['sha256'] for search in searches for source in search['sources']} - fit.protected - fit.requested()
    bodies = [evidence for evidence in _list_evidence(fit.supplied) if _sha256(evidence) in responses]
    for evidence in sorted(bodies, key=lambda evidence: -len(evidence.get('content') or '')):
        if _elide_body(evidence, 'body-elided-route-limit', ELIDED_SEARCH_RESPONSE_NOTE):
            yield


def _reduce_scientific_history_packets(fit):
    """The earlier packets of the slot's history, largest first, give way to a digest reference; each turn keeps its
    observed output and its independence."""
    turns = [turn for turn in fit.supplied.get('scientific_history', [])
             if isinstance(turn, dict) and isinstance(turn.get('packet'), dict)]
    for turn in sorted(turns, key=lambda turn: -len(json.dumps(turn['packet'], sort_keys=True, ensure_ascii=False))):
        turn['packet'] = {'delivery': 'packet-elided-route-limit', 'packet_digest': turn['packet'].get('digest'),
                          'note': HISTORY_PACKET_LIMIT_NOTE}
        yield


def _reduce_read_full_texts(fit):
    """The problem evidence delivers each full reading as its record, notes and inspected passages, so the full texts
    that the packet cites through a source link of a fully read work give way, largest first, one per step."""
    read = {row['version_id'] for row in (fit.supplied.get('problem_evidence') or {}).get('readings') or []
            if row.get('depth') == 'fulltext'}
    texts = {sha256 for version_id, sha256 in _collect_source_links(fit.supplied) if version_id in read} - fit.kept()
    bodies = [evidence for evidence in _list_evidence(fit.supplied) if _sha256(evidence) in texts]
    for evidence in sorted(bodies, key=lambda evidence: -len(evidence.get('content') or '')):
        if _elide_body(evidence, 'body-elided-route-limit', ELIDED_TEXT_NOTE):
            yield


def _reduce_other_bodies(fit):
    """Any other evidence body of a problem packet gives way, largest first, one per step."""
    if 'problem_evidence' not in fit.supplied:
        return
    bodies = [evidence for evidence in _list_evidence(fit.supplied) if _sha256(evidence) not in fit.kept()]
    for evidence in sorted(bodies, key=lambda evidence: -len(evidence.get('content') or '')):
        if _elide_body(evidence, 'body-elided-route-limit', ELIDED_EVIDENCE_NOTE):
            yield


def _reduce_html_originals(fit):
    """Withhold an oversized HTML original only with its complete, same-capture native extraction; every extraction
    that justified an omission stays for the later steps."""
    for _ in _elide_html_originals(fit.supplied):
        yield
    fit.protected.update(omission['extraction_sha256'] for delivery in fit.supplied.get('source_deliveries', [])
                         for omission in delivery.get('transport_omissions', []) if 'extraction_sha256' in omission)


def _complete_text_entry(evidence):
    reference = evidence.get('artifact') or {}
    content = evidence.get('content')
    if evidence.get('encoding') != 'utf-8' or not isinstance(content, str) or not content:
        return False
    data = content.encode('utf-8')
    return len(data) == reference.get('size') and hashlib.sha256(data).hexdigest() == reference.get('sha256')


def _elide_html_originals(supplied):
    """Withhold oversized HTML only with its complete, same-capture native extraction."""
    entries = {(entry.get('artifact') or {}).get('sha256'): entry
               for entry in supplied.get('evidence', []) if isinstance(entry, dict)}
    for delivery in supplied.get('source_deliveries', []):
        if (delivery.get('request') or {}).get('depth') != 'fulltext':
            continue
        for provenance in delivery.get('source_provenance', []):
            original = provenance.get('original_descriptor') or {}
            if (provenance.get('kind') is not None or provenance.get('capture_method') != 'http'
                    or provenance.get('origin_verified') is not True
                    or provenance.get('completeness') != 'complete'
                    or provenance.get('delivery_mode') != 'extracted-text-only'
                    or original.get('media_type') not in ('text/html', 'application/xhtml+xml')):
                continue
            original_sha = original.get('sha256')
            html = entries.get(original_sha)
            captured = provenance.get('delivered_sha256', [])
            if html is None or original_sha not in captured or not _complete_text_entry(html):
                continue
            extractions = [entries[sha] for sha in captured if sha != original_sha and sha in entries
                           and (entries[sha].get('artifact') or {}).get('media_type', '').split(';')[0] == 'text/plain'
                           and _complete_text_entry(entries[sha])]
            if len(extractions) != 1:
                continue
            html.update(content='', delivery='body-elided-html-original-route-limit', note=HTML_ORIGINAL_OMISSION_NOTE)
            for related in supplied.get('source_deliveries', []):
                affected = False
                for source in related.get('source_provenance', []):
                    delivered = source.get('delivered_sha256', [])
                    if original_sha in delivered:
                        source.setdefault('captured_delivery_sha256', list(delivered))
                        source['delivered_sha256'] = [sha for sha in delivered if sha != original_sha]
                        affected = True
                if affected:
                    related.setdefault('transport_omissions', []).append({
                        'sha256': original_sha, 'reason': 'local_input_limit',
                        'extraction_sha256': extractions[0]['artifact']['sha256'], 'note': HTML_ORIGINAL_OMISSION_NOTE})
            yield 'html_original:' + original_sha


def _build_local_request(route, prompt, supplied, messages):
    content = PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
    return {'model': route['model'], 'instructions': prompt,
            'input': list(messages) + [{'role': 'user', 'content': content}],
            'tools': [], 'store': False, 'max_output_tokens': route['configuration']['max_output_tokens'],
            'text': {'format': {'type': 'json_object'}}}


def _compact_history(supplied, history):
    """The replayed earlier turns of a session, compacted as the packet's history marker asks."""
    messages = list(history)
    marker = supplied.get('history_delivery')
    if marker in (HISTORY_COMPACT, HISTORY_COMPACT_V2):
        repeated = ({key: supplied[key] for key in REPEATABLE_KEYS if key in supplied}
                    if marker == HISTORY_COMPACT_V2 else None)
        messages = [_compact_history_message(message, repeated) for message in messages]
    return messages


# The reductions that a local route applies, in order, while the input exceeds its limit. Each step is applied one
# reduction at a time until the input fits; an input that no step fits is sent as it stands, so that the provider's
# refusal is recorded as a failed attempt. FIT_ORDER builds every new request. The earlier orders rebuild the requests
# that earlier releases and the records' local runtimes recorded, so that their attempts stay authenticated.
FIT_ORDER = ('history_bodies', 'scientific_history_bodies', 'html_originals', 'previously_supplied', 'text_priority',
             'search_responses', 'scientific_history_packets', 'history_packets', 'read_full_texts', 'source_capacity',
             'other_bodies')
EARLIER_FIT_ORDERS = (
    # The local runtime of the extremal-graph-stability record.
    ('history_bodies', 'scientific_history_bodies', 'html_originals', 'previously_supplied', 'text_priority',
     'source_capacity'),
    # The local runtime of the Sidorenko girth-six record (candidates 10 and 11).
    ('scientific_history_bodies', 'previously_supplied', 'text_priority', 'search_responses',
     'scientific_history_packets', 'history_bodies', 'history_packets', 'read_full_texts', 'other_bodies'),
    # 0.56.1 and 0.57.0.
    ('scientific_history_bodies', 'previously_supplied', 'text_priority', 'scientific_history_packets',
     'history_bodies', 'history_packets'),
    # 0.56.0.
    ('scientific_history_bodies', 'previously_supplied', 'text_priority', 'history_bodies'),
)
_REDUCTIONS = {'history_bodies': _reduce_history_bodies, 'history_packets': _reduce_history_packets,
               'scientific_history_bodies': _reduce_scientific_history_bodies,
               'previously_supplied': _reduce_previously_supplied, 'text_priority': _reduce_text_priority,
               'search_responses': _reduce_search_responses,
               'scientific_history_packets': _reduce_scientific_history_packets,
               'read_full_texts': _reduce_read_full_texts, 'other_bodies': _reduce_other_bodies,
               'html_originals': _reduce_html_originals}


class _Fit:
    """The state of one local input fit: the packet and the replayed messages that the reductions change, and the
    bodies that no reduction may elide. Under FIT_ORDER every reduction of the current evidence also keeps the bodies
    of the sources that this turn delivers on request, except that an HTML original is withheld when its complete
    extraction is delivered and whole sources can be deferred as pending."""

    def __init__(self, supplied, messages, order):
        from .problem_openness_sources import protected_evidence
        self.supplied, self.messages = supplied, messages
        self.protected = set(protected_evidence(supplied))
        self.keeps_requested_sources = order == FIT_ORDER

    def requested(self):
        """Under FIT_ORDER, the bodies of the sources that this turn delivers on request."""
        if not self.keeps_requested_sources:
            return set()
        from .review_source_capacity import _references
        return {sha256 for delivery in self.supplied.get('source_deliveries', []) if delivery.get('status') == 'delivered'
                for sha256 in _references(delivery.get('evidence', []))}

    def kept(self):
        return self.protected | self.requested() if self.keeps_requested_sources else set()


def _fit_local_input(route, prompt, supplied, history, limit, measure, order=FIT_ORDER):
    """Apply the reductions of the order until the input fits the route limit."""
    def fits(request):
        return len(measure(request)) <= limit

    fit = _Fit(supplied, _compact_history(supplied, history), order)
    request = _build_local_request(route, prompt, supplied, fit.messages)
    for step in order:
        if fits(request):
            break
        if step == 'source_capacity':
            from .problem_openness_sources import protected_evidence
            from .review_source_capacity import fit as fit_sources
            batched = fit_sources(supplied, lambda packet: _build_local_request(route, prompt, packet, fit.messages),
                                  measure, limit, protected_evidence(supplied))
            if batched is not None:
                return batched
            continue
        for _ in _REDUCTIONS[step](fit):
            request = _build_local_request(route, prompt, supplied, fit.messages)
            if fits(request):
                break
    return request


def _elide_history_packet(message):
    """An earlier user turn reduced to the digest of the packet it carried, stating the omission."""
    content = message.get('content')
    if message.get('role') != 'user' or not isinstance(content, str) or not content.startswith(PACKET_LABEL):
        return message
    replayed = json.loads(content[len(PACKET_LABEL):])
    if replayed.get('delivery') == 'packet-elided-route-limit':
        return message
    reduced = {'delivery': 'packet-elided-route-limit', 'packet_digest': replayed.get('digest'),
               'history_delivery': replayed.get('history_delivery'), 'note': HISTORY_PACKET_LIMIT_NOTE}
    return dict(message, content=PACKET_LABEL + json.dumps(reduced, sort_keys=True, ensure_ascii=False))


def local_input_limit(route):
    """The input limit, in characters, of a local text route; None for a route without one."""
    if route['adapter'] in FITTED_ADAPTERS:
        return CODEX_INPUT_LIMIT_CHARS
    if route['adapter'] == 'claude_cli_v1':
        from . import review_claude_transport
        return review_claude_transport.INPUT_LIMIT_CHARACTERS
    return None


def _fit_codex_input(route, prompt, supplied, history, order):
    """The Codex app-server admits 1,048,576 characters of input."""
    return _fit_local_input(route, prompt, supplied, history, local_input_limit(route), _local_adapter(route).packet_text,
                            order)


def _fit_claude_input(route, prompt, supplied, history, order):
    """The Claude Code route admits its own limit of characters; the adapter refuses a larger input and records it."""
    from . import review_claude_transport
    return _fit_local_input(route, prompt, supplied, history, local_input_limit(route),
                            review_claude_transport.packet_text, order)


def logical_messages(request):
    """Recover the exact declared logical messages solely from an actual request."""
    from . import review_exact_text, review_exact_text_v2
    messages = request['input']
    if len(messages) == 1 and messages[0].get('role') == 'user':
        for codec in (review_exact_text, review_exact_text_v2):
            if codec.is_encoded(messages[0].get('content')):
                return codec.decode(messages[0]['content'])
    return messages


def request_body(route, prompt, packet, history=(), order=FIT_ORDER):
    """History contains only the same reviewer's verified, canonical bar exchange. A local route fits the input with
    the reductions of the order."""
    validate_route(route)
    supplied = json.loads(json.dumps(packet))
    from .problem_openness_sources import apply_delivery
    apply_delivery(supplied)
    if route['adapter'] == 'codex_cli_visual_v1':
        from .review_visual_inputs import probe_request, source_request
        if set(supplied) == {'allowed_control', 'evidence_control', 'evidence'}:
            return probe_request(route, prompt, supplied)
        request = source_request(route, prompt, supplied, history)
        from .review_visual_context_repair import MARKER, validate_profile
        if MARKER in supplied:
            from .review_exact_text_cost_v1 import optimize_request
            validate_profile(supplied[MARKER])
            request = optimize_request(request)
        return request
    files = []
    for evidence in supplied.get('evidence', []):
        if not isinstance(evidence, dict) or evidence.get('encoding') != 'base64':
            continue
        reference = evidence['artifact']
        if route['adapter'] in TEXT_ONLY_ADAPTERS:
            # A text-only local route carries no original bytes; the omission is stated in the packet.
            evidence.pop('content')
            evidence['encoding'] = 'omitted'
            evidence['delivery'] = 'bytes-omitted-on-text-route'
            evidence['note'] = OMITTED_BYTES_NOTE
            continue
        encoded = evidence.pop('content')
        delivered_type, _ = detect_media_type(base64.b64decode(encoded), reference['media_type'])
        data_url = 'data:' + delivered_type + ';base64,' + encoded
        if delivered_type == 'application/pdf':
            files.append({'type': 'input_file', 'filename': reference['sha256'] + '.pdf', 'file_data': data_url})
        else:
            files.append({'type': 'input_image', 'image_url': data_url})
        evidence['delivery'] = 'attached-exact-bytes'
    # The Responses API refuses text.format json_object unless the input messages
    # contain the word 'json'; the packet is labelled as JSON for that reason.
    content = PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
    if files:
        content = [{'type': 'input_text', 'text': content}] + files
    request = {'model': route['model'], 'instructions': prompt,
               'input': list(history) + [{'role': 'user', 'content': content}],
               'tools': [], 'store': False, 'max_output_tokens': route['configuration']['max_output_tokens'],
               'text': {'format': {'type': 'json_object'}}}
    from .review_source_complete import MARKER, project
    if MARKER in supplied:
        if route['adapter'] != 'codex_cli_v1':
            raise ResearchError('review_complete_source_route', 'Complete visible source input uses the unchanged Codex local route')
        from .review_exact_text import encode
        messages, complete_sources = project(supplied, list(history))
        request['input'] = [{'role': 'user', 'content': encode(messages, complete_sources)}]
        if len(_local_adapter(route).packet_text(request)) > CODEX_INPUT_LIMIT_CHARS:
            from .review_exact_text_v2 import encode as encode_v2
            request['input'] = [{'role': 'user', 'content': encode_v2(messages, complete_sources)}]
        return request
    if route['adapter'] in FITTED_ADAPTERS:
        return _fit_codex_input(route, prompt, supplied, history, order)
    if route['adapter'] == 'claude_cli_v1':
        return _fit_claude_input(route, prompt, supplied, history, order)
    return request


def list_built_requests(route, prompt, packet, history=()):
    """The requests that this harness builds from these inputs: the request of FIT_ORDER first, then, on a local route
    that fits its input, each request of an earlier fit order that differs from it. A recorded request is the
    harness's own when it equals one of them."""
    current = request_body(route, prompt, packet, history)
    yield current
    if route['adapter'] not in FITTED_ADAPTERS + ('claude_cli_v1',):
        return
    seen = [current]
    for order in EARLIER_FIT_ORDERS:
        built = request_body(route, prompt, packet, history, order)
        if built not in seen:
            seen.append(built)
            yield built


def send_request(route, request, credential):
    """Send once without redirects, implicit proxy configuration, or retries."""
    local = _local_adapter(route)
    if local is not None:
        return local.send_request(route, request, credential)
    if not isinstance(credential, str) or not credential:
        raise ResearchError('review_credential_missing', 'An explicit provider credential is required')
    validate_route(route)
    url = urlsplit(route['endpoint'])
    connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=route['configuration']['timeout_seconds'],
                                            context=ssl.create_default_context())
    try:
        body = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        connection.request('POST', url.path or '/', body=body,
                           headers={'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = response.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ResearchError('review_response_too_large', 'The provider response exceeds 16 MiB')
        if not 200 <= response.status < 300:
            raise ResearchError('review_provider_failure', 'The provider returned HTTP ' + str(response.status))
        return json.loads(data)
    finally:
        connection.close()



def context_verified(route, request, response):
    local = _local_adapter(route)
    if local is not None:
        return local.verify_context(route, request, response)
    return (route['adapter'] == ADAPTER and request.get('tools') == [] and request.get('store') is False
            and 'previous_response_id' not in request and 'conversation' not in request)


def response_output(response):
    parts = [part['text'] for item in response.get('output', []) if item.get('type') == 'message'
             for part in item.get('content', []) if part.get('type') == 'output_text']
    output = json.loads(''.join(parts))
    if response.get('status') != 'completed' or not isinstance(output, dict) or not response.get('id'):
        raise ResearchError('review_incomplete', 'The provider did not return a complete JSON object')
    return output


def observe(route, request, credential):
    """Preserve operational failure and unknown usage instead of inventing a verdict."""
    started = time.monotonic()
    result = {'request_digest': digest(request), 'route_digest': digest(route), 'runtime': runtime(route),
              'response': None, 'output': None, 'status': 'failed', 'error': None,
              'usage': {'input_tokens': None, 'output_tokens': None, 'cost_usd': None, 'wall_seconds': None}}
    try:
        response = send_request(route, request, credential)
        result['response'] = response
        usage = response.get('usage') or {}
        for key in ('input_tokens', 'output_tokens'):
            count = usage.get(key)
            if type(count) is int and count >= 0:
                result['usage'][key] = count
        output = response_output(response)
        result.update(status='completed', output=output, provider_response_id=response['id'],
                      observed_model=response.get('model'))
    except (OSError, ValueError, TypeError, KeyError, ResearchError) as error:
        result['error'] = {'kind': type(error).__name__, 'code': getattr(error, 'code', 'review_transport_failure')}
    result['usage']['wall_seconds'] = time.monotonic() - started
    return result
