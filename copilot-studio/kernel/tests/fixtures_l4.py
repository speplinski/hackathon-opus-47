"""Write L4 fixtures for tests/l4.test.ts from the Python pipeline.

Run from the repo root:
    uv run python copilot-studio/kernel/tests/fixtures_l4.py

Needs the study data (data/derived, gitignored) and the backfilled
clusters (data/derived_evchain, from scripts/run_backfill_evidence_chain_study.sh).
Every expected value is produced by the Python modules themselves.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from auditable_design.layers import (
    l4_audit,
    l4_audit_accessibility,
    l4_audit_business_alignment,
    l4_audit_decision_psychology,
    l4_audit_interaction_design,
    l4_audit_ux_architecture,
)
from auditable_design.schemas import InsightCluster

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "fixtures" / "l4.json"

LENSES = {
    "audit-usability-fundamentals": (l4_audit, "usability_fundamentals"),
    "audit-accessibility": (l4_audit_accessibility, "accessibility"),
    "audit-business-alignment": (l4_audit_business_alignment, "business_alignment"),
    "audit-decision-psychology": (l4_audit_decision_psychology, "decision_psychology"),
    "audit-interaction-design": (l4_audit_interaction_design, "interaction_design"),
    "audit-ux-architecture": (l4_audit_ux_architecture, "ux_architecture"),
}
CLUSTERS = ["cluster_11", "cluster_00", "cluster_12"]
ALL_GRID = ["cluster_00", "cluster_01", "cluster_06", "cluster_11", "cluster_12", "cluster_13"]
BRANCHES = ["opus47", "opus46", "sonnet46"]


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _clusters() -> dict[str, dict]:
    backfilled = {
        c["cluster_id"]: c
        for c in _jsonl(
            REPO / "data/derived_evchain/data/derived/l3_clusters/shared_l2opus47/l3_clusters.jsonl"
        )
    }
    out = {}
    for cid in ALL_GRID:
        row = _jsonl(
            REPO
            / f"data/derived/l4_audit/audit_accessibility/shared_l2opus47/audit_accessibility_input_{cid}.jsonl"
        )[0]
        row["representative_review_ids"] = backfilled[cid]["representative_review_ids"]
        assert row["representative_quotes"] == backfilled[cid]["representative_quotes"]
        out[cid] = InsightCluster.model_validate(row).model_dump(mode="json")
    return out


def _native(lens_dir: str, cid: str, branch: str) -> dict:
    stem = cid.replace("_", "")
    p = (
        REPO
        / f"data/derived/l4_audit/audit_{lens_dir}/shared_l2opus47"
        / f"l4_verdicts_audit_{lens_dir}_{stem}_{branch}_multimodal.native.jsonl"
    )
    return _jsonl(p)[0]


def _expect_parse(mod, text: str, n_quotes: int) -> dict:
    try:
        payload = mod.parse_audit_response(text, n_quotes=n_quotes)
        return {"ok": True, "payload": payload, "reason": None}
    except mod.AuditParseError as e:
        return {"ok": False, "payload": None, "reason": str(e)}


def main() -> None:
    clusters = _clusters()
    cases: list[dict] = []

    # 1) audited study payloads: message + conversion (prefer opus47)
    for lens, (mod, lens_dir) in LENSES.items():
        for cid in CLUSTERS:
            cluster = InsightCluster.model_validate(clusters[cid])
            for branch in BRANCHES:
                row = _native(lens_dir, cid, branch)
                if row["status"] == "audited":
                    break
            payload = row["payload"]
            # Simulate a model reply with prose and a code fence around the JSON.
            text = "Here is the audit.\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"
            parsed = _expect_parse(mod, text, len(cluster.representative_quotes))
            assert parsed["ok"], (lens, cid, parsed["reason"])
            hv = [v.model_dump(mode="json") for v in mod._build_heuristic_violations(parsed["payload"], cluster)]
            cases.append(
                {
                    "kind": "audited",
                    "lens": lens,
                    "cluster_id": cid,
                    "branch": branch,
                    "response_text": text,
                    "expected_message": mod.build_user_message(cluster),
                    "expected_violations": hv,
                    "expected_verdict_id": mod._verdict_id(mod.SKILL_ID, cid),
                    "expected_native_ref": f"{mod.DEFAULT_NATIVE.name}#{mod._verdict_id(mod.SKILL_ID, cid)}",
                }
            )

    # 2) every real fallback in the grid: Python reason must match exactly
    for lens, (mod, lens_dir) in LENSES.items():
        for cid in ALL_GRID:
            for branch in BRANCHES:
                row = _native(lens_dir, cid, branch)
                if row["status"] == "audited":
                    continue
                raw = row["payload"]["raw_response"]
                cluster = InsightCluster.model_validate(clusters[cid])
                parsed = _expect_parse(mod, raw, len(cluster.representative_quotes))
                assert not parsed["ok"]
                cases.append(
                    {
                        "kind": "rejected",
                        "lens": lens,
                        "cluster_id": cid,
                        "branch": branch,
                        "response_text": raw,
                        "expected_reason": parsed["reason"],
                        "reason_exact": True,
                    }
                )

    # 3) synthetic rejections built by mutating a valid payload
    def base(lens: str) -> tuple[object, dict, InsightCluster]:
        mod, lens_dir = LENSES[lens]
        for branch in BRANCHES:
            row = _native(lens_dir, "cluster_11", branch)
            if row["status"] == "audited":
                break
        payload = copy.deepcopy(row["payload"])
        payload["findings"] = payload["findings"][:1]  # keep fixtures small
        return mod, payload, InsightCluster.model_validate(clusters["cluster_11"])

    mutations = []

    def add(lens: str, name: str, fn, exact: bool = True, text: str | None = None) -> None:
        mod, payload, cluster = base(lens)
        if text is None:
            fn(payload)
            text = json.dumps(payload, ensure_ascii=False)
        parsed = _expect_parse(mod, text, len(cluster.representative_quotes))
        mutations.append(name)
        cases.append(
            {
                "kind": "accepted" if parsed["ok"] else "rejected",
                "lens": lens,
                "cluster_id": "cluster_11",
                "name": name,
                "response_text": text,
                "expected_reason": parsed["reason"],
                "reason_exact": exact,
            }
        )

    N = "audit-usability-fundamentals"
    A = "audit-accessibility"
    B = "audit-business-alignment"
    D = "audit-decision-psychology"
    I = "audit-interaction-design"  # noqa: E741 (one letter per lens, as N, A, B, D, U)
    U = "audit-ux-architecture"
    add(N, "no_json", None, exact=False, text="I cannot audit this cluster.")
    add(N, "malformed_json", None, exact=False, text='{"summary": "x", "findings": [}')
    add(N, "extra_top_key", lambda p: p.update({"notes": "x"}))
    add(N, "empty_quotes", lambda p: p["findings"][0].update({"evidence_quote_idxs": []}))
    add(N, "idx_out_of_range", lambda p: p["findings"][0].update({"evidence_quote_idxs": [0, 9]}))
    add(N, "bool_severity", lambda p: p["findings"][0].update({"severity": True}))
    add(N, "dimension_score_6", lambda p: p["dimension_scores"].update({"action_cognition": 6}))
    add(N, "bad_dimension", lambda p: p["findings"][0].update({"dimension": "vibes"}))
    add(A, "aaa_not_advisory", lambda p: p["findings"][0].update({"wcag_level": "AAA", "wcag_ref": "1.4.6", "severity": 3}))
    add(A, "obsolete_411", lambda p: p["findings"][0].update({"wcag_level": "A", "wcag_ref": "4.1.1"}))
    add(A, "inclusive_with_ref", lambda p: p["findings"][0].update({"wcag_level": "inclusive", "wcag_ref": "1.4.3"}))
    add(A, "bad_ref_format", lambda p: p["findings"][0].update({"wcag_level": "AA", "wcag_ref": "1.4"}))
    add(
        A,
        "artifact_only_ok",
        lambda p: p["findings"][0].update({"evidence_source": ["screenshot"], "evidence_quote_idxs": []}),
    )
    add(A, "dup_sources", lambda p: p["findings"][0].update({"evidence_source": ["quotes", "quotes"]}))
    add(B, "tension_unordered", lambda p: p["findings"][0].update({"building_blocks": ["vp", "cs"], "tension": ["vp", "cs"]}))
    add(B, "tension_not_in_blocks", lambda p: p["findings"][0].update({"building_blocks": ["vp"], "tension": ["cs", "vp"]}))
    add(B, "bad_pattern", lambda p: p["findings"][0].update({"pattern": "ads"}))
    add(B, "dup_pair", lambda p: p["findings"].append(copy.deepcopy(p["findings"][0])))
    add(D, "no_quotes_source", lambda p: p["findings"][0].update({"evidence_source": ["screenshot"]}))
    add(D, "dark_pattern_sev1", lambda p: p["findings"][0].update({"intent": "dark_pattern", "severity": 1}))
    add(D, "dup_mechanism", lambda p: p["findings"].append(copy.deepcopy(p["findings"][0])))
    add(I, "flow_excise_none", lambda p: p["findings"][0].update({"dimension": "flow_excise", "excise_type": "none", "severity": 1}))
    add(I, "bad_posture", lambda p: p["findings"][0].update({"posture": "floating"}))
    add(
        I,
        "idxs_without_quotes",
        lambda p: p["findings"][0].update({"evidence_source": ["screenshot"], "evidence_quote_idxs": [0, 4]}),
    )
    add(U, "bad_decision_mode", lambda p: p["findings"][0].update({"decision_mode": "whim"}))
    add(U, "missing_key", lambda p: p["findings"][0].pop("recommendation"))

    # 4) Python repr of tricky strings (used inside reasoning strings)
    reprs = [
        "plain",
        "it's",
        'say "hi"',
        "both ' and \"",
        "back\\slash",
        "tab\tnew\nline\rcr",
        "nbsp here",
        "zero​width",
        "emoji 🔥 ok",
        "polskie ąęłńóśźż",
        "bell\x07",
        "del\x7f",
        "soft­hyphen",
        "line sep",
    ]
    repr_cases = [{"s": s, "repr": repr(s)} for s in reprs]

    data = {"clusters": clusters, "cases": cases, "repr_cases": repr_cases}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kinds = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB): {kinds}")


if __name__ == "__main__":
    main()
