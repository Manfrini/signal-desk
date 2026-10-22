"""The first-run setup wizard, driven entirely through injected IO."""

from __future__ import annotations

from unittest import mock

import pytest

from src import setup_wizard
from src.processors import claude_cli_analyzer, llm_health, profile_expander
from src.storage import json_storage


class FakeInput:
    """Pops canned answers in order; raises EOFError once exhausted."""

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self._answers:
            raise EOFError
        return self._answers.pop(0)


def _prints() -> tuple[list[str], "callable"]:
    lines: list[str] = []

    def fn(*args, **kwargs):
        lines.append(" ".join(str(a) for a in args))

    return lines, fn


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path, monkeypatch):
    """Route every storage side effect at a tmp dir instead of the real repo."""
    profile_path = tmp_path / "profile.json"
    sources_path = tmp_path / "sources.json"
    overrides_path = tmp_path / "overrides.json"
    real_load_profile = json_storage.load_profile_config
    real_save_profile = json_storage.save_profile_config
    real_load_sources = json_storage.load_sources_config

    monkeypatch.setattr(
        json_storage, "load_profile_config",
        lambda path=profile_path, legacy_yaml_path=tmp_path / "nope.yaml": real_load_profile(
            path=profile_path, legacy_yaml_path=tmp_path / "nope.yaml"
        ),
    )
    monkeypatch.setattr(
        json_storage, "save_profile_config",
        lambda doc, path=profile_path: real_save_profile(doc, path=profile_path),
    )
    monkeypatch.setattr(
        json_storage, "load_sources_config",
        lambda path=sources_path, legacy_yaml_path=tmp_path / "no_sources.yaml": real_load_sources(
            path=sources_path, legacy_yaml_path=tmp_path / "no_sources.yaml"
        ),
    )

    import src.settings as settings_mod

    real_save_overrides = settings_mod.save_settings_overrides
    monkeypatch.setattr(
        settings_mod, "save_settings_overrides",
        lambda updates, overrides_path=overrides_path: real_save_overrides(
            updates, overrides_path=overrides_path
        ),
    )
    yield {"profile_path": profile_path, "sources_path": sources_path, "overrides_path": overrides_path}


def _no_providers(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)


def _claude_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude" if name == "claude" else None)


def test_real_input_without_a_tty_refuses_and_returns_2(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = setup_wizard.run_setup()

    assert code == 2
    assert "terminale" in capsys.readouterr().out


def test_no_providers_installed_returns_1(monkeypatch):
    _no_providers(monkeypatch)
    lines, print_fn = _prints()
    fake_input = FakeInput()

    code = setup_wizard.run_setup(input_fn=fake_input, print_fn=print_fn)

    assert code == 1
    assert any("Nessun CLI LLM trovato" in line for line in lines)

def test_single_installed_provider_is_auto_selected(monkeypatch):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    lines, print_fn = _prints()
    fake_input = FakeInput(
        "",  # language: default it
        "",  # about_me: blank line -> skip profile
        "n",  # first run: no
    )

    code = setup_wizard.run_setup(input_fn=fake_input, print_fn=print_fn)

    assert code == 0
    assert any("Uso Claude Code" in line for line in lines)


def test_healthy_provider_skips_the_login_offer(monkeypatch):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    login_runner = mock.Mock()
    lines, print_fn = _prints()
    fake_input = FakeInput("", "", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=print_fn, login_runner=login_runner)

    login_runner.assert_not_called()
    assert any("sessione attiva" in line for line in lines)

def test_unhealthy_provider_offers_login_and_runs_it_on_yes(monkeypatch):
    _claude_installed(monkeypatch)
    health = {"ok": False}
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: health)
    login_runner = mock.Mock(side_effect=lambda cmd: health.update(ok=True))
    lines, print_fn = _prints()
    fake_input = FakeInput(
        "s",  # accept login
        "",   # language
        "",   # about_me blank
        "n",  # first run
    )

    setup_wizard.run_setup(input_fn=fake_input, print_fn=print_fn, login_runner=login_runner)

    login_runner.assert_called_once_with(["claude", "auth", "login", "--claudeai"])
    assert any("accesso riuscito" in line for line in lines)

def test_declining_login_does_not_call_the_runner(monkeypatch):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": False})
    login_runner = mock.Mock()
    lines, print_fn = _prints()
    fake_input = FakeInput("n", "", "", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=print_fn, login_runner=login_runner)

    login_runner.assert_not_called()


def test_defaults_to_italian_on_blank_answer(monkeypatch, isolate_storage):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    lines, print_fn = _prints()
    fake_input = FakeInput("", "", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=print_fn)

    saved = json_storage.load_json(isolate_storage["overrides_path"])
    assert saved["output_language"] == "it"
    assert saved["ui_language"] == "it"

def test_accepts_english(monkeypatch, isolate_storage):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    fake_input = FakeInput("en", "", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=lambda *a, **k: None)

    saved = json_storage.load_json(isolate_storage["overrides_path"])
    assert saved["output_language"] == "en"


