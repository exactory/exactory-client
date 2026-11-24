"""After a slate that selects nothing, the harness names the next search step until a problem is posted."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

from research_harness import cli, output_views, problem_claims, problem_reports
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.report_views import next_summary, status_summary
from research_harness.storage import Store
from test_research_problem_candidates import build_candidate
from test_research_problem_claims import ProblemCase
from test_research_problem_forming import build_form, build_forming, build_outcome

VIEWS_0921_PATH = Path(__file__).parent / "fixtures" / "problem_search_views_0921.json"
DIGEST_PLACEHOLDER = "DEPENDENCY_DIGEST"

SEARCH_CODES = ("problem_search_step_required", "problem_search_step_open", "problem_search_continued")
NO_POST_EXPLANATION_0921 = "The latest slate selects no candidate; a new slate that selects one reopens the post path."
SLATE_REASON = "No candidate has a place that counts; the reviews refuse every candidate."


def put_record(store, kind, key, value):
    return store.mutate("test.record", {"kind": kind, "key": key, "value": value},
                        lambda tx: tx.put(kind, key, value), expected_revision=store.revision,
                        request_id="record-" + kind + "-" + key + "-" + str(store.revision))


def build_slate_record(identifier, revision, *, candidate_ids, selected=(), deferred=(), rejected=(), review_ids=()):
    """A slate in the form that problem-slate stores."""
    return dict(id=identifier, selected=list(selected),
                deferred=[dict(candidate_id=value, evidence_needed="A work that states " + value + " as open.")
                          for value in deferred],
                rejected=[dict(candidate_id=value, reason="The reviews find " + value + " known.") for value in rejected],
                bar_review_ids=["review-bar-first", "review-bar-second"], review_ids=list(review_ids),
                comparison="The candidates were compared with the nearest problems of both bars.",
                reason="" if selected else SLATE_REASON, candidate_ids=sorted(candidate_ids), recorded_revision=revision)


def build_slate_review(identifier, revision, *, bound=True, identities=None):
    """A slate review with two objections; the second names no candidate. Without bound, the objections are in the
    form that a slate review stored before candidates were bound to objections. identities is the stored map of
    objection_identities, which a review holds when another reviewer of the record stated the same id."""
    objections = [dict(id="objection-restated", claim="The deferred version restates a printed question.",
                       reason="The survey states it.", evidence=[],
                       resolution_condition="A work that needs the statement.", candidate_ids=["candidate-a-3"]),
                  dict(id="objection-missing", claim="The slate omits a printed open statement of the field.",
                       reason="The survey states it as open.", evidence=[],
                       resolution_condition="A forming record that states it as a candidate or sets it aside.",
                       candidate_ids=[])]
    if not bound:
        for objection in objections:
            del objection["candidate_ids"]
    review = dict(id=identifier, assignment_id="assignment-" + identifier, reviewer_id="reviewer-b",
                  payload=dict(stage="slate", objections=objections), recorded_revision=revision)
    if identities is not None:
        review["objection_identities"] = identities
    return review


def build_step(identifier, way, *, slate_id="slate-1", candidate_ids=(), evidence=(), passed_over=(), successor=None,
               leads=()):
    return dict(id=identifier, slate_id=slate_id, way=way, candidate_ids=list(candidate_ids),
                learned="The slate reviews refused every candidate for a stated reason.", evidence=list(evidence),
                plan="Do the work of the way " + way + " and record its product.", passed_over=list(passed_over),
                successor_workspace=None if successor is None else str(successor), other_leads=list(leads))


class SearchAfterRefusalTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()

    def record_step(self, payload, store=None):
        store = self.store if store is None else store
        self.sequence += 1
        return cli.OPERATIONS["problem-search-step"](store, payload, expected_revision=store.revision,
                                                      request_id="step-" + str(self.sequence))["result"]

    def assert_refused(self, code, function, details=None):
        revision = self.store.revision
        with self.assertRaises(ResearchError) as caught:
            function()
        self.assertEqual(caught.exception.code, code, caught.exception.as_dict())
        if details is not None:
            self.assertEqual(caught.exception.details, details)
        self.assertEqual(self.store.revision, revision)
        return caught.exception

    def put_candidate(self, identifier, previous=None):
        self.put("problem_candidate", identifier, dict(build_candidate(identifier, previous=previous),
                                                       recorded_revision=self.store.revision + 1))

    def put_slate(self, identifier, **members):
        self.put("problem_slate", identifier, build_slate_record(identifier, self.store.revision + 1, **members))

    def build_incident(self, *, bound=True, identities=None):
        """The shape of the run: a candidate with two later versions and three others, one forming call with a refuted
        form, one slate review with two objections, a slate with two deferred and two rejected candidates that cites
        the review, and a report."""
        self.put_candidate("candidate-a")
        self.put_candidate("candidate-a-2", previous="candidate-a")
        self.put_candidate("candidate-a-3", previous="candidate-a-2")
        for identifier in ("candidate-b", "candidate-c", "candidate-d"):
            self.put_candidate(identifier)
        self.put("forming_call", "forming-1", dict(build_forming(forms=[build_form(), build_form("form-2")]),
                                                    recorded_revision=self.store.revision + 1))
        self.put("form_outcome", "outcome-1", dict(build_outcome(standing="refuted", disposition=dict(
            status="set_aside", reason="A computation refutes it.", form_id=None, candidate_id=None)),
            recorded_revision=self.store.revision + 1))
        self.put("form_outcome", "outcome-2", dict(build_outcome("outcome-2", form_id="form-2", disposition=dict(
            status="set_aside", reason="The available means did not decide it.", form_id=None, candidate_id=None)),
            recorded_revision=self.store.revision + 1))
        self.put("value_review", "review-slate", build_slate_review("review-slate", self.store.revision + 1,
                                                                    bound=bound, identities=identities))
        self.put_slate("slate-1", candidate_ids=["candidate-a-3", "candidate-b", "candidate-c", "candidate-d"],
                       deferred=["candidate-a-3", "candidate-b"], rejected=["candidate-c", "candidate-d"],
                       review_ids=["review-slate"])
        self.mutate(cli.OPERATIONS["problem-report"], dict(id="report-1", reopen_when="A work that needs a candidate."))

    def put_work(self, identifier):
        """A record of the work of a step: a form outcome recorded after it. Returns its reference."""
        self.put("form_outcome", identifier, dict(build_outcome(identifier, form_id="form-2", disposition=dict(
            status="set_aside", reason="The work of the step did not decide it.", form_id=None, candidate_id=None)),
            recorded_revision=self.store.revision + 1))
        return self.cite("form_outcome", identifier)

    def cite(self, kind, identifier, store=None):
        records = (self.store if store is None else store).snapshot()["records"]
        return dict(kind="record", record_kind=kind, id=identifier, digest=digest(records[kind][identifier]))

    def read_next(self):
        return cli.status_report(self.store)["next"]

    def test_next_names_the_first_way_after_a_slate_that_selects_nothing(self):
        self.build_incident()
        report = cli.status_report(self.store)
        upcoming = report["next"]
        slate = self.store.snapshot()["records"]["problem_slate"]["slate-1"]
        self.assertEqual({key: upcoming[key] for key in ("code", "slate_id", "reason", "deferred", "rejected")},
                         dict(code="problem_search_step_required", slate_id="slate-1", reason=SLATE_REASON,
                              deferred=slate["deferred"], rejected=slate["rejected"]))
        self.assertEqual([(row["review_id"], row["id"], row["candidate_ids"], row["resolution_condition"])
                          for row in upcoming["objections"]],
                         [("review-slate", "objection-restated", ["candidate-a-3"], "A work that needs the statement."),
                          ("review-slate", "objection-missing", [],
                           "A forming record that states it as a candidate or sets it aside.")])
        self.assertEqual(upcoming["ways"], [
            dict(way="raise_deferred", applicable=True, step_ids=[], candidates=slate["deferred"]),
            dict(way="pose_stated_item", applicable=True, step_ids=[]),
            dict(way="study_refutation", applicable=True, step_ids=[], refuted_forms=1),
            dict(way="attack_stated_question", applicable=True, step_ids=[]),
            dict(way="next_exploration", applicable=True, step_ids=[], leads=[])])
        step = dict(command="problem-search-step", payload=dict(
            slate_id="slate-1", way="raise_deferred", candidate_ids=["candidate-a-3", "candidate-b"], passed_over=[],
            successor_workspace=None, other_leads=[]), chooses=["id", "learned", "evidence", "plan"])
        self.assertEqual(upcoming["next"], [step])
        self.assertEqual(next_summary(report)["next_hint"]["next_step"], step)
        self.assertEqual(next_summary(report)["next_hint"]["code"], "problem_search_step_required")

    def test_each_named_step_is_accepted_until_the_search_leaves_the_record(self):
        self.build_incident()
        successor = self.root.parent / "next-exploration"
        successor.mkdir()
        codes, ways = [], []
        for index in range(8):
            upcoming = self.read_next()
            codes.append(upcoming["code"])
            if upcoming["code"] == "problem_search_continued":
                break
            step = (upcoming["next"] if upcoming["code"] == "problem_search_step_required" else upcoming["then"])[0]
            self.assertEqual(step["command"], "problem-search-step")
            chosen = dict(id="step-" + str(index), learned="What the earlier work found.",
                          plan="The work of this step.",
                          evidence=[self.put_work("work-" + str(index))] if index else [])
            if "successor_workspace" in step["chooses"]:
                chosen.update(successor_workspace=str(successor),
                              other_leads=[dict(field="A neighbouring field question.", reason="Noted in a reading.")])
            self.assertEqual(set(chosen), set(step["chooses"]))
            ways.append(self.record_step(dict(step["payload"], **chosen))["way"])
        self.assertEqual(codes, ["problem_search_step_required"] + ["problem_search_step_open"] * 4
                         + ["problem_search_continued"])
        self.assertEqual(ways, ["raise_deferred", "pose_stated_item", "study_refutation", "attack_stated_question",
                                "next_exploration"])
        self.assertEqual((upcoming["step_id"], upcoming["successor_workspace"]), ("step-4", str(successor.resolve())))

    def test_a_later_way_gives_a_reason_for_each_way_passed_over(self):
        self.build_incident()
        step = build_step("step-1", "pose_stated_item")
        self.assert_refused("problem_search_way_passed_over", lambda: self.record_step(step),
                            dict(ways=["raise_deferred"]))
        reason = dict(way="raise_deferred", reason="Three searches found no work that states the deferred candidates.")
        self.assertEqual(self.record_step(dict(step, passed_over=[reason]))["passed_over"], [reason])
        self.assertEqual(self.read_next()["then"][0]["payload"]["way"], "raise_deferred")
        # A slate that defers nothing: raise_deferred is not applicable and needs no reason.
        self.put_slate("slate-2", candidate_ids=["candidate-a-3", "candidate-b", "candidate-c", "candidate-d"],
                       rejected=["candidate-a-3", "candidate-b", "candidate-c", "candidate-d"])
        step = build_step("step-2", "attack_stated_question", slate_id="slate-2")
        self.assert_refused("problem_search_way_passed_over", lambda: self.record_step(step),
                            dict(ways=["pose_stated_item", "study_refutation"]))
        passed = [dict(way=way, reason="The reason of the record.") for way in ("pose_stated_item", "study_refutation")]
        self.assertEqual(self.record_step(dict(step, passed_over=passed))["way"], "attack_stated_question")
        for entries in ([dict(way="next_exploration", reason="A later way.")], passed + passed[:1]):
            with self.subTest(passed_over=entries):
                self.assert_refused("invalid_input", lambda: self.record_step(dict(
                    step, id="step-3", passed_over=entries, evidence=[self.cite("form_outcome", "outcome-1")])))

    def test_a_way_applies_only_on_its_ground(self):
        self.put_candidate("candidate-1")
        self.put_slate("slate-1", candidate_ids=["candidate-1"], rejected=["candidate-1"])
        for way in ("raise_deferred", "study_refutation"):
            with self.subTest(way=way):
                self.assert_refused("problem_search_way_inapplicable", lambda: self.record_step(build_step("step", way)),
                                    dict(way=way))
        upcoming = self.read_next()
        self.assertEqual([row["applicable"] for row in upcoming["ways"]], [False, True, False, True, True])
        self.assertEqual(upcoming["next"][0]["payload"]["way"], "pose_stated_item")
        self.assertEqual(self.record_step(dict(upcoming["next"][0]["payload"], id="step", learned="A finding.",
                                               plan="A plan.", evidence=[]))["way"], "pose_stated_item")

    def test_a_step_follows_the_latest_slate_that_selects_nothing(self):
        self.put_candidate("candidate-1")
        self.assert_refused("problem_search_slate_mismatch", lambda: self.record_step(build_step("step", "pose_stated_item")),
                            dict(slate_id=None, selected=[]))
        self.put_slate("slate-1", candidate_ids=["candidate-1"], selected=["candidate-1"])
        self.assert_refused("problem_search_slate_mismatch", lambda: self.record_step(build_step("step", "pose_stated_item")),
                            dict(slate_id="slate-1", selected=["candidate-1"]))
        self.put_slate("slate-2", candidate_ids=["candidate-1"], rejected=["candidate-1"])
        self.assert_refused("problem_search_slate_mismatch", lambda: self.record_step(build_step("step", "pose_stated_item")),
                            dict(slate_id="slate-2", selected=[]))
        self.put_candidate("candidate-2")
        self.assertEqual(self.read_next()["code"], "problem_slate_stale")
        saved = self.record_step(build_step("step", "pose_stated_item", slate_id="slate-2"))
        self.assertEqual((saved["slate_id"], saved["recorded_revision"]), ("slate-2", self.store.revision))

    def test_candidates_and_evidence_of_a_step(self):
        self.build_incident()
        self.assert_refused("problem_search_candidate_mismatch", lambda: self.record_step(build_step(
            "step-1", "raise_deferred", candidate_ids=["candidate-b", "candidate-c"])), dict(candidate_ids=["candidate-c"]))
        self.assert_refused("problem_search_candidate_mismatch", lambda: self.record_step(build_step(
            "step-1", "raise_deferred")), dict(candidate_ids=[]))
        self.record_step(build_step("step-1", "raise_deferred", candidate_ids=["candidate-b"]))
        later = build_step("step-2", "pose_stated_item")
        self.assert_refused("problem_search_evidence_missing", lambda: self.record_step(later), dict(step_id="step-1"))
        stale = dict(self.cite("form_outcome", "outcome-1"), digest="0" * 64)
        self.assert_refused("strategy_evidence_stale", lambda: self.record_step(dict(later, evidence=[stale])))
        # The slate review and outcome-1 were recorded before step-1: they are no record of the work of that step.
        for reference in (self.cite("value_review", "review-slate"), self.cite("form_outcome", "outcome-1")):
            with self.subTest(cited=reference["id"]):
                self.assert_refused("problem_search_evidence_missing", lambda: self.record_step(dict(
                    later, evidence=[reference])), dict(step_id="step-1"))
        cited = dict(later, evidence=[self.cite("value_review", "review-slate"), self.put_work("work-1")])
        leaving = build_step("step-2", "next_exploration", evidence=cited["evidence"], successor=self.root.parent,
                             passed_over=[dict(way=way, reason="A reason.") for way in
                                          ("pose_stated_item", "study_refutation", "attack_stated_question")])
        for case in (dict(cited, learned="x" * 4001), dict(cited, plan="x" * 4001),
                     dict(cited, successor_workspace=str(self.root.parent)), dict(cited, other_leads=[dict(
                         field="A field.", reason="A reason.")]),
                     dict(leaving, other_leads=[dict(field="A field.", reason="A reason.")] * 17),
                     dict(leaving, other_leads=[dict(field="x" * 1001, reason="A reason.")]),
                     dict(leaving, successor_workspace="relative/path"),
                     dict(leaving, successor_workspace=str(self.root.parent / "missing")),
                     dict(leaving, successor_workspace=None), dict(cited, extra=1)):
            with self.subTest(case={key: str(value)[:40] for key, value in case.items()}):
                self.assert_refused("invalid_input", lambda: self.record_step(case))
        self.assert_refused("problem_search_successor_self", lambda: self.record_step(dict(
            leaving, successor_workspace=str(self.root / "."))))
        self.assertEqual(self.record_step(dict(cited, learned="x" * 4000))["id"], "step-2")
        repeated = dict(cited, evidence=[self.put_work("work-2")])
        self.assert_refused("record_conflict", lambda: self.record_step(repeated))

    def test_a_new_slate_ends_a_step_and_names_what_it_produced(self):
        self.put_candidate("candidate-1")
        self.put_slate("slate-1", candidate_ids=["candidate-1"], rejected=["candidate-1"])
        step = self.record_step(build_step("step-1", "pose_stated_item"))
        self.put_candidate("candidate-2")
        self.put_slate("slate-2", candidate_ids=["candidate-1", "candidate-2"], selected=["candidate-2"],
                       rejected=["candidate-1"])
        report = cli.status_report(self.store)
        self.assertNotIn(report["next"]["code"], SEARCH_CODES + ("problem_no_post",))
        self.assertEqual(report["search"]["steps"], [dict(step, ended_by=dict(kind="slate", id="slate-2"),
                                                          produced_candidate_ids=["candidate-2"])])
        self.assertEqual(status_summary(report)["search"], dict(steps=1, latest_way="pose_stated_item", has_origin=False))
        self.put_slate("slate-3", candidate_ids=["candidate-1", "candidate-2"], rejected=["candidate-1", "candidate-2"])
        upcoming = self.read_next()
        self.assertEqual(upcoming["code"], "problem_search_step_required")
        self.assertEqual([row["step_ids"] for row in upcoming["ways"]], [[]] * 5)
        self.put_candidate("candidate-3")
        self.put_slate("slate-4", candidate_ids=["candidate-1", "candidate-2", "candidate-3"],
                       rejected=["candidate-1", "candidate-2", "candidate-3"])
        search = cli.status_report(self.store)["search"]
        self.assertEqual((search["slates_without_step"], search["origin"]), (["slate-3"], None))

    def test_a_record_with_a_post_keeps_problem_no_post(self):
        self.put_candidate("candidate-1")
        self.put_slate("slate-0", candidate_ids=["candidate-1"], rejected=["candidate-1"])
        self.assertEqual(self.read_next()["code"], "problem_search_step_required")
        self.put("problem_post", "post", dict(id="post", statement_id="statement-0", statement_digest="0" * 64,
                                              problem_evidence_digest=None, posted_at="2026-11-10T12:00:00Z",
                                              recorded_revision=self.store.revision + 1))
        self.put_slate("slate-1", candidate_ids=["candidate-1"], rejected=["candidate-1"])
        self.assertEqual(self.read_next(), {"code": "problem_no_post", "reason": SLATE_REASON,
                                            "explanation": NO_POST_EXPLANATION_0921})
        self.assertTrue(problem_reports.is_report_due(dict(self.store.snapshot()["records"],
                                                           forming_call=dict(call=dict(id="call", forms=[])))))

    def initialize_other(self, name):
        store = Store(self.root.parent / name, create=True)
        self.sequence += 1
        problem_claims.initialize_problem(store, {"slug": name, "area": "math.CO", "field": "The field of " + name,
                                                  "authors": ["problem-author"]}, expected_revision=0,
                                          request_id="initialize-" + name)
        return store

    def record_origin(self, store, payload):
        self.sequence += 1
        return cli.OPERATIONS["problem-search-origin"](store, payload, expected_revision=store.revision,
                                                       request_id="origin-" + str(self.sequence))["result"]

    def test_the_next_record_keeps_what_the_exploration_learned(self):
        self.build_incident()
        second, third = self.initialize_other("second"), self.initialize_other("third")
        payload = dict(id="origin-1", source_workspace=str(self.root), source_revision=self.store.revision)
        with self.assertRaises(ResearchError) as caught:
            self.record_origin(second, payload)
        self.assertEqual((caught.exception.code, caught.exception.details), ("problem_search_origin_unlinked", dict(
            source_workspace=str(self.root.resolve()), successor_workspace=None)))
        lead = dict(field="Window sums of other weights.", reason="A reading noted it at the edge of the field.")
        passed = [dict(way=way, reason="The searches of the record found nothing.") for way in
                  ("raise_deferred", "pose_stated_item", "study_refutation", "attack_stated_question")]
        self.record_step(build_step("leave", "next_exploration", successor=second.root, passed_over=passed, leads=[lead]))
        payload["source_revision"] = self.store.revision
        for changes, code in ((dict(source_workspace="relative"), "invalid_input"),
                              (dict(source_revision=-1), "invalid_input"),
                              (dict(source_revision=True), "invalid_input"),
                              (dict(source_workspace=str(second.root)), "problem_search_origin_self"),
                              (dict(source_workspace=str(self.root.parent / "missing")), "problem_search_source_unavailable"),
                              (dict(source_workspace=str(self.root.parent)), "problem_search_source_unavailable"),
                              (dict(source_revision=1), "problem_search_source_revision_mismatch"),
                              (dict(source_workspace=str(third.root), source_revision=third.revision),
                               "problem_search_origin_unlinked")):
            with self.subTest(code=code, changes=changes):
                with self.assertRaises(ResearchError) as caught:
                    self.record_origin(second, dict(payload, **changes))
                self.assertEqual(caught.exception.code, code)
        origin = self.record_origin(second, payload)
        records = self.store.snapshot()["records"]
        source = origin["source"]
        self.assertEqual((origin["source_workspace"], origin["earlier"], origin["earlier_omitted"]),
                         (str(self.root.resolve()), [], 0))
        self.assertEqual((source["area"], source["field"]), ("math.CO", "Extremal sequences and finite bounds"))
        self.assertEqual(source["slate"], {key: records["problem_slate"]["slate-1"][key]
                                           for key in ("id", "reason", "comparison", "review_ids")})
        self.assertEqual([(row["id"], row["disposition"], "evidence_needed" in row) for row in source["candidates"]],
                         [("candidate-a-3", "deferred", True), ("candidate-b", "deferred", True),
                          ("candidate-c", "rejected", False), ("candidate-d", "rejected", False)])
        self.assertEqual([row["id"] for row in source["objections"]], ["objection-restated", "objection-missing"])
        self.assertEqual(source["forms"], [dict(id="form-1", text=build_form()["text"], standing="refuted")])
        self.assertEqual([row["way"] for row in source["steps"]], ["next_exploration"])
        self.assertEqual((source["other_leads"], source["report"]), ([lead], dict(
            id="report-1", outcome="Statements were formed and none was posted",
            reopen_when="A work that needs a candidate.")))
        self.assertEqual(source["omitted"], dict(candidates=0, objections=0, forms=0, steps=0))
        self.assertEqual(cli.status_report(second)["search"]["origin"], origin)
        with self.assertRaises(ResearchError) as caught:
            self.record_origin(second, payload)
        self.assertEqual(caught.exception.code, "record_conflict")
        # The second exploration names the third, which keeps one row for the first.
        put_record(second, "problem_candidate", "candidate-1", dict(build_candidate(), recorded_revision=second.revision + 1))
        put_record(second, "problem_slate", "slate-1", build_slate_record(
            "slate-1", second.revision + 1, candidate_ids=["candidate-1"], rejected=["candidate-1"]))
        self.record_step(build_step("leave", "next_exploration", successor=third.root, passed_over=passed[1:2] + [
            dict(way="attack_stated_question", reason="No stated question.")], leads=[lead]), store=second)
        origin = self.record_origin(third, dict(id="origin-1", source_workspace=str(second.root),
                                                source_revision=second.revision))
        self.assertEqual(origin["earlier"], [dict(workspace=str(self.root.resolve()), revision=payload["source_revision"],
                                                  area="math.CO", field="Extremal sequences and finite bounds",
                                                  ways=["next_exploration"], other_leads=[lead],
                                                  report=source["report"])])
        self.assertEqual(origin["source"]["field"], "The field of second")

    def test_the_report_follows_the_end_of_the_search(self):
        self.put("forming_call", "forming-1", dict(build_forming(), recorded_revision=self.store.revision + 1))
        self.put("form_outcome", "outcome-1", dict(build_outcome(disposition=dict(
            status="set_aside", reason="No further work.", form_id=None, candidate_id=None)),
            recorded_revision=self.store.revision + 1))
        self.put_candidate("candidate-1")
        self.put_slate("slate-1", candidate_ids=["candidate-1"], rejected=["candidate-1"])
        records = self.store.snapshot()["records"]
        self.assertFalse(problem_reports.is_report_due(records))
        self.assertEqual(problem_reports.describe_report_advisories(records), [])
        posted = copy.deepcopy(records)
        posted["problem_post"] = dict(post=dict(id="post", recorded_revision=self.store.revision + 1))
        self.assertTrue(problem_reports.is_report_due(posted))
        self.record_step(build_step("step-1", "pose_stated_item"))
        self.assertFalse(problem_reports.is_report_due(self.store.snapshot()["records"]))
        self.record_step(build_step("step-2", "next_exploration", successor=self.root.parent,
                                    evidence=[self.put_work("work-1")],
                                    passed_over=[dict(way="attack_stated_question", reason="No stated question.")]))
        records = self.store.snapshot()["records"]
        self.assertTrue(problem_reports.is_report_due(records))
        self.assertEqual([row["code"] for row in problem_reports.describe_report_advisories(records)],
                         ["problem_report_due"])
        report = self.mutate(cli.OPERATIONS["problem-report"], dict(id="report-1", reopen_when="A new work."))["result"]
        self.assertEqual([row["id"] for row in report["search"]["steps"]], ["step-1", "step-2"])
        self.assertEqual(problem_reports.describe_report_advisories(self.store.snapshot()["records"]), [])
        self.record_step(build_step("step-3", "pose_stated_item", evidence=[self.put_work("work-2")]))
        self.assertEqual([row["code"] for row in problem_reports.describe_report_advisories(
            self.store.snapshot()["records"])], ["problem_report_stale"])

    def test_stored_forms_of_release_0_92_keep_their_digests_and_views(self):
        """The views of the stored kinds equal the views that release 0.92.1 rendered for the same records
        (fixtures/problem_search_views_0921.json, made with de0a7e89; the dependency digest of the report holds the
        creation time of the marker, so both sides hold a placeholder in its place)."""
        identities = {"objection-missing": "objection-missing@review-slate"}
        self.build_incident(bound=False, identities=identities)
        records = self.store.snapshot()["records"]
        report = records["problem_report"]["report-1"]
        stored_digest = digest({kind: dict(records.get(kind, {})) for kind in problem_reports.DEPENDENCY_KINDS})
        self.assertEqual(report["dependency_digest"], stored_digest)
        self.assertNotIn("search", report)
        self.assertEqual(problem_reports.describe_report_advisories(records), [])
        upcoming = self.read_next()
        self.assertEqual([(row["id"], row.get("stated_id"), row["candidate_ids"]) for row in upcoming["objections"]],
                         [("objection-restated", None, []),
                          ("objection-missing@review-slate", "objection-missing", [])])
        self.assertNotIn("search", cli.status_report(self.store))
        views = output_views._build_records(records, self.artifacts, output_views._ViewInputs({}, {}))
        released = json.loads(VIEWS_0921_PATH.read_text())
        self.assertEqual({path: dict(content=view.content.decode().replace(report["dependency_digest"],
                                                                          DIGEST_PLACEHOLDER), entry=view.entry)
                          for path, view in views.items()}, released)

    def test_next_exploration_names_the_leads_of_the_origin(self):
        lead = dict(field="Window sums of other weights.", reason="An earlier exploration noted it.")
        self.put("problem_search_origin", "origin-1", dict(
            id="origin-1", source_workspace="/earlier/record", source_revision=7, earlier=[], earlier_omitted=0,
            source=dict(area="math.CO", field="A field.", steps=[], other_leads=[lead], report=None),
            recorded_revision=self.store.revision + 1))
        self.put_candidate("candidate-1")
        self.put_slate("slate-1", candidate_ids=["candidate-1"], rejected=["candidate-1"])
        self.assertEqual(self.read_next()["ways"][-1], dict(way="next_exploration", applicable=True, step_ids=[],
                                                            leads=[lead]))

    def test_both_commands_need_a_problem_record(self):
        plain = Store(self.root.parent / "plain", create=True)
        for command, payload in (("problem-search-step", build_step("step", "pose_stated_item")),
                                 ("problem-search-origin", dict(id="origin", source_workspace=str(self.root),
                                                                source_revision=self.store.revision))):
            with self.subTest(command=command):
                with self.assertRaises(ResearchError) as caught:
                    cli.OPERATIONS[command](plain, payload, expected_revision=0, request_id=command)
                self.assertEqual(caught.exception.code, "orchestration_conflict")

    def test_an_origin_keeps_the_latest_rows_of_each_list(self):
        """More than 32 candidates and earlier explorations: the cut keeps the latest rows by recorded revision."""
        source = self.initialize_other("source")
        identifiers = ["candidate-%02d" % index for index in range(35)]
        for identifier in identifiers:
            put_record(source, "problem_candidate", identifier,
                       dict(build_candidate(identifier), recorded_revision=source.revision + 1))
        earlier = [dict(workspace="/earlier/" + str(index), revision=index, area="math.CO", field="A field.",
                        ways=["next_exploration"], other_leads=[], report=None) for index in range(32)]
        put_record(source, "problem_search_origin", "origin-0", dict(
            id="origin-0", source_workspace="/earlier/32", source_revision=40, earlier=earlier, earlier_omitted=5,
            source=dict(area="math.CO", field="The last field.", steps=[], other_leads=[], report=None),
            recorded_revision=source.revision + 1))
        # The two candidates recorded last are deferred; the slate lists the deferred candidates first.
        put_record(source, "problem_slate", "slate-1", build_slate_record(
            "slate-1", source.revision + 1, candidate_ids=identifiers, deferred=identifiers[33:],
            rejected=identifiers[:33]))
        self.record_step(build_step("leave", "next_exploration", successor=self.root, passed_over=[
            dict(way=way, reason="A reason.") for way in ("raise_deferred", "pose_stated_item", "attack_stated_question")]),
            store=source)
        origin = self.record_origin(self.store, dict(id="origin-1", source_workspace=str(source.root),
                                                     source_revision=source.revision))
        self.assertEqual([row["id"] for row in origin["source"]["candidates"]], identifiers[3:])
        self.assertEqual(origin["source"]["omitted"]["candidates"], 3)
        self.assertEqual([row["workspace"] for row in origin["earlier"]],
                         ["/earlier/" + str(index) for index in range(1, 33)])
        self.assertEqual(origin["earlier_omitted"], 6)

    def test_search_records_have_their_views(self):
        self.put_candidate("candidate-1")
        self.put_slate("slate-1", candidate_ids=["candidate-1"], rejected=["candidate-1"])
        step = self.record_step(build_step("step-1", "pose_stated_item"))
        origins = []
        for identifier in ("origin-1", "origin-2"):
            origin = dict(id=identifier, source_workspace="/earlier/" + identifier, source_revision=7,
                          source=dict(area="math.CO", field="A field.", notes="x" * (output_views.COPY_LIMIT_BYTES + 1)),
                          earlier=[], earlier_omitted=0, recorded_revision=self.store.revision + 1)
            self.put("problem_search_origin", identifier, origin)
            origins.append(origin)
        model = output_views._build_records(self.store.snapshot()["records"], self.artifacts,
                                            output_views._ViewInputs({}, {}))
        placed = {path: view.entry["status"] for path, view in model.items() if "search-" in path}
        self.assertEqual(placed, {"final/problem/search-steps/step-1.json": "current",
                                  "final/problem/search-origins/origin-2.json": "current",
                                  "final/history/problem/search-origins/origin-1.json": "superseded"})
        self.assertEqual(json.loads(model["final/problem/search-steps/step-1.json"].content), step)
        self.assertEqual(json.loads(model["final/problem/search-origins/origin-2.json"].content), origins[1])
        self.assertEqual(json.loads(model["final/history/problem/search-origins/origin-1.json"].content), origins[0])
