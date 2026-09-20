"""A deferred source remains unread, reviewable, and unusable as claim evidence."""

import copy
import json

from development_fixtures import DevelopmentCase
from literature_fixtures import LiteratureCase
from research_harness.cli import OPERATIONS, status_report
from research_harness.literature import foundation_report, import_bundle
from research_harness.reading import record_reading, require_fulltext
from research_harness.report_views import status_summary


class DeferralCase:
    def get_operation(self, name):
        self.assertIn(name, OPERATIONS, "Source deferral must be a supported native mutation")
        return OPERATIONS[name]

    def require_source(self, version, identifier="required", purpose="validity"):
        value = {"id": identifier, "profile": "research", "version_id": version,
                 "purpose": purpose, "reason": "The derivation is a dependent scientific claim."}
        if purpose in ("classic", "core", "lineage"):
            value["depends_on"] = "historical-derivation"
        self.mutate(require_fulltext, value)

    def build_deferral(self, version, identifier="defer-1"):
        return {"id": identifier, "profile": "research", "version_id": version,
                "reason": "Required original material remains unavailable.",
                "authorization": self.artifacts.put(b"PRIVATE: Explicit user instruction to continue with a source gap.\n", "text/plain"),
                "acquisition_evidence": [self.artifacts.put(b"Authored retrieval log: the required supplement was not located. No HTTP status is asserted.\n", "text/plain")],
                "dependent_claims": ["The unavailable derivation cannot support the scientific claim."],
                "continuing_work": ["Develop the complete objective using independently read alternative evidence."]}

    def defer(self, version, identifier="defer-1"):
        return self.mutate(self.get_operation("defer-source"), self.build_deferral(version, identifier))

    def add_missing_supplement(self, version, capture=None):
        bundle = self.bundle(version, capture, bundle_id="with-gap-" + str(self.sequence))
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": None,
                                "reason": "The article requires an unavailable derivation.", "url": "https://example.org/supplement"})
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "partial-" + str(self.sequence)))
        return bundle


