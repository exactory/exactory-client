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
started keeps its recorded turns and continues under these rules. New problem chains use v5: repeated canonical
search fields travel as shared bodies with per-search residuals, and authenticated complete delivery must rebuild
each exact original search before it earns credit. Their final turns retain the manifest and receipts instead of
expanding factored searches again; independently protected bytes retain their final-input treatment. A math chain
has its own protocol. Both math and v5 problem chains rebuild each turn's record from authenticated observed outputs.
"""

import copy
import hashlib
import json
import math

from . import review_excerpts
from . import review_transport as transport
from .bar_sources import _reference, descriptor
from .errors import ResearchError, describe_error_at
from .evidence import digest
from .operations import fields, normalized_text, text

PROTOCOL_V1 = 'exactory-openness-reading-v1'
PROTOCOL_V2 = 'exactory-openness-reading-v2'
# Keep the default reading prompt protocol at v3 for callers that omit reading_protocol.
PROTOCOL = 'exactory-openness-reading-v3'
PROTOCOL_V4 = 'exactory-openness-reading-v4'
PROTOCOL_V5 = 'exactory-openness-reading-v5'
PROTOCOL_V6 = 'exactory-openness-reading-v6'
# The protocol of a math-solver result review's chain: the units of v3 in the math order, no statement record, and a
# final passage request that must deliver characters.
MATH_PROTOCOL = 'exactory-openness-reading-math-v1'
# The protocols whose units carry the identities of review_excerpts.IDENTITY_FIELDS.
GENERIC_UNIT_PROTOCOLS = (PROTOCOL, PROTOCOL_V4, PROTOCOL_V5, PROTOCOL_V6, MATH_PROTOCOL)
TURN_KEY = 'reading_turn'
RECORD_KEY = 'reading_record'
STAGES = ('reading', 'final')
NOTES_CHARACTERS = 20000
INVALID_RESPONSE = 'invalid_reading_response'
PASSAGE_PROGRESS_ERR_MSG = ('A reading response in a final turn needs a passage request that delivers characters '
                            'within the remaining passage allowance')
RESPONSE_FIELDS = ('schema_version', 'stage', 'subject_digest', 'claim_digest', 'reviewer', 'excerpts', 'notes',
                   'passage_requests')
# A part of a record or of a captured response is a fragment of its body, so it is saved as plain text.
PART_MEDIA_TYPE = 'text/plain; charset=utf-8'
BODY_WITHHELD_DELIVERY = 'body-delivered-in-reading-turns'
BODY_WITHHELD_NOTE = 'Delivered complete in reading turns {}; body withheld to fit the route input limit.'
NOTES_WITHHELD_NOTE = 'The notes of turn {} are withheld from this input so that it fits the route input limit.'
SEARCH_DELIVERY = 'search-rebuilt-from-reading-turns'
SEARCH_DELIVERY_NOTE = ('Exact original search rebuilt from authenticated reading turns; see '
                        'reading_record.search_delivery and search_receipts.')
# After the full texts that text_delivery_priority lists, the final turn withholds the complete bodies of these kinds
# in this order: the other full texts, captured search responses, reading records and search records.
WITHHOLDING_ORDER = ('fulltext', 'search_response', 'reading', 'literature_search')
V6_WITHHOLDING_ORDER = ('fulltext', 'result_text', 'search_response', 'reading', 'accepted_result', 'literature_search')
# The fields that the compact reading record leaves out of a checked excerpt or a passage answer: the source
# descriptor moves to the record's sources, the locator gives start, end and sha256, and the request gives its
# identity and id.
VERBOSE_ENTRY_KEYS = ('artifact_descriptor', 'locator', 'request', 'note', 'checked', 'reading_credit')

# Admission policy for the v6 builder. These constants do not change released protocols.
RECORD_ALLOWANCE_FLOOR = 300000
RESERVED_IDENTIFIER_CHARACTERS = 120
PROJECTION_STOP = 'review_chain_projection_over_limit'
IDENTIFIER_LIMIT_MESSAGE = 'A v6 assignment or attempt identifier has at most 120 characters.'
PACKET_OUTDATED_MESSAGE = 'Export a current problem openness packet before starting this review.'
PROJECTION_STOP_MESSAGE = 'The reading chain needs an owner decision about its input capacity before another call.'
PROBLEM_PROJECTION_DECISION = 'Whether the review waits for a reading protocol that bounds its record, or runs over a smaller packet.'
MATH_PROJECTION_DECISION = 'Whether the chain continues to its final, waits for reading-math-v2, or runs over a smaller packet.'
WAIT_PROBLEM_ANSWER = 'Wait for a bounded problem reading protocol.'
WAIT_MATH_ANSWER = 'Wait for reading-math-v2.'
SMALLER_PACKET_ANSWER = 'Run over a smaller packet.'
CONTINUE_MATH_ANSWER = 'Continue this chain to its final.'
# Stable review-context reason marker; the owner's actual instruction remains in its evidence artifact.
MATH_CONTINUATION_REASON = 'reading_chain_continue_to_final'
FORECAST_NO_BOUND_MESSAGE = 'This forecast is an estimate, not an enforceable bound on later requests.'
FINITE_PROJECTION_MESSAGE = 'The serialized record allowance bounds the character projection; its token cost is an estimate.'
CONTINUE_TO_V7_MESSAGE = 'Read the remaining units under this protocol, then take the final in a v7 chain that carries the verified turns.'
EXPORT_TO_V7_MESSAGE = 'Export now and let a v7 chain carry the verified turns and read what remains.'

# AR18.2: None means that validation and retention impose no bound. A symbolic bound is fixed by the first packet.
LEGACY_ENTRY_BOUNDS = {
    'verified_excerpt': dict(count=10000, serialized_characters='bound_unit_and_checked_excerpt'),
    'unmatched_excerpt': dict(count=None, serialized_characters=None),
    'over_allowance_excerpt': dict(count=None, serialized_characters='bound_unit_and_purpose'),
    'delivered_passage': dict(count=100000, serialized_characters=None),
    'undelivered_passage': dict(count=None, serialized_characters=None),
    'turn': dict(count='planned_reading_turns_plus_100000', serialized_characters='reserved_identifiers_and_delivery_rows'),
    'source': dict(count='plan_artifacts', serialized_characters='descriptor_and_bound_identities'),
}
READING_ENTRY_BOUNDS = {protocol: LEGACY_ENTRY_BOUNDS for protocol in
                        (PROTOCOL_V1, PROTOCOL_V2, PROTOCOL, PROTOCOL_V4, PROTOCOL_V5, MATH_PROTOCOL)}
READING_ENTRY_BOUNDS[PROTOCOL_V6] = {
    kind: dict(count='record_total', serialized_characters='record_total')
    if kind not in ('turn', 'source') else dict(bounds)
    for kind, bounds in LEGACY_ENTRY_BOUNDS.items()}
READING_ENTRY_BOUNDS[PROTOCOL_V6]['turn']['count'] = 'planned_reading_turns_plus_record_total'
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
# The prompts of a math-solver result review's chain.
MATH_READING_PROMPT = (
    'This is one reading turn of an openness review of a native mathematical result. reading_turn.statement is the '
    'exact native subject under review, a checkpoint or a closure subject. reading_turn.formulations holds the exact '
    'statement of the openness minimum and its neighbouring formulations. Each turn is a fresh input without earlier '
    'conversation. Read everything delivered now completely. Do not judge openness until the final turn.\n\n'
    'reading_turn.reading_plan lists delivered_before, delivered_now and remaining. The bodies of delivered_now are in '
    'evidence. A unit has a kind and an identity: search_response uses source_id, fulltext uses version_id, and record '
    'uses record_kind and record_id, where record_kind is literature_search or reading. A captured response and a full '
    'text also carry text_sha256, the SHA-256 of the whole stored body. This review has no statement record of a '
    'problem. The units arrive in this order. First come the captured responses of the bound searches, in the order of '
    'the searches of the minimum and then of the responses of each search. Then come the extracted full texts of the '
    'originals that the bound readings pin, by version_id and then text_sha256. Then come the bound search records, '
    'then the bound reading records. A store record arrives as its exact JSON text.\n\n'
    'One reading turn delivers at most 120,000 characters of new unit text. A larger body arrives in consecutive '
    'parts. Each plan entry gives part, parts, start, end and characters. start and end are character offsets in the '
    'whole body, and the artifact of the entry identifies the delivered whole body or slice. A unit or part delivered '
    'in this turn is not delivered in a later reading turn. The final turn carries a delivered body again only when '
    'its input has room for it.\n\n'
    'reading_turn.reading_record carries earlier notes, checked excerpts, passage answers and allowances. Its sources '
    'list gives one whole-source descriptor per cited artifact, with the identities that name it. A verified excerpt '
    'or a delivered passage gives its exact source text, the character offsets start and end, and the SHA-256 of the '
    'UTF-8 bytes of that span. An unmatched excerpt is your own unchecked copy. A passage answer identifies its '
    'requesting assignment and request ID.\n\n'
    'To fit the route input limit, a later turn can withhold the notes of the earliest turns and replace them with '
    'notes_withheld. The final turn can first withhold bodies that reading turns delivered complete, with a note that '
    'names those turns. The harness never withholds an excerpt or a passage. The native subject, the formulations and '
    'the allowances remain. Keep what the judgment needs as excerpts, and carry essential conclusions and unresolved '
    'issues from earlier notes into your latest notes.\n\n'
    'Return exactly '
    '{schema_version:1,stage:"reading",subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}. Copy '
    'subject_digest, claim_digest and reviewer exactly from reading_turn. notes is a nonempty string of at most 20,000 '
    'characters. It states what the delivered material establishes, what remains open, and how it bears on the native '
    'subject and on every formulation in reading_turn.formulations.\n\n'
    'excerpts is a list of {source_id,text,purpose}, {version_id,text,purpose} or '
    '{record_kind,record_id,text,purpose}. The first two forms can include text_sha256. Include it when one version_id '
    'names several full texts, because without it the identity is ambiguous and the excerpt stays unmatched. Do not '
    'mix identity forms. Copy definitions, normalizations, relevant results with every hypothesis, statements that a '
    'question is open or solved, and material search and record details such as queries, returned results, coverage, '
    'dispositions and reading notes. purpose says in a few words why the quotation matters. Copy text from a captured '
    'response or a record as it stands in the stored body, with the escapes inside JSON strings, not from a '
    'reconstructed field value.\n\n'
    'The harness matches against the whole stored body after it changes each character to its NFKC form, changes each '
    'run of white space to one space and removes outer white space. It uses the first match and returns the original '
    'source characters. An excerpt needs at least 20 normalized characters, and its matched original span has at most '
    '6,000 characters. A missing source, an identity that names several bodies without text_sha256, an absent match or '
    'an invalid length gives status "unmatched" with a reason. Verified excerpts use at most 200,000 original '
    'characters over the chain. An otherwise matching excerpt beyond that allowance has status "over_allowance" and no '
    'delivered text.\n\n'
    'passage_requests is a possibly empty list of {id,<the same identity fields>,anchor,characters}. IDs are unique '
    'within this response. anchor is a verbatim phrase of at least 20 normalized characters. characters is an integer '
    'from 1 to 20,000. The next turn returns the original text that starts at the first matching anchor, up to that '
    'length and the end of the body, or status "not_found". Delivered passages use at most 100,000 characters over the '
    'chain. A passage beyond the remaining allowance has status "over_allowance" and no delivered text. '
    'reading_turn.reading_record.allowances shows both totals and their usage. Excerpts and passages do not establish '
    'complete reading of a unit.')
MATH_FINAL_PROMPT = (
    ' This is the final turn of an openness reading chain over a native mathematical result. reading_record carries '
    'the preceding turns, the retained notes, the checked excerpts, the passage answers and the allowances. Judge the '
    'exact native subject, a checkpoint or a closure subject, and every formulation in '
    'native.scientific_context.openness_minimum from this record and the evidence delivered here.\n\n'
    'Reading units have three kinds. A search_response is named by source_id, a fulltext by version_id, and a record '
    'by record_kind and record_id, where record_kind is literature_search or reading. A captured response and a full '
    'text also carry text_sha256, the SHA-256 of the whole stored body. This review has no statement record of a '
    'problem. The reading turns delivered the captured responses of the bound searches first. Then came the extracted '
    'full texts of the originals that the bound readings pin. Then came the bound search records and the bound reading '
    'records, each record as its exact JSON text. The sources list gives one whole-source descriptor per cited '
    'artifact and its identities. A verified excerpt or a delivered passage contains original source characters with '
    'start, end and the SHA-256 of the span. An unmatched excerpt is an unchecked reviewer copy. An over_allowance '
    'entry delivers no text.\n\n'
    'Every unit of this review reached the reading turns complete, whole or in all of its parts. To fit the route '
    'limit, this input can withhold bodies that reading turns delivered complete and then the notes of the earliest '
    'turns. Each withheld body names the turns that delivered it. notes_withheld names the turn whose notes are '
    'absent. The harness never withholds an excerpt or a passage. The native subject, the formulations and the '
    'allowances remain. An absent body or note is not available in conversation.\n\n'
    'Return the native result review described above. If you need a bounded passage before you judge and the passage '
    'allowance has characters left, return instead exactly '
    '{schema_version:1,stage:"reading",subject_digest,claim_digest,reviewer,excerpts,notes,passage_requests}. Copy '
    'subject_digest, claim_digest and reviewer exactly from native. notes is a nonempty string of at most 20,000 '
    'characters. Carry essential earlier conclusions and unresolved issues into these latest notes.\n\n'
    'excerpts is a list of {source_id,text,purpose}, {version_id,text,purpose} or '
    '{record_kind,record_id,text,purpose}, with optional text_sha256 on the first two forms. Include text_sha256 when '
    'one version_id names several full texts. Keep material quotations as excerpts. Matching uses the NFKC form of '
    'each character, one space for each run of white space, and the first occurrence in the whole stored body. An '
    'excerpt needs at least 20 normalized characters and at most 6,000 original matched characters. The '
    'verified-excerpt allowance is 200,000 characters for the chain. An unmatched copy stays explicitly unchecked.\n\n'
    'A reading response here must contain at least one passage request {id,<the same identity '
    'fields>,anchor,characters}. IDs are unique within this response. anchor has at least 20 normalized characters. '
    'characters is an integer from 1 to 20,000. Copy an anchor in a captured response or a record from the exact '
    'stored text, with the escapes inside JSON strings. A further final turn supplies the original text from the first '
    'matching anchor. Delivered passages have a chain allowance of 100,000 characters. At least one of your passage '
    'requests must deliver characters. The harness refuses a reading response whose every request has an absent '
    'anchor, names no single stored body, or exceeds the remaining allowance. This turn is then run again. When no '
    'passage allowance remains, return the native result review. Missing decisive evidence remains a gap. An excerpt, '
    'a passage or a descriptor does not establish complete delivery of a unit.')
# The final-turn passage rule that v4 adds to the v3 final prompt (has_deliverable_passage enforces it).
FINAL_PROGRESS_RULE = (
    '\n\nAt least one of your passage requests must deliver characters: its anchor is found in a bound body, and its '
    'characters fit the remaining passage allowance. The harness refuses a reading response in which no request '
    'delivers characters, because every request misses its anchor or exceeds the remaining allowance, and the chain '
    'does not continue from it. When no passage can help you judge, return the native result review instead.')
SEARCH_READING_RULE = (
    '\n\nSearch delivery in this v5 problem chain uses reading_record.search_delivery, a manifest frozen in the first '
    'assignment. After full texts, shared canonical JSON field bodies arrive in first-use order, then residual '
    'searches in minimum order, then the other records. A search_shared_field unit identifies its field and every '
    'owning original search and formulation in owners. A search_residual unit contains the original record_id and '
    'search_digest, its remaining fields, and shared_fields mapping field names to body digests. Each component '
    'arrives whole or in consecutive parts under the same 120,000-character budget. A search completes only after '
    'authenticated reading turns deliver its residual and every part of each field it references and the exact '
    'original canonical bytes and digest rebuild. The manifest and saved artifacts supply no reading credit. '
    'Assess every formulation separately: equal judgments do not imply equal relevance to different questions. '
    'Quote shared or residual material using an owning original literature_search record_kind and record_id. '
    'Excerpts and passage answers retain that original record digest and its original canonical JSON offsets; '
    'component identities are delivery identities, not citation identities.')
SEARCH_FINAL_RULE = (
    '\n\nThis v5 final input keeps every original search binding, the frozen reading_record.search_delivery manifest '
    'and authenticated search_receipts. Factored search bodies without an independent protected role are absent, '
    'including when the route has room: '
    'search-rebuilt-from-reading-turns denotes exact original searches reconstructed from fully delivered components. '
    'Use their verified receipts, your notes, original-record excerpts and passages to judge every bound search '
    'and formulation. Other final-input delivery requirements remain in force. A manifest, descriptor, excerpt or '
    'passage alone never establishes complete search delivery.')
PROMPTS = {PROTOCOL_V1: {'reading': READING_PROMPT_V1, 'final': FINAL_PROMPT_V1},
           PROTOCOL_V2: {'reading': READING_PROMPT_V2, 'final': FINAL_PROMPT_V2},
           PROTOCOL: {'reading': READING_PROMPT, 'final': FINAL_PROMPT},
           PROTOCOL_V4: {'reading': READING_PROMPT, 'final': FINAL_PROMPT + FINAL_PROGRESS_RULE},
           PROTOCOL_V5: {'reading': READING_PROMPT + SEARCH_READING_RULE,
                         'final': FINAL_PROMPT + FINAL_PROGRESS_RULE + SEARCH_FINAL_RULE},
           MATH_PROTOCOL: {'reading': MATH_READING_PROMPT, 'final': MATH_FINAL_PROMPT}}


V6_READING_PARAGRAPH = (' [X2: v6 family readings, accepted results, and supplied origin.]')
V6_FINAL_PARAGRAPH = (' [X2: v6 delivered family readings, accepted results, and supplied origin.]')
# Each recorded search key has one class and one writer-pass placeholder. Both v6 prompts use these lines.
ROW_DISPOSITION_GUIDE_LINE = ('A row disposition is the author\'s statement about a row that the harness could '
    'not import; the row beside the reason is what the provider returned. A reading named in the reason is '
    'bound as a supporting reading and is a unit of the chain that requires complete delivery.')
SEARCH_FIELD_GUIDE = {
    'id': {'class': 'identity', 'line': '[X2 R42: identity; the search record identifier.]'},
    'profile': {'class': 'identity', 'line': '[X2 R42: identity; the literature profile.]'},
    'purpose': {'class': 'identity', 'line': '[X2 R42: identity; the purpose of the search.]'},
    'question': {'class': 'scope_binding', 'line': '[X2 R42: scope_binding; the question searched.]'},
    'scope': {'class': 'scope_binding', 'line': '[X2 R42: scope_binding; the declared bounds of this search.]'},
    'queries': {'class': 'capture', 'line': '[X2 R42: capture; the recorded query texts.]'},
    'responses': {'class': 'capture', 'line': '[X2 R42: capture; the captured responses and their query bindings.]'},
    'captured_at': {'class': 'capture', 'line': '[X2 R42: capture; the date of the recorded search.]'},
    'found_work_ids': {'class': 'capture', 'line': '[X2 R42: capture; the imported works found in the responses.]'},
    'cited_work_ids': {'class': 'author_judgment', 'line': '[X2 R42: author_judgment; works cited by the author.]'},
    'verdict': {'class': 'author_judgment', 'line': '[X2 R42: author_judgment; the author verdict is no coverage finding.]'},
    'impact': {'class': 'author_judgment', 'line': '[X2 R42: author_judgment; the author impact assessment is no coverage finding.]'},
    'dispositions': {'class': 'coverage', 'line': '[X2 R42: coverage; the relevance judgment and reason for each found work.]'},
    'row_dispositions': {'class': 'coverage', 'line': ROW_DISPOSITION_GUIDE_LINE},
    'resolved': {'class': 'coverage', 'line': '[X2 R42: coverage; explicit resolutions of earlier work judgments.]'},
    'gaps': {'class': 'coverage', 'line': '[X2 R42: coverage; the declared remaining gaps.]'},
    'coverage': {'class': 'coverage', 'line': '[X2 R42: coverage; per-query complete, top_k, bounded_unordered or unspecified coverage.]'},
    'pending': {'class': 'coverage', 'line': '[X2 R42: coverage; pending capture, parsing or enumeration issues.]'},
    'integrity_errors': {'class': 'coverage', 'line': '[X2 R42: coverage; errors in the captured search evidence.]'},
    'page_groups': {'class': 'capture', 'line': '[X2 R42: capture; pagination, enumeration and excluded rows of captured responses.]'},
    'hits_per_query': {'class': 'author_bound', 'line': '[X2 R42: author_bound; the author-chosen bound is no coverage finding.]'},
    'depth': {'class': 'author_bound', 'line': '[X2 R42: author_bound; the requested search depth overrides.]'},
    'binding': {'class': 'scope_binding', 'line': '[X2 R42: scope_binding; an unchanged provisional search rebound to a literature scope.]'},
    'scope_digest': {'class': 'scope_binding', 'line': '[X2 R42: scope_binding; null when recorded without a literature scope; no coverage finding.]'},
    'provisional': {'class': 'scope_binding', 'line': '[X2 R42: scope_binding; no literature scope existed, so the search was recorded by question; says nothing about coverage or outstanding readings.]'},
    'evidence_digest': {'class': 'scope_binding', 'line': '[X2 R42: scope_binding; the evidence digest at scope binding.]'},
    'frontier_digest': {'class': 'scope_binding', 'line': '[X2 R42: scope_binding; the frontier digest at scope binding.]'},
    'native_parse_version': {'class': 'capture', 'line': '[X2 R42: capture; the parser version used for native responses.]'},
}
SEARCH_FIELD_GUIDE_TEXT = '\n\nSearch record field guide:\n' + '\n'.join(
    key + ': ' + row['line'] for key, row in SEARCH_FIELD_GUIDE.items()) + '\n\n'
SUPPORTING_READING_SENTENCE = (' [X2 R20: A supporting reading is delivered as its reading record, with role '
    '"supporting" and citing searches in cited_by; a work whose full text the openness judgment needs must be '
    'judged relevant so that its reading is decisive and its text is a unit.]')
SEARCH_COMPLETION_SENTENCE = (' For completes, the harness checked matching profile and question, capture time '
    'not earlier than the completed search, retained query identities, clean coverage at least as strong, complete '
    'or prefix coverage where earlier pages do not join, every returned row and found work, reasons for changed '
    'material judgments and a closure statement for every earlier gap; completed searches omitted from this packet '
    'contribute no gaps to this review, and closed_gaps is the author\'s statement of how the gaps were closed.')
SUPPLIED_ORIGIN_SENTENCE = (' A full-text unit with origin contains a text saved by a person. The harness did not '
    'fetch it or verify its reported origin. Its full reading counts; state when a decisive judgment rests on it.')
RESULT_READING_RULE = (
    ' An accepted_result record identifies the solver objective, accepted checkpoint, linked review and evidence '
    'closure cited by the searches. Read its manifests and UTF-8 artifacts in result_text units; PDF artifacts '
    'arrive as extracted text with the PDF and text digests. Both kinds of unit may arrive in consecutive parts. '
    'A described proof or theorem artifact is a result_undelivered gap; other described binary parts, executables '
    'and toolchains carry notices and are not scientific text. A certificate or lean manifest includes the '
    'verification record. Acceptance in that objective establishes no openness finding for this statement.')
RESULT_FINAL_RULE = (
    ' Judge each accepted result from its accepted_result record and delivered result_text units, including '
    'their verified parts and the retained reading notes. Described proof or theorem artifacts remain gaps; '
    'other described binary parts, executables and toolchains are notices. The solver acceptance carries no '
    'finding about the openness of the statement under review.')
RECORD_ALLOWANCE_SENTENCE = (' The third allowance, record, counts serialized characters of every retained excerpt, '
    'passage answer and extra final reading turn, including fields and escapes.')
RECORD_PASSAGE_LIMIT_MESSAGE = 'The record allowance cannot retain another final reading turn and a delivered passage; return the finding.'
RECORD_NOT_RETAINED_SENTENCE = (' An entry beyond the remaining record total is counted by kind in '
    'allowances.record.not_retained and is absent from later inputs; it uses no other allowance.')
PROMPTS[PROTOCOL_V6] = {
    'reading': READING_PROMPT + SEARCH_READING_RULE + V6_READING_PARAGRAPH + SUPPORTING_READING_SENTENCE
               + SEARCH_FIELD_GUIDE_TEXT + SEARCH_COMPLETION_SENTENCE + SUPPLIED_ORIGIN_SENTENCE + RESULT_READING_RULE
               + RECORD_ALLOWANCE_SENTENCE + RECORD_NOT_RETAINED_SENTENCE,
    'final': (FINAL_PROMPT + FINAL_PROGRESS_RULE + SEARCH_FINAL_RULE).replace(
        'native result review described above', 'finding described above').replace(
        'native result review', 'finding described above') + V6_FINAL_PARAGRAPH + SUPPORTING_READING_SENTENCE
        + SEARCH_FIELD_GUIDE_TEXT + SEARCH_COMPLETION_SENTENCE + SUPPLIED_ORIGIN_SENTENCE + RESULT_FINAL_RULE,
}


def is_openness_packet(native):
    """Whether a native packet is a problem openness packet exported by `export --kind problem-openness`."""
    return (native['contract'].get('orchestration') == 'problem_creation'
            and 'openness_minimum' in native['scientific_context'])


def is_math_openness_packet(native):
    """Whether a native packet is a math-solver result review that binds an openness minimum: a result outside problem
    creation, for a checkpoint or for the objective's closure."""
    return (native['kind'] == 'result' and 'openness_minimum' in native['scientific_context']
            and native['contract'].get('orchestration') != 'problem_creation')


