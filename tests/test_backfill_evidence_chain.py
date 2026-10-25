"""scripts/backfill_evidence_chain.py — deterministic backfill of review ids."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

from auditable_design.layers import l4_audit
from auditable_design.layers import l5_reconcile as l5
from auditable_design.schemas import AuditVerdict, InsightCluster, ReconciledVerdict
from tests import test_l3_cluster as t3
from tests import test_l4_audit as t4
from tests import test_l5_reconcile as t5

_SPEC = importlib.util.spec_from_file_location(
    "backfill_evidence_chain",
    Path(__file__).resolve().parent.parent / "scripts" / "backfill_evidence_chain.py",
)
assert _SPEC and _SPEC.loader
bf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bf)


def _graphs() -> dict[str, object]:
    texts = {
        "r1": ("mic says wrong", "so annoying", "I quit"),
        "r2": ("wrong answer again", "so annoying", "I quit"),
        "r3": ("mic says wrong", "hate it", "uninstalled"),
    }
    out = {}
    for rid, (a, b, c) in texts.items():
        source = f"{a}. {b}. {c}"
        out[rid] = t3._graph(
            rid,
            [("n1", "triggered_element", a), ("n2", "pain", b), ("n3", "workaround", c)],
            source,
        )
    return out


def _cluster(quotes: list[str]) -> InsightCluster:
    return InsightCluster(
        cluster_id="cluster_00",
        label="Speech check",
        member_review_ids=["r1", "r2", "r3"],
        centroid_vector_ref="l3_centroids.npy#0",
        representative_quotes=quotes,
    )


class TestClusters:
    def test_exact_unique_matches_fill_ids(self) -> None:
        new, issues = bf.backfill_cluster(
            _cluster(["wrong answer again", "hate it"]), _graphs()
        )
        assert new.representative_review_ids == ["r2", "r3"]
        assert issues == []

    def test_ambiguous_quote_takes_first_member_and_reports(self) -> None:
        new, issues = bf.backfill_cluster(_cluster(["mic says wrong"]), _graphs())
        assert new.representative_review_ids == ["r1"]
        assert issues[0]["issue"] == "ambiguous"
        assert issues[0]["candidates"] == ["r1", "r3"]

    def test_repeated_quote_text_goes_to_distinct_reviews(self) -> None:
        new, issues = bf.backfill_cluster(
            _cluster(["mic says wrong", "mic says wrong"]), _graphs()
        )
        assert new.representative_review_ids == ["r1", "r3"]
        assert [i["chosen"] for i in issues] == ["r1", "r3"]

    def test_corpus_fallback_and_short_member_ids(self) -> None:
        cluster = InsightCluster(
            cluster_id="cluster_02",
            label="Hand-built",
            member_review_ids=["r1abc", "r2abc"],
            centroid_vector_ref="l3_centroids.npy#2",
            representative_quotes=["keep my streak"],
        )
        corpus = {
            "r1abcdef": "nothing relevant here",
            "r2abcdef": "I just want to keep my streak alive",
        }
        new, issues = bf.backfill_cluster(cluster, {}, corpus)
        assert new.representative_review_ids == ["r2abc"]
        assert issues[0]["issue"] == "matched_in_corpus_text"

    def test_unmatched_quote_leaves_cluster_untouched(self) -> None:
        new, issues = bf.backfill_cluster(
            _cluster(["wrong answer again", "not in any review"]), _graphs()
        )
        assert new.representative_review_ids == []
        assert issues == [{"cluster_id": "cluster_00", "quote_idx": 1, "issue": "no_match"}]


class TestL4:
    def test_fills_ids_from_native_findings(self) -> None:
        cluster = t4._cluster()
        cluster = InsightCluster(
            **{**cluster.model_dump(), "representative_review_ids": ["r1", "r2", "r3", "r1", "r2"]}
        )
        payload = t4._happy_payload()
        verdict = AuditVerdict(
            verdict_id="audit-usability-fundamentals__cluster_00",
            cluster_id="cluster_00",
            skill_id="audit-usability-fundamentals",
            relevant_heuristics=l4_audit._build_heuristic_violations(
                payload, t4._cluster()
            ),
            native_payload_ref="x.native.jsonl#audit-usability-fundamentals__cluster_00",
            produced_at=datetime.now(UTC),
            claude_model="claude-sonnet-4-6",
            skill_hash="a" * 64,
        )
        assert verdict.relevant_heuristics[0].evidence_review_ids == []
        new, issue = bf.backfill_l4_verdict(verdict, payload, cluster)
        assert issue is None
        assert new.relevant_heuristics[0].evidence_review_ids == ["r1", "r2"]

    def test_mismatched_findings_are_reported_not_guessed(self) -> None:
        cluster = InsightCluster(
            **{**t4._cluster().model_dump(), "representative_review_ids": ["r1"] * 5}
        )
        verdict = t5._verdict(skill_id="audit-usability-fundamentals")
        new, issue = bf.backfill_l4_verdict(verdict, {"findings": []}, cluster)
        assert issue == "findings_count_mismatch"
        assert new is verdict


class TestL5:
    def _bundle(self) -> l5._ClusterBundle:
        verdicts = t5._six_verdict_bundle()
        verdicts["audit-interaction-design"].relevant_heuristics[0].evidence_review_ids = ["r1"]
        verdicts["audit-ux-architecture"].relevant_heuristics[0].evidence_review_ids = ["r3", "r1"]
        return t5._bundle(verdicts=verdicts)

    def _stored(self) -> tuple[ReconciledVerdict, dict[str, object]]:
        """Simulate a sidecar written before source_findings existed."""
        parsed = l5.parse_reconcile_response(
            t5._happy_response_text(), bundle=t5._bundle(), n_quotes=5
        )
        verdict = l5._build_reconciled_verdict(parsed, "cluster_02")
        native = json.loads(json.dumps(parsed))
        for e in native["ranked_violations"]:
            e.pop("source_findings")
        return verdict, native

    def test_rederives_and_unions_ids(self) -> None:
        verdict, native = self._stored()
        assert verdict.ranked_violations[0].evidence_review_ids == []
        new, new_native, issue = bf.backfill_l5_verdict(verdict, native, self._bundle())
        assert issue is None
        assert new.ranked_violations[0].evidence_review_ids == ["r1", "r3"]
        assert "source_findings" in new_native["ranked_violations"][0]

    def test_legacy_payload_without_graph_is_reported(self) -> None:
        verdict, native = self._stored()
        native.pop("graph")
        new, _, issue = bf.backfill_l5_verdict(verdict, native, self._bundle())
        assert issue == "no_graph_in_native_payload"
        assert new is verdict
