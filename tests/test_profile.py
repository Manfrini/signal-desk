"""Tests for profile loading and personal-relevance scoring (Fase 2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.processors.profile import (
    EMPTY_PROFILE,
    load_profile,
    personal_relevance,
    profile_goal,
)


class LoadProfileTest(unittest.TestCase):
    def test_missing_file_returns_empty_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile = load_profile(Path(tmp_dir) / "nope.yaml")
            self.assertEqual(profile["goal"], "")
            self.assertEqual(profile["interests"], [])

    def test_loads_declared_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "profile.yaml"
            path.write_text(
                "goal: Build hands-off AI\n"
                "interests:\n  - autonomous agents\n"
                "keywords_boost:\n  - agent\n",
                encoding="utf-8",
            )
            profile = load_profile(path)
            self.assertEqual(profile_goal(profile), "Build hands-off AI")
            self.assertIn("autonomous agents", profile["interests"])


class PersonalRelevanceTest(unittest.TestCase):
    def _profile(self) -> dict:
        return {
            "goal": "Build hands-off AI",
            "interests": ["autonomous agents"],
            "projects": [{"name": "X", "keywords": ["pipeline"]}],
            "skills_to_grow": ["langgraph"],
            "keywords_boost": ["agent", "orchestration"],
        }

    def test_keyword_boost_scores_higher(self) -> None:
        item = {"title": "New agent orchestration framework", "summary": ""}
        score, matched = personal_relevance(item, self._profile())
        self.assertGreaterEqual(score, 20)
        self.assertIn("agent", matched)
        self.assertIn("orchestration", matched)

    def test_open_source_gets_bonus(self) -> None:
        base = {"title": "Some unrelated news", "summary": ""}
        oss = {"title": "Some unrelated news", "summary": "", "signal_type": "open_source_trend"}
        base_score, _ = personal_relevance(base, self._profile())
        oss_score, _ = personal_relevance(oss, self._profile())
        self.assertGreater(oss_score, base_score)

    def test_no_match_scores_zero(self) -> None:
        item = {"title": "Weather forecast for tomorrow", "summary": ""}
        score, matched = personal_relevance(item, self._profile())
        self.assertEqual(score, 0)
        self.assertEqual(matched, [])

    def test_empty_profile_is_inert(self) -> None:
        item = {"title": "New agent orchestration", "summary": ""}
        score, matched = personal_relevance(item, dict(EMPTY_PROFILE))
        self.assertEqual(score, 0)
        self.assertEqual(matched, [])

    def test_score_is_clamped_to_100(self) -> None:
        profile = {"keywords_boost": ["agent"] * 50}
        item = {"title": "agent " * 5, "summary": "agent"}
        score, _ = personal_relevance(item, profile)
        self.assertLessEqual(score, 100)


if __name__ == "__main__":
    unittest.main()
