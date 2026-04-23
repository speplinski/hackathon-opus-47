"""Tests for ``auditable_design.layers.l4_audit_interaction_design``.

Structure mirrors ``test_l4_audit_business_alignment.py`` (the
Osterwalder sibling module) so a reader can diff the two and see where
Cooper's interaction-design contract diverges:

* **Four Cooper dimensions** — ``posture_platform_fit``,
  ``flow_excise``, ``idioms_learnability``, ``etiquette_forgiveness``;
  About Face groupings, not Canvas blocks and not POUR.
* **``posture`` + ``user_tier`` + ``excise_type`` findings, no
  ``building_blocks`` / ``tension`` / ``pattern`` and no ``mechanism`` /
  ``intent``** — each finding names exactly one posture (seven values
  including ``mixed`` for drift and ``not_applicable`` for cross-surface
  idiom claims), exactly one user tier (four values, Cooper's
  perpetual-intermediate primacy), and exactly one excise type (five
  values, four Cooper categories plus ``none``).
* **Quotes are *not* always required** — unlike Kahneman, an
  interaction-design finding can rest on ``html`` or ``ui_context``
  alone (modal-excise on a dialog, posture mismatch from markup). The
  parser enforces the bidirectional rule: ``"quotes"`` in
  ``evidence_source`` ↔ non-empty ``evidence_quote_idxs``.
* **Posture-mixed cap** — a finding with ``posture == "mixed"`` at
  severity ≥ 3 forces the enclosing dimension score to ``≤ 2``
  (behavioural drift is structural).
* **Excise cap** — a finding with ``excise_type != "none"`` at severity
  ≥ 3 forces the enclosing dimension score to ``≤ 2`` (Cooper's
  commensurate-effort principle).
* **flow_excise requires non-none excise** — every finding in the
  ``flow_excise`` dimension must carry a non-``none`` ``excise_type``.
  The reverse is not required: a posture finding may legitimately be
  an excise finding too.
* **No duplicate ``(heuristic, posture)`` pairs** — two findings may
  share a heuristic when postures differ, but not when both pairs are
  identical.

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
from auditable_design.layers import l4_audit_interaction_design as ia
from auditable_design.layers.l4_audit_interaction_design import (
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
    VALID_EXCISE_TYPES,
    VALID_POSTURES,
    VALID_USER_TIERS,
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
        "Mid-lesson paywall breaks focus and posture-shifts learning flow "
        "into a purchase decision"
    ),
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    centroid_ref: str = "l3_centroids.npy#0",
    ui_context: str | None = None,
    html: str | None = None,
    screenshot_ref: str | None = None,
) -> InsightCluster:
    """Build an InsightCluster with SKILL.md-aligned defaults.

    Defaults model the Duolingo mid-lesson paywall worked example from
    SKILL.md — the canonical posture-drift + modal-excise surface.
    """
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref=centroid_ref,
        representative_quotes=quotes
        or [
            "forced to quit mid-lesson if I don't pay",
            "modal interrupts my focus every 5 questions",
            "I lose my streak because the paywall popped up",
            "the app wants me to buy stuff when I'm trying to learn",
        ],
        ui_context=ui_context,
        html=html,
        screenshot_ref=screenshot_ref,
    )


def _finding(
    *,
    dimension: str = "flow_excise",
    heuristic: str = "modal_excise_interrupts_sovereign_flow",
    posture: str = "sovereign",
    user_tier: str = "intermediate",
    excise_type: str = "modal",
    violation: str = (
        "Modal dialog mid-lesson interrupts the sovereign learning "
        "posture with a purchase decision, stopping the proceedings "
        "with idiocy in Cooper's sense."
    ),
    severity: int = 3,
    evidence_source: list[str] | None = None,
    evidence_quote_idxs: list[int] | None = None,
    recommendation: str = (
        "Relocate monetisation modals to between-lesson boundaries; "
        "reserve in-lesson surface for the learning posture."
    ),
) -> dict[str, Any]:
    """Build one finding dict with SKILL.md-valid defaults.

    Defaults describe a plausible flow_excise modal-excise finding
    anchored on the first two quotes. Override individual fields to
    hit business-rule edges. Default severity 3 + excise_type "modal"
    pairs with a dim score of 2 in the default payload so the excise
    cap is respected; callers who lift the dim score must also change
    severity or excise_type.
    """
    return {
        "dimension": dimension,
        "heuristic": heuristic,
        "posture": posture,
        "user_tier": user_tier,
        "excise_type": excise_type,
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
        "Product declares a sovereign learning posture but drifts into "
        "transient-purchase mid-lesson, with modal excise compounding "
        "the interruption."
    ),
) -> dict[str, Any]:
    """Structurally-valid interaction-design payload.

    Defaults: neutral-to-low scores (2 on flow_excise to make the
    default excise-sev-3 finding consistent with the cap, 3 elsewhere),
    one modal-excise finding. Callers override whichever slice is
    under test.
    """
    scores = (
        dim_scores
        if dim_scores is not None
        else {
            "posture_platform_fit": 3,
            "flow_excise": 2,
            "idioms_learnability": 3,
            "etiquette_forgiveness": 3,
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
        assert SKILL_ID == "audit-interaction-design"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l4_audit_interaction_design"

    def test_default_model_is_sonnet(self) -> None:
        # Same rationale as the other L4 skills: Sonnet 4.6 is
        # reasoning-capable without Opus's budget. Shift to Opus
        # requires an ADR.
        assert MODEL == "claude-sonnet-4-6"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        # Interaction-design payloads carry per-finding posture +
        # user_tier + excise_type (short closed-set codes) plus
        # violation/recommendation — similar envelope to Osterwalder.
        # 6144 sits comfortably in the operating band.
        assert 4096 <= MAX_TOKENS <= 12288

    def test_dimension_keys_exactly_four_cooper(self) -> None:
        assert DIMENSION_KEYS == frozenset(
            {
                "posture_platform_fit",
                "flow_excise",
                "idioms_learnability",
                "etiquette_forgiveness",
            }
        )

    def test_valid_postures_closed_set_seven(self) -> None:
        assert VALID_POSTURES == frozenset(
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

    def test_valid_user_tiers_closed_set_four(self) -> None:
        assert VALID_USER_TIERS == frozenset(
            {
                "beginner",
                "intermediate",
                "expert",
                "all",
            }
        )

    def test_valid_excise_types_closed_set_five(self) -> None:
        assert VALID_EXCISE_TYPES == frozenset(
            {
                "navigational",
                "modal",
                "skeuomorphic",
                "stylistic",
                "none",
            }
        )

    def test_default_paths_under_data_derived(self) -> None:
        # Same input as the other L4 skills (shared L3b labeled
        # clusters); distinct outputs so L5 can ingest all five
        # skills as sibling layer-4 rows.
        assert DEFAULT_LABELED == Path("data/derived/l3b_labeled_clusters.jsonl")
        assert DEFAULT_VERDICTS == Path(
            "data/derived/l4_audit_interaction_design_verdicts.jsonl"
        )
        assert DEFAULT_NATIVE == Path(
            "data/derived/l4_audit_interaction_design_verdicts.native.jsonl"
        )

    def test_skill_hash_independent_of_siblings(self) -> None:
        # Defence in depth: editing any other L4 SKILL.md must not
        # alter interaction-design's cache key and vice versa.
        from auditable_design.layers import (
            l4_audit,
            l4_audit_accessibility,
            l4_audit_business_alignment,
            l4_audit_decision_psychology,
        )

        assert skill_hash() != l4_audit.skill_hash()
        assert skill_hash() != l4_audit_accessibility.skill_hash()
        assert skill_hash() != l4_audit_decision_psychology.skill_hash()
        assert skill_hash() != l4_audit_business_alignment.skill_hash()


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
        c = _cluster(ui_context="Duolingo mid-lesson paywall")
        msg = build_user_message(c)
        assert "<ui_context>Duolingo mid-lesson paywall</ui_context>" in msg
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

    def test_non_excise_finding_passes(self) -> None:
        """A posture-mismatch finding need not be an excise claim —
        excise_type='none' is legitimate outside ``flow_excise``."""
        f = _finding(
            dimension="posture_platform_fit",
            heuristic="posture_mismatch_on_desktop_sovereign",
            posture="sovereign",
            user_tier="intermediate",
            excise_type="none",
            severity=2,
            evidence_source=["ui_context"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
        )

    def test_markup_only_finding_passes(self) -> None:
        """Key difference from Kahneman: an interaction-design finding
        that cites only ``html`` / ``ui_context`` is legal — e.g. a
        confirmation-dialog modal-excise observation."""
        f = _finding(
            dimension="etiquette_forgiveness",
            heuristic="confirm_before_destructive",
            posture="transient",
            user_tier="all",
            excise_type="none",
            severity=2,
            evidence_source=["html", "ui_context"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
        )

    def test_not_applicable_posture_passes(self) -> None:
        """``not_applicable`` is a legal posture for cross-surface idiom
        or learnability claims that do not localise to a posture."""
        f = _finding(
            dimension="idioms_learnability",
            heuristic="custom_idiom_conflicts_with_platform",
            posture="not_applicable",
            user_tier="beginner",
            excise_type="none",
            severity=2,
            evidence_source=["ui_context"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
        )

    def test_all_seven_postures_pass_individually(self) -> None:
        """Defensive coverage: every closed-set posture value parses."""
        for posture in VALID_POSTURES:
            f = _finding(
                heuristic=f"h_{posture}",
                dimension="posture_platform_fit",
                posture=posture,
                user_tier="intermediate",
                excise_type="none",
                severity=2,
                evidence_source=["quotes"],
                evidence_quote_idxs=[0],
            )
            parse_audit_response(
                json.dumps(_happy_payload(
                    dim_scores={k: 3 for k in DIMENSION_KEYS},
                    findings=[f],
                )),
                n_quotes=5,
            )

    def test_all_four_user_tiers_pass_individually(self) -> None:
        for tier in VALID_USER_TIERS:
            f = _finding(
                heuristic=f"h_{tier}",
                dimension="idioms_learnability",
                posture="sovereign",
                user_tier=tier,
                excise_type="none",
                severity=2,
                evidence_source=["quotes"],
                evidence_quote_idxs=[0],
            )
            parse_audit_response(
                json.dumps(_happy_payload(
                    dim_scores={k: 3 for k in DIMENSION_KEYS},
                    findings=[f],
                )),
                n_quotes=5,
            )

    def test_all_five_excise_types_pass_individually(self) -> None:
        """Every closed-set excise type should be legal. ``none`` in a
        posture finding; the other four in a flow_excise finding. All
        at sev 2 to keep clear of the cap."""
        non_none = [e for e in VALID_EXCISE_TYPES if e != "none"]
        for i, excise in enumerate(non_none):
            f = _finding(
                heuristic=f"h_{excise}",
                dimension="flow_excise",
                posture="sovereign",
                user_tier="intermediate",
                excise_type=excise,
                severity=2,
                evidence_source=["quotes"],
                evidence_quote_idxs=[0],
            )
            parse_audit_response(
                json.dumps(_happy_payload(
                    dim_scores={k: 3 for k in DIMENSION_KEYS},
                    findings=[f],
                )),
                n_quotes=5,
            )
        # ``none`` legal in a non-flow_excise dimension.
        f_none = _finding(
            heuristic="h_none",
            dimension="posture_platform_fit",
            posture="sovereign",
            user_tier="intermediate",
            excise_type="none",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f_none],
            )),
            n_quotes=5,
        )

    def test_excise_sev_3_with_dim_2_passes(self) -> None:
        """The excise-cap rule is inclusive on the upper side:
        sev-3 excise with dim score 2 is legal (≤ 2 required)."""
        parse_audit_response(_happy_response_text(), n_quotes=5)

    def test_excise_sev_3_with_dim_1_passes(self) -> None:
        """Floor of the allowed band — dim score 1 passes the ≤ 2 rule."""
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["flow_excise"] = 1
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores)),
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
                "posture_platform_fit": 3,
                "flow_excise": 2,
                "idioms_learnability": 3,
                # etiquette_forgiveness missing
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

    def test_extra_dimension_key(self) -> None:
        payload = _happy_payload(
            dim_scores={**{k: 3 for k in DIMENSION_KEYS}, "extra": 3}
        )
        with pytest.raises(AuditParseError, match="unexpected keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_score_out_of_range(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["posture_platform_fit"] = 7
        with pytest.raises(AuditParseError, match=r"out of \{1,2,3,4,5\}"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )

    def test_bool_rejected_as_score(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["posture_platform_fit"] = True  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="must be int, got bool"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )


# =============================================================================
# parse_audit_response — findings structural
# =============================================================================


class TestParseAuditResponseFindingsStructural:
    def test_missing_posture_rejected(self) -> None:
        f = _finding()
        f.pop("posture")
        with pytest.raises(AuditParseError, match=r"findings\[0\] missing keys"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_missing_user_tier_rejected(self) -> None:
        f = _finding()
        f.pop("user_tier")
        with pytest.raises(AuditParseError, match=r"findings\[0\] missing keys"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_missing_excise_type_rejected(self) -> None:
        f = _finding()
        f.pop("excise_type")
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
        payload is fed into the interaction-design parser."""
        f = _finding()
        f.pop("posture")
        f.pop("user_tier")
        f.pop("excise_type")
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
        f.pop("posture")
        f.pop("user_tier")
        f.pop("excise_type")
        f["building_blocks"] = ["vp"]
        f["tension"] = []
        f["pattern"] = "freemium"
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
# parse_audit_response — posture closed set
# =============================================================================


