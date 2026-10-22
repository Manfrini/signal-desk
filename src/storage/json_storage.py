"""JSON storage helpers for local news data."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import date as date_type
from datetime import timedelta
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Serializes every read-modify-write against the JSON data files. The server
# is a ThreadingHTTPServer with background job threads (deep dives, newsletter
# generation, the daily pipeline), and every one of these files is loaded,
# mutated in Python, and saved back as a whole — without this lock, two
# concurrent writers can race and one update silently overwrites the other.
# Re-entrant so a locked call can call another locked helper.
STORAGE_LOCK = threading.RLock()

DEFAULT_DATA_DIR = Path("data")
RAW_NEWS_FILENAME = "raw_news.json"
PROCESSED_NEWS_FILENAME = "news.json"
DAILY_INTELLIGENCE_SUMMARY_FILENAME = "daily_intelligence_summary.json"
PREVIEW_RAW_NEWS_FILENAME = "preview_raw_news.json"
PREVIEW_PROCESSED_NEWS_FILENAME = "preview_news.json"
PREVIEW_DAILY_INTELLIGENCE_SUMMARY_FILENAME = "preview_daily_intelligence_summary.json"
SECTION_BRIEFS_FILENAME = "section_briefs.json"
PREVIEW_SECTION_BRIEFS_FILENAME = "preview_section_briefs.json"
DEFAULT_READ_STATE_PATH = Path("data/read_urls.json")
DEFAULT_TREND_STATE_PATH = Path("data/trend_state.json")
DEFAULT_LLM_INSIGHTS_PATH = Path("data/llm_insights.json")
DEFAULT_LLM_USAGE_LOG_PATH = Path("data/llm_usage_log.json")
DEFAULT_DEEP_DIVES_PATH = Path("data/deep_dives.json")
DEFAULT_VIDEO_TRANSCRIPTS_PATH = Path("data/video_transcripts.json")
DEFAULT_TRANSCRIPT_STATUS_PATH = Path("data/transcript_status.json")
DEFAULT_SAVED_PATH = Path("data/saved_articles.json")
DEFAULT_NEWSLETTERS_DIR = Path("data/newsletters")
DEFAULT_FEEDBACK_PATH = Path("data/feedback.json")
DEFAULT_PREFERENCES_PATH = Path("data/preferences.json")
DEFAULT_SOURCES_PATH = Path("data/sources.json")
DEFAULT_LEGACY_SOURCES_YAML_PATH = Path("config/sources.yaml")
DEFAULT_PROFILE_PATH = Path("data/profile.json")
DEFAULT_LEGACY_PROFILE_YAML_PATH = Path("config/profile.yaml")


def load_json(path: str | Path, default: Any = None) -> Any:
    """Load a UTF-8 JSON file.

    Returns ``default`` (without raising) if the file is missing, unreadable,
    or contains invalid JSON — e.g. left truncated by a crash mid-write. A
    corrupt file is quarantined (renamed with a ``.corrupt-<timestamp>``
    suffix) so it doesn't keep breaking every future read, and the problem is
    logged at ERROR so it isn't missed.
    """
    target_path = Path(path)
    try:
        with target_path.open("r", encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.error("Corrupt or unreadable JSON file %s: %s", target_path, exc)
        _quarantine_corrupt_file(target_path)
        return default


def _quarantine_corrupt_file(path: Path) -> None:
    """Rename a corrupt file out of the way so it stops breaking future reads."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantined = path.with_name(f"{path.name}.corrupt-{timestamp}")
        path.rename(quarantined)
        logger.error("Quarantined corrupt file: %s -> %s", path, quarantined)
    except OSError as exc:
        logger.error("Failed to quarantine corrupt file %s: %s", path, exc)