def find_chain_protocol(native, prior_protocol=None):
    """The reading protocol of a new turn of a chain over a native packet."""
    if is_math_openness_packet(native):
        return MATH_PROTOCOL
    if prior_protocol is not None:
        return prior_protocol if prior_protocol in (PROTOCOL_V5, PROTOCOL_V6) else PROTOCOL_V4
    return PROTOCOL_V6 if native['scientific_context']['openness_minimum'].get('schema_version') == 2 else PROTOCOL_V5


def require_reserved_identifier(identifier, protocol):
    """Refuse an identifier that exceeds the length reserved by a finite v6 projection."""
    if protocol == PROTOCOL_V6 and len(identifier) > RESERVED_IDENTIFIER_CHARACTERS:
        raise ResearchError('invalid_input', IDENTIFIER_LIMIT_MESSAGE,
                            {'characters': len(identifier), 'maximum_characters': RESERVED_IDENTIFIER_CHARACTERS})


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


def list_reading_texts(records, native, *, artifacts=None, review=None):
    """The full-text rows of an openness packet: for each version that a bound reading read, the extracted texts of
    its bound main captures carried as evidence, so that a capture recorded after the export does not enlarge the
    chain. An alternate main body that the packet's openness_delivery contract omits is not read; the
    selected representation of its version is. A row is {kind, version_id, text_sha256, artifact}; the rows follow
    the packet's text_delivery_priority, then the version id and the text digest."""
    from .graph import main_captures
    from .capture_selection import bind_packet_selections
    records = bind_packet_selections(records, native['scientific_context'], artifacts=artifacts, review=review)
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
                source = records['source'][capture['source_id']]
                if 'supplied' in source:
                    rows[version, reference['sha256']]['origin'] = source['supplied']
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


