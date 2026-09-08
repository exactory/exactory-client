import copy
import importlib
import json
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness.acquisition import import_response
from research_harness.literature import foundation_report, import_bundle, record_search
from research_harness.reading import record_reading


class SynthesisCase(LiteratureCase):
    def api(self, name="synthesis"):
        return importlib.import_module("research_harness." + name)

    def configured(self, profile="research"):
        api = self.api("principles")
        work = self.metadata()
        target = {"kind": "objective", "id": "full-objective", "statement": "Establish finite bounds for all bounded input sequences."}
        if profile == "verification":
            capture = self.capture(work)
            target = {"kind": "work", "id": work, "source_id": capture["source_id"], "sha256": capture["original"]["sha256"]}
        self.mutate(api.initialize_research, {"profile": profile, "target": target})
        self.scope([work], profile=profile, **({"target": target} if profile == "verification" else {}))
        return work

    def read_source(self, number=1, *, version=1, complete=True):
        work = self.metadata(number, version=version)
        bundle = self.bundle(work, bundle_id="bundle-%d-%d" % (number, self.sequence))
        self.mutate(import_bundle, bundle)
        if complete:
            self.mutate(record_reading, self.full_note(bundle, note_id="reading-" + bundle["id"]))
        return bundle["units"][0]["link"]

    def claim(self, link, statement="The source establishes a bound under finite input assumptions.", **changes):
        return dict({"statement": statement, "scope": "Bounded sequences in the original study.",
                     "assumptions": ["Inputs are bounded."], "evidence": [link], "date": "2026-09-07",
                     "evidence_timing": "current", "source_support": "supported_as_scoped",
                     "scientific_status": "unresolved", "uncertainties": []}, **changes)

    def gap(self):
        return {"scope": "Near-term non-scientific use.", "reason": "No observed application was established in the source.",
                "next_evidence": "Study downstream constructions and barriers if a relevant use appears."}

    def standards(self, link, profile="research", identifier="standards"):
        return {"id": identifier, "profile": profile, "scope": "The current bounded-sequence study.",
                "field": "Sequence theory", "article_type": None, "venue": None,
                "cohort_doctrine": [self.claim(link, "The cohort makes assumptions and bounded regimes explicit.")],
                "methodology": [self.claim(link)], "reporting": [self.claim(link)],
                "citation": [self.claim(link)], "presentation": [self.claim(link)],
                "applicability_questions": []}

    def rationale(self, link, identifier="rationale"):
        return {"id": identifier, "profile": "research", "scope": "The complete objective for bounded sequences.",
                "and": self.claim(link, scientific_status="established"),
                "but": self.claim(link, "The unbounded extension remains unsupported."),
                "therefore": {"proposal": "Construct a finite bound covering all allowed inputs.",
                              "test": "Prove the claimed bound or exhibit a violating input.",
                              "failure_conditions": ["An allowed sequence violates the bound."]},
                "value": {"kind": "basic_science", "beneficiaries": [],
                          "capabilities": ["A reusable bound for subsequent sequence theory."],
                          "uncertainties": [self.gap()]}}

    def case(self, link, number, relation="external"):
        return {"id": "case-" + str(number), "work_id": link["version_id"], "relation": relation,
                "field": "Numerical analysis" if relation == "external" else "Sequence theory",
                "selection_reason": "This study can test the separation between existence and construction.",
                "original": self.claim(link), "bottleneck": self.claim(link),
                "prior_constraint": self.claim(link), "conceptual_change": self.claim(link),
                "later_validation": {"claims": [], "gaps": [self.gap()]},
                "adoption": {"claims": [], "gaps": [self.gap()]},
                "transfer": {"mechanism": self.claim(link), "mapping": "Map the finite constraint to our sequence domain.",
                             "assumptions": ["The domain is bounded."], "test": "Test the bound at extremal inputs.",
                             "failure_conditions": ["An admissible extremum violates the proposal."],
                             "limits": ["The source supplies no unbounded-domain guarantee."]},
                "selection_signals": ["High citation counts are a discovery signal only."]}

    def innovation(self, cases, identifier="innovation"):
        return {"id": identifier, "profile": "research", "scope": "The finite-bound objective and its distinguishing test.", "cases": cases}

    def context(self, link):
        return {"id": "context", "profile": "research", "scope": "Scientific capabilities and possible neighboring use.",
                "current": [self.claim(link)], "historical": [self.claim(link, evidence_timing="retrospective")],
                "beneficiaries": [], "capabilities": ["Better finite constructions."],
                "barriers": ["No implementation is known for the unbounded setting."],
                "uncertainties": [self.gap()], "speculative_links": [self.gap()]}

    def complete_foundation(self, work, profile="research"):
        collection = self.cohort((1,))
        scope = self.store.snapshot()["records"]["literature_scope"][profile]
        self.scope([work], [collection], profile=profile,
                   **({"target": scope["target"]} if profile == "verification" else {}))
        self.foundation_searches(profile)

    def foundation_searches(self, profile="research", identifier_suffix=""):
        for purpose in ("direct", "originals", "theory", "adjacent", "recent"):
            query = purpose + " finite bound"
            self.sequence += 1
            captured = import_response(self.store, "mcp", json.dumps({"q": query, "results": []}).encode(),
                source_url="https://example.org/search", captured_at="2026-09-07T12:00:00Z", media_type="application/json", mappings=[],
                expected_revision=self.store.revision, request_id="search-" + str(self.sequence))
            self.mutate(record_search, {"id": purpose + identifier_suffix, "profile": profile, "purpose": purpose, "queries": [query],
                "responses": [{"source_id": captured["source_ids"][0], "query": query,
                    "query_locator": {"kind": "json", "pointer": "/q", "value": query}, "results_pointer": "/results"}],
                "captured_at": "2026-09-07T12:00:00Z", "scope": "The finite-bound comparison.",
                "found_work_ids": [], "verdict": "nothing-new", "cited_work_ids": [], "impact": "No matching result in this capture.", "gaps": []})

    def synthesis_codes(self, profile="research"):
        return {x["code"] for x in self.api().synthesis_report(self.store, profile)["obligations"]}


