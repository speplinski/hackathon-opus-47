"""Layer 3b — cluster labelling (Claude-backed).

L3 produces clusters with placeholder labels of the form
``"UNLABELED:cluster_NN"`` (see ``l3_cluster`` module docstring
§"Label lifecycle"). This layer rewrites them into human-readable
labels by one-shot Claude calls against the ``label-cluster`` skill.

Input / output
--------------
* Reads :data:`DEFAULT_CLUSTERS` (``data/derived/l3_clusters.jsonl``) —
  a list of :class:`InsightCluster` records.
* Writes :data:`DEFAULT_LABELED` (``data/derived/l3b_labeled_clusters.jsonl``)
  — the same records with ``label`` rewritten. All other fields
  (``cluster_id``, ``member_review_ids``, ``centroid_vector_ref``,
  ``representative_quotes``) are carried forward **unchanged**. Any
  drift in those fields breaks the L3 → L3b → L4 audit chain.
* Sidecar ``.meta.json`` via :func:`storage.write_jsonl_atomic`, with
  ``skill_hashes={"label-cluster": <dir-hash>}`` populated. This is how
  L3b differs from L3 at the audit boundary: L3 has empty
  ``skill_hashes`` (no Claude call), L3b has exactly one skill.
* Sidecar ``.provenance.json`` with counts and the quarantine-style
  breakdown of labels that fell back to the ``UNLABELED:`` placeholder.

Fallback discipline
-------------------
If Claude's response cannot be parsed or validated (bad JSON, missing
``label`` key, length out of bounds), the cluster keeps its
``UNLABELED:cluster_NN`` placeholder and the failure is recorded in
:class:`LabelOutcome` (status ``"fallback"``). The output file remains
a total function of the input — every cluster_id in → every cluster_id
out — but clusters with fallback labels are still visibly flagged by the
``UNLABELED:`` prefix so a reader scanning the artifact can tell the
layer didn't silently emit a guess.

This mirrors L2's quarantine-vs-error distinction: fallback is a
traceable "skill output rejected" signal, not a crash.

Determinism
-----------
* ``temperature=0.0``; Opus 4.7 drops custom sampling params per
  :func:`claude_client._omits_sampling_params`, but the key_hash still
  records the caller-requested temperature.
* One call per cluster — no batching subtleties; output order matches
  input order (sorted by cluster_id on write).
* Replay cache keyed on
  ``sha256(skill_id, skill_hash, model, temperature, max_tokens,
  system, user)`` — identical reruns cost zero. Changing SKILL.md
  changes ``skill_hash`` and invalidates the cache — intentional,
  a skill edit is a semantic change (same contract as L2).
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from auditable_design.claude_client import Client
from auditable_design.schemas import SCHEMA_VERSION, InsightCluster
from auditable_design.storage import hash_file, read_jsonl, write_jsonl_atomic

__all__ = [
    "DEFAULT_CLUSTERS",
    "DEFAULT_LABELED",
    "LABEL_MAX_LEN",
    "LABEL_MIN_LEN",
    "LAYER_NAME",
    "MAX_TOKENS",
    "MIXED_LABEL",
    "MODEL",
    "SKILL_ID",
    "SYSTEM_PROMPT",
    "TEMPERATURE",
    "LabelOutcome",
    "LabelParseError",
    "build_user_message",
    "label_cluster",
    "label_batch",
    "main",
    "parse_label_response",
    "skill_hash",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_ID: str = "label-cluster"
LAYER_NAME: str = "l3b_label"

# Claude 4.x Haiku — labelling is a short, bounded transformation on
# 1–5 short quotes; the Opus premium is unnecessary. The replay cache
# makes reruns free regardless of model choice, but the first run
# should not pay Opus prices for work Haiku handles. If a future eval
# shows Haiku mis-labels clusters (e.g. confuses "voice recognition"
# with "speech"), bump to Sonnet 4.6 first before Opus.
MODEL: str = "claude-haiku-4-5-20251001"
TEMPERATURE: float = 0.0
# Response is a one-line JSON object: ``{"label": "<≤60 chars>"}``.
# 128 tokens covers the worst case with room for the label reaching the
# upper bound without ever truncating the closing ``"}``.
MAX_TOKENS: int = 128

# Label length bounds enforced after parse. Upper bound mirrors the
# SKILL.md contract (60 chars); the layer is the last line of defence
# if the model ignores it.
LABEL_MIN_LEN: int = 1
LABEL_MAX_LEN: int = 60

# Sentinel the skill returns for incoherent clusters. Carried through
# unchanged — downstream audits (L4 cluster-coherence) treat it as a
# first-class signal, not an error.
MIXED_LABEL: str = "Mixed complaints"

# Default paths — relative to repo root, resolved in main().
DEFAULT_CLUSTERS = Path("data/derived/l3_clusters.jsonl")
DEFAULT_LABELED = Path("data/derived/l3b_labeled_clusters.jsonl")


# ---------------------------------------------------------------------------
# System prompt — loaded from SKILL.md
# ---------------------------------------------------------------------------


def _resolve_repo_root() -> Path:
    """Walk up to find pyproject.toml. Duplicated from l2_structure /
    l3_cluster — third layer to need it, so the TODO to extract into
    ``cli_utils`` now has three call sites to justify it. Not done here
    because the extraction is orthogonal to L3b's contract and would
    make this change reviewable-in-isolation harder.
    """
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("cannot locate repo root (no pyproject.toml above this file)")


def _load_skill_body() -> str:
    """Read ``skills/label-cluster/SKILL.md`` and strip YAML frontmatter.

    Identical shape to L2's loader — the frontmatter is Claude Code
    skill-loader metadata, not guidance for the model. Fails at import
    if the file is missing: the layer cannot function without its skill.
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
# invalidates the replay cache for prior L3b runs. Intentional.
SYSTEM_PROMPT: str = _load_skill_body()


