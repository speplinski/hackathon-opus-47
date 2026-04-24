#!/usr/bin/env bash
# L6 shared-input matched runner — priority scores for 6 clusters × 3 models.
#
# L6 is text-only (5-dim priority scoring of L5 reconciled verdicts,
# no UI surfaces). Invokes the production module directly in batch
# mode — one invocation per branch reads the branch's reconciled
# verdicts file and emits one PriorityScore per cluster.
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN)
#   opus46   →  claude-opus-4-6    (comparative)
#   sonnet46 →  claude-sonnet-4-6  (comparative)
#
# Inputs per branch:
#   data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_<branch>.jsonl
#   data/derived/l5_reconcile/shared_l2opus47/l3b_filtered_<branch>.jsonl
#
# Outputs per branch:
#   data/derived/l6_weight/shared_l2opus47/l6_priority_<branch>.jsonl
#   data/derived/l6_weight/shared_l2opus47/l6_priority_<branch>.native.jsonl
#
# L6 runs 2-3 Claude passes per cluster (double-pass baseline + optional
# third if per-dim score drift > 1). With 6 clusters per branch the
# minimum is 12 calls, max 18. Across 3 branches: 36-54 calls total.
#
# Cost: medium prompts (full L5 verdict serialized in each call).
# Estimate: reported \$6-12, real \$2-4 after Opus 3x correction.
#
# Usage:
#   bash scripts/run_l6_shared_matched.sh
#
# Prerequisites:
#   - L5 outputs at data/derived/l5_reconcile/shared_l2opus47/
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed

set -uo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L5_DIR="$(pwd)/data/derived/l5_reconcile/shared_l2opus47"
L6_DIR="$(pwd)/data/derived/l6_weight/shared_l2opus47"
mkdir -p "$L6_DIR"

if [[ ! -f "$L5_DIR/l5_reconciled_opus47.jsonl" ]]; then
  echo "error: missing L5 reconciled verdicts at $L5_DIR" >&2
  echo "hint: run 'bash scripts/run_l5_shared_matched.sh' first" >&2
  exit 1
fi

run_matched() {
  local branch="$1"       # opus47 | opus46 | sonnet46
  local model="$2"        # claude-opus-4-7 | claude-opus-4-6 | claude-sonnet-4-6
  local reconciled="$L5_DIR/l5_reconciled_${branch}.jsonl"
  local clusters="$L5_DIR/l3b_filtered_${branch}.jsonl"
  local out="$L6_DIR/l6_priority_${branch}.jsonl"
  local native="$L6_DIR/l6_priority_${branch}.native.jsonl"
  local run_id="l6-shared-l2opus47-${branch}"

  echo "==> [$branch] model=$model run_id=$run_id"
  local rc=0
  uv run python -m auditable_design.layers.l6_weight \
    --reconciled "$reconciled" \
    --clusters "$clusters" \
    --output "$out" \
    --native-output "$native" \
    --run-id "$run_id" \
    --model "$model" \
    --concurrency 4 \
    --usd-ceiling 10.0 \
    --mode live || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "==> [$branch] completed with exit=$rc (fallback or transport — see warnings)"
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

echo "All three L6 matched runs complete. Outputs in $L6_DIR."
echo "Next: L7 decide, L8 optimize, verify_on_product, export_design_brief."
