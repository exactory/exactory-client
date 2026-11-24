"""Canonical native mathematical review packets, without strategic-value gates."""

from datetime import date
import json
import re
import unicodedata

from .artifacts import validate_reference
from .errors import ResearchError, describe_error_at
from .evidence import digest
from .operations import fields, text

PROTOCOL = "native-math-review-v1"
ATTESTATION_PREFIX = "review-assignment:"
KINDS = {"proposal", "result", "recovery"}
LEGACY_PROMPT = (
    "Assess the exact native mathematical subject and its supplied immutable evidence. "
    "The original mathematical objective, proof policy, finite computation obligations and resource rules remain binding. "
    "This task has no strategic-value, intent, dossier or bar prerequisites. "
    "Previously recorded scientific strategy assessments, failures and accepted evidence are deliberately supplied "
    "for continuity; this is not a blind assessment of that scientific history. Do not treat them as proof by authority. "
    "execution_provenance identifies native-selected verifier binaries whose bytes are checked by execution; "
    "their machine code is not supplied as scientific text. Scientific input artifacts remain in evidence. "
    "Use the exact native.subject_digest, native.claim_digest and native.reviewer values in the response. "
    "For native.kind=proposal return exactly {schema_version:1,subject_digest,claim_digest,reviewer,"
    "decision:approve|revise|reject,findings:{root_connection,mathematical_substance,assumptions,scope,"
    "equivalence,necessity,novelty,significance,renewal_basis},unresolved_objections:[{blocking,description}]}. "
    "Findings are strings; inapplicable proposal findings may be empty. "
    "For native.kind=result, an approval is exactly {subject_digest,claim_digest,reviewer,decision:approve,"
    "findings:{statement,assumptions,scope,dependencies,policy}}, with all five findings nonempty. "
    "For native.kind=recovery, an approval is exactly {schema_version:1,subject_digest,reviewer,decision:approve,"
    "findings:{read_only_origin,delta,authority,preservation,quiescence},unresolved_objections:[]}. "
    "A result or recovery which cannot be approved must instead return {decision:revise|reject,reason,objections}; "
    "that response is retained as an operational scientific assessment and cannot authorize the native transition. "
    "Do not invent evidence access, certify omitted dependencies, or transform an unsupported finding into approval."
)
OPENNESS_PROMPT = LEGACY_PROMPT.replace(
    "For native.kind=result, an approval is exactly {subject_digest,claim_digest,reviewer,decision:approve,"
    "findings:{statement,assumptions,scope,dependencies,policy}}, with all five findings nonempty. ",
    "For native.kind=result, an approval is exactly {schema_version:2,subject_digest,claim_digest,reviewer,decision:approve,"
    "findings:{statement,assumptions,scope,dependencies,policy,openness}}, with all findings nonempty. "
    "openness is exactly {outcome:supported_open|already_solved|unresolved,statement_digest,evidence,searched_at,coverage,reason}. "
    "statement_digest equals native.claim_digest, binding the complete exact statement, quantifiers, assumptions and scope. "
    "evidence is a nonempty list of delivered evidence digests for the novelty study and its shared search and reading records. "
    "searched_at is the YYYY-MM-DD search date; coverage records searches for the exact statement and each neighbouring formulation, "
    "with each query's date, sources, depth and top-k versus complete coverage. reason explains the finding. "
    "Fully read every decisive prior or settling source; an unavailable decisive source remains a gap, gives no reading credit "
    "and keeps the outcome unresolved. already_solved must cite the fully read settling source in evidence "
    "and explain it in reason; it never cancels requested formal verification or other deliverables. "
    "unresolved may approve a proof whose claim excludes openness, but cannot approve a claim that the statement was open. "
    "For root closure (native.subject has objective_id and acceptance_ids), unresolved cannot approve: "
    "every math-solver objective, including a request to prove or disprove the proposition, needs supported_open or already_solved. "
    "Proof validity can be approved separately while this openness milestone and root closure remain pending. "
    "Material new settling evidence requires reassessment in a new observed review bound to that exact evidence before closure. "
)


MINIMUM_PROMPT = OPENNESS_PROMPT.replace(
    'openness is exactly {outcome:supported_open|already_solved|unresolved,statement_digest,evidence,searched_at,coverage,reason}. ',
    'openness has '
    '{outcome:supported_open|already_solved|unresolved,statement_digest,evidence,searched_at,coverage,reason}, '
    'plus minimum when supplied in native.scientific_context.openness_minimum. minimum is exactly '
    '{schema_version:1,statement,neighbours,searches:[{formulation,search_digest}],reading_digests,settling_work_id}. '
    'Return that exact supplied minimum; do not invent search or reading records. Resolved outcomes require '
    'it: each exact statement and declared neighbour needs a common literature_search with captured_at and '
    'coverage, bound by search_digest in evidence; every relevant or contradictory result needs a bound '
    'complete fulltext reading. already_solved also names its fully read settling work in settling_work_id. '
    'Missing, unavailable or deferred sources, unspecified search coverage and integrity gaps permit only '
    'unresolved. '
)

RESOLUTIONS_PROMPT = MINIMUM_PROMPT.replace(
    '{schema_version:1,statement,neighbours,searches:[{formulation,search_digest}],reading_digests,settling_work_id}. ',
    '{schema_version:1,statement,neighbours,searches:[{formulation,search_digest}],reading_digests,settling_work_id}, '
    'optionally with duplicate_resolutions:[{search_digest,work_id,canonical_work_id}]. '
    'Keep earlier uncertain hits and their explicit later resolutions in the bound searches. '
    'An out_of_scope resolution removes the uncertain hit\'s reading obligation; duplicate_resolutions '
    'transfers it to the named relevant or contradictory canonical source in that resolving search. '
    'The canonical source needs a complete fulltext reading; a duplicate without that binding remains a gap. '
)

