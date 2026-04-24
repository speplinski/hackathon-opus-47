"""Layer 8 — design-optimization iteration loop.

L7 generated a DesignDecision per cluster (before_snapshot →
after_snapshot). L8 **re-audits the proposed after_snapshot** against
the baseline heuristic list and decides, via Pareto + weighted-sum,
whether the iteration is an accepted improvement.

Thin-spine scope
----------------
Two iterations per cluster:

* **Iteration 0 (baseline).** Scores imported from L5
  ReconciledVerdict — each ranked violation becomes a heuristic score
  in the synthetic ``"reconciled"`` skill bucket. No Claude call.
* **Iteration 1 (proposed).** Claude re-audits the
  ``decision.after_snapshot`` against the same heuristic list, emits
  per-heuristic severity on ADR-008's anchored scale, and the Pareto
  evaluator decides accept/reject.

Multi-step optimization loops (iteration 2+ proposing further tweaks)
are out of scope for the pilot; the module's ``scores`` shape and
iteration-linkage discipline are ready for that extension when
implementation chooses to add a generative "tweak the design" step.

Scores nested-dict shape (``{skill_id: {heuristic: severity}}``)
-----------------------------------------------------------------
The schema requires :class:`OptimizationIteration.scores` to be a
dict keyed by skill_id, with per-heuristic severities nested. L5's
reconciled verdict has already collapsed cross-skill corroborations —
a single ``modal_excise__corroborated`` heuristic may carry evidence
from five L4 skills. Rather than re-decompose the reconciled view
back into six per-skill buckets (brittle reasoning-string parsing),
the module uses a single synthetic skill_id ``"reconciled"`` and
nests all heuristics under it. Schema is satisfied; semantics are
honest — the baseline scores are a reconciled cross-skill view, not
per-skill views.

Input / output
--------------
* Reads L5 reconciled + L6 priority + L7 decisions + L3b labeled
  clusters; joins by cluster_id.
* Writes :data:`DEFAULT_ITERATIONS` — two :class:`OptimizationIteration`
  records per cluster (baseline + proposed).
* Writes :data:`DEFAULT_ARTIFACTS_DIR` — two ``.md`` artefacts per
  cluster (``{cluster_id}_iter0.md`` with before_snapshot,
  ``{cluster_id}_iter1.md`` with after_snapshot) referenced via
  ``design_artifact_ref``.
* Writes :data:`DEFAULT_NATIVE` — raw skill payloads keyed by
  cluster_id for iteration 1.
* Writes a ``.provenance.json`` sidecar with accept/reject counts,
  regression distribution, mean severity delta.

Fallback discipline
-------------------
Parse failure or missing-key failure on iteration 1 → fallback
record with baseline-copied severities (a no-op iteration that the
Pareto evaluator correctly rejects as no-improvement). Transport
errors propagate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from auditable_design.claude_client import Client
from auditable_design.evaluators.pareto import (
    DEFAULT_MAX_REGRESSION,
    ParetoVerdict,
    verdict as pareto_verdict,
)
from auditable_design.layers.l4_audit import (
    AuditParseError,
    _atomic_write_bytes,
    _configure_logging,
    _default_run_id,
    _resolve_repo_root,
    load_clusters,
)
from auditable_design.layers.l6_weight import load_reconciled_verdicts
from auditable_design.layers.l7_decide import load_priority_scores
from auditable_design.schemas import (
    SCHEMA_VERSION,
    DesignDecision,
    InsightCluster,
    OptimizationIteration,
    PriorityScore,
    ReconciledVerdict,
)
from auditable_design.storage import hash_file, read_jsonl, write_jsonl_atomic

__all__ = [
    "BASELINE_SKILL_ID",
    "DEFAULT_ARTIFACTS_DIR",
    "DEFAULT_CLUSTERS",
    "DEFAULT_DECISIONS",
    "DEFAULT_ITERATIONS",
    "DEFAULT_MAX_REGRESSION",
    "DEFAULT_NATIVE",
    "DEFAULT_PRIORITY",
    "DEFAULT_RECONCILED",
    "LAYER_NAME",
    "MAX_TOKENS",
    "MODEL",
    "SKILL_ID",
    "SYSTEM_PROMPT",
    "TEMPERATURE",
    "VALID_ANCHORED_SEVERITIES",
    "OptimizeOutcome",
    "OptimizeParseError",
    "build_baseline_iteration",
    "build_provenance",
    "build_user_message",
    "load_decisions",
    "main",
    "optimize_batch",
    "optimize_cluster",
    "parse_optimize_response",
    "skill_hash",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_ID: str = "design-optimize"
LAYER_NAME: str = "l8_optimize"

# Opus 4.7 per ADR-009: re-audit is reasoning-heavy.
MODEL: str = "claude-opus-4-7"
TEMPERATURE: float = 0.0

# Output: scored_heuristics dict (≤ 15 keys × ~20 tokens each) +
# reasoning (≤ 300 tokens) ≈ 600 tokens upper bound. 4096 leaves
# 5× headroom.
MAX_TOKENS: int = 4096

# ADR-008 anchored severity scale.
VALID_ANCHORED_SEVERITIES: frozenset[int] = frozenset({0, 3, 5, 7, 9})

# Synthetic skill_id used for the baseline iteration's nested scores
# dict. See module docstring for rationale.
BASELINE_SKILL_ID: str = "reconciled"

# Default paths.
DEFAULT_RECONCILED = Path("data/derived/l5_reconciled_verdicts.jsonl")
DEFAULT_PRIORITY = Path("data/derived/l6_priority_scores.jsonl")
DEFAULT_DECISIONS = Path("data/derived/l7_design_decisions.jsonl")
DEFAULT_CLUSTERS = Path("data/derived/l3b_labeled_clusters.jsonl")
DEFAULT_ITERATIONS = Path("data/derived/l8_optimization_iterations.jsonl")
DEFAULT_NATIVE = Path("data/derived/l8_optimization_iterations.native.jsonl")
DEFAULT_ARTIFACTS_DIR = Path("data/artifacts/iterations")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OptimizeParseError(AuditParseError):
    """Parse / validation failure specific to the L8 re-audit payload."""


# ---------------------------------------------------------------------------
# System prompt — loaded from SKILL.md
# ---------------------------------------------------------------------------


def _load_skill_body() -> str:
    repo_root = _resolve_repo_root()
    path = repo_root / "skills" / SKILL_ID / "SKILL.md"
    if not path.exists():
        raise RuntimeError(
            f"{LAYER_NAME}: SKILL.md not found at {path}; layer cannot initialise"
        )
    content = path.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            content = content[end + len("\n---\n") :]
    return content.strip()


SYSTEM_PROMPT: str = _load_skill_body()


def skill_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def load_decisions(path: Path) -> dict[str, DesignDecision]:
    """Load L7 decisions as a dict keyed by the cluster_id extracted
    from ``decision_id`` (which has the form ``decision__{cluster_id}__{idx}``)."""
    rows = read_jsonl(path)
    result: dict[str, DesignDecision] = {}
    for i, row in enumerate(rows):
        try:
            d = DesignDecision.model_validate(row)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"{LAYER_NAME}: row {i} of {path} is not a valid "
                f"DesignDecision: {e}"
            ) from e
        # decision_id = "decision__{cluster_id}__{idx}"
        parts = d.decision_id.split("__")
        if len(parts) < 3 or parts[0] != "decision":
            _log.warning(
                "unexpected decision_id shape %r — skipping",
                d.decision_id,
            )
            continue
        cluster_id = "__".join(parts[1:-1])
        if cluster_id in result:
            _log.warning(
                "duplicate decision for cluster=%s — later row wins",
                cluster_id,
            )
        result[cluster_id] = d
    return result


# ---------------------------------------------------------------------------
# Baseline iteration construction
# ---------------------------------------------------------------------------


def _baseline_scores(reconciled: ReconciledVerdict) -> dict[str, dict[str, int]]:
    """Convert a ReconciledVerdict's ranked_violations into the
    OptimizationIteration.scores shape (``{skill_id: {h: sev}}``).

    All heuristics nest under the synthetic :data:`BASELINE_SKILL_ID`
    because the reconciled view has already collapsed cross-skill
    corroborations. Severities are taken from
    ``HeuristicViolation.severity`` (ADR-008 anchored 0–10).
    """
    per_heuristic: dict[str, int] = {}
    for v in reconciled.ranked_violations:
        # If the same heuristic appears twice (rare), keep the max
        # severity to be conservative.
        prev = per_heuristic.get(v.heuristic, 0)
        per_heuristic[v.heuristic] = max(prev, int(v.severity))
    return {BASELINE_SKILL_ID: per_heuristic}


def _baseline_iteration_id(cluster_id: str) -> str:
    return f"iteration__{cluster_id}__00"


def _proposed_iteration_id(cluster_id: str) -> str:
    return f"iteration__{cluster_id}__01"


def build_baseline_iteration(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    decision: DesignDecision,
    artifacts_dir: Path,
    *,
    run_id: str,
) -> OptimizationIteration:
    """Construct iteration 0 (baseline) from the reconciled verdict.

    Writes the baseline design artifact (``{cluster_id}_iter00.md``)
    containing ``decision.before_snapshot`` as the textual surface
    description. The returned OptimizationIteration references that
    file via ``design_artifact_ref``.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts_dir / f"{cluster.cluster_id}_iter00.md"
    artifact_body = (
        f"# {cluster.cluster_id} — iteration 0 (baseline)\n\n"
        f"## Cluster label\n{cluster.label}\n\n"
        f"## before_snapshot (from L7 decision {decision.decision_id})\n"
        f"{decision.before_snapshot}\n\n"
        f"## Baseline heuristic severities (from L5 reconciled verdict)\n"
    )
    for h, sev in _baseline_scores(reconciled)[BASELINE_SKILL_ID].items():
        artifact_body += f"- `{h}` — severity {sev}\n"
    artifact_path.write_text(artifact_body, encoding="utf-8")

    return OptimizationIteration(
        iteration_id=_baseline_iteration_id(cluster.cluster_id),
        run_id=run_id,
        iteration_index=0,
        parent_iteration_id=None,
        design_artifact_ref=str(artifact_path),
        scores=_baseline_scores(reconciled),
        reasoning=(
            "Baseline — heuristic severities imported verbatim from L5 "
            "reconciled verdict. No Claude call; no regression possible "
            "(iteration 0 is its own reference)."
        ),
        accepted=True,
        regression_reason=None,
        delta_per_heuristic={},
        informing_review_ids=list(cluster.member_review_ids),
        recorded_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_user_message(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    decision: DesignDecision,
) -> str:
    """Render the per-cluster user message for design-optimize.

    Envelope: cluster context + before_snapshot + after_snapshot +
    baseline_heuristics (slug + severity + violation). The model
    re-audits each baseline heuristic against the after_snapshot.
    """
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})

    parts: list[str] = [
        "<cluster>",
        f"  <cluster_id>{cluster.cluster_id.translate(escape)}</cluster_id>",
        f"  <label>{cluster.label.translate(escape)}</label>",
    ]
    if cluster.ui_context is not None:
        parts.append(
            f"  <ui_context>{cluster.ui_context.translate(escape)}</ui_context>"
        )
    for i, q in enumerate(cluster.representative_quotes):
        parts.append(f'  <q idx="{i}">{q.translate(escape)}</q>')
    parts.append("</cluster>")

    parts.append(
        f"<before_snapshot>{decision.before_snapshot.translate(escape)}</before_snapshot>"
    )
    parts.append(
        f"<after_snapshot>{decision.after_snapshot.translate(escape)}</after_snapshot>"
    )

    parts.append("<baseline_heuristics>")
    for v in reconciled.ranked_violations:
        parts.append(
            f'  <h slug="{v.heuristic.translate(escape)}" '
            f'severity="{v.severity}">'
            f"{v.violation.translate(escape)}"
            f"</h>"
        )
    parts.append("</baseline_heuristics>")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_TOP_LEVEL_KEYS = {"scored_heuristics", "reasoning"}


