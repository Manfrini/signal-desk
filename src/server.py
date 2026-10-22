"""Local-only HTTP server to run the analysis from inside the dashboard.

Serves the generated dashboard and exposes two endpoints:
- ``GET /api/status`` — current analysis state (idle/running/done/error)
- ``POST /api/run`` — start the daily pipeline (collect + analyze with the
  Claude CLI + regenerate outputs) in a background thread

Binds to 127.0.0.1 only, so it is never exposed on the network. No external
dependencies — standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from datetime import date as date_type
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

if os.name != "nt":
    import pty
    import select

from src.processors.claude_cli_analyzer import _clean_subprocess_env
from src.processors.llm_health import claude_cli_health, read_llm_status
from src.storage.json_storage import DEFAULT_SOURCES_PATH, load_transcript_status, today_utc

logger = logging.getLogger(__name__)

DEFAULT_DASHBOARD_PATH = Path("outputs/dashboard/index.html")
DEFAULT_DESIGN_MM_DIR = Path("design/mm")
MAX_REQUEST_BODY_BYTES = 1_000_000  # 1 MB is generous for this app's JSON payloads
# A stored quota failure is shown for this long unless a later run clears it.
QUOTA_STATUS_MAX_AGE_SECONDS = 3 * 3600
APP_MARKER = "signal-desk"  # identifies this server on /api/status
PORT_FALLBACK_ATTEMPTS = 20  # 8000..8019 before giving up

_running_server: "ThreadingHTTPServer | None" = None

_ASSET_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


def _serve_design_asset(send: "Callable[[int, bytes | str, str], None]", request_path: str) -> bool:
    """Serve a read-only static file from design/mm/ if the path targets it.

    Returns True once the request has been handled (found or not), False if
    the path isn't under /design/mm/ at all (caller should try other routes).
    Guards against path traversal by resolving against the real directory.
    """
    prefix = "/design/mm/"
    if request_path != "/design/mm" and not request_path.startswith(prefix):
        return False

    base_dir = DEFAULT_DESIGN_MM_DIR.resolve()
    relative = request_path[len(prefix):] if request_path.startswith(prefix) else ""
    candidate = (base_dir / relative).resolve()

    if base_dir not in candidate.parents and candidate != base_dir:
        send(403, json.dumps({"error": "forbidden"}), "application/json")
        return True
    if not candidate.is_file():
        send(404, json.dumps({"error": "not found"}), "application/json")
        return True

    content_type = _ASSET_CONTENT_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
    send(200, candidate.read_bytes(), content_type)
    return True


class AnalysisRunner:
    """Run the pipeline in a background thread and expose its state."""

    def __init__(self, runner: Callable[[], Any] | None = None) -> None:
        self._runner = runner or self._default_runner
        self._lock = threading.Lock()
        self.status = "idle"  # idle | running | done | error
        self.error = ""
        self.last_result: Any = None
        self._source_id: str | None = None

    def _default_runner(self) -> Any:
        from src.pipeline.daily_pipeline import DailyPipeline

        return DailyPipeline().run(dry_run=False, source_id=self._source_id)

    def start(self, source_id: str | None = None) -> bool:
        """Start an analysis if one is not already running.

        If ``source_id`` is given, only that source is (re)collected — used
        for a manual per-source rerun from the Fonti page.
        """
        with self._lock:
            if self.status == "running":
                return False
            self.status = "running"
            self.error = ""
            self._source_id = source_id
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            self.last_result = self._runner()
            self.status = "done"
            logger.info("Analysis run completed.")
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            self.error = str(exc)
            self.status = "error"
            logger.exception("Analysis run failed.")

    def snapshot(self) -> dict[str, str]:
        return {"status": self.status, "error": self.error, "source_id": self._source_id or ""}


_URL_RE = re.compile(r"https?://\S+")
_DEVICE_CODE_RE = re.compile(
    r"(?:device\s+)?(?:code|codice)(?:\s+(?:is|è|di\s+verifica))?\s*[:=\-]?\s*"
    r"([A-Z0-9]{4,}(?:[-\s][A-Z0-9]{4,})+)",
    re.I,
)


class LlmAuthManager:
    """Run a provider's browser login in a PTY and expose only its state.

    The CLI drives the whole OAuth flow itself (opens the browser, catches
    the localhost redirect, writes its own credential store). We just start
    it, watch for completion via the provider's own status check, and time
    out.

    The PTY output is scanned once for the authorize URL (shown as a fallback
    link if the browser did not open) and is otherwise never surfaced or
    logged — it can contain tokens.
    """

    def __init__(
        self,
        login_cmd: "list[str] | None" = None,
        status_probe: "Callable[[], bool] | None" = None,
        timeout_seconds: float = 180.0,
        poll_seconds: float = 2.0,
        provider: str = "",
    ) -> None:
        from src.processors.llm_provider import get_provider

        spec = get_provider(provider)
        self._provider = spec.id
        self._label = spec.label
        self._login_cmd = login_cmd or list(spec.login_cmd or [])
        self._status_probe = status_probe or self._default_probe
        self._timeout = timeout_seconds
        self._poll = poll_seconds
        self._lock = threading.Lock()
        self.state = "idle"  # idle | pending | success | error | timeout
        self.url = ""
        self.device_code = ""
        self.message = ""

    def _default_probe(self) -> bool:
        from src.processors.llm_health import claude_cli_health as _health

        return bool(_health(provider=self._provider, ttl_seconds=0).get("ok"))

    def start(self) -> bool:
        with self._lock:
            if self.state == "pending":
                return False
            self.state = "pending"
            self.url = ""
            self.device_code = ""
            self.message = f"Apertura del browser per l'accesso a {self._label}…"
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def snapshot(self) -> "dict[str, str]":
        return {
            "state": self.state,
            "url": self.url,
            "device_code": self.device_code,
            "message": self.message,
            "provider": self._provider,
        }

    def _run(self) -> None:
        if os.name == "nt":
            self._run_windows()
            return
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                self._login_cmd,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=_clean_subprocess_env(),
                start_new_session=True,
                close_fds=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            os.close(master)
            os.close(slave)
            self._finish("error", f"Impossibile avviare l'accesso: {exc}"[:200])
            return

        os.close(slave)
        deadline = time.monotonic() + self._timeout
        last_poll = 0.0
        buffer = ""
        try:
            while True:
                if time.monotonic() > deadline:
                    proc.kill()
                    self._finish("timeout", "Tempo scaduto: accesso non completato.")
                    return

                rlist, _, _ = select.select([master], [], [], 0.3)
                if rlist:
                    try:
                        chunk = os.read(master, 4096).decode("utf-8", "replace")
                    except OSError:
                        chunk = ""
                    buffer += chunk
                    if not self.url:
                        found = _URL_RE.search(buffer)
                        if found:
                            self.url = found.group(0).rstrip(").,\r\n")
                    if self._provider == "codex" and not self.device_code:
                        found_code = _DEVICE_CODE_RE.search(buffer)
                        if found_code:
                            self.device_code = found_code.group(1).upper().replace(" ", "-")

                now = time.monotonic()
                if now - last_poll >= self._poll:
                    last_poll = now
                    if self._safe_probe():
                        proc.kill()
                        self._finish("success", f"{self._label} connesso.")
                        return

                if proc.poll() is not None:
                    if self._safe_probe():
                        self._finish("success", f"{self._label} connesso.")
                    else:
                        self._finish("error", "Accesso non riuscito. Riprova.")
                    return
        finally:
            try:
                os.close(master)
            except OSError:
                pass
            if proc.poll() is None:
                proc.kill()

    def _run_windows(self) -> None:
        """Run provider login without POSIX PTY support.

        Codex device auth and current CLI browser flows write their progress
        to stdout.  We expose only the verification URL/code, never raw CLI
        output, exactly as on POSIX.
        """
        try:
            proc = subprocess.Popen(
                self._login_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_clean_subprocess_env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._finish("error", f"Impossibile avviare l'accesso: {exc}"[:200])
            return

        deadline = time.monotonic() + self._timeout
        output = ""
        lines: "queue.Queue[str]" = queue.Queue()

        def read_output() -> None:
            if not proc.stdout:
                return
            for line in iter(proc.stdout.readline, ""):
                lines.put(line)

        threading.Thread(target=read_output, daemon=True).start()
        while time.monotonic() <= deadline:
            try:
                while True:
                    output = (output + lines.get_nowait())[-8000:]
                    self._capture_login_hints(output)
            except queue.Empty:
                pass
            if self._safe_probe():
                proc.terminate()
                self._finish("success", f"{self._label} è connesso.")
                return
            if proc.poll() is not None:
                break
            time.sleep(self._poll)
        if time.monotonic() > deadline:
            proc.kill()
            self._finish("timeout", "Tempo scaduto: accesso non completato.")
        else:
            self._finish("error", "Accesso non completato. Riprova dal Profilo.")

    def _capture_login_hints(self, output: str) -> None:
        """Extract only the user-facing URL/device code from CLI output."""
        match = _URL_RE.search(output)
        if match:
            self.url = match.group(0).rstrip(".,)")
        code_match = _DEVICE_CODE_RE.search(output)
        if code_match:
            self.device_code = code_match.group(1).replace(" ", "-").upper()

    def _safe_probe(self) -> bool:
        try:
            return bool(self._status_probe())
        except Exception:  # noqa: BLE001 - a probe failure just means "not yet"
            return False

    def _finish(self, state: str, message: str) -> None:
        with self._lock:
            self.state = state
            self.message = message


llm_auth = LlmAuthManager()  # kept as a plain module attribute: existing
# tests monkeypatch `server_mod.llm_auth` directly, and it's also simply the
# claude manager -- the common case, since claude is the default provider.
_llm_auth_by_other_provider: dict[str, LlmAuthManager] = {}


def get_llm_auth(provider: str = "") -> LlmAuthManager:
    """Return the LlmAuthManager for ``provider`` (default: the configured one).

    Routing through this instead of the bare ``llm_auth`` global is what lets
    the "Connetti" button work for whichever provider is selected in
    settings, while ``llm_auth`` itself stays the plain, always-claude
    instance existing tests already patch.
    """
    from src.settings import llm_provider as _configured_llm_provider

    provider_id = provider or _configured_llm_provider()
    if provider_id == "claude":
        return llm_auth
    if provider_id not in _llm_auth_by_other_provider:
        _llm_auth_by_other_provider[provider_id] = LlmAuthManager(provider=provider_id)
    return _llm_auth_by_other_provider[provider_id]


MAX_CONCURRENT_DEEP_DIVES = 2


class DeepDiveManager:
    """Generate and cache per-URL deep dives, one background job per URL."""

    def __init__(self, generator: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self._generator = generator or self._default_generator
        self._lock = threading.Lock()
        self._status: dict[str, str] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}
        self._active_count = 0
        try:
            from src.storage.json_storage import load_deep_dives

            cached = load_deep_dives()
            # Older builds persisted failures as results. Ignore them, so an
            # article poisoned that way can be analysed again.
            self._results.update(
                {u: d for u, d in cached.items() if not (isinstance(d, dict) and d.get("failed"))}
            )
        except Exception:  # noqa: BLE001 - startup must not crash on bad cache state
            logger.exception("Failed to load cached deep dives at startup.")

    @staticmethod
    def _default_generator(item: dict[str, Any]) -> dict[str, Any]:
        from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer, is_available
        from src.processors.deep_dive import generate_deep_dive
        from src.processors.llm_budget import is_budget_exceeded
        from src.processors.profile import load_profile, profile_context
        from src.settings import output_language
        from src.storage.json_storage import append_llm_usage_log

        goal = profile_context(load_profile())
        language = output_language()
        analyzer = (
            ClaudeCliAnalyzer(goal=goal, language=language)
            if (is_available() and not is_budget_exceeded())
            else None
        )
        dive = generate_deep_dive(item, goal=goal, analyzer=analyzer, language=language)
        if analyzer is not None and analyzer.calls:
            append_llm_usage_log(
                {
                    "date": today_utc().isoformat(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "items_analyzed": 1,
                    "kind": "deep_dive",
                    **analyzer.usage_summary(),
                }
            )
        return dive

    def get(self, url: str) -> dict[str, Any]:
        if url in self._results:
            return {"status": "done", "result": self._results[url]}
        status = self._status.get(url, "idle")
        return {"status": status, "result": None, "error": self._errors.get(url, "")}

    def start(self, url: str, item: dict[str, Any]) -> str:
        """Returns "done"/"running" as before, or "busy" if too many deep
        dives (each a real Claude CLI subprocess) are already in flight."""
        with self._lock:
            if url in self._results:
                return "done"
            if self._status.get(url) == "running":
                return "running"
            if self._active_count >= MAX_CONCURRENT_DEEP_DIVES:
                return "busy"
            self._errors.pop(url, None)
            self._status[url] = "running"
            self._active_count += 1
        threading.Thread(target=self._run, args=(url, item), daemon=True).start()
        return "running"

    def _run(self, url: str, item: dict[str, Any]) -> None:
        try:
            dive = self._generator(item)
            if isinstance(dive, dict) and dive.get("failed"):
                # Not an analysis: leave nothing behind, so the next click on
                # Approfondisci runs the article again instead of replaying
                # the error message from the cache.
                self._status[url] = "error"
                self._errors[url] = str(dive.get("overview") or "")
                logger.warning("Deep dive for %s did not produce an analysis.", url)
                return
            try:
                from src.storage.json_storage import save_deep_dive

                save_deep_dive(url, dive)
            except Exception:  # noqa: BLE001 - the dive result still reaches the UI via _results
                logger.exception("Failed to persist deep dive for %s.", url)
            try:
                # Opening a deep dive is an implicit sign of interest.
                from src.processors.feedback import record_deep_dive
                from src.storage.json_storage import (
                    STORAGE_LOCK,
                    load_feedback,
                    load_preferences,
                    save_feedback,
                    save_preferences,
                )

                with STORAGE_LOCK:
                    feedback, preferences = record_deep_dive(item, load_feedback(), load_preferences())
                    save_feedback(feedback)
                    save_preferences(preferences)
            except Exception:  # noqa: BLE001 - learning from this dive is best-effort
                logger.exception("Failed to record implicit feedback for %s.", url)
            self._results[url] = dive
            self._status[url] = "done"
        except Exception as exc:  # noqa: BLE001
            self._status[url] = "error"
            logger.exception("Deep dive failed for %s: %s", url, exc)
        finally:
            with self._lock:
                self._active_count -= 1


MAX_CONCURRENT_INSIGHTS = 2


class InsightManager:
    """Analyse one article on demand, for items the batch run never reaches.

    The batch analysis covers at most the last week, so an older article, or
    one whose batch failed, can never get an insight from it; this lets the
    reader ask for the one article in front of them.

    Like the deep dives, a run that produces nothing is reported as an error
    and never cached — otherwise the article would be stuck with the failure.
    """

    def __init__(self, generator: "Callable[[dict[str, Any]], dict[str, Any]] | None" = None) -> None:
        self._generator = generator or self._default_generator
        self._lock = threading.Lock()
        self._status: dict[str, str] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}
        self._active_count = 0

    @staticmethod
    def _default_generator(item: dict[str, Any]) -> dict[str, Any]:
        from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer
        from src.processors.profile import load_profile, profile_context
        from src.settings import output_language
        from src.storage.json_storage import append_llm_usage_log

        analyzer = ClaudeCliAnalyzer(goal=profile_context(load_profile()), language=output_language())
        insights = analyzer.analyze_items([item])
        if analyzer.calls:
            append_llm_usage_log(
                {
                    "date": today_utc().isoformat(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "items_analyzed": 1,
                    "kind": "insight_single",
                    **analyzer.usage_summary(),
                }
            )
        return insights.get(str(item.get("url", "")).strip(), {})

    def get(self, url: str) -> dict[str, Any]:
        if url in self._results:
            return {"status": "done", "result": self._results[url]}
        return {
            "status": self._status.get(url, "idle"),
            "result": None,
            "error": self._errors.get(url, ""),
        }

    def start(self, url: str, item: dict[str, Any]) -> str:
        with self._lock:
            if url in self._results:
                return "done"
            if self._status.get(url) == "running":
                return "running"
            if self._active_count >= MAX_CONCURRENT_INSIGHTS:
                return "busy"
            self._errors.pop(url, None)
            self._status[url] = "running"
            self._active_count += 1
        threading.Thread(target=self._run, args=(url, item), daemon=True).start()
        return "running"

    def _run(self, url: str, item: dict[str, Any]) -> None:
        try:
            insight = self._generator(item) or {}
            useful = {
                key: str(insight.get(key, "")).strip()
                for key in ("why_it_matters", "possible_action", "summary")
                if str(insight.get(key, "")).strip()
            }
            if not useful.get("why_it_matters") and not useful.get("possible_action"):
                self._status[url] = "error"
                self._errors[url] = "L'AI non ha prodotto un'analisi per questo articolo."
                return
            from src.storage import json_storage

            json_storage.merge_llm_insights({url: useful})
            self._results[url] = useful
            self._status[url] = "done"
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self._status[url] = "error"
            self._errors[url] = str(exc)[:200]
            logger.exception("Single-item insight failed for %s: %s", url, exc)
        finally:
            with self._lock:
                self._active_count -= 1


MAX_CONCURRENT_NEWSLETTERS = 2


class NewsletterManager:
    """Generate and cache newsletter editions, one background job per period."""

    def __init__(
        self, generator: Callable[[str, str], dict[str, Any]] | None = None
    ) -> None:
        self._generator = generator or self._default_generator
        self._lock = threading.Lock()
        self._active_count = 0
        self._status: dict[str, str] = {}
        self._results: dict[str, dict[str, Any]] = {}
        try:
            from src.storage.json_storage import load_newsletters

            self._results.update(load_newsletters())
        except Exception:  # noqa: BLE001 - startup must not crash on bad cache state
            logger.exception("Failed to load cached newsletters at startup.")

    @staticmethod
    def _key(date_from: str, date_to: str) -> str:
        from src.storage.json_storage import newsletter_slug

        return newsletter_slug(date_from, date_to)

    @staticmethod
    def _default_generator(date_from: str, date_to: str) -> dict[str, Any]:
        from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer, is_available
        from src.processors.llm_budget import is_budget_exceeded
        from src.processors.newsletter import generate_newsletter
        from src.processors.profile import load_profile, profile_context
        from src.settings import output_language
        from src.storage.json_storage import (
            append_llm_usage_log,
            load_recent_processed_news,
        )

        start = _parse_iso_date(date_from)
        end = _parse_iso_date(date_to)
        days = max(1, (today_utc() - start).days + 1) if start else 30
        items = load_recent_processed_news(days=min(days, 365))

        goal = profile_context(load_profile())
        language = output_language()
        llm_enabled = _llm_enabled()
        analyzer = (
            ClaudeCliAnalyzer(goal=goal, language=language)
            if (llm_enabled and is_available() and not is_budget_exceeded())
            else None
        )
        newsletter = generate_newsletter(
            items,
            date_from=date_from,
            date_to=date_to,
            goal=goal,
            llm_enabled=llm_enabled,
            analyzer=analyzer,
            language=language,
        )
        if analyzer is not None and analyzer.calls:
            append_llm_usage_log(
                {
                    "date": (end or today_utc()).isoformat(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "items_analyzed": newsletter.get("item_count", 0),
                    "kind": "newsletter",
                    **analyzer.usage_summary(),
                }
            )
        return newsletter

    def get(self, date_from: str, date_to: str) -> dict[str, Any]:
        key = self._key(date_from, date_to)
        if key in self._results:
            return {"status": "done", "result": self._results[key]}
        return {"status": self._status.get(key, "idle"), "result": None}

    def editions(self) -> list[dict[str, Any]]:
        return sorted(
            self._results.values(),
            key=lambda nl: str(nl.get("date_to", "")),
            reverse=True,
        )

    def start(self, date_from: str, date_to: str, force: bool = False) -> str:
        """Returns "done"/"running" as before, or "busy" if too many
        newsletter editions (each a real Claude CLI subprocess) are already
        in flight."""
        key = self._key(date_from, date_to)
        with self._lock:
            if key in self._results and not force:
                return "done"
            if self._status.get(key) == "running":
                return "running"
            if self._active_count >= MAX_CONCURRENT_NEWSLETTERS:
                return "busy"
            self._status[key] = "running"
            self._active_count += 1
        threading.Thread(
            target=self._run, args=(key, date_from, date_to), daemon=True
        ).start()
        return "running"

    def _run(self, key: str, date_from: str, date_to: str) -> None:
        try:
            newsletter = self._generator(date_from, date_to)
            try:
                from src.storage.json_storage import save_newsletter

                save_newsletter(newsletter)
            except Exception:  # noqa: BLE001 - the edition still reaches the UI via _results
                logger.exception("Failed to persist newsletter edition %s.", key)
            self._results[key] = newsletter
            self._status[key] = "done"
        except Exception as exc:  # noqa: BLE001
            self._status[key] = "error"
            logger.exception("Newsletter generation failed for %s: %s", key, exc)
        finally:
            with self._lock:
                self._active_count -= 1


class ProfileExpansionManager:
    """Turn free-text about_me into a structured profile proposal.

    Unlike DeepDiveManager/NewsletterManager (many concurrent keys), there is
    only one profile per install, so this tracks a single in-flight job: a
    concurrency cap of 1 is the whole point (no benefit to running two
    expansions of the same text at once, and it keeps the LLM call count
    bounded the same way the other managers do).
    """

    def __init__(self, generator: Callable[[str], "dict[str, Any] | None"] | None = None) -> None:
        self._generator = generator or self._default_generator
        self._lock = threading.Lock()
        self._state = "idle"  # idle | running | done | error
        self._proposal: dict[str, Any] | None = None
        self._error = ""

    @staticmethod
    def _default_generator(about_me: str) -> dict[str, Any] | None:
        from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer
        from src.processors.profile_expander import expand_profile
        from src.settings import output_language

        return expand_profile(about_me, analyzer=ClaudeCliAnalyzer(), language=output_language())

    def snapshot(self) -> dict[str, Any]:
        return {"state": self._state, "proposal": self._proposal, "error": self._error}

    def start(self, about_me: str) -> str:
        with self._lock:
            if self._state == "running":
                return "busy"
            self._state = "running"
            self._proposal = None
            self._error = ""
        threading.Thread(target=self._run, args=(about_me,), daemon=True).start()
        return "running"

    def _run(self, about_me: str) -> None:
        try:
            proposal = self._generator(about_me)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Profile expansion failed: %s", exc)
            self._state = "error"
            self._error = "Espansione non riuscita. Riprova."
            return
        if proposal is None:
            self._state = "error"
            self._error = (
                "Non sono riuscito a interpretare il testo: prova a scrivere qualche riga in più."
            )
            return
        self._proposal = proposal
        self._state = "done"


_YOUTUBE_FEED_PATH_RE = re.compile(r"^/feeds/videos\.xml$")
_YOUTUBE_CHANNEL_PATH_RE = re.compile(r"^/channel/(UC[0-9A-Za-z_-]{10,})")
_YOUTUBE_HANDLE_PATH_RE = re.compile(r"^/(?:@|c/|user/)")
_EXTERNAL_ID_RE = re.compile(r'"externalId":"(UC[0-9A-Za-z_-]{10,})"')
_YOUTUBE_USER_AGENT = "Mozilla/5.0"
_YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")


def resolve_youtube_feed_url(url: str) -> tuple[str, str]:
    """Resolve a human-facing YouTube channel URL to its videos feed URL.

    Returns (resolved_url, error). Non-YouTube URLs and URLs that are
    already a feed pass through unchanged. ``/channel/UC...`` URLs resolve
    without any network call (the id is already in the URL); ``@handle``
    and the legacy ``/c/...``, ``/user/...`` forms require fetching the
    channel page to find its channel id.

    Host and path are checked via ``urlparse`` (not a raw substring search),
    so a URL like ``file:///etc/passwd#youtube.com/@a`` is not mistaken for
    a YouTube URL just because that text appears somewhere in it.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in _YOUTUBE_HOSTS:
        return url, ""
    if _YOUTUBE_FEED_PATH_RE.match(parsed.path) and "channel_id=" in parsed.query:
        return url, ""

    channel_match = _YOUTUBE_CHANNEL_PATH_RE.match(parsed.path)
    if channel_match:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_match.group(1)}", ""

    if not _YOUTUBE_HANDLE_PATH_RE.match(parsed.path):
        return url, ""

    import urllib.request

    from src.net_guard import MAX_RESPONSE_BYTES

    # Uses urllib instead of requests: requests' default session/redirect
    # handling reliably gets served YouTube's cookie-consent interstitial
    # page for this endpoint (verified directly, reproducible), while a
    # plain urllib GET with the same User-Agent gets the real channel page.
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _YOUTUBE_USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as response:
            # read(n) reads at most n bytes, so this can never buffer more
            # than the cap regardless of how large the real response is.
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return url, f"pagina del canale troppo grande (oltre {MAX_RESPONSE_BYTES} byte)"
            html = raw.decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001 - any fetch failure is a resolution error
        return url, f"impossibile raggiungere il canale YouTube: {exc}"

    match = _EXTERNAL_ID_RE.search(html)
    if not match:
        return url, "impossibile risolvere il canale YouTube (channel id non trovato)"
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={match.group(1)}", ""


