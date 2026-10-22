"""Tests for HTML output generation."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.outputs.dashboard_output import DashboardOutput
from src.outputs.email_output import EmailOutput
from src.outputs.output_service import OutputService


ITEMS = [
    {
        "title": "AI update",
        "url": "https://example.com/original",
        "company": "Example",
        "category": "general_ai",
        "summary": "In breve: AI update",
        "published_at": "2026-05-05",
        "key_points": ["Useful point."],
        "content_quality": "full",
        "relevance_score": 5,
        "signal_score": 76,
        "summary_quality": "high",
        "primary_topic": "Foundation Models",
        "why_it_matters": "This affects model selection decisions.",
        "possible_action": "Add the update to the evaluation backlog.",
    }
]


class OutputGenerationTest(unittest.TestCase):
    def test_generators_preserve_original_urls(self) -> None:
        self.assertIn(
            'href="https://example.com/original"',
            DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS),
        )
        self.assertIn(
            'href="https://example.com/original"',
            EmailOutput(date=date(2026, 5, 5)).render_html(ITEMS),
        )

    def test_output_service_writes_preview_and_normal_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: false\n",
                encoding="utf-8",
            )
            service = OutputService(
                settings_path=settings_path,
                dashboard_dir=tmp_path / "dashboard",
                email_dir=tmp_path / "email",
                date=date(2026, 5, 5),
            )

            preview = service.generate(ITEMS, dry_run=True)
            normal = service.generate(ITEMS, dry_run=False)

            self.assertEqual(preview["dashboard_path"], tmp_path / "dashboard" / "preview_index.html")
            self.assertEqual(preview["email_html_path"].name, "preview_2026-05-05.html")
            self.assertEqual(preview["email_text_path"].name, "preview_2026-05-05.txt")
            self.assertEqual(preview["email_sending"], "skipped dry-run")
            self.assertEqual(normal["dashboard_path"], tmp_path / "dashboard" / "index.html")
            self.assertEqual(normal["email_html_path"].name, "2026-05-05.html")
            self.assertEqual(normal["email_text_path"].name, "2026-05-05.txt")
            self.assertEqual(normal["email_sending"], "disabled")

    def test_output_service_can_keep_email_items_separate_from_dashboard_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: false\n",
                encoding="utf-8",
            )
            service = OutputService(
                settings_path=settings_path,
                dashboard_dir=tmp_path / "dashboard",
                email_dir=tmp_path / "email",
                date=date(2026, 5, 5),
            )
            historical_item = {**ITEMS[0], "title": "Historical dashboard item"}
            current_item = {**ITEMS[0], "title": "Current newsletter item"}

            result = service.generate(
                [historical_item, current_item],
                dry_run=False,
                email_items=[current_item],
            )

            dashboard_html = result["dashboard_path"].read_text(encoding="utf-8")
            email_html = result["email_html_path"].read_text(encoding="utf-8")
            self.assertIn("Historical dashboard item", dashboard_html)
            self.assertIn("Current newsletter item", email_html)
            self.assertNotIn("Historical dashboard item", email_html)


if __name__ == "__main__":
    unittest.main()
