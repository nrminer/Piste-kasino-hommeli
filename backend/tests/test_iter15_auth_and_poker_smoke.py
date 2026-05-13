"""Iteration 15 smoke tests for customer/operator auth and poker state flows."""

import os

import pytest
import requests


# Customer and operator authentication modules
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")


@pytest.fixture(scope="session")
def api_base_url():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL is not set")
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def session_client():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="session")
def operator_token(session_client, api_base_url):
    resp = session_client.post(
        f"{api_base_url}/api/operator/login",
        json={"password": "operator123"},
        timeout=20,
    )
    if resp.status_code != 200:
        pytest.skip(f"Operator login unavailable: {resp.status_code} {resp.text}")
    data = resp.json()
    token = data.get("access_token")
    assert isinstance(token, str) and token
    return token


def test_customer_login_success(session_client, api_base_url):
    resp = session_client.post(
        f"{api_base_url}/api/customer/login",
        json={"name": "Test Player", "password": "test123"},
        timeout=20,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test Player"
    assert isinstance(data.get("id"), int)
    assert isinstance(data.get("points"), int)


def test_customer_login_invalid_password(session_client, api_base_url):
    resp = session_client.post(
        f"{api_base_url}/api/customer/login",
        json={"name": "Test Player", "password": "wrong-password"},
        timeout=20,
    )
    assert resp.status_code in (400, 401)
    data = resp.json()
    assert any(k in data for k in ("error", "message", "detail"))


def test_operator_login_success(session_client, api_base_url):
    resp = session_client.post(
        f"{api_base_url}/api/operator/login",
        json={"password": "operator123"},
        timeout=20,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("access_token"), str)
    assert data.get("access_token")


def test_operator_me_with_bearer_token(session_client, api_base_url, operator_token):
    resp = session_client.get(
        f"{api_base_url}/api/operator/me",
        headers={"Authorization": f"Bearer {operator_token}"},
        timeout=20,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("ok"), bool)


def test_poker_state_shape(session_client, api_base_url):
    resp = session_client.get(f"{api_base_url}/api/poker/state", timeout=20)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("status"), str)
    assert "seats" in data
    assert isinstance(data.get("seats"), list)
