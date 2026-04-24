#!/usr/bin/env bash
# L1 full-corpus matched-model driver — all 3 models classify all 600 reviews.
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN pipeline — hero narrative)
#   opus46   →  claude-opus-4-6    (comparative baseline)
#   sonnet46 →  claude-sonnet-4-6  (comparative baseline)
#
# Each model independently classifies the full corpus so the downstream
# matched grid (L2 → L3 → L3b → L4×6) operates on per-branch-consistent
# inputs. This is the convention already applied at L3b
# (scripts/run_l3b_matched.sh); extending it upstream to L1 makes the
# three branches self-contained end-to-end rather than sharing an Opus 4.6
# classifier output.
#
# Output naming:
#   data/derived/l1_classification/l1_full_opus47.jsonl
#   data/derived/l1_classification/l1_full_opus46.jsonl
#   data/derived/l1_classification/l1_full_sonnet46.jsonl
#
# Idempotency: L1 skips reviews whose review_id is already in the output
# file (see l1_classify docstring §Idempotency). Re-running the script
# with an existing full output is a no-op on the Claude-call side.
#
# Cost (cost_tracker overestimates Opus 3×, Sonnet accurate):
#   opus47    ~$5–10   (600 reviews × Opus pricing)
#   opus46    $0 if existing full output kept; otherwise ~$5–10
#   sonnet46  ~$2
#   total     ~$7–12 actual once corrected
#
# Usage:
#   bash scripts/run_l1_matched.sh
#
# Prerequisites:
#   - data/raw/corpus.jsonl present (600 reviews)
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed

set -euo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

OUT_DIR="data/derived/l1_classification"
mkdir -p "$OUT_DIR"

run_matched() {
  local branch="$1"       # opus47 | opus46 | sonnet46
  local model="$2"        # claude-opus-4-7 | claude-opus-4-6 | claude-sonnet-4-6
  local output="$OUT_DIR/l1_full_${branch}.jsonl"
  local run_id="l1-full-${branch}"

  echo "==> [$branch] model=$model run_id=$run_id"
  uv run python -m auditable_design.layers.l1_classify \
    --input data/raw/corpus.jsonl \
    --output "$output" \
    --run-id "$run_id" \
    --model "$model" \
    --concurrency 6 \
    --usd-ceiling 10.0 \
    --mode live
  echo "==> [$branch] done: $output"
  echo
}

# opus47 first — MAIN pipeline
run_matched "opus47"   "claude-opus-4-7"

# comparative baselines
run_matched "opus46"   "claude-opus-4-6"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three L1 full runs complete. Outputs in $OUT_DIR."
echo "Next: L2 structure (per model), then L3 cluster, then L3b label."
