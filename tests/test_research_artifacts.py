"""Immutable content objects must remain verified inside their workspace."""

import errno
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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

    def test_first_put_syncs_each_new_directory_entry_including_workspace_ancestors(self):
        workspace = Path(self.temporary.name) / "new-parent" / "workspace"
        original_fsync = os.fsync
        synchronized = set()

        def record_sync(descriptor):
            info = os.fstat(descriptor)
            original_fsync(descriptor)
            if stat.S_ISDIR(info.st_mode):
                synchronized.add((info.st_dev, info.st_ino))

        with mock.patch("research_harness.artifacts.os.fsync", side_effect=record_sync):
            reference = ArtifactStore(workspace).put(b"abc", "text/plain")
        for parent in (workspace.parent.parent, workspace.parent, workspace,
                       workspace / "research", workspace / "research" / "sources",
                       workspace / "research" / "sources" / "objects"):
            info = parent.stat()
            self.assertIn((info.st_dev, info.st_ino), synchronized, str(parent))
        self.assertEqual(ArtifactStore(workspace).read(reference), b"abc")

    def test_directory_entry_sync_failure_rejects_first_put_and_allows_a_durable_retry(self):
        original_fsync = os.fsync
        for index, relative in enumerate(("parent", "workspace", "research", "sources")):
            with self.subTest(parent=relative):
                workspace = Path(self.temporary.name) / ("workspace-" + str(index))
                parents = {"parent": workspace.parent, "workspace": workspace,
                           "research": workspace / "research",
                           "sources": workspace / "research" / "sources"}
                target = parents[relative]

                def fail_target_sync(descriptor):
                    info = os.fstat(descriptor)
                    if target.exists():
                        expected = target.stat()
                        if (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino):
                            raise OSError(errno.EIO, "Injected directory synchronization failure")
                    original_fsync(descriptor)

                store = ArtifactStore(workspace)
                with mock.patch("research_harness.artifacts.os.fsync", side_effect=fail_target_sync):
                    self.assert_error("storage_io", lambda: store.put(b"abc", "text/plain"))
                    # Existing but unsynchronized directory entries still need sync.
                    self.assert_error("storage_io", lambda: store.put(b"abc", "text/plain"))
                self.assertEqual(list(workspace.rglob("ba7816bf*")), [])
                reference = store.put(b"abc", "text/plain")
                self.assertEqual(store.read(reference), b"abc")

    def test_later_writer_syncs_an_ancestor_left_by_a_paused_directory_creator(self):
        parent = self.workspace.parent.stat()
        original_fsync = os.fsync
        creator_waiting = threading.Event()
        release_creator = threading.Event()
        later_writer_synchronized = threading.Event()
        results = []

        def synchronize_ancestor(descriptor):
            info = os.fstat(descriptor)
            is_workspace_parent = (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino)
            if is_workspace_parent and threading.current_thread() is creator:
                creator_waiting.set()
                if not release_creator.wait(5):
                    raise RuntimeError("Test did not release directory creator")
                raise OSError(errno.EIO, "Injected original directory synchronization failure")
            original_fsync(descriptor)
            if is_workspace_parent:
                later_writer_synchronized.set()

        def create():
            try:
                results.append(self.store.put(b"abc", "text/plain"))
            except BaseException as error:
                results.append(error)

        creator = threading.Thread(target=create)
        with mock.patch("research_harness.artifacts.os.fsync", side_effect=synchronize_ancestor):
            creator.start()
            try:
                self.assertTrue(creator_waiting.wait(5))
                self.assertTrue(self.workspace.is_dir())
                reference = self.store.put(b"abc", "text/plain")
                self.assertTrue(later_writer_synchronized.is_set())
            finally:
                release_creator.set()
                creator.join(5)
        self.assertFalse(creator.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], ResearchError)
        self.assertEqual(results[0].code, "storage_io")
        self.assertEqual(self.store.read(reference), b"abc")

    def test_reuse_syncs_the_object_directory_while_the_original_publisher_is_paused(self):
        self.objects.mkdir(parents=True)
        object_directory = self.objects.stat()
        original_fsync = os.fsync
        publisher_waiting = threading.Event()
        release_publisher = threading.Event()
        reuse_synchronized = threading.Event()
        results = []

        def synchronized_publication(descriptor):
            info = os.fstat(descriptor)
            is_object_directory = (info.st_dev, info.st_ino) == (
                object_directory.st_dev, object_directory.st_ino)
            if is_object_directory and threading.current_thread() is publisher:
                publisher_waiting.set()
                if not release_publisher.wait(5):
                    raise RuntimeError("Test did not release original publisher")
                raise OSError(errno.EIO, "Injected original publisher synchronization failure")
            original_fsync(descriptor)
            if is_object_directory:
                reuse_synchronized.set()

        def publish():
            try:
                results.append(self.store.put(b"abc", "text/plain"))
            except BaseException as error:
                results.append(error)

        publisher = threading.Thread(target=publish)
        with mock.patch("research_harness.artifacts.os.fsync", side_effect=synchronized_publication):
            publisher.start()
            try:
                self.assertTrue(publisher_waiting.wait(5))
                reference = self.store.put(b"abc", "text/plain")
                self.assertTrue(reuse_synchronized.is_set())
            finally:
                release_publisher.set()
                publisher.join(5)
        self.assertFalse(publisher.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], ResearchError)
        self.assertEqual(results[0].code, "storage_io")
        self.assertEqual(self.store.read(reference), b"abc")

    def test_reuse_propagates_its_own_publication_sync_failure(self):
        reference = self.store.put(b"abc", "text/plain")
        directory = self.objects.stat()
        original_fsync = os.fsync

        def fail_publication_sync(descriptor):
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                raise OSError(errno.EIO, "Injected reuse synchronization failure")
            original_fsync(descriptor)

        with mock.patch("research_harness.artifacts.os.fsync", side_effect=fail_publication_sync):
            self.assert_error("storage_io", lambda: self.store.put(b"abc", "text/plain"))
        self.assertEqual(self.store.read(reference), b"abc")

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
