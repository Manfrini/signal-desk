"""Collector orchestration service."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .base import BaseCollector, SourceConfig, has_required_schema
from .github_trend_collector import GithubTrendCollector
from .github_trending_collector import GithubTrendingPageCollector
from .hacker_news_collector import HackerNewsCollector
from .html_collector import HtmlCollector
from .rss_collector import RssCollector
from src.storage.json_storage import (
    DEFAULT_SOURCES_PATH,
    DEFAULT_TREND_STATE_PATH,
    load_read_urls,
    load_sources_config,
    load_trend_state,
    today_utc,
)

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_MAX_ITEMS_PER_SOURCE = 10
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_READ_STATE_PATH = Path("data/read_urls.json")


class CollectorService:
    """Coordinate collection across enabled sources."""

    def __init__(
        self,
        sources_path: Path | str = DEFAULT_SOURCES_PATH,
        settings_path: Path | str = DEFAULT_SETTINGS_PATH,
        collectors: dict[str, BaseCollector] | None = None,
        today: date_type | None = None,
    ) -> None:
        self.sources_path = Path(sources_path)
        self.settings_path = Path(settings_path)
        self.today = today or today_utc()
        self.collectors = collectors or {
            "rss": RssCollector(),
            "html": HtmlCollector(),
            "github_repo_trend": GithubTrendCollector(),
            "github_trending": GithubTrendingPageCollector(),
            "hacker_news": HackerNewsCollector(),
        }

    def collect(self) -> dict[str, Any]:
        """Collect items from enabled sources and return items plus errors."""
        return self._collect(self.load_enabled_sources())

    def collect_source(self, source_id: str) -> dict[str, Any]:
        """Collect items from a single source by id, for a manual per-source rerun.

        Looks the source up regardless of its ``enabled`` flag, since a
        manual rerun request is an explicit ask to try that source now.
        """
        matching = [source for source in self.load_sources() if source.id == source_id]
        if not matching:
            raise ValueError(f"Unknown source id: {source_id}")
        return self._collect(matching)

    def _collect(self, sources: list[SourceConfig]) -> dict[str, Any]:
        """Collect items from the given sources and return items plus errors."""
        settings = self._load_settings()
        max_items_per_source = int(
            settings.get("max_items_per_source", DEFAULT_MAX_ITEMS_PER_SOURCE)
        )
        lookback_days = int(settings.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        read_state_path = Path(settings.get("read_state_path", DEFAULT_READ_STATE_PATH))
        trend_state_path = Path(settings.get("trend_state_path", DEFAULT_TREND_STATE_PATH))
        read_urls = load_read_urls(read_state_path)
        trend_state = load_trend_state(trend_state_path)
        cutoff_date = self.today - timedelta(days=lookback_days)

        collected_items: list[dict[str, Any]] = []
        collection_errors: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        skipped_old_items = 0
        skipped_read_items = 0
        skipped_unparseable_date_items = 0
        excluded_wrong_domain_items = 0
        per_source: list[dict[str, Any]] = []

        for source in sources:
            logger.info("Collecting source %s (%s)", source.id, source.url)
            collector = self.collectors.get(source.type)
            if collector is None:
                error = f"Unsupported source type: {source.type}"
                collection_errors.append(self._error_payload(source, error))
                per_source.append(_source_stat(source, found=0, accepted=0, status="error", error=error))
                logger.error("Unsupported source type %s for source %s", source.type, source.id)
                continue
            if hasattr(collector, "set_trend_state"):
                collector.set_trend_state(trend_state)
            if hasattr(collector, "set_cutoff_date"):
                collector.set_cutoff_date(cutoff_date)

            try:
                source_items = collector.collect(source)
            except Exception as exc:  # noqa: BLE001 - source failures must not stop the run.
                collection_errors.append(self._error_payload(source, str(exc)))
                per_source.append(_source_stat(source, found=0, accepted=0, status="error", error=str(exc)))
                logger.error("Collection failed for source %s: %s", source.id, exc)
                continue
            excluded_wrong_domain_items += int(
                getattr(collector, "excluded_wrong_domain_items", 0) or 0
            )

            accepted_for_source = 0
            for item in source_items:
                url = str(item.get("url", "")).strip()
                if not url:
                    logger.info("Skipping item without URL from source %s", source.id)
                    continue
                if url in seen_urls:
                    logger.info("Skipping duplicate URL from source %s: %s", source.id, url)
                    continue
                if "/shorts/" in url.lower():
                    logger.info("Skipping YouTube Short from source %s: %s", source.id, url)
                    continue
                if not is_allowed_domain(url, source.allowed_domains):
                    excluded_wrong_domain_items += 1
                    logger.info("Skipping wrong-domain item from source %s: %s", source.id, url)
                    continue
                if not has_required_schema(item):
                    logger.info("Skipping item with invalid schema from source %s", source.id)
                    continue
                if url in read_urls:
                    skipped_read_items += 1
                    logger.info("Skipping already-read URL from source %s: %s", source.id, url)
                    continue

                published_at = str(item.get("published_at", ""))
                published_date = parse_published_date(published_at)
                # An estimate stays an estimate: the collector already marked
                # items whose date is the run's, not the page's, and a
                # parseable value must not promote that to "reliable".
                if item.get("date_quality") != "estimated":
                    item["date_quality"] = date_quality_for(published_at, published_date)
                if published_date is not None and published_date < cutoff_date:
                    skipped_old_items += 1
                    logger.info("Skipping old item from source %s: %s", source.id, url)
                    continue
                if item["date_quality"] == "invalid":
                    skipped_unparseable_date_items += 1
                    logger.info(
                        "Keeping item with unparseable date from source %s: %s",
                        source.id,
                        url,
                    )

                collected_items.append(item)
                seen_urls.add(url)
                accepted_for_source += 1
                if accepted_for_source >= max_items_per_source:
                    break

            per_source.append(
                _source_stat(
                    source,
                    found=len(source_items),
                    accepted=accepted_for_source,
                    status="ok",
                    error="",
                )
            )
            logger.info(
                "Collected %s items from source %s",
                accepted_for_source,
                source.id,
            )

        return {
            "collected_items": collected_items,
            "collection_errors": collection_errors,
            "skipped_old_items": skipped_old_items,
            "skipped_read_items": skipped_read_items,
            "skipped_unparseable_date_items": skipped_unparseable_date_items,
            "excluded_wrong_domain_items": excluded_wrong_domain_items,
            "per_source": per_source,
            "read_state_path": read_state_path,
            "trend_state_path": trend_state_path,
        }

    def _load_settings(self) -> dict[str, Any]:
        with self.settings_path.open("r", encoding="utf-8") as file_handle:
            return yaml.safe_load(file_handle) or {}

    def load_enabled_sources(self) -> list[SourceConfig]:
        """Load enabled source configurations."""
        return [source for source in self.load_sources() if source.enabled]

    def load_sources(self) -> list[SourceConfig]:
        """Load all source configurations from data/sources.json."""
        payload = load_sources_config(self.sources_path)

        sources: list[SourceConfig] = []
        for source_payload in payload.get("sources", []):
            source = SourceConfig.from_dict(source_payload)
            sources.append(source)
        return sources

    @staticmethod
    def _error_payload(source: SourceConfig, message: str) -> dict[str, str]:
        return {
            "source_id": source.id,
            "source_name": source.name,
            "url": source.url,
            "error": message,
        }


def parse_published_date(value: str) -> date_type | None:
    """Parse common RSS/HTML date strings into a local date."""
    normalized = value.strip()
    if not normalized:
        return None

    try:
        parsed_email_date = parsedate_to_datetime(normalized)
        if parsed_email_date.tzinfo is None:
            parsed_email_date = parsed_email_date.replace(tzinfo=timezone.utc)
        return parsed_email_date.date()
    except (TypeError, ValueError, IndexError, OverflowError):
        pass

    iso_value = normalized.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_value).date()
    except ValueError:
        pass

    for date_format in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(normalized, date_format).date()
        except ValueError:
            continue
    return None


def date_quality_for(value: str, parsed_date: date_type | None) -> str:
    """Return date quality for a raw published_at value."""
    if parsed_date is not None:
        return "reliable"
    if not value.strip():
        return "unknown"
    return "invalid"


def _source_stat(source: Any, *, found: int, accepted: int, status: str, error: str) -> dict[str, Any]:
    """Build one per-source report entry for the Fonti page."""
    return {
        "source_id": source.id,
        "source_name": source.name,
        "source_group": getattr(source, "source_group", "") or "",
        "source_url": getattr(source, "url", "") or "",
        "found": found,
        "accepted": accepted,
        "status": status,
        "error": error,
    }


def is_allowed_domain(url: str, allowed_domains: tuple[str, ...]) -> bool:
    """Return whether URL host belongs to one of the configured source domains."""
    if not allowed_domains:
        return True
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if not host:
        return False
    for domain in allowed_domains:
        normalized = domain.lower().removeprefix("www.")
        if host == normalized or host.endswith("." + normalized):
            return True
    return False
