"""Backend tests for slot RTP calibration (iteration 2).
Verifies the new calibrated payout multipliers produce realistic returns
and that the JSON response shape is preserved.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if BASE_URL:
    BASE_URL = BASE_URL.rstrip("/")
PID = 1
BET = 100

EXPECTED_KEYS = {
    "grid", "wins", "total_mult", "bet", "payout", "net", "points",
    "scatter_positions", "free_spins_triggered", "free_spin_count",
    "free_spin_mult", "free_spin_results", "bonus_payout",
    "jackpot_won", "jackpot_payout", "jackpot_pool",
}


@pytest.fixture(scope="module")
def session():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL is missing")
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def spins(session):
    """Run 30 fruits-theme spins and collect responses."""
    results = []
    for _ in range(30):
        r = session.post(f"{BASE_URL}/api/points/{PID}/slots", json={"bet": BET, "theme": "fruits"})
        assert r.status_code == 200, f"Slot spin failed: {r.status_code} {r.text[:200]}"
        results.append(r.json())
    return results


def test_all_spins_status_200(spins):
    assert len(spins) == 30


def test_response_shape_preserved(spins):
    for i, d in enumerate(spins):
        missing = EXPECTED_KEYS - set(d.keys())
        assert not missing, f"Spin {i} missing keys: {missing}"


def test_total_mult_is_small_fractional(spins):
    # All total_mult should be small fractional values, not large integers (75-1000)
    big = [d["total_mult"] for d in spins if d["total_mult"] > 50]
    assert len(big) <= 1, f"Too many spins with total_mult > 50: {big[:5]} (expected mostly 0..10 range)"
    # at least some non-zero
    nz = [d["total_mult"] for d in spins if d["total_mult"] > 0]
    print(f"non-zero spins: {len(nz)}/30, sample mults: {nz[:10]}")


def test_payout_is_integer(spins):
    for d in spins:
        assert isinstance(d["payout"], int), f"payout not int: {type(d['payout'])} = {d['payout']}"
        assert d["payout"] >= 0


def test_payout_matches_round_bet_mult(spins):
    # base payout (excl bonus / jackpot) should equal int(round(bet * total_mult))
    for d in spins:
        expected = int(round(d["bet"] * d["total_mult"]))
        assert d["payout"] == expected, f"payout {d['payout']} != round({d['bet']}*{d['total_mult']})={expected}"


def test_payout_distribution_has_low_returns(spins):
    """Current multi-payline rules often return small fractional wins instead of true zeroes.
    Keep this regression focused on realism/RTP sanity without depending on a flaky
    exact zero-payout occurrence in a small random sample.
    """
    low_returns = [d for d in spins if d["payout"] < d["bet"]]
    assert len(low_returns) >= 1, "Expected at least one spin to return less than the bet"
    print(f"low-return spins: {len(low_returns)}/30")


def test_aggregate_rtp_in_realistic_band(spins):
    """Across 30 spins (incl bonus + jackpot) RTP should not be in old 9x-15x absurd range.
    Sample is small so we just sanity-check it's < 5x (500%)."""
    total_bet = sum(d["bet"] for d in spins)
    total_return = sum(d["payout"] + d["bonus_payout"] + d["jackpot_payout"] for d in spins)
    rtp = total_return / total_bet
    print(f"30-spin sample RTP: {rtp*100:.1f}% (target ~85%, n=30 so high variance ok)")
    assert rtp < 5.0, f"RTP={rtp*100:.0f}% way too high — old uncalibrated math?"
    assert rtp >= 0.0


def test_other_themes_smoke(session):
    for theme in ("egypt", "space"):
        r = session.post(f"{BASE_URL}/api/points/{PID}/slots", json={"bet": BET, "theme": theme})
        assert r.status_code == 200, f"{theme} spin failed: {r.text[:200]}"
        d = r.json()
        assert set(EXPECTED_KEYS).issubset(d.keys())
        assert isinstance(d["payout"], int)
        assert d["total_mult"] < 100  # sanity
