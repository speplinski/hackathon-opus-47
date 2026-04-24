#!/usr/bin/env bash
# L8 thin-spine shared-input matched runner — 6 clusters × 3 models.
#
# Thin-spine re-audits each cluster's L7 decision.after_snapshot
# against the baseline heuristic list and emits two
# OptimizationIteration records per cluster: iter 0 (baseline, from
# L5 severity pass-through) and iter 1 (proposed, L7 decision
# re-audited). No multi-round refinement here — that's L8 loop.
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN)
#   opus46   →  claude-opus-4-6    (comparative)
#   sonnet46 →  claude-sonnet-4-6  (comparative)
#
# Inputs per branch:
#   data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_<branch>.jsonl
#   data/derived/l6_weight/shared_l2opus47/l6_priority_<branch>.jsonl
#   data/derived/l7_decide/shared_l2opus47/l7_design_decisions_<branch>.jsonl
#   data/derived/l5_reconcile/shared_l2opus47/l3b_filtered_<branch>.jsonl
#
# Outputs per branch:
#   data/derived/l8_optimize/shared_l2opus47/
#     l8_optimization_iterations_<branch>.jsonl
#     l8_optimization_iterations_<branch>.native.jsonl
#
# Cost: 6 clusters per call × 3 models ≈ 18 Claude calls (one per
# cluster; the re-audit is single-pass). Large prompts (full decision
# + reconciled context). Estimate reported \$3-6, real \$1-2.
#
# Usage:
#   bash scripts/run_l8_optimize_shared_matched.sh
#
# Prerequisites: L5 + L6 + L7 matched outputs, ANTHROPIC_API_KEY,
# uv venv.

set -uo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L5_DIR="$(pwd)/data/derived/l5_reconcile/shared_l2opus47"
L6_DIR="$(pwd)/data/derived/l6_weight/shared_l2opus47"
L7_DIR="$(pwd)/data/derived/l7_decide/shared_l2opus47"
L8_DIR="$(pwd)/data/derived/l8_optimize/shared_l2opus47"
ARTIFACTS_DIR="$(pwd)/data/artifacts/iterations/shared_l2opus47"
mkdir -p "$L8_DIR" "$ARTIFACTS_DIR"

if [[ ! -f "$L7_DIR/l7_design_decisions_opus47.jsonl" ]]; then
  echo "error: missing L7 decisions at $L7_DIR" >&2
  echo "hint: run scripts/run_l7_shared_matched.sh first" >&2
  exit 1
fi

run_matched() {
  local branch="$1"       # opus47 | opus46 | sonnet46
  local model="$2"        # claude-opus-4-7 | claude-opus-4-6 | claude-sonnet-4-6
  local reconciled="$L5_DIR/l5_reconciled_${branch}.jsonl"
  local priority="$L6_DIR/l6_priority_${branch}.jsonl"
  local decisions="$L7_DIR/l7_design_decisions_${branch}.jsonl"
  local clusters="$L5_DIR/l3b_filtered_${branch}.jsonl"
  local out="$L8_DIR/l8_optimization_iterations_${branch}.jsonl"
  local native="$L8_DIR/l8_optimization_iterations_${branch}.native.jsonl"
  local run_id="l8-thin-shared-l2opus47-${branch}"

  echo "==> [$branch] model=$model run_id=$run_id"
  local rc=0
  uv run python -m auditable_design.layers.l8_optimize \
    --reconciled "$reconciled" \
    --priority "$priority" \
    --decisions "$decisions" \
    --clusters "$clusters" \
    --output "$out" \
    --native-output "$native" \
    --artifacts-dir "$ARTIFACTS_DIR" \
    --run-id "$run_id" \
    --model "$model" \
    --concurrency 4 \
    --usd-ceiling 10.0 \
    --mode live || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "==> [$branch] completed with exit=$rc (fallback/transport — see warnings)"
  else
    echo "==> [$branch] done: $out"
  fi
  echo
}

# opus47 first — MAIN pipeline
run_matched "opus47"   "claude-opus-4-7"

# comparative baselines
run_matched "opus46"   "claude-opus-4-6"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three L8 thin-spine matched runs complete. Outputs in $L8_DIR."
echo "Next: L8 loop (hero cluster only) → verify_on_product → export_design_brief."
