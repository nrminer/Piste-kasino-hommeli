"""Merge spec parts 1-4 and validate the final JSON for the card games spec."""
import json, sys, os, ast

PART_PATHS = [
    "/app/spec_parts2/part_1.json",
    "/app/spec_parts2/part_2.json",
    "/app/spec_parts2/part_3.json",
    "/app/spec_parts2/part_4.json",
]
OUT_PATH = "/app/casino_card_games_spec.json"

merged = {}
for p in PART_PATHS:
    with open(p, "r", encoding="utf-8") as f:
        obj = json.load(f)
    obj.pop("_part_index", None)
    obj.pop("_total_parts", None)
    obj.pop("_final", None)
    overlap = set(merged.keys()) & set(obj.keys())
    if overlap:
        print(f"ERROR: overlapping top-level keys between parts: {overlap}", file=sys.stderr)
        sys.exit(1)
    merged.update(obj)

REQUIRED = [
    "metadata", "rtp_calculation", "art_tokens", "animation_specs",
    "animation_timing_table", "blackjack_upgrade", "texas_holdem_upgrade",
    "card_renderer", "rng_to_animation_pseudocode", "ui_wireframes",
    "css_glsl_snippets", "db_migrations", "sound_bank", "performance_budget",
    "accessibility", "qa_checklist", "implementation_notes",
]
missing = [k for k in REQUIRED if k not in merged]
if missing:
    print(f"ERROR: missing required keys: {missing}", file=sys.stderr)
    sys.exit(1)

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)

# Re-read to confirm validity
with open(OUT_PATH, "r", encoding="utf-8") as f:
    final = json.load(f)

# Validate Python pseudocode blocks
errors = 0
for name, code in final["rng_to_animation_pseudocode"].items():
    if not isinstance(code, str) or code.startswith("(see "):
        continue
    try:
        ast.parse(code)
        print(f"OK   pseudocode[{name}]: {len(code)} chars")
    except SyntaxError as e:
        print(f"FAIL pseudocode[{name}]: {e}", file=sys.stderr)
        errors += 1

# Validate split/surrender/bonus_buy pseudocode in blackjack_upgrade
for key in ["split_mechanic", "surrender_mechanic", "bonus_buy"]:
    code = final["blackjack_upgrade"][key]["pseudocode"]
    try:
        ast.parse(code)
        print(f"OK   blackjack_upgrade.{key}.pseudocode: {len(code)} chars")
    except SyntaxError as e:
        print(f"FAIL blackjack_upgrade.{key}.pseudocode: {e}", file=sys.stderr)
        errors += 1

# Validate poker mode_a/mode_b pseudocode
for key, path in [("mode_a", ["mode_a_display_only", "pot_award_pseudocode"]),
                   ("mode_b", ["mode_b_semi_automated", "bet_pot_pseudocode"])]:
    code = final["texas_holdem_upgrade"][path[0]][path[1]]
    try:
        ast.parse(code)
        print(f"OK   texas_holdem_upgrade.{path[0]}.{path[1]}: {len(code)} chars")
    except SyntaxError as e:
        print(f"FAIL texas_holdem_upgrade.{path[0]}.{path[1]}: {e}", file=sys.stderr)
        errors += 1

# Validate RG loss-streak pseudocode
code = final["accessibility"]["responsible_gambling"]["loss_streak_pseudocode"]
try:
    ast.parse(code)
    print(f"OK   accessibility.responsible_gambling.loss_streak_pseudocode: {len(code)} chars")
except SyntaxError as e:
    print(f"FAIL loss_streak_pseudocode: {e}", file=sys.stderr)
    errors += 1

# RTP smoke checks
def in_tol(val, target, tol):
    return abs(val - target) <= tol

bj_rtp = final["rtp_calculation"]["blackjack"]["base_rtp_pct"]
print(f"RTP blackjack base: {bj_rtp}% within ±0.1 of 90.0 = {in_tol(bj_rtp, 90.0, 0.1)}")
if not in_tol(bj_rtp, 90.0, 0.1):
    errors += 1

pp_rtp = final["rtp_calculation"]["blackjack_perfect_pairs"]["rtp_pct"]
in_band_pp = 88.0 <= pp_rtp <= 92.0
print(f"RTP perfect_pairs: {pp_rtp}% in [88, 92] = {in_band_pp}")
if not in_band_pp:
    errors += 1

t213_rtp = final["rtp_calculation"]["blackjack_21plus3"]["rtp_pct"]
in_band_213 = 88.0 <= t213_rtp <= 92.0
print(f"RTP 21+3: {t213_rtp}% in [88, 92] = {in_band_213}")
if not in_band_213:
    errors += 1

hb_rtp = final["rtp_calculation"]["texas_holdem"]["house_banked_variant_rtp_pct"]
print(f"RTP house-banked holdem: {hb_rtp}% within ±0.1 of 90.0 = {in_tol(hb_rtp, 90.0, 0.1)}")
if not in_tol(hb_rtp, 90.0, 0.1):
    errors += 1

# Counts
size = os.path.getsize(OUT_PATH)
print(f"\nfile_size_bytes: {size}")
print(f"top_level_keys: {len(final)}")
print(f"animations: {len(final['animation_specs'])}")
print(f"animation_timing_rows: {len(final['animation_timing_table'])}")
print(f"sound_bank_count: {len(final['sound_bank'])}")
print(f"db_migrations_count: {len(final['db_migrations'])}")
print(f"new_blackjack_endpoints: {len(final['blackjack_upgrade']['new_api_endpoints'])}")
print(f"new_poker_mode_b_endpoints: {len(final['texas_holdem_upgrade']['mode_b_semi_automated']['new_api_endpoints'])}")

print(f"\nTOTAL ERRORS: {errors}")
sys.exit(1 if errors else 0)
