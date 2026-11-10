"""The accounts of a problem's value: facts once, with references between parts."""

import copy

from . import strategy
from .errors import ResearchError
from .evidence import digest
from .operations import fields, strings, text, timestamp


ACCOUNT_LIST_PARTS = ("answer_evidence", "boundaries", "explanatory_use", "alternatives", "strength", "relations",
                      "checks", "search_coverage", "refuted_conjectures", "replacement")
STRENGTH_HEADS = ("range_of_instances", "constants", "exponents", "exactness")
RELATION_KINDS = ("implies", "follows_from", "equivalent_to", "incompatible_with", "strengthens", "generalizes", "presents_anew")
ALTERNATIVE_ROLES = ("stronger", "weaker", "same_assertion", "other_assertion", "no_alternative")


def collect_entries(accounts):
    entries = [accounts["answer"]]
    for part in ACCOUNT_LIST_PARTS:
        entries.extend(accounts.get(part, []))
    if accounts.get("gain") is not None:
        entries.append(accounts["gain"])
        entries.extend(accounts["gain"]["separate_grounds"])
    for relation in accounts["relations"]:
        if relation.get("consequence") is not None:
            entries.extend(relation["consequence"]["weaker_derivations"])
    return entries


def resolve_entry(entries, identifier):
    text(identifier, "A cited account entry ID", code="problem_account_reference_missing")
    if identifier not in entries:
        raise ResearchError("problem_account_reference_missing",
                            "No entry of these accounts has this ID. Cite the ID of an entry that the accounts hold, or "
                            "add that entry", {"id": identifier})
    return entries[identifier]


def collect_entry_evidence(entries, identifier, trail=()):
    if identifier in trail:
        raise ResearchError("problem_account_reference_cycle",
                            "The references of this entry lead back to the entry itself. Remove one reference of the cycle, so that the "
                            "entry reaches its evidence", {"id": identifier})
    entry = resolve_entry(entries, identifier)
    evidence = list(entry.get("evidence", []))
    identifiers = list(entry.get("evidence_ids", []))
    identifiers.extend(entry.get("alternative_ids", []))
    if "role" in entry and entry.get("relation_id") in entries:
        identifiers.append(entry["relation_id"])
    if entry.get("check_id") in entries:
        identifiers.append(entry["check_id"])
    if "surplus" in entry:
        identifiers.extend(entry["surplus"]["evidence_ids"] + entry["surplus"]["relation_ids"])
    if "establishing_ids" in entry:
        identifiers.extend(entry["establishing_ids"] + entry["relation_ids"] + entry["conjunction"]["evidence_ids"])
        identifiers.extend(ground["id"] for ground in entry["separate_grounds"])
    if entry.get("consequence") is not None:
        identifiers.extend(comparison["id"] for comparison in entry["consequence"]["weaker_derivations"])
    for reference in identifiers:
        evidence.extend(collect_entry_evidence(entries, reference, trail + (identifier,)))
    return list({digest(reference): reference for reference in evidence}.values())


def collect_account_evidence(accounts):
    # Deliver all facts, including ones that public paragraphs do not cite.
    return [reference for entry in collect_entries(accounts) for reference in entry.get("evidence", [])]


def find_result_revision(records, reference):
    identifier = reference.get("native_attempt_digest")
    if identifier is None:
        identifier = records["problem_attempt"][reference["attempt_id"]]["solver"]["native_attempt_digest"]
    native = records["native_attempt"][identifier]
    acceptance = next(row for row in native["acceptances"] if row["id"] == reference["acceptance_id"])
    claim = next(row["claim"] for row in native["checkpoints"] if row["id"] == acceptance["checkpoint_id"])
    return min(row["recorded_revision"] for row in records["native_attempt"].values()
               if (row["attack_root"], row["objective_id"]) == (native["attack_root"], native["objective_id"])
               and any(digest(item) == digest(acceptance) for item in row["acceptances"])
               and any(item["id"] == acceptance["checkpoint_id"] and item["claim"] == claim for item in row["checkpoints"]))


def find_evidence_availability(records, reference):
    if reference["kind"] == "reading":
        year = records["work"][reference["version_id"]].get("year")
        return None if year is None else str(year)
    if reference["kind"] == "result":
        if "native_attempt_digest" in reference:
            native = records["native_attempt"][reference["native_attempt_digest"]]
        else:
            attempt = records["problem_attempt"][reference["attempt_id"]]
            native = records["native_attempt"][attempt["solver"]["native_attempt_digest"]]
        acceptance = next(row for row in native["acceptances"] if row["id"] == reference["acceptance_id"])
        checkpoint = next(row for row in native["checkpoints"] if row["id"] == acceptance["checkpoint_id"])
        for record in (acceptance, checkpoint):
            for key in ("accepted_at", "created_at", "recorded_at", "timestamp"):
                if record.get(key):
                    return record[key]
    return None


def derive_evidence_date(records, reference):
    availability = find_evidence_availability(records, reference)
    return None if availability is None else availability[:10]


