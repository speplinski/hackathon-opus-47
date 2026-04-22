"""L3 clustering layer — insight clusters over L2 complaint graphs.

Pipeline
--------
1. Read ``data/derived/l2_graphs.jsonl`` produced by L2.
2. Extract ``pain`` and ``expectation`` node quotes (only those node types
   carry the complaint signal we want to cluster; ``context``,
   ``triggered_element``, ``workaround``, ``lost_value`` are deliberately
   excluded per concept.md §6).
3. Embed the quotes locally via :func:`auditable_design.embedders.local_encoder.encode`
   (sentence-transformers MiniLM, unit-norm, float32).
4. Cluster with HDBSCAN first. If HDBSCAN produces zero valid clusters
   (or raises), fall back to KMeans with ``k=KMEANS_FALLBACK_K=6``.
5. For each cluster:
   - Centroid = arithmetic mean of member embeddings (not re-normalised —
     the centroid represents the density peak, not a unit-norm direction).
   - Representative quotes = top-5 nodes closest to the *normalised*
     centroid by cosine similarity (``embeddings @ centroid/‖centroid‖``).
   - Member review IDs = all distinct ``review_id`` values among the
     cluster's nodes (multi-membership: a review can belong to multiple
     clusters if its pain/expectation nodes landed in different clusters).
6. Write ``data/derived/l3_clusters.jsonl`` + ``l3_centroids.npy`` +
   standard ``.meta.json`` sidecar; also emit a ``l3_clusters.provenance.json``
   that captures encoder/clustering runtime tuples for replay audit.

Label lifecycle (→ L3b)
-----------------------
L3 produces **placeholder labels** of the form ``"UNLABELED:cluster_00"``.
Human-readable labels (generated via Claude) are written by a *distinct*
downstream layer — **L3b** (``l3b_label``) — not an in-place rewrite of
this layer's artifact.

Why a separate layer, not in-place:

1. Immutability contract: ADR-011 treats layer artifacts as immutable
   replay anchors. Rewriting ``l3_clusters.jsonl`` with real labels
   would silently bump its ``artifact_sha256`` and invalidate every
   downstream artifact that hashed it.
2. ``skill_hashes`` boundary: L3 uses **no** Claude skill
   (``skill_hashes={}`` in the sidecar). Labeling calls Claude, so it
   owns its own skill hash and must live behind a distinct ``layer=``
   tag for replay auditing.
3. Re-runnability: clustering and labeling have very different cost
   profiles (clustering = seconds, labeling = Claude-budgeted). Keeping
   them as separate layers lets a reviewer re-label without re-clustering
   and vice versa.

L3b will read ``l3_clusters.jsonl`` and emit
``l3b_labeled_clusters.jsonl`` with rewritten ``label`` fields and its
own ``skill_hashes``. ``cluster_id``, ``member_review_ids``,
``centroid_vector_ref``, and ``representative_quotes`` carry forward
unchanged. The ``"UNLABELED:"`` prefix in L3 output makes the
intermediate state scannable by eye when the artifact is opened before
L3b has run.

Determinism
-----------
- HDBSCAN is deterministic given identical input; no seed is needed.
- KMeans uses ``random_state=seed`` (threaded through from CLI).
- Local encoder is byte-identical under a fixed runtime tuple
  (see ``local_encoder`` docstring).

Success gates (enforced at eval time, not here)
-----------------------------------------------
- 5-8 clusters on the full corpus, ≥20 reviews per cluster.
- On the N=50 sub-sample that gate is relaxed — see
  ``docs/evals/l3_cluster_evaluation.md``.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from auditable_design.embedders.local_encoder import encode
from auditable_design.schemas import ComplaintGraph, InsightCluster
from auditable_design.storage import hash_file, write_jsonl_atomic

__all__ = [
    "CLUSTERABLE_NODE_TYPES",
    "DEFAULT_CENTROIDS",
    "DEFAULT_CLUSTERS",
    "DEFAULT_GRAPHS",
    "KMEANS_FALLBACK_K",
    "LAYER_NAME",
    "MIN_CLUSTER_SIZE_DEFAULT",
    "REPRESENTATIVE_QUOTES_K",
    "SCHEMA_VERSION",
    "aggregate_review_membership",
    "build_insight_clusters",
    "cluster_hdbscan",
    "cluster_kmeans",
    "compute_centroids",
    "extract_clusterable_nodes",
    "load_l2_graphs",
    "main",
    "run_clustering",
    "select_representative_quotes",
]

_log = logging.getLogger(__name__)

LAYER_NAME: str = "l3_cluster"
SCHEMA_VERSION: int = 1

CLUSTERABLE_NODE_TYPES: frozenset[str] = frozenset({"pain", "expectation"})
"""Node types embedded and clustered. Derived from concept.md §6.

