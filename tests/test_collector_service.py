"""Tests for collector orchestration."""

from __future__ import annotations

import json
import tempfile
import unittest
import logging
from datetime import date
from pathlib import Path

from src.collectors.base import BaseCollector, SourceConfig, normalize_item
from src.collectors.collector_service import CollectorService


class FakeCollector(BaseCollector):
    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items

    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        return self.items


class FailingCollector(BaseCollector):
    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        raise RuntimeError("source unavailable")


class CollectorServiceTest(unittest.TestCase):
    def test_collect_deduplicates_by_url_and_skips_missing_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sources_path = tmp_path / "sources.json"
            settings_path = tmp_path / "settings.yaml"
            sources_path.write_text(
                json.dumps({"sources": [
                    {"id": "source_one", "name": "Source One", "company": "Example",
                     "url": "https://example.com/news", "type": "html",
                     "category_hint": "general_ai", "enabled": True},
                ]}),
                encoding="utf-8",
            )
            settings_path.write_text("max_items_per_source: 10\n", encoding="utf-8")

            source = SourceConfig(
                id="source_one",
                name="Source One",
                company="Example",
                url="https://example.com/news",
                type="html",
                category_hint="general_ai",
                enabled=True,
            )
            first = normalize_item(source, title="First", url="https://example.com/a")
            duplicate = normalize_item(source, title="Duplicate", url="https://example.com/a")
            missing_url = {
                key: ""
                for key in (
                    "title",
                    "url",
                    "source_id",
                    "source_name",
                    "company",
                    "category_hint",
                    "published_at",
                    "collected_at",
                    "raw_summary",
                    "raw_content",
                )
            }

            service = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"html": FakeCollector([first, duplicate, missing_url])},
            )

            result = service.collect()

            self.assertEqual(len(result["collected_items"]), 1)
            self.assertEqual(result["collected_items"][0]["url"], "https://example.com/a")
            self.assertEqual(result["collection_errors"], [])

    def test_collect_reports_per_source_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sources_path = tmp_path / "sources.json"
            settings_path = tmp_path / "settings.yaml"
            sources_path.write_text(
                json.dumps({"sources": [
                    {"id": "ok_source", "name": "OK Source", "company": "Example",
                     "url": "https://example.com/news", "type": "html",
                     "category_hint": "general_ai", "source_group": "product_data", "enabled": True},
                    {"id": "broken_source", "name": "Broken Source", "company": "Example",
                     "url": "https://example.com/x", "type": "nonexistent_type",
                     "category_hint": "general_ai", "enabled": True},
                ]}),
                encoding="utf-8",
            )
            settings_path.write_text("max_items_per_source: 10\n", encoding="utf-8")
            source = SourceConfig(id="ok_source", name="OK Source", company="Example",
                                  url="https://example.com/news", type="html",
                                  category_hint="general_ai", enabled=True)
            items = [normalize_item(source, title="A", url="https://example.com/a"),
                     normalize_item(source, title="B", url="https://example.com/b")]
            service = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"html": FakeCollector(items)},
            )

            per_source = {s["source_id"]: s for s in service.collect()["per_source"]}

            self.assertEqual(per_source["ok_source"]["status"], "ok")
            self.assertEqual(per_source["ok_source"]["found"], 2)
            self.assertEqual(per_source["ok_source"]["accepted"], 2)
            self.assertEqual(per_source["ok_source"]["source_group"], "product_data")
            self.assertEqual(per_source["broken_source"]["status"], "error")
            self.assertIn("Unsupported", per_source["broken_source"]["error"])

    def test_collect_source_only_collects_the_requested_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sources_path = tmp_path / "sources.json"
            settings_path = tmp_path / "settings.yaml"
            sources_path.write_text(
                json.dumps({"sources": [
                    {"id": "source_one", "name": "Source One", "company": "Example",
                     "url": "https://example.com/one", "type": "html",
                     "category_hint": "general_ai", "enabled": True},
                    {"id": "source_two", "name": "Source Two", "company": "Example",
                     "url": "https://example.com/two", "type": "html",
                     "category_hint": "general_ai", "enabled": True},
                ]}),
                encoding="utf-8",
            )
            settings_path.write_text("max_items_per_source: 10\n", encoding="utf-8")
            source_one = SourceConfig(id="source_one", name="Source One", company="Example",
                                       url="https://example.com/one", type="html",
                                       category_hint="general_ai", enabled=True)
            items = [normalize_item(source_one, title="A", url="https://example.com/a")]

            class OnlySourceOneCollector(BaseCollector):
                def collect(self, source: SourceConfig) -> list[dict[str, str]]:
                    self_test.assertEqual(source.id, "source_one")
                    return items

            self_test = self
            service = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"html": OnlySourceOneCollector()},
            )

            result = service.collect_source("source_one")

            self.assertEqual(len(result["collected_items"]), 1)
            self.assertEqual(len(result["per_source"]), 1)
            self.assertEqual(result["per_source"][0]["source_id"], "source_one")

    def test_collect_source_works_even_if_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sources_path = tmp_path / "sources.json"
            settings_path = tmp_path / "settings.yaml"
            sources_path.write_text(
                json.dumps({"sources": [
                    {"id": "disabled_source", "name": "Disabled Source", "company": "Example",
                     "url": "https://example.com/x", "type": "html",
                     "category_hint": "general_ai", "enabled": False},
                ]}),
                encoding="utf-8",
            )
            settings_path.write_text("max_items_per_source: 10\n", encoding="utf-8")
            service = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"html": FakeCollector([])},
            )

            result = service.collect_source("disabled_source")

            self.assertEqual(result["per_source"][0]["source_id"], "disabled_source")

    def test_collect_source_raises_for_unknown_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sources_path = tmp_path / "sources.json"
            settings_path = tmp_path / "settings.yaml"
            sources_path.write_text(json.dumps({"sources": []}), encoding="utf-8")
            settings_path.write_text("max_items_per_source: 10\n", encoding="utf-8")
            service = CollectorService(sources_path=sources_path, settings_path=settings_path)

            with self.assertRaises(ValueError):
                service.collect_source("nope")

    def test_collect_records_source_errors_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sources_path = tmp_path / "sources.json"
            settings_path = tmp_path / "settings.yaml"
            sources_path.write_text(
                json.dumps({"sources": [
                    {"id": "source_one", "name": "Source One", "company": "Example",
                     "url": "https://example.com/news", "type": "html",
                     "category_hint": "general_ai", "enabled": True},
                ]}),
                encoding="utf-8",
            )
            settings_path.write_text("max_items_per_source: 10\n", encoding="utf-8")

            service = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"html": FailingCollector()},
            )

            logging.disable(logging.CRITICAL)
            try:
                result = service.collect()
            finally:
                logging.disable(logging.NOTSET)

            self.assertEqual(result["collected_items"], [])
            self.assertEqual(len(result["collection_errors"]), 1)
            self.assertEqual(result["collection_errors"][0]["source_id"], "source_one")
            self.assertIn("source unavailable", result["collection_errors"][0]["error"])

    def test_collect_skips_items_older_than_lookback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source = SourceConfig(
                id="source_one",
                name="Source One",
                company="Example",
                url="https://example.com/feed.xml",
                type="rss",
                category_hint="general_ai",
                enabled=True,
            )
            recent = normalize_item(
                source,
                title="Recent",
                url="https://example.com/recent",
                published_at="2026-05-05",
            )
            old = normalize_item(
                source,
                title="Old",
                url="https://example.com/old",
                published_at="2026-04-20",
            )
            sources_path, settings_path = self._write_config(tmp_path, source.type)

            result = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"rss": FakeCollector([recent, old])},
                today=date(2026, 5, 6),
            ).collect()

            self.assertEqual([item["url"] for item in result["collected_items"]], ["https://example.com/recent"])
            self.assertEqual(result["skipped_old_items"], 1)

    def test_collect_skips_already_read_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            read_state_path = tmp_path / "read_urls.json"
            read_state_path.write_text(
                '{"urls": ["https://example.com/read"]}\n',
                encoding="utf-8",
            )
            source = SourceConfig(
                id="source_one",
                name="Source One",
                company="Example",
                url="https://example.com/feed.xml",
                type="rss",
                category_hint="general_ai",
                enabled=True,
            )
            read = normalize_item(
                source,
                title="Read",
                url="https://example.com/read",
                published_at="2026-05-05",
            )
            unread = normalize_item(
                source,
                title="Unread",
                url="https://example.com/unread",
                published_at="2026-05-05",
            )
            sources_path, settings_path = self._write_config(
                tmp_path,
                source.type,
                read_state_path=read_state_path,
            )

            result = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"rss": FakeCollector([read, unread])},
                today=date(2026, 5, 6),
            ).collect()

            self.assertEqual([item["url"] for item in result["collected_items"]], ["https://example.com/unread"])
            self.assertEqual(result["skipped_read_items"], 1)

    def test_collect_keeps_missing_dates_for_html_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source = SourceConfig(
                id="source_one",
                name="Source One",
                company="Example",
                url="https://example.com/news",
                type="html",
                category_hint="general_ai",
                enabled=True,
            )
            item = normalize_item(
                source,
                title="HTML item without date",
                url="https://example.com/news/item",
                published_at="",
            )
            sources_path, settings_path = self._write_config(tmp_path, source.type)

            result = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"html": FakeCollector([item])},
                today=date(2026, 5, 6),
            ).collect()

            self.assertEqual(len(result["collected_items"]), 1)
            self.assertEqual(result["skipped_unparseable_date_items"], 0)

    def test_collect_skips_missing_dates_for_rss_sources_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source = SourceConfig(
                id="source_one",
                name="Source One",
                company="Example",
                url="https://example.com/feed.xml",
                type="rss",
                category_hint="general_ai",
                enabled=True,
            )
            item = normalize_item(
                source,
                title="RSS item without date",
                url="https://example.com/rss/item",
                published_at="",
            )
            sources_path, settings_path = self._write_config(tmp_path, source.type)

            result = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"rss": FakeCollector([item])},
                today=date(2026, 5, 6),
            ).collect()

            self.assertEqual(len(result["collected_items"]), 1)
            # A missing date is now estimated from the run that saw the item,
            # so it can age out of the recency window. What this test guards
            # stays the same: no crash, and not counted as unparseable.
            self.assertEqual(result["collected_items"][0]["date_quality"], "estimated")
            self.assertEqual(result["skipped_unparseable_date_items"], 0)

    def test_collect_rejects_wrong_domain_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source = SourceConfig(
                id="suno_blog",
                name="Suno Blog",
                company="Suno",
                url="https://suno.com/blog/",
                type="html",
                category_hint="music_generation",
                enabled=True,
                allowed_domains=("suno.com",),
            )
            allowed = normalize_item(
                source,
                title="Suno update",
                url="https://suno.com/blog/v5-5",
                published_at="",
            )
            wrong = normalize_item(
                source,
                title="External update",
                url="https://nvidia.github.io/NeMo/blogs/2024/2024-01-parakeet/",
                published_at="",
            )
            sources_path, settings_path = self._write_config(
                tmp_path,
                source.type,
                allowed_domains=("suno.com",),
            )

            result = CollectorService(
                sources_path=sources_path,
                settings_path=settings_path,
                collectors={"html": FakeCollector([allowed, wrong])},
                today=date(2026, 5, 6),
            ).collect()

            self.assertEqual([item["url"] for item in result["collected_items"]], ["https://suno.com/blog/v5-5"])
            self.assertEqual(result["excluded_wrong_domain_items"], 1)

    def _write_config(
        self,
        tmp_path: Path,
        source_type: str,
        read_state_path: Path | None = None,
        allowed_domains: tuple[str, ...] = ("example.com",),
    ) -> tuple[Path, Path]:
        sources_path = tmp_path / "sources.json"
        settings_path = tmp_path / "settings.yaml"
        sources_path.write_text(
            json.dumps({"sources": [
                {"id": "source_one", "name": "Source One", "company": "Example",
                 "url": "https://example.com/news", "allowed_domains": list(allowed_domains),
                 "type": source_type, "category_hint": "general_ai", "enabled": True},
            ]}),
            encoding="utf-8",
        )
        configured_read_state_path = read_state_path or (tmp_path / "read_urls.json")
        settings_path.write_text(
            f"max_items_per_source: 10\nlookback_days: 7\nread_state_path: {configured_read_state_path}\n",
            encoding="utf-8",
        )
        return sources_path, settings_path


if __name__ == "__main__":
    unittest.main()
