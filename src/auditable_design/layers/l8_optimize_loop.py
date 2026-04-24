"""Layer 8 multi-round orchestrator — iterative refinement beyond
the thin spine.

The thin-spine :mod:`auditable_design.layers.l8_optimize` produces
two iterations per cluster: iteration 0 (baseline) and iteration 1
(re-audit of L7's proposed ``after_snapshot``). This module
continues the loop: iteration 2+ alternate

    design-tweak    → propose a minimal modification to the parent's
                      accepted ``after_snapshot`` focusing on
                      residual heuristics.
    design-optimize → re-audit the new snapshot against the same
                      baseline heuristic list.

An external verifier (:mod:`..evaluators.tchebycheff` or
:mod:`..evaluators.pareto`) decides whether each new iteration
replaces the parent. The loop terminates on:

* **Convergence** — Tchebycheff reports ``converged=True`` (parent
  is already all-zero), or the current sum of residual severities
  drops to :data:`CONVERGENCE_SEVERITY_THRESHOLD`.
* **Stall** — two consecutive iterations rejected.
* **Budget** — ``max_iterations`` reached (counted inclusive of the
  baseline + L7 iteration).

Input
-----
Reads the jsonl emitted by :mod:`l8_optimize`
(``l8_optimization_iterations_*.jsonl``): iteration 0 (baseline)
and iteration 1 (L7 re-audit). Uses the last **accepted** iteration
as the loop's starting parent.

Output
------
Appends iterations 2..N onto an ``*.loop.jsonl`` sidecar plus
corresponding ``artifacts/`` markdown and a ``.provenance.json``.
Never mutates the thin-spine input file.

Scope
-----
One verifier selected per run via ``--verifier``. A single cluster
per invocation (the smoke runs N clusters × M verifiers × K
models). No concurrent cluster batching — the loop is sequential by
nature, and the per-round Claude calls are few.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from auditable_design.claude_client import Client
from auditable_design.evaluators.pareto import (
    DEFAULT_MAX_REGRESSION,
    ParetoVerdict,
    verdict as pareto_verdict,
)
from auditable_design.evaluators.tchebycheff import (
    DEFAULT_MIN_IMPROVEMENT_PCT,
    TchebycheffVerdict,
    verdict as tchebycheff_verdict,
)
from auditable_design.layers.l4_audit import (
    AuditParseError,
    _atomic_write_bytes,
    _configure_logging,
    _default_run_id,
    _resolve_repo_root,
    load_clusters,
)
from auditable_design.layers.l8_optimize import (
    BASELINE_SKILL_ID,
    MAX_TOKENS as REAUDIT_MAX_TOKENS,
    MODEL as REAUDIT_MODEL,
    SKILL_ID as REAUDIT_SKILL_ID,
    SYSTEM_PROMPT as REAUDIT_SYSTEM_PROMPT,
    TEMPERATURE as REAUDIT_TEMPERATURE,
    VALID_ANCHORED_SEVERITIES,
    OptimizeParseError,
    _baseline_iteration_id,
    load_decisions,
    parse_optimize_response,
    reconciled_heuristic_list,
    skill_hash as reaudit_skill_hash,
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
from auditable_design.storage import read_jsonl, write_jsonl_atomic

__all__ = [
    "CONVERGENCE_SEVERITY_THRESHOLD",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_STALL_LIMIT",
    "LAYER_NAME",
    "LoopOutcome",
    "LoopVerdict",
    "TWEAK_MAX_TOKENS",
    "TWEAK_MODEL",
    "TWEAK_SKILL_ID",
    "TWEAK_SYSTEM_PROMPT",
    "TWEAK_TEMPERATURE",
    "TweakParseError",
    "VerifierName",
    "apply_verifier",
    "build_tweak_user_message",
    "main",
    "parse_tweak_response",
    "run_loop",
    "tweak_skill_hash",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LAYER_NAME: str = "l8_optimize_loop"

TWEAK_SKILL_ID: str = "design-tweak"
# Opus 4.7 per ADR-009 — iterative refinement is reasoning-heavy.
TWEAK_MODEL: str = "claude-opus-4-7"
TWEAK_TEMPERATURE: float = 0.0

# Output: new_snapshot up to ~300 words (~400 tokens) + two slug
# lists + reasoning ≤ 300 tokens ≈ 900 upper bound. 4096 leaves
# generous headroom.
TWEAK_MAX_TOKENS: int = 4096

# Loop budget — inclusive of baseline (iter 0) + L7 re-audit (iter 1),
# so with DEFAULT_MAX_ITERATIONS=5 the loop can add up to 3 more
# iterations (2, 3, 4). Matches the "~3h of work" budget scope.
DEFAULT_MAX_ITERATIONS: int = 5

# Two consecutive rejections → terminate (probable saturation).
DEFAULT_STALL_LIMIT: int = 2

# If the last accepted iteration's sum of severities drops to this
# value, the loop terminates even if the verifier could still find
# a smaller improvement — diminishing returns. Set low enough that
# every heuristic must be at severity 0 or 3.
CONVERGENCE_SEVERITY_THRESHOLD: int = 5

# Defaults mirror :mod:`l8_optimize`.
DEFAULT_RECONCILED = Path("data/derived/l5_reconciled_verdicts.jsonl")
DEFAULT_PRIORITY = Path("data/derived/l6_priority_scores.jsonl")
DEFAULT_DECISIONS = Path("data/derived/l7_design_decisions.jsonl")
DEFAULT_CLUSTERS = Path("data/derived/l3b_labeled_clusters.jsonl")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TweakParseError(AuditParseError):
    """Parse or validation failure on a design-tweak payload."""


# ---------------------------------------------------------------------------
# System prompt — loaded from design-tweak/SKILL.md
# ---------------------------------------------------------------------------


def _load_tweak_skill_body() -> str:
    repo_root = _resolve_repo_root()
    path = repo_root / "skills" / TWEAK_SKILL_ID / "SKILL.md"
    if not path.exists():
        raise RuntimeError(
            f"{LAYER_NAME}: SKILL.md not found at {path}; loop cannot initialise"
        )
    content = path.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            content = content[end + len("\n---\n") :]
    return content.strip()


TWEAK_SYSTEM_PROMPT: str = _load_tweak_skill_body()


def tweak_skill_hash() -> str:
    return hashlib.sha256(TWEAK_SYSTEM_PROMPT.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Verifier dispatch
# ---------------------------------------------------------------------------

VerifierName = Literal["pareto", "tchebycheff"]


@dataclass(frozen=True, slots=True)
class LoopVerdict:
    """Uniform view over Pareto or Tchebycheff verdicts.

    The loop orchestrator needs (a) accept/reject, (b) reason
    string, (c) regression count for provenance, (d) a convergence
    signal (Pareto has no such flag; Tchebycheff does). This shim
    normalises both.
    """

    verifier: VerifierName
    accepted: bool
    reason: str
    regression_count: int
    converged: bool
    delta_per_heuristic: dict[str, int]
    # Optional verifier-specific payload for provenance.
    raw: ParetoVerdict | TchebycheffVerdict


def apply_verifier(
    parent: dict[str, int],
    child: dict[str, int],
    verifier: VerifierName,
    *,
    max_regression: int = DEFAULT_MAX_REGRESSION,
    min_improvement_pct: float = DEFAULT_MIN_IMPROVEMENT_PCT,
) -> LoopVerdict:
    """Run the selected verifier and wrap the result as a LoopVerdict."""
    if verifier == "pareto":
        raw = pareto_verdict(parent, child, max_regression=max_regression)
        # Pareto has no native convergence concept; treat parent
        # all-zero as converged for loop-termination parity.
        converged = all(v == 0 for v in parent.values())
        return LoopVerdict(
            verifier="pareto",
            accepted=raw.accepted,
            reason=raw.reason,
            regression_count=raw.regression_count,
            converged=converged,
            delta_per_heuristic=dict(raw.delta_per_heuristic),
            raw=raw,
        )
    if verifier == "tchebycheff":
        raw = tchebycheff_verdict(
            parent, child, min_improvement_pct=min_improvement_pct
        )
        return LoopVerdict(
            verifier="tchebycheff",
            accepted=raw.accepted,
            reason=raw.reason,
            regression_count=raw.regression_count,
            converged=raw.converged,
            delta_per_heuristic=dict(raw.delta_per_heuristic),
            raw=raw,
        )
    raise ValueError(
        f"unknown verifier {verifier!r} — expected 'pareto' or 'tchebycheff'"
    )


# ---------------------------------------------------------------------------
# Tweak response parsing
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_TWEAK_TOP_LEVEL_KEYS = {
    "new_snapshot",
    "addresses_heuristics",
    "preserves_heuristics",
    "reasoning",
}

_MIN_SNAPSHOT_WORDS = 50  # SKILL.md advises 80-300; leave 30% slack
_MAX_SNAPSHOT_WORDS = 400


def parse_tweak_response(
    text: str,
    *,
    current_scores: dict[str, int],
) -> dict[str, Any]:
    """Extract and validate a design-tweak payload.

    Enforces that ``addresses_heuristics`` only references residual
    heuristics (``current_scores[h] > 0``) and
    ``preserves_heuristics`` only references resolved heuristics
    (``current_scores[h] == 0``). Both lists must come entirely
    from ``current_scores``' key set — no new slugs.
    """
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise TweakParseError(f"no JSON object found in response: {text!r}")
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise TweakParseError(
            f"malformed JSON: {err}; text={text!r}"
        ) from err
    if not isinstance(data, dict):
        raise TweakParseError(
            f"expected JSON object, got {type(data).__name__}"
        )

    actual = set(data.keys())
    missing = _TWEAK_TOP_LEVEL_KEYS - actual
    if missing:
        raise TweakParseError(
            f"missing required top-level keys: {sorted(missing)}"
        )
    extra = actual - _TWEAK_TOP_LEVEL_KEYS
    if extra:
        raise TweakParseError(
            f"unexpected top-level keys: {sorted(extra)}"
        )

    snapshot = data["new_snapshot"]
    if not isinstance(snapshot, str) or not snapshot.strip():
        raise TweakParseError("'new_snapshot' must be non-empty str")
    n_words = len(snapshot.split())
    if n_words < _MIN_SNAPSHOT_WORDS or n_words > _MAX_SNAPSHOT_WORDS:
        raise TweakParseError(
            f"new_snapshot length {n_words} words outside "
            f"[{_MIN_SNAPSHOT_WORDS}, {_MAX_SNAPSHOT_WORDS}]"
        )

    addresses = data["addresses_heuristics"]
    if not isinstance(addresses, list) or not addresses:
        raise TweakParseError(
            "'addresses_heuristics' must be non-empty list"
        )
    preserves = data["preserves_heuristics"]
    if not isinstance(preserves, list):
        raise TweakParseError("'preserves_heuristics' must be list")

    baseline_slugs = set(current_scores.keys())

    addr_set = set(addresses)
    pres_set = set(preserves)

    extra_addr = addr_set - baseline_slugs
    if extra_addr:
        raise TweakParseError(
            f"addresses_heuristics contains unknown slugs: {sorted(extra_addr)}"
        )
    extra_pres = pres_set - baseline_slugs
    if extra_pres:
        raise TweakParseError(
            f"preserves_heuristics contains unknown slugs: {sorted(extra_pres)}"
        )

    # Discipline contract: addresses must be residuals, preserves must be zeros.
    non_residual_addr = {h for h in addr_set if current_scores[h] == 0}
    if non_residual_addr:
        raise TweakParseError(
            "addresses_heuristics contains slugs already at severity 0 "
            f"(must address residuals only): {sorted(non_residual_addr)}"
        )
    non_zero_pres = {h for h in pres_set if current_scores[h] != 0}
    if non_zero_pres:
        raise TweakParseError(
            "preserves_heuristics contains slugs NOT at severity 0 "
            f"(must preserve resolved only): {sorted(non_zero_pres)}"
        )

    reasoning = data["reasoning"]
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise TweakParseError("'reasoning' must be non-empty str")

    return data


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_tweak_user_message(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    *,
    current_snapshot: str,
    current_scores: dict[str, int],
    verdict_reason: str,
) -> str:
    """Render the per-iteration user message for design-tweak."""
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
        f"<current_snapshot>{current_snapshot.translate(escape)}</current_snapshot>"
    )

    parts.append("<current_scores>")
    for h in sorted(current_scores.keys()):
        parts.append(f"  {h.translate(escape)}: {current_scores[h]}")
    parts.append("</current_scores>")

    parts.append("<baseline_heuristics>")
    for v in reconciled.ranked_violations:
        parts.append(
            f'  <h slug="{v.heuristic.translate(escape)}">'
            f"{v.violation.translate(escape)}"
            f"</h>"
        )
    parts.append("</baseline_heuristics>")

    parts.append(
        f"<verdict_reason>{verdict_reason.translate(escape)}</verdict_reason>"
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Re-audit prompt build (minimal variant over l8_optimize.build_user_message)
# ---------------------------------------------------------------------------


def build_reaudit_user_message(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    *,
    before_snapshot: str,
    after_snapshot: str,
) -> str:
    """Render the per-iteration user message for design-optimize
    re-audit when the snapshots come from a tweak cycle, not from
    L7's DesignDecision.
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
        f"<before_snapshot>{before_snapshot.translate(escape)}</before_snapshot>"
    )
    parts.append(
        f"<after_snapshot>{after_snapshot.translate(escape)}</after_snapshot>"
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
# Loop core
# ---------------------------------------------------------------------------


TerminationReason = Literal[
    "max_iterations",
    "stall",
    "converged",
    "severity_threshold",
    "tweak_parse_fail",
    "reaudit_parse_fail",
]


@dataclass(frozen=True, slots=True)
class LoopOutcome:
    """Aggregate outcome of one cluster's multi-round loop.

    ``new_iterations`` — iterations produced by this run (iter ≥ 2).
    Iteration 0 and 1 are inputs, not repeated here.

    ``termination_reason`` — why the loop stopped. One of:
    ``max_iterations``, ``stall``, ``converged``,
    ``severity_threshold``, ``tweak_parse_fail``,
    ``reaudit_parse_fail``.

    ``final_parent_id`` — id of the last accepted iteration (the
    "current best"); may be iter 1 if no new iteration was accepted.
    """

    cluster_id: str
    new_iterations: list[OptimizationIteration]
    verdicts: list[LoopVerdict]
    native_payloads: list[dict[str, Any]] = field(default_factory=list)
    termination_reason: TerminationReason = "max_iterations"
    final_parent_id: str = ""


def _read_existing_iterations(
    path: Path, cluster_id: str
) -> list[OptimizationIteration]:
    """Load existing iterations for a cluster from the thin-spine file."""
    if not path.exists():
        raise RuntimeError(
            f"{LAYER_NAME}: iterations input {path} not found — run "
            f"l8_optimize first"
        )
    rows = read_jsonl(path)
    out: list[OptimizationIteration] = []
    for row in rows:
        try:
            it = OptimizationIteration.model_validate(row)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"{LAYER_NAME}: row in {path} is not a valid "
                f"OptimizationIteration: {e}"
            ) from e
        # Filter to this cluster. IterationIds have the shape
        # iteration__{cluster_id}__{index:02d}; robust split.
        if _cluster_id_of(it) == cluster_id:
            out.append(it)
    if len(out) < 2:
        raise RuntimeError(
            f"{LAYER_NAME}: need at least 2 iterations for cluster "
            f"{cluster_id} in {path}; found {len(out)}"
        )
    out.sort(key=lambda it: it.iteration_index)
    return out


