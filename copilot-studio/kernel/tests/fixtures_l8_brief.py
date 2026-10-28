"""Build fixtures for tests/l8_brief.test.ts from the hackathon study data.

Run from the repo root:
    uv run python copilot-studio/kernel/tests/fixtures_l8_brief.py

Every expected value comes from the Python pipeline itself (messages,
iterations, artifacts, verifier verdicts, parser errors, verify markdown,
the design brief), so the TypeScript port is checked against the code it
replaces, on real study inputs. Model responses are the stored native
payloads replayed through fake clients — no API calls.

Needs data/derived (and data/derived_evchain for the brief); writes
copilot-studio/kernel/tests/fixtures/l8_brief.json.gz (gzip: ~900 KB of JSON).
"""

from __future__ import annotations

import asyncio
import gzip
import importlib.util
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

from auditable_design.claude_client import ClaudeResponse  # noqa: E402
from auditable_design.evaluators import pareto, tchebycheff  # noqa: E402
from auditable_design.layers import l8_optimize as l8  # noqa: E402
from auditable_design.layers import l8_optimize_loop as loop  # noqa: E402
from auditable_design.layers.l6_weight import load_reconciled_verdicts  # noqa: E402
from auditable_design.layers.l7_decide import load_priority_scores  # noqa: E402
from auditable_design.schemas import (  # noqa: E402
    InsightCluster,
    OptimizationIteration,
)

D = REPO / "data/derived"
E = REPO / "data/derived_evchain/data/derived"
OUT = REPO / "copilot-studio/kernel/tests/fixtures/l8_brief.json.gz"
ART = "ART"  # artifact-ref prefix used in fixtures instead of a tmp dir
BRANCHES = ["opus47", "opus46", "sonnet46"]
MODEL = {"opus47": "claude-opus-4-7", "opus46": "claude-opus-4-6", "sonnet46": "claude-sonnet-4-6"}


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def _jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _cluster(p: Path, cid: str) -> InsightCluster:
    for r in _jsonl(p):
        if r["cluster_id"] == cid:
            return InsightCluster.model_validate(r)
    raise KeyError(cid)


def _dump(model) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _iter_json(it: OptimizationIteration, tmp: Path) -> dict[str, Any]:
    d = _dump(it)
    d.pop("recorded_at")
    d["design_artifact_ref"] = d["design_artifact_ref"].replace(str(tmp), ART)
    return d


def _resp(text: str) -> ClaudeResponse:
    return ClaudeResponse(
        call_id="x", key_hash="x", skill_id="x", skill_hash="x", response=text,
        input_tokens=0, output_tokens=0, cost_usd=0.0, model="x", temperature=0.0, prompt="",
        timestamp="2026-01-01T00:00:00+00:00", cache_hit=False, elapsed_s=0.0,
    )


@dataclass
class Fake:
    by_skill: dict[str, list[str]]
    served: list[dict[str, str]] = field(default_factory=list)

    async def call(self, *, system, user, model, skill_id, skill_hash, temperature=0.0, max_tokens=0):
        q = self.by_skill.get(skill_id, [])
        text = q.pop(0) if q else "NO MORE SCRIPTED RESPONSES"
        self.served.append({"skill_id": skill_id, "user": user, "response": text})
        return _resp(text)


def _artifacts(tmp: Path) -> dict[str, str]:
    return {f"{ART}/{p.name}": p.read_text(encoding="utf-8") for p in sorted(tmp.glob("*.md"))}


# ---------------------------------------------------------------------------
# Thin spine (iteration 0 + iteration 1, Pareto verdict)
# ---------------------------------------------------------------------------


