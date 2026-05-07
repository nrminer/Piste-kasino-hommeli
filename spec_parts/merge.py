"""Merge spec parts and validate the final JSON."""
import json, sys, os

PART_PATHS = [
    "/app/spec_parts/part_1.json",
    "/app/spec_parts/part_2.json",
    "/app/spec_parts/part_3.json",
]
OUT_PATH = "/app/casino_game_spec.json"

merged = {}
for p in PART_PATHS:
    with open(p, "r", encoding="utf-8") as f:
        obj = json.load(f)  # validates each part is valid JSON
    overlap = set(merged.keys()) & set(obj.keys())
    if overlap:
        print(f"ERROR: overlapping top-level keys between parts: {overlap}", file=sys.stderr)
        sys.exit(1)
    merged.update(obj)

# Required top-level keys per problem statement
REQUIRED = [
    "metadata", "rtp_calculation", "art_tokens", "animation_specs",
    "bonus_definitions", "lottie_stubs", "animation_timing_table",
    "rng_to_animation_pseudocode", "ui_wireframes", "css_glsl_snippets",
    "db_migrations", "api_endpoints", "performance_budget", "accessibility",
    "sound_bank", "voiceover_cues", "qa_checklist", "implementation_notes",
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

size = os.path.getsize(OUT_PATH)
print(f"OK: merged {len(PART_PATHS)} parts → {OUT_PATH}")
print(f"top_level_keys: {len(final)}")
print(f"file_size_bytes: {size}")
print(f"games_listed: {len(final['metadata']['games'])}")
print(f"animation_specs_count: {len(final['animation_specs'])}")
print(f"bonus_definitions_count: {len(final['bonus_definitions'])}")
print(f"sound_bank_count: {len(final['sound_bank'])}")
print(f"voiceover_cues_count: {len(final['voiceover_cues'])}")
print(f"db_migrations_count: {len(final['db_migrations'])}")
print(f"api_endpoints_count: {len(final['api_endpoints'])}")