def list_sources(path: str | Path = DEFAULT_SOURCES_PATH) -> list[dict[str, Any]]:
    """Return every configured source (enabled or not)."""
    from src.storage.json_storage import load_sources_config

    return list(load_sources_config(path).get("sources", []))


def add_source(payload: dict[str, Any], path: str | Path = DEFAULT_SOURCES_PATH) -> dict[str, Any]:
    """Validate and add one source. Returns {"ok": bool, "error": str}."""
    from src.storage.json_storage import STORAGE_LOCK, load_sources_config, save_sources_config

    from src.net_guard import is_safe_public_url

    source_id = str(payload.get("id", "")).strip()
    url = str(payload.get("url", "")).strip()
    name = str(payload.get("name", "")).strip() or source_id
    if not source_id or not url:
        return {"ok": False, "error": "id e url sono obbligatori"}
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url non valido"}
    safe, unsafe_reason = is_safe_public_url(url)
    if not safe:
        return {"ok": False, "error": f"url non sicuro: {unsafe_reason}"}

    with STORAGE_LOCK:
        doc = load_sources_config(path)
        sources = doc.get("sources", [])
        if any(str(s.get("id")) == source_id for s in sources):
            return {"ok": False, "error": f"esiste già una fonte con id '{source_id}'"}

        url, resolve_error = resolve_youtube_feed_url(url)
        if resolve_error:
            return {"ok": False, "error": resolve_error}
        # Re-check after resolution: the resolved feed URL is what will
        # actually be fetched on every future collection run.
        safe, unsafe_reason = is_safe_public_url(url)
        if not safe:
            return {"ok": False, "error": f"url risolto non sicuro: {unsafe_reason}"}

        domain = str(payload.get("allowed_domains", "")).strip() or urlparse(url).netloc.replace("www.", "")
        entry = {
            "id": source_id,
            "name": name,
            "company": str(payload.get("company", "")).strip() or name,
            "url": url,
            "allowed_domains": [d.strip() for d in str(domain).split(",") if d.strip()],
            "type": str(payload.get("type", "")).strip() or "rss",
            "category_hint": str(payload.get("category_hint", "")).strip() or "general_ai",
            "source_group": str(payload.get("source_group", "")).strip() or "product_data",
            "signal_type": str(payload.get("signal_type", "")).strip() or "official_news",
            "enabled": bool(payload.get("enabled", True)),
            "notes": str(payload.get("notes", "")).strip() or "Aggiunta dall'app.",
        }
        sources.append(entry)
        doc["sources"] = sources
        save_sources_config(doc, path)
        return {"ok": True, "error": "", "source": entry}


