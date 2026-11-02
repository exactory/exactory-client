"""Invocation-local joins preserve packet bytes and evidence digests."""

import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from development_fixtures import DevelopmentCase
from research_harness import development, review_packets
from research_harness.evidence import digest


def build_baseline_source_closure(records, version_ids):
    # Unchanged builder from release/0.71.0 before D3-5.
    works, readings, bundles, source_ids = {}, {}, {}, set()
    for version in sorted(version_ids):
        work = records.get("work", {}).get(version)
        if work is None:
            continue
        works[version] = work
        source_ids.update(work.get("source_ids", []))
        for reading in records.get("reading", {}).values():
            if reading["version_id"] == version:
                readings[reading["id"]] = reading
                source_ids.update(i["link"]["source_id"] for i in reading["inspections"])
        for bundle in records.get("source_bundle", {}).values():
            if bundle["version_id"] == version:
                bundles[bundle["id"]] = bundle
                source_ids.update(u["link"]["source_id"] for u in bundle["units"] if u["link"] is not None)
    sources = {identifier: records["source"][identifier] for identifier in sorted(source_ids) if identifier in records.get("source", {})}
    return {"work": works, "reading": readings, "source_bundle": bundles, "source": sources}


def build_baseline_branch_inputs(context):
    # Unchanged builder from release/0.71.0 before D3-5.
    branches = {}
    for identifier, cycle in context.records.get("cycle", {}).items():
        plan = context.records["cycle_plan"][identifier]
        context.verify_plan_digest(plan)
        executions = {i: context.records["execution"][i] for i in cycle["execution_ids"]}
        for execution in executions.values():
            development._execution_artifacts(context.artifacts, execution["payload"])
        branches[identifier] = {"cycle": cycle, "plan": plan,
            "plan_evidence": development._plan_evidence(development._Evidence(context), plan["payload"]),
            "executions": executions, "assessment": context.assess_record(cycle["assessment_id"]) if cycle["assessment_id"] is not None else None,
            "admissions": {i: a for i, a in context.records.get("execution_admission", {}).items() if a["cycle_id"] == identifier},
            "checkpoints": {i: c for i, c in context.records.get("checkpoint", {}).items() if c["cycle_id"] == identifier}}
        probes = development.build_program_probe_inventory(context.records, context.artifacts, plan,
                                                           branches[identifier]["admissions"])
        if probes:
            branches[identifier]["program_probes"] = probes
        if branches[identifier]["assessment"] is not None:
            branches[identifier]["assessment"] = {k: v for k, v in branches[identifier]["assessment"].items()
                                                  if k != "prerequisites"}
    return branches


def encode_packet(value):
    # Delivery encoding also detects dictionary order changes, unlike digest().
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


class CountedTable(dict):
    def __init__(self, rows):
        super().__init__(rows)
        self.traversals = 0
        self.visits = 0

    def __iter__(self):
        self.traversals += 1
        for key in super().__iter__():
            self.visits += 1
            yield key

    def items(self):
        self.traversals += 1
        for item in super().items():
            self.visits += 1
            yield item

    def values(self):
        self.traversals += 1
        for value in super().values():
            self.visits += 1
            yield value


def build_source_records(version_count=10, row_count=100):
    versions = ["version-%02d" % n for n in reversed(range(version_count))]
    records = {"work": {v: {"id": v, "title": "Bound α", "source_ids": ["metadata", "absent"]}
                        for v in versions},
               "reading": {}, "source_bundle": {},
               "source": {s: {"id": s} for s in ("unrelated", "unit", "inspection", "metadata")}}
    for n in reversed(range(row_count)):
        version = versions[n % version_count] if n % 4 else "unselected"
        reading_id, bundle_id = "reading-%03d" % n, "bundle-%03d" % n
        records["reading"][reading_id] = {"id": reading_id, "version_id": version,
            "inspections": [{"link": {"source_id": "inspection"}}, {"link": {"source_id": "absent"}}]}
        records["source_bundle"][bundle_id] = {"id": bundle_id, "version_id": version,
            "units": [{"link": None}, {"link": {"source_id": "unit"}}, {"link": {"source_id": "metadata"}}]}
    return records


