#!/usr/bin/env bash
# Run the L8 multi-round optimization loop smoke on a matched grid.
#
# Grid: 1 cluster × 2 verifiers (pareto, tchebycheff) × 3 models
# (opus46, sonnet46, opus47) with matched tweak == reaudit = 6 cells.
#
# Matches the eval convention across L3b, L4×6, L5, L6, L7, L8
# thin-spine — every layer eval compares the same three models.
# Weaker/cheaper models (sonnet46) are the interesting cases for
# pareto-vs-tchebycheff divergence; opus47 is the strong-model
# baseline.
#
# Each cell picks the matching thin-spine iterations file as input
# (l8_optimization_iterations_cluster02_<short>.jsonl) so the loop
# continues from its own model's iter 1, not a mismatched one.
#
# Expected spend: ~$2.5–3 (6 cells × up to 3 tweak/reaudit rounds).
# Most cells will terminate on round 2 (converged) like the opus47
# pilot did; sonnet46 may run longer.
#
# macOS bash 3.2 compatible — no associative arrays.
set -uo pipefail

cd "$(dirname "$0")/.."

SMOKE="scripts/smoke_l8_loop.py"
MODELS=(claude-opus-4-6 claude-sonnet-4-6 claude-opus-4-7)
VERIFIERS=(pareto tchebycheff)

short_of() {
    case "$1" in
        claude-opus-4-6)   echo "opus46"  ;;
        claude-sonnet-4-6) echo "sonnet46";;
        claude-opus-4-7)   echo "opus47"  ;;
        *) echo "${1//\//_}" ;;
    esac
}

for model in "${MODELS[@]}"; do
    short=$(short_of "$model")
    input="data/derived/l8_optimize/l8_optimization_iterations_cluster02_${short}.jsonl"
    if [[ ! -f "$input" ]]; then
        echo "skip: $model (no thin-spine input at ${input##*/})"
        continue
    fi
    for v in "${VERIFIERS[@]}"; do
        echo "---"
        echo "run: model=$model verifier=$v"
        uv run python "$SMOKE" \
            --verifier "$v" \
            --tweak-model "$model" \
            --reaudit-model "$model" \
            --iterations-input "$input" || {
            rc=$?
            echo "  (smoke exited $rc — check log; continuing grid)"
        }
    done
done

echo "---"
echo "all runs complete. provenance files:"
ls -1 data/derived/l8_loop/*.provenance.json 2>/dev/null | sort