def list_math_text_rows(records, native, *, artifacts=None, review=None):
    """The full-text rows of a math result openness packet: for each bound reading whose source bundle pins an original,
    the extracted text of that original's bound main capture, by version id and then text digest. The packet must
    carry each text, so that a capture recorded after the export never enlarges the chain. A row is {kind, version_id,
    text_sha256, artifact}."""
    from .graph import main_captures
    from .capture_selection import bind_packet_selections
    records = bind_packet_selections(records, native['scientific_context'], artifacts=artifacts, review=review)
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
        source = records['source'][bundle['source_id']]
        if 'supplied' in source:
            rows[reading['version_id'], texts[0]['sha256']]['origin'] = source['supplied']
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
    if minimum['schema_version'] == 2:
        bound += [('reading', sha256) for sha256 in minimum['supporting_reading_digests']]
        bound += [('accepted_result', row['result_digest']) for row in minimum['results']]
    result_ids = {row['result_digest']: row['native_attempt_digest'] + ':' + row['acceptance_id']
                  for row in minimum.get('results', [])}
    if is_openness_packet(native):
        bound.append(('problem_statement', native['subject_digest']))
    searches = []
    rows = {}
    for record_kind, sha256 in bound:
        reference = carried.get(sha256)
        if reference is None:
            raise ResearchError('openness_reading_incomplete', 'The openness packet does not carry a record that it '
                                'binds', {'record_kind': record_kind, 'sha256': sha256})
        record = None if record_kind == 'accepted_result' else json.loads(artifacts.read(reference))
        record_id = result_ids[sha256] if record is None else record['id']
        if record_kind == 'literature_search' and minimum.get('supporting_reading_digests'):
            searches.append(record)
        row = {'kind': 'record', 'record_kind': record_kind, 'record_id': record_id, 'artifact': reference}
        if minimum['schema_version'] == 2 and record_kind == 'reading':
            row['role'] = 'decisive' if sha256 in minimum['reading_digests'] else 'supporting'
            if row['role'] == 'supporting':
                from .problem_openness import list_reading_citations
                row['cited_by'] = list_reading_citations(record_id, searches)
        rows.setdefault((record_kind, record_id), row)
    rows = list(rows.values())
    return ([row for row in rows if row['record_kind'] == 'literature_search']
            + sorted((row for row in rows if row['record_kind'] == 'reading'), key=lambda row: row['record_id'])
            + [row for row in rows if row['record_kind'] == 'accepted_result']
            + [row for row in rows if row['record_kind'] == 'problem_statement'])


def list_result_texts(artifacts, native):
    """Plan the text parts of accepted-result descriptors bound by the v2 minimum."""
    carried = {entry['digest']: entry['artifact'] for entry in native['evidence']}
    rows = []
    for result in native['scientific_context']['openness_minimum'].get('results', []):
        body = json.loads(artifacts.read(carried[result['result_digest']]))
        texts = [row['sha256'] for row in body.get('manifests', [])]
        texts += [row.get('text_sha256', row['sha256']) for row in body['artifacts']
                  if row['delivery'] in ('text', 'pdf_text')]
        for sha256 in dict.fromkeys(texts):
            rows.append(dict(kind='result_text', native_attempt_digest=result['native_attempt_digest'],
                acceptance_id=result['acceptance_id'], text_sha256=sha256, artifact=carried[sha256]))
    return rows


def list_reading_rows(records, artifacts, native, *, review=None):
    """The whole reading units that an openness packet requires, in the order of delivery: for a problem its full texts,
    then its records; for a math result its captured responses, its pinned full texts, then its records."""
    if is_math_openness_packet(native):
        return (list_math_response_rows(records, artifacts, native) + list_math_text_rows(
                    records, native, artifacts=artifacts, review=review)
                + list_record_rows(artifacts, native))
    return (list_reading_texts(records, native, artifacts=artifacts, review=review)
            + list_result_texts(artifacts, native) + list_record_rows(artifacts, native))


def _plan_search_delivery(artifacts, native):
    """Plan only the exact search artifacts bound by this immutable native minimum, without writes."""
    from .review_search_delivery import plan_search_delivery
    carried = {entry['artifact']['sha256']: entry['artifact'] for entry in native['evidence']}
    return plan_search_delivery([dict(binding, search=json.loads(artifacts.read(carried[binding['search_digest']])))
                                 for binding in native['scientific_context']['openness_minimum']['searches']])


