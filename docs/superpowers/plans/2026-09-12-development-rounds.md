# Development Rounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one managed study develop its paper across independently approved research rounds, with a mechanical round gate that decides between a new round and publication, and with blind cohort predictions recorded at every measurement.

**Architecture:** Five new operations (`round`, `round-review`, `round-admit`, `round-assess`, `manuscript-prediction`) and one new gate (`round`) are added to the existing append-only SQLite research store through the same `prepared_mutation` / `immutable_record` / obligation idioms. Two new modules, `research_harness/rounds.py` and `research_harness/predictions.py`, hold the round records and the blind predictions; small additive changes in `development.py` (the `next_round` disposition, objective lineage), `literature.py` (four consequence search purposes), `reading.py` (the `exemplar` full-text purpose), `resources.py` (the `development` purpose and `rounds` unit), `gates.py` (gate and transitions), `publication.py` (shared assessor validation), `review_packets.py` / `review_delivery.py` (round packet), `report_views.py` / `cli.py` (status, priority, commands) wire them in. Skills, workflow docs, CLI reference, examples, constitution Version 3 and the 0.39.0 release note complete the change.

**Tech Stack:** Python 3.9 standard library only; `unittest` (run with `python3 -m unittest`); SQLite store with `schema_version` 1; Markdown docs; `codex/generate.py` for the Codex mirror.

**Spec:** `docs/superpowers/specs/2026-09-12-development-rounds-design.md` (this plan argues from it; read it first). The validation cases R01 to R25 are in `misc/harness-improvement/v3/validation.md` of the exactory repository and are restated per task below.

## Global Constraints

- Python 3.9 standard library. No new dependency. `pdftotext` stays optional.
- `schema_version` stays 1. Events and receipts stay append-only. History is never rewritten.
- Every read command stays read-only. `status`, `next`, `gate`, `export` write no event.
- One `Evaluation` per command; no cache outlives a process. New state functions take `(records, artifacts)` and call `Evaluation.of(records, artifacts)` like `gates.gate_state`.
- Legacy records keep their credit: a 0.38.0 store at `literature` gains only `constitution_revalidation_required`; a store at `evaluate` with a twice-accepted bundle gains `round_decision_missing` as well, and nothing else.
- The rubric core stays exactly `summary, strengths, weaknesses, soundness, presentation, contribution, overall, decision`. The prediction is a separate record.
- Three blind measurement reviews and two accepting blind publication reviews stay as they are.
- Blind review scores and predictions are recorded and reported; no gate rule and no goal reads them.
- No default round count. A `development` budget is optional and set by the user with `budget`.
- Every round is bounded by records, not by per-source bookkeeping: one `round_decision`, one `round_review`, one `round_admission`, one `round_assessment`.
- All code, tests, docs, commit messages in English. Commits end with the attribution lines the session gives you (`Co-Authored-By` and `Claude-Session`).
- Work in the worktree `/Users/ryshiro/exactory/plugins/exactory-client-worktrees/development-rounds` on branch `feat/development-rounds`. Run tests from that directory: `python3 -m unittest tests.test_research_rounds -v` for the new module, and the full suite `python3 -m unittest discover -s tests` before the release tasks (about 17 minutes; the baseline is 901 tests OK).
- Commit messages: `test:` for a RED test commit, `feat(research):`/`fix(research):`/`docs:` for GREEN and docs commits, one commit per TDD step pair at least. Write the message to a file and pass `-F` (a heredoc containing an `exactory-...` line is refused by a session hook).

## File Structure

| File | Responsibility |
| --- | --- |
| `research_harness/rounds.py` (new) | Round decision, review, admission, assessment records; `round_state` gate; `round_summary` for status; `active_round` for literature |
| `research_harness/predictions.py` (new) | `manuscript-prediction` record; `measurement_summary` (review and prediction medians for a bundle) |
| `research_harness/resources.py` | `development` purpose and `rounds` unit |
| `research_harness/development.py` | `next_round` disposition, `carried_developments`, objective lineage acceptance in `_inheritance` |
| `research_harness/principles.py` | `widen_objective` (configuration target, `research_objective`, `objective_lineage`) |
| `research_harness/publication.py` | `validate_assessor` shared by manuscript reviews, predictions and round reviews |
| `research_harness/literature.py` | `DEVELOPMENT_PURPOSES`; round obligations in `_foundation_state` |
| `research_harness/reading.py` | `exemplar` in `FULLTEXT_PURPOSES` |
| `research_harness/gates.py` | `round` gate; `evaluate -> literature` and `evaluate -> deposit` rules |
| `research_harness/review_packets.py`, `review_delivery.py` | `round_packet`, `deliver_round` |
| `research_harness/report_views.py`, `cli.py` | `PRIORITY` additions, `round` in status, new commands, gate and export kind |
| `docs/research-cli-examples.json`, `docs/research-cli.md`, `docs/research-workflow.md` | Operation shapes and workflow section |
| `RESEARCH_CONSTITUTION.md`, `skills/*`, `codex/skills/*`, `README.md`, manifests, `docs/releases/0.39.0.md`, `docs/testing/development-rounds.md` | Policy, guidance, release |
| `tests/rounds_fixtures.py` (new), `tests/test_research_rounds.py` (new) | Round fixtures and the R01 to R25 cases; existing test modules gain the small cases named per task |

## Shared fixture: `tests/rounds_fixtures.py`

Task 1 creates this file; later tasks extend it. It builds on `DevelopmentCase` (six read sources, a complete foundation, one executed and assessed cycle with a readiness review) and on the manuscript fixtures of `tests/test_research_publication.py` and `tests/integration_fixtures.py`.

```python
"""Round fixtures: a measured manuscript, its blind reviews and predictions, and round records."""

import copy
import json

from development_fixtures import DevelopmentCase
from integration_fixtures import observed_candidate
from research_harness import predictions, publication, rounds
from test_research_publication import ResearchPublicationTests

DEVELOPMENT_SEARCHES = ("downstream", "next_step", "exemplars", "changes")


class RoundsCase(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.execution_payload = observed_candidate(self)
        (self.root / "draft").mkdir()
        (self.root / "evidence").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Round fixture.\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("The exact finite bound was enumerated.")
        (self.root / "draft/references.bib").write_text("@article{bounded,title={Authored bound}}\n")

    def claims(self, *extra, revised=(), superseded=()):
        """Claim objects for evidence/claims.json: 'bound' plus the named extra claims."""
        items = [{"id": "bound", "claim": "The maximum is 9."}]
        items.extend({"id": claim, "claim": "Claim " + claim + " holds."} for claim in extra)
        for claim in revised:
            item = next(i for i in items if i["id"] == claim)
            item["revised"] = {"previous": item["claim"], "reason": "Sharpened after the round's evidence."}
        for claim in superseded:
            item = next(i for i in items if i["id"] == claim)
            item["superseded"] = {"reason": "Replaced by a wider claim."}
        return items

    def pin(self, claims=None, identifier=None, reviews=2):
        """Write claims.json, pin the bundle and record `reviews` accepting blind reviews."""
        claims = self.claims() if claims is None else claims
        (self.root / "evidence/claims.json").write_text(json.dumps(claims))
        identifier = identifier or ("paper-" + str(self.store.revision))
        evidence = [self.result_evidence(self.execution_payload)]
        bundle = self.mutate(publication.prepare_publication, {"id": identifier,
            "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt", "bibliography": "draft/references.bib",
                      "claims": "evidence/claims.json", "sources": None},
            "claim_evidence": [{"claim_id": c["id"], "evidence": evidence} for c in claims]})["result"]
        for number in range(1, reviews + 1):
            self.mutate(publication.record_manuscript_review,
                        ResearchPublicationTests.manuscript_review(self, bundle, identifier + "-gate-" + str(number)))
        return bundle

    def prediction_payload(self, bundle, assessor, percentile=30, band=(20, 40)):
        return {"id": assessor + "-prediction", "bundle_digest": bundle["digest"], "blind": True,
                "assessor": {"id": assessor, "kind": "agent",
                             "provenance": self.artifacts.put(("Blind context " + assessor).encode(), "text/plain"),
                             "relationship": "A separate fixture assessor.", "independence_basis": "A blind context received the exact manuscript."},
                "prediction": {"corpus": "arxiv", "category": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31",
                               "percentile": percentile, "band": {"best": band[0], "worst": band[1]}},
                "reasons": ["The authored fixture states a narrow finite result."]}

    def measure(self, bundle, suffix, percentiles=(30, 25, 40)):
        """Three blind reviews and three predictions on the bundle, as one measurement."""
        for number, percentile in enumerate(percentiles, 1):
            assessor = "measure-" + suffix + "-" + str(number)
            self.mutate(publication.record_manuscript_review, ResearchPublicationTests.manuscript_review(self, bundle, assessor))
            self.mutate(predictions.record_prediction, self.prediction_payload(bundle, assessor, percentile))

    def round_evidence(self):
        return [self.source_evidence(), self.result_evidence(self.execution_payload)]

    def recandidate(self, suffix, *, alternative="next_round"):
        """Assess cycle-1 again with its alternative carried, checkpoint it and review readiness again."""
        api = self.development()
        plan = self.store.snapshot()["records"]["cycle_plan"]["cycle-1"]["payload"]
        payload = self.assessment(plan, self.execution_payload, identifier="assessment-" + suffix)
        payload["development"]["alternatives"][0].update(disposition=alternative, reason="Exceeds this round's admitted scope.")
        self.mutate(api.assess_cycle, payload)
        self.save_checkpoint("cycle-1", "assessment-" + suffix, "checkpoint-" + suffix)
        self.mutate(api.record_readiness_review, self.review(self.execution_payload, identifier="review-" + suffix))
        return "assessment-" + suffix

    def set_stage(self, stage):
        """Write the study projection at a stage directly; status tests need a stage, not a transition."""
        from research_harness.integration import export_workspace
        from research_harness.operations import prepared_mutation
        state = {"version": 2, "slug": "rounds", "stage": stage, "status": "pending", "autopilot": False, "waiting": None,
                 "loop": {"target": None, "budget": None, "notes": ""}, "created": "2026-09-12T00:00:00Z", "updated": "2026-09-12T00:00:00Z",
                 "research": {"store": ".exactory/research.sqlite3", "profile": "research"}}
        self.mutate(lambda store, payload, **identity: prepared_mutation(store, "test.stage", payload,
                    lambda records, value: ([("workspace", "study", state)], state), **identity), {})
        export_workspace(self.store)

    def goal(self, direction="vertical", statement="Extend the finite bound to every integer in [0, 5]."):
        return {"direction": direction, "field_change": None, "statement": statement,
                "contribution_delta": "Readers can apply the bound over the wider range without a new enumeration.",
                "beneficiaries": [{"who": "Authors of bounded-sequence proofs", "bottleneck": "The finite range stops at 3.",
                                   "evidence": self.round_evidence()}],
                "success_criteria": [{"id": "sc-wider", "kind": "claim", "statement": "The manuscript establishes the bound up to 5 with evidence."}],
                "stop_conditions": [{"id": "st-counterexample", "statement": "An integer in [4, 5] violates the bound."}],
                "continuity": "The round keeps every claim, reading and evidence of the current paper and extends the enumeration.",
                "route": "Plan one enumeration cycle over [4, 5] and assess it.", "risks": ["The wider enumeration may reveal a counterexample."],
                "evidence": self.round_evidence()}

    def decision_payload(self, bundle, closes=1, decision="continue", *, direction="vertical", statement=None,
                         objective=None, lineage=None, carried=(), candidates=None, reopening=None):
        goal = self.goal(direction, statement) if statement else self.goal(direction)
        pursued = {"id": "cand-goal", "direction": goal["direction"], "statement": goal["statement"],
                   "disposition": "pursue" if decision == "continue" else "rejected",
                   "reason": "The wider range is the paper's most valuable next step." if decision == "continue" else "Nothing remains to dig.",
                   "evidence": self.round_evidence()}
        rejected = {"id": "cand-transfer", "direction": "horizontal", "statement": "Transfer the bound to real inputs.",
                    "disposition": "rejected", "reason": "No evidenced demand.", "evidence": self.round_evidence()}
        payload = {"id": "decision-" + str(closes) + "-" + decision + "-" + str(self.store.revision), "closes": closes, "decision": decision,
                   "bundle_digest": bundle["digest"], "candidates": candidates if candidates is not None else [pursued, rejected],
                   "carried": list(carried), "next": None, "reason": "Recorded by the fixture."}
        if decision == "continue":
            payload["next"] = {"number": closes + 1, "objective": objective or copy.deepcopy(self.objective),
                               "objective_lineage": lineage, "goal": goal,
                               "resource_limits": {"literature": {"network_requests": 20, "readings": 10}, "experiment": {"wall_seconds": 600}},
                               "reopening": reopening}
        return payload

    def review_payload(self, decision, verdict="approved", assessor="round-assessor", checks=None):
        kinds = rounds.CHECKS_CONTINUE if decision["decision"] == "continue" else rounds.CHECKS_STOP
        return {"id": decision["id"] + "-review-" + assessor, "round_id": decision["id"], "round_digest": decision["digest"],
                "assessor": {"id": assessor, "kind": "agent",
                             "provenance": self.artifacts.put(("Round context " + assessor).encode(), "text/plain"),
                             "relationship": "A separate fixture assessor.", "independence_basis": "The round packet was delivered to a fresh context."},
                "verdict": verdict, "checks": checks if checks is not None else [
                    {"kind": kind, "status": "passed" if verdict == "approved" else "unresolved",
                     "reason": "The fixture goal names a concrete delta, beneficiaries and a route.", "evidence": self.round_evidence()}
                    for kind in kinds],
                "limitations": ["This authored receipt does not establish comprehension or impartiality."]}

    def open_round(self, bundle=None, closes=1, **decision_kwargs):
        """Decide continue, approve it and admit the next round; returns (decision, review, admission)."""
        bundle = bundle or self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle, closes, **decision_kwargs))["result"]
        review = self.mutate(rounds.record_round_review, self.review_payload(decision))["result"]
        admission = self.mutate(rounds.admit_round, {"id": "round-" + str(decision["payload"]["next"]["number"]),
                                                     "round_id": decision["id"], "review_id": review["id"],
                                                     "reason": "The approved goal opens the round."})["result"]
        return decision, review, admission

    def development_searches(self, suffix):
        """Record the four consequence purposes with captured empty results, as `foundation_searches` does."""
        for purpose in DEVELOPMENT_SEARCHES:
            self.record_purpose(purpose, purpose + "-" + suffix)

    def exemplar_requirement(self, suffix):
        from research_harness.reading import require_fulltext
        link = self.links[1]
        return self.mutate(require_fulltext, {"id": "exemplar-" + suffix, "profile": "research", "version_id": link["version_id"],
                                              "purpose": "exemplar", "reason": "A comparable first result developed into a larger contribution."})

    def assess_payload(self, admission, bundle, observed=True):
        goal = admission["goal"]
        status = "observed" if observed else "not_observed"
        return {"id": admission["id"] + "-assessment", "round_id": admission["id"], "bundle_digest": bundle["digest"],
                "criteria": [{"id": c["id"], "status": status, "explanation": "Judged from the round's bundle.",
                              "evidence": self.round_evidence()} for c in goal["success_criteria"]],
                "stop_conditions": [{"id": s["id"], "status": "not_observed", "explanation": "No counterexample appeared.",
                                     "evidence": self.round_evidence()} for s in goal["stop_conditions"]],
                "summary": "The round's outcome as recorded by the fixture."}
```

`record_purpose(purpose, identifier)` is the per-purpose body of `SynthesisCase.foundation_searches` (an `import_response` of an empty MCP result followed by `record_search` with `found_work_ids: []`, `verdict: "nothing-new"`, `dispositions: []`); Task 8 extracts it in `tests/test_research_synthesis.py` so both loops share it. Until Task 8, fixtures that need it define the same body locally.

---

### Task 1: Resource purpose `development` and unit `rounds`

**Files:**
- Modify: `research_harness/resources.py:19-20` (`UNITS`, `PURPOSES`)
- Modify: `docs/research-cli-examples.json` (`budget` example gains `"rounds": null`)
- Test: `tests/test_research_resources.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `resources.UNITS` contains `"rounds"`; `resources.PURPOSES` contains `"development"`; `resources.charge(records, "development", {"rounds": 1})` returns an account change or raises `resource_budget_exhausted`; `resources.obligations(records, "research")` reports the exhausted development budget.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_resources.py` inside `ResourceTests`:

