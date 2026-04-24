#!/usr/bin/env bash
# Generate design briefs across the 3-model matched grid.
#
# Per v4 value proposition, the design brief is the pipeline's
# shipping artifact — one markdown the designer opens and starts
# work from. Matched grid produces one brief per model so the
# designer can compare how each model's pipeline output flows into
# the final handoff.
#
# Grid: 1 cluster × 3 models (opus46, sonnet46, opus47) × 1 loop
# verifier (tchebycheff as default; pareto available via
# --loop-verifier) = 3 cells. Zero Claude calls — pure aggregation
# of outputs the pipeline already produced.
#
# macOS bash 3.2 compatible — no associative arrays.
set -uo pipefail

cd "$(dirname "$0")/.."

SMOKE="scripts/export_design_brief.py"
MODELS=(claude-opus-4-6 claude-sonnet-4-6 claude-opus-4-7)

for model in "${MODELS[@]}"; do
    echo "---"
    echo "run: model=$model"
    uv run python "$SMOKE" --model "$model" || {
        rc=$?
        echo "  (brief exited $rc — continuing grid)"
    }
done

echo "---"
echo "all briefs generated. output files:"
ls -1 data/derived/design_brief/*.md 2>/dev/null | sort
