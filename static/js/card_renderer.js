/*!
 * CardRenderer — Auditor's Ledger casino card rendering engine
 * Vanilla JS + Canvas2D, ImageBitmap-cached card sprites (~4.2 MB / 53 entries).
 *
 * Usage:
 *     const canvas = document.getElementById('table-canvas');
 *     const ctx = canvas.getContext('2d');
 *     const renderer = new CardRenderer(ctx);
 *     renderer.drawCard(ctx, { rank: 'A', suit: '♠' }, 100, 100, true, 1.0, 0);
 *     renderer.drawChip(ctx, 200, 200, 500, 1.0);
 *     renderer.drawChipStack(ctx, 300, 200, [{denomination:100},{denomination:500}]);
 *     renderer.animateCardFlip({ rank: 'K', suit: '♥' }, 0, 0, 400, 300, 700, () => console.log('done'));
 *
 * Standalone (no template surgery required). Optional integration via:
 *     <script src="/static/js/card_renderer.js"></script>
 */
(function (global) {
  'use strict';

  // ─────────────────────────────────────────────────────────────────────────────
  // Pip layout table for ranks 2-10. Each entry is a list of [x_rel, y_rel] pip
  // positions where (0,0) = top-left of card face area, (1,1) = bottom-right.
  // ─────────────────────────────────────────────────────────────────────────────
  const PIP_LAYOUT_TABLE = {
    2:  [[0.50, 0.20], [0.50, 0.80]],
    3:  [[0.50, 0.20], [0.50, 0.50], [0.50, 0.80]],
    4:  [[0.30, 0.20], [0.70, 0.20], [0.30, 0.80], [0.70, 0.80]],
    5:  [[0.30, 0.20], [0.70, 0.20], [0.50, 0.50], [0.30, 0.80], [0.70, 0.80]],
    6:  [[0.30, 0.20], [0.70, 0.20], [0.30, 0.50], [0.70, 0.50], [0.30, 0.80], [0.70, 0.80]],
    7:  [[0.30, 0.20], [0.70, 0.20], [0.50, 0.32], [0.30, 0.50], [0.70, 0.50], [0.30, 0.80], [0.70, 0.80]],
    8:  [[0.30, 0.20], [0.70, 0.20], [0.50, 0.30], [0.30, 0.50], [0.70, 0.50], [0.50, 0.70], [0.30, 0.80], [0.70, 0.80]],
    9:  [[0.30, 0.20], [0.70, 0.20], [0.30, 0.40], [0.70, 0.40], [0.50, 0.50], [0.30, 0.60], [0.70, 0.60], [0.30, 0.80], [0.70, 0.80]],
    10: [[0.30, 0.18], [0.70, 0.18], [0.50, 0.30], [0.30, 0.40], [0.70, 0.40], [0.30, 0.60], [0.70, 0.60], [0.50, 0.70], [0.30, 0.82], [0.70, 0.82]],
  };

  // ─────────────────────────────────────────────────────────────────────────────
  // SVG path data for the four suits (48×48 viewbox, anchored to pip render box).
  // ─────────────────────────────────────────────────────────────────────────────
  const SUIT_SVG_PATHS = {
    '♠': 'M24 6 C 30 16 42 18 42 28 C 42 36 32 38 26 32 L 24 38 L 30 44 L 18 44 L 24 38 C 18 38 6 36 6 28 C 6 18 18 16 24 6 Z',
    '♥': 'M24 42 C 36 32 46 24 46 16 Q 46 6 36 6 C 30 6 24 12 24 16 C 24 12 18 6 12 6 Q 2 6 2 16 C 2 24 12 32 24 42 Z',
    '♦': 'M24 4 L 44 24 L 24 44 L 4 24 Z',
    '♣': 'M24 14 m -8 0 a 8 8 0 1 0 16 0 a 8 8 0 1 0 -16 0 M14 26 m -8 0 a 8 8 0 1 0 16 0 a 8 8 0 1 0 -16 0 M34 26 m -8 0 a 8 8 0 1 0 16 0 a 8 8 0 1 0 -16 0 M24 28 L 18 44 L 30 44 Z',
  };

  const SUIT_COLORS_DEFAULT = { '♠': '#0A0A0A', '♥': '#CC2200', '♦': '#CC2200', '♣': '#0A0A0A' };
  const SUIT_COLORS_PROTAN_DEUTAN = { '♠': '#0A0A0A', '♥': '#E87040', '♦': '#0066CC', '♣': '#006644' };
  const SUIT_COLORS_TRITAN = { '♠': '#0A0A0A', '♥': '#CC2200', '♦': '#009900', '♣': '#CC9900' };

  // Chip palette per denomination tier
  const CHIP_PALETTE = {
    100:   { body: '#F2EAD3', spot: '#B91C1C', text: '#0A0A0A' },
    500:   { body: '#B91C1C', spot: '#F2EAD3', text: '#FFFFFF' },
    1000:  { body: '#0F5132', spot: '#F2EAD3', text: '#FFFFFF' },
    5000:  { body: '#0A0A0A', spot: '#B91C1C', text: '#FFFFFF' },
    10000: { body: '#5B2C6F', spot: '#FFB800', text: '#FFFFFF' },
  };

  // ─────────────────────────────────────────────────────────────────────────────
  // CardRenderer class
  // ─────────────────────────────────────────────────────────────────────────────
  class CardRenderer {
    constructor(ctx, options) {
      this.ctx = ctx;
      const opts = options || {};
      this.cardW = opts.cardWidth || 120;
      this.cardH = opts.cardHeight || 168;
      this.colorblindMode = opts.colorblindMode || 'default';
      this.spriteCache = new Map();
      this.SUIT_SVG_PATHS = SUIT_SVG_PATHS;
      this.PIP_LAYOUT_TABLE = PIP_LAYOUT_TABLE;
      this._supportsOffscreenCanvas = typeof OffscreenCanvas !== 'undefined';
    }

    setColorblindMode(mode) {
      if (mode !== this.colorblindMode) {
        this.colorblindMode = mode;
        this.spriteCache.clear();
      }
    }

    _suitColors() {
      switch (this.colorblindMode) {
        case 'protanopia_deuteranopia': return SUIT_COLORS_PROTAN_DEUTAN;
        case 'tritanopia':              return SUIT_COLORS_TRITAN;
        default:                        return SUIT_COLORS_DEFAULT;
      }
    }

    _cacheKey(card, faceUp) {
      if (!faceUp) return 'BACK';
      return card.rank + '_' + card.suit + '_' + this.colorblindMode;
    }

    _newOffscreen(w, h) {
      if (this._supportsOffscreenCanvas) return new OffscreenCanvas(w, h);
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      return c;
    }

    _renderCardToOffscreen(card, faceUp) {
      const off = this._newOffscreen(this.cardW, this.cardH);
      const octx = off.getContext('2d');
      if (faceUp) {
        // Card face: linen-finish off-white
        octx.fillStyle = '#FBF8EE';
        octx.fillRect(0, 0, this.cardW, this.cardH);
        octx.strokeStyle = '#0A0A0A';
        octx.lineWidth = 1.5;
        octx.strokeRect(0.75, 0.75, this.cardW - 1.5, this.cardH - 1.5);
        this._drawCornerIndex(octx, card, 8, 10, false);
        this._drawCornerIndex(octx, card, this.cardW - 8, this.cardH - 10, true);
        const rankNum = parseInt(card.rank, 10);
        if (!isNaN(rankNum) && rankNum >= 2 && rankNum <= 10) {
          const layout = this.PIP_LAYOUT_TABLE[rankNum];
          const pipSize = 0.13 * this.cardW;
          for (const [px, py] of layout) {
            const cx = px * this.cardW;
            const cy = py * this.cardH;
            this._drawSuitPip(octx, card.suit, cx, cy, pipSize);
          }
        } else {
          this._drawFaceCard(octx, card);
        }
      } else {
        this._drawCardBack(octx);
      }
      if (typeof off.transferToImageBitmap === 'function') {
        return off.transferToImageBitmap();
      }
      return off;
    }

    _drawCardBack(octx) {
      // Navy cloth-weave back with gold foil border
      octx.fillStyle = '#1A2440';
      octx.fillRect(0, 0, this.cardW, this.cardH);
      // Cloth-weave pattern via 16×16 tile
      octx.fillStyle = '#2A3550';
      for (let x = 0; x < this.cardW; x += 4) {
        for (let y = 0; y < this.cardH; y += 4) {
          if (((x / 4) + (y / 4)) % 2 === 0) {
            octx.fillRect(x, y, 2, 2);
          }
        }
      }
      // Gold foil border
      octx.strokeStyle = '#C9A227';
      octx.lineWidth = 2.5;
      octx.strokeRect(6, 6, this.cardW - 12, this.cardH - 12);
      // Inner crest area
      octx.strokeStyle = '#9B7B2E';
      octx.lineWidth = 1;
      octx.strokeRect(20, 30, this.cardW - 40, this.cardH - 60);
      // AL crest
      octx.fillStyle = '#C9A227';
      octx.font = 'bold 24px "Chivo", "Chivo 900", sans-serif';
      octx.textAlign = 'center';
      octx.textBaseline = 'middle';
      octx.fillText('AL', this.cardW / 2, this.cardH / 2);
    }

    _drawCornerIndex(octx, card, x, y, isBottomRight) {
      octx.save();
      const colors = this._suitColors();
      octx.fillStyle = colors[card.suit] || '#0A0A0A';
      if (isBottomRight) {
        octx.translate(x, y);
        octx.rotate(Math.PI);
      } else {
        octx.translate(x, y);
      }
      octx.font = 'bold 16px "JetBrains Mono", monospace';
      octx.textAlign = 'left';
      octx.textBaseline = 'top';
      octx.fillText(card.rank, 0, 0);
      this._drawSuitPip(octx, card.suit, 6, 22, 8);
      octx.restore();
    }

    _drawSuitPip(octx, suit, cx, cy, sz) {
      const colors = this._suitColors();
      octx.save();
      octx.translate(cx - sz / 2, cy - sz / 2);
      octx.scale(sz / 48, sz / 48);
      octx.fillStyle = colors[suit] || '#0A0A0A';
      try {
        const path = new Path2D(this.SUIT_SVG_PATHS[suit] || '');
        octx.fill(path);
      } catch (e) {
        octx.fillRect(0, 0, 48, 48);
      }
      octx.restore();
    }

    _drawFaceCard(octx, card) {
      const colors = this._suitColors();
      const colorMap = { 'J': '#1F3A93', 'Q': '#9B7B2E', 'K': '#5B2C6F', 'A': '#0A0A0A' };
      octx.fillStyle = '#FBF8EE';
      octx.fillRect(20, 32, this.cardW - 40, this.cardH - 64);
      octx.strokeStyle = colorMap[card.rank] || '#0A0A0A';
      octx.lineWidth = 2;
      octx.strokeRect(22, 34, this.cardW - 44, this.cardH - 68);
      octx.fillStyle = colors[card.suit] || colorMap[card.rank] || '#0A0A0A';
      octx.font = 'bold 56px "Chivo", "Chivo 900", sans-serif';
      octx.textAlign = 'center';
      octx.textBaseline = 'middle';
      octx.fillText(card.rank, this.cardW / 2, this.cardH / 2);
      this._drawSuitPip(octx, card.suit, this.cardW / 2, this.cardH / 2 + 36, 18);
    }

    // ─── Public API ────────────────────────────────────────────────────────────
    drawCard(ctx, card, x, y, faceUp, scale, rotation) {
      faceUp = faceUp !== false;
      scale = scale || 1;
      rotation = rotation || 0;
      const key = this._cacheKey(card || { rank: '?', suit: '?' }, faceUp);
      if (!this.spriteCache.has(key)) {
        this.spriteCache.set(key, this._renderCardToOffscreen(card || { rank: '?', suit: '?' }, faceUp));
      }
      const sprite = this.spriteCache.get(key);
      const w = this.cardW * scale;
      const h = this.cardH * scale;
      ctx.save();
      ctx.translate(x + w / 2, y + h / 2);
      if (rotation) ctx.rotate(rotation);
      ctx.drawImage(sprite, -w / 2, -h / 2, w, h);
      ctx.restore();
    }

    animateCardFlip(card, startX, startY, endX, endY, duration, onComplete) {
      const startTs = (typeof performance !== 'undefined' && performance.now)
        ? performance.now()
        : Date.now();
      const ctx = this.ctx;
      const self = this;
      const tick = (now) => {
        const t = Math.min(1, (now - startTs) / duration);
        const easeT = 1 - Math.pow(1 - t, 3);
        const x = startX + (endX - startX) * easeT;
        const y = startY + (endY - startY) * easeT - 60 * Math.sin(Math.PI * t);
        const flipProgress = Math.min(1, Math.max(0, (t - 0.5) * 2));
        const faceUp = flipProgress > 0.5;
        const yScale = Math.max(0.05, Math.abs(Math.cos(Math.PI * flipProgress)));
        ctx.save();
        ctx.translate(x + self.cardW / 2, y + self.cardH / 2);
        ctx.scale(1, yScale);
        self.drawCard(ctx, card, -self.cardW / 2, -self.cardH / 2, faceUp, 1, 0);
        ctx.restore();
        if (t < 1) {
          requestAnimationFrame(tick);
        } else if (typeof onComplete === 'function') {
          onComplete();
        }
      };
      requestAnimationFrame(tick);
    }

    drawChip(ctx, x, y, denomination, scale) {
      scale = scale || 1;
      const palette = CHIP_PALETTE[denomination] || CHIP_PALETTE[100];
      const r = 32 * scale;
      ctx.save();
      ctx.translate(x, y);
      // Body radial gradient
      const grad = ctx.createRadialGradient(-r * 0.3, -r * 0.4, 0, 0, 0, r);
      grad.addColorStop(0, '#F5EFD5');
      grad.addColorStop(0.4, palette.body);
      grad.addColorStop(1, this._darken(palette.body, 0.4));
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = '#0A0A0A';
      ctx.stroke();
      // 8 edge spots
      ctx.fillStyle = palette.spot;
      for (let i = 0; i < 8; i++) {
        const angle = (i / 8) * Math.PI * 2;
        const sx = Math.cos(angle) * r * 0.78;
        const sy = Math.sin(angle) * r * 0.78;
        ctx.save();
        ctx.translate(sx, sy);
        ctx.rotate(angle);
        ctx.fillRect(-6 * scale, -3 * scale, 12 * scale, 6 * scale);
        ctx.restore();
      }
      // Denomination text (hot-stamped gold)
      ctx.fillStyle = '#C9A227';
      ctx.font = 'bold ' + Math.round(11 * scale) + 'px "Chivo", "Chivo 900", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(denomination), 0, 0);
      ctx.restore();
    }

    drawChipStack(ctx, x, y, chips) {
      if (!chips || chips.length === 0) return;
      chips.forEach((chip, idx) => {
        const offsetY = -idx * 6;
        const jitterDeg = ((this._hashJitter(idx, x, y) - 0.5) * 4);
        ctx.save();
        ctx.translate(x, y + offsetY);
        ctx.rotate((jitterDeg * Math.PI) / 180);
        this.drawChip(ctx, 0, 0, chip.denomination || 100, chip.scale || 1);
        ctx.restore();
      });
    }

    _hashJitter(i, x, y) {
      const seed = (i * 9301 + (x | 0) * 49297 + (y | 0) * 233280) % 233280;
      return Math.abs(seed) / 233280;
    }

    _darken(hex, factor) {
      const c = hex.replace('#', '');
      const r = parseInt(c.substring(0, 2), 16);
      const g = parseInt(c.substring(2, 4), 16);
      const b = parseInt(c.substring(4, 6), 16);
      const dr = Math.max(0, Math.round(r * (1 - factor)));
      const dg = Math.max(0, Math.round(g * (1 - factor)));
      const db = Math.max(0, Math.round(b * (1 - factor)));
      return '#' + ((dr << 16) | (dg << 8) | db).toString(16).padStart(6, '0');
    }

    clearCache() {
      this.spriteCache.clear();
    }

    cacheStats() {
      return { entries: this.spriteCache.size, cardW: this.cardW, cardH: this.cardH };
    }
  }

  // Expose globally for inline templates and module-style usage
  global.CardRenderer = CardRenderer;
  global.CARD_RENDERER_PIP_LAYOUT = PIP_LAYOUT_TABLE;
  global.CARD_RENDERER_SUIT_PATHS = SUIT_SVG_PATHS;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { CardRenderer, PIP_LAYOUT_TABLE, SUIT_SVG_PATHS, CHIP_PALETTE };
  }
})(typeof window !== 'undefined' ? window : this);
