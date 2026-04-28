# 🎰 Kasinon Hallinta — Casino Manager (Finnish)

A self-hosted casino floor manager built with Flask. Track players, transactions,
bonuses and points; run a live Texas Hold'em table from a tablet; let customers
log in from their phone to see their balance, claim bonuses, spin a prize wheel
and play mini-games (Blackjack, Pikapokeri, Slots, Coinflip, Baccarat, War).

## Pages

| Path           | Audience            | Purpose                                  |
|----------------|---------------------|------------------------------------------|
| `/`            | Cashier / Manager   | Dashboard, players, poker dealer screen  |
| `/asiakas`     | Customer (phone)    | Login, bonuses, points, mini-games       |
| `/poker/join`  | Poker player tablet | Join active table, see your hole cards   |

## Run locally

```bash
pip install -r requirements.txt
python app.py             # SQLite store, http://localhost:5000
```

## Deploy to Vercel

Everything is wired up — just make sure your Vercel project has a Redis-style
KV attached so the data survives cold starts.

### 1. Push to GitHub and import the repo into Vercel

The `vercel.json` already points the runtime at `api/index.py` (Python 3.11)
and includes the `templates/` folder with the function bundle.

### 2. Attach a KV (Redis) store

In **Vercel → Storage → Create**, pick any of:

- **Upstash for Redis** (recommended — free tier works)
- **Vercel KV** (legacy — still supported)
- Any Redis with a `redis://` or `rediss://` URL

Connect it to your project. Vercel automatically injects one of these env vars
which the app picks up in this order: `KV_URL`, `REDIS_URL`, `UPSTASH_REDIS_URL`.

### 3. Re-deploy

That's it. The `/api/_health` endpoint reports the active storage backend:

```bash
curl https://<your-app>.vercel.app/api/_health
# { "ok": true, "storage": { "mode": "redis", "reason": "" } }
```

### Preview without KV

If no KV is attached the app still boots — it falls back to a per-instance
in-memory store so you can browse the UI. **Data does not persist** across cold
starts in this mode, so don't forget step 2 before going to production.

## Environment variables

| Variable                    | Required        | Purpose                            |
|-----------------------------|-----------------|------------------------------------|
| `KV_URL` / `REDIS_URL`      | Production      | Redis/Upstash connection string    |
| `UPSTASH_REDIS_URL`         | Optional        | Alternative name for the same      |
| `VERCEL_URL`                | Auto by Vercel  | Used as a hostname fallback        |

## Tech stack

- **Backend:** Flask 3 (single-file `app.py` for SQLite local dev, mirrored
  Redis-backed `api/index.py` for Vercel)
- **Storage:** SQLite locally, Redis/Upstash KV on Vercel, in-memory fallback
- **Frontend:** Server-rendered HTML + vanilla JS (no build step)
- **PWA:** `/manifest.json` lets `/asiakas` install as an app on iOS/Android

## Project layout

```
app.py              Flask app for local dev (SQLite)
api/index.py        Flask app for Vercel (Redis/KV + in-memory fallback)
templates/          HTML pages (shared between both runtimes)
vercel.json         Vercel routing + Python runtime config
requirements.txt    Python deps for Vercel
backend/, frontend/ Emergent preview-environment glue (NOT deployed to Vercel)
```
