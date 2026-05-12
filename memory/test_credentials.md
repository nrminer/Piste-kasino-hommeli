# Test Credentials

## 3D Blackjack — Existing customer flow
Player is created against the existing Flask `/api/players` endpoint and shares the same `players.points` wallet as the rest of the casino.

| Field       | Value          |
| ----------- | -------------- |
| Player ID   | 1              |
| Name        | BJ3D Test      |
| Password    | test123        |
| Email       | bj3d@test.com  |
| VIP Level   | Standard       |
| Balance     | ~4570 pts after E2E run (top up via `/api/players/1/points/grant` if needed) |

### How to use
1. Open `/asiakas` (existing customer dashboard) OR `/blackjack3d` directly.
2. Log in with `name=BJ3D Test`, `password=test123`.
3. Both pages share the `cust_player_id` / `cust_player_name` / `cust_player_pw` localStorage keys, so logging in on one auto-logs you into the other.

### Topping up the test balance (admin/cashier flow)
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
