"""One-shot smoke for the L8 multi-round optimization loop.

Companion to :mod:`auditable_design.layers.l8_optimize_loop`. Takes
one cluster's existing thin-spine L8 output (iter 0 + iter 1) and
continues the loop with iter 2+ until termination. One model for
design-tweak, one for re-audit (can differ), one verifier.

Writes, per (cluster, verifier, tweak-model, reaudit-model):

* ``l8_loop_iterations_{clusterNN}_{verifier}_{tmodel}_{rmodel}.jsonl``
  (iter 2+ only; the thin-spine input remains untouched)
* ``l8_loop_iterations_{clusterNN}_{verifier}_{tmodel}_{rmodel}.native.jsonl``
* ``l8_loop_iterations_{clusterNN}_{verifier}_{tmodel}_{rmodel}.provenance.json``
* ``artifacts/{verifier}_{tmodel}_{rmodel}/{cluster_id}_iter{NN}.md``

Text-only, same modality as L8 thin spine.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Make `src/` importable without requiring `uv run` wrapping.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.claude_client import Client  # noqa: E402
from auditable_design.evaluators.pareto import (  # noqa: E402
    DEFAULT_MAX_REGRESSION,
)
from auditable_design.evaluators.tchebycheff import (  # noqa: E402
    DEFAULT_MIN_IMPROVEMENT_PCT,
)
from auditable_design.layers.l6_weight import (  # noqa: E402
    load_reconciled_verdicts,
)
from auditable_design.layers.l7_decide import (  # noqa: E402
    load_priority_scores,
)
from auditable_design.layers.l8_optimize import (  # noqa: E402
    BASELINE_SKILL_ID,
    load_decisions,
    skill_hash as reaudit_skill_hash,
)
from auditable_design.layers.l8_optimize_loop import (  # noqa: E402
    CONVERGENCE_SEVERITY_THRESHOLD,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_STALL_LIMIT,
    TWEAK_MODEL as DEFAULT_TWEAK_MODEL,
    _read_existing_iterations,
    build_provenance,
    run_loop,
    tweak_skill_hash,
)
from auditable_design.layers.l8_optimize import (  # noqa: E402
    MODEL as DEFAULT_REAUDIT_MODEL,
)
from auditable_design.schemas import InsightCluster  # noqa: E402

DEFAULT_RECONCILED = (
    _REPO_ROOT
    / "data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl"
)
DEFAULT_PRIORITY = (
    _REPO_ROOT
    / "data/derived/l6_weight/l6_priority_cluster02_opus46.jsonl"
)
DEFAULT_DECISIONS = (
    _REPO_ROOT
    / "data/derived/l7_decide/l7_design_decisions_cluster02_opus46.jsonl"
)
DEFAULT_CLUSTERS = (
    _REPO_ROOT
    / "data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl"
)
DEFAULT_ITERATIONS_INPUT = (
    _REPO_ROOT
    / "data/derived/l8_optimize/l8_optimization_iterations_cluster02_opus47.jsonl"
)
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/l8_loop"


_MODEL_SHORT = {
    "claude-opus-4-6": "opus46",
    "claude-sonnet-4-6": "sonnet46",
    "claude-opus-4-7": "opus47",
    "claude-haiku-4-5": "haiku45",
}


def _short_model(model: str) -> str:
    for full, short in _MODEL_SHORT.items():
        if model.startswith(full):
            return short
    return model.replace("/", "_")


def _load_cluster(path: Path, cluster_id: str) -> InsightCluster:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("cluster_id") == cluster_id:
            return InsightCluster.model_validate(row)
    raise RuntimeError(f"cluster_id={cluster_id!r} not found in {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke for L8 multi-round optimization loop on one "
            "cluster. Consumes existing l8_optimize iterations "
            "(iter 0 + iter 1) and appends iter 2+ until termination."
        )
    )
    parser.add_argument(
        "--iterations-input", type=Path, default=DEFAULT_ITERATIONS_INPUT
    )
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--priority", type=Path, default=DEFAULT_PRIORITY)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--clusters", type=Path, default=DEFAULT_CLUSTERS)
    parser.add_argument(
        "--verifier", choices=["pareto", "tchebycheff"],
        default="tchebycheff",
    )
    parser.add_argument(
        "--tweak-model", default=DEFAULT_TWEAK_MODEL,
    )
    parser.add_argument(
        "--reaudit-model", default=DEFAULT_REAUDIT_MODEL,
    )
    parser.add_argument(
        "--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS,
    )
    parser.add_argument(
        "--stall-limit", type=int, default=DEFAULT_STALL_LIMIT,
    )
    parser.add_argument(
        "--severity-threshold", type=int,
        default=CONVERGENCE_SEVERITY_THRESHOLD,
    )
    parser.add_argument(
        "--max-regression", type=int, default=DEFAULT_MAX_REGRESSION,
    )
    parser.add_argument(
        "--min-improvement-pct", type=float,
        default=DEFAULT_MIN_IMPROVEMENT_PCT,
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    # Load inputs.
    reconciled_map = load_reconciled_verdicts(args.reconciled)
    if len(reconciled_map) != 1:
        raise RuntimeError(
            f"expected exactly one reconciled verdict in {args.reconciled}, "
            f"got {len(reconciled_map)}"
        )
    cluster_id, reconciled = next(iter(reconciled_map.items()))

    priority = load_priority_scores(args.priority)[cluster_id]
    decision = load_decisions(args.decisions)[cluster_id]
    cluster = _load_cluster(args.clusters, cluster_id)
    existing = _read_existing_iterations(args.iterations_input, cluster_id)

    # Suffix for output filenames — analogous to matched eval
    # convention across layers (cluster_stem drops underscores, model
    # short first then verifier). When tweak and reaudit models match
    # (the typical matched grid), the model appears once.
    tshort = _short_model(args.tweak_model)
    rshort = _short_model(args.reaudit_model)
    cluster_stem = cluster_id.replace("_", "")
    model_part = tshort if tshort == rshort else f"{tshort}-r{rshort}"
    suffix = f"{cluster_stem}_{model_part}_{args.verifier}"
    args.out_dir.mkdir(parents=True, exist_ok=True)

    iters_out = args.out_dir / f"l8_loop_iterations_{suffix}.jsonl"
    native_out = args.out_dir / f"l8_loop_iterations_{suffix}.native.jsonl"
    prov_out = args.out_dir / f"l8_loop_iterations_{suffix}.provenance.json"
    artifacts_dir = (
        args.out_dir / "artifacts" / f"{model_part}_{args.verifier}"
    )

    # Run the loop.
    from datetime import UTC, datetime
    run_id = f"l8-loop-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    client = Client(
        mode="live",
        run_id=run_id,
        usd_ceiling=5.0,
        concurrency=2,
    )
    t_hash = tweak_skill_hash()
    r_hash = reaudit_skill_hash()

    print(
        f"[smoke] cluster={cluster_id} verifier={args.verifier} "
        f"tweak={args.tweak_model} reaudit={args.reaudit_model}",
        flush=True,
    )
    print(
        f"[smoke] starting iter_index={existing[-1].iteration_index + 1} "
        f"max_iterations={args.max_iterations}",
        flush=True,
    )

    outcome = asyncio.run(
        run_loop(
            cluster=cluster,
            reconciled=reconciled,
            decision=decision,
            priority=priority,
            existing_iterations=existing,
            client=client,
            verifier=args.verifier,
            max_iterations=args.max_iterations,
            stall_limit=args.stall_limit,
            severity_threshold=args.severity_threshold,
            max_regression=args.max_regression,
            min_improvement_pct=args.min_improvement_pct,
            tweak_model=args.tweak_model,
            reaudit_model=args.reaudit_model,
            tweak_skill_hash_value=t_hash,
            reaudit_skill_hash_value=r_hash,
            run_id=run_id,
            artifacts_dir=artifacts_dir,
        )
    )

    # Write outputs.
    iters_out.write_text(
        "\n".join(
            json.dumps(it.model_dump(mode="json"), ensure_ascii=False)
            for it in outcome.new_iterations
        )
        + ("\n" if outcome.new_iterations else ""),
        encoding="utf-8",
    )
    native_out.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in outcome.native_payloads
        )
        + ("\n" if outcome.native_payloads else ""),
        encoding="utf-8",
    )
    provenance = build_provenance(
        outcome,
        verifier=args.verifier,
        tweak_model=args.tweak_model,
        reaudit_model=args.reaudit_model,
        tweak_skill_id="design-tweak",
        reaudit_skill_id="design-optimize",
        tweak_skill_hash_value=t_hash,
        reaudit_skill_hash_value=r_hash,
        run_id=run_id,
        max_iterations=args.max_iterations,
        stall_limit=args.stall_limit,
        severity_threshold=args.severity_threshold,
        max_regression=args.max_regression,
        min_improvement_pct=args.min_improvement_pct,
    )
    prov_out.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Summary.
    accepted = sum(1 for it in outcome.new_iterations if it.accepted)
    rejected = len(outcome.new_iterations) - accepted
    final_scores: dict[str, int] = {}
    all_iters = list(existing) + list(outcome.new_iterations)
    # Find final parent's scores.
    for it in all_iters:
        if it.iteration_id == outcome.final_parent_id:
            final_scores = dict(it.scores.get(BASELINE_SKILL_ID, {}))
            break

    print(
        f"[smoke] termination={outcome.termination_reason} "
        f"new_iters={len(outcome.new_iterations)} "
        f"accepted={accepted} rejected={rejected}",
        flush=True,
    )
    print(
        f"[smoke] final parent={outcome.final_parent_id} "
        f"severity_sum={sum(final_scores.values())}",
        flush=True,
    )
    print(f"[smoke] iterations → {iters_out}", flush=True)
    print(f"[smoke] native      → {native_out}", flush=True)
    print(f"[smoke] provenance  → {prov_out}", flush=True)
    print(f"[smoke] artifacts   → {artifacts_dir}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
