"""Unit coverage for theme-specific slot free-spin mechanics."""

import sys

sys.path.insert(0, "/app")

from app import SLOT_THEMES, _slot_run_free_spins  # noqa: E402


def test_egypt_free_spins_use_expanding_symbol():
    results, payout, feature = _slot_run_free_spins("egypt", 100, SLOT_THEMES["egypt"])
    assert feature["type"] == "expanding_symbol"
    assert feature["expanding_symbol"] not in ("wild", "scatter")
    assert len(results) == SLOT_THEMES["egypt"]["free_spins"]
    assert isinstance(payout, int)
    for spin in results:
        sym = spin["feature"]["expanding_symbol"]
        for col in spin["feature"]["expanded_reels"]:
            assert all(spin["grid"][col][row] == sym for row in range(3))


def test_space_free_spins_expand_wilds_and_can_retrigger():
    results, payout, feature = _slot_run_free_spins("space", 100, SLOT_THEMES["space"])
    assert feature["type"] == "expanding_wild_respins"
    assert SLOT_THEMES["space"]["free_spins"] <= len(results) <= SLOT_THEMES["space"]["free_spins"] + 7
    assert isinstance(payout, int)
    for spin in results:
        for col in spin["feature"]["expanded_wild_reels"]:
            assert all(spin["grid"][col][row] == "wild" for row in range(3))


def test_fruits_free_spins_use_fire_joker_multiplier_ladder():
    results, payout, feature = _slot_run_free_spins("fruits", 100, SLOT_THEMES["fruits"])
    assert feature["type"] == "fire_joker_ladder"
    assert len(results) == SLOT_THEMES["fruits"]["free_spins"]
    assert isinstance(payout, int)
    multipliers = [spin["feature"]["bonus_multiplier"] for spin in results]
    assert min(multipliers) >= SLOT_THEMES["fruits"]["fs_mult"]
    assert max(multipliers) <= 5
    assert multipliers == sorted(multipliers)
