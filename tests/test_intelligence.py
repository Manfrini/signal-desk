"""Tests for deterministic V1.5 intelligence enrichment."""

from __future__ import annotations

from datetime import date

from src.processors.intelligence import (
    calculate_signal_score,
    cluster_topics,
    daily_intelligence_summary,
    possible_action_for,
    summarize_item,
    top_signals,
    why_it_matters_for,
)


RAW_ITEM = {
    "title": "GitHub Copilot adds enterprise coding agent controls",
    "source_id": "github_copilot_changelog",
    "category_hint": "coding_ai",
    "published_at": "2026-05-06",
    "excerpt": "GitHub Copilot adds enterprise coding agent controls, allowing platform teams to distribute managed plugins and govern AI-assisted workflows.",
    "raw_summary": "Updates, ideas, and inspiration from GitHub.",
    "raw_content": "GitHub Copilot adds enterprise coding agent controls, allowing platform teams to distribute managed plugins and govern AI-assisted workflows. Admins can standardize setup across teams and review adoption patterns.",
}


def test_boilerplate_summary_filtering_and_quality() -> None:
    summary, quality = summarize_item(RAW_ITEM, RAW_ITEM["title"])

    assert "Updates, ideas, and inspiration" not in summary
    assert "enterprise coding agent controls" in summary
    assert quality == "high"


def test_title_fallback_summary_is_low_quality() -> None:
    summary, quality = summarize_item(
        {
            **RAW_ITEM,
            "excerpt": "Anthropic is an AI safety and research company based in San Francisco.",
            "raw_content": "",
            "raw_summary": "",
        },
        "Claude update",
    )

    assert summary == "Claude update"
    assert quality == "low"


def test_the_deterministic_path_writes_no_insight_at_all() -> None:
    # These used to return one pre-written sentence per topic, so every
    # article on a topic carried the same line. A field the product cannot
    # fill honestly is left empty; the dashboard says so and offers to run
    # the analysis, which is the only thing that can fill it.
    why = why_it_matters_for("AI Coding Agents", "coding_ai", 5, RAW_ITEM["excerpt"])
    action = possible_action_for("AI Coding Agents", "coding_ai", 5)

    assert why == ""
    assert action == ""


def test_topic_clustering_covers_coding_and_enterprise() -> None:
    primary, secondary = cluster_topics(
        RAW_ITEM,
        title=RAW_ITEM["title"],
        summary=RAW_ITEM["excerpt"],
        category="coding_ai",
    )

    assert primary == "AI Coding Agents"
    assert secondary == "Enterprise AI Adoption"


def test_signal_score_orders_stronger_items_higher() -> None:
    strong = calculate_signal_score(
        RAW_ITEM,
        title=RAW_ITEM["title"],
        summary=RAW_ITEM["excerpt"],
        relevance_score=5,
        content_quality="full",
        today=date(2026, 5, 7),
    )
    weak = calculate_signal_score(
        {**RAW_ITEM, "source_id": "unknown", "published_at": "", "raw_content": "Marketing event recap."},
        title="Company event recap",
        summary="Marketing event recap.",
        relevance_score=2,
        content_quality="title_only",
        today=date(2026, 5, 7),
    )

    assert strong > weak
    assert strong <= 100


def test_todays_item_outranks_equivalent_three_day_old_item() -> None:
    today = date(2026, 5, 7)
    kwargs = dict(title=RAW_ITEM["title"], summary=RAW_ITEM["excerpt"], relevance_score=5,
                  content_quality="full", today=today)
    fresh = calculate_signal_score({**RAW_ITEM, "published_at": "2026-05-07"}, **kwargs)
    older = calculate_signal_score({**RAW_ITEM, "published_at": "2026-05-04"}, **kwargs)
    assert fresh > older


def test_partial_content_is_demoted_below_full() -> None:
    kwargs = dict(title=RAW_ITEM["title"], summary=RAW_ITEM["excerpt"], relevance_score=5, today=date(2026, 5, 7))
    full = calculate_signal_score(RAW_ITEM, content_quality="full", **kwargs)
    excerpt = calculate_signal_score(RAW_ITEM, content_quality="excerpt", **kwargs)
    assert full > excerpt


def test_top_signals_and_daily_summary_use_signal_score() -> None:
    items = [
        {
            "title": "Weak",
            "source_name": "Example",
            "url": "https://example.com/weak",
            "content_quality": "excerpt",
            "relevance_score": 3,
            "signal_score": 45,
            "primary_topic": "General AI",
        },
        {
            "title": "Strong",
            "source_name": "GitHub",
            "url": "https://example.com/strong",
            "content_quality": "full",
            "relevance_score": 5,
            "signal_score": 88,
            "primary_topic": "AI Coding Agents",
        },
    ]

    assert top_signals(items, limit=1)[0]["title"] == "Strong"
    summary = daily_intelligence_summary(items)
    assert summary["top_topics"] == ["General AI", "AI Coding Agents"] or summary["top_topics"] == ["AI Coding Agents", "General AI"]
    assert "suggested_focus" in summary
