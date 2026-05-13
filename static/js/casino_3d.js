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
  // +z is the "front" face (face of card); -z is the back design.
  const matFront = new THREE.MeshStandardMaterial({ map: faceTex, roughness: .35, metalness: .05 });
  const matBack  = new THREE.MeshStandardMaterial({ map: cachedBack, roughness: .35, metalness: .05 });
  const edge     = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: .8 });
  const geom = new THREE.BoxGeometry(CARD_W, CARD_H, CARD_T);
  const mesh = new THREE.Mesh(geom, [edge, edge, edge, edge, matFront, matBack]);
  mesh.castShadow = true;
  mesh.receiveShadow = false;
  mesh.userData = { rank, suit, isFlipped: false, faceTex, faceMatIndex: 4 };
  // Lay flat on the felt, face DOWN (face touching felt, back visible from above).
  // rotation.x = +PI/2 puts the +Z geometry face (the face of the card) toward -Y world.
  mesh.rotation.set(Math.PI / 2, 0, 0);
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
 * Per-zone start x is tuned so the typical card count for that zone is
 * centred on the felt (community = 5 cards, player/dealer/banker = 2-3
 * cards). Z separates dealer (back) from player (front).
 */
function slotPosition(zone, index) {
  const spread = 0.78;
  // start x = -spread * (typicalCount - 1) / 2 so the row sits centred.
  const layout = {
    dealer:    { startX: -spread, z: -1.1 },
    player:    { startX: -spread, z:  1.1 },
    banker:    { startX: -spread, z: -1.1 },
    community: { startX: -spread * 2, z: 0.4 },
    split0:    { startX: -spread, z: 1.1 },
    split1:    { startX: -spread, z: 2.2 },
  };
  const cfg = layout[zone] || layout.player;
  // y is just above the felt — cards lie flat. Stacked slightly to avoid
  // z-fighting with the felt plane.
  return { x: cfg.startX + index * spread, y: 1.01, z: cfg.z };
}

