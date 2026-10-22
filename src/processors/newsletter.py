"""Newsletter generation over an arbitrary timeframe.

Unlike the daily dashboard, a newsletter covers a period the user chooses.
It reuses the same section grouping as the dashboard and the same
"one structured Claude call for all briefs" pattern as section_briefs.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date as date_type
from datetime import datetime, timezone
from typing import Any

from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer, is_available
from src.processors.intelligence import _parse_date
from src.processors.llm_budget import is_budget_exceeded
from src.processors.sections import group_by_section, personal_score
from src.processors.prompt_lang import language_directive
from src.processors.prompt_safety import make_nonce, untrusted_block, untrusted_rule

logger = logging.getLogger(__name__)

# Newsletter sections: key -> (label, description used in the prompt).
NEWSLETTER_SECTIONS: tuple[tuple[str, str], ...] = (
    ("big", "Le big dell'AI"),
    ("perte", "Product & Data"),
    ("tools", "Da provare"),
)
ITEMS_PER_SECTION = 6
PROMPT_ITEMS_PER_SECTION = 10

NEWSLETTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "big": {"type": "string"},
        "perte": {"type": "string"},
        "tools": {"type": "string"},
    },
    "required": ["intro"],
}


def generate_newsletter(
    items: list[dict[str, Any]],
    *,
    date_from: str,
    date_to: str,
    goal: str = "",
    llm_enabled: bool = False,
    analyzer: ClaudeCliAnalyzer | None = None,
    language: str = "it",
) -> dict[str, Any]:
    """Build a newsletter edition for the given timeframe."""
    selected = filter_by_timeframe(items, date_from, date_to)
    grouped = group_by_section(selected)

    texts = None
    # Same budget guard as section_briefs: analyzer=None from a caller that
    # hit the daily cap must not be silently replaced by a fresh one here.
    if llm_enabled and (analyzer is not None or (is_available() and not is_budget_exceeded())):
        texts = _generate_texts(grouped, date_from, date_to, goal, analyzer, language)
    if texts is None:
        texts = _fallback_texts(grouped, selected)

    sections = []
    for key, label in NEWSLETTER_SECTIONS:
        section_items = _top_items(grouped.get(key, []), key)
        sections.append(
            {
                "key": key,
                "label": label,
                "brief": texts.get(key, ""),
                "items": [_item_summary(item) for item in section_items],
            }
        )

    return {
        "title": f"AI Signal Desk — {_format_period(date_from, date_to)}",
        "date_from": date_from,
        "date_to": date_to,
        "period": _format_period(date_from, date_to),
        "intro": texts.get("intro", ""),
        "sections": sections,
        "item_count": len(selected),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def filter_by_timeframe(
    items: list[dict[str, Any]], date_from: str, date_to: str
) -> list[dict[str, Any]]:
    """Return items published (or collected) within [date_from, date_to]."""
    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if start is None or end is None:
        return list(items)
    if start > end:
        start, end = end, start
    selected: list[dict[str, Any]] = []
    for item in items:
        item_date = _item_date(item)
        if item_date is not None and start <= item_date <= end:
            selected.append(item)
    return selected


def _item_date(item: dict[str, Any]) -> date_type | None:
    for key in ("published_at", "collected_at"):
        parsed = _parse_date(str(item.get(key) or ""))
        if parsed is not None:
            return parsed
    return None


def _top_items(section_items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    if key == "perte":
        ranked = sorted(section_items, key=personal_score, reverse=True)
    elif key == "tools":
        # Same ranking field as the dashboard's "tools" tab (sections.py's
        # group_by_section already pre-sorts this way; signal_score would
        # silently discard that ordering and rank differently here).
        ranked = sorted(
            section_items,
            key=lambda item: int(item.get("trend_score", 0) or 0),
            reverse=True,
        )
    else:
        ranked = sorted(
            section_items,
            key=lambda item: int(item.get("signal_score", 0) or 0),
            reverse=True,
        )
    return ranked[:ITEMS_PER_SECTION]


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("clean_title") or item.get("title") or "").strip(),
        "url": str(item.get("url", "")).strip(),
        "source": str(item.get("source_name") or item.get("company") or "").strip(),
        "published_at": str(item.get("published_at") or item.get("collected_at") or "").strip(),
        "summary": str(item.get("summary", "")).strip(),
        "why_it_matters": str(item.get("why_it_matters", "")).strip(),
    }


def _generate_texts(
    grouped: dict[str, list[dict[str, Any]]],
    date_from: str,
    date_to: str,
    goal: str,
    analyzer: ClaudeCliAnalyzer | None,
    language: str = "it",
) -> dict[str, str] | None:
    if not any(grouped.get(key) for key, _ in NEWSLETTER_SECTIONS):
        return None
    runner = analyzer or ClaudeCliAnalyzer(goal=goal, language=language)
    payload = runner.run_structured(
        _build_prompt(grouped, date_from, date_to, goal, language=language), NEWSLETTER_SCHEMA
    )
    if not isinstance(payload, dict):
        return None
    texts = {"intro": str(payload.get("intro", "")).strip()}
    for key, _ in NEWSLETTER_SECTIONS:
        texts[key] = str(payload.get(key, "")).strip()
    return texts


def _build_prompt(
    grouped: dict[str, list[dict[str, Any]]],
    date_from: str,
    date_to: str,
    goal: str,
    *,
    language: str = "it",
) -> str:
    nonce = make_nonce()
    blocks: list[str] = []
    for key, label in NEWSLETTER_SECTIONS:
        section_items = grouped.get(key, [])[:PROMPT_ITEMS_PER_SECTION]
        lines = "\n".join(
            f"- {str(item.get('clean_title') or item.get('title') or '').strip()}"
            f" ({str(item.get('source_name') or item.get('company') or '').strip()})"
            for item in section_items
        ) or "- (nessuna notizia)"
        blocks.append(f"## Sezione '{key}' — {label}\n" + untrusted_block(lines, nonce))
    goal_line = (
        f"Profilo del lettore: {goal}. Tienine conto.\n" if goal else ""
    )
    return (
        "Sei l'autore di una newsletter su AI e tecnologia. "
        f"Periodo coperto: {_format_period(date_from, date_to)}.\n"
        f"{untrusted_rule(nonce)}"
        f"{goal_line}"
        f"Scrivi {language_directive(language)}:\n"
        "- intro: 3-4 frasi che aprono l'edizione raccontando il filo conduttore "
        "del periodo (cosa è successo davvero, cosa se ne ricava). Niente elenco "
        "di titoli, niente marketing.\n"
        "- una sintesi di 2-3 frasi per ciascuna sezione (chiavi: "
        f"{', '.join(key for key, _ in NEWSLETTER_SECTIONS)}), che vada al succo e "
        "dia spunti per approfondire.\n"
        "Se una sezione non ha notizie, scrivi una frase neutra.\n\n"
        + "\n\n".join(blocks)
    )


def _fallback_texts(
    grouped: dict[str, list[dict[str, Any]]], selected: list[dict[str, Any]]
) -> dict[str, str]:
    """Deterministic Italian texts when the LLM is unavailable."""
    texts: dict[str, str] = {}
    if selected:
        topics = Counter(
            str(item.get("primary_topic") or "").strip()
            for item in selected
            if item.get("primary_topic")
        )
        top = ", ".join(topic for topic, _ in topics.most_common(3) if topic)
        texts["intro"] = (
            f"{len(selected)} notizie nel periodo."
            + (f" Temi principali: {top}." if top else "")
        )
    else:
        texts["intro"] = "Nessuna notizia nel periodo selezionato."
    for key, _ in NEWSLETTER_SECTIONS:
        section_items = grouped.get(key, [])
        if not section_items:
            texts[key] = "Nessuna notizia rilevante in questa sezione nel periodo."
            continue
        lead = str(
            section_items[0].get("clean_title") or section_items[0].get("title") or ""
        ).strip()
        texts[key] = f"{len(section_items)} notizie." + (f" In primo piano: {lead}." if lead else "")
    return texts


def _format_period(date_from: str, date_to: str) -> str:
    return f"{date_from} → {date_to}"
