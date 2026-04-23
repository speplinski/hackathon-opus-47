"""Tests for ``auditable_design.layers.l4_audit_business_alignment``.

Structure mirrors ``test_l4_audit_decision_psychology.py`` (the
Kahneman sibling module) so a reader can diff the two and see exactly
where business-alignment's contract diverges from the other L4 skills:

* **Four Osterwalder dimensions** — ``value_delivery``,
  ``revenue_relationships``, ``infrastructure_fit``,
  ``pattern_coherence``; Canvas-block groupings, not POUR and not
  Kahneman's dual-process cut.
* **``building_blocks`` + ``tension`` + ``pattern`` findings, no
  ``mechanism`` / ``intent``** — each finding names one-or-more of the
  nine Canvas codes, optionally a two-block tension pair in
  lexicographic order, and exactly one business-model pattern from a
  closed set.
* **Quotes are *not* always required** — unlike Kahneman, a business-
  alignment finding can rest on ``html`` or ``ui_context`` alone
  (SKILL.md: pricing-page defects, KP/KR observations from markup).
  The parser enforces the bidirectional rule: ``"quotes"`` in
  ``evidence_source`` ↔ non-empty ``evidence_quote_idxs``.
* **Tension discipline** — a finding with non-empty ``tension`` at
  ``severity ≥ 3`` forces the enclosing dimension score to ``≤ 2``
  (cross-finding rule, analogous to Kahneman's dark-pattern cap).
  Additionally, ``tension`` must be either ``[]`` or a two-element
  lex-ordered list of distinct blocks present in ``building_blocks``.
* **No duplicate ``(heuristic, tension)`` pairs** — two findings may
  share a heuristic when tensions differ or one is single-block, but
  not when both pairs are identical.

Strategy
--------
Every test that would otherwise exercise Claude uses an in-process
:class:`FakeClient` with scripted responses — same pattern as the
Kahneman sibling module. No network, no real replay log; whole file
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
from auditable_design.layers import l4_audit_business_alignment as ba
from auditable_design.layers.l4_audit_business_alignment import (
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
    VALID_BLOCKS,
    VALID_PATTERNS,
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
        "Energy-and-streak paywall fragments the learning promise with "
        "pay-or-wait choice mid-lesson"
    ),
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    centroid_ref: str = "l3_centroids.npy#0",
    ui_context: str | None = None,
    html: str | None = None,
    screenshot_ref: str | None = None,
) -> InsightCluster:
    """Build an InsightCluster with SKILL.md-aligned defaults.

    Defaults model the Duolingo energy/streak paywall worked example
    from SKILL.md — the canonical VP↔R$ tension surface.
    """
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref=centroid_ref,
        representative_quotes=quotes
        or [
            "If you don't agree to pay mid-lesson, you have to quit mid-lesson",
            "the VP says 'free, fun, effective' but you hit a paywall every 5 questions",
            "energy system forces me to pay or watch ads",
            "I have Super and still get ads for other paid courses mid-lesson",
        ],
        ui_context=ui_context,
        html=html,
        screenshot_ref=screenshot_ref,
    )


def _finding(
    *,
    dimension: str = "revenue_relationships",
    heuristic: str = "monetisation_interrupts_value",
    building_blocks: list[str] | None = None,
    tension: list[str] | None = None,
    pattern: str = "freemium",
    violation: str = (
        "Revenue Stream triggers at the moment the Customer Relationship "
        "is delivering its core value (mid-lesson), making monetisation "
        "synonymous with value-delivery interruption."
    ),
    severity: int = 3,
    evidence_source: list[str] | None = None,
    evidence_quote_idxs: list[int] | None = None,
    recommendation: str = (
        "Relocate monetisation triggers to between-lesson boundaries so "
        "that paying users experience additive value."
    ),
) -> dict[str, Any]:
    """Build one finding dict with SKILL.md-valid defaults.

    Defaults describe a plausible VP↔R$ / CR↔R$ tension anchored on the
    first two quotes. Override individual fields to hit business-rule
    edges. The defaults deliberately put a sev-3 tension on
    ``revenue_relationships`` so a parser that silently drops the
    tension × dimension coupling will fail the default ``_happy_payload``
    shape (which pairs this finding with a dimension score of 2).
    """
    return {
        "dimension": dimension,
        "heuristic": heuristic,
        "building_blocks": (
            building_blocks
            if building_blocks is not None
            else ["cr", "r_dollar", "vp"]
        ),
        "tension": tension if tension is not None else ["cr", "r_dollar"],
        "pattern": pattern,
        "violation": violation,
        "severity": severity,
        "evidence_source": evidence_source
        if evidence_source is not None
        else ["quotes", "ui_context"],
        "evidence_quote_idxs": evidence_quote_idxs
        if evidence_quote_idxs is not None
        else [0, 2],
        "recommendation": recommendation,
    }


def _happy_payload(
    *,
    dim_scores: dict[str, int] | None = None,
    findings: list[dict[str, Any]] | None = None,
    summary: str = (
        "Product declares a freemium-with-conversion VP in marketing but "
        "implements freemium-with-forced-continuity in-product; dominant "
        "tension is VP↔R$."
    ),
) -> dict[str, Any]:
    """Structurally-valid business-alignment payload.

    Defaults: neutral-to-low scores (2 on revenue_relationships to make
    the default tension-sev-3 finding consistent, 3 elsewhere), one
    CR↔R$ tension finding. Callers override whichever slice is under
    test.
    """
    scores = (
        dim_scores
        if dim_scores is not None
        else {
            "value_delivery": 3,
            "revenue_relationships": 2,
            "infrastructure_fit": 3,
            "pattern_coherence": 3,
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
        assert SKILL_ID == "audit-business-alignment"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l4_audit_business_alignment"

    def test_default_model_is_sonnet(self) -> None:
        # Same rationale as the other L4 skills: Sonnet 4.6 is
        # reasoning-capable without Opus's budget. Shift to Opus
        # requires an ADR.
        assert MODEL == "claude-sonnet-4-6"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        # Business-alignment payloads carry per-finding building_blocks
        # + tension + pattern (all short closed-set codes) — similar
        # envelope to Kahneman. 6144 sits comfortably in the operating
        # band.
        assert 4096 <= MAX_TOKENS <= 12288

    def test_dimension_keys_exactly_four_osterwalder(self) -> None:
        assert DIMENSION_KEYS == frozenset(
            {
                "value_delivery",
                "revenue_relationships",
                "infrastructure_fit",
                "pattern_coherence",
            }
        )

    def test_valid_blocks_closed_set_nine_canvas_codes(self) -> None:
        assert VALID_BLOCKS == frozenset(
            {
                "cs",
                "vp",
                "ch",
                "cr",
                "r_dollar",
                "kr",
                "ka",
                "kp",
                "c_dollar",
            }
        )

    def test_valid_patterns_closed_set(self) -> None:
        assert VALID_PATTERNS == frozenset(
            {
                "multi_sided",
                "freemium",
                "long_tail",
                "subscription",
                "unbundled",
                "open",
                "none_identified",
            }
        )

    def test_default_paths_under_data_derived(self) -> None:
        # Same input as the other L4 skills (shared L3b labeled
        # clusters); distinct outputs so L5 can ingest all four
        # skills as sibling layer-4 rows.
        assert DEFAULT_LABELED == Path("data/derived/l3b_labeled_clusters.jsonl")
        assert DEFAULT_VERDICTS == Path(
            "data/derived/l4_audit_business_alignment_verdicts.jsonl"
        )
        assert DEFAULT_NATIVE == Path(
            "data/derived/l4_audit_business_alignment_verdicts.native.jsonl"
        )

    def test_skill_hash_independent_of_siblings(self) -> None:
        # Defence in depth: editing any other L4 SKILL.md must not
        # alter business-alignment's cache key and vice versa.
        from auditable_design.layers import (
            l4_audit,
            l4_audit_accessibility,
            l4_audit_decision_psychology,
        )

        assert skill_hash() != l4_audit.skill_hash()
        assert skill_hash() != l4_audit_accessibility.skill_hash()
        assert skill_hash() != l4_audit_decision_psychology.skill_hash()


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
        reruns and matches the Kahneman sibling byte-for-byte.
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

    def test_single_block_finding_passes(self) -> None:
        """Single-block findings (``tension == []``) are the typical
        cosmetic / minor case — no cross-block conflict."""
        f = _finding(
            heuristic="value_prop_illegible",
            building_blocks=["vp"],
            tension=[],
            pattern="freemium",
            severity=2,
            evidence_source=["quotes", "ui_context"],
            evidence_quote_idxs=[1],
        )
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
        )

    def test_markup_only_finding_passes(self) -> None:
        """Key difference from Kahneman: a business-alignment finding
        that cites only ``html`` / ``ui_context`` is legal — e.g. a
        pricing-page structural observation."""
        f = _finding(
            heuristic="pricing_not_visible",
            building_blocks=["r_dollar"],
            tension=[],
            pattern="freemium",
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

    def test_pattern_none_identified_passes(self) -> None:
        """``none_identified`` is a legal pattern when the evidence is
        thin or genuinely ambiguous (SKILL.md: don't guess)."""
        f = _finding(
            heuristic="pattern_absent_and_needed",
            building_blocks=["vp"],
            tension=[],
            pattern="none_identified",
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

    def test_all_seven_patterns_pass_individually(self) -> None:
        """Defensive coverage: every closed-set pattern value parses."""
        for pattern in VALID_PATTERNS:
            f = _finding(
                heuristic=f"h_{pattern}",
                building_blocks=["vp"],
                tension=[],
                pattern=pattern,
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

    def test_all_nine_blocks_pass_as_building_blocks(self) -> None:
        """Every closed-set Canvas code should be legal as a
        single-block finding."""
        for block in VALID_BLOCKS:
            f = _finding(
                heuristic=f"h_{block}",
                building_blocks=[block],
                tension=[],
                pattern="none_identified",
                severity=1,
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

    def test_tension_sev_3_with_dim_2_passes(self) -> None:
        """The dimension-cap rule is inclusive on the upper side:
        sev-3 tension with dim score 2 is legal (≤ 2 required)."""
        parse_audit_response(_happy_response_text(), n_quotes=5)

    def test_tension_sev_3_with_dim_1_passes(self) -> None:
        """Floor of the allowed band — dim score 1 passes the ≤ 2 rule."""
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["revenue_relationships"] = 1
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
                "value_delivery": 3,
                "revenue_relationships": 3,
                "infrastructure_fit": 3,
                # pattern_coherence missing
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

    def test_extra_dimension_key(self) -> None:
        payload = _happy_payload(
            dim_scores={**{k: 3 for k in DIMENSION_KEYS}, "extra": 3}
        )
        with pytest.raises(AuditParseError, match="unexpected keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_score_out_of_range(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["value_delivery"] = 7
        with pytest.raises(AuditParseError, match=r"out of \{1,2,3,4,5\}"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )

    def test_bool_rejected_as_score(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["value_delivery"] = True  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="must be int, got bool"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )


# =============================================================================
# parse_audit_response — findings structural
# =============================================================================


class TestParseAuditResponseFindingsStructural:
    def test_missing_building_blocks_rejected(self) -> None:
        f = _finding()
        f.pop("building_blocks")
        with pytest.raises(AuditParseError, match=r"findings\[0\] missing keys"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_missing_tension_rejected(self) -> None:
        f = _finding()
        f.pop("tension")
        with pytest.raises(AuditParseError, match=r"findings\[0\] missing keys"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_missing_pattern_rejected(self) -> None:
        f = _finding()
        f.pop("pattern")
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
        payload is fed into the business-alignment parser."""
        f = _finding()
        f.pop("building_blocks")
        f.pop("tension")
        f.pop("pattern")
        f["mechanism"] = "loss_aversion"
        f["intent"] = "dark_pattern"
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
# parse_audit_response — building_blocks closed set + non-empty + no-dupes
# =============================================================================


class TestParseAuditResponseBuildingBlocks:
    def test_empty_building_blocks_rejected(self) -> None:
        f = _finding(building_blocks=[])
        with pytest.raises(AuditParseError, match="building_blocks must be non-empty"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_invalid_block_code(self) -> None:
        f = _finding(building_blocks=["vp", "bogus"])
        with pytest.raises(
            AuditParseError, match=r"building_blocks\[1\]='bogus' not in"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_building_blocks_must_be_list(self) -> None:
        f = _finding()
        f["building_blocks"] = "vp"  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="building_blocks must be list"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_duplicate_blocks_rejected(self) -> None:
        f = _finding(building_blocks=["vp", "vp", "cr"])
        with pytest.raises(
            AuditParseError, match="building_blocks contains duplicates"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_raw_dollar_code_rejected(self) -> None:
        """Regression guard: the SKILL.md uses ``r_dollar`` / ``c_dollar``
        to avoid ``$`` footguns; raw ``r$`` must not leak through."""
        f = _finding(building_blocks=["r$"])
        with pytest.raises(AuditParseError, match=r"not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — tension rules (lex order, in-bb, 2-element)
# =============================================================================


class TestParseAuditResponseTension:
    def test_tension_not_list_rejected(self) -> None:
        f = _finding()
        f["tension"] = "cr,r_dollar"  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="tension must be list"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_tension_length_one_rejected(self) -> None:
        f = _finding(tension=["vp"])
        with pytest.raises(
            AuditParseError, match="tension must be either .* 2-element"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_tension_length_three_rejected(self) -> None:
        f = _finding(tension=["cr", "r_dollar", "vp"])
        with pytest.raises(
            AuditParseError, match="tension must be either .* 2-element"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_tension_invalid_block_rejected(self) -> None:
        f = _finding(tension=["cr", "bogus"])
        with pytest.raises(AuditParseError, match=r"tension\[1\]='bogus' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_tension_same_block_twice_rejected(self) -> None:
        # ``["cr", "cr"]`` fails the distinct-block check; the lex-order
        # check would never fire because >= handles equality.
        f = _finding(
            building_blocks=["cr", "r_dollar"],
            tension=["cr", "cr"],
        )
        with pytest.raises(
            AuditParseError, match="tension must be two distinct blocks"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_tension_reversed_order_rejected(self) -> None:
        """SKILL.md: tension pair must be lex-ordered so parser can
        dedupe (a,b) from (b,a)."""
        f = _finding(tension=["r_dollar", "cr"])  # reversed
        with pytest.raises(
            AuditParseError, match="not in lexicographic order"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_tension_block_not_in_building_blocks_rejected(self) -> None:
        """A tension names two blocks the finding implicates — both
        members must appear in ``building_blocks``."""
        f = _finding(
            building_blocks=["vp", "cr"],  # missing r_dollar
            tension=["cr", "r_dollar"],
        )
        with pytest.raises(
            AuditParseError,
            match=r"tension=.*names blocks .* not present in building_blocks",
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_tension_empty_list_legal(self) -> None:
        """Single-block finding — empty tension is legal."""
        f = _finding(
            building_blocks=["vp"],
            tension=[],
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


# =============================================================================
# parse_audit_response — pattern closed set
# =============================================================================


class TestParseAuditResponsePattern:
    def test_invalid_pattern_rejected(self) -> None:
        f = _finding(pattern="viral")
        with pytest.raises(AuditParseError, match=r"pattern='viral' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_pattern_non_string_rejected(self) -> None:
        f = _finding()
        f["pattern"] = None  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="pattern must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — tension × dimension cap (cross-finding rule)
# =============================================================================


class TestParseAuditResponseTensionCap:
    def test_tension_sev3_forces_dim_score_cap(self) -> None:
        """Cross-finding rule: tension sev ≥ 3 → dim score ≤ 2."""
        scores = {
            "value_delivery": 3,
            "revenue_relationships": 3,  # offending: > 2 with sev-3 tension
            "infrastructure_fit": 3,
            "pattern_coherence": 3,
        }
        payload = _happy_payload(dim_scores=scores, findings=[_finding()])
        with pytest.raises(
            AuditParseError,
            match=r"tension=.*at severity 3.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_tension_sev4_forces_dim_score_cap(self) -> None:
        """Same rule at the top of the severity range — guards against
        an off-by-one in the ``>= 3`` threshold."""
        f = _finding(severity=4)
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["revenue_relationships"] = 3  # > 2 — should fail
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError,
            match=r"tension=.*forces dimension",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_single_block_sev3_does_not_force_dim_cap(self) -> None:
        """The dimension-cap rule applies only to findings with a
        non-empty tension. A sev-3 single-block finding + dim score 3
        is legal."""
        f = _finding(
            building_blocks=["vp"],
            tension=[],
            severity=3,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_tension_sev2_does_not_force_dim_cap(self) -> None:
        """Cap triggers at sev ≥ 3; a sev-2 tension + dim score 3 is
        legal (defends against an off-by-one in the ``>= 3`` check)."""
        f = _finding(severity=2)
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )


# =============================================================================
# parse_audit_response — no duplicate (heuristic, tension) pairs
# =============================================================================


class TestParseAuditResponseDuplicates:
    def test_duplicate_pair_rejected(self) -> None:
        f1 = _finding(heuristic="monetisation_interrupts_value")
        f2 = _finding(
            heuristic="monetisation_interrupts_value",
            # Same heuristic AND same tension — duplicate pair.
            evidence_quote_idxs=[1, 3],
        )
        # Second finding would also re-trigger the cap; lift scores so
        # the cap is satisfied and duplicate-check fires.
        scores = {k: 2 for k in DIMENSION_KEYS}
        payload = _happy_payload(dim_scores=scores, findings=[f1, f2])
        with pytest.raises(
            AuditParseError,
            match=r"repeats \(heuristic, tension\) pair",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_same_heuristic_different_tension_passes(self) -> None:
        """Two findings may share ``heuristic`` if they name different
        tensions — SKILL.md guards against *pair* duplicates."""
        f1 = _finding(
            heuristic="monetisation_interrupts_value",
            building_blocks=["cr", "r_dollar"],
            tension=["cr", "r_dollar"],
        )
        f2 = _finding(
            heuristic="monetisation_interrupts_value",
            building_blocks=["r_dollar", "vp"],
            tension=["r_dollar", "vp"],
            evidence_quote_idxs=[1, 3],
        )
        # Both findings on revenue_relationships with sev-3 tensions —
        # that dim must be ≤ 2 to pass the cap rule.
        scores = {k: 2 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f1, f2])),
            n_quotes=5,
        )

    def test_same_heuristic_one_tension_one_single_block_passes(self) -> None:
        """A tension finding and a single-block finding may share a
        heuristic — tension tuples are ``("cr","r_dollar")`` vs ``()``."""
        f1 = _finding(
            heuristic="upgrade_path_opaque",
            building_blocks=["cr", "r_dollar"],
            tension=["cr", "r_dollar"],
        )
        f2 = _finding(
            heuristic="upgrade_path_opaque",
            building_blocks=["r_dollar"],
            tension=[],
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[1],
        )
        scores = {k: 2 for k in DIMENSION_KEYS}
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
        """The business-alignment relaxation: markup-only findings
        with no quotes and no idxs are legal (key contrast with
        Kahneman)."""
        f = _finding(
            heuristic="pricing_not_visible",
            building_blocks=["r_dollar"],
            tension=[],
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
        # Construct findings at severities 1..4. Use single-block
        # findings to avoid the tension × dim-cap rule, and unique
        # (heuristic, tension) pairs.
        findings = [
            _finding(
                heuristic=f"h{n}",
                building_blocks=["vp"],
                tension=[],
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
        violations = ba._build_heuristic_violations(payload, c)
        assert [v.severity for v in violations] == [3, 5, 7, 9]

    def test_reasoning_block_tension_pattern_tags(self) -> None:
        c = _cluster(quotes=["q0", "q1"])
        f = _finding(
            dimension="revenue_relationships",
            heuristic="monetisation_interrupts_value",
            building_blocks=["cr", "r_dollar", "vp"],
            tension=["cr", "r_dollar"],
            pattern="freemium",
            severity=3,
            evidence_source=["quotes", "ui_context"],
            evidence_quote_idxs=[0, 1],
        )
        violations = ba._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert reasoning.startswith("[revenue_relationships]")
        assert "blocks: cr,r_dollar,vp" in reasoning
        assert "tension: cr↔r_dollar" in reasoning
        assert "pattern: freemium" in reasoning
        assert "q[0]='q0'" in reasoning
        assert "q[1]='q1'" in reasoning
        assert "Nielsen 3 → anchored 7" in reasoning
        # Kahneman tags must NOT appear — this is the Osterwalder skill.
        assert "mechanism:" not in reasoning
        assert "intent:" not in reasoning

    def test_reasoning_single_block_tension_tag(self) -> None:
        """Single-block findings render ``tension: none`` not an arrow."""
        c = _cluster(quotes=["q0"])
        f = _finding(
            heuristic="pricing_not_visible",
            building_blocks=["r_dollar"],
            tension=[],
            pattern="freemium",
            severity=2,
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        violations = ba._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert "tension: none" in reasoning
        # Markup-only finding: evidence fragment renders an em-dash
        # placeholder rather than a q[idx]=... listing.
        assert "Evidence (html):" in reasoning

    def test_reasoning_sources_tag_reflects_evidence_source(self) -> None:
        c = _cluster(quotes=["q0"])
        f = _finding(
            evidence_source=["quotes", "html"],
            evidence_quote_idxs=[0],
        )
        violations = ba._build_heuristic_violations(
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
        violations = ba._build_heuristic_violations(
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
            severity=2,
            building_blocks=["vp"],
            tension=[],
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        violations = ba._build_heuristic_violations(
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

    def test_tension_cap_rule_triggers_fallback_not_exception(self) -> None:
        """A payload that violates the tension × dimension coupling is
        a parse-level rejection → fallback, never a transport
        exception. Guards against the cross-finding check being hoisted
        out of ``parse_audit_response`` by a future refactor."""
        scores = {k: 3 for k in DIMENSION_KEYS}  # all 3 — violates cap
        bad_payload = _happy_payload(dim_scores=scores, findings=[_finding()])
        client = FakeClient(default_response=json.dumps(bad_payload))
        outcome = asyncio.run(
            audit_cluster(_cluster(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "fallback"
        assert "tension" in (outcome.reason or "")

    def test_transport_failure_propagates(self) -> None:
        client = FakeClient(
            raise_on={"Energy-and-streak": RuntimeError("replay miss")}
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
# build_provenance — Osterwalder-extended aggregates
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

    def test_building_block_counts_all_nine_keys(self) -> None:
        """Every Canvas code in :data:`VALID_BLOCKS` should appear as
        a key in the histogram, even at count 0 — so a reviewer never
        mistakes "no CS findings" for "CS wasn't evaluated"."""
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    heuristic="h1",
                    building_blocks=["vp"],
                    tension=[],
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="h2",
                    building_blocks=["vp", "cs"],
                    tension=["cs", "vp"],
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
        assert set(prov["building_block_counts"]) == VALID_BLOCKS
        assert prov["building_block_counts"]["vp"] == 2
        assert prov["building_block_counts"]["cs"] == 1
        assert prov["building_block_counts"]["kp"] == 0

    def test_pattern_histogram_all_seven_keys(self) -> None:
        """Every pattern in :data:`VALID_PATTERNS` should appear as a
        key in the histogram, even at count 0."""
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    heuristic="h1",
                    building_blocks=["vp"],
                    tension=[],
                    pattern="freemium",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="h2",
                    building_blocks=["vp"],
                    tension=[],
                    pattern="freemium",
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="h3",
                    building_blocks=["cr"],
                    tension=[],
                    pattern="subscription",
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
        assert set(prov["pattern_histogram"]) == VALID_PATTERNS
        assert prov["pattern_histogram"]["freemium"] == 2
        assert prov["pattern_histogram"]["subscription"] == 1
        assert prov["pattern_histogram"]["multi_sided"] == 0

    def test_tension_counts_sorted_descending(self) -> None:
        """Tension pair counts ship as a list sorted by
        ``(-count, block_a, block_b)``. Determinism matters: provenance
        diffs across reruns on the same corpus must stay empty. All
        tension findings here share the same dim and are sev 3, so
        scores need dim_cap (≤ 2)."""
        payload = _happy_payload(
            dim_scores={
                "value_delivery": 2,
                "revenue_relationships": 2,
                "infrastructure_fit": 3,
                "pattern_coherence": 3,
            },
            findings=[
                _finding(
                    heuristic="h1",
                    dimension="revenue_relationships",
                    building_blocks=["cr", "r_dollar"],
                    tension=["cr", "r_dollar"],
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="h2",
                    dimension="revenue_relationships",
                    building_blocks=["cr", "r_dollar", "vp"],
                    tension=["cr", "r_dollar"],
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[1],
                ),
                _finding(
                    heuristic="h3",
                    dimension="value_delivery",
                    building_blocks=["cs", "vp"],
                    tension=["cs", "vp"],
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="h4",
                    dimension="value_delivery",
                    building_blocks=["cr", "vp"],
                    tension=["cr", "vp"],
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
        # Tie-break by lex (block_a, block_b): cs,vp < cr,vp would be
        # wrong — actual lex compares first blocks: cr < cs.
        assert prov["tension_counts"] == [
            {"tension": ["cr", "r_dollar"], "count": 2},
            {"tension": ["cr", "vp"], "count": 1},
            {"tension": ["cs", "vp"], "count": 1},
        ]

    def test_tension_and_single_block_gauges(self) -> None:
        """Provenance exposes ``tension_findings`` and
        ``single_block_findings`` as a quick glance at how often
        cross-block conflict fires."""
        payload = _happy_payload(
            dim_scores={
                "value_delivery": 3,
                "revenue_relationships": 2,
                "infrastructure_fit": 3,
                "pattern_coherence": 3,
            },
            findings=[
                _finding(
                    heuristic="tension_a",
                    dimension="revenue_relationships",
                    building_blocks=["cr", "r_dollar"],
                    tension=["cr", "r_dollar"],
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="single_a",
                    dimension="value_delivery",
                    building_blocks=["vp"],
                    tension=[],
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="single_b",
                    dimension="infrastructure_fit",
                    building_blocks=["kr"],
                    tension=[],
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
        assert prov["tension_findings"] == 1
        assert prov["single_block_findings"] == 2

    def test_severity_histogram(self) -> None:
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    heuristic="h1",
                    building_blocks=["vp"],
                    tension=[],
                    severity=1,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="h2",
                    building_blocks=["vp"],
                    tension=[],
                    severity=3,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
                _finding(
                    heuristic="h3",
                    building_blocks=["vp"],
                    tension=[],
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

    def test_no_kahneman_fields_in_provenance(self) -> None:
        """Defence-in-depth: provenance payload must not accidentally
        carry Kahneman-flavoured aggregates (``intent_histogram``,
        ``mechanism_counts``). A hybrid provenance would break L5
        ingestion heuristics that key on skill_id."""
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
        assert "intent_histogram" not in prov
        assert "mechanism_counts" not in prov
        assert "wcag_level_histogram" not in prov
        # Osterwalder-specific aggregates present.
        assert "building_block_counts" in prov
        assert "pattern_histogram" in prov
        assert "tension_counts" in prov


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
        monkeypatch.setattr(ba, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [
                _cluster(cluster_id="cluster_00").model_dump(mode="json"),
                _cluster(
                    cluster_id="cluster_01",
                    label=(
                        "Ad-supported VP promises premium experience but "
                        "interrupts with cross-sell ads for Super users"
                    ),
                    quotes=[
                        "I pay for Super but still get ads",
                        "the premium promise isn't delivered",
                    ],
                ).model_dump(mode="json"),
            ],
        )
        output_path = data_dir / "verdicts.jsonl"
        native_path = data_dir / "verdicts.native.jsonl"

        fake = FakeClient(default_response=_happy_response_text())

        def _fake_client_ctor(**_kwargs: Any) -> FakeClient:
            return fake

        monkeypatch.setattr(ba, "Client", _fake_client_ctor)

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
                "l4-ba-test-run",
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
        # Osterwalder-specific aggregates land in the on-disk provenance.
        assert "building_block_counts" in prov
        assert "pattern_histogram" in prov
        assert "tension_counts" in prov

    def test_cli_empty_input_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(ba, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        output_path = data_dir / "out.jsonl"
        native_path = data_dir / "native.jsonl"

        monkeypatch.setattr(ba, "Client", lambda **_k: FakeClient())
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
