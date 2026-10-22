# Signal Desk — Technical reference

Detailed reference for developers and advanced users. For installation and
everyday use, see the [README](../README.md).

Signal Desk is a local-first Python application for collecting official AI news,
normalizing and classifying it, and generating useful daily outputs.

## V1.5 Intelligence Layer

- Enabled sources are limited to locally verified official RSS feeds or HTML pages with usable article cards.
- Every source defines `allowed_domains`; collected URLs outside those domains are rejected before storage.
- The shipped catalogue holds only sources we can collect from. Ones that cannot be collected yet — dead feeds, extractors that return navigation instead of articles, product pages with no article stream — are recorded in [SOURCES-BACKLOG.md](SOURCES-BACKLOG.md) with what each one needs, and a test keeps the two lists from overlapping.
- Sources are managed in `data/sources.json` (add/edit/remove from the dashboard's Fonti section, or edit the file directly); disabled sources remain documented there with notes explaining the reason. `config/sources.yaml` is the historical seed file — it is migrated into `data/sources.json` once, automatically, on first run, and is no longer read afterward.
- In the dashboard's **Fonti** tab, **Tutte le fonti** shows the full catalogue without run statistics. Select a run to see only its collected sources, with found/new counts, outcomes, and the operational **Nota** for each source. A source later disabled keeps its historical outcome visible but no longer counts as an active error. Search and category/status filters work in both modes; the error filter is available only for a selected run.
- When served locally, **Aggiungi fonte** uses the standard design-system button sizing and opens the source form above the run selector; **Chiudi** clears and closes it. **Modifica** opens the pre-filled form, while canceling or saving successfully closes it. The form and its toggle remain hidden in static exports.
- Collected items preserve the original URL and include `clean_title`, `excerpt`, `raw_content`, `content_quality`, and `date_quality`.
- The HTML collector reads dates on both extraction paths. The `<article>` path takes a `<time>` element; the link-based fallback, used by sources whose cards are plain divs, looks for a `<time>` in the small block around the link and otherwise for a date written in the card's own text. Human dates such as `Sep 18, 2026` are normalised to ISO before being emitted — the pipeline's date parser does not accept them, so an un-normalised date is as good as none. The date search stops at the card: a block holding more than one link is a grid, and borrowing its `<time>` would date every sibling with someone else's date.
- An article URL normally needs two path segments (`/news/<slug>`), which is what keeps `/pricing` and `/about` out. A card carrying a real publication date is accepted whatever its depth, so a lead story at a top-level path — anthropic.com announces new models at `/claude-opus-5-5` — is collected. In-page anchors are rejected on their own: the source page plus a fragment is navigation, not an article.
- Collection is limited to the configured lookback window, currently 7 days.
- Non-dry-run collection records read URLs in `data/read_urls.json` and skips them in later runs.
- Processed summaries use deterministic extractive cleanup from article excerpts/content and remove marketing or company boilerplate.
- Processed items include one primary category, up to three tags, `relevance_score`, `summary_quality`, `why_it_matters`, `possible_action`, `primary_topic`, `secondary_topic`, and `signal_score`.
- `why_it_matters` and `possible_action` come only from a real per-item LLM insight. The deterministic path leaves them empty rather than writing a pre-written sentence per topic: filler that reads the same across articles is worse than an empty field. Where they are missing the dashboard says which case it is — the analysis has not run on this item (and re-running fills it in), or the AI answered without producing the field — styled as a stated gap, not as content. Each run analyses every item not already cached and published within `lookback_days` (capped at 7), so a run after a few days' pause covers everything new since the last run without ever spending on more than a week of news. An article older than that, or one whose batch failed, would never get an insight: a card with no insight offers **Analizza questo articolo**, which analyses that one item on demand (`POST /api/insight`). Like the deep dives, an analysis that produces nothing is reported as an error and never cached.
- `signal_score` is a local deterministic ranking score derived from relevance, source authority, content quality, recency, and strategic keywords. It is not an external or fake analytics metric.
- The stored `signal_score` freezes its recency bonus at processing time. The dashboard re-applies recency for the day it renders and exposes the result as `display_signal_score`, which is what the cards show and what the ordering uses. `signal_score` itself is never overwritten, so rebuilding the page from the items embedded in the previous one cannot age them twice.
- Ecosystem sources add `source_group`, `signal_type`, and `trend_score` so official model news stays separate from open-source, builder tooling, agentic coding, Google ecosystem, and community signals.
- Conservative ecosystem monitoring is enabled only for stable APIs: a curated GitHub repository monitor and filtered Hacker News Algolia search. No broad GitHub Trending, Reddit, Product Hunt, or fragile HTML scraping is enabled by default.
- Non-dry-run collection updates local trend state in `data/trend_state.json`; dry-run reads existing state but never updates it.
- The daily pipeline generates `data/YYYY-MM-DD/daily_intelligence_summary.json`; dry-run generates `data/YYYY-MM-DD/preview_daily_intelligence_summary.json`.
- Dashboard output excludes invalid/irrelevant items and combines processed news from the current day plus the preceding six days, deduplicated by URL. The window is on the item's own `published_at`, not on the run that collected it: the run folders are walked to find items, but one published outside the window is dropped. Collection applies its own lookback, so merging whole folders used to stack the two windows and leave articles on the page at nearly twice their intended age. An item whose page carries no date is stamped with the date of the run that first saw it — an upper bound, since it cannot have been published after we read it — and marked `date_quality: estimated`, which the pipeline never promotes to `reliable`. Without that bound a dateless item would never leave the window. Items stored before this fall back to their `collected_at` for the same reason. The **Brief** is editorially ordered by `signal_score`, content quality, and date, considers up to ten articles per section for its 3–4 sentence synthesis, and provides compact links to its selected reference articles. **Esplora** and **Salvati** are ordered by publication date, newest first. It includes an intelligence-oriented static UI with sidebar views, search, category, topic, source group, signal type, content quality, signal threshold, trend threshold, date range, and compact-mode filters.
- Dashboard and email outputs show key points when available and exclude invalid or relevance-1 items.
- Dashboard visibility is separated from collection deduplication: already-read URLs are not collected again, but the dashboard merges the latest run with recent processed history so prior signals remain visible.

## Deep dives

A deep dive is a per-URL background job that fetches the source and asks the
LLM for an extended summary. Results are cached in `data/deep_dives.json` and
served instantly on the next click.

Failures are never cached. `_error_dive` marks its payload `failed: True`, the
manager reports `error` with the reason instead of storing it, and the next
click runs the article again. Payloads written by older builds that lack the
marker are ignored at load time, so an article poisoned that way recovers on
the next restart. In the UI every failure ends in a message plus a **Riprova**
button — including a 429 (daily AI budget spent, or too many dives in flight),
which starts nothing and so must not be polled.

## Security notes

A full review of the application, with the threat model and every finding,
is in [SECURITY-REVIEW.md](SECURITY-REVIEW.md).

The local server binds `127.0.0.1` only and has no authentication by design:
it is a single-user app. The controls that do exist:

- **Host/Origin gate on every response**, not only `/api/`. The dashboard page
  embeds the collected items, the source catalogue and the LLM usage history as
  inline JSON, so gating only the APIs would have left the richer target open to
  a DNS-rebound page.
- **`X-Frame-Options: DENY` and `frame-ancestors 'none'`** on every response. The
  Host/Origin gate cannot see UI redress: clicks steered onto a hidden iframe of
  this page produce requests carrying this server's own Host and Origin.
- **Redirect hops are re-validated.** `fetch_capped_text` follows redirects itself
  and runs `is_safe_public_url` on every hop that changes origin, so a source that
  passes the check cannot answer `302 Location: http://169.254.169.254/`.
- **Browser-supplied items are whitelisted** before they reach storage
  (`_sanitize_deep_dive_item`, `_sanitize_saved_item`) — `/api/save` and
  `/api/feedback` included, since feedback re-weights the stored personalisation.
- **The CLI subprocess gets an environment allow-list**, not the parent's whole
  environment, so unrelated secrets are not readable from it.

**Known limitation — the codex provider.** `codex exec` runs model-chosen shell
commands, and `-s read-only` does not confine reads to the `-C` working
directory; there is no flag to disable its shell tool. A prompt injection landing
through collected article text can therefore read local files on a codex install.
Verified empirically. The `claude` provider does not have this exposure: with
`--allowedTools ""` its `Read` tool is refused (also verified). `prompt_safety`
delimits third-party text in both cases, but that is a mitigation, not a boundary.

## V1 Scope

- Collect official AI news.
- Normalize news items into a consistent shape.
- Classify news items.
- Store normalized items locally.
- Generate a daily email newsletter.
- Generate a local HTML dashboard.

## Requirements

- Python 3.9 or later.
- All commands below must be run from the repository root — every path
  (`config/`, `data/`, `outputs/`) is resolved relative to the current
  working directory, not to the script's location.
- The dashboard server and the daily commands run on macOS and Windows.
  On Windows the provider login uses the CLI's own browser/device flow rather
  than a POSIX terminal device.
- Optional but recommended: a local LLM CLI, to get AI-generated insights,
  newsletters, and profile personalization instead of the deterministic
  rule-based fallback. [Claude Code](https://claude.com/claude-code)
  (`npm install -g @anthropic-ai/claude-code`) and Codex CLI
  (`npm install -g @openai/codex`) are supported. Configure or change the
  active provider only in **Profilo → Impostazioni AI**; the choice is saved
  locally and used for insights, briefs, newsletters, and profile
  personalization. The dashboard banner reports the chosen provider's state
  and offers to reconnect that same provider, without changing the selection.
  In the profile you can also set an optional model override; leave it empty to
  use the CLI provider's configured default. Changing provider clears that
  override so an incompatible model name is never sent to the new CLI. Without
  either CLI, the app still runs end to end — every feature has a deterministic
  fallback — just without AI-written summaries.

Codex CLI uses device authorization from the dashboard: it shows a code and
opens the official verification page, rather than relying on a browser callback
to a temporary localhost port.
Its structured responses are read from the CLI's final JSON text event, so the
same Codex configuration works for both batch analysis and **Approfondisci**.

## Install

### Avvio semplice su macOS

Per l'uso quotidiano, anche senza terminale, fai doppio clic su
`scripts/start.command` nel Finder. Al primo avvio lo script crea
automaticamente un ambiente Python isolato in `.venv/` e installa i componenti
necessari; gli avvii successivi aprono direttamente Signal Desk nel browser.
Se `requirements.txt` cambia dopo un aggiornamento, installa automaticamente i
nuovi componenti al successivo avvio.

### Avvio semplice su Windows

Fai doppio clic su `scripts/start.ps1`, scegliendo **Esegui con PowerShell**.
Al primo avvio crea `.venv/` e installa i componenti necessari; poi apre
Signal Desk nel browser. Se Windows blocca gli script scaricati, apri
PowerShell nella cartella del progetto ed esegui:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Entrambi gli script richiedono Python 3.9 o successivo. Se manca, mostrano il
link all'installer ufficiale e possono essere eseguiti di nuovo al termine
dell'installazione.

### Avvio da terminale

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

To also run the test suite, install the dev extra instead (adds `pytest`):

```bash
python -m pip install -r requirements-dev.txt
```

## First run

```bash
python -m src.main setup
```

An interactive wizard that: detects which LLM CLI is installed and checks
whether it's logged in (offering to log in if not); asks for your preferred
application and output language (initially the same; you can separate them in
**Profilo → Impostazioni AI**); asks you to describe yourself in a few lines and — if an
LLM is available — turns that into a structured profile (interests, skills,
keywords) that you review and confirm before it's saved; migrates
`config/sources.yaml` into `data/sources.json` on first use; and offers to
run a first collection so the dashboard has something to show. It's
idempotent — re-running it shows your current settings as defaults — and
safe to skip: every step can be declined, and `serve` will fall back to a
plainer landing page (with a pointer back to `setup`) if you haven't
generated a dashboard yet.

Your profile is not required to start using the app. Without one, items are
still collected, scored, and ranked globally (`signal_score`); only the
personalized "Per te"/"For you" section stays empty until you write a few
lines about yourself — from `setup`, or later from the dashboard's "Profilo"
tab.

## Run

Run the daily pipeline in dry-run mode:

```bash
python -m src.main run-daily --dry-run
```

Or use the Makefile shortcut:

```bash
make run
```

Dry-run writes clearly named preview files under `data/YYYY-MM-DD/preview_raw_news.json`, `data/YYYY-MM-DD/preview_news.json`, `data/YYYY-MM-DD/preview_daily_intelligence_summary.json`, `outputs/dashboard/preview_index.html`, `outputs/email/preview_YYYY-MM-DD.html`, and `outputs/email/preview_YYYY-MM-DD.txt`; it never sends email.

Run the full daily pipeline:

```bash
python -m src.main run-daily
```

The full pipeline collects sources, saves `data/YYYY-MM-DD/raw_news.json`, saves `data/YYYY-MM-DD/collection_report.json`, processes `data/YYYY-MM-DD/news.json`, generates `data/YYYY-MM-DD/daily_intelligence_summary.json`, generates `outputs/dashboard/index.html`, generates `outputs/email/YYYY-MM-DD.html`, generates `outputs/email/YYYY-MM-DD.txt`, and sends no email unless sending is enabled and implemented.

The local dashboard is fully offline: it uses the local design system snapshot in `design/mm/` (`tokens.css`, `app-logo.svg`, local Inter fonts — see "Design system" below) instead of Tailwind or any other CDN, and can be opened directly from `outputs/dashboard/index.html` or `outputs/dashboard/preview_index.html`. Opened as a static file, it has no backend and no authentication: the `Saved` view stores selected source URLs only in the browser `localStorage`, and there is no profile UI. Serve it locally instead (`python -m src.main serve`, see "Serve locally" below) to get saved articles/feedback persisted to disk, the in-app "Analizza" button, LLM login from the browser, and the profile editor.

### Serve locally

```bash
python -m src.main serve --port 8000
```

Starts a local HTTP server (binds `127.0.0.1` only) at the given port
(default 8000) serving the generated dashboard plus its JSON APIs — sources,
saved articles, feedback, newsletters, deep dives, profile, and LLM
login/status. It reads and writes under `data/` on every request, so run it
from the repository root. If no dashboard has been generated yet, it serves
a short landing page pointing you at `python -m src.main setup` (or
`run-daily`, if you'd rather skip the wizard).

### Design system

`design/mm/` is a versioned local snapshot of a design system, copied here so the
app builds and renders with no external/runtime dependency (see `design/mm/README.md`
and `design/mm/VERSION` for provenance — the upstream is a private repository, not
reachable from a clone of this project). Contents:

- `design/mm/tokens.css`: color, typography, spacing, and radius tokens for light and
  dark themes (`:root`/`[data-theme="light"]` and `[data-theme="dark"]`), plus local
  `@font-face` declarations for Inter.
- `design/mm/app-logo.svg`: the brand-neutral mark shown in the dashboard sidebar
  (`logo.svg` is the original author's personal monogram, kept for reference only —
  the generated dashboard does not use it).
- `design/mm/fonts/`: the local Inter variable font files (`.woff2`) referenced by
  `tokens.css` — no Google Fonts or other font CDN.

The generated dashboard links `tokens.css` with a relative path
(`../../design/mm/tokens.css`) that resolves correctly both when the HTML file is
opened directly (`file://`) and when served by `python -m src.main serve`, which
serves `design/mm/*` read-only under `/design/mm/`. Dashboard CSS variables (`--text`,
`--accent`, etc.) are aliased onto the MM tokens rather than hardcoding colors, so the
app inherits the brand palette and theme without duplicating it.

The daily/executive email (`src/outputs/email_output.py`) cannot depend on external
CSS or web fonts — email clients vary too much — so it uses an email-safe, inline
translation of the same semantic colors (table-based layout, no `border-radius`,
`Arial`/`Helvetica` only) instead of linking `tokens.css`.

To update the snapshot: change the source design system first, then refresh
`design/mm/` and bump `design/mm/VERSION` — do not invent product-specific tokens
locally.

The dashboard is organized as a strategic AI intelligence terminal:

- `Top Signals Today`: highest `signal_score` items.
- `High Relevance`: strong signals that deserve review.
- `Worth Watching`: medium-signal updates worth tracking.
- `Ecosystem`: non-foundation-model sources from builder, coding, Google, and community groups.
- `Trending OSS`: curated open-source repository trend signals ranked by `trend_score`.
- `Builder Tools`: builder, agentic coding, and Google ecosystem tooling signals.
- `Emerging Signals`: items with meaningful trend movement.
- `Live Intelligence Stream`: all valid processed items after filters.
- Daily intelligence overview card: headline, main shift, PM takeaway, data takeaway, and suggested focus.
- The filter/control panel can be collapsed and remembers its open/closed state in browser `localStorage`.
- Cards include a `new` or `seen` badge so a run with zero new items can still show recent historical signals.

Collect configured sources in dry-run mode:

```bash
python -m src.main collect --dry-run
make collect
```

The collector dry-run performs live network requests, normalizes items, reports item and error counts, and does not write files.
It applies the same 7-day lookback and read-state skip rules as a normal run, but it does not update `data/read_urls.json`.
It also applies trend scoring against the current local trend state, but it does not update `data/trend_state.json`.

Collect and save raw news locally:

```bash
python -m src.main collect
```

Raw collected items are saved as pretty-printed UTF-8 JSON under `data/YYYY-MM-DD/raw_news.json`.
Collected URLs are also added to the ignored local read-state file at `data/read_urls.json`.
Repository trend snapshots are saved to the ignored local trend-state file at `data/trend_state.json`.

Process today's raw news:

```bash
python -m src.main process
make process
```

Processed items are saved as pretty-printed UTF-8 JSON under `data/YYYY-MM-DD/news.json`.

The executive email output is a two-minute brief:

- `AI Daily Intelligence Brief — YYYY-MM-DD`
- opening daily intelligence summary
- top 3-5 signals with why it matters, possible action, and source link
- strategic themes grouped by topic cluster
- worth watching items
- optional compact low-priority section

Validate today's processed news file:

```bash
python -m src.main validate
```

The validation command checks `data/YYYY-MM-DD/news.json`, confirms every processed item has the core fields plus intelligence/ecosystem fields (`why_it_matters`, `possible_action`, `primary_topic`, `signal_score`, `summary_quality`, `source_group`, `signal_type`, and `trend_score`), and reports content quality, relevance, signal score buckets, trend score buckets, source group/type distribution, summary quality, topic distribution, selected top signals, top trending tools, excluded noise, useful source counts, wrong-domain exclusions, and source/failure counts when a collection report exists.

Run the automated test suite:

```bash
python -m pytest
```

Validate imports and tests together:

```bash
make validate
```

## Configuration

`config/settings.yaml` (tracked, hand-commented in Italian) holds the base
settings: `max_items_per_source`, `lookback_days`, `read_state_path`,
`trend_state_path`, `output_email_enabled`, `output_dashboard_enabled`, and
`llm_analysis_enabled` (requires an LLM CLI — see Requirements — and only
analyzes items not already cached from the last `lookback_days`, at most 7). `language` and
`timezone` are currently unused by any code path.

User-chosen settings that shouldn't rewrite that hand-commented file — the
LLM provider (`llm_provider`), an optional model override (`llm_model`), the
language for generated content (`output_language`, default `it`), and the
application language (`ui_language`, default `it`) —
live in `data/settings_overrides.json` instead (gitignored, merged on top of
`config/settings.yaml` at read time; see `src/settings.py`). `setup` writes
this file for you; it's plain JSON if you'd rather edit it by hand.

The dashboard's **Consumo AI** view records provider-reported input/output
tokens, the provider (**Claude** or **ChatGPT**), the selected model (or
**Predefinito CLI** when none was chosen), and the access mode at execution
time: **API key**, **Account Claude/ChatGPT**, or **Non disponibile** when the
CLI status is ambiguous or unavailable. This identifies authentication, not an
invoice or subscription charge. Credentials and raw authentication responses
are never stored in the usage records. Existing records without a provider,
model, or access mode retain a clear fallback label.

Costs are recorded only when returned by the provider. Missing costs are JSON
`null` and appear as empty cells, while an explicitly reported zero remains
zero. A multi-call run has a cost only when every call reports it. There are no
local price tables, cost estimates, or billing integrations. **Costi disponibili**
sums only available costs and is empty when none are available; it is not a
complete spending total when some records lack costs. Historical records marked
`cost_estimated: true` are excluded from monetary display without rewriting the
stored history.

Token totals preserve each provider's semantics: Claude's separate cache
read/write counters are included in its input total; Codex input/output totals
already include cached/reasoning tokens. Cache and reasoning breakdowns are
also retained in usage records and are not added twice. Existing token history
is not recalculated. These reporting changes do not enable the optional budget cap.

Your profile lives in `data/profile.json` (gitignored — never committed):
`about_me` (free text), `goal`, `interests`, `skills_to_grow`,
`keywords_boost`, and `projects`, used to re-weight items by personal
relevance (`src/processors/profile.py`). Edit it from the dashboard's
"Profilo" tab, regenerate it with `setup`, or edit the JSON directly.
The Profile page uses the same content width and UI kit as the other sections
and keeps every field visible: a full-width personal description
and goal, interests and skills side by side on desktop (stacked below 768 px),
and priority topics. Lists accept commas or line breaks. **Compila i campi con
l’AI** fills a proposal for review; only **Salva profilo** persists it. Provider
and language settings appear at the bottom and use their own save button.
The daily brief header and statistics are hidden while editing the profile;
navigation and the theme switch remain available.
`config/profile.example.yaml` is a non-loaded, illustrative sample — copy it
for reference, not for the app to read.

`config/sources.yaml` is the tracked seed list of RSS/HTML sources,
migrated once into `data/sources.json` on first use (see the "Sources are
managed in `data/sources.json`" note above); add/remove/edit sources from
the dashboard's "Fonti" tab afterward, or edit `data/sources.json` directly.

## Project Structure

- `config/`: source and runtime settings (tracked; `profile.example.yaml` is a
  non-loaded sample).
- `data/`: ignored local data artifacts, including your profile
  (`profile.json`) and settings overrides (`settings_overrides.json`).
- `outputs/email/`: ignored generated email outputs.
- `outputs/dashboard/`: ignored generated dashboard outputs.
- `src/collectors/`: source collection modules for RSS and HTML sources.
- `src/outputs/`: dashboard and email HTML generation modules.
- `src/pipeline/`: daily pipeline orchestration.
- `src/processors/`: rule-based processing, classification, LLM provider
  abstraction (`llm_provider.py`), and profile modules.
- `src/server.py`: the local HTTP server behind `serve` (dashboard + JSON APIs).
- `src/settings.py`: reads/writes the user-overridable settings described
  under Configuration.
- `src/setup_wizard.py`: the interactive `setup` command.
- `tests/`: test suite.

Generated files belong under `data/` and `outputs/`; only `.gitkeep` placeholders are tracked there.

## Development Workflow

- Develop each module in isolation.
- After completing each module, run the code and validate the output.
- Commit after each completed and validated module or major step.
- Keep `main` runnable at all times.
- Do not push broken commits.
- Keep generated files out of Git.

## Git Workflow Rules

- Commit frequently with meaningful messages.
- Do not create large commits.
- Do not rewrite history unless explicitly requested.
- After each major step, create a commit.
- Use conventional commit prefixes: `feat:`, `fix:`, `chore:`, `refactor:`.
- Do not commit sensitive data.
- Keep the repository clean and organized.
