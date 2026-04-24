#!/usr/bin/env bash
# Run the L5 reconcile smoke across a matched-model grid.
#
# L5 is text-only (reconcile consumes structured verdicts, not UI
# surfaces), so the grid is 3 models × 1 modality = 3 cells. Closes
# ADR-009's L5 pilot action item (stratified-triad pilot; here the
# "stratification" is the six L4 skills' bundle for cluster_02, and
# the "triad" is the three Claude families).
#
# Skips cells with an existing audited=1 provenance; pass --all to
# force-rerun. Every run is a LIVE Anthropic call — expected total
# spend: ~$0.10-0.30 (3 cells × ~15k in + ~3k out each; Opus 4.7
# and Opus 4.6 are the cost drivers).
#
# macOS bash 3.2 compatible — no associative arrays.
set -uo pipefail

cd "$(dirname "$0")/.."

SMOKE="scripts/smoke_l5_reconcile.py"
MODELS=(claude-opus-4-6 claude-sonnet-4-6 claude-opus-4-7)
FORCE_ALL=0

for arg in "$@"; do
    case "$arg" in
        --all) FORCE_ALL=1 ;;
        -h|--help)
            echo "Usage: $0 [--all]"
            echo "  --all  force-rerun cells we already have verdicts for"
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
    target="data/derived/l5_reconcile/l5_reconciled_cluster02${suffix}.provenance.json"
    if [[ -f "$target" && $FORCE_ALL -eq 0 ]]; then
        audited=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['audited_count'])" "$target")
        if [[ "$audited" == "1" ]]; then
            echo "skip: $model (audited=1 in ${target##*/})"
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
ls -1 data/derived/l5_reconcile/*.provenance.json 2>/dev/null | sort