def parse_optimize_response(
    text: str,
    *,
    baseline_heuristics: list[str],
) -> dict[str, Any]:
    """Extract and validate the L8 re-audit payload.

    Cross-validates that ``scored_heuristics`` keys exactly match
    the ``baseline_heuristics`` list — the Pareto evaluator requires
    a comparable vector. Missing or extra keys raise.

    Each severity must be in :data:`VALID_ANCHORED_SEVERITIES`.
    """
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise OptimizeParseError(f"no JSON object found in response: {text!r}")
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise OptimizeParseError(
            f"malformed JSON: {err}; text={text!r}"
        ) from err
    if not isinstance(data, dict):
        raise OptimizeParseError(
            f"expected JSON object, got {type(data).__name__}"
        )

    actual = set(data.keys())
    missing = _TOP_LEVEL_KEYS - actual
    if missing:
        raise OptimizeParseError(
            f"missing required top-level keys: {sorted(missing)}"
        )
    extra = actual - _TOP_LEVEL_KEYS
    if extra:
        raise OptimizeParseError(
            f"unexpected top-level keys: {sorted(extra)}"
        )

    scored = data["scored_heuristics"]
    if not isinstance(scored, dict):
        raise OptimizeParseError(
            f"'scored_heuristics' must be dict, got {type(scored).__name__}"
        )

    baseline_set = set(baseline_heuristics)
    scored_set = set(scored.keys())
    if scored_set != baseline_set:
        missing_h = baseline_set - scored_set
        extra_h = scored_set - baseline_set
        parts = []
        if missing_h:
            parts.append(f"missing heuristics: {sorted(missing_h)}")
        if extra_h:
            parts.append(f"extra heuristics: {sorted(extra_h)}")
        raise OptimizeParseError(
            f"scored_heuristics keys must exactly match baseline list — "
            f"{'; '.join(parts)}"
        )

    for h, sev in scored.items():
        if not isinstance(sev, int) or isinstance(sev, bool):
            raise OptimizeParseError(
                f"scored_heuristics[{h!r}] must be int, got {type(sev).__name__}"
            )
        if sev not in VALID_ANCHORED_SEVERITIES:
            raise OptimizeParseError(
                f"scored_heuristics[{h!r}]={sev} not in "
                f"{sorted(VALID_ANCHORED_SEVERITIES)}"
            )

    reasoning = data["reasoning"]
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise OptimizeParseError("'reasoning' must be non-empty str")

    return data


