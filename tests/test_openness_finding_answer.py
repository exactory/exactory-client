"""A problem openness answer records its judgment, including an honest unresolved finding."""

import copy
import itertools
import json
import unittest
from unittest.mock import Mock, patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import native_review
from research_harness.errors import ResearchError


def build_packet(forecast="supported_open"):
    outcomes = {name: "possible" if name == forecast else "blocked"
                for name in ("supported_open", "already_solved")}
    return {"native": {"kind": "result", "subject_digest": "a" * 64, "claim_digest": "b" * 64,
                       "reviewer": {"source": "host", "actor_id": "reviewer", "attestation_id": "assignment:first"},
                       "scientific_context": {"audit_forecast": {"outcomes": outcomes, "searched_through": "2026-11-14"},
                                              "openness_minimum": {"statement": "The exact statement", "neighbours": []},
                                              "decisive_work_ids": ["work-1"]}},
            "reading_record": {"turns": [{"assignment_id": "reading-1"}]}}


def build_answer(packet, outcome="supported_open", grounds=()):
    native = packet.get("native", packet)
    result = {"schema_version": 3, **{key: copy.deepcopy(native[key])
              for key in ("subject_digest", "claim_digest", "reviewer")},
              "findings": {key: "The delivered evidence supports this assessment."
                           for key in native_review.RESULT_FINDINGS}}
    result["findings"]["openness"] = {"outcome": outcome, "coverage": "The exact formulation was searched.",
                                       "reason": "The delivered evidence establishes this finding.",
                                       "grounds": list(grounds)}
    return result