def _cluster_id_of(it: OptimizationIteration) -> str:
    parts = it.iteration_id.split("__")
    if len(parts) < 3:
        return ""
    return "__".join(parts[1:-1])


def _severity_sum(scores: dict[str, dict[str, int]]) -> int:
    """Sum all severities across all skill buckets."""
    total = 0
    for bucket in scores.values():
        total += sum(bucket.values())
    return total


def _flat_scores(scores: dict[str, dict[str, int]]) -> dict[str, int]:
    """Flatten nested scores dict (single-bucket convention)."""
    if BASELINE_SKILL_ID in scores:
        return dict(scores[BASELINE_SKILL_ID])
    # Fallback: collapse all buckets.
    flat: dict[str, int] = {}
    for bucket in scores.values():
        for h, s in bucket.items():
            flat[h] = max(flat.get(h, 0), s)
    return flat


async def run_loop(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    decision: DesignDecision,
    priority: PriorityScore,
    existing_iterations: list[OptimizationIteration],
    client: Client,
    *,
    verifier: VerifierName,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    stall_limit: int = DEFAULT_STALL_LIMIT,
    severity_threshold: int = CONVERGENCE_SEVERITY_THRESHOLD,
    max_regression: int = DEFAULT_MAX_REGRESSION,
    min_improvement_pct: float = DEFAULT_MIN_IMPROVEMENT_PCT,
    tweak_model: str = TWEAK_MODEL,
    reaudit_model: str = REAUDIT_MODEL,
    tweak_skill_hash_value: str | None = None,
    reaudit_skill_hash_value: str | None = None,
    run_id: str,
    artifacts_dir: Path,
) -> LoopOutcome:
    """Drive the multi-round loop for one cluster.

    ``existing_iterations`` comes from the thin-spine jsonl (iter 0 +
    iter 1). The loop appends iter 2+ until a termination condition
    is hit.
    """
    t_hash = tweak_skill_hash_value or tweak_skill_hash()
    r_hash = reaudit_skill_hash_value or reaudit_skill_hash()

    # Determine starting parent: the most recent accepted iteration.
    parent = _latest_accepted(existing_iterations)
    if parent is None:
        # No accepted proposed iteration — baseline is still the best
        # and there's nothing to tweak from.
        parent = existing_iterations[0]
    parent_snapshot = _snapshot_of(parent, decision)

    new_iters: list[OptimizationIteration] = []
    verdicts: list[LoopVerdict] = []
    native_payloads: list[dict[str, Any]] = []
    consecutive_rejects = 0
    next_index = (
        max(it.iteration_index for it in existing_iterations) + 1
    )

    termination: TerminationReason = "max_iterations"

    while next_index < max_iterations:
        parent_scores = _flat_scores(parent.scores)

        # Pre-loop convergence check — parent is already all-zero;
        # no residual to tweak, and the tweak parser would reject any
        # addresses_heuristics list as "already at severity 0" anyway.
        # Cleaner termination than bouncing off a parse failure.
        if parent_scores and all(v == 0 for v in parent_scores.values()):
            termination = "converged"
            break

        if _severity_sum(parent.scores) <= severity_threshold:
            termination = "severity_threshold"
            break

        # Build and execute design-tweak call.
        parent_verdict_reason = (
            parent.regression_reason
            or parent.reasoning
            or "Continuing refinement."
        )
        tweak_user = build_tweak_user_message(
            cluster,
            reconciled,
            current_snapshot=parent_snapshot,
            current_scores=parent_scores,
            verdict_reason=parent_verdict_reason,
        )
        tweak_resp = await client.call(
            system=TWEAK_SYSTEM_PROMPT,
            user=tweak_user,
            model=tweak_model,
            skill_id=TWEAK_SKILL_ID,
            skill_hash=t_hash,
            temperature=TWEAK_TEMPERATURE,
            max_tokens=TWEAK_MAX_TOKENS,
        )
        try:
            tweak_payload = parse_tweak_response(
                tweak_resp.response, current_scores=parent_scores
            )
        except TweakParseError as e:
            _log.warning(
                "tweak parse failed iter %d for %s: %s — halting loop",
                next_index,
                cluster.cluster_id,
                e,
            )
            termination = "tweak_parse_fail"
            break

        new_snapshot = str(tweak_payload["new_snapshot"])

        # Build and execute design-optimize re-audit call.
        reaudit_user = build_reaudit_user_message(
            cluster,
            reconciled,
            before_snapshot=parent_snapshot,
            after_snapshot=new_snapshot,
        )
        reaudit_resp = await client.call(
            system=REAUDIT_SYSTEM_PROMPT,
            user=reaudit_user,
            model=reaudit_model,
            skill_id=REAUDIT_SKILL_ID,
            skill_hash=r_hash,
            temperature=REAUDIT_TEMPERATURE,
            max_tokens=REAUDIT_MAX_TOKENS,
        )
        baseline_heuristics = reconciled_heuristic_list(reconciled)
        try:
            reaudit_payload = parse_optimize_response(
                reaudit_resp.response,
                baseline_heuristics=baseline_heuristics,
            )
        except OptimizeParseError as e:
            _log.warning(
                "reaudit parse failed iter %d for %s: %s — halting loop",
                next_index,
                cluster.cluster_id,
                e,
            )
            termination = "reaudit_parse_fail"
            break

        proposed_scores = dict(reaudit_payload["scored_heuristics"])
        reasoning_text = str(reaudit_payload["reasoning"])

        # Apply verifier.
        v = apply_verifier(
            parent=parent_scores,
            child=proposed_scores,
            verifier=verifier,
            max_regression=max_regression,
            min_improvement_pct=min_improvement_pct,
        )
        verdicts.append(v)

        # Write artifact for this iteration.
        artifact = _write_iteration_artifact(
            cluster_id=cluster.cluster_id,
            iteration_index=next_index,
            parent_iteration_id=parent.iteration_id,
            parent_snapshot=parent_snapshot,
            new_snapshot=new_snapshot,
            tweak_addresses=list(tweak_payload["addresses_heuristics"]),
            tweak_preserves=list(tweak_payload["preserves_heuristics"]),
            tweak_reasoning=str(tweak_payload["reasoning"]),
            baseline_heuristics=baseline_heuristics,
            parent_scores=parent_scores,
            proposed_scores=proposed_scores,
            reaudit_reasoning=reasoning_text,
            verdict=v,
            artifacts_dir=artifacts_dir,
        )

        new_iter = OptimizationIteration(
            iteration_id=_child_iteration_id(
                cluster.cluster_id, next_index
            ),
            run_id=run_id,
            iteration_index=next_index,
            parent_iteration_id=parent.iteration_id,
            design_artifact_ref=str(artifact),
            scores={BASELINE_SKILL_ID: proposed_scores},
            reasoning=reasoning_text,
            accepted=v.accepted,
            regression_reason=v.reason if not v.accepted else None,
            delta_per_heuristic=dict(v.delta_per_heuristic),
            informing_review_ids=list(cluster.member_review_ids),
            recorded_at=datetime.now(UTC),
        )
        new_iters.append(new_iter)
        native_payloads.append(
            {
                "iteration_index": next_index,
                "tweak_payload": tweak_payload,
                "reaudit_payload": reaudit_payload,
                "verifier": verifier,
                "verdict_reason": v.reason,
            }
        )

        if v.accepted:
            consecutive_rejects = 0
            parent = new_iter
            parent_snapshot = new_snapshot
        else:
            consecutive_rejects += 1

        if v.converged:
            termination = "converged"
            next_index += 1
            break
        if consecutive_rejects >= stall_limit:
            termination = "stall"
            next_index += 1
            break

        next_index += 1

    final_parent = _latest_accepted(list(existing_iterations) + new_iters)
    if final_parent is None:
        final_parent = existing_iterations[0]

    return LoopOutcome(
        cluster_id=cluster.cluster_id,
        new_iterations=new_iters,
        verdicts=verdicts,
        native_payloads=native_payloads,
        termination_reason=termination,
        final_parent_id=final_parent.iteration_id,
    )


