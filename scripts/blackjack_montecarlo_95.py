"""
Blackjack Monte Carlo simulator calibrated to 95.0% RTP target.

Approach (per user choice "a"): use clean standard 6-deck S17 BJ 3:2 DAS rules
(baseline ~99.6% RTP under optimal basic strategy) then apply a "house rake on
net winnings" multiplier to land on 95.0% ± 0.1%.

The rake parameter `house_rake_on_winnings` is calibrated by running the sim
multiple times and tuning until observed RTP hits the 95.0% target.

Run:
    python /app/scripts/blackjack_montecarlo_95.py            # 100k hands quick
    python /app/scripts/blackjack_montecarlo_95.py --hands 10000000  # full 10M run
"""
import argparse, random, time, json, sys, os
from collections import Counter

# ─── Card model ──────────────────────────────────────────────────────────────
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
SUITS = ['♠', '♥', '♦', '♣']
RANK_VALUE = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
              '10': 10, 'J': 10, 'Q': 10, 'K': 10, 'A': 11}


def build_shoe(num_decks=6):
    shoe = [(r, s) for r in RANKS for s in SUITS] * num_decks
    random.shuffle(shoe)
    return shoe


def hand_total(cards):
    """Best total ≤ 21, treating Aces as 11 or 1."""
    total = sum(RANK_VALUE[c[0]] for c in cards)
    aces = sum(1 for c in cards if c[0] == 'A')
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def is_soft(cards):
    total = sum(RANK_VALUE[c[0]] for c in cards)
    aces = sum(1 for c in cards if c[0] == 'A')
    soft = False
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    if aces > 0 and total <= 21:
        soft = True
    return soft


def is_blackjack(cards):
    return len(cards) == 2 and hand_total(cards) == 21


def is_pair(cards):
    return len(cards) == 2 and cards[0][0] == cards[1][0]


# ─── Basic strategy (6-deck S17, 3:2 BJ, DAS allowed) ─────────────────────────
# Decision matrix returns one of: 'H' hit, 'S' stand, 'D' double (else hit),
# 'P' split, 'Y' = D if allowed else H. Dealer up: 2..10 + 'A'(11).
def _du_index(dealer_up):
    if dealer_up == 11: return 9   # Ace as 11
    return dealer_up - 2  # 2->0, 10->8


# Hard totals (player_total in 5..21, dealer_up in 2..10 + 11)
HARD_STRATEGY = {
    5:  list("HHHHHHHHHH"), 6: list("HHHHHHHHHH"), 7: list("HHHHHHHHHH"),
    8:  list("HHHHHHHHHH"), 9: list("HDDDDHHHHH"),
    10: list("DDDDDDDDHH"), 11: list("DDDDDDDDDH"),
    12: list("HHSSSHHHHH"), 13: list("SSSSSHHHHH"), 14: list("SSSSSHHHHH"),
    15: list("SSSSSHHHHH"), 16: list("SSSSSHHHHH"),
    17: list("SSSSSSSSSS"), 18: list("SSSSSSSSSS"), 19: list("SSSSSSSSSS"),
    20: list("SSSSSSSSSS"), 21: list("SSSSSSSSSS"),
}

# Soft totals (player has Ace counted as 11; total = 13..21)
SOFT_STRATEGY = {
    13: list("HHHDDHHHHH"), 14: list("HHHDDHHHHH"),
    15: list("HHDDDHHHHH"), 16: list("HHDDDHHHHH"),
    17: list("HDDDDHHHHH"), 18: list("SDDDDSSHHH"),
    19: list("SSSSSSSSSS"), 20: list("SSSSSSSSSS"), 21: list("SSSSSSSSSS"),
}

PAIR_STRATEGY = {
    '2':  list("PPPPPPHHHH"), '3': list("PPPPPPHHHH"),
    '4':  list("HHHPPHHHHH"), '5': list("DDDDDDDDHH"),
    '6':  list("PPPPPHHHHH"), '7': list("PPPPPPHHHH"),
    '8':  list("PPPPPPPPPP"), '9': list("PPPPPSPPSS"),
    '10': list("SSSSSSSSSS"),  'A': list("PPPPPPPPPP"),
}


def basic_strategy_action(player_cards, dealer_up_value, can_double, can_split):
    if is_pair(player_cards) and can_split:
        rank = player_cards[0][0]
        # Treat J, Q, K as 10 for pair strategy
        if rank in ('J', 'Q', 'K'):
            rank = '10'
        action = PAIR_STRATEGY[rank][_du_index(dealer_up_value)]
        if action == 'P':
            return 'split'
    total = hand_total(player_cards)
    if is_soft(player_cards) and total >= 13 and total <= 21:
        action = SOFT_STRATEGY[total][_du_index(dealer_up_value)]
    else:
        action = HARD_STRATEGY[max(5, min(21, total))][_du_index(dealer_up_value)]
    if action == 'D':
        return 'double' if can_double else 'hit'
    if action == 'S':
        return 'stand'
    return 'hit'


