"""Tests for ``auditable_design.layers.l2_structure`` — no network, no real Claude.

Structure mirrors the module's sections (skill hash, prompt build, parsing,
post-processing, extract pipeline, Claude wrapper + batch, IO, CLI).

The FakeClient stand-in is lifted from test_l1_classify.py — it mirrors the
:class:`auditable_design.claude_client.Client` surface that L2 uses and
records invocations for assertion. Kept local to each test module so one
test file doesn't silently drift because the other changed its fake.
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
from auditable_design.layers import l2_structure
from auditable_design.layers.l2_structure import (
    MAX_NODES,
    MAX_TOKENS,
    MIN_NODES,
    MODEL,
    NODE_TYPES,
    RELATION_TYPES,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    L2Outcome,
    ParseError,
    build_user_message,
    compute_node_offsets,
    extract_graph,
    has_substring_containment,
    load_existing_graphs,
    load_existing_thin,
    load_ux_relevant_reviews,
    parse_response,
    skill_hash,
    structure_batch,
    structure_one,
)
from auditable_design.schemas import ClassifiedReview, ComplaintGraph, RawReview


# =============================================================================
# Helpers — fake client + factories
# =============================================================================


@dataclass
class FakeClient:
    """In-memory stand-in for claude_client.Client.

    Same shape as L1's FakeClient — duplicated so a change in one test module
    doesn't silently affect the other.
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
            raise RuntimeError(f"FakeClient: no scripted response for user={user[:80]!r}...")
        return ClaudeResponse(
            call_id="fake-call",
            key_hash="0" * 64,
            skill_id=skill_id,
            skill_hash=skill_hash,
            model=model,
            temperature=float(temperature),
            prompt=f"SYSTEM:\t{system}\tUSER:\t{user}",
            response=response_text,
            input_tokens=100,
            output_tokens=40,
            cost_usd=0.0,
            timestamp="2026-04-22T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _review(
    *,
    review_id: str = "a" * 40,
    rating: int = 2,
    text: str = "Paywall is annoying. Used to be free.",
    timestamp: datetime | None = None,
    author_hash: str = "0" * 64,
    source: str = "google_play",
    lang: str = "en",
    app_version: str | None = "5.0.1",
) -> RawReview:
    return RawReview(
        review_id=review_id,
        source=source,  # type: ignore[arg-type]
        author_hash=author_hash,
        timestamp_utc=timestamp or datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
        rating=rating,
        text=text,
        lang=lang,
        app_version=app_version,
    )


def _graph_json(
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
) -> str:
    """Render a Claude-style response for a graph with the supplied nodes/edges.

    Default is the canonical 'Paywall is annoying. Used to be free.' example
    from SKILL.md — three faithful anchors + two edges.
    """
    if nodes is None:
        nodes = [
            {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "Paywall"},
            {"node_id": "n2", "node_type": "pain", "verbatim_quote": "annoying"},
            {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "Used to be free"},
        ]
    if edges is None:
        edges = [
            {"src": "n1", "dst": "n2", "relation": "triggers"},
            {"src": "n1", "dst": "n3", "relation": "violates_expectation"},
        ]
    return json.dumps({"nodes": nodes, "edges": edges})


# =============================================================================
# Constants — sanity
# =============================================================================


class TestConstants:
    def test_node_types_closed_vocab(self) -> None:
        assert NODE_TYPES == frozenset(
            {"pain", "expectation", "triggered_element", "workaround", "lost_value"}
        )

    def test_relation_types_closed_vocab(self) -> None:
        assert RELATION_TYPES == frozenset(
            {"triggers", "violates_expectation", "compensates_for", "correlates_with"}
        )

    def test_node_bounds_match_schema(self) -> None:
        assert MIN_NODES == 3
        assert MAX_NODES == 7

    def test_model_is_opus_47(self) -> None:
        # Per ADR-009; if this changes, ADR-009 must be updated too.
        assert MODEL == "claude-opus-4-7"


# =============================================================================
# skill_hash
# =============================================================================


class TestSkillHash:
    def test_is_deterministic(self) -> None:
        assert skill_hash() == skill_hash()

    def test_is_sha256_hex(self) -> None:
        h = skill_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_matches_sha256_of_system_prompt(self) -> None:
        expected = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        assert skill_hash() == expected

    def test_frontmatter_stripped_from_prompt(self) -> None:
        # SKILL.md's YAML frontmatter must not be part of SYSTEM_PROMPT —
        # it's loader metadata, not model guidance. Stripping keeps the
        # skill_hash stable across frontmatter-only edits.
        assert not SYSTEM_PROMPT.startswith("---")
        assert "structure-of-complaint" not in SYSTEM_PROMPT.splitlines()[0]


# =============================================================================
# build_user_message
# =============================================================================


