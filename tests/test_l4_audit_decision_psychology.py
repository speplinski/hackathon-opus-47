"""Tests for ``auditable_design.layers.l4_audit_decision_psychology``.

Structure mirrors ``test_l4_audit_accessibility.py`` (the WCAG sibling
module) so a reader can diff the two and see exactly where
decision-psychology's contract diverges from the other L4 skills:

* **Four dimensions, not five** — ``cognitive_load_ease``,
  ``choice_architecture``, ``judgment_heuristics``,
  ``temporal_experience``; Kahneman's dual-process cut, not POUR.
* **``mechanism`` + ``intent`` findings, no WCAG** — each finding names
  the Kahneman mechanism in play (e.g. ``loss_aversion``) and tags the
  design intent from the closed set
  ``{nudge, dark_pattern, unintentional, absent}``.
* **Quotes are always required** — unlike accessibility, which lets a
  pure markup-observed finding carry ``evidence_quote_idxs == []``, a
  decision-psychology finding is a claim about a *user decision* and
  must therefore anchor to ≥ 1 quote (SKILL.md rule, enforced at parse).
* **Dark-pattern discipline** — ``intent == 'dark_pattern'`` forces
  ``severity ≥ 2`` (per-finding), and ``severity ≥ 3`` + ``dark_pattern``
  forces the enclosing dimension score to ``≤ 2`` (cross-finding).
* **No duplicate ``(heuristic, mechanism)`` pairs** — guards against a
  model emitting two copies of the same observation under different
  framings.

Strategy
--------
Every test that would otherwise exercise Claude uses an in-process
:class:`FakeClient` with scripted responses — same pattern as L3b,
Norman, and accessibility sibling modules. No network, no real replay
log; whole file runs in < 1 s.
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
from auditable_design.layers import l4_audit_decision_psychology as dp
from auditable_design.layers.l4_audit_decision_psychology import (
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
    VALID_INTENTS,
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

    Duplicated (intentionally) from the accessibility and Norman test
    modules so a change in one test file doesn't silently affect the
    others. First substring hit in ``user`` wins when scripting
    responses.
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
    label: str = "Streak-save modal pressures users into one-tap purchase",
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    centroid_ref: str = "l3_centroids.npy#0",
    ui_context: str | None = None,
    html: str | None = None,
    screenshot_ref: str | None = None,
) -> InsightCluster:
    """Build an InsightCluster with SKILL.md-aligned defaults.

    Defaults model the Duolingo streak-save modal (same UI surface as
    the accessibility test helper — Kahneman sees it through the
    loss-aversion lens rather than the contrast lens).
    """
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref=centroid_ref,
        representative_quotes=quotes
        or [
            "I panicked when I saw I was about to lose 200 days",
            "I tapped the gems option before I realised what I was buying",
            "the 'no thanks' link is almost hidden",
            "it made me feel awful about missing one day",
        ],
        ui_context=ui_context,
        html=html,
        screenshot_ref=screenshot_ref,
    )


def _finding(
    *,
    dimension: str = "choice_architecture",
    heuristic: str = "streak_recovery_upsell",
    mechanism: str = "loss_aversion",
    intent: str = "dark_pattern",
    violation: str = (
        "Modal frames the loss of a 200-day streak as imminent and "
        "offers a one-tap paid recovery while the free dismiss is "
        "visually suppressed."
    ),
    severity: int = 3,
    evidence_source: list[str] | None = None,
    evidence_quote_idxs: list[int] | None = None,
    recommendation: str = (
        "Balance visual weight of 'pay' vs 'no thanks'; delay the "
        "upsell until the user explicitly opts to recover."
    ),
) -> dict[str, Any]:
    """Build one finding dict with SKILL.md-valid defaults.

    Defaults describe a plausible loss-aversion dark pattern anchored
    on the first two quotes. Override individual fields to hit
    business-rule edges. The defaults deliberately put ``intent`` at
    ``dark_pattern`` + severity 3 so a parser that silently drops the
    dark-pattern × dimension coupling will fail the default
    ``_happy_payload`` shape (which pairs this finding with a dimension
    score of 2).
    """
    return {
        "dimension": dimension,
        "heuristic": heuristic,
        "mechanism": mechanism,
        "intent": intent,
        "violation": violation,
        "severity": severity,
        "evidence_source": evidence_source
        if evidence_source is not None
        else ["quotes", "html"],
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
        "Streak-save modal leans on loss aversion to drive purchase; "
        "choice architecture biases toward pay."
    ),
) -> dict[str, Any]:
    """Structurally-valid decision-psychology payload.

    Defaults: neutral-to-low scores (2 on choice_architecture to make
    the default dark_pattern sev-3 finding consistent, 3 elsewhere),
    one loss-aversion dark-pattern finding. Callers override whichever
    slice is under test.
    """
    scores = (
        dim_scores
        if dim_scores is not None
        else {
            "cognitive_load_ease": 3,
            "choice_architecture": 2,
            "judgment_heuristics": 3,
            "temporal_experience": 3,
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
        assert SKILL_ID == "audit-decision-psychology"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l4_audit_decision_psychology"

    def test_default_model_is_sonnet(self) -> None:
        # Same rationale as Norman and accessibility: Sonnet 4.6 is
        # reasoning-capable without Opus's budget. Shift to Opus
        # requires an ADR.
        assert MODEL == "claude-sonnet-4-6"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        # Decision-psychology payloads carry per-finding mechanism +
        # intent (both short) but no WCAG fields — similar envelope to
        # accessibility. 6144 sits comfortably in the operating band.
        assert 4096 <= MAX_TOKENS <= 12288

    def test_dimension_keys_exactly_four_kahneman(self) -> None:
        assert DIMENSION_KEYS == frozenset(
            {
                "cognitive_load_ease",
                "choice_architecture",
                "judgment_heuristics",
                "temporal_experience",
            }
        )

    def test_valid_intents_closed_set(self) -> None:
        assert VALID_INTENTS == frozenset(
            {"nudge", "dark_pattern", "unintentional", "absent"}
        )

    def test_default_paths_under_data_derived(self) -> None:
        # Same input as Norman and accessibility (shared L3b labeled
        # clusters); distinct outputs so L5 can ingest all three
        # skills as sibling layer-4 rows.
        assert DEFAULT_LABELED == Path("data/derived/l3b_labeled_clusters.jsonl")
        assert DEFAULT_VERDICTS == Path(
            "data/derived/l4_audit_decision_psychology_verdicts.jsonl"
        )
        assert DEFAULT_NATIVE == Path(
            "data/derived/l4_audit_decision_psychology_verdicts.native.jsonl"
        )

    def test_skill_hash_independent_of_siblings(self) -> None:
        # Defence in depth: editing Norman's or accessibility's
        # SKILL.md must not alter decision-psychology's cache key and
        # vice versa.
        from auditable_design.layers import l4_audit, l4_audit_accessibility

        assert skill_hash() != l4_audit.skill_hash()
        assert skill_hash() != l4_audit_accessibility.skill_hash()


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
        c = _cluster(ui_context="streak modal mobile web")
        msg = build_user_message(c)
        assert "<ui_context>streak modal mobile web</ui_context>" in msg
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
        c = _cluster(screenshot_ref="data/artifacts/ui/streak.png")
        msg = build_user_message(c)
        assert (
            "<screenshot_ref>data/artifacts/ui/streak.png</screenshot_ref>" in msg
        )

    def test_tag_order_is_fixed(self) -> None:
        """Fixed tag order: label → ui_context → html → screenshot_ref
        → q*. Locking the order keeps replay cache keys stable across
        reruns and matches the accessibility sibling byte-for-byte.
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

    def test_all_optional_fields_together(self) -> None:
        c = _cluster(
            quotes=["q0", "q1"],
            ui_context="modal context",
            html="<button>Click</button>",
            screenshot_ref="data/artifacts/ui/x.png",
        )
        msg = build_user_message(c)
        assert msg.count("<ui_context>") == 1
        assert msg.count("<html>") == 1
        assert msg.count("<screenshot_ref>") == 1
        assert msg.count('<q idx="0">') == 1
        assert msg.count('<q idx="1">') == 1

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
        """HTML excerpt passes through verbatim via CDATA — same
        guarantee as the accessibility sibling."""
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

    def test_nudge_finding_passes(self) -> None:
        f = _finding(
            intent="nudge",
            heuristic="friction_timer",
            mechanism="system_2_engagement",
            severity=1,
            evidence_source=["quotes", "ui_context"],
            evidence_quote_idxs=[0],
        )
        # sev 1 + nudge avoids both the dark-pattern floor and the
        # dimension-score coupling. Default dim scores fine.
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
        )

    def test_unintentional_finding_passes(self) -> None:
        f = _finding(
            intent="unintentional",
            heuristic="time_pressure_framing",
            mechanism="availability_heuristic",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[2],
        )
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
        )

    def test_absent_intent_passes(self) -> None:
        """``absent`` intent marks a missing-mechanism case ("design by
        default" failure) — it is a legal classification even when the
        design doesn't actually nudge anyone. SKILL.md explicitly lists
        it in the closed set."""
        f = _finding(
            intent="absent",
            heuristic="no_salient_default",
            mechanism="default_bias",
            severity=2,
            evidence_source=["quotes", "html"],
            evidence_quote_idxs=[0],
        )
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
        )

    def test_dark_pattern_sev_2_passes(self) -> None:
        """The sev-2 floor is inclusive: dark_pattern + severity 2 is
        legal. Defends against an off-by-one in the floor check."""
        f = _finding(
            intent="dark_pattern",
            severity=2,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        # sev 2 avoids the dimension-coupling rule (which triggers at
        # ≥ 3); default dim scores fine.
        parse_audit_response(
            json.dumps(_happy_payload(
                dim_scores={k: 3 for k in DIMENSION_KEYS},
                findings=[f],
            )),
            n_quotes=5,
        )

    def test_dark_pattern_sev_3_dim_2_passes(self) -> None:
        """The dimension-coupling rule is inclusive on the upper side:
        sev 3 dark_pattern with dim score 2 is legal (≤ 2 required)."""
        parse_audit_response(_happy_response_text(), n_quotes=5)

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
                "cognitive_load_ease": 3,
                "choice_architecture": 3,
                "judgment_heuristics": 3,
                # temporal_experience missing
            }
        )
        with pytest.raises(AuditParseError, match="dimension_scores missing keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_norman_dimensions_are_rejected(self) -> None:
        """A Norman-shaped payload (4 Norman keys) must fail here,
        catching a wiring mistake where the wrong skill's output was
        pointed at the wrong parser.
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

    def test_accessibility_dimensions_are_rejected(self) -> None:
        """Five POUR+Inclusive keys must fail here — defends against a
        copy-paste slip in L5 ingestion or CLI wiring."""
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
        scores["cognitive_load_ease"] = 7
        with pytest.raises(AuditParseError, match=r"out of \{1,2,3,4,5\}"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )

    def test_bool_rejected_as_score(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["cognitive_load_ease"] = True  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="must be int, got bool"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )


# =============================================================================
# parse_audit_response — findings structural
# =============================================================================


class TestParseAuditResponseFindingsStructural:
    def test_missing_finding_key(self) -> None:
        f = _finding()
        f.pop("mechanism")
        with pytest.raises(AuditParseError, match=r"findings\[0\] missing keys"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_missing_intent_rejected(self) -> None:
        """``intent`` is Kahneman-skill specific — it must be on every
        finding."""
        f = _finding()
        f.pop("intent")
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

    def test_accessibility_wcag_keys_rejected(self) -> None:
        """WCAG-shaped findings (``wcag_ref`` / ``wcag_level``) must
        fail here — catches the mirror wiring mistake where an
        accessibility payload is fed into the decision-psychology
        parser."""
        f = _finding()
        f.pop("mechanism")
        f.pop("intent")
        f["wcag_ref"] = "1.4.3"
        f["wcag_level"] = "AA"
        with pytest.raises(
            AuditParseError, match="findings\\[0\\] (missing|unexpected) keys"
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

    def test_norman_dimension_rejected(self) -> None:
        f = _finding(dimension="interaction_fundamentals")
        with pytest.raises(
            AuditParseError, match="dimension='interaction_fundamentals'"
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

    def test_empty_mechanism_string(self) -> None:
        """``mechanism`` is the Kahneman vocabulary anchor — an empty
        value defeats the whole point of the skill."""
        f = _finding(mechanism="   ")
        with pytest.raises(AuditParseError, match=r"mechanism.*non-empty"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )


# =============================================================================
# parse_audit_response — intent closed set + dark-pattern discipline
# =============================================================================


class TestParseAuditResponseIntent:
    def test_invalid_intent(self) -> None:
        f = _finding(intent="manipulative")
        with pytest.raises(AuditParseError, match=r"intent='manipulative' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_intent_non_string_rejected(self) -> None:
        f = _finding()
        f["intent"] = None  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match=r"intent=None not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_dark_pattern_severity_floor_rejects_1(self) -> None:
        """SKILL.md: dark_pattern findings are never cosmetic — sev ≥ 2."""
        f = _finding(
            intent="dark_pattern",
            severity=1,
            evidence_source=["quotes"],
            evidence_quote_idxs=[0],
        )
        with pytest.raises(
            AuditParseError, match="dark_pattern findings to carry severity ≥ 2"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_dark_pattern_sev3_forces_dim_score_cap(self) -> None:
        """Cross-finding rule: dark_pattern sev ≥ 3 → dim score ≤ 2."""
        scores = {
            "cognitive_load_ease": 3,
            "choice_architecture": 3,  # offending: > 2 with dark_pattern sev 3
            "judgment_heuristics": 3,
            "temporal_experience": 3,
        }
        payload = _happy_payload(dim_scores=scores, findings=[_finding()])
        with pytest.raises(
            AuditParseError, match="dark_pattern severity.*forces dimension"
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_dark_pattern_sev4_forces_dim_score_cap(self) -> None:
        """Same rule at the top of the severity range — guards against
        an off-by-one in the ``>= 3`` threshold."""
        f = _finding(severity=4)
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["choice_architecture"] = 3  # > 2 — should fail
        payload = _happy_payload(dim_scores=scores, findings=[f])
        with pytest.raises(
            AuditParseError, match="dark_pattern severity.*forces dimension"
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_nudge_sev3_does_not_force_dim_cap(self) -> None:
        """The dimension-cap rule applies to dark_pattern, not nudge.
        A sev-3 nudge + dim score 3 is legal."""
        f = _finding(intent="nudge", severity=3)
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )

    def test_unintentional_sev3_does_not_force_dim_cap(self) -> None:
        f = _finding(intent="unintentional", severity=3)
        scores = {k: 3 for k in DIMENSION_KEYS}
        parse_audit_response(
            json.dumps(_happy_payload(dim_scores=scores, findings=[f])),
            n_quotes=5,
        )


# =============================================================================
# parse_audit_response — no duplicate (heuristic, mechanism) pairs
# =============================================================================


class TestParseAuditResponseDuplicates:
    def test_duplicate_pair_rejected(self) -> None:
        f1 = _finding(heuristic="streak_recovery_upsell", mechanism="loss_aversion")
        f2 = _finding(
            heuristic="streak_recovery_upsell",
            mechanism="loss_aversion",
            evidence_quote_idxs=[2],
        )
        payload = _happy_payload(findings=[f1, f2])
        with pytest.raises(
            AuditParseError,
            match=r"repeats \(heuristic, mechanism\) pair",
        ):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_same_heuristic_different_mechanism_passes(self) -> None:
        """Two findings may share ``heuristic`` if they name different
        mechanisms — SKILL.md guards against *pair* duplicates."""
        f1 = _finding(heuristic="pre_commitment_wall", mechanism="loss_aversion")
        f2 = _finding(
            heuristic="pre_commitment_wall",
            mechanism="anchoring",
            evidence_quote_idxs=[2],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f1, f2])), n_quotes=5
        )

    def test_same_mechanism_different_heuristic_passes(self) -> None:
        f1 = _finding(heuristic="streak_recovery_upsell", mechanism="loss_aversion")
        f2 = _finding(
            heuristic="timer_countdown_nudge",
            mechanism="loss_aversion",
            evidence_quote_idxs=[2],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f1, f2])), n_quotes=5
        )


# =============================================================================
# parse_audit_response — evidence_source + quotes-always-required
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

    def test_evidence_source_without_quotes_rejected(self) -> None:
        """The key difference from accessibility: a Kahneman finding
        that cites only markup/screenshot is illegal — decision audit
        needs a quote anchor."""
        f = _finding(
            evidence_source=["html", "screenshot"],
            evidence_quote_idxs=[],
        )
        with pytest.raises(
            AuditParseError, match="does not include 'quotes'"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_quote_idxs_empty_rejected_even_with_quotes(self) -> None:
        """``quotes`` in evidence_source but empty idxs — contradiction
        at the schema level, independently flagged from the quotes-
        always rule."""
        f = _finding(
            evidence_source=["quotes", "html"],
            evidence_quote_idxs=[],
        )
        with pytest.raises(
            AuditParseError, match="evidence_quote_idxs is empty"
        ):
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
        # Construct findings at severities 1..4. Pair each with a
        # unique (heuristic, mechanism) to avoid the duplicate-pair
        # rule, and use intents that don't trip the dimension-cap rule
        # when scores are neutral.
        findings = [
            _finding(
                heuristic=f"h{n}",
                mechanism=f"m{n}",
                intent="unintentional",
                severity=n,
                evidence_source=["quotes", "html"],
                evidence_quote_idxs=[0],
            )
            for n in (1, 2, 3, 4)
        ]
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=findings,
        )
        c = _cluster()
        violations = dp._build_heuristic_violations(payload, c)
        assert [v.severity for v in violations] == [3, 5, 7, 9]

    def test_reasoning_mechanism_and_intent_tags(self) -> None:
        c = _cluster(quotes=["q0", "q1"])
        f = _finding(
            dimension="choice_architecture",
            heuristic="streak_recovery_upsell",
            mechanism="loss_aversion",
            intent="dark_pattern",
            severity=3,
            evidence_source=["quotes", "html"],
            evidence_quote_idxs=[0, 1],
        )
        violations = dp._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert reasoning.startswith("[choice_architecture]")
        assert "(mechanism: loss_aversion; intent: dark_pattern)" in reasoning
        assert "q[0]='q0'" in reasoning
        assert "q[1]='q1'" in reasoning
        assert "Nielsen 3 → anchored 7" in reasoning
        # WCAG tags must NOT appear — this is the Kahneman skill.
        assert "WCAG" not in reasoning

    def test_reasoning_sources_tag_reflects_evidence_source(self) -> None:
        c = _cluster(quotes=["q0"])
        f = _finding(
            evidence_source=["quotes", "html"],
            evidence_quote_idxs=[0],
        )
        violations = dp._build_heuristic_violations(
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
        violations = dp._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert "Specific-violation-text" in reasoning
        assert "Recommendation: Specific-recommendation-text" in reasoning

    def test_violation_severity_is_anchored_not_nielsen(self) -> None:
        """ADR-008: violation records always carry anchored severity
        (0..10 band). Confirms the remap happens exactly once, not
        twice, not zero times."""
        c = _cluster()
        f = _finding(severity=2, evidence_source=["quotes"], evidence_quote_idxs=[0])
        violations = dp._build_heuristic_violations(
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

    def test_dark_pattern_rule_triggers_fallback_not_exception(self) -> None:
        """A payload that violates the dark-pattern × dimension
        coupling is a parse-level rejection → fallback, never a
        transport exception. Guards against the cross-finding check
        being hoisted out of ``parse_audit_response`` by a future
        refactor."""
        scores = {k: 3 for k in DIMENSION_KEYS}  # all 3 — violates cap
        bad_payload = _happy_payload(dim_scores=scores, findings=[_finding()])
        client = FakeClient(default_response=json.dumps(bad_payload))
        outcome = asyncio.run(
            audit_cluster(_cluster(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "fallback"
        assert "dark_pattern" in (outcome.reason or "")

    def test_transport_failure_propagates(self) -> None:
        client = FakeClient(
            raise_on={"Streak-save modal": RuntimeError("replay miss")}
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
        the prompt that reaches the client. Same guarantee as the
        accessibility sibling — guards against a refactor silently
        dropping the new fields from the prompt builder."""
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
# build_provenance — Kahneman-extended aggregates
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

    def test_intent_histogram(self) -> None:
        """Every intent in :data:`VALID_INTENTS` should appear as a
        key in the histogram, even at count 0 — so a reviewer never
        mistakes "no dark_pattern findings" for "dark_pattern wasn't
        evaluated"."""
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    heuristic="h1",
                    mechanism="m1",
                    intent="nudge",
                    severity=2,
                ),
                _finding(
                    heuristic="h2",
                    mechanism="m2",
                    intent="nudge",
                    severity=2,
                ),
                _finding(
                    heuristic="h3",
                    mechanism="m3",
                    intent="unintentional",
                    severity=2,
                ),
            ],
        )
        outcomes = [
            self._outcome(cluster_id="c00", status="audited", payload=payload)
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert prov["intent_histogram"] == {
            "nudge": 2,
            "dark_pattern": 0,
            "unintentional": 1,
            "absent": 0,
        }

    def test_mechanism_counts_sorted_descending(self) -> None:
        """Mechanism counts ship as a list sorted by ``(-count, name)``.
        Determinism matters: provenance diffs across reruns on the
        same corpus must stay empty."""
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    heuristic="h1",
                    mechanism="loss_aversion",
                    intent="unintentional",
                    severity=2,
                ),
                _finding(
                    heuristic="h2",
                    mechanism="loss_aversion",
                    intent="unintentional",
                    severity=2,
                ),
                _finding(
                    heuristic="h3",
                    mechanism="anchoring",
                    intent="unintentional",
                    severity=2,
                ),
                _finding(
                    heuristic="h4",
                    mechanism="availability_heuristic",
                    intent="unintentional",
                    severity=2,
                ),
            ],
        )
        outcomes = [
            self._outcome(cluster_id="c00", status="audited", payload=payload)
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        # Tie-break by alphabetical name: anchoring < availability_heuristic.
        assert prov["mechanism_counts"] == [
            {"mechanism": "loss_aversion", "count": 2},
            {"mechanism": "anchoring", "count": 1},
            {"mechanism": "availability_heuristic", "count": 1},
        ]

    def test_severity_histogram(self) -> None:
        payload = _happy_payload(
            dim_scores={k: 3 for k in DIMENSION_KEYS},
            findings=[
                _finding(
                    heuristic="h1",
                    mechanism="m1",
                    intent="unintentional",
                    severity=1,
                ),
                _finding(
                    heuristic="h2",
                    mechanism="m2",
                    intent="unintentional",
                    severity=3,
                ),
                _finding(
                    heuristic="h3",
                    mechanism="m3",
                    intent="unintentional",
                    severity=3,
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

    def test_no_wcag_fields_in_provenance(self) -> None:
        """Defence-in-depth: provenance payload must not accidentally
        carry accessibility-flavoured aggregates (``wcag_level_histogram``,
        ``wcag_ref_counts``). A hybrid provenance would break L5
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
        assert "wcag_level_histogram" not in prov
        assert "wcag_ref_counts" not in prov
        # Kahneman-specific aggregates present.
        assert "intent_histogram" in prov
        assert "mechanism_counts" in prov


# =============================================================================
# CLI — main
# =============================================================================


class TestMain:
    """End-to-end CLI tests. The Norman sibling is deselected in the
    sandbox because pytest's tmpdir ownership check trips on the
    sandbox user; the same may happen here. Keep these tests lean so
    sandbox runs can skip the class via ``-k 'not TestMain'`` without
    losing the core-logic coverage above.
    """

    def test_cli_end_to_end_with_fakeclient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(dp, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [
                _cluster(cluster_id="cluster_00").model_dump(mode="json"),
                _cluster(
                    cluster_id="cluster_01",
                    label="Loss-framed timer manipulates user into early payment",
                    quotes=[
                        "I felt rushed to pay before the timer hit zero",
                        "no idea what happens if I wait",
                    ],
                ).model_dump(mode="json"),
            ],
        )
        output_path = data_dir / "verdicts.jsonl"
        native_path = data_dir / "verdicts.native.jsonl"

        fake = FakeClient(default_response=_happy_response_text())

        def _fake_client_ctor(**_kwargs: Any) -> FakeClient:
            return fake

        monkeypatch.setattr(dp, "Client", _fake_client_ctor)

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
                "l4-dp-test-run",
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
        # Kahneman-specific aggregates land in the on-disk provenance.
        assert "intent_histogram" in prov
        assert "mechanism_counts" in prov

    def test_cli_empty_input_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(dp, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        output_path = data_dir / "out.jsonl"
        native_path = data_dir / "native.jsonl"

        monkeypatch.setattr(dp, "Client", lambda **_k: FakeClient())
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