```python
    def test_development_rounds_are_a_budgeted_unit(self):
        from research_harness.resources import PURPOSES, UNITS, charge, obligations
        self.assertIn("rounds", UNITS)
        self.assertIn("development", PURPOSES)
        self.mutate(set_budget, {"profile": "research", "purpose": "development", "limits": self.limits(rounds=1),
                                 "reason": "The user wants at most one development round."})
        records = self.store.snapshot()["records"]
        kind, key, account = charge(records, "development", {"rounds": 1})
        self.assertEqual((kind, key, account["charged"]["rounds"]), ("resource_account", "research:development", 1))
        charged = dict(records, resource_account={key: account})
        self.assert_error("resource_budget_exhausted", lambda: charge(charged, "development", {"rounds": 1}))
        self.assertEqual([o["code"] for o in obligations(charged, "research")], ["resource_budget_exhausted"])

    def test_a_budget_payload_names_the_rounds_unit(self):
        limits = self.limits()
        del limits["rounds"]
        self.assert_error("invalid_input", lambda: self.mutate(set_budget, {"profile": "research", "purpose": "literature",
                                                                             "limits": limits, "reason": "Incomplete."}))
```

`self.limits(**values)` already returns every unit with `None` defaults (`tests/test_research_resources.py:15-19`); extend its unit list with `rounds` in the same edit.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_resources -v`
Expected: the two new tests fail (`rounds` not in `UNITS`; `charge` refuses the unknown purpose with `invalid_input`).

- [ ] **Step 3: Implement**

In `research_harness/resources.py`:

```python
UNITS = ("network_requests", "source_bytes", "readings", "screenings", "model_input_tokens", "model_output_tokens", "wall_seconds", "rounds")
PURPOSES = ("literature", "screening", "experiment", "development")
```

Update the module docstring's first paragraph with one sentence: "The `development` purpose counts admitted development rounds in the `rounds` unit; `round-admit` charges one round." In `docs/research-cli-examples.json`, add `"rounds": null` to the `budget` example's `limits` (keep key order: append after `wall_seconds`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_resources tests.test_research_guidance -v`
Expected: PASS (the guidance test compares the example catalog with the CLI output, so it must still pass).

- [ ] **Step 5: Commit**

```bash
git add research_harness/resources.py docs/research-cli-examples.json tests/test_research_resources.py
git commit -F /tmp/msg-task1.txt
```
Message: `feat(research): budget development rounds as a resource unit`

---

### Task 2: The `next_round` disposition and carried developments

**Files:**
- Modify: `research_harness/development.py:644-698` (`_development`), add `carried_developments`
- Create: `tests/rounds_fixtures.py` (the shared fixture above, without the parts that need `rounds`/`predictions` yet: keep the imports of `rounds` and `predictions` inside the methods that use them so the module imports before those modules exist)
- Test: `tests/test_research_development.py`

**Interfaces:**
- Produces: `development.DISPOSITIONS = ("pursue", "not_useful", "resolved", "budget_paused", "next_round")`; `development.carried_developments(records, cycle_ids=None) -> list[dict]` with items `{"assessment_id", "kind": "alternative", "question", "strategy", "reason"}` or `{"assessment_id", "kind": "branch", "cycle_id", "reason"}`, taken from each cycle's current assessment (`cycle["assessment_id"]`), for every cycle (or only `cycle_ids`), sorted by `(assessment_id, kind, question or cycle_id)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_development.py`:

```python
    def test_a_next_round_alternative_is_carried_and_does_not_block_readiness(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution)
        payload["development"]["alternatives"][0].update(disposition="next_round",
            reason="The wider range exceeds this round's admitted scope.")
        assessed = self.mutate(api.assess_cycle, payload)["result"]
        self.assertNotIn("useful_development_remaining", {o["code"] for o in assessed["obligations"]})
        self.save_checkpoint()
        self.mutate(api.record_readiness_review, self.review(execution))
        self.assertTrue(api.readiness_report(self.store)["ready"])
        carried = api.carried_developments(self.store.snapshot()["records"])
        self.assertEqual(carried, [{"assessment_id": "assessment-1", "kind": "alternative",
                                    "question": "Does the bound extend beyond n = 3?", "strategy": "generalization",
                                    "reason": "The wider range exceeds this round's admitted scope."}])

    def test_a_next_round_branch_is_carried_by_cycle_id(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution)
        payload["development"]["branches"][0].update(disposition="next_round", reason="Carried to the round gate.")
        self.mutate(api.assess_cycle, payload)
        carried = api.carried_developments(self.store.snapshot()["records"], ["cycle-1"])
        self.assertEqual(carried, [{"assessment_id": "assessment-1", "kind": "branch", "cycle_id": "cycle-1",
                                    "reason": "Carried to the round gate."}])
        self.assertEqual(api.carried_developments(self.store.snapshot()["records"], ["other"]), [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_development -k next_round -v`
Expected: FAIL with `invalid_development` ("Alternative disposition must be one of: ...") and `AttributeError: carried_developments`.

- [ ] **Step 3: Implement**

In `research_harness/development.py`, next to `STRATEGIES`:

```python
DISPOSITIONS = ("pursue", "not_useful", "resolved", "budget_paused", "next_round")
```

In `_development`, replace both literal tuples `("pursue", "not_useful", "resolved", "budget_paused")` with `DISPOSITIONS`. The `if option["disposition"] in ("pursue", "budget_paused")` branches stay unchanged, so `next_round` adds no obligation. Add after `_development`:

```python
def carried_developments(records, cycle_ids=None):
    """The `next_round` alternatives and branches of each cycle's current assessment."""
    carried = []
    for cycle in records.get("cycle", {}).values():
        if cycle["assessment_id"] is None or cycle_ids is not None and cycle["id"] not in cycle_ids:
            continue
        development = records["cycle_assessment"][cycle["assessment_id"]]["payload"]["development"]
        if development is None:
            continue
        for option in development["alternatives"]:
            if option["disposition"] == "next_round":
                carried.append({"assessment_id": cycle["assessment_id"], "kind": "alternative", "question": option["question"],
                                "strategy": option["strategy"], "reason": option["reason"]})
        for branch in development["branches"]:
            if branch["disposition"] == "next_round":
                carried.append({"assessment_id": cycle["assessment_id"], "kind": "branch", "cycle_id": branch["cycle_id"],
                                "reason": branch["reason"]})
    return sorted(carried, key=lambda item: (item["assessment_id"], item["kind"], item.get("question") or item.get("cycle_id")))
```

Create `tests/rounds_fixtures.py` with the shared fixture from the top of this plan. Move the `from research_harness import predictions, publication, rounds` import into the methods that use `predictions` and `rounds` (`measure`, `open_round`, `review_payload`), because those modules do not exist until Tasks 3 and 6; keep `publication` at module level.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_development -v`
Expected: PASS, including the existing dispositions tests.

- [ ] **Step 5: Commit**

Message: `feat(research): carry a next_round development to the round gate`

---

### Task 3: Objective lineage

**Files:**
- Modify: `research_harness/principles.py` (add `widen_objective`)
- Modify: `research_harness/development.py:272-310` (`_inheritance`), add `_objective_lineage`
- Test: `tests/test_research_development.py`

**Interfaces:**
- Produces: `principles.widen_objective(records, target, lineage, round_id) -> list[change]` where `target` is a research objective `{kind: "objective", id, statement}`, `lineage` is `{"previous_id", "containment"}`; changes: `("configuration", "research", config with target)`, `immutable_record(records, "research_objective", target["id"], target)`, `immutable_record(records, "objective_lineage", target["id"], {"id": target["id"], "predecessor": previous_id, "containment", "round_id"})`. Raises `objective_locked` when `previous_id` is not the current objective id, the statement is unchanged, or the target id is already an objective; `invalid_target` for a malformed (including null) target or lineage. Containment is not checked mechanically (the spec's section 5.1 leaves it to the round reviewer). The review of this task dropped the `artifacts` parameter: the research-profile objective check does not read artifacts.
- Produces: `development._objective_lineage(context) -> list[objective]` from the current objective back through `objective_lineage` predecessors; `_inheritance` accepts a checkpoint whose `objective` is in that list.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_development.py`:

```python
    def widen(self, statement="For every integer n in [0, 5], n squared is at most 25, with equality at n = 5."):
        from research_harness.operations import prepared_mutation
        principles = self.api("principles")
        target = {"kind": "objective", "id": "wider-square-bound", "statement": statement}

        def prepare(records, value):
            changes = principles.widen_objective(records, target,
                                                 {"previous_id": self.objective["id"], "containment": "The range [0, 3] is contained in [0, 5]."},
                                                 "round-2")
            return changes, target
        self.mutate(lambda store, payload, **identity: prepared_mutation(store, "test.widen", payload, prepare, **identity), {})
        return target

    def test_a_widened_objective_keeps_the_old_one_as_an_ancestor(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        self.mutate(api.assess_cycle, self.assessment(plan, execution))
        checkpoint = self.save_checkpoint()
        target = self.widen()
        records = self.store.snapshot()["records"]
        self.assertEqual(records["configuration"]["research"]["target"], target)
        self.assertEqual(records["research_objective"][self.objective["id"]], self.objective)
        self.assertEqual(records["objective_lineage"][target["id"]]["predecessor"], self.objective["id"])
        # The widened objective changes the dependencies every earlier assessment bound, so the
        # earlier cycle is assessed again under the current objective before it is inherited.
        again = self.assessment(plan, execution, identifier="assessment-1b")
        reassessed = self.mutate(api.assess_cycle, again)["result"]
        self.assertTrue(reassessed["validated_result"])
        # A full scope of the old objective is a partial result for the wider one.
        self.assertFalse(reassessed["complete"])
        self.assertIn("objective_scope_incomplete", {o["code"] for o in reassessed["obligations"]})
        checkpoint = self.save_checkpoint(assessment_id="assessment-1b", identifier="checkpoint-1b")
        successor = self.plan("cycle-2")
        successor.update(objective=target, predecessor=checkpoint["id"], question="Does the bound hold up to n = 5?",
                         distinguishing_test="Enumerate all integers up to 5.",
                         scope={"id": "up-to-three", "kind": "partial", "statement": "At n in [0, 3] the square is at most 9.",
                                "assumptions": ["n is an integer in the stated finite range."],
                                "remaining_obligations": ["Establish the bound for n = 4, 5."]},
                         inheritance=[{"checkpoint_id": checkpoint["id"], "assessment_id": "assessment-1b", "use": "validated_result",
                                       "evidence": [self.result_evidence(execution)], "assumptions": ["n is an integer in the stated finite range."],
                                       "deduction": "The enumeration up to 3 contributes the first part of the wider range."}])
        successor["literature"]["scope"] = successor["scope"]
        self.mutate(api.plan_cycle, successor)
        self.assertEqual(self.store.snapshot()["records"]["cycle"]["cycle-2"]["status"], "planned")
        stale = self.plan("cycle-3")
        stale.update(question="A plan that still carries the old objective.", distinguishing_test="Old objective test.")
        self.assert_error("objective_mismatch", lambda: self.mutate(api.plan_cycle, stale))

    def test_an_unlinked_unchanged_or_malformed_objective_is_refused(self):
        self.prepared_study()
        principles = self.api("principles")
        records = self.store.snapshot()["records"]
        unlinked = {"kind": "objective", "id": "narrow", "statement": "At n = 0 the square is at most 9."}
        self.assert_error("objective_locked", lambda: principles.widen_objective(records, unlinked,
            {"previous_id": "someone-else", "containment": "x"}, "round-2"))
        self.assert_error("objective_locked", lambda: principles.widen_objective(records, dict(self.objective, id="same"),
            {"previous_id": self.objective["id"], "containment": "x"}, "round-2"))
        # The review of this task added the reused-id, null-target, wrong-kind, missing-containment and
        # empty-containment cases; see tests/test_research_development.py for the complete test.
```

Note for the executor: a widened objective changes the configuration digest that every synthesis section binds (`synthesis._assess` binds `configuration["digest"]` for `standards` too), so all four sections (`standards`, `rationale`, `context`, `innovation`) report `synthesis_dependencies_stale` after widening and are re-recorded before `plan_cycle` passes `require_ready`. The spec's section 7 records this. The test asserts the stale set and then calls `refresh_synthesis`.

Note for the executor: `validated_result` inheritance requires the inherited assessment to be currently validated. A widened objective changes `context.dependencies()` (objective and configuration digest), so every earlier assessment reports `development_dependencies_stale` until it is assessed again under the current objective. That is the existing rule for any dependency change and it stays: a later round re-assesses the earlier cycles with the same payloads under new ids (in a real round the preparation digest changes anyway, because the round records new searches). What Task 3 changes is that the re-assessment and the inheritance succeed under the widened objective: `_assess` checks an assessment's scope against the objective its plan carried, and `_inheritance` accepts a checkpoint whose objective is an ancestor.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_development -k objective -v`
Expected: FAIL with `AttributeError: widen_objective`.

- [ ] **Step 3: Implement**

In `research_harness/principles.py`:

```python
def widen_objective(records, target, lineage, round_id):
    """Changes that widen the research objective through an admitted round; the old objective stays retained."""
    config = _configuration(records)
    current = config["target"]
    fields(lineage, ("previous_id", "containment"), code="invalid_target")
    text(lineage["containment"], "Objective containment", code="invalid_target")
    _validate_objective(target)  # the research-profile shape check extracted from _validate_target; refuses None
    if config["profile"] != "research" or current is None or lineage["previous_id"] != current["id"]:
        raise ResearchError("objective_locked", "Widen the current complete objective through its recorded predecessor")
    if target["id"] == current["id"] or target["statement"] == current["statement"] or target["id"] in records.get("research_objective", {}):
        raise ResearchError("objective_locked", "A widened objective needs a new identity and a wider statement")
    record = {"id": target["id"], "predecessor": current["id"], "containment": lineage["containment"], "round_id": round_id}
    return [("configuration", "research", dict(config, target=target)),
            immutable_record(records, "research_objective", target["id"], target),
            immutable_record(records, "objective_lineage", target["id"], record)]
```

In `research_harness/development.py`:

```python
def _objective_lineage(context):
    """The current objective and every recorded predecessor, newest first."""
    lineage, current = [], context.objective
    while current is not None:
        lineage.append(current)
        link = context.records.get("objective_lineage", {}).get(current["id"])
        current = context.records.get("research_objective", {}).get(link["predecessor"]) if link else None
    return lineage
```

In `_inheritance`, replace `if parent["objective"] != context.objective:` with `if parent["objective"] not in _objective_lineage(context):` and `if checkpoint["objective"] != context.objective or checkpoint["assessment_id"] != item["assessment_id"]:` with `if checkpoint["objective"] not in _objective_lineage(context) or checkpoint["assessment_id"] != item["assessment_id"]:`.

`_assess` validates `value["scope"]` against `context.objective`; an old full-scope assessment re-evaluated under a widened objective would raise `objective_scope_mismatch`. Make `_assess` use the objective the assessed plan carried: replace `_scope(value["scope"], context.objective)` with `_scope(value["scope"], plan["payload"]["objective"])` (the plan is fetched two lines earlier). Where `_assess` appends `objective_scope_incomplete` for a partial scope, append it also when `plan["payload"]["objective"] != context.objective` (a full scope of an ancestor objective is partial for the current one), with the same explanation text. Leave `_Context.assessment`'s dependency comparison as it is: a widened objective makes earlier assessments stale until they are assessed again, which the test does. In `assess_cycle` the `development_scope` record keeps `context.objective`; leave it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_development tests.test_research_gates -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `feat(research): widen the objective through recorded lineage`

---
### Task 4: `rounds.py`: the round decision and its independent review

