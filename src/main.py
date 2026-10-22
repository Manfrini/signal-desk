"""CLI entry point for AI News Intelligence Agent."""

from __future__ import annotations

import argparse


def run_daily(dry_run: bool) -> None:
    """Run the daily pipeline."""
    from src.pipeline.daily_pipeline import DailyPipeline

    result = DailyPipeline().run(dry_run=dry_run)
    print_pipeline_summary(result)


def collect_sources(dry_run: bool) -> None:
    """Collect configured sources."""
    from src.collectors.collector_service import CollectorService

    result = CollectorService().collect()
    collected_items = result["collected_items"]
    collection_errors = result["collection_errors"]
    read_state_path = result.get("read_state_path", "data/read_urls.json")

    if dry_run:
        print("Collection dry-run complete.")
    else:
        from src.pipeline.daily_pipeline import _load_existing_items, _merge_items_by_url
        from src.storage.json_storage import (
            mark_read_urls,
            raw_news_path,
            save_raw_news,
            update_trend_state_from_items,
        )

        # Merge with whatever is already in today's raw_news.json so running
        # this command twice in a day adds to today's data instead of
        # replacing it (collected_items alone only holds this run's delta,
        # since already-read URLs from an earlier run today are excluded
        # upstream) - same fix already applied to the daily pipeline.
        collected_items = _merge_items_by_url(
            collected_items,
            _load_existing_items(raw_news_path()),
        )
        saved_path = save_raw_news(collected_items)
        updated_read_state_path = mark_read_urls(collected_items, read_state_path)
        updated_trend_state_path = update_trend_state_from_items(
            collected_items,
            result.get("trend_state_path", "data/trend_state.json"),
        )
        print("Collection complete.")
        print(f"Saved raw news: {saved_path}")
        print(f"Updated read state: {updated_read_state_path}")
        print(f"Updated trend state: {updated_trend_state_path}")

    print(f"Items collected: {len(collected_items)}")
    print(f"Collection errors: {len(collection_errors)}")
    print(f"Skipped old items: {result.get('skipped_old_items', 0)}")
    print(f"Skipped already-read items: {result.get('skipped_read_items', 0)}")
    print(f"Skipped unparseable-date items: {result.get('skipped_unparseable_date_items', 0)}")
    print(f"Excluded wrong-domain items: {result.get('excluded_wrong_domain_items', 0)}")

    if collection_errors:
        print("Errors:")
        for error in collection_errors:
            print(f"- {error['source_id']}: {error['error']}")


def process_news() -> None:
    """Process today's raw news file."""
    from src.processors.processor_service import MissingRawNewsError, ProcessorService

    try:
        result = ProcessorService().process()
    except MissingRawNewsError as exc:
        print(str(exc))
        print("Run `python -m src.main collect` before processing.")
        return

    print("Processing complete.")
    print(f"Read raw news: {result['input_path']}")
    print(f"Saved processed news: {result['output_path']}")
    print(f"Items processed: {result['processed_count']}")


def serve(port: int | None) -> None:
    """Start the local dashboard server with the in-app analyze button.

    ``port`` is None when the user did not pass --port: the server is then
    free to move to the next port if 8000 is taken. An explicit --port is a
    choice, so it is used as given and a busy port fails loudly.
    """
    from src.server import run_server

    run_server(port=port or 8000, allow_fallback=port is None)


def setup_wizard() -> int:
    """Run the interactive first-run setup wizard."""
    from src.setup_wizard import run_setup

    return run_setup()


def prune(keep_days: int, dry_run: bool) -> None:
    """Delete dated data older than ``keep_days`` and trim unbounded logs.

    Conservative by design (see src/storage/retention.py): only dated
    folders and state files with an unambiguous per-entry date are touched.
    read_urls.json, saved_articles.json, feedback.json, and
    preferences.json are never pruned here.
    """
    from src.storage.retention import (
        find_old_date_folders,
        prune_date_folders,
        prune_llm_insights,
        prune_llm_usage_log,
    )

    if dry_run:
        old_folders = find_old_date_folders(keep_days=keep_days)
        print(f"[dry-run] Cartelle da rimuovere ({len(old_folders)}):")
        for folder in old_folders:
            print(f"  - {folder}")
        print("[dry-run] Nessun file di stato è stato modificato.")
        return

    removed_folders = prune_date_folders(keep_days=keep_days)
    dropped_usage = prune_llm_usage_log(keep_days=keep_days)
    dropped_insights = prune_llm_insights(keep_days=keep_days)

    print(f"Cartelle rimosse: {len(removed_folders)}")
    for folder in removed_folders:
        print(f"  - {folder}")
    print(f"Record di consumo LLM rimossi: {dropped_usage}")
    print(f"Insight LLM in cache rimossi: {dropped_insights}")


