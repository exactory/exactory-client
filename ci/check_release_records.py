#!/usr/bin/env python3
"""Report missing release records without changing tags, releases, or files."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+"
TAG_PREFIX = "exactory--v"


def parse_version(version):
    """Return the numeric components used to order release versions."""
    if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError("Expected an X.Y.Z version, got %r" % (version,))
    return tuple(int(component) for component in version.split("."))


def parse_readme_versions(text):
    """Read every local release-note link, regardless of its link text."""
    pattern = r"\[[^\]]*\]\(docs/releases/(%s)\.md\)" % VERSION_PATTERN
    return set(re.findall(pattern, text))


def parse_note_versions(filenames):
    """Read release versions from note filenames."""
    versions = set()
    for filename in filenames:
        match = re.fullmatch(r"(%s)\.md" % VERSION_PATTERN, filename)
        if match is not None:
            versions.add(match.group(1))
    return versions


def parse_tag_versions(tag_names):
    """Read plugin versions while ignoring tags from other namespaces."""
    versions = set()
    for tag_name in tag_names:
        if tag_name.startswith(TAG_PREFIX):
            version = tag_name[len(TAG_PREFIX):]
            parse_version(version)
            versions.add(version)
    return versions


def parse_release_tag_names(text):
    """Read published tag names from gh release list JSON; drafts do not count."""
    try:
        releases = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("Invalid release JSON: %s" % error) from error
    if not isinstance(releases, list):
        raise ValueError("Release input must be a JSON list")
    tag_names = set()
    for release in releases:
        if (not isinstance(release, dict)
                or not isinstance(release.get("tagName"), str)
                or not isinstance(release.get("isDraft"), bool)):
            raise ValueError(
                "Each release must have a tagName string and an isDraft boolean"
            )
        if not release["isDraft"]:
            tag_names.add(release["tagName"])
    return tag_names


def compare_records(readme_versions, note_versions, tag_names, release_tag_names,
                    plugin_version, release=None):
    """Return version-sorted missing records from the supplied immutable facts.

    Every report value is a list of X.Y.Z versions. An explicit release has
    separate requirements even when neither its note nor its tag exists yet.
    """
    readme_versions = set(readme_versions)
    note_versions = set(note_versions)
    tag_versions = parse_tag_versions(tag_names)
    published_versions = parse_tag_versions(release_tag_names)
    parse_version(plugin_version)
    first_note = min(
        (parse_version(version) for version in note_versions), default=None,
    )
    tags_since_first_note = {
        version for version in tag_versions
        if first_note is not None and parse_version(version) >= first_note
    }
    requested_versions = set()
    if release is not None:
        parse_version(release)
        requested_versions.add(release)
    differences = {
        "tags_without_release": tag_versions - published_versions,
        "notes_without_tag": note_versions - tag_versions,
        "tags_without_note": tags_since_first_note - note_versions,
        "readme_versions_without_note": readme_versions - note_versions,
        "notes_without_readme_version": note_versions - readme_versions,
        "plugin_versions_without_note": {plugin_version} - note_versions,
        "release_versions_without_tag": requested_versions - tag_versions,
        "release_versions_without_release": requested_versions - published_versions,
    }
    return {name: sorted(versions, key=parse_version)
            for name, versions in differences.items()}


def read_note_versions(root):
    """Enumerate notes and require that each version's file can be read."""
    directory = root / "docs" / "releases"
    versions = parse_note_versions(path.name for path in directory.iterdir())
    for version in sorted(versions, key=parse_version):
        (directory / (version + ".md")).read_text(encoding="utf-8")
    return versions


def read_plugin_version(root):
    """Read the current version, reporting malformed input with its source path."""
    path = root / ".claude-plugin" / "plugin.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("Expected a JSON object")
        version = manifest.get("version")
        parse_version(version)
    except ValueError as error:
        raise ValueError("%s: %s" % (path, error)) from error
    return version


def read_command_output(arguments):
    """Read a listing command, preserving its failure reason on stderr."""
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    if result.returncode:
        reason = result.stderr.strip() or result.stdout.strip()
        raise ValueError(
            "%s exited %s: %s" % (arguments[0], result.returncode, reason)
        )
    return result.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root", type=Path, nargs="?",
        default=Path(__file__).resolve().parents[1],
        help="Repository root (default: this script's repository)",
    )
    parser.add_argument(
        "--tags-file", type=Path,
        help="Read newline-separated tag names instead of git tag -l",
    )
    parser.add_argument(
        "--releases-file", type=Path,
        help="Read gh release list JSON instead of querying GitHub",
    )
    parser.add_argument(
        "--release", metavar="X.Y.Z",
        help="Require this release's tag and GitHub release",
    )
    arguments = parser.parse_args(argv)
    try:
        readme_versions = parse_readme_versions(
            (arguments.root / "README.md").read_text(encoding="utf-8"),
        )
        note_versions = read_note_versions(arguments.root)
        plugin_version = read_plugin_version(arguments.root)
        if arguments.tags_file is None:
            tags_text = read_command_output([
                "git", "-C", str(arguments.root), "tag", "-l", TAG_PREFIX + "*",
            ])
        else:
            tags_text = arguments.tags_file.read_text(encoding="utf-8")
        if arguments.releases_file is None:
            releases_text = read_command_output([
                "gh", "release", "list", "--repo", "exactory/exactory-client",
                "--limit", "1000", "--json", "tagName,isDraft",
            ])
        else:
            releases_text = arguments.releases_file.read_text(encoding="utf-8")
        report = compare_records(
            readme_versions, note_versions,
            [line.strip() for line in tags_text.splitlines() if line.strip()],
            parse_release_tag_names(releases_text), plugin_version, arguments.release,
        )
    except (OSError, UnicodeError, ValueError) as error:
        print("Cannot read release records: %s" % error, file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 1 if any(report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
