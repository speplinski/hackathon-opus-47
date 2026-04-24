#!/usr/bin/env bash
# verify_on_product shared-input matched runner — hero cluster_11 × 3 models.
#
# Scope matches the L8 loop: hero cluster_11 only. The VLM hook
# consumes the L5 reconciled verdict (must have exactly 1 cluster)
# and checks every heuristic against all three real Duolingo
# screenshots at once — confirmed / partial / refuted verdicts plus
# any out-of-baseline defects Opus 4.7 may surface.
#
# Per-branch: filter the branch's L5 reconciled JSONL down to
# cluster_11 into a 1-row file (required by verify_on_product's
# "exactly one cluster" contract), then invoke the script.
#
# Model matching:
#   opus47   →  claude-opus-4-7    (MAIN — dissent-willing baseline)
#   opus46   →  claude-opus-4-6    (comparative)
#   sonnet46 →  claude-sonnet-4-6  (comparative)
#
# Outputs per branch (in data/derived/verify_on_product/shared_l2opus47/):
#   verify_on_product_cluster11_<short>.json
#   verify_on_product_cluster11_<short>.md
#   verify_on_product_cluster11_<short>.provenance.json
#
# Cost: 3 VLM calls with 3 PNGs + heuristic list each. Reported
# \$1-3, real ~\$0.50-1 after Opus 3x correction.
#
# Usage:
#   bash scripts/run_verify_on_product_shared_matched.sh
#
# Prerequisites: L5 reconciled at data/derived/l5_reconcile/shared_l2opus47/,
# screenshots at data/raw/duolingo_screenshots/, ANTHROPIC_API_KEY, uv venv.

set -uo pipefail

if [[ ! -d "src/auditable_design" ]]; then
  echo "error: run this from the repo root (src/auditable_design not found)" >&2
  exit 1
fi

CLUSTER_ID="cluster_11"
L5_DIR="$(pwd)/data/derived/l5_reconcile/shared_l2opus47"
OUT_DIR="$(pwd)/data/derived/verify_on_product/shared_l2opus47"
SS_DIR="$(pwd)/data/raw/duolingo_screenshots"
TMP_DIR="$(pwd)/data/derived/verify_on_product/shared_l2opus47/_filtered_inputs"
mkdir -p "$OUT_DIR" "$TMP_DIR"

for ss in energy_manage.png out_of_energy_home.png out_of_energy_mid_lesson.png; do
  if [[ ! -f "$SS_DIR/$ss" ]]; then
    echo "error: missing screenshot $SS_DIR/$ss" >&2
    exit 1
  fi
done

# Filter a branch's L5 reconciled down to a single-cluster JSONL.
filter_for_cluster() {
  local branch="$1"
  local src="$L5_DIR/l5_reconciled_${branch}.jsonl"
  local dst="$TMP_DIR/l5_reconciled_${CLUSTER_ID}_${branch}.jsonl"
  python3 -c "
import json, sys
src = '$src'; dst = '$dst'; cid = '$CLUSTER_ID'
kept = 0
with open(dst, 'w', encoding='utf-8') as out:
    for line in open(src, encoding='utf-8'):
        if not line.strip(): continue
        row = json.loads(line)
        if row.get('cluster_id') == cid:
            out.write(line if line.endswith('\n') else line + '\n')
            kept += 1
if kept != 1:
    print(f'error: expected 1 {cid} row in {src}, kept {kept}', file=sys.stderr)
    sys.exit(1)
"
  echo "$dst"
}

run_matched() {
  local branch="$1"
  local model="$2"
  local reconciled
  reconciled=$(filter_for_cluster "$branch") || {
    echo "filter failed for $branch" >&2
    return 1
  }

  echo "==> [$branch] model=$model cluster=$CLUSTER_ID"
  local rc=0
  uv run python scripts/verify_on_product.py \
    --reconciled "$reconciled" \
    --screenshots-dir "$SS_DIR" \
    --out-dir "$OUT_DIR" \
    --model "$model" || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "==> [$branch] exited $rc"
  else
    echo "==> [$branch] done."
  fi
  echo
}

# opus47 first — MAIN pipeline
run_matched "opus47"   "claude-opus-4-7"

# comparative baselines
run_matched "opus46"   "claude-opus-4-6"
run_matched "sonnet46" "claude-sonnet-4-6"

echo "All three verify_on_product matched runs complete. Outputs in $OUT_DIR."
echo "Next: export_design_brief (hero cluster)."
