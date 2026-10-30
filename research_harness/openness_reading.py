"""Openness reviews that read their full texts, captured responses and records over several turns.

When one input of a text route cannot hold the reading units of an openness packet complete, or when the units
together exceed one turn's reading budget, the native_math assignment becomes a reading chain. Two packets qualify: a
problem openness packet, and a math-solver result review whose packet binds an openness minimum. A reading unit has a
kind and an identity: the extracted full text of a read version (version_id with text_sha256), a captured search
response (source_id with text_sha256), or a store record that the packet binds and carries as evidence, a search
record, a reading record or, for a problem, the statement record (record_kind and record_id), read as its exact JSON
text. Each reading turn delivers at most review_excerpts.TURN_FULLTEXT_CHARACTERS of new unit text, whole units or
consecutive parts of a longer one: for a problem the full texts first, with the statement and its formulations; for a
math result the captured responses of its bound searches, then the extracted texts of the originals that its bound
readings pin, then its records, with the native subject and the formulations. The reviewer returns notes and verbatim
excerpts, which the harness checks against the bound bodies. The final turn is the native result review of the whole
packet with the reading record of the earlier turns. Only as far as the route's input limit needs, it withholds the
bodies that reading turns delivered complete and then the notes of the earliest turns; it never withholds an excerpt or
a passage. Every turn is a fresh input; the reading record carries the continuity. A chain that 0.64.2 to 0.66.2
started keeps its recorded turns and continues under these rules. A math chain has its own protocol, under which the
harness rebuilds every turn's record from the observed outputs when it verifies the chain.
"""

import copy
import hashlib
import json

from . import review_excerpts
from . import review_transport as transport
from .bar_sources import _reference, descriptor
from .errors import ResearchError, describe_error_at
from .evidence import digest
from .operations import fields, normalized_text, text

PROTOCOL_V1 = 'exactory-openness-reading-v1'
PROTOCOL_V2 = 'exactory-openness-reading-v2'
PROTOCOL = 'exactory-openness-reading-v3'
# The protocol of a math-solver result review's chain: the units of v3 in the math order, no statement record, and a
# final passage request that must deliver characters.
MATH_PROTOCOL = 'exactory-openness-reading-math-v1'
# The protocols whose units carry the identities of review_excerpts.IDENTITY_FIELDS.
GENERIC_UNIT_PROTOCOLS = (PROTOCOL, MATH_PROTOCOL)
TURN_KEY = 'reading_turn'
RECORD_KEY = 'reading_record'
STAGES = ('reading', 'final')
NOTES_CHARACTERS = 20000
INVALID_RESPONSE = 'invalid_reading_response'
# Draft for the text author.
PASSAGE_PROGRESS_ERR_MSG = ('A reading response in a final turn needs a passage request that delivers characters '
                            'within the remaining passage allowance')
RESPONSE_FIELDS = ('schema_version', 'stage', 'subject_digest', 'claim_digest', 'reviewer', 'excerpts', 'notes',
                   'passage_requests')
# A part of a record or of a captured response is a fragment of its body, so it is saved as plain text.
PART_MEDIA_TYPE = 'text/plain; charset=utf-8'
BODY_WITHHELD_DELIVERY = 'body-delivered-in-reading-turns'
BODY_WITHHELD_NOTE = 'Delivered complete in reading turns {}; body withheld to fit the route input limit.'
NOTES_WITHHELD_NOTE = 'The notes of turn {} are withheld from this input so that it fits the route input limit.'
# After the full texts that text_delivery_priority lists, the final turn withholds the complete bodies of these kinds
# in this order: the other full texts, captured search responses, reading records and search records.
WITHHOLDING_ORDER = ('fulltext', 'search_response', 'reading', 'literature_search')
# The fields that the compact reading record leaves out of a checked excerpt or a passage answer: the source
# descriptor moves to the record's sources, the locator gives start, end and sha256, and the request gives its
# identity and id.
VERBOSE_ENTRY_KEYS = ('artifact_descriptor', 'locator', 'request', 'note', 'checked', 'reading_credit')
# The prompts of 0.64.2 to 0.65.1, kept for the assignments recorded with them.
READING_PROMPT_V1 = (
    'This is one reading turn of an openness review of the exact problem statement in reading_turn.statement. The '
    'full texts that the review must read are longer together than one input of this route can hold, so they reach you '
    'in parts, one group in each turn. reading_turn.reading_plan names the texts delivered now, whose bodies are in '
    'evidence, the texts delivered in earlier turns and the texts that remain. A text delivered in this turn is not '
    'delivered in a later reading turn, and the final turn repeats a full text only when its input has room for it, '
    'so do not count on seeing it again. Each turn is a fresh input without the earlier turns: '
    'reading_turn.reading_record holds your notes, checked excerpts and passages from them, and the final turn rests '
    'on that record. Read every delivered text completely now. Copy verbatim what the openness judgment needs: '
    'definitions, normalizations, every relevant theorem, lemma or proposition with all of its hypotheses, and every '
    'statement that a question is open or has been solved. Return exactly {schema_version:1,stage:"reading",'
    'subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}, with subject_digest, claim_digest and '
    'reviewer copied from reading_turn. notes is a nonempty string of at most 20,000 characters: what each delivered '
    'work proves, what it leaves open, and how it bears on the statement and on each neighbouring formulation in '
    'reading_turn.formulations. excerpts is a list of {version_id,text,purpose}: text is copied character for '
    'character from the delivered text of version_id and has at least 20 and at most 6,000 characters; purpose says '
    'in a few words why you keep it. The harness looks for each excerpt in the stored text of that version after it '
    'changes each character to its NFKC form and each run of white space to one space. A verified excerpt reaches '
    'the later turns as the exact source characters with their location. An excerpt that is not found, is too short '
    'or too long, or names a version without a stored text is kept with status "unmatched" and a reason; its text is '
    'your own copy and was not checked. Verified excerpts have a total of 200,000 characters in the review, and an '
    'excerpt beyond that total has status "over_allowance". passage_requests is a list, possibly empty, of '
    '{id,version_id,anchor,characters}: anchor is a '
    'verbatim phrase of at least 20 characters from a delivered text, and characters is an integer from 1 to 20,000. '
    'The next turn gives the source text that starts at the first occurrence of the anchor, with at most that many '
    'characters, or status "not_found". Delivered passages have a total of 100,000 characters in the review, and a '
    'passage beyond that total has status "over_allowance". reading_turn.reading_record.allowances shows what is used '
    'of both totals. Do not judge openness in this turn; the final turn does that.')
FINAL_PROMPT_V1 = (
    ' This is the final turn of an openness review that read its full texts in earlier reading turns. reading_record '
    'holds those turns: your notes of each turn, your excerpts with their check status, the passages that you '
    'requested and the allowances. A verified excerpt or a delivered passage is the exact text of its source at the '
    'stated location; an unmatched excerpt is your own copy and was not checked. Each full text of the readings was '
    'delivered complete in a reading turn. The evidence of this turn repeats the full texts that this input can hold; '
    'a body that it withholds was read in a reading turn. Base the judgment on your notes, your checked excerpts and '
    'passages, and the full texts that this input holds, and return the native result review described above. When '
    'you need part of a text again before you can judge, return instead {schema_version:1,stage:"reading",'
    'subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests} with subject_digest, claim_digest and '
    'reviewer copied from native and with at least one passage request {id,version_id,anchor,characters}, where '
    'anchor is a verbatim phrase of at least 20 characters and characters is an integer from 1 to 20,000. A further '
    'final turn then delivers those passages. When reading_record.allowances.passages has no characters left, return '
    'the native result review.')
