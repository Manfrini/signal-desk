"""Shared collector types and normalization helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin


REQUIRED_ITEM_KEYS = (
    "title",
    "clean_title",
    "url",
    "source_id",
    "source_name",
    "company",
    "category_hint",
    "published_at",
    "collected_at",
    "raw_summary",
    "excerpt",
    "raw_content",
    "content_quality",
    "date_quality",
    "source_group",
    "signal_type",
    "trend_score",
)


@dataclass(frozen=True)
class SourceConfig:
    """Configuration for one news source."""

    id: str
    name: str
    company: str
    url: str
    type: str
    category_hint: str
    enabled: bool
    notes: str = ""
    allowed_domains: tuple[str, ...] = ()
    source_group: str = "foundation_models"
    signal_type: str = "official_news"
    repositories: tuple[str, ...] = ()
    query: str = ""
    min_points: int = 20
    min_comments: int = 5

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceConfig":
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            company=str(payload["company"]),
            url=str(payload["url"]),
            type=str(payload["type"]),
            category_hint=str(payload["category_hint"]),
            enabled=bool(payload["enabled"]),
            notes=str(payload.get("notes", "")),
            allowed_domains=tuple(str(domain) for domain in payload.get("allowed_domains", [])),
            source_group=str(payload.get("source_group", "foundation_models")),
            signal_type=str(payload.get("signal_type", "official_news")),
            repositories=tuple(str(repo) for repo in payload.get("repositories", [])),
            query=str(payload.get("query", "")),
            min_points=int(payload.get("min_points", 20)),
            min_comments=int(payload.get("min_comments", 5)),
        )


class BaseCollector(ABC):
    """Base interface for source collectors."""

    @abstractmethod
    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        """Collect and normalize items for one source."""


def collected_at_now() -> str:
    """Return the current UTC collection timestamp."""
    return datetime.now(timezone.utc).isoformat()


def normalize_item(
    source: SourceConfig,
    *,
    title: str | None,
    url: str | None,
    published_at: str | None = "",
    raw_summary: str | None = "",
    clean_title: str | None = None,
    excerpt: str | None = "",
    raw_content: str | None = "",
    content_quality: str = "title_only",
    date_quality: str = "unknown",
    trend_score: int = 0,
) -> dict[str, str] | None:
    """Normalize a raw collected item into the project schema."""
    if not url or not str(url).strip():
        return None

    normalized_url = urljoin(source.url, str(url).strip())
    if not normalized_url:
        return None

    normalized_title = (title or "").strip()
    collected = collected_at_now()
    published = (published_at or "").strip()
    if not published:
        # No date anywhere on the page. The run that first sees an item is an
        # upper bound on its publication date -- it cannot have come out after
        # we read it -- so use that. It treats the item as fresh as it could
        # possibly be, never fresher, and above all it lets the item age:
        # a dateless item is never dropped by the recency window, so without
        # this it would sit on the page indefinitely.
        published = collected
        date_quality = "estimated"
    return {
        "title": normalized_title,
        "clean_title": (clean_title or normalized_title).strip(),
        "url": normalized_url,
        "source_id": source.id,
        "source_name": source.name,
        "company": source.company,
        "category_hint": source.category_hint,
        "published_at": published,
        "collected_at": collected,
        "raw_summary": (raw_summary or "").strip(),
        "excerpt": (excerpt or "").strip(),
        "raw_content": (raw_content or "").strip(),
        "content_quality": content_quality,
        "date_quality": date_quality,
        "source_group": source.source_group,
        "signal_type": source.signal_type,
        "trend_score": str(int(trend_score)),
    }


def has_required_schema(item: dict[str, Any]) -> bool:
    """Return whether a collected item has the required schema keys."""
    return all(key in item for key in REQUIRED_ITEM_KEYS)