**Files:**
- Create: `research_harness/rounds.py`
- Modify: `research_harness/publication.py:103-128` (extract `validate_assessor`)
- Modify: `research_harness/literature.py:52` (add `DEVELOPMENT_PURPOSES`, used by Task 5's opening state; the obligations come in Task 6)
- Modify: `tests/rounds_fixtures.py` (module-level `from research_harness import rounds` now works)
- Test: `tests/test_research_rounds.py` (new), `tests/test_research_publication.py`

**Interfaces:**
- Produces: `rounds.record_round(store, payload, *, expected_revision, request_id)` (operation `round.decide`, record kind `round_decision`), `rounds.record_round_review(...)` (operation `round.review`, record kind `round_review`), constants `DECISIONS`, `DIRECTIONS`, `CANDIDATE_DISPOSITIONS`, `CRITERION_KINDS`, `CHECKS_CONTINUE`, `CHECKS_STOP`, `VERDICTS`, helpers `admissions(records)`, `latest_admission(records)`, `assessment_for(records, admission_id)`, `current_number(records)`, `active_round(records)`.
- Produces: `publication.validate_assessor(artifacts, assessor, authors)` used by `publication._review`, `predictions.record_prediction` (Task 7) and `rounds.record_round_review`.
- Consumes: `publication._bundle`, `development._Context`, `development._Evidence`, `development.carried_developments`, `principles.widen_objective`, `resources.obligations`.

Record shapes:

```python
# round_decision/<id>
{"id", "payload", "closes", "decision", "bundle_id", "bundle_digest", "evidence", "decided_revision", "request_id", "digest"}
# round_review/<id>
{"id", "payload", "round_id", "round_digest", "closes", "decision", "verdict", "reviewed_revision", "request_id", "digest"}
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_rounds.py`:

```python
"""Development rounds: decisions, reviews, admissions, assessments and the round gate."""

import copy

from research_harness import rounds
from rounds_fixtures import RoundsCase


class RoundDecisionTests(RoundsCase):
    def test_a_decision_binds_the_exact_current_bundle_and_the_current_round(self):
        bundle = self.pin()
        stale = self.decision_payload(bundle)
        stale["bundle_digest"] = "0" * 64
        self.assert_error("round_bundle_mismatch", lambda: self.mutate(rounds.record_round, stale))
        wrong = self.decision_payload(bundle, closes=2)
        self.assert_error("round_number_mismatch", lambda: self.mutate(rounds.record_round, wrong))
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.assertEqual((decision["closes"], decision["decision"], decision["bundle_digest"]), (1, "continue", bundle["digest"]))
        self.assertEqual(rounds.current_number(self.store.snapshot()["records"]), 1)

    def test_continue_pursues_exactly_one_candidate_and_stop_pursues_none(self):
        bundle = self.pin()
        none = self.decision_payload(bundle)
        none["candidates"][0]["disposition"] = "rejected"
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, none))
        two = self.decision_payload(bundle)
        two["candidates"][1]["disposition"] = "pursue"
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, two))
        stop = self.decision_payload(bundle, decision="stop")
        stop["candidates"][0]["disposition"] = "pursue"
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, stop))
        recorded = self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))["result"]
        self.assertEqual(recorded["decision"], "stop")
        self.assertIsNone(recorded["payload"]["next"])

    def test_a_goal_needs_claim_or_scope_criteria_stop_conditions_and_continuity(self):
        bundle = self.pin()
        for change in ({"success_criteria": []}, {"stop_conditions": []},
                       {"success_criteria": [{"id": "m", "kind": "measurement", "statement": "Percentile median improves by 10."}]}):
            payload = self.decision_payload(bundle)
            payload["next"]["goal"].update(change)
            self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, payload))
        payload = self.decision_payload(bundle)
        del payload["next"]["goal"]["continuity"]
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, payload))

    def test_a_carried_development_must_be_disposed(self):
        self.recandidate("carry")
        bundle = self.pin()
        omitted = self.decision_payload(bundle)
        self.assert_error("carried_development_missing", lambda: self.mutate(rounds.record_round, omitted))
        carried = [{"assessment_id": "assessment-carry", "kind": "alternative", "question": "Does the bound extend beyond n = 3?",
                    "disposition": "pursue", "reason": "It is the round's goal."}]
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle, carried=carried))["result"]
        self.assertEqual(decision["payload"]["carried"], carried)

    def test_a_repeated_goal_or_rejected_candidate_needs_reopening_with_changed_evidence(self):
        bundle = self.pin()
        first = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.mutate(rounds.record_round_review, self.review_payload(first, verdict="not_approved", assessor="first-assessor"))
        repeated = self.decision_payload(bundle, statement="Transfer the bound to real inputs.")
        repeated["candidates"][0]["direction"] = repeated["next"]["goal"]["direction"] = "horizontal"
        self.assert_error("round_goal_repeated", lambda: self.mutate(rounds.record_round, repeated))

    def test_review_evidence_names_a_manuscript_review_of_this_bundle(self):
        bundle = self.pin()
        review_id = bundle["id"] + "-gate-1"
        payload = self.decision_payload(bundle)
        payload["candidates"][0]["evidence"].append({"kind": "review", "review_id": review_id})
        decision = self.mutate(rounds.record_round, payload)["result"]
        self.assertIn(review_id, [e["reference"].get("review_id") for e in decision["evidence"]])
        wrong = self.decision_payload(bundle)
        wrong["candidates"][0]["evidence"].append({"kind": "review", "review_id": "absent"})
        self.assert_error("round_evidence_mismatch", lambda: self.mutate(rounds.record_round, wrong))


class RoundReviewTests(RoundsCase):
    def test_the_review_is_independent_complete_and_bound_to_the_decision(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        author = self.review_payload(decision, assessor="cycle-author")
        self.assert_error("review_not_independent", lambda: self.mutate(rounds.record_round_review, author))
        short = self.review_payload(decision)
        short["checks"] = short["checks"][:-1]
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round_review, short))
        doubled = self.review_payload(decision)
        doubled["checks"].append(dict(doubled["checks"][0]))
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round_review, doubled))
        stale = self.review_payload(decision)
        stale["round_digest"] = "0" * 64
        self.assert_error("round_review_stale", lambda: self.mutate(rounds.record_round_review, stale))
        review = self.mutate(rounds.record_round_review, self.review_payload(decision, verdict="not_approved"))["result"]
        self.assertEqual(review["verdict"], "not_approved")
        again = self.review_payload(decision, verdict="approved")
        self.assert_error("round_review_duplicate", lambda: self.mutate(rounds.record_round_review, again))

    def test_a_stop_review_has_its_own_checks(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))["result"]
        wrong = self.review_payload(decision)
        wrong["checks"] = [dict(wrong["checks"][0], kind="impact"), dict(wrong["checks"][1], kind="demand")]
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round_review, wrong))
        review = self.mutate(rounds.record_round_review, self.review_payload(decision))["result"]
        self.assertEqual({c["kind"] for c in review["payload"]["checks"]}, set(rounds.CHECKS_STOP))

    def test_one_approved_decision_per_closing_round(self):
        bundle = self.pin()
        first = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.mutate(rounds.record_round_review, self.review_payload(first))
        second = self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))["result"]
        self.assert_error("round_decision_duplicate",
                          lambda: self.mutate(rounds.record_round_review, self.review_payload(second, assessor="other-assessor")))
```

Append to `tests/test_research_publication.py`:

```python
    def test_validate_assessor_is_shared_and_refuses_authors(self):
        api = self.publication()
        from research_harness.evaluation import Evaluation
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        assessor = self.manuscript_review({"digest": "x"}, "reviewer-a")["assessor"]
        self.assertEqual(api.validate_assessor(evaluation, assessor, ["cycle-author"]), assessor)
        self.assert_error("review_not_independent", lambda: api.validate_assessor(evaluation, dict(assessor, id="Cycle Author"), ["cycle-author"]))
        self.assert_error("review_not_independent", lambda: api.validate_assessor(evaluation, dict(assessor, kind="robot"), []))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_rounds tests.test_research_publication -v`
Expected: FAIL with `ModuleNotFoundError: research_harness.rounds` and `AttributeError: validate_assessor`.

- [ ] **Step 3: Implement**

In `research_harness/publication.py`, extract the assessor checks from `_review` into:

```python
def validate_assessor(artifacts, assessor, authors):
    """An identified human or agent assessor with pinned provenance who is not an author."""
    fields(assessor, ("id", "kind", "provenance", "relationship", "independence_basis"))
    for key in ("id", "relationship", "independence_basis"):
        text(assessor[key], "Independent assessor " + key)
    if assessor["kind"] not in ("human", "agent"):
        raise ResearchError("review_not_independent", "Supply an identified independent reviewer")
    artifacts.read(assessor["provenance"])
    if _assessor_key(assessor["id"]) in {_assessor_key(a) for a in authors}:
        raise ResearchError("review_not_independent", "An author cannot provide an independent assessment")
    return assessor
```

`_review` becomes: `if value["blind"] is not True: raise ResearchError("review_not_independent", "Supply an identified independent blind reviewer")` followed by `validate_assessor(artifacts, value["assessor"], bundle["candidate"]["authors"])`; move `_assessor_key` above `_review`. Keep the error code and messages for the existing tests (`review_not_independent`).

In `research_harness/literature.py`, after `SEARCH_PURPOSES`:

```python
DEVELOPMENT_PURPOSES = ("downstream", "next_step", "exemplars", "changes")
```

Create `research_harness/rounds.py`:

```python
"""Development rounds: the paper-level loop and its gate.

A round decision (`round`) closes the current round on the exact publication bundle and
either proposes the next round's goal (`continue`) or stops. An independent round review
judges it (`round-review`). `round-admit` opens the proposed round, applies a widened
objective and records the round's opening state; `round-assess` judges a finished round
against its goal. `round_state` is the gate between `evaluate` and either a new round or
`deposit`. Blind review scores and predictions are derived and reported; no rule here
reads them.
"""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, strings, text
from . import development, principles, publication, resources


DECISIONS = ("continue", "stop")
DIRECTIONS = ("vertical", "horizontal")
CANDIDATE_DISPOSITIONS = ("pursue", "rejected", "deferred")
CRITERION_KINDS = ("claim", "scope")
CHECKS_CONTINUE = ("impact", "demand", "novelty_risk", "feasibility", "distinctness", "continuity")
CHECKS_STOP = ("stop", "demand")
VERDICTS = ("approved", "not_approved", "unresolved")
CHECK_STATUSES = ("passed", "failed", "unresolved")
_ERROR = "invalid_round"


def _text(value, name):
    return text(value, name, code=_ERROR)


def _fields(value, required, optional=()):
    fields(value, required, optional, code=_ERROR)


def _strings(value, name, nonempty=False):
    return strings(value, name, nonempty=nonempty, code=_ERROR)


def _choice(value, choices, name):
    if value not in choices:
        raise ResearchError(_ERROR, name + " must be one of: " + ", ".join(choices))


def _items(value, name, nonempty=True):
    if not isinstance(value, list) or nonempty and not value:
        raise ResearchError(_ERROR, name + " must be an array" + (" with at least one entry" if nonempty else ""))
    return value


def _normalized(value):
    return " ".join(value.casefold().split())


def admissions(records):
    return sorted(records.get("round_admission", {}).values(), key=lambda a: a["number"])


def latest_admission(records):
    items = admissions(records)
    return items[-1] if items else None


def assessment_for(records, admission_id):
    return next((a for a in records.get("round_assessment", {}).values() if a["round_id"] == admission_id), None)


def current_number(records):
    latest = latest_admission(records)
    return latest["number"] if latest else 1


def active_round(records):
    """The admitted round without an assessment, or None."""
    latest = latest_admission(records)
    return latest if latest is not None and assessment_for(records, latest["id"]) is None else None


def _authors(records):
    return sorted({plan["payload"]["author"] for plan in records.get("cycle_plan", {}).values()})


class _RoundEvidence:
    """Source and result evidence as the development layer validates it, plus manuscript-review evidence."""

    def __init__(self, context, bundle):
        self.records, self.bundle = context.records, bundle
        self.development = development._Evidence(context)
        self.items = {}

    def many(self, values, name):
        linked = []
        for value in _items(values, name):
            if isinstance(value, dict) and value.get("kind") == "review":
                _fields(value, ("kind", "review_id"))
                review = self.records.get("manuscript_review", {}).get(value["review_id"])
                if review is None or review["bundle_digest"] != self.bundle["digest"]:
                    raise ResearchError("round_evidence_mismatch", "Review evidence names a manuscript review of the current bundle")
                item = {"reference": value, "review_digest": review["digest"]}
            else:
                item = self.development.one(value)
            self.items[digest(value)] = item
            linked.append(item)
        return linked

    def summary(self):
        return [self.items[key] for key in sorted(self.items)]


def _prepare_context(records, artifacts):
    evaluation = Evaluation.of(records, artifacts)
    context = development._Context(records, evaluation)
    context.require_objective()
    return context, publication._bundle(records, evaluation)


def _candidates(values, evidence):
    seen, pursued = set(), []
    for candidate in _items(values, "Candidates"):
        _fields(candidate, ("id", "direction", "statement", "disposition", "reason", "evidence"))
        for key in ("id", "statement", "reason"):
            _text(candidate[key], "Candidate " + key)
        if candidate["id"] in seen:
            raise ResearchError(_ERROR, "Candidate IDs must be unique")
        seen.add(candidate["id"])
        _choice(candidate["direction"], DIRECTIONS, "Candidate direction")
        _choice(candidate["disposition"], CANDIDATE_DISPOSITIONS, "Candidate disposition")
        evidence.many(candidate["evidence"], "Candidate evidence")
        if candidate["disposition"] == "pursue":
            pursued.append(candidate)
    return pursued


def _carried_key(item):
    return (item["assessment_id"], item["kind"], item.get("question") or item.get("cycle_id"))


def _carried(records, latest, values):
    opening = latest["opening"]["cycle_ids"] if latest else []
    closing = [identifier for identifier in records.get("cycle", {}) if identifier not in opening]
    expected = {_carried_key(item): item for item in development.carried_developments(records, closing)}
    seen = {}
    for item in _items(values, "Carried developments", nonempty=False):
        _fields(item, ("assessment_id", "kind", "disposition", "reason"), ("question", "cycle_id"))
        _text(item["reason"], "Carried development reason")
        _choice(item["disposition"], CANDIDATE_DISPOSITIONS, "Carried development disposition")
        key = _carried_key(item)
        if key not in expected or key in seen:
            raise ResearchError(_ERROR, "Dispose each carried development of the closing round exactly once")
        seen[key] = item
    missing = [expected[key] for key in expected if key not in seen]
    if missing:
        raise ResearchError("carried_development_missing", "Dispose of every development the closing round carried",
                            {"missing": missing})


def _goal(value, evidence, pursued):
    _fields(value, ("direction", "field_change", "statement", "contribution_delta", "beneficiaries", "success_criteria",
                    "stop_conditions", "continuity", "route", "risks", "evidence"))
    for key in ("statement", "contribution_delta", "continuity", "route"):
        _text(value[key], "Goal " + key)
    _choice(value["direction"], DIRECTIONS, "Goal direction")
    if value["direction"] != pursued["direction"] or _normalized(value["statement"]) != _normalized(pursued["statement"]):
        raise ResearchError(_ERROR, "The goal states the pursued candidate")
    if value["field_change"] is not None:
        _fields(value["field_change"], ("corpus", "primaryCategory"))
        for key in ("corpus", "primaryCategory"):
            _text(value["field_change"][key], "Field change " + key)
        if value["direction"] != "horizontal":
            raise ResearchError(_ERROR, "A field change is a horizontal goal")
    for beneficiary in _items(value["beneficiaries"], "Goal beneficiaries"):
        _fields(beneficiary, ("who", "bottleneck", "evidence"))
        _text(beneficiary["who"], "Beneficiary")
        _text(beneficiary["bottleneck"], "Beneficiary bottleneck")
        evidence.many(beneficiary["evidence"], "Beneficiary evidence")
    for name, items, kinds in (("Success criteria", value["success_criteria"], CRITERION_KINDS),
                               ("Stop conditions", value["stop_conditions"], None)):
        seen = set()
        for item in _items(items, name):
            _fields(item, ("id", "kind", "statement") if kinds else ("id", "statement"))
            _text(item["id"], name + " ID")
            _text(item["statement"], name)
            if kinds:
                _choice(item["kind"], kinds, name + " kind")
            if item["id"] in seen:
                raise ResearchError(_ERROR, name + " IDs must be unique")
            seen.add(item["id"])
    _strings(value["risks"], "Goal risks")
    evidence.many(value["evidence"], "Goal evidence")
    return value


def _limits(value, goal):
    if not isinstance(value, dict) or not set(value) <= {"literature", "experiment"}:
        raise ResearchError(_ERROR, "Resource limits name the literature and experiment purposes")
    for purpose, amounts in value.items():
        resources._amounts(amounts, "Round " + purpose + " limits")
    if goal["field_change"] is not None:
        literature = value.get("literature", {})
        if not literature.get("network_requests") or not literature.get("readings"):
            raise ResearchError("round_field_change_refused", "A field change needs literature allowances for the collected difference")


def _reopening(records, value, evidence):
    """A reopening names an assessed earlier round and carries evidence its decision did not."""
    if value is None:
        return
    _fields(value, ("round_id", "reason", "evidence"))
    _text(value["reason"], "Reopening reason")
    admission = records.get("round_admission", {}).get(value["round_id"])
    if admission is None or assessment_for(records, admission["id"]) is None:
        raise ResearchError(_ERROR, "Reopen an assessed earlier round")
    known = {digest(e["reference"]) for e in records["round_decision"][admission["decision_id"]]["evidence"]}
    changed = evidence.many(value["evidence"], "Reopening evidence")
    if all(digest(item["reference"]) in known for item in changed):
        raise ResearchError("round_reopening_unchanged", "Reopening needs evidence the earlier decision did not carry")


def _distinct(records, goal, reopening):
    earlier = [a["goal"]["statement"] for a in admissions(records)]
    for decision in records.get("round_decision", {}).values():
        earlier.extend(c["statement"] for c in decision["payload"]["candidates"] if c["disposition"] == "rejected")
    if _normalized(goal["statement"]) in {_normalized(s) for s in earlier} and reopening is None:
        raise ResearchError("round_goal_repeated", "A goal that repeats an earlier goal or a rejected candidate needs reopening with changed evidence")


def _direction_open(records, goal, reopening):
    unsuccessful = []
    for admission in reversed(admissions(records)):
        assessment = assessment_for(records, admission["id"])
        if assessment is None or assessment["successful"]:
            break
        unsuccessful.append(admission["goal"]["direction"])
    if len(unsuccessful) >= 2 and goal["direction"] in unsuccessful[:2] and reopening is None:
        raise ResearchError("round_direction_exhausted", "Two consecutive rounds in this direction were unsuccessful; change direction or reopen with changed evidence")


def _next(records, context, evidence, value, number, pursued):
    _fields(value, ("number", "objective", "objective_lineage", "goal", "resource_limits", "reopening"))
    if value["number"] != number + 1:
        raise ResearchError(_ERROR, "The next round follows the closing round")
    if value["objective"] == context.objective:
        if value["objective_lineage"] is not None:
            raise ResearchError(_ERROR, "An unchanged objective has no lineage")
    else:
        principles.widen_objective(records, value["objective"], value["objective_lineage"], "proposed")
    goal = _goal(value["goal"], evidence, pursued)
    _limits(value["resource_limits"], goal)
    _reopening(records, value["reopening"], evidence)
    _distinct(records, goal, value["reopening"])
    _direction_open(records, goal, value["reopening"])
    exhausted = [o for o in resources.obligations(records, "research") if o.get("purpose") == "development"]
    if exhausted:
        raise ResearchError("resource_budget_exhausted", "The development budget has no room for another round", exhausted[0])
    return value


def record_round(store, payload, *, expected_revision, request_id):
    """Close the current round on the exact bundle and decide: continue with a goal, or stop."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context, bundle = _prepare_context(records, artifacts)
        _fields(value, ("id", "closes", "decision", "bundle_digest", "candidates", "carried", "next", "reason"))
        _text(value["id"], "Round decision ID")
        _text(value["reason"], "Decision reason")
        _choice(value["decision"], DECISIONS, "Round decision")
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("round_bundle_mismatch", "Decide on the exact current manuscript bundle")
        number = current_number(records)
        if value["closes"] != number:
            raise ResearchError("round_number_mismatch", "Close the current round", {"current": number})
        latest = latest_admission(records)
        if latest is not None:
            assessment = assessment_for(records, latest["id"])
            if assessment is None or assessment["bundle_digest"] != bundle["digest"]:
                raise ResearchError("round_assessment_missing", "Assess the current round against its goal on this bundle before deciding")
        evidence = _RoundEvidence(context, bundle)
        pursued = _candidates(value["candidates"], evidence)
        _carried(records, latest, value["carried"])
        if value["decision"] == "continue":
            if len(pursued) != 1:
                raise ResearchError(_ERROR, "A continue decision pursues exactly one candidate")
            _next(records, context, evidence, value["next"], number, pursued[0])
        elif pursued or value["next"] is not None:
            raise ResearchError(_ERROR, "A stop decision pursues no candidate and proposes no round")
        record = {"id": value["id"], "payload": value, "closes": number, "decision": value["decision"],
                  "bundle_id": bundle["id"], "bundle_digest": bundle["digest"], "evidence": evidence.summary(),
                  "decided_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "round_decision", value["id"], record)], record

    return prepared_mutation(store, "round.decide", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def record_round_review(store, payload, *, expected_revision, request_id):
    """An independent assessor's judgment of one round decision."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context, bundle = _prepare_context(records, artifacts)
        _fields(value, ("id", "round_id", "round_digest", "assessor", "verdict", "checks", "limitations"))
        _text(value["id"], "Round review ID")
        decision = records.get("round_decision", {}).get(value["round_id"])
        if decision is None:
            raise ResearchError("unknown_round_decision", "Review a recorded round decision", {"id": value["round_id"]})
        if value["round_digest"] != decision["digest"] or decision["bundle_digest"] != bundle["digest"]:
            raise ResearchError("round_review_stale", "Review the exact current decision on the current bundle")
        publication.validate_assessor(context.artifacts, value["assessor"], _authors(records))
        _choice(value["verdict"], VERDICTS, "Round verdict")
        _strings(value["limitations"], "Review limitations", nonempty=True)
        kinds = CHECKS_CONTINUE if decision["decision"] == "continue" else CHECKS_STOP
        evidence = _RoundEvidence(context, bundle)
        seen = set()
        for check in _items(value["checks"], "Round checks"):
            _fields(check, ("kind", "status", "reason", "evidence"))
            _choice(check["kind"], kinds, "Round check")
            if check["kind"] in seen:
                raise ResearchError(_ERROR, "Address each round check once")
            seen.add(check["kind"])
            _choice(check["status"], CHECK_STATUSES, "Round check status")
            _text(check["reason"], "Round check reason")
            evidence.many(check["evidence"], "Round check evidence")
        if seen != set(kinds):
            raise ResearchError(_ERROR, "Address every round check: " + ", ".join(kinds))
        key = publication._assessor_key(value["assessor"]["id"])
        for saved in records.get("round_review", {}).values():
            if saved["round_id"] == decision["id"] and publication._assessor_key(saved["payload"]["assessor"]["id"]) == key:
                raise ResearchError("round_review_duplicate", "This assessor already reviewed this decision", {"review_id": saved["id"]})
            if value["verdict"] == "approved" and saved["verdict"] == "approved" and saved["closes"] == decision["closes"]:
                raise ResearchError("round_decision_duplicate", "This closing round already has an approved decision", {"review_id": saved["id"]})
        record = {"id": value["id"], "payload": value, "round_id": decision["id"], "round_digest": decision["digest"],
                  "closes": decision["closes"], "decision": decision["decision"], "verdict": value["verdict"],
                  "reviewed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "round_review", value["id"], record)], record

    return prepared_mutation(store, "round.review", payload, prepare, expected_revision=expected_revision, request_id=request_id)
```

`resources._amounts` is module-private by name but stable; use it, as `screening.py` and `reading.py` use other module-private helpers across modules in this codebase.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_rounds tests.test_research_publication tests.test_research_review_packets -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `feat(research): record and review round decisions on the exact bundle`

---

### Task 5: `round-admit` opens the next round

**Files:**
- Modify: `research_harness/rounds.py` (add `admit_round`, `_opening`, `_field_change`)
- Test: `tests/test_research_rounds.py`

**Interfaces:**
- Produces: `rounds.admit_round(store, payload, *, expected_revision, request_id)` (operation `round.admit`, record kind `round_admission`) with payload `{id, round_id, review_id, reason}`. Record:

```python
{"id", "number", "decision_id", "review_id", "goal", "objective", "objective_lineage", "resource_limits", "reopening",
 "opening": {"bundle_id", "bundle_digest", "claim_ids", "search_selection": {purpose: search_id or None},
             "requirement_ids", "cycle_ids", "reading_count", "accounts"},
 "reason", "admitted_revision", "request_id", "digest"}
```

- Consumes: `resources.charge(records, "development", {"rounds": 1})`, `principles.widen_objective`, `literature.SEARCH_PURPOSES + literature.DEVELOPMENT_PURPOSES`, `resources.account_report`, `workspace.strict_json`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_rounds.py`:

```python
class RoundAdmissionTests(RoundsCase):
    def test_admission_needs_an_approved_continue_decision_and_records_the_opening_state(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        early = {"id": "round-2", "round_id": decision["id"], "review_id": "absent", "reason": "Too early."}
        self.assert_error("unknown_round_review", lambda: self.mutate(rounds.admit_round, early))
        review = self.mutate(rounds.record_round_review, self.review_payload(decision, verdict="not_approved"))["result"]
        pending = dict(early, review_id=review["id"])
        self.assert_error("round_review_required", lambda: self.mutate(rounds.admit_round, pending))
        approved = self.mutate(rounds.record_round_review, self.review_payload(decision, assessor="second-assessor"))["result"]
        admission = self.mutate(rounds.admit_round, dict(early, review_id=approved["id"]))["result"]
        self.assertEqual((admission["number"], admission["objective"]), (2, self.objective))
        opening = admission["opening"]
        self.assertEqual((opening["bundle_id"], opening["claim_ids"], opening["cycle_ids"]), (bundle["id"], ["bound"], ["cycle-1"]))
        self.assertEqual(set(opening["search_selection"]), set(rounds.OPENING_PURPOSES))
        self.assertEqual(opening["search_selection"]["downstream"], None)
        self.assertEqual(opening["search_selection"]["direct"], "direct")
        records = self.store.snapshot()["records"]
        self.assertEqual(rounds.current_number(records), 2)
        self.assertEqual(rounds.active_round(records)["id"], "round-2")
        again = {"id": "round-2b", "round_id": decision["id"], "review_id": approved["id"], "reason": "Twice."}
        self.assert_error("round_active", lambda: self.mutate(rounds.admit_round, again))

    def test_admission_widens_the_objective_and_charges_the_development_budget(self):
        from research_harness.resources import set_budget
        limits = {unit: None for unit in ("network_requests", "source_bytes", "readings", "screenings",
                                          "model_input_tokens", "model_output_tokens", "wall_seconds", "rounds")}
        self.mutate(set_budget, {"profile": "research", "purpose": "development", "limits": dict(limits, rounds=1),
                                 "reason": "One development round at most."})
        wider = {"kind": "objective", "id": "wider-square-bound",
                 "statement": "For every integer n in [0, 5], n squared is at most 25, with equality at n = 5."}
        lineage = {"previous_id": self.objective["id"], "containment": "The range [0, 3] is contained in [0, 5]."}
        decision, review, admission = self.open_round(objective=wider, lineage=lineage)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["configuration"]["research"]["target"], wider)
        self.assertEqual(records["objective_lineage"][wider["id"]]["round_id"], admission["id"])
        self.assertEqual(records["resource_account"]["research:development"]["charged"]["rounds"], 1)
        self.assertEqual(records["research_objective"][self.objective["id"]], self.objective)

    def test_an_exhausted_development_budget_refuses_the_decision(self):
        from research_harness.resources import set_budget
        limits = {unit: None for unit in ("network_requests", "source_bytes", "readings", "screenings",
                                          "model_input_tokens", "model_output_tokens", "wall_seconds", "rounds")}
        self.mutate(set_budget, {"profile": "research", "purpose": "development", "limits": dict(limits, rounds=0),
                                 "reason": "No development round."})
        bundle = self.pin()
        self.assert_error("resource_budget_exhausted", lambda: self.mutate(rounds.record_round, self.decision_payload(bundle)))

    def test_a_field_change_stays_in_the_corpus_and_needs_literature_room(self):
        bundle = self.pin()
        moved = self.decision_payload(bundle, direction="horizontal", statement="Transfer the bound to a neighbouring category.")
        moved["candidates"][0]["direction"] = "horizontal"
        moved["next"]["goal"]["field_change"] = {"corpus": "pubmed", "primaryCategory": "q-bio.QM"}
        decision = self.mutate(rounds.record_round, moved)["result"]
        review = self.mutate(rounds.record_round_review, self.review_payload(decision))["result"]
        payload = {"id": "round-2", "round_id": decision["id"], "review_id": review["id"], "reason": "Move fields."}
        self.assert_error("round_field_change_refused", lambda: self.mutate(rounds.admit_round, payload))
        bare = self.decision_payload(bundle, direction="horizontal", statement="Transfer the bound to a neighbouring category.")
        bare["candidates"][0]["direction"] = "horizontal"
        bare["next"]["goal"]["field_change"] = {"corpus": "arxiv", "primaryCategory": "math.CO"}
        bare["next"]["resource_limits"] = {"experiment": {"wall_seconds": 10}}
        self.assert_error("round_field_change_refused", lambda: self.mutate(rounds.record_round, bare))
```

The narrower-objective case reaches `round_assessment_missing` because round 2 is active and unassessed; the unlinked objective itself is refused earlier in the widen path (covered by `test_an_unlinked_unchanged_or_malformed_objective_is_refused` in Task 3; containment is the author's assertion and is not checked from statement text). When the admitted round widened the objective, all four synthesis sections are stale until the round's literature stage re-records them (the Task 3 note and the spec's section 7); `run_round_work` in Task 8 refreshes all four. The first test expects `rounds.OPENING_PURPOSES`, the tuple of the nine search purposes the opening state snapshots.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_rounds.RoundAdmissionTests -v`
Expected: FAIL with `AttributeError: admit_round`.

- [ ] **Step 3: Implement**

Add to `research_harness/rounds.py` (import `from . import literature` and `from .workspace import strict_json`):

```python
OPENING_PURPOSES = literature.SEARCH_PURPOSES + literature.DEVELOPMENT_PURPOSES


def _opening(records, evaluation, bundle):
    """What the round starts from; freshness inside the round is judged against it."""
    claims = strict_json(evaluation.read(bundle["files"]["claims"]["artifact"]))
    selection = records.get("search_selection", {})
    return {"bundle_id": bundle["id"], "bundle_digest": bundle["digest"], "claim_ids": sorted(c["id"] for c in claims),
            "search_selection": {p: selection.get("research:" + p, {}).get("search_id") for p in OPENING_PURPOSES},
            "requirement_ids": sorted(records.get("fulltext_requirement", {})),
            "cycle_ids": sorted(records.get("cycle", {})), "reading_count": len(records.get("reading", {})),
            "accounts": resources.account_report(records, "research")}


def _field_change(records, change):
    definitions = [c["definition"] for c in records.get("collection", {}).values()]
    if change["corpus"] not in {d["corpus"] for d in definitions}:
        raise ResearchError("round_field_change_refused", "A field change stays in the study's corpus; a new corpus is not a development round")
    if change["primaryCategory"] in {d["primaryCategory"] for d in definitions}:
        raise ResearchError("round_field_change_refused", "The category is already part of the study's cohort")


def admit_round(store, payload, *, expected_revision, request_id):
    """Open the approved next round: charge the development budget, widen the objective, record the opening state."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context, bundle = _prepare_context(records, artifacts)
        _fields(value, ("id", "round_id", "review_id", "reason"))
        _text(value["id"], "Round admission ID")
        _text(value["reason"], "Admission reason")
        decision = records.get("round_decision", {}).get(value["round_id"])
        if decision is None:
            raise ResearchError("unknown_round_decision", "Admit a recorded round decision", {"id": value["round_id"]})
        review = records.get("round_review", {}).get(value["review_id"])
        if review is None:
            raise ResearchError("unknown_round_review", "Admit a reviewed round decision", {"id": value["review_id"]})
        if review["round_id"] != decision["id"] or review["round_digest"] != decision["digest"] or review["verdict"] != "approved":
            raise ResearchError("round_review_required", "An approved independent review of this decision is required")
        if decision["decision"] != "continue":
            raise ResearchError(_ERROR, "Admit a continue decision")
        if decision["bundle_digest"] != bundle["digest"]:
            raise ResearchError("round_review_stale", "The decision's bundle is no longer current")
        if active_round(records) is not None or any(a["decision_id"] == decision["id"] for a in admissions(records)):
            raise ResearchError("round_active", "Assess the current round before opening another")
        proposal = decision["payload"]["next"]
        if proposal["goal"]["field_change"] is not None:
            _field_change(records, proposal["goal"]["field_change"])
        changes = []
        charge = resources.charge(records, "development", {"rounds": 1})
        if charge is not None:
            changes.append(charge)
        if proposal["objective"] != context.objective:
            changes.extend(principles.widen_objective(records, proposal["objective"],
                                                      proposal["objective_lineage"], value["id"]))
        record = {"id": value["id"], "number": proposal["number"], "decision_id": decision["id"], "review_id": review["id"],
                  "goal": proposal["goal"], "objective": proposal["objective"], "objective_lineage": proposal["objective_lineage"],
                  "resource_limits": proposal["resource_limits"], "reopening": proposal["reopening"],
                  "opening": _opening(records, context.artifacts, bundle), "reason": value["reason"],
                  "admitted_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        changes.append(immutable_record(records, "round_admission", value["id"], record))
        return changes, record

    return prepared_mutation(store, "round.admit", payload, prepare, expected_revision=expected_revision, request_id=request_id)
```

`resources.charge` with `refuse=True` raises `resource_budget_exhausted` when a recorded development budget has no room and returns `None` when the study has no configured profile (never the case here).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_rounds -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `feat(research): admit an approved development round with its opening state`

---

### Task 6: Consequence purposes and the exemplar reading in the literature stage

**Files:**
- Modify: `research_harness/literature.py:335-341` (`record_search` accepts development purposes), `:479-501` (round obligations), `:604-608` (judgments cover development purposes when a round is active)
- Modify: `research_harness/reading.py:39` (`FULLTEXT_PURPOSES` gains `exemplar`)
- Modify: `tests/test_research_synthesis.py` (extract `record_purpose`)
- Test: `tests/test_research_rounds.py`, `tests/test_research_literature.py`

**Interfaces:**
- Produces: `record_search` accepts `purpose in SEARCH_PURPOSES + DEVELOPMENT_PURPOSES`; `_foundation_state` reports `round_search_missing` (with `purpose`) for every development purpose whose selected search is absent or equals the active round's opening selection, and `round_exemplar_missing` when no `fulltext_requirement` with purpose `exemplar` exists outside the opening requirement ids; both only while `rounds.active_round(records)` is not None and the profile is `research`. Development searches, once selected, enter `judgments` (so the synthesis sections depend on them) and get the same scope, dispositions, evidence and frontier checks as the five purposes.
- Produces: `SynthesisCase.record_purpose(purpose, identifier)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_research_synthesis.py`, refactor `foundation_searches` so its loop body is `self.record_purpose(purpose, purpose + identifier_suffix)`, where:

```python
    def record_purpose(self, purpose, identifier, profile="research"):
        from research_harness.acquisition import import_response
        from research_harness.literature import record_search
        self.sequence += 1
        source = import_response(self.store, "mcp", json.dumps({"q": purpose, "results": []}).encode(),
                                 source_url="https://example.org/search", captured_at="2026-09-07T12:00:00Z",
                                 media_type="application/json", mappings=[], expected_revision=self.store.revision,
                                 request_id="search-source-" + str(self.sequence))["result"]["source_id"]
        return self.mutate(record_search, {"id": identifier, "profile": profile, "purpose": purpose, "queries": [purpose],
            "responses": [{"source_id": source, "query": purpose, "query_locator": {"kind": "json", "pointer": "/q", "value": purpose},
                           "results_pointer": "/results"}],
            "captured_at": "2026-09-07T12:00:00Z", "scope": "The complete objective.", "found_work_ids": [],
            "verdict": "nothing-new", "cited_work_ids": [], "impact": "Nothing new was found.", "gaps": [], "dispositions": []})
```

Copy the exact `import_response` call and `record_search` payload from the existing `foundation_searches` body (`tests/test_research_synthesis.py:95-107`) rather than the sketch above, so the five-purpose behavior is unchanged; the sketch shows the shape only.

Append to `tests/test_research_rounds.py`:

```python
class RoundLiteratureTests(RoundsCase):
    def codes(self):
        from research_harness.literature import foundation_report
        return {o["code"]: o for o in foundation_report(self.store, "research")["obligations"]}

    def test_a_development_purpose_is_a_search_purpose(self):
        self.record_purpose("downstream", "downstream-early")
        self.assertEqual(self.store.snapshot()["records"]["search_selection"]["research:downstream"], {"search_id": "downstream-early"})

    def test_an_active_round_requires_fresh_consequence_searches_and_an_exemplar(self):
        self.record_purpose("downstream", "downstream-early")
        self.assertNotIn("round_search_missing", self.codes())
        self.open_round()
        missing = [o for o in self.store_obligations("research") if o["code"] == "round_search_missing"]
        self.assertEqual(sorted(o["purpose"] for o in missing), ["changes", "downstream", "exemplars", "next_step"])
        self.assertIn("round_exemplar_missing", self.codes())
        for purpose in ("downstream", "next_step", "exemplars"):
            self.record_purpose(purpose, purpose + "-round-2")
        missing = [o for o in self.store_obligations("research") if o["code"] == "round_search_missing"]
        self.assertEqual([o["purpose"] for o in missing], ["changes"])
        self.record_purpose("changes", "changes-round-2")
        self.assertNotIn("round_search_missing", self.codes())
        self.exemplar_requirement("round-2")
        self.assertNotIn("round_exemplar_missing", self.codes())

    def test_development_searches_enter_the_judgments_that_synthesis_depends_on(self):
        from research_harness.synthesis import synthesis_report
        self.open_round()
        before = synthesis_report(self.store, "research")["literature_digest"]
        self.record_purpose("downstream", "downstream-round-2")
        self.assertNotEqual(synthesis_report(self.store, "research")["literature_digest"], before)
```

`store_obligations(profile)` exists on `LiteratureCase` (`tests/literature_fixtures.py:130-132`). Append to `tests/test_research_literature.py` one case asserting that `record_search` refuses an unknown purpose with `invalid_search` and accepts `"changes"` (use the same `record_purpose` helper through a `SynthesisCase`-based test class if the module's base class lacks it; otherwise add it to `tests/test_research_rounds.py` instead).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_rounds.RoundLiteratureTests -v`
Expected: FAIL with `invalid_search` ("Use the five search purposes ...").

- [ ] **Step 3: Implement**

In `research_harness/reading.py`: `FULLTEXT_PURPOSES = ("major_claim", "novelty", "innovation", "validity", "exemplar")`.

In `research_harness/literature.py`, `record_search`: replace `if value["purpose"] not in SEARCH_PURPOSES` with `if value["purpose"] not in SEARCH_PURPOSES + DEVELOPMENT_PURPOSES` and update the message to "Use the five search purposes, the four development purposes, and the existing novelty verdict vocabulary".

In `_foundation_state`, replace the search block (lines 479-501) with a version that iterates over the required purposes:

```python
    from .rounds import active_round
    active = active_round(records) if profile == "research" else None
    purposes = SEARCH_PURPOSES + (DEVELOPMENT_PURPOSES if active else ())
    searches = {k: s for k, s in records.get("literature_search", {}).items() if s["profile"] == profile}
    selected_searches = {purpose: records.get("search_selection", {}).get(profile + ":" + purpose, {}).get("search_id") for purpose in purposes}
    for purpose in purposes:
        if active and purpose in DEVELOPMENT_PURPOSES and (selected_searches[purpose] is None
                                                            or selected_searches[purpose] == active["opening"]["search_selection"].get(purpose)):
            obligations.append(obligation("round_search_missing", "Record this development round's captured search for the purpose.", purpose=purpose))
            continue
        matches = [s for s in searches.values() if s["id"] == selected_searches[purpose] and s["purpose"] == purpose]
        ... (the existing body, unchanged)
    if active and not any(r["purpose"] == "exemplar" and r["id"] not in active["opening"]["requirement_ids"] for r in full_requirements.values()):
        obligations.append(obligation("round_exemplar_missing", "Select a development exemplar with require-fulltext (purpose exemplar) and read it in full."))
```

`selected_searches` already feeds `dependencies["search_selection"]` and `judgments`, so development searches enter the judgments digest once they are required. `full_requirements` is computed earlier in the same function (line 465). The lazy import avoids the module cycle (`rounds` imports `literature`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_rounds tests.test_research_synthesis tests.test_research_literature -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `feat(research): require consequence searches and an exemplar in a development round`

---
### Task 7: `manuscript-prediction`: the blind cohort prediction

**Files:**
- Create: `research_harness/predictions.py`
- Test: `tests/test_research_rounds.py`

**Interfaces:**
- Produces: `predictions.record_prediction(store, payload, *, expected_revision, request_id)` (operation `publication.predict`, record kind `manuscript_prediction`) with payload `{id, bundle_digest, blind, assessor, prediction, reasons}`; record `{id, payload, bundle_digest, prediction, reviewed_revision, request_id, digest}`.
- Produces: `predictions.measurement_summary(records, bundle) -> {"reviews": {"count", "overall", "contribution", "soundness", "presentation"}, "predictions": {"count", "percentile"}}` where each measure is `{"median": number or None, "spread": [min, max] or None}`; reviews are the latest per assessor for the bundle, as `publication_state` selects them.
- Consumes: `publication._bundle`, `publication.validate_assessor`, `rounds._authors` (move `_authors` to `publication.authors(records)` so both modules use it), the study's first collection definition from `literature_scope["research"]["collection_ids"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_rounds.py`:

```python
class PredictionTests(RoundsCase):
    def test_a_prediction_binds_the_bundle_the_cohort_and_a_blind_assessor(self):
        from research_harness import predictions
        bundle = self.pin()
        recorded = self.mutate(predictions.record_prediction, self.prediction_payload(bundle, "predictor-a"))["result"]
        self.assertEqual(recorded["prediction"]["percentile"], 30)
        wrong_cohort = self.prediction_payload(bundle, "predictor-b")
        wrong_cohort["prediction"]["category"] = "cs.CL"
        self.assert_error("prediction_cohort_mismatch", lambda: self.mutate(predictions.record_prediction, wrong_cohort))
        wrong_band = self.prediction_payload(bundle, "predictor-c", percentile=10, band=(20, 40))
        self.assert_error("invalid_prediction", lambda: self.mutate(predictions.record_prediction, wrong_band))
        again = self.prediction_payload(bundle, "Predictor-A")
        self.assert_error("manuscript_prediction_duplicate", lambda: self.mutate(predictions.record_prediction, again))
        author = self.prediction_payload(bundle, "cycle-author")
        self.assert_error("review_not_independent", lambda: self.mutate(predictions.record_prediction, author))
        sighted = self.prediction_payload(bundle, "predictor-d")
        sighted["blind"] = False
        self.assert_error("review_not_independent", lambda: self.mutate(predictions.record_prediction, sighted))
        stale = self.prediction_payload(bundle, "predictor-e")
        stale["bundle_digest"] = "0" * 64
        self.assert_error("publication_review_stale", lambda: self.mutate(predictions.record_prediction, stale))

    def test_measurement_summary_reports_medians_and_spreads(self):
        from research_harness import predictions
        bundle = self.pin()
        self.measure(bundle, "one", percentiles=(30, 25, 40))
        summary = predictions.measurement_summary(self.store.snapshot()["records"], bundle)
        self.assertEqual(summary["predictions"], {"count": 3, "percentile": {"median": 30, "spread": [25, 40]}})
        self.assertEqual(summary["reviews"]["count"], 5)
        self.assertEqual(summary["reviews"]["overall"], {"median": 6, "spread": [6, 6]})
        empty = predictions.measurement_summary(self.store.snapshot()["records"], {"digest": "0" * 64})
        self.assertEqual(empty["predictions"], {"count": 0, "percentile": {"median": None, "spread": None}})
```

The review count is 5 because `pin()` records two gate reviews and `measure()` three more on the same bundle.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_rounds.PredictionTests -v`
Expected: FAIL with `ModuleNotFoundError: research_harness.predictions`.

- [ ] **Step 3: Implement**

Move `_authors` from `rounds.py` to `publication.py` as `authors(records)` and import it in `rounds.py`. Create `research_harness/predictions.py`:

```python
"""Blind cohort predictions on the exact manuscript bundle, and the measurement summary.

A prediction is the percentile a blind assessor expects the paper to reach in the study's
frozen cohort, in the shape the market's verdict carries. Predictions are recorded and
summarized as results; no gate rule reads them.
"""

from statistics import median

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .publication import _assessor_key, _bundle, authors, validate_assessor

_ERROR = "invalid_prediction"


def _cohort(records):
    """The study's primary cohort definition: the first collection of the research scope."""
    scope = records.get("literature_scope", {}).get("research", {})
    for identifier in scope.get("collection_ids", []):
        collection = records.get("collection", {}).get(identifier)
        if collection is not None:
            return collection["definition"]
    raise ResearchError("prediction_cohort_mismatch", "The study has no frozen cohort to predict against")


def _prediction(records, value):
    fields(value, ("corpus", "category", "windowStart", "windowEnd", "percentile", "band"), code=_ERROR)
    fields(value["band"], ("best", "worst"), code=_ERROR)
    numbers = (value["percentile"], value["band"]["best"], value["band"]["worst"])
    if any(type(n) is not int or not 1 <= n <= 100 for n in numbers):
        raise ResearchError(_ERROR, "Percentile and band are integers from 1 to 100")
    if not value["band"]["best"] <= value["percentile"] <= value["band"]["worst"]:
        raise ResearchError(_ERROR, "The band contains the percentile: best <= percentile <= worst")
    definition = _cohort(records)
    expected = {"corpus": definition["corpus"], "category": definition["primaryCategory"],
                "windowStart": definition["windowStart"], "windowEnd": definition["windowEnd"]}
    if {k: value[k] for k in expected} != expected:
        raise ResearchError("prediction_cohort_mismatch", "Predict against the study's frozen cohort", expected)
    return value


def record_prediction(store, payload, *, expected_revision, request_id):
    """One blind assessor's cohort prediction for the exact current bundle."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        evaluation = Evaluation(records, artifacts)
        fields(value, ("id", "bundle_digest", "blind", "assessor", "prediction", "reasons"), code=_ERROR)
        text(value["id"], "Prediction ID", code=_ERROR)
        bundle = _bundle(records, evaluation)
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("publication_review_stale", "Predict on the exact current manuscript bundle")
        if value["blind"] is not True:
            raise ResearchError("review_not_independent", "Supply an identified independent blind assessor")
        validate_assessor(evaluation, value["assessor"], authors(records))
        strings(value["reasons"], "Prediction reasons", nonempty=True, code=_ERROR)
        _prediction(records, value["prediction"])
        key = _assessor_key(value["assessor"]["id"])
        for saved in records.get("manuscript_prediction", {}).values():
            if saved["bundle_digest"] == bundle["digest"] and _assessor_key(saved["payload"]["assessor"]["id"]) == key:
                raise ResearchError("manuscript_prediction_duplicate", "This assessor already predicted this exact bundle", {"prediction_id": saved["id"]})
        record = {"id": value["id"], "payload": value, "bundle_digest": bundle["digest"], "prediction": value["prediction"],
                  "reviewed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "manuscript_prediction", value["id"], record)], record

    return prepared_mutation(store, "publication.predict", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _measure(values):
    return {"median": median(values) if values else None, "spread": [min(values), max(values)] if values else None}


def measurement_summary(records, bundle):
    """Latest review per assessor and every prediction on the bundle, as medians and spreads."""
    latest = {}
    for saved in records.get("manuscript_review", {}).values():
        if saved["bundle_digest"] == bundle["digest"]:
            key = _assessor_key(saved["assessor"]["id"])
            if key not in latest or saved["reviewed_revision"] > latest[key]["reviewed_revision"]:
                latest[key] = saved
    cores = [saved["core"] for saved in latest.values()]
    percentiles = [saved["prediction"]["percentile"] for saved in records.get("manuscript_prediction", {}).values()
                   if saved["bundle_digest"] == bundle["digest"]]
    reviews = {"count": len(cores)}
    reviews.update({key: _measure([core[key] for core in cores]) for key in ("overall", "contribution", "soundness", "presentation")})
    return {"reviews": reviews, "predictions": {"count": len(percentiles), "percentile": _measure(percentiles)}}
```

`statistics.median` of an even count returns a float mean of the two middle values; the test uses three values.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_rounds -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `feat(research): record blind cohort predictions on the exact bundle`

---

### Task 8: `round-assess` and the round's derived progress

**Files:**
- Modify: `research_harness/rounds.py` (add `derive_progress`, `assess_round`)
- Modify: `tests/rounds_fixtures.py` (add `run_round_work`)
- Test: `tests/test_research_rounds.py`

**Interfaces:**
- Produces: `rounds.derive_progress(records, evaluation, admission, bundle) -> dict` with keys `fresh_purposes` (list), `exemplar` (bool), `cycles` (list of new assessed cycle ids), `readings` (int), `new_claim_ids`, `revised_claim_ids`, `superseded_claim_ids`, `dropped_claim_ids` (lists; empty when `bundle` is None), `usage` ({purpose: {unit: charged since opening}}), `measurement` (from `predictions.measurement_summary`, or None), `unproductive` (bool).
- Produces: `rounds.assess_round(store, payload, *, expected_revision, request_id)` (operation `round.assess`, record kind `round_assessment`), payload `{id, round_id, bundle_digest, criteria, stop_conditions, summary}`; record `{id, payload, round_id, bundle_digest, derived, successful, unproductive, assessed_revision, request_id, digest}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/rounds_fixtures.py`:

```python
    def run_round_work(self, suffix, *, new_cycle=True):
        """What a development round does: consequence searches, an exemplar, refreshed synthesis, re-assessed
        earlier cycles, and (by default) one new assessed cycle selected as the readiness candidate."""
        api = self.development()
        # A new full-text requirement changes the citation frontier, so every search judgment is
        # recorded again (the existing 0.38.0 rule) before the synthesis sections are refreshed.
        self.exemplar_requirement(suffix)
        self.foundation_searches(identifier_suffix="-" + suffix)
        self.development_searches(suffix)
        self.refresh_synthesis(suffix)
        records = self.store.snapshot()["records"]
        earlier = []
        for cycle in sorted(records["cycle"].values(), key=lambda c: c["id"]):
            plan = records["cycle_plan"][cycle["id"]]["payload"]
            execution = records["execution"][cycle["execution_ids"][0]]["payload"]
            payload = self.assessment(plan, execution, identifier="assessment-" + cycle["id"] + "-" + suffix)
            self.mutate(api.assess_cycle, payload)
            earlier.append((cycle["id"], execution))
        if not new_cycle:
            self.save_checkpoint("cycle-1", "assessment-cycle-1-" + suffix, "checkpoint-" + suffix)
            self.mutate(api.record_readiness_review, self.review(self.execution_payload, identifier="review-" + suffix))
            return None
        plan = self.plan("cycle-" + suffix)
        plan.update(question="Does the bound hold on the wider range " + suffix + "?",
                    distinguishing_test="Enumerate the wider range " + suffix + ".")
        plan, execution = self.run_cycle(plan, run_id="run-" + suffix)
        payload = self.assessment(plan, execution, identifier="assessment-" + suffix)
        payload["development"]["branches"].extend({"cycle_id": identifier, "disposition": "resolved",
            "reason": "Assessed again under the current preparation.", "evidence": [self.result_evidence(old)]}
            for identifier, old in earlier)
        self.mutate(api.assess_cycle, payload)
        self.save_checkpoint("cycle-" + suffix, "assessment-" + suffix, "checkpoint-" + suffix)
        self.mutate(api.record_readiness_review, self.review(execution, identifier="review-" + suffix))
        return execution
```

Append to `tests/test_research_rounds.py`:

```python
class RoundAssessmentTests(RoundsCase):
    def test_a_productive_round_is_assessed_with_derived_progress(self):
        decision, review, admission = self.open_round()
        execution = self.run_round_work("r2")
        bundle = self.pin(self.claims("wider"), identifier="paper-r2")
        self.measure(bundle, "r2")
        assessed = self.mutate(rounds.assess_round, self.assess_payload(admission, bundle))["result"]
        derived = assessed["derived"]
        self.assertEqual(sorted(derived["fresh_purposes"]), ["changes", "downstream", "exemplars", "next_step"])
        self.assertTrue(derived["exemplar"])
        self.assertEqual(derived["cycles"], ["cycle-r2"])
        self.assertEqual((derived["new_claim_ids"], derived["dropped_claim_ids"]), (["wider"], []))
        self.assertEqual(derived["measurement"]["predictions"]["count"], 3)
        self.assertGreaterEqual(derived["readings"], 0)
        self.assertEqual((assessed["successful"], assessed["unproductive"]), (True, False))
        self.assert_error("round_already_assessed", lambda: self.mutate(rounds.assess_round, self.assess_payload(admission, bundle)))

    def test_an_unproductive_round_is_unsuccessful_even_when_a_criterion_is_observed(self):
        decision, review, admission = self.open_round()
        self.run_round_work("r2", new_cycle=False)
        bundle = self.pin(identifier="paper-r2")
        assessed = self.mutate(rounds.assess_round, self.assess_payload(admission, bundle, observed=True))["result"]
        self.assertEqual((assessed["successful"], assessed["unproductive"]), (False, True))
        self.assertEqual(assessed["derived"]["cycles"], [])
        self.assertEqual(assessed["derived"]["new_claim_ids"], [])

    def test_every_criterion_and_stop_condition_is_judged_once_on_the_current_bundle(self):
        decision, review, admission = self.open_round()
        self.run_round_work("r2")
        bundle = self.pin(self.claims("wider"), identifier="paper-r2")
        stale = self.assess_payload(admission, bundle)
        stale["bundle_digest"] = "0" * 64
        self.assert_error("round_bundle_mismatch", lambda: self.mutate(rounds.assess_round, stale))
        short = self.assess_payload(admission, bundle)
        short["criteria"] = []
        self.assert_error("invalid_round", lambda: self.mutate(rounds.assess_round, short))
        unknown = self.assess_payload(admission, bundle)
        unknown["stop_conditions"][0]["id"] = "other"
        self.assert_error("invalid_round", lambda: self.mutate(rounds.assess_round, unknown))

    def test_claims_continuity_marks_revised_superseded_and_dropped_claims(self):
        decision, review, admission = self.open_round()
        self.run_round_work("r2")
        records = self.store.snapshot()["records"]
        from research_harness.evaluation import Evaluation
        evaluation = Evaluation(records, self.artifacts)
        bundle = self.pin(self.claims("wider", revised=["bound"]), identifier="paper-revised")
        progress = rounds.derive_progress(self.store.snapshot()["records"], evaluation, admission, bundle)
        self.assertEqual((progress["new_claim_ids"], progress["revised_claim_ids"], progress["dropped_claim_ids"]), (["wider"], ["bound"], []))
        bundle = self.pin(self.claims("wider", superseded=["bound"]), identifier="paper-superseded")
        progress = rounds.derive_progress(self.store.snapshot()["records"], evaluation, admission, bundle)
        self.assertEqual((progress["superseded_claim_ids"], progress["dropped_claim_ids"]), (["bound"], []))
        (self.root / "evidence/claims.json").write_text('[{"id": "wider", "claim": "Claim wider holds."}]')
        bundle = self.mutate(publication.prepare_publication, {"id": "paper-dropped", "files": {"pdf": "draft/paper.pdf",
            "abstract": "draft/abstract.txt", "bibliography": "draft/references.bib", "claims": "evidence/claims.json", "sources": None},
            "claim_evidence": [{"claim_id": "wider", "evidence": [self.result_evidence(self.execution_payload)]}]})["result"]
        progress = rounds.derive_progress(self.store.snapshot()["records"], evaluation, admission, bundle)
        self.assertEqual(progress["dropped_claim_ids"], ["bound"])
```

Add `from research_harness import publication` to the test module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_rounds.RoundAssessmentTests -v`
Expected: FAIL with `AttributeError: assess_round` / `derive_progress`.

- [ ] **Step 3: Implement**

Add to `research_harness/rounds.py` (import `from . import predictions` and `from .literature import DEVELOPMENT_PURPOSES`):

```python
STATUSES = ("observed", "not_observed", "unresolved")


def _charged_since(records, opening_accounts):
    usage = {}
    for purpose, units in resources.account_report(records, "research").items():
        before = opening_accounts.get(purpose, {})
        usage[purpose] = {unit: value["charged"] - before.get(unit, {}).get("charged", 0) for unit, value in units.items()}
    return usage


def derive_progress(records, evaluation, admission, bundle):
    """What the round did since its admission, judged against the opening state it recorded."""
    opening = admission["opening"]
    selection = records.get("search_selection", {})
    fresh = [p for p in DEVELOPMENT_PURPOSES
             if selection.get("research:" + p, {}).get("search_id") not in (None, opening["search_selection"].get(p))]
    exemplar = any(r["purpose"] == "exemplar" and r["id"] not in opening["requirement_ids"]
                   for r in records.get("fulltext_requirement", {}).values())
    cycles = sorted(c["id"] for c in records.get("cycle", {}).values()
                    if c["id"] not in opening["cycle_ids"] and c["assessment_id"] is not None)
    claims = {}
    if bundle is not None:
        claims = {c["id"]: c for c in strict_json(evaluation.read(bundle["files"]["claims"]["artifact"]))}
    new = sorted(i for i, c in claims.items() if i not in opening["claim_ids"] and "superseded" not in c)
    progress = {"fresh_purposes": fresh, "exemplar": exemplar, "cycles": cycles,
                "readings": len(records.get("reading", {})) - opening["reading_count"],
                "new_claim_ids": new,
                "revised_claim_ids": sorted(i for i, c in claims.items() if "revised" in c),
                "superseded_claim_ids": sorted(i for i, c in claims.items() if "superseded" in c),
                "dropped_claim_ids": sorted(i for i in opening["claim_ids"] if i not in claims) if bundle else [],
                "usage": _charged_since(records, opening["accounts"]),
                "measurement": predictions.measurement_summary(records, bundle) if bundle else None}
    progress["unproductive"] = not (new and len(fresh) == len(DEVELOPMENT_PURPOSES) and exemplar and cycles)
    return progress


def _judged(values, expected, name, evidence):
    seen = []
    for item in _items(values, name):
        _fields(item, ("id", "status", "explanation", "evidence"))
        if item["id"] not in expected or item["id"] in seen:
            raise ResearchError(_ERROR, "Judge each " + name.lower() + " of the goal exactly once")
        seen.append(item["id"])
        _choice(item["status"], STATUSES, name + " status")
        _text(item["explanation"], name + " explanation")
        evidence.many(item["evidence"], name + " evidence")
    if set(seen) != set(expected):
        raise ResearchError(_ERROR, "Judge every " + name.lower() + " of the goal")
    return values


def assess_round(store, payload, *, expected_revision, request_id):
    """Judge the current round against its goal on the exact current bundle; derive what the round did."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context, bundle = _prepare_context(records, artifacts)
        _fields(value, ("id", "round_id", "bundle_digest", "criteria", "stop_conditions", "summary"))
        _text(value["id"], "Round assessment ID")
        _text(value["summary"], "Round summary")
        admission = records.get("round_admission", {}).get(value["round_id"])
        if admission is None:
            raise ResearchError("unknown_round", "Assess an admitted round", {"id": value["round_id"]})
        if assessment_for(records, admission["id"]) is not None:
            raise ResearchError("round_already_assessed", "This round already has its assessment", {"round_id": admission["id"]})
        if latest_admission(records)["id"] != admission["id"]:
            raise ResearchError(_ERROR, "Assess the current round")
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("round_bundle_mismatch", "Assess the round on the exact current manuscript bundle")
        evidence = _RoundEvidence(context, bundle)
        goal = admission["goal"]
        criteria = _judged(value["criteria"], {c["id"] for c in goal["success_criteria"]}, "Success criteria", evidence)
        _judged(value["stop_conditions"], {s["id"] for s in goal["stop_conditions"]}, "Stop conditions", evidence)
        derived = derive_progress(records, context.artifacts, admission, bundle)
        successful = any(c["status"] == "observed" for c in criteria) and not derived["unproductive"]
        record = {"id": value["id"], "payload": value, "round_id": admission["id"], "bundle_digest": bundle["digest"],
                  "derived": derived, "successful": successful, "unproductive": derived["unproductive"],
                  "assessed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "round_assessment", value["id"], record)], record

    return prepared_mutation(store, "round.assess", payload, prepare, expected_revision=expected_revision, request_id=request_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_rounds -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `feat(research): assess a development round against its goal`

---

### Task 9: The `round` gate, the stage transitions and the status report

**Files:**
- Modify: `research_harness/rounds.py` (add `round_state`, `round_summary`)
- Modify: `research_harness/gates.py:34-73` (`gate_state`), `:82-109` (`validate_transition`)
- Modify: `research_harness/cli.py:58` (`GATES`), `:144-168` (`status_report`)
- Modify: `research_harness/report_views.py:14-24` (`PRIORITY`), `:71-84` (`status_summary`)
- Test: `tests/test_research_rounds.py`, `tests/test_research_report_views.py`

**Interfaces:**
- Produces: `rounds.round_state(records, artifacts) -> report` with keys `ready`, `obligations`, `decision_obligations`, `progress_obligations`, `round` (number), `active` (bool), `decision` (`"continue"`, `"stop"` or None; set only when an approved decision binds the current bundle), `decision_id`, `next` (the proposal, for an approved continue), `admitted` (bool), `progress` (from `derive_progress`, for an active round), `measurement`, `digest`, `counts`, `mechanical_only`.
- Produces: `rounds.round_summary(report) -> dict` bounded: `{"number", "active", "decision", "admitted", "obligations": count, "progress": {"fresh_purposes", "exemplar", "cycles", "new_claims", "dropped_claims", "readings"}, "measurement"}`.
- Produces: `gate round`; `validate_transition` rules: `evaluate -> literature` requires `rounds.active_round(records)`; `evaluate -> deposit` requires the `round` gate ready with `decision == "stop"`.
- Produces: `status_report` includes `round` and, at the `evaluate` stage, merges the publication and round obligations into `obligations` (deduplicated by digest, ordered by `PRIORITY`); `status_summary` copies `round`.

Obligation codes produced by `round_state`: `round_assessment_missing`, `round_search_missing`, `round_exemplar_missing`, `round_cycle_missing`, `round_claim_missing`, `round_claims_dropped`, `round_decision_missing`, `round_review_missing`, `round_review_pending`, `round_admission_missing`, plus whatever `publication._bundle` raises (`publication_bundle_missing`, `publication_readiness_stale`, `publication_artifact_changed`, `readiness_required`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_rounds.py`:

```python
class RoundGateTests(RoundsCase):
    def gate(self):
        from research_harness.gates import gate_report
        return gate_report(self.store, "round")

    def codes(self, report):
        return [o["code"] for o in report["obligations"]]

    def test_the_gate_walks_from_bundle_to_decision_to_review_to_admission(self):
        self.assertIn("publication_bundle_missing", self.codes(self.gate()))
        bundle = self.pin()
        self.assertEqual(self.codes(self.gate()), ["round_decision_missing"])
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.assertEqual(self.codes(self.gate()), ["round_review_missing"])
        self.mutate(rounds.record_round_review, self.review_payload(decision, verdict="not_approved"))
        self.assertEqual(self.codes(self.gate()), ["round_review_pending"])
        review = self.mutate(rounds.record_round_review, self.review_payload(decision, assessor="second"))["result"]
        report = self.gate()
        self.assertEqual((self.codes(report), report["decision"], report["admitted"]), (["round_admission_missing"], "continue", False))
        self.mutate(rounds.admit_round, {"id": "round-2", "round_id": decision["id"], "review_id": review["id"], "reason": "Open."})
        report = self.gate()
        self.assertTrue(report["active"])
        self.assertEqual(report["round"], 2)
        self.assertIn("round_assessment_missing", self.codes(report))
        self.assertIn("round_cycle_missing", self.codes(report))
        self.assertEqual(sorted(o["purpose"] for o in report["obligations"] if o["code"] == "round_search_missing"),
                         ["changes", "downstream", "exemplars", "next_step"])

    def test_a_stop_decision_makes_the_gate_ready(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))["result"]
        self.mutate(rounds.record_round_review, self.review_payload(decision))
        report = self.gate()
        self.assertEqual((report["ready"], report["decision"]), (True, "stop"))

    def test_transitions_follow_the_round_gate(self):
        from research_harness.evaluation import Evaluation
        from research_harness.gates import validate_transition
        evaluate, literature = {"stage": "evaluate", "status": "pending"}, {"stage": "literature", "status": "pending"}
        deposit = {"stage": "deposit", "status": "pending"}

        def transition(target):
            records = self.store.snapshot()["records"]
            return validate_transition(records, Evaluation(records, self.artifacts), evaluate, target)
        self.assert_error("readiness_required", lambda: transition(literature))
        bundle = self.pin()
        self.assert_error("readiness_required", lambda: transition(deposit))
        decision, review, admission = self.open_round(bundle)
        transition(literature)
        self.assert_error("readiness_required", lambda: transition(deposit))

    def test_a_stop_after_an_assessed_round_permits_deposit(self):
        from research_harness.evaluation import Evaluation
        from research_harness.gates import validate_transition
        decision, review, admission = self.open_round()
        self.run_round_work("r2")
        bundle = self.pin(self.claims("wider"), identifier="paper-r2")
        self.mutate(rounds.assess_round, self.assess_payload(admission, bundle))
        stop = self.mutate(rounds.record_round, self.decision_payload(bundle, closes=2, decision="stop"))["result"]
        self.mutate(rounds.record_round_review, self.review_payload(stop))
        records = self.store.snapshot()["records"]
        validate_transition(records, Evaluation(records, self.artifacts), {"stage": "evaluate", "status": "pending"},
                            {"stage": "deposit", "status": "pending"})

    def test_two_unsuccessful_rounds_exhaust_a_direction(self):
        decision, review, admission = self.open_round()
        self.run_round_work("r2", new_cycle=False)
        bundle = self.pin(identifier="paper-r2")
        self.mutate(rounds.assess_round, self.assess_payload(admission, bundle, observed=False))
        decision, review, admission = self.open_round(bundle, closes=2, statement="Extend the finite bound to every integer in [0, 7].")
        self.run_round_work("r3", new_cycle=False)
        bundle = self.pin(identifier="paper-r3")
        self.mutate(rounds.assess_round, self.assess_payload(admission, bundle, observed=False))
        same = self.decision_payload(bundle, closes=3, statement="Extend the finite bound to every integer in [0, 9].")
        self.assert_error("round_direction_exhausted", lambda: self.mutate(rounds.record_round, same))
        other = self.decision_payload(bundle, closes=3, direction="horizontal", statement="Apply the bound to a neighbouring category.")
        other["candidates"][0]["direction"] = "horizontal"
        self.mutate(rounds.record_round, other)
        reopened = self.decision_payload(bundle, closes=3, statement="Extend the finite bound to every integer in [0, 9].",
                                         reopening={"round_id": "round-2", "reason": "A new measurement changed the picture.",
                                                    "evidence": [{"kind": "review", "review_id": "paper-r3-gate-1"}]})
        recorded = self.mutate(rounds.record_round, reopened)["result"]
        self.assertEqual(recorded["payload"]["next"]["reopening"]["round_id"], "round-2")


class RoundStatusTests(RoundsCase):
    def test_status_at_evaluate_carries_publication_and_round_obligations_in_priority_order(self):
        from research_harness.cli import status_report
        from research_harness.report_views import status_summary
        self.set_stage("literature")
        self.pin()
        report = status_report(self.store)
        self.assertNotIn("round_decision_missing", [o["code"] for o in report["obligations"]])
        self.assertEqual(report["round"]["number"], 1)
        self.set_stage("evaluate")
        report = status_report(self.store)
        self.assertEqual([o["code"] for o in report["obligations"]], ["round_decision_missing"])
        self.assertFalse(report["ready"])
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% changed\n%%EOF")
        codes = [o["code"] for o in status_report(self.store)["obligations"]]
        self.assertEqual(codes, ["publication_artifact_changed"])
        summary = status_summary(status_report(self.store))
        self.assertEqual(summary["round"]["number"], 1)

    def test_a_manuscript_awaiting_reviews_is_led_to_the_reviews_before_the_round(self):
        from research_harness.cli import status_report
        self.set_stage("evaluate")
        self.pin(reviews=0)
        codes = [o["code"] for o in status_report(self.store)["obligations"]]
        self.assertEqual(codes, ["manuscript_reviews_required", "round_decision_missing"])
```

Append to `tests/test_research_report_views.py` a case that adds a `round` object with 10000-character strings in a `progress` list and asserts the summary stays under 16 KiB and `summary["round"]["number"]` is preserved (use the existing `report_fixture()` and `status_summary`; `round_summary` output contains only numbers, booleans, short purpose names and the measurement dict, so pass such a dict as `report["round"]`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_rounds.RoundGateTests tests.test_research_rounds.RoundStatusTests -v`
Expected: FAIL with `invalid_gate` for `round` and `AttributeError: round_state`.

- [ ] **Step 3: Implement**

Add to `research_harness/rounds.py`:

```python
def _decision_obligations(records, bundle, number):
    """The approved decision for the current bundle, or what is missing for it."""
    reviews = list(records.get("round_review", {}).values())
    candidates = [d for d in records.get("round_decision", {}).values() if d["bundle_digest"] == bundle["digest"] and d["closes"] == number]
    approved = [d for d in candidates if any(r["round_id"] == d["id"] and r["verdict"] == "approved" for r in reviews)]
    if approved:
        decision = approved[0]
        admitted = any(a["decision_id"] == decision["id"] for a in admissions(records))
        if decision["decision"] == "continue" and not admitted:
            return decision, admitted, [obligation("round_admission_missing", "Admit the approved next round with round-admit.", decision_id=decision["id"])]
        return decision, admitted, []
    if candidates:
        latest = max(candidates, key=lambda d: d["decided_revision"])
        pending = any(r["round_id"] == latest["id"] for r in reviews)
        code = "round_review_pending" if pending else "round_review_missing"
        return None, False, [obligation(code, "Obtain an approving independent review of the round decision.", decision_id=latest["id"])]
    return None, False, [obligation("round_decision_missing", "Decide on this exact manuscript: continue with a goal, or stop.", round=number)]


def _progress_obligations(records, evaluation, admission, bundle):
    progress = derive_progress(records, evaluation, admission, bundle)
    obligations = [obligation("round_search_missing", "Record this development round's captured search for the purpose.", purpose=p)
                   for p in DEVELOPMENT_PURPOSES if p not in progress["fresh_purposes"]]
    if not progress["exemplar"]:
        obligations.append(obligation("round_exemplar_missing", "Select a development exemplar with require-fulltext (purpose exemplar) and read it in full."))
    if not progress["cycles"]:
        obligations.append(obligation("round_cycle_missing", "Plan, execute and assess at least one cycle in this round."))
    if bundle is not None:
        if progress["dropped_claim_ids"]:
            obligations.append(obligation("round_claims_dropped", "Keep, revise or supersede every claim of the round's opening bundle.", claim_ids=progress["dropped_claim_ids"]))
        if not progress["new_claim_ids"]:
            obligations.append(obligation("round_claim_missing", "The round's manuscript needs at least one new evidenced claim."))
    obligations.append(obligation("round_assessment_missing", "Assess the current round against its goal before deciding.", round_id=admission["id"]))
    return obligations, progress


def round_state(records, artifacts):
    """The gate between evaluate and either a new development round or deposit."""
    evaluation = Evaluation.of(records, artifacts)
    latest = latest_admission(records)
    number = latest["number"] if latest else 1
    active = active_round(records)
    bundle, decision_obligations = None, []
    try:
        bundle = publication._bundle(records, evaluation)
    except ResearchError as error:
        decision_obligations.append(obligation(error.code, error.message, **(error.details or {})))
    progress_obligations, progress = [], None
    if active is not None:
        progress_obligations, progress = _progress_obligations(records, evaluation, active, bundle)
    decision, admitted = None, False
    if bundle is not None:
        decision, admitted, pending = _decision_obligations(records, bundle, number)
        decision_obligations.extend(pending)
    obligations = decision_obligations + progress_obligations
    settled = decision if decision is not None and not decision_obligations else None
    return {"ready": not obligations, "obligations": obligations, "decision_obligations": decision_obligations,
            "progress_obligations": progress_obligations, "round": number, "active": active is not None,
            "decision": settled["decision"] if settled else None, "decision_id": settled["id"] if settled else None,
            "next": settled["payload"]["next"] if settled and settled["decision"] == "continue" else None,
            "admitted": admitted, "progress": progress,
            "measurement": predictions.measurement_summary(records, bundle) if bundle else None,
            "digest": digest({"round": number, "decision": settled["digest"] if settled else None, "obligations": obligations}),
            "counts": {"obligations": len(obligations)}, "mechanical_only": True}


def round_summary(report):
    """A bounded view of the round gate for status reports."""
    progress = report.get("progress") or {}
    return {"number": report["round"], "active": report["active"], "decision": report["decision"], "admitted": report["admitted"],
            "obligations": len(report["obligations"]),
            "progress": {"fresh_purposes": progress.get("fresh_purposes", []), "exemplar": progress.get("exemplar", False),
                         "cycles": len(progress.get("cycles", [])), "new_claims": len(progress.get("new_claim_ids", [])),
                         "dropped_claims": len(progress.get("dropped_claim_ids", [])), "readings": progress.get("readings", 0)}
            if progress else None,
            "measurement": report.get("measurement")}
```

In `research_harness/gates.py`:

```python
    if action == "round":
        from .rounds import round_state
        return round_state(records, artifacts)
```

inside `gate_state` before `if action == "initiate":`, and in `validate_transition`, inside the `if (source, target) in BACKWARD:` block before `return`:

```python
        if (source, target) == ("evaluate", "literature"):
            from .rounds import active_round, round_state
            if active_round(records) is None:
                require_ready(round_state(records, artifacts), "entering a development round")
```

and before the `prerequisites` check:

```python
    if target == "deposit":
        from .rounds import round_state
        report = require_ready(round_state(records, artifacts), "entering deposit")
        if report["decision"] != "stop":
            raise ResearchError("readiness_required", "Deposit follows an approved decision to stop developing the paper",
                                {"action": "entering deposit", "decision": report["decision"], "next": "exactory-research gate round"})
```

(`require_ready` on an active round's gate reports the progress obligations, so the message names what the round still owes; when the gate is ready without an admission the error names `round_admission_missing`.)

In `research_harness/cli.py`: `GATES = (..., "submitted", "round")`. In `status_report`, after `obligations = current_obligations(...)`:

```python
    round_report = None
    if config is not None and profile == "research":
        from .rounds import round_state, round_summary
        round_report = round_state(records, evaluation)
        if study and study["stage"] == "evaluate":
            from .publication import publication_state
            merged = obligations + publication_state(records, evaluation, "publication")["obligations"] + round_report["obligations"]
            obligations = sorted({digest(o): o for o in merged}.values(), key=priority_key)
            report = dict(report, obligations=obligations, ready=not obligations)
```

where `priority_key` is `report_views.order_obligations`' key (expose `order_key(o)` from `report_views` and use it in both). Add `round=round_summary(round_report) if round_report else None` to the returned dict.

In `research_harness/report_views.py`, extend `PRIORITY` with, in this order after `resource_budget_exhausted`: `publication_bundle_missing`, `publication_readiness_stale`, `publication_artifact_changed`, `manuscript_reviews_required`, `round_assessment_missing`, `round_search_missing`, `round_exemplar_missing`, `round_cycle_missing`, `round_claims_dropped`, `round_claim_missing`, `round_decision_missing`, `round_review_missing`, `round_review_pending`, `round_admission_missing`. In `status_summary` add `"round": report.get("round")`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_rounds tests.test_research_report_views tests.test_research_gates tests.test_research_cli -v`
Expected: PASS (the CLI tests exercise `status --summary` end to end and its 16 KiB bound).

- [ ] **Step 5: Commit**

Message: `feat(research): gate deposit and new rounds on the round decision`

---
### Task 10: The round packet and `export --kind round`

**Files:**
- Modify: `research_harness/review_packets.py` (add `round_packet`)
- Modify: `research_harness/review_delivery.py` (add `deliver_round`)
- Test: `tests/test_research_review_packets.py`

**Interfaces:**
- Produces: `review_packets.round_packet(records, bundle, decision, report) -> manifest` with `kind: "round"`, `manuscript` (the manuscript packet), `reviews` (latest per assessor for the bundle: `{"kind": assessor kind, "core": rubric core}`), `predictions` (the prediction objects), `decision` (the decision payload and digest under review), `rounds` (every admission's `number`, `goal`, `objective`, `resource_limits` and its assessment's `payload`, `derived`, `successful`, `unproductive`), `development` (the `development` block of every cycle assessment of the closing round, keyed by assessment id), `synthesis` (the selected `context` and `innovation` payloads), `searches` (selected `downstream` and `next_step` records: `purpose`, `found_work_ids`, `dispositions`, `impact`, `gaps`), `resources` (`resources.account_report`), `digest`. Scrubbed with `_FORBIDDEN_KEYS + ("authors", "author")`.
- Produces: `review_delivery.deliver_round(store, destination)`: the latest decision on the current bundle (else `round_decision_missing`), delivered like the other packets.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_review_packets.py` (its `PacketTests` build on `DevelopmentCase`; add a second class on `RoundsCase`):

```python
class RoundPacketTests(RoundsCase):
    def test_the_round_packet_carries_history_and_reviews_but_no_labels_or_authors(self):
        from research_harness import rounds
        from research_harness.review_delivery import deliver_round
        bundle = self.pin()
        self.measure(bundle, "one")
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        directory = self.root / "reviews" / "round-1"
        deliver_round(self.store, directory)
        text = (directory / "inputs.json").read_text()
        manifest = json.loads(text)
        self.assertEqual(manifest["kind"], "round")
        self.assertEqual(manifest["decision"]["digest"], decision["digest"])
        self.assertEqual(len(manifest["reviews"]), 5)
        self.assertEqual(len(manifest["predictions"]), 3)
        self.assertIn("overall", text)
        self.assertIn("percentile", text)
        self.assertIn("candidates", text)
        for forbidden in ('"request_id"', '"token"', '_revision"', '"authors"', '"author"'):
            self.assertNotIn(forbidden, text)
        self.assertIn("assessment-1", manifest["development"])

    def test_the_manuscript_packet_stays_blind_to_rounds_and_predictions(self):
        from research_harness.review_delivery import deliver_manuscript
        decision, review, admission = self.open_round()
        self.run_round_work("r2")
        bundle = self.pin(self.claims("wider"), identifier="paper-r2")
        self.measure(bundle, "r2")
        directory = self.root / "reviews" / "manuscript-r2"
        deliver_manuscript(self.store, directory)
        text = (directory / "inputs.json").read_text()
        for forbidden in ('"round', '"percentile"', '"core"', '"goal"', '"overall"'):
            self.assertNotIn(forbidden, text)
```

Add `import json` and `from rounds_fixtures import RoundsCase` to the module.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_review_packets -v`
Expected: FAIL with `ImportError: deliver_round`.

- [ ] **Step 3: Implement**

In `research_harness/review_packets.py`:

```python
def round_packet(records, bundle, decision, report):
    """The round assessor judges the program: the paper, its reviews and predictions, the decision, and the history."""
    from .publication import _assessor_key
    from .resources import account_report
    latest = {}
    for saved in records.get("manuscript_review", {}).values():
        if saved["bundle_digest"] == bundle["digest"]:
            key = _assessor_key(saved["assessor"]["id"])
            if key not in latest or saved["reviewed_revision"] > latest[key]["reviewed_revision"]:
                latest[key] = saved
    reviews = [{"kind": saved["assessor"]["kind"], "core": saved["core"]} for saved in latest.values()]
    predictions = [p["prediction"] for p in records.get("manuscript_prediction", {}).values() if p["bundle_digest"] == bundle["digest"]]
    rounds = []
    for admission in sorted(records.get("round_admission", {}).values(), key=lambda a: a["number"]):
        assessment = next((a for a in records.get("round_assessment", {}).values() if a["round_id"] == admission["id"]), None)
        rounds.append({"number": admission["number"], "goal": admission["goal"], "objective": admission["objective"],
                       "resource_limits": admission["resource_limits"],
                       "assessment": None if assessment is None else {k: assessment[k] for k in ("payload", "derived", "successful", "unproductive")}})
    opening = rounds[-1]["number"] if rounds else None
    latest_admission = max(records.get("round_admission", {}).values(), key=lambda a: a["number"], default=None)
    opened = latest_admission["opening"]["cycle_ids"] if latest_admission else []
    development = {}
    for cycle in records.get("cycle", {}).values():
        if cycle["id"] not in opened and cycle["assessment_id"] is not None:
            development[cycle["assessment_id"]] = records["cycle_assessment"][cycle["assessment_id"]]["payload"]["development"]
    synthesis = {kind: records["synthesis"][records["synthesis_selection"]["research:" + kind]["id"]]["payload"]
                 for kind in ("context", "innovation") if "research:" + kind in records.get("synthesis_selection", {})}
    searches = {}
    for purpose in ("downstream", "next_step"):
        selected = records.get("search_selection", {}).get("research:" + purpose)
        if selected:
            search = records["literature_search"][selected["search_id"]]
            searches[purpose] = {k: search.get(k) for k in ("purpose", "found_work_ids", "dispositions", "impact", "gaps")}
    manifest = {"kind": "round", "manuscript": manuscript_packet(records, bundle), "reviews": reviews, "predictions": predictions,
                "decision": {"payload": decision["payload"], "digest": decision["digest"], "closes": decision["closes"]},
                "rounds": rounds, "development": development, "synthesis": synthesis, "searches": searches,
                "resources": account_report(records, "research"), "measurement": report.get("measurement")}
    manifest["digest"] = digest({"bundle": bundle["digest"], "decision": decision["digest"]})
    return scrub(manifest, _FORBIDDEN_KEYS + ("authors", "author"))
```

(`opening` is unused; drop it. The closing round's cycles are the ones not in the latest admission's opening cycle ids, or every cycle when no round was admitted.)

In `research_harness/review_delivery.py`:

```python
def deliver_round(store, destination):
    from .rounds import round_state
    snapshot = store.snapshot()
    evaluation = Evaluation(snapshot["records"], ArtifactStore(store.root))
    report = round_state(snapshot["records"], evaluation)
    bundle = publication_state(snapshot["records"], evaluation, "manuscript")["bundle"]
    if bundle is None:
        raise ResearchError("publication_bundle_missing", "Pin the exact manuscript before delivering a round packet")
    decisions = [d for d in snapshot["records"].get("round_decision", {}).values() if d["bundle_digest"] == bundle["digest"]]
    if not decisions:
        raise ResearchError("round_decision_missing", "Record the round decision before delivering it for review")
    decision = max(decisions, key=lambda d: d["decided_revision"])
    return _deliver(store, destination, round_packet(snapshot["records"], bundle, decision, report))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_review_packets -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `feat(research): deliver a round packet to the independent round assessor`

---

### Task 11: CLI commands, example catalog and CLI documentation

**Files:**
- Modify: `research_harness/cli.py:22-58` (`OPERATIONS`, `GATES`), `:107-111` (export kinds), `:217-231` (export dispatch)
- Modify: `docs/research-cli-examples.json` (five new entries)
- Modify: `docs/research-cli.md` (operation rows, a "Development rounds" section, `gate round`, `export --kind round`, the `next_round` disposition in "Assessing unfinished work", the `exemplar` purpose)
- Modify: `docs/research-workflow.md` (a "Develop the paper across rounds" section between manuscript assessment and verification)
- Test: `tests/test_research_cli.py`, `tests/test_research_guidance.py` (existing tests enforce the catalog and the workflow's shell blocks)

**Interfaces:**
- Produces: commands `round`, `round-review`, `round-admit`, `round-assess`, `manuscript-prediction` (each `--file`, `--expected-revision`, `--request-id`); `gate round`; `export --kind round --destination PATH`; `example` for each new operation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_cli.py` in `ResearchCliTests`:

```python
    def test_round_commands_are_registered_with_examples_and_the_round_gate(self):
        from research_harness.cli import GATES, OPERATIONS
        for command in ("round", "round-review", "round-admit", "round-assess", "manuscript-prediction"):
            self.assertIn(command, OPERATIONS)
            example = self.run_cli("exactory-research", "example", command)
            self.assertEqual(example.returncode, 0, example.stderr)
        self.assertIn("round", GATES)
        self.init_lab()
        gate = self.run_cli("exactory-research", "gate", "round")
        self.assertNotEqual(gate.returncode, 0)
        self.assertIn("publication_bundle_missing", gate.stderr)
        export = self.run_cli("exactory-research", "export", "--kind", "round", "--destination", str(self.root / "round-packet"))
        self.assertNotEqual(export.returncode, 0)
        self.assertIn("publication_bundle_missing", export.stderr)
```

The example entries must equal what `exactory-research example` prints (`tests/test_research_guidance.py:108-111` compares the catalog with the CLI for every operation), and the `docs/research-workflow.md` shell blocks must parse with the real parsers (`tests/test_research_guidance.py:60-82`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research_cli.ResearchCliTests.test_round_commands_are_registered_with_examples_and_the_round_gate -v`
Expected: FAIL (`round` not in `OPERATIONS`).

- [ ] **Step 3: Implement**

In `research_harness/cli.py`: import `predictions` and `rounds`; add to `OPERATIONS`:

```python
    "round": rounds.record_round,
    "round-review": rounds.record_round_review,
    "round-admit": rounds.admit_round,
    "round-assess": rounds.assess_round,
    "manuscript-prediction": predictions.record_prediction,
```

`GATES = (..., "submitted", "round")`; export `--kind` choices gain `"round"`; in `run`, `elif args.kind == "round": return deliver_round(store, args.destination)` inside the destination-requiring branch.

In `docs/research-cli-examples.json`, add entries (use the catalog's placeholder conventions: `"0" * 64` digests, `SOURCE`, `RESULT`, the artifact placeholder for provenance):

- `round`: the decision payload of the spec's section 5.1 with `closes: 1`, `decision: "continue"`, two candidates (one `pursue`, one `rejected`), `carried: []`, `next` with `number: 2`, the unchanged objective, `objective_lineage: null`, a goal with one `claim` criterion and one `scope` criterion, one stop condition, `continuity`, `route`, `risks`, `field_change: null`, `resource_limits: {"literature": {"network_requests": 60, "readings": 40}, "experiment": {"wall_seconds": 7200}}`, `reopening: null`, and a `reason`.
- `round-review`: `{"id": "round-review-1", "round_id": "round-decision-1", "round_digest": "0"*64, "assessor": {...}, "verdict": "approved", "checks": [six checks impact, demand, novelty_risk, feasibility, distinctness, continuity, each with status "passed", a reason and [SOURCE, RESULT]], "limitations": [...]}`.
- `round-admit`: `{"id": "round-2", "round_id": "round-decision-1", "review_id": "round-review-1", "reason": "The approved goal opens the round."}`.
- `round-assess`: `{"id": "round-assessment-2", "round_id": "round-2", "bundle_digest": "0"*64, "criteria": [{"id": "sc-1", "status": "observed", "explanation": "...", "evidence": [RESULT]}, {"id": "sc-2", ...}], "stop_conditions": [{"id": "st-1", "status": "not_observed", "explanation": "...", "evidence": [RESULT]}], "summary": "..."}`.
- `manuscript-prediction`: `{"id": "prediction-1", "bundle_digest": "0"*64, "blind": true, "assessor": {...}, "prediction": {"corpus": "arxiv", "category": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31", "percentile": 25, "band": {"best": 15, "worst": 40}}, "reasons": ["..."]}`.

In `docs/research-cli.md`:

- "Operation fields" rows for the five operations in the row format the table uses.
- In "Assessing unfinished work": add `next_round` to the two disposition tables: "a useful development that exceeds this round's admitted scope; it leaves no readiness obligation and is carried to the round gate, which must dispose of it".
- A new section `## Development rounds` after "Review, publication, and verification" describing, in the reference's voice: the decision on the exact bundle (`round`), the review with its checks (`round-review`), the admission and its opening state (`round-admit`), the round's obligations while active (`round_search_missing`, `round_exemplar_missing`, `round_cycle_missing`, `round_claims_dropped`, `round_claim_missing`, `round_assessment_missing`), the assessment (`round-assess`) and what it derives, the blind prediction (`manuscript-prediction`) and its cohort binding, `gate round`, the transition rules, `export --kind round`, the claim markers `revised` and `superseded`, the `exemplar` full-text purpose, the `development` budget purpose with the `rounds` unit, and the error codes each operation raises.
- In "Status, projections, and recovery": `status` carries `round`; at the `evaluate` stage the status obligations also carry the publication and round obligations; `gate ACTION` lists `round`.

In `docs/research-workflow.md`, add a section `## Develop the paper across rounds` after "Manuscript assessment and publication" with the decision procedure and one `sh` block:

```sh
exactory-research example manuscript-prediction > prediction.json
exactory-research manuscript-prediction --file prediction.json --expected-revision REVISION --request-id predict-001
exactory-research gate round
exactory-research example round > round-decision.json
exactory-research round --file round-decision.json --expected-revision REVISION --request-id round-decide-001
exactory-research export --kind round --destination reviews/round-001
exactory-research round-review --file round-review.json --expected-revision REVISION --request-id round-review-001
exactory-research round-admit --file round-admit.json --expected-revision REVISION --request-id round-admit-001
exactory-lab decide --stage evaluate --decision "Open round 2" --why "The approved goal names the contribution the paper lacks."
exactory-lab state set --stage literature --status pending
```

and, for the end of a round, `round-assess` followed by the next decision, and for a stop, `exactory-lab state set --stage deposit --status pending`. State in prose: every blind measurement reviewer returns the rubric core and a prediction; the round's literature records the four consequence purposes and one exemplar requirement; the earlier cycles are assessed again under the current preparation before the round's candidate is selected; claims keep their ids and are revised or superseded, never dropped; scores and predictions are results, not criteria; the loop ends only through the recorded exits.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research_cli tests.test_research_guidance -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `feat(research): expose the round operations, gate, export and examples`

---

### Task 12: Constitution Version 3, skills, README, version 0.39.0, release note and testing record

**Files:**
- Modify: `RESEARCH_CONSTITUTION.md` (Version 3, new section)
- Modify: `skills/ai-science/SKILL.md`, `skills/ai-science/LOOP.md`, `skills/ai-science/STUDY.md`, `skills/literature-review/SKILL.md`, `skills/ideate/SKILL.md`, `skills/experiment/SKILL.md`, `skills/write/SKILL.md`, `skills/write/WORKSPACE.md`, `skills/evaluate/SKILL.md`, `skills/evaluate/RUBRIC.md`, `skills/deposit/SKILL.md`
- Modify: `README.md`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `tests/test_research_guidance.py:86-87`, `tests/test_manifest.py:40`, `tests/test_codex.py:25`, `tests/test_research_report_views.py:22,54` (fixture literal)
- Create: `docs/releases/0.39.0.md`, `docs/testing/development-rounds.md`
- Regenerate: `codex/skills/*/SKILL.md` with `python3 codex/generate.py`
- Test: `tests/test_research_principles.py` (constitution version), `tests/test_codex.py`, `tests/test_manifest.py`, `tests/test_research_guidance.py`

**Interfaces:**
- Produces: constitution `Version: 3`; `runtime_provenance()["constitution"]["version"] == "3"`; manifests and tests at `0.39.0`.

- [ ] **Step 1: Write the failing tests**

Change the three version literals to `"0.39.0"` and the release-note path to `docs/releases/0.39.0.md` (`tests/test_research_guidance.py:86-87`, `tests/test_manifest.py:40`, `tests/test_codex.py:25`). In `tests/test_research_principles.py` find the test that reads the constitution version (search for `"2"` next to `constitution`) and change the expected version to `"3"`; if no such assertion exists, add one:

```python
    def test_the_distributed_constitution_is_version_3(self):
        from research_harness.principles import constitution_contract
        self.assertEqual(constitution_contract()["version"], "3")
```

Run: `python3 -m unittest tests.test_manifest tests.test_codex tests.test_research_guidance tests.test_research_principles -v`
Expected: FAIL on the version literals and the missing release note.

- [ ] **Step 2: Implement the policy and guidance edits**

`RESEARCH_CONSTITUTION.md`: `Version: 3`; append the section "Development across rounds" from the spec's section 16, verbatim.

`skills/ai-science/SKILL.md`: in the stage table, change the `evaluate` row's required product to "Current manuscript bundle, separate independent blind assessments with cohort predictions, and the round decision"; add a paragraph after "Research development and manuscript improvement": "A study develops its paper across rounds. At the end of `evaluate`, decide on the exact bundle with `round`, obtain the independent round review, and either admit the next round (`round-admit`, then `--stage literature --status pending`) or stop and enter `deposit`. LOOP.md's third section governs the round." Keep the rest.

`skills/ai-science/LOOP.md`: add a third section "Develop the paper across rounds" after "Improve the manuscript": the round gate procedure (read `gate round`; write the candidates from the carried developments, the review findings, `context` and `innovation`, and the Grand Challenges; choose one goal with direction, delta, beneficiaries, criteria, stop conditions and continuity, or stop with every candidate disposed; deliver `export --kind round` to a fresh subagent that returns the review JSON; admit; enter literature), what the round's literature stage records (the four consequence purposes, the exemplar requirement, re-recorded judgments and sections), the re-assessment of earlier cycles before the round's candidate, claims continuity, the measurement with predictions, and the exits table from the spec's section 10 in prose. State that scores and predictions are recorded results and never criteria.

`skills/ai-science/STUDY.md`: mention the round records in the SQLite list and that `status` reports `round`.

`skills/literature-review/SKILL.md`: a paragraph "In a development round" naming the four purposes and their questions, the exemplar requirement (`require-fulltext` with purpose `exemplar`), and that the earlier judgments are re-recorded with carried findings when the frontier changes.

`skills/ideate/SKILL.md`: read the admitted goal and the carried developments before forming hypotheses; every hypothesis names the success criterion it serves; successors inherit across rounds through objective lineage.

`skills/experiment/SKILL.md`: the `next_round` disposition for a useful development that exceeds the round's admitted scope.

`skills/write/SKILL.md` and `WORKSPACE.md`: claims keep their ids across rounds; a changed claim carries `revised: {previous, reason}`, a replaced claim `superseded: {reason}`; `score_history.jsonl` lines gain `predictions` and `prediction_median`.

`skills/evaluate/SKILL.md` and `RUBRIC.md`: each blind reviewer returns the rubric core and, as a second file, the prediction in the market's shape; record it with `manuscript-prediction`; the core stays exactly the eight fields.

`skills/deposit/SKILL.md`: deposit follows an approved `stop` decision (`gate round`).

`README.md`: replace the "Version 0.38.0 makes ..." paragraph with a "Version 0.39.0 develops a paper across independently approved research rounds ..." paragraph that links `docs/releases/0.39.0.md` and keeps the links to the earlier notes. Manifests: `"version": "0.39.0"`.

`docs/releases/0.39.0.md` in the format of `docs/releases/0.38.0.md`: intro linking the spec; "Research workflow" bullets (the five operations, the gate and transitions, the consequence purposes and exemplar, `next_round`, objective lineage, claims continuity, predictions and measurement summary, the `development` budget, status `round` and the merged obligations at `evaluate`, the round packet); "Distribution and validation" naming the new tests and the testing record; "Upgrade and resume" listing `constitution_revalidation_required` for every study, `round_decision_missing` for a study at `evaluate`, the new `rounds` unit in budget payloads, and that a study before `evaluate` sees no other change.

`docs/testing/development-rounds.md`: the TDD evidence record in the format of `docs/testing/literature-efficiency.md`: date and host, the baseline suite (901 tests OK, 1025 s), the per-task RED/GREEN commands and results as actually observed, the mapping table from R01 to R25 to the test methods, and the final full-suite result.

Regenerate the Codex mirror: `python3 codex/generate.py` then `python3 codex/generate.py --check`.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_manifest tests.test_codex tests.test_research_guidance tests.test_research_principles tests.test_hooks -v`
Expected: PASS.

- [ ] **Step 4: Commit**

Message: `docs: constitution version 3, round guidance and release 0.39.0`

---

## Release

After Task 12, in this order:

1. Full suite: `python3 -m unittest discover -s tests` (about 17 minutes). Record the count and result in `docs/testing/development-rounds.md`. Every test passes or the plan is not done.
2. Two-reviewer pass, as for 0.38.0: one reviewer for record integrity and gate semantics (every new obligation code has a producer and a test; every write is `prepared_mutation`; no read command mutates; scrub covers the round packet), one for specification conformance (spec sections 5 to 14 against the code and the docs). Every blocker and major finding is fixed in code with a regression test; the spec is amended where a rule was shown wrong. Record the findings and fixes in the testing record.
3. `python3 codex/generate.py --check`, `git diff --check`, and the full suite again if code changed.
4. Push `feat/development-rounds`, open a PR to `main` in `exactory/exactory-client` with the release summary and the attribution lines, wait for the six CI jobs, merge, fast-forward `local-dev` and `dev` to the merge commit, tag `exactory--v0.39.0` ("Exactory 0.39.0"), create the GitHub release from `docs/releases/0.39.0.md` with absolute links. The marketplace manifest needs no change.
5. Pull `main` in the primary checkout `plugins/exactory-client` so the installed CLI runs 0.39.0; the closed-gravity study then reports `constitution_revalidation_required` on its next command.

## Self-review notes

- Spec coverage: sections 5.1 to 5.7 (Tasks 2 to 8), 6 (Task 9), 7 (Task 6), 8 (Tasks 2 and 3), 9 (Tasks 7 to 9), 10 (Tasks 4, 8, 9), 11 (Task 10), 12 (Tasks 1 and 5), 13 and 14 (Task 9), 15 and 16 (Tasks 11 and 12), 17 (Release).
- The validation cases: R01, R02, R05, R06, R07 (Task 4), R03 and R09 (Tasks 4 and 5 through `round_assessment_missing` and `round_decision_duplicate`), R04 (Tasks 2 and 4), R08 (Task 4), R10, R19, R23, R24 (Task 9), R11, R12 (Tasks 3 and 5), R13, R14 (Task 6), R15, R16, R18 (Task 8 and 9), R17 (Task 7), R20 (Tasks 1 and 5), R21, R22 (Task 10), R25 (Task 5; the difference-collection linking is the existing per-version reading rule, asserted in Task 5 by the unchanged cohort obligations when a second collection over the same members is added in a test the executor writes with `self.cohort((1,))` under a second category through `collect_cohort`).
- Type consistency: `round_decision` records carry `closes`, `decision`, `bundle_digest`, `digest`; `round_review` carries `round_id`, `round_digest`, `verdict`, `closes`; `round_admission` carries `number`, `decision_id`, `review_id`, `goal`, `opening`; `round_assessment` carries `round_id`, `bundle_digest`, `derived`, `successful`, `unproductive`; the same names are used in every task.
