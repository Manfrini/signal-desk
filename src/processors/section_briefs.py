"""Per-section daily briefs in Italian.

Generates a short paragraph for each dashboard section summarizing the most
important things from that section's items. Uses a single Claude CLI call for
all sections when LLM analysis is enabled; otherwise a deterministic fallback.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer, is_available
from src.processors.llm_budget import is_budget_exceeded
from src.processors.sections import SECTION_KEYS, SECTION_LABELS, group_by_section
from src.processors.prompt_lang import language_directive
from src.processors.prompt_safety import make_nonce, untrusted_block, untrusted_rule

logger = logging.getLogger(__name__)

ITEMS_PER_SECTION = 10

BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in SECTION_KEYS},
}


def generate_section_briefs(
    items: list[dict[str, Any]],
    *,
    goal: str = "",
    llm_enabled: bool = False,
    analyzer: ClaudeCliAnalyzer | None = None,
    language: str = "it",
) -> dict[str, str]:
    """Return a short brief per section (in ``language``, default Italian).

    Falls back to a deterministic summary if the LLM is disabled/unavailable
    or the call fails.
    """
    grouped = group_by_section(items)
    # The `is_available()` arm covers callers that pass no analyzer at all
    # and expect one to be built here. It must ALSO respect the daily budget:
    # a caller that passed analyzer=None *because* the cap was hit would
    # otherwise have one built for it below, spending past the cap (and
    # invisibly, since the caller's usage-log append is gated on the
    # analyzer it thinks doesn't exist).
    if llm_enabled and (analyzer is not None or (is_available() and not is_budget_exceeded())):
        briefs = _generate_with_llm(grouped, goal, analyzer, language)
        if briefs:
            return briefs
    return {key: _fallback_brief(grouped.get(key, [])) for key in SECTION_KEYS}


def _generate_with_llm(
    grouped: dict[str, list[dict[str, Any]]],
    goal: str,
    analyzer: ClaudeCliAnalyzer | None,
    language: str = "it",
) -> dict[str, str] | None:
    if not any(grouped.get(key) for key in SECTION_KEYS):
        return None
    prompt = _build_prompt(grouped, goal, language=language)
    runner = analyzer or ClaudeCliAnalyzer(goal=goal, language=language)
    payload = runner.run_structured(prompt, BRIEF_SCHEMA)
    if not isinstance(payload, dict):
        return None
    briefs: dict[str, str] = {}
    for key in SECTION_KEYS:
        value = str(payload.get(key, "")).strip()
        briefs[key] = value or _fallback_brief(grouped.get(key, []))
    return briefs


def _build_prompt(
    grouped: dict[str, list[dict[str, Any]]], goal: str, *, language: str = "it"
) -> str:
    nonce = make_nonce()
    blocks: list[str] = []
    for key in SECTION_KEYS:
        section_items = grouped.get(key, [])[:ITEMS_PER_SECTION]
        titles = "\n".join(
            f"- {str(item.get('clean_title') or item.get('title') or '').strip()}"
            f" ({str(item.get('source_name') or item.get('company') or '').strip()})"
            for item in section_items
        ) or "- (nessuna notizia)"
        blocks.append(
            f"## Sezione '{key}' — {SECTION_LABELS[key]}\n"
            + untrusted_block(titles, nonce)
        )
    joined = "\n\n".join(blocks)
    goal_line = (
        f"Profilo dell'utente: {goal}. Tienine conto nei brief.\n\n"
        if goal
        else ""
    )
    return (
        "Sei un analista di intelligence su AI e tecnologia. "
        f"{untrusted_rule(nonce)}"
        f"{goal_line}"
        f"Per ciascuna sezione qui sotto scrivi un BRIEF {language_directive(language)} "
        "di 3-4 frasi "
        "che riassume le informazioni più importanti e cosa ne emerge. Niente "
        "marketing, niente elenco di titoli: una sintesi utile. Se una sezione "
        "non ha notizie, scrivi una frase neutra.\n"
        "Restituisci un oggetto con una chiave per sezione "
        f"({', '.join(SECTION_KEYS)}).\n\n"
        f"{joined}"
    )


def _fallback_brief(section_items: list[dict[str, Any]]) -> str:
    """Deterministic Italian brief used when the LLM is not available."""
    if not section_items:
        return "Nessuna notizia rilevante in questa sezione oggi."
    topics = Counter(
        str(item.get("primary_topic") or "").strip()
        for item in section_items
        if item.get("primary_topic")
    )
    top_topics = [topic for topic, _ in topics.most_common(2) if topic]
    lead = str(section_items[0].get("clean_title") or section_items[0].get("title") or "").strip()
    parts = [f"{len(section_items)} notizie in evidenza."]
    if top_topics:
        parts.append(f"Temi principali: {', '.join(top_topics)}.")
    if lead:
        parts.append(f"In primo piano: {lead}.")
    return " ".join(parts)
