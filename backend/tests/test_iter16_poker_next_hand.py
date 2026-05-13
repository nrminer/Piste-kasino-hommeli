"""Regression tests for operator poker next-hand preset flow."""

import os
import requests


def _read_frontend_backend_url() -> str | None:
    try:
        with open("/app/frontend/.env", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        return None
    return None


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or _read_frontend_backend_url()


def _base_url() -> str:
    assert BASE_URL, "REACT_APP_BACKEND_URL must be set"
    return BASE_URL.rstrip("/")


def _api_client() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _operator_token(client: requests.Session) -> str:
    # Operator auth module
    r = client.post(f"{_base_url()}/api/operator/login", json={"password": "operator123"}, timeout=20)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data.get("access_token"), str) and data["access_token"]
    return data["access_token"]


def _customer_login(client: requests.Session) -> dict:
    # Customer login module
    r = client.post(
        f"{_base_url()}/api/customer/login",
        json={"name": "Test Player", "password": "test123"},
        timeout=20,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Player"
    assert isinstance(data.get("id"), int)
    return data


def test_operator_login_and_operator_me():
    # Operator auth module
    client = _api_client()
    token = _operator_token(client)
    r = client.get(
        f"{_base_url()}/api/operator/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["role"] == "operator"


def test_poker_deal_accepts_community_preset_with_null_slots_and_applies_presets():
    # Poker preset/deal/advance module
    client = _api_client()
    customer = _customer_login(client)

    new_game = client.post(f"{_base_url()}/api/poker/new", timeout=20)
    assert new_game.status_code == 200
    new_data = new_game.json()
    assert isinstance(new_data.get("id"), int)
    assert new_data.get("status") == "waiting"

    join = client.post(
        f"{_base_url()}/api/poker/join",
        json={"name": customer["name"], "player_id": customer["id"]},
        timeout=20,
    )
    assert join.status_code == 200
    join_data = join.json()
    assert isinstance(join_data.get("token"), str) and join_data["token"]

    state_waiting = client.get(f"{_base_url()}/api/poker/state", timeout=20)
    assert state_waiting.status_code == 200
    waiting_data = state_waiting.json()
    seats = waiting_data.get("seats", [])
    seat = next((s for s in seats if s.get("player_name") == customer["name"] and s.get("active")), None)
    assert seat is not None
    seat_id = str(seat["id"])

    preset_payload = {
        seat_id: [{"rank": "A", "suit": "♠"}, {"rank": "K", "suit": "♠"}],
        "community": [{"rank": "Q", "suit": "♥"}, None, None, None, None],
    }
    preset = client.post(f"{_base_url()}/api/poker/preset", json=preset_payload, timeout=20)
    assert preset.status_code == 200
    assert preset.json().get("ok") is True

    deal = client.post(f"{_base_url()}/api/poker/deal", timeout=20)
    assert deal.status_code == 200
    deal_data = deal.json()
    assert deal_data["ok"] is True
    assert deal_data["stage"] == "preflop"

    post_deal_state = client.get(f"{_base_url()}/api/poker/state", timeout=20)
    assert post_deal_state.status_code == 200
    post_deal_data = post_deal_state.json()
    seat_after = next((s for s in post_deal_data.get("seats", []) if s.get("id") == seat["id"]), None)
    assert seat_after is not None
    assert seat_after.get("hole_cards") == [{"rank": "A", "suit": "♠"}, {"rank": "K", "suit": "♠"}]

    advance = client.post(f"{_base_url()}/api/poker/advance", timeout=20)
    assert advance.status_code == 200
    advance_data = advance.json()
    assert advance_data["stage"] == "flop"
    assert isinstance(advance_data.get("community_cards"), list)
    assert advance_data["community_cards"][0] == {"rank": "Q", "suit": "♥"}
