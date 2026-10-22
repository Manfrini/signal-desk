"""Tests for CLI behavior."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src.main import collect_sources
from src.storage.json_storage import load_json


COLLECT_RESULT = {
    "collected_items": [{"title": "Item", "url": "https://example.com"}],
    "collection_errors": [],
}


class MainCliTest(unittest.TestCase):
    @patch("src.collectors.collector_service.CollectorService.collect")
    @patch("src.storage.json_storage.save_raw_news")
    def test_collect_dry_run_does_not_save(self, save_raw_news, collect) -> None:
        collect.return_value = COLLECT_RESULT
        output = io.StringIO()

        with redirect_stdout(output):
            collect_sources(dry_run=True)

        save_raw_news.assert_not_called()
        self.assertIn("Collection dry-run complete.", output.getvalue())
        self.assertIn("Items collected: 1", output.getvalue())

    @patch("src.pipeline.daily_pipeline._load_existing_items", return_value=[])
    @patch("src.storage.json_storage.mark_read_urls")
    @patch("src.storage.json_storage.save_raw_news")
    @patch("src.collectors.collector_service.CollectorService.collect")
    def test_collect_without_dry_run_saves_raw_news(
        self, collect, save_raw_news, mark_read_urls, load_existing_items
    ) -> None:
        collect.return_value = COLLECT_RESULT
        save_raw_news.return_value = "data/2026-05-05/raw_news.json"
        mark_read_urls.return_value = "data/read_urls.json"
        output = io.StringIO()

        with redirect_stdout(output):
            collect_sources(dry_run=False)

        # collected_items now goes through the same-day merge (empty history
        # here, so only the "new" tag changes) before being saved.
        merged_items = [{**COLLECT_RESULT["collected_items"][0], "run_status": "new"}]
        save_raw_news.assert_called_once_with(merged_items)
        mark_read_urls.assert_called_once_with(merged_items, "data/read_urls.json")
        self.assertIn("Collection complete.", output.getvalue())
        self.assertIn("Saved raw news: data/2026-05-05/raw_news.json", output.getvalue())

    def test_running_collect_twice_in_a_day_merges_instead_of_overwriting(self) -> None:
        # Regression test for the exact bug: `collect` used to overwrite
        # raw_news.json with only the current run's delta, so a second
        # invocation on the same day silently dropped the first run's items.
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw_news.json"

            def make_result(url: str) -> dict:
                return {
                    "collected_items": [{"title": url, "url": url}],
                    "collection_errors": [],
                }

            with patch("src.storage.json_storage.raw_news_path", return_value=raw_path), \
                 patch("src.storage.json_storage.mark_read_urls", return_value="ignored"), \
                 patch("src.storage.json_storage.update_trend_state_from_items", return_value="ignored"), \
                 patch(
                     "src.collectors.collector_service.CollectorService.collect",
                     side_effect=[make_result("https://example.com/first"), make_result("https://example.com/second")],
                 ):
                collect_sources(dry_run=False)
                collect_sources(dry_run=False)

            saved = load_json(raw_path)
            urls = {item["url"] for item in saved}
            self.assertEqual(urls, {"https://example.com/first", "https://example.com/second"})


if __name__ == "__main__":
    unittest.main()
