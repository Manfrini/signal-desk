"""LLM-based insight generation via the local Claude Code CLI.

This module generates per-item insights (summary, why_it_matters,
possible_action, relevance) using the configured local LLM CLI. The CLI's
authentication status identifies account or API-key access when available.

Design constraints (decided with the project owner):
- Only items from the last 24h that are not already cached are sent to
  Claude. Previously analyzed items are reused from the on-disk cache.
- Items are analyzed in small batches to amortize subprocess overhead.
- Generation is local; the CLI must be available or the caller falls back
  to the deterministic rule-based pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

from src.processors.intelligence import _parse_date  # reuse lenient date parsing
from src.processors.prompt_lang import language_directive
from src.processors.llm_provider import (
    STRUCTURED_SCHEMA_FILE,
    ProviderInvocationError,
    build_command,
    get_provider,
    make_strict_schema,
    normalize_output,
    reported_cost,
)
from src.processors.prompt_safety import make_nonce, untrusted_block, untrusted_rule

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_RETRIES = 1

# JSON Schema passed to `claude --json-schema` so the model returns a
# validated array of per-item insight objects.
INSIGHT_ARRAY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "summary": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "possible_action": {"type": "string"},
                    "relevance": {"type": "integer"},
                    "relevance_to_goal": {"type": "integer"},
                },
                "required": [
                    "index",
                    "summary",
                    "why_it_matters",
                    "possible_action",
                    "relevance",
                ],
            },
        }
    },
    "required": ["items"],
}

# Fields copied from an LLM insight onto a processed item.
INSIGHT_FIELDS = ("summary", "why_it_matters", "possible_action")


class ClaudeCliError(RuntimeError):
    """Raised when the Claude CLI cannot be invoked or returns bad output."""


# When the provider reports an exhausted quota, every further call fails the
# same way until the plan resets. Stop asking for a while instead of failing
# the whole backlog one batch at a time. State is per process; the cooldown
# keeps a long-running server from staying blocked after the quota resets.
QUOTA_COOLDOWN_SECONDS = 1800
_QUOTA_ERROR = re.compile(r"usage limit|rate limit|quota|out of credits|insufficient credits", re.I)
_quota_blocked_until = 0.0
_quota_detail = ""


def is_quota_error(message: str) -> bool:
    return bool(_QUOTA_ERROR.search(message or ""))


def quota_block() -> str:
    """The provider's quota message while calls are paused, else ``""``."""
    return _quota_detail if time.monotonic() < _quota_blocked_until else ""


def is_available(binary: str = "") -> bool:
    """Return whether the configured (or given) LLM CLI binary is on PATH.

    With no ``binary``, resolves the currently configured provider (Fase 5:
    claude or codex) rather than assuming claude -- a pipeline configured for
    codex must not silently fall back to rule-based analysis just because
    ``claude`` happens to be absent.
    """
    if not binary:
        binary = get_provider(_configured_provider()).binary
    return shutil.which(binary) is not None


def _configured_provider() -> str:
    from src.settings import llm_provider

    return llm_provider()


def _configured_model() -> str:
    from src.settings import llm_model

    return llm_model()


def _detect_auth_mode(provider: str, binary: str, env: dict[str, str]) -> str:
    # Lazy import: health probes also use our cleaned subprocess environment.
    from src.processors.llm_health import detect_auth_mode

    return detect_auth_mode(provider, binary, env)


