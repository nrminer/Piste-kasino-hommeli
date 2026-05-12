# Auditor's Ledger Casino — Game Spec Pack

## Original Problem Statement
"INTEGRATE THIS INTO THE CURRENT POINT SYSTEM DO NOT REMOVE THE DATABASE."
"This" = the 3D Blackjack v1 described in /app/plan.md (React Three Fiber + provably-fair RNG + bet slider + full Blackjack rules + fairness verification).
"Current point system" = existing Flask + SQLite casino app (`/app/app.py`, ~3,200 lines, `/app/casino.db`) with a `players.points` wallet and existing 2D Blackjack at `/api/points/<pid>/blackjack/*`.

## Architecture
- **Stack preserved**: Flask + SQLite (`casino.db`). NO database removal. NO Mongo/FastAPI swap.
- **Integration approach**: drop-in Flask Blueprint at `/app/blackjack3d.py` exposes new `/api/blackjack3d/*` routes; legacy 2D Blackjack untouched.
- **Wallet integration**: re-uses existing `_atomic_deduct_points` / `_add_points` from `app.py`, so 3D rounds debit/credit the same `players.points` column and emit `point_transactions` audit rows.
- **Persistence**: new `blackjack3d_games` table (idempotent `CREATE TABLE IF NOT EXISTS` migration on import). Legacy `blackjack_games` left intact.
- **Frontend**: new standalone template `/app/templates/blackjack3d.html` (vanilla JS + Three.js v0.160 from CDN — no React, matches existing tech stack). Reads login from same `cust_player_id` / `cust_player_name` / `cust_player_pw` localStorage keys used by `/asiakas`, re-auths via `/api/customer/login`.
- **Provably-fair RNG**: HMAC-SHA256 keyed by `server_seed`, byte stream feeds rejection-sampled uint32 Fisher-Yates shuffle. Commit (`SHA-256(server_seed)`) returned at round start, full `server_seed` revealed after settlement. Deterministic — same `(server_seed, client_seed, nonce, decks)` rebuilds identical shoe (verified by automated test).

## User Personas
- **Existing customer (Finnish casino patron)**: logs in at `/asiakas`, can now click "✨ Pelaa 3D Blackjack →" to launch the 3D experience with their existing point balance.
- **QA/Compliance**: can verify each round via SHA-256(server_seed) == server_seed_hash and re-derive the exact shoe from the revealed seeds.
- **Operator**: no operational changes — same SQLite DB, same admin endpoints, new game variant fully observable in `point_transactions` audit log.

## Core Requirements (this session)
- 3D rendering with cards, table felt, gold inlay, chip-denomination buttons
- Bet slider + denomination chips + animated deal sequence
- Hit / Stand / Double / Split (multi-hand) / Surrender / Insurance — all integrated with points wallet
- Three rule presets: Vegas Strip (S17), Vegas Classic (H17), European (NHC)
- Basic-strategy hint (toggle in settings)
- Settings drawer: preset, sound, color-blind mode, reduced motion, hint
- Fairness verification modal with client-side SHA-256 check
- Result overlay (Voitto / Häviö / Tasapeli / Blackjack / Antautuminen)
- Sound via WebAudio (no asset files)
- Keyboard shortcuts (H / S / D / P / R / I / N)
- WCAG-friendly badges, focus rings, reduced-motion path

## Implemented (this session — 2026-05)
- 2026-05: **3D Blackjack module integrated into existing Flask + SQLite point system**.
  - **Backend** (`/app/blackjack3d.py`, ~700 lines):
    - Provably-fair RNG (HMAC-SHA256 byte stream → rejection-sampled uint32 Fisher-Yates).
    - Blueprint registers 5 endpoints + 1 HTML route:
      - `GET  /blackjack3d` (template)
      - `GET  /api/blackjack3d/presets`
      - `POST /api/blackjack3d/round/start`
      - `POST /api/blackjack3d/round/<gid>/action`
      - `GET  /api/blackjack3d/round/<gid>`
      - `POST /api/blackjack3d/round/<gid>/reveal`
      - `GET  /api/blackjack3d/round/<gid>/hint`
    - Multi-hand state machine (splits up to 4 per preset).
    - Surrender / Insurance / Double Down / Dealer peek and NHC variants.
    - Basic strategy table for hint endpoint.
    - Idempotent SQLite migration for new `blackjack3d_games` table on import.
    - Wallet integration via `_atomic_deduct_points` / `_add_points` from `app.py` (no schema change to `players` or `point_transactions`).
  - **Frontend** (`/app/templates/blackjack3d.html`, ~900 lines):
    - Three.js scene (table felt, gold rail inlay, dealer arc, dealt-card animations, hole-card flip).
    - Card-face textures generated client-side via Canvas (color-blind safe variants).
    - Bet panel + slider + denomination chips with synthesized WebAudio sounds.
    - Action bar with keyboard shortcuts + strategy-hint highlight (toggle).
    - Settings drawer (preset, sound, motion, color-blind, hint).
    - Profile drawer (session stats + logout).
    - Result overlay + Fairness modal with live SHA-256 client-side verification.
    - Reads `/asiakas` localStorage credentials and re-auths via `/api/customer/login` so a single login covers both screens.
  - **Customer page integration** (`/app/templates/customer.html` ~line 803): added "✨ Pelaa 3D Blackjack →" launch button inside the Blackjack game panel.
- 2026-05: **Automated testing**: 13/13 backend pytest tests pass (`/app/backend/tests/test_blackjack3d.py`):
  - Presets endpoint, start round + points deduction, insufficient funds 400, stand/hit/double/surrender flows, SHA-256 commit-reveal verification, deterministic shoe reproduction, basic-strategy hint correctness, wallet integration, legacy 2D regression. Frontend Playwright E2E confirmed (login → deal → stand → result → verify fairness).

## Backlog / Next Tasks
### P0 (none — feature is shipped & green)
### P1
- (Polish) Collapse the redundant natural-BJ resolution branches in `blackjack3d.py round_start` into one explicit branch table for clarity (testing agent code-review note — no functional impact).
- (Polish) Improve table felt texture realism (procedural noise pattern, vignette).
- (Polish) Show running balance + per-hand chip stacks on the 3D table itself (currently 2D HUD overlay).
### P2
- Add Baccarat + War in the same module pattern (3D scenes + provably-fair).
- Per-player session log endpoint that lists all `blackjack3d_games` rows with summarized P&L.
- WebSocket "multi-player" table view (server-arbitrated like existing poker module).

## Files (changed this session)
- `/app/blackjack3d.py` — NEW Flask Blueprint module (provably-fair RNG + state machine + routes).
- `/app/app.py` — 4-line hook at the bottom registering the blueprint and running migrations.
- `/app/templates/blackjack3d.html` — NEW 3D Blackjack page (Three.js + vanilla JS).
- `/app/templates/customer.html` — 1 new launch link inside `gpanel-blackjack`.
- `/app/backend/tests/test_blackjack3d.py` — NEW (added by testing agent).

(Iteration 1–3 prior state — slot / card-games spec packs + 2D Blackjack — left untouched.)
