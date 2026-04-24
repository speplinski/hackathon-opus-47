#!/usr/bin/env bash
# L8 loop shared-input matched runner — hero cluster_11 × 3 models.
#
# Multi-round refinement orchestrator — continues from the thin-spine
# iter 1 and proposes further tweaks. Terminates on convergence
# (severity_sum ≤ threshold), stall_limit consecutive rejections, or
# max_iterations. Uses tchebycheff verifier by default (ADR-009 §L8
# decided tchebycheff is the production default; pareto is a
# comparison-mode alternative).
#
# Scope: hero cluster_11 only. The other five clusters are kept at
# thin-spine depth — their severity reductions (47-100%) are already
# strong enough for POC demo without the loop overhead. Loop is
# per-cluster (the l8_optimize_loop module takes --cluster-id and
# runs one orchestration per invocation).
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN — tweak + reaudit)
#   opus46   →  claude-opus-4-6    (comparative)
#   sonnet46 →  claude-sonnet-4-6  (comparative)
#
# Inputs per branch:
#   data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_<branch>.jsonl
#   data/derived/l6_weight/shared_l2opus47/l6_priority_<branch>.jsonl
#   data/derived/l7_decide/shared_l2opus47/l7_design_decisions_<branch>.jsonl
#   data/derived/l5_reconcile/shared_l2opus47/l3b_filtered_<branch>.jsonl
#   data/derived/l8_optimize/shared_l2opus47/l8_optimization_iterations_<branch>.jsonl
#
# Outputs per branch:
#   data/derived/l8_loop/shared_l2opus47/
#     l8_loop_iterations_cluster11_<branch>_tchebycheff.jsonl
#     l8_loop_iterations_cluster11_<branch>_tchebycheff.native.jsonl
#     l8_loop_iterations_cluster11_<branch>_tchebycheff.provenance.json
#
# Cost: 3 branches × up to 5 loop iterations × (tweak + reaudit per
# iter) ≈ 6-30 calls total. Hero cluster is already at 95-100%
# reduction, so most branches converge at iter 2 (severity-threshold
# trigger). Estimate reported \$2-4, real \$1-2.
#
# Usage:
#   bash scripts/run_l8_loop_shared_matched.sh
#
# Prerequisites: thin-spine L8 + upstream, ANTHROPIC_API_KEY, uv venv.

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
ARTIFACTS_DIR="$(pwd)/data/artifacts/iterations/shared_l2opus47"
mkdir -p "$LOOP_DIR" "$ARTIFACTS_DIR"

if [[ ! -f "$L8_DIR/l8_optimization_iterations_opus47.jsonl" ]]; then
  echo "error: missing L8 thin-spine outputs at $L8_DIR" >&2
  echo "hint: run scripts/run_l8_optimize_shared_matched.sh first" >&2
  exit 1
fi

run_matched() {
  local branch="$1"       # opus47 | opus46 | sonnet46
  local model="$2"        # claude-opus-4-7 | claude-opus-4-6 | claude-sonnet-4-6
  local reconciled="$L5_DIR/l5_reconciled_${branch}.jsonl"
  local priority="$L6_DIR/l6_priority_${branch}.jsonl"
  local decisions="$L7_DIR/l7_design_decisions_${branch}.jsonl"
  local clusters="$L5_DIR/l3b_filtered_${branch}.jsonl"
  local iters_in="$L8_DIR/l8_optimization_iterations_${branch}.jsonl"
  local iters_out="$LOOP_DIR/l8_loop_iterations_cluster11_${branch}_${VERIFIER}.jsonl"
  local native="$LOOP_DIR/l8_loop_iterations_cluster11_${branch}_${VERIFIER}.native.jsonl"
  local prov="$LOOP_DIR/l8_loop_iterations_cluster11_${branch}_${VERIFIER}.provenance.json"
  local run_id="l8-loop-shared-l2opus47-${branch}-${VERIFIER}"

  echo "==> [$branch] model=$model run_id=$run_id verifier=$VERIFIER"
  local rc=0
  uv run python -m auditable_design.layers.l8_optimize_loop \
    --cluster-id "$CLUSTER_ID" \
    --iterations-input "$iters_in" \
    --iterations-output "$iters_out" \
    --native-output "$native" \
    --provenance-output "$prov" \
    --artifacts-dir "$ARTIFACTS_DIR" \
    --verifier "$VERIFIER" \
    --reconciled "$reconciled" \
    --priority "$priority" \
    --decisions "$decisions" \
    --clusters "$clusters" \
    --tweak-model "$model" \
    --reaudit-model "$model" \
    --run-id "$run_id" \
    --mode live \
    --usd-ceiling 10.0 \
    --concurrency 4 || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "==> [$branch] completed with exit=$rc"
  else
    echo "==> [$branch] done: $iters_out"
  fi
  echo
}

# opus47 first — MAIN pipeline
run_matched "opus47"   "claude-opus-4-7"

# comparative baselines
run_matched "opus46"   "claude-opus-4-6"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three L8 loop matched runs complete for $CLUSTER_ID. Outputs in $LOOP_DIR."
echo "Next: verify_on_product → export_design_brief."
