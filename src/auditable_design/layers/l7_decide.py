"""Layer 7 — design decisions from reconciled + prioritised clusters.

L5 reconciles; L6 prioritises; L7 **decides**. Generates one
:class:`DesignPrinciple` and one :class:`DesignDecision` per cluster
from the ReconciledVerdict + PriorityScore + cluster context. The
principle is a re-usable operational constraint (quotable in design
critiques across surfaces); the decision is a concrete before/after
change on the specific surface this cluster is about.

One call per cluster, single pass
---------------------------------
Unlike L6's double-pass judgment discipline, L7 is a generation task:
the model proposes a principle + decision, grounded in the input
evidence. A second invocation would produce a second legitimate
reading rather than a validation of the first; double-pass would
conflict with the skill's "editorial voice" nature. If drift across
models matters for production, L7 runs a matched-model eval (ADR-009
L7 pilot) and documents the spread.

Traceability cross-validation
-----------------------------
The parser enforces two reverse-lookups:

* ``principle.derived_from_review_ids`` must be a subset of the
  cluster's ``member_review_ids``. A principle cited as derived from
  review_ids not in the cluster is a hallucinated citation — parser
  falls back.
* ``decision.resolves_heuristics`` must be a subset of the
  ReconciledVerdict's ``ranked_violations[*].heuristic``. A decision
  that resolves a heuristic the reconciled verdict did not surface
  is a made-up audit trail — parser falls back.

Both lists are required non-empty (schema and SKILL.md discipline).

Input / output
--------------
* Reads L5 ``l5_reconciled_verdicts.jsonl`` and L6
  ``l6_priority_scores.jsonl`` (joined by cluster_id) plus the L3b
  ``l3b_labeled_clusters.jsonl`` for cluster context.
* Writes :data:`DEFAULT_PRINCIPLES` — one :class:`DesignPrinciple`
  per reconciled+prioritised cluster.
* Writes :data:`DEFAULT_DECISIONS` — one :class:`DesignDecision` per
  principle (1:1 cardinality at thin-spine).
* Writes :data:`DEFAULT_NATIVE` with the raw skill payloads keyed by
  cluster_id.
* Writes a ``.provenance.json`` sidecar with principle-name
  uniqueness checks, heuristic-resolution distribution, review-id
  citation distribution, fallback reasons.

Model default
-------------
Opus 4.7 per ADR-009: L7 is generative-reasoning-heavy (design
principle extraction from multi-skill corroboration + tension
resolution), low-volume (one principle+decision per cluster).

Fallback discipline
-------------------
Parse failure or cross-reference violation → fallback records with
empty string defaults preserved in the native sidecar's raw response.
Transport errors still propagate.
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
from auditable_design.layers.l4_audit import (
    AuditParseError,
    _atomic_write_bytes,
    _configure_logging,
    _default_run_id,
    _resolve_repo_root,
    load_clusters,
)
from auditable_design.layers.l5_reconcile import load_verdicts_bundle as _reserved_unused  # noqa: F401
from auditable_design.layers.l6_weight import (
    load_reconciled_verdicts,
)
from auditable_design.schemas import (
    SCHEMA_VERSION,
    DesignDecision,
    DesignPrinciple,
    InsightCluster,
    PriorityScore,
    ReconciledVerdict,
)
from auditable_design.storage import hash_file, read_jsonl, write_jsonl_atomic

__all__ = [
    "DEFAULT_CLUSTERS",
    "DEFAULT_DECISIONS",
    "DEFAULT_NATIVE",
    "DEFAULT_PRINCIPLES",
    "DEFAULT_PRIORITY",
    "DEFAULT_RECONCILED",
    "LAYER_NAME",
    "MAX_TOKENS",
    "MODEL",
    "SKILL_ID",
    "SYSTEM_PROMPT",
    "TEMPERATURE",
    "DecideOutcome",
    "DecideParseError",
    "build_provenance",
    "build_user_message",
    "decide_batch",
    "decide_cluster",
    "load_priority_scores",
    "main",
    "parse_decide_response",
    "skill_hash",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_ID: str = "design-decide"
LAYER_NAME: str = "l7_decide"

# Opus 4.7 per ADR-009: L7 is generative, reasoning-heavy, low-volume.
MODEL: str = "claude-opus-4-7"
TEMPERATURE: float = 0.0

# Per-cluster output: principle (name + statement + 3–7 review_ids) +
# decision (description + before_snapshot + after_snapshot + 2–4
# heuristic slugs) ≈ 500–800 tokens. 4096 leaves 5× headroom.
MAX_TOKENS: int = 4096

# Default paths.
DEFAULT_RECONCILED = Path("data/derived/l5_reconciled_verdicts.jsonl")
DEFAULT_PRIORITY = Path("data/derived/l6_priority_scores.jsonl")
DEFAULT_CLUSTERS = Path("data/derived/l3b_labeled_clusters.jsonl")
DEFAULT_PRINCIPLES = Path("data/derived/l7_design_principles.jsonl")
DEFAULT_DECISIONS = Path("data/derived/l7_design_decisions.jsonl")
DEFAULT_NATIVE = Path("data/derived/l7_design_decisions.native.jsonl")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DecideParseError(AuditParseError):
    """Parse / cross-reference failure specific to the L7 decide payload."""


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


def load_priority_scores(path: Path) -> dict[str, PriorityScore]:
    """Load L6 output as a dict keyed by cluster_id."""
    rows = read_jsonl(path)
    result: dict[str, PriorityScore] = {}
    for i, row in enumerate(rows):
        try:
            ps = PriorityScore.model_validate(row)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"{LAYER_NAME}: row {i} of {path} is not a valid "
                f"PriorityScore: {e}"
            ) from e
        if ps.cluster_id in result:
            _log.warning(
                "duplicate priority score for cluster=%s — later row wins",
                ps.cluster_id,
            )
        result[ps.cluster_id] = ps
    return result


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_user_message(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    priority: PriorityScore,
) -> str:
    """Render the per-cluster user message for design-decide.

    Envelope threads:

    * ``<cluster>`` — label, member_review_ids (SPACE-SEPARATED for the
      model's citation selection), ui_context / html / screenshot_ref,
      representative_quotes.
    * ``<reconciled_verdict>`` — ranked_violations (heuristic + severity +
      reasoning), tensions (skill_a × skill_b @ axis with resolution).
    * ``<priority_score>`` — dimensions + weighted_total (model does not
      see meta_weights — the PriorityScore's weights are persisted via
      L6 but are a user-layer concern irrelevant to decision generation).

    All text content XML-escaped; ``html`` CDATA-wrapped.
    """
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})

    parts: list[str] = [
        "<cluster>",
        f"  <cluster_id>{cluster.cluster_id.translate(escape)}</cluster_id>",
        f"  <label>{cluster.label.translate(escape)}</label>",
        # Render member_review_ids as a space-separated list so the
        # model can scan and cite specific ones.
        f"  <member_review_ids>{' '.join(cluster.member_review_ids).translate(escape)}</member_review_ids>",
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
    parts.append("  <ranked_violations>")
    for i, v in enumerate(reconciled.ranked_violations):
        parts.append(
            f'    <entry idx="{i}" heuristic="{v.heuristic.translate(escape)}" '
            f'severity="{v.severity}">'
            f"{v.violation.translate(escape)} "
            f"[reasoning: {v.reasoning.translate(escape)}]"
            f"</entry>"
        )
    parts.append("  </ranked_violations>")
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

    parts.append("<priority_score>")
    parts.append(
        f"  <dimensions>"
        f"severity={priority.dimensions['severity']} "
        f"reach={priority.dimensions['reach']} "
        f"persistence={priority.dimensions['persistence']} "
        f"business_impact={priority.dimensions['business_impact']} "
        f"cognitive_cost={priority.dimensions['cognitive_cost']}"
        f"</dimensions>"
    )
    parts.append(f"  <weighted_total>{priority.weighted_total:.2f}</weighted_total>")
    parts.append("</priority_score>")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_TOP_LEVEL_KEYS = {"principle", "decision"}
_PRINCIPLE_KEYS = {"name", "statement", "derived_from_review_ids"}
_DECISION_KEYS = {
    "description",
    "before_snapshot",
    "after_snapshot",
    "resolves_heuristics",
}


def parse_decide_response(
    text: str,
    *,
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
) -> dict[str, Any]:
    """Extract and validate the L7 decide payload.

    Cross-validates:

    * ``principle.derived_from_review_ids`` ⊆ ``cluster.member_review_ids``
    * ``decision.resolves_heuristics`` ⊆ reconciled ``ranked_violations[*].heuristic``
    """
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise DecideParseError(f"no JSON object found in response: {text!r}")
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise DecideParseError(
            f"malformed JSON: {err}; text={text!r}"
        ) from err
    if not isinstance(data, dict):
        raise DecideParseError(
            f"expected JSON object, got {type(data).__name__}"
        )

    actual = set(data.keys())
    missing = _TOP_LEVEL_KEYS - actual
    if missing:
        raise DecideParseError(
            f"missing required top-level keys: {sorted(missing)}"
        )
    extra = actual - _TOP_LEVEL_KEYS
    if extra:
        raise DecideParseError(f"unexpected top-level keys: {sorted(extra)}")

    _validate_principle(data["principle"], cluster=cluster)
    _validate_decision(data["decision"], reconciled=reconciled)

    return data


def _validate_principle(
    principle: Any,
    *,
    cluster: InsightCluster,
) -> None:
    if not isinstance(principle, dict):
        raise DecideParseError(
            f"'principle' must be dict, got {type(principle).__name__}"
        )
    missing = _PRINCIPLE_KEYS - set(principle.keys())
    if missing:
        raise DecideParseError(
            f"principle missing keys: {sorted(missing)}"
        )
    extra = set(principle.keys()) - _PRINCIPLE_KEYS
    if extra:
        raise DecideParseError(
            f"principle has unexpected keys: {sorted(extra)}"
        )
    for str_key in ("name", "statement"):
        v = principle[str_key]
        if not isinstance(v, str) or not v.strip():
            raise DecideParseError(
                f"principle.{str_key} must be non-empty str"
            )

    review_ids = principle["derived_from_review_ids"]
    if not isinstance(review_ids, list) or not review_ids:
        raise DecideParseError(
            "principle.derived_from_review_ids must be non-empty list"
        )
    allowed = set(cluster.member_review_ids)
    for i, rid in enumerate(review_ids):
        if not isinstance(rid, str) or not rid.strip():
            raise DecideParseError(
                f"principle.derived_from_review_ids[{i}] must be non-empty str"
            )
        if rid not in allowed:
            raise DecideParseError(
                f"principle.derived_from_review_ids[{i}]={rid!r} not in "
                f"cluster.member_review_ids (size={len(allowed)}) — "
                f"hallucinated citation"
            )


def _validate_decision(
    decision: Any,
    *,
    reconciled: ReconciledVerdict,
) -> None:
    if not isinstance(decision, dict):
        raise DecideParseError(
            f"'decision' must be dict, got {type(decision).__name__}"
        )
    missing = _DECISION_KEYS - set(decision.keys())
    if missing:
        raise DecideParseError(f"decision missing keys: {sorted(missing)}")
    extra = set(decision.keys()) - _DECISION_KEYS
    if extra:
        raise DecideParseError(
            f"decision has unexpected keys: {sorted(extra)}"
        )
    for str_key in ("description", "before_snapshot", "after_snapshot"):
        v = decision[str_key]
        if not isinstance(v, str) or not v.strip():
            raise DecideParseError(
                f"decision.{str_key} must be non-empty str"
            )

    heuristics = decision["resolves_heuristics"]
    if not isinstance(heuristics, list) or not heuristics:
        raise DecideParseError(
            "decision.resolves_heuristics must be non-empty list"
        )
    allowed = {v.heuristic for v in reconciled.ranked_violations}
    for i, h in enumerate(heuristics):
        if not isinstance(h, str) or not h.strip():
            raise DecideParseError(
                f"decision.resolves_heuristics[{i}] must be non-empty str"
            )
        if h not in allowed:
            raise DecideParseError(
                f"decision.resolves_heuristics[{i}]={h!r} not in "
                f"reconciled.ranked_violations heuristics ({sorted(allowed)}) — "
                f"invented slug"
            )


# ---------------------------------------------------------------------------
# Outcome construction
# ---------------------------------------------------------------------------


def _principle_id(cluster_id: str) -> str:
    return f"principle__{cluster_id}"


def _decision_id(cluster_id: str, idx: int = 1) -> str:
    """Thin-spine: one decision per cluster, suffix 1 reserved so
    future multi-decision runs can increment without colliding."""
    return f"decision__{cluster_id}__{idx}"


def _build_principle(
    payload: dict[str, Any],
    cluster_id: str,
) -> DesignPrinciple:
    p = payload["principle"]
    return DesignPrinciple(
        principle_id=_principle_id(cluster_id),
        cluster_id=cluster_id,
        name=p["name"],
        statement=p["statement"],
        derived_from_review_ids=list(p["derived_from_review_ids"]),
    )


def _build_decision(
    payload: dict[str, Any],
    cluster_id: str,
) -> DesignDecision:
    d = payload["decision"]
    return DesignDecision(
        decision_id=_decision_id(cluster_id),
        principle_id=_principle_id(cluster_id),
        description=d["description"],
        before_snapshot=d["before_snapshot"],
        after_snapshot=d["after_snapshot"],
        resolves_heuristics=list(d["resolves_heuristics"]),
    )


# ---------------------------------------------------------------------------
# Per-cluster pipeline
# ---------------------------------------------------------------------------


DecideStatus = Literal["decided", "fallback"]


@dataclass(frozen=True, slots=True)
class DecideOutcome:
    """One cluster's decide result.

    On success, ``principle`` and ``decision`` are populated; ``status``
    is "decided". On parse or cross-reference failure, both artefacts
    are None (caller must skip them when writing) and ``status`` is
    "fallback" with ``reason`` set.

    ``native_payload`` always carries the raw skill response dict (on
    success: the parsed payload; on fallback: a {fallback, reason,
    raw_response} dict mirroring L4/L5 conventions).
    """

    cluster_id: str
    principle: DesignPrinciple | None
    decision: DesignDecision | None
    native_payload: dict[str, Any]
    status: DecideStatus
    reason: str | None = None


async def decide_cluster(
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    priority: PriorityScore,
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str,
) -> DecideOutcome:
    """Generate one principle + one decision for one cluster.

    Single-pass; parse failure yields a fallback outcome (no second
    attempt). Transport errors propagate.
    """
    user = build_user_message(cluster, reconciled, priority)
    resp = await client.call(
        system=SYSTEM_PROMPT,
        user=user,
        model=model,
        skill_id=skill_id,
        skill_hash=skill_hash_value,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )

    try:
        payload = parse_decide_response(
            resp.response,
            cluster=cluster,
            reconciled=reconciled,
        )
    except DecideParseError as e:
        _log.warning(
            "decide parse failed for cluster %s: %s — falling back",
            cluster.cluster_id,
            e,
        )
        return DecideOutcome(
            cluster_id=cluster.cluster_id,
            principle=None,
            decision=None,
            native_payload={
                "fallback": True,
                "reason": str(e),
                "raw_response": resp.response,
            },
            status="fallback",
            reason=str(e),
        )

    principle = _build_principle(payload, cluster.cluster_id)
    decision = _build_decision(payload, cluster.cluster_id)
    return DecideOutcome(
        cluster_id=cluster.cluster_id,
        principle=principle,
        decision=decision,
        native_payload=payload,
        status="decided",
        reason=None,
    )


async def decide_batch(
    clusters: list[InsightCluster],
    reconciled_by_cluster: dict[str, ReconciledVerdict],
    priority_by_cluster: dict[str, PriorityScore],
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str | None = None,
) -> tuple[list[DecideOutcome], list[tuple[str, Exception]]]:
    """Generate principle + decision for a batch of clusters concurrently.

    A cluster missing either a reconciled verdict or a priority score
    gets a fallback outcome without a Claude call. Transport errors
    per-cluster propagate into ``failures``.
    """
    sh = skill_hash_value if skill_hash_value is not None else skill_hash()

    async def _one(
        c: InsightCluster,
    ) -> tuple[str, DecideOutcome | Exception]:
        reconciled = reconciled_by_cluster.get(c.cluster_id)
        priority = priority_by_cluster.get(c.cluster_id)
        if reconciled is None or priority is None:
            missing = []
            if reconciled is None:
                missing.append("reconciled_verdict")
            if priority is None:
                missing.append("priority_score")
            reason = (
                f"cluster {c.cluster_id} missing {', '.join(missing)}; "
                f"skipping L7 decision"
            )
            _log.warning(reason)
            return (
                c.cluster_id,
                DecideOutcome(
                    cluster_id=c.cluster_id,
                    principle=None,
                    decision=None,
                    native_payload={"fallback": True, "reason": reason, "raw_response": ""},
                    status="fallback",
                    reason=reason,
                ),
            )
        try:
            outcome = await decide_cluster(
                c, reconciled, priority, client,
                model=model, skill_id=skill_id, skill_hash_value=sh,
            )
            return (c.cluster_id, outcome)
        except Exception as e:  # noqa: BLE001
            return (c.cluster_id, e)

    results = await asyncio.gather(*(_one(c) for c in clusters))
    outcomes: list[DecideOutcome] = []
    failures: list[tuple[str, Exception]] = []
    for cid, payload in results:
        if isinstance(payload, DecideOutcome):
            outcomes.append(payload)
        else:
            failures.append((cid, payload))
    return outcomes, failures


def _sort_outcomes(outcomes: list[DecideOutcome]) -> list[DecideOutcome]:
    return sorted(outcomes, key=lambda o: o.cluster_id)


def _native_row(outcome: DecideOutcome) -> dict[str, Any]:
    return {
        "cluster_id": outcome.cluster_id,
        "status": outcome.status,
        "reason": outcome.reason,
        "payload": outcome.native_payload,
    }


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def build_provenance(
    outcomes: list[DecideOutcome],
    failures: list[tuple[str, Exception]],
    *,
    model: str,
) -> dict[str, Any]:
    """Summarise an L7 run into the provenance payload.

    Tracks: count of resolved heuristics per decision (distribution),
    count of review_ids cited per principle (distribution), number of
    unique principle names (accretion signal for future L10 evolution
    layer), fallback reasons, transport failures.
    """
    decided = [o for o in outcomes if o.status == "decided"]
    fallback = [o for o in outcomes if o.status == "fallback"]

    heuristics_resolved_counts: list[int] = []
    review_ids_cited_counts: list[int] = []
    principle_names: list[str] = []
    for o in decided:
        if o.decision is not None:
            heuristics_resolved_counts.append(len(o.decision.resolves_heuristics))
        if o.principle is not None:
            review_ids_cited_counts.append(len(o.principle.derived_from_review_ids))
            principle_names.append(o.principle.name)

    def _hist(values: list[int]) -> dict[str, int]:
        h: dict[str, int] = {}
        for v in values:
            h[str(v)] = h.get(str(v), 0) + 1
        return dict(sorted(h.items()))

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "cluster_count": len(outcomes) + len(failures),
        "decided_count": len(decided),
        "fallback_count": len(fallback),
        "transport_failure_count": len(failures),
        "resolves_heuristics_count_histogram": _hist(heuristics_resolved_counts),
        "derived_from_review_ids_count_histogram": _hist(review_ids_cited_counts),
        "distinct_principle_names": len(set(principle_names)),
        "principle_name_duplication_count": len(principle_names) - len(set(principle_names)),
        "mean_heuristics_resolved": (
            sum(heuristics_resolved_counts) / len(heuristics_resolved_counts)
            if heuristics_resolved_counts
            else 0.0
        ),
        "mean_review_ids_cited": (
            sum(review_ids_cited_counts) / len(review_ids_cited_counts)
            if review_ids_cited_counts
            else 0.0
        ),
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
            "L7 design-decide — generates one DesignPrinciple + one "
            "DesignDecision per cluster from its ReconciledVerdict + "
            "PriorityScore + cluster context. Writes principles + "
            "decisions + native + provenance."
        ),
    )
    parser.add_argument(
        "--reconciled",
        type=Path,
        default=repo_root / DEFAULT_RECONCILED,
    )
    parser.add_argument(
        "--priority",
        type=Path,
        default=repo_root / DEFAULT_PRIORITY,
    )
    parser.add_argument(
        "--clusters",
        type=Path,
        default=repo_root / DEFAULT_CLUSTERS,
    )
    parser.add_argument(
        "--principles-output",
        type=Path,
        default=repo_root / DEFAULT_PRINCIPLES,
    )
    parser.add_argument(
        "--decisions-output",
        type=Path,
        default=repo_root / DEFAULT_DECISIONS,
    )
    parser.add_argument(
        "--native-output",
        type=Path,
        default=repo_root / DEFAULT_NATIVE,
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
        _log.error("empty clusters input — nothing to decide")
        return 1

    reconciled_by_cluster = load_reconciled_verdicts(args.reconciled)
    _log.info(
        "loaded %d reconciled verdicts from %s",
        len(reconciled_by_cluster),
        args.reconciled,
    )
    priority_by_cluster = load_priority_scores(args.priority)
    _log.info(
        "loaded %d priority scores from %s",
        len(priority_by_cluster),
        args.priority,
    )

    run_id = args.run_id or _default_run_id().replace("l4-", "l7-", 1)

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
        decide_batch(
            clusters,
            reconciled_by_cluster,
            priority_by_cluster,
            client,
            model=args.model,
        )
    )

    sorted_outcomes = _sort_outcomes(outcomes)

    # Principles file — only successes.
    args.principles_output.parent.mkdir(parents=True, exist_ok=True)
    clusters_hash = hash_file(args.clusters)
    reconciled_hash = hash_file(args.reconciled)
    priority_hash = hash_file(args.priority)

    principles = [
        o.principle.model_dump(mode="json")
        for o in sorted_outcomes
        if o.principle is not None
    ]
    out_meta_p = write_jsonl_atomic(
        args.principles_output,
        principles,
        run_id=run_id,
        layer=f"{LAYER_NAME}_principles",
        input_hashes={
            args.clusters.name: clusters_hash,
            args.reconciled.name: reconciled_hash,
            args.priority.name: priority_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d principles to %s (sha256=%s…)",
        len(principles),
        args.principles_output,
        out_meta_p.artifact_sha256[:16],
    )

    # Decisions file — only successes.
    args.decisions_output.parent.mkdir(parents=True, exist_ok=True)
    decisions = [
        o.decision.model_dump(mode="json")
        for o in sorted_outcomes
        if o.decision is not None
    ]
    out_meta_d = write_jsonl_atomic(
        args.decisions_output,
        decisions,
        run_id=run_id,
        layer=f"{LAYER_NAME}_decisions",
        input_hashes={
            args.clusters.name: clusters_hash,
            args.reconciled.name: reconciled_hash,
            args.priority.name: priority_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d decisions to %s (sha256=%s…)",
        len(decisions),
        args.decisions_output,
        out_meta_d.artifact_sha256[:16],
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
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d native payloads to %s (sha256=%s…)",
        len(sorted_outcomes),
        args.native_output,
        native_meta.artifact_sha256[:16],
    )

    # Provenance sidecar — use principles_output as the anchor file.
    provenance_path = args.principles_output.with_suffix(".provenance.json")
    provenance_payload = (
        json.dumps(
            build_provenance(outcomes, failures, model=args.model),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(provenance_path, provenance_payload)
    _log.info("wrote L7 provenance to %s", provenance_path)

    decided_count = sum(1 for o in outcomes if o.status == "decided")
    fallback_count = sum(1 for o in outcomes if o.status == "fallback")
    _log.info(
        "L7 done. mode=%s live-spend=$%.4f decided=%d fallback=%d transport_fail=%d",
        args.mode,
        client.cumulative_usd,
        decided_count,
        fallback_count,
        len(failures),
    )

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
