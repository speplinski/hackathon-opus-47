#!/usr/bin/env bash
# L3b per-pipeline matched-model eval driver.
#
# Companion to the shared-labeller baseline documented in
# `docs/evals/l3b_full_corpus_three_way.md`. The baseline uses Haiku 4.5 for
# all three inputs, which isolates *input* divergence (different L3
# inventories, same labeller). This script runs the complementary eval:
# each pipeline branch is labelled by the model that produced its upstream
# L1/L2 output, preserving per-branch model consistency end-to-end.
#
# Model matching:
#   opus46 input   →  claude-opus-4-6   (L1 default in layers/l1_classify.py)
#   opus47 input   →  claude-opus-4-7   (L2 default in layers/l2_structure.py)
#   sonnet46 input →  claude-sonnet-4-6
#
# Outputs are written to a sibling `matched/` subdirectory so the Haiku
# baseline artefacts at data/derived/l3b_labeled_clusters/*.jsonl are left
# untouched — both sets coexist for the matched-vs-shared comparison in a
# follow-up eval doc.
#
# Mode is `live`: the replay cache only contains Haiku entries for these
# three inputs. Cache keys include the model id, so new Opus/Sonnet calls
# won't collide with baseline entries and will be written to the same
# `data/cache/responses.jsonl`, extending the replay log rather than
# overwriting it.
#
# Cost: 31 calls total (14 opus46 + 10 opus47 + 7 sonnet46) with short
# prompts (~800 input / ~80 output tokens each). Tracker estimate well
# under $1; real billing probably <$0.50 after the Opus 3× overestimate
# correction. The $2 per-run ceiling (L3b default) catches misconfiguration,
# not normal spend.
#
# Usage:
#   bash scripts/run_l3b_matched.sh
#
# Exit codes:
#   0 — all three runs succeeded
#   1 — at least one run failed (script aborts on first failure via -e)
#
# Prerequisites:
#   - L3 inputs present at data/derived/l3_clusters/l3_clusters_full_*.jsonl
#     (verify sha256 against docs/evals/l3_full_corpus_three_way.md before
#     assuming this run is reproducible against that eval)
#   - ANTHROPIC_API_KEY in env
#   - uv venv with dev extras installed

set -euo pipefail

# Paths are relative to the repo root; run the script from there.
if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

L3_DIR="data/derived/l3_clusters"
OUT_DIR="data/derived/l3b_labeled_clusters/matched"
mkdir -p "$OUT_DIR"

run_matched() {
  local branch="$1"       # opus46 | opus47 | sonnet46
  local model="$2"        # claude-opus-4-6 | claude-opus-4-7 | claude-sonnet-4-6
  local input="$L3_DIR/l3_clusters_full_${branch}.jsonl"
  local output="$OUT_DIR/l3b_labeled_clusters_full_${branch}.jsonl"
  local run_id="l3b-full-${branch}-matched"

  if [[ ! -f "$input" ]]; then
    echo "error: missing L3 input $input" >&2
    return 1
  fi

  echo "==> [$branch] model=$model run_id=$run_id"
  uv run python -m auditable_design.layers.l3b_label \
    --clusters "$input" \
    --output "$output" \
    --run-id "$run_id" \
    --model "$model" \
    --mode live
  echo "==> [$branch] done: $output"
  echo
}

run_matched "opus46"   "claude-opus-4-6"
run_matched "opus47"   "claude-opus-4-7"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three matched L3b runs complete. Outputs in $OUT_DIR."
echo "Next: sha256sum the three outputs and write the matched eval doc."
