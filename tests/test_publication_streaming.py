"""Readiness, upload snapshots and per-file recovery without whole payload reads."""

from contextlib import contextmanager
import hashlib
import inspect
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from research_harness import publication, zenodo
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.remote import get_intent
from research_harness.storage import Store
from research_harness import workspace
from test_draft import (_DepositTestCase, _PlainWorkspaceDepositTestCase, _FakeZenodoApi,
                        _draft, prepare_manuscript)


class _BoundedReader:
    def __init__(self, source):
        self.source = source

    def read(self, size=-1):
        if not 0 < size <= 1024 * 1024:
            raise AssertionError("payload read must be bounded")
        return self.source.read(size)

    def __getattr__(self, name):
        return getattr(self.source, name)


class _StreamingZenodoApi(_FakeZenodoApi):
    def __init__(self, case, expected):
        super().__init__()
        self.case, self.expected = case, expected
        self.streams, self.bodies = [], {}

    def __call__(self, request):
        if request.get_method() == "PUT" and "/files/" in request.full_url:
            name = request.full_url.rsplit("/", 1)[-1]
            stream = request.data
            self.case.assertTrue(hasattr(stream, "read"), "upload must send a stream")
            self.case.assertTrue(stream.seekable())
            self.case.assertEqual(stream.tell(), 0)
            self.case.assertEqual(request.get_header("Content-length"), str(len(self.expected[name])))
            self.case.assertIsNone(request.get_header("Transfer-encoding"))
            # Read like the HTTP transport, then replay the seekable body.
            chunks = []
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            body = b"".join(chunks)
            self.case.assertEqual(body, self.expected[name])
            stream.seek(0)
            self.streams.append(stream)
            self.bodies[name] = body
        return super().__call__(request)


class ManagedStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = b"%PDF-1.4\n" + b"paper content\n" * 80000
        cls.sources = b"PK\x03\x04" + b"source content\n" * 80000

        def prepare(case, **kwargs):
            (case.root / "draft/paper.pdf").write_bytes(cls.pdf)
            (case.root / "sources.zip").write_bytes(cls.sources)
            return prepare_manuscript(case, sources="sources.zip", **kwargs)

        fixture = _DepositTestCase()
        cls.addClassCleanup(fixture.doCleanups)
        cls.fixture_root = fixture
        previous = Path.cwd()
        with patch("test_draft.prepare_manuscript", side_effect=prepare):
            fixture.setUp()
        os.chdir(previous)

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.workspace_dir = Path(scratch.name) / "workspace"
        shutil.copytree(self.fixture_root.workspace_dir, self.workspace_dir)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.workspace_dir)
        self.fake_api = _FakeZenodoApi()
        patcher = patch.object(_draft, "_open_url", self.fake_api)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(self.workspace_dir)
        self.bundle = publication.find_selected_bundle(self.store.snapshot()["records"])
        self.paths = {item["path"] for item in self.bundle["files"].values() if item}
        self.expected = {"paper.pdf": self.pdf, "supplementary-sources.zip": self.sources}

    def validate(self):
        return zenodo.validate_deposit(self.store, Path("draft/paper.pdf"), Path("draft/abstract.txt"),
                                       Path("sources.zip"), new_version=False, environment="sandbox")

    def begin(self):
        report = self.validate()
        binding = {"bundle_digest": self.bundle["digest"], "prepared_revision": self.bundle["prepared_revision"],
                   "base_url": "https://sandbox.zenodo.org/api", "environment": "sandbox",
                   "new_version": False, "publish": False, "metadata": {"title": "Streaming paper"},
                   "uploads": [{"name": name, "artifact": self.bundle["files"][kind]["artifact"]}
                               for name, kind in (("paper.pdf", "pdf"), ("supplementary-sources.zip", "sources"))],
                   "prior": None}
        return zenodo.begin_deposit(self.store, binding, expected_revision=report["revision"], request_id="streaming")

    @contextmanager
    def bound_payload_reads(self):
        original_open, original_read = workspace.open_file, ArtifactStore.read
        payloads = {self.bundle["files"][kind]["artifact"]["sha256"] for kind in ("pdf", "sources")}

        @contextmanager
        def open_bounded(root, relative):
            with original_open(root, relative) as source:
                yield _BoundedReader(source) if relative in self.paths else source

        def read_bounded(artifacts, reference):
            caller = inspect.currentframe().f_back
            if caller.f_code.co_name == "read" and caller.f_globals.get("__name__") == "research_harness.evaluation":
                caller = caller.f_back
            if (reference["sha256"] in payloads and caller.f_globals.get("__name__") in
                    ("research_harness.publication", "research_harness.zenodo")):
                raise AssertionError("payload artifact must use verified streaming")
            return original_read(artifacts, reference)

        with patch.object(workspace, "open_file", open_bounded), \
                patch.object(publication, "open_file", open_bounded, create=True), \
                patch.object(ArtifactStore, "read", read_bounded):
            yield

    def test_validate_deposit_compares_large_bundle_files_with_bounded_reads(self):
        with self.bound_payload_reads():
            self.assertTrue(self.validate()["ready"])

    def test_validate_deposit_compares_upload_alias_with_bounded_reads(self):
        report = self.validate()
        # Isolate the second comparison, after the real readiness report has run.
        with patch.object(publication, "publication_state", return_value=report), self.bound_payload_reads():
            self.assertTrue(self.validate()["ready"])

    def test_continue_deposit_sends_exact_seekable_snapshots_with_content_lengths(self):
        intent = self.begin()
        api = _StreamingZenodoApi(self, self.expected)
        with patch.object(_draft, "_open_url", api):
            receipt = zenodo.continue_deposit(self.store, intent["id"],
                                             _draft._build_zenodo_client("sandbox", "token"))
        self.assertEqual(receipt["deposition_id"], 4242)
        self.assertEqual(api.bodies, self.expected)
        self.assertTrue(all(stream.closed for stream in api.streams))

    def test_changed_working_file_after_upload_freeze_is_refused_before_create(self):
        intent = self.begin()
        original = ArtifactStore.open_verified
        streams = []
        target = self.bundle["files"]["sources"]["artifact"]

        @contextmanager
        def freeze_then_change(artifacts, reference):
            with original(artifacts, reference) as frozen:
                streams.append(frozen.stream)
                if reference == target:
                    (self.workspace_dir / "draft/paper.pdf").write_bytes(b"%PDF-changed after freezing")
                yield frozen

        with patch.object(ArtifactStore, "open_verified", freeze_then_change):
            with self.assertRaises(ResearchError) as caught:
                zenodo.continue_deposit(self.store, intent["id"],
                                        _draft._build_zenodo_client("sandbox", "token"))
        self.assertEqual(caught.exception.code, "readiness_required")
        self.assertEqual(self.fake_api.requests, [])
        self.assertTrue(all(stream.closed for stream in streams))

    def test_corrupt_later_upload_is_refused_before_first_remote_write(self):
        intent = self.begin()
        original = zenodo._current
        changed = []

        def check_then_corrupt(store, binding):
            revision = original(store, binding)
            if not changed:
                target = store.root / self.bundle["files"]["sources"]["artifact"]["path"]
                target.chmod(0o600)
                target.write_bytes(b"corrupted after readiness")
                changed.append(True)
            return revision

        with patch.object(zenodo, "_current", check_then_corrupt):
            with self.assertRaises(ResearchError):
                zenodo.continue_deposit(self.store, intent["id"],
                                        _draft._build_zenodo_client("sandbox", "token"))
        self.assertEqual(self.fake_api.requests, [])
        self.assertIsNone(get_intent(self.store, intent["id"])["pending"])

    def test_unknown_upload_is_reconciled_by_incremental_md5_without_resending(self):
        intent = self.begin()
        expected_md5 = hashlib.md5(self.pdf).hexdigest()

        class LostUpload(_FakeZenodoApi):
            lost = False

            def __call__(api, request):
                if request.get_method() == "GET" and request.full_url.endswith("/deposit/depositions/4242"):
                    api.requests.append(request)
                    return {"id": 4242, "submitted": False,
                            "files": [{"filename": "paper.pdf", "checksum": "md5:" + expected_md5}]}
                answer = super().__call__(request)
                if request.full_url.endswith("/files/bucket-1/paper.pdf") and not api.lost:
                    api.lost = True
                    raise urllib.error.URLError("response lost after upload")
                return answer

        api = LostUpload()
        client = _draft._build_zenodo_client("sandbox", "token")
        md5 = hashlib.md5

        def incrementally(data=b"", **kwargs):
            self.assertEqual(data, b"", "MD5 must receive payload through incremental updates")
            return md5(data, **kwargs)

        with patch.object(_draft, "_open_url", api):
            with self.assertRaises(SystemExit):
                zenodo.continue_deposit(self.store, intent["id"], client)
            self.assertEqual(get_intent(self.store, intent["id"])["pending"]["name"], "upload:paper.pdf")
            with self.bound_payload_reads(), patch.object(hashlib, "md5", incrementally):
                zenodo.continue_deposit(self.store, intent["id"], client, reconcile=True)
        paper_requests = [r for r in api.requests if r.full_url.endswith("/files/bucket-1/paper.pdf")]
        self.assertEqual(len(paper_requests), 1)
        saved = get_intent(self.store, intent["id"])
        self.assertEqual(saved["responses"]["upload:paper.pdf"]["observation"]["kind"], "remote_read")
        self.assertEqual(saved["status"], "complete")


