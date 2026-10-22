"""Processor orchestration service."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date as date_type
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.processors.claude_cli_analyzer import (
    ClaudeCliAnalyzer,
    INSIGHT_FIELDS,
    filter_items_for_analysis,
    is_available,
)
from src.processors.llm_budget import is_budget_exceeded
from src.processors.profile import load_profile, personal_relevance, profile_context
from src.settings import output_language
from src.processors.rule_based_processor import RuleBasedProcessor
from src.storage.json_storage import (
    append_llm_usage_log,
    load_json,
    load_llm_insights,
    merge_llm_insights,
    processed_news_path,
    preview_processed_news_path,
    raw_news_path,
    save_json,
    save_processed_news,
    today_utc,
)

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
# The analysis window follows the collection window (``lookback_days``) but
# never exceeds a week: after a long pause, one run must not spend on a month
# of news at once.
MAX_ANALYSIS_DAYS = 7


class MissingRawNewsError(FileNotFoundError):
    """Raised when the expected raw news input file is missing."""


class ProcessorService:
    """Load raw collected items, process them, and save processed news."""

    def __init__(
        self,
        processor: RuleBasedProcessor | None = None,
        base_dir: str | Path = "data",
        date: date_type | None = None,
        llm_enabled: bool | None = None,
        analyzer: ClaudeCliAnalyzer | None = None,
        settings_path: str | Path = DEFAULT_SETTINGS_PATH,
        profile: dict[str, Any] | None = None,
        language: str | None = None,
    ) -> None:
        self.processor = processor or RuleBasedProcessor()
        self.base_dir = base_dir
        self.date = date
        self.settings_path = Path(settings_path)
        self.llm_enabled = (
            llm_enabled if llm_enabled is not None else self._llm_enabled_from_settings()
        )
        self.analyzer = analyzer
        self.profile = profile if profile is not None else load_profile()
        self.language = language if language is not None else output_language()

    def process(
        self,
        raw_items: list[dict[str, Any]] | None = None,
        preview: bool = False,
    ) -> dict[str, Any]:
        """Process raw news for the configured date."""
        input_path = raw_news_path(base_dir=self.base_dir, date=self.date)
        if raw_items is None and not input_path.exists():
            raise MissingRawNewsError(f"Raw news file not found: {input_path}")

        if raw_items is None:
            raw_items = load_json(input_path, default=[])
            if not isinstance(raw_items, list):
                raise MissingRawNewsError(f"Raw news file is corrupt or invalid: {input_path}")

        processed_items = [self.processor.process_item(item) for item in raw_items]
        _boost_repeated_mentions(processed_items)
        self._apply_llm_insights(raw_items, processed_items)
        self._apply_personal_relevance(processed_items)
        if preview:
            output_path = save_json(
                preview_processed_news_path(base_dir=self.base_dir, date=self.date),
                processed_items,
            )
        else:
            output_path = save_processed_news(
                processed_items,
                base_dir=self.base_dir,
                date=self.date,
            )

        return {
            "input_path": input_path,
            "output_path": output_path,
            "raw_count": len(raw_items),
            "processed_count": len(processed_items),
            "processed_items": processed_items,
        }

    def _load_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {}
        try:
            with self.settings_path.open("r", encoding="utf-8") as file_handle:
                settings = yaml.safe_load(file_handle) or {}
        except (OSError, yaml.YAMLError):
            return {}
        return settings if isinstance(settings, dict) else {}

    def _llm_enabled_from_settings(self) -> bool:
        return bool(self._load_settings().get("llm_analysis_enabled", False))

    def _analysis_window_days(self) -> int:
        """Days of news the batch analysis covers: ``lookback_days``, capped at 7."""
        try:
            days = int(self._load_settings().get("lookback_days", MAX_ANALYSIS_DAYS))
        except (TypeError, ValueError):
            days = MAX_ANALYSIS_DAYS
        return max(1, min(MAX_ANALYSIS_DAYS, days))

    def _apply_llm_insights(
        self,
        raw_items: list[dict[str, Any]],
        processed_items: list[dict[str, Any]],
    ) -> None:
        """Overlay Claude-generated insights onto processed items.

        Every item not already cached and published within the analysis
        window (``lookback_days``, at most 7) is sent to the LLM, so a run
        after a pause covers everything new since the last run; cached items
        reuse the on-disk cache. Any failure leaves the
        deterministic rule-based insights untouched.
        """
        if not self.llm_enabled:
            return
        if self.analyzer is None and not is_available():
            logger.info("Claude CLI not found; keeping rule-based insights.")
            return
        if self.analyzer is None and is_budget_exceeded(base_dir=self.base_dir):
            logger.info("Daily LLM budget reached; keeping rule-based insights.")
            return

        cache_path = Path(self.base_dir) / "llm_insights.json"
        cached_all = load_llm_insights(cache_path)
        # Cached prose is language-specific. Older records deliberately lack
        # this marker and are not reused after a user changes content language.
        cached = {
            url: insight
            for url, insight in cached_all.items()
            if str(insight.get("language", "")) == self.language
        }
        to_analyze = filter_items_for_analysis(
            raw_items,
            set(cached.keys()),
            max_age_hours=self._analysis_window_days() * 24,
        )
        if to_analyze:
            analyzer = self.analyzer or ClaudeCliAnalyzer(
                goal=profile_context(self.profile), language=self.language
            )
            new_insights = analyzer.analyze_items(to_analyze)
            if new_insights:
                for insight in new_insights.values():
                    insight["language"] = self.language
                merged = merge_llm_insights(new_insights, cache_path)
                cached = {
                    url: insight
                    for url, insight in merged.items()
                    if str(insight.get("language", "")) == self.language
                }
            logger.info("Generated %d new LLM insight(s).", len(new_insights))
            usage = getattr(analyzer, "usage_summary", lambda: None)()
            if usage and usage.get("calls"):
                logger.info(
                    "LLM usage this run: %d call(s), cost %s, %d in / %d out tokens",
                    usage["calls"], (
                        f"${usage['total_cost_usd']:.4f}"
                        if usage.get("total_cost_usd") is not None else "not available"
                    ),
                    usage["input_tokens"], usage["output_tokens"],
                )
                run_date = self.date or today_utc()
                append_llm_usage_log(
                    {
                        "date": run_date.isoformat(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "items_analyzed": len(to_analyze),
                        "kind": "insights",
                        **usage,
                    },
                    Path(self.base_dir) / "llm_usage_log.json",
                )

        applied = 0
        for item in processed_items:
            url = str(item.get("url", "")).strip()
            insight = cached.get(url)
            if not insight:
                continue
            for field in INSIGHT_FIELDS:
                value = str(insight.get(field, "")).strip()
                if value:
                    item[field] = value
            relevance_to_goal = insight.get("relevance_to_goal")
            if isinstance(relevance_to_goal, int):
                item["relevance_to_goal"] = max(1, min(5, relevance_to_goal))
            item["insight_source"] = "claude_cli"
            applied += 1
        if applied:
            logger.info("Applied LLM insights to %d item(s).", applied)

    def _apply_personal_relevance(
        self, processed_items: list[dict[str, Any]]
    ) -> None:
        """Add personal_relevance_score and personal_match from the profile.

        Learned feedback preferences, when present, nudge the score slightly.
        """
        from src.storage.json_storage import load_preferences

        preferences = load_preferences(Path(self.base_dir) / "preferences.json")
        for item in processed_items:
            score, matched = personal_relevance(item, self.profile, preferences)
            score += _relevance_to_goal_boost(item)
            item["personal_relevance_score"] = max(0, min(100, score))
            item["personal_match"] = matched


def _relevance_to_goal_boost(item: dict[str, Any]) -> int:
    """Turn the LLM's relevance_to_goal (1-5) into a personal-score delta.

    Centered on 3 (neutral -> 0) so a brand-new user who has only written
    ``about_me`` in their profile — with no keyword lists yet — still gets a
    populated "Per te" section (sections.py filters on personal_score > 0)
    as soon as the LLM has analyzed a batch, instead of waiting for
    keyword-boost matches that don't exist yet. Guarded to real LLM insights
    only, so a stale rule-based item never gets a phantom boost.

    The literal marker value ``"claude_cli"`` predates the multi-provider
    support in llm_provider.py and is not provider-specific: it's set the
    same way regardless of which CLI actually ran (claude, codex, ...), so
    the check below is correct for every provider as-is.
    """
    if item.get("insight_source") != "claude_cli":
        return 0
    relevance_to_goal = item.get("relevance_to_goal")
    if not isinstance(relevance_to_goal, int):
        return 0
    return (relevance_to_goal - 3) * 5


def _boost_repeated_mentions(items: list[dict[str, Any]]) -> None:
    subjects = [
        _trend_subject(item)
        for item in items
        if int(item.get("trend_score", 0) or 0) > 0
    ]
    counts = Counter(subject for subject in subjects if subject)
    for item in items:
        subject = _trend_subject(item)
        repeated_mentions = max(0, counts.get(subject, 0) - 1)
        if repeated_mentions:
            item["trend_score"] = min(
                100,
                int(item.get("trend_score", 0) or 0) + min(20, repeated_mentions * 10),
            )
            item["repeated_mentions"] = repeated_mentions


def _trend_subject(item: dict[str, Any]) -> str:
    subject = str(item.get("trend_subject", "")).strip().lower()
    if subject:
        return subject
    title = str(item.get("title", "")).strip().lower()
    return " ".join(title.split()[:4])