def validate_news() -> int:
    """Validate today's processed news file."""
    from src.processors.validation import (
        content_quality_counts,
        excluded_noise_count,
        relevance_score_counts,
        signal_score_distribution,
        summary_quality_counts,
        title_only_counts_by_source,
        top_signal_items,
        top_sources_by_useful_signals,
        topic_distribution,
        trend_score_distribution,
        top_trending_items,
        source_group_counts,
        signal_type_counts,
        validate_processed_items,
    )
    from src.storage.json_storage import date_folder, load_json, load_recent_processed_news, processed_news_path

    news_path = processed_news_path()
    if not news_path.exists():
        print(f"Processed news file not found: {news_path}")
        print("Run `python -m src.main process` or `python -m src.main run-daily` before validation.")
        return 1

    items = load_json(news_path, default=[])
    if not isinstance(items, list):
        print(f"Processed news file is corrupt or invalid: {news_path}")
        return 1
    dashboard_items = _merge_items_for_validation(
        items,
        load_recent_processed_news(days=7, include_preview=True),
    )
    report_items = dashboard_items if dashboard_items else items
    errors = validate_processed_items(items)
    quality_counts = content_quality_counts(report_items)
    relevance_counts = relevance_score_counts(report_items)
    signal_counts = signal_score_distribution(report_items)
    summary_counts = summary_quality_counts(report_items)
    topic_counts = topic_distribution(report_items)
    trend_counts = trend_score_distribution(report_items)
    group_counts = source_group_counts(report_items)
    type_counts = signal_type_counts(report_items)
    top_signals = top_signal_items(report_items)
    top_trends = top_trending_items(report_items)
    useful_sources = top_sources_by_useful_signals(report_items)
    title_only_by_source = title_only_counts_by_source(report_items)
    source_stats = _load_source_stats()
    collection_report = _load_collection_report(date_folder())

    print(f"Total processed items: {len(items)}")
    print(f"Dashboard recent items: {len(dashboard_items)}")
    print(f"Items with full content: {quality_counts['full']}")
    print(f"Items with excerpt: {quality_counts['excerpt']}")
    print(f"Items title_only: {quality_counts['title_only']}")
    print(f"Invalid/skipped items: {quality_counts['invalid']}")
    print(f"Relevance score distribution: {relevance_counts}")
    print(f"Signal score distribution: {signal_counts}")
    print(f"Summary quality distribution: {summary_counts}")
    print(f"Topic distribution: {topic_counts}")
    print(f"Trend score distribution: {trend_counts}")
    print(f"Source group distribution: {group_counts}")
    print(f"Signal type distribution: {type_counts}")
    print(f"Top signals selected: {top_signals}")
    print(f"Top trending tools: {top_trends}")
    print(f"Excluded noise items: {excluded_noise_count(items)}")
    print(f"Top sources by useful signals: {useful_sources}")
    print(f"Title-only by source: {title_only_by_source}")
    print(f"Enabled sources: {source_stats['enabled']}")
    print(f"Disabled sources: {source_stats['disabled']}")
    print(f"Excluded wrong-domain items: {collection_report.get('excluded_wrong_domain_items', 0)}")
    print(f"Enabled ecosystem sources: {collection_report.get('ecosystem_enabled_sources_count', 0)}")
    print(f"Disabled ecosystem sources: {collection_report.get('ecosystem_disabled_sources_count', 0)}")
    print(f"Trend state: {collection_report.get('trend_state_path', 'data/trend_state.json')}")

    failed_sources = collection_report.get("failed_sources", [])
    print(f"Failed sources: {len(failed_sources)}")
    for error in failed_sources:
        print(f"- {error.get('source_id')}: {error.get('error')}")

    if errors:
        print(f"Validation failed: {len(errors)} item(s) with missing required fields.")
        for error in errors:
            fields = ", ".join(error["missing_fields"])
            print(f"- Item {error['index']}: missing {fields}")
        return 1

    print(f"Validation passed: {len(items)} current processed item(s) checked.")
    print(f"Validated file: {news_path}")
    return 0


def _load_source_stats() -> dict[str, int]:
    from src.storage.json_storage import load_sources_config

    sources = load_sources_config().get("sources", [])
    enabled = sum(1 for source in sources if source.get("enabled"))
    return {"enabled": enabled, "disabled": len(sources) - enabled}


def _load_collection_report(folder) -> dict[str, object]:
    report_path = folder / "collection_report.json"
    if report_path.exists():
        from src.storage.json_storage import load_json

        report = load_json(report_path, default={})
        if isinstance(report, dict):
            return report
    return {"failed_sources": []}