class TestBuildUserMessage:
    def test_wraps_text_in_user_review_tag(self) -> None:
        r = _review(review_id="feedbeef" * 5, text="Hello world")
        msg = build_user_message(r)
        assert '<user_review id="feedbeef' in msg
        assert msg.rstrip().endswith("</user_review>")

    def test_escapes_markup_like_characters(self) -> None:
        r = _review(text="I love Duolingo <3 & it's great")
        msg = build_user_message(r)
        assert "&lt;3" in msg
        assert "&amp;" in msg
        assert "<3" not in msg

    def test_review_id_propagates_verbatim(self) -> None:
        rid = "1" * 40
        r = _review(review_id=rid)
        msg = build_user_message(r)
        assert f'id="{rid}"' in msg

    def test_rating_not_included(self) -> None:
        # L2 deliberately omits rating — L1 sends it (relevance signal),
        # but L2 focuses on structural extraction. Rating would bias
        # toward over/under-emitting complaint content.
        r = _review(rating=5)
        msg = build_user_message(r)
        assert "rating" not in msg


# =============================================================================
# parse_response — happy paths + wrapper tolerance
# =============================================================================


class TestParseResponseBasic:
    def test_round_trip_canonical(self) -> None:
        nodes, edges = parse_response(_graph_json())
        assert len(nodes) == 3
        assert len(edges) == 2
        assert nodes[0] == {
            "node_id": "n1",
            "node_type": "triggered_element",
            "verbatim_quote": "Paywall",
        }
        assert edges[0] == {"src": "n1", "dst": "n2", "relation": "triggers"}

    def test_zero_edges_accepted(self) -> None:
        raw = _graph_json(edges=[])
        nodes, edges = parse_response(raw)
        assert len(nodes) == 3
        assert edges == []

    def test_below_min_nodes_still_parses(self) -> None:
        # parse_response does NOT enforce MIN_NODES (that gate lives in
        # extract_graph so we can route <3 to quarantine, not crash).
        raw = _graph_json(
            nodes=[
                {"node_id": "n1", "node_type": "pain", "verbatim_quote": "stupid"},
                {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "wait"},
            ],
            edges=[],
        )
        nodes, edges = parse_response(raw)
        assert len(nodes) == 2
        assert edges == []


class TestParseResponseWrapperTolerance:
    def test_tolerates_leading_prose(self) -> None:
        raw = f"Here is the graph: {_graph_json()}"
        nodes, _ = parse_response(raw)
        assert len(nodes) == 3

    def test_tolerates_trailing_prose(self) -> None:
        raw = f"{_graph_json()}\n\nHope that helps!"
        nodes, _ = parse_response(raw)
        assert len(nodes) == 3

    def test_tolerates_code_fence(self) -> None:
        raw = f"```json\n{_graph_json()}\n```"
        nodes, _ = parse_response(raw)
        assert len(nodes) == 3

    def test_no_json_at_all_rejected(self) -> None:
        with pytest.raises(ParseError, match="no JSON object"):
            parse_response("Sorry, I cannot extract a graph.")


# =============================================================================
# parse_response — schema / vocabulary / structural enforcement
# =============================================================================