Other node_types (context, triggered_element, workaround, lost_value)
carry structural information about the complaint but are not the
signal we want to cluster. Excluding them keeps clusters focused on
what users *complain about* vs *expected*.
"""

MIN_CLUSTER_SIZE_DEFAULT: int = 5
"""HDBSCAN ``min_cluster_size``.

Concept docs target ≥20 reviews/cluster on the full corpus; on an N=50
sub-sample that would forbid any cluster at all. We default to 5 here
(loose enough for sub-sample smoke tests) and let the eval doc decide
whether the resulting clustering clears the stricter gate.
"""

KMEANS_FALLBACK_K: int = 6
"""``k`` for KMeans fallback — midpoint of the concept.md 5-8 target."""

REPRESENTATIVE_QUOTES_K: int = 5
"""Top-K quotes closest to centroid to store as ``representative_quotes``.

Bounded by the Pydantic schema (max_length=5) — bumping this requires a
schema change.
"""

# Default file locations (relative to repo root).
DEFAULT_GRAPHS: Path = Path("data/derived/l2_graphs.jsonl")
DEFAULT_CLUSTERS: Path = Path("data/derived/l3_clusters.jsonl")
DEFAULT_CENTROIDS: Path = Path("data/derived/l3_centroids.npy")


def _resolve_repo_root() -> Path:
    """Locate the repo root by walking up to find ``pyproject.toml``.

    Kept duplicated with :mod:`l2_structure` for now; if a third layer
    needs the same logic, extract to ``auditable_design.cli_utils``.
    """
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("cannot locate repo root (no pyproject.toml above this file)")


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def load_l2_graphs(path: Path) -> list[ComplaintGraph]:
    """Read L2 output JSONL (one :class:`ComplaintGraph` per line).

    Pydantic enforces the per-record schema on load; a malformed line
    raises ``pydantic.ValidationError`` with the offending payload.
    """
    graphs: list[ComplaintGraph] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_no}") from exc
            graphs.append(ComplaintGraph.model_validate(payload))
    return graphs


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def extract_clusterable_nodes(
    graphs: list[ComplaintGraph],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Pull ``pain``/``expectation`` node quotes plus a parallel index.

    Returns:
        ``(quotes, index)`` where ``len(quotes) == len(index)`` and
        ``index[i] = (review_id, node_id)`` of ``quotes[i]``. The parallel
        structure lets callers map cluster labels back to review/node
        identity without recomputing.
    """
    quotes: list[str] = []
    index: list[tuple[str, str]] = []
    for graph in graphs:
        for node in graph.nodes:
            if node.node_type in CLUSTERABLE_NODE_TYPES:
                quotes.append(node.verbatim_quote)
                index.append((graph.review_id, node.node_id))
    return quotes, index


