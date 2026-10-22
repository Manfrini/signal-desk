"""Learning from explicit and implicit feedback.

The user votes 👍/👎 on items, and opening a deep dive counts as an implicit
(stronger) sign of interest. Each vote nudges learned weights for the item's
topics, keywords and source by a **small step**, capped per signal, so the
ranking improves gradually instead of flipping on a single click.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.processors.sections import personal_score  # noqa: F401  (kept for callers)

logger = logging.getLogger(__name__)

# Gradual learning knobs.
VOTE_STEP = 1.0            # weight moved by one explicit vote
DEEP_DIVE_BONUS = 1.0      # extra weight when the item was also deep-dived
SIGNAL_CAP = 10.0          # max |weight| a single topic/keyword/source can reach
MIN_VOTES_TO_APPLY = 3     # learned boost stays inert below this many votes
MAX_LEARNED_BOOST = 15     # max |points| the learned boost adds to a 0-100 score


def record_feedback(
    item: dict[str, Any],
    vote: int,
    feedback: dict[str, dict[str, Any]],
    preferences: dict[str, Any],
    *,
    deep_dived: bool | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Register a vote and update learned weights. Returns (feedback, prefs).

    ``vote`` is +1 / -1 (0 clears the vote). Re-voting replaces the previous
    contribution, so weights never double-count the same item.
    """
    url = str(item.get("url", "")).strip()
    if not url:
        return feedback, preferences

    previous = feedback.get(url, {})
    was_deep = bool(previous.get("deep_dived", False))
    is_deep = was_deep if deep_dived is None else bool(deep_dived)

    # Undo the previous contribution, then apply the new one.
    _apply_weights(item, _contribution(int(previous.get("vote", 0)), was_deep), preferences, sign=-1)
    _apply_weights(item, _contribution(int(vote), is_deep), preferences, sign=+1)

    if vote == 0 and not is_deep:
        feedback.pop(url, None)
    else:
        feedback[url] = {
            "vote": int(vote),
            "deep_dived": is_deep,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    return feedback, preferences


def record_deep_dive(
    item: dict[str, Any],
    feedback: dict[str, dict[str, Any]],
    preferences: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Mark an item as deep-dived (implicit interest)."""
    url = str(item.get("url", "")).strip()
    if not url or bool(feedback.get(url, {}).get("deep_dived")):
        return feedback, preferences
    return record_feedback(
        item,
        int(feedback.get(url, {}).get("vote", 0)),
        feedback,
        preferences,
        deep_dived=True,
    )


def learned_boost(item: dict[str, Any], preferences: dict[str, Any]) -> int:
    """Return a small, capped score adjustment learned from past feedback."""
    if not preferences or int(preferences.get("vote_count", 0)) < MIN_VOTES_TO_APPLY:
        return 0
    total = 0.0
    for topic in _topics(item):
        total += float(preferences.get("topics", {}).get(topic, 0))
    for keyword in _keywords(item):
        total += float(preferences.get("keywords", {}).get(keyword, 0)) * 0.5
    source = _source(item)
    if source:
        total += float(preferences.get("sources", {}).get(source, 0))
    return int(max(-MAX_LEARNED_BOOST, min(MAX_LEARNED_BOOST, total)))


def empty_preferences() -> dict[str, Any]:
    return {"topics": {}, "keywords": {}, "sources": {}, "vote_count": 0}


def _contribution(vote: int, deep_dived: bool) -> float:
    if vote == 0:
        return DEEP_DIVE_BONUS if deep_dived else 0.0
    step = VOTE_STEP * (1 if vote > 0 else -1)
    if deep_dived and vote > 0:
        step += DEEP_DIVE_BONUS
    return step


def _apply_weights(
    item: dict[str, Any], amount: float, preferences: dict[str, Any], *, sign: int
) -> None:
    if not amount:
        return
    delta = amount * sign
    preferences.setdefault("topics", {})
    preferences.setdefault("keywords", {})
    preferences.setdefault("sources", {})
    for topic in _topics(item):
        _bump(preferences["topics"], topic, delta)
    for keyword in _keywords(item):
        _bump(preferences["keywords"], keyword, delta)
    source = _source(item)
    if source:
        _bump(preferences["sources"], source, delta)
    preferences["vote_count"] = max(0, int(preferences.get("vote_count", 0)) + sign)


def _bump(bucket: dict[str, Any], key: str, delta: float) -> None:
    value = float(bucket.get(key, 0)) + delta
    value = max(-SIGNAL_CAP, min(SIGNAL_CAP, value))
    if abs(value) < 1e-9:
        bucket.pop(key, None)
    else:
        bucket[key] = round(value, 3)


def _topics(item: dict[str, Any]) -> list[str]:
    return [
        str(item.get(key, "")).strip()
        for key in ("primary_topic", "secondary_topic")
        if str(item.get(key, "")).strip()
    ]


def _keywords(item: dict[str, Any]) -> list[str]:
    return [str(term).strip().lower() for term in item.get("personal_match", []) if str(term).strip()]


def _source(item: dict[str, Any]) -> str:
    return str(item.get("source_id") or item.get("source_name") or "").strip()
