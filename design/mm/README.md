# Design system snapshot (Data-crisp)

This directory is a versioned local snapshot of the author's "Data-crisp"
design system. The upstream repository is private and unreachable to anyone
cloning this project: the snapshot is self-contained by design, so the app
builds and renders with no external dependency.

## Contents

- `tokens.css`: color, typography, spacing, radius, and light/dark theme tokens.
- `ui-kit.css`: `mm-`prefixed components built on the tokens (buttons, cards,
  badges, tables, the dashboard shell/rail/page-head/stat-row). Contains **no
  global selectors** — inert until a page uses an `mm-` class; page resets live
  under `.mm-app`.
- `app-logo.svg`: brand-neutral Signal Desk mark, used by the dashboard.
- `logo.svg`: the author's personal monogram (kept for reference; the
  generated dashboard does not use it).
- `fonts/`: local Inter variable fonts and license.

## Usage

Use `tokens.css` as the visual source of truth and `ui-kit.css` for shared
components. Product-specific CSS should map its existing components to the
`mm-` classes or, failing that, to the token variables — never introducing
local color or spacing values. Email HTML cannot depend on CSS files or web
fonts: it uses an email-safe inline translation of the same semantic colors.

The dashboard generator must keep its generated HTML usable from this repository
without requiring the upstream checkout.

## Updating

Do not edit this snapshot to invent product-specific tokens. Update the source
design system first, then refresh this directory and update `VERSION`.
