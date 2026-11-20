"""Hand-written accounts exercising the value command's public payload."""

from research_harness.problem_accounts import is_full_reading

CHANGES_IF_RESOLVED_TEXT = ("A proof gives the bound for every finite construction, which the source states as an open "
                            "question. A disproof gives a finite construction at which the bound fails.")


def build_accounts(evidence=None, neighbours=()):
    return {"answer": {"id": "answer", "status": "none", "text": None, "evidence_ids": []},
            "answer_evidence": [], "boundaries": [],
            "explanatory_use": [] if evidence is None else [{
                "id": "use", "fact": "The bounded examples are established.", "scope": "The finite constructions only.",
                "date": "unknown", "evidence": evidence, "evidence_ids": [], "answer_argument": None}],
            "alternatives": [{"id": "neighbour-" + str(index), "neighbour": neighbour,
                "form_id": None, "role": "no_alternative", "reason": "A comparison question, with no asserted alternative."}
                for index, neighbour in enumerate(neighbours)],
            "strength": [{"id": "strength-" + head, "head": head, "description": "The stated " + head + ".",
                "alternative_ids": [], "no_alternative_reason": "No relaxation is claimed under this head."}
                for head in ("range_of_instances", "constants", "exponents", "exactness")],
            "relations": [], "refuted_conjectures": []}


def build_place(evidence, identifier="place", **changes):
    return dict({"id": identifier, "fact": "The source states the bound for every finite construction as an open question.",
                 "scope": "The finite constructions of the source.",
                 "role": {"kind": "stated", "description": "The source states the statement as an open question."},
                 "relation_ids": [], "date": "unknown", "evidence": evidence, "evidence_ids": []}, **changes)


def build_account_value(evidence, identifier="value-1", neighbours=(), place_evidence=None):
    """A new value record: the place rests on the full readings among evidence unless place_evidence names its own."""
    accounts = build_accounts(evidence, neighbours)
    accounts["places"] = [build_place([item for item in evidence if is_full_reading(item)]
                                      if place_evidence is None else place_evidence)]
    return {"id": identifier, "statement_id": "statement-1", "accounts": accounts,
            "motivation": [{"text": "The bound would connect the finite constructions.",
                            "evidence": [{"kind": "account", "id": "place"}]}],
            "changes_if_resolved": [{"text": CHANGES_IF_RESOLVED_TEXT, "evidence": [{"kind": "account", "id": "place"}]}],
            "current_state": [{"text": "Only bounded examples have been established.",
                               "evidence": [{"kind": "account", "id": "use"}]}]}


def build_relation(identifier="relation", **changes):
    return dict({"id": identifier, "relation": "implies", "target": "The finite bound.",
        "alternative_id": None, "standing": "expected", "scope": "The finite family.", "date": "unknown",
        "evidence": [], "evidence_ids": [], "earlier_statement": False, "consequence": None}, **changes)


def build_alternative(identifier="weaker", **changes):
    return dict({"id": identifier, "neighbour": "A weaker bound.", "form_id": None, "role": "weaker",
        "relation_id": "relation", "surplus": {"description": "The exact exponent.", "status": "neither",
        "evidence_ids": [], "relation_ids": []}}, **changes)
