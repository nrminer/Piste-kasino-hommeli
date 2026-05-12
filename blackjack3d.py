"""
3D Blackjack module — integrates with the existing Flask + SQLite "points"
wallet defined in /app/app.py. This file is imported at the bottom of app.py;
it registers a Flask Blueprint that exposes /api/blackjack3d/* endpoints and
a /blackjack3d HTML page.

Design notes:
  * Re-uses the existing points wallet (`_atomic_deduct_points`, `_add_points`,
    the `players.points` column, and `point_transactions` audit log).
  * Persists round state in a NEW `blackjack3d_games` table so the legacy
    `blackjack_games` table (and the 2D game routes) keep working unchanged.
  * Provably-fair RNG: commit(server_seed_hash) -> play -> reveal(server_seed).
    Shoe is rebuilt deterministically from (server_seed, client_seed, nonce)
    via an HMAC-SHA256 byte stream + Fisher-Yates. Players can rebuild the
    shoe client-side after reveal and confirm every card.
  * Supports multi-hand splits (max 4), insurance, surrender, double down,
    and three rule presets (Vegas Strip S17, Vegas Classic H17, European NHC).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from flask import Blueprint, jsonify, render_template, request

bp = Blueprint("blackjack3d", __name__)

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

# ─── Rule presets ───────────────────────────────────────────────────────────
PRESETS: dict[str, dict[str, Any]] = {
    "vegas_strip": {
        "id": "vegas_strip",
        "name": "Vegas Strip (S17)",
        "decks": 6,
        "dealer_hits_soft_17": False,
        "blackjack_pays": "3:2",
        "double_after_split": True,
        "max_splits": 4,
        "split_aces_one_card": True,
        "surrender": "late",
        "insurance": True,
        "dealer_peek": True,
        "house_edge_hint": "≈0.4%",
    },
    "vegas_classic": {
        "id": "vegas_classic",
        "name": "Vegas Classic (H17)",
        "decks": 6,
        "dealer_hits_soft_17": True,
        "blackjack_pays": "3:2",
        "double_after_split": True,
        "max_splits": 4,
        "split_aces_one_card": True,
        "surrender": "late",
        "insurance": True,
        "dealer_peek": True,
        "house_edge_hint": "≈0.6%",
    },
    "european_nhc": {
        "id": "european_nhc",
        "name": "European (No Hole)",
        "decks": 6,
        "dealer_hits_soft_17": False,
        "blackjack_pays": "3:2",
        "double_after_split": False,
        "max_splits": 3,
        "split_aces_one_card": True,
        "surrender": "none",
        "insurance": True,
        "dealer_peek": False,
        "house_edge_hint": "≈0.6%",
    },
}

MIN_BET = 10
MAX_BET = 10000

# ─── Migrations (idempotent, called once on import) ─────────────────────────
def _migrate(db_path: str) -> None:
    import sqlite3
    db = sqlite3.connect(db_path)
    try:
        db.execute(
            """CREATE TABLE IF NOT EXISTS blackjack3d_games (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id         INTEGER NOT NULL,
                preset            TEXT    NOT NULL,
                bet               INTEGER NOT NULL,
                server_seed       TEXT    NOT NULL,
                server_seed_hash  TEXT    NOT NULL,
                client_seed       TEXT    NOT NULL,
                nonce             INTEGER NOT NULL,
                shoe_json         TEXT    NOT NULL,
                shoe_index        INTEGER DEFAULT 0,
                hands_json        TEXT    NOT NULL,
                dealer_json       TEXT    NOT NULL,
                active_hand       INTEGER DEFAULT 0,
                status            TEXT    DEFAULT 'active',
                insurance_bet     INTEGER DEFAULT 0,
                insurance_result  TEXT    DEFAULT '',
                revealed          INTEGER DEFAULT 0,
                outcome_json      TEXT    DEFAULT '',
                created_at        TEXT    DEFAULT CURRENT_TIMESTAMP,
                ended_at          TEXT,
                FOREIGN KEY (player_id) REFERENCES players(id)
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bj3d_player ON blackjack3d_games(player_id, status, created_at)"
        )
        db.commit()
        db.close()
    except Exception:
        try:
            db.close()
        except Exception:
            pass


# ─── Provably-fair RNG ──────────────────────────────────────────────────────
def gen_server_seed() -> str:
    return secrets.token_hex(32)  # 64 hex chars (256 bits)


def gen_client_seed() -> str:
    return secrets.token_hex(8)  # 16 hex chars (64 bits)


