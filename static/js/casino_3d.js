/**
 * AUDITOR'S LEDGER · Three.js card-table helpers
 * ----------------------------------------------------------------------------
 * Self-contained ESM module that paints a casino table + cards + chips in 3D
 * inside any container. Used by Blackjack, Baccarat, Pikapokeri and War on
 * /asiakas. No external state — every consumer creates a `TableScene` and
 * drives it imperatively.
 *
 * Public surface (see end of file):
 *   - `createTableScene(container, opts)` → returns { dealCard, flipCard,
 *     clearTable, setBanner, dispose, getCardCount }
 *   - `cardLabel(card)` → 'A♠'-style short label
 *
 * Implementation notes:
 *   - Cards are flat BoxGeometry with a CanvasTexture for the face and a
 *     procedural back texture (so we don't need any image assets).
 *   - Deal animation is a quadratic Bezier from a hidden "deck" anchor to the
 *     target slot, plus a flip on Y. Easing is cubic.
 *   - Colors read live from CSS custom properties (`--felt`, `--primary`) via
 *     getComputedStyle so an operator theme change immediately re-paints felt
 *     and chip stripes.
 *   - Camera angle is fixed at ~38° looking down for a clean "in front of the
 *     table" framing.
 */

import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';

/* ─── CSS variable bridge ─────────────────────────────────────────────── */
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/* ─── Card texture (canvas → CanvasTexture) ───────────────────────────── */
const SUIT_GLYPH = { '♠': '♠', '♣': '♣', '♥': '♥', '♦': '♦' };
const SUIT_COLOR = { '♠': '#0c0c0c', '♣': '#0c0c0c', '♥': '#c24a3a', '♦': '#c24a3a' };

/** Render a card face onto a 256×360 canvas. */
function buildCardFaceTexture(rank, suit) {
  const w = 256, h = 360;
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d');
  // background
  g.fillStyle = '#f6f0e0';
  g.fillRect(0, 0, w, h);
  // gold inner frame
  g.strokeStyle = '#cfb47a';
  g.lineWidth = 4;
  g.strokeRect(8, 8, w - 16, h - 16);
  const color = SUIT_COLOR[suit] || '#0c0c0c';
  const glyph = SUIT_GLYPH[suit] || '?';
  g.fillStyle = color;
  // top-left rank+suit
  g.textAlign = 'left';
  g.textBaseline = 'top';
  g.font = 'bold 56px serif';
  g.fillText(rank, 22, 22);
  g.font = '52px serif';
  g.fillText(glyph, 22, 80);
  // bottom-right mirrored
  g.save();
  g.translate(w - 22, h - 22);
  g.rotate(Math.PI);
  g.font = 'bold 56px serif';
  g.fillText(rank, 0, 0);
  g.font = '52px serif';
  g.fillText(glyph, 0, 58);
  g.restore();
  // big center glyph
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.font = 'bold 180px serif';
  g.fillText(glyph, w / 2, h / 2 + 6);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

/** Render the card back pattern using the current theme primary color. */
function buildCardBackTexture() {
  const w = 256, h = 360;
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d');
  // deep base
  g.fillStyle = '#11161e';
  g.fillRect(0, 0, w, h);
  // primary-tinted border
  const primary = cssVar('--primary', '#c9a84c');
  g.strokeStyle = primary;
  g.lineWidth = 5;
  g.strokeRect(10, 10, w - 20, h - 20);
  g.strokeStyle = primary + '44';
  g.lineWidth = 1;
  g.strokeRect(20, 20, w - 40, h - 40);
  // diamond grid
  g.fillStyle = primary + '22';
  for (let y = 30; y < h - 30; y += 18) {
    for (let x = 30; x < w - 30; x += 18) {
      const offset = ((y / 18) | 0) % 2 ? 9 : 0;
      g.beginPath();
      g.moveTo(x + offset, y);
      g.lineTo(x + offset + 6, y + 9);
      g.lineTo(x + offset, y + 18);
      g.lineTo(x + offset - 6, y + 9);
      g.closePath();
      g.fill();
    }
  }
  // monogram
  g.fillStyle = primary;
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.font = 'bold 56px serif';
  g.fillText('♠', w / 2, h / 2);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

/* ─── Card mesh ───────────────────────────────────────────────────────── */
const CARD_W = 0.7;
const CARD_H = 0.98;
const CARD_T = 0.012;
let cachedBack = null;

function makeCardMesh(rank, suit) {
  if (!cachedBack) cachedBack = buildCardBackTexture();
  const faceTex = buildCardFaceTexture(rank, suit);
  // BoxGeometry face order: [+x, -x, +y, -y, +z, -z]
  // +z is the "front" face we'll show after flipping; -z is the back.
  const matFront = new THREE.MeshStandardMaterial({ map: faceTex, roughness: .35, metalness: .05 });
  const matBack  = new THREE.MeshStandardMaterial({ map: cachedBack, roughness: .35, metalness: .05 });
  const edge     = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: .8 });
  const geom = new THREE.BoxGeometry(CARD_W, CARD_H, CARD_T);
  const mesh = new THREE.Mesh(geom, [edge, edge, edge, edge, matFront, matBack]);
  mesh.castShadow = true;
  mesh.receiveShadow = false;
  mesh.userData = { rank, suit, isFlipped: false };
  // start face-down (back facing camera)
  mesh.rotation.y = Math.PI;
  return mesh;
}