STRATEGY_PROMPT = RESOLUTIONS_PROMPT + (
    "The result review type is also used for policy and strategy subjects; identify the actual subject before judging it. "
    "A native.subject containing context_digest, assessments, plan_bindings, strategy_order, next_hypotheses and "
    "remaining_obligation_ids is a StrategyAssessment. Its subject_digest binds that assessment, while claim_digest "
    "identifies the original objective claim whose unresolved obligations must be preserved. "
    "For this subject, judge whether the proposed dispositions follow from the supplied current context, studies, "
    "retained failures, plan bindings and next hypotheses. Check that each continuing strategy is justified, "
    "that a failed method has concrete changed inputs before reuse, and that the proposed execution order and "
    "success and failure criteria can advance the remaining obligations within the recorded resource rules. "
    "A justified StrategyAssessment may be approved while the original theorem and openness remain unresolved. "
    "Such approval does not accept a proof, establish openness, renew a budget or close an obligation. "
    "Do not demand a proof of the original theorem as a prerequisite for approving a justified research step. "
    "Use the same required result findings to explain the assessment's statement, assumptions, scope, dependencies "
    "and policy; give the openness finding supported by the delivered evidence, using unresolved when necessary. "
    "This distinction does not relax any proof checkpoint, computation, acceptance or root-closure requirement. "
)

# Codex normalizes outer whitespace when loading its instruction file. Keep the
# prior prompt exact for historical records; fresh prompts must survive that load.
TRIMMED_STRATEGY_PROMPT = STRATEGY_PROMPT.rstrip()
PROMPT = TRIMMED_STRATEGY_PROMPT + (
    " For every native result review, findings.statement, findings.assumptions, findings.scope, "
    "findings.dependencies and findings.policy must each be a nonempty JSON string. "
    "findings.openness.coverage and findings.openness.reason must each be a nonempty JSON string, "
    "not an object or an array. Keep structured search details inside those strings when needed."
)

FINDING_INVALID_MESSAGE = (
    "This answer is not the openness finding that the prompt of this assignment describes. details.rule names "
    "the rule that the answer breaks, details.path the place in the answer, and details.forecast the forecast of "
    "the packet. Return the finding in the stated form, with an outcome that the forecast states as possible, or "
    "with the outcome unresolved.")
# The kinds of ground, each with what the reviewer holds (rendered into X1) and the next step of the author
# (reported with a recorded unresolved finding).
GROUND_RULES = {
    "reviewer_gap": dict(
        description=("The delivered evidence lacks something that the judgment needs and that the gaps of the "
                     "forecast do not list: for example a work that no reading of the packet read in full, a "
                     "search that is too narrow for a formulation, or a source that could not be read. text names "
                     "what is missing."),
        next=("Repair what the ground names with search, fulltext or import-fulltext, and reading. Then export a "
              "new packet with export --kind problem-openness.")),
    "source_settles": dict(
        description=("A decisive work of the packet, read in full, proves or refutes the statement, and the "
                     "forecast does not state already_solved as possible. work_id is required and is one of "
                     "native.scientific_context.decisive_work_ids. text states the result and where the work "
                     "proves it."),
        next=("Repair the gaps of the finding. Then export a new packet with this work as "
              "minimum.settling_work_id, or record a new statement with problem-statement.")),
    "settling_source_rejected": dict(
        description=("The declared settling work, openness_minimum.settling_work_id, does not settle the "
                     "statement. text states why."),
        next="Export a new packet without this settling work."),
    "own_argument": dict(
        description=("An argument of your own, which no source of the packet holds, proves or refutes the "
                     "statement. text states the argument, or turns names the reading turns whose notes hold it."),
        next=("Check the argument, for example in a solver objective, whose accepted result an export can bind "
              "under minimum.results. Or record a new statement with problem-statement.")),
    "packet_defect": dict(
        description=("The packet lacks or contradicts something that a finding needs. text names the defect."),
        next="Record the packet defect in the harness-improvement records. Then export a new packet."),
}
GROUND_NEXT_STEPS = {kind: rule["next"] for kind, rule in GROUND_RULES.items()}
# Both the validator and X1 read these rows in order.
FINDING_RULES = (
    dict(number=1, outcome="already_solved", forecast="already_solved", grounds="empty",
         condition=("audit_forecast.outcomes.already_solved is \"possible\", and you judge that the declared "
                    "settling work, read in full, settles the statement")),
    dict(number=2, outcome="supported_open", forecast="supported_open", grounds="empty",
         condition=("audit_forecast.outcomes.supported_open is \"possible\", and you hold none of the grounds "
                    "below")),
    dict(number=3, outcome="unresolved", forecast=None, grounds="present_or_both_blocked",
         condition="neither rule 1 nor rule 2 applies"),
)
# What each grounds value of FINDING_RULES asks of the answer, in the words of the prompt.
FINDING_GROUNDS_TEXT = {
    "empty": "grounds is the empty list",
    "present_or_both_blocked": ("grounds holds every ground that you hold, and it can be empty only when the "
                                "forecast states both outcomes as blocked"),
}


def render_finding_rule(row):
    return "Rule {number}. When {condition}, return the outcome {outcome}; {text}.".format(
        text=FINDING_GROUNDS_TEXT[row["grounds"]], **row)