class TestParseResponseStructure:
    def test_top_level_must_be_object(self) -> None:
        with pytest.raises(ParseError, match="no JSON object"):
            parse_response("[1, 2, 3]")

    def test_missing_nodes_key_rejected(self) -> None:
        raw = json.dumps({"edges": []})
        with pytest.raises(ParseError, match="missing required top-level key"):
            parse_response(raw)

    def test_missing_edges_key_treated_as_empty(self) -> None:
        # Opus will occasionally omit ``edges`` entirely on short reviews
        # that carry no clear causal link. SKILL.md permits zero edges,
        # so the parser treats a missing key as an empty list rather
        # than dropping the row. Observed on review 07d0c087 in the
        # N=50 sub-sample run.
        nodes = [
            {"node_id": "n1", "node_type": "pain", "verbatim_quote": "frustrated"},
            {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "paywall"},
            {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "free access"},
        ]
        raw = json.dumps({"nodes": nodes})
        out_nodes, out_edges = parse_response(raw)
        assert len(out_nodes) == 3
        assert out_edges == []

    def test_extra_top_level_key_rejected(self) -> None:
        raw = json.dumps({"nodes": [], "edges": [], "reasoning": "hmm"})
        with pytest.raises(ParseError, match="unexpected top-level keys"):
            parse_response(raw)

    def test_nodes_not_a_list_rejected(self) -> None:
        raw = json.dumps({"nodes": {"n1": "x"}, "edges": []})
        with pytest.raises(ParseError, match="nodes must be a list"):
            parse_response(raw)

    def test_edges_not_a_list_rejected(self) -> None:
        raw = json.dumps({"nodes": [], "edges": {"e1": "x"}})
        with pytest.raises(ParseError, match="edges must be a list"):
            parse_response(raw)

    def test_node_missing_keys_rejected(self) -> None:
        raw = json.dumps(
            {
                "nodes": [{"node_id": "n1", "verbatim_quote": "x"}],  # missing node_type
                "edges": [],
            }
        )
        with pytest.raises(ParseError, match=r"nodes\[0\] key set mismatch"):
            parse_response(raw)

    def test_node_extra_keys_rejected(self) -> None:
        # Option B offset authority: model must NOT emit quote_start/quote_end.
        # Enforcing this at parse-level means a SKILL edit that asks for
        # offsets breaks loudly, not silently.
        raw = json.dumps(
            {
                "nodes": [
                    {
                        "node_id": "n1",
                        "node_type": "pain",
                        "verbatim_quote": "ow",
                        "quote_start": 0,
                        "quote_end": 2,
                    }
                ],
                "edges": [],
            }
        )
        with pytest.raises(ParseError, match=r"nodes\[0\] key set mismatch"):
            parse_response(raw)

    def test_unknown_node_type_rejected(self) -> None:
        raw = _graph_json(
            nodes=[{"node_id": "n1", "node_type": "delight", "verbatim_quote": "love"}]
        )
        with pytest.raises(ParseError, match="not in closed vocabulary"):
            parse_response(raw)

    def test_unknown_relation_rejected(self) -> None:
        raw = _graph_json(
            edges=[{"src": "n1", "dst": "n2", "relation": "causes"}]  # invented
        )
        with pytest.raises(ParseError, match="not in closed vocabulary"):
            parse_response(raw)

    def test_empty_node_id_rejected(self) -> None:
        raw = _graph_json(
            nodes=[{"node_id": "", "node_type": "pain", "verbatim_quote": "ow"}]
        )
        with pytest.raises(ParseError, match="non-empty string"):
            parse_response(raw)

    def test_empty_verbatim_quote_rejected(self) -> None:
        raw = _graph_json(
            nodes=[{"node_id": "n1", "node_type": "pain", "verbatim_quote": ""}]
        )
        with pytest.raises(ParseError, match="non-empty string"):
            parse_response(raw)

    def test_duplicate_node_id_rejected(self) -> None:
        raw = _graph_json(
            nodes=[
                {"node_id": "n1", "node_type": "pain", "verbatim_quote": "a"},
                {"node_id": "n1", "node_type": "expectation", "verbatim_quote": "b"},
                {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "c"},
            ],
            edges=[],
        )
        with pytest.raises(ParseError, match="duplicate node_id"):
            parse_response(raw)

    def test_self_loop_rejected(self) -> None:
        raw = _graph_json(edges=[{"src": "n1", "dst": "n1", "relation": "triggers"}])
        with pytest.raises(ParseError, match="self-loop"):
            parse_response(raw)

    def test_edge_references_missing_node_rejected(self) -> None:
        raw = _graph_json(edges=[{"src": "n1", "dst": "n99", "relation": "triggers"}])
        with pytest.raises(ParseError, match="does not reference a known node"):
            parse_response(raw)

    def test_edge_missing_keys_rejected(self) -> None:
        raw = _graph_json(edges=[{"src": "n1", "dst": "n2"}])  # no relation
        with pytest.raises(ParseError, match=r"edges\[0\] key set mismatch"):
            parse_response(raw)

    def test_malformed_json_rejected(self) -> None:
        raw = '{"nodes": [{"node_id": "n1",'
        with pytest.raises(ParseError, match="malformed JSON|no JSON object"):
            parse_response(raw)


# =============================================================================
# compute_node_offsets
# =============================================================================


class TestComputeNodeOffsets:
    def test_happy_path(self) -> None:
        source = "Paywall is annoying. Used to be free."
        nodes = [
            {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "Paywall"},
            {"node_id": "n2", "node_type": "pain", "verbatim_quote": "annoying"},
            {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "Used to be free"},
        ]
        out, missing = compute_node_offsets(nodes, source)
        assert missing == []
        assert len(out) == 3
        # offsets match source substring
        for node in out:
            assert source[node["quote_start"] : node["quote_end"]] == node["verbatim_quote"]

    def test_missing_quote_reported(self) -> None:
        source = "Paywall is annoying."
        nodes = [
            {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "Paywall"},
            {"node_id": "n2", "node_type": "pain", "verbatim_quote": "frustrating"},  # not in src
        ]
        out, missing = compute_node_offsets(nodes, source)
        assert missing == ["n2"]
        assert len(out) == 1
        assert out[0]["node_id"] == "n1"

    def test_repeated_phrase_takes_first_occurrence(self) -> None:
        # Documented in docstring: str.find returns first match.
        source = "no no no"
        nodes = [{"node_id": "n1", "node_type": "pain", "verbatim_quote": "no"}]
        out, missing = compute_node_offsets(nodes, source)
        assert missing == []
        assert out[0]["quote_start"] == 0
        assert out[0]["quote_end"] == 2

    def test_preserves_original_keys(self) -> None:
        source = "hello world"
        nodes = [
            {"node_id": "n1", "node_type": "pain", "verbatim_quote": "hello", "_extra": "keep"}
        ]
        out, _ = compute_node_offsets(nodes, source)
        assert out[0]["_extra"] == "keep"
        assert out[0]["node_type"] == "pain"


# =============================================================================
# has_substring_containment
# =============================================================================


