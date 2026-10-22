"""On-demand deep dive for a single news item.

Reads the full source (article text or YouTube transcript) and asks Claude for
an extended Italian summary: overview, bullet points, and key insights.
"""

from __future__ import annotations

import logging
from typing import Any

from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer, is_available
from src.processors.prompt_lang import language_directive
from src.processors.prompt_safety import make_nonce, untrusted_block, untrusted_rule
from src.processors.content_fetcher import (
    REASON_FETCH_ERROR,
    REASON_NO_CAPTIONS,
    fetch_full_content_with_reason,
)

# User-facing (Italian) note shown in the deep-dive panel per partial reason.
_PARTIAL_REASON_NOTES = {
    REASON_NO_CAPTIONS: (
        "Questo video non ha sottotitoli/trascrizione disponibili: "
        "approfondimento basato solo su titolo e riassunto."
    ),
    REASON_FETCH_ERROR: (
        "Non è stato possibile scaricare il contenuto completo della fonte "
        "(spesso un blocco temporaneo lato fonte, non un problema del video/articolo): "
        "approfondimento basato solo su titolo e riassunto."
    ),
}
_DEFAULT_PARTIAL_NOTE = (
    "Fonte completa non accessibile: approfondimento basato sul contenuto disponibile."
)

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 8000

DEEP_DIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["title", "detail"],
            },
        },
        "insights": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "points", "insights"],
}


def generate_deep_dive(
    item: dict[str, Any],
    *,
    goal: str = "",
    analyzer: ClaudeCliAnalyzer | None = None,
    language: str = "it",
) -> dict[str, Any]:
    """Return a deep dive dict for one item.

    Always includes ``content_complete`` (False when only the excerpt/summary
    was available, e.g. paywall or captionless video) and, when partial,
    ``partial_reason`` (machine code) so the UI can explain *why* rather than
    showing a generic message for every case. Also includes ``source_text``,
    the raw fetched content (full article text or YouTube transcript), so it
    is persisted alongside the analysis and doesn't need to be re-fetched.
    """
    text, complete, reason = fetch_full_content_with_reason(item)
    if not text:
        return _error_dive("Non è stato possibile recuperare alcun contenuto dalla fonte.")

    runner = analyzer or (ClaudeCliAnalyzer(goal=goal) if is_available() else None)
    if runner is None:
        return _error_dive(
            "Analisi non disponibile: il CLI Claude non è raggiungibile.", complete, reason, text
        )

    prompt = _build_prompt(item, text[:MAX_CONTENT_CHARS], goal, complete, language=language)
    payload = runner.run_structured(prompt, DEEP_DIVE_SCHEMA)
    if not isinstance(payload, dict):
        logger.warning(
            "Deep dive received no structured LLM result (provider=%s, model=%s).",
            getattr(runner, "provider", "unknown"),
            getattr(runner, "model", "default") or "default",
        )
        return _error_dive(
            "L'AI non ha restituito un risultato utilizzabile. "
            "Controlla il modello selezionato e riprova.",
            complete,
            reason,
            text,
        )

    points = [
        {"title": str(p.get("title", "")).strip(), "detail": str(p.get("detail", "")).strip()}
        for p in payload.get("points", [])
        if isinstance(p, dict) and str(p.get("detail", "")).strip()
    ]
    insights = [str(i).strip() for i in payload.get("insights", []) if str(i).strip()]
    result = {
        "overview": str(payload.get("overview", "")).strip(),
        "points": points,
        "insights": insights,
        "content_complete": complete,
        "source_text": text,
    }
    if not complete:
        result["partial_note"] = _PARTIAL_REASON_NOTES.get(reason, _DEFAULT_PARTIAL_NOTE)
    return result


def _build_prompt(
    item: dict[str, Any], text: str, goal: str, complete: bool, *, language: str = "it"
) -> str:
    title = str(item.get("clean_title") or item.get("title") or "").strip()
    source = str(item.get("source_name") or item.get("company") or "").strip()
    goal_line = (
        f"Profilo dell'utente: {goal}. Evidenzia ciò che è utile a questo profilo.\n"
        if goal
        else ""
    )
    partial_line = (
        "ATTENZIONE: non è stato possibile leggere la fonte completa (paywall o "
        "contenuto non accessibile). Dichiaralo nell'overview e non inventare dettagli.\n"
        if not complete
        else ""
    )
    nonce = make_nonce()
    return (
        f"Sei un analista. Approfondisci la notizia seguente {language_directive(language)}, "
        "basandoti SOLO sul contenuto fornito (non inventare).\n"
        f"{untrusted_rule(nonce)}"
        f"{goal_line}{partial_line}"
        "Produci:\n"
        "- overview: 2-3 frasi che inquadrano cosa dice davvero il contenuto.\n"
        "- points: 3-6 punti, ciascuno con 'title' (breve) e 'detail' (1-2 frasi) sui "
        "fatti/argomenti più importanti.\n"
        "- insights: 2-4 spunti interessanti o implicazioni concrete.\n\n"
        + untrusted_block(f"Titolo: {title}\nFonte: {source}\n\nContenuto:\n{text}", nonce)
    )


def _error_dive(
    message: str, complete: bool = False, reason: str = "", text: str = ""
) -> dict[str, Any]:
    # `failed` is what tells the caller this is not an analysis. Without it the
    # payload is shaped exactly like a real one, and a failure that reached the
    # cache was served as "done" forever, with no way to try the article again.
    dive: dict[str, Any] = {
        "overview": message,
        "points": [],
        "insights": [],
        "content_complete": complete,
        "failed": True,
    }
    if not complete and reason:
        dive["partial_note"] = _PARTIAL_REASON_NOTES.get(reason, _DEFAULT_PARTIAL_NOTE)
    if text:
        dive["source_text"] = text
    return dive