def save_json(path: str | Path, payload: Any) -> Path:
    """Save payload as pretty-printed UTF-8 JSON, atomically.

    Writes to a temp file in the same directory, flushes and fsyncs it, then
    atomically replaces the target with ``os.replace``. This means a crash or
    kill mid-write can never leave a truncated/corrupt file at ``path`` — the
    old contents (or nothing, if this is the first write) survive intact.
    """
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=target_path.parent, prefix=f".{target_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, ensure_ascii=False, indent=2)
            file_handle.write("\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(tmp_name, target_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    logger.info("Saved JSON file: %s", target_path)
    return target_path


def load_read_urls(path: str | Path = DEFAULT_READ_STATE_PATH) -> set[str]:
    """Load URLs already collected in previous non-dry-run executions."""
    state_path = Path(path)
    if not state_path.exists():
        return set()

    payload = load_json(state_path)
    if isinstance(payload, list):
        return {str(url).strip() for url in payload if str(url).strip()}
    if isinstance(payload, dict):
        urls = payload.get("urls", [])
        return {str(url).strip() for url in urls if str(url).strip()}
    return set()


def save_read_urls(urls: set[str], path: str | Path = DEFAULT_READ_STATE_PATH) -> Path:
    """Save read URL state as stable pretty JSON."""
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "urls": sorted(urls),
    }
    return save_json(path, payload)


def mark_read_urls(
    items: list[dict[str, Any]],
    path: str | Path = DEFAULT_READ_STATE_PATH,
) -> Path:
    """Add collected item URLs to the read-state file."""
    with STORAGE_LOCK:
        urls = load_read_urls(path)
        urls.update(str(item.get("url", "")).strip() for item in items if item.get("url"))
        return save_read_urls(urls, path)


def load_trend_state(path: str | Path = DEFAULT_TREND_STATE_PATH) -> dict[str, Any]:
    """Load local trend state used for star-growth comparisons."""
    state_path = Path(path)
    if not state_path.exists():
        return {"repositories": {}, "updated_at": ""}
    payload = load_json(state_path)
    return payload if isinstance(payload, dict) else {"repositories": {}, "updated_at": ""}


