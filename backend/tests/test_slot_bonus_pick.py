"""Slots bonus mini-game API tests.
Modules covered: bonus game generation from /api/points/<pid>/slots and /api/points/<pid>/slots/bonus/<gid>/pick.
"""

import os

import pytest
import requests


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
PLAYER_ID = 1
BET = 100


@pytest.fixture(scope="module")
def api_client():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL is missing")
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def bonus_game_id(api_client):
    base = BASE_URL.rstrip("/")
    # Free-spins trigger is random; try enough spins to reliably produce one bonus game.
    for _ in range(150):
        r = api_client.post(f"{base}/api/points/{PLAYER_ID}/slots", json={"bet": BET, "theme": "fruits"})
        assert r.status_code == 200
        d = r.json()
        if d.get("bonus_game") and d["bonus_game"].get("id"):
            return d["bonus_game"]["id"]
    pytest.skip("Could not generate bonus game within 150 slot spins")


def test_bonus_pick_success_flow_three_unique_tiles(api_client, bonus_game_id):
    base = BASE_URL.rstrip("/")
    picked = []
    for tile in [0, 1, 2]:
        r = api_client.post(
            f"{base}/api/points/{PLAYER_ID}/slots/bonus/{bonus_game_id}/pick",
            json={"tile": tile},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["tile"] == tile
        assert isinstance(d["reward"]["amount"], int)
        picked.append(tile)
        assert d["picked"] == picked

    # Last pick should complete the mini-game.
    assert d["complete"] is True
    assert d["picks_remaining"] == 0


def test_bonus_pick_rejects_repeat_tile_after_opened(api_client, bonus_game_id):
    base = BASE_URL.rstrip("/")
    r = api_client.post(
        f"{base}/api/points/{PLAYER_ID}/slots/bonus/{bonus_game_id}/pick",
        json={"tile": 0},
    )
    assert r.status_code == 400
    d = r.json()
    assert "error" in d


def test_bonus_pick_rejects_invalid_tile_index(api_client, bonus_game_id):
    base = BASE_URL.rstrip("/")
    r = api_client.post(
        f"{base}/api/points/{PLAYER_ID}/slots/bonus/{bonus_game_id}/pick",
        json={"tile": 99},
    )
    assert r.status_code == 400
    d = r.json()
    assert "error" in d