def validate_account_evidence(records, artifacts, accounts, entries, revision):
    from .problem_evidence import validate_computation, validate_reading, validate_result
    required_codes = {entry["id"]: code for part, code in (
        ("answer_evidence", "problem_answer_evidence_missing"), ("boundaries", "problem_boundary_evidence_missing"),
        ("explanatory_use", "problem_explanatory_evidence_missing"), ("relations", "problem_relation_evidence_missing"))
        for entry in accounts[part]}
    checks = {entry["id"]: entry for entry in accounts.get("checks", [])}
    for entry in entries.values():
        if entry.get("role") == "weaker" and entry.get("surplus") is None:
            raise ResearchError("problem_surplus_missing",
                                "A weaker alternative needs surplus: {description, status, evidence_ids, relation_ids}. "
                                "State what the statement asserts beyond this alternative, and set status to supported, "
                                "used, supported_and_used or neither", {"id": entry["id"]})
        for key in ("evidence_ids", "alternative_ids", "relation_ids", "establishing_ids"):
            if key in entry:
                strings(entry[key], "The account entry field " + key)
        for key in ("relation_id", "check_id"):
            if entry.get(key) is not None:
                text(entry[key], "The account entry field " + key)
        for key in ("surplus", "conjunction"):
            if key in entry:
                fields(entry[key], ("description", "status", "evidence_ids") + (("relation_ids",) if key == "surplus" else ()))
                strings(entry[key]["evidence_ids"], "The evidence_ids of the " + key + " object")
                if key == "surplus":
                    strings(entry[key]["relation_ids"], "The relation_ids of the surplus")
        code = required_codes.get(entry["id"], "invalid_input")
        exploratory = entry["id"] in checks and entry.get("standing") != "test"
        for reference in strategy.items(entry.get("evidence", []), "The evidence of an account entry", code="invalid_input"):
            fields(reference, ("kind",), None)
            strategy.choice(reference["kind"], ("reading", "computation", "result", "probe") if exploratory else
                            ("reading", "computation", "result"), "The kind of an evidence reference of an account entry", code)
            if reference["kind"] == "reading":
                text(reference.get("reading_id"), "The reading_id of a reading reference")
                text(reference.get("version_id"), "The version_id of a reading reference")
                validate_reading(records, artifacts, reference)
            elif reference["kind"] == "computation":
                text(reference.get("execution_id"), "The execution_id of a computation reference")
                text(reference.get("output_id"), "The output_id of a computation reference")
                validate_computation(records, artifacts, reference, recorded_revision=revision)
            elif reference["kind"] == "result":
                validate_result(records, reference)
            else:
                fields(reference, ("kind", "id"))
                strategy.get_record(records, "probe", reference["id"])


def validate_fact(records, entry, entries, *, required_code=None, exploratory=False):
    from .problem_forming import validate_evidence_date
    references = collect_entry_evidence(entries, entry["id"])
    if required_code and not references:
        raise ResearchError(required_code,
                            "This entry has no evidence. Cite a full reading, an admitted computation or an accepted result in "
                            "evidence, or cite in evidence_ids an entry that holds one. If the entry is a relation "
                            "that no such evidence shows, record its standing as expected",
                            {"id": entry["id"]})
    if "date" in entry:
        validate_evidence_date(entry["date"])
    dates = []
    for reference in references:
        if reference["kind"] == "probe" and not exploratory:
            raise ResearchError(required_code or "invalid_input",
                                "A probe is exploratory and supports no fact of the accounts. Cite a full reading, an "
                                "admitted computation or an accepted result. Only a check with the standing planned, "
                                "observation or origin can cite a probe", {"id": entry["id"]})
        date = derive_evidence_date(records, reference)
        if date is not None:
            validate_evidence_date(date)
            dates.append(date)
    # All sources must have dates to derive the availability of a combined fact.
    if references and len(dates) == len(references):
        entry["date"] = max(dates)
    elif "date" not in entry:
        raise ResearchError("problem_account_date_missing",
                            "The record cannot derive the date of this entry from its evidence. Enter date, the day on "
                            "which the fact became available, as YYYY, YYYY-MM, YYYY-MM-DD or a timestamp with a "
                            "timezone, or enter unknown",
                            {"id": entry["id"]})
    validate_evidence_date(entry["date"])


def validate_alternatives(records, accounts, entries, statement):
    from .problem_forming import collect_forms
    alternatives = {entry["id"]: entry for entry in accounts["alternatives"]}
    relations = {entry["id"]: entry for entry in accounts["relations"]}
    for neighbour in statement["neighbours"]:
        matches = [entry for entry in alternatives.values() if entry.get("neighbour") == neighbour and entry.get("role") in ALTERNATIVE_ROLES]
        if len(matches) != 1:
            raise ResearchError("problem_neighbour_role_missing",
                                "Give each declared neighbour of the statement exactly one alternatives entry: "
                                "neighbour is its exact text, and role is stronger, weaker, same_assertion, "
                                "other_assertion or no_alternative", {"neighbour": neighbour})
    forms = collect_forms(records)
    for entry in alternatives.values():
        fields(entry, ("id", "neighbour", "form_id", "role"), ("relation_id", "surplus", "reason", "search_ids"))
        strategy.choice(entry["role"], ALTERNATIVE_ROLES, "The role of an alternative", "problem_neighbour_role_missing")
        for key in ("neighbour", "form_id"):
            if entry[key] is not None:
                text(entry[key], "The alternative field " + key)
        if entry["neighbour"] not in statement["neighbours"]:
            form = forms.get(entry["form_id"])
            if form is None:
                raise ResearchError("problem_alternative_missing",
                                    "This alternative is no declared neighbour and no recorded form. Set neighbour to the "
                                    "exact text of a declared neighbour of the current statement. For an alternative that "
                                    "was named later, record it as a form with forming, and give form_id and search_ids", {"id": entry["id"]})
        elif entry["form_id"] is not None and entry["form_id"] not in forms:
            raise ResearchError("problem_alternative_missing",
                                "form_id names no form of this record. Name a form of a recorded forming call, or set "
                                "form_id to null", {"id": entry["id"]})
        search_ids = strings(entry.get("search_ids", []), "The search_ids of an alternative",
                             nonempty=entry["neighbour"] not in statement["neighbours"])
        for identifier in search_ids:
            strategy.get_record(records, "literature_search", identifier, code="problem_account_search_missing")
        role = entry["role"]
        if role in ("stronger", "weaker"):
            if entry.get("relation_id") is not None:
                text(entry["relation_id"], "The relation_id of an alternative")
            relation = relations.get(entry.get("relation_id"))
            if relation is None or relation.get("alternative_id") != entry["id"]:
                raise ResearchError("problem_alternative_relation_missing",
                                    "A stronger or weaker alternative needs its relation to the statement. Add a relations "
                                    "entry whose alternative_id is the ID of this alternative, and name that entry in "
                                    "relation_id",
                                    {"id": entry["id"]})
        if role in ("stronger", "no_alternative"):
            text(entry.get("reason"), "The reason of an alternative with the role stronger or no_alternative")
        if role == "weaker":
            surplus = entry.get("surplus")
            text(surplus["description"], "The description of the surplus over a weaker alternative")
            strategy.choice(surplus["status"], ("supported", "used", "supported_and_used", "neither"), "The status of the surplus", "invalid_input")
            strings(surplus["evidence_ids"], "The evidence_ids of the surplus")
            strings(surplus["relation_ids"], "The relation_ids of the surplus")
            for identifier in surplus["evidence_ids"] + surplus["relation_ids"]:
                resolve_entry(entries, identifier)
            if surplus["status"] in ("supported", "supported_and_used") and not any(
                    fact["id"] in surplus["evidence_ids"] and fact["alternative_id"] == entry["id"] and fact["direction"] == "for"
                    for fact in accounts["answer_evidence"]):
                raise ResearchError("problem_surplus_support_missing",
                                    "A surplus with the status supported needs evidence for the answer against this same "
                                    "alternative. Cite in surplus.evidence_ids an answer_evidence entry with the direction "
                                    "for whose alternative_id is this alternative, or record the status used or neither",
                                    {"id": entry["id"]})
            if surplus["status"] in ("used", "supported_and_used") and not any(
                    relation["id"] in surplus["relation_ids"] and relation["standing"] == "established"
                    and (relation.get("consequence") or {}).get("credited_to") == "statement"
                    for relation in relations.values()):
                raise ResearchError("problem_surplus_use_missing",
                                    "A surplus with the status used or supported_and_used needs an established consequence "
                                    "that is credited to the statement. Cite in surplus.relation_ids a relations entry "
                                    "whose standing is established and consequence.credited_to is statement, or record "
                                    "the status supported or neither", {"id": entry["id"]})
    for entry in accounts["strength"]:
        fields(entry, ("id", "head", "description", "alternative_ids", "no_alternative_reason"), code="problem_strength_incomplete")
        text(entry["head"], "The head of a strength entry")
    heads = [entry["head"] for entry in accounts["strength"]]
    if sorted(heads) != sorted(STRENGTH_HEADS):
        raise ResearchError("problem_strength_incomplete",
                            "Give strength exactly four entries, one for each head: range_of_instances, "
                            "constants, exponents and exactness")
    for entry in accounts["strength"]:
        text(entry["description"], "The description of a strength entry")
        strings(entry["alternative_ids"], "The alternative_ids of a strength entry")
        if not entry["alternative_ids"] and not entry["no_alternative_reason"]:
            raise ResearchError("problem_strength_incomplete",
                                "Name in alternative_ids the alternative that relaxes this part of the strength, "
                                "or give in no_alternative_reason the reason that none exists", {"id": entry["id"]})
        if entry["no_alternative_reason"] is not None:
            text(entry["no_alternative_reason"], "The no_alternative_reason of a strength entry")
        for identifier in entry["alternative_ids"]:
            if identifier not in alternatives or alternatives[identifier]["role"] == "no_alternative":
                raise ResearchError("problem_alternative_missing",
                                    "A strength entry names an alternative that the accounts do not hold. Name the ID of "
                                    "an alternatives entry whose role is not no_alternative", {"id": identifier})
    return alternatives


