/**
 * Tiny reverse proxy: forwards every incoming request on port 3000 to the
 * Flask backend running on port 8001. This lets the Emergent preview
 * environment serve the original Flask HTML pages (/, /asiakas,
 * /poker/join, /manifest.json) while the ingress separately routes /api/*
 * straight to the same backend.
 */
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');

const PORT       = parseInt(process.env.PORT, 10) || 3000;
const HOST       = process.env.HOST || '0.0.0.0';
const TARGET     = process.env.BACKEND_TARGET || 'http://127.0.0.1:8001';

// HTTP status returned when the upstream Flask backend is unreachable
// (e.g. during a supervisor-driven restart). Surfaced as a friendly Finnish
// "warming up" page rather than the default node-proxy stack trace.
const BAD_GATEWAY_STATUS = 502;

const app = express();

// Lightweight health probe — returns 200 even if the backend is restarting.
app.get('/__proxy_health', (_req, res) => res.json({ ok: true, target: TARGET }));

app.use(
  '/',
  createProxyMiddleware({
    target: TARGET,
    changeOrigin: true,
    ws: true,
    xfwd: true,
    logLevel: 'warn',
    onError(err, _req, res) {
      if (!res.headersSent) {
        res.writeHead(BAD_GATEWAY_STATUS, { 'Content-Type': 'text/html; charset=utf-8' });
      }
      res.end(
        `<!doctype html><meta charset="utf-8"><title>Backend not ready</title>` +
        `<body style="font-family:system-ui;background:#0d1f17;color:#f0ead8;` +
        `display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0">` +
        `<div style="text-align:center"><h1 style="color:#c9a84c">Käynnistetään…</h1>` +
        `<p>Palvelin lämpenee. Päivitä hetken kuluttua.</p>` +
        `<p style="opacity:.5;font-size:.8em">${err.code || ''}</p></div></body>`
      );
    },
  })
);

app.listen(PORT, HOST);
