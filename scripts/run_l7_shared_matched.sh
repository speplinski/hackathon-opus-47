#!/usr/bin/env bash
# L7 shared-input matched runner — design decisions for 6 clusters × 3 models.
#
# L7 generates one DesignPrinciple + one DesignDecision per cluster
# from the ReconciledVerdict (L5) + PriorityScore (L6) + cluster
# context (L3b). Text-only — no UI surfaces at this layer. Single
# pass per cluster (L7 is a generation task, not a judgment-validation
# task like L6).
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN)
#   opus46   →  claude-opus-4-6    (comparative)
#   sonnet46 →  claude-sonnet-4-6  (comparative)
#
# Inputs per branch:
#   data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_<branch>.jsonl
#   data/derived/l6_weight/shared_l2opus47/l6_priority_<branch>.jsonl
#   data/derived/l5_reconcile/shared_l2opus47/l3b_filtered_<branch>.jsonl
#
# Outputs per branch:
#   data/derived/l7_decide/shared_l2opus47/
#     l7_design_principles_<branch>.jsonl
#     l7_design_decisions_<branch>.jsonl
#     l7_design_decisions_<branch>.native.jsonl
#
# Cost: 6 clusters × 3 models = 18 single-pass calls. Large prompts
# (full L5 verdict + L6 score + cluster). Estimate reported \$4-8,
# real \$1.5-3 after Opus 3x correction.
#
# Usage:
#   bash scripts/run_l7_shared_matched.sh
#
# Prerequisites:
#   - L5 + L6 outputs at data/derived/l{5,6}_*/shared_l2opus47/
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed

set -uo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L5_DIR="$(pwd)/data/derived/l5_reconcile/shared_l2opus47"
L6_DIR="$(pwd)/data/derived/l6_weight/shared_l2opus47"
L7_DIR="$(pwd)/data/derived/l7_decide/shared_l2opus47"
mkdir -p "$L7_DIR"

for f in "$L5_DIR/l5_reconciled_opus47.jsonl" "$L6_DIR/l6_priority_opus47.jsonl"; do
  if [[ ! -f "$f" ]]; then
    echo "error: missing upstream input $f" >&2
    echo "hint: run scripts/run_l5_shared_matched.sh and scripts/run_l6_shared_matched.sh" >&2
    exit 1
  fi
done

run_matched() {
  local branch="$1"       # opus47 | opus46 | sonnet46
  local model="$2"        # claude-opus-4-7 | claude-opus-4-6 | claude-sonnet-4-6
  local reconciled="$L5_DIR/l5_reconciled_${branch}.jsonl"
  local priority="$L6_DIR/l6_priority_${branch}.jsonl"
  local clusters="$L5_DIR/l3b_filtered_${branch}.jsonl"
  local principles="$L7_DIR/l7_design_principles_${branch}.jsonl"
  local decisions="$L7_DIR/l7_design_decisions_${branch}.jsonl"
  local native="$L7_DIR/l7_design_decisions_${branch}.native.jsonl"
  local run_id="l7-shared-l2opus47-${branch}"

  echo "==> [$branch] model=$model run_id=$run_id"
  local rc=0
  uv run python -m auditable_design.layers.l7_decide \
    --reconciled "$reconciled" \
    --priority "$priority" \
    --clusters "$clusters" \
    --principles-output "$principles" \
    --decisions-output "$decisions" \
    --native-output "$native" \
    --run-id "$run_id" \
    --model "$model" \
    --concurrency 4 \
    --usd-ceiling 10.0 \
    --mode live || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "==> [$branch] completed with exit=$rc (fallback or transport — see warnings)"
  else
    echo "==> [$branch] done: $decisions"
  fi
  echo
}

# opus47 first — MAIN pipeline
run_matched "opus47"   "claude-opus-4-7"

# comparative baselines
run_matched "opus46"   "claude-opus-4-6"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three L7 matched runs complete. Outputs in $L7_DIR."
echo "Next: L8 optimize (thin-spine + loop), verify_on_product, export_design_brief."
