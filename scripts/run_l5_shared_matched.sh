#!/usr/bin/env bash
# L5 shared-input matched runner — 3 models × 6 clusters reconciled
# from the shared-L2/L3 matched grid L4 verdicts.
#
# Each branch consumes its own L4 verdicts (bundle written by
# scripts/build_l5_bundles.py) + its own L3b labelling (filtered to
# the six target clusters). Reconciliation is batched: one L5
# invocation per model processes all six clusters at once, producing
# one reconciled verdict per cluster. That's the layer's native
# shape — the six-lens-to-one-verdict collapse per cluster.
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN)
#   opus46   →  claude-opus-4-6    (comparative)
#   sonnet46 →  claude-sonnet-4-6  (comparative)
#
# Inputs per branch (built by scripts/build_l5_bundles.py):
#   data/derived/l5_reconcile/shared_l2opus47/l5_bundle_<branch>.jsonl
#   data/derived/l5_reconcile/shared_l2opus47/l3b_filtered_<branch>.jsonl
#
# Outputs per branch:
#   data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_<branch>.jsonl
#   data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_<branch>.native.jsonl
#   data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_<branch>.provenance.json
#
# Cost: six clusters per call × three branches ≈ 18 Claude calls.
# Medium-size prompts (full L4 verdicts for six lenses per cluster);
# estimate reported \$10-15, real \$4-6 after Opus 3x correction.
#
# Usage:
#   uv run python scripts/build_l5_bundles.py       # first, build bundles
#   bash scripts/run_l5_shared_matched.sh            # then reconcile
#
# Prerequisites:
#   - L5 bundles + filtered clusters (see build_l5_bundles.py)
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed

set -uo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L5_DIR="$(pwd)/data/derived/l5_reconcile/shared_l2opus47"

if [[ ! -f "$L5_DIR/l5_bundle_opus47.jsonl" ]]; then
  echo "error: missing $L5_DIR/l5_bundle_opus47.jsonl" >&2
  echo "hint: run 'uv run python scripts/build_l5_bundles.py' first" >&2
  exit 1
fi

run_matched() {
  local branch="$1"       # opus47 | opus46 | sonnet46
  local model="$2"        # claude-opus-4-7 | claude-opus-4-6 | claude-sonnet-4-6
  local bundle="$L5_DIR/l5_bundle_${branch}.jsonl"
  local clusters="$L5_DIR/l3b_filtered_${branch}.jsonl"
  local out="$L5_DIR/l5_reconciled_${branch}.jsonl"
  local native="$L5_DIR/l5_reconciled_${branch}.native.jsonl"
  local run_id="l5-shared-l2opus47-${branch}"

  echo "==> [$branch] model=$model run_id=$run_id"
  local rc=0
  uv run python -m auditable_design.layers.l5_reconcile \
    --verdicts "$bundle" \
    --clusters "$clusters" \
    --output "$out" \
    --native-output "$native" \
    --run-id "$run_id" \
    --model "$model" \
    --concurrency 4 \
    --usd-ceiling 10.0 \
    --mode live || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "==> [$branch] completed with exit=$rc (transport or fallback — see warnings)"
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

echo "All three L5 matched runs complete. Outputs in $L5_DIR."
echo "Next: L6 priority, L7 decide, L8 optimize, verify_on_product, export_design_brief."
