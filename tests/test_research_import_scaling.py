"""Import admission and coverage stay bounded without losing captured results."""

import copy
from contextlib import contextmanager
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from literature_fixtures import LiteratureCase
from research_harness import imports, literature, search_pages
from research_harness.acquisition import import_response
from research_harness.errors import ResearchError


class CountingBytes(bytes):
    def decode(self, *args, **kwargs):
        self.decodes += 1
        return super().decode(*args, **kwargs)


class CountingPointer(str):
    def startswith(self, *args, **kwargs):
        self.operations[0] += 1
        return super().startswith(*args, **kwargs)


class CountingPosition(int):
    def __eq__(self, other):
        self.operations[0] += 1
        return super().__eq__(other)

    __hash__ = int.__hash__


class CountingQuery(str):
    def __eq__(self, other):
        self.operations[0] += 1
        return super().__eq__(other)

    __hash__ = str.__hash__


class ImportInputTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("research_harness.import_io"), "Bounded import input helpers are missing")
        self.io = importlib.import_module("research_harness.import_io")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def assert_oversized(self, action, kind, limit):
        with self.assertRaises(ResearchError) as raised:
            action()
        self.assertEqual(raised.exception.code, "import_input_too_large")
        self.assertEqual(raised.exception.details["input_kind"], kind)
        self.assertEqual(raised.exception.details["limit_bytes"], limit)
        self.assertGreater(raised.exception.details["observed_bytes_at_least"], limit)

    def test_oversized_regular_response_and_payload_files_are_refused(self):
        path = self.root / "input.json"
        for kind in ("response", "payload"):
            with self.subTest(input_kind=kind):
                with path.open("wb") as stream:
                    stream.truncate(8 * 1024 * 1024 + 1)
                self.assert_oversized(lambda: self.io.read_import_input(path, input_kind=kind), kind, 8 * 1024 * 1024)

    def test_file_growth_is_caught_while_reading_under_a_byte_bound(self):
        path = self.root / "growing.json"
        path.write_bytes(b"12345678")
        fstat = os.fstat
        fdopen = os.fdopen
        retained = [0]

        def grow_after_stat(descriptor):
            info = fstat(descriptor)
            with path.open("ab") as stream:
                stream.write(b"x" * 1048576)
            return info

        @contextmanager
        def checked_reader(*args, **kwargs):
            with fdopen(*args, **kwargs) as source:
                def read(size=-1):
                    self.assertGreater(size, 0, "Import reads must have a byte bound")
                    self.assertLessEqual(size, min(65536, 9 - retained[0]))
                    block = source.read(size)
                    retained[0] += len(block)
                    return block
                yield SimpleNamespace(read=read)

        with mock.patch.object(self.io.os, "fstat", side_effect=grow_after_stat), \
                mock.patch.object(self.io.os, "fdopen", side_effect=checked_reader):
            self.assert_oversized(lambda: self.io.read_import_input(path, input_kind="response", limit_bytes=8), "response", 8)
        self.assertEqual(retained[0], 9)

    def test_boundary_and_zero_byte_allowances(self):
        path = self.root / "input"
        for body, limit in ((b"", 0), (b"12345678", 8), (b"x" * 131073, 131073)):
            path.write_bytes(body)
            self.assertEqual(self.io.read_import_input(path, input_kind="response", limit_bytes=limit), body)
        path.write_bytes(b"x")
        self.assert_oversized(lambda: self.io.read_import_input(path, input_kind="payload", limit_bytes=0), "payload", 0)

    def test_workspace_response_reads_keep_missing_and_nonregular_input_refusals(self):
        (self.root / 'directory').mkdir()
        os.mkfifo(self.root / 'fifo')
        for name in ('missing', 'directory', 'fifo'):
            with self.subTest(path=name):
                with self.assertRaises(ResearchError) as caught:
                    self.io.read_import_input(name, input_kind='response', workspace_root=self.root)
                self.assertEqual(caught.exception.code, 'invalid_input')

    def test_directories_and_fifos_are_refused_as_nonregular_files(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (self.root, fifo):
            with self.subTest(path=path), self.assertRaises(ResearchError) as raised:
                self.io.read_import_input(path, input_kind="response")
            self.assertEqual(raised.exception.code, "invalid_input")

    def test_opened_descriptor_is_used_after_path_replacement(self):
        path = self.root / "input"
        path.write_bytes(b"original")
        fstat = os.fstat

        def replace_after_stat(descriptor):
            info = fstat(descriptor)
            path.unlink()
            path.write_bytes(b"different")
            return info

        with mock.patch.object(self.io.os, "fstat", side_effect=replace_after_stat):
            self.assertEqual(self.io.read_import_input(path, input_kind="response"), b"original")


class MappedAdmissionTests(unittest.TestCase):
    def test_json_is_decoded_once_and_keeps_the_normalized_result(self):
        data = CountingBytes(b'\xef\xbb\xbf{"id":"https://example.org/paper","title":"A saved result"}')
        data.decodes = 0
        page = imports.parse_mapped(data, "application/json", [{"id": "/id", "title": "/title"}])
        self.assertEqual([(w["id"], w["title"]) for w in page.works],
                         [("url:https://example.org/paper", "A saved result")])
        self.assertEqual(data.decodes, 1)

    def test_json_requires_utf8_even_when_the_parser_recognizes_another_encoding(self):
        document = '{"id":"https://example.org/paper","title":"A saved result"}'
        for encoding in ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
            with self.subTest(encoding=encoding):
                with self.assertRaises(ResearchError) as raised:
                    imports.parse_mapped(document.encode(encoding), "application/json", [{"id": "/id", "title": "/title"}])
                self.assertEqual(raised.exception.code, "invalid_import")

    def test_text_mappings_still_use_unicode_character_spans(self):
        page = imports.parse_mapped("https://example.org/paper\nCaf\u00e9".encode(), "text/plain",
                                    [{"id": {"start": 0, "end": 25}, "title": {"start": 26, "end": 30}}])
        self.assertEqual(page.works[0]["title"], "Caf\u00e9")


class MappedCoverageTests(LiteratureCase):
    def capture_results(self, count, pointer="/results", mapping_indices=None, nested_id=False):
        rows = [{"id": "https://example.org/paper/" + str(n), "title": "Result " + str(n)} for n in range(count)]
        if nested_id:
            rows = [{"meta": row, "title": row["title"]} for row in rows]
        document = rows
        for part in reversed(pointer.split("/")[1:] if pointer else []):
            document = {part.replace("~1", "/").replace("~0", "~"): document}
        mappings = [{"id": pointer + "/%d/" % n + ("meta/id" if nested_id else "id"),
                     "title": pointer + "/%d/title" % n} for n in (range(count) if mapping_indices is None else mapping_indices)]
        self.sequence += 1
        result = import_response(self.store, "mcp", json.dumps(document).encode(),
            source_url="https://example.org/search?q=bounded", captured_at="2026-09-07T12:00:00Z",
            media_type="application/json", mappings=mappings,
            expected_revision=self.store.revision, request_id="mapped-" + str(self.sequence))
        response = {"source_id": result["source_ids"][0], "query": "bounded", "results_pointer": pointer}
        return self.store.snapshot()["records"], response, result["work_ids"]

    def test_pointer_coverage_operations_grow_linearly(self):
        for count in (32, 128):
            with self.subTest(count=count):
                records, response, expected = self.capture_results(count)
                operations = [0]
                imported = next(r for r in records["source_import"].values() if r["source_id"] == response["source_id"])
                for mapping in imported["mappings"]:
                    pointer = CountingPointer(mapping["id"])
                    pointer.operations = operations
                    mapping["id"] = pointer
                _, found, pending, page = literature._search_response(records, self.artifacts, response, "saved query")
                self.assertEqual(found, expected)
                self.assertEqual((pending, page), ([], None))
                self.assertLessEqual(operations[0], 2 * count)

    def test_escaped_prefix_root_array_and_nested_id_paths_preserve_every_result(self):
        for pointer, nested in (("/a~1b~0c/results", False), ("", False), ("/results", True)):
            with self.subTest(pointer=pointer, nested=nested):
                records, response, expected = self.capture_results(12, pointer, nested_id=nested)
                _, found, pending, _ = literature._search_response(records, self.artifacts, response, "saved query")
                self.assertEqual(found, expected)
                self.assertEqual(pending, [])

    def test_duplicate_mappings_cannot_hide_a_missing_result_or_index_boundary(self):
        records, response, _ = self.capture_results(11, mapping_indices=[0, 10] + list(range(2, 11)))
        self.assert_error("invalid_search", lambda: literature._search_response(records, self.artifacts, response, "saved query"))

    def test_missing_mapping_and_noncanonical_index_are_refused(self):
        records, response, _ = self.capture_results(3)
        original = copy.deepcopy(records)
        imported = next(iter(records["source_import"].values()))
        imported["mappings"].pop()
        self.assert_error("invalid_search", lambda: literature._search_response(records, self.artifacts, response, "saved query"))
        records = original
        imported = next(iter(records["source_import"].values()))
        imported["mappings"][1]["id"] = "/results/01/id"
        self.assert_error("invalid_search", lambda: literature._search_response(records, self.artifacts, response, "saved query"))

    def test_adjacent_pointer_prefix_does_not_cover_the_results(self):
        records, response, _ = self.capture_results(2)
        imported = next(iter(records["source_import"].values()))
        imported["mappings"][1]["id"] = "/results-extra/1/id"
        self.assert_error("invalid_search", lambda: literature._search_response(records, self.artifacts, response, "saved query"))


class SearchCoverageScalingTests(unittest.TestCase):
    def build_capture(self, query, position, version):
        return {"query": query, "source": {"id": "source-" + str(position), "provider": "crossref"},
                "page": {"group": {"query": query, "parameters": [("sort", "relevance")]},
                         "position": position, "sha256": str(position)}, "found": [version], "returned_count": 1}

    def test_position_membership_is_linear_and_keeps_first_seen_page_order(self):
        for count in (32, 128):
            with self.subTest(count=count):
                operations = [0]
                captures = []
                for n in reversed(range(count)):
                    position = CountingPosition(n)
                    position.operations = operations
                    captures.append(self.build_capture("bounded", position, "work-" + str(n)))
                captures.append(captures[0])
                result = search_pages.build_search_coverage(["bounded"], 10, captures,
                    [{"id": "group", "query": "bounded", "complete": True, "returned_count": count}], [])
                self.assertLessEqual(operations[0], 3 * count)
                self.assertEqual(result, [{"query": "bounded", "requested_k": 10, "returned_count": count,
                    "inspected_count": count, "page_positions": list(reversed(range(count))), "provider": "crossref",
                    "ordering": {"sort": "relevance"}, "kind": "complete"}])

    def test_query_grouping_does_not_rescan_all_captures_and_groups(self):
        for count in (32, 128):
            with self.subTest(count=count):
                operations = [0]
                queries, captures, groups = [], [], []
                for n in range(count):
                    query = CountingQuery("query-" + str(n))
                    query.operations = operations
                    queries.append(str(query))
                    captures.append(self.build_capture(query, 0, "work-" + str(n)))
                    groups.append({"id": "group-" + str(n), "query": query, "complete": True, "returned_count": 1})
                result = search_pages.build_search_coverage(queries, 10, captures, groups, [])
                self.assertLessEqual(operations[0], 20 * count)
                self.assertEqual([r["query"] for r in result], queries)
                self.assertTrue(all((r["returned_count"], r["inspected_count"], r["kind"]) == (1, 1, "complete") for r in result))

    def test_repeated_pages_and_integrity_failures_keep_the_existing_classification(self):
        captures = [self.build_capture("bounded", n, "work-" + str(n)) for n in (7, 2, 4, 7)]
        group = {"id": "group", "query": "bounded", "complete": False, "returned_count": 3}
        incomplete = [{"code": "search_response_incomplete", "group_id": "group"}]
        ranked = search_pages.build_search_coverage(["bounded"], 3, captures, [group], incomplete)[0]
        self.assertEqual((ranked["returned_count"], ranked["inspected_count"], ranked["page_positions"], ranked["kind"]),
                         (3, 3, [7, 2, 4], "top_k"))
        broken = search_pages.build_search_coverage(["bounded"], 3, captures, [group],
            incomplete + [{"code": "search_page_conflict", "group_id": "group"}])[0]
        self.assertEqual(broken["kind"], "bounded_unordered")

    def test_integrity_failures_remain_isolated_to_their_query_group(self):
        captures = [self.build_capture(query, 0, "work-" + query) for query in ("first", "second")]
        groups = [{"id": "group-" + query, "query": query, "complete": False, "returned_count": 1}
                  for query in ("first", "second")]
        pending = [{"code": "search_response_incomplete", "group_id": group["id"]} for group in groups]
        pending.append({"code": "search_page_conflict", "group_id": "group-first"})
        result = search_pages.build_search_coverage(["first", "second"], 1, captures, groups, pending)
        self.assertEqual([(row["query"], row["kind"]) for row in result],
                         [("first", "bounded_unordered"), ("second", "top_k")])
