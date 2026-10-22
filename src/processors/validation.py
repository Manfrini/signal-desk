"""Validation helpers for processed news items."""

from __future__ import annotations

from typing import Any
from collections import Counter


REQUIRED_PROCESSED_FIELDS = (
    "title",
    "url",
    "source_name",
    "company",
    "category",
    "summary",
    "why_it_matters",
    "possible_action",
    "primary_topic",
    "signal_score",
    "summary_quality",
    "source_group",
    "signal_type",
    "trend_score",
)


def validate_processed_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return validation errors for processed news items."""
    errors: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        missing_fields = [
            field
            for field in REQUIRED_PROCESSED_FIELDS
            if not str(item.get(field, "")).strip()
        ]
        if missing_fields:
            errors.append(
                {
                    "index": index,
                    "url": item.get("url", ""),
                    "missing_fields": missing_fields,
                }
            )

    return errors


def content_quality_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return counts for known content quality levels."""
    counter = Counter(str(item.get("content_quality") or "title_only") for item in items)
    return {
        "full": counter.get("full", 0),
        "excerpt": counter.get("excerpt", 0),
        "title_only": counter.get("title_only", 0),
        "invalid": counter.get("invalid", 0),
    }


def relevance_score_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return counts for relevance score values."""
    counter = Counter(str(item.get("relevance_score") or "unknown") for item in items)
    return dict(sorted(counter.items()))


def signal_score_distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return signal score counts grouped into report buckets."""
    buckets = {"80-100": 0, "60-79": 0, "40-59": 0, "20-39": 0, "0-19": 0}
    for item in items:
        score = int(item.get("signal_score") or 0)
        if score >= 80:
            buckets["80-100"] += 1
        elif score >= 60:
            buckets["60-79"] += 1
        elif score >= 40:
            buckets["40-59"] += 1
        elif score >= 20:
            buckets["20-39"] += 1
        else:
            buckets["0-19"] += 1
    return buckets


def summary_quality_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return counts for summary quality values."""
    counter = Counter(str(item.get("summary_quality") or "low") for item in items)
    return {
        "high": counter.get("high", 0),
        "medium": counter.get("medium", 0),
        "low": counter.get("low", 0),
    }


def topic_distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return processed item counts grouped by primary topic."""
    counter = Counter(str(item.get("primary_topic") or "General AI") for item in items)
    return dict(counter.most_common())


def top_signal_items(items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Return top signal records for validation output."""
    valid_items = [
        item
        for item in items
        if item.get("content_quality") != "invalid" and int(item.get("relevance_score", 3) or 3) > 1
    ]
    ranked = sorted(
        valid_items,
        key=lambda item: (
            int(item.get("signal_score") or 0),
            str(item.get("published_at", "")),
        ),
        reverse=True,
    )
    return [
        {
            "title": item.get("title", ""),
            "source": item.get("source_name") or item.get("company") or "",
            "signal_score": int(item.get("signal_score") or 0),
        }
        for item in ranked[:limit]
    ]


def excluded_noise_count(items: list[dict[str, Any]]) -> int:
    """Return count of invalid or very low-signal processed items."""
    return sum(
        1
        for item in items
        if item.get("content_quality") == "invalid"
        or int(item.get("relevance_score", 3) or 3) <= 1
        or int(item.get("signal_score") or 0) < 20
    )


def top_sources_by_useful_signals(items: list[dict[str, Any]], limit: int = 5) -> dict[str, int]:
    """Return sources with most useful signals."""
    counter = Counter(
        str(item.get("source_name") or item.get("company") or "unknown")
        for item in items
        if item.get("content_quality") != "invalid"
        and int(item.get("relevance_score", 3) or 3) > 1
        and int(item.get("signal_score") or 0) >= 60
    )
    return dict(counter.most_common(limit))


def trend_score_distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return trend score counts grouped into report buckets."""
    buckets = {"80-100": 0, "60-79": 0, "40-59": 0, "20-39": 0, "0-19": 0}
    for item in items:
        score = int(item.get("trend_score") or 0)
        if score >= 80:
            buckets["80-100"] += 1
        elif score >= 60:
            buckets["60-79"] += 1
        elif score >= 40:
            buckets["40-59"] += 1
        elif score >= 20:
            buckets["20-39"] += 1
        else:
            buckets["0-19"] += 1
    return buckets


def source_group_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return processed item counts grouped by source group."""
    counter = Counter(str(item.get("source_group") or "foundation_models") for item in items)
    return dict(counter.most_common())


def signal_type_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return processed item counts grouped by signal type."""
    counter = Counter(str(item.get("signal_type") or "official_news") for item in items)
    return dict(counter.most_common())


def top_trending_items(items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Return top trend records for validation output."""
    ranked = sorted(
        [
            item
            for item in items
            if int(item.get("trend_score") or 0) > 0
            and item.get("content_quality") != "invalid"
        ],
        key=lambda item: int(item.get("trend_score") or 0),
        reverse=True,
    )
    return [
        {
            "title": item.get("title", ""),
            "source": item.get("source_name") or item.get("company") or "",
            "trend_score": int(item.get("trend_score") or 0),
        }
        for item in ranked[:limit]
    ]


def title_only_counts_by_source(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return title-only item counts grouped by source id/name."""
    counter = Counter(
        str(item.get("source_id") or item.get("source_name") or "unknown")
        for item in items
        if item.get("content_quality") == "title_only"
    )
    return dict(sorted(counter.items()))
