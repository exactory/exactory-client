"""Compute an exploration's report without changing its scientific gates."""

import json

from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, text
from .problem_accounts import collect_entries, collect_entry_evidence
from .problem_candidates import find_current_slate, find_slate
from .problem_claims import compute_statement_digest, find_latest, require_problem
from .problem_forming import count_forming
from .review_budget import CHARGE_KINDS
from .review_protocol import usage_report


# Keep the complete kinds, including superseded evidence. A report accounts for
# the exploration, rather than just the evidence of its current statement.
DEPENDENCY_KINDS = (
    "workspace", "problem_area", "forming_call", "form_outcome", "problem_forming_import", "forming_transfer",
    "problem_candidate", "problem_slate", "problem_statement", "problem_selection",
    "problem_value", "problem_decision", "problem_post", "problem_post_time",
    "problem_post_fact", "value_review", "review_adjudication", "review_assignment",
    "review_epoch", "problem_attempt", "native_attempt", "native_attempt_current",
    "cycle_plan", "execution", "cycle_assessment", "probe", "resource_account",
    "resource_budget", "strategy_account", "review_observation", "review_budget",
) + CHARGE_KINDS
OPEN_FORMS_MESSAGE = (
    "The report is due, and the forms named in form_ids have no outcome or the disposition open. Record a "
    "form-outcome for each, with the disposition superseded, taken_up_as_candidate or set_aside, then record the report."
)
MISSING_EVIDENCE_MESSAGE = (
    "The post named in post_id passed the gate, and no saved review packet of this store delivers the value record "
    "that its evidence digest covered, so the report cannot read its classification at the post. Restore the store "
    "from a copy that holds those packets and that value record unchanged, then record the report."
)
PROOF_PUBLICATION_NOTE = "The record does not hold whether the proofs of these consequences were posted."
GAIN_CLASSIFICATION = "a gain was established"
ORDINARY_CLASSIFICATION = "ordinary"
UNASSESSED_CLASSIFICATION = "kind unassessed"
POST_CLASSIFICATIONS = (GAIN_CLASSIFICATION, ORDINARY_CLASSIFICATION, UNASSESSED_CLASSIFICATION)


def order_records(records, kind):
    return sorted(records.get(kind, {}).values(), key=lambda row: (row.get("recorded_revision", 0), row["id"]))


def project_dependencies(records):
    return {kind: dict(records.get(kind, {})) for kind in DEPENDENCY_KINDS}


def describe_forms(records):
    latest = {}
    for outcome in order_records(records, "form_outcome"):
        latest[(outcome["form_id"], outcome["target"]["kind"], outcome["target"]["id"])] = outcome
    forms = []
    for call in order_records(records, "forming_call"):
        for form in call["forms"]:
            outcome = latest.get((form["id"], "form", None))
            forms.append(dict(form=form, forming_call_id=call["id"], move=call["move"],
                standing="without_evidence" if outcome is None else outcome["standing"],
                disposition={"status": "open"} if outcome is None else outcome["disposition"],
                outcome=outcome, evidence=[] if outcome is None else outcome["evidence"],
                lack_of_means=None if outcome is None else outcome["lack_of_means"],
                item_outcomes=[row for (identifier, kind, _), row in latest.items()
                               if identifier == form["id"] and kind != "form"]))
    return forms


def describe_withdrawals(records):
    withdrawals = []
    for decision in order_records(records, "problem_decision"):
        if decision["action"] != "withdraw":
            continue
        target_recorded = "candidate_ids" in decision or "statement_id" in decision
        if "candidate_ids" in decision:
            candidates = decision["candidate_ids"]
        elif decision.get("statement_id") is not None:
            candidate = records["problem_statement"][decision["statement_id"]].get("candidate_id")
            candidates = [] if candidate is None else [candidate]
        else:
            # A stored withdrawal has no target. It can identify a candidate
            # only when every possible earlier selection names that candidate.
            earlier = {row.get("candidate_id") for row in records.get("problem_statement", {}).values()
                       if row["recorded_revision"] < decision["recorded_revision"]}
            candidates = sorted(earlier) if len(earlier) == 1 and None not in earlier else []
        withdrawals.append(dict(decision=decision, candidate_ids=candidates, target_recorded=target_recorded))
    return withdrawals


