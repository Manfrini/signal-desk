"""Health of the local LLM CLI: is the binary present and authenticated.

Wraps each provider's own status check (``claude auth status --json``,
``codex login status``) so the dashboard and the pipeline can tell the user
when LLM analysis has silently fallen back to the deterministic path (e.g.
the CLI's OAuth session expired) -- instant, no API call, no cost.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.processors.claude_cli_analyzer import _clean_subprocess_env
from src.processors.llm_provider import ProviderSpec, get_provider
from src.storage.json_storage import load_json, save_json

logger = logging.getLogger(__name__)

LLM_STATUS_FILENAME = "llm_status.json"
_STATUS_TIMEOUT_SECONDS = 10

# Keyed by provider id, since more than one CLI can be checked (the setup
# wizard probes whichever the user is about to select, independent of
# whatever `src.settings.llm_provider()` currently says).
_CACHE: dict[str, dict[str, Any]] = {}


def reset_health_cache(provider: str = "") -> None:
    """Drop the in-memory health cache (used by tests and after a login).

    With no ``provider``, clears every cached provider's result.
    """
    if provider:
        _CACHE.pop(provider, None)
    else:
        _CACHE.clear()


def _real_run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_STATUS_TIMEOUT_SECONDS,
        check=False,
        env=_clean_subprocess_env() if env is None else env,
        stdin=subprocess.DEVNULL,
    )


def detect_auth_mode(provider: str, binary: str, env: dict[str, str]) -> str:
    """Probe the execution environment without using cached account state.

    Return only a classification; never persist credentials or CLI output.
    Environment-based routing can override the saved login, so an account
    status alone is insufficient in that case.
    """
    spec = get_provider(provider)
    if binary and spec.auth_status_cmd:
        spec = replace(spec, binary=binary, auth_status_cmd=(binary, *spec.auth_status_cmd[1:]))
    result = _probe(spec, lambda cmd: _real_run(cmd, env=env))
    mode = result.get("auth_mode", "unknown")
    if provider == "codex" and any(env.get(key) for key in (
        "CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    )):
        return "unknown"
    return mode


def claude_cli_health(
    *,
    provider: str = "",
    ttl_seconds: int = 60,
    _run: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
    _now: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Return the current LLM CLI health, cached per-provider for ``ttl_seconds``.

    ``{"ok": bool, "reason": str, "detail": str, "checked_at": iso}`` where
    ``reason`` is one of ``ok``, ``not_installed``, ``logged_out``, ``error``.
    With no ``provider``, checks whatever ``src.settings.llm_provider()``
    currently says (claude by default).
    """
    spec = get_provider(provider)
    now = (_now or time.monotonic)()
    cached = _CACHE.get(spec.id)
    if cached is not None and now - cached["at"] < ttl_seconds:
        return cached["result"]

    result = _probe(spec, _run or _real_run)
    _CACHE[spec.id] = {"result": result, "at": now}
    return result


def _probe(spec: ProviderSpec, run: Callable[[list[str]], subprocess.CompletedProcess]) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    base = {"ok": False, "reason": "error", "detail": "", "checked_at": checked_at,
            "auth_mode": "unknown"}

    if shutil.which(spec.binary) is None:
        return {**base, "reason": "not_installed", "detail": f"binario `{spec.binary}` non trovato sul PATH"}

    if spec.auth_status_cmd is None:
        return {**base, "detail": f"nessun controllo di stato disponibile per {spec.label}"}

    try:
        completed = run(list(spec.auth_status_cmd))
    except (subprocess.SubprocessError, OSError) as exc:
        # Exceptions can include credentials or command lines: log only the type.
        logger.warning("%s CLI health probe failed (%s).", spec.label, type(exc).__name__)
        return {**base, "detail": "controllo dello stato non riuscito"}

    if spec.id == "claude":
        return _parse_claude_status(completed, checked_at, base)
    if spec.id == "codex":
        return _parse_codex_status(completed, checked_at, base, spec)

    return {**base, "detail": f"controllo di stato non implementato per {spec.label}"}