class DirectStreamingTests(_PlainWorkspaceDepositTestCase):
    def test_direct_uploads_freeze_all_files_before_any_write_and_send_streams(self):
        pdf = b"%PDF-1.4\n" + b"paper\x00" * 500000
        sources = b"PK\x03\x04" + b"sources\xff" * 500000
        (self.workspace_dir / "draft/paper.pdf").write_bytes(pdf)
        (self.workspace_dir / "sources.zip").write_bytes(sources)
        api = _StreamingZenodoApi(self, {"paper.pdf": pdf, "supplementary-sources.zip": sources})
        original = Path.read_bytes

        def refuse_whole_payload(path):
            if path.name in ("paper.pdf", "sources.zip"):
                raise AssertionError("direct payload must be frozen with bounded reads")
            return original(path)

        # Replacing source files once create starts must not alter either upload.
        def send(request):
            if request.get_method() == "POST":
                (self.workspace_dir / "draft/paper.pdf").write_bytes(b"later paper")
                (self.workspace_dir / "sources.zip").write_bytes(b"later sources")
            return api(request)

        with patch.object(Path, "read_bytes", refuse_whole_payload), patch.object(_draft, "_open_url", send):
            self._deposit(["--creator", "Example, Author", "--sources", "sources.zip"])
        self.assertEqual(api.bodies, api.expected)
        self.assertTrue(all(stream.closed for stream in api.streams))

    def test_direct_stream_requests_have_explicit_lengths(self):
        pdf = (self.workspace_dir / "draft/paper.pdf").read_bytes()
        api = _StreamingZenodoApi(self, {"paper.pdf": pdf})
        with patch.object(_draft, "_open_url", api):
            self._deposit(["--creator", "Example, Author"])
        self.assertEqual(api.bodies, {"paper.pdf": pdf})
