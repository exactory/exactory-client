"""Ideate records of how tentative statements were formed and how they ended.

Forms grant no claim or test credit. Counts and model usage are the performer's
report; probes retain their exploratory provenance, including counterexamples.
"""

from datetime import datetime, timezone

from . import problem_evidence, strategy
from .errors import ResearchError
from .operations import fields, immutable_record, prepared_mutation, strings, text, timestamp
from .problem_claims import require_problem


CLAIM_STANDINGS = ("refuted", "proved", "implied_by_known_results", "found_in_literature", "supported",
                   "unresolved", "without_evidence", "not_examined")
FORM_DISPOSITIONS = ("open", "superseded", "taken_up_as_candidate", "set_aside")


def collect_forms(records):
    return {form["id"]: form for call in records.get("forming_call", {}).values() for form in call["forms"]}


def find_form(forms, identifier):
    text(identifier, "Form ID")
    if identifier not in forms:
        raise ResearchError("problem_form_missing", "Name a form held in the problem record", {"form_id": identifier})
    return forms[identifier]


def validate_count(value, name):
    if type(value) is not int or value < 0:
        raise ResearchError("invalid_input", name + " must be a nonnegative integer")


def validate_description(value):
    fields(value, ("kind", "description"))
    text(value["kind"], "Kind")
    text(value["description"], "Description")


def validate_grounds(records, artifacts, values, *, required=False):
    for item in strategy.items(values, "Grounds", code="invalid_input"):
        fields(item, ("description", "evidence"))
        text(item["description"], "Ground description")
        strategy.evidence(records, artifacts, item["evidence"], required=required)


def validate_checks(values):
    identifiers = []
    for check in strategy.items(values, "Planned checks", code="invalid_input"):
        fields(check, ("id", "description", "contradiction", "no_contradiction_reason"))
        text(check["id"], "Check ID")
        text(check["description"], "Planned check")
        identifiers.append(check["id"])
        if (check["contradiction"] is None) == (check["no_contradiction_reason"] is None):
            raise ResearchError("invalid_input", "Give the contradicting outcome or the reason that no outcome can contradict the form")
        for key in ("contradiction", "no_contradiction_reason"):
            if check[key] is not None:
                text(check[key], "Check " + key)
    strings(identifiers, "Check IDs")


def validate_correction(form, earlier_forms):
    checks = strategy.items(form["correction_checks"], "Correction checks", code="invalid_input")
    previous = form["previous"]
    if previous is None:
        if checks:
            raise ResearchError("invalid_input", "Correction checks require an earlier form")
        return
    fields(previous, ("form_id", "reason"))
    text(previous["reason"], "Correction reason")
    earlier = find_form(earlier_forms, previous["form_id"])
    identifiers = []
    for check in checks:
        fields(check, ("check_id", "was_made", "keeps_prediction", "chosen_with_outcome"))
        text(check["check_id"], "Earlier check ID")
        identifiers.append(check["check_id"])
        for key in ("was_made", "keeps_prediction", "chosen_with_outcome"):
            if type(check[key]) is not bool:
                raise ResearchError("invalid_input", "Correction check " + key + " must be a boolean")
    if (len(identifiers) != len(set(identifiers))
            or set(identifiers) != {check["id"] for check in earlier["planned_checks"]}):
        raise ResearchError("problem_form_correction_incomplete", "Account for every planned check of the earlier form exactly once")


