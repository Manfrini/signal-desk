"""Declarative table of supported LLM CLIs.

``claude`` and ``codex`` are wired end to end and verified by running the
real binaries (see the handover plan, Fase 5). ``gemini`` is deliberately
NOT in this table yet: on the machine this was verified from, the CLI's
individual OAuth tier has been discontinued ("IneligibleTierError...
migrate to the Antigravity suite") and setting ``GEMINI_API_KEY`` does not
bypass that check, so neither its JSON output shape nor its tool-lockdown
equivalent to ``--allowedTools ""`` could be confirmed. Add it once someone
with a working credential can verify both.

Deliberately NOT responsible for the actual ``subprocess.run`` call: that
stays in ``claude_cli_analyzer.py`` so existing tests that mock
``claude_cli_analyzer.subprocess.run`` keep working untouched. This module
only builds the argv, transforms schemas when a provider needs that, and
normalizes a ``CompletedProcess`` into the envelope string the rest of the
codebase already expects (the shape produced by ``claude --output-format
json``).
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from typing import Any

# Structured-output strategies a provider's CLI can use.
STRUCTURED_INLINE_FLAG = "inline_flag"   # claude: --json-schema '<json>'
STRUCTURED_SCHEMA_FILE = "schema_file"   # codex:  --output-schema <file>
STRUCTURED_PROMPT_ONLY = "prompt_only"   # a future provider with no schema flag

DEFAULT_PROVIDER_ID = "claude"


class ProviderInvocationError(RuntimeError):
    """Raised when a provider CLI exits non-zero or its output can't be read."""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    binary: str
    default_model: str
    structured_mode: str
    login_kind: str  # "pty_url" | "api_key" | "none"
    login_cmd: tuple[str, ...] | None
    auth_status_cmd: tuple[str, ...] | None
    install_hint: str  # shown by the setup wizard when the binary is missing


PROVIDERS: dict[str, ProviderSpec] = {
    "claude": ProviderSpec(
        id="claude",
        label="Claude Code",
        binary="claude",
        default_model="sonnet",
        structured_mode=STRUCTURED_INLINE_FLAG,
        login_kind="pty_url",
        login_cmd=("claude", "auth", "login", "--claudeai"),
        auth_status_cmd=("claude", "auth", "status", "--json"),
        install_hint="Installa Claude Code: https://claude.com/claude-code",
    ),
    "codex": ProviderSpec(
        id="codex",
        label="Codex (OpenAI)",
        binary="codex",
        default_model="",  # "" -> let the CLI use its own configured default
        structured_mode=STRUCTURED_SCHEMA_FILE,
        login_kind="pty_url",
        # Device authorization does not depend on a temporary localhost
        # callback listener surviving while the browser completes OAuth.
        login_cmd=("codex", "login", "--device-auth"),
        auth_status_cmd=("codex", "login", "status"),
        install_hint=(
            "Installa Codex CLI: npm install -g @openai/codex "
            "(richiede un account ChatGPT o una API key OpenAI)"
        ),
    ),
}


def get_provider(provider_id: str = "") -> ProviderSpec:
    """Return the spec for ``provider_id``, falling back to the default provider."""
    return PROVIDERS.get(provider_id or DEFAULT_PROVIDER_ID, PROVIDERS[DEFAULT_PROVIDER_ID])


