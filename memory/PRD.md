# Auditor's Ledger Casino — Game Spec Pack

## Original Problem Statement (consolidated through 2026-05)
"INTEGRATE THIS INTO THE CURRENT POINT SYSTEM DO NOT REMOVE THE DATABASE." (3D Blackjack from plan.md → inline into existing Flask/SQLite casino). Subsequent refactors removed the provably-fair flow, introduced a 6-deck cut-card shoe, added an env-password-protected operator panel, and unified the bet-pad UX across every game.

## Architecture
- **Stack preserved**: Flask + SQLite (`/app/casino.db`). NO database removal. NO FastAPI/Mongo swap.
- **Modules**:
  - `/app/app.py` — main Flask app (legacy, ~3,200 lines)
  - `/app/blackjack3d.py` — Blueprint with the Shoe class + game routes + admin endpoints
  - `/app/operator_auth.py` — Blueprint with `/api/operator/login` + `@op_required` decorator (PyJWT HS256)
- **Auth**: customer login already existed (`/api/customer/login`, name+password). Operator login is a separate flow: env-var password → short-lived JWT (HS256, 60-min TTL by default) → Bearer header on admin endpoints. No hard-coded secrets — `OPERATOR_PASSWORD` and `OPERATOR_TOKEN_SECRET` come from `/app/.env` (loaded via python-dotenv at boot).
- **Wallet integration**: every game (3D Blackjack, WAR, Baccarat, Pikapokeri, Slots, Coinflip) debits/credits the same `players.points` column through the existing `_atomic_deduct_points` / `_add_points` helpers and emits `point_transactions` audit rows.
- **Shoe model** (post-refactor): persistent 6-deck shoe per player in the new `blackjack3d_shoes` table. `random.shuffle` initially; cut-card index at 75% (`SHOE_CUT_FRACTION` env). When the cut is reached during a round, the current hand resolves with the same shoe and the shoe auto-reshuffles before the next round. Operator can also force a reshuffle.

## User Personas
- **Customer**: logs in at `/asiakas`, plays Coinflip / WAR / Baccarat / Blackjack / Pikapokeri / Slots — all sharing the same chip-row + slider bet pad. Sees a small shoe-status pill under the Blackjack table indicating cut-card progress and number of shuffles.
- **Operator**: visits `/operator`, enters the `OPERATOR_PASSWORD` from the server's env, gets a JWT, and lands on an admin dashboard with stats, shoe state per player (reset buttons), Blackjack settings, and the 50 most recent rounds.

## Core Requirements (this session)
- 3D Blackjack with realistic deal/flip animations, visible chip stack on the felt
- 6-deck shoe with cut-card reshuffle (configurable cut fraction)
- Unified bet pad across all card games
- Operator panel protected by env password (no hard-coded secrets)
- Customer & operator panels share visual theme/components
- Wallet authoritative on server; clients only send intents

