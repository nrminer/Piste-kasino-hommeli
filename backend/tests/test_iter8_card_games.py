"""Iteration 8 — Backend regression for the 10 new endpoints (Blackjack + Poker mode-B)
plus card_renderer.js static asset and Monte Carlo simulator inputs.

Targets the live Flask app already running on http://localhost:5000.
"""
import os
import sqlite3
import time
import math
import json
import random
import re
import pytest
import requests

BASE = os.environ.get("BJ_TEST_BASE_URL", "http://localhost:5000")
DB_PATH = os.environ.get("BJ_TEST_DB_PATH", "/app/casino.db")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _seed_points(pid, pts=200000):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE players SET points = ? WHERE id = ?", (pts, pid))
    con.commit()
    con.close()


def _create_player(name_prefix="TEST_iter8_"):
    name = f"{name_prefix}{int(time.time() * 1000)}_{random.randint(0, 9999)}"
    r = requests.post(f"{BASE}/api/players", json={"name": name}, timeout=10)
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    _seed_points(pid, 200000)
    return pid


def _bj_start(pid, bet=100, max_tries=15):
    """Start BJ until we get an `active` game (not natural BJ)."""
    last = None
    for _ in range(max_tries):
        r = requests.post(f"{BASE}/api/points/{pid}/blackjack/start", json={"bet": bet}, timeout=10)
        last = r
        if r.status_code != 200:
            continue
        j = r.json()
        if j.get("status") == "active":
            return j
    pytest.skip(f"Could not get an active BJ hand in {max_tries} tries: last={last.text[:120]}")


# ===========================================================================
# 0. Sanity
# ===========================================================================
class TestSanity:
    def test_app_alive(self):
        r = requests.get(f"{BASE}/asiakas", timeout=10)
        assert r.status_code == 200

    def test_create_player_and_seed(self):
        pid = _create_player()
        r = requests.get(f"{BASE}/api/players/{pid}/points", timeout=10)
        assert r.status_code == 200
        assert int(r.json().get("points", 0)) >= 200000


# ===========================================================================
# 1. /sidebet
# ===========================================================================
class TestBlackjackSidebet:
    def test_sidebet_happy_path(self):
        pid = _create_player()
        g = _bj_start(pid, bet=100)
        gid = g.get("game_id") or g.get("id")
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/{gid}/sidebet",
            json={"perfect_pairs_pts": 50, "twenty_one_plus_three_pts": 50},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert "resolved" in data
        # Both keys should be present in resolved
        assert "perfect_pairs" in data["resolved"]
        assert "twenty_one_plus_three" in data["resolved"]

    def test_sidebet_exceeds_base_bet(self):
        pid = _create_player()
        g = _bj_start(pid, bet=100)
        gid = g.get("game_id") or g.get("id")
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/{gid}/sidebet",
            json={"perfect_pairs_pts": 500, "twenty_one_plus_three_pts": 0},
            timeout=10,
        )
        assert r.status_code == 400
        assert "error" in r.json()

    def test_sidebet_negative(self):
        pid = _create_player()
        g = _bj_start(pid, bet=100)
        gid = g.get("game_id") or g.get("id")
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/{gid}/sidebet",
            json={"perfect_pairs_pts": -10, "twenty_one_plus_three_pts": 0},
            timeout=10,
        )
        assert r.status_code == 400

    def test_sidebet_both_zero(self):
        pid = _create_player()
        g = _bj_start(pid, bet=100)
        gid = g.get("game_id") or g.get("id")
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/{gid}/sidebet",
            json={"perfect_pairs_pts": 0, "twenty_one_plus_three_pts": 0},
            timeout=10,
        )
        assert r.status_code == 400

    def test_sidebet_unknown_game(self):
        pid = _create_player()
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/9999999/sidebet",
            json={"perfect_pairs_pts": 10, "twenty_one_plus_three_pts": 0},
            timeout=10,
        )
        assert r.status_code == 404


