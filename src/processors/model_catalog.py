"""Local, user-refreshable catalogues of models supported by LLM CLIs.

The app never calls a model-provider inference API for this feature.  A manual
refresh reads two fixed official documentation pages over HTTPS and, when
available, asks Antigravity's local CLI for the models enabled on this account.
The resulting small snapshot is saved locally so the profile form remains fast
and useful while offline.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from src.storage.json_storage import load_json, save_json

CATALOG_PATH = Path("data/model_catalog.json")
_MAX_RESPONSE_BYTES = 1_000_000
_TIMEOUT_SECONDS = 10

SOURCES = {
    "claude": "https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables",
    "codex": "https://developers.openai.com/api/docs/models/all",
    "antigravity": "local CLI: agy models",
}

# A usable offline baseline. A refresh replaces these entries only after it
# obtains at least one validated model identifier from the corresponding source.
DEFAULT_MODELS = {
    "claude": [
        ("claude-opus-5", "Claude Opus 5"),
        ("claude-opus-4-8", "Claude Opus 4.8"),
        ("claude-opus-4-7", "Claude Opus 4.7"),
        ("claude-opus-4-6", "Claude Opus 4.6"),
        ("claude-sonnet-5", "Claude Sonnet 5"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
    ],
    "codex": [
        ("gpt-6-astra", "GPT-6 Astra"),
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-5.3-codex", "GPT-5.3-Codex"),
    ],
    "antigravity": [],
}


def read_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    """Return the latest valid snapshot, falling back to the bundled list."""
    saved = load_json(path, default={})
    providers = saved.get("providers") if isinstance(saved, dict) else None
    if not isinstance(providers, dict):
        providers = {}
    merged: dict[str, Any] = {}
    for provider, defaults in DEFAULT_MODELS.items():
        entry = providers.get(provider)
        models = entry.get("models") if isinstance(entry, dict) else None
        merged[provider] = {
            "models": models if _valid_models(models) else _records(defaults),
            "source": str(entry.get("source", SOURCES[provider])) if isinstance(entry, dict) else SOURCES[provider],
            "updated_at": str(entry.get("updated_at", "")) if isinstance(entry, dict) else "",
        }
    return {"providers": merged}


def refresh_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    """Refresh every catalogue from its fixed, inspectable source."""
    current = read_catalog(path)["providers"]
    candidates = {
        "claude": _fetch_claude_models(),
        "codex": _fetch_codex_models(),
        "antigravity": _list_antigravity_models(),
    }
    now = datetime.now(timezone.utc).isoformat()
    for provider, models in candidates.items():
        if models:
            current[provider] = {"models": models, "source": SOURCES[provider], "updated_at": now}
    save_json(path, {"providers": current})
    return {"providers": current}


def _fetch_text(url: str) -> str:
    response = requests.get(url, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": "Signal-Desk-model-catalog/1"})
    response.raise_for_status()
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise ValueError("catalogue source too large")
    return BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)


def _fetch_claude_models() -> list[dict[str, str]]:
    try:
        text = _fetch_text(SOURCES["claude"])
    except (requests.RequestException, ValueError):
        return []
    names = sorted(set(re.findall(r"Claude (?:Fable|Mythos|Opus|Sonnet|Haiku) \d(?:\.\d+)?", text)))
    return _records([(name.lower().replace(" ", "-").replace(".", "-"), name) for name in names])


def _fetch_codex_models() -> list[dict[str, str]]:
    try:
        text = _fetch_text(SOURCES["codex"])
    except (requests.RequestException, ValueError):
        return []
    ids = sorted(set(re.findall(r"gpt-(?:6-astra|5\.6-(?:sol|terra|luna)|5\.5|5\.3-codex)", text)))
    return _records([(model, model.upper().replace("GPT-", "GPT-")) for model in ids])


def _list_antigravity_models() -> list[dict[str, str]]:
    if shutil.which("agy") is None:
        return []
    try:
        result = subprocess.run(["agy", "models"], capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    pairs = []
    for line in result.stdout.splitlines():
        columns = line.split("\t", 1)
        if len(columns) == 2 and re.fullmatch(r"[a-z0-9._-]{1,64}", columns[0].strip()):
            pairs.append((columns[0].strip(), columns[1].strip()[:120]))
    return _records(pairs)


def _records(values: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"id": model_id, "label": label} for model_id, label in values if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", model_id)]


def _valid_models(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, dict) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", str(item.get("id", "")))
        for item in value
    )
