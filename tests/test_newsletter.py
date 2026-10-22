"""Tests for newsletter generation over a timeframe."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from src.processors.newsletter import filter_by_timeframe, generate_newsletter
from src.server import NewsletterManager
from src.storage.json_storage import load_newsletters, newsletter_slug, save_newsletter


ITEMS = [
    {"title": "OpenAI ships model", "url": "https://o.com/1", "company": "OpenAI",
     "source_name": "OpenAI News", "published_at": "2026-07-20", "signal_score": 80,
     "primary_topic": "Foundation Models", "signal_type": "official_news",
     "personal_relevance_score": 0, "summary": "s"},
    {"title": "cline agent", "url": "https://gh.com/2", "company": "GitHub",
     "source_name": "GitHub", "published_at": "2026-07-22", "signal_score": 70,
     "primary_topic": "AI Coding Agents", "signal_type": "open_source_trend",
     "trend_score": 60, "personal_relevance_score": 40, "summary": "s"},
    {"title": "Vecchia notizia", "url": "https://x.com/3", "company": "Example",
     "source_name": "Example", "published_at": "2026-06-01", "signal_score": 90,
     "primary_topic": "General AI", "signal_type": "official_news",
     "personal_relevance_score": 0, "summary": "s"},
]


class FilterTimeframeTest(unittest.TestCase):
    def test_keeps_only_items_inside_period(self) -> None:
        selected = filter_by_timeframe(ITEMS, "2026-07-19", "2026-07-25")
        self.assertEqual({i["url"] for i in selected}, {"https://o.com/1", "https://gh.com/2"})

    def test_falls_back_to_collected_at(self) -> None:
        items = [{"url": "https://a.com", "published_at": "", "collected_at": "2026-07-21"}]
        self.assertEqual(len(filter_by_timeframe(items, "2026-07-20", "2026-07-22")), 1)

    def test_item_without_any_date_is_excluded(self) -> None:
        items = [{"url": "https://a.com", "published_at": "", "collected_at": ""}]
        self.assertEqual(filter_by_timeframe(items, "2026-07-20", "2026-07-22"), [])


class GenerateNewsletterTest(unittest.TestCase):
    class _StubAnalyzer:
        calls = 1

        def run_structured(self, prompt, schema):
            return {"intro": "Intro LLM", "big": "Brief big LLM"}

        def usage_summary(self):
            return {"calls": 1, "total_cost_usd": 0.06, "input_tokens": 200, "output_tokens": 90}

    def test_fallback_texts_without_llm(self) -> None:
        nl = generate_newsletter(ITEMS, date_from="2026-07-19", date_to="2026-07-25", llm_enabled=False)
        self.assertEqual(nl["item_count"], 2)
        self.assertEqual([s["key"] for s in nl["sections"]], ["big", "perte", "tools"])
        self.assertIn("2 notizie nel periodo", nl["intro"])

    def test_sections_contain_expected_items(self) -> None:
        nl = generate_newsletter(ITEMS, date_from="2026-07-19", date_to="2026-07-25", llm_enabled=False)
        by_key = {s["key"]: s for s in nl["sections"]}
        self.assertEqual(by_key["big"]["items"][0]["title"], "OpenAI ships model")
        self.assertEqual(by_key["perte"]["items"][0]["title"], "cline agent")
        self.assertEqual(by_key["tools"]["items"][0]["title"], "cline agent")

    def test_tools_section_ranked_by_trend_score_like_the_dashboard(self) -> None:
        # Regression test: the newsletter used to rank "tools" by
        # signal_score, discarding the trend_score ordering group_by_section
        # already applied -- inconsistent with the dashboard's own "tools"
        # tab, which sorts by trend_score.
        items = [
            {"title": "Low trend, high signal", "url": "https://gh.com/a", "company": "GitHub",
             "source_name": "GitHub", "published_at": "2026-07-20",
             "signal_type": "open_source_trend", "signal_score": 95, "trend_score": 10,
             "personal_relevance_score": 0, "summary": "s"},
            {"title": "High trend, low signal", "url": "https://gh.com/b", "company": "GitHub",
             "source_name": "GitHub", "published_at": "2026-07-21",
             "signal_type": "open_source_trend", "signal_score": 20, "trend_score": 90,
             "personal_relevance_score": 0, "summary": "s"},
        ]
        nl = generate_newsletter(items, date_from="2026-07-19", date_to="2026-07-25", llm_enabled=False)
        by_key = {s["key"]: s for s in nl["sections"]}
        self.assertEqual(by_key["tools"]["items"][0]["title"], "High trend, low signal")

    def test_llm_texts_used_with_per_section_fallback(self) -> None:
        nl = generate_newsletter(ITEMS, date_from="2026-07-19", date_to="2026-07-25",
                                 llm_enabled=True, analyzer=self._StubAnalyzer())
        by_key = {s["key"]: s for s in nl["sections"]}
        self.assertEqual(nl["intro"], "Intro LLM")
        self.assertEqual(by_key["big"]["brief"], "Brief big LLM")
        self.assertEqual(by_key["perte"]["brief"], "")  # missing key stays empty

    def test_empty_period_still_returns_structure(self) -> None:
        nl = generate_newsletter(ITEMS, date_from="2026-01-01", date_to="2026-01-05", llm_enabled=False)
        self.assertEqual(nl["item_count"], 0)
        self.assertIn("Nessuna notizia", nl["intro"])


class NewsletterStorageTest(unittest.TestCase):
    def test_save_and_load_editions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            save_newsletter({"date_from": "2026-07-19", "date_to": "2026-07-25", "title": "T"}, folder)
            editions = load_newsletters(folder)
            self.assertIn("2026-07-19_2026-07-25", editions)
            self.assertEqual(editions["2026-07-19_2026-07-25"]["title"], "T")

    def test_newsletter_slug_strips_path_traversal_characters(self) -> None:
        # Defense in depth: even if an unvalidated date_from/date_to ever
        # reached here, the slug can never escape the newsletters directory.
        slug = newsletter_slug("../../../etc/passwd", "2026-07-25")
        self.assertNotIn("/", slug)
        self.assertNotIn(".", slug)
        self.assertEqual(slug, "_2026-07-25")

    def test_save_newsletter_with_traversal_dates_stays_inside_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir) / "newsletters"
            outside_marker = Path(tmp_dir) / "escaped.json"

            saved_path = save_newsletter(
                {"date_from": "../../escaped", "date_to": "x", "title": "T"}, folder
            )

            self.assertFalse(outside_marker.exists())
            self.assertEqual(saved_path.resolve().parent, folder.resolve())


class NewsletterManagerTest(unittest.TestCase):
    def test_generates_and_caches(self) -> None:
        manager = NewsletterManager(
            generator=lambda f, t: {"date_from": f, "date_to": t, "title": "Edizione", "sections": []}
        )
        manager._results.clear()
        self.assertEqual(manager.start("2026-07-19", "2026-07-25"), "running")
        deadline = time.time() + 2
        while time.time() < deadline and manager.get("2026-07-19", "2026-07-25")["status"] != "done":
            time.sleep(0.01)
        state = manager.get("2026-07-19", "2026-07-25")
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["title"], "Edizione")
        self.assertEqual(manager.start("2026-07-19", "2026-07-25"), "done")  # cached

    def test_rejects_new_jobs_as_busy_once_at_the_concurrency_limit(self) -> None:
        import threading

        from src.server import MAX_CONCURRENT_NEWSLETTERS

        gate = threading.Event()

        def blocking_generator(date_from: str, date_to: str) -> dict:
            gate.wait(2)
            return {"date_from": date_from, "date_to": date_to, "title": "Edizione", "sections": []}

        manager = NewsletterManager(generator=blocking_generator)
        manager._results.clear()

        periods = [(f"2026-0{i + 1}-01", f"2026-0{i + 1}-07") for i in range(MAX_CONCURRENT_NEWSLETTERS)]
        for date_from, date_to in periods:
            self.assertEqual(manager.start(date_from, date_to), "running")

        self.assertEqual(manager.start("2026-12-01", "2026-12-07"), "busy")

        gate.set()
        for date_from, date_to in periods:
            deadline = time.time() + 2
            while time.time() < deadline and manager.get(date_from, date_to)["status"] != "done":
                time.sleep(0.01)
            self.assertEqual(manager.get(date_from, date_to)["status"], "done")


if __name__ == "__main__":
    unittest.main()
