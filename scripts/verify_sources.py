#!/usr/bin/env python3
"""Check that configured sources are reachable, parseable, and still active.

Feeds rot: domains move, RSS paths change, blogs go quiet. This script
exercises the *real* collector for each source's type (the same classes the
daily pipeline uses, so "verified" means "works in this app", not "works in
feedparser") and reports what came back.

A source passes when it fetches without error, yields at least one item, and
published something within `--max-age-days`. The freshness check reads the
items' own dates rather than the pipeline's lookback window, so a healthy but
slow-publishing blog is not mistaken for a dead one.

Usage:
    python scripts/verify_sources.py                  # every source
    python scripts/verify_sources.py --enabled-only   # just the active ones
    python scripts/verify_sources.py --json report.json

Exits non-zero if any *enabled* source fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collectors.base import SourceConfig  # noqa: E402
from src.collectors.collector_service import CollectorService, parse_published_date  # noqa: E402

DEFAULT_MAX_AGE_DAYS = 90
DEFAULT_WORKERS = 8

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"


def verify_source(
    service: CollectorService, source: SourceConfig, *, max_age_days: int, today: date
) -> dict[str, Any]:
    """Fetch one source with its real collector and classify the outcome."""
    result: dict[str, Any] = {
        "id": source.id,
        "name": source.name,
        "type": source.type,
        "category": source.category_hint,
        "enabled": source.enabled,
        "url": source.url,
        "items": 0,
        "latest": "",
        "age_days": None,
        "status": STATUS_ERROR,
        "error": "",
    }

    collector = service.collectors.get(source.type)
    if collector is None:
        result["error"] = f"no collector for type {source.type!r}"
        return result

    try:
        items = collector.collect(source)
    except Exception as exc:  # noqa: BLE001 - any failure is a failed source
        result["error"] = f"{type(exc).__name__}: {exc}"[:160]
        return result

    result["items"] = len(items)
    if not items:
        result["status"] = STATUS_EMPTY
        result["error"] = "fetched but returned no items"
        return result

    dates = [
        parsed
        for parsed in (parse_published_date(str(item.get("published_at", ""))) for item in items)
        if parsed is not None
    ]
    if not dates:
        # Some collectors (GitHub trending, HN) legitimately carry no dates;
        # having items at all is the strongest signal available for them.
        result["status"] = STATUS_OK
        result["latest"] = "n/d"
        return result

    latest = max(dates)
    result["latest"] = latest.isoformat()
    result["age_days"] = (today - latest).days
    result["status"] = STATUS_OK if result["age_days"] <= max_age_days else STATUS_STALE
    if result["status"] == STATUS_STALE:
        result["error"] = f"last item {result['age_days']} days old"
    return result


def run(
    *,
    enabled_only: bool,
    max_age_days: int,
    workers: int,
    only: list[str] | None,
    sources_path: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    service = CollectorService(sources_path=sources_path) if sources_path else CollectorService()
    sources = service.load_sources()
    if enabled_only:
        sources = [s for s in sources if s.enabled]
    if only:
        wanted = set(only)
        sources = [s for s in sources if s.id in wanted]

    today = service.today
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda s: verify_source(service, s, max_age_days=max_age_days, today=today),
                sources,
            )
        )
    results.sort(key=lambda r: (r["status"] == STATUS_OK, r["category"], r["id"]))

    failed_enabled = sum(1 for r in results if r["enabled"] and r["status"] != STATUS_OK)
    return results, failed_enabled


def print_report(results: list[dict[str, Any]], max_age_days: int) -> None:
    width = max((len(r["id"]) for r in results), default=10)
    mark = {STATUS_OK: "OK  ", STATUS_STALE: "OLD ", STATUS_EMPTY: "EMPTY", STATUS_ERROR: "FAIL"}

    for r in results:
        flag = "*" if r["enabled"] else " "
        line = (
            f" {flag}{mark.get(r['status'], '?'):<6}{r['id']:<{width}}  "
            f"items={r['items']:<4} latest={r['latest'] or '-':<12}"
        )
        if r["error"]:
            line += f"  {r['error'][:70]}"
        print(line)

    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    verified = by_status.get(STATUS_OK, 0)

    print()
    print(f"totale:    {len(results)}   (* = attiva)")
    print(f"verificate {verified}   ok=fetch+parse+item+pubblicazione entro {max_age_days} giorni")
    for status in (STATUS_STALE, STATUS_EMPTY, STATUS_ERROR):
        if by_status.get(status):
            print(f"{status:<10} {by_status[status]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify configured news sources.")
    parser.add_argument("--enabled-only", action="store_true", help="Check only enabled sources.")
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"Consider a source stale past this age (default {DEFAULT_MAX_AGE_DAYS}).",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Parallel fetches.")
    parser.add_argument("--only", nargs="*", help="Check only these source ids.")
    parser.add_argument("--json", dest="json_path", help="Also write the full report as JSON.")
    parser.add_argument(
        "--sources",
        dest="sources_path",
        help="Verify a different catalogue file (e.g. a list of candidates before adding them).",
    )
    args = parser.parse_args()

    results, failed_enabled = run(
        enabled_only=args.enabled_only,
        max_age_days=args.max_age_days,
        workers=args.workers,
        only=args.only,
        sources_path=args.sources_path,
    )
    print_report(results, args.max_age_days)

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nreport JSON: {args.json_path}")

    if failed_enabled:
        print(f"\n{failed_enabled} fonte/i ATTIVA/E non verificata/e.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
