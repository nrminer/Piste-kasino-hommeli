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