def build_branch_context(case, cycle_count=10, row_count=100):
    cycles = ["cycle-%02d" % n for n in reversed(range(cycle_count))]
    records = dict(case.store.snapshot()["records"])
    records.update({"cycle": {}, "cycle_plan": {}, "execution": {}, "execution_admission": {}, "checkpoint": {}})
    assessments = {}
    for n, identifier in enumerate(cycles):
        execution_id, assessment_id = "execution-" + identifier, "assessment-" + identifier if n % 2 else None
        records["cycle"][identifier] = {"id": identifier, "execution_ids": [execution_id], "assessment_id": assessment_id}
        records["cycle_plan"][identifier] = {"id": identifier, "digest": "plan-" + identifier,
            "payload": {"literature": {"sources": [case.links[0]]}, "inheritance": [], "reopening": []}}
        records["execution"][execution_id] = {"payload": {"id": execution_id, "command": None,
            "origin": {"kind": "managed"},
            "outputs": [{"artifact": case.artifacts.put(execution_id.encode(), "text/plain")}]}}
        if assessment_id is not None:
            assessments[assessment_id] = {"id": assessment_id, "evidence": [{"reference": {"kind": "result"}}],
                "prerequisites": {"workflow": ["retained privately"]}, "digest": "assessment-digest"}
    for n in reversed(range(row_count)):
        identifier = cycles[n % cycle_count] if n % 4 else "absent-cycle"
        admission_id, checkpoint_id = "admission-%03d" % n, "checkpoint-%03d" % n
        records["execution_admission"][admission_id] = {"id": admission_id, "cycle_id": identifier,
            "command": {"program": {"sha256": "program"}}}
        records["checkpoint"][checkpoint_id] = {"id": checkpoint_id, "cycle_id": identifier, "digest": "checkpoint-digest"}
    return SimpleNamespace(records=records, artifacts=development.Evaluation(records, case.artifacts),
                           foundation={}, verify_plan_digest=Mock(),
                           assess_record=Mock(side_effect=lambda identifier: assessments[identifier]))


class SourceIndexTests(unittest.TestCase):
    def test_source_join_traverses_each_table_once_as_selected_versions_increase(self):
        for version_count in (1, 10, 25):
            with self.subTest(version_count=version_count):
                records = build_source_records(version_count)
                versions = list(records["work"])
                expected = build_baseline_source_closure(records, versions)
                for name in ("reading", "source_bundle"):
                    records[name] = CountedTable(records[name])
                actual = review_packets._source_closure(records, versions)
                self.assertEqual(encode_packet(actual), encode_packet(expected))
                for name in ("reading", "source_bundle"):
                    with self.subTest(table=name):
                        self.assertEqual(records[name].traversals, 1, name)
                        self.assertEqual(records[name].visits, 100, name)

    def test_source_closure_preserves_order_links_and_missing_records(self):
        records = build_source_records()
        versions = ["version-09", "missing-work", "version-00", "version-05", "version-00"]
        for missing in ((), ("reading",), ("source_bundle", "source"), ("work",)):
            with self.subTest(missing=missing):
                selected = {key: value for key, value in records.items() if key not in missing}
                expected = build_baseline_source_closure(selected, versions)
                actual = review_packets._source_closure(selected, versions)
                self.assertEqual(encode_packet(actual), encode_packet(expected))
                self.assertEqual(digest(actual), digest(expected))
        actual = review_packets._source_closure(records, versions)
        self.assertEqual(list(actual["work"]), ["version-00", "version-05", "version-09"])
        self.assertEqual(list(actual["source"]), ["inspection", "metadata", "unit"])
        self.assertEqual(review_packets._source_closure(records, []), build_baseline_source_closure(records, []))

    def test_source_indexes_are_rebuilt_for_each_invocation(self):
        records = build_source_records()
        versions = list(records["work"])
        before = review_packets._source_closure(records, versions)
        records["reading"]["later"] = {"id": "later", "version_id": versions[0], "inspections": []}
        after = review_packets._source_closure(records, versions)
        self.assertNotIn("later", before["reading"])
        self.assertIn("later", after["reading"])
        self.assertEqual(encode_packet(after), encode_packet(build_baseline_source_closure(records, versions)))

    def test_manuscript_packet_bytes_and_digests_match_the_baseline(self):
        records = build_source_records()
        bundle = {"digest": "bundle", "files": {"pdf": {"path": "paper.pdf"}},
            "claim_evidence": [{"evidence": [{"kind": "source", "link": {"version_id": version}}
                                            for version in records["work"]]}],
            "review_inputs": {"synthesis": {"sections": {"standards": {"payload": {"field": "Bounds"}}},
                                             "foundation": {}}}, "execution_observations": {}}
        with patch.object(review_packets, "_source_closure", build_baseline_source_closure):
            expected = review_packets.manuscript_packet(records, bundle)
        actual = review_packets.manuscript_packet(records, bundle)
        self.assertEqual(encode_packet(actual), encode_packet(expected))
        self.assertEqual(actual["digest"], expected["digest"])
        self.assertEqual(digest(actual["evidence"]), digest(expected["evidence"]))


class BranchIndexTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()

    def test_branch_join_traverses_each_table_once_as_cycles_increase(self):
        for cycle_count in (1, 10, 25):
            with self.subTest(cycle_count=cycle_count):
                context = build_branch_context(self, cycle_count)
                expected = build_baseline_branch_inputs(context)
                for name in ("execution_admission", "checkpoint"):
                    context.records[name] = CountedTable(context.records[name])
                actual = development._branch_inputs(context)
                self.assertEqual(encode_packet(actual), encode_packet(expected))
                for name in ("execution_admission", "checkpoint"):
                    with self.subTest(table=name):
                        self.assertEqual(context.records[name].traversals, 1, name)
                        self.assertEqual(context.records[name].visits, 100, name)

    def test_branch_inputs_preserve_order_assessments_executions_and_probes(self):
        context = build_branch_context(self)
        first = next(iter(context.records["cycle"]))
        plan = context.records["cycle_plan"][first]
        plan["payload"]["prior_knowledge"] = {"probe_ids": ["probe"]}
        context.records["probe_intent"] = {"probe": {"program": {"sha256": "program"},
            "inputs": [], "interpreter": "python", "program_run": {"phase": "before plan"}}}
        context.records["probe"] = {"probe": {"program_run": {"outputs_unread": True}, "outputs": [],
            "stdout": {}, "stderr": {}, "exit_status": 0, "elapsed_seconds": 1}}
        expected = build_baseline_branch_inputs(context)
        context.verify_plan_digest.reset_mock()
        with patch.object(context.artifacts, "read", wraps=context.artifacts.read) as read:
            actual = development._branch_inputs(context)
        for execution in context.records["execution"].values():
            self.assertEqual(read.call_args_list.count(call(execution["payload"]["outputs"][0]["artifact"])), 1)
        self.assertEqual(encode_packet(actual), encode_packet(expected))
        self.assertEqual(digest(actual), digest(expected))
        self.assertEqual(list(actual), list(context.records["cycle"]))
        self.assertIn("program_probes", actual[first])
        self.assertEqual(context.verify_plan_digest.call_count, 10)
        self.assertTrue(any(branch["assessment"] is not None for branch in actual.values()))
        self.assertTrue(all(branch["assessment"] is None or "prerequisites" not in branch["assessment"]
                            for branch in actual.values()))
        self.assertTrue(all(row["cycle_id"] != "absent-cycle" for branch in actual.values()
                            for row in branch["checkpoints"].values()))

    def test_branch_inputs_preserve_empty_tables_and_missing_record_errors(self):
        for missing in ((), ("execution_admission",), ("checkpoint",), ("cycle",)):
            with self.subTest(missing=missing):
                context = build_branch_context(self)
                for name in missing:
                    context.records.pop(name)
                self.assertEqual(encode_packet(development._branch_inputs(context)),
                                 encode_packet(build_baseline_branch_inputs(context)))
        for kind in ("cycle_plan", "execution"):
            context = build_branch_context(self)
            context.records[kind].pop(next(iter(context.records[kind])))
            with self.assertRaises(KeyError) as expected:
                build_baseline_branch_inputs(context)
            with self.assertRaises(KeyError) as actual:
                development._branch_inputs(context)
            self.assertEqual(actual.exception.args, expected.exception.args)

    def test_branch_indexes_are_rebuilt_for_each_invocation(self):
        context = build_branch_context(self)
        before = development._branch_inputs(context)
        cycle_id = next(iter(context.records["cycle"]))
        context.records["checkpoint"]["later"] = {"id": "later", "cycle_id": cycle_id}
        after = development._branch_inputs(context)
        self.assertNotIn("later", before[cycle_id]["checkpoints"])
        self.assertIn("later", after[cycle_id]["checkpoints"])
        self.assertEqual(encode_packet(after), encode_packet(build_baseline_branch_inputs(context)))


class StudyPacketCompatibilityTests(DevelopmentCase):
    def test_readiness_packet_and_candidate_evidence_digests_match_the_baseline(self):
        self.prepared_candidate()
        self.save_checkpoint(identifier="retained-checkpoint", select=False)
        records = self.store.snapshot()["records"]
        retained = copy.deepcopy(records)
        with patch.object(development, "_branch_inputs", build_baseline_branch_inputs):
            expected = development.readiness_state(records, self.artifacts)
        actual = development.readiness_state(records, self.artifacts)
        self.assertIsNotNone(actual["candidate"])
        for key in ("digest", "branches_digest", "source_records_digest", "assessment_digest", "checkpoint_digest"):
            self.assertEqual(actual["candidate"][key], expected["candidate"][key], key)
        self.assertEqual(encode_packet(review_packets.readiness_packet(dict(actual, execution_observations={}))),
                         encode_packet(review_packets.readiness_packet(dict(expected, execution_observations={}))))
        self.assertEqual(records, retained)


if __name__ == "__main__":
    unittest.main()