FINDING_V2_TEXT = (
    '\n\nFinding rules version 2. A reviewer_gap is one unread work or one search that was not run. '
    'It holds kind, text, formulation, basis, need, unsettled, and exactly one of work or search. '
    'formulation is the exact statement or a declared neighbour. basis is a nonempty list of quotations '
    '{the identity fields of an excerpt, text}, each at least 20 normalized characters, copied from a verified '
    'excerpt or delivered passage, or from delivered evidence in a review of one request. '
    'need states the question about this formulation that the judgment needs answered. unsettled states why '
    'no bound reading, query or recorded search judgment answers it; explain why any exclusion reason fails. '
    'work is {title, identifiers?}; it names a work in the basis and no work already read. '
    'search is {terms, missing_term}; missing_term occurs in order in a quotation and has a content token '
    'that occurs in no bound query of this formulation. Reordering, case and punctuation do not make a gap. '
    'Tokens use NFKC, case folding and splitting at every nonletter and nondigit. '
    'finding_index.searches gives record_id, search_digest, formulations and digest references to queries, scope, '
    'dispositions, resolved and row_dispositions where present; finding_index.fields holds each value once under its '
    'digest. When the index is too long for the fixed part of a request, its longest values other than queries are '
    'not in fields, and finding_index.units gives for each, under the same digest, {unit, characters}: the handle of '
    'the body that holds the value, and its length. A passage request for that handle returns text of the body. When '
    'finding_index is {unit, characters, searches, read_works}, the whole index is the content of the body with that '
    'handle, which an earlier turn delivered. The checks use every bound query and every read work in each case. '
    'finding_index.read_works lists reading_id, reading_digest, version_id, identifiers and title. '
    'A matching normalized identifier or tokenized title means that the work is already read. '
    'The checks run in this order: gap_fields, gap_formulation, gap_basis, gap_work_read, '
    'gap_term_delivered, gap_term_bound. An index, database, language, period or unpublished literature '
    'that was not reached is a coverage limit unless delivered text supplies the named work or term and need. '
    'State limits in coverage. With supported_open possible and no ground, return supported_open. '
    'For a final over provider-refused unread ranges, cite the complete unread set in a packet_defect ground; '
    'a reviewer_gap still needs every named-gap field and check. '
    'Other kinds of ground and the three outcome rules keep their fields and meaning.')
# Function words and query operators are one fixed vocabulary for every term and query.
GAP_FUNCTION_WORDS = frozenset(('a an and are as at be been being by for from has have in into is it its '
    'of on or that the their then there these this those to was were which with without not all any '
    'title abstract author authors doi arxiv site intitle inurl filetype').split())


def find_finding_version(assignment):
    """Select the contract recorded by the immutable assignment prompt."""
    return (assignment or {}).get('prompt', {}).get('parameters', {}).get('finding_version', 1)


def tokenize_gap_text(value):
    """Reduce a title, query or term to ordered Unicode letter/digit tokens."""
    value = unicodedata.normalize('NFKC', value).casefold()
    return ''.join(character if character.isalpha() or character.isdigit() else ' '
                   for character in value).split()


def normalize_gap_identifier(value):
    """Compare DOI and arXiv spellings independently of prefixes and arXiv versions."""
    value = unicodedata.normalize('NFKC', value).strip().casefold()
    value = re.sub(r'^(?:https?://)?(?:dx\.)?doi\.org/|^doi:\s*', '', value)
    value = re.sub(r'^(?:https?://)?arxiv\.org/(?:abs|pdf)/|^arxiv:\s*', '', value)
    value = re.sub(r'\.pdf$', '', value)
    return re.sub(r'v\d+$', '', value) if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})v?\d*', value) else value


def render_finding_prompt(finding_version=1):
    """Render X1 from the same ordered rules and ground vocabulary used for validation."""
    rendered = (
        "This review answers one question: what does the delivered evidence establish about whether the exact "
        "statement in native.subject is open? Your answer is a finding. It is no judgment of the worth of the "
        "problem, and it gives no permission to post it. An unresolved finding that states its grounds is a "
        "complete and correct answer.\n\n"
        "Return exactly {schema_version:3,subject_digest,claim_digest,reviewer,findings:{statement,assumptions,"
        "scope,dependencies,policy,openness:{outcome,coverage,reason,grounds:[{kind,text,work_id?,turns?}]}}}. "
        "Copy subject_digest, claim_digest and reviewer exactly from native. findings.statement, "
        "findings.assumptions, findings.scope, findings.dependencies and findings.policy are nonempty strings: "
        "your assessment of the statement as it is written, of its assumptions, of its scope, of the results and "
        "sources that the judgment depends on, and of how the delivered evidence meets the rules of this prompt. "
        "outcome is supported_open, already_solved or unresolved. coverage is a nonempty string, or a list with "
        "exactly one entry for the statement and one for each neighbour of "
        "native.scientific_context.openness_minimum; it states for each formulation which searches covered it, "
        "when, and how far. reason is a nonempty string that explains the outcome. grounds is a list.\n\n"
        "native.scientific_context.audit_forecast holds what the harness computed from the records when it built "
        "this packet. outcomes states for supported_open and for already_solved whether the harness can record "
        "that outcome: \"possible\" or \"blocked\". gaps, which native.scientific_context.minimum_gaps repeats, "
        "lists each condition that blocks an outcome, with the outcomes that it blocks, the search and the work "
        "that it concerns, and details. notices lists facts that block no outcome, such as the coverage of each "
        "search, responses that hold fewer results than the registry reports, and rows that an import rule leaves "
        "out. searched_through is the date of the latest capture among the bound searches. You cannot remove a "
        "gap: the harness refuses an outcome that the forecast states as blocked, whatever the evidence shows. "
        "Your own assessment of the delivered evidence can add grounds. The harness adds the statement digest, the "
        "evidence list, the search date and the minimum to the recorded finding, so the answer holds none of "
        "them.\n\n"
        "The outcome follows three rules, read in order. "
        + " ".join(render_finding_rule(row) for row in FINDING_RULES)
        + "\n\nA ground is {kind,text}, with work_id where its kind asks for it and with optional turns. text is a "
        "nonempty string. kind is one of the following. "
        + " ".join(kind + ": " + rule["description"] for kind, rule in GROUND_RULES.items())
        + " turns is a list of distinct assignment ids of reading turns of this chain, taken from "
        "reading_record.turns, whose notes hold the detail of the ground, so that the ground does not repeat "
        "them. An argument that you form in the final turn stands in text. A review of one turn has no reading "
        "turns, so its grounds hold no turns."
    )
    return rendered + (FINDING_V2_TEXT if finding_version == 2 else '')


