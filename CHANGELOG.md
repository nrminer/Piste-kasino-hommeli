# Changelog

## 2026-05-13 — AI handoff README
- Added `/app/AI_README.md` with the app map for future AI agents: routes, major files, database tables, APIs, testing commands, credentials pointer, fragile blackjack/poker areas, and recommended refactor path.

## 2026-05-13 — Blackjack premature status fix
- Replaced raw/final blackjack status text in the table corner with neutral progress labels while hands are still resolving.
- Final win/loss result remains visible only in the center banner after the round is fully settled.

## 2026-05-13 — Blackjack hand explanations in Activity
- Added `Näytä käsi` expandable details for each row in **Viimeisimmät blackjack-kierrokset**.
- Activity API now includes player/dealer cards, calculated totals, and a human-readable reason for the outcome.
- Verified with a losing hand: player total 22 vs dealer total 16 displayed with reason `Pelaaja meni yli 21 pisteen (22)`.

## 2026-05-13 — Operator Activity tab fix
- Fixed **Viimeisimmät blackjack-kierrokset** by including customer blackjack rounds from `blackjack_games`, not only the empty `blackjack3d_games` table.
- Fixed **Audit-loki** rendering by accepting `/api/audit`'s array response shape.
- Added readable Finnish status labels and stable table test IDs for Activity tables.

## 2026-05-13 — Phone/tablet responsive pass
- Made customer screens mobile-first: wrapping topbar, responsive lobby, stacked game layouts, sticky mobile back rows, larger touch targets, and sticky mobile bet pads.
- Added mobile/tablet fit rules for Blackjack 3D, Texas Hold'em, slots, coinflip, Baccarat side choices, and video-poker hold controls.
- Made operator panel phone/tablet friendly with horizontal tab navigation, local table scrollers, wrapping poker URL, mobile poker controls, and bottom-sheet-style modals.
- Added scroll-to-top behavior when switching customer games/operator tabs and active-tab scroll into view.
- Validated on mobile 390×844, tablet 820×1180, and desktop with no page-level horizontal overflow in tested flows.

## 2026-05-13 — Frontend smoothness and accessibility pass
- Lazy-loaded Three.js/casino table code so the customer lobby no longer downloads `/static/js/casino_3d.js` until a 3D game is opened.
- Upgraded typography and loading hints with Playfair Display, Manrope, Google Fonts preconnects, and page meta descriptions.
- Improved the customer lobby layout with a responsive asymmetric grid, better hover/focus states, keyboard-operable game tiles, and reduced-motion support.
- Added `role="status" aria-live="polite"` to customer/operator toasts and global `:focus-visible` styling.
- Added retry logic for `/api/theme` on customer/operator boot to reduce intermittent first-load theme warnings.
- Paused customer/operator poker polling while the browser tab is hidden and added an operator `Preset armed` badge.
- Verified with self-tests and testing-agent validation: customer/operator login, accessible lobby tiles, lazy Three.js load, Blackjack 3D render, operator poker URL, and backend smoke checks.

### Rollback notes
- Revert changes in `/app/templates/customer.html`, `/app/templates/operator.html`, and `/app/static/css/theme.css` to restore the previous eager-load UI behavior.
- If lazy loading causes issues, restore the static import `import { createTableScene, cardLabel } from '/static/js/casino_3d.js';` at the top of the customer module and remove `getTableSceneModule()/ensureTableScene()` calls.

## 2026-05-13 — Operator Hold'em preset cards restored
- Added **Set next hand** inside Operator → Texas Hold'em for player hole cards and community cards.
- Hardened `/api/poker/deal` to ignore null placeholder cards in presets.
- Redirected `/poker/join` to `/asiakas` and removed the obsolete separate poker player template.

## 2026-05-13 — Blackjack multi-hand and poker panel unification
- Fixed blackjack multi-hand so earlier hands are not abandoned when 2–3 hands are opened.
- Added Texas Hold'em to the customer panel and merged poker management into the operator panel.