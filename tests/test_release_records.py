"""Check release records without network access or git mutations."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci" / "check_release_records.py"
CORRECTION = """## Correction (2026-11-04)

The section "The math-conjecturer skill" says that the page keeps each inline formula on one line. That statement is
not correct for a long formula. The page puts an inline formula and the text that touches it without a space into one
unit. If this unit fits on a line, the page moves it to the next line as one unit. If the unit is wider than a line,
the formula wraps inside the unit at the break points of the formula renderer.

The instruction to the agent does not change. A long definition or a long inequality goes into a display formula on
its own line.
"""
EMPTY_REPORT = {
    "tags_without_release": [],
    "notes_without_tag": [],
    "tags_without_note": [],
    "changelog_versions_without_note": [],
    "notes_without_changelog_version": [],
    "plugin_versions_without_note": [],
    "release_versions_without_tag": [],
    "release_versions_without_release": [],
}


class ReleaseRecordsAssertions(unittest.TestCase):
    def load_api(self):
        self.assertTrue(SCRIPT.is_file(), "The release-records check is not implemented")
        spec = importlib.util.spec_from_file_location("release_records", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def compare_records(self, versions, tags, releases, **kwargs):
        return self.load_api().compare_records(
            changelog_versions=set(versions), note_versions=set(versions),
            tag_names=tags, release_tag_names=releases,
            plugin_version=versions[-1] if versions else "0.73.0", **kwargs,
        )

    def assert_report(self, report, **differences):
        self.assertEqual(report, dict(EMPTY_REPORT, **differences))


class ReleaseRecordsTests(ReleaseRecordsAssertions):
    def test_0580_tag_without_github_release(self):
        report = self.compare_records(["0.58.0"], ["exactory--v0.58.0"], [])
        self.assert_report(report, tags_without_release=["0.58.0"])

    def test_0662_note_without_tag(self):
        report = self.compare_records(["0.66.2"], [], [])
        self.assert_report(report, notes_without_tag=["0.66.2"])

    def test_tags_before_first_note_still_require_releases(self):
        tags = ["exactory--v0.33.1", "exactory--v0.33.0", "exactory--v0.1.0"]
        report = self.compare_records(["0.33.1"], tags, ["exactory--v0.33.1"])
        self.assert_report(report, tags_without_release=["0.1.0", "0.33.0"])

    def test_tags_since_first_note_require_notes_in_numeric_version_order(self):
        versions = ["0.9.0", "0.11.0"]
        tags = ["exactory--v0.100.0", "exactory--v0.10.0", "exactory--v0.9.0",
                "exactory--v0.11.0", "exactory--v0.2.0"]
        report = self.compare_records(versions, tags, tags)
        self.assert_report(report, tags_without_note=["0.10.0", "0.100.0"])

    def test_current_missing_tags_have_the_reported_real_shape(self):
        versions = ["0.34.0", "0.34.2", "0.50.0", "0.55.1", "0.56.1"]
        self.assert_report(
            self.compare_records(versions, [], []), notes_without_tag=versions,
        )

    def test_current_missing_releases_have_the_reported_real_shape(self):
        versions = ["0.56.0", "0.58.0"]
        tags = ["exactory--v0.58.0", "exactory--v0.1.0",
                "exactory--v0.56.0", "exactory--v0.33.0"]
        self.assert_report(
            self.compare_records(versions, tags, []),
            tags_without_release=["0.1.0", "0.33.0", "0.56.0", "0.58.0"],
        )

    def test_requested_release_requires_tag_even_without_a_note(self):
        report = self.compare_records(
            ["0.72.4"], ["exactory--v0.72.4"],
            ["exactory--v0.72.4", "exactory--v0.73.0"], release="0.73.0",
        )
        self.assert_report(report, release_versions_without_tag=["0.73.0"])

    def test_requested_release_requires_github_release_even_without_a_tag(self):
        report = self.compare_records(
            ["0.72.4"], ["exactory--v0.72.4"], ["exactory--v0.72.4"],
            release="0.73.0",
        )
        self.assert_report(
            report, release_versions_without_tag=["0.73.0"],
            release_versions_without_release=["0.73.0"],
        )

    def test_draft_does_not_count_as_a_github_release(self):
        api = self.load_api()
        releases = api.parse_release_tag_names(json.dumps([
            {"tagName": "exactory--v0.58.0", "isDraft": True},
            {"tagName": "exactory--v0.57.0", "isDraft": False},
        ]))
        self.assertEqual(releases, {"exactory--v0.57.0"})
        self.assert_report(
            self.compare_records(
                ["0.58.0"], ["exactory--v0.58.0"], releases, release="0.58.0",
            ), tags_without_release=["0.58.0"],
            release_versions_without_release=["0.58.0"],
        )

    def test_changelog_and_note_differences_are_reported(self):
        api = self.load_api()
        report = api.compare_records(
            changelog_versions={"0.58.0", "0.60.0", "0.9.0"},
            note_versions={"0.58.0", "0.59.0", "0.10.0"},
            tag_names=["exactory--v" + version for version in
                       ("0.58.0", "0.59.0", "0.10.0")],
            release_tag_names=["exactory--v" + version for version in
                               ("0.58.0", "0.59.0", "0.10.0")],
            plugin_version="0.72.4",
        )
        self.assert_report(
            report, changelog_versions_without_note=["0.9.0", "0.60.0"],
            notes_without_changelog_version=["0.10.0", "0.59.0"],
            plugin_versions_without_note=["0.72.4"],
        )

    def test_parse_changelog_release_links_with_any_text(self):
        text = (
            "Version [0.58.0](docs/releases/0.58.0.md) describes a release.\n"
            "Version\n[0.39.0](docs/releases/0.39.0.md) is wrapped.\n"
            "See [0.55.0](docs/releases/0.55.0.md) for background.\n"
            "Read the [upgrade notes](docs/releases/0.42.1.md) before updating.\n"
            "Version [0.66.2](https://example.org/0.66.2.md) is external.\n"
        )
        self.assertEqual(
            self.load_api().parse_changelog_versions(text),
            {"0.39.0", "0.42.1", "0.55.0", "0.58.0"},
        )

    def test_note_linked_from_running_text_has_a_complete_record(self):
        api = self.load_api()
        changelog_versions = api.parse_changelog_versions(
            "Version 0.39.2 enforces the final decision; see the "
            "[release notes](docs/releases/0.39.2.md). "
            "[0.42.1](docs/releases/0.42.1.md) keeps the investigation current."
        )
        tags = ["exactory--v0.39.2", "exactory--v0.42.1"]
        self.assert_report(api.compare_records(
            changelog_versions=changelog_versions,
            note_versions={"0.39.2", "0.42.1"},
            tag_names=tags, release_tag_names=tags, plugin_version="0.42.1",
        ))

    def test_changelog_link_to_missing_note_is_reported(self):
        api = self.load_api()
        changelog_versions = api.parse_changelog_versions(
            "Version [0.58.0](docs/releases/0.58.0.md) is available. "
            "Read the [upgrade notes](docs/releases/0.73.0.md) before updating."
        )
        self.assert_report(
            api.compare_records(
                changelog_versions=changelog_versions, note_versions={"0.58.0"},
                tag_names=["exactory--v0.58.0"],
                release_tag_names=["exactory--v0.58.0"], plugin_version="0.58.0",
            ), changelog_versions_without_note=["0.73.0"],
        )

    def test_parse_note_filenames_ignores_non_version_documents(self):
        self.assertEqual(
            self.load_api().parse_note_versions(
                ["0.58.0.md", "0.66.2.md", "README.md", "0.73.0.txt"],
            ), {"0.58.0", "0.66.2"},
        )

    def test_complete_records_give_empty_report(self):
        tags = ["exactory--v0.58.0", "exactory--v0.66.2"]
        self.assert_report(self.compare_records(
            ["0.58.0", "0.66.2"], tags, tags, release="0.66.2",
        ))

    def test_no_notes_does_not_invent_a_first_note(self):
        self.assert_report(
            self.compare_records([], ["exactory--v0.1.0"], []),
            tags_without_release=["0.1.0"], plugin_versions_without_note=["0.73.0"],
        )

    def test_other_tag_names_are_not_plugin_releases(self):
        tags = ["exactory--v0.58.0", "another--v1.0.0", "v0.59.0"]
        self.assert_report(self.compare_records(["0.58.0"], tags, tags))


class ReleaseRecordsCLITests(ReleaseRecordsAssertions):
    def setUp(self):
        self.load_api()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        (self.root / "docs/releases").mkdir(parents=True)
        (self.root / "docs/releases/0.58.0.md").write_text(
            "# Exactory 0.58.0\n", encoding="utf-8",
        )
        (self.root / "CHANGELOG.md").write_text(
            "Version [0.58.0](docs/releases/0.58.0.md) describes a release.\n",
            encoding="utf-8",
        )
        (self.root / ".claude-plugin").mkdir()
        (self.root / ".claude-plugin/plugin.json").write_text(
            json.dumps({"version": "0.58.0"}), encoding="utf-8",
        )
        self.tags_path = self.root / "tags.txt"
        self.tags_path.write_text("exactory--v0.58.0\n", encoding="utf-8")
        self.releases_path = self.root / "releases.json"
        self.releases_path.write_text(json.dumps([
            {"tagName": "exactory--v0.58.0", "isDraft": False},
        ]), encoding="utf-8")
        self.bin_path = self.root / "bin"
        self.bin_path.mkdir()

    def run_script(self, *args, use_files=True):
        arguments = [sys.executable, str(SCRIPT), str(self.root)]
        if use_files:
            arguments.extend([
                "--tags-file", str(self.tags_path),
                "--releases-file", str(self.releases_path),
            ])
        environment = dict(os.environ, PATH=str(self.bin_path), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            arguments + list(args), capture_output=True, text=True, env=environment,
        )

    def write_stub(self, name, body):
        path = self.bin_path / name
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(0o755)

    def test_complete_records_exit_zero_with_json_only(self):
        result = self.run_script("--release", "0.58.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), EMPTY_REPORT)
        self.assertEqual(result.stderr, "")

    def test_incomplete_records_exit_one_with_json(self):
        self.releases_path.write_text("[]", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_report(json.loads(result.stdout), tags_without_release=["0.58.0"])

    def test_release_option_reports_missing_tag_and_release(self):
        result = self.run_script("--release", "0.73.0")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_report(
            json.loads(result.stdout), release_versions_without_tag=["0.73.0"],
            release_versions_without_release=["0.73.0"],
        )

    def test_failing_gh_exits_two_and_explains_unreadable_input(self):
        self.write_stub("gh", "echo 'gh: authentication required' >&2\nexit 7")
        result = self.run_script("--tags-file", str(self.tags_path), use_files=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("gh", result.stderr)
        self.assertIn("authentication required", result.stderr)

    def test_missing_gh_exits_two(self):
        result = self.run_script("--tags-file", str(self.tags_path), use_files=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("gh", result.stderr)

    def test_default_commands_only_list_tags_and_releases(self):
        self.write_stub("git", (
            '[ "$1" = "-C" ] && [ "$2" = "' + str(self.root) + '" ] && '
            '[ "$3" = "tag" ] && [ "$4" = "-l" ] && '
            '[ "$5" = "exactory--v*" ] && [ "$#" = 5 ] || exit 9\n'
            "echo exactory--v0.58.0"
        ))
        self.write_stub("gh", (
            '[ "$*" = "release list --repo exactory/exactory-client --limit 1000 '
            '--json tagName,isDraft" ] || exit 9\n'
            "echo '[{\"tagName\":\"exactory--v0.58.0\",\"isDraft\":false}]'"
        ))
        result = self.run_script(use_files=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), EMPTY_REPORT)

    def test_unreadable_repository_inputs_exit_two(self):
        for relative_path in ("CHANGELOG.md", "docs/releases", ".claude-plugin/plugin.json"):
            path = self.root / relative_path
            renamed = path.with_name(path.name + ".unavailable")
            with self.subTest(path=relative_path):
                path.rename(renamed)
                try:
                    result = self.run_script()
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(relative_path, result.stderr)
                finally:
                    renamed.rename(path)

    def test_unreadable_supplied_tag_file_exits_two(self):
        self.tags_path.unlink()
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("tags.txt", result.stderr)

    def test_unreadable_supplied_release_file_exits_two(self):
        self.releases_path.unlink()
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("releases.json", result.stderr)

    def test_unreadable_note_file_exits_two_instead_of_reporting_absence(self):
        path = self.root / "docs/releases/0.58.0.md"
        path.unlink()
        path.mkdir()
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("0.58.0.md", result.stderr)

    def test_failing_git_exits_two_with_the_listing_error(self):
        self.write_stub("git", "echo 'not a git repository' >&2\nexit 128")
        result = self.run_script(
            "--releases-file", str(self.releases_path), use_files=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("not a git repository", result.stderr)

    def test_draft_release_exits_one(self):
        self.releases_path.write_text(json.dumps([
            {"tagName": "exactory--v0.58.0", "isDraft": True},
        ]), encoding="utf-8")
        result = self.run_script("--release", "0.58.0")
        self.assertEqual(result.returncode, 1)
        self.assert_report(
            json.loads(result.stdout), tags_without_release=["0.58.0"],
            release_versions_without_release=["0.58.0"],
        )

    def test_invalid_requested_release_exits_two(self):
        result = self.run_script("--release", "latest")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("latest", result.stderr)

    def test_malformed_release_inputs_exit_two(self):
        for contents in ("not json", "{}", '[{"tagName":"exactory--v0.58.0"}]',
                         '[{"tagName":"exactory--v0.58.0","isDraft":"false"}]'):
            with self.subTest(contents=contents):
                self.releases_path.write_text(contents, encoding="utf-8")
                result = self.run_script()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("release", result.stderr.lower())

    def test_invalid_manifest_inputs_exit_two(self):
        for contents in ("not json", "[]", "{}", '{"version":"latest"}'):
            with self.subTest(contents=contents):
                (self.root / ".claude-plugin/plugin.json").write_text(
                    contents, encoding="utf-8",
                )
                result = self.run_script()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("plugin.json", result.stderr)

    def test_script_leaves_all_input_files_unchanged(self):
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        self.run_script()
        after = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)


class RepositoryReleaseRecordsTests(unittest.TestCase):
    def test_repository_changelog_and_notes_match(self):
        api = ReleaseRecordsAssertions().load_api()
        changelog_versions = api.parse_changelog_versions(
            (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        )
        note_versions = api.read_note_versions(ROOT)
        self.assertEqual(
            changelog_versions, note_versions,
            "CHANGELOG links without notes: %s; notes without CHANGELOG links: %s"
            % (sorted(changelog_versions - note_versions, key=api.parse_version),
               sorted(note_versions - changelog_versions, key=api.parse_version)),
        )

    def test_0570_note_ends_with_supervisor_correction(self):
        text = (ROOT / "docs/releases/0.57.0.md").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n\n" + CORRECTION), "The dated correction is missing")


if __name__ == "__main__":
    unittest.main()
