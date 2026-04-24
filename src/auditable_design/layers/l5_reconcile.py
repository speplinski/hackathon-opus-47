"""Layer 5 — cross-skill reconciliation of L4 audit verdicts.

First L5 layer. Sits between the six parallel L4 audits (Norman,
Accessibility, Kahneman, Osterwalder, Cooper, Garrett) and the L6
weighting layer. Consumes the six per-skill :class:`AuditVerdict`
records for each cluster and emits one :class:`ReconciledVerdict` per
cluster: a flat ranked list of violations deduplicated by cross-skill
corroboration, plus a list of :class:`SkillTension` records where two
skills lean on opposing design principles.

Follows the **separate-module pattern** established by L4, but with
N-verdicts-to-1-verdict shape rather than L4's 1-cluster-to-1-verdict.
Neutral pipeline helpers (`_resolve_repo_root`,
`_configure_logging`, `_default_run_id`, `_atomic_write_bytes`,
`_fallback_native`, `load_clusters`, `_verdict_id`) are imported from
:mod:`l4_audit`; a handful of L5-specific helpers are defined here.

Why a reconciliation layer
--------------------------
Six skills looking at the same cluster produce six verdicts in six
vocabularies. A downstream consumer (demo, L6 weighting) needs one
prioritised view; naive concatenation misses corroborations (two skills
name the same defect in different words) and tensions (two skills lean
on opposing principles). The reconciliation skill is SOT-derived:

- **Evidence graph as audit trail.** The full node-and-edge graph
  (violations / corroborations / contradictions / tensions / gaps)
  lives in the native sidecar — unused by the consumer, available to
  reviewers.
- **Flat ranked list as consumer view.** ``ReconciledVerdict`` carries
  the ranked violations and the tensions list; consumers read those.
- **Severity passes through verbatim.** L5 does not re-score. Severities
  are imported from the source L4 ``HeuristicViolation.severity``
  (ADR-008 anchored 0–10) without modification.

Input / output
--------------
* Reads a *bundle* file — one JSONL concatenating the
  :class:`AuditVerdict` rows from all six L4 skills. Rows may appear
  in any order; the loader groups by ``cluster_id`` and requires all
  six ``skill_id`` values per cluster (configurable — a cluster with
  fewer verdicts falls back rather than blocking the whole run).
* Reads the L3b labeled clusters file for cluster context (label,
  representative quotes, optional ``ui_context`` / ``html`` /
  ``screenshot_ref``).
* Writes :data:`DEFAULT_VERDICTS` — one :class:`ReconciledVerdict` per
  cluster, via :class:`storage.write_jsonl_atomic`.
* Writes :data:`DEFAULT_NATIVE` with the full skill payload (summary,
  graph, ranked_violations, tensions, gaps) keyed by ``cluster_id``.
* Writes a ``.provenance.json`` sidecar with L5-specific aggregates —
  node-type histogram, tension-axis histogram, corroboration
  distribution, gap count.

Model default
-------------
Opus 4.7 per ADR-009: L5 reconciles six audits into a ranked list with
cross-skill tension detection — reasoning-heavy, low-volume
(one call per cluster). Opus 4.7 strips ``temperature`` at the wire;
``claude_client._omits_sampling_params`` handles this transparently.

Fallback discipline
-------------------
Identical philosophy to L4: parse failure → fallback
:class:`ReconciledVerdict` with zero ranked violations and zero
tensions; the raw skill response is preserved in the native sidecar.
Transport-level errors still propagate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
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
    _fallback_native,
    _resolve_repo_root,
    load_clusters,
)
from auditable_design.schemas import (
    SCHEMA_VERSION,
    AuditVerdict,
    HeuristicViolation,
    InsightCluster,
    ReconciledVerdict,
    SkillTension,
)
from auditable_design.storage import hash_file, read_jsonl, write_jsonl_atomic

__all__ = [
    "DEFAULT_CLUSTERS",
    "DEFAULT_NATIVE",
    "DEFAULT_VERDICTS",
    "LAYER_NAME",
    "MAX_TOKENS",
    "MODEL",
    "SKILL_ID",
    "SKILL_TO_FRAME",
    "SYSTEM_PROMPT",
    "TEMPERATURE",
    "VALID_L4_SKILLS",
    "VALID_NODE_TYPES",
    "VALID_RELATION_TYPES",
    "VALID_TENSION_AXES",
    "ReconcileOutcome",
    "ReconcileParseError",
    "build_provenance",
    "build_user_message",
    "load_verdicts_bundle",
    "main",
    "parse_reconcile_response",
    "reconcile_batch",
    "reconcile_cluster",
    "skill_hash",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_ID: str = "sot-reconcile"
LAYER_NAME: str = "l5_reconcile"

# Opus 4.7 per ADR-009: L5 is reasoning-heavy (tension detection,
# corroboration grouping, gap inference) and low-volume (one call per
# cluster, ~14 calls for a full-corpus run). Opus 4.7 rejects
# ``temperature`` at the API; ``claude_client._omits_sampling_params``
# gates the send.
MODEL: str = "claude-opus-4-7"
TEMPERATURE: float = 0.0

# Reconciliation output is dense: graph (≤20 nodes × 80 tokens + ≤30
# edges × 30 tokens) ≈ 2.5k output; ranked_violations (8–15 entries ×
# 120 tokens) ≈ 1.5k; tensions (0–3 × 200 tokens) ≈ 600; gaps (0–2 ×
# 200 tokens) ≈ 400; plus summary ≈ 200. SKILL.md caps ``graph.nodes``
# at 20 (selective inclusion — not every L4 finding becomes a node);
# the initial 8192 ceiling was hit in practice on Opus 4.6 when the
# model imported all L4 findings 1:1 as violation nodes. 16384 gives
# 2× headroom over the practical ~6k-output worst case even if a
# reasoning preamble appears before the JSON.
MAX_TOKENS: int = 16384

# Closed set of the six L4 skill ids. An input verdict whose
# ``skill_id`` is not in this set is a wiring error and triggers a
# fallback. Any future L4 skill additions must update this set AND the
# SKILL.md's axis taxonomy.
VALID_L4_SKILLS: frozenset[str] = frozenset(
    {
        "audit-usability-fundamentals",
        "audit-accessibility",
        "audit-decision-psychology",
        "audit-business-alignment",
        "audit-interaction-design",
        "audit-ux-architecture",
    }
)

# Frame classification for the ``unique_frames`` tie-breaker. Each L4
# skill maps to exactly one frame; frames group skills by the design
# concern they primarily audit. Two skills sharing a frame count once
# in the ``unique_frames`` tally of a corroborated violation.
SKILL_TO_FRAME: dict[str, str] = {
    "audit-usability-fundamentals": "fundamentals",
    "audit-accessibility": "accessibility",
    "audit-decision-psychology": "decision",
    "audit-business-alignment": "business",
    "audit-interaction-design": "interaction",
    "audit-ux-architecture": "architecture",
}

# Valid node-type codes — SKILL.md output contract.
VALID_NODE_TYPES: frozenset[str] = frozenset(
    {"violation", "corroboration", "contradiction", "tension", "gap"}
)

# Valid relation-type codes — SKILL.md output contract.
VALID_RELATION_TYPES: frozenset[str] = frozenset(
    {"corroborates", "contradicts", "in_tension_with", "elaborates"}
)

# Closed set of tension axes. SKILL.md permits coining a new axis when
# none fit AND the novelty is likely to recur — so this set is a
# soft-close: the parser warns on unknown axes but does not reject.
# Emit warning to the log; keep processing.
VALID_TENSION_AXES: frozenset[str] = frozenset(
    {
        "user_control_vs_platform_norms",
        "efficiency_vs_safety",
        "conversion_vs_user_wellbeing",
        "discoverability_vs_density",
        "principled_accretion_vs_featuritis",
        "idiom_vs_metaphor",
        "system1_ease_vs_system2_deliberation",
    }
)

# Valid evidence-source tokens — inherited from L4 for gap nodes.
_VALID_EVIDENCE_SOURCES: frozenset[str] = frozenset(
    {"quotes", "ui_context", "html", "screenshot"}
)

# Severity is imported from L4; legal anchored values are 0 and the
# four ADR-008 anchors.
_VALID_SEVERITIES_ANCHORED: frozenset[int] = frozenset({0, 3, 5, 7, 9})

# Default paths.
DEFAULT_CLUSTERS = Path("data/derived/l3b_labeled_clusters.jsonl")
DEFAULT_VERDICTS = Path("data/derived/l5_reconciled_verdicts.jsonl")
DEFAULT_NATIVE = Path("data/derived/l5_reconciled_verdicts.native.jsonl")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ReconcileParseError(AuditParseError):
    """Parse / validation failure specific to the L5 reconcile payload.

    Inherits from :class:`AuditParseError` so callers that already
    catch the L4 exception class catch L5 failures too; the distinct
    subclass lets L5-specific tooling discriminate.
    """


# ---------------------------------------------------------------------------
# System prompt — loaded from SKILL.md
# ---------------------------------------------------------------------------


def _load_skill_body() -> str:
    """Read ``skills/sot-reconcile/SKILL.md`` and strip YAML frontmatter.

    Fails at import if the file is missing — the layer cannot function
    without its skill.
    """
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
    """sha256 of :data:`SYSTEM_PROMPT`.

    Editing ``skills/sot-reconcile/SKILL.md`` changes this hash and
    invalidates the L5 replay cache. Independent of every L4 skill's
    hash — L5 hash collisions with L4 are a wiring bug.
    """
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Input bundles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ClusterBundle:
    """One cluster's complete L4 verdict set.

    ``verdicts_by_skill`` maps skill_id → the corresponding
    :class:`AuditVerdict`. A cluster whose bundle is missing one or
    more of the six canonical skill_ids will still reconcile, but the
    SKILL.md contract expects the full set; missing skills produce a
    softer reconciliation (no corroboration from the absent skill).
    """

    cluster_id: str
    verdicts_by_skill: dict[str, AuditVerdict]


def load_verdicts_bundle(path: Path) -> dict[str, _ClusterBundle]:
    """Load an L5 input bundle (one JSONL concatenating L4 verdicts).

    Groups rows by ``cluster_id``, then by ``skill_id``. Duplicate
    (cluster_id, skill_id) pairs are a contract violation — the newer
    row wins with a warning (same cluster audited twice by the same
    skill means the earlier run was superseded; keeping both would
    double-count in corroboration).

    Returns a dict keyed by cluster_id.
    """
    rows = read_jsonl(path)
    bundle: dict[str, dict[str, AuditVerdict]] = defaultdict(dict)
    for i, row in enumerate(rows):
        try:
            verdict = AuditVerdict.model_validate(row)
        except Exception as e:  # noqa: BLE001 — fail-loud on malformed row
            raise RuntimeError(
                f"{LAYER_NAME}: row {i} of {path} is not a valid AuditVerdict: {e}"
            ) from e

        if verdict.skill_id not in VALID_L4_SKILLS:
            raise RuntimeError(
                f"{LAYER_NAME}: row {i} of {path} has unknown skill_id="
                f"{verdict.skill_id!r}; must be one of {sorted(VALID_L4_SKILLS)}"
            )

        if verdict.skill_id in bundle[verdict.cluster_id]:
            _log.warning(
                "duplicate verdict for cluster=%s skill=%s — later row wins",
                verdict.cluster_id,
                verdict.skill_id,
            )
        bundle[verdict.cluster_id][verdict.skill_id] = verdict

    return {cid: _ClusterBundle(cid, vs) for cid, vs in bundle.items()}


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_user_message(
    cluster: InsightCluster,
    bundle: _ClusterBundle,
) -> str:
    """Render the per-cluster user message for the reconciliation skill.

    Shape matches SKILL.md's ``<cluster>…</cluster>`` + ``<verdicts>…
    </verdicts>`` contract:

    * ``<cluster>`` block — label, optional ui_context/html/screenshot_ref,
      quotes.
    * ``<verdicts>`` block — one ``<verdict skill="...">`` per input
      verdict, each listing ``<finding idx="N">`` rows parallel to
      ``AuditVerdict.relevant_heuristics``.

    All text-content strings are XML-escaped (``&``, ``<``, ``>``) as
    defence in depth against prompt injection. ``html`` is CDATA-wrapped
    so the model sees raw markup while the outer tags remain the
    injection boundary.

    Finding rows are rendered as one-liners carrying the three fields
    the model uses for reconcile: ``heuristic`` (slug), ``severity``
    (anchored 0–10), and ``violation`` (prose). The reasoning field is
    folded into ``violation`` verbatim — it already carries the
    skill-specific structured fields (posture, product_type, etc.) the
    L4 modules encoded.
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
    if cluster.html is not None:
        parts.append(f"  <html><![CDATA[\n{cluster.html}\n]]></html>")
    if cluster.screenshot_ref is not None:
        parts.append(
            f"  <screenshot_ref>{cluster.screenshot_ref.translate(escape)}</screenshot_ref>"
        )
    for i, q in enumerate(cluster.representative_quotes):
        parts.append(f'  <q idx="{i}">{q.translate(escape)}</q>')
    parts.append("</cluster>")

    parts.append("<verdicts>")
    # Render in the canonical tie-break order (see SKILL.md ranking
    # rule). Produces deterministic prompts across runs.
    canonical_order = [
        "audit-business-alignment",
        "audit-decision-psychology",
        "audit-accessibility",
        "audit-usability-fundamentals",
        "audit-interaction-design",
        "audit-ux-architecture",
    ]
    for skill_id in canonical_order:
        verdict = bundle.verdicts_by_skill.get(skill_id)
        if verdict is None:
            continue  # skill absent from bundle — SKILL.md tolerates
        parts.append(f'  <verdict skill="{skill_id}">')
        for idx, finding in enumerate(verdict.relevant_heuristics):
            parts.append(
                f'    <finding idx="{idx}" heuristic="{finding.heuristic.translate(escape)}" '
                f'severity="{finding.severity}">'
                f"{finding.violation.translate(escape)} "
                f"[reasoning: {finding.reasoning.translate(escape)}]"
                f"</finding>"
            )
        parts.append("  </verdict>")
    parts.append("</verdicts>")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_TOP_LEVEL_KEYS = {"summary", "graph"}

