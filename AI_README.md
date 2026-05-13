# AI Handoff README — Auditor's Ledger Casino

This file is for future AI agents/developers working in `/app`. It maps where the important pieces live, what is currently routed, and the main pitfalls to avoid.

## What this app is

Private friends-only casino web app with:
- Customer casino panel: `/asiakas`
- Operator management panel: `/operator`
- SQLite-backed customers, points, blackjack rounds, Texas Hold'em state, and audit logs
- 3D card table visuals powered by Three.js

Default route:
- `/` redirects to `/operator`

Legacy note:
- `/app/templates/index.html` is an old retired cashier/operator page. It remains on disk, but `/operator` is the current canonical operator panel.
- `/poker/join` redirects to `/asiakas`; the old separate poker player template was removed.

## Runtime architecture

### Backend
- `/app/app.py` — main Flask app and most APIs/game routes.
- `/app/blackjack3d.py` — blackjack shoe/operator blueprint and operator blackjack Activity endpoint.
- `/app/operator_auth.py` — operator JWT auth helper/decorator.
- `/app/backend/server.py` — FastAPI wrapper mounting Flask through `WSGIMiddleware` for supervisor.
- `/app/backend/casino.db` — SQLite database.

Do **not** swap this to Mongo/FastAPI unless the user explicitly asks for a full rewrite.

### Frontend/templates
- `/app/templates/customer.html` — customer single-page app at `/asiakas`.
- `/app/templates/operator.html` — operator single-page app at `/operator`.
- `/app/templates/index.html` — retired legacy page, not the source of truth.
- `/app/static/css/theme.css` — shared design system and responsive/mobile styles.
- `/app/static/js/casino_3d.js` — Three.js table scene module.
- `/app/static/js/card_renderer.js` — helper renderer.

### Documentation/memory
- `/app/memory/PRD.md` — current product/spec history and backlog.
- `/app/memory/test_credentials.md` — current test/demo credentials. Use as source of truth.
- `/app/auth_testing.md` — auth testing notes.
- `/app/CHANGELOG.md` — change history and rollback notes.
- `/app/AI_README.md` — this handoff file.

## Important routes

### Pages
- `GET /` → redirects to `/operator`
- `GET /asiakas` → customer panel
- `GET /operator` → operator panel
- `GET /poker/join` → redirects to `/asiakas`

### Theme
- `GET /api/theme`
- `PUT /api/operator/theme` — operator token required

### Customer/player
- `POST /api/customer/login`
- `GET /api/players`
- `POST /api/players`
- `GET /api/players/<pid>/points`
- `POST /api/players/<pid>/points/grant`

### Blackjack customer/operator
- `POST /api/points/<pid>/blackjack/start`
  - Supports `keep_active: true` for 2–3 hand play.
- `POST /api/points/blackjack/<gid>/action`
  - Odd route shape; keep callers in sync if refactoring.
- `POST /api/points/<pid>/blackjack/<gid>/sidebet`
- `GET /api/operator/blackjack/recent_rounds`
  - Operator Activity uses this.
  - It combines `blackjack_games` and `blackjack3d_games`.
- `GET /api/operator/blackjack/shoes`
- `POST /api/operator/blackjack/shoes/reset_all`
- `GET /api/operator/blackjack/settings`

### Texas Hold'em
- `GET /api/poker/state`
- `POST /api/poker/new`
- `POST /api/poker/join`
- `GET /api/poker/player/<token>`
- `POST /api/poker/player/<token>/showcards`
- `POST /api/poker/deal`
- `POST /api/poker/advance`
- `POST /api/poker/preset`
- `GET /api/poker/evaluate`
- `GET /api/poker/hands`

### Other games
Most are in `/app/app.py`. Search by route/game name:

```bash
grep -n "baccarat\|pikapokeri\|war\|slots\|coinflip" /app/app.py
```

### Audit
- `GET /api/audit`
  - Currently returns an array.
  - Operator frontend also supports `{events: [...]}` for compatibility.

## Main database tables

Database path: `/app/backend/casino.db`

Important tables:
- `players` — customer accounts and point balances.
- `audit_events` — audit trail.
- `blackjack_games` — current customer blackjack rounds; this is what customer Blackjack writes to.
- `blackjack3d_games` — newer 3D blueprint rounds; often empty right now.
- `blackjack3d_shoes` — blackjack shoe state/settings.
- Poker tables are defined in `/app/app.py`; search for `poker_` table creation.
- Theme values are persisted as `theme_*` app settings.

## Current UI map

### Customer panel — `/app/templates/customer.html`

Customer flow:
1. Splash/login
2. Lobby
3. Game screens

Lobby games:
- Blackjack
- Texas Hold'em
- Baccarat
- Pikapokeri
- Casino War
- Hedelmäpeli / Slots
- Kolikonheitto / Coinflip

Important customer JS/state:
- `window.bjState` — exposed intentionally for debugging multi-hand Blackjack.
- `ensureTableScene()` — lazy-loads `/static/js/casino_3d.js` only when a 3D game opens.
- `bjStart()`, `bjAction()`, `bjAdvanceToPlayable()`, `bjSettleRound()` — blackjack flow.

### Operator panel — `/app/templates/operator.html`