def cluster_hdbscan(
    embeddings: npt.NDArray[np.float32],
    *,
    min_cluster_size: int,
) -> tuple[npt.NDArray[np.int64], dict[str, Any]]:
    """Run HDBSCAN. Returns ``(labels, algo_provenance)``.

    Labels: ``-1`` = noise, ``>=0`` = cluster membership.

    On unit-norm vectors, Euclidean distance is monotonically related to
    cosine distance (‖a-b‖² = 2·(1-cos(a,b))), so ``metric="euclidean"``
    gives the same cluster structure as ``metric="cosine"`` would — and
    HDBSCAN's Euclidean path is more battle-tested.
    """
    import hdbscan

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
        cluster_selection_method="eom",
        prediction_data=False,
    )
    labels = clusterer.fit_predict(embeddings.astype(np.float64))
    provenance: dict[str, Any] = {
        "algorithm": "hdbscan",
        "min_cluster_size": min_cluster_size,
        "metric": "euclidean",
        "cluster_selection_method": "eom",
        "hdbscan_version": hdbscan.__version__,
    }
    return labels.astype(np.int64), provenance


def cluster_kmeans(
    embeddings: npt.NDArray[np.float32],
    *,
    k: int,
    seed: int,
) -> tuple[npt.NDArray[np.int64], dict[str, Any]]:
    """Fallback clusterer: KMeans with explicit random_state.

    ``n_init=10`` is pinned deliberately rather than relying on the
    sklearn default — see the inline comment for rationale.
    """
    import sklearn
    from sklearn.cluster import KMeans

    # Pin ``n_init=10`` explicitly instead of using sklearn ≥1.4's default
    # of ``n_init="auto"``. "auto" resolves to 10 for random init but *1*
    # for k-means++ (which is the default ``init``), which would make
    # results depend on the installed sklearn version and silently change
    # cluster quality across environments. A fixed integer also silences
    # the FutureWarning that sklearn <1.4 emits for the "auto" value —
    # important because the replay log (ADR-011) pins the exact sklearn
    # version and we want byte-identical KMeans output for the same seed.
    clusterer = KMeans(n_clusters=k, random_state=seed, n_init=10)
    labels = clusterer.fit_predict(embeddings.astype(np.float64))
    provenance: dict[str, Any] = {
        "algorithm": "kmeans",
        "k": k,
        "seed": seed,
        "n_init": 10,
        "sklearn_version": sklearn.__version__,
    }
    return labels.astype(np.int64), provenance


def _normalize_labels(labels: npt.NDArray[np.int64]) -> npt.NDArray[np.int64]:
    """Remap non-noise labels to contiguous ``0..k-1``; preserve ``-1``.

    HDBSCAN and sklearn's KMeans both emit contiguous labels today, but
    relying on that convention is a hidden coupling: downstream code
    uses ``cluster_id`` both as a dict key *and* as a row index into
    ``centroids_stacked``. After this pass, ``cluster_id == row_index``
    is true **by construction**, so the ``centroid_vector_ref`` pointer
    is correct regardless of which backend produced ``labels`` (future
    agglomerative/spectral/whatever).

    Noise points (``-1``) are preserved verbatim — they do not
    participate in centroid computation or the .npy.
    """
    positive_mask = labels >= 0
    if not positive_mask.any():
        return labels
    # return_inverse gives indices into unique_positive for each positive
    # sample — exactly the contiguous remapping we want.
    _unique_positive, inverse = np.unique(labels[positive_mask], return_inverse=True)
    normalized = labels.copy()
    normalized[positive_mask] = inverse.astype(np.int64)
    return normalized


def compute_centroids(
    embeddings: npt.NDArray[np.float32],
    labels: npt.NDArray[np.int64],
) -> dict[int, npt.NDArray[np.float32]]:
    """Arithmetic mean embedding per cluster_id. Skips noise (``-1``).

    Not re-normalised — see module docstring. Downstream cosine-based
    selection normalises on the fly.
    """
    centroids: dict[int, npt.NDArray[np.float32]] = {}
    unique_labels = np.unique(labels[labels >= 0])
    for cluster_id in unique_labels:
        mask = labels == cluster_id
        centroids[int(cluster_id)] = embeddings[mask].mean(axis=0).astype(np.float32)
    return centroids