def validate_relations(accounts, entries, alternatives):
    weaker = {entry["id"] for entry in alternatives.values() if entry["role"] == "weaker"}
    for entry in accounts["relations"]:
        strategy.choice(entry["relation"], RELATION_KINDS, "The relation of a relations entry", "invalid_input")
        text(entry["target"], "The target of a relation")
        strategy.choice(entry["standing"], ("established", "expected"), "The standing of a relation", "invalid_input")
        if type(entry["earlier_statement"]) is not bool:
            raise ResearchError("invalid_input",
                                "Set earlier_statement to true when the target is an earlier statement of the same "
                                "content, and to false otherwise")
        if entry["alternative_id"] is not None:
            text(entry["alternative_id"], "The alternative_id of a relation")
        if entry["alternative_id"] is not None and entry["alternative_id"] not in alternatives:
            raise ResearchError("problem_alternative_missing",
                                "alternative_id of a relation names no alternatives entry. Name the ID of an "
                                "alternatives entry, or set alternative_id to null for a relation to another statement")
        consequence = entry["consequence"]
        if consequence is None:
            continue
        fields(consequence, ("assertion", "instances_or_translation", "credited_to", "weaker_derivations"))
        for key in ("assertion", "instances_or_translation"):
            text(consequence[key], "The consequence field " + key)
        text(consequence["credited_to"], "The credited_to of a consequence")
        if consequence["credited_to"] != "statement" and consequence["credited_to"] not in alternatives:
            raise ResearchError("problem_alternative_missing",
                                "Credit the consequence to the weakest named alternative that gives it. Set "
                                "credited_to to the ID of an alternatives entry, or to statement when no weaker "
                                "alternative gives the consequence")
        compared = []
        for comparison in consequence["weaker_derivations"]:
            fields(comparison, ("id", "alternative_id", "status", "reason", "evidence_ids"))
            strategy.choice(comparison["status"], ("not_attempted", "derived", "not_derived"), "The status of a weaker_derivations entry", "invalid_input")
            text(comparison["reason"], "The reason of a weaker_derivations entry")
            strings(comparison["evidence_ids"], "The evidence_ids of a weaker_derivations entry")
            for identifier in comparison["evidence_ids"]:
                resolve_entry(entries, identifier)
            text(comparison["alternative_id"], "The alternative_id of a weaker_derivations entry")
            compared.append(comparison["alternative_id"])
        if consequence["credited_to"] == "statement" and (set(compared) != weaker or len(compared) != len(weaker)):
            raise ResearchError("problem_consequence_comparison_missing",
                                "A consequence credited to the statement needs one "
                                "weaker_derivations entry for each alternative with the role weaker. Give each entry the "
                                "status derived, not_derived or not_attempted, with the reason",
                                {"id": entry["id"]})


