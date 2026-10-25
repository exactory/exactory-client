"""CSS URL boundaries preserve quoted resources without inventing nested dependencies."""

import unittest
from html import escape
from pathlib import Path

from research_harness.errors import ResearchError
from research_harness.html_visuals import resource_inventory, validate_visual


PAGE = "https://pmc.ncbi.nlm.nih.gov/articles/PMC13104172/"
ORIGIN = "https://pmc.ncbi.nlm.nih.gov/"
CAPTION = "Authored figure caption."
SVG_DATA = "data:image/svg+xml,%3Csvg%3E%3Cg id='c' fill='url(%23c)'/%3E%3C/svg%3E"


class CssUrlTests(unittest.TestCase):
    def inventory(self, content):
        start = content.index(CAPTION)
        return resource_inventory(content, PAGE, {"start": start, "end": start + len(CAPTION)})

    def figure(self, style=""):
        return ('<figure style="' + escape(style, quote=True) + '"><figcaption>' + CAPTION
                + '</figcaption></figure>')

    def assert_urls(self, style, urls):
        result = self.inventory(self.figure(style))
        self.assertEqual([r["url"] for r in result["resources"]], urls)
        self.assertEqual(result["pending"], [])

    def svg(self, style):
        return ('<svg xmlns="http://www.w3.org/2000/svg"><path style="' + escape(style, quote=True)
                + '"/></svg>').encode()

    def test_double_quoted_url_contains_single_quote(self):
        self.assert_urls('background:url("https://assets.example.org/one\'two.png")',
                         ["https://assets.example.org/one'two.png"])

    def test_single_quoted_url_contains_double_quote(self):
        self.assert_urls("background:url('https://assets.example.org/one\"two.png')",
                         ['https://assets.example.org/one"two.png'])

    def test_quoted_url_contains_closing_parenthesis(self):
        for quote in ('"', "'"):
            with self.subTest(quote=quote):
                self.assert_urls('background:url(' + quote + 'https://assets.example.org/one)two.png' + quote + ')',
                                 ['https://assets.example.org/one)two.png'])

    def test_unquoted_url_keeps_its_resource(self):
        self.assert_urls('background:url(https://assets.example.org/plain.png)',
                         ['https://assets.example.org/plain.png'])

    def test_whitespace_around_quoted_and_unquoted_values_is_preserved(self):
        for value in ('"https://assets.example.org/space.png"', "'https://assets.example.org/space.png'",
                      'https://assets.example.org/space.png'):
            with self.subTest(value=value):
                self.assert_urls('background:URL( \n\t' + value + '\t\n )',
                                 ['https://assets.example.org/space.png'])

    def test_several_urls_in_one_declaration_keep_every_resource(self):
        self.assert_urls('background:url(/alpha.png),url("/beta.png"),URL(\'/gamma.png\')',
                         [ORIGIN + 'alpha.png', ORIGIN + 'beta.png', ORIGIN + 'gamma.png'])

    def test_pmc_document_data_uri_does_not_invent_a_page_relative_image(self):
        content = (Path(__file__).parent / 'fixtures/research/pmc-quoted-svg-style.html').read_text(encoding='utf-8')
        result = self.inventory(content)
        self.assertEqual([r['url'] for r in result['resources']], ['https://images.example.org/figure.jpg'])
        self.assertEqual([p['code'] for p in result['pending']], ['visual_document_style_unsupported'] * 4)

    def test_selected_inline_data_uri_has_no_external_dependency(self):
        self.assert_urls('background-image:url("' + SVG_DATA + '")', [])

    def test_ancestor_inline_data_uri_has_no_external_dependency(self):
        content = ('<section style="' + escape('background:url("' + SVG_DATA + '")', quote=True) + '">'
                   + self.figure() + '</section>')
        result = self.inventory(content)
        self.assertEqual(result['resources'], [])
        self.assertEqual(result['pending'], [])

    def test_inline_svg_presentation_data_uri_has_no_external_dependency(self):
        content = ('<figure><svg><path fill="' + escape('url("' + SVG_DATA + '")', quote=True)
                   + '"/></svg><figcaption>' + CAPTION + '</figcaption></figure>')
        result = self.inventory(content)
        self.assertEqual(result['resources'], [])
        self.assertEqual(result['pending'], [])

    def test_standalone_svg_cannot_hide_external_url_behind_an_internal_url(self):
        for style in ('fill:url("https://assets.example.org/a\'url(#c)")',
                      'fill:url(\'https://assets.example.org/a"url(#c)\')'):
            with self.subTest(style=style):
                with self.assertRaises(ResearchError) as raised:
                    validate_visual(self.svg(style), 'image/svg+xml')
                self.assertEqual(raised.exception.code, 'visual_asset_dependencies_unsupported')

    def test_standalone_svg_keeps_internal_references_and_existing_style_guards(self):
        for style in ('fill:url(#c)', 'fill:url("#c")', "fill:url( '#c' )",
                      'fill:url(#c);stroke:url("#d");filter:url(\'#e\')'):
            with self.subTest(style=style):
                validate_visual(self.svg(style), 'image/svg+xml')
        for style in ('fill:url(/external.svg)', 'fill:url("https://assets.example.org/external.svg")',
                      'fill:u\\72l(#c)', 'fill:paint(url(#c))', '@import "/style.css"'):
            with self.subTest(style=style):
                with self.assertRaises(ResearchError) as raised:
                    validate_visual(self.svg(style), 'image/svg+xml')
                self.assertEqual(raised.exception.code, 'visual_asset_dependencies_unsupported')

    def test_document_style_keeps_ordinary_urls_and_its_pending_code(self):
        content = ('<style>.plot{background:url(/alpha.png),url("/beta.png"),url(\'/gamma.png\')}</style>'
                   + self.figure())
        result = self.inventory(content)
        self.assertEqual([r['url'] for r in result['resources']],
                         [ORIGIN + 'alpha.png', ORIGIN + 'beta.png', ORIGIN + 'gamma.png'])
        self.assertEqual([r['references'][0]['value'] for r in result['resources']],
                         ['/alpha.png', '/beta.png', '/gamma.png'])
        self.assertEqual([p['code'] for p in result['pending']], ['visual_document_style_unsupported'])
