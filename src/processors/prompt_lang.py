"""The directive that tells an LLM prompt which language to answer in.

Shared by every prompt builder that needs it (batch insights, deep dive,
section briefs, newsletter, profile expansion) so the wording stays
consistent and adding a language only means adding one dict entry.
"""

from __future__ import annotations

_LANGUAGE_DIRECTIVES = {"it": "IN ITALIANO", "en": "IN ENGLISH"}


def language_directive(language: str) -> str:
    """Return the imperative directive for ``language`` (an ISO 639-1 code)."""
    return _LANGUAGE_DIRECTIVES.get(language, f'NELLA LINGUA "{language}"')
