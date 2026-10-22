"""Retention: bound the otherwise-unbounded growth of local data.

Every collection run adds a new ``data/YYYY-MM-DD/`` folder, and several
state files (``llm_usage_log.json``, ``llm_insights.json``) grow one entry
per run/item forever. Nothing here runs automatically -- it's a CLI command
(``python -m src.main prune``) the user chooses to run.

Deliberately conservative about what gets pruned: only data with an
unambiguous per-entry date. ``read_urls.json`` (a flat set with no per-URL
timestamp), ``saved_articles.json``, ``feedback.json``, and
``preferences.json`` (explicit user data / learned state) are never touched
here -- there's no safe way to bound the first without risking a dedup
regression, and the others are precisely the kind of user data that must
never be silently deleted.
"""

from __future__ import annotations

import re
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.storage.json_storage import (
    DEFAULT_DATA_DIR,
    DEFAULT_LLM_INSIGHTS_PATH,
    DEFAULT_LLM_USAGE_LOG_PATH,
    STORAGE_LOCK,
    load_json,
    save_json,
)

_DATE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def find_old_date_folders(
    *, base_dir: str | Path = DEFAULT_DATA_DIR, keep_days: int, today: date | None = None
) -> list[Path]:
    """Return dated data folders older than ``keep_days``, oldest first."""
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    base = Path(base_dir)
    if not base.is_dir():
        return []
    old = []
    for entry in base.iterdir():
        if not entry.is_dir() or not _DATE_FOLDER_RE.match(entry.name):
            continue
        try:
            folder_date = date.fromisoformat(entry.name)
        except ValueError:
            continue
        if folder_date < cutoff:
            old.append(entry)
    return sorted(old)


def prune_date_folders(
    *, base_dir: str | Path = DEFAULT_DATA_DIR, keep_days: int, today: date | None = None
) -> list[Path]:
    """Delete dated data folders older than ``keep_days``. Returns what was removed."""
    old = find_old_date_folders(base_dir=base_dir, keep_days=keep_days, today=today)
    for folder in old:
        shutil.rmtree(folder, ignore_errors=True)
    return old


def prune_llm_usage_log(
    *, path: str | Path = DEFAULT_LLM_USAGE_LOG_PATH, keep_days: int, today: date | None = None
) -> int:
    """Drop usage-log records older than ``keep_days``. Returns how many were dropped."""
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    with STORAGE_LOCK:
        records = load_json(path, default={})
        runs = records.get("runs", []) if isinstance(records, dict) else []
        kept, dropped = _partition_by_date(runs, cutoff)
        if dropped:
            save_json(path, {"runs": kept})
        return dropped


def prune_llm_insights(
    *, path: str | Path = DEFAULT_LLM_INSIGHTS_PATH, keep_days: int, today: date | None = None
) -> int:
    """Drop cached insights whose ``generated_at`` is older than ``keep_days``."""
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    with STORAGE_LOCK:
        payload = load_json(path, default={})
        insights = payload.get("insights", {}) if isinstance(payload, dict) else {}
        if not isinstance(insights, dict):
            return 0
        kept: dict[str, Any] = {}
        dropped = 0
        for url, entry in insights.items():
            entry_date = _parse_entry_date(entry.get("generated_at") if isinstance(entry, dict) else None)
            if entry_date is not None and entry_date < cutoff:
                dropped += 1
                continue
            kept[url] = entry
        if dropped:
            save_json(path, {"insights": kept})
        return dropped


def _partition_by_date(records: list[dict[str, Any]], cutoff: date) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    dropped = 0
    for record in records:
        record_date = _parse_entry_date(record.get("date") if isinstance(record, dict) else None)
        if record_date is not None and record_date < cutoff:
            dropped += 1
            continue
        kept.append(record)
    return kept, dropped


def _parse_entry_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
