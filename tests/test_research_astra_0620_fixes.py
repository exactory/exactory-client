"""Regressions for the nine reviewed 0.62.0 defects."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from development_fixtures import DevelopmentCase
from research_harness import cli, development, fulltext, publication, publication_files
from research_harness.artifacts import ArtifactStore
from research_harness.cli import status_report
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.storage import Store


class RestoreAndDriftTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)

    def save_bundle(self, entries):
        files = {}
        for index, (path, pinned) in enumerate(entries):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(pinned)
            files[str(index)] = {"path": path, "artifact": self.artifacts.put(pinned, "application/octet-stream")}
        bundle = {"id": "legacy", "files": files, "prepared_revision": 0,
                  "review_inputs": {}, "execution_observations": []}
        bundle["digest"] = digest(bundle)

        def save(transaction):
            transaction.put("publication_bundle", bundle["id"], bundle)
            transaction.put("publication_selection", "bundle", {"id": bundle["id"]})
            return {"legacy_result": [3, 4, 7]}

        self.store.mutate("test.legacy", {}, save, expected_revision=self.store.revision,
                          request_id="bundle-" + str(self.store.revision))
        self.bundle = bundle
        return bundle

    def restore(self):
        return publication_files.restore_bundle(self.store, self.bundle["digest"])

    def test_g4_status_survives_missing_and_corrupt_pinned_objects(self):
        bundle = self.save_bundle([("draft/paper.pdf", b"pinned")])
        target = self.root / bundle["files"]["0"]["artifact"]["path"]
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                target.unlink(missing_ok=True)
                if corrupt:
                    target.write_bytes(b"corrupt")
                before = self.store.snapshot()
                report = status_report(self.store, counters=True)
                self.assertIsNone(report["bundle_drift"])
                self.assertEqual(self.store.snapshot(), before)
                with self.assertRaises(ResearchError) as raised:
                    self.restore()
                self.assertEqual(raised.exception.code, "artifact_corrupt" if corrupt else "artifact_missing")

    def test_g7_drift_streams_only_working_bytes_and_counts_them(self):
        data = b"x" * (2 * 65536 + 17)
        self.save_bundle([("draft/large.zip", data)])
        evaluation = Evaluation(self.store.snapshot()["records"], self.artifacts)
        open_stream = os.fdopen
        reads = []

        class BoundedReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def read(self, size=-1):
                if not 0 < size <= 65536:
                    raise AssertionError("Drift must not issue an unbounded file read")
                reads.append(size)
                return self.stream.read(size)

            def __getattr__(self, name):
                return getattr(self.stream, name)

        def open_bounded(*args, **kwargs):
            return BoundedReader(open_stream(*args, **kwargs))

        with mock.patch("os.fdopen", side_effect=open_bounded):
            self.assertIsNone(publication_files.find_bundle_drift(evaluation.records, evaluation))
        self.assertTrue(reads)
        self.assertEqual(evaluation.counters["working_bytes_hashed"], len(data))
        self.assertEqual(evaluation.counters["artifacts_verified"], 0)
        (self.root / "draft/large.zip").write_bytes(b"short")
        evaluation = Evaluation(evaluation.records, self.artifacts)
        with mock.patch("os.fdopen", side_effect=AssertionError("Size mismatch must not read bytes")):
            self.assertEqual(publication_files.find_bundle_drift(evaluation.records, evaluation)["files"],
                             [{"path": "draft/large.zip", "code": "publication_artifact_changed"}])
        self.assertEqual(evaluation.counters["working_bytes_hashed"], 0)

    def test_g7_status_and_next_counters_include_drift_hashing(self):
        self.save_bundle([("draft/paper", b"pinned")])
        for command in ("status", "next"):
            for summary in (False, True):
                with self.subTest(command=command, summary=summary):
                    arguments = [command, "--workspace", str(self.root), "--counters"]
                    if summary:
                        arguments.append("--summary")
                    report = cli.run(cli.build_parser().parse_args(arguments))
                    self.assertEqual(report["evaluation"]["working_bytes_hashed"], 6)
        report = status_report(self.store, counters=True)
        report["next"] = {key: "\x01" * 10000 for key in
                          ("code", "version_id", "work_id", "collection_id", "unit_id", "explanation")}
        report.update(manuscript_ready=False, objective_complete=False,
                      manuscript_obligations=[{"code": "x" * 47 + str(index)} for index in range(6)],
                      objective_obligations=[{"code": "y" * 47 + str(index)} for index in range(6)],
                      scope_contract_id="\x01" * 64, scientific_target_digest="a" * 64)
        summary = cli.next_summary(report, counters=True)
        self.assertEqual(summary["evaluation"]["working_bytes_hashed"], 6)
        self.assertLessEqual(len(json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8")), 4096)

    def test_g4_unreadable_working_file_keeps_its_code(self):
        self.save_bundle([("draft/paper", b"pinned")])
        with mock.patch.object(publication_files, "_regular_file", side_effect=PermissionError(13, "denied"),
                               create=True), mock.patch.object(publication_files, "read_file",
                                                               side_effect=ResearchError("storage_io", "denied")):
            report = publication_files.find_bundle_drift(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(report["files"], [{"path": "draft/paper", "code": "storage_io"}])

    def test_g2_restore_backs_up_the_edit_made_after_its_last_comparison(self):
        self.save_bundle([("draft/paper.pdf", b"pinned")])
        working = self.root / "draft/paper.pdf"
        working.write_bytes(b"earlier edit")
        before = self.store.snapshot()

        rename = os.rename

        def move_with_edit(name, displaced, **kwargs):
            if name == "paper.pdf" and displaced.startswith(".restore-backup-"):
                working.write_bytes(b"late edit")
            return rename(name, displaced, **kwargs)

        with mock.patch.object(publication_files.os, "rename", side_effect=move_with_edit):
            result = self.restore()
        self.assertEqual(self.artifacts.read(result["restored"][0]["backup"]), b"late edit")
        self.assertEqual(working.read_bytes(), b"pinned")
        self.assertEqual(self.store.snapshot(), before)

    def test_g2_restore_refuses_a_new_destination_and_lists_all_backups(self):
        self.save_bundle([("draft/a", b"pin-a"), ("draft/b", b"pin-b")])
        for name in ("a", "b"):
            (self.root / "draft" / name).write_bytes(("edit-" + name).encode())

        install = publication_files._install_restore_group

        def install_with_destination(root, group, installed):
            if group["items"][0]["path"] == "draft/b":
                (root / "draft/b").write_bytes(b"concurrent edit")
            return install(root, group, installed)

        with mock.patch.object(publication_files, "_install_restore_group", side_effect=install_with_destination):
            with self.assertRaises(ResearchError) as raised:
                self.restore()
        self.assertEqual(raised.exception.code, "publication_artifact_changed")
        self.assertEqual((self.root / "draft/b").read_bytes(), b"concurrent edit")
        kept = {item["path"]: self.artifacts.read(item["backup"])
                for item in raised.exception.details["backups"] if item["backup"] is not None}
        self.assertEqual(kept, {"draft/a": b"edit-a", "draft/b": b"edit-b"})

    def test_g3_restore_refuses_conflicting_inode_aliases_before_writing(self):
        self.save_bundle([("draft/a", b"first"), ("draft/b", b"second")])
        (self.root / "draft/b").unlink()
        os.link(self.root / "draft/a", self.root / "draft/b")
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as raised:
            self.restore()
        self.assertEqual(raised.exception.code, "publication_artifact_invalid")
        self.assertEqual((self.root / "draft/a").read_bytes(), b"first")
        self.assertEqual((self.root / "draft/b").read_bytes(), b"first")
        self.assertEqual(self.store.snapshot(), before)

    def test_g3_equal_inode_aliases_are_restored_as_one_destination(self):
        self.save_bundle([("draft/a", b"pinned"), ("draft/b", b"pinned")])
        (self.root / "draft/b").unlink()
        os.link(self.root / "draft/a", self.root / "draft/b")
        (self.root / "draft/a").write_bytes(b"edit")
        result = self.restore()
        self.assertEqual(len(result["restored"]), 1)
        self.assertEqual((self.root / "draft/a").read_bytes(), b"pinned")
        self.assertEqual((self.root / "draft/b").read_bytes(), b"pinned")
        self.assertTrue(os.path.samefile(self.root / "draft/a", self.root / "draft/b"))

    def test_g3_missing_case_and_unicode_aliases_are_refused_before_writes(self):
        for first, second in (("draft/Case", "draft/case"), ("draft/caf\u00e9", "draft/cafe\u0301"),
                              ("draft/stra\u00dfe", "draft/STRASSE"),
                              ("draft/Parent/Paper", "draft/parent/paper"),
                              ("draft/caf\u00e9/Paper", "draft/cafe\u0301/paper"),
                              ("draft/stra\u00dfe/Paper", "draft/STRASSE/paper")):
            with self.subTest(first=first):
                self.save_bundle([(first, b"first"), (second, b"different")])
                for path in (first, second):
                    (self.root / path).unlink(missing_ok=True)
                before = self.store.snapshot()
                objects = set((self.root / "research/sources/objects").iterdir())
                with self.assertRaises(ResearchError) as raised:
                    self.restore()
                self.assertEqual(raised.exception.code, "publication_artifact_invalid")
                self.assertFalse((self.root / first).exists())
                self.assertEqual(set((self.root / "research/sources/objects").iterdir()), objects)
                self.assertEqual(self.store.snapshot(), before)

    def test_g3_equal_normalized_paths_are_restored_as_one_destination(self):
        for first, second in (("draft/Case", "draft/case"), ("draft/caf\u00e9", "draft/cafe\u0301"),
                              ("draft/stra\u00dfe/Paper", "draft/STRASSE/paper")):
            with self.subTest(first=first):
                self.save_bundle([(first, b"pinned"), (second, b"pinned")])
                for path in (first, second):
                    (self.root / path).unlink(missing_ok=True)
                result = self.restore()
                self.assertEqual(len(result["restored"]), 1)
                self.assertEqual((self.root / first).read_bytes(), b"pinned")

    def test_g3_normalized_paths_coalesce_with_distinct_parent_inodes(self):
        for name in ("first", "second"):
            (self.root / name).mkdir()
        self.assertNotEqual((self.root / "first").stat().st_ino, (self.root / "second").stat().st_ino)
        checked_parent = publication_files.checked_parent
        for first, second in (("Draft", "draft"), ("caf\u00e9", "cafe\u0301"), ("Stra\u00dfe", "STRASSE")):
            with self.subTest(first=first):
                parents = {first: "first", second: "second"}

                def resolve_distinct_parents(root, path, **kwargs):
                    parent, _, name = path.partition("/")
                    return checked_parent(root, parents[parent] + "/" + name, **kwargs)

                with mock.patch.object(publication_files, "checked_parent", side_effect=resolve_distinct_parents):
                    first_entry, first_keys = publication_files._resolve_restore_keys(self.root, first + "/paper")
                    second_entry, second_keys = publication_files._resolve_restore_keys(self.root, second + "/paper")
                self.assertEqual(first_entry, second_entry)
                self.assertTrue(first_keys & second_keys)

    def test_g2_displaced_path_preserves_edits_through_an_already_open_file(self):
        self.save_bundle([("draft/paper", b"pinned")])
        path = self.root / "draft/paper"
        path.write_bytes(b"edit")
        install = publication_files._install_restore_group
        with path.open("r+b") as writer:
            def install_with_edit(root, group, installed):
                writer.seek(0)
                writer.write(b"edit through open file")
                writer.truncate()
                writer.flush()
                return install(root, group, installed)

            with mock.patch.object(publication_files, "_install_restore_group", side_effect=install_with_edit):
                result = self.restore()
        kept = self.root / result["restored"][0]["backup_path"]
        self.assertEqual(kept.read_bytes(), b"edit through open file")
        self.assertEqual(path.read_bytes(), b"pinned")

    def test_g8_pin_reports_missing_mapping_before_outside_evidence(self):
        paths = {"pdf": "draft/p.pdf", "abstract": "draft/a.txt", "bibliography": "draft/b.bib",
                 "claims": "draft/c.json", "sources": None}
        for kind, data in (("pdf", b"%PDF-1.4"), ("abstract", b"Abstract"), ("bibliography", b"@article{x}"),
                           ("claims", b'[{"id":"A","claim":"A"},{"id":"B","claim":"B"}]')):
            target = self.root / paths[kind]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
        payload = {"id": "pin", "files": paths, "claim_evidence": [{"claim_id": "A", "evidence": [{"outside": True}]}]}
        with mock.patch.object(publication, "_ready", return_value={"candidate": {"evidence": []}}):
            with self.assertRaises(ResearchError) as raised:
                publication.prepare_publication(self.store, payload, expected_revision=self.store.revision,
                                                request_id="missing-map")
        self.assertEqual(raised.exception.code, "publication_claims_missing")


class JatsRefusalTests(unittest.TestCase):
    def test_g5_undefined_text_attribute_and_parameter_entities_are_refused(self):
        for xml in (b'<article><body><p>&missing;</p></body></article>',
                    b'<article><body><p>Result</p><fig><graphic href="&missing;"/></fig></body></article>',
                    b'<!DOCTYPE article [ %missing; ]><article><body><p>Result</p></body></article>',
                    b'<!DOCTYPE article SYSTEM "unread.dtd" [<!ATTLIST graphic href CDATA "&img;">]>'
                    b'<article><body><p>Result</p><fig><graphic/></fig></body></article>',
                    b'<!DOCTYPE article SYSTEM "unread.dtd" [<!ATTLIST unused value CDATA "&img;">]>'
                    b'<article><body><p>Result</p></body></article>',
                    b'<!DOCTYPE article [<!ENTITY img SYSTEM "unread">]>'
                    b'<article><body><p>Result</p><fig><graphic href="&img;"/></fig></body></article>'):
            with self.subTest(xml=xml):
                self.assertEqual(fulltext.extract(xml, "application/xml")["status"], "xml_external_entity")

    def test_g5_unknown_attribute_entities_are_refused_in_supported_encodings(self):
        for encoding in ("utf-8", "utf-16", "utf-16-be", "iso-8859-1"):
            for attributes in ('href="&img;"', 'xmlns:g="&img;" href="image"'):
                with self.subTest(encoding=encoding, attributes=attributes):
                    declared = "UTF-16" if encoding == "utf-16-be" else encoding
                    xml = ('<?xml version="1.0" encoding="' + declared + '"?>'
                           '<!DOCTYPE article SYSTEM "unread.dtd"><article><body><p>Result.</p>'
                           '<fig><graphic ' + attributes + '/></fig></body></article>')
                    result = fulltext.extract(xml.encode(encoding), "application/xml")
                    self.assertEqual(result["status"], "xml_external_entity")
                    self.assertIsNone(result["text"])

    def test_g6_graphic_deduplication_does_not_search_the_ordered_list(self):
        class OrderedNames(list):
            def __contains__(self, value):
                raise AssertionError("Graphic deduplication must use a set")

        article = fulltext._JatsArticle()
        article.visual_asset_names = OrderedNames()
        article.parse(b'<article><body><p>Result.</p><fig><graphic href="a"/>'
                      b'<graphic href="b"/><graphic href="a"/></fig></body></article>')
        self.assertEqual(article.visual_asset_names, ["a", "b"])

    def test_g6_parser_limits_return_distinct_pending_statuses(self):
        xml = b'<article><body><p>Result.</p><fig><graphic href="a"/></fig></body></article>'
        for name, limit, status in (("JATS_MAX_ELEMENTS", 3, "xml_element_limit"),
                                    ("JATS_MAX_DEPTH", 2, "xml_depth_limit"),
                                    ("JATS_MAX_TEXT_CHARACTERS", 3, "xml_text_limit")):
            with self.subTest(limit=name), mock.patch.object(fulltext, name, limit, create=True):
                result = fulltext.extract(xml, "application/xml")
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["text"])
        with mock.patch.object(fulltext, "JATS_TIMEOUT_SECONDS", 1, create=True), \
                mock.patch.object(fulltext.time, "monotonic", side_effect=[0, 2, 3, 4]):
            self.assertEqual(fulltext.extract(xml, "application/xml")["status"], "xml_timeout")

    def test_literal_markup_and_predefined_entities_still_extract(self):
        for xml in (b'<article><body><p><![CDATA[<graphic href="&img;"/>]]> &amp; &#65;</p>'
                    b'<fig><graphic href="a&amp;b"/></fig></body></article>',
                    b'<!DOCTYPE article [<!ATTLIST graphic href CDATA "a&amp;b">]>'
                    b'<article><body><p>Result</p><fig><graphic/></fig></body></article>'):
            with self.subTest(xml=xml):
                result = fulltext.extract(xml, "application/xml")
                self.assertEqual(result["status"], "extracted")
                self.assertEqual(result["visual_asset_names"], ["a&b"])


class FormatOnlyClosureTests(DevelopmentCase):
    def require_closed_conversion(self, status):
        self.prepared_study()
        plan, execution = self.run_cycle()
        self.mutate(development.assess_cycle, self.assessment(plan, execution))
        if status != "complete":
            cycle = self.store.snapshot()["records"]["cycle"][plan["id"]]

            def retain_closed_cycle(transaction):
                transaction.put("cycle", plan["id"], dict(cycle, status=status))

            self.store.mutate("test.closed-cycle", {}, retain_closed_cycle, expected_revision=self.store.revision,
                              request_id="closed-" + status)
        before = self.store.snapshot()["records"]
        original = before["execution_admission"][execution["origin"]["admission_id"]]
        payload = {key: copy.deepcopy(original[key]) for key in
                   ("cycle_id", "plan_digest", "command", "reserved_units")}
        payload.update(id="conversion", format_only_of=execution["id"])
        admission = self.mutate(development.admit_execution, payload)["result"]
        self.assertEqual(self.store.snapshot()["records"]["cycle"][plan["id"]], before["cycle"][plan["id"]])
        self.mutate(development.record_execution, self.execution(admission))
        after = self.store.snapshot()["records"]
        self.assertEqual(after["cycle"][plan["id"]], before["cycle"][plan["id"]])
        self.assertEqual(after["cycle_assessment"], before["cycle_assessment"])
        self.assert_error("cycle_closed", lambda: self.admit(identifier="ordinary-after-conversion"))

    def test_g1_conversion_keeps_a_complete_cycle_closed_to_ordinary_admission(self):
        self.require_closed_conversion("complete")

    def test_g1_conversion_keeps_a_failed_cycle_closed(self):
        self.require_closed_conversion("failed")

    def test_g1_conversion_keeps_a_budget_paused_cycle_closed(self):
        self.require_closed_conversion("budget_paused")

    def test_g9_identical_malformed_json_needs_no_json_equality(self):
        artifact = self.artifacts.put(b'{"malformed":', "application/json")
        output = {"id": "unused", "requirement_id": "checks", "artifact": artifact}
        earlier = {"id": "original", "payload": {"outputs": [output]}}
        development._validate_format_only_outputs(self.artifacts, earlier, {"outputs": [copy.deepcopy(output)]})
        changed = dict(output, artifact=self.artifacts.put(b'{"different":', "application/json"))
        with self.assertRaises(ResearchError) as raised:
            development._validate_format_only_outputs(self.artifacts, earlier, {"outputs": [changed]})
        self.assertEqual(raised.exception.code, "format_only_output_mismatch")

    def test_g9_renamed_output_still_covers_equivalent_earlier_json_serializations(self):
        first = {"id": "a", "requirement_id": "checks",
                 "artifact": self.artifacts.put(b'{"a":1}', "application/json")}
        second = dict(first, id="b", artifact=self.artifacts.put(b'{ "a": 1 }', "application/json"))
        earlier = {"id": "original", "payload": {"outputs": [first, second]}}
        development._validate_format_only_outputs(self.artifacts, earlier,
                                                   {"outputs": [dict(first, id="renamed")]})
