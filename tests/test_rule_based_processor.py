"""Tests for the rule-based processor."""

from __future__ import annotations

import unittest

from src.processors.rule_based_processor import PROCESSED_ITEM_KEYS, RuleBasedProcessor


RAW_ITEM = {
    "title": "GitHub launches Copilot coding agent",
    "url": "https://example.com/original",
    "source_id": "github_copilot_changelog",
    "source_name": "GitHub Copilot Changelog",
    "company": "GitHub",
    "category_hint": "coding_ai",
    "published_at": "2026-05-05",
    "collected_at": "2026-05-05T10:00:00+00:00",
    "raw_summary": "Developer tooling update.",
    "excerpt": "GitHub Copilot now supports enterprise-managed coding agent workflows, allowing developer teams to review code faster and govern agent activity.",
    "raw_content": "GitHub Copilot now supports enterprise-managed coding agent workflows, allowing developer teams to review code faster and govern agent activity. The update helps teams standardize AI-assisted development and measure pull request workflow adoption.",
    "content_quality": "full",
    "date_quality": "reliable",
}


class RuleBasedProcessorTest(unittest.TestCase):
    def test_preserves_original_url_and_required_schema(self) -> None:
        processed = RuleBasedProcessor().process_item(RAW_ITEM)

        self.assertEqual(processed["url"], "https://example.com/original")
        self.assertEqual(set(processed), set(PROCESSED_ITEM_KEYS))

    def test_uses_category_hint_when_available(self) -> None:
        processed = RuleBasedProcessor().process_item(RAW_ITEM)

        self.assertEqual(processed["category"], "coding_ai")

    def test_keyword_fallback_when_category_hint_missing(self) -> None:
        raw_item = {
            **RAW_ITEM,
            "category_hint": "",
            "title": "New music and voice model for songs",
            "raw_summary": "",
            "excerpt": "",
            "raw_content": "",
        }

        processed = RuleBasedProcessor().process_item(raw_item)

        self.assertEqual(processed["category"], "music_generation")

    def test_summary_key_points_and_intelligence_fields_are_generated(self) -> None:
        processed = RuleBasedProcessor().process_item(RAW_ITEM)

        self.assertNotIn("Notizia da GitHub:", processed["summary"])
        self.assertNotIn("In breve:", processed["summary"])
        self.assertGreaterEqual(len(processed["key_points"]), 1)
        self.assertIn("coding_ai", processed["tags"])
        self.assertEqual(processed["content_quality"], "full")
        self.assertEqual(processed["primary_topic"], "AI Coding Agents")
        self.assertGreater(processed["signal_score"], 0)
        # Without an LLM insight these stay empty rather than repeating a
        # canned per-topic sentence across every article.
        self.assertEqual(processed["possible_action"], "")
        self.assertEqual(processed["why_it_matters"], "")

    def test_relevance_score_for_core_model_api_update(self) -> None:
        processed = RuleBasedProcessor().process_item(
            {
                **RAW_ITEM,
                "category_hint": "",
                "title": "New API model launch improves reasoning",
                "excerpt": "The API model launch improves reasoning and multimodal developer workflows.",
            }
        )

        self.assertEqual(processed["relevance_score"], 5)

    def test_relevance_score_for_startup_founder_content(self) -> None:
        processed = RuleBasedProcessor().process_item(
            {
                **RAW_ITEM,
                "category_hint": "business_product",
                "title": "How this founder closed a seed round without a pitch deck",
                "excerpt": "The founder shares fundraising lessons for early-stage startups building a go-to-market plan.",
                "raw_summary": "",
                "raw_content": "",
            }
        )

        self.assertEqual(processed["relevance_score"], 4)
        self.assertNotEqual(processed["content_quality"], "invalid")

    def test_relevance_score_one_marks_noise_invalid(self) -> None:
        processed = RuleBasedProcessor().process_item(
            {
                **RAW_ITEM,
                "category_hint": "",
                "title": "Company picnic photos",
                "excerpt": "The team gathered for a picnic in the park with employees and families.",
                "raw_summary": "",
                "raw_content": "",
            }
        )

        self.assertEqual(processed["relevance_score"], 1)
        self.assertEqual(processed["content_quality"], "invalid")

    def test_tags_are_capped_to_three_and_primary_category_is_single_value(self) -> None:
        processed = RuleBasedProcessor().process_item(
            {
                **RAW_ITEM,
                "category_hint": "",
                "title": "Copilot image music research model API launch",
                "excerpt": "Developer coding tools add image, music, research, pricing, and assistant capabilities.",
                "raw_content": " ".join(["video diffusion audio benchmark enterprise multimodal"] * 1200),
            }
        )

        self.assertIsInstance(processed["category"], str)
        self.assertLessEqual(len(processed["tags"]), 3)

    def test_boilerplate_summary_fallback_is_low_quality(self) -> None:
        processed = RuleBasedProcessor().process_item(
            {
                **RAW_ITEM,
                "title": "Anthropic launches a Claude update",
                "excerpt": "Anthropic is an AI safety and research company based in San Francisco.",
                "raw_summary": "",
                "raw_content": "",
                "content_quality": "excerpt",
            }
        )

        self.assertEqual(processed["summary"], "Anthropic launches a Claude update")
        self.assertEqual(processed["summary_quality"], "low")
        self.assertEqual(processed["content_quality"], "title_only")

    def test_signal_score_and_topic_are_added(self) -> None:
        processed = RuleBasedProcessor().process_item(RAW_ITEM)

        self.assertIsInstance(processed["signal_score"], int)
        self.assertIn(processed["primary_topic"], {"AI Coding Agents", "Enterprise AI Adoption"})
        self.assertIn(processed["summary_quality"], {"high", "medium", "low"})

    def test_consent_page_clean_title_falls_back_to_original_title(self) -> None:
        processed = RuleBasedProcessor().process_item(
            {
                "title": "Google rilascia Gemma-4-12B, un modello open potente",
                "clean_title": "Prima di continuare su YouTube",
                "url": "https://www.youtube.com/watch?v=abc123",
                "excerpt": "Google rilascia un nuovo modello open.",
            }
        )
        self.assertEqual(processed["title"], "Google rilascia Gemma-4-12B, un modello open potente")
        self.assertNotIn("Prima di continuare", processed["title"])


if __name__ == "__main__":
    unittest.main()