def is_recorded_before_slate(row, slate):
    """A row recorded before the latest slate, which decides its candidates again."""
    return slate is not None and row["recorded_revision"] < slate["recorded_revision"]


def describe_candidate_endings(records):
    slate = find_slate(records)
    withdrawals = describe_withdrawals(records)
    dispositions = {}
    if slate is not None:
        dispositions.update((identifier, dict(status="selected")) for identifier in slate["selected"])
        for key in ("deferred", "rejected"):
            dispositions.update((row["candidate_id"], dict(row, status=key)) for row in slate[key])
    endings = []
    for candidate in order_records(records, "problem_candidate"):
        statements = [row for row in order_records(records, "problem_statement")
                      if row.get("candidate_id") == candidate["id"]]
        posted = [post["id"] for post in order_records(records, "problem_post")
                  if any(post["statement_digest"] == compute_statement_digest(statement) for statement in statements)]
        transfers = [row for row in order_records(records, "forming_transfer")
                     if candidate["id"] == row["candidate_id"] and not is_recorded_before_slate(row, slate)]
        withdrawn = []
        for withdrawal in withdrawals:
            decision = withdrawal["decision"]
            if is_recorded_before_slate(decision, slate):
                continue
            if candidate["id"] in withdrawal["candidate_ids"]:
                withdrawn.append(decision["id"])
        endings.append(dict(candidate=candidate, slate_id=None if slate is None else slate["id"],
            disposition=dispositions.get(candidate["id"]), post_ids=posted, withdrawal_ids=withdrawn,
            transfers=transfers, ended=bool(posted or withdrawn or transfers),
            ending="posted" if posted else "withdrawn" if withdrawn else "taken up in another record" if transfers else None))
    return endings


def is_report_due(records, candidates=None):
    marker = records.get("workspace", {}).get("problem", {})
    applies = marker.get("start", {}).get("kind") in ("phenomenon", "thought_experiment") or bool(records.get("forming_call"))
    slate = find_current_slate(records)
    if not applies or slate is None:
        return False
    if not slate["selected"]:
        return True
    candidates = describe_candidate_endings(records) if candidates is None else candidates
    ended = {row["candidate"]["id"] for row in candidates if row["ended"]}
    return all(identifier in ended for identifier in slate["selected"])


def find_covered_value(records, artifacts, post):
    identity = post.get("problem_evidence_digest")
    frames = [epoch["manifest"]["frame"] for epoch in records.get("review_epoch", {}).values()]
    for assignment in order_records(records, "review_assignment"):
        frames.append(json.loads(artifacts.read(assignment["packet"])))
    for frame in frames:
        if frame.get("problem_evidence_digest") != identity or identity is None:
            continue
        value = frame.get("problem_evidence", {}).get("value")
        if value is None:
            continue
        saved = records.get("problem_value", {}).get(value["id"])
        if saved is not None and digest(saved) == value["digest"]:
            return saved
    raise ResearchError("problem_report_evidence_missing", MISSING_EVIDENCE_MESSAGE, {"post_id": post["id"]})


def describe_consequences(values):
    consequences = {}
    for value in values:
        accounts = value.get("accounts")
        if accounts is None:
            continue
        entries = {entry["id"]: entry for entry in collect_entries(accounts)}
        for relation in accounts["relations"]:
            if relation["standing"] != "established" or relation["consequence"] is None:
                continue
            references = [reference for reference in collect_entry_evidence(entries, relation["id"])
                          if reference["kind"] == "result"]
            if references:
                row = dict(relation=relation, result_references=references)
                consequences[digest(row)] = dict(row, value_id=value["id"])
    return list(consequences.values())