def hash_seed(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _byte_stream(server_seed: str, client_seed: str, nonce: int):
    """Yield deterministic bytes via HMAC-SHA256 keyed by server_seed."""
    counter = 0
    while True:
        msg = f"{client_seed}:{nonce}:{counter}".encode("utf-8")
        block = hmac.new(server_seed.encode("utf-8"), msg, hashlib.sha256).digest()
        for b in block:
            yield b
        counter += 1


def build_shoe(server_seed: str, client_seed: str, nonce: int, decks: int) -> list[dict]:
    """Build a `decks`-deck shoe and Fisher-Yates shuffle it deterministically."""
    cards: list[dict] = []
    for _ in range(decks):
        for s in SUITS:
            for r in RANKS:
                cards.append({"r": r, "s": s})
    stream = _byte_stream(server_seed, client_seed, nonce)
    # Fisher-Yates using 4-byte uint32 draws (rejection-sampled to be unbiased).
    for i in range(len(cards) - 1, 0, -1):
        bound = i + 1
        # Largest multiple of `bound` that fits in uint32; reject above to debias.
        limit = (0xFFFFFFFF // bound) * bound
        while True:
            val = (next(stream) << 24) | (next(stream) << 16) | (next(stream) << 8) | next(stream)
            if val < limit:
                break
        j = val % bound
        cards[i], cards[j] = cards[j], cards[i]
    return cards


# ─── Hand evaluation ────────────────────────────────────────────────────────
def card_value(rank: str) -> int:
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_total(cards: list[dict]) -> tuple[int, bool]:
    """Return (best_total<=21 if possible, is_soft)."""
    total = sum(card_value(c["r"]) for c in cards)
    aces = sum(1 for c in cards if c["r"] == "A")
    soft = aces > 0 and total <= 21
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
        soft = aces > 0 and total <= 21
    return total, soft


def is_blackjack(cards: list[dict]) -> bool:
    if len(cards) != 2:
        return False
    total, _ = hand_total(cards)
    return total == 21


def is_pair(cards: list[dict]) -> bool:
    return len(cards) == 2 and cards[0]["r"] == cards[1]["r"]


# ─── Basic strategy (compact table; multi-deck S17 reference) ───────────────
# Actions: H=hit, S=stand, D=double (else hit), Ds=double (else stand),
#          P=split, R=surrender (else hit), Rs=surrender (else stand).
_HARD = {
    # player_total -> dealer up-card -> action  (dealer up: 2..A as "2".."10","A")
    5:  {d: "H" for d in "23456789TA"},
    6:  {d: "H" for d in "23456789TA"},
    7:  {d: "H" for d in "23456789TA"},
    8:  {d: "H" for d in "23456789TA"},
    9:  {**{d: "H" for d in "23456789TA"}, "3":"D","4":"D","5":"D","6":"D"},
    10: {**{d: "D" for d in "23456789"}, "T":"H","A":"H"},
    11: {d: "D" for d in "23456789TA"},
    12: {**{d: "H" for d in "23456789TA"}, "4":"S","5":"S","6":"S"},
    13: {**{d: "S" for d in "23456"},     "7":"H","8":"H","9":"H","T":"H","A":"H"},
    14: {**{d: "S" for d in "23456"},     "7":"H","8":"H","9":"H","T":"H","A":"H"},
    15: {**{d: "S" for d in "23456"},     "7":"H","8":"H","9":"H","T":"R","A":"H"},
    16: {**{d: "S" for d in "23456"},     "7":"H","8":"H","9":"R","T":"R","A":"R"},
    17: {d: "S" for d in "23456789TA"},
    18: {d: "S" for d in "23456789TA"},
    19: {d: "S" for d in "23456789TA"},
    20: {d: "S" for d in "23456789TA"},
    21: {d: "S" for d in "23456789TA"},
}
_SOFT = {
    # soft total (A counted as 11)
    13: {**{d:"H" for d in "23456789TA"}, "5":"D","6":"D"},
    14: {**{d:"H" for d in "23456789TA"}, "5":"D","6":"D"},
    15: {**{d:"H" for d in "23456789TA"}, "4":"D","5":"D","6":"D"},
    16: {**{d:"H" for d in "23456789TA"}, "4":"D","5":"D","6":"D"},
    17: {**{d:"H" for d in "23456789TA"}, "3":"D","4":"D","5":"D","6":"D"},
    18: {**{d:"S" for d in "23456789TA"}, "2":"S","3":"Ds","4":"Ds","5":"Ds","6":"Ds","9":"H","T":"H","A":"H"},
    19: {d:"S" for d in "23456789TA"},
    20: {d:"S" for d in "23456789TA"},
    21: {d:"S" for d in "23456789TA"},
}
_PAIRS = {
    "2": {**{d:"H" for d in "23456789TA"}, "2":"P","3":"P","4":"P","5":"P","6":"P","7":"P"},
    "3": {**{d:"H" for d in "23456789TA"}, "2":"P","3":"P","4":"P","5":"P","6":"P","7":"P"},
    "4": {**{d:"H" for d in "23456789TA"}, "5":"P","6":"P"},
    "5": {**{d:"D" for d in "23456789"}, "T":"H","A":"H"},
    "6": {**{d:"H" for d in "23456789TA"}, "2":"P","3":"P","4":"P","5":"P","6":"P"},
    "7": {**{d:"H" for d in "23456789TA"}, "2":"P","3":"P","4":"P","5":"P","6":"P","7":"P"},
    "8": {d:"P" for d in "23456789TA"},
    "9": {**{d:"S" for d in "23456789TA"}, "2":"P","3":"P","4":"P","5":"P","6":"P","8":"P","9":"P"},
    "T": {d:"S" for d in "23456789TA"},
    "A": {d:"P" for d in "23456789TA"},
}

def _dealer_key(c: dict) -> str:
    r = c["r"]
    if r in ("J", "Q", "K", "10"):
        return "T"
    return r

def basic_strategy(hand: list[dict], dealer_up: dict, can_double: bool, can_split: bool, can_surrender: bool) -> str:
    dk = _dealer_key(dealer_up)
    # Pair handling
    if can_split and is_pair(hand):
        rank = hand[0]["r"]
        key = "T" if rank in ("J","Q","K","10") else rank
        rec = _PAIRS.get(key, {}).get(dk, "H")
        if rec == "P":
            return "split"
    total, soft = hand_total(hand)
    table = _SOFT if soft else _HARD
    rec = table.get(total, {}).get(dk, "H")
    # Resolve combined actions
    if rec == "D":
        return "double" if (can_double and len(hand) == 2) else "hit"
    if rec == "Ds":
        return "double" if (can_double and len(hand) == 2) else "stand"
    if rec == "R":
        return "surrender" if (can_surrender and len(hand) == 2) else "hit"
    if rec == "Rs":
        return "surrender" if (can_surrender and len(hand) == 2) else "stand"
    if rec == "H":
        return "hit"
    if rec == "S":
        return "stand"
    return "stand"


# ─── Round serialization / view models ──────────────────────────────────────
def _public_dealer(dealer: list[dict], reveal_all: bool) -> list[dict | None]:
    if reveal_all or not dealer:
        return dealer
    # First card visible, rest hidden as None placeholders to keep length.
    return [dealer[0]] + [None] * (len(dealer) - 1)


def _round_view(game: dict, reveal: bool = False) -> dict:
    hands = json.loads(game["hands_json"])
    dealer = json.loads(game["dealer_json"])
    preset = PRESETS[game["preset"]]
    active_idx = game["active_hand"]
    is_active = game["status"] == "active"

    # Render hands with current totals
    hand_views = []
    for i, h in enumerate(hands):
        total, soft = hand_total(h["cards"])
        hv = {
            "index": i,
            "cards": h["cards"],
            "bet": h["bet"],
            "status": h["status"],   # 'playing' | 'stand' | 'bust' | 'doubled' | 'surrendered' | 'blackjack'
            "doubled": h.get("doubled", False),
            "is_split_aces": h.get("is_split_aces", False),
            "total": total,
            "soft": soft,
            "is_active": is_active and i == active_idx,
            "is_blackjack": is_blackjack(h["cards"]),
        }
        if h["status"] != "playing":
            hv["payout"] = h.get("payout", 0)
            hv["outcome"] = h.get("outcome", "")
        hand_views.append(hv)

    dealer_visible = _public_dealer(dealer, reveal_all=not is_active or reveal)
    d_total = None
    d_soft = None
    if not is_active or reveal:
        d_total, d_soft = hand_total(dealer)
    else:
        # Show only visible total
        d_total, d_soft = hand_total([dealer[0]])

    # Legal actions for the active hand
    legal: list[str] = []
    ins_available = False
    if is_active and 0 <= active_idx < len(hand_views):
        ah = hands[active_idx]
        ah_cards = ah["cards"]
        ah_split_aces = ah.get("is_split_aces", False)
        legal.append("stand")
        if not (ah_split_aces and preset["split_aces_one_card"]):
            legal.append("hit")
        # Double
        if len(ah_cards) == 2 and not ah.get("doubled", False):
            if not ah_split_aces or preset.get("double_after_split"):
                # No doubling after a split unless preset allows.
                if active_idx == 0 or preset.get("double_after_split"):
                    legal.append("double")
        # Split
        splits_used = sum(1 for h in hands if h.get("from_split"))
        if (
            len(ah_cards) == 2
            and is_pair(ah_cards)
            and splits_used + 1 < preset["max_splits"]
        ):
            legal.append("split")
        # Surrender — only first action of first hand
        if (
            preset["surrender"] != "none"
            and len(hands) == 1
            and len(ah_cards) == 2
            and active_idx == 0
        ):
            legal.append("surrender")
        # Insurance — first action, dealer shows Ace, only once
        if (
            len(hands) == 1
            and len(ah_cards) == 2
            and dealer
            and dealer[0]["r"] == "A"
            and game["insurance_bet"] == 0
            and preset["insurance"]
        ):
            ins_available = True

    return {
        "game_id": game["id"],
        "preset": game["preset"],
        "preset_meta": preset,
        "bet": game["bet"],
        "status": game["status"],
        "hands": hand_views,
        "active_hand": active_idx,
        "dealer": dealer_visible,
        "dealer_total": d_total,
        "dealer_soft": d_soft,
        "insurance_bet": game["insurance_bet"],
        "insurance_result": game["insurance_result"],
        "insurance_available": ins_available,
        "legal_actions": legal,
        "server_seed_hash": game["server_seed_hash"],
        "client_seed": game["client_seed"],
        "nonce": game["nonce"],
        "revealed": bool(game["revealed"]),
        "shoe_index": game["shoe_index"],
        "outcome": json.loads(game["outcome_json"]) if game["outcome_json"] else None,
    }


# ─── Game engine ────────────────────────────────────────────────────────────
def _draw_card(shoe: list[dict], idx: int) -> tuple[dict, int]:
    return shoe[idx], idx + 1


def _new_hand(cards: list[dict], bet: int, from_split: bool = False, is_split_aces: bool = False) -> dict:
    return {
        "cards": cards,
        "bet": bet,
        "status": "playing",
        "doubled": False,
        "from_split": from_split,
        "is_split_aces": is_split_aces,
    }


def _settle_dealer_and_hands(game: dict, preset: dict) -> tuple[list[dict], list[dict], int, int]:
    """Play out dealer per preset rules and resolve every non-terminal hand.
    Returns (updated_hands, updated_dealer, updated_shoe_idx, total_player_payout).
    """
    shoe = json.loads(game["shoe_json"])
    idx = game["shoe_index"]
    hands = json.loads(game["hands_json"])
    dealer = json.loads(game["dealer_json"])

    # If every hand is bust/surrendered, dealer doesn't play (European NHC has no peek;
    # for simplicity we still draw one face-down on initial deal but skip resolution).
    any_alive = any(h["status"] in ("stand", "doubled") for h in hands)
    if any_alive:
        # Dealer reveals second card (already in `dealer`), then hits per rules.
        while True:
            total, soft = hand_total(dealer)
            if total < 17:
                c, idx = _draw_card(shoe, idx)
                dealer.append(c)
                continue
            if total == 17 and soft and preset["dealer_hits_soft_17"]:
                c, idx = _draw_card(shoe, idx)
                dealer.append(c)
                continue
            break

    dealer_total, _ = hand_total(dealer)
    dealer_bust = dealer_total > 21
    payout_total = 0

    for h in hands:
        if h["status"] == "bust":
            h["payout"] = 0
            h["outcome"] = "lose"
            continue
        if h["status"] == "surrendered":
            # Half the bet was already deducted/returned in handler; record outcome.
            h["payout"] = h["bet"] // 2  # already returned, but record for display
            h["outcome"] = "surrender"
            payout_total += h["payout"]
            continue
        if h["status"] == "blackjack":
            # 3:2 BJ pays bet + 1.5*bet
            pay = h["bet"] + (h["bet"] * 3 // 2)
            h["payout"] = pay
            h["outcome"] = "blackjack"
            payout_total += pay
            continue

        # Active stand / doubled
        p_total, _ = hand_total(h["cards"])
        if dealer_bust or p_total > dealer_total:
            pay = h["bet"] * 2
            h["payout"] = pay
            h["outcome"] = "win"
            payout_total += pay
        elif p_total == dealer_total:
            pay = h["bet"]  # push
            h["payout"] = pay
            h["outcome"] = "push"
            payout_total += pay
        else:
            h["payout"] = 0
            h["outcome"] = "lose"

    return hands, dealer, idx, payout_total


# ─── Flask routes ───────────────────────────────────────────────────────────
@bp.route("/blackjack3d")
def blackjack3d_page():
    return render_template("blackjack3d.html")


@bp.route("/api/blackjack3d/presets", methods=["GET"])
def list_presets():
    return jsonify({"presets": list(PRESETS.values())})


@bp.route("/api/blackjack3d/round/start", methods=["POST"])
def round_start():
    # Lazy import to avoid circular import at module load.
    from app import (
        get_db,
        _atomic_deduct_points,
        _add_points,
    )

    d = request.get_json(silent=True) or {}
    try:
        player_id = int(d.get("player_id") or 0)
        bet = int(d.get("bet") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Virheelliset parametrit."}), 400
    preset_id = (d.get("preset") or "vegas_strip").strip()
    if preset_id not in PRESETS:
        return jsonify({"error": "Tuntematon sääntöasetus."}), 400
    if bet < MIN_BET or bet > MAX_BET:
        return jsonify({"error": f"Panoksen oltava {MIN_BET}–{MAX_BET}."}), 400
    if player_id <= 0:
        return jsonify({"error": "Kirjaudu sisään pelataksesi."}), 401

    preset = PRESETS[preset_id]

    db = get_db()
    row = db.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
    if not row:
        return jsonify({"error": "Pelaajaa ei löydy."}), 404
    if (row["points"] or 0) < bet:
        return jsonify({"error": "Ei tarpeeksi pisteitä."}), 400

    # Abandon any orphaned active 3D rounds for this player.
    db.execute(
        "UPDATE blackjack3d_games SET status='abandoned' WHERE player_id=? AND status='active'",
        (player_id,),
    )
    db.commit()

    new_bal = _atomic_deduct_points(db, player_id, bet, "Blackjack 3D panos")
    if new_bal is None:
        return jsonify({"error": "Ei tarpeeksi pisteitä."}), 400

    # Build provably-fair shoe.
    server_seed = gen_server_seed()
    client_seed = (d.get("client_seed") or "").strip() or gen_client_seed()
    nonce = 1
    shoe = build_shoe(server_seed, client_seed, nonce, preset["decks"])

    # Initial deal: P, D, P, D (dealer's 2nd card is "hole" for non-NHC presets).
    idx = 0
    p1, idx = _draw_card(shoe, idx)
    d1, idx = _draw_card(shoe, idx)
    p2, idx = _draw_card(shoe, idx)
    d2, idx = _draw_card(shoe, idx)
    player_cards = [p1, p2]
    dealer_cards = [d1, d2]

    hands = [_new_hand(player_cards, bet)]
    status = "active"
    insurance_bet = 0
    insurance_result = ""
    outcome_json = ""

    # Natural blackjack detection — only auto-settle if dealer can't have BJ
    # OR preset uses peek and we resolve immediately.
    player_bj = is_blackjack(player_cards)
    dealer_up_is_ten_or_ace = dealer_cards[0]["r"] in ("A", "10", "J", "Q", "K")
    dealer_bj = is_blackjack(dealer_cards)

    if player_bj:
        if preset["dealer_peek"] and dealer_up_is_ten_or_ace:
            # Resolve immediately.
            if dealer_bj:
                hands[0]["status"] = "stand"  # push will be resolved by settler
                hands[0]["payout"] = bet
                hands[0]["outcome"] = "push"
                status = "settled"
                _add_points(db, player_id, bet, "Blackjack 3D tasapeli (BJ vs BJ)")
            else:
                hands[0]["status"] = "blackjack"
                pay = bet + (bet * 3 // 2)
                hands[0]["payout"] = pay
                hands[0]["outcome"] = "blackjack"
                status = "settled"
                _add_points(db, player_id, pay, "Blackjack 3D luonnollinen 21")
        elif not preset["dealer_peek"] and not dealer_up_is_ten_or_ace:
            # No peek but dealer up-card can't make BJ.
            hands[0]["status"] = "blackjack"
            pay = bet + (bet * 3 // 2)
            hands[0]["payout"] = pay
            hands[0]["outcome"] = "blackjack"
            status = "settled"
            _add_points(db, player_id, pay, "Blackjack 3D luonnollinen 21")
        elif not preset["dealer_peek"]:
            # NHC: keep round active until end (player gets BJ, dealer might tie or win)
            hands[0]["status"] = "stand"  # auto-stand on natural; dealer plays out
        # else: peek with up-card that can't make BJ (handled by first branch's "no" path)
        if status == "active" and preset["dealer_peek"] and not dealer_up_is_ten_or_ace:
            # Player BJ wins immediately (dealer can't have BJ).
            hands[0]["status"] = "blackjack"
            pay = bet + (bet * 3 // 2)
            hands[0]["payout"] = pay
            hands[0]["outcome"] = "blackjack"
            status = "settled"
            _add_points(db, player_id, pay, "Blackjack 3D luonnollinen 21")

    # Persist
    revealed = 1 if status == "settled" else 0
    if status == "settled":
        outcome_json = json.dumps({
            "hands": [
                {"outcome": h.get("outcome", ""), "payout": h.get("payout", 0), "bet": h["bet"]}
                for h in hands
            ],
            "dealer_total": hand_total(dealer_cards)[0],
            "net": sum(h.get("payout", 0) for h in hands) - bet,
        }, ensure_ascii=False)

    cur = db.execute(
        """INSERT INTO blackjack3d_games(
            player_id, preset, bet, server_seed, server_seed_hash, client_seed, nonce,
            shoe_json, shoe_index, hands_json, dealer_json, active_hand, status,
            insurance_bet, insurance_result, revealed, outcome_json, ended_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            player_id, preset_id, bet, server_seed, hash_seed(server_seed),
            client_seed, nonce,
            json.dumps(shoe, ensure_ascii=False), idx,
            json.dumps(hands, ensure_ascii=False),
            json.dumps(dealer_cards, ensure_ascii=False),
            0, status, insurance_bet, insurance_result, revealed,
            outcome_json,
            None if status == "active" else _now_iso(),
        ),
    )
    gid = cur.lastrowid
    db.commit()

    game = db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone()
    view = _round_view(dict(game), reveal=(status != "active"))
    view["balance"] = (db.execute("SELECT points FROM players WHERE id=?", (player_id,)).fetchone() or {"points": 0})["points"]
    if status == "settled":
        view["server_seed"] = server_seed
    return jsonify(view)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _maybe_finalize(db, game_row: dict) -> dict:
    """If every player hand is in a terminal state, play the dealer + settle."""
    hands = json.loads(game_row["hands_json"])
    all_done = all(h["status"] in ("bust", "stand", "doubled", "surrendered", "blackjack") for h in hands)
    if not all_done:
        return game_row

    from app import _add_points

    preset = PRESETS[game_row["preset"]]
    updated_hands, updated_dealer, updated_idx, payout = _settle_dealer_and_hands(game_row, preset)

    # Credit player wallet (single transaction line per non-zero hand).
    for h in updated_hands:
        if h.get("payout", 0) > 0 and h.get("outcome") != "surrender":
            label = {
                "blackjack": "Blackjack 3D luonnollinen 21",
                "win": "Blackjack 3D voitto",
                "push": "Blackjack 3D tasapeli",
            }.get(h.get("outcome", ""), "Blackjack 3D voitto")
            _add_points(db, game_row["player_id"], h["payout"], label)

    outcome_summary = {
        "hands": [
            {"outcome": h.get("outcome", ""), "payout": h.get("payout", 0), "bet": h["bet"]}
            for h in updated_hands
        ],
        "dealer_total": hand_total(updated_dealer)[0],
        "net": sum(h.get("payout", 0) for h in updated_hands) - sum(h["bet"] for h in updated_hands),
    }
    db.execute(
        """UPDATE blackjack3d_games SET hands_json=?, dealer_json=?, shoe_index=?,
           status=?, revealed=?, outcome_json=?, ended_at=? WHERE id=?""",
        (
            json.dumps(updated_hands, ensure_ascii=False),
            json.dumps(updated_dealer, ensure_ascii=False),
            updated_idx,
            "settled",
            1,
            json.dumps(outcome_summary, ensure_ascii=False),
            _now_iso(),
            game_row["id"],
        ),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (game_row["id"],)).fetchone())


def _advance_active_hand_if_needed(db, game_row: dict) -> dict:
    """Advance `active_hand` to the next playing hand. Persists changes."""
    hands = json.loads(game_row["hands_json"])
    active = game_row["active_hand"]
    moved = False
    while active < len(hands) and hands[active]["status"] != "playing":
        active += 1
        moved = True
    if moved:
        db.execute("UPDATE blackjack3d_games SET active_hand=? WHERE id=?", (active, game_row["id"]))
        db.commit()
        game_row = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (game_row["id"],)).fetchone())
    return game_row


@bp.route("/api/blackjack3d/round/<int:gid>/action", methods=["POST"])
def round_action(gid: int):
    from app import get_db, _atomic_deduct_points, _add_points

    d = request.get_json(silent=True) or {}
    action = (d.get("action") or "").strip().lower()

    db = get_db()
    game = db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone()
    if not game:
        return jsonify({"error": "Peliä ei löydy."}), 404
    game = dict(game)
    if game["status"] != "active":
        return jsonify({"error": "Peli on jo päättynyt."}), 400

    preset = PRESETS[game["preset"]]
    hands = json.loads(game["hands_json"])
    dealer = json.loads(game["dealer_json"])
    shoe = json.loads(game["shoe_json"])
    idx = game["shoe_index"]
    pid = game["player_id"]
    active = game["active_hand"]
    if active >= len(hands):
        return jsonify({"error": "Ei aktiivista kättä."}), 400

    cur_hand = hands[active]

    # Insurance handling first (only valid pre-action, single hand, dealer up=A).
    if action == "insurance":
        if (
            len(hands) != 1
            or len(cur_hand["cards"]) != 2
            or dealer[0]["r"] != "A"
            or game["insurance_bet"] != 0
            or not preset["insurance"]
        ):
            return jsonify({"error": "Vakuutus ei ole nyt mahdollinen."}), 400
        ins = max(1, game["bet"] // 2)
        if _atomic_deduct_points(db, pid, ins, "Blackjack 3D vakuutus") is None:
            return jsonify({"error": "Ei tarpeeksi pisteitä vakuutukseen."}), 400
        dealer_bj = is_blackjack(dealer)
        if dealer_bj:
            payout = ins * 3  # 2:1 plus stake = 3x
            _add_points(db, pid, payout, "Blackjack 3D vakuutusvoitto")
            game["insurance_result"] = "win"
            # Resolve hand: BJ vs BJ = push, else lose.
            if is_blackjack(cur_hand["cards"]):
                cur_hand["status"] = "stand"  # settler resolves push
            else:
                cur_hand["status"] = "bust"   # treat as immediate loss
                cur_hand["payout"] = 0
                cur_hand["outcome"] = "lose"
        else:
            game["insurance_result"] = "lose"
        db.execute(
            "UPDATE blackjack3d_games SET insurance_bet=?, insurance_result=?, hands_json=? WHERE id=?",
            (ins, game["insurance_result"], json.dumps(hands, ensure_ascii=False), gid),
        )
        db.commit()
        game = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone())
        if dealer_bj:
            game = _maybe_finalize(db, game)
        view = _round_view(game, reveal=(game["status"] != "active"))
        view["balance"] = (db.execute("SELECT points FROM players WHERE id=?", (pid,)).fetchone() or {"points": 0})["points"]
        if game["status"] != "active":
            view["server_seed"] = game["server_seed"]
        return jsonify(view)

    if action == "decline_insurance":
        # If dealer has BJ, expose it and settle (no peek penalty); else continue.
        if dealer[0]["r"] != "A" or not preset["insurance"]:
            return jsonify({"error": "Ei vakuutustilanteetta."}), 400
        if preset["dealer_peek"] and is_blackjack(dealer):
            # Dealer reveals BJ; player loses unless also BJ.
            if is_blackjack(cur_hand["cards"]):
                cur_hand["status"] = "stand"
            else:
                cur_hand["status"] = "bust"
                cur_hand["payout"] = 0
                cur_hand["outcome"] = "lose"
            db.execute(
                "UPDATE blackjack3d_games SET hands_json=?, insurance_result='lose' WHERE id=?",
                (json.dumps(hands, ensure_ascii=False), gid),
            )
            db.commit()
            game = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone())
            game = _maybe_finalize(db, game)
        else:
            db.execute(
                "UPDATE blackjack3d_games SET insurance_result='lose' WHERE id=?",
                (gid,),
            )
            db.commit()
            game = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone())
        view = _round_view(game, reveal=(game["status"] != "active"))
        view["balance"] = (db.execute("SELECT points FROM players WHERE id=?", (pid,)).fetchone() or {"points": 0})["points"]
        if game["status"] != "active":
            view["server_seed"] = game["server_seed"]
        return jsonify(view)

    if action == "hit":
        if cur_hand.get("is_split_aces") and preset["split_aces_one_card"]:
            return jsonify({"error": "Hajotetut ässät saavat vain yhden kortin."}), 400
        c, idx = _draw_card(shoe, idx)
        cur_hand["cards"].append(c)
        total, _ = hand_total(cur_hand["cards"])
        if total > 21:
            cur_hand["status"] = "bust"
            cur_hand["payout"] = 0
            cur_hand["outcome"] = "lose"
        elif total == 21:
            cur_hand["status"] = "stand"

    elif action == "stand":
        cur_hand["status"] = "stand"

    elif action == "double":
        if len(cur_hand["cards"]) != 2 or cur_hand.get("doubled"):
            return jsonify({"error": "Tuplaus ei ole mahdollinen."}), 400
        if cur_hand.get("from_split") and not preset.get("double_after_split"):
            return jsonify({"error": "Tuplaus splittauksen jälkeen ei sallittu."}), 400
        if _atomic_deduct_points(db, pid, cur_hand["bet"], "Blackjack 3D tuplaus") is None:
            return jsonify({"error": "Ei tarpeeksi pisteitä tuplaukseen."}), 400
        cur_hand["bet"] *= 2
        cur_hand["doubled"] = True
        c, idx = _draw_card(shoe, idx)
        cur_hand["cards"].append(c)
        total, _ = hand_total(cur_hand["cards"])
        if total > 21:
            cur_hand["status"] = "bust"
            cur_hand["payout"] = 0
            cur_hand["outcome"] = "lose"
        else:
            cur_hand["status"] = "doubled"

    elif action == "split":
        if not (len(cur_hand["cards"]) == 2 and is_pair(cur_hand["cards"])):
            return jsonify({"error": "Splittaus ei ole mahdollinen."}), 400
        splits_used = sum(1 for h in hands if h.get("from_split"))
        if splits_used + 1 >= preset["max_splits"]:
            return jsonify({"error": "Maksimimäärä splittauksia käytetty."}), 400
        if _atomic_deduct_points(db, pid, cur_hand["bet"], "Blackjack 3D splittaus") is None:
            return jsonify({"error": "Ei tarpeeksi pisteitä splittaukseen."}), 400
        c1 = cur_hand["cards"][0]
        c2 = cur_hand["cards"][1]
        is_aces = c1["r"] == "A"
        # First new card for each split hand
        new1, idx = _draw_card(shoe, idx)
        new2, idx = _draw_card(shoe, idx)
        old_bet = cur_hand["bet"]
        # Replace current hand and insert a new one immediately after.
        hands[active] = _new_hand([c1, new1], old_bet, from_split=True, is_split_aces=is_aces)
        hands.insert(active + 1, _new_hand([c2, new2], old_bet, from_split=True, is_split_aces=is_aces))
        # Split-aces auto-stand.
        if is_aces and preset["split_aces_one_card"]:
            for h in (hands[active], hands[active + 1]):
                t, _ = hand_total(h["cards"])
                h["status"] = "blackjack" if (t == 21 and len(h["cards"]) == 2 and not h.get("from_split", False)) else "stand"
                # Per rules: split-aces 21 is NOT a natural blackjack — treat as 21 stand.
                if h["status"] == "blackjack":
                    h["status"] = "stand"

    elif action == "surrender":
        if not (
            preset["surrender"] != "none"
            and len(hands) == 1
            and len(cur_hand["cards"]) == 2
            and active == 0
        ):
            return jsonify({"error": "Antautuminen ei ole mahdollinen."}), 400
        half = cur_hand["bet"] // 2
        _add_points(db, pid, half, "Blackjack 3D antautuminen (palautus)")
        cur_hand["status"] = "surrendered"
        cur_hand["payout"] = half
        cur_hand["outcome"] = "surrender"

    else:
        return jsonify({"error": "Virheellinen toiminto."}), 400

    # Persist mutation
    db.execute(
        """UPDATE blackjack3d_games SET hands_json=?, shoe_json=?, shoe_index=? WHERE id=?""",
        (json.dumps(hands, ensure_ascii=False), json.dumps(shoe, ensure_ascii=False), idx, gid),
    )
    db.commit()
    game = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone())
    game = _advance_active_hand_if_needed(db, game)
    game = _maybe_finalize(db, game)

    view = _round_view(game, reveal=(game["status"] != "active"))
    view["balance"] = (db.execute("SELECT points FROM players WHERE id=?", (pid,)).fetchone() or {"points": 0})["points"]
    if game["status"] != "active":
        view["server_seed"] = game["server_seed"]
    return jsonify(view)


@bp.route("/api/blackjack3d/round/<int:gid>", methods=["GET"])
def round_state(gid: int):
    from app import get_db

    db = get_db()
    row = db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({"error": "Peliä ei löydy."}), 404
    game = dict(row)
    view = _round_view(game, reveal=(game["status"] != "active"))
    view["balance"] = (db.execute("SELECT points FROM players WHERE id=?", (game["player_id"],)).fetchone() or {"points": 0})["points"]
    if game["status"] != "active" and game["revealed"]:
        view["server_seed"] = game["server_seed"]
    return jsonify(view)


@bp.route("/api/blackjack3d/round/<int:gid>/reveal", methods=["POST"])
def round_reveal(gid: int):
    from app import get_db

    db = get_db()
    row = db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({"error": "Peliä ei löydy."}), 404
    game = dict(row)
    if game["status"] == "active":
        return jsonify({"error": "Peli on yhä käynnissä."}), 400
    if not game["revealed"]:
        db.execute("UPDATE blackjack3d_games SET revealed=1 WHERE id=?", (gid,))
        db.commit()
    return jsonify({
        "server_seed": game["server_seed"],
        "server_seed_hash": game["server_seed_hash"],
        "client_seed": game["client_seed"],
        "nonce": game["nonce"],
        "preset": game["preset"],
        "decks": PRESETS[game["preset"]]["decks"],
    })


@bp.route("/api/blackjack3d/round/<int:gid>/hint", methods=["GET"])
def round_hint(gid: int):
    from app import get_db

    db = get_db()
    row = db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({"error": "Peliä ei löydy."}), 404
    game = dict(row)
    if game["status"] != "active":
        return jsonify({"hint": None})
    preset = PRESETS[game["preset"]]
    hands = json.loads(game["hands_json"])
    dealer = json.loads(game["dealer_json"])
    active = game["active_hand"]
    if active >= len(hands):
        return jsonify({"hint": None})
    h = hands[active]
    can_double = len(h["cards"]) == 2 and not h.get("doubled")
    can_split = (
        len(h["cards"]) == 2 and is_pair(h["cards"]) and
        sum(1 for hh in hands if hh.get("from_split")) + 1 < preset["max_splits"]
    )
    can_surrender = (
        preset["surrender"] != "none"
        and len(hands) == 1
        and len(h["cards"]) == 2
        and active == 0
    )
    rec = basic_strategy(h["cards"], dealer[0], can_double, can_split, can_surrender)
    return jsonify({"hint": rec})


def register(app, db_path: str) -> None:
    """Wire the blueprint into the host Flask app and run migrations."""
    _migrate(db_path)
    app.register_blueprint(bp)
