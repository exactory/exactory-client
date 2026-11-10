"""Ordered, versioned additions to problem reviewer prompts.

Append a new version beside the earlier bytes in a paragraph's versions tuple.
Only new assignments select the last version; recognition replays the recorded id.
The carry keeps its original envelope parameter and suffix for 0.82.0 replay.
"""

from dataclasses import dataclass
from typing import Callable, Tuple

from .errors import ResearchError
from .evidence import digest
from .operations import fields
from .problem_objection_carry import (PROMPT as CARRY_PROMPT, PROTOCOL as CARRY_PROTOCOL,
                                     PROMPT_PARAMETER as CARRY_PARAMETER)


PARAMETER = 'problem_paragraphs'
CONDITIONS = 'problem_prompt_conditions'
FORMING_RECORD_KINDS = ('forming_call', 'form_outcome')
INVALID_CONTEXT = 'Use admitted problem prompt paragraph versions once, in their registered order and roles'


def _has_carry(role, packet):
    return CARRY_PARAMETER in packet


def _has_accounts(role, packet):
    accounts = (packet.get('problem_evidence', {}).get('value') or {}).get('accounts')
    return (isinstance(accounts, dict) and bool(accounts)
            and (role != 'adjudicator' or packet.get('rubric_definition', {}).get('item') in
                 ('A3', 'priority', 'calibration', 'significance')))


def _has_forming_candidates(role, packet):
    return any(candidate.get('source', {}).get('kind') == 'phenomenon'
               or any(item.get('kind') == 'record' and item.get('record_kind') in FORMING_RECORD_KINDS
                      for item in candidate.get('source', {}).get('evidence', []))
               for candidate in packet.get('candidates', []))


def _has_replacement(role, packet):
    accounts = (packet.get('problem_evidence', {}).get('value') or {}).get('accounts') or {}
    return (bool(accounts.get('replacement'))
            and (role != 'adjudicator' or packet.get('rubric_definition', {}).get('item') == 'A7'))


def _has_areas(role, packet):
    return bool(packet.get('problem_areas'))


@dataclass(frozen=True)
class PromptParagraph:
    name: str
    roles: Tuple[str, ...]
    condition: Callable
    versions: Tuple[Tuple[str, str], ...]


