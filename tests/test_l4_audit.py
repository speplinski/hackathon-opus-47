"""Tests for ``auditable_design.layers.l4_audit``.

Structure mirrors the layer module:
constants → skill_hash → build_user_message → parse_audit_response →
_build_heuristic_violations → audit_cluster / audit_batch →
build_provenance → CLI.

Strategy
--------
Every test that would otherwise exercise Claude uses an in-process
:class:`FakeClient` with scripted responses — same pattern as
``test_l3b_label.py``. No network, no real replay log, no
sentence-transformers load; the whole file runs in <1s.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from auditable_design.claude_client import ClaudeResponse
from auditable_design.layers import l4_audit
from auditable_design.layers.l4_audit import (
    DEFAULT_LABELED,
    DEFAULT_NATIVE,
    DEFAULT_VERDICTS,
    DIMENSION_KEYS,
    LAYER_NAME,
    MAX_TOKENS,
    MODEL,
    NIELSEN_TO_ANCHORED,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    AuditOutcome,
    AuditParseError,
    audit_batch,
    audit_cluster,
    build_provenance,
    build_user_message,
    load_clusters,
    main,
    parse_audit_response,
    skill_hash,
    sort_outcomes,
)
from auditable_design.schemas import AuditVerdict, HeuristicViolation, InsightCluster


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class FakeClient:
    """In-memory stand-in for claude_client.Client.

    Same shape as the L3b test's FakeClient, intentionally duplicated so
    a change in one test module doesn't silently affect the other.
    Matching keys are searched in insertion order; the first substring
    hit in ``user`` wins.
    """

    scripted: dict[str, str] = field(default_factory=dict)
    default_response: str | None = None
    raise_on: dict[str, Exception] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    cumulative_usd: float = 0.0
    cache_size: int = 0
    mode: str = "fake"

    async def call(
        self,
        *,
        system: str,
        user: str,
        model: str,
        skill_id: str,
        skill_hash: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ClaudeResponse:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "skill_id": skill_id,
                "skill_hash": skill_hash,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        for key, exc in self.raise_on.items():
            if key in user:
                raise exc
        response_text = self.default_response
        for key, text in self.scripted.items():
            if key in user:
                response_text = text
                break
        if response_text is None:
            raise RuntimeError(
                f"FakeClient: no scripted response for user={user[:80]!r}..."
            )
        return ClaudeResponse(
            call_id="fake-call",
            key_hash="0" * 64,
            skill_id=skill_id,
            skill_hash=skill_hash,
            model=model,
            temperature=float(temperature),
            prompt=f"SYSTEM:\t{system}\tUSER:\t{user}",
            response=response_text,
            input_tokens=200,
            output_tokens=120,
            cost_usd=0.0,
            timestamp="2026-04-23T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _cluster(
    *,
    cluster_id: str = "cluster_00",
    label: str = "Voice recognition marks correct answers wrong",
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    centroid_ref: str = "l3_centroids.npy#0",
) -> InsightCluster:
    """Build an InsightCluster with defaults matching SKILL.md's
    worked example — the test reader can eyeball shape against docs.
    """
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref=centroid_ref,
        representative_quotes=quotes
        or [
            "I am speaking but it says wrong",
            "I keep getting it wrong even when I say it right",
            "gave me wrong answers",
            "I feel so stupid, I can't pass the speaking lessons",
            "always wrong and no way to report it",
        ],
    )


def _happy_payload(
    *,
    dim_scores: dict[str, int] | None = None,
    findings: list[dict[str, Any]] | None = None,
    summary: str = "Voice recognition rejects correct speech with no diagnostic feedback.",
) -> dict[str, Any]:
    """Build a structurally-valid payload. Defaults keep tests tight."""
    return {
        "summary": summary,
        "dimension_scores": dim_scores
        or {k: 3 for k in DIMENSION_KEYS},
        "findings": findings
        if findings is not None
        else [
            {
                "dimension": "interaction_fundamentals",
                "heuristic": "insufficient_feedback",
                "violation": "Binary verdict with no reason code.",
                "severity": 3,
                "evidence_quote_idxs": [0, 1],
                "recommendation": "Surface a reason code alongside the verdict.",
            }
        ],
    }


def _happy_response_text(payload: dict[str, Any] | None = None) -> str:
    if payload is None:
        payload = _happy_payload()
    return json.dumps(payload)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for r in rows
        )
        + ("\n" if rows else "")
    )


# =============================================================================
# Constants — sanity
# =============================================================================


class TestConstants:
    def test_skill_id(self) -> None:
        assert SKILL_ID == "audit-usability-fundamentals"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l4_audit"

    def test_default_model_is_sonnet(self) -> None:
        # Audit is reasoning-heavy but does not need Opus for thin
        # spine; if this shifts to Opus there should be an ADR.
        assert MODEL == "claude-sonnet-4-6"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        # 4096 gives ~2.5x headroom over the ~1.5k upper-bound for a
        # dense 8-finding audit. Below 2048 would risk truncation;
        # above 8192 would invite runaway reasoning.
        assert 2048 <= MAX_TOKENS <= 8192

    def test_dimension_keys_exactly_four(self) -> None:
        assert DIMENSION_KEYS == frozenset(
            {
                "interaction_fundamentals",
                "action_cognition",
                "error_architecture",
                "system_maturity",
            }
        )

    def test_nielsen_to_anchored_mapping(self) -> None:
        # ADR-008 anchors: 3=cosmetic, 6=material, 9=critical.
        # If this mapping changes, L5/L6 aggregation behaviour changes
        # — must be a PR in its own right.
        assert NIELSEN_TO_ANCHORED == {1: 3, 2: 5, 3: 7, 4: 9}

    def test_default_paths_under_data_derived(self) -> None:
        assert DEFAULT_LABELED == Path("data/derived/l3b_labeled_clusters.jsonl")
        assert DEFAULT_VERDICTS == Path("data/derived/l4_audit_verdicts.jsonl")
        assert DEFAULT_NATIVE == Path(
            "data/derived/l4_audit_verdicts.native.jsonl"
        )


# =============================================================================
# skill_hash
# =============================================================================


class TestSkillHash:
    def test_returns_64_char_hex(self) -> None:
        h = skill_hash()
        assert len(h) == 64
        int(h, 16)  # raises if not hex

    def test_is_sha256_of_system_prompt(self) -> None:
        expected = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        assert skill_hash() == expected

    def test_stable_across_calls(self) -> None:
        assert skill_hash() == skill_hash()


# =============================================================================
# build_user_message
# =============================================================================


class TestBuildUserMessage:
    def test_contains_label_and_all_quotes(self) -> None:
        c = _cluster()
        msg = build_user_message(c)
        assert "<cluster>" in msg and "</cluster>" in msg
        assert f"<label>{c.label}</label>" in msg
        for i, q in enumerate(c.representative_quotes):
            assert f'<q idx="{i}">{q}</q>' in msg

    def test_escapes_angle_brackets_and_ampersand(self) -> None:
        c = _cluster(
            label="A & B <injected>",
            quotes=["hi <script>alert()</script> & more"],
        )
        msg = build_user_message(c)
        # Literal tags in data must be escaped — no bare < > & inside.
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg
        assert "&amp;" in msg
        # The skill's own <cluster>/<label>/<q> wrappers must remain
        # unescaped — they are the injection boundary, not data.
        assert "<cluster>" in msg
        assert "<label>" in msg
        assert '<q idx="0">' in msg

    def test_idx_attribute_is_zero_indexed(self) -> None:
        c = _cluster(quotes=["a", "b", "c"])
        msg = build_user_message(c)
        assert '<q idx="0">a</q>' in msg
        assert '<q idx="1">b</q>' in msg
        assert '<q idx="2">c</q>' in msg


# =============================================================================
# parse_audit_response
# =============================================================================


class TestParseAuditResponse:
    def test_happy_path(self) -> None:
        payload = parse_audit_response(
            _happy_response_text(), n_quotes=5
        )
        assert payload["summary"].startswith("Voice")
        assert set(payload["dimension_scores"]) == DIMENSION_KEYS
        assert len(payload["findings"]) == 1

    def test_tolerates_leading_prose(self) -> None:
        text = (
            "Here is my audit, thinking carefully:\n\n"
            + _happy_response_text()
        )
        parse_audit_response(text, n_quotes=5)

    def test_tolerates_code_fences(self) -> None:
        text = "```json\n" + _happy_response_text() + "\n```"
        parse_audit_response(text, n_quotes=5)

    def test_no_json(self) -> None:
        with pytest.raises(AuditParseError, match="no JSON object"):
            parse_audit_response("sorry nothing here", n_quotes=3)

    def test_malformed_json(self) -> None:
        # Unbalanced brace → greedy {.*} regex does not match → "no JSON object found".
        with pytest.raises(AuditParseError, match="no JSON object"):
            parse_audit_response('{"summary": "x"', n_quotes=3)

    def test_missing_top_level_key(self) -> None:
        with pytest.raises(AuditParseError, match="missing required top-level keys"):
            parse_audit_response('{"summary": "x", "dimension_scores": {}}', n_quotes=3)

    def test_extra_top_level_key(self) -> None:
        payload = _happy_payload()
        text = json.dumps({**payload, "extra": 1})
        with pytest.raises(AuditParseError, match="unexpected top-level keys"):
            parse_audit_response(text, n_quotes=5)

    def test_empty_summary(self) -> None:
        payload = _happy_payload(summary="   ")
        with pytest.raises(AuditParseError, match="summary.*non-empty"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_dimension_scores_missing_key(self) -> None:
        payload = _happy_payload(
            dim_scores={
                "interaction_fundamentals": 3,
                "action_cognition": 3,
                "error_architecture": 3,
                # system_maturity missing
            }
        )
        with pytest.raises(AuditParseError, match="dimension_scores missing keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_dimension_scores_extra_key(self) -> None:
        payload = _happy_payload(
            dim_scores={**{k: 3 for k in DIMENSION_KEYS}, "extra": 3}
        )
        with pytest.raises(AuditParseError, match="unexpected keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_dimension_score_out_of_range(self) -> None:
        payload = _happy_payload(
            dim_scores={
                "interaction_fundamentals": 7,
                "action_cognition": 3,
                "error_architecture": 3,
                "system_maturity": 3,
            }
        )
        with pytest.raises(AuditParseError, match=r"out of \{1,2,3,4,5\}"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_bool_not_accepted_as_int(self) -> None:
        # ``True`` is a subclass of int; reject explicitly.
        payload = _happy_payload(
            dim_scores={
                "interaction_fundamentals": True,  # type: ignore[dict-item]
                "action_cognition": 3,
                "error_architecture": 3,
                "system_maturity": 3,
            }
        )
        with pytest.raises(AuditParseError, match="must be int, got bool"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_finding_missing_key(self) -> None:
        bad_finding = {
            "dimension": "interaction_fundamentals",
            "heuristic": "h",
            "violation": "v",
            # severity missing
            "evidence_quote_idxs": [0],
            "recommendation": "r",
        }
        payload = _happy_payload(findings=[bad_finding])
        with pytest.raises(AuditParseError, match="findings\\[0\\] missing keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_finding_invalid_dimension(self) -> None:
        payload = _happy_payload(
            findings=[
                {
                    "dimension": "bogus_dimension",
                    "heuristic": "h",
                    "violation": "v",
                    "severity": 3,
                    "evidence_quote_idxs": [0],
                    "recommendation": "r",
                }
            ]
        )
        with pytest.raises(AuditParseError, match="dimension='bogus_dimension'"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_finding_severity_out_of_range(self) -> None:
        payload = _happy_payload(
            findings=[
                {
                    "dimension": "interaction_fundamentals",
                    "heuristic": "h",
                    "violation": "v",
                    "severity": 5,  # Nielsen max is 4
                    "evidence_quote_idxs": [0],
                    "recommendation": "r",
                }
            ]
        )
        with pytest.raises(AuditParseError, match="severity=5 out of"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_finding_empty_evidence_idxs(self) -> None:
        payload = _happy_payload(
            findings=[
                {
                    "dimension": "interaction_fundamentals",
                    "heuristic": "h",
                    "violation": "v",
                    "severity": 3,
                    "evidence_quote_idxs": [],
                    "recommendation": "r",
                }
            ]
        )
        with pytest.raises(AuditParseError, match="must be anchored"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_finding_idx_out_of_range(self) -> None:
        payload = _happy_payload(
            findings=[
                {
                    "dimension": "interaction_fundamentals",
                    "heuristic": "h",
                    "violation": "v",
                    "severity": 3,
                    "evidence_quote_idxs": [99],
                    "recommendation": "r",
                }
            ]
        )
        with pytest.raises(AuditParseError, match=r"out of \[0, 5\)"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_empty_findings_list_is_valid(self) -> None:
        # A healthy cluster might have no findings at all (dim scores 5
        # across the board). SKILL.md documents 0–8 as the valid range.
        payload = _happy_payload(
            dim_scores={k: 5 for k in DIMENSION_KEYS},
            findings=[],
        )
        parse_audit_response(json.dumps(payload), n_quotes=5)


# =============================================================================
# _build_heuristic_violations — severity mapping + reasoning
# =============================================================================


class TestBuildHeuristicViolations:
    def test_severity_mapping_all_four_levels(self) -> None:
        findings = [
            {
                "dimension": "interaction_fundamentals",
                "heuristic": f"h{n}",
                "violation": f"v{n}",
                "severity": n,
                "evidence_quote_idxs": [0],
                "recommendation": f"r{n}",
            }
            for n in (1, 2, 3, 4)
        ]
        payload = _happy_payload(findings=findings)
        c = _cluster()
        violations = l4_audit._build_heuristic_violations(payload, c)
        anchored = [v.severity for v in violations]
        assert anchored == [3, 5, 7, 9]

    def test_reasoning_includes_quote_evidence(self) -> None:
        c = _cluster(quotes=["quote A", "quote B"])
        payload = _happy_payload(
            findings=[
                {
                    "dimension": "interaction_fundamentals",
                    "heuristic": "insufficient_feedback",
                    "violation": "No reason code.",
                    "severity": 3,
                    "evidence_quote_idxs": [0, 1],
                    "recommendation": "Show reason code.",
                }
            ]
        )
        violations = l4_audit._build_heuristic_violations(payload, c)
        assert len(violations) == 1
        assert "q[0]='quote A'" in violations[0].reasoning
        assert "q[1]='quote B'" in violations[0].reasoning
        assert "Nielsen 3 → anchored 7" in violations[0].reasoning

    def test_reasoning_tags_dimension(self) -> None:
        c = _cluster()
        payload = _happy_payload(
            findings=[
                {
                    "dimension": "error_architecture",
                    "heuristic": "missing_undo",
                    "violation": "Cannot revert.",
                    "severity": 4,
                    "evidence_quote_idxs": [0],
                    "recommendation": "Add undo.",
                }
            ]
        )
        violations = l4_audit._build_heuristic_violations(payload, c)
        assert violations[0].reasoning.startswith("[error_architecture]")


# =============================================================================
# audit_cluster
# =============================================================================


class TestAuditCluster:
    def test_happy_path_yields_audited_outcome(self) -> None:
        client = FakeClient(default_response=_happy_response_text())
        c = _cluster()
        outcome = asyncio.run(
            audit_cluster(c, client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "audited"
        assert outcome.reason is None
        assert outcome.verdict.cluster_id == c.cluster_id
        assert outcome.verdict.skill_id == SKILL_ID
        assert len(outcome.verdict.relevant_heuristics) == 1

    def test_parse_failure_yields_fallback_outcome(self) -> None:
        client = FakeClient(default_response="not json at all")
        c = _cluster()
        outcome = asyncio.run(
            audit_cluster(c, client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "fallback"
        assert outcome.reason is not None
        # Fallback verdict has zero violations — the contract is total
        # in cluster_id space, but the brain explicitly abstained.
        assert outcome.verdict.relevant_heuristics == []
        assert outcome.verdict.cluster_id == c.cluster_id

    def test_fallback_native_payload_carries_raw_response(self) -> None:
        client = FakeClient(default_response="unparseable")
        c = _cluster()
        outcome = asyncio.run(
            audit_cluster(c, client, skill_hash_value=skill_hash())
        )
        assert outcome.native_payload == {
            "fallback": True,
            "reason": outcome.reason,
            "raw_response": "unparseable",
        }

    def test_transport_failure_propagates(self) -> None:
        """A transport exception (e.g. replay miss) must NOT become a
        fallback — the cache is out of sync and the caller needs to
        know loudly.
        """
        client = FakeClient(
            raise_on={"speaking": RuntimeError("replay miss")},
        )
        c = _cluster()
        with pytest.raises(RuntimeError, match="replay miss"):
            asyncio.run(audit_cluster(c, client, skill_hash_value=skill_hash()))

    def test_call_uses_layer_constants(self) -> None:
        client = FakeClient(default_response=_happy_response_text())
        c = _cluster()
        asyncio.run(audit_cluster(c, client, skill_hash_value=skill_hash()))
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["skill_id"] == SKILL_ID
        assert call["model"] == MODEL
        assert call["temperature"] == TEMPERATURE
        assert call["max_tokens"] == MAX_TOKENS
        assert call["system"] == SYSTEM_PROMPT


# =============================================================================
# audit_batch
# =============================================================================


class TestAuditBatch:
    def test_processes_all_clusters(self) -> None:
        client = FakeClient(default_response=_happy_response_text())
        clusters = [
            _cluster(cluster_id=f"cluster_{i:02d}") for i in range(3)
        ]
        outcomes, failures = asyncio.run(audit_batch(clusters, client))
        assert len(outcomes) == 3
        assert failures == []
        assert {o.cluster_id for o in outcomes} == {
            "cluster_00",
            "cluster_01",
            "cluster_02",
        }

    def test_isolates_transport_failures(self) -> None:
        """One cluster's transport error must not stop the batch."""
        client = FakeClient(
            default_response=_happy_response_text(),
            raise_on={"cluster_01_marker": RuntimeError("boom")},
        )
        clusters = [
            _cluster(cluster_id="cluster_00"),
            _cluster(
                cluster_id="cluster_01",
                quotes=["cluster_01_marker in quote"],
            ),
            _cluster(cluster_id="cluster_02"),
        ]
        outcomes, failures = asyncio.run(audit_batch(clusters, client))
        assert len(outcomes) == 2
        assert len(failures) == 1
        assert failures[0][0] == "cluster_01"
        assert isinstance(failures[0][1], RuntimeError)

    def test_parse_failure_is_a_fallback_not_a_failure(self) -> None:
        client = FakeClient(default_response="garbage")
        clusters = [_cluster()]
        outcomes, failures = asyncio.run(audit_batch(clusters, client))
        assert failures == []
        assert len(outcomes) == 1
        assert outcomes[0].status == "fallback"


