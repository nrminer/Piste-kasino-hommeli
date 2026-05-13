# Auditor's Ledger Casino — Game Spec Pack

## Original Problem Statement (history)
**Session 1 (iter 12):** "Redo the 3d blackjack, baccarat, pikapokeri, war. Make it 3D and redo the frontend. Also match the customers theme with operators theme."

**Session 2 (iter 13):** "The CARDS are straight up and the blackjacks 'mystery' card is ? card why? its supposed to be realistic, also integrate slots, and coin flip thats already been added. theres now 2 different customer panels integrate everything to one of your choosings."

**Session 3 (iter 15):** "Fix the multihand system in the blackjack, and integrate the poker system into the main customers panel, and why is the texas hold em management panel seperate from the operators panel, fix everything"

**Session 4:** "Wheres the \"set cards and community cards for next round\" option for operator? it existed before. Also polish the code remove all unneeded code/test images/etc."

**Session 5:** Autonomous frontend smoothness pass. User clarified: no security focus; improve frontend UX/accessibility/performance/smoothness, keep behavior working, remove safe unused/generated artifacts.

**Session 6:** "make everything phone/tablet friend"

**Session 7:** "audit lokit or viimeisimmät blackjack kierrokset isnt working"

**Session 8:** "i want to see the hand why the user lost"

## Architecture
- **Stack preserved**: Flask + SQLite (`/app/backend/casino.db`). NO DB removal. NO FastAPI/Mongo swap.
- **Backend modules** (`/app/`):
  - `app.py` — main Flask app (~3.3k lines). Now: `/` is a 302 redirect to `/operator` (was the legacy index.html cashier UI — retired); all `/api/...` game endpoints live here.
  - `blackjack3d.py` — 6-deck Shoe Blueprint.
  - `operator_auth.py` — `op_required` JWT decorator.
  - `backend/server.py` — FastAPI/WSGIMiddleware wrapper.
- **NEW operator-configurable theme system (iter12)**:
  - `THEME_DEFAULTS` dict + `_load_theme()` helper.
  - `GET /api/theme` (public) — returns full theme dict.
  - `PUT /api/operator/theme` (op-protected) — persists `theme_*` keys.
  - 12 overridable keys: brand_name, tagline, logo_text, logo_url, primary, accent, bg, surface, text, muted, danger, felt.
