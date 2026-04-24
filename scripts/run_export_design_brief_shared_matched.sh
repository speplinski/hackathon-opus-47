#!/usr/bin/env bash
# export_design_brief shared-input matched runner — hero cluster_11 × 3 models.
#
# Aggregates the pipeline's shipping artifact (one markdown brief per
# model) from the shared_l2opus47 pipeline outputs. Each invocation
# points at the branch-specific L5/L6/L7/L8/verify files via the
# --*-path flags (each defaults to the legacy matched-grid layout if
# omitted — we pass them explicitly here to pull from shared_l2opus47/).
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN — the hero brief)
#   opus46   →  claude-opus-4-6    (comparative)
#   sonnet46 →  claude-sonnet-4-6  (comparative)
#
# Outputs per branch:
#   data/derived/design_brief/shared_l2opus47/design_brief_cluster11_<short>.md
#   data/derived/design_brief/shared_l2opus47/design_brief_cluster11_<short>.provenance.json
#
# Cost: \$0 — offline aggregation, no Claude calls.
#
# Usage:
#   bash scripts/run_export_design_brief_shared_matched.sh

set -uo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

CLUSTER_ID="cluster_11"
VERIFIER="tchebycheff"

L5_DIR="$(pwd)/data/derived/l5_reconcile/shared_l2opus47"
L6_DIR="$(pwd)/data/derived/l6_weight/shared_l2opus47"
L7_DIR="$(pwd)/data/derived/l7_decide/shared_l2opus47"
L8_DIR="$(pwd)/data/derived/l8_optimize/shared_l2opus47"
LOOP_DIR="$(pwd)/data/derived/l8_loop/shared_l2opus47"
VERIFY_DIR="$(pwd)/data/derived/verify_on_product/shared_l2opus47"
OUT_DIR="$(pwd)/data/derived/design_brief/shared_l2opus47"
mkdir -p "$OUT_DIR"

run_matched() {
  local branch="$1"
  local model="$2"
  local clusters="$L5_DIR/l3b_filtered_${branch}.jsonl"
  local reconciled="$L5_DIR/l5_reconciled_${branch}.jsonl"
  local priority="$L6_DIR/l6_priority_${branch}.jsonl"
  local decisions="$L7_DIR/l7_design_decisions_${branch}.jsonl"
  local iters_thin="$L8_DIR/l8_optimization_iterations_${branch}.jsonl"
  local iters_loop="$LOOP_DIR/l8_loop_iterations_cluster11_${branch}_${VERIFIER}.jsonl"
  local verify="$VERIFY_DIR/verify_on_product_cluster11_${branch}.json"

  echo "==> [$branch] model=$model cluster=$CLUSTER_ID"
  local rc=0
  uv run python scripts/export_design_brief.py \
    --cluster-id "$CLUSTER_ID" \
    --model "$model" \
    --loop-verifier "$VERIFIER" \
    --out-dir "$OUT_DIR" \
    --clusters-path "$clusters" \
    --reconciled-path "$reconciled" \
    --priority-path "$priority" \
    --decisions-path "$decisions" \
    --iters-thin-path "$iters_thin" \
    --iters-loop-path "$iters_loop" \
    --verify-path "$verify" || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "==> [$branch] exited $rc"
  else
    echo "==> [$branch] done."
  fi
  echo
}

# opus47 first — MAIN pipeline
run_matched "opus47"   "claude-opus-4-7"

# comparative baselines
run_matched "opus46"   "claude-opus-4-6"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three design_brief matched exports complete. Briefs in $OUT_DIR."
