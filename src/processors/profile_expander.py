"""Turn a user's free-text self-description into a structured profile.

The user writes a few lines about who they are and what they care about
(``about_me``); this module calls the configured LLM to derive the same
structured fields ``config/profile.yaml`` used to declare by hand (goal,
interests, skills, keyword boosts, projects), so ``personal_relevance``
(``src/processors/profile.py``) can score items against them.

The LLM output is never written to disk directly — the caller (the
``/api/profile/expand`` route) hands the sanitized result back to the
dashboard as a *proposal*; the user reviews and saves it via
``POST /api/profile``. Sanitization here exists because two things read
these lists downstream without a validation layer of their own:
``personal_relevance``'s ``_term_matches`` compiles each keyword as a regex,
and a term that doesn't compile would crash every future scoring pass.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer
from src.processors.prompt_lang import language_directive

logger = logging.getLogger(__name__)

# Caps mirror the size of a hand-written profile.yaml (see
# config/profile.example.yaml) so an expansion can't produce an unusably
# long profile that would dominate every item's score.
MAX_INTERESTS = 25
MAX_SKILLS = 15
MAX_KEYWORDS = 60
MAX_PROJECTS = 10
MIN_TERM_LENGTH = 2
MAX_TERM_LENGTH = 40

# Terms generic enough to match nearly every AI/tech item, defeating the
# purpose of a personal boost. Deliberately short: legitimately narrow terms
# (e.g. "product", "sql") must never be caught by this list.
_GENERIC_TERM_DENYLIST = {"ai", "the", "a", "an", "new", "news", "today", "tech", "technology"}

PROFILE_EXPANSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "interests": {"type": "array", "items": {"type": "string"}},
        "skills_to_grow": {"type": "array", "items": {"type": "string"}},
        "keywords_boost": {"type": "array", "items": {"type": "string"}},
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "keywords"],
            },
        },
    },
    "required": ["goal", "interests", "skills_to_grow", "keywords_boost"],
}




def _build_expansion_prompt(about_me: str, language: str) -> str:
    return (
        "Un utente ha scritto una breve descrizione di sé per personalizzare "
        "un aggregatore di notizie AI/tech. Trasforma il testo in un profilo "
        "strutturato che verrà usato per calcolare quanto ogni notizia è "
        "rilevante per lui/lei tramite un semplice match di keyword.\n\n"
        "Vincoli IMPORTANTI sulle liste di keyword (interests, skills_to_grow, "
        "keywords_boost, projects[].keywords):\n"
        "- ogni termine in MINUSCOLO, 1-3 parole, senza punteggiatura;\n"
        "- termini IN INGLESE (le notizie sono in inglese anche se l'utente "
        "scrive in italiano);\n"
        "- NIENTE termini ultra-generici che matchano quasi tutto (es. 'ai', "
        "'tech', 'news'): scegli termini specifici al suo campo/interessi;\n"
        "- 8-15 interests, 5-10 skills_to_grow, 20-40 keywords_boost.\n\n"
        f"- goal: una frase in prima persona, {language_directive(language)}, "
        "che riassume cosa l'utente vuole ottenere restando aggiornato.\n"
        "- projects: solo se il testo menziona progetti concreti, altrimenti "
        "un array vuoto.\n\n"
        "Testo dell'utente:\n"
        f'"""{about_me}"""\n\n'
        "Restituisci un oggetto con le chiavi: goal, interests, skills_to_grow, "
        "keywords_boost, projects."
    )


def expand_profile(
    about_me: str,
    *,
    analyzer: ClaudeCliAnalyzer | None = None,
    language: str = "it",
) -> dict[str, Any] | None:
    """Call the LLM to derive a structured profile from free text.

    Returns a sanitized proposal dict, or ``None`` if the CLI is unavailable,
    the call failed, or the output couldn't be turned into a usable profile.
    """
    about_me = str(about_me or "").strip()
    if not about_me:
        return None

    runner = analyzer or ClaudeCliAnalyzer()
    prompt = _build_expansion_prompt(about_me, language)
    payload = runner.run_structured(prompt, PROFILE_EXPANSION_SCHEMA)
    return _sanitize_expansion(payload)


def _sanitize_expansion(payload: Any) -> dict[str, Any] | None:
    """Validate and clean a raw LLM expansion before it is ever shown/saved."""
    if not isinstance(payload, dict):
        return None

    goal = str(payload.get("goal", "")).strip()[:500]
    interests = _sanitize_terms(payload.get("interests"), MAX_INTERESTS)
    skills = _sanitize_terms(payload.get("skills_to_grow"), MAX_SKILLS)
    keywords = _sanitize_terms(payload.get("keywords_boost"), MAX_KEYWORDS)
    projects = _sanitize_projects(payload.get("projects"))

    if not keywords:
        # The model produced nothing usable for the one signal that carries
        # the most weight in personal_relevance (+10 per match).
        return None

    return {
        "goal": goal,
        "interests": interests,
        "skills_to_grow": skills,
        "keywords_boost": keywords,
        "projects": projects,
    }


def _sanitize_terms(raw: Any, max_items: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in raw:
        term = str(entry).strip().lower()
        if not (MIN_TERM_LENGTH <= len(term) <= MAX_TERM_LENGTH):
            continue
        if term in _GENERIC_TERM_DENYLIST or term in seen:
            continue
        if not _compiles_as_term(term):
            # personal_relevance._term_matches compiles every keyword as a
            # regex; a term that can't compile would crash every future
            # scoring pass, so it must never reach the saved profile.
            continue
        seen.add(term)
        cleaned.append(term)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _sanitize_projects(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    projects: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()[:120]
        if not name:
            continue
        description = str(entry.get("description", "")).strip()[:500]
        keywords = _sanitize_terms(entry.get("keywords"), MAX_KEYWORDS)
        projects.append({"name": name, "description": description, "keywords": keywords})
        if len(projects) >= MAX_PROJECTS:
            break
    return projects


def _compiles_as_term(term: str) -> bool:
    try:
        re.compile(r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b")
    except re.error:
        return False
    return True
