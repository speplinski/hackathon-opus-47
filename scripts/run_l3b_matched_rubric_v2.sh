#!/usr/bin/env bash
# L3b matched-model rubric-v2 re-run across all three pipelines.
#
# Follow-up to `docs/evals/l3b_matched_three_way.md`, which measured a 100%
# tier-3 rubric violation rate on Opus 4.6 (6/14 clusters) and 0% on
# Haiku / Sonnet 4.6 (0/17). After `skills/label-cluster/SKILL.md` was
# hardened with explicit "Forbidden label shapes" + affect-only clause +
# worked example, a standalone opus46 re-run confirmed tier-3 violations
# dropped to 0/14 (with a secondary reasoning-drift failure: 2/14
# UNLABELED placeholders where the model wrote reasoning before JSON).
#
# This script extends the measurement to the other two matched labellers
# for symmetry. Questions it answers:
#
#   - Sonnet 4.6 (7 themed / 4 Mixed at baseline): does the new affect-only
#     clause push any of the 3 "themed" labels into Mixed? If yes, the
#     hardening trades Opus 4.6 tier-3 wins for Sonnet over-triggering —
#     important for the "which labeller to ship" decision.
#   - Opus 4.7 (7 themed / 3 Mixed at baseline, no tier-3 violations):
#     does it exhibit the same reasoning-drift output-contract failure
#     Opus 4.6 hit? A non-zero UNLABELED count here generalises the
#     failure to Opus-family, making the prefill fix in `claude_client`
#     a higher-priority follow-up.
#
# Cache behaviour: the new skill_hash is `df9289ee…` (baseline was
# `8f6bffe5…`). opus46 entries from the standalone rubric v2 run are
# already in the replay log at the new skill_hash and will replay from
# cache (0 new calls, $0 spend). opus47 and sonnet46 are fresh calls.
#
# Cost (new calls only): 10 opus47 + 7 sonnet46 = 17 calls. Tracker
# estimate ~$0.20-0.30; real billing ~$0.05-0.07 after Opus ÷3 and Sonnet
# 1:1 calibration.
#
# Outputs are written alongside the standalone opus46 artefact in
# `data/derived/l3b_labeled_clusters/matched_rubric_v2/` so all three
# rubric-v2 branches coexist in one directory, matching the structure of
# the baseline `matched/` directory.
#
# Usage:
#   bash scripts/run_l3b_matched_rubric_v2.sh
#
# Exit codes:
#   0 — all three runs succeeded (opus46 via cache, opus47/sonnet46 live)
#   1 — at least one run failed (script aborts on first failure via -e)
#
# Prerequisites:
#   - L3 inputs present at data/derived/l3_clusters/l3_clusters_full_*.jsonl
#   - data/cache/responses.jsonl with the 14 opus46 rubric-v2 entries
#     (committed in the same series as this script, or produced locally
#     by an earlier run of `run_l3b_opus46_rubric_v2.sh` before this script
#     subsumed it)
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed

set -euo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L3_DIR="data/derived/l3_clusters"
OUT_DIR="data/derived/l3b_labeled_clusters/matched_rubric_v2"
mkdir -p "$OUT_DIR"

run_matched_v2() {
  local branch="$1"       # opus46 | opus47 | sonnet46
  local model="$2"        # claude-opus-4-6 | claude-opus-4-7 | claude-sonnet-4-6
  local input="$L3_DIR/l3_clusters_full_${branch}.jsonl"
  local output="$OUT_DIR/l3b_labeled_clusters_full_${branch}.jsonl"
  local run_id="l3b-full-${branch}-matched-rubric-v2"

  if [[ ! -f "$input" ]]; then
    echo "error: missing L3 input $input" >&2
    return 1
  fi

  echo "==> [$branch] model=$model run_id=$run_id"
  uv run python -m auditable_design.layers.l3b_label \
    --clusters "$input" \
    --output "$output" \
    --run-id "$run_id" \
    --model "$model" \
    --mode live
  echo "==> [$branch] done: $output"
  echo
}

run_matched_v2 "opus46"   "claude-opus-4-6"
run_matched_v2 "opus47"   "claude-opus-4-7"
run_matched_v2 "sonnet46" "claude-sonnet-4-6"

echo "All three rubric-v2 matched runs complete. Outputs in $OUT_DIR."
echo "Next: compare tier-3 violations + parse-fail rate against matched baseline."
