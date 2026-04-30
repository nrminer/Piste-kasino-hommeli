# Immersive Slot Machine Experience — UI, Motion, RNG/RTP & Asset Spec

## 1. UI Mockups

### Desktop Layout
- Central 5×3 reel set in a polished metal/glass cabinet.
- Left side panel: animated character/puzzle lock with pulsing nodes.
- Right side panel: bonus gate meter reacting to scatters and win count.
- Top HUD: balance, RTP, current bet, and settings access.
- Bottom controls: autoplay, turbo, sound, paytable, and glowing primary spin button.
- Theme skins: Luxury, Neon Noir, Steampunk.

### Mobile Layout
- Reels remain central and full-width.
- Side panels stack above/below the reel frame as compact cinematic widgets.
- HUD chips wrap into a 2-column layout.
- Spin button remains large and thumb-accessible.

## 2. Animated Prototype Behaviors

### Spin Sequence
1. Reel start: mechanical tick + rising synth pulse, mobile vibration, optional gamepad rumble.
2. Variable reel cycling with symbol blur and subtle cabinet hum.
3. Staggered stops from left to right with per-reel clacks.
4. Scatter anticipation slows later reels and triggers rising tones.

### Win Sequence
- Small win: green/gold reactive lighting and short particle burst.
- Big win/jackpot: camera shake, brighter cabinet glow, larger particles, stronger haptic pattern, cinematic audio impact.
- Payline overlay remains clear and animated.

### Bonus Sequence
- Scatter-triggered free spins can now create a server-backed Bonus Vault mini-game.
- Full-screen transition opens 12 vault tiles.
- Player opens 3 tiles; each pick is server-authoritative and immediately credits reward points.

## 3. RNG / RTP Technical Spec
- Base slot spin RNG remains server-side in `/api/points/<pid>/slots`.
- Reel result, wins, paylines, scatters, free spins, and jackpot outcomes remain authoritative on the backend.
- Bonus Vault rewards are pre-generated server-side in `slot_bonus_games.rewards_json`; only picked tiles are revealed to the client.
- Client animations are presentation-only and never determine payouts.
- Persistent RTP display is shown in the slot HUD. Current UI displays theme/skin RTP targets around 95.8–96.2%.
- Regression tests must continue validating payout response shape, payout math, and aggregate RTP sanity.

## 4. Animation Timing
- Spin start sound: 0–170ms rising tick sequence.
- First reel stop: ~920ms normal / ~350ms turbo.
- Standard reel gap: ~260ms plus jitter.
- Anticipation reel gap: ~1300ms normal / ~600ms turbo.
- Symbol snap: ~340ms cubic easing.
- Big-win camera shake: ~500ms.
- Bonus tile reveal: ~450ms.

## 5. Accessibility & Performance
- Reduced-motion toggle disables heavy motion at the slot component level.
- High-contrast toggle increases contrast on slot symbols/cells.
- Persistent RTP display is visible in the HUD.
- Particle emission adapts to hardware concurrency and user reduced-motion preference.
- Animations use CSS transforms/opacity where possible for GPU acceleration.
- Mobile haptics use `navigator.vibrate`; controller rumble uses Gamepad vibration actuators when available.

## 6. Asset List

### Textures / Materials
- Polished dark metal cabinet.
- Glass reel cover with inner reflections.
- Velvet/dark casino background.
- Neon cyan/violet accent glow.
- Brushed brass/steam metal accents.

### Sounds
- Reel start mechanical ticks.
- Per-reel stop clacks.
- Scatter anticipation rising synth.
- Small win chime.
- Big win cinematic impact/fanfare.
- Bonus vault reveal click.
- No-win soft downbeat.

### Particle Presets
- Gold coin sparks for wins.
- Cyan energy particles for bonus transitions.
- Green/gold short burst for small wins.
- High-density gold burst for big wins/jackpots.
