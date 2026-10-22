"""Single seam for user-overridable preferences (LLM provider/model and
application/content languages).

``config/settings.yaml`` is git-tracked and hand-commented in Italian, so it
must never be rewritten by code. User choices instead go to
``data/settings_overrides.json`` (gitignored, same as the rest of ``data/``),
merged on top of the YAML at read time. The existing ``llm_analysis_enabled``
flag is intentionally left alone here: it already has two independent,
tested readers (``src/server.py:_llm_enabled``, and
``ProcessorService._llm_enabled_from_settings``, which accepts an explicit
``settings_path`` for tests) and refactoring a working, tested path buys
nothing for handover-readiness. This module only owns settings that are new.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.processors.llm_provider import DEFAULT_PROVIDER_ID, PROVIDERS
from src.storage.json_storage import STORAGE_LOCK, load_json, save_json

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_OVERRIDES_PATH = Path("data/settings_overrides.json")

SUPPORTED_LANGUAGES = ("it", "en")
DEFAULT_LANGUAGE = "it"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_overrides(path: Path) -> dict[str, Any]:
    data = load_json(path, default={})
    return data if isinstance(data, dict) else {}


def load_settings(
    *,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    overrides_path: Path = DEFAULT_OVERRIDES_PATH,
) -> dict[str, Any]:
    """Return the tracked YAML settings merged with user overrides on top."""
    merged = _load_yaml(settings_path)
    merged.update(_load_overrides(overrides_path))
    return merged


def llm_provider(**kwargs: Any) -> str:
    """Return the configured LLM provider id, falling back to the default."""
    value = str(load_settings(**kwargs).get("llm_provider", "")).strip()
    return value if value in PROVIDERS else DEFAULT_PROVIDER_ID


def llm_model(**kwargs: Any) -> str:
    """Return the configured model override, or "" to use the provider default."""
    return str(load_settings(**kwargs).get("llm_model", "")).strip()


def output_language(**kwargs: Any) -> str:
    """Return the configured output language, falling back to Italian."""
    value = str(load_settings(**kwargs).get("output_language", "")).strip().lower()
    return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def ui_language(**kwargs: Any) -> str:
    """Return the dashboard/interface language, falling back to Italian.

    ``output_language`` deliberately remains the public name for generated
    content so existing installs and callers keep their behaviour.
    """
    value = str(load_settings(**kwargs).get("ui_language", "")).strip().lower()
    return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def llm_daily_budget_usd(**kwargs: Any) -> float | None:
    """Return the configured daily LLM spend cap in USD, or None (no cap).

    A missing/zero/negative/unparseable value means unlimited -- the default
    for anyone who hasn't set one, so this is opt-in rather than a surprise
    limit on day one.
    """
    raw = load_settings(**kwargs).get("llm_daily_budget_usd")
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def save_settings_overrides(
    updates: dict[str, Any], *, overrides_path: Path = DEFAULT_OVERRIDES_PATH
) -> Path:
    """Merge ``updates`` into the user-overrides file (read-modify-write).

    Used by the setup wizard and by ``/api/llm-settings`` (Fase 5) so a
    partial update (e.g. only ``llm_provider``) never clobbers other
    previously-set overrides.
    """
    with STORAGE_LOCK:
        current = _load_overrides(overrides_path)
        current.update(updates)
        return save_json(overrides_path, current)
