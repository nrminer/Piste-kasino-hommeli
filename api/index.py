import os, json, random, string, hashlib, threading
from datetime import datetime
from flask import Flask, request, jsonify, render_template, g
import redis as redis_lib

_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(_DIR, '..', 'templates'))

# ─── Storage backend ──────────────────────────────────────────────────────────
# We support three modes, in this order:
#   1. Upstash / Vercel KV via TCP (KV_URL or REDIS_URL — `rediss://...`)
#   2. Standard Redis via REDIS_URL
#   3. Process-local in-memory fallback (so preview deployments without a KV
#      attached still boot and let the user explore the UI). The fallback is
#      *not* persistent across cold starts on serverless platforms, so we surface
#      a banner via `/api/_health` so the client can warn the user.

REDIS_URL = (
    os.environ.get('KV_URL')
    or os.environ.get('REDIS_URL')
    or os.environ.get('UPSTASH_REDIS_URL')
    or ''
)

# Cache the redis client at module scope. Vercel reuses warm containers, so this
# avoids re-establishing TLS on every request.
_redis_client     = None
_redis_client_err = None
_redis_lock       = threading.Lock()


def _build_redis_client():
    """Lazily build a redis client. Returns (client, error_str_or_None)."""
    global _redis_client, _redis_client_err
    if _redis_client is not None or _redis_client_err is not None:
        return _redis_client, _redis_client_err
    with _redis_lock:
        if _redis_client is not None or _redis_client_err is not None:
            return _redis_client, _redis_client_err
        if not REDIS_URL:
            _redis_client_err = 'no_url'
            return None, _redis_client_err
        try:
            client = redis_lib.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30,
            )
            client.ping()
            _redis_client = client
        except Exception as e:  # pragma: no cover — depends on env
            _redis_client_err = f'{type(e).__name__}: {e}'
        return _redis_client, _redis_client_err


# ─── In-memory fallback (mimics the redis hash + counter API we use) ──────────

class _MemKV:
    """Just enough of the redis-py interface for this app to keep working."""
    def __init__(self):
        self._lock     = threading.Lock()
        self._counters = {}
        self._hashes   = {}

    # incr / hget / hset / hgetall / hdel
    def incr(self, key):
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            return self._counters[key]

    def hset(self, name, key=None, value=None, mapping=None):
        with self._lock:
            store = self._hashes.setdefault(name, {})
            if mapping:
                store.update({str(k): str(v) for k, v in mapping.items()})
                return len(mapping)
            store[str(key)] = value if isinstance(value, str) else str(value)
            return 1

    def hget(self, name, key):
        with self._lock:
            return self._hashes.get(name, {}).get(str(key))

    def hgetall(self, name):
        with self._lock:
            return dict(self._hashes.get(name, {}))

    def hdel(self, name, key):
        with self._lock:
            return self._hashes.get(name, {}).pop(str(key), None) is not None

    def ping(self):
        return True

    def close(self):  # no-op
        pass


_memkv = _MemKV()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def get_r():
    """Return a connected redis client, or the in-memory fallback."""
    rc = getattr(g, '_redis', None)
    if rc is not None:
        return rc
    client, _ = _build_redis_client()
    rc = client if client is not None else _memkv
    g._redis = rc
    return rc


def _kv_status():
    """Used by /api/_health and the template to show a banner."""
    if not REDIS_URL:
        return {'mode': 'memory', 'reason': 'KV_URL is not configured.'}
    client, err = _build_redis_client()
    if client is None:
        return {'mode': 'memory', 'reason': f'KV unreachable ({err}).'}
    return {'mode': 'redis', 'reason': ''}


@app.teardown_appcontext
def close_redis(exc):  # noqa: ARG001
    # Connection is cached at module scope; nothing to release per-request.
    g.pop('_redis', None)


@app.route('/api/_health')
def _health():
    return jsonify({'ok': True, 'storage': _kv_status()})

def _insert(rc, table, data):
    id = int(rc.incr(f'seq:{table}'))
    row = dict(data)
    row['id'] = id
    row.setdefault('created_at', _now())
    rc.hset(f'tbl:{table}', str(id), json.dumps(row))
    return id

def _get(rc, table, id):
    v = rc.hget(f'tbl:{table}', str(id))
    return json.loads(v) if v else None

def _all(rc, table):
    rows = rc.hgetall(f'tbl:{table}')
    return [json.loads(v) for v in rows.values()] if rows else []

def _update(rc, table, id, updates):
    row = _get(rc, table, id)
    if row is None:
        return False
    row.update(updates)
    rc.hset(f'tbl:{table}', str(id), json.dumps(row))
    return True

def _delete(rc, table, id):
    rc.hdel(f'tbl:{table}', str(id))

def _where(rc, table, **conds):
    return [r for r in _all(rc, table) if all(r.get(k) == v for k, v in conds.items())]

def _delete_where(rc, table, **conds):
    for row in _where(rc, table, **conds):
        rc.hdel(f'tbl:{table}', str(row['id']))

def _update_where(rc, table, updates, **conds):
    for row in _where(rc, table, **conds):
        row.update(updates)
        rc.hset(f'tbl:{table}', str(row['id']), json.dumps(row))

def _setting_get(rc, key):
    v = rc.hget('settings', key)
    return v if v is not None else SETTINGS_DEFAULTS.get(key, '')

