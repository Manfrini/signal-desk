"""First-run wizard: `python -m src.main setup`.

Guides a new installation end to end — which LLM CLI to use, logging in,
content and application language, a free-text profile (expanded into structured interests/
keywords via the LLM), the sources bootstrap, and an optional first
collection run — so a person who is not Marco doesn't inherit his profile
or have to read the source to figure out what `serve` needs first.

Every side-effecting step takes an injectable callable (``input_fn``,
``print_fn``, ``login_runner``, ``pipeline_runner``) so the whole flow is
unit-testable without a real terminal, a real CLI login, or a real
network/LLM call — the same dependency-injection habit already used by
``llm_health.claude_cli_health`` (``_run``/``_now``) and the various
``analyzer=`` parameters across ``src/processors``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Callable

InputFn = Callable[[str], str]
PrintFn = Callable[..., None]


def _ask(input_fn: InputFn, prompt: str, *, default: str = "") -> str:
    """Prompt via ``input_fn``, treating EOF (Ctrl+D / closed stdin) as if the
    user accepted the default instead of letting the wizard crash mid-flow."""
    try:
        return input_fn(prompt)
    except EOFError:
        return default


def run_setup(
    *,
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
    login_runner: Callable[[list[str]], Any] | None = None,
    pipeline_runner: Callable[[], Any] | None = None,
) -> int:
    """Run the interactive setup wizard. Returns a process exit code."""
    if input_fn is input and not sys.stdin.isatty():
        print_fn(
            "`setup` è interattivo e richiede un terminale. "
            "Esegui `python -m src.main setup` da un terminale vero, "
            "oppure configura a mano config/settings.yaml e data/profile.json."
        )
        return 2

    print_fn(f"Python {sys.version.split()[0]}")

    installed = _check_providers(print_fn=print_fn)
    provider_id = _choose_provider(installed, input_fn=input_fn, print_fn=print_fn)
    if provider_id is None:
        return 1

    _check_auth(provider_id, input_fn=input_fn, print_fn=print_fn, login_runner=login_runner)

    language = _choose_language(input_fn=input_fn, print_fn=print_fn)

    from src.settings import save_settings_overrides

    save_settings_overrides(
        {"llm_provider": provider_id, "output_language": language, "ui_language": language}
    )
    print_fn(
        f"Impostazioni salvate: provider={provider_id}, lingua app={language}, lingua contenuti={language}."
    )

    _collect_profile(provider_id, language, input_fn=input_fn, print_fn=print_fn)
    _bootstrap_sources(print_fn=print_fn)
    _offer_first_run(input_fn=input_fn, print_fn=print_fn, pipeline_runner=pipeline_runner)

    print_fn("")
    print_fn("Setup completato. Per avviare la dashboard:")
    print_fn("  python -m src.main serve")
    print_fn("(apri http://127.0.0.1:8000 nel browser)")
    return 0


def _check_providers(*, print_fn: PrintFn) -> list[str]:
    import shutil

    from src.processors.llm_provider import PROVIDERS

    print_fn("")
    print_fn("Provider LLM disponibili:")
    installed = []
    for spec in PROVIDERS.values():
        if shutil.which(spec.binary) is not None:
            installed.append(spec.id)
            print_fn(f"  [x] {spec.label} ({spec.binary})")
        else:
            print_fn(f"  [ ] {spec.label}: non trovato. {spec.install_hint}")
    return installed


def _choose_provider(
    installed: list[str], *, input_fn: InputFn, print_fn: PrintFn
) -> str | None:
    from src.processors.llm_provider import PROVIDERS

    if not installed:
        print_fn("")
        print_fn(
            "Nessun CLI LLM trovato sul PATH. Installane uno (vedi sopra) e rilancia `setup`."
        )
        print_fn(
            "Puoi comunque continuare senza: la pipeline userà solo l'analisi deterministica."
        )
        return None
    if len(installed) == 1:
        provider_id = installed[0]
        print_fn(f"Uso {PROVIDERS[provider_id].label} (unico CLI trovato).")
        return provider_id

    print_fn("")
    print_fn("Quale vuoi usare?")
    for index, provider_id in enumerate(installed, start=1):
        print_fn(f"  {index}. {PROVIDERS[provider_id].label}")
    choice = _ask(input_fn, f"Scelta [1-{len(installed)}, invio per 1]: ").strip()
    if not choice:
        return installed[0]
    try:
        index = int(choice)
    except ValueError:
        return installed[0]
    if 1 <= index <= len(installed):
        return installed[index - 1]
    return installed[0]


def _check_auth(
    provider_id: str,
    *,
    input_fn: InputFn,
    print_fn: PrintFn,
    login_runner: Callable[[list[str]], Any] | None = None,
) -> None:
    from src.processors.llm_provider import get_provider

    spec = get_provider(provider_id)
    print_fn("")

    if spec.login_kind == "api_key":
        print_fn(
            f"{spec.label} si autentica con una API key: imposta la variabile d'ambiente "
            "richiesta (vedi la documentazione del provider) e rilancia `setup` per verificare."
        )
        return

    ok = _probe_auth(provider_id, print_fn=print_fn)
    if ok:
        print_fn(f"{spec.label}: sessione attiva.")
        return

    if spec.login_kind != "pty_url" or spec.login_cmd is None:
        print_fn(f"{spec.label}: impossibile verificare l'accesso automaticamente.")
        return

    answer = _ask(input_fn, f"{spec.label} non risulta autenticato. Accedere ora? [S/n]: ", default="n").strip().lower()
    if answer in ("n", "no"):
        return

    runner = login_runner or (lambda cmd: subprocess.run(cmd))
    print_fn("Apro il login (segui le istruzioni a schermo)...")
    try:
        runner(list(spec.login_cmd))
    except (OSError, subprocess.SubprocessError) as exc:
        print_fn(f"Login non riuscito: {exc}")
        return

    if _probe_auth(provider_id, print_fn=print_fn):
        print_fn(f"{spec.label}: accesso riuscito.")
    else:
        print_fn(f"{spec.label}: non risulta ancora autenticato. Potrai riprovare da `setup` o dalla dashboard.")


def _probe_auth(provider_id: str, *, print_fn: PrintFn) -> bool:
    from src.processors.llm_health import claude_cli_health, reset_health_cache

    reset_health_cache(provider_id)
    return bool(claude_cli_health(provider=provider_id, ttl_seconds=0).get("ok"))


def _choose_language(*, input_fn: InputFn, print_fn: PrintFn) -> str:
    from src.settings import SUPPORTED_LANGUAGES

    print_fn("")
    print_fn(f"Lingua dell'app e dei contenuti ({'/'.join(SUPPORTED_LANGUAGES)}):")
    choice = _ask(input_fn, "Lingua [invio per it]: ").strip().lower()
    return choice if choice in SUPPORTED_LANGUAGES else "it"


def _collect_profile(
    provider_id: str, language: str, *, input_fn: InputFn, print_fn: PrintFn
) -> None:
    print_fn("")
    print_fn("Raccontami chi sei e cosa ti interessa (per personalizzare la rilevanza).")
    print_fn("Scrivi qualche riga, poi lascia una riga vuota per terminare:")
    lines: list[str] = []
    while True:
        try:
            line = input_fn("")
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    about_me = "\n".join(lines).strip()
    if not about_me:
        print_fn("Nessun testo inserito: puoi compilare il profilo più tardi dalla dashboard.")
        return

    from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer, is_available
    from src.processors.profile_expander import expand_profile
    from src.storage.json_storage import load_profile_config, save_profile_config

    proposal = None
    if is_available():
        print_fn(f"Interpreto il testo con {provider_id}...")
        analyzer = ClaudeCliAnalyzer(provider=provider_id, language=language)
        proposal = expand_profile(about_me, analyzer=analyzer, language=language)

    if proposal is None:
        print_fn(
            "Non sono riuscito a generare un profilo strutturato (LLM non disponibile o "
            "testo non interpretabile). Il testo verrà salvato comunque; potrai completare "
            "interessi e keyword dalla dashboard."
        )
        current = load_profile_config()
        current["about_me"] = about_me
        save_profile_config(current)
        return

    print_fn("")
    print_fn("Proposta generata:")
    print_fn(f"  Obiettivo: {proposal.get('goal', '')}")
    print_fn(f"  Interessi: {', '.join(proposal.get('interests', []))}")
    print_fn(f"  Competenze da sviluppare: {', '.join(proposal.get('skills_to_grow', []))}")
    print_fn(f"  Keyword: {', '.join(proposal.get('keywords_boost', []))}")
    confirm = _ask(input_fn, "Confermi e salvi questo profilo? [S/n]: ", default="n").strip().lower()
    if confirm in ("n", "no"):
        print_fn("Profilo non salvato. Potrai compilarlo dalla dashboard in qualsiasi momento.")
        current = load_profile_config()
        current["about_me"] = about_me
        save_profile_config(current)
        return

    current = load_profile_config()
    current["about_me"] = about_me
    current.update(proposal)
    save_profile_config(current)
    print_fn("Profilo salvato.")


def _bootstrap_sources(*, print_fn: PrintFn) -> None:
    from src.storage.json_storage import load_sources_config

    doc = load_sources_config()
    print_fn("")
    print_fn(f"Fonti configurate: {len(doc.get('sources', []))}.")


def _offer_first_run(
    *, input_fn: InputFn, print_fn: PrintFn, pipeline_runner: Callable[[], Any] | None = None
) -> None:
    print_fn("")
    answer = _ask(input_fn, "Vuoi fare ora una prima raccolta e generare la dashboard? (~2-5 min) [S/n]: ", default="n")
    if answer.strip().lower() in ("n", "no"):
        print_fn("Puoi farlo più tardi con: python -m src.main run-daily")
        return

    print_fn("Raccolta in corso...")
    runner = pipeline_runner or _run_daily_pipeline
    try:
        runner()
    except Exception as exc:  # noqa: BLE001 - report and let the user retry manually
        print_fn(f"Raccolta non riuscita: {exc}")
        print_fn("Puoi riprovare con: python -m src.main run-daily")
        return
    print_fn("Raccolta completata.")


def _run_daily_pipeline() -> Any:
    from src.pipeline.daily_pipeline import DailyPipeline

    return DailyPipeline().run(dry_run=False)
