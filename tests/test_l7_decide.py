"""Tests for ``auditable_design.layers.l7_decide``.

Structure mirrors ``test_l6_weight.py`` where shapes are shared
(constants, FakeClient, batch plumbing) and diverges for L7-specific
concerns:

* **Single-pass** — unlike L6's double-pass judgment, L7 is a
  generation task; one call per cluster, parse failure → fallback
  (no retry).
* **Cross-reference validation** — parser enforces that every
  `derived_from_review_ids` entry exists in cluster.member_review_ids
  and every `resolves_heuristics` entry exists in the reconciled
  verdict's ranked_violations heuristic list. Hallucinated citations
  and invented slugs both rejected.
* **Two output artefacts per success** — DesignPrinciple + DesignDecision
  with FK `decision.principle_id → principle.principle_id`. IDs are
  generated deterministically from cluster_id by the module.

Strategy
--------
In-process :class:`FakeClient` with scripted responses; no network.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auditable_design.claude_client import ClaudeResponse
from auditable_design.layers import l7_decide as l7
from auditable_design.layers.l7_decide import (
    DEFAULT_CLUSTERS,
    DEFAULT_DECISIONS,
    DEFAULT_NATIVE,
    DEFAULT_PRINCIPLES,
    DEFAULT_PRIORITY,
    DEFAULT_RECONCILED,
    LAYER_NAME,
    MAX_TOKENS,
    MODEL,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    DecideOutcome,
    DecideParseError,
    build_provenance,
    build_user_message,
    decide_batch,
    decide_cluster,
    load_priority_scores,
    main,
    parse_decide_response,
    skill_hash,
)
from auditable_design.schemas import (
    DesignDecision,
    DesignPrinciple,
    HeuristicViolation,
    InsightCluster,
    PriorityScore,
    ReconciledVerdict,
    SkillTension,
)


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class FakeClient:
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
            input_tokens=400,
            output_tokens=200,
            cost_usd=0.0,
            timestamp="2026-04-23T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _cluster(
    *,
    cluster_id: str = "cluster_02",
    label: str = "Streak loss framing pressures users into mid-session purchase",
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    ui_context: str | None = "Duolingo mid-lesson modal blocks next question.",
    html: str | None = None,
    screenshot_ref: str | None = None,
) -> InsightCluster:
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=members or ["r1", "r2", "r3", "r4", "r5", "r6", "r7"],
        centroid_vector_ref="l3_centroids.npy#0",
        representative_quotes=quotes
        or [
            "streak saver popup is outright manipulative",
            "I'm trying to keep my 800+ day streak",
            "forced to pay or watch ads mid-lesson",
        ],
        ui_context=ui_context,
        html=html,
        screenshot_ref=screenshot_ref,
    )


def _reconciled(
    *,
    cluster_id: str = "cluster_02",
    heuristics: list[str] | None = None,
) -> ReconciledVerdict:
    """Build a minimal ReconciledVerdict with specified heuristic slugs
    in its ranked_violations. Default has three slugs the default
    decision payload can resolve."""
    slugs = heuristics or [
        "posture_drift__skeleton_override",
        "modal_excise",
        "competing_calls_to_action",
    ]
    ranked = [
        HeuristicViolation(
            heuristic=h,
            violation=f"Violation text for {h}",
            severity=9 if i == 0 else 7,
            evidence_review_ids=[],
            reasoning=f"rank_score={9 if i == 0 else 7} | skills=[...] | {h}",
        )
        for i, h in enumerate(slugs)
    ]
    return ReconciledVerdict(
        cluster_id=cluster_id,
        ranked_violations=ranked,
        tensions=[
            SkillTension(
                skill_a="audit-interaction-design",
                skill_b="audit-decision-psychology",
                axis="efficiency_vs_safety",
                resolution="Cooper governs reversible; Kahneman governs irreversible.",
            )
        ],
    )


def _priority(
    *,
    cluster_id: str = "cluster_02",
    severity: int = 10,
    reach: int = 9,
    persistence: int = 8,
    business_impact: int = 9,
    cognitive_cost: int = 10,
) -> PriorityScore:
    dims = {
        "severity": severity,
        "reach": reach,
        "persistence": persistence,
        "business_impact": business_impact,
        "cognitive_cost": cognitive_cost,
    }
    weights = {k: 0.2 for k in dims}
    total = sum(dims[k] * weights[k] for k in dims)
    return PriorityScore(
        cluster_id=cluster_id,
        dimensions=dims,
        meta_weights=weights,
        weighted_total=total,
        validation_passes=2,
        validation_delta=0.0,
    )


def _decide_payload(
    *,
    principle_name: str = "Monetisation lives at boundaries, not mid-flow",
    principle_statement: str = (
        "A user's core-loop progress is never blocked by a monetisation "
        "surface; retention offers appear at natural pauses."
    ),
    derived_review_ids: list[str] | None = None,
    decision_description: str = (
        "Move the streak-risk modal from mid-lesson to lesson-complete boundary."
    ),
    before_snapshot: str = (
        "Modal fires mid-lesson on energy depletion. Full-viewport blocker."
    ),
    after_snapshot: str = (
        "Modal moves to lesson-complete screen as non-blocking inline banner."
    ),
    resolves_heuristics: list[str] | None = None,
) -> dict[str, Any]:
    # NB: use explicit `is not None` — `[] or default` would wrongly
    # substitute the default on empty-list inputs, which are exactly
    # what the parser-validation tests want to exercise.
    return {
        "principle": {
            "name": principle_name,
            "statement": principle_statement,
            "derived_from_review_ids": (
                derived_review_ids if derived_review_ids is not None
                else ["r1", "r3", "r5"]
            ),
        },
        "decision": {
            "description": decision_description,
            "before_snapshot": before_snapshot,
            "after_snapshot": after_snapshot,
            "resolves_heuristics": (
                resolves_heuristics if resolves_heuristics is not None
                else ["posture_drift__skeleton_override", "modal_excise"]
            ),
        },
    }


def _decide_text(payload: dict[str, Any] | None = None) -> str:
    return json.dumps(payload or _decide_payload())


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
# Constants
# =============================================================================


class TestConstants:
    def test_skill_id(self) -> None:
        assert SKILL_ID == "design-decide"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l7_decide"

    def test_default_model_is_opus_47(self) -> None:
        assert MODEL == "claude-opus-4-7"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        assert 2048 <= MAX_TOKENS <= 8192

    def test_default_paths(self) -> None:
        assert DEFAULT_RECONCILED == Path(
            "data/derived/l5_reconciled_verdicts.jsonl"
        )
        assert DEFAULT_PRIORITY == Path(
            "data/derived/l6_priority_scores.jsonl"
        )
        assert DEFAULT_CLUSTERS == Path(
            "data/derived/l3b_labeled_clusters.jsonl"
        )
        assert DEFAULT_PRINCIPLES == Path(
            "data/derived/l7_design_principles.jsonl"
        )
        assert DEFAULT_DECISIONS == Path(
            "data/derived/l7_design_decisions.jsonl"
        )
        assert DEFAULT_NATIVE == Path(
            "data/derived/l7_design_decisions.native.jsonl"
        )

    def test_skill_hash_independent_of_other_skills(self) -> None:
        from auditable_design.layers import (
            l4_audit,
            l5_reconcile,
            l6_weight,
        )

        assert skill_hash() != l4_audit.skill_hash()
        assert skill_hash() != l5_reconcile.skill_hash()
        assert skill_hash() != l6_weight.skill_hash()


# =============================================================================
# skill_hash
# =============================================================================


class TestSkillHash:
    def test_returns_64_char_hex(self) -> None:
        h = skill_hash()
        assert len(h) == 64
        int(h, 16)

    def test_is_sha256_of_system_prompt(self) -> None:
        expected = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        assert skill_hash() == expected

    def test_stable(self) -> None:
        assert skill_hash() == skill_hash()


# =============================================================================
# load_priority_scores
# =============================================================================


class TestLoadPriorityScores:
    def test_loads_single(self, tmp_path: Path) -> None:
        path = tmp_path / "p.jsonl"
        _write_jsonl(path, [_priority(cluster_id="c1").model_dump(mode="json")])
        loaded = load_priority_scores(path)
        assert set(loaded) == {"c1"}
        assert loaded["c1"].dimensions["severity"] == 10

    def test_loads_multiple(self, tmp_path: Path) -> None:
        path = tmp_path / "p.jsonl"
        _write_jsonl(
            path,
            [
                _priority(cluster_id="c1").model_dump(mode="json"),
                _priority(cluster_id="c2").model_dump(mode="json"),
            ],
        )
        loaded = load_priority_scores(path)
        assert set(loaded) == {"c1", "c2"}

    def test_duplicate_last_wins(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "dup.jsonl"
        first = _priority(cluster_id="c1", severity=5).model_dump(mode="json")
        second = _priority(cluster_id="c1", severity=9).model_dump(mode="json")
        _write_jsonl(path, [first, second])
        with caplog.at_level("WARNING"):
            loaded = load_priority_scores(path)
        assert loaded["c1"].dimensions["severity"] == 9
        assert any("duplicate" in r.message for r in caplog.records)

    def test_malformed_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('{"dimensions": {}}\n')
        with pytest.raises(RuntimeError, match="not a valid PriorityScore"):
            load_priority_scores(path)


# =============================================================================
# build_user_message
# =============================================================================


class TestBuildUserMessage:
    def test_contains_all_three_blocks(self) -> None:
        c = _cluster()
        r = _reconciled()
        p = _priority()
        msg = build_user_message(c, r, p)
        assert "<cluster>" in msg and "</cluster>" in msg
        assert "<reconciled_verdict>" in msg
        assert "<priority_score>" in msg

    def test_member_review_ids_space_separated(self) -> None:
        """Model needs to scan member_review_ids; space-separated is
        easier to read than one-per-line."""
        c = _cluster(members=["r1", "r2", "r3"])
        msg = build_user_message(c, _reconciled(), _priority())
        assert "<member_review_ids>r1 r2 r3</member_review_ids>" in msg

    def test_ranked_violations_include_heuristic_slugs(self) -> None:
        """The model must see heuristic slugs so it can cite them in
        resolves_heuristics."""
        c = _cluster()
        r = _reconciled(heuristics=["foo_heuristic", "bar_heuristic"])
        msg = build_user_message(c, r, _priority())
        assert 'heuristic="foo_heuristic"' in msg
        assert 'heuristic="bar_heuristic"' in msg

    def test_priority_dimensions_rendered(self) -> None:
        c = _cluster()
        msg = build_user_message(c, _reconciled(), _priority(severity=8, reach=7))
        assert "severity=8" in msg
        assert "reach=7" in msg
        assert "<weighted_total>" in msg

    def test_optional_context_absent_when_none(self) -> None:
        c = _cluster(ui_context=None, html=None, screenshot_ref=None)
        msg = build_user_message(c, _reconciled(), _priority())
        assert "<ui_context>" not in msg
        assert "<html>" not in msg
        assert "<screenshot_ref>" not in msg

    def test_html_cdata_wrapped(self) -> None:
        raw = '<button onclick="x()">Submit</button>'
        c = _cluster(html=raw)
        msg = build_user_message(c, _reconciled(), _priority())
        assert "<html><![CDATA[\n" in msg
        assert raw in msg

    def test_tensions_block_present(self) -> None:
        c = _cluster()
        r = _reconciled()
        msg = build_user_message(c, r, _priority())
        assert 'axis="efficiency_vs_safety"' in msg
        assert 'skill_a="audit-interaction-design"' in msg


# =============================================================================
# parse_decide_response — happy
# =============================================================================


class TestParseHappy:
    def test_minimal_happy(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = parse_decide_response(_decide_text(), cluster=c, reconciled=r)
        assert "principle" in payload
        assert "decision" in payload

    def test_tolerates_leading_prose(self) -> None:
        c = _cluster()
        r = _reconciled()
        text = "Thinking...\n\n" + _decide_text()
        parse_decide_response(text, cluster=c, reconciled=r)

    def test_tolerates_code_fences(self) -> None:
        c = _cluster()
        r = _reconciled()
        text = "```json\n" + _decide_text() + "\n```"
        parse_decide_response(text, cluster=c, reconciled=r)


# =============================================================================
# parse_decide_response — failures (structural)
# =============================================================================


class TestParseStructural:
    def test_no_json(self) -> None:
        c = _cluster()
        r = _reconciled()
        with pytest.raises(DecideParseError, match="no JSON object"):
            parse_decide_response("no json here", cluster=c, reconciled=r)

    def test_malformed_json(self) -> None:
        c = _cluster()
        r = _reconciled()
        with pytest.raises(DecideParseError, match="malformed JSON|no JSON"):
            parse_decide_response('{"principle":', cluster=c, reconciled=r)

    def test_missing_principle(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload()
        del payload["principle"]
        with pytest.raises(DecideParseError, match="missing required top-level"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_missing_decision(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload()
        del payload["decision"]
        with pytest.raises(DecideParseError, match="missing required top-level"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_extra_top_level_key(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = {**_decide_payload(), "bonus": 1}
        with pytest.raises(DecideParseError, match="unexpected top-level"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )


# =============================================================================
# parse_decide_response — principle validation
# =============================================================================


class TestParsePrinciple:
    def test_principle_missing_name(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload()
        del payload["principle"]["name"]
        with pytest.raises(DecideParseError, match="principle missing keys"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_principle_empty_name(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload(principle_name="   ")
        with pytest.raises(DecideParseError, match="principle.name.*non-empty"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_principle_empty_statement(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload(principle_statement="   ")
        with pytest.raises(DecideParseError, match="principle.statement.*non-empty"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_principle_extra_key(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload()
        payload["principle"]["extra"] = "nope"
        with pytest.raises(DecideParseError, match="principle has unexpected"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_derived_review_ids_empty_list(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload(derived_review_ids=[])
        with pytest.raises(
            DecideParseError, match="derived_from_review_ids.*non-empty list"
        ):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_derived_review_id_not_in_cluster(self) -> None:
        """Hallucinated review_id cited → fallback."""
        c = _cluster(members=["r1", "r2", "r3"])  # no r99
        r = _reconciled()
        payload = _decide_payload(derived_review_ids=["r1", "r99"])
        with pytest.raises(
            DecideParseError, match="hallucinated citation"
        ):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_derived_review_ids_all_valid(self) -> None:
        """Valid review_ids all from cluster.member_review_ids → parses."""
        c = _cluster(members=["r1", "r2", "r3", "r4", "r5"])
        r = _reconciled()
        payload = _decide_payload(derived_review_ids=["r2", "r4"])
        parsed = parse_decide_response(
            json.dumps(payload), cluster=c, reconciled=r
        )
        assert parsed["principle"]["derived_from_review_ids"] == ["r2", "r4"]


# =============================================================================
# parse_decide_response — decision validation
# =============================================================================


class TestParseDecision:
    def test_decision_missing_description(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload()
        del payload["decision"]["description"]
        with pytest.raises(DecideParseError, match="decision missing keys"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_decision_missing_before_snapshot(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload()
        del payload["decision"]["before_snapshot"]
        with pytest.raises(DecideParseError, match="decision missing keys"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_decision_missing_after_snapshot(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload()
        del payload["decision"]["after_snapshot"]
        with pytest.raises(DecideParseError, match="decision missing keys"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_decision_extra_key(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload()
        payload["decision"]["bonus"] = "nope"
        with pytest.raises(DecideParseError, match="decision has unexpected"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_decision_empty_description(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload(decision_description="   ")
        with pytest.raises(DecideParseError, match="description.*non-empty"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_resolves_heuristics_empty_list(self) -> None:
        c = _cluster()
        r = _reconciled()
        payload = _decide_payload(resolves_heuristics=[])
        with pytest.raises(
            DecideParseError, match="resolves_heuristics.*non-empty list"
        ):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_resolves_heuristics_invented_slug(self) -> None:
        """Invented heuristic slug → parse fallback."""
        c = _cluster()
        r = _reconciled(heuristics=["real_slug_a", "real_slug_b"])
        payload = _decide_payload(
            resolves_heuristics=["real_slug_a", "invented_slug"]
        )
        with pytest.raises(DecideParseError, match="invented slug"):
            parse_decide_response(
                json.dumps(payload), cluster=c, reconciled=r
            )

    def test_resolves_heuristics_all_valid(self) -> None:
        c = _cluster()
        r = _reconciled(heuristics=["slug_a", "slug_b", "slug_c"])
        payload = _decide_payload(resolves_heuristics=["slug_a", "slug_c"])
        parsed = parse_decide_response(
            json.dumps(payload), cluster=c, reconciled=r
        )
        assert parsed["decision"]["resolves_heuristics"] == ["slug_a", "slug_c"]


# =============================================================================
# decide_cluster end-to-end
# =============================================================================


class TestDecideCluster:
    def test_happy_path(self) -> None:
        client = FakeClient(default_response=_decide_text())
        outcome = asyncio.run(
            decide_cluster(
                _cluster(), _reconciled(), _priority(), client,
                skill_hash_value=skill_hash(),
            )
        )
        assert outcome.status == "decided"
        assert outcome.principle is not None
        assert outcome.decision is not None
        assert outcome.principle.cluster_id == "cluster_02"
        assert outcome.decision.principle_id == outcome.principle.principle_id

    def test_generated_ids_are_stable(self) -> None:
        client = FakeClient(default_response=_decide_text())
        outcome = asyncio.run(
            decide_cluster(
                _cluster(), _reconciled(), _priority(), client,
                skill_hash_value=skill_hash(),
            )
        )
        assert outcome.principle.principle_id == "principle__cluster_02"
        assert outcome.decision.decision_id == "decision__cluster_02__1"

    def test_fk_consistency(self) -> None:
        """decision.principle_id matches principle.principle_id."""
        client = FakeClient(default_response=_decide_text())
        outcome = asyncio.run(
            decide_cluster(
                _cluster(), _reconciled(), _priority(), client,
                skill_hash_value=skill_hash(),
            )
        )
        assert outcome.decision.principle_id == outcome.principle.principle_id

    def test_principle_fields_from_payload(self) -> None:
        payload = _decide_payload(
            principle_name="X never Y",
            principle_statement="Z governs W",
            derived_review_ids=["r2"],
        )
        client = FakeClient(default_response=json.dumps(payload))
        outcome = asyncio.run(
            decide_cluster(
                _cluster(members=["r1", "r2", "r3"]),
                _reconciled(), _priority(),
                client, skill_hash_value=skill_hash(),
            )
        )
        assert outcome.principle.name == "X never Y"
        assert outcome.principle.statement == "Z governs W"
        assert outcome.principle.derived_from_review_ids == ["r2"]

    def test_decision_fields_from_payload(self) -> None:
        payload = _decide_payload(
            decision_description="move modal",
            before_snapshot="modal at A",
            after_snapshot="modal at B",
            resolves_heuristics=["modal_excise"],
        )
        client = FakeClient(default_response=json.dumps(payload))
        outcome = asyncio.run(
            decide_cluster(
                _cluster(), _reconciled(heuristics=["modal_excise"]),
                _priority(), client, skill_hash_value=skill_hash(),
            )
        )
        assert outcome.decision.description == "move modal"
        assert outcome.decision.before_snapshot == "modal at A"
        assert outcome.decision.after_snapshot == "modal at B"
        assert outcome.decision.resolves_heuristics == ["modal_excise"]

    def test_parse_failure_yields_fallback(self) -> None:
        client = FakeClient(default_response="not json")
        outcome = asyncio.run(
            decide_cluster(
                _cluster(), _reconciled(), _priority(), client,
                skill_hash_value=skill_hash(),
            )
        )
        assert outcome.status == "fallback"
        assert outcome.principle is None
        assert outcome.decision is None

    def test_invented_heuristic_yields_fallback(self) -> None:
        """Parser cross-reference check catches invented slug."""
        payload = _decide_payload(
            resolves_heuristics=["this_doesnt_exist"]
        )
        client = FakeClient(default_response=json.dumps(payload))
        outcome = asyncio.run(
            decide_cluster(
                _cluster(),
                _reconciled(heuristics=["a", "b"]),
                _priority(),
                client, skill_hash_value=skill_hash(),
            )
        )
        assert outcome.status == "fallback"
        assert "invented slug" in (outcome.reason or "")

    def test_hallucinated_review_id_yields_fallback(self) -> None:
        payload = _decide_payload(derived_review_ids=["r99"])
        client = FakeClient(default_response=json.dumps(payload))
        outcome = asyncio.run(
            decide_cluster(
                _cluster(members=["r1", "r2"]),
                _reconciled(), _priority(),
                client, skill_hash_value=skill_hash(),
            )
        )
        assert outcome.status == "fallback"
        assert "hallucinated" in (outcome.reason or "")

    def test_call_uses_layer_constants(self) -> None:
        client = FakeClient(default_response=_decide_text())
        asyncio.run(
            decide_cluster(
                _cluster(), _reconciled(), _priority(), client,
                skill_hash_value=skill_hash(),
            )
        )
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["skill_id"] == SKILL_ID
        assert call["model"] == MODEL
        assert call["temperature"] == TEMPERATURE
        assert call["max_tokens"] == MAX_TOKENS
        assert call["system"] == SYSTEM_PROMPT

    def test_transport_failure_propagates(self) -> None:
        client = FakeClient(
            raise_on={"Streak loss framing": RuntimeError("replay miss")},
        )
        with pytest.raises(RuntimeError, match="replay miss"):
            asyncio.run(
                decide_cluster(
                    _cluster(), _reconciled(), _priority(), client,
                    skill_hash_value=skill_hash(),
                )
            )


# =============================================================================
# decide_batch
# =============================================================================


class TestDecideBatch:
    def test_processes_all_clusters(self) -> None:
        client = FakeClient(default_response=_decide_text())
        clusters = [_cluster(cluster_id=f"c{i}") for i in range(2)]
        rec = {
            "c0": _reconciled(cluster_id="c0"),
            "c1": _reconciled(cluster_id="c1"),
        }
        pri = {
            "c0": _priority(cluster_id="c0"),
            "c1": _priority(cluster_id="c1"),
        }
        outcomes, failures = asyncio.run(
            decide_batch(clusters, rec, pri, client)
        )
        assert len(outcomes) == 2
        assert failures == []

    def test_missing_reconciled_yields_fallback(self) -> None:
        client = FakeClient()  # empty — would fail if called
        clusters = [_cluster(cluster_id="c0")]
        outcomes, failures = asyncio.run(
            decide_batch(clusters, {}, {"c0": _priority(cluster_id="c0")}, client)
        )
        assert failures == []
        assert outcomes[0].status == "fallback"
        assert "reconciled_verdict" in (outcomes[0].reason or "")

    def test_missing_priority_yields_fallback(self) -> None:
        client = FakeClient()
        clusters = [_cluster(cluster_id="c0")]
        outcomes, failures = asyncio.run(
            decide_batch(
                clusters,
                {"c0": _reconciled(cluster_id="c0")},
                {},
                client,
            )
        )
        assert failures == []
        assert outcomes[0].status == "fallback"
        assert "priority_score" in (outcomes[0].reason or "")

    def test_transport_failure_isolated(self) -> None:
        client = FakeClient(
            default_response=_decide_text(),
            raise_on={"cluster_bad": RuntimeError("boom")},
        )
        clusters = [
            _cluster(cluster_id="c0"),
            _cluster(
                cluster_id="c1",
                quotes=["cluster_bad marker"],
            ),
        ]
        rec = {
            "c0": _reconciled(cluster_id="c0"),
            "c1": _reconciled(cluster_id="c1"),
        }
        pri = {
            "c0": _priority(cluster_id="c0"),
            "c1": _priority(cluster_id="c1"),
        }
        outcomes, failures = asyncio.run(
            decide_batch(clusters, rec, pri, client)
        )
        assert len(outcomes) == 1
        assert len(failures) == 1
        assert failures[0][0] == "c1"


# =============================================================================
# build_provenance
# =============================================================================


class TestProvenance:
    def _outcome(
        self,
        *,
        cluster_id: str,
        status: str,
        principle_name: str = "X never Y",
        review_ids: list[str] | None = None,
        heuristics: list[str] | None = None,
    ) -> DecideOutcome:
        if status == "decided":
            principle = DesignPrinciple(
                principle_id=f"principle__{cluster_id}",
                cluster_id=cluster_id,
                name=principle_name,
                statement="Z governs W.",
                derived_from_review_ids=review_ids or ["r1", "r2", "r3"],
            )
            decision = DesignDecision(
                decision_id=f"decision__{cluster_id}__1",
                principle_id=f"principle__{cluster_id}",
                description="move modal",
                before_snapshot="modal at A",
                after_snapshot="modal at B",
                resolves_heuristics=heuristics or ["h1", "h2"],
            )
        else:
            principle = None
            decision = None
        return DecideOutcome(
            cluster_id=cluster_id,
            principle=principle,
            decision=decision,
            native_payload={"_": "ok"} if status == "decided" else {"fallback": True},
            status=status,  # type: ignore[arg-type]
            reason=None if status == "decided" else "fallback reason",
        )

    def test_counts(self) -> None:
        outs = [
            self._outcome(cluster_id="c0", status="decided"),
            self._outcome(cluster_id="c1", status="fallback"),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL)
        assert prov["cluster_count"] == 2
        assert prov["decided_count"] == 1
        assert prov["fallback_count"] == 1

    def test_resolves_heuristics_count_histogram(self) -> None:
        outs = [
            self._outcome(
                cluster_id="c0", status="decided", heuristics=["h1", "h2"]
            ),
            self._outcome(
                cluster_id="c1", status="decided",
                heuristics=["h1", "h2", "h3", "h4"],
            ),
            self._outcome(
                cluster_id="c2", status="decided", heuristics=["h1"]
            ),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL)
        hist = prov["resolves_heuristics_count_histogram"]
        assert hist == {"1": 1, "2": 1, "4": 1}

    def test_derived_from_review_ids_count_histogram(self) -> None:
        outs = [
            self._outcome(
                cluster_id="c0", status="decided", review_ids=["r1", "r2"]
            ),
            self._outcome(
                cluster_id="c1", status="decided",
                review_ids=["r1", "r2", "r3", "r4", "r5"],
            ),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL)
        hist = prov["derived_from_review_ids_count_histogram"]
        assert hist == {"2": 1, "5": 1}

    def test_distinct_principle_names(self) -> None:
        outs = [
            self._outcome(cluster_id="c0", status="decided", principle_name="A"),
            self._outcome(cluster_id="c1", status="decided", principle_name="B"),
            self._outcome(cluster_id="c2", status="decided", principle_name="A"),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL)
        assert prov["distinct_principle_names"] == 2
        assert prov["principle_name_duplication_count"] == 1

    def test_mean_statistics(self) -> None:
        outs = [
            self._outcome(
                cluster_id="c0", status="decided",
                heuristics=["a", "b"],  # 2
                review_ids=["r1"],  # 1
            ),
            self._outcome(
                cluster_id="c1", status="decided",
                heuristics=["a", "b", "c", "d"],  # 4
                review_ids=["r1", "r2", "r3"],  # 3
            ),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL)
        assert prov["mean_heuristics_resolved"] == 3.0  # (2+4)/2
        assert prov["mean_review_ids_cited"] == 2.0  # (1+3)/2

    def test_transport_failures_rendered(self) -> None:
        prov = build_provenance(
            outcomes=[], failures=[("c99", ValueError("oops"))], model=MODEL,
        )
        assert prov["transport_failure_count"] == 1


# =============================================================================
# CLI — main
# =============================================================================


class TestMain:
    def test_cli_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l7, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [_cluster(cluster_id="cluster_02").model_dump(mode="json")],
        )
        reconciled_path = data_dir / "reconciled.jsonl"
        _write_jsonl(
            reconciled_path,
            [_reconciled(cluster_id="cluster_02").model_dump(mode="json")],
        )
        priority_path = data_dir / "priority.jsonl"
        _write_jsonl(
            priority_path,
            [_priority(cluster_id="cluster_02").model_dump(mode="json")],
        )
        principles_path = data_dir / "principles.jsonl"
        decisions_path = data_dir / "decisions.jsonl"
        native_path = data_dir / "native.jsonl"

        fake = FakeClient(default_response=_decide_text())
        monkeypatch.setattr(l7, "Client", lambda **_k: fake)

        rc = main(
            [
                "--clusters", str(clusters_path),
                "--reconciled", str(reconciled_path),
                "--priority", str(priority_path),
                "--principles-output", str(principles_path),
                "--decisions-output", str(decisions_path),
                "--native-output", str(native_path),
                "--mode", "replay",
                "--run-id", "l7-test-run",
            ]
        )
        assert rc == 0
        assert principles_path.exists()
        assert decisions_path.exists()
        assert native_path.exists()

        principles = [
            json.loads(line) for line in principles_path.read_text().splitlines()
        ]
        assert len(principles) == 1
        DesignPrinciple.model_validate(principles[0])

        decisions = [
            json.loads(line) for line in decisions_path.read_text().splitlines()
        ]
        assert len(decisions) == 1
        DesignDecision.model_validate(decisions[0])
        # FK consistency.
        assert decisions[0]["principle_id"] == principles[0]["principle_id"]

        prov_path = principles_path.with_suffix(".provenance.json")
        prov = json.loads(prov_path.read_text())
        assert prov["decided_count"] == 1

    def test_cli_empty_clusters(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l7, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        (data_dir / "reconciled.jsonl").write_text("")
        (data_dir / "priority.jsonl").write_text("")

        monkeypatch.setattr(l7, "Client", lambda **_k: FakeClient())
        rc = main(
            [
                "--clusters", str(clusters_path),
                "--reconciled", str(data_dir / "reconciled.jsonl"),
                "--priority", str(data_dir / "priority.jsonl"),
                "--principles-output", str(data_dir / "p.jsonl"),
                "--decisions-output", str(data_dir / "d.jsonl"),
                "--native-output", str(data_dir / "n.jsonl"),
                "--mode", "replay",
            ]
        )
        assert rc == 1