- **Frontend (completely rewritten this session series)**:
  - `/app/static/css/theme.css` — shared CSS variables, base components + slots + coinflip styles.
  - `/app/static/js/casino_3d.js` — Three.js scene module: `createTableScene(container, opts)` returns `{ dealCard, flipCard, replaceCard, revealCard, setChipStack, clearTable, refreshTheme, dispose, getCardCount }`. Cards are BoxGeometry with CanvasTexture faces; lie FLAT on felt with rotation `(±π/2, 0, 0)`. `revealCard` swaps the front-material texture to the real rank/suit before flipping — used for blackjack hole-card reveal. `flipCard` rotates around X-axis with a lift-and-settle arc.
  - `/app/templates/customer.html` — single SPA at `/asiakas`: splash → lobby → in-game screens for Blackjack / Texas Hold'em / Baccarat / Pikapokeri / Casino War / Hedelmäpeli (slots) / Kolikonheitto (coinflip). Three.js table module is lazy-loaded only when a 3D game is opened.
  - `/app/templates/operator.html` — operator SPA at `/operator`: splash → sidebar nav (Yleiskuva / Asiakkaat / Brändäys / Aktiviteetti / Texas Hold'em / Pakat) → theme editor, customer CRUD + points +/− modal, audit/activity log, poker management, shoe management.
- **Theme bridge**: both surfaces fetch `/api/theme` on boot and write to `document.documentElement.style.--xxx`. The 3D scene reads `getComputedStyle()` so theme changes immediately repaint felt/border colors.

## User Personas
- **Customer**: `/asiakas` — name + password (created by operator). 6 games (4 in 3D + slots + coinflip), balance pill, recent-rounds log.
- **Operator**: `/operator` — env-var password. Manages customers (CRUD + grant/deduct points), edits the platform theme, browses BJ rounds + audit, resets shoes.

## Implemented this session (2026-05)
### Iter 12 (3D rebuild + theme matching)
- 3D Blackjack, Baccarat, Pikapokeri, Casino War with Three.js
- `customer.html` + `operator.html` rewritten (3.8k+367 lines → 1.3k+586 lines)
- Shared `theme.css` + `casino_3d.js`
- Operator-configurable theme with 12 keys (`GET /api/theme`, `PUT /api/operator/theme`)

### Iter 13 (realism + integration + consolidation)
- **Cards lie FLAT on felt** — `makeCardMesh` initial rotation `(π/2, 0, 0)`; flip rotates around X-axis to `-π/2` for face-up, with a vertical lift apex. Realistic top-down card view from the dealing perspective.
- **Real hole-card reveal** — added `revealCard(zone, index, card)` which swaps the front material's CanvasTexture to the actual card before flipping. `bjStart` and `bjAction` now call `revealCard('dealer', 1, s.dealer_cards[1])` instead of `flipCard` on the placeholder.
- **Slots integrated** — new `Hedelmäpeli` screen with 5×3 emoji grid, theme picker (Hedelmät/Egypti/Avaruus), jackpot pill loading from `GET /api/slots/jackpot`, scatter+jackpot cell highlighting, win list. Wires to existing `POST /api/points/:pid/slots`.
- **Coinflip integrated** — new `Kolikonheitto` screen with CSS-3D rotating gold coin (perspective + `transform: rotateY(1800deg)`), heads/tails picker, result banner. Wires to existing `POST /api/points/:pid/coinflip` with correct `{bet, choice}` body.
- **Consolidated panels** — `/` now `302 → /operator`; the legacy `templates/index.html` cashier UI is retired (still on disk, just not routed). `/operator` is the single canonical operator surface.

### Iter 15 (blackjack multi-hand + Texas Hold'em unification)
- **Blackjack multi-hand fixed** — `/api/points/<pid>/blackjack/start` now accepts `keep_active` so opening 2–3 hands no longer abandons earlier hands. Customer UI starts hands sequentially and keeps all hand game IDs playable.
- **Blackjack stuck-hand fix** — blackjack `hit` that reaches exactly 21 now settles automatically instead of leaving an active hand with disabled controls.
- **Multi-hand UI progression fixed** — active-hand pill updates immediately (`KÄSI 1 / 2` → `KÄSI 2 / 2`) after resolving a hand; exposed `window.bjState` for easier UI regression checks.
- **Customer Texas Hold'em integrated** — customer lobby has a Texas Hold'em tile and embedded panel. Customers can join the existing poker table from `/asiakas`, view community/hole cards, toggle show-cards, and refresh table state.
- **Operator Texas Hold'em merged** — `/operator` includes a Texas Hold'em tab with join link, new/deal/advance/evaluate/void controls, seat management, community cards, results, and hand history.
- **Operator demo credentials restored locally** — `/app/.env` now provides `OPERATOR_PASSWORD=operator123`, token secret, and 240-minute TTL for local testing.

### Current update (operator preset cards + cleanup)
- **Restored “Set next hand”** inside Operator → Texas Hold'em. The modal lets operators set both player hole cards and flop/turn/river community cards for the next round.
- **Preset backend hardened** — `/api/poker/deal` now ignores empty `null` placeholders in community presets, preventing 500 errors while preserving card positions for the next flop/turn/river.
- **Poker player link unified** — operator join URL now points to `/asiakas`; legacy `/poker/join` redirects to `/asiakas`.
- **Removed obsolete separate poker page** — deleted `templates/poker_player.html` after rerouting references.
- **Cleaned generated artifacts** — removed temporary test reports, pytest cache, pycache folders, generated iteration tests, and screenshot artifacts.

### Current update (frontend smoothness + accessibility)
- **Faster lobby boot** — removed eager `casino_3d.js` import from customer boot; Three.js is dynamically imported on first Blackjack/Baccarat/Pikapokeri/War open.
- **Smoother premium lobby** — responsive asymmetric lobby grid, improved hover/focus motion, Playfair Display + Manrope font system, font preconnects, meta descriptions, mobile background behavior.
- **Accessibility pass** — all 7 lobby tiles are keyboard-focusable `role="button"` elements with aria labels; visible focus states added globally; toasts now use `role="status" aria-live="polite"`; reduced-motion users get near-zero transitions.
- **Runtime polish** — customer/operator theme fetch retries reduce first-load flakes; customer/operator poker polling pauses while browser tab is hidden; operator poker shows a `Preset armed` badge.
- **Measured current response timings** — `/asiakas` 0.248s, `/operator` 0.133s, `/api/theme` 0.105s, `/api/poker/state` 0.136s total curl time from the workspace.

### Current update (phone/tablet responsive pass)
- **Customer mobile/tablet** — topbar wraps cleanly, lobby cards resize, game screens stack, back rows stick below the mobile topbar, 3D scenes fit, action buttons become touch-sized, and bet pads sit below content as sticky mobile cards.
- **Game-specific mobile fixes** — slots grid, coinflip, Texas Hold'em cards, Baccarat side choices, and video-poker hold controls now scale down without page-level horizontal overflow.
- **Operator mobile/tablet** — sidebar becomes a horizontal scroll tab bar, active tab scrolls into view, tables use local horizontal scrollers, poker join URL wraps, poker controls fit into a grid, and preset modal behaves like a mobile bottom sheet.
- **Navigation polish** — changing customer game or operator tab scrolls back to the top so phone users land at the relevant header.

### Current update (operator activity fix)
- **Fixed Viimeisimmät blackjack-kierrokset** — the Activity endpoint now includes the live customer blackjack table (`blackjack_games`) in addition to the newer `blackjack3d_games` table. This restored visibility for the actual rounds currently being played.
- **Fixed Audit-loki rendering** — frontend now accepts the current `/api/audit` response shape (array) as well as `{events: [...]}`.
- **Polished activity labels** — blackjack status values now render as readable Finnish labels and both tables have stable test IDs.

### Current update (blackjack hand explanations)
- **Added expandable hand details** to Operator → Aktiviteetti → Viimeisimmät blackjack-kierrokset. Each row now has `Näytä käsi`.
- **Shows why the user lost/won** — details include player cards, dealer cards, calculated totals, and a short Finnish reason such as `Pelaaja meni yli 21 pisteen (22)` or dealer/player comparison.
- **Backend now returns card context** from `blackjack_games`: `player_cards`, `dealer_cards`, `player_total`, `dealer_total`, and `reason`.

## Files touched
- `/app/app.py` — added theme dict + 2 endpoints (iter12); `/` route now redirects to `/operator` (iter13).
- `/app/static/css/theme.css` — full design system + slots + coinflip styles.
- `/app/static/js/casino_3d.js` — flat-card orientation, `revealCard`, idempotent reveal guard.
- `/app/templates/customer.html` — full SPA with 6 games.
- `/app/templates/operator.html` — full operator console.
- `/app/templates/poker_player.html` — removed; `/poker/join` redirects to `/asiakas`.
- `/app/.env` — `OPERATOR_PASSWORD=operator123`, secret, TTL.
- `/app/backend/tests/test_theme_and_3d_games.py` (iter12), `/app/backend/tests/test_iter13_slots_coinflip_redirect.py` (iter13).
- `/app/memory/PRD.md`, `/app/memory/test_credentials.md`.
- `/app/CHANGELOG.md` — change history and rollback notes.

## Test status
- **Iter12**: 15/15 backend pytest pass; 100 % frontend on 4 game flows.
- **Iter13**: 16/16 backend pytest pass; 100 % frontend on the 6-tile lobby, flat cards, hole-card reveal, slots spin, coinflip, theme matching, `/` redirect.
- **Iter15**: 6/6 backend regression tests passed. Frontend poker/operator/customer integrations passed. Follow-up self-test confirms blackjack 2-hand indicator transitions from `KÄSI 1 / 2` to `KÄSI 2 / 2` after first Stand.
- **Current update**: Testing agent validated the full Set next hand flow with real APIs: operator modal save → deal → flop advance. Manual curl also verified `/poker/join` redirects to `/asiakas` and `/operator` + `/asiakas` return 200.
- **Frontend smoothness pass**: Testing agent validated customer/operator login, 7 accessible lobby tiles, lazy Three.js behavior, Blackjack 3D render, operator poker join URL `/asiakas`, toast aria-live, and no blocking regressions. Follow-up console smoke after theme retry patch showed no `/api/theme` warning.
- **Phone/tablet responsive pass**: Testing agent validated customer/operator mobile 390×844, tablet 820×1180, and desktop routes. Customer lobby/Blackjack/Slots/Texas Hold'em and operator Customers/Texas Hold'em screens passed with no page-level horizontal overflow. Only non-blocking WebGL performance warnings observed in Blackjack.
- **Operator activity fix**: Self-test verified `/api/operator/blackjack/recent_rounds` returns 45 rounds and `/api/audit` returns 5 events; browser check verified Activity tab renders 45 blackjack rows and 5 audit rows.
- **Blackjack hand explanations**: Self-test verified API returns losing hand #41 with player total 22 vs dealer 16; browser check opened `Näytä käsi` and displayed both hands plus the loss reason.

## Backlog / Next Tasks
### P1 (Non-blocking cosmetic carry-overs from testing agent)
- Banner: result banner now sits at 22% from top (above cards) — verify with screenshot it no longer overlaps player cards on tall scenes.
- Investigate possible un-disposed mesh / chip placeholder remnant at bottom-left of BJ felt (chip stack repositioned in iter13 to `(-2.2, 1.02, 0.6)` — verify).
- Anti-double-click guard on `cfFlip` and `slotsSpin` (button is currently disabled-while-busy via local state; harden with a debounce flag).
- Split very large `customer.html`, `operator.html`, and `app.py` into per-game/modules to reduce future blackjack/poker regression risk.
- Dispose 3D scenes on route leave after adding a tested scene cache/restore path.
- Optional mobile performance: investigate Three.js/WebGL `ReadPixels` warnings on low-end devices.

### P2
- Normalise blackjack action route: `/api/points/blackjack/<gid>/action` is the odd one out — every other BJ subroute is `/api/points/<pid>/blackjack/<gid>/...`. Refactor for consistency.
- Move `slotsLoadJackpot` re-seed side-effect OUT of the GET handler.
- Add scene `.dispose()` on screen-leave to free WebGL contexts.

## Smart enhancement (next worth doing)
**Per-VIP theme overlays** — the operator panel already tracks each player's VIP tier (Standard / Silver / Gold / Whale). With ~30 lines we could let the operator pick a slightly different accent color or felt color per tier (e.g., Whale = violet trim + sapphire felt), applied via an extra `<style>` block in `customer.html` keyed on `player.vip_level`. Instant retention/upsell lever: "Reach Whale to unlock your personalised table look."
