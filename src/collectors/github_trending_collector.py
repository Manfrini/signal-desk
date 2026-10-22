"""GitHub Trending page collector.

Unlike ``github_repo_trend`` (which only re-checks a fixed, curated list of
repositories), this collector reads the actual https://github.com/trending
page to discover whatever repositories are gaining stars today.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from src.net_guard import fetch_capped_text
from .base import BaseCollector, SourceConfig, normalize_item

DEFAULT_TIMEOUT_SECONDS = 15
USER_AGENT = "AI-News-Intelligence-Agent/0.1"

_STARS_TODAY_RE = re.compile(r"([\d,]+)\s+stars? today")


class GithubTrendingPageCollector(BaseCollector):
    """Collect items from the github.com/trending page."""

    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        text = fetch_capped_text(
            source.url,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )

        soup = BeautifulSoup(text, "html.parser")
        items: list[dict[str, str]] = []
        for row in soup.select("article.Box-row"):
            item = _normalize_row(source, row)
            if item is not None:
                items.append(item)
        return items


def _normalize_row(source: SourceConfig, row: Tag) -> dict[str, str] | None:
    heading = row.find("h2")
    link = heading.find("a", href=True) if isinstance(heading, Tag) else None
    href = str(link.get("href") or "").strip() if isinstance(link, Tag) else ""
    if not href:
        return None

    full_name = href.strip("/")
    absolute_url = urljoin("https://github.com", href)

    description_node = row.find("p")
    description = description_node.get_text(" ", strip=True) if isinstance(description_node, Tag) else ""
    if not description:
        description = f"{full_name} è tra i repository GitHub più in crescita oggi."

    language_node = row.find(attrs={"itemprop": "programmingLanguage"})
    language = language_node.get_text(strip=True) if isinstance(language_node, Tag) else ""

    stars_total = _stars_total(row)
    stars_today = _stars_today(row)

    normalized = normalize_item(
        source,
        title=full_name,
        url=absolute_url,
        published_at="",
        raw_summary=description,
        excerpt=description,
        raw_content=description,
        content_quality="excerpt",
        date_quality="unknown",
        trend_score=_trend_score(stars_total=stars_total, stars_today=stars_today),
    )
    if normalized is None:
        return None
    return {
        **normalized,
        "trend_subject": full_name,
        "github_language": language,
        "github_stars": str(stars_total),
        "github_stars_today": str(stars_today),
    }


def _stars_total(row: Tag) -> int:
    link = row.find("a", href=lambda h: bool(h) and h.endswith("/stargazers"))
    if not isinstance(link, Tag):
        return 0
    return _parse_int(link.get_text(strip=True))


def _stars_today(row: Tag) -> int:
    for span in row.find_all("span"):
        if not isinstance(span, Tag):
            continue
        match = _STARS_TODAY_RE.search(span.get_text(" ", strip=True))
        if match:
            return _parse_int(match.group(1))
    return 0


def _parse_int(value: str) -> int:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else 0


def _trend_score(*, stars_total: int, stars_today: int) -> int:
    growth = min(55, stars_today // 3)
    popularity = min(30, stars_total // 3000)
    return max(0, min(100, 15 + growth + popularity))