Tabs:
- Yleiskuva
- Asiakkaat
- Brändäys
- Aktiviteetti
- Texas Hold'em
- Pakat (BJ)

Important operator features:
- Customer CRUD and points adjustments.
- Theme editor.
- Activity tab with:
  - recent blackjack rounds
  - audit log
  - `Näytä käsi` expandable hand details showing player/dealer cards, totals, and reason for result
- Texas Hold'em management including `Set next hand` preset modal.
- Blackjack shoe/settings management.

## Fragile areas / gotchas

### Blackjack multi-hand
- Multi-hand is implemented as separate blackjack game rows.
- `keep_active: true` prevents hand 2/3 from abandoning hand 1.
- Do not reintroduce the old “start new hand abandons previous active hand” behavior.

### Blackjack corner status
- The bottom-right table corner must **not** show raw/final statuses like `done_win` or “won” while another hand/dealer resolution is pending.
- It should show neutral labels only:
  - `Kesken`
  - `Käsi valmis`
  - `Ratkaistaan`
  - `Valmis`
- Final win/loss belongs in the center banner after the whole round settles.

### Operator Activity blackjack rows
- Activity must include `blackjack_games`, because current customer Blackjack writes there.
- `blackjack3d_games` alone is not enough and may be empty.

### Blackjack hand explanations
- `/api/operator/blackjack/recent_rounds` should return:
  - `player_cards`
  - `dealer_cards`
  - `player_total`
  - `dealer_total`
  - `reason`
- Operator Activity row button `Näytä käsi` depends on these fields.

### Poker presets
- Operator → Texas Hold'em → `Set next hand` opens the preset modal.
- Preset community arrays may include `null` placeholders; `/api/poker/deal` must ignore null cards but preserve positions.

### Mobile/tablet responsiveness
- Most responsive behavior is in `/app/static/css/theme.css`.
- Operator tables should scroll inside local wrappers, not cause whole-page horizontal overflow.
- Customer game screens stack on phone/tablet with sticky mobile back rows and touch-friendly controls.

### 3D card layout
- Shared card placement lives in `/app/static/js/casino_3d.js`, function `slotPosition(zone, index)`.
- Blackjack multi-hand uses `hand0`, `hand1`, `hand2` zones laid out as left/center/right table-seat positions with small `rotZ` angles. Do not revert to one same-row horizontal lane; that caused visible overlap.
- Pikapokeri uses `community` for all 5 cards. Keep this row large/near the player and keep the deck stack far enough right so the fifth card stays readable.

## Testing snippets

Use the external backend URL:

```bash
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
```

Operator token:

```bash
TOK=$(curl -s -X POST "$API_URL/api/operator/login" \
  -H 'Content-Type: application/json' \
  -d '{"password":"operator123"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

Recent blackjack rounds:

```bash
curl -s "$API_URL/api/operator/blackjack/recent_rounds" \
  -H "Authorization: Bearer $TOK" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('rounds', [])), d.get('rounds', [{}])[0])"
```

Audit log:

```bash
curl -s "$API_URL/api/audit" \
  -H "Authorization: Bearer $TOK" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(type(d).__name__, len(d))"
```

Python syntax:

```bash
python3 -m py_compile /app/app.py /app/blackjack3d.py /app/operator_auth.py /app/api/index.py
```

Inline template JS syntax:

```bash
python3 - <<'PY'
from pathlib import Path
import re, subprocess, sys
for p in ['/app/templates/customer.html','/app/templates/operator.html']:
    s = Path(p).read_text()
    scripts = '\n'.join(m.group(1) for m in re.finditer(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', s, re.S|re.I))
    out = Path('/tmp')/(Path(p).stem + '_inline.js')
    out.write_text(scripts)
    r = subprocess.run(['node','--check',str(out)], capture_output=True, text=True)
    print(Path(p).name, 'ok' if r.returncode == 0 else 'fail')
    if r.returncode:
        print(r.stderr)
        sys.exit(r.returncode)
PY
```

Supervisor/logs:

```bash
sudo supervisorctl restart backend
tail -n 100 /var/log/supervisor/backend.*.log
```

## Credentials

Do not guess credentials. Use:

```text
/app/memory/test_credentials.md
```

That file is the current source of truth for local/demo customer and operator access.

## Recommended regression checklist

After blackjack changes:
- One-hand final result appears only after settle.
- Two-hand progression does not abandon earlier hands.
- Corner status stays neutral until full settle.
- Operator Activity → `Näytä käsi` still shows cards/totals/reason.

After poker changes:
- Operator → Texas Hold'em loads.
- `Set next hand` saves presets.
- `POST /api/poker/deal` tolerates `null` community placeholders.
- Customer `/asiakas` poker join works.

After responsive/CSS changes:
- Phone width around 390px has no page-level horizontal overflow.
- Tablet width around 820px has no page-level horizontal overflow.
- Operator tables scroll locally.

## Recommended next refactor

Largest maintainability improvement:
1. Split `/app/templates/customer.html` into per-game JS modules.
2. Split `/app/templates/operator.html` into tab modules.
3. Split `/app/app.py` into smaller domain/route modules.

Do this incrementally with browser regression checks after each extraction.