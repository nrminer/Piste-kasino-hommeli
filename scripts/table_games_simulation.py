#!/usr/bin/env python3
"""Monte-Carlo sanity report for non-slot table games.

This is not a full certified math model; it validates that the upgraded rules
produce plausible casino odds and no obviously broken payout behavior.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    _RV,
    _baccarat_deal_result,
    _is_blackjack,
    _settle_blackjack_round,
    new_deck,
)


BET = 100


def sim_baccarat(spins: int):
    returns = {"player": 0, "banker": 0, "tie": 0}
    for _ in range(spins):
        result = _baccarat_deal_result(new_deck())
        winner = result["winner"]
        for side in returns:
            if side == winner:
                if side == "player":
                    returns[side] += BET * 2
                elif side == "banker":
                    returns[side] += BET + int(BET * 0.95)
                else:
                    returns[side] += BET * 9
            elif winner == "tie" and side in ("player", "banker"):
                returns[side] += BET
    return {side: returns[side] / (BET * spins) for side in returns}


def sim_war(spins: int):
    returned = 0
    wagered = 0
    for _ in range(spins):
        deck = new_deck()
        pc, dc = deck.pop(), deck.pop()
        wagered += BET
        pv, dv = _RV[pc["rank"]], _RV[dc["rank"]]
        if pv > dv:
            returned += BET * 2
        elif pv == dv:
            wagered += BET
            for _burn in range(6):
                deck.pop()
            pc2, dc2 = deck.pop(), deck.pop()
            pv2, dv2 = _RV[pc2["rank"]], _RV[dc2["rank"]]
            if pv2 > dv2:
                returned += BET * 3
            elif pv2 == dv2:
                returned += BET * 2
    return returned / wagered


def sim_coinflip(spins: int):
    returned = 0
    for _ in range(spins):
        result = "heads" if random.random() < 0.5 else "tails"
        if result == "heads" and random.random() < 0.96:
            returned += BET * 2
    return returned / (BET * spins)


def sim_blackjack_flat_stand_17(spins: int):
    """Simple flat strategy: stand on 17+, hit below. Used only as a sanity trend."""
    returned = 0
    for _ in range(spins):
        deck = new_deck()
        pcards = [deck.pop(), deck.pop()]
        dcards = [deck.pop(), deck.pop()]
        if _is_blackjack(pcards) and _is_blackjack(dcards):
            returned += BET
            continue
        if _is_blackjack(pcards):
            returned += BET + int(BET * 1.5)
            continue
        if _is_blackjack(dcards):
            continue
        while sum_card_total(pcards) < 17:
            pcards.append(deck.pop())
            if sum_card_total(pcards) > 21:
                break
        if sum_card_total(pcards) <= 21:
            _status, _outcome, payout = _settle_blackjack_round(deck, pcards, dcards, BET)
            returned += payout
    return returned / (BET * spins)


def sum_card_total(cards):
    from app import _hand_total  # imported here to keep script compact
    return _hand_total(cards)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spins", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=20260501)
    args = parser.parse_args()
    random.seed(args.seed)
    b = sim_baccarat(args.spins)
    print("Table game RTP/return sanity report")
    print(f"Coinflip choice return: {sim_coinflip(args.spins)*100:.2f}%")
    print(f"Casino War auto-war return: {sim_war(args.spins)*100:.2f}%")
    print(f"Baccarat Player: {b['player']*100:.2f}% · Banker: {b['banker']*100:.2f}% · Tie: {b['tie']*100:.2f}%")
    print(f"Blackjack simple stand-17 strategy: {sim_blackjack_flat_stand_17(args.spins)*100:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
