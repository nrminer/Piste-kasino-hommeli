# Test Credentials

## Customer (player) — existing wallet flow
| Field        | Value          |
| ------------ | -------------- |
| Player ID    | 1              |
| Name         | BJ3D Test      |
| Password     | test123        |
| Email        | bj3d@test.com  |
| VIP Level    | Standard       |
| Balance      | ~10 040 pts after iter11 automated test run (top up via `/api/players/1/points/grant` if needed) |

Both `/asiakas` and `/operator` are reachable directly; the customer credentials are *only* used on `/asiakas`. The customer page stores the session as `cust_player_id`, `cust_player_name`, `cust_player_pw` in `localStorage` — those keys are read by the inline Blackjack flow.

### Topping up the test balance (operator/cashier helper)
```bash
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2)
curl -X POST "$API_URL/api/players/1/points/grant" \
     -H "Content-Type: application/json" \
     -d '{"count":5000,"reason":"Top-up for testing"}'
```

### Re-creating the player from scratch (if `casino.db` is reset)
```bash
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2)
curl -X POST "$API_URL/api/players" -H "Content-Type: application/json" \
     -d '{"name":"BJ3D Test","email":"bj3d@test.com","password":"test123","vip_level":"Standard"}'
# Then grant 5000 points (see above).
```

---

## Operator — env-protected admin panel
| Field        | Value      | Source         |
| ------------ | ---------- | -------------- |
| URL          | `/operator` | Flask template |
| Password     | `admin123` | `/app/.env` → `OPERATOR_PASSWORD` |
| Token TTL    | 60 min     | `/app/.env` → `OPERATOR_TOKEN_TTL_MIN` |
| Token secret | (32+ chars) | `/app/.env` → `OPERATOR_TOKEN_SECRET` |

The page issues a PyJWT (HS256) Bearer token after a successful POST to `/api/operator/login`. The token is stored client-side in `localStorage` under `operator_token`. All `/api/operator/*` endpoints require an `Authorization: Bearer <token>` header.

### Rotating the operator password
1. Edit `/app/.env`, change `OPERATOR_PASSWORD=…` (and ideally `OPERATOR_TOKEN_SECRET` so existing tokens are invalidated).
2. `sudo supervisorctl restart backend` to reload env.

### Programmatic operator login (curl)
```bash
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2)
TOK=$(curl -s -X POST "$API_URL/api/operator/login" \
       -H "Content-Type: application/json" \
       -d '{"password":"admin123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s "$API_URL/api/operator/blackjack/stats" -H "Authorization: Bearer $TOK"
```