def describe_statements(records, artifacts):
    statements = []
    for statement in order_records(records, "problem_statement"):
        posts = []
        statement_digest = compute_statement_digest(statement)
        versions = {row["id"] for row in records["problem_statement"].values()
                    if compute_statement_digest(row) == statement_digest}
        values = [row for row in order_records(records, "problem_value") if row["statement_id"] in versions]
        for post in order_records(records, "problem_post"):
            if post["statement_digest"] != statement_digest:
                continue
            passed = post.get("gate") != "bypassed"
            covered = find_covered_value(records, artifacts, post) if passed else None
            classification = UNASSESSED_CLASSIFICATION
            if covered is not None and "accounts" in covered:
                classification = GAIN_CLASSIFICATION if covered["accounts"].get("gain") is not None else ORDINARY_CLASSIFICATION
            facts = [row for row in order_records(records, "problem_post_fact") if row["post_id"] == post["id"]]
            revision = post.get("recorded_revision")
            if revision is None:
                revision = records.read_creation_revision("problem_post", post["id"])
            time = records.get("problem_post_time", {}).get(post["id"])
            later_reviews = []
            for kind in ("value_review", "review_adjudication"):
                for row in order_records(records, kind):
                    if row["recorded_revision"] <= revision:
                        continue
                    subject = row.get("subject", {})
                    reviewed = records.get("problem_statement", {}).get(subject.get("statement_id"))
                    if reviewed is not None and compute_statement_digest(reviewed) == post["statement_digest"]:
                        later_reviews.append(dict(kind=kind, record=row))
            posts.append(dict(post=post, passed=passed, classification=classification,
                covered_value_id=None if covered is None else covered["id"],
                problem_evidence_digest=post.get("problem_evidence_digest"),
                posted_at=post.get("posted_at") if time is None else time["posted_at"],
                time_statement=time, has_post_fact=bool(facts),
                after_post=dict(values=[row for row in values if row["recorded_revision"] > revision],
                                facts=facts, reviews=later_reviews),
                consequences=describe_consequences(values), proof_publication=PROOF_PUBLICATION_NOTE))
        statements.append(dict(statement=statement, publication=("not posted" if not posts else
            "posted through the gate" if posts[0]["passed"] else "posted outside the gate"), posts=posts))
    return statements


def describe_tests(records):
    runs = {}
    for value in order_records(records, "problem_value"):
        accounts = value.get("accounts")
        if accounts is None:
            continue
        entries = {entry["id"]: entry for entry in collect_entries(accounts)}
        for check in accounts.get("checks", []):
            if check["standing"] != "test":
                continue
            for reference in collect_entry_evidence(entries, check["id"]):
                if reference["kind"] != "computation":
                    continue
                identifier = reference["execution_id"]
                assessment = max((row for row in records.get("cycle_assessment", {}).values()
                                  if identifier in row["payload"]["execution_ids"]),
                                 key=lambda row: row["assessed_revision"], default=None)
                plan = records["cycle_plan"][records["execution"][identifier]["payload"]["cycle_id"]]
                statuses = {} if assessment is None else {row["signal_id"]: row["status"] for row in assessment["payload"]["failures"]}
                signals = [statuses.get(row["id"], "unresolved") for row in plan["payload"]["failure_signals"]]
                failed = "observed" in signals
                passed = check["passed"] and signals and all(status == "not_observed" for status in signals)
                runs[identifier] = dict(execution_id=identifier, value_id=value["id"], check=check,
                    outcome="failed" if failed else "passed" if passed else "unresolved",
                    execution=records["execution"][identifier],
                    plan=plan,
                    assessment=assessment)
    return list(runs.values())


def choose_outcome(records, forms, candidates, statements):
    slate = find_current_slate(records)
    ended = {row["candidate"]["id"] for row in candidates if row["ended"]}
    if slate is not None and any(identifier not in ended for identifier in slate["selected"]):
        return "The assessment is incomplete"
    posts = {post["post"]["id"]: post for statement in statements for post in statement["posts"]}
    if any(post["classification"] == GAIN_CLASSIFICATION for post in posts.values()):
        return "A statement of the kind was posted"
    if any(post["classification"] == UNASSESSED_CLASSIFICATION for post in posts.values()):
        return "Statements were posted and the kind of at least one is unassessed"
    if posts:
        return "Only ordinary problems were posted"
    if forms or statements:
        return "Statements were formed and none was posted"
    return "No statement was formed"


