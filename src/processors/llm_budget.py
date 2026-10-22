"""Optional daily spend cap on LLM calls.

Handing this app to someone else means handing them unbounded LLM spend by
default: every "Approfondisci"/newsletter click, and every batch of new
items during a daily run, is a real subprocess call with no per-day quota.
This module makes a cap possible (``llm_daily_budget_usd`` in
``config/settings.yaml`` or ``data/settings_overrides.json``) without making
one mandatory — unset (the default), it never blocks anything.

The cap is read from ``data/llm_usage_log.json``, which every call site
already writes to via ``append_llm_usage_log`` (see
``src/storage/json_storage.py``), so no new tracking is introduced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.settings import llm_daily_budget_usd
from src.storage.json_storage import (
    DEFAULT_LLM_USAGE_LOG_PATH,
    load_llm_usage_log,
    today_utc,
)


def today_spend_usd(
    *, base_dir: str | Path = "data", today: str | None = None
) -> float:
    """Return the sum of ``total_cost_usd`` for usage-log records dated today.

    Estimated-cost records (``cost_estimated: true``, from providers that
    don't report a real $ figure) count the same as real ones -- the cap is
    meant to be a safety margin, not a precise invoice.
    """
    # today_utc(), not date.today(): every writer of these records stamps
    # the UTC date, so a local-date comparison would sum the wrong day
    # (and read 0.0, silently resetting the cap early) on any machine not
    # set to UTC.
    day = today or today_utc().isoformat()
    log_path = Path(base_dir) / DEFAULT_LLM_USAGE_LOG_PATH.name
    records = load_llm_usage_log(log_path)
    return sum(
        float(record.get("total_cost_usd") or 0.0)
        for record in records
        if str(record.get("date", "")) == day
    )


def is_budget_exceeded(*, base_dir: str | Path = "data", today: str | None = None) -> bool:
    """Return whether today's spend has reached the configured daily cap.

    Always False when no cap is configured.
    """
    budget = llm_daily_budget_usd()
    if budget is None:
        return False
    return today_spend_usd(base_dir=base_dir, today=today) >= budget


def budget_status(*, base_dir: str | Path = "data", today: str | None = None) -> dict[str, Any]:
    """Return a snapshot for the UI: cap, today's spend, and whether it's hit."""
    budget = llm_daily_budget_usd()
    spent = today_spend_usd(base_dir=base_dir, today=today)
    return {
        "budget_usd": budget,
        "spent_usd": round(spent, 4),
        "exceeded": budget is not None and spent >= budget,
    }
