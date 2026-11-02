"""Render joins inspect each inventory once and listing hashes use bounded reads."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from search_controller import output_views, render, strategy_refresh
from search_controller.model import apply_event, initial_state
from tests.search_fixtures import contract, digest, proposal, review


class CountedDict(dict):
    def __init__(self, value):
        super().__init__(value)
        self.traversals = 0

    def items(self):
        self.traversals += 1
        return super().items()

    def values(self):
        self.traversals += 1
        return super().values()


class CountedList(list):
    def __init__(self, value):
        super().__init__(value)
        self.traversals = 0
        self.membership_checks = 0

    def __iter__(self):
        self.traversals += 1
        return super().__iter__()

    def __contains__(self, value):
        self.membership_checks += 1
        return super().__contains__(value)


class RenderScalingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "attack"
        self.root.mkdir()
        self.state = initial_state(contract(), "objective-000001")

    def test_many_node_markdown_prepares_status_and_inventories_once(self):
        candidate = proposal()
        approval = review(candidate)
        for sequence, (kind, payload) in enumerate([
                ("proposal_recorded", {"proposal": candidate, "digest": digest(candidate)}),
                ("review_recorded", {"proposal_id": "proposal-000001", "review": approval,
                                     "digest": digest(approval)}),
                ("proposal_admitted", {"proposal_id": "proposal-000001"})], start=1):
            self.state = apply_event(self.state, {"sequence": sequence, "request_id": str(sequence),
                                                 "kind": kind, "payload": payload})
        template = self.state["nodes"]["node-000001"]
        self.state["nodes"] = {}
        for number in range(24):
            identity = "node-{:06d}".format(number + 1)
            node = dict(copy.deepcopy(template), id=identity, attack_slug="attempt-{:02d}".format(number))
            self.state["nodes"][identity] = node
            (self.root / node["attack_slug"]).mkdir()
        identities = list(self.state["nodes"])
        self.state["control"]["deferred_node_ids"] = identities[::5]
        for number in range(73):
            identity = "checkpoint-{:06d}".format(number + 1)
            self.state["checkpoints"][identity] = {
                "id": identity, "origin": {"node_id": identities[number % 24] if number < 72 else None},
                "what_changed": "Recorded step {}".format(number), "remaining_obligation_ids": ["obligation-000001"],
                "next_hypothesis": "Continue step {}".format(number + 1)}
        policy = self.state["control"]["strategy_refresh"]
        policy["enabled"] = True
        for number in range(5):
            rows = [{"node_id": identities[index], "strategy": "induction", "disposition": "continue",
                     "reason": "Checked round {}".format(number), "next_action": "Inspect the recurrence",
                     "evidence_ids": ["checkpoint-000001"]} for index in range(number % 2, 24, 3)]
            if number == 4:
                rows = []
            elif rows:
                rows.append(dict(rows[0], strategy="comparison"))
            policy["assessments"].append({
                "id": "assessment-{:06d}".format(number + 1),
                "predecessor_id": "assessment-{:06d}".format(number) if number else None,
                "digest": digest(number), "review": {"round": number},
                "assessment": {"assessments": rows, "context_digest": digest([number]),
                               "what_changed": "Round {}".format(number), "remaining_obligation_ids": [],
                               "strategy_order": ["induction", "comparison"], "considered_failure_ids": [],
                               "next_hypotheses": [{"statement": "The recurrence holds", "success_criterion": "All cases",
                                                    "failure_signal": "A negative coefficient"}],
                               "no_new_hypothesis_reason": "The previous hypothesis remains" if number % 2 else None}})
        policy["failures"] = {"failure-{}".format(number): {"node_id": identities[number % 24],
                                                          "observation": "Failure {}".format(number)}
                              for number in range(31)}
        # Context construction has its own evidence traversal; count only the Markdown preparation below.
        status = strategy_refresh.assessment_status(self.state)
        checkpoints = self.state["checkpoints"] = CountedDict(self.state["checkpoints"])
        assessments = policy["assessments"] = CountedList(policy["assessments"])
        rows = [record["assessment"] for record in assessments]
        for value in rows:
            value["assessments"] = CountedList(value["assessments"])
        assessments.traversals = 0
        failures = policy["failures"] = CountedDict(policy["failures"])
        written = []
        with mock.patch.object(strategy_refresh, "assessment_status", return_value=status) as assess, \
                mock.patch.object(render, "replace_text", side_effect=lambda path, text: written.append(
                    [path.relative_to(self.root).as_posix(), text])):
            render.render(self.root, self.state)
        self.assertEqual(len(written), 25)
        # Frozen from the unchanged renderer, including file order and every byte of all 25 documents.
        self.assertEqual(digest(written), "bfb0aff5e5727f355ec62625917418dd56e5588f28e12c2f3231d1d2a6e1ecf8")
        counts = {"status": assess.call_count, "checkpoints": checkpoints.traversals,
                  "assessments": assessments.traversals, "failures": failures.traversals}
        counts.update({"rows-{}".format(index): value["assessments"].traversals for index, value in enumerate(rows)})
        for name, count in counts.items():
            with self.subTest(inventory=name):
                self.assertEqual(count, 1)

    def test_proposal_and_import_joins_visit_each_inventory_once_in_recorded_order(self):
        state = self.state
        for number in range(24):
            identity = "proposal-{:06d}".format(number + 1)
            state["proposals"][identity] = {"id": identity, "status": "admitted" if number % 2 else "proposed"}
            for suffix in ("first", "second"):
                node_id = "node-{}-{}".format(number, suffix)
                state["nodes"][node_id] = {"id": node_id, "proposal_id": identity}
            for index in (2, 0, 1):
                review_id = "review-{}-{}".format(number, index)
                state["reviews"][review_id] = {"id": review_id, "proposal_id": identity,
                                               "record": {"round": index}}
            state["service"]["imports"][identity] = {"id": identity}
        state["nodes"]["orphan"] = {"id": "orphan", "proposal_id": None}
        state["reviews"]["orphan"] = {"id": "orphan", "proposal_id": "missing", "record": {}}
        for index in (3, 1, 2):
            for identity in reversed(list(state["proposals"]) + ["missing"]):
                state["service"]["import_versions"].append({"import_id": identity, "version": index})
                state["service"]["adoption_allowances"].append({"import_id": identity, "allowance": index})
        inventories = []
        for name in ("proposals", "nodes", "reviews"):
            state[name] = CountedDict(state[name])
            inventories.append(state[name])
        service = state["service"]
        service["imports"] = CountedDict(service["imports"])
        inventories.append(service["imports"])
        for name in ("import_versions", "adoption_allowances"):
            service[name] = CountedList(service[name])
            inventories.append(service[name])
        views = output_views.Views(self.root, None, {"events": []}, state)
        output_views.build_admissions(views)
        output_views.build_imports(views)
        self.assertEqual(len(views.files), 48)
        self.assertEqual(digest([[path, raw.decode("utf-8"), entry] for path, (raw, entry) in views.files.items()]),
                         "9cb6dcd5d10cd8ff6a951353a988772cacbf9478a61086fd3d000944af8a8894")
        self.assertEqual([inventory.traversals for inventory in inventories], [1] * len(inventories))

    def test_adoptions_use_set_membership_and_retain_first_occurrence_order(self):
        views = output_views.Views(self.root, None, {"events": []}, self.state)
        adoptions = views.adoptions = CountedList([])
        first = {"subject_digest": "a" * 64, "reviewer": {"attestation_id": "review-assignment:first"},
                 "decision": "approve", "findings": {}}
        second = dict(first, reviewer={"attestation_id": "review-assignment:second"})
        for _ in range(10):
            views.adopt([first, second, first], "import", "import-1", "current", "imports/1.json")
        views.adopt(first, "import", "import-2", "current", "imports/2.json")
        expected = [{"review_id": digest(item), "assignment_id": assignment,
                     "adopting_record": {"kind": "import", "id": identity, "status": "current", "path": path}}
                    for item, assignment, identity, path in [(first, "first", "import-1", "imports/1.json"),
                                                             (second, "second", "import-1", "imports/1.json"),
                                                             (first, "first", "import-2", "imports/2.json")]]
        self.assertEqual(adoptions, expected)
        self.assertEqual(adoptions.membership_checks, 0)

    def test_large_unit_listing_reads_bounded_chunks_and_keeps_exact_file_descriptions(self):
        workspace = self.root / "attempt"
        units = workspace / "units"
        units.mkdir(parents=True)
        target = units / "large.bin"
        body = bytes(range(256)) * (12 * 1024) + b"tail"
        target.write_bytes(body)
        journal_lines = [b'{"move":1,"strategy":"induction","entry":"first"}', b'not JSON', b'[]']
        journal = b"\r\n \r" + b"\r\n".join(journal_lines) + b"\n"
        (workspace / "journal.jsonl").write_bytes(journal)
        (units / "FINISHED.json").write_bytes(b'{"complete":false}\n')
        (workspace / "HANDOFF.md").write_bytes(b"Continue the recurrence.\n")
        self.state["nodes"]["node-000001"] = {"id": "node-000001", "attack_slug": "attempt"}
        reads = []
        original_open, original_read_bytes = Path.open, Path.read_bytes
        case = self

        class BoundedReader:
            def __enter__(self):
                self.stream = original_open(target, "rb")
                return self

            def read(self, size=-1):
                case.assertGreater(size, 0, "Listing hashes must not request the complete file")
                case.assertLessEqual(size, 1024 * 1024)
                reads.append(size)
                return self.stream.read(size)

            def __exit__(self, *args):
                self.stream.close()

        def open_file(path, *args, **kwargs):
            return BoundedReader() if path == target else original_open(path, *args, **kwargs)

        def read_bytes(path):
            case.assertNotEqual(path, target, "Listing-only artifacts must not call read_bytes")
            return original_read_bytes(path)

        views = output_views.Views(self.root, None, {"events": []}, self.state)
        with mock.patch.object(Path, "open", open_file), mock.patch.object(Path, "read_bytes", read_bytes):
            output_views.build_nodes(views)
        value = json.loads(views.files["intermediate/math/attack/nodes/attempt/index.json"][0])
        self.assertEqual(value["units"], [
            {"path": "attack/attempt/units/large.bin", "digest": hashlib.sha256(body).hexdigest(), "size": len(body)},
            {"path": "attack/attempt/HANDOFF.md", "digest": hashlib.sha256(b"Continue the recurrence.\n").hexdigest(),
             "size": 25}])
        self.assertEqual(value["finish"], {"path": "attack/attempt/units/FINISHED.json",
                                         "digest": hashlib.sha256(b'{"complete":false}\n').hexdigest(), "size": 19})
        self.assertEqual(value["journal"], {"path": "attack/attempt/journal.jsonl",
                                           "digest": hashlib.sha256(journal).hexdigest(), "size": len(journal),
                                           "moves": [output_views.describe_move(line) for line in journal_lines]})
        self.assertGreaterEqual(len(reads), 4)
        self.assertEqual(target.read_bytes(), body)


if __name__ == "__main__":
    unittest.main()