## Implemented (history)
- 2026-05 (initial): Provably-fair 3D Blackjack module + standalone `/blackjack3d` page + customer integration.
- 2026-05 (follow-up): Inlined 3D Blackjack into `/asiakas` Blackjack panel; visible 3D chip stack scaling with bet.
- **2026-05 (current)**: Major refactor per the operator-panel spec:
  - **REMOVED** provably-fair / HMAC-SHA256 flow everywhere. `/api/blackjack3d/round/<id>/reveal` deleted (404). Front-end fairness modal + verify button deleted. Standalone `/blackjack3d` page deleted (404). `customer.html` Blackjack panel HUD now says "Blackjack · 6 pakkaa".
  - **6-deck cut-card shoe** (`Shoe` class in `/app/blackjack3d.py`) with `random.shuffle`. Persisted per player in new `blackjack3d_shoes` SQLite table. Reshuffles automatically when cut card is reached (current round always resolves with the existing shoe first).
  - **Operator auth** (`/app/operator_auth.py`): PyJWT HS256, `OPERATOR_PASSWORD` + `OPERATOR_TOKEN_SECRET` from `/app/.env`. Endpoints: `POST /api/operator/login`, `GET /api/operator/me`. `@op_required` decorator returns 401 on missing/invalid token. Constant-time password compare via `hmac.compare_digest`.
  - **Operator admin endpoints** (Blueprint inside `/app/blackjack3d.py`):
    - `GET  /api/operator/blackjack/stats`
    - `GET  /api/operator/blackjack/settings`
    - `GET  /api/operator/blackjack/shoes`
    - `POST /api/operator/blackjack/shoes/<pid>/reset`
    - `POST /api/operator/blackjack/shoes/reset_all`
    - `GET  /api/operator/blackjack/recent_rounds`
    - All gated by `@op_required`.
  - **New `/operator` page** (`/app/templates/operator.html`): theme-matched login splash + dashboard (6 stat tiles, settings overview, per-player shoe table with progress bars + reshuffle buttons, last 50 rounds). Token stored in `localStorage` under `operator_token`; auto-validates via `/api/operator/me` on page load.
  - **Unified bet pad** (CSS `.chip-row` + `.chip-btn`) applied to **all five customer game panels**: Coinflip, WAR, Baccarat, Blackjack, Pikapokeri. Each has the same slider + 25/100/500/1K denomination chip buttons + "Tyhjennä" clear. Helper functions `betAdd(inputId, delta)` and `betClear(inputId)` are reusable across panels. A global `input`-event listener keeps the slider and number input in sync for every game that follows the `*-bet` / `*-bet-slider` naming convention.
  - **Shoe status pill** in the Blackjack panel: `🃏 6 pakkaa · X% käytetty · cut 75% · sekoituksia N`. Switches to a gold warning ("Lähestyy leikkauskorttia") when within 15% of the cut, and "🔀 Pakat sekoitetaan ennen seuraavaa jakoa" right after a reshuffle event.
  - **Env file** `/app/.env` created (with `.env.example` next to it documenting the variables). No secrets committed in code paths.
- 2026-05: **Automated testing** (iteration_11.json): 20/20 backend pytest pass + Playwright frontend E2E across `/asiakas` and `/operator` confirms every flow above. Cut-card reshuffle observed across a 40-round stress test (shuffles incremented). Operator admin endpoints reject unauthenticated requests; correct password yields a working JWT.

## Files (touched this session)
- NEW `/app/operator_auth.py`
- REWROTE `/app/blackjack3d.py` (provably-fair → cut-card shoe, added admin endpoints)
- UPDATED `/app/app.py` (registers both blueprints)
- UPDATED `/app/templates/customer.html` (fairness UI deleted, shoe-status pill added, unified chip-row on all 5 games)
- NEW `/app/templates/operator.html`
- DELETED `/app/templates/blackjack3d.html` (standalone page removed — UI now lives inline on `/asiakas`)
- NEW `/app/.env` and `/app/.env.example`

## Backlog / Next Tasks
### P1 (recommended polish)
- Split `/app/blackjack3d.py` (~1060 lines) → `shoe.py` + `routes.py` (testing agent code-review note, not blocking).
- Extract Blackjack inline JS from `customer.html` into `/app/static/js/blackjack3d_inline.js`.
- Operator UI: per-preset live editor that updates `PRESETS` in memory (currently hard-coded server side).
- Drop the unused legacy PF columns from `blackjack3d_games` in a follow-up migration.
### P2
- Multi-table operator view (server-arbitrated rooms).
- Cut-card "burn card" simulation (toss top card after each shuffle for realism).
- Live shoe-state push to admin via SSE/WebSocket.

## Smart enhancement (next worth doing)
**Hot-reload of `PRESETS` and shoe parameters from the operator panel** — let an admin click a preset row and edit `dealer_hits_soft_17`, `surrender`, `decks`, `cut_fraction`, then "Apply to running shoes." This would let venue staff A/B test rules without touching the server, and combined with the new stats endpoint you get a tiny analytics loop: change rule → watch the live house-edge metric move on the same screen.