def validate_accounts(records, artifacts, value):
    from .problem_claims import find_current_statement
    from .problem_forming import validate_description
    accounts = copy.deepcopy(value["accounts"])
    fields(accounts, ("answer", "answer_evidence", "boundaries", "explanatory_use", "alternatives", "strength", "relations", "refuted_conjectures"),
           ("checks", "gain", "search_coverage", "replacement"))
    for part in ACCOUNT_LIST_PARTS:
        strategy.items(accounts.get(part, []), "The accounts part " + part, code="invalid_input")
    fields(accounts["answer"], ("id", "status", "text", "evidence_ids"))
    # Check the nested shapes before indexing them in the shared entry registry.
    for relation in accounts["relations"]:
        fields(relation, ("id", "relation", "target", "alternative_id", "standing", "scope", "evidence", "evidence_ids", "earlier_statement", "consequence"), ("date",))
        if relation["consequence"] is not None:
            fields(relation["consequence"], ("assertion", "instances_or_translation", "credited_to", "weaker_derivations"))
            strategy.items(relation["consequence"]["weaker_derivations"], "The weaker_derivations of a consequence", code="invalid_input")
    if accounts.get("gain") is not None:
        fields(accounts["gain"], ("id", "form", "claim", "relation_ids", "separate_grounds", "conjunction", "establishing_ids", "new_object_justification"))
        strategy.items(accounts["gain"]["separate_grounds"], "The separate_grounds of the gain", code="invalid_input")
    entries = {}
    for entry in collect_entries(accounts):
        fields(entry, ("id",), None)
        text(entry["id"], "The ID of an account entry")
        if entry["id"] in entries or entry["id"] == "statement":
            raise ResearchError("problem_account_id_conflict",
                                "Two entries of the accounts have this ID, or an entry has the ID statement. "
                                "Give each entry an ID of its own. The ID statement is reserved for credited_to", {"id": entry["id"]})
        entries[entry["id"]] = entry
    validate_account_evidence(records, artifacts, accounts, entries, value.get("recorded_revision"))
    for identifier in entries:
        collect_entry_evidence(entries, identifier)
    answer = accounts["answer"]
    strategy.choice(answer["status"], ("none", "forecast", "conjectured"), "The status of the answer", "invalid_input")
    if answer["status"] != "none":
        text(answer["text"], "The text of a forecast or conjectured answer")
    elif answer["text"] is not None:
        raise ResearchError("invalid_input",
                            "An answer with the status none states no answer. Set its text to null, or record the "
                            "status forecast or conjectured")
    supporting = {entry["id"] for entry in accounts["answer_evidence"] if entry.get("direction") == "for"}
    if answer["status"] == "conjectured" and not supporting.intersection(answer["evidence_ids"]):
        raise ResearchError("problem_answer_evidence_missing",
                            "A conjectured answer needs evidence for it. Name in answer.evidence_ids at "
                            "least one answer_evidence entry with the direction for. If nothing speaks for an answer, "
                            "record the status none or forecast")
    for identifier in answer["evidence_ids"]:
        resolve_entry(entries, identifier)
    alternatives = validate_alternatives(records, accounts, entries, find_current_statement(records))
    for part, additional, code in (("answer_evidence", ("kind", "direction", "alternative_id"), "problem_answer_evidence_missing"),
                                   ("boundaries", ("argument",), "problem_boundary_evidence_missing"),
                                   ("explanatory_use", ("answer_argument",), "problem_explanatory_evidence_missing")):
        for entry in accounts[part]:
            fields(entry, ("id", "fact", "scope", "evidence", "evidence_ids") + additional, ("date", "check_id"))
            text(entry["fact"], "The fact of an account entry")
            text(entry["scope"], "The scope of an account entry")
            if part == "answer_evidence":
                validate_description(entry["kind"])
                strategy.choice(entry["direction"], ("for", "against"), "The direction of an answer_evidence entry", "invalid_input")
                text(entry["alternative_id"], "The alternative_id of an answer_evidence entry")
                if entry["alternative_id"] not in alternatives or alternatives[entry["alternative_id"]]["role"] == "no_alternative":
                    raise ResearchError("problem_answer_alternative_missing",
                                        "Evidence for or against the answer tells against a named "
                                        "alternative. Set alternative_id to the ID of an alternatives entry whose role is "
                                        "not no_alternative", {"id": entry["id"]})
            else:
                key = additional[0]
                if entry[key] is not None:
                    text(entry[key], "The argument of a boundaries entry or the answer_argument of an explanatory_use entry")
            if "check_id" in entry:
                text(entry["check_id"], "The check_id of an account entry")
                if part != "answer_evidence" or entry["kind"]["kind"] != "passed_test":
                    resolve_entry(entries, entry["check_id"])
            validate_fact(records, entry, entries, required_code=code)
    for entry in accounts["relations"]:
        text(entry["scope"], "The scope of a relation")
        validate_fact(records, entry, entries,
                      required_code="problem_relation_evidence_missing" if entry["standing"] == "established" else None)
    validate_relations(accounts, entries, alternatives)
    validate_tier_two(records, value, accounts, entries, alternatives)
    validate_replacement(records, artifacts, accounts, entries)
    return accounts


def collect_candidate_forms(records, statement):
    from .problem_forming import collect_forms
    forms = collect_forms(records)
    candidate = records.get("problem_candidate", {}).get(statement.get("candidate_id"))
    identifiers = set()
    for reference in (candidate or {}).get("source", {}).get("evidence", []):
        if reference.get("kind") != "record":
            continue
        if reference.get("record_kind") == "forming_call":
            call = records.get("forming_call", {}).get(reference["id"])
            if call is not None and digest(call) == reference["digest"]:
                identifiers.update(form["id"] for form in call["forms"] if form["purpose"]["kind"] == "expected_statement")
        elif reference.get("record_kind") == "form_outcome":
            outcome = records.get("form_outcome", {}).get(reference["id"])
            if outcome is not None and digest(outcome) == reference["digest"]:
                identifiers.add(outcome["form_id"])
    return {identifier: forms[identifier] for identifier in identifiers if identifier in forms}


def precedes_evidence_date(call, date):
    if call.get("recorded_at") is None:
        return False
    if len(date) <= 10:
        return call["recorded_at"][:len(date)] < date
    return timestamp(call["recorded_at"]) < timestamp(date)