FINDING_V2_PAGE_TEXT = (
    '\n\nFinding rules version 2. Under these rules a reviewer_gap is one unread work or one search that was not '
    'run. It holds kind, text, formulation, basis, need, unsettled and exactly one of work or search, with optional '
    'refs. formulation is {ref:<handle>} with the handle of the target of its row; this is the one place outside '
    'refs where this answer writes ref. The harness puts the exact text of the formulation in its place and does '
    'not count it in the limits of the row. basis is a list of one to eight quotations {unit,text}: unit is the '
    'member unit of an excerpt or a passage answer of reading_final.entries, a u handle, and text copies at least '
    '20 consecutive characters of the text of that entry. need states the question about this formulation that the '
    'judgment needs answered. unsettled states why no bound reading, query or recorded search judgment answers it; '
    'explain why any exclusion reason fails. work is {title,identifiers?}; it names a work in the basis and no work '
    'already read. search is {terms,missing_term}; missing_term occurs in order in a quotation and has a content '
    'token that occurs in no bound query of this formulation. Reordering, case and punctuation do not make a gap. '
    'Tokens use NFKC, case folding and splitting at every nonletter and nondigit. A refusal names the rule that '
    'failed in details.rule: gap_fields, gap_formulation, gap_basis, gap_work_read, gap_term_delivered or '
    'gap_term_bound. An index, a database, a language, a period or unpublished literature that was not reached is a '
    'limit of coverage unless an entry supplies the named work or term and the need: state such a limit in '
    'coverage, and give no ground for it. Other kinds of ground keep their fields and meaning.\n\n'
    'finding_index holds the bound searches of the targets of this page. finding_index.searches gives record_id, '
    'search_digest, formulations and digest references to queries, scope, dispositions, resolved and '
    'row_dispositions where present, and finding_index.fields holds each value once under its digest. Where the '
    'index is too long, its longest values other than queries are not in fields, and finding_index.units gives for '
    'each, under the same digest, {unit,characters}: the handle of the body that holds the value, and its length. A '
    'passage request for that handle returns text of the body. finding_index.read_works lists reading_id, '
    'reading_digest, version_id, identifiers and title of every read work. A matching normalized '
    'identifier or tokenized title means that the work is already read. When finding_index is '
    '{unit,characters,searches,read_works}, the body with that handle holds the index of every bound search.')
FINDING_V2_CLOSING_TEXT = (
    '\n\nFinding rules version 2. Under these rules a reviewer_gap is one unread work or one search that was not '
    'run. It holds kind, text, formulation, basis, need, unsettled and exactly one of work or search, and the pages '
    'state such gaps: a row whose kinds hold reviewer_gap carries one. In a reviewer_gap of this answer, '
    'formulation is the exact text of the statement or of a neighbour, and basis quotes retained entries as '
    '{unit,text}. Without a retained entry in this turn to quote, the answer adds no reviewer_gap of its own. A '
    'limit of the literature coverage that names no work and no term is no ground: the pages state such limits in '
    'coverage, and they do not keep rule 2 from applying. Other kinds of ground and the three outcome rules keep '
    'their fields and meaning. finding_index holds, or names by a unit, the index of the bound searches and of the '
    'read works of the chain, against which the harness checks a reviewer_gap.')


def find_finding_v2_text(stage):
    """Select the addition for the fields the current stage can answer."""
    if stage == 'page':
        return FINDING_V2_PAGE_TEXT
    if stage == 'closing':
        return FINDING_V2_CLOSING_TEXT
    return FINDING_V2_TEXT


PROBLEM_OPENNESS_PROMPT = render_finding_prompt()


def uses_finding_contract(assignment):
    """The saved prompt, rather than today's packet shape, selects the answer contract."""
    template = assignment.get("prompt", {}).get("template_id", "")
    return template in ("exactory-openness-reading-v6/final", "exactory-openness-reading-v7/final",
                        "exactory-review-v7/native_math", "exactory-openness-reading-v7/closing",
                        "exactory-openness-reading-v7/page")


def _require_finding(condition, rule, path, forecast):
    if not condition:
        raise ResearchError("openness_finding_invalid", FINDING_INVALID_MESSAGE,
                            dict(rule=rule, path=path, forecast=forecast))


# The three rules of a final over unread ranges (reading_final.unread). Each refusal reaches the reviewer in the
# refusal notice of the replacement, so each text states the rule and the correction.
UNREAD_OUTCOME_MESSAGE = (
    'This final turn holds unread ranges under reading_final.unread, so the harness records no outcome other than '
    'unresolved. Return the finding with the outcome unresolved.')
UNREAD_SET_REFERENCE_MESSAGE = (
    'A finding over unread ranges needs a ground of the kind reviewer_gap whose refs holds {ref:"x1"}, the citation '
    'of the complete set of unread ranges. Add that ground.')
UNREAD_SET_V2_MESSAGE = (
    'A finding over unread ranges needs a packet_defect ground citing the complete unread set in refs. '
    'A named reviewer_gap with that citation is also valid when it passes all six gap rules.')
UNREAD_UNIT_REFERENCE_MESSAGE = (
    'A ground of this finding cites a unit that does not match a row of the unread set: its u handle is not the unit '
    'of a row, or its artifact is not the artifact of that row. In a '
    'finding over unread ranges a u handle cites an unread unit: take it from the member unit of a row of '
    'reading_final.unread or of a page of the list unread, or remove the citation.')


