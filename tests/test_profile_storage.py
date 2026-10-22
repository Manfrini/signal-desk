"""Profile storage: JSON-first with a one-shot migration from the legacy
config/profile.yaml, mirroring load_sources_config's pattern."""

from __future__ import annotations

from pathlib import Path

from src.processors.profile import load_profile
from src.storage.json_storage import load_profile_config, save_profile_config


def test_missing_files_return_inert_empty_v2_profile(tmp_path):
    profile = load_profile_config(
        path=tmp_path / "profile.json", legacy_yaml_path=tmp_path / "nope.yaml"
    )
    assert profile["version"] == 2
    assert profile["about_me"] == ""
    assert profile["goal"] == ""
    assert profile["keywords_boost"] == []


def test_migrates_legacy_yaml_once_and_persists_json(tmp_path):
    json_path = tmp_path / "profile.json"
    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text(
        "goal: Build hands-off AI\n"
        "interests:\n  - autonomous agents\n"
        "keywords_boost:\n  - agent\n  - sql\n",
        encoding="utf-8",
    )

    profile = load_profile_config(path=json_path, legacy_yaml_path=yaml_path)

    assert profile["version"] == 2
    assert profile["goal"] == "Build hands-off AI"
    assert profile["keywords_boost"] == ["agent", "sql"]
    assert profile["derived"]["source"] == "manual"
    assert json_path.exists()


def test_second_load_reads_json_and_ignores_yaml_changes(tmp_path):
    json_path = tmp_path / "profile.json"
    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text("goal: first\n", encoding="utf-8")
    load_profile_config(path=json_path, legacy_yaml_path=yaml_path)

    yaml_path.write_text("goal: second\n", encoding="utf-8")
    profile = load_profile_config(path=json_path, legacy_yaml_path=yaml_path)

    assert profile["goal"] == "first"


def test_corrupt_json_degrades_to_empty_profile(tmp_path):
    json_path = tmp_path / "profile.json"
    json_path.write_text("{not valid json", encoding="utf-8")

    profile = load_profile_config(path=json_path, legacy_yaml_path=tmp_path / "nope.yaml")

    assert profile["version"] == 2
    assert profile["keywords_boost"] == []


def test_save_then_load_round_trips(tmp_path):
    json_path = tmp_path / "profile.json"
    doc = {"version": 2, "about_me": "product analyst", "goal": "g",
           "interests": ["sql"], "projects": [], "skills_to_grow": [],
           "keywords_boost": ["sql"], "derived": {}, "edited_fields": []}

    save_profile_config(doc, path=json_path)
    loaded = load_profile_config(path=json_path, legacy_yaml_path=tmp_path / "nope.yaml")

    assert loaded["about_me"] == "product analyst"
    assert loaded["keywords_boost"] == ["sql"]


def test_load_profile_default_path_argument_resolves_through_config(tmp_path, monkeypatch):
    # load_profile's default arg equality check must still route through
    # load_profile_config even when DEFAULT_PROFILE_PATH is monkeypatched
    # (used by the dashboard/API layer against a temp data dir in tests).
    import src.processors.profile as profile_mod

    json_path = tmp_path / "profile.json"
    save_profile_config({"version": 2, "goal": "from json"}, path=json_path)
    monkeypatch.setattr(profile_mod, "DEFAULT_PROFILE_PATH", json_path)

    profile = load_profile(json_path)

    assert profile["goal"] == "from json"


def test_load_profile_explicit_json_path_not_matching_default(tmp_path):
    json_path = tmp_path / "custom_profile.json"
    save_profile_config({"version": 2, "goal": "explicit json"}, path=json_path)

    profile = load_profile(json_path)

    assert profile["goal"] == "explicit json"