# The prompts of 0.65.2 to 0.66.2, kept for the assignments recorded with them.
READING_PROMPT_V2 = (
    'This is one reading turn of an openness review of the exact problem statement in reading_turn.statement. The '
    'full texts that the review must read are longer together than one input of this route can hold, and a turn has '
    'a limited time, so they reach you in parts. One reading turn delivers at most 120,000 characters of new full '
    'text: a text within that size arrives whole, and a longer text arrives in consecutive parts of at most 120,000 '
    'characters, one after another in later turns. reading_turn.reading_plan names what is delivered now, whose '
    'bodies are in evidence, what was delivered in earlier turns and what remains; each entry has '
    '{version_id,part,parts,start,end,characters}, where start and end are character offsets in the stored text of '
    'the version. A text or part delivered in this turn is not delivered in a later reading turn, and the final turn '
    'repeats a full text only when its input has room for it, so do not count on seeing it again. Each turn is a '
    'fresh input without the earlier turns: reading_turn.reading_record holds your notes, checked excerpts and '
    'passages from them, and the final turn rests on that record. Read everything delivered now completely. Copy '
    'verbatim what the openness judgment needs: definitions, normalizations, every relevant theorem, lemma or '
    'proposition with all of its hypotheses, and every statement that a question is open or has been solved. Return '
    'exactly {schema_version:1,stage:"reading",subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}, '
    'with subject_digest, claim_digest and reviewer copied from reading_turn. notes is a nonempty string of at most '
    '20,000 characters: what each delivered work or part proves, what it leaves open, and how it bears on the '
    'statement and on each neighbouring formulation in reading_turn.formulations. excerpts is a list of '
    '{version_id,text,purpose}: text is copied character for character from a delivered text or part of version_id '
    'and has at least 20 and at most 6,000 characters; purpose says in a few words why you keep it. The harness looks '
    'for each excerpt in the whole stored text of that version after it changes each character to its NFKC form and '
    'each run of white space to one space. A verified excerpt reaches the later turns as the exact source characters '
    'with their location. An excerpt that is not found, is too short or too long, or names a version without a stored '
    'text is kept with status "unmatched" and a reason; its text is your own copy and was not checked. Verified '
    'excerpts have a total of 200,000 characters in the review, and an excerpt beyond that total has status '
    '"over_allowance". passage_requests is a list, possibly empty, of {id,version_id,anchor,characters}: anchor is a '
    'verbatim phrase of at least 20 characters from a delivered text, and characters is an integer from 1 to 20,000. '
    'The next turn gives the source text that starts at the first occurrence of the anchor, with at most that many '
    'characters, or status "not_found". Delivered passages have a total of 100,000 characters in the review, and a '
    'passage beyond that total has status "over_allowance". reading_turn.reading_record.allowances shows what is used '
    'of both totals. Do not judge openness in this turn; the final turn does that.')
FINAL_PROMPT_V2 = FINAL_PROMPT_V1.replace(
    'Each full text of the readings was delivered complete in a reading turn.',
    'Each full text of the readings was delivered complete in reading turns, whole or in all of its parts.')
READING_PROMPT = (
    'This is one reading turn of an openness review of the exact statement in reading_turn.statement. Each turn is a '
    'fresh input without earlier conversation. Read everything delivered now completely; do not judge openness until '
    'the final turn.\n\n'
    'reading_turn.reading_plan lists delivered_before, delivered_now and remaining. The bodies of delivered_now are '
    'in evidence. A unit has a kind and identity: fulltext uses version_id; search_response uses source_id; record '
    'uses record_kind and record_id. record_kind is literature_search, reading or problem_statement. Full texts and '
    'captured responses may also specify text_sha256, identifying the whole stored body. Use that hash when supplied. '
    'A historical plan entry without kind is a full text. Store records arrive as their exact JSON text. In this '
    "problem review, full texts arrive before the packet's records.\n\n"
    'One reading turn delivers at most 120,000 characters of new unit text. A larger body arrives in consecutive '
    'parts. Each plan entry gives part, parts, start, end and characters; start and end are character offsets in the '
    'whole body, and its artifact identifies the delivered whole body or slice. A delivered unit or part need not '
    'appear again.\n\n'
    'reading_turn.reading_record carries earlier notes, checked excerpts, passage answers and allowances. Its sources '
    'list gives one whole-source descriptor per cited artifact, with the identities that name it. A verified excerpt '
    'or delivered passage gives its exact source text, character offsets start and end, and the SHA-256 of that '
    "span's UTF-8 bytes. Unmatched excerpts are your own unchecked copies. Passage answers identify their requesting "
    'assignment and request ID.\n\n'
    'To fit the route input limit, later turns may withhold the notes of the earliest turns, replacing notes with '
    'notes_withheld. The final turn may first withhold bodies delivered complete in earlier reading turns, with a '
    'note naming those turns. The harness never withholds an excerpt or a passage. The statement, formulations and '
    'allowances remain. Keep what the judgment needs as excerpts, and carry essential conclusions and unresolved '
    'issues from earlier notes into your latest notes.\n\n'
    'Return exactly '
    '{schema_version:1,stage:"reading",subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}. Copy '
    'subject_digest, claim_digest and reviewer exactly from reading_turn. notes is a nonempty string of at most '
    '20,000 characters explaining what the delivered material establishes, what remains open, and its bearing on the '
    'statement and every formulation in reading_turn.formulations.\n\n'
    'excerpts is a list of {version_id,text,purpose}, {source_id,text,purpose}, or '
    '{record_kind,record_id,text,purpose}. The first two forms may include text_sha256. Do not mix identity forms. '
    'Copy definitions, normalizations, relevant results with every hypothesis, open or solved statements, and '
    'material record details such as queries, coverage, dispositions and reading notes. purpose briefly explains why '
    'the quotation matters. Copy record text from the JSON body, including escapes inside its strings, rather than '
    'from a reconstructed field value.\n\n'
    'The harness matches against the whole stored body after changing each character to its NFKC form, collapsing '
    'whitespace runs and trimming outer whitespace. It uses the first match and returns the original source '
    'characters. An excerpt needs at least 20 normalized characters and its matched original span must be at most '
    '6,000 characters. A missing source, absent match or invalid length produces status "unmatched" with a reason. '
    'Verified excerpts consume at most 200,000 original characters over the chain; an otherwise matching excerpt '
    'beyond that allowance has status "over_allowance" and no delivered text.\n\n'
    'passage_requests is a possibly empty list of {id,<the same identity fields>,anchor,characters}. IDs are unique '
    'within this response. anchor is a verbatim phrase of at least 20 normalized characters. characters is an integer '
    'from 1 to 20,000. The next turn returns original text starting at the first matching anchor, up to that length '
    'and the end of the body, or status "not_found". Delivered passages consume at most 100,000 characters over the '
    'chain; a passage exceeding the remaining allowance has status "over_allowance" and no delivered text. '
    'reading_turn.reading_record.allowances shows both totals and their usage. Excerpts and passages do not establish '
    'complete reading of a unit.')
FINAL_PROMPT = (
    ' This is the final turn of an openness reading chain. reading_record carries the preceding turns, retained '
    'notes, checked excerpts, passage answers and allowances. Judge the exact native subject and every formulation in '
    'native.scientific_context.openness_minimum using this record and the evidence delivered here.\n\n'
    'Reading units have three kinds. A fulltext is named by version_id, a search_response by source_id, and a record '
    'by record_kind and record_id. record_kind is literature_search, reading or problem_statement. Full texts and '
    'captured responses may also specify text_sha256 identifying the whole body. Store records were delivered as '
    'exact JSON text. The sources list gives one whole-source descriptor per cited artifact and its identities. '
    "Verified excerpts and delivered passages contain original source characters with start, end and the span's "
    'SHA-256. Unmatched excerpts are unchecked reviewer copies; over_allowance entries deliver no text.\n\n'
    'A required unit must have reached earlier reading turns complete, whole or in all its parts, or be delivered '
    'complete in this final input. To fit the route limit, this input may withhold bodies already delivered complete '
    'in reading turns and then notes of the earliest turns. Each withheld body names its delivering turns; '
    'notes_withheld names the turn whose notes are absent. The harness never withholds an excerpt or a passage. The '
    'statement, formulations and allowances remain. Do not assume absent bodies or notes remain available in '
    'conversation.\n\n'
    'Return the native result review described above. If you need a bounded passage before judging and the passage '
    'allowance remains, return instead exactly '
    '{schema_version:1,stage:"reading",subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}. Copy '
    'the three binding fields from native. notes is a nonempty string of at most 20,000 characters; retain essential '
    'earlier conclusions and unresolved issues in these latest notes.\n\n'
    'excerpts uses {version_id,text,purpose}, {source_id,text,purpose}, or {record_kind,record_id,text,purpose}, with '
    'optional text_sha256 on the first two forms. Preserve material quotations as excerpts. Matching uses '
    'per-character NFKC, collapsed whitespace and the first occurrence in the whole stored body. Each excerpt needs '
    'at least 20 normalized characters and at most 6,000 original matched characters; the verified-excerpt allowance '
    'is 200,000 characters for the chain. Unmatched copies remain explicitly unchecked.\n\n'
    'A reading response here must contain at least one passage request: {id,<the same identity '
    'fields>,anchor,characters}. IDs are unique within this response. anchor has at least 20 normalized characters; '
    'characters is an integer from 1 to 20,000. Copy record anchors from the exact JSON text, including escapes '
    'inside strings. A subsequent turn supplies the original text from the first matching anchor, or "not_found" or '
    '"over_allowance". Delivered passages have a chain allowance of 100,000 characters. When no passage allowance '
    'remains, return the native result review. Missing decisive evidence remains a gap; an excerpt, passage or '
    'descriptor does not establish complete-unit delivery.')
