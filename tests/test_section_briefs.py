"""Tests for per-section briefs."""

from __future__ import annotations

import unittest

from src.processors.sections import group_by_section
from src.processors.section_briefs import ITEMS_PER_SECTION, generate_section_briefs


ITEMS = [
    {"title": "OpenAI ships new model", "company": "OpenAI", "primary_topic": "Foundation Models",
     "signal_type": "official_news", "personal_relevance_score": 0},
    {"title": "Lenny on product", "company": "Lenny's Newsletter", "primary_topic": "Enterprise AI Adoption",
     "signal_type": "opinion", "personal_relevance_score": 0},
    {"title": "cline agent", "company": "GitHub", "primary_topic": "AI Coding Agents",
     "signal_type": "open_source_trend", "trend_score": 60, "personal_relevance_score": 40,
     "personal_match": ["agent"]},
]


class GroupBySectionTest(unittest.TestCase):
    def test_groups_items_into_four_sections(self) -> None:
        grouped = group_by_section(ITEMS)
        self.assertEqual([i["company"] for i in grouped["big"]], ["OpenAI"])
        self.assertEqual([i["company"] for i in grouped["opinion"]], ["Lenny's Newsletter"])
        self.assertEqual([i["company"] for i in grouped["tools"]], ["GitHub"])
        self.assertEqual(grouped["perte"][0]["company"], "GitHub")  # personal>0

    def test_brief_considers_ten_articles_per_section(self) -> None:
        self.assertEqual(ITEMS_PER_SECTION, 10)


class FallbackBriefTest(unittest.TestCase):
    def test_fallback_briefs_when_llm_disabled(self) -> None:
        briefs = generate_section_briefs(ITEMS, goal="", llm_enabled=False)
        self.assertEqual(set(briefs.keys()), {"big", "perte", "opinion", "tools"})
        # Non-empty, deterministic Italian summaries.
        self.assertIn("notizie in evidenza", briefs["big"])
        self.assertIn("Foundation Models", briefs["big"])

    def test_empty_section_gets_neutral_brief(self) -> None:
        briefs = generate_section_briefs([], goal="", llm_enabled=False)
        self.assertIn("Nessuna notizia", briefs["big"])


class LlmBriefTest(unittest.TestCase):
    def test_uses_llm_payload_with_fallback_per_section(self) -> None:
        class StubAnalyzer:
            calls = 1

            def run_structured(self, prompt, schema):
                return {"big": "Brief LLM big", "perte": "Brief LLM perte"}

            def usage_summary(self):
                return {"calls": 1, "total_cost_usd": 0.05, "input_tokens": 100, "output_tokens": 50}

        briefs = generate_section_briefs(
            ITEMS, goal="x", llm_enabled=True, analyzer=StubAnalyzer()
        )
        self.assertEqual(briefs["big"], "Brief LLM big")
        self.assertEqual(briefs["perte"], "Brief LLM perte")
        # Missing keys fall back to deterministic briefs.
        self.assertIn("notizie in evidenza", briefs["opinion"])


if __name__ == "__main__":
    unittest.main()
