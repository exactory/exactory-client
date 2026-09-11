"""Runtime provenance is reported and recorded, and never changes replay or gates."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from literature_fixtures import LiteratureCase


PLUGIN = Path(__file__).resolve().parents[1]


class ProvenanceTests(unittest.TestCase):
    def test_runtime_provenance_names_the_build(self):
        from research_harness.provenance import runtime_provenance
        value = runtime_provenance()
        self.assertEqual(set(value), {"plugin_version", "source_commit", "dirty", "executable", "package_digest",
                                      "constitution", "schema_version"})
        self.assertEqual(value["plugin_version"], json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())["version"])
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(len(value["package_digest"]), 64)
        self.assertEqual(value["constitution"]["version"], "1")
        self.assertEqual(len(value["constitution"]["sha256"]), 64)
        self.assertTrue(value["executable"].endswith("bin/exactory-research"))
        self.assertEqual(value, runtime_provenance())

    def test_git_fields_are_null_outside_a_checkout(self):
        from research_harness.provenance import runtime_provenance
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            (root / ".claude-plugin").mkdir(parents=True)
            (root / ".claude-plugin/plugin.json").write_text('{"version": "9.9.9"}')
            (root / "research_harness").mkdir()
            (root / "bin").mkdir()
            (root / "RESEARCH_CONSTITUTION.md").write_text("Version: 7\n")
            value = runtime_provenance(root)
        self.assertEqual((value["plugin_version"], value["source_commit"], value["dirty"]), ("9.9.9", None, None))
        self.assertEqual(value["constitution"]["version"], "7")

    def test_http_client_sends_the_plugin_version(self):
        from research_fixtures import client
        from research_harness.provenance import plugin_version
        http, wire, _ = client([(200, {"Content-Type": "application/json"}, b"{}")])
        http.get("https://example.org/x", accept=("application/json",))
        self.assertEqual(wire.requests[0][1]["User-Agent"], "exactory-research/" + plugin_version())


class ProvenanceReceiptTests(LiteratureCase):
    def test_receipts_record_runtime_and_replay_ignores_runtime_differences(self):
        from research_harness import operations
        from research_harness.reading import record_reading
        a = self.metadata()
        payload = self.abstract_note(a)
        first = record_reading(self.store, payload, expected_revision=self.store.revision, request_id="same-request")
        receipt = self.store.snapshot()["records"]["literature_operation"]["same-request"]
        self.assertEqual(receipt["operation"], "literature.reading")
        self.assertEqual(len(receipt["runtime"]["package_digest"]), 64)
        with mock.patch.object(operations, "runtime_provenance", return_value={"plugin_version": "0.0.0-other"}):
            again = record_reading(self.store, payload, expected_revision=self.store.revision, request_id="same-request")
        self.assertEqual(first, again)

    def test_acquisition_admissions_record_runtime(self):
        a = self.metadata()
        records = self.store.snapshot()["records"]
        admission = next(iter(records["acquisition_operation"].values()))
        self.assertEqual(admission["runtime"]["schema_version"], 1)
        _ = a


if __name__ == "__main__":
    unittest.main()
