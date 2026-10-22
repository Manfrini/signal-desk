"""src/settings.py: the merge of tracked YAML settings with user overrides."""

from __future__ import annotations

import json
from pathlib import Path

from src import settings


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_defaults_when_nothing_is_configured(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"

    assert settings.llm_provider(settings_path=yaml_path, overrides_path=overrides_path) == "claude"
    assert settings.llm_model(settings_path=yaml_path, overrides_path=overrides_path) == ""
    assert settings.output_language(settings_path=yaml_path, overrides_path=overrides_path) == "it"
    assert settings.ui_language(settings_path=yaml_path, overrides_path=overrides_path) == "it"


def test_yaml_value_is_read(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"
    _write(yaml_path, "output_language: en\n")

    assert settings.output_language(settings_path=yaml_path, overrides_path=overrides_path) == "en"


def test_override_wins_over_yaml(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"
    _write(yaml_path, "output_language: it\n")
    overrides_path.write_text(json.dumps({"output_language": "en"}), encoding="utf-8")

    assert settings.output_language(settings_path=yaml_path, overrides_path=overrides_path) == "en"


def test_unknown_provider_falls_back_to_default(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(json.dumps({"llm_provider": "not-a-real-provider"}), encoding="utf-8")

    assert settings.llm_provider(settings_path=yaml_path, overrides_path=overrides_path) == "claude"


def test_unsupported_language_falls_back_to_italian(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(json.dumps({"output_language": "fr"}), encoding="utf-8")

    assert settings.output_language(settings_path=yaml_path, overrides_path=overrides_path) == "it"


def test_ui_language_is_independent_from_content_language(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(
        json.dumps({"ui_language": "en", "output_language": "it"}), encoding="utf-8"
    )

    assert settings.ui_language(settings_path=yaml_path, overrides_path=overrides_path) == "en"
    assert settings.output_language(settings_path=yaml_path, overrides_path=overrides_path) == "it"


def test_corrupt_yaml_degrades_to_empty_not_a_crash(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"
    _write(yaml_path, "{this is not valid yaml")

    assert settings.llm_provider(settings_path=yaml_path, overrides_path=overrides_path) == "claude"


def test_corrupt_overrides_json_degrades_to_empty_not_a_crash(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text("{not valid json", encoding="utf-8")

    assert settings.llm_provider(settings_path=yaml_path, overrides_path=overrides_path) == "claude"


def test_save_overrides_creates_the_file(tmp_path):
    overrides_path = tmp_path / "overrides.json"

    settings.save_settings_overrides({"llm_provider": "claude"}, overrides_path=overrides_path)

    assert json.loads(overrides_path.read_text(encoding="utf-8"))["llm_provider"] == "claude"


def test_save_overrides_merges_instead_of_replacing(tmp_path):
    overrides_path = tmp_path / "overrides.json"
    settings.save_settings_overrides({"llm_provider": "claude"}, overrides_path=overrides_path)

    settings.save_settings_overrides({"output_language": "en"}, overrides_path=overrides_path)

    saved = json.loads(overrides_path.read_text(encoding="utf-8"))
    assert saved["llm_provider"] == "claude"
    assert saved["output_language"] == "en"


def test_save_overrides_is_reflected_by_the_readers(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    overrides_path = tmp_path / "overrides.json"

    settings.save_settings_overrides({"output_language": "en"}, overrides_path=overrides_path)

    assert settings.output_language(settings_path=yaml_path, overrides_path=overrides_path) == "en"