def thin_cases() -> list[dict[str, Any]]:
    cases = []
    for b in BRANCHES:
        recon_map = load_reconciled_verdicts(D / f"l5_reconcile/shared_l2opus47/l5_reconciled_{b}.jsonl")
        decisions = l8.load_decisions(D / f"l7_decide/shared_l2opus47/l7_design_decisions_{b}.jsonl")
        natives = {r["cluster_id"]: r for r in _jsonl(D / f"l8_optimize/shared_l2opus47/l8_optimization_iterations_{b}.native.jsonl")}
        stored = {r["iteration_id"]: r for r in _jsonl(D / f"l8_optimize/shared_l2opus47/l8_optimization_iterations_{b}.jsonl")}
        cids = ["cluster_11", "cluster_00"] if b == "opus47" else ["cluster_11"]
        for cid in cids:
            cluster = _cluster(D / f"l5_reconcile/shared_l2opus47/l3b_filtered_{b}.jsonl", cid)
            nat = natives[cid]
            text = nat["payload"].get("raw_response") if nat["status"] == "fallback" else json.dumps(nat["payload"], ensure_ascii=False)
            tmp = Path(tempfile.mkdtemp())
            fake = Fake({"design-optimize": [text]})
            out = asyncio.run(l8.optimize_cluster(
                cluster, recon_map[cid], None, decisions[cid], fake,
                skill_hash_value="0" * 64, run_id=f"l8-thin-{b}", artifacts_dir=tmp,
            ))
            base, prop = _iter_json(out.baseline, tmp), _iter_json(out.proposed, tmp)
            # sanity: replay reproduces the stored study rows
            for it in (base, prop):
                s = dict(stored[it["iteration_id"]])
                for k in ("recorded_at", "design_artifact_ref", "run_id"):
                    s.pop(k)
                mine = {k: v for k, v in it.items() if k not in ("design_artifact_ref", "run_id")}
                assert mine == s, (b, cid, it["iteration_id"])
            cases.append({
                "name": f"{b}/{cid}",
                "cluster": _dump(cluster),
                "reconciled": _dump(recon_map[cid]),
                "decision": _dump(decisions[cid]),
                "run_id": f"l8-thin-{b}",
                "response_text": text,
                "expected": {
                    "reaudit_message": fake.served[0]["user"],
                    "baseline_heuristics": l8.reconciled_heuristic_list(recon_map[cid]),
                    "baseline": base,
                    "proposed": prop,
                    "status": out.status,
                    "native_payload": out.native_payload,
                    "verdict": {"accepted": out.verdict.accepted, "reason": out.verdict.reason,
                                "regression_count": out.verdict.regression_count,
                                "dominance": out.verdict.dominance,
                                "delta_per_heuristic": out.verdict.delta_per_heuristic},
                    "artifacts": _artifacts(tmp),
                },
            })
            shutil.rmtree(tmp)
    return cases


# ---------------------------------------------------------------------------
# Multi-round loop
# ---------------------------------------------------------------------------


def _loop_inputs(cid: str, b: str, verifier: str):
    if cid == "cluster_02":
        cluster = _cluster(D / "l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl", cid)
        recon = load_reconciled_verdicts(D / f"l5_reconcile/l5_reconciled_cluster02_{b}.jsonl")[cid]
        decision = l8.load_decisions(D / f"l7_decide/l7_design_decisions_cluster02_{b}.jsonl")[cid]
        thin = D / f"l8_optimize/l8_optimization_iterations_cluster02_{b}.jsonl"
        native = D / f"l8_loop/l8_loop_iterations_cluster02_{b}_{verifier}.native.jsonl"
    else:
        cluster = _cluster(D / f"l5_reconcile/shared_l2opus47/l3b_filtered_{b}.jsonl", cid)
        recon = load_reconciled_verdicts(D / f"l5_reconcile/shared_l2opus47/l5_reconciled_{b}.jsonl")[cid]
        decision = l8.load_decisions(D / f"l7_decide/shared_l2opus47/l7_design_decisions_{b}.jsonl")[cid]
        thin = D / f"l8_optimize/shared_l2opus47/l8_optimization_iterations_{b}.jsonl"
        native = D / f"l8_loop/shared_l2opus47/l8_loop_iterations_{cid.replace('_', '')}_{b}_{verifier}.native.jsonl"
    existing = loop._read_existing_iterations(thin, cid)
    if cid == "cluster_02":
        # The cluster_02 loop runs re-audited against whichever L5 verdict
        # produced the thin-spine baseline; pick the one whose heuristic
        # list matches iteration 0.
        want = set(existing[0].scores["reconciled"])
        for cand in BRANCHES:
            r = load_reconciled_verdicts(D / f"l5_reconcile/l5_reconciled_cluster02_{cand}.jsonl")[cid]
            if set(l8.reconciled_heuristic_list(r)) == want:
                recon = r
                break
    rows = _jsonl(native) if native.exists() and native.stat().st_size else []
    return cluster, recon, decision, existing, rows


