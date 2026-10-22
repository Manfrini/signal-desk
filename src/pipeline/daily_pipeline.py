"""Full V1 daily pipeline orchestration."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date as date_type
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.collectors.collector_service import CollectorService
from src.outputs.output_service import OutputService
from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer, is_available, quota_block
from src.processors.intelligence import daily_intelligence_summary
from src.processors.llm_budget import is_budget_exceeded
from src.processors.llm_health import claude_cli_health, write_llm_status
from src.processors.processor_service import ProcessorService
from src.processors.profile import profile_context
from src.processors.section_briefs import generate_section_briefs
from src.storage.json_storage import (
    append_llm_usage_log,
    date_folder,
    load_json,
    load_recent_processed_news,
    mark_read_urls,
    raw_news_path,
    update_trend_state_from_items,
    save_json,
    save_daily_intelligence_summary,
    save_preview_raw_news,
    save_preview_daily_intelligence_summary,
    save_preview_section_briefs,
    save_raw_news,
    save_section_briefs,
    today_utc,
)

logger = logging.getLogger(__name__)


class DailyPipeline:
    """Run the collect, process, and output generation pipeline."""

    def __init__(
        self,
        collector_service: CollectorService | None = None,
        processor_service: ProcessorService | None = None,
        output_service: OutputService | None = None,
        base_dir: str | Path = "data",
        date: date_type | None = None,
    ) -> None:
        # Resolved once, eagerly (UTC) -- so every sub-service shares the
        # exact same day even if the run happens to straddle midnight UTC,
        # instead of each one independently (and possibly differently)
        # resolving "today" a few statements apart.
        self.date = date or today_utc()
        self.base_dir = base_dir
        self.collector_service = collector_service or CollectorService()
        self.processor_service = processor_service or ProcessorService(
            base_dir=base_dir,
            date=self.date,
        )
        self.output_service = output_service or OutputService(date=self.date)

    def run(self, dry_run: bool, source_id: str | None = None) -> dict[str, Any]:
        """Run the full daily pipeline.

        If ``source_id`` is given, only that source is (re)collected — the
        rest of the pipeline (merge with today's data, processing, dashboard
        regeneration) proceeds exactly as usual, for a manual per-source
        rerun from the Fonti page.
        """
        logger.info("Step 1/7: collecting enabled sources")
        collection_result = (
            self.collector_service.collect_source(source_id)
            if source_id
            else self.collector_service.collect()
        )
        collected_items = collection_result["collected_items"]
        collection_errors = collection_result["collection_errors"]
        skipped_old_items = int(collection_result.get("skipped_old_items", 0))
        skipped_read_items = int(collection_result.get("skipped_read_items", 0))
        skipped_unparseable_date_items = int(
            collection_result.get("skipped_unparseable_date_items", 0)
        )
        excluded_wrong_domain_items = int(collection_result.get("excluded_wrong_domain_items", 0))
        configured_read_state_path = collection_result.get("read_state_path", "data/read_urls.json")
        configured_trend_state_path = collection_result.get("trend_state_path", "data/trend_state.json")
        report_path = self._save_collection_report(
            collection_errors,
            skipped_old_items=skipped_old_items,
            skipped_read_items=skipped_read_items,
            skipped_unparseable_date_items=skipped_unparseable_date_items,
            excluded_wrong_domain_items=excluded_wrong_domain_items,
            scoped_to_single_source=bool(source_id),
            trend_state_path=str(configured_trend_state_path),
            per_source=collection_result.get("per_source", []),
            dry_run=dry_run,
        )

        logger.info("Step 2/7: saving raw news")
        read_state_path: Path | None = None
        trend_state_path: Path | None = None
        if dry_run:
            raw_path = save_preview_raw_news(
                collected_items,
                base_dir=self.base_dir,
                date=self.date,
            )
        else:
            # Merge with whatever was already collected today so a second run
            # on the same day adds to today's data instead of replacing it
            # (collected_items alone would only hold this run's delta, since
            # already-read URLs from an earlier run today are excluded upstream).
            collected_items = _merge_items_by_url(
                collected_items,
                _load_existing_items(raw_news_path(base_dir=self.base_dir, date=self.date)),
            )
            raw_path = save_raw_news(collected_items, base_dir=self.base_dir, date=self.date)

        logger.info("Step 3/7: processing raw news")
        # mark_read_urls/update_trend_state_from_items intentionally run only
        # *after* processing succeeds (below), not here. If processing raised,
        # marking these URLs read now would permanently lose them: tomorrow's
        # run would skip them as already-read, but they'd never have made it
        # into news.json (today's raw_news.json, on the other hand, is safe to
        # have already been updated - it merges with future runs' data).
        processing_result = self.processor_service.process(
            raw_items=collected_items,
            preview=dry_run,
        )
        processed_items = processing_result["processed_items"]

        if not dry_run:
            read_state_path = mark_read_urls(collected_items, configured_read_state_path)
            trend_state_path = update_trend_state_from_items(
                collected_items,
                configured_trend_state_path,
            )

        logger.info("Step 4/7: saved processed news")
        processed_path = processing_result["output_path"]
        dashboard_items = _merge_items_by_url(
            processed_items,
            load_recent_processed_news(
                base_dir=self.base_dir,
                date=self.date,
                days=7,
                include_preview=False,
            ),
        )
        daily_summary = daily_intelligence_summary(dashboard_items)
        if dry_run:
            daily_summary_path = save_preview_daily_intelligence_summary(
                daily_summary,
                base_dir=self.base_dir,
                date=self.date,
            )
        else:
            daily_summary_path = save_daily_intelligence_summary(
                daily_summary,
                base_dir=self.base_dir,
                date=self.date,
            )

        self._generate_section_briefs(dashboard_items, dry_run=dry_run)

        logger.info("Step 5/7: generating dashboard")
        logger.info("Step 6/7: generating email HTML")
        output_result = self.output_service.generate(
            dashboard_items,
            dry_run=dry_run,
            daily_summary=daily_summary,
            email_items=processed_items,
        )
        quality_report = _quality_report(dashboard_items)

        logger.info("Step 7/7: email sending status: %s", output_result["email_sending"])

        return {
            "collected_count": len(collected_items),
            "processed_count": processing_result["processed_count"],
            "dashboard_items_count": len(dashboard_items),
            "errors_count": len(collection_errors),
            "collection_errors": collection_errors,
            "skipped_old_items": skipped_old_items,
            "skipped_read_items": skipped_read_items,
            "skipped_unparseable_date_items": skipped_unparseable_date_items,
            "excluded_wrong_domain_items": excluded_wrong_domain_items,
            "collection_report_path": report_path,
            "read_state_path": read_state_path,
            "trend_state_path": trend_state_path or configured_trend_state_path,
            "raw_news_path": raw_path,
            "processed_news_path": processed_path,
            "daily_intelligence_summary_path": daily_summary_path,
            "dashboard_html_path": output_result["dashboard_path"],
            "email_html_path": output_result["email_html_path"],
            "email_text_path": output_result["email_text_path"],
            "email_sending": output_result["email_sending"],
            **quality_report,
        }

    def _generate_section_briefs(
        self, dashboard_items: list[dict[str, Any]], *, dry_run: bool
    ) -> None:
        """Generate and persist a short brief per dashboard section."""
        goal = profile_context(getattr(self.processor_service, "profile", {}) or {})
        language = str(getattr(self.processor_service, "language", "") or "it")
        llm_enabled = bool(getattr(self.processor_service, "llm_enabled", False))
        analyzer = (
            ClaudeCliAnalyzer(goal=goal, language=language)
            if (llm_enabled and is_available() and not is_budget_exceeded(base_dir=self.base_dir))
            else None
        )
        briefs = generate_section_briefs(
            dashboard_items,
            goal=goal,
            llm_enabled=llm_enabled,
            analyzer=analyzer,
            language=language,
        )
        if dry_run:
            save_preview_section_briefs(briefs, base_dir=self.base_dir, date=self.date)
        else:
            save_section_briefs(briefs, base_dir=self.base_dir, date=self.date)
            if llm_enabled:
                # Record whether the configured LLM CLI is actually usable,
                # so the dashboard can surface a silent fallback instead of
                # hiding it.
                from src.settings import llm_provider as _configured_llm_provider

                health = claude_cli_health(provider=_configured_llm_provider())
                paused = quota_block()
                if health.get("ok") and paused:
                    # The CLI is installed and logged in, but the plan is out
                    # of quota: say so instead of reporting a healthy provider.
                    health = {**health, "ok": False, "reason": "quota", "detail": paused}
                write_llm_status(health, base_dir=self.base_dir)
        if analyzer is not None and analyzer.calls:
            append_llm_usage_log(
                {
                    "date": (self.date or today_utc()).isoformat(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "items_analyzed": 0,
                    "kind": "section_briefs",
                    **analyzer.usage_summary(),
                },
                Path(self.base_dir) / "llm_usage_log.json",
            )

    def _save_collection_report(
        self,
        collection_errors: list[dict[str, str]],
        skipped_old_items: int,
        skipped_read_items: int,
        skipped_unparseable_date_items: int,
        excluded_wrong_domain_items: int,
        trend_state_path: str,
        per_source: list[dict[str, Any]] | None = None,
        dry_run: bool = False,
        scoped_to_single_source: bool = False,
    ) -> Path:
        if hasattr(self.collector_service, "load_sources"):
            sources = self.collector_service.load_sources()
        else:
            sources = []
        enabled_sources = [source for source in sources if source.enabled]
        disabled_sources = [source for source in sources if not source.enabled]
        filename = "preview_collection_report.json" if dry_run else "collection_report.json"
        report_path = date_folder(base_dir=self.base_dir, date=self.date) / filename
        merged_per_source = per_source or []
        merged_errors = collection_errors
        if not dry_run:
            # A single-source rerun (or a second run today) only produces
            # stats for the sources it touched; merge with today's existing
            # report so untouched sources keep their last known stats
            # instead of vanishing from the Fonti table.
            existing_payload = load_json(report_path) if report_path.exists() else {}
            if not isinstance(existing_payload, dict):
                existing_payload = {}
            if scoped_to_single_source:
                # These counters cover only the one rerun source, not the
                # whole day; add them to today's existing totals instead of
                # replacing them, or a per-source rerun would make the day's
                # aggregate stats collapse down to that one source's numbers.
                skipped_old_items += int(existing_payload.get("skipped_old_items", 0) or 0)
                skipped_read_items += int(existing_payload.get("skipped_read_items", 0) or 0)
                skipped_unparseable_date_items += int(
                    existing_payload.get("skipped_unparseable_date_items", 0) or 0
                )
                excluded_wrong_domain_items += int(existing_payload.get("excluded_wrong_domain_items", 0) or 0)
            existing_per_source = existing_payload.get("per_source", []) if isinstance(existing_payload, dict) else []
            existing_failed = existing_payload.get("failed_sources", []) if isinstance(existing_payload, dict) else []
            merged_per_source = _merge_by_key(merged_per_source, existing_per_source, "source_id")
            # Sources touched this run get a fresh status either way (success
            # or failure, per_source always has an entry) - drop any stale
            # failure recorded for them earlier today before re-adding this
            # run's actual failures, so a source that now succeeds stops
            # showing up as failed.
            touched_ids = {
                str(item.get("source_id", "")).strip() for item in (per_source or []) if item.get("source_id")
            }
            existing_failed = [
                item for item in existing_failed if str(item.get("source_id", "")).strip() not in touched_ids
            ]
            merged_errors = existing_failed + collection_errors
        return save_json(
            report_path,
            {
                "enabled_sources_count": len(enabled_sources),
                "disabled_sources_count": len(disabled_sources),
                "enabled_sources": [source.id for source in enabled_sources],
                "disabled_sources": [
                    {
                        "source_id": source.id,
                        "source_name": source.name,
                        "notes": source.notes,
                    }
                    for source in disabled_sources
                ],
                "failed_sources": merged_errors,
                "per_source": merged_per_source,
                "skipped_old_items": skipped_old_items,
                "skipped_read_items": skipped_read_items,
                "skipped_unparseable_date_items": skipped_unparseable_date_items,
                "excluded_wrong_domain_items": excluded_wrong_domain_items,
                "trend_state_path": trend_state_path,
                "ecosystem_enabled_sources_count": sum(
                    1 for source in enabled_sources if source.source_group != "foundation_models"
                ),
                "ecosystem_disabled_sources_count": sum(
                    1 for source in disabled_sources if source.source_group != "foundation_models"
                ),
            },
        )


def _quality_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    content_quality = Counter(str(item.get("content_quality", "unknown")) for item in items)
    relevance = Counter(str(item.get("relevance_score", "unknown")) for item in items)
    signal_scores = Counter(_signal_bucket(int(item.get("signal_score", 0) or 0)) for item in items)
    summary_quality = Counter(str(item.get("summary_quality", "unknown")) for item in items)
    topics = Counter(str(item.get("primary_topic", "General AI")) for item in items)
    trend_scores = Counter(_trend_bucket(int(item.get("trend_score", 0) or 0)) for item in items)
    source_groups = Counter(str(item.get("source_group", "foundation_models")) for item in items)
    signal_types = Counter(str(item.get("signal_type", "official_news")) for item in items)
    title_only_by_source = Counter(
        str(item.get("source_id", "unknown"))
        for item in items
        if item.get("content_quality") == "title_only"
    )
    excluded_items = [
        item
        for item in items
        if item.get("content_quality") == "invalid" or int(item.get("relevance_score", 3) or 3) <= 1
    ]
    return {
        "excluded_count": len(excluded_items),
        "content_quality_distribution": dict(content_quality),
        "relevance_score_distribution": dict(relevance),
        "signal_score_distribution": dict(signal_scores),
        "summary_quality_distribution": dict(summary_quality),
        "topic_distribution": dict(topics),
        "trend_score_distribution": dict(trend_scores),
        "source_group_distribution": dict(source_groups),
        "signal_type_distribution": dict(signal_types),
        "top_trending_items": _top_trending_items(items),
        "title_only_by_source": dict(title_only_by_source),
    }


def _load_existing_items(path: Path) -> list[dict[str, Any]]:
    """Load a previously saved items file for merging, if one exists yet."""
    if not path.exists():
        return []
    payload = load_json(path)
    return payload if isinstance(payload, list) else []


def _merge_by_key(
    new_items: list[dict[str, Any]],
    existing_items: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    """Merge two lists of dicts by a string key, new entries winning on conflict."""
    merged: dict[str, dict[str, Any]] = {}
    for item in existing_items:
        item_key = str(item.get(key, "")).strip()
        if item_key:
            merged[item_key] = item
    for item in new_items:
        item_key = str(item.get(key, "")).strip()
        if item_key:
            merged[item_key] = item
    return list(merged.values())


def _merge_items_by_url(
    new_items: list[dict[str, Any]],
    historical_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in historical_items:
        url = str(item.get("url", "")).strip()
        if url:
            merged[url] = {**item, "run_status": item.get("run_status", "seen")}
    for item in new_items:
        url = str(item.get("url", "")).strip()
        if url:
            merged[url] = {**item, "run_status": "new"}
    return list(merged.values())


def _signal_bucket(score: int) -> str:
    if score >= 80:
        return "80-100"
    if score >= 60:
        return "60-79"
    if score >= 40:
        return "40-59"
    if score >= 20:
        return "20-39"
    return "0-19"


def _trend_bucket(score: int) -> str:
    if score >= 80:
        return "80-100"
    if score >= 60:
        return "60-79"
    if score >= 40:
        return "40-59"
    if score >= 20:
        return "20-39"
    return "0-19"


def _top_trending_items(items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ranked = sorted(
        [
            item
            for item in items
            if int(item.get("trend_score", 0) or 0) > 0
            and item.get("content_quality") != "invalid"
        ],
        key=lambda item: int(item.get("trend_score", 0) or 0),
        reverse=True,
    )
    return [
        {
            "title": item.get("title", ""),
            "source": item.get("source_name") or item.get("company") or "",
            "trend_score": int(item.get("trend_score", 0) or 0),
        }
        for item in ranked[:limit]
    ]