def update_source(
    source_id: str, updates: dict[str, Any], path: str | Path = DEFAULT_SOURCES_PATH
) -> dict[str, Any]:
    """Update fields on an existing source. Returns {"ok": bool, "error": str}."""
    from src.net_guard import is_safe_public_url
    from src.storage.json_storage import STORAGE_LOCK, load_sources_config, save_sources_config

    with STORAGE_LOCK:
        doc = load_sources_config(path)
        sources = doc.get("sources", [])
        index = next((i for i, s in enumerate(sources) if str(s.get("id")) == source_id), None)
        if index is None:
            return {"ok": False, "error": f"nessuna fonte con id '{source_id}'"}

        entry = dict(sources[index])
        if "url" in updates:
            new_url = str(updates["url"]).strip()
            if not new_url.startswith(("http://", "https://")):
                return {"ok": False, "error": "url non valido"}
            safe, unsafe_reason = is_safe_public_url(new_url)
            if not safe:
                return {"ok": False, "error": f"url non sicuro: {unsafe_reason}"}
            new_url, resolve_error = resolve_youtube_feed_url(new_url)
            if resolve_error:
                return {"ok": False, "error": resolve_error}
            # Re-check after resolution: the resolved feed URL is what will
            # actually be fetched on every future collection run.
            safe, unsafe_reason = is_safe_public_url(new_url)
            if not safe:
                return {"ok": False, "error": f"url risolto non sicuro: {unsafe_reason}"}
            entry["url"] = new_url
        for field in (
            "name", "company", "type", "category_hint", "source_group",
            "signal_type", "notes", "allowed_domains",
        ):
            if field in updates:
                entry[field] = updates[field]
        if "enabled" in updates:
            entry["enabled"] = bool(updates["enabled"])

        sources[index] = entry
        doc["sources"] = sources
        save_sources_config(doc, path)
        return {"ok": True, "error": "", "source": entry}


