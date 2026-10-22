# AGENTS.md

## Project

AI News Intelligence Agent is a local-first Python application for collecting official AI news, processing it, and generating:

1. a daily email newsletter
2. a local HTML dashboard
3. future pluggable outputs, such as LinkedIn posts

## Product Scope

V1 scope:

- collect official AI news
- normalize news items
- classify news items
- store normalized items
- generate a daily email newsletter
- generate a local HTML dashboard

V2 scope later:

- impact scoring
- overall daily insight for a Product/Data Analyst profile

## Architecture Rules

- Keep the architecture modular.
- Build in small, testable increments.
- Do not over-engineer.
- Prefer clear functions over clever abstractions.
- Develop modules in isolation where practical.
- When modifying code, avoid rewriting unrelated files.
- Always update `README.md` when behavior changes.

## Data And Source Rules

- Every news item must always preserve the original source URL.
- Prefer explicit, inspectable data formats.
- Keep generated files out of Git.

## Tooling Constraints

- Do not use Notion.
- Avoid paid third-party services.
- Prefer Python, JSON, YAML, and SQLite only if needed.

## Tech Preferences

- Python
- `feedparser`
- `requests`
- `beautifulsoup4`
- `pyyaml`
- `jinja2` optional
- `pytest` optional

## Development Workflow

- Keep the project runnable from `main`.
- Do not push broken commits.
- Run the relevant module after changing it.
- Validate output before committing.
- Commit frequently with meaningful conventional commit messages.