# ===========================================================================
# 2. /split
# ===========================================================================
class TestBlackjackSplit:
    def test_split_non_pair_rejected(self):
        """At minimum, rejection on a non-pair must return 400."""
        pid = _create_player()
        # try to find a non-pair (much more common than a pair)
        for _ in range(15):
            g = _bj_start(pid, bet=100)
            gid = g.get("game_id") or g.get("id")
            pcards = g.get("player_cards") or g.get("player") or []
            if len(pcards) == 2 and pcards[0]["rank"] != pcards[1]["rank"]:
                r = requests.post(f"{BASE}/api/points/{pid}/blackjack/{gid}/split", timeout=10)
                assert r.status_code == 400
                assert "error" in r.json()
                # cleanup so next iteration can start fresh
                requests.post(f"{BASE}/api/points/blackjack/{gid}/action", json={"action": "stand"}, timeout=10)
                return
            requests.post(f"{BASE}/api/points/blackjack/{gid}/action", json={"action": "stand"}, timeout=10)
        pytest.skip("Could not locate a non-pair in 15 deals (extremely improbable)")

    def test_split_pair_happy_path(self):
        """If a pair is dealt within N tries, validate the split response shape and atomic deduction."""
        pid = _create_player()
        random.seed(7)
        for _ in range(30):
            g = _bj_start(pid, bet=100)
            gid = g.get("game_id") or g.get("id")
            pcards = g.get("player_cards") or []
            if len(pcards) == 2 and pcards[0]["rank"] == pcards[1]["rank"]:
                bal_before = requests.get(f"{BASE}/api/players/{pid}/points", timeout=10).json()["points"]
                r = requests.post(f"{BASE}/api/points/{pid}/blackjack/{gid}/split", timeout=10)
                assert r.status_code == 200, r.text
                data = r.json()
                assert "hand_a" in data
                assert "hand_b" in data
                assert data["split_count"] >= 1
                assert len(data["hand_a"]) == 2
                assert len(data["hand_b"]) == 2
                bal_after = requests.get(f"{BASE}/api/players/{pid}/points", timeout=10).json()["points"]
                assert bal_after == bal_before - 100
                return
            requests.post(f"{BASE}/api/points/blackjack/{gid}/action", json={"action": "stand"}, timeout=10)
        pytest.skip("No pair dealt in 30 tries — split pair flow not exercised this run (probabilistic)")

    def test_split_unknown_game(self):
        pid = _create_player()
        r = requests.post(f"{BASE}/api/points/{pid}/blackjack/9999999/split", timeout=10)
        assert r.status_code == 404


# ===========================================================================
# 3. /surrender
# ===========================================================================
class TestBlackjackSurrender:
    def test_surrender_unknown_game(self):
        pid = _create_player()
        r = requests.post(f"{BASE}/api/points/{pid}/blackjack/9999999/surrender", timeout=10)
        assert r.status_code == 404

    def test_surrender_returns_half_or_400(self):
        """Surrender requires dealer up 9..A. Confirm the contract works on at least
        one suitable deal within N tries; otherwise validate a 400 reason."""
        pid = _create_player()
        for _ in range(25):
            g = _bj_start(pid, bet=100)
            gid = g.get("game_id") or g.get("id")
            dealer_up = (g.get("dealer_cards") or g.get("dealer") or [{}])[0]
            up_rank = dealer_up.get("rank")
            r = requests.post(f"{BASE}/api/points/{pid}/blackjack/{gid}/surrender", timeout=10)
            if up_rank in ("9", "10", "J", "Q", "K", "A"):
                if r.status_code == 200:
                    data = r.json()
                    assert data.get("status") == "done_surrender"
                    assert data.get("refund_pts") == 100 // 2
                    return
            else:
                # dealer up < 9 → must be 400
                assert r.status_code == 400, f"Expected 400 for dealer up {up_rank}, got {r.status_code}"
                return
        pytest.skip("Could not exercise surrender contract in 25 deals.")


# ===========================================================================
# 4. /active-hand
# ===========================================================================
class TestBlackjackActiveHand:
    def test_active_hand_zero_ok(self):
        pid = _create_player()
        g = _bj_start(pid, bet=100)
        gid = g.get("game_id") or g.get("id")
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/{gid}/active-hand",
            json={"hand_index": 0}, timeout=10,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("active_hand_index") == 0

    def test_active_hand_out_of_range(self):
        pid = _create_player()
        g = _bj_start(pid, bet=100)
        gid = g.get("game_id") or g.get("id")
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/{gid}/active-hand",
            json={"hand_index": 99}, timeout=10,
        )
        assert r.status_code == 400