def remove_source(source_id: str, path: str | Path = DEFAULT_SOURCES_PATH) -> dict[str, Any]:
    """Remove a source by id. Returns {"ok": bool, "error": str}."""
    from src.storage.json_storage import STORAGE_LOCK, load_sources_config, save_sources_config

    with STORAGE_LOCK:
        doc = load_sources_config(path)
        sources = doc.get("sources", [])
        remaining = [s for s in sources if str(s.get("id")) != source_id]
        if len(remaining) == len(sources):
            return {"ok": False, "error": f"nessuna fonte con id '{source_id}'"}

        doc["sources"] = remaining
        save_sources_config(doc, path)
        return {"ok": True, "error": ""}


def _parse_iso_date(value: str) -> date_type | None:
    try:
        return date_type.fromisoformat(str(value).strip())
    except ValueError:
        return None


# The deep-dive item arrives as arbitrary JSON from the browser and its text
# ends up inside an LLM prompt. Accept only the fields the deep-dive path
# actually consumes (content_fetcher._fallback_text, deep_dive._build_prompt,
# feedback.record_deep_dive), each length-capped, and drop everything else.
_DEEP_DIVE_TEXT_FIELDS = {
    "url": 2000,
    "title": 500,
    "clean_title": 500,
    "summary": 4000,
    "source_name": 200,
    "source_id": 200,
    "company": 200,
    "primary_topic": 120,
    "secondary_topic": 120,
}
_DEEP_DIVE_LIST_FIELDS = {
    "key_points": (12, 500),
    "personal_match": (40, 80),
}


