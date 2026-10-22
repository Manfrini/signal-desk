"""profile_expander.py: turning free text into a structured profile proposal.

expand_profile() is deliberately never asserted against a real LLM here —
_sanitize_expansion is the part with real logic (validation, denylist,
regex-safety, caps), and it's what protects personal_relevance from a
malformed or hostile expansion.
"""

from __future__ import annotations

from unittest import mock

from src.processors.profile_expander import (
    MAX_KEYWORDS,
    _sanitize_expansion,
    expand_profile,
)


def test_none_when_llm_output_is_not_a_dict():
    assert _sanitize_expansion("plain text") is None
    assert _sanitize_expansion(["a", "list"]) is None
    assert _sanitize_expansion(None) is None


def test_none_when_keywords_boost_ends_up_empty():
    payload = {"goal": "g", "interests": ["x"], "skills_to_grow": [], "keywords_boost": []}
    assert _sanitize_expansion(payload) is None


def test_drops_generic_denylisted_terms():
    payload = {
        "goal": "g", "interests": [], "skills_to_grow": [],
        "keywords_boost": ["ai", "tech", "news", "product analytics"],
    }
    result = _sanitize_expansion(payload)
    assert result["keywords_boost"] == ["product analytics"]


def test_drops_too_short_and_too_long_terms():
    payload = {
        "goal": "g", "interests": [], "skills_to_grow": [],
        "keywords_boost": ["x", "a" * 41, "sql"],
    }
    result = _sanitize_expansion(payload)
    assert result["keywords_boost"] == ["sql"]


def test_lowercases_and_dedupes_terms():
    payload = {
        "goal": "g", "interests": [], "skills_to_grow": [],
        "keywords_boost": ["SQL", "sql", "Sql"],
    }
    result = _sanitize_expansion(payload)
    assert result["keywords_boost"] == ["sql"]


def test_compiles_as_term_guards_against_invalid_regex_terms():
    # personal_relevance._term_matches builds \b<term>\b as a regex from
    # every saved keyword. re.escape neutralizes ordinary metacharacters
    # (brackets, backslashes), but the guard exists as defense-in-depth for
    # whatever re.escape does not: it must never let a term through that
    # would raise re.error when compiled, and must never crash itself.
    from src.processors.profile_expander import _compiles_as_term

    for term in ["sql", "bad[bracket", "trailing\\", "a/b test", "text-to-sql"]:
        assert _compiles_as_term(term) is True


def test_caps_keyword_list_length():
    many = [f"term{i}" for i in range(MAX_KEYWORDS + 20)]
    payload = {"goal": "g", "interests": [], "skills_to_grow": [], "keywords_boost": many}
    result = _sanitize_expansion(payload)
    assert len(result["keywords_boost"]) == MAX_KEYWORDS


def test_projects_without_a_name_are_dropped():
    payload = {
        "goal": "g", "interests": [], "skills_to_grow": [], "keywords_boost": ["sql"],
        "projects": [{"description": "no name"}, {"name": "Real", "keywords": ["x", "rss"]}],
    }
    result = _sanitize_expansion(payload)
    assert len(result["projects"]) == 1
    assert result["projects"][0]["name"] == "Real"
    assert result["projects"][0]["keywords"] == ["rss"]


def test_expand_profile_returns_none_for_blank_input():
    assert expand_profile("   ") is None
    assert expand_profile("") is None


def test_expand_profile_sanitizes_the_analyzer_output():
    analyzer = mock.Mock()
    analyzer.run_structured.return_value = {
        "goal": "stay current",
        "interests": ["product analytics"],
        "skills_to_grow": ["sql"],
        "keywords_boost": ["agent", "ai"],
        "projects": [],
    }

    result = expand_profile("I'm a product analyst", analyzer=analyzer)

    assert result["keywords_boost"] == ["agent"]
    assert analyzer.run_structured.called


def test_expand_profile_returns_none_when_analyzer_yields_nothing_usable():
    analyzer = mock.Mock()
    analyzer.run_structured.return_value = None

    assert expand_profile("some text", analyzer=analyzer) is None
