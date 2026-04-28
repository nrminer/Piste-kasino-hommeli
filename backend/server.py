"""
FastAPI wrapper that mounts the Flask casino-manager app for the Emergent preview
environment. The Flask app (../app.py) keeps using SQLite locally; for Vercel
production the entry-point is /app/api/index.py (Redis/Upstash KV).

Why this wrapper exists:
  - Supervisor expects a uvicorn-launched ASGI app at /app/backend/server.py
  - The original project is Flask (WSGI). FastAPI's WSGIMiddleware bridges the two.
"""
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware

# Make the project root importable so `app` (Flask) can be loaded as-is.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Import the Flask casino app (defines all /api/* and HTML routes).
from app import app as flask_app  # noqa: E402

app = FastAPI(title="Kasinon Hallinta — Local Wrapper")

# Mount the entire Flask app at the root. ASGI requests are translated to WSGI.
app.mount("/", WSGIMiddleware(flask_app))