def select_representative_quotes(
    embeddings: npt.NDArray[np.float32],
    labels: npt.NDArray[np.int64],
    quotes: list[str],
    centroids: dict[int, npt.NDArray[np.float32]],
    *,
    k: int = REPRESENTATIVE_QUOTES_K,
) -> dict[int, list[str]]:
    """Top-k quotes closest to the (unit-normalised) centroid, per cluster.

    If a cluster has fewer than ``k`` members, all members are returned —
    schema requires ``1 <= len(representative_quotes) <= 5`` so as long
    as the cluster is non-empty (guaranteed by ``compute_centroids``) the
    output is valid.
    """
    representatives: dict[int, list[str]] = {}
    for cluster_id, centroid in centroids.items():
        mask = labels == cluster_id
        member_indices = np.where(mask)[0]
        member_embeddings = embeddings[member_indices]

        # Unit-norm the centroid for cosine; members are already unit-norm.
        # +1e-12 guards against the pathological zero-centroid case which
        # shouldn't happen in practice but mustn't NaN if it does.
        centroid_norm = centroid / (float(np.linalg.norm(centroid)) + 1e-12)
        similarities = member_embeddings @ centroid_norm

        top_within_cluster = np.argsort(similarities)[::-1][:k]
        top_quote_indices = member_indices[top_within_cluster]
        representatives[cluster_id] = [quotes[i] for i in top_quote_indices]
    return representatives


def aggregate_review_membership(
    labels: npt.NDArray[np.int64],
    node_index: list[tuple[str, str]],
) -> dict[int, list[str]]:
    """Return ``{cluster_id: sorted [review_id, ...]}`` (multi-membership).

    A review is in cluster ``c`` iff *any* of its pain/expectation nodes
    landed in ``c``. Noise points (label=-1) contribute no memberships.
    Review IDs are de-duped within a cluster and sorted for deterministic
    downstream diffs.
    """
    membership: dict[int, set[str]] = defaultdict(set)
    for (review_id, _node_id), label in zip(node_index, labels, strict=True):
        if label >= 0:
            membership[int(label)].add(review_id)
    return {cid: sorted(members) for cid, members in membership.items()}


def build_insight_clusters(
    *,
    centroids: dict[int, npt.NDArray[np.float32]],
    representative_quotes: dict[int, list[str]],
    review_membership: dict[int, list[str]],
    centroids_path: Path,
) -> list[InsightCluster]:
    """Assemble Pydantic :class:`InsightCluster` records.

    ``centroid_vector_ref`` uses ``"<file>#<index>"`` pointer syntax so a
    downstream reader can ``np.load(file)[index]`` to get the centroid.
    The file half is the *basename* to keep the reference portable when
    the directory moves.

    Labels are placeholders — Claude labeling happens in a later pass.
    """
    results: list[InsightCluster] = []
    for cluster_id in sorted(centroids.keys()):
        ref = f"{centroids_path.name}#{cluster_id}"
        # Direct indexing (not .get-with-default): every cluster_id in
        # ``centroids`` came from a label in ``labels[labels >= 0]``, and
        # every such label originated from a node with a review_id, so
        # ``review_membership`` *must* contain this cluster_id. A missing
        # entry here signals a broken invariant upstream (one of
        # ``extract_clusterable_nodes`` / ``compute_centroids`` /
        # ``aggregate_review_membership`` drifted out of agreement), not
        # user data. Convert the implicit KeyError into an explicit
        # RuntimeError with a pointer to the likely culprits — a raw
        # ``KeyError: 3`` at this call site would give a future debugger
        # no clue which of the three functions went wrong.
        if cluster_id not in review_membership:
            raise RuntimeError(
                f"invariant violation: cluster_id={cluster_id} has a centroid "
                f"but no review members. extract_clusterable_nodes / "
                f"compute_centroids / aggregate_review_membership have drifted "
                f"— every clusterable node is supposed to carry a review_id."
            )
        members = review_membership[cluster_id]
        results.append(
            InsightCluster(
                cluster_id=f"cluster_{cluster_id:02d}",
                # Placeholder label. L3 deliberately does not call Claude —
                # labeling is a separate downstream pass (see module
                # docstring §"Label lifecycle"). The "UNLABELED:" prefix
                # makes the intermediate state visible to a reader scanning
                # l3_clusters.jsonl.
                label=f"UNLABELED:cluster_{cluster_id:02d}",
                member_review_ids=members,
                centroid_vector_ref=ref,
                representative_quotes=representative_quotes[cluster_id],
            )
        )
    return results


