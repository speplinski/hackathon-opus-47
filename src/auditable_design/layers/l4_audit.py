"""Layer 4 — cluster audit (Claude-backed).

L3b writes ``data/derived/l3b_labeled_clusters.jsonl`` — a list of
:class:`InsightCluster` records with human-readable labels. L4 audits
each cluster through one or more skill lenses and emits
:class:`AuditVerdict` records.

This implementation is **thin-spine**: one cluster × one skill
(``audit-usability-fundamentals`` — the Norman framework) to prove the
contract end-to-end before widening to the six-skill audit matrix
(ARCHITECTURE.md §4.5). Adding a second skill later is additive — same
parse/outcome shape, different SKILL.md and ``SKILL_ID`` constant.

Input / output
--------------
* Reads :data:`DEFAULT_LABELED` (``data/derived/l3b_labeled_clusters.jsonl``).
* Writes :data:`DEFAULT_VERDICTS` (``data/derived/l4_audit_verdicts.jsonl``)
  — one :class:`AuditVerdict` per input cluster (thin-spine cardinality).
* Writes the **native payload sidecar**
  (``data/derived/l4_audit_verdicts.native.jsonl``) — one row per verdict
  keyed by ``verdict_id``, carrying the raw skill output (``summary``,
  ``dimension_scores``, ``findings``). ``AuditVerdict.native_payload_ref``
  points at this file as ``"l4_audit_verdicts.native.jsonl#<verdict_id>"``.
* Sidecar ``.meta.json`` via :func:`storage.write_jsonl_atomic`, with
  ``skill_hashes={SKILL_ID: <skill-body-hash>}``.
* Sidecar ``.provenance.json`` with per-dimension summary counts and the
  fallback breakdown.

Severity remap
--------------
The Norman skill emits severities on Nielsen's 1–4 scale (its natural
vocabulary). :class:`HeuristicViolation.severity` is the audit-contract
0–10 range (ADR-008 anchors: 3=cosmetic, 6=material, 9=critical). The
mapping is:

* Nielsen 1 (Cosmetic)      → 3  (cosmetic anchor)
* Nielsen 2 (Minor)         → 5  (between cosmetic and material)
* Nielsen 3 (Major)         → 7  (between material and critical)
* Nielsen 4 (Catastrophic)  → 9  (critical anchor)

The raw Nielsen severity is preserved in the native payload so a
reviewer can reconstruct the original scale if needed.

Fallback discipline
-------------------
If the skill's response cannot be parsed or validated (malformed JSON,
missing required keys, invalid dimension names, out-of-range severities,
invalid quote indices), the cluster gets a **fallback verdict** with
zero heuristic violations and the parse reason recorded. The output file
remains a total function of the input — every cluster_id in → every
cluster_id out — but fallback verdicts are visibly flagged by an empty
``relevant_heuristics`` list and a populated ``native_payload_ref``
pointing to the raw skill response. Transport-level failures still
propagate so the caller can distinguish a parse failure from a broken
pipe.

Determinism
-----------
* ``temperature=0.0`` (dropped by Opus 4.7 per ``claude_client``; still
  recorded in key_hash for reproducibility).
* One call per cluster — output order matches input order (sorted by
  ``cluster_id`` on write).
* Replay cache keyed on (skill_id, skill_hash, model, temperature,
  max_tokens, system, user) — identical reruns cost zero. Editing
  SKILL.md changes ``skill_hash`` and invalidates the cache, which is
  intentional and matches L2/L3b's contract.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from auditable_design.claude_client import Client
from auditable_design.schemas import (
    SCHEMA_VERSION,
    AuditVerdict,
    HeuristicViolation,
    InsightCluster,
)
from auditable_design.storage import hash_file, read_jsonl, write_jsonl_atomic

__all__ = [
    "DEFAULT_LABELED",
    "DEFAULT_NATIVE",
    "DEFAULT_VERDICTS",
    "DIMENSION_KEYS",
    "LAYER_NAME",
    "MAX_TOKENS",
    "MODEL",
    "NIELSEN_TO_ANCHORED",
    "SKILL_ID",
    "SYSTEM_PROMPT",
    "TEMPERATURE",
    "AuditOutcome",
    "AuditParseError",
    "audit_batch",
    "audit_cluster",
    "build_user_message",
    "main",
    "parse_audit_response",
    "skill_hash",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_ID: str = "audit-usability-fundamentals"
LAYER_NAME: str = "l4_audit"

# Sonnet 4.6 is the default for audit: the task is reasoning-heavy
# (mapping a cluster of complaints to Norman heuristics + severity
# judgment) but does not need Opus-grade reasoning for the thin spine.
# Haiku would be under-powered for multi-dimensional synthesis; Opus
# burns budget that the replay cache then wastes on reruns. If a future
# eval shows Sonnet mis-maps Norman dimensions or under-weights
# learned-helplessness markers, bump to Opus 4.7.
MODEL: str = "claude-sonnet-4-6"
TEMPERATURE: float = 0.0
# Response is a structured JSON with up to ~8 findings, each ~80 tokens
# (violation + recommendation + evidence + heuristic id), plus summary +
# dimension_scores scaffolding. Upper-bound budget of ~1.5k tokens; 4096
# leaves ~2.5x headroom for any reasoning preamble the model emits before
# the JSON object. Billed only on actual output, so the headroom is free.
MAX_TOKENS: int = 4096

# The four Norman dimensions the skill emits scores for. Parsing is
# strict: exactly these keys, exactly these spellings. Drift would make
# the audit un-aggregatable across runs.
DIMENSION_KEYS: frozenset[str] = frozenset(
    {
        "interaction_fundamentals",
        "action_cognition",
        "error_architecture",
        "system_maturity",
    }
)

# Nielsen 1-4 → anchored 0-10 (ADR-008). Frozen at import; any change is
# a semantic shift in how audits feed into L5/L6 and must be a PR in its
# own right.
NIELSEN_TO_ANCHORED: dict[int, int] = {1: 3, 2: 5, 3: 7, 4: 9}

# Default paths — relative to repo root, resolved in main().
DEFAULT_LABELED = Path("data/derived/l3b_labeled_clusters.jsonl")
DEFAULT_VERDICTS = Path("data/derived/l4_audit_verdicts.jsonl")
DEFAULT_NATIVE = Path("data/derived/l4_audit_verdicts.native.jsonl")


# ---------------------------------------------------------------------------
# System prompt — loaded from SKILL.md
# ---------------------------------------------------------------------------


def _resolve_repo_root() -> Path:
    """Walk up to find pyproject.toml. Duplicated from l3b_label /
    l3_cluster / l2_structure — fourth layer to need it, so the TODO to
    extract into ``cli_utils`` has crossed the threshold of justifying
    itself. Not done in this change because the extraction is orthogonal
    to L4's contract and would widen a thin-spine PR beyond review.
    """
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("cannot locate repo root (no pyproject.toml above this file)")


def _load_skill_body() -> str:
    """Read ``skills/audit-usability-fundamentals/SKILL.md`` and strip
    YAML frontmatter.

    Fails at import if the file is missing: the layer cannot function
    without its skill. Matches L3b's loader shape.
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


