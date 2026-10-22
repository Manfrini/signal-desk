"""The shipped catalogue holds sources we can actually collect from.

25 entries used to sit in `config/sources.yaml` marked "Disabled: ..." in
their notes — dead feeds, extractors that returned navigation links, product
pages with no article stream. Carrying them had two costs: the Fonti page
listed sources that could never produce anything, and the disabled flag could
drift (five of them were enabled in a real install, one of which pulled
documentation pages into the news stream).

They now live in docs/SOURCES-BACKLOG.md, which keeps the record of what needs
fixing. These tests keep the two in step.
"""

from __future__ import annotations

from pathlib import Path

import yaml

SEED = Path("config/sources.yaml")
BACKLOG = Path("docs/SOURCES-BACKLOG.md")


def _seed_sources() -> list[dict]:
    payload = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    return payload["sources"] if isinstance(payload, dict) else payload


def test_no_source_is_shipped_marked_disabled_in_its_notes():
    marked = [
        s["id"] for s in _seed_sources() if "disabled" in str(s.get("notes", "")).lower()
    ]

    assert marked == [], (
        "these belong in docs/SOURCES-BACKLOG.md, not in the catalogue: " + ", ".join(marked)
    )


def test_every_shipped_source_has_a_url_and_an_id():
    for source in _seed_sources():
        assert str(source.get("id", "")).strip()
        assert str(source.get("url", "")).strip().startswith("http")


def test_the_backlog_does_not_list_a_source_that_is_also_shipped():
    # A source is either collectable or waiting to be fixed, never both.
    shipped = {s["id"] for s in _seed_sources()}
    backlog = set(__import__("re").findall(r"^\| `([a-z0-9_]+)`", BACKLOG.read_text(encoding="utf-8"), __import__("re").M))

    assert backlog, "the backlog should list the sources that were removed"
    assert not (shipped & backlog)
