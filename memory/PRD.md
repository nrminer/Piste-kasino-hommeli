# Kasinon Hallinta — Product Requirements Document

## Original problem statement

> "Make improvements for the website also make it work in vercel production.
>  Also make all the functions work normally."

## Architecture

- **Two parallel Flask runtimes** sharing the `templates/` folder:
  - `app.py` → SQLite (local dev)
  - `api/index.py` → Redis/Upstash KV (Vercel) with an in-memory fallback for
    preview deployments without KV attached.
- **Local Emergent preview wrappers** (NOT deployed):
  - `backend/server.py` → FastAPI mounting Flask via `WSGIMiddleware` (port 8001)
  - `frontend/server.js` → Express proxy that forwards :3000 → :8001
- **Templates**: 3 HTML pages (admin, customer, poker player), all using the
  same JS/CSS conventions, the Finnish "Kasinon Hallinta" brand and a casino
  green/gold visual identity.

## User personas

| Persona              | Path           | Goals                                            |
|----------------------|----------------|--------------------------------------------------|
| Cashier / Manager    | `/`            | Track players, record wins/losses, run poker    |
| Customer (phone)     | `/asiakas`     | See balance & bonuses, claim, play mini-games    |
| Poker player (tablet)| `/poker/join`  | Join the live Texas Hold'em table, see cards     |

## Core requirements (static)

1. Player CRUD with optional password (SHA-256, customer-side login)
2. Transaction ledger (win/loss per game type, dashboard aggregations)
3. Bonus engine (label + amount, push notification to the customer phone, claim)
4. Spin wheel with server-side prize weighting + spin allowance per player
5. Points system (grant/spend/redeem to cash bonus)
6. Six in-house mini-games: Blackjack, Pikapokeri (Jacks-or-better), Slots
   (3 themes + scatter free-spins), Coinflip, Baccarat, War
7. Live multi-seat Texas Hold'em — dealer screen, per-player tablet, seat
   join via short link, hand evaluator, optional preset hands
8. Streak modes (`normal` / forced `win` / forced `lose` per player)
9. Configurable points-per-€ + redemption limits

## What's been implemented (Iteration 4 — 2026-04-28, hand-log filter + CSV)

- **Per-session filter** on `GET /api/poker/hands` via `?session_id=N`,
  plus a `sessions` array in the response (sorted newest first, with
  hand count + last-played timestamp) so the manager UI can build the
  dropdown without a second round-trip
- **CSV export** at `GET /api/poker/hands.csv?session_id=N` — Excel-friendly
  UTF-8 with BOM and `;` delimiter (Finnish locale convention). Filename
  is auto-suffixed when filtered: `kasihistoria_istunto-4.csv`
- Manager UI:
  - **Session dropdown** at the top of the Käsihistoria panel, auto-populated
    from the `sessions` metadata (re-renders only when set of IDs changes,
    preserving focus). Default option: "Kaikki istunnot"
  - **📥 Lataa CSV** button uses a hidden `<a>` element so the browser
    handles the download natively (no popup, no new tab)
  - Updated empty state message when filter narrows to a session with no hands
- Shared helpers `_hand_log_row_to_dict`, `_fmt_cards`, `_hand_log_csv_response`
  added to both `app.py` and `api/index.py` for symmetric behaviour

### Validated
- 6 hands across 3 sessions, dropdown lists all three with correct counts ✓
- Filter narrows from 6 → 2 entries when "Istunto #4" selected ✓
- CSV download triggers a real browser download (Playwright captured
  filename + body, 3 lines, correct UTF-8 BOM, `;` delimiter, Finnish
  card glyphs, winner column populated) ✓
- Vercel runtime returns identical JSON/CSV ✓

## What's been implemented (Iteration 3 — 2026-04-28, hand-log)

### Käsihistoria — every dealt hand is logged for the manager
- New table `poker_hand_log` (SQLite) / hash `tbl:poker_hand_log` (Redis/in-mem)
  storing: hand_number, started_at/ended_at, stage_reached, ended_by,
  community_cards, full seat snapshot (seat#, name, hole_cards, folded,
  show_cards), winners (with hand_name, hand_rank, is_winner)
- Lifecycle hooks: `/api/poker/deal` opens a new entry (and finalises any
  prior `in_progress` row as **abandoned**), `/advance` updates the
  community cards & stage, `/evaluate` records winners and finalises as
  **showdown**, `/void` snapshots seats *before* clearing them and finalises
  as **void**
