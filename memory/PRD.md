# PRD — Casino Management & Customer Game Polish

## Original Problem Statement
Please redo the management and customer sides of the design, and make the minigame slot mechanics more realistic. Animate the WAR game and Pikapokeri. Also check the code for any bugs and polish the overall implementation.

User choices: balanced scope across all requested areas; clean business dashboard for management plus playful customer side; improve mechanics/animations while keeping current payout rules; focus QA on games and payout logic; no brand constraints.

## Architecture Decisions
- Existing app is a Flask/SQLite application mounted through FastAPI/WSGI for the preview service; templates remain server-rendered HTML with inline CSS/JS.
- Kept backend payout rules unchanged and focused slot work on realistic client-side reel behavior, paytable display accuracy, and regression safety.
- Used CSS/GPU-friendly animations and reduced-motion fallbacks for cards, particles, slot reels, and UI interactions.
- Restored missing Flask runtime dependencies through backend requirements to keep the supervisor-managed service healthy.

## Implemented
- Refreshed management dashboard styling into a cleaner operational control-room interface with calmer cards, sidebar, tables, buttons, forms, and analytics panels.
- Refreshed customer game UX with jewel-glass styling, responsive spacing, stronger affordances, WAR card battle animation, Pikapokeri shuffle/deal/draw animation, and richer slot reel inertia/staggering.
- Fixed user-facing slot paytable values to match calibrated backend payout multipliers without changing payout logic.
- Fixed `/poker/join` free-spin flow: sends `player_id`, initializes wheel constants before drawing, handles 403/no-spin responses gracefully, and avoids runtime crashes.
- Added game/payout regression coverage and verified existing RTP tests.

## Verification
- Inline template JavaScript syntax checks passed.
- Backend game regression tests passed: `/app/backend/tests/test_games_payout_regression.py`.
- Slot RTP/payout regression tests passed: `/app/backend/tests/test_slot_rtp.py`.
- Browser smoke checks passed for management root, customer login, WAR, Pikapokeri, slots, and poker join spin request path.

## Prioritized Backlog
### P0
- None currently known after final verification.

### P1
- Add real management workflow upgrades: selectable bulk player actions, server-backed audit trail filters, and explicit role-based permissions.
- Add sound hooks/audio settings for slot reel stop and win timing.

### P2
- Add deeper animation performance instrumentation/FPS overlay for low-end devices.
- Expand visual redesign to the standalone poker player page so it fully matches the updated customer experience.