def loop_cases() -> list[dict[str, Any]]:
    cases = []
    combos = [("cluster_02", b, v) for b in BRANCHES for v in ("tchebycheff", "pareto")]
    combos += [("cluster_11", b, "tchebycheff") for b in BRANCHES]
    for cid, b, verifier in combos:
        cluster, recon, decision, existing, rows = _loop_inputs(cid, b, verifier)
        tweaks = [json.dumps(r["tweak_payload"], ensure_ascii=False) for r in rows]
        reaudits = [json.dumps(r["reaudit_payload"], ensure_ascii=False) for r in rows]
        tmp = Path(tempfile.mkdtemp())
        fake = Fake({"design-tweak": tweaks, "design-optimize": reaudits})
        out = asyncio.run(loop.run_loop(
            cluster, recon, decision, None, existing, fake,
            verifier=verifier, run_id=f"l8-loop-{b}-{verifier}", artifacts_dir=tmp,
            tweak_skill_hash_value="0" * 64, reaudit_skill_hash_value="0" * 64,
        ))
        cases.append({
            "name": f"{cid}/{b}/{verifier}",
            "cluster": _dump(cluster),
            "reconciled": _dump(recon),
            "decision": _dump(decision),
            "existing_iterations": [{k: v for k, v in _dump(it).items()} for it in existing],
            "verifier": verifier,
            "run_id": f"l8-loop-{b}-{verifier}",
            "expected": {
                "calls": fake.served,
                "new_iterations": [_iter_json(it, tmp) for it in out.new_iterations],
                "verdicts": [{"verifier": v.verifier, "accepted": v.accepted, "reason": v.reason,
                              "regression_count": v.regression_count, "converged": v.converged,
                              "delta_per_heuristic": v.delta_per_heuristic} for v in out.verdicts],
                "native_payloads": out.native_payloads,
                "termination_reason": out.termination_reason,
                "final_parent_id": out.final_parent_id,
                "artifacts": _artifacts(tmp),
            },
        })
        shutil.rmtree(tmp)
    return cases


# ---------------------------------------------------------------------------
# Verifier pairs and parser rejections
# ---------------------------------------------------------------------------