class TestHasSubstringContainment:
    def test_disjoint_quotes_pass(self) -> None:
        nodes = [
            {"node_id": "n1", "verbatim_quote": "Paywall"},
            {"node_id": "n2", "verbatim_quote": "annoying"},
            {"node_id": "n3", "verbatim_quote": "Used to be free"},
        ]
        found, detail = has_substring_containment(nodes)
        assert found is False
        assert detail == ""

    def test_proper_containment_detected(self) -> None:
        nodes = [
            {"node_id": "n1", "verbatim_quote": "my phone"},
            {"node_id": "n2", "verbatim_quote": "not on my phone"},
        ]
        found, detail = has_substring_containment(nodes)
        assert found is True
        assert "'n1'" in detail and "'n2'" in detail
        assert "⊂" in detail

    def test_identical_quotes_detected(self) -> None:
        nodes = [
            {"node_id": "n1", "verbatim_quote": "annoying"},
            {"node_id": "n2", "verbatim_quote": "annoying"},
        ]
        found, detail = has_substring_containment(nodes)
        assert found is True
        assert "identical" in detail

    def test_single_node_is_safe(self) -> None:
        nodes = [{"node_id": "n1", "verbatim_quote": "only"}]
        found, _ = has_substring_containment(nodes)
        assert found is False

    def test_empty_list_is_safe(self) -> None:
        found, _ = has_substring_containment([])
        assert found is False

    def test_shared_characters_without_containment_pass(self) -> None:
        # Shared letters shouldn't trip — only full substring containment does.
        nodes = [
            {"node_id": "n1", "verbatim_quote": "abcd"},
            {"node_id": "n2", "verbatim_quote": "bcde"},
        ]
        found, _ = has_substring_containment(nodes)
        assert found is False


# =============================================================================
# extract_graph — the routing pipeline
# =============================================================================


class TestExtractGraphHappy:
    def test_canonical_three_node_graph(self) -> None:
        r = _review(text="Paywall is annoying. Used to be free.")
        outcome = extract_graph(r, _graph_json())
        assert outcome.status == "graph"
        assert outcome.quarantine_record is None
        assert outcome.graph is not None
        g = outcome.graph
        assert isinstance(g, ComplaintGraph)
        assert g.review_id == r.review_id
        assert len(g.nodes) == 3
        assert len(g.edges) == 2
        # offsets well-formed
        for node in g.nodes:
            span = r.text[node.quote_start : node.quote_end]
            assert span == node.verbatim_quote

    def test_five_nodes_ok(self) -> None:
        text = "new practice mode ruins the streak thing. log in every day. skip practice altogether. No streak, no point."
        r = _review(text=text)
        nodes = [
            {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "new practice mode"},
            {"node_id": "n2", "node_type": "pain", "verbatim_quote": "ruins the streak thing"},
            {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "log in every day"},
            {"node_id": "n4", "node_type": "workaround", "verbatim_quote": "skip practice altogether"},
            {"node_id": "n5", "node_type": "lost_value", "verbatim_quote": "No streak, no point"},
        ]
        outcome = extract_graph(r, _graph_json(nodes=nodes, edges=[]))
        assert outcome.status == "graph"
        assert outcome.graph is not None
        assert len(outcome.graph.nodes) == 5


class TestExtractGraphQuarantine:
    def test_under_minimum_nodes_quarantined(self) -> None:
        r = _review(text="wait 4h, stupid")
        # 2 faithful nodes — SKILL's thin-review path.
        nodes = [
            {"node_id": "n1", "node_type": "pain", "verbatim_quote": "stupid"},
            {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "wait 4h"},
        ]
        outcome = extract_graph(r, _graph_json(nodes=nodes, edges=[]))
        assert outcome.status == "quarantine"
        assert outcome.graph is None
        rec = outcome.quarantine_record
        assert rec is not None
        assert rec["reason"] == "under_minimum_nodes"
        assert rec["node_count"] == 2
        assert rec["review_id"] == r.review_id

    def test_over_maximum_nodes_quarantined(self) -> None:
        # 8 nodes > MAX_NODES=7. Should route, not crash Pydantic.
        text = " ".join(f"word{i}" for i in range(10))
        r = _review(text=text)
        nodes = [
            {"node_id": f"n{i}", "node_type": "pain", "verbatim_quote": f"word{i}"}
            for i in range(8)
        ]
        outcome = extract_graph(r, _graph_json(nodes=nodes, edges=[]))
        assert outcome.status == "quarantine"
        assert outcome.quarantine_record is not None
        assert outcome.quarantine_record["reason"] == "over_maximum_nodes"

    def test_substring_containment_quarantined(self) -> None:
        # "my phone" ⊂ "not on my phone" from SKILL's pitfalls list.
        r = _review(text="I cannot do lessons on my phone or anywhere. not on my phone. so bad.")
        nodes = [
            {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "my phone"},
            {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "not on my phone"},
            {"node_id": "n3", "node_type": "pain", "verbatim_quote": "so bad"},
        ]
        outcome = extract_graph(r, _graph_json(nodes=nodes, edges=[]))
        assert outcome.status == "quarantine"
        assert outcome.quarantine_record is not None
        assert outcome.quarantine_record["reason"] == "substring_containment"

    def test_hallucination_quarantined(self) -> None:
        r = _review(text="Paywall is annoying. Used to be free.")
        # One quote faithfully anchored, one invented.
        nodes = [
            {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "Paywall"},
            {"node_id": "n2", "node_type": "pain", "verbatim_quote": "absolutely dreadful"},
            {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "Used to be free"},
        ]
        outcome = extract_graph(r, _graph_json(nodes=nodes, edges=[]))
        assert outcome.status == "quarantine"
        assert outcome.quarantine_record is not None
        assert outcome.quarantine_record["reason"] == "hallucination"
        assert "n2" in outcome.quarantine_record["detail"]

    def test_hallucination_wins_over_containment(self) -> None:
        # Documented ordering: step 2 (offsets) runs before step 3 (containment).
        # A graph with BOTH a hallucinated quote and substring-containment
        # routes to hallucination — the more severe failure class.
        r = _review(text="Paywall is annoying.")
        nodes = [
            {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "Paywall"},
            {"node_id": "n2", "node_type": "pain", "verbatim_quote": "Paywall"},  # duplicate
            {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "never existed"},
        ]
        outcome = extract_graph(r, _graph_json(nodes=nodes, edges=[]))
        assert outcome.status == "quarantine"
        assert outcome.quarantine_record is not None
        assert outcome.quarantine_record["reason"] == "hallucination"

    def test_parse_error_propagates(self) -> None:
        # extract_graph does NOT catch ParseError — it's a transport-class
        # failure, not a semantic one, and belongs in the `failures` bucket.
        r = _review()
        with pytest.raises(ParseError):
            extract_graph(r, "this is not JSON at all")


