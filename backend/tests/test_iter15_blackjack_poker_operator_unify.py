"""Iter15 regression tests for unified poker panels + blackjack multi-hand.

Coverage in this file:
- customer/operator auth smoke for requested credentials
- poker state and legacy join page availability
- blackjack keep_active multi-hand behavior + no-stuck-on-21 regression
"""

import os
import pytest
import requests


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")

if not BASE_URL:
    pytestmark = pytest.mark.skip(reason="REACT_APP_BACKEND_URL is not set")


@pytest.fixture(scope="session")
def api_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def ensure_player_balance(api_session):
    # Ensure enough points for repeated blackjack starts/actions.
    pid = 1
    bal_resp = api_session.get(f"{BASE_URL}/api/players/{pid}/points", timeout=20)
    if bal_resp.status_code != 200:
        pytest.skip(f"Unable to read player balance for pid=1: {bal_resp.status_code}")
    current = int(bal_resp.json().get("points", 0))
    if current < 5000:
        api_session.post(
            f"{BASE_URL}/api/players/{pid}/points/grant",
            json={"count": 10000, "reason": "TEST_iter15_topup"},
            timeout=20,
        )
    return pid


class TestAuthAndPanelAvailability:
    """Auth and panel integration entrypoints."""

    def test_customer_login_with_requested_demo_account(self, api_session):
        resp = api_session.post(
            f"{BASE_URL}/api/customer/login",
            json={"name": "Test Player", "password": "test123"},
            timeout=20,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("name") == "Test Player"
        assert isinstance(data.get("id"), int)

    def test_operator_login_with_operator123(self, api_session):
        resp = api_session.post(
            f"{BASE_URL}/api/operator/login",
            json={"password": "operator123"},
            timeout=20,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data.get("access_token"), str) and data["access_token"]

        me = api_session.get(
            f"{BASE_URL}/api/operator/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
            timeout=20,
        )
        assert me.status_code == 200, me.text
        me_data = me.json()
        assert me_data.get("ok") is True
        assert me_data.get("role") == "operator"

    def test_poker_state_endpoint_loads(self, api_session):
        resp = api_session.get(f"{BASE_URL}/api/poker/state", timeout=20)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "status" in data

    def test_legacy_poker_join_page_loads(self, api_session):
        resp = api_session.get(f"{BASE_URL}/poker/join", timeout=20)
        assert resp.status_code == 200, resp.text
        assert "Pokeripöytä" in resp.text or "Kirjaudu sisään" in resp.text


class TestBlackjackMultiHandRegression:
    """Blackjack multi-hand regressions for keep_active + hit-to-21 settle."""

    def test_keep_active_true_preserves_previous_hand(self, api_session, ensure_player_balance):
        pid = ensure_player_balance
        hand1 = api_session.post(
            f"{BASE_URL}/api/points/{pid}/blackjack/start",
            json={"bet": 25, "keep_active": True},
            timeout=20,
        )
        assert hand1.status_code == 200, hand1.text
        d1 = hand1.json()
        assert isinstance(d1.get("game_id"), int)

        hand2 = api_session.post(
            f"{BASE_URL}/api/points/{pid}/blackjack/start",
            json={"bet": 25, "keep_active": True},
            timeout=20,
        )
        assert hand2.status_code == 200, hand2.text
        d2 = hand2.json()
        assert isinstance(d2.get("game_id"), int)
        assert d2["game_id"] != d1["game_id"]

        stand_first = api_session.post(
            f"{BASE_URL}/api/points/blackjack/{d1['game_id']}/action",
            json={"action": "stand"},
            timeout=20,
        )
        assert stand_first.status_code == 200, stand_first.text
        st = stand_first.json()
        assert st.get("status", "").startswith("done_")
        assert st.get("outcome") in {"win", "loss", "push", "blackjack", "bust"}

        # Second hand should still be actionable or already naturally resolved.
        hit_or_done = api_session.post(
            f"{BASE_URL}/api/points/blackjack/{d2['game_id']}/action",
            json={"action": "hit"},
            timeout=20,
        )
        assert hit_or_done.status_code in (200, 400), hit_or_done.text
        if hit_or_done.status_code == 400:
            err = (hit_or_done.json().get("error") or "").lower()
            assert "päättynyt" in err

    def test_hit_to_21_does_not_leave_active_stuck_hand(self, api_session, ensure_player_balance):
        pid = ensure_player_balance
        observed_21 = False

        for _ in range(35):
            start = api_session.post(
                f"{BASE_URL}/api/points/{pid}/blackjack/start",
                json={"bet": 25},
                timeout=20,
            )
            assert start.status_code == 200, start.text
            state = start.json()

            if state.get("status") != "active":
                continue

            gid = state["game_id"]

            for _ in range(6):
                act = api_session.post(
                    f"{BASE_URL}/api/points/blackjack/{gid}/action",
                    json={"action": "hit"},
                    timeout=20,
                )
                assert act.status_code == 200, act.text
                j = act.json()
                total = int(j.get("player_total", 0))

                if total == 21:
                    observed_21 = True
                    assert j.get("status") != "active", j
                    assert j.get("outcome") in {"win", "loss", "push", "blackjack", "bust"}, j
                    break

                if j.get("status") != "active" or total > 21:
                    break

            if observed_21:
                break

        assert observed_21, "Could not observe a hit-to-21 scenario within retry budget"