- New endpoint: `GET /api/poker/hands?limit=&offset=` (paginated, newest first)
- Manager UI `/` → Pokeripöytä gains a "📜 Käsihistoria" panel with:
  - Per-hand collapsible cards showing badge (Showdown / Mitätöity /
    Hylätty / Käynnissä), timestamp, stage, player count
  - Inline winner line `🏆 <name> — <hand_name>` in gold
  - Expanded view: community cards + every seat (with mini cards),
    winning row highlighted in gold, folded rows struck-through
  - Page-size 25, "Näytä lisää" pagination, "Päivitä" refresh, total counter
- Auto-refresh: hand log reloads whenever the dealer hits the poker view
  refresh action (`loadPoker`)

### Validated (curl + Python test_client + browser screenshots, both runtimes)
- Lifecycle correct: preflop→flop→turn→river→showdown logs winners ✓
- Re-deal finalises prior in-progress hand as `abandoned` ✓
- Void snapshots seats BEFORE clearing hole cards (regression test passed) ✓
- Pagination, total count, ordering (newest first) ✓
- UI: 6 hands rendered; expand/collapse works; cards/winners/badges displayed ✓
- Vercel-bound runtime mirrors SQLite version 1:1 ✓

## What's been implemented (Iteration 2 — 2026-04-28, slots overhaul)

### Slots — full real-machine rewrite (5 reels × 3 rows)
- Backend: 5×3 grid, **20 paylines**, tiered payouts (3-of-a-kind / 4 / 5),
  **wild substitution** (with `wild` paying 5-of-a-kind on its own), scatter
  free spins (3+ scatters → theme-defined free spins × multiplier),
  **progressive jackpot** with 1 % rake per bet — 5 wilds on the middle
  payline awards the entire pool and reseeds at 5 000 pts.
  - Persisted in the same `settings` hash (Redis) / `system_settings` table
    (SQLite), so the pool grows across all players and survives deploys.
  - New endpoint: `GET /api/slots/jackpot` for the live ticker.
- Frontend (`/asiakas` → Pisteet → Slots):
  - 5×3 grid, fluidly sized for mobile (60→48→42 px cells)
  - Animated **jackpot ticker** above the machine (gold sweep + bump on win)
  - **Autoplay**: 10 / 25 / 50 spins with stop button + live counter
  - **Turbo** mode (≈3× faster animation)
  - **Sound** toggle (WebAudio API beeps for spin click, win arpeggio, jackpot
    fanfare — no audio files, fully self-contained)
  - **Win-line SVG overlay** highlighting up to 8 paylines simultaneously
    with 8-color staggered draw animation
  - **Big-win banner** for ≥ 20× bet, **jackpot banner** with celebration
  - Wild + scatter cells get distinct pulsing borders (red / purple)
  - Updated paytable showing 3×/4×/5× columns + wild + scatter + jackpot info

### Validated (Python test_client + browser screenshots, both runtimes)
- 5×3 grid shape, 20-payline evaluation with wild substitution ✓
- Multi-payline wins (one spin produced 4 simultaneous wins, +8 700 net) ✓
- Tiered payouts (cherry×3=2×, lemon×4=8×, orange×5=40×) ✓
- Jackpot pool growing 5 003 → 5 013 → 5 018 across spins ✓
- Autoplay 10× ran end-to-end, status counter updates, stop button visible ✓
- Turbo accelerates spin animation ✓
- Sound toggle persists across spins ✓
- Big-win and jackpot banners render correctly ✓

## What's been implemented (Iteration 1 — 2026-04-28)

### Improvements
- **Vercel-ready**: `vercel.json` rewritten with `@vercel/python@4.5.1`,
  `includeFiles: "templates/**"`, explicit rewrites for HTML routes,
  security headers, and `Cache-Control: no-store` on `/api/*`.
- **In-memory fallback** in `api/index.py`: app boots even if no KV is
  attached so users can demo the UI immediately after deploy.
- **Lazy + cached redis client** at module scope (warm-container friendly)
  with a 5-second connect/socket timeout and `ping()` health check.
- **`/api/_health`** endpoint surfaces the active storage backend so the
  UI can warn ("redis" vs "memory" with reason).
- **Dynamic poker join URL**: replaced the hardcoded `:5000` with
  `window.location.origin + '/poker/join'` so it's correct on Vercel,
  the Emergent preview, and any custom domain.
- **`get_local_ip()`** now uses `request.host` first, falls back to
  `VERCEL_URL`, then to socket-based LAN detection.
- **Favicon** route + SVG icon (no more 404 noise).
- **`.vercelignore`** cleaned up — excludes Emergent local glue
  (`backend/`, `frontend/`), SQLite files, and `app.py` from the function bundle.
- Dead templates removed (`add_player.html`, `player_detail.html`, `base.html`,
  `models.py`, `casino.db`).

