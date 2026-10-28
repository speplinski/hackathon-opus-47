"""Build L0–L3b fixtures from the corpus, the replay log and study clusters.

Run from the repo root:  uv run python copilot-studio/kernel/tests/fixtures_l0_l3.py
Expected values come from the Python pipeline, so the TypeScript port is
checked against real model responses, not invented ones.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from auditable_design.layers import l1_classify, l2_structure, l3b_label
from auditable_design.schemas import RawReview

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "fixtures" / "l0_l3.json"


def jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


reviews = {r["review_id"]: r for r in jsonl(ROOT / "data/raw/corpus.jsonl")}
log = jsonl(ROOT / "data/cache/responses.jsonl")
rid_re = re.compile(r'<user_review id="([^"]+)">')

# --- L1 / L2: reviews that have an Opus 4.7 L2 response in the log ----
l2_rows = {}
l1_rows = {}
for r in log:
    m = rid_re.search(r["prompt"])
    if not m:
        continue
    rid = m.group(1)
    if r["skill_id"] == "structure-of-complaint" and r["model"] == "claude-opus-4-7":
        l2_rows.setdefault(rid, r)
    if r["skill_id"] == "l1_classify":
        l1_rows.setdefault(rid, r)

picked = sorted(set(l2_rows) & set(l1_rows) & set(reviews))
# Prefer reviews whose text contains markup or non-BMP characters, then fill.
special = [rid for rid in picked if re.search(r"[<>&]|[\U00010000-\U0010FFFF]", reviews[rid]["text"])]
sample = (special[:10] + [rid for rid in picked if rid not in special])[:60]

l1_cases, l2_cases = [], []
for rid in sample:
    raw = RawReview.model_validate(reviews[rid])
    utt = {"utterance_id": rid, "source_type": "app_review", "text": raw.text, "rating": raw.rating}
    # L1
    resp = l1_rows[rid]["response"]
    try:
        is_ux, conf, tags = l1_classify.parse_response(resp)
        exp = {"ok": True, "is_ux_relevant": is_ux, "classifier_confidence": conf, "rubric_tags": tags}
    except Exception as e:
        exp = {"ok": False, "error": str(e)}
    l1_cases.append({"utterance": utt, "message": l1_classify.build_user_message(raw), "response_text": resp, "expected": exp})
    # L2
    resp2 = l2_rows[rid]["response"]
    try:
        o = l2_structure.extract_graph(raw, resp2)
        if o.status == "graph":
            exp2 = {"status": "graph", "graph": o.graph.model_dump(mode="json")}
        else:
            exp2 = {"status": "quarantine", "reason": o.quarantine_record["reason"], "node_count": o.quarantine_record["node_count"]}
    except Exception as e:
        exp2 = {"status": "error", "error": str(e)}
    l2_cases.append({"utterance": utt, "message": l2_structure.build_user_message(raw), "response_text": resp2, "expected": exp2})

# --- L3b: study clusters (message) + logged label responses (parser) ---
clusters = jsonl(ROOT / "data/derived/l3_clusters/shared_l2opus47/l3_clusters.jsonl")
l3b_msgs = [{"quotes": c["representative_quotes"], "message": l3b_label.build_user_message(c["representative_quotes"])} for c in clusters]
l3b_parse = []
for r in [x for x in log if x["skill_id"] == "label-cluster"][:40]:
    try:
        l3b_parse.append({"response_text": r["response"], "ok": True, "label": l3b_label.parse_label_response(r["response"])})
    except Exception as e:
        l3b_parse.append({"response_text": r["response"], "ok": False, "error": str(e)})

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({"l1": l1_cases, "l2": l2_cases, "l3b_messages": l3b_msgs, "l3b_parse": l3b_parse}, ensure_ascii=False, indent=1))
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB): l1={len(l1_cases)} l2={len(l2_cases)} l3b={len(l3b_msgs)}/{len(l3b_parse)}")
print("l2 statuses:", {s: sum(1 for c in l2_cases if c['expected']['status'] == s) for s in ('graph', 'quarantine', 'error')})
print("l1 ok:", sum(1 for c in l1_cases if c['expected']['ok']), "l3b ok:", sum(1 for c in l3b_parse if c['ok']), "special:", len(special))

# --- evidence lookup (AD_Dowody): cluster_11, Opus 4.7, backfilled ----
M = ROOT / "data/derived_evchain/data/derived"
c11 = next(c for c in jsonl(M / "l5_reconcile/shared_l2opus47/l3b_filtered_opus47.jsonl") if c["cluster_id"] == "cluster_11")
r11 = next(v for v in jsonl(M / "l5_reconcile/shared_l2opus47/l5_reconciled_opus47.jsonl") if v["cluster_id"] == "cluster_11")
(OUT.parent / "dowody.json").write_text(json.dumps({"cluster": c11, "reconciled": r11}, ensure_ascii=False, indent=1))
print("wrote dowody fixture")
