"""Tests for ``auditable_design.layers.l4_audit_ux_architecture``.

Structure mirrors ``test_l4_audit_interaction_design.py`` (the Cooper
sibling module) so a reader can diff the two and see where Garrett's
UX-architecture contract diverges:

* **Five Garrett planes** — ``strategy_coherence``, ``scope_coverage``,
  ``structure_navigation``, ``skeleton_wireframe``, ``surface_sensory``;
  Elements-of-UX planes, not Cooper dimensions, not Canvas blocks, not
  POUR.
* **``product_type`` + ``decision_mode`` findings, no ``posture`` /
  ``user_tier`` / ``excise_type``** — each finding names exactly one
  product type (four values: functional / informational / hybrid /
  not_applicable — Garrett's fundamental duality plus seam + escape
  hatch) and exactly one decision mode (five values: conscious / default
  / mimicry / fiat / not_applicable — Garrett's central moral axis).
* **Quotes are *not* always required** — unlike Kahneman, a UX-
  architecture finding can rest on ``html`` or ``ui_context`` alone
  (skeleton-priority defect visible in markup, structure mismatch
  observed in HTML). The parser enforces the bidirectional rule:
  ``"quotes"`` in ``evidence_source`` ↔ non-empty
  ``evidence_quote_idxs``.
* **Unconscious-decision cap** — a finding with ``decision_mode`` ∈
  {``default``, ``mimicry``, ``fiat``} at severity ≥ 3 forces the
  enclosing dimension score to ``≤ 2``. Garrett's central claim: every
  UX element should be the product of a conscious decision; an
  unconscious decision at sev ≥ 3 is structural, not local.
* **No duplicate ``(heuristic, product_type)`` pairs** — two findings
  may share a heuristic when product types differ (one functional, one
  informational), but not when both pairs are identical.

Strategy
--------
Every test that would otherwise exercise Claude uses an in-process
:class:`FakeClient` with scripted responses — same pattern as the
other L4 test modules. No network, no real replay log; whole file
runs in < 1 s.
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
from auditable_design.layers import l4_audit_ux_architecture as ux
from auditable_design.layers.l4_audit_ux_architecture import (
    DEFAULT_LABELED,
    DEFAULT_NATIVE,
    DEFAULT_VERDICTS,
    DIMENSION_KEYS,
    LAYER_NAME,
    MAX_TOKENS,
    MODEL,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    VALID_DECISION_MODES,
    VALID_PRODUCT_TYPES,
    AuditOutcome,
    AuditParseError,
    audit_batch,
    audit_cluster,
    build_provenance,
    build_user_message,
    main,
    parse_audit_response,
    skill_hash,
)
from auditable_design.schemas import AuditVerdict, InsightCluster


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class FakeClient:
    """In-memory stand-in for claude_client.Client.

    Duplicated (intentionally) from the other L4 test modules so a
    change in one test file doesn't silently affect the others. First
    substring hit in ``user`` wins when scripting responses.
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
    label: str = (
        "Streak-loss modal sits where primary-action path should — "
        "skeleton overridden by monetisation fiat"
    ),
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    centroid_ref: str = "l3_centroids.npy#0",
    ui_context: str | None = None,
    html: str | None = None,
    screenshot_ref: str | None = None,
) -> InsightCluster:
    """Build an InsightCluster with SKILL.md-aligned defaults.

    Defaults model the Duolingo streak-loss worked example from
    SKILL.md — the canonical skeleton-plane-overridden-by-fiat surface.
    """
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref=centroid_ref,
        representative_quotes=quotes
        or [
            "the app has been quietly turning into a subscription funnel",
            "the modal doesn't look like the lesson screen at all",
            "when the modal fires there's nothing else on the screen",
            "I don't know what this app is anymore — is it a learning tool or a subscription product",
        ],
        ui_context=ui_context,
        html=html,
        screenshot_ref=screenshot_ref,
    )


def _finding(
    *,
    dimension: str = "skeleton_wireframe",
    heuristic: str = "skeleton_does_not_honour_priority",
    product_type: str = "functional",
    decision_mode: str = "conscious",
    violation: str = (
        "The mid-lesson skeleton is replaced wholesale by a marketing "
        "modal; the lesson's own skeleton elements are hidden rather "
        "than preserved alongside the overlay."
    ),
    severity: int = 3,
    evidence_source: list[str] | None = None,
    evidence_quote_idxs: list[int] | None = None,
    recommendation: str = (
        "Render the streak-risk surface as a non-replacing overlay "
        "that preserves the lesson skeleton underneath."
    ),
) -> dict[str, Any]:
    """Build one finding dict with SKILL.md-valid defaults.

    Defaults describe a plausible skeleton-priority finding anchored
    on the first two quotes. Override individual fields to hit
    business-rule edges. Default ``decision_mode`` is ``conscious`` so
    the unconscious-decision cap does not trigger; callers who test
    the cap must override to ``default``/``mimicry``/``fiat``.
    """
    return {
        "dimension": dimension,
        "heuristic": heuristic,
        "product_type": product_type,
        "decision_mode": decision_mode,
        "violation": violation,
        "severity": severity,
        "evidence_source": evidence_source
        if evidence_source is not None
        else ["quotes", "ui_context"],
        "evidence_quote_idxs": evidence_quote_idxs
        if evidence_quote_idxs is not None
        else [0, 1],
        "recommendation": recommendation,
    }


