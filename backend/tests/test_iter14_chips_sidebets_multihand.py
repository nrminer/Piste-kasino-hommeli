"""Iter14 backend regression tests:

Focus areas:
1. POST /api/points/<pid>/baccarat now accepts side bet keys
   (player_pair_pts, banker_pair_pts, either_pair_pts) and returns
   side_bets, side_bets_net, total_net.
2. POST /api/points/<pid>/blackjack/<gid>/sidebet remains intact
   (Perfect Pairs + 21+3).
3. POST /api/points/<pid>/blackjack/start still works (multi-hand BJ is
   implemented by FE issuing N parallel /start calls — verify the endpoint
   still hands out a fresh game per call).
4. /static/js/casino_3d.js no longer contains chip-mesh code
   (makeChipMesh / chipsGroup) and CylinderGeometry is absent.
5. Carry-over: lobby + slots + coinflip still healthy (smoke).
"""
import os
import re
import pytest
import requests

BASE_URL = os.environ.get(
    'REACT_APP_BACKEND_URL',
    'https://card-game-suite-3d.preview.emergentagent.com'
).rstrip('/')
PID = 1


@pytest.fixture(scope='session')
def s():
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})
    return sess


@pytest.fixture(scope='session', autouse=True)
def _ensure_balance(s):
    """Top player 1 up to at least 20 000 points so multi-hand + side-bet
    tests never fail mid-flight."""
    r = s.get(f'{BASE_URL}/api/players/{PID}/points', timeout=15)
    if r.status_code != 200:
        pytest.skip(f'Cannot read balance: {r.status_code}')
    bal = int(r.json().get('points', 0))
    if bal < 20000:
        s.post(f'{BASE_URL}/api/players/{PID}/points/grant',
               json={'count': 30000, 'reason': 'TEST_iter14_topup'}, timeout=15)


# ─── 1. Baccarat side bets ───────────────────────────────────────────────────