def _merge_items_for_validation(
    current_items: list[dict[str, object]],
    recent_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for item in recent_items:
        url = str(item.get("url", "")).strip()
        if url:
            merged[url] = item
    for item in current_items:
        url = str(item.get("url", "")).strip()
        if url:
            merged[url] = item
    return list(merged.values())


def print_pipeline_summary(result: dict[str, object]) -> None:
    """Print the final daily pipeline summary."""
    dashboard_path = result["dashboard_html_path"] or "disabled"
    print("Daily pipeline complete.")
    print(f"Collected items: {result['collected_count']}")
    print(f"Processed items: {result['processed_count']}")
    print(f"Dashboard items: {result.get('dashboard_items_count', result['processed_count'])}")
    print(f"Collection errors: {result['errors_count']}")
    print(f"Skipped old items: {result.get('skipped_old_items', 0)}")
    print(f"Skipped already-read items: {result.get('skipped_read_items', 0)}")
    print(f"Skipped unparseable-date items: {result.get('skipped_unparseable_date_items', 0)}")
    print(f"Excluded wrong-domain items: {result.get('excluded_wrong_domain_items', 0)}")
    print(f"Excluded processed items: {result.get('excluded_count', 0)}")
    print(f"Content quality distribution: {result.get('content_quality_distribution', {})}")
    print(f"Relevance score distribution: {result.get('relevance_score_distribution', {})}")
    print(f"Signal score distribution: {result.get('signal_score_distribution', {})}")
    print(f"Summary quality distribution: {result.get('summary_quality_distribution', {})}")
    print(f"Topic distribution: {result.get('topic_distribution', {})}")
    print(f"Trend score distribution: {result.get('trend_score_distribution', {})}")
    print(f"Source group distribution: {result.get('source_group_distribution', {})}")
    print(f"Signal type distribution: {result.get('signal_type_distribution', {})}")
    print(f"Top trending tools: {result.get('top_trending_items', [])}")
    print(f"Title-only by source: {result.get('title_only_by_source', {})}")
    if result.get("read_state_path"):
        print(f"Read state: {result['read_state_path']}")
    if result.get("trend_state_path"):
        print(f"Trend state: {result['trend_state_path']}")
    if "collection_report_path" in result:
        print(f"Collection report: {result['collection_report_path']}")
    print(f"Raw news: {result['raw_news_path']}")
    print(f"Processed news: {result['processed_news_path']}")
    print(f"Daily intelligence summary: {result.get('daily_intelligence_summary_path')}")
    print(f"Dashboard HTML: {dashboard_path}")
    print(f"Email HTML: {result['email_html_path']}")
    print(f"Email text: {result['email_text_path']}")
    print(f"Email sending: {result['email_sending']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-news-agent",
        description="Collect, process, and generate AI news intelligence outputs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_daily_parser = subparsers.add_parser(
        "run-daily",
        help="Run the daily AI news intelligence pipeline.",
    )
    run_daily_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned pipeline steps without performing work.",
    )

    collect_parser = subparsers.add_parser(
        "collect",
        help="Collect configured AI news sources.",
    )
    collect_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect sources without persisting results.",
    )

    subparsers.add_parser(
        "process",
        help="Process today's raw news into normalized news output.",
    )

    subparsers.add_parser(
        "validate",
        help="Validate today's processed news output.",
    )

    serve_parser = subparsers.add_parser(
        "serve",
        help="Serve the dashboard locally with an in-app 'Analizza' button.",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Local port to listen on (default 8000, or the next free one).",
    )

    subparsers.add_parser(
        "setup",
        help="Interactive first-run wizard: LLM provider, login, language, profile, sources.",
    )

    prune_parser = subparsers.add_parser(
        "prune",
        help="Delete dated data older than --keep-days and trim unbounded LLM logs.",
    )
    prune_parser.add_argument(
        "--keep-days",
        type=int,
        required=True,
        help="Delete dated folders and log entries older than this many days.",
    )
    prune_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without deleting anything.",
    )

    return parser


def main() -> None:
    from src.logging_setup import configure_logging

    configure_logging()
    args = build_parser().parse_args()

    if args.command == "run-daily":
        run_daily(dry_run=args.dry_run)
    elif args.command == "collect":
        collect_sources(dry_run=args.dry_run)
    elif args.command == "process":
        process_news()
    elif args.command == "serve":
        serve(port=args.port)
    elif args.command == "validate":
        raise SystemExit(validate_news())
    elif args.command == "setup":
        raise SystemExit(setup_wizard())
    elif args.command == "prune":
        prune(keep_days=args.keep_days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
