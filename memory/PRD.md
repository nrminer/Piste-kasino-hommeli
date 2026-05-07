# Auditor's Ledger Casino — Game Spec Pack

## Original Problem Statement
Tuottaa täydellinen, toteutusvalmis design-, taide-, UX- ja logiikkaspesifikaatiopaketti olemassa olevan Flask/SQLite-kasinojärjestelmän korttipelien ja kolikkopelien päivittämistä ja laajentamista varten. Tulosteen tulee olla yksi JSON-objekti, jonka 2–4 hengen tiimi voi toteuttaa suoraan.

## Architecture (deliverable spec only — no code changes to Flask/SQLite)
- Existing: Flask + SQLite, points-only currency, 4 games (slots/pikapokeri/blackjack/poker)
- Spec adds: 2 new slot variants (tumble cluster pays + hold & win), bonus mechanics (wheel/pick/sticky free spins), side bets (Perfect Pairs, 21+3), Wild Deuces variant, Double Up, RG controls
- Design system: "Auditor's Ledger" Swiss-Modernist Financial Terminal (flat, sharp, JetBrains Mono + Chivo 900)

## User Personas
- **Player (Finnish casino patron)**: clinical UI, RG-aware, wants engagement variety
- **Operator**: bonus-buy revenue lift, RTP audit trail, streak-mode bias control
- **Compliance Officer**: full RNG seed reproducibility, RTP Monte-Carlo validation, RG flow logging

## Core Requirements (static)
- Target RTP 90.0% ± 0.1% across base + bonus combined (per game)
- Volatility tiers: Fruits L-M / Egypt M-H / Space H / Tumble H / Hold&Win M-H
- Hyper-realistic tactile materials (PBR), 3 lighting setups, 8 named animations
- WCAG 2.2 AA accessibility, prefers-reduced-motion, 3 colorblind shape-token variants
- Mandatory 60-min RG interstitial; bonus-buy consent modal; session timer always-on
- Performance budgets: mobile 30 draws/150 particles/30fps, desktop 60/600/60

## Implemented (this session)
- 2026-01: Delivered single-file spec `/app/casino_game_spec.json` (149.6 KB, 18 top-level keys)
  - rtp_calculation (8 games, MC-validated, 3-decimal precision)
  - art_tokens (color palettes, PBR materials, 3 lighting setups, 12 SVG icon constructions)
  - animation_specs (A1–A8, 60fps keyframes, easing curves, fallback states)
  - bonus_definitions (14 bonuses across 5 slots + 3 card-game side mechanics)
  - lottie_stubs (5 skeletons), animation_timing_table (10 events)
  - rng_to_animation_pseudocode (9 fully validated Python blocks)
  - ui_wireframes (9 screens), css_glsl_snippets (6 valid CSS snippets)
  - db_migrations (8 SQL strings, idempotent), api_endpoints (8 routes)
  - performance_budget (3 platforms + LOD rules), accessibility (RG + colorblind + reduced-motion)
  - sound_bank (16 SFX), voiceover_cues (8 stingers), qa_checklist (7 categories)
  - implementation_notes (5 deep guidance blocks)
- Validation: ast.parse OK on all 17 pseudocode blocks; RTP tolerance pass on all 8 games

## Backlog / Next Tasks
### P0 (none — spec is delivery-complete)
### P1
- Implement Flask routes from `api_endpoints` block (8 routes)
- Run db_migrations against staging SQLite
- Build JS animation registry honoring AnimationBus pattern from implementation_notes.frontend_animation_bridge
### P2
- Authoring of actual SVG / Lottie binary assets from iconography construction tokens
- Sound-bank WAV asset creation per physical_description
- 10M-spin Monte Carlo runner (qa_checklist.RTP_validation)
- Localization-keys file (Finnish strings → en-US/sv-SE keys)

## Files
- `/app/casino_game_spec.json` — primary deliverable (149.6 KB)
- `/app/spec_parts/part_1.json` … `part_3.json` — source parts (preserved for diff/rebuild)
- `/app/spec_parts/merge.py`, `validate_pseudocode.py` — build + validation tooling

---

## ITERATION 2 — Card Games Deep-Dive Spec (Blackjack + Texas Hold'em)

### Delivered
- 2026-01: `/app/casino_card_games_spec.json` (193.4 KB, 17 top-level keys)
  - Full RTP math: Blackjack base 90.011%, Perfect Pairs 89.58%, 21+3 89.46%, House-banked Hold'em variant 90.04%
  - 10 animation specs (A1–A10) with 60fps keyframes, easings, fallbacks
  - Blackjack upgrades: split mechanic, surrender, side bets, "Guaranteed Blackjack" bonus buy, streak-mode CSS filters
  - Texas Hold'em upgrades: Mode A (display-only) + Mode B (semi-automated betting w/ blinds + side pots), preset editor, hand-history replay
  - CardRenderer class (vanilla JS + Canvas2D) with PIP_LAYOUT_TABLE for ranks 2-10 + face card art + chip rendering
  - 12 SFX, 23 DB migrations, 10 new API endpoints (5 BJ + 5 Poker mode-B), 12 UI wireframes, 10 CSS snippets
  - Accessibility: 3 colorblind modes (default, protan/deutan, tritan), reduced-motion fallbacks per anim, ARIA-live announcements with Finnish suit/rank names
  - Responsible gambling: 20-loss streak interstitial, 5×-balance net-loss interstitial, session_state schema
- Validation: All 15 Python pseudocode blocks parse; all 4 RTP targets within tolerance; merged file is valid JSON

### Files
- `/app/casino_card_games_spec.json` — primary deliverable
- `/app/spec_parts2/part_{1..4}.json` — source parts (split at top-level key boundaries per OUTPUT SPLITTING RULES)
- `/app/spec_parts2/merge_and_validate.py` — build + validation tooling

### Backlog (P1)
- Implement 10 new API endpoints (5 BJ split/surrender/sidebet/bonus-buy/active-hand + 5 Poker mode-B)
- Run 23 DB migrations against staging SQLite (idempotent IF NOT EXISTS / ALTER ADD COLUMN)
- Add CardRenderer JS class to /static/js/card_renderer.js, integrate into existing Blackjack + Poker frontends

### Backlog (P2)
- Build SVG/Lottie binaries from art_tokens.iconography construction tokens
- Generate WAV samples per sound_bank physical_descriptions
- Run 10M-hand Blackjack Monte Carlo to verify RTP 90.011% ± 0.10%
- Localize Finnish UI keys to en-US, sv-SE