def _list_gap_basis(packet):
    """List only quotations whose delivery or excerpt check the packet records."""
    record = packet.get('reading_record', {})
    result = [row for key, status in (('excerpts', 'verified'), ('passages', 'delivered'))
              for row in record.get(key, []) if row.get('status') == status]
    result.extend(row for row in packet.get('reading_final', {}).get('entries', [])
                  if row.get('status') in ('verified', 'delivered'))
    if not record and 'reading_final' not in packet:
        from . import review_excerpts
        for row in packet.get('evidence', []):
            if row.get('encoding') != 'utf-8' or not isinstance(row.get('content'), str):
                continue
            identity = row.get('identity', {})
            if not identity and isinstance(row.get('label'), dict):
                identity = row['label']
            # Evidence metadata names full texts and store records in ordinary native packets.
            identities = [identity] if identity else []
            for field in ('version_id', 'source_id', 'record_id', 'native_attempt_digest'):
                if field in row:
                    identities.append(dict(review_excerpts.find_identity(row)[1:]))
                    break
            for value in identities:
                result.append(dict(value, text=row['content']))
    return result


def accepts_unread_ground(ground, finding_version):
    """Keep a provider delivery defect separate from an unsupported literature gap."""
    return ground.get('kind') in (('reviewer_gap', 'packet_defect') if finding_version == 2 else ('reviewer_gap',))


def _matches_gap_basis(quotation, delivered):
    from . import review_excerpts
    if 'unit' in quotation:
        return set(quotation) == {'unit', 'text'} and quotation['unit'] == delivered.get('unit')
    required, optional = review_excerpts.find_identity_fields(quotation, review_excerpts.IDENTITY_FIELDS)
    return (set(required) <= set(quotation) and set(quotation) <= set(required + optional) | {'text'}
        and all(quotation[key] == delivered.get(key) for key in required + optional if key in quotation))


def _validate_named_gap(packet, ground, path, require):
    """Apply the six mechanical named-gap rules in their specified order."""
    from .review_excerpts import normalize_with_offsets
    required = {'kind', 'text', 'formulation', 'basis', 'need', 'unsettled'}
    allowed = required | {'turns', 'refs', 'work', 'search'}
    work, search = ground.get('work'), ground.get('search')
    valid = (required <= set(ground) <= allowed and ('work' in ground) != ('search' in ground)
        and all(_nonempty(ground[key]) for key in ('text', 'formulation', 'need', 'unsettled')))
    if work is not None:
        valid = valid and (isinstance(work, dict) and {'title'} <= set(work) <= {'title', 'identifiers'}
            and _nonempty(work['title']) and ('identifiers' not in work or isinstance(work['identifiers'], list)
                and all(_nonempty(value) for value in work['identifiers'])))
    elif search is not None:
        valid = valid and (isinstance(search, dict) and set(search) == {'terms', 'missing_term'}
            and _nonempty(search['missing_term']) and (_nonempty(search['terms'])
                or isinstance(search['terms'], list) and bool(search['terms'])
                and all(_nonempty(value) for value in search['terms'])))
    else:
        valid = False
    require(valid, 'gap_fields', path)
    minimum = packet['native']['scientific_context']['openness_minimum']
    require(ground['formulation'] in [minimum['statement']] + minimum['neighbours'], 'gap_formulation', path + '/formulation')
    basis = ground['basis']
    sources = _list_gap_basis(packet)
    require(isinstance(basis, list) and bool(basis) and all(isinstance(quote, dict)
        and _nonempty(quote.get('text')) and len(normalize_with_offsets(quote['text'])[0]) >= 20
        and any(_matches_gap_basis(quote, row) and isinstance(row.get('text'), str)
            and normalize_with_offsets(quote['text'])[0] in normalize_with_offsets(row['text'])[0]
            for row in sources) for quote in basis), 'gap_basis', path + '/basis')
    index = packet['finding_index']
    if work is not None:
        identifiers = {normalize_gap_identifier(value) for value in work.get('identifiers', [])}
        title = tokenize_gap_text(work['title'])
        require(not any(title == tokenize_gap_text(row['title']) or identifiers.intersection(
            normalize_gap_identifier(value) for value in row['identifiers']) for row in index['read_works']),
            'gap_work_read', path + '/work')
    if search is not None:
        term = tokenize_gap_text(search['missing_term'])
        quotes = [tokenize_gap_text(row['text']) for row in basis]
        require(bool(term) and any(any(tokens[start:start + len(term)] == term
            for start in range(len(tokens) - len(term) + 1)) for tokens in quotes),
            'gap_term_delivered', path + '/search/missing_term')
        queries = [query for row in index['searches'] if ground['formulation'] in row['formulations']
                   for query in index['fields'].get(row.get('queries'), [])]
        tokens = {token for query in queries for token in tokenize_gap_text(query)}
        require(bool(set(term) - GAP_FUNCTION_WORDS - tokens), 'gap_term_bound', path + '/search/missing_term')


