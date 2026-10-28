"""Fixture for the end-to-end flow simulation (cluster_11, Opus 4.6).

Run from the repo root:  uv run python copilot-studio/kernel/tests/fixtures_symulacja.py

Takes the study's recorded model outputs (native payloads) as stand-ins for
the model, and the study's backfilled results as the expected values, so the
test can call the bundled Office Scripts in flow order and compare.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
D = ROOT / "data/derived"
M = ROOT / "data/derived_evchain/data/derived"
OUT = Path(__file__).resolve().parent / "fixtures" / "symulacja.json.gz"
CID, B = "cluster_11", "opus46"
LENSES = ["usability_fundamentals", "accessibility", "decision_psychology", "business_alignment", "interaction_design", "ux_architecture"]


def jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def one(rows: list[dict], cid: str = CID) -> dict:
    return next(r for r in rows if r.get("cluster_id") == cid)


l4_cluster = jsonl(D / f"l4_audit/audit_interaction_design/shared_l2opus47/audit_interaction_design_input_{CID}.jsonl")[0]
l4_cluster["representative_review_ids"] = one(jsonl(M / "l3_clusters/shared_l2opus47/l3_clusters.jsonl"))["representative_review_ids"]
l5_cluster = one(jsonl(M / f"l5_reconcile/shared_l2opus47/l3b_filtered_{B}.jsonl"))

l4 = []
for lens in LENSES:
    stem = f"l4_verdicts_audit_{lens}_{CID.replace('_', '')}_{B}_multimodal"
    native = jsonl(D / f"l4_audit/audit_{lens}/shared_l2opus47/{stem}.native.jsonl")[0]
    expected = jsonl(M / f"l4_audit/audit_{lens}/shared_l2opus47/{stem}.jsonl")[0]
    resp = native["payload"]["raw_response"] if native["status"] != "audited" else json.dumps(native["payload"], ensure_ascii=False)
    l4.append({"lens": expected["skill_id"], "status": native["status"], "response_text": resp,
               "expected_heuristics": expected["relevant_heuristics"], "claude_model": expected["claude_model"],
               "produced_at": expected["produced_at"], "skill_hash": expected["skill_hash"],
               "native_payload_ref": expected["native_payload_ref"]})

l5_native = one(jsonl(M / f"l5_reconcile/shared_l2opus47/l5_reconciled_{B}.native.jsonl"))
l6_native = one(jsonl(D / f"l6_weight/shared_l2opus47/l6_priority_{B}.native.jsonl"))
l7_native = one(jsonl(D / f"l7_decide/shared_l2opus47/l7_design_decisions_{B}.native.jsonl"))

fx = {
    "l4_cluster": l4_cluster,
    "l5_cluster": l5_cluster,
    "l4": l4,
    "l5": {"response_text": json.dumps({k: l5_native["payload"][k] for k in ("summary", "graph")}, ensure_ascii=False),
           "expected": one(jsonl(M / f"l5_reconcile/shared_l2opus47/l5_reconciled_{B}.jsonl"))},
    "l6": {"response_texts": [json.dumps(p["payload"], ensure_ascii=False) for p in l6_native["passes"]],
           "expected": one(jsonl(D / f"l6_weight/shared_l2opus47/l6_priority_{B}.jsonl"))},
    "l7": {"response_text": json.dumps(l7_native["payload"], ensure_ascii=False),
           "expected_decision": one(jsonl(D / f"l7_decide/shared_l2opus47/l7_design_decisions_{B}.jsonl")) if any(
               "cluster_id" in r for r in jsonl(D / f"l7_decide/shared_l2opus47/l7_design_decisions_{B}.jsonl")) else None,
           "expected_decisions_file": jsonl(D / f"l7_decide/shared_l2opus47/l7_design_decisions_{B}.jsonl"),
           "expected_principles_file": jsonl(D / f"l7_decide/shared_l2opus47/l7_design_principles_{B}.jsonl")},
}
# --- L8 thin spine + loop, verify, brief ------------------------------
thin_rows = [r for r in jsonl(D / f"l8_optimize/shared_l2opus47/l8_optimization_iterations_{B}.jsonl") if f"__{CID}__" in r["iteration_id"]]
thin_native = one(jsonl(D / f"l8_optimize/shared_l2opus47/l8_optimization_iterations_{B}.native.jsonl"))
loop_path = D / f"l8_loop/shared_l2opus47/l8_loop_iterations_{CID.replace('_', '')}_{B}_tchebycheff"
loop_rows = jsonl(Path(str(loop_path) + ".jsonl"))
loop_native = jsonl(Path(str(loop_path) + ".native.jsonl"))
# Labels as hard-coded in scripts/verify_on_product.py main().
labels = {
    "energy_manage.png": "energy_manage — dedicated energy surface with 22h 31m regen timer, Super upsell, Recharge (500 gems), Mini charge",
    "out_of_energy_home.png": "out_of_energy_home — blocking modal from home/between-lessons, Super with checkmark default, Recharge (450 gems), Quit lesson muted",
    "out_of_energy_mid_lesson.png": "out_of_energy_mid_lesson — modal blocks mid-lesson ('Translate this sentence' visible above), 'TRY 1 WEEK FOR FREE' primary, 'LOSE XP' as punitive alternative",
}
verify_payload = json.loads((D / f"verify_on_product/shared_l2opus47/verify_on_product_{CID.replace('_', '')}_{B}.json").read_text())
brief_md = (M / f"design_brief/shared_l2opus47/design_brief_{CID.replace('_', '')}_{B}.md").read_text()

fx["l8"] = {
    "run_id": thin_rows[0]["run_id"],
    "artifacts_dir": thin_rows[0]["design_artifact_ref"].rsplit("/", 1)[0],
    "thin_response_text": thin_native["payload"].get("raw_response") if thin_native["status"] == "fallback" else json.dumps(thin_native["payload"], ensure_ascii=False),
    "expected_thin": thin_rows,
    "loop_run_id": loop_rows[0]["run_id"] if loop_rows else None,
    "loop_tweaks": [json.dumps(r["tweak_payload"], ensure_ascii=False) for r in loop_native],
    "loop_reaudits": [json.dumps(r["reaudit_payload"], ensure_ascii=False) for r in loop_native],
    "expected_loop": loop_rows,
}
fx["verify"] = {"screenshots": [{"label": labels[k], "path": f"data/raw/duolingo_screenshots/{k}"} for k in labels],
                "response_text": json.dumps(verify_payload, ensure_ascii=False), "expected_payload": verify_payload}
fx["brief"] = {"expected_markdown": brief_md}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_bytes(gzip.compress(json.dumps(fx, ensure_ascii=False).encode("utf-8")))
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB); l4 statuses: {[x['status'] for x in l4]}")
