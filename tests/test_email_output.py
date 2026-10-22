"""Tests for Italian email output."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.outputs.email_output import EmailOutput, _safe_href


def item(index: int, category: str = "general_ai") -> dict[str, object]:
    return {
        "title": f"Notizia {index}",
        "url": f"https://example.com/{index}",
        "company": "Example",
        "source_name": "Example Source",
        "category": category,
        "summary": f"Sommario {index}",
        "key_points": [f"Punto {index}"],
        "content_quality": "full",
        "relevance_score": 5,
        "signal_score": 80 - index,
        "summary_quality": "high",
        "primary_topic": "Foundation Models",
        "why_it_matters": f"Why {index}",
        "possible_action": f"Action {index}",
    }


class EmailOutputTest(unittest.TestCase):
    def test_generates_html_and_text_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = EmailOutput(output_dir=tmp_dir, date=date(2026, 5, 5))

            paths = output.generate([item(1)], preview=False)

            self.assertEqual(paths.html_path, Path(tmp_dir) / "2026-05-05.html")
            self.assertEqual(paths.text_path, Path(tmp_dir) / "2026-05-05.txt")
            self.assertTrue(paths.html_path.exists())
            self.assertTrue(paths.text_path.exists())

    def test_title_and_original_links_are_rendered(self) -> None:
        output = EmailOutput(date=date(2026, 5, 5))

        html = output.render_html([item(1)])
        text = output.render_text([item(1)])

        self.assertIn("AI Daily Intelligence Brief — 2026-05-05", html)
        self.assertIn('href="https://example.com/1"', html)
        self.assertIn("AI Daily Intelligence Brief — 2026-05-05", text)
        self.assertIn("Link: https://example.com/1", text)
        self.assertIn("Why it matters", html)
        self.assertIn("Possible action", text)

    def test_javascript_url_in_item_renders_as_hash_not_executable_href(self) -> None:
        output = EmailOutput(date=date(2026, 5, 5))
        malicious_item = {**item(1), "url": "javascript:alert(document.cookie)"}

        html = output.render_html([malicious_item])

        self.assertNotIn('href="javascript:', html)

    def test_safe_href_rejects_non_http_schemes(self) -> None:
        self.assertEqual(_safe_href("https://example.com/x"), "https://example.com/x")
        self.assertEqual(_safe_href("javascript:alert(1)"), "#")
        self.assertEqual(_safe_href("data:text/html,<script>alert(1)</script>"), "#")
        self.assertEqual(_safe_href(""), "#")

    def test_top_signals_are_limited_to_five_items(self) -> None:
        output = EmailOutput(date=date(2026, 5, 5))
        items = [item(index, "coding_ai") for index in range(1, 8)]

        html = output.render_html(items)
        text = output.render_text(items)

        self.assertIn("Top Signals Today", html)
        self.assertIn("TOP SIGNALS TODAY", text)
        self.assertIn("Notizia 5", html)
        top_section = html.split("Strategic Themes")[0]
        self.assertNotIn("Notizia 6", top_section)

    def test_preview_paths_are_clearly_named(self) -> None:
        output = EmailOutput(output_dir="outputs/email", date=date(2026, 5, 5))

        paths = output.paths(preview=True)

        self.assertEqual(paths.html_path, Path("outputs/email") / "preview_2026-05-05.html")
        self.assertEqual(paths.text_path, Path("outputs/email") / "preview_2026-05-05.txt")

    def test_invalid_items_are_excluded(self) -> None:
        output = EmailOutput(date=date(2026, 5, 5))
        invalid = {**item(99), "content_quality": "invalid"}

        html = output.render_html([item(1), invalid])

        self.assertIn("Notizia 1", html)
        self.assertNotIn("Notizia 99", html)

    def test_html_uses_mm_palette_no_indigo_no_external_assets(self) -> None:
        output = EmailOutput(date=date(2026, 5, 5))
        html = output.render_html([item(1)])

        # No leftover indigo brand color from the old dashboard-shared palette.
        self.assertNotIn("#4f46e5", html.lower())
        self.assertNotIn("#4338ca", html.lower())
        self.assertNotIn("#eef2ff", html.lower())
        # No CSS files, stylesheets, web fonts, JS, or SVG: email clients vary
        # wildly in support, so everything must be plain inline-styled HTML.
        self.assertNotIn("<link", html.lower())
        self.assertNotIn("<style", html.lower())
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<svg", html.lower())
        self.assertNotIn("tokens.css", html)
        self.assertNotIn("ui-kit.css", html)
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("cdn.", html.lower())
        # MM email-safe palette present, inlined.
        for color in ("#FAF9F7", "#FFFFFF", "#1A1B1E", "#5E6169", "#0F4C81", "#E7EFF7", "#E4E1DB"):
            self.assertIn(color, html)
        self.assertIn("Arial, Helvetica, sans-serif", html)

    def test_html_is_table_based_and_email_safe(self) -> None:
        output = EmailOutput(date=date(2026, 5, 5))
        html = output.render_html([item(1)])

        self.assertIn('role="presentation"', html)
        self.assertIn('width="600"', html)
        self.assertIn("bgcolor=", html)
        # Structural tags that many email clients strip or mishandle.
        self.assertNotIn("<header", html.lower())
        self.assertNotIn("<main", html.lower())
        self.assertNotIn("<article", html.lower())
        self.assertNotIn("<section", html.lower())
        self.assertNotIn("border-radius", html)
        self.assertNotIn("margin:0 auto", html.replace(" ", ""))

    def test_text_output_is_unaffected_by_html_palette_change(self) -> None:
        output = EmailOutput(date=date(2026, 5, 5))
        text = output.render_text([item(1), {**item(2), "signal_score": 55}])

        # render_text was intentionally left untouched: plain text, no HTML,
        # no colors, same sections/order/escaping/original URLs as before.
        self.assertNotIn("<", text)
        self.assertIn("AI Daily Intelligence Brief — 2026-05-05", text)
        self.assertIn("TOP SIGNALS TODAY", text)
        self.assertIn("STRATEGIC THEMES", text)
        self.assertIn("Link: https://example.com/1", text)

    def test_executive_sections_are_rendered(self) -> None:
        output = EmailOutput(date=date(2026, 5, 5))
        html = output.render_html([item(1), {**item(2), "signal_score": 55}])
        text = output.render_text([item(1), {**item(2), "signal_score": 55}])

        self.assertIn("Strategic Themes", html)
        self.assertIn("Worth Watching", html)
        self.assertIn("STRATEGIC THEMES", text)
        self.assertIn("WORTH WATCHING", text)


if __name__ == "__main__":
    unittest.main()
