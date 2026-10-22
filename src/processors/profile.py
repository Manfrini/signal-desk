"""User profile loading and personal-relevance scoring (Fase 2).

The profile (``config/profile.yaml``) declares the owner's goal, interests,
projects and skills. It is used to re-weight items by how relevant they are
to the owner personally — kept separate from the global ``signal_score`` so
the "world" view is preserved.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from src.storage.json_storage import DEFAULT_PROFILE_PATH, load_json, load_profile_config

logger = logging.getLogger(__name__)

# Empty profile: personalization stays inert (no boost, no goal). Shape
# matches the v2 schema in src/storage/json_storage.py (about_me/derived/
# edited_fields), so a profile loaded from either path has the same keys.
EMPTY_PROFILE: dict[str, Any] = {
    "version": 2,
    "about_me": "",
    "goal": "",
    "interests": [],
    "projects": [],
    "skills_to_grow": [],
    "keywords_boost": [],
    "derived": {"source": "", "generated_at": "", "provider": "", "from_about_me_hash": ""},
    "edited_fields": [],
}


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    """Load the user profile, falling back to an inert empty profile.

    The default path resolves through ``load_profile_config`` (JSON in
    ``data/``, migrating a legacy ``config/profile.yaml`` once if present).
    An explicit ``.yaml``/``.yml`` path is read directly, for callers that
    still point at a hand-edited YAML profile; any other explicit path is
    read as JSON. Either way the result is merged over ``EMPTY_PROFILE`` so
    every key is always present.
    """
    profile_path = Path(path)
    if profile_path == Path(DEFAULT_PROFILE_PATH):
        payload = load_profile_config(path=profile_path)
    elif profile_path.suffix in (".yaml", ".yml"):
        if not profile_path.exists():
            return dict(EMPTY_PROFILE)
        try:
            with profile_path.open("r", encoding="utf-8") as file_handle:
                payload = yaml.safe_load(file_handle) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Could not load profile %s: %s", profile_path, exc)
            return dict(EMPTY_PROFILE)
    else:
        payload = load_json(profile_path, default={})

    if not isinstance(payload, dict):
        return dict(EMPTY_PROFILE)
    merged = dict(EMPTY_PROFILE)
    merged.update(payload)
    return merged


def profile_goal(profile: dict[str, Any]) -> str:
    """Return the trimmed goal string from a profile."""
    return str(profile.get("goal", "")).strip()


def profile_context(profile: dict[str, Any], *, max_chars: int = 800) -> str:
    """Return a compact free-text block describing the user, for the LLM.

    Unlike ``profile_goal`` (only the ``goal`` sentence), this also surfaces
    ``about_me`` and the declared interests/skills, so a brand-new user who
    has only written a few lines about themselves — with no keyword lists
    yet — still gives the LLM something to work with (see
    ``relevance_to_goal`` in processor_service.py, which turns this into a
    personal-relevance boost even before any keyword matches).
    """
    about_me = str(profile.get("about_me", "")).strip()
    goal = profile_goal(profile)
    interests = _as_terms(profile.get("interests"))
    skills = _as_terms(profile.get("skills_to_grow"))

    lines: list[str] = []
    if about_me:
        lines.append(f"Chi è l'utente: {about_me}")
    if goal:
        lines.append(f"Obiettivo: {goal}")
    if interests:
        lines.append(f"Temi di interesse: {', '.join(interests)}")
    if skills:
        lines.append(f"Competenze da sviluppare: {', '.join(skills)}")

    return "\n".join(lines).strip()[:max_chars]


def personal_relevance(
    item: dict[str, Any],
    profile: dict[str, Any],
    preferences: dict[str, Any] | None = None,
) -> tuple[int, list[str]]:
    """Return a 0-100 personal relevance score and the matched terms.

    The score blends keyword-boost matches (weighted more) with interest and
    skill matches, plus a bonus for open-source signals (which align with the
    owner's goal of building hands-off AI products). When ``preferences`` are
    given, a small capped adjustment learned from past feedback is applied on
    top — the declared profile stays the baseline.
    """
    text = _item_text(item)
    if not text:
        return 0, []

    boost_terms = _as_terms(profile.get("keywords_boost"))
    interest_terms = _as_terms(profile.get("interests"))
    skill_terms = _as_terms(profile.get("skills_to_grow"))
    project_terms = _project_keywords(profile.get("projects"))

    matched: list[str] = []
    score = 0
    for term in boost_terms:
        if _term_matches(text, term):
            score += 10
            matched.append(term)
    for term in interest_terms + project_terms:
        if _term_matches(text, term):
            score += 6
            matched.append(term)
    for term in skill_terms:
        if _term_matches(text, term):
            score += 5
            matched.append(term)

    if str(item.get("signal_type", "")) == "open_source_trend":
        score += 8

    if preferences:
        from src.processors.feedback import learned_boost

        score += learned_boost(item, preferences)

    return max(0, min(100, score)), _dedupe(matched)


def _item_text(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("title", "")),
        str(item.get("clean_title", "")),
        str(item.get("summary", "")),
        str(item.get("why_it_matters", "")),
        str(item.get("primary_topic", "")),
        str(item.get("secondary_topic", "")),
        " ".join(str(tag) for tag in item.get("tags", []) if tag),
    ]
    return " ".join(parts).lower()


def _as_terms(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(term).strip().lower() for term in value if str(term).strip()]


def _project_keywords(projects: Any) -> list[str]:
    if not isinstance(projects, list):
        return []
    keywords: list[str] = []
    for project in projects:
        if isinstance(project, dict):
            keywords.extend(_as_terms(project.get("keywords")))
    return keywords


def _term_matches(text: str, term: str) -> bool:
    pattern = r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, text) is not None


def _dedupe(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique.append(term)
    return unique
