"""Hacker News community signal collector via Algolia API."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from urllib.parse import quote_plus

import requests

from .base import BaseCollector, SourceConfig, normalize_item

logger = logging.getLogger(__name__)

ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
DEFAULT_TIMEOUT_SECONDS = 15
USER_AGENT = "AI-News-Intelligence-Agent/0.1"
NOISE_TERMS = ("show hn: personal", "ask hn: who is hiring", "job", "hiring", "crypto price")
REQUIRED_TERMS = (
    "ai",
    "llm",
    "agent",
    "open source",
    "model",
    "copilot",
    "claude",
    "openai",
    "gemini",
    "hugging face",
)


class HackerNewsCollector(BaseCollector):
    """Collect filtered Hacker News AI community signals."""

    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        query = source.query or "AI OR LLM OR agent"
        response = requests.get(
            f"{ALGOLIA_URL}?query={quote_plus(query)}&tags=story",
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
        items: list[dict[str, str]] = []
        for hit in payload.get("hits", []):
            if not _is_valid_hit(hit, source):
                continue
            normalized = _normalize_hit(source, hit)
            if normalized is not None:
                items.append(normalized)
        return items


def _is_valid_hit(hit: dict[str, object], source: SourceConfig) -> bool:
    title = str(hit.get("title") or "").strip()
    text = title.lower()
    points = int(hit.get("points") or 0)
    comments = int(hit.get("num_comments") or 0)
    if points < source.min_points or comments < source.min_comments:
        return False
    if any(term in text for term in NOISE_TERMS):
        return False
    return any(term in text for term in REQUIRED_TERMS)


def _normalize_hit(source: SourceConfig, hit: dict[str, object]) -> dict[str, str] | None:
    title = str(hit.get("title") or "").strip()
    story_id = str(hit.get("objectID") or "").strip()
    url = str(hit.get("url") or "").strip() or f"https://news.ycombinator.com/item?id={story_id}"
    points = int(hit.get("points") or 0)
    comments = int(hit.get("num_comments") or 0)
    created_at = str(hit.get("created_at") or "")
    summary = f"Hacker News discussion with {points} points and {comments} comments: {title}"
    item = normalize_item(
        source,
        title=title,
        url=url,
        published_at=created_at,
        raw_summary=summary,
        excerpt=summary,
        raw_content=summary,
        content_quality="excerpt",
        date_quality="reliable" if created_at else "unknown",
        trend_score=_trend_score(points=points, comments=comments, created_at=created_at),
    )
    if item is None:
        return None
    return {
        **item,
        "trend_subject": _subject_from_title(title),
        "hn_points": str(points),
        "hn_comments": str(comments),
    }


def _trend_score(*, points: int, comments: int, created_at: str) -> int:
    attention = min(45, points // 4) + min(30, comments // 3)
    recency = 10 if _is_recent(created_at) else 0
    return max(0, min(100, 15 + attention + recency))


def _is_recent(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - parsed).days <= 7


def _subject_from_title(title: str) -> str:
    words = [word.strip(".,:;!?()[]{}").lower() for word in title.split()]
    meaningful = [word for word in words if len(word) > 2][:4]
    return " ".join(meaningful) or title.lower()
