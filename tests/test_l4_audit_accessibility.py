"""Tests for ``auditable_design.layers.l4_audit_accessibility``.

Structure mirrors ``test_l4_audit.py`` (the Norman sibling module) so a
reader can diff the two and see exactly where accessibility's contract
diverges: additional optional prompt tags (``<ui_context>``,
``<html>``, ``<screenshot_ref>``), five POUR+Inclusive dimensions,
WCAG-extended findings (``wcag_ref``, ``wcag_level``,
``evidence_source``), AAA advisory discipline, and the
``evidence_source`` ↔ ``evidence_quote_idxs`` coupling.

Strategy
--------
Every test that would otherwise exercise Claude uses an in-process
:class:`FakeClient` with scripted responses. Pattern is the same as
L3b and Norman; no network, no real replay log; whole file runs in <1s.
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
from auditable_design.layers import l4_audit_accessibility as acc
from auditable_design.layers.l4_audit_accessibility import (
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
    WCAG_LEVELS,
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

    Duplicated (intentionally) from ``test_l4_audit.py``'s FakeClient so
    a change in one test module doesn't silently affect the other.
    First substring hit in ``user`` wins when scripting responses.
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
    label: str = "Dismiss link unreadable and focus disappears on modal",
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    centroid_ref: str = "l3_centroids.npy#0",
    ui_context: str | None = None,
    html: str | None = None,
    screenshot_ref: str | None = None,
) -> InsightCluster:
    """Build an InsightCluster with SKILL.md-aligned defaults.

    Defaults model a realistic Duolingo streak-save modal quotes-only
    cluster; the ``ui_context``/``html``/``screenshot_ref`` extras are
    opt-in so tests can exercise each combination cleanly.
    """
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref=centroid_ref,
        representative_quotes=quotes
        or [
            "the 'no thanks' link is almost invisible",
            "I tab into it and have no idea where focus went",
            "I was in hospital last week and lost my 200-day streak",
            "the button is huge and green, the skip is tiny and grey",
        ],
        ui_context=ui_context,
        html=html,
        screenshot_ref=screenshot_ref,
    )


def _finding(
    *,
    dimension: str = "perceivable",
    heuristic: str = "insufficient_text_contrast",
    wcag_ref: str | None = "1.4.3",
    wcag_level: str = "AA",
    violation: str = "Dismiss link ~1.6:1 on white, below 4.5:1 AA.",
    severity: int = 3,
    evidence_source: list[str] | None = None,
    evidence_quote_idxs: list[int] | None = None,
    recommendation: str = "Raise dismiss link contrast to at least 7:1.",
) -> dict[str, Any]:
    """Build one finding dict with SKILL.md-valid defaults.

    Defaults describe a plausible AA contrast failure anchored on the
    first two quotes — a single call with no overrides returns a
    finding that parses cleanly. Override individual fields to hit
    business-rule edges.
    """
    return {
        "dimension": dimension,
        "heuristic": heuristic,
        "wcag_ref": wcag_ref,
        "wcag_level": wcag_level,
        "violation": violation,
        "severity": severity,
        "evidence_source": evidence_source
        if evidence_source is not None
        else ["html", "quotes"],
        "evidence_quote_idxs": evidence_quote_idxs
        if evidence_quote_idxs is not None
        else [0, 3],
        "recommendation": recommendation,
    }


def _happy_payload(
    *,
    dim_scores: dict[str, int] | None = None,
    findings: list[dict[str, Any]] | None = None,
    summary: str = "Dismiss link fails AA contrast and focus indicator; situational inclusion gap on streak recovery.",
) -> dict[str, Any]:
    """Structurally-valid accessibility payload.

    Defaults: neutral 3s on every dimension, one AA contrast finding.
    Callers override whichever slice is under test.
    """
    return {
        "summary": summary,
        "dimension_scores": dim_scores or {k: 3 for k in DIMENSION_KEYS},
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
        assert SKILL_ID == "audit-accessibility"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l4_audit_accessibility"

    def test_default_model_is_sonnet(self) -> None:
        # Parallel to Norman: Sonnet 4.6 is reasoning-capable without
        # Opus's budget. Shift to Opus requires an ADR.
        assert MODEL == "claude-sonnet-4-6"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        # Accessibility payloads are richer (5 dim scores, up to 10
        # findings × ~110 tokens with WCAG fields). 6144 sits between
        # the ~2k bound and a runaway ceiling.
        assert 4096 <= MAX_TOKENS <= 12288

    def test_dimension_keys_exactly_five_pour_plus_inclusive(self) -> None:
        assert DIMENSION_KEYS == frozenset(
            {
                "perceivable",
                "operable",
                "understandable",
                "robust",
                "inclusive_cognitive",
            }
        )

    def test_wcag_levels(self) -> None:
        assert WCAG_LEVELS == frozenset({"A", "AA", "AAA", "inclusive"})

    def test_default_paths_under_data_derived(self) -> None:
        # Same input as Norman (shared L3b labeled clusters); distinct
        # outputs so L5 can ingest both skills as sibling layer-4 rows.
        assert DEFAULT_LABELED == Path("data/derived/l3b_labeled_clusters.jsonl")
        assert DEFAULT_VERDICTS == Path(
            "data/derived/l4_audit_accessibility_verdicts.jsonl"
        )
        assert DEFAULT_NATIVE == Path(
            "data/derived/l4_audit_accessibility_verdicts.native.jsonl"
        )

    def test_skill_hash_independent_of_norman(self) -> None:
        # Defence in depth: editing Norman's SKILL.md must not alter
        # accessibility's cache key and vice versa. The hash independence
        # is the structural guarantee.
        from auditable_design.layers import l4_audit

        assert skill_hash() != l4_audit.skill_hash()


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
        c = _cluster(ui_context="streak modal desktop web")
        msg = build_user_message(c)
        assert "<ui_context>streak modal desktop web</ui_context>" in msg
        assert "<html>" not in msg
        assert "<screenshot_ref>" not in msg

    def test_html_only_wrapped_in_cdata(self) -> None:
        c = _cluster(html='<a style="color:#d1d5db">no thanks</a>')
        msg = build_user_message(c)
        # Outer <html> tags are the injection boundary; inner markup is
        # in CDATA so angle brackets survive verbatim.
        assert "<html><![CDATA[\n" in msg
        assert '<a style="color:#d1d5db">no thanks</a>' in msg
        assert "]]></html>" in msg

    def test_screenshot_ref_only(self) -> None:
        c = _cluster(screenshot_ref="data/artifacts/ui/speak.png")
        msg = build_user_message(c)
        assert (
            "<screenshot_ref>data/artifacts/ui/speak.png</screenshot_ref>" in msg
        )

    def test_tag_order_is_fixed(self) -> None:
        """Fixed tag order: label → ui_context → html → screenshot_ref
        → q*. Locking the order keeps replay cache keys stable across
        reruns (any reorder changes prompt bytes and invalidates).
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
        # Every optional tag renders exactly once.
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
        # Structural tags stay unescaped — injection boundary.
        assert "<cluster>" in msg
        assert "<label>" in msg

    def test_html_content_is_not_escaped_because_cdata(self) -> None:
        """HTML excerpt passes through verbatim because it is CDATA-
        wrapped. This is the whole point of CDATA: keep raw markup
        readable to the model without double-escaping it into
        unrecognisable entities.
        """
        raw = '<button onclick="x()">Submit & go</button>'
        c = _cluster(html=raw)
        msg = build_user_message(c)
        assert raw in msg
        # Defence check: the raw string must survive WITHOUT being
        # entity-encoded.
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

    def test_aaa_advisory_at_severity_1(self) -> None:
        f = _finding(
            wcag_ref="2.4.13",
            wcag_level="AAA",
            severity=1,
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_inclusive_finding_with_null_wcag_ref(self) -> None:
        f = _finding(
            dimension="inclusive_cognitive",
            heuristic="situational_inclusion_gap",
            wcag_ref=None,
            wcag_level="inclusive",
            severity=3,
            evidence_source=["quotes", "ui_context"],
            evidence_quote_idxs=[0, 1],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_markup_only_finding_has_empty_quote_idxs(self) -> None:
        """SKILL.md rule: ``"quotes"`` ∉ evidence_source ↔
        evidence_quote_idxs == [] — this is the path that lets a pure
        markup-observed finding (e.g. computed contrast failure with
        no user-quote anchor) be emitted legally."""
        f = _finding(
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )

    def test_mixed_evidence_sources(self) -> None:
        f = _finding(
            evidence_source=["html", "screenshot", "ui_context", "quotes"],
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
        # Unbalanced brace → greedy regex doesn't match → "no JSON object".
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
                "perceivable": 3,
                "operable": 3,
                "understandable": 3,
                "robust": 3,
                # inclusive_cognitive missing
            }
        )
        with pytest.raises(AuditParseError, match="dimension_scores missing keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_norman_dimensions_are_rejected(self) -> None:
        """A Norman-shaped payload (4 Norman keys) must fail here, even
        though it would parse in ``l4_audit.parse_audit_response``.
        Catches a wiring mistake where the wrong skill's output was
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

    def test_extra_dimension_key(self) -> None:
        payload = _happy_payload(
            dim_scores={**{k: 3 for k in DIMENSION_KEYS}, "extra": 3}
        )
        with pytest.raises(AuditParseError, match="unexpected keys"):
            parse_audit_response(json.dumps(payload), n_quotes=5)

    def test_score_out_of_range(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["perceivable"] = 7
        with pytest.raises(AuditParseError, match=r"out of \{1,2,3,4,5\}"):
            parse_audit_response(
                json.dumps(_happy_payload(dim_scores=scores)), n_quotes=5
            )

    def test_bool_rejected_as_score(self) -> None:
        scores = {k: 3 for k in DIMENSION_KEYS}
        scores["perceivable"] = True  # type: ignore[assignment]
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
        f.pop("wcag_ref")
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

    def test_invalid_dimension(self) -> None:
        f = _finding(dimension="bogus_dimension")
        with pytest.raises(AuditParseError, match="dimension='bogus_dimension'"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_norman_dimension_rejected(self) -> None:
        """Catches the mirror wiring mistake: Norman-dim finding fed
        into the accessibility parser."""
        f = _finding(dimension="interaction_fundamentals")
        with pytest.raises(
            AuditParseError, match="dimension='interaction_fundamentals'"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_severity_out_of_range(self) -> None:
        f = _finding(severity=5, wcag_level="AA")
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
# parse_audit_response — WCAG level discipline
# =============================================================================


class TestParseAuditResponseWcagLevel:
    def test_invalid_level(self) -> None:
        f = _finding(wcag_level="B")
        with pytest.raises(AuditParseError, match=r"wcag_level='B' not in"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_inclusive_must_have_null_wcag_ref(self) -> None:
        f = _finding(wcag_level="inclusive", wcag_ref="1.4.3")
        with pytest.raises(
            AuditParseError, match="must be null when wcag_level=='inclusive'"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_wcag_level_requires_non_null_ref(self) -> None:
        f = _finding(wcag_level="AA", wcag_ref=None)
        with pytest.raises(AuditParseError, match="wcag_ref must be str"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_wcag_ref_regex_malformed(self) -> None:
        for bad in ("14.3", "1.4.", "1..3", "foo", "a.b.c"):
            f = _finding(wcag_ref=bad)
            with pytest.raises(AuditParseError, match="does not match"):
                parse_audit_response(
                    json.dumps(_happy_payload(findings=[f])), n_quotes=5
                )

    def test_obsolete_sc_411_rejected(self) -> None:
        """WCAG 4.1.1 Parsing was obsoleted in 2.2; SKILL.md forbids
        citing it and the parser enforces."""
        f = _finding(wcag_ref="4.1.1", wcag_level="A")
        with pytest.raises(AuditParseError, match="obsolete in WCAG 2.2"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_aaa_severity_must_be_1(self) -> None:
        f = _finding(
            wcag_ref="2.4.13",
            wcag_level="AAA",
            severity=3,
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        with pytest.raises(
            AuditParseError, match="AAA findings to carry severity 1"
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_aaa_with_severity_1_passes(self) -> None:
        # Positive counterpart of the rule above: severity 1 AAA finding
        # is the SKILL.md-legal shape. Gives the test suite a "what
        # right looks like" anchor next to the rejection case.
        f = _finding(
            wcag_ref="2.4.13",
            wcag_level="AAA",
            severity=1,
            evidence_source=["html"],
            evidence_quote_idxs=[],
        )
        parse_audit_response(
            json.dumps(_happy_payload(findings=[f])), n_quotes=5
        )


# =============================================================================
# parse_audit_response — evidence_source + evidence_quote_idxs coupling
# =============================================================================


class TestParseAuditResponseEvidenceSource:
    def test_evidence_source_empty(self) -> None:
        f = _finding(
            evidence_source=[],
            evidence_quote_idxs=[],
        )
        with pytest.raises(AuditParseError, match="must be non-empty"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_source_not_list(self) -> None:
        f = _finding()
        f["evidence_source"] = "html"  # type: ignore[assignment]
        with pytest.raises(AuditParseError, match="must be list"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_source_invalid_token(self) -> None:
        f = _finding(evidence_source=["markup"], evidence_quote_idxs=[])
        with pytest.raises(AuditParseError, match=r"not in \["):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_evidence_source_duplicates_rejected(self) -> None:
        f = _finding(
            evidence_source=["html", "html", "quotes"],
            evidence_quote_idxs=[0],
        )
        with pytest.raises(AuditParseError, match="contains duplicates"):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_quotes_in_source_but_idxs_empty(self) -> None:
        f = _finding(
            evidence_source=["quotes"],
            evidence_quote_idxs=[],
        )
        with pytest.raises(
            AuditParseError,
            match="includes 'quotes' but evidence_quote_idxs is empty",
        ):
            parse_audit_response(
                json.dumps(_happy_payload(findings=[f])), n_quotes=5
            )

    def test_quotes_absent_but_idxs_non_empty(self) -> None:
        f = _finding(
            evidence_source=["html"],
            evidence_quote_idxs=[0],
        )
        with pytest.raises(
            AuditParseError,
            match="does not include 'quotes' but evidence_quote_idxs",
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


# =============================================================================
# _build_heuristic_violations — severity mapping + reasoning encoding
# =============================================================================


class TestBuildHeuristicViolations:
    def test_severity_mapping_all_four_levels(self) -> None:
        findings = [
            _finding(
                heuristic=f"h{n}",
                severity=n,
                # AAA advisory constraint forces different wcag_level
                # at severity 1 — use AA for 2/3/4 and AAA only at 1.
                wcag_level="AAA" if n == 1 else "AA",
                wcag_ref="2.4.13" if n == 1 else "1.4.3",
                evidence_source=["html"] if n == 1 else ["html", "quotes"],
                evidence_quote_idxs=[] if n == 1 else [0],
            )
            for n in (1, 2, 3, 4)
        ]
        payload = _happy_payload(findings=findings)
        c = _cluster()
        violations = acc._build_heuristic_violations(payload, c)
        assert [v.severity for v in violations] == [3, 5, 7, 9]

    def test_reasoning_wcag_level_tag_formatting(self) -> None:
        c = _cluster(quotes=["q0", "q1"])
        f = _finding(
            dimension="perceivable",
            wcag_ref="1.4.3",
            wcag_level="AA",
            evidence_source=["html", "quotes"],
            evidence_quote_idxs=[0, 1],
        )
        violations = acc._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert reasoning.startswith("[perceivable]")
        assert "(WCAG 1.4.3 AA)" in reasoning
        assert "q[0]='q0'" in reasoning
        assert "q[1]='q1'" in reasoning
        assert "Nielsen 3 → anchored 7" in reasoning

    def test_reasoning_inclusive_tag_formatting(self) -> None:
        c = _cluster(quotes=["hospital quote"])
        f = _finding(
            dimension="inclusive_cognitive",
            heuristic="situational_inclusion_gap",
            wcag_ref=None,
            wcag_level="inclusive",
            severity=3,
            evidence_source=["quotes", "ui_context"],
            evidence_quote_idxs=[0],
        )
        violations = acc._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert "(inclusive)" in reasoning
        # No WCAG SC citation when level is inclusive.
        assert "WCAG" not in reasoning

    def test_reasoning_markup_only_finding(self) -> None:
        c = _cluster(quotes=["unrelated quote"])
        f = _finding(
            evidence_source=["html", "screenshot"],
            evidence_quote_idxs=[],
        )
        violations = acc._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        reasoning = violations[0].reasoning
        assert "(no quote anchor" in reasoning
        assert "html+screenshot" in reasoning

    def test_reasoning_sources_tag_reflects_evidence_source(self) -> None:
        c = _cluster(quotes=["q0"])
        f = _finding(
            evidence_source=["html", "quotes"],
            evidence_quote_idxs=[0],
        )
        violations = acc._build_heuristic_violations(
            _happy_payload(findings=[f]), c
        )
        assert "Evidence (html+quotes):" in violations[0].reasoning


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

    def test_transport_failure_propagates(self) -> None:
        client = FakeClient(raise_on={"Dismiss": RuntimeError("replay miss")})
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
        """End-to-end: an enriched cluster's optional fields land in the
        prompt that reaches the client. Guards against a refactor
        silently dropping the new fields from the prompt builder while
        they still parse at the schema layer.
        """
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
# build_provenance — accessibility-extended aggregates
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

    def test_wcag_level_histogram(self) -> None:
        payload = _happy_payload(
            findings=[
                _finding(wcag_level="A", wcag_ref="1.1.1"),
                _finding(wcag_level="AA", wcag_ref="1.4.3"),
                _finding(
                    wcag_level="AAA",
                    wcag_ref="2.4.13",
                    severity=1,
                    evidence_source=["html"],
                    evidence_quote_idxs=[],
                ),
                _finding(
                    dimension="inclusive_cognitive",
                    wcag_level="inclusive",
                    wcag_ref=None,
                    severity=2,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
            ]
        )
        outcomes = [
            self._outcome(cluster_id="c00", status="audited", payload=payload)
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert prov["wcag_level_histogram"] == {
            "A": 1,
            "AA": 1,
            "AAA": 1,
            "inclusive": 1,
        }

    def test_wcag_ref_counts(self) -> None:
        payload = _happy_payload(
            findings=[
                _finding(wcag_ref="1.4.3", wcag_level="AA"),
                _finding(wcag_ref="1.4.3", wcag_level="AA"),
                _finding(
                    wcag_ref="2.4.7",
                    wcag_level="AA",
                    heuristic="missing_focus_indicator",
                ),
                _finding(
                    dimension="inclusive_cognitive",
                    wcag_level="inclusive",
                    wcag_ref=None,
                    evidence_source=["quotes"],
                    evidence_quote_idxs=[0],
                ),
            ]
        )
        outcomes = [
            self._outcome(cluster_id="c00", status="audited", payload=payload)
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert prov["wcag_ref_counts"] == {"1.4.3": 2, "2.4.7": 1}
        # Inclusive finding has wcag_ref=None → not counted (by design).
        assert None not in prov["wcag_ref_counts"]

    def test_severity_histogram(self) -> None:
        payload = _happy_payload(
            findings=[
                _finding(severity=1, wcag_level="AAA", wcag_ref="2.4.13"),
                _finding(severity=3, wcag_level="AA", wcag_ref="1.4.3"),
                _finding(
                    severity=3,
                    wcag_level="AA",
                    wcag_ref="2.4.7",
                    heuristic="missing_focus_indicator",
                ),
            ]
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


# =============================================================================
# CLI — main
# =============================================================================


class TestMain:
    """End-to-end CLI tests. The Norman sibling (test_l4_audit::TestMain)
    is deselected in the sandbox because pytest's tmpdir ownership check
    trips on the sandbox user — the same may happen here. Keep these
    tests lean so sandbox runs can skip the class via `-k 'not TestMain'`
    without losing the core-logic coverage above.
    """

    def test_cli_end_to_end_with_fakeclient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(acc, "_resolve_repo_root", lambda: tmp_path)

        # The Norman module's _resolve_repo_root is imported by the
        # accessibility module at import time (shared helpers) but the
        # storage layer resolves against the accessibility module's
        # _resolve_repo_root call site — patching acc alone suffices.
        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [
                _cluster(cluster_id="cluster_00").model_dump(mode="json"),
                _cluster(
                    cluster_id="cluster_01",
                    label="Speak this sentence — no feedback",
                    quotes=["it says wrong", "no idea why"],
                ).model_dump(mode="json"),
            ],
        )
        output_path = data_dir / "verdicts.jsonl"
        native_path = data_dir / "verdicts.native.jsonl"

        fake = FakeClient(default_response=_happy_response_text())

        def _fake_client_ctor(**_kwargs: Any) -> FakeClient:
            return fake

        monkeypatch.setattr(acc, "Client", _fake_client_ctor)

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
                "l4-acc-test-run",
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

    def test_cli_empty_input_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(acc, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        output_path = data_dir / "out.jsonl"
        native_path = data_dir / "native.jsonl"

        monkeypatch.setattr(acc, "Client", lambda **_k: FakeClient())
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