_GRAPH_KEYS = {"nodes", "edges"}

_NODE_KEYS_BASE = {
    "id",
    "type",
    "label",
    "rationale",
    "confidence",
    "source_skill",
    "source_heuristic",
    "source_severity_anchored",
    "source_finding_idx",
    "member_ids",
    "skill_a",
    "skill_b",
    "axis",
    "resolution",
    "evidence_source",
    "evidence_quote_idxs",
    "why_missed",
}

_EDGE_KEYS = {"source", "target", "type"}

_RANKED_KEYS = {
    "heuristic",
    "violation",
    "severity",
    "source_skills",
    "corroboration_count",
    "unique_frames",
    "rank_score",
    "rationale",
}

_TENSION_KEYS = {"skill_a", "skill_b", "axis", "resolution"}

_GAP_KEYS = {"rationale", "evidence_source", "evidence_quote_idxs", "why_missed"}


def _repair_unescaped_string_quotes(raw: str, max_iters: int = 32) -> str:
    """Iteratively escape stray ``"`` that prematurely terminate JSON
    strings. Duplicated from the L4 parsers — same failure mode (Sonnet
    4.6 occasionally emits literal inner double-quotes instead of
    ``\\"``). Extraction into :mod:`l4_audit` helpers is a separate
    cleanup PR.
    """
    s = raw
    for _ in range(max_iters):
        try:
            json.loads(s)
            return s
        except json.JSONDecodeError as err:
            msg = str(err)
            if (
                "Expecting ',' delimiter" not in msg
                and "Expecting property name" not in msg
            ):
                return raw if s == raw else s
            pos = err.pos
            i = pos - 1
            while i >= 0 and s[i] != '"':
                i -= 1
            if i < 0:
                return raw if s == raw else s
            backslashes = 0
            j = i - 1
            while j >= 0 and s[j] == "\\":
                backslashes += 1
                j -= 1
            if backslashes % 2 == 1:
                return raw if s == raw else s
            s = s[:i] + "\\" + s[i:]
    return s


