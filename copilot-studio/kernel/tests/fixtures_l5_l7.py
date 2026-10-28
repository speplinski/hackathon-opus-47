"""Build L5–L7 golden fixtures from the study data with the Python layers.

Run from the repo root:

    uv run python copilot-studio/kernel/tests/fixtures_l5_l7.py

Needs the study data (gitignored): data/derived_evchain (backfilled L5
inputs, see scripts/run_backfill_evidence_chain_study.sh) and
data/derived/l6_weight, data/derived/l7_decide. Writes
copilot-studio/kernel/tests/fixtures/l5_l7.json.

The model responses are reconstructed from the native sidecars, which
store the parsed payload: the response text fed to the parser is
json.dumps(payload) restricted to the keys the model emits.
"""

from __future__ import annotations

import json
from pathlib import Path

from auditable_design.layers import l5_reconcile as l5
from auditable_design.layers import l6_weight as l6
from auditable_design.layers import l7_decide as l7
from auditable_design.schemas import InsightCluster, PriorityScore, ReconciledVerdict

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "data/derived_evchain/data/derived/l5_reconcile/shared_l2opus47"
L6 = ROOT / "data/derived/l6_weight/shared_l2opus47"
L7 = ROOT / "data/derived/l7_decide/shared_l2opus47"
OUT = ROOT / "copilot-studio/kernel/tests/fixtures/l5_l7.json"

CASES = [("opus47", "cluster_11"), ("opus46", "cluster_11"), ("sonnet46", "cluster_11")]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _by_cluster(rows: list[dict]) -> dict[str, dict]:
    return {r["cluster_id"]: r for r in rows}


def main() -> None:
    cases = []
    for branch, cid in CASES:
        cluster = InsightCluster.model_validate(_by_cluster(_jsonl(EV / f"l3b_filtered_{branch}.jsonl"))[cid])
        bundles = l5.load_verdicts_bundle(EV / f"l5_bundle_{branch}.jsonl")
        bundle = bundles[cid]
        verdict_rows = [v.model_dump(mode="json") for v in bundle.verdicts_by_skill.values()]

        # --- L5
        native5 = _by_cluster(_jsonl(EV / f"l5_reconciled_{branch}.native.jsonl"))[cid]["payload"]
        response5 = json.dumps({"summary": native5["summary"], "graph": native5["graph"]}, ensure_ascii=False)
        payload5 = l5.parse_reconcile_response(response5, bundle=bundle, n_quotes=len(cluster.representative_quotes))
        reconciled = l5._build_reconciled_verdict(payload5, cid, bundle)
        stored = ReconciledVerdict.model_validate(
            _by_cluster(_jsonl(EV / f"l5_reconciled_{branch}.jsonl"))[cid]
        )
        assert stored == reconciled, f"{branch}/{cid}: recomputed L5 differs from backfilled file"

        # --- L6
        native6 = _by_cluster(_jsonl(L6 / f"l6_priority_{branch}.native.jsonl"))[cid]
        responses6 = [json.dumps(p["payload"], ensure_ascii=False) for p in native6["passes"] if p["status"] == "parsed"]
        dims = [l6.parse_priority_response(t)["dimensions"] for t in responses6]
        agg, delta = l6._aggregate_passes(dims)
        priority = PriorityScore(
            cluster_id=cid,
            dimensions=agg,
            meta_weights=dict(l6.DEFAULT_META_WEIGHTS),
            weighted_total=l6.weighted_total(agg, l6.DEFAULT_META_WEIGHTS),
            validation_passes=len(dims),
            validation_delta=delta,
        )
        stored6 = PriorityScore.model_validate(_by_cluster(_jsonl(L6 / f"l6_priority_{branch}.jsonl"))[cid])
        assert stored6.dimensions == priority.dimensions and stored6.validation_delta == priority.validation_delta

        # --- L7
        native7 = _by_cluster(_jsonl(L7 / f"l7_design_decisions_{branch}.native.jsonl"))[cid]["payload"]
        response7 = json.dumps(native7, ensure_ascii=False)
        payload7 = l7.parse_decide_response(response7, cluster=cluster, reconciled=reconciled)
        principle = l7._build_principle(payload7, cid)
        decision = l7._build_decision(payload7, cid)

        cases.append(
            {
                "branch": branch,
                "cluster_id": cid,
                "cluster": cluster.model_dump(mode="json"),
                "verdicts": verdict_rows,
                "l5": {
                    "message": l5.build_user_message(cluster, bundle),
                    "response_text": response5,
                    "ranked_violations": payload5["ranked_violations"],
                    "tensions": payload5["tensions"],
                    "gaps": payload5["gaps"],
                    "verdict": reconciled.model_dump(mode="json"),
                },
                "l6": {
                    "message": l6.build_user_message(cluster, reconciled),
                    "response_texts": responses6,
                    "priority": priority.model_dump(mode="json"),
                },
                "l7": {
                    "message": l7.build_user_message(cluster, reconciled, stored6),
                    "priority_in": stored6.model_dump(mode="json"),
                    "response_text": response7,
                    "principle": principle.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                },
            }
        )

    # Synthetic L6 aggregation cases (banker's rounding, 3-pass median).
    keys = ["severity", "reach", "persistence", "business_impact", "cognitive_cost"]
    synthetic_passes = [
        [[3, 2, 5, 7, 0], [4, 3, 6, 8, 1]],
        [[9, 9, 9, 9, 9], [8, 8, 8, 8, 8]],
        [[2, 5, 7, 1, 10], [6, 5, 3, 1, 0], [4, 9, 5, 2, 7]],
    ]
    aggregation = []
    for ps in synthetic_passes:
        dl = [dict(zip(keys, p, strict=True)) for p in ps]
        a, d = l6._aggregate_passes(dl)
        aggregation.append(
            {
                "passes": dl,
                "dims": a,
                "delta": d,
                "needs_third": l6._needs_third_pass(dl[0], dl[1]),
                "weighted_total": l6.weighted_total(a, l6.DEFAULT_META_WEIGHTS),
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"cases": cases, "l6_aggregation": aggregation}, ensure_ascii=False))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(cases)} cases)")


if __name__ == "__main__":
    main()