# The prompts of a math-solver result review's chain. Drafts for the text author.
MATH_READING_PROMPT = (
    'This is one reading turn of an openness review of a native mathematical result. reading_turn.statement is the '
    'exact native subject under review, a checkpoint or a closure subject, and reading_turn.formulations holds the '
    'exact statement of the openness minimum and its neighbouring formulations. Each turn is a fresh input without '
    'earlier conversation. Read everything delivered now completely; do not judge openness until the final turn.\n\n'
    'reading_turn.reading_plan lists delivered_before, delivered_now and remaining. The bodies of delivered_now are '
    'in evidence. A unit has a kind and identity: search_response uses source_id; fulltext uses version_id; record '
    'uses record_kind and record_id, where record_kind is literature_search or reading. Captured responses and full '
    'texts also carry text_sha256, the SHA-256 of the whole stored body. No problem statement record is delivered. The '
    "units arrive in this order: the captured responses of the bound searches, in the order of the minimum's searches "
    "and then of each search's responses; then the extracted main texts of the originals that the bound readings pin, "
    'by version_id and then text_sha256; then the bound search records and the bound reading records. Store records '
    'arrive as their exact JSON text.\n\n'
    'One reading turn delivers at most 120,000 characters of new unit text. A larger body arrives in consecutive '
    'parts. Each plan entry gives part, parts, start, end and characters; start and end are character offsets in the '
    'whole body, and its artifact identifies the delivered whole body or slice. A delivered unit or part need not '
    'appear again.\n\n'
    'reading_turn.reading_record carries earlier notes, checked excerpts, passage answers and allowances. Its sources '
    'list gives one whole-source descriptor per cited artifact, with the identities that name it. A verified excerpt '
    'or delivered passage gives its exact source text, character offsets start and end, and the SHA-256 of that '
    "span's UTF-8 bytes. Unmatched excerpts are your own unchecked copies. Passage answers identify their requesting "
    'assignment and request ID.\n\n'
    'To fit the route input limit, later turns may withhold the notes of the earliest turns, replacing notes with '
    'notes_withheld. The final turn may first withhold bodies delivered complete in earlier reading turns, with a '
    'note naming those turns. The harness never withholds an excerpt or a passage. The native subject, formulations '
    'and allowances remain. Keep what the judgment needs as excerpts, and carry essential conclusions and unresolved '
    'issues from earlier notes into your latest notes.\n\n'
    'Return exactly '
    '{schema_version:1,stage:"reading",subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}. Copy '
    'subject_digest, claim_digest and reviewer exactly from reading_turn. notes is a nonempty string of at most '
    '20,000 characters explaining what the delivered material establishes, what remains open, and its bearing on the '
    'native subject and every formulation in reading_turn.formulations.\n\n'
    'excerpts is a list of {source_id,text,purpose}, {version_id,text,purpose}, or '
    '{record_kind,record_id,text,purpose}. The first two forms may include text_sha256; include it when one version_id '
    'names several full texts. Do not mix identity forms. Copy definitions, normalizations, relevant results with '
    'every hypothesis, open or solved statements, and material search and record details such as queries, returned '
    'results, coverage, dispositions and reading notes. purpose briefly explains why the quotation matters. Copy text '
    'from a captured response or a record as it stands in its JSON text, including escapes inside its strings, rather '
    'than from a reconstructed field value.\n\n'
    'The harness matches against the whole stored body after changing each character to its NFKC form, collapsing '
    'whitespace runs and trimming outer whitespace. It uses the first match and returns the original source '
    'characters. An excerpt needs at least 20 normalized characters and its matched original span must be at most '
    '6,000 characters. A missing source, an identity that names several bodies without text_sha256, an absent match '
    'or an invalid length produces status "unmatched" with a reason. Verified excerpts consume at most 200,000 '
    'original characters over the chain; an otherwise matching excerpt beyond that allowance has status '
    '"over_allowance" and no delivered text.\n\n'
    'passage_requests is a possibly empty list of {id,<the same identity fields>,anchor,characters}. IDs are unique '
    'within this response. anchor is a verbatim phrase of at least 20 normalized characters. characters is an integer '
    'from 1 to 20,000. The next turn returns original text starting at the first matching anchor, up to that length '
    'and the end of the body, or status "not_found". Delivered passages consume at most 100,000 characters over the '
    'chain; a passage exceeding the remaining allowance has status "over_allowance" and no delivered text. '
    'reading_turn.reading_record.allowances shows both totals and their usage. Excerpts and passages do not establish '
    'complete reading of a unit.')
MATH_FINAL_PROMPT = (
    ' This is the final turn of an openness reading chain over a native mathematical result. reading_record carries '
    'the preceding turns, retained notes, checked excerpts, passage answers and allowances. Judge the exact native '
    'subject, a checkpoint or a closure subject, and every formulation in '
    'native.scientific_context.openness_minimum using this record and the evidence delivered here.\n\n'
    'Reading units have three kinds. A search_response is named by source_id, a fulltext by version_id, and a record '
    'by record_kind and record_id; record_kind is literature_search or reading. No problem statement record is part '
    'of this review. Captured responses and full texts also carry text_sha256 identifying the whole body. The reading '
    'turns delivered the captured responses of the bound searches first, then the extracted texts of the originals '
    'that the bound readings pin, then the search and reading records, each record as exact JSON text. The sources '
    'list gives one whole-source descriptor per cited artifact and its identities. Verified excerpts and delivered '
    "passages contain original source characters with start, end and the span's SHA-256. Unmatched excerpts are "
    'unchecked reviewer copies; over_allowance entries deliver no text.\n\n'
    'A required unit must have reached earlier reading turns complete, whole or in all its parts, or be delivered '
    'complete in this final input. To fit the route limit, this input may withhold bodies already delivered complete '
    'in reading turns and then notes of the earliest turns. Each withheld body names its delivering turns; '
    'notes_withheld names the turn whose notes are absent. The harness never withholds an excerpt or a passage. The '
    'native subject, formulations and allowances remain. Do not assume absent bodies or notes remain available in '
    'conversation.\n\n'
    'Return the native result review described above. If you need a bounded passage before judging and the passage '
    'allowance remains, return instead exactly '
    '{schema_version:1,stage:"reading",subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}. Copy '
    'the three binding fields from native. notes is a nonempty string of at most 20,000 characters; retain essential '
    'earlier conclusions and unresolved issues in these latest notes.\n\n'
    'excerpts uses {source_id,text,purpose}, {version_id,text,purpose}, or {record_kind,record_id,text,purpose}, with '
    'optional text_sha256 on the first two forms. Preserve material quotations as excerpts. Matching uses '
    'per-character NFKC, collapsed whitespace and the first occurrence in the whole stored body. Each excerpt needs '
    'at least 20 normalized characters and at most 6,000 original matched characters; the verified-excerpt allowance '
    'is 200,000 characters for the chain. Unmatched copies remain explicitly unchecked.\n\n'
    'A reading response here must contain at least one passage request: {id,<the same identity '
    'fields>,anchor,characters}. IDs are unique within this response. anchor has at least 20 normalized characters; '
    'characters is an integer from 1 to 20,000. Copy anchors in captured responses and records from the exact JSON '
    'text, including escapes inside strings. A subsequent turn supplies the original text from the first matching '
    'anchor. Delivered passages have a chain allowance of 100,000 characters. At least one of your passage requests '
    'must deliver characters: a reading response whose every request has an absent anchor, names no single stored '
    'body, or exceeds the remaining allowance is refused, and the same turn can then run again. When no passage '
    'allowance remains, return the native result review. Missing decisive evidence remains a gap; an excerpt, '
    'passage or descriptor does not establish complete-unit delivery.')
PROMPTS = {PROTOCOL_V1: {'reading': READING_PROMPT_V1, 'final': FINAL_PROMPT_V1},
           PROTOCOL_V2: {'reading': READING_PROMPT_V2, 'final': FINAL_PROMPT_V2},
           PROTOCOL: {'reading': READING_PROMPT, 'final': FINAL_PROMPT},
           MATH_PROTOCOL: {'reading': MATH_READING_PROMPT, 'final': MATH_FINAL_PROMPT}}


def is_openness_packet(native):
    """Whether a native packet is a problem openness packet exported by `export --kind problem-openness`."""
    return (native['contract'].get('orchestration') == 'problem_creation'
            and 'openness_minimum' in native['scientific_context'])


def is_math_openness_packet(native):
    """Whether a native packet is a math-solver result review that binds an openness minimum: a result outside problem
    creation, for a checkpoint or for the objective's closure."""
    return (native['kind'] == 'result' and 'openness_minimum' in native['scientific_context']
            and native['contract'].get('orchestration') != 'problem_creation')


def find_chain_protocol(native):
    """The reading protocol of a new turn of a chain over a native packet."""
    return MATH_PROTOCOL if is_math_openness_packet(native) else PROTOCOL


def find_stage(packet):
    """The reading-chain stage of a native_math packet: 'reading', 'final', or None for a single-turn packet."""
    return 'reading' if TURN_KEY in packet else 'final' if RECORD_KEY in packet else None


def find_binding(packet):
    """The reviewer binding of a native_math packet: its native packet, or a reading turn's copy of the subject,
    claim and reviewer of that native packet."""
    return packet[TURN_KEY] if TURN_KEY in packet else packet['native']


