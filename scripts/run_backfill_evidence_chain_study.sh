#!/usr/bin/env bash
# Backfill per-finding review ids into the hackathon study data, with no
# model calls. Covers:
#   * the shared_l2opus47 grid (6 clusters x 6 lenses x 3 models, L5 per
#     model; Study B is cluster_11 in this grid);
#   * Study A (hand-built cluster_02 input; L5 for three models over the
#     Opus 4.6 text-mode L4 bundle).
#
# Inputs are read from data/derived (unchanged). Outputs mirror their
# input paths under data/derived_evchain/. One JSON report per step goes
# to data/derived_evchain/reports/.
#
# Usage (from the repo root):  bash scripts/run_backfill_evidence_chain_study.sh

set -euo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root" >&2
  exit 1
fi

OUT="$(pwd)/data/derived_evchain"
REP=$OUT/reports
mkdir -p "$REP"
BF="uv run python scripts/backfill_evidence_chain.py"
D="$(pwd)/data/derived"
M="$OUT/data/derived"   # mirrored (backfilled) copy of $D

# --- shared_l2opus47 grid ---------------------------------------------------
$BF clusters \
  --graphs $D/l2_structure/l2_graphs_full_opus47.jsonl \
  --clusters \
    $D/l3_clusters/shared_l2opus47/l3_clusters.jsonl \
    $D/l3b_labeled_clusters/shared_l2opus47/l3b_labeled_clusters_{opus47,opus46,sonnet46}.jsonl \
    $D/l5_reconcile/shared_l2opus47/l3b_filtered_{opus47,opus46,sonnet46}.jsonl \
    $D/l4_audit/audit_*/shared_l2opus47/audit_*_input_cluster_*.jsonl \
  --out-root $OUT > $REP/grid_clusters.json

GRID_L4=$(ls $D/l4_audit/audit_*/shared_l2opus47/l4_verdicts_*.jsonl | grep -v '\.native\.jsonl$')
$BF l4 \
  --clusters $M/l3_clusters/shared_l2opus47/l3_clusters.jsonl \
  --verdicts $GRID_L4 \
  --out-root $OUT > $REP/grid_l4.json

$BF bundle \
  --bundles $D/l5_reconcile/shared_l2opus47/l5_bundle_{opus47,opus46,sonnet46}.jsonl \
  --l4-verdicts $(ls $M/l4_audit/audit_*/shared_l2opus47/l4_verdicts_*.jsonl | grep -v '\.native\.jsonl$') \
  --out-root $OUT > $REP/grid_bundles.json

for b in opus47 opus46 sonnet46; do
  $BF l5 \
    --bundle $M/l5_reconcile/shared_l2opus47/l5_bundle_$b.jsonl \
    --verdicts $D/l5_reconcile/shared_l2opus47/l5_reconciled_$b.jsonl \
    --out-root $OUT > $REP/grid_l5_$b.json
done

# --- Study A: cluster_02 ----------------------------------------------------
A_INPUTS=$(grep -l '"cluster_id": "cluster_02"' $D/l4_audit/audit_*/audit_*input.jsonl)
$BF clusters \
  --graphs $D/l2_structure/l2_graphs_full_opus46.jsonl \
  --corpus data/raw/corpus.jsonl \
  --clusters $A_INPUTS \
  --out-root $OUT > $REP/studyA_clusters.json

A_CLUSTER=$M/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl
A_L4=$(ls $D/l4_audit/audit_*/l4_verdicts_*_cluster02_*.jsonl | grep -v '\.native\.jsonl$')
$BF l4 --clusters $A_CLUSTER --verdicts $A_L4 --out-root $OUT > $REP/studyA_l4.json

$BF bundle \
  --bundles $D/l5_reconcile/cluster_02_opus46_text_bundle.jsonl \
  --l4-verdicts $(ls $M/l4_audit/audit_*/l4_verdicts_*_cluster02_*.jsonl | grep -v '\.native\.jsonl$') \
  --out-root $OUT > $REP/studyA_bundle.json

for b in opus47 opus46 sonnet46; do
  $BF l5 \
    --bundle $M/l5_reconcile/cluster_02_opus46_text_bundle.jsonl \
    --verdicts $D/l5_reconcile/l5_reconciled_cluster02_$b.jsonl \
    --out-root $OUT > $REP/studyA_l5_$b.json
done

# --- Study B briefs (cluster_11), regenerated from backfilled L3/L5 ----------
G=$D/l5_reconcile/shared_l2opus47
for pair in opus47:claude-opus-4-7 opus46:claude-opus-4-6 sonnet46:claude-sonnet-4-6; do
  b=${pair%%:*}; model=${pair#*:}
  uv run python scripts/export_design_brief.py \
    --cluster-id cluster_11 --model "$model" --loop-verifier tchebycheff \
    --out-dir $M/design_brief/shared_l2opus47 \
    --clusters-path $M/l5_reconcile/shared_l2opus47/l3b_filtered_$b.jsonl \
    --reconciled-path $M/l5_reconcile/shared_l2opus47/l5_reconciled_$b.jsonl \
    --priority-path $D/l6_weight/shared_l2opus47/l6_priority_$b.jsonl \
    --decisions-path $D/l7_decide/shared_l2opus47/l7_design_decisions_$b.jsonl \
    --iters-thin-path $D/l8_optimize/shared_l2opus47/l8_optimization_iterations_$b.jsonl \
    --iters-loop-path $D/l8_loop/shared_l2opus47/l8_loop_iterations_cluster11_${b}_tchebycheff.jsonl \
    --verify-path $D/verify_on_product/shared_l2opus47/verify_on_product_cluster11_$b.json
done
echo "reports in $REP"