def _sanitize_deep_dive_item(item: Any) -> dict[str, Any]:
    """Return a whitelisted, length-capped copy of a browser-supplied item."""
    if not isinstance(item, dict):
        return {}
    clean: dict[str, Any] = {}
    for field, max_len in _DEEP_DIVE_TEXT_FIELDS.items():
        value = str(item.get(field, "")).strip()
        if value:
            clean[field] = value[:max_len]
    for field, (max_items, max_len) in _DEEP_DIVE_LIST_FIELDS.items():
        raw = item.get(field)
        if not isinstance(raw, list):
            continue
        values = [str(entry).strip()[:max_len] for entry in raw[:max_items] if str(entry).strip()]
        if values:
            clean[field] = values
    return clean


_SAVED_TEXT_FIELDS = {
    **_DEEP_DIVE_TEXT_FIELDS,
    "excerpt": 4000,
    "why_it_matters": 2000,
    "possible_action": 2000,
    "category": 120,
    "content_quality": 40,
    "signal_type": 60,
    "source_group": 60,
    "published_at": 40,
    "collected_at": 40,
}
_SAVED_NUMERIC_FIELDS = (
    "signal_score",
    "trend_score",
    "relevance_score",
    "personal_relevance_score",
)


def _sanitize_saved_item(item: Any) -> dict[str, Any]:
    """Return a whitelisted, length-capped copy of a browser-supplied item.

    The saved view and the feedback weighting both replay stored items
    through the same card renderer as a collected one, so the whitelist is
    the deep-dive one widened to the fields that card actually reads --
    nothing else needs to survive the round trip to disk.
    """
    if not isinstance(item, dict):
        return {}
    clean: dict[str, Any] = {}
    for field, max_len in _SAVED_TEXT_FIELDS.items():
        value = str(item.get(field, "")).strip()
        if value:
            clean[field] = value[:max_len]
    for field, (max_items, max_len) in _DEEP_DIVE_LIST_FIELDS.items():
        raw = item.get(field)
        if not isinstance(raw, list):
            continue
        values = [str(entry).strip()[:max_len] for entry in raw[:max_items] if str(entry).strip()]
        if values:
            clean[field] = values
    for field in _SAVED_NUMERIC_FIELDS:
        if field not in item:
            continue
        try:
            clean[field] = int(item[field])
        except (TypeError, ValueError):
            continue  # a non-numeric score is dropped, not stored as text
    return clean


def _llm_settings_snapshot() -> dict[str, Any]:
    """Return the current provider/model and language preferences plus each provider's
    install/auth state, for the dashboard's settings panel."""
    import shutil

    from src.processors.llm_health import claude_cli_health
    from src.processors.llm_provider import PROVIDERS
    from src.settings import llm_model, llm_provider, output_language, ui_language

    providers = []
    for spec in PROVIDERS.values():
        installed = shutil.which(spec.binary) is not None
        authed = bool(claude_cli_health(provider=spec.id).get("ok")) if installed else False
        providers.append(
            {
                "id": spec.id,
                "label": spec.label,
                "installed": installed,
                "login_kind": spec.login_kind,
                "authed": authed,
            }
        )
    return {
        "provider": llm_provider(),
        "model": llm_model(),
        "language": output_language(),
        "ui_language": ui_language(),
        "providers": providers,
    }


def _sanitize_llm_settings_edit(payload: Any) -> dict[str, Any]:
    """Whitelist and validate a browser-submitted /api/llm-settings body."""
    from src.processors.llm_provider import PROVIDERS
    from src.settings import SUPPORTED_LANGUAGES

    if not isinstance(payload, dict):
        return {}
    clean: dict[str, Any] = {}
    provider = str(payload.get("provider", "")).strip()
    if provider in PROVIDERS:
        clean["llm_provider"] = provider
    language = str(payload.get("language", "")).strip().lower()
    if language in SUPPORTED_LANGUAGES:
        clean["output_language"] = language
    interface_language = str(payload.get("ui_language", "")).strip().lower()
    if interface_language in SUPPORTED_LANGUAGES:
        clean["ui_language"] = interface_language
    if "model" in payload:
        model = str(payload.get("model", "")).strip()
        if re.fullmatch(r"[A-Za-z0-9._-]{0,64}", model):
            clean["llm_model"] = model
    return clean


_PROFILE_EDITABLE_LIST_FIELDS = ("interests", "skills_to_grow", "keywords_boost")