class FindingRuleTests(unittest.TestCase):
    def test_every_forecast_ground_subset_and_outcome_follows_the_rule(self):
        kinds = ("reviewer_gap", "source_settles", "settling_source_rejected", "own_argument", "packet_defect")
        for forecast, flags, outcome in itertools.product(
                ("supported_open", "already_solved", "blocked"), itertools.product((False, True), repeat=5),
                ("supported_open", "already_solved", "unresolved")):
            packet = build_packet(forecast)
            grounds = [dict(kind=kind, text="A scientific ground.",
                            **({"work_id": "work-1"} if kind == "source_settles" else {}))
                       for kind, enabled in zip(kinds, flags) if enabled]
            expected = ((outcome == forecast and not grounds) if outcome != "unresolved"
                        else bool(grounds) or forecast == "blocked")
            with self.subTest(forecast=forecast, grounds=[row["kind"] for row in grounds], outcome=outcome):
                if expected:
                    native_review.validate_finding(packet, build_answer(packet, outcome, grounds))
                else:
                    with self.assertRaises(ResearchError) as caught:
                        native_review.validate_finding(packet, build_answer(packet, outcome, grounds))
                    self.assertEqual(caught.exception.code, "openness_finding_invalid")
                    self.assertEqual(caught.exception.details["forecast"], packet["native"]["scientific_context"]["audit_forecast"])

    def test_prompt_renders_the_same_ordered_rule_table(self):
        self.assertEqual([row["number"] for row in native_review.FINDING_RULES], [1, 2, 3])
        for row in native_review.FINDING_RULES:
            self.assertIn(native_review.render_finding_rule(row), native_review.PROBLEM_OPENNESS_PROMPT)
        for kind, description in native_review.GROUND_NEXT_STEPS.items():
            self.assertIn(kind, native_review.PROBLEM_OPENNESS_PROMPT)
            self.assertTrue(description)

    def test_ground_bindings_use_only_the_assignment_packet(self):
        packet = build_packet("blocked")
        good = {"kind": "source_settles", "text": "The read source settles this statement.",
                "work_id": "work-1", "turns": ["reading-1"]}
        native_review.validate_finding(packet, build_answer(packet, "unresolved", [good]))
        for key, value in (("work_id", "unbound"), ("turns", ["another-chain"])):
            bad = dict(good, **{key: value})
            with self.subTest(key=key), self.assertRaises(ResearchError) as caught:
                native_review.validate_finding(packet, build_answer(packet, "unresolved", [bad]))
            self.assertEqual(caught.exception.code, "openness_finding_invalid")

            self.assertEqual(caught.exception.details["path"], "/findings/openness/grounds/0/" + key)
            self.assertEqual(caught.exception.contract_check, "openness.finding")

    def test_ground_cannot_name_a_final_stage_turn(self):
        packet = build_packet("blocked")
        packet["reading_record"]["turns"][0]["stage"] = "final"
        with self.assertRaises(ResearchError) as caught:
            native_review.validate_finding(packet, build_answer(packet, "unresolved", [dict(
                kind="own_argument", text="An argument is explained in a recorded turn.", turns=["reading-1"])]))
        self.assertEqual(caught.exception.details["rule"], "reading_turns")

    def test_answer_has_exact_bindings_closed_fields_and_text_findings(self):
        packet = build_packet()
        modifications = (((), "decision", "revise"), ((), "schema_version", 2),
                         ((), "claim_digest", "c" * 64), (("findings",), "policy", {}),
                         (("findings", "openness"), "coverage", {}),
                         (("findings", "openness"), "grounds", "none"),
                         (("findings", "openness"), "evidence", []))
        for path, field, value in modifications:
            answer = build_answer(packet)
            target = answer
            for component in path:
                target = target[component]
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ResearchError) as caught:
                native_review.validate_finding(packet, answer)
            self.assertEqual(caught.exception.code, "openness_finding_invalid")

    def test_single_turn_and_structured_formulation_coverage(self):
        packet = build_packet()["native"]
        answer = build_answer(packet)
        answer["findings"]["openness"]["coverage"] = [{"formulation": "The exact statement", "assessment": "Searched."}]
        native_review.validate_finding(packet, answer)

    def test_incident_revise_outputs_stay_legacy_and_are_refused_as_findings(self):
        from research_harness.openness_reading import validate_turn_output
        packet = build_packet("blocked")
        for reason in ("There are unread sources.", "The searches leave gaps.", "The openness remains unresolved."):
            output = {"decision": "revise", "reason": reason, "objections": []}
            packet["reading_record"]["protocol"] = "exactory-openness-reading-v5"
            validate_turn_output(packet, output)
            packet["reading_record"]["protocol"] = "exactory-openness-reading-v6"
            with self.assertRaises(ResearchError) as caught:
                validate_turn_output(packet, output)
            self.assertEqual(caught.exception.code, "openness_finding_invalid")

    def test_saved_prompt_alone_selects_the_single_turn_finding_contract(self):
        from research_harness.review_protocol import canonical_prompt
        old = {"prompt": canonical_prompt("native_math", version=6)}
        new = {"prompt": canonical_prompt("native_math", version=7)}
        self.assertFalse(native_review.uses_finding_contract(old))
        self.assertTrue(native_review.uses_finding_contract(new))

    def test_ground_shapes_and_every_required_binding_are_checked(self):
        packet = build_packet("blocked")
        for ground in ({"kind": "unknown", "text": "Ground."},
                       {"kind": "own_argument", "text": ""}, {"kind": "source_settles", "text": "Source."},
                       {"kind": "own_argument", "text": "Argument.", "turns": ["reading-1", "reading-1"]},
                       {"kind": "own_argument", "text": "Argument.", "turns": [1]},
                       {"kind": "own_argument", "text": "Argument.", "unexpected": True}):
            with self.subTest(ground=ground), self.assertRaises(ResearchError) as caught:
                native_review.validate_finding(packet, build_answer(packet, "unresolved", [ground]))
            self.assertEqual(caught.exception.code, "openness_finding_invalid")
        for key in ("subject_digest", "reviewer"):
            answer = build_answer(packet, "unresolved")
            answer[key] = "different"
            with self.subTest(key=key), self.assertRaises(ResearchError) as caught:
                native_review.validate_finding(packet, answer)
            self.assertEqual(caught.exception.details["path"], "/" + key)


