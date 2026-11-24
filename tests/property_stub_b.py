"""Answers made from the instructions and user message of one invocation."""

import json
import re


def read_packet(message):
    """Read the JSON after the review packet label in the user message."""
    label, _, body = message.partition('\n')
    if 'packet' not in label.lower():
        raise AssertionError(label)
    return json.loads(body)


def read_binding(packet):
    """Find the stage from the turn member and the presence of a page or decision rows."""
    if 'reading_turn' in packet:
        return 'reading', packet['reading_turn']
    if 'reading_combine' in packet:
        return 'combine', packet['reading_combine']
    final = packet['reading_final']
    if 'closing' in final:
        return 'closing', final
    if 'page_plan_digest' in final:
        return 'page', final
    return 'final', final


def read_answer_bound(binding):
    return binding['capacity']['answer_bound']


def read_targets(binding):
    return binding['page']['targets']


def read_deliveries(packet, binding):
    by_artifact = {row['artifact']['sha256']: row['content'] for row in packet['evidence']}
    return [(row, by_artifact[row['artifact']['sha256']]) for row in binding['plan']['now']]


def read_search_date(text):
    dates = re.findall(r'Searched through (\d{4}-\d\d-\d\d)|"captured_at"\s*:\s*"(\d{4}-\d\d-\d\d)', text)
    return max((left or right for left, right in dates), default='')


def build_notes(packet, binding):
    """Carry visible evidence handles and their digests forward in the reviewer's notes."""
    previous = binding.get('notes') or ''
    handles = dict(re.findall(r'(u\d+) ([0-9a-f]{64})', previous))
    searched_at = read_search_date(previous)
    for row, text in read_deliveries(packet, binding):
        if row['label'].startswith('Shared search fields:') and 'captured_at' in row['label']:
            match = re.match(r'"(\d{4}-\d\d-\d\d)T', text)
            if match:
                searched_at = max(searched_at, match[1])
        if row.get('sha256') and row['start'] == 0 and not row.get('request'):
            if (row['kind'] == 'fulltext' and 'Theorem' in text
                    or row['kind'] == 'search_response' and '"query"' in text
                    or is_evidence_record(row, text)):
                handles[row['handle']] = row['sha256']
                searched_at = max(searched_at, read_search_date(text))
    return 'Reviewed the supplied evidence. Searched through ' + searched_at + ' ' + ' '.join(
        '%s %s' % pair for pair in handles.items()) + '.'


def is_evidence_record(row, text):
    if row['kind'] != 'record':
        return False
    if row['label'] == row['sha256']:
        return True
    try:
        value = json.loads(text)
    except ValueError:
        return False
    return isinstance(value, dict) and (
        {'responses', 'captured_at'} <= value.keys()
        or {'version_id', 'depth', 'inspections'} <= value.keys()
        or {'search_digest', 'shared_fields'} <= value.keys())


def build_excess_excerpts(packet, binding, answer):
    """Keep adding verbatim excerpts with valid targets until the answer exceeds twice its bound."""
    row, text = max(((row, text) for row, text in read_deliveries(packet, binding) if row['kind'] != 'list_page'),
                    key=lambda delivery: len(delivery[1]))
    target = binding['allowances']['targets'][0]['handle']
    quote = dict(unit=row['handle'], text=text[:1000], purpose='This passage bears on the formulation.',
                 targets=[target])
    limit = 2 * read_answer_bound(binding)
    count = max(1, (limit - len(json.dumps(answer))) // (len(json.dumps(quote)) + 1))
    answer['excerpts'] = [quote] * count
    while len(json.dumps(answer, separators=(',', ':'))) <= limit:
        answer['excerpts'].append(quote)


def build_reading_answer(packet, binding, fill):
    notes = build_notes(packet, binding)
    excerpts = []
    if fill:
        notes = notes.ljust(binding['allowances']['notes'], 'n')
        texts = [(row, text) for row, text in read_deliveries(packet, binding) if row['kind'] == 'fulltext']
        for row, text in texts:
            for target in binding['allowances']['targets']:
                # The shortest and the longest usable span of this small text both fit the visible grant.
                if target['remaining'] >= len(text) + 1200 and binding['allowances']['excerpts'] >= len(text) + 1200:
                    excerpts.extend(dict(unit=row['handle'], text=quote,
                                         purpose='The excerpt helps assess the formulation.', targets=[target['handle']])
                                    for quote in (text[:1], text))
    return dict(schema_version=2, stage='reading', notes=notes, excerpts=excerpts, gaps=[], passage_requests=[],
                **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})