def run_clustering(
    graphs: list[ComplaintGraph],
    *,
    seed: int,
    centroids_path: Path,
    min_cluster_size: int = MIN_CLUSTER_SIZE_DEFAULT,
    kmeans_k: int = KMEANS_FALLBACK_K,
) -> tuple[list[InsightCluster], dict[str, Any], npt.NDArray[np.float32]]:
    """End-to-end pipeline. Returns ``(clusters, run_provenance, centroids_stacked)``.

    ``run_provenance`` merges:

    - ``encoder``: the full provenance dict from the local encoder
      (model name, weights hash, runtime tuple, …)
    - ``clustering``: algorithm + params + library version
    - ``fallback_reason``: ``None`` if HDBSCAN succeeded, otherwise a
      short human-readable explanation of why we fell through to KMeans
    - ``node_count``, ``clusterable_node_types``, ``min_cluster_size``,
      ``kmeans_k``: config snapshot for auditability

    ``centroids_stacked`` is the ``(n_clusters, dim)`` float32 array that
    the CLI writes to ``.npy``. Returning it here (rather than writing in
    this function) keeps ``run_clustering`` free of filesystem side
    effects for tests.

    Raises:
        ValueError: if no clusterable nodes exist (empty L2 input or L2
            produced no pain/expectation nodes). Fail loud rather than
            silently emit an empty clusters file.
        RuntimeError: if both HDBSCAN and KMeans failed to produce any
            clusters (e.g. <k samples for KMeans fallback).
    """
    quotes, node_index = extract_clusterable_nodes(graphs)
    if not quotes:
        raise ValueError(
            f"no clusterable nodes found "
            f"(filter: {sorted(CLUSTERABLE_NODE_TYPES)}); "
            f"did L2 produce pain/expectation nodes on this corpus?"
        )

    _log.info("embedding %d clusterable nodes", len(quotes))
    embeddings, encoder_provenance = encode(quotes, seed=seed)

    fallback_reason: str | None = None
    labels: npt.NDArray[np.int64]
    clustering_provenance: dict[str, Any]
    try:
        labels, clustering_provenance = cluster_hdbscan(embeddings, min_cluster_size=min_cluster_size)
        n_clusters = int(np.unique(labels[labels >= 0]).size)
        if n_clusters == 0:
            fallback_reason = f"HDBSCAN found 0 valid clusters at min_cluster_size={min_cluster_size}"
    except Exception as exc:
        # Any HDBSCAN failure (shape mismatch, numerical issue, upstream
        # regression) triggers fallback — the user should still get
        # clusters, with the exception type surfaced in the provenance file.
        fallback_reason = f"HDBSCAN raised {type(exc).__name__}: {exc}"

    if fallback_reason is not None:
        _log.warning(
            "HDBSCAN did not produce usable clusters (%s); falling back to KMeans k=%d",
            fallback_reason,
            kmeans_k,
        )
        # Guard: sklearn KMeans raises ValueError when n_samples < k.
        # Surface that as our friendlier RuntimeError so callers get one
        # shape of "clustering failed" rather than a cascade of
        # algorithm-specific exceptions.
        if len(embeddings) < kmeans_k:
            raise RuntimeError(
                f"KMeans fallback cannot run: only {len(embeddings)} clusterable "
                f"nodes, need ≥ kmeans_k={kmeans_k}. Either lower --kmeans-k "
                f"or ensure L2 produced more pain/expectation nodes."
            )
        labels, clustering_provenance = cluster_kmeans(embeddings, k=kmeans_k, seed=seed)

    # Normalize labels to contiguous 0..k-1 (noise preserved as -1) so that
    # cluster_id == row index in the stacked .npy is true by construction.
    # See ``_normalize_labels`` docstring for the rationale.
    labels = _normalize_labels(labels)

    centroids = compute_centroids(embeddings, labels)
    if not centroids:
        raise RuntimeError(
            "clustering produced zero clusters after fallback — "
            f"(node_count={len(quotes)}, kmeans_k={kmeans_k}); "
            "cannot emit InsightCluster records"
        )

    representative_quotes = select_representative_quotes(embeddings, labels, quotes, centroids)
    review_membership = aggregate_review_membership(labels, node_index)

    insight_clusters = build_insight_clusters(
        centroids=centroids,
        representative_quotes=representative_quotes,
        review_membership=review_membership,
        centroids_path=centroids_path,
    )

    sorted_ids = sorted(centroids.keys())
    centroids_stacked = np.stack([centroids[cid] for cid in sorted_ids], axis=0)

    run_provenance: dict[str, Any] = {
        "encoder": encoder_provenance,
        "clustering": clustering_provenance,
        "fallback_reason": fallback_reason,
        "node_count": len(quotes),
        "clusterable_node_types": sorted(CLUSTERABLE_NODE_TYPES),
        "min_cluster_size": min_cluster_size,
        "kmeans_k": kmeans_k,
    }

    return insight_clusters, run_provenance, centroids_stacked


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
    # Microsecond precision (``%f`` = 6 digits) avoids collisions when
    # two runs land in the same wall-clock second — realistic for tests,
    # tight CI retries, or a reviewer re-running L3 twice while tweaking
    # a flag. A duplicate run_id would make downstream audit diffs
    # ambiguous, so the extra characters are worth it.
    return f"l3-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write ``payload`` to ``path`` atomically + durably.

    Mirrors ``storage._write_bytes_atomic``'s pattern: ``tmp + fsync +
    rename + dir-fsync``. Atomicity (no half-written file visible to a
    reader) is ensured by ``os.replace``; **durability** under
    ungraceful shutdown requires the two fsyncs.

    Kept as a private helper (not imported from ``storage``) because
    ``storage._write_bytes_atomic`` is module-private in that file and
    reaching across for it would be worse coupling than a small local
    copy. If a third layer needs the same primitive, promote it to
    ``storage`` with a public name.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    # Best-effort directory fsync on POSIX so the rename itself is
    # flushed to the directory entry. Windows does not expose a
    # POSIX-style directory fd; skipped there, matching ``storage``.
    if hasattr(os, "O_DIRECTORY"):
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _write_npy_atomic(path: Path, array: npt.NDArray[np.float32]) -> None:
    """Write a numpy array atomically + durably.

    Serialises ``array`` to an in-memory buffer via :func:`numpy.save`
    and hands the bytes to :func:`_atomic_write_bytes`. Writing through
    a ``BytesIO`` also sidesteps :func:`numpy.save`'s path magic: when
    passed a path-like whose name does *not* end in ``.npy``, numpy
    silently appends the extension — for a tmp path like
    ``l3_centroids.npy.tmp`` that would write to
    ``l3_centroids.npy.tmp.npy`` and leave ``tmp.replace(path)`` failing
    with ``FileNotFoundError``. Bytes in → bytes out: no surprises.
    """
    buf = io.BytesIO()
    np.save(buf, array)
    _atomic_write_bytes(path, buf.getvalue())