# Changing SKILL.md → changes SYSTEM_PROMPT → changes skill_hash →
# invalidates the replay cache for prior L4 runs. Intentional — matches
# the contract ADR-011 defines for every skill-backed layer.
SYSTEM_PROMPT: str = _load_skill_body()


def skill_hash() -> str:
    """sha256 of :data:`SYSTEM_PROMPT`.

    The hash IS the identity of the auditor's brain. Every
    :meth:`claude_client.Client.call` invocation is keyed on this, so
    any skill edit forces a re-run rather than silent reuse of stale
    verdicts.
    """
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_user_message(cluster: InsightCluster) -> str:
    """Render the per-cluster user message for the audit skill.

    Shape matches SKILL.md's ``<cluster><label/><q idx="N">...</q>...</cluster>``
    contract. Every quote is escaped the same way L3b escapes them (``<``,
    ``>``, ``&``) — the cluster wrapper and ``<q>`` tags are the injection
    boundary.

    The ``idx`` attribute on each ``<q>`` is the same index the skill
    must use in ``evidence_quote_idxs``. Keeping these explicit in the
    prompt prevents the model from inferring indices from position and
    drifting off-by-one.
    """
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})

    label_escaped = cluster.label.translate(escape)
    quotes_inner = "\n".join(
        f'  <q idx="{i}">{q.translate(escape)}</q>'
        for i, q in enumerate(cluster.representative_quotes)
    )
    return (
        f"<cluster>\n"
        f"  <label>{label_escaped}</label>\n"
        f"{quotes_inner}\n"
        f"</cluster>"
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class AuditParseError(ValueError):
    """Raised when a Claude response cannot be coerced into a valid
    audit payload.

    Caught by :func:`audit_cluster` and converted into an
    :class:`AuditOutcome` with status ``"fallback"`` — a fallback is
    recorded, not raised. The exception class exists so the layer
    runner can tell an audit parse failure apart from a transport-level
    error (which still propagates).
    """


# Greedy outermost ``{...}`` with DOTALL — same primitive as L2/L3b.
# The audit payload is much richer than L3b's single-key response, but
# the tolerance for leading prose / code fences is identical.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Allowed top-level keys in the skill's output.
_TOP_LEVEL_KEYS = {"summary", "dimension_scores", "findings"}

# Allowed per-finding keys.
_FINDING_KEYS = {
    "dimension",
    "heuristic",
    "violation",
    "severity",
    "evidence_quote_idxs",
    "recommendation",
}

# Valid Nielsen severity values (skill-native scale).
_VALID_SEVERITIES: frozenset[int] = frozenset({1, 2, 3, 4})

# Valid dimension-score values (1–5 per SKILL.md dimension table).
_VALID_DIMENSION_SCORES: frozenset[int] = frozenset({1, 2, 3, 4, 5})


def parse_audit_response(text: str, *, n_quotes: int) -> dict[str, Any]:
    """Extract and validate the audit payload from a Claude response.

    ``n_quotes`` is the number of ``<q>`` tags in the prompt; every
    ``evidence_quote_idxs`` entry must be in ``range(n_quotes)``.

    On success returns the parsed payload dict with these guarantees:

    * ``summary`` is a non-empty string.
    * ``dimension_scores`` is a dict with exactly the four
      :data:`DIMENSION_KEYS` keys, each an int in ``{1, 2, 3, 4, 5}``.
    * ``findings`` is a list; each entry has exactly :data:`_FINDING_KEYS`,
      all typed and ranged correctly.

    Raises:
        AuditParseError: On any structural or type violation, with a
            message that identifies the offending field.
    """
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise AuditParseError(f"no JSON object found in response: {text!r}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AuditParseError(f"malformed JSON: {e}; text={text!r}") from e
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
        # Bool is a subclass of int in Python; reject it explicitly so a
        # "true"/"false" doesn't quietly coerce to 1/0.
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
        if not isinstance(finding, dict):
            raise AuditParseError(
                f"findings[{i}] must be dict, got {type(finding).__name__}"
            )
        f_actual = set(finding.keys())
        f_missing = _FINDING_KEYS - f_actual
        if f_missing:
            raise AuditParseError(
                f"findings[{i}] missing keys: {sorted(f_missing)}"
            )
        f_extra = f_actual - _FINDING_KEYS
        if f_extra:
            raise AuditParseError(
                f"findings[{i}] unexpected keys: {sorted(f_extra)}"
            )
        dim = finding["dimension"]
        if dim not in DIMENSION_KEYS:
            raise AuditParseError(
                f"findings[{i}].dimension={dim!r} not in {sorted(DIMENSION_KEYS)}"
            )
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
        sev = finding["severity"]
        if not isinstance(sev, int) or isinstance(sev, bool):
            raise AuditParseError(
                f"findings[{i}].severity must be int, got {type(sev).__name__}"
            )
        if sev not in _VALID_SEVERITIES:
            raise AuditParseError(
                f"findings[{i}].severity={sev} out of {{1,2,3,4}}"
            )
        idxs = finding["evidence_quote_idxs"]
        if not isinstance(idxs, list):
            raise AuditParseError(
                f"findings[{i}].evidence_quote_idxs must be list, "
                f"got {type(idxs).__name__}"
            )
        if not idxs:
            # SKILL.md: "If a finding cannot be anchored to at least one
            # quote index, do not emit it." Enforce at parse time.
            raise AuditParseError(
                f"findings[{i}].evidence_quote_idxs is empty; every finding "
                "must be anchored to at least one quote"
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

    return data


def _build_heuristic_violations(
    payload: dict[str, Any],
    cluster: InsightCluster,
) -> list[HeuristicViolation]:
    """Translate the skill's native findings into audit-contract
    :class:`HeuristicViolation` records.

    Nielsen severity is remapped to ADR-008's 0–10 anchors via
    :data:`NIELSEN_TO_ANCHORED`.

    ``evidence_review_ids`` is left empty: the cluster's
    ``representative_quotes`` is ``list[str]`` with no explicit mapping
    back to review ids. Attempting to recover review ids by substring
    search against member reviews is fragile (multi-quote reviews,
    overlapping substrings) and overkill for the thin spine. The
    evidence trail is preserved via the quote indices in ``reasoning``,
    and the full native payload is carried through the sidecar — so
    dropping ``evidence_review_ids`` does not lose information, it just
    relocates it. A later widening can compute the mapping at L4 or
    push it upstream into L3.
    """
    violations: list[HeuristicViolation] = []
    for finding in payload["findings"]:
        nielsen = finding["severity"]
        anchored = NIELSEN_TO_ANCHORED[nielsen]
        quote_refs = "; ".join(
            f"q[{idx}]={cluster.representative_quotes[idx]!r}"
            for idx in finding["evidence_quote_idxs"]
        )
        reasoning = (
            f"[{finding['dimension']}] {finding['violation']} "
            f"Recommendation: {finding['recommendation']} "
            f"Evidence: {quote_refs} "
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
# Outcome + per-cluster pipeline
# ---------------------------------------------------------------------------


AuditStatus = Literal["audited", "fallback"]


@dataclass(frozen=True, slots=True)
class AuditOutcome:
    """One cluster's audit result.

    ``verdict`` is always populated — on fallback it carries zero
    heuristic violations and a native payload that points at the raw
    skill response so a reviewer can see what the model emitted.
    ``native_payload`` is the raw parsed dict on success, or a minimal
    error record on fallback. ``reason`` is None for success and a
    parse-error message on fallback.
    """

    cluster_id: str
    verdict: AuditVerdict
    native_payload: dict[str, Any]
    status: AuditStatus
    reason: str | None = None


def _verdict_id(skill_id: str, cluster_id: str) -> str:
    """Stable, unique verdict id within a single (skill × cluster)
    pairing. Reruns reuse the same id — this is intentional; the
    per-run identity lives in the sidecar's ``run_id``.
    """
    return f"{skill_id}__{cluster_id}"


def _fallback_native(raw_response: str, reason: str) -> dict[str, Any]:
    """Native payload used when the skill response fails to parse.

    Shape is deliberately different from a success payload (no
    ``summary`` / ``dimension_scores`` / ``findings`` keys) so a
    downstream consumer cannot accidentally treat a fallback as a real
    audit. The reviewer sees exactly the bytes the model emitted.
    """
    return {
        "fallback": True,
        "reason": reason,
        "raw_response": raw_response,
    }


async def audit_cluster(
    cluster: InsightCluster,
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str,
) -> AuditOutcome:
    """Audit one cluster. Never raises on parse failure — falls back.

    Genuine transport errors (SDK exceptions not caught by the client's
    retry layer, replay-miss in replay mode) still propagate so the
    caller can decide whether to abort the batch.
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
    """Audit a list of clusters concurrently.

    Returns:
        ``(outcomes, failures)`` — outcomes carry both ``"audited"`` and
        ``"fallback"`` rows; ``failures`` carry transport-level
        exceptions (one ``(cluster_id, exc)`` per failed cluster).
        Transport failures are *not* expressed as fallback outcomes: a
        replay miss in replay mode, for instance, means the cache is
        out of sync with the cluster file and should surface loudly.
    """
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
# IO
# ---------------------------------------------------------------------------


def load_clusters(path: Path) -> list[InsightCluster]:
    """Read L3b output JSONL (one :class:`InsightCluster` per line).

    Pydantic validates each row on load; a malformed row raises
    :class:`pydantic.ValidationError` with the offending payload.
    """
    clusters: list[InsightCluster] = []
    for i, raw in enumerate(read_jsonl(path), start=1):
        try:
            clusters.append(InsightCluster.model_validate(raw))
        except ValidationError as e:
            raise ValueError(f"{path}: line {i}: {e}") from e
    return clusters


def sort_outcomes(outcomes: list[AuditOutcome]) -> list[AuditOutcome]:
    """Sort by cluster_id for deterministic output ordering.

    Transport failures drop clusters from ``outcomes`` entirely, so the
    caller is responsible for noticing missing ids; this function does
    not pad with placeholders.
    """
    return sorted(outcomes, key=lambda o: o.cluster_id)


# ---------------------------------------------------------------------------
# Provenance sidecar
# ---------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Atomic + durable bytes write. Identical primitive to L3b's.

    Kept duplicated for the same reason L3b keeps it: promoting it is a
    refactor orthogonal to L4's contract.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    if hasattr(os, "O_DIRECTORY"):
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


@dataclass
class _ProvenanceAccumulator:
    """Mutable accumulator for provenance building; frozen into a dict
    at the end. Separating it from :func:`build_provenance` keeps the
    aggregation logic testable in isolation.
    """

    dimension_score_totals: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in DIMENSION_KEYS}
    )
    findings_count: int = 0
    severity_histogram: dict[int, int] = field(
        default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0}
    )


def build_provenance(
    outcomes: list[AuditOutcome],
    failures: list[tuple[str, Exception]],
    *,
    model: str,
) -> dict[str, Any]:
    """Summarise an L4 run into the provenance payload.

    Shape mirrors L3b's provenance: top-level config + rolled-up counts
    + per-reason breakdown for fallbacks. Adds L4-specific aggregates
    over the native payload: dimension-score sums, finding counts, and
    a Nielsen-severity histogram. These let a reviewer see the overall
    health distribution of the audit without opening the native
    sidecar.
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


def _native_row(outcome: AuditOutcome) -> dict[str, Any]:
    """Render one native-payload sidecar row.

    Always keyed by ``verdict_id`` so the main verdict's
    ``native_payload_ref = "<file>#<verdict_id>"`` can be resolved by a
    reader without knowing the line number.
    """
    return {
        "verdict_id": outcome.verdict.verdict_id,
        "cluster_id": outcome.cluster_id,
        "status": outcome.status,
        "payload": outcome.native_payload,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _default_run_id() -> str:
    """Microsecond-precision run_id; matches L3/L3b's scheme."""
    return f"l4-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"


def main(argv: list[str] | None = None) -> int:
    repo_root = _resolve_repo_root()

    parser = argparse.ArgumentParser(
        description="L4 audit — one-shot Claude call per L3b cluster × skill.",
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
        help=f"L4 verdicts JSONL output (default: {DEFAULT_VERDICTS}).",
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
        # Thin spine is ~7 clusters × one Sonnet call (~$0.02 each).
        # $5 is an order-of-magnitude kill-switch catching a
        # misconfiguration (accidental 10,000-cluster input on Opus)
        # before it does damage.
        help="Per-run USD kill-switch ceiling (live mode only).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Optional run_id; default is 'l4-YYYYmmddTHHMMSSffffff' at UTC "
            "now (microseconds avoid same-second collisions)."
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
    # Separate atomic write — the native payloads are reviewer-facing
    # and do not participate in the audit-contract key chain, so they
    # get their own .meta.json via write_jsonl_atomic for symmetry.
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
    _log.info("wrote L4 run provenance to %s", provenance_path)

    # Quick tally -------------------------------------------------------
    audited_count = sum(1 for o in outcomes if o.status == "audited")
    fallback_count = sum(1 for o in outcomes if o.status == "fallback")
    _log.info(
        "L4 done. mode=%s live-spend=$%.4f audited=%d fallback=%d transport_fail=%d",
        args.mode,
        client.cumulative_usd,
        audited_count,
        fallback_count,
        len(failures),
    )

    # Non-zero exit only on transport failures. Fallback verdicts are a
    # traceable signal, not an error — treating them as exit=1 would
    # make the pipeline refuse to proceed on a valid "the skill couldn't
    # audit one cluster cleanly" outcome.
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