class ClaudeCliAnalyzer:
    """Generate insights for raw items by calling the local Claude CLI."""

    def __init__(
        self,
        binary: str = "",
        model: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        goal: str = "",
        provider: str = "",
        language: str = "it",
    ) -> None:
        # `provider` selects the CLI (see src/processors/llm_provider.py);
        # "" means "use whatever src.settings.llm_provider() currently says"
        # (claude by default). `binary`/`model` stay "" unless the caller
        # explicitly overrides them, so the resolved provider's own binary
        # and default model apply -- hardcoding "claude"/"sonnet" here would
        # silently force every codex-configured call back onto claude.
        self.provider = provider or _configured_provider()
        self.binary = binary
        # Same resolution as `provider`: an unset model falls back to the
        # configured override (`llm_model`), and only then to the provider's
        # own default inside build_command. Without this the model chosen in
        # /api/llm-settings was persisted and echoed back as active while
        # never reaching a single CLI invocation.
        self.model = model or _configured_model()
        self.batch_size = max(1, batch_size)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.goal = goal.strip()
        self.language = language
        # Real usage accumulated across batches (from the CLI wrapper).
        self.total_cost_usd: float | None = None
        self._cost_complete = True
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_input_tokens = 0
        self.cache_write_input_tokens = 0
        self.reasoning_output_tokens = 0
        self.calls = 0
        self.auth_mode = "unknown"
        self._auth_checked = False

    def analyze_items(
        self, raw_items: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Return insights keyed by item URL for the given raw items.

        Items without a URL are skipped. A failed batch is logged and
        omitted from the result so the caller can fall back per item.
        """
        analyzable = [item for item in raw_items if str(item.get("url", "")).strip()]
        insights: dict[str, dict[str, Any]] = {}
        for start in range(0, len(analyzable), self.batch_size):
            batch = analyzable[start : start + self.batch_size]
            try:
                batch_insights = self._analyze_batch(batch)
            except ClaudeCliError as exc:
                logger.warning("Claude CLI batch failed, skipping: %s", exc)
                if quota_block():
                    logger.warning("Provider quota exhausted: skipping the remaining batches.")
                    break
                continue
            insights.update(batch_insights)
        return insights

    def _analyze_batch(
        self, batch: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        prompt = _build_batch_prompt(batch, self.goal, language=self.language)
        raw_output = self._invoke_with_retries(prompt, INSIGHT_ARRAY_SCHEMA)
        self._accumulate_usage(raw_output)
        parsed = _parse_cli_result(raw_output)
        return _map_insights_to_urls(parsed, batch)

    def _accumulate_usage(self, raw_output: str) -> None:
        """Add this call's cost/tokens from the CLI wrapper (best-effort)."""
        self.calls += 1
        try:
            wrapper = json.loads(raw_output)
        except (json.JSONDecodeError, TypeError):
            wrapper = {}
        if not isinstance(wrapper, dict):
            wrapper = {}
        cost = reported_cost(wrapper.get("total_cost_usd"))
        if cost is None or wrapper.get("cost_estimated") is True:
            self._cost_complete = False
        self.total_cost_usd = (
            (self.total_cost_usd or 0.0) + cost
            if self._cost_complete and cost is not None else None
        )
        usage = wrapper.get("usage") or {}
        if isinstance(usage, dict):
            # `input_tokens` is only the non-cached fraction; the bulk of the
            # input (incl. Claude Code's system prompt) is in the cache fields.
            self.input_tokens += (
                int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
            )
            self.output_tokens += int(usage.get("output_tokens") or 0)
            self.cached_input_tokens += int(
                usage.get("cached_input_tokens", usage.get("cache_read_input_tokens")) or 0
            )
            self.cache_write_input_tokens += int(
                usage.get("cache_write_input_tokens", usage.get("cache_creation_input_tokens")) or 0
            )
            self.reasoning_output_tokens += int(usage.get("reasoning_output_tokens") or 0)

    def usage_summary(self) -> dict[str, Any]:
        """Return accumulated usage for logging/reporting."""
        spec = get_provider(self.provider)
        return {
            "calls": self.calls,
            "total_cost_usd": self.total_cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "provider": self.provider,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "auth_mode": self.auth_mode,
            # Persist the requested model alongside the provider so the
            # dashboard can make every historical run auditable. An empty
            # value deliberately means the provider CLI chose its own default.
            "model": self.model or spec.default_model,
        }

    def run_structured(self, prompt: str, schema: dict[str, Any]) -> Any:
        """Run one structured-output call and return the parsed payload.

        Returns the ``structured_output`` object, or None on failure. Used for
        one-off structured tasks (e.g. section briefs), separate from the
        batched item analysis.
        """
        try:
            raw = self._invoke_with_retries(prompt, schema)
        except ClaudeCliError as exc:
            logger.warning("Claude CLI structured call failed: %s", exc)
            return None
        self._accumulate_usage(raw)
        try:
            wrapper = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Structured %s response was not valid JSON (model=%s).",
                get_provider(self.provider).label,
                self.model or "default",
            )
            return None
        if isinstance(wrapper, dict) and wrapper.get("is_error"):
            logger.warning(
                "Structured %s response reported an error (model=%s).",
                get_provider(self.provider).label,
                self.model or "default",
            )
            return None
        payload = _extract_payload(wrapper) if isinstance(wrapper, dict) else None
        # Claude returns an object in ``structured_output``. Codex's JSONL
        # stream instead carries the schema-constrained object as JSON text in
        # its final agent message, normalized above into ``result``.
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning(
                    "Structured %s response did not contain JSON text (model=%s).",
                    get_provider(self.provider).label,
                    self.model or "default",
                )
                return None
        if not isinstance(payload, (dict, list)):
            logger.warning(
                "Structured %s response had no usable payload (model=%s).",
                get_provider(self.provider).label,
                self.model or "default",
            )
            return None
        return payload

    def _invoke_with_retries(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        paused = quota_block()
        if paused:
            raise ClaudeCliError(f"provider quota exhausted, call skipped: {paused}")
        try:
            return self._invoke_retrying(prompt, schema)
        except ClaudeCliError as exc:
            if is_quota_error(str(exc)):
                global _quota_blocked_until, _quota_detail
                _quota_blocked_until = time.monotonic() + QUOTA_COOLDOWN_SECONDS
                _quota_detail = str(exc)
            raise

    def _invoke_retrying(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._invoke(prompt, schema)
            except (subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
                self._cost_complete = False
                self.total_cost_usd = None
                last_error = exc
                logger.info(
                    "Claude CLI invocation attempt %d failed: %s", attempt + 1, exc
                )
        raise ClaudeCliError(f"Claude CLI invocation failed: {last_error}")

    def _invoke(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        spec = get_provider(self.provider)
        env = _clean_subprocess_env()
        if not self._auth_checked:
            self.auth_mode = _detect_auth_mode(self.provider, self.binary, env)
            self._auth_checked = True
        resolved_schema = schema or INSIGHT_ARRAY_SCHEMA

        schema_path = ""
        cwd_dir = ""
        if spec.structured_mode == STRUCTURED_SCHEMA_FILE:
            # codex has no inline-schema flag: the (strict-mode-transformed)
            # schema goes to a temp file, and -C points it at a throwaway
            # directory so it never touches the real repo -- both cleaned up
            # below regardless of outcome.
            cwd_dir = tempfile.mkdtemp(prefix="llm-provider-cwd-")
            fd, schema_path = tempfile.mkstemp(suffix=".json", prefix="llm-schema-")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(make_strict_schema(resolved_schema), handle)

        try:
            command = build_command(
                spec,
                prompt,
                resolved_schema,
                self.model,
                binary=self.binary,
                schema_path=schema_path,
                cwd_dir=cwd_dir,
            )
            # The subprocess call stays here (not in llm_provider) so the CLI
            # argv-building/output-parsing can be shared across providers
            # while `claude_cli_analyzer.subprocess.run` remains the one
            # mockable seam every existing test patches.
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env=env,
                stdin=subprocess.DEVNULL,
            )
            try:
                return normalize_output(spec, completed)
            except ProviderInvocationError as exc:
                self._cost_complete = False
                self.total_cost_usd = None
                raise ClaudeCliError(str(exc)) from exc
        finally:
            if schema_path:
                try:
                    os.unlink(schema_path)
                except OSError:
                    pass
            if cwd_dir:
                shutil.rmtree(cwd_dir, ignore_errors=True)


# What the CLI subprocess is allowed to inherit. An allow-list, not a
# deny-list: the CLI is fed third-party web text, and on the codex provider
# it can run shell commands (verified: `codex exec -s read-only` reads files
# outside its -C working directory, and there is no flag to disable its shell
# tool). Everything left in this environment is therefore readable by a
# successful prompt injection, so unrelated secrets -- GITHUB_TOKEN, AWS_*,
# database URLs -- must not be there to read.
_ENV_ALLOWLIST = frozenset(
    {
        # POSIX essentials
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TERM", "TZ",
        "LANG", "LC_ALL", "LC_CTYPE",
        # Windows essentials
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
        "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP",
        # Corporate networks: without these the CLI cannot reach its API.
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "no_proxy",
        # Provider credentials and config. ANTHROPIC_* is deliberately absent:
        # see the note below.
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME",
    }
)


def _clean_subprocess_env() -> dict[str, str]:
    """Return the minimal environment the provider CLI needs to run.

    Two jobs. First, keep the blast radius of a prompt injection small by
    passing an allow-list instead of the parent's whole environment.

    Second, the original reason this function exists: when the pipeline runs
    inside an active Claude Code session, inherited variables like
    ``ANTHROPIC_BASE_URL`` or ``CLAUDE_CODE_*`` point the nested CLI at the
    parent's proxy and make it hang. They are simply not in the allow-list,
    so the headless ``claude`` starts its own authenticated session.
    """
    return {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}


def filter_items_for_analysis(
    raw_items: list[dict[str, Any]],
    cached_urls: set[str],
    *,
    now: datetime | None = None,
    max_age_hours: int = 7 * 24,
) -> list[dict[str, Any]]:
    """Return items from the last ``max_age_hours`` (default a week) not already cached.

    Items whose date cannot be parsed are treated conservatively as recent
    so genuinely new content is not silently dropped.
    """
    current = now or datetime.now(timezone.utc)
    selected: list[dict[str, Any]] = []
    for item in raw_items:
        url = str(item.get("url", "")).strip()
        if not url or url in cached_urls:
            continue
        if _within_age(item, current, max_age_hours):
            selected.append(item)
    return selected


def _within_age(
    item: dict[str, Any], current: datetime, max_age_hours: int
) -> bool:
    raw_date = str(item.get("published_at") or item.get("collected_at") or "").strip()
    parsed = _parse_date(raw_date)
    if parsed is None:
        return True
    item_dt = datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
    age_hours = (current - item_dt).total_seconds() / 3600
    return age_hours <= max_age_hours


def _build_batch_prompt(
    batch: list[dict[str, Any]], goal: str = "", *, language: str = "it"
) -> str:
    """Build an Italian analysis prompt for a batch of items.

    When ``goal`` is set, why_it_matters/possible_action are oriented toward
    that goal and a ``relevance_to_goal`` score is requested.
    """
    nonce = make_nonce()
    blocks: list[str] = []
    for index, item in enumerate(batch):
        title = str(item.get("clean_title") or item.get("title") or "").strip()
        source = str(item.get("source_name") or item.get("company") or "").strip()
        content = _content_for_prompt(item)
        # Title, source and body all come from third parties: the whole
        # per-item record goes inside one delimited block.
        record = f"Fonte: {source}\nTitolo: {title}\nContenuto: {content}"
        blocks.append(f"--- Notizia {index} ---\n" + untrusted_block(record, nonce))
    joined = "\n\n".join(blocks)

    goal_block = ""
    goal_fields = ""
    if goal:
        goal_block = (
            "Profilo dell'utente per cui analizzi:\n"
            f"{goal}\n"
            "Orienta why_it_matters e possible_action a questo profilo.\n\n"
        )
        goal_fields = (
            "- relevance_to_goal: intero da 1 (irrilevante per il profilo) a "
            "5 (molto utile per il profilo).\n"
        )

    return (
        "Sei un analista di intelligence su AI e tecnologia. "
        f"{untrusted_rule(nonce)}"
        f"{goal_block}"
        "Analizza le notizie seguenti e, per ciascuna, produci:\n"
        "- summary: 1-2 frasi concrete su cosa è successo (no marketing), "
        "NELLA STESSA LINGUA della notizia originale (non tradurre).\n"
        f"- why_it_matters: 1 frase {language_directive(language)} sul perché è "
        "rilevante per chi costruisce prodotti AI o vuole migliorare le proprie "
        "competenze tecniche.\n"
        f"- possible_action: 1 azione pratica e specifica, {language_directive(language)}.\n"
        "- relevance: intero da 1 (rumore) a 5 (segnale forte).\n"
        f"{goal_fields}"
        "Restituisci un oggetto con un array 'items', uno per notizia, "
        "usando lo stesso 'index' numerico mostrato qui sotto.\n\n"
        f"{joined}"
    )


def _content_for_prompt(item: dict[str, Any], max_chars: int = 1500) -> str:
    for key in ("excerpt", "raw_content", "raw_summary"):
        value = str(item.get(key, "")).strip()
        if value:
            return value[:max_chars]
    return ""


def _parse_cli_result(raw_output: str) -> list[dict[str, Any]]:
    """Parse the CLI wrapper JSON and extract the structured insights.

    With ``--json-schema`` the validated payload is returned in the
    ``structured_output`` field. Older/other CLI versions may instead put
    it in ``result`` (as a JSON string or object), so both are handled
    defensively.
    """
    try:
        wrapper = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClaudeCliError(f"Could not parse CLI output as JSON: {exc}") from exc

    if isinstance(wrapper, dict) and wrapper.get("is_error"):
        raise ClaudeCliError(f"CLI reported an error: {wrapper.get('result')}")

    payload = _extract_payload(wrapper)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ClaudeCliError(f"Could not parse inner result JSON: {exc}") from exc

    if isinstance(payload, dict):
        items = payload.get("items", [])
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    if not isinstance(items, list):
        raise ClaudeCliError("CLI result did not contain an items array")
    return [entry for entry in items if isinstance(entry, dict)]


def _extract_payload(wrapper: Any) -> Any:
    """Return the structured payload from the CLI wrapper object."""
    if not isinstance(wrapper, dict):
        return wrapper
    structured = wrapper.get("structured_output")
    if structured is not None:
        return structured
    return wrapper.get("result", wrapper)


def _map_insights_to_urls(
    parsed: list[dict[str, Any]], batch: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Map parsed insights back to item URLs via their batch index."""
    insights: dict[str, dict[str, Any]] = {}
    for entry in parsed:
        index = entry.get("index")
        if not isinstance(index, int) or not 0 <= index < len(batch):
            continue
        url = str(batch[index].get("url", "")).strip()
        if not url:
            continue
        insights[url] = _normalize_insight(entry)
    return insights


def _normalize_insight(entry: dict[str, Any]) -> dict[str, Any]:
    insight: dict[str, Any] = {
        "summary": str(entry.get("summary", "")).strip(),
        "why_it_matters": str(entry.get("why_it_matters", "")).strip(),
        "possible_action": str(entry.get("possible_action", "")).strip(),
        "source": "claude_cli",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    relevance = entry.get("relevance")
    if isinstance(relevance, int):
        insight["relevance"] = max(1, min(5, relevance))
    relevance_to_goal = entry.get("relevance_to_goal")
    if isinstance(relevance_to_goal, int):
        insight["relevance_to_goal"] = max(1, min(5, relevance_to_goal))
    return insight
