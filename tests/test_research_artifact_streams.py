"""Streaming artifact operations retain atomic publication and checked paths."""

import errno
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from research_harness.artifacts import ArtifactStore, _CheckEffectRequired, _StagedArtifacts, describe_artifact
from research_harness.errors import ResearchError
from research_harness.http import DEFAULT_MAX_RESPONSE_BYTES
from test_file_streams import BoundedReader


class ResearchArtifactStreamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        self.store = ArtifactStore(self.root)
        self.objects = self.root / "research/sources/objects"

    def require_method(self, name):
        self.assertTrue(callable(getattr(self.store, name, None)), name + " primitive is missing")
        return getattr(self.store, name)

    def guard_reads(self):
        original = os.fdopen

        def open_bounded(descriptor, *args, **kwargs):
            stream = original(descriptor, *args, **kwargs)
            if args and args[0] == "rb":
                read = stream.read

                def read_bounded(size=-1):
                    self.assertTrue(0 < size <= 1024 * 1024, "Unbounded artifact read")
                    return read(size)

                stream.read = read_bounded
            return stream

        return mock.patch("research_harness.artifacts.os.fdopen", side_effect=open_bounded)

    def test_stream_and_file_insertions_match_ordinary_put(self):
        put_stream = self.require_method("put_stream")
        put_file = self.require_method("put_file")
        for data in (b"", b"abc", bytes(range(256)) * 10000):
            with self.subTest(size=len(data)):
                path = Path(self.temporary.name) / "input"
                path.write_bytes(data)
                streamed = put_stream(BoundedReader(data), "application/octet-stream")
                before = (self.root / streamed["path"]).stat().st_ino
                self.assertEqual(streamed, self.store.put(data, "application/octet-stream"))
                self.assertEqual(streamed, put_file(path, "application/octet-stream"))
                self.assertEqual((self.root / streamed["path"]).stat().st_ino, before)
                self.assertEqual(self.store.read(streamed), data)
                self.assertEqual((self.root / streamed["path"]).stat().st_mode & 0o222, 0)

    def test_insert_generated_stream_above_http_ceiling_with_bounded_memory(self):
        put_stream = self.require_method("put_stream")
        total = DEFAULT_MAX_RESPONSE_BYTES + 1
        remaining = [total]
        digest = hashlib.sha256()

        def read(size):
            self.assertTrue(0 < size <= 1024 * 1024)
            data = b"x" * min(size, remaining[0])
            remaining[0] -= len(data)
            digest.update(data)
            return data

        source = mock.Mock(read=read)
        source.fileno.side_effect = io.UnsupportedOperation()
        with self.guard_reads():
            reference = put_stream(source, "application/octet-stream")
            self.require_method("verify")(reference)
        self.assertEqual(reference["size"], total)
        self.assertEqual(reference["sha256"], digest.hexdigest())
        self.assertEqual((self.root / reference["path"]).stat().st_size, total)

    def test_existing_object_reuse_and_sealed_verification_are_streamed(self):
        reference = self.store.put(b"abc", "text/plain")
        sealed = self.store.put_sealed(b"abc", "text/plain")
        with self.guard_reads():
            self.assertEqual(self.store.put(b"abc", "text/plain"), reference)
            self.store.verify_sealed(sealed)

    def test_verify_and_stream_reuse_reject_corruption_without_whole_object_reads(self):
        verify = self.require_method("verify")
        put_stream = self.require_method("put_stream")
        reference = self.store.put(b"abc", "text/plain")
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"def")
        with self.guard_reads():
            for action in (lambda: verify(reference), lambda: put_stream(BoundedReader(b"abc"), "text/plain")):
                with self.assertRaises(ResearchError) as caught:
                    action()
                self.assertEqual(caught.exception.code, "artifact_corrupt")
        self.assertEqual(path.read_bytes(), b"def")
        self.assertEqual(list(self.objects.iterdir()), [path])

    def test_concurrent_insertions_publish_one_complete_object(self):
        self.require_method("put_stream")
        start = threading.Barrier(4)
        data = b"abc" * 800000

        def put(_):
            start.wait(10)
            return ArtifactStore(self.root).put_stream(BoundedReader(data), "text/plain")

        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(put, range(4)))
        self.assertEqual(results, [describe_artifact(data, "text/plain")] * 4)
        self.assertEqual(list(self.objects.iterdir()), [self.root / results[0]["path"]])
        self.assertEqual(self.store.read(results[0]), data)

    def test_incomplete_or_wrong_expected_input_never_publishes(self):
        put_stream = self.require_method("put_stream")
        reference = describe_artifact(b"abc", "text/plain")
        for source in (BoundedReader(b"ab"), BoundedReader(b"def"), BoundedReader(b"abcd")):
            with self.assertRaises(ResearchError) as caught:
                put_stream(source, "text/plain", expected=reference)
            self.assertEqual(caught.exception.code, "artifact_corrupt")
            self.assertEqual(list(self.objects.iterdir()), [])

    def test_read_interruption_and_disk_exhaustion_clean_unpublished_objects(self):
        put_stream = self.require_method("put_stream")
        for failure in (KeyboardInterrupt(), OSError(errno.ENOSPC, "full")):
            source = mock.Mock()
            source.fileno.side_effect = io.UnsupportedOperation()
            source.read.side_effect = [b"prefix", failure]
            with self.assertRaises((KeyboardInterrupt, ResearchError)):
                put_stream(source, "text/plain")
            self.assertEqual(list(self.objects.iterdir()), [])

    def test_short_writes_are_completed_and_zero_writes_fail_without_publication(self):
        put_stream = self.require_method("put_stream")
        original = os.fdopen
        for limit in (7, 0):
            def open_short(descriptor, *args, **kwargs):
                stream = original(descriptor, *args, **kwargs)
                if args and "w" in args[0]:
                    write = stream.write
                    stream.write = lambda data: write(data[:limit])
                return stream

            data = b"short writes" * 100
            with mock.patch("research_harness.artifacts.os.fdopen", side_effect=open_short):
                if limit:
                    reference = put_stream(BoundedReader(data), "text/plain")
                else:
                    with self.assertRaises(ResearchError) as caught:
                        put_stream(BoundedReader(b"different"), "text/plain")
                    self.assertEqual(caught.exception.code, "storage_io")
            self.assertEqual(self.store.read(reference), data)
            self.assertEqual(list(self.objects.iterdir()), [self.root / reference["path"]])

    def test_failed_file_sync_cannot_publish_an_object(self):
        put_stream = self.require_method("put_stream")
        self.objects.mkdir(parents=True)
        original = os.fsync

        def fail_file_sync(descriptor):
            import stat
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError(errno.ENOSPC, "full")
            return original(descriptor)

        with mock.patch("research_harness.artifacts.os.fsync", side_effect=fail_file_sync):
            with self.assertRaises(ResearchError) as caught:
                put_stream(BoundedReader(b"abc"), "text/plain")
        self.assertEqual(caught.exception.code, "storage_io")
        self.assertEqual(list(self.objects.iterdir()), [])

    def test_disk_full_during_write_cleans_partial_object_and_allows_retry(self):
        original = os.fdopen

        def open_disk_full(descriptor, *args, **kwargs):
            stream = original(descriptor, *args, **kwargs)
            if args and "w" in args[0]:
                write = stream.write
                writes = [True]

                def write_until_full(data):
                    if writes and writes.pop():
                        return write(data[:2])
                    raise OSError(errno.ENOSPC, "full")

                stream.write = write_until_full
            return stream

        with mock.patch("research_harness.artifacts.os.fdopen", side_effect=open_disk_full):
            with self.assertRaises(ResearchError) as caught:
                self.store.put_stream(BoundedReader(b"abc"), "text/plain")
        self.assertEqual(caught.exception.code, "storage_io")
        self.assertEqual(list(self.objects.iterdir()), [])
        reference = self.store.put_stream(BoundedReader(b"abc"), "text/plain")
        self.assertEqual(self.store.read(reference), b"abc")

    def test_stream_operations_preserve_staged_reads_and_do_not_publish_in_check_mode(self):
        stored = self.store.put(b"stored", "text/plain")
        staged = _StagedArtifacts(self.root)
        prepared = staged.put(b"prepared", "text/plain")
        for reference, data in ((stored, b"stored"), (prepared, b"prepared")):
            staged.verify(reference)
            with staged.open_verified(reference) as frozen:
                self.assertEqual(frozen.stream.read(), data)
        self.assertEqual(staged.read_digests(), sorted([stored["sha256"], prepared["sha256"]]))
        before = sorted(self.objects.iterdir())
        with self.assertRaises(_CheckEffectRequired):
            staged.put_stream(BoundedReader(b"unpublished"), "text/plain")
        self.assertEqual(sorted(self.objects.iterdir()), before)
        self.assertFalse((self.root / prepared["path"]).exists())

    def test_controller_staging_cannot_publish_streams_through_its_external_effect_delegate(self):
        from research_harness.review_staged_protocol import ControllerArtifacts
        controller = ControllerArtifacts(self.store)
        path = Path(self.temporary.name) / "input"
        path.write_bytes(b"abc")
        for operation in (lambda: controller.put_stream(BoundedReader(b"abc"), "text/plain"),
                          lambda: controller.put_file(path, "text/plain")):
            with self.subTest(operation=operation):
                with self.assertRaises(_CheckEffectRequired):
                    operation()
                self.assertFalse(self.root.exists())

    def test_staged_verified_reads_are_observed_before_check_completion_exits_the_context(self):
        from research_harness.check_mode import _CheckComplete
        reference = self.store.put(b"abc", "text/plain")
        staged = _StagedArtifacts(self.root)
        with self.assertRaises(_CheckComplete) as caught:
            with staged.open_verified(reference) as frozen:
                self.assertEqual(frozen.stream.read(), b"abc")
                raise _CheckComplete({"artifact_digests": staged.read_digests()})
        self.assertEqual(caught.exception.response["artifact_digests"], [reference["sha256"]])
        self.assertEqual(staged.read_digests(), [reference["sha256"]])
        untouched = _StagedArtifacts(self.root)
        with self.assertRaises(ResearchError):
            with untouched.open_verified(dict(reference, size=4)):
                self.fail("Corrupt input accepted")
        self.assertEqual(untouched.read_digests(), [])

    def test_stream_insertion_rejects_unsafe_object_directories_and_source_links(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        for index, relative in enumerate(("research", "research/sources", "research/sources/objects")):
            root = Path(self.temporary.name) / ("unsafe-" + str(index))
            candidate = root / relative
            candidate.parent.mkdir(parents=True)
            candidate.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ResearchError) as caught:
                ArtifactStore(root).put_stream(BoundedReader(b"abc"), "text/plain")
            self.assertEqual(caught.exception.code, "unsafe_path")
            self.assertEqual(list(outside.iterdir()), [])
        link = Path(self.temporary.name) / "input-link"
        link.symlink_to(outside / "missing")
        with self.assertRaises(ResearchError) as caught:
            self.store.put_file(link, "text/plain")
        self.assertEqual(caught.exception.code, "unsafe_path")

    def test_file_insertion_reports_invalid_path_types_as_unsafe_paths(self):
        for path in (None, 42):
            with self.subTest(path=path):
                with self.assertRaises(ResearchError) as caught:
                    self.store.put_file(path, "text/plain")
                self.assertEqual(caught.exception.code, "unsafe_path")
                self.assertFalse(self.root.exists())

    def test_materialization_rejects_a_symlinked_destination(self):
        reference = self.store.put(b"abc", "text/plain")
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        destination = Path(self.temporary.name) / "destination-link"
        destination.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ResearchError) as caught:
            with self.store.materialize_verified(reference, destination):
                self.fail("Unsafe materialization destination accepted")
        self.assertEqual(caught.exception.code, "unsafe_path")
        self.assertEqual(list(outside.iterdir()), [])

    def test_open_verified_is_private_seekable_and_survives_source_mutation(self):
        open_verified = self.require_method("open_verified")
        reference = self.store.put(b"abc", "text/plain")
        with self.guard_reads():
            context = open_verified(reference)
            frozen = context.__enter__()
        try:
            path = self.root / reference["path"]
            path.chmod(0o600)
            path.write_bytes(b"def")
            self.assertEqual(frozen.stream.read(), b"abc")
            frozen.stream.seek(0)
            self.assertEqual(frozen.stream.read(), b"abc")
            self.assertEqual(frozen.md5, "900150983cd24fb0d6963f7d28e17f72")
        finally:
            context.__exit__(None, None, None)
        self.assertTrue(frozen.stream.closed)

    def test_open_verified_rejects_bad_size_before_spool_allocation_and_bad_hash_before_yield(self):
        open_verified = self.require_method("open_verified")
        reference = self.store.put(b"abc", "text/plain")
        with mock.patch("research_harness.file_streams.tempfile.TemporaryFile",
                        side_effect=AssertionError("Allocated before size check")):
            with self.assertRaises(ResearchError) as caught:
                with open_verified(dict(reference, size=4)):
                    self.fail("Wrong size accepted")
        self.assertEqual(caught.exception.code, "artifact_corrupt")
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"def")
        with self.assertRaises(ResearchError) as caught:
            with open_verified(reference):
                self.fail("Corrupt input exposed")
        self.assertEqual(caught.exception.code, "artifact_corrupt")

    def test_materialization_is_private_and_subprocess_reads_it_after_source_replacement(self):
        materialize = self.require_method("materialize_verified")
        reference = self.store.put(b"abc", "text/plain")
        destination = Path(self.temporary.name) / "extract"
        destination.mkdir()
        with materialize(reference, destination) as path:
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent.stat().st_mode & 0o077, 0)
            original = self.root / reference["path"]
            original.unlink()
            original.write_bytes(b"def")
            result = subprocess.check_output([sys.executable, "-c",
                                              "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
                                              str(path)])
            self.assertEqual(result, b"abc")
        self.assertEqual(list(destination.iterdir()), [])

    def test_materialization_cleans_up_on_consumer_failure_and_corruption(self):
        materialize = self.require_method("materialize_verified")
        reference = self.store.put(b"abc", "text/plain")
        destination = Path(self.temporary.name) / "extract"
        destination.mkdir()
        with self.assertRaisesRegex(RuntimeError, "consumer"):
            with materialize(reference, destination):
                raise RuntimeError("consumer")
        self.assertEqual(list(destination.iterdir()), [])
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"def")
        with self.assertRaises(ResearchError):
            with materialize(reference, destination):
                self.fail("Corrupt materialization exposed")
        self.assertEqual(list(destination.iterdir()), [])

    def test_materialization_checks_its_written_bytes_before_subprocess_access(self):
        materialize = self.require_method("materialize_verified")
        reference = self.store.put(b"abc", "text/plain")
        destination = Path(self.temporary.name) / "extract"
        destination.mkdir()
        original = os.fdopen

        def open_corrupt_writer(descriptor, *args, **kwargs):
            stream = original(descriptor, *args, **kwargs)
            if args and "w" in args[0]:
                write = stream.write
                stream.write = lambda data: write(b"x" * len(data))
            return stream

        with mock.patch("research_harness.artifacts.os.fdopen", side_effect=open_corrupt_writer):
            with self.assertRaises(ResearchError) as caught:
                with materialize(reference, destination):
                    self.fail("Corrupt subprocess input exposed")
        self.assertEqual(caught.exception.code, "artifact_corrupt")
        self.assertEqual(list(destination.iterdir()), [])

    def test_new_read_operations_validate_descriptors_paths_and_sealed_boundary(self):
        verify = self.require_method("verify")
        open_verified = self.require_method("open_verified")
        reference = self.store.put(b"abc", "text/plain")
        sealed = self.store.put_sealed(b"abc", "text/plain")
        for modified, code in ((dict(reference, size=True), "invalid_input"),
                               (dict(reference, path="../outside"), "unsafe_path"),
                               (sealed, "artifact_sealed")):
            for operation in (verify, open_verified):
                with self.subTest(code=code, operation=operation.__name__):
                    with self.assertRaises(ResearchError) as caught:
                        if operation == verify:
                            operation(modified)
                        else:
                            with operation(modified):
                                self.fail("Invalid descriptor accepted")
                    self.assertEqual(caught.exception.code, code)
        path = self.root / reference["path"]
        path.unlink()
        path.symlink_to(Path(self.temporary.name) / "outside")
        with self.assertRaises(ResearchError) as caught:
            verify(reference)
        self.assertEqual(caught.exception.code, "unsafe_path")

    def test_missing_verification_does_not_create_workspace(self):
        verify = self.require_method("verify")
        with self.assertRaises(ResearchError) as caught:
            verify(describe_artifact(b"abc", "text/plain"))
        self.assertEqual(caught.exception.code, "artifact_missing")
        self.assertFalse(self.root.exists())
