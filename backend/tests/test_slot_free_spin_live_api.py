"""Live API regression for slot free-spin feature payloads.
Modules covered: /api/points/<pid>/slots free_spin_feature by theme.
"""

import os

import pytest
import requests


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
PLAYER_ID = 1
BET = 10


@pytest.fixture(scope="module")
def api_client():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL is missing")
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module", autouse=True)
def ensure_points(api_client):
    base = BASE_URL.rstrip("/")
    r = api_client.post(
        f"{base}/api/players/{PLAYER_ID}/points/grant",
        json={"count": 20000, "reason": "TEST_slot_free_spin_live_api"},
    )
    assert r.status_code == 200, f"Failed to grant points: {r.status_code} {r.text[:200]}"


def _spin_until_free_spins(api_client, theme: str, max_spins: int = 250):
    base = BASE_URL.rstrip("/")
    for _ in range(max_spins):
        r = api_client.post(
            f"{base}/api/points/{PLAYER_ID}/slots",
            json={"bet": BET, "theme": theme},
        )
        assert r.status_code == 200, f"{theme} spin failed: {r.status_code} {r.text[:200]}"
        data = r.json()
        if data.get("free_spins_triggered"):
            return data
    pytest.skip(f"No free-spins trigger for theme={theme} within {max_spins} spins")


def test_fruits_fire_joker_ladder_feature_payload(api_client):
    data = _spin_until_free_spins(api_client, "fruits")
    feature = data.get("free_spin_feature") or {}
    results = data.get("free_spin_results") or []

    assert feature.get("type") == "fire_joker_ladder"
    assert data.get("free_spin_count") == 8
    assert len(results) == 8

    for idx, spin in enumerate(results, start=1):
        sf = spin.get("feature") or {}
        assert sf.get("type") == "fire_joker_ladder"
        assert isinstance(sf.get("bonus_multiplier"), int)
        assert isinstance(sf.get("next_multiplier"), int)
        assert 1 <= sf.get("spin_index", 0) <= 8
        assert sf.get("spin_index") == idx


def test_egypt_expanding_symbol_feature_payload(api_client):
    data = _spin_until_free_spins(api_client, "egypt")
    feature = data.get("free_spin_feature") or {}
    results = data.get("free_spin_results") or []

    assert feature.get("type") == "expanding_symbol"
    assert feature.get("expanding_symbol") not in ("wild", "scatter", None)
    assert data.get("free_spin_count") == 10
    assert len(results) == 10

    for spin in results:
        sf = spin.get("feature") or {}
        symbol = sf.get("expanding_symbol")
        assert sf.get("type") == "expanding_symbol"
        for col in sf.get("expanded_reels") or []:
            assert all(spin["grid"][col][row] == symbol for row in range(3))


def test_space_expanding_wild_respins_feature_payload(api_client):
    data = _spin_until_free_spins(api_client, "space")
    feature = data.get("free_spin_feature") or {}
    results = data.get("free_spin_results") or []

    assert feature.get("type") == "expanding_wild_respins"
    assert data.get("free_spin_count") == 8
    assert 8 <= len(results) <= 15

    for spin in results:
        sf = spin.get("feature") or {}
        assert sf.get("type") == "expanding_wild_respins"
        assert isinstance(sf.get("new_wild_reels"), list)
        assert isinstance(sf.get("expanded_wild_reels"), list)
        assert isinstance(sf.get("extra_spin_awarded"), bool)
        assert 8 <= int(sf.get("total_spins", 8)) <= 15
        for col in sf.get("expanded_wild_reels"):
            assert all(spin["grid"][col][row] == "wild" for row in range(3))
