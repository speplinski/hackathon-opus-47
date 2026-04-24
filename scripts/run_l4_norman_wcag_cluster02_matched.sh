#!/usr/bin/env bash
# Backfill Norman + WCAG on cluster_02 — historical debt. The other four
# L4 skills (Cooper, Kahneman, Osterwalder, Garrett) were all audited on
# the shared cluster_02 "Streak loss framing" fixture; Norman and WCAG
# audited different clusters (cluster_01 "Chess" and cluster_01 "Voice
# recognition" respectively) before the shared fixture was standardised.
#
# This runner closes that gap: 2 skills × 3 models × 2 modalities = 12
# new L4 cells, all on cluster_02. Expected total spend: ~$0.30.
#
# Pass --all to force-rerun cells we already have verdicts for.
#
# macOS bash 3.2 compatible — no associative arrays.
# Not using `set -e` on the smoke call: smokes return exit 1 on a
# fallback (parse miss), which is a data observation, not a script
# failure — we want to finish the grid so the user gets a full picture.
set -uo pipefail

cd "$(dirname "$0")/.."

MODELS=(claude-opus-4-6 claude-sonnet-4-6 claude-opus-4-7)
MODALITIES=(text image)
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

# One inner loop that takes a skill-specific 4-tuple as positional args
# (smoke path, input path, out dir, filename stem prefix). Called twice
# below — once per skill — so the 3×2 grid logic stays in one place.
run_grid_for_skill() {
    local skill_name="$1"
    local smoke="$2"
    local input_path="$3"
    local out_dir="$4"
    local stem_prefix="$5"

    for model in "${MODELS[@]}"; do
        short=$(short_of "$model")
        for modality in "${MODALITIES[@]}"; do
            suffix="_${short}"
            [[ "$modality" == "image" ]] && suffix="${suffix}_multimodal"
            target="${out_dir}/${stem_prefix}_cluster02${suffix}.provenance.json"
            if [[ -f "$target" && $FORCE_ALL -eq 0 ]]; then
                audited=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['audited_count'])" "$target")
                if [[ "$audited" == "1" ]]; then
                    echo "skip: $skill_name × $model × $modality (audited=1 in ${target##*/})"
                    continue
                else
                    echo "rerun: $skill_name × $model × $modality (prior run was fallback)"
                fi
            fi
            echo "---"
            echo "run: $skill_name × $model × $modality → ${suffix}"
            uv run python "$smoke" \
                --input "$input_path" \
                --model "$model" \
                --modality "$modality" || {
                rc=$?
                echo "  (smoke exited $rc — likely fallback; continuing grid)"
            }
        done
    done
}

run_grid_for_skill \
    "norman" \
    "scripts/smoke_l4_usability_fundamentals_multimodal.py" \
    "data/derived/l4_audit/audit_usability_fundamentals/audit_usability_fundamentals_cluster02_input.jsonl" \
    "data/derived/l4_audit/audit_usability_fundamentals" \
    "l4_verdicts_audit_usability_fundamentals"

run_grid_for_skill \
    "wcag" \
    "scripts/smoke_l4_accessibility_multimodal.py" \
    "data/derived/l4_audit/audit_accessibility/audit_accessibility_cluster02_input.jsonl" \
    "data/derived/l4_audit/audit_accessibility" \
    "l4_verdicts_audit_accessibility"

echo "---"
echo "backfill complete. new cluster_02 provenance files:"
ls -1 \
    data/derived/l4_audit/audit_usability_fundamentals/*cluster02*.provenance.json \
    data/derived/l4_audit/audit_accessibility/*cluster02*.provenance.json \
    2>/dev/null | sort