class FindingRecordingTests(OpennessReadingCase):
    reading_protocol = None

    def export_finding_packet(self, outcome="supported_open"):
        from research_harness.problem_openness import export_openness
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        searches = self.searches
        if outcome == "unresolved":
            searches = searches + [ProblemOpennessMinimumTests.search(
                self, gaps=["The neighbouring literature remains insufficiently covered."], query="A wider query")]
        result = export_openness(self.store, {"assignment_id": "turn-1", "reviewer_id": "openness-reviewer",
            "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": row["id"]}
                                     for row in searches], "duplicate_resolutions": [],
                        "settling_work_id": self.versions[0] if outcome == "already_solved" else None}},
            accept_gaps=outcome == "unresolved")
        return result["native_packet"]

    def record_answer(self, identifier):
        return self.mutate(self.load_problem_module().record_openness, {"assignment_id": identifier})["result"]

    def test_each_rule_row_reaches_record_and_composes_the_native_bindings(self):
        from research_harness import problem_openness
        from research_harness.review_continuation import plan_next
        for outcome in ("supported_open", "already_solved", "unresolved"):
            with self.subTest(outcome=outcome):
                # A separate export and assignment is sufficient; earlier findings stay immutable.
                reference = self.export_finding_packet(outcome)
                self.assign("turn-1", reference)
                packet = self.read_packet("turn-1")
                self.invoke("turn-1", build_answer(packet, outcome))
                records = self.store.snapshot()["records"]
                self.assertEqual(plan_next(records, self.artifacts, "turn-1")["state"], "record")
                saved = self.record_answer("turn-1")
                native = packet["native"]
                self.assertEqual(saved["outcome"], outcome)
                self.assertEqual(saved["statement_digest"], native["claim_digest"])
                self.assertEqual(saved["minimum"], native["scientific_context"]["openness_minimum"])
                self.assertEqual(saved["minimum_gaps"], native["scientific_context"]["minimum_gaps"])
                self.assertEqual(saved["searched_at"], native["scientific_context"]["audit_forecast"]["searched_through"])
                self.assertEqual(saved["evidence"], sorted(row["digest"] for row in native["evidence"]))
                if outcome == "unresolved":
                    obligation = problem_openness.assess_openness(self.store.snapshot()["records"], self.artifacts)[0]
                    self.assertEqual(obligation["code"], "problem_openness_unresolved")
                    self.assertTrue(obligation["gaps"])
                    self.assertEqual(obligation["grounds"], [])
                # Reinitialize the fixture so every case binds its assignment id exactly once.
                if outcome != "unresolved":
                    self.doCleanups()
                    self.setUp()

    def test_unresolved_ground_next_steps_are_shown_for_stored_findings(self):
        from research_harness.problem_openness import assess_openness
        reference = self.export_finding_packet()
        self.assign("turn-1", reference)
        ground = {"kind": "own_argument", "text": "A new argument needs independent checking."}
        self.invoke("turn-1", build_answer(self.read_packet("turn-1"), "unresolved", [ground]))
        saved = self.record_answer("turn-1")
        result = assess_openness(self.store.snapshot()["records"], self.artifacts)[0]
        self.assertEqual(result["grounds"], [dict(ground, next=native_review.GROUND_NEXT_STEPS["own_argument"])])
        self.assertEqual(saved["grounds"], [ground])
        # Earlier recorded unresolved findings have no reviewer grounds, but gain today's repair details.
        records = self.store.snapshot()["records"]
        records["problem_openness"][saved["id"]].pop("grounds")
        legacy = assess_openness(records, self.artifacts)[0]
        self.assertEqual(legacy["grounds"], [])
        self.assertIn("gaps", legacy)

    def test_single_turn_invalid_answer_gets_the_finding_refusal_and_safe_notice(self):
        from research_harness import review_refusal, review_refusal_notice
        reference = self.export_finding_packet()
        self.assign("turn-1", reference)
        output = build_answer(self.read_packet("turn-1"), "already_solved")
        self.invoke("turn-1", output)
        records = self.store.snapshot()["records"]
        refusal = records["review_output_refusal"]["attempt-turn-1"]
        self.assertEqual((refusal["code"], refusal["check"]), ("openness_finding_invalid", "openness.finding"))
        notice = review_refusal_notice.build_refusal_notice(records, self.artifacts, ["attempt-turn-1"])
        self.assertEqual(notice["refused"][0]["details"]["forecast"],
                         self.read_packet("turn-1")["native"]["scientific_context"]["audit_forecast"])
        self.assertEqual(review_refusal.classify_observed_output(records, self.artifacts, "attempt-turn-1")["check"],
                         "openness.finding")

    def test_two_bound_capture_timezones_compose_the_utc_search_date(self):
        from research_harness.problem_openness import export_openness
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        searches = [ProblemOpennessMinimumTests.search(self, self.versions, captured_at=captured, query="Dated query " + str(index))
                    for index, captured in enumerate(("2026-11-13T23:30:00-07:00", "2026-11-14T08:00:00+09:00"))]
        reference = export_openness(self.store, {"assignment_id": "turn-1", "reviewer_id": "openness-reviewer",
            "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": row["id"]}
                                     for row in self.searches + searches], "duplicate_resolutions": [],
                        "settling_work_id": None}})["native_packet"]
        self.assign("turn-1", reference)
        self.invoke("turn-1", build_answer(self.read_packet("turn-1")))
        self.assertEqual(self.record_answer("turn-1")["searched_at"], "2026-11-14")


class FindingChainRecordingTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (80000, 80000)
    export_finding_packet = FindingRecordingTests.export_finding_packet
    record_answer = FindingRecordingTests.record_answer

    def test_v6_final_records_an_unresolved_finding_over_accepted_gaps(self):
        from research_harness.review_continuation import plan_next
        self.limit_input(1300000)
        reference = self.export_finding_packet("unresolved")
        turns = self.run_reading_turns(reference, turn_count=30)
        packet = self.read_packet(turns[-1])
        self.assertEqual(packet["reading_record"]["protocol"], "exactory-openness-reading-v6")
        self.invoke(turns[-1], build_answer(packet, "unresolved"))
        with patch('research_harness.openness_minimum.audit_minimum', side_effect=AssertionError('Unresolved needs no audit.')):
            self.assertEqual(plan_next(self.store.snapshot()["records"], self.artifacts, turns[-1])["state"], "record")
            saved = self.record_answer(turns[-1])
        self.assertEqual(saved["outcome"], "unresolved")
        self.assertEqual([row["assignment_id"] for row in saved["reading_turns"]], turns)

    def assert_chain_records(self, outcome):
        from research_harness.openness_minimum import audit_minimum
        from research_harness.review_continuation import plan_next
        self.limit_input(1300000)
        reference = self.export_finding_packet(outcome)
        turns = self.run_reading_turns(reference, turn_count=30)
        final = turns[-1]
        packet = self.read_packet(final)
        self.invoke(final, build_answer(packet, outcome))
        with patch('research_harness.openness_minimum.audit_minimum', wraps=audit_minimum) as audit:
            self.assertEqual(plan_next(self.store.snapshot()["records"], self.artifacts, final)["state"], "record")
            saved = self.record_answer(final)
            self.assertGreater(audit.call_count, 0)
        self.assertEqual(saved["outcome"], outcome)
        self.assertEqual([row["assignment_id"] for row in saved["reading_turns"]], turns)

    def test_v6_final_records_supported_open_after_audit(self):
        self.assert_chain_records("supported_open")

    def test_v6_final_records_already_solved_after_audit(self):
        self.assert_chain_records("already_solved")