# ─── Game engine ──────────────────────────────────────────────────────────────
def _safe_pop(shoe):
    """Pop a card; refill shoe if empty."""
    if not shoe:
        shoe.extend(build_shoe(6))
    return list.pop(shoe)


def play_hand(shoe, bet, *, s17=True, max_splits=3, allow_das=True, allow_rsa=False):
    """
    Play one round of blackjack and return:
      net_payout_units (float, can be negative; e.g. -1.0 = lost 1× bet, +1.5 = BJ won)
      blackjack_event ('player'|'dealer'|'both'|None)
      cards_used (count for shoe management)
    """
    if len(shoe) < 30:
        # Reshuffle
        shoe.clear()
        shoe.extend(build_shoe(6))
    player = [_safe_pop(shoe), _safe_pop(shoe)]
    dealer = [_safe_pop(shoe), _safe_pop(shoe)]
    dealer_up = RANK_VALUE[dealer[0][0]]

    # Naturals
    p_bj = is_blackjack(player)
    d_bj = is_blackjack(dealer)
    if p_bj and d_bj:
        return 0.0, 'both', 4
    if p_bj:
        return 1.5, 'player', 4
    if d_bj:
        return -1.0, 'dealer', 4

    cards_used = 4
    # Player can split — track multiple hands
    hands = [{'cards': player, 'bet': bet, 'doubled': False, 'split_from_aces': False}]
    splits_done = 0

    def _resolve_player_hand(h):
        nonlocal cards_used, splits_done
        # Split from aces: only 1 card already drawn, no further actions
        if h['split_from_aces']:
            return
        while True:
            if hand_total(h['cards']) >= 21:
                return
            can_double = (len(h['cards']) == 2 and (allow_das or splits_done == 0))
            can_split_now = (len(h['cards']) == 2 and is_pair(h['cards']) and splits_done < max_splits
                             and not (h['cards'][0][0] == 'A' and splits_done >= 1 and not allow_rsa))
            action = basic_strategy_action(h['cards'], dealer_up, can_double, can_split_now)
            if action == 'split' and can_split_now:
                # Split: create new hand from second card; deduct another bet
                rank = h['cards'][0][0]
                new_card_left = _safe_pop(shoe)
                new_card_right = _safe_pop(shoe)
                cards_used += 2
                old_left, old_right = h['cards']
                h['cards'] = [old_left, new_card_left]
                new_hand = {'cards': [old_right, new_card_right], 'bet': bet, 'doubled': False,
                            'split_from_aces': (rank == 'A')}
                if rank == 'A':
                    h['split_from_aces'] = True
                hands.append(new_hand)
                splits_done += 1
                if h['split_from_aces']:
                    return
                continue
            if action == 'double':
                h['cards'].append(_safe_pop(shoe))
                cards_used += 1
                h['bet'] *= 2
                h['doubled'] = True
                return
            if action == 'stand':
                return
            if action == 'hit':
                h['cards'].append(_safe_pop(shoe))
                cards_used += 1

    i = 0
    while i < len(hands):
        _resolve_player_hand(hands[i])
        i += 1

    # Dealer plays
    while True:
        total = hand_total(dealer)
        if total > 21:
            break
        if total >= 18:
            break
        if total == 17:
            if s17 or not is_soft(dealer):
                break
        dealer.append(_safe_pop(shoe))
        cards_used += 1

    dealer_total = hand_total(dealer)
    dealer_busted = dealer_total > 21

    net = 0.0
    for h in hands:
        pt = hand_total(h['cards'])
        if pt > 21:
            net -= h['bet']
            continue
        if dealer_busted:
            net += h['bet']
            continue
        if pt > dealer_total:
            net += h['bet']
        elif pt < dealer_total:
            net -= h['bet']
        # else push — no change
    return net, None, cards_used


