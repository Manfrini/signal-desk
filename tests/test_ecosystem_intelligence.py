"""Tests for ecosystem intelligence and trend monitoring."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from src.collectors.base import SourceConfig, normalize_item
from src.collectors.github_trend_collector import GithubTrendCollector
from src.collectors.hacker_news_collector import HackerNewsCollector
from src.outputs.dashboard_output import DashboardOutput
from src.pipeline.daily_pipeline import DailyPipeline
from src.processors.processor_service import ProcessorService
from src.processors.rule_based_processor import RuleBasedProcessor
from src.storage.json_storage import load_trend_state


def ecosystem_source() -> SourceConfig:
    return SourceConfig(
        id="github_ai_repo_trends",
        name="Curated AI GitHub Repo Trends",
        company="GitHub",
        url="https://api.github.com",
        type="github_repo_trend",
        category_hint="coding_ai",
        enabled=True,
        allowed_domains=("github.com",),
        source_group="open_source_community",
        signal_type="open_source_trend",
        repositories=("langchain-ai/langchain",),
    )


def test_source_config_and_normalize_preserve_ecosystem_fields() -> None:
    source = SourceConfig.from_dict(
        {
            "id": "builder",
            "name": "Builder",
            "company": "Builder",
            "url": "https://example.com",
            "type": "html",
            "category_hint": "coding_ai",
            "enabled": True,
            "source_group": "builder_ecosystem",
            "signal_type": "tooling",
        }
    )

    item = normalize_item(source, title="Tool update", url="https://example.com/update", trend_score=42)

    assert source.source_group == "builder_ecosystem"
    assert source.signal_type == "tooling"
    assert item is not None
    assert item["source_group"] == "builder_ecosystem"
    assert item["signal_type"] == "tooling"
    assert item["trend_score"] == "42"


def test_github_trend_collector_computes_star_growth_from_state() -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "full_name": "langchain-ai/langchain",
        "html_url": "https://github.com/langchain-ai/langchain",
        "description": "Build context-aware reasoning applications.",
        "stargazers_count": 1200,
        "forks_count": 50,
        "open_issues_count": 10,
        "pushed_at": "2026-05-06T10:00:00Z",
    }

    collector = GithubTrendCollector(
        trend_state={"repositories": {"langchain-ai/langchain": {"stars": 1180}}}
    )
    with patch("src.collectors.github_trend_collector.requests.get", return_value=response):
        items = collector.collect(ecosystem_source())

    assert len(items) == 1
    assert items[0]["trend_subject"] == "langchain-ai/langchain"
    assert items[0]["github_star_growth"] == "20"
    assert int(items[0]["trend_score"]) > 40
    assert items[0]["signal_type"] == "open_source_trend"


def test_hacker_news_collector_rejects_low_score_and_keeps_ai_signal() -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "hits": [
            {
                "title": "Tiny AI agent framework for local workflows",
                "url": "https://github.com/example/agent",
                "objectID": "1",
                "points": 80,
                "num_comments": 30,
                "created_at": "2026-05-06T10:00:00Z",
            },
            {
                "title": "Random launch",
                "url": "https://example.com/random",
                "objectID": "2",
                "points": 2,
                "num_comments": 0,
                "created_at": "2026-05-06T10:00:00Z",
            },
        ]
    }
    source = SourceConfig(
        id="hacker_news_ai",
        name="Hacker News AI Signals",
        company="Hacker News",
        url="https://hn.algolia.com/api/v1/search_by_date",
        type="hacker_news",
        category_hint="general_ai",
        enabled=True,
        source_group="open_source_community",
        signal_type="community_signal",
        query="AI",
        min_points=40,
        min_comments=10,
    )

    with patch("src.collectors.hacker_news_collector.requests.get", return_value=response):
        items = HackerNewsCollector().collect(source)

    assert [item["title"] for item in items] == ["Tiny AI agent framework for local workflows"]
    assert items[0]["source_group"] == "open_source_community"
    assert int(items[0]["trend_score"]) >= 40


def test_processor_preserves_group_type_and_trend_score() -> None:
    processed = RuleBasedProcessor().process_item(
        {
            "title": "langchain-ai/langchain open-source trend",
            "clean_title": "langchain-ai/langchain open-source trend",
            "url": "https://github.com/langchain-ai/langchain",
            "source_id": "github_ai_repo_trends",
            "source_name": "Curated AI GitHub Repo Trends",
            "company": "GitHub",
            "category_hint": "coding_ai",
            "published_at": "",
            "collected_at": "2026-05-07T10:00:00+00:00",
            "raw_summary": "LangChain has strong GitHub activity for agent workflows.",
            "excerpt": "LangChain has strong GitHub activity for agent workflows.",
            "raw_content": "LangChain has strong GitHub activity for agent workflows.",
            "content_quality": "excerpt",
            "date_quality": "unknown",
            "source_group": "open_source_community",
            "signal_type": "open_source_trend",
            "trend_score": "68",
        }
    )

    assert processed["source_group"] == "open_source_community"
    assert processed["signal_type"] == "open_source_trend"
    assert processed["trend_score"] == 68


def test_pipeline_updates_trend_state_only_for_non_dry_run() -> None:
    class FakeCollector:
        def collect(self):
            return {
                "collected_items": [
                    {
                        "title": "Repo trend",
                        "clean_title": "Repo trend",
                        "url": "https://github.com/example/repo",
                        "source_id": "github_ai_repo_trends",
                        "source_name": "Curated AI GitHub Repo Trends",
                        "company": "GitHub",
                        "category_hint": "coding_ai",
                        "published_at": "",
                        "collected_at": "2026-05-07T10:00:00+00:00",
                        "raw_summary": "Repo trend summary for AI agent workflows.",
                        "excerpt": "Repo trend summary for AI agent workflows.",
                        "raw_content": "Repo trend summary for AI agent workflows.",
                        "content_quality": "excerpt",
                        "date_quality": "unknown",
                        "source_group": "open_source_community",
                        "signal_type": "open_source_trend",
                        "trend_score": "50",
                        "trend_subject": "example/repo",
                        "github_stars": "100",
                    }
                ],
                "collection_errors": [],
                "trend_state_path": trend_state_path,
            }

        def load_sources(self):
            return []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        trend_state_path = tmp_path / "trend_state.json"
        settings_path = tmp_path / "settings.yaml"
        settings_path.write_text("output_dashboard_enabled: true\noutput_email_enabled: false\n", encoding="utf-8")

        from src.outputs.output_service import OutputService

        dry_pipeline = DailyPipeline(
            collector_service=FakeCollector(),
            processor_service=ProcessorService(base_dir=tmp_path, date=date(2026, 5, 7), llm_enabled=False),
            output_service=OutputService(
                settings_path=settings_path,
                dashboard_dir=tmp_path / "dashboard",
                email_dir=tmp_path / "email",
                date=date(2026, 5, 7),
            ),
            base_dir=tmp_path,
            date=date(2026, 5, 7),
        )
        dry_pipeline.run(dry_run=True)
        assert not trend_state_path.exists()

        normal_pipeline = DailyPipeline(
            collector_service=FakeCollector(),
            processor_service=ProcessorService(base_dir=tmp_path, date=date(2026, 5, 7), llm_enabled=False),
            output_service=OutputService(
                settings_path=settings_path,
                dashboard_dir=tmp_path / "dashboard",
                email_dir=tmp_path / "email",
                date=date(2026, 5, 7),
            ),
            base_dir=tmp_path,
            date=date(2026, 5, 7),
        )
        normal_pipeline.run(dry_run=False)

        state = load_trend_state(trend_state_path)
        assert state["repositories"]["example/repo"]["stars"] == 100


def test_dashboard_renders_ecosystem_sections_and_filters() -> None:
    html = DashboardOutput(date=date(2026, 5, 7)).render_html(
        [
            {
                "title": "Repo trend",
                "url": "https://github.com/example/repo",
                "company": "GitHub",
                "source_name": "Curated AI GitHub Repo Trends",
                "category": "coding_ai",
                "summary": "Repo trend summary.",
                "content_quality": "excerpt",
                "relevance_score": 5,
                "signal_score": 66,
                "trend_score": 72,
                "summary_quality": "medium",
                "primary_topic": "AI Coding Agents",
                "source_group": "open_source_community",
                "signal_type": "open_source_trend",
                "why_it_matters": "This may affect builder tooling choices.",
                "possible_action": "Track adoption before changing internal tool standards.",
            }
        ]
    )

    # OSS items surface in the brief "Strumenti & API" tab with a Trend badge.
    assert "Strumenti &amp; API" in html
    assert "Repo trend" in html
    assert "Trend 72" in html
    # Old dense ecosystem sections and filters are gone.
    assert "Builder Tools" not in html
    assert "sourceGroupFilter" not in html
    assert "signalTypeFilter" not in html
