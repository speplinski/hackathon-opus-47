#!/usr/bin/env bash
# Run the L4 accessibility smoke across a matched-model × modality grid.
#
# By default skips the two opus47 runs you already have on disk; pass
# --all to force-rerun them. Every run is a LIVE Anthropic call (no
# replay cache) so set the kill-switch on your billing side accordingly
# — expected total: ~4 × (13k in + 2k out) ≈ $0.50-0.80.
# Intentionally NOT using `set -e` on the smoke call: the smoke returns
# exit 1 on a fallback (parse miss), which is a data observation, not
# a script failure — we want to finish the grid so the user gets a
# full picture and can diff fallbacks across models. Hard errors
# (missing files, transport failures) still surface via stderr.
set -uo pipefail

cd "$(dirname "$0")/.."

SMOKE="scripts/smoke_l4_accessibility_multimodal.py"
MODELS=(claude-opus-4-6 claude-sonnet-4-6 claude-opus-4-7)
MODALITIES=(text image)
FORCE_ALL=0

for arg in "$@"; do
    case "$arg" in
        --all) FORCE_ALL=1 ;;
        -h|--help)
            echo "Usage: $0 [--all]"
            echo "  --all  force-rerun models we already have verdicts for"
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
    for modality in "${MODALITIES[@]}"; do
        suffix="_${short}"
        [[ "$modality" == "image" ]] && suffix="${suffix}_multimodal"
        target="data/derived/l4_audit/audit_accessibility/l4_verdicts_audit_accessibility_cluster01${suffix}.provenance.json"
        if [[ -f "$target" && $FORCE_ALL -eq 0 ]]; then
            # Skip only when the existing run was a SUCCESS — fallbacks
            # get re-run so a stale SKILL.md-drift fallback (like the
            # pre-fix sonnet46 text run) doesn't poison the eval.
            audited=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['audited_count'])" "$target")
            if [[ "$audited" == "1" ]]; then
                echo "skip: $model × $modality (audited=1 in ${target##*/})"
                continue
            else
                echo "rerun: $model × $modality (prior run was fallback)"
            fi
        fi
        echo "---"
        echo "run: $model × $modality → ${suffix}"
        uv run python "$SMOKE" --model "$model" --modality "$modality" || {
            rc=$?
            echo "  (smoke exited $rc — likely fallback; continuing grid)"
        }
    done
done

echo "---"
echo "all runs complete. provenance files:"
ls -1 data/derived/l4_audit/audit_accessibility/*.provenance.json | sort