def _sanitize_profile_edit(payload: Any) -> dict[str, Any]:
    """Return a sanitized, whitelisted copy of a browser-submitted profile.

    Reuses profile_expander's term/project sanitizers (regex-safety, length
    caps, dedup) so a hand-typed keyword is held to the same standard as an
    LLM-proposed one before it ever reaches personal_relevance's matcher.
    Unlike the expansion path, an empty keywords_boost is allowed here (the
    user may be deliberately clearing fields), so no field is required.
    """
    from src.processors.profile_expander import (
        MAX_INTERESTS,
        MAX_KEYWORDS,
        MAX_PROJECTS,
        MAX_SKILLS,
        _sanitize_projects,
        _sanitize_terms,
    )

    field_caps = {
        "interests": MAX_INTERESTS,
        "skills_to_grow": MAX_SKILLS,
        "keywords_boost": MAX_KEYWORDS,
    }

    if not isinstance(payload, dict):
        return {}
    clean: dict[str, Any] = {}
    # Keyed on presence, not truthiness: the list fields below already work
    # this way, and gating on truthiness meant clearing about_me/goal from
    # the dashboard silently kept the old value (the response even
    # repopulated the field the user had just emptied).
    if "about_me" in payload:
        clean["about_me"] = str(payload.get("about_me", "")).strip()[:2000]
    if "goal" in payload:
        clean["goal"] = str(payload.get("goal", "")).strip()[:500]
    for field, cap in field_caps.items():
        if field in payload:
            clean[field] = _sanitize_terms(payload.get(field), cap)
    if "projects" in payload:
        clean["projects"] = _sanitize_projects(payload.get("projects"))[:MAX_PROJECTS]
    return clean


