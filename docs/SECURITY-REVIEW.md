# Security review — Signal Desk

**Date:** 21 September 2026 · **Reviewed at:** `5867662` · **Method:** manual code review, read-only, with empirical verification of the LLM findings.

Every finding below was reproduced by a failing test before being fixed. Findings 1 and 3–7 are closed in `6f18657`; finding 2 is partially mitigated and partially documented as a known limitation.

## Threat model

Signal Desk is local-first: the server binds `127.0.0.1`, there is one user, and there is no authentication by design. Three attackers are realistic:

1. **A hostile website** the user visits while the server is running — CSRF, DNS rebinding, clickjacking.
2. **A compromised RSS/HTML source** — collected content is untrusted by definition and reaches both the page and the LLM prompts.
3. **An unprivileged local process** — a postinstall script, a second account on the machine.

## Overall posture

Good, and above average for a personal project: the defences carry comments explaining *why* they exist. Verified working before this review:

- No `shell=True`, `eval`, `exec` or `pickle`; YAML always via `safe_load`; subprocess always with a list argv.
- Path traversal on `/design/mm/*` correctly closed (`resolve()` plus an ancestry check).
- CSRF and DNS rebinding refused on the APIs via a `Host`/`Origin` check.
- XSS: escaping applied consistently on both the Python and JS sides; `_safe_href`/`safeHref` reject `javascript:` URLs; `_escape_json_for_script` neutralises `<` inside inline JSON.
- SSRF: `src/net_guard.py` blocks loopback/private/link-local before any server-side fetch.
- Prompt injection: `src/processors/prompt_safety.py` fences third-party text with a per-invocation random nonce.
- 1 MB request body cap, `0600` permissions on `data/`, no secrets committed.

---

## Findings

### 1. HIGH — The dashboard page was served without the `Host` check ✅ fixed

`src/server.py` — the gate covered only `/api/`, while `GET /` did not. That page is not an empty shell: it embeds `news-data`, `usage-data`, `sources-catalog`, `sources-data` and `daily-summary-data` as inline JSON.

A hostile site with a 1-second-TTL DNS record loads `http://evil.example:8000/`, re-points the name at `127.0.0.1`, and re-fetches `/`. The browser sends `Host: evil.example:8000` — which the gate would refuse, except it was never consulted for `/`. The whole article history, source catalogue and AI cost history follow.

The personal profile was **not** exposed this way (it is read through `/api/profile`, which was gated). The same gap, with a smaller payload, applied to `/design/mm/*`.

**Fix:** the gate now runs at the top of `do_GET`, before every route.

### 2. MEDIUM-HIGH — LLM tool access ⚠️ partly mitigated, partly a known limitation

The comment on `--allowedTools ""` claimed it forbade all tools. Both provider branches were tested empirically against a decoy file:

- **Claude** — the `Read` tool was **refused** (`permission_denials` in the CLI output). The flag works in practice; this branch is not exposed.
- **Codex** — **the decoy leaked.** `codex exec -s read-only` ran `/bin/zsh -lc "sed -n '1,200p' …/canary.txt"`, read a file **outside** its `-C` working directory, and returned the contents in the structured answer.

There is no way to disable the codex shell tool: `sandbox_permissions=[]` does not help, and `tools.shell`, `features.shell` and `tools.exec` are all rejected as unknown configuration keys. So a prompt injection arriving through collected article text can read local files on a codex install.

**Mitigated:** the CLI subprocess now receives an environment **allow-list** instead of the parent's whole environment, so `GITHUB_TOKEN`, `AWS_*`, database URLs and the like are no longer readable from it. Both CLIs still authenticate after the change.

**Documented:** the limitation is stated in `TECHNICAL.md` and, in plain language, in both READMEs at the point where the user picks a provider. The provider remains the user's choice — the app does not override it.

### 3. MEDIUM — `/api/save` and `/api/feedback` persisted arbitrary JSON ✅ fixed

The only validation was a non-empty `url`; the caller's dict was then stored verbatim, while the deep-dive route beside them whitelisted nine fields with length caps. Beyond unbounded file growth, `record_feedback` updates the weights in `data/preferences.json`, so the personalisation could be poisoned into favouring an attacker's sources.

**Fix:** a new `_sanitize_saved_item` — the deep-dive whitelist widened to the fields the card actually renders — applied to both routes.

### 4. MEDIUM — No anti-framing headers ✅ fixed

`_send` is the single response path and emitted two headers. A hostile site could frame `http://127.0.0.1:8000/` invisibly over a decoy and steer clicks onto the real controls; the resulting requests originate *inside* the legitimate frame and therefore carry the correct `Host` and `Origin`. The gate is structurally blind to UI redress.

**Fix:** `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'` on every response.

### 5. MEDIUM — The SSRF guard was bypassable with an HTTP redirect ✅ fixed

`requests.get` defaults to `allow_redirects=True`, and the redirect target was never re-validated: the guard ran once, on the pre-redirect URL. A source operator could publish an item whose link passes both `is_allowed_domain` and `is_safe_public_url`, then answer `302 Location: http://192.168.1.1/`. The extracted text becomes the item's content, is persisted, rendered, and sent to the LLM.

The test proved it: `requests` genuinely attempted a connection to `169.254.169.254`.

**Fix:** `fetch_capped_text` follows redirects itself and re-runs `is_safe_public_url` on every hop that **changes origin**. Same-origin hops are not re-checked — the origin was already approved, and re-checking would break the relative-`Location` redirects ordinary sites serve.

### 6. LOW — Two `esc()` helpers with different guarantees ✅ fixed

The page defined `esc` twice. The local one round-tripped through `textContent`/`innerHTML`, which escapes `&`, `<` and `>` but **not** quotes — and it was used in attribute position for the login URL, which is scraped from the CLI's PTY output by a regex whose `\S+` matches a quote.

**Fix:** one `esc` (the quote-escaping one), the login fallback link routed through `safeHref`, and the last raw attribute interpolation (the sources date dropdown's `value="…"`) escaped.

### 7. LOW — Prefix routing on four routes ✅ fixed

Four routes matched on the raw path prefix while the other nineteen compared the parsed path exactly, so `POST /api/runner` reached the pipeline handler — confirmed by a test that got `202` and actually started a run. No privilege boundary was crossed, but future routes under those prefixes would have been silently shadowed.

**Fix:** exact `urlparse(...).path` comparison on all four.

---

## Not classified as a vulnerability: the absence of authentication

Any local process can `curl` with `Host: 127.0.0.1`, pass the gate, read the profile and history, and start LLM subprocesses.

This is a declared design choice (`src/server.py`) and coherent with the product: a single-user app on loopback. On the same machine, a hostile process running as the same user can already read `data/*.json` directly, so a token would add little against that attacker. It remains the most effective mitigation if the server were ever exposed beyond loopback, or to defend against a process running as a *different* user: a random per-boot token, printed at startup and required on every request.

## Verification

```bash
python -m pytest                                   # 500 tests
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example:8000' http://127.0.0.1:8000/   # 403
curl -sI http://127.0.0.1:8000/ | grep -i frame                                                # DENY
curl -s -o /dev/null -w '%{http_code}\n' -X POST -d '{}' http://127.0.0.1:8000/api/runner       # 404
```

## If this is repeated

The `javascript:` defence is implemented four times across the codebase (`dashboard_output._safe_href`, the JS `safeHref`, `email_output._safe_href`, and implicitly in `_is_wrong_domain`). Findings 6 and the dropdown escaping were symptoms of that duplication rather than independent mistakes — consolidating it would remove a recurring class of defect rather than one instance.