# =============================================================================
# structure_one / structure_batch
# =============================================================================


class TestStructureOne:
    def test_happy_path_emits_graph(self) -> None:
        r = _review(review_id="1" * 40, text="Paywall is annoying. Used to be free.")
        client = FakeClient(default_response=_graph_json())
        out = asyncio.run(
            structure_one(r, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert out.status == "graph"
        assert out.graph is not None
        # Verify wire-level: system prompt, model, temperature, max_tokens.
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["system"] == SYSTEM_PROMPT
        assert call["model"] == MODEL
        assert call["temperature"] == TEMPERATURE
        assert call["max_tokens"] == MAX_TOKENS
        assert call["skill_id"] == SKILL_ID

    def test_quarantine_does_not_raise(self) -> None:
        # Thin review — under minimum. structure_one should RETURN a
        # quarantine L2Outcome, not raise.
        r = _review(text="wait 4h, stupid")
        thin_response = _graph_json(
            nodes=[
                {"node_id": "n1", "node_type": "pain", "verbatim_quote": "stupid"},
                {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "wait 4h"},
            ],
            edges=[],
        )
        client = FakeClient(default_response=thin_response)
        out = asyncio.run(
            structure_one(r, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert out.status == "quarantine"
        assert out.quarantine_record is not None
        assert out.quarantine_record["reason"] == "under_minimum_nodes"

    def test_parse_error_propagates(self) -> None:
        r = _review()
        client = FakeClient(default_response="not JSON at all")
        with pytest.raises(ParseError):
            asyncio.run(
                structure_one(r, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
            )

    def test_client_error_propagates(self) -> None:
        r = _review()
        client = FakeClient(
            default_response=_graph_json(),
            raise_on={r.review_id: RuntimeError("upstream broken")},
        )
        with pytest.raises(RuntimeError, match="upstream broken"):
            asyncio.run(
                structure_one(r, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
            )


class TestStructureBatch:
    def test_all_graphs(self) -> None:
        reviews = [
            _review(review_id=f"{i:040d}", text="Paywall is annoying. Used to be free.")
            for i in range(3)
        ]
        client = FakeClient(default_response=_graph_json())
        graphs, thin, failures = asyncio.run(
            structure_batch(reviews, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert len(graphs) == 3
        assert thin == []
        assert failures == []

    def test_mixed_graph_quarantine_failure(self) -> None:
        # three-way split: one graph, one quarantine, one ParseError.
        good = _review(review_id="aaaa" + "0" * 36, text="Paywall is annoying. Used to be free.")
        thin = _review(review_id="bbbb" + "0" * 36, text="wait 4h, stupid")
        broken = _review(review_id="cccc" + "0" * 36, text="noise")

        thin_response = _graph_json(
            nodes=[
                {"node_id": "n1", "node_type": "pain", "verbatim_quote": "stupid"},
                {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "wait 4h"},
            ],
            edges=[],
        )
        client = FakeClient(
            scripted={
                "aaaa": _graph_json(),
                "bbbb": thin_response,
            },
            default_response="not JSON",  # triggers ParseError for cccc
        )
        graphs, thins, failures = asyncio.run(
            structure_batch([good, thin, broken], client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert [g.review_id for g in graphs] == [good.review_id]
        assert [t["review_id"] for t in thins] == [thin.review_id]
        assert [rid for rid, _ in failures] == [broken.review_id]
        assert isinstance(failures[0][1], ParseError)

    def test_empty_input(self) -> None:
        client = FakeClient()
        graphs, thins, failures = asyncio.run(
            structure_batch([], client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert graphs == []
        assert thins == []
        assert failures == []


# =============================================================================
# load_ux_relevant_reviews
# =============================================================================


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


class TestLoadUxRelevantReviews:
    def test_filters_to_ux_relevant_only(self, tmp_path: Path) -> None:
        corpus_path = tmp_path / "corpus.jsonl"
        classified_path = tmp_path / "classified.jsonl"
        r_ux = _review(review_id="a" * 40)
        r_noise = _review(review_id="b" * 40)
        _write_jsonl(corpus_path, [r_ux.model_dump(mode="json"), r_noise.model_dump(mode="json")])
        rows = [
            ClassifiedReview(
                review_id="a" * 40,
                is_ux_relevant=True,
                classifier_confidence=0.9,
                rubric_tags=["paywall"],
                classified_at=datetime(2026, 4, 22, tzinfo=UTC),
            ).model_dump(mode="json"),
            ClassifiedReview(
                review_id="b" * 40,
                is_ux_relevant=False,
                classifier_confidence=0.4,
                rubric_tags=["off_topic"],
                classified_at=datetime(2026, 4, 22, tzinfo=UTC),
            ).model_dump(mode="json"),
        ]
        _write_jsonl(classified_path, rows)
        got = load_ux_relevant_reviews(corpus_path, classified_path)
        assert [r.review_id for r in got] == ["a" * 40]

    def test_missing_classified_raises(self, tmp_path: Path) -> None:
        corpus_path = tmp_path / "corpus.jsonl"
        _write_jsonl(corpus_path, [_review().model_dump(mode="json")])
        with pytest.raises(ValueError, match="L1 classification file not found"):
            load_ux_relevant_reviews(corpus_path, tmp_path / "nope.jsonl")

    def test_malformed_classified_raises(self, tmp_path: Path) -> None:
        corpus_path = tmp_path / "corpus.jsonl"
        classified_path = tmp_path / "classified.jsonl"
        _write_jsonl(corpus_path, [_review().model_dump(mode="json")])
        _write_jsonl(classified_path, [{"review_id": "x"}])  # missing required fields
        with pytest.raises(ValueError, match="line 1"):
            load_ux_relevant_reviews(corpus_path, classified_path)

    def test_corpus_without_matches_returns_empty(self, tmp_path: Path) -> None:
        # Classified file lists IDs not in the corpus; nothing to return.
        corpus_path = tmp_path / "corpus.jsonl"
        classified_path = tmp_path / "classified.jsonl"
        _write_jsonl(corpus_path, [_review(review_id="c" * 40).model_dump(mode="json")])
        _write_jsonl(
            classified_path,
            [
                ClassifiedReview(
                    review_id="z" * 40,
                    is_ux_relevant=True,
                    classifier_confidence=0.9,
                    rubric_tags=["paywall"],
                    classified_at=datetime(2026, 4, 22, tzinfo=UTC),
                ).model_dump(mode="json"),
            ],
        )
        got = load_ux_relevant_reviews(corpus_path, classified_path)
        assert got == []


# =============================================================================
# load_existing_graphs / load_existing_thin
# =============================================================================


def _complete_graph(review_id: str = "a" * 40) -> ComplaintGraph:
    source = "Paywall is annoying. Used to be free."
    return ComplaintGraph(
        review_id=review_id,
        nodes=[
            {  # type: ignore[list-item]
                "node_id": "n1",
                "node_type": "triggered_element",
                "verbatim_quote": "Paywall",
                "quote_start": 0,
                "quote_end": 7,
            },
            {  # type: ignore[list-item]
                "node_id": "n2",
                "node_type": "pain",
                "verbatim_quote": "annoying",
                "quote_start": source.find("annoying"),
                "quote_end": source.find("annoying") + len("annoying"),
            },
            {  # type: ignore[list-item]
                "node_id": "n3",
                "node_type": "expectation",
                "verbatim_quote": "Used to be free",
                "quote_start": source.find("Used to be free"),
                "quote_end": source.find("Used to be free") + len("Used to be free"),
            },
        ],
        edges=[],
    )


class TestLoadExistingGraphs:
    def test_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_existing_graphs(tmp_path / "nope.jsonl") == []

    def test_round_trip(self, tmp_path: Path) -> None:
        g = _complete_graph()
        path = tmp_path / "graphs.jsonl"
        path.write_text(json.dumps(g.model_dump(mode="json")) + "\n")
        got = load_existing_graphs(path)
        assert len(got) == 1
        assert got[0].review_id == g.review_id
        assert len(got[0].nodes) == 3

    def test_tolerates_invalid_line(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        good = _complete_graph()
        path = tmp_path / "graphs.jsonl"
        lines = [
            json.dumps(good.model_dump(mode="json")),
            json.dumps({"review_id": "b" * 40, "nodes": []}),  # fails min_length=3
        ]
        path.write_text("\n".join(lines) + "\n")
        with caplog.at_level("WARNING"):
            got = load_existing_graphs(path)
        assert len(got) == 1
        assert any("line 2 invalid" in rec.message for rec in caplog.records)


class TestLoadExistingThin:
    def test_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_existing_thin(tmp_path / "nope.jsonl") == []

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "thin.jsonl"
        rec = {
            "review_id": "a" * 40,
            "reason": "under_minimum_nodes",
            "node_count": 2,
            "detail": "test",
            "processed_at": "2026-04-22T12:00:00+00:00",
        }
        path.write_text(json.dumps(rec) + "\n")
        got = load_existing_thin(path)
        assert got == [rec]

    def test_tolerates_missing_keys(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "thin.jsonl"
        good = {
            "review_id": "a" * 40,
            "reason": "under_minimum_nodes",
            "node_count": 2,
            "detail": "test",
            "processed_at": "2026-04-22T12:00:00+00:00",
        }
        bad = {"review_id": "b" * 40, "reason": "hallucination"}  # missing keys
        path.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
        with caplog.at_level("WARNING"):
            got = load_existing_thin(path)
        assert len(got) == 1
        assert got[0]["review_id"] == "a" * 40
        assert any("missing required keys" in rec.message for rec in caplog.records)


# =============================================================================
# CLI — end-to-end
# =============================================================================


class _FakeClientFactory:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_instance: FakeClient | None = None

    def __call__(self, **kwargs: Any) -> FakeClient:
        self.last_instance = FakeClient(default_response=self.response)
        return self.last_instance


class TestMainCLI:
    def _setup_repo(self, tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
        repo = tmp_path / "repo"
        (repo / "data" / "raw").mkdir(parents=True)
        (repo / "data" / "derived").mkdir(parents=True)
        (repo / "data" / "quarantine").mkdir(parents=True)
        (repo / "data" / "cache").mkdir(parents=True)
        (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
        corpus_path = repo / "data" / "raw" / "corpus.jsonl"
        classified_path = repo / "data" / "derived" / "l1_classified.jsonl"
        graphs_path = repo / "data" / "derived" / "l2_graphs.jsonl"
        quarantine_path = repo / "data" / "quarantine" / "l2_thin.jsonl"
        replay_log = repo / "data" / "cache" / "responses.jsonl"
        return repo, corpus_path, classified_path, graphs_path, quarantine_path, replay_log

    def _pin_repo_root(self, monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
        monkeypatch.setattr(l2_structure, "_resolve_repo_root", lambda: repo)

    def _write_classified_all_ux(self, path: Path, review_ids: list[str]) -> None:
        rows = [
            ClassifiedReview(
                review_id=rid,
                is_ux_relevant=True,
                classifier_confidence=0.9,
                rubric_tags=["paywall"],
                classified_at=datetime(2026, 4, 22, tzinfo=UTC),
            ).model_dump(mode="json")
            for rid in review_ids
        ]
        _write_jsonl(path, rows)

    def test_end_to_end_writes_graphs_and_meta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, corpus_path, classified_path, graphs_path, quarantine_path, replay_log = (
            self._setup_repo(tmp_path)
        )
        reviews = [
            _review(
                review_id=f"{i:040x}",
                text="Paywall is annoying. Used to be free.",
            )
            for i in range(3)
        ]
        _write_jsonl(corpus_path, [r.model_dump(mode="json") for r in reviews])
        self._write_classified_all_ux(classified_path, [r.review_id for r in reviews])

        self._pin_repo_root(monkeypatch, repo)
        factory = _FakeClientFactory(_graph_json())
        monkeypatch.setattr(l2_structure, "Client", factory)

        rc = l2_structure.main(
            [
                "--corpus", str(corpus_path),
                "--classified", str(classified_path),
                "--output", str(graphs_path),
                "--quarantine", str(quarantine_path),
                "--replay-log", str(replay_log),
                "--run-id", "test-run",
                "--mode", "live",
            ]
        )
        assert rc == 0
        assert graphs_path.exists()
        assert quarantine_path.exists()  # written even when empty

        graph_lines = [json.loads(x) for x in graphs_path.read_text().splitlines() if x.strip()]
        assert len(graph_lines) == 3
        assert {row["review_id"] for row in graph_lines} == {r.review_id for r in reviews}

        thin_lines = [
            json.loads(x) for x in quarantine_path.read_text().splitlines() if x.strip()
        ]
        assert thin_lines == []

        # Sidecars — both artifacts produce .meta.json.
        for artifact in (graphs_path, quarantine_path):
            meta_path = artifact.with_suffix(artifact.suffix + ".meta.json")
            assert meta_path.exists()
            meta = json.loads(meta_path.read_text())
            assert meta["run_id"] == "test-run"
            assert meta["layer"] == l2_structure.LAYER_NAME
            assert l2_structure.SKILL_ID in meta["skill_hashes"]

    def test_quarantine_routing_e2e(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Thin response — two nodes. Expect under_minimum_nodes row in
        # quarantine file and zero rows in graphs file.
        repo, corpus_path, classified_path, graphs_path, quarantine_path, replay_log = (
            self._setup_repo(tmp_path)
        )
        r = _review(review_id="a" * 40, text="wait 4h, stupid")
        _write_jsonl(corpus_path, [r.model_dump(mode="json")])
        self._write_classified_all_ux(classified_path, [r.review_id])

        self._pin_repo_root(monkeypatch, repo)
        thin_response = _graph_json(
            nodes=[
                {"node_id": "n1", "node_type": "pain", "verbatim_quote": "stupid"},
                {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "wait 4h"},
            ],
            edges=[],
        )
        factory = _FakeClientFactory(thin_response)
        monkeypatch.setattr(l2_structure, "Client", factory)

        rc = l2_structure.main(
            [
                "--corpus", str(corpus_path),
                "--classified", str(classified_path),
                "--output", str(graphs_path),
                "--quarantine", str(quarantine_path),
                "--replay-log", str(replay_log),
                "--run-id", "thin-run",
                "--mode", "live",
            ]
        )
        assert rc == 0
        graph_lines = [json.loads(x) for x in graphs_path.read_text().splitlines() if x.strip()]
        assert graph_lines == []
        thin_lines = [
            json.loads(x) for x in quarantine_path.read_text().splitlines() if x.strip()
        ]
        assert len(thin_lines) == 1
        assert thin_lines[0]["review_id"] == r.review_id
        assert thin_lines[0]["reason"] == "under_minimum_nodes"

    def test_rerun_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First run classifies one as graph + one as thin. Second run should
        # make zero Claude calls — both review_ids are already accounted for
        # across the two output files.
        repo, corpus_path, classified_path, graphs_path, quarantine_path, replay_log = (
            self._setup_repo(tmp_path)
        )
        good = _review(review_id="a" * 40, text="Paywall is annoying. Used to be free.")
        thin_review = _review(review_id="b" * 40, text="wait 4h, stupid")
        _write_jsonl(
            corpus_path,
            [good.model_dump(mode="json"), thin_review.model_dump(mode="json")],
        )
        self._write_classified_all_ux(classified_path, [good.review_id, thin_review.review_id])
        self._pin_repo_root(monkeypatch, repo)

        # Script two responses via review_id substring match.
        thin_response = _graph_json(
            nodes=[
                {"node_id": "n1", "node_type": "pain", "verbatim_quote": "stupid"},
                {"node_id": "n2", "node_type": "triggered_element", "verbatim_quote": "wait 4h"},
            ],
            edges=[],
        )

        class _Factory:
            def __init__(self) -> None:
                self.last_instance: FakeClient | None = None

            def __call__(self, **kwargs: Any) -> FakeClient:
                self.last_instance = FakeClient(
                    scripted={good.review_id: _graph_json(), thin_review.review_id: thin_response}
                )
                return self.last_instance

        factory_a = _Factory()
        monkeypatch.setattr(l2_structure, "Client", factory_a)
        l2_structure.main(
            [
                "--corpus", str(corpus_path),
                "--classified", str(classified_path),
                "--output", str(graphs_path),
                "--quarantine", str(quarantine_path),
                "--replay-log", str(replay_log),
                "--run-id", "run-1",
                "--mode", "live",
            ]
        )
        assert factory_a.last_instance is not None
        assert len(factory_a.last_instance.calls) == 2

        # Second run — everything already processed, zero new calls.
        factory_b = _Factory()
        monkeypatch.setattr(l2_structure, "Client", factory_b)
        rc = l2_structure.main(
            [
                "--corpus", str(corpus_path),
                "--classified", str(classified_path),
                "--output", str(graphs_path),
                "--quarantine", str(quarantine_path),
                "--replay-log", str(replay_log),
                "--run-id", "run-2",
                "--mode", "live",
            ]
        )
        assert rc == 0
        assert factory_b.last_instance is not None
        assert factory_b.last_instance.calls == []

    def test_empty_ux_relevant_set_is_a_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Classified file says nothing is UX-relevant → no reviews to process.
        repo, corpus_path, classified_path, graphs_path, quarantine_path, replay_log = (
            self._setup_repo(tmp_path)
        )
        r = _review(review_id="a" * 40)
        _write_jsonl(corpus_path, [r.model_dump(mode="json")])
        # is_ux_relevant=False for the one review
        _write_jsonl(
            classified_path,
            [
                ClassifiedReview(
                    review_id=r.review_id,
                    is_ux_relevant=False,
                    classifier_confidence=0.9,
                    rubric_tags=["off_topic"],
                    classified_at=datetime(2026, 4, 22, tzinfo=UTC),
                ).model_dump(mode="json")
            ],
        )
        self._pin_repo_root(monkeypatch, repo)

        factory = _FakeClientFactory(_graph_json())
        monkeypatch.setattr(l2_structure, "Client", factory)
        rc = l2_structure.main(
            [
                "--corpus", str(corpus_path),
                "--classified", str(classified_path),
                "--output", str(graphs_path),
                "--quarantine", str(quarantine_path),
                "--replay-log", str(replay_log),
                "--run-id", "empty-run",
                "--mode", "live",
            ]
        )
        assert rc == 0
        assert factory.last_instance is not None
        assert factory.last_instance.calls == []
        # Both output files still written (empty), each with sidecar.
        assert graphs_path.read_text() == ""
        assert quarantine_path.read_text() == ""