class TestBaccaratSideBets:
    """game_baccarat extension: player_pair_pts / banker_pair_pts /
    either_pair_pts. Pays 11:1 / 11:1 / 5:1 respectively."""

    def test_baccarat_with_all_three_side_bets_returns_side_dict(self, s):
        body = {
            'bet': 100, 'side': 'player',
            'player_pair_pts': 50,
            'banker_pair_pts': 50,
            'either_pair_pts': 50,
        }
        r = s.post(f'{BASE_URL}/api/points/{PID}/baccarat', json=body, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        # Must include extended fields
        for k in ('side_bets', 'side_bets_net', 'total_net',
                  'player_hand', 'banker_hand', 'outcome'):
            assert k in data, f'missing key {k} in response: {data.keys()}'
        # All three side bets should be resolved
        sb = data['side_bets']
        assert 'player_pair' in sb and 'banker_pair' in sb and 'either_pair' in sb
        # Each entry has won/payout/bet
        for k, v in sb.items():
            assert set(['won', 'payout', 'bet']).issubset(v.keys()), v
            assert isinstance(v['won'], bool)
            assert v['bet'] == 50
            if v['won']:
                # 11:1 → 12× for pair-side, 5:1 → 6× for either
                assert v['payout'] in (50 * 12, 50 * 6), (k, v)
            else:
                assert v['payout'] == 0
        # total_net = main_net + side_bets_net
        main_net = data['net']
        assert data['total_net'] == main_net + data['side_bets_net']

    def test_baccarat_without_side_bets_returns_empty_side_dict(self, s):
        body = {'bet': 100, 'side': 'banker'}
        r = s.post(f'{BASE_URL}/api/points/{PID}/baccarat', json=body, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data['side_bets'] == {}
        assert data['side_bets_net'] == 0
        assert data['total_net'] == data['net']

    def test_baccarat_insufficient_balance_for_main_plus_side(self, s):
        # Read balance, then craft a request that exceeds it
        bal = int(s.get(f'{BASE_URL}/api/players/{PID}/points').json()['points'])
        huge = bal + 5000
        body = {'bet': huge, 'side': 'player', 'player_pair_pts': 10}
        r = s.post(f'{BASE_URL}/api/points/{PID}/baccarat', json=body, timeout=15)
        # Either the main-bet validator fires first (400) or side validator does
        assert r.status_code == 400, r.text

    def test_baccarat_negative_side_bet_rejected(self, s):
        body = {'bet': 100, 'side': 'player', 'player_pair_pts': -5}
        r = s.post(f'{BASE_URL}/api/points/{PID}/baccarat', json=body, timeout=15)
        assert r.status_code == 400, r.text
        assert 'Player Pair' in r.text or 'pelaaja' in r.text.lower() or 'sivu' in r.text.lower()

    def test_baccarat_side_bet_payout_matches_dealt_pairs(self, s):
        """Run multiple rounds; whenever player_pair fires, payout MUST be 12×
        stake; whenever banker_pair fires, 12×; whenever EITHER pair, 6×."""
        rounds = 12
        any_paid = False
        for _ in range(rounds):
            body = {'bet': 50, 'side': 'player',
                    'player_pair_pts': 25, 'banker_pair_pts': 25,
                    'either_pair_pts': 25}
            r = s.post(f'{BASE_URL}/api/points/{PID}/baccarat', json=body, timeout=15)
            assert r.status_code == 200, r.text
            d = r.json()
            phand, bhand = d['player_hand'], d['banker_hand']
            pp = len(phand) >= 2 and phand[0]['rank'] == phand[1]['rank']
            bp = len(bhand) >= 2 and bhand[0]['rank'] == bhand[1]['rank']
            sb = d['side_bets']
            assert sb['player_pair']['won'] == pp
            assert sb['banker_pair']['won'] == bp
            assert sb['either_pair']['won'] == (pp or bp)
            if sb['player_pair']['won']:
                assert sb['player_pair']['payout'] == 25 * 12; any_paid = True
            if sb['banker_pair']['won']:
                assert sb['banker_pair']['payout'] == 25 * 12; any_paid = True
            if sb['either_pair']['won']:
                assert sb['either_pair']['payout'] == 25 * 6;  any_paid = True
        # Not strictly required, but very likely (~10% pair rate × 12 rounds × 3 sides)
        # Comment as a soft check only.
        _ = any_paid


# ─── 2. Blackjack side bets endpoint (unchanged) ─────────────────────────────

class TestBlackjackSideBetsEndpoint:
    def test_bj_sidebet_pp_and_213_returns_resolved(self, s):
        # 1) Start a BJ hand
        r = s.post(f'{BASE_URL}/api/points/{PID}/blackjack/start',
                   json={'bet': 100}, timeout=15)
        assert r.status_code == 200, r.text
        gid = r.json()['game_id']
        # 2) Post side bets
        r2 = s.post(f'{BASE_URL}/api/points/{PID}/blackjack/{gid}/sidebet',
                    json={'perfect_pairs_pts': 50,
                          'twenty_one_plus_three_pts': 50},
                    timeout=15)
        assert r2.status_code == 200, r2.text
        data = r2.json()
        assert data.get('ok') is True
        assert 'resolved' in data
        # Resolved keys
        assert 'perfect_pairs' in data['resolved']
        assert 'twenty_one_plus_three' in data['resolved']
        for k in ('perfect_pairs', 'twenty_one_plus_three'):
            entry = data['resolved'][k]
            assert 'won' in entry and 'payout' in entry and 'bet' in entry
            assert entry['bet'] == 50


# ─── 3. Blackjack multi-hand uses parallel /start — endpoint sanity ─────────

class TestBlackjackMultiHandStart:
    def test_three_independent_start_calls_yield_three_game_ids(self, s):
        ids = []
        for _ in range(3):
            r = s.post(f'{BASE_URL}/api/points/{PID}/blackjack/start',
                       json={'bet': 50}, timeout=15)
            assert r.status_code == 200, r.text
            j = r.json()
            assert 'game_id' in j
            assert 'player_cards' in j and 'dealer_cards' in j
            assert len(j['player_cards']) == 2
            ids.append(j['game_id'])
        assert len(set(ids)) == 3, f'game_ids not unique: {ids}'


# ─── 4. Static asset: chips fully removed from casino_3d.js ─────────────────

class TestNoChipsInThreeJs:
    @pytest.fixture(scope='class')
    def js(self, s):
        r = s.get(f'{BASE_URL}/static/js/casino_3d.js', timeout=15)
        assert r.status_code == 200, r.text[:200]
        return r.text

    def test_no_chip_mesh_factory(self, js):
        assert 'function makeChipMesh' not in js
        # Any remaining `Chip` token should be in a comment that says removed/no-op
        # (we just assert no mesh-building function exists)
        assert 'makeChipMesh(' not in js

    def test_no_chips_group(self, js):
        # chipsGroup variable creation must be gone
        assert re.search(r'\bchipsGroup\s*=\s*new\s+THREE\.Group', js) is None
        assert re.search(r'scene\.add\(\s*chipsGroup\s*\)', js) is None

    def test_no_cylinder_geometry(self, js):
        # CylinderGeometry was only used for chips. Should be absent.
        assert 'CylinderGeometry' not in js, \
            'CylinderGeometry still referenced — chips might not be fully removed'

    def test_setchipstack_is_noop(self, js):
        # The function still exists for backwards compat but its body must
        # be effectively empty (no THREE.* calls inside it).
        m = re.search(r'function\s+setChipStack[^{]*\{([^}]*)\}', js)
        assert m, 'setChipStack function not found'
        body = m.group(1)
        # Only comments / whitespace allowed
        assert 'THREE.' not in body
        assert 'add(' not in body


# ─── 5. Carry-over smoke (lobby, slots, coinflip) ────────────────────────────

class TestCarryOverSmoke:
    def test_root_still_redirects_to_operator(self, s):
        r = s.get(f'{BASE_URL}/', allow_redirects=False, timeout=10)
        assert r.status_code in (301, 302), r.status_code
        assert '/operator' in r.headers.get('Location', '')

    def test_slots_spin_still_works(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/slots',
                   json={'bet': 50, 'theme': 'fruits'}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert 'grid' in d and len(d['grid']) == 5
        assert all(len(col) == 3 for col in d['grid'])

    def test_coinflip_still_works(self, s):
        r = s.post(f'{BASE_URL}/api/points/{PID}/coinflip',
                   json={'bet': 50, 'choice': 'heads'}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get('result') in ('heads', 'tails')
