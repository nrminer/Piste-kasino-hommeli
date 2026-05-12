"""
Blackjack module (Flask Blueprint).

Design (post-refactor):
  * Game logic is FULLY AUTHORITATIVE on the server. Clients only send
    intents (start, hit, stand, …); the server holds the shoe, deals
    cards, runs dealer logic and settles bets against the existing
    `players.points` wallet from /app/app.py.
  * RNG is plain `random.shuffle` (no HMAC / provably-fair seeds — the
    earlier commit/reveal flow was removed at user request).
  * A persistent 6-deck shoe per player is kept in the new
    `blackjack3d_shoes` table. A cut-card index is set on each shuffle
    (default 75 % of cards used → reshuffle). When a hand crosses the
    cut card, the *current* round resolves with the existing shoe and
    the shoe is reshuffled before the next round.
  * Operator endpoints (under /api/operator/blackjack/*) let an
    authenticated admin reset shoes, view shoe state and tweak rules.
    Auth is provided by /app/operator_auth.py — short-lived JWT keyed
    by OPERATOR_PASSWORD + OPERATOR_TOKEN_SECRET env vars.
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, render_template, request

from operator_auth import op_required


bp = Blueprint("blackjack3d", __name__)

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

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
        "name": "European (No-hole)",
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
        # Round table (keeps legacy PF columns for any pre-existing rows,
        # but new rows just leave them as empty strings).
        db.execute(
            """CREATE TABLE IF NOT EXISTS blackjack3d_games (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id         INTEGER NOT NULL,
                preset            TEXT    NOT NULL,
                bet               INTEGER NOT NULL,
                server_seed       TEXT    DEFAULT '',
                server_seed_hash  TEXT    DEFAULT '',
                client_seed       TEXT    DEFAULT '',
                nonce             INTEGER DEFAULT 0,
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
        db.execute("CREATE INDEX IF NOT EXISTS idx_bj3d_player ON blackjack3d_games(player_id, status, created_at)")
        # NEW: persistent shoe state per player.
        db.execute(
            """CREATE TABLE IF NOT EXISTS blackjack3d_shoes (
                player_id      INTEGER PRIMARY KEY,
                decks          INTEGER NOT NULL DEFAULT 6,
                cut_fraction   REAL    NOT NULL DEFAULT 0.75,
                cut_index      INTEGER NOT NULL,
                cards_json     TEXT    NOT NULL,
                draw_index     INTEGER NOT NULL DEFAULT 0,
                shuffles       INTEGER NOT NULL DEFAULT 1,
                created_at     TEXT    DEFAULT CURRENT_TIMESTAMP,
                last_shuffled  TEXT    DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (player_id) REFERENCES players(id)
            )"""
        )
        db.commit()
        db.close()
    except Exception:
        try:
            db.close()
        except Exception:
            pass


# ─── Shoe ───────────────────────────────────────────────────────────────────
def _build_deck(decks: int) -> list[dict]:
    cards: list[dict] = []
    for _ in range(decks):
        for s in SUITS:
            for r in RANKS:
                cards.append({"r": r, "s": s})
    return cards


def _env_shoe_defaults() -> tuple[int, float]:
    try:
        decks = int(os.environ.get("SHOE_DECKS", "6") or 6)
    except (TypeError, ValueError):
        decks = 6
    try:
        cut = float(os.environ.get("SHOE_CUT_FRACTION", "0.75") or 0.75)
    except (TypeError, ValueError):
        cut = 0.75
    cut = max(0.30, min(0.95, cut))
    decks = max(1, min(12, decks))
    return decks, cut


class Shoe:
    """Pure-Python multi-deck shoe with a configurable cut card.

    `draw(n)` returns `(cards, needs_reshuffle)`. `needs_reshuffle` becomes
    True as soon as the draw index reaches/exceeds the cut card. The caller
    is expected to finish the current hand and then call `reset()` so the
    next round starts on a freshly shuffled shoe.
    """

    def __init__(self, decks: int = 6, cut_fraction: float = 0.75):
        self.decks = decks
        self.cut_fraction = cut_fraction
        self.cards: list[dict] = []
        self.cut_index: int = 0
        self.draw_index: int = 0
        self.shuffles: int = 0
        self.reset()

    def reset(self) -> None:
        self.cards = _build_deck(self.decks)
        random.shuffle(self.cards)
        self.cut_index = int(len(self.cards) * self.cut_fraction)
        self.draw_index = 0
        self.shuffles += 1

    def draw(self, count: int = 1) -> tuple[list[dict], bool]:
        if self.draw_index + count > len(self.cards):
            # Exhausted (extreme edge case) → reshuffle and continue.
            self.reset()
        cards = self.cards[self.draw_index : self.draw_index + count]
        self.draw_index += count
        return cards, self.draw_index >= self.cut_index

    # Serialization helpers for DB persistence.
    def to_row(self, player_id: int) -> dict[str, Any]:
        return {
            "player_id": player_id,
            "decks": self.decks,
            "cut_fraction": self.cut_fraction,
            "cut_index": self.cut_index,
            "cards_json": json.dumps(self.cards, ensure_ascii=False),
            "draw_index": self.draw_index,
            "shuffles": self.shuffles,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Shoe":
        self = cls.__new__(cls)
        self.decks = int(row["decks"])
        self.cut_fraction = float(row["cut_fraction"])
        self.cards = json.loads(row["cards_json"])
        self.cut_index = int(row["cut_index"])
        self.draw_index = int(row["draw_index"])
        self.shuffles = int(row["shuffles"] or 0)
        return self


def _load_or_init_shoe(db, player_id: int) -> Shoe:
    row = db.execute("SELECT * FROM blackjack3d_shoes WHERE player_id=?", (player_id,)).fetchone()
    if row:
        return Shoe.from_row(dict(row))
    decks, cut = _env_shoe_defaults()
    shoe = Shoe(decks=decks, cut_fraction=cut)
    _persist_shoe(db, player_id, shoe, new=True)
    return shoe


def _persist_shoe(db, player_id: int, shoe: Shoe, new: bool = False) -> None:
    r = shoe.to_row(player_id)
    if new:
        db.execute(
            """INSERT INTO blackjack3d_shoes(player_id,decks,cut_fraction,cut_index,cards_json,draw_index,shuffles,last_shuffled)
            VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(player_id) DO UPDATE SET decks=excluded.decks,cut_fraction=excluded.cut_fraction,
              cut_index=excluded.cut_index, cards_json=excluded.cards_json, draw_index=excluded.draw_index,
              shuffles=excluded.shuffles, last_shuffled=CURRENT_TIMESTAMP""",
            (r["player_id"], r["decks"], r["cut_fraction"], r["cut_index"], r["cards_json"], r["draw_index"], r["shuffles"]),
        )
    else:
        db.execute(
            """UPDATE blackjack3d_shoes SET cut_index=?, cards_json=?, draw_index=?, shuffles=?,
              decks=?, cut_fraction=?
              WHERE player_id=?""",
            (r["cut_index"], r["cards_json"], r["draw_index"], r["shuffles"], r["decks"], r["cut_fraction"], player_id),
        )
    db.commit()


# ─── Hand evaluation ────────────────────────────────────────────────────────
def card_value(rank: str) -> int:
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_total(cards: list[dict]) -> tuple[int, bool]:
    total = sum(card_value(c["r"]) for c in cards)
    aces = sum(1 for c in cards if c["r"] == "A")
    soft = aces > 0 and total <= 21
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
        soft = aces > 0 and total <= 21
    return total, soft


def is_blackjack(cards: list[dict]) -> bool:
    return len(cards) == 2 and hand_total(cards)[0] == 21


def is_pair(cards: list[dict]) -> bool:
    return len(cards) == 2 and cards[0]["r"] == cards[1]["r"]


# ─── Basic strategy (compact reference) ─────────────────────────────────────
_HARD = {
    5: {d: "H" for d in "23456789TA"},
    6: {d: "H" for d in "23456789TA"},
    7: {d: "H" for d in "23456789TA"},
    8: {d: "H" for d in "23456789TA"},
    9: {**{d: "H" for d in "23456789TA"}, "3": "D", "4": "D", "5": "D", "6": "D"},
    10: {**{d: "D" for d in "23456789"}, "T": "H", "A": "H"},
    11: {d: "D" for d in "23456789TA"},
    12: {**{d: "H" for d in "23456789TA"}, "4": "S", "5": "S", "6": "S"},
    13: {**{d: "S" for d in "23456"}, "7": "H", "8": "H", "9": "H", "T": "H", "A": "H"},
    14: {**{d: "S" for d in "23456"}, "7": "H", "8": "H", "9": "H", "T": "H", "A": "H"},
    15: {**{d: "S" for d in "23456"}, "7": "H", "8": "H", "9": "H", "T": "R", "A": "H"},
    16: {**{d: "S" for d in "23456"}, "7": "H", "8": "H", "9": "R", "T": "R", "A": "R"},
    17: {d: "S" for d in "23456789TA"},
    18: {d: "S" for d in "23456789TA"},
    19: {d: "S" for d in "23456789TA"},
    20: {d: "S" for d in "23456789TA"},
    21: {d: "S" for d in "23456789TA"},
}
_SOFT = {
    13: {**{d: "H" for d in "23456789TA"}, "5": "D", "6": "D"},
    14: {**{d: "H" for d in "23456789TA"}, "5": "D", "6": "D"},
    15: {**{d: "H" for d in "23456789TA"}, "4": "D", "5": "D", "6": "D"},
    16: {**{d: "H" for d in "23456789TA"}, "4": "D", "5": "D", "6": "D"},
    17: {**{d: "H" for d in "23456789TA"}, "3": "D", "4": "D", "5": "D", "6": "D"},
    18: {**{d: "S" for d in "23456789TA"}, "2": "S", "3": "Ds", "4": "Ds", "5": "Ds", "6": "Ds", "9": "H", "T": "H", "A": "H"},
    19: {d: "S" for d in "23456789TA"},
    20: {d: "S" for d in "23456789TA"},
    21: {d: "S" for d in "23456789TA"},
}
_PAIRS = {
    "2": {**{d: "H" for d in "23456789TA"}, "2": "P", "3": "P", "4": "P", "5": "P", "6": "P", "7": "P"},
    "3": {**{d: "H" for d in "23456789TA"}, "2": "P", "3": "P", "4": "P", "5": "P", "6": "P", "7": "P"},
    "4": {**{d: "H" for d in "23456789TA"}, "5": "P", "6": "P"},
    "5": {**{d: "D" for d in "23456789"}, "T": "H", "A": "H"},
    "6": {**{d: "H" for d in "23456789TA"}, "2": "P", "3": "P", "4": "P", "5": "P", "6": "P"},
    "7": {**{d: "H" for d in "23456789TA"}, "2": "P", "3": "P", "4": "P", "5": "P", "6": "P", "7": "P"},
    "8": {d: "P" for d in "23456789TA"},
    "9": {**{d: "S" for d in "23456789TA"}, "2": "P", "3": "P", "4": "P", "5": "P", "6": "P", "8": "P", "9": "P"},
    "T": {d: "S" for d in "23456789TA"},
    "A": {d: "P" for d in "23456789TA"},
}


def _dealer_key(c: dict) -> str:
    r = c["r"]
    if r in ("J", "Q", "K", "10"):
        return "T"
    return r


def basic_strategy(hand: list[dict], dealer_up: dict, can_double: bool, can_split: bool, can_surrender: bool) -> str:
    dk = _dealer_key(dealer_up)
    if can_split and is_pair(hand):
        rank = hand[0]["r"]
        key = "T" if rank in ("J", "Q", "K", "10") else rank
        if _PAIRS.get(key, {}).get(dk, "H") == "P":
            return "split"
    total, soft = hand_total(hand)
    rec = (_SOFT if soft else _HARD).get(total, {}).get(dk, "H")
    if rec == "D":
        return "double" if (can_double and len(hand) == 2) else "hit"
    if rec == "Ds":
        return "double" if (can_double and len(hand) == 2) else "stand"
    if rec == "R":
        return "surrender" if (can_surrender and len(hand) == 2) else "hit"
    if rec == "Rs":
        return "surrender" if (can_surrender and len(hand) == 2) else "stand"
    return "hit" if rec == "H" else "stand"


# ─── View models ────────────────────────────────────────────────────────────
def _public_dealer(dealer: list[dict], reveal_all: bool) -> list:
    if reveal_all or not dealer:
        return dealer
    return [dealer[0]] + [None] * (len(dealer) - 1)


def _shoe_meta(db, player_id: int) -> dict[str, Any]:
    row = db.execute("SELECT decks, cut_fraction, cut_index, draw_index, shuffles FROM blackjack3d_shoes WHERE player_id=?", (player_id,)).fetchone()
    if not row:
        return {}
    total = row["decks"] * 52
    return {
        "decks": row["decks"],
        "cut_fraction": row["cut_fraction"],
        "cut_index": row["cut_index"],
        "draw_index": row["draw_index"],
        "shuffles": row["shuffles"],
        "cards_remaining": max(0, total - row["draw_index"]),
        "cards_until_cut": max(0, row["cut_index"] - row["draw_index"]),
        "approaching_cut": row["draw_index"] >= int(row["cut_index"] * 0.85),
    }


def _round_view(db, game: dict, reveal: bool = False) -> dict:
    hands = json.loads(game["hands_json"])
    dealer = json.loads(game["dealer_json"])
    preset = PRESETS[game["preset"]]
    active_idx = game["active_hand"]
    is_active = game["status"] == "active"

    hand_views = []
    for i, h in enumerate(hands):
        total, soft = hand_total(h["cards"])
        hv = {
            "index": i,
            "cards": h["cards"],
            "bet": h["bet"],
            "status": h["status"],
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
    if not is_active or reveal:
        d_total, d_soft = hand_total(dealer)
    else:
        d_total, d_soft = hand_total([dealer[0]])

    legal: list[str] = []
    ins_available = False
    if is_active and 0 <= active_idx < len(hand_views):
        ah = hands[active_idx]
        ah_cards = ah["cards"]
        ah_split_aces = ah.get("is_split_aces", False)
        legal.append("stand")
        if not (ah_split_aces and preset["split_aces_one_card"]):
            legal.append("hit")
        if len(ah_cards) == 2 and not ah.get("doubled", False):
            if not ah_split_aces or preset.get("double_after_split"):
                if active_idx == 0 or preset.get("double_after_split"):
                    legal.append("double")
        splits_used = sum(1 for h in hands if h.get("from_split"))
        if (
            len(ah_cards) == 2
            and is_pair(ah_cards)
            and splits_used + 1 < preset["max_splits"]
        ):
            legal.append("split")
        if (
            preset["surrender"] != "none"
            and len(hands) == 1
            and len(ah_cards) == 2
            and active_idx == 0
        ):
            legal.append("surrender")
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
        "outcome": json.loads(game["outcome_json"]) if game["outcome_json"] else None,
        "shoe": _shoe_meta(db, game["player_id"]),
    }


# ─── Game engine ────────────────────────────────────────────────────────────
def _new_hand(cards: list[dict], bet: int, from_split: bool = False, is_split_aces: bool = False) -> dict:
    return {
        "cards": cards,
        "bet": bet,
        "status": "playing",
        "doubled": False,
        "from_split": from_split,
        "is_split_aces": is_split_aces,
    }


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _settle_dealer_and_hands(game: dict, preset: dict, shoe: Shoe) -> tuple[list[dict], list[dict], bool, int]:
    hands = json.loads(game["hands_json"])
    dealer = json.loads(game["dealer_json"])
    needs_reshuffle_after = False

    any_alive = any(h["status"] in ("stand", "doubled") for h in hands)
    if any_alive:
        while True:
            total, soft = hand_total(dealer)
            if total < 17:
                cards, nr = shoe.draw(1)
                dealer.append(cards[0])
                needs_reshuffle_after = needs_reshuffle_after or nr
                continue
            if total == 17 and soft and preset["dealer_hits_soft_17"]:
                cards, nr = shoe.draw(1)
                dealer.append(cards[0])
                needs_reshuffle_after = needs_reshuffle_after or nr
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
            h["payout"] = h["bet"] // 2
            h["outcome"] = "surrender"
            payout_total += h["payout"]
            continue
        if h["status"] == "blackjack":
            pay = h["bet"] + (h["bet"] * 3 // 2)
            h["payout"] = pay
            h["outcome"] = "blackjack"
            payout_total += pay
            continue
        p_total, _ = hand_total(h["cards"])
        if dealer_bust or p_total > dealer_total:
            pay = h["bet"] * 2
            h["payout"] = pay
            h["outcome"] = "win"
            payout_total += pay
        elif p_total == dealer_total:
            h["payout"] = h["bet"]
            h["outcome"] = "push"
            payout_total += h["bet"]
        else:
            h["payout"] = 0
            h["outcome"] = "lose"
    return hands, dealer, needs_reshuffle_after, payout_total


# ─── Routes ─────────────────────────────────────────────────────────────────
@bp.route("/operator")
def operator_page():
    return render_template("operator.html")


@bp.route("/api/blackjack3d/presets", methods=["GET"])
def list_presets():
    return jsonify({"presets": list(PRESETS.values())})


@bp.route("/api/blackjack3d/round/start", methods=["POST"])
def round_start():
    from app import get_db, _atomic_deduct_points, _add_points

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

    db.execute("UPDATE blackjack3d_games SET status='abandoned' WHERE player_id=? AND status='active'", (player_id,))
    db.commit()

    new_bal = _atomic_deduct_points(db, player_id, bet, "Blackjack 3D panos")
    if new_bal is None:
        return jsonify({"error": "Ei tarpeeksi pisteitä."}), 400

    # Load or initialize persistent shoe for this player.
    shoe = _load_or_init_shoe(db, player_id)
    if shoe.decks != preset["decks"]:
        # Preset switched to a different deck count → start a fresh shoe.
        decks, cut = _env_shoe_defaults()
        shoe = Shoe(decks=preset["decks"], cut_fraction=cut)

    # Initial deal: P, D, P, D
    deal_cards, _ = shoe.draw(4)
    player_cards = [deal_cards[0], deal_cards[2]]
    dealer_cards = [deal_cards[1], deal_cards[3]]
    needs_reshuffle = shoe.draw_index >= shoe.cut_index

    hands = [_new_hand(player_cards, bet)]
    status = "active"
    insurance_bet = 0
    insurance_result = ""
    outcome_json = ""

    player_bj = is_blackjack(player_cards)
    dealer_bj = is_blackjack(dealer_cards)
    dealer_up_high = dealer_cards[0]["r"] in ("A", "10", "J", "Q", "K")

    if player_bj:
        if preset["dealer_peek"] and dealer_up_high:
            if dealer_bj:
                hands[0]["status"] = "stand"
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
        elif preset["dealer_peek"] and not dealer_up_high:
            hands[0]["status"] = "blackjack"
            pay = bet + (bet * 3 // 2)
            hands[0]["payout"] = pay
            hands[0]["outcome"] = "blackjack"
            status = "settled"
            _add_points(db, player_id, pay, "Blackjack 3D luonnollinen 21")
        else:
            # NHC: auto-stand; dealer will play out at settle.
            hands[0]["status"] = "stand"

    # Build outcome json if auto-settled
    if status == "settled":
        outcome_json = json.dumps(
            {
                "hands": [{"outcome": h.get("outcome", ""), "payout": h.get("payout", 0), "bet": h["bet"]} for h in hands],
                "dealer_total": hand_total(dealer_cards)[0],
                "net": sum(h.get("payout", 0) for h in hands) - bet,
            },
            ensure_ascii=False,
        )
        if needs_reshuffle:
            shoe.reset()

    _persist_shoe(db, player_id, shoe)

    cur = db.execute(
        """INSERT INTO blackjack3d_games(
            player_id, preset, bet, server_seed, server_seed_hash, client_seed, nonce,
            shoe_json, shoe_index, hands_json, dealer_json, active_hand, status,
            insurance_bet, insurance_result, revealed, outcome_json, ended_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            player_id, preset_id, bet, "", "", "", 0,
            "[]", 0,
            json.dumps(hands, ensure_ascii=False),
            json.dumps(dealer_cards, ensure_ascii=False),
            0, status, insurance_bet, insurance_result, 0,
            outcome_json,
            None if status == "active" else _now_iso(),
        ),
    )
    gid = cur.lastrowid
    db.commit()

    game = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone())
    view = _round_view(db, game, reveal=(status != "active"))
    view["balance"] = (db.execute("SELECT points FROM players WHERE id=?", (player_id,)).fetchone() or {"points": 0})["points"]
    if needs_reshuffle and status == "active":
        view["shoe"]["reshuffle_pending"] = True
    return jsonify(view)


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
    pid = game["player_id"]
    active = game["active_hand"]
    if active >= len(hands):
        return jsonify({"error": "Ei aktiivista kättä."}), 400
    cur_hand = hands[active]

    shoe = _load_or_init_shoe(db, pid)

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
            _add_points(db, pid, ins * 3, "Blackjack 3D vakuutusvoitto")
            game["insurance_result"] = "win"
            if is_blackjack(cur_hand["cards"]):
                cur_hand["status"] = "stand"
            else:
                cur_hand["status"] = "bust"
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
            game = _maybe_finalize(db, game, shoe)
        return _respond(db, game, pid)

    if action == "decline_insurance":
        if dealer[0]["r"] != "A" or not preset["insurance"]:
            return jsonify({"error": "Ei vakuutustilanteetta."}), 400
        if preset["dealer_peek"] and is_blackjack(dealer):
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
            game = _maybe_finalize(db, game, shoe)
        else:
            db.execute("UPDATE blackjack3d_games SET insurance_result='lose' WHERE id=?", (gid,))
            db.commit()
            game = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone())
        return _respond(db, game, pid)

    if action == "hit":
        if cur_hand.get("is_split_aces") and preset["split_aces_one_card"]:
            return jsonify({"error": "Hajotetut ässät saavat vain yhden kortin."}), 400
        cards, _ = shoe.draw(1)
        cur_hand["cards"].append(cards[0])
        total, _s = hand_total(cur_hand["cards"])
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
        cards, _ = shoe.draw(1)
        cur_hand["cards"].append(cards[0])
        total, _s = hand_total(cur_hand["cards"])
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
        c1, c2 = cur_hand["cards"][0], cur_hand["cards"][1]
        is_aces = c1["r"] == "A"
        new1, _ = shoe.draw(1)
        new2, _ = shoe.draw(1)
        old_bet = cur_hand["bet"]
        hands[active] = _new_hand([c1, new1[0]], old_bet, from_split=True, is_split_aces=is_aces)
        hands.insert(active + 1, _new_hand([c2, new2[0]], old_bet, from_split=True, is_split_aces=is_aces))
        if is_aces and preset["split_aces_one_card"]:
            for h in (hands[active], hands[active + 1]):
                h["status"] = "stand"

    elif action == "surrender":
        if not (preset["surrender"] != "none" and len(hands) == 1 and len(cur_hand["cards"]) == 2 and active == 0):
            return jsonify({"error": "Antautuminen ei ole mahdollinen."}), 400
        half = cur_hand["bet"] // 2
        _add_points(db, pid, half, "Blackjack 3D antautuminen (palautus)")
        cur_hand["status"] = "surrendered"
        cur_hand["payout"] = half
        cur_hand["outcome"] = "surrender"

    else:
        return jsonify({"error": "Virheellinen toiminto."}), 400

    db.execute("UPDATE blackjack3d_games SET hands_json=? WHERE id=?", (json.dumps(hands, ensure_ascii=False), gid))
    _persist_shoe(db, pid, shoe)
    db.commit()
    game = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone())
    game = _advance_active_hand_if_needed(db, game)
    game = _maybe_finalize(db, game, shoe)
    return _respond(db, game, pid)


def _respond(db, game: dict, pid: int):
    view = _round_view(db, game, reveal=(game["status"] != "active"))
    view["balance"] = (db.execute("SELECT points FROM players WHERE id=?", (pid,)).fetchone() or {"points": 0})["points"]
    return jsonify(view)


def _advance_active_hand_if_needed(db, game: dict) -> dict:
    hands = json.loads(game["hands_json"])
    active = game["active_hand"]
    moved = False
    while active < len(hands) and hands[active]["status"] != "playing":
        active += 1
        moved = True
    if moved:
        db.execute("UPDATE blackjack3d_games SET active_hand=? WHERE id=?", (active, game["id"]))
        db.commit()
        game = dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (game["id"],)).fetchone())
    return game


def _maybe_finalize(db, game_row: dict, shoe: Shoe) -> dict:
    hands = json.loads(game_row["hands_json"])
    all_done = all(h["status"] in ("bust", "stand", "doubled", "surrendered", "blackjack") for h in hands)
    if not all_done:
        return game_row

    from app import _add_points

    preset = PRESETS[game_row["preset"]]
    updated_hands, updated_dealer, needs_reshuffle, payout = _settle_dealer_and_hands(game_row, preset, shoe)

    for h in updated_hands:
        if h.get("payout", 0) > 0 and h.get("outcome") != "surrender":
            label = {
                "blackjack": "Blackjack 3D luonnollinen 21",
                "win": "Blackjack 3D voitto",
                "push": "Blackjack 3D tasapeli",
            }.get(h.get("outcome", ""), "Blackjack 3D voitto")
            _add_points(db, game_row["player_id"], h["payout"], label)

    outcome_summary = {
        "hands": [{"outcome": h.get("outcome", ""), "payout": h.get("payout", 0), "bet": h["bet"]} for h in updated_hands],
        "dealer_total": hand_total(updated_dealer)[0],
        "net": sum(h.get("payout", 0) for h in updated_hands) - sum(h["bet"] for h in updated_hands),
    }
    # Persist any dealer draws + reshuffle if cut card reached this round.
    if needs_reshuffle or shoe.draw_index >= shoe.cut_index:
        shoe.reset()
    _persist_shoe(db, game_row["player_id"], shoe)
    db.execute(
        """UPDATE blackjack3d_games SET hands_json=?, dealer_json=?, status=?, revealed=?,
           outcome_json=?, ended_at=? WHERE id=?""",
        (
            json.dumps(updated_hands, ensure_ascii=False),
            json.dumps(updated_dealer, ensure_ascii=False),
            "settled",
            1,
            json.dumps(outcome_summary, ensure_ascii=False),
            _now_iso(),
            game_row["id"],
        ),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (game_row["id"],)).fetchone())


@bp.route("/api/blackjack3d/round/<int:gid>", methods=["GET"])
def round_state(gid: int):
    from app import get_db

    db = get_db()
    row = db.execute("SELECT * FROM blackjack3d_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({"error": "Peliä ei löydy."}), 404
    game = dict(row)
    return _respond(db, game, game["player_id"])


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
        len(h["cards"]) == 2
        and is_pair(h["cards"])
        and sum(1 for hh in hands if hh.get("from_split")) + 1 < preset["max_splits"]
    )
    can_surrender = (preset["surrender"] != "none" and len(hands) == 1 and len(h["cards"]) == 2 and active == 0)
    rec = basic_strategy(h["cards"], dealer[0], can_double, can_split, can_surrender)
    return jsonify({"hint": rec})


@bp.route("/api/blackjack3d/shoe", methods=["GET"])
def shoe_state_for_player():
    """Public, lightweight shoe meta for the customer page (no card list)."""
    from app import get_db

    pid = request.args.get("player_id", type=int)
    if not pid:
        return jsonify({"error": "player_id vaaditaan."}), 400
    db = get_db()
    return jsonify({"shoe": _shoe_meta(db, pid)})


# ─── Operator (admin) endpoints ─────────────────────────────────────────────
@bp.route("/api/operator/blackjack/shoes", methods=["GET"])
@op_required
def op_list_shoes():
    from app import get_db

    db = get_db()
    rows = db.execute(
        """SELECT s.player_id, p.name, s.decks, s.cut_fraction, s.cut_index,
                  s.draw_index, s.shuffles, s.last_shuffled,
                  (s.decks*52 - s.draw_index) AS cards_remaining
           FROM blackjack3d_shoes s LEFT JOIN players p ON p.id = s.player_id
           ORDER BY s.last_shuffled DESC"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["cards_until_cut"] = max(0, d["cut_index"] - d["draw_index"])
        d["approaching_cut"] = d["draw_index"] >= int(d["cut_index"] * 0.85)
        out.append(d)
    return jsonify({"shoes": out})


@bp.route("/api/operator/blackjack/shoes/<int:player_id>/reset", methods=["POST"])
@op_required
def op_reset_shoe(player_id: int):
    from app import get_db

    db = get_db()
    shoe = _load_or_init_shoe(db, player_id)
    shoe.reset()
    _persist_shoe(db, player_id, shoe)
    return jsonify({"ok": True, "player_id": player_id, "shoe": _shoe_meta(db, player_id)})


@bp.route("/api/operator/blackjack/shoes/reset_all", methods=["POST"])
@op_required
def op_reset_all_shoes():
    from app import get_db

    db = get_db()
    rows = db.execute("SELECT player_id FROM blackjack3d_shoes").fetchall()
    count = 0
    for r in rows:
        shoe = _load_or_init_shoe(db, r["player_id"])
        shoe.reset()
        _persist_shoe(db, r["player_id"], shoe)
        count += 1
    return jsonify({"ok": True, "reset": count})


@bp.route("/api/operator/blackjack/settings", methods=["GET"])
@op_required
def op_get_settings():
    decks, cut = _env_shoe_defaults()
    return jsonify({
        "min_bet": MIN_BET,
        "max_bet": MAX_BET,
        "default_decks": decks,
        "default_cut_fraction": cut,
        "presets": list(PRESETS.values()),
    })


@bp.route("/api/operator/blackjack/recent_rounds", methods=["GET"])
@op_required
def op_recent_rounds():
    from app import get_db

    db = get_db()
    rows = db.execute(
        """SELECT g.id, g.player_id, p.name, g.preset, g.bet, g.status,
                  g.created_at, g.ended_at, g.outcome_json
           FROM blackjack3d_games g LEFT JOIN players p ON p.id = g.player_id
           ORDER BY g.id DESC LIMIT 50"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["outcome"] = json.loads(d.pop("outcome_json") or "null")
        except Exception:
            d["outcome"] = None
        out.append(d)
    return jsonify({"rounds": out})


@bp.route("/api/operator/blackjack/stats", methods=["GET"])
@op_required
def op_stats():
    from app import get_db

    db = get_db()
    totals = db.execute(
        """SELECT
              COUNT(*)              AS total_rounds,
              COUNT(CASE WHEN status='settled' THEN 1 END) AS settled_rounds,
              COALESCE(SUM(bet),0)  AS handle
           FROM blackjack3d_games"""
    ).fetchone()
    shoes = db.execute("SELECT COUNT(*) c, COALESCE(SUM(shuffles),0) total_shuffles FROM blackjack3d_shoes").fetchone()
    players = db.execute("SELECT COUNT(*) c, COALESCE(SUM(points),0) bank FROM players").fetchone()
    return jsonify({
        "rounds": dict(totals),
        "shoes": dict(shoes),
        "players": dict(players),
    })


def register(app, db_path: str) -> None:
    _migrate(db_path)
    app.register_blueprint(bp)
