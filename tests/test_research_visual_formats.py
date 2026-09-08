"""Authored boundaries for static visual context and complete JPEG containers."""

import unittest
import struct
import zlib
from pathlib import Path

from research_harness.errors import ResearchError
from research_harness.html_visuals import resource_inventory, validate_visual


def png_stream(compressed, width=1, height=1):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


def no_image_containers():
    return (("image/gif", b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0, 0, 0) + b";"),
            ("image/webp", b"RIFF" + struct.pack("<I", 12) + b"WEBPVP8 " + struct.pack("<I", 0)),
            ("image/png", png_stream(b"")))


class VisualFormatTests(unittest.TestCase):
    def inventory(self, figure, before="", after="", anchor=None):
        content = before + figure + after
        anchor = figure if anchor is None else anchor
        start = content.index(anchor)
        return resource_inventory(content, "https://example.org/article.html",
                                  {"start": start, "end": start + len(anchor), "quote": anchor})

    def test_external_document_stylesheet_and_later_style_cannot_hide_from_caption(self):
        figure = '<figure><div class="plot">Plot</div><figcaption>Caption</figcaption></figure>'
        for before, after in (('<link rel="stylesheet" href="/plot.css">', ''),
                              ('', '<style>.plot { background-image: url(/plot.png) }</style>')):
            with self.subTest(before=before, after=after):
                result = self.inventory(figure, before, after, anchor="Caption")
                self.assertIn("visual_document_style_unsupported", {p["code"] for p in result["pending"]})

    def test_ancestor_style_resource_is_retained_outside_the_figure_anchor(self):
        figure = '<figure><div>Plot</div><figcaption>Caption</figcaption></figure>'
        result = self.inventory(figure, '<section style="background-image: url(/inherited.png)">', '</section>', anchor="Caption")
        self.assertIn("https://example.org/inherited.png", {r["url"] for r in result["resources"]})

    def test_inline_svg_uses_the_same_static_boundary_as_standalone_svg(self):
        for child in ('<animate attributeName="fill" values="red;blue"/>', '<animateMotion path="M0 0L1 1"/>',
                      '<animateTransform attributeName="transform" type="scale" values="1;2"/>', '<set attributeName="href" to="/later.png"/>',
                      '<discard begin="1s"/>', '<foreignObject><div>Dynamic material</div></foreignObject>',
                      '<path onclick="change()"/>', '<style>path { fill: url(/uncaptured.svg) }</style>'):
            with self.subTest(child=child):
                result = self.inventory('<figure><svg>' + child + '</svg></figure>')
                self.assertTrue(result["pending"])
                with self.assertRaises(ResearchError):
                    validate_visual(('<svg xmlns="http://www.w3.org/2000/svg">' + child + '</svg>').encode(), "image/svg+xml")

    def test_static_inline_svg_paths_text_and_literal_functions_remain_supported(self):
        result = self.inventory('<figure><svg><path d="M0 0L8 8" stroke="black"/><text x="0" y="8">h(x)</text></svg></figure>')
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["resources"], [])

    def test_authored_baseline_and_progressive_jpeg_containers_are_supported(self):
        for name in ("authored-grid.jpg", "authored-grid-progressive.jpg"):
            with self.subTest(name=name):
                validate_visual((Path(__file__).parent / "fixtures/research" / name).read_bytes(), "image/jpeg")

    def test_jpeg_requires_frame_scan_tables_and_nonempty_entropy_data(self):
        data = (Path(__file__).parent / "fixtures/research/authored-grid.jpg").read_bytes()
        frame, scan, table = (data.index(marker) for marker in (b"\xff\xc0", b"\xff\xda", b"\xff\xdb"))
        frame_end = frame + 2 + int.from_bytes(data[frame + 2:frame + 4], "big")
        scan_end = scan + 2 + int.from_bytes(data[scan + 2:scan + 4], "big")
        table_end = table + 2 + int.from_bytes(data[table + 2:table + 4], "big")
        huffman = data.index(b"\xff\xc4")
        huffman_end = huffman + 2 + int.from_bytes(data[huffman + 2:huffman + 4], "big")
        malformed = (b"\xff\xd8\xff\xff\xd9", data[:scan] + b"\xff\xd9", data[:scan_end] + b"\xff\xd9",
                     data[:frame] + data[frame_end:], data[:table] + data[table_end:], data[:-1],
                     data[:frame + 2] + b"\xff\xff" + data[frame + 4:],
                     data[:frame + 5] + b"\x00\x00" + data[frame + 7:], data + b"trailing",
                     data[:huffman] + data[huffman_end:])
        for index, body in enumerate(malformed):
            with self.subTest(index=index):
                with self.assertRaises(ResearchError) as raised:
                    validate_visual(body, "image/jpeg")
                self.assertEqual(raised.exception.code, "malformed_visual_asset")

    def test_every_truncated_prefix_of_the_authored_jpegs_has_a_typed_failure(self):
        for name in ("authored-grid.jpg", "authored-grid-progressive.jpg"):
            data = (Path(__file__).parent / "fixtures/research" / name).read_bytes()
            for end in range(len(data)):
                with self.subTest(name=name, end=end):
                    with self.assertRaises(ResearchError) as raised:
                        validate_visual(data[:end], "image/jpeg")
                    self.assertEqual(raised.exception.code, "malformed_visual_asset")

    def test_other_supported_containers_cannot_omit_the_actual_image_payload(self):
        for media_type, data in no_image_containers():
            with self.subTest(media_type=media_type):
                with self.assertRaises(ResearchError) as raised:
                    validate_visual(data, media_type)
                self.assertEqual(raised.exception.code, "malformed_visual_asset")

    def test_authored_gif_and_lossy_and_lossless_webp_remain_supported(self):
        for name, media_type in (("authored-grid.gif", "image/gif"), ("authored-grid.webp", "image/webp"),
                                 ("authored-grid-lossless.webp", "image/webp")):
            with self.subTest(name=name):
                validate_visual((Path(__file__).parent / "fixtures/research" / name).read_bytes(), media_type)

    def test_png_requires_a_complete_compressed_scanline_stream(self):
        valid = zlib.compress(b"\x00\xff\x00\x80")
        validate_visual(png_stream(valid), "image/png")
        for compressed in (b"", zlib.compress(b""), valid[:-1], valid + b"trailing", zlib.compress(b"\x00\xff")):
            with self.subTest(compressed=compressed):
                with self.assertRaises(ResearchError) as raised:
                    validate_visual(png_stream(compressed), "image/png")
                self.assertEqual(raised.exception.code, "malformed_visual_asset")

    def test_png_structural_check_retains_a_bounded_unverifiable_state(self):
        with self.assertRaises(ResearchError) as raised:
            validate_visual(png_stream(zlib.compress(b"\x00"), width=10000, height=10000), "image/png")
        self.assertEqual(raised.exception.code, "unsupported_visual_asset")

    def test_webp_extended_header_requires_its_complete_image_chunk(self):
        image = (Path(__file__).parent / "fixtures/research/authored-grid-lossless.webp").read_bytes()[12:]
        extended = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00\x07\x00\x00\x07\x00\x00"
        def container(chunks):
            return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WEBP" + chunks
        validate_visual(container(extended + image), "image/webp")
        for chunks in (extended, b"VP8L" + struct.pack("<I", 5) + image[8:13] + b"\x00",
                       image[:4] + struct.pack("<I", len(image) + 1) + image[8:]):
            with self.subTest(chunks=chunks):
                with self.assertRaises(ResearchError) as raised:
                    validate_visual(container(chunks), "image/webp")
                self.assertEqual(raised.exception.code, "malformed_visual_asset")

    def test_gif_image_descriptor_still_needs_a_complete_nonempty_data_block(self):
        data = (Path(__file__).parent / "fixtures/research/authored-grid.gif").read_bytes()
        table_end = 13 + (3 * 2 ** ((data[10] & 7) + 1) if data[10] & 128 else 0)
        image = data.index(b",", table_end)
        payload = image + 10
        if data[image + 9] & 128:
            payload += 3 * 2 ** ((data[image + 9] & 7) + 1)
        payload += 1  # The minimum LZW code size precedes the sub-blocks.
        for body in (data[:payload] + b"\x00;", data[:payload] + b"\xff;", data[:-1]):
            with self.subTest(body=body):
                with self.assertRaises(ResearchError) as raised:
                    validate_visual(body, "image/gif")
                self.assertEqual(raised.exception.code, "malformed_visual_asset")