/* ─── Chip mesh (single chip) ─────────────────────────────────────────── */
function makeChipMesh(color) {
  const geom = new THREE.CylinderGeometry(0.18, 0.18, 0.04, 32);
  const mat  = new THREE.MeshStandardMaterial({ color, roughness: .45, metalness: .15 });
  const mesh = new THREE.Mesh(geom, mat);
  mesh.rotation.x = Math.PI / 2;   // lay flat
  mesh.castShadow = true;
  return mesh;
}

/* ─── Tween helpers (no GSAP — small custom rAF tween) ────────────────── */
function tween({ duration, from, to, onUpdate, onComplete, easing }) {
  const ease = easing || (t => 1 - Math.pow(1 - t, 3)); // easeOutCubic
  const start = performance.now();
  function step() {
    const t = Math.min(1, (performance.now() - start) / duration);
    const k = ease(t);
    const value = {};
    for (const key in from) value[key] = from[key] + (to[key] - from[key]) * k;
    onUpdate(value);
    if (t < 1) requestAnimationFrame(step);
    else if (onComplete) onComplete();
  }
  step();
}

/** Quadratic Bezier in 3D. */
function bezier3(p0, p1, p2, t) {
  const u = 1 - t;
  return new THREE.Vector3(
    u * u * p0.x + 2 * u * t * p1.x + t * t * p2.x,
    u * u * p0.y + 2 * u * t * p1.y + t * t * p2.y,
    u * u * p0.z + 2 * u * t * p1.z + t * t * p2.z,
  );
}

/* ─── Card slot layout ────────────────────────────────────────────────── */
/**
 * Convert (zoneName, indexInZone) → world position on the table.
 * Zones: 'player', 'dealer', 'banker', 'community', 'split0', 'split1'
 */
function slotPosition(zone, index) {
  // Cards lay out left-to-right starting at the player/dealer "anchor" x.
  const x = index * 0.78 - 1.2;
  const positions = {
    dealer:    { x: x + 0.6, y: 1.1, z: -1.0 },
    player:    { x: x + 0.6, y: 1.1, z:  1.0 },
    banker:    { x: x + 0.7, y: 1.1, z: -1.0 },
    community: { x: x + 0.4, y: 1.1, z:  1.0 },
    split0:    { x: x + 0.6, y: 1.1, z:  1.0 },
    split1:    { x: x + 0.6, y: 1.1, z:  2.2 },
  };
  return positions[zone] || positions.player;
}

