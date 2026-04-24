#!/usr/bin/env bash
# Run the L8 design-optimize smoke across a matched-model grid.
#
# L8 is text-only (re-audits structured before/after snapshots, not
# UI), so the grid is 3 models × 1 modality = 3 cells. Closes ADR-009's
# L8 pilot action item. Each cell is one Claude call (single-pass
# generation — no double-pass judgment).
#
# Expected total spend: ~$0.15-0.30 (3 cells × ~9k in + ~500 out on
# Opus 4.7; cheaper on Sonnet).
#
# macOS bash 3.2 compatible — no associative arrays.
set -uo pipefail

cd "$(dirname "$0")/.."

SMOKE="scripts/smoke_l8_optimize.py"
MODELS=(claude-opus-4-6 claude-sonnet-4-6 claude-opus-4-7)
FORCE_ALL=0

for arg in "$@"; do
    case "$arg" in
        --all) FORCE_ALL=1 ;;
        -h|--help)
            echo "Usage: $0 [--all]"
            echo "  --all  force-rerun cells we already have iterations for"
            exit 0
            ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

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
    suffix="_${short}"
    target="data/derived/l8_optimize/l8_optimization_iterations_cluster02${suffix}.provenance.json"
    if [[ -f "$target" && $FORCE_ALL -eq 0 ]]; then
        optimized=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['optimized_count'])" "$target")
        if [[ "$optimized" == "1" ]]; then
            echo "skip: $model (optimized=1 in ${target##*/})"
            continue
        else
            echo "rerun: $model (prior run was fallback)"
        fi
    fi
    echo "---"
    echo "run: $model → ${suffix}"
    uv run python "$SMOKE" --model "$model" || {
        rc=$?
        echo "  (smoke exited $rc — likely fallback; continuing grid)"
    }
done

echo "---"
echo "all runs complete. provenance files:"
ls -1 data/derived/l8_optimize/*.provenance.json 2>/dev/null | sort