def _llm_enabled() -> bool:
    try:
        import yaml

        with Path("config/settings.yaml").open("r", encoding="utf-8") as handle:
            return bool((yaml.safe_load(handle) or {}).get("llm_analysis_enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _llm_budget_exceeded() -> bool:
    from src.processors.llm_budget import is_budget_exceeded

    return is_budget_exceeded()


def make_handler(
    runner: AnalysisRunner,
    dashboard_path: Path,
    deep_dives: "DeepDiveManager | None" = None,
    newsletters: "NewsletterManager | None" = None,
    profile_expansion: "ProfileExpansionManager | None" = None,
    insights: "InsightManager | None" = None,
) -> type[BaseHTTPRequestHandler]:
    if insights is None:
        insights = InsightManager()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes | str, ctype: str) -> None:
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # Refuse framing. The Host/Origin gate cannot see UI redress: a
            # click steered onto a hidden iframe of this page produces a
            # request with this server's own Host and Origin, which the gate
            # accepts. Only the browser can refuse to render the frame.
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _is_allowed_request_origin(self) -> bool:
            """Reject requests whose Host/Origin don't look like this local server.

            Defends against CSRF (a hostile page's <form>/fetch POSTing here)
            and DNS rebinding (a hostname that resolves to 127.0.0.1 only
            after the browser has already loaded a page from it — the Host
            header would still show that hostname, not 127.0.0.1/localhost).
            """
            host_header = self.headers.get("Host", "")
            host_only = host_header.rsplit(":", 1)[0].strip().lower().strip("[]")
            if host_only not in ("127.0.0.1", "localhost", "::1"):
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_host = (urlparse(origin).hostname or "").lower()
                if origin_host not in ("127.0.0.1", "localhost", "::1"):
                    return False
            return True

        def do_GET(self) -> None:  # noqa: N802 - required name
            # The gate covers every response, not just /api/. The read APIs
            # expose personal data (saved articles, feedback, the collected
            # news, the LLM status) -- but so does the dashboard page itself,
            # which embeds the items, the source catalogue and the usage
            # history as inline JSON. Gating only the APIs left the richer
            # target ungated: a rebound page could simply fetch '/'.
            if not self._is_allowed_request_origin():
                self._send(403, json.dumps({"error": "forbidden"}), "application/json")
                return
            if _serve_design_asset(self._send, urlparse(self.path).path):
                return
            if urlparse(self.path).path == "/api/status":
                # The app marker lets the launch scripts tell this server from
                # any other application answering on the same port.
                payload = {"app": APP_MARKER, **runner.snapshot()}
                self._send(200, json.dumps(payload), "application/json")
                return
            if urlparse(self.path).path == "/api/llm-status":
                from src.settings import llm_provider as _configured_llm_provider

                health = claude_cli_health(provider=_configured_llm_provider())
                stored = read_llm_status()
                payload = {**health, "last_ok_at": stored.get("last_ok_at")}
                # A healthy CLI can still be out of quota. Report that while the
                # last run (or a call in this process) hit the limit recently.
                from src.processors.claude_cli_analyzer import quota_block

                quota_detail = quota_block()
                if not quota_detail and stored.get("reason") == "quota":
                    try:
                        age = datetime.now(timezone.utc) - datetime.fromisoformat(
                            str(stored.get("checked_at", ""))
                        )
                        if age.total_seconds() < QUOTA_STATUS_MAX_AGE_SECONDS:
                            quota_detail = str(stored.get("detail", "")) or "quota esaurita"
                    except ValueError:
                        pass
                if health.get("ok") and quota_detail:
                    payload = {**payload, "ok": False, "reason": "quota", "detail": quota_detail}
                self._send(200, json.dumps(payload), "application/json")
                return
            if urlparse(self.path).path == "/api/transcript-status":
                status = load_transcript_status()
                resume_at = str(status.get("resume_at", ""))
                try:
                    blocked = datetime.fromisoformat(resume_at) > datetime.now(timezone.utc)
                except ValueError:
                    blocked = False
                payload = {"blocked": blocked, "resume_at": resume_at if blocked else ""}
                self._send(200, json.dumps(payload), "application/json")
                return
            if urlparse(self.path).path == "/api/llm-auth/status":
                self._send(200, json.dumps(get_llm_auth().snapshot()), "application/json")
                return
            if urlparse(self.path).path == "/api/deep-dive":
                if deep_dives is None:
                    self._send(404, json.dumps({"error": "not enabled"}), "application/json")
                    return
                query = parse_qs(urlparse(self.path).query)
                url = (query.get("url") or [""])[0]
                self._send(200, json.dumps(deep_dives.get(url)), "application/json")
                return
            if urlparse(self.path).path == "/api/newsletter":
                if newsletters is None:
                    self._send(404, json.dumps({"error": "not enabled"}), "application/json")
                    return
                query = parse_qs(urlparse(self.path).query)
                date_from = (query.get("from") or [""])[0]
                date_to = (query.get("to") or [""])[0]
                if not date_from or not date_to:
                    self._send(200, json.dumps({"editions": newsletters.editions()}), "application/json")
                    return
                self._send(200, json.dumps(newsletters.get(date_from, date_to)), "application/json")
                return
            if urlparse(self.path).path == "/api/source-news":
                from src.storage.json_storage import load_news_by_source

                query = parse_qs(urlparse(self.path).query)
                source_id = (query.get("source_id") or [""])[0]
                items = load_news_by_source(source_id)
                self._send(200, json.dumps({"items": items}), "application/json")
                return
            if urlparse(self.path).path == "/api/sources":
                self._send(200, json.dumps({"sources": list_sources()}), "application/json")
                return
            if urlparse(self.path).path == "/api/feedback":
                from src.storage.json_storage import load_feedback

                self._send(200, json.dumps({"feedback": load_feedback()}), "application/json")
                return
            if urlparse(self.path).path == "/api/saved":
                from src.storage.json_storage import load_deep_dives, load_saved_articles

                articles = load_saved_articles()
                dives = load_deep_dives()
                items = []
                for url, snapshot in articles.items():
                    enriched = dict(snapshot)
                    if url in dives:
                        enriched["deep_dive"] = dives[url]
                    items.append(enriched)
                self._send(200, json.dumps({"items": items}), "application/json")
                return
            if urlparse(self.path).path == "/api/profile":
                from src.processors.profile import load_profile

                profile = load_profile()
                has_profile = bool(
                    profile.get("about_me") or profile.get("goal") or profile.get("keywords_boost")
                )
                self._send(
                    200,
                    json.dumps({"profile": profile, "has_profile": has_profile}),
                    "application/json",
                )
                return
            if urlparse(self.path).path == "/api/profile/expand/status":
                if profile_expansion is None:
                    self._send(404, json.dumps({"error": "not enabled"}), "application/json")
                    return
                self._send(200, json.dumps(profile_expansion.snapshot()), "application/json")
                return
            if urlparse(self.path).path == "/api/insight":
                url = (parse_qs(urlparse(self.path).query).get("url") or [""])[0].strip()
                if not url:
                    self._send(400, json.dumps({"error": "missing url"}), "application/json")
                    return
                self._send(200, json.dumps(insights.get(url)), "application/json")
                return
            if urlparse(self.path).path == "/api/llm-settings":
                self._send(200, json.dumps(_llm_settings_snapshot()), "application/json")
                return
            if urlparse(self.path).path == "/api/model-catalog":
                from src.processors.model_catalog import read_catalog

                self._send(200, json.dumps(read_catalog()), "application/json")
                return
            path = Path(dashboard_path)
            if path.exists():
                self._send(200, path.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(
                    200,
                    "<!doctype html><meta charset='utf-8'><body style='font-family:sans-serif;padding:2rem'>"
                    "<h1>Dashboard non ancora generata</h1>"
                    "<p>Prima volta qui? Lancia <code>python -m src.main setup</code> da un "
                    "terminale: ti guida nella scelta dell'LLM, del profilo e delle fonti, poi "
                    "propone una prima raccolta.</p>"
                    "<p>In alternativa, lancia direttamente "
                    "<code>python -m src.main run-daily</code> e poi ricarica questa pagina.</p></body>",
                    "text/html; charset=utf-8",
                )

        def do_POST(self) -> None:  # noqa: N802 - required name
            if not self._is_allowed_request_origin():
                self._send(403, json.dumps({"error": "forbidden"}), "application/json")
                return
            if urlparse(self.path).path == "/api/run":
                source_id = str(self._read_json().get("source_id", "")).strip() or None
                started = runner.start(source_id)
                payload = {"started": started, **runner.snapshot()}
                self._send(202 if started else 409, json.dumps(payload), "application/json")
                return
            if urlparse(self.path).path == "/api/llm-auth/start":
                auth = get_llm_auth()
                started = auth.start()
                payload = {"started": started, **auth.snapshot()}
                self._send(202 if started else 409, json.dumps(payload), "application/json")
                return
            if urlparse(self.path).path == "/api/deep-dive":
                if deep_dives is None:
                    self._send(404, json.dumps({"error": "not enabled"}), "application/json")
                    return
                item = _sanitize_deep_dive_item(self._read_json().get("item"))
                url = str(item.get("url", "")).strip()
                if not url:
                    self._send(400, json.dumps({"error": "missing url"}), "application/json")
                    return
                if _llm_budget_exceeded():
                    self._send(
                        429,
                        json.dumps({"error": "budget giornaliero per l'AI esaurito, riprova domani"}),
                        "application/json",
                    )
                    return
                status = deep_dives.start(url, item)
                if status == "busy":
                    self._send(429, json.dumps({"error": "troppi approfondimenti in corso, riprova tra poco"}), "application/json")
                    return
                self._send(202, json.dumps({"status": status, **deep_dives.get(url)}), "application/json")
                return
            route = urlparse(self.path).path
            if route == "/api/insight":
                item = _sanitize_deep_dive_item(self._read_json().get("item"))
                url = str(item.get("url", "")).strip()
                if not url:
                    self._send(400, json.dumps({"error": "missing url"}), "application/json")
                    return
                if _llm_budget_exceeded():
                    self._send(
                        429,
                        json.dumps({"error": "budget giornaliero per l'AI esaurito, riprova domani"}),
                        "application/json",
                    )
                    return
                status = insights.start(url, item)
                if status == "busy":
                    self._send(
                        429,
                        json.dumps({"error": "troppe analisi in corso, riprova tra poco"}),
                        "application/json",
                    )
                    return
                self._send(202, json.dumps({"status": status, **insights.get(url)}), "application/json")
                return
            if route == "/api/newsletter":
                if newsletters is None:
                    self._send(404, json.dumps({"error": "not enabled"}), "application/json")
                    return
                body = self._read_json()
                date_from = str(body.get("date_from", "")).strip()
                date_to = str(body.get("date_to", "")).strip()
                if not date_from or not date_to:
                    self._send(400, json.dumps({"error": "missing dates"}), "application/json")
                    return
                if _parse_iso_date(date_from) is None or _parse_iso_date(date_to) is None:
                    self._send(400, json.dumps({"error": "invalid dates"}), "application/json")
                    return
                status = newsletters.start(date_from, date_to, bool(body.get("force")))
                if status == "busy":
                    self._send(429, json.dumps({"error": "troppe newsletter in generazione, riprova tra poco"}), "application/json")
                    return
                payload = {"status": status, **newsletters.get(date_from, date_to)}
                self._send(202, json.dumps(payload), "application/json")
                return
            if route == "/api/feedback":
                from src.processors.feedback import record_feedback
                from src.storage.json_storage import (
                    STORAGE_LOCK,
                    load_feedback,
                    load_preferences,
                    save_feedback,
                    save_preferences,
                )

                body = self._read_json()
                item = _sanitize_saved_item(body.get("item"))
                if not str(item.get("url", "")).strip():
                    self._send(400, json.dumps({"error": "missing url"}), "application/json")
                    return
                with STORAGE_LOCK:
                    feedback, preferences = record_feedback(
                        item, int(body.get("vote", 0)), load_feedback(), load_preferences()
                    )
                    save_feedback(feedback)
                    save_preferences(preferences)
                self._send(200, json.dumps({"ok": True, "feedback": feedback.get(str(item["url"]), {})}), "application/json")
                return
            if route == "/api/profile":
                from src.storage.json_storage import (
                    STORAGE_LOCK,
                    load_profile_config,
                    save_profile_config,
                )

                edit = _sanitize_profile_edit(self._read_json().get("profile"))
                with STORAGE_LOCK:
                    current = load_profile_config()
                    current.update(edit)
                    current["edited_fields"] = sorted(
                        set(current.get("edited_fields") or []) | set(edit.keys())
                    )
                    save_profile_config(current)
                self._send(200, json.dumps({"ok": True, "profile": current}), "application/json")
                return
            if route == "/api/profile/expand":
                if profile_expansion is None:
                    self._send(404, json.dumps({"error": "not enabled"}), "application/json")
                    return
                about_me = str(self._read_json().get("about_me", "")).strip()[:2000]
                if not about_me:
                    self._send(400, json.dumps({"error": "missing about_me"}), "application/json")
                    return
                status = profile_expansion.start(about_me)
                if status == "busy":
                    self._send(
                        429,
                        json.dumps({"error": "espansione già in corso, riprova tra poco"}),
                        "application/json",
                    )
                    return
                self._send(202, json.dumps({"status": status}), "application/json")
                return
            if route == "/api/llm-settings":
                from src.processors.llm_health import reset_health_cache
                from src.settings import save_settings_overrides

                edit = _sanitize_llm_settings_edit(self._read_json())
                if not edit:
                    self._send(400, json.dumps({"error": "invalid settings"}), "application/json")
                    return
                save_settings_overrides(edit)
                if "llm_provider" in edit:
                    reset_health_cache()
                self._send(200, json.dumps({"ok": True, **_llm_settings_snapshot()}), "application/json")
                return
            if route == "/api/model-catalog/refresh":
                from src.processors.model_catalog import refresh_catalog

                self._send(200, json.dumps({"ok": True, **refresh_catalog()}), "application/json")
                return
            if route == "/api/sources":
                result = add_source(self._read_json().get("source") or {})
                self._send(200 if result["ok"] else 400, json.dumps(result), "application/json")
                return
            if route == "/api/sources/update":
                body = self._read_json()
                source_id = str(body.get("source_id", "")).strip()
                if not source_id:
                    self._send(400, json.dumps({"ok": False, "error": "source_id mancante"}), "application/json")
                    return
                result = update_source(source_id, body.get("updates") or {})
                self._send(200 if result["ok"] else 400, json.dumps(result), "application/json")
                return
            if route == "/api/sources/remove":
                source_id = str(self._read_json().get("source_id", "")).strip()
                if not source_id:
                    self._send(400, json.dumps({"ok": False, "error": "source_id mancante"}), "application/json")
                    return
                result = remove_source(source_id)
                self._send(200 if result["ok"] else 400, json.dumps(result), "application/json")
                return
            if route == "/api/save":
                from src.storage.json_storage import add_saved_article

                item = _sanitize_saved_item(self._read_json().get("item"))
                if not str(item.get("url", "")).strip():
                    self._send(400, json.dumps({"error": "missing url"}), "application/json")
                    return
                add_saved_article(item)
                self._send(200, json.dumps({"ok": True}), "application/json")
                return
            if route == "/api/unsave":
                from src.storage.json_storage import remove_saved_article

                url = str(self._read_json().get("url", "")).strip()
                remove_saved_article(url)
                self._send(200, json.dumps({"ok": True}), "application/json")
                return
            self._send(404, json.dumps({"error": "not found"}), "application/json")

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length < 0 or length > MAX_REQUEST_BODY_BYTES:
                    # A negative length would make rfile.read(length) block
                    # forever waiting for a close; an oversized one could
                    # exhaust memory. Neither is a body we can legitimately
                    # need for this app's small JSON payloads.
                    logger.warning("Rejecting request with invalid Content-Length: %s", length)
                    return {}
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw or b"{}")
                return data if isinstance(data, dict) else {}
            except (ValueError, json.JSONDecodeError):
                return {}

        def log_message(self, *args: Any) -> None:  # silence default logging
            return

    return Handler


def refresh_dashboard_shell(
    dashboard_path: str | Path,
    data_dir: str | Path | None = None,
    date: date_type | None = None,
) -> bool:
    """Refresh UI code from the currently saved dashboard data.

    A source update should not force a collection or an LLM run just to show
    a new menu or setting. The last generated page already embeds its safe,
    normalized item list, so rebuild the HTML shell from it when the local
    server starts. Failures leave the existing page untouched.

    The AI-written brief is reloaded from disk and passed back in. Without
    that, every restart rebuilt the page without it: the daily summary fell
    back to the deterministic one -- which recomputes a similar headline, so
    the loss was invisible -- and the section briefs became generic labels.
    """
    path = Path(dashboard_path)
    if not path.exists():
        return False
    try:
        source = path.read_text(encoding="utf-8")
        match = re.search(r'<script id="news-data" type="application/json">(.*?)</script>', source, re.S)
        if not match:
            return False
        items = json.loads(match.group(1))
        if not isinstance(items, list):
            return False
        from src.outputs.dashboard_output import DEFAULT_DATA_DIR, DashboardOutput

        output = DashboardOutput(
            data_dir=data_dir if data_dir is not None else DEFAULT_DATA_DIR,
            output_dir=path.parent,
            date=date,
        )
        # generate() reloads the stored summary and section briefs itself.
        output.generate(items)
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning("Could not refresh the dashboard interface at startup.", exc_info=True)
        return False


def find_free_port(host: str, start: int, attempts: int = PORT_FALLBACK_ATTEMPTS) -> int:
    """Return ``start`` if it is free, else the next free port after it.

    Port 8000 is one of the busiest on a developer machine, and the people
    this app is built for start it by double-clicking a script: an
    ``Address already in use`` traceback is not an answer they can act on.
    """
    import socket

    for candidate in range(start, start + attempts):
        probe = socket.socket()
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, candidate))
        except OSError:
            continue
        finally:
            probe.close()
        return candidate
    raise OSError(
        f"Nessuna porta libera tra {start} e {start + attempts - 1}. "
        f"Chiudi qualche applicazione o indica una porta con --port."
    )


