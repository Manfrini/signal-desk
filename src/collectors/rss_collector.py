"""RSS source collector."""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Callable

import feedparser

from src.net_guard import fetch_capped_text
from .base import BaseCollector, SourceConfig, normalize_item
from .article_extractor import ArticleContent, fetch_article_content

logger = logging.getLogger(__name__)
MAX_ITEMS_TO_ENRICH = 10
DEFAULT_TIMEOUT_SECONDS = 15
USER_AGENT = "AI-News-Intelligence-Agent/0.1"


class RssCollector(BaseCollector):
    """Collect items from RSS or Atom feeds."""

    def __init__(
        self,
        article_fetcher: Callable[[str], ArticleContent] = fetch_article_content,
    ) -> None:
        self.article_fetcher = article_fetcher
        self.cutoff_date: date_type | None = None

    def set_cutoff_date(self, cutoff_date: date_type) -> None:
        """Items published before this date are discarded downstream, so they are not enriched."""
        self.cutoff_date = cutoff_date

    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        text = fetch_capped_text(
            source.url,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        parsed_feed = feedparser.parse(text)
        if getattr(parsed_feed, "bozo", False):
            logger.warning("RSS parser reported an issue for source %s", source.id)

        items: list[dict[str, str]] = []
        for entry in parsed_feed.entries:
            normalized = normalize_item(
                source,
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                published_at=entry.get("published", entry.get("updated", "")),
                raw_summary=entry.get("summary", entry.get("description", "")),
                raw_content="",
            )
            if normalized is None:
                logger.info("Skipping RSS item without URL from source %s", source.id)
                continue
            if self._is_before_cutoff(normalized):
                items.append(normalized)  # dropped by the age filter; skip the article/transcript fetch
            else:
                items.append(
                    _enrich_with_article_content(normalized, self.article_fetcher, source.allowed_domains)
                )
            if len(items) >= MAX_ITEMS_TO_ENRICH:
                break

        return items

    def _is_before_cutoff(self, item: dict[str, str]) -> bool:
        if self.cutoff_date is None:
            return False
        from .collector_service import parse_published_date

        published = parse_published_date(str(item.get("published_at", "")))
        return published is not None and published < self.cutoff_date


def _enrich_with_article_content(
    item: dict[str, str],
    article_fetcher: Callable[[str], ArticleContent],
    allowed_domains: tuple[str, ...] = (),
) -> dict[str, str]:
    if item.get("signal_type") == "video":
        return _enrich_with_transcript(item)
    from .collector_service import is_allowed_domain

    if not is_allowed_domain(item["url"], allowed_domains):
        # Don't even fetch: a compromised/typo'd feed could otherwise make
        # this collector reach any URL it wants before the domain check
        # (which normally runs after collect() returns) ever applies.
        logger.info("Skipping enrichment for wrong-domain item from source %s: %s", item.get("source_id"), item["url"])
        return _fallback_from_summary(item)
    try:
        article = article_fetcher(item["url"])
    except Exception as exc:  # noqa: BLE001 - one article fetch must not stop source collection.
        logger.info("Article content fetch failed for %s: %s", item["url"], exc)
        if _meaningful_metadata(item.get("raw_summary", "")):
            return {
                **item,
                "excerpt": item["raw_summary"],
                "content_quality": "excerpt",
            }
        return {**item, "content_quality": "title_only"}

    clean_title = article.clean_title or item["clean_title"] or item["title"]
    excerpt = article.excerpt or item["raw_summary"]
    return {
        **item,
        "clean_title": clean_title,
        "excerpt": excerpt,
        "raw_content": article.raw_content,
        "content_quality": article.content_quality,
    }


def _enrich_with_transcript(item: dict[str, str]) -> dict[str, str]:
    """Enrich a video item with its real transcript instead of a generic
    article-page fetch, so daily relevance scoring sees actual spoken content.
    """
    from src.processors.content_fetcher import (
        MIN_FULL_LENGTH,
        _is_youtube,
        _youtube_transcript_cached,
        youtube_video_id,
    )

    if not _is_youtube(item["url"]):
        return item
    video_id = youtube_video_id(item["url"])
    if not video_id:
        return item

    try:
        transcript, _reason = _youtube_transcript_cached(video_id)
    except Exception as exc:  # noqa: BLE001 - one video must not stop source collection.
        logger.info("Transcript fetch failed for %s: %s", item["url"], exc)
        return _fallback_from_summary(item)

    if len(transcript) < MIN_FULL_LENGTH:
        return _fallback_from_summary(item)
    return {**item, "raw_content": transcript, "content_quality": "full"}


def _fallback_from_summary(item: dict[str, str]) -> dict[str, str]:
    if _meaningful_metadata(item.get("raw_summary", "")):
        return {**item, "excerpt": item["raw_summary"], "content_quality": "excerpt"}
    return {**item, "content_quality": "title_only"}


def _meaningful_metadata(value: str) -> bool:
    text = " ".join(str(value).split())
    if len(text) < 40:
        return False
    lowered = text.lower()
    boilerplate = (
        "your source for the latest",
        "latest news and updates",
        "skip to content",
    )
    return not any(fragment in lowered for fragment in boilerplate)
