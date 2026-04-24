#!/usr/bin/env bash
# L3b matched-model driver on the shared L3 clustering.
#
# Three models independently label the SAME 14 clusters produced by
# scripts/run_l3_single.sh. Because the cluster boundaries are fixed
# by a deterministic L3 (HDBSCAN over local embeddings, no Claude
# call), the only thing varying across branches is the labelling
# decision itself. This isolates per-model labelling behaviour on
# identical input — each branch's cluster_02 is literally the same
# set of reviews, only the human-readable label differs.
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN pipeline)
#   opus46   →  claude-opus-4-6    (comparative baseline)
#   sonnet46 →  claude-sonnet-4-6  (comparative baseline)
#
# Shared input:
#   data/derived/l3_clusters/shared_l2opus47/l3_clusters.jsonl  (14 clusters)
#
# Outputs:
#   data/derived/l3b_labeled_clusters/shared_l2opus47/
#     l3b_labeled_clusters_opus47.jsonl
#     l3b_labeled_clusters_opus46.jsonl
#     l3b_labeled_clusters_sonnet46.jsonl
#
# Mode is 'live'. Cache hits from prior matched-model runs (the older
# l3b_labeled_clusters/matched_rubric_v1 and _v2 experiments) will
# only apply if the (skill_hash, model, cluster_payload) key matches;
# since the cluster payloads here come from a new L3 run on a new
# node-type filter, expect fresh Claude calls.
#
# Cost: 14 clusters × 3 models = 42 short calls. Sonnet per-call
# ~\$0.001, Opus ~\$0.003 (after 3x tracker correction). Total
# estimate \$0.05–0.10.
#
# Usage:
#   bash scripts/run_l3b_shared_matched.sh
#
# Prerequisites:
#   - data/derived/l3_clusters/shared_l2opus47/l3_clusters.jsonl
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed

set -euo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L3_INPUT="data/derived/l3_clusters/shared_l2opus47/l3_clusters.jsonl"
OUT_DIR="data/derived/l3b_labeled_clusters/shared_l2opus47"
mkdir -p "$OUT_DIR"

if [[ ! -f "$L3_INPUT" ]]; then
  echo "error: missing L3 input $L3_INPUT (run scripts/run_l3_single.sh first)" >&2
  exit 1
fi

run_matched() {
  local branch="$1"       # opus47 | opus46 | sonnet46
  local model="$2"        # claude-opus-4-7 | claude-opus-4-6 | claude-sonnet-4-6
  local output="$OUT_DIR/l3b_labeled_clusters_${branch}.jsonl"
  local run_id="l3b-shared-l2opus47-${branch}"

  echo "==> [$branch] model=$model run_id=$run_id"
  uv run python -m auditable_design.layers.l3b_label \
    --clusters "$L3_INPUT" \
    --output "$output" \
    --run-id "$run_id" \
    --model "$model" \
    --concurrency 6 \
    --usd-ceiling 2.0 \
    --mode live
  echo "==> [$branch] done: $output"
  echo
}

# opus47 first — MAIN pipeline
run_matched "opus47"   "claude-opus-4-7"

# comparative baselines (same clusters, different labeller)
run_matched "opus46"   "claude-opus-4-6"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three L3b matched runs complete. Outputs in $OUT_DIR."
echo "Next: review labels cross-model, then L4 multi-lens audits."