# Fixed order. Every registered paragraph has shipped: the carry text with 0.82.0,
# the accounts, slate-forming and replacement texts with 0.84.0, and the areas text
# with 0.85.0. Preserve the bytes of a shipped version, and append a new version id
# for a changed text. The tests pin the hash of each version.
PARAGRAPHS = (
    PromptParagraph('objection-carry', ('slate', 'result'), _has_carry,
                    ((CARRY_PROTOCOL, CARRY_PROMPT),)),
    PromptParagraph('accounts', ('result', 'adjudicator'), _has_accounts,
        (('exactory-problem-accounts-v1',
          '\n\nThe accounts of the statement (exactory-problem-accounts-v1). '
          'The problem evidence holds, under value.accounts, the accounts that the author recorded with the value '
          'paragraphs. '
          'They keep the conjectured answer apart from the question, and they enter each fact once, with its scope and '
          'the date on which it became available. '
          'An entry cites the evidence of another entry by its id. '
          'answer gives the status of the conjectured answer: none, forecast (an answer stated without grounds) or '
          'conjectured. '
          'answer_evidence holds each fact that tells for or against that answer, with the alternative that it tells '
          'against. '
          'boundaries holds each fact that shows a relaxation or a hypothesis to be necessary. '
          'explanatory_use holds each fact that shows what the statement organizes. '
          'alternatives holds the nearest alternatives with their roles (stronger, weaker, same_assertion with another '
          'constant or exponent, other_assertion about the same objects), and for a weaker one the surplus, which is '
          'what the statement asserts beyond it, with its status (supported, used, supported_and_used or neither). '
          'An alternative that names a form and no declared neighbour was named after the formulation: its status '
          'rests on the searches that its entry names, and the openness finding does not cover it. '
          'strength lists the range of instances, the constants, the exponents and the exactness of the statement, each '
          'with the alternative that relaxes it or the reason that none exists. '
          'relations holds each relation of the statement to a named statement or body of facts, with its standing '
          '(established or expected), and for a consequence the alternative that it is credited to. '
          'checks, gain, search_coverage, refuted_conjectures and replacement are present where the record needs them. '
          'value.derived_accounts holds what the harness computed from the records, and the author did not write it. '
          'Its checks list every run whose plan names the present text of the statement (current) or an earlier text of '
          'its lineage (earlier), with the assessed status of each failure signal, and beside each run entered as a test '
          'the probes and runs that the record held before the plan, with their questions. '
          'Its history lists the forms from which the statement descends, with the outcome of each. '
          'Its counts give the forming calls, forms, recorded comparisons, probes and runs of that lineage and of the '
          'whole exploration. '
          'account_records delivers the cited searches, the run plans with their test declarations, the forming calls '
          'and the outcomes as records. '
          'Each value paragraph lists under delivered_evidence the evidence of the entries that it cites. '
          'The harness checked the form of the entries and of their references. '
          'It did not check that an entry is true, so judge each entry against the evidence that it cites. '
          'Read the rubric with the accounts as follows. '
          'No item, level or threshold changes. '
          'Where this paragraph is stricter than an anchor above, this paragraph governs. '
          'Relations. '
          'A relation is established when an accepted result, a full reading or an admitted computation within its '
          'stated scope shows it for the present text of the statement. '
          'Compare the claim of each cited result, and the claim of its objective where the packet gives one (claim and '
          'objective_claim under results), with the relation that its entry states. '
          'A result that was proved for an earlier form or for another statement does not establish the relation for '
          'the present text. '
          'Every other relation is expected and supports no premise. '
          'Significance. '
          'The levels of B1 and B2 grade what each answer to the question would settle. '
          'A consequence is credited to the weakest named alternative that gives it, and a consequence credited to a '
          'weaker alternative does not by itself raise a level of the statement posed. '
          'Evidence for the conjectured answer adds nothing to a level. '
          'That a statement gathers others is no evidence for its answer. '
          'Priority. '
          'The objection that a neighbour is the better question is answered for a weaker alternative only when the '
          'surplus over it is supported or used. '
          'It is supported by evidence for the conjectured answer that tells against that same alternative. '
          'It is used by a consequence that is credited to the statement and wanted on grounds independent of it. '
          'A surplus that is neither, such as an exact constant or exponent that no evidence supports and no '
          'consequence uses, leaves the objection unanswered and priority below level 2. '
          'For a consequence credited to the statement, the record says for each weaker alternative whether a '
          'derivation from it was attempted and how it ended. '
          'Weigh a derivation that was not attempted as a shortfall. '
          'The record says of each stronger alternative why it was not posed. '
          'That no alternative and no earlier statement was overlooked rests on the searches of search_coverage and is '
          'qualified by what they could not reach. '
          'Accessibility. '
          'A theorem that is the statement in a neighbouring setting is evidence for the conjectured answer and is no '
          'foothold. '
          'A theorem on a subclass of the instances of the statement itself is a foothold when it gives a meaningful '
          'entry or partial progress, as class E-F requires. '
          'Calibration. '
          'Each fact stands in one account. '
          'A stronger form proved false locates a boundary, and a recovered theorem shows explanatory use. '
          'Each speaks for the conjectured answer only through the argument that its entry states. '
          'A check is a test of the statement only when its plan, frozen before the execution, named the present text, '
          'the outcome that would contradict it, and an alternative that the record held at that time with the outcome '
          'that this alternative excludes, and when nothing shows that the outcome was known before. '
          'The harness checked the order of the records, the named alternative, the assessed failure signals, and that '
          'no earlier probe or run of the record asks the question of the plan word for word. '
          'It did not establish that the outcome was unknown to the model that planned the run, whether an earlier '
          'computation covers the tested quantity under another question, or what the search for the outcome in print '
          'found. '
          'Judge these from the test declaration of the plan, the independence entry of the check, the earlier '
          'computations listed beside the test, the probes that the forming calls of the history list with the role '
          'origin, and the delivered search. '
          'If you know the outcome from a source or a table, say so and name it. '
          'The check is then an observation. '
          'A run on an earlier text is the origin of a corrected text. '
          'It stays a test of the present text only where the check names its lineage and the forming call of the '
          'correction states that the corrected text keeps the tested prediction and that the correction was not chosen '
          'with the outcome of the run. '
          'Judge that statement against the ground of the correction and the dates of the run and of the correction. '
          'A passed test is experimental support for the conjectured answer against the alternative that its plan named, '
          'and against no other. '
          'Every other computed agreement is an observation or the origin of the statement. '
          'An agreement that was in view when the text, a constant or an exponent was chosen is the origin. '
          'It is a numerical pattern and supports no answer. '
          'Weigh an agreement that was found among many tries against the delivered counts. '
          'Where nothing speaks for an answer, the record conjectures none or states one as a forecast. '
          'The value paragraphs are the public text. '
          'They state only what a cited entry carries, and they use neither the name "conjecture of the abc kind" nor '
          'a comparison with a named conjecture as a claim about the standing of the statement. '
          'Contribution. '
          'Under A3 the record says which relation holds between the statement and each earlier statement (an '
          'equivalence, a strengthening, a generalization or a new presentation of existing results), shows it, and '
          'names every earlier statement that its searches found. '
          'Give a finding on the gain in value.rubric_evidence.A3 whenever the gain part claims one, and whenever the '
          'relations hold two or more established consequences with separate grounds of which at least one is credited '
          'to the statement, whatever the author claims. '
          'A gain is an established advantage of the one statement over the list of what it gathers. '
          'Statements have separate grounds when the works read state or study each for reasons of its own and present '
          'none as a case, a refinement or a consequence of another. '
          'No statement has a gain by its form. '
          'The gain has one of three forms. '
          'Ask the questions of the form that the record claims. '
          'First form, a common structure. '
          'Is each gathered statement derived from one and the same assertion of the statement, by applying it to a '
          'family of its instances or through a proved translation? '
          'Read which assertion each derivation uses, however the statement is written. '
          'A statement from which each gathered statement follows through a clause of its own, such as a conjunction of '
          'two statements written as one inequality on a minimum, gathers them as a list. '
          'What does the one statement add to the conjunction of what it gathers? '
          'Where the record proves it stronger in a stated respect, that respect is a surplus, which is supported or '
          'used as under Priority. '
          'Where it is equivalent to the conjunction, or the relation is not known, the record names what the one '
          'statement adds and establishes it, for example a construction or a method of proof shown on proved cases of '
          'two gathered statements. '
          'Equivalence with the conjunction excludes nothing by itself. '
          'Second form, an explanatory relation. '
          'Which two bodies of facts does the statement connect? '
          'On which cases were both known independently, each with its source and its date? '
          'Does a proof, a full reading or an admitted computation show that the statement determines the facts of one '
          'from those of the other on those cases? '
          'Were the cases chosen or adjusted with the outcome in view, which makes them the origin? '
          'Third form, another proved advantage. '
          'What is the advantage, in one sentence, and which proof in the record establishes it? '
          'For reductions under which a named line of proofs reaches results that it does not reach without the '
          'statement, more than one is proved. '
          'In each form the objects of the statement are the standard ones of an area, or the gain part justifies the '
          'new definition on grounds other than what the statement gathers. '
          'A gathered statement counts whichever alternative it is credited to. '
          'The gain belongs to the statement when at least one gathered statement with separate grounds is credited to '
          'the statement itself. '
          'When every gathered statement is credited to one weaker alternative, the structure belongs to that '
          'alternative. '
          'A claimed gain that is not established fails A3 until it is shown or withdrawn. '
          'Where the judgments go. '
          'The response shape of this turn is unchanged: an intermediate, source-request or staged task response gives '
          'only the fields that its task owns. '
          'In a completed result review the assurance problem_value covers the accounts. '
          'It fails when a relation marked established is not established for the present text, when an entry stands in '
          'the wrong account, or when a claimed gain is not shown. '
          'The grades of value.rubric and the texts of value.rubric_evidence for A3, B1, B2, priority, accessibility and '
          'calibration carry the judgments above. '
          'State these findings as material objections in objections: a nearer alternative or an earlier statement that '
          'the accounts do not name, a relation that the value case relies on and that is not established for the '
          'present text, a fact in a value paragraph that no cited entry carries, and a gain that the accounts establish '
          'and the gain part does not claim. '
          'An adjudicator who receives this paragraph with a rubric definition applies it to the one item that it '
          'decides and keeps its own response shape.'),)),
    PromptParagraph('slate-forming', ('slate',), _has_forming_candidates,
        (('exactory-problem-slate-forming-v1',
          '\n\nCandidates formed in the record (exactory-problem-slate-forming-v1). '
          'A candidate whose source.kind is phenomenon, or whose source.evidence names a forming_call or a form_outcome '
          'record, is a statement that the record formed itself and did not find in a source. '
          'Its source.reference names the starting material, the move that produced the statement, the forms that it '
          'derives from and the outcome of each. '
          'Its observation states the gain that the record expects to establish and the work that would establish it. '
          'source.evidence names the forming records, and this packet does not deliver their content. '
          'Say in limitations what you could not judge without them. '
          'A gain is an established advantage of one statement over the list of what it gathers. '
          'No statement has a gain by its form: a conjunction of known statements, a strengthening or a generalization '
          'of one named statement, a variant in one parameter, and a statement about an object that is defined so that '
          'it unpacks into known statements each need the established gain, like any other statement. '
          'The number of statements that would follow from a candidate gives it no priority, and that a statement '
          'gathers others is no evidence for its answer. '
          'For each such candidate, answer these questions in value.reason and in your objections. '
          'Which form of gain does the candidate expect: a common structure, an explanatory relation between two bodies '
          'of facts, or another proved advantage? '
          'What would the statement gather, and do the gathered statements have separate grounds, which means that the '
          'works named state or study each for reasons of its own and present none as a case, a refinement or a '
          'consequence of another? '
          'For a common structure: would each gathered statement be derived from one and the same assertion of the '
          'candidate, by applying it to a family of its instances or through a proved translation? '
          'A candidate from which each gathered statement would follow through a clause of its own, however it is '
          'written, gathers a list. '
          'How would the statement stand to the conjunction of what it gathers: equivalent, stronger in a stated '
          'respect, or not known? '
          'Where it would be equivalent, or the relation is not known, what would the one statement add, and how would '
          'the record establish that? '
          'For an explanatory relation: on which cases are both bodies of facts known independently, and can the record '
          'show on them that the statement determines the facts of one from those of the other? '
          'For another advantage: what is it, and which proof would establish it? '
          'Does a nearer alternative, among the declared neighbours or known to you, already give what the candidate '
          'names: a weaker form, the same assertion with another constant or exponent, another assertion about the same '
          'objects, or an earlier statement? '
          'What it gives is credited to that alternative. '
          'Which part of the strength of the candidate (its range of instances, a constant, an exponent, its exactness) '
          'would be neither supported by evidence for its answer nor used by a consequence that is wanted on '
          'independent grounds? '
          'Are its objects the standard ones of an area, or does it rest on a new definition that only what it gathers '
          'motivates? '
          'Can the work that the observation names establish the expected relations and the gain, and which check or '
          'attack would refute the candidate first? '
          'The potential grades of such a candidate grade what the record would establish if the relations that it '
          'marks as expected are proved. '
          'Give level 1 where the expected gain rests on assertion, and say what evidence would raise it. '
          'Where a candidate gathers a list, where a nearer alternative already gives what it names, or where it rests '
          'on a numerical pattern, such as a bound or a constant fitted to computed values, say so as an objection that '
          'names the candidate in candidate_ids. '
          'The response shape of this turn is unchanged.'),)),
    PromptParagraph('replacement', ('result', 'adjudicator'), _has_replacement,
        (('exactory-problem-replacement-v1',
          '\n\nA statement posed in place of a refuted one (exactory-problem-replacement-v1). '
          'The accounts of this statement hold a replacement part, value.accounts.replacement. '
          'Each entry names one refuted statement, with the refuting evidence and one account in words of how the '
          'present statement stands to it. '
          'The refuted statement is a refuted conjecture that the question concerns (refuted_id, an entry of '
          'refuted_conjectures), or a form that this record held as its expected statement and that a check or a proof '
          'refuted (form_id, a form of a delivered forming call). '
          'Item A7 is unchanged, and its test of purpose decides. '
          'Read the purpose from the question and its value case, not from its surface form. '
          'A statement posed in place of a refuted one meets three necessary conditions, of which none is sufficient. '
          'First, it speaks about every object of the refuted statement, the counterexamples among them, and says '
          'something of substance about them. '
          'Second, no clause of it and nothing that it leaves open is the refuted assertion on a class of objects. '
          'A case in which the refuted assertion is already a theorem is not left open. '
          'Third, the place at which it sets its bound, threshold or exponent is fixed by a stated ground other than how '
          'far the known counterexamples reach, such as a theorem, a derivation or a structural fact. '
          'A statement that keeps the refuted assertion on a class that leaves out the counterexamples fails the second '
          'condition, also when it adds a weaker bound on the rest. '
          'A statement that moves a constant or an exponent of the refuted assertion to a value that the known '
          'counterexamples have not reached fails the third condition. '
          'A statement that fails one of the three conditions fails A7. '
          'A statement that meets all three is still judged by its purpose: it fails A7 when its purpose is to find '
          'where the refuted statement still holds, with a value case that rests on that survival. '
          'Questions about the mechanism or the structure of the counterexamples and about the results that fall with '
          'the refuted statement stay admitted as A7 states them, and the first condition is not asked of them. '
          'A stronger form that the record wrote as an alternative, to test whether a hypothesis is necessary, and '
          'refuted, is evidence that locates a boundary and is no refuted statement in this sense. '
          'Check each condition against the statement, the refuting evidence and the account of each entry. '
          'Say in value.rubric_evidence.A7 which conditions hold, which fail, and what the purpose of the question is. '
          'The grade stays in value.rubric.admission.A7. '
          'If the question concerns a refuted conjecture that refuted_conjectures does not name, state that as a '
          'material objection in objections. '
          'The response shape of this turn is unchanged: an intermediate, source-request or staged task response gives '
          'only the fields that its task owns. '
          'An adjudicator who receives this paragraph with the definition of A7 decides by this reading only whether '
          'the question is of the excluded kind, and keeps its own response shape.'),)),
    PromptParagraph('areas', ('bar', 'result'), _has_areas,
        (('exactory-problem-areas-v1',
          '\n\nThe further areas of the record (exactory-problem-areas-v1). '
          'The statements of this record concern more than one area of mathematics. '
          'The area of the packet is the subject class of the record. '
          'It is the area of the post, and it was fixed when the record was opened. '
          'problem_areas lists each further area, with its subject class (area) and the reason for naming it. '
          'The reason is the description of the author. '
          'Read it to find the part of that area which the field question touches, and take it as no evidence of '
          'value. '
          'For a bar. '
          'State the standard of each area separately: once for the subject class of the record, and once for each '
          'further area. '
          'For each area, answer the six points of a bar (First to Sixth above) for that area alone: its active lines of '
          'work with the works that show the activity; its nearest known problems and conjectures, with what a '
          'resolution of each would give; what it counts as a substantial advance; the meaning that its specialists give '
          'to levels 2 and 3, on each ground of significance and for comparative priority; what makes a question '
          'uninteresting there; and its refuted conjectures, with the questions that each refutation opens. '
          'For a further area, the field is the part of its subject class that the field question of the packet and the '
          'reason of its entry point to. '
          'Write the standards one after another in value.reason, each under the name of its subject class, and begin '
          'with the subject class of the record. '
          'Rest each standard on delivered sources of its own area, and request the sources of an area that you need and '
          'do not have. '
          'Where the delivered sources do not let you state the standard of an area, name that area in limitations; its '
          'standard then stays unstated, and the standard of another area does not stand in for it. '
          'When the packet carries problem_refresh, this bar continues your earlier bar, and later readers receive it in '
          'place of that bar. '
          'Keep what the earlier bar states for the areas that it covers, add the standard of each area that it does not '
          'cover, and give every standard in full. '
          'For a result review. '
          'The bars of the packet state one standard for each area, and you apply each of them. '
          'Judge each relation, each earlier statement and each alternative by the standard of the area that it belongs '
          'to. '
          'The standard of that area says whether a consequence there is wanted, whether a statement there is an earlier '
          'statement of the same content, and whether an alternative there is the better question. '
          'Where the grade of an item rests on such a judgment, say in the text of that item under value.rubric_evidence '
          'which area the relation, the earlier statement or the alternative belongs to, and what the standard of that '
          'area shows. '
          'Give each grade of value.rubric by the standard of the subject class of the record, one grade for each item. '
          'The thresholds for posting read these grades. '
          'A consequence or a line of work in a further area enters them as far as it is established and the standard of '
          'its own area shows it to be wanted, and no further. '
          'A grade that the statement would have by the standard of a further area belongs to the evidence text of the '
          'item, and it is not the grade of the item. '
          'When the weight of the statement lies in a further area, say so in value.reason: such a statement belongs in '
          'a record of that subject class, and the grades by the standard of this record show what it gives here. '
          'If a bar states no standard for an area that problem_areas names, say so in limitations; a claim that belongs '
          'to that area then enters no grade. '
          'No item, level or threshold changes. '
          'The response shape of this turn is unchanged: an intermediate, source-request or staged task response gives '
          'only the fields that its task owns.'),)))


