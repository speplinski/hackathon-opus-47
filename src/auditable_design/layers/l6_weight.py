"""Layer 6 — priority weighting of reconciled verdicts.

L5 produces a :class:`ReconciledVerdict` per cluster — cross-skill
ranked violations, tensions, gaps. L6 scores each reconciled cluster
on five priority dimensions (severity / reach / persistence /
business_impact / cognitive_cost), applies user-configurable
meta-weights, and emits one :class:`PriorityScore` per cluster.

Two-pass with optional third
----------------------------
The skill is non-deterministic (priority is a judgment call even at
temperature 0); each cluster is scored twice and compared. If any
dimension's two scores differ by more than 1, a third pass is asked
and the median per dimension is taken. ``validation_passes`` records
2 or 3; ``validation_delta`` records the maximum per-dimension delta
across passes (useful for reviewer confidence calibration — a cluster
with delta=0 is much more trustworthy than one with delta=3).

Weights are user-layer, not model-layer
---------------------------------------
The skill does NOT see meta_weights. The model scores the five
dimensions on content alone; the L6 module applies
:data:`DEFAULT_META_WEIGHTS` (symmetric 0.2 each, summing to 1.0) to
compute ``weighted_total = sum(dim_score * weight)``. Users override
weights via the UI / ``RunContext`` (per ARCHITECTURE.md §4.7); the
model output is weight-agnostic and reusable across weight sets.

Input / output
--------------
* Reads a JSONL of :class:`ReconciledVerdict` rows (the output of
  ``l5_reconcile``). Default: ``data/derived/l5_reconciled_verdicts.jsonl``.
* Reads the L3b labeled clusters file for cluster context (label,
  quotes, member count, optional ui_context / html / screenshot_ref).
* Writes :data:`DEFAULT_VERDICTS` — one :class:`PriorityScore` per
  reconciled cluster.
* Writes :data:`DEFAULT_NATIVE` with the raw per-pass payloads
  (up to three passes, each carrying dimensions + rationale).
* Writes a ``.provenance.json`` sidecar with per-dim score distributions,
  validation-pass histogram, validation-delta distribution.

Model default
-------------
Opus 4.7 per ADR-009: L6 is reasoning-heavy (priority judgment under
anchors) and low-volume (one reconciled verdict per cluster, double-pass
baseline). Opus 4.7 strips ``temperature`` at the API;
``claude_client._omits_sampling_params`` handles this transparently.

Fallback discipline
-------------------
A cluster whose reconciled verdict parses but whose two passes both
produce a malformed payload falls back to an empty PriorityScore with
``validation_passes=2``, ``validation_delta=0.0``, and the raw payloads
in the native sidecar. Transport-level errors still propagate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from auditable_design.claude_client import Client
from auditable_design.layers.l4_audit import (
    AuditParseError,
    _atomic_write_bytes,
    _configure_logging,
    _default_run_id,
    _resolve_repo_root,
    load_clusters,
)
from auditable_design.schemas import (
    SCHEMA_VERSION,
    InsightCluster,
    PriorityScore,
    ReconciledVerdict,
)
from auditable_design.storage import hash_file, read_jsonl, write_jsonl_atomic

__all__ = [
    "DEFAULT_CLUSTERS",
    "DEFAULT_META_WEIGHTS",
    "DEFAULT_NATIVE",
    "DEFAULT_RECONCILED",
    "DEFAULT_VERDICTS",
    "DIMENSION_KEYS",
    "LAYER_NAME",
    "MAX_TOKENS",
    "MODEL",
    "SKILL_ID",
    "SYSTEM_PROMPT",
    "TEMPERATURE",
    "PriorityOutcome",
    "PriorityParseError",
    "build_provenance",
    "build_user_message",
    "load_reconciled_verdicts",
    "main",
    "parse_priority_response",
    "score_cluster",
    "score_batch",
    "skill_hash",
    "weighted_total",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_ID: str = "priority-weight"
LAYER_NAME: str = "l6_weight"

# Opus 4.7 per ADR-009: L6 is reasoning-heavy (anchor-calibrated priority
# scoring + judgment on non-orthogonal dimensions), low-volume (one
# reconciled verdict per cluster, 2–3 passes). Opus 4.7 rejects
# ``temperature`` at the API; ``claude_client._omits_sampling_params``
# gates the send.
MODEL: str = "claude-opus-4-7"
TEMPERATURE: float = 0.0

# Per-pass output: 5 dim scores (5 ints) + 5 rationales (one sentence
# each ~40 tokens) + overall_note (~60 tokens) ≈ 300 tokens. 4096 leaves
# generous headroom for any reasoning preamble.
MAX_TOKENS: int = 4096

# The five priority dimensions (schemas.py §4.7). Parser enforces
# exactly these keys.
DIMENSION_KEYS: frozenset[str] = frozenset(
    {
        "severity",
        "reach",
        "persistence",
        "business_impact",
        "cognitive_cost",
    }
)

# Default meta-weights — symmetric. Sum to 1.0. Users override via
# RunContext / UI per ARCHITECTURE.md §4.7.
DEFAULT_META_WEIGHTS: dict[str, float] = {
    "severity": 0.2,
    "reach": 0.2,
    "persistence": 0.2,
    "business_impact": 0.2,
    "cognitive_cost": 0.2,
}

# Validation discipline (SKILL.md "Two honest scorers" section):
# Double-pass baseline; if any per-dim delta > this threshold, trigger
# a third pass and take the median per dim.
MAX_DIMENSION_DELTA: int = 1

# Default paths.
DEFAULT_RECONCILED = Path("data/derived/l5_reconciled_verdicts.jsonl")
DEFAULT_CLUSTERS = Path("data/derived/l3b_labeled_clusters.jsonl")
DEFAULT_VERDICTS = Path("data/derived/l6_priority_scores.jsonl")
DEFAULT_NATIVE = Path("data/derived/l6_priority_scores.native.jsonl")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PriorityParseError(AuditParseError):
    """Parse failure specific to the L6 priority-scoring payload."""


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


def load_reconciled_verdicts(path: Path) -> dict[str, ReconciledVerdict]:
    """Load L5 output as a dict keyed by cluster_id."""
    rows = read_jsonl(path)
    result: dict[str, ReconciledVerdict] = {}
    for i, row in enumerate(rows):
        try:
            v = ReconciledVerdict.model_validate(row)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"{LAYER_NAME}: row {i} of {path} is not a valid "
                f"ReconciledVerdict: {e}"
            ) from e
        if v.cluster_id in result:
            _log.warning(
                "duplicate reconciled verdict for cluster=%s — later row wins",
                v.cluster_id,
            )
        result[v.cluster_id] = v
    return result


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_user_message(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
) -> str:
    """Render the per-cluster user message for priority-weight.

    Threads the cluster context (label, ui_context, html,
    screenshot_ref, quotes, member_review_ids count) and the
    ReconciledVerdict (summary + top ranked + tensions + gaps) into
    one XML envelope. Text-only — screenshots are not attached; L6
    judges on structured reconciled evidence, not UI rendering.
    """
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})

    parts: list[str] = [
        "<cluster>",
        f"  <cluster_id>{cluster.cluster_id.translate(escape)}</cluster_id>",
        f"  <label>{cluster.label.translate(escape)}</label>",
        f"  <member_review_ids_count>{len(cluster.member_review_ids)}</member_review_ids_count>",
    ]
    if cluster.ui_context is not None:
        parts.append(
            f"  <ui_context>{cluster.ui_context.translate(escape)}</ui_context>"
        )
    if cluster.html is not None:
        parts.append(f"  <html><![CDATA[\n{cluster.html}\n]]></html>")
    if cluster.screenshot_ref is not None:
        parts.append(
            f"  <screenshot_ref>{cluster.screenshot_ref.translate(escape)}</screenshot_ref>"
        )
    for i, q in enumerate(cluster.representative_quotes):
        parts.append(f'  <q idx="{i}">{q.translate(escape)}</q>')
    parts.append("</cluster>")

    parts.append("<reconciled_verdict>")
    parts.append(
        f"  <cluster_id>{reconciled.cluster_id.translate(escape)}</cluster_id>"
    )
    # Ranked violations — include top ranked entries for the model's
    # judgment. Render heuristic + severity + source_skills + rationale.
    parts.append("  <ranked_violations>")
    for i, v in enumerate(reconciled.ranked_violations):
        # The HeuristicViolation.reasoning is already a rich string
        # that carries cross-skill context (rank_score, source_skills,
        # etc.) from the L5 _build_reconciled_verdict synthesis.
        parts.append(
            f'    <entry idx="{i}" heuristic="{v.heuristic.translate(escape)}" '
            f'severity="{v.severity}">'
            f"{v.violation.translate(escape)} "
            f"[reasoning: {v.reasoning.translate(escape)}]"
            f"</entry>"
        )
    parts.append("  </ranked_violations>")

    # Tensions — the load-bearing L5 contribution; include verbatim.
    parts.append("  <tensions>")
    for i, t in enumerate(reconciled.tensions):
        parts.append(
            f'    <tension idx="{i}" skill_a="{t.skill_a}" skill_b="{t.skill_b}" '
            f'axis="{t.axis.translate(escape)}">'
            f"{t.resolution.translate(escape)}"
            f"</tension>"
        )
    parts.append("  </tensions>")
    parts.append("</reconciled_verdict>")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_TOP_LEVEL_KEYS = {"dimensions", "rationale", "overall_note"}


def parse_priority_response(text: str) -> dict[str, Any]:
    """Extract and validate one priority-scoring pass.

    On success returns a dict with `dimensions` (5 ints in [0,10]),
    `rationale` (5 non-empty strings), `overall_note` (non-empty).
    Raises :class:`PriorityParseError` on any structural or type
    violation.
    """
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise PriorityParseError(f"no JSON object found in response: {text!r}")
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise PriorityParseError(f"malformed JSON: {err}; text={text!r}") from err
    if not isinstance(data, dict):
        raise PriorityParseError(
            f"expected JSON object, got {type(data).__name__}"
        )

    actual = set(data.keys())
    missing = _TOP_LEVEL_KEYS - actual
    if missing:
        raise PriorityParseError(
            f"missing required top-level keys: {sorted(missing)}"
        )
    extra = actual - _TOP_LEVEL_KEYS
    if extra:
        raise PriorityParseError(f"unexpected top-level keys: {sorted(extra)}")

    # dimensions
    dims = data["dimensions"]
    if not isinstance(dims, dict):
        raise PriorityParseError(
            f"'dimensions' must be dict, got {type(dims).__name__}"
        )
    d_missing = DIMENSION_KEYS - set(dims.keys())
    if d_missing:
        raise PriorityParseError(
            f"dimensions missing keys: {sorted(d_missing)}"
        )
    d_extra = set(dims.keys()) - DIMENSION_KEYS
    if d_extra:
        raise PriorityParseError(
            f"dimensions has unexpected keys: {sorted(d_extra)}"
        )
    for k, v in dims.items():
        if not isinstance(v, int) or isinstance(v, bool):
            raise PriorityParseError(
                f"dimensions[{k!r}] must be int, got {type(v).__name__}"
            )
        if not (0 <= v <= 10):
            raise PriorityParseError(
                f"dimensions[{k!r}]={v} out of [0, 10]"
            )

    # rationale
    rationale = data["rationale"]
    if not isinstance(rationale, dict):
        raise PriorityParseError(
            f"'rationale' must be dict, got {type(rationale).__name__}"
        )
    r_missing = DIMENSION_KEYS - set(rationale.keys())
    if r_missing:
        raise PriorityParseError(
            f"rationale missing keys: {sorted(r_missing)}"
        )
    r_extra = set(rationale.keys()) - DIMENSION_KEYS
    if r_extra:
        raise PriorityParseError(
            f"rationale has unexpected keys: {sorted(r_extra)}"
        )
    for k, v in rationale.items():
        if not isinstance(v, str) or not v.strip():
            raise PriorityParseError(
                f"rationale[{k!r}] must be non-empty str"
            )

    note = data["overall_note"]
    if not isinstance(note, str) or not note.strip():
        raise PriorityParseError("'overall_note' must be non-empty str")

    return data


# ---------------------------------------------------------------------------
# Multi-pass aggregation
# ---------------------------------------------------------------------------


def _aggregate_passes(
    passes: list[dict[str, int]],
) -> tuple[dict[str, int], float]:
    """Combine per-dimension scores from 2 or 3 passes.

    With 2 passes: if every per-dim delta ≤ :data:`MAX_DIMENSION_DELTA`,
    take the mean rounded to int (ties break down — `int(round(x))`
    is banker's-round in Python 3, which is acceptable at the hackathon
    scale; for stricter semantics use ``math.floor(x + 0.5)``).

    With 3 passes: take the median per dim (statistics.median on an
    odd-length list returns an integer when inputs are ints).

    Returns (aggregated dimensions, max per-dim delta across all passes).
    """
    if len(passes) == 2:
        deltas = {
            d: abs(passes[0][d] - passes[1][d]) for d in DIMENSION_KEYS
        }
        max_delta = max(deltas.values())
        aggregated = {
            d: int(round((passes[0][d] + passes[1][d]) / 2))
            for d in DIMENSION_KEYS
        }
        return aggregated, float(max_delta)

    if len(passes) == 3:
        # Compute max delta across the three-pass triad (for provenance).
        max_delta = 0
        for d in DIMENSION_KEYS:
            vals = [passes[i][d] for i in range(3)]
            triad_max_delta = max(vals) - min(vals)
            if triad_max_delta > max_delta:
                max_delta = triad_max_delta
        aggregated = {
            d: int(statistics.median([passes[i][d] for i in range(3)]))
            for d in DIMENSION_KEYS
        }
        return aggregated, float(max_delta)

    raise ValueError(f"unexpected pass count {len(passes)} (must be 2 or 3)")


def _needs_third_pass(pass_a: dict[str, int], pass_b: dict[str, int]) -> bool:
    """True if any per-dim delta exceeds :data:`MAX_DIMENSION_DELTA`."""
    return any(
        abs(pass_a[d] - pass_b[d]) > MAX_DIMENSION_DELTA for d in DIMENSION_KEYS
    )


def weighted_total(
    dimensions: dict[str, int], meta_weights: dict[str, float]
) -> float:
    """``sum(dim_value × meta_weight)`` — the final priority scalar.

    Weights and dimensions must share the same key set
    (:data:`DIMENSION_KEYS`). The callers ensure this invariant.
    """
    return float(
        sum(dimensions[d] * meta_weights[d] for d in DIMENSION_KEYS)
    )


# ---------------------------------------------------------------------------
# Per-cluster pipeline
# ---------------------------------------------------------------------------


PriorityStatus = Literal["scored", "fallback"]


@dataclass(frozen=True, slots=True)
class PriorityOutcome:
    """One cluster's priority-scoring result.

    On success, `priority` is a fully-populated PriorityScore with the
    aggregated dimensions, the weighted total, pass count, and max
    delta. `passes` carries the raw per-pass payloads (2 or 3) for the
    native sidecar.

    On fallback (both/all passes malformed), `priority` has zero-filled
    dimensions and `weighted_total = 0`, and `reason` names the fault.
    """

    cluster_id: str
    priority: PriorityScore
    passes: list[dict[str, Any]]
    status: PriorityStatus
    reason: str | None = None


async def _score_one_pass(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    client: Client,
    *,
    model: str,
    skill_hash_value: str,
) -> dict[str, Any]:
    """One Claude call; raises PriorityParseError on malformed payload."""
    user = build_user_message(cluster, reconciled)
    resp = await client.call(
        system=SYSTEM_PROMPT,
        user=user,
        model=model,
        skill_id=SKILL_ID,
        skill_hash=skill_hash_value,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
    return parse_priority_response(resp.response)


async def score_cluster(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    client: Client,
    *,
    model: str = MODEL,
    meta_weights: dict[str, float] | None = None,
    skill_hash_value: str,
) -> PriorityOutcome:
    """Score one cluster. Two-pass baseline with optional third pass.

    Never raises on parse failure within a pass — a single malformed
    pass produces a fallback pass payload containing the raw response;
    the aggregation step drops fallbacks and, if both passes are
    fallbacks, produces a fallback outcome. Transport errors still
    propagate.
    """
    weights = meta_weights if meta_weights is not None else DEFAULT_META_WEIGHTS

    passes_raw: list[dict[str, Any]] = []

    # Pass 1
    try:
        p1 = await _score_one_pass(
            cluster, reconciled, client,
            model=model, skill_hash_value=skill_hash_value,
        )
        passes_raw.append({"pass": 1, "status": "parsed", "payload": p1})
    except PriorityParseError as e:
        passes_raw.append({"pass": 1, "status": "fallback", "reason": str(e)})

    # Pass 2
    try:
        p2 = await _score_one_pass(
            cluster, reconciled, client,
            model=model, skill_hash_value=skill_hash_value,
        )
        passes_raw.append({"pass": 2, "status": "parsed", "payload": p2})
    except PriorityParseError as e:
        passes_raw.append({"pass": 2, "status": "fallback", "reason": str(e)})

    parsed_passes = [
        p["payload"] for p in passes_raw if p["status"] == "parsed"
    ]

    # If neither pass parsed, fall back.
    if not parsed_passes:
        fallback_dims = {k: 0 for k in DIMENSION_KEYS}
        priority = PriorityScore(
            cluster_id=cluster.cluster_id,
            dimensions=fallback_dims,
            meta_weights=dict(weights),
            weighted_total=0.0,
            validation_passes=2,
            validation_delta=0.0,
        )
        return PriorityOutcome(
            cluster_id=cluster.cluster_id,
            priority=priority,
            passes=passes_raw,
            status="fallback",
            reason="both passes failed to parse",
        )

    # If only one pass parsed, use its scores directly with pass count 2
    # and a zero delta (no comparison available). Flag as fallback so
    # a reviewer notices — the validation discipline did not run.
    if len(parsed_passes) == 1:
        dims = parsed_passes[0]["dimensions"]
        total = weighted_total(dims, weights)
        priority = PriorityScore(
            cluster_id=cluster.cluster_id,
            dimensions=dims,
            meta_weights=dict(weights),
            weighted_total=total,
            validation_passes=2,
            validation_delta=0.0,
        )
        return PriorityOutcome(
            cluster_id=cluster.cluster_id,
            priority=priority,
            passes=passes_raw,
            status="fallback",
            reason="only one of two passes parsed — no validation comparison",
        )

    # Two parsed passes — check if they drift beyond threshold.
    p_a_dims = parsed_passes[0]["dimensions"]
    p_b_dims = parsed_passes[1]["dimensions"]
    if _needs_third_pass(p_a_dims, p_b_dims):
        _log.info(
            "cluster %s: passes 1 and 2 drift > %d on some dimension — "
            "calling third pass",
            cluster.cluster_id,
            MAX_DIMENSION_DELTA,
        )
        try:
            p3 = await _score_one_pass(
                cluster, reconciled, client,
                model=model, skill_hash_value=skill_hash_value,
            )
            passes_raw.append({"pass": 3, "status": "parsed", "payload": p3})
            parsed_passes.append(p3)
        except PriorityParseError as e:
            passes_raw.append(
                {"pass": 3, "status": "fallback", "reason": str(e)}
            )
            _log.warning(
                "cluster %s: third pass failed to parse; "
                "aggregating on 2 passes",
                cluster.cluster_id,
            )

    # Aggregate.
    dim_lists = [pp["dimensions"] for pp in parsed_passes]
    aggregated, max_delta = _aggregate_passes(dim_lists)
    total = weighted_total(aggregated, weights)

    priority = PriorityScore(
        cluster_id=cluster.cluster_id,
        dimensions=aggregated,
        meta_weights=dict(weights),
        weighted_total=total,
        validation_passes=len(parsed_passes),
        validation_delta=max_delta,
    )
    return PriorityOutcome(
        cluster_id=cluster.cluster_id,
        priority=priority,
        passes=passes_raw,
        status="scored",
        reason=None,
    )


async def score_batch(
    clusters: list[InsightCluster],
    reconciled_by_cluster: dict[str, ReconciledVerdict],
    client: Client,
    *,
    model: str = MODEL,
    meta_weights: dict[str, float] | None = None,
    skill_hash_value: str | None = None,
) -> tuple[list[PriorityOutcome], list[tuple[str, Exception]]]:
    """Score a batch of clusters concurrently."""
    sh = skill_hash_value if skill_hash_value is not None else skill_hash()
    weights = meta_weights if meta_weights is not None else DEFAULT_META_WEIGHTS

    async def _one(
        c: InsightCluster,
    ) -> tuple[str, PriorityOutcome | Exception]:
        reconciled = reconciled_by_cluster.get(c.cluster_id)
        if reconciled is None:
            # No reconciled verdict for this cluster → fallback without call.
            _log.warning(
                "cluster %s has no reconciled verdict — skipping L6 scoring",
                c.cluster_id,
            )
            fallback_dims = {k: 0 for k in DIMENSION_KEYS}
            priority = PriorityScore(
                cluster_id=c.cluster_id,
                dimensions=fallback_dims,
                meta_weights=dict(weights),
                weighted_total=0.0,
                validation_passes=2,
                validation_delta=0.0,
            )
            return (
                c.cluster_id,
                PriorityOutcome(
                    cluster_id=c.cluster_id,
                    priority=priority,
                    passes=[],
                    status="fallback",
                    reason="no reconciled verdict in input",
                ),
            )
        try:
            outcome = await score_cluster(
                c, reconciled, client,
                model=model, meta_weights=weights,
                skill_hash_value=sh,
            )
            return (c.cluster_id, outcome)
        except Exception as e:  # noqa: BLE001
            return (c.cluster_id, e)

    results = await asyncio.gather(*(_one(c) for c in clusters))
    outcomes: list[PriorityOutcome] = []
    failures: list[tuple[str, Exception]] = []
    for cid, payload in results:
        if isinstance(payload, PriorityOutcome):
            outcomes.append(payload)
        else:
            failures.append((cid, payload))
    return outcomes, failures


def _sort_outcomes(outcomes: list[PriorityOutcome]) -> list[PriorityOutcome]:
    return sorted(outcomes, key=lambda o: o.cluster_id)


def _native_row(outcome: PriorityOutcome) -> dict[str, Any]:
    """One native sidecar row keyed by cluster_id, carrying the raw
    per-pass payloads."""
    return {
        "cluster_id": outcome.cluster_id,
        "status": outcome.status,
        "reason": outcome.reason,
        "passes": outcome.passes,
    }


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass
class _ProvenanceAccumulator:
    """Aggregates per-dim score distributions and validation metadata
    across a run. Used to surface scoring variance to reviewers."""

    dim_sum: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in DIMENSION_KEYS}
    )
    dim_min: dict[str, int] = field(
        default_factory=lambda: {k: 10 for k in DIMENSION_KEYS}
    )
    dim_max: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in DIMENSION_KEYS}
    )
    validation_pass_hist: dict[int, int] = field(
        default_factory=lambda: {2: 0, 3: 0}
    )
    validation_delta_hist: dict[int, int] = field(default_factory=dict)
    third_pass_triggered: int = 0
    weighted_totals: list[float] = field(default_factory=list)


def build_provenance(
    outcomes: list[PriorityOutcome],
    failures: list[tuple[str, Exception]],
    *,
    model: str,
    meta_weights: dict[str, float],
) -> dict[str, Any]:
    """Summarise an L6 run into the provenance payload."""
    scored = [o for o in outcomes if o.status == "scored"]
    fallback = [o for o in outcomes if o.status == "fallback"]

    acc = _ProvenanceAccumulator()
    for o in scored:
        p = o.priority
        for d in DIMENSION_KEYS:
            v = p.dimensions[d]
            acc.dim_sum[d] += v
            if v < acc.dim_min[d]:
                acc.dim_min[d] = v
            if v > acc.dim_max[d]:
                acc.dim_max[d] = v
        acc.validation_pass_hist[p.validation_passes] = (
            acc.validation_pass_hist.get(p.validation_passes, 0) + 1
        )
        delta = int(p.validation_delta)
        acc.validation_delta_hist[delta] = (
            acc.validation_delta_hist.get(delta, 0) + 1
        )
        if p.validation_passes == 3:
            acc.third_pass_triggered += 1
        acc.weighted_totals.append(p.weighted_total)

    n = len(scored)
    mean_weighted = (
        sum(acc.weighted_totals) / n if n > 0 else 0.0
    )
    mean_per_dim = (
        {d: acc.dim_sum[d] / n for d in DIMENSION_KEYS} if n > 0
        else {d: 0.0 for d in DIMENSION_KEYS}
    )

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "meta_weights": dict(meta_weights),
        "cluster_count": len(outcomes) + len(failures),
        "scored_count": n,
        "fallback_count": len(fallback),
        "transport_failure_count": len(failures),
        "third_pass_triggered": acc.third_pass_triggered,
        "validation_passes_histogram": {
            str(k): v for k, v in sorted(acc.validation_pass_hist.items())
        },
        "validation_delta_histogram": {
            str(k): v for k, v in sorted(acc.validation_delta_hist.items())
        },
        "dimension_score_mean": mean_per_dim,
        "dimension_score_min": dict(acc.dim_min) if n > 0 else {
            d: 0 for d in DIMENSION_KEYS
        },
        "dimension_score_max": dict(acc.dim_max) if n > 0 else {
            d: 0 for d in DIMENSION_KEYS
        },
        "weighted_total_mean": mean_weighted,
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
            "L6 priority-weight — scores each cluster's ReconciledVerdict "
            "on five priority dimensions (severity, reach, persistence, "
            "business_impact, cognitive_cost) via double-pass Claude "
            "scoring with optional third pass, then applies user "
            "meta-weights to compute a weighted priority total."
        ),
    )
    parser.add_argument(
        "--reconciled",
        type=Path,
        default=repo_root / DEFAULT_RECONCILED,
        help=f"L5 reconciled verdicts JSONL (default: {DEFAULT_RECONCILED}).",
    )
    parser.add_argument(
        "--clusters",
        type=Path,
        default=repo_root / DEFAULT_CLUSTERS,
        help=f"L3b labeled clusters JSONL (default: {DEFAULT_CLUSTERS}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / DEFAULT_VERDICTS,
        help=(
            f"L6 priority scores JSONL output (default: {DEFAULT_VERDICTS})."
        ),
    )
    parser.add_argument(
        "--native-output",
        type=Path,
        default=repo_root / DEFAULT_NATIVE,
        help=f"Native payload sidecar JSONL (default: {DEFAULT_NATIVE}).",
    )
    parser.add_argument(
        "--mode",
        choices=("live", "replay"),
        default="replay",
    )
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--replay-log",
        type=Path,
        default=repo_root / "data/cache/responses.jsonl",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--usd-ceiling", type=float, default=5.0)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    clusters = load_clusters(args.clusters)
    _log.info("loaded %d clusters from %s", len(clusters), args.clusters)
    if not clusters:
        _log.error("empty clusters input — nothing to score")
        return 1

    reconciled_by_cluster = load_reconciled_verdicts(args.reconciled)
    _log.info(
        "loaded %d reconciled verdicts from %s",
        len(reconciled_by_cluster),
        args.reconciled,
    )

    run_id = args.run_id or _default_run_id().replace("l4-", "l6-", 1)

    client = Client(
        mode=args.mode,
        run_id=run_id,
        replay_log_path=args.replay_log,
        usd_ceiling=args.usd_ceiling,
        concurrency=args.concurrency,
    )
    _log.info(
        "client mode=%s replay-log=%s cache_size=%d usd_ceiling=$%.2f",
        args.mode,
        args.replay_log,
        client.cache_size,
        args.usd_ceiling,
    )

    outcomes, failures = asyncio.run(
        score_batch(
            clusters,
            reconciled_by_cluster,
            client,
            model=args.model,
            meta_weights=DEFAULT_META_WEIGHTS,
        )
    )

    if failures:
        for cid, err in failures:
            _log.warning(
                "score transport failure for %s: %s: %s",
                cid,
                type(err).__name__,
                err,
            )

    sorted_outcomes = _sort_outcomes(outcomes)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    clusters_hash = hash_file(args.clusters)
    reconciled_hash = hash_file(args.reconciled)

    out_meta = write_jsonl_atomic(
        args.output,
        [o.priority.model_dump(mode="json") for o in sorted_outcomes],
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes={
            args.clusters.name: clusters_hash,
            args.reconciled.name: reconciled_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d priority scores to %s (sha256=%s…)",
        len(sorted_outcomes),
        args.output,
        out_meta.artifact_sha256[:16],
    )

    args.native_output.parent.mkdir(parents=True, exist_ok=True)
    native_meta = write_jsonl_atomic(
        args.native_output,
        [_native_row(o) for o in sorted_outcomes],
        run_id=run_id,
        layer=f"{LAYER_NAME}_native",
        input_hashes={
            args.clusters.name: clusters_hash,
            args.reconciled.name: reconciled_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d native passes to %s (sha256=%s…)",
        len(sorted_outcomes),
        args.native_output,
        native_meta.artifact_sha256[:16],
    )

    provenance_path = args.output.with_suffix(".provenance.json")
    provenance_payload = (
        json.dumps(
            build_provenance(
                outcomes, failures,
                model=args.model,
                meta_weights=DEFAULT_META_WEIGHTS,
            ),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(provenance_path, provenance_payload)
    _log.info("wrote L6 provenance to %s", provenance_path)

    scored_count = sum(1 for o in outcomes if o.status == "scored")
    fallback_count = sum(1 for o in outcomes if o.status == "fallback")
    _log.info(
        "L6 done. mode=%s live-spend=$%.4f scored=%d fallback=%d transport_fail=%d",
        args.mode,
        client.cumulative_usd,
        scored_count,
        fallback_count,
        len(failures),
    )

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