def validate_finding(packet, output, *, finding_version=1, artifacts=None):
    """Check a v3 answer using only its immutable assignment packet and output."""
    from .review_refusal import contract_check
    native = packet.get("native", packet)
    context = native["scientific_context"]
    forecast = context["audit_forecast"]
    if finding_version == 2:
        from .review_search_delivery import validate_finding_index
        validate_finding_index(packet.get('finding_index'))
    v7 = packet.get('reading_final', {}).get('protocol') == 'exactory-openness-reading-v7'
    with contract_check("openness.finding"):
        def require(condition, rule, path):
            _require_finding(condition, rule, path, forecast)

        require(isinstance(output, dict) and set(output) == {
            "schema_version", "subject_digest", "claim_digest", "reviewer", "findings"}, "answer_fields", "")
        require(type(output["schema_version"]) is int and output["schema_version"] == 3,
                "schema_version", "/schema_version")
        for key in ("subject_digest", "claim_digest", "reviewer"):
            require(output[key] == native[key], "binding", "/" + key)
        findings = output["findings"]
        require(isinstance(findings, dict) and set(findings) == set(RESULT_FINDINGS) | {"openness"},
                "finding_fields", "/findings")
        for key in RESULT_FINDINGS:
            require(_nonempty(findings[key]), "nonempty_text", "/findings/" + key)
        openness = findings["openness"]
        base = "/findings/openness"
        require(isinstance(openness, dict) and set(openness) == {"outcome", "coverage", "reason", "grounds"},
                "openness_fields", base)
        require(_nonempty(openness["reason"]), "nonempty_text", base + "/reason")
        coverage = openness["coverage"]
        minimum = context["openness_minimum"]
        target_count = len(packet['reading_final']['page']['targets']) if v7 else 1 + len(minimum['neighbours'])
        require(_nonempty(coverage) or isinstance(coverage, list)
                and len(coverage) == target_count, "coverage", base + "/coverage")
        if v7:
            require(len(openness['reason']) <= 4000, 'reason_bound', base + '/reason')
            require(all(len(json.dumps(row, ensure_ascii=False, separators=(',', ':'))) <= 800 for row in coverage)
                    if isinstance(coverage, list) else len(coverage) <= 800 * target_count,
                    'coverage_bound', base + '/coverage')
        grounds = openness["grounds"]
        require(isinstance(grounds, list), "grounds_list", base + "/grounds")
        turns = {row["assignment_id"] for row in packet.get("reading_record", {}).get("turns", [])
                 if row.get("stage") != "final"}
        for index, ground in enumerate(grounds):
            path = base + "/grounds/" + str(index)
            gap = finding_version == 2 and isinstance(ground, dict) and ground.get('kind') == 'reviewer_gap'
            if gap:
                _validate_named_gap(packet, ground, path, require)
            require(isinstance(ground, dict) and {"kind", "text"} <= set(ground)
                    and set(ground) <= ({"kind", "text", "work_id", "refs"} if v7
                                        else {"kind", "text", "work_id", "turns"}) | (
                        {'formulation', 'basis', 'need', 'unsettled', 'work', 'search', 'turns'} if gap else set()),
                    "ground_fields", path)
            require(isinstance(ground["kind"], str) and ground["kind"] in GROUND_NEXT_STEPS, "ground_kind", path + "/kind")
            require(_nonempty(ground["text"]), "nonempty_text", path + "/text")
            if v7:
                require(len(ground['text']) <= 600, 'ground_bound', path + '/text')
            if 'refs' in ground:
                references = ground['refs']
                require(isinstance(references, list), 'recorded_references', path + '/refs')
                for reference in references:
                    validate_reference(reference)
            if "work_id" in ground:
                require(_nonempty(ground["work_id"]), "work_id", path + "/work_id")
            if ground["kind"] == "source_settles":
                require(ground.get("work_id") in context["decisive_work_ids"], "decisive_work", path + "/work_id")
            if "turns" in ground:
                references = ground["turns"]
                require(isinstance(references, list) and all(isinstance(item, str) for item in references)
                        and len(set(references)) == len(references) and set(references) <= turns,
                        "reading_turns", path + "/turns")
        unread = packet.get('reading_final', {}).get('unread') if v7 else None
        if unread is not None:
            def require_unread(condition, rule, message, path):
                if not condition:
                    raise ResearchError('openness_finding_invalid', message,
                                        dict(rule=rule, path=path, forecast=forecast))
            require_unread(openness['outcome'] == 'unresolved', 'unread_outcome',
                           UNREAD_OUTCOME_MESSAGE, base + '/outcome')
            require_unread(any(accepts_unread_ground(ground, finding_version)
                and unread['artifact'] in ground.get('refs', []) for ground in grounds),
                'unread_set_reference', UNREAD_SET_V2_MESSAGE if finding_version == 2 else UNREAD_SET_REFERENCE_MESSAGE,
                base + '/grounds')
            rows = json.loads(artifacts.read(unread['artifact']))['rows']
            units = {row['unit']: row['artifact'] for row in rows}
            require_unread(all(reference['unit'] in units and
                {key: value for key, value in reference.items() if key != 'unit'} == units[reference['unit']]
                for ground in grounds for reference in ground.get('refs', []) if 'unit' in reference),
                'unread_unit_reference', UNREAD_UNIT_REFERENCE_MESSAGE, base + '/grounds')
        outcomes = forecast["outcomes"]
        rule = next((row for row in FINDING_RULES if row["outcome"] == openness["outcome"]), None)
        require(rule is not None, "outcome", base + "/outcome")
        if rule["grounds"] == "empty":
            accepted = outcomes[rule["forecast"]] == "possible" and not grounds
        else:
            accepted = bool(grounds) or all(value == "blocked" for value in outcomes.values())
        require(accepted, rule["number"], base + "/outcome")


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ResearchError("invalid_native_review_packet", "Native evidence needs exact SHA-256 bindings")


