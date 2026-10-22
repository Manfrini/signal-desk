"""Curated GitHub repository trend collector."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

import requests

from .base import BaseCollector, SourceConfig, normalize_item

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.github.com/repos"
DEFAULT_TIMEOUT_SECONDS = 15
USER_AGENT = "AI-News-Intelligence-Agent/0.1"


class GithubTrendCollector(BaseCollector):
    """Collect lightweight trend signals for a curated repository list."""

    def __init__(self, trend_state: dict[str, Any] | None = None) -> None:
        self.trend_state = trend_state or {"repositories": {}}

    def set_trend_state(self, trend_state: dict[str, Any]) -> None:
        """Inject local trend state before collection."""
        self.trend_state = trend_state

    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for repository in source.repositories:
            payload = self._fetch_repository(repository)
            normalized = self._normalize_repository(source, repository, payload)
            if normalized is not None:
                items.append(normalized)
        return items

    def _fetch_repository(self, repository: str) -> dict[str, Any]:
        response = requests.get(
            f"{API_BASE_URL}/{repository}",
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
        )
        response.raise_for_status()
        return response.json()

    def _normalize_repository(
        self,
        source: SourceConfig,
        repository: str,
        payload: dict[str, Any],
    ) -> dict[str, str] | None:
        full_name = str(payload.get("full_name") or repository)
        stars = int(payload.get("stargazers_count") or 0)
        forks = int(payload.get("forks_count") or 0)
        open_issues = int(payload.get("open_issues_count") or 0)
        previous = self.trend_state.get("repositories", {}).get(full_name, {})
        previous_stars = int(previous.get("stars") or stars)
        star_growth = max(0, stars - previous_stars)
        pushed_at = str(payload.get("pushed_at") or payload.get("updated_at") or "")
        description = str(payload.get("description") or "").strip()
        title = f"{full_name} open-source trend"
        summary = (
            f"{full_name} has {stars:,} GitHub stars, {forks:,} forks, "
            f"and {open_issues:,} open issues. Star growth since last local run: {star_growth}."
        )
        if description:
            summary = f"{description} {summary}"
        item = normalize_item(
            source,
            title=title,
            url=str(payload.get("html_url") or f"https://github.com/{full_name}"),
            published_at="",
            raw_summary=summary,
            excerpt=summary,
            raw_content=summary,
            content_quality="excerpt",
            date_quality="unknown",
            trend_score=_trend_score(stars=stars, star_growth=star_growth, pushed_at=pushed_at),
        )
        if item is None:
            return None
        return {
            **item,
            "trend_subject": full_name,
            "github_stars": str(stars),
            "github_forks": str(forks),
            "github_open_issues": str(open_issues),
            "github_star_growth": str(star_growth),
        }


def _trend_score(*, stars: int, star_growth: int, pushed_at: str) -> int:
    popularity = min(35, stars // 500)
    growth = min(35, star_growth * 5)
    recency = 10 if _is_recent(pushed_at) else 0
    return max(0, min(100, 20 + popularity + growth + recency))


def _is_recent(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - parsed).days <= 14
