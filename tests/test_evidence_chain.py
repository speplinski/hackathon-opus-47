"""Evidence chain from L4/L5 findings back to individual reviews.

L3 records which review each representative quote was taken from
(``InsightCluster.representative_review_ids``). L4 resolves a finding's
``evidence_quote_idxs`` through it into ``evidence_review_ids``; L5 takes
the union over the L4 findings a ranked entry collapses.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from auditable_design.layers import l3_cluster
from auditable_design.layers import l5_reconcile as l5
from auditable_design.schemas import InsightCluster
from tests import test_l3_cluster as t3
from tests import test_l5_reconcile as t5

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _cluster(**overrides: object) -> InsightCluster:
    base: dict[str, object] = {
        "cluster_id": "cluster_00",
        "label": "label",
        "member_review_ids": ["r1", "r2", "r3"],
        "centroid_vector_ref": "l3_centroids.npy#0",
        "representative_quotes": ["q0", "q1", "q2"],
    }
    base.update(overrides)
    return InsightCluster(**base)  # type: ignore[arg-type]


class TestSchema:
    def test_defaults_to_empty_for_old_clusters(self) -> None:
        c = _cluster()
        assert c.representative_review_ids == []
        assert c.evidence_review_ids_for([0, 2]) == []

    def test_resolves_quote_indices_in_order_without_duplicates(self) -> None:
        c = _cluster(representative_review_ids=["r2", "r1", "r2"])
        assert c.evidence_review_ids_for([2, 1, 0]) == ["r2", "r1"]
        assert c.evidence_review_ids_for([]) == []

    def test_length_must_match_quotes(self) -> None:
        with pytest.raises(ValidationError, match="align by position"):
            _cluster(representative_review_ids=["r1", "r2"])

    def test_ids_must_be_cluster_members(self) -> None:
        with pytest.raises(ValidationError, match="not in member_review_ids"):
            _cluster(representative_review_ids=["r1", "r2", "r9"])

    def test_round_trips_through_json(self) -> None:
        c = _cluster(representative_review_ids=["r1", "r2", "r3"])
        again = InsightCluster.model_validate_json(c.model_dump_json())
        assert again.representative_review_ids == ["r1", "r2", "r3"]


# ---------------------------------------------------------------------------
# L3
# ---------------------------------------------------------------------------


class TestL3:
    def test_indices_match_quotes(self) -> None:
        rng = np.random.default_rng(0)
        emb = rng.normal(size=(6, 4)).astype(np.float32)
        labels = np.array([0, 0, 0, 1, 1, 1], dtype=np.int64)
        quotes = [f"q{i}" for i in range(6)]
        centroids = l3_cluster.compute_centroids(emb, labels)
        idxs = l3_cluster.select_representative_indices(emb, labels, centroids, k=2)
        texts = l3_cluster.select_representative_quotes(
            emb, labels, quotes, centroids, k=2
        )
        for cid in idxs:
            assert [quotes[i] for i in idxs[cid]] == texts[cid]
            assert all(labels[i] == cid for i in idxs[cid])

    def test_run_clustering_records_review_per_quote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each representative quote's review id is the review whose L2
        node produced that quote."""
        topics = {0: "login fails", 1: "payment declined"}
        graphs = []
        quote_to_cluster: dict[str, int] = {}
        for r in range(10):
            cid = r % 2
            quote = f"{topics[cid]} case {r}"
            quote_to_cluster[quote] = cid
            source = f"{quote}. so annoying. I want my money back"
            graphs.append(
                t3._graph(
                    f"rev{r:02d}",
                    [
                        ("n1", "triggered_element", quote),
                        ("n2", "pain", "so annoying"),
                        ("n3", "workaround", "I want my money back"),
                    ],
                    source,
                )
            )
        monkeypatch.setattr(
            l3_cluster, "encode", t3._make_fake_encode(quote_to_cluster)
        )
        clusters, _, _ = l3_cluster.run_clustering(
            graphs,
            seed=42,
            centroids_path=Path("l3_centroids.npy"),
            min_cluster_size=3,
        )
        quote_by_review = {
            g.review_id: g.nodes[0].verbatim_quote for g in graphs
        }
        assert clusters
        for c in clusters:
            assert len(c.representative_review_ids) == len(c.representative_quotes)
            for rid, quote in zip(
                c.representative_review_ids, c.representative_quotes, strict=True
            ):
                assert quote_by_review[rid] == quote


# ---------------------------------------------------------------------------
# L4 — all six skill modules
# ---------------------------------------------------------------------------

