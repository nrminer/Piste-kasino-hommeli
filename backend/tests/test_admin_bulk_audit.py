"""Admin bulk actions + audit filter/search API tests.
Modules covered: /api/players/bulk and /api/audit.
"""

import os
import pytest
import requests


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
PLAYER_ID = 1
PLAYER_NAME_Q = "testpelaaja"


@pytest.fixture(scope="module")
def api_client():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL is missing")
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _players(api_client):
    r = api_client.get(f"{BASE_URL.rstrip('/')}/api/players")
    assert r.status_code == 200
    return r.json()


def _player_by_id(api_client, pid):
    players = _players(api_client)
    row = next((p for p in players if p["id"] == pid), None)
    assert row is not None
    return row


def test_bulk_set_streak_updates_player(api_client):
    before = _player_by_id(api_client, PLAYER_ID)
    original_mode = before.get("streak_mode") or "normal"

    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/players/bulk",
        json={"ids": [PLAYER_ID], "action": "set_streak", "mode": "win"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["action"] == "set_streak"
    assert data["affected"] >= 1

    after = _player_by_id(api_client, PLAYER_ID)
    assert after["streak_mode"] == "win"

    # Restore original state for stable follow-up runs.
    restore = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/players/bulk",
        json={"ids": [PLAYER_ID], "action": "set_streak", "mode": original_mode},
    )
    assert restore.status_code == 200


def test_bulk_grant_spins_updates_player(api_client):
    before = _player_by_id(api_client, PLAYER_ID)
    before_spins = int(before.get("spins_remaining") or 0)

    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/players/bulk",
        json={"ids": [PLAYER_ID], "action": "grant_spins", "count": 1},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["action"] == "grant_spins"

    after = _player_by_id(api_client, PLAYER_ID)
    assert int(after.get("spins_remaining") or 0) == before_spins + 1

    # Restore original state for stable follow-up runs.
    restore = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/players/bulk",
        json={"ids": [PLAYER_ID], "action": "grant_spins", "count": -1},
    )
    assert restore.status_code == 200


def test_audit_action_filter_returns_bulk_events(api_client):
    r = api_client.get(
        f"{BASE_URL.rstrip('/')}/api/audit",
        params={"action": "bulk_set_streak", "limit": 20},
    )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert all(item["action"] == "bulk_set_streak" for item in data)


def test_audit_search_returns_bulk_entries(api_client):
    r = api_client.get(
        f"{BASE_URL.rstrip('/')}/api/audit",
        params={"q": PLAYER_NAME_Q, "limit": 50},
    )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(
        "bulk_" in (item.get("action") or "")
        and PLAYER_NAME_Q in (item.get("player_name") or "").lower()
        for item in data
    )