def verifier_cases(thin: list[dict[str, Any]], loops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[tuple[dict[str, int], dict[str, int]]] = []
    for c in thin:
        pairs.append((c["expected"]["baseline"]["scores"]["reconciled"], c["expected"]["proposed"]["scores"]["reconciled"]))
    for c in loops:
        its = {it["iteration_id"]: it for it in c["existing_iterations"] + c["expected"]["new_iterations"]}
        for it in c["expected"]["new_iterations"]:
            pairs.append((its[it["parent_iteration_id"]]["scores"]["reconciled"], it["scores"]["reconciled"]))
    synthetic = [
        ({"a": 7, "b": 5}, {"a": 7, "b": 5}),
        ({"a": 0, "b": 0}, {"a": 3, "b": 0}),
        ({}, {}),
        ({"a": 9, "b": 3, "c": 5}, {"a": 7, "b": 5, "c": 5}),
        ({"a": 9, "b": 3, "c": 5}, {"a": 3, "b": 7, "c": 7}),
        ({"a": 9, "b": 9}, {"a": 9, "b": 7}),
        ({"a": 5, "b": 5}, {"a": 3, "b": 9}),
        ({"a": 9, "b": 5}, {"a": 9, "b": 0}),
        ({"z": 9, "a": 9}, {"z": 7, "a": 7}),
        ({"a": 10, "b": 4}, {"a": 9, "b": 4}),
    ]
    out = []
    for parent, child in pairs + synthetic:
        p = pareto.verdict(parent, child)
        p2 = pareto.verdict(parent, child, max_regression=2)
        t = tchebycheff.verdict(parent, child)
        t2 = tchebycheff.verdict(parent, child, min_improvement_pct=25.0)
        out.append({
            "parent": parent, "child": child,
            "pareto": {"accepted": p.accepted, "reason": p.reason, "regression_count": p.regression_count,
                       "dominance": p.dominance, "delta_per_heuristic": p.delta_per_heuristic},
            "pareto_max2": {"accepted": p2.accepted, "reason": p2.reason},
            "tchebycheff": {"accepted": t.accepted, "reason": t.reason, "parent_cost": t.parent_cost,
                            "child_cost": t.child_cost, "binding_heuristic": t.binding_heuristic,
                            "improvement_pct": t.improvement_pct, "converged": t.converged,
                            "regression_count": t.regression_count, "delta_per_heuristic": t.delta_per_heuristic},
            "tchebycheff_25": {"accepted": t2.accepted, "reason": t2.reason},
        })
    return out


def parser_cases() -> list[dict[str, Any]]:
    words = " ".join(["word"] * 60)
    scores = {"a": 7, "b": 0, "c": 3}
    tweak_ok = {"new_snapshot": words, "addresses_heuristics": ["a"], "preserves_heuristics": ["b"], "reasoning": "r"}
    tweak_inputs = [
        json.dumps(tweak_ok),
        "prose before " + json.dumps(tweak_ok) + " prose after",
        "no json here",
        "{not: json}",
        json.dumps({**tweak_ok, "extra": 1}),
        json.dumps({k: v for k, v in tweak_ok.items() if k != "reasoning"}),
        json.dumps({**tweak_ok, "new_snapshot": "too short"}),
        json.dumps({**tweak_ok, "addresses_heuristics": []}),
        json.dumps({**tweak_ok, "addresses_heuristics": ["zz"]}),
        json.dumps({**tweak_ok, "addresses_heuristics": ["b"]}),
        json.dumps({**tweak_ok, "preserves_heuristics": ["a"]}),
        json.dumps({**tweak_ok, "preserves_heuristics": ["yy", "xx"]}),
        json.dumps({**tweak_ok, "reasoning": "  "}),
        "[1, 2]",
    ]
    reaudit_ok = {"scored_heuristics": {"a": 3, "b": 0, "c": 0}, "reasoning": "fine"}
    reaudit_inputs = [
        json.dumps(reaudit_ok),
        "```json\n" + json.dumps(reaudit_ok) + "\n```",
        "nothing",
        json.dumps({**reaudit_ok, "extra": True}),
        json.dumps({"reasoning": "x"}),
        json.dumps({**reaudit_ok, "scored_heuristics": {"a": 3, "b": 0}}),
        json.dumps({**reaudit_ok, "scored_heuristics": {"a": 3, "b": 0, "c": 0, "d": 5}}),
        json.dumps({**reaudit_ok, "scored_heuristics": {"a": 4, "b": 0, "c": 0}}),
        json.dumps({**reaudit_ok, "scored_heuristics": {"a": "3", "b": 0, "c": 0}}),
        json.dumps({**reaudit_ok, "scored_heuristics": {"a": True, "b": 0, "c": 0}}),
        json.dumps({**reaudit_ok, "scored_heuristics": []}),
        json.dumps({**reaudit_ok, "reasoning": ""}),
    ]
    out: dict[str, list[dict[str, Any]]] = {"tweak": [], "reaudit": []}
    for t in tweak_inputs:
        try:
            loop.parse_tweak_response(t, current_scores=scores)
            out["tweak"].append({"text": t, "ok": True, "error": None})
        except loop.TweakParseError as e:
            out["tweak"].append({"text": t, "ok": False, "error": str(e)})
    for t in reaudit_inputs:
        try:
            l8.parse_optimize_response(t, baseline_heuristics=["a", "b", "c"])
            out["reaudit"].append({"text": t, "ok": True, "error": None})
        except l8.OptimizeParseError as e:
            out["reaudit"].append({"text": t, "ok": False, "error": str(e)})
    return out


# ---------------------------------------------------------------------------
# Verify on product
# ---------------------------------------------------------------------------

SCREENSHOTS = [
    ("data/raw/duolingo_screenshots/energy_manage.png", "energy_manage — dedicated energy surface with 22h 31m regen timer, Super upsell, Recharge (500 gems), Mini charge"),
    ("data/raw/duolingo_screenshots/out_of_energy_home.png", "out_of_energy_home — blocking modal from home/between-lessons, Super with checkmark default, Recharge (450 gems), Quit lesson muted"),
    ("data/raw/duolingo_screenshots/out_of_energy_mid_lesson.png", "out_of_energy_mid_lesson — modal blocks mid-lesson ('Translate this sentence' visible above), 'TRY 1 WEEK FOR FREE' primary, 'LOSE XP' as punitive alternative"),
]


def verify_cases() -> list[dict[str, Any]]:
    vp = _load_script("verify_on_product")
    tmp = Path(tempfile.mkdtemp())
    shots = []
    for rel, label in SCREENSHOTS:
        p = tmp / Path(rel).name
        p.write_bytes(b"\x89PNG fake")
        shots.append((p, label))
    cases = []
    for b in BRANCHES:
        d = D / "verify_on_product/shared_l2opus47"
        recon = load_reconciled_verdicts(d / f"_filtered_inputs/l5_reconciled_cluster_11_{b}.jsonl")["cluster_11"]
        content = vp._build_user_content(recon, shots)
        payload = json.loads((d / f"verify_on_product_cluster11_{b}.json").read_text(encoding="utf-8"))
        cases.append({
            "name": b,
            "model": MODEL[b],
            "reconciled": _dump(recon),
            "screenshots": [{"path": rel, "label": label} for rel, label in SCREENSHOTS],
            "payload": payload,
            "expected": {
                "content_texts": [c["text"] for c in content if c["type"] == "text"],
                "md": (d / f"verify_on_product_cluster11_{b}.md").read_text(encoding="utf-8"),
            },
        })
    shutil.rmtree(tmp)
    # parser rejections
    slugs = {"a", "b"}
    good = {"cluster_id": "c", "grounded_evidence": {
        "a": {"confirmed": "confirmed", "evidence": "e", "adjusted_severity": 7, "reasoning": "r"},
        "b": {"confirmed": "refuted", "evidence": "e", "adjusted_severity": 0, "reasoning": "r"}}, "summary": "s"}
    bad = [
        json.dumps(good),
        "nothing",
        json.dumps({k: v for k, v in good.items() if k != "grounded_evidence"}),
        json.dumps({**good, "grounded_evidence": []}),
        json.dumps({**good, "grounded_evidence": {"a": good["grounded_evidence"]["a"]}}),
        json.dumps({**good, "grounded_evidence": {**good["grounded_evidence"], "z": good["grounded_evidence"]["a"]}}),
        json.dumps({**good, "grounded_evidence": {**good["grounded_evidence"], "a": {**good["grounded_evidence"]["a"], "confirmed": "maybe"}}}),
        json.dumps({**good, "grounded_evidence": {**good["grounded_evidence"], "a": {**good["grounded_evidence"]["a"], "adjusted_severity": 4}}}),
        json.dumps({k: v for k, v in good.items() if k != "summary"}),
    ]
    parse = []
    for t in bad:
        try:
            p = vp._extract_json(t)
            vp._validate_payload(p, slugs)
            parse.append({"text": t, "ok": True, "error": None})
        except Exception as e:
            parse.append({"text": t, "ok": False, "error": str(e)})
    return [{"cases": cases, "parse": parse, "slugs": sorted(slugs)}]


# ---------------------------------------------------------------------------
# Design brief
# ---------------------------------------------------------------------------


class _FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 4, 24, 18, 11, 48, tzinfo=UTC)