class SourceDeferralTests(DeferralCase, LiteratureCase):
    def setup_gap(self):
        root, gap = self.metadata(), self.metadata(2)
        self.scope([root])
        self.require_source(gap)
        return root, gap

    def test_missing_whole_source_moves_only_its_obligations_and_never_marks_it_read(self):
        root, gap = self.setup_gap()
        self.capture(gap, status=404)
        before = foundation_report(self.store, "research")
        retained = [o for o in before["obligations"] if o.get("version_id") == gap]
        self.assertTrue(retained)
        self.defer(gap)
        after = foundation_report(self.store, "research")
        self.assertEqual(after["deferred_obligations"], retained)
        self.assertEqual(after["counts"]["fulltext_read"], 0)
        self.assertEqual(after["source_deferrals"][0]["status"], "active")
        self.assertFalse(next(i for i in after["inventory"] if i["version_id"] == gap)["fulltext_read"])
        self.assertTrue(any(o.get("version_id") == root for o in after["obligations"]))
        self.assertIn("search_purpose_missing", {o["code"] for o in after["obligations"]})
        self.assertFalse(after["ready"])
        self.assertEqual(len(self.store.snapshot()["records"].get("availability", {})), 0)

    def test_missing_supplement_keeps_original_pending_units_and_partial_reading(self):
        _, gap = self.setup_gap()
        self.add_missing_supplement(gap)
        before = foundation_report(self.store, "research")
        retained = [o for o in before["obligations"] if o.get("version_id") == gap]
        self.assertIn("required_unit_missing", {o["code"] for o in retained})
        readings = copy.deepcopy(self.store.snapshot()["records"]["reading"])
        self.defer(gap)
        after = foundation_report(self.store, "research")
        self.assertEqual(after["deferred_obligations"], retained)
        self.assertEqual(after["counts"]["fulltext_read"], 0)
        self.assertEqual(self.store.snapshot()["records"]["reading"], readings)

    def test_roots_verification_unknown_and_nonrequired_versions_are_rejected(self):
        root, gap = self.setup_gap()
        other = self.metadata(3)
        operation = self.get_operation("defer-source")
        for version, profile, code in ((root, "research", "source_deferral_inapplicable"),
                                       (gap, "verification", "source_deferral_inapplicable"),
                                       ("arxiv:2601.99999v1", "research", "unknown_work"),
                                       (other, "research", "source_deferral_inapplicable")):
            with self.subTest(version=version, profile=profile):
                self.assert_error(code, lambda: self.mutate(operation, dict(self.build_deferral(version), profile=profile)))
        target = {"kind": "work", "id": gap, "source_id": None, "sha256": None}
        self.scope([gap], profile="verification", target=target)
        self.assert_error("source_deferral_inapplicable", lambda: self.defer(gap))

    def test_checked_nonempty_artifacts_and_dependency_dispositions_are_required(self):
        _, gap = self.setup_gap()
        operation = self.get_operation("defer-source")
        empty = self.artifacts.put(b"", "text/plain")
        for changes in ({"authorization": empty}, {"acquisition_evidence": []},
                        {"acquisition_evidence": [empty]}, {"dependent_claims": []}, {"continuing_work": []}):
            with self.subTest(changes=changes):
                self.assert_error("invalid_source_deferral", lambda: self.mutate(operation, dict(self.build_deferral(gap), **changes)))
        missing = dict(self.build_deferral(gap)["authorization"], sha256="0" * 64)
        from research_harness.errors import ResearchError
        with self.assertRaises(ResearchError):
            self.mutate(operation, dict(self.build_deferral(gap), authorization=missing))

    def test_read_source_cannot_be_deferred(self):
        _, gap = self.setup_gap()
        bundle = self.bundle(gap)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        self.assert_error("source_deferral_inapplicable", lambda: self.defer(gap))

    def test_available_complete_bundle_without_reading_cannot_be_deferred(self):
        _, gap = self.setup_gap()
        self.mutate(import_bundle, self.bundle(gap))
        self.assert_error("source_deferral_inapplicable", lambda: self.defer(gap))

    def test_available_original_without_imported_bundle_cannot_be_deferred(self):
        _, gap = self.setup_gap()
        self.capture(gap)
        self.assert_error("source_deferral_inapplicable", lambda: self.defer(gap))

    def test_available_required_units_with_missing_inspections_cannot_be_deferred(self):
        _, gap = self.setup_gap()
        bundle = self.bundle(gap)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, omit=("body",)))
        self.assertIn("required_unit_uninspected", self.codes())
        self.assert_error("source_deferral_inapplicable", lambda: self.defer(gap))

    def test_unlinked_main_units_are_inventory_work_not_acquisition_gaps(self):
        self.scope([self.metadata()])
        for index, kind in enumerate(("text", "abstract", "figure", "table", "equation", "bibliography")):
            with self.subTest(kind=kind):
                gap = self.metadata(index + 2)
                self.require_source(gap, identifier="required-main-" + kind)
                capture = self.capture(gap, body="The complete main body contains its methods, abstract, figures, tables and equations. References: none.")
                bundle = self.bundle(gap, capture, bundle_id="unlinked-main-" + kind)
                bundle["units"].append({"id": "unlinked-" + kind, "kind": kind, "required": True, "link": None,
                                        "reason": "This unit is in the acquired main body; its locator has not been entered.",
                                        "url": "https://arxiv.org/pdf/" + gap[6:]})
                self.mutate(import_bundle, bundle)
                self.assert_error("source_deferral_inapplicable", lambda: self.defer(gap, "main-" + kind))

    def add_external_figure(self):
        _, gap = self.setup_gap()
        figure = '<figure><img src="/assets/required.png"><figcaption>Required figure.</figcaption></figure>'
        capture = self.capture(gap, "References: none.</p>" + figure + "<p>")
        bundle = self.bundle(gap, capture)
        link = {"version_id": gap, "source_id": capture["source_id"], "artifact": capture["original"],
                "locator": {"kind": "html", "anchor": self.span(capture["original"], figure)}}
        bundle["units"].append({"id": "figure", "kind": "figure", "required": True, "link": link})
        return gap, bundle, link

    def acquire_figure(self, link, response):
        from research_fixtures import client
        from research_harness.visual_assets import acquire_visual_asset
        http, _, _ = client([response], max_retries=0)
        self.sequence += 1
        return acquire_visual_asset(self.store, link, "https://arxiv.org/assets/required.png", http=http,
            expected_revision=self.store.revision, request_id="figure-" + str(self.sequence))

    def test_missing_and_http_failed_required_visuals_move_unchanged_to_deferred_obligations(self):
        gap, bundle, link = self.add_external_figure()
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        for suffix, expected_code in (("missing", "visual_asset_missing"), ("failed", "visual_asset_pending")):
            if suffix == "failed":
                self.acquire_figure(link, (404, {"Content-Type": "text/plain"}, b"Not found"))
            with self.subTest(condition=suffix):
                before = foundation_report(self.store, "research")
                retained = [o for o in before["obligations"] if o.get("version_id") == gap]
                self.assertIn(expected_code, {o["code"] for o in retained})
                self.defer(gap, suffix)
                after = foundation_report(self.store, "research")
                self.assertEqual(after["deferred_obligations"], retained)
                self.assertFalse([o for o in after["obligations"] if o.get("version_id") == gap])
                self.assertFalse(next(i for i in after["inventory"] if i["version_id"] == gap)["fulltext_read"])

    def test_malformed_visual_is_not_an_access_gap_or_a_deferrable_validity_error(self):
        gap, bundle, link = self.add_external_figure()
        self.acquire_figure(link, (200, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff\xff\xd9"))
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        self.assert_error("source_deferral_inapplicable", lambda: self.defer(gap))
        bundle["id"] = "visual-and-supplement"
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": None,
                                "reason": "The derivation is separately unavailable.", "url": "https://example.org/supplement"})
        self.mutate(import_bundle, bundle)
        self.defer(gap)
        pending = [o for o in foundation_report(self.store, "research")["obligations"]
                   if o.get("version_id") == gap and o["code"] == "visual_asset_pending"]
        self.assertTrue(pending)
        self.assertTrue(all(o["reason"] == "malformed_visual_asset" for o in pending))

    def test_changed_capture_restores_obligations_and_retains_stale_decision(self):
        _, gap = self.setup_gap()
        self.defer(gap)
        self.capture(gap, status=404)
        report = foundation_report(self.store, "research")
        self.assertEqual(report["source_deferrals"][0]["status"], "stale")
        self.assertFalse(report["deferred_obligations"])
        self.assertTrue(any(o.get("version_id") == gap for o in report["obligations"]))
        self.defer(gap, "reassessed")
        self.assertEqual([r["status"] for r in foundation_report(self.store, "research")["source_deferrals"]], ["superseded", "active"])

    def test_changed_requirements_and_bundle_units_stale_exact_binding(self):
        _, gap = self.setup_gap()
        bundle = self.add_missing_supplement(gap)
        self.defer(gap)
        self.require_source(gap, "extra", "contradiction")
        self.assertEqual(foundation_report(self.store, "research")["source_deferrals"][0]["status"], "stale")
        self.defer(gap, "reassessed")
        later = copy.deepcopy(bundle)
        later["id"] = "expanded"
        later["units"].append({"id": "appendix", "kind": "supplement", "required": True, "link": None,
                               "reason": "Another unavailable proof appendix.", "url": "https://example.org/appendix"})
        self.mutate(import_bundle, later)
        self.assertEqual(foundation_report(self.store, "research")["source_deferrals"][-1]["status"], "stale")

    def test_resume_restores_obligations_and_receipts_replay_without_reselection(self):
        _, gap = self.setup_gap()
        operation = self.get_operation("defer-source")
        payload, revision = self.build_deferral(gap), self.store.revision
        receipt = operation(self.store, payload, expected_revision=revision, request_id="defer-request")
        resume = {"id": "resume-1", "profile": "research", "version_id": gap,
                  "deferral_id": payload["id"], "reason": "Resume acquisition and ordinary reading obligations."}
        self.mutate(self.get_operation("resume-source"), resume)
        report = foundation_report(self.store, "research")
        self.assertEqual(report["source_deferrals"][0]["status"], "resumed")
        self.assertFalse(report["deferred_obligations"])
        replay = operation(self.store, payload, expected_revision=revision, request_id="defer-request")
        self.assertEqual(replay["result"], receipt["result"])
        self.assertEqual(foundation_report(self.store, "research")["source_deferrals"][0]["status"], "resumed")
        self.assert_error("record_conflict", lambda: self.mutate(operation, dict(payload, reason="Changed decision under the same ID.")))
        self.assert_error("stale_revision", lambda: operation(self.store, dict(payload, id="new"), expected_revision=revision, request_id="new-request"))

    def test_status_exposes_active_and_stale_deferrals_without_private_authorization(self):
        _, gap = self.setup_gap()
        self.defer(gap)
        summary = status_summary(status_report(self.store))
        self.assertEqual(summary["source_deferrals"]["active"], 1)
        self.assertGreater(summary["deferred_obligations"]["total"], 0)
        self.assertNotIn("PRIVATE", json.dumps(summary))
        self.capture(gap, status=404)
        self.assertEqual(status_summary(status_report(self.store))["source_deferrals"]["stale"], 1)

    def test_deferred_classic_is_not_mandatory_but_lineage_and_resumed_classic_are(self):
        from research_harness.principles import initialize_research
        from research_harness.publication import lineage_citation_obligations
        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "lineage-v1"})
        root, gap = self.metadata(), self.metadata(2)
        self.scope([root])
        self.require_source(root, "parent", "lineage")
        self.require_source(gap, "classic", "classic")
        self.defer(gap)
        bundle = {"files": {"bibliography": {"artifact": self.artifacts.put(b"Unrelated references.\n", "text/plain")}}}
        obligations = lineage_citation_obligations(self.store.snapshot()["records"], self.artifacts, bundle)
        self.assertEqual([o["version_id"] for o in obligations], [root])
        self.mutate(self.get_operation("resume-source"), {"id": "resume", "profile": "research", "version_id": gap,
                    "deferral_id": "defer-1", "reason": "Resume this classic's original obligations."})
        obligations = lineage_citation_obligations(self.store.snapshot()["records"], self.artifacts, bundle)
        self.assertEqual({o["version_id"] for o in obligations}, {root, gap})

    def test_identity_and_historical_obligations_of_deferred_source_remain_blocking(self):
        root, gap = self.setup_gap()
        bundle = self.bundle(gap, self.capture(gap, body="Dependent source. References: Unknown reference."))
        bundle["bibliography"]["entries"] = [{"target": "arxiv:2601.99999v1", "kind": "paper", "reason": "An unresolved cited paper.",
            "link": self.link(gap, bundle["source_id"], bundle["units"][0]["link"]["artifact"], "Unknown reference.")}]
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": None,
                                "reason": "The required supplement is unavailable.", "url": "https://example.org/supplement"})
        self.mutate(import_bundle, bundle)
        root_bundle = self.bundle(root, self.capture(root, body="Root. References: Dependent source."), bundle_id="root-bundle")
        root_bundle["bibliography"]["entries"] = [{"target": gap, "kind": "paper", "reason": "The dependent paper cited by the root.",
            "link": self.link(root, root_bundle["source_id"], root_bundle["units"][0]["link"]["artifact"], "Dependent source.")}]
        self.mutate(import_bundle, root_bundle)
        self.scope([root], historical_cutoff="2025-01-01")
        self.defer(gap)
        report = foundation_report(self.store, "research")
        remaining = {o["code"] for o in report["obligations"] if o.get("version_id") == gap}
        self.assertTrue({"reference_unresolved", "historical_version_unresolved"} <= remaining)
        self.assertTrue(report["deferred_obligations"])

    def test_deferral_and_resume_have_cli_examples(self):
        from research_harness.cli import build_parser, run
        parser = build_parser()
        for name in ("defer-source", "resume-source"):
            with self.subTest(operation=name):
                example = run(parser.parse_args(["example", name]))
                self.assertEqual(example["profile"], "research")
                self.assertTrue(example["version_id"])