def require_prior_form(records, statement, before_revision, before_date=None):
    forms = collect_candidate_forms(records, statement)
    if (before_revision is not None or before_date is not None) and any(
            (before_revision is None or call["recorded_revision"] < before_revision)
            and (before_date is None or precedes_evidence_date(call, before_date))
            and any(form["id"] in forms for form in call["forms"])
            for call in records.get("forming_call", {}).values()):
        return
    raise ResearchError("problem_accounts_history_missing",
                        "A gain claim and a passed test need a history. The candidate of the "
                        "statement must cite, in source.evidence, a forming call or a form outcome whose form was "
                        "recorded before the evidence of the gain or the plan of the test. Without such a form, record "
                        "gain as null, and record the check as an observation")


def validate_test(records, entry, entries, alternatives, statement, revision):
    from .development import compute_plan_statement_digest
    from .problem_claims import compute_statement_digest
    code = "problem_test_run_invalid"
    independence = entry["independence"]
    if independence is None:
        raise ResearchError(code,
                            "A check with the standing test needs independence: {status: outcome_not_known, reason}. "
                            "If the outcome was known, record the check with the standing observation")
    fields(independence, ("status", "reason"), ("prior_knowledge",), code=code)
    text(independence["reason"], "The reason of the independence of a test", code=code)
    if independence["status"] != "outcome_not_known":
        raise ResearchError(code,
                            "A run whose outcome was known, or whose independence is not established, is an "
                            "observation. Record the check with the standing observation and passed false")
    alternative = alternatives.get(entry["alternative_id"])
    references = collect_entry_evidence(entries, entry["id"])
    computations = [reference for reference in references if reference["kind"] == "computation"]
    if not computations or alternative is None or alternative["role"] == "no_alternative":
        raise ResearchError(code,
                            "A test cites an admitted and assessed run, and it names an alternative. Cite the "
                            "computation in evidence or through evidence_ids, and set alternative_id to an alternatives "
                            "entry whose role is not no_alternative")
    for reference in computations:
        execution = records["execution"][reference["execution_id"]]
        plan = records.get("cycle_plan", {}).get(execution["payload"]["cycle_id"])
        declaration = None if plan is None else plan["payload"].get("test_declaration")
        if declaration is None or not plan["planned_revision"] < execution["admitted_revision"]:
            raise ResearchError(code,
                                "The plan of this run carries no test declaration that was frozen before the "
                                "execution. A run is a test only when its cycle plan held test_declaration. Record the "
                                "check with the standing observation")
        if compute_plan_statement_digest(records, plan) != compute_statement_digest(statement):
            lineage = entry.get("lineage")
            from .problem_forming import collect_forms
            form = None if lineage is None else collect_forms(records).get(lineage["form_id"])
            correction = next((row for row in (form or {}).get("correction_checks", [])
                               if row["check_id"] == lineage["check_id"]), None)
            old_statement = records["problem_statement"][plan["payload"]["objective"]["id"]]
            previous = None if form is None else collect_forms(records).get((form.get("previous") or {}).get("form_id"))
            if (form is None or form["id"] not in collect_candidate_forms(records, statement)
                    or form["text"] != statement["statement"] or previous is None or previous["text"] != old_statement["statement"]
                    or correction is None or correction.get("execution_id") != reference["execution_id"]
                    or not correction["was_made"] or not correction["keeps_prediction"]
                    or correction["chosen_with_outcome"] or not any(ground["evidence"] for ground in form["grounds"])):
                raise ResearchError(code,
                                    "The plan of this run names another text than the current statement. The run "
                                    "stays a test only through lineage {form_id, check_id}: the correction form has the "
                                    "text of the current statement and a ground with evidence, and its correction_checks "
                                    "entry for this run has was_made true, keeps_prediction true and chosen_with_outcome "
                                    "false. Otherwise record the check with the standing origin or observation")
        named = declaration["alternative"]
        if not ((named["kind"] == "neighbour" and named["text"] == alternative["neighbour"])
                or (named["kind"] == "form" and named["form_id"] == alternative["form_id"]
                    and any(form["id"] == alternative["form_id"] for form in records["forming_call"][named["id"]]["forms"]))):
            raise ResearchError(code,
                                "A test tells against the alternative that its frozen plan named. Set alternative_id "
                                "to the alternatives entry of that neighbour or form. An alternative that was named "
                                "after the plan gets no test credit from this run")
        knowledge = independence.get("prior_knowledge", [])
        strategy.items(knowledge, "The prior_knowledge findings of the independence of a test", code=code)
        indices = []
        for finding in knowledge:
            fields(finding, ("source_index", "status", "reason"), code=code)
            if type(finding["source_index"]) is not int or finding["status"] != "outcome_not_known":
                raise ResearchError(code,
                                    "Each source of the prior-knowledge account of the plan needs a finding "
                                    "{source_index, status: outcome_not_known, reason}. If a source shows that the "
                                    "outcome was known, record the check with the standing observation")
            text(finding["reason"], "The reason of a prior-knowledge finding", code=code)
            indices.append(finding["source_index"])
        if sorted(indices) != list(range(len(declaration["prior_knowledge"]))):
            raise ResearchError(code,
                                "Give independence.prior_knowledge exactly one finding for each entry of "
                                "test_declaration.prior_knowledge of the plan. source_index counts those entries from 0")
        search = records.get("literature_search", {}).get(declaration["search_id"])
        if search is None:
            raise ResearchError(code,
                                "The store does not hold the search that the test declaration names in search_id. "
                                "Without the search for the outcome in print, record the check with the standing "
                                "observation")
        # Exact repeated quantities are mechanically known; semantic overlap of
        # other questions and the plan's prose knowledge account are reviewed.
        for earlier in records.get("execution", {}).values():
            if earlier["recorded_revision"] >= plan["planned_revision"]:
                continue
            earlier_plan = records.get("cycle_plan", {}).get(earlier["payload"]["cycle_id"])
            if earlier_plan is not None and earlier_plan["payload"]["question"] == plan["payload"]["question"]:
                raise ResearchError(code,
                                    "An earlier run of this record asked the question of this plan, so the outcome "
                                    "was known. Record the check with the standing observation")
        for probe in records.get("probe", {}).values():
            if probe.get("revision", plan["planned_revision"]) < plan["planned_revision"] and probe.get("question") == plan["payload"]["question"]:
                raise ResearchError(code,
                                    "An earlier probe of this record asked the question of this plan, so the outcome "
                                    "was known. Record the check with the standing observation")
        assessments = [row for row in records.get("cycle_assessment", {}).values()
                       if reference["execution_id"] in row["payload"]["execution_ids"] and
                       (revision is None or row["assessed_revision"] <= revision)]
        assessment = max(assessments, key=lambda row: row["assessed_revision"], default=None)
        if assessment is None:
            raise ResearchError(code,
                                "No assessment covers this run. Assess the cycle with assess, with a status for each "
                                "failure signal of the plan. Then record the value again")
        signals = {row["id"] for row in plan["payload"]["failure_signals"]}
        failures = assessment["payload"]["failures"]
        if entry["passed"] and (not signals or any(not any(row["signal_id"] == identifier and row["status"] == "not_observed"
                                                          for row in failures) for identifier in signals)):
            raise ResearchError(code,
                                "A passed test needs each failure signal of its plan assessed as not_observed. "
                                "Record passed false for a test with an observed or unresolved signal. A failed test "
                                "stays with its text")
        require_prior_form(records, statement, plan["planned_revision"])