def select_prompt_parameters(role, packet):
    """Select the paragraphs called for by this delivered packet, including compact task contexts."""
    if packet.get('subject', {}).get('kind') != 'problem':
        return {}
    conditions = (packet.get(CONDITIONS) if 'value' not in packet.get('problem_evidence', {}) else None)
    versions = [paragraph.versions[-1][0] for paragraph in PARAGRAPHS[1:]
                if role in paragraph.roles and
                (paragraph.name in conditions if conditions is not None else paragraph.condition(role, packet))]
    if role in PARAGRAPHS[0].roles and _has_carry(role, packet):
        carry_version = PARAGRAPHS[0].versions[-1][0]
        if carry_version != CARRY_PROTOCOL:
            versions.insert(0, carry_version)
    parameters = {PARAMETER: versions} if versions else {}
    if role in PARAGRAPHS[0].roles and _has_carry(role, packet):
        parameters[CARRY_PARAMETER] = packet[CARRY_PARAMETER]
    return parameters


def preserve_prompt_conditions(role, source, packet):
    """Keep the applicability facts when a staged task or continuation elides the delivered value."""
    selected = select_prompt_parameters(role, source).get(PARAMETER, [])
    names = [paragraph.name for paragraph in PARAGRAPHS[1:]
             if any(version in selected for version, _ in paragraph.versions)]
    if names:
        packet[CONDITIONS] = names


