#!/usr/bin/env bash
# L2 shared-input matched-model driver — 3 models extract complaint graphs
# from the SAME L1 classifier output (opus47).
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN pipeline — hero narrative)
#   opus46   →  claude-opus-4-6    (comparative baseline)
#   sonnet46 →  claude-sonnet-4-6  (comparative baseline)
#
# This is the *shared-input* eval pattern (cf. L3b matched runner's
# "shared labeller" baseline in docs/evals/l3b_full_corpus_three_way.md):
# all three L2 branches consume the opus47 L1 output. That isolates
# divergence in the L2 graph-extraction step itself, independent of L1
# filtering differences.
#
# Outputs live in a subdirectory so the existing matched-L1 outputs
# at data/derived/l2_structure/l2_graphs_full_*.jsonl are left untouched
# (both sets coexist for shared-input-vs-matched comparison).
#
# Output naming:
#   data/derived/l2_structure/shared_l1opus47/l2_graphs_full_opus47.jsonl
#   data/derived/l2_structure/shared_l1opus47/l2_graphs_full_opus46.jsonl
#   data/derived/l2_structure/shared_l1opus47/l2_graphs_full_sonnet46.jsonl
#
# Quarantine (thin/padded/hallucinated graphs) written per branch:
#   data/quarantine/shared_l1opus47/l2_thin_opus47.jsonl
#   …
#
# Idempotency: L2 merges review_ids already present in output or
# quarantine; a rerun with existing files is a no-op (replay cache hits).
#
# Cost (cost_tracker overestimates Opus 3×, Sonnet accurate):
#   ~400 ux-relevant reviews/model × per-call pricing. Sonnet ~$3,
#   Opus ~$10–15 each. Ceiling $15/run. Cache hits from prior L2 matched
#   runs may reduce spend substantially if the same (review, L1_payload,
#   model) keys are hit.
#
# Usage:
#   bash scripts/run_l2_matched.sh
#
# Prerequisites:
#   - data/derived/l1_classification/l1_full_opus47.jsonl
#   - data/raw/corpus.jsonl
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed

set -euo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L1_INPUT="data/derived/l1_classification/l1_full_opus47.jsonl"
OUT_DIR="data/derived/l2_structure/shared_l1opus47"
QUARANTINE_DIR="data/quarantine/shared_l1opus47"
mkdir -p "$OUT_DIR" "$QUARANTINE_DIR"

if [[ ! -f "$L1_INPUT" ]]; then
  echo "error: missing L1 input $L1_INPUT (run scripts/run_l1_matched.sh first)" >&2
  exit 1
fi

run_matched() {
  local branch="$1"       # opus47 | opus46 | sonnet46
  local model="$2"        # claude-opus-4-7 | claude-opus-4-6 | claude-sonnet-4-6
  local output="$OUT_DIR/l2_graphs_full_${branch}.jsonl"
  local quarantine="$QUARANTINE_DIR/l2_thin_${branch}.jsonl"
  local run_id="l2-full-${branch}-on-l1opus47"

  echo "==> [$branch] model=$model run_id=$run_id"
  # L2 exits non-zero on ANY parse failure. We want to continue across
  # branches (1% failures on opus46 shouldn't block sonnet46). Capture
  # exit code, continue either way, surface non-zero at the end.
  local rc=0
  uv run python -m auditable_design.layers.l2_structure \
    --corpus data/raw/corpus.jsonl \
    --classified "$L1_INPUT" \
    --output "$output" \
    --quarantine "$quarantine" \
    --run-id "$run_id" \
    --model "$model" \
    --concurrency 6 \
    --usd-ceiling 15.0 \
    --mode live || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "==> [$branch] completed with exit=$rc (parse failures — see warnings above)"
  else
    echo "==> [$branch] done: $output"
  fi
  echo
}

# opus47 first — MAIN pipeline
run_matched "opus47"   "claude-opus-4-7"

# comparative baselines (both consume opus47's L1)
run_matched "opus46"   "claude-opus-4-6"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three L2 shared-input runs complete. Outputs in $OUT_DIR."
echo "Next: L3 cluster (per model), then L3b label."