def _find_record(packet):
    """The reading record that a reading or final turn's packet carries."""
    return packet[TURN_KEY][RECORD_KEY] if TURN_KEY in packet else packet[RECORD_KEY]


def find_protocol(packet):
    """The reading protocol under which a reading or final turn's packet was built; None for a single-turn packet."""
    if TURN_KEY in packet:
        return packet[TURN_KEY]['protocol']
    return packet[RECORD_KEY]['protocol'] if RECORD_KEY in packet else None


def _describe_identity(value):
    """The identity of the reading unit that a value names, as {kind, <identity fields>}."""
    identity = review_excerpts.find_identity(value)
    return dict(identity[1:], kind=identity[0])


def list_reading_texts(records, native):
    """The full-text rows of an openness packet: for each version that a bound reading read, the extracted text of each
    of its main captures that the packet carries as evidence, so that a capture recorded after the export does not
    enlarge the chain. An alternate main body that the packet's openness_delivery contract omits is not read; the
    selected representation of its version is. A row is {kind, version_id, text_sha256, artifact}; the rows follow
    the packet's text_delivery_priority, then the version id and the text digest."""
    from .graph import main_captures
    bound = set(native['scientific_context']['openness_minimum']['reading_digests'])
    omitted = set((native['scientific_context'].get('openness_delivery') or {}).get('alternate_body_digests', []))
    carried = {entry['artifact']['sha256'] for entry in native['evidence']} - omitted
    priority = {sha256: index for index, sha256 in
                enumerate(native['scientific_context'].get('text_delivery_priority', []))}
    ranks, rows = {}, {}
    for version in {reading['version_id'] for reading in records.get('reading', {}).values()
                    if digest(reading) in bound}:
        work = records.get('work', {}).get(version)
        if work is None or not work.get('fulltexts'):
            continue
        ranks[version] = min((priority[capture['text']['sha256']] for capture in work['fulltexts']
                              if capture.get('text') and capture['text']['sha256'] in priority),
                             default=len(priority))
        for capture in main_captures(records, work):
            reference = capture.get('text')
            if (capture.get('extraction_status') == 'extracted' and reference is not None
                    and reference['sha256'] in carried):
                rows[version, reference['sha256']] = {'kind': 'fulltext', 'version_id': version,
                                                      'text_sha256': reference['sha256'], 'artifact': reference}
    return sorted(rows.values(), key=lambda row: (ranks[row['version_id']], row['version_id'], row['text_sha256']))


def list_math_response_rows(records, artifacts, native):
    """The captured-response rows of a math result openness packet: for each bound search in the order of the minimum,
    the captured response of each of its responses in the order of the search record that the packet carries. The
    packet must carry each response body, so that a capture recorded after the export never enlarges the chain. A row
    is {kind, source_id, text_sha256, artifact}."""
    carried = {entry['artifact']['sha256']: entry['artifact'] for entry in native['evidence']}
    rows = {}
    for binding in native['scientific_context']['openness_minimum']['searches']:
        reference = carried.get(binding['search_digest'])
        if reference is None:
            raise ResearchError('openness_reading_incomplete', 'The openness packet does not carry a record that it '
                                'binds', {'record_kind': 'literature_search', 'sha256': binding['search_digest']})
        for response in json.loads(artifacts.read(reference))['responses']:
            body = (records.get('source', {}).get(response['source_id']) or {}).get('response')
            if body is None or body['sha256'] not in carried:
                raise ResearchError('openness_reading_incomplete', 'The openness packet does not carry the captured '
                                    'response of a bound search', {'source_id': response['source_id']})
            rows.setdefault(response['source_id'], {'kind': 'search_response', 'source_id': response['source_id'],
                                                    'text_sha256': body['sha256'], 'artifact': body})
    return list(rows.values())


def list_math_text_rows(records, native):
    """The full-text rows of a math result openness packet: for each bound reading whose source bundle pins an original,
    the extracted text of that original's main capture, by version id and then text digest. The packet must carry each
    text, so that a capture recorded after the export never enlarges the chain. A row is {kind, version_id,
    text_sha256, artifact}."""
    from .graph import main_captures
    bound = set(native['scientific_context']['openness_minimum']['reading_digests'])
    carried = {entry['artifact']['sha256'] for entry in native['evidence']}
    rows = {}
    for reading in records.get('reading', {}).values():
        if digest(reading) not in bound or reading.get('bundle_id') is None:
            continue
        bundle = records.get('source_bundle', {}).get(reading['bundle_id'])
        work = records.get('work', {}).get(reading['version_id'])
        texts = [] if bundle is None or work is None else [
            capture['text'] for capture in main_captures(records, work)
            if capture['original']['sha256'] == bundle['original_sha256']
            and capture.get('extraction_status') == 'extracted' and capture.get('text') is not None]
        if not texts or texts[0]['sha256'] not in carried:
            raise ResearchError('openness_reading_incomplete', 'The openness packet does not carry the extracted text '
                                'of an original that a bound reading pins',
                                {'reading_id': reading['id'], 'version_id': reading['version_id']})
        rows[reading['version_id'], texts[0]['sha256']] = {'kind': 'fulltext', 'version_id': reading['version_id'],
                                                           'text_sha256': texts[0]['sha256'], 'artifact': texts[0]}
    return [rows[key] for key in sorted(rows)]


def list_record_rows(artifacts, native):
    """The record rows of an openness packet, each the exact artifact of a store record that the packet binds and
    carries as evidence: the bound search records in the order of the minimum, the bound reading records by record id,
    then, for a problem, the statement record. The artifact of a record holds its canonical JSON, so its SHA-256 is the
    record digest that the packet binds. A row is {kind, record_kind, record_id, artifact}."""
    minimum = native['scientific_context']['openness_minimum']
    carried = {entry['artifact']['sha256']: entry['artifact'] for entry in native['evidence']}
    bound = ([('literature_search', row['search_digest']) for row in minimum['searches']]
             + [('reading', sha256) for sha256 in minimum['reading_digests']])
    if is_openness_packet(native):
        bound.append(('problem_statement', native['subject_digest']))
    rows = {}
    for record_kind, sha256 in bound:
        reference = carried.get(sha256)
        if reference is None:
            raise ResearchError('openness_reading_incomplete', 'The openness packet does not carry a record that it '
                                'binds', {'record_kind': record_kind, 'sha256': sha256})
        record_id = json.loads(artifacts.read(reference))['id']
        rows.setdefault((record_kind, record_id), {'kind': 'record', 'record_kind': record_kind,
                                                   'record_id': record_id, 'artifact': reference})
    rows = list(rows.values())
    return ([row for row in rows if row['record_kind'] == 'literature_search']
            + sorted((row for row in rows if row['record_kind'] == 'reading'), key=lambda row: row['record_id'])
            + [row for row in rows if row['record_kind'] == 'problem_statement'])


def list_reading_rows(records, artifacts, native):
    """The whole reading units that an openness packet requires, in the order of delivery: for a problem its full texts,
    then its records; for a math result its captured responses, its pinned full texts, then its records."""
    if is_math_openness_packet(native):
        return (list_math_response_rows(records, artifacts, native) + list_math_text_rows(records, native)
                + list_record_rows(artifacts, native))
    return list_reading_texts(records, native) + list_record_rows(artifacts, native)


def list_reading_units(artifacts, rows):
    """The reading units of whole rows, in order: each body whole when it fits one turn's reading budget, otherwise its
    consecutive parts, each saved as its own text artifact, a part of a record or a captured response as plain text.
    A unit is the row's kind and identity with {part, parts, start, end, characters, artifact_descriptor}, where start
    and end are character offsets in the whole body."""
    units = []
    for row in rows:
        content = artifacts.read(row['artifact']).decode('utf-8')
        spans = review_excerpts.compute_part_spans(content)
        media_type = None if row['kind'] == 'fulltext' else PART_MEDIA_TYPE
        for part, (start, end) in enumerate(spans, 1):
            reference = review_excerpts.save_part(artifacts, content, row['artifact'], start, end, media_type)
            units.append(dict(_describe_identity(row), part=part, parts=len(spans), start=start, end=end,
                              characters=end - start, artifact_descriptor=descriptor(reference)))
    return units


def _compute_spans(artifacts, rows):
    """For each row's identity, the [start, end) and the SHA-256 of the exact UTF-8 bytes of each of its parts."""
    spans = {}
    for row in rows:
        content = artifacts.read(row['artifact']).decode('utf-8')
        spans[review_excerpts.find_identity(row)] = [
            (start, end, hashlib.sha256(content[start:end].encode('utf-8')).hexdigest())
            for start, end in review_excerpts.compute_part_spans(content)]
    return spans


def _collect_unit_sources(rows):
    """The whole bodies of the rows by the required identity of their unit and by SHA-256: the catalog against which
    the excerpts and passages of a reading chain are checked."""
    sources = {}
    for row in rows:
        required = review_excerpts.IDENTITY_FIELDS[row['kind']][0]
        key = (row['kind'],) + tuple((name, row[name]) for name in required)
        sources.setdefault(key, {})[row['artifact']['sha256']] = row['artifact']
    return sources


