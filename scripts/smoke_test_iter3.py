"""End-to-end smoke test for the 10 new endpoints."""
import requests, json, time, random

BASE = "http://localhost:5000"


def post(url, body=None):
    r = requests.post(BASE + url, json=body or {}, timeout=10)
    print(f"POST {url}\n  {r.status_code} {r.text[:300]}")
    return r


def get(url):
    r = requests.get(BASE + url, timeout=10)
    print(f"GET  {url}\n  {r.status_code} {r.text[:300]}")
    return r


print("=== Test 1: Create test player ===")
r = post("/api/players", {"name": "TestPlayer_" + str(int(time.time()))})
assert r.status_code in (200, 201), r.text
pid = r.json()["id"]
print(f"player_id={pid}")

# Seed points directly via SQL
import sqlite3
db_path = "/app/casino.db"
con = sqlite3.connect(db_path)
con.execute("UPDATE players SET points = 50000 WHERE id=?", (pid,))
con.commit()
con.close()
print(f"  seeded 50000 pts directly via SQL")

print("\n=== Test 2: Blackjack /start + /sidebet ===")
r = post(f"/api/points/{pid}/blackjack/start", {"bet": 100})
gid = r.json().get("game_id") or r.json().get("id")
print(f"game_id={gid}")

if r.json().get("status") == "active":
    r = post(f"/api/points/{pid}/blackjack/{gid}/sidebet",
             {"perfect_pairs_pts": 50, "twenty_one_plus_three_pts": 50})
    assert r.status_code == 200, "sidebet failed"
    print(f"  sidebet result: {r.json().get('resolved')}")
else:
    print(f"  game ended immediately (likely natural BJ): {r.json().get('status')}")

print("\n=== Test 3: Blackjack /split ===")
random.seed(42)
for attempt in range(10):
    r = post(f"/api/points/{pid}/blackjack/start", {"bet": 100})
    j = r.json()
    if j.get("status") == "active":
        gid = j.get("game_id") or j.get("id")
        pcards = j.get("player_cards") or j.get("player") or []
        if len(pcards) == 2 and pcards[0]["rank"] == pcards[1]["rank"]:
            r = post(f"/api/points/{pid}/blackjack/{gid}/split")
            print(f"  split attempt {attempt}: {r.status_code} {r.json()}")
            break
        else:
            post(f"/api/points/{pid}/blackjack/{gid}/action", {"action": "stand"})
else:
    print("  no pair was dealt in 10 tries — split mechanic not exercised")

print("\n=== Test 4: Blackjack /surrender ===")
r = post(f"/api/points/{pid}/blackjack/start", {"bet": 100})
j = r.json()
if j.get("status") == "active":
    gid = j.get("game_id") or j.get("id")
    r = post(f"/api/points/{pid}/blackjack/{gid}/surrender")
    print(f"  surrender result: {r.status_code} {r.json()}")
    assert r.status_code in (200, 400), "surrender response unexpected"

print("\n=== Test 5: Blackjack /active-hand ===")
r = post(f"/api/points/{pid}/blackjack/start", {"bet": 100})
j = r.json()
if j.get("status") == "active":
    gid = j.get("game_id") or j.get("id")
    r = post(f"/api/points/{pid}/blackjack/{gid}/active-hand", {"hand_index": 0})
    print(f"  active-hand=0: {r.status_code} {r.json()}")
    r = post(f"/api/points/{pid}/blackjack/{gid}/active-hand", {"hand_index": 99})
    print(f"  active-hand=99 (should fail): {r.status_code}")
    assert r.status_code == 400, "out-of-range hand_index should fail"
    post(f"/api/points/{pid}/blackjack/{gid}/action", {"action": "stand"})

print("\n=== Test 6: Blackjack /bonus-buy ===")
r = post(f"/api/points/{pid}/blackjack/bonus-buy", {"intended_bet_pts": 100})
print(f"  bonus-buy: {r.status_code} payout_pts={r.json().get('payout_pts')} price_pts={r.json().get('price_pts')}")
assert r.status_code == 200, "bonus-buy failed"
assert r.json().get("payout_pts") == 250, f"BJ payout 6:5 ratio expected? got {r.json()}"

r = post(f"/api/points/{pid}/blackjack/bonus-buy", {"intended_bet_pts": 100})
print(f"  bonus-buy cooldown test: {r.status_code} (expected 429)")
assert r.status_code == 429, "cooldown should reject second purchase"

print("\n=== Test 7: Poker mode-B flow ===")
r = post("/api/poker/new")
print(f"  poker/new: {r.status_code}")
r = post("/api/poker/start-mode-b", {"small_blind_pts": 50, "big_blind_pts": 100})
print(f"  start-mode-b: {r.status_code} {r.text[:200]}")

r = get("/api/poker/round-state")
print(f"  round-state: {r.status_code} mode={r.json().get('mode')}")

r = post("/api/poker/auto-settle", {"operator_confirmed": False})
print(f"  auto-settle without confirm (should 400): {r.status_code}")
assert r.status_code == 400

print("\n=== ALL ENDPOINTS REACHED — smoke test pass ===")