def run_simulation(num_hands, seed, bet=1.0, house_rake_on_winnings=0.0):
    """
    Run num_hands of blackjack with optional house rake on net winnings.
    Returns dict with observed RTP and statistics.
    """
    random.seed(seed)
    shoe = build_shoe(6)
    total_staked = 0.0
    total_returned = 0.0
    win_count = 0
    loss_count = 0
    push_count = 0
    bj_player = 0
    bj_dealer = 0
    bj_both = 0
    return_squared = 0.0
    t0 = time.time()
    for i in range(num_hands):
        hand_bet = bet
        net, bj_event, _ = play_hand(shoe, hand_bet)
        if net > 0 and house_rake_on_winnings > 0:
            net = net * (1 - house_rake_on_winnings)
        total_staked += hand_bet
        total_returned += hand_bet + net
        return_squared += (hand_bet + net) ** 2
        if net > 0:
            win_count += 1
        elif net < 0:
            loss_count += 1
        else:
            push_count += 1
        if bj_event == 'player':
            bj_player += 1
        elif bj_event == 'dealer':
            bj_dealer += 1
        elif bj_event == 'both':
            bj_both += 1
        if (i + 1) % 100000 == 0 and num_hands > 100000:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            print(f"  ...{i+1:,} / {num_hands:,} hands ({rate:,.0f} hands/sec)", flush=True)
    elapsed = time.time() - t0
    rtp = total_returned / total_staked
    mean_return = total_returned / num_hands
    var_return = (return_squared / num_hands) - mean_return ** 2
    return {
        'num_hands': num_hands,
        'observed_rtp': rtp,
        'observed_rtp_pct': round(rtp * 100, 4),
        'win_pct': round(win_count / num_hands * 100, 3),
        'loss_pct': round(loss_count / num_hands * 100, 3),
        'push_pct': round(push_count / num_hands * 100, 3),
        'natural_player_rate_pct': round(bj_player / num_hands * 100, 3),
        'natural_dealer_rate_pct': round(bj_dealer / num_hands * 100, 3),
        'variance_per_bet': round(var_return / (bet ** 2), 4),
        'std_dev_per_bet': round((var_return ** 0.5) / bet, 4),
        'house_rake_on_winnings': house_rake_on_winnings,
        'elapsed_sec': round(elapsed, 2),
        'hands_per_sec': round(num_hands / elapsed, 0) if elapsed > 0 else 0,
    }


def calibrate_to_target(target_rtp, num_hands_per_iter, max_iters=8, seed=0xA17EBABE):
    """Binary search the rake parameter to land on target_rtp."""
    print(f"\n=== Calibrating house_rake_on_winnings to target RTP {target_rtp:.4f} ===")
    lo, hi = 0.0, 0.20
    best = None
    for it in range(max_iters):
        mid = (lo + hi) / 2
        result = run_simulation(num_hands_per_iter, seed=seed + it, house_rake_on_winnings=mid)
        rtp = result['observed_rtp']
        diff = rtp - target_rtp
        print(f"  iter {it}: rake={mid:.5f} → RTP={rtp:.5f} (diff={diff:+.5f})")
        if best is None or abs(diff) < abs(best[1] - target_rtp):
            best = (mid, rtp)
        if abs(diff) < 0.0008:
            print(f"  → converged at rake={mid:.5f} (RTP={rtp:.5f})")
            return mid
        if rtp > target_rtp:
            lo = mid
        else:
            hi = mid
    print(f"  → best found: rake={best[0]:.5f} (RTP={best[1]:.5f})")
    return best[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hands", type=int, default=100000, help="Hands per validation run (default 100k)")
    parser.add_argument("--seed", type=int, default=0xA17EBABE)
    parser.add_argument("--target-rtp", type=float, default=0.95)
    parser.add_argument("--rake", type=float, default=None,
                        help="Skip calibration; use given rake")
    parser.add_argument("--calibrate-hands", type=int, default=200000,
                        help="Hands per calibration iteration")
    parser.add_argument("--report", type=str, default="/app/scripts/bj_montecarlo_report.json")
    args = parser.parse_args()

    print("Blackjack Monte Carlo simulator")
    print(f"  Target RTP: {args.target_rtp:.4f}")
    print(f"  Validation hands: {args.hands:,}")
    print(f"  Seed: 0x{args.seed:X}")

    if args.rake is not None:
        rake = args.rake
        print(f"  Using user-supplied rake={rake}")
    else:
        rake = calibrate_to_target(args.target_rtp, args.calibrate_hands, seed=args.seed)

    print(f"\n=== Final validation run with rake={rake:.5f} ===")
    final = run_simulation(args.hands, seed=args.seed + 100, house_rake_on_winnings=rake)
    final['target_rtp_pct'] = round(args.target_rtp * 100, 4)
    final['target_tolerance_pct'] = 0.10
    final['within_tolerance'] = abs(final['observed_rtp_pct'] - final['target_rtp_pct']) <= 0.10

    print("\n=== Final results ===")
    for k, v in final.items():
        print(f"  {k}: {v}")

    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, 'w', encoding='utf-8') as f:
        json.dump(final, f, indent=2, ensure_ascii=False)
    print(f"\nReport written to {args.report}")
    return 0 if final['within_tolerance'] else 1


if __name__ == "__main__":
    sys.exit(main())
