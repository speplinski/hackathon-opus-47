#!/usr/bin/env bash
# Run verify-on-product across the 3-model matched grid.
#
# Grid: 1 cluster × 3 models (opus46, sonnet46, opus47) × 3
# screenshots = 3 VLM calls. Same convention as L3b / L4 / L5 /
# L6 / L7 / L8 / baseline_b1 matched evals.
#
# Each cell: one VLM call with 3 PNGs + structured heuristic list.
# Expected spend: ~$0.60-1.00 (VLM tokens more expensive than text;
# Opus 4.6 and 4.7 ≈ $0.30 each, Sonnet 4.6 ≈ $0.05-0.10).
#
# Macos bash 3.2 compatible — no associative arrays.
set -uo pipefail

cd "$(dirname "$0")/.."

SMOKE="scripts/verify_on_product.py"
MODELS=(claude-opus-4-6 claude-sonnet-4-6 claude-opus-4-7)

for model in "${MODELS[@]}"; do
    echo "---"
    echo "run: model=$model"
    uv run python "$SMOKE" --model "$model" || {
        rc=$?
        echo "  (verify exited $rc — continuing grid)"
    }
done

echo "---"
echo "all runs complete. provenance files:"
ls -1 data/derived/verify_on_product/*.provenance.json 2>/dev/null | sort