def read_paragraph_parameters(prompt):
    """Replay the paragraph versions of an existing assignment when sizing another turn."""
    parameters = prompt['parameters']
    return {key: parameters[key] for key in (CARRY_PARAMETER, PARAMETER) if key in parameters}


def _select_versions(role, carried, versions):
    if versions is None:
        versions = []
    if not isinstance(versions, list) or not all(isinstance(version, str) for version in versions):
        raise ResearchError('invalid_review_context', INVALID_CONTEXT)
    carry_versions = [version for version, _ in PARAGRAPHS[0].versions if version in versions]
    # The original text version is implicit in the 0.82.0 marker; later text ids are explicit.
    if CARRY_PROTOCOL in versions or carry_versions and carried is None:
        raise ResearchError('invalid_review_context', INVALID_CONTEXT)
    if carried is not None:
        fields(carried, ('protocol', 'import_set_digest'))
        if carried['protocol'] != CARRY_PROTOCOL or role not in PARAGRAPHS[0].roles:
            raise ResearchError('invalid_review_context', 'Carried objection prompts belong to slate and result assignments')
    requested = ([CARRY_PROTOCOL] if carried is not None and not carry_versions else []) + versions
    selected = [(version, text) for paragraph in PARAGRAPHS if role in paragraph.roles
                for version, text in paragraph.versions if version in requested]
    if requested != [version for version, _ in selected]:
        raise ResearchError('invalid_review_context', INVALID_CONTEXT)
    # Two historical versions of one paragraph cannot appear on the same prompt.
    if any(sum(version in requested for version, _ in paragraph.versions) > 1 for paragraph in PARAGRAPHS):
        raise ResearchError('invalid_review_context', INVALID_CONTEXT)
    return selected


