"""Immutable content objects must remain verified inside their workspace."""

import os
import tempfile
import threading
import unittest
from pathlib import Path

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError


class ResearchArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        self.objects = self.workspace / "research" / "sources" / "objects"
        self.store = ArtifactStore(self.workspace)

    def assert_error(self, code, action):
        with self.assertRaises(ResearchError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_put_returns_content_address_and_reads_original_bytes(self):
        reference = self.store.put(b"abc", "text/plain")
        self.assertEqual(reference, {
            "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "path": "research/sources/objects/ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "size": 3, "media_type": "text/plain",
        })
        self.assertEqual(ArtifactStore(self.workspace).read(reference), b"abc")
        self.assertEqual((self.workspace / reference["path"]).stat().st_mode & 0o222, 0)

    def test_empty_binary_content_is_valid(self):
        reference = self.store.put(b"", "application/octet-stream")
        self.assertEqual(reference["size"], 0)
        self.assertEqual(reference["sha256"],
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertEqual(self.store.read(reference), b"")

    def test_identical_put_reuses_existing_verified_object_without_writing_it(self):
        first = self.store.put(b"abc", "text/plain")
        path = self.workspace / first["path"]
        before = (path.stat().st_ino, path.stat().st_mtime_ns)
        second = self.store.put(b"abc", "application/octet-stream")
        self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns), before)
        self.assertEqual(second["sha256"], first["sha256"])
        self.assertEqual(second["media_type"], "application/octet-stream")
        self.assertEqual(list(self.objects.iterdir()), [path])

    def test_concurrent_puts_publish_one_complete_object(self):
        results = []
        start = threading.Barrier(4)

        def put():
            start.wait()
            try:
                results.append(ArtifactStore(self.workspace).put(b"abc", "text/plain"))
            except BaseException as error:
                results.append(error)

        threads = [threading.Thread(target=put) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 4)
        for result in results:
            self.assertIsInstance(result, dict)
            self.assertEqual(self.store.read(result), b"abc")
        self.assertEqual(len(list(self.objects.iterdir())), 1)

    def test_constructor_and_failed_read_do_not_create_the_workspace(self):
        self.assertFalse(self.workspace.exists())
        reference = {"sha256": "0" * 64, "path": "research/sources/objects/" + "0" * 64,
                     "size": 0, "media_type": "text/plain"}
        self.assert_error("artifact_missing", lambda: self.store.read(reference))
        self.assertFalse(self.workspace.exists())

    def test_corrupted_object_is_rejected_on_read_and_reuse_without_overwrite(self):
        reference = self.store.put(b"abc", "text/plain")
        path = self.workspace / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"def")
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.assert_error("artifact_corrupt", lambda: self.store.read(reference))
        self.assert_error("artifact_corrupt", lambda: self.store.put(b"abc", "text/plain"))
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_reference_hash_size_and_required_fields_are_validated(self):
        reference = self.store.put(b"abc", "text/plain")
        for changes, code in (({"sha256": "../escape"}, "invalid_input"),
                              ({"size": True}, "invalid_input"), ({"size": -1}, "invalid_input"),
                              ({"size": 2}, "artifact_corrupt"), ({"media_type": ""}, "invalid_input")):
            modified = dict(reference, **changes)
            self.assert_error(code, lambda: self.store.read(modified))
        for field in reference:
            modified = dict(reference)
            del modified[field]
            self.assert_error("invalid_input", lambda: self.store.read(modified))

    def test_paths_cannot_traverse_escape_or_name_non_object_files(self):
        reference = self.store.put(b"abc", "text/plain")
        outside = Path(self.temporary.name) / "outside"
        outside.write_bytes(b"abc")
        for path in (str(outside), "../outside", "research/sources/objects/../../../outside",
                     "research/sources/objects/./" + reference["sha256"],
                     "research//sources/objects/" + reference["sha256"],
                     "untrusted.txt", "research\\sources\\objects\\" + reference["sha256"]):
            modified = dict(reference, path=path)
            self.assert_error("unsafe_path", lambda: self.store.read(modified))
        different = dict(reference, sha256="0" * 64)
        self.assert_error("unsafe_path", lambda: self.store.read(different))

    def test_each_directory_symlink_is_rejected_on_put_without_external_writes(self):
        for index, relative in enumerate(("research", "research/sources", "research/sources/objects")):
            with self.subTest(relative=relative):
                root = Path(self.temporary.name) / ("workspace-" + str(index))
                outside = Path(self.temporary.name) / ("outside-" + str(index))
                outside.mkdir()
                candidate = root / relative
                candidate.parent.mkdir(parents=True)
                candidate.symlink_to(outside, target_is_directory=True)
                self.assert_error("unsafe_path", lambda: ArtifactStore(root).put(b"abc", "text/plain"))
                self.assertEqual(list(outside.iterdir()), [])

    def test_object_symlink_is_rejected_on_read_and_reuse(self):
        reference = self.store.put(b"abc", "text/plain")
        path = self.workspace / reference["path"]
        outside = Path(self.temporary.name) / "outside"
        outside.write_bytes(b"abc")
        path.unlink()
        path.symlink_to(outside)
        self.assert_error("unsafe_path", lambda: self.store.read(reference))
        self.assert_error("unsafe_path", lambda: self.store.put(b"abc", "text/plain"))
        self.assertEqual(outside.read_bytes(), b"abc")

    def test_in_workspace_symlinks_and_replaced_parent_are_also_rejected(self):
        reference = self.store.put(b"abc", "text/plain")
        original = self.workspace / reference["path"]
        alias = self.workspace / "alias"
        original.rename(alias)
        original.symlink_to(alias)
        self.assert_error("unsafe_path", lambda: self.store.read(reference))
        original.unlink()
        alias.rename(original)
        saved = self.workspace / "saved"
        (self.workspace / "research").rename(saved)
        (self.workspace / "research").symlink_to(saved, target_is_directory=True)
        self.assert_error("unsafe_path", lambda: self.store.read(reference))

    def test_directories_and_fifos_are_rejected_as_content_objects(self):
        reference = self.store.put(b"abc", "text/plain")
        path = self.workspace / reference["path"]
        path.unlink()
        path.mkdir()
        self.assert_error("unsafe_path", lambda: self.store.read(reference))
        path.rmdir()
        os.mkfifo(path)
        self.assert_error("unsafe_path", lambda: self.store.read(reference))

    def test_invalid_put_does_not_create_artifact_directories(self):
        for data, media_type in (("text", "text/plain"), (b"abc", ""),
                                  (b"abc", "text/plain\nsecret"), (b"abc", None),
                                  (b"abc", "text/\x00plain"), (b"abc", "text/\ud800")):
            self.assert_error("invalid_input", lambda: self.store.put(data, media_type))
        self.assertFalse(self.workspace.exists())

    def test_symlinked_workspace_is_rejected_without_external_writes(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        self.workspace.symlink_to(outside, target_is_directory=True)
        self.assert_error("unsafe_path", lambda: self.store.put(b"abc", "text/plain"))
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
