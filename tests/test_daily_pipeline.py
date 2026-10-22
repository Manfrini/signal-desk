"""Tests for the daily pipeline orchestration."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.pipeline.daily_pipeline import DailyPipeline
from src.storage.json_storage import load_read_urls, save_json


def raw_item() -> dict[str, str]:
    return {
        "title": "GitHub Copilot update",
        "clean_title": "GitHub Copilot update",
        "url": "https://example.com/copilot",
        "source_id": "github_copilot_changelog",
        "source_name": "GitHub",
        "company": "GitHub",
        "category_hint": "coding_ai",
        "published_at": "2026-05-05",
        "collected_at": "2026-05-05T10:00:00+00:00",
        "raw_summary": "Developer update",
        "excerpt": "GitHub Copilot adds enterprise coding agent workflows for developer teams.",
        "raw_content": "GitHub Copilot adds enterprise coding agent workflows for developer teams. The update helps teams govern AI-assisted development and measure adoption.",
        "content_quality": "excerpt",
        "date_quality": "reliable",
    }


class FakeCollectorService:
    def collect(self):
        return {
            "collected_items": [raw_item()],
            "collection_errors": [
                {"source_id": "blocked", "source_name": "Blocked", "url": "https://blocked", "error": "403"}
            ],
            "skipped_old_items": 2,
            "skipped_read_items": 3,
            "skipped_unparseable_date_items": 4,
        }


class DailyPipelineTest(unittest.TestCase):
    def test_pipeline_continues_with_collection_errors_and_uses_preview_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: true\n",
                encoding="utf-8",
            )

            from src.outputs.output_service import OutputService
            from src.processors.processor_service import ProcessorService

            pipeline = DailyPipeline(
                collector_service=FakeCollectorService(),
                processor_service=ProcessorService(base_dir=tmp_path, date=date(2026, 5, 5), llm_enabled=False),
                output_service=OutputService(
                    settings_path=settings_path,
                    dashboard_dir=tmp_path / "dashboard",
                    email_dir=tmp_path / "email",
                    date=date(2026, 5, 5),
                ),
                base_dir=tmp_path,
                date=date(2026, 5, 5),
            )

            result = pipeline.run(dry_run=True)

            self.assertEqual(result["collected_count"], 1)
            self.assertEqual(result["processed_count"], 1)
            self.assertEqual(result["dashboard_items_count"], 1)
            self.assertEqual(result["errors_count"], 1)
            self.assertEqual(result["dashboard_html_path"], tmp_path / "dashboard" / "preview_index.html")
            self.assertEqual(result["email_html_path"].name, "preview_2026-05-05.html")
            self.assertEqual(result["email_text_path"].name, "preview_2026-05-05.txt")
            self.assertEqual(result["raw_news_path"].name, "preview_raw_news.json")
            self.assertEqual(result["processed_news_path"].name, "preview_news.json")
            self.assertEqual(result["daily_intelligence_summary_path"].name, "preview_daily_intelligence_summary.json")
            self.assertEqual(result["email_sending"], "skipped dry-run")
            self.assertEqual(result["skipped_old_items"], 2)
            self.assertEqual(result["skipped_read_items"], 3)
            self.assertEqual(result["skipped_unparseable_date_items"], 4)
            self.assertTrue(result["raw_news_path"].exists())
            self.assertTrue(result["processed_news_path"].exists())
            self.assertTrue(result["daily_intelligence_summary_path"].exists())

    def test_pipeline_updates_read_state_only_for_non_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            read_state_path = tmp_path / "read_urls.json"
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: false\n",
                encoding="utf-8",
            )

            class ReadStateCollectorService(FakeCollectorService):
                def collect(self):
                    result = super().collect()
                    result["collection_errors"] = []
                    result["read_state_path"] = read_state_path
                    return result

            from src.outputs.output_service import OutputService
            from src.processors.processor_service import ProcessorService

            dry_pipeline = DailyPipeline(
                collector_service=ReadStateCollectorService(),
                processor_service=ProcessorService(base_dir=tmp_path, date=date(2026, 5, 5), llm_enabled=False),
                output_service=OutputService(
                    settings_path=settings_path,
                    dashboard_dir=tmp_path / "dashboard",
                    email_dir=tmp_path / "email",
                    date=date(2026, 5, 5),
                ),
                base_dir=tmp_path,
                date=date(2026, 5, 5),
            )
            dry_pipeline.run(dry_run=True)

            self.assertFalse(read_state_path.exists())

            normal_pipeline = DailyPipeline(
                collector_service=ReadStateCollectorService(),
                processor_service=ProcessorService(base_dir=tmp_path, date=date(2026, 5, 5), llm_enabled=False),
                output_service=OutputService(
                    settings_path=settings_path,
                    dashboard_dir=tmp_path / "dashboard",
                    email_dir=tmp_path / "email",
                    date=date(2026, 5, 5),
                ),
                base_dir=tmp_path,
                date=date(2026, 5, 5),
            )
            normal_pipeline.run(dry_run=False)

            self.assertEqual(load_read_urls(read_state_path), {"https://example.com/copilot"})

    def test_crash_during_processing_does_not_mark_urls_as_read(self) -> None:
        # If processing raises, the item never makes it into news.json. If we
        # had already marked its URL as read, it would be permanently lost:
        # tomorrow's collection would skip it as already-seen, and it would
        # never be reprocessed. Marking-read must happen only after
        # processing succeeds.
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            read_state_path = tmp_path / "read_urls.json"
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: false\n",
                encoding="utf-8",
            )

            class ReadStateCollectorService(FakeCollectorService):
                def collect(self):
                    result = super().collect()
                    result["collection_errors"] = []
                    result["read_state_path"] = read_state_path
                    return result

            class CrashingProcessorService:
                def process(self, raw_items, preview=False):
                    raise RuntimeError("boom: simulated processing crash")

            pipeline = DailyPipeline(
                collector_service=ReadStateCollectorService(),
                processor_service=CrashingProcessorService(),
                base_dir=tmp_path,
                date=date(2026, 5, 5),
            )

            with self.assertRaises(RuntimeError):
                pipeline.run(dry_run=False)

            self.assertEqual(load_read_urls(read_state_path), set())

    def test_dashboard_uses_recent_history_when_current_run_has_no_new_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: false\n",
                encoding="utf-8",
            )
            historical_item = {
                "title": "Historical signal",
                "url": "https://example.com/historical",
                "source_name": "Example Source",
                "company": "Example",
                "category": "general_ai",
                "summary": "Historical summary",
                "why_it_matters": "It still matters for the dashboard.",
                "possible_action": "Keep it visible in recent history.",
                "primary_topic": "General AI",
                "signal_score": 50,
                "summary_quality": "medium",
                "content_quality": "excerpt",
                "relevance_score": 4,
                "source_group": "foundation_models",
                "signal_type": "official_news",
                "trend_score": 0,
            }
            save_json(tmp_path / "2026-05-06" / "news.json", [historical_item])

            class EmptyCollectorService:
                def collect(self):
                    return {
                        "collected_items": [],
                        "collection_errors": [],
                        "skipped_old_items": 0,
                        "skipped_read_items": 1,
                        "skipped_unparseable_date_items": 0,
                    }

                def load_sources(self):
                    return []

            from src.outputs.output_service import OutputService
            from src.processors.processor_service import ProcessorService

            pipeline = DailyPipeline(
                collector_service=EmptyCollectorService(),
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

            result = pipeline.run(dry_run=False)

            self.assertEqual(result["processed_count"], 0)
            self.assertEqual(result["dashboard_items_count"], 1)
            dashboard_html = (tmp_path / "dashboard" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Historical signal", dashboard_html)
            self.assertIn("seen", dashboard_html)

    def test_second_run_same_day_merges_instead_of_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            read_state_path = tmp_path / "read_urls.json"
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: false\n",
                encoding="utf-8",
            )

            def item(url: str, title: str) -> dict[str, str]:
                return {
                    **raw_item(),
                    "url": url,
                    "title": title,
                    "clean_title": title,
                }

            class FirstRunCollectorService:
                def collect(self):
                    return {
                        "collected_items": [item("https://example.com/first", "First run item")],
                        "collection_errors": [],
                        "skipped_old_items": 0,
                        "skipped_read_items": 0,
                        "skipped_unparseable_date_items": 0,
                        "read_state_path": read_state_path,
                    }

            class SecondRunCollectorService:
                def collect(self):
                    # The first run's item is now in read_urls, so a real
                    # collector would no longer return it here either.
                    return {
                        "collected_items": [item("https://example.com/second", "Second run item")],
                        "collection_errors": [],
                        "skipped_old_items": 0,
                        "skipped_read_items": 1,
                        "skipped_unparseable_date_items": 0,
                        "read_state_path": read_state_path,
                    }

            from src.outputs.output_service import OutputService
            from src.processors.processor_service import ProcessorService
            from src.storage.json_storage import load_json, processed_news_path

            def make_pipeline(collector_service):
                return DailyPipeline(
                    collector_service=collector_service,
                    processor_service=ProcessorService(base_dir=tmp_path, date=date(2026, 5, 5), llm_enabled=False),
                    output_service=OutputService(
                        settings_path=settings_path,
                        dashboard_dir=tmp_path / "dashboard",
                        email_dir=tmp_path / "email",
                        date=date(2026, 5, 5),
                    ),
                    base_dir=tmp_path,
                    date=date(2026, 5, 5),
                )

            make_pipeline(FirstRunCollectorService()).run(dry_run=False)
            make_pipeline(SecondRunCollectorService()).run(dry_run=False)

            news = load_json(processed_news_path(base_dir=tmp_path, date=date(2026, 5, 5)))
            urls = {item["url"] for item in news}
            self.assertEqual(urls, {"https://example.com/first", "https://example.com/second"})

    def test_single_source_rerun_merges_report_and_keeps_other_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            read_state_path = tmp_path / "read_urls.json"
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: false\n",
                encoding="utf-8",
            )

            def item(url: str, title: str, source_id: str) -> dict[str, str]:
                return {**raw_item(), "url": url, "title": title, "clean_title": title, "source_id": source_id}

            class FullRunCollectorService:
                def collect(self):
                    return {
                        "collected_items": [item("https://example.com/a", "From A", "source_a")],
                        "collection_errors": [
                            {"source_id": "source_b", "source_name": "Source B", "url": "https://b", "error": "403"}
                        ],
                        "skipped_old_items": 0,
                        "skipped_read_items": 0,
                        "skipped_unparseable_date_items": 0,
                        "read_state_path": read_state_path,
                        "per_source": [
                            {"source_id": "source_a", "source_name": "Source A", "found": 1, "accepted": 1, "status": "ok", "error": ""},
                            {"source_id": "source_b", "source_name": "Source B", "found": 0, "accepted": 0, "status": "error", "error": "403"},
                        ],
                    }

            class SingleSourceCollectorService:
                def collect_source(self, source_id):
                    assert source_id == "source_b"
                    return {
                        "collected_items": [item("https://example.com/b", "From B", "source_b")],
                        "collection_errors": [],
                        "skipped_old_items": 0,
                        "skipped_read_items": 0,
                        "skipped_unparseable_date_items": 0,
                        "read_state_path": read_state_path,
                        "per_source": [
                            {"source_id": "source_b", "source_name": "Source B", "found": 1, "accepted": 1, "status": "ok", "error": ""},
                        ],
                    }

            from src.outputs.output_service import OutputService
            from src.processors.processor_service import ProcessorService
            from src.storage.json_storage import load_json, processed_news_path

            def make_pipeline(collector_service):
                return DailyPipeline(
                    collector_service=collector_service,
                    processor_service=ProcessorService(base_dir=tmp_path, date=date(2026, 5, 5), llm_enabled=False),
                    output_service=OutputService(
                        settings_path=settings_path,
                        dashboard_dir=tmp_path / "dashboard",
                        email_dir=tmp_path / "email",
                        date=date(2026, 5, 5),
                    ),
                    base_dir=tmp_path,
                    date=date(2026, 5, 5),
                )

            make_pipeline(FullRunCollectorService()).run(dry_run=False)
            make_pipeline(SingleSourceCollectorService()).run(dry_run=False, source_id="source_b")

            news = load_json(processed_news_path(base_dir=tmp_path, date=date(2026, 5, 5)))
            urls = {n["url"] for n in news}
            self.assertEqual(urls, {"https://example.com/a", "https://example.com/b"})

            report = load_json(tmp_path / "2026-05-05" / "collection_report.json")
            per_source_by_id = {p["source_id"]: p for p in report["per_source"]}
            self.assertEqual(per_source_by_id["source_a"]["status"], "ok")
            self.assertEqual(per_source_by_id["source_b"]["status"], "ok")
            failed_ids = {f["source_id"] for f in report["failed_sources"]}
            self.assertNotIn("source_b", failed_ids)

    def test_single_source_rerun_adds_to_todays_aggregate_counters_instead_of_replacing_them(self) -> None:
        # Regression test: a per-source rerun's tiny counters (covering only
        # that one source) used to overwrite the whole day's totals.
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            read_state_path = tmp_path / "read_urls.json"
            settings_path = tmp_path / "settings.yaml"
            settings_path.write_text(
                "output_dashboard_enabled: true\noutput_email_enabled: false\n",
                encoding="utf-8",
            )

            def item(url: str, title: str, source_id: str) -> dict[str, str]:
                return {**raw_item(), "url": url, "title": title, "clean_title": title, "source_id": source_id}

            class FullRunCollectorService:
                def collect(self):
                    return {
                        "collected_items": [item("https://example.com/a", "From A", "source_a")],
                        "collection_errors": [],
                        "skipped_old_items": 40,
                        "skipped_read_items": 12,
                        "skipped_unparseable_date_items": 3,
                        "excluded_wrong_domain_items": 5,
                        "read_state_path": read_state_path,
                        "per_source": [
                            {"source_id": "source_a", "source_name": "Source A", "found": 1, "accepted": 1, "status": "ok", "error": ""},
                        ],
                    }

            class SingleSourceCollectorService:
                def collect_source(self, source_id):
                    return {
                        "collected_items": [item("https://example.com/b", "From B", "source_b")],
                        "collection_errors": [],
                        "skipped_old_items": 1,
                        "skipped_read_items": 0,
                        "skipped_unparseable_date_items": 0,
                        "excluded_wrong_domain_items": 0,
                        "read_state_path": read_state_path,
                        "per_source": [
                            {"source_id": "source_b", "source_name": "Source B", "found": 1, "accepted": 1, "status": "ok", "error": ""},
                        ],
                    }

            from src.outputs.output_service import OutputService
            from src.processors.processor_service import ProcessorService
            from src.storage.json_storage import load_json

            def make_pipeline(collector_service):
                return DailyPipeline(
                    collector_service=collector_service,
                    processor_service=ProcessorService(base_dir=tmp_path, date=date(2026, 5, 5), llm_enabled=False),
                    output_service=OutputService(
                        settings_path=settings_path,
                        dashboard_dir=tmp_path / "dashboard",
                        email_dir=tmp_path / "email",
                        date=date(2026, 5, 5),
                    ),
                    base_dir=tmp_path,
                    date=date(2026, 5, 5),
                )

            make_pipeline(FullRunCollectorService()).run(dry_run=False)
            make_pipeline(SingleSourceCollectorService()).run(dry_run=False, source_id="source_b")

            report = load_json(tmp_path / "2026-05-05" / "collection_report.json")
            # The single-source rerun's tiny counters must be *added* to the
            # full run's totals, not replace them.
            self.assertEqual(report["skipped_old_items"], 41)
            self.assertEqual(report["skipped_read_items"], 12)
            self.assertEqual(report["skipped_unparseable_date_items"], 3)
            self.assertEqual(report["excluded_wrong_domain_items"], 5)

    def test_validate_merge_counts_recent_dashboard_items(self) -> None:
        from src.main import _merge_items_for_validation

        current = [{"title": "Current", "url": "https://example.com/current"}]
        recent = [
            {"title": "Historical", "url": "https://example.com/historical"},
            {"title": "Current older", "url": "https://example.com/current"},
        ]

        merged = _merge_items_for_validation(current, recent)

        self.assertEqual(len(merged), 2)
        self.assertIn("Current", {item["title"] for item in merged})
        self.assertIn("Historical", {item["title"] for item in merged})


if __name__ == "__main__":
    unittest.main()
