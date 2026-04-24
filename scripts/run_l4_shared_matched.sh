#!/usr/bin/env bash
# L4 shared-input matched driver — 6 clusters × 6 lenses × 3 models × image modality
# on real Duolingo screenshots.
#
# Prerequisites:
#   - scripts/build_l4_inputs.py has been run (generates the 36
#     per-(lens, cluster) input files under shared_l2opus47/)
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed
#
# Pipeline positioning:
#   L3b (shared_l2opus47/l3b_labeled_clusters_opus47.jsonl)
#     → build_l4_inputs.py adds ui_context + real screenshot_ref
#     → this runner sweeps all (cluster × lens × model × modality)
#     → outputs feed L5 reconciliation
#
# Six clusters covered (the subset with a canonical Duolingo screenshot
# in data/raw/duolingo_screenshots/):
#   cluster_00  PT energy (energy_manage.png)
#   cluster_01  Super subscription upsell (out_of_energy_home.png)
#   cluster_06  Super Duolingo upsell ads (out_of_energy_home.png)
#   cluster_11  Streak loss (out_of_energy_mid_lesson.png, shows LOSE XP)
#   cluster_12  Energy system mechanic (energy_manage.png)
#   cluster_13  New energy system (energy_manage.png)
#
# Outputs land in data/derived/l4_audit/audit_<lens>/shared_l2opus47/
# with names of the form:
#   l4_verdicts_audit_<lens>_<clusterNN>_<model_short>[_multimodal].jsonl
# plus matching .native.jsonl and .provenance.json. No legacy
# cluster_02 outputs are touched — those remain in the parent
# audit_<lens>/ directory as the historical baseline.
#
# Exit-code tolerance: L4 smokes return 1 on fallback (parse miss / thin
# payload) which is a data observation, not a script failure. We want
# to finish the grid so the user can diff fallbacks across models —
# same convention as run_l4_interaction_design_matched.sh.
#
# Cost (tracker overestimates Opus 3×):
#   108 runs × ~14k in + ~2.5k out ≈ reported $15–22, real $5–8.
# Text modality deliberately skipped — review TEXT is already the input
# to L1–L3b upstream, so auditing labels + quotes again here (without
# pixels) duplicates signal. Image modality with real Duolingo
# screenshots is where L4 earns its keep.
#
# Usage:
#   bash scripts/run_l4_shared_matched.sh
#   bash scripts/run_l4_shared_matched.sh --all   # force-rerun completed

set -uo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

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

CLUSTERS=(cluster_00 cluster_01 cluster_06 cluster_11 cluster_12 cluster_13)
LENSES=(accessibility business_alignment decision_psychology interaction_design usability_fundamentals ux_architecture)
MODELS=(claude-opus-4-7 claude-opus-4-6 claude-sonnet-4-6)
MODALITIES=(image)

# cluster_id → real-product screenshot path. Keep in sync with
# scripts/build_l4_inputs.py CLUSTER_CONFIG. Using a case statement
# instead of an associative array so the script runs on macOS default
# bash 3.2 (declare -A needs bash 4+).
screenshot_for_cluster() {
    case "$1" in
        cluster_00|cluster_12|cluster_13)
            echo "data/raw/duolingo_screenshots/energy_manage.png" ;;
        cluster_01|cluster_06)
            echo "data/raw/duolingo_screenshots/out_of_energy_home.png" ;;
        cluster_11)
            echo "data/raw/duolingo_screenshots/out_of_energy_mid_lesson.png" ;;
        *)
            echo "error: no screenshot mapped for $1" >&2; return 1 ;;
    esac
}

short_of() {
    case "$1" in
        claude-opus-4-6)   echo "opus46"  ;;
        claude-sonnet-4-6) echo "sonnet46";;
        claude-opus-4-7)   echo "opus47"  ;;
        *) echo "${1//\//_}" ;;
    esac
}

# Sanity: inputs must exist.
for cluster in "${CLUSTERS[@]}"; do
    for lens in "${LENSES[@]}"; do
        input="data/derived/l4_audit/audit_${lens}/shared_l2opus47/audit_${lens}_input_${cluster}.jsonl"
        if [[ ! -f "$input" ]]; then
            echo "error: missing input $input" >&2
            echo "hint: run 'uv run python scripts/build_l4_inputs.py' first" >&2
            exit 1
        fi
    done
done

total=0
ran=0
skipped=0

for cluster in "${CLUSTERS[@]}"; do
    cluster_stem="${cluster//_/}"
    screenshot=$(screenshot_for_cluster "$cluster")
    for lens in "${LENSES[@]}"; do
        smoke="scripts/smoke_l4_${lens}_multimodal.py"
        input="data/derived/l4_audit/audit_${lens}/shared_l2opus47/audit_${lens}_input_${cluster}.jsonl"
        # Smoke computes verdicts_path.relative_to(_REPO_ROOT) for its
        # success print, which requires out_dir to be absolute. Use an
        # absolute path here so the print doesn't crash after a
        # successful Claude call + file write.
        out_dir="$(pwd)/data/derived/l4_audit/audit_${lens}/shared_l2opus47"
        mkdir -p "$out_dir"

        for model in "${MODELS[@]}"; do
            short=$(short_of "$model")
            for modality in "${MODALITIES[@]}"; do
                total=$((total+1))
                suffix="_${short}"
                [[ "$modality" == "image" ]] && suffix="${suffix}_multimodal"
                target="${out_dir}/l4_verdicts_audit_${lens}_${cluster_stem}${suffix}.provenance.json"

                if [[ -f "$target" && $FORCE_ALL -eq 0 ]]; then
                    audited=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('audited_count', 0))" "$target" 2>/dev/null || echo "0")
                    if [[ "$audited" == "1" ]]; then
                        skipped=$((skipped+1))
                        continue
                    fi
                fi

                echo "---"
                echo "[$total] $cluster × $lens × $model × $modality"
                uv run python "$smoke" \
                    --input "$input" \
                    --screenshot "$screenshot" \
                    --model "$model" \
                    --modality "$modality" \
                    --out-dir "$out_dir" || {
                    rc=$?
                    echo "  (smoke exited $rc — likely fallback; continuing grid)"
                }
                ran=$((ran+1))
            done
        done
    done
done

echo "==="
echo "total=$total ran=$ran skipped=$skipped"
echo "outputs under data/derived/l4_audit/audit_*/shared_l2opus47/"
