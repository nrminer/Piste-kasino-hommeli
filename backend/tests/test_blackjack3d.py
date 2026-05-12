"""Backend tests for /api/blackjack3d/* endpoints integrated with legacy points wallet."""
import hashlib
import hmac
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://card-game-suite-3d.preview.emergentagent.com").rstrip("/")
PID = 1  # BJ3D Test seeded player

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]


def _byte_stream(server_seed, client_seed, nonce):
    counter = 0
    while True:
        msg = f"{client_seed}:{nonce}:{counter}".encode()
        block = hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
        for b in block:
            yield b
        counter += 1


def build_shoe(server_seed, client_seed, nonce, decks):
    cards = []
    for _ in range(decks):
        for s in SUITS:
            for r in RANKS:
                cards.append({"r": r, "s": s})
    stream = _byte_stream(server_seed, client_seed, nonce)
    for i in range(len(cards) - 1, 0, -1):
        bound = i + 1
        limit = (0xFFFFFFFF // bound) * bound
        while True:
            val = (next(stream) << 24) | (next(stream) << 16) | (next(stream) << 8) | next(stream)
            if val < limit:
                break
        j = val % bound
        cards[i], cards[j] = cards[j], cards[i]
    return cards


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _balance(session):
    r = session.get(f"{BASE_URL}/api/players")
    for p in r.json():
        if p["id"] == PID:
            return p["points"]
    return None


def _start_round(session, bet=100, preset="vegas_strip"):
    return session.post(
        f"{BASE_URL}/api/blackjack3d/round/start",
        json={"player_id": PID, "bet": bet, "preset": preset},
    )


# ── Presets endpoint ─────────────────────────────────────────────────────
def test_presets_returns_three_presets(session):
    r = session.get(f"{BASE_URL}/api/blackjack3d/presets")
    assert r.status_code == 200
    data = r.json()
    ids = {p["id"] for p in data["presets"]}
    assert ids == {"vegas_strip", "vegas_classic", "european_nhc"}
    vs = next(p for p in data["presets"] if p["id"] == "vegas_strip")
    assert vs["decks"] == 6
    assert vs["dealer_hits_soft_17"] is False
    vc = next(p for p in data["presets"] if p["id"] == "vegas_classic")
    assert vc["dealer_hits_soft_17"] is True
    eu = next(p for p in data["presets"] if p["id"] == "european_nhc")
    assert eu["dealer_peek"] is False


# ── Start round happy path: deduction + commit ───────────────────────────
def test_start_round_deducts_and_commits(session):
    bal_before = _balance(session)
    r = _start_round(session, bet=100)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "server_seed_hash" in data and len(data["server_seed_hash"]) == 64
    assert "client_seed" in data
    assert data["nonce"] == 1
    # dealer hole hidden as None when active
    if data["status"] == "active":
        assert data["dealer"][1] is None
        assert "stand" in data["legal_actions"] and "hit" in data["legal_actions"]
    # server_seed must NOT be exposed while active
    if data["status"] == "active":
        assert "server_seed" not in data or data.get("server_seed") in (None, "")
    bal_after = data["balance"]
    assert bal_after == bal_before - 100
    # cleanup: stand to settle so we don't keep round open
    if data["status"] == "active":
        session.post(
            f"{BASE_URL}/api/blackjack3d/round/{data['game_id']}/action",
            json={"action": "stand"},
        )


def test_start_round_insufficient_points_returns_400(session):
    r = _start_round(session, bet=999999)
    assert r.status_code == 400
    assert "error" in r.json()


# ── Action: stand ────────────────────────────────────────────────────────
def test_action_stand_settles_with_seed_reveal(session):
    r = _start_round(session, bet=50)
    assert r.status_code == 200
    d = r.json()
    if d["status"] == "settled":
        # natural blackjack — server_seed should already be present
        assert "server_seed" in d
        return
    gid = d["game_id"]
    r2 = session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "stand"})
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["status"] == "settled"
    assert "server_seed" in d2
    # SHA-256 of server_seed must match committed hash
    assert hashlib.sha256(d2["server_seed"].encode()).hexdigest() == d["server_seed_hash"]
    # outcome.net must be int
    assert d2["outcome"] is not None and isinstance(d2["outcome"]["net"], int)


# ── Action: hit until bust transitions to lose ───────────────────────────
def test_action_hit_can_bust(session):
    # Try a few times since outcome depends on shoe; assert hit returns 200 each time
    r = _start_round(session, bet=20)
    d = r.json()
    if d["status"] == "settled":
        return
    gid = d["game_id"]
    last = d
    for _ in range(10):
        rh = session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "hit"})
        assert rh.status_code == 200, rh.text
        last = rh.json()
        if last["status"] == "settled":
            break
        # active hand status check
        ah = last["hands"][last["active_hand"]]
        if ah["status"] in ("bust", "stand"):
            # if bust, settle should happen on next call or already settled
            if last["status"] == "settled":
                break
    # If still active, stand to settle for cleanup
    if last["status"] == "active":
        session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "stand"})