def validate_tier_two(records, value, accounts, entries, alternatives):
    from .problem_claims import find_current_statement
    statement = find_current_statement(records)
    checks = {entry["id"]: entry for entry in accounts.get("checks", [])}
    passed = any(entry.get("standing") == "test" and entry.get("passed") is True for entry in checks.values())
    passed = passed or any(entry["kind"]["kind"] == "passed_test" for entry in accounts["answer_evidence"])
    gain = accounts.get("gain")
    if (passed or gain is not None) and (not {"checks", "gain", "search_coverage"} <= accounts.keys()
                                         or not accounts["search_coverage"]):
        raise ResearchError("problem_accounts_tier_two_missing",
                            "A gain claim and a passed test need the second tier of the accounts. "
                            "Give checks, gain (null when the record claims no gain) and a nonempty search_coverage")
    for entry in checks.values():
        fields(entry, ("id", "target", "contradiction", "no_contradiction_reason", "result", "standing", "passed",
                       "alternative_id", "evidence", "evidence_ids", "independence"), ("date", "lineage"))
        fields(entry["target"], ("kind", "description"))
        strategy.choice(entry["target"]["kind"], ("instance", "family", "consequence", "asymptotic_regime"), "The kind of the target of a check", "invalid_input")
        text(entry["target"]["description"], "The description of the target of a check")
        strategy.choice(entry["standing"], ("planned", "observation", "origin", "test"), "The standing of a check", "invalid_input")
        if type(entry["passed"]) is not bool or entry["passed"] and entry["standing"] != "test":
            raise ResearchError("invalid_input",
                                "passed is a boolean, and only a check with the standing test can have passed true. "
                                "Set passed to false for a planned check, an observation or an origin")
        if (entry["contradiction"] is None) == (entry["no_contradiction_reason"] is None):
            raise ResearchError("invalid_input",
                                "Give exactly one of contradiction and no_contradiction_reason, and set the other to "
                                "null: the outcome that would contradict the statement, or the reason that no outcome can")
        for key in ("contradiction", "no_contradiction_reason", "result"):
            if entry[key] is not None:
                text(entry[key], "The check field " + key)
        validate_fact(records, entry, entries, exploratory=entry["standing"] != "test")
        if entry["alternative_id"] is not None:
            text(entry["alternative_id"], "The alternative_id of a check")
            resolve_entry(entries, entry["alternative_id"])
        if "lineage" in entry:
            fields(entry["lineage"], ("form_id", "check_id"))
            for key in ("form_id", "check_id"):
                text(entry["lineage"][key], "The lineage field " + key)
        if entry["standing"] == "test":
            if entry["contradiction"] is None or entry["result"] is None:
                raise ResearchError("problem_test_run_invalid",
                                    "A check with the standing test needs contradiction, the outcome that would "
                                    "contradict the statement, and result, the outcome of the run. A check that no outcome "
                                    "can contradict is an observation")
            validate_test(records, entry, entries, alternatives, statement, value.get("recorded_revision"))
    for entry in accounts["answer_evidence"]:
        if entry["kind"]["kind"] == "passed_test":
            check = checks.get(entry.get("check_id"))
            if check is None or check["standing"] != "test" or not check["passed"] or check["alternative_id"] != entry["alternative_id"]:
                raise ResearchError("problem_test_run_invalid",
                                    "An answer_evidence entry of the kind passed_test names in check_id a "
                                    "check with the standing test and passed true, and both name the same alternative_id. "
                                    "An agreement without such a check is an observation and no evidence for the answer: "
                                    "remove the entry, and keep the check in checks")
    for coverage in accounts.get("search_coverage", []):
        fields(coverage, ("id", "purpose", "search_ids", "unreached"))
        strategy.choice(coverage["purpose"], ("earlier_statements", "alternatives", "gathered_as_case"), "The purpose of a search_coverage entry", "invalid_input")
        text(coverage["unreached"], "The unreached field of a search_coverage entry")
        strings(coverage["search_ids"], "The search_ids of a search_coverage entry", nonempty=True)
        for identifier in coverage["search_ids"]:
            strategy.get_record(records, "literature_search", identifier, code="problem_account_search_missing")
    if gain is None:
        return
    strategy.choice(gain["form"], ("common_structure", "explanatory_relation", "proved_advantage"), "The form of the gain", "invalid_input")
    text(gain["claim"], "The claim of the gain")
    strings(gain["relation_ids"], "The relation_ids of the gain", nonempty=True)
    gathered = [resolve_entry(entries, identifier) for identifier in gain["relation_ids"]]
    if (any(entry not in accounts["relations"] or entry["standing"] != "established" for entry in gathered)
            or not any((entry["consequence"] or {}).get("credited_to") == "statement" for entry in gathered)):
        raise ResearchError("problem_gain_relations_invalid",
                            "The gain gathers established relations, and at least one of them is a "
                            "consequence that is credited to the statement. Name such relations in gain.relation_ids, or "
                            "record gain as null")
    for ground in gain["separate_grounds"]:
        fields(ground, ("id", "relation_id", "evidence_ids"))
        if ground["relation_id"] not in gain["relation_ids"]:
            raise ResearchError("problem_gain_relations_invalid",
                                "A separate_grounds entry names in relation_id a relation that "
                                "gain.relation_ids does not hold. Name a gathered relation")
        strings(ground["evidence_ids"], "The evidence_ids of a separate_grounds entry", nonempty=True)
        for identifier in ground["evidence_ids"]:
            if not collect_entry_evidence(entries, identifier):
                raise ResearchError("problem_gain_evidence_missing",
                                    "A separate_grounds entry cites an entry without evidence. Cite in "
                                    "evidence_ids an entry whose evidence is the work that states or studies the gathered "
                                    "statement for reasons of its own")
    conjunction = gain["conjunction"]
    strategy.choice(conjunction["status"], ("proved_equivalent", "proved_stronger", "not_known"), "The status of gain.conjunction", "invalid_input")
    text(conjunction["description"], "The description of gain.conjunction")
    strings(conjunction["evidence_ids"], "The evidence_ids of gain.conjunction")
    conjunction_evidence = [reference for identifier in conjunction["evidence_ids"] for reference in collect_entry_evidence(entries, identifier)]
    if conjunction["status"] != "not_known" and not conjunction_evidence:
        raise ResearchError("problem_gain_conjunction_evidence_missing",
                            "A relation to the conjunction with the status proved_equivalent "
                            "or proved_stronger needs evidence. Cite in conjunction.evidence_ids an entry that holds the "
                            "proof, or record the status not_known")
    strings(gain["establishing_ids"], "The establishing_ids of the gain", nonempty=True)
    establishing = [reference for identifier in gain["establishing_ids"] for reference in collect_entry_evidence(entries, identifier)]
    if not establishing or any(reference["kind"] == "probe" for reference in establishing):
        raise ResearchError("problem_gain_evidence_missing",
                            "No admissible evidence establishes the gain in its claimed form. Cite in "
                            "gain.establishing_ids entries whose evidence is a full reading, an admitted computation or "
                            "an accepted result. A probe establishes no gain. Without such evidence, record gain as null")
    if gain["new_object_justification"] is not None:
        text(gain["new_object_justification"], "The new_object_justification of the gain")
    revisions, dates = [], []
    for reference in establishing:
        revision = None
        if reference["kind"] == "result":
            revision = find_result_revision(records, reference)
        elif reference["kind"] == "computation":
            revision = records["execution"][reference["execution_id"]]["recorded_revision"]
        elif reference["kind"] == "reading":
            revision = records["reading"][reference["reading_id"]].get("recorded_revision")
        date = find_evidence_availability(records, reference)
        if date is None and revision is None:
            date = min((entry["date"] for entry in entries.values() if reference in entry.get("evidence", [])
                        and entry.get("date") not in (None, "unknown")), default=None)
        if revision is None and date is None:
            require_prior_form(records, statement, None)
        if revision is not None:
            revisions.append(revision)
        if date is not None:
            dates.append(date)
    require_prior_form(records, statement, min(revisions) if revisions else None, min(dates) if dates else None)