def _parse_claude_status(
    completed: subprocess.CompletedProcess, checked_at: str, base: dict[str, Any]
) -> dict[str, Any]:
    # `claude auth status --json` exits non-zero when logged out but still
    # prints a valid `{"loggedIn": false, ...}` payload, so parse stdout first
    # and only treat it as an error when there is no usable JSON.
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = None

    if isinstance(payload, dict) and "loggedIn" in payload:
        if payload.get("loggedIn") is True:
            mode = {"claude.ai": "account", "api_key": "api_key"}.get(
                payload.get("authMethod"), "unknown"
            ) if isinstance(payload.get("authMethod"), str) else "unknown"
            if payload.get("apiProvider") not in (None, "firstParty"):
                mode = "unknown"
            if mode == "account" and payload.get("apiKeySource"):
                mode = "unknown"
            return {**base, "ok": True, "reason": "ok", "detail": "", "auth_mode": mode}
        return {**base, "reason": "logged_out", "detail": "sessione Claude non attiva"}

    raw = (completed.stderr or completed.stdout or "").strip()
    if raw:
        # Status output may contain credentials; log no raw response.
        logger.warning("Unreadable Claude CLI auth status.")
    return {**base, "detail": "stato di autenticazione non leggibile"}


def _parse_codex_status(
    completed: subprocess.CompletedProcess,
    checked_at: str,
    base: dict[str, Any],
    spec: ProviderSpec,
) -> dict[str, Any]:
    # `codex login status` has no --json flag (verified live): parse its
    # plain-text output. Confirmed live, logged in: exit 0, "Logged in using
    # ChatGPT" -- printed to STDERR, not stdout (verified by inspecting the
    # streams separately; a naive stdout-only check silently misreports a
    # logged-in session as logged out). The logged-out exit code/text was
    # NOT verified on this machine (already logged in); treat any non-zero
    # exit or text that doesn't say "logged in" as logged_out rather than
    # error, since that is the far more likely case for an unauthenticated
    # CLI.
    text = ((completed.stdout or "") + " " + (completed.stderr or "")).strip()
    lowered = text.lower()
    # The logged-out response is literally "Not logged in". Check it before
    # the success phrase: a substring-only success test would make the auth
    # manager kill the browser/device flow immediately.
    if "not logged in" in lowered or "logged out" in lowered:
        return {**base, "reason": "logged_out", "detail": f"sessione {spec.label} non attiva"}
    if completed.returncode == 0 and "logged in" in lowered:
        account = "logged in using chatgpt" in lowered
        api_key = "logged in using an api key" in lowered
        mode = "unknown" if account == api_key else ("account" if account else "api_key")
        return {**base, "ok": True, "reason": "ok", "detail": "", "auth_mode": mode}
    return {**base, "reason": "logged_out", "detail": f"sessione {spec.label} non attiva"}


def read_llm_status(base_dir: str | Path = "data") -> dict[str, Any]:
    """Return the persisted LLM status, or ``{}`` if absent/unreadable."""
    data = load_json(Path(base_dir) / LLM_STATUS_FILENAME, default={})
    return data if isinstance(data, dict) else {}


def write_llm_status(health: dict[str, Any], *, base_dir: str | Path = "data") -> Path:
    """Persist ``health`` to ``data/llm_status.json``.

    ``last_ok_at`` is set to the current check time when healthy, otherwise the
    previously stored ``last_ok_at`` is carried forward so the UI can show how
    long the LLM has been unavailable.
    """
    previous = read_llm_status(base_dir)
    checked_at = health.get("checked_at") or datetime.now(timezone.utc).isoformat()
    record = {
        "ok": bool(health.get("ok")),
        "reason": health.get("reason", "error"),
        "detail": health.get("detail", ""),
        "checked_at": checked_at,
        "last_ok_at": checked_at if health.get("ok") else previous.get("last_ok_at"),
    }
    return save_json(Path(base_dir) / LLM_STATUS_FILENAME, record)