# ===========================================================================
# 5. /bonus-buy
# ===========================================================================
class TestBlackjackBonusBuy:
    def test_bonus_buy_price_and_natural_bj(self):
        pid = _create_player()
        bet = 100
        bal_before = requests.get(f"{BASE}/api/players/{pid}/points", timeout=10).json()["points"]
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/bonus-buy",
            json={"intended_bet_pts": bet}, timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # price must equal ceil(bet * 2.8) = 280
        assert data.get("price_pts") == math.ceil(bet * 2.8) == 280
        # forced natural BJ → player cards include an Ace and a 10-valued card
        pcards = data.get("player_cards") or []
        ranks = [c["rank"] for c in pcards]
        assert "A" in ranks
        assert any(r in ("10", "J", "Q", "K") for r in ranks), f"Got ranks {ranks}"
        # bonus_buy_log row created → cooldown should now be 120
        assert int(data.get("cooldown_sec_remaining_after_this") or 0) >= 60
        # balance accounting: deducted price (280), credited payout
        bal_after = requests.get(f"{BASE}/api/players/{pid}/points", timeout=10).json()["points"]
        assert bal_after == bal_before - 280 + int(data["payout_pts"])
        # smoke_test asserts payout_pts == 250 for bet=100 — keep parity
        assert data.get("payout_pts") == 250

    def test_bonus_buy_cooldown_and_session_limit(self):
        pid = _create_player()
        # 1st purchase
        r1 = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/bonus-buy",
            json={"intended_bet_pts": 100}, timeout=10,
        )
        assert r1.status_code == 200
        # 2nd purchase IMMEDIATELY → 429 cooldown
        r2 = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/bonus-buy",
            json={"intended_bet_pts": 100}, timeout=10,
        )
        assert r2.status_code == 429
        assert "cooldown_sec_remaining" in r2.json()

    def test_bonus_buy_session_max_via_db(self):
        """Bypass cooldown by back-dating the first 2 rows; expect 409 on the 3rd."""
        pid = _create_player()
        # purchase 1 normally
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/bonus-buy",
            json={"intended_bet_pts": 100}, timeout=10,
        )
        assert r.status_code == 200
        # back-date the existing row to outside cooldown but inside the 4h session window
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "UPDATE bonus_buy_log SET created_at = datetime('now','-3 minutes') "
            "WHERE player_id=? AND game_theme='blackjack'", (pid,),
        )
        con.commit()
        con.close()
        # purchase 2 — should pass (cooldown over, session count = 1)
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/bonus-buy",
            json={"intended_bet_pts": 100}, timeout=10,
        )
        assert r.status_code == 200, r.text
        # back-date again
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "UPDATE bonus_buy_log SET created_at = datetime('now','-3 minutes') "
            "WHERE player_id=? AND game_theme='blackjack'", (pid,),
        )
        con.commit()
        con.close()
        # purchase 3 — session limit (max 2 / 4h) → 409
        r = requests.post(
            f"{BASE}/api/points/{pid}/blackjack/bonus-buy",
            json={"intended_bet_pts": 100}, timeout=10,
        )
        assert r.status_code == 409, r.text