def main(argv: list[str] | None = None) -> int:
    repo_root = _resolve_repo_root()

    parser = argparse.ArgumentParser(
        description="L3 clustering — HDBSCAN/KMeans over L2 pain/expectation nodes.",
    )
    parser.add_argument(
        "--graphs",
        type=Path,
        default=repo_root / DEFAULT_GRAPHS,
        help=f"L2 graphs JSONL (default: {DEFAULT_GRAPHS}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / DEFAULT_CLUSTERS,
        help=f"Clusters JSONL output (default: {DEFAULT_CLUSTERS}).",
    )
    parser.add_argument(
        "--centroids",
        type=Path,
        default=repo_root / DEFAULT_CENTROIDS,
        help=f"Centroids .npy output (default: {DEFAULT_CENTROIDS}).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Optional run_id; default is 'l3-YYYYmmddTHHMMSSffffff' at UTC "
            "now (microseconds avoid same-second collisions)."
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--min-cluster-size",
        type=int,
        default=MIN_CLUSTER_SIZE_DEFAULT,
        help=f"HDBSCAN min_cluster_size (default: {MIN_CLUSTER_SIZE_DEFAULT}).",
    )
    parser.add_argument(
        "--kmeans-k",
        type=int,
        default=KMEANS_FALLBACK_K,
        help=f"KMeans k for fallback (default: {KMEANS_FALLBACK_K}).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    graphs = load_l2_graphs(args.graphs)
    _log.info("loaded %d L2 graphs from %s", len(graphs), args.graphs)

    run_id = args.run_id or _default_run_id()

    insight_clusters, run_provenance, centroids_stacked = run_clustering(
        graphs,
        seed=args.seed,
        min_cluster_size=args.min_cluster_size,
        kmeans_k=args.kmeans_k,
        centroids_path=args.centroids,
    )
    _log.info(
        "produced %d insight clusters (fallback=%s)",
        len(insight_clusters),
        run_provenance["fallback_reason"] or "no",
    )

    # Write centroids .npy first: the clusters jsonl's sidecar input_hashes
    # reference this file, so it must exist on disk before we hash it.
    _write_npy_atomic(args.centroids, centroids_stacked)
    _log.info(
        "wrote centroids shape=%r to %s",
        centroids_stacked.shape,
        args.centroids,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)

    graphs_hash = hash_file(args.graphs)
    centroids_hash = hash_file(args.centroids)

    meta = write_jsonl_atomic(
        args.output,
        [c.model_dump(mode="json") for c in insight_clusters],
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes={
            args.graphs.name: graphs_hash,
            args.centroids.name: centroids_hash,
        },
        skill_hashes={},  # L3 uses no skill — embeddings + HDBSCAN/KMeans only
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d clusters to %s (sha256=%s…)",
        len(insight_clusters),
        args.output,
        meta.artifact_sha256[:16],
    )

    # Provenance audit file — sibling to the jsonl. Intentionally *not*
    # written through write_jsonl_atomic: ArtifactMeta's schema is fixed
    # and cannot absorb encoder/clustering runtime tuples. This file is
    # for humans (and replay tooling) to cross-check drift.
    #
    # Uses the same atomic + durable write primitive as the .npy path.
    # provenance.json is auditor-facing, not load-bearing for ADR-011
    # replay verification (see ARCHITECTURE.md §4.4), but durability
    # still matters for the reviewer use-case: a lost provenance.json
    # forces a full rerun to recover the encoder+clustering runtime
    # tuple, and there is no benefit to weaker durability here.
    provenance_path = args.output.with_suffix(".provenance.json")
    provenance_payload = (
        json.dumps(run_provenance, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(provenance_path, provenance_payload)
    _log.info("wrote L3 run provenance to %s", provenance_path)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