def validate_form(records, artifacts, form, forms, earlier_forms):
    fields(form, ("id", "text", "exact", "purpose", "previous", "grounds", "nearest_alternatives",
                  "expected_relations", "planned_checks", "correction_checks"))
    text(form["text"], "Form text")
    if type(form["exact"]) is not bool:
        raise ResearchError("invalid_input", "Form exact must be a boolean")
    purpose = form["purpose"]
    fields(purpose, ("kind", "form_id"))
    strategy.choice(purpose["kind"], ("expected_statement", "alternative"), "Form purpose", "invalid_input")
    if purpose["kind"] == "alternative":
        find_form(forms, purpose["form_id"])
    elif purpose["form_id"] is not None:
        raise ResearchError("invalid_input", "An expected statement has no purpose form_id")
    validate_grounds(records, artifacts, form["grounds"])
    strings(form["nearest_alternatives"], "Nearest alternative form IDs")
    for identifier in form["nearest_alternatives"]:
        find_form(forms, identifier)
    if form["id"] in form["nearest_alternatives"] or purpose["form_id"] == form["id"]:
        raise ResearchError("invalid_input", "A form cannot be its own alternative")
    relation_ids = []
    for relation in strategy.items(form["expected_relations"], "Expected relations", code="invalid_input"):
        fields(relation, ("id", "description", "form_ids"))
        text(relation["id"], "Expected relation ID")
        text(relation["description"], "Expected relation")
        relation_ids.append(relation["id"])
        strings(relation["form_ids"], "Related form IDs")
        for identifier in relation["form_ids"]:
            find_form(forms, identifier)
    strings(relation_ids, "Expected relation IDs")
    validate_checks(form["planned_checks"])
    validate_correction(form, earlier_forms)


def validate_performer(artifacts, performer):
    fields(performer, ("kind",), ("session_id", "lane_id", "model", "effort", "prompt", "output"))
    strategy.choice(performer["kind"], ("session", "model_lane"), "Performer kind", "invalid_input")
    if performer["kind"] == "session":
        fields(performer, ("kind", "session_id"))
        text(performer["session_id"], "Performer session ID")
    else:
        fields(performer, ("kind", "lane_id", "model", "effort", "prompt", "output"))
        for key in ("lane_id", "model", "effort"):
            text(performer[key], "Performer " + key)
        for key in ("prompt", "output"):
            fields(performer[key], ("path", "sha256", "size", "media_type"))
            artifacts.read(performer[key])


