"""Backend test suite for the 3D redo: shared theme endpoints + 4 card games
(blackjack, baccarat, pikapokeri, casino war). Targets the public preview URL.

Runs against the live preview backend (see frontend/.env).
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    # fallback: read frontend/.env
    try:
        with open('/app/frontend/.env') as f:
            for ln in f:
                if ln.startswith('REACT_APP_BACKEND_URL='):
                    BASE_URL = ln.split('=', 1)[1].strip().rstrip('/')
    except Exception:
        pass
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"

PLAYER_ID = 1
CUSTOMER_PW = 'test123'
OPERATOR_PW = 'admin123'


# ───────────────────── fixtures ─────────────────────
@pytest.fixture(scope='session')
def api():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


@pytest.fixture(scope='session')
def op_token(api):
    r = api.post(f"{BASE_URL}/api/operator/login", json={'password': OPERATOR_PW})
    assert r.status_code == 200, f"op login failed: {r.status_code} {r.text}"
    tok = r.json().get('access_token')
    assert tok and isinstance(tok, str)
    return tok


@pytest.fixture
def op_headers(op_token):
    return {'Authorization': f'Bearer {op_token}'}


@pytest.fixture(autouse=True)
def _ensure_balance(api):
    """Make sure the test player has plenty of points before each test."""
    r = api.get(f"{BASE_URL}/api/players/{PLAYER_ID}/points")
    if r.status_code == 200:
        bal = r.json().get('points', 0)
        if bal < 5000:
            api.post(f"{BASE_URL}/api/players/{PLAYER_ID}/points/grant",
                     json={'count': 5000, 'reason': 'TEST_topup'})
    yield


# ───────────────────── THEME ─────────────────────
class TestTheme:
    """Public theme endpoint + operator-protected update."""

    def test_get_theme_returns_defaults(self, api):
        r = api.get(f"{BASE_URL}/api/theme")
        assert r.status_code == 200
        d = r.json()
        # must contain the brand + key colors
        for k in ('theme_brand_name', 'theme_primary', 'theme_bg', 'theme_surface', 'theme_text'):
            assert k in d, f"missing {k} in /api/theme"
        assert d['theme_primary'].startswith('#')

    def test_put_theme_requires_auth(self, api):
        r = api.put(f"{BASE_URL}/api/operator/theme", json={'theme_primary': '#123456'})
        assert r.status_code == 401

    def test_put_theme_rejects_bad_token(self, api):
        r = api.put(f"{BASE_URL}/api/operator/theme",
                    json={'theme_primary': '#123456'},
                    headers={'Authorization': 'Bearer nope'})
        assert r.status_code == 401

    def test_put_theme_persists_and_resets(self, api, op_headers):
        # 1) read current
        original = api.get(f"{BASE_URL}/api/theme").json()
        # 2) set a new primary
        new_color = '#ff6b6b'
        new_brand = 'TEST_BrandX'
        r = api.put(f"{BASE_URL}/api/operator/theme",
                    json={'theme_primary': new_color, 'theme_brand_name': new_brand},
                    headers=op_headers)
        assert r.status_code == 200
        body = r.json()
        assert body.get('ok') is True
        assert body['theme']['theme_primary'] == new_color
        assert body['theme']['theme_brand_name'] == new_brand
        # 3) verify via public GET
        re_get = api.get(f"{BASE_URL}/api/theme").json()
        assert re_get['theme_primary'] == new_color
        assert re_get['theme_brand_name'] == new_brand
        # 4) restore originals
        api.put(f"{BASE_URL}/api/operator/theme",
                json={'theme_primary': original['theme_primary'],
                      'theme_brand_name': original['theme_brand_name']},
                headers=op_headers)
        restored = api.get(f"{BASE_URL}/api/theme").json()
        assert restored['theme_primary'] == original['theme_primary']
        assert restored['theme_brand_name'] == original['theme_brand_name']


# ───────────────────── OPERATOR AUTH ─────────────────────
class TestOperatorAuth:
    def test_login_wrong_pw(self, api):
        r = api.post(f"{BASE_URL}/api/operator/login", json={'password': 'wrong'})
        assert r.status_code == 401

    def test_login_correct(self, api):
        r = api.post(f"{BASE_URL}/api/operator/login", json={'password': OPERATOR_PW})
        assert r.status_code == 200
        d = r.json()
        assert 'access_token' in d
        assert isinstance(d['access_token'], str) and len(d['access_token']) > 20

    def test_me_requires_token(self, api):
        r = api.get(f"{BASE_URL}/api/operator/me")
        assert r.status_code == 401

    def test_me_with_token(self, api, op_headers):
        r = api.get(f"{BASE_URL}/api/operator/me", headers=op_headers)
        assert r.status_code == 200
        assert r.json().get('ok') is True


# ───────────────────── CUSTOMER LOGIN + BALANCE ─────────────────────
class TestCustomerSession:
    def test_customer_login(self, api):
        r = api.post(f"{BASE_URL}/api/customer/login",
                     json={'name': 'Pelaaja', 'password': CUSTOMER_PW})
        assert r.status_code == 200, r.text
        d = r.json()
        # accept either 'id' or 'player_id' key
        assert d.get('id') == PLAYER_ID or d.get('player_id') == PLAYER_ID

    def test_get_points(self, api):
        r = api.get(f"{BASE_URL}/api/players/{PLAYER_ID}/points")
        assert r.status_code == 200
        d = r.json()
        assert 'points' in d
        assert isinstance(d['points'], int)


# ───────────────────── BLACKJACK 3D ─────────────────────
class TestBlackjack:
    def test_start_and_stand(self, api):
        r = api.post(f"{BASE_URL}/api/points/{PLAYER_ID}/blackjack/start",
                     json={'bet': 100})
        assert r.status_code == 200, r.text
        d = r.json()
        assert 'game_id' in d
        p = d.get('player_cards') or d.get('player') or []
        de = d.get('dealer_cards') or d.get('dealer') or []
        assert len(p) == 2 and len(de) >= 1
        gid = d['game_id']
        # stand to resolve
        r2 = api.post(f"{BASE_URL}/api/points/blackjack/{gid}/action",
                      json={'action': 'stand'})
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2.get('status') in ('done', 'complete', 'finished') or 'outcome' in d2 or 'result' in d2


# ───────────────────── BACCARAT ─────────────────────
class TestBaccarat:
    def test_play_player_bet(self, api):
        r = api.post(f"{BASE_URL}/api/points/{PLAYER_ID}/baccarat",
                     json={'bet': 100, 'side': 'player'})
        assert r.status_code == 200, r.text
        d = r.json()
        # must have hands + outcome
        assert 'player' in d or 'player_hand' in d
        assert 'banker' in d or 'banker_hand' in d
        # the outcome/winner field
        winner = d.get('winner') or d.get('outcome') or d.get('result')
        assert winner is not None

    def test_play_banker(self, api):
        r = api.post(f"{BASE_URL}/api/points/{PLAYER_ID}/baccarat",
                     json={'bet': 100, 'side': 'banker'})
        assert r.status_code == 200


# ───────────────────── PIKAPOKERI ─────────────────────
class TestPikapokeri:
    def test_start_then_draw(self, api):
        r = api.post(f"{BASE_URL}/api/points/{PLAYER_ID}/pikapokeri/start",
                     json={'bet': 100})
        assert r.status_code == 200, r.text
        d = r.json()
        assert 'game_id' in d
        # hand of 5 cards
        hand = d.get('hand') or d.get('cards') or d.get('player')
        assert hand and len(hand) == 5
        gid = d['game_id']
        # hold none, draw 5
        r2 = api.post(f"{BASE_URL}/api/points/pikapokeri/{gid}/draw",
                      json={'hold': [False, False, False, False, False]})
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        final = d2.get('hand') or d2.get('cards')
        assert final and len(final) == 5
        assert 'result_name' in d2 or 'result' in d2 or 'rank' in d2


# ───────────────────── CASINO WAR ─────────────────────
class TestWar:
    def test_play(self, api):
        # play several to maybe hit a tie too
        outcomes = []
        for _ in range(3):
            r = api.post(f"{BASE_URL}/api/points/{PLAYER_ID}/war",
                         json={'bet': 100})
            assert r.status_code == 200, r.text
            d = r.json()
            assert 'player_card' in d or 'player' in d
            assert 'dealer_card' in d or 'dealer' in d
            outcomes.append(d.get('outcome') or d.get('winner') or d.get('result'))
        # should always return SOME outcome string
        assert all(o is not None for o in outcomes)