# ---------------------------------------------------------------------------
# Per-cluster pipeline
# ---------------------------------------------------------------------------


OptimizeStatus = Literal["optimized", "fallback"]


@dataclass(frozen=True, slots=True)
class OptimizeOutcome:
    """One cluster's optimization result.

    Always yields two iterations (baseline + proposed). On fallback
    the proposed iteration has baseline-copied scores (a no-op that
    the Pareto verdict correctly rejects) and ``reason`` names the
    parse fault.
    """

    cluster_id: str
    baseline: OptimizationIteration
    proposed: OptimizationIteration
    verdict: ParetoVerdict
    native_payload: dict[str, Any]
    status: OptimizeStatus
    reason: str | None = None


async def optimize_cluster(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    priority: PriorityScore,  # unused here but kept for signature parity + future use
    decision: DesignDecision,
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str,
    run_id: str,
    artifacts_dir: Path,
    max_regression: int = DEFAULT_MAX_REGRESSION,
) -> OptimizeOutcome:
    """Generate iteration 0 + iteration 1 for one cluster.

    Iteration 0 is built from the reconciled baseline; iteration 1
    is a Claude re-audit of ``decision.after_snapshot``. The Pareto
    verdict accepts or rejects iteration 1; the outcome carries both
    iterations plus the verdict for provenance.

    Transport errors propagate; parse failures fall back to a no-op
    iteration 1 that the Pareto verdict will correctly reject as no
    improvement.
    """
    baseline = build_baseline_iteration(
        cluster, reconciled, decision, artifacts_dir, run_id=run_id
    )

    baseline_heuristics = list(
        reconciled_heuristic_list(reconciled)
    )

    user = build_user_message(cluster, reconciled, decision)
    resp = await client.call(
        system=SYSTEM_PROMPT,
        user=user,
        model=model,
        skill_id=skill_id,
        skill_hash=skill_hash_value,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )

    baseline_scores = baseline.scores[BASELINE_SKILL_ID]
    try:
        payload = parse_optimize_response(
            resp.response, baseline_heuristics=baseline_heuristics
        )
        proposed_scores = dict(payload["scored_heuristics"])
        reasoning_text = payload["reasoning"]
        parse_error: str | None = None
    except OptimizeParseError as e:
        _log.warning(
            "optimize parse failed for cluster %s: %s — "
            "iteration 1 falls back to baseline scores",
            cluster.cluster_id,
            e,
        )
        proposed_scores = dict(baseline_scores)  # no-op — will be rejected
        reasoning_text = (
            f"Fallback — parse failure on re-audit; proposed iteration "
            f"copies baseline scores (no improvement). Parse error: {e}"
        )
        parse_error = str(e)

    # Pareto verdict over the single-skill flat score dict.
    v = pareto_verdict(
        parent=baseline_scores,
        child=proposed_scores,
        max_regression=max_regression,
    )

    # Write the proposed design artifact.
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    proposed_artifact = artifacts_dir / f"{cluster.cluster_id}_iter01.md"
    proposed_body = (
        f"# {cluster.cluster_id} — iteration 1 (proposed)\n\n"
        f"## Parent iteration\n{baseline.iteration_id}\n\n"
        f"## after_snapshot (from L7 decision {decision.decision_id})\n"
        f"{decision.after_snapshot}\n\n"
        f"## Re-audit severities\n"
    )
    for h in baseline_heuristics:
        proposed_body += (
            f"- `{h}` — baseline {baseline_scores[h]} → proposed "
            f"{proposed_scores[h]} (delta {v.delta_per_heuristic.get(h, 0):+d})\n"
        )
    proposed_body += f"\n## Model reasoning\n{reasoning_text}\n\n"
    proposed_body += f"## Pareto verdict\n{v.reason}\n"
    proposed_artifact.write_text(proposed_body, encoding="utf-8")

    proposed = OptimizationIteration(
        iteration_id=_proposed_iteration_id(cluster.cluster_id),
        run_id=run_id,
        iteration_index=1,
        parent_iteration_id=baseline.iteration_id,
        design_artifact_ref=str(proposed_artifact),
        scores={BASELINE_SKILL_ID: proposed_scores},
        reasoning=reasoning_text,
        accepted=v.accepted,
        regression_reason=v.reason if not v.accepted else None,
        delta_per_heuristic=dict(v.delta_per_heuristic),
        informing_review_ids=list(cluster.member_review_ids),
        recorded_at=datetime.now(UTC),
    )

    if parse_error is not None:
        native_payload = {
            "fallback": True,
            "reason": parse_error,
            "raw_response": resp.response,
        }
        status: OptimizeStatus = "fallback"
    else:
        native_payload = payload
        status = "optimized"

    return OptimizeOutcome(
        cluster_id=cluster.cluster_id,
        baseline=baseline,
        proposed=proposed,
        verdict=v,
        native_payload=native_payload,
        status=status,
        reason=parse_error,
    )


