#!/usr/bin/env bash
# L3 single-run driver — clustering on the shared L2 input (opus47's L2).
#
# L3 is deterministic: HDBSCAN over local MiniLM embeddings of pain +
# expectation nodes, no Claude calls. With a fixed input there is
# exactly one clustering to produce, so the 3-branch matched pattern
# used upstream (L1, L2) does not apply here — three identical runs
# would just write the same file thrice.
#
# Model divergence re-enters the pipeline at L3b (cluster labelling),
# which IS a Claude call and does benefit from per-branch variation.
# That is run separately by scripts/run_l3b_shared_matched.sh and
# reads this script's single output.
#
# Input:
#   data/derived/l2_structure/shared_l1opus47/l2_graphs_full_opus47.jsonl
#
# Outputs (in data/derived/l3_clusters/shared_l2opus47/):
#   l3_clusters.jsonl     — cluster rows, placeholder "UNLABELED:" labels
#   l3_centroids.npy      — stacked centroids, referenced by the sidecar
#   *.meta.json           — artefact hashes
#   *.provenance.json     — encoder/clustering runtime tuple
#
# Cost: $0 — no Claude calls. Runtime seconds.
#
# Usage:
#   bash scripts/run_l3_single.sh
#
# Prerequisites:
#   - data/derived/l2_structure/shared_l1opus47/l2_graphs_full_opus47.jsonl
#   - uv venv with dev extras installed

set -euo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L2_INPUT="data/derived/l2_structure/shared_l1opus47/l2_graphs_full_opus47.jsonl"
OUT_DIR="data/derived/l3_clusters/shared_l2opus47"
CLUSTERS="$OUT_DIR/l3_clusters.jsonl"
CENTROIDS="$OUT_DIR/l3_centroids.npy"
RUN_ID="l3-shared-l2opus47"

mkdir -p "$OUT_DIR"

if [[ ! -f "$L2_INPUT" ]]; then
  echo "error: missing L2 input $L2_INPUT (run scripts/run_l2_matched.sh first)" >&2
  exit 1
fi

echo "==> [L3] input=$L2_INPUT run_id=$RUN_ID"
uv run python -m auditable_design.layers.l3_cluster \
  --graphs "$L2_INPUT" \
  --output "$CLUSTERS" \
  --centroids "$CENTROIDS" \
  --run-id "$RUN_ID"
echo "==> [L3] done: $CLUSTERS"
echo
echo "Next: bash scripts/run_l3b_shared_matched.sh (3 models label the same clusters)."