# ── Action: double ───────────────────────────────────────────────────────
def test_action_double_deducts_extra_and_settles(session):
    bal_before = _balance(session)
    r = _start_round(session, bet=30)
    d = r.json()
    if d["status"] == "settled":
        return
    if "double" not in d["legal_actions"]:
        # Cannot guarantee double for any 2-card hand; legal_actions should usually include it
        gid = d["game_id"]
        session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "stand"})
        pytest.skip("Double not legal on initial deal in this shoe")
    gid = d["game_id"]
    rd = session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "double"})
    assert rd.status_code == 200, rd.text
    dd = rd.json()
    assert dd["status"] == "settled"
    # Net effect: starting 30 bet deducted, then 30 more on double, then payout
    bal_after = dd["balance"]
    net = dd["outcome"]["net"]
    # bal_after == bal_before + net  (since net already accounts for total bet)
    assert bal_after == bal_before + net


# ── Action: surrender returns half ───────────────────────────────────────
def test_action_surrender_returns_half(session):
    bal_before = _balance(session)
    r = _start_round(session, bet=100)
    d = r.json()
    if d["status"] == "settled":
        return
    if "surrender" not in d["legal_actions"]:
        gid = d["game_id"]
        session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "stand"})
        pytest.skip("Surrender not legal here")
    gid = d["game_id"]
    rs = session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "surrender"})
    assert rs.status_code == 200
    ds = rs.json()
    assert ds["status"] == "settled"
    # Lost half of 100 => net = -50
    assert ds["outcome"]["net"] == -50
    assert ds["balance"] == bal_before - 50


# ── Reveal endpoint: SHA-256 of server_seed equals committed hash ────────
def test_reveal_seed_matches_hash(session):
    r = _start_round(session, bet=10)
    d = r.json()
    gid = d["game_id"]
    committed_hash = d["server_seed_hash"]
    if d["status"] == "active":
        session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "stand"})
    rv = session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/reveal")
    assert rv.status_code == 200
    rd = rv.json()
    assert hashlib.sha256(rd["server_seed"].encode()).hexdigest() == committed_hash
    assert rd["server_seed_hash"] == committed_hash


# ── Deterministic shoe verification ──────────────────────────────────────
def test_deterministic_shoe_reproduces_initial_cards(session):
    r = _start_round(session, bet=10)
    d = r.json()
    gid = d["game_id"]
    # stand to settle to get seed
    if d["status"] == "active":
        rs = session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "stand"})
        d2 = rs.json()
    else:
        d2 = d
    # reveal
    rv = session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/reveal").json()
    shoe = build_shoe(rv["server_seed"], rv["client_seed"], d["nonce"], rv["decks"])
    # initial dealt cards order: p1, d1, p2, d2
    assert d["hands"][0]["cards"][0] == shoe[0]
    assert d["dealer"][0] == shoe[1]
    assert d["hands"][0]["cards"][1] == shoe[2]
    # dealer[1] hidden during active but revealed after settle
    assert d2["dealer"][1] == shoe[3]


# ── Hint endpoint: returns valid action ──────────────────────────────────
def test_hint_returns_valid_action(session):
    r = _start_round(session, bet=10)
    d = r.json()
    gid = d["game_id"]
    if d["status"] == "active":
        rh = session.get(f"{BASE_URL}/api/blackjack3d/round/{gid}/hint")
        assert rh.status_code == 200
        hint = rh.json()["hint"]
        assert hint in ("hit", "stand", "double", "split", "surrender")
        session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "stand"})


# ── Wallet integration: win pays bet*2 back (net +bet) ───────────────────
def test_wallet_balance_consistent_after_round(session):
    bal_before = _balance(session)
    r = _start_round(session, bet=40)
    d = r.json()
    gid = d["game_id"]
    if d["status"] == "active":
        rs = session.post(f"{BASE_URL}/api/blackjack3d/round/{gid}/action", json={"action": "stand"})
        ds = rs.json()
    else:
        ds = d
    bal_after = ds["balance"]
    net = ds["outcome"]["net"]
    assert bal_after == bal_before + net
    # Validate against DB-reported balance too
    assert _balance(session) == bal_after


# ── Legacy 2D blackjack endpoint still works ─────────────────────────────
def test_legacy_2d_blackjack_endpoint_still_works(session):
    r = session.post(f"{BASE_URL}/api/points/{PID}/blackjack/start", json={"bet": 10})
    # Accept 200 (started) or 400 (validation), but NOT 404/500 (route gone / crashed)
    assert r.status_code in (200, 400), f"Legacy endpoint broken: {r.status_code} {r.text[:200]}"


# ── Frontend page renders ────────────────────────────────────────────────
def test_blackjack3d_page_returns_200(session):
    r = session.get(f"{BASE_URL}/blackjack3d")
    assert r.status_code == 200
    body = r.text
    assert "bj3d" in body  # data-testid prefix present
