"""Checked inputs are frozen before any consumer can read their private copy."""

import errno
import gzip
import hashlib
import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_harness import workspace
from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError


class BoundedReader(io.BytesIO):
    def read(self, size=-1):
        if not 0 < size <= 1024 * 1024:
            raise AssertionError("Source must be read in bounded chunks")
        return super().read(size)


class FileStreamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def freeze(self, source, expected=None):
        self.assertIsNotNone(importlib.util.find_spec("research_harness.file_streams"),
                             "The shared frozen-file primitive is missing")
        return importlib.import_module("research_harness.file_streams").freeze_file(source, expected)

    def open_file(self, relative):
        self.assertTrue(callable(getattr(workspace, "open_file", None)),
                        "The checked workspace stream primitive is missing")
        return workspace.open_file(self.root, relative)

    def test_freeze_hashes_bounded_chunks_and_supports_seek_replay(self):
        data = b"abc" * 800000
        source = BoundedReader(data)
        with self.freeze(source, describe_artifact(data, "text/plain")) as frozen:
            self.assertEqual(frozen.size, len(data))
            self.assertEqual(frozen.sha256, hashlib.sha256(data).hexdigest())
            self.assertEqual(frozen.md5, hashlib.md5(data).hexdigest())
            self.assertEqual(frozen.stream.tell(), 0)
            self.assertEqual(frozen.stream.read(), data)
            frozen.stream.seek(3)
            self.assertEqual(frozen.stream.read(3), b"abc")
            self.assertEqual(os.fstat(frozen.stream.fileno()).st_mode & 0o077, 0)
        self.assertTrue(frozen.stream.closed)
        self.assertFalse(source.closed)

    def test_freeze_empty_stream_and_cleanup_on_consumer_error(self):
        with self.assertRaisesRegex(RuntimeError, "consumer"):
            with self.freeze(BoundedReader(b"")) as frozen:
                self.assertEqual(frozen.size, 0)
                self.assertEqual(frozen.sha256, hashlib.sha256(b"").hexdigest())
                self.assertEqual(frozen.md5, hashlib.md5(b"").hexdigest())
                raise RuntimeError("consumer")
        self.assertTrue(frozen.stream.closed)

    def test_freeze_verifies_a_nonseekable_stream_without_trusting_its_stat_size(self):
        reader, writer = os.pipe()
        with os.fdopen(reader, "rb") as source:
            with os.fdopen(writer, "wb") as target:
                target.write(b"abc")
            with self.freeze(source, describe_artifact(b"abc", "text/plain")) as frozen:
                self.assertEqual(frozen.stream.read(), b"abc")
                frozen.stream.seek(0)
                self.assertEqual(frozen.stream.read(), b"abc")

    def test_freeze_verifies_logical_bytes_of_compressed_streams(self):
        path = self.root / "compressed.gz"
        path.write_bytes(gzip.compress(b"abc"))
        with gzip.open(path, "rb") as source:
            with self.freeze(source, describe_artifact(b"abc", "text/plain")) as frozen:
                self.assertEqual(frozen.stream.read(), b"abc")

    def test_freeze_verifies_only_the_remaining_plain_file_bytes(self):
        path = self.root / "input"
        path.write_bytes(b"prefixabc")
        with path.open("rb") as source:
            source.read(6)
            with self.freeze(source, describe_artifact(b"abc", "text/plain")) as frozen:
                self.assertEqual(frozen.stream.read(), b"abc")

    def test_freeze_rejects_corrupt_size_and_digest_before_yielding(self):
        reference = describe_artifact(b"abc", "text/plain")
        for data in (b"ab", b"abcd", b"def"):
            with self.subTest(data=data):
                with self.assertRaises(ResearchError) as caught:
                    with self.freeze(BoundedReader(data), reference):
                        self.fail("Unverified content reached the consumer")
                self.assertEqual(caught.exception.code, "artifact_corrupt")

    def test_freeze_checks_regular_file_size_before_allocating_a_spool(self):
        path = self.root / "input"
        path.write_bytes(b"too long")
        self.assertIsNotNone(importlib.util.find_spec("research_harness.file_streams"))
        with path.open("rb") as source:
            with mock.patch("research_harness.file_streams.tempfile.TemporaryFile",
                            side_effect=AssertionError("Allocated before checking size")):
                with self.assertRaises(ResearchError) as caught:
                    with self.freeze(source, describe_artifact(b"abc", "text/plain")):
                        self.fail("Wrong size accepted")
        self.assertEqual(caught.exception.code, "artifact_corrupt")

    def test_freeze_validates_expected_descriptor_before_reading(self):
        for changes in ({"size": True}, {"sha256": "bad"}, {"path": "../outside"},
                        {"media_type": "text/plain\n"}):
            with self.subTest(changes=changes):
                reference = dict(describe_artifact(b"abc", "text/plain"), **changes)
                with self.assertRaises(ResearchError):
                    with self.freeze(mock.Mock(read=mock.Mock(side_effect=AssertionError("Read"))), reference):
                        self.fail("Invalid descriptor accepted")

    def test_freeze_handles_short_writes_and_interrupted_reads_and_writes(self):
        self.assertIsNotNone(importlib.util.find_spec("research_harness.file_streams"))
        original = tempfile.TemporaryFile
        opened = []

        def open_short_writer(*args, **kwargs):
            stream = original(*args, **kwargs)
            opened.append(stream)
            target = mock.MagicMock(wraps=stream)
            target.__enter__.return_value = target
            target.__exit__.side_effect = lambda *args: stream.close()
            interrupted = [False]

            def write(data):
                if not interrupted[0]:
                    interrupted[0] = True
                    raise InterruptedError()
                return stream.write(data[:7])

            target.write.side_effect = write
            return target

        source = BoundedReader(b"abc" * 100)
        original_read = source.read
        interruptions = [True]

        def read(size):
            if interruptions and interruptions.pop():
                raise InterruptedError()
            return original_read(size)

        source.read = read
        with mock.patch("research_harness.file_streams.tempfile.TemporaryFile", side_effect=open_short_writer):
            with self.freeze(source) as frozen:
                self.assertEqual(frozen.stream.read(), b"abc" * 100)
        self.assertTrue(all(stream.closed for stream in opened))

    def test_freeze_cleans_spool_after_read_failure_and_disk_exhaustion(self):
        self.assertIsNotNone(importlib.util.find_spec("research_harness.file_streams"))
        original = tempfile.TemporaryFile
        for failure in (RuntimeError("interrupted"), OSError(errno.ENOSPC, "full")):
            opened = []

            def open_spool(*args, **kwargs):
                stream = original(*args, **kwargs)
                opened.append(stream)
                return stream

            source = mock.Mock()
            source.read.side_effect = [b"prefix", failure]
            source.fileno.side_effect = io.UnsupportedOperation()
            with mock.patch("research_harness.file_streams.tempfile.TemporaryFile", side_effect=open_spool):
                with self.assertRaises((RuntimeError, ResearchError)):
                    with self.freeze(source):
                        self.fail("Incomplete input accepted")
            self.assertTrue(opened)
            self.assertTrue(all(stream.closed for stream in opened))

    def test_freeze_detects_corruption_of_the_private_copy_before_yielding(self):
        original = tempfile.TemporaryFile
        opened = []

        def open_corrupt_spool(*args, **kwargs):
            stream = original(*args, **kwargs)
            opened.append(stream)
            write = stream.write
            stream.write = lambda data: write(b"x" * len(data))
            return stream

        with mock.patch("research_harness.file_streams.tempfile.TemporaryFile", side_effect=open_corrupt_spool):
            with self.assertRaises(ResearchError) as caught:
                with self.freeze(BoundedReader(b"abc")):
                    self.fail("Corrupt private bytes reached the consumer")
        self.assertEqual(caught.exception.code, "artifact_corrupt")
        self.assertTrue(all(stream.closed for stream in opened))

    def test_read_file_keeps_existing_filesystem_error_translation(self):
        (self.root / "input").write_bytes(b"abc")
        original = os.fdopen

        def open_failed_read(*args, **kwargs):
            stream = original(*args, **kwargs)
            stream.read = mock.Mock(side_effect=OSError(errno.EIO, "failed read"))
            return stream

        with mock.patch("research_harness.workspace.os.fdopen", side_effect=open_failed_read):
            with self.assertRaises(ResearchError) as caught:
                workspace.read_file(self.root, "input")
        self.assertEqual(caught.exception.code, "storage_io")

    def test_open_file_keeps_checked_descriptor_after_path_replacement(self):
        path = self.root / "input"
        path.write_bytes(b"abc")
        with self.open_file("input") as source:
            replacement = self.root / "replacement"
            replacement.write_bytes(b"def")
            replacement.replace(path)
            self.assertEqual(source.read(), b"abc")
        self.assertTrue(source.closed)

    def test_open_file_rejects_unsafe_paths_and_nonregular_entries(self):
        (self.root / "input").write_bytes(b"abc")
        (self.root / "link").symlink_to(self.root / "input")
        (self.root / "dir").mkdir()
        (self.root / "parent-link").symlink_to(self.root / "dir", target_is_directory=True)
        os.mkfifo(self.root / "fifo")
        for relative in ("../input", "/input", "./input", "dir//input", "dir/../input",
                         "input\\other", "input\x00", "link", "dir", "fifo", "parent-link/input"):
            with self.subTest(relative=relative):
                with self.assertRaises(ResearchError) as caught:
                    with self.open_file(relative):
                        self.fail("Unsafe input accepted")
                self.assertEqual(caught.exception.code, "unsafe_path")

    def test_open_file_missing_input_does_not_create_anything(self):
        with self.assertRaises(ResearchError) as caught:
            with self.open_file("missing/input"):
                self.fail("Missing input accepted")
        self.assertEqual(caught.exception.code, "artifact_missing")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_open_file_preserves_consumer_error_and_closes_descriptor(self):
        (self.root / "input").write_bytes(b"abc")
        with self.assertRaisesRegex(OSError, "consumer"):
            with self.open_file("input") as source:
                raise OSError(errno.ENOSPC, "consumer")
        self.assertTrue(source.closed)