class SynthesisTests(SynthesisCase):
    def test_missing_rationale_standards_innovation_and_context_are_explicit(self):
        api = self.api()
        self.configured()
        report = api.synthesis_report(self.store, "research")
        self.assertFalse(report["ready"])
        self.assertTrue({"standards_missing", "rationale_missing", "innovation_missing", "context_missing"} <= self.synthesis_codes())

    def test_basic_science_without_promised_deployment_is_eligible_but_needs_cited_abt(self):
        api = self.api()
        self.configured()
        link = self.read_source()
        payload = self.rationale(link)
        result = self.mutate(api.record_rationale, payload)
        self.assertTrue(result["result"]["ready"])
        self.assertNotIn("near_term_application_required", self.synthesis_codes())
        invalid = self.rationale(link, "no-context")
        invalid["and"]["evidence"] = []
        self.assert_error("invalid_synthesis", lambda: self.mutate(api.record_rationale, invalid))
        self.assertEqual(self.store.snapshot()["records"]["synthesis"]["rationale"]["payload"]["value"]["kind"], "basic_science")

    def test_only_established_and_completes_research_while_other_claim_roles_remain_scoped(self):
        api = self.api()
        work = self.configured()
        links = [self.read_source(n) for n in range(1, 7)]
        self.complete_foundation(work)
        self.mutate(api.record_standards, self.standards(links[0]))
        self.mutate(api.record_context, self.context(links[0]))
        cases = [self.case(links[0], 0, "within_field")] + [self.case(link, n) for n, link in enumerate(links[1:], 1)]
        self.mutate(api.record_innovation, self.innovation(cases))
        historical = []
        for status in ("refuted", "unresolved", "proposed", "established"):
            payload = self.rationale(links[0], "and-" + status)
            payload["and"]["scientific_status"] = status
            payload["but"]["scientific_status"] = status
            revision = self.store.revision
            result = self.mutate(api.record_rationale, payload)
            saved = self.store.snapshot()["records"]["synthesis"][payload["id"]]
            historical.append((payload, revision, result, saved))
            with self.subTest(status=status):
                report = api.synthesis_report(self.store, "research")
                expected_codes = set() if status == "established" else {"and_context_not_established"}
                self.assertEqual({x["code"] for x in result["result"]["obligations"]}, expected_codes)
                self.assertEqual({x["code"] for x in report["obligations"]}, expected_codes)
                self.assertEqual(result["result"]["ready"], status == "established")
                self.assertEqual(report["ready"], status == "established")
                self.assertTrue(report["configuration"]["ready"])
                self.assertTrue(report["foundation"]["ready"])
                for kind in ("standards", "context", "innovation"):
                    self.assertTrue(report["sections"][kind]["ready"])
                section = report["sections"]["rationale"]
                self.assertEqual(section["payload"], payload)
                self.assertTrue(all(x["reading"] is not None for x in section["evidence"]))
                for item in section["obligations"]:
                    self.assertEqual(item["claim_path"], "and")
                    self.assertEqual(item["scientific_status"], status)
        before = self.store.snapshot()
        for payload, revision, result, saved in historical:
            self.assertEqual(before["records"]["synthesis"][payload["id"]], saved)
            self.assertEqual(api.record_rationale(self.store, payload, expected_revision=revision,
                             request_id=result["request_id"]), result)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual({x["id"] for x in api.synthesis_report(self.store, "research")["history"] if x["kind"] == "rationale"},
                         {payload["id"] for payload, _, _, _ in historical})

    def test_historical_and_eligibility_is_reassessed_without_rewriting_saved_judgments(self):
        api = self.api()
        self.configured()
        payload = self.rationale(self.read_source())
        payload["and"]["scientific_status"] = "refuted"
        result = self.mutate(api.record_rationale, payload)
        before = self.store.snapshot()
        records = copy.deepcopy(before["records"])
        # Model the cached eligibility saved by the earlier implementation.
        assessment = records["synthesis"][payload["id"]]["assessment"]
        assessment.update(ready=True, obligations=[], counts={"obligations": 0})
        historical = copy.deepcopy(records)
        report = api.synthesis_state(records, self.artifacts, "research")
        section = report["sections"]["rationale"]
        self.assertFalse(section["ready"])
        self.assertEqual([x["code"] for x in section["obligations"]], ["and_context_not_established"])
        self.assertEqual(section["payload"], payload)
        self.assertEqual(records, historical)
        self.assertEqual(api.record_rationale(self.store, payload, expected_revision=0, request_id=result["request_id"]), result)
        self.assertEqual(self.store.snapshot(), before)

    def test_full_reading_required_and_current_required_supplement_invalidates_prior_synthesis(self):
        api = self.api()
        self.configured()
        link = self.read_source(complete=False)
        self.mutate(api.record_rationale, self.rationale(link))
        self.assertIn("reading_missing", self.synthesis_codes())
        records = self.store.snapshot()["records"]
        bundle = records["source_bundle"][records["bundle_selection"][link["version_id"]]["bundle_id"]]
        self.mutate(record_reading, self.full_note(bundle, "now-read"))
        self.assertIn("synthesis_dependencies_stale", self.synthesis_codes())
        self.mutate(api.record_rationale, self.rationale(link, "read-rationale"))
        expanded = {k: copy.deepcopy(bundle[k]) for k in ("version_id", "source_id", "scope", "completeness", "units", "inventory", "bibliography", "resolutions")}
        expanded["id"] = "expanded"
        expanded["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": None,
                                   "reason": "The supplement is now identified.", "url": "https://example.org/supplement"})
        self.mutate(import_bundle, expanded)
        report = api.synthesis_report(self.store, "research")
        self.assertFalse(report["sections"]["rationale"]["ready"])
        self.assertIn("required_unit_missing", {x["code"] for x in report["obligations"]})

    def test_distinct_external_papers_and_within_field_cases_are_counted_separately(self):
        api = self.api()
        self.configured()
        links = [self.read_source(n) for n in range(1, 7)]
        cases = [self.case(links[0], 0, "within_field")] + [self.case(link, n) for n, link in enumerate(links[1:], 1)]
        result = self.mutate(api.record_innovation, self.innovation(cases))
        self.assertTrue(result["result"]["ready"], result)
        self.assertEqual(result["result"]["counts"]["external_papers"], 5)
        repeated = [self.case(links[0], 0, "within_field")] + [self.case(links[1], n) for n in range(1, 6)]
        result = self.mutate(api.record_innovation, self.innovation(repeated, "repeated"))
        self.assertEqual(result["result"]["counts"]["external_papers"], 1)
        self.assertIn("external_cases_insufficient", {x["code"] for x in result["result"]["obligations"]})
        self.mutate(api.record_innovation, self.innovation(cases[1:], "no-within"))
        self.assertIn("within_field_case_missing", self.synthesis_codes())

    def test_external_tier_one_root_keeps_external_credit_after_current_reassessment(self):
        api = self.api()
        work = self.configured()
        links = [self.read_source(n) for n in range(1, 7)]
        self.complete_foundation(work)
        self.mutate(api.record_standards, self.standards(links[0]))
        self.mutate(api.record_context, self.context(links[0]))
        self.mutate(api.record_rationale, self.rationale(links[0]))
        cases = [self.case(links[0], 0, "within_field")] + [self.case(link, n) for n, link in enumerate(links[1:], 1)]
        payload = self.innovation(cases)
        original = self.mutate(api.record_innovation, payload)
        self.assertTrue(api.synthesis_report(self.store, "research")["ready"])
        records = self.store.snapshot()["records"]
        scope = records["literature_scope"]["research"]
        self.scope([work, links[1]["version_id"]], scope["collection_ids"])
        stale = api.synthesis_report(self.store, "research")
        self.assertFalse(stale["ready"])
        self.assertEqual({x["code"] for x in stale["foundation"]["obligations"]}, {"search_scope_stale"})
        root = next(x for x in stale["foundation"]["inventory"] if x["version_id"] == links[1]["version_id"])
        self.assertEqual(root["tier"], 1)
        self.assertTrue(root["fulltext_read"])
        for section in stale["sections"].values():
            self.assertFalse(section["ready"])
            self.assertIn("synthesis_dependencies_stale", {x["code"] for x in section["obligations"]})
        self.foundation_searches(identifier_suffix="-expanded")
        current_foundation = foundation_report(self.store, "research")
        self.assertTrue(current_foundation["ready"], current_foundation["obligations"])
        self.mutate(api.record_standards, self.standards(links[0], identifier="standards-expanded"))
        context = self.context(links[0])
        context["id"] = "context-expanded"
        self.mutate(api.record_context, context)
        self.mutate(api.record_rationale, self.rationale(links[0], "rationale-expanded"))
        result = self.mutate(api.record_innovation, self.innovation(cases, "innovation-expanded"))
        report = api.synthesis_report(self.store, "research")
        self.assertEqual(result["result"]["counts"]["external_papers"], 5)
        self.assertEqual(result["result"]["counts"]["within_field_papers"], 1)
        self.assertTrue(result["result"]["ready"], result["result"]["obligations"])
        self.assertTrue(report["ready"], report["obligations"])
        self.assertEqual(report["obligations"], [])
        section = report["sections"]["innovation"]
        self.assertEqual(section["payload"]["cases"], cases)
        self.assertEqual(section["evidence"], original["result"]["evidence"])
        self.assertNotEqual(section["dependencies"]["scope"], original["result"]["dependencies"]["scope"])
        self.assertEqual(self.store.snapshot()["records"]["synthesis"][payload["id"]], records["synthesis"][payload["id"]])
        self.assertEqual(api.record_innovation(self.store, payload, expected_revision=0,
                         request_id=original["request_id"]), original)

    def test_external_field_assessment_rejects_same_field_and_conflicting_family_relationships(self):
        api = self.api()
        self.configured()
        links = [self.read_source(n) for n in range(1, 7)]
        self.mutate(api.record_standards, self.standards(links[0]))
        cases = [self.case(links[0], 0, "within_field")] + [self.case(link, n) for n, link in enumerate(links[1:], 1)]
        original = self.mutate(api.record_innovation, self.innovation(cases))
        self.assertTrue(original["result"]["ready"])
        same_field = copy.deepcopy(cases)
        same_field[1]["field"] = " SEQUENCE THEORY "
        conflict = cases + [self.case(links[1], "within-conflict", "within_field")]
        for identifier, selected, code in (("same-field", same_field, "external_case_within_field"),
                                           ("conflicting-family", conflict, "case_field_conflict")):
            with self.subTest(identifier=identifier):
                payload = self.innovation(selected, identifier)
                result = self.mutate(api.record_innovation, payload)["result"]
                self.assertFalse(result["ready"])
                self.assertEqual(result["counts"]["external_papers"], 4)
                self.assertEqual(result["counts"]["within_field_papers"], 1)
                self.assertEqual({x["code"] for x in result["obligations"]}, {code, "external_cases_insufficient"})
                self.assertEqual(self.store.snapshot()["records"]["synthesis"][identifier]["payload"], payload)

    def test_original_later_validation_and_adoption_keep_their_own_evidence_and_gaps(self):
        api = self.api()
        self.configured()
        link = self.read_source()
        later = self.read_source(2, complete=False)
        case = self.case(link, 1)
        case["later_validation"] = {"claims": [self.claim(later, "A later study reports a bounded outcome.")], "gaps": []}
        result = self.mutate(api.record_innovation, self.innovation([case]))
        self.assertIn("reading_missing", {x["code"] for x in result["result"]["obligations"]})
        invalid = self.innovation([case], "no-transfer")
        invalid["cases"][0]["transfer"]["failure_conditions"] = []
        self.assert_error("invalid_synthesis", lambda: self.mutate(api.record_innovation, invalid))

    def test_context_requires_dated_current_historical_sources_and_scoped_uncertainty(self):
        api = self.api()
        self.configured()
        link = self.read_source()
        result = self.mutate(api.record_context, self.context(link))
        self.assertTrue(result["result"]["ready"])
        for field, bad_value in (("historical", []), ("current", []), ("uncertainties", [])):
            value = self.context(link)
            value["id"] = "bad-" + field
            value[field] = bad_value
            self.assert_error("invalid_synthesis", lambda: self.mutate(api.record_context, value))
        invalid = self.context(link)
        invalid["id"] = "undated"
        del invalid["historical"][0]["date"]
        self.assert_error("invalid_synthesis", lambda: self.mutate(api.record_context, invalid))

    def test_current_text_cannot_become_historical_prior_art_and_date_assertions_remain_visible(self):
        api = self.api()
        self.configured()
        link = self.read_source(2, version=2)
        payload = self.rationale(link)
        payload["and"]["date"] = "2026-01-15"
        payload["and"]["evidence_timing"] = "contemporaneous"
        self.mutate(api.record_rationale, payload)
        report = api.synthesis_report(self.store, "research")
        self.assertIn("historical_version_unresolved", {x["code"] for x in report["obligations"]})
        evidence = report["sections"]["rationale"]["evidence"]
        self.assertTrue(any(x["date_assertions"] and x["link"]["version_id"] == link["version_id"] for x in evidence))
        self.assertTrue(all(x["reading"] is not None for x in evidence))

    def test_source_support_and_scientific_status_remain_independent(self):
        api = self.api()
        self.configured()
        link = self.read_source()
        for support in ("source_not_supported", "source_contradicted", "unresolved"):
            for status in ("established", "unresolved"):
                with self.subTest(support=support, status=status):
                    payload = self.rationale(link, support + "-" + status)
                    payload["and"].update(source_support=support, scientific_status=status, uncertainties=[self.gap()])
                    result = self.mutate(api.record_rationale, payload)["result"]
                    self.assertFalse(result["ready"])
                    expected_codes = {"claim_source_support_unresolved"}
                    if status != "established":
                        expected_codes.add("and_context_not_established")
                    report = api.synthesis_report(self.store, "research")
                    section = report["sections"]["rationale"]
                    self.assertEqual({x["code"] for x in section["obligations"]}, expected_codes)
                    self.assertEqual(section["payload"]["and"]["source_support"], support)
                    self.assertEqual(section["payload"]["and"]["scientific_status"], status)

    def test_verification_requires_standards_without_author_goals_or_external_gallery(self):
        api = self.api()
        work = self.configured("verification")
        link = self.read_source()
        self.complete_foundation(work, "verification")
        self.assertTrue(foundation_report(self.store, "verification")["ready"])
        self.mutate(api.record_standards, self.standards(link, "verification"))
        report = api.synthesis_report(self.store, "verification")
        self.assertTrue(report["ready"], report["obligations"])
        self.assertEqual(set(report["sections"]), {"standards"})
        self.assertNotIn("research_objective", self.store.snapshot()["records"])
        invalid = self.rationale(link)
        invalid["profile"] = "verification"
        self.assert_error("profile_inapplicable", lambda: self.mutate(api.record_rationale, invalid))

    def test_complete_research_and_scope_changes_require_new_assessments_preserving_old_results(self):
        api = self.api()
        work = self.configured()
        links = [self.read_source(n) for n in range(1, 7)]
        self.complete_foundation(work)
        self.assertTrue(foundation_report(self.store, "research")["ready"])
        self.mutate(api.record_standards, self.standards(links[0]))
        self.mutate(api.record_context, self.context(links[0]))
        payload = self.rationale(links[0])
        revision = self.store.revision
        result = api.record_rationale(self.store, payload, expected_revision=revision, request_id="rationale-original")
        cases = [self.case(links[0], 0, "within_field")] + [self.case(link, n) for n, link in enumerate(links[1:], 1)]
        self.mutate(api.record_innovation, self.innovation(cases))
        report = api.synthesis_report(self.store, "research")
        self.assertTrue(report["ready"], report["obligations"])
        self.assertIn("entailment", report["limits"])
        self.metadata(20)
        self.assertEqual(report["digest"], api.synthesis_report(self.store, "research")["digest"])
        self.scope([links[1]["version_id"]])
        self.assertIn("synthesis_dependencies_stale", self.synthesis_codes())
        self.assertEqual(api.record_rationale(self.store, payload, expected_revision=revision, request_id="rationale-original"), result)
        self.assertEqual(self.store.snapshot()["records"]["synthesis"]["rationale"]["payload"], payload)

    def test_constitution_adoption_does_not_automatically_revalidate_synthesis(self):
        api = self.api()
        policy = self.api("principles")
        self.configured()
        link = self.read_source()
        self.mutate(api.record_rationale, self.rationale(link))
        old = self.store.snapshot()["records"]["configuration"]["research"]["constitution"]
        changed = self.root / "new-policy.md"
        changed.write_text("# Research constitution\n\nVersion: 2\n\nCurrent evidence must be reassessed.\n", encoding="utf-8")
        with patch.object(policy, "CONSTITUTION_PATH", changed):
            self.mutate(policy.revalidate_constitution, {"previous_sha256": old["sha256"], "reason": "Review the changed policy."})
            self.assertIn("synthesis_dependencies_stale", self.synthesis_codes())
            self.mutate(api.record_rationale, self.rationale(link, "rationale-v2"))
            self.assertTrue(api.synthesis_report(self.store, "research")["sections"]["rationale"]["ready"])
            self.assertEqual(len(self.store.snapshot()["records"]["synthesis"]), 2)

    def test_field_standard_can_cite_a_full_read_original_url_document(self):
        from research_harness.acquisition import acquire_fulltext
        from research_fixtures import client
        api = self.api()
        self.configured()
        url = "https://example.org/standards/sequence-reporting"
        identifier = "url:" + url
        data = {"id": identifier, "title": "Sequence reporting standard", "type": "standard"}
        import_response(self.store, "web", json.dumps(data).encode(), source_url="https://example.org/catalog",
            captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/id", "title": "/title", "type": "/type"}],
            expected_revision=self.store.revision, request_id="standard-identity")
        body = b'<article><section class="article-body"><p>State assumptions and comparison regimes. References: none.</p></section></article>'
        http, _, _ = client([(200, {"Content-Type": "text/html"}, body)])
        capture = acquire_fulltext(self.store, identifier, url, http=http,
            expected_revision=self.store.revision, request_id="standard-body")["capture"]
        bundle = self.bundle(identifier, capture, "standard-bundle")
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "standard-reading"))
        link = bundle["units"][0]["link"]
        payload = self.standards(link)
        payload["venue"] = "The selected sequence journal"
        payload["article_type"] = "Methods article"
        result = self.mutate(api.record_standards, payload)
        self.assertTrue(result["result"]["ready"], result)
        self.assertEqual(result["result"]["evidence"][0]["link"]["version_id"], identifier)
        payload = self.innovation([self.case(link, 1)], "not-a-paper")
        result = self.mutate(api.record_innovation, payload)
        self.assertEqual(result["result"]["counts"]["external_papers"], 0)
        self.assertIn("innovation_paper_type_unresolved", {x["code"] for x in result["result"]["obligations"]})

    def test_different_versions_and_aliases_cannot_inflate_external_coverage(self):
        from research_fixtures import atom, entry
        api = self.api()
        self.configured()
        links = [self.read_source(2, version=v) for v in range(1, 6)]
        result = self.mutate(api.record_innovation, self.innovation([self.case(link, v) for v, link in enumerate(links)]))
        self.assertEqual(result["result"]["counts"]["external_papers"], 1)
        data = atom([entry("2601.00003v1", doi="10.5555/shared-alias")], total=1)
        import_response(self.store, "arxiv", data, source_url="https://export.arxiv.org/api/query?id_list=2601.00003v1",
            captured_at="2026-09-07T12:00:00Z", expected_revision=self.store.revision, request_id="alias-third")
        # A conflicting same-work assertion for the acquired exact original is
        # retained by the provider boundary; synthesis must not resolve it by fiat.
        raw = {"id": "arxiv:2601.00003v1", "title": "Alias claim", "aliases": [links[0]["version_id"]]}
        import_response(self.store, "mcp", json.dumps(raw).encode(), source_url="https://example.org/alias",
            captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/id", "title": "/title", "aliases": "/aliases"}],
            expected_revision=self.store.revision, request_id="conflicting-alias")
        self.assertIn("ambiguous_alias", self.synthesis_codes())
        self.assertEqual(len(self.store.snapshot()["records"]["synthesis"]), 1)

    def test_more_than_ten_external_papers_are_kept_but_require_a_bounded_selection(self):
        api = self.api()
        self.configured()
        cases = [self.case(self.read_source(n), n) for n in range(2, 13)]
        result = self.mutate(api.record_innovation, self.innovation(cases))
        self.assertEqual(result["result"]["counts"]["external_papers"], 11)
        self.assertIn("external_cases_excess", {x["code"] for x in result["result"]["obligations"]})
        self.assertEqual(len(self.store.snapshot()["records"]["work"]), 12)

    def test_later_event_before_the_original_is_preserved_as_a_temporal_obligation(self):
        api = self.api()
        self.configured()
        link = self.read_source(2)
        case = self.case(link, 1)
        event = self.claim(link, "A reported validation has a conflicting event date.", date="2025-01-01")
        case["later_validation"] = {"claims": [event], "gaps": [self.gap()]}
        result = self.mutate(api.record_innovation, self.innovation([case]))
        self.assertIn("innovation_event_order_unresolved", {x["code"] for x in result["result"]["obligations"]})
        stored = self.store.snapshot()["records"]["synthesis"]["innovation"]["payload"]
        self.assertEqual(stored["cases"][0]["later_validation"]["claims"][0]["date"], "2025-01-01")

    def test_source_reading_of_another_original_cannot_support_the_linked_body(self):
        api = self.api()
        self.configured()
        link = self.read_source(2)
        capture = self.capture(link["version_id"], "A different result is limited to nonempty inputs. References: none.")
        second = self.link(link["version_id"], capture["source_id"], capture["text"])
        result = self.mutate(api.record_rationale, self.rationale(second))
        self.assertIn("reading_missing", {x["code"] for x in result["result"]["obligations"]})
        historical = self.mutate(api.record_rationale, self.rationale(link, "historical-original"))
        self.assertTrue(historical["result"]["ready"])

    def test_verification_claims_use_the_configured_exact_target_body(self):
        api = self.api()
        work = self.configured("verification")
        capture = self.capture(work, "The changed target supports a different regime. References: none.")
        bundle = self.bundle(work, capture, "changed-target")
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "changed-target-reading"))
        result = self.mutate(api.record_standards, self.standards(bundle["units"][0]["link"], "verification"))
        self.assertIn("reading_missing", {x["code"] for x in result["result"]["obligations"]})

    def test_quantitative_scope_keeps_actual_roles_units_and_unknown_dimensions(self):
        api = self.api()
        self.configured()
        link = self.read_source()
        payload = self.rationale(link)
        dimensions = [
            {"dimension": "decay rate", "value": "2", "units": "per iteration", "role": "fitted", "status": "known", "reason": "Estimated on the training series."},
            {"dimension": "input bound", "value": "8", "units": None, "role": "external_input", "status": "known", "reason": "Supplied by the measurement protocol."},
            {"dimension": "series selection", "value": "all complete series", "units": None, "role": "selection", "status": "known", "reason": "The observed sampling restriction."},
            {"dimension": "implementation latency", "value": None, "units": "seconds", "role": "observed", "status": "unknown", "reason": "No implementation was tested."},
        ]
        payload["and"]["quantitative_scope"] = dimensions
        self.mutate(api.record_rationale, payload)
        self.assertEqual(api.synthesis_report(self.store, "research")["sections"]["rationale"]["payload"]["and"]["quantitative_scope"], dimensions)
        bad = copy.deepcopy(payload)
        bad["id"] = "invented-value"
        bad["and"]["quantitative_scope"][-1]["value"] = "0"
        self.assert_error("invalid_synthesis", lambda: self.mutate(api.record_rationale, bad))

    def test_reusing_a_collection_still_requires_study_specific_transfer(self):
        api = self.api()
        self.configured()
        link = self.read_source(2)
        payload = self.innovation([self.case(link, 1)])
        payload["origin_collection"] = {"id": "shared-cases", "version": "3"}
        payload["cases"][0]["transfer"]["mapping"] = ""
        self.assert_error("invalid_synthesis", lambda: self.mutate(api.record_innovation, payload))
        self.assertNotIn("synthesis", self.store.snapshot()["records"])

    def test_synthesis_prepare_race_cannot_commit_an_assessment_against_old_dependencies(self):
        api = self.api()
        self.configured()
        link = self.read_source()
        current = api.foundation_state

        def foundation_after_concurrent_write(records, artifacts, profile):
            result = current(records, artifacts, profile)
            self.store.mutate("concurrent-note", {}, lambda tx: tx.put("note", "concurrent", {"text": "Another writer acted."}),
                              expected_revision=self.store.revision, request_id="concurrent-note")
            return result

        with patch.object(api, "foundation_state", side_effect=foundation_after_concurrent_write):
            self.assert_error("stale_revision", lambda: self.mutate(api.record_rationale, self.rationale(link)))
        self.assertNotIn("synthesis", self.store.snapshot()["records"])
        self.assertEqual(self.store.snapshot()["records"]["note"]["concurrent"]["text"], "Another writer acted.")

    def test_malformed_payloads_fail_typed_without_mutating_existing_assessments(self):
        api = self.api()
        self.configured()
        link = self.read_source()
        good = self.rationale(link)
        self.mutate(api.record_rationale, good)
        for path, invalid in ((("id",), []), (("and", "evidence"), {}), (("and", "date"), False), (("value", "kind"), [])):
            value = copy.deepcopy(good)
            value["id"] = "malformed"
            container = value
            for key in path[:-1]:
                container = container[key]
            container[path[-1]] = invalid
            before = self.store.snapshot()
            self.assert_error("invalid_synthesis", lambda: self.mutate(api.record_rationale, value))
            self.assertEqual(self.store.snapshot(), before)

    def test_section_counts_include_the_current_stale_assessment_obligation(self):
        api = self.api()
        self.configured()
        link = self.read_source()
        self.mutate(api.record_rationale, self.rationale(link))
        self.scope([self.metadata(2)])
        section = api.synthesis_report(self.store, "research")["sections"]["rationale"]
        self.assertEqual([x["code"] for x in section["obligations"]], ["synthesis_dependencies_stale"])
        self.assertEqual(section["counts"]["obligations"], 1)

    def test_wrong_profile_report_does_not_expose_private_author_objectives(self):
        api = self.api()
        self.configured()
        report = api.synthesis_report(self.store, "verification")
        self.assertFalse(report["ready"])
        self.assertIn("profile_mismatch", {x["code"] for x in report["obligations"]})
        self.assertIsNone(report["configuration"]["target"])
        self.assertIsNone(report["configuration"]["configuration"])
        self.assertEqual(set(report["sections"]), {"standards"})

    def test_current_cohort_version_reading_does_not_supply_prepublication_claim_evidence(self):
        from research_harness.acquisition import collect_cohort
        from research_fixtures import atom, client, entry, xml_response
        api = self.api()
        root = self.configured()
        http, _, _ = client([xml_response(atom([entry("2601.00002v2")], total=1))])
        collected = collect_cohort(self.store, {"corpus": "arxiv", "primaryCategory": "cs.LG",
            "windowStart": "2026-01-01", "windowEnd": "2026-01-31"}, http=http,
            expected_revision=self.store.revision, request_id="current-version-cohort")
        collection = self.store.snapshot()["records"]["collection"][collected["collection_id"]]
        self.scope([root], [collected["collection_id"]], historical_cutoff="2026-01-15")
        link = self.read_source(2, version=2)
        rationale = self.rationale(link)
        rationale["and"].update(date="2026-01-15", evidence_timing="contemporaneous")
        self.mutate(api.record_rationale, rationale)
        report = api.synthesis_report(self.store, "research")
        self.assertEqual(report["foundation"]["cohort"]["counts"]["abstracts_read"], 1)
        self.assertTrue(any(x["code"] == "historical_version_unresolved" and x.get("claim_path") == "and"
                            for x in report["sections"]["rationale"]["obligations"]))
        records = self.store.snapshot()["records"]
        self.assertNotIn("arxiv:2601.00002v1", records["work"])
        self.assertEqual(records["collection"][collected["collection_id"]], collection)
        dates = report["sections"]["rationale"]["evidence"][0]["date_assertions"]
        self.assertTrue(any(d["values"].get("published") == "2026-01-03T12:00:00Z" for d in dates))
        self.assertTrue(any(d["values"].get("updated") == "2026-02-01T10:00:00Z" for d in dates))

    def test_required_external_units_are_reported_even_outside_the_foundation_graph(self):
        api = self.api()
        self.configured()
        link = self.read_source(2)
        self.mutate(api.record_rationale, self.rationale(link))
        records = self.store.snapshot()["records"]
        bundle = records["source_bundle"][records["bundle_selection"][link["version_id"]]["bundle_id"]]
        expanded = {k: copy.deepcopy(bundle[k]) for k in ("version_id", "source_id", "scope", "completeness", "units", "inventory", "bibliography", "resolutions")}
        expanded["id"] = "external-supplement"
        expanded["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": None,
                                   "reason": "A required external appendix is still unavailable.", "url": "https://example.org/external-appendix"})
        self.mutate(import_bundle, expanded)
        report = api.synthesis_report(self.store, "research")
        self.assertFalse(any(x["version_id"] == link["version_id"] for x in report["foundation"]["inventory"]))
        obligations = report["sections"]["rationale"]["obligations"]
        self.assertTrue(any(x["code"] == "required_unit_missing" and x["unit_id"] == "supplement" for x in obligations))
        self.assertFalse(report["sections"]["rationale"]["ready"])

    def test_external_metadata_bytes_remain_required_for_source_dates_and_identity(self):
        api = self.api()
        self.configured()
        link = self.read_source(2)
        payload = self.rationale(link)
        result = self.mutate(api.record_rationale, payload)
        records = self.store.snapshot()["records"]
        source_id = records["work"][link["version_id"]]["source_ids"][0]
        artifact = records["source"][source_id]["response"]
        path = self.root / artifact["path"]
        path.chmod(0o600)
        path.write_bytes(b"The original metadata was replaced.")
        section = api.synthesis_report(self.store, "research")["sections"]["rationale"]
        self.assertFalse(section["ready"])
        self.assertIn("artifact_corrupt", {x["code"] for x in section["obligations"]})
        self.assertEqual(api.record_rationale(self.store, payload, expected_revision=0, request_id=result["request_id"]), result)

    def test_later_source_backed_paper_type_resolves_unknown_but_preserves_conflict(self):
        api = self.api()
        self.configured()
        identifier = "arxiv:2601.00002v1"
        raw = {"id": identifier, "title": "An initially unclassified source"}
        import_response(self.store, "mcp", json.dumps(raw).encode(), source_url="https://example.org/catalog",
            captured_at="2026-09-07T12:00:00Z", media_type="application/json", mappings=[{"id": "/id", "title": "/title"}],
            expected_revision=self.store.revision, request_id="unclassified")
        link = self.read_source(2)
        result = self.mutate(api.record_innovation, self.innovation([self.case(link, 1)]))
        self.assertEqual(result["result"]["counts"]["external_papers"], 1)
        self.assertEqual(self.store.snapshot()["records"]["work"][identifier]["type"], "unknown")
        raw["type"] = "standard"
        import_response(self.store, "mcp", json.dumps(raw).encode(), source_url="https://example.org/conflicting-type",
            captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/id", "title": "/title", "type": "/type"}],
            expected_revision=self.store.revision, request_id="type-conflict")
        section = api.synthesis_report(self.store, "research")["sections"]["innovation"]
        self.assertEqual(section["counts"]["external_papers"], 0)
        self.assertIn("innovation_paper_type_unresolved", {x["code"] for x in section["obligations"]})