def make_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``schema`` transformed for OpenAI's strict mode.

    Verified live against ``codex exec --output-schema``: every object node
    must set ``"additionalProperties": false`` AND list every one of its
    properties in ``required`` (not just the ones that are conceptually
    mandatory). A property that was optional in the source schema is instead
    made nullable (``"type"`` widened to include ``"null"``) so the model can
    omit it semantically by returning ``null``. Recurses into ``properties``,
    array ``items``, and ``$defs``/``definitions`` if present.
    """
    return _strictify(schema)


def _strictify(node: Any) -> Any:
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    if not isinstance(node, dict):
        return node

    out = {key: _strictify(value) for key, value in node.items()}

    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        original_required = set(out.get("required") or [])
        properties = out["properties"]
        for name, prop_schema in properties.items():
            if name in original_required or not isinstance(prop_schema, dict):
                continue
            prop_schema = dict(prop_schema)
            prop_type = prop_schema.get("type")
            if isinstance(prop_type, str) and prop_type != "null":
                prop_schema["type"] = [prop_type, "null"]
            elif isinstance(prop_type, list) and "null" not in prop_type:
                prop_schema["type"] = [*prop_type, "null"]
            properties[name] = prop_schema
        out["required"] = list(properties.keys())
        out["additionalProperties"] = False

    return out


def build_command(
    spec: ProviderSpec,
    prompt: str,
    schema: dict,
    model: str = "",
    *,
    binary: str = "",
    schema_path: str = "",
    cwd_dir: str = "",
) -> list[str]:
    """Return the argv to invoke ``spec`` headless with structured output."""
    resolved_binary = binary or spec.binary
    resolved_model = model or spec.default_model

    if spec.structured_mode == STRUCTURED_INLINE_FLAG:
        return [
            resolved_binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--model",
            resolved_model,
            "--no-session-persistence",
            "--disable-slash-commands",
            # Don't load the user's global MCP servers: they add minutes of
            # startup time (and can hang) for a task that needs no tools.
            "--strict-mcp-config",
            # Pure text analysis: forbid all tools so the CLI answers in a
            # single turn instead of behaving as an agent (exploring the
            # filesystem with Bash/Read, costing many turns and seconds).
            "--allowedTools",
            "",
        ]

    if spec.structured_mode == STRUCTURED_SCHEMA_FILE:
        if not schema_path:
            raise ValueError("schema_path is required for the schema_file structured mode")
        command = [
            resolved_binary,
            "exec",
            "--json",
            # Isolation, verified live: none of these require an OpenAI
            # account beyond normal auth, and together they stop codex from
            # touching the real filesystem or loading the user's own
            # skills/MCP config -- the equivalent of claude's
            # --allowedTools "" for a CLI that has no such flag.
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "-s",
            "read-only",
            "--output-schema",
            schema_path,
        ]
        if cwd_dir:
            command += ["-C", cwd_dir]
        if resolved_model:
            command += ["-m", resolved_model]
        command += ["--", prompt]
        return command

    raise NotImplementedError(f"structured_mode {spec.structured_mode!r} has no provider yet")


def normalize_output(spec: ProviderSpec, completed: "subprocess.CompletedProcess[str]") -> str:
    """Return the CLI wrapper JSON as a string, raising on a failed call.

    For ``inline_flag`` providers (claude today) stdout already *is* that
    envelope, so this is a pass-through plus the shared non-zero-exit check.
    For ``schema_file`` providers (codex), stdout is JSONL and gets
    synthesized into the same envelope shape.
    """
    if spec.structured_mode == STRUCTURED_INLINE_FLAG:
        if completed.returncode != 0:
            raise ProviderInvocationError(
                f"{spec.label} CLI exited with {completed.returncode}: "
                f"{completed.stderr.strip()[:500]}"
            )
        return completed.stdout

    if spec.structured_mode == STRUCTURED_SCHEMA_FILE:
        return _normalize_jsonl_output(spec, completed)

    raise NotImplementedError(f"structured_mode {spec.structured_mode!r} has no provider yet")


def _normalize_jsonl_output(
    spec: ProviderSpec, completed: "subprocess.CompletedProcess[str]"
) -> str:
    text: str | None = None
    usage: dict[str, Any] = {}
    costs: list[float | None] = []
    failed_message: str | None = None

    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
        elif event_type == "turn.completed":
            candidate = event.get("usage")
            if isinstance(candidate, dict):
                for key in (
                    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                    "output_tokens", "reasoning_output_tokens",
                ):
                    usage[key] = usage.get(key, 0) + int(candidate.get(key) or 0)
            costs.append(reported_cost(event.get("total_cost_usd")))
        elif event_type in ("turn.failed", "error"):
            # A turn.failed can arrive with the process still exiting 0 in
            # some codex versions (verified: it did not here, but the
            # upstream research flagged this as version-dependent) -- treat
            # it as an error regardless of exit code, never trust exit code
            # alone for this provider.
            error = event.get("error")
            failed_message = (
                error.get("message") if isinstance(error, dict) else event.get("message")
            ) or "codex turn failed"

    if failed_message:
        raise ProviderInvocationError(f"{spec.label} CLI: {str(failed_message)[:500]}")
    if completed.returncode != 0:
        raise ProviderInvocationError(
            f"{spec.label} CLI exited with {completed.returncode}: "
            f"{completed.stderr.strip()[:500]}"
        )
    if text is None:
        raise ProviderInvocationError(f"{spec.label} CLI produced no answer")

    envelope = {
        "type": "result",
        "is_error": False,
        "result": text,
        "structured_output": None,
        "total_cost_usd": (
            sum(cost for cost in costs if cost is not None)
            if costs and all(cost is not None for cost in costs) else None
        ),
        "usage": {
            # Codex input/output totals already include cached/reasoning
            # tokens. Keep the breakdown without adding it a second time.
            "input_tokens": int(usage.get("input_tokens") or 0),
            "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
            "cache_write_input_tokens": int(usage.get("cache_write_input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_output_tokens": int(usage.get("reasoning_output_tokens") or 0),
        },
    }
    return json.dumps(envelope)


def reported_cost(value: Any) -> float | None:
    """Accept only a finite, non-negative monetary amount from a provider."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None
