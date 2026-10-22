"""Pytest coverage for basic project validation paths."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import date

import pytest

from src.collectors.base import BaseCollector, SourceConfig, normalize_item
from src.collectors.collector_service import CollectorService
from src.processors.rule_based_processor import RuleBasedProcessor
from src.processors.validation import content_quality_counts, validate_processed_items
from src.storage.json_storage import load_json, save_json


class FakeCollector(BaseCollector):
    """Collector used to exercise orchestration behavior without network calls."""

    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items

    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        return self.items


@pytest.fixture
def source() -> SourceConfig:
    return SourceConfig(
        id="example_source",
        name="Example Source",
        company="Example",
        url="https://example.com/news",
        type="html",
        category_hint="general_ai",
        enabled=True,
    )


@pytest.fixture
def raw_item() -> dict[str, str]:
    return {
        "title": "GitHub Copilot agent update",
        "url": "https://example.com/original",
        "source_id": "github_copilot",
        "source_name": "GitHub Copilot",
        "company": "GitHub",
        "category_hint": "coding_ai",
        "published_at": "2026-05-06",
        "collected_at": "2026-05-06T10:00:00+00:00",
        "raw_summary": "Developer tooling update.",
        "excerpt": "GitHub Copilot adds better coding workflows for developers.",
        "raw_content": "",
        "content_quality": "excerpt",
    }


def write_settings(path: Path) -> None:
    path.write_text("max_items_per_source: 10\n", encoding="utf-8")


def test_source_config_loading_returns_only_enabled_sources(tmp_path: Path) -> None:
    sources_path = tmp_path / "sources.json"
    settings_path = tmp_path / "settings.yaml"
    sources_path.write_text(
        json.dumps({"sources": [
            {"id": "openai_news", "name": "OpenAI News", "company": "OpenAI",
             "url": "https://openai.com/news/", "type": "html", "category_hint": "general_ai",
             "enabled": True, "allowed_domains": ["openai.com"]},
            {"id": "disabled_source", "name": "Disabled Source", "company": "Example",
             "url": "https://example.com/", "type": "html", "category_hint": "research",
             "enabled": False},
        ]}),
        encoding="utf-8",
    )
    write_settings(settings_path)

    sources = CollectorService(
        sources_path=sources_path,
        settings_path=settings_path,
        collectors={},
    ).load_enabled_sources()

    assert [source.id for source in sources] == ["openai_news"]
    assert sources[0].url == "https://openai.com/news/"
    assert sources[0].allowed_domains == ("openai.com",)


def test_collector_service_deduplicates_by_url(tmp_path: Path, source: SourceConfig) -> None:
    sources_path = tmp_path / "sources.json"
    settings_path = tmp_path / "settings.yaml"
    sources_path.write_text(
        json.dumps({"sources": [
            {"id": "example_source", "name": "Example Source", "company": "Example",
             "url": "https://example.com/news", "type": "html",
             "category_hint": "general_ai", "enabled": True},
        ]}),
        encoding="utf-8",
    )
    write_settings(settings_path)
    first = normalize_item(source, title="First", url="https://example.com/a")
    duplicate = normalize_item(source, title="Duplicate", url="https://example.com/a")

    result = CollectorService(
        sources_path=sources_path,
        settings_path=settings_path,
        collectors={"html": FakeCollector([first, duplicate])},
    ).collect()

    assert len(result["collected_items"]) == 1
    assert result["collected_items"][0]["title"] == "First"
    assert result["collected_items"][0]["url"] == "https://example.com/a"


def test_processor_preserves_original_url(raw_item: dict[str, str]) -> None:
    processed = RuleBasedProcessor().process_item(raw_item)

    assert processed["url"] == "https://example.com/original"


@pytest.mark.parametrize(
    ("title", "expected_category"),
    [
        ("New CLI coding agent for developers", "coding_ai"),
        ("Image diffusion model for video generation", "image_generation"),
        ("Suno launches audio and song tools", "music_generation"),
        ("Research paper with benchmark evals", "research"),
        ("Enterprise product pricing launch", "business_product"),
        ("Multimodal assistant reasoning API", "general_ai"),
    ],
)
def test_keyword_category_classification(
    raw_item: dict[str, str],
    title: str,
    expected_category: str,
) -> None:
    processed = RuleBasedProcessor().process_item(
        {
            **raw_item,
            "title": title,
            "category_hint": "",
            "raw_summary": "",
            "excerpt": "",
            "raw_content": "",
        }
    )

    assert processed["category"] == expected_category


def test_json_storage_save_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "news.json"
    payload = [{"title": "Novità AI", "url": "https://example.com"}]

    saved_path = save_json(path, payload)

    assert saved_path == path
    assert load_json(path) == payload
    assert "Novità AI" in path.read_text(encoding="utf-8")


def test_validate_processed_items_requires_core_fields() -> None:
    valid_item = {
        "title": "News",
        "url": "https://example.com",
        "source_name": "Example Source",
        "company": "Example",
        "category": "general_ai",
        "summary": "News summary.",
        "why_it_matters": "This affects planning.",
        "possible_action": "Review the roadmap.",
        "primary_topic": "General AI",
        "signal_score": 42,
        "summary_quality": "medium",
        "source_group": "foundation_models",
        "signal_type": "official_news",
        "trend_score": 0,
    }
    invalid_item = {**valid_item, "summary": "", "url": ""}

    errors = validate_processed_items([valid_item, invalid_item])

    assert errors == [
        {
            "index": 1,
            "url": "",
            "missing_fields": ["url", "summary"],
        }
    ]


def test_content_quality_counts_reports_distribution() -> None:
    counts = content_quality_counts(
        [
            {"content_quality": "full"},
            {"content_quality": "excerpt"},
            {"content_quality": "title_only"},
            {"content_quality": "invalid"},
            {},
        ]
    )

    assert counts == {"full": 1, "excerpt": 1, "title_only": 2, "invalid": 1}


def test_validate_command_reports_sources_and_quality(tmp_path: Path, monkeypatch, capsys) -> None:
    from src.main import validate_news

    monkeypatch.chdir(tmp_path)
    today = date.today().isoformat()
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data" / today
    config_dir.mkdir()
    data_dir.mkdir(parents=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "sources.json").write_text(
        json.dumps({"sources": [
            {"id": "enabled_source", "name": "Enabled Source", "company": "Example",
             "url": "https://example.com/feed.xml", "type": "rss", "category_hint": "general_ai",
             "enabled": True, "notes": "verified"},
            {"id": "disabled_source", "name": "Disabled Source", "company": "Example",
             "url": "https://example.com/", "type": "html", "category_hint": "general_ai",
             "enabled": False, "notes": "disabled"},
        ]}),
        encoding="utf-8",
    )
    save_json(
        data_dir / "news.json",
        [
            {
                "title": "News",
                "url": "https://example.com/news",
                "source_name": "Enabled Source",
                "company": "Example",
                "category": "general_ai",
                "summary": "In breve: News",
                "content_quality": "full",
                "relevance_score": 5,
                "signal_score": 75,
                "summary_quality": "high",
                "primary_topic": "Foundation Models",
                "why_it_matters": "This affects model evaluation.",
                "possible_action": "Add it to the evaluation backlog.",
                "source_group": "foundation_models",
                "signal_type": "official_news",
                "trend_score": 0,
                "source_id": "enabled_source",
            }
        ],
    )
    save_json(
        data_dir / "collection_report.json",
        {
            "failed_sources": [
                {"source_id": "blocked", "error": "403"},
            ],
            "excluded_wrong_domain_items": 2,
        },
    )

    assert validate_news() == 0
    output = capsys.readouterr().out

    assert "Total processed items: 1" in output
    assert "Items with full content: 1" in output
    assert "Enabled sources: 1" in output
    assert "Disabled sources: 1" in output
    assert "Failed sources: 1" in output
    assert "Relevance score distribution: {'5': 1}" in output
    assert "Signal score distribution: {'80-100': 0, '60-79': 1, '40-59': 0, '20-39': 0, '0-19': 0}" in output
    assert "Summary quality distribution: {'high': 1, 'medium': 0, 'low': 0}" in output
    assert "Topic distribution: {'Foundation Models': 1}" in output
    assert "Excluded wrong-domain items: 2" in output