def reconciled_heuristic_list(reconciled: ReconciledVerdict) -> list[str]:
    """Dedupe baseline heuristic slugs in the reconciled verdict.

    Parser expects the baseline list; this function is the canonical
    way to build it. Ordered by first appearance for determinism.
    """
    seen: list[str] = []
    for v in reconciled.ranked_violations:
        if v.heuristic not in seen:
            seen.append(v.heuristic)
    return seen


async def optimize_batch(
    clusters: list[InsightCluster],
    reconciled_by_cluster: dict[str, ReconciledVerdict],
    priority_by_cluster: dict[str, PriorityScore],
    decision_by_cluster: dict[str, DesignDecision],
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str | None = None,
    run_id: str,
    artifacts_dir: Path,
    max_regression: int = DEFAULT_MAX_REGRESSION,
) -> tuple[list[OptimizeOutcome], list[tuple[str, Exception]]]:
    """Concurrent optimize over a batch of clusters.

    A cluster missing any of reconciled / priority / decision gets a
    fallback skip without a Claude call.
    """
    sh = skill_hash_value if skill_hash_value is not None else skill_hash()

    async def _one(
        c: InsightCluster,
    ) -> tuple[str, OptimizeOutcome | Exception]:
        reconciled = reconciled_by_cluster.get(c.cluster_id)
        priority = priority_by_cluster.get(c.cluster_id)
        decision = decision_by_cluster.get(c.cluster_id)
        if reconciled is None or priority is None or decision is None:
            missing = []
            if reconciled is None:
                missing.append("reconciled_verdict")
            if priority is None:
                missing.append("priority_score")
            if decision is None:
                missing.append("design_decision")
            reason = (
                f"cluster {c.cluster_id} missing {', '.join(missing)}; "
                f"skipping L8 optimization"
            )
            _log.warning(reason)
            # Synthesize a minimal "both iterations baseline" outcome
            # that the downstream provenance can display.
            return (
                c.cluster_id,
                _empty_outcome(c, reason, run_id=run_id, artifacts_dir=artifacts_dir),
            )
        try:
            outcome = await optimize_cluster(
                c, reconciled, priority, decision, client,
                model=model, skill_id=skill_id,
                skill_hash_value=sh, run_id=run_id,
                artifacts_dir=artifacts_dir,
                max_regression=max_regression,
            )
            return (c.cluster_id, outcome)
        except Exception as e:  # noqa: BLE001
            return (c.cluster_id, e)

    results = await asyncio.gather(*(_one(c) for c in clusters))
    outcomes: list[OptimizeOutcome] = []
    failures: list[tuple[str, Exception]] = []
    for cid, payload in results:
        if isinstance(payload, OptimizeOutcome):
            outcomes.append(payload)
        else:
            failures.append((cid, payload))
    return outcomes, failures


