"""Tests for ``auditable_design.layers.l3_cluster``.

Strategy
--------
Most tests mock the encoder via ``monkeypatch.setattr(l3_cluster, "encode", ...)``
with a deterministic synthetic embedder that produces unit-norm vectors
with pre-baked cluster structure. This keeps every unit test under ~50 ms
and keeps the assertions about "did we cluster correctly" independent of
torch/sentence-transformers weight drift.

One integration canary (:class:`TestEncoderCanary`) exercises the full
pipeline against the real MiniLM model on a tiny corpus. First run
downloads ~80 MB into the Hugging Face cache; subsequent runs are fully
local and take <10 s. No pytest marker gates it — the test suite's budget
tolerates that one slow test today, and a marker would mean someone has
to remember to run a second command to exercise the full path.

Structure mirrors ``l3_cluster.py``'s sections:
IO → helpers → core pipeline → end-to-end ``run_clustering`` → CLI.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pydantic
import pytest

from auditable_design.layers import l3_cluster
from auditable_design.layers.l3_cluster import (
    CLUSTERABLE_NODE_TYPES,
    DEFAULT_CENTROIDS,
    DEFAULT_CLUSTERS,
    DEFAULT_GRAPHS,
    KMEANS_FALLBACK_K,
    LAYER_NAME,
    REPRESENTATIVE_QUOTES_K,
    SCHEMA_VERSION,
    _atomic_write_bytes,
    _default_run_id,
    _normalize_labels,
    _write_npy_atomic,
    aggregate_review_membership,
    build_insight_clusters,
    cluster_hdbscan,
    cluster_kmeans,
    compute_centroids,
    extract_clusterable_nodes,
    load_l2_graphs,
    run_clustering,
    select_representative_quotes,
)
from auditable_design.schemas import ComplaintGraph, InsightCluster

# =============================================================================
# Helpers — graph fixtures and fake encoder
# =============================================================================

# ``FakeEncode`` is the exact shape ``run_clustering`` expects from
# :func:`auditable_design.embedders.local_encoder.encode`.
FakeEncode = Callable[..., tuple[npt.NDArray[np.float32], dict[str, Any]]]


def _node(
    node_id: str,
    node_type: str,
    verbatim_quote: str,
    source: str,
) -> dict[str, Any]:
    """Build a node payload with offsets computed from ``source``.

    ``ComplaintNode`` validates ``quote_end - quote_start == len(quote)``,
    so this helper fails loudly if ``verbatim_quote`` isn't present in
    ``source`` — saves the test author from silently mis-building fixtures.
    """
    start = source.find(verbatim_quote)
    if start < 0:
        raise AssertionError(f"quote {verbatim_quote!r} not substring of {source!r}")
    return {
        "node_id": node_id,
        "node_type": node_type,
        "verbatim_quote": verbatim_quote,
        "quote_start": start,
        "quote_end": start + len(verbatim_quote),
    }


def _graph(
    review_id: str,
    node_specs: list[tuple[str, str, str]],
    source: str,
) -> ComplaintGraph:
    """Build a :class:`ComplaintGraph` from ``(node_id, node_type, quote)`` tuples.

    Edges are left empty — L3 never reads edges, only node types and
    quotes, so constructing edge fixtures would be churn.
    """
    nodes = [_node(nid, ntype, q, source) for nid, ntype, q in node_specs]
    return ComplaintGraph.model_validate(
        {"review_id": review_id, "nodes": nodes, "edges": []}
    )


def _make_fake_encode(
    quote_to_cluster: dict[str, int],
    *,
    dim: int = 8,
    jitter: float = 0.02,
) -> FakeEncode:
    """Factory: deterministic synthetic encoder aligned to cluster basis vectors.

    Each quote maps to a base direction along a canonical basis vector
    (``basis[cluster_id % dim]``). Deterministic per-quote jitter keeps
    points distinct (HDBSCAN behaves badly on exact duplicates) while
    preserving the cluster-vs-cluster separation so both HDBSCAN and
    KMeans produce the expected clustering.

    Returned embeddings are unit-norm float32 — the same contract the
    real encoder guarantees, which matters because downstream code
    (``select_representative_quotes``) relies on it for the
    cosine-via-dot-product path.
    """

    def fake_encode(
        texts: list[str],
        *,
        model_name: str = "fake-encoder",
        seed: int,
    ) -> tuple[npt.NDArray[np.float32], dict[str, Any]]:
        if not texts:
            raise ValueError("encode() called with empty input")  # mirror real contract
        # Offset the seed used for jitter so the noise added per-quote is
        # not byte-identical to any downstream array a test might seed
        # with the same value. The constant 17 is arbitrary — any
        # non-zero offset would do; this just keeps the fake from
        # colliding with common choices (0, 1, 42) in test arithmetic.
        rng = np.random.default_rng(seed + 17)
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, text in enumerate(texts):
            cid = quote_to_cluster.get(text, 0)
            v = np.zeros(dim, dtype=np.float32)
            v[cid % dim] = 1.0
            noise = rng.standard_normal(dim).astype(np.float32) * jitter
            v = v + noise
            v = v / (float(np.linalg.norm(v)) + 1e-12)
            out[i] = v
        provenance: dict[str, Any] = {
            "model_name": model_name,
            "model_weights_hash": "0" * 16,
            "embedding_dim": dim,
            "normalize_embeddings": True,
            "seed": seed,
            "device": "cpu",
            "torch_version": "fake",
            "sentence_transformers_version": "fake",
            "numpy_version": np.__version__,
            "python_version": "fake",
            "platform": "fake",
        }
        return out, provenance

    return fake_encode


def _three_cluster_fixture() -> tuple[list[ComplaintGraph], dict[str, int]]:
    """5 graphs × 3 clusterable nodes each = 15 quotes in 3 clean clusters.

    Returns ``(graphs, quote_to_cluster)``. Used by the end-to-end
    :func:`run_clustering` tests and the CLI test.
    """
    # Cluster 0: paywall; Cluster 1: streak; Cluster 2: audio.
    clusters = {
        0: [
            "paywall everywhere",
            "gated behind payment",
            "must subscribe now",
            "cost too high",
            "forced to pay up",
        ],
        1: [
            "lost my streak",
            "streak broken again",
            "streak frozen incorrectly",
            "streak reset overnight",
            "daily streak disappeared",
        ],
        2: [
            "audio crackles",
            "voice sounds robotic",
            "speech too quiet",
            "pronunciation skipped",
            "sound cuts out",
        ],
    }
    # Invert the clusters dict once up front so the per-graph loop below
    # is a single pass over (cid, quote) pairs instead of a nested
    # linear search for each quote. The graph's ``node.verbatim_quote``
    # is the key used by ``_make_fake_encode`` at lookup time.
    quote_to_cluster: dict[str, int] = {
        quote: cid for cid, quotes in clusters.items() for quote in quotes
    }
    graphs: list[ComplaintGraph] = []
    # Flatten: each cluster supplies one quote per graph, i goes 0..4.
    for i in range(5):
        src_parts: list[str] = []
        specs: list[tuple[str, str, str]] = []
        for cid, quotes in clusters.items():
            q = quotes[i]
            src_parts.append(q)
            # Alternate pain / expectation to exercise both filters.
            ntype = "pain" if cid % 2 == 0 else "expectation"
            specs.append((f"n{cid}", ntype, q))
        # Tack on one non-clusterable node so each graph has 3+ nodes
        # (ComplaintGraph requires min_length=3) and we verify the filter
        # skips it. The triggered_element quote must also be in source.
        filler = "some context"
        src_parts.append(filler)
        specs.append(("nf", "triggered_element", filler))
        source = ". ".join(src_parts) + "."
        graphs.append(_graph(f"{i:040x}", specs, source))
    return graphs, quote_to_cluster


# =============================================================================
# Module-level constants
# =============================================================================


class TestConstants:
    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l3_cluster"

    def test_schema_version_positive(self) -> None:
        assert SCHEMA_VERSION >= 1

    def test_clusterable_node_types_closed(self) -> None:
        # Per concept.md §6 — clustering signal lives on pain/expectation
        # only. Other node types carry structure, not signal.
        assert CLUSTERABLE_NODE_TYPES == frozenset({"pain", "expectation"})

    def test_kmeans_k_in_concept_range(self) -> None:
        # concept.md §6 target is 5-8 clusters; fallback K is the midpoint.
        assert 5 <= KMEANS_FALLBACK_K <= 8

    def test_representative_quotes_matches_schema_cap(self) -> None:
        # InsightCluster enforces max_length=5 on representative_quotes;
        # REPRESENTATIVE_QUOTES_K must not exceed that.
        assert REPRESENTATIVE_QUOTES_K == 5

    def test_default_paths_under_data_derived(self) -> None:
        assert DEFAULT_GRAPHS == Path("data/derived/l2_graphs.jsonl")
        assert DEFAULT_CLUSTERS == Path("data/derived/l3_clusters.jsonl")
        assert DEFAULT_CENTROIDS == Path("data/derived/l3_centroids.npy")


# =============================================================================
# load_l2_graphs
# =============================================================================


class TestLoadL2Graphs:
    def test_round_trip(self, tmp_path: Path) -> None:
        g = _graph(
            "a" * 40,
            [
                ("n1", "pain", "Paywall"),
                ("n2", "triggered_element", "annoying"),
                ("n3", "expectation", "Used to be free"),
            ],
            source="Paywall is annoying. Used to be free.",
        )
        path = tmp_path / "graphs.jsonl"
        path.write_text(json.dumps(g.model_dump(mode="json")) + "\n")
        got = load_l2_graphs(path)
        assert len(got) == 1
        assert got[0].review_id == "a" * 40
        assert len(got[0].nodes) == 3

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        g = _graph(
            "b" * 40,
            [
                ("n1", "pain", "slow"),
                ("n2", "pain", "lag"),
                ("n3", "expectation", "fast"),
            ],
            source="slow. lag. fast.",
        )
        path = tmp_path / "graphs.jsonl"
        payload = json.dumps(g.model_dump(mode="json"))
        path.write_text(f"\n{payload}\n\n")
        got = load_l2_graphs(path)
        assert len(got) == 1

    def test_malformed_json_line_raises(self, tmp_path: Path) -> None:
        # ``load_l2_graphs`` does NOT silently skip malformed lines —
        # upstream L2 is contractually supposed to emit valid JSONL,
        # so a malformed row is a correctness bug, not noise.
        path = tmp_path / "graphs.jsonl"
        path.write_text("{this is not valid json\n")
        with pytest.raises(ValueError, match=r"invalid JSON at .*:1"):
            load_l2_graphs(path)

    def test_schema_violation_raises(self, tmp_path: Path) -> None:
        # ComplaintGraph requires 3-7 nodes; 0 triggers pydantic.
        # Pinned to pydantic.ValidationError (rather than bare Exception)
        # so a future refactor that accidentally raises a non-pydantic
        # error earlier — e.g. a TypeError from a broken loader — fails
        # the test instead of silently passing it.
        path = tmp_path / "graphs.jsonl"
        path.write_text(json.dumps({"review_id": "a" * 40, "nodes": [], "edges": []}) + "\n")
        with pytest.raises(pydantic.ValidationError):
            load_l2_graphs(path)


# =============================================================================
# extract_clusterable_nodes
# =============================================================================


class TestExtractClusterableNodes:
    def test_filters_to_pain_and_expectation(self) -> None:
        source = "paywall. annoying. free. workaround. gone."
        g = _graph(
            "a" * 40,
            [
                ("n1", "triggered_element", "paywall"),
                ("n2", "pain", "annoying"),
                ("n3", "expectation", "free"),
                ("n4", "workaround", "workaround"),
                ("n5", "lost_value", "gone"),
            ],
            source=source,
        )
        quotes, index = extract_clusterable_nodes([g])
        # Only pain+expectation retained.
        assert quotes == ["annoying", "free"]
        assert index == [("a" * 40, "n2"), ("a" * 40, "n3")]

    def test_empty_input_returns_empty(self) -> None:
        quotes, index = extract_clusterable_nodes([])
        assert quotes == []
        assert index == []

    def test_preserves_order_across_graphs(self) -> None:
        g1 = _graph(
            "1" * 40,
            [("n1", "pain", "a"), ("n2", "pain", "b"), ("n3", "expectation", "c")],
            source="a. b. c.",
        )
        g2 = _graph(
            "2" * 40,
            [("n1", "pain", "d"), ("n2", "pain", "e"), ("n3", "expectation", "f")],
            source="d. e. f.",
        )
        quotes, index = extract_clusterable_nodes([g1, g2])
        # Order = graph order × node order within each graph.
        assert quotes == ["a", "b", "c", "d", "e", "f"]
        assert [rid for rid, _ in index] == ["1" * 40] * 3 + ["2" * 40] * 3

    def test_index_parallel_to_quotes(self) -> None:
        # Contract: len(quotes) == len(index), index[i] pairs with quotes[i].
        g = _graph(
            "a" * 40,
            [("n1", "pain", "x"), ("n2", "triggered_element", "y"), ("n3", "expectation", "z")],
            source="x. y. z.",
        )
        quotes, index = extract_clusterable_nodes([g])
        assert len(quotes) == len(index) == 2


# =============================================================================
# cluster_hdbscan
# =============================================================================


class TestClusterHDBSCAN:
    def test_happy_path_two_clusters(self) -> None:
        # Two tight clusters of 4 points each along orthogonal basis vectors.
        rng = np.random.default_rng(0)
        cluster_a = np.zeros((4, 4), dtype=np.float32)
        cluster_a[:, 0] = 1.0
        cluster_a += rng.standard_normal((4, 4)).astype(np.float32) * 0.01
        cluster_b = np.zeros((4, 4), dtype=np.float32)
        cluster_b[:, 1] = 1.0
        cluster_b += rng.standard_normal((4, 4)).astype(np.float32) * 0.01
        embeddings = np.vstack([cluster_a, cluster_b])
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)

        labels, provenance = cluster_hdbscan(embeddings.astype(np.float32), min_cluster_size=3)
        assert labels.dtype == np.int64
        assert labels.shape == (8,)
        # Expect exactly two non-noise clusters.
        unique = np.unique(labels[labels >= 0])
        assert unique.size == 2
        assert provenance["algorithm"] == "hdbscan"
        assert provenance["min_cluster_size"] == 3
        assert provenance["metric"] == "euclidean"
        assert "hdbscan_version" in provenance

    def test_noise_label_is_negative_one(self) -> None:
        # Points scattered uniformly — HDBSCAN labels most as noise.
        rng = np.random.default_rng(1)
        embeddings = rng.standard_normal((6, 4)).astype(np.float32)
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        labels, _ = cluster_hdbscan(embeddings, min_cluster_size=5)
        # With min_cluster_size=5 and 6 random points, everything should
        # land in noise (-1).
        assert (labels == -1).all()


# =============================================================================
# cluster_kmeans
# =============================================================================


class TestClusterKMeans:
    def test_happy_path(self) -> None:
        rng = np.random.default_rng(0)
        embeddings = rng.standard_normal((12, 6)).astype(np.float32)
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        labels, provenance = cluster_kmeans(embeddings, k=3, seed=42)
        assert labels.dtype == np.int64
        assert labels.shape == (12,)
        # No noise in KMeans; labels are in [0, k).
        assert labels.min() == 0
        assert labels.max() == 2
        assert provenance["algorithm"] == "kmeans"
        assert provenance["k"] == 3
        assert provenance["seed"] == 42
        assert provenance["n_init"] == 10
        assert "sklearn_version" in provenance

    def test_deterministic_under_same_seed(self) -> None:
        rng = np.random.default_rng(2)
        embeddings = rng.standard_normal((10, 4)).astype(np.float32)
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        labels_a, _ = cluster_kmeans(embeddings, k=3, seed=7)
        labels_b, _ = cluster_kmeans(embeddings, k=3, seed=7)
        assert np.array_equal(labels_a, labels_b)

    def test_n_init_pinned_to_10(self) -> None:
        # Documents the explicit n_init value; see cluster_kmeans docstring
        # for why "auto" would be wrong.
        embeddings = np.eye(6, dtype=np.float32)
        _labels, provenance = cluster_kmeans(embeddings, k=2, seed=0)
        assert provenance["n_init"] == 10


# =============================================================================
# _normalize_labels
# =============================================================================


class TestNormalizeLabels:
    def test_preserves_noise(self) -> None:
        labels = np.array([-1, 0, 1, -1, 0], dtype=np.int64)
        out = _normalize_labels(labels)
        assert (out[np.array([0, 3])] == -1).all()

    def test_already_contiguous_is_identity(self) -> None:
        labels = np.array([0, 1, 2, 1, 0], dtype=np.int64)
        out = _normalize_labels(labels)
        assert np.array_equal(out, labels)

    def test_remaps_sparse_labels_to_contiguous(self) -> None:
        # e.g. a clusterer returned 0, 3, 5, -1, 3, 5 — we want 0, 1, 2, -1, 1, 2.
        labels = np.array([0, 3, 5, -1, 3, 5], dtype=np.int64)
        out = _normalize_labels(labels)
        assert np.array_equal(out, np.array([0, 1, 2, -1, 1, 2], dtype=np.int64))

    def test_all_noise_passthrough(self) -> None:
        labels = np.array([-1, -1, -1], dtype=np.int64)
        out = _normalize_labels(labels)
        assert np.array_equal(out, labels)

    def test_preserves_dtype(self) -> None:
        labels = np.array([5, 5, 7], dtype=np.int64)
        out = _normalize_labels(labels)
        assert out.dtype == np.int64


# =============================================================================
# compute_centroids
# =============================================================================


class TestComputeCentroids:
    def test_happy_path_means_correct(self) -> None:
        embeddings = np.array(
            [
                [1.0, 0.0],
                [1.0, 0.0],
                [0.0, 1.0],
                [0.0, 1.0],
            ],
            dtype=np.float32,
        )
        labels = np.array([0, 0, 1, 1], dtype=np.int64)
        centroids = compute_centroids(embeddings, labels)
        assert set(centroids.keys()) == {0, 1}
        np.testing.assert_allclose(centroids[0], [1.0, 0.0])
        np.testing.assert_allclose(centroids[1], [0.0, 1.0])

    def test_skips_noise(self) -> None:
        embeddings = np.array([[1.0, 0.0], [0.5, 0.5]], dtype=np.float32)
        labels = np.array([0, -1], dtype=np.int64)
        centroids = compute_centroids(embeddings, labels)
        # Noise (-1) must not produce a centroid entry.
        assert list(centroids.keys()) == [0]

    def test_returns_float32(self) -> None:
        embeddings = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
        labels = np.array([0, 0], dtype=np.int64)
        centroids = compute_centroids(embeddings, labels)
        assert centroids[0].dtype == np.float32

    def test_empty_returns_empty(self) -> None:
        embeddings = np.zeros((0, 4), dtype=np.float32)
        labels = np.zeros((0,), dtype=np.int64)
        assert compute_centroids(embeddings, labels) == {}


# =============================================================================
# select_representative_quotes
# =============================================================================


class TestSelectRepresentativeQuotes:
    def test_top_k_closest_to_centroid(self) -> None:
        # Three points in cluster 0: two near (1,0), one away. Top-2 should
        # pick the two closest.
        embeddings = np.array(
            [
                [1.0, 0.0],      # closest to centroid
                [0.99, 0.01],    # near
                [0.7, 0.71],     # far-ish (won't make top-2)
            ],
            dtype=np.float32,
        )
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        labels = np.array([0, 0, 0], dtype=np.int64)
        centroids = {0: np.array([1.0, 0.0], dtype=np.float32)}
        reps = select_representative_quotes(
            embeddings, labels, ["closest", "near", "far"], centroids, k=2
        )
        assert reps[0] == ["closest", "near"]

    def test_cluster_with_fewer_than_k_members_returns_all(self) -> None:
        # If cluster has 2 members and we ask for 5, we get 2 (not crash).
        embeddings = np.array([[1.0, 0.0], [0.9, 0.1]], dtype=np.float32)
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        labels = np.array([0, 0], dtype=np.int64)
        centroids = {0: np.array([1.0, 0.0], dtype=np.float32)}
        reps = select_representative_quotes(embeddings, labels, ["a", "b"], centroids, k=5)
        assert len(reps[0]) == 2

    def test_ignores_non_member_points(self) -> None:
        # Members: only the first two; third belongs to cluster 1 and
        # should not surface in cluster 0's representatives.
        embeddings = np.array(
            [[1.0, 0.0], [0.95, 0.05], [0.0, 1.0]], dtype=np.float32
        )
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        labels = np.array([0, 0, 1], dtype=np.int64)
        centroids = {
            0: np.array([1.0, 0.0], dtype=np.float32),
            1: np.array([0.0, 1.0], dtype=np.float32),
        }
        reps = select_representative_quotes(embeddings, labels, ["q0a", "q0b", "q1"], centroids)
        assert "q1" not in reps[0]
        assert "q0a" in reps[0] and "q0b" in reps[0]

    def test_zero_centroid_does_not_nan(self) -> None:
        # Pathological: centroid is zero vector. The +1e-12 guard should
        # keep the math finite (similarities will all be ~0, picks arbitrary).
        embeddings = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        labels = np.array([0, 0], dtype=np.int64)
        centroids = {0: np.array([0.0, 0.0], dtype=np.float32)}
        reps = select_representative_quotes(embeddings, labels, ["a", "b"], centroids, k=2)
        # Didn't raise, didn't emit NaN quotes.
        assert set(reps[0]) == {"a", "b"}


# =============================================================================
# aggregate_review_membership
# =============================================================================


class TestAggregateReviewMembership:
    def test_multi_membership_review_in_two_clusters(self) -> None:
        # review "r1" has one node in cluster 0 and one in cluster 1 →
        # must appear in both ``member_review_ids`` lists. This is the
        # core multi-membership invariant from ``InsightCluster``'s docstring.
        labels = np.array([0, 1], dtype=np.int64)
        node_index = [("r1", "n1"), ("r1", "n2")]
        membership = aggregate_review_membership(labels, node_index)
        assert membership == {0: ["r1"], 1: ["r1"]}

    def test_deduplicates_within_cluster(self) -> None:
        # Same review contributes two nodes to cluster 0 — should appear once.
        labels = np.array([0, 0, 0], dtype=np.int64)
        node_index = [("r1", "n1"), ("r1", "n2"), ("r2", "n1")]
        membership = aggregate_review_membership(labels, node_index)
        assert membership == {0: ["r1", "r2"]}

    def test_skips_noise(self) -> None:
        labels = np.array([0, -1, 1], dtype=np.int64)
        node_index = [("r1", "n1"), ("r2", "n1"), ("r3", "n1")]
        membership = aggregate_review_membership(labels, node_index)
        # r2 had only a noise node; must not appear in any cluster.
        assert 0 in membership and 1 in membership
        all_members = set(membership[0]) | set(membership[1])
        assert "r2" not in all_members

    def test_sorts_review_ids_for_determinism(self) -> None:
        # Input in reverse-sorted order — output must be sorted.
        labels = np.array([0, 0, 0], dtype=np.int64)
        node_index = [("z", "n1"), ("b", "n1"), ("a", "n1")]
        membership = aggregate_review_membership(labels, node_index)
        assert membership[0] == ["a", "b", "z"]

    def test_empty_returns_empty(self) -> None:
        labels = np.zeros((0,), dtype=np.int64)
        membership = aggregate_review_membership(labels, [])
        assert membership == {}


# =============================================================================
# build_insight_clusters
# =============================================================================


class TestBuildInsightClusters:
    def test_happy_path(self, tmp_path: Path) -> None:
        centroids = {
            0: np.array([1.0, 0.0], dtype=np.float32),
            1: np.array([0.0, 1.0], dtype=np.float32),
        }
        reps = {0: ["q0a", "q0b"], 1: ["q1a"]}
        membership = {0: ["r1", "r2"], 1: ["r3"]}
        centroids_path = tmp_path / "l3_centroids.npy"
        clusters = build_insight_clusters(
            centroids=centroids,
            representative_quotes=reps,
            review_membership=membership,
            centroids_path=centroids_path,
        )
        assert len(clusters) == 2
        assert all(isinstance(c, InsightCluster) for c in clusters)
        # Sorted by cluster_id numeric tail.
        assert clusters[0].cluster_id == "cluster_00"
        assert clusters[1].cluster_id == "cluster_01"

    def test_label_is_unlabeled_placeholder(self) -> None:
        # Per module docstring §"Label lifecycle": L3 writes UNLABELED:*
        # prefixes so a reader can scan the intermediate artifact by eye.
        centroids = {0: np.array([1.0], dtype=np.float32)}
        clusters = build_insight_clusters(
            centroids=centroids,
            representative_quotes={0: ["q"]},
            review_membership={0: ["r1"]},
            centroids_path=Path("l3_centroids.npy"),
        )
        assert clusters[0].label.startswith("UNLABELED:")
        assert clusters[0].label == "UNLABELED:cluster_00"

    def test_centroid_vector_ref_format(self) -> None:
        # Format: "<basename>#<index>" — downstream resolves via
        # ``np.load(file)[int(index)]``.
        centroids = {
            0: np.array([1.0], dtype=np.float32),
            3: np.array([0.5], dtype=np.float32),
        }
        clusters = build_insight_clusters(
            centroids=centroids,
            representative_quotes={0: ["a"], 3: ["b"]},
            review_membership={0: ["r1"], 3: ["r3"]},
            centroids_path=Path("/tmp/data/l3_centroids.npy"),
        )
        # Basename (not full path).
        assert clusters[0].centroid_vector_ref == "l3_centroids.npy#0"
        assert clusters[1].centroid_vector_ref == "l3_centroids.npy#3"

    def test_invariant_violation_raises_runtime_error(self) -> None:
        # Centroid for cluster 5 but no review_membership entry — this
        # means upstream broke the "every clusterable node has a
        # review_id" invariant. We expect a loud RuntimeError, not a
        # silent KeyError.
        centroids = {0: np.array([1.0], dtype=np.float32), 5: np.array([1.0], dtype=np.float32)}
        reps = {0: ["q"], 5: ["q"]}
        membership = {0: ["r1"]}  # missing 5
        with pytest.raises(RuntimeError, match="invariant violation"):
            build_insight_clusters(
                centroids=centroids,
                representative_quotes=reps,
                review_membership=membership,
                centroids_path=Path("l3_centroids.npy"),
            )

    def test_member_review_ids_passthrough(self) -> None:
        centroids = {0: np.array([1.0], dtype=np.float32)}
        clusters = build_insight_clusters(
            centroids=centroids,
            representative_quotes={0: ["q"]},
            review_membership={0: ["r1", "r2", "r3"]},
            centroids_path=Path("l3_centroids.npy"),
        )
        assert clusters[0].member_review_ids == ["r1", "r2", "r3"]


# =============================================================================
# run_clustering — end-to-end with mocked encoder
# =============================================================================


class TestRunClustering:
    def test_happy_path_three_clusters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graphs, quote_to_cluster = _three_cluster_fixture()
        monkeypatch.setattr(l3_cluster, "encode", _make_fake_encode(quote_to_cluster))

        clusters, provenance, centroids_stacked = run_clustering(
            graphs,
            seed=42,
            centroids_path=Path("l3_centroids.npy"),
            min_cluster_size=3,
        )
        # With 15 points across 3 tight clusters, HDBSCAN must produce ≥2
        # clusters (could pick up the third too; robust to both paths).
        assert len(clusters) >= 2
        # Centroids stacked shape matches number of clusters.
        assert centroids_stacked.shape[0] == len(clusters)
        # Provenance blob has all required sections.
        assert "encoder" in provenance
        assert "clustering" in provenance
        assert "fallback_reason" in provenance
        assert provenance["node_count"] == 15  # 5 graphs × 3 clusterable nodes
        assert provenance["min_cluster_size"] == 3
        assert provenance["kmeans_k"] == KMEANS_FALLBACK_K

    def test_empty_graphs_raises(self) -> None:
        # Contract: empty input fails loud rather than emits zero clusters.
        # No encoder monkeypatch is needed — ``run_clustering`` short-
        # circuits on the empty-input check before ever calling encode().
        with pytest.raises(ValueError, match="no clusterable nodes"):
            run_clustering(
                [],
                seed=42,
                centroids_path=Path("l3_centroids.npy"),
            )

    def test_graphs_without_clusterable_nodes_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # All nodes are triggered_element/workaround/lost_value — filter empty.
        source = "paywall. workaround. gone."
        g = _graph(
            "a" * 40,
            [
                ("n1", "triggered_element", "paywall"),
                ("n2", "workaround", "workaround"),
                ("n3", "lost_value", "gone"),
            ],
            source=source,
        )
        monkeypatch.setattr(l3_cluster, "encode", _make_fake_encode({}))
        with pytest.raises(ValueError, match="no clusterable nodes"):
            run_clustering(
                [g],
                seed=42,
                centroids_path=Path("l3_centroids.npy"),
            )

    def test_hdbscan_raises_triggers_kmeans_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate HDBSCAN blowing up — the fallback path must kick in
        # and produce clusters via KMeans, with ``fallback_reason`` set.
        graphs, quote_to_cluster = _three_cluster_fixture()
        monkeypatch.setattr(l3_cluster, "encode", _make_fake_encode(quote_to_cluster))

        def _boom(*_args: Any, **_kwargs: Any) -> tuple[npt.NDArray[np.int64], dict[str, Any]]:
            raise RuntimeError("synthetic HDBSCAN failure")

        monkeypatch.setattr(l3_cluster, "cluster_hdbscan", _boom)

        clusters, provenance, _ = run_clustering(
            graphs,
            seed=42,
            centroids_path=Path("l3_centroids.npy"),
            kmeans_k=3,
        )
        assert provenance["fallback_reason"] is not None
        assert "synthetic HDBSCAN failure" in provenance["fallback_reason"]
        assert provenance["clustering"]["algorithm"] == "kmeans"
        # KMeans always produces k non-noise clusters on non-degenerate input.
        assert len(clusters) == 3

    def test_hdbscan_zero_clusters_triggers_kmeans_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # HDBSCAN ran fine but found no clusters (everything noise) — the
        # code should record a ``fallback_reason`` and route to KMeans.
        graphs, quote_to_cluster = _three_cluster_fixture()
        monkeypatch.setattr(l3_cluster, "encode", _make_fake_encode(quote_to_cluster))

        def _all_noise(
            embeddings: npt.NDArray[np.float32], *, min_cluster_size: int
        ) -> tuple[npt.NDArray[np.int64], dict[str, Any]]:
            labels = np.full(embeddings.shape[0], -1, dtype=np.int64)
            return labels, {"algorithm": "hdbscan", "min_cluster_size": min_cluster_size}

        monkeypatch.setattr(l3_cluster, "cluster_hdbscan", _all_noise)

        clusters, provenance, _ = run_clustering(
            graphs,
            seed=42,
            centroids_path=Path("l3_centroids.npy"),
            kmeans_k=3,
        )
        assert provenance["fallback_reason"] is not None
        assert "0 valid clusters" in provenance["fallback_reason"]
        assert provenance["clustering"]["algorithm"] == "kmeans"
        assert len(clusters) == 3

    def test_kmeans_fallback_cannot_run_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # HDBSCAN returns zero clusters AND there are fewer samples than
        # kmeans_k — the function must turn sklearn's ValueError into our
        # friendlier RuntimeError.
        source = "a. b. c."
        g = _graph(
            "a" * 40,
            [("n1", "pain", "a"), ("n2", "pain", "b"), ("n3", "expectation", "c")],
            source=source,
        )
        monkeypatch.setattr(
            l3_cluster,
            "encode",
            _make_fake_encode({"a": 0, "b": 0, "c": 0}),
        )

        def _boom(*_args: Any, **_kwargs: Any) -> tuple[npt.NDArray[np.int64], dict[str, Any]]:
            raise RuntimeError("hdbscan down")

        monkeypatch.setattr(l3_cluster, "cluster_hdbscan", _boom)

        with pytest.raises(RuntimeError, match="KMeans fallback cannot run"):
            run_clustering(
                [g],
                seed=42,
                centroids_path=Path("l3_centroids.npy"),
                kmeans_k=6,  # 3 samples < 6 → fallback can't run
            )

    def test_centroids_stacked_row_order_matches_cluster_ids(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # After ``_normalize_labels``, cluster_id == row index in the
        # stacked .npy. ``centroid_vector_ref`` values should match the
        # row ordering.
        graphs, quote_to_cluster = _three_cluster_fixture()
        monkeypatch.setattr(l3_cluster, "encode", _make_fake_encode(quote_to_cluster))

        clusters, _, centroids_stacked = run_clustering(
            graphs,
            seed=42,
            centroids_path=Path("l3_centroids.npy"),
            min_cluster_size=3,
        )
        for i, c in enumerate(clusters):
            # Every ref should point to row i of the stacked array.
            assert c.centroid_vector_ref == f"l3_centroids.npy#{i}"
            assert c.cluster_id == f"cluster_{i:02d}"
        # Sanity: stacked array shape agrees with cluster count.
        assert centroids_stacked.shape[0] == len(clusters)


# =============================================================================
# Atomic writes
# =============================================================================


class TestAtomicWrites:
    def test_atomic_write_bytes_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "file.bin"
        payload = b"hello world\n"
        _atomic_write_bytes(path, payload)
        assert path.read_bytes() == payload
        # Creates parent dir if missing (the ``sub/`` above).
        assert path.parent.is_dir()

    def test_atomic_write_bytes_overwrites(self, tmp_path: Path) -> None:
        path = tmp_path / "file.bin"
        _atomic_write_bytes(path, b"first")
        _atomic_write_bytes(path, b"second")
        assert path.read_bytes() == b"second"

    def test_atomic_write_bytes_cleans_tmp(self, tmp_path: Path) -> None:
        # No ``.tmp`` sibling should remain after a successful write.
        path = tmp_path / "file.bin"
        _atomic_write_bytes(path, b"x")
        assert not (tmp_path / "file.bin.tmp").exists()

    def test_write_npy_atomic_roundtrip(self, tmp_path: Path) -> None:
        # ``np.save`` normally appends .npy to non-.npy paths; the whole
        # point of the ``BytesIO`` trick is to neutralise that behavior
        # so the file lands at exactly the path we asked for.
        path = tmp_path / "array.npy"
        array = np.arange(12, dtype=np.float32).reshape(3, 4)
        _write_npy_atomic(path, array)
        assert path.exists()
        loaded = np.load(path)
        assert loaded.dtype == np.float32
        np.testing.assert_array_equal(loaded, array)

    def test_write_npy_atomic_does_not_append_extension(self, tmp_path: Path) -> None:
        # Regression guard for C-01: if ``np.save`` went back to
        # auto-appending .npy to a non-.npy path (e.g. the tmp path
        # l3_centroids.npy.tmp), the final file would land at
        # l3_centroids.npy.tmp.npy and ``tmp.replace`` would fail.
        path = tmp_path / "no_extension"
        _write_npy_atomic(path, np.zeros(3, dtype=np.float32))
        assert path.exists()
        # No stray sibling file.
        assert not (tmp_path / "no_extension.npy").exists()


# =============================================================================
# _default_run_id
# =============================================================================


class TestDefaultRunId:
    def test_starts_with_l3_prefix(self) -> None:
        assert _default_run_id().startswith("l3-")

    def test_has_microsecond_precision(self) -> None:
        # Format: l3-YYYYmmddTHHMMSSffffff  — 8 date chars, T, 6 time + 6 micro.
        # Regex (rather than length arithmetic) so a failure message
        # points at the format mismatch directly, not at an opaque
        # length assertion.
        import re

        rid = _default_run_id()
        assert re.fullmatch(r"l3-\d{8}T\d{12}", rid) is not None

    def test_passes_run_id_pattern(self) -> None:
        # storage.validate_run_id would reject the value if it contained
        # illegal characters (spaces, slashes). This test documents the
        # compatibility contract with ``storage.RUN_ID_PATTERN``.
        from auditable_design.storage import RUN_ID_PATTERN

        assert RUN_ID_PATTERN.match(_default_run_id()) is not None


# =============================================================================
# CLI — end-to-end with mocked encoder
# =============================================================================


class TestMainCLI:
    def _write_graphs(self, path: Path, graphs: list[ComplaintGraph]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(g.model_dump(mode="json")) for g in graphs) + "\n"
        )

    def test_end_to_end_writes_clusters_centroids_meta_provenance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graphs, quote_to_cluster = _three_cluster_fixture()
        graphs_path = tmp_path / "data" / "derived" / "l2_graphs.jsonl"
        clusters_path = tmp_path / "data" / "derived" / "l3_clusters.jsonl"
        centroids_path = tmp_path / "data" / "derived" / "l3_centroids.npy"
        self._write_graphs(graphs_path, graphs)

        # Pin repo root so allowed-roots check passes even when the test
        # cwd is outside the project.
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        monkeypatch.setattr(l3_cluster, "_resolve_repo_root", lambda: tmp_path)

        monkeypatch.setattr(l3_cluster, "encode", _make_fake_encode(quote_to_cluster))

        rc = l3_cluster.main(
            [
                "--graphs", str(graphs_path),
                "--output", str(clusters_path),
                "--centroids", str(centroids_path),
                "--run-id", "test-run",
                "--seed", "42",
                "--min-cluster-size", "3",
            ]
        )
        assert rc == 0
        assert clusters_path.exists()
        assert centroids_path.exists()

        # Sidecar .meta.json.
        meta_path = clusters_path.with_suffix(clusters_path.suffix + ".meta.json")
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["run_id"] == "test-run"
        assert meta["layer"] == LAYER_NAME
        # L3 is pure compute — skill_hashes must be empty.
        assert meta["skill_hashes"] == {}
        # Input hashes cover both L2 graphs and the freshly-written centroids.
        assert graphs_path.name in meta["input_hashes"]
        assert centroids_path.name in meta["input_hashes"]

        # Provenance sidecar.
        provenance_path = clusters_path.with_suffix(".provenance.json")
        assert provenance_path.exists()
        provenance = json.loads(provenance_path.read_text())
        assert "encoder" in provenance
        assert "clustering" in provenance
        assert provenance["node_count"] == 15

        # Cluster records are well-formed InsightClusters.
        cluster_lines = [
            json.loads(x) for x in clusters_path.read_text().splitlines() if x.strip()
        ]
        assert len(cluster_lines) >= 2
        for row in cluster_lines:
            c = InsightCluster.model_validate(row)
            assert c.label.startswith("UNLABELED:")
            assert c.centroid_vector_ref.startswith(f"{centroids_path.name}#")

        # Centroids .npy is loadable and its row count matches cluster count.
        centroids_array = np.load(centroids_path)
        assert centroids_array.shape[0] == len(cluster_lines)
        assert centroids_array.dtype == np.float32

    def test_run_id_default_used_when_flag_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Omitting --run-id should fall back to ``_default_run_id()``.
        graphs, quote_to_cluster = _three_cluster_fixture()
        graphs_path = tmp_path / "data" / "derived" / "l2_graphs.jsonl"
        clusters_path = tmp_path / "data" / "derived" / "l3_clusters.jsonl"
        centroids_path = tmp_path / "data" / "derived" / "l3_centroids.npy"
        self._write_graphs(graphs_path, graphs)

        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        monkeypatch.setattr(l3_cluster, "_resolve_repo_root", lambda: tmp_path)
        monkeypatch.setattr(l3_cluster, "encode", _make_fake_encode(quote_to_cluster))

        rc = l3_cluster.main(
            [
                "--graphs", str(graphs_path),
                "--output", str(clusters_path),
                "--centroids", str(centroids_path),
                "--min-cluster-size", "3",
            ]
        )
        assert rc == 0
        meta = json.loads(
            clusters_path.with_suffix(clusters_path.suffix + ".meta.json").read_text()
        )
        assert meta["run_id"].startswith("l3-")


# =============================================================================
# Integration canary — one real-encoder run, end-to-end
# =============================================================================


class TestEncoderCanary:
    """Single real-encoder smoke test over a tiny corpus.

    Covers drift that the mocked encoder cannot catch:

    - sentence-transformers / torch version compatibility
    - The unit-norm contract from the real encoder survives clustering
    - Provenance dict captures real torch / sentence_transformers versions
    - .npy + .meta.json + .provenance.json all land on disk

    First run downloads ~80 MB of weights into the HF cache; subsequent
    runs are fully local (<10 s). No pytest marker gates this — see
    module docstring.
    """

    def test_full_pipeline_against_real_encoder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Six graphs × 2 clusterable nodes each — two thematic clusters
        # (paywall, streak). Real MiniLM should be able to tell them apart
        # even at this tiny scale, though we don't assert cluster count
        # (fragile against encoder drift). We assert the pipeline runs
        # cleanly and writes all expected artifacts.
        sources = [
            ("paywall everywhere. gated behind payment.", "paywall everywhere", "gated behind payment"),
            ("forced to pay up. cost too high.", "forced to pay up", "cost too high"),
            ("must subscribe now. no free tier.", "must subscribe now", "no free tier"),
            ("lost my streak. streak broken again.", "lost my streak", "streak broken again"),
            ("streak reset overnight. streak frozen incorrectly.", "streak reset overnight", "streak frozen incorrectly"),
            ("daily streak disappeared. streak gone.", "daily streak disappeared", "streak gone"),
        ]
        graphs: list[ComplaintGraph] = []
        for i, (src, q1, q2) in enumerate(sources):
            graphs.append(
                _graph(
                    f"{i:040x}",
                    [
                        ("n1", "pain", q1),
                        ("n2", "expectation", q2),
                        ("n3", "triggered_element", src.split(".")[0].strip() or q1),
                    ],
                    source=src,
                )
            )

        graphs_path = tmp_path / "data" / "derived" / "l2_graphs.jsonl"
        clusters_path = tmp_path / "data" / "derived" / "l3_clusters.jsonl"
        centroids_path = tmp_path / "data" / "derived" / "l3_centroids.npy"
        graphs_path.parent.mkdir(parents=True, exist_ok=True)
        graphs_path.write_text(
            "\n".join(json.dumps(g.model_dump(mode="json")) for g in graphs) + "\n"
        )

        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        monkeypatch.setattr(l3_cluster, "_resolve_repo_root", lambda: tmp_path)

        # No encode mock — exercise real sentence-transformers path.
        rc = l3_cluster.main(
            [
                "--graphs", str(graphs_path),
                "--output", str(clusters_path),
                "--centroids", str(centroids_path),
                "--run-id", "canary",
                "--seed", "42",
                "--min-cluster-size", "3",
                "--kmeans-k", "2",
            ]
        )
        assert rc == 0
        assert clusters_path.exists()
        assert centroids_path.exists()

        provenance_path = clusters_path.with_suffix(".provenance.json")
        provenance = json.loads(provenance_path.read_text())
        # Real encoder captures real versions.
        assert provenance["encoder"]["model_name"] == "sentence-transformers/all-MiniLM-L6-v2"
        assert provenance["encoder"]["device"] == "cpu"
        assert provenance["encoder"]["embedding_dim"] == 384
        assert provenance["encoder"]["torch_version"] != "fake"
        assert provenance["encoder"]["sentence_transformers_version"] != "fake"
        # Hash is the 16-hex-char truncated sha256 from the real weights.
        assert len(provenance["encoder"]["model_weights_hash"]) == 16

        # At least one cluster landed — either HDBSCAN found them, or
        # KMeans fallback fired. Either is a pass for the canary.
        cluster_lines = [
            json.loads(x) for x in clusters_path.read_text().splitlines() if x.strip()
        ]
        assert len(cluster_lines) >= 1