### Validated functions (test_client smoke test, both runtimes)
1. Player CRUD with password ✓
2. Customer login (correct + wrong pw) ✓
3. Transactions (player wins/losses) ✓
4. Points: grant/spend ✓
5. Mini-games: Coinflip, War, Baccarat, Blackjack, Slots, Pikapokeri ✓
6. Poker: new session, join, evaluate ✓
7. Bonuses: add, claim, push ✓
8. Spin wheel: validation (403 with no spins, success when granted) ✓
9. Settings GET/PUT ✓
10. Dashboard aggregations ✓

## Backlog / future enhancements

### P1 — Production-readiness
- Migrate password hashing from SHA-256 to bcrypt (BREAKING — needs migration)
- Rate-limit `/api/customer/login` (currently no brute-force protection)
- Add CSRF protection for state-changing endpoints

### P2 — UX / conversion
- Daily-streak login bonus to drive returning customers
- Push web-notifications when a bonus arrives (currently polling-based)
- Per-game return-to-player (RTP) display so customers see fairness
- Multilingual support (currently Finnish only)
- Admin exports (CSV) for tax/regulatory reporting

### P3 — Nice-to-have
- Roulette mini-game
- Tournament mode for multi-seat Hold'em with pot tracking
- Dark/light theme toggle for the manager dashboard

## Next action items

- Attach Upstash Redis in Vercel dashboard before going live
- Smoke-test `/api/_health` after deploy to confirm `mode: redis`
- Consider migrating to bcrypt before exposing to real customers


---

## 2026-04-29 · 3D Blackjack & Baccarat (Three.js) session

### New user request
Transform the existing 2D Blackjack and Baccarat mini-games on `/asiakas` into a
3D casino environment — realistic table, cards, chips, dealer, animations, sound
and responsive PC + mobile support.

### User choices gathered
- Three.js via CDN inside the existing Flask/HTML stack (no React rewrite)
- Only Blackjack and Baccarat panels transformed (other games untouched)
- Dealer-only AI (no extra AI seat opponents)
- WebAudio synthesized SFX (no external asset CDN)
- PC + mobile responsive (no VR, no real-time multiplayer)

### Implemented (all in `/app/templates/customer.html`)
- `<head>`: Three.js r160 CDN
- CSS: `.casino3d-wrap`, `.casino3d-hud`, `.casino3d-total`, `.casino3d-burst`,
  `.bac3d-spots`, mobile media queries (320 → 260 → 230 px height)
- HTML: `#bj-3d-wrap-start`, `#bj-3d-wrap`, `#bac-3d-wrap` with HUD labels,
  total-pill overlays and burst overlays. `data-testid` added on every new
  interactive element.
- JS module: `_getAudio`, `Sounds` (deal / flip / chip / shuffle / win / lose /
  push / bust), `_buildCardTexture` / `_getCardBackTex` (canvas-drawn textures),
  `Card3D` (plane with front + back faces, animated flight + flip),
  `CasinoScene` (felt table, wooden rim, gold ring, ambient + spotlight + point
  lights, dealer silhouette, card-deck prop, shoe position), `BJ3D` singleton
  (player / dealer card choreography, placeholder reveal on stand, outcome
  bursts), `BAC3D` singleton (interleaved P/B deal, totals, bet-spot highlight,
  outcome bursts)
- Hook wrappers around existing `bjReset`, `renderBjState`, `doBaccarat`,
  `bacChoose`, `switchGameTab` — **no backend changes**
- UX polish: `bj-action-row` auto-hides when round ends so only "Uusi peli" shows

### Verified by testing agent (92 % success, iteration_1.json)
- 3D canvas renders in both BJ and Baccarat
- Aloita peli → animated deal → Ota kortti / Jää → dealer reveal → VOITTO / HÄVIÖ
  burst → Uusi peli round-trip works
- Baccarat: side selection, dealing, totals, outcome burst all wired
- No JS errors, no regressions on Coinflip / WAR / Pikapokeri / Slots
- 400 px responsive breakpoint honoured

### Deferred / Backlog
- Chip-flight animation from player stack to bet spot on bet placement
- Win-confetti particle burst on VOITTO
- Brighter dealer avatar with deal gesture
- AI seat opponents with basic strategy (user declined)
- VR / WebXR (user declined)
- Real-time multiplayer via websockets (user declined)
- Externalise the ~700-line 3D JS module into `/app/static/casino3d.js` for
  maintainability (today it lives inline inside `customer.html`)

### Test credentials
See `/app/memory/test_credentials.md` — `TestPelaaja` / `test123` (Player ID 1,
5 000 points granted for this session).