def load_packet(artifacts, reference, reviewer_id):
    """Validate the portable envelope; native ingress verifies its source state."""
    from .workspace import strict_json
    value = strict_json(artifacts.read(reference))
    fields(value, ("protocol", "kind", "root", "objective_id", "contract", "subject", "subject_digest",
                   "claim_digest", "reviewer", "scientific_context", "evidence", "execution_provenance", "unmaterialized_digests"))
    if value["protocol"] != PROTOCOL or value["kind"] not in KINDS:
        raise ResearchError("invalid_native_review_packet", "Unknown native review contract")
    for key in ("root", "objective_id"):
        text(value[key], "Native " + key)
    if not isinstance(value["subject"], dict) or digest(value["subject"]) != value["subject_digest"]:
        raise ResearchError("invalid_native_review_packet", "Native subject bytes differ from their digest")
    if not isinstance(value["contract"], dict) or not isinstance(value["scientific_context"], dict):
        raise ResearchError("invalid_native_review_packet", "Native objective and scientific context must be supplied")
    _sha(value["subject_digest"])
    if value["kind"] == "recovery":
        if value["claim_digest"] is not None:
            raise ResearchError("invalid_native_review_packet", "Recovery uses its original schema without a claim digest")
    else:
        _sha(value["claim_digest"])
    provenance = value["reviewer"]
    fields(provenance, ("source", "actor_id", "attestation_id"))
    if (provenance["source"] != "host" or provenance["actor_id"] != reviewer_id
            or not isinstance(provenance["attestation_id"], str)
            or not provenance["attestation_id"].startswith(ATTESTATION_PREFIX)
            or not provenance["attestation_id"][len(ATTESTATION_PREFIX):]):
        raise ResearchError("invalid_native_review_packet", "Use the exact observed assignment identity")
    seen = set()
    if not isinstance(value["evidence"], list) or not isinstance(value["unmaterialized_digests"], list):
        raise ResearchError("invalid_native_review_packet", "Native evidence closure must be an explicit inventory")
    for entry in value["evidence"]:
        fields(entry, ("digest", "kind", "artifact"))
        _sha(entry["digest"])
        validate_reference(entry["artifact"])
        if (entry["kind"] not in ("blob", "artifact") or entry["digest"] != entry["artifact"]["sha256"]
                or entry["digest"] in seen):
            raise ResearchError("invalid_native_review_packet", "Native evidence is duplicated or identifies different bytes")
        seen.add(entry["digest"])
        exported = artifacts.read(entry["artifact"])
        if "raw_identities" in entry["artifact"]:
            originals = entry["artifact"]["raw_identities"]
            if not isinstance(originals, list) or not originals:
                raise ResearchError("invalid_native_review_packet", "Raw identities must name original JSON artifacts")
            normalized = digest(strict_json(exported))
            raw_digests = set()
            for original in originals:
                validate_reference(original)
                if original["sha256"] in raw_digests or digest(strict_json(artifacts.read(original))) != normalized:
                    raise ResearchError("invalid_native_review_packet", "Raw bytes do not establish the exported canonical identity")
                raw_digests.add(original["sha256"])
    for item in value["unmaterialized_digests"]:
        _sha(item)
    if not isinstance(value["execution_provenance"], list):
        raise ResearchError("invalid_native_review_packet", "Native executable provenance must be explicit")
    for item in value["execution_provenance"]:
        fields(item, ("path", "digest", "size", "role"))
        text(item["path"], "Native executable path")
        _sha(item["digest"])
        if (item["role"] != "native_verifier_executable" or type(item["size"]) is not int
                or item["size"] < 1 or item["digest"] in seen):
            raise ResearchError("invalid_native_review_packet", "Native executable provenance is invalid or duplicated")
        seen.add(item["digest"])
    if len(set(value["unmaterialized_digests"])) != len(value["unmaterialized_digests"]):
        raise ResearchError("invalid_native_review_packet", "Native unavailable references must be distinct")
    return value


def require_assignment_identity(packet, assignment):
    """The packet names the assignment, or the first turn of its reading or parts series."""
    first = assignment.get("reading_chain", {}).get("first_assignment_id",
        assignment.get("review_parts", {}).get("replaces_assignment_id",
            assignment.get("review_parts", {}).get("series_assignment_id", assignment["id"])))
    expected = {"source": "host", "actor_id": assignment["reviewer_id"],
                "attestation_id": ATTESTATION_PREFIX + first}
    if packet["reviewer"] != expected or assignment["dossier_id"] is not None:
        raise ResearchError("invalid_native_review_packet", "Native review binds one exact assignment without a dossier")


PROPOSAL_FINDINGS = ("root_connection", "mathematical_substance", "assumptions", "scope", "equivalence", "necessity",
                     "novelty", "significance", "renewal_basis")
RESULT_FINDINGS = ("statement", "assumptions", "scope", "dependencies", "policy")
RECOVERY_FINDINGS = ("read_only_origin", "delta", "authority", "preservation", "quiescence")


def _require(condition, message, *, path):
    if not condition:
        raise ResearchError("invalid_native_review", message, {"path": path})


def _closed(value, keys, message, *, path):
    _require(isinstance(value, dict) and set(value) == set(keys), message, path=path)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_objections(value, *, empty=False, path='/unresolved_objections'):
    # The refusal names the member that it checks: unresolved_objections of a proposal review, or objections of a
    # math nonapproval.
    member = path.rsplit('/', 1)[-1]
    entry = 'unresolved objection' if member == 'unresolved_objections' else 'objection'
    _require(isinstance(value, list) and (not empty or not value), member + " must be a list" +
             (" and empty in an approval" if empty else ""), path=path)
    for index, objection in enumerate(value):
        item_path = path + '/' + str(index)
        _closed(objection, ("blocking", "description"), "Each " + entry + " is exactly {blocking,description}",
                path=item_path)
        _require(type(objection["blocking"]) is bool,
                 "An objection needs a boolean blocking flag and a nonempty description", path=item_path + '/blocking')
        _require(_nonempty(objection["description"]),
                 "An objection needs a boolean blocking flag and a nonempty description", path=item_path + '/description')