def _empty_outcome(
    cluster: InsightCluster,
    reason: str,
    *,
    run_id: str,
    artifacts_dir: Path,
) -> OptimizeOutcome:
    """Minimal OptimizeOutcome for clusters missing prerequisite inputs."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    placeholder = artifacts_dir / f"{cluster.cluster_id}_iter00.md"
    placeholder.write_text(
        f"# {cluster.cluster_id} — empty (no inputs)\n\n{reason}\n",
        encoding="utf-8",
    )
    baseline = OptimizationIteration(
        iteration_id=_baseline_iteration_id(cluster.cluster_id),
        run_id=run_id,
        iteration_index=0,
        parent_iteration_id=None,
        design_artifact_ref=str(placeholder),
        scores={BASELINE_SKILL_ID: {}},
        reasoning=f"Skipped — {reason}",
        accepted=True,
        regression_reason=None,
        delta_per_heuristic={},
        informing_review_ids=list(cluster.member_review_ids),
        recorded_at=datetime.now(UTC),
    )
    # We still need a proposed iteration to keep the outcome-shape
    # consistent; duplicate the baseline (schema requires a parent for
    # index>0).
    proposed = OptimizationIteration(
        iteration_id=_proposed_iteration_id(cluster.cluster_id),
        run_id=run_id,
        iteration_index=1,
        parent_iteration_id=baseline.iteration_id,
        design_artifact_ref=str(placeholder),
        scores={BASELINE_SKILL_ID: {}},
        reasoning=f"Skipped — {reason}",
        accepted=False,
        regression_reason=reason,
        delta_per_heuristic={},
        informing_review_ids=list(cluster.member_review_ids),
        recorded_at=datetime.now(UTC),
    )
    verdict = ParetoVerdict(
        accepted=False,
        reason=reason,
        regression_count=0,
        dominance=False,
        delta_per_heuristic={},
    )
    return OptimizeOutcome(
        cluster_id=cluster.cluster_id,
        baseline=baseline,
        proposed=proposed,
        verdict=verdict,
        native_payload={"fallback": True, "reason": reason, "raw_response": ""},
        status="fallback",
        reason=reason,
    )


def _sort_outcomes(outcomes: list[OptimizeOutcome]) -> list[OptimizeOutcome]:
    return sorted(outcomes, key=lambda o: o.cluster_id)


def _native_row(outcome: OptimizeOutcome) -> dict[str, Any]:
    return {
        "cluster_id": outcome.cluster_id,
        "status": outcome.status,
        "reason": outcome.reason,
        "pareto_accepted": outcome.verdict.accepted,
        "pareto_reason": outcome.verdict.reason,
        "pareto_dominance": outcome.verdict.dominance,
        "pareto_regression_count": outcome.verdict.regression_count,
        "payload": outcome.native_payload,
    }


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def build_provenance(
    outcomes: list[OptimizeOutcome],
    failures: list[tuple[str, Exception]],
    *,
    model: str,
    max_regression: int,
) -> dict[str, Any]:
    """Summarise an L8 run into the provenance payload.

    Tracks accept/reject rates, Pareto-dominance vs weighted-sum-accept
    breakdown, regression-count distribution, per-heuristic severity
    delta statistics.
    """
    optimized = [o for o in outcomes if o.status == "optimized"]
    fallback = [o for o in outcomes if o.status == "fallback"]
    accepted = [o for o in outcomes if o.verdict.accepted]
    rejected = [o for o in outcomes if not o.verdict.accepted]
    dominance_accepted = [
        o for o in accepted if o.verdict.dominance
    ]
    weighted_sum_accepted = [
        o for o in accepted if not o.verdict.dominance
    ]

    regression_histogram: dict[str, int] = {}
    for o in outcomes:
        key = str(o.verdict.regression_count)
        regression_histogram[key] = regression_histogram.get(key, 0) + 1

    # Per-heuristic mean delta (improvement if negative).
    delta_sums: dict[str, int] = {}
    delta_counts: dict[str, int] = {}
    for o in outcomes:
        for h, d in o.verdict.delta_per_heuristic.items():
            delta_sums[h] = delta_sums.get(h, 0) + d
            delta_counts[h] = delta_counts.get(h, 0) + 1
    mean_delta_per_heuristic = (
        {h: delta_sums[h] / delta_counts[h] for h in delta_sums}
        if delta_sums
        else {}
    )

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "max_regression": max_regression,
        "cluster_count": len(outcomes) + len(failures),
        "optimized_count": len(optimized),
        "fallback_count": len(fallback),
        "transport_failure_count": len(failures),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "dominance_accepted_count": len(dominance_accepted),
        "weighted_sum_accepted_count": len(weighted_sum_accepted),
        "regression_count_histogram": dict(sorted(regression_histogram.items())),
        "mean_delta_per_heuristic": {
            h: round(v, 2) for h, v in mean_delta_per_heuristic.items()
        },
        "fallback_reasons": sorted(
            [{"cluster_id": o.cluster_id, "reason": o.reason} for o in fallback],
            key=lambda r: r["cluster_id"],
        ),
        "transport_failures": sorted(
            [
                {"cluster_id": cid, "error": f"{type(e).__name__}: {e}"}
                for cid, e in failures
            ],
            key=lambda r: r["cluster_id"],
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    repo_root = _resolve_repo_root()

    parser = argparse.ArgumentParser(
        description=(
            "L8 design-optimize — re-audits each cluster's L7 decision "
            "after_snapshot against the baseline heuristic list, applies "
            "Pareto dominance + weighted-sum fallback, and emits two "
            "OptimizationIteration records per cluster (baseline + "
            "proposed). Thin-spine: 2 iterations per cluster, not a "
            "multi-step optimisation loop."
        ),
    )
    parser.add_argument("--reconciled", type=Path, default=repo_root / DEFAULT_RECONCILED)
    parser.add_argument("--priority", type=Path, default=repo_root / DEFAULT_PRIORITY)
    parser.add_argument("--decisions", type=Path, default=repo_root / DEFAULT_DECISIONS)
    parser.add_argument("--clusters", type=Path, default=repo_root / DEFAULT_CLUSTERS)
    parser.add_argument(
        "--output", type=Path, default=repo_root / DEFAULT_ITERATIONS
    )
    parser.add_argument(
        "--native-output", type=Path, default=repo_root / DEFAULT_NATIVE
    )
    parser.add_argument(
        "--artifacts-dir", type=Path, default=repo_root / DEFAULT_ARTIFACTS_DIR
    )
    parser.add_argument(
        "--mode", choices=("live", "replay"), default="replay"
    )
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--replay-log", type=Path,
        default=repo_root / "data/cache/responses.jsonl",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--usd-ceiling", type=float, default=5.0)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--max-regression", type=int, default=DEFAULT_MAX_REGRESSION,
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    clusters = load_clusters(args.clusters)
    _log.info("loaded %d clusters from %s", len(clusters), args.clusters)
    if not clusters:
        _log.error("empty clusters input — nothing to optimize")
        return 1

    reconciled_by_cluster = load_reconciled_verdicts(args.reconciled)
    priority_by_cluster = load_priority_scores(args.priority)
    decision_by_cluster = load_decisions(args.decisions)
    _log.info(
        "inputs: %d reconciled, %d priority, %d decisions",
        len(reconciled_by_cluster),
        len(priority_by_cluster),
        len(decision_by_cluster),
    )

    run_id = args.run_id or _default_run_id().replace("l4-", "l8-", 1)

    client = Client(
        mode=args.mode,
        run_id=run_id,
        replay_log_path=args.replay_log,
        usd_ceiling=args.usd_ceiling,
        concurrency=args.concurrency,
    )
    _log.info(
        "client mode=%s cache_size=%d usd_ceiling=$%.2f max_regression=%d",
        args.mode,
        client.cache_size,
        args.usd_ceiling,
        args.max_regression,
    )

    outcomes, failures = asyncio.run(
        optimize_batch(
            clusters,
            reconciled_by_cluster,
            priority_by_cluster,
            decision_by_cluster,
            client,
            model=args.model,
            run_id=run_id,
            artifacts_dir=args.artifacts_dir,
            max_regression=args.max_regression,
        )
    )

    sorted_outcomes = _sort_outcomes(outcomes)

    # Iterations file — both iterations per cluster, flat.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    clusters_hash = hash_file(args.clusters)
    reconciled_hash = hash_file(args.reconciled)
    priority_hash = hash_file(args.priority)
    decisions_hash = hash_file(args.decisions)

    iterations_rows: list[dict[str, Any]] = []
    for o in sorted_outcomes:
        iterations_rows.append(o.baseline.model_dump(mode="json"))
        iterations_rows.append(o.proposed.model_dump(mode="json"))

    out_meta = write_jsonl_atomic(
        args.output,
        iterations_rows,
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes={
            args.clusters.name: clusters_hash,
            args.reconciled.name: reconciled_hash,
            args.priority.name: priority_hash,
            args.decisions.name: decisions_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d iterations (%d clusters × 2) to %s (sha256=%s…)",
        len(iterations_rows),
        len(sorted_outcomes),
        args.output,
        out_meta.artifact_sha256[:16],
    )

    # Native sidecar.
    args.native_output.parent.mkdir(parents=True, exist_ok=True)
    native_meta = write_jsonl_atomic(
        args.native_output,
        [_native_row(o) for o in sorted_outcomes],
        run_id=run_id,
        layer=f"{LAYER_NAME}_native",
        input_hashes={
            args.clusters.name: clusters_hash,
            args.reconciled.name: reconciled_hash,
            args.priority.name: priority_hash,
            args.decisions.name: decisions_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d native rows to %s (sha256=%s…)",
        len(sorted_outcomes),
        args.native_output,
        native_meta.artifact_sha256[:16],
    )

    # Provenance.
    provenance_path = args.output.with_suffix(".provenance.json")
    provenance_payload = (
        json.dumps(
            build_provenance(
                outcomes, failures, model=args.model,
                max_regression=args.max_regression,
            ),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(provenance_path, provenance_payload)
    _log.info("wrote L8 provenance to %s", provenance_path)

    accepted_count = sum(1 for o in outcomes if o.verdict.accepted)
    optimized_count = sum(1 for o in outcomes if o.status == "optimized")
    fallback_count = sum(1 for o in outcomes if o.status == "fallback")
    _log.info(
        "L8 done. mode=%s live-spend=$%.4f optimized=%d fallback=%d "
        "accepted=%d rejected=%d transport_fail=%d",
        args.mode,
        client.cumulative_usd,
        optimized_count,
        fallback_count,
        accepted_count,
        len(outcomes) - accepted_count,
        len(failures),
    )

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