def compute_report(records, artifacts, cutoff):
    forms = describe_forms(records)
    candidates = describe_candidate_endings(records)
    statements = describe_statements(records, artifacts)
    tests = describe_tests(records)
    latest_values = {}
    for value in order_records(records, "problem_value"):
        latest_values[value["statement_id"]] = value
    relations = [row for value in latest_values.values() for row in value.get("accounts", {}).get("relations", [])]
    posts = {post["post"]["id"]: post for statement in statements for post in statement["posts"]}
    marker = records["workspace"]["problem"]
    calls = order_records(records, "forming_call")
    start = marker.get("start", {})
    return dict(cutoff=cutoff, dependency_digest=digest(project_dependencies(records)),
        start=dict(field=marker["field"], kind=start.get("kind"), description=start.get("description"),
                   first_question=None if not calls else calls[0]["question"]),
        forms=forms, imports=order_records(records, "problem_forming_import"), candidates=candidates, statements=statements,
        withdrawals=describe_withdrawals(records),
        counts=dict(forming=count_forming(records),
            probes=len(records.get("probe", {})), runs=len(records.get("execution", {})),
            recorded_comparisons=sum(call["comparison_count"] or 0 for call in calls),
            forms_with_evidence=sum(row["standing"] in ("refuted", "proved", "implied_by_known_results", "found_in_literature") for row in forms),
            forms_without_evidence=sum(row["standing"] == "without_evidence" for row in forms),
            forms_not_examined=[dict(form_id=row["form"]["id"], lack_of_means=row["lack_of_means"])
                                for row in forms if row["standing"] == "not_examined"],
            relations={standing: sum(row["standing"] == standing for row in relations) for standing in ("established", "expected")},
            forming_expected_relations=sum(len(row["form"]["expected_relations"]) for row in forms),
            tests=dict(total=len(tests), **{outcome: sum(row["outcome"] == outcome for row in tests)
                                          for outcome in ("passed", "failed", "unresolved")}),
            posts={classification: sum(row["classification"] == classification for row in posts.values())
                   for classification in POST_CLASSIFICATIONS}),
        tests=tests, spent=dict(accounts=dict(records.get("resource_account", {})), strategy=dict(records.get("strategy_account", {})),
            reviews=usage_report(records),
            reported_forming_usage=[dict(forming_call_id=call["id"], usage=call["usage"]) for call in calls]),
        outcome=choose_outcome(records, forms, candidates, statements))


def record_report(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "reopen_when"))
        text(value["id"], "Problem report ID")
        text(value["reopen_when"], "Finding that would make the exploration worth reopening")
        forms = describe_forms(records)
        if is_report_due(records) and any(row["disposition"]["status"] == "open" for row in forms):
            raise ResearchError("problem_report_forms_open", OPEN_FORMS_MESSAGE,
                                {"form_ids": [row["form"]["id"] for row in forms if row["disposition"]["status"] == "open"]})
        report = dict(compute_report(records, artifacts, expected_revision), **value, recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_report", value["id"], report)], report

    return prepared_mutation(store, "problem.report", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def describe_report_advisories(records):
    report = find_latest(records, "problem_report")
    if report is not None and report["dependency_digest"] != digest(project_dependencies(records)):
        return [dict(code="problem_report_stale",
                     reason="The records that this report was computed from changed after its cutoff. "
                            "Record a new report with problem-report, under a new id.",
                     report_id=report["id"])]
    if report is None and is_report_due(records):
        return [dict(code="problem_report_due",
                     reason="The current slate selects nothing, or each candidate that it selects is posted, withdrawn "
                            "or taken up in another record. Record the report of the exploration with problem-report.")]
    return []