def list_delivery_rows(rows, manifest):
    """The physical v5 units: full texts, shared fields, residual searches, then other logical records."""
    shared = [{'kind': 'record', 'record_kind': 'search_shared_field',
               'record_id': entry['field'] + ':' + entry['artifact']['sha256'],
               'field': entry['field'], 'owners': entry['owners'], 'artifact': entry['artifact']}
              for entry in manifest['shared_fields']]
    searches = [{'kind': 'record', 'record_kind': 'search_residual' if entry['residual'] else 'literature_search',
                 'record_id': entry['record_id'], 'search_digest': entry['search_digest'],
                 'artifact': entry['residual'] or entry['artifact']}
                for entry in manifest['searches']]
    return ([row for row in rows if row['kind'] == 'fulltext']
            + [row for row in rows if row['kind'] == 'result_text'] + shared + searches
            + [row for row in rows if row['kind'] not in ('fulltext', 'result_text')
               and row.get('record_kind') not in ('literature_search', 'problem_statement')]
            + [row for row in rows if row.get('record_kind') == 'problem_statement'])


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
            units.append(dict({key: value for key, value in row.items() if key != 'artifact'},
                              part=part, parts=len(spans), start=start, end=end,
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
    from .review_route_capacity import measure_request
    return measure_request(route, request)['characters']


def _measure_serialized_entries(route, entries):
    from .review_route_capacity import measure_request
    filled = measure_request(route, _render(route, '', {'entries': entries}))
    empty = measure_request(route, _render(route, '', {'entries': []}))
    return {key: filled[key] - empty[key] for key in ('characters', 'utf8_bytes')}


def measure_record_entries(route, entries):
    """Characters added by a list in the actual request representation, including separators and escapes.

    V6 retention must charge this delta, rather than len(text) or len(json.dumps(entry)). Local transports serialize
    the packet inside a message, so even a JSON quote has a second escape in their native input.
    """
    return _measure_serialized_entries(route, entries)['characters']


def classify_retained_entry(entry, *, passage=False):
    if passage:
        return 'delivered_passage' if entry.get('status') == 'delivered' else 'undelivered_passage'
    return {'verified': 'verified_excerpt', 'over_allowance': 'over_allowance_excerpt'}.get(
        entry.get('status'), 'unmatched_excerpt')


def describe_reading_entry_bounds(record):
    """Keep the older retention protocols' uncapped purposes distinct from the AR18.2 table of excerpts v3."""
    bounds = copy.deepcopy(READING_ENTRY_BOUNDS[record['protocol']])
    if (record['protocol'] != PROTOCOL_V6
            and record.get('excerpt_protocol', review_excerpts.PROTOCOL) != review_excerpts.SESSION_PROTOCOL):
        for kind in ('verified_excerpt', 'over_allowance_excerpt'):
            bounds[kind]['serialized_characters'] = None
    if record['protocol'] in (PROTOCOL_V1, PROTOCOL_V2, PROTOCOL):
        bounds['turn']['count'] = None
    return bounds


def measure_retained_entries(route, record):
    """Inventory every retained status, including the diagnostics that the text allowances do not charge."""
    groups = {kind: [] for kind in LEGACY_ENTRY_BOUNDS}
    for entry in record['excerpts']:
        groups[classify_retained_entry(entry)].append(entry)
    for entry in record['passages']:
        groups[classify_retained_entry(entry, passage=True)].append(entry)
    groups['turn'] = record['turns']
    groups['source'] = record.get('sources', [])
    result = {kind: dict(count=len(entries), characters=measure_record_entries(route, entries))
              for kind, entries in groups.items()}
    # Grouping statuses loses the separator between groups. Assign it to the later group's first entry.
    for entries, kinds, passage in ((record['excerpts'], ('verified_excerpt', 'unmatched_excerpt', 'over_allowance_excerpt'), False),
                                    (record['passages'], ('delivered_passage', 'undelivered_passage'), True)):
        if not entries:
            continue
        first_kind = classify_retained_entry(entries[0], passage=passage)
        for kind in kinds:
            if result[kind]['count'] and kind != first_kind:
                result[kind]['characters'] += 2
    return result


def describe_reading_units(artifacts, rows):
    """Group physical plan bodies by kind and body characters, largest first, as the reading budget counts them."""
    groups = {}
    for row in rows:
        kind = row.get('record_kind', row['kind'])
        if kind == 'search_shared_field':
            kind = 'shared_search_body'
        group = groups.setdefault(kind, dict(kind=kind, count=0, characters=0))
        group['count'] += 1
        group['characters'] += len(artifacts.read(row['artifact']).decode('utf-8'))
    return sorted(groups.values(), key=lambda group: (-group['characters'], group['kind']))


def _build_projection_record(artifacts, rows, record, units, record_total, *, source_rows):
    """Measure a maximal fixed record: one planned turn per unit, reserved ids, all sources and search receipts.

    Grouping units can only remove turn envelopes. Additional final passage turns belong to the record allowance.
    A control character reserves the largest JSON escape permitted by a later identifier's character limit.
    """
    identifier = '\x00' * RESERVED_IDENTIFIER_CHARACTERS
    projected = copy.deepcopy(record)
    projected.update(turns=[], sources=[], excerpts=[], passages=[])
    if record['protocol'] == PROTOCOL_V6:
        from .storage import _MAX_STORED_INTEGER
        projected['projection_characters'] = _MAX_STORED_INTEGER
    for allowance in projected['allowances'].values():
        allowance['used'] = allowance['characters']
    from .storage import _MAX_STORED_INTEGER
    projected['allowances']['record'] = dict(characters=record_total, used=record_total,
        not_retained={kind: _MAX_STORED_INTEGER for kind in LEGACY_ENTRY_BOUNDS if kind != 'source'})
    deliveries = {}
    for row in source_rows:
        identity = _describe_identity(row)
        _add_source(projected['sources'], descriptor(row['artifact']), identity)
        if 'text_sha256' in identity:
            _add_source(projected['sources'], descriptor(row['artifact']),
                        {key: value for key, value in identity.items() if key != 'text_sha256'})
    rows_by_identity = {}
    for row in rows:
        rows_by_identity.setdefault(review_excerpts.find_identity(row), []).append(row)
    for unit in units:
        reference = unit['artifact_descriptor']
        whole = [_describe_identity(unit)] if unit['parts'] == 1 else []
        turn = dict(assignment_id=identifier, attempt_id=identifier, delivered_units=whole,
                    notes_withheld=NOTES_WITHHELD_NOTE.format(identifier))
        if record['protocol'] == PROTOCOL_V6:
            turn['stage'] = 'reading'
        if unit['parts'] > 1:
            turn['delivered_parts'] = [dict(_describe_identity(unit), **{key: unit[key] for key in
                ('part', 'parts', 'start', 'end', 'characters')})]
        projected['turns'].append(turn)
        for row in rows_by_identity.get(review_excerpts.find_identity(unit), []):
            body = deliveries.setdefault(row['artifact']['sha256'], dict(
                content=artifacts.read(row['artifact']), deliveries=[]))
            body['deliveries'].append(dict(assignment_id=identifier, attempt_id=identifier,
                part=unit['part'], parts=unit['parts'], start=unit['start'], end=unit['end'],
                sha256=reference['sha256']))
    if 'search_delivery' in record:
        from .review_search_delivery import verify_search_deliveries
        manifest = record['search_delivery']
        receipts = verify_search_deliveries(manifest, deliveries, {
            entry['search_digest']: artifacts.read(entry['artifact']) for entry in manifest['searches']})
        projected['search_receipts'] = receipts
        # Reserve one envelope per completed search. Grouped completions remove envelopes.
        for turn, receipt in zip(projected['turns'], receipts):
            turn['completed_searches'] = [copy.deepcopy(receipt)]
        if len(receipts) > len(projected['turns']) and projected['turns']:
            projected['turns'][-1]['completed_searches'].extend(copy.deepcopy(receipts[len(projected['turns']):]))
    return projected


def _measure_largest_reading_request(route, packet, record, units, artifacts, prompt):
    """Bound every prefix with the largest plan frame plus the largest distinct delivered evidence window.

    A delivered_now reference is shorter than the descriptor used before or after it. Keeping every descriptor in
    remaining therefore bounds every partition of the plan. Evidence deduplicates artifact references, so a longer
    prefix can have the same bodies and a smaller plan than a shorter prefix; measuring just that prefix is unsafe.
    """
    from .review_protocol import _evidence
    from .review_route_capacity import measure_request
    frame = _assemble_reading_turn(artifacts, packet, record, [], [], units, len(units) + 1)
    entries, costs, keys = {}, {}, []
    for unit in units:
        reference = _reference(unit['artifact_descriptor'])
        key = (reference['sha256'], digest(reference))
        keys.append(key)
        if key not in entries:
            entries[key] = _evidence(artifacts, [reference])[0]
            costs[key] = _measure_serialized_entries(route, [entries[key]])
    # Nonnegative singleton costs let the largest raw-character window dominate all of its shorter prefixes.
    start, raw_characters, counts = 0, 0, {}
    totals = dict(characters=0, utf8_bytes=0)
    largest, windows = dict(characters=-1, utf8_bytes=-1), {}
    for end, unit in enumerate(units):
        raw_characters += unit['characters']
        key = keys[end]
        counts[key] = counts.get(key, 0) + 1
        if counts[key] == 1:
            for measure in totals:
                totals[measure] += costs[key][measure]
        while start < end and raw_characters > review_excerpts.TURN_FULLTEXT_CHARACTERS:
            raw_characters -= units[start]['characters']
            old = keys[start]
            counts[old] -= 1
            if not counts[old]:
                del counts[old]
                for measure in totals:
                    totals[measure] -= costs[old][measure]
            start += 1
        for measure in totals:
            size = totals[measure] + 2 * max(0, len(counts) - 1)
            if size > largest[measure]:
                largest[measure], windows[measure] = size, (start, end + 1)
    measures = []
    for start, end in sorted(set(windows.values())):
        frame['evidence'] = [entries[key] for key in sorted(set(keys[start:end]))]
        measures.append(measure_request(route, _render(route, prompt, frame)))
    return measures


def project_reading_chain(route, packet, rows, record, *, artifacts, record_total=None, prompts=None,
                          eligible_texts=(), final_input=None):
    """Bound every mandatory request of a chain, using the v6 serialized-record allowance.

    The caller supplies the frozen packet, including v6's minimum_gaps and audit_forecast, and its actual prompts.
    Until UB6 registers those prompts, v6 measurements use the v5 renderings. The v6 builder must fit optional bodies
    and notes within the returned projection; they earn no extra allowance. No released packet is rewritten here.
    With no record_total, allocate the largest total the route admits; later calls pass the frozen total explicitly.
    """
    protocol = record['protocol']
    delivery_rows = list_delivery_rows(rows, record['search_delivery']) if 'search_delivery' in record else rows
    bounds = describe_reading_entry_bounds(record)
    unbounded = [kind for kind, bound in bounds.items()
                 if bound['count'] is None or bound['serialized_characters'] is None]
    result = dict(protocol=protocol, finite=not unbounded, entry_bounds=bounds, unbounded_kinds=unbounded,
                  units=describe_reading_units(artifacts, delivery_rows), characters=None, fixed_parts={},
                  record_total=record_total, record_floor=RECORD_ALLOWANCE_FLOOR,
                  reserved_identifier_characters=RESERVED_IDENTIFIER_CHARACTERS)
    if unbounded:
        measured = final_input if final_input is not None else measure_final_input(
            route, packet, rows, record, eligible_texts=eligible_texts)
        result['fixed_parts'] = dict(measured['parts'])
        delivery_rows = list_delivery_rows(rows, record['search_delivery']) if 'search_delivery' in record else rows
        result['planned_reading_turns'] = sum(len(value) for value in _compute_spans(artifacts, delivery_rows).values())
        return result
    choose_total = record_total is None
    if choose_total:
        record_total = RECORD_ALLOWANCE_FLOOR
    from .review_protocol import canonical_prompt
    prompt_protocol = protocol if protocol in PROMPTS else PROTOCOL_V5
    prompts = prompts or {stage: canonical_prompt('native_math', reading_stage=stage,
        reading_protocol=prompt_protocol)['text'] for stage in STAGES}
    delivery_rows = list_delivery_rows(rows, record['search_delivery']) if 'search_delivery' in record else rows
    units = list_reading_units(artifacts, delivery_rows)
    projected = _build_projection_record(artifacts, delivery_rows, record, units,
        max(record_total, transport.local_input_limit(route) or record_total), source_rows=rows)
    final = copy.deepcopy(packet)
    final[RECORD_KEY] = projected
    factored = set()
    if 'search_delivery' in record:
        factored = _project_search_delivery(final, projected)
    optional = {row['artifact']['sha256'] for row in _list_withholding_candidates(
        final, rows, eligible_texts)} - factored
    identifier = '\x00' * RESERVED_IDENTIFIER_CHARACTERS
    identity_parts = {}
    for unit in units:
        identity = review_excerpts.find_identity(unit)
        identity_parts[identity] = identity_parts.get(identity, 0) + 1
    body_parts = {row['artifact']['sha256']: identity_parts.get(review_excerpts.find_identity(row), 0)
                  for row in rows}
    for entry in final['evidence']:
        if entry['artifact']['sha256'] in optional:
            entry.update(content='', delivery=BODY_WITHHELD_DELIVERY,
                note=BODY_WITHHELD_NOTE.format(', '.join([identifier] * max(
                    1, body_parts[entry['artifact']['sha256']]))))
    fixed_final = _measure(route, _render(route, prompts['final'], final))
    from .review_route_capacity import measure_request
    measurements = dict(final=measure_request(route, _render(route, prompts['final'], final)))
    reading_measures = _measure_largest_reading_request(route, packet, projected, units, artifacts,
                                                       prompts['reading'])
    fixed = dict(final=fixed_final, reading=max((measure['characters'] for measure in reading_measures), default=0))
    from .review_route_capacity import describe_route_capacity
    capacity = describe_route_capacity(route)
    ratio = capacity['input_text_characters']['characters_per_token'] or 1
    token_parts = dict(final=measurements['final']['tokens_upper'],
                       reading=max((measure['tokens_upper'] for measure in reading_measures), default=0))
    if any(measure['measure'] == 'bytes' for measure in [measurements['final']] + reading_measures):
        ratio = 1
    if choose_total:
        character_room = (capacity['input_characters']['value'] or max(fixed.values())) - max(fixed.values())
        token_room = math.floor((capacity['input_tokens']['value'] - max(token_parts.values())) * ratio)
        record_total = max(0, min(character_room, token_room))
    result.update(record_total=record_total, fixed_parts=fixed, fixed_tokens=token_parts,
                  fixed_record_parts={key: measure_record_entries(route, projected.get(key, []))
                                      for key in ('sources', 'turns', 'search_receipts')},
                  characters=None if unbounded else max(fixed.values()) + record_total,
                  tokens_upper=None if unbounded else max(token_parts.values()) + math.ceil(record_total / ratio),
                  token_estimate=True,
                  planned_reading_turns=len(units), prompt_protocol=prompt_protocol)
    return result


def describe_projection_decision(protocol, *, records=None, artifacts=None, assignment=None):
    """The owner's answers use D2's payload rules; an admission need not have a stored head yet."""
    from .review_plan_steps import PLAN_STEPS, describe_step, _build_identity_payload
    math_chain = protocol == MATH_PROTOCOL
    number = 5 if math_chain else 4
    if assignment is not None and records is not None and (number == 4 or assignment.get('packet')):
        export = describe_step(records, artifacts, assignment, number)
    else:
        row = PLAN_STEPS[number]
        export = dict(id=number, command=row['command'], payload={} if math_chain else dict(kind='problem-openness'),
                      chooses=list(row['chooses']),
                      prerequisites=[dict(condition=value) for value in row['prerequisites']])
        payload = _build_identity_payload(assignment) if assignment is not None else dict(role='native_math', dossier_id=None)
        chooses = ['id', 'route_id', 'reviewer_id', 'context.native_packet']
        if assignment is None:
            chooses.append('author_id')
        export['then'] = [dict(command='review-assignment', payload=payload, chooses=chooses)]
    wait = copy.deepcopy(export)
    wait['prerequisites'].insert(0, dict(condition='The installed release holds ' + (
        'reading-math-v2 for this packet.' if math_chain else
        'exactory-openness-reading-v6 or exactory-openness-reading-v7 for this packet.')))
    smaller = copy.deepcopy(export)
    smaller['prerequisites'].insert(0, dict(condition='Bind less material in a new packet.'))
    assignment_step = smaller['then'][0]
    smaller['then'] = [dict(assignment_step, check=True), assignment_step]
    answers = [dict(answer=WAIT_MATH_ANSWER if math_chain else WAIT_PROBLEM_ANSWER, steps=[wait]),
               dict(answer=SMALLER_PACKET_ANSWER, steps=[smaller])]
    if math_chain and assignment is not None and assignment.get('packet'):
        step = describe_step(records, artifacts, assignment, 9)
        step['payload']['reason'] = MATH_CONTINUATION_REASON
        step['chooses'] = [key for key in step['chooses'] if key != 'reason']
        answers.insert(0, dict(answer=CONTINUE_MATH_ANSWER, steps=[step]))
    return dict(owner_decides=MATH_PROJECTION_DECISION if math_chain else PROBLEM_PROJECTION_DECISION, then=answers)


def admit_reading_chain(route, projection, *, records=None, artifacts=None, assignment=None):
    """The first-assignment admission, deliberately separate from the v5 first-assignment call site."""
    from .review_route_capacity import describe_route_capacity
    capacity = describe_route_capacity(route)
    limit = dict(characters=capacity['input_characters']['value'], tokens=capacity['input_tokens']['value'])
    reason = 'no_enforceable_bound' if not projection['finite'] else 'below_floor'
    if (projection['finite'] and projection['record_total'] >= RECORD_ALLOWANCE_FLOOR
            and (limit['characters'] is None or projection['characters'] <= limit['characters'])
            and projection['tokens_upper'] <= limit['tokens']):
        return dict(state='record', projection=projection, limit=limit)
    return dict(state='stop', code=PROJECTION_STOP, message=PROJECTION_STOP_MESSAGE, stop_class='decision',
        projection=projection, details=dict(reason=reason, projection=projection, limit=limit,
            unbounded_kinds=projection['unbounded_kinds'], units=projection['units'],
            decision=describe_projection_decision(projection['protocol'], records=records,
                                                  artifacts=artifacts, assignment=assignment)))


def _raise_projection_stop(record, details, *, records=None, artifacts=None, assignment=None):
    """Keep the exact refusal evidence and D2's chain-specific owner answers together."""
    details = dict(details)
    protocol = record['protocol']
    details.setdefault('reason', 'no_enforceable_bound')
    details.setdefault('projection', dict(finite=False, parts=details.get('parts', {}), characters=None))
    details.setdefault('limit', details.get('input_limit_characters'))
    details['unbounded_kinds'] = [kind for kind, bound in describe_reading_entry_bounds(record).items()
                                if bound['count'] is None or bound['serialized_characters'] is None]
    details['decision'] = describe_projection_decision(protocol, records=records, artifacts=artifacts,
                                                      assignment=assignment)
    raise ResearchError(PROJECTION_STOP, PROJECTION_STOP_MESSAGE, details)


def _find_delivery_turns(unit, turns):
    """Find prospective delivery metadata, including the version-only rows of the earliest stored protocols."""
    identity = dict(review_excerpts.find_identity(unit)[1:])
    result = []
    for index, turn in enumerate(turns):
        whole = any(all(identity.get(key) == value for key, value in review_excerpts.find_identity(row)[1:])
                    for row in turn.get('delivered_units', []))
        legacy = unit['kind'] == 'fulltext' and unit['version_id'] in turn.get('delivered', [])
        part = any(('part' not in unit or row['part'] == unit['part']) and all(identity.get(key) == value
                   for key, value in review_excerpts.find_identity(row)[1:])
                   for row in turn.get('delivered_parts', []))
        if whole or legacy or part:
            result.append(index)
    return result


def measure_final_input(route, packet, rows, record, *, eligible_texts=(), reading_turn_ids=None):
    """Build the prospective final as the final builder does, retaining every record member.

    All plan bodies may eventually have delivery credit. This diagnostic grants none: it only measures their
    future withholding. When even maximal withholding cannot fit, _fit_turn returns that exact request here.
    """
    from .review_protocol import canonical_prompt
    candidate = copy.deepcopy(packet)
    candidate[RECORD_KEY] = copy.deepcopy(record)
    factored = set()
    if record['protocol'] in (PROTOCOL_V5, PROTOCOL_V6):
        factored = _project_search_delivery(candidate, record)
    priority = packet['native']['scientific_context'].get('text_delivery_priority', [])
    lengths = {entry['artifact']['sha256']: len(entry['content']) for entry in packet['evidence']
               if entry.get('encoding') == 'utf-8'}
    ranked = []
    for row in _list_withholding_candidates(candidate, rows, eligible_texts):
        sha256 = row['artifact']['sha256']
        if sha256 in factored:
            continue
        identifiers = [record['turns'][index]['assignment_id'] for index in _find_delivery_turns(row, record['turns'])
                       if reading_turn_ids is None or record['turns'][index]['assignment_id'] in reading_turn_ids]
        rank = ((0, -priority.index(sha256)) if row['kind'] == 'fulltext' and sha256 in priority else
                (1 + (V6_WITHHOLDING_ORDER if record['protocol'] == PROTOCOL_V6 else WITHHOLDING_ORDER).index(
                     row.get('record_kind', row['kind'])),
                 -lengths.get(sha256, row['artifact']['size'])))
        ranked.append((rank, review_excerpts.find_identity(row), sha256, identifiers or ['']))
    bodies = [(sha256, identifiers) for _, _, sha256, identifiers in sorted(ranked)]
    prompt = canonical_prompt('native_math', reading_stage='final', reading_protocol=record['protocol'])['text']
    candidate, request = _fit_turn(route, prompt, candidate, bodies, refuse=False)
    report = _measure_parts(route, prompt, candidate)
    from .review_route_capacity import measure_request
    report.update(measurement_kind='built_final_request', status='measured', phase='final_forecast',
                  request=measure_request(route, request))
    report['headroom_upper_bound_characters'] = report['input_limit_characters'] - report['input_characters']
    supplied = review_excerpts.read_sent_packet(request)
    text_value = transport._local_adapter(route).packet_text(request)
    turn_ids = {turn['assignment_id'] for turn in record['turns']}
    attempt_ids = {turn['attempt_id'] for turn in record['turns']}
    def count_identifiers(values):
        nonlocal text_value
        characters = 0
        for value in sorted(values, key=lambda value: (-len(value), value)):
            if not value:
                continue
            escaped = json.dumps(json.dumps(value, ensure_ascii=False)[1:-1], ensure_ascii=False)[1:-1]
            characters += text_value.count(escaped) * len(escaped)
            text_value = text_value.replace(escaped, '')
        return characters
    # Attempt ids often contain the assignment id. Count the longest identities first, once per occurrence.
    attempt_characters = count_identifiers(attempt_ids)
    report['identifier_characters'] = dict(assignment_ids=count_identifiers(turn_ids),
                                           attempt_ids=attempt_characters)
    from .review_protocol import collect_references
    hashes = {reference['sha256'] for reference in collect_references(supplied)}
    report['identifier_characters']['digests'] = sum(text_value.count(value) * len(value) for value in hashes)
    return report


def _measure_chain_request(records, artifacts, route, request, assignments):
    """AR17's estimate with the chain's largest observed last-send token cost, when available."""
    from .review_route_capacity import measure_request
    measure = measure_request(route, request)
    largest = None
    for attempt in records.get('review_attempt', {}).values():
        if attempt['assignment_id'] not in assignments:
            continue
        tokens = (attempt.get('usage') or {}).get('input_tokens')
        if not tokens or not attempt.get('request') or (attempt.get('usage') or {}).get('sends', 1) != 1:
            continue
        earlier = measure_request(route, json.loads(artifacts.read(attempt['request'])))
        ratio = tokens / max(1, earlier['text_characters'])
        largest = max(largest or 0, ratio)
    measure['chain_tokens_per_character'] = largest
    if largest is not None:
        measure['tokens_upper'] = max(measure['tokens_upper'], math.ceil(measure['text_characters'] * largest))
    measure['fits'] = ((measure['character_limit'] is None or measure['characters'] <= measure['character_limit'])
                       and measure['tokens_upper'] <= measure['token_limit'])
    return measure


def forecast_reading_chain(records, artifacts, route, packet, rows, record, *, request=None,
                            eligible_texts=(), assignments=(), projection=None, final_input=None):
    """Advisory legacy growth, serialized retained entries and remaining text allowances, outside packet digests."""
    if final_input is None:
        reading_turn_ids = {identifier for identifier in assignments if records['review_assignment'][identifier].get(
            'prompt', {}).get('parameters', {}).get('reading_stage') == 'reading'}
        final = measure_final_input(route, packet, rows, record, eligible_texts=eligible_texts,
                                    reading_turn_ids=reading_turn_ids or None)
    else:
        final = copy.deepcopy(final_input)
    entries = measure_retained_entries(route, record)
    allowances = {kind: value['characters'] - value['used'] for kind, value in record['allowances'].items()}
    plan_rows = list_delivery_rows(rows, record['search_delivery']) if 'search_delivery' in record else rows
    # No writes: compute spans, rather than materializing parts for a receipt.
    planned = sum(len(value) for value in _compute_spans(artifacts, plan_rows).values())
    observed = len(record['turns'])
    retained = sum(value['characters'] for kind, value in entries.items() if kind not in ('turn', 'source'))
    charged = sum(value['used'] for kind, value in record['allowances'].items() if kind in ('excerpts', 'passages'))
    ratio = max(3.24, retained / max(1, charged))
    if record['protocol'] == MATH_PROTOCOL:
        # Two recorded turns establish the first measured mean. Uncharged diagnostics are in retained.
        estimate = (final['input_characters'] + math.ceil(sum(value['characters'] for value in entries.values())
                     / observed * max(0, planned - observed))) if observed >= 2 else None
    else:
        estimate = final['input_characters'] + math.ceil(sum(allowances.values()) * ratio)
        estimate += max(0, planned - observed) * math.ceil(entries['turn']['characters'] / max(1, observed))
    finite = projection is not None and projection['finite']
    if finite:
        estimate = projection['characters']
    limit = transport.local_input_limit(route)
    forecast = dict(protocol=record['protocol'], finite=finite, is_bound=finite,
        measured_final=final, retained_entries=entries, remaining_allowances=allowances,
        planned_reading_turns=planned, planned_reading_turns_upper_bound=True, recorded_turns=observed,
        units=describe_reading_units(artifacts, plan_rows),
        final_estimate=dict(status='available' if estimate is not None else 'unavailable', estimate=not finite,
                            input_characters=estimate, input_limit_characters=limit))
    if not finite:
        forecast['message'] = FORECAST_NO_BOUND_MESSAGE
    warning = dict(warning=estimate is not None and estimate > limit, estimate=True,
                   projected_input_characters=estimate, input_limit_characters=limit,
                   message=FINITE_PROJECTION_MESSAGE if finite else FORECAST_NO_BOUND_MESSAGE,
                   ways=[CONTINUE_TO_V7_MESSAGE, EXPORT_TO_V7_MESSAGE])
    final.update(remaining_allowances=allowances, capacity_warning=warning['warning'])
    if request is not None:
        warning.update(request=_measure_chain_request(records, artifacts, route, request, set(assignments)))
        warning['warning'] = warning['warning'] or not warning['request']['fits']
    if record['protocol'] == MATH_PROTOCOL:
        warning['ways'] = [CONTINUE_MATH_ANSWER, WAIT_MATH_ANSWER, SMALLER_PACKET_ANSWER]
    return forecast, warning


def describe_reading_receipt(records, artifacts, assignment, route, logical, packet, request, *, admit_turn=True):
    """Attach forecasts to receipts only; the saved assignment, prompt and request keep their bytes."""
    record = _find_record(packet)
    rows = list_reading_rows(records, artifacts, logical['native'])
    selected = _classify_final_texts(records, artifacts, logical['native'], rows, record['protocol'])
    prior = records.get('review_assignment', {}).get(assignment['context'].get('prior_assignment_id'))
    stored = records.get('review_assignment', {}).get(assignment['id'])
    head = stored or prior
    lineage = _list_chain(records, head) if head is not None else []
    identifiers = {row['id'] for row in lineage}
    reading_turn_ids = {row['id'] for row in lineage if row.get('prompt', {}).get('parameters', {}).get(
        'reading_stage') == 'reading'}
    final = measure_final_input(route, logical, rows, record, eligible_texts=selected,
                                reading_turn_ids=reading_turn_ids or None)
    total = record['allowances'].get('record', {}).get('characters')
    projection = project_reading_chain(route, logical, rows, record, artifacts=artifacts, eligible_texts=selected,
                                       record_total=total, final_input=final)
    forecast, warning = forecast_reading_chain(records, artifacts, route, logical, rows, record,
        request=request, eligible_texts=selected, assignments=identifiers, projection=projection, final_input=final)
    admission = admit_reading_chain(route, projection, records=records, artifacts=artifacts,
                                    assignment=head or assignment)
    exact = warning['request']
    details = dict(request=exact, capacity_forecast=forecast, limit=exact['character_limit'],
                   projection=projection, units=projection['units'])
    if admit_turn and exact['character_limit'] is not None and exact['characters'] > exact['character_limit']:
        _raise_projection_stop(record, details, records=records, artifacts=artifacts,
                               assignment=head or assignment)
    if admit_turn and record['protocol'] == MATH_PROTOCOL and warning['projected_input_characters'] is not None:
        estimate = forecast['final_estimate']
        # Row 9's authenticated owner observation on this head authorizes the rest of this chain.
        continued = any(event['assignment_id'] in identifiers and event['kind'] == 'observation'
                        and event['source'] == 'owner' and event['reason'] == MATH_CONTINUATION_REASON
                        for event in records.get('review_context_event', {}).values())
        if estimate['input_characters'] > estimate['input_limit_characters'] and not continued:
            _raise_projection_stop(record, dict(details, estimate=True), records=records,
                                   artifacts=artifacts, assignment=head or assignment)
    warnings = dict(input=warning)
    if record['protocol'] == PROTOCOL_V6:
        from .review_route_capacity import describe_assignment_limits, describe_assignment_kind, read_output_profile, describe_output_warning
        limits = describe_assignment_limits(route, read_output_profile(records, route), assignment)
        warnings.update(describe_output_warning({describe_assignment_kind(assignment): limits}) or {})
    return dict(diagnostics=dict(final_input=forecast['measured_final']), capacity_forecast=forecast,
                capacity_warning=warnings, reading_chain_admission=admission)


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


def _fit_turn(route, prompt, packet, bodies=(), *, refuse=True):
    """A copy of a turn's packet whose request fits the route's input limit, with that request. While the request does
    not fit, it withholds one item at a time: the listed bodies in order, each given as its SHA-256 and the reading
    turns that delivered it, then the notes of the earlier turns, oldest first. It never withholds an excerpt or a
    passage. When nothing more can be withheld, the chain capacity stop names the characters of the base packet,
    the notes, the excerpts and the passages."""
    candidate = copy.deepcopy(packet)
    limit = transport.local_input_limit(route)
    if _find_record(candidate)['protocol'] == PROTOCOL_V6:
        limit = min(limit, _find_record(candidate)['projection_characters'])
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
            if not refuse:
                return candidate, request
            details = _measure_parts(route, prompt, candidate)
            details['retained_entries'] = measure_retained_entries(route, _find_record(candidate))
            _raise_projection_stop(_find_record(candidate), details)
        turn.pop('notes', None)
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
    if not candidates:
        final = dict(packet, **{RECORD_KEY: record})
        final_prompt = canonical_prompt('native_math', reading_stage='final', reading_protocol=record['protocol'])['text']
        _raise_projection_stop(record, _measure_parts(route, final_prompt, final))
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
        _raise_projection_stop(record, {'unit': {key: item for key, item in candidates[0].items()
                                                            if key != 'artifact_descriptor'}})
    return chosen


def _list_withholding_candidates(packet, rows, eligible_texts=()):
    """The carried rows structurally eligible for final-body withholding, without delivery credit."""
    from .problem_openness_sources import protected_evidence, required_proof
    protected = set(protected_evidence(packet)) | required_proof(packet['native'])
    if find_protocol(packet) in (PROTOCOL_V5, PROTOCOL_V6):
        protected.difference_update(eligible_texts)
    carried = {entry['artifact']['sha256'] for entry in packet['evidence']}
    return [row for row in rows if row.get('record_kind') != 'problem_statement'
            and row['artifact']['sha256'] not in protected and row['artifact']['sha256'] in carried]


def preflight_final_input(route, input_limit, packet, rows, record, *, eligible_texts=(), delivered=None, refuse=True):
    """Measure a diagnostic mandatory-final-input floor, or return unknown for unsupported representations.

    No artifacts, observations or deliverable packets are produced. The projection keeps the immutable native
    context, mandatory bodies and existing excerpts/passages, but drops optional bodies and all other chain
    metadata. JSON text serialization is monotone under these removals; encoded/framed representations need a
    separate proof before their projected size can establish impossibility.
    """
    from .review_protocol import canonical_prompt
    from .review_source_complete import MARKER
    report = {'phase': 'final_preflight', 'measurement_kind': 'mandatory_input_lower_bound',
              'status': 'unknown', 'input_characters': None, 'input_limit_characters': input_limit,
              'mandatory_components': []}
    v5 = record['protocol'] in (PROTOCOL_V5, PROTOCOL_V6)
    if v5:
        report.update(headroom_upper_bound_characters=None, planned_removable_texts=[
            dict(_describe_identity(row), artifact=row['artifact'],
                 deliveries=(delivered or {}).get(row['artifact']['sha256'], {}).get('deliveries', []))
            for row in rows if row['kind'] == 'fulltext' and row['artifact']['sha256'] in eligible_texts],
            remaining_allowances={kind: value['characters'] - value['used']
                                  for kind, value in record['allowances'].items()}, capacity_warning=None)
    if input_limit is None:
        return dict(report, reason='The route has no character input limit.')
    if (route['adapter'] not in ('codex_cli_v1', 'codex_cli_framed_v1', 'claude_cli_v1') or MARKER in packet
            or route['adapter'] == 'codex_cli_framed_v1'
            and 'review_text_encoding' in packet['native']['scientific_context']):
        return dict(report, reason='No sound mandatory-input lower bound is established for this representation.')
    candidate = copy.deepcopy(packet)
    if record['protocol'] in (PROTOCOL_V5, PROTOCOL_V6):
        _project_search_delivery(candidate, record)
    candidate[RECORD_KEY] = record
    optional = {row['artifact']['sha256'] for row in _list_withholding_candidates(candidate, rows, eligible_texts)}
    for entry in candidate['evidence']:
        if entry['artifact']['sha256'] in optional - set(eligible_texts):
            entry['content'] = ''
    candidate[RECORD_KEY] = {key: copy.deepcopy(record[key]) for key in ('protocol', 'excerpts', 'passages')}
    if record['protocol'] in (PROTOCOL_V5, PROTOCOL_V6):
        candidate[RECORD_KEY]['search_delivery'] = copy.deepcopy(record['search_delivery'])
    prompt = canonical_prompt('native_math', reading_stage='final', reading_protocol=record['protocol'])['text']
    request = _render(route, prompt, candidate)
    # Inspect construction's actual alternative/contained and binary projections for the component inventory.
    supplied = review_excerpts.read_sent_packet(request)
    if v5:
        # Diagnostic only: first validate and project the complete source contract normally, then remove texts
        # that the chain must deliver before its final turn. This creates neither a receipt nor a usable packet.
        for entry in supplied['evidence']:
            if entry['artifact']['sha256'] in eligible_texts:
                entry['content'] = ''
        request['input'][-1]['content'] = transport.PACKET_LABEL + json.dumps(supplied, sort_keys=True,
                                                                            ensure_ascii=False)
    report.update(status='measured', input_characters=_measure(route, request))
    for entry in supplied['evidence']:
        if entry['artifact']['sha256'] not in optional:
            report['mandatory_components'].append({'kind': 'evidence', 'sha256': entry['artifact']['sha256'],
                'size_bytes': entry['artifact']['size'], 'characters': len(entry.get('content', '')),
                'encoding': entry.get('encoding'), 'delivery': entry.get('delivery')})
    components = [('final_prompt', prompt), ('native_context', json.dumps(candidate['native'], sort_keys=True,
                                                                         ensure_ascii=False))]
    if v5:
        components.append(('search_manifest', json.dumps(record['search_delivery'], sort_keys=True,
                                                        ensure_ascii=False)))
    for kind, key in (('excerpt', 'excerpts'), ('passage', 'passages')):
        components.extend((kind, json.dumps(item, sort_keys=True, ensure_ascii=False)) for item in record[key])
    for kind, content in components:
        data = content.encode('utf-8')
        report['mandatory_components'].append({'kind': kind, 'sha256': hashlib.sha256(data).hexdigest(),
                                               'size_bytes': len(data), 'characters': len(content)})
    if v5:
        headroom = input_limit - report['input_characters']
        report.update(headroom_upper_bound_characters=headroom,
                      capacity_warning=headroom < sum(report['remaining_allowances'].values()))
    if refuse and report['input_characters'] > input_limit:
        final = measure_final_input(route, packet, rows, record, eligible_texts=eligible_texts)
        _raise_projection_stop(record, dict(report,
            projection=dict(finite=False, characters=None, parts=final['parts']),
            capacity_forecast=dict(protocol=record['protocol'], finite=False, is_bound=False,
                message=FORECAST_NO_BOUND_MESSAGE, measured_final=final,
                retained_entries=measure_retained_entries(route, record),
                remaining_allowances={kind: value['characters'] - value['used']
                                      for kind, value in record['allowances'].items()})))
    return report


def _list_withholdable_bodies(packet, rows, spans, observations, eligible_texts=()):
    """The bodies that the final turn may withhold, in the order in which it withholds them, each as its SHA-256 and
    the reading turns that delivered it: the full texts from the end of text_delivery_priority, then the other full
    texts, the captured responses, the reading records and the search records, each kind largest first. A body
    qualifies when the actual requests of reading turns delivered it complete, whole or in all of its parts. The
    statement record and independently protected bodies stay. Eligible v5 selected texts use the same complete
    reading-delivery rule as the other full texts."""
    native = packet['native']
    priority = native['scientific_context'].get('text_delivery_priority', [])
    whole, parts = _collect_coverage(observations)
    receipts = (select_delivered_bodies(_collect_delivered_bodies(rows, spans, observations).values())
                if packet.get(RECORD_KEY, {}).get('protocol') in (PROTOCOL_V5, PROTOCOL_V6) else None)
    ranked = []
    for row in _list_withholding_candidates(packet, rows, eligible_texts):
        key, sha256 = review_excerpts.find_identity(row), row['artifact']['sha256']
        if not _is_complete(key, spans[key], whole, parts):
            continue
        if row['kind'] == 'fulltext' and sha256 in priority:
            rank = (0, -priority.index(sha256))
        else:
            rank = (1 + (V6_WITHHOLDING_ORDER if find_protocol(packet) == PROTOCOL_V6 else WITHHOLDING_ORDER).index(
                     row.get('record_kind', row['kind'])), -spans[key][-1][1])
        delivering = [observation['assignment_id'] for observation in observations if observation['reading']
                      and (key in observation['whole'] or any(item[0] == key for item in observation['parts']))]
        if receipts is not None:
            delivering = list(dict.fromkeys(receipt['assignment_id'] for receipt in receipts[sha256]['deliveries']))
        ranked.append((rank, key, sha256, delivering))
    bodies, seen = [], set()
    for _, _, sha256, delivering in sorted(ranked):
        if sha256 not in seen:
            seen.add(sha256)
            bodies.append((sha256, delivering))
    return bodies


def _build_final_turn(route, packet, record, rows, spans, observations, eligible_texts=()):
    """The native packet with the reading record, withholding only as far as its request needs to fit the route."""
    from .review_protocol import canonical_prompt
    value = {key: item for key, item in packet.items() if key != 'digest'}
    value[RECORD_KEY] = record
    if record['protocol'] in (PROTOCOL_V5, PROTOCOL_V6):
        value = copy.deepcopy(value)
        _project_search_delivery(value, record)
    bodies = _list_withholdable_bodies(value, rows, spans, observations, eligible_texts)
    prompt = canonical_prompt('native_math', reading_stage='final', reading_protocol=record['protocol'])['text']
    return _fit_turn(route, prompt, value, bodies)[0]


def _project_search_delivery(packet, record):
    """Omit factored search bodies, returning their digests, while preserving independently protected bytes."""
    from .problem_openness_sources import protected_evidence, required_proof
    protected = set(protected_evidence(packet)) | required_proof(packet['native'])
    factored = {row['search_digest'] for row in record['search_delivery']['searches']
                if row['residual'] is not None} - protected
    for entry in packet['evidence']:
        if entry['artifact']['sha256'] in factored:
            entry.update(content='', delivery=SEARCH_DELIVERY, note=SEARCH_DELIVERY_NOTE)
    return factored


def _read_prior(records, artifacts, value, prior_id):
    """The earlier turn that a chain turn continues, with its packet and its observed reading response."""
    from .review_protocol import assignment_state, find_replacement, has_ended_without_output, is_dead_assignment
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
           and not is_dead_assignment(records, saved)
           for saved in records.get('review_assignment', {}).values()):
        raise ResearchError('review_source_continuation_fork', 'Continue the latest turn of the reading chain')
    state = assignment_state(records, artifacts, prior_id)
    if not state['ready']:
        raise ResearchError('review_source_continuation_unverified',
                            'The earlier turn needs an observed, authenticated output in a verified context',
                            {'obligations': state['obligations']})
    prior_packet = json.loads(artifacts.read(prior['packet']))
    validate_turn_output(prior_packet, state['output'])
    _require(has_deliverable_passage(records, artifacts, prior_packet, state['output'],
                                      assignment=prior, attempt=records['review_attempt'][state['attempt_id']]), PASSAGE_PROGRESS_ERR_MSG,
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


def _describe_delivered_turn(assignment_id, attempt_id, sent, rows, spans, *, reading, manifest=None):
    """Describe the actual request's complete unit/part receipts without observing its output again."""
    complete = review_excerpts.find_complete_bodies(sent)
    planned = ({(review_excerpts.find_identity(row), row['part'])
                for row in sent.get(TURN_KEY, {}).get('reading_plan', {}).get('delivered_now', [])}
               if manifest is not None else set())
    whole, parts, delivered_parts = set(), set(), []
    for row in rows:
        key = review_excerpts.find_identity(row)
        if (row['artifact']['sha256'] in complete
                and (manifest is None or not reading or (key, 1) in planned and len(spans[key]) == 1)):
            whole.add(key)
        for part, (start, end, sha256) in enumerate(spans[key], 1):
            if (len(spans[key]) > 1 and sha256 in complete
                    and (manifest is None or (key, part) in planned)):
                parts.add((key, part))
                delivered_parts.append(dict(_describe_identity(row), part=part, parts=len(spans[key]),
                                            start=start, end=end, characters=end - start))
    turn = {'assignment_id': assignment_id, 'attempt_id': attempt_id,
            'delivered_units': [_describe_identity(row) for row in rows
                                if review_excerpts.find_identity(row) in whole]}
    if delivered_parts:
        turn['delivered_parts'] = delivered_parts
    return turn, whole, parts


def _observe_turns(records, artifacts, assignments, rows, spans, manifest=None):
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
        packet = json.loads(artifacts.read(assignment['packet']))
        reading = find_stage(packet) == 'reading'
        turn, whole, parts = _describe_delivered_turn(assignment['id'], attempt['id'], sent, rows, spans,
                                                     reading=reading, manifest=manifest)
        observations.append({'assignment_id': assignment['id'], 'output': state['output'], 'reading': reading,
                             'delivered_now': packet[TURN_KEY]['reading_plan']['delivered_now'] if reading else [],
                             'whole': whole, 'parts': parts, 'turn': turn})
        if manifest is not None:
            complete = review_excerpts.find_complete_bodies(sent)
            observations[-1]['bodies'] = {entry['artifact']['sha256']: entry['content'].encode('utf-8')
                                         for entry in sent.get('evidence', [])
                                         if entry['artifact']['sha256'] in complete}
    if manifest is not None:
        completed = set()
        for index, observation in enumerate(observations):
            receipts = _verify_search_coverage(artifacts, manifest, rows, spans, observations[:index + 1])
            newly = [receipt for receipt in receipts if receipt['search_digest'] not in completed]
            completed.update(receipt['search_digest'] for receipt in newly)
            if newly:
                observation['turn']['completed_searches'] = newly
    return observations


def _collect_delivered_bodies(rows, spans, observations):
    """Keep exact whole bytes and contributing receipts by unit identity, including units with equal digests."""
    delivered = {}
    for row in rows:
        key = review_excerpts.find_identity(row)
        reference = row['artifact']
        deliveries, pieces = [], {}
        for observation in observations:
            if not observation['reading']:
                continue
            body = observation['bodies'].get(reference['sha256'])
            if key in observation['whole']:
                pieces = {1: body}
                deliveries = [dict({name: observation['turn'][name] for name in ('assignment_id', 'attempt_id')},
                                   part=1, parts=1, start=0, end=spans[key][-1][1], sha256=reference['sha256'])]
                break
            for part, (start, end, sha256) in enumerate(spans[key], 1):
                if (key, part) in observation['parts'] and part not in pieces:
                    pieces[part] = observation['bodies'][sha256]
                    deliveries.append(dict({name: observation['turn'][name] for name in ('assignment_id', 'attempt_id')},
                                           part=part, parts=len(spans[key]), start=start, end=end, sha256=sha256))
        if deliveries and (deliveries[0]['parts'] == 1 or len(pieces) == len(spans[key])):
            delivered[key] = dict(_describe_identity(row), artifact=reference,
                                  content=b''.join(pieces[part] for part in sorted(pieces)), deliveries=deliveries)
    return delivered


def select_delivered_bodies(units):
    """Choose the first complete unit by identity for each digest, consistently across fitting and validation."""
    selected = {}
    for unit in sorted(units, key=review_excerpts.find_identity):
        selected.setdefault(unit['artifact']['sha256'], unit)
    return selected


def _verify_search_coverage(artifacts, manifest, rows, spans, observations):
    """Reconstruct search bytes only from whole components or all exact parts observed in reading requests."""
    from .review_search_delivery import verify_search_deliveries
    originals = {entry['search_digest']: artifacts.read(entry['artifact']) for entry in manifest['searches']}
    return verify_search_deliveries(manifest, select_delivered_bodies(
        _collect_delivered_bodies(rows, spans, observations).values()), originals)


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


def measure_record_charge(route, record):
    """The exact serialized characters charged by v6, including list separators for extra turns."""
    extra = [turn for turn in record['turns'] if turn['stage'] == 'final']
    reading = any(turn['stage'] == 'reading' for turn in record['turns'])
    return (sum(measure_record_entries(route, record[key]) for key in ('excerpts', 'passages'))
            + measure_record_entries(route, extra) + (2 if extra and reading else 0))


def _retain_v6_entry(route, record, key, entry, sources, allowances, kind):
    """Retain an entry only when the third total holds it; rejected entries charge no text allowance."""
    allowance = record['allowances']['record']
    used = allowance['used'] + measure_record_entries(route, [entry]) + (2 if record[key] else 0)
    if used > allowance['characters']:
        counts = allowance['not_retained']
        counts[kind] = counts.get(kind, 0) + 1
        return False
    record[key].append(entry)
    record['sources'] = sources
    record['allowances'] = allowances
    record['allowances']['record']['used'] = used
    return True


def _retain_v6_output(records, artifacts, earlier, output, rows, route, turns):
    """Retain a whole output in order after charging its actual final-turn receipt."""
    record = copy.deepcopy(earlier)
    record['turns'] = turns
    used = measure_record_charge(route, record)
    if used > record['allowances']['record']['characters']:
        return None
    record['allowances']['record']['used'] = used
    unit_sources = _collect_unit_sources(rows)
    excerpt_protocol = earlier['excerpt_protocol']
    def retain_excerpt(entry, allowances):
        sources = copy.deepcopy(record['sources'])
        compact = _compact_entry(entry, sources)
        return _retain_v6_entry(route, record, 'excerpts', compact, sources, allowances,
                                classify_retained_entry(compact))

    def retain_passage(entry, allowances):
        sources = copy.deepcopy(record['sources'])
        compact = _compact_entry(entry, sources)
        compact['assignment_id'] = turns[-1]['assignment_id']
        return _retain_v6_entry(route, record, 'passages', compact, sources, allowances,
                                classify_retained_entry(compact, passage=True))

    review_excerpts.check_excerpts(records, artifacts, output['excerpts'], record['allowances'],
        unit_sources=unit_sources, protocol=excerpt_protocol, retained=record['excerpts'], retain_entry=retain_excerpt)
    review_excerpts.answer_passages(records, artifacts, output['passage_requests'], record['allowances'],
        unit_sources=unit_sources, protocol=excerpt_protocol, retain_entry=retain_passage)
    return record


def _extend_v6_record(records, artifacts, prior_packet, state, rows, observations):
    """Rebuild bounded retained state in output order, charging every final row before its entries."""
    earlier = _find_record(prior_packet)
    _require_observed_turns(earlier, observations[:-1])
    route = records['review_route'][records['review_assignment'][observations[-1]['assignment_id']]['route_id']]
    turns = [dict(observation['turn'], stage='reading' if observation['reading'] else 'final',
                  **(dict(notes=observation['output']['notes']) if observation['reading'] else
                     dict(notes_withheld=NOTES_WITHHELD_NOTE.format(observation['assignment_id']))))
             for observation in observations]
    record = _retain_v6_output(records, artifacts, earlier, state['output'], rows, route, turns)
    if record is None:
        raise ResearchError(INVALID_RESPONSE, RECORD_PASSAGE_LIMIT_MESSAGE, {'path': '/passage_requests'})
    manifest = earlier['search_delivery']
    delivery_rows = list_delivery_rows(rows, manifest)
    record['search_receipts'] = _verify_search_coverage(
        artifacts, manifest, delivery_rows, _compute_spans(artifacts, delivery_rows), observations)
    return record


def _extend_record(records, artifacts, prior_packet, state, rows, observations, protocol):
    """The compact reading record of the given protocol after the prior turn. It lists every observed turn of the chain
    with the units that its actual request delivered and with its notes from its observed output, keeps the earlier
    excerpts and passage answers, and adds the prior output's excerpts and the answers to its passage requests, checked
    under the prior's own protocol. A verbose record of 0.64.2 to 0.66.2 keeps its checked texts and allowance use."""
    if protocol == PROTOCOL_V6:
        return _extend_v6_record(records, artifacts, prior_packet, state, rows, observations)
    earlier = _find_record(prior_packet)
    _require_observed_turns(earlier, observations[:-1])
    unit_sources = _collect_unit_sources(rows) if find_protocol(prior_packet) in GENERIC_UNIT_PROTOCOLS else None
    excerpt_protocol = earlier.get('excerpt_protocol', review_excerpts.PROTOCOL)
    excerpts, allowances = review_excerpts.check_excerpts(records, artifacts, state['output']['excerpts'],
                                                          earlier['allowances'], unit_sources=unit_sources,
                                                          protocol=excerpt_protocol, retained=earlier['excerpts'])
    passages, allowances = review_excerpts.answer_passages(records, artifacts, state['output']['passage_requests'],
                                                           allowances, unit_sources=unit_sources,
                                                           protocol=excerpt_protocol)
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
    record = {'protocol': protocol, 'turns': turns, 'sources': sources, 'excerpts': excerpts, 'passages': passages,
              'allowances': allowances}
    if 'excerpt_protocol' in earlier:
        record['excerpt_protocol'] = excerpt_protocol
    if protocol in (PROTOCOL_V5, PROTOCOL_V6):
        manifest = earlier['search_delivery']
        delivery_rows = list_delivery_rows(rows, manifest)
        record.update(search_delivery=copy.deepcopy(manifest), search_receipts=_verify_search_coverage(
            artifacts, manifest, delivery_rows, _compute_spans(artifacts, delivery_rows), observations))
    return record


def _create_record(protocol, manifest=None, *, excerpt_protocol=None):
    """The reading record that a chain's first turn carries: no turns, sources, excerpts or passages yet."""
    record = {'protocol': protocol, 'turns': [], 'sources': [], 'excerpts': [], 'passages': [],
              'allowances': review_excerpts.create_allowances()}
    if excerpt_protocol is not None:
        record['excerpt_protocol'] = excerpt_protocol
    if protocol == PROTOCOL_V6:
        from .storage import _MAX_STORED_INTEGER
        record.update(planned_reading_turns=0, projection_characters=_MAX_STORED_INTEGER)
    if protocol in (PROTOCOL_V5, PROTOCOL_V6):
        record.update(search_delivery=manifest, search_receipts=[])
    return record


def prepare_packet(records, artifacts, value, route, packet):
    """The packet of a native_math assignment and its place in a reading chain. An openness packet, of a problem or of
    a math result, stays a single turn when one input of the route holds every reading unit complete and its units
    total at most one turn's reading budget. Otherwise the first assignment is reading turn 1; a turn that continues a
    chain is the next reading turn while a required unit has not reached a reading turn complete, and then the final
    turn. V5 planning diagnostics accompany the returned chain metadata; record_assignment removes them before
    saving the chain and returns them separately from the scientific packet."""
    native = packet['native']
    prior_id = value['context'].get('prior_assignment_id')
    if prior_id is None and is_openness_packet(native):
        context = native['scientific_context']
        if (context['openness_minimum'].get('schema_version') != 2
                or 'minimum_gaps' not in context or 'audit_forecast' not in context):
            raise ResearchError('openness_packet_outdated', PACKET_OUTDATED_MESSAGE, {'handoff': 'export'})
    from .capture_selection import bind_packet_selections
    records = bind_packet_selections(records, native['scientific_context'], artifacts=artifacts, review=value)
    if prior_id is None:
        _require_initial_captures(records, native)
        if (not (is_openness_packet(native) or is_math_openness_packet(native))
                or transport.local_input_limit(route) is None):
            return packet, None
        from .review_protocol import canonical_prompt
        rows = list_reading_rows(records, artifacts, native, review=value)
        characters = sum(len(artifacts.read(row['artifact']).decode('utf-8')) for row in rows)
        if (characters <= review_excerpts.TURN_FULLTEXT_CHARACTERS
                and _fits(route, canonical_prompt('native_math', version=7 if is_openness_packet(native) else 6)['text'],
                          packet, [row['artifact'] for row in rows])):
            return packet, None
        protocol = find_chain_protocol(native)
        require_reserved_identifier(value['id'], protocol)
        plan = _plan_search_delivery(artifacts, native) if protocol in (PROTOCOL_V5, PROTOCOL_V6) else None
        if plan is not None:
            for content in plan['bodies'].values():
                artifacts.put(content, 'application/json')
        record = _create_record(protocol, plan['manifest'] if plan is not None else None,
                                excerpt_protocol=review_excerpts.SESSION_PROTOCOL)
        chain = {'first_assignment_id': value['id'], 'turn': 1}
        if protocol == PROTOCOL_V6:
            record['planned_reading_turns'] = sum(len(parts) for parts in
                _compute_spans(artifacts, list_delivery_rows(rows, record['search_delivery'])).values())
            projection = project_reading_chain(route, packet, rows, record, artifacts=artifacts,
                eligible_texts=_classify_final_texts(records, artifacts, native, rows, protocol))
            admission = admit_reading_chain(route, projection, records=records, artifacts=artifacts, assignment=value)
            if admission['state'] != 'record':
                raise ResearchError(PROJECTION_STOP, PROJECTION_STOP_MESSAGE, admission['details'])
            record['allowances']['record'] = dict(characters=projection['record_total'], used=0, not_retained={})
            record['projection_characters'] = projection['characters']
        selected = _classify_final_texts(records, artifacts, native, rows, protocol)
        report = preflight_final_input(route, transport.local_input_limit(route), packet, rows, record,
                                       eligible_texts=selected, refuse=protocol != MATH_PROTOCOL)
        if protocol in (PROTOCOL_V5, PROTOCOL_V6):
            chain['diagnostics'] = {'final_input': report}
        if plan is not None:
            for content in plan['bodies'].values():
                artifacts.put(content, 'application/json')
            rows = list_delivery_rows(rows, plan['manifest'])
        return _build_reading_turn(artifacts, route, packet, record, [], list_reading_units(artifacts, rows), 1), chain
    prior, prior_packet, state = _read_prior(records, artifacts, value, prior_id)
    rows = list_reading_rows(records, artifacts, native, review=value)
    protocol = find_chain_protocol(native, find_protocol(prior_packet))
    require_reserved_identifier(value['id'], protocol)
    manifest = _find_record(prior_packet)['search_delivery'] if protocol in (PROTOCOL_V5, PROTOCOL_V6) else None
    delivery_rows = list_delivery_rows(rows, manifest) if manifest is not None else rows
    spans = _compute_spans(artifacts, delivery_rows)
    prior_chain = _list_chain(records, prior)
    observations = _observe_turns(records, artifacts, prior_chain, delivery_rows, spans, manifest)
    if protocol in (PROTOCOL_V5, PROTOCOL_V6):
        try:
            _require_problem_continuity(records, artifacts, native, prior_chain, rows, observations, manifest)
        except ResearchError as error:
            from .capture_selection import PACKET_KEY, SELECTION_KIND
            context = native['scientific_context']
            if (error.code == 'openness_reading_incomplete' and SELECTION_KIND in records
                    and PACKET_KEY not in context and PACKET_KEY not in context.get('source_inventory', {})):
                error.details = dict(error.details or {}, handoff='export')
            raise
    record = _extend_record(records, artifacts, prior_packet, state, rows, observations, protocol)
    chain = dict(prior['reading_chain'], turn=prior['reading_chain']['turn'] + 1)
    selected = _classify_final_texts(records, artifacts, native, rows, protocol)
    delivered_bodies = (select_delivered_bodies(_collect_delivered_bodies(delivery_rows, spans, observations).values())
                        if protocol in (PROTOCOL_V5, PROTOCOL_V6) else None)
    report = preflight_final_input(route, transport.local_input_limit(route), packet, rows, record,
                                   eligible_texts=selected, delivered=delivered_bodies, refuse=protocol != MATH_PROTOCOL)
    if protocol in (PROTOCOL_V5, PROTOCOL_V6):
        chain['diagnostics'] = {'final_input': report}
    remaining = _list_remaining_units(artifacts, delivery_rows, spans, observations)
    if remaining:
        delivered = [dict({key: item for key, item in row.items() if key != 'artifact'},
                          artifact_descriptor=descriptor(row['artifact']))
                     for observation in observations for row in observation['delivered_now']]
        return _build_reading_turn(artifacts, route, packet, record, delivered, remaining, chain['turn']), chain
    return _build_final_turn(route, packet, record, rows, _compute_spans(artifacts, rows), observations, selected), chain


def _require_initial_captures(records, native):
    """Refuse an unbound export whose carried extraction disagrees with its derived main capture."""
    from .capture_selection import PACKET_KEY, SELECTION_KIND, list_bound_captures
    from .graph import main_captures
    context = native['scientific_context']
    if (SELECTION_KIND not in records or PACKET_KEY in context
            or PACKET_KEY in context.get('source_inventory', {}) or 'openness_minimum' not in context):
        return
    carried = {entry['artifact']['sha256'] for entry in native['evidence']}
    for (version, original), captures in list_bound_captures(records, context).items():
        main = next(capture for capture in main_captures(records, records['work'][version])
                    if capture['original']['sha256'] == original)
        if (main.get('text') is not None and main['text']['sha256'] not in carried
                and any(capture['text']['sha256'] in carried for capture in captures)):
            raise ResearchError('openness_reading_incomplete',
                                'This unbound packet carries another extraction of a required original. Export a '
                                'new openness packet before starting its review.',
                                {'version_id': version, 'handoff': 'export'})


def _classify_final_texts(records, artifacts, native, rows, protocol):
    """Selected full texts without any independent protection role, only for the v5 final planner."""
    if protocol not in (PROTOCOL_V5, PROTOCOL_V6):
        return set()
    from .problem_openness_sources import classify_selected_texts
    contained = {span['sha256'] for span in native['scientific_context'].get(
        'openness_delivery', {}).get('contained_sources', [])}
    return {sha256 for sha256, reasons in classify_selected_texts(records, artifacts, native, rows).items()
            if reasons == {'selected_text'} and sha256 not in contained}


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
    kinds = (review_excerpts.UNIT_KINDS_V6 if protocol == PROTOCOL_V6 else
             review_excerpts.UNIT_KINDS if protocol in GENERIC_UNIT_PROTOCOLS else None)
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
        if find_protocol(packet) == PROTOCOL_V6:
            from .native_review import validate_finding
            validate_finding(packet, output)
        else:
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


def has_deliverable_passage(records, artifacts, packet, output, *, assignment=None, attempt=None, turn=None):
    """A final reading response must retain a delivered passage after all preceding output entries and its receipt."""
    if (find_stage(packet) != 'final' or packet[RECORD_KEY]['protocol'] not in
            (PROTOCOL_V4, PROTOCOL_V5, PROTOCOL_V6, MATH_PROTOCOL) or output.get('stage') != 'reading'):
        return True
    earlier = packet[RECORD_KEY]
    allowances = earlier['allowances']
    turns = earlier['turns']
    review = assignment or (records.get('review_assignment', {}).get(turns[-1]['assignment_id']) if turns else None)
    rows = list_reading_rows(records, artifacts, packet['native'], review=review)
    if find_protocol(packet) == PROTOCOL_V6:
        route = records['review_route'][review['route_id']]
        if turn is None:
            delivery_rows = list_delivery_rows(rows, earlier['search_delivery'])
            if assignment is not None and attempt is not None:
                sent = review_excerpts.read_sent_packet(json.loads(artifacts.read(attempt['request']))) or {}
                turn, _, _ = _describe_delivered_turn(assignment['id'], attempt['id'], sent, delivery_rows,
                    _compute_spans(artifacts, delivery_rows), reading=False, manifest=earlier['search_delivery'])
            else:
                # Direct callers without an observed request reserve every possible complete-unit receipt.
                identifier = '\x00' * RESERVED_IDENTIFIER_CHARACTERS
                turn = dict(assignment_id=identifier, attempt_id=identifier,
                            delivered_units=[_describe_identity(row) for row in delivery_rows])
        current = dict(turn, stage='final', notes_withheld=NOTES_WITHHELD_NOTE.format(turn['assignment_id']))
        record = _retain_v6_output(records, artifacts, earlier, output, rows, route,
                                   copy.deepcopy(turns) + [current])
        return record is not None and any(entry['status'] == 'delivered'
            and entry['assignment_id'] == turn['assignment_id'] for entry in record['passages'][len(earlier['passages']):])
    _, after = review_excerpts.answer_passages(records, artifacts, output['passage_requests'], allowances,
                                               unit_sources=_collect_unit_sources(rows))
    return after['passages']['used'] > allowances['passages']['used']


def find_final_assignment(records, artifacts, identifier):
    """The effective terminal turn of the reading chain whose first turn is the given assignment. A continuation that
    ended without output and that a later continuation of the same turn replaced is history, not a branch; any other
    second continuation is a fork. The terminal turn is a final turn whose observed output is the native result."""
    from .review_protocol import assignment_state, find_replacement, has_ended_without_output, is_dead_assignment
    assignments = records.get('review_assignment', {})
    current = assignments[identifier]
    while True:
        live = [saved for saved in assignments.values()
                if saved['role'] == 'native_math' and saved['context'].get('prior_assignment_id') == current['id']
                and not ((has_ended_without_output(records, saved['id']) or is_dead_assignment(records, saved))
                         and find_replacement(records, saved))]
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


def _list_legacy_texts(records, native, *, artifacts=None, review=None):
    """The stored extracted full text of each version that the readings of the openness minimum read, ordered by the
    packet's text_delivery_priority and then by version id: the full texts of the chains whose final turn 0.64.2 to
    0.66.2 built."""
    from .capture_selection import bind_packet_selections
    records = bind_packet_selections(records, native['scientific_context'], artifacts=artifacts, review=review)
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
    texts = _list_legacy_texts(records, native, artifacts=artifacts, review=assignment)
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


def _require_withheld_bodies(packet, rows, spans, observations, eligible_texts=()):
    """A final turn withholds a prefix of the bodies that it may withhold, in the withholding order, each with the note
    that names the reading turns that delivered it."""
    withheld = {entry['artifact']['sha256']: entry['note'] for entry in packet['evidence']
                if entry.get('delivery') == BODY_WITHHELD_DELIVERY}
    allowed = _list_withholdable_bodies(packet, rows, spans, observations, eligible_texts)[:len(withheld)]
    if withheld != {sha256: BODY_WITHHELD_NOTE.format(', '.join(delivering)) for sha256, delivering in allowed}:
        raise ResearchError('openness_reading_incomplete', 'A final turn withholds only a prefix of the bodies that '
                            'its reading turns delivered complete, in the withholding order, each with the note that '
                            'names those turns', {'sha256': sorted(withheld)})


def _require_math_continuity(records, artifacts, native, chain, rows, spans, observations):
    """A math chain keeps the binding of its first turn: the native packet, route, reviewer, author, protocol and the
    canonical prompt of each stage, under the common text that the turn's own prompt records. Each turn carries the
    reading record that the observed outputs of the turns before it give, with only the notes that fitting withholds,
    and a final turn withholds only bodies that reading turns delivered complete. Every turn but the last returned a
    valid reading response that continued the chain, and the last is a final turn whose output is the native
    result."""
    from .review_protocol import canonical_prompt, find_common_prompt_version
    first = chain[0]
    first_packet = json.loads(artifacts.read(first['packet']))
    record = _create_record(MATH_PROTOCOL, excerpt_protocol=_find_record(first_packet).get('excerpt_protocol'))
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
                or assignment['prompt'] != canonical_prompt(
                    'native_math', reading_stage=stage, reading_protocol=MATH_PROTOCOL,
                    common_version=find_common_prompt_version(assignment['prompt']))):
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
            _require(has_deliverable_passage(records, artifacts, packet, output,
                                              assignment=assignment, turn=observation['turn']), PASSAGE_PROGRESS_ERR_MSG,
                     path='/passage_requests')
            record = _extend_record(records, artifacts, packet, {'output': output}, rows, observations[:index + 1],
                                    MATH_PROTOCOL)


def _require_problem_plan(artifacts, packet, rows, spans, observations):
    """A v5 reading plan describes exactly the remaining physical units and the preceding deliveries."""
    from .artifacts import describe_artifact
    whole, parts = _collect_coverage(observations)
    expected = []
    for row in rows:
        key = review_excerpts.find_identity(row)
        if _is_complete(key, spans[key], whole, parts):
            continue
        content = artifacts.read(row['artifact']).decode('utf-8')
        for part, (start, end, _) in enumerate(spans[key], 1):
            if (key, part) in parts:
                continue
            reference = (row['artifact'] if len(spans[key]) == 1 else
                         describe_artifact(content[start:end].encode('utf-8'), PART_MEDIA_TYPE))
            expected.append(dict({name: value for name, value in row.items() if name != 'artifact'},
                                 part=part, parts=len(spans[key]), start=start, end=end, characters=end - start,
                                 artifact_descriptor=descriptor(reference)))
    plan = packet[TURN_KEY]['reading_plan']
    now = [dict({key: value for key, value in row.items() if key != 'artifact'},
                 artifact_descriptor=descriptor(row['artifact'])) for row in plan['delivered_now']]
    before = [dict({key: value for key, value in row.items() if key != 'artifact'},
                    artifact_descriptor=descriptor(row['artifact']))
              for observation in observations for row in observation['delivered_now']]
    if (not now or now + plan['remaining'] != expected or plan['delivered_before'] != before
            or plan['turn'] != len(observations) + 1
            or sum(row['characters'] for row in now) > review_excerpts.TURN_FULLTEXT_CHARACTERS):
        raise ResearchError('openness_reading_incomplete', 'A v5 reading plan must name the exact remaining '
                            'components and the preceding deliveries')


def _require_problem_continuity(records, artifacts, native, chain, rows, observations, manifest, *, final=False):
    """Rebuild every v5 carried record from authenticated outputs while keeping the first assignment's boundary."""
    from .review_protocol import canonical_prompt, find_common_prompt_version
    if manifest != _plan_search_delivery(artifacts, native)['manifest']:
        raise ResearchError('openness_reading_incomplete', 'The frozen search manifest differs from the bound originals')
    first = chain[0]
    first_packet = json.loads(artifacts.read(first['packet']))
    protocol = find_protocol(first_packet)
    record = _create_record(protocol, manifest,
                            excerpt_protocol=_find_record(first_packet).get('excerpt_protocol'))
    if protocol == PROTOCOL_V6:
        record['allowances']['record'] = dict(
            characters=_find_record(first_packet)['allowances']['record']['characters'], used=0, not_retained={})
        record['planned_reading_turns'] = _find_record(first_packet)['planned_reading_turns']
        record['projection_characters'] = _find_record(first_packet)['projection_characters']
    delivery_rows = list_delivery_rows(rows, manifest)
    spans = _compute_spans(artifacts, rows)
    delivery_spans = _compute_spans(artifacts, delivery_rows)
    if protocol == PROTOCOL_V6 and record['planned_reading_turns'] != sum(len(parts) for parts in delivery_spans.values()):
        raise ResearchError('openness_reading_incomplete', 'The first reading plan must reserve every physical unit part')
    for index, (assignment, observation) in enumerate(zip(chain, observations)):
        packet, output = json.loads(artifacts.read(assignment['packet'])), observation['output']
        stage = find_stage(packet)
        if stage == 'final':
            bound = packet['native'] == native
        else:
            turn = packet[TURN_KEY]
            minimum = native['scientific_context']['openness_minimum']
            bound = (turn['statement'] == native['subject'] and all(
                turn[key] == native[key] for key in ('subject_digest', 'claim_digest', 'reviewer'))
                and turn['formulations'] == {'statement': minimum['statement'], 'neighbours': minimum['neighbours']})
        if (not bound or find_protocol(packet) != protocol or assignment['role'] != 'native_math'
                or assignment['context']['native_packet'] != first['context']['native_packet']
                or assignment['route_id'] != first['route_id']
                or normalized_text(assignment['reviewer_id']) != normalized_text(first['reviewer_id'])
                or normalized_text(assignment['author_id']) != normalized_text(first['author_id'])
                or assignment.get('reading_chain') != {'first_assignment_id': first['id'], 'turn': index + 1}
                or assignment['prompt'] != canonical_prompt(
                    'native_math', reading_stage=stage, reading_protocol=protocol,
                    common_version=find_common_prompt_version(assignment['prompt']))):
            raise ResearchError('openness_reading_incomplete', 'Every turn of a v5 problem chain keeps the binding, '
                                'protocol and prompt of its first turn', {'assignment_id': assignment['id']})
        carried = _find_record(packet)
        if _project_withheld_notes(record, carried) != carried:
            raise ResearchError('openness_reading_incomplete', 'A v5 turn carries a record that the authenticated '
                                'outputs and deliveries of earlier turns do not give', {'assignment_id': assignment['id']})
        if stage == 'reading':
            _require_problem_plan(artifacts, packet, delivery_rows, delivery_spans, observations[:index])
        else:
            expected = copy.deepcopy(packet)
            omitted = _project_search_delivery(expected, record)
            actual_entries = {entry['artifact']['sha256']: entry for entry in packet['evidence']}
            expected_entries = {entry['artifact']['sha256']: entry for entry in expected['evidence']}
            factored = {entry['search_digest'] for entry in manifest['searches'] if entry['residual'] is not None}
            if (any(actual_entries.get(sha256) != expected_entries.get(sha256) for sha256 in factored)
                    or {entry['artifact']['sha256'] for entry in packet['evidence']
                        if entry.get('delivery') == SEARCH_DELIVERY} != omitted):
                raise ResearchError('openness_reading_incomplete', 'The v5 final search omissions must match the '
                                    'frozen manifest exactly')
            selected = _classify_final_texts(records, artifacts, native, rows, protocol)
            _require_withheld_bodies(packet, rows, spans, observations[:index], selected)
        validate_turn_output(packet, output)
        terminal = stage == 'final' and output.get('stage') != 'reading'
        if terminal != (final and index + 1 == len(chain)):
            raise ResearchError('openness_reading_incomplete', 'A v5 problem chain ends at its first native final result')
        if not terminal:
            _require(has_deliverable_passage(records, artifacts, packet, output,
                                              assignment=assignment, turn=observation['turn']), PASSAGE_PROGRESS_ERR_MSG,
                     path='/passage_requests')
            record = _extend_record(records, artifacts, packet, {'output': output}, rows, observations[:index + 1],
                                    protocol)


def verify_problem_coverage(records, artifacts, assignment, *, final=False):
    """Authenticate a v5 prefix and derive original-search and whole-unit credit without source validation.

    This helper also serves final source validation without recursively calling validate_delivery. Search receipts use
    only reading turns of this chain. Unit receipts likewise describe complete earlier reading delivery, with the
    original artifact and exact contributing assignments, attempts and parts.
    """
    from .native_review import load_packet
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    rows = list_reading_rows(records, artifacts, native, review=assignment)
    manifest = _plan_search_delivery(artifacts, native)['manifest']
    delivery_rows = list_delivery_rows(rows, manifest)
    spans = _compute_spans(artifacts, delivery_rows)
    chain = _list_chain(records, assignment)
    observations = _observe_turns(records, artifacts, chain, delivery_rows, spans, manifest)
    _require_problem_continuity(records, artifacts, native, chain, rows, observations, manifest, final=final)
    receipts = _verify_search_coverage(artifacts, manifest, delivery_rows, spans, observations)
    searches = [receipt['search_digest'] for receipt in receipts]
    whole, parts = _collect_coverage(observations)
    delivered = _collect_delivered_bodies(delivery_rows, spans, observations)
    units = [dict(_describe_identity(row), artifact=row['artifact'],
                  deliveries=delivered[review_excerpts.find_identity(row)]['deliveries'])
             for row in rows if row.get('record_kind') != 'literature_search'
             and _is_complete(review_excerpts.find_identity(row), spans[review_excerpts.find_identity(row)],
                              whole, parts)]
    if final:
        whole.update(observations[-1]['whole'])
        missing = [_describe_identity(row) for row in rows if (
            row['artifact']['sha256'] not in searches if row.get('record_kind') == 'literature_search' else
            not _is_complete(review_excerpts.find_identity(row), spans[review_excerpts.find_identity(row)], whole, parts))]
        if missing:
            raise ResearchError('openness_reading_incomplete', 'Every v5 unit and reconstructed original search '
                                'must be delivered complete', {'units': missing})
    return {'turns': [observation['turn'] for observation in observations], 'searches': searches,
            'search_receipts': receipts, 'units': units}


def verify_chain(records, artifacts, assignment):
    """The turns of a final turn's reading chain, each with the units whose whole body, and the parts whose exact
    slice, its actual recorded request delivered complete; the last entry is the final turn itself. Every turn must be
    ready, the final turn's reading record must name the observed attempts of the turns before it, and every unit that
    the packet requires must have reached reading turns complete, whole or in all of its parts, or be complete in the
    final turn's own request. A math chain must also keep its binding and rebuild from its observed outputs, as
    _require_math_continuity states. A final turn that 0.64.2 to 0.66.2 built is verified by the rules of those
    releases."""
    packet = json.loads(artifacts.read(assignment['packet']))
    if packet[RECORD_KEY]['protocol'] in (PROTOCOL_V5, PROTOCOL_V6):
        return verify_problem_coverage(records, artifacts, assignment, final=True)['turns']
    if packet[RECORD_KEY]['protocol'] not in GENERIC_UNIT_PROTOCOLS:
        return _verify_legacy_chain(records, artifacts, assignment)
    from .native_review import load_packet
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    rows = list_reading_rows(records, artifacts, native, review=assignment)
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
