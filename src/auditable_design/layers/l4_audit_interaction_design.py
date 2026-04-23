"""Layer 4 — cluster audit through Cooper's interaction-design lens.

Fifth skill to land in L4, sibling to
:mod:`auditable_design.layers.l4_audit` (Norman),
:mod:`auditable_design.layers.l4_audit_accessibility` (WCAG + Inclusive),
:mod:`auditable_design.layers.l4_audit_decision_psychology`
(Kahneman), and
:mod:`auditable_design.layers.l4_audit_business_alignment`
(Osterwalder). Follows the **separate-module pattern** established by
the other four — each L4 skill owns its ``SYSTEM_PROMPT``,
``DIMENSION_KEYS``, prompt builder, parser, and provenance; neutral
pipeline helpers are imported from :mod:`l4_audit`.

Why a separate module (not dispatch)
------------------------------------
Same rationale as the other L4 skills:

- **SKILL.md drift stays local.** Editing the interaction-design skill
  changes *this* module's ``skill_hash`` only — the other four skills'
  replay caches remain hot.
- **Per-skill output grammar.** This skill emits 4 dimensions
  (posture_platform_fit, flow_excise, idioms_learnability,
  etiquette_forgiveness) with per-finding ``posture`` (closed set of
  seven values including ``mixed`` for drift findings and
  ``not_applicable`` for cross-surface idiom claims), ``user_tier``
  (beginner / intermediate / expert / all — Cooper's perpetual-
  intermediate primacy is the core claim), and ``excise_type`` (closed
  set of five) that the other skills' parsers would reject.
- **Operator reads one file per skill.**

Input / output
--------------
* Reads the same ``data/derived/l3b_labeled_clusters.jsonl`` as the
  other L4 skills. ``InsightCluster`` already carries the optional
  ``ui_context`` / ``html`` / ``screenshot_ref`` fields the prompt
  builder threads through.
* Writes :data:`DEFAULT_VERDICTS`
  (``data/derived/l4_audit_interaction_design_verdicts.jsonl``) — one
  :class:`AuditVerdict` per input cluster.
* Writes :data:`DEFAULT_NATIVE`
  (``data/derived/l4_audit_interaction_design_verdicts.native.jsonl``)
  with the raw skill payload (``summary`` + 4-dim ``dimension_scores``
  + findings with ``posture`` / ``user_tier`` / ``excise_type`` fields).
* Sidecars (``.meta.json`` + ``.provenance.json``) written analogously
  to the other L4 skills. Provenance adds a posture histogram, a user-
  tier histogram, and an excise-type counter.

Severity remap
--------------
SKILL.md emits Nielsen 1–4 severities per finding (same scale as the
other four L4 skills) so L5/L6 aggregation does not need per-skill
special-casing. The remap uses :data:`l4_audit.NIELSEN_TO_ANCHORED`
(1→3 / 2→5 / 3→7 / 4→9, ADR-008 anchors). Interaction-design context
(``posture``, ``user_tier``, ``excise_type``, ``evidence_source``) is
preserved in the violation's ``reasoning`` field and, fully, in the
native payload sidecar.

Quote-anchoring rule
--------------------
Matches the accessibility and business-alignment skills — permissive:
interaction-design findings may rest on ``html`` / ``ui_context``
alone (e.g. a modal-excise finding on a confirmation dialog, a
posture-mismatch finding from markup). The parser enforces the
bidirectional rule instead: if ``"quotes"`` appears in
``evidence_source``, ``evidence_quote_idxs`` must be non-empty; if
``evidence_quote_idxs`` is non-empty, ``"quotes"`` must appear in
``evidence_source``.

Posture and excise discipline
-----------------------------
SKILL.md carries four rules that this parser enforces:

- A finding with ``posture == "mixed"`` at severity ≥ 3 forces its
  dimension score to ≤ 2 (mirror of Osterwalder's tension cap — a
  posture-drift finding is a structural behavioural mismatch, not a
  local fix).
- A finding with ``excise_type != "none"`` at severity ≥ 3 forces its
  dimension score to ≤ 2 (Cooper's *commensurate effort* principle:
  heavy excise compounds).
- Every finding in the ``flow_excise`` dimension must name a non-
  ``none`` ``excise_type``. Findings in the other three dimensions
  may name ``excise_type: "none"`` but are not required to — a
  posture mismatch that manifests as modal excise is legitimately
  both.
- Two findings may share a ``heuristic`` but must not share the same
  ``(heuristic, posture)`` pair. Enforced.

Fallback discipline
-------------------
Identical shape to the other L4 skills: parse failure → fallback
verdict with zero heuristic violations, reason recorded, raw response
preserved in the native sidecar.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auditable_design.claude_client import Client
from auditable_design.layers.l4_audit import (
    NIELSEN_TO_ANCHORED,
    AuditOutcome,
    AuditParseError,
    _atomic_write_bytes,
    _configure_logging,
    _default_run_id,
    _fallback_native,
    _native_row,
    _resolve_repo_root,
    _verdict_id,
    load_clusters,
    sort_outcomes,
)
from auditable_design.schemas import (
    SCHEMA_VERSION,
    AuditVerdict,
    HeuristicViolation,
    InsightCluster,
)
from auditable_design.storage import hash_file, write_jsonl_atomic

__all__ = [
    "DEFAULT_LABELED",
    "DEFAULT_NATIVE",
    "DEFAULT_VERDICTS",
    "DIMENSION_KEYS",
    "LAYER_NAME",
    "MAX_TOKENS",
    "MODEL",
    "SKILL_ID",
    "SYSTEM_PROMPT",
    "TEMPERATURE",
    "VALID_EXCISE_TYPES",
    "VALID_POSTURES",
    "VALID_USER_TIERS",
    "audit_batch",
    "audit_cluster",
    "build_provenance",
    "build_user_message",
    "main",
    "parse_audit_response",
    "skill_hash",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_ID: str = "audit-interaction-design"
LAYER_NAME: str = "l4_audit_interaction_design"

# Sonnet 4.6 is the default — same rationale as the other L4 skills:
# the task is reasoning-heavy (identifying the correct posture,
# distinguishing excise from deliberate friction, recognising idiom
# vs metaphor vs implementation-centric) but does not need Opus's
# full budget. Bump to Opus 4.7 if a matched eval shows Sonnet
# confusing posture taxonomy or mis-classifying excise types.
MODEL: str = "claude-sonnet-4-6"
TEMPERATURE: float = 0.0

# Response shape: ~10 findings, each ~140 tokens (violation +
# recommendation + evidence_source list + heuristic + posture +
# user_tier + excise_type), plus summary + 4-key dimension_scores.
# Upper bound ~2.5k output tokens; 6144 leaves headroom for a
# reasoning preamble. Billed only on actual output.
MAX_TOKENS: int = 6144

# The four Cooper dimensions the skill emits scores for. Strict
# parsing: exactly these keys, exactly these spellings.
DIMENSION_KEYS: frozenset[str] = frozenset(
    {
        "posture_platform_fit",
        "flow_excise",
        "idioms_learnability",
        "etiquette_forgiveness",
    }
)

# Valid posture codes — SKILL.md output contract. Seven values:
# the five book postures (sovereign / transient / daemonic /
# satellite / standalone), plus ``mixed`` for posture-drift
# findings, plus ``not_applicable`` for cross-surface idiom /
# learnability findings that do not localise to a posture.
VALID_POSTURES: frozenset[str] = frozenset(
    {
        "sovereign",
        "transient",
        "daemonic",
        "satellite",
        "standalone",
        "mixed",
        "not_applicable",
    }
)

# Valid user-tier codes — SKILL.md output contract. Four values.
# Cooper's perpetual-intermediate primacy: well-designed audits
# should see most findings with ``user_tier`` of ``intermediate`` or
# ``all``; findings that only hurt ``beginner`` or ``expert`` are
# narrower.
VALID_USER_TIERS: frozenset[str] = frozenset(
    {
        "beginner",
        "intermediate",
        "expert",
        "all",
    }
)

# Valid excise-type codes — SKILL.md output contract. Five values.
# The four Cooper excise categories (navigational / modal /
# skeuomorphic / stylistic) plus ``none`` for findings that are not
# excise claims (posture, idiom, affordance, etiquette).
VALID_EXCISE_TYPES: frozenset[str] = frozenset(
    {
        "navigational",
        "modal",
        "skeuomorphic",
        "stylistic",
        "none",
    }
)

# Valid evidence-source tokens — SKILL.md output contract. Identical
# set to the other L4 skills; interaction-design differs in that
# ``"quotes"`` is *not* required on every finding (unlike Kahneman).
_VALID_EVIDENCE_SOURCES: frozenset[str] = frozenset(
    {"quotes", "ui_context", "html", "screenshot"}
)

# Default paths — same labeled-clusters input as the other L4 skills,
# separate verdict + native outputs so the five skills never overwrite
# each other.
DEFAULT_LABELED = Path("data/derived/l3b_labeled_clusters.jsonl")
DEFAULT_VERDICTS = Path(
    "data/derived/l4_audit_interaction_design_verdicts.jsonl"
)
DEFAULT_NATIVE = Path(
    "data/derived/l4_audit_interaction_design_verdicts.native.jsonl"
)


# ---------------------------------------------------------------------------
# System prompt — loaded from SKILL.md
# ---------------------------------------------------------------------------


def _load_skill_body() -> str:
    """Read ``skills/audit-interaction-design/SKILL.md`` and strip
    YAML frontmatter.

    Fails at import if the file is missing: the layer cannot function
    without its skill. Same shape as the other L4 modules' loaders,
    different ``SKILL_ID``.
    """
    repo_root = _resolve_repo_root()
    path = repo_root / "skills" / SKILL_ID / "SKILL.md"
    if not path.exists():
        raise RuntimeError(
            f"{LAYER_NAME}: SKILL.md not found at {path}; "
            f"layer cannot initialise"
        )
    content = path.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            content = content[end + len("\n---\n") :]
    return content.strip()


# Changing SKILL.md → changes SYSTEM_PROMPT → changes skill_hash →
# invalidates the replay cache for prior interaction-design audits.
# Intentional (ADR-011 contract, same as the other L4 skills).
SYSTEM_PROMPT: str = _load_skill_body()


def skill_hash() -> str:
    """sha256 of :data:`SYSTEM_PROMPT`.

    Interaction-design's skill hash is independent of the other L4
    skills — editing any SKILL.md invalidates only its own cache.
    """
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_user_message(cluster: InsightCluster) -> str:
    """Render the per-cluster user message for the interaction-design
    skill.

    Shape matches SKILL.md's ``<cluster>...</cluster>`` contract and
    is byte-identical in structure to the other L4 prompts:

    * ``<label>`` — always present.
    * ``<ui_context>`` — optional, rendered iff non-None.
    * ``<html>`` — optional, rendered iff non-None, CDATA-wrapped.
    * ``<screenshot_ref>`` — optional, rendered iff non-None.
    * ``<q idx="N">`` — one per representative quote.

    Every string is XML-escaped (``&``, ``<``, ``>``) as defence in
    depth against prompt injection via quote text. The ``html`` field
    is wrapped in ``<![CDATA[...]]>`` so the model sees raw markup
    while the surrounding ``<html>``/``</html>`` tags remain the
    injection boundary.

    The prompts across all five L4 skills are deliberately shape-
    identical at the wire level so the same :class:`InsightCluster`
    can be audited by any subset without branching logic upstream;
    the divergence is in the system prompt.
    """
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})

    label_escaped = cluster.label.translate(escape)

    parts: list[str] = [
        "<cluster>",
        f"  <label>{label_escaped}</label>",
    ]

    if cluster.ui_context is not None:
        ui_ctx = cluster.ui_context.translate(escape)
        parts.append(f"  <ui_context>{ui_ctx}</ui_context>")

    if cluster.html is not None:
        parts.append(f"  <html><![CDATA[\n{cluster.html}\n]]></html>")

    if cluster.screenshot_ref is not None:
        ss = cluster.screenshot_ref.translate(escape)
        parts.append(f"  <screenshot_ref>{ss}</screenshot_ref>")

    for i, q in enumerate(cluster.representative_quotes):
        parts.append(f'  <q idx="{i}">{q.translate(escape)}</q>')

    parts.append("</cluster>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


# Greedy outermost ``{...}`` — identical primitive to the other L4
# parsers and L3b's.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_TOP_LEVEL_KEYS = {"summary", "dimension_scores", "findings"}

# Per-finding keys — interaction-design-flavoured. Relative to the
# Osterwalder shape this swaps ``building_blocks`` / ``tension`` /
# ``pattern`` for ``posture`` / ``user_tier`` / ``excise_type``.
_FINDING_KEYS = {
    "dimension",
    "heuristic",
    "posture",
    "user_tier",
    "excise_type",
    "violation",
    "severity",
    "evidence_source",
    "evidence_quote_idxs",
    "recommendation",
}

_VALID_SEVERITIES: frozenset[int] = frozenset({1, 2, 3, 4})
_VALID_DIMENSION_SCORES: frozenset[int] = frozenset({1, 2, 3, 4, 5})


def _repair_unescaped_string_quotes(raw: str, max_iters: int = 32) -> str:
    """Iteratively escape stray ``"`` that prematurely terminate JSON
    strings.

    Duplicated verbatim from the other L4 modules — same failure mode
    (Sonnet 4.6 occasionally emits literal inner double-quotes instead
    of ``\\"``). If a later refactor wants to share a single copy, the
    natural home is :mod:`l4_audit` helpers; not extracted pre-emptively
    to keep this commit's blast radius minimal.
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


def parse_audit_response(text: str, *, n_quotes: int) -> dict[str, Any]:
    """Extract and validate the interaction-design audit payload.

    Compared to the Osterwalder parser this:

    * Uses the four interaction-design dimensions.
    * Expects ``posture`` (one of :data:`VALID_POSTURES`), ``user_tier``
      (one of :data:`VALID_USER_TIERS`), and ``excise_type`` (one of
      :data:`VALID_EXCISE_TYPES`) on every finding.
    * Does **not** require ``"quotes"`` in every ``evidence_source``
      (SKILL.md: interaction-design findings can rest on ``html`` /
      ``ui_context`` alone — e.g. a modal-excise finding on a dialog).
    * Enforces the bidirectional quotes↔idxs rule instead: ``"quotes"``
      in ``evidence_source`` ↔ non-empty ``evidence_quote_idxs``.
    * Enforces the posture-cap rule: a finding with
      ``posture == "mixed"`` at severity ≥ 3 forces its dimension score
      to ≤ 2.
    * Enforces the excise-cap rule: a finding with
      ``excise_type != "none"`` at severity ≥ 3 forces its dimension
      score to ≤ 2.
    * Enforces the flow_excise-requires-excise rule: every finding in
      the ``flow_excise`` dimension must name a non-``none``
      ``excise_type``.
    * Enforces ``(heuristic, posture)`` uniqueness across findings.

    ``n_quotes`` is the number of ``<q>`` tags in the prompt; every
    ``evidence_quote_idxs`` entry must be in ``range(n_quotes)``.

    On success returns the parsed payload dict with these guarantees:

    * ``summary`` is a non-empty string.
    * ``dimension_scores`` is a dict with exactly the four
      :data:`DIMENSION_KEYS`, each an int in ``{1, 2, 3, 4, 5}``.
    * ``findings`` is a list; each entry has exactly :data:`_FINDING_KEYS`,
      all typed, ranged, and business-rule-consistent.

    Raises:
        AuditParseError: On any structural, type, or business-rule
            violation, with a message identifying the offending field.
    """
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise AuditParseError(f"no JSON object found in response: {text!r}")
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as first_err:
        # Same repair pass as the other L4 modules; same Sonnet-4.6-era
        # failure mode.
        repaired = _repair_unescaped_string_quotes(raw)
        if repaired == raw:
            raise AuditParseError(
                f"malformed JSON: {first_err}; text={text!r}"
            ) from first_err
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as retry_err:
            raise AuditParseError(
                f"malformed JSON: {first_err}; "
                f"sanitised retry also failed: {retry_err}; text={text!r}"
            ) from retry_err
    if not isinstance(data, dict):
        raise AuditParseError(f"expected JSON object, got {type(data).__name__}")

    actual = set(data.keys())
    missing = _TOP_LEVEL_KEYS - actual
    if missing:
        raise AuditParseError(f"missing required top-level keys: {sorted(missing)}")
    extra = actual - _TOP_LEVEL_KEYS
    if extra:
        raise AuditParseError(f"unexpected top-level keys: {sorted(extra)}")

    # --- summary -----------------------------------------------------
    summary = data["summary"]
    if not isinstance(summary, str):
        raise AuditParseError(
            f"'summary' must be str, got {type(summary).__name__}"
        )
    if not summary.strip():
        raise AuditParseError("'summary' must be non-empty")

    # --- dimension_scores --------------------------------------------
    dscores = data["dimension_scores"]
    if not isinstance(dscores, dict):
        raise AuditParseError(
            f"'dimension_scores' must be dict, got {type(dscores).__name__}"
        )
    ds_actual = set(dscores.keys())
    ds_missing = DIMENSION_KEYS - ds_actual
    if ds_missing:
        raise AuditParseError(f"dimension_scores missing keys: {sorted(ds_missing)}")
    ds_extra = ds_actual - DIMENSION_KEYS
    if ds_extra:
        raise AuditParseError(
            f"dimension_scores has unexpected keys: {sorted(ds_extra)}"
        )
    for k, v in dscores.items():
        if not isinstance(v, int) or isinstance(v, bool):
            raise AuditParseError(
                f"dimension_scores[{k!r}] must be int, got {type(v).__name__}"
            )
        if v not in _VALID_DIMENSION_SCORES:
            raise AuditParseError(
                f"dimension_scores[{k!r}]={v} out of {{1,2,3,4,5}}"
            )

    # --- findings ----------------------------------------------------
    findings = data["findings"]
    if not isinstance(findings, list):
        raise AuditParseError(
            f"'findings' must be list, got {type(findings).__name__}"
        )
    for i, finding in enumerate(findings):
        _validate_finding(finding, i=i, n_quotes=n_quotes)

    # --- cross-finding consistency ----------------------------------
    # SKILL.md: a finding with posture="mixed" at severity ≥ 3 forces
    # its dimension score to ≤ 2 (posture-drift is a structural
    # behavioural mismatch, not a local fix).
    for i, finding in enumerate(findings):
        if finding["posture"] == "mixed" and finding["severity"] >= 3:
            dim = finding["dimension"]
            score = dscores[dim]
            if score > 2:
                raise AuditParseError(
                    f"findings[{i}] has posture='mixed' "
                    f"at severity {finding['severity']} in dimension "
                    f"{dim!r}, but dimension_scores[{dim!r}]={score} > 2 "
                    f"(SKILL.md rule: posture mismatch at sev ≥ 3 "
                    f"forces dimension ≤ 2)"
                )

    # SKILL.md: a finding with excise_type != "none" at severity ≥ 3
    # forces its dimension score to ≤ 2 (Cooper's commensurate-effort
    # principle — heavy excise compounds).
    for i, finding in enumerate(findings):
        if finding["excise_type"] != "none" and finding["severity"] >= 3:
            dim = finding["dimension"]
            score = dscores[dim]
            if score > 2:
                raise AuditParseError(
                    f"findings[{i}] has excise_type="
                    f"{finding['excise_type']!r} at severity "
                    f"{finding['severity']} in dimension {dim!r}, but "
                    f"dimension_scores[{dim!r}]={score} > 2 "
                    f"(SKILL.md rule: excise at sev ≥ 3 forces "
                    f"dimension ≤ 2)"
                )

    # SKILL.md: every finding in the flow_excise dimension must name
    # a non-"none" excise_type. Findings in other dimensions may
    # legitimately be excise findings too (a posture mismatch that
    # manifests as modal excise), so we only police the direction
    # flow_excise → non-none.
    for i, finding in enumerate(findings):
        if (
            finding["dimension"] == "flow_excise"
            and finding["excise_type"] == "none"
        ):
            raise AuditParseError(
                f"findings[{i}] is in dimension 'flow_excise' but "
                f"excise_type='none' — SKILL.md rule: every finding "
                f"in flow_excise must name a non-'none' excise_type"
            )

    # SKILL.md also requires no two findings to share the same
    # (heuristic, posture) pair — two findings may share a heuristic
    # when the posture differs but not when both are identical.
    # Checked in one pass over the list.
    seen_pairs: set[tuple[str, str]] = set()
    for i, finding in enumerate(findings):
        pair = (finding["heuristic"], finding["posture"])
        if pair in seen_pairs:
            raise AuditParseError(
                f"findings[{i}] repeats (heuristic, posture) pair "
                f"{pair!r} — SKILL.md forbids duplicates"
            )
        seen_pairs.add(pair)

    return data


def _validate_finding(finding: Any, *, i: int, n_quotes: int) -> None:
    """Validate one finding dict in place.

    Per-finding rules specific to this skill:

    * ``heuristic``, ``violation``, ``recommendation`` are non-empty
      strings.
    * ``posture`` is one of :data:`VALID_POSTURES`.
    * ``user_tier`` is one of :data:`VALID_USER_TIERS`.
    * ``excise_type`` is one of :data:`VALID_EXCISE_TYPES`.
    * ``severity`` ∈ {1, 2, 3, 4}.
    * ``evidence_source`` is a non-empty list of codes from
      :data:`_VALID_EVIDENCE_SOURCES`, no duplicates.
    * Bidirectional quotes↔idxs rule: ``"quotes"`` in
      ``evidence_source`` ↔ non-empty ``evidence_quote_idxs``.
    * ``evidence_quote_idxs`` entries are valid indices into the
      prompt's ``<q>`` list.
    """
    if not isinstance(finding, dict):
        raise AuditParseError(
            f"findings[{i}] must be dict, got {type(finding).__name__}"
        )
    f_actual = set(finding.keys())
    f_missing = _FINDING_KEYS - f_actual
    if f_missing:
        raise AuditParseError(f"findings[{i}] missing keys: {sorted(f_missing)}")
    f_extra = f_actual - _FINDING_KEYS
    if f_extra:
        raise AuditParseError(f"findings[{i}] unexpected keys: {sorted(f_extra)}")

    # --- dimension --------------------------------------------------
    dim = finding["dimension"]
    if dim not in DIMENSION_KEYS:
        raise AuditParseError(
            f"findings[{i}].dimension={dim!r} not in {sorted(DIMENSION_KEYS)}"
        )

    # --- string fields ---------------------------------------------
    for str_key in ("heuristic", "violation", "recommendation"):
        val = finding[str_key]
        if not isinstance(val, str):
            raise AuditParseError(
                f"findings[{i}].{str_key} must be str, got {type(val).__name__}"
            )
        if not val.strip():
            raise AuditParseError(
                f"findings[{i}].{str_key} must be non-empty"
            )

    # --- posture ---------------------------------------------------
    posture = finding["posture"]
    if not isinstance(posture, str):
        raise AuditParseError(
            f"findings[{i}].posture must be str, got {type(posture).__name__}"
        )
    if posture not in VALID_POSTURES:
        raise AuditParseError(
            f"findings[{i}].posture={posture!r} not in "
            f"{sorted(VALID_POSTURES)}"
        )

    # --- user_tier -------------------------------------------------
    user_tier = finding["user_tier"]
    if not isinstance(user_tier, str):
        raise AuditParseError(
            f"findings[{i}].user_tier must be str, "
            f"got {type(user_tier).__name__}"
        )
    if user_tier not in VALID_USER_TIERS:
        raise AuditParseError(
            f"findings[{i}].user_tier={user_tier!r} not in "
            f"{sorted(VALID_USER_TIERS)}"
        )

    # --- excise_type ----------------------------------------------
    excise_type = finding["excise_type"]
    if not isinstance(excise_type, str):
        raise AuditParseError(
            f"findings[{i}].excise_type must be str, "
            f"got {type(excise_type).__name__}"
        )
    if excise_type not in VALID_EXCISE_TYPES:
        raise AuditParseError(
            f"findings[{i}].excise_type={excise_type!r} not in "
            f"{sorted(VALID_EXCISE_TYPES)}"
        )

    # --- severity --------------------------------------------------
    sev = finding["severity"]
    if not isinstance(sev, int) or isinstance(sev, bool):
        raise AuditParseError(
            f"findings[{i}].severity must be int, got {type(sev).__name__}"
        )
    if sev not in _VALID_SEVERITIES:
        raise AuditParseError(
            f"findings[{i}].severity={sev} out of {{1,2,3,4}}"
        )

    # --- evidence_source -------------------------------------------
    esources = finding["evidence_source"]
    if not isinstance(esources, list):
        raise AuditParseError(
            f"findings[{i}].evidence_source must be list, "
            f"got {type(esources).__name__}"
        )
    if not esources:
        raise AuditParseError(
            f"findings[{i}].evidence_source must be non-empty"
        )
    for j, src in enumerate(esources):
        if not isinstance(src, str):
            raise AuditParseError(
                f"findings[{i}].evidence_source[{j}] must be str, "
                f"got {type(src).__name__}"
            )
        if src not in _VALID_EVIDENCE_SOURCES:
            raise AuditParseError(
                f"findings[{i}].evidence_source[{j}]={src!r} not in "
                f"{sorted(_VALID_EVIDENCE_SOURCES)}"
            )
    if len(set(esources)) != len(esources):
        raise AuditParseError(
            f"findings[{i}].evidence_source contains duplicates: {esources!r}"
        )

    # --- evidence_quote_idxs ---------------------------------------
    # Interaction-design permits markup/context-only findings (unlike
    # Kahneman). Enforce the bidirectional rule: "quotes" in source
    # ↔ non-empty idxs.
    idxs = finding["evidence_quote_idxs"]
    if not isinstance(idxs, list):
        raise AuditParseError(
            f"findings[{i}].evidence_quote_idxs must be list, "
            f"got {type(idxs).__name__}"
        )
    has_quotes_src = "quotes" in esources
    if has_quotes_src and not idxs:
        raise AuditParseError(
            f"findings[{i}].evidence_source includes 'quotes' but "
            f"evidence_quote_idxs is empty — SKILL.md rule: 'quotes' "
            f"in evidence_source requires non-empty quote idxs"
        )
    if idxs and not has_quotes_src:
        raise AuditParseError(
            f"findings[{i}].evidence_quote_idxs={idxs!r} is non-empty "
            f"but 'quotes' is not in evidence_source={esources!r} — "
            f"SKILL.md rule: non-empty quote idxs requires 'quotes' "
            f"in evidence_source"
        )
    for j, idx in enumerate(idxs):
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise AuditParseError(
                f"findings[{i}].evidence_quote_idxs[{j}] must be int, "
                f"got {type(idx).__name__}"
            )
        if not (0 <= idx < n_quotes):
            raise AuditParseError(
                f"findings[{i}].evidence_quote_idxs[{j}]={idx} out of "
                f"[0, {n_quotes})"
            )


# ---------------------------------------------------------------------------
# Violation construction
# ---------------------------------------------------------------------------


def _build_heuristic_violations(
    payload: dict[str, Any],
    cluster: InsightCluster,
) -> list[HeuristicViolation]:
    """Translate interaction-design findings into
    :class:`HeuristicViolation` records.

    The audit-contract :class:`HeuristicViolation` has no dedicated
    fields for ``posture`` / ``user_tier`` / ``excise_type`` /
    ``evidence_source``; we encode them into the ``reasoning`` string,
    and the full structured copy lives in the native payload sidecar.
    Same strategy the other L4 modules use for their skill-specific
    metadata.

    ``evidence_review_ids`` is left empty for the same reason as the
    other L4 modules: quotes lack an explicit back-mapping to review
    ids, and fuzzy substring matching is fragile. Upstream L3 populates
    this in a later pass.
    """
    violations: list[HeuristicViolation] = []
    for finding in payload["findings"]:
        nielsen = finding["severity"]
        anchored = NIELSEN_TO_ANCHORED[nielsen]
        posture = finding["posture"]
        user_tier = finding["user_tier"]
        excise_type = finding["excise_type"]
        sources = finding["evidence_source"]

        quote_refs_inner = "; ".join(
            f"q[{idx}]={cluster.representative_quotes[idx]!r}"
            for idx in finding["evidence_quote_idxs"]
        )
        sources_tag = "+".join(sources)
        excise_tag = (
            f"excise: {excise_type}" if excise_type != "none" else "excise: none"
        )

        reasoning = (
            f"[{finding['dimension']}] "
            f"(posture: {posture}; tier: {user_tier}; {excise_tag}) "
            f"{finding['violation']} "
            f"Recommendation: {finding['recommendation']} "
            f"Evidence ({sources_tag}): {quote_refs_inner or '—'} "
            f"(severity: Nielsen {nielsen} → anchored {anchored})"
        )

        violations.append(
            HeuristicViolation(
                heuristic=finding["heuristic"],
                violation=finding["violation"],
                severity=anchored,
                evidence_review_ids=[],
                reasoning=reasoning,
            )
        )
    return violations


# ---------------------------------------------------------------------------
# Per-cluster pipeline
# ---------------------------------------------------------------------------


async def audit_cluster(
    cluster: InsightCluster,
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str,
) -> AuditOutcome:
    """Audit one cluster through the interaction-design lens.

    Never raises on parse failure — falls back. Transport errors still
    propagate so the caller can distinguish a parse miss from a broken
    pipe. Mirror of the other L4 modules' ``audit_cluster`` with the
    interaction-design prompt + parser substituted.
    """
    user = build_user_message(cluster)
    resp = await client.call(
        system=SYSTEM_PROMPT,
        user=user,
        model=model,
        skill_id=skill_id,
        skill_hash=skill_hash_value,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )

    verdict_id = _verdict_id(skill_id, cluster.cluster_id)
    produced_at = datetime.now(UTC)
    native_ref = f"{DEFAULT_NATIVE.name}#{verdict_id}"

    try:
        payload = parse_audit_response(
            resp.response,
            n_quotes=len(cluster.representative_quotes),
        )
    except AuditParseError as e:
        _log.warning(
            "audit parse failed for cluster %s: %s — falling back",
            cluster.cluster_id,
            e,
        )
        verdict = AuditVerdict(
            verdict_id=verdict_id,
            cluster_id=cluster.cluster_id,
            skill_id=skill_id,
            relevant_heuristics=[],
            native_payload_ref=native_ref,
            produced_at=produced_at,
            claude_model=model,
            skill_hash=skill_hash_value,
        )
        return AuditOutcome(
            cluster_id=cluster.cluster_id,
            verdict=verdict,
            native_payload=_fallback_native(resp.response, str(e)),
            status="fallback",
            reason=str(e),
        )

    violations = _build_heuristic_violations(payload, cluster)
    verdict = AuditVerdict(
        verdict_id=verdict_id,
        cluster_id=cluster.cluster_id,
        skill_id=skill_id,
        relevant_heuristics=violations,
        native_payload_ref=native_ref,
        produced_at=produced_at,
        claude_model=model,
        skill_hash=skill_hash_value,
    )
    return AuditOutcome(
        cluster_id=cluster.cluster_id,
        verdict=verdict,
        native_payload=payload,
        status="audited",
        reason=None,
    )


async def audit_batch(
    clusters: list[InsightCluster],
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str | None = None,
) -> tuple[list[AuditOutcome], list[tuple[str, Exception]]]:
    """Audit a list of clusters concurrently. Shape-identical to the
    other L4 modules' ``audit_batch``."""
    sh = skill_hash_value if skill_hash_value is not None else skill_hash()

    async def _one(c: InsightCluster) -> tuple[str, AuditOutcome | Exception]:
        try:
            outcome = await audit_cluster(
                c,
                client,
                model=model,
                skill_id=skill_id,
                skill_hash_value=sh,
            )
            return (c.cluster_id, outcome)
        except Exception as e:  # noqa: BLE001 — per-cluster isolation
            return (c.cluster_id, e)

    results = await asyncio.gather(*(_one(c) for c in clusters))
    outcomes: list[AuditOutcome] = []
    failures: list[tuple[str, Exception]] = []
    for cid, payload in results:
        if isinstance(payload, AuditOutcome):
            outcomes.append(payload)
        else:
            failures.append((cid, payload))
    return outcomes, failures


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass
class _ProvenanceAccumulator:
    """Mutable accumulator for interaction-design audit provenance.

    Adds Cooper-specific tallies (posture histogram, user-tier
    histogram, excise-type counter, plus a mixed-posture gauge and a
    no-excise gauge) on top of the dim-score totals and severity
    histogram the other L4 modules record.
    """

    dimension_score_totals: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in DIMENSION_KEYS}
    )
    findings_count: int = 0
    severity_histogram: dict[int, int] = field(
        default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0}
    )
    posture_histogram: dict[str, int] = field(
        default_factory=lambda: {p: 0 for p in VALID_POSTURES}
    )
    user_tier_histogram: dict[str, int] = field(
        default_factory=lambda: {t: 0 for t in VALID_USER_TIERS}
    )
    excise_type_histogram: dict[str, int] = field(
        default_factory=lambda: {e: 0 for e in VALID_EXCISE_TYPES}
    )
    # Count of findings with posture == "mixed" — convenience gauge
    # for "how often does a cluster surface posture drift".
    mixed_posture_findings: int = 0
    # Count of findings with excise_type != "none" — convenience
    # gauge for "how much of this cluster is an excise story".
    excise_findings: int = 0