def _happy_payload(
    *,
    dim_scores: dict[str, int] | None = None,
    findings: list[dict[str, Any]] | None = None,
    summary: str = (
        "Scope has accreted monetisation the strategy does not endorse; "
        "skeleton is overridden mid-lesson by a marketing modal whose "
        "surface language is lifted from the pricing page."
    ),
) -> dict[str, Any]:
    """Structurally-valid UX-architecture payload.

    Defaults: neutral 3s across the five planes (the default finding's
    ``decision_mode`` is ``conscious`` so the unconscious-decision cap
    is not tripped), one skeleton-priority finding. Callers override
    whichever slice is under test.
    """
    scores = (
        dim_scores
        if dim_scores is not None
        else {
            "strategy_coherence": 3,
            "scope_coverage": 3,
            "structure_navigation": 3,
            "skeleton_wireframe": 3,
            "surface_sensory": 3,
        }
    )
    return {
        "summary": summary,
        "dimension_scores": scores,
        "findings": findings if findings is not None else [_finding()],
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
        assert SKILL_ID == "audit-ux-architecture"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l4_audit_ux_architecture"

    def test_default_model_is_sonnet(self) -> None:
        # Same rationale as the other L4 skills: Sonnet 4.6 is
        # reasoning-capable without Opus's budget. Shift to Opus
        # requires an ADR.
        assert MODEL == "claude-sonnet-4-6"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        # UX-architecture payloads carry per-finding product_type +
        # decision_mode (short closed-set codes) plus
        # violation/recommendation — similar envelope to Cooper.
        # 6144 sits comfortably in the operating band.
        assert 4096 <= MAX_TOKENS <= 12288

    def test_dimension_keys_exactly_five_garrett_planes(self) -> None:
        assert DIMENSION_KEYS == frozenset(
            {
                "strategy_coherence",
                "scope_coverage",
                "structure_navigation",
                "skeleton_wireframe",
                "surface_sensory",
            }
        )

    def test_valid_product_types_closed_set_four(self) -> None:
        assert VALID_PRODUCT_TYPES == frozenset(
            {
                "functional",
                "informational",
                "hybrid",
                "not_applicable",
            }
        )

    def test_valid_decision_modes_closed_set_five(self) -> None:
        assert VALID_DECISION_MODES == frozenset(
            {
                "conscious",
                "default",
                "mimicry",
                "fiat",
                "not_applicable",
            }
        )

    def test_default_paths_under_data_derived(self) -> None:
        # Same input as the other L4 skills (shared L3b labeled
        # clusters); distinct outputs so L5 can ingest all six
        # skills as sibling layer-4 rows.
        assert DEFAULT_LABELED == Path("data/derived/l3b_labeled_clusters.jsonl")
        assert DEFAULT_VERDICTS == Path(
            "data/derived/l4_audit_ux_architecture_verdicts.jsonl"
        )
        assert DEFAULT_NATIVE == Path(
            "data/derived/l4_audit_ux_architecture_verdicts.native.jsonl"
        )

    def test_skill_hash_independent_of_siblings(self) -> None:
        # Defence in depth: editing any other L4 SKILL.md must not
        # alter UX-architecture's cache key and vice versa.
        from auditable_design.layers import (
            l4_audit,
            l4_audit_accessibility,
            l4_audit_business_alignment,
            l4_audit_decision_psychology,
            l4_audit_interaction_design,
        )

        assert skill_hash() != l4_audit.skill_hash()
        assert skill_hash() != l4_audit_accessibility.skill_hash()
        assert skill_hash() != l4_audit_decision_psychology.skill_hash()
        assert skill_hash() != l4_audit_business_alignment.skill_hash()
        assert skill_hash() != l4_audit_interaction_design.skill_hash()


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

    def test_stable_across_calls(self) -> None:
        assert skill_hash() == skill_hash()


# =============================================================================
# build_user_message
# =============================================================================


class TestBuildUserMessage:
    def test_minimal_cluster_contains_label_and_quotes_only(self) -> None:
        c = _cluster(quotes=["a", "b"])
        msg = build_user_message(c)
        assert "<cluster>" in msg and "</cluster>" in msg
        assert f"<label>{c.label}</label>" in msg
        assert '<q idx="0">a</q>' in msg
        assert '<q idx="1">b</q>' in msg
        # Optional tags omitted when fields are None.
        assert "<ui_context>" not in msg
        assert "<html>" not in msg
        assert "<screenshot_ref>" not in msg

    def test_ui_context_only(self) -> None:
        c = _cluster(ui_context="Duolingo mid-lesson streak-risk modal")
        msg = build_user_message(c)
        assert (
            "<ui_context>Duolingo mid-lesson streak-risk modal</ui_context>" in msg
        )
        assert "<html>" not in msg
        assert "<screenshot_ref>" not in msg

    def test_html_only_wrapped_in_cdata(self) -> None:
        c = _cluster(html='<button class="cta">Keep my streak</button>')
        msg = build_user_message(c)
        # Outer <html> tags are the injection boundary; inner markup is
        # in CDATA so angle brackets survive verbatim.
        assert "<html><![CDATA[\n" in msg
        assert '<button class="cta">Keep my streak</button>' in msg
        assert "]]></html>" in msg

    def test_screenshot_ref_only(self) -> None:
        c = _cluster(screenshot_ref="data/artifacts/ui/paywall.png")
        msg = build_user_message(c)
        assert (
            "<screenshot_ref>data/artifacts/ui/paywall.png</screenshot_ref>"
            in msg
        )

    def test_tag_order_is_fixed(self) -> None:
        """Fixed tag order: label → ui_context → html → screenshot_ref
        → q*. Locking the order keeps replay cache keys stable across
        reruns and matches the other L4 prompts byte-for-byte.
        """
        c = _cluster(
            quotes=["x"],
            ui_context="ctx",
            html="<div></div>",
            screenshot_ref="s.png",
        )
        msg = build_user_message(c)
        i_label = msg.index("<label>")
        i_ui = msg.index("<ui_context>")
        i_html = msg.index("<html>")
        i_ss = msg.index("<screenshot_ref>")
        i_q = msg.index('<q idx="0">')
        assert i_label < i_ui < i_html < i_ss < i_q

    def test_label_quotes_ui_context_and_screenshot_ref_are_escaped(self) -> None:
        c = _cluster(
            label="A & B <injected>",
            quotes=["hi <script>alert()</script> & more"],
            ui_context="caf\u00e9 & noise <issue>",
            screenshot_ref="path/with<bad>&chars.png",
        )
        msg = build_user_message(c)
        # Data content escaped; structural tags remain literal.
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg
        assert "&amp;" in msg
        assert "&lt;injected&gt;" in msg
        assert "&lt;issue&gt;" in msg
        assert "path/with&lt;bad&gt;&amp;chars.png" in msg
        assert "<cluster>" in msg
        assert "<label>" in msg

    def test_html_content_is_not_escaped_because_cdata(self) -> None:
        raw = '<button onclick="x()">Submit & go</button>'
        c = _cluster(html=raw)
        msg = build_user_message(c)
        assert raw in msg
        assert "&amp;" not in msg.split("<html>")[1].split("</html>")[0]

    def test_idx_attribute_is_zero_indexed(self) -> None:
        c = _cluster(quotes=["a", "b", "c"])
        msg = build_user_message(c)
        assert '<q idx="0">a</q>' in msg
        assert '<q idx="1">b</q>' in msg
        assert '<q idx="2">c</q>' in msg


# =============================================================================
# parse_audit_response — happy paths
# =============================================================================


class TestParseAuditResponseHappy:
    def test_minimal_happy_path(self) -> None:
        payload = parse_audit_response(_happy_response_text(), n_quotes=5)
        assert set(payload["dimension_scores"]) == DIMENSION_KEYS
        assert len(payload["findings"]) == 1

    def test_tolerates_leading_prose(self) -> None:
        text = "Thinking...\n\n" + _happy_response_text()
        parse_audit_response(text, n_quotes=5)

    def test_tolerates_code_fences(self) -> None:
        text = "```json\n" + _happy_response_text() + "\n```"
        parse_audit_response(text, n_quotes=5)

    def test_empty_findings_list_is_valid(self) -> None:
        payload = _happy_payload(
            dim_scores={k: 5 for k in DIMENSION_KEYS},
            findings=[],
        )
        parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_conscious_finding_sev3_with_dim_3_passes(self) -> None:
        """The unconscious-decision cap does not trigger when
        ``decision_mode == 'conscious'`` — a conscious sev-3 finding
        with dim score 3 is legal."""
        f = _finding(
            dimension="strategy_coherence",
            heuristic="strategy_contradicts_itself",
            product_type="hybrid",
            decision_mode="conscious",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_markup_only_finding_passes(self) -> None:
        """Key difference from Kahneman: a UX-architecture finding
        that cites only ``html`` / ``ui_context`` is legal — e.g. a
        skeleton-priority defect visible in markup without a quoted
        reviewer voice."""
        f = _finding(
            dimension="skeleton_wireframe",
            heuristic="interface_components_default_platform",
            product_type="functional",
            decision_mode="default",
            severity=2,
            evidence_source=["html", "ui_context"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_not_applicable_product_type_passes(self) -> None:
        """``not_applicable`` is a legal product_type for findings on
        planes where the functional/informational duality does not
        split cleanly (strategy and surface)."""
        f = _finding(
            dimension="strategy_coherence",
            heuristic="user_needs_unarticulated",
            product_type="not_applicable",
            decision_mode="default",
            severity=2,
            evidence_source=["ui_context"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_not_applicable_decision_mode_passes(self) -> None:
        """``not_applicable`` is a legal decision_mode for findings
        whose shape does not support an authorship diagnosis."""
        f = _finding(
            dimension="structure_navigation",
            heuristic="information_architecture_implicit",
            product_type="informational",
            decision_mode="not_applicable",
            severity=2,
            evidence_source=["ui_context"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_all_four_product_types_pass_individually(self) -> None:
        """Defensive coverage: every closed-set product_type parses."""
        for product_type in VALID_PRODUCT_TYPES:
            f = _finding(
                heuristic=f"h_{product_type}",
                dimension="structure_navigation",
                product_type=product_type,
                decision_mode="conscious",
                severity=2,
                evidence_source=["quotes"],
                evidence_quote_idxs=[0],
            )
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])),
                n_quotes=5,
            )

    def test_all_five_decision_modes_pass_individually(self) -> None:
        """Every closed-set decision_mode should be legal at sev ≤ 2
        (the unconscious-decision cap only triggers at sev ≥ 3)."""
        for decision_mode in VALID_DECISION_MODES:
            f = _finding(
                heuristic=f"h_{decision_mode}",
                dimension="skeleton_wireframe",
                product_type="functional",
                decision_mode=decision_mode,
                severity=2,
                evidence_source=["quotes"],
                evidence_quote_idxs=[0],
            )
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])),
                n_quotes=5,
            )

    def test_unconscious_mode_sev3_with_dim_2_passes(self) -> None:
        """The decision-mode cap is inclusive on the upper side:
        sev-3 unconscious-mode with dim score 2 is legal (≤ 2 required)."""
        f = _finding(
            dimension="skeleton_wireframe",
            heuristic="skeleton_does_not_honour_priority",
            product_type="functional",
            decision_mode="fiat",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["skeleton_wireframe"] = 2
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_unconscious_mode_sev3_with_dim_1_passes(self) -> None:
        """Floor of the allowed band — dim score 1 passes the ≤ 2 rule."""
        f = _finding(
            dimension="skeleton_wireframe",
            heuristic="skeleton_does_not_honour_priority",
            product_type="functional",
            decision_mode="mimicry",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["skeleton_wireframe"] = 1
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_mixed_evidence_sources_with_quotes(self) -> None:
        f = _finding(
            evidence_source=["quotes", "html", "screenshot", "ui_context"],
            evidence_quote_idxs=[0, 1, 2],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )


# =============================================================================
# parse_audit_response — structural failures
# =============================================================================


class TestParseAuditResponseStructural:
    def test_no_json(self) -> None:
        with pytest.raises(AuditParseError, match="no JSON object"):
            parse_audit_response("sorry nothing here", n_quotes=3)

    def test_malformed_json_unbalanced(self) -> None:
        with pytest.raises(AuditParseError, match="no JSON object"):
            parse_audit_response('{"summary": "x"', n_quotes=3)

    def test_missing_top_level_key(self) -> None:
        with pytest.raises(AuditParseError, match="missing required top-level keys"):
            parse_audit_response(
                '{"summary": "x", "dimension_scores": {}}', n_quotes=3
            )

    def test_extra_top_level_key(self) -> None:
        payload = {**_happy_payload(), "extra": 1}
        with pytest.raises(AuditParseError, match="unexpected top-level keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_empty_summary(self) -> None:
        payload = _happy_payload(summary="   ")
        with pytest.raises(AuditParseError, match="summary.*non-empty"):
            parse_audit_response(json.dumps(payload), n_quotes=5)


# =============================================================================
# parse_audit_response — dimension_scores
# =============================================================================


class TestParseAuditResponseDimensions:
    def test_missing_dimension_key(self) -> None:
        payload = _happy_payload(
            dim_scores={
                "strategy_coherence": 3,
                "scope_coverage": 3,
                "structure_navigation": 3,
                "skeleton_wireframe": 3,
                # surface_sensory missing
            }
        )
        with pytest.raises(AuditParseError, match="dimension_scores missing keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_norman_dimensions_are_rejected(self) -> None:
        """A Norman-shaped payload must fail here — wiring mistake
        where the wrong skill's output was pointed at the wrong parser.
        """
        payload = _happy_payload(
            dim_scores={
                "interaction_fundamentals": 3,
                "action_cognition": 3,
                "error_architecture": 3,
                "system_maturity": 3,
            }
        )
        with pytest.raises(
            AuditParseError, match="dimension_scores (missing|has unexpected) keys"
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_kahneman_dimensions_are_rejected(self) -> None:
        """Kahneman-shaped dimension keys must fail here — guards
        against cross-skill L5 ingestion wiring slip."""
        payload = _happy_payload(
            dim_scores={
                "cognitive_load_ease": 3,
                "choice_architecture": 3,
                "judgment_heuristics": 3,
                "temporal_experience": 3,
            }
        )
        with pytest.raises(
            AuditParseError, match="dimension_scores (missing|has unexpected) keys"
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_accessibility_dimensions_are_rejected(self) -> None:
        payload = _happy_payload(
            dim_scores={
                "perceivable": 3,
                "operable": 3,
                "understandable": 3,
                "robust": 3,
                "inclusive_cognitive": 3,
            }
        )
        with pytest.raises(
            AuditParseError, match="dimension_scores (missing|has unexpected) keys"
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_osterwalder_dimensions_are_rejected(self) -> None:
        """Canvas-shaped dimension keys must fail here — guards against
        cross-skill wiring slip from the Osterwalder sibling."""
        payload = _happy_payload(
            dim_scores={
                "value_delivery": 3,
                "revenue_relationships": 3,
                "infrastructure_fit": 3,
                "pattern_coherence": 3,
            }
        )
        with pytest.raises(
            AuditParseError, match="dimension_scores (missing|has unexpected) keys"
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_cooper_dimensions_are_rejected(self) -> None:
        """Cooper-shaped dimension keys must fail here — guards against
        cross-skill wiring slip from the interaction-design sibling."""
        payload = _happy_payload(
            dim_scores={
                "posture_platform_fit": 3,
                "flow_excise": 3,
                "idioms_learnability": 3,
                "etiquette_forgiveness": 3,
            }
        )
        with pytest.raises(
            AuditParseError, match="dimension_scores (missing|has unexpected) keys"
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_extra_dimension_key(self) -> None:
        payload = _happy_payload(
            dim_scores={**{k: 3 for k in DIMENSION_KEYS}, "extra": 3}
        )
        with pytest.raises(AuditParseError, match="unexpected keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_score_out_of_range(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["strategy_coherence"] = 7
        with pytest.raises(AuditParseError, match=r"out of \{1,2,3,4,5\}"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )

    def test_bool_rejected_as_score(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["strategy_coherence"] = True  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="must be int, got bool"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )


# =============================================================================
# parse_audit_response — findings structural
# =============================================================================


class TestParseAuditResponseFindingsStructural:
    def test_missing_product_type_rejected(self) -> None:
        f = _finding()
        f.pop("product_type")
        with pytest.raises(AuditParseError, match=r"findings\[0\] missing keys"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_missing_decision_mode_rejected(self) -> None:
        f = _finding()
        f.pop("decision_mode")
        with pytest.raises(AuditParseError, match=r"findings\[0\] missing keys"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_extra_finding_key(self) -> None:
        f = {**_finding(), "bogus": "x"}
        with pytest.raises(AuditParseError, match="unexpected keys"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_kahneman_keys_rejected(self) -> None:
        """Kahneman-shaped findings (``mechanism`` / ``intent``) must
        fail here — catches the mirror wiring mistake where a Kahneman
        payload is fed into the UX-architecture parser."""
        f = _finding()
        f.pop("product_type")
        f.pop("decision_mode")
        f["mechanism"] = "loss_aversion"
        f["intent"] = "dark_pattern"
        with pytest.raises(
            AuditParseError, match=r"findings\[0\] (missing|unexpected) keys"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_osterwalder_keys_rejected(self) -> None:
        """Osterwalder-shaped findings (``building_blocks`` / ``tension``
        / ``pattern``) must fail here."""
        f = _finding()
        f.pop("product_type")
        f.pop("decision_mode")
        f["building_blocks"] = ["vp"]
        f["tension"] = []
        f["pattern"] = "freemium"
        with pytest.raises(
            AuditParseError, match=r"findings\[0\] (missing|unexpected) keys"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_cooper_keys_rejected(self) -> None:
        """Cooper-shaped findings (``posture`` / ``user_tier`` /
        ``excise_type``) must fail here — catches the mirror wiring
        mistake where a Cooper payload is fed into the UX-architecture
        parser."""
        f = _finding()
        f.pop("product_type")
        f.pop("decision_mode")
        f["posture"] = "sovereign"
        f["user_tier"] = "intermediate"
        f["excise_type"] = "modal"
        with pytest.raises(
            AuditParseError, match=r"findings\[0\] (missing|unexpected) keys"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_invalid_dimension(self) -> None:
        f = _finding(dimension="bogus_dimension")
        with pytest.raises(AuditParseError, match="dimension='bogus_dimension'"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_kahneman_dimension_rejected(self) -> None:
        f = _finding(dimension="choice_architecture")
        with pytest.raises(
            AuditParseError, match="dimension='choice_architecture'"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_osterwalder_dimension_rejected(self) -> None:
        f = _finding(dimension="value_delivery")
        with pytest.raises(
            AuditParseError, match="dimension='value_delivery'"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_cooper_dimension_rejected(self) -> None:
        f = _finding(dimension="flow_excise")
        with pytest.raises(AuditParseError, match="dimension='flow_excise'"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_accessibility_dimension_rejected(self) -> None:
        f = _finding(dimension="perceivable")
        with pytest.raises(AuditParseError, match="dimension='perceivable'"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_severity_out_of_range(self) -> None:
        f = _finding(severity=5)
        with pytest.raises(AuditParseError, match=r"severity=5 out of"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_empty_heuristic_string(self) -> None:
        f = _finding(heuristic="   ")
        with pytest.raises(AuditParseError, match=r"heuristic.*non-empty"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — product_type closed set
# =============================================================================


class TestParseAuditResponseProductType:
    def test_invalid_product_type_rejected(self) -> None:
        f = _finding(product_type="transactional")
        with pytest.raises(
            AuditParseError, match=r"product_type='transactional' not in"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_product_type_non_string_rejected(self) -> None:
        f = _finding()
        f["product_type"] = None  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="product_type must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_product_type_empty_string_rejected(self) -> None:
        f = _finding(product_type="")
        with pytest.raises(AuditParseError, match=r"product_type='' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_product_type_list_rejected(self) -> None:
        """``product_type`` is a single string, not a list — this
        guards against a model that copies the Osterwalder
        ``building_blocks`` shape onto the Garrett finding."""
        f = _finding()
        f["product_type"] = ["functional"]  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="product_type must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — decision_mode closed set
# =============================================================================


class TestParseAuditResponseDecisionMode:
    def test_invalid_decision_mode_rejected(self) -> None:
        f = _finding(decision_mode="imitation")
        with pytest.raises(
            AuditParseError, match=r"decision_mode='imitation' not in"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_decision_mode_non_string_rejected(self) -> None:
        f = _finding()
        f["decision_mode"] = 42  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="decision_mode must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_decision_mode_empty_string_rejected(self) -> None:
        f = _finding(decision_mode="")
        with pytest.raises(AuditParseError, match=r"decision_mode='' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — decision_mode × dimension cap (cross-finding rule)
# =============================================================================


class TestParseAuditResponseDecisionModeCap:
    def test_default_sev3_forces_dim_score_cap(self) -> None:
        """Cross-finding rule: decision_mode == 'default' at sev ≥ 3 →
        dim score ≤ 2. Unconscious design is structural."""
        f = _finding(
            dimension="skeleton_wireframe",
            heuristic="interface_components_default_platform",
            product_type="functional",
            decision_mode="default",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}  # offending: > 2 with default sev 3
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError,
            match=r"decision_mode='default'.*at severity 3.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_mimicry_sev3_forces_dim_score_cap(self) -> None:
        """Same cap applies to ``mimicry`` — the rule is
        "unconscious decision", not "platform default"."""
        f = _finding(
            dimension="surface_sensory",
            heuristic="surface_trend_mimicry",
            product_type="informational",
            decision_mode="mimicry",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError,
            match=r"decision_mode='mimicry'.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_fiat_sev3_forces_dim_score_cap(self) -> None:
        """Same cap applies to ``fiat`` — the rule is "unconscious
        decision", not "platform default" or "mimicry"."""
        f = _finding(
            dimension="skeleton_wireframe",
            heuristic="skeleton_does_not_honour_priority",
            product_type="hybrid",
            decision_mode="fiat",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError,
            match=r"decision_mode='fiat'.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_unconscious_sev4_forces_dim_score_cap(self) -> None:
        """Same rule at the top of the severity range — guards against
        an off-by-one in the ``>= 3`` threshold."""
        f = _finding(
            dimension="scope_coverage",
            heuristic="scope_tracks_competitor_checklist",
            product_type="functional",
            decision_mode="mimicry",
            severity=4,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError,
            match=r"decision_mode='mimicry'.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_conscious_sev3_does_not_force_dim_cap(self) -> None:
        """The decision-mode cap applies only to default/mimicry/fiat.
        A sev-3 conscious finding + dim score 3 is legal."""
        f = _finding(
            dimension="strategy_coherence",
            heuristic="strategy_contradicts_itself",
            product_type="hybrid",
            decision_mode="conscious",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_not_applicable_sev3_does_not_force_dim_cap(self) -> None:
        """A decision_mode of ``not_applicable`` at sev ≥ 3 does not
        trigger the cap — the rule is about the three unconscious
        modes, not about "anything non-conscious"."""
        f = _finding(
            dimension="structure_navigation",
            heuristic="information_architecture_implicit",
            product_type="informational",
            decision_mode="not_applicable",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_unconscious_sev2_does_not_force_dim_cap(self) -> None:
        """Cap triggers at sev ≥ 3; a sev-2 unconscious-mode + dim
        score 3 is legal (defends against an off-by-one in the
        ``>= 3`` check)."""
        f = _finding(
            dimension="surface_sensory",
            heuristic="surface_trend_mimicry",
            product_type="informational",
            decision_mode="mimicry",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )


# =============================================================================
# parse_audit_response — no duplicate (heuristic, product_type) pairs
# =============================================================================


class TestParseAuditResponseDuplicates:
    def test_duplicate_pair_rejected(self) -> None:
        f1 = _finding(heuristic="featuritis", product_type="functional")
        f2 = _finding(
            heuristic="featuritis",
            product_type="functional",
            # Same heuristic AND same product_type — duplicate pair.
            evidence_quote_idxs=[2, 3],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        payload = _happy_payload(dim_scores=scores, findings=[f1, f2])
        with pytest.raises(
            AuditParseError,
            match=r"repeats \(heuristic, product_type\) pair",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_same_heuristic_different_product_type_passes(self) -> None:
        """Two findings may share ``heuristic`` if they name different
        product types — SKILL.md guards against *pair* duplicates.
        Canonical example: featuritis exists both on the functional
        surface (feature accretion) and on the informational surface
        (content accretion)."""
        f1 = _finding(
            dimension="scope_coverage",
            heuristic="featuritis",
            product_type="functional",
            decision_mode="conscious",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        f2 = _finding(
            dimension="scope_coverage",
            heuristic="featuritis",
            product_type="informational",
            decision_mode="conscious",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[1],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f1, f2])),
            n_quotes=5,
        )

    def test_same_heuristic_hybrid_vs_functional_passes(self) -> None:
        """A hybrid-type finding and a functional-type finding may
        share a heuristic — product types differ so the pair is
        unique."""
        f1 = _finding(
            dimension="skeleton_wireframe",
            heuristic="skeleton_does_not_honour_priority",
            product_type="hybrid",
            decision_mode="conscious",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        f2 = _finding(
            dimension="skeleton_wireframe",
            heuristic="skeleton_does_not_honour_priority",
            product_type="functional",
            decision_mode="conscious",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[1],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f1, f2])),
            n_quotes=5,
        )


# =============================================================================
# parse_audit_response — evidence_source + bidirectional quotes/idxs rule
# =============================================================================


class TestParseAuditResponseEvidenceSource:
    def test_evidence_source_empty(self) -> None:
        f = _finding(
            evidence_source=[],
            evidence_quote_idxs=[0],
        )
        with pytest.raises(AuditParseError, match="must be non-empty"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_source_not_list(self) -> None:
        f = _finding()
        f["evidence_source"] = "quotes"  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="must be list"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_source_invalid_token(self) -> None:
        f = _finding(evidence_source=["markup", "quotes"], evidence_quote_idxs=[0])
        with pytest.raises(AuditParseError, match=r"not in \["):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_source_duplicates_rejected(self) -> None:
        f = _finding(
            evidence_source=["quotes", "quotes", "html"],
            evidence_quote_idxs=[0],
        )
        with pytest.raises(AuditParseError, match="contains duplicates"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_quotes_in_source_requires_nonempty_idxs(self) -> None:
        """Bidirectional rule forward: ``quotes`` in evidence_source →
        non-empty evidence_quote_idxs."""
        f = _finding(
            evidence_source=["quotes", "html"],
            evidence_quote_idxs=[],
        )
        with pytest.raises(
            AuditParseError,
            match="'quotes' in evidence_source requires non-empty quote idxs",
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_nonempty_idxs_require_quotes_in_source(self) -> None:
        """Bidirectional rule reverse: non-empty evidence_quote_idxs →
        ``quotes`` in evidence_source."""
        f = _finding(
            evidence_source=["html", "ui_context"],
            evidence_quote_idxs=[0, 1],
        )
        with pytest.raises(
            AuditParseError,
            match=r"non-empty quote idxs requires 'quotes' in evidence_source",
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_no_quotes_no_idxs_passes(self) -> None:
        """The UX-architecture relaxation: markup-only findings with
        no quotes and no idxs are legal (key contrast with Kahneman)."""
        f = _finding(
            dimension="skeleton_wireframe",
            heuristic="interface_components_default_platform",
            product_type="functional",
            decision_mode="default",
            severity=2,
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_evidence_idx_out_of_range(self) -> None:
        f = _finding(
            evidence_source=["quotes", "html"],
            evidence_quote_idxs=[99],
        )
        with pytest.raises(AuditParseError, match=r"out of \[0, 5\)"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_quote_idxs_not_list(self) -> None:
        f = _finding()
        f["evidence_quote_idxs"] = "0"  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="must be list"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_quote_idxs_bool_rejected(self) -> None:
        f = _finding()
        f["evidence_quote_idxs"] = [True]  # type: ignore[list-item]
        with pytest.raises(AuditParseError, match="must be int, got bool"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# _build_heuristic_violations — severity mapping + reasoning encoding
# =============================================================================


class TestBuildHeuristicViolations:
    def test_severity_mapping_all_four_levels(self) -> None:
        # Construct findings at severities 1..4. Use conscious findings
        # so the decision-mode cap does not trigger, with unique
        # (heuristic, product_type) pairs.
        findings = [
            _finding(
                dimension="structure_navigation",
                heuristic=f"h{n}",
                product_type="functional",
                decision_mode="conscious",
                severity=n,
                evidence_source=["quotes"],
                evidence_quote_idxs=[0],
            )
            for n in (1, 2, 3, 4)
        ]
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=findings,
        )
        c = _cluster()
        violations = ux._build_heuristic_violations(payload, c)
        assert [v.severity for v in violations] == [3, 5, 7, 9]

    def test_reasoning_product_type_and_decision_mode_tags(self) -> None:
        c = _cluster(quotes=["q0", "q1"])
        f = _finding(
            dimension="skeleton_wireframe",
            heuristic="skeleton_does_not_honour_priority",
            product_type="hybrid",
            decision_mode="fiat",
            severity=3,
            evidence_source=["quotes", "ui_context"],
            evidence_quote_idxs=[0, 1],
        )
        # Lift dim cap so the fiat sev-3 cap is satisfied.
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["skeleton_wireframe"] = 2
        violations = ux._build_heuristic_violations(
            _happy_payload(dim_scores=scores, findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert reasoning.startswith("[skeleton_wireframe]")
        assert "product_type: hybrid" in reasoning
        assert "decision_mode: fiat" in reasoning
        assert "q[0]='q0'" in reasoning
        assert "q[1]='q1'" in reasoning
        assert "Nielsen 3 → anchored 7" in reasoning
        # Cooper / Osterwalder / Kahneman tags must NOT appear.
        assert "posture:" not in reasoning
        assert "tier:" not in reasoning
        assert "excise:" not in reasoning
        assert "mechanism:" not in reasoning
        assert "intent:" not in reasoning
        assert "blocks:" not in reasoning
        assert "tension:" not in reasoning
        assert "pattern:" not in reasoning

    def test_reasoning_conscious_finding_renders_tag(self) -> None:
        """Conscious findings render ``decision_mode: conscious``
        verbatim in the reasoning tag — the tag is always present, so
        a downstream reviewer can distinguish "not assessed" from
        "assessed as conscious"."""
        c = _cluster(quotes=["q0"])
        f = _finding(
            dimension="structure_navigation",
            heuristic="interaction_model_inconsistent",
            product_type="functional",
            decision_mode="conscious",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        violations = ux._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert "decision_mode: conscious" in reasoning
        assert "product_type: functional" in reasoning

    def test_reasoning_markup_only_uses_dash_placeholder(self) -> None:
        """A markup-only finding (no quotes) renders an em-dash
        placeholder rather than a q[idx]=... listing."""
        c = _cluster(quotes=["q0"])
        f = _finding(
            dimension="skeleton_wireframe",
            heuristic="interface_components_default_platform",
            product_type="functional",
            decision_mode="default",
            severity=2,
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        violations = ux._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert "Evidence (html):" in reasoning
        assert "—" in reasoning

    def test_reasoning_sources_tag_reflects_evidence_source(self) -> None:
        c = _cluster(quotes=["q0"])
        f = _finding(
            evidence_source=["quotes", "html"],
            evidence_quote_idxs=[0],
        )
        violations = ux._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        assert "Evidence (quotes+html):" in violations[0].reasoning

    def test_reasoning_uses_violation_and_recommendation(self) -> None:
        c = _cluster(quotes=["q0"])
        f = _finding(
            violation="Specific-violation-text",
            recommendation="Specific-recommendation-text",
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        violations = ux._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert "Specific-violation-text" in reasoning
        assert "Recommendation: Specific-recommendation-text" in reasoning

    def test_violation_severity_is_anchored_not_nielsen(self) -> None:
        """ADR-008: violation records always carry anchored severity
        (0..10 band). Confirms the remap happens exactly once."""
        c = _cluster()
        f = _finding(
            dimension="structure_navigation",
            heuristic="interaction_model_inconsistent",
            product_type="functional",
            decision_mode="conscious",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        violations = ux._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        assert violations[0].severity == 5  # Nielsen 2 → anchored 5


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
        assert outcome.verdict.relevant_heuristics == []

    def test_decision_mode_cap_rule_triggers_fallback_not_exception(self) -> None:
        """A payload that violates the decision-mode × dimension cap is
        a parse-level rejection → fallback, never a transport
        exception. Guards against the cross-finding check being hoisted
        out of ``parse_audit_response`` by a future refactor."""
        bad_f = _finding(
            dimension="skeleton_wireframe",
            heuristic="skeleton_does_not_honour_priority",
            product_type="hybrid",
            decision_mode="fiat",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}  # all 3 — violates fiat cap
        bad_payload = _happy_payload(dim_scores=scores, findings=[bad_f])
        client = FakeClient(default_response=json.dumps(bad_payload))
        outcome = asyncio.run(
            audit_cluster(_cluster(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "fallback"
        assert "decision_mode" in (outcome.reason or "")

    def test_duplicate_pair_triggers_fallback_not_exception(self) -> None:
        """(heuristic, product_type) duplicates are parse-level → fallback."""
        f1 = _finding(heuristic="featuritis", product_type="functional")
        f2 = _finding(
            heuristic="featuritis",
            product_type="functional",
            evidence_quote_idxs=[2, 3],
        )
        bad_payload = _happy_payload(findings=[f1, f2])
        client = FakeClient(default_response=json.dumps(bad_payload))
        outcome = asyncio.run(
            audit_cluster(_cluster(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "fallback"
        assert "product_type" in (outcome.reason or "") or "heuristic" in (
            outcome.reason or ""
        )

    def test_transport_failure_propagates(self) -> None:
        client = FakeClient(
            raise_on={"Streak-loss modal": RuntimeError("replay miss")}
        )
        c = _cluster()
        with pytest.raises(RuntimeError, match="replay miss"):
            asyncio.run(
                audit_cluster(c, client, skill_hash_value=skill_hash())
            )

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

    def test_cluster_with_html_and_screenshot_flows_into_prompt(self) -> None:
        """End-to-end: an enriched cluster's optional fields land in
        the prompt that reaches the client."""
        client = FakeClient(default_response=_happy_response_text())
        c = _cluster(
            html="<button>Submit</button>",
            screenshot_ref="data/artifacts/ui/x.png",
            ui_context="some modal",
        )
        asyncio.run(audit_cluster(c, client, skill_hash_value=skill_hash()))
        user_msg = client.calls[0]["user"]
        assert "<button>Submit</button>" in user_msg
        assert "<screenshot_ref>data/artifacts/ui/x.png</screenshot_ref>" in user_msg
        assert "<ui_context>some modal</ui_context>" in user_msg


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

    def test_parse_failure_is_a_fallback_not_a_failure(self) -> None:
        client = FakeClient(default_response="garbage")
        outcomes, failures = asyncio.run(audit_batch([_cluster()], client))
        assert failures == []
        assert outcomes[0].status == "fallback"


# =============================================================================
# build_provenance — Garrett-extended aggregates
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

    def test_dimension_score_totals_five_keys(self) -> None:
        scores = {k: 2 for k in DIMENSION_KEYS}
        outcomes = [
            self._outcome(
                cluster_id="c00",
                status="audited",
                payload=_happy_payload(dim_scores=scores, findings=[]),
            ),
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert set(prov["dimension_score_totals"]) == DIMENSION_KEYS
        assert all(v == 2 for v in prov["dimension_score_totals"].values())

    def test_product_type_histogram_all_four_keys(self) -> None:
        """Every product type in :data:`VALID_PRODUCT_TYPES` should
        appear as a key in the histogram, even at count 0 — so a
        reviewer never mistakes "no hybrid findings" for "hybrid wasn't
        evaluated"."""
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    dimension="structure_navigation",
                    heuristic="h1",
                    product_type="functional",
                    decision_mode="conscious",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="structure_navigation",
                    heuristic="h2",
                    product_type="informational",
                    decision_mode="conscious",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="structure_navigation",
                    heuristic="h3",
                    product_type="functional",
                    decision_mode="conscious",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
            ],
        )
        outcomes = [
            self._outcome(cluster_id="c00", status="audited", payload=payload)
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert set(prov["product_type_histogram"]) == VALID_PRODUCT_TYPES
        assert prov["product_type_histogram"]["functional"] == 2
        assert prov["product_type_histogram"]["informational"] == 1
        assert prov["product_type_histogram"]["hybrid"] == 0
        assert prov["product_type_histogram"]["not_applicable"] == 0

    def test_decision_mode_histogram_all_five_keys(self) -> None:
        """Every decision_mode in :data:`VALID_DECISION_MODES` should
        appear as a key in the histogram, even at count 0."""
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    dimension="structure_navigation",
                    heuristic="h1",
                    product_type="functional",
                    decision_mode="conscious",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="structure_navigation",
                    heuristic="h2",
                    product_type="informational",
                    decision_mode="conscious",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="structure_navigation",
                    heuristic="h3",
                    product_type="functional",
                    decision_mode="default",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
            ],
        )
        outcomes = [
            self._outcome(cluster_id="c00", status="audited", payload=payload)
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert set(prov["decision_mode_histogram"]) == VALID_DECISION_MODES
        assert prov["decision_mode_histogram"]["conscious"] == 2
        assert prov["decision_mode_histogram"]["default"] == 1
        assert prov["decision_mode_histogram"]["mimicry"] == 0
        assert prov["decision_mode_histogram"]["fiat"] == 0
        assert prov["decision_mode_histogram"]["not_applicable"] == 0

    def test_unconscious_decision_gauge(self) -> None:
        """Provenance exposes ``unconscious_decision_findings`` as a
        quick gauge of how often the unconscious-decision cap fires.
        Counts only ``default``/``mimicry``/``fiat`` — not
        ``not_applicable`` or ``conscious``."""
        # Dim scores are set so each finding's cap is satisfied
        # individually (sev-2 findings don't trigger the cap).
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    dimension="skeleton_wireframe",
                    heuristic="mimicry_a",
                    product_type="informational",
                    decision_mode="mimicry",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="skeleton_wireframe",
                    heuristic="default_a",
                    product_type="functional",
                    decision_mode="default",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="structure_navigation",
                    heuristic="clean_a",
                    product_type="informational",
                    decision_mode="conscious",
                    severity=1,
                    evidence_source=["ui_context"],
                    evidence_quote_idxs=[],
                ),
                _finding(
                    dimension="strategy_coherence",
                    heuristic="na_a",
                    product_type="not_applicable",
                    decision_mode="not_applicable",
                    severity=1,
                    evidence_source=["ui_context"],
                    evidence_quote_idxs=[],
                ),
            ],
        )
        outcomes = [
            self._outcome(cluster_id="c00", status="audited", payload=payload)
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        # Two unconscious findings (mimicry + default); conscious and
        # not_applicable do NOT count.
        assert prov["unconscious_decision_findings"] == 2

    def test_severity_histogram(self) -> None:
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    dimension="structure_navigation",
                    heuristic="h1",
                    product_type="functional",
                    decision_mode="conscious",
                    severity=1,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="structure_navigation",
                    heuristic="h2",
                    product_type="functional",
                    decision_mode="conscious",
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="structure_navigation",
                    heuristic="h3",
                    product_type="functional",
                    decision_mode="conscious",
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
            ],
        )
        outcomes = [
            self._outcome(cluster_id="c00", status="audited", payload=payload)
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

    def test_no_cooper_or_osterwalder_fields_in_provenance(self) -> None:
        """Defence-in-depth: provenance payload must not accidentally
        carry Cooper-, Kahneman-, or Osterwalder-flavoured aggregates.
        A hybrid provenance would break L5 ingestion heuristics that
        key on skill_id."""
        prov = build_provenance(
            outcomes=[
                self._outcome(
                    cluster_id="c00",
                    status="audited",
                    payload=_happy_payload(),
                )
            ],
            failures=[],
            model=MODEL,
        )
        # Cooper aggregates absent.
        assert "posture_histogram" not in prov
        assert "user_tier_histogram" not in prov
        assert "excise_type_histogram" not in prov
        assert "mixed_posture_findings" not in prov
        assert "excise_findings" not in prov
        # Kahneman aggregates absent.
        assert "intent_histogram" not in prov
        assert "mechanism_counts" not in prov
        # Accessibility aggregates absent.
        assert "wcag_level_histogram" not in prov
        # Osterwalder aggregates absent.
        assert "building_block_counts" not in prov
        assert "pattern_histogram" not in prov
        assert "tension_counts" not in prov
        # Garrett-specific aggregates present.
        assert "product_type_histogram" in prov
        assert "decision_mode_histogram" in prov
        assert "unconscious_decision_findings" in prov


# =============================================================================
# CLI — main
# =============================================================================


class TestMain:
    """End-to-end CLI tests. Keep lean so sandbox runs can skip the
    class via ``-k 'not TestMain'`` without losing core-logic coverage.
    """

    def test_cli_end_to_end_with_fakeclient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(ux, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [
                _cluster(cluster_id="cluster_00").model_dump(mode="json"),
                _cluster(
                    cluster_id="cluster_01",
                    label=(
                        "Settings IA mirrors org chart — structure plane "
                        "tracks departments, not user tasks"
                    ),
                    quotes=[
                        "I have to guess which department owns this feature",
                        "the menus are named after their teams",
                    ],
                ).model_dump(mode="json"),
            ],
        )
        output_path = data_dir / "verdicts.jsonl"
        native_path = data_dir / "verdicts.native.jsonl"

        fake = FakeClient(default_response=_happy_response_text())

        def _fake_client_ctor(**_kwargs: Any) -> FakeClient:
            return fake

        monkeypatch.setattr(ux, "Client", _fake_client_ctor)

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
                "l4-ux-test-run",
            ]
        )

        assert rc == 0
        assert output_path.exists()
        assert native_path.exists()

        verdicts_raw = [
            json.loads(line) for line in output_path.read_text().splitlines()
        ]
        assert len(verdicts_raw) == 2
        for row in verdicts_raw:
            AuditVerdict.model_validate(row)
            assert row["skill_id"] == SKILL_ID

        native_raw = [
            json.loads(line) for line in native_path.read_text().splitlines()
        ]
        assert len(native_raw) == 2
        assert {r["verdict_id"] for r in native_raw} == {
            f"{SKILL_ID}__cluster_00",
            f"{SKILL_ID}__cluster_01",
        }

        prov_path = output_path.with_suffix(".provenance.json")
        assert prov_path.exists()
        prov = json.loads(prov_path.read_text())
        assert prov["cluster_count"] == 2
        assert prov["audited_count"] == 2
        assert prov["skill_id"] == SKILL_ID
        # Garrett-specific aggregates land in the on-disk provenance.
        assert "product_type_histogram" in prov
        assert "decision_mode_histogram" in prov
        assert "unconscious_decision_findings" in prov

    def test_cli_empty_input_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(ux, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        output_path = data_dir / "out.jsonl"
        native_path = data_dir / "native.jsonl"

        monkeypatch.setattr(ux, "Client", lambda **_k: FakeClient())
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