def parse_reconcile_response(
    text: str,
    *,
    bundle: _ClusterBundle,
    n_quotes: int,
) -> dict[str, Any]:
    """Extract and validate the L5 reconcile payload.

    On success returns the parsed payload dict. On any structural,
    type, or cross-reference violation raises :class:`ReconcileParseError`.

    ``bundle`` is the input cluster bundle; the parser cross-validates
    violation nodes' ``source_skill`` / ``source_finding_idx`` /
    ``source_heuristic`` / ``source_severity_anchored`` against it.
    ``n_quotes`` is used to range-check gap nodes' ``evidence_quote_idxs``.
    """
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise ReconcileParseError(f"no JSON object found in response: {text!r}")
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as first_err:
        repaired = _repair_unescaped_string_quotes(raw)
        if repaired == raw:
            raise ReconcileParseError(
                f"malformed JSON: {first_err}; text={text!r}"
            ) from first_err
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as retry_err:
            raise ReconcileParseError(
                f"malformed JSON: {first_err}; "
                f"sanitised retry also failed: {retry_err}"
            ) from retry_err
    if not isinstance(data, dict):
        raise ReconcileParseError(f"expected JSON object, got {type(data).__name__}")

    actual = set(data.keys())
    missing = _TOP_LEVEL_KEYS - actual
    if missing:
        raise ReconcileParseError(
            f"missing required top-level keys: {sorted(missing)}"
        )
    # Legacy tolerance: SKILL.md v1.x had `ranked_violations`, `tensions`,
    # `gaps` as top-level keys emitted by the model. v2.0 derives them
    # from the graph, but we silently drop the legacy keys so old raw
    # responses still parse cleanly. Any other unexpected top-level
    # key is still a hard fail.
    _LEGACY_TOP_LEVEL = {"ranked_violations", "tensions", "gaps"}
    extra = actual - _TOP_LEVEL_KEYS - _LEGACY_TOP_LEVEL
    if extra:
        raise ReconcileParseError(f"unexpected top-level keys: {sorted(extra)}")
    for legacy_key in _LEGACY_TOP_LEVEL & actual:
        _log.info(
            "legacy top-level key %r ignored — parser derives from graph",
            legacy_key,
        )
        data.pop(legacy_key, None)

    # --- summary -----------------------------------------------------
    summary = data["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise ReconcileParseError("'summary' must be a non-empty str")

    # --- graph -------------------------------------------------------
    graph = data["graph"]
    if not isinstance(graph, dict):
        raise ReconcileParseError(
            f"'graph' must be dict, got {type(graph).__name__}"
        )
    g_actual = set(graph.keys())
    g_missing = _GRAPH_KEYS - g_actual
    if g_missing:
        raise ReconcileParseError(f"graph missing keys: {sorted(g_missing)}")
    g_extra = g_actual - _GRAPH_KEYS
    if g_extra:
        raise ReconcileParseError(f"graph has unexpected keys: {sorted(g_extra)}")
    nodes = graph["nodes"]
    edges = graph["edges"]
    if not isinstance(nodes, list):
        raise ReconcileParseError(
            f"graph.nodes must be list, got {type(nodes).__name__}"
        )
    if not isinstance(edges, list):
        raise ReconcileParseError(
            f"graph.edges must be list, got {type(edges).__name__}"
        )

    # --- nodes ------------------------------------------------------
    node_ids: set[str] = set()
    violation_node_ids: set[str] = set()
    tension_node_count = 0
    gap_node_count = 0
    tension_nodes: list[dict[str, Any]] = []
    gap_nodes: list[dict[str, Any]] = []
    for i, node in enumerate(nodes):
        _validate_node(node, i=i, bundle=bundle, n_quotes=n_quotes)
        if node["id"] in node_ids:
            raise ReconcileParseError(f"graph.nodes[{i}] duplicate id={node['id']!r}")
        node_ids.add(node["id"])
        if node["type"] == "violation":
            violation_node_ids.add(node["id"])
        elif node["type"] == "tension":
            tension_node_count += 1
            tension_nodes.append(node)
        elif node["type"] == "gap":
            gap_node_count += 1
            gap_nodes.append(node)

    # corroboration member_ids must refer to violation nodes (second
    # pass — we need all violation ids first).
    for i, node in enumerate(nodes):
        if node["type"] == "corroboration":
            members = node["member_ids"]
            for m_id in members:
                if m_id not in violation_node_ids:
                    raise ReconcileParseError(
                        f"graph.nodes[{i}] corroboration.member_ids contains "
                        f"{m_id!r} which is not a violation-node id"
                    )

    # --- edges ------------------------------------------------------
    for i, edge in enumerate(edges):
        _validate_edge(edge, i=i, node_ids=node_ids)

    # --- derive flat lists from graph -------------------------------
    # SKILL.md v2.0: model emits only {summary, graph}. Parser walks
    # the graph to produce the consumer-facing flat lists —
    # ranked_violations, tensions, gaps. This keeps the model's output
    # single-representation (graph), eliminates dual-representation
    # drift, and makes the flat lists deterministic by construction.
    ranked, tensions, gaps = _derive_flat_lists_from_graph(
        nodes, tension_nodes, gap_nodes
    )
    data["ranked_violations"] = ranked
    data["tensions"] = tensions
    data["gaps"] = gaps

    return data


def _derive_flat_lists_from_graph(
    nodes: list[dict[str, Any]],
    tension_nodes: list[dict[str, Any]],
    gap_nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Walk a validated graph and derive the three consumer-facing
    flat lists: ranked_violations, tensions, gaps.

    Ranked-violations derivation:

    * Each `corroboration` node collapses its member violation nodes
      into one ranked entry. Severity is the max of member severities;
      ``source_skills`` is the dedup of member ``source_skill``.
      Heuristic slug uses the corroboration's ``label`` (falling back
      to the first member's ``source_heuristic`` if the label reads
      like free prose).
    * Each `violation` node that is NOT a member of any corroboration
      contributes a solitary ranked entry.
    * Parser then computes ``rank_score = severity * corroboration_count``
      and ``unique_frames`` via :data:`SKILL_TO_FRAME`, and sorts
      descending (rank_score, unique_frames, severity).

    Tensions and gaps are a straight extraction of their respective
    graph node types into the consumer schema.

    The arithmetic and sort are derived here, not emitted by the
    model — v2.0 contract (SKILL.md 2026-04-24 rewrite).
    """
    nodes_by_id = {n["id"]: n for n in nodes}
    violations = [n for n in nodes if n["type"] == "violation"]
    corroborations = [n for n in nodes if n["type"] == "corroboration"]

    # Map each violation to the FIRST corroboration that contains it
    # (a violation cited by two corroborations is unusual but possible —
    # we pick the first for deterministic collapsing).
    vid_to_corr: dict[str, str] = {}
    for c in corroborations:
        for vid in c.get("member_ids", []):
            if vid in nodes_by_id and vid not in vid_to_corr:
                vid_to_corr[vid] = c["id"]

    ranked: list[dict[str, Any]] = []

    # Corroborated entries: one per corroboration node.
    for c in corroborations:
        members = [
            nodes_by_id[vid]
            for vid in c.get("member_ids", [])
            if vid in nodes_by_id and nodes_by_id[vid]["type"] == "violation"
        ]
        if not members:
            continue
        severities = [int(m["source_severity_anchored"]) for m in members]
        severity = max(severities)
        source_skills = sorted({m["source_skill"] for m in members})
        # Heuristic slug for the corroborated entry: prefer a short
        # label, fall back to the first member's heuristic.
        label = str(c.get("label") or "").strip()
        if label and len(label) <= 80 and " " not in label.strip("_ "):
            heuristic = label
        else:
            # Combine member heuristics to signal the cross-skill
            # collapse, but cap the length.
            slugs = sorted({m["source_heuristic"] for m in members})
            heuristic = "__".join(slugs) if len(slugs) <= 3 else f"{slugs[0]}__corroborated"
        violation_text = str(c.get("rationale") or label or "Corroborated defect across multiple skills.")
        rationale = (
            f"Corroboration across {len(source_skills)} skill(s) "
            f"({', '.join(source_skills)}): {c.get('rationale', '').strip() or label}"
        ).strip()
        entry = {
            "heuristic": heuristic,
            "violation": violation_text,
            "severity": severity,
            "source_skills": source_skills,
            "rationale": rationale,
        }
        ranked.append(entry)

    # Solitary entries: violations not in any corroboration.
    for v in violations:
        if v["id"] in vid_to_corr:
            continue
        entry = {
            "heuristic": v["source_heuristic"],
            "violation": str(
                v.get("rationale") or v.get("label") or v["source_heuristic"]
            ).strip()
            or v["source_heuristic"],
            "severity": int(v["source_severity_anchored"]),
            "source_skills": [v["source_skill"]],
            "rationale": (
                f"Solitary {v['source_skill']} finding "
                f"(severity {v['source_severity_anchored']}); "
                f"no cross-skill corroboration surfaced for this defect."
            ),
        }
        ranked.append(entry)

    # Compute rank_score, corroboration_count, unique_frames; sort.
    for entry in ranked:
        entry["corroboration_count"] = len(entry["source_skills"])
        entry["unique_frames"] = len(
            {SKILL_TO_FRAME[s] for s in entry["source_skills"]}
        )
        entry["rank_score"] = entry["severity"] * entry["corroboration_count"]
    ranked.sort(
        key=lambda e: (
            -int(e["rank_score"]),
            -int(e["unique_frames"]),
            -int(e["severity"]),
        )
    )

    # Tensions: extract tension nodes into flat entries.
    tensions: list[dict[str, Any]] = [
        {
            "skill_a": t["skill_a"],
            "skill_b": t["skill_b"],
            "axis": t["axis"],
            "resolution": t["resolution"],
        }
        for t in tension_nodes
    ]

    # Gaps: extract gap nodes into flat entries.
    gaps: list[dict[str, Any]] = [
        {
            "rationale": g["rationale"],
            "evidence_source": list(g["evidence_source"]),
            "evidence_quote_idxs": list(g["evidence_quote_idxs"]),
            "why_missed": g["why_missed"],
        }
        for g in gap_nodes
    ]

    return ranked, tensions, gaps


def _auto_repair_ranked(ranked: list[dict[str, Any]]) -> None:
    """Mutate ``ranked`` in place to align computable properties with
    their canonical values.

    Repairs:

    * ``corroboration_count`` → ``len(source_skills)`` when deduplicated
      (source_skills is cast to a set and back to a sorted list to
      guarantee the count is distinct-skill count).
    * ``unique_frames`` → number of distinct frames covered by
      source_skills via :data:`SKILL_TO_FRAME`.
    * ``rank_score`` → ``severity * corroboration_count`` after the
      above repairs.

    All repairs are logged at WARNING level so reviewers can see when
    the model emitted mis-computed values.
    """
    for i, entry in enumerate(ranked):
        skills = entry["source_skills"]
        # Dedup skills to distinct identities; preserve order for
        # determinism.
        seen: list[str] = []
        for s in skills:
            if s not in seen:
                seen.append(s)
        if seen != skills:
            _log.warning(
                "ranked_violations[%d].source_skills had duplicates %s "
                "— deduped to %s",
                i,
                skills,
                seen,
            )
            entry["source_skills"] = seen
        corr_expected = len(seen)
        if entry.get("corroboration_count") != corr_expected:
            _log.warning(
                "ranked_violations[%d].corroboration_count=%s → %s "
                "(len of deduped source_skills)",
                i,
                entry.get("corroboration_count"),
                corr_expected,
            )
            entry["corroboration_count"] = corr_expected

        frames = {SKILL_TO_FRAME[s] for s in seen}
        frames_expected = len(frames)
        if entry.get("unique_frames") != frames_expected:
            _log.warning(
                "ranked_violations[%d].unique_frames=%s → %s "
                "(distinct frames in %s)",
                i,
                entry.get("unique_frames"),
                frames_expected,
                sorted(frames),
            )
            entry["unique_frames"] = frames_expected

        sev = int(entry["severity"])
        rs_expected = sev * corr_expected
        if entry.get("rank_score") != rs_expected:
            _log.warning(
                "ranked_violations[%d].rank_score=%s → %s "
                "(severity %d × corroboration %d)",
                i,
                entry.get("rank_score"),
                rs_expected,
                sev,
                corr_expected,
            )
            entry["rank_score"] = rs_expected


def _validate_node(
    node: Any,
    *,
    i: int,
    bundle: _ClusterBundle,
    n_quotes: int,
) -> None:
    """Validate one graph node. Type-discriminated required fields."""
    if not isinstance(node, dict):
        raise ReconcileParseError(
            f"graph.nodes[{i}] must be dict, got {type(node).__name__}"
        )
    missing = _NODE_KEYS_BASE - set(node.keys())
    if missing:
        raise ReconcileParseError(
            f"graph.nodes[{i}] missing keys: {sorted(missing)}"
        )
    extra = set(node.keys()) - _NODE_KEYS_BASE
    if extra:
        raise ReconcileParseError(
            f"graph.nodes[{i}] unexpected keys: {sorted(extra)}"
        )

    if not isinstance(node["id"], str) or not node["id"].strip():
        raise ReconcileParseError(f"graph.nodes[{i}].id must be non-empty str")

    ntype = node["type"]
    if ntype not in VALID_NODE_TYPES:
        raise ReconcileParseError(
            f"graph.nodes[{i}].type={ntype!r} not in {sorted(VALID_NODE_TYPES)}"
        )

    for str_key in ("label", "rationale"):
        v = node[str_key]
        if not isinstance(v, str) or not v.strip():
            raise ReconcileParseError(
                f"graph.nodes[{i}].{str_key} must be non-empty str"
            )

    conf = node["confidence"]
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ReconcileParseError(
            f"graph.nodes[{i}].confidence must be number, "
            f"got {type(conf).__name__}"
        )
    if not (0.0 <= float(conf) <= 1.0):
        raise ReconcileParseError(
            f"graph.nodes[{i}].confidence={conf} out of [0.0, 1.0]"
        )

    # Type-discriminated required fields.
    if ntype == "violation":
        _validate_violation_node(node, i=i, bundle=bundle)
    elif ntype == "corroboration":
        # member_ids checked in the second pass; here just check shape.
        m_ids = node["member_ids"]
        if not isinstance(m_ids, list):
            raise ReconcileParseError(
                f"graph.nodes[{i}] corroboration.member_ids must be list"
            )
        if len(m_ids) < 2:
            raise ReconcileParseError(
                f"graph.nodes[{i}] corroboration.member_ids must have ≥ 2 "
                f"entries (SKILL.md rule)"
            )
        for j, mid in enumerate(m_ids):
            if not isinstance(mid, str):
                raise ReconcileParseError(
                    f"graph.nodes[{i}].member_ids[{j}] must be str"
                )
    elif ntype == "contradiction":
        _validate_skill_pair(node, i=i, bundle=bundle, node_kind="contradiction")
        if not isinstance(node["resolution"], str) or not node["resolution"].strip():
            raise ReconcileParseError(
                f"graph.nodes[{i}] contradiction.resolution must be non-empty str"
            )
    elif ntype == "tension":
        _validate_skill_pair(node, i=i, bundle=bundle, node_kind="tension")
        axis = node["axis"]
        if not isinstance(axis, str) or not axis.strip():
            raise ReconcileParseError(
                f"graph.nodes[{i}] tension.axis must be non-empty str"
            )
        if axis not in VALID_TENSION_AXES:
            # Soft close: warn, do not reject. Reviewer sees the coinage
            # in the output and can decide to add it to the closed set
            # in a later SKILL.md revision.
            _log.warning(
                "graph.nodes[%d] tension.axis=%r is not in the closed set; "
                "passing but flagging for review",
                i,
                axis,
            )
        if not isinstance(node["resolution"], str) or not node["resolution"].strip():
            raise ReconcileParseError(
                f"graph.nodes[{i}] tension.resolution must be non-empty str"
            )
    elif ntype == "gap":
        _validate_gap_node(node, i=i, n_quotes=n_quotes)


def _validate_violation_node(
    node: dict[str, Any],
    *,
    i: int,
    bundle: _ClusterBundle,
) -> None:
    """Cross-validate a violation node against the input bundle.

    Every violation node's ``source_skill`` / ``source_heuristic`` /
    ``source_severity_anchored`` / ``source_finding_idx`` must name a
    real finding in a real verdict in the bundle. SKILL.md forbids
    introducing new heuristics at L5 — violations are imported.
    """
    ss = node["source_skill"]
    if not isinstance(ss, str) or ss not in VALID_L4_SKILLS:
        raise ReconcileParseError(
            f"graph.nodes[{i}] violation.source_skill={ss!r} not in "
            f"VALID_L4_SKILLS"
        )
    if ss not in bundle.verdicts_by_skill:
        raise ReconcileParseError(
            f"graph.nodes[{i}] violation.source_skill={ss!r} not present "
            f"in input bundle for cluster {bundle.cluster_id!r}"
        )
    verdict = bundle.verdicts_by_skill[ss]
    idx = node["source_finding_idx"]
    if not isinstance(idx, int) or isinstance(idx, bool):
        raise ReconcileParseError(
            f"graph.nodes[{i}] violation.source_finding_idx must be int"
        )
    if not (0 <= idx < len(verdict.relevant_heuristics)):
        raise ReconcileParseError(
            f"graph.nodes[{i}] violation.source_finding_idx={idx} out of "
            f"[0, {len(verdict.relevant_heuristics)}) for skill={ss!r}"
        )
    source_finding = verdict.relevant_heuristics[idx]
    if node["source_heuristic"] != source_finding.heuristic:
        raise ReconcileParseError(
            f"graph.nodes[{i}] violation.source_heuristic="
            f"{node['source_heuristic']!r} does not match finding "
            f"{idx} of skill {ss!r} (={source_finding.heuristic!r})"
        )
    sev = node["source_severity_anchored"]
    if not isinstance(sev, int) or isinstance(sev, bool):
        raise ReconcileParseError(
            f"graph.nodes[{i}] violation.source_severity_anchored must be int"
        )
    if sev != source_finding.severity:
        raise ReconcileParseError(
            f"graph.nodes[{i}] violation.source_severity_anchored={sev} "
            f"does not match source finding severity={source_finding.severity}"
        )


def _validate_skill_pair(
    node: dict[str, Any],
    *,
    i: int,
    bundle: _ClusterBundle,
    node_kind: str,
) -> None:
    """Validate ``skill_a`` / ``skill_b`` for tension + contradiction nodes."""
    a = node["skill_a"]
    b = node["skill_b"]
    if not isinstance(a, str) or a not in VALID_L4_SKILLS:
        raise ReconcileParseError(
            f"graph.nodes[{i}] {node_kind}.skill_a={a!r} not in VALID_L4_SKILLS"
        )
    if not isinstance(b, str) or b not in VALID_L4_SKILLS:
        raise ReconcileParseError(
            f"graph.nodes[{i}] {node_kind}.skill_b={b!r} not in VALID_L4_SKILLS"
        )
    if a == b:
        raise ReconcileParseError(
            f"graph.nodes[{i}] {node_kind}.skill_a == skill_b ({a!r}); "
            f"must differ"
        )
    for sk in (a, b):
        if sk not in bundle.verdicts_by_skill:
            raise ReconcileParseError(
                f"graph.nodes[{i}] {node_kind} references skill {sk!r} "
                f"not present in input bundle for cluster "
                f"{bundle.cluster_id!r}"
            )


def _validate_gap_node(
    node: dict[str, Any],
    *,
    i: int,
    n_quotes: int,
) -> None:
    """Validate a gap graph node.

    Enforces the bidirectional evidence rule inherited from L4: if
    ``quotes`` in evidence_source then evidence_quote_idxs non-empty
    and vice versa. ``why_missed`` required (non-empty str) —
    SKILL.md: a gap without a ``why_missed`` is suspect.
    """
    evs = node["evidence_source"]
    if not isinstance(evs, list):
        raise ReconcileParseError(
            f"graph.nodes[{i}] gap.evidence_source must be list"
        )
    if not evs:
        raise ReconcileParseError(
            f"graph.nodes[{i}] gap.evidence_source must be non-empty"
        )
    for j, src in enumerate(evs):
        if not isinstance(src, str):
            raise ReconcileParseError(
                f"graph.nodes[{i}] gap.evidence_source[{j}] must be str"
            )
        if src not in _VALID_EVIDENCE_SOURCES:
            raise ReconcileParseError(
                f"graph.nodes[{i}] gap.evidence_source[{j}]={src!r} not in "
                f"{sorted(_VALID_EVIDENCE_SOURCES)}"
            )
    if len(set(evs)) != len(evs):
        raise ReconcileParseError(
            f"graph.nodes[{i}] gap.evidence_source has duplicates"
        )

    idxs = node["evidence_quote_idxs"]
    if not isinstance(idxs, list):
        raise ReconcileParseError(
            f"graph.nodes[{i}] gap.evidence_quote_idxs must be list"
        )
    has_quotes = "quotes" in evs
    if has_quotes and not idxs:
        raise ReconcileParseError(
            f"graph.nodes[{i}] gap: 'quotes' in evidence_source but "
            f"evidence_quote_idxs empty (bidirectional rule)"
        )
    if idxs and not has_quotes:
        raise ReconcileParseError(
            f"graph.nodes[{i}] gap: evidence_quote_idxs non-empty but "
            f"'quotes' not in evidence_source (bidirectional rule)"
        )
    for j, idx in enumerate(idxs):
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise ReconcileParseError(
                f"graph.nodes[{i}] gap.evidence_quote_idxs[{j}] must be int"
            )
        if not (0 <= idx < n_quotes):
            raise ReconcileParseError(
                f"graph.nodes[{i}] gap.evidence_quote_idxs[{j}]={idx} out "
                f"of [0, {n_quotes})"
            )

    if not isinstance(node["why_missed"], str) or not node["why_missed"].strip():
        raise ReconcileParseError(
            f"graph.nodes[{i}] gap.why_missed must be non-empty str"
        )


def _validate_edge(
    edge: Any,
    *,
    i: int,
    node_ids: set[str],
) -> None:
    """Validate one graph edge."""
    if not isinstance(edge, dict):
        raise ReconcileParseError(
            f"graph.edges[{i}] must be dict, got {type(edge).__name__}"
        )
    missing = _EDGE_KEYS - set(edge.keys())
    if missing:
        raise ReconcileParseError(
            f"graph.edges[{i}] missing keys: {sorted(missing)}"
        )
    extra = set(edge.keys()) - _EDGE_KEYS
    if extra:
        raise ReconcileParseError(
            f"graph.edges[{i}] unexpected keys: {sorted(extra)}"
        )
    for endpoint in ("source", "target"):
        v = edge[endpoint]
        if not isinstance(v, str) or v not in node_ids:
            raise ReconcileParseError(
                f"graph.edges[{i}].{endpoint}={v!r} not a known node id"
            )
    rtype = edge["type"]
    if rtype not in VALID_RELATION_TYPES:
        raise ReconcileParseError(
            f"graph.edges[{i}].type={rtype!r} not in "
            f"{sorted(VALID_RELATION_TYPES)}"
        )


def _validate_ranked(
    entry: Any,
    *,
    i: int,
    bundle: _ClusterBundle,
) -> None:
    """Validate one ranked_violations entry + check formula consistency."""
    if not isinstance(entry, dict):
        raise ReconcileParseError(
            f"ranked_violations[{i}] must be dict, "
            f"got {type(entry).__name__}"
        )
    missing = _RANKED_KEYS - set(entry.keys())
    if missing:
        raise ReconcileParseError(
            f"ranked_violations[{i}] missing keys: {sorted(missing)}"
        )
    extra = set(entry.keys()) - _RANKED_KEYS
    if extra:
        raise ReconcileParseError(
            f"ranked_violations[{i}] unexpected keys: {sorted(extra)}"
        )

    for str_key in ("heuristic", "violation", "rationale"):
        v = entry[str_key]
        if not isinstance(v, str) or not v.strip():
            raise ReconcileParseError(
                f"ranked_violations[{i}].{str_key} must be non-empty str"
            )

    sev = entry["severity"]
    if not isinstance(sev, int) or isinstance(sev, bool):
        raise ReconcileParseError(
            f"ranked_violations[{i}].severity must be int"
        )
    if sev not in _VALID_SEVERITIES_ANCHORED:
        raise ReconcileParseError(
            f"ranked_violations[{i}].severity={sev} not in "
            f"{sorted(_VALID_SEVERITIES_ANCHORED)}"
        )

    skills = entry["source_skills"]
    if not isinstance(skills, list) or not skills:
        raise ReconcileParseError(
            f"ranked_violations[{i}].source_skills must be non-empty list"
        )
    seen: set[str] = set()
    for j, sk in enumerate(skills):
        if not isinstance(sk, str) or sk not in VALID_L4_SKILLS:
            raise ReconcileParseError(
                f"ranked_violations[{i}].source_skills[{j}]={sk!r} not in "
                f"VALID_L4_SKILLS"
            )
        if sk not in bundle.verdicts_by_skill:
            raise ReconcileParseError(
                f"ranked_violations[{i}].source_skills[{j}]={sk!r} not "
                f"present in input bundle"
            )
        seen.add(sk)

    # Types must be correct (int, not bool, not string) — but value
    # mismatches are auto-repaired downstream in :func:`_auto_repair_ranked`.
    # This keeps the per-entry validator cheap and focused on "is the
    # shape right?" rather than "do the numbers agree?".
    corr = entry["corroboration_count"]
    if not isinstance(corr, int) or isinstance(corr, bool):
        raise ReconcileParseError(
            f"ranked_violations[{i}].corroboration_count must be int"
        )

    uf = entry["unique_frames"]
    if not isinstance(uf, int) or isinstance(uf, bool):
        raise ReconcileParseError(
            f"ranked_violations[{i}].unique_frames must be int"
        )

    rs = entry["rank_score"]
    if not isinstance(rs, int) or isinstance(rs, bool):
        raise ReconcileParseError(
            f"ranked_violations[{i}].rank_score must be int"
        )


def _validate_tension_entry(
    entry: Any,
    *,
    i: int,
    bundle: _ClusterBundle,
) -> None:
    """Validate one tensions[*] entry."""
    if not isinstance(entry, dict):
        raise ReconcileParseError(
            f"tensions[{i}] must be dict, got {type(entry).__name__}"
        )
    missing = _TENSION_KEYS - set(entry.keys())
    if missing:
        raise ReconcileParseError(
            f"tensions[{i}] missing keys: {sorted(missing)}"
        )
    extra = set(entry.keys()) - _TENSION_KEYS
    if extra:
        raise ReconcileParseError(
            f"tensions[{i}] unexpected keys: {sorted(extra)}"
        )
    _validate_skill_pair(
        {
            "skill_a": entry["skill_a"],
            "skill_b": entry["skill_b"],
        },
        i=i,
        bundle=bundle,
        node_kind="tension",
    )
    for str_key in ("axis", "resolution"):
        v = entry[str_key]
        if not isinstance(v, str) or not v.strip():
            raise ReconcileParseError(
                f"tensions[{i}].{str_key} must be non-empty str"
            )


def _validate_gap_entry(
    entry: Any,
    *,
    i: int,
    n_quotes: int,
) -> None:
    """Validate one gaps[*] entry (shape-level — cross-check against
    gap nodes happens in the top-level parser).
    """
    if not isinstance(entry, dict):
        raise ReconcileParseError(
            f"gaps[{i}] must be dict, got {type(entry).__name__}"
        )
    missing = _GAP_KEYS - set(entry.keys())
    if missing:
        raise ReconcileParseError(f"gaps[{i}] missing keys: {sorted(missing)}")
    extra = set(entry.keys()) - _GAP_KEYS
    if extra:
        raise ReconcileParseError(
            f"gaps[{i}] unexpected keys: {sorted(extra)}"
        )
    for str_key in ("rationale", "why_missed"):
        v = entry[str_key]
        if not isinstance(v, str) or not v.strip():
            raise ReconcileParseError(
                f"gaps[{i}].{str_key} must be non-empty str"
            )
    evs = entry["evidence_source"]
    if not isinstance(evs, list) or not evs:
        raise ReconcileParseError(
            f"gaps[{i}].evidence_source must be non-empty list"
        )
    for j, src in enumerate(evs):
        if not isinstance(src, str) or src not in _VALID_EVIDENCE_SOURCES:
            raise ReconcileParseError(
                f"gaps[{i}].evidence_source[{j}]={src!r} not in "
                f"{sorted(_VALID_EVIDENCE_SOURCES)}"
            )
    idxs = entry["evidence_quote_idxs"]
    if not isinstance(idxs, list):
        raise ReconcileParseError(
            f"gaps[{i}].evidence_quote_idxs must be list"
        )
    has_quotes = "quotes" in evs
    if has_quotes and not idxs:
        raise ReconcileParseError(
            f"gaps[{i}]: 'quotes' in evidence_source but idxs empty"
        )
    if idxs and not has_quotes:
        raise ReconcileParseError(
            f"gaps[{i}]: idxs non-empty but 'quotes' absent"
        )
    for j, idx in enumerate(idxs):
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise ReconcileParseError(
                f"gaps[{i}].evidence_quote_idxs[{j}] must be int"
            )
        if not (0 <= idx < n_quotes):
            raise ReconcileParseError(
                f"gaps[{i}].evidence_quote_idxs[{j}]={idx} out of "
                f"[0, {n_quotes})"
            )


# ---------------------------------------------------------------------------
# ReconciledVerdict construction
# ---------------------------------------------------------------------------


def _build_reconciled_verdict(
    payload: dict[str, Any],
    cluster_id: str,
) -> ReconciledVerdict:
    """Translate the parsed skill payload into the persisted
    :class:`ReconciledVerdict`.

    ``ranked_violations`` (in the payload) become
    :class:`HeuristicViolation` records — the flat consumer view.
    Tensions pass through to :class:`SkillTension` records verbatim.
    The full graph + gaps + payload metadata live in the native
    sidecar, not in the ReconciledVerdict itself.
    """
    ranked: list[HeuristicViolation] = []
    for entry in payload["ranked_violations"]:
        reasoning_parts = [
            f"rank_score={entry['rank_score']} "
            f"(severity={entry['severity']} × "
            f"corroboration={entry['corroboration_count']}, "
            f"unique_frames={entry['unique_frames']})",
            f"skills=[{', '.join(entry['source_skills'])}]",
            f"rationale: {entry['rationale']}",
        ]
        ranked.append(
            HeuristicViolation(
                heuristic=entry["heuristic"],
                violation=entry["violation"],
                severity=entry["severity"],
                evidence_review_ids=[],  # inherited from L4 — L5 does not reassign
                reasoning=" | ".join(reasoning_parts),
            )
        )

    tensions: list[SkillTension] = []
    for t in payload["tensions"]:
        tensions.append(
            SkillTension(
                skill_a=t["skill_a"],
                skill_b=t["skill_b"],
                axis=t["axis"],
                resolution=t["resolution"],
            )
        )

    return ReconciledVerdict(
        cluster_id=cluster_id,
        ranked_violations=ranked,
        tensions=tensions,
    )


# ---------------------------------------------------------------------------
# Per-cluster pipeline
# ---------------------------------------------------------------------------


ReconcileStatus = Literal["audited", "fallback"]


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """One cluster's reconciliation result.

    Mirrors :class:`l4_audit.AuditOutcome` with the verdict type swapped
    — ``verdict`` is a :class:`ReconciledVerdict` here. On fallback the
    verdict has empty ``ranked_violations`` and ``tensions`` and the
    ``native_payload`` carries the raw skill response.
    """

    cluster_id: str
    verdict: ReconciledVerdict
    native_payload: dict[str, Any]
    status: ReconcileStatus
    reason: str | None = None


async def reconcile_cluster(
    cluster: InsightCluster,
    bundle: _ClusterBundle,
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str,
) -> ReconcileOutcome:
    """Reconcile one cluster. Never raises on parse failure — falls back.

    Transport-level errors propagate. If the cluster's bundle is empty
    (zero L4 verdicts), a fallback is returned immediately without a
    Claude call — there is nothing to reconcile.
    """
    if not bundle.verdicts_by_skill:
        _log.warning(
            "reconcile: cluster %s has zero L4 verdicts in bundle — "
            "emitting empty reconciled verdict",
            cluster.cluster_id,
        )
        verdict = ReconciledVerdict(
            cluster_id=cluster.cluster_id,
            ranked_violations=[],
            tensions=[],
        )
        return ReconcileOutcome(
            cluster_id=cluster.cluster_id,
            verdict=verdict,
            native_payload={
                "fallback": True,
                "reason": "no L4 verdicts in input bundle",
                "raw_response": "",
            },
            status="fallback",
            reason="no L4 verdicts in input bundle",
        )

    user = build_user_message(cluster, bundle)
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
        payload = parse_reconcile_response(
            resp.response,
            bundle=bundle,
            n_quotes=len(cluster.representative_quotes),
        )
    except ReconcileParseError as e:
        _log.warning(
            "reconcile parse failed for cluster %s: %s — falling back",
            cluster.cluster_id,
            e,
        )
        verdict = ReconciledVerdict(
            cluster_id=cluster.cluster_id,
            ranked_violations=[],
            tensions=[],
        )
        return ReconcileOutcome(
            cluster_id=cluster.cluster_id,
            verdict=verdict,
            native_payload=_fallback_native(resp.response, str(e)),
            status="fallback",
            reason=str(e),
        )

    verdict = _build_reconciled_verdict(payload, cluster.cluster_id)
    return ReconcileOutcome(
        cluster_id=cluster.cluster_id,
        verdict=verdict,
        native_payload=payload,
        status="audited",
        reason=None,
    )


async def reconcile_batch(
    clusters: list[InsightCluster],
    bundles_by_cluster: dict[str, _ClusterBundle],
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str | None = None,
) -> tuple[list[ReconcileOutcome], list[tuple[str, Exception]]]:
    """Reconcile a batch of clusters concurrently.

    Clusters without a bundle entry get an empty-bundle fallback.
    Transport errors propagate per-cluster into ``failures``.
    """
    sh = skill_hash_value if skill_hash_value is not None else skill_hash()

    async def _one(
        c: InsightCluster,
    ) -> tuple[str, ReconcileOutcome | Exception]:
        bundle = bundles_by_cluster.get(
            c.cluster_id, _ClusterBundle(c.cluster_id, {})
        )
        try:
            outcome = await reconcile_cluster(
                c,
                bundle,
                client,
                model=model,
                skill_id=skill_id,
                skill_hash_value=sh,
            )
            return (c.cluster_id, outcome)
        except Exception as e:  # noqa: BLE001 — per-cluster isolation
            return (c.cluster_id, e)

    results = await asyncio.gather(*(_one(c) for c in clusters))
    outcomes: list[ReconcileOutcome] = []
    failures: list[tuple[str, Exception]] = []
    for cid, payload in results:
        if isinstance(payload, ReconcileOutcome):
            outcomes.append(payload)
        else:
            failures.append((cid, payload))
    return outcomes, failures


def _sort_outcomes(outcomes: list[ReconcileOutcome]) -> list[ReconcileOutcome]:
    """Deterministic order by cluster_id — matches L4's sort_outcomes
    shape without importing it (AuditOutcome-typed)."""
    return sorted(outcomes, key=lambda o: o.cluster_id)


def _native_row(outcome: ReconcileOutcome) -> dict[str, Any]:
    """One native sidecar row keyed by cluster_id.

    Carries the full payload (summary, graph, ranked_violations,
    tensions, gaps) on success; a {fallback, reason, raw_response} on
    failure. Parallels :func:`l4_audit._native_row` but keyed on
    cluster_id (L5 has no skill_id × cluster_id product).
    """
    return {
        "cluster_id": outcome.cluster_id,
        "status": outcome.status,
        "payload": outcome.native_payload,
    }


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass
class _ProvenanceAccumulator:
    """Mutable accumulator for L5 reconcile provenance.

    Tallies node-type distribution, tension-axis distribution,
    corroboration-count distribution (how many violations were
    corroborated by 2, 3, 4, 5, 6 skills), and gap count.
    """

    node_type_histogram: dict[str, int] = field(
        default_factory=lambda: {t: 0 for t in VALID_NODE_TYPES}
    )
    relation_type_histogram: dict[str, int] = field(
        default_factory=lambda: {r: 0 for r in VALID_RELATION_TYPES}
    )
    tension_axis_histogram: dict[str, int] = field(default_factory=dict)
    corroboration_count_histogram: dict[int, int] = field(
        default_factory=lambda: {k: 0 for k in range(1, 7)}
    )
    total_ranked_violations: int = 0
    total_tensions: int = 0
    total_gaps: int = 0
    top_rank_score_sum: int = 0
    clusters_with_no_tensions: int = 0
    clusters_with_any_gap: int = 0


def build_provenance(
    outcomes: list[ReconcileOutcome],
    failures: list[tuple[str, Exception]],
    *,
    model: str,
) -> dict[str, Any]:
    """Summarise an L5 reconcile run into the provenance payload.

    Reviewer-facing signal: how many clusters reconciled cleanly, the
    node-type distribution across the graph, which tension axes
    dominated, corroboration structure (a distribution skewed toward 1
    means the six skills disagree often; skewed toward 3+ means the
    cluster has a clear dominant defect).
    """
    audited = [o for o in outcomes if o.status == "audited"]
    fallback = [o for o in outcomes if o.status == "fallback"]

    acc = _ProvenanceAccumulator()
    for outcome in audited:
        p = outcome.native_payload
        ranked = p.get("ranked_violations", [])
        tensions = p.get("tensions", [])
        gaps = p.get("gaps", [])

        acc.total_ranked_violations += len(ranked)
        acc.total_tensions += len(tensions)
        acc.total_gaps += len(gaps)
        if not tensions:
            acc.clusters_with_no_tensions += 1
        if gaps:
            acc.clusters_with_any_gap += 1
        if ranked:
            acc.top_rank_score_sum += int(ranked[0]["rank_score"])

        for entry in ranked:
            corr = int(entry["corroboration_count"])
            if corr in acc.corroboration_count_histogram:
                acc.corroboration_count_histogram[corr] += 1
            else:
                acc.corroboration_count_histogram[corr] = 1

        for t in tensions:
            axis = t["axis"]
            acc.tension_axis_histogram[axis] = (
                acc.tension_axis_histogram.get(axis, 0) + 1
            )

        for node in p.get("graph", {}).get("nodes", []):
            nt = node["type"]
            acc.node_type_histogram[nt] = acc.node_type_histogram.get(nt, 0) + 1
        for edge in p.get("graph", {}).get("edges", []):
            et = edge["type"]
            acc.relation_type_histogram[et] = (
                acc.relation_type_histogram.get(et, 0) + 1
            )

    n_audited = len(audited)
    mean_top = (
        acc.top_rank_score_sum / n_audited if n_audited > 0 else 0.0
    )

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "cluster_count": len(outcomes) + len(failures),
        "audited_count": n_audited,
        "fallback_count": len(fallback),
        "transport_failure_count": len(failures),
        "total_ranked_violations": acc.total_ranked_violations,
        "total_tensions": acc.total_tensions,
        "total_gaps": acc.total_gaps,
        "clusters_with_no_tensions": acc.clusters_with_no_tensions,
        "clusters_with_any_gap": acc.clusters_with_any_gap,
        "mean_top_rank_score": mean_top,
        "node_type_histogram": dict(acc.node_type_histogram),
        "relation_type_histogram": dict(acc.relation_type_histogram),
        "tension_axis_histogram": dict(acc.tension_axis_histogram),
        "corroboration_count_histogram": {
            str(k): v for k, v in sorted(acc.corroboration_count_histogram.items())
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
            "L5 reconcile — Claude call per cluster that reconciles all "
            "L4 verdicts for that cluster into a single ReconciledVerdict "
            "(ranked violations + explicit tensions + optional gaps)."
        ),
    )
    parser.add_argument(
        "--verdicts",
        type=Path,
        required=True,
        help=(
            "JSONL bundle concatenating the six L4 skills' AuditVerdict "
            "rows for the clusters to reconcile. Grouped by cluster_id "
            "at load time."
        ),
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
            f"L5 reconciled verdicts JSONL output (default: "
            f"{DEFAULT_VERDICTS})."
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
        help="Claude client mode (default: replay — reviewer-safe).",
    )
    parser.add_argument(
        "--model", default=MODEL, help=f"Claude model (default: {MODEL})."
    )
    parser.add_argument(
        "--replay-log",
        type=Path,
        default=repo_root / "data/cache/responses.jsonl",
        help="Path to the Claude replay log (default: data/cache/responses.jsonl).",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--usd-ceiling",
        type=float,
        default=5.0,
        help="Per-run USD kill-switch ceiling (live mode only).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Optional run_id; default is 'l5-YYYYmmddTHHMMSSffffff' at UTC "
            "now (microseconds avoid same-second collisions)."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    clusters = load_clusters(args.clusters)
    _log.info("loaded %d clusters from %s", len(clusters), args.clusters)
    if not clusters:
        _log.error("empty clusters input — nothing to reconcile")
        return 1

    bundles_by_cluster = load_verdicts_bundle(args.verdicts)
    _log.info(
        "loaded %d cluster bundles from %s",
        len(bundles_by_cluster),
        args.verdicts,
    )

    run_id = args.run_id or _default_run_id().replace("l4-", "l5-", 1)

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
        reconcile_batch(
            clusters,
            bundles_by_cluster,
            client,
            model=args.model,
        )
    )

    if failures:
        for cid, err in failures:
            _log.warning(
                "reconcile transport failure for %s: %s: %s",
                cid,
                type(err).__name__,
                err,
            )
        _log.error(
            "%d/%d reconciles failed at transport level",
            len(failures),
            len(clusters),
        )

    sorted_outcomes = _sort_outcomes(outcomes)

    # Verdicts file -----------------------------------------------------
    args.output.parent.mkdir(parents=True, exist_ok=True)
    clusters_hash = hash_file(args.clusters)
    verdicts_hash = hash_file(args.verdicts)

    out_meta = write_jsonl_atomic(
        args.output,
        [o.verdict.model_dump(mode="json") for o in sorted_outcomes],
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes={
            args.clusters.name: clusters_hash,
            args.verdicts.name: verdicts_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d verdicts to %s (sha256=%s…)",
        len(sorted_outcomes),
        args.output,
        out_meta.artifact_sha256[:16],
    )

    # Native payload sidecar -------------------------------------------
    args.native_output.parent.mkdir(parents=True, exist_ok=True)
    native_meta = write_jsonl_atomic(
        args.native_output,
        [_native_row(o) for o in sorted_outcomes],
        run_id=run_id,
        layer=f"{LAYER_NAME}_native",
        input_hashes={
            args.clusters.name: clusters_hash,
            args.verdicts.name: verdicts_hash,
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

    # Provenance sidecar ------------------------------------------------
    provenance_path = args.output.with_suffix(".provenance.json")
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
    _log.info("wrote L5 reconcile provenance to %s", provenance_path)

    audited_count = sum(1 for o in outcomes if o.status == "audited")
    fallback_count = sum(1 for o in outcomes if o.status == "fallback")
    _log.info(
        "L5 reconcile done. mode=%s live-spend=$%.4f "
        "audited=%d fallback=%d transport_fail=%d",
        args.mode,
        client.cumulative_usd,
        audited_count,
        fallback_count,
        len(failures),
    )

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
