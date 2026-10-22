# Sources backlog

Official sources we want in Signal Desk but cannot collect from yet. They were
removed from the catalogue so the Fonti page shows only sources that work; this
file is the record of what is missing and why, so the work is not lost.

Re-add one by putting its entry back in `config/sources.yaml` with `enabled: true`
once the problem below is solved, and verify with `python -m src.main collect --dry-run`.

## Broken: the feed or the page no longer works

These were verified working at some point and are not any more. Each needs a new
endpoint, or an extractor that copes with the page as it is served today.

| id | Source | URL | Why it fails |
|---|---|---|---|
| `amplitude_blog` | Amplitude Blog | <https://amplitude.com/feed> | RSS URL returned 404. No verified public feed found. Feed URL aggiornato e verificato. |
| `microsoft_ai_blog` | Microsoft AI Blog | <https://blogs.microsoft.com/feed> | feed returned 410 Gone (blogs.microsoft.com/ai/feed dismesso). Da riattivare con un URL RSS valido. Feed URL aggiornato e verificato. |
| `huggingface_trending` | Hugging Face Trending | <https://huggingface.co/models?sort=trending> | public trending page is HTML and API query returned 400 locally; avoid scraping until a stable endpoint is verified. |
| `product_hunt_ai` | Product Hunt AI | <https://www.producthunt.com/topics/artificial-intelligence> | returned 403 locally and broad launch pages are noisy. |
| `perplexity_docs_changelog` | Perplexity Docs Changelog | <https://docs.perplexity.ai/changelog> | reachable official docs page, but current extraction returns API/navigation links instead of clean changelog items; no RSS verified. |
| `runway_research` | Runway Research | <https://runwayml.com/research> | official research page is reachable, but extraction includes generic publication/navigation links and no verified RSS was found. |
| `runway_news` | Runway News | <https://runwayml.com/news> | official page is reachable, but current HTML extraction returns product/navigation links and no verified RSS was found. |
| `adobe_firefly_blog` | Adobe Blog - Firefly | <https://blog.adobe.com/> | official blog landing page exposes CaaS/navigation links, not reliable Firefly article cards. |
| `udio_blog` | Udio Blog | <https://www.udio.com/blog> | DNS/connection failures in local tests; Help Center changelog was inspected but is not a clean recurring source. |
| `udio_help_changelog` | Udio Help Changelog | <https://help.udio.com/en/articles/10748731-changelog-what-s-new-with-udio> | official help changelog is reachable, but extraction produces a single help article rather than recurring news items. |
| `eleuther_blog` | EleutherAI Blog | <https://blog.eleuther.ai/> | feed `index.xml` returns 404 while the site is up; find the new feed URL. |

## Never built: no feed found, extraction never validated

Reachable official pages with no RSS endpoint we could verify. Each needs a
source-specific extractor, written and checked against the live page.

| id | Source | URL | What is missing |
|---|---|---|---|
| `cursor_changelog` | Cursor Changelog | <https://www.cursor.com/changelog> | official page exists but requires source-specific validation before enabling HTML extraction. |
| `gemini_api_updates` | Gemini API Updates | <https://ai.google.dev/gemini-api/docs/changelog> | docs changelog requires source-specific extraction before enabling. |
| `windsurf_changelog` | Windsurf Changelog | <https://windsurf.com/changelog> | official page reachable but not enabled until extractor quality is validated. |
| `meta_ai_blog` | Meta AI Blog | <https://ai.meta.com/blog/> | official page exists, but no verified RSS/feed endpoint was found and generic HTML extraction is not yet validated. |
| `hermes_agents_ecosystem` | Hermes Agents | <https://github.com/> | project identity/feed is ambiguous; avoid noisy scraping until a canonical source is configured. |
| `openclaw_github` | OpenClaw | <https://github.com/openclaw> | requested builder ecosystem source, but no verified stable project feed or repository target was identified. |
| `firebase_studio` | Firebase Studio | <https://firebase.studio/> | product page only; monitor via Google AI/Firebase sources after a stable feed is verified. |

## Not a news source as configured

The page loads and parses, but it is documentation or a product landing page:
there is no stream of dated articles to collect. Each needs a different URL —
a changelog or newsroom for the same product — rather than a better extractor.

| id | Source | URL | Why it does not fit |
|---|---|---|---|
| `bolt_new` | Bolt.new | <https://bolt.new/> | product page is not a clean recurring source. |
| `claude_code` | Claude Code | <https://docs.anthropic.com/en/docs/claude-code> | documentation page is not a recurring news source. |
| `firebase_ai` | Firebase AI | <https://firebase.google.com/docs/ai-logic> | documentation page is not a recurring source. |
| `google_labs` | Google Labs | <https://labs.google/> | experimental landing page, not a clean recurring news feed. |
| `supabase_ai` | Supabase AI | <https://supabase.com/feed.xml> | product page is not a recurring news source; no verified AI-specific RSS endpoint found. Feed URL aggiornato e verificato. |
| `vercel_ai` | Vercel AI | <https://vercel.com/ai> | product page is not a clean recurring source; no verified AI-specific RSS endpoint found. |
