"""
Operator panel authentication.

Verifies the password from the OPERATOR_PASSWORD env variable, issues a
short-lived JWT (HS256) signed with OPERATOR_TOKEN_SECRET, and exposes a
`@op_required` decorator that protects admin endpoints by validating the
`Authorization: Bearer <token>` header.

No secrets are hard-coded — everything comes from /app/.env (loaded via
python-dotenv) or the shell environment.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any

import jwt
from flask import Blueprint, Flask, jsonify, request


bp = Blueprint("operator_auth", __name__)


def _settings() -> dict[str, Any]:
    return {
        "password": os.environ.get("OPERATOR_PASSWORD", ""),
        "secret": os.environ.get("OPERATOR_TOKEN_SECRET", ""),
        "ttl_min": int(os.environ.get("OPERATOR_TOKEN_TTL_MIN", "60") or 60),
    }


def _issue_token() -> tuple[str, datetime]:
    s = _settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s["ttl_min"])
    payload = {
        "sub": "operator",
        "role": "operator",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, s["secret"], algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token, exp


def _decode_token(token: str) -> dict[str, Any] | None:
    s = _settings()
    try:
        payload = jwt.decode(token, s["secret"], algorithms=["HS256"])
        if payload.get("sub") != "operator":
            return None
        return payload
    except jwt.PyJWTError:
        return None


def op_required(fn):
    """Protect a Flask view: require a valid operator JWT in the
    `Authorization: Bearer …` header. Returns 401 on missing/invalid token."""

    @wraps(fn)
    def wrapped(*args, **kwargs):
        hdr = request.headers.get("Authorization", "")
        if not hdr.lower().startswith("bearer "):
            return jsonify({"error": "Operator token vaaditaan."}), 401
        token = hdr[7:].strip()
        if not token or _decode_token(token) is None:
            return jsonify({"error": "Virheellinen tai vanhentunut token."}), 401
        return fn(*args, **kwargs)

    return wrapped


@bp.route("/api/operator/login", methods=["POST"])
def op_login():
    s = _settings()
    if not s["password"] or not s["secret"]:
        return jsonify({"error": "OPERATOR_PASSWORD / OPERATOR_TOKEN_SECRET ei asetettu palvelimella."}), 500
    d = request.get_json(silent=True) or {}
    pw = (d.get("password") or "").strip()
    if not pw:
        return jsonify({"error": "Salasana vaaditaan."}), 400
    # Constant-time string compare to resist timing attacks.
    import hmac as _hmac
    if not _hmac.compare_digest(pw, s["password"]):
        return jsonify({"error": "Väärä salasana."}), 401
    token, exp = _issue_token()
    return jsonify({
        "access_token": token,
        "token_type": "bearer",
        "expires_at": exp.isoformat(),
        "ttl_minutes": s["ttl_min"],
    })


@bp.route("/api/operator/me", methods=["GET"])
@op_required
def op_me():
    hdr = request.headers.get("Authorization", "")
    payload = _decode_token(hdr[7:]) or {}
    return jsonify({
        "ok": True,
        "role": payload.get("role"),
        "exp": payload.get("exp"),
    })


def register(app: Flask) -> None:
    """Wire the blueprint into the host Flask app + load .env once."""
    try:
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=False)
    except Exception:
        pass
    app.register_blueprint(bp)