L4_MODULES = [
    ("l4_audit", "test_l4_audit"),
    ("l4_audit_accessibility", "test_l4_audit_accessibility"),
    ("l4_audit_business_alignment", "test_l4_audit_business_alignment"),
    ("l4_audit_decision_psychology", "test_l4_audit_decision_psychology"),
    ("l4_audit_interaction_design", "test_l4_audit_interaction_design"),
    ("l4_audit_ux_architecture", "test_l4_audit_ux_architecture"),
]


@pytest.mark.parametrize(("layer_name", "test_name"), L4_MODULES)
def test_l4_maps_quote_indices_to_review_ids(layer_name: str, test_name: str) -> None:
    layer = importlib.import_module(f"auditable_design.layers.{layer_name}")
    helpers = importlib.import_module(f"tests.{test_name}")
    base = helpers._cluster()
    n = len(base.representative_quotes)
    members = base.member_review_ids
    rep_ids = [members[i % len(members)] for i in range(n)]
    cluster = InsightCluster(
        **{**base.model_dump(), "representative_review_ids": rep_ids}
    )
    payload = helpers._happy_payload()
    violations = layer._build_heuristic_violations(payload, cluster)
    assert len(violations) == len(payload["findings"])
    for finding, v in zip(payload["findings"], violations, strict=True):
        expected: list[str] = []
        for idx in finding["evidence_quote_idxs"]:
            if rep_ids[idx] not in expected:
                expected.append(rep_ids[idx])
        assert v.evidence_review_ids == expected


@pytest.mark.parametrize(("layer_name", "test_name"), L4_MODULES)
def test_l4_without_mapping_stays_empty(layer_name: str, test_name: str) -> None:
    layer = importlib.import_module(f"auditable_design.layers.{layer_name}")
    helpers = importlib.import_module(f"tests.{test_name}")
    violations = layer._build_heuristic_violations(
        helpers._happy_payload(), helpers._cluster()
    )
    assert all(v.evidence_review_ids == [] for v in violations)


# ---------------------------------------------------------------------------
# L5
# ---------------------------------------------------------------------------


def _bundle_with_ids() -> l5._ClusterBundle:
    verdicts = t5._six_verdict_bundle()
    ixd = verdicts["audit-interaction-design"]
    uxa = verdicts["audit-ux-architecture"]
    ixd.relevant_heuristics[0].evidence_review_ids = ["r1", "r2"]
    uxa.relevant_heuristics[0].evidence_review_ids = ["r2", "r3"]
    uxa.relevant_heuristics[1].evidence_review_ids = ["r3"]
    return t5._bundle(verdicts=verdicts)


class TestL5:
    def test_parser_records_source_findings(self) -> None:
        parsed = l5.parse_reconcile_response(
            t5._happy_response_text(), bundle=t5._bundle(), n_quotes=5
        )
        (entry,) = parsed["ranked_violations"]
        assert entry["source_findings"] == [
            {"skill": "audit-interaction-design", "finding_idx": 0},
            {"skill": "audit-ux-architecture", "finding_idx": 0},
        ]

    def test_corroborated_entry_unions_member_review_ids(self) -> None:
        bundle = _bundle_with_ids()
        client = t5.FakeClient(default_response=t5._happy_response_text())
        outcome = asyncio.run(
            l5.reconcile_cluster(
                t5._cluster(), bundle, client, skill_hash_value=l5.skill_hash()
            )
        )
        (hv,) = outcome.verdict.ranked_violations
        assert hv.evidence_review_ids == ["r1", "r2", "r3"]

    def test_solitary_entry_keeps_its_own_ids(self) -> None:
        bundle = _bundle_with_ids()
        node = t5._violation_node(
            node_id="v9",
            source_skill="audit-ux-architecture",
            source_heuristic="strategy_contradicts_itself",
            source_severity_anchored=7,
            source_finding_idx=1,
        )
        text = json.dumps(t5._happy_payload(nodes=[node], edges=[]))
        client = t5.FakeClient(default_response=text)
        outcome = asyncio.run(
            l5.reconcile_cluster(
                t5._cluster(), bundle, client, skill_hash_value=l5.skill_hash()
            )
        )
        (hv,) = outcome.verdict.ranked_violations
        assert hv.evidence_review_ids == ["r3"]

    def test_entry_without_source_findings_resolves_empty(self) -> None:
        assert l5.evidence_review_ids_for_entry({}, _bundle_with_ids()) == []

    def test_review_ids_do_not_reach_the_prompt(self) -> None:
        """Adding ids must not change L5 prompts — replay keys stay valid."""
        plain = l5.build_user_message(t5._cluster(), t5._bundle())
        with_ids = l5.build_user_message(t5._cluster(), _bundle_with_ids())
        assert plain == with_ids
