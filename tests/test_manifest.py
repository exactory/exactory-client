"""Tests for the plugin manifest, the marketplace manifest, and the file layout."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import py_compile
import tempfile
import unittest
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_MARKETPLACE_MANIFEST_PATH = (
    _PLUGIN_ROOT.parent / "marketplace" / ".claude-plugin" / "marketplace.json"
)


def _compile_python_source(path: Path) -> None:
    with tempfile.TemporaryDirectory() as scratch_dir:
        py_compile.compile(str(path), cfile=os.path.join(scratch_dir, "out.pyc"), doraise=True)


def _load_bin_module(command_name: str):
    loader = importlib.machinery.SourceFileLoader(
        f"manifest_{command_name.replace('-', '_')}", str(_PLUGIN_ROOT / "bin" / command_name)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TestPluginManifest(unittest.TestCase):
    def test_plugin_manifest_parses_and_carries_the_release_version(self) -> None:
        manifest = json.loads((_PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "exactory")
        self.assertEqual(manifest["version"], "0.13.0")

    def test_every_bin_user_agent_carries_the_manifest_version(self) -> None:
        version = json.loads(
            (_PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text()
        )["version"]
        expected_ua_prefixes = {
            "exactory": f"exactory-client/{version}",
            "exactory-check": f"exactory-check/{version}",
            "exactory-draft": f"exactory-draft/{version}",
            "exactory-predict": f"exactory-predict/{version}",
        }
        # Files only. CI compiles every bin before it runs the tests, and on Python 3.12
        # that writes a `bin/__pycache__` directory, which is a build artifact and not a
        # command this assertion is about.
        bin_names = sorted(
            path.name for path in (_PLUGIN_ROOT / "bin").iterdir() if path.is_file()
        )
        self.assertEqual(bin_names, sorted(expected_ua_prefixes))
        for command_name, expected_ua_prefix in expected_ua_prefixes.items():
            with self.subTest(command=command_name):
                module = _load_bin_module(command_name)
                self.assertTrue(module._USER_AGENT.startswith(expected_ua_prefix))


class TestMarketplaceManifest(unittest.TestCase):
    def setUp(self) -> None:
        if not _MARKETPLACE_MANIFEST_PATH.exists():
            self.skipTest("the marketplace repo is not checked out next to this one")

    def test_marketplace_lists_one_plugin_and_the_rename(self) -> None:
        manifest = json.loads(_MARKETPLACE_MANIFEST_PATH.read_text())
        self.assertEqual([entry["name"] for entry in manifest["plugins"]], ["exactory"])
        self.assertEqual(manifest["renames"], {"exactory-verifier": "exactory"})


class TestSkillLayout(unittest.TestCase):
    def test_every_skill_directory_has_a_skill_file_with_frontmatter(self) -> None:
        skill_dirs = [path for path in (_PLUGIN_ROOT / "skills").iterdir() if path.is_dir()]
        self.assertTrue(skill_dirs)
        for skill_dir in skill_dirs:
            with self.subTest(skill=skill_dir.name):
                lines = (skill_dir / "SKILL.md").read_text().splitlines()
                self.assertEqual(lines[0], "---")
                frontmatter = lines[1 : lines.index("---", 1)]
                self.assertTrue(any(line.startswith("description:") for line in frontmatter))


class TestExecutableSources(unittest.TestCase):
    def test_every_bin_command_has_a_shebang_and_compiles(self) -> None:
        bin_files = sorted(path for path in (_PLUGIN_ROOT / "bin").iterdir() if path.is_file())
        self.assertTrue(bin_files)
        for path in bin_files:
            with self.subTest(command=path.name):
                self.assertTrue(path.read_text().startswith("#!"))
                _compile_python_source(path)

    def test_every_hook_script_has_a_shebang_and_compiles(self) -> None:
        hooks_dir = _PLUGIN_ROOT / "hooks"
        self.assertTrue(hooks_dir.is_dir())
        hook_scripts = sorted(hooks_dir.glob("*.py"))
        self.assertTrue(hook_scripts)
        for path in hook_scripts:
            with self.subTest(script=path.name):
                self.assertTrue(path.read_text().startswith("#!"))
                _compile_python_source(path)


if __name__ == "__main__":
    unittest.main()