def validate_replacement(records, artifacts, accounts, entries):
    from .problem_claims import find_current_statement
    from .problem_forming import collect_forms
    forms = collect_forms(records)
    pending = list(collect_candidate_forms(records, find_current_statement(records)))
    lineage = set()
    while pending:
        identifier = pending.pop()
        if identifier in lineage:
            continue
        lineage.add(identifier)
        previous = forms[identifier].get("previous")
        if previous is not None:
            pending.append(previous["form_id"])
    refuted = {row["form_id"] for row in records.get("form_outcome", {}).values()
               if row["target"]["kind"] == "form" and row["standing"] == "refuted"}
    required_forms = {identifier for identifier in lineage & refuted if forms[identifier]["purpose"]["kind"] == "expected_statement"}
    conjectures = {entry["id"] for entry in accounts["refuted_conjectures"]}
    for entry in accounts["refuted_conjectures"]:
        fields(entry, ("id", "statement", "identified_by", "sources", "evidence_ids"))
        text(entry["statement"], "The statement of a refuted_conjectures entry")
        strategy.choice(entry["identified_by"], ("author", "search", "reviewer"), "The identified_by of a refuted_conjectures entry", "invalid_input")
        strategy.evidence(records, artifacts, entry["sources"], required=entry["identified_by"] != "author")
        strings(entry["evidence_ids"], "The evidence_ids of a refuted_conjectures entry")
        for identifier in entry["evidence_ids"]:
            resolve_entry(entries, identifier)
    covered_forms, covered_conjectures = set(), set()
    for entry in accounts.get("replacement", []):
        fields(entry, ("id", "refuted_id", "form_id", "evidence_ids", "account"))
        text(entry["account"], "The account of a replacement entry")
        strings(entry["evidence_ids"], "The evidence_ids of a replacement entry")
        evidence = collect_entry_evidence(entries, entry["id"])
        if not evidence or any(reference["kind"] not in ("reading", "computation", "result") for reference in evidence):
            raise ResearchError("problem_replacement_evidence_missing",
                                "A replacement entry needs admissible evidence of the refutation. Cite in "
                                "evidence_ids entries whose evidence is a full reading, an admitted computation or an "
                                "accepted result. For a refutation that rests on a probe, run a bounded solver "
                                "objective on the refuted form first")
        if (entry["refuted_id"] is None) == (entry["form_id"] is None):
            raise ResearchError("invalid_input",
                                "A replacement entry names exactly one refuted statement. Set refuted_id to the ID of a "
                                "refuted_conjectures entry and form_id to null, or set form_id to a refuted form and "
                                "refuted_id to null")
        if entry["refuted_id"] is not None:
            text(entry["refuted_id"], "The refuted_id of a replacement entry")
            if entry["refuted_id"] not in conjectures:
                raise ResearchError("problem_account_reference_missing",
                                    "refuted_id names no refuted_conjectures entry. Add the refuted "
                                    "conjecture to refuted_conjectures, or name an entry that the accounts hold")
            covered_conjectures.add(entry["refuted_id"])
        else:
            text(entry["form_id"], "The form_id of a replacement entry")
            if entry["form_id"] not in refuted:
                raise ResearchError("problem_form_missing",
                                    "form_id names no form that an outcome of this record gives the standing refuted. "
                                    "Record that outcome with form-outcome first, or name a refuted form")
            covered_forms.add(entry["form_id"])
    if required_forms - covered_forms or conjectures - covered_conjectures:
        raise ResearchError("problem_replacement_missing",
                            "The statement is posed in place of a refuted one, and the accounts hold no "
                            "replacement entry for it. Add one replacement entry for each form in form_ids, which the "
                            "record held as its expected statement in the correction chain of the statement, and for "
                            "each refuted conjecture in conjecture_ids",
                            {"form_ids": sorted(required_forms - covered_forms), "conjecture_ids": sorted(conjectures - covered_conjectures)})


