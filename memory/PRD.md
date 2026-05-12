# Auditor's Ledger Casino — Game Spec Pack

## Original Problem Statement (latest session, 2026-05)
"Redo the 3d blackjack, baccarat, pikapokeri, war. Make it 3D and redo the frontend. Also match the customers theme with operators theme"

User choices (verbatim from `ask_human`):
- Build the new frontend on the existing codebase (don't replace the DB).
- Pikapokeri = Finnish-style Video Poker (Jacks or Better).
- 3D engine: Three.js (via CDN ESM, no React).
- Currency: internal points system — operators credit player accounts.
- Theme matching: customer game UI and operator panel share the SAME theme.

## Architecture
- **Stack preserved**: Flask + SQLite (`/app/backend/casino.db`). NO DB removal. NO FastAPI/Mongo swap.
- **Backend modules** (existing):
  - `/app/app.py` — main Flask app (~3.2k lines) — game endpoints, points, players, audit.
  - `/app/blackjack3d.py` — Blueprint with 6-deck Shoe class + admin endpoints.
  - `/app/operator_auth.py` — operator login + `@op_required` (PyJWT HS256).
  - `/app/backend/server.py` — FastAPI/WSGIMiddleware wrapper for Emergent supervisor.
- **NEW theme system (this session)**:
  - `THEME_DEFAULTS` dict + `_load_theme()` in `app.py`.
  - `GET  /api/theme` (public) → returns full theme dict.
  - `PUT  /api/operator/theme` (op-protected) → persists `theme_*` keys to `system_settings`.
  - 12 overridable keys: brand_name, tagline, logo_text, logo_url, primary, accent, bg, surface, text, muted, danger, felt.
- **Frontend (completely rewritten this session)**:
  - `/app/static/css/theme.css` — shared CSS variables + base components (Cormorant Garamond display, Outfit body, JetBrains Mono).
  - `/app/static/js/casino_3d.js` — Three.js scene module: `createTableScene(container, opts)` returns `{ dealCard, flipCard, replaceCard, setChipStack, clearTable, refreshTheme, dispose, getCardCount }`. Cards are BoxGeometry with CanvasTexture faces; chips are stacked Cylinders; cinematic key/rim/ambient lighting with PCF soft shadows; cards deal via quadratic Bezier with easeOutCubic; flip via Y-axis rotation.
  - `/app/templates/customer.html` — new SPA (~1100 lines): splash → lobby with 4 bento tiles → in-game screens for Blackjack / Baccarat / Pikapokeri / War, each with its own 3D scene, HUD overlays, shared chip bet pad, and result banner. ESM script that imports `/static/js/casino_3d.js`.
  - `/app/templates/operator.html` — new operator panel (~600 lines): splash → sidebar nav (Yleiskuva / Asiakkaat / Brändäys / Aktiviteetti / Pakat) → theme editor with live preview + color pickers, customer CRUD + points +/− modal, audit/activity log, shoe management.
- **Theme bridge**: both templates fetch `/api/theme` on boot and write to `document.documentElement.style.--xxx`. The 3D scene reads the live CSS values (`getComputedStyle`) so changing the operator theme repaints felt color, chip stripes, and frame highlights everywhere.

## User Personas
- **Customer**: logs in at `/asiakas` with name + password (created by an operator). Sees lobby with 4 game tiles, balance pill, and recent-rounds log. Plays Blackjack / Baccarat / Pikapokeri / War with full 3D card animations.
- **Operator**: logs in at `/operator` with the env-var password. Manages customers (CRUD + grant/deduct points), edits the platform theme (immediately reflected on customer UI), browses BJ rounds and audit events, resets blackjack shoes.

## Core Requirements (this session)
- Three.js 3D rendering for all four games (cards dealt with Bezier arc + flip; felt + chip stack)
- Full customer SPA rewrite with shared design tokens
- Operator panel redesigned to MATCH the customer aesthetic exactly
- Operator-configurable shared theme (12 keys, persisted in SQLite)

## Implemented this session (2026-05)
- **Backend additions** (`app.py`):
  - `THEME_DEFAULTS` dict (12 keys).
  - `_load_theme(db)` helper.
  - `GET /api/theme` (public).
  - `PUT /api/operator/theme` (manual op-required validation; whitelists keys; trims to 200 chars).
- **NEW frontend** (replaces 3.8k-line `customer.html` and 367-line `operator.html`):
  - `/app/static/css/theme.css` (560 lines)
  - `/app/static/js/casino_3d.js` (495 lines)
  - `/app/templates/customer.html` (1107 lines)
  - `/app/templates/operator.html` (586 lines)
- **`.env`** created with `OPERATOR_PASSWORD=admin123`, `OPERATOR_TOKEN_SECRET=...`, `OPERATOR_TOKEN_TTL_MIN=120`.
- **Test coverage** via testing agent iter12: 15/15 pytest backend pass, Playwright frontend 4/4 games dealt 3D, operator 5 tabs validated.

## Files (touched this session)
- NEW `/app/static/css/theme.css`
- NEW `/app/static/js/casino_3d.js`
- REWROTE `/app/templates/customer.html`
- REWROTE `/app/templates/operator.html`
- UPDATED `/app/app.py` (added theme dict + 2 endpoints)
- NEW `/app/.env`
- NEW `/app/backend/tests/test_theme_and_3d_games.py`
- UPDATED `/app/memory/test_credentials.md`

## Backlog / Next Tasks
### P1
- Move the result banner OUT of the `.scene-3d` wrapper so it sits above the table instead of overlapping cards (minor cosmetic, banner is still readable thanks to z-index 5).
- Add scene `.dispose()` on screen-leave to free WebGL contexts (current code reuses scenes, fine for now).
- Operator theme: add a "Preview as customer" link that opens `/asiakas` in a new tab with the live theme.
- Add caching headers + ETag to `GET /api/theme`.
### P2
- Split `/app/app.py` (3.3k lines) into a `games` blueprint.
- Add a "Quick deal" mode (skip animation) for impatient players.
- Theme presets dropdown ("Minimal-Luxe", "Vegas Red", "Cyber Neon") for one-click operator branding.

## Smart enhancement (next worth doing)
**Operator-defined per-VIP theme overlays** — let the operator pick a slightly different primary color for Whale/Gold customers (e.g., Whale = purple accent). On `/api/customer/login` we already return `vip_level`; the customer SPA could apply `--primary` per VIP. This costs the operator nothing extra, makes high-rollers feel special, and gives the operator a soft retention/upsell lever ("upgrade your tier to unlock a custom table look").