def _validate_openness(openness, claim_digest, *, structured_coverage, path='/findings/openness'):
    _require(isinstance(openness, dict) and set(openness) - {"minimum"} == {
        "outcome", "statement_digest", "evidence", "searched_at", "coverage", "reason"},
        "openness is exactly {outcome,statement_digest,evidence,searched_at,coverage,reason}, with minimum when supplied",
        path=path)
    _require(openness["outcome"] in ("supported_open", "already_solved", "unresolved"), "Unknown openness outcome",
             path=path + '/outcome')
    _require(openness["statement_digest"] == claim_digest, "openness.statement_digest must equal native.claim_digest",
             path=path + '/statement_digest')
    evidence = openness["evidence"]
    _require(isinstance(evidence, list) and evidence and len(set(map(str, evidence))) == len(evidence)
             and all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) for item in evidence),
             "openness.evidence is a nonempty list of distinct SHA-256 digests", path=path + '/evidence')
    for key in ("searched_at", "reason"):
        _require(_nonempty(openness[key]), "searched_at and reason must be nonempty strings", path=path + '/' + key)
    # problem-openness records a problem record's coverage as given, also as a nonempty list or object.
    coverage = openness["coverage"]
    _require(_nonempty(coverage) or (structured_coverage and isinstance(coverage, (list, dict)) and bool(coverage)),
             "coverage must be a nonempty string" + (", list or object" if structured_coverage else ""),
             path=path + '/coverage')
    try:
        _require(date.fromisoformat(openness["searched_at"]).isoformat() == openness["searched_at"],
                 "openness.searched_at must be YYYY-MM-DD", path=path + '/searched_at')
    except ValueError as error:
        raise ResearchError("invalid_native_review", "openness.searched_at must be YYYY-MM-DD",
                            {"path": path + '/searched_at'}) from error
    if "minimum" in openness:
        from .openness_minimum import validate_minimum
        with describe_error_at(path + '/minimum'):
            validate_minimum(openness["minimum"])


def validate_output(packet, output):
    """Check an observed native review against the shape that its prompt states and the native controller records:
    the exact subject, claim and reviewer bindings of the packet, the decision vocabulary and the closed fields. A
    result or recovery that is not an approval is retained only as an assessment and authorizes nothing, so it needs
    only its decision and a reason."""
    _require(isinstance(output, dict), "A native review is one JSON object", path='')
    kind = packet["kind"]
    if kind != "proposal" and output.get("decision") != "approve":
        _require(output.get("decision") in ("revise", "reject"),
                 "A result or recovery that is not approved needs decision revise or reject and a nonempty reason",
                 path='/decision')
        _require(_nonempty(output.get("reason")),
                 "A result or recovery that is not approved needs decision revise or reject and a nonempty reason",
                 path='/reason')
        return
    _require(output.get("subject_digest") == packet["subject_digest"], "Use the exact native.subject_digest",
             path='/subject_digest')
    _require(output.get("reviewer") == packet["reviewer"], "Use the exact native.reviewer object", path='/reviewer')
    if kind == "proposal":
        _closed(output, ("schema_version", "subject_digest", "claim_digest", "reviewer", "decision", "findings",
                         "unresolved_objections"), "A proposal review has exactly the fields of the native Review", path='')
        _require(output["schema_version"] == 1 and type(output["schema_version"]) is int,
                 "Proposal reviews use schema_version 1", path='/schema_version')
        _require(output["claim_digest"] == packet["claim_digest"], "Use the exact native.claim_digest", path='/claim_digest')
        _require(output["decision"] in ("approve", "revise", "reject"), "Use decision approve, revise or reject", path='/decision')
        _closed(output["findings"], PROPOSAL_FINDINGS, "Proposal findings are exactly the nine native findings", path='/findings')
        for key in PROPOSAL_FINDINGS:
            _require(isinstance(output["findings"][key], str), "Proposal findings are strings", path='/findings/' + key)
        _validate_objections(output["unresolved_objections"])
    elif kind == "result":
        version = output.get("schema_version", 1)
        _require(type(version) is int and version in (1, 2), "Result reviews use schema_version 1 or 2", path='/schema_version')
        _closed(output, ("subject_digest", "claim_digest", "reviewer", "decision", "findings")
                + (("schema_version",) if "schema_version" in output else ()),
                "A result approval has exactly the fields of the native result review", path='')
        _require(output["claim_digest"] == packet["claim_digest"], "Use the exact native.claim_digest", path='/claim_digest')
        _closed(output["findings"], RESULT_FINDINGS + (("openness",) if version == 2 else ()),
                "Result findings are statement, assumptions, scope, dependencies and policy, with openness in version 2",
                path='/findings')
        for key in RESULT_FINDINGS:
            _require(_nonempty(output["findings"][key]),
                     "findings.statement, findings.assumptions, findings.scope, findings.dependencies and findings.policy "
                     "must each be a nonempty string; in schema_version 2, findings.openness is an object", path='/findings/' + key)
        if version == 2:
            _validate_openness(output["findings"]["openness"], packet["claim_digest"],
                               structured_coverage=packet["contract"].get("orchestration") == "problem_creation")
    else:
        _closed(output, ("schema_version", "subject_digest", "reviewer", "decision", "findings", "unresolved_objections"),
                "A recovery approval has exactly the fields of the native recovery review", path='')
        _require(output["schema_version"] == 1 and type(output["schema_version"]) is int,
                 "Recovery reviews use schema_version 1", path='/schema_version')
        _closed(output["findings"], RECOVERY_FINDINGS, "Recovery findings are exactly the five native findings", path='/findings')
        for key in RECOVERY_FINDINGS:
            _require(_nonempty(output["findings"][key]), "Every recovery finding is a nonempty string", path='/findings/' + key)
        _validate_objections(output["unresolved_objections"], empty=True)
