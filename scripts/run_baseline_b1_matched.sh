#!/usr/bin/env bash
# Run the B1 naive baseline across the 3-model matched grid.
#
# Grid: 1 cluster × 3 models (opus46, sonnet46, opus47) = 3 cells.
# Same convention as L3b / L4 / L5 / L6 / L7 / L8 matched evals.
#
# Each cell runs two Claude calls (naive generation + design-optimize
# re-audit). Expected spend: ~$0.40-0.60 total.
#
# macOS bash 3.2 compatible — no associative arrays.
set -uo pipefail

cd "$(dirname "$0")/.."

SMOKE="scripts/baseline_b1.py"
MODELS=(claude-opus-4-6 claude-sonnet-4-6 claude-opus-4-7)

for model in "${MODELS[@]}"; do
    echo "---"
    echo "run: model=$model"
    uv run python "$SMOKE" --model "$model" || {
        rc=$?
        echo "  (b1 exited $rc — continuing grid)"
    }
done

echo "---"
echo "all runs complete. provenance files:"
ls -1 data/derived/baseline_b1/*.provenance.json 2>/dev/null | sort
