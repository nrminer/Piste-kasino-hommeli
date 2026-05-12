# Test Credentials

## Customer (player) — for /asiakas
| Field        | Value          |
| ------------ | -------------- |
| Player ID    | 1              |
| Name         | Pelaaja        |
| Password     | test123        |
| Email        | test@test.fi   |
| VIP Level    | Gold           |
| Balance      | ~9,900 points (top up via operator panel or `/api/players/1/points/grant`)  |

The customer page is at `/asiakas`. Login via name + password fields (data-testids `cust-login-name`, `cust-login-pw`, `cust-login-btn`).
Session is stored in `localStorage` key `cust_session`.

### Topping up the test balance
```bash
curl -X POST http://localhost:8001/api/players/1/points/grant \
  -H "Content-Type: application/json" \
  -d '{"count":5000,"reason":"Top-up for testing"}'
```

### Re-creating the player from scratch (if SQLite is reset)
```bash
curl -X POST http://localhost:8001/api/players \
  -H "Content-Type: application/json" \
  -d '{"name":"Pelaaja","email":"test@test.fi","password":"test123","vip_level":"Gold"}'
curl -X POST http://localhost:8001/api/players/1/points/grant \
  -H "Content-Type: application/json" \
  -d '{"count":10000,"reason":"Top-up"}'
```

---

## Operator — env-protected admin panel
| Field        | Value      | Source         |
| ------------ | ---------- | -------------- |
| URL          | `/operator` | Flask template |
| Password     | `admin123` | `/app/.env` → `OPERATOR_PASSWORD` |
| Token TTL    | 120 min    | `/app/.env` → `OPERATOR_TOKEN_TTL_MIN` |
| Token secret | (32+ chars) | `/app/.env` → `OPERATOR_TOKEN_SECRET` |

The page issues a PyJWT (HS256) Bearer token after a successful POST to `/api/operator/login`. Token stored client-side in `localStorage` under `operator_token`. All `/api/operator/*` endpoints require an `Authorization: Bearer <token>` header.

### Programmatic operator login (curl)
```bash
TOK=$(curl -s -X POST http://localhost:8001/api/operator/login \
       -H "Content-Type: application/json" \
       -d '{"password":"admin123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s http://localhost:8001/api/operator/blackjack/stats -H "Authorization: Bearer $TOK"
```

### Theme update
```bash
curl -X PUT http://localhost:8001/api/operator/theme \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOK" \
  -d '{"theme_brand_name":"My Casino","theme_primary":"#ff6b6b"}'
```