def _latest_accepted(
    iterations: list[OptimizationIteration],
) -> OptimizationIteration | None:
    for it in reversed(iterations):
        if it.accepted:
            return it
    return None


def _snapshot_of(
    iteration: OptimizationIteration, decision: DesignDecision
) -> str:
    """Best-effort snapshot recovery for an iteration.

    For iter 0 and iter 1 we pull from the L7 DesignDecision
    (before_ and after_snapshot respectively). For iter 2+ the
    snapshot lives inside the artifact markdown written by this
    module; read it back.
    """
    if iteration.iteration_index == 0:
        return decision.before_snapshot
    if iteration.iteration_index == 1:
        return decision.after_snapshot
    # Iter 2+ — pull from artifact file.
    artifact_path = Path(iteration.design_artifact_ref)
    if not artifact_path.exists():
        raise RuntimeError(
            f"{LAYER_NAME}: artifact {artifact_path} missing for "
            f"iteration {iteration.iteration_id}; cannot resume loop"
        )
    body = artifact_path.read_text(encoding="utf-8")
    # Artifacts written below use a "## new_snapshot" section.
    marker = "## new_snapshot\n"
    idx = body.find(marker)
    if idx < 0:
        raise RuntimeError(
            f"{LAYER_NAME}: artifact {artifact_path} has no "
            f"'## new_snapshot' section"
        )
    after = body[idx + len(marker) :]
    # Truncate at the next h2.
    end = after.find("\n## ")
    return (after if end < 0 else after[:end]).strip()