def append_prompt_paragraphs(base, role, carried=None, versions=None):
    selected = _select_versions(role, carried, versions)
    return _append_selected_paragraphs(base, carried, versions, selected)


def _append_selected_paragraphs(base, carried, versions, selected):
    parameters = dict(base['parameters'])
    if carried is not None:
        parameters[CARRY_PARAMETER] = carried
    if versions:
        parameters[PARAMETER] = list(versions)
    text = base['text'] + ''.join(text for _, text in selected)
    return dict(template_id=base['template_id'] + ''.join('/' + version[len('exactory-problem-'):] for version, _ in selected),
                template_hash=digest(text), rendered_prompt_hash=digest(text), parameters=parameters, text=text)


def strip_prompt_paragraphs(prompt, role):
    """Return the exact base envelope, or None for an unregistered or altered addition."""
    parameters = prompt.get('parameters', {})
    if PARAMETER not in parameters and CARRY_PARAMETER not in parameters:
        return prompt
    versions = parameters.get(PARAMETER)
    if PARAMETER in parameters and not versions:
        return None
    try:
        selected = _select_versions(role, parameters.get(CARRY_PARAMETER), versions)
    except (ResearchError, TypeError, KeyError):
        return None
    suffix = ''.join(text for _, text in selected)
    template_suffix = ''.join('/' + version[len('exactory-problem-'):] for version, _ in selected)
    if not selected or not prompt.get('text', '').endswith(suffix) or not prompt.get('template_id', '').endswith(template_suffix):
        return None
    base_parameters = {key: value for key, value in parameters.items() if key not in (PARAMETER, CARRY_PARAMETER)}
    text = prompt['text'][:-len(suffix)]
    base = dict(template_id=prompt['template_id'][:-len(template_suffix)], template_hash=digest(text),
                rendered_prompt_hash=digest(text), parameters=base_parameters, text=text)
    return base if _append_selected_paragraphs(base, parameters.get(CARRY_PARAMETER), versions, selected) == prompt else None


def matches_packet_paragraphs(prompt, role, packet):
    """Check applicability by paragraph family, retaining any admitted historical version."""
    actual = prompt.get('parameters', {})
    expected = select_prompt_parameters(role, packet)
    if actual.get(CARRY_PARAMETER) != expected.get(CARRY_PARAMETER):
        return False
    versions = actual.get(PARAMETER, [])
    wanted = expected.get(PARAMETER, [])
    return all(any(version in versions for version, _ in paragraph.versions)
               == any(version in wanted for version, _ in paragraph.versions) for paragraph in PARAGRAPHS[1:])


def append_rubric_definition(packet):
    """Refresh the applicable paragraph beside the retained normative anchor for a new assignment."""
    definition = packet['rubric_definition']['definition']
    for paragraph in reversed(PARAGRAPHS):
        for _, text in paragraph.versions:
            if definition.endswith(text):
                definition = definition[:-len(text)]
    selected = select_prompt_parameters('adjudicator', packet).get(PARAMETER, [])
    packet['rubric_definition']['definition'] = definition + ''.join(
        text for paragraph in PARAGRAPHS for version, text in paragraph.versions if version in selected)
