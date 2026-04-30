#!/usr/bin/env python3
"""Monte-Carlo RTP calibration for the slot themes.

This script imports the actual server slot math and estimates RTP including:
- base game line wins
- theme-specific free-spin features
- expected Bonus Vault pick value

It intentionally excludes progressive jackpot variance from the headline RTP
because the rare jackpot pool depends on live pool state. The 1% contribution
is represented in the production economy via the jackpot pool itself.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    SLOT_THEMES,
    _slot_calc_wins,
    _slot_run_free_spins,
    _slot_scatter_positions,
    _slot_spin,
)


BET = 100
BONUS_VAULT_MULT_SUM = 4.0
TARGET_RTP = 0.85


def simulate_theme(theme_id: str, spins: int, bet: int) -> dict:
    theme = SLOT_THEMES[theme_id]
    total_bet = bet * spins
    base_return = 0
    free_spin_return = 0
    vault_ev = 0.0
    triggers = 0

    for _ in range(spins):
        grid = _slot_spin(theme_id)
        wins = _slot_calc_wins(grid, theme["payouts"])
        base_payout = int(round(bet * sum(w["mult"] for w in wins)))
        base_return += base_payout

        if len(_slot_scatter_positions(grid)) >= 3:
            triggers += 1
            _, bonus_payout, _ = _slot_run_free_spins(theme_id, bet, theme)
            free_spin_return += bonus_payout
            # Three random picks from 12 hidden tiles: EV = sum(mults)/12*3.
            vault_ev += bet * (BONUS_VAULT_MULT_SUM / 12 * 3)

    total_return = base_return + free_spin_return + vault_ev
    return {
        "theme": theme_id,
        "rtp": total_return / total_bet,
        "base": base_return / total_bet,
        "free_spins": free_spin_return / total_bet,
        "bonus_vault_ev": vault_ev / total_bet,
        "trigger_rate": triggers / spins,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spins", type=int, default=150_000)
    parser.add_argument("--seed", type=int, default=850085)
    parser.add_argument("--bet", type=int, default=BET)
    parser.add_argument("--tolerance", type=float, default=0.025)
    args = parser.parse_args()

    random.seed(args.seed)
    rows = [simulate_theme(theme_id, args.spins, args.bet) for theme_id in SLOT_THEMES]
    avg = sum(r["rtp"] for r in rows) / len(rows)

    print(f"Target RTP: {TARGET_RTP * 100:.2f}%")
    for r in rows:
        print(
            f"{r['theme']:>6}: RTP={r['rtp']*100:5.2f}% "
            f"base={r['base']*100:5.2f}% free={r['free_spins']*100:5.2f}% "
            f"vaultEV={r['bonus_vault_ev']*100:4.2f}% trigger={r['trigger_rate']*100:4.2f}%"
        )
    print(f"average: RTP={avg * 100:.2f}%")

    worst_delta = max(abs(r["rtp"] - TARGET_RTP) for r in rows)
    if worst_delta > args.tolerance:
        print(f"FAIL: theme RTP outside ±{args.tolerance*100:.1f}% tolerance")
        return 1
    if abs(avg - TARGET_RTP) > args.tolerance / 2:
        print(f"FAIL: average RTP outside ±{args.tolerance*50:.1f}% tolerance")
        return 1
    print("PASS: RTP is calibrated around target.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