def test_blank_about_me_skips_profile_entirely(monkeypatch, isolate_storage):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    fake_input = FakeInput("", "", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=lambda *a, **k: None)

    assert not isolate_storage["profile_path"].exists()

def test_llm_unavailable_still_saves_the_free_text(monkeypatch, isolate_storage):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    monkeypatch.setattr(claude_cli_analyzer, "is_available", lambda *a, **k: False)
    fake_input = FakeInput("", "I am a product analyst", "", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=lambda *a, **k: None)

    saved = json_storage.load_json(isolate_storage["profile_path"])
    assert saved["about_me"] == "I am a product analyst"
    assert saved["keywords_boost"] == []

def test_confirmed_expansion_is_saved_with_the_proposal(monkeypatch, isolate_storage):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    monkeypatch.setattr(claude_cli_analyzer, "is_available", lambda *a, **k: True)
    proposal = {
        "goal": "stay current", "interests": ["sql"], "skills_to_grow": [],
        "keywords_boost": ["agent"], "projects": [],
    }
    monkeypatch.setattr(profile_expander, "expand_profile", lambda *a, **k: proposal)
    fake_input = FakeInput("", "I am a product analyst", "", "s", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=lambda *a, **k: None)

    saved = json_storage.load_json(isolate_storage["profile_path"])
    assert saved["about_me"] == "I am a product analyst"
    assert saved["keywords_boost"] == ["agent"]

def test_declined_expansion_keeps_only_the_free_text(monkeypatch, isolate_storage):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    monkeypatch.setattr(claude_cli_analyzer, "is_available", lambda *a, **k: True)
    proposal = {
        "goal": "stay current", "interests": ["sql"], "skills_to_grow": [],
        "keywords_boost": ["agent"], "projects": [],
    }
    monkeypatch.setattr(profile_expander, "expand_profile", lambda *a, **k: proposal)
    fake_input = FakeInput("", "I am a product analyst", "", "n", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=lambda *a, **k: None)

    saved = json_storage.load_json(isolate_storage["profile_path"])
    assert saved["about_me"] == "I am a product analyst"
    assert saved["keywords_boost"] == []

def test_multiline_about_me_stops_on_blank_line(monkeypatch, isolate_storage):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    monkeypatch.setattr(claude_cli_analyzer, "is_available", lambda *a, **k: False)
    fake_input = FakeInput("", "Line one", "Line two", "", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=lambda *a, **k: None)

    saved = json_storage.load_json(isolate_storage["profile_path"])
    assert saved["about_me"] == "Line one\nLine two"


def test_prints_the_configured_source_count(monkeypatch, isolate_storage, tmp_path):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    json_storage.save_json(
        isolate_storage["sources_path"],
        {"categories": [], "sources": [{"id": "a"}, {"id": "b"}]},
    )
    lines, print_fn = _prints()
    fake_input = FakeInput("", "", "n")

    setup_wizard.run_setup(input_fn=fake_input, print_fn=print_fn)

    assert any("Fonti configurate: 2" in line for line in lines)


def test_accepting_runs_the_injected_pipeline(monkeypatch):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    pipeline_runner = mock.Mock()
    fake_input = FakeInput("", "", "s")

    code = setup_wizard.run_setup(
        input_fn=fake_input, print_fn=lambda *a, **k: None, pipeline_runner=pipeline_runner
    )

    pipeline_runner.assert_called_once()
    assert code == 0

def test_declining_does_not_run_the_pipeline(monkeypatch):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    pipeline_runner = mock.Mock()
    fake_input = FakeInput("", "", "n")

    setup_wizard.run_setup(
        input_fn=fake_input, print_fn=lambda *a, **k: None, pipeline_runner=pipeline_runner
    )

    pipeline_runner.assert_not_called()

def test_a_failing_pipeline_is_reported_not_raised(monkeypatch):
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    pipeline_runner = mock.Mock(side_effect=RuntimeError("network down"))
    lines, print_fn = _prints()
    fake_input = FakeInput("", "", "s")

    code = setup_wizard.run_setup(
        input_fn=fake_input, print_fn=print_fn, pipeline_runner=pipeline_runner
    )

    assert code == 0
    assert any("network down" in line for line in lines)


def test_eof_at_any_prompt_ends_the_wizard_gracefully_instead_of_crashing(monkeypatch):
    # A closed stdin (piped input running out, Ctrl+D) must never surface as
    # an uncaught EOFError -- every _ask() call falls back to its default.
    _claude_installed(monkeypatch)
    monkeypatch.setattr(llm_health, "claude_cli_health", lambda **_: {"ok": True})
    fake_input = FakeInput()  # no answers at all: every prompt hits EOF immediately

    code = setup_wizard.run_setup(input_fn=fake_input, print_fn=lambda *a, **k: None)

    assert code == 0