def build_provenance(
    outcomes: list[AuditOutcome],
    failures: list[tuple[str, Exception]],
    *,
    model: str,
) -> dict[str, Any]:
    """Summarise an interaction-design L4 run into the provenance
    payload.

    Shape parallels the Osterwalder ``build_provenance`` with Cooper-
    specific extensions (posture histogram, user-tier histogram,
    excise-type histogram, and mixed-posture / excise gauges). These
    let a reviewer see which postures dominate the audit, whether
    findings fall on beginners / intermediates / experts, and how
    often excise fires without opening the native sidecar.
    """
    audited = [o for o in outcomes if o.status == "audited"]
    fallback = [o for o in outcomes if o.status == "fallback"]

    acc = _ProvenanceAccumulator()
    for outcome in audited:
        payload = outcome.native_payload
        for k in DIMENSION_KEYS:
            acc.dimension_score_totals[k] += int(payload["dimension_scores"][k])
        for finding in payload["findings"]:
            acc.findings_count += 1
            sev = int(finding["severity"])
            acc.severity_histogram[sev] = acc.severity_histogram.get(sev, 0) + 1
            posture = finding["posture"]
            acc.posture_histogram[posture] = (
                acc.posture_histogram.get(posture, 0) + 1
            )
            tier = finding["user_tier"]
            acc.user_tier_histogram[tier] = (
                acc.user_tier_histogram.get(tier, 0) + 1
            )
            excise = finding["excise_type"]
            acc.excise_type_histogram[excise] = (
                acc.excise_type_histogram.get(excise, 0) + 1
            )
            if posture == "mixed":
                acc.mixed_posture_findings += 1
            if excise != "none":
                acc.excise_findings += 1

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "cluster_count": len(outcomes) + len(failures),
        "audited_count": len(audited),
        "fallback_count": len(fallback),
        "transport_failure_count": len(failures),
        "dimension_score_totals": dict(acc.dimension_score_totals),
        "findings_count": acc.findings_count,
        "nielsen_severity_histogram": dict(acc.severity_histogram),
        "posture_histogram": dict(acc.posture_histogram),
        "user_tier_histogram": dict(acc.user_tier_histogram),
        "excise_type_histogram": dict(acc.excise_type_histogram),
        "mixed_posture_findings": acc.mixed_posture_findings,
        "excise_findings": acc.excise_findings,
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
            "L4 interaction-design audit — one-shot Claude call per "
            "L3b cluster through Cooper's About Face lens (posture, "
            "flow & excise, idioms & learnability, etiquette & "
            "forgiveness)."
        ),
    )
    parser.add_argument(
        "--clusters",
        type=Path,
        default=repo_root / DEFAULT_LABELED,
        help=f"L3b labeled clusters JSONL (default: {DEFAULT_LABELED}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / DEFAULT_VERDICTS,
        help=(
            f"L4 interaction-design verdicts JSONL output "
            f"(default: {DEFAULT_VERDICTS})."
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
    parser.add_argument("--concurrency", type=int, default=6)
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
            "Optional run_id; default is 'l4-YYYYmmddTHHMMSSffffff' at UTC "
            "now (microseconds avoid same-second collisions). Same prefix "
            "as the other L4 skills so L5 ingests all five as layer-4 peers."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    clusters = load_clusters(args.clusters)
    _log.info("loaded %d clusters from %s", len(clusters), args.clusters)

    if not clusters:
        _log.error("empty clusters input — nothing to audit")
        return 1

    run_id = args.run_id or _default_run_id()

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
        audit_batch(
            clusters,
            client,
            model=args.model,
        )
    )

    if failures:
        for cid, err in failures:
            _log.warning(
                "audit transport failure for %s: %s: %s",
                cid,
                type(err).__name__,
                err,
            )
        _log.error(
            "%d/%d audits failed at transport level",
            len(failures),
            len(clusters),
        )

    sorted_outcomes = sort_outcomes(outcomes)

    # Verdicts file -----------------------------------------------------
    args.output.parent.mkdir(parents=True, exist_ok=True)
    clusters_hash = hash_file(args.clusters)

    out_meta = write_jsonl_atomic(
        args.output,
        [o.verdict.model_dump(mode="json") for o in sorted_outcomes],
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes={args.clusters.name: clusters_hash},
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
        input_hashes={args.clusters.name: clusters_hash},
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
    _log.info(
        "wrote L4 interaction-design run provenance to %s",
        provenance_path,
    )

    audited_count = sum(1 for o in outcomes if o.status == "audited")
    fallback_count = sum(1 for o in outcomes if o.status == "fallback")
    _log.info(
        "L4 interaction-design done. mode=%s live-spend=$%.4f "
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
