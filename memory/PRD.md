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

## Follow-up Enhancement — Bulk Ops, Audit, Sound, Poker Polish
User chose all suggested enhancements, prioritizing consistent visual polish.

### Implemented
- Added real management bulk controls for selected customers: grant spins, grant/reduce points, set VIP level, set streak mode, and delete selected accounts.
- Added server-backed audit trail with `/api/audit`, action filters, text search, and logged admin/bulk/customer-account events.
- Added `/api/players/bulk` plus SQLite `audit_events` migration and regression coverage.
- Enhanced slot sound sync using WebAudio reel-start ticks, reel-stop tones, scatter anticipation cues, no-win stingers, and scaled win fanfares.
- Refreshed standalone `/poker/join` visual design to match the upgraded customer casino styling.

### Verification
- QA Iteration 4 passed: management bulk/audit flows, customer slots sound toggle/spin, poker join visual/login/spin path.
- Final self-regression passed: 18/18 backend tests across game payout, slot RTP, and admin bulk/audit suites.
- Inline JS syntax and Python compile checks passed.

### Backlog Updates
#### P0
- None currently known.
#### P1
- Split large server-rendered templates into smaller maintainable modules.
- Replace the external Three.js build script with a modern module import to remove the deprecation warning.
#### P2
- Add richer role-based permissions around bulk actions.

## Immersive Slot Machine Upgrade
User requested a cinematic, tactile, rewarding slot-machine experience for mobile and desktop, with all visual variations selectable, full in-app implementation plus spec notes, full bonus logic with rewards, mobile haptics + Gamepad rumble, and HUD/settings accessibility controls.

### Implemented
- Rebuilt the slots presentation with a cinematic 5×3 reel stage, glass/metal lighting, animated side panels, HUD chips for balance/RTP/bet, first-use tooltip, and glowing spin CTA.
- Added selectable visual skins: Luxury, Neon Noir, and Steampunk.
- Added quick/settings accessibility controls: high contrast, reduced motion, particle toggle, and persistent RTP display.
- Added tactile feedback hooks: mobile vibration and Gamepad API rumble where supported.
- Expanded reel feedback with reactive lighting, adaptive particles, big-win camera shake, and synchronized reel/win/no-win/anticipation sound cues.
- Added server-backed full-screen Bonus Vault mini-game: scatter/free-spin bonus can generate a hidden reward board; player opens 3 tiles and rewards credit immediately.
- Added technical deliverable: `/app/memory/slot_machine_technical_spec.md` with UI mockups, animation timing, RNG/RTP notes, accessibility/performance rules, and asset list.

### Verification
- QA Iteration 5 passed: immersive slot UI, skins, HUD, settings toggles, tooltip behavior, spin flow, bonus overlay, and bonus pick API.
- Final regression passed: 21/21 backend tests, including new `/app/backend/tests/test_slot_bonus_pick.py`.
- Python compile and inline JavaScript syntax checks passed.

### Backlog Updates
#### P0
- None currently known.
#### P1
- Add missing `data-testid` attributes to older login/main-tab controls for even stronger automation.
- Migrate deprecated Three.js CDN include to module-based import.
#### P2
- Split large inline slot/customer template into smaller JS/CSS modules for maintainability.

## Cleanup Enhancement — Test Hooks & Three.js Module Loading
User approved the next cleanup items.

### Implemented
- Added stable `data-testid` attributes to customer login inputs/buttons/errors, customer main navigation tabs, game tabs, slot theme tabs, stats tabs, logout, and poker join login controls.
- Replaced deprecated global Three.js script include with the module build and safely exposed `window.THREE` for existing 3D Blackjack/Baccarat scene code.
- Added guarded lazy initialization so 3D scenes wait for the module before rendering.

### Verification
- Customer login, main tab navigation, Blackjack/Baccarat 3D tabs, and slot theme selection verified via browser automation using the new test IDs.
- Poker join login verified using new test IDs.
- Deprecated Three.js CDN warning is removed; only browser WebGL performance warnings remain during 3D rendering.
- Final regression passed: 21/21 backend tests. Python compile and inline JS/module syntax checks passed.

### Backlog Updates
#### P0
- None currently known.
#### P1
- Optional: handle expected `/api/poker/join` no-open-game response without browser network-error noise.
#### P2
- Continue splitting large templates into maintainable modules.