class DevelopmentDeferralTests(DeferralCase, DevelopmentCase):
    def test_deferral_changes_preparation_digest_without_rewriting_historical_synthesis(self):
        self.prepared_study()
        gap = self.metadata(90)
        self.require_source(gap)
        before = self.api().synthesis_report(self.store, "research")
        historical = self.store.snapshot()["records"]["synthesis"]
        self.defer(gap)
        after = self.api().synthesis_report(self.store, "research")
        self.assertNotEqual(before["literature_digest"], after["literature_digest"])
        self.assertNotEqual(before["preparation_digest"], after["preparation_digest"])
        self.assertEqual(self.store.snapshot()["records"]["synthesis"], historical)
        self.foundation_searches(identifier_suffix="-deferred")
        self.refresh_synthesis("deferred")
        self.assertTrue(self.api().synthesis_report(self.store, "research")["ready"])
        self.mutate(self.development().plan_cycle, self.plan())
        self.assertIn("development_assessment_missing", self.readiness_codes())

    def test_deferred_version_cannot_supply_previously_read_alternative_body_evidence(self):
        self.prepared_study()
        link = self.links[1]
        version = link["version_id"]
        self.require_source(version)
        self.add_missing_supplement(version, self.capture(version, body="Another original needs its absent supplement. References: none."))
        self.defer(version)
        self.foundation_searches(identifier_suffix="-deferred")
        self.refresh_synthesis("deferred")
        plan = self.plan()
        plan["literature"]["sources"] = [link]
        self.assert_error("source_deferred", lambda: self.mutate(self.development().plan_cycle, plan))

    def test_assessment_rejects_deferred_source_even_when_an_older_body_was_read(self):
        self.prepared_study()
        plan, execution = self.run_cycle()
        link = self.links[1]
        self.require_source(link["version_id"])
        self.add_missing_supplement(link["version_id"], self.capture(link["version_id"], body="Another original needs its absent supplement. References: none."))
        self.defer(link["version_id"])
        self.foundation_searches(identifier_suffix="-deferred")
        self.refresh_synthesis("deferred")
        assessment = self.assessment(plan, execution)
        assessment["development"]["contribution"]["evidence"].append({"kind": "source", "link": link})
        self.assert_error("source_deferred", lambda: self.mutate(self.development().assess_cycle, assessment))

    def test_reassessed_deferral_invalidates_an_existing_admission(self):
        from research_harness.development import validate_admitted_execution
        self.prepared_study()
        gap = self.metadata(90)
        self.require_source(gap)
        self.defer(gap)
        self.foundation_searches(identifier_suffix="-deferred")
        self.refresh_synthesis("deferred")
        self.mutate(self.development().plan_cycle, self.plan())
        admission = self.admit()
        self.defer(gap, "reconsidered")
        self.refresh_synthesis("reconsidered")
        self.assert_error("literature_comparison_stale", lambda: validate_admitted_execution(
            self.store.snapshot()["records"], self.artifacts, admission["id"]))

    def test_blind_manuscript_discloses_gap_without_private_decision_artifacts(self):
        from research_harness.review_packets import manuscript_packet, readiness_packet
        from research_harness.execution_evidence import author_readiness_state
        from integration_fixtures import observed_candidate
        self.prepared_study()
        gap = self.metadata(90)
        self.require_source(gap, purpose="classic")
        payload = self.build_deferral(gap)
        self.mutate(self.get_operation("defer-source"), payload)
        self.foundation_searches(identifier_suffix="-deferred")
        self.refresh_synthesis("deferred")
        execution = observed_candidate(self)
        report = author_readiness_state(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue(report["ready"])
        packet = readiness_packet(report)
        self.assertNotIn(payload["authorization"]["sha256"], json.dumps(packet))
        bundle = {"digest": "fixture-bundle", "files": {},
                  "claim_evidence": [{"claim_id": "result", "evidence": [self.result_evidence(execution)]}],
                  "review_inputs": report["review_inputs"], "execution_observations": report["execution_observations"]}
        packet = manuscript_packet(self.store.snapshot()["records"], bundle)
        self.assertEqual(packet["source_gaps"][0]["version_id"], gap)
        self.assertFalse(packet["source_gaps"][0]["fulltext_read"])
        self.assertTrue(packet["source_gaps"][0]["dependent_claims"])
        text = json.dumps(packet)
        for private in ("authorization", "acquisition_evidence", payload["authorization"]["sha256"], "PRIVATE"):
            self.assertNotIn(private, text)