def _measure(route, request):
    """The number of input characters of a request as the route's adapter measures them."""
    return len(transport._local_adapter(route).packet_text(request))


def _fits(route, prompt, packet, artifacts_now):
    """Whether the route's actual fitted request for a packet fits its input limit and delivers every listed body
    complete."""
    request = transport.request_body(route, prompt, packet)
    if _measure(route, request) > transport.local_input_limit(route):
        return False
    complete = review_excerpts.find_complete_bodies(review_excerpts.read_sent_packet(request) or {})
    return all(reference['sha256'] in complete for reference in artifacts_now)


def _render(route, prompt, packet):
    """The request of a packet, digested again after a change, as the route sends it without any capacity
    reduction."""
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    return transport.request_body(route, prompt, packet, order=())


def _measure_parts(route, prompt, packet):
    """The input characters of a packet's request and their division into the notes, excerpts and passages of its
    reading record and the base packet, each part being the characters that removing it saves."""
    candidate = copy.deepcopy(packet)
    record = _find_record(candidate)
    sizes = [_measure(route, _render(route, prompt, candidate))]
    for turn in record['turns']:
        turn.pop('notes', None)
        turn.pop('notes_withheld', None)
    sizes.append(_measure(route, _render(route, prompt, candidate)))
    record['excerpts'] = []
    sizes.append(_measure(route, _render(route, prompt, candidate)))
    record['passages'] = []
    sizes.append(_measure(route, _render(route, prompt, candidate)))
    return {'input_characters': sizes[0], 'input_limit_characters': transport.local_input_limit(route),
            'parts': {'base_packet': sizes[3], 'notes': sizes[0] - sizes[1], 'excerpts': sizes[1] - sizes[2],
                      'passages': sizes[2] - sizes[3]}}


def _fit_turn(route, prompt, packet, bodies=()):
    """A copy of a turn's packet whose request fits the route's input limit, with that request. While the request does
    not fit, it withholds one item at a time: the listed bodies in order, each given as its SHA-256 and the reading
    turns that delivered it, then the notes of the earlier turns, oldest first. It never withholds an excerpt or a
    passage. When nothing more can be withheld, review_input_too_large names the characters of the base packet, the
    notes, the excerpts and the passages."""
    candidate = copy.deepcopy(packet)
    limit = transport.local_input_limit(route)
    bodies, turns = iter(bodies), iter(_find_record(candidate)['turns'])
    while True:
        request = _render(route, prompt, candidate)
        if _measure(route, request) <= limit:
            return candidate, request
        withheld = next(bodies, None)
        if withheld is not None:
            sha256, assignment_ids = withheld
            for entry in candidate['evidence']:
                if entry['artifact']['sha256'] == sha256:
                    entry.update(content='', delivery=BODY_WITHHELD_DELIVERY,
                                 note=BODY_WITHHELD_NOTE.format(', '.join(assignment_ids)))
            continue
        turn = next(turns, None)
        if turn is None:
            details = _measure_parts(route, prompt, candidate)
            parts = details['parts']
            raise ResearchError('review_input_too_large',
                                'The ' + find_stage(candidate) + ' turn of the reading chain has '
                                + str(details['input_characters']) + ' input characters after it withholds every body '
                                'and note that it may withhold, above the route input limit of ' + str(limit)
                                + ': base packet ' + str(parts['base_packet']) + ', notes ' + str(parts['notes'])
                                + ', excerpts ' + str(parts['excerpts']) + ', passages ' + str(parts['passages']) + '.',
                                details)
        del turn['notes']
        turn['notes_withheld'] = NOTES_WITHHELD_NOTE.format(turn['assignment_id'])


