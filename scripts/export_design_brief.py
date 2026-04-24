"""Export design brief — the pipeline's shipping artifact for designers.

Aggregates every per-layer output into a single markdown document
the designer opens and starts work from. No Claude calls; pure
aggregation of files the pipeline already produced.

Sections emitted:

1. Executive summary
2. User pain signal (representative quotes + informing review IDs)
3. Measured pain spaces (L5 reconciled × verify-on-product grounding)
4. Priority reasoning (L6 dimensions + weighted total)
5. Validated direction (L7 decision + L8-loop final accepted)
6. Out-of-baseline observations (verify-on-product summary)
7. Audit trail (rejected loop iterations)
8. Signal quality indicators (severity reduction, grounding ratio,
   loop convergence — transparent components, not a rollup)
9. Handoff notes (what the designer owns next + honest limits)

Output: one markdown + one provenance JSON per (cluster, model).

Usage:
    uv run python scripts/export_design_brief.py \\
        [--cluster-id cluster_02] \\
        [--model claude-opus-4-7] \\
        [--loop-verifier tchebycheff]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.layers.l6_weight import (  # noqa: E402
    load_reconciled_verdicts,
)
from auditable_design.layers.l7_decide import (  # noqa: E402
    load_priority_scores,
)
from auditable_design.layers.l8_optimize import (  # noqa: E402
    load_decisions,
)
from auditable_design.schemas import (  # noqa: E402
    DesignDecision,
    InsightCluster,
    OptimizationIteration,
    PriorityScore,
    ReconciledVerdict,
)


DEFAULT_CLUSTER_ID = "cluster_02"
DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_LOOP_VERIFIER = "tchebycheff"
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/design_brief"


_MODEL_SHORT = {
    "claude-opus-4-6": "opus46",
    "claude-sonnet-4-6": "sonnet46",
    "claude-opus-4-7": "opus47",
}


def _short_model(m: str) -> str:
    return _MODEL_SHORT.get(m, m.replace("/", "_"))


def _sha256(path: Path) -> str:
    if not path.exists():
        return "missing"
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _load_cluster(path: Path, cluster_id: str) -> InsightCluster:
    for row in _load_jsonl(path):
        if row.get("cluster_id") == cluster_id:
            return InsightCluster.model_validate(row)
    raise RuntimeError(f"cluster_id={cluster_id!r} not found in {path}")


def _extract_section(md: Path, header: str) -> str | None:
    if not md.exists():
        return None
    body = md.read_text(encoding="utf-8")
    marker = f"## {header}\n"
    idx = body.find(marker)
    if idx < 0:
        return None
    after = body[idx + len(marker):]
    end = after.find("\n## ")
    return (after if end < 0 else after[:end]).strip()


# ---------------------------------------------------------------------------
# Brief assembly
# ---------------------------------------------------------------------------


def _build_brief(
    *,
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    priority: PriorityScore,
    decision: DesignDecision,
    iterations: list[OptimizationIteration],
    verify_payload: dict[str, Any] | None,
    cluster_stem: str,
    model_short: str,
    loop_verifier: str,
    inputs: dict[str, Path],
) -> str:
    """Render the full brief as a single markdown string."""
    baseline_severities = {
        v.heuristic: int(v.severity) for v in reconciled.ranked_violations
    }
    baseline_sum = sum(baseline_severities.values())

    # Find the loop's last accepted iteration (final parent).
    iters_sorted = sorted(iterations, key=lambda it: it.iteration_index)
    final_accepted = None
    for it in reversed(iters_sorted):
        if it.accepted:
            final_accepted = it
            break
    if final_accepted is None:
        raise RuntimeError("no accepted iteration found")
    final_scores = final_accepted.scores.get("reconciled", {})
    final_sum = sum(final_scores.values()) if final_scores else baseline_sum
    severity_reduction_pct = (
        100.0 * (baseline_sum - final_sum) / max(1, baseline_sum)
    )

    # Verify-on-product: aggregate counts + per-heuristic lookup.
    ge = (verify_payload or {}).get("grounded_evidence", {}) or {}
    verdict_counts = {"confirmed": 0, "partial": 0, "refuted": 0}
    for entry in ge.values():
        v = entry.get("confirmed")
        if v in verdict_counts:
            verdict_counts[v] += 1
    verified_total = sum(verdict_counts.values())
    grounded_ratio = (
        (verdict_counts["confirmed"] + 0.5 * verdict_counts["partial"])
        / max(1, verified_total)
    )

    # Loop convergence: check iteration termination signal.
    loop_converged = any(
        it.iteration_index >= 2 and it.accepted for it in iters_sorted
    )
    rejected_iters = [it for it in iters_sorted if not it.accepted]

    lines: list[str] = []

    # -- Header ------------------------------------------------------
    lines += [
        f"# Design brief — {cluster.cluster_id}",
        "",
        f"**Label:** {cluster.label}",
        "",
        f"**Pipeline model:** `{model_short}` · "
        f"**loop verifier:** `{loop_verifier}` · "
        f"**cluster members:** {len(cluster.member_review_ids)} reviews · "
        f"**baseline heuristics:** {len(baseline_severities)}",
        "",
        f"**Generated:** {datetime.now(UTC).isoformat(timespec='seconds')}",
        "",
        "---",
        "",
    ]

    # -- Executive summary -------------------------------------------
    exec_summary = (
        f"Users of **{cluster.label.lower()}** report friction "
        f"captured as {len(baseline_severities)} named heuristic "
        f"violations across six design lenses "
        f"(Norman / WCAG / Kahneman / Osterwalder / Cooper / "
        f"Garrett), with reconciled severity sum **{baseline_sum}**. "
        f"The pipeline proposes a direction (L7) that, refined "
        f"through iterative self-verification (L8 loop), drives the "
        f"measured severity to **{final_sum}** "
        f"(**{severity_reduction_pct:.0f}% reduction** on the same "
        f"heuristic list)."
    )
    if verify_payload is not None:
        exec_summary += (
            f" Real-product verification against product screenshots "
            f"confirms {verdict_counts['confirmed']} of "
            f"{verified_total} heuristics, softens "
            f"{verdict_counts['partial']} to partial, and refutes "
            f"{verdict_counts['refuted']} as a review-inferred "
            f"false-positive — a correction the pipeline could not "
            f"make from review text alone."
        )
    exec_summary += (
        " Self-verified (ensemble-internal); external validation "
        "remains the design team's responsibility."
    )
    lines += ["## Executive summary", "", exec_summary, "", "---", ""]

    # -- User pain signal --------------------------------------------
    lines += [
        "## User pain signal",
        "",
        f"Cluster aggregates **{len(cluster.member_review_ids)} "
        f"reviews** with {len(cluster.representative_quotes)} "
        f"representative quotes captured by L3b.",
        "",
    ]
    if cluster.ui_context:
        lines += [
            f"**UI context (as identified by L3b):** "
            f"{cluster.ui_context}",
            "",
        ]
    lines += ["**Representative user quotes:**", ""]
    for q in cluster.representative_quotes:
        lines.append(f'> {q}')
        lines.append("")
    lines += ["**Informing review IDs:**", ""]
    for rid in cluster.member_review_ids:
        lines.append(f"- `{rid}`")
    lines += ["", "---", ""]

    # -- Measured pain spaces ----------------------------------------
    lines += [
        "## Measured pain spaces",
        "",
        f"L5 reconciliation across six lenses produced the ranked "
        f"list below. Severities use the ADR-008 anchored scale "
        f"(`{{0, 3, 5, 7, 9}}`) designed for cross-run "
        f"reproducibility rather than calibrated intensity.",
        "",
    ]
    if verify_payload is not None:
        lines += [
            "Grounded evidence from product screenshots is attached "
            "per heuristic (confirmed / partial / refuted).",
            "",
            "| Heuristic | L5 sev | Grounded verdict | Adjusted sev | Evidence |",
            "|---|---|---|---|---|",
        ]
        for v in reconciled.ranked_violations:
            entry = ge.get(v.heuristic, {}) or {}
            verdict = entry.get("confirmed", "—")
            adj = entry.get("adjusted_severity", v.severity)
            ev = str(entry.get("evidence", "")).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{v.heuristic}` | {v.severity} | {verdict} | "
                f"{adj} | {ev or '—'} |"
            )
    else:
        lines += [
            "| Heuristic | L5 sev | Violation |",
            "|---|---|---|",
        ]
        for v in reconciled.ranked_violations:
            viol = v.violation.replace("|", "\\|").replace("\n", " ")
            viol = viol[:180] + ("…" if len(viol) > 180 else "")
            lines.append(f"| `{v.heuristic}` | {v.severity} | {viol} |")

    lines += ["", f"**Baseline severity sum:** {baseline_sum}", ""]
    if reconciled.tensions:
        lines += [
            "**Skill tensions surfaced by reconciliation:**",
            "",
        ]
        for t in reconciled.tensions:
            lines.append(
                f"- `{t.skill_a}` ↔ `{t.skill_b}` on *{t.axis}* — "
                f"resolved: {t.resolution}"
            )
        lines.append("")
    lines += ["---", ""]

    # -- Priority reasoning ------------------------------------------
    lines += [
        "## Priority reasoning",
        "",
        f"L6 weights the cluster on five dimensions (anchored 0–10).",
        "",
        "| Dimension | Score | Meta-weight |",
        "|---|---|---|",
    ]
    for dim_name, score in priority.dimensions.items():
        w = priority.meta_weights.get(dim_name, 0.0)
        lines.append(f"| {dim_name} | {score} | {w:.2f} |")
    lines += [
        "",
        f"**Weighted total:** {priority.weighted_total:.1f} · "
        f"validation passes: {priority.validation_passes} · "
        f"validation delta: {priority.validation_delta:.2f}",
        "",
        "---",
        "",
    ]

    # -- Validated direction -----------------------------------------
    lines += [
        "## Validated direction",
        "",
        f"L7 proposed a design decision for the highest-priority "
        f"pain space. L8 loop then refined the decision through "
        f"iterative self-verification: the loop's final accepted "
        f"iteration (iter "
        f"{final_accepted.iteration_index:02d}) drops the "
        f"measured severity from **{baseline_sum}** to "
        f"**{final_sum}**.",
        "",
        "### Before (current product state, per L7)",
        "",
        f"> {decision.before_snapshot}",
        "",
        "### After (validated direction)",
        "",
    ]
    after_snapshot = _recover_snapshot(
        final_accepted, decision
    )
    lines += [f"> {after_snapshot}", ""]

    lines += [
        "### Per-heuristic delta (L5 baseline → loop final)",
        "",
        "| Heuristic | Baseline | Final | Δ |",
        "|---|---|---|---|",
    ]
    for v in reconciled.ranked_violations:
        b = baseline_severities[v.heuristic]
        f = final_scores.get(v.heuristic, b)
        delta = f - b
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"| `{v.heuristic}` | {b} | {f} | {sign}{delta} |"
        )
    lines += [
        "",
        f"**Severity reduction:** "
        f"{baseline_sum - final_sum} units ({severity_reduction_pct:.0f}%).",
        "",
        "**Resolves heuristics (per L7 decision):**",
        "",
    ]
    for h in decision.resolves_heuristics:
        lines.append(f"- `{h}`")
    lines += ["", "---", ""]

    # -- Out-of-baseline observations --------------------------------
    if verify_payload is not None and verify_payload.get("summary"):
        lines += [
            "## Out-of-baseline observations",
            "",
            "Real-product verification can surface defects that the "
            "review-inferred heuristic list did not name. These are "
            "candidates for inclusion in the next clustering cycle.",
            "",
            "> " + verify_payload["summary"].replace("\n", "\n> "),
            "",
            "---",
            "",
        ]

    # -- Audit trail -------------------------------------------------
    lines += [
        "## Audit trail — iteration log",
        "",
        "Every iteration the loop produced is recorded below, "
        "including rejected attempts. This is the transparency "
        "guarantee: the designer can see not only the final "
        "direction but also what the pipeline tried and why each "
        "attempt was accepted or rejected.",
        "",
        "| Iter | Status | Severity sum | Parent | Notes |",
        "|---|---|---|---|---|",
    ]
    for it in iters_sorted:
        sev_sum = sum(it.scores.get("reconciled", {}).values())
        status = "✓ accepted" if it.accepted else "✗ rejected"
        parent = it.parent_iteration_id or "—"
        note = (
            (it.reasoning or it.regression_reason or "")
            .replace("|", "\\|")
            .replace("\n", " ")
        )
        note = note[:120] + ("…" if len(note) > 120 else "")
        lines.append(
            f"| {it.iteration_index:02d} | {status} | {sev_sum} | "
            f"`{parent}` | {note} |"
        )
    if rejected_iters:
        lines += [
            "",
            "**Rejected-iteration reasons (verifier verdict):**",
            "",
        ]
        for it in rejected_iters:
            reason = (it.regression_reason or "").replace("\n", " ")
            lines.append(
                f"- iter {it.iteration_index:02d} → "
                f"{reason or '(no reason recorded)'}"
            )
    lines += ["", "---", ""]

    # -- Signal quality ---------------------------------------------
    lines += [
        "## Signal quality indicators",
        "",
        "These are transparent components, not a rollup score. The "
        "designer weights them based on context.",
        "",
        f"- **Severity reduction**: "
        f"{severity_reduction_pct:.0f}% "
        f"({baseline_sum} → {final_sum})",
        f"- **Loop convergence**: "
        f"{'converged' if loop_converged else 'stalled'} · "
        f"{len(iters_sorted)} total iterations · "
        f"{len(rejected_iters)} rejected",
    ]
    if verify_payload is not None:
        lines += [
            f"- **Grounded-evidence ratio**: "
            f"{verdict_counts['confirmed']} confirmed / "
            f"{verdict_counts['partial']} partial / "
            f"{verdict_counts['refuted']} refuted "
            f"(weighted score: {grounded_ratio * 100:.0f}%)",
        ]
    else:
        lines.append(
            "- **Real-product grounding**: not run for this cluster "
            "(pipeline operated on L7 prose snapshots only)"
        )
    lines += ["", "---", ""]

    # -- Handoff -----------------------------------------------------
    lines += [
        "## Handoff — what the designer owns next",
        "",
        "This brief is **direction, not specification**. Translate "
        "into wireframes, components, and flows in the tooling your "
        "team uses (Claude design, Figma, Linear). The agent does "
        "not commit, does not merge, does not ship — the work "
        "starts here and is owned by human design/engineering.",
        "",
        "**What the brief guarantees:**",
        "",
        "- Every finding has a typed chain back to informing user "
        "reviews and (where run) real-product screenshots.",
        "- The validated direction self-verifies (ensemble-internal) "
        "against the same heuristic baseline — i.e. the re-audit "
        "confirms the direction reduces measured pain.",
        "- Every rejected loop attempt is preserved above for audit.",
        "",
        "**What the brief does NOT guarantee:**",
        "",
        "- Real-user validation (A/B testing, longitudinal study).",
        "- Implementation feasibility in the team's tech stack.",
        "- Aesthetic / brand fit with the product's visual system.",
        "- That the direction is the *only* direction that would "
        "work — it is one validated direction, not the space of "
        "valid directions.",
        "",
        "Designers should feel free to reject the direction on any "
        "of the above axes. The pipeline's job is to ensure "
        "rejection happens on the basis of visible evidence, not "
        "blind trust in either side.",
        "",
        "---",
        "",
    ]

    # -- Provenance footer ------------------------------------------
    lines += [
        "## Provenance",
        "",
        "| Layer | Input file | sha256 |",
        "|---|---|---|",
    ]
    for label, path in inputs.items():
        lines.append(
            f"| {label} | `{path.relative_to(_REPO_ROOT)}` | "
            f"`{_sha256(path)[:16]}` |"
        )
    lines += ["", ""]

    return "\n".join(lines)


def _recover_snapshot(
    iteration: OptimizationIteration, decision: DesignDecision
) -> str:
    """Pull the snapshot text the loop iteration refers to."""
    if iteration.iteration_index == 0:
        return decision.before_snapshot
    if iteration.iteration_index == 1:
        return decision.after_snapshot
    artifact_path = Path(iteration.design_artifact_ref)
    if not artifact_path.exists():
        return decision.after_snapshot  # fallback
    body = artifact_path.read_text(encoding="utf-8")
    marker = "## new_snapshot\n"
    idx = body.find(marker)
    if idx < 0:
        return decision.after_snapshot
    after = body[idx + len(marker):]
    end = after.find("\n## ")
    return (after if end < 0 else after[:end]).strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export the design brief for one cluster — the pipeline's "
            "shipping artifact for designers."
        )
    )
    parser.add_argument("--cluster-id", default=DEFAULT_CLUSTER_ID)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--loop-verifier",
        default=DEFAULT_LOOP_VERIFIER,
        choices=["pareto", "tchebycheff"],
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    # Path overrides — default to the original matched-grid layout
    # (cluster_02 hero). Pass explicit paths to aggregate a brief
    # from the shared_l2opus47/ layout or any other subdirectory.
    parser.add_argument("--clusters-path", type=Path, default=None)
    parser.add_argument("--reconciled-path", type=Path, default=None)
    parser.add_argument("--priority-path", type=Path, default=None)
    parser.add_argument("--decisions-path", type=Path, default=None)
    parser.add_argument("--iters-thin-path", type=Path, default=None)
    parser.add_argument("--iters-loop-path", type=Path, default=None)
    parser.add_argument("--verify-path", type=Path, default=None)
    args = parser.parse_args(argv)

    cluster_id = args.cluster_id
    model_short = _short_model(args.model)
    cluster_stem = cluster_id.replace("_", "")

    # Locate inputs. These paths match the matched-grid naming used
    # throughout the repo. Each is overridable via the per-path flags
    # above so a shared-input run can point at shared_l2opus47/ files.
    clusters_path = args.clusters_path or (
        _REPO_ROOT
        / "data/derived/l4_audit/audit_interaction_design"
        / "audit_interaction_design_input.jsonl"
    )
    reconciled_path = args.reconciled_path or (
        _REPO_ROOT
        / "data/derived/l5_reconcile"
        / f"l5_reconciled_{cluster_stem}_{model_short}.jsonl"
    )
    priority_path = args.priority_path or (
        _REPO_ROOT
        / "data/derived/l6_weight"
        / f"l6_priority_{cluster_stem}_{model_short}.jsonl"
    )
    decisions_path = args.decisions_path or (
        _REPO_ROOT
        / "data/derived/l7_decide"
        / f"l7_design_decisions_{cluster_stem}_{model_short}.jsonl"
    )
    iters_thin_path = args.iters_thin_path or (
        _REPO_ROOT
        / "data/derived/l8_optimize"
        / f"l8_optimization_iterations_{cluster_stem}_{model_short}.jsonl"
    )
    iters_loop_path = args.iters_loop_path or (
        _REPO_ROOT
        / "data/derived/l8_loop"
        / f"l8_loop_iterations_{cluster_stem}_{model_short}_{args.loop_verifier}.jsonl"
    )
    verify_path = args.verify_path or (
        _REPO_ROOT
        / "data/derived/verify_on_product"
        / f"verify_on_product_{cluster_stem}_{model_short}.json"
    )

    # Load.
    cluster = _load_cluster(clusters_path, cluster_id)
    reconciled_map = load_reconciled_verdicts(reconciled_path)
    if cluster_id not in reconciled_map:
        raise RuntimeError(f"no L5 verdict for {cluster_id} in {reconciled_path}")
    reconciled = reconciled_map[cluster_id]

    priority_map = load_priority_scores(priority_path)
    if cluster_id not in priority_map:
        raise RuntimeError(f"no L6 priority for {cluster_id} in {priority_path}")
    priority = priority_map[cluster_id]

    decision = load_decisions(decisions_path)[cluster_id]

    iterations_rows = _load_jsonl(iters_thin_path) + _load_jsonl(iters_loop_path)
    iterations_all = [OptimizationIteration.model_validate(r) for r in iterations_rows]
    # Filter to just this cluster — the shared_l2opus47 layout packs
    # multiple clusters into one iterations file, while the older
    # per-cluster layout had exactly one. Filtering here covers both.
    _prefix = f"iteration__{cluster_id}__"
    iterations = [it for it in iterations_all if it.iteration_id.startswith(_prefix)]
    if not iterations:
        raise RuntimeError(
            f"no iterations for {cluster_id} at {iters_thin_path} / {iters_loop_path} "
            f"(loaded {len(iterations_all)} total rows)"
        )

    verify_payload: dict[str, Any] | None = None
    if verify_path.exists():
        verify_payload = json.loads(verify_path.read_text(encoding="utf-8"))

    inputs = {
        "L3b cluster": clusters_path,
        "L5 reconciled": reconciled_path,
        "L6 priority": priority_path,
        "L7 decision": decisions_path,
        "L8 thin-spine iterations": iters_thin_path,
        "L8 loop iterations": iters_loop_path,
    }
    if verify_payload is not None:
        inputs["verify-on-product"] = verify_path

    brief = _build_brief(
        cluster=cluster,
        reconciled=reconciled,
        priority=priority,
        decision=decision,
        iterations=iterations,
        verify_payload=verify_payload,
        cluster_stem=cluster_stem,
        model_short=model_short,
        loop_verifier=args.loop_verifier,
        inputs=inputs,
    )

    # Write.
    args.out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{cluster_stem}_{model_short}"
    brief_path = args.out_dir / f"design_brief_{suffix}.md"
    prov_path = args.out_dir / f"design_brief_{suffix}.provenance.json"

    brief_path.write_text(brief, encoding="utf-8")

    baseline_sum = sum(int(v.severity) for v in reconciled.ranked_violations)
    iters_sorted = sorted(iterations, key=lambda it: it.iteration_index)
    final_accepted = next(
        (it for it in reversed(iters_sorted) if it.accepted), None
    )
    final_sum = (
        sum((final_accepted.scores.get("reconciled", {}) or {}).values())
        if final_accepted is not None
        else baseline_sum
    )

    provenance = {
        "schema_version": 1,
        "layer": "design_brief",
        "cluster_id": cluster_id,
        "model": args.model,
        "loop_verifier": args.loop_verifier,
        "baseline_severity_sum": baseline_sum,
        "final_severity_sum": final_sum,
        "iteration_count": len(iters_sorted),
        "rejected_iteration_count": sum(
            1 for it in iters_sorted if not it.accepted
        ),
        "verify_on_product_included": verify_payload is not None,
        "inputs": {
            label: {
                "path": str(path.relative_to(_REPO_ROOT)),
                "sha256": _sha256(path),
            }
            for label, path in inputs.items()
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }
    prov_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"[brief] cluster={cluster_id} model={args.model}", flush=True)
    print(
        f"[brief] baseline_sum={baseline_sum} final_sum={final_sum} "
        f"iters={len(iters_sorted)} rejected={provenance['rejected_iteration_count']} "
        f"verify_included={provenance['verify_on_product_included']}",
        flush=True,
    )
    print(f"[brief] → {brief_path}", flush=True)
    print(f"[brief] → {prov_path}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