class FindingPassageBudgetTests(unittest.TestCase):
    def setUp(self):
        # Isolate retention order from transport framing; full journeys test the actual route representation.
        patcher = patch('research_harness.openness_reading.measure_record_entries',
                        side_effect=lambda route, entries: len(json.dumps(entries)) - 2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_budget_case(self, *, excerpts=(), requests=()):
        from research_harness import openness_reading as reading, review_excerpts
        record = {"protocol": reading.PROTOCOL_V6, "planned_reading_turns": 1,
                  "excerpt_protocol": review_excerpts.SESSION_PROTOCOL,
                  "turns": [{"assignment_id": "prior", "attempt_id": "read-attempt", "delivered_units": [], "stage": "reading"}],
                  "sources": [], "excerpts": [], "passages": [], "search_delivery": {"searches": []},
                  "allowances": {"excerpts": {"characters": 100000, "used": 0},
                                 "passages": {"characters": 100000, "used": 0},
                                 "record": {"characters": 100000, "used": 0, "not_retained": {}}}}
        packet = {"native": {}, "reading_record": record}
        output = {"stage": "reading", "notes": "Current notes", "excerpts": list(excerpts),
                  "passage_requests": list(requests)}
        turn = {"assignment_id": "final", "attempt_id": "final-attempt", "delivered_units": []}
        records = {"review_assignment": {"prior": {"route_id": "route"}}, "review_route": {"route": {}}}
        return records, packet, output, turn

    def test_final_passage_budget_retains_preceding_excerpts_and_request_diagnostics_first(self):
        from research_harness import openness_reading as reading, review_excerpts
        records, packet, output, turn = self.build_budget_case(excerpts=[{}], requests=[{}])
        excerpt = {"version_id": "work", "purpose": "A retained excerpt.", "status": "verified", "text": "e" * 40}
        passage = {"request": {"id": "request", "version_id": "work"}, "status": "delivered", "text": "p" * 40}

        def check(records, artifacts, excerpts, allowances, **kwargs):
            retained = kwargs.get("retain_entry")
            if retained is not None:
                retained(copy.deepcopy(excerpt), allowances)
            return [copy.deepcopy(excerpt)], allowances

        def answer(records, artifacts, requests, allowances, **kwargs):
            retained = kwargs.get("retain_entry")
            if retained is not None:
                retained(copy.deepcopy(passage), allowances)
            return [copy.deepcopy(passage)], allowances

        decorated = dict(turn, stage="final", notes_withheld=reading.NOTES_WITHHELD_NOTE.format(turn["assignment_id"]))
        trial = copy.deepcopy(packet["reading_record"])
        trial["turns"].append(decorated)
        trial["passages"].append(dict(reading._compact_entry(passage, []), assignment_id="final"))
        packet["reading_record"]["allowances"]["record"]["characters"] = reading.measure_record_charge({}, trial)
        with patch.object(reading, "list_reading_rows", return_value=[]), \
                patch.object(review_excerpts, "check_excerpts", side_effect=check), \
                patch.object(review_excerpts, "answer_passages", side_effect=answer):
            self.assertFalse(reading.has_deliverable_passage(records, None, packet, output, turn=turn))

    def test_actual_final_delivery_row_is_charged_before_accepting_a_passage(self):
        from research_harness import openness_reading as reading, review_excerpts
        records, packet, output, turn = self.build_budget_case(requests=[{}])
        turn["delivered_units"] = [{"kind": "record", "record_kind": "reading", "record_id": "r" * 1000}]
        passage = {"request": {"id": "request", "version_id": "work"}, "status": "delivered", "text": "p" * 40}
        # Enough room for a small passage, but not for this request's actual delivery receipt.
        packet["reading_record"]["allowances"]["record"]["characters"] = 500
        with patch.object(reading, "list_reading_rows", return_value=[]), \
                patch.object(review_excerpts, "answer_passages", return_value=([passage], packet["reading_record"]["allowances"])):
            self.assertFalse(reading.has_deliverable_passage(records, None, packet, output, turn=turn))

    def test_request_diagnostics_consume_record_room_before_later_delivered_passages(self):
        from research_harness import openness_reading as reading, review_excerpts
        body = "A bound source begins here. " + "s" * 2000
        reference = {"path": "artifacts/source", "sha256": "d" * 64, "size": len(body), "media_type": "text/plain"}
        row = {"kind": "fulltext", "version_id": "work", "text_sha256": reference["sha256"], "artifact": reference}
        requests = [{"id": "missing", "version_id": "work", "anchor": "An absent passage", "characters": 40},
                    {"id": "found", "version_id": "work", "anchor": "A bound source", "characters": 1000}]
        records, packet, output, turn = self.build_budget_case(requests=requests)
        artifacts = Mock()
        artifacts.read.return_value = body.encode()
        entries, _ = review_excerpts.answer_passages(records, artifacts, [requests[1]],
            packet["reading_record"]["allowances"], unit_sources=reading._collect_unit_sources([row]),
            protocol=review_excerpts.SESSION_PROTOCOL)
        trial = copy.deepcopy(packet["reading_record"])
        current = dict(turn, stage="final", notes_withheld=reading.NOTES_WITHHELD_NOTE.format(turn["assignment_id"]))
        trial["turns"].append(current)
        trial["passages"] = [dict(reading._compact_entry(entries[0], []), assignment_id=turn["assignment_id"])]
        packet["reading_record"]["allowances"]["record"]["characters"] = reading.measure_record_charge({}, trial)
        result = reading._retain_v6_output(records, artifacts, packet["reading_record"], output, [row], {},
                                           packet["reading_record"]["turns"] + [current])
        self.assertEqual([entry["status"] for entry in result["passages"]], ["not_found"])
        self.assertEqual(result["allowances"]["passages"]["used"], 0)
        self.assertEqual(result["allowances"]["record"]["not_retained"], {"delivered_passage": 1})

    def test_final_rows_are_charged_even_when_grouping_used_fewer_reading_turns(self):
        from research_harness import openness_reading as reading
        _, packet, _, turn = self.build_budget_case()
        record = packet["reading_record"]
        record["planned_reading_turns"] = 100
        record["turns"].append(dict(turn, stage="final", notes_withheld="Withheld."))
        self.assertGreater(reading.measure_record_charge({}, record), 0)


    def test_rejected_excerpt_does_not_hide_a_later_copy_that_fits(self):
        from research_harness import openness_reading as reading, review_excerpts
        body = 'A bound source states a precise finite theorem. ' + 'p' * 100
        reference = {'path': 'artifacts/source', 'sha256': 'd' * 64, 'size': len(body), 'media_type': 'text/plain'}
        row = {'kind': 'fulltext', 'version_id': 'work', 'text_sha256': reference['sha256'], 'artifact': reference}
        small = dict(version_id='work', text=body[:40], purpose='Keep.')
        records, packet, output, turn = self.build_budget_case(excerpts=[dict(small, purpose='p' * 500), small])
        artifacts = Mock()
        artifacts.read.return_value = body.encode()
        entries, _ = review_excerpts.check_excerpts(records, artifacts, [small],
            packet['reading_record']['allowances'], unit_sources=reading._collect_unit_sources([row]),
            protocol=review_excerpts.SESSION_PROTOCOL)
        trial = copy.deepcopy(packet['reading_record'])
        current = dict(turn, stage='final', notes_withheld=reading.NOTES_WITHHELD_NOTE.format(turn['assignment_id']))
        trial['turns'].append(current)
        trial['excerpts'].append(reading._compact_entry(entries[0], []))
        packet['reading_record']['allowances']['record']['characters'] = reading.measure_record_charge({}, trial)
        retained = reading._retain_v6_output(records, artifacts, packet['reading_record'], output, [row], {},
                                             trial['turns'])
        self.assertEqual([entry['purpose'] for entry in retained['excerpts']], ['Keep.'])
        self.assertEqual(retained['allowances']['record']['not_retained'], {'verified_excerpt': 1})
        self.assertEqual(retained['allowances']['excerpts']['used'], 40)
        self.assertEqual(retained['allowances']['record']['used'], reading.measure_record_charge({}, retained))


if __name__ == "__main__":
    unittest.main()
