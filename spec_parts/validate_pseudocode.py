"""Validate that all rng_to_animation_pseudocode blocks are syntactically valid Python."""
import ast, json, sys

with open("/app/casino_game_spec.json", encoding="utf-8") as f:
    spec = json.load(f)

errors = 0
for name, code in spec["rng_to_animation_pseudocode"].items():
    try:
        ast.parse(code)
        print(f"OK  {name}: {len(code)} chars parses")
    except SyntaxError as e:
        errors += 1
        print(f"FAIL {name}: {e}", file=sys.stderr)

# also validate bonus_definitions rng_pseudocode entries
for b in spec["bonus_definitions"]:
    code = b.get("rng_pseudocode")
    if not code:
        continue
    try:
        ast.parse(code)
        print(f"OK  bonus[{b['id']}].rng_pseudocode: {len(code)} chars parses")
    except SyntaxError as e:
        errors += 1
        print(f"FAIL bonus[{b['id']}].rng_pseudocode: {e}", file=sys.stderr)

# RTP smoke checks: ensure declared total_rtp_pct values are within 90 ± 0.1 (allow small wider tol for blackjack combined)
def in_tol(val, target, tol):
    return abs(val - target) <= tol

rtp = spec["rtp_calculation"]
for game in ["slots_fruits","slots_egypt","slots_space","slots_tumble","slots_hold_win"]:
    pct = rtp[game]["rtp_breakdown"]["total_rtp_pct"]
    ok = in_tol(pct, 90.0, 0.1)
    print(f"RTP slots[{game}]: {pct}% within ±0.1 of 90.0 = {ok}")
    if not ok:
        errors += 1

for game in ["pikapokeri","pikapokeri_wild_deuces"]:
    pct = rtp[game]["total_rtp_pct"]
    ok = in_tol(pct, 90.0, 0.15)
    print(f"RTP pikapokeri[{game}]: {pct}% within ±0.15 of 90.0 = {ok}")
    if not ok:
        errors += 1

bj = rtp["blackjack"]["combined_player_rtp_estimate_pct"]
ok = in_tol(bj, 90.0, 0.10)
print(f"RTP blackjack combined: {bj}% within ±0.10 of 90.0 = {ok}")
if not ok:
    errors += 1

print(f"\nTOTAL ERRORS: {errors}")
sys.exit(1 if errors else 0)