# =============================================================================
# sort_outcomes
# =============================================================================


class TestSortOutcomes:
    def test_sorted_by_cluster_id(self) -> None:
        def _mk(cid: str) -> AuditOutcome:
            v = AuditVerdict(
                verdict_id=f"{SKILL_ID}__{cid}",
                cluster_id=cid,
                skill_id=SKILL_ID,
                relevant_heuristics=[],
                native_payload_ref=None,
                produced_at="2026-04-23T12:00:00+00:00",  # type: ignore[arg-type]
                claude_model=MODEL,
                skill_hash="0" * 64,
            )
            return AuditOutcome(
                cluster_id=cid,
                verdict=v,
                native_payload={},
                status="audited",
            )

        outcomes = [_mk("cluster_02"), _mk("cluster_00"), _mk("cluster_01")]
        sorted_ = sort_outcomes(outcomes)
        assert [o.cluster_id for o in sorted_] == [
            "cluster_00",
            "cluster_01",
            "cluster_02",
        ]


# =============================================================================
# build_provenance
# =============================================================================


class TestBuildProvenance:
    def _outcome(
        self,
        *,
        cluster_id: str,
        status: str,
        payload: dict[str, Any],
        reason: str | None = None,
    ) -> AuditOutcome:
        v = AuditVerdict(
            verdict_id=f"{SKILL_ID}__{cluster_id}",
            cluster_id=cluster_id,
            skill_id=SKILL_ID,
            relevant_heuristics=[],
            native_payload_ref=None,
            produced_at="2026-04-23T12:00:00+00:00",  # type: ignore[arg-type]
            claude_model=MODEL,
            skill_hash="0" * 64,
        )
        return AuditOutcome(
            cluster_id=cluster_id,
            verdict=v,
            native_payload=payload,
            status=status,  # type: ignore[arg-type]
            reason=reason,
        )

    def test_counts_match(self) -> None:
        outcomes = [
            self._outcome(
                cluster_id="c00",
                status="audited",
                payload=_happy_payload(),
            ),
            self._outcome(
                cluster_id="c01",
                status="fallback",
                payload={"fallback": True, "reason": "bad", "raw_response": "x"},
                reason="bad",
            ),
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert prov["cluster_count"] == 2
        assert prov["audited_count"] == 1
        assert prov["fallback_count"] == 1
        assert prov["transport_failure_count"] == 0

    def test_dimension_score_totals_sum_correctly(self) -> None:
        outcomes = [
            self._outcome(
                cluster_id="c00",
                status="audited",
                payload=_happy_payload(
                    dim_scores={
                        "interaction_fundamentals": 4,
                        "action_cognition": 3,
                        "error_architecture": 5,
                        "system_maturity": 2,
                    },
                    findings=[],
                ),
            ),
            self._outcome(
                cluster_id="c01",
                status="audited",
                payload=_happy_payload(
                    dim_scores={
                        "interaction_fundamentals": 1,
                        "action_cognition": 2,
                        "error_architecture": 3,
                        "system_maturity": 4,
                    },
                    findings=[],
                ),
            ),
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert prov["dimension_score_totals"] == {
            "interaction_fundamentals": 5,
            "action_cognition": 5,
            "error_architecture": 8,
            "system_maturity": 6,
        }

    def test_severity_histogram(self) -> None:
        outcomes = [
            self._outcome(
                cluster_id="c00",
                status="audited",
                payload=_happy_payload(
                    findings=[
                        {
                            "dimension": "interaction_fundamentals",
                            "heuristic": "h1",
                            "violation": "v",
                            "severity": 1,
                            "evidence_quote_idxs": [0],
                            "recommendation": "r",
                        },
                        {
                            "dimension": "action_cognition",
                            "heuristic": "h2",
                            "violation": "v",
                            "severity": 3,
                            "evidence_quote_idxs": [0],
                            "recommendation": "r",
                        },
                        {
                            "dimension": "error_architecture",
                            "heuristic": "h3",
                            "violation": "v",
                            "severity": 3,
                            "evidence_quote_idxs": [0],
                            "recommendation": "r",
                        },
                    ]
                ),
            ),
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert prov["nielsen_severity_histogram"] == {1: 1, 2: 0, 3: 2, 4: 0}
        assert prov["findings_count"] == 3

    def test_transport_failures_rendered(self) -> None:
        prov = build_provenance(
            outcomes=[],
            failures=[("c99", ValueError("oops"))],
            model=MODEL,
        )
        assert prov["transport_failure_count"] == 1
        assert prov["transport_failures"][0]["cluster_id"] == "c99"
        assert "ValueError" in prov["transport_failures"][0]["error"]


# =============================================================================
# CLI — main
# =============================================================================


class TestMain:
    def test_cli_end_to_end_with_fakeclient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full pipeline smoke test. Patches Client to FakeClient so no
        network is touched; proves the main() wire-up from argparse →
        load → batch → verdict JSONL + native JSONL + provenance JSON.
        """
        # storage.write_jsonl_atomic refuses writes outside
        # repo_root/data (and demo/public/data). We retarget repo_root
        # onto tmp_path and put every file under tmp_path/data/.
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l4_audit, "_resolve_repo_root", lambda: tmp_path)

        # Build an L3b-shaped input file.
        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [
                _cluster(cluster_id="cluster_00").model_dump(mode="json"),
                _cluster(
                    cluster_id="cluster_01",
                    label="App freezes repeatedly",
                    quotes=["freezing", "app freezes"],
                ).model_dump(mode="json"),
            ],
        )
        output_path = data_dir / "verdicts.jsonl"
        native_path = data_dir / "verdicts.native.jsonl"

        fake = FakeClient(default_response=_happy_response_text())

        def _fake_client_ctor(**_kwargs: Any) -> FakeClient:
            return fake

        monkeypatch.setattr(l4_audit, "Client", _fake_client_ctor)

        rc = main(
            [
                "--clusters",
                str(clusters_path),
                "--output",
                str(output_path),
                "--native-output",
                str(native_path),
                "--mode",
                "replay",
                "--run-id",
                "l4-test-run",
            ]
        )

        assert rc == 0
        assert output_path.exists()
        assert native_path.exists()

        # Verdicts JSONL: one row per cluster, AuditVerdict shape.
        verdicts_raw = [
            json.loads(line) for line in output_path.read_text().splitlines()
        ]
        assert len(verdicts_raw) == 2
        for row in verdicts_raw:
            AuditVerdict.model_validate(row)  # shape check

        # Native JSONL: one row per verdict, keyed by verdict_id.
        native_raw = [
            json.loads(line) for line in native_path.read_text().splitlines()
        ]
        assert len(native_raw) == 2
        verdict_ids = {r["verdict_id"] for r in native_raw}
        assert verdict_ids == {
            f"{SKILL_ID}__cluster_00",
            f"{SKILL_ID}__cluster_01",
        }

        # Provenance sidecar next to the verdicts file.
        prov_path = output_path.with_suffix(".provenance.json")
        assert prov_path.exists()
        prov = json.loads(prov_path.read_text())
        assert prov["cluster_count"] == 2
        assert prov["audited_count"] == 2
        assert prov["fallback_count"] == 0

    def test_cli_empty_input_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l4_audit, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        output_path = data_dir / "out.jsonl"
        native_path = data_dir / "native.jsonl"

        monkeypatch.setattr(
            l4_audit, "Client", lambda **_k: FakeClient()
        )
        rc = main(
            [
                "--clusters",
                str(clusters_path),
                "--output",
                str(output_path),
                "--native-output",
                str(native_path),
                "--mode",
                "replay",
            ]
        )
        assert rc == 1

    def test_cli_fallback_does_not_fail_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A parse-failure fallback is traceable signal, not an error —
        main() must still return 0.
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l4_audit, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [_cluster(cluster_id="cluster_00").model_dump(mode="json")],
        )
        output_path = data_dir / "out.jsonl"
        native_path = data_dir / "native.jsonl"

        fake = FakeClient(default_response="garbage not json")
        monkeypatch.setattr(l4_audit, "Client", lambda **_k: fake)
        rc = main(
            [
                "--clusters",
                str(clusters_path),
                "--output",
                str(output_path),
                "--native-output",
                str(native_path),
                "--mode",
                "replay",
            ]
        )
        assert rc == 0
        prov = json.loads(
            output_path.with_suffix(".provenance.json").read_text()
        )
        assert prov["fallback_count"] == 1
        assert prov["audited_count"] == 0
