"""Tests for gradual feedback learning."""

from __future__ import annotations

import unittest

from src.processors.feedback import (
    MAX_LEARNED_BOOST,
    SIGNAL_CAP,
    empty_preferences,
    learned_boost,
    record_deep_dive,
    record_feedback,
)
from src.processors.profile import personal_relevance


def _item(url: str = "https://a.com") -> dict:
    return {
        "url": url,
        "title": "Agent orchestration framework",
        "primary_topic": "AI Coding Agents",
        "secondary_topic": "",
        "source_id": "github_blog",
        "personal_match": ["agent"],
    }


class RecordFeedbackTest(unittest.TestCase):
    def test_upvote_creates_positive_weights(self) -> None:
        fb, prefs = record_feedback(_item(), 1, {}, empty_preferences())
        self.assertEqual(fb["https://a.com"]["vote"], 1)
        self.assertGreater(prefs["topics"]["AI Coding Agents"], 0)
        self.assertGreater(prefs["sources"]["github_blog"], 0)

    def test_downvote_creates_negative_weights(self) -> None:
        _, prefs = record_feedback(_item(), -1, {}, empty_preferences())
        self.assertLess(prefs["topics"]["AI Coding Agents"], 0)

    def test_revote_replaces_previous_contribution(self) -> None:
        fb, prefs = record_feedback(_item(), 1, {}, empty_preferences())
        fb, prefs = record_feedback(_item(), -1, fb, prefs)
        # Not +1 then -1 stacked: the up-vote is undone first.
        self.assertLess(prefs["topics"]["AI Coding Agents"], 0)
        self.assertEqual(prefs["vote_count"], 1)

    def test_deep_dive_counts_as_implicit_interest(self) -> None:
        fb, prefs = record_deep_dive(_item(), {}, empty_preferences())
        self.assertTrue(fb["https://a.com"]["deep_dived"])
        self.assertGreater(prefs["topics"]["AI Coding Agents"], 0)

    def test_weights_are_capped_per_signal(self) -> None:
        fb, prefs = {}, empty_preferences()
        for i in range(50):
            fb, prefs = record_feedback(_item(f"https://a.com/{i}"), 1, fb, prefs)
        self.assertLessEqual(prefs["topics"]["AI Coding Agents"], SIGNAL_CAP)


class LearnedBoostTest(unittest.TestCase):
    def _prefs_with_votes(self, votes: int) -> dict:
        fb, prefs = {}, empty_preferences()
        for i in range(votes):
            fb, prefs = record_feedback(_item(f"https://a.com/{i}"), 1, fb, prefs)
        return prefs

    def test_inert_below_minimum_votes(self) -> None:
        self.assertEqual(learned_boost(_item(), self._prefs_with_votes(1)), 0)

    def test_applies_after_enough_votes(self) -> None:
        self.assertGreater(learned_boost(_item(), self._prefs_with_votes(5)), 0)

    def test_boost_is_capped(self) -> None:
        self.assertLessEqual(learned_boost(_item(), self._prefs_with_votes(50)), MAX_LEARNED_BOOST)

    def test_change_is_gradual_not_drastic(self) -> None:
        profile = {"keywords_boost": ["agent"]}
        base, _ = personal_relevance(_item(), profile)
        after_one, _ = personal_relevance(_item(), profile, self._prefs_with_votes(1))
        after_many, _ = personal_relevance(_item(), profile, self._prefs_with_votes(30))
        self.assertEqual(after_one, base)                       # one vote changes nothing
        self.assertGreater(after_many, base)                    # many votes do move it
        self.assertLessEqual(after_many - base, MAX_LEARNED_BOOST)


if __name__ == "__main__":
    unittest.main()
