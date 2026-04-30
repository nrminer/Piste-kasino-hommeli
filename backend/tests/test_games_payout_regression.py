"""Critical game + payout regression tests.
Modules covered: customer auth, WAR, Pikapokeri, slots payout shape/math, poker spin auth path.
"""

import os
import pytest
import requests


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
PLAYER_ID = 1


@pytest.fixture(scope="module")
def api_client():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL is missing")
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


def test_customer_login_seeded_player(api_client):
    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/customer/login",
        json={"name": "TestPelaaja", "password": "test123"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "TestPelaaja"
    assert data["id"] == PLAYER_ID
    assert isinstance(data.get("points"), int)


def test_war_play_success_and_cards_shape(api_client):
    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/points/{PLAYER_ID}/war",
        json={"bet": 100},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] in {"win", "loss", "push"}
    assert set(data["player_card"].keys()) == {"rank", "suit"}
    assert set(data["dealer_card"].keys()) == {"rank", "suit"}
    assert isinstance(data["points"], int)


def test_pikapokeri_deal_then_draw_success(api_client):
    start = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/points/{PLAYER_ID}/pikapokeri/start",
        json={"bet": 100},
    )
    assert start.status_code == 200
    s = start.json()
    assert s["status"] == "deal"
    assert len(s["hand"]) == 5

    draw = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/points/pikapokeri/{s['game_id']}/draw",
        json={"hold": [0, 2]},
    )
    assert draw.status_code == 200
    d = draw.json()
    assert len(d["hand"]) == 5
    assert d["outcome"] in {"win", "loss"}
    assert isinstance(d["payout"], int)
    assert isinstance(d["points"], int)


def test_slots_response_shape_and_math_unchanged(api_client):
    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/points/{PLAYER_ID}/slots",
        json={"bet": 100, "theme": "fruits"},
    )
    assert r.status_code == 200
    d = r.json()

    expected_keys = {
        "grid",
        "wins",
        "total_mult",
        "bet",
        "payout",
        "net",
        "points",
        "scatter_positions",
        "free_spins_triggered",
        "free_spin_count",
        "free_spin_mult",
        "free_spin_results",
        "bonus_payout",
        "jackpot_won",
        "jackpot_payout",
        "jackpot_pool",
    }
    assert expected_keys.issubset(set(d.keys()))

    expected_payout = int(round(d["bet"] * d["total_mult"]))
    assert d["payout"] == expected_payout
    assert isinstance(d["grid"], list) and len(d["grid"]) == 5
    assert all(len(col) == 3 for col in d["grid"])


def test_poker_spin_requires_player_id(api_client):
    r = api_client.post(f"{BASE_URL.rstrip('/')}/api/poker/spin", json={})
    assert r.status_code == 401
    d = r.json()
    assert "error" in d


def test_poker_spin_with_player_id_not_401(api_client):
    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/poker/spin",
        json={"player_id": PLAYER_ID},
    )
    assert r.status_code in {200, 403}
    d = r.json()
    if r.status_code == 200:
        assert "prize" in d and "spins_remaining" in d
    else:
        assert "spins_remaining" in d or "error" in d