def _setting_set(rc, key, value):
    rc.hset('settings', key, str(value))

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

    Order of preference:
      1. The request host (works for Vercel, custom domains and the Emergent
         preview alike). Only available inside a request context.
      2. VERCEL_URL (build/cold-start fallback on Vercel deployments).
      3. 'localhost' as a last-resort placeholder.
    """
    try:
        if request:  # inside a request → use the actual hostname seen by clients
            return request.host
    except RuntimeError:
        pass
    return os.environ.get('VERCEL_URL', 'localhost')

def current_session(rc):
    sessions = _all(rc, 'poker_sessions')
    return max(sessions, key=lambda s: s['id']) if sessions else None

def _hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

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

# ─── Points / game constants ──────────────────────────────────────────────────

PRIZES_CATALOG = [
    {'id': 'cash5',  'cost':  500, 'label': '€5 kassabonus',             'kind': 'cash', 'amount':  5},
    {'id': 'cash10', 'cost': 1000, 'label': '€10 kassabonus',            'kind': 'cash', 'amount': 10},
    {'id': 'cash25', 'cost': 2250, 'label': '€25 kassabonus',            'kind': 'cash', 'amount': 25},
    {'id': 'cash50', 'cost': 4000, 'label': '€50 kassabonus',            'kind': 'cash', 'amount': 50},
    {'id': 'spin1',  'cost':  300, 'label': '1 onnenpyörän pyöräytys',   'kind': 'spin', 'spins': 1},
    {'id': 'spin5',  'cost': 1200, 'label': '5 onnenpyörän pyöräytystä', 'kind': 'spin', 'spins': 5},
]
PRIZE_BY_ID = {p['id']: p for p in PRIZES_CATALOG}

MIN_BET, MAX_BET = 10, 10000

PIKAPOKERI_PAYOUTS = {9: 800, 8: 50, 7: 25, 6: 9, 5: 6, 4: 4, 3: 3, 2: 2, 1: 1}
PIKAPOKERI_NAMES   = {
    9: 'Royal Flush', 8: 'Värisuora', 7: 'Nelikko',
    6: 'Full House',  5: 'Väri',      4: 'Suora',
    3: 'Kolmikko',    2: 'Kaksi paria', 1: 'Pari (J tai parempi)', -1: 'Häviö',
}

SETTINGS_DEFAULTS = {
    'points_per_eur':    '10',
    'min_redeem_pts':    '500',
    'max_redeem_pts':    '5000',
    'point_expiry_days': '365',
}

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
            {'id':'scatter', 'weight':5},
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
            {'id':'scatter','weight':5},
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

# 20 paylines for a 5×3 grid. Each line is a list of (col, row) tuples,
# always evaluated left-to-right starting at the leftmost reel (col 0).
SLOT_PAYLINES = [
    [(0,1),(1,1),(2,1),(3,1),(4,1)],  # 1  — middle row
    [(0,0),(1,0),(2,0),(3,0),(4,0)],  # 2  — top row
    [(0,2),(1,2),(2,2),(3,2),(4,2)],  # 3  — bottom row
    [(0,0),(1,1),(2,2),(3,1),(4,0)],  # 4  — V (top-bot-top)
    [(0,2),(1,1),(2,0),(3,1),(4,2)],  # 5  — ^ (bot-top-bot)
    [(0,0),(1,0),(2,1),(3,0),(4,0)],  # 6  — top-dip
    [(0,2),(1,2),(2,1),(3,2),(4,2)],  # 7  — bottom-bump
    [(0,1),(1,0),(2,0),(3,0),(4,1)],  # 8  — hat
    [(0,1),(1,2),(2,2),(3,2),(4,1)],  # 9  — bowl
    [(0,0),(1,1),(2,1),(3,1),(4,0)],  # 10 — bridge top
    [(0,2),(1,1),(2,1),(3,1),(4,2)],  # 11 — bridge bottom
    [(0,1),(1,0),(2,1),(3,2),(4,1)],  # 12 — wave down
    [(0,1),(1,2),(2,1),(3,0),(4,1)],  # 13 — wave up
    [(0,0),(1,2),(2,0),(3,2),(4,0)],  # 14 — zigzag top
    [(0,2),(1,0),(2,2),(3,0),(4,2)],  # 15 — zigzag bot
    [(0,1),(1,1),(2,0),(3,1),(4,1)],  # 16 — spike up
    [(0,1),(1,1),(2,2),(3,1),(4,1)],  # 17 — spike down
    [(0,0),(1,0),(2,2),(3,0),(4,0)],  # 18 — drop top
    [(0,2),(1,2),(2,0),(3,2),(4,2)],  # 19 — drop bot
    [(0,0),(1,2),(2,2),(3,2),(4,0)],  # 20 — long arc
]

# Progressive jackpot — 1% of every bet feeds the pool. 5 wilds on the
# middle payline awards the entire pool to the player and resets to seed.
JACKPOT_INITIAL  = 5000
JACKPOT_RAKE_PCT = 0.01
JACKPOT_KEY      = 'jackpot_pool'

# ─── Point helpers ────────────────────────────────────────────────────────────

def _log_points(rc, pid, delta, reason):
    _insert(rc, 'point_transactions', {'player_id': pid, 'delta': int(delta), 'reason': reason})

def _atomic_deduct_points(rc, pid, amount, reason):
    row = _get(rc, 'players', pid)
    if not row or (row.get('points') or 0) < amount:
        return None
    row['points'] = (row.get('points') or 0) - amount
    rc.hset('tbl:players', str(pid), json.dumps(row))
    _log_points(rc, pid, -amount, reason)
    return row['points']

def _add_points(rc, pid, amount, reason):
    row = _get(rc, 'players', pid)
    if not row:
        return None
    row['points'] = (row.get('points') or 0) + amount
    rc.hset('tbl:players', str(pid), json.dumps(row))
    _log_points(rc, pid, amount, reason)
    return row['points']

def _get_bet(d, player_id, rc):
    try:
        bet = int(d.get('bet', 0))
    except (TypeError, ValueError):
        return 0, (jsonify({'error': 'Virheellinen panos.'}), 400)
    if bet < MIN_BET or bet > MAX_BET:
        return 0, (jsonify({'error': f'Panoksen oltava {MIN_BET}–{MAX_BET} pistettä.'}), 400)
    row = _get(rc, 'players', player_id)
    if not row:
        return 0, (jsonify({'error': 'Pelaajaa ei löydy.'}), 404)
    if (row.get('points') or 0) < bet:
        return 0, (jsonify({'error': 'Ei tarpeeksi pisteitä.'}), 400)
    return bet, None

def _get_streak_mode(rc, pid):
    row = _get(rc, 'players', pid)
    return (row.get('streak_mode') or 'normal') if row else 'normal'

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
    """Longest left-aligned match. wild substitutes for the target. Stops at scatter.

    Returns (target_symbol, length). Length 0 means no eligible match.
    """
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
    """Evaluate every payline. Returns list of {line, symbol, count, mult, cells}."""
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
    """Progressive jackpot triggers when all 5 cells of the middle row are wilds."""
    middle = SLOT_PAYLINES[0]
    return all(grid[c][r] == 'wild' for c, r in middle)

def _pikapokeri_eval(cards):
    rank, tiebreakers = _eval5(cards)
    if rank == 0:
        return -1, 0, 'Häviö'
    if rank == 1:
        if tiebreakers[0] < 11:
            return -1, 0, 'Häviö'
        return 1, 1, 'Pari (J tai parempi)'
    mult = PIKAPOKERI_PAYOUTS.get(rank, 0)
    return rank, mult, PIKAPOKERI_NAMES.get(rank, 'Häviö')

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
        "name": "Kasino", "short_name": "Kasino",
        "start_url": "/asiakas", "display": "standalone",
        "background_color": "#0a1a10", "theme_color": "#0a1a10",
        "orientation": "portrait",
        "icons": [{"src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' fill='%230a1a10'/><text y='.9em' font-size='80' x='10'>♠</text></svg>",
                   "sizes": "any", "type": "image/svg+xml"}]
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

# ─── Players API ─────────────────────────────────────────────────────────────

@app.route('/api/players', methods=['GET'])
def list_players():
    rc      = get_r()
    q       = request.args.get('q',   '').strip().lower()
    vip     = request.args.get('vip', '').strip()
    players = sorted(_all(rc, 'players'), key=lambda p: p.get('name',''))
    all_txs = _all(rc, 'transactions')
    tx_by   = {}
    for t in all_txs:
        tx_by.setdefault(t['player_id'], []).append(t)
    result = []
    for p in players:
        p = dict(p)
        txs = tx_by.get(p['id'], [])
        p['total_won']   = sum(t['amount'] for t in txs if t['amount'] > 0)
        p['total_lost']  = sum(-t['amount'] for t in txs if t['amount'] < 0)
        p['net_balance'] = sum(t['amount'] for t in txs)
        p['tx_count']    = len(txs)
        has_pw = bool(p.get('password_hash',''))
        p.pop('password_hash', None)
        p['has_password']    = has_pw
        p['spins_remaining'] = p.get('spins_remaining') or 0
        p['points']          = p.get('points') or 0
        p['streak_mode']     = p.get('streak_mode') or 'normal'
        result.append(p)
    if q:
        result = [p for p in result if q in p['name'].lower()
                  or q in (p.get('email') or '').lower()
                  or q in (p.get('phone') or '').lower()]
    if vip:
        result = [p for p in result if p.get('vip_level') == vip]
    return jsonify(result)

@app.route('/api/players', methods=['POST'])
def create_player():
    d       = request.json
    rc      = get_r()
    pw      = (d.get('password') or '').strip()
    pw_hash = _hash_pw(pw) if pw else ''
    pid = _insert(rc, 'players', {
        'name': d['name'], 'email': d.get('email',''), 'phone': d.get('phone',''),
        'vip_level': d.get('vip_level','Standard'), 'notes': d.get('notes',''),
        'password_hash': pw_hash, 'spins_remaining': 0, 'points': 0, 'streak_mode': 'normal',
    })
    row = dict(_get(rc, 'players', pid))
    row.pop('password_hash', None)
    row['has_password'] = bool(pw_hash)
    row.update({'total_won': 0, 'total_lost': 0, 'net_balance': 0, 'tx_count': 0})
    return jsonify(row), 201

@app.route('/api/players/<int:pid>', methods=['PUT'])
def update_player(pid):
    d  = request.json
    rc = get_r()
    pw = (d.get('password') or '').strip()
    updates = {'name': d['name'], 'email': d.get('email',''), 'phone': d.get('phone',''),
               'vip_level': d.get('vip_level','Standard'), 'notes': d.get('notes','')}
    if pw:
        updates['password_hash'] = _hash_pw(pw)
    _update(rc, 'players', pid, updates)
    return jsonify({'ok': True})

@app.route('/api/players/<int:pid>', methods=['DELETE'])
def delete_player(pid):
    rc = get_r()
    for table in ('transactions','bonuses','point_transactions','blackjack_games','pikapokeri_games'):
        _delete_where(rc, table, player_id=pid)
    _delete(rc, 'players', pid)
    return jsonify({'ok': True})

@app.route('/api/players/<int:pid>/grant-spins', methods=['POST'])
def grant_spins(pid):
    d  = request.json or {}
    rc = get_r()
    try:
        count = int(d.get('count', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen määrä.'}), 400
    if count == 0:
        return jsonify({'error': 'Määrä ei voi olla 0.'}), 400
    row = _get(rc, 'players', pid)
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    new_val = max(0, (row.get('spins_remaining') or 0) + count)
    _update(rc, 'players', pid, {'spins_remaining': new_val})
    return jsonify({'ok': True, 'spins_remaining': new_val, 'granted': count})

@app.route('/api/players/<int:pid>/spins', methods=['GET'])
def get_spins(pid):
    rc  = get_r()
    row = _get(rc, 'players', pid)
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    return jsonify({'spins_remaining': row.get('spins_remaining') or 0})

@app.route('/api/players/<int:pid>/transactions', methods=['GET'])
def player_transactions(pid):
    rc   = get_r()
    txs  = sorted(_where(rc, 'transactions', player_id=pid),
                  key=lambda t: t.get('created_at',''), reverse=True)
    return jsonify(txs)

@app.route('/api/players/<int:pid>/transactions', methods=['POST'])
def add_transaction(pid):
    d   = request.json
    rc  = get_r()
    tid = _insert(rc, 'transactions', {
        'player_id': pid, 'amount': float(d['amount']),
        'game_type': d.get('game_type','Muu'), 'note': d.get('note',''),
    })
    return jsonify(_get(rc, 'transactions', tid)), 201

@app.route('/api/transactions/<int:tid>', methods=['DELETE'])
def delete_transaction(tid):
    _delete(get_r(), 'transactions', tid)
    return jsonify({'ok': True})

# ─── Bonuses API ─────────────────────────────────────────────────────────────

@app.route('/api/players/<int:pid>/bonuses', methods=['GET'])
def get_player_bonuses(pid):
    rc   = get_r()
    rows = sorted(_where(rc, 'bonuses', player_id=pid),
                  key=lambda b: b.get('created_at',''), reverse=True)
    return jsonify(rows)

@app.route('/api/players/<int:pid>/bonuses', methods=['POST'])
def add_bonus(pid):
    d        = request.json
    rc       = get_r()
    seen_val = 0 if d.get('notify', True) else 1
    bid = _insert(rc, 'bonuses', {
        'player_id': pid, 'label': d.get('label','Bonus'),
        'amount': float(d.get('amount', 0)), 'claimed': 0, 'seen': seen_val,
    })
    return jsonify(_get(rc, 'bonuses', bid)), 201

@app.route('/api/bonuses/<int:bid>', methods=['DELETE'])
def delete_bonus(bid):
    _delete(get_r(), 'bonuses', bid)
    return jsonify({'ok': True})

@app.route('/api/bonuses/<int:bid>/seen', methods=['POST'])
def mark_bonus_seen(bid):
    _update(get_r(), 'bonuses', bid, {'seen': 1})
    return jsonify({'ok': True})

@app.route('/api/bonuses/<int:bid>/claim', methods=['POST'])
def claim_bonus(bid):
    rc    = get_r()
    bonus = _get(rc, 'bonuses', bid)
    if not bonus:
        return jsonify({'error': 'Bonusta ei löydy.'}), 404
    if bonus.get('claimed'):
        return jsonify({'error': 'Bonus on jo lunastettu.', 'already_claimed': True}), 400
    _update(rc, 'bonuses', bid, {'claimed': 1, 'seen': 1})
    bonus = _get(rc, 'bonuses', bid)
    return jsonify({'ok': True, 'bonus': bonus, 'amount': bonus['amount'], 'label': bonus['label']})

# ─── Dashboard API ───────────────────────────────────────────────────────────

@app.route('/api/dashboard')
def dashboard():
    rc        = get_r()
    all_txs   = _all(rc, 'transactions')
    all_p     = _all(rc, 'players')
    house_rev = sum(-t['amount'] for t in all_txs if t['amount'] < 0)
    paid_out  = sum( t['amount'] for t in all_txs if t['amount'] > 0)
    tx_by     = {}
    for t in all_txs:
        tx_by.setdefault(t['player_id'], []).append(t)
    pm = {p['id']: p for p in all_p}

    top_losers = sorted(
        [{'id': p['id'], 'name': p['name'], 'vip_level': p.get('vip_level',''),
          'net': sum(t['amount'] for t in tx_by.get(p['id'], []))}
         for p in all_p
         if sum(t['amount'] for t in tx_by.get(p['id'], [])) < 0],
        key=lambda x: x['net'])[:5]

    top_winners = sorted(
        [{'id': p['id'], 'name': p['name'], 'vip_level': p.get('vip_level',''),
          'net': sum(t['amount'] for t in tx_by.get(p['id'], []))}
         for p in all_p
         if sum(t['amount'] for t in tx_by.get(p['id'], [])) > 0],
        key=lambda x: x['net'], reverse=True)[:5]

    recent = []
    for t in sorted(all_txs, key=lambda t: t.get('created_at',''), reverse=True)[:12]:
        tx = dict(t)
        p  = pm.get(t['player_id'], {})
        tx['player_name'] = p.get('name','')
        tx['vip_level']   = p.get('vip_level','')
        recent.append(tx)

    game_stats = {}
    for t in all_txs:
        gt = t.get('game_type','Muu')
        gs = game_stats.setdefault(gt, {'game_type': gt, 'cnt': 0, 'house_take': 0.0})
        gs['cnt'] += 1
        if t['amount'] < 0:
            gs['house_take'] += -t['amount']
    by_game = sorted(game_stats.values(), key=lambda x: x['house_take'], reverse=True)

    return jsonify({
        'house_revenue': house_rev, 'total_payouts': paid_out,
        'net_house': house_rev - paid_out, 'total_players': len(all_p),
        'total_transactions': len(all_txs), 'top_losers': top_losers,
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
    rc     = get_r()
    player = next((p for p in _all(rc, 'players') if p['name'].lower() == name.lower()), None)
    if not player:
        return jsonify({'error': 'Käyttäjää ei löydy. Pyydä kassohenkilökuntaa rekisteröimään sinut.'}), 404
    p = dict(player)
    if p.get('password_hash'):
        if not password:
            return jsonify({'error': 'Tili vaatii salasanan.', 'needs_password': True}), 401
        if _hash_pw(password) != p['password_hash']:
            return jsonify({'error': 'Väärä salasana.'}), 401
    p.pop('password_hash', None)
    p['has_password']    = bool(player.get('password_hash'))
    p['spins_remaining'] = p.get('spins_remaining') or 0
    p['points']          = p.get('points') or 0
    p['bonuses'] = sorted(_where(rc, 'bonuses', player_id=p['id']),
                          key=lambda b: b.get('created_at',''), reverse=True)
    return jsonify(p)

# ─── Poker API ───────────────────────────────────────────────────────────────

@app.route('/api/poker/state')
def poker_state():
    rc   = get_r()
    sess = current_session(rc)
    if not sess:
        return jsonify({'status': 'none'})
    sess = dict(sess)
    sess['community_cards'] = json.loads(sess.get('community_cards_json','[]'))
    sess['preset_hands']    = json.loads(sess.get('preset_hands_json') or '{}')
    seats = sorted(_where(rc, 'poker_seats', session_id=sess['id']),
                   key=lambda s: s['seat_number'])
    for s in seats:
        s['hole_cards'] = json.loads(s.get('hole_cards_json','[]'))
    sess['seats'] = seats
    return jsonify(sess)

@app.route('/api/poker/new', methods=['POST'])
def poker_new():
    rc  = get_r()
    sid = _insert(rc, 'poker_sessions', {
        'status': 'waiting', 'deck_json': json.dumps(new_deck()),
        'community_cards_json': '[]', 'stage': 'waiting', 'preset_hands_json': '{}',
    })
    return jsonify({'id': sid, 'status': 'waiting'})

@app.route('/api/poker/join', methods=['POST'])
def poker_join_api():
    d    = request.json
    rc   = get_r()
    sess = current_session(rc)
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Nimi vaaditaan.'}), 400
    if not sess:
        return jsonify({'error': 'Ei avoimia pelejä — pyydä jakajaa aloittamaan peli.'}), 400
    existing = next((s for s in _where(rc, 'poker_seats', session_id=sess['id'], active=1)
                     if s['player_name'] == name), None)
    if existing:
        return jsonify({'token': existing['join_token'], 'seat': existing['seat_number'], 'name': name})
    if sess['status'] != 'waiting':
        return jsonify({'error': 'Peli on jo käynnissä — odotetaan seuraavaa kierrosta.'}), 400
    count = len(_where(rc, 'poker_seats', session_id=sess['id'], active=1))
    if count >= 9:
        return jsonify({'error': 'Pöytä täynnä (max 9 pelaajaa).'}), 400
    token     = gen_token()
    player_id = d.get('player_id')
    _insert(rc, 'poker_seats', {
        'session_id': sess['id'], 'player_name': name, 'player_id': player_id,
        'hole_cards_json': '[]', 'folded': 0, 'active': 1, 'show_cards': 0,
        'join_token': token, 'seat_number': count + 1,
    })
    return jsonify({'token': token, 'seat': count + 1, 'name': name})

# ─── Poker hand log helpers (Redis / in-memory backend) ─────────────────────
# Every hand started with `/api/poker/deal` is logged to `poker_hand_log`.
# The log captures hole cards at deal-time, community cards as they are
# revealed, the stage reached, the final winners (on showdown), and the
# way the hand ended (showdown / void / abandoned by an early re-deal).

def _hand_log_for_session(rc, session_id):
    rows = [r for r in _where(rc, 'poker_hand_log', session_id=session_id, ended_by='in_progress')]
    if not rows:
        return None
    return max(rows, key=lambda r: r['id'])

def _seats_snapshot_kv(rc, session_id):
    seats = sorted(_where(rc, 'poker_seats', session_id=session_id, active=1),
                   key=lambda s: s['seat_number'])
    return [{
        'seat_number': s['seat_number'],
        'player_name': s['player_name'],
        'player_id':   s.get('player_id'),
        'hole_cards':  json.loads(s.get('hole_cards_json') or '[]'),
        'folded':      bool(s.get('folded')),
        'show_cards':  bool(s.get('show_cards')),
    } for s in seats]

def _log_hand_start(rc, session_id):
    prev = _hand_log_for_session(rc, session_id)
    if prev:
        _update(rc, 'poker_hand_log', prev['id'],
                {'ended_by': 'abandoned', 'ended_at': _now()})
    existing = _where(rc, 'poker_hand_log', session_id=session_id)
    nxt = max((r.get('hand_number') or 0) for r in existing) + 1 if existing else 1
    _insert(rc, 'poker_hand_log', {
        'session_id':      session_id,
        'hand_number':     nxt,
        'started_at':      _now(),
        'ended_at':        None,
        'stage_reached':   'preflop',
        'ended_by':        'in_progress',
        'community_cards': [],
        'seats':           _seats_snapshot_kv(rc, session_id),
        'winners':         [],
    })

def _log_hand_advance(rc, session_id, stage, community):
    log = _hand_log_for_session(rc, session_id)
    if not log:
        return
    _update(rc, 'poker_hand_log', log['id'],
            {'stage_reached': stage, 'community_cards': community})

def _log_hand_winners(rc, session_id, winners, stage):
    log = _hand_log_for_session(rc, session_id)
    if not log:
        return
    fields = {'winners': winners}
    if stage == 'showdown':
        fields.update({
            'ended_by': 'showdown',
            'ended_at': _now(),
            'seats':    _seats_snapshot_kv(rc, session_id),
        })
    _update(rc, 'poker_hand_log', log['id'], fields)

def _log_hand_void(rc, session_id):
    log = _hand_log_for_session(rc, session_id)
    if not log:
        return
    _update(rc, 'poker_hand_log', log['id'], {
        'ended_by': 'void', 'ended_at': _now(),
        'seats':    _seats_snapshot_kv(rc, session_id),
    })

@app.route('/api/poker/hands')
def poker_hands_log():
    rc     = get_r()
    limit  = max(1, min(int(request.args.get('limit', 25)), 200))
    offset = max(0, int(request.args.get('offset', 0)))
    sid    = request.args.get('session_id')
    rows = sorted(_all(rc, 'poker_hand_log'), key=lambda r: r['id'], reverse=True)
    if sid and sid not in ('', 'all'):
        try:
            sid_int = int(sid)
            rows = [r for r in rows if r.get('session_id') == sid_int]
        except (TypeError, ValueError):
            pass
    total = len(rows)
    page  = rows[offset:offset+limit]
    out = [_hand_log_row_to_dict(r) for r in page]
    # Sessions metadata: latest first, with hand counts
    all_rows = sorted(_all(rc, 'poker_hand_log'),
                      key=lambda r: r.get('started_at') or '', reverse=True)
    seen, sessions_meta = set(), []
    for r in all_rows:
        s = r.get('session_id')
        if s is None or s in seen:
            continue
        seen.add(s)
        sessions_meta.append({
            'session_id': s,
            'last':       r.get('started_at'),
            'count':      sum(1 for x in all_rows if x.get('session_id') == s),
        })
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
    rc  = get_r()
    sid = request.args.get('session_id')
    rows = sorted(_all(rc, 'poker_hand_log'), key=lambda r: r['id'], reverse=True)
    if sid and sid not in ('', 'all'):
        try:
            sid_int = int(sid)
            rows = [r for r in rows if r.get('session_id') == sid_int]
        except (TypeError, ValueError):
            pass
    hands = [_hand_log_row_to_dict(r) for r in rows]
    return _hand_log_csv_response(hands, sid)


def _hand_log_row_to_dict(r):
    return {
        'id':              r.get('id'),
        'session_id':      r.get('session_id'),
        'hand_number':     r.get('hand_number'),
        'started_at':      r.get('started_at'),
        'ended_at':        r.get('ended_at'),
        'stage_reached':   r.get('stage_reached'),
        'ended_by':        r.get('ended_by'),
        'community_cards': r.get('community_cards') or [],
        'seats':           r.get('seats') or [],
        'winners':         r.get('winners') or [],
    }


def _fmt_cards(cards):
    return ' '.join(f"{c.get('rank','?')}{c.get('suit','?')}" for c in (cards or []))


def _hand_log_csv_response(hands, session_id_filter=None):
    """Stream the hand log as CSV. UTF-8 BOM + ; delimiter (Excel/Finnish-friendly)."""
    import csv, io
    from flask import Response
    buf = io.StringIO()
    buf.write('\ufeff')
    w = csv.writer(buf, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    w.writerow([
        'Käsi #', 'Istunto', 'Aloitettu', 'Päättynyt',
        'Vaihe', 'Päättymistapa', 'Yhteiskortit',
        'Pelaajat', 'Voittajat',
    ])
    end_map = {'showdown':'Showdown','void':'Mitätöity','abandoned':'Hylätty','in_progress':'Käynnissä'}
    for h in hands:
        seats = '; '.join(
            f"#{s.get('seat_number')} {s.get('player_name','')}: {_fmt_cards(s.get('hole_cards'))}"
            + (' (fold)' if s.get('folded') else '')
            for s in (h.get('seats') or [])
        )
        winners = ', '.join(
            f"{x.get('player_name','')} ({x.get('hand_name','')})"
            for x in (h.get('winners') or []) if x.get('is_winner')
        )
        w.writerow([
            h.get('hand_number'),
            h.get('session_id'),
            h.get('started_at') or '',
            h.get('ended_at') or '',
            h.get('stage_reached') or '',
            end_map.get(h.get('ended_by'), h.get('ended_by') or ''),
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
    rc    = get_r()
    sess  = current_session(rc)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    deck    = json.loads(sess['deck_json'])
    presets = json.loads(sess.get('preset_hands_json') or '{}')
    seats   = sorted(_where(rc, 'poker_seats', session_id=sess['id'], active=1),
                     key=lambda s: s['seat_number'])
    if not seats:
        return jsonify({'error': 'Ei pelaajia pöydässä.'}), 400
    used = {(c['rank'], c['suit']) for cards in presets.values() for c in cards if isinstance(cards, list)}
    deck = [c for c in deck if (c['rank'], c['suit']) not in used]
    if len(deck) < len(seats) * 2 + 5:
        deck = [c for c in new_deck() if (c['rank'], c['suit']) not in used]
    for seat in seats:
        sid = str(seat['id'])
        cards = presets[sid] if (sid in presets and len(presets[sid]) == 2) else [deck.pop(), deck.pop()]
        _update(rc, 'poker_seats', seat['id'], {'hole_cards_json': json.dumps(cards), 'folded': 0})
    comm_preset = presets.get('community', [])
    _update(rc, 'poker_sessions', sess['id'], {
        'deck_json': json.dumps(deck), 'stage': 'preflop', 'status': 'active',
        'community_cards_json': '[]',
        'preset_hands_json': json.dumps({'community': comm_preset} if comm_preset else {}),
    })
    _log_hand_start(rc, sess['id'])
    return jsonify({'ok': True, 'stage': 'preflop'})

@app.route('/api/poker/preset', methods=['POST'])
def poker_preset():
    d    = request.json or {}
    rc   = get_r()
    sess = current_session(rc)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    _update(rc, 'poker_sessions', sess['id'], {'preset_hands_json': json.dumps(d)})
    return jsonify({'ok': True})

@app.route('/api/poker/advance', methods=['POST'])
def poker_advance():
    rc   = get_r()
    sess = current_session(rc)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    deck      = json.loads(sess['deck_json'])
    community = json.loads(sess['community_cards_json'])
    stage     = sess['stage']
    comm_pre  = json.loads(sess.get('preset_hands_json') or '{}').get('community', [])
    def _cc(idx):
        return comm_pre[idx] if (idx < len(comm_pre) and comm_pre[idx]) else deck.pop()
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
    _update(rc, 'poker_sessions', sess['id'], {
        'deck_json': json.dumps(deck), 'stage': new_stage,
        'community_cards_json': json.dumps(community),
    })
    _log_hand_advance(rc, sess['id'], new_stage, community)
    return jsonify({'ok': True, 'stage': new_stage, 'community_cards': community})

@app.route('/api/poker/void', methods=['POST'])
def poker_void():
    rc   = get_r()
    sess = current_session(rc)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    # Snapshot the hand BEFORE we wipe seat hole cards.
    _log_hand_void(rc, sess['id'])
    _update(rc, 'poker_sessions', sess['id'], {
        'deck_json': json.dumps(new_deck()), 'stage': 'waiting',
        'community_cards_json': '[]', 'status': 'waiting', 'preset_hands_json': '{}',
    })
    _update_where(rc, 'poker_seats', {'hole_cards_json': '[]', 'folded': 0, 'show_cards': 0},
                  session_id=sess['id'], active=1)
    return jsonify({'ok': True})

@app.route('/api/poker/fold/<int:seat_id>', methods=['POST'])
def poker_fold(seat_id):
    _update(get_r(), 'poker_seats', seat_id, {'folded': 1})
    return jsonify({'ok': True})

@app.route('/api/poker/remove/<int:seat_id>', methods=['DELETE'])
def poker_remove(seat_id):
    _update(get_r(), 'poker_seats', seat_id, {'active': 0})
    return jsonify({'ok': True})

@app.route('/api/poker/player/<token>/showcards', methods=['POST'])
def toggle_show_cards(token):
    rc   = get_r()
    seat = next((s for s in _all(rc, 'poker_seats') if s.get('join_token') == token), None)
    if not seat:
        return jsonify({'error': 'Virheellinen tunnus.'}), 404
    new_val = 0 if seat['show_cards'] else 1
    _update(rc, 'poker_seats', seat['id'], {'show_cards': new_val})
    return jsonify({'show_cards': bool(new_val)})

@app.route('/api/poker/player/<token>')
def poker_player_state(token):
    rc   = get_r()
    seat = next((s for s in _all(rc, 'poker_seats') if s.get('join_token') == token), None)
    if not seat:
        return jsonify({'error': 'Virheellinen tunnus.'}), 404
    sess     = _get(rc, 'poker_sessions', seat['session_id'])
    n_active = len(_where(rc, 'poker_seats', session_id=sess['id'], active=1))
    return jsonify({
        'name':            seat['player_name'],
        'seat':            seat['seat_number'],
        'hole_cards':      json.loads(seat.get('hole_cards_json','[]')),
        'folded':          bool(seat['folded']),
        'active':          bool(seat['active']),
        'show_cards':      bool(seat['show_cards']),
        'stage':           sess['stage'],
        'community_cards': json.loads(sess.get('community_cards_json','[]')),
        'status':          sess['status'],
        'n_players':       n_active,
    })

@app.route('/api/poker/evaluate')
def poker_evaluate():
    rc        = get_r()
    sess      = current_session(rc)
    if not sess:
        return jsonify({'error': 'Ei istuntoa.'}), 400
    community = json.loads(sess.get('community_cards_json','[]'))
    if len(community) < 3:
        return jsonify({'error': 'Tarvitaan vähintään flop arviointia varten.'}), 400
    seats = sorted(
        [s for s in _all(rc, 'poker_seats')
         if s['session_id'] == sess['id'] and s['active'] == 1 and s['folded'] == 0],
        key=lambda s: s['seat_number'])
    results = []
    for s in seats:
        hole = json.loads(s.get('hole_cards_json','[]'))
        if len(hole) != 2:
            continue
        rank, tb = best_hand(hole, community)
        results.append({
            'seat_id': s['id'], 'seat_number': s['seat_number'],
            'player_name': s['player_name'], 'hole_cards': hole,
            'hand_rank': rank, 'hand_name': HAND_NAMES[rank], 'tiebreakers': tb,
        })
    results.sort(key=lambda r: (r['hand_rank'], r['tiebreakers']), reverse=True)
    if results:
        top = results[0]
        for r in results:
            r['is_winner'] = (r['hand_rank'] == top['hand_rank'] and r['tiebreakers'] == top['tiebreakers'])
    _log_hand_winners(rc, sess['id'], results, sess['stage'])
    return jsonify(results)

@app.route('/api/poker/spin', methods=['POST'])
def poker_spin():
    d         = request.json or {}
    player_id = d.get('player_id')
    if not player_id:
        return jsonify({'error': 'Kirjaudu sisään pyöräyttääksesi.'}), 401
    rc  = get_r()
    row = _get(rc, 'players', player_id)
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    remaining = row.get('spins_remaining') or 0
    if remaining <= 0:
        return jsonify({'error': 'Sinulla ei ole pyöräytyksiä. Pyydä kassohenkilökunnalta.',
                        'spins_remaining': 0}), 403
    row['spins_remaining'] = remaining - 1
    rc.hset('tbl:players', str(player_id), json.dumps(row))
    new_remaining = row['spins_remaining']

    total = sum(p['weight'] for p in SPIN_PRIZES)
    r_val = random.uniform(0, total)
    cum   = 0
    idx, prize = 0, SPIN_PRIZES[0]
    for i, pr in enumerate(SPIN_PRIZES):
        cum += pr['weight']
        if r_val <= cum:
            idx, prize = i, pr
            break
    _insert(rc, 'bonuses', {
        'player_id': player_id, 'label': f"Pyöräytys: {prize['label']}",
        'amount': float(prize['bonus']), 'claimed': 0, 'seen': 0,
    })
    return jsonify({'prize': prize, 'index': idx, 'spins_remaining': new_remaining})

# ─── Point admin endpoints ───────────────────────────────────────────────────

@app.route('/api/players/<int:pid>/points', methods=['GET'])
def get_points(pid):
    rc  = get_r()
    row = _get(rc, 'players', pid)
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    history = sorted(_where(rc, 'point_transactions', player_id=pid),
                     key=lambda t: t.get('created_at',''), reverse=True)[:30]
    return jsonify({'points': row.get('points') or 0, 'history': history})

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
    rc  = get_r()
    row = _get(rc, 'players', pid)
    if not row:
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    current      = row.get('points') or 0
    new_val      = max(0, current + count)
    actual_delta = new_val - current
    _update(rc, 'players', pid, {'points': new_val})
    _log_points(rc, pid, actual_delta, reason)
    return jsonify({'ok': True, 'points': new_val, 'granted': actual_delta})

@app.route('/api/points/prizes')
def list_prizes():
    return jsonify(PRIZES_CATALOG)

@app.route('/api/players/<int:pid>/points/redeem', methods=['POST'])
def redeem_prize(pid):
    d         = request.json or {}
    prize     = PRIZE_BY_ID.get(d.get('prize_id'))
    if not prize:
        return jsonify({'error': 'Palkintoa ei löydy.'}), 404
    rc      = get_r()
    new_bal = _atomic_deduct_points(rc, pid, prize['cost'], f"Lunastus: {prize['label']}")
    if new_bal is None:
        return jsonify({'error': 'Ei tarpeeksi pisteitä.'}), 400
    if prize['kind'] == 'cash':
        _insert(rc, 'bonuses', {
            'player_id': pid, 'label': f"Pistelunastus: {prize['label']}",
            'amount': float(prize['amount']), 'claimed': 0, 'seen': 0,
        })
    elif prize['kind'] == 'spin':
        row = _get(rc, 'players', pid)
        if row:
            _update(rc, 'players', pid, {'spins_remaining': (row.get('spins_remaining') or 0) + int(prize['spins'])})
    return jsonify({'ok': True, 'points': new_bal, 'prize': prize})

# ─── Mini-games ──────────────────────────────────────────────────────────────

def _card_value_bj(rank):
    if rank in ('J','Q','K'): return 10
    if rank == 'A':           return 11
    return int(rank)

def _hand_total(cards):
    total = sum(_card_value_bj(c['rank']) for c in cards)
    aces  = sum(1 for c in cards if c['rank']=='A')
    while total > 21 and aces > 0:
        total -= 10; aces -= 1
    return total

@app.route('/api/points/<int:pid>/coinflip', methods=['POST'])
def game_coinflip(pid):
    d      = request.json or {}
    choice = (d.get('choice') or '').lower()
    if choice not in ('heads','tails'):
        return jsonify({'error': 'Valitse klaava tai kruuna.'}), 400
    rc = get_r()
    bet, err = _get_bet(d, pid, rc)
    if err: return err
    _atomic_deduct_points(rc, pid, bet, f'Kolikonheitto panos ({choice})')
    streak = _get_streak_mode(rc, pid)
    if streak == 'win':
        result = choice; payout = bet * 2
        _add_points(rc, pid, payout, f'Kolikonheitto voitto ({result})')
        outcome = 'win'
    elif streak == 'lose':
        result = 'tails' if choice == 'heads' else 'heads'
        outcome, payout = 'loss', 0
    else:
        result = 'heads' if random.random() < 0.50 else 'tails'
        if choice == result and random.random() < 0.96:
            payout = bet * 2
            _add_points(rc, pid, payout, f'Kolikonheitto voitto ({result})')
            outcome = 'win'
        else:
            outcome, payout = 'loss', 0
    bal = (_get(rc, 'players', pid) or {}).get('points') or 0
    return jsonify({'outcome': outcome, 'result': result, 'choice': choice,
                    'bet': bet, 'payout': payout, 'net': payout - bet, 'points': bal})

@app.route('/api/points/<int:pid>/war', methods=['POST'])
def game_war(pid):
    d  = request.json or {}
    rc = get_r()
    bet, err = _get_bet(d, pid, rc)
    if err: return err
    _atomic_deduct_points(rc, pid, bet, 'Sota-peli panos')
    deck = new_deck()
    pc, dc = deck.pop(), deck.pop()
    pv, dv = _RV[pc['rank']], _RV[dc['rank']]
    streak = _get_streak_mode(rc, pid)
    if streak == 'win':
        outcome = 'win';  payout = bet * 2; _add_points(rc, pid, payout, 'Sota-peli voitto')
    elif streak == 'lose':
        outcome = 'loss'; payout = 0
    elif pv > dv:
        outcome = 'win';  payout = bet * 2; _add_points(rc, pid, payout, 'Sota-peli voitto')
    elif pv < dv:
        outcome = 'loss'; payout = 0
    else:
        outcome = 'push'; payout = bet;     _add_points(rc, pid, payout, 'Sota-peli tasapeli')
    bal = (_get(rc, 'players', pid) or {}).get('points') or 0
    return jsonify({'outcome': outcome, 'player_card': pc, 'dealer_card': dc,
                    'bet': bet, 'payout': payout, 'net': payout - bet, 'points': bal})

@app.route('/api/points/<int:pid>/baccarat', methods=['POST'])
def game_baccarat(pid):
    d    = request.json or {}
    side = (d.get('side') or '').lower()
    if side not in ('player','banker','tie'):
        return jsonify({'error': 'Valitse: player, banker tai tie.'}), 400
    rc = get_r()
    bet, err = _get_bet(d, pid, rc)
    if err: return err
    _atomic_deduct_points(rc, pid, bet, f'Baccarat panos ({side})')
    def val(r):
        if r in ('J','Q','K','10'): return 0
        if r == 'A': return 1
        return int(r)
    deck = new_deck()
    p1, p2, b1, b2 = deck.pop(), deck.pop(), deck.pop(), deck.pop()
    ptot = (val(p1['rank']) + val(p2['rank'])) % 10
    btot = (val(b1['rank']) + val(b2['rank'])) % 10
    phand, bhand = [p1, p2], [b1, b2]
    if ptot < 8 and btot < 8:
        p3 = None
        if ptot <= 5:
            p3 = deck.pop(); phand.append(p3); ptot = (ptot + val(p3['rank'])) % 10
        draw_banker = False
        if p3 is None:
            draw_banker = btot <= 5
        else:
            p3v = val(p3['rank'])
            if   btot <= 2: draw_banker = True
            elif btot == 3: draw_banker = p3v != 8
            elif btot == 4: draw_banker = p3v in (2,3,4,5,6,7)
            elif btot == 5: draw_banker = p3v in (4,5,6,7)
            elif btot == 6: draw_banker = p3v in (6,7)
        if draw_banker:
            b3 = deck.pop(); bhand.append(b3); btot = (btot + val(b3['rank'])) % 10
    winner = 'player' if ptot > btot else ('banker' if btot > ptot else 'tie')
    streak = _get_streak_mode(rc, pid)
    if streak == 'win':
        winner = side
    elif streak == 'lose':
        winner = {'player':'banker','banker':'player'}.get(side, 'player')
    payout = 0
    if side == winner:
        if   side == 'player': payout = bet * 2
        elif side == 'banker': payout = bet + int(bet * 0.95)
        elif side == 'tie':    payout = bet * 9
        _add_points(rc, pid, payout, f'Baccarat voitto ({winner})')
        outcome = 'win'
    elif winner == 'tie' and side in ('player','banker'):
        payout = bet; _add_points(rc, pid, payout, 'Baccarat tasapeli (palautus)'); outcome = 'push'
    else:
        outcome = 'loss'
    bal = (_get(rc, 'players', pid) or {}).get('points') or 0
    return jsonify({'outcome': outcome, 'winner': winner, 'side': side,
                    'player_hand': phand, 'banker_hand': bhand,
                    'player_total': ptot, 'banker_total': btot,
                    'bet': bet, 'payout': payout, 'net': payout - bet, 'points': bal})

# ── Blackjack ──

def _bj_state(game):
    pcards  = json.loads(game['player_cards_json'])
    dcards  = json.loads(game['dealer_cards_json'])
    ins_bet = game.get('insurance_bet') or 0
    active  = game['status'] == 'active'
    return {
        'game_id':      game['id'],
        'bet':          game['bet'],
        'status':       game['status'],
        'player_cards': pcards,
        'dealer_cards': dcards if not active else [dcards[0]] + [{'rank':'?','suit':'?'}]*(len(dcards)-1),
        'player_total': _hand_total(pcards),
        'dealer_total': _hand_total(dcards) if not active else _hand_total([dcards[0]]),
        'insurance_available': (active and len(pcards)==2 and dcards[0]['rank']=='A' and ins_bet==0),
        'insurance_bet': ins_bet,
    }

@app.route('/api/points/<int:pid>/blackjack/start', methods=['POST'])
def game_bj_start(pid):
    d  = request.json or {}
    rc = get_r()
    _update_where(rc, 'blackjack_games', {'status': 'abandoned'}, player_id=pid, status='active')
    bet, err = _get_bet(d, pid, rc)
    if err: return err
    _atomic_deduct_points(rc, pid, bet, 'Blackjack panos')
    deck = new_deck()
    pc   = [deck.pop(), deck.pop()]
    dc   = [deck.pop(), deck.pop()]
    status = 'active'
    streak = _get_streak_mode(rc, pid)
    payout = 0
    if _hand_total(pc) == 21 and streak != 'lose':
        status = 'done_blackjack'
        payout = bet + int(bet * 1.5)
        _add_points(rc, pid, payout, 'Blackjack luonnollinen 21')
    gid = _insert(rc, 'blackjack_games', {
        'player_id': pid, 'bet': bet, 'deck_json': json.dumps(deck),
        'player_cards_json': json.dumps(pc), 'dealer_cards_json': json.dumps(dc),
        'status': status, 'insurance_bet': 0,
    })
    game  = _get(rc, 'blackjack_games', gid)
    state = _bj_state(game)
    state['points'] = (_get(rc, 'players', pid) or {}).get('points') or 0
    if status.startswith('done'):
        state.update({'outcome': 'blackjack', 'payout': payout, 'net': int(bet * 1.5)})
    return jsonify(state)

@app.route('/api/points/blackjack/<int:gid>/action', methods=['POST'])
def game_bj_action(gid):
    d      = request.json or {}
    action = (d.get('action') or '').lower()
    rc     = get_r()
    game   = _get(rc, 'blackjack_games', gid)
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

    if action == 'hit':
        pcards.append(deck.pop())
        status = 'done_bust' if _hand_total(pcards) > 21 else 'active'
        outcome = 'bust' if status == 'done_bust' else None
    elif action == 'stand':
        while _hand_total(dcards) < 17: dcards.append(deck.pop())
        ptot, dtot = _hand_total(pcards), _hand_total(dcards)
        if dtot > 21 or ptot > dtot:
            status='done_win';  outcome='win';  payout=bet*2
        elif ptot==dtot:
            status='done_push'; outcome='push'; payout=bet
        else:
            status='done_loss'; outcome='loss'
    elif action == 'double':
        if len(pcards) != 2:
            return jsonify({'error': 'Tuplaus vain ensimmäisellä vuorolla.'}), 400
        if _atomic_deduct_points(rc, pid, bet, 'Blackjack tuplaus') is None:
            return jsonify({'error': 'Ei tarpeeksi pisteitä tuplaukseen.'}), 400
        bet *= 2
        pcards.append(deck.pop())
        if _hand_total(pcards) > 21:
            status='done_bust'; outcome='bust'
        else:
            while _hand_total(dcards) < 17: dcards.append(deck.pop())
            ptot, dtot = _hand_total(pcards), _hand_total(dcards)
            if dtot > 21 or ptot > dtot:
                status='done_win';  outcome='win';  payout=bet*2
            elif ptot==dtot:
                status='done_push'; outcome='push'; payout=bet
            else:
                status='done_loss'; outcome='loss'
    elif action == 'insurance':
        if len(pcards) != 2:
            return jsonify({'error': 'Vakuutus on mahdollinen vain pelin alussa.'}), 400
        if dcards[0]['rank'] != 'A':
            return jsonify({'error': 'Vakuutus on mahdollinen vain kun jakajalla on ässä.'}), 400
        if game.get('insurance_bet', 0) > 0:
            return jsonify({'error': 'Vakuutus on jo otettu.'}), 400
        ins = max(1, bet // 2)
        if _atomic_deduct_points(rc, pid, ins, 'Blackjack vakuutuspanos') is None:
            return jsonify({'error': 'Ei tarpeeksi pisteitä vakuutukseen.'}), 400
        dealer_bj = _hand_total(dcards) == 21
        if dealer_bj:
            ins_payout = ins * 3
            _add_points(rc, pid, ins_payout, 'Blackjack vakuutus voitto')
            if _hand_total(pcards) == 21:
                _add_points(rc, pid, bet, 'Blackjack tasapeli (BJ vs BJ)')
                status='done_push'; outcome='push'; payout=bet
            else:
                status='done_loss'; outcome='loss'; payout=0
            if _get_streak_mode(rc, pid) == 'win' and outcome == 'loss':
                _add_points(rc, pid, bet*2, 'Blackjack voitto (streak)')
                outcome='win'; status='done_win'; payout=bet*2
            net_total = (ins_payout - ins) + (payout - bet)
        else:
            ins_payout=0; outcome=None; status='active'; net_total=-ins
        _update(rc, 'blackjack_games', gid, {'insurance_bet': ins, 'status': status,
                                              'deck_json': json.dumps(deck)})
        game  = _get(rc, 'blackjack_games', gid)
        state = _bj_state(game)
        state['points']           = (_get(rc,'players',pid) or {}).get('points') or 0
        state['dealer_has_bj']    = dealer_bj
        state['insurance_result'] = 'win' if dealer_bj else 'loss'
        state['insurance_payout'] = ins_payout
        state['insurance_amount'] = ins
        if dealer_bj and outcome:
            state.update({'outcome': outcome, 'payout': payout, 'net': net_total})
        else:
            state['net'] = net_total
        return jsonify(state)
    else:
        return jsonify({'error': 'Virheellinen toiminto.'}), 400

    if outcome is not None:
        streak = _get_streak_mode(rc, pid)
        if streak == 'lose' and outcome in ('win','push'):
            outcome='loss'; status='done_loss'; payout=0
        elif streak == 'win' and outcome in ('loss','bust'):
            outcome='win'; status='done_win'; payout=bet*2
    if payout > 0:
        _add_points(rc, pid, payout, f'Blackjack {outcome}')
    _update(rc, 'blackjack_games', gid, {
        'deck_json': json.dumps(deck), 'player_cards_json': json.dumps(pcards),
        'dealer_cards_json': json.dumps(dcards), 'status': status, 'bet': bet,
    })
    game  = _get(rc, 'blackjack_games', gid)
    state = _bj_state(game)
    state['points'] = (_get(rc,'players',pid) or {}).get('points') or 0
    if outcome:
        state.update({'outcome': outcome, 'payout': payout, 'net': payout - bet})
    return jsonify(state)

# ─── Slots ───────────────────────────────────────────────────────────────────

@app.route('/api/slots/jackpot', methods=['GET'])
def slots_jackpot():
    rc = get_r()
    try:
        pool = int(_setting_get(rc, JACKPOT_KEY) or JACKPOT_INITIAL)
    except (TypeError, ValueError):
        pool = JACKPOT_INITIAL
    if pool < JACKPOT_INITIAL:
        pool = JACKPOT_INITIAL
        _setting_set(rc, JACKPOT_KEY, str(pool))
    return jsonify({'pool': pool, 'seed': JACKPOT_INITIAL, 'rake_pct': JACKPOT_RAKE_PCT})


@app.route('/api/points/<int:pid>/slots', methods=['POST'])
def game_slots(pid):
    d        = request.json or {}
    rc       = get_r()
    bet, err = _get_bet(d, pid, rc)
    if err: return err
    theme_id = d.get('theme', 'fruits') if d.get('theme') in SLOT_THEMES else 'fruits'
    _atomic_deduct_points(rc, pid, bet, f'Slots panos ({theme_id})')

    streak = _get_streak_mode(rc, pid)
    theme  = SLOT_THEMES[theme_id]

    # 1. Contribute to the progressive jackpot pool (1 % of every bet, min 1).
    try:
        pool = int(_setting_get(rc, JACKPOT_KEY) or JACKPOT_INITIAL)
    except (TypeError, ValueError):
        pool = JACKPOT_INITIAL
    if pool < JACKPOT_INITIAL:
        pool = JACKPOT_INITIAL
    pool += max(1, int(bet * JACKPOT_RAKE_PCT))
    _setting_set(rc, JACKPOT_KEY, str(pool))

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

    # 5. Free-spins bonus (3+ scatters).
    free_spins_triggered = len(scatter_positions) >= 3
    fs_count   = theme['free_spins'] if free_spins_triggered else 0
    fs_mult    = theme['fs_mult']    if free_spins_triggered else 1
    fs_results = []
    bonus_payout = 0
    if free_spins_triggered:
        for _ in range(fs_count):
            fg = _slot_spin(theme_id, include_scatter=False)
            fw = _slot_calc_wins(fg, theme['payouts'])
            fm = sum(w['mult'] for w in fw)
            fp = int(round(bet * fm * fs_mult))
            bonus_payout += fp
            fs_results.append({'grid': fg, 'wins': fw, 'payout': fp})

    # 6. Progressive jackpot win — collect & reset the pool.
    jackpot_payout = 0
    if jackpot_won:
        jackpot_payout = pool
        pool = JACKPOT_INITIAL
        _setting_set(rc, JACKPOT_KEY, str(pool))

    total_payout = payout + bonus_payout + jackpot_payout
    if total_payout > 0:
        reason = f'Slots voitto ({theme_id})' + (' — JACKPOT!' if jackpot_won else '')
        _add_points(rc, pid, total_payout, reason)

    bal = (_get(rc, 'players', pid) or {}).get('points') or 0
    return jsonify({
        'grid':                 grid,
        'wins':                 wins,
        'total_mult':           total_mult,
        'bet':                  bet,
        'payout':               payout,
        'net':                  total_payout - bet,
        'points':               bal,
        'scatter_positions':    scatter_positions,
        'free_spins_triggered': free_spins_triggered,
        'free_spin_count':      fs_count,
        'free_spin_mult':       fs_mult,
        'free_spin_results':    fs_results,
        'bonus_payout':         bonus_payout,
        'jackpot_won':          jackpot_won,
        'jackpot_payout':       jackpot_payout,
        'jackpot_pool':         pool,
    })

# ─── Streak mode ─────────────────────────────────────────────────────────────

@app.route('/api/players/<int:pid>/streak', methods=['POST'])
def set_streak_mode(pid):
    d    = request.json or {}
    mode = d.get('mode','normal')
    if mode not in ('normal','win','lose'):
        return jsonify({'error': 'Virheellinen tila.'}), 400
    rc = get_r()
    if not _get(rc, 'players', pid):
        return jsonify({'error': 'Pelaajaa ei löydy.'}), 404
    _update(rc, 'players', pid, {'streak_mode': mode})
    return jsonify({'ok': True, 'streak_mode': mode})

# ─── System settings ─────────────────────────────────────────────────────────

@app.route('/api/settings', methods=['GET'])
def get_settings():
    rc  = get_r()
    out = dict(SETTINGS_DEFAULTS)
    out.update(rc.hgetall('settings') or {})
    return jsonify(out)

@app.route('/api/settings', methods=['PUT'])
def update_settings():
    d  = request.json or {}
    rc = get_r()
    for key, val in d.items():
        if key in SETTINGS_DEFAULTS:
            _setting_set(rc, key, str(val))
    return jsonify({'ok': True})

# ─── Cash redemption ─────────────────────────────────────────────────────────

@app.route('/api/players/<int:pid>/points/cash-redeem', methods=['POST'])
def cash_redeem(pid):
    d  = request.json or {}
    rc = get_r()
    try:
        pts = int(d.get('points', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Virheellinen pisteiden määrä.'}), 400
    min_pts = int(_setting_get(rc, 'min_redeem_pts'))
    max_pts = int(_setting_get(rc, 'max_redeem_pts'))
    ppu     = float(_setting_get(rc, 'points_per_eur'))
    if pts < min_pts:
        return jsonify({'error': f'Vähimmäislunastus on {min_pts} pistettä.'}), 400
    if pts > max_pts:
        return jsonify({'error': f'Enimmäislunastus on {max_pts} pistettä kerrallaan.'}), 400
    eur     = round(pts / ppu, 2)
    new_bal = _atomic_deduct_points(rc, pid, pts, f'Käteisnosto: {pts} p → €{eur:.2f}')
    if new_bal is None:
        return jsonify({'error': 'Ei tarpeeksi pisteitä.'}), 400
    _insert(rc, 'bonuses', {
        'player_id': pid, 'label': f'Pisteistä lunastettu: {pts} pistettä',
        'amount': float(eur), 'claimed': 0, 'seen': 0,
    })
    return jsonify({'ok': True, 'points': new_bal, 'eur': eur, 'pts_redeemed': pts})

# ─── Pikapokeri ───────────────────────────────────────────────────────────────

@app.route('/api/points/<int:pid>/pikapokeri/start', methods=['POST'])
def pikapokeri_start(pid):
    d  = request.json or {}
    rc = get_r()
    _update_where(rc, 'pikapokeri_games', {'status': 'abandoned'}, player_id=pid, status='deal')
    bet, err = _get_bet(d, pid, rc)
    if err: return err
    _atomic_deduct_points(rc, pid, bet, 'Pikapokeri panos')
    deck = new_deck()
    hand = [deck.pop() for _ in range(5)]
    gid  = _insert(rc, 'pikapokeri_games', {
        'player_id': pid, 'bet': bet, 'deck_json': json.dumps(deck),
        'hand_json': json.dumps(hand), 'status': 'deal', 'payout': 0,
        'result_rank': -1, 'result_name': '',
    })
    bal = (_get(rc,'players',pid) or {}).get('points') or 0
    return jsonify({'game_id': gid, 'hand': hand, 'bet': bet, 'status': 'deal', 'points': bal})

@app.route('/api/points/pikapokeri/<int:gid>/draw', methods=['POST'])
def pikapokeri_draw(gid):
    d    = request.json or {}
    hold = [int(i) for i in d.get('hold',[]) if str(i).isdigit()]
    rc   = get_r()
    game = _get(rc, 'pikapokeri_games', gid)
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
    streak = _get_streak_mode(rc, pid)
    if streak == 'lose' and mult > 0:
        rank, mult, result_name = -1, 0, 'Häviö'
    elif streak == 'win' and mult == 0:
        rank, mult, result_name = 3, 3, 'Kolmikko'
    payout = bet * mult
    if payout > 0:
        _add_points(rc, pid, payout, f'Pikapokeri voitto ({result_name})')
    _update(rc, 'pikapokeri_games', gid, {
        'hand_json': json.dumps(new_hand), 'deck_json': json.dumps(deck),
        'status': 'done', 'payout': payout, 'result_rank': rank, 'result_name': result_name,
    })
    bal = (_get(rc,'players',pid) or {}).get('points') or 0
    return jsonify({
        'hand': new_hand, 'rank': rank, 'result_name': result_name,
        'mult': mult, 'bet': bet, 'payout': payout, 'net': payout-bet,
        'outcome': 'win' if payout > 0 else 'loss', 'points': bal,
    })