def _child_iteration_id(cluster_id: str, index: int) -> str:
    return f"iteration__{cluster_id}__{index:02d}"


def _write_iteration_artifact(
    *,
    cluster_id: str,
    iteration_index: int,
    parent_iteration_id: str,
    parent_snapshot: str,
    new_snapshot: str,
    tweak_addresses: list[str],
    tweak_preserves: list[str],
    tweak_reasoning: str,
    baseline_heuristics: list[str],
    parent_scores: dict[str, int],
    proposed_scores: dict[str, int],
    reaudit_reasoning: str,
    verdict: LoopVerdict,
    artifacts_dir: Path,
) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / f"{cluster_id}_iter{iteration_index:02d}.md"

    body = (
        f"# {cluster_id} — iteration {iteration_index} "
        f"({'accepted' if verdict.accepted else 'rejected'})\n\n"
        f"## Parent iteration\n{parent_iteration_id}\n\n"
        f"## Verifier\n{verdict.verifier}\n\n"
        f"## parent_snapshot\n{parent_snapshot}\n\n"
        f"## new_snapshot\n{new_snapshot}\n\n"
        f"## design-tweak addresses\n"
    )
    for h in tweak_addresses:
        body += f"- `{h}`\n"
    body += "\n## design-tweak preserves\n"
    for h in tweak_preserves:
        body += f"- `{h}`\n"
    body += f"\n## design-tweak reasoning\n{tweak_reasoning}\n\n"
    body += "## Re-audit severities\n"
    for h in baseline_heuristics:
        body += (
            f"- `{h}` — parent {parent_scores[h]} → child "
            f"{proposed_scores[h]} "
            f"(delta {verdict.delta_per_heuristic.get(h, 0):+d})\n"
        )
    body += f"\n## Re-audit reasoning\n{reaudit_reasoning}\n\n"
    body += f"## Verifier verdict\n{verdict.reason}\n"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def build_provenance(
    outcome: LoopOutcome,
    *,
    verifier: VerifierName,
    tweak_model: str,
    reaudit_model: str,
    tweak_skill_id: str,
    reaudit_skill_id: str,
    tweak_skill_hash_value: str,
    reaudit_skill_hash_value: str,
    run_id: str,
    max_iterations: int,
    stall_limit: int,
    severity_threshold: int,
    max_regression: int,
    min_improvement_pct: float,
) -> dict[str, Any]:
    accepted = sum(1 for it in outcome.new_iterations if it.accepted)
    rejected = sum(1 for it in outcome.new_iterations if not it.accepted)
    return {
        "schema_version": SCHEMA_VERSION,
        "layer": LAYER_NAME,
        "run_id": run_id,
        "cluster_id": outcome.cluster_id,
        "verifier": verifier,
        "new_iteration_count": len(outcome.new_iterations),
        "accepted_count": accepted,
        "rejected_count": rejected,
        "termination_reason": outcome.termination_reason,
        "final_parent_id": outcome.final_parent_id,
        "max_iterations": max_iterations,
        "stall_limit": stall_limit,
        "severity_threshold": severity_threshold,
        "max_regression": max_regression,
        "min_improvement_pct": min_improvement_pct,
        "tweak_model": tweak_model,
        "reaudit_model": reaudit_model,
        "tweak_skill_id": tweak_skill_id,
        "reaudit_skill_id": reaudit_skill_id,
        "tweak_skill_hash": tweak_skill_hash_value,
        "reaudit_skill_hash": reaudit_skill_hash_value,
        "recorded_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=LAYER_NAME,
        description=(
            "Multi-round L8 optimization orchestrator for one cluster. "
            "Requires a prior l8_optimize run as input."
        ),
    )
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument(
        "--iterations-input", type=Path, required=True,
        help="l8_optimization_iterations_*.jsonl from the thin-spine run",
    )
    parser.add_argument(
        "--iterations-output", type=Path, required=True,
        help="Output jsonl (iter 2+) — sidecar to the thin-spine file",
    )
    parser.add_argument(
        "--native-output", type=Path, required=True,
        help="Output jsonl for raw payloads per iteration",
    )
    parser.add_argument(
        "--provenance-output", type=Path, required=True,
    )
    parser.add_argument(
        "--artifacts-dir", type=Path, required=True,
    )
    parser.add_argument(
        "--verifier",
        choices=["pareto", "tchebycheff"],
        default="tchebycheff",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS
    )
    parser.add_argument(
        "--stall-limit", type=int, default=DEFAULT_STALL_LIMIT
    )
    parser.add_argument(
        "--severity-threshold", type=int,
        default=CONVERGENCE_SEVERITY_THRESHOLD,
    )
    parser.add_argument(
        "--max-regression", type=int, default=DEFAULT_MAX_REGRESSION
    )
    parser.add_argument(
        "--min-improvement-pct",
        type=float,
        default=DEFAULT_MIN_IMPROVEMENT_PCT,
    )
    parser.add_argument("--tweak-model", default=TWEAK_MODEL)
    parser.add_argument("--reaudit-model", default=REAUDIT_MODEL)
    parser.add_argument(
        "--reconciled", type=Path, default=DEFAULT_RECONCILED
    )
    parser.add_argument(
        "--priority", type=Path, default=DEFAULT_PRIORITY
    )
    parser.add_argument(
        "--decisions", type=Path, default=DEFAULT_DECISIONS
    )
    parser.add_argument(
        "--clusters", type=Path, default=DEFAULT_CLUSTERS
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--mode",
        choices=("live", "replay"),
        default="replay",
        help="Claude client mode (default: replay — reviewer-safe).",
    )
    parser.add_argument(
        "--replay-log",
        type=Path,
        default=Path("data/cache/responses.jsonl"),
        help="Path to the Claude replay log.",
    )
    parser.add_argument(
        "--usd-ceiling",
        type=float,
        default=10.0,
        help="Per-run USD kill-switch ceiling (live mode only).",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    run_id = args.run_id or _default_run_id()
    _log.info("starting %s run %s cluster=%s verifier=%s",
              LAYER_NAME, run_id, args.cluster_id, args.verifier)

    # Load inputs.
    clusters = {c.cluster_id: c for c in load_clusters(args.clusters)}
    if args.cluster_id not in clusters:
        _log.error("cluster %s not found in %s",
                   args.cluster_id, args.clusters)
        return 2
    cluster = clusters[args.cluster_id]

    reconciled_by_cluster = load_reconciled_verdicts(args.reconciled)
    if args.cluster_id not in reconciled_by_cluster:
        _log.error("no reconciled verdict for %s", args.cluster_id)
        return 2
    reconciled = reconciled_by_cluster[args.cluster_id]

    priority_by_cluster = load_priority_scores(args.priority)
    priority = priority_by_cluster.get(args.cluster_id)
    if priority is None:
        _log.error("no priority score for %s", args.cluster_id)
        return 2

    decision_by_cluster = load_decisions(args.decisions)
    decision = decision_by_cluster.get(args.cluster_id)
    if decision is None:
        _log.error("no decision for %s", args.cluster_id)
        return 2

    existing = _read_existing_iterations(
        args.iterations_input, args.cluster_id
    )

    client = Client(
        mode=args.mode,
        run_id=run_id,
        replay_log_path=args.replay_log,
        usd_ceiling=args.usd_ceiling,
        concurrency=args.concurrency,
    )
    t_hash = tweak_skill_hash()
    r_hash = reaudit_skill_hash()

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
            artifacts_dir=args.artifacts_dir,
        )
    )

    # Write iter 2+ only (thin-spine input is untouched).
    write_jsonl_atomic(
        args.iterations_output,
        [it.model_dump(mode="json") for it in outcome.new_iterations],
        run_id=run_id,
        layer=LAYER_NAME,
    )
    write_jsonl_atomic(
        args.native_output,
        outcome.native_payloads,
        run_id=run_id,
        layer=LAYER_NAME,
    )

    provenance = build_provenance(
        outcome,
        verifier=args.verifier,
        tweak_model=args.tweak_model,
        reaudit_model=args.reaudit_model,
        tweak_skill_id=TWEAK_SKILL_ID,
        reaudit_skill_id=REAUDIT_SKILL_ID,
        tweak_skill_hash_value=t_hash,
        reaudit_skill_hash_value=r_hash,
        run_id=run_id,
        max_iterations=args.max_iterations,
        stall_limit=args.stall_limit,
        severity_threshold=args.severity_threshold,
        max_regression=args.max_regression,
        min_improvement_pct=args.min_improvement_pct,
    )
    _atomic_write_bytes(
        args.provenance_output,
        (json.dumps(provenance, indent=2) + "\n").encode("utf-8"),
    )
    _log.info(
        "loop done cluster=%s termination=%s new=%d accepted=%d",
        outcome.cluster_id,
        outcome.termination_reason,
        len(outcome.new_iterations),
        sum(1 for it in outcome.new_iterations if it.accepted),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