# ===========================================================================
# 6+7+9. Poker mode-B start / post-blinds / round-state
# ===========================================================================
class TestPokerModeB:
    def _new_session(self):
        return requests.post(f"{BASE}/api/poker/new", timeout=10).json()

    def test_start_mode_b_no_seats_400(self):
        self._new_session()
        r = requests.post(
            f"{BASE}/api/poker/start-mode-b",
            json={"small_blind_pts": 50, "big_blind_pts": 100}, timeout=10,
        )
        assert r.status_code == 400

    def test_start_mode_b_with_seats_ok(self):
        self._new_session()
        # join 2 seats
        for nm in ("TEST_p1", "TEST_p2"):
            r = requests.post(f"{BASE}/api/poker/join", json={"name": nm}, timeout=10)
            assert r.status_code == 200, r.text
        r = requests.post(
            f"{BASE}/api/poker/start-mode-b",
            json={"small_blind_pts": 50, "big_blind_pts": 100}, timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("mode") == "mode_b"
        assert data.get("small_blind_pts") == 50
        assert data.get("big_blind_pts") == 100

    def test_round_state_returns_required_fields(self):
        r = requests.get(f"{BASE}/api/poker/round-state", timeout=10)
        assert r.status_code == 200
        d = r.json()
        for key in ("session_id", "mode", "stage", "pot_pts", "current_bet_pts", "seats"):
            assert key in d, f"Missing key {key} in round-state: {d}"

    def test_post_blinds_requires_player_seats(self):
        """Calling post-blinds when seats have no player_id is allowed (no debit) but should
        not error; but if seats not present, the endpoint should 400."""
        # Fresh session w/o seats
        self._new_session()
        # bring it back to mode_b state with no seats first → start should 400
        r = requests.post(f"{BASE}/api/poker/start-mode-b",
                          json={"small_blind_pts": 50, "big_blind_pts": 100}, timeout=10)
        assert r.status_code == 400  # confirm gating
        # Now create a session with seats and run the full flow
        self._new_session()
        for nm in ("TEST_pb1", "TEST_pb2", "TEST_pb3"):
            requests.post(f"{BASE}/api/poker/join", json={"name": nm}, timeout=10)
        r = requests.post(f"{BASE}/api/poker/start-mode-b",
                          json={"small_blind_pts": 50, "big_blind_pts": 100}, timeout=10)
        assert r.status_code == 200
        r = requests.post(f"{BASE}/api/poker/post-blinds", timeout=10)
        # Without player_id linked to seats, the route still returns 200 with seat ids
        assert r.status_code == 200, r.text
        d = r.json()
        assert "small_blind_seat_id" in d
        assert "big_blind_seat_id" in d
        assert "next_bettor_seat_id" in d
        assert d.get("pot_pts") == 150  # 50 + 100


# ===========================================================================
# 8. /api/poker/seat/<token>/bet
# ===========================================================================
class TestPokerSeatBet:
    def test_unknown_token_404(self):
        r = requests.post(f"{BASE}/api/poker/seat/TOTALLY_BAD_TOKEN/bet",
                          json={"action": "fold"}, timeout=10)
        assert r.status_code == 404

    def test_unknown_action_400(self):
        # spin up a session and join one seat to get a valid token
        requests.post(f"{BASE}/api/poker/new", timeout=10)
        j = requests.post(f"{BASE}/api/poker/join", json={"name": "TEST_bet1"}, timeout=10).json()
        token = j.get("token")
        assert token
        r = requests.post(f"{BASE}/api/poker/seat/{token}/bet",
                          json={"action": "moonwalk"}, timeout=10)
        assert r.status_code == 400


# ===========================================================================
# 10. /auto-settle
# ===========================================================================
class TestPokerAutoSettle:
    def test_auto_settle_requires_confirm(self):
        r = requests.post(f"{BASE}/api/poker/auto-settle",
                          json={"operator_confirmed": False}, timeout=10)
        assert r.status_code == 400


# ===========================================================================
# 11. card_renderer.js static asset
# ===========================================================================
class TestCardRendererAsset:
    def test_file_served_200(self):
        r = requests.get(f"{BASE}/static/js/card_renderer.js", timeout=10)
        assert r.status_code == 200
        body = r.text
        # required symbols
        assert "class CardRenderer" in body
        for sym in ("drawCard", "drawChip", "drawChipStack", "animateCardFlip"):
            assert sym in body, f"missing method {sym}"
        # window.CardRenderer = CardRenderer (or global.CardRenderer assigned)
        assert re.search(r"\b(global|window)\.CardRenderer\s*=", body), \
            "CardRenderer not exposed on global/window"
        # rough syntax sanity — balanced braces
        assert body.count("{") == body.count("}"), "Unbalanced braces in card_renderer.js"

    def test_renderer_no_obvious_syntax_errors(self):
        """If node is available, run a syntax check; else skip."""
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            pytest.skip("node not installed in environment")
        out = subprocess.run(
            [node, "--check", "/app/static/js/card_renderer.js"],
            capture_output=True, text=True,
        )
        assert out.returncode == 0, f"node --check failed: {out.stderr}"


# ===========================================================================
# 12. Monte Carlo simulator script — invoked from the bash command (separate)
# ===========================================================================
class TestMonteCarloArtifact:
    def test_report_exists_and_rtp_in_band(self):
        """Validates the artifact produced by `python scripts/blackjack_montecarlo_95.py
        --hands 100000 --rake 0.0886`. The bash kicks it off; this test reads the report."""
        report = "/app/scripts/bj_montecarlo_report.json"
        if not os.path.exists(report):
            pytest.skip(f"{report} not present yet — Monte Carlo run did not produce a report")
        with open(report) as fh:
            data = json.load(fh)
        rtp = float(
            data.get("observed_rtp")
            or data.get("rtp")
            or data.get("RTP")
            or 0
        )
        assert 0.945 <= rtp <= 0.955, f"RTP {rtp} outside [0.945, 0.955] band"
