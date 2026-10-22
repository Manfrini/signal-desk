"""Tests for processor service."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from src.processors.processor_service import MissingRawNewsError, ProcessorService
from src.storage.json_storage import load_json, raw_news_path, save_json


class ProcessorServiceTest(unittest.TestCase):
    def test_process_writes_news_json_from_raw_news(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            selected_date = date(2026, 5, 5)
            raw_path = raw_news_path(base_dir=tmp_dir, date=selected_date)
            save_json(
                raw_path,
                [
                    {
                        "title": "New image diffusion model",
                        "url": "https://example.com/image",
                        "source_id": "source",
                        "source_name": "Source",
                        "company": "Example",
                        "category_hint": "",
                        "published_at": "2026-05-05",
                        "collected_at": "2026-05-05T10:00:00+00:00",
                        "raw_summary": "Image generation launch",
                        "raw_content": "",
                    }
                ],
            )

            result = ProcessorService(base_dir=tmp_dir, date=selected_date).process()

            output_path = Path(tmp_dir) / "2026-05-05" / "news.json"
            self.assertEqual(result["output_path"], output_path)
            self.assertEqual(result["raw_count"], 1)
            self.assertEqual(result["processed_count"], 1)
            processed_items = load_json(output_path)
            self.assertEqual(processed_items[0]["url"], "https://example.com/image")
            self.assertEqual(processed_items[0]["category"], "image_generation")

    def test_process_raises_clear_error_when_raw_news_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(MissingRawNewsError) as context:
                ProcessorService(base_dir=tmp_dir, date=date(2026, 5, 5)).process()

            self.assertIn("Raw news file not found", str(context.exception))


class _StubAnalyzer:
    """Records the items it was asked to analyze and returns canned insights."""

    def __init__(self, insights: dict[str, dict[str, str]]) -> None:
        self.insights = insights
        self.analyzed_urls: list[str] = []

    def analyze_items(self, raw_items):
        self.analyzed_urls.extend(str(item.get("url", "")) for item in raw_items)
        return {
            url: insight
            for url, insight in self.insights.items()
            if any(str(item.get("url", "")) == url for item in raw_items)
        }


TODAY = date.today()


class ProcessorServiceLlmTest(unittest.TestCase):
    def _raw_item(self, url: str, published_at: str) -> dict:
        return {
            "title": "New coding agent launch",
            "url": url,
            "source_id": "source",
            "source_name": "Source",
            "company": "Example",
            "category_hint": "",
            "published_at": published_at,
            "collected_at": published_at,
            "raw_summary": "Coding agent launch",
            "raw_content": "",
        }

    def test_llm_insights_overlay_processed_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            selected_date = TODAY
            raw_items = [self._raw_item("https://example.com/a", TODAY.isoformat())]
            analyzer = _StubAnalyzer(
                {
                    "https://example.com/a": {
                        "summary": "Insight LLM",
                        "why_it_matters": "Conta perché LLM",
                        "possible_action": "Azione LLM",
                    }
                }
            )
            service = ProcessorService(
                base_dir=tmp_dir,
                date=selected_date,
                llm_enabled=True,
                analyzer=analyzer,
            )
            result = service.process(raw_items=raw_items)

            item = result["processed_items"][0]
            self.assertEqual(item["summary"], "Insight LLM")
            self.assertEqual(item["why_it_matters"], "Conta perché LLM")
            self.assertEqual(item["insight_source"], "claude_cli")
            self.assertEqual(analyzer.analyzed_urls, ["https://example.com/a"])
            # Cache file is written under base_dir for isolation.
            self.assertTrue((Path(tmp_dir) / "llm_insights.json").exists())

    def test_cached_items_are_not_reanalyzed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            selected_date = TODAY
            raw_items = [self._raw_item("https://example.com/a", TODAY.isoformat())]
            insights = {
                "https://example.com/a": {
                    "summary": "Insight LLM",
                    "why_it_matters": "Conta",
                    "possible_action": "Azione",
                }
            }
            # First run populates the cache.
            first = _StubAnalyzer(insights)
            ProcessorService(
                base_dir=tmp_dir,
                date=selected_date,
                llm_enabled=True,
                analyzer=first,
            ).process(raw_items=raw_items)

            # Second run must reuse the cache, not call the analyzer again.
            second = _StubAnalyzer(insights)
            result = ProcessorService(
                base_dir=tmp_dir,
                date=selected_date,
                llm_enabled=True,
                analyzer=second,
            ).process(raw_items=raw_items)

            self.assertEqual(second.analyzed_urls, [])
            self.assertEqual(result["processed_items"][0]["summary"], "Insight LLM")

    def test_changing_content_language_does_not_reuse_cached_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_items = [self._raw_item("https://example.com/a", TODAY.isoformat())]
            italian = _StubAnalyzer({"https://example.com/a": {"summary": "Sintesi", "why_it_matters": "Conta", "possible_action": "Agisci"}})
            ProcessorService(base_dir=tmp_dir, date=TODAY, llm_enabled=True, analyzer=italian, language="it").process(raw_items=raw_items)

            english = _StubAnalyzer({"https://example.com/a": {"summary": "Summary", "why_it_matters": "It matters", "possible_action": "Act"}})
            result = ProcessorService(base_dir=tmp_dir, date=TODAY, llm_enabled=True, analyzer=english, language="en").process(raw_items=raw_items)

            self.assertEqual(english.analyzed_urls, ["https://example.com/a"])
            self.assertEqual(result["processed_items"][0]["summary"], "Summary")

    def test_items_from_earlier_in_the_week_are_analyzed(self) -> None:
        # A run after a few days' pause must cover everything new since the
        # last run, not just the last 24 hours.
        with tempfile.TemporaryDirectory() as tmp_dir:
            three_days_ago = (TODAY - timedelta(days=3)).isoformat()
            raw_items = [self._raw_item("https://example.com/a", three_days_ago)]
            analyzer = _StubAnalyzer(
                {"https://example.com/a": {"why_it_matters": "Conta", "possible_action": "Azione"}}
            )
            result = ProcessorService(
                base_dir=tmp_dir,
                date=TODAY,
                llm_enabled=True,
                analyzer=analyzer,
            ).process(raw_items=raw_items)

            self.assertEqual(analyzer.analyzed_urls, ["https://example.com/a"])
            self.assertEqual(result["processed_items"][0]["why_it_matters"], "Conta")

    def test_lookback_wider_than_a_week_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = Path(tmp_dir) / "settings.yaml"
            settings_path.write_text("lookback_days: 30\n", encoding="utf-8")
            raw_items = [
                self._raw_item("https://example.com/old", (TODAY - timedelta(days=20)).isoformat())
            ]
            analyzer = _StubAnalyzer({})
            ProcessorService(
                base_dir=tmp_dir,
                date=TODAY,
                llm_enabled=True,
                analyzer=analyzer,
                settings_path=settings_path,
            ).process(raw_items=raw_items)

            self.assertEqual(analyzer.analyzed_urls, [])

    def test_old_items_keep_rule_based_insights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            selected_date = date(2026, 6, 10)
            raw_items = [self._raw_item("https://example.com/old", "2026-05-01")]
            analyzer = _StubAnalyzer({})
            result = ProcessorService(
                base_dir=tmp_dir,
                date=selected_date,
                llm_enabled=True,
                analyzer=analyzer,
            ).process(raw_items=raw_items)

            self.assertEqual(analyzer.analyzed_urls, [])
            self.assertNotIn("insight_source", result["processed_items"][0])

    def test_disabled_llm_keeps_rule_based_insights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_items = [self._raw_item("https://example.com/a", "2026-06-10")]
            analyzer = _StubAnalyzer(
                {"https://example.com/a": {"summary": "Insight LLM"}}
            )
            result = ProcessorService(
                base_dir=tmp_dir,
                date=date(2026, 6, 10),
                llm_enabled=False,
                analyzer=analyzer,
            ).process(raw_items=raw_items)

            self.assertEqual(analyzer.analyzed_urls, [])
            self.assertNotIn("insight_source", result["processed_items"][0])


class ProcessorServicePersonalRelevanceTest(unittest.TestCase):
    def test_personal_relevance_fields_added_from_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_items = [
                {
                    "title": "New agent orchestration framework",
                    "url": "https://example.com/agent",
                    "source_id": "source",
                    "source_name": "Source",
                    "company": "Example",
                    "published_at": "2026-06-10",
                    "collected_at": "2026-06-10",
                    "raw_summary": "agent orchestration",
                    "raw_content": "",
                }
            ]
            profile = {"keywords_boost": ["agent", "orchestration"]}
            result = ProcessorService(
                base_dir=tmp_dir,
                date=date(2026, 6, 10),
                llm_enabled=False,
                profile=profile,
            ).process(raw_items=raw_items)

            item = result["processed_items"][0]
            self.assertGreater(item["personal_relevance_score"], 0)
            self.assertIn("agent", item["personal_match"])

    def test_empty_profile_gives_zero_personal_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_items = [
                {
                    "title": "New agent orchestration framework",
                    "url": "https://example.com/agent",
                    "source_id": "source",
                    "source_name": "Source",
                    "company": "Example",
                    "published_at": "2026-06-10",
                    "collected_at": "2026-06-10",
                    "raw_summary": "agent",
                    "raw_content": "",
                }
            ]
            result = ProcessorService(
                base_dir=tmp_dir,
                date=date(2026, 6, 10),
                llm_enabled=False,
                profile={},
            ).process(raw_items=raw_items)

            self.assertEqual(result["processed_items"][0]["personal_relevance_score"], 0)

    def test_relevance_to_goal_populates_per_te_for_a_brand_new_profile(self) -> None:
        """A user with only free-text about_me — no keyword_boost matches yet
        — must still get a populated "Per te" section once the LLM has
        scored the item against their profile context (relevance_to_goal),
        closing the cold-start gap described in the handover plan."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_items = [
                {
                    "title": "Some AI news",
                    "url": "https://example.com/a",
                    "source_id": "source",
                    "source_name": "Source",
                    "company": "Example",
                    "published_at": TODAY.isoformat(),
                    "collected_at": TODAY.isoformat(),
                    "raw_summary": "Some AI news",
                    "raw_content": "",
                }
            ]
            analyzer = _StubAnalyzer(
                {"https://example.com/a": {"summary": "s", "relevance_to_goal": 5}}
            )
            # Empty keyword lists: only about_me was ever written.
            profile = {"about_me": "product analyst", "keywords_boost": []}
            result = ProcessorService(
                base_dir=tmp_dir,
                date=TODAY,
                llm_enabled=True,
                analyzer=analyzer,
                profile=profile,
            ).process(raw_items=raw_items)

            item = result["processed_items"][0]
            self.assertEqual(item["personal_match"], [])  # no keyword matched
            self.assertGreater(item["personal_relevance_score"], 0)  # yet it enters "Per te"

    def test_relevance_to_goal_of_three_is_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_items = [
                {
                    "title": "Some AI news",
                    "url": "https://example.com/a",
                    "source_id": "source",
                    "source_name": "Source",
                    "company": "Example",
                    "published_at": TODAY.isoformat(),
                    "collected_at": TODAY.isoformat(),
                    "raw_summary": "Some AI news",
                    "raw_content": "",
                }
            ]
            analyzer = _StubAnalyzer(
                {"https://example.com/a": {"summary": "s", "relevance_to_goal": 3}}
            )
            result = ProcessorService(
                base_dir=tmp_dir,
                date=TODAY,
                llm_enabled=True,
                analyzer=analyzer,
                profile={"keywords_boost": []},
            ).process(raw_items=raw_items)

            self.assertEqual(result["processed_items"][0]["personal_relevance_score"], 0)

    def test_rule_based_item_is_not_affected_by_a_stale_relevance_to_goal(self) -> None:
        # insight_source is only set by _apply_llm_insights; a hand-crafted
        # item claiming relevance_to_goal without going through the LLM path
        # (insight_source != "claude_cli") must not get a phantom boost.
        from src.processors.processor_service import _relevance_to_goal_boost

        self.assertEqual(_relevance_to_goal_boost({"relevance_to_goal": 5}), 0)
        self.assertEqual(
            _relevance_to_goal_boost({"relevance_to_goal": 5, "insight_source": "rule_based"}), 0
        )


if __name__ == "__main__":
    unittest.main()