def shutdown_server() -> None:
    """Stop the server started by ``run_server`` (used by the tests)."""
    if _running_server is not None:
        _running_server.shutdown()


def run_server(
    port: int = 8000,
    host: str = "127.0.0.1",
    dashboard_path: str | Path = DEFAULT_DASHBOARD_PATH,
    *,
    allow_fallback: bool = True,
    on_ready: "Callable[[str], None] | None" = None,
) -> None:
    """Start the local server (blocking) until interrupted.

    When ``allow_fallback`` is set and the port is taken, move to the next
    free one rather than crashing. Someone who passed ``--port`` meant that
    port, so the CLI clears the flag and the bind failure surfaces.
    """
    global _running_server

    refresh_dashboard_shell(dashboard_path)
    runner = AnalysisRunner()
    deep_dives = DeepDiveManager()
    newsletters = NewsletterManager()
    profile_expansion = ProfileExpansionManager()
    handler = make_handler(runner, Path(dashboard_path), deep_dives, newsletters, profile_expansion)

    chosen = find_free_port(host, port) if allow_fallback else port
    httpd = ThreadingHTTPServer((host, chosen), handler)
    _running_server = httpd
    url = f"http://{host}:{chosen}"
    if chosen != port:
        print(f"La porta {port} è occupata da un'altra applicazione: uso la {chosen}.")
    print(f"Signal Desk attivo su {url}  (Ctrl+C per fermare)")
    if on_ready is not None:
        on_ready(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArresto del server.")
    finally:
        httpd.server_close()
        _running_server = None