def save_trend_state(state: dict[str, Any], path: str | Path = DEFAULT_TREND_STATE_PATH) -> Path:
    """Save local trend state as stable pretty JSON."""
    payload = {
        **state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return save_json(path, payload)


def update_trend_state_from_items(
    items: list[dict[str, Any]],
    path: str | Path = DEFAULT_TREND_STATE_PATH,
) -> Path:
    """Persist repository trend snapshots from collected items."""
    with STORAGE_LOCK:
        state = load_trend_state(path)
        repositories = dict(state.get("repositories", {}))
        for item in items:
            repo = str(item.get("trend_subject", "")).strip()
            if not repo:
                continue
            stars = int(item.get("github_stars") or 0)
            repositories[repo] = {
                "stars": stars,
                "url": item.get("url", ""),
                "source_id": item.get("source_id", ""),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
        return save_trend_state({"repositories": repositories}, path)


def load_llm_insights(
    path: str | Path = DEFAULT_LLM_INSIGHTS_PATH,
) -> dict[str, dict[str, Any]]:
    """Load the cached per-URL LLM insights from previous runs."""
    state_path = Path(path)
    if not state_path.exists():
        return {}
    payload = load_json(state_path)
    if isinstance(payload, dict):
        insights = payload.get("insights", {})
        if isinstance(insights, dict):
            return {
                str(url): value
                for url, value in insights.items()
                if isinstance(value, dict)
            }
    return {}


def save_llm_insights(
    insights: dict[str, dict[str, Any]],
    path: str | Path = DEFAULT_LLM_INSIGHTS_PATH,
) -> Path:
    """Persist the per-URL LLM insights cache as stable pretty JSON."""
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "insights": insights,
    }
    return save_json(path, payload)


def merge_llm_insights(
    new_insights: dict[str, dict[str, Any]],
    path: str | Path = DEFAULT_LLM_INSIGHTS_PATH,
) -> dict[str, dict[str, Any]]:
    """Merge freshly generated insights into the cache and persist it.

    Returns the full merged mapping so callers can apply it in-memory
    without re-reading the file.
    """
    with STORAGE_LOCK:
        cached = load_llm_insights(path)
        cached.update(new_insights)
        save_llm_insights(cached, path)
        return cached


def load_saved_articles(
    path: str | Path = DEFAULT_SAVED_PATH,
) -> dict[str, dict[str, Any]]:
    """Load saved-article snapshots keyed by URL (persist across runs)."""
    state_path = Path(path)
    if not state_path.exists():
        return {}
    payload = load_json(state_path)
    if isinstance(payload, dict):
        articles = payload.get("articles", {})
        if isinstance(articles, dict):
            return {str(url): v for url, v in articles.items() if isinstance(v, dict)}
    return {}


def add_saved_article(
    item: dict[str, Any],
    path: str | Path = DEFAULT_SAVED_PATH,
) -> dict[str, dict[str, Any]]:
    """Persist a full item snapshot so it survives the 7-day window."""
    url = str(item.get("url", "")).strip()
    with STORAGE_LOCK:
        articles = load_saved_articles(path)
        if url:
            snapshot = dict(item)
            snapshot["saved_at"] = datetime.now(timezone.utc).isoformat()
            articles[url] = snapshot
            save_json(path, {"updated_at": datetime.now(timezone.utc).isoformat(), "articles": articles})
        return articles


def remove_saved_article(
    url: str,
    path: str | Path = DEFAULT_SAVED_PATH,
) -> dict[str, dict[str, Any]]:
    """Remove one saved article."""
    with STORAGE_LOCK:
        articles = load_saved_articles(path)
        if url in articles:
            del articles[url]
            save_json(path, {"updated_at": datetime.now(timezone.utc).isoformat(), "articles": articles})
        return articles


def load_feedback(path: str | Path = DEFAULT_FEEDBACK_PATH) -> dict[str, dict[str, Any]]:
    """Load per-URL feedback (votes and deep-dive flags)."""
    state_path = Path(path)
    if not state_path.exists():
        return {}
    payload = load_json(state_path)
    if isinstance(payload, dict):
        entries = payload.get("feedback", {})
        if isinstance(entries, dict):
            return {str(url): v for url, v in entries.items() if isinstance(v, dict)}
    return {}


def save_feedback(
    feedback: dict[str, dict[str, Any]], path: str | Path = DEFAULT_FEEDBACK_PATH
) -> Path:
    """Persist per-URL feedback."""
    return save_json(
        path,
        {"updated_at": datetime.now(timezone.utc).isoformat(), "feedback": feedback},
    )


def load_preferences(path: str | Path = DEFAULT_PREFERENCES_PATH) -> dict[str, Any]:
    """Load learned weights (topics/keywords/sources)."""
    state_path = Path(path)
    if not state_path.exists():
        return {"topics": {}, "keywords": {}, "sources": {}, "vote_count": 0}
    payload = load_json(state_path)
    if isinstance(payload, dict):
        return payload
    return {"topics": {}, "keywords": {}, "sources": {}, "vote_count": 0}


def save_preferences(
    preferences: dict[str, Any], path: str | Path = DEFAULT_PREFERENCES_PATH
) -> Path:
    """Persist learned weights."""
    payload = dict(preferences)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    return save_json(path, payload)


def newsletter_slug(date_from: str, date_to: str) -> str:
    """Return the file stem for one newsletter edition.

    Defense in depth against path traversal: the caller (server.py) validates
    these as real ISO dates before ever reaching here, but this function
    strips anything outside `[0-9-]` too, so it can never produce a slug that
    escapes the newsletters directory even if called with unvalidated input.
    """
    safe_from = re.sub(r"[^0-9-]", "", str(date_from).strip())
    safe_to = re.sub(r"[^0-9-]", "", str(date_to).strip())
    return f"{safe_from}_{safe_to}"


def save_newsletter(
    newsletter: dict[str, Any],
    directory: str | Path = DEFAULT_NEWSLETTERS_DIR,
) -> Path:
    """Persist one newsletter edition (one file per timeframe)."""
    slug = newsletter_slug(newsletter.get("date_from", ""), newsletter.get("date_to", ""))
    return save_json(Path(directory) / f"{slug}.json", newsletter)


def load_newsletters(
    directory: str | Path = DEFAULT_NEWSLETTERS_DIR,
) -> dict[str, dict[str, Any]]:
    """Load all saved newsletter editions keyed by slug."""
    folder = Path(directory)
    if not folder.exists():
        return {}
    editions: dict[str, dict[str, Any]] = {}
    for path in folder.glob("*.json"):
        payload = load_json(path)
        if isinstance(payload, dict):
            editions[path.stem] = payload
    return editions


def load_deep_dives(
    path: str | Path = DEFAULT_DEEP_DIVES_PATH,
) -> dict[str, dict[str, Any]]:
    """Load cached per-URL deep dives."""
    state_path = Path(path)
    if not state_path.exists():
        return {}
    payload = load_json(state_path)
    if isinstance(payload, dict):
        dives = payload.get("dives", {})
        if isinstance(dives, dict):
            return {str(url): v for url, v in dives.items() if isinstance(v, dict)}
    return {}


def save_deep_dive(
    url: str,
    dive: dict[str, Any],
    path: str | Path = DEFAULT_DEEP_DIVES_PATH,
) -> dict[str, dict[str, Any]]:
    """Add/replace one deep dive in the cache and persist it."""
    with STORAGE_LOCK:
        dives = load_deep_dives(path)
        dives[url] = dive
        save_json(path, {"updated_at": datetime.now(timezone.utc).isoformat(), "dives": dives})
        return dives


def load_video_transcripts(
    path: str | Path = DEFAULT_VIDEO_TRANSCRIPTS_PATH,
) -> dict[str, dict[str, Any]]:
    """Load cached per-video-id transcript fetch outcomes."""
    state_path = Path(path)
    if not state_path.exists():
        return {}
    payload = load_json(state_path)
    if isinstance(payload, dict):
        transcripts = payload.get("transcripts", {})
        if isinstance(transcripts, dict):
            return {str(video_id): v for video_id, v in transcripts.items() if isinstance(v, dict)}
    return {}


def save_video_transcript(
    video_id: str,
    entry: dict[str, Any],
    path: str | Path = DEFAULT_VIDEO_TRANSCRIPTS_PATH,
) -> dict[str, dict[str, Any]]:
    """Add/replace one video's cached transcript outcome and persist it."""
    with STORAGE_LOCK:
        transcripts = load_video_transcripts(path)
        transcripts[video_id] = entry
        save_json(
            path,
            {"updated_at": datetime.now(timezone.utc).isoformat(), "transcripts": transcripts},
        )
        return transcripts


def load_transcript_status(
    path: str | Path = DEFAULT_TRANSCRIPT_STATUS_PATH,
) -> dict[str, Any]:
    """Load the last YouTube block (``blocked_at``/``resume_at``), or ``{}``."""
    state_path = Path(path)
    if not state_path.exists():
        return {}
    payload = load_json(state_path)
    return payload if isinstance(payload, dict) else {}


def save_transcript_status(
    status: dict[str, Any],
    path: str | Path = DEFAULT_TRANSCRIPT_STATUS_PATH,
) -> None:
    """Persist the YouTube block state so the dashboard can show it."""
    with STORAGE_LOCK:
        save_json(path, status)


def load_llm_usage_log(
    path: str | Path = DEFAULT_LLM_USAGE_LOG_PATH,
) -> list[dict[str, Any]]:
    """Load the historical per-run LLM usage records."""
    log_path = Path(path)
    if not log_path.exists():
        return []
    payload = load_json(log_path)
    if isinstance(payload, dict):
        runs = payload.get("runs", [])
        return [r for r in runs if isinstance(r, dict)]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def append_llm_usage_log(
    record: dict[str, Any],
    path: str | Path = DEFAULT_LLM_USAGE_LOG_PATH,
) -> Path:
    """Append one run's usage record to the persistent history."""
    with STORAGE_LOCK:
        runs = load_llm_usage_log(path)
        runs.append(record)
        return save_json(path, {"runs": runs})


def today_utc() -> date_type:
    """Return today's date in UTC.

    Every event timestamp this app writes is UTC (``datetime.now(timezone.
    utc)``), but the day-folder name used to default to the LOCAL date
    (``date.today()``). On a machine set to a timezone far from UTC, that
    mismatch could put a run's own data in a folder dated a day off from its
    own timestamps, or (right around local midnight) split one run's data
    across two folders. Every ``date or date_type.today()`` fallback in this
    codebase should use this instead.
    """
    return datetime.now(timezone.utc).date()


def date_folder(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the folder for a collection date."""
    selected_date = date or today_utc()
    return Path(base_dir) / selected_date.isoformat()


def raw_news_path(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the path for raw collected news."""
    return date_folder(base_dir=base_dir, date=date) / RAW_NEWS_FILENAME


def processed_news_path(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the path for processed news."""
    return date_folder(base_dir=base_dir, date=date) / PROCESSED_NEWS_FILENAME


def daily_intelligence_summary_path(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the path for the daily intelligence summary."""
    return date_folder(base_dir=base_dir, date=date) / DAILY_INTELLIGENCE_SUMMARY_FILENAME


def preview_raw_news_path(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the path for preview raw collected news."""
    return date_folder(base_dir=base_dir, date=date) / PREVIEW_RAW_NEWS_FILENAME


def preview_processed_news_path(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the path for preview processed news."""
    return date_folder(base_dir=base_dir, date=date) / PREVIEW_PROCESSED_NEWS_FILENAME


def preview_daily_intelligence_summary_path(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the path for the preview daily intelligence summary."""
    return date_folder(base_dir=base_dir, date=date) / PREVIEW_DAILY_INTELLIGENCE_SUMMARY_FILENAME


def save_raw_news(
    items: list[dict[str, Any]],
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Save raw collected news items."""
    return save_json(raw_news_path(base_dir=base_dir, date=date), items)


def save_preview_raw_news(
    items: list[dict[str, Any]],
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Save preview raw collected news items."""
    return save_json(preview_raw_news_path(base_dir=base_dir, date=date), items)


def save_processed_news(
    items: list[dict[str, Any]],
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Save processed news items."""
    return save_json(processed_news_path(base_dir=base_dir, date=date), items)


def save_preview_processed_news(
    items: list[dict[str, Any]],
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Save preview processed news items."""
    return save_json(preview_processed_news_path(base_dir=base_dir, date=date), items)


def section_briefs_path(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the path for per-section briefs."""
    return date_folder(base_dir=base_dir, date=date) / SECTION_BRIEFS_FILENAME


def preview_section_briefs_path(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Return the path for preview per-section briefs."""
    return date_folder(base_dir=base_dir, date=date) / PREVIEW_SECTION_BRIEFS_FILENAME


def save_section_briefs(
    briefs: dict[str, str],
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Save the generated per-section briefs."""
    return save_json(section_briefs_path(base_dir=base_dir, date=date), briefs)


def save_preview_section_briefs(
    briefs: dict[str, str],
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Save the preview per-section briefs."""
    return save_json(preview_section_briefs_path(base_dir=base_dir, date=date), briefs)


def save_daily_intelligence_summary(
    summary: dict[str, Any],
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Save the generated daily intelligence summary."""
    return save_json(daily_intelligence_summary_path(base_dir=base_dir, date=date), summary)


def save_preview_daily_intelligence_summary(
    summary: dict[str, Any],
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
) -> Path:
    """Save the preview daily intelligence summary."""
    return save_json(preview_daily_intelligence_summary_path(base_dir=base_dir, date=date), summary)


def load_all_collection_reports(
    base_dir: str | Path = DEFAULT_DATA_DIR,
) -> dict[str, dict[str, Any]]:
    """Return {date_str: collection_report} for every run on disk."""
    base = Path(base_dir)
    reports: dict[str, dict[str, Any]] = {}
    if not base.exists():
        return reports
    for folder in base.iterdir():
        report_path = folder / "collection_report.json"
        if not folder.is_dir() or not report_path.exists():
            continue
        payload = load_json(report_path)
        if isinstance(payload, dict):
            reports[folder.name] = payload
    return reports


def latest_news_by_source(
    base_dir: str | Path = DEFAULT_DATA_DIR,
) -> dict[str, str]:
    """Return {source_id: most recent publication date} across all runs.

    Computed once by scanning every `data/<date>/news.json`, so the Fonti page
    can show how fresh each source is without a request per row.
    """
    from src.processors.intelligence import _parse_date

    base = Path(base_dir)
    latest: dict[str, date_type] = {}
    if not base.exists():
        return {}
    for folder in base.iterdir():
        news_path = folder / "news.json"
        if not folder.is_dir() or not news_path.exists():
            continue
        payload = load_json(news_path)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id", "")).strip()
            if not source_id:
                continue
            # Dates come in mixed formats (ISO and RFC): parse before comparing.
            parsed = _parse_date(str(item.get("published_at") or "")) or _parse_date(
                str(item.get("collected_at") or "")
            )
            if parsed is None:
                continue
            if source_id not in latest or parsed > latest[source_id]:
                latest[source_id] = parsed
    return {source_id: day.isoformat() for source_id, day in latest.items()}


def load_news_by_source(
    source_id: str,
    base_dir: str | Path = DEFAULT_DATA_DIR,
) -> list[dict[str, Any]]:
    """Return every processed item ever collected from one source.

    Scans all `data/<date>/news.json` folders, deduplicates by URL and sorts
    newest first, so the Fonti page can show a source's history.
    """
    base = Path(base_dir)
    if not base.exists() or not str(source_id).strip():
        return []
    merged: dict[str, dict[str, Any]] = {}
    for folder in sorted(base.iterdir()):
        news_path = folder / "news.json"
        if not folder.is_dir() or not news_path.exists():
            continue
        payload = load_json(news_path)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("source_id", "")).strip() != str(source_id).strip():
                continue
            url = str(item.get("url", "")).strip()
            if url:
                merged[url] = item
    return sorted(
        merged.values(),
        key=lambda item: str(item.get("published_at") or item.get("collected_at") or ""),
        reverse=True,
    )


def load_recent_processed_news(
    base_dir: str | Path = DEFAULT_DATA_DIR,
    date: date_type | None = None,
    days: int = 7,
    include_preview: bool = False,
) -> list[dict[str, Any]]:
    """Load processed news published within the recent window.

    The run folders are walked to find the items, but an item is judged by
    its own ``published_at``: collection already keeps a lookback window of
    its own, so merging whole folders stacked the two and left articles on
    the page at nearly twice their intended age.

    An item with no usable date is kept. Nothing proves it is stale, and
    dropping it would silently lose the sources that publish without one.
    """
    selected_date = date or today_utc()
    oldest_allowed = selected_date - timedelta(days=days)
    items: list[dict[str, Any]] = []
    for offset in range(days):
        current_date = selected_date - timedelta(days=offset)
        paths = [processed_news_path(base_dir=base_dir, date=current_date)]
        if include_preview:
            paths.append(preview_processed_news_path(base_dir=base_dir, date=current_date))
        for path in paths:
            if not path.exists():
                continue
            payload = load_json(path)
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                if _published_before(item, oldest_allowed):
                    continue
                items.append(item)
    return items


def _published_before(item: dict[str, Any], limit: date_type) -> bool:
    """True when the item's date falls before ``limit``.

    Falls back to ``collected_at`` for items stored before the collector
    started stamping a date: the run that saw them bounds their publication
    date, and without the fallback they would never leave the window.
    """
    from src.processors.intelligence import _parse_date

    published = _parse_date(str(item.get("published_at", "")))
    if published is None:
        published = _parse_date(str(item.get("collected_at", "")))
    return published is not None and published < limit


def load_sources_config(
    path: str | Path = DEFAULT_SOURCES_PATH,
    legacy_yaml_path: str | Path = DEFAULT_LEGACY_SOURCES_YAML_PATH,
) -> dict[str, Any]:
    """Load the sources config (categories + sources) from JSON.

    On first use (no ``data/sources.json`` yet), migrates it once from the
    legacy ``config/sources.yaml`` if that file exists, persisting the result
    so every later call reads JSON only.
    """
    sources_path = Path(path)
    with STORAGE_LOCK:
        seed = _load_seed_sources(Path(legacy_yaml_path))
        # Only the app's own catalogue absorbs newly shipped sources. A
        # caller naming a different file (tests, a verification run against
        # a candidate list) wants exactly that file, not the shipped seed.
        is_default_catalogue = sources_path == Path(DEFAULT_SOURCES_PATH)

        if sources_path.exists():
            payload = load_json(sources_path)
            if not isinstance(payload, dict):
                return {"categories": [], "sources": []}
            if not is_default_catalogue:
                return payload
            return _merge_new_seed_sources(payload, seed, sources_path)

        if seed:
            migrated = {
                "categories": seed.get("categories", []),
                "sources": seed.get("sources", []),
            }
            save_json(sources_path, migrated)
            return migrated

        return {"categories": [], "sources": []}


def _load_seed_sources(legacy_path: Path) -> dict[str, Any]:
    if not legacy_path.exists():
        return {}
    import yaml

    try:
        with legacy_path.open("r", encoding="utf-8") as file_handle:
            payload = yaml.safe_load(file_handle) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("Could not read the source seed at %s", legacy_path)
        return {}
    return payload if isinstance(payload, dict) else {}


def _merge_new_seed_sources(
    current: dict[str, Any], seed: dict[str, Any], sources_path: Path
) -> dict[str, Any]:
    """Add sources the seed has gained since this install was set up.

    Without this, the one-shot YAML migration means an existing install is
    frozen at whatever the catalogue looked like on the day it was created:
    every source added upstream afterwards would be invisible, with no way
    to discover it.

    Strictly additive, by design:
    - existing entries are never touched, so hand edits and the user's own
      enabled/disabled choices always win;
    - newly seeded sources arrive **disabled**, so upgrading never silently
      starts collecting from feeds the user never chose. They show up in the
      Fonti page, ready to be switched on.
    """
    seed_sources = seed.get("sources") or []
    if not isinstance(seed_sources, list):
        return current

    existing = current.get("sources")
    if not isinstance(existing, list):
        return current

    known = {str(src.get("id", "")) for src in existing if isinstance(src, dict)}
    additions = [
        {**src, "enabled": False}
        for src in seed_sources
        if isinstance(src, dict) and str(src.get("id", "")) and str(src["id"]) not in known
    ]
    if not additions:
        return current

    current["sources"] = existing + additions
    if seed.get("categories") and not current.get("categories"):
        current["categories"] = seed["categories"]
    save_json(sources_path, current)
    logger.info("Added %d new source(s) from the seed catalogue (disabled).", len(additions))
    return current


def save_sources_config(
    doc: dict[str, Any],
    path: str | Path = DEFAULT_SOURCES_PATH,
) -> Path:
    """Persist the sources config (categories + sources) as JSON."""
    return save_json(path, doc)


# Fields carried over verbatim from the v1 (config/profile.yaml) schema.
_PROFILE_V1_FIELDS = ("goal", "interests", "projects", "skills_to_grow", "keywords_boost")


def _empty_profile_v2() -> dict[str, Any]:
    return {
        "version": 2,
        "about_me": "",
        "goal": "",
        "interests": [],
        "projects": [],
        "skills_to_grow": [],
        "keywords_boost": [],
        "derived": {"source": "", "generated_at": "", "provider": "", "from_about_me_hash": ""},
        "edited_fields": [],
    }


def _upgrade_profile_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Carry a legacy (YAML) profile's fields into the v2 JSON schema."""
    upgraded = _empty_profile_v2()
    for field in _PROFILE_V1_FIELDS:
        if field in payload:
            upgraded[field] = payload[field]
    upgraded["derived"]["source"] = "manual"
    return upgraded


def load_profile_config(
    path: str | Path = DEFAULT_PROFILE_PATH,
    legacy_yaml_path: str | Path = DEFAULT_LEGACY_PROFILE_YAML_PATH,
) -> dict[str, Any]:
    """Load the user profile from JSON.

    On first use (no ``data/profile.json`` yet), migrates it once from the
    legacy ``config/profile.yaml`` if that file exists, persisting the
    upgraded v2 document so every later call reads JSON only. A brand new
    checkout with neither file returns an inert empty (v2) profile.
    """
    profile_path = Path(path)
    with STORAGE_LOCK:
        if profile_path.exists():
            payload = load_json(profile_path)
            if isinstance(payload, dict) and payload:
                return payload
            return _empty_profile_v2()

        legacy_path = Path(legacy_yaml_path)
        if legacy_path.exists():
            import yaml

            try:
                with legacy_path.open("r", encoding="utf-8") as file_handle:
                    payload = yaml.safe_load(file_handle) or {}
            except (OSError, yaml.YAMLError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            migrated = _upgrade_profile_v1_to_v2(payload)
            save_json(profile_path, migrated)
            return migrated

        return _empty_profile_v2()


def save_profile_config(doc: dict[str, Any], path: str | Path = DEFAULT_PROFILE_PATH) -> Path:
    """Persist the user profile as JSON."""
    with STORAGE_LOCK:
        return save_json(path, doc)