def build_page_answer(binding, math, fill):
    rows = []
    for target in read_targets(binding):
        row = dict(formulation=target, coverage='Reviewed the evidence for this formulation.',
                   sentence='The supplied record leaves this formulation open.')
        if fill:
            row['coverage'] = row['coverage'].ljust(800 if math else 798, 'c')
            row['sentence'] = row['sentence'].ljust(300, 's')
        if not math:
            row['grounds'] = [dict(kind='own_argument', text='The argument needs a separate check.')]
        rows.append(row)
    return dict(targets=rows)


def build_findings():
    return {key: 'Assessed the supplied subject.' for key in (
        'statement', 'assumptions', 'scope', 'dependencies', 'policy')}


def fill_closing_answer(output, binding):
    """Use the complete advertised bound while spreading prose across the offered fields."""
    limit = read_answer_bound(binding)
    for name in build_findings():
        output[name] = output[name].ljust(min(1900, len(output[name]) + limit - len(json.dumps(output))), 'x')
    output['reason'] = output['reason'].ljust(
        min(3900, len(output['reason']) + limit - len(json.dumps(output))), 'r')
    while len(json.dumps(output)) < limit:
        ground = dict(kind='own_argument', text='The remaining argument requires checking.')
        output['grounds'].append(ground)
        room = limit - len(json.dumps(output))
        if room < 0:
            output['grounds'].pop()
            output['statement'] += 'x' * (limit - len(json.dumps(output)))
            break
        ground['text'] = ground['text'].ljust(min(590, len(ground['text']) + room), 'g')


def build_math_final(packet, binding):
    native = packet['native']
    refs = [dict(ref=row['handle']) for row in binding['evidence_units']['rows']]
    findings = dict(build_findings(), openness=dict(outcome='supported_open',
        reason='The bound evidence leaves the formulation open.', searched_at=binding['minimum']['searched_through'],
        evidence=refs))
    findings['openness'].update(statement_digest=native['claim_digest'], coverage='Reviewed the bound evidence.')
    return dict(schema_version=2, decision='approve', findings=findings,
                **{key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')})


def build_answer(instructions, message, *, fill=False, over_bound=False, list_allowances=False):
    """Answer one turn, optionally using the allowances or exceeding the answer bound."""
    packet = read_packet(message)
    stage, binding = read_binding(packet)
    math = 'exactory-openness-reading-math-v2' in instructions
    if stage == 'reading':
        output = build_reading_answer(packet, binding, fill)
        if over_bound:
            build_excess_excerpts(packet, binding, output)
        if (list_allowances and binding['turn'] == 1 and 'kind:"list_page",list,after' in instructions
                and binding['allowances']['targets']):
            output['passage_requests'].append(dict(id='allowance-list', kind='list_page', list='allowances', after=None))
        return output
    elif stage == 'combine':
        block = binding['block']
        entry = block['entries'][0]
        output = dict(block=block['handle'], summary='Read the listed entries.',
                      refs=[entry.get('handle', entry.get('block'))])
    elif stage == 'page':
        return build_page_answer(binding, math, fill)
    elif math:
        output = build_math_final(packet, binding)
        field = (output['findings'], 'statement')
    else:
        output = dict(build_findings(), outcome='unresolved', reason='A remaining gap.', grounds=[])
        if stage == 'closing' and 'unread' in binding and 'x1' in instructions:
            kind = 'packet_defect' if 'finding_index' in packet and 'packet_defect' in instructions else 'reviewer_gap'
            output['grounds'].append(dict(kind=kind, text='The listed ranges could not be read.', refs=[dict(ref='x1')]))
        if fill:
            fill_closing_answer(output, binding)
        return output
    if fill and stage == 'final':
        row, name = field
        room = read_answer_bound(binding) - len(json.dumps(output))
        row[name] += 'x' * max(0, room)
    return output
