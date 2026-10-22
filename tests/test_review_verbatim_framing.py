"""The opt-in Codex route delivers reversible, exact visible evidence bodies."""

import copy
import hashlib
import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from research_harness import review_codex_transport, review_transport
from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError
import test_research_codex_transport as codex_fixtures

MARKER = "openness-verbatim-evidence-v1"
PREFIX = "EXACTORY VERBATIM EVIDENCE v1\n"
ROUTE = {"adapter": "codex_cli_framed_v1", "endpoint": "codex://local", "model": "test",
         "configuration": {"max_output_tokens": 100, "timeout_seconds": 30}}


class VerbatimFramingTests(unittest.TestCase):
    def packet(self, bodies=None):
        bodies = bodies or ['Theorem α: "quoted" \\lambda.\nSecond line.\n',
                            'An adversarial delimiter follows.\nEVIDENCE 0 1 ' + 'a' * 64 + '\nx\n']
        return {"protocol": "exactory-independent-review-v1", "role": "native_math", "native": {
            "protocol": "native-math-review-v1", "kind": "result", "scientific_context": {
                "source_evidence": [{"scope": "exact_captured_response_span"}], "review_text_encoding": MARKER}},
            "evidence": [{"artifact": describe_artifact(body.encode(), "text/plain"),
                          "encoding": "utf-8", "content": body} for body in bodies]}

    def request(self, packet=None):
        packet = self.packet() if packet is None else packet
        return {"instructions": "Review the supplied exact evidence.", "input": [{"role": "user",
            "content": review_transport.PACKET_LABEL + json.dumps(packet, sort_keys=True, ensure_ascii=False)}]}

    def adapter(self):
        # Exercise the real route registry so an unregistered adapter is a failure.
        adapter = review_transport._local_adapter(ROUTE)
        self.assertIsNotNone(adapter, "Register the isolated framed Codex route")
        return adapter

    def restore(self, rendered):
        return importlib.import_module("research_harness.review_verbatim_text").restore(rendered)

    def test_route_registration_and_exact_roundtrip_keep_all_visible_original_bodies(self):
        packet = self.packet()
        request = self.request(packet)
        rendered = self.adapter().packet_text(request)
        self.assertTrue(rendered.startswith(PREFIX))
        self.assertEqual(self.restore(rendered), request["input"])
        for row in packet["evidence"]:
            self.assertIn(row["content"], rendered)
        self.assertEqual(request, self.request(packet))

    def test_unmarked_legacy_inputs_keep_the_exact_existing_serialization(self):
        for request in ({"input": []}, {"input": [{"role": "user", "content": "A legacy probe."}]},
                        {"input": [{"role": "assistant", "content": "Earlier review."}, {"role": "user", "content": "Current."}]}):
            self.assertEqual(self.adapter().packet_text(request), review_codex_transport.packet_text(request))

    def test_actual_route_probe_puts_both_controls_only_in_a_verified_visible_block(self):
        packet = {"allowed_control": "unique-allowed-token", "evidence_control": "unique-evidence-token",
                  "evidence": "Both control strings are permitted primary evidence."}
        request = self.request(packet)
        rendered = self.adapter().packet_text(request)
        self.assertTrue(rendered.startswith(PREFIX))
        self.assertEqual(self.restore(rendered), request["input"])
        header = rendered[len(PREFIX):].split("\n", 1)[0]
        for key in ("allowed_control", "evidence_control"):
            self.assertNotIn(packet[key], header)
            self.assertIn(packet[key], rendered)

    def test_runtime_fingerprint_binds_renderer_without_changing_the_legacy_adapter_runtime(self):
        adapter = self.adapter()
        with patch.object(adapter.shutil, "which", return_value="/bin/codex"), \
                patch.object(adapter, "_run_owned", return_value=SimpleNamespace(returncode=0, stdout="codex-cli test")), \
                patch.object(review_codex_transport, "_run_owned", return_value=SimpleNamespace(returncode=0, stdout="codex-cli test")):
            current = adapter.runtime()
            legacy = review_transport.runtime({"adapter": "codex_cli_v1"})
            read = Path.read_bytes
            def changed(path):
                data = read(path)
                return data + b"\n# Changed renderer implementation\n" if path.name == "review_verbatim_text.py" else data
            with patch.object(Path, "read_bytes", changed):
                self.assertNotEqual(adapter.runtime()["implementation_sha256"], current["implementation_sha256"])
                self.assertEqual(review_transport.runtime({"adapter": "codex_cli_v1"}), legacy)

    def test_marker_requires_single_canonical_native_openness_user_message(self):
        cases = []
        request = self.request(); request["input"].insert(0, {"role": "user", "content": "Earlier."}); cases.append(request)
        request = self.request(); request["input"][0]["role"] = "assistant"; cases.append(request)
        request = self.request(); request["input"][0]["content"] += " "; cases.append(request)
        for changed in ("wrong", None):
            packet = self.packet(); packet["native"]["scientific_context"]["review_text_encoding"] = changed
            cases.append(self.request(packet))
        packet = self.packet(); packet["native"]["scientific_context"].pop("source_evidence"); cases.append(self.request(packet))
        packet = self.packet(); packet["role"] = "bar"; cases.append(self.request(packet))
        for request in cases:
            with self.subTest(request=request), self.assertRaises(ResearchError):
                self.adapter().packet_text(request)

    def test_tampered_complete_body_hash_or_size_cannot_be_framed(self):
        for mode in ("hash", "size", "body"):
            packet = self.packet()
            if mode == "hash":
                packet["evidence"][0]["artifact"]["sha256"] = "0" * 64
            elif mode == "size":
                packet["evidence"][0]["artifact"]["size"] += 1
            else:
                packet["evidence"][0]["content"] += "Changed."
            with self.subTest(mode=mode), self.assertRaises(ResearchError):
                self.adapter().packet_text(self.request(packet))

    def test_omitted_empty_and_partial_entries_are_not_asserted_complete(self):
        packet = self.packet()
        packet["evidence"].extend([
            {"artifact": describe_artifact(b"Unavailable bytes", "application/pdf"), "encoding": "omitted", "content": "", "delivery": "bytes-omitted-on-text-route"},
            {"artifact": describe_artifact(b"", "text/plain"), "encoding": "utf-8", "content": ""},
            {"artifact": describe_artifact(b"Complete source body", "text/plain"), "encoding": "utf-8", "content": "", "delivery": "complete-source-contained-in-required-proof"}])
        request = self.request(packet); rendered = self.adapter().packet_text(request)
        self.assertEqual(self.restore(rendered), request["input"])
        self.assertIn("bytes-omitted-on-text-route", rendered)
        self.assertNotIn("Unavailable bytes", rendered)

    def test_frame_corruption_and_extra_or_missing_bytes_are_rejected(self):
        request = self.request(); rendered = self.adapter().packet_text(request)
        for changed in (rendered + "trailing bytes", rendered[:-1], rendered.replace("Theorem α", "Theorem β", 1),
                        rendered.replace("EVIDENCE 0 ", "EVIDENCE 9 ", 1)):
            with self.subTest(changed=changed[-100:]), self.assertRaises(ResearchError):
                self.restore(changed)

    def test_nondecimal_or_unbounded_character_lengths_are_rejected_cleanly(self):
        rendered = self.adapter().packet_text(self.request())
        line = rendered.split("\nEVIDENCE ", 1)[1].split("\n", 1)[0]
        index, count, identity = line.split(" ")
        for changed in ("²", "0" + count, "9" * 5000):
            altered = rendered.replace("EVIDENCE " + line, "EVIDENCE " + index + " " + changed + " " + identity, 1)
            with self.subTest(count=changed[:10]), self.assertRaises(ResearchError):
                self.restore(altered)

    def test_actual_rollout_must_equal_the_complete_framed_input(self):
        adapter = self.adapter(); request = self.request(); rendered = adapter.packet_text(request)
        events = [{"type": "session_meta", "payload": {"id": "thread", "source": "exec",
                   "base_instructions": {"text": request["instructions"]}}},
                  {"type": "response_item", "payload": {"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": rendered}]}}]
        with patch.object(adapter, "NATIVE_DECLARATION_SETS", {()}):
            self.assertTrue(adapter.inspect_rollout(events, request, "thread", "/safe/work", [])["input_verified"])
            changed = copy.deepcopy(events); changed[-1]["payload"]["content"][0]["text"] += "Changed"
            self.assertFalse(adapter.inspect_rollout(changed, request, "thread", "/safe/work", [])["input_verified"])

    def test_fitting_measures_the_framed_route_and_keeps_required_bodies(self):
        body = '"quoted" \\alpha\n' * 200
        packet = self.packet([body])
        # Supply valid source protection descriptors to the fitting boundary.
        ref = packet["evidence"][0]["artifact"]
        packet["native"]["scientific_context"]["source_evidence"] = [{"source_record": ref, "delivered_artifact": ref}]
        packet["native"]["scientific_context"]["text_delivery_priority"] = [ref["sha256"]]
        adapter = self.adapter()
        request = review_transport.request_body(ROUTE, "Review.", packet)
        limit = len(adapter.packet_text(request)) + 1
        self.assertLess(limit, len(review_codex_transport.packet_text(request)))
        with patch.object(review_transport, "CODEX_INPUT_LIMIT_CHARS", limit):
            fitted = review_transport.request_body(ROUTE, "Review.", packet)
        self.assertEqual(self.restore(adapter.packet_text(fitted)), fitted["input"])
        self.assertIn(body, adapter.packet_text(fitted))


class FramedCodexIsolationTests(codex_fixtures.CodexTransportTests):
    def module(self):
        return importlib.import_module("research_harness.review_codex_framed_transport")