def _assemble_reading_turn(artifacts, packet, record, delivered, now, remaining, turn):
    """A reading turn packet whose only evidence is the units delivered now. The plan and the record name every other
    body by its descriptor, so that no earlier body is delivered again."""
    from .review_protocol import _evidence
    native = packet['native']
    minimum = native['scientific_context']['openness_minimum']
    plan = {'turn': turn, 'delivered_before': delivered, 'remaining': remaining,
            'delivered_now': [dict({key: item for key, item in row.items() if key != 'artifact_descriptor'},
                                   artifact=_reference(row['artifact_descriptor'])) for row in now]}
    value = {key: packet[key] for key in ('protocol', 'role', 'reviewer_id', 'dossier_id')}
    value[TURN_KEY] = dict({key: native[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
                           protocol=record['protocol'], statement=native['subject'], reading_plan=plan,
                           reading_record=record,
                           formulations={'statement': minimum['statement'], 'neighbours': minimum['neighbours']})
    value['evidence'] = _evidence(artifacts, plan['delivered_now'])
    value['digest'] = digest(value)
    return value


def _build_reading_turn(artifacts, route, packet, record, delivered, candidates, turn):
    """The next reading turn: the longest prefix of the remaining units, at least one, whose characters fit one turn's
    reading budget and whose request, with the earliest notes withheld as far as needed, fits the route and delivers
    each of them complete."""
    from .review_protocol import canonical_prompt
    prompt = canonical_prompt('native_math', reading_stage='reading', reading_protocol=record['protocol'])['text']
    chosen, characters = None, 0
    for count, unit in enumerate(candidates, 1):
        characters += unit['characters']
        if count > 1 and characters > review_excerpts.TURN_FULLTEXT_CHARACTERS:
            break
        trial = _assemble_reading_turn(artifacts, packet, record, delivered, candidates[:count], candidates[count:],
                                       turn)
        try:
            trial, request = _fit_turn(route, prompt, trial)
        except ResearchError:
            if chosen is None:
                raise
            break
        complete = review_excerpts.find_complete_bodies(review_excerpts.read_sent_packet(request) or {})
        if not all(row['artifact_descriptor']['sha256'] in complete for row in candidates[:count]):
            break
        chosen = trial
    if chosen is None:
        raise ResearchError('review_input_too_large', 'The reading turn does not deliver its first unit complete',
                            {'unit': {key: item for key, item in candidates[0].items()
                                      if key != 'artifact_descriptor'}})
    return chosen


def _list_withholdable_bodies(packet, rows, spans, observations):
    """The bodies that the final turn may withhold, in the order in which it withholds them, each as its SHA-256 and
    the reading turns that delivered it: the full texts from the end of text_delivery_priority, then the other full
    texts, the captured responses, the reading records and the search records, each kind largest first. A body
    qualifies when the actual requests of reading turns delivered it complete, whole or in all of its parts. The
    statement record and the bodies that proof, source or screening contracts protect stay."""
    from .problem_openness_sources import protected_evidence, required_proof
    native = packet['native']
    protected = set(protected_evidence(packet)) | required_proof(native)
    carried = {entry['artifact']['sha256'] for entry in packet['evidence']}
    priority = native['scientific_context'].get('text_delivery_priority', [])
    whole, parts = _collect_coverage(observations)
    ranked = []
    for row in rows:
        key, sha256 = review_excerpts.find_identity(row), row['artifact']['sha256']
        if (row.get('record_kind') == 'problem_statement' or sha256 in protected or sha256 not in carried
                or not _is_complete(key, spans[key], whole, parts)):
            continue
        if row['kind'] == 'fulltext' and sha256 in priority:
            rank = (0, -priority.index(sha256))
        else:
            rank = (1 + WITHHOLDING_ORDER.index(row.get('record_kind', row['kind'])), -spans[key][-1][1])
        delivering = [observation['assignment_id'] for observation in observations if observation['reading']
                      and (key in observation['whole'] or any(item[0] == key for item in observation['parts']))]
        ranked.append((rank, key, sha256, delivering))
    bodies, seen = [], set()
    for _, _, sha256, delivering in sorted(ranked):
        if sha256 not in seen:
            seen.add(sha256)
            bodies.append((sha256, delivering))
    return bodies


def _build_final_turn(route, packet, record, rows, spans, observations):
    """The native packet with the reading record, withholding only as far as its request needs to fit the route."""
    from .review_protocol import canonical_prompt
    value = {key: item for key, item in packet.items() if key != 'digest'}
    value[RECORD_KEY] = record
    bodies = _list_withholdable_bodies(value, rows, spans, observations)
    prompt = canonical_prompt('native_math', reading_stage='final', reading_protocol=record['protocol'])['text']
    return _fit_turn(route, prompt, value, bodies)[0]


def _read_prior(records, artifacts, value, prior_id):
    """The earlier turn that a chain turn continues, with its packet and its observed reading response."""
    from .review_protocol import assignment_state, find_replacement, has_ended_without_output
    prior = records.get('review_assignment', {}).get(prior_id)
    if prior is None:
        raise ResearchError('review_record_missing', 'Missing review_assignment: ' + str(prior_id))
    if find_replacement(records, prior) is not None:
        raise ResearchError('review_assignment_replaced',
                            'This turn ended without output and a later turn replaced it; continue the replacement')
    if (prior['role'] != 'native_math' or 'reading_chain' not in prior
            or prior['context']['native_packet'] != value['context']['native_packet']
            or prior['route_id'] != value['route_id']
            or normalized_text(prior['reviewer_id']) != normalized_text(value['reviewer_id'])
            or normalized_text(prior['author_id']) != normalized_text(value['author_id'])):
        raise ResearchError('review_source_continuation_mismatch',
                            'Continue a reading chain of the same native packet with the same reviewer, route and '
                            'author')
    # A turn whose attempts all ended without output does not hold the chain: this assignment replaces it.
    if any(saved['role'] == 'native_math' and saved['context'].get('prior_assignment_id') == prior_id
           and not has_ended_without_output(records, saved['id'])
           for saved in records.get('review_assignment', {}).values()):
        raise ResearchError('review_source_continuation_fork', 'Continue the latest turn of the reading chain')
    state = assignment_state(records, artifacts, prior_id)
    if not state['ready']:
        raise ResearchError('review_source_continuation_unverified',
                            'The earlier turn needs an observed, authenticated output in a verified context',
                            {'obligations': state['obligations']})
    prior_packet = json.loads(artifacts.read(prior['packet']))
    validate_turn_output(prior_packet, state['output'])
    _require(has_deliverable_passage(records, artifacts, prior_packet, state['output']), PASSAGE_PROGRESS_ERR_MSG,
             path='/passage_requests')
    if state['output'].get('stage') != 'reading':
        raise ResearchError('review_resampling_forbidden',
                            'A final turn that returned the native result ends the chain')
    return prior, prior_packet, state


def _list_chain(records, assignment):
    """The turns of a reading chain from its first assignment to this one, by their prior_assignment_id links."""
    chain = [assignment]
    while chain[0]['context'].get('prior_assignment_id') is not None:
        prior_id = chain[0]['context']['prior_assignment_id']
        prior = records.get('review_assignment', {}).get(prior_id)
        if prior is None:
            raise ResearchError('openness_reading_incomplete', 'A turn of the reading chain is missing',
                                {'assignment_id': prior_id})
        chain.insert(0, prior)
    return chain


def _observe_turns(records, artifacts, assignments, rows, spans):
    """For each turn, its authenticated observation: the units whose whole body and the parts whose exact slice its
    actual recorded request delivered complete, its observed output, and whether it is a reading turn. Every turn must
    be ready."""
    from .review_protocol import assignment_state
    observations = []
    for assignment in assignments:
        state = assignment_state(records, artifacts, assignment['id'])
        if not state['ready']:
            raise ResearchError('review_context_unverified',
                                'Every turn of the reading chain needs an observed, authenticated output in a verified '
                                'context', {'assignment_id': assignment['id'], 'obligations': state['obligations']})
        attempt = records['review_attempt'][state['attempt_id']]
        sent = review_excerpts.read_sent_packet(json.loads(artifacts.read(attempt['request']))) or {}
        complete = review_excerpts.find_complete_bodies(sent)
        packet = json.loads(artifacts.read(assignment['packet']))
        whole, parts, delivered_parts = set(), set(), []
        for row in rows:
            key = review_excerpts.find_identity(row)
            if row['artifact']['sha256'] in complete:
                whole.add(key)
            for part, (start, end, sha256) in enumerate(spans[key], 1):
                if len(spans[key]) > 1 and sha256 in complete:
                    parts.add((key, part))
                    delivered_parts.append(dict(_describe_identity(row), part=part, parts=len(spans[key]),
                                                start=start, end=end, characters=end - start))
        turn = {'assignment_id': assignment['id'], 'attempt_id': attempt['id'],
                'delivered_units': [_describe_identity(row) for row in rows
                                    if review_excerpts.find_identity(row) in whole]}
        if delivered_parts:
            turn['delivered_parts'] = delivered_parts
        reading = find_stage(packet) == 'reading'
        observations.append({'assignment_id': assignment['id'], 'output': state['output'], 'reading': reading,
                             'delivered_now': packet[TURN_KEY]['reading_plan']['delivered_now'] if reading else [],
                             'whole': whole, 'parts': parts, 'turn': turn})
    return observations


def _collect_coverage(observations):
    """The identities of the units whose whole body, and the (identity, part) pairs of the parts, that the reading
    turns among the observations delivered complete."""
    whole, parts = set(), set()
    for observation in observations:
        if observation['reading']:
            whole.update(observation['whole'])
            parts.update(observation['parts'])
    return whole, parts


def _is_complete(key, spans, whole, parts):
    """Whether a unit was delivered complete: its whole body, or each of its parts."""
    return key in whole or len(spans) > 1 and all((key, part) in parts for part in range(1, len(spans) + 1))


def _require_observed_turns(record, observations):
    """A reading record names the observed attempts of the turns that it carries, in order."""
    if ([[turn['assignment_id'], turn['attempt_id']] for turn in record['turns']]
            != [[observation['assignment_id'], observation['turn']['attempt_id']] for observation in observations]):
        raise ResearchError('openness_reading_incomplete',
                            'The reading record no longer names the observed turns of its chain')


def _list_remaining_units(artifacts, rows, spans, observations):
    """The units that a continued chain still delivers: for each required row in order that no reading turn delivered
    complete, the row whole or its parts that no reading turn delivered. A chain that 0.64.2 to 0.65.2 started, whose
    plans named full texts only, therefore continues with the packet's records."""
    whole, parts = _collect_coverage(observations)
    units = []
    for row in rows:
        key = review_excerpts.find_identity(row)
        if not _is_complete(key, spans[key], whole, parts):
            units.extend(unit for unit in list_reading_units(artifacts, [row]) if (key, unit['part']) not in parts)
    return units


def _add_source(sources, reference, identity):
    """Name a cited artifact in the record's sources once, with every identity that names it."""
    source = next((source for source in sources if source['artifact_descriptor'] == reference), None)
    if source is None:
        source = {'artifact_descriptor': reference, 'identities': []}
        sources.append(source)
    if identity not in source['identities']:
        source['identities'].append(identity)


def _compact_entry(entry, sources):
    """A checked excerpt or a passage answer in the compact form of the reading record: its identity, status, text and
    the fields that its status needs, with start, end and the SHA-256 of a verified span. The descriptor of its source
    joins sources. An entry already in that form stays as it is."""
    compact = {key: item for key, item in entry.items() if key not in VERBOSE_ENTRY_KEYS}
    if 'request' in entry:
        compact.update(review_excerpts.find_identity(entry['request'])[1:], request_id=entry['request']['id'])
    if 'locator' in entry:
        compact.update({key: entry['locator'][key] for key in ('start', 'end', 'sha256')})
    if 'artifact_descriptor' in entry:
        _add_source(sources, entry['artifact_descriptor'], _describe_identity(entry.get('request', entry)))
    return compact


def _extend_record(records, artifacts, prior_packet, state, rows, observations, protocol):
    """The compact reading record of the given protocol after the prior turn. It lists every observed turn of the chain
    with the units that its actual request delivered and with its notes from its observed output, keeps the earlier
    excerpts and passage answers, and adds the prior output's excerpts and the answers to its passage requests, checked
    under the prior's own protocol. A verbose record of 0.64.2 to 0.66.2 keeps its checked texts and allowance use."""
    earlier = _find_record(prior_packet)
    _require_observed_turns(earlier, observations[:-1])
    unit_sources = _collect_unit_sources(rows) if find_protocol(prior_packet) in GENERIC_UNIT_PROTOCOLS else None
    excerpts, allowances = review_excerpts.check_excerpts(records, artifacts, state['output']['excerpts'],
                                                          earlier['allowances'], unit_sources=unit_sources)
    passages, allowances = review_excerpts.answer_passages(records, artifacts, state['output']['passage_requests'],
                                                           allowances, unit_sources=unit_sources)
    sources = copy.deepcopy(earlier.get('sources', []))
    excerpts = [_compact_entry(entry, sources) for entry in earlier['excerpts'] + excerpts]
    passages = [_compact_entry(entry, sources) for entry in earlier['passages'] + passages]
    requests = [(observation['assignment_id'], request['id']) for observation in observations
                for request in observation['output']['passage_requests']]
    if [passage['request_id'] for passage in passages] != [request_id for _, request_id in requests]:
        raise ResearchError('openness_reading_incomplete',
                            'The passage answers of the reading record no longer follow the requests of its turns')
    for passage, (assignment_id, _) in zip(passages, requests):
        passage['assignment_id'] = assignment_id
    turns = [dict(observation['turn'], notes=observation['output']['notes']) for observation in observations]
    return {'protocol': protocol, 'turns': turns, 'sources': sources, 'excerpts': excerpts, 'passages': passages,
            'allowances': allowances}


def _create_record(protocol):
    """The reading record that a chain's first turn carries: no turns, sources, excerpts or passages yet."""
    return {'protocol': protocol, 'turns': [], 'sources': [], 'excerpts': [], 'passages': [],
            'allowances': review_excerpts.create_allowances()}


def prepare_packet(records, artifacts, value, route, packet):
    """The packet of a native_math assignment and its place in a reading chain. An openness packet, of a problem or of
    a math result, stays a single turn when one input of the route holds every reading unit complete and its units
    total at most one turn's reading budget. Otherwise the first assignment is reading turn 1; a turn that continues a
    chain is the next reading turn while a required unit has not reached a reading turn complete, and then the final
    turn."""
    native = packet['native']
    prior_id = value['context'].get('prior_assignment_id')
    if prior_id is None:
        if (not (is_openness_packet(native) or is_math_openness_packet(native))
                or transport.local_input_limit(route) is None):
            return packet, None
        from .review_protocol import canonical_prompt
        rows = list_reading_rows(records, artifacts, native)
        characters = sum(len(artifacts.read(row['artifact']).decode('utf-8')) for row in rows)
        if (characters <= review_excerpts.TURN_FULLTEXT_CHARACTERS
                and _fits(route, canonical_prompt('native_math')['text'], packet, [row['artifact'] for row in rows])):
            return packet, None
        record = _create_record(find_chain_protocol(native))
        chain = {'first_assignment_id': value['id'], 'turn': 1}
        return _build_reading_turn(artifacts, route, packet, record, [], list_reading_units(artifacts, rows), 1), chain
    prior, prior_packet, state = _read_prior(records, artifacts, value, prior_id)
    rows = list_reading_rows(records, artifacts, native)
    spans = _compute_spans(artifacts, rows)
    observations = _observe_turns(records, artifacts, _list_chain(records, prior), rows, spans)
    record = _extend_record(records, artifacts, prior_packet, state, rows, observations, find_chain_protocol(native))
    chain = dict(prior['reading_chain'], turn=prior['reading_chain']['turn'] + 1)
    remaining = _list_remaining_units(artifacts, rows, spans, observations)
    if remaining:
        delivered = [dict({key: item for key, item in row.items() if key != 'artifact'},
                          artifact_descriptor=descriptor(row['artifact']))
                     for observation in observations for row in observation['delivered_now']]
        return _build_reading_turn(artifacts, route, packet, record, delivered, remaining, chain['turn']), chain
    return _build_final_turn(route, packet, record, rows, spans, observations), chain


def _require(condition, message, **details):
    if not condition:
        raise ResearchError(INVALID_RESPONSE, message, details)


def _validate_reading_response(binding, output, protocol):
    _require(isinstance(output, dict) and set(output) == set(RESPONSE_FIELDS),
             'A reading response is exactly {schema_version,stage,subject_digest,claim_digest,reviewer,excerpts,notes,'
             'passage_requests}', path='')
    _require(type(output['schema_version']) is int and output['schema_version'] == 1,
             'A reading response has schema_version 1 and stage "reading"', path='/schema_version')
    _require(output['stage'] == 'reading',
             'A reading response has schema_version 1 and stage "reading"', path='/stage')
    for key in ('subject_digest', 'claim_digest', 'reviewer'):
        _require(output[key] == binding[key], 'Copy subject_digest, claim_digest and reviewer exactly', path='/' + key)
    with describe_error_at('/notes'):
        _require(isinstance(output['notes'], str), 'notes is a nonempty string of at most 20,000 characters')
        _require(bool(output['notes'].strip()) and len(output['notes']) <= NOTES_CHARACTERS,
                 'notes is a nonempty string of at most 20,000 characters', characters=len(output['notes']),
                 minimum_characters=1, maximum_characters=NOTES_CHARACTERS)
    # The responses of 0.64.2 to 0.66.2 name versions only.
    kinds = review_excerpts.UNIT_KINDS if protocol in GENERIC_UNIT_PROTOCOLS else None
    review_excerpts.validate_excerpts(output['excerpts'], INVALID_RESPONSE, kinds)
    _require(isinstance(output['passage_requests'], list), 'passage_requests is a list', path='/passage_requests')
    seen = set()
    for index, request in enumerate(output['passage_requests']):
        path = '/passage_requests/' + str(index)
        identifier = request.get('id') if isinstance(request, dict) else None
        context = {'request_id': identifier} if (
            isinstance(identifier, str) and identifier.strip() and '\x00' not in identifier
            and not any('\ud800' <= character <= '\udfff' for character in identifier)) else {}
        with describe_error_at(path, **context):
            required, optional = review_excerpts.find_identity_fields(request, kinds)
            fields(request, ('id', 'anchor', 'characters') + required, optional, code=INVALID_RESPONSE, path=path)
            with describe_error_at(path + '/id'):
                text(request['id'], 'Passage request ID', code=INVALID_RESPONSE)
                _require(request['id'] not in seen, 'Passage request IDs are unique')
            seen.add(request['id'])
            review_excerpts.validate_passage_request(request, INVALID_RESPONSE, kinds, path=path)


def validate_turn_output(packet, output):
    """The output contract of a reading-chain turn under the protocol of its packet. A reading turn returns a reading
    response. A final turn returns the native result review, or a reading response with at least one passage request
    while the passage allowance has characters left."""
    from .native_review import validate_output
    final = find_stage(packet) == 'final'
    if final and not (isinstance(output, dict) and output.get('stage') == 'reading'):
        validate_output(packet['native'], output)
        return
    _validate_reading_response(find_binding(packet), output, find_protocol(packet))
    if final:
        _require(bool(output['passage_requests']), 'A reading response in a final turn requests at least one passage',
                 path='/passage_requests', items=len(output['passage_requests']), minimum_items=1)
        allowance = packet[RECORD_KEY]['allowances']['passages']
        _require(allowance['used'] < allowance['characters'],
                 'The passage allowance is used up; return the native result review', path='/passage_requests',
                 used_characters=allowance['used'], maximum_characters=allowance['characters'])


def has_deliverable_passage(records, artifacts, packet, output):
    """Whether a valid turn output may continue its chain under the passage-progress rule of the math protocol: a
    reading response in a final turn must request at least one passage that the harness delivers from a bound body
    within the remaining passage allowance. Every other output, and every output under a problem protocol, may."""
    if (find_stage(packet) != 'final' or packet[RECORD_KEY]['protocol'] != MATH_PROTOCOL
            or output.get('stage') != 'reading'):
        return True
    allowances = packet[RECORD_KEY]['allowances']
    unit_sources = _collect_unit_sources(list_reading_rows(records, artifacts, packet['native']))
    _, after = review_excerpts.answer_passages(records, artifacts, output['passage_requests'], allowances,
                                               unit_sources=unit_sources)
    return after['passages']['used'] > allowances['passages']['used']


def find_final_assignment(records, artifacts, identifier):
    """The effective terminal turn of the reading chain whose first turn is the given assignment. A continuation that
    ended without output and that a later continuation of the same turn replaced is history, not a branch; any other
    second continuation is a fork. The terminal turn is a final turn whose observed output is the native result."""
    from .review_protocol import assignment_state, find_replacement, has_ended_without_output
    assignments = records.get('review_assignment', {})
    current = assignments[identifier]
    while True:
        live = [saved for saved in assignments.values()
                if saved['role'] == 'native_math' and saved['context'].get('prior_assignment_id') == current['id']
                and not (has_ended_without_output(records, saved['id']) and find_replacement(records, saved))]
        if len(live) > 1:
            raise ResearchError('review_source_continuation_fork', 'The reading chain has more than one live '
                                'continuation', {'assignment_ids': sorted(saved['id'] for saved in live)})
        if not live:
            break
        current = live[0]
    output = assignment_state(records, artifacts, current['id'])['output']
    if (find_stage(json.loads(artifacts.read(current['packet']))) != 'final' or not isinstance(output, dict)
            or output.get('stage') == 'reading'):
        raise ResearchError('openness_reading_incomplete', 'Continue the reading chain to a final turn that returns '
                            'the native result', {'assignment_id': current['id']})
    return current


def _list_legacy_texts(records, native):
    """The stored extracted full text of each version that the readings of the openness minimum read, ordered by the
    packet's text_delivery_priority and then by version id: the full texts of the chains whose final turn 0.64.2 to
    0.66.2 built."""
    reading_digests = set(native['scientific_context']['openness_minimum']['reading_digests'])
    versions = {reading['version_id'] for reading in records.get('reading', {}).values()
                if digest(reading) in reading_digests}
    priority = {sha256: index for index, sha256 in
                enumerate(native['scientific_context'].get('text_delivery_priority', []))}

    def find_rank(version):
        captured = [capture['text']['sha256'] for capture in records['work'][version]['fulltexts']
                    if capture.get('text')]
        return min((priority[sha256] for sha256 in captured if sha256 in priority), default=len(priority))
    texts = [{'version_id': version, 'artifact': review_excerpts.find_fulltext(records, version)}
             for version in versions]
    texts = [row for row in texts if row['artifact'] is not None]
    return sorted(texts, key=lambda row: (find_rank(row['version_id']), row['version_id']))


def _verify_legacy_chain(records, artifacts, assignment):
    """verify_chain for a final turn that 0.64.2 to 0.66.2 built, by the rules of those releases: every full text of
    the readings must have reached reading turns complete, whole or in all of its parts."""
    from .native_review import load_packet
    from .review_protocol import assignment_state
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    packet = json.loads(artifacts.read(assignment['packet']))
    chain, current = [], records['review_assignment'].get(assignment['context'].get('prior_assignment_id'))
    while current is not None:
        chain.insert(0, current)
        current = records['review_assignment'].get(current['context'].get('prior_assignment_id'))
    texts = _list_legacy_texts(records, native)
    part_digests = {}
    for row in texts:
        digests = review_excerpts.compute_part_digests(artifacts.read(row['artifact']).decode('utf-8'))
        part_digests[row['version_id']] = digests if len(digests) > 1 else []
    turns, read = [], set()
    for turn in chain:
        state = assignment_state(records, artifacts, turn['id'])
        if not state['ready']:
            raise ResearchError('review_context_unverified',
                                'Every turn of the reading chain needs an observed, authenticated output in a verified '
                                'context', {'assignment_id': turn['id'], 'obligations': state['obligations']})
        attempt = records['review_attempt'][state['attempt_id']]
        sent = review_excerpts.read_sent_packet(json.loads(artifacts.read(attempt['request']))) or {}
        complete = review_excerpts.find_complete_bodies(sent)
        delivered = [row['version_id'] for row in texts if row['artifact']['sha256'] in complete]
        parts = [{'version_id': row['version_id'], 'part': part, 'parts': len(part_digests[row['version_id']])}
                 for row in texts for part, sha256 in enumerate(part_digests[row['version_id']], 1)
                 if sha256 in complete]
        if find_stage(json.loads(artifacts.read(turn['packet']))) == 'reading':
            read.update(delivered)
            read.update((row['version_id'], row['part']) for row in parts)
        entry = {'assignment_id': turn['id'], 'attempt_id': attempt['id'], 'delivered': delivered}
        if parts:
            entry['delivered_parts'] = parts
        turns.append(entry)
    if ([[row['assignment_id'], row['attempt_id']] for row in packet[RECORD_KEY]['turns']]
            != [[row['assignment_id'], row['attempt_id']] for row in turns]):
        raise ResearchError('openness_reading_incomplete',
                            'The reading record of the final turn no longer names the observed turns of its chain')
    missing = [row['version_id'] for row in texts if row['version_id'] not in read
               and not (part_digests[row['version_id']] and all(
                   (row['version_id'], part) in read for part in range(1, len(part_digests[row['version_id']]) + 1)))]
    if missing:
        raise ResearchError('openness_reading_incomplete',
                            'Every full text of the readings must reach a reading turn of the chain complete',
                            {'version_ids': missing})
    return turns


def _project_withheld_notes(expected, carried):
    """The expected reading record with the notes of each turn that the carried record withholds replaced by the
    withholding note: the only change that fitting a turn to the route makes to its record."""
    projected = copy.deepcopy(expected)
    for turn, entry in zip(projected['turns'], carried['turns']):
        if 'notes_withheld' in entry and 'notes' in turn:
            del turn['notes']
            turn['notes_withheld'] = NOTES_WITHHELD_NOTE.format(turn['assignment_id'])
    return projected


def _require_withheld_bodies(packet, rows, spans, observations):
    """A final turn withholds a prefix of the bodies that it may withhold, in the withholding order, each with the note
    that names the reading turns that delivered it."""
    withheld = {entry['artifact']['sha256']: entry['note'] for entry in packet['evidence']
                if entry.get('delivery') == BODY_WITHHELD_DELIVERY}
    allowed = _list_withholdable_bodies(packet, rows, spans, observations)[:len(withheld)]
    if withheld != {sha256: BODY_WITHHELD_NOTE.format(', '.join(delivering)) for sha256, delivering in allowed}:
        raise ResearchError('openness_reading_incomplete', 'A final turn withholds a body that its reading turns did '
                            'not deliver complete', {'sha256': sorted(withheld)})


def _require_math_continuity(records, artifacts, native, chain, rows, spans, observations):
    """A math chain keeps the binding of its first turn: the native packet, route, reviewer, author, protocol and the
    canonical prompt of each stage. Each turn carries the reading record that the observed outputs of the turns before
    it give, with only the notes that fitting withholds, and a final turn withholds only bodies that reading turns
    delivered complete. Every turn but the last returned a valid reading response that continued the chain, and the
    last is a final turn whose output is the native result."""
    from .review_protocol import canonical_prompt
    first, record = chain[0], _create_record(MATH_PROTOCOL)
    for index, (assignment, observation) in enumerate(zip(chain, observations)):
        packet, output = json.loads(artifacts.read(assignment['packet'])), observation['output']
        stage = find_stage(packet)
        if stage == 'final':
            bound = packet['native'] == native
        else:
            turn = packet[TURN_KEY]
            bound = turn['statement'] == native['subject'] and all(
                turn[key] == native[key] for key in ('subject_digest', 'claim_digest', 'reviewer'))
        if (not bound or find_protocol(packet) != MATH_PROTOCOL
                or assignment['context']['native_packet'] != first['context']['native_packet']
                or assignment['route_id'] != first['route_id']
                or normalized_text(assignment['reviewer_id']) != normalized_text(first['reviewer_id'])
                or normalized_text(assignment['author_id']) != normalized_text(first['author_id'])
                or assignment.get('reading_chain') != {'first_assignment_id': first['id'], 'turn': index + 1}
                or assignment['prompt'] != canonical_prompt('native_math', reading_stage=stage,
                                                            reading_protocol=MATH_PROTOCOL)):
            raise ResearchError('openness_reading_incomplete', 'Every turn of a math reading chain keeps the binding, '
                                'protocol and prompt of its first turn', {'assignment_id': assignment['id']})
        carried = _find_record(packet)
        if _project_withheld_notes(record, carried) != carried:
            raise ResearchError('openness_reading_incomplete', 'A turn carries a reading record that the observed '
                                'outputs of the earlier turns do not give', {'assignment_id': assignment['id']})
        if stage == 'final':
            _require_withheld_bodies(packet, rows, spans, observations[:index])
        validate_turn_output(packet, output)
        last = index + 1 == len(chain)
        if last != (stage == 'final' and output.get('stage') != 'reading'):
            raise ResearchError('openness_reading_incomplete', 'A math reading chain ends at its first final turn that '
                                'returns the native result', {'assignment_id': assignment['id']})
        if not last:
            _require(has_deliverable_passage(records, artifacts, packet, output), PASSAGE_PROGRESS_ERR_MSG,
                     path='/passage_requests')
            record = _extend_record(records, artifacts, packet, {'output': output}, rows, observations[:index + 1],
                                    MATH_PROTOCOL)


def verify_chain(records, artifacts, assignment):
    """The turns of a final turn's reading chain, each with the units whose whole body, and the parts whose exact
    slice, its actual recorded request delivered complete; the last entry is the final turn itself. Every turn must be
    ready, the final turn's reading record must name the observed attempts of the turns before it, and every unit that
    the packet requires must have reached reading turns complete, whole or in all of its parts, or be complete in the
    final turn's own request. A math chain must also keep its binding and rebuild from its observed outputs, as
    _require_math_continuity states. A final turn that 0.64.2 to 0.66.2 built is verified by the rules of those
    releases."""
    packet = json.loads(artifacts.read(assignment['packet']))
    if packet[RECORD_KEY]['protocol'] not in GENERIC_UNIT_PROTOCOLS:
        return _verify_legacy_chain(records, artifacts, assignment)
    from .native_review import load_packet
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    rows = list_reading_rows(records, artifacts, native)
    spans = _compute_spans(artifacts, rows)
    chain = _list_chain(records, assignment)
    observations = _observe_turns(records, artifacts, chain, rows, spans)
    if packet[RECORD_KEY]['protocol'] == MATH_PROTOCOL:
        _require_math_continuity(records, artifacts, native, chain, rows, spans, observations)
    _require_observed_turns(packet[RECORD_KEY], observations[:-1])
    whole, parts = _collect_coverage(observations[:-1])
    whole.update(observations[-1]['whole'])
    missing = []
    for row in rows:
        key = review_excerpts.find_identity(row)
        if not _is_complete(key, spans[key], whole, parts):
            identity = _describe_identity(row)
            if len(spans[key]) > 1:
                identity['missing_parts'] = [part for part in range(1, len(spans[key]) + 1) if (key, part) not in parts]
            missing.append(identity)
    if missing:
        raise ResearchError('openness_reading_incomplete',
                            'Every reading unit of the packet must reach a reading turn of the chain complete, or the '
                            'final turn', {'units': missing, 'version_ids': sorted(
                                identity['version_id'] for identity in missing if identity['kind'] == 'fulltext')})
    return [observation['turn'] for observation in observations]