/* ─── Main table scene ────────────────────────────────────────────────── */
export function createTableScene(container, opts = {}) {
  const showChips = opts.showChips !== false;

  const scene = new THREE.Scene();

  // Camera
  const aspect = container.clientWidth / Math.max(1, container.clientHeight);
  const camera = new THREE.PerspectiveCamera(46, aspect, 0.1, 50);
  camera.position.set(0, 5.0, 4.6);
  camera.lookAt(0, 0.8, 0);

  // Renderer
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);
  container.appendChild(renderer.domElement);

  // Lights
  const ambient = new THREE.AmbientLight(0xffffff, 0.45);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xfff2cc, 1.1);
  key.position.set(4, 6, 4);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.near = 1;
  key.shadow.camera.far = 20;
  key.shadow.camera.left = -6;
  key.shadow.camera.right = 6;
  key.shadow.camera.top = 6;
  key.shadow.camera.bottom = -6;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xa3c4ff, 0.25);
  rim.position.set(-4, 4, -3);
  scene.add(rim);

  // Felt table (rounded plane)
  const feltGeom = new THREE.PlaneGeometry(8, 5.2, 1, 1);
  const feltMat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(cssVar('--felt', '#1a2a22')),
    roughness: 0.92,
    metalness: 0.0,
  });
  const felt = new THREE.Mesh(feltGeom, feltMat);
  felt.rotation.x = -Math.PI / 2;
  felt.position.y = 1.0;
  felt.receiveShadow = true;
  scene.add(felt);

  // Felt edge (subtle gold inner border via line segments)
  const borderGeom = new THREE.EdgesGeometry(new THREE.BoxGeometry(7.6, 0.02, 4.8));
  const borderMat = new THREE.LineBasicMaterial({
    color: new THREE.Color(cssVar('--primary', '#c9a84c')),
    transparent: true, opacity: 0.35,
  });
  const border = new THREE.LineSegments(borderGeom, borderMat);
  border.position.y = 1.005;
  scene.add(border);

  // Deck stack anchor (cards spawn here)
  const deckPos = new THREE.Vector3(2.6, 1.05, 0.3);

  // Build a small physical deck for visual reference
  const deckGroup = new THREE.Group();
  for (let i = 0; i < 12; i++) {
    const slab = new THREE.Mesh(
      new THREE.BoxGeometry(CARD_W, CARD_H, CARD_T),
      new THREE.MeshStandardMaterial({
        map: cachedBack || (cachedBack = buildCardBackTexture()),
        roughness: .4,
      })
    );
    slab.rotation.y = Math.PI;
    slab.position.set(deckPos.x, deckPos.y + i * CARD_T, deckPos.z);
    slab.castShadow = true;
    deckGroup.add(slab);
  }
  scene.add(deckGroup);

  // Chip stack (left of player area). Hidden if showChips=false.
  const chipsGroup = new THREE.Group();
  chipsGroup.position.set(-1.6, 1.02, 1.6);
  scene.add(chipsGroup);

  // Resize observer
  const ro = new ResizeObserver(() => {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  });
  ro.observe(container);

  // Animate loop
  let rafId = 0;
  function loop() {
    rafId = requestAnimationFrame(loop);
    // Subtle parallax based on container hover (skip if no events captured)
    renderer.render(scene, camera);
  }
  loop();

  /* ── Public API ───────────────────────────────────────────────────── */

  // Card registry (per zone we track which cards are currently on the table).
  const cards = {};

  /** Deal a card to a zone with animation. Returns a promise that resolves
   *  after the deal animation completes (before flip). */
  function dealCard(zone, index, faceUp = false, card = null) {
    return new Promise((resolve) => {
      // If card data is missing, use a back-only mesh
      const mesh = card
        ? makeCardMesh(card.rank, card.suit)
        : makeCardMesh('?', '♠');
      // start at deck anchor, slight stack offset
      const start = new THREE.Vector3(deckPos.x, deckPos.y + 0.5, deckPos.z);
      mesh.position.copy(start);
      mesh.rotation.set(0, Math.PI, 0); // face-down
      scene.add(mesh);
      const dst = slotPosition(zone, index);
      // Bezier control: high arc above the table
      const ctrl = new THREE.Vector3(
        (start.x + dst.x) / 2,
        Math.max(start.y, dst.y) + 1.5,
        (start.z + dst.z) / 2,
      );
      const fromAngle = mesh.rotation.y;
      const toAngle = fromAngle + Math.PI * 0.5; // slight rotation in flight
      tween({
        duration: 480,
        from: { t: 0 },
        to:   { t: 1 },
        onUpdate({ t }) {
          const p = bezier3(start, ctrl, dst, t);
          mesh.position.copy(p);
          mesh.rotation.y = fromAngle + (toAngle - fromAngle) * t;
          mesh.rotation.z = Math.sin(t * Math.PI) * 0.4;
        },
        onComplete() {
          mesh.position.set(dst.x, dst.y + 0.005, dst.z);
          mesh.rotation.set(0, Math.PI, 0);
          mesh.userData.isFlipped = false;
          (cards[zone] = cards[zone] || []).push(mesh);
          if (faceUp) {
            flipCard(zone, index).then(resolve);
          } else {
            resolve();
          }
        },
      });
    });
  }

  /** Flip a card from face-down to face-up (or vice versa). */
  function flipCard(zone, index) {
    return new Promise((resolve) => {
      const mesh = (cards[zone] || [])[index];
      if (!mesh) return resolve();
      const from = mesh.rotation.y;
      const to = mesh.userData.isFlipped ? Math.PI : 0;
      tween({
        duration: 340,
        from: { y: from },
        to:   { y: to },
        onUpdate({ y }) {
          mesh.rotation.y = y;
          // small lift during flip
          const t = Math.abs((to - from) === 0 ? 0 : (y - from) / (to - from));
          mesh.position.y = 1.105 + Math.sin(t * Math.PI) * 0.18;
        },
        onComplete() {
          mesh.rotation.y = to;
          mesh.position.y = 1.105;
          mesh.userData.isFlipped = !mesh.userData.isFlipped;
          resolve();
        },
      });
    });
  }

  /** Remove all cards from the table. */
  function clearTable() {
    for (const zone in cards) {
      for (const mesh of cards[zone]) {
        scene.remove(mesh);
        mesh.geometry.dispose();
        if (Array.isArray(mesh.material)) mesh.material.forEach(m => m.map && m.map.dispose());
      }
      cards[zone] = [];
    }
  }

  /** Replace cards in a zone (used for video poker where we want to swap a
   *  specific card after the player draws). */
  async function replaceCard(zone, index, card) {
    const old = (cards[zone] || [])[index];
    if (old) {
      scene.remove(old);
      old.geometry.dispose();
    }
    const mesh = makeCardMesh(card.rank, card.suit);
    const dst = slotPosition(zone, index);
    mesh.position.set(deckPos.x, deckPos.y + 0.5, deckPos.z);
    mesh.rotation.set(0, Math.PI, 0);
    scene.add(mesh);
    (cards[zone] = cards[zone] || []);
    cards[zone][index] = mesh;
    const start = mesh.position.clone();
    const ctrl = new THREE.Vector3((start.x + dst.x) / 2, start.y + 1.5, (start.z + dst.z) / 2);
    await new Promise(r => tween({
      duration: 380,
      from: { t: 0 }, to: { t: 1 },
      onUpdate({ t }) {
        const p = bezier3(start, ctrl, dst, t);
        mesh.position.copy(p);
        mesh.rotation.z = Math.sin(t * Math.PI) * 0.3;
      },
      onComplete() {
        mesh.position.set(dst.x, dst.y + 0.005, dst.z);
        mesh.rotation.z = 0;
        r();
      },
    }));
    await flipCard(zone, index);
  }

  /** Set or clear the chip stack height — visually represents the bet. */
  function setChipStack(amount) {
    while (chipsGroup.children.length) {
      const m = chipsGroup.children.pop();
      m.geometry.dispose();
      m.material.dispose();
    }
    if (!showChips || amount <= 0) return;
    const primary = cssVar('--primary', '#c9a84c');
    const accent  = cssVar('--accent', '#5fa86b');
    const colors = [primary, accent, '#a83a3a', '#3a6aa8'];
    const count = Math.min(20, Math.max(1, Math.round(Math.log10(amount + 1) * 6)));
    for (let i = 0; i < count; i++) {
      const c = makeChipMesh(colors[i % colors.length]);
      c.position.set(0, 0.04 * i + 0.02, 0);
      chipsGroup.add(c);
    }
  }

  /** Update felt color (after operator theme change). */
  function refreshTheme() {
    feltMat.color.set(cssVar('--felt', '#1a2a22'));
    borderMat.color.set(cssVar('--primary', '#c9a84c'));
  }

  function dispose() {
    cancelAnimationFrame(rafId);
    ro.disconnect();
    clearTable();
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) {
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        mats.forEach(m => { m.map && m.map.dispose(); m.dispose(); });
      }
    });
    renderer.dispose();
    if (renderer.domElement && renderer.domElement.parentNode) {
      renderer.domElement.parentNode.removeChild(renderer.domElement);
    }
  }

  function getCardCount(zone) { return (cards[zone] || []).length; }

  return {
    dealCard,
    flipCard,
    replaceCard,
    clearTable,
    setChipStack,
    refreshTheme,
    dispose,
    getCardCount,
  };
}

/** 'A♠' style short label for HUD use. */
export function cardLabel(card) {
  if (!card || card.rank === '?') return '🂠';
  return `${card.rank}${card.suit}`;
}