def skill_hash() -> str:
    """sha256 of :data:`SYSTEM_PROMPT`.

    Same audit contract as L2: the hash IS the identity of the layer's
    brain. Every :meth:`claude_client.Client.call` invocation is keyed
    on this, so any skill edit forces a re-run rather than silent reuse
    of stale labels.
    """
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_user_message(quotes: list[str]) -> str:
    """Render the per-cluster user message.

    Unlike L2, L3b's input is not a raw user review — it's already been
    through L1 (relevance filter) and L2 (verbatim-quote extraction), so
    the quotes are short, curated substrings of reviews. ADR-010's
    wrap_user_text guard is still applicable in principle, but the
    ``<cluster_quotes><q>...</q></cluster_quotes>`` shape defined in
    SKILL.md is our injection boundary for this layer: Claude is told
    to treat content inside ``<q>`` as data, not instructions.

    We escape ``<``, ``>``, ``&`` manually inside each quote because the
    existing :func:`prompt_builder.wrap_user_text` wraps a single blob in
    ``<user_review>``; it isn't shaped for a list of quotes inside a
    custom container. Using a specialised wrapper here keeps the shape
    the skill was written against without diluting ``wrap_user_text``'s
    single-responsibility.
    """
    # Defensive escape — matches the subset prompt_builder._ESCAPE_MAP
    # handles. Quote-level escape, not cluster-level, because each <q>
    # encloses one quote.
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})

    inner = "\n".join(f"  <q>{q.translate(escape)}</q>" for q in quotes)
    return f"<cluster_quotes>\n{inner}\n</cluster_quotes>"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class LabelParseError(ValueError):
    """Raised when a Claude response cannot be coerced into a valid label.

    Caught by :func:`label_cluster` and converted into a
    :class:`LabelOutcome` with status ``"fallback"`` — a fallback is
    recorded, not raised. The exception class exists so the layer
    runner can tell a label failure apart from a transport-level
    error (which still propagates).
    """


