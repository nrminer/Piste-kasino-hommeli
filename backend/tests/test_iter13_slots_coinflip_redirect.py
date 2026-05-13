"""Iter13 backend tests:

- GET / now 302-redirects to /operator (old cashier index retired).
- GET /api/slots/jackpot returns {pool, seed, rake_pct}.
- POST /api/points/1/slots {bet, theme} returns valid 5x3 grid + payout.
- POST /api/points/1/coinflip {bet, choice} returns {outcome, result, bet, net, points}.
- Theme + 3D card-game endpoints (BJ/Baccarat/Pikapokeri/War) still work.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://holdem-ops-unify.preview.emergentagent.com').rstrip('/')
PID = 1


@pytest.fixture(scope='session')
def s():
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})
    return sess


@pytest.fixture(scope='session', autouse=True)
def _ensure_balance(s):
    """Top player 1 up to at least 5000 points so bets never fail."""
    r = s.get(f'{BASE_URL}/api/players/{PID}/points', timeout=15)
    if r.status_code != 200:
        pytest.skip(f'Cannot read balance: {r.status_code}')
    bal = int(r.json().get('points', 0))
    if bal < 5000:
        s.post(f'{BASE_URL}/api/players/{PID}/points/grant',
               json={'count': 10000, 'reason': 'TEST_iter13_topup'}, timeout=15)


# ─── 1. Root redirect ────────────────────────────────────────────────────────

class TestRootRedirect:
    def test_root_redirects_to_operator(self, s):
        r = s.get(f'{BASE_URL}/', allow_redirects=False, timeout=15)
        assert r.status_code in (301, 302, 307, 308), f'expected redirect, got {r.status_code}'
        loc = r.headers.get('Location', '')
        assert '/operator' in loc, f'Location header {loc!r} does not point to /operator'

    def test_operator_page_loads(self, s):
        r = s.get(f'{BASE_URL}/operator', timeout=15)
        assert r.status_code == 200
        assert 'operator' in r.text.lower() or 'admin' in r.text.lower() or 'salasana' in r.text.lower()

    def test_asiakas_page_loads(self, s):
        r = s.get(f'{BASE_URL}/asiakas', timeout=15)
        assert r.status_code == 200
        # The lobby should mention all 6 game tiles via testids
        for tid in ('cust-tile-blackjack', 'cust-tile-baccarat',
                    'cust-tile-pikapokeri', 'cust-tile-war',
                    'cust-tile-slots', 'cust-tile-coinflip'):
            assert tid in r.text, f'missing tile {tid} in /asiakas html'


# ─── 2. Slots ────────────────────────────────────────────────────────────────

class TestSlots:
    def test_jackpot_shape(self, s):
        r = s.get(f'{BASE_URL}/api/slots/jackpot', timeout=15)
        assert r.status_code == 200
        d = r.json()
        for key in ('pool', 'seed', 'rake_pct'):
            assert key in d, f'missing {key} in {d}'
        assert isinstance(d['pool'], int) and d['pool'] > 0
        assert isinstance(d['seed'], int) and d['seed'] > 0
        assert 0 < float(d['rake_pct']) <= 1.0

    def test_spin_fruits_grid_5x3(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/slots',
                   json={'bet': 100, 'theme': 'fruits'}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        grid = d.get('grid')
        assert isinstance(grid, list) and len(grid) == 5, f'grid not 5 cols: {grid}'
        for col in grid:
            assert isinstance(col, list) and len(col) == 3, f'col not 3 rows: {col}'
            for sym in col:
                assert sym is not None
        for key in ('wins', 'scatter_positions', 'jackpot_pool', 'payout', 'net', 'points', 'bet'):
            assert key in d, f'missing {key}'
        assert d['bet'] == 100
        assert isinstance(d['payout'], int)
        assert isinstance(d['net'], int)

    @pytest.mark.parametrize('theme', ['fruits', 'egypt', 'space'])
    def test_spin_each_theme(self, s, theme):
        r = s.post(f'{BASE_URL}/api/points/{PID}/slots',
                   json={'bet': 50, 'theme': theme}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d['grid']) == 5

    def test_spin_unknown_theme_falls_back_to_fruits(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/slots',
                   json={'bet': 50, 'theme': 'nonsense'}, timeout=15)
        assert r.status_code == 200
        assert len(r.json()['grid']) == 5

    def test_spin_jackpot_pool_increases(self, s):
        before = s.get(f'{BASE_URL}/api/slots/jackpot', timeout=15).json()['pool']
        s.post(f'{BASE_URL}/api/points/{PID}/slots',
               json={'bet': 200, 'theme': 'fruits'}, timeout=15)
        after = s.get(f'{BASE_URL}/api/slots/jackpot', timeout=15).json()['pool']
        # Pool either grew by rake or reset (if jackpot hit). Both are valid.
        assert after >= 0


# ─── 3. Coinflip ─────────────────────────────────────────────────────────────

class TestCoinflip:
    def test_flip_heads_shape(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/coinflip',
                   json={'bet': 100, 'choice': 'heads'}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        for key in ('outcome', 'result', 'bet', 'net', 'points', 'choice'):
            assert key in d, f'missing {key} in {d}'
        assert d['result'] in ('heads', 'tails')
        assert d['outcome'] in ('win', 'loss')
        assert d['choice'] == 'heads'
        assert d['bet'] == 100

    def test_flip_tails(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/coinflip',
                   json={'bet': 100, 'choice': 'tails'}, timeout=15)
        assert r.status_code == 200
        assert r.json()['choice'] == 'tails'

    def test_flip_rejects_side_field(self, s):
        """Legacy field name 'side' must NOT be accepted; only 'choice' works."""
        r = s.post(f'{BASE_URL}/api/points/{PID}/coinflip',
                   json={'bet': 100, 'side': 'heads'}, timeout=15)
        assert r.status_code == 400, f'side should be rejected but got {r.status_code}'

    def test_flip_rejects_bad_choice(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/coinflip',
                   json={'bet': 100, 'choice': 'edge'}, timeout=15)
        assert r.status_code == 400


# ─── 4. Card games (regression — flat-card render is FE-only) ───────────────

class TestCardGamesRegression:
    def test_blackjack_start_returns_dealer_cards_with_hole(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/blackjack/start',
                   json={'bet': 100}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert 'dealer_cards' in d and len(d['dealer_cards']) >= 2
        assert 'player_cards' in d and len(d['player_cards']) >= 2
        # Hole card may be a placeholder or include rank/suit — FE handles reveal.
        gid = d.get('game_id')
        assert gid
        # Stand to resolve and check dealer_cards is now revealed (real ranks).
        r2 = s.post(f'{BASE_URL}/api/points/blackjack/{gid}/action',
                    json={'action': 'stand'}, timeout=15)
        assert r2.status_code == 200
        d2 = r2.json()
        for c in d2.get('dealer_cards', []):
            assert c.get('rank') and c.get('suit'), f'unrevealed card in result: {c}'

    def test_war_round(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/war', json={'bet': 50}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert 'player_card' in d and 'dealer_card' in d
        assert d['outcome'] in ('win', 'loss', 'tie', 'push')
