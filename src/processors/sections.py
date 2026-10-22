"""Shared definitions of the dashboard sections.

Single source of truth for how items are grouped into the four brief tabs,
used by both the dashboard rendering and the per-section brief generation.
"""

from __future__ import annotations

from typing import Any

# Section keys and their Italian labels (order = tab order).
SECTION_KEYS = ("big", "perte", "opinion", "tools")
SECTION_LABELS = {
    "big": "Le big dell'AI",
    "perte": "Product & Data",
    "opinion": "Opinioni e video",
    "tools": "Strumenti & API",
}

MAINSTREAM_COMPANIES = (
    "openai", "anthropic", "google", "deepmind", "microsoft", "meta",
    "apple", "amazon", "nvidia", "mistral", "xai",
)
_TOOL_GROUPS = {"builder_ecosystem", "agentic_coding", "google_ecosystem"}


def is_mainstream(item: dict[str, Any]) -> bool:
    name = str(item.get("company") or item.get("source_name") or "").lower()
    return any(brand in name for brand in MAINSTREAM_COMPANIES)


def is_opinion(item: dict[str, Any]) -> bool:
    return str(item.get("signal_type", "")) in {"opinion", "video"}


def is_tool(item: dict[str, Any]) -> bool:
    return (
        str(item.get("signal_type", "")) == "open_source_trend"
        or str(item.get("source_group", "")) in _TOOL_GROUPS
    )


def personal_score(item: dict[str, Any]) -> int:
    return int(item.get("personal_relevance_score", 0) or 0)


def group_by_section(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group items into the four sections (same criteria as the dashboard)."""
    return {
        "big": [item for item in items if is_mainstream(item)],
        "perte": sorted(
            [item for item in items if personal_score(item) > 0],
            key=personal_score,
            reverse=True,
        ),
        "opinion": [item for item in items if is_opinion(item)],
        "tools": sorted(
            [item for item in items if is_tool(item)],
            key=lambda item: int(item.get("trend_score", 0) or 0),
            reverse=True,
        ),
    }
