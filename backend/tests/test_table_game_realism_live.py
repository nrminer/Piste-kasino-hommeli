"""Live API realism regression for customer table games (coinflip, war, baccarat, blackjack, pikapokeri)."""

import os
import sys

import pytest
import requests

sys.path.insert(0, "/app")
from app import PIKAPOKERI_PAYOUTS  # noqa: E402


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
PLAYER_ID = 1


@pytest.fixture(scope="module")
def api_client():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL is missing")
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module", autouse=True)
def ensure_test_points(api_client):
    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/players/{PLAYER_ID}/points/grant",
        json={"count": 50000, "reason": "TEST_table_game_realism_live"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert isinstance(data.get("points"), int)


def test_coinflip_rules_metadata_and_payout_shape(api_client):
    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/points/{PLAYER_ID}/coinflip",
        json={"bet": 100, "choice": "heads"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["outcome"] in {"win", "loss"}
    assert d["choice"] == "heads"
    assert d["result"] in {"heads", "tails"}
    assert d["rules"]["name"] == "Casino Coinflip"
    assert d["rules"]["rtp_target"] == 0.96
    assert d["rules"]["payout"] == "1:1"
    assert d["payout"] in {0, 200}


def test_war_outcomes_include_casino_variants_and_tie_breaker_metadata(api_client):
    reset_streak = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/players/{PLAYER_ID}/streak",
        json={"mode": "normal"},
    )
    assert reset_streak.status_code == 200

    outcomes_seen = set()
    tie_war_payload = None

    for _ in range(200):
        r = api_client.post(
            f"{BASE_URL.rstrip('/')}/api/points/{PLAYER_ID}/war",
            json={"bet": 100},
        )
        assert r.status_code == 200
        d = r.json()
        outcome = d["outcome"]
        outcomes_seen.add(outcome)
        assert outcome in {"win", "loss", "war_win", "war_loss", "war_push", "surrender"}
        assert d["rules"]["name"] == "Casino War"
        assert "tie" in d["rules"]
        assert set(d["player_card"].keys()) == {"rank", "suit"}
        assert set(d["dealer_card"].keys()) == {"rank", "suit"}
        if outcome in {"war_win", "war_loss", "war_push"}:
            tie_war_payload = d
            break

    assert tie_war_payload is not None
    assert tie_war_payload["tie_breaker"] is not None
    assert set(tie_war_payload["tie_breaker"].keys()) == {
        "player_card",
        "dealer_card",
        "burn_count_each",
    }


@pytest.mark.parametrize("side", ["player", "banker", "tie"])
def test_baccarat_real_rules_metadata_draw_events_and_payouts(api_client, side):
    bet = 100
    r = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/points/{PLAYER_ID}/baccarat",
        json={"bet": bet, "side": side},
    )
    assert r.status_code == 200
    d = r.json()

    assert d["winner"] in {"player", "banker", "tie"}
    assert d["outcome"] in {"win", "loss", "push"}
    assert isinstance(d["natural"], bool)
    assert isinstance(d["draw_events"], list)
    assert d["rules"]["name"] == "Punto Banco Baccarat"
    assert d["rules"]["banker_commission"] == "5%"
    assert d["rules"]["tie_pays"] == "8:1"

    winner = d["winner"]
    if winner == side:
        expected = {"player": 200, "banker": 195, "tie": 900}[side]
        assert d["outcome"] == "win"
        assert d["payout"] == expected
    elif winner == "tie" and side in {"player", "banker"}:
        assert d["outcome"] == "push"
        assert d["payout"] == 100
    else:
        assert d["outcome"] == "loss"
        assert d["payout"] == 0


def test_blackjack_start_and_double_flow(api_client):
    start = api_client.post(
        f"{BASE_URL.rstrip('/')}/api/points/{PLAYER_ID}/blackjack/start",
        json={"bet": 100},
    )
    assert start.status_code == 200
    s = start.json()
    assert s["rules"]["blackjack_pays"] == "3:2"
    assert s["rules"]["dealer"] == "stands on all 17s"

    if s["status"] == "active":
        act = api_client.post(
            f"{BASE_URL.rstrip('/')}/api/points/blackjack/{s['game_id']}/action",
            json={"action": "double"},
        )
        assert act.status_code == 200
        a = act.json()
        assert a["bet"] == 200
        assert "player_cards" in a and "dealer_cards" in a
        assert isinstance(a["points"], int)
    else:
        assert s["status"] in {"done_blackjack", "done_push", "done_loss"}


def test_blackjack_insurance_path_when_available(api_client):
    attempts = 0
    saw_insurance_offer = False
    while attempts < 20 and not saw_insurance_offer:
        attempts += 1
        start = api_client.post(
            f"{BASE_URL.rstrip('/')}/api/points/{PLAYER_ID}/blackjack/start",
            json={"bet": 100},
        )
        assert start.status_code == 200
        s = start.json()
        if s.get("insurance_available"):
            saw_insurance_offer = True
            ins = api_client.post(
                f"{BASE_URL.rstrip('/')}/api/points/blackjack/{s['game_id']}/action",
                json={"action": "insurance"},
            )
            assert ins.status_code == 200
            i = ins.json()
            assert i["insurance_result"] in {"win", "loss"}
            assert isinstance(i["insurance_amount"], int)
            assert isinstance(i["insurance_payout"], int)

    if not saw_insurance_offer:
        pytest.skip("Dealer ace upcard not observed within 20 starts")


def test_pikapokeri_flow_and_fullpay_table_constant(api_client):
    assert PIKAPOKERI_PAYOUTS == {9: 800, 8: 50, 7: 25, 6: 9, 5: 6, 4: 4, 3: 3, 2: 2, 1: 1}

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
        json={"hold": [0, 1]},
    )
    assert draw.status_code == 200
    d = draw.json()
    assert len(d["hand"]) == 5
    assert d["outcome"] in {"win", "loss"}
    assert isinstance(d["payout"], int)
    assert isinstance(d["points"], int)