def record_forming(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "question", "move", "inputs", "amounts", "dictionary", "comparison_count", "forms",
                       "probes", "stopping_reason", "performer", "usage"))
        text(value["id"], "Forming call ID")
        record = dict(value, recorded_revision=expected_revision + 1, recorded_at=datetime.now(timezone.utc).isoformat())
        change = immutable_record(records, "forming_call", value["id"], record)
        text(value["stopping_reason"], "Stopping reason")
        question = value["question"]
        fields(question, ("kind", "description", "places"))
        text(question["kind"], "Question kind")
        text(question["description"], "Question")
        if question["kind"] == "phenomenon" and not question["places"]:
            raise ResearchError("invalid_input", "A phenomenon question requires its places with evidence")
        validate_grounds(records, artifacts, question["places"], required=True)
        validate_description(value["move"])
        validate_grounds(records, artifacts, value["inputs"], required=True)
        earlier_forms = collect_forms(records)
        forms = dict(earlier_forms)
        produced = strategy.items(value["forms"], "Forms", code="invalid_input")
        for form in produced:
            fields(form, ("id",), None)
            text(form["id"], "Form ID")
            if form["id"] in forms:
                raise ResearchError("problem_form_id_conflict", "Each form ID must be unique in the problem record", {"form_id": form["id"]})
            forms[form["id"]] = form
        for form in produced:
            validate_form(records, artifacts, form, forms, earlier_forms)
            earlier_forms[form["id"]] = form
        amounts = value["amounts"]
        fields(amounts, ("forms", "nearest_alternatives", "expected_relations", "pairs"))
        validate_count(amounts["forms"], "Reported form count")
        for key in ("nearest_alternatives", "expected_relations"):
            fields(amounts[key], [form["id"] for form in produced])
            for count in amounts[key].values():
                validate_count(count, "Reported " + key + " count")
        if amounts["pairs"] is not None:
            validate_count(amounts["pairs"], "Reported pair count")
        dictionary = value["dictionary"]
        if dictionary is not None:
            fields(dictionary, ("source_setting", "target_setting", "entries"))
            for key in ("source_setting", "target_setting"):
                text(dictionary[key], "Transfer " + key)
            for entry in strategy.items(dictionary["entries"], "Transfer dictionary", nonempty=True, code="invalid_input"):
                fields(entry, ("source", "target", "description"))
                for key in ("source", "target", "description"):
                    text(entry[key], "Dictionary " + key)
        if value["move"]["kind"] == "transfer" and dictionary is None:
            raise ResearchError("invalid_input", "A transfer requires its dictionary")
        if value["comparison_count"] is not None:
            validate_count(value["comparison_count"], "Comparison count")
        if value["move"]["kind"] == "comparison" and (value["comparison_count"] is None or amounts["pairs"] is None):
            raise ResearchError("invalid_input", "A comparison requires the reported pair count and comparison count")
        for probe in strategy.items(value["probes"], "Used probes", code="invalid_input"):
            fields(probe, ("probe_id", "form_id", "role"))
            strategy.choice(probe["role"], ("observation", "origin"), "Probe role", "invalid_input")
            observed = strategy.get_record(records, "probe", probe["probe_id"])
            if probe["form_id"] is not None:
                find_form(forms, probe["form_id"])
                origin = next((call for call in records.get("forming_call", {}).values()
                               if any(form["id"] == probe["form_id"] for form in call["forms"])), None)
                if probe["role"] == "origin" and origin is not None and observed["revision"] > origin["recorded_revision"]:
                    raise ResearchError("problem_form_origin_late", "A probe recorded after a form cannot be the origin of that form")
        validate_performer(artifacts, value["performer"])
        if value["usage"] is not None:
            fields(value["usage"], (), None)
            for key, amount in value["usage"].items():
                text(key, "Usage measurement")
                strategy.number(amount, "Reported usage", nullable=True, code="invalid_input")
        return [change], record

    return prepared_mutation(store, "problem.forming", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def validate_outcome_evidence(records, artifacts, values, recorded_revision):
    for item in strategy.items(values, "Outcome evidence", code="invalid_input"):
        fields(item, ("kind", "description", "standing", "date", "reference", "counterexample"))
        text(item["kind"], "Evidence kind")
        text(item["description"], "Evidence description")
        timestamp(item["date"])
        if item["counterexample"] is not None:
            text(item["counterexample"], "Counterexample")
        reference = item["reference"]
        fields(reference, ("kind",), None)
        strategy.choice(reference["kind"], ("reading", "computation", "result", "probe"), "Evidence reference kind", "invalid_input")
        expected = "exploratory" if reference["kind"] == "probe" else "admissible"
        if item["standing"] != expected:
            raise ResearchError("problem_form_evidence_standing", "A probe is exploratory; a full reading, admitted computation or accepted result is admissible")
        if reference["kind"] == "reading":
            for key in ("version_id", "reading_id"):
                text(reference.get(key), "Reading " + key)
            problem_evidence.validate_reading(records, artifacts, reference)
        elif reference["kind"] == "computation":
            for key in ("execution_id", "output_id"):
                text(reference.get(key), "Computation " + key)
            formal = reference.get("formal_check")
            if isinstance(formal, dict) and isinstance(formal.get("claim"), dict):
                text(formal["claim"].get("proof_policy"), "Native proof policy")
            problem_evidence.validate_computation(records, artifacts, reference, recorded_revision=recorded_revision)
        elif reference["kind"] == "result":
            problem_evidence.validate_result(records, reference)
        else:
            fields(reference, ("kind", "id"))
            strategy.get_record(records, "probe", reference["id"])


def validate_disposition(records, forms, form_id, value):
    fields(value, ("status", "reason", "form_id", "candidate_id"))
    strategy.choice(value["status"], FORM_DISPOSITIONS, "Form disposition", "invalid_input")
    text(value["reason"], "Disposition reason")
    if value["status"] == "superseded":
        later = find_form(forms, value["form_id"]) if value["form_id"] is not None else None
        if later is None or later["id"] == form_id:
            raise ResearchError("invalid_input", "A superseded form requires a different later form")
        positions = {form["id"]: (call["recorded_revision"], index)
                     for call in records["forming_call"].values() for index, form in enumerate(call["forms"])}
        if positions[later["id"]] <= positions[form_id]:
            raise ResearchError("invalid_input", "The superseding form must have been recorded later")
    elif value["form_id"] is not None:
        raise ResearchError("invalid_input", "Only a superseded disposition names a later form")
    if value["status"] == "taken_up_as_candidate":
        if value["candidate_id"] is None:
            raise ResearchError("invalid_input", "A taken-up form requires its candidate ID")
        strategy.get_record(records, "problem_candidate", value["candidate_id"])
    elif value["candidate_id"] is not None:
        raise ResearchError("invalid_input", "Only a taken-up disposition names a candidate")


def record_form_outcome(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "form_id", "target", "standing", "disposition", "lack_of_means", "outcome", "evidence", "reason"))
        for key in ("id", "outcome", "reason"):
            text(value[key], "Form outcome " + key)
        forms = collect_forms(records)
        form = find_form(forms, value["form_id"])
        target = value["target"]
        fields(target, ("kind", "id"))
        strategy.choice(target["kind"], ("form", "expected_relation", "planned_check"), "Outcome target", "invalid_input")
        if target["kind"] == "form":
            if target["id"] is not None:
                raise ResearchError("invalid_input", "A form outcome has no target item ID")
            strategy.choice(value["standing"], CLAIM_STANDINGS, "Claim standing", "invalid_input")
            validate_disposition(records, forms, form["id"], value["disposition"])
            if value["standing"] == "not_examined":
                text(value["lack_of_means"], "Lack of means")
            elif value["lack_of_means"] is not None:
                raise ResearchError("invalid_input", "Only a not-examined form records a lack of means")
        else:
            text(target["id"], "Outcome target item ID")
            key = "expected_relations" if target["kind"] == "expected_relation" else "planned_checks"
            if target["id"] not in {item["id"] for item in form[key]}:
                raise ResearchError("problem_form_item_missing", "Name an expected relation or planned check held by this form", target)
            if any(value[key] is not None for key in ("standing", "disposition", "lack_of_means")):
                raise ResearchError("invalid_input", "Claim standing and exploration disposition belong to a whole-form outcome")
        validate_outcome_evidence(records, artifacts, value["evidence"], expected_revision + 1)
        admissible = any(item["standing"] == "admissible" for item in value["evidence"])
        if value["standing"] in ("refuted", "proved", "implied_by_known_results", "found_in_literature", "supported"):
            if not value["evidence"] or value["standing"] not in ("refuted", "supported") and not admissible:
                raise ResearchError("problem_form_evidence_missing", "Support this claim standing with evidence; proof and literature standings require admissible evidence")
        refutation_basis = None
        if value["standing"] == "refuted":
            if not admissible and not any(item["counterexample"] is not None for item in value["evidence"]):
                raise ResearchError("problem_form_counterexample_missing", "An exploratory refutation must exhibit its counterexample")
            refutation_basis = "admissible_evidence" if admissible else "exploratory_computation"
        earlier = [row for row in records.get("form_outcome", {}).values()
                   if row["form_id"] == value["form_id"] and row["target"] == target]
        previous = max(earlier, key=lambda row: row["recorded_revision"], default=None)
        record = dict(value, previous=None if previous is None else previous["id"], refutation_basis=refutation_basis,
                      recorded_revision=expected_revision + 1, recorded_at=datetime.now(timezone.utc).isoformat())
        return [immutable_record(records, "form_outcome", value["id"], record)], record

    return prepared_mutation(store, "problem.form_outcome", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def count_forming(records):
    forms = collect_forms(records)
    standings = dict.fromkeys(CLAIM_STANDINGS, 0)
    dispositions = dict.fromkeys(FORM_DISPOSITIONS, 0)
    latest = {row["form_id"]: row for row in sorted(records.get("form_outcome", {}).values(), key=lambda row: row["recorded_revision"])
              if row["target"]["kind"] == "form"}
    for identifier in forms:
        outcome = latest.get(identifier)
        if outcome is not None:
            standings[outcome["standing"]] += 1
            dispositions[outcome["disposition"]["status"]] += 1
    return {"calls": len(records.get("forming_call", {})), "forms": len(forms), "by_standing": standings,
            "by_disposition": dispositions, "without_outcome": len(forms) - len(latest),
            "outcomes": len(records.get("form_outcome", {}))}