def brief_cases() -> list[dict[str, Any]]:
    br = _load_script("export_design_brief")
    br.datetime = _FixedDT
    cases = []

    def one(name, *, cluster_p, recon_p, prio_p, dec_p, thin_p, loop_p, verify_p, cid, b, verifier, artifact_override=None):
        cluster = br._load_cluster(cluster_p, cid)
        recon = load_reconciled_verdicts(recon_p)[cid]
        prio = load_priority_scores(prio_p)[cid]
        dec = l8.load_decisions(dec_p)[cid]
        rows = br._load_jsonl(thin_p) + br._load_jsonl(loop_p)
        its = [OptimizationIteration.model_validate(r) for r in rows]
        its = [it for it in its if it.iteration_id.startswith(f"iteration__{cid}__")]
        artifact_bodies = {}
        if artifact_override:
            ref_from, local = artifact_override
            for it in its:
                if it.design_artifact_ref == ref_from:
                    it.design_artifact_ref = str(local)
                    artifact_bodies[str(local)] = local.read_text(encoding="utf-8")
        verify = json.loads(verify_p.read_text(encoding="utf-8")) if verify_p and verify_p.exists() else None
        inputs = {"L3b cluster": cluster_p, "L5 reconciled": recon_p, "L6 priority": prio_p,
                  "L7 decision": dec_p, "L8 thin-spine iterations": thin_p, "L8 loop iterations": loop_p}
        if verify is not None:
            inputs["verify-on-product"] = verify_p
        md = br._build_brief(cluster=cluster, reconciled=recon, priority=prio, decision=dec, iterations=its,
                             verify_payload=verify, cluster_stem=cid.replace("_", ""), model_short=b,
                             loop_verifier=verifier, inputs=inputs)
        cases.append({
            "name": name,
            "payload": {
                "cluster": _dump(cluster), "reconciled": _dump(recon), "priority": _dump(prio),
                "decision": _dump(dec), "iterations": [_dump(it) for it in its],
                "verify_payload": verify, "model_short": b, "loop_verifier": verifier,
                "generated_at": "2026-04-24T18:11:48+00:00",
                "inputs": [{"label": k, "path": str(p.relative_to(REPO)), "sha256": br._sha256(p)} for k, p in inputs.items()],
                "artifact_bodies": artifact_bodies,
            },
            "expected": md,
        })

    for b in BRANCHES:
        one(f"evchain/{b}", cid="cluster_11", b=b, verifier="tchebycheff",
            cluster_p=E / f"l5_reconcile/shared_l2opus47/l3b_filtered_{b}.jsonl",
            recon_p=E / f"l5_reconcile/shared_l2opus47/l5_reconciled_{b}.jsonl",
            prio_p=D / f"l6_weight/shared_l2opus47/l6_priority_{b}.jsonl",
            dec_p=D / f"l7_decide/shared_l2opus47/l7_design_decisions_{b}.jsonl",
            thin_p=D / f"l8_optimize/shared_l2opus47/l8_optimization_iterations_{b}.jsonl",
            loop_p=D / f"l8_loop/shared_l2opus47/l8_loop_iterations_cluster11_{b}_tchebycheff.jsonl",
            verify_p=D / f"verify_on_product/shared_l2opus47/verify_on_product_cluster11_{b}.json")
    one("original-no-evidence-ids/opus47", cid="cluster_11", b="opus47", verifier="tchebycheff",
        cluster_p=D / "l5_reconcile/shared_l2opus47/l3b_filtered_opus47.jsonl",
        recon_p=D / "l5_reconcile/shared_l2opus47/l5_reconciled_opus47.jsonl",
        prio_p=D / "l6_weight/shared_l2opus47/l6_priority_opus47.jsonl",
        dec_p=D / "l7_decide/shared_l2opus47/l7_design_decisions_opus47.jsonl",
        thin_p=D / "l8_optimize/shared_l2opus47/l8_optimization_iterations_opus47.jsonl",
        loop_p=D / "l8_loop/shared_l2opus47/l8_loop_iterations_cluster11_opus47_tchebycheff.jsonl",
        verify_p=None)
    loop_p = D / "l8_loop/l8_loop_iterations_cluster02_opus47_tchebycheff.jsonl"
    ref = next(r["design_artifact_ref"] for r in _jsonl(loop_p) if r["iteration_index"] == 2)
    one("cluster02/opus47/tchebycheff", cid="cluster_02", b="opus47", verifier="tchebycheff",
        cluster_p=D / "l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl",
        recon_p=D / "l5_reconcile/l5_reconciled_cluster02_opus47.jsonl",
        prio_p=D / "l6_weight/l6_priority_cluster02_opus47.jsonl",
        dec_p=D / "l7_decide/l7_design_decisions_cluster02_opus47.jsonl",
        thin_p=D / "l8_optimize/l8_optimization_iterations_cluster02_opus47.jsonl",
        loop_p=loop_p, verify_p=None,
        artifact_override=(ref, D / "l8_loop/artifacts/opus47_tchebycheff/cluster_02_iter02.md"))
    one("cluster02/sonnet46/pareto-no-artifact", cid="cluster_02", b="sonnet46", verifier="pareto",
        cluster_p=D / "l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl",
        recon_p=D / "l5_reconcile/l5_reconciled_cluster02_sonnet46.jsonl",
        prio_p=D / "l6_weight/l6_priority_cluster02_sonnet46.jsonl",
        dec_p=D / "l7_decide/l7_design_decisions_cluster02_sonnet46.jsonl",
        thin_p=D / "l8_optimize/l8_optimization_iterations_cluster02_sonnet46.jsonl",
        loop_p=D / "l8_loop/l8_loop_iterations_cluster02_sonnet46_pareto.jsonl", verify_p=None)
    return cases


def main() -> int:
    thin = thin_cases()
    loops = loop_cases()
    fixture = {
        "thin": thin,
        "loop": loops,
        "verifiers": verifier_cases(thin, loops),
        "parsers": parser_cases(),
        "verify": verify_cases()[0],
        "brief": brief_cases(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(fixture, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    OUT.write_bytes(gzip.compress(raw, mtime=0))
    print(f"wrote {OUT.relative_to(REPO)} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