class TestParseAuditResponsePosture:
    def test_invalid_posture_rejected(self) -> None:
        f = _finding(posture="ambient")
        with pytest.raises(AuditParseError, match=r"posture='ambient' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_posture_non_string_rejected(self) -> None:
        f = _finding()
        f["posture"] = None  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="posture must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_posture_empty_string_rejected(self) -> None:
        f = _finding(posture="")
        with pytest.raises(AuditParseError, match=r"posture='' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_posture_list_rejected(self) -> None:
        """``posture`` is a single string, not a list — this guards
        against a model that copies the Osterwalder ``building_blocks``
        shape onto the Cooper finding."""
        f = _finding()
        f["posture"] = ["sovereign"]  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="posture must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — user_tier closed set
# =============================================================================


class TestParseAuditResponseUserTier:
    def test_invalid_user_tier_rejected(self) -> None:
        f = _finding(user_tier="novice")
        with pytest.raises(AuditParseError, match=r"user_tier='novice' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_user_tier_non_string_rejected(self) -> None:
        f = _finding()
        f["user_tier"] = 42  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="user_tier must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — excise_type closed set
# =============================================================================


class TestParseAuditResponseExciseType:
    def test_invalid_excise_type_rejected(self) -> None:
        f = _finding(excise_type="cognitive")
        with pytest.raises(
            AuditParseError, match=r"excise_type='cognitive' not in"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_excise_type_non_string_rejected(self) -> None:
        f = _finding()
        f["excise_type"] = None  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="excise_type must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_excise_type_bool_rejected(self) -> None:
        """JSON-level guard: ``True`` must not sneak past the string
        typing check. (Python: ``isinstance(True, str)`` is False — this
        is less of a footgun than the severity-bool case — but keep the
        guard symmetric.)"""
        f = _finding()
        f["excise_type"] = True  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="excise_type must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — posture=mixed × dimension cap (cross-finding rule)
# =============================================================================


class TestParseAuditResponsePostureCap:
    def test_posture_mixed_sev3_forces_dim_score_cap(self) -> None:
        """Cross-finding rule: posture=="mixed" at sev ≥ 3 →
        dim score ≤ 2. Use a non-excise finding so the excise cap does
        not pre-empt the posture cap."""
        f = _finding(
            dimension="posture_platform_fit",
            heuristic="posture_drift_sov_to_trans",
            posture="mixed",
            user_tier="intermediate",
            excise_type="none",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}  # offending: > 2 with mixed sev 3
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError,
            match=r"posture='mixed'.*at severity 3.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_posture_mixed_sev4_forces_dim_score_cap(self) -> None:
        """Same rule at the top of the severity range — guards against
        an off-by-one in the ``>= 3`` threshold."""
        f = _finding(
            dimension="posture_platform_fit",
            heuristic="posture_drift_sov_to_trans",
            posture="mixed",
            user_tier="intermediate",
            excise_type="none",
            severity=4,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError,
            match=r"posture='mixed'.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_posture_non_mixed_sev3_does_not_force_dim_cap(self) -> None:
        """The posture-cap applies only to posture=='mixed'. A sev-3
        sovereign finding + dim score 3 is legal (if no excise)."""
        f = _finding(
            dimension="posture_platform_fit",
            heuristic="sovereign_on_wrong_surface",
            posture="sovereign",
            user_tier="intermediate",
            excise_type="none",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_posture_mixed_sev2_does_not_force_dim_cap(self) -> None:
        """Cap triggers at sev ≥ 3; a sev-2 mixed + dim score 3 is
        legal (defends against an off-by-one in the ``>= 3`` check)."""
        f = _finding(
            dimension="posture_platform_fit",
            heuristic="minor_posture_drift",
            posture="mixed",
            user_tier="intermediate",
            excise_type="none",
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
# parse_audit_response — excise × dimension cap (cross-finding rule)
# =============================================================================


class TestParseAuditResponseExciseCap:
    def test_excise_sev3_forces_dim_score_cap(self) -> None:
        """Cross-finding rule: excise_type != 'none' at sev ≥ 3 →
        dim score ≤ 2."""
        scores = {k: 3 for k in DIMENSION_KEYS}  # offending: flow_excise > 2 with sev-3 modal
        payload = _happy_payload(dim_scores=scores, findings=[_finding()])
        with pytest.raises(
            AuditParseError,
            match=r"excise_type='modal'.*at severity 3.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_excise_sev4_forces_dim_score_cap(self) -> None:
        """Same rule at the top of the severity range."""
        f = _finding(severity=4)
        scores = {k: 3 for k in DIMENSION_KEYS}
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError,
            match=r"excise_type='modal'.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_excise_none_sev3_does_not_force_dim_cap(self) -> None:
        """The excise-cap applies only to excise_type != 'none'. A
        sev-3 non-excise finding + dim score 3 is legal (if posture
        is not mixed)."""
        f = _finding(
            dimension="etiquette_forgiveness",
            heuristic="cannot_undo_send",
            posture="transient",
            user_tier="all",
            excise_type="none",
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_excise_sev2_does_not_force_dim_cap(self) -> None:
        """Cap triggers at sev ≥ 3; a sev-2 excise finding + dim
        score 3 is legal (off-by-one guard)."""
        f = _finding(severity=2)
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_all_four_non_none_excise_types_trigger_cap(self) -> None:
        """Every non-'none' excise_type at sev ≥ 3 should trip the cap
        — the rule is 'excise != none', not 'excise == modal'."""
        for excise in ("navigational", "modal", "skeuomorphic", "stylistic"):
            f = _finding(
                dimension="flow_excise",
                heuristic=f"h_{excise}_sev3",
                posture="sovereign",
                user_tier="intermediate",
                excise_type=excise,
                severity=3,
                evidence_source=["quotes"],
                evidence_quote_idxs=[0],
            )
            scores = {k: 3 for k in DIMENSION_KEYS}  # offending
            payload = _happy_payload(dim_scores=scores, findings=[f])
            with pytest.raises(
                AuditParseError,
                match=rf"excise_type='{excise}'.*forces dimension",
            ):
                parse_audit_response(json.dumps(payload), n_quotes=5)


# =============================================================================
# parse_audit_response — flow_excise requires non-none excise_type
# =============================================================================


class TestParseAuditResponseFlowExciseRequiresExcise:
    def test_flow_excise_with_none_excise_rejected(self) -> None:
        """Every finding in the flow_excise dimension must name a
        non-'none' excise_type (SKILL.md rule: flow_excise is the
        dimension where excise claims live)."""
        f = _finding(
            dimension="flow_excise",
            heuristic="something_about_flow",
            posture="sovereign",
            user_tier="intermediate",
            excise_type="none",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        with pytest.raises(
            AuditParseError,
            match=r"dimension 'flow_excise'.*excise_type='none'",
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_non_flow_excise_with_none_excise_passes(self) -> None:
        """The reverse rule is not enforced: a posture/idiom/etiquette
        finding may legitimately be excise_type='none'."""
        f = _finding(
            dimension="etiquette_forgiveness",
            heuristic="polite_confirmation",
            posture="transient",
            user_tier="all",
            excise_type="none",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_non_flow_excise_with_non_none_excise_passes(self) -> None:
        """A posture finding that also manifests as modal excise is
        legitimate — the flow_excise rule polices only the forward
        direction (flow_excise → non-none), not the reverse."""
        f = _finding(
            dimension="posture_platform_fit",
            heuristic="modal_posture_mismatch",
            posture="sovereign",
            user_tier="intermediate",
            excise_type="modal",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )


# =============================================================================
# parse_audit_response — no duplicate (heuristic, posture) pairs
# =============================================================================


class TestParseAuditResponseDuplicates:
    def test_duplicate_pair_rejected(self) -> None:
        f1 = _finding(heuristic="modal_excise_interrupts_flow")
        f2 = _finding(
            heuristic="modal_excise_interrupts_flow",
            # Same heuristic AND same posture — duplicate pair.
            evidence_quote_idxs=[2, 3],
        )
        # Both findings are sev-3 modal excise; lift scores so the cap
        # is satisfied and duplicate-check fires.
        scores = {k: 2 for k in DIMENSION_KEYS}
        payload = _happy_payload(dim_scores=scores, findings=[f1, f2])
        with pytest.raises(
            AuditParseError,
            match=r"repeats \(heuristic, posture\) pair",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_same_heuristic_different_posture_passes(self) -> None:
        """Two findings may share ``heuristic`` if they name different
        postures — SKILL.md guards against *pair* duplicates."""
        f1 = _finding(
            heuristic="modal_excise_interrupts_flow",
            posture="sovereign",
        )
        f2 = _finding(
            heuristic="modal_excise_interrupts_flow",
            posture="transient",
            evidence_quote_idxs=[2, 3],
        )
        # Both findings are sev-3 modal excise → need cap dim ≤ 2.
        scores = {k: 2 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f1, f2])),
            n_quotes=5,
        )

    def test_same_heuristic_mixed_vs_sovereign_passes(self) -> None:
        """A mixed-posture finding and a sovereign-posture finding may
        share a heuristic — postures differ so the pair is unique."""
        f1 = _finding(
            dimension="posture_platform_fit",
            heuristic="posture_issue_at_paywall",
            posture="mixed",
            user_tier="intermediate",
            excise_type="none",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        f2 = _finding(
            dimension="posture_platform_fit",
            heuristic="posture_issue_at_paywall",
            posture="sovereign",
            user_tier="intermediate",
            excise_type="none",
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
        """The interaction-design relaxation: markup-only findings
        with no quotes and no idxs are legal (key contrast with
        Kahneman)."""
        f = _finding(
            dimension="etiquette_forgiveness",
            heuristic="confirm_before_destructive",
            posture="transient",
            user_tier="all",
            excise_type="none",
            severity=2,
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
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
        # Construct findings at severities 1..4. Use non-excise
        # findings in non-flow_excise dimensions to sidestep the
        # excise/posture/flow caps, and unique (heuristic, posture)
        # pairs.
        findings = [
            _finding(
                dimension="idioms_learnability",
                heuristic=f"h{n}",
                posture="sovereign",
                user_tier="intermediate",
                excise_type="none",
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
        violations = ia._build_heuristic_violations(payload, c)
        assert [v.severity for v in violations] == [3, 5, 7, 9]

    def test_reasoning_posture_tier_and_excise_tags(self) -> None:
        c = _cluster(quotes=["q0", "q1"])
        f = _finding(
            dimension="flow_excise",
            heuristic="modal_excise_interrupts_flow",
            posture="sovereign",
            user_tier="intermediate",
            excise_type="modal",
            severity=3,
            evidence_source=["quotes", "ui_context"],
            evidence_quote_idxs=[0, 1],
        )
        violations = ia._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert reasoning.startswith("[flow_excise]")
        assert "posture: sovereign" in reasoning
        assert "tier: intermediate" in reasoning
        assert "excise: modal" in reasoning
        assert "q[0]='q0'" in reasoning
        assert "q[1]='q1'" in reasoning
        assert "Nielsen 3 → anchored 7" in reasoning
        # Osterwalder and Kahneman tags must NOT appear — this is the
        # Cooper skill.
        assert "mechanism:" not in reasoning
        assert "intent:" not in reasoning
        assert "blocks:" not in reasoning
        assert "tension:" not in reasoning
        assert "pattern:" not in reasoning

    def test_reasoning_non_excise_renders_excise_none(self) -> None:
        """Non-excise findings render ``excise: none`` verbatim in the
        reasoning tag."""
        c = _cluster(quotes=["q0"])
        f = _finding(
            dimension="idioms_learnability",
            heuristic="custom_idiom_breaks_platform",
            posture="not_applicable",
            user_tier="beginner",
            excise_type="none",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        violations = ia._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert "excise: none" in reasoning
        assert "posture: not_applicable" in reasoning
        assert "tier: beginner" in reasoning

    def test_reasoning_markup_only_uses_dash_placeholder(self) -> None:
        """A markup-only finding (no quotes) renders an em-dash
        placeholder rather than a q[idx]=... listing."""
        c = _cluster(quotes=["q0"])
        f = _finding(
            dimension="etiquette_forgiveness",
            heuristic="confirm_before_destructive",
            posture="transient",
            user_tier="all",
            excise_type="none",
            severity=2,
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        violations = ia._build_heuristic_violations(
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
        violations = ia._build_heuristic_violations(
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
        violations = ia._build_heuristic_violations(
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
            dimension="idioms_learnability",
            heuristic="custom_idiom_breaks_platform",
            posture="not_applicable",
            user_tier="beginner",
            excise_type="none",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        violations = ia._build_heuristic_violations(
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

    def test_excise_cap_rule_triggers_fallback_not_exception(self) -> None:
        """A payload that violates the excise × dimension coupling is
        a parse-level rejection → fallback, never a transport
        exception. Guards against the cross-finding check being hoisted
        out of ``parse_audit_response`` by a future refactor."""
        scores = {k: 3 for k in DIMENSION_KEYS}  # all 3 — violates excise cap
        bad_payload = _happy_payload(dim_scores=scores, findings=[_finding()])
        client = FakeClient(default_response=json.dumps(bad_payload))
        outcome = asyncio.run(
            audit_cluster(_cluster(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "fallback"
        assert "excise_type" in (outcome.reason or "")

    def test_flow_excise_rule_triggers_fallback_not_exception(self) -> None:
        """flow_excise-with-none-excise is a parse-level rejection → fallback."""
        bad_f = _finding(
            dimension="flow_excise",
            heuristic="wrong_excise_label",
            posture="sovereign",
            user_tier="intermediate",
            excise_type="none",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        bad_payload = _happy_payload(findings=[bad_f])
        client = FakeClient(default_response=json.dumps(bad_payload))
        outcome = asyncio.run(
            audit_cluster(_cluster(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "fallback"
        assert "flow_excise" in (outcome.reason or "")

    def test_transport_failure_propagates(self) -> None:
        client = FakeClient(
            raise_on={"Mid-lesson paywall": RuntimeError("replay miss")}
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
# build_provenance — Cooper-extended aggregates
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

    def test_dimension_score_totals_four_keys(self) -> None:
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

    def test_posture_histogram_all_seven_keys(self) -> None:
        """Every posture in :data:`VALID_POSTURES` should appear as a
        key in the histogram, even at count 0 — so a reviewer never
        mistakes "no daemonic findings" for "daemonic wasn't evaluated"."""
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h1",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="none",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h2",
                    posture="transient",
                    user_tier="intermediate",
                    excise_type="none",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h3",
                    posture="sovereign",
                    user_tier="beginner",
                    excise_type="none",
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
        assert set(prov["posture_histogram"]) == VALID_POSTURES
        assert prov["posture_histogram"]["sovereign"] == 2
        assert prov["posture_histogram"]["transient"] == 1
        assert prov["posture_histogram"]["daemonic"] == 0
        assert prov["posture_histogram"]["mixed"] == 0
        assert prov["posture_histogram"]["not_applicable"] == 0

    def test_user_tier_histogram_all_four_keys(self) -> None:
        """Every tier in :data:`VALID_USER_TIERS` should appear as a
        key in the histogram, even at count 0."""
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h1",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="none",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h2",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="none",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h3",
                    posture="sovereign",
                    user_tier="beginner",
                    excise_type="none",
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
        assert set(prov["user_tier_histogram"]) == VALID_USER_TIERS
        assert prov["user_tier_histogram"]["intermediate"] == 2
        assert prov["user_tier_histogram"]["beginner"] == 1
        assert prov["user_tier_histogram"]["expert"] == 0
        assert prov["user_tier_histogram"]["all"] == 0

    def test_excise_type_histogram_all_five_keys(self) -> None:
        """Every excise type in :data:`VALID_EXCISE_TYPES` should appear
        as a key in the histogram, even at count 0."""
        payload = _happy_payload(
            dim_scores={k: 2 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    dimension="flow_excise",
                    heuristic="h1",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="modal",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="flow_excise",
                    heuristic="h2",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="modal",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="flow_excise",
                    heuristic="h3",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="navigational",
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
        assert set(prov["excise_type_histogram"]) == VALID_EXCISE_TYPES
        assert prov["excise_type_histogram"]["modal"] == 2
        assert prov["excise_type_histogram"]["navigational"] == 1
        assert prov["excise_type_histogram"]["skeuomorphic"] == 0
        assert prov["excise_type_histogram"]["stylistic"] == 0
        assert prov["excise_type_histogram"]["none"] == 0

    def test_mixed_posture_and_excise_gauges(self) -> None:
        """Provenance exposes ``mixed_posture_findings`` and
        ``excise_findings`` as quick gauges of how often drift and
        excise fire."""
        payload = _happy_payload(
            dim_scores={
                "posture_platform_fit": 2,
                "flow_excise": 2,
                "idioms_learnability": 3,
                "etiquette_forgiveness": 3,
            },
            findings=[
                _finding(
                    dimension="posture_platform_fit",
                    heuristic="mixed_a",
                    posture="mixed",
                    user_tier="intermediate",
                    excise_type="none",
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="flow_excise",
                    heuristic="excise_a",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="modal",
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="idioms_learnability",
                    heuristic="clean_a",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="none",
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
        assert prov["mixed_posture_findings"] == 1
        assert prov["excise_findings"] == 1

    def test_severity_histogram(self) -> None:
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h1",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="none",
                    severity=1,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h2",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="none",
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    dimension="idioms_learnability",
                    heuristic="h3",
                    posture="sovereign",
                    user_tier="intermediate",
                    excise_type="none",
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

    def test_no_kahneman_or_osterwalder_fields_in_provenance(self) -> None:
        """Defence-in-depth: provenance payload must not accidentally
        carry Kahneman- or Osterwalder-flavoured aggregates. A hybrid
        provenance would break L5 ingestion heuristics that key on
        skill_id."""
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
        # Kahneman aggregates absent.
        assert "intent_histogram" not in prov
        assert "mechanism_counts" not in prov
        # Accessibility aggregates absent.
        assert "wcag_level_histogram" not in prov
        # Osterwalder aggregates absent.
        assert "building_block_counts" not in prov
        assert "pattern_histogram" not in prov
        assert "tension_counts" not in prov
        # Cooper-specific aggregates present.
        assert "posture_histogram" in prov
        assert "user_tier_histogram" in prov
        assert "excise_type_histogram" in prov
        assert "mixed_posture_findings" in prov
        assert "excise_findings" in prov


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
        monkeypatch.setattr(ia, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [
                _cluster(cluster_id="cluster_00").model_dump(mode="json"),
                _cluster(
                    cluster_id="cluster_01",
                    label=(
                        "Settings buried under three tabs — navigational "
                        "excise for sovereign configuration surface"
                    ),
                    quotes=[
                        "I can never find the right setting",
                        "why is dark mode three menus deep",
                    ],
                ).model_dump(mode="json"),
            ],
        )
        output_path = data_dir / "verdicts.jsonl"
        native_path = data_dir / "verdicts.native.jsonl"

        fake = FakeClient(default_response=_happy_response_text())

        def _fake_client_ctor(**_kwargs: Any) -> FakeClient:
            return fake

        monkeypatch.setattr(ia, "Client", _fake_client_ctor)

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
                "l4-ia-test-run",
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
        # Cooper-specific aggregates land in the on-disk provenance.
        assert "posture_histogram" in prov
        assert "user_tier_histogram" in prov
        assert "excise_type_histogram" in prov
        assert "mixed_posture_findings" in prov
        assert "excise_findings" in prov

    def test_cli_empty_input_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(ia, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        output_path = data_dir / "out.jsonl"
        native_path = data_dir / "native.jsonl"

        monkeypatch.setattr(ia, "Client", lambda **_k: FakeClient())
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