# Same primitive as L2: greedy outermost ``{...}`` with DOTALL tolerates
# code fences, leading prose, trailing whitespace. Label responses are
# very short so a tight regex would work, but matching L2's shape keeps
# the two layers' parse behaviour uniform for reviewers.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_label_response(text: str) -> str:
    """Extract and validate the label string from a Claude response.

    Steps:

    1. Find the outermost JSON object (tolerates prose / fences).
    2. Parse as JSON; must be a dict with exactly the key ``"label"``.
    3. Value must be a string, stripped, length in
       ``[LABEL_MIN_LEN, LABEL_MAX_LEN]``.
    4. No ``"UNLABELED:"`` prefix — the skill would be echoing the
       placeholder, which is a known failure mode of lazy labellers
       and must not silently pass through.

    Raises:
        LabelParseError: On any of the above violations, with a message
            that identifies which check failed.
    """
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise LabelParseError(f"no JSON object found in response: {text!r}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LabelParseError(f"malformed JSON: {e}; text={text!r}") from e
    if not isinstance(data, dict):
        raise LabelParseError(f"expected JSON object, got {type(data).__name__}")

    allowed = {"label"}
    actual = set(data.keys())
    # Check missing-required before extra-key — a response like
    # ``{"name": "X"}`` is primarily a "no label" failure; calling it
    # "unexpected key 'name'" buries the actionable complaint.
    if "label" not in actual:
        raise LabelParseError("missing required top-level key: 'label'")
    extra = actual - allowed
    if extra:
        raise LabelParseError(f"unexpected top-level keys: {sorted(extra)}")

    raw = data["label"]
    if not isinstance(raw, str):
        raise LabelParseError(
            f"'label' must be str, got {type(raw).__name__}: {raw!r}"
        )
    # Strip whitespace defensively — SKILL.md says no leading/trailing
    # whitespace, but models occasionally emit trailing newlines inside
    # JSON strings. Stripping is safer than rejecting for a whitespace
    # typo; a model that emits genuinely empty labels fails the length
    # check below.
    label = raw.strip()
    n = len(label)
    if n < LABEL_MIN_LEN:
        raise LabelParseError(
            f"label length {n} < LABEL_MIN_LEN={LABEL_MIN_LEN}: {label!r}"
        )
    if n > LABEL_MAX_LEN:
        raise LabelParseError(
            f"label length {n} > LABEL_MAX_LEN={LABEL_MAX_LEN}: {label!r}"
        )
    # Guard against the model echoing the placeholder shape. Case-
    # insensitive because ``"unlabeled:"`` is the exact sentinel we
    # want to catch regardless of wire-transfer casing drift.
    if label.lower().startswith("unlabeled:"):
        raise LabelParseError(f"label echoes the UNLABELED: placeholder: {label!r}")

    return label


# ---------------------------------------------------------------------------
# Outcome + per-cluster pipeline
# ---------------------------------------------------------------------------


LabelStatus = Literal["labeled", "fallback"]


@dataclass(frozen=True, slots=True)
class LabelOutcome:
    """One cluster's labelling result.

    ``cluster_id`` is always the input cluster's id. ``label`` is either
    the model's output (``status="labeled"``) or the carried-through
    ``UNLABELED:cluster_NN`` placeholder (``status="fallback"``). The
    ``reason`` is populated only on fallback, and names the parse/
    validation failure — used in the provenance sidecar's breakdown.
    """

    cluster_id: str
    label: str
    status: LabelStatus
    reason: str | None = None


async def label_cluster(
    cluster: InsightCluster,
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str,
) -> LabelOutcome:
    """Label one cluster. Never raises on parse failure — falls back.

    Genuine transport errors (SDK exceptions not caught by the client's
    retry layer, replay-miss in replay mode) still propagate so the
    caller can decide whether to abort the batch.
    """
    user = build_user_message(cluster.representative_quotes)
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
        label = parse_label_response(resp.response)
    except LabelParseError as e:
        _log.warning(
            "label parse failed for cluster %s: %s — falling back to placeholder",
            cluster.cluster_id,
            e,
        )
        return LabelOutcome(
            cluster_id=cluster.cluster_id,
            label=cluster.label,  # keep the UNLABELED:cluster_NN placeholder
            status="fallback",
            reason=str(e),
        )
    return LabelOutcome(
        cluster_id=cluster.cluster_id,
        label=label,
        status="labeled",
        reason=None,
    )


async def label_batch(
    clusters: list[InsightCluster],
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str | None = None,
) -> tuple[list[LabelOutcome], list[tuple[str, Exception]]]:
    """Label a list of clusters concurrently.

    Returns:
        ``(outcomes, failures)`` — outcomes carry both ``"labeled"``
        and ``"fallback"`` rows; ``failures`` carry transport-level
        exceptions (one ``(cluster_id, exc)`` per failed cluster).
        Transport failures are *not* expressed as fallback outcomes:
        a replay miss in replay mode, for instance, means the cache
        is out of sync with the cluster file and should surface
        loudly, not silently become an UNLABELED row.
    """
    sh = skill_hash_value if skill_hash_value is not None else skill_hash()

    async def _one(c: InsightCluster) -> tuple[str, LabelOutcome | Exception]:
        try:
            outcome = await label_cluster(
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
    outcomes: list[LabelOutcome] = []
    failures: list[tuple[str, Exception]] = []
    for cid, payload in results:
        if isinstance(payload, LabelOutcome):
            outcomes.append(payload)
        else:
            failures.append((cid, payload))
    return outcomes, failures


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def load_clusters(path: Path) -> list[InsightCluster]:
    """Read L3 output JSONL (one :class:`InsightCluster` per line).

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


def merge_outcomes(
    clusters: list[InsightCluster],
    outcomes: list[LabelOutcome],
) -> list[InsightCluster]:
    """Produce the final :class:`InsightCluster` list with rewritten labels.

    Every cluster in ``clusters`` appears in the output, in sorted
    ``cluster_id`` order, regardless of whether it got a labeled or
    fallback outcome. If an outcome is missing for a cluster (e.g.
    transport failure dropped it), the original placeholder label is
    preserved and a warning is logged — the output file stays a total
    function of the input.
    """
    by_id: dict[str, LabelOutcome] = {o.cluster_id: o for o in outcomes}
    merged: list[InsightCluster] = []
    for c in sorted(clusters, key=lambda x: x.cluster_id):
        outcome = by_id.get(c.cluster_id)
        if outcome is None:
            _log.warning(
                "no label outcome for cluster %s — keeping placeholder",
                c.cluster_id,
            )
            merged.append(c)
            continue
        # Pydantic model_copy preserves the frozen/strict config; we
        # only override ``label``. Every other field (cluster_id,
        # member_review_ids, centroid_vector_ref, representative_quotes)
        # is carried through verbatim — that's the L3 → L3b invariant.
        merged.append(c.model_copy(update={"label": outcome.label}))
    return merged


# ---------------------------------------------------------------------------
# Provenance sidecar
# ---------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Atomic + durable bytes write. Identical primitive to L3's.

    Kept duplicated for the same reason L3 keeps it: ``storage._write_bytes_atomic``
    is module-private, and promoting it for a third call site is a
    refactor orthogonal to this layer's contract. When a fourth layer
    needs the same, promote it with a public name.
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


def build_provenance(
    outcomes: list[LabelOutcome],
    failures: list[tuple[str, Exception]],
    *,
    model: str,
) -> dict[str, Any]:
    """Summarise a run into the provenance payload.

    Mirrors L3's provenance shape: top-level config + rolled-up counts
    + a per-reason breakdown for the fallback path. ``fallback_reasons``
    is a list-of-dicts rather than a ``{reason: count}`` map because a
    reason string can be long (includes the raw model response in
    some cases) and sorting by ``cluster_id`` keeps the diff between
    runs readable.
    """
    labeled = [o for o in outcomes if o.status == "labeled"]
    fallback = [o for o in outcomes if o.status == "fallback"]
    mixed = [o for o in labeled if o.label == MIXED_LABEL]
    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "cluster_count": len(outcomes) + len(failures),
        "labeled_count": len(labeled),
        "mixed_complaints_count": len(mixed),
        "fallback_count": len(fallback),
        "transport_failure_count": len(failures),
        "fallback_reasons": sorted(
            [
                {"cluster_id": o.cluster_id, "reason": o.reason}
                for o in fallback
            ],
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


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _default_run_id() -> str:
    """Microsecond-precision run_id. Same rationale as l3_cluster:
    two reruns in the same wall-clock second would collide on a
    coarser stamp and make audit diffs ambiguous.
    """
    return f"l3b-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"


def main(argv: list[str] | None = None) -> int:
    repo_root = _resolve_repo_root()

    parser = argparse.ArgumentParser(
        description="L3b labelling — one-shot Claude call per L3 cluster.",
    )
    parser.add_argument(
        "--clusters",
        type=Path,
        default=repo_root / DEFAULT_CLUSTERS,
        help=f"L3 clusters JSONL (default: {DEFAULT_CLUSTERS}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / DEFAULT_LABELED,
        help=f"Labeled clusters JSONL output (default: {DEFAULT_LABELED}).",
    )
    parser.add_argument(
        "--mode",
        choices=("live", "replay"),
        default="replay",
        help="Claude client mode (default: replay — reviewer-safe).",
    )
    parser.add_argument("--model", default=MODEL, help=f"Claude model (default: {MODEL}).")
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
        default=2.0,
        # Labelling is cheap: ~7-14 clusters (per-model) × one short
        # Haiku call each. A $2 ceiling is orders of magnitude above
        # expected spend and will never fire on a normal run — its job
        # is to catch a misconfiguration (accidentally pointing at a
        # 10,000-cluster input) before it does damage.
        help="Per-run USD kill-switch ceiling (live mode only).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Optional run_id; default is 'l3b-YYYYmmddTHHMMSSffffff' at UTC "
            "now (microseconds avoid same-second collisions)."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    clusters = load_clusters(args.clusters)
    _log.info("loaded %d clusters from %s", len(clusters), args.clusters)

    if not clusters:
        _log.error("empty clusters input — nothing to label")
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
        label_batch(
            clusters,
            client,
            model=args.model,
        )
    )

    if failures:
        for cid, err in failures:
            _log.warning(
                "label transport failure for %s: %s: %s",
                cid,
                type(err).__name__,
                err,
            )
        _log.error(
            "%d/%d labellings failed at transport level",
            len(failures),
            len(clusters),
        )

    merged = merge_outcomes(clusters, outcomes)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    clusters_hash = hash_file(args.clusters)

    out_meta = write_jsonl_atomic(
        args.output,
        [c.model_dump(mode="json") for c in merged],
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes={args.clusters.name: clusters_hash},
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d labeled clusters to %s (sha256=%s…)",
        len(merged),
        args.output,
        out_meta.artifact_sha256[:16],
    )

    # Provenance sidecar. Auditor-facing (not load-bearing for ADR-011
    # replay), but durable via the same primitive as L3.
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
    _log.info("wrote L3b run provenance to %s", provenance_path)

    # Quick histogram — cheap signal on run health. Mirrors L2's
    # quarantine-reason histogram at the tail of a run.
    labeled_count = sum(1 for o in outcomes if o.status == "labeled")
    mixed_count = sum(
        1 for o in outcomes if o.status == "labeled" and o.label == MIXED_LABEL
    )
    fallback_count = sum(1 for o in outcomes if o.status == "fallback")
    _log.info(
        "L3b done. mode=%s live-spend=$%.4f labeled=%d mixed=%d fallback=%d transport_fail=%d",
        args.mode,
        client.cumulative_usd,
        labeled_count,
        mixed_count,
        fallback_count,
        len(failures),
    )

    # Non-zero exit on transport failures only. Fallback labels are
    # a traceable signal, not an error — treating them as exit=1 would
    # make the pipeline refuse to proceed on a perfectly valid "the
    # skill couldn't name this one cluster" outcome.
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