/* ─── Main table scene ────────────────────────────────────────────────── */
export function createTableScene(container, opts = {}) {
  const showChips = opts.showChips !== false;

  const scene = new THREE.Scene();

  // Camera — angled down-and-toward, like a player seated at the south
  // edge of the table looking up at the dealer. With flat cards we want
  // enough downward tilt to read the faces clearly.
  const aspect = container.clientWidth / Math.max(1, container.clientHeight);
  const camera = new THREE.PerspectiveCamera(44, aspect, 0.1, 50);
  camera.position.set(0, 4.6, 4.4);
  camera.lookAt(0, 1.0, 0.2);

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

  // Deck stack anchor (cards spawn here). Cards lay FLAT on the felt; the
  // stack is built by raising each new card slightly in world Y.
  const deckPos = new THREE.Vector3(2.6, 1.01, 0.0);

  // Build a small physical deck for visual reference — a stack of face-down
  // flat cards near the right side of the table.
  const deckGroup = new THREE.Group();
  for (let i = 0; i < 14; i++) {
    if (!cachedBack) cachedBack = buildCardBackTexture();
    // Use BoxGeometry with face textures so it visually matches dealt cards.
    const edgeMat  = new THREE.MeshStandardMaterial({ color: 0xfafafa, roughness: .8 });
    const backMat  = new THREE.MeshStandardMaterial({ map: cachedBack, roughness: .4 });
    const slab = new THREE.Mesh(
      new THREE.BoxGeometry(CARD_W, CARD_H, CARD_T),
      [edgeMat, edgeMat, edgeMat, edgeMat, edgeMat, backMat],
    );
    // Lay flat with the back design (BoxGeometry -Z face) pointing UP.
    slab.rotation.set(Math.PI / 2, 0, 0);
    slab.position.set(deckPos.x, deckPos.y + i * CARD_T, deckPos.z);
    slab.castShadow = true;
    deckGroup.add(slab);
  }
  scene.add(deckGroup);

  // Chip stack (left of player area). Hidden if showChips=false.
  const chipsGroup = new THREE.Group();
  // Place chips ON the felt but a bit further into the table so they don't
  // sit underneath the camera and clip out of frame.
  chipsGroup.position.set(-2.2, 1.02, 0.6);
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
   *  after the deal animation completes (then the optional flip). */
  function dealCard(zone, index, faceUp = false, card = null) {
    return new Promise((resolve) => {
      // For face-down cards (e.g. blackjack hole card) we don't yet know the
      // real value; spawn a placeholder mesh — when the hole is revealed the
      // caller swaps in real data via revealCard().
      const mesh = card
        ? makeCardMesh(card.rank, card.suit)
        : makeCardMesh('?', '♠');
      // Spawn at the top of the visible deck stack.
      const deckTopY = deckPos.y + 14 * CARD_T;
      const start = new THREE.Vector3(deckPos.x, deckTopY + 0.2, deckPos.z);
      mesh.position.copy(start);
      // Already lying flat face-down from makeCardMesh().
      scene.add(mesh);
      const dst = slotPosition(zone, index);
      // Bezier control: gentle arc above the table so the card "slides" over
      // the felt rather than sailing high in the air.
      const ctrl = new THREE.Vector3(
        (start.x + dst.x) / 2,
        Math.max(start.y, dst.y) + 0.6,
        (start.z + dst.z) / 2,
      );
      tween({
        duration: 420,
        from: { t: 0 },
        to:   { t: 1 },
        onUpdate({ t }) {
          const p = bezier3(start, ctrl, dst, t);
          mesh.position.copy(p);
          // Tiny in-flight Z-axis wobble for flair; keep card mostly flat.
          mesh.rotation.z = Math.sin(t * Math.PI) * 0.18;
        },
        onComplete() {
          mesh.position.set(dst.x, dst.y, dst.z);
          mesh.rotation.set(Math.PI / 2, 0, 0); // settle flat face-down
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

  /** Flip a card by rotating around the world X axis 180° while lifting the
   *  card off the felt at the apex — the natural casino "turn-over" motion. */
  function flipCard(zone, index) {
    return new Promise((resolve) => {
      const mesh = (cards[zone] || [])[index];
      if (!mesh) return resolve();
      const from = mesh.rotation.x;
      const to   = mesh.userData.isFlipped ? Math.PI / 2 : -Math.PI / 2;
      const baseY = mesh.position.y;
      tween({
        duration: 360,
        from: { t: 0 },
        to:   { t: 1 },
        onUpdate({ t }) {
          mesh.rotation.x = from + (to - from) * t;
          // Lift then settle — peaks at t=0.5
          mesh.position.y = baseY + Math.sin(t * Math.PI) * 0.22;
        },
        onComplete() {
          mesh.rotation.x = to;
          mesh.position.y = baseY;
          mesh.userData.isFlipped = !mesh.userData.isFlipped;
          resolve();
        },
      });
    });
  }

  /** Swap a face-down card's face texture to the REAL card data, then flip
   *  it. Used for the blackjack dealer hole card — we don't know the value
   *  at deal time, but reveal it once the round resolves. Idempotent — a
   *  second call with the same card is a no-op so retries don't double-flip. */
  async function revealCard(zone, index, card) {
    const mesh = (cards[zone] || [])[index];
    if (!mesh || !card) return;
    if (mesh.userData.revealed) return;
    // Build a new face texture and swap into the front material.
    const newFaceTex = buildCardFaceTexture(card.rank, card.suit);
    const mats = mesh.material;
    if (Array.isArray(mats) && mats[mesh.userData.faceMatIndex]) {
      const m = mats[mesh.userData.faceMatIndex];
      if (m.map) m.map.dispose();
      m.map = newFaceTex;
      m.needsUpdate = true;
    }
    mesh.userData.rank = card.rank;
    mesh.userData.suit = card.suit;
    mesh.userData.revealed = true;
    await flipCard(zone, index);
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

  /** Replace a card in a zone (used for video poker draw — discard the old
   *  card, deal a new one face-down at that slot, then flip face-up). */
  async function replaceCard(zone, index, card) {
    const old = (cards[zone] || [])[index];
    if (old) {
      scene.remove(old);
      old.geometry.dispose();
      if (Array.isArray(old.material)) old.material.forEach(m => m.map && m.map.dispose());
    }
    const mesh = makeCardMesh(card.rank, card.suit);
    const dst = slotPosition(zone, index);
    const deckTopY = deckPos.y + 14 * CARD_T;
    mesh.position.set(deckPos.x, deckTopY + 0.2, deckPos.z);
    scene.add(mesh);
    (cards[zone] = cards[zone] || []);
    cards[zone][index] = mesh;
    const start = mesh.position.clone();
    const ctrl = new THREE.Vector3((start.x + dst.x) / 2, start.y + 0.6, (start.z + dst.z) / 2);
    await new Promise(r => tween({
      duration: 380,
      from: { t: 0 }, to: { t: 1 },
      onUpdate({ t }) {
        const p = bezier3(start, ctrl, dst, t);
        mesh.position.copy(p);
        mesh.rotation.z = Math.sin(t * Math.PI) * 0.18;
      },
      onComplete() {
        mesh.position.set(dst.x, dst.y, dst.z);
        mesh.rotation.set(Math.PI / 2, 0, 0);
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
    revealCard,
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
