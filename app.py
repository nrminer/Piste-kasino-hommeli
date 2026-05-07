import os, json, random, string, socket, hashlib, time, math
from datetime import datetime
from flask import Flask, request, jsonify, render_template, g
import sqlite3

app = Flask(__name__)
DATABASE = 'casino.db'

def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# ─── DB helpers ──────────────────────────────────────────────────────────────

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT DEFAULT '',
    phone         TEXT DEFAULT '',
    vip_level     TEXT DEFAULT 'Standard',
    notes         TEXT DEFAULT '',
    password_hash TEXT DEFAULT '',
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL,
    amount      REAL NOT NULL,
    game_type   TEXT DEFAULT 'Muu',
    note        TEXT DEFAULT '',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (player_id) REFERENCES players(id)
);
CREATE TABLE IF NOT EXISTS bonuses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL,
    label       TEXT NOT NULL DEFAULT 'Bonus',
    amount      REAL DEFAULT 0,
    claimed     INTEGER DEFAULT 0,
    seen        INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (player_id) REFERENCES players(id)
);
CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    player_id   INTEGER DEFAULT NULL,
    player_name TEXT DEFAULT '',
    details     TEXT DEFAULT '',
    actor       TEXT DEFAULT 'management',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS slot_bonus_games (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id    INTEGER NOT NULL,
    theme_id     TEXT DEFAULT 'fruits',
    bet          INTEGER NOT NULL,
    rewards_json TEXT NOT NULL,
    picked_json  TEXT DEFAULT '[]',
    total_reward INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'active',
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (player_id) REFERENCES players(id)
);
CREATE TABLE IF NOT EXISTS poker_sessions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    status               TEXT DEFAULT 'waiting',
    deck_json            TEXT DEFAULT '[]',
    community_cards_json TEXT DEFAULT '[]',
    stage                TEXT DEFAULT 'waiting',
    preset_hands_json    TEXT DEFAULT '{}',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS poker_seats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    player_name         TEXT NOT NULL,
    player_id           INTEGER DEFAULT NULL,
    hole_cards_json     TEXT DEFAULT '[]',
    folded              INTEGER DEFAULT 0,
    active              INTEGER DEFAULT 1,
    show_cards          INTEGER DEFAULT 0,
    join_token          TEXT NOT NULL UNIQUE,
    seat_number         INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES poker_sessions(id)
);
'''

def _hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(SCHEMA)
    migrations = [
        'ALTER TABLE poker_seats    ADD COLUMN show_cards        INTEGER DEFAULT 0',
        'ALTER TABLE poker_seats    ADD COLUMN player_id         INTEGER DEFAULT NULL',
        'ALTER TABLE poker_sessions ADD COLUMN preset_hands_json TEXT DEFAULT "{}"',
        "CREATE TABLE IF NOT EXISTS bonuses (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER NOT NULL, label TEXT NOT NULL DEFAULT 'Bonus', amount REAL DEFAULT 0, claimed INTEGER DEFAULT 0, seen INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (player_id) REFERENCES players(id))",
        "ALTER TABLE players ADD COLUMN password_hash TEXT DEFAULT ''",
        "ALTER TABLE bonuses ADD COLUMN seen INTEGER DEFAULT 1",
        "ALTER TABLE players ADD COLUMN spins_remaining INTEGER DEFAULT 0",
        "ALTER TABLE players ADD COLUMN points INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS point_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            reason TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES players(id)
        )""",
        """CREATE TABLE IF NOT EXISTS blackjack_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            bet INTEGER NOT NULL,
            deck_json TEXT NOT NULL,
            player_cards_json TEXT NOT NULL,
            dealer_cards_json TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            result_json TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES players(id)
        )""",
        "ALTER TABLE players ADD COLUMN streak_mode TEXT DEFAULT 'normal'",
        "CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, player_id INTEGER DEFAULT NULL, player_name TEXT DEFAULT '', details TEXT DEFAULT '', actor TEXT DEFAULT 'management', created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS slot_bonus_games (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER NOT NULL, theme_id TEXT DEFAULT 'fruits', bet INTEGER NOT NULL, rewards_json TEXT NOT NULL, picked_json TEXT DEFAULT '[]', total_reward INTEGER DEFAULT 0, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (player_id) REFERENCES players(id))",
        """CREATE TABLE IF NOT EXISTS system_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS pikapokeri_games (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id   INTEGER NOT NULL,
            bet         INTEGER NOT NULL,
            deck_json   TEXT NOT NULL,
            hand_json   TEXT NOT NULL,
            status      TEXT DEFAULT 'deal',
            payout      INTEGER DEFAULT 0,
            result_rank INTEGER DEFAULT -1,
            result_name TEXT DEFAULT '',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES players(id)
        )""",
        "ALTER TABLE blackjack_games ADD COLUMN insurance_bet INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS poker_hand_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL,
            hand_number     INTEGER NOT NULL,
            started_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            ended_at        TEXT,
            stage_reached   TEXT DEFAULT 'preflop',
            ended_by        TEXT DEFAULT 'in_progress',
            community_cards TEXT DEFAULT '[]',
            seats           TEXT DEFAULT '[]',
            winners         TEXT DEFAULT '[]'
        )""",
        "CREATE INDEX IF NOT EXISTS idx_hand_log_session ON poker_hand_log(session_id, hand_number)",
        "CREATE INDEX IF NOT EXISTS idx_hand_log_started ON poker_hand_log(started_at DESC)",
        # ── Card games upgrade pack (Iteration 3) ──
        "ALTER TABLE blackjack_games ADD COLUMN side_bets_json TEXT DEFAULT ''",
        "ALTER TABLE blackjack_games ADD COLUMN split_hands_json TEXT DEFAULT '[]'",
        "ALTER TABLE blackjack_games ADD COLUMN split_count INTEGER DEFAULT 0",
        "ALTER TABLE blackjack_games ADD COLUMN active_hand_index INTEGER DEFAULT 0",
        "ALTER TABLE blackjack_games ADD COLUMN surrender_amount INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS bonus_buy_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            game_theme TEXT NOT NULL,
            bonus_type TEXT NOT NULL,
            cost_points INTEGER NOT NULL,
            payout_points INTEGER DEFAULT 0,
            rng_seed TEXT,
            status TEXT DEFAULT 'consumed',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_bonus_buy_log_player ON bonus_buy_log(player_id, game_theme, created_at)",
        "ALTER TABLE poker_sessions ADD COLUMN pot_json TEXT DEFAULT '{}'",
        "ALTER TABLE poker_sessions ADD COLUMN current_bettor_seat_id INTEGER DEFAULT NULL",
        "ALTER TABLE poker_sessions ADD COLUMN current_bet_pts INTEGER DEFAULT 0",
        "ALTER TABLE poker_sessions ADD COLUMN min_raise_pts INTEGER DEFAULT 0",
        "ALTER TABLE poker_sessions ADD COLUMN big_blind_pts INTEGER DEFAULT 100",
        "ALTER TABLE poker_sessions ADD COLUMN small_blind_pts INTEGER DEFAULT 50",
        "ALTER TABLE poker_sessions ADD COLUMN dealer_button_seat_id INTEGER DEFAULT NULL",
        "ALTER TABLE poker_sessions ADD COLUMN mode TEXT DEFAULT 'mode_a'",
        "ALTER TABLE poker_seats ADD COLUMN current_round_bet_pts INTEGER DEFAULT 0",
        "ALTER TABLE poker_seats ADD COLUMN total_session_contribution_pts INTEGER DEFAULT 0",
        "ALTER TABLE poker_seats ADD COLUMN all_in INTEGER DEFAULT 0",
        "ALTER TABLE poker_seats ADD COLUMN last_action TEXT DEFAULT ''",
    ]
    for m in migrations:
        try:
            db.execute(m)
        except Exception:
            pass
    db.commit()
    db.close()

def _audit(db, action, player_id=None, player_name='', details=None, actor='management'):
    try:
        payload = details if isinstance(details, str) else json.dumps(details or {}, ensure_ascii=False)
        db.execute(
            'INSERT INTO audit_events(action,player_id,player_name,details,actor) VALUES(?,?,?,?,?)',
            (action, player_id, player_name or '', payload, actor)
        )
    except Exception:
        pass

def _create_slot_bonus_game(db, pid, theme_id, bet):
    """Create a server-authoritative pick bonus round.
    Rewards are pre-generated and hidden from the client until picked.
    """
    # Low-variance pick bonus calibrated as part of total slot RTP.
    # Sum=4.0, three random picks from 12 => EV ≈ 1× bet per bonus trigger.
    mults = [0, 0, 0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.35, 0.50, 0.80, 1.60]
    random.shuffle(mults)
    rewards = []
    labels = ['Mystery', 'Spark', 'Gear', 'Crown', 'Vault', 'Nova', 'Gem', 'Wild', 'Key', 'Pulse', 'Mask', 'Finale']
    for idx, mult in enumerate(mults):
        rewards.append({
            'tile': idx,
            'label': labels[idx],
            'mult': mult,
            'reward': int(round(bet * mult)),
        })
    cur = db.execute(
        'INSERT INTO slot_bonus_games(player_id,theme_id,bet,rewards_json) VALUES(?,?,?,?)',
        (pid, theme_id, bet, json.dumps(rewards, ensure_ascii=False))
    )
    return {'id': cur.lastrowid, 'picks_remaining': 3, 'tile_count': len(rewards)}

init_db()

# ─── Deck utils ──────────────────────────────────────────────────────────────

SUITS = ['♠', '♥', '♦', '♣']
RANKS = ['2','3','4','5','6','7','8','9','10','J','Q','K','A']

def new_deck():
    deck = [{'rank': r, 'suit': s} for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck

def gen_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=24))

def get_local_ip():
    """Returns a host the customer/poker-player browser can reach.

    Inside a request we use `request.host` (works for the Emergent preview,
    Vercel, and any custom domain). Outside a request we fall back to the
    LAN IP, with 'localhost' as the last resort.
    """
    try:
        if request:
            return request.host
    except RuntimeError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return 'localhost'

def current_session(db):
    row = db.execute('SELECT * FROM poker_sessions ORDER BY id DESC LIMIT 1').fetchone()
    return dict(row) if row else None

# ─── Poker hand evaluation ────────────────────────────────────────────────────

HAND_NAMES = {
    9: 'Royal Flush', 8: 'Värisuora',   7: 'Nelikko',
    6: 'Full House',  5: 'Väri',        4: 'Suora',
    3: 'Kolmikko',    2: 'Kaksi paria', 1: 'Pari', 0: 'Korkein kortti',
}
_RV = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'10':10,'J':11,'Q':12,'K':13,'A':14}

def _eval5(cards):
    from collections import Counter
    vals  = sorted([_RV[c['rank']] for c in cards], reverse=True)
    suits = [c['suit'] for c in cards]
    flush = len(set(suits)) == 1
    uv    = sorted(set(vals), reverse=True)
    straight, hi = False, 0
    if len(uv) == 5 and uv[0] - uv[4] == 4:
        straight, hi = True, uv[0]
    elif uv == [14,5,4,3,2]:
        straight, hi, vals = True, 5, [5,4,3,2,1]
    cnt    = Counter(vals)
    groups = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
    cl     = [g[1] for g in groups]
    vl     = [g[0] for g in groups]
    if straight and flush: return (9 if hi == 14 else 8, [hi])
    if cl[0] == 4:         return (7, vl)
    if cl[0] == 3 and cl[1] == 2: return (6, vl)
    if flush:              return (5, vals)
    if straight:           return (4, [hi])
    if cl[0] == 3:         return (3, vl)
    if cl[0] == 2 and cl[1] == 2: return (2, vl)
    if cl[0] == 2:         return (1, vl)
    return (0, vals)

def best_hand(hole, community):
    from itertools import combinations
    best = None
    for combo in combinations(hole + community, 5):
        r = _eval5(list(combo))
        if best is None or r > best:
            best = r
    return best

# ─── Spin prizes ─────────────────────────────────────────────────────────────

SPIN_PRIZES = [
    {'bonus': 10,  'label': '10% Matchausbonus',  'weight': 80},
    {'bonus': 20,  'label': '20% Matchausbonus',  'weight': 12},
    {'bonus': 50,  'label': '50% Matchausbonus',  'weight': 5},
    {'bonus': 100, 'label': '100% Matchausbonus', 'weight': 3},
]

# ─── Pages ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', local_ip=get_local_ip())

@app.route('/poker/join')
def poker_join_page():
    return render_template('poker_player.html')

@app.route('/asiakas')
def customer_page():
    return render_template('customer.html', local_ip=get_local_ip())

@app.route('/manifest.json')
def pwa_manifest():
    from flask import Response
    manifest = {
        "name": "Kasino",
        "short_name": "Kasino",
        "start_url": "/asiakas",
        "display": "standalone",
        "background_color": "#0a1a10",
        "theme_color": "#0a1a10",
        "orientation": "portrait",
        "icons": [
            {"src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' fill='%230a1a10'/><text y='.9em' font-size='80' x='10'>♠</text></svg>",
             "sizes": "any", "type": "image/svg+xml"}
        ]
    }
    return Response(json.dumps(manifest), mimetype='application/manifest+json')

@app.route('/favicon.ico')
@app.route('/favicon.svg')
def favicon():
    from flask import Response
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<rect width='64' height='64' rx='12' fill='#0d1f17'/>"
        "<text x='50%' y='52%' text-anchor='middle' dominant-baseline='middle' "
        "font-size='42' font-family='serif' fill='#c9a84c'>♠</text></svg>"
    )
    return Response(svg, mimetype='image/svg+xml',
                    headers={'Cache-Control': 'public, max-age=86400'})

@app.route('/api/_health')
def _health():
    return jsonify({'ok': True, 'storage': {'mode': 'sqlite'}})

# ─── Players API ─────────────────────────────────────────────────────────────

@app.route('/api/players', methods=['GET'])
def list_players():
    db = get_db()
    q   = request.args.get('q',   '').strip().lower()
    vip = request.args.get('vip', '').strip()
    rows = db.execute('''
        SELECT p.*,
            COALESCE(SUM(CASE WHEN t.amount >  0 THEN  t.amount ELSE 0 END), 0) AS total_won,
            COALESCE(SUM(CASE WHEN t.amount <  0 THEN -t.amount ELSE 0 END), 0) AS total_lost,
            COALESCE(SUM(t.amount), 0) AS net_balance,
            COUNT(t.id) AS tx_count
        FROM players p
        LEFT JOIN transactions t ON t.player_id = p.id
        GROUP BY p.id ORDER BY p.name
    ''').fetchall()
    players = [dict(r) for r in rows]
    # Strip password hash from list response, but expose has_password flag
    for p in players:
        has_pw = bool(p.get('password_hash', ''))
        p.pop('password_hash', None)
        p['has_password']    = has_pw
        p['spins_remaining'] = p.get('spins_remaining') or 0
        p['points']          = p.get('points') or 0
        p['streak_mode']     = p.get('streak_mode') or 'normal'
    if q:
        players = [p for p in players if q in p['name'].lower()
                   or q in (p['email'] or '').lower()
                   or q in (p['phone'] or '').lower()]
    if vip:
        players = [p for p in players if p['vip_level'] == vip]
    return jsonify(players)

@app.route('/api/players', methods=['POST'])
def create_player():
    d  = request.json
    db = get_db()
    pw = (d.get('password') or '').strip()
    pw_hash = _hash_pw(pw) if pw else ''
    cur = db.execute(
        'INSERT INTO players(name,email,phone,vip_level,notes,password_hash) VALUES(?,?,?,?,?,?)',
        (d['name'], d.get('email',''), d.get('phone',''),
         d.get('vip_level','Standard'), d.get('notes',''), pw_hash)
    )
    db.commit()
    row = dict(db.execute('SELECT * FROM players WHERE id=?', (cur.lastrowid,)).fetchone())
    row.pop('password_hash', None)
    row['has_password'] = bool(pw_hash)
    row.update({'total_won': 0, 'total_lost': 0, 'net_balance': 0, 'tx_count': 0})
    _audit(db, 'player_created', cur.lastrowid, d['name'], {'vip_level': d.get('vip_level','Standard')})
    db.commit()
    return jsonify(row), 201

@app.route('/api/players/bulk', methods=['POST'])
def bulk_players():
    d = request.json or {}
    ids = []
    for raw in d.get('ids', []):
        try:
            pid = int(raw)
            if pid not in ids:
                ids.append(pid)
        except (TypeError, ValueError):
            pass
    if not ids:
        return jsonify({'error': 'Valitse vähintään yksi asiakas.'}), 400
    action = d.get('action')
    db = get_db()
    marks = ','.join('?' for _ in ids)
    rows = [dict(r) for r in db.execute(f'SELECT id,name FROM players WHERE id IN ({marks})', ids).fetchall()]
    if not rows:
        return jsonify({'error': 'Valittuja asiakkaita ei löydy.'}), 404
    found_ids = [r['id'] for r in rows]
    if action == 'grant_spins':
        try:
            count = int(d.get('count', 0))
        except (TypeError, ValueError):
            return jsonify({'error': 'Virheellinen pyöräytysmäärä.'}), 400
        if count == 0:
            return jsonify({'error': 'Määrä ei voi olla 0.'}), 400
        for p in rows:
            db.execute('UPDATE players SET spins_remaining=MAX(0, COALESCE(spins_remaining,0)+?) WHERE id=?', (count, p['id']))
            _audit(db, 'bulk_grant_spins', p['id'], p['name'], {'count': count})
    elif action == 'grant_points':
        try:
            count = int(d.get('count', 0))
        except (TypeError, ValueError):
            return jsonify({'error': 'Virheellinen pistemäärä.'}), 400
        if count == 0:
            return jsonify({'error': 'Määrä ei voi olla 0.'}), 400
        reason = (d.get('reason') or 'Bulk pistepäivitys').strip()[:120]
        for p in rows:
            row = db.execute('SELECT points FROM players WHERE id=?', (p['id'],)).fetchone()
            current = row['points'] or 0
            new_val = max(0, current + count)
            actual_delta = new_val - current
            db.execute('UPDATE players SET points=? WHERE id=?', (new_val, p['id']))
            _log_points(db, p['id'], actual_delta, reason)
            _audit(db, 'bulk_grant_points', p['id'], p['name'], {'delta': actual_delta, 'reason': reason})
    elif action == 'set_vip':
        vip = d.get('vip_level')
        if vip not in ('Standard', 'Silver', 'Gold', 'Whale'):
            return jsonify({'error': 'Virheellinen VIP-taso.'}), 400
        for p in rows:
            db.execute('UPDATE players SET vip_level=? WHERE id=?', (vip, p['id']))
            _audit(db, 'bulk_set_vip', p['id'], p['name'], {'vip_level': vip})
    elif action == 'set_streak':
        mode = d.get('mode')
        if mode not in ('normal', 'win', 'lose'):
            return jsonify({'error': 'Virheellinen putkitila.'}), 400
        for p in rows:
            db.execute('UPDATE players SET streak_mode=? WHERE id=?', (mode, p['id']))
            _audit(db, 'bulk_set_streak', p['id'], p['name'], {'mode': mode})
    elif action == 'delete':
        for p in rows:
            db.execute('DELETE FROM transactions WHERE player_id=?', (p['id'],))
            db.execute('DELETE FROM bonuses WHERE player_id=?', (p['id'],))
            db.execute('DELETE FROM point_transactions WHERE player_id=?', (p['id'],))
            db.execute('DELETE FROM players WHERE id=?', (p['id'],))
            _audit(db, 'bulk_delete_player', p['id'], p['name'], {'deleted': True})
    else:
        return jsonify({'error': 'Tuntematon bulk-toiminto.'}), 400
    db.commit()
    return jsonify({'ok': True, 'affected': len(found_ids), 'action': action})

@app.route('/api/players/<int:pid>', methods=['PUT'])
def update_player(pid):
    d  = request.json
    db = get_db()
    pw = (d.get('password') or '').strip()
    if pw:
        pw_hash = _hash_pw(pw)
        db.execute(
            'UPDATE players SET name=?,email=?,phone=?,vip_level=?,notes=?,password_hash=? WHERE id=?',
            (d['name'], d.get('email',''), d.get('phone',''),
             d.get('vip_level','Standard'), d.get('notes',''), pw_hash, pid)
        )
    else:
        db.execute(
            'UPDATE players SET name=?,email=?,phone=?,vip_level=?,notes=? WHERE id=?',
            (d['name'], d.get('email',''), d.get('phone',''),
             d.get('vip_level','Standard'), d.get('notes',''), pid)
        )
    row = db.execute('SELECT name FROM players WHERE id=?', (pid,)).fetchone()
    _audit(db, 'player_updated', pid, row['name'] if row else d.get('name',''), {'vip_level': d.get('vip_level','Standard')})
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/players/<int:pid>', methods=['DELETE'])
def delete_player(pid):
    db = get_db()
    row = db.execute('SELECT name FROM players WHERE id=?', (pid,)).fetchone()
    db.execute('DELETE FROM transactions WHERE player_id=?', (pid,))
    db.execute('DELETE FROM bonuses WHERE player_id=?', (pid,))
    db.execute('DELETE FROM point_transactions WHERE player_id=?', (pid,))
    db.execute('DELETE FROM players WHERE id=?', (pid,))
    _audit(db, 'player_deleted', pid, row['name'] if row else '', {'deleted': True})
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/players/<int:pid>/grant-spins', methods=['POST'])
def grant_spins(pid):
    d  = request.json or {}
    try:
        count = int(d.get('count', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen määrä.'}), 400
    if count == 0:
        return jsonify({'error': 'Määrä ei voi olla 0.'}), 400
    db = get_db()
    row = db.execute('SELECT * FROM players WHERE id=?', (pid,)).fetchone()
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    current = (row['spins_remaining'] or 0) if 'spins_remaining' in row.keys() else 0
    new_val = max(0, current + count)
    db.execute('UPDATE players SET spins_remaining=? WHERE id=?', (new_val, pid))
    _audit(db, 'grant_spins', pid, row['name'], {'count': count, 'spins_remaining': new_val})
    db.commit()
    return jsonify({'ok': True, 'spins_remaining': new_val, 'granted': count})

@app.route('/api/players/<int:pid>/spins', methods=['GET'])
def get_spins(pid):
    db = get_db()
    row = db.execute('SELECT spins_remaining FROM players WHERE id=?', (pid,)).fetchone()
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    return jsonify({'spins_remaining': row['spins_remaining'] or 0})

@app.route('/api/players/<int:pid>/transactions', methods=['GET'])
def player_transactions(pid):
    rows = get_db().execute(
        'SELECT * FROM transactions WHERE player_id=? ORDER BY created_at DESC', (pid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/players/<int:pid>/transactions', methods=['POST'])
def add_transaction(pid):
    d   = request.json
    db  = get_db()
    cur = db.execute(
        'INSERT INTO transactions(player_id,amount,game_type,note) VALUES(?,?,?,?)',
        (pid, float(d['amount']), d.get('game_type','Muu'), d.get('note',''))
    )
    p = db.execute('SELECT name FROM players WHERE id=?', (pid,)).fetchone()
    _audit(db, 'transaction_added', pid, p['name'] if p else '', {'amount': float(d['amount']), 'game_type': d.get('game_type','Muu')})
    db.commit()
    return jsonify(dict(db.execute('SELECT * FROM transactions WHERE id=?', (cur.lastrowid,)).fetchone())), 201

@app.route('/api/transactions/<int:tid>', methods=['DELETE'])
def delete_transaction(tid):
    db = get_db()
    row = db.execute('SELECT t.*, p.name AS player_name FROM transactions t LEFT JOIN players p ON p.id=t.player_id WHERE t.id=?', (tid,)).fetchone()
    db.execute('DELETE FROM transactions WHERE id=?', (tid,))
    if row:
        _audit(db, 'transaction_deleted', row['player_id'], row['player_name'] or '', {'amount': row['amount'], 'game_type': row['game_type']})
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/audit', methods=['GET'])
def list_audit():
    db = get_db()
    action = request.args.get('action', '').strip()
    q = request.args.get('q', '').strip().lower()
    try:
        limit = min(100, max(10, int(request.args.get('limit', 50))))
    except (TypeError, ValueError):
        limit = 50
    where, args = [], []
    if action:
        where.append('action=?'); args.append(action)
    if q:
        where.append('(LOWER(player_name) LIKE ? OR LOWER(details) LIKE ? OR LOWER(action) LIKE ?)')
        args.extend([f'%{q}%', f'%{q}%', f'%{q}%'])
    sql = 'SELECT * FROM audit_events'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY created_at DESC, id DESC LIMIT ?'
    args.append(limit)
    return jsonify([dict(r) for r in db.execute(sql, args).fetchall()])

# ─── Bonuses API ─────────────────────────────────────────────────────────────

@app.route('/api/players/<int:pid>/bonuses', methods=['GET'])
def get_player_bonuses(pid):
    rows = get_db().execute(
        'SELECT * FROM bonuses WHERE player_id=? ORDER BY created_at DESC', (pid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/players/<int:pid>/bonuses', methods=['POST'])
def add_bonus(pid):
    d   = request.json
    db  = get_db()
    # seen=0 → triggers real-time notification on player device; seen=1 → silent
    # notify:true (default) means seen=0 so the player gets a pop-up
    seen_val = 0 if d.get('notify', True) else 1
    cur = db.execute(
        'INSERT INTO bonuses(player_id,label,amount,seen) VALUES(?,?,?,?)',
        (pid, d.get('label','Bonus'), float(d.get('amount', 0)), seen_val)
    )
    db.commit()
    return jsonify(dict(db.execute('SELECT * FROM bonuses WHERE id=?', (cur.lastrowid,)).fetchone())), 201

@app.route('/api/bonuses/<int:bid>', methods=['DELETE'])
def delete_bonus(bid):
    db = get_db()
    db.execute('DELETE FROM bonuses WHERE id=?', (bid,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/bonuses/<int:bid>/seen', methods=['POST'])
def mark_bonus_seen(bid):
    db = get_db()
    db.execute('UPDATE bonuses SET seen=1 WHERE id=?', (bid,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/bonuses/<int:bid>/claim', methods=['POST'])
def claim_bonus(bid):
    db = get_db()
    bonus = db.execute('SELECT * FROM bonuses WHERE id=?', (bid,)).fetchone()
    if not bonus:
        return jsonify({'error': 'Bonusta ei löydy.'}), 404
    if bonus['claimed']:
        return jsonify({'error': 'Bonus on jo lunastettu.', 'already_claimed': True}), 400
    db.execute('UPDATE bonuses SET claimed=1, seen=1 WHERE id=?', (bid,))
    db.commit()
    claimed = dict(db.execute('SELECT * FROM bonuses WHERE id=?', (bid,)).fetchone())
    return jsonify({'ok': True, 'bonus': claimed, 'amount': claimed['amount'], 'label': claimed['label']})

# ─── Dashboard API ───────────────────────────────────────────────────────────

@app.route('/api/dashboard')
def dashboard():
    db = get_db()
    house_rev = db.execute('SELECT COALESCE(SUM(-amount),0) FROM transactions WHERE amount<0').fetchone()[0]
    paid_out  = db.execute('SELECT COALESCE(SUM(amount),0)  FROM transactions WHERE amount>0').fetchone()[0]
    n_players = db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
    n_tx      = db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]

    top_losers = [dict(r) for r in db.execute('''
        SELECT p.id,p.name,p.vip_level,COALESCE(SUM(t.amount),0) AS net
        FROM players p LEFT JOIN transactions t ON t.player_id=p.id
        GROUP BY p.id HAVING net<0 ORDER BY net ASC LIMIT 5
    ''').fetchall()]

    top_winners = [dict(r) for r in db.execute('''
        SELECT p.id,p.name,p.vip_level,COALESCE(SUM(t.amount),0) AS net
        FROM players p LEFT JOIN transactions t ON t.player_id=p.id
        GROUP BY p.id HAVING net>0 ORDER BY net DESC LIMIT 5
    ''').fetchall()]

    recent = [dict(r) for r in db.execute('''
        SELECT t.*, p.name AS player_name, p.vip_level
        FROM transactions t JOIN players p ON p.id=t.player_id
        ORDER BY t.created_at DESC LIMIT 12
    ''').fetchall()]

    by_game = [dict(r) for r in db.execute('''
        SELECT game_type, COUNT(*) AS cnt,
               COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) AS house_take
        FROM transactions GROUP BY game_type ORDER BY house_take DESC
    ''').fetchall()]

    return jsonify({
        'house_revenue': house_rev, 'total_payouts': paid_out,
        'net_house': house_rev - paid_out, 'total_players': n_players,
        'total_transactions': n_tx, 'top_losers': top_losers,
        'top_winners': top_winners, 'recent_transactions': recent, 'by_game': by_game,
    })

# ─── Customer API ─────────────────────────────────────────────────────────────

@app.route('/api/customer/login', methods=['POST'])
def customer_login():
    data     = request.json or {}
    name     = (data.get('name') or '').strip()
    password = (data.get('password') or '').strip()
    if not name:
        return jsonify({'error': 'Syötä käyttäjänimi.'}), 400
    db = get_db()
    player = db.execute('SELECT * FROM players WHERE LOWER(name)=LOWER(?)', (name,)).fetchone()
    if not player:
        return jsonify({'error': 'Käyttäjää ei löydy. Pyydä kassohenkilökuntaa rekisteröimään sinut.'}), 404
    p = dict(player)
    # Password check: if player has password set, verify it
    if p.get('password_hash'):
        if not password:
            return jsonify({'error': 'Tili vaatii salasanan.', 'needs_password': True}), 401
        if _hash_pw(password) != p['password_hash']:
            return jsonify({'error': 'Väärä salasana.'}), 401
    # Return player data (no password hash)
    p.pop('password_hash', None)
    p['has_password'] = bool(player['password_hash'])
    p['spins_remaining'] = p.get('spins_remaining') or 0
    p['points']          = p.get('points') or 0
    bonuses = [dict(r) for r in db.execute(
        'SELECT * FROM bonuses WHERE player_id=? ORDER BY created_at DESC', (p['id'],)
    ).fetchall()]
    p['bonuses'] = bonuses
    return jsonify(p)

# ─── Poker API ───────────────────────────────────────────────────────────────

@app.route('/api/poker/state')
def poker_state():
    db   = get_db()
    sess = current_session(db)
    if not sess:
        return jsonify({'status': 'none'})
    sess['community_cards']  = json.loads(sess['community_cards_json'])
    sess['preset_hands']     = json.loads(sess.get('preset_hands_json') or '{}')
    seats = [dict(r) for r in db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? ORDER BY seat_number', (sess['id'],)
    ).fetchall()]
    for s in seats:
        s['hole_cards'] = json.loads(s['hole_cards_json'])
    sess['seats'] = seats
    return jsonify(sess)

@app.route('/api/poker/new', methods=['POST'])
def poker_new():
    db  = get_db()
    cur = db.execute(
        'INSERT INTO poker_sessions(status,deck_json,community_cards_json,stage,preset_hands_json) VALUES(?,?,?,?,?)',
        ('waiting', json.dumps(new_deck()), '[]', 'waiting', '{}')
    )
    db.commit()
    return jsonify({'id': cur.lastrowid, 'status': 'waiting'})

@app.route('/api/poker/join', methods=['POST'])
def poker_join_api():
    d    = request.json
    db   = get_db()
    sess = current_session(db)
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Nimi vaaditaan.'}), 400
    if not sess:
        return jsonify({'error': 'Ei avoimia pelejä — pyydä jakajaa aloittamaan peli.'}), 400
    # Always allow rejoining an existing active seat — even if the game is already running.
    # This handles re-login on a new device or after clearing the browser.
    existing = db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? AND player_name=? AND active=1',
        (sess['id'], name)
    ).fetchone()
    if existing:
        return jsonify({'token': existing['join_token'], 'seat': existing['seat_number'], 'name': name})
    # New seats can only be added when the game is in waiting state
    if sess['status'] != 'waiting':
        return jsonify({'error': 'Peli on jo käynnissä — odotetaan seuraavaa kierrosta.'}), 400
    count = db.execute(
        'SELECT COUNT(*) FROM poker_seats WHERE session_id=? AND active=1', (sess['id'],)
    ).fetchone()[0]
    if count >= 9:
        return jsonify({'error': 'Pöytä täynnä (max 9 pelaajaa).'}), 400
    token     = gen_token()
    player_id = d.get('player_id')
    db.execute(
        'INSERT INTO poker_seats(session_id,player_name,player_id,join_token,seat_number) VALUES(?,?,?,?,?)',
        (sess['id'], name, player_id, token, count + 1)
    )
    db.commit()
    return jsonify({'token': token, 'seat': count + 1, 'name': name})

# ─── Poker hand log helpers ──────────────────────────────────────────────────
# Every hand started with `/api/poker/deal` is logged. The log captures
# hole cards at deal-time, community cards as they are revealed, the
# stage reached, the final winners (on showdown), and the way the hand
# ended (showdown / void / abandoned by a re-deal before completion).

def _hand_log_for_session(db, session_id):
    """Return the current in-progress log row for a session, or None."""
    row = db.execute(
        "SELECT * FROM poker_hand_log "
        "WHERE session_id=? AND ended_by='in_progress' "
        "ORDER BY id DESC LIMIT 1",
        (session_id,)
    ).fetchone()
    return dict(row) if row else None

def _seats_snapshot(db, session_id):
    """Snapshot of every active seat at the table right now."""
    rows = db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? AND active=1 ORDER BY seat_number',
        (session_id,)
    ).fetchall()
    return [{
        'seat_number': r['seat_number'],
        'player_name': r['player_name'],
        'player_id':   r['player_id'],
        'hole_cards':  json.loads(r['hole_cards_json'] or '[]'),
        'folded':      bool(r['folded']),
        'show_cards':  bool(r['show_cards']),
    } for r in rows]

def _log_hand_start(db, session_id):
    """Log a freshly dealt hand. Any previous in-progress hand on this session
    is closed off as 'abandoned'."""
    prev = _hand_log_for_session(db, session_id)
    if prev:
        db.execute(
            'UPDATE poker_hand_log SET ended_by=?, ended_at=? WHERE id=?',
            ('abandoned', _now(), prev['id'])
        )
    nxt = (db.execute(
        'SELECT MAX(hand_number) AS m FROM poker_hand_log WHERE session_id=?',
        (session_id,)
    ).fetchone()['m'] or 0) + 1
    db.execute(
        'INSERT INTO poker_hand_log'
        '(session_id,hand_number,started_at,stage_reached,ended_by,community_cards,seats,winners)'
        ' VALUES(?,?,?,?,?,?,?,?)',
        (session_id, nxt, _now(), 'preflop', 'in_progress',
         '[]', json.dumps(_seats_snapshot(db, session_id)), '[]')
    )

def _log_hand_advance(db, session_id, stage, community):
    log = _hand_log_for_session(db, session_id)
    if not log:
        return
    db.execute(
        'UPDATE poker_hand_log SET stage_reached=?, community_cards=? WHERE id=?',
        (stage, json.dumps(community), log['id'])
    )

def _log_hand_winners(db, session_id, winners, stage):
    log = _hand_log_for_session(db, session_id)
    if not log:
        return
    if stage == 'showdown':
        db.execute(
            'UPDATE poker_hand_log SET winners=?, seats=?, ended_by=?, ended_at=? WHERE id=?',
            (json.dumps(winners), json.dumps(_seats_snapshot(db, session_id)),
             'showdown', _now(), log['id'])
        )
    else:
        db.execute(
            'UPDATE poker_hand_log SET winners=? WHERE id=?',
            (json.dumps(winners), log['id'])
        )

def _log_hand_void(db, session_id):
    log = _hand_log_for_session(db, session_id)
    if not log:
        return
    db.execute(
        'UPDATE poker_hand_log SET ended_by=?, ended_at=?, seats=? WHERE id=?',
        ('void', _now(), json.dumps(_seats_snapshot(db, session_id)), log['id'])
    )

@app.route('/api/poker/hands')
def poker_hands_log():
    db     = get_db()
    limit  = max(1, min(int(request.args.get('limit', 25)), 200))
    offset = max(0, int(request.args.get('offset', 0)))
    sid    = request.args.get('session_id')
    where, params = '', []
    if sid and sid not in ('', 'all'):
        try:
            where = ' WHERE session_id=?'
            params.append(int(sid))
        except (TypeError, ValueError):
            pass
    rows = db.execute(
        f'SELECT * FROM poker_hand_log{where} ORDER BY id DESC LIMIT ? OFFSET ?',
        (*params, limit, offset)
    ).fetchall()
    total = db.execute(
        f'SELECT COUNT(*) AS c FROM poker_hand_log{where}', params
    ).fetchone()['c']
    sessions_meta = [{'session_id': r['session_id'], 'last': r['s'], 'count': r['n']}
                     for r in db.execute(
        'SELECT session_id, MAX(started_at) AS s, COUNT(*) AS n '
        'FROM poker_hand_log GROUP BY session_id ORDER BY s DESC'
    ).fetchall()]
    out = [_hand_log_row_to_dict(r) for r in rows]
    return jsonify({
        'hands':    out,
        'total':    total,
        'limit':    limit,
        'offset':   offset,
        'sessions': sessions_meta,
    })

@app.route('/api/poker/hands.csv')
def poker_hands_csv():
    """Download the hand log as CSV (Excel-friendly UTF-8 BOM, ; delimiter)."""
    db  = get_db()
    sid = request.args.get('session_id')
    where, params = '', []
    if sid and sid not in ('', 'all'):
        try:
            where = ' WHERE session_id=?'
            params.append(int(sid))
        except (TypeError, ValueError):
            pass
    rows = db.execute(
        f'SELECT * FROM poker_hand_log{where} ORDER BY id DESC',
        params
    ).fetchall()
    hands = [_hand_log_row_to_dict(r) for r in rows]
    return _hand_log_csv_response(hands, sid)


def _hand_log_row_to_dict(r):
    return {
        'id':              r['id'],
        'session_id':      r['session_id'],
        'hand_number':     r['hand_number'],
        'started_at':      r['started_at'],
        'ended_at':        r['ended_at'],
        'stage_reached':   r['stage_reached'],
        'ended_by':        r['ended_by'],
        'community_cards': json.loads(r['community_cards'] or '[]'),
        'seats':           json.loads(r['seats'] or '[]'),
        'winners':         json.loads(r['winners'] or '[]'),
    }


def _fmt_cards(cards):
    """Render a list of {rank,suit} dicts as 'A♠ K♥ 7♦' (plain text)."""
    return ' '.join(f"{c.get('rank','?')}{c.get('suit','?')}" for c in (cards or []))


def _hand_log_csv_response(hands, session_id_filter=None):
    """Return a Flask Response streaming the hand log as CSV.

    Excel-friendly: UTF-8 with BOM, semicolon delimiter (Finnish locale convention).
    """
    import csv, io
    from flask import Response
    buf = io.StringIO()
    buf.write('\ufeff')  # BOM so Excel detects UTF-8
    w = csv.writer(buf, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    w.writerow([
        'Käsi #', 'Istunto', 'Aloitettu', 'Päättynyt',
        'Vaihe', 'Päättymistapa', 'Yhteiskortit',
        'Pelaajat', 'Voittajat',
    ])
    for h in hands:
        seats = '; '.join(
            f"#{s.get('seat_number')} {s.get('player_name','')}: {_fmt_cards(s.get('hole_cards'))}"
            + (' (fold)' if s.get('folded') else '')
            for s in (h.get('seats') or [])
        )
        winners = ', '.join(
            f"{w.get('player_name','')} ({w.get('hand_name','')})"
            for w in (h.get('winners') or []) if w.get('is_winner')
        )
        w_endmap = {'showdown':'Showdown','void':'Mitätöity',
                    'abandoned':'Hylätty','in_progress':'Käynnissä'}
        w.writerow([
            h.get('hand_number'),
            h.get('session_id'),
            h.get('started_at') or '',
            h.get('ended_at') or '',
            h.get('stage_reached') or '',
            w_endmap.get(h.get('ended_by'), h.get('ended_by') or ''),
            _fmt_cards(h.get('community_cards')),
            seats,
            winners,
        ])
    fname = 'kasihistoria'
    if session_id_filter and session_id_filter not in ('', 'all'):
        fname += f'_istunto-{session_id_filter}'
    fname += '.csv'
    return Response(
        buf.getvalue(),
        content_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{fname}"',
                 'Cache-Control': 'no-store'}
    )

@app.route('/api/poker/deal', methods=['POST'])
def poker_deal():
    db    = get_db()
    sess  = current_session(db)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    deck    = json.loads(sess['deck_json'])
    presets = json.loads(sess.get('preset_hands_json') or '{}')
    seats   = db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? AND active=1 ORDER BY seat_number', (sess['id'],)
    ).fetchall()
    if not seats:
        return jsonify({'error': 'Ei pelaajia pöydässä.'}), 400

    used = {(c['rank'], c['suit']) for cards in presets.values() for c in cards if isinstance(cards, list)}
    deck = [c for c in deck if (c['rank'], c['suit']) not in used]
    if len(deck) < len(seats) * 2 + 5:
        full = new_deck()
        deck = [c for c in full if (c['rank'], c['suit']) not in used]

    for seat in seats:
        sid = str(seat['id'])
        if sid in presets and len(presets[sid]) == 2:
            cards = presets[sid]
        else:
            cards = [deck.pop(), deck.pop()]
        db.execute('UPDATE poker_seats SET hole_cards_json=?,folded=0 WHERE id=?',
                   (json.dumps(cards), seat['id']))

    # Preserve community presets across deal; clear only player hole-card presets
    comm_preset = presets.get('community', [])
    new_presets = {'community': comm_preset} if comm_preset else {}
    db.execute(
        'UPDATE poker_sessions SET deck_json=?,stage=?,status=?,community_cards_json=?,preset_hands_json=? WHERE id=?',
        (json.dumps(deck), 'preflop', 'active', '[]', json.dumps(new_presets), sess['id'])
    )
    db.commit()
    # Snapshot the new hand AFTER hole cards have been written so the log
    # captures everyone's actual starting hand.
    _log_hand_start(db, sess['id'])
    db.commit()
    return jsonify({'ok': True, 'stage': 'preflop'})

@app.route('/api/poker/preset', methods=['POST'])
def poker_preset():
    d    = request.json or {}
    db   = get_db()
    sess = current_session(db)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    db.execute('UPDATE poker_sessions SET preset_hands_json=? WHERE id=?',
               (json.dumps(d), sess['id']))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/poker/advance', methods=['POST'])
def poker_advance():
    db   = get_db()
    sess = current_session(db)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    deck      = json.loads(sess['deck_json'])
    community = json.loads(sess['community_cards_json'])
    stage     = sess['stage']
    comm_pre  = json.loads(sess.get('preset_hands_json') or '{}').get('community', [])
    def _cc(idx):
        """Return preset community card at index, or pop from deck."""
        if idx < len(comm_pre) and comm_pre[idx]:
            return comm_pre[idx]
        return deck.pop()
    if stage == 'preflop':
        community = [_cc(0), _cc(1), _cc(2)]; new_stage = 'flop'
    elif stage == 'flop':
        community.append(_cc(3)); new_stage = 'turn'
    elif stage == 'turn':
        community.append(_cc(4)); new_stage = 'river'
    elif stage == 'river':
        new_stage = 'showdown'
    else:
        return jsonify({'error': 'Ei voida edetä tästä vaiheesta.'}), 400
    db.execute(
        'UPDATE poker_sessions SET deck_json=?,stage=?,community_cards_json=? WHERE id=?',
        (json.dumps(deck), new_stage, json.dumps(community), sess['id'])
    )
    _log_hand_advance(db, sess['id'], new_stage, community)
    db.commit()
    return jsonify({'ok': True, 'stage': new_stage, 'community_cards': community})

@app.route('/api/poker/void', methods=['POST'])
def poker_void():
    db   = get_db()
    sess = current_session(db)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    # Snapshot the hand BEFORE we wipe seat hole cards — otherwise the log
    # would record empty hands.
    _log_hand_void(db, sess['id'])
    db.execute(
        'UPDATE poker_sessions SET deck_json=?,stage=?,community_cards_json=?,status=?,preset_hands_json=? WHERE id=?',
        (json.dumps(new_deck()), 'waiting', '[]', 'waiting', '{}', sess['id'])
    )
    db.execute('UPDATE poker_seats SET hole_cards_json=?,folded=0,show_cards=0 WHERE session_id=? AND active=1',
               ('[]', sess['id']))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/poker/fold/<int:seat_id>', methods=['POST'])
def poker_fold(seat_id):
    db = get_db()
    db.execute('UPDATE poker_seats SET folded=1 WHERE id=?', (seat_id,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/poker/remove/<int:seat_id>', methods=['DELETE'])
def poker_remove(seat_id):
    db = get_db()
    db.execute('UPDATE poker_seats SET active=0 WHERE id=?', (seat_id,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/poker/player/<token>/showcards', methods=['POST'])
def toggle_show_cards(token):
    db   = get_db()
    seat = db.execute('SELECT * FROM poker_seats WHERE join_token=?', (token,)).fetchone()
    if not seat:
        return jsonify({'error': 'Virheellinen tunnus.'}), 404
    new_val = 0 if seat['show_cards'] else 1
    db.execute('UPDATE poker_seats SET show_cards=? WHERE join_token=?', (new_val, token))
    db.commit()
    return jsonify({'show_cards': bool(new_val)})

@app.route('/api/poker/player/<token>')
def poker_player_state(token):
    db   = get_db()
    seat = db.execute('SELECT * FROM poker_seats WHERE join_token=?', (token,)).fetchone()
    if not seat:
        return jsonify({'error': 'Virheellinen tunnus.'}), 404
    seat = dict(seat)
    sess = dict(db.execute('SELECT * FROM poker_sessions WHERE id=?', (seat['session_id'],)).fetchone())
    n_active = db.execute(
        'SELECT COUNT(*) FROM poker_seats WHERE session_id=? AND active=1', (sess['id'],)
    ).fetchone()[0]
    return jsonify({
        'name':            seat['player_name'],
        'seat':            seat['seat_number'],
        'hole_cards':      json.loads(seat['hole_cards_json']),
        'folded':          bool(seat['folded']),
        'active':          bool(seat['active']),
        'show_cards':      bool(seat['show_cards']),
        'stage':           sess['stage'],
        'community_cards': json.loads(sess['community_cards_json']),
        'status':          sess['status'],
        'n_players':       n_active,
    })

@app.route('/api/poker/evaluate')
def poker_evaluate():
    db        = get_db()
    sess      = current_session(db)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    community = json.loads(sess['community_cards_json'])
    if len(community) < 3:
        return jsonify({'error': 'Tarvitaan vähintään flop arviointia varten.'}), 400
    seats = [dict(r) for r in db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? AND active=1 AND folded=0 ORDER BY seat_number',
        (sess['id'],)
    ).fetchall()]
    results = []
    for s in seats:
        hole = json.loads(s['hole_cards_json'])
        if len(hole) != 2:
            continue
        rank, tb = best_hand(hole, community)
        results.append({
            'seat_id':     s['id'],
            'seat_number': s['seat_number'],
            'player_name': s['player_name'],
            'hole_cards':  hole,
            'hand_rank':   rank,
            'hand_name':   HAND_NAMES[rank],
            'tiebreakers': tb,
        })
    results.sort(key=lambda r: (r['hand_rank'], r['tiebreakers']), reverse=True)
    if results:
        top = results[0]
        for r in results:
            r['is_winner'] = (r['hand_rank'] == top['hand_rank']
                              and r['tiebreakers'] == top['tiebreakers'])
    _log_hand_winners(db, sess['id'], results, sess['stage'])
    db.commit()
    return jsonify(results)

@app.route('/api/poker/spin', methods=['POST'])
def poker_spin():
    d         = request.json or {}
    player_id = d.get('player_id')
    if not player_id:
        return jsonify({'error': 'Kirjaudu sisään pyöräyttääksesi.'}), 401
    db = get_db()
    row = db.execute('SELECT * FROM players WHERE id=?', (player_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    remaining = row['spins_remaining'] or 0
    if remaining <= 0:
        return jsonify({'error': 'Sinulla ei ole pyöräytyksiä. Pyydä kassohenkilökunnalta.',
                        'spins_remaining': 0}), 403
    # Atomic decrement — only succeeds if remaining > 0
    cur = db.execute(
        'UPDATE players SET spins_remaining = spins_remaining - 1 '
        'WHERE id=? AND spins_remaining > 0', (player_id,)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'error': 'Ei pyöräytyksiä jäljellä.', 'spins_remaining': 0}), 403
    new_remaining = (db.execute('SELECT spins_remaining FROM players WHERE id=?',
                                (player_id,)).fetchone()['spins_remaining']) or 0

    total = sum(p['weight'] for p in SPIN_PRIZES)
    r     = random.uniform(0, total)
    cum   = 0
    idx, prize = 0, SPIN_PRIZES[0]
    for i, pr in enumerate(SPIN_PRIZES):
        cum += pr['weight']
        if r <= cum:
            idx, prize = i, pr
            break

    # Auto-create the bonus so the player can redeem the prize from the bonuses panel.
    label = f"Pyöräytys: {prize['label']}"
    db.execute(
        'INSERT INTO bonuses(player_id,label,amount,seen) VALUES(?,?,?,?)',
        (player_id, label, float(prize['bonus']), 0)
    )
    db.commit()

    return jsonify({'prize': prize, 'index': idx, 'spins_remaining': new_remaining})

# ─── Points system ───────────────────────────────────────────────────────────
# Prize catalog — redemption costs are in points.
PRIZES_CATALOG = [
    {'id': 'cash5',   'cost':  500, 'label': '€5 kassabonus',                'kind': 'cash', 'amount':  5},
    {'id': 'cash10', 'cost': 1000, 'label': '€10 kassabonus',               'kind': 'cash', 'amount': 10},
    {'id': 'cash25', 'cost': 2250, 'label': '€25 kassabonus',               'kind': 'cash', 'amount': 25},
    {'id': 'cash50', 'cost': 4000, 'label': '€50 kassabonus',               'kind': 'cash', 'amount': 50},
    {'id': 'spin1',  'cost':  300, 'label': '1 onnenpyörän pyöräytys',      'kind': 'spin', 'spins': 1},
    {'id': 'spin5',  'cost': 1200, 'label': '5 onnenpyörän pyöräytystä',    'kind': 'spin', 'spins': 5},
]
PRIZE_BY_ID = {p['id']: p for p in PRIZES_CATALOG}

# Mini-game constraints
MIN_BET, MAX_BET = 10, 10000

# Pikapokeri (Jacks-or-Better video poker) payout multipliers
PIKAPOKERI_PAYOUTS = {9: 800, 8: 50, 7: 25, 6: 9, 5: 6, 4: 4, 3: 3, 2: 2, 1: 1}
PIKAPOKERI_NAMES   = {
    9: 'Royal Flush', 8: 'Värisuora', 7: 'Nelikko',
    6: 'Full House',  5: 'Väri',      4: 'Suora',
    3: 'Kolmikko',    2: 'Kaksi paria', 1: 'Pari (J tai parempi)', -1: 'Häviö',
}

# Default system settings
SETTINGS_DEFAULTS = {
    'points_per_eur':   '10',   # 100 pts = €10
    'min_redeem_pts':   '500',
    'max_redeem_pts':   '5000',
    'point_expiry_days':'365',
}

# Slot machine themes — 5 reels × 3 rows, tiered payouts (3/4/5-of-a-kind),
# wild substitution, free spins on 3+ scatters, progressive jackpot on 5 wilds.
SLOT_THEMES = {
    'fruits': {
        'symbols': [
            {'id':'cherry',  'weight':22},
            {'id':'lemon',   'weight':19},
            {'id':'orange',  'weight':16},
            {'id':'grape',   'weight':13},
            {'id':'bell',    'weight':9},
            {'id':'star',    'weight':6},
            {'id':'jackpot', 'weight':3},
            {'id':'diamond', 'weight':2},
            {'id':'wild',    'weight':3},   # 🃏 substitutes for any non-scatter
            {'id':'scatter', 'weight':5},   # 💰 free-spin trigger
        ],
        # RTP-calibrated payouts (3oak / 4oak / 5oak per line) — base RTP ≈ 85 %
        # over 200 k Monte-Carlo spins (incl. 1 % progressive-jackpot rake).
        # Values are per-line multipliers; payouts sum across all 20 lines.
        'payouts': {
            'cherry':  [0.19, 0.48,  1.45],
            'lemon':   [0.29, 0.73,  2.18],
            'orange':  [0.39, 0.97,  3.38],
            'grape':   [0.58, 1.74,  6.29],
            'bell':    [0.87, 2.61, 10.64],
            'star':    [1.74, 5.32, 21.27],
            'jackpot': [3.38,13.05, 43.52],
            'diamond': [6.53,26.11, 87.03],
            'wild':    [0.87, 4.35, 17.41],
        },
        'free_spins': 8, 'fs_mult': 2,
    },
    'egypt': {
        'symbols': [
            {'id':'scarab',  'weight':22},
            {'id':'eye',     'weight':18},
            {'id':'jar',     'weight':15},
            {'id':'eagle',   'weight':11},
            {'id':'cat',     'weight':9},
            {'id':'pharaoh', 'weight':6},
            {'id':'book',    'weight':4},
            {'id':'wild',    'weight':3},   # 𓂀 sphinx
            {'id':'scatter', 'weight':5},   # 🔮 Ra
        ],
        'payouts': {
            'scarab':  [0.12, 0.30,  0.85],
            'eye':     [0.18, 0.45,  1.40],
            'jar':     [0.30, 0.85,  2.90],
            'eagle':   [0.45, 1.45,  5.20],
            'cat':     [0.85, 2.30, 10.00],
            'pharaoh': [2.30, 7.20, 23.00],
            'book':    [4.65,14.50, 46.00],
            'wild':    [0.60, 2.90, 11.50],
        },
        'free_spins': 10, 'fs_mult': 2,
    },
    'space': {
        'symbols': [
            {'id':'planet', 'weight':22},
            {'id':'comet',  'weight':18},
            {'id':'alien',  'weight':15},
            {'id':'rocket', 'weight':11},
            {'id':'stars',  'weight':9},
            {'id':'moon',   'weight':6},
            {'id':'gem',    'weight':4},
            {'id':'wild',   'weight':3},    # 👽 substitutes
            {'id':'scatter','weight':5},    # 🌀 wormhole
        ],
        'payouts': {
            'planet': [0.10, 0.26,  0.82],
            'comet':  [0.15, 0.41,  1.34],
            'alien':  [0.26, 0.82,  2.72],
            'rocket': [0.41, 1.34,  4.93],
            'stars':  [0.82, 2.16,  9.55],
            'moon':   [2.16, 6.83, 21.88],
            'gem':    [4.36,13.66, 43.65],
            'wild':   [0.51, 2.72, 10.94],
        },
        'free_spins': 8, 'fs_mult': 3,  # space pays 3× during free spins
    },
}

# 20 paylines for a 5×3 grid. Each line evaluates left-to-right starting at
# the leftmost reel (col 0); wild substitutes for the target symbol.
SLOT_PAYLINES = [
    [(0,1),(1,1),(2,1),(3,1),(4,1)],  # 1  middle row
    [(0,0),(1,0),(2,0),(3,0),(4,0)],  # 2  top row
    [(0,2),(1,2),(2,2),(3,2),(4,2)],  # 3  bottom row
    [(0,0),(1,1),(2,2),(3,1),(4,0)],  # 4  V
    [(0,2),(1,1),(2,0),(3,1),(4,2)],  # 5  ^
    [(0,0),(1,0),(2,1),(3,0),(4,0)],  # 6  top-dip
    [(0,2),(1,2),(2,1),(3,2),(4,2)],  # 7  bottom-bump
    [(0,1),(1,0),(2,0),(3,0),(4,1)],  # 8  hat
    [(0,1),(1,2),(2,2),(3,2),(4,1)],  # 9  bowl
    [(0,0),(1,1),(2,1),(3,1),(4,0)],  # 10 bridge top
    [(0,2),(1,1),(2,1),(3,1),(4,2)],  # 11 bridge bottom
    [(0,1),(1,0),(2,1),(3,2),(4,1)],  # 12 wave down
    [(0,1),(1,2),(2,1),(3,0),(4,1)],  # 13 wave up
    [(0,0),(1,2),(2,0),(3,2),(4,0)],  # 14 zigzag top
    [(0,2),(1,0),(2,2),(3,0),(4,2)],  # 15 zigzag bot
    [(0,1),(1,1),(2,0),(3,1),(4,1)],  # 16 spike up
    [(0,1),(1,1),(2,2),(3,1),(4,1)],  # 17 spike down
    [(0,0),(1,0),(2,2),(3,0),(4,0)],  # 18 drop top
    [(0,2),(1,2),(2,0),(3,2),(4,2)],  # 19 drop bot
    [(0,0),(1,2),(2,2),(3,2),(4,0)],  # 20 long arc
]

# Progressive jackpot — 1 % of every bet feeds the pool. 5 wilds across the
# middle row (payline 1) awards the entire pool to the player and resets.
JACKPOT_INITIAL  = 5000
JACKPOT_RAKE_PCT = 0.01
JACKPOT_KEY      = 'jackpot_pool'

def _slot_spin(theme_id, include_scatter=True, include_wild=True):
    """Generate a 5×3 grid: grid[col][row] for col 0..4, row 0..2."""
    theme = SLOT_THEMES.get(theme_id, SLOT_THEMES['fruits'])
    syms  = theme['symbols']
    if not include_scatter:
        syms = [s for s in syms if s['id'] != 'scatter']
    if not include_wild:
        syms = [s for s in syms if s['id'] != 'wild']
    ids     = [s['id'] for s in syms]
    weights = [s['weight'] for s in syms]
    return [[random.choices(ids, weights=weights)[0] for _ in range(3)] for _ in range(5)]


def _slot_eval_line(cells):
    """Longest left-aligned match. wild substitutes; scatter breaks the line."""
    if not cells or cells[0] == 'scatter':
        return None, 0
    target = None
    length = 0
    for c in cells:
        if c == 'scatter':
            break
        if c == 'wild':
            length += 1
            continue
        if target is None:
            target = c
            length += 1
        elif c == target:
            length += 1
        else:
            break
    if target is None:
        target = 'wild'  # all-wild prefix
    return target, length


def _slot_calc_wins(grid, payouts):
    wins = []
    for i, line in enumerate(SLOT_PAYLINES):
        cells = [grid[col][row] for col, row in line]
        sym, length = _slot_eval_line(cells)
        if sym is None or length < 3:
            continue
        table = payouts.get(sym, [0, 0, 0])
        idx   = length - 3
        mult  = table[idx] if 0 <= idx < len(table) else 0
        if mult <= 0:
            continue
        wins.append({
            'line':   i,
            'symbol': sym,
            'count':  length,
            'mult':   mult,
            'cells':  [[c, r] for (c, r) in line[:length]],
        })
    return wins


def _slot_scatter_positions(grid):
    return [[col, row] for col in range(5) for row in range(3) if grid[col][row] == 'scatter']


def _slot_is_jackpot(grid):
    """Progressive jackpot triggers when the entire middle payline is wild."""
    middle = SLOT_PAYLINES[0]
    return all(grid[c][r] == 'wild' for c, r in middle)


def _slot_clone_grid(grid):
    return [col[:] for col in grid]


def _slot_regular_symbols(theme_id):
    theme = SLOT_THEMES.get(theme_id, SLOT_THEMES['fruits'])
    return [s['id'] for s in theme['symbols'] if s['id'] not in ('wild', 'scatter')]


def _slot_symbol_positions(grid, symbol):
    return [[col, row] for col in range(5) for row in range(3) if grid[col][row] == symbol]


def _slot_expand_symbol_reels(grid, symbol):
    expanded = []
    for col in range(5):
        if any(grid[col][row] == symbol for row in range(3)):
            expanded.append(col)
            for row in range(3):
                grid[col][row] = symbol
    return expanded


def _slot_run_free_spins(theme_id, bet, theme):
    """Theme-specific free-spins inspired by real slot bonus mechanics.

    - fruits: Fire Joker-style win multiplier ladder.
    - egypt: Amu/Ra-style chosen expanding symbol.
    - space: Starburst-style expanding wilds + respins.
    """
    fs_count = theme['free_spins']
    fs_mult = theme['fs_mult']
    fs_results = []
    bonus_payout = 0
    feature = {'theme': theme_id, 'name': 'Classic free spins'}

    if theme_id == 'egypt':
        payout_scale = 0.325
        expanding_symbol = random.choice(_slot_regular_symbols(theme_id))
        feature = {
            'theme': theme_id,
            'type': 'expanding_symbol',
            'name': 'Ra Expanding Symbol',
            'description': 'A chosen symbol expands to full reels during free spins.',
            'expanding_symbol': expanding_symbol,
        }
        for spin_idx in range(fs_count):
            fg = _slot_spin(theme_id, include_scatter=False)
            expanded_reels = _slot_expand_symbol_reels(fg, expanding_symbol)
            fw = _slot_calc_wins(fg, theme['payouts'])
            fm = sum(w['mult'] for w in fw)
            fp = int(round(bet * fm * fs_mult * payout_scale))
            bonus_payout += fp
            fs_results.append({
                'grid': fg, 'wins': fw, 'payout': fp,
                'feature': {
                    'type': 'expanding_symbol',
                    'expanding_symbol': expanding_symbol,
                    'expanded_reels': expanded_reels,
                    'spin_index': spin_idx + 1,
                    'display_mult': fs_mult,
                    'payout_scale': payout_scale,
                }
            })
        return fs_results, bonus_payout, feature

    if theme_id == 'space':
        payout_mult = 0.595
        feature = {
            'theme': theme_id,
            'type': 'expanding_wild_respins',
            'name': 'Nova Expanding Wilds',
            'description': 'Wilds expand to full reels and award respins when new reels ignite.',
        }
        total_spins = fs_count
        spin_idx = 0
        while spin_idx < total_spins and spin_idx < fs_count + 7:
            spin_idx += 1
            fg = _slot_spin(theme_id, include_scatter=False)
            # Starburst-style: wilds expand on the current spin and award respins.
            new_wild_reels = []
            for col in range(5):
                if any(fg[col][row] == 'wild' for row in range(3)):
                    new_wild_reels.append(col)
                    for row in range(3):
                        fg[col][row] = 'wild'
            extra_spin_awarded = bool(new_wild_reels) and total_spins < fs_count + 7
            if extra_spin_awarded:
                total_spins += 1
            fw = _slot_calc_wins(fg, theme['payouts'])
            fm = sum(w['mult'] for w in fw)
            # Space is respin-driven, so use calibrated pay strength for ~85% RTP.
            fp = int(round(bet * fm * payout_mult))
            bonus_payout += fp
            fs_results.append({
                'grid': fg, 'wins': fw, 'payout': fp,
                'feature': {
                    'type': 'expanding_wild_respins',
                    'expanded_wild_reels': new_wild_reels,
                    'new_wild_reels': new_wild_reels,
                    'extra_spin_awarded': extra_spin_awarded,
                    'spin_index': spin_idx,
                    'total_spins': total_spins,
                    'display_mult': payout_mult,
                }
            })
        return fs_results, bonus_payout, feature

    # Fruits: Fire Joker-style rising win multiplier ladder.
    feature = {
        'theme': theme_id,
        'type': 'fire_joker_ladder',
        'name': 'Fire Joker Ladder',
        'description': 'Wins and jokers heat the multiplier ladder up to 5×.',
    }
    payout_scale = 0.495
    current_mult = fs_mult
    for spin_idx in range(fs_count):
        fg = _slot_spin(theme_id, include_scatter=False)
        fire_jokers = _slot_symbol_positions(fg, 'wild')
        fw = _slot_calc_wins(fg, theme['payouts'])
        fm = sum(w['mult'] for w in fw)
        fp = int(round(bet * fm * current_mult * payout_scale))
        bonus_payout += fp
        heat_up = bool(fw) or bool(fire_jokers)
        next_mult = min(5, current_mult + 1) if heat_up else current_mult
        fs_results.append({
            'grid': fg, 'wins': fw, 'payout': fp,
            'feature': {
                'type': 'fire_joker_ladder',
                'fire_joker_positions': fire_jokers,
                'bonus_multiplier': current_mult,
                'next_multiplier': next_mult,
                'heat_up': heat_up,
                'spin_index': spin_idx + 1,
                'display_mult': current_mult,
                'payout_scale': payout_scale,
            }
        })
        current_mult = next_mult
    return fs_results, bonus_payout, feature


def _set_setting(db, key, value):
    db.execute('INSERT OR REPLACE INTO system_settings(key,value) VALUES(?,?)',
               (key, str(value)))

def _get_streak_mode(db, pid):
    row = db.execute('SELECT streak_mode FROM players WHERE id=?', (pid,)).fetchone()
    if not row: return 'normal'
    return row['streak_mode'] or 'normal'

def _get_setting(db, key):
    row = db.execute('SELECT value FROM system_settings WHERE key=?', (key,)).fetchone()
    return row['value'] if row else SETTINGS_DEFAULTS.get(key, '')

def _pikapokeri_eval(cards):
    """Returns (rank, multiplier, name). rank=-1 = losing hand."""
    rank, tiebreakers = _eval5(cards)
    if rank == 0:
        return -1, 0, 'Häviö'
    if rank == 1:
        if tiebreakers[0] < 11:
            return -1, 0, 'Häviö'
        return 1, 1, 'Pari (J tai parempi)'
    mult = PIKAPOKERI_PAYOUTS.get(rank, 0)
    return rank, mult, PIKAPOKERI_NAMES.get(rank, 'Häviö')

def _log_points(db, pid, delta, reason):
    db.execute('INSERT INTO point_transactions(player_id,delta,reason) VALUES(?,?,?)',
               (pid, int(delta), reason))

def _atomic_deduct_points(db, pid, amount, reason):
    """Atomically deduct points. Returns new balance on success, None if insufficient."""
    row = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()
    if not row:
        return None
    if (row['points'] or 0) < amount:
        return None
    cur = db.execute(
        'UPDATE players SET points = points - ? WHERE id=? AND points >= ?',
        (amount, pid, amount)
    )
    if cur.rowcount == 0:
        return None
    _log_points(db, pid, -amount, reason)
    new_bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return new_bal

def _add_points(db, pid, amount, reason):
    db.execute('UPDATE players SET points = points + ? WHERE id=?', (amount, pid))
    _log_points(db, pid, amount, reason)
    return db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0

def _get_bet(req, player_id, db):
    """Extract + validate bet, return (bet, error_response_or_None)."""
    try:
        bet = int(req.get('bet', 0))
    except (TypeError, ValueError):
        return 0, (jsonify({'error': 'Virheellinen panos.'}), 400)
    if bet < MIN_BET or bet > MAX_BET:
        return 0, (jsonify({'error': f'Panoksen oltava {MIN_BET}–{MAX_BET} pistettä.'}), 400)
    row = db.execute('SELECT points FROM players WHERE id=?', (player_id,)).fetchone()
    if not row:
        return 0, (jsonify({'error': 'Pelaajaa ei löydy.'}), 404)
    if (row['points'] or 0) < bet:
        return 0, (jsonify({'error': 'Ei tarpeeksi pisteitä.'}), 400)
    return bet, None

# ─── Point admin endpoints ───────────────────────────────────────────────────

@app.route('/api/players/<int:pid>/points', methods=['GET'])
def get_points(pid):
    db  = get_db()
    row = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    history = [dict(r) for r in db.execute(
        'SELECT * FROM point_transactions WHERE player_id=? ORDER BY created_at DESC LIMIT 30', (pid,)
    ).fetchall()]
    return jsonify({'points': row['points'] or 0, 'history': history})

@app.route('/api/players/<int:pid>/points/grant', methods=['POST'])
def grant_points(pid):
    d = request.json or {}
    try:
        count = int(d.get('count', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen määrä.'}), 400
    if count == 0:
        return jsonify({'error': 'Määrä ei voi olla 0.'}), 400
    reason = (d.get('reason') or ('Kassan myöntö' if count > 0 else 'Kassan vähennys')).strip()[:120]
    db = get_db()
    row = db.execute('SELECT * FROM players WHERE id=?', (pid,)).fetchone()
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    current = row['points'] or 0
    new_val = max(0, current + count)
    actual_delta = new_val - current
    db.execute('UPDATE players SET points=? WHERE id=?', (new_val, pid))
    _log_points(db, pid, actual_delta, reason)
    _audit(db, 'grant_points', pid, row['name'], {'delta': actual_delta, 'reason': reason, 'points': new_val})
    db.commit()
    return jsonify({'ok': True, 'points': new_val, 'granted': actual_delta})

@app.route('/api/points/prizes')
def list_prizes():
    return jsonify(PRIZES_CATALOG)

@app.route('/api/players/<int:pid>/points/redeem', methods=['POST'])
def redeem_prize(pid):
    d  = request.json or {}
    pid_prize = d.get('prize_id')
    prize = PRIZE_BY_ID.get(pid_prize)
    if not prize:
        return jsonify({'error': 'Palkintoa ei löydy.'}), 404
    db = get_db()
    new_bal = _atomic_deduct_points(db, pid, prize['cost'], f"Lunastus: {prize['label']}")
    if new_bal is None:
        return jsonify({'error': 'Ei tarpeeksi pisteitä.'}), 400
    if prize['kind'] == 'cash':
        # Create a bonus the player can claim from the bonuses panel
        db.execute(
            'INSERT INTO bonuses(player_id,label,amount,seen) VALUES(?,?,?,?)',
            (pid, f"Pistelunastus: {prize['label']}", float(prize['amount']), 0)
        )
    elif prize['kind'] == 'spin':
        db.execute('UPDATE players SET spins_remaining = spins_remaining + ? WHERE id=?',
                   (int(prize['spins']), pid))
    db.commit()
    return jsonify({'ok': True, 'points': new_bal, 'prize': prize})

# ─── Mini-games ──────────────────────────────────────────────────────────────

def _card_value_bj(rank, soft_ace=True):
    if rank in ('J','Q','K'): return 10
    if rank == 'A':           return 11 if soft_ace else 1
    return int(rank)

def _hand_total(cards):
    total  = sum(_card_value_bj(c['rank']) for c in cards)
    aces   = sum(1 for c in cards if c['rank']=='A')
    while total > 21 and aces > 0:
        total -= 10
        aces  -= 1
    return total

def _hand_total_soft(cards):
    total = sum(_card_value_bj(c['rank']) for c in cards)
    aces = sum(1 for c in cards if c['rank'] == 'A')
    soft = False
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    if any(c['rank'] == 'A' for c in cards) and total <= 21:
        hard_total = sum(_card_value_bj(c['rank'], soft_ace=False) for c in cards)
        soft = hard_total + 10 == total
    return total, soft

def _is_blackjack(cards):
    return len(cards) == 2 and _hand_total(cards) == 21

def _dealer_should_hit_blackjack(cards):
    # Common casino rule used here: dealer stands on all 17s, including soft 17.
    total, _soft = _hand_total_soft(cards)
    return total < 17

def _baccarat_value(rank):
    if rank in ('J','Q','K','10'):
        return 0
    if rank == 'A':
        return 1
    return int(rank)

def _baccarat_total(hand):
    return sum(_baccarat_value(c['rank']) for c in hand) % 10

def _baccarat_deal_result(deck):
    p1, p2, b1, b2 = deck.pop(), deck.pop(), deck.pop(), deck.pop()
    phand, bhand = [p1, p2], [b1, b2]
    ptot, btot = _baccarat_total(phand), _baccarat_total(bhand)
    draw_events = []
    natural = ptot in (8, 9) or btot in (8, 9)
    if not natural:
        p3 = None
        if ptot <= 5:
            p3 = deck.pop(); phand.append(p3)
            draw_events.append({'side': 'player', 'card': p3, 'reason': 'Player total 0–5 draws third card'})
            ptot = _baccarat_total(phand)
        draw_banker = False
        reason = ''
        if p3 is None:
            draw_banker = btot <= 5
            reason = 'Banker draws on 0–5 when player stands'
        else:
            p3v = _baccarat_value(p3['rank'])
            if btot <= 2:
                draw_banker = True; reason = 'Banker total 0–2 always draws'
            elif btot == 3:
                draw_banker = p3v != 8; reason = 'Banker 3 draws unless player third card is 8'
            elif btot == 4:
                draw_banker = p3v in (2,3,4,5,6,7); reason = 'Banker 4 draws against 2–7'
            elif btot == 5:
                draw_banker = p3v in (4,5,6,7); reason = 'Banker 5 draws against 4–7'
            elif btot == 6:
                draw_banker = p3v in (6,7); reason = 'Banker 6 draws against 6–7'
        if draw_banker:
            b3 = deck.pop(); bhand.append(b3)
            draw_events.append({'side': 'banker', 'card': b3, 'reason': reason})
            btot = _baccarat_total(bhand)
    if ptot > btot:
        winner = 'player'
    elif btot > ptot:
        winner = 'banker'
    else:
        winner = 'tie'
    return {
        'player_hand': phand, 'banker_hand': bhand,
        'player_total': ptot, 'banker_total': btot,
        'winner': winner, 'natural': natural, 'draw_events': draw_events,
    }

def _settle_blackjack_round(deck, pcards, dcards, bet):
    while _dealer_should_hit_blackjack(dcards):
        dcards.append(deck.pop())
    ptot, dtot = _hand_total(pcards), _hand_total(dcards)
    if dtot > 21 or ptot > dtot:
        return 'done_win', 'win', bet * 2
    if ptot == dtot:
        return 'done_push', 'push', bet
    return 'done_loss', 'loss', 0

@app.route('/api/points/<int:pid>/coinflip', methods=['POST'])
def game_coinflip(pid):
    d      = request.json or {}
    choice = (d.get('choice') or '').lower()
    if choice not in ('heads','tails'):
        return jsonify({'error': 'Valitse klaava tai kruuna.'}), 400
    db = get_db()
    bet, err = _get_bet(d, pid, db)
    if err: return err
    _atomic_deduct_points(db, pid, bet, f'Kolikonheitto panos ({choice})')
    streak = _get_streak_mode(db, pid)
    if streak == 'win':
        result  = choice
        payout  = bet * 2
        _add_points(db, pid, payout, f'Kolikonheitto voitto ({result})')
        outcome = 'win'
    elif streak == 'lose':
        result  = 'tails' if choice == 'heads' else 'heads'
        outcome, payout = 'loss', 0
    else:
        # Casino coinflip: unbiased visible coin, 96% RTP via small house-edge void.
        result = 'heads' if random.random() < 0.50 else 'tails'
        if choice == result and random.random() < 0.96:
            payout  = bet * 2
            _add_points(db, pid, payout, f'Kolikonheitto voitto ({result})')
            outcome = 'win'
        else:
            outcome, payout = 'loss', 0
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({
        'outcome': outcome, 'result': result, 'choice': choice,
        'bet': bet, 'payout': payout, 'net': payout - bet, 'points': bal,
        'rules': {'name': 'Casino Coinflip', 'rtp_target': 0.96, 'payout': '1:1'},
    })

@app.route('/api/points/<int:pid>/war', methods=['POST'])
def game_war(pid):
    d = request.json or {}
    db = get_db()
    bet, err = _get_bet(d, pid, db)
    if err: return err
    _atomic_deduct_points(db, pid, bet, 'Sota-peli panos')
    deck = new_deck()
    pc, dc = deck.pop(), deck.pop()
    pv, dv = _RV[pc['rank']], _RV[dc['rank']]
    streak = _get_streak_mode(db, pid)
    tie_breaker = None
    if streak == 'win':
        outcome = 'win';  payout = bet * 2
        _add_points(db, pid, payout, 'Sota-peli voitto')
    elif streak == 'lose':
        outcome = 'loss'; payout = 0
    elif pv > dv:
        outcome = 'win';  payout = bet * 2
        _add_points(db, pid, payout, 'Sota-peli voitto')
    elif pv < dv:
        outcome = 'loss'; payout = 0
    else:
        # Casino War tie: automatically goes to war when player can cover the raise.
        # Ante pushes on war wins, raise pays even money; second tie pushes both bets.
        can_raise = (db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0) >= bet
        if can_raise:
            _atomic_deduct_points(db, pid, bet, 'Sota-peli war raise')
            burn_player = [deck.pop() for _ in range(3)]
            burn_dealer = [deck.pop() for _ in range(3)]
            pc2, dc2 = deck.pop(), deck.pop()
            pv2, dv2 = _RV[pc2['rank']], _RV[dc2['rank']]
            tie_breaker = {'player_card': pc2, 'dealer_card': dc2, 'burn_count_each': 3}
            if pv2 > dv2:
                outcome = 'war_win'; payout = bet * 3
                _add_points(db, pid, payout, 'Sota-peli war voitto')
            elif pv2 < dv2:
                outcome = 'war_loss'; payout = 0
            else:
                outcome = 'war_push'; payout = bet * 2
                _add_points(db, pid, payout, 'Sota-peli war tasapeli')
        else:
            outcome = 'surrender'; payout = bet // 2
            _add_points(db, pid, payout, 'Sota-peli surrender')
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({
        'outcome': outcome, 'player_card': pc, 'dealer_card': dc,
        'tie_breaker': tie_breaker,
        'bet': bet, 'payout': payout, 'net': payout - bet, 'points': bal,
        'rules': {'name': 'Casino War', 'tie': 'auto war with matching raise when possible'},
    })

@app.route('/api/points/<int:pid>/baccarat', methods=['POST'])
def game_baccarat(pid):
    d    = request.json or {}
    side = (d.get('side') or '').lower()
    if side not in ('player','banker','tie'):
        return jsonify({'error': 'Valitse: player, banker tai tie.'}), 400
    db = get_db()
    bet, err = _get_bet(d, pid, db)
    if err: return err
    _atomic_deduct_points(db, pid, bet, f'Baccarat panos ({side})')
    deck = new_deck()
    dealt = _baccarat_deal_result(deck)
    phand, bhand = dealt['player_hand'], dealt['banker_hand']
    ptot, btot = dealt['player_total'], dealt['banker_total']
    winner = dealt['winner']
    # Streak override (cards still shown, only outcome changes)
    streak = _get_streak_mode(db, pid)
    if streak == 'win':
        winner = side
    elif streak == 'lose':
        if   side == 'player': winner = 'banker'
        elif side == 'banker': winner = 'player'
        else:                  winner = 'player'  # tie bet → any non-tie
    # Payouts: player 1:1, banker 0.95:1, tie 8:1. Losers on non-tie bet if winner=tie? Classic rule: tie is a push for player/banker bets.
    payout = 0
    if side == winner:
        if   side == 'player': payout = bet * 2
        elif side == 'banker': payout = bet + int(bet * 0.95)
        elif side == 'tie':    payout = bet * 9
        _add_points(db, pid, payout, f'Baccarat voitto ({winner})')
        outcome = 'win'
    elif winner == 'tie' and side in ('player','banker'):
        payout  = bet  # push
        _add_points(db, pid, payout, 'Baccarat tasapeli (palautus)')
        outcome = 'push'
    else:
        outcome = 'loss'
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({
        'outcome': outcome, 'winner': winner, 'side': side,
        'player_hand': phand, 'banker_hand': bhand,
        'player_total': ptot, 'banker_total': btot,
        'bet': bet, 'payout': payout, 'net': payout - bet, 'points': bal,
        'natural': dealt['natural'], 'draw_events': dealt['draw_events'],
        'rules': {'name': 'Punto Banco Baccarat', 'banker_commission': '5%', 'tie_pays': '8:1'},
    })

# ── Blackjack (stateful) ──
def _bj_state(game):
    pcards  = json.loads(game['player_cards_json'])
    dcards  = json.loads(game['dealer_cards_json'])
    ins_bet = game['insurance_bet'] if game['insurance_bet'] is not None else 0
    active  = game['status'] == 'active'
    return {
        'game_id':      game['id'],
        'bet':          game['bet'],
        'status':       game['status'],
        'player_cards': pcards,
        'dealer_cards': dcards if not active else [dcards[0]] + [{'rank':'?','suit':'?'}]*(len(dcards)-1),
        'player_total': _hand_total(pcards),
        'dealer_total': _hand_total(dcards) if not active else _hand_total([dcards[0]]),
        'player_soft': _hand_total_soft(pcards)[1],
        'dealer_soft': _hand_total_soft(dcards if not active else [dcards[0]])[1],
        'insurance_available': (
            active and
            len(pcards) == 2 and
            dcards[0]['rank'] == 'A' and
            ins_bet == 0
        ),
        'insurance_bet': ins_bet,
        'rules': {'name': 'Blackjack', 'blackjack_pays': '3:2', 'dealer': 'stands on all 17s', 'double': 'first two cards only'},
    }

@app.route('/api/points/<int:pid>/blackjack/start', methods=['POST'])
def game_bj_start(pid):
    d = request.json or {}
    db = get_db()
    # Cancel any stale active game
    db.execute("UPDATE blackjack_games SET status='abandoned' WHERE player_id=? AND status='active'", (pid,))
    bet, err = _get_bet(d, pid, db)
    if err: return err
    _atomic_deduct_points(db, pid, bet, 'Blackjack panos')
    deck   = new_deck()
    pc     = [deck.pop(), deck.pop()]
    dc     = [deck.pop(), deck.pop()]
    status = 'active'
    streak = _get_streak_mode(db, pid)
    player_bj = _is_blackjack(pc)
    dealer_bj = _is_blackjack(dc)
    payout = 0
    outcome = None
    # Real natural handling: player BJ pays 3:2 unless dealer also has BJ; dealer natural ends round.
    if player_bj or dealer_bj:
        if player_bj and dealer_bj:
            status = 'done_push'; outcome = 'push'; payout = bet
            _add_points(db, pid, payout, 'Blackjack luonnollinen tasapeli')
        elif player_bj and streak != 'lose':
            status = 'done_blackjack'; outcome = 'blackjack'; payout = bet + int(bet * 1.5)
            _add_points(db, pid, payout, 'Blackjack luonnollinen 21')
        elif dealer_bj and dc[0]['rank'] != 'A':
            status = 'done_loss'; outcome = 'loss'; payout = 0
        # If dealer shows Ace with natural, keep the hand active until the
        # player takes/declines insurance; the action endpoint then reveals BJ.
    cur = db.execute(
        '''INSERT INTO blackjack_games(player_id,bet,deck_json,player_cards_json,dealer_cards_json,status)
           VALUES(?,?,?,?,?,?)''',
        (pid, bet, json.dumps(deck), json.dumps(pc), json.dumps(dc), status)
    )
    gid = cur.lastrowid
    db.commit()
    game = db.execute('SELECT * FROM blackjack_games WHERE id=?', (gid,)).fetchone()
    state = _bj_state(game)
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    state['points'] = bal
    if status.startswith('done'):
        if outcome is None:
            outcome = 'blackjack' if status == 'done_blackjack' else status.replace('done_', '')
        state['outcome'] = outcome
        state['payout']  = payout
        state['net']     = payout - bet
        state['dealer_has_bj'] = dealer_bj
    return jsonify(state)

@app.route('/api/points/blackjack/<int:gid>/action', methods=['POST'])
def game_bj_action(gid):
    d      = request.json or {}
    action = (d.get('action') or '').lower()
    db     = get_db()
    game   = db.execute('SELECT * FROM blackjack_games WHERE id=?', (gid,)).fetchone()
    if not game:
        return jsonify({'error': 'Peliä ei löydy.'}), 404
    if game['status'] != 'active':
        return jsonify({'error': 'Peli on jo päättynyt.'}), 400
    pid    = game['player_id']
    bet    = game['bet']
    deck   = json.loads(game['deck_json'])
    pcards = json.loads(game['player_cards_json'])
    dcards = json.loads(game['dealer_cards_json'])

    outcome = None; payout = 0

    # Real insurance flow: if dealer shows Ace and has blackjack, any non-insurance
    # action after the prompt reveals/settles the natural immediately.
    if action != 'insurance' and len(pcards) == 2 and dcards[0]['rank'] == 'A' and _is_blackjack(dcards):
        if _is_blackjack(pcards):
            status = 'done_push'; outcome = 'push'; payout = bet
        else:
            status = 'done_loss'; outcome = 'loss'; payout = 0
        if payout > 0:
            _add_points(db, pid, payout, 'Blackjack dealer natural settlement')
        db.execute(
            '''UPDATE blackjack_games SET deck_json=?,player_cards_json=?,dealer_cards_json=?,status=?,bet=?
               WHERE id=?''',
            (json.dumps(deck), json.dumps(pcards), json.dumps(dcards), status, bet, gid)
        )
        db.commit()
        game = db.execute('SELECT * FROM blackjack_games WHERE id=?', (gid,)).fetchone()
        state = _bj_state(game)
        bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
        state.update({'points': bal, 'outcome': outcome, 'payout': payout, 'net': payout - bet, 'dealer_has_bj': True})
        return jsonify(state)

    if action == 'hit':
        pcards.append(deck.pop())
        total = _hand_total(pcards)
        if total > 21:
            status  = 'done_bust'
            outcome = 'bust'
        else:
            status = 'active'
    elif action == 'stand':
        status, outcome, payout = _settle_blackjack_round(deck, pcards, dcards, bet)
    elif action == 'double':
        # Must have exactly 2 cards, and enough points for another bet
        if len(pcards) != 2:
            return jsonify({'error': 'Tuplaus vain ensimmäisellä vuorolla.'}), 400
        if _atomic_deduct_points(db, pid, bet, 'Blackjack tuplaus') is None:
            return jsonify({'error': 'Ei tarpeeksi pisteitä tuplaukseen.'}), 400
        bet *= 2
        pcards.append(deck.pop())
        if _hand_total(pcards) > 21:
            status = 'done_bust'; outcome = 'bust'
        else:
            status, outcome, payout = _settle_blackjack_round(deck, pcards, dcards, bet)
    elif action == 'insurance':
        if len(pcards) != 2:
            return jsonify({'error': 'Vakuutus on mahdollinen vain pelin alussa.'}), 400
        if dcards[0]['rank'] != 'A':
            return jsonify({'error': 'Vakuutus on mahdollinen vain kun jakajalla on ässä.'}), 400
        if game['insurance_bet'] and game['insurance_bet'] > 0:
            return jsonify({'error': 'Vakuutus on jo otettu.'}), 400
        ins = max(1, bet // 2)
        if _atomic_deduct_points(db, pid, ins, 'Blackjack vakuutuspanos') is None:
            return jsonify({'error': 'Ei tarpeeksi pisteitä vakuutukseen.'}), 400
        dealer_bj = _hand_total(dcards) == 21
        if dealer_bj:
            ins_payout = ins * 3  # 2:1 pays: get stake back + 2× profit
            _add_points(db, pid, ins_payout, 'Blackjack vakuutus voitto')
            if _hand_total(pcards) == 21:
                # Both have BJ — push on main hand
                _add_points(db, pid, bet, 'Blackjack tasapeli (BJ vs BJ)')
                status = 'done_push'; outcome = 'push'; payout = bet
            else:
                status = 'done_loss'; outcome = 'loss'; payout = 0
            # Apply streak override
            streak = _get_streak_mode(db, pid)
            if streak == 'win' and outcome == 'loss':
                # Force win: refund bet too
                _add_points(db, pid, bet * 2, 'Blackjack voitto (streak)')
                outcome = 'win'; status = 'done_win'; payout = bet * 2
            net_total = (ins_payout - ins) + (payout - bet)
        else:
            ins_payout = 0
            outcome    = None
            status     = 'active'
            net_total  = -ins
        db.execute(
            'UPDATE blackjack_games SET insurance_bet=?,status=? WHERE id=?',
            (ins, status, gid)
        )
        db.commit()
        game  = db.execute('SELECT * FROM blackjack_games WHERE id=?', (gid,)).fetchone()
        state = _bj_state(game)
        bal   = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
        state['points']           = bal
        state['dealer_has_bj']    = dealer_bj
        state['insurance_result'] = 'win' if dealer_bj else 'loss'
        state['insurance_payout'] = ins_payout
        state['insurance_amount'] = ins
        if dealer_bj and outcome:
            state['outcome'] = outcome
            state['payout']  = payout
            state['net']     = net_total
        else:
            state['net'] = net_total
        return jsonify(state)
    else:
        return jsonify({'error': 'Virheellinen toiminto.'}), 400

    # Apply streak override when game ends
    if outcome is not None:
        streak = _get_streak_mode(db, pid)
        if streak == 'lose' and outcome in ('win', 'push'):
            outcome = 'loss'; status = 'done_loss'; payout = 0
        elif streak == 'win' and outcome in ('loss', 'bust'):
            outcome = 'win'; status = 'done_win'; payout = bet * 2
    if payout > 0:
        _add_points(db, pid, payout, f'Blackjack {outcome}')
    db.execute(
        '''UPDATE blackjack_games SET deck_json=?,player_cards_json=?,dealer_cards_json=?,status=?,bet=?
           WHERE id=?''',
        (json.dumps(deck), json.dumps(pcards), json.dumps(dcards), status, bet, gid)
    )
    db.commit()
    game = db.execute('SELECT * FROM blackjack_games WHERE id=?', (gid,)).fetchone()
    state = _bj_state(game)
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    state['points'] = bal
    if outcome:
        state['outcome'] = outcome
        state['payout']  = payout
        state['net']     = payout - bet  # bet is doubled if they doubled
    return jsonify(state)

# ─── Slots ───────────────────────────────────────────────────────────────────

@app.route('/api/slots/jackpot', methods=['GET'])
def slots_jackpot():
    db = get_db()
    try:
        pool = int(_get_setting(db, JACKPOT_KEY) or JACKPOT_INITIAL)
    except (TypeError, ValueError):
        pool = JACKPOT_INITIAL
    if pool < JACKPOT_INITIAL:
        pool = JACKPOT_INITIAL
        _set_setting(db, JACKPOT_KEY, pool)
        db.commit()
    return jsonify({'pool': pool, 'seed': JACKPOT_INITIAL, 'rake_pct': JACKPOT_RAKE_PCT})


@app.route('/api/points/<int:pid>/slots', methods=['POST'])
def game_slots(pid):
    d        = request.json or {}
    db       = get_db()
    bet, err = _get_bet(d, pid, db)
    if err: return err
    theme_id = d.get('theme', 'fruits')
    if theme_id not in SLOT_THEMES:
        theme_id = 'fruits'
    _atomic_deduct_points(db, pid, bet, f'Slots panos ({theme_id})')

    streak = _get_streak_mode(db, pid)
    theme  = SLOT_THEMES[theme_id]

    # 1. Contribute to the progressive jackpot pool.
    try:
        pool = int(_get_setting(db, JACKPOT_KEY) or JACKPOT_INITIAL)
    except (TypeError, ValueError):
        pool = JACKPOT_INITIAL
    if pool < JACKPOT_INITIAL:
        pool = JACKPOT_INITIAL
    pool += max(1, int(bet * JACKPOT_RAKE_PCT))
    _set_setting(db, JACKPOT_KEY, pool)

    # 2. Spin & evaluate.
    grid              = _slot_spin(theme_id)
    wins              = _slot_calc_wins(grid, theme['payouts'])
    scatter_positions = _slot_scatter_positions(grid)
    jackpot_won       = _slot_is_jackpot(grid)

    # 3. Streak overrides.
    if streak == 'lose' and (wins or len(scatter_positions) >= 3 or jackpot_won):
        for _ in range(10):
            grid              = _slot_spin(theme_id, include_wild=False)
            wins              = _slot_calc_wins(grid, theme['payouts'])
            scatter_positions = _slot_scatter_positions(grid)
            jackpot_won       = False
            if not wins and len(scatter_positions) < 3:
                break
    elif streak == 'win' and not wins and not jackpot_won:
        sym = theme['symbols'][0]['id']
        for c in range(3):
            grid[c][1] = sym
        wins              = _slot_calc_wins(grid, theme['payouts'])
        scatter_positions = _slot_scatter_positions(grid)

    # 4. Base payout.
    total_mult = sum(w['mult'] for w in wins)
    payout     = int(round(bet * total_mult))

    # 5. Free-spins bonus.
    free_spins_triggered = len(scatter_positions) >= 3
    fs_count    = theme['free_spins'] if free_spins_triggered else 0
    fs_mult     = theme['fs_mult']    if free_spins_triggered else 1
    fs_results  = []
    bonus_payout = 0
    bonus_game = None
    fs_feature = None
    if free_spins_triggered:
        fs_results, bonus_payout, fs_feature = _slot_run_free_spins(theme_id, bet, theme)
        bonus_game = _create_slot_bonus_game(db, pid, theme_id, bet)

    # 6. Progressive jackpot.
    jackpot_payout = 0
    if jackpot_won:
        jackpot_payout = pool
        pool = JACKPOT_INITIAL
        _set_setting(db, JACKPOT_KEY, pool)

    total_payout = payout + bonus_payout + jackpot_payout
    net = total_payout - bet
    if total_payout > 0:
        reason = f'Slots voitto ({theme_id})' + (' — JACKPOT!' if jackpot_won else '')
        _add_points(db, pid, total_payout, reason)
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({
        'grid':                 grid,
        'wins':                 wins,
        'total_mult':           total_mult,
        'bet':                  bet,
        'payout':               payout,
        'net':                  net,
        'points':               bal,
        'scatter_positions':    scatter_positions,
        'free_spins_triggered': free_spins_triggered,
        'free_spin_count':      fs_count,
        'free_spin_mult':       fs_mult,
        'free_spin_feature':    fs_feature,
        'free_spin_results':    fs_results,
        'bonus_payout':         bonus_payout,
        'jackpot_won':          jackpot_won,
        'jackpot_payout':       jackpot_payout,
        'jackpot_pool':         pool,
        'bonus_game':           bonus_game,
    })

@app.route('/api/points/<int:pid>/slots/bonus/<int:gid>/pick', methods=['POST'])
def slot_bonus_pick(pid, gid):
    d = request.json or {}
    try:
        tile = int(d.get('tile'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen valinta.'}), 400
    db = get_db()
    game = db.execute('SELECT * FROM slot_bonus_games WHERE id=? AND player_id=?', (gid, pid)).fetchone()
    if not game:
        return jsonify({'error': 'Bonusroundia ei löydy.'}), 404
    if game['status'] != 'active':
        bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
        return jsonify({'error': 'Bonusroundi on jo päättynyt.', 'complete': True, 'points': bal}), 400
    rewards = json.loads(game['rewards_json'])
    picked = json.loads(game['picked_json'] or '[]')
    if tile < 0 or tile >= len(rewards):
        return jsonify({'error': 'Valintaa ei ole.'}), 400
    if tile in picked:
        return jsonify({'error': 'Tämä ruutu on jo avattu.'}), 400
    picked.append(tile)
    reward = rewards[tile]
    amount = int(reward.get('reward') or 0)
    total = (game['total_reward'] or 0) + amount
    complete = len(picked) >= 3
    status = 'complete' if complete else 'active'
    if amount > 0:
        _add_points(db, pid, amount, f"Slots bonus pick ({game['theme_id']})")
    db.execute(
        'UPDATE slot_bonus_games SET picked_json=?,total_reward=?,status=? WHERE id=?',
        (json.dumps(picked), total, status, gid)
    )
    player = db.execute('SELECT name, points FROM players WHERE id=?', (pid,)).fetchone()
    _audit(db, 'slot_bonus_pick', pid, player['name'] if player else '', {'tile': tile, 'reward': amount, 'complete': complete})
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({
        'ok': True,
        'tile': tile,
        'reward': {'label': reward.get('label'), 'mult': reward.get('mult'), 'amount': amount},
        'picked': picked,
        'picks_remaining': max(0, 3 - len(picked)),
        'total_reward': total,
        'complete': complete,
        'points': bal,
    })

# ─── Streak mode (admin) ─────────────────────────────────────────────────────

@app.route('/api/players/<int:pid>/streak', methods=['POST'])
def set_streak_mode(pid):
    d    = request.json or {}
    mode = d.get('mode', 'normal')
    if mode not in ('normal', 'win', 'lose'):
        return jsonify({'error': 'Virheellinen tila.'}), 400
    db = get_db()
    if not db.execute('SELECT id FROM players WHERE id=?', (pid,)).fetchone():
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    db.execute('UPDATE players SET streak_mode=? WHERE id=?', (mode, pid))
    row = db.execute('SELECT name FROM players WHERE id=?', (pid,)).fetchone()
    _audit(db, 'set_streak', pid, row['name'] if row else '', {'mode': mode})
    db.commit()
    return jsonify({'ok': True, 'streak_mode': mode})

# ─── System settings (admin) ─────────────────────────────────────────────────

@app.route('/api/settings', methods=['GET'])
def get_settings():
    db   = get_db()
    rows = db.execute('SELECT key, value FROM system_settings').fetchall()
    out  = dict(SETTINGS_DEFAULTS)
    for r in rows:
        out[r['key']] = r['value']
    return jsonify(out)

@app.route('/api/settings', methods=['PUT'])
def update_settings():
    d  = request.json or {}
    db = get_db()
    for key, val in d.items():
        if key in SETTINGS_DEFAULTS:
            db.execute('INSERT OR REPLACE INTO system_settings(key,value) VALUES(?,?)',
                       (key, str(val)))
    db.commit()
    return jsonify({'ok': True})

# ─── Cash redemption (points → EUR bonus) ────────────────────────────────────

@app.route('/api/players/<int:pid>/points/cash-redeem', methods=['POST'])
def cash_redeem(pid):
    d  = request.json or {}
    db = get_db()
    try:
        pts = int(d.get('points', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen pisteiden määrä.'}), 400
    min_pts = int(_get_setting(db, 'min_redeem_pts'))
    max_pts = int(_get_setting(db, 'max_redeem_pts'))
    ppu     = float(_get_setting(db, 'points_per_eur'))   # points per €1
    if pts < min_pts:
        return jsonify({'error': f'Vähimmäislunastus on {min_pts} pistettä.'}), 400
    if pts > max_pts:
        return jsonify({'error': f'Enimmäislunastus on {max_pts} pistettä kerrallaan.'}), 400
    eur     = round(pts / ppu, 2)
    new_bal = _atomic_deduct_points(db, pid, pts, f'Käteisnosto: {pts} p → €{eur:.2f}')
    if new_bal is None:
        return jsonify({'error': 'Ei tarpeeksi pisteitä.'}), 400
    db.execute(
        'INSERT INTO bonuses(player_id,label,amount,seen) VALUES(?,?,?,?)',
        (pid, f'Pisteistä lunastettu: {pts} pistettä', float(eur), 0)
    )
    db.commit()
    return jsonify({'ok': True, 'points': new_bal, 'eur': eur, 'pts_redeemed': pts})

# ─── Pikapokeri (Jacks-or-Better video poker) ─────────────────────────────────

@app.route('/api/points/<int:pid>/pikapokeri/start', methods=['POST'])
def pikapokeri_start(pid):
    d  = request.json or {}
    db = get_db()
    db.execute("UPDATE pikapokeri_games SET status='abandoned' WHERE player_id=? AND status='deal'", (pid,))
    bet, err = _get_bet(d, pid, db)
    if err: return err
    _atomic_deduct_points(db, pid, bet, 'Pikapokeri panos')
    deck = new_deck()
    hand = [deck.pop() for _ in range(5)]
    cur  = db.execute(
        'INSERT INTO pikapokeri_games(player_id,bet,deck_json,hand_json,status) VALUES(?,?,?,?,?)',
        (pid, bet, json.dumps(deck), json.dumps(hand), 'deal')
    )
    gid = cur.lastrowid
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({'game_id': gid, 'hand': hand, 'bet': bet, 'status': 'deal', 'points': bal})

@app.route('/api/points/pikapokeri/<int:gid>/draw', methods=['POST'])
def pikapokeri_draw(gid):
    d    = request.json or {}
    hold = [int(i) for i in d.get('hold', []) if str(i).isdigit()]
    db   = get_db()
    game = db.execute('SELECT * FROM pikapokeri_games WHERE id=?', (gid,)).fetchone()
    if not game:
        return jsonify({'error': 'Peliä ei löydy.'}), 404
    if game['status'] != 'deal':
        return jsonify({'error': 'Peli on jo päättynyt.'}), 400
    pid  = game['player_id']
    bet  = game['bet']
    deck = json.loads(game['deck_json'])
    hand = json.loads(game['hand_json'])

    new_hand = [hand[i] if i in hold else deck.pop() for i in range(5)]

    rank, mult, result_name = _pikapokeri_eval(new_hand)

    streak = _get_streak_mode(db, pid)
    if streak == 'lose' and mult > 0:
        rank, mult, result_name = -1, 0, 'Häviö'
    elif streak == 'win' and mult == 0:
        rank, mult, result_name = 3, 3, 'Kolmikko'

    payout = bet * mult
    if payout > 0:
        _add_points(db, pid, payout, f'Pikapokeri voitto ({result_name})')

    db.execute(
        'UPDATE pikapokeri_games SET hand_json=?,deck_json=?,status=?,payout=?,result_rank=?,result_name=? WHERE id=?',
        (json.dumps(new_hand), json.dumps(deck), 'done', payout, rank, result_name, gid)
    )
    db.commit()
    bal     = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    outcome = 'win' if payout > 0 else 'loss'
    return jsonify({
        'hand': new_hand, 'rank': rank, 'result_name': result_name,
        'mult': mult, 'bet': bet, 'payout': payout, 'net': payout - bet,
        'outcome': outcome, 'points': bal,
    })

# ─── Card games upgrade pack — Iteration 3 endpoints ─────────────────────────
# Adds: Blackjack side-bets (Perfect Pairs, 21+3), split, surrender, active-hand,
# bonus-buy "Guaranteed Blackjack"; Texas Hold'em mode-B (player-driven betting).
# All routes preserve backward compatibility with existing /start and /action.

_BJ_BONUS_BUY_COOLDOWN_SEC = 120
_BJ_BONUS_BUY_MAX_PER_SESSION = 2
_BJ_BONUS_BUY_PRICE_MULTIPLIER = 2.8
_SUIT_COLOR = {'♠': 'black', '♣': 'black', '♥': 'red', '♦': 'red'}


def _card_rank_value(card):
    """Numeric rank value: A=14, K=13, Q=12, J=11, 10..2."""
    return _RV.get(card.get('rank', ''), 0)


def _resolve_perfect_pairs(player_cards, side_bet_pts):
    """Pays 25× suited, 12× same-color, 6× mixed; lose stake on no-pair."""
    if len(player_cards) < 2:
        return {'won': False, 'result': 'none', 'payout': 0, 'bet': side_bet_pts}
    c1, c2 = player_cards[0], player_cards[1]
    if c1.get('rank') != c2.get('rank'):
        return {'won': False, 'result': 'none', 'payout': 0, 'bet': side_bet_pts}
    if c1.get('suit') == c2.get('suit'):
        return {'won': True, 'result': 'suited', 'payout': 25 * side_bet_pts, 'bet': side_bet_pts}
    if _SUIT_COLOR.get(c1.get('suit')) == _SUIT_COLOR.get(c2.get('suit')):
        return {'won': True, 'result': 'colored', 'payout': 12 * side_bet_pts, 'bet': side_bet_pts}
    return {'won': True, 'result': 'mixed', 'payout': 6 * side_bet_pts, 'bet': side_bet_pts}


def _resolve_21plus3(player_cards, dealer_up, side_bet_pts):
    """3-card poker hand from player's 2 + dealer's up-card."""
    if len(player_cards) < 2 or not dealer_up:
        return {'won': False, 'result': 'none', 'payout': 0, 'bet': side_bet_pts}
    cards = [player_cards[0], player_cards[1], dealer_up]
    rank_values = sorted([_card_rank_value(c) for c in cards])
    suits = [c.get('suit') for c in cards]
    is_flush = len(set(suits)) == 1
    distinct = len(set(rank_values)) == 3
    is_straight = distinct and (rank_values[2] - rank_values[0] == 2)
    if not is_straight and set(rank_values) == {2, 3, 14}:
        is_straight = True
    is_trips = len(set(c.get('rank') for c in cards)) == 1
    if is_trips and is_flush:
        return {'won': True, 'result': 'suited_trips', 'payout': 100 * side_bet_pts, 'bet': side_bet_pts}
    if is_straight and is_flush:
        return {'won': True, 'result': 'straight_flush', 'payout': 40 * side_bet_pts, 'bet': side_bet_pts}
    if is_trips:
        return {'won': True, 'result': 'three_of_a_kind', 'payout': 30 * side_bet_pts, 'bet': side_bet_pts}
    if is_straight:
        return {'won': True, 'result': 'straight', 'payout': 10 * side_bet_pts, 'bet': side_bet_pts}
    if is_flush:
        return {'won': True, 'result': 'flush', 'payout': 5 * side_bet_pts, 'bet': side_bet_pts}
    return {'won': False, 'result': 'none', 'payout': 0, 'bet': side_bet_pts}


@app.route('/api/points/<int:pid>/blackjack/<int:gid>/sidebet', methods=['POST'])
def game_bj_sidebet(pid, gid):
    d = request.json or {}
    db = get_db()
    game = db.execute('SELECT * FROM blackjack_games WHERE id=? AND player_id=?', (gid, pid)).fetchone()
    if not game:
        return jsonify({'error': 'Peliä ei löydy.'}), 404
    if game['status'] != 'active':
        return jsonify({'error': 'Sivupanos ei mahdollinen — peli ei ole aktiivinen.'}), 400
    pcards = json.loads(game['player_cards_json'])
    dcards = json.loads(game['dealer_cards_json'])
    if len(pcards) != 2:
        return jsonify({'error': 'Sivupanos vain ennen ensimmäistä toimintoa.'}), 400
    existing = json.loads(game['side_bets_json'] or '{}') if game['side_bets_json'] else {}
    if existing.get('perfect_pairs') or existing.get('twenty_one_plus_three'):
        return jsonify({'error': 'Sivupanos on jo asetettu tälle kädelle.'}), 400
    try:
        pp_pts = int(d.get('perfect_pairs_pts') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen Perfect Pairs -panos.'}), 400
    try:
        t213_pts = int(d.get('twenty_one_plus_three_pts') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen 21+3 -panos.'}), 400
    if pp_pts < 0 or t213_pts < 0:
        return jsonify({'error': 'Sivupanos ei saa olla negatiivinen.'}), 400
    if pp_pts == 0 and t213_pts == 0:
        return jsonify({'error': 'Anna vähintään yksi sivupanos.'}), 400
    base_bet = game['bet']
    if pp_pts > base_bet:
        return jsonify({'error': f'Perfect Pairs -panos ylittää peruspanoksen ({base_bet}).'}), 400
    if t213_pts > base_bet:
        return jsonify({'error': f'21+3 -panos ylittää peruspanoksen ({base_bet}).'}), 400
    if pp_pts > 0 and _atomic_deduct_points(db, pid, pp_pts, 'Blackjack Perfect Pairs sivupanos') is None:
        return jsonify({'error': 'Ei tarpeeksi pisteitä Perfect Pairs -panokseen.'}), 400
    if t213_pts > 0 and _atomic_deduct_points(db, pid, t213_pts, 'Blackjack 21+3 sivupanos') is None:
        if pp_pts > 0:
            _add_points(db, pid, pp_pts, 'Blackjack PP palautus (21+3 epäonnistui)')
        return jsonify({'error': 'Ei tarpeeksi pisteitä 21+3 -panokseen.'}), 400
    resolved = {}
    if pp_pts > 0:
        pp = _resolve_perfect_pairs(pcards, pp_pts)
        resolved['perfect_pairs'] = pp
        if pp['payout'] > 0:
            _add_points(db, pid, pp['payout'], f"Blackjack Perfect Pairs voitto ({pp['result']})")
    if t213_pts > 0:
        t213 = _resolve_21plus3(pcards, dcards[0], t213_pts)
        resolved['twenty_one_plus_three'] = t213
        if t213['payout'] > 0:
            _add_points(db, pid, t213['payout'], f"Blackjack 21+3 voitto ({t213['result']})")
    db.execute('UPDATE blackjack_games SET side_bets_json=? WHERE id=?', (json.dumps(resolved), gid))
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({'ok': True, 'resolved': resolved, 'side_bets_json': json.dumps(resolved), 'balance_after': bal})


@app.route('/api/points/<int:pid>/blackjack/<int:gid>/split', methods=['POST'])
def game_bj_split(pid, gid):
    db = get_db()
    game = db.execute('SELECT * FROM blackjack_games WHERE id=? AND player_id=?', (gid, pid)).fetchone()
    if not game:
        return jsonify({'error': 'Peliä ei löydy.'}), 404
    if game['status'] != 'active':
        return jsonify({'error': 'Splittaus ei mahdollinen — peli ei ole aktiivinen.'}), 400
    pcards = json.loads(game['player_cards_json'])
    split_hands = json.loads(game['split_hands_json'] or '[]')
    active_idx = game['active_hand_index'] or 0
    split_count = game['split_count'] or 0
    current_hand = pcards if active_idx == 0 else split_hands[active_idx - 1]
    if len(current_hand) != 2:
        return jsonify({'error': 'Splittaus vain alkuperäisillä 2 kortilla.'}), 400
    if current_hand[0]['rank'] != current_hand[1]['rank']:
        return jsonify({'error': 'Splittaus vain saman arvon parista.'}), 400
    if split_count >= 3:
        return jsonify({'error': 'Maksimi 3 splittiä (4 kättä).'}), 400
    if current_hand[0]['rank'] == 'A' and split_count >= 1:
        return jsonify({'error': 'Ässä-splittiä ei voi splittauttaa uudestaan.'}), 400
    bet = game['bet']
    if _atomic_deduct_points(db, pid, bet, f'Blackjack split panos #{split_count + 1}') is None:
        return jsonify({'error': 'Ei tarpeeksi pisteitä splittiin.'}), 400
    deck = json.loads(game['deck_json'])
    if len(deck) < 2:
        _add_points(db, pid, bet, 'Blackjack split palautus (pakka loppui)')
        return jsonify({'error': 'Pakka liian tyhjä splittaukseen.'}), 400
    new_a = deck.pop()
    new_b = deck.pop()
    hand_a = [current_hand[0], new_a]
    hand_b = [current_hand[1], new_b]
    if active_idx == 0:
        pcards = hand_a
        split_hands.insert(0, hand_b)
    else:
        split_hands[active_idx - 1] = hand_a
        split_hands.append(hand_b)
    is_aces = current_hand[0]['rank'] == 'A'
    new_split_count = split_count + 1
    db.execute(
        'UPDATE blackjack_games SET deck_json=?, player_cards_json=?, split_hands_json=?, split_count=?, bet=? WHERE id=?',
        (json.dumps(deck), json.dumps(pcards), json.dumps(split_hands), new_split_count, bet, gid)
    )
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({
        'split_complete': True,
        'hand_a': hand_a,
        'hand_b': hand_b,
        'split_count': new_split_count,
        'active_hand_index': active_idx,
        'split_aces_locked': is_aces,
        'balance_after': bal,
    })


@app.route('/api/points/<int:pid>/blackjack/<int:gid>/surrender', methods=['POST'])
def game_bj_surrender(pid, gid):
    db = get_db()
    game = db.execute('SELECT * FROM blackjack_games WHERE id=? AND player_id=?', (gid, pid)).fetchone()
    if not game:
        return jsonify({'error': 'Peliä ei löydy.'}), 404
    if game['status'] != 'active':
        return jsonify({'error': 'Luovutus ei mahdollinen — peli ei ole aktiivinen.'}), 400
    pcards = json.loads(game['player_cards_json'])
    if len(pcards) != 2:
        return jsonify({'error': 'Luovutus vain alkuperäisillä 2 kortilla.'}), 400
    if (game['split_count'] or 0) > 0:
        return jsonify({'error': 'Luovutus ei mahdollinen splitin jälkeen.'}), 400
    dcards = json.loads(game['dealer_cards_json'])
    dealer_up_value = _card_rank_value(dcards[0])
    if dealer_up_value not in (9, 10, 11, 12, 13, 14):
        return jsonify({'error': 'Late surrender vain kun jakajan avoin kortti on 9, 10, J, Q, K tai A.'}), 400
    refund = game['bet'] // 2
    _add_points(db, pid, refund, f'Blackjack luovutus (palautus {refund} pts)')
    db.execute(
        'UPDATE blackjack_games SET status=?, surrender_amount=?, result_json=? WHERE id=?',
        ('done_surrender', refund,
         json.dumps({'outcome': 'surrender', 'refund_pts': refund}), gid)
    )
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({'surrendered': True, 'refund_pts': refund, 'status': 'done_surrender', 'balance_after': bal})


@app.route('/api/points/<int:pid>/blackjack/<int:gid>/active-hand', methods=['POST'])
def game_bj_active_hand(pid, gid):
    d = request.json or {}
    db = get_db()
    game = db.execute('SELECT * FROM blackjack_games WHERE id=? AND player_id=?', (gid, pid)).fetchone()
    if not game:
        return jsonify({'error': 'Peliä ei löydy.'}), 404
    try:
        hand_index = int(d.get('hand_index', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen hand_index.'}), 400
    split_hands = json.loads(game['split_hands_json'] or '[]')
    max_idx = len(split_hands)
    if hand_index < 0 or hand_index > max_idx:
        return jsonify({'error': f'hand_index alueen ulkopuolella (0..{max_idx}).'}), 400
    db.execute('UPDATE blackjack_games SET active_hand_index=? WHERE id=?', (hand_index, gid))
    db.commit()
    pcards = json.loads(game['player_cards_json'])
    current_hand = pcards if hand_index == 0 else split_hands[hand_index - 1]
    return jsonify({
        'active_hand_index': hand_index,
        'current_hand': current_hand,
        'available_actions': ['hit', 'stand', 'double'] if len(current_hand) == 2 else ['hit', 'stand']
    })


def _bj_session_started_at_iso(db, pid):
    """Earliest activity in the last 4 hours counts as the current session start."""
    row = db.execute(
        "SELECT MIN(created_at) AS s FROM bonus_buy_log WHERE player_id=? AND game_theme='blackjack' "
        "AND created_at > datetime('now', '-4 hours')",
        (pid,)
    ).fetchone()
    return row['s'] if row and row['s'] else None


@app.route('/api/points/<int:pid>/blackjack/bonus-buy', methods=['POST'])
def game_bj_bonus_buy(pid):
    """Guaranteed Blackjack: pay 2.8× intended bet → next deal forces natural BJ.
    Payout matches existing /start route convention: bet + (bet * 1.5) = 3:2 BJ ratio.
    """
    d = request.json or {}
    db = get_db()
    try:
        intended_bet = int(d.get('intended_bet_pts') or d.get('bet') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen panos.'}), 400
    if intended_bet < MIN_BET or intended_bet > MAX_BET:
        return jsonify({'error': f'Panos {MIN_BET}..{MAX_BET} pisteen välillä.'}), 400
    last = db.execute(
        "SELECT created_at FROM bonus_buy_log WHERE player_id=? AND game_theme='blackjack' "
        "ORDER BY created_at DESC LIMIT 1", (pid,)
    ).fetchone()
    cooldown_remaining = 0
    if last:
        try:
            last_ts = datetime.fromisoformat(last['created_at'].replace('Z', '+00:00')) if 'T' in last['created_at'] \
                      else datetime.strptime(last['created_at'], '%Y-%m-%d %H:%M:%S')
            elapsed = (datetime.utcnow() - last_ts.replace(tzinfo=None)).total_seconds()
            if elapsed < _BJ_BONUS_BUY_COOLDOWN_SEC:
                cooldown_remaining = int(_BJ_BONUS_BUY_COOLDOWN_SEC - elapsed)
                return jsonify({'error': 'Cooldown aktiivinen.', 'cooldown_sec_remaining': cooldown_remaining}), 429
        except Exception:
            pass
    session_count = db.execute(
        "SELECT COUNT(*) AS c FROM bonus_buy_log WHERE player_id=? AND game_theme='blackjack' "
        "AND created_at > datetime('now', '-4 hours')", (pid,)
    ).fetchone()['c'] or 0
    if session_count >= _BJ_BONUS_BUY_MAX_PER_SESSION:
        return jsonify({'error': 'Istuntoraja saavutettu (max 2 ostoa / 4h).',
                        'session_remaining': 0}), 409
    price = math.ceil(intended_bet * _BJ_BONUS_BUY_PRICE_MULTIPLIER)
    if _atomic_deduct_points(db, pid, price, f'Blackjack bonus buy ({price} pts)') is None:
        return jsonify({'error': 'Ei tarpeeksi pisteitä ostoon.'}), 402
    rng_seed = hashlib.sha256(f'{pid}:bonus_buy:{time.time()}:{random.random()}'.encode()).hexdigest()[:16]
    db.execute("UPDATE blackjack_games SET status='abandoned' WHERE player_id=? AND status='active'", (pid,))
    deck = new_deck()
    suit_idx_a = int(rng_seed[0], 16) % 4
    forced_ace = {'rank': 'A', 'suit': SUITS[suit_idx_a]}
    ten_rank_idx = int(rng_seed[1], 16) % 4
    ten_ranks = ['10', 'J', 'Q', 'K']
    forced_ten_rank = ten_ranks[ten_rank_idx]
    suit_idx_t = int(rng_seed[2], 16) % 4
    forced_ten = {'rank': forced_ten_rank, 'suit': SUITS[suit_idx_t]}
    deck = [c for c in deck if not (c['rank'] == 'A' and c['suit'] == forced_ace['suit'])]
    deck = [c for c in deck if not (c['rank'] == forced_ten['rank'] and c['suit'] == forced_ten['suit'])]
    pc = [forced_ace, forced_ten]
    dc = [deck.pop(), deck.pop()]
    while _is_blackjack(dc):
        deck.insert(0, dc[1])
        deck.insert(0, dc[0])
        random.shuffle(deck)
        dc = [deck.pop(), deck.pop()]
    payout = intended_bet + int(intended_bet * 1.5)
    _add_points(db, pid, payout, 'Blackjack bonus buy → luonnollinen 21')
    cur = db.execute(
        '''INSERT INTO blackjack_games(player_id,bet,deck_json,player_cards_json,dealer_cards_json,status,result_json)
           VALUES(?,?,?,?,?,?,?)''',
        (pid, intended_bet, json.dumps(deck), json.dumps(pc), json.dumps(dc), 'done_blackjack',
         json.dumps({'outcome': 'blackjack', 'payout': payout, 'bonus_buy': True, 'rng_seed': rng_seed}))
    )
    gid = cur.lastrowid
    db.execute(
        'INSERT INTO bonus_buy_log(player_id,game_theme,bonus_type,cost_points,payout_points,rng_seed,status) '
        'VALUES(?,?,?,?,?,?,?)',
        (pid, 'blackjack', 'guaranteed_blackjack', price, payout, rng_seed, 'consumed')
    )
    _audit(db, 'bonus_buy_blackjack', pid, '', {'bet': intended_bet, 'price': price, 'payout': payout, 'gid': gid})
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (pid,)).fetchone()['points'] or 0
    return jsonify({
        'ok': True,
        'game_id': gid,
        'price_pts': price,
        'payout_pts': payout,
        'player_cards': pc,
        'dealer_cards': dc,
        'cooldown_sec_remaining_after_this': _BJ_BONUS_BUY_COOLDOWN_SEC,
        'session_remaining': max(0, _BJ_BONUS_BUY_MAX_PER_SESSION - session_count - 1),
        'balance_after': bal,
    })


# ─── Texas Hold'em mode-B (player-driven betting) ────────────────────────────

def _poker_compute_next_bettor(db, session_id, current_bettor_seat_id, current_bet_pts):
    seats = db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? AND active=1 AND folded=0 ORDER BY seat_number',
        (session_id,)
    ).fetchall()
    if len(seats) <= 1:
        return None
    if current_bettor_seat_id is None:
        return seats[0]
    current_idx = next((i for i, s in enumerate(seats) if s['id'] == current_bettor_seat_id), None)
    if current_idx is None:
        current_idx = -1
    n = len(seats)
    for offset in range(1, n + 1):
        candidate = seats[(current_idx + offset) % n]
        if candidate['all_in']:
            continue
        if (candidate['current_round_bet_pts'] or 0) < current_bet_pts:
            return candidate
    return None


def _poker_compute_side_pots(db, session_id):
    seats = db.execute(
        'SELECT id, total_session_contribution_pts, all_in, folded FROM poker_seats '
        'WHERE session_id=? AND active=1 ORDER BY total_session_contribution_pts ASC',
        (session_id,)
    ).fetchall()
    side_pots = []
    prior = 0
    eligible = [s['id'] for s in seats if not s['folded']]
    for s in seats:
        contribution = s['total_session_contribution_pts'] or 0
        if s['all_in'] and contribution > prior:
            level = contribution - prior
            pot_pts = level * len(eligible)
            side_pots.append({'eligible_seat_ids': list(eligible), 'pot_pts': pot_pts})
            prior = contribution
            eligible = [e for e in eligible if e != s['id']]
    return side_pots


@app.route('/api/poker/start-mode-b', methods=['POST'])
def poker_start_mode_b():
    d = request.json or {}
    db = get_db()
    sess = current_session(db)
    if not sess:
        return jsonify({'error': 'Aktiivista sessiota ei löydy.'}), 404
    try:
        sb = int(d.get('small_blind_pts') or 50)
        bb = int(d.get('big_blind_pts') or 100)
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheelliset blindit.'}), 400
    if sb <= 0 or bb <= sb:
        return jsonify({'error': 'Big blind > small blind > 0 vaaditaan.'}), 400
    seats = db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? AND active=1 ORDER BY seat_number',
        (sess['id'],)
    ).fetchall()
    if len(seats) < 2:
        return jsonify({'error': 'Vähintään 2 paikkaa vaaditaan.'}), 400
    dealer_seat_id = seats[0]['id']
    db.execute(
        'UPDATE poker_sessions SET mode=?, small_blind_pts=?, big_blind_pts=?, dealer_button_seat_id=?, '
        'pot_json=?, current_bet_pts=?, min_raise_pts=?, current_bettor_seat_id=NULL WHERE id=?',
        ('mode_b', sb, bb, dealer_seat_id, '{}', 0, bb, sess['id'])
    )
    db.execute(
        'UPDATE poker_seats SET current_round_bet_pts=0, total_session_contribution_pts=0, all_in=0, last_action="" '
        'WHERE session_id=?',
        (sess['id'],)
    )
    _audit(db, 'poker_mode_b_start', None, '', {'session_id': sess['id'], 'sb': sb, 'bb': bb})
    db.commit()
    return jsonify({
        'ok': True,
        'session_id': sess['id'],
        'mode': 'mode_b',
        'small_blind_pts': sb,
        'big_blind_pts': bb,
        'dealer_button_seat_id': dealer_seat_id,
    })


@app.route('/api/poker/post-blinds', methods=['POST'])
def poker_post_blinds():
    db = get_db()
    sess = current_session(db)
    if not sess:
        return jsonify({'error': 'Aktiivista sessiota ei löydy.'}), 404
    if sess['mode'] != 'mode_b':
        return jsonify({'error': 'post-blinds vain mode-B sessiossa.'}), 400
    seats = db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? AND active=1 ORDER BY seat_number',
        (sess['id'],)
    ).fetchall()
    if len(seats) < 2:
        return jsonify({'error': 'Vähintään 2 aktiivista paikkaa.'}), 400
    dealer_idx = next((i for i, s in enumerate(seats) if s['id'] == sess['dealer_button_seat_id']), 0)
    n = len(seats)
    sb_seat = seats[(dealer_idx + 1) % n]
    bb_seat = seats[(dealer_idx + 2) % n]
    utg_seat = seats[(dealer_idx + 3) % n]
    sb_amount = sess['small_blind_pts'] or 50
    bb_amount = sess['big_blind_pts'] or 100
    if sb_seat['player_id']:
        if _atomic_deduct_points(db, sb_seat['player_id'], sb_amount, f'Poker SB seat#{sb_seat["seat_number"]}') is None:
            return jsonify({'error': f'Seat {sb_seat["seat_number"]} ei voi maksaa SB:tä ({sb_amount} pts).'}), 402
    if bb_seat['player_id']:
        if _atomic_deduct_points(db, bb_seat['player_id'], bb_amount, f'Poker BB seat#{bb_seat["seat_number"]}') is None:
            if sb_seat['player_id']:
                _add_points(db, sb_seat['player_id'], sb_amount, 'Poker SB palautus (BB epäonnistui)')
            return jsonify({'error': f'Seat {bb_seat["seat_number"]} ei voi maksaa BB:tä ({bb_amount} pts).'}), 402
    db.execute(
        'UPDATE poker_seats SET current_round_bet_pts=?, total_session_contribution_pts=? WHERE id=?',
        (sb_amount, sb_amount, sb_seat['id'])
    )
    db.execute(
        'UPDATE poker_seats SET current_round_bet_pts=?, total_session_contribution_pts=? WHERE id=?',
        (bb_amount, bb_amount, bb_seat['id'])
    )
    pot = {'main': sb_amount + bb_amount, 'side': []}
    db.execute(
        'UPDATE poker_sessions SET pot_json=?, current_bet_pts=?, min_raise_pts=?, current_bettor_seat_id=? WHERE id=?',
        (json.dumps(pot), bb_amount, bb_amount, utg_seat['id'], sess['id'])
    )
    db.commit()
    return jsonify({
        'small_blind_seat_id': sb_seat['id'],
        'big_blind_seat_id': bb_seat['id'],
        'pot_pts': pot['main'],
        'next_bettor_seat_id': utg_seat['id'],
    })


@app.route('/api/poker/seat/<token>/bet', methods=['POST'])
def poker_seat_bet(token):
    d = request.json or {}
    action = (d.get('action') or '').lower()
    if action not in ('fold', 'check', 'call', 'raise', 'all_in'):
        return jsonify({'error': 'Tuntematon toiminto.'}), 400
    db = get_db()
    seat = db.execute('SELECT * FROM poker_seats WHERE join_token=?', (token,)).fetchone()
    if not seat:
        return jsonify({'error': 'Tuntematon paikka.'}), 404
    sess = db.execute('SELECT * FROM poker_sessions WHERE id=?', (seat['session_id'],)).fetchone()
    if not sess:
        return jsonify({'error': 'Sessiota ei löydy.'}), 404
    if sess['mode'] != 'mode_b':
        return jsonify({'error': 'Mode-B vaaditaan panostuksiin.'}), 400
    if seat['folded'] or not seat['active']:
        return jsonify({'error': 'Paikkasi ei ole aktiivinen.'}), 400
    if seat['id'] != sess['current_bettor_seat_id']:
        return jsonify({'error': 'Ei vuorosi.'}), 403
    pot = json.loads(sess['pot_json'] or '{"main":0,"side":[]}')
    main_pot = pot.get('main', 0)
    current_bet = sess['current_bet_pts'] or 0
    seat_round_bet = seat['current_round_bet_pts'] or 0
    seat_balance = db.execute('SELECT points FROM players WHERE id=?', (seat['player_id'],)).fetchone()
    seat_balance = seat_balance['points'] if seat_balance else 0
    new_round_bet = seat_round_bet
    new_min_raise = sess['min_raise_pts'] or 0
    if action == 'fold':
        db.execute('UPDATE poker_seats SET folded=1, last_action=? WHERE id=?', ('fold', seat['id']))
    elif action == 'check':
        if seat_round_bet < current_bet:
            return jsonify({'error': 'Et voi checkata kun panos on auki.'}), 400
        db.execute('UPDATE poker_seats SET last_action=? WHERE id=?', ('check', seat['id']))
    elif action == 'call':
        diff = current_bet - seat_round_bet
        if diff <= 0:
            return jsonify({'error': 'Ei voi callata — panos jo katettu.'}), 400
        if seat_balance < diff:
            return _poker_seat_all_in(db, sess, seat, seat_balance, pot)
        if _atomic_deduct_points(db, seat['player_id'], diff, f'Poker call session#{sess["id"]}') is None:
            return jsonify({'error': 'Ei tarpeeksi pisteitä callaukseen.'}), 402
        main_pot += diff
        new_round_bet = current_bet
        db.execute(
            'UPDATE poker_seats SET current_round_bet_pts=?, total_session_contribution_pts=total_session_contribution_pts+?, last_action=? WHERE id=?',
            (new_round_bet, diff, 'call', seat['id'])
        )
    elif action == 'raise':
        try:
            raise_to = int(d.get('raise_to_pts') or 0)
        except (TypeError, ValueError):
            return jsonify({'error': 'Virheellinen korotus.'}), 400
        if raise_to < current_bet + new_min_raise:
            return jsonify({'error': f'Korotus alle minimin (vaaditaan ≥ {current_bet + new_min_raise}).'}), 400
        diff = raise_to - seat_round_bet
        if seat_balance < diff:
            return _poker_seat_all_in(db, sess, seat, seat_balance, pot)
        if _atomic_deduct_points(db, seat['player_id'], diff, f'Poker raise session#{sess["id"]}') is None:
            return jsonify({'error': 'Ei tarpeeksi pisteitä korotukseen.'}), 402
        main_pot += diff
        new_round_bet = raise_to
        new_min_raise = raise_to - current_bet
        current_bet = raise_to
        db.execute(
            'UPDATE poker_seats SET current_round_bet_pts=?, total_session_contribution_pts=total_session_contribution_pts+?, last_action=? WHERE id=?',
            (new_round_bet, diff, 'raise', seat['id'])
        )
    elif action == 'all_in':
        return _poker_seat_all_in(db, sess, seat, seat_balance, pot)
    pot['main'] = main_pot
    db.execute('UPDATE poker_sessions SET pot_json=?, current_bet_pts=?, min_raise_pts=? WHERE id=?',
               (json.dumps(pot), current_bet, new_min_raise, sess['id']))
    next_seat = _poker_compute_next_bettor(db, sess['id'], seat['id'], current_bet)
    round_complete = next_seat is None
    if not round_complete:
        db.execute('UPDATE poker_sessions SET current_bettor_seat_id=? WHERE id=?', (next_seat['id'], sess['id']))
    else:
        db.execute('UPDATE poker_sessions SET current_bettor_seat_id=NULL WHERE id=?', (sess['id'],))
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (seat['player_id'],)).fetchone()
    bal = bal['points'] if bal else 0
    return jsonify({
        'action_accepted': True,
        'new_pot_pts': main_pot,
        'current_bet_pts': current_bet,
        'next_bettor_seat_id': next_seat['id'] if next_seat else None,
        'round_complete': round_complete,
        'balance_after': bal,
    })


def _poker_seat_all_in(db, sess, seat, seat_balance, pot):
    if seat_balance <= 0:
        return jsonify({'error': 'Ei pisteitä all-iniin.'}), 402
    deduct_amt = seat_balance
    if _atomic_deduct_points(db, seat['player_id'], deduct_amt, f'Poker all-in session#{sess["id"]}') is None:
        return jsonify({'error': 'All-in epäonnistui (samanaikainen muutos saldoon).'}), 402
    seat_round_bet = seat['current_round_bet_pts'] or 0
    new_round_bet = seat_round_bet + deduct_amt
    pot['main'] = pot.get('main', 0) + deduct_amt
    new_current_bet = sess['current_bet_pts'] or 0
    if new_round_bet > new_current_bet:
        new_current_bet = new_round_bet
    db.execute(
        'UPDATE poker_seats SET current_round_bet_pts=?, total_session_contribution_pts=total_session_contribution_pts+?, all_in=1, last_action=? WHERE id=?',
        (new_round_bet, deduct_amt, 'all_in', seat['id'])
    )
    side_pots = _poker_compute_side_pots(db, sess['id'])
    pot['side'] = side_pots
    db.execute('UPDATE poker_sessions SET pot_json=?, current_bet_pts=? WHERE id=?',
               (json.dumps(pot), new_current_bet, sess['id']))
    next_seat = _poker_compute_next_bettor(db, sess['id'], seat['id'], new_current_bet)
    round_complete = next_seat is None
    if not round_complete:
        db.execute('UPDATE poker_sessions SET current_bettor_seat_id=? WHERE id=?', (next_seat['id'], sess['id']))
    else:
        db.execute('UPDATE poker_sessions SET current_bettor_seat_id=NULL WHERE id=?', (sess['id'],))
    db.commit()
    bal = db.execute('SELECT points FROM players WHERE id=?', (seat['player_id'],)).fetchone()
    bal = bal['points'] if bal else 0
    return jsonify({
        'action_accepted': True,
        'all_in': True,
        'new_pot_pts': pot['main'],
        'current_bet_pts': new_current_bet,
        'next_bettor_seat_id': next_seat['id'] if next_seat else None,
        'round_complete': round_complete,
        'side_pots': side_pots,
        'balance_after': bal,
    })


@app.route('/api/poker/round-state', methods=['GET'])
def poker_round_state():
    db = get_db()
    sess = current_session(db)
    if not sess:
        return jsonify({'error': 'Aktiivista sessiota ei löydy.'}), 404
    seats = db.execute(
        'SELECT id, seat_number, player_name, current_round_bet_pts, total_session_contribution_pts, '
        'last_action, all_in, folded FROM poker_seats WHERE session_id=? AND active=1 ORDER BY seat_number',
        (sess['id'],)
    ).fetchall()
    pot = json.loads(sess['pot_json'] or '{"main":0,"side":[]}')
    return jsonify({
        'session_id': sess['id'],
        'mode': sess['mode'] or 'mode_a',
        'stage': sess['stage'],
        'pot_pts': pot.get('main', 0),
        'side_pots': pot.get('side', []),
        'current_bet_pts': sess['current_bet_pts'] or 0,
        'min_raise_pts': sess['min_raise_pts'] or 0,
        'current_bettor_seat_id': sess['current_bettor_seat_id'],
        'dealer_button_seat_id': sess['dealer_button_seat_id'],
        'small_blind_pts': sess['small_blind_pts'] or 0,
        'big_blind_pts': sess['big_blind_pts'] or 0,
        'seats': [dict(s) for s in seats],
    })


@app.route('/api/poker/auto-settle', methods=['POST'])
def poker_auto_settle():
    d = request.json or {}
    db = get_db()
    sess = current_session(db)
    if not sess:
        return jsonify({'error': 'Aktiivista sessiota ei löydy.'}), 404
    if not d.get('operator_confirmed'):
        return jsonify({'error': 'operator_confirmed=true vaaditaan.'}), 400
    seats = db.execute(
        'SELECT * FROM poker_seats WHERE session_id=? AND active=1 AND folded=0',
        (sess['id'],)
    ).fetchall()
    community = json.loads(sess['community_cards_json'] or '[]')
    pot = json.loads(sess['pot_json'] or '{"main":0,"side":[]}')
    total_main = pot.get('main', 0)
    side_pots = pot.get('side', [])
    if len(seats) == 0:
        return jsonify({'error': 'Ei aktiivisia paikkoja.'}), 400
    if len(seats) == 1:
        winner_seat = seats[0]
        if winner_seat['player_id']:
            _add_points(db, winner_seat['player_id'], total_main, f'Poker auto-settle session#{sess["id"]}')
        winners = [{'seat_id': winner_seat['id'], 'player_id': winner_seat['player_id'],
                    'pot_share_pts': total_main, 'hand_rank': None}]
        db.execute('UPDATE poker_sessions SET stage=?, current_bettor_seat_id=NULL WHERE id=?',
                   ('showdown', sess['id']))
        db.commit()
        return jsonify({'settled': True, 'winners': winners, 'side_pots': side_pots})
    if len(community) != 5:
        return jsonify({'error': 'Showdown vaatii 5 yhteistä korttia.'}), 400
    results = []
    for seat in seats:
        holes = json.loads(seat['hole_cards_json'] or '[]')
        if len(holes) != 2:
            continue
        rank_int, tiebreak = best_hand(holes, community)
        results.append({
            'seat_id': seat['id'], 'player_id': seat['player_id'],
            'rank_int': rank_int, 'tiebreak': tiebreak,
            'contribution': seat['total_session_contribution_pts'] or 0,
        })
    results.sort(key=lambda r: (-r['rank_int'], [-x for x in r['tiebreak']]))
    top_rank = (results[0]['rank_int'], results[0]['tiebreak'])
    main_winners = [r for r in results if (r['rank_int'], r['tiebreak']) == top_rank]
    awarded = []
    if main_winners and total_main > 0:
        share = total_main // len(main_winners)
        leftover = total_main - share * len(main_winners)
        for i, w in enumerate(main_winners):
            amount = share + (leftover if i == 0 else 0)
            if w['player_id']:
                _add_points(db, w['player_id'], amount, f'Poker main pot session#{sess["id"]}')
            awarded.append({'seat_id': w['seat_id'], 'player_id': w['player_id'],
                            'pot_share_pts': amount, 'hand_rank': w['rank_int']})
    for sp in side_pots:
        eligible_ids = sp.get('eligible_seat_ids', [])
        eligible_results = [r for r in results if r['seat_id'] in eligible_ids]
        if not eligible_results:
            continue
        eligible_results.sort(key=lambda r: (-r['rank_int'], [-x for x in r['tiebreak']]))
        top = (eligible_results[0]['rank_int'], eligible_results[0]['tiebreak'])
        winners = [r for r in eligible_results if (r['rank_int'], r['tiebreak']) == top]
        sp_pts = sp.get('pot_pts', 0)
        if not winners or sp_pts <= 0:
            continue
        share = sp_pts // len(winners)
        leftover = sp_pts - share * len(winners)
        for i, w in enumerate(winners):
            amount = share + (leftover if i == 0 else 0)
            if w['player_id']:
                _add_points(db, w['player_id'], amount, f'Poker side pot session#{sess["id"]}')
            awarded.append({'seat_id': w['seat_id'], 'player_id': w['player_id'],
                            'pot_share_pts': amount, 'hand_rank': w['rank_int'], 'pot_type': 'side'})
    db.execute('UPDATE poker_sessions SET stage=?, current_bettor_seat_id=NULL WHERE id=?',
               ('showdown', sess['id']))
    log_seats = []
    for s in seats:
        log_seats.append({'id': s['id'], 'seat_number': s['seat_number'], 'player_name': s['player_name'],
                          'hole_cards_json': s['hole_cards_json']})
    db.execute(
        'INSERT INTO poker_hand_log(session_id,hand_number,started_at,ended_at,stage_reached,ended_by,community_cards,seats,winners) '
        'VALUES(?,?,?,?,?,?,?,?,?)',
        (sess['id'], 1, sess['created_at'], datetime.utcnow().isoformat(), 'showdown', 'auto_settle',
         json.dumps(community), json.dumps(log_seats), json.dumps(awarded))
    )
    _audit(db, 'poker_auto_settle', None, '', {'session_id': sess['id'], 'awarded': awarded})
    db.commit()
    return jsonify({'settled': True, 'winners': awarded, 'side_pots': side_pots})




if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)