def describe_account_records(records, accounts, derived=None):
    """Deliver the exact searches, plans and forming provenance cited by accounts."""
    from .problem_evidence import cite_record
    held = {}
    for part in ("search_coverage", "alternatives"):
        for entry in accounts.get(part, []):
            for identifier in entry.get("search_ids", []):
                held[("literature_search", identifier)] = records["literature_search"][identifier]
    for reference in collect_account_evidence(accounts):
        if reference["kind"] == "computation":
            execution = records["execution"][reference["execution_id"]]
            identifier = execution["payload"]["cycle_id"]
            plan = records["cycle_plan"][identifier]
            held[("cycle_plan", identifier)] = plan
            declaration = plan["payload"].get("test_declaration")
            if declaration is not None:
                identifier = declaration["search_id"]
                held[("literature_search", identifier)] = records["literature_search"][identifier]
    for entry in accounts["refuted_conjectures"]:
        for reference in entry["sources"]:
            if reference.get("kind") == "record":
                held[(reference["record_kind"], reference["id"])] = records[reference["record_kind"]][reference["id"]]
    form_ids = {entry["form_id"] for part in ("alternatives", "replacement") for entry in accounts.get(part, [])
                if entry["form_id"] is not None}
    form_ids.update(entry["lineage"]["form_id"] for entry in accounts.get("checks", []) if "lineage" in entry)
    for identifier, call in records.get("forming_call", {}).items():
        if any(form["id"] in form_ids for form in call["forms"]):
            held[("forming_call", identifier)] = call
    for identifier, outcome in records.get("form_outcome", {}).items():
        if outcome["form_id"] in form_ids:
            held[("form_outcome", identifier)] = outcome
    if derived is not None:
        for row in derived["history"]:
            identifier = row["forming_call_id"]
            held[("forming_call", identifier)] = records["forming_call"][identifier]
        history_ids = {row["form"]["id"] for row in derived["history"]}
        for identifier, call in records.get("forming_call", {}).items():
            if any(probe["form_id"] in history_ids for probe in call["probes"]):
                held[("forming_call", identifier)] = call
        for identifier, outcome in records.get("form_outcome", {}).items():
            if outcome["form_id"] in history_ids:
                held[("form_outcome", identifier)] = outcome
        for row in derived["checks"]["current"] + derived["checks"]["earlier"]:
            identifier = row["plan_id"]
            plan = records["cycle_plan"][identifier]
            held[("cycle_plan", identifier)] = plan
            for kind, identifier in (("execution", row["execution_id"]), ("cycle_assessment", row["assessment_id"])):
                if identifier is not None:
                    held[(kind, identifier)] = records[kind][identifier]
            declaration = plan["payload"].get("test_declaration")
            if declaration is not None:
                identifier = declaration["search_id"]
                held[("literature_search", identifier)] = records["literature_search"][identifier]
            for earlier in row.get("earlier_computations", []):
                kind, identifier = earlier["kind"], earlier["id"]
                held[(kind, identifier)] = records[kind][identifier]
                if kind == "execution":
                    identifier = records[kind][identifier]["payload"]["cycle_id"]
                    held[("cycle_plan", identifier)] = records["cycle_plan"][identifier]
    searches = [row for (kind, _), row in held.items() if kind == "literature_search"]
    for search in searches:
        for response in search["responses"]:
            identifier = response["source_id"]
            held[("source", identifier)] = records["source"][identifier]
    return [{"record": record, "cite": cite_record(kind, identifier, record)} for (kind, identifier), record in sorted(held.items())]


def validate_paragraph(records, artifacts, paragraph, entries):
    from .problem_evidence import validate_reading
    fields(paragraph, ("text", "evidence"), ("field_fact",))
    text(paragraph["text"], "The text of a value paragraph")
    if "field_fact" in paragraph and type(paragraph["field_fact"]) is not bool:
        raise ResearchError("invalid_input",
                            "Set field_fact to true for a paragraph that states a fact about the field, such as the "
                            "activity of a line, and leave field_fact out otherwise")
    cited = []
    for reference in strategy.items(paragraph["evidence"], "The evidence of a value paragraph", nonempty=True, code="invalid_input"):
        if isinstance(reference, dict) and reference.get("kind") == "account":
            fields(reference, ("kind", "id"))
            cited.append(resolve_entry(entries, reference["id"]))
        elif isinstance(reference, dict) and reference.get("kind") == "reading" and paragraph.get("field_fact") is True:
            text(reference.get("reading_id"), "The reading_id of a reading reference")
            text(reference.get("version_id"), "The version_id of a reading reference")
            validate_reading(records, artifacts, reference)
        else:
            raise ResearchError("problem_value_account_required",
                                "A value paragraph cites entries of the accounts as {kind: account, id}. "
                                "Enter the fact in its account and cite that entry. Only a paragraph with field_fact true "
                                "can cite a full reading directly, for a fact about the field that no account holds")
    if cited and all(entry.get("relation") in RELATION_KINDS and entry["standing"] == "expected" for entry in cited):
        raise ResearchError("problem_value_expected_only",
                            "Every entry that this paragraph cites is an expected relation, and an "
                            "expected relation supports no premise. Establish a relation with a proof, a full reading or "
                            "an admitted computation, cite an entry that holds a fact, or remove the paragraph")